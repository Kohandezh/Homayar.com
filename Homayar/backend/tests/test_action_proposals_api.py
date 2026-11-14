"""HOM-135 PR 2: the action proposal service, routes, permissions and audit (ADR 0082).

The workspace is the HOM-73 review queue (``build_appeal_queue``): ``owner`` and
``admin`` approve; ``sup`` (supervisor of team A), ``plain`` (a second supervisor of team
A), ``agent`` (the agent of team A), ``sup_b``, ``lapsed`` are members and propose;
``viewer`` does neither.  An action item proposal is built on its own conversation of
team A with a current transcript revision; a coaching proposal is built on a reviewed call
whose ``off_topic`` criterion failed (the HOM-77 fixtures).

Every deny case runs next to an allow control that differs in one thing, and checks
that nothing was written: no proposal row, no delivery request, no success event.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, func, select, update

from backend.database.saas_models import (
    ActionDeliveryRequest,
    ActionItem,
    ActionProposal,
    AnalysisRun,
    AuditEvent,
    CoachingTask,
    ConversationExternalLink,
    EvidenceSpan,
    ExternalRecord,
    IntegrationMappingProfile,
    Meeting,
    MeetingStatus,
    RecordScope,
    ReviewResultRecord,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    Workspace,
)
from backend.security.authorization import (
    API_KEY_SCOPES,
    ROLE_PERMISSIONS,
    Permission,
    WorkspaceRole,
)
from backend.security.context import CSRF_COOKIE
from backend.services import action_proposal_service as service
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_capabilities_oracle import REASONS, _apply_state, _restore_install
from backend.tests.test_review_appeals_api import Queue, build_appeal_queue, filed, reviewed
from backend.tests.test_review_queue_access import code

PROPOSALS = "/api/v1/action-proposals"
DESCRIPTION = "Send the signed offer to the customer before the Friday call."
INSTRUCTION = "Ask the weather question only after the needs check."
EDITED_TITLE = "Offer follow-up for the Friday call"
# External ids are unique per workspace, system and record type.
_EXTERNAL_IDS = itertools.count(1001)
TICKET_KEYS = {
    "schema",
    "title",
    "description",
    "priority",
    "due_at",
    "assignee_text",
    "kind",
    "meeting_id",
    "action_item_id",
}
CRM_KEYS = {
    "schema",
    "source_system",
    "record_type",
    "external_id",
    "note",
    "due_at",
    "priority",
    "kind",
    "meeting_id",
    "action_item_id",
}
COACHING_KEYS = {
    "schema",
    "review_item_id",
    "criterion_id",
    "agent_user_id",
    "owner_user_id",
    "due_at",
    "instruction",
}
VIEW_KEYS = {
    "id",
    "meeting_id",
    "action_type",
    "source_type",
    "action_item_id",
    "review_item_id",
    "criterion_id",
    "target_system",
    "target_external_record_id",
    "payload",
    "payload_hash",
    "payload_version",
    "source_transcript_revision_id",
    "status",
    "revision",
    "created_by_user_id",
    "created_at",
    "updated_at",
    "decided_by_user_id",
    "decided_at",
    "reject_reason",
    "revoked_by_user_id",
    "revoked_at",
    "revoke_reason",
    "evidence_span_ids",
    "stale",
    "delivery_request",
}
AUDIT_KEYS = {
    "proposal_id",
    "meeting_id",
    "action_type",
    "source_type",
    "payload_version",
    "payload_hash",
    "revision",
    "reason",
    "reason_code",
    "stale_reason",
    "delivery_request_id",
    "edited",
}


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_appeal_queue(client, email_outbox, monkeypatch)


# --- helpers ------------------------------------------------------------------------------


def due(delta: timedelta = timedelta(days=7)) -> str:
    return (datetime.now(UTC) + delta).isoformat()


def canonical_hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def conversation(queue: Queue, *, team_id: str | None = "team_a", agent=None) -> dict:
    """A completed call with one current transcript revision."""
    team = queue.team_a if team_id == "team_a" else team_id
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=queue.workspace_id,
            created_by_user_id=queue.owner.user_id,
            title="Call with an action",
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
            team_id=team,
            agent_user_id=agent.user_id if agent is not None else None,
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=queue.workspace_id,
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="en",
            segment_count=0,
            is_current=True,
        )
        db.add(revision)
        db.commit()
        return {"meeting_id": meeting.id, "revision_id": revision.id}


def new_revision(meeting_id: str) -> str:
    """A correction: the meeting gets a new current revision."""
    with inspection_session_factory()() as db:
        old = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id, TranscriptRevision.is_current
            )
        )
        db.execute(
            update(TranscriptRevision)
            .where(TranscriptRevision.id == old.id)
            .values(is_current=False)
        )
        db.flush()
        revision = TranscriptRevision(
            workspace_id=old.workspace_id,
            meeting_id=meeting_id,
            revision_number=old.revision_number + 1,
            parent_revision_id=old.id,
            source=TranscriptSource.CORRECTION,
            reason="correction",
            language="en",
            segment_count=0,
            is_current=True,
        )
        db.add(revision)
        db.commit()
        return revision.id


def action_item(
    queue: Queue,
    call: dict,
    *,
    status: str = "open",
    kind: str | None = None,
    run_revision_id: str | None = None,
    description: str = DESCRIPTION,
) -> str:
    """An action item of the call; with ``run_revision_id`` an AI item of a run on it."""
    with inspection_session_factory()() as db:
        run_id = None
        if run_revision_id is not None:
            run = AnalysisRun(
                workspace_id=queue.workspace_id,
                meeting_id=call["meeting_id"],
                transcript_revision_id=run_revision_id,
                state=StageState.COMPLETED,
                is_mock=False,
                is_current=False,
            )
            db.add(run)
            db.flush()
            run_id = run.id
        item = ActionItem(
            workspace_id=queue.workspace_id,
            meeting_id=call["meeting_id"],
            description=description,
            assignee_text="Sara from sales",
            due_at=datetime(2026, 12, 4, 9, 30, tzinfo=UTC),
            priority="high",
            status=status,
            kind=kind,
            analysis_run_id=run_id,
            created_by="ai" if run_id else "user",
        )
        db.add(item)
        db.commit()
        return item.id


def set_item(item_id: str, **values: Any) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ActionItem).where(ActionItem.id == item_id).values(**values))
        db.commit()


def _organization_id(workspace_id: str) -> str:
    with inspection_session_factory()() as db:
        return db.get(Workspace, workspace_id).organization_id


def crm_record(
    queue: Queue,
    call: dict,
    *,
    source_system: str = "zendesk",
    link_type: str | None = "customer",
    is_active: bool = True,
    external_id: str | None = None,
    meeting_id: str | None = None,
) -> str:
    """An external record of the workspace, linked to the call as ``link_type``."""
    organization_id = _organization_id(queue.workspace_id)
    external_id = external_id or f"CUST-{next(_EXTERNAL_IDS)}"
    record_type = link_type if link_type in ("customer", "ticket", "agent") else "customer"
    with inspection_session_factory()() as db:
        record = ExternalRecord(
            organization_id=organization_id,
            workspace_id=queue.workspace_id,
            source_system=source_system,
            record_type=record_type,
            external_id=external_id,
            is_active=is_active,
        )
        db.add(record)
        db.flush()
        if link_type is not None:
            db.add(
                ConversationExternalLink(
                    organization_id=organization_id,
                    workspace_id=queue.workspace_id,
                    meeting_id=meeting_id or call["meeting_id"],
                    source_system=source_system,
                    link_type=link_type,
                    external_record_id=record.id,
                    profile_version=1,
                )
            )
        db.commit()
        return record.id


def mapping_profile(queue: Queue, *, source_system: str = "zendesk", active: bool = True) -> str:
    with inspection_session_factory()() as db:
        existing = db.scalar(
            select(IntegrationMappingProfile).where(
                IntegrationMappingProfile.workspace_id == queue.workspace_id,
                IntegrationMappingProfile.source_system == source_system,
            )
        )
        if existing is not None:
            existing.is_active = active
            db.commit()
            return existing.id
        profile = IntegrationMappingProfile(
            organization_id=_organization_id(queue.workspace_id),
            workspace_id=queue.workspace_id,
            source_system=source_system,
            conversation_source_system="pbx",
            field_map={"customer_id": "customer.id"},
            is_active=active,
        )
        db.add(profile)
        db.commit()
        return profile.id


def set_record(record_id: str, **values: Any) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ExternalRecord).where(ExternalRecord.id == record_id).values(**values))
        db.commit()


def crm_body(item_id: str, record_id: str | None, **overrides: Any) -> dict:
    return {
        "action_type": "crm_update",
        "action_item_id": item_id,
        "target_system": "zendesk",
        "target_external_record_id": record_id,
        **overrides,
    }


def ticket_body(item_id: str, **overrides: Any) -> dict:
    return {
        "action_type": "ticket_create",
        "action_item_id": item_id,
        "target_system": "zendesk",
        **overrides,
    }


def coaching_body(review_item_id: str, owner, **overrides: Any) -> dict:
    return {
        "action_type": "coaching_task",
        "review_item_id": review_item_id,
        "criterion_id": "off_topic",
        "owner_user_id": owner.user_id,
        "due_at": due(),
        "instruction": INSTRUCTION,
        **overrides,
    }


def create(persona, body: dict):
    return persona.post(PROPOSALS, json=body)


def created(persona, body: dict) -> dict:
    response = create(persona, body)
    assert response.status_code == 201, response.text
    return response.json()


def crm_setup(queue: Queue, **item: Any) -> dict:
    call = conversation(queue)
    item_id = action_item(queue, call, **item)
    external_id = f"CUST-{next(_EXTERNAL_IDS)}"
    record_id = crm_record(queue, call, external_id=external_id)
    return {**call, "item_id": item_id, "record_id": record_id, "external_id": external_id}


def crm_on(queue: Queue, persona=None, **item: Any) -> dict:
    setup = crm_setup(queue, **item)
    proposal = created(persona or queue.sup, crm_body(setup["item_id"], setup["record_id"]))
    return {**proposal, "setup": setup}


def ticket_on(queue: Queue, persona=None, **item: Any) -> dict:
    mapping_profile(queue)
    call = conversation(queue)
    item_id = action_item(queue, call, **item)
    proposal = created(persona or queue.sup, ticket_body(item_id))
    return {**proposal, "setup": {**call, "item_id": item_id}}


def coaching_on(queue: Queue, persona=None, **overrides: Any) -> dict:
    target = reviewed(queue)
    proposal = created(persona or queue.sup, coaching_body(target.id, queue.plain, **overrides))
    return {**proposal, "setup": {"meeting_id": target.meeting_id, "review_item_id": target.id}}


def act(persona, proposal: dict, action: str, **body: Any):
    payload = {"expected_revision": proposal["revision"], **body}
    return persona.post(f"{PROPOSALS}/{proposal['id']}/{action}", json=payload)


def approve(persona, proposal: dict, **overrides: Any):
    body = {
        "payload_hash": proposal["payload_hash"],
        "source_transcript_revision_id": proposal["source_transcript_revision_id"],
        **overrides,
    }
    return act(persona, proposal, "approve", **body)


def approved(persona, proposal: dict, **overrides: Any) -> dict:
    response = approve(persona, proposal, **overrides)
    assert response.status_code == 201, response.text
    return response.json()


def acted(persona, proposal: dict, action: str, **body: Any) -> dict:
    response = act(persona, proposal, action, **body)
    assert response.status_code == 200, response.text
    return response.json()


def read(persona, proposal: dict) -> dict:
    response = persona.get(f"{PROPOSALS}/{proposal['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def stored(proposal_id: str) -> ActionProposal:
    with inspection_session_factory()() as db:
        row = db.get(ActionProposal, proposal_id)
        assert row is not None
        db.expunge(row)
        return row


def requests_of(proposal_id: str) -> list[ActionDeliveryRequest]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(ActionDeliveryRequest).where(ActionDeliveryRequest.proposal_id == proposal_id)
        ).all()
        for row in rows:
            db.expunge(row)
        return list(rows)


def events(proposal_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_type == "action_proposal", AuditEvent.target_id == proposal_id)
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        ).all()
        for row in rows:
            db.expunge(row)
        return list(rows)


def counts() -> dict[str, int]:
    with inspection_session_factory()() as db:
        return {
            "proposals": db.scalar(select(func.count()).select_from(ActionProposal)) or 0,
            "requests": db.scalar(select(func.count()).select_from(ActionDeliveryRequest)) or 0,
            "events": db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type.like("action_proposal.%"))
            )
            or 0,
        }


def refused(response, status: int, error: str) -> dict:
    assert response.status_code == status, response.text
    assert code(response) == error, response.text
    return response.json()["error"]


def routes(proposal: dict) -> list[tuple[str, str, dict | None]]:
    base = f"{PROPOSALS}/{proposal['id']}"
    revision = {"expected_revision": proposal["revision"]}
    return [
        ("POST", PROPOSALS, {"action_type": "ticket_create", "action_item_id": "x"}),
        ("GET", PROPOSALS, None),
        ("GET", base, None),
        ("POST", f"{base}/refresh", revision),
        (
            "POST",
            f"{base}/approve",
            {
                **revision,
                "payload_hash": proposal["payload_hash"],
                "source_transcript_revision_id": proposal["source_transcript_revision_id"],
            },
        ),
        ("POST", f"{base}/reject", {**revision, "reason": "other"}),
        ("POST", f"{base}/revoke", {**revision, "reason": "other"}),
    ]


def send(persona, method: str, path: str, body: dict | None, *, csrf: bool = True):
    headers = dict(persona.headers)
    if not csrf:
        headers.pop("X-CSRF-Token", None)
    return persona.client.request(method, path, json=body, headers=headers, cookies=persona.cookies)


# --- 1. permissions, the module guard, API keys and CSRF ---------------------------------


def test_the_two_permissions_belong_to_the_designed_roles_and_never_to_a_key():
    assert Permission.ACTION_PROPOSE.value == "action:propose"
    assert Permission.ACTION_APPROVE.value == "action:approve"
    propose = {
        role for role, granted in ROLE_PERMISSIONS.items() if Permission.ACTION_PROPOSE in granted
    }
    approve_roles = {
        role for role, granted in ROLE_PERMISSIONS.items() if Permission.ACTION_APPROVE in granted
    }
    assert propose == {WorkspaceRole.OWNER, WorkspaceRole.ADMIN, WorkspaceRole.MEMBER}
    assert approve_roles == {WorkspaceRole.OWNER, WorkspaceRole.ADMIN}
    assert Permission.ACTION_PROPOSE not in API_KEY_SCOPES
    assert Permission.ACTION_APPROVE not in API_KEY_SCOPES


def test_a_viewer_is_refused_on_every_route_and_an_owner_is_not(queue):
    proposal = crm_on(queue)
    before = counts()
    for method, path, body in routes(proposal):
        response = send(queue.viewer, method, path, body)
        refused(response, 403, "permission_denied")
    assert counts() == before
    # Allow control: the owner reads the same proposal.
    assert read(queue.owner, proposal)["id"] == proposal["id"]


def test_a_member_proposes_and_reads_but_never_approves_rejects_or_revokes(queue):
    proposal = crm_on(queue, queue.plain)
    assert read(queue.plain, proposal)["status"] == "pending"
    before = counts()
    for action, body in (
        (
            "approve",
            {
                "payload_hash": proposal["payload_hash"],
                "source_transcript_revision_id": proposal["source_transcript_revision_id"],
            },
        ),
        ("reject", {"reason": "other"}),
        ("revoke", {"reason": "other"}),
    ):
        refused(act(queue.plain, proposal, action, **body), 403, "permission_denied")
    assert counts() == before
    # Allow controls: an admin approves; the member refreshes their own kind of write.
    other = crm_on(queue, queue.plain)
    assert acted(queue.plain, other, "refresh")["payload_version"] == 2
    assert approved(queue.admin, proposal)["status"] == "approved"


def test_an_api_key_with_every_scope_is_not_accepted_on_any_route(queue):
    proposal = crm_on(queue)
    owner = queue.owner
    minted = _mint(
        owner.client,
        owner.headers,
        owner.cookies,
        name="every-scope",
        scopes=sorted(scope.value for scope in API_KEY_SCOPES),
    )
    assert minted.status_code == 201, minted.text
    key = minted.json()["key"]
    owner.client.cookies.clear()
    for method, path, body in routes(proposal):
        response = owner.client.request(
            method, path, json=body, headers=_bearer(key, **{"X-Workspace-ID": queue.workspace_id})
        )
        refused(response, 401, "api_key_not_accepted")
    # Allow control: the same caller's session reads it.
    assert read(owner, proposal)["id"] == proposal["id"]


@pytest.mark.parametrize("state", ["admin", "license", "install"])
def test_the_integrations_module_refuses_before_the_permission(queue, settings, state):
    proposal = crm_on(queue)
    client = queue.owner.client
    _apply_state(client, settings, queue.workspace_id, "integrations", state)
    try:
        before = counts()
        for persona in (queue.owner, queue.viewer):  # the viewer lacks the permission
            for method, path, body in routes(proposal):
                error = refused(send(persona, method, path, body), 403, "module_disabled")
                assert error["details"] == {"module": "integrations", "reason": REASONS[state]}
        assert counts() == before
    finally:
        _restore_install(client, settings)
    # Allow control: back on, the owner reads it.
    if state == "admin":
        with inspection_session_factory()() as db:
            workspace = db.get(Workspace, queue.workspace_id)
            workspace.settings_json = {
                key: value
                for key, value in (workspace.settings_json or {}).items()
                if key != "modules"
            }
            db.commit()
    if state != "license":
        assert read(queue.owner, proposal)["id"] == proposal["id"]


def test_every_write_needs_the_csrf_token(queue):
    proposal = crm_on(queue)
    before = counts()
    for method, path, body in routes(proposal):
        if method != "POST":
            continue
        refused(send(queue.owner, method, path, body, csrf=False), 403, "csrf_failed")
    assert counts() == before
    # Allow control: with the token, the reject lands.
    assert acted(queue.owner, proposal, "reject", reason="not_needed")["status"] == "rejected"


# --- 2. record scope and other workspaces --------------------------------------------------


def _team_scope(queue: Queue) -> None:
    with inspection_session_factory()() as db:
        db.get(Workspace, queue.workspace_id).record_scope = RecordScope.TEAM
        db.commit()


def test_record_scope_hides_a_proposal_outside_the_scope(queue):
    _team_scope(queue)
    # Created by the owner on a call of team B: ``sup`` supervises team A only.
    call = conversation(queue, team_id=queue.team_b)
    item_id = action_item(queue, call)
    record_id = crm_record(queue, call)
    outside = created(queue.owner, crm_body(item_id, record_id))
    for method, path, body in routes(outside)[2:]:
        if path.endswith(("approve", "reject", "revoke")):
            continue  # a member never holds action:approve
        refused(send(queue.sup, method, path, body), 404, "action_proposal_not_found")
    listed = queue.sup.get(PROPOSALS).json()["items"]
    assert outside["id"] not in [row["id"] for row in listed]
    # Creating from that call's item is out of scope too.
    other_item = action_item(queue, call)
    refused(create(queue.sup, crm_body(other_item, record_id)), 404, "action_item_not_found")
    # Allow control: a call of team A is inside ``sup``'s scope.
    inside = crm_on(queue, queue.owner)
    assert read(queue.sup, inside)["id"] == inside["id"]
    assert inside["id"] in [row["id"] for row in queue.sup.get(PROPOSALS).json()["items"]]


def test_ids_of_another_workspace_answer_404(queue, client, email_outbox, monkeypatch):
    proposal = crm_on(queue)
    from backend.tests.conftest import login_user, register_user

    credentials = register_user(client, email_outbox, email="proposals-stranger@example.com")
    session = login_user(client, credentials)
    cookies = {name: value for name, value in client.cookies.items()}
    headers = {
        "X-Workspace-ID": session["workspaces"][0]["id"],
        "X-CSRF-Token": cookies[CSRF_COOKIE],
    }
    response = client.get(f"{PROPOSALS}/{proposal['id']}", headers=headers, cookies=cookies)
    refused(response, 404, "action_proposal_not_found")
    response = client.post(
        f"{PROPOSALS}/{proposal['id']}/refresh",
        json={"expected_revision": 1},
        headers=headers,
        cookies=cookies,
    )
    refused(response, 404, "action_proposal_not_found")
    response = client.post(
        PROPOSALS,
        json=crm_body(proposal["action_item_id"], proposal["target_external_record_id"]),
        headers=headers,
        cookies=cookies,
    )
    refused(response, 404, "action_item_not_found")


# --- 3. create ------------------------------------------------------------------------------


def test_create_builds_the_exact_ticket_payload_and_pins_the_source(queue):
    proposal = ticket_on(queue, kind="next_step")
    setup = proposal["setup"]
    assert set(proposal) - {"setup"} == VIEW_KEYS
    assert proposal["status"] == "pending" and proposal["revision"] == 1
    assert proposal["payload_version"] == 1 and proposal["action_type"] == "ticket_create"
    assert proposal["source_type"] == "action_item"
    assert proposal["source_transcript_revision_id"] == setup["revision_id"]
    assert proposal["created_by_user_id"] == queue.sup.user_id
    payload = proposal["payload"]
    assert set(payload) == TICKET_KEYS
    assert payload == {
        "schema": "ticket_create.v1",
        "title": DESCRIPTION[:120].strip(),
        "description": DESCRIPTION,
        "priority": "high",
        "due_at": "2026-12-04T09:30:00+00:00",
        "assignee_text": "Sara from sales",
        "kind": "next_step",
        "meeting_id": setup["meeting_id"],
        "action_item_id": setup["item_id"],
    }
    assert proposal["payload_hash"] == canonical_hash(payload)
    assert proposal["stale"] == {"is_stale": False, "reason": None}
    assert proposal["delivery_request"] is None
    row = stored(proposal["id"])
    assert row.payload_json == payload
    assert row.source_digest == canonical_hash(
        {
            "id": setup["item_id"],
            "description": DESCRIPTION,
            "assignee_text": "Sara from sales",
            "assignee_user_id": None,
            "due_at": "2026-12-04T09:30:00+00:00",
            "priority": "high",
            "status": "open",
            "kind": "next_step",
            "analysis_run_id": None,
        }
    )


def test_create_builds_the_exact_crm_payload_from_the_linked_record(queue):
    proposal = crm_on(queue, kind="commitment")
    setup = proposal["setup"]
    payload = proposal["payload"]
    assert set(payload) == CRM_KEYS
    assert payload == {
        "schema": "crm_update.v1",
        "source_system": "zendesk",
        "record_type": "customer",
        "external_id": setup["external_id"],
        "note": DESCRIPTION,
        "due_at": "2026-12-04T09:30:00+00:00",
        "priority": "high",
        "kind": "commitment",
        "meeting_id": setup["meeting_id"],
        "action_item_id": setup["item_id"],
    }
    assert proposal["target_system"] == "zendesk"
    assert proposal["target_external_record_id"] == setup["record_id"]
    assert proposal["payload_hash"] == canonical_hash(payload)


def test_the_kind_label_keeps_a_next_step_apart_from_a_commitment(queue):
    commitment = ticket_on(queue, kind="commitment")
    next_step = ticket_on(queue, kind="next_step")
    unlabeled = ticket_on(queue)
    assert commitment["payload"]["kind"] == "commitment"
    assert next_step["payload"]["kind"] == "next_step"
    assert unlabeled["payload"]["kind"] is None
    assert commitment["payload_hash"] != next_step["payload_hash"]


def test_create_builds_the_exact_coaching_payload(queue):
    when = due(timedelta(days=5))
    proposal = coaching_on(queue, due_at=when, instruction=f"  {INSTRUCTION}\n")
    setup = proposal["setup"]
    payload = proposal["payload"]
    assert set(payload) == COACHING_KEYS
    assert payload == {
        "schema": "coaching_task.v1",
        "review_item_id": setup["review_item_id"],
        "criterion_id": "off_topic",
        "agent_user_id": queue.agent.user_id,
        "owner_user_id": queue.plain.user_id,
        "due_at": datetime.fromisoformat(when).astimezone(UTC).isoformat(),
        "instruction": INSTRUCTION,
    }
    assert proposal["source_type"] == "review_finding"
    assert proposal["target_system"] is None and proposal["target_external_record_id"] is None
    assert proposal["evidence_span_ids"] == []
    # Nothing is written to the coaching tables: HOM-136 delivers later.
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(CoachingTask)) == 0


def test_the_evidence_of_an_item_source_is_listed_in_order(queue):
    call = conversation(queue)
    item_id = action_item(queue, call)
    with inspection_session_factory()() as db:
        segment = TranscriptSegment(
            workspace_id=queue.workspace_id,
            meeting_id=call["meeting_id"],
            revision_id=call["revision_id"],
            sequence=0,
            start_ms=0,
            end_ms=1000,
            text="I will send the offer.",
        )
        db.add(segment)
        db.flush()
        spans = []
        for index, moment in enumerate((2, 1)):
            span = EvidenceSpan(
                workspace_id=queue.workspace_id,
                meeting_id=call["meeting_id"],
                transcript_revision_id=call["revision_id"],
                segment_id=segment.id,
                speaker_key="agent",
                finding_type="action_item",
                finding_id=item_id,
                finding_index=index,
                text_start=0,
                text_end=4,
                start_ms=0,
                end_ms=1000,
                evidence_type="turn_reference",
                created_at=datetime(2026, 11, 1, tzinfo=UTC) + timedelta(minutes=moment),
            )
            db.add(span)
            db.flush()
            spans.append(span)
        db.commit()
        expected = [spans[1].id, spans[0].id]  # oldest first
    record_id = crm_record(queue, call)
    proposal = created(queue.sup, crm_body(item_id, record_id))
    assert proposal["evidence_span_ids"] == expected


@pytest.mark.parametrize(
    ("label", "body_of", "field"),
    [
        (
            "unknown type",
            lambda s: {**ticket_body(s["item"]), "action_type": "email"},
            "action_type",
        ),
        ("crm without item", lambda s: crm_body(None, s["record"]), "action_item_id"),
        (
            "crm with a finding",
            lambda s: crm_body(s["item"], s["record"], review_item_id=s["item"]),
            "review_item_id",
        ),
        (
            "ticket with a criterion",
            lambda s: ticket_body(s["item"], criterion_id="off_topic"),
            "criterion_id",
        ),
        ("crm without record", lambda s: crm_body(s["item"], None), "target_external_record_id"),
        (
            "crm without system",
            lambda s: crm_body(s["item"], s["record"], target_system=None),
            "target_system",
        ),
        (
            "ticket with record",
            lambda s: ticket_body(s["item"], target_external_record_id=s["record"]),
            "target_external_record_id",
        ),
        (
            "ticket without system",
            lambda s: ticket_body(s["item"], target_system=None),
            "target_system",
        ),
        (
            "coaching with item",
            lambda s: {**coaching_body("x", s["owner"]), "action_item_id": s["item"]},
            "action_item_id",
        ),
        (
            "coaching without criterion",
            lambda s: {**coaching_body("x", s["owner"]), "criterion_id": None},
            "criterion_id",
        ),
        (
            "coaching with system",
            lambda s: {**coaching_body("x", s["owner"]), "target_system": "zendesk"},
            "target_system",
        ),
        ("bad slug", lambda s: ticket_body(s["item"], target_system="Bad Slug"), "target_system"),
    ],
)
def test_create_refuses_a_bad_shape(queue, label, body_of, field):
    setup = crm_setup(queue)
    sample = {"item": setup["item_id"], "record": setup["record_id"], "owner": queue.plain}
    before = counts()
    error = refused(create(queue.sup, body_of(sample)), 422, "action_proposal_invalid")
    assert field in error["message"], (label, error)
    assert counts() == before
    # Allow control: the same source with the right shape.
    assert created(queue.sup, crm_body(setup["item_id"], setup["record_id"]))["status"] == "pending"


def test_create_body_forbids_unknown_keys(queue):
    setup = crm_setup(queue)
    response = create(queue.sup, {**crm_body(setup["item_id"], setup["record_id"]), "x": 1})
    assert response.status_code == 422, response.text
    assert counts()["proposals"] == 0


def test_the_source_item_must_exist_be_visible_and_be_actionable(queue):
    setup = crm_setup(queue)
    record = setup["record_id"]
    refused(create(queue.sup, crm_body("0" * 36, record)), 404, "action_item_not_found")
    # A deleted call hides its items.
    gone = crm_setup(queue)
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting)
            .where(Meeting.id == gone["meeting_id"])
            .values(deleted_at=datetime.now(UTC))
        )
        db.commit()
    refused(
        create(queue.sup, crm_body(gone["item_id"], gone["record_id"])),
        404,
        "action_item_not_found",
    )
    for status in ("done", "cancelled"):
        item = action_item(queue, setup, status=status)
        refused(create(queue.sup, crm_body(item, record)), 409, "action_item_not_actionable")
    assert counts()["proposals"] == 0
    # Allow controls: open and in progress.
    for status in ("open", "in_progress"):
        item = action_item(queue, setup, status=status)
        assert created(queue.sup, crm_body(item, record))["status"] == "pending"


def test_the_finding_runs_the_coaching_create_checks_with_their_codes(queue):
    target = reviewed(queue)
    body = coaching_body(target.id, queue.plain)
    refused(create(queue.sup, {**body, "review_item_id": "0" * 36}), 404, "review_item_not_found")
    # ``sup_b`` supervises team B only: the review item is not visible.
    refused(create(queue.sup_b, body), 404, "review_item_not_found")
    # The call's agent never coaches their own call (an admin sees every review item).
    own = reviewed(queue, agent=queue.admin)
    refused(create(queue.admin, coaching_body(own.id, queue.plain)), 422, "coaching_actor_is_agent")
    refused(create(queue.sup, {**body, "criterion_id": "nope"}), 404, "criterion_not_found")
    refused(
        create(queue.sup, {**body, "criterion_id": "greeting"}), 422, "coaching_finding_not_failed"
    )
    refused(
        create(queue.sup, {**body, "owner_user_id": queue.agent.user_id}),
        422,
        "coaching_owner_is_agent",
    )
    refused(
        create(queue.sup, {**body, "due_at": due(-timedelta(hours=1))}), 422, "coaching_due_invalid"
    )
    refused(create(queue.sup, {**body, "instruction": "   "}), 422, "coaching_instruction_required")
    assert counts()["proposals"] == 0
    # An active coaching task for the finding blocks the proposal (same code as HOM-77).
    task = queue.sup.post(
        f"/api/v1/review-items/{target.id}/coaching-tasks",
        json={k: body[k] for k in ("criterion_id", "owner_user_id", "due_at", "instruction")},
    )
    assert task.status_code == 201, task.text
    refused(create(queue.sup, body), 409, "coaching_task_exists")
    # Allow control: another reviewed call with the same body shape.
    other = reviewed(queue)
    assert created(queue.sup, coaching_body(other.id, queue.plain))["status"] == "pending"


def test_create_pins_the_current_revision_and_refuses_a_stale_source(queue):
    call = conversation(queue)
    record = crm_record(queue, call)
    # An AI item read from the current revision is pinned to it.
    item = action_item(queue, call, run_revision_id=call["revision_id"])
    assert (
        created(queue.sup, crm_body(item, record))["source_transcript_revision_id"]
        == call["revision_id"]
    )
    # After a correction, an item of the old run is stale; a person item is not.
    current = new_revision(call["meeting_id"])
    old_ai = action_item(queue, call, run_revision_id=call["revision_id"])
    refused(create(queue.sup, crm_body(old_ai, record)), 409, "action_proposal_source_stale")
    person = action_item(queue, call)
    assert created(queue.sup, crm_body(person, record))["source_transcript_revision_id"] == current
    # No current revision at all.
    with inspection_session_factory()() as db:
        db.execute(
            update(TranscriptRevision)
            .where(TranscriptRevision.meeting_id == call["meeting_id"])
            .values(is_current=False)
        )
        db.commit()
    bare = action_item(queue, call)
    refused(create(queue.sup, crm_body(bare, record)), 409, "transcript_missing")


def test_a_crm_target_is_an_active_linked_record_of_the_same_system(queue):
    call = conversation(queue)
    item = action_item(queue, call)
    unlinked = crm_record(queue, call, link_type=None, external_id="CUST-2")
    inactive = crm_record(
        queue, call, is_active=False, external_id="CUST-3", source_system="hubspot"
    )
    agent_link = crm_record(
        queue, call, link_type="agent", external_id="AG-1", source_system="odoo"
    )
    elsewhere = conversation(queue)
    other_call = crm_record(queue, elsewhere, external_id="CUST-4")
    for record, system in (
        (unlinked, "zendesk"),
        (inactive, "hubspot"),
        (agent_link, "odoo"),
        (other_call, "zendesk"),
        ("0" * 36, "zendesk"),
    ):
        response = create(queue.sup, crm_body(item, record, target_system=system))
        refused(response, 422, "action_proposal_target_invalid")
    linked = crm_record(queue, call, external_id="CUST-5", source_system="salesforce")
    # The system must be the record's own.
    refused(
        create(queue.sup, crm_body(item, linked, target_system="zendesk")),
        422,
        "action_proposal_target_invalid",
    )
    assert counts()["proposals"] == 0
    # Allow controls: a customer link and a ticket link.
    assert (
        created(queue.sup, crm_body(item, linked, target_system="salesforce"))["status"]
        == "pending"
    )
    ticket_link = crm_record(
        queue, call, link_type="ticket", external_id="T-9", source_system="freshdesk"
    )
    second = action_item(queue, call)
    assert (
        created(queue.sup, crm_body(second, ticket_link, target_system="freshdesk"))["status"]
        == "pending"
    )


def test_a_ticket_target_is_an_active_mapping_profile(queue):
    call = conversation(queue)
    item = action_item(queue, call)
    refused(create(queue.sup, ticket_body(item)), 422, "action_proposal_target_invalid")
    mapping_profile(queue, active=False)
    refused(create(queue.sup, ticket_body(item)), 422, "action_proposal_target_invalid")
    mapping_profile(queue, active=True)
    refused(
        create(queue.sup, ticket_body(item, target_system="jira")),
        422,
        "action_proposal_target_invalid",
    )
    assert counts()["proposals"] == 0
    assert created(queue.sup, ticket_body(item))["status"] == "pending"


def test_one_open_proposal_per_source_and_type(queue):
    proposal = crm_on(queue)
    setup = proposal["setup"]
    body = crm_body(setup["item_id"], setup["record_id"])
    before = counts()
    refused(create(queue.sup, body), 409, "action_proposal_exists")
    assert counts() == before
    # Another type on the same item is another proposal.
    mapping_profile(queue)
    ticket = created(queue.sup, ticket_body(setup["item_id"]))
    # An approved one still blocks (one logical effect per source and type).
    approved(queue.owner, proposal)
    refused(create(queue.sup, body), 409, "action_proposal_exists")
    # A revoke frees the slot.
    revoked = acted(queue.owner, stored_view(queue, proposal), "revoke", reason="no_longer_needed")
    assert revoked["status"] == "revoked"
    assert created(queue.sup, body)["status"] == "pending"
    # A reject frees it too.
    acted(queue.owner, ticket, "reject", reason="duplicate")
    assert created(queue.sup, ticket_body(setup["item_id"]))["status"] == "pending"
    # The same for a finding.
    coaching = coaching_on(queue)
    finding = coaching_body(coaching["setup"]["review_item_id"], queue.plain)
    refused(create(queue.sup, finding), 409, "action_proposal_exists")
    acted(queue.owner, coaching, "reject", reason="not_needed")
    assert created(queue.sup, finding)["status"] == "pending"


def stored_view(queue: Queue, proposal: dict) -> dict:
    return read(queue.owner, proposal)


# --- 4. staleness, refresh and binding -----------------------------------------------------


def _assert_stale(queue: Queue, proposal: dict, reason: str) -> None:
    view = read(queue.owner, proposal)
    assert view["stale"] == {"is_stale": True, "reason": reason}
    before = counts()
    error = refused(approve(queue.owner, view), 409, "action_proposal_stale")
    assert error["details"] == {"reason": reason}
    assert requests_of(proposal["id"]) == []
    assert stored(proposal["id"]).status == "pending"
    assert counts()["requests"] == before["requests"]


def test_a_changed_source_is_stale_until_refreshed(queue):
    proposal = crm_on(queue)
    set_item(proposal["setup"]["item_id"], description="Send the revised offer instead.")
    _assert_stale(queue, proposal, "source_changed")
    view = read(queue.owner, proposal)
    fresh = acted(queue.sup, view, "refresh")
    assert fresh["payload_version"] == 2 and fresh["revision"] == view["revision"] + 1
    assert fresh["payload"]["note"] == "Send the revised offer instead."
    assert fresh["stale"] == {"is_stale": False, "reason": None}
    assert approved(queue.owner, fresh)["payload_version"] == 2


def test_a_new_transcript_revision_is_stale_until_refreshed(queue):
    proposal = crm_on(queue)
    current = new_revision(proposal["setup"]["meeting_id"])
    _assert_stale(queue, proposal, "revision_changed")
    fresh = acted(queue.sup, read(queue.owner, proposal), "refresh")
    assert fresh["source_transcript_revision_id"] == current
    assert approved(queue.owner, fresh)["status"] == "approved"


def test_a_closed_item_is_stale_and_its_refresh_is_refused(queue):
    proposal = crm_on(queue)
    set_item(proposal["setup"]["item_id"], status="done")
    _assert_stale(queue, proposal, "source_not_actionable")
    refused(
        act(queue.sup, read(queue.owner, proposal), "refresh"), 409, "action_item_not_actionable"
    )
    # Back to in progress: the refresh lands.
    set_item(proposal["setup"]["item_id"], status="in_progress")
    view = read(queue.owner, proposal)
    assert view["stale"] == {"is_stale": True, "reason": "source_changed"}
    assert acted(queue.sup, view, "refresh")["stale"]["is_stale"] is False


def test_a_removed_item_is_stale(queue):
    proposal = crm_on(queue)
    with inspection_session_factory()() as db:
        db.execute(delete(ActionItem).where(ActionItem.id == proposal["setup"]["item_id"]))
        db.commit()
    _assert_stale(queue, proposal, "source_removed")
    assert read(queue.owner, proposal)["action_item_id"] is None


def test_an_inactive_target_is_stale(queue):
    proposal = crm_on(queue)
    set_record(proposal["setup"]["record_id"], is_active=False)
    _assert_stale(queue, proposal, "target_invalid")
    refused(
        act(queue.sup, read(queue.owner, proposal), "refresh"),
        422,
        "action_proposal_target_invalid",
    )
    set_record(proposal["setup"]["record_id"], is_active=True)
    assert approved(queue.owner, read(queue.owner, proposal))["status"] == "approved"


def test_a_ticket_without_its_active_profile_is_stale(queue):
    proposal = ticket_on(queue)
    mapping_profile(queue, active=False)
    _assert_stale(queue, proposal, "target_invalid")


def _flip_human_outcome(meeting_id: str, criterion_id: str, outcome: str) -> None:
    with inspection_session_factory()() as db:
        human = db.scalar(
            select(ReviewResultRecord).where(ReviewResultRecord.meeting_id == meeting_id)
        )
        result = json.loads(json.dumps(human.result))
        for entry in result["criteria"]:
            if entry["criterion_id"] == criterion_id:
                entry["outcome"] = outcome
        db.execute(
            update(ReviewResultRecord)
            .where(ReviewResultRecord.id == human.id)
            .values(result=result)
        )
        db.commit()


def test_a_finding_that_is_no_longer_failed_is_stale(queue):
    proposal = coaching_on(queue)
    _flip_human_outcome(proposal["setup"]["meeting_id"], "off_topic", "passed")
    _assert_stale(queue, proposal, "finding_not_failed")
    refused(
        act(queue.sup, read(queue.owner, proposal), "refresh"), 422, "coaching_finding_not_failed"
    )


def test_a_removed_review_item_is_stale_and_still_readable_and_rejectable(queue):
    proposal = coaching_on(queue)
    from backend.database.saas_models import ReviewItem

    with inspection_session_factory()() as db:
        db.execute(delete(ReviewItem).where(ReviewItem.id == proposal["setup"]["review_item_id"]))
        db.commit()
    _assert_stale(queue, proposal, "source_removed")
    view = read(queue.sup, proposal)
    assert view["review_item_id"] is None
    assert acted(queue.owner, view, "reject", reason="not_needed")["status"] == "rejected"


def test_an_approve_with_an_old_hash_or_an_old_pin_is_refused(queue):
    proposal = crm_on(queue)
    set_item(proposal["setup"]["item_id"], description="A newer description.")
    fresh = acted(queue.sup, read(queue.owner, proposal), "refresh")
    before = counts()
    # The old hash with the current revision.
    error = refused(
        approve(queue.owner, fresh, payload_hash=proposal["payload_hash"]),
        409,
        "action_proposal_payload_changed",
    )
    assert error["message"]
    # The old revision number.
    error = refused(approve(queue.owner, proposal), 409, "action_proposal_revision_conflict")
    assert error["details"] == {"current_revision": fresh["revision"]}
    # An old pin.
    new_revision(proposal["setup"]["meeting_id"])
    refreshed = acted(queue.sup, read(queue.owner, proposal), "refresh")
    refused(
        approve(
            queue.owner,
            refreshed,
            source_transcript_revision_id=proposal["source_transcript_revision_id"],
        ),
        409,
        "action_proposal_pin_mismatch",
    )
    assert requests_of(proposal["id"]) == []
    assert counts()["requests"] == before["requests"]
    # Allow control: the current hash, pin and revision.
    assert approved(queue.owner, refreshed)["status"] == "approved"


def test_edit_then_approve_stores_the_edit_and_the_request_copies_it(queue):
    proposal = ticket_on(queue)
    edited = {
        **proposal["payload"],
        "title": EDITED_TITLE,
        "priority": "low",
        "due_at": "2026-12-10T08:00:00+00:00",
    }
    view = approved(queue.owner, proposal, payload=edited)
    assert view["payload"] == edited
    assert view["payload_version"] == 2
    assert view["payload_hash"] == canonical_hash(edited)
    (request,) = requests_of(proposal["id"])
    assert request.payload_json == edited
    assert request.payload_hash == canonical_hash(edited)
    assert request.payload_version == 2
    approved_event = [
        e for e in events(proposal["id"]) if e.event_type == "action_proposal.approved"
    ]
    assert approved_event[0].details["edited"] is True


def test_an_edit_of_a_field_that_is_not_editable_is_refused(queue):
    proposal = ticket_on(queue)
    before = counts()
    for key, value in (("meeting_id", "0" * 36), ("kind", "commitment"), ("schema", "x")):
        refused(
            approve(queue.owner, proposal, payload={**proposal["payload"], key: value}),
            422,
            "action_proposal_field_not_editable",
        )
    for payload in (
        {**proposal["payload"], "extra": 1},
        {k: v for k, v in proposal["payload"].items() if k != "title"},
        {**proposal["payload"], "priority": "urgent"},
        {**proposal["payload"], "title": ""},
        {**proposal["payload"], "title": "x" * 121},
        {**proposal["payload"], "description": "x" * 4001},
        {**proposal["payload"], "due_at": "next week"},
    ):
        refused(approve(queue.owner, proposal, payload=payload), 422, "action_proposal_invalid")
    assert requests_of(proposal["id"]) == []
    assert counts()["requests"] == before["requests"]
    assert stored(proposal["id"]).payload_version == 1
    # Allow control: an editable field.
    assert (
        approved(queue.owner, proposal, payload={**proposal["payload"], "title": "Ok"})[
            "payload_version"
        ]
        == 2
    )


def test_a_coaching_edit_runs_the_coaching_checks(queue):
    proposal = coaching_on(queue)
    payload = proposal["payload"]
    refused(
        approve(queue.owner, proposal, payload={**payload, "due_at": due(timedelta(days=200))}),
        422,
        "coaching_due_invalid",
    )
    refused(
        approve(queue.owner, proposal, payload={**payload, "owner_user_id": queue.agent.user_id}),
        422,
        "coaching_owner_is_agent",
    )
    refused(
        approve(queue.owner, proposal, payload={**payload, "agent_user_id": queue.plain.user_id}),
        422,
        "action_proposal_field_not_editable",
    )
    assert requests_of(proposal["id"]) == []
    view = approved(queue.owner, proposal, payload={**payload, "instruction": "Practise it twice."})
    assert view["payload"]["instruction"] == "Practise it twice."
    (request,) = requests_of(proposal["id"])
    assert request.source_type == "review_finding"
    assert request.source_id == proposal["setup"]["review_item_id"]
    assert request.source_criterion_id == "off_topic"
    assert request.target_system is None and request.target_external_record_id is None


def test_an_edited_instruction_is_invalid_and_create_and_refresh_keep_the_coaching_code(queue):
    """P2-3.1: a blank, empty or too long instruction in edit-then-approve answers
    ``422 action_proposal_invalid`` and writes nothing; create and refresh run the
    coaching check with its own code."""
    proposal = coaching_on(queue)
    payload = proposal["payload"]
    before = counts()
    for value in ("", "   ", "x" * 2001):
        refused(
            approve(queue.owner, proposal, payload={**payload, "instruction": value}),
            422,
            "action_proposal_invalid",
        )
    assert requests_of(proposal["id"]) == []
    row = stored(proposal["id"])
    assert (row.status, row.revision, row.payload_version) == ("pending", 1, 1)
    assert row.payload_json["instruction"] == INSTRUCTION
    after = counts()
    assert (after["proposals"], after["requests"]) == (before["proposals"], before["requests"])
    denied = [event for event in events(proposal["id"]) if event.outcome == "denied"]
    assert [event.details["reason_code"] for event in denied] == ["action_proposal_invalid"] * 3
    # Create keeps the coaching code for the same values.
    target = reviewed(queue)
    for value in ("", "   ", "x" * 2001):
        refused(
            create(queue.sup, coaching_body(target.id, queue.plain, instruction=value)),
            422,
            "coaching_instruction_required",
        )
    # Refresh keeps it too (a stored instruction that is no longer valid).
    with inspection_session_factory()() as db:
        db.execute(
            update(ActionProposal)
            .where(ActionProposal.id == proposal["id"])
            .values(payload_json={**payload, "instruction": "   "})
        )
        db.commit()
    refused(act(queue.sup, proposal, "refresh"), 422, "coaching_instruction_required")
    refreshed_denied = [
        event
        for event in events(proposal["id"])
        if event.outcome == "denied" and event.event_type == "action_proposal.refreshed"
    ]
    assert [event.details["reason_code"] for event in refreshed_denied] == [
        "coaching_instruction_required"
    ]
    # Allow control: a valid edited instruction is approved.
    other = coaching_on(queue)
    view = approved(queue.owner, other, payload={**other["payload"], "instruction": "Practise it."})
    assert view["payload"]["instruction"] == "Practise it."


def _denied_codes(proposal_id: str) -> list[tuple[str, str]]:
    return [
        (event.event_type, event.details["reason_code"])
        for event in events(proposal_id)
        if event.outcome == "denied"
    ]


@pytest.mark.parametrize(("agent_role", "control_role"), [("admin", "owner"), ("owner", "admin")])
def test_the_calls_own_agent_never_writes_a_coaching_proposal(queue, agent_role, control_role):
    """P2-4.1: an owner or admin passes the review-queue rule on their own call, so they
    read the coaching proposal about it, but every write answers
    ``422 coaching_actor_is_agent`` before the status and revision checks."""
    agent, control = getattr(queue, agent_role), getattr(queue, control_role)
    own = reviewed(queue, agent=agent)
    proposal = created(queue.sup, coaching_body(own.id, queue.plain))
    payload = proposal["payload"]
    before = counts()
    assert read(agent, proposal)["id"] == proposal["id"]
    attempts = [
        approve(agent, proposal),
        approve(agent, proposal, payload=payload),
        approve(agent, proposal, payload={**payload, "instruction": "Practise it twice."}),
        act(agent, proposal, "reject", reason="incorrect"),
        act(agent, proposal, "refresh"),
        # The agent check runs before the revision and the status checks.
        act(agent, {**proposal, "revision": 99}, "reject", reason="incorrect"),
        act(agent, proposal, "revoke", reason="other"),
    ]
    for response in attempts:
        refused(response, 422, "coaching_actor_is_agent")
    assert requests_of(proposal["id"]) == []
    row = stored(proposal["id"])
    assert (row.status, row.revision, row.payload_version) == ("pending", 1, 1)
    assert row.payload_json == payload
    after = counts()
    assert (after["proposals"], after["requests"]) == (before["proposals"], before["requests"])
    code_of = "coaching_actor_is_agent"
    assert _denied_codes(proposal["id"]) == [
        ("action_proposal.approved", code_of),
        ("action_proposal.approved", code_of),
        ("action_proposal.approved", code_of),
        ("action_proposal.rejected", code_of),
        ("action_proposal.refreshed", code_of),
        ("action_proposal.rejected", code_of),
        ("action_proposal.revoked", code_of),
    ]
    # Allow control: another approver on the same proposal approves and revokes it; the
    # agent still cannot revoke the approved proposal.
    view = approved(control, proposal)
    assert view["status"] == "approved"
    refused(act(agent, view, "revoke", reason="other"), 422, "coaching_actor_is_agent")
    assert acted(control, view, "revoke", reason="other")["status"] == "revoked"
    assert len(requests_of(proposal["id"])) == 1
    # The rule is about coaching: an action item proposal on a call of the same agent is
    # approved by that agent.
    call = conversation(queue, agent=agent)
    item_proposal = created(queue.sup, crm_body(action_item(queue, call), crm_record(queue, call)))
    assert approved(agent, item_proposal)["status"] == "approved"


def test_the_coachee_never_writes_a_coaching_proposal_after_the_call_moves(queue):
    """P2-6.3: the person a coaching proposal coaches (the stored payload's
    ``agent_user_id``) is refused on every write with ``422 coaching_actor_is_agent``,
    also after the call moves to another agent, in the place of P2-4.1 (before the status
    and the revision checks).  Another approver still acts on the same proposals."""
    calls = [reviewed(queue, agent=queue.admin) for _ in range(2)]
    first = created(queue.sup, coaching_body(calls[0].id, queue.plain))
    second = created(queue.sup, coaching_body(calls[1].id, queue.plain))
    assert first["payload"]["agent_user_id"] == queue.admin.user_id
    assert second["payload"]["agent_user_id"] == queue.admin.user_id
    done = approved(queue.owner, first)
    for call in calls:
        moved = queue.owner.patch(
            f"/api/v1/meetings/{call.scored.meeting_id}",
            json={"agent_user_id": queue.agent.user_id},
        )
        assert moved.status_code == 200, moved.text
    # The admin is no longer the call's agent, only the coachee of both proposals.
    pending = read(queue.admin, second)
    assert pending["stale"]["is_stale"] is True
    done = read(queue.admin, done)
    before = counts()
    attempts = [
        approve(queue.admin, pending),
        approve(queue.admin, pending, payload=pending["payload"]),
        act(queue.admin, pending, "reject", reason="incorrect"),
        act(queue.admin, pending, "refresh"),
        act(queue.admin, {**pending, "revision": 99}, "reject", reason="incorrect"),
        act(queue.admin, done, "revoke", reason="other"),
        act(queue.admin, {**done, "revision": 99}, "revoke", reason="other"),
    ]
    for response in attempts:
        refused(response, 422, "coaching_actor_is_agent")
    assert requests_of(second["id"]) == []
    assert len(requests_of(first["id"])) == 1
    assert (stored(second["id"]).status, stored(second["id"]).revision) == (
        "pending",
        pending["revision"],
    )
    assert (stored(first["id"]).status, stored(first["id"]).revision) == (
        "approved",
        done["revision"],
    )
    after = counts()
    assert (after["proposals"], after["requests"]) == (before["proposals"], before["requests"])
    code_of = "coaching_actor_is_agent"
    assert _denied_codes(second["id"]) == [
        ("action_proposal.approved", code_of),
        ("action_proposal.approved", code_of),
        ("action_proposal.rejected", code_of),
        ("action_proposal.refreshed", code_of),
        ("action_proposal.rejected", code_of),
    ]
    assert _denied_codes(first["id"]) == [
        ("action_proposal.revoked", code_of),
        ("action_proposal.revoked", code_of),
    ]
    # Allow control: the owner, who is neither the call's agent nor the coachee, rejects
    # the pending proposal and revokes the approved one.
    assert acted(queue.owner, pending, "reject", reason="incorrect")["status"] == "rejected"
    assert acted(queue.owner, done, "revoke", reason="other")["status"] == "revoked"


def test_approve_runs_the_coaching_checks_with_and_without_the_payload(queue):
    """P2-4.2: approve of a coaching proposal re-runs the coaching create checks on the
    payload it approves, with the coaching codes, so the answer is the same with no
    payload and with the unchanged payload."""

    def both_refused(proposal: dict, status: int, error: str) -> None:
        current = stored(proposal["id"]).payload_json
        refused(approve(queue.owner, proposal), status, error)
        refused(approve(queue.owner, proposal, payload=current), status, error)
        assert requests_of(proposal["id"]) == []
        row = stored(proposal["id"])
        assert (row.status, row.revision, row.payload_version) == ("pending", 1, 1)
        assert _denied_codes(proposal["id"]) == [("action_proposal.approved", error)] * 2

    def store(proposal: dict, **values: Any) -> None:
        with inspection_session_factory()() as db:
            db.execute(
                update(ActionProposal)
                .where(ActionProposal.id == proposal["id"])
                .values(payload_json={**proposal["payload"], **values})
            )
            db.commit()

    # An open appeal of the review.
    appealed = coaching_on(queue)
    filed(queue.agent, appealed["setup"]["review_item_id"])
    both_refused(appealed, 409, "review_appeal_open")
    # An active coaching task for the finding.
    tasked = coaching_on(queue)
    task = queue.sup.post(
        f"/api/v1/review-items/{tasked['setup']['review_item_id']}/coaching-tasks",
        json={
            key: tasked["payload"][key]
            for key in ("criterion_id", "owner_user_id", "due_at", "instruction")
        },
    )
    assert task.status_code == 201, task.text
    both_refused(tasked, 409, "coaching_task_exists")
    # A due date that has passed since the proposal was built.
    late = coaching_on(queue)
    store(late, due_at=due(-timedelta(hours=1)))
    both_refused(late, 422, "coaching_due_invalid")
    # A stored instruction that is no longer valid keeps the coaching code (it is not an
    # edit, so P2-3.1 does not apply).
    blank = coaching_on(queue)
    store(blank, instruction="   ")
    both_refused(blank, 422, "coaching_instruction_required")
    # Allow control: the same approve with no payload and with the unchanged payload.
    plain = approved(queue.owner, coaching_on(queue))
    assert plain["status"] == "approved"
    unchanged = coaching_on(queue)
    view = approved(queue.owner, unchanged, payload=unchanged["payload"])
    assert (view["status"], view["payload_version"]) == ("approved", 1)
    (event,) = [
        event
        for event in events(unchanged["id"])
        if event.event_type == "action_proposal.approved" and event.outcome == "success"
    ]
    assert event.details["edited"] is False


# --- 5. zero external writes and the status rules -------------------------------------------


def test_only_an_approved_proposal_has_its_one_request(queue):
    pending = crm_on(queue)
    rejected = acted(queue.owner, crm_on(queue), "reject", reason="incorrect")
    revoked_source = crm_on(queue)
    approved_view = approved(queue.owner, revoked_source)
    revoked = acted(queue.owner, approved_view, "revoke", reason="approved_in_error")
    stale = crm_on(queue)
    set_item(stale["setup"]["item_id"], description="Changed.")
    refused(approve(queue.owner, stale), 409, "action_proposal_stale")
    for proposal in (pending, rejected, stale):
        assert requests_of(proposal["id"]) == []
    # The revoked one keeps its request untouched; HOM-136 reads the proposal's status.
    (kept,) = requests_of(revoked["id"])
    assert revoked["status"] == "revoked"
    assert revoked["delivery_request"]["id"] == kept.id
    winner = crm_on(queue)
    view = approved(queue.admin, winner)
    (request,) = requests_of(winner["id"])
    expected_key = hashlib.sha256(
        f"{queue.workspace_id}|{winner['id']}|{winner['payload_hash']}".encode()
    ).hexdigest()
    assert request.idempotency_key == expected_key
    assert view["delivery_request"] == {
        "id": request.id,
        "idempotency_key": expected_key,
        "approved_by_user_id": queue.admin.user_id,
        "approved_at": view["decided_at"],
    }
    assert request.payload_json == winner["payload"]
    assert request.payload_hash == winner["payload_hash"]
    assert request.source_type == "action_item" and request.source_id == winner["action_item_id"]
    assert request.source_transcript_revision_id == winner["source_transcript_revision_id"]
    assert request.source_digest == stored(winner["id"]).source_digest
    assert request.target_system == "zendesk"
    assert request.target_external_record_id == winner["target_external_record_id"]
    assert view["status"] == "approved" and view["decided_by_user_id"] == queue.admin.user_id
    assert view["revision"] == 2
    assert view["stale"] is None  # only a pending proposal is checked live
    # Self-approval is allowed (ADR 0082 D4): the owner proposes and approves.
    own = crm_on(queue, queue.owner)
    assert approved(queue.owner, own)["decided_by_user_id"] == queue.owner.user_id


def test_every_transition_outside_the_design_answers_409(queue):
    rejected = acted(queue.owner, crm_on(queue), "reject", reason="other")
    refused(approve(queue.owner, rejected), 409, "action_proposal_not_pending")
    refused(
        act(queue.owner, rejected, "reject", reason="other"), 409, "action_proposal_not_pending"
    )
    refused(
        act(queue.owner, rejected, "revoke", reason="other"), 409, "action_proposal_not_approved"
    )
    refused(act(queue.sup, rejected, "refresh"), 409, "action_proposal_not_pending")
    approved_view = approved(queue.owner, crm_on(queue))
    refused(approve(queue.owner, approved_view), 409, "action_proposal_not_pending")
    refused(
        act(queue.owner, approved_view, "reject", reason="other"),
        409,
        "action_proposal_not_pending",
    )
    refused(act(queue.sup, approved_view, "refresh"), 409, "action_proposal_not_pending")
    revoked = acted(queue.owner, approved_view, "revoke", reason="other")
    refused(
        act(queue.owner, revoked, "revoke", reason="other"), 409, "action_proposal_not_approved"
    )
    refused(approve(queue.owner, revoked), 409, "action_proposal_not_pending")
    pending = crm_on(queue)
    refused(
        act(queue.owner, pending, "revoke", reason="other"), 409, "action_proposal_not_approved"
    )
    # The revision check comes after the status check.
    error = refused(
        act(queue.owner, {**pending, "revision": 7}, "reject", reason="other"),
        409,
        "action_proposal_revision_conflict",
    )
    assert error["details"] == {"current_revision": 1}


def test_reasons_must_come_from_their_lists(queue):
    pending = crm_on(queue)
    for reason in ("spam", "", "Other"):
        refused(act(queue.owner, pending, "reject", reason=reason), 422, "action_proposal_invalid")
    approved_view = approved(queue.owner, crm_on(queue))
    for reason in ("x", "not_needed"):
        refused(
            act(queue.owner, approved_view, "revoke", reason=reason), 422, "action_proposal_invalid"
        )
    for reason in ("not_needed", "incorrect", "duplicate", "other"):
        assert acted(queue.owner, crm_on(queue), "reject", reason=reason)["reject_reason"] == reason
    for reason in ("approved_in_error", "no_longer_needed", "other"):
        view = approved(queue.owner, crm_on(queue))
        assert acted(queue.owner, view, "revoke", reason=reason)["revoke_reason"] == reason


def test_a_missing_proposal_answers_404_and_writes_nothing(queue):
    before = counts()
    fake = {
        "id": "0" * 36,
        "revision": 1,
        "payload_hash": "a",
        "source_transcript_revision_id": "b",
    }
    for method, path, body in routes(fake)[2:]:
        refused(send(queue.owner, method, path, body), 404, "action_proposal_not_found")
    assert counts() == before


# --- 6. the list ----------------------------------------------------------------------------


def test_the_list_filters_pages_and_leaves_out_the_payload(queue):
    first = crm_on(queue)
    second = ticket_on(queue)
    third = coaching_on(queue)
    acted(queue.owner, second, "reject", reason="other")
    page = queue.owner.get(PROPOSALS, params={"limit": 2})
    assert page.status_code == 200, page.text
    body = page.json()
    assert [row["id"] for row in body["items"]] == [first["id"], second["id"]]
    assert all("payload" not in row and "stale" not in row for row in body["items"])
    assert body["next_cursor"]
    rest = queue.owner.get(PROPOSALS, params={"limit": 2, "cursor": body["next_cursor"]}).json()
    assert [row["id"] for row in rest["items"]] == [third["id"]]
    assert rest["next_cursor"] is None
    pending = queue.owner.get(PROPOSALS, params={"status": "pending"}).json()["items"]
    assert [row["id"] for row in pending] == [first["id"], third["id"]]
    both = queue.owner.get(PROPOSALS, params=[("status", "pending"), ("status", "rejected")])
    assert len(both.json()["items"]) == 3
    by_type = queue.owner.get(PROPOSALS, params={"action_type": "coaching_task"}).json()["items"]
    assert [row["id"] for row in by_type] == [third["id"]]
    by_meeting = queue.owner.get(
        PROPOSALS, params={"meeting_id": first["setup"]["meeting_id"]}
    ).json()["items"]
    assert [row["id"] for row in by_meeting] == [first["id"]]
    for params in (
        {"status": "done"},
        {"action_type": "email"},
        {"limit": 0},
        {"limit": 101},
        {"limit": "x"},
        {"cursor": "not-a-cursor"},
    ):
        refused(queue.owner.get(PROPOSALS, params=params), 422, "action_proposal_invalid")


def _cursor(**values: Any) -> str:
    raw = json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def test_the_list_numbers_take_only_ascii_digits_in_range(queue):
    """P2-4.3: ``limit`` takes ASCII digits from 1 to 100 only, and the cursor version is
    the JSON number 1 only; anything else is ``422 action_proposal_invalid``, never a 500."""
    first = crm_on(queue)
    second = crm_on(queue)
    for limit in (
        "²",  # superscript two
        "٢",  # Arabic-Indic two
        "۵",  # Extended Arabic-Indic (Persian) five
        "２",  # fullwidth two
        "1" * 5000,
        "",
        " 2",
        "2 ",
        "+2",
        "-1",
        "2.0",
        "1e1",
        "0",
        "000",
        "101",
    ):
        refused(queue.owner.get(PROPOSALS, params={"limit": limit}), 422, "action_proposal_invalid")
    moment = stored(first["id"]).created_at
    created_at = (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).isoformat()
    for version in (True, 1.0, "1"):
        bad = _cursor(v=version, created_at=created_at, id=first["id"])
        refused(queue.owner.get(PROPOSALS, params={"cursor": bad}), 422, "action_proposal_invalid")
    # A date at the edge of the calendar does not fit in UTC.
    for edge in ("0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00"):
        bad = _cursor(v=1, created_at=edge, id=first["id"])
        refused(queue.owner.get(PROPOSALS, params={"cursor": bad}), 422, "action_proposal_invalid")
    # Allow control: ASCII digits in range, and the cursor of version 1.
    for limit, size in (("1", 1), ("002", 2), ("100", 2)):
        page = queue.owner.get(PROPOSALS, params={"limit": limit})
        assert page.status_code == 200, page.text
        assert len(page.json()["items"]) == size
    good = _cursor(v=1, created_at=created_at, id=first["id"])
    rest = queue.owner.get(PROPOSALS, params={"cursor": good})
    assert rest.status_code == 200, rest.text
    assert [row["id"] for row in rest.json()["items"]] == [second["id"]]


def _cursor_of_length(created_at: str, row_id: str, size: int) -> str:
    """A valid cursor of exactly ``size`` characters: JSON allows trailing spaces, and
    ``size`` is a multiple of 4, so the base64url text has no padding."""
    raw = json.dumps({"v": 1, "created_at": created_at, "id": row_id}, separators=(",", ":"))
    data = raw.encode("utf-8")
    data += b" " * (size * 3 // 4 - len(data))
    text = base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")
    assert len(text) == size
    return text


def test_a_cursor_over_512_characters_is_refused_and_never_gives_a_500(queue, monkeypatch):
    """P2-6.1: a cursor longer than 512 characters answers ``422 action_proposal_invalid``
    before any decoding, so deep JSON nesting never reaches the decoder; a
    ``RecursionError`` in the decoder is the same 422, never a 500."""
    first = crm_on(queue)
    second = crm_on(queue)
    every = queue.owner.get(PROPOSALS)
    assert every.status_code == 200, every.text
    order = [row["id"] for row in every.json()["items"]]
    assert sorted(order) == sorted([first["id"], second["id"]])
    page = queue.owner.get(PROPOSALS, params={"limit": "1"})
    assert page.status_code == 200, page.text
    real = page.json()["next_cursor"]
    assert real is not None and len(real) <= 512
    moment = stored(order[0]).created_at
    created_at = (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).isoformat()
    nested = base64.urlsafe_b64encode(b"[" * 10000).rstrip(b"=").decode("ascii")
    assert len(nested) == 13334
    for bad in (
        nested,
        "A" * 513,
        # A cursor that decodes to a valid cursor, but is longer than the bound.
        _cursor_of_length(created_at, order[0], 516),
    ):
        refused(queue.owner.get(PROPOSALS, params={"cursor": bad}), 422, "action_proposal_invalid")
    # Allow control: the real cursor of the list, and a valid cursor of exactly 512
    # characters, answer 200 with the next row.
    for good in (real, _cursor_of_length(created_at, order[0], 512)):
        rest = queue.owner.get(PROPOSALS, params={"cursor": good})
        assert rest.status_code == 200, rest.text
        assert [row["id"] for row in rest.json()["items"]] == order[1:]

    # Belt and braces: a RecursionError from the JSON decoder is a 422 too.
    def too_deep(_text: str) -> Any:
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(
        service, "json", SimpleNamespace(loads=too_deep, JSONDecodeError=json.JSONDecodeError)
    )
    with pytest.raises(service.ActionProposalError) as refusal:
        service._decode_cursor(real)
    assert (refusal.value.status_code, refusal.value.code) == (422, "action_proposal_invalid")


def _post_ascii(persona, path: str, body: dict):
    """POST ``body`` as ASCII JSON.  httpx writes JSON as UTF-8, which cannot hold a lone
    surrogate; ``json.dumps`` writes it as the escape ``\\ud800``, as a client may send."""
    return persona.client.post(
        path,
        cookies=persona.cookies,
        headers={**persona.headers, "Content-Type": "application/json"},
        content=json.dumps(body).encode("ascii"),
    )


def _approve_ascii(persona, proposal: dict, payload: dict):
    body = {
        "expected_revision": proposal["revision"],
        "payload_hash": proposal["payload_hash"],
        "source_transcript_revision_id": proposal["source_transcript_revision_id"],
        "payload": payload,
    }
    return _post_ascii(persona, f"{PROPOSALS}/{proposal['id']}/approve", body)


def test_an_invalid_edited_value_answers_422_and_never_a_500(queue):
    """P2-7.1: a due date that does not fit in UTC and text that cannot be encoded as UTF-8
    (a lone surrogate) are invalid edited values: ``422 action_proposal_invalid`` with one
    denied approve event, never a 500."""
    ticket = ticket_on(queue)
    crm = crm_on(queue)
    for change in (
        {"due_at": "0001-01-01T00:00:00+01:00"},
        {"due_at": "9999-12-31T23:59:59-01:00"},
        {"title": "\ud800 bad"},
    ):
        response = _approve_ascii(queue.owner, ticket, {**ticket["payload"], **change})
        refused(response, 422, "action_proposal_invalid")
    response = _approve_ascii(queue.owner, crm, {**crm["payload"], "note": "note \udfff"})
    refused(response, 422, "action_proposal_invalid")
    invalid = ("action_proposal.approved", "action_proposal_invalid")
    assert _denied_codes(ticket["id"]) == [invalid, invalid, invalid]
    assert _denied_codes(crm["id"]) == [invalid]
    for proposal in (ticket, crm):
        assert requests_of(proposal["id"]) == []
        row = stored(proposal["id"])
        assert (row.status, row.revision, row.payload_version) == ("pending", 1, 1)
    # Allow control: valid edits of the same fields are approved.
    view = approved(
        queue.owner, ticket, payload={**ticket["payload"], "due_at": due(), "title": "ok"}
    )
    assert (view["payload_version"], view["payload"]["title"]) == (2, "ok")
    view = approved(queue.owner, crm, payload={**crm["payload"], "note": "A clean note."})
    assert (view["payload_version"], view["payload"]["note"]) == (2, "A clean note.")


def test_an_unchanged_payload_on_approve_is_not_an_edit(queue):
    """P2-7.3: an editable value equal to the stored one keeps the stored value and skips
    the field checks, so approving the exact stored payload is not an edit: version 1,
    ``edited`` false, the stored hash and its idempotency key.  A changed value is still
    checked and normalized."""
    text = "  Call the customer back  "
    for proposal in (ticket_on(queue, description=text), crm_on(queue, description=text)):
        field = "description" if proposal["action_type"] == "ticket_create" else "note"
        assert proposal["payload"][field] == text
        view = approved(queue.owner, proposal, payload=proposal["payload"])
        assert view["payload_version"] == 1
        assert view["payload"] == proposal["payload"]
        assert view["payload_hash"] == proposal["payload_hash"]
        (request,) = requests_of(proposal["id"])
        expected_key = hashlib.sha256(
            f"{queue.workspace_id}|{proposal['id']}|{proposal['payload_hash']}".encode()
        ).hexdigest()
        assert request.idempotency_key == expected_key
        assert request.payload_json == proposal["payload"]
        assert [
            event.details["edited"]
            for event in events(proposal["id"])
            if event.event_type == "action_proposal.approved" and event.outcome == "success"
        ] == [False]
    # Allow control: a changed value is checked and normalized as before.
    changed = ticket_on(queue, description=text)
    view = approved(queue.owner, changed, payload={**changed["payload"], "description": "  New  "})
    assert (view["payload_version"], view["payload"]["description"]) == (2, "New")


def test_an_id_that_the_database_cannot_hold_is_refused_before_any_query(queue):
    """P2-7.2 and P2-7.5: an id with a NUL byte (PostgreSQL refuses it in text) or a lone
    surrogate (psycopg cannot send it) never reaches a query: a list or body id answers
    ``422 action_proposal_invalid``, a path id ``404 action_proposal_not_found``.  The 500
    shows on PostgreSQL; run this file there too."""
    proposal = crm_on(queue)
    refused(
        queue.owner.get(PROPOSALS, params={"meeting_id": "\x00"}), 422, "action_proposal_invalid"
    )
    before = counts()
    for body in (
        ticket_body("\x00"),
        ticket_body("\ud800"),
        {"action_type": "coaching_task", "review_item_id": "\x00", "criterion_id": "x"},
        {"action_type": "coaching_task", "review_item_id": "x", "criterion_id": "\x00"},
        crm_body(proposal["setup"]["item_id"], "\x00"),
    ):
        refused(_post_ascii(queue.owner, PROPOSALS, body), 422, "action_proposal_invalid")
    refused(queue.owner.get(f"{PROPOSALS}/%00"), 404, "action_proposal_not_found")
    refused(
        queue.owner.post(
            f"{PROPOSALS}/%00/reject", json={"expected_revision": 1, "reason": "incorrect"}
        ),
        404,
        "action_proposal_not_found",
    )
    after = counts()
    assert after == before
    # Allow control: an id the database can hold is read as before.
    page = queue.owner.get(PROPOSALS, params={"meeting_id": "x"})
    assert page.status_code == 200, page.text
    assert page.json()["items"] == []
    page = queue.owner.get(PROPOSALS, params={"meeting_id": proposal["meeting_id"]})
    assert [row["id"] for row in page.json()["items"]] == [proposal["id"]]
    assert read(queue.owner, proposal)["id"] == proposal["id"]


def test_a_coaching_owner_the_database_cannot_hold_is_an_ineligible_owner(queue):
    """P2-8.1: a coaching ``owner_user_id`` with a NUL byte (PostgreSQL refuses it in text)
    or a lone surrogate (psycopg cannot send it) is no owner: create and approve answer
    ``422 review_assignee_ineligible`` at the owner step of the coaching checks, as an
    unknown owner does, never a 500.  The NUL case shows on PostgreSQL."""
    target = reviewed(queue)
    before = counts()
    for owner in ("\x00", "\ud800", "no-such-user"):
        body = coaching_body(target.id, queue.plain, owner_user_id=owner)
        refused(_post_ascii(queue.owner, PROPOSALS, body), 422, "review_assignee_ineligible")
    assert counts() == before
    proposal = created(queue.sup, coaching_body(target.id, queue.plain))
    for owner in ("\x00", "\ud800", "no-such-user"):
        response = _approve_ascii(
            queue.owner, proposal, {**proposal["payload"], "owner_user_id": owner}
        )
        refused(response, 422, "review_assignee_ineligible")
    ineligible = ("action_proposal.approved", "review_assignee_ineligible")
    assert _denied_codes(proposal["id"]) == [ineligible, ineligible, ineligible]
    assert requests_of(proposal["id"]) == []
    row = stored(proposal["id"])
    assert (row.status, row.revision, row.payload_version) == ("pending", 1, 1)
    # Allow control: the stored, eligible owner is approved.
    view = approved(queue.owner, proposal, payload=proposal["payload"])
    assert (view["status"], view["payload_version"]) == ("approved", 1)


def test_a_payload_key_is_refused_without_echoing_it(queue):
    """P2-8.2: an unknown or missing payload key answers ``422 action_proposal_invalid``
    with a fixed message that does not echo the client's key, so a key with a lone
    surrogate is a 422 with one denied event, never a 500 while the error is written."""
    proposal = ticket_on(queue)
    payload = proposal["payload"]
    missing = {key: value for key, value in payload.items() if key != "title"}
    for sent in ({**payload, "\ud800": 1}, {**payload, "extra": 1}, missing):
        error = refused(_approve_ascii(queue.owner, proposal, sent), 422, "action_proposal_invalid")
        assert "\ud800" not in error["message"] and "extra" not in error["message"]
    invalid = ("action_proposal.approved", "action_proposal_invalid")
    assert _denied_codes(proposal["id"]) == [invalid, invalid, invalid]
    assert requests_of(proposal["id"]) == []
    # Allow control: the payload with exactly the stored keys is approved.
    assert approved(queue.owner, proposal, payload=payload)["status"] == "approved"


def test_a_coaching_proposal_follows_the_review_queue_visibility(queue):
    proposal = coaching_on(queue)
    # ``sup_b`` holds action:propose and sees every call (record scope workspace), but not
    # the review item of team A.
    refused(queue.sup_b.get(f"{PROPOSALS}/{proposal['id']}"), 404, "action_proposal_not_found")
    assert proposal["id"] not in [row["id"] for row in queue.sup_b.get(PROPOSALS).json()["items"]]
    # Allow control: an action item proposal on the same team is visible to ``sup_b``.
    item_proposal = crm_on(queue)
    assert read(queue.sup_b, item_proposal)["id"] == item_proposal["id"]
    assert read(queue.plain, proposal)["id"] == proposal["id"]


# --- 7. audit -------------------------------------------------------------------------------


def test_each_change_writes_one_event_with_ids_codes_hashes_and_versions_only(queue):
    proposal = crm_on(queue)
    set_item(proposal["setup"]["item_id"], description=DESCRIPTION + " Updated.")
    fresh = acted(queue.sup, read(queue.owner, proposal), "refresh")
    view = approved(queue.owner, fresh, payload={**fresh["payload"], "note": "Edited note text."})
    revoked = acted(queue.owner, view, "revoke", reason="no_longer_needed")
    rejected = acted(queue.owner, ticket_on(queue), "reject", reason="duplicate")
    found = events(proposal["id"])
    assert [event.event_type for event in found] == [
        "action_proposal.created",
        "action_proposal.refreshed",
        "action_proposal.approved",
        "action_proposal.revoked",
    ]
    for event in found + events(rejected["id"]):
        assert event.outcome == "success"
        assert set(event.details) <= AUDIT_KEYS, event.details
        assert event.workspace_id == queue.workspace_id
        assert event.target_type == "action_proposal"
    created_event, refreshed_event, approved_event, revoked_event = found
    assert created_event.actor_user_id == queue.sup.user_id
    assert created_event.details["payload_hash"] == proposal["payload_hash"]
    assert created_event.details["payload_version"] == 1
    assert refreshed_event.details["payload_version"] == 2
    assert approved_event.details["delivery_request_id"] == view["delivery_request"]["id"]
    assert approved_event.details["edited"] is True
    assert revoked_event.details["reason"] == "no_longer_needed"
    assert revoked["revision"] == revoked_event.details["revision"]
    assert events(rejected["id"])[-1].details["reason"] == "duplicate"
    stored_text = json.dumps([event.details for event in found + events(rejected["id"])])
    external_id = proposal["setup"]["external_id"]
    for text in (DESCRIPTION, "Edited note text.", external_id, "Sara from sales"):
        assert text not in stored_text


def test_a_refusal_after_visibility_writes_one_denied_event_with_its_code(queue):
    proposal = crm_on(queue)
    refused(
        act(queue.owner, proposal, "revoke", reason="other"), 409, "action_proposal_not_approved"
    )
    refused(act(queue.owner, proposal, "reject", reason="spam"), 422, "action_proposal_invalid")
    set_item(proposal["setup"]["item_id"], description="Changed again.")
    refused(approve(queue.owner, proposal), 409, "action_proposal_stale")
    denied = [event for event in events(proposal["id"]) if event.outcome == "denied"]
    assert [(event.event_type, event.details["reason_code"]) for event in denied] == [
        ("action_proposal.revoked", "action_proposal_not_approved"),
        ("action_proposal.rejected", "action_proposal_invalid"),
        ("action_proposal.approved", "action_proposal_stale"),
    ]
    assert denied[-1].details["stale_reason"] == "source_changed"
    for event in denied:
        assert set(event.details) <= AUDIT_KEYS
        assert event.actor_user_id == queue.owner.user_id
    # An edited instruction is an edited value (P2-3.1); a due date keeps the coaching code.
    coaching = coaching_on(queue)
    refused(
        approve(queue.owner, coaching, payload={**coaching["payload"], "instruction": " "}),
        422,
        "action_proposal_invalid",
    )
    refused(
        approve(
            queue.owner,
            coaching,
            payload={**coaching["payload"], "due_at": due(-timedelta(days=1))},
        ),
        422,
        "coaching_due_invalid",
    )
    coaching_denied = [e for e in events(coaching["id"]) if e.outcome == "denied"]
    assert [event.details["reason_code"] for event in coaching_denied] == [
        "action_proposal_invalid",
        "coaching_due_invalid",
    ]
    for event in coaching_denied:
        assert event.event_type == "action_proposal.approved"
        assert INSTRUCTION not in json.dumps(event.details)
    # A 404 discloses nothing and writes nothing; a create refusal writes nothing either.
    before = counts()
    refused(approve(queue.sup_b, coaching), 403, "permission_denied")
    refused(
        queue.sup_b.post(f"{PROPOSALS}/{coaching['id']}/refresh", json={"expected_revision": 1}),
        404,
        "action_proposal_not_found",
    )
    refused(
        create(queue.sup, crm_body("0" * 36, None, target_external_record_id="x")),
        404,
        "action_item_not_found",
    )
    assert counts() == before
