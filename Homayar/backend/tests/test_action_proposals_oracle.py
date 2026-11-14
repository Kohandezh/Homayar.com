from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, update

from backend.database.saas_models import (
    ActionDeliveryRequest,
    ActionItem,
    ActionProposal,
    ActionStatus,
    AnalysisRun,
    AuditEvent,
    ConversationExternalLink,
    EvidenceSpan,
    ExternalRecord,
    IntegrationMappingProfile,
    ReviewItem,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    Workspace,
)
from backend.security.authorization import API_KEY_SCOPES
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _doc_block
from backend.tests.test_capabilities_oracle import (
    MODULE_KEYS,
    _apply_state,
    _license,
    _restore_install,
    _seed_admin,
)
from backend.tests.test_review_appeals_oracle import (
    REVIEW,
    _assign,
    _body,
    _change,
    _done,
    _filed,
    _outsider,
    _review,
    _uphold,
)
from backend.tests.test_review_decisions_oracle import _Case, _transcript
from backend.tests.test_review_queue_oracle import (
    _due,
    _permissions,
    _race,
    _refused,
    _requires_postgres,
    _set_meeting,
    _utc,
    _World,
)

ROOT = "/api/v1/action-proposals"
COACHING_ITEMS = "/api/v1/review-items"
EVENT_PREFIX = "action_proposal."
DESCRIPTION = "Send the signed renewal contract to Parisa before Friday"
INSTRUCTION = "Greet the customer by name before the offer."
ASSIGNEE_TEXT = "Parisa Ahmadi"
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
REQUEST_VIEW_KEYS = {"id", "idempotency_key", "approved_by_user_id", "approved_at"}
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
WRITE_EVENTS = {
    "refresh": "action_proposal.refreshed",
    "approve": "action_proposal.approved",
    "reject": "action_proposal.rejected",
    "revoke": "action_proposal.revoked",
}
REJECT_REASONS = ("not_needed", "incorrect", "duplicate", "other")
REVOKE_REASONS = ("approved_in_error", "no_longer_needed", "other")
DESIGNED_ROUTES = {
    ("POST", ROOT): "action:propose +csrf +session",
    ("GET", ROOT): "action:propose +session",
    ("GET", f"{ROOT}/{{}}"): "action:propose +session",
    ("POST", f"{ROOT}/{{}}/refresh"): "action:propose +csrf +session",
    ("POST", f"{ROOT}/{{}}/approve"): "action:approve +csrf +session",
    ("POST", f"{ROOT}/{{}}/reject"): "action:approve +csrf +session",
    ("POST", f"{ROOT}/{{}}/revoke"): "action:approve +csrf +session",
}
PEOPLE = ("admin", "sup", "sup2", "agent", "viewer")


def _code(response) -> str:
    return response.json()["error"]["code"]


def _details(response) -> dict:
    return response.json()["error"].get("details") or {}


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha(payload: dict) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _key_of(workspace_id: str, proposal_id: str, payload_hash: str) -> str:
    return hashlib.sha256(f"{workspace_id}|{proposal_id}|{payload_hash}".encode()).hexdigest()


def _due_time(**delta: float) -> datetime:
    return (datetime.now(UTC) + timedelta(**delta)).replace(microsecond=0)


def _world(client, email_outbox, monkeypatch, *extra: str, record_scope: str = "") -> _World:
    names = tuple(dict.fromkeys((*PEOPLE, *extra)))
    return _World(client, email_outbox, monkeypatch, *names, record_scope=record_scope)


def _organization(workspace_id: str) -> str:
    with inspection_session_factory()() as db:
        return db.get(Workspace, workspace_id).organization_id


def _item(workspace_id: str, meeting_id: str, **values: object) -> str:
    fields = {
        "workspace_id": workspace_id,
        "meeting_id": meeting_id,
        "description": DESCRIPTION,
        "assignee_text": ASSIGNEE_TEXT,
        "priority": "high",
        "status": ActionStatus.OPEN,
        "kind": "commitment",
        "due_at": _due_time(days=5),
        **values,
    }
    with inspection_session_factory()() as db:
        row = ActionItem(**fields)
        db.add(row)
        db.commit()
        return row.id


def _set_item(item_id: str, **values: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ActionItem).where(ActionItem.id == item_id).values(**values))
        db.commit()


def _remove_review_item(item_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(delete(ReviewItem).where(ReviewItem.id == item_id))
        db.commit()


def _remove_item(item_id: str) -> None:
    with inspection_session_factory()() as db:
        db.execute(delete(ActionItem).where(ActionItem.id == item_id))
        db.commit()


def _run(workspace_id: str, meeting_id: str, revision_id: str) -> str:
    with inspection_session_factory()() as db:
        row = AnalysisRun(
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            transcript_revision_id=revision_id,
            state=StageState.COMPLETED,
        )
        db.add(row)
        db.commit()
        return row.id


def _new_revision(workspace_id: str, meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        number = db.scalar(
            select(func.max(TranscriptRevision.revision_number)).where(
                TranscriptRevision.meeting_id == meeting_id
            )
        )
        db.execute(
            update(TranscriptRevision)
            .where(TranscriptRevision.meeting_id == meeting_id)
            .values(is_current=False, superseded_at=datetime.now(UTC))
        )
        db.flush()
        row = TranscriptRevision(
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            revision_number=(number or 0) + 1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="en",
            segment_count=2,
            is_current=True,
        )
        db.add(row)
        db.commit()
        return row.id


def _record(
    workspace_id: str,
    meeting_id: str | None,
    *,
    system: str = "hubspot",
    link: str | None = "customer",
    active: bool = True,
    record_type: str = "customer",
) -> tuple[str, str]:
    external_id = f"C-{uuid.uuid4().hex[:10]}"
    organization_id = _organization(workspace_id)
    with inspection_session_factory()() as db:
        record = ExternalRecord(
            organization_id=organization_id,
            workspace_id=workspace_id,
            source_system=system,
            record_type=record_type,
            external_id=external_id,
            is_active=active,
        )
        db.add(record)
        db.flush()
        if link is not None:
            db.add(
                ConversationExternalLink(
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    meeting_id=meeting_id,
                    source_system=system,
                    link_type=link,
                    external_record_id=record.id,
                    profile_version=1,
                )
            )
        db.commit()
        return record.id, external_id


def _set_record(record_id: str, **values: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ExternalRecord).where(ExternalRecord.id == record_id).values(**values))
        db.commit()


def _profile(workspace_id: str, system: str = "zendesk", *, active: bool = True) -> str:
    with inspection_session_factory()() as db:
        found = db.scalar(
            select(IntegrationMappingProfile.id).where(
                IntegrationMappingProfile.workspace_id == workspace_id,
                IntegrationMappingProfile.source_system == system,
            )
        )
        if found is not None:
            return found
        row = IntegrationMappingProfile(
            organization_id=_organization(workspace_id),
            workspace_id=workspace_id,
            source_system=system,
            conversation_source_system=system,
            field_map={},
            is_active=active,
        )
        db.add(row)
        db.commit()
        return row.id


def _set_profile(workspace_id: str, system: str = "zendesk", **values: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(
            update(IntegrationMappingProfile)
            .where(
                IntegrationMappingProfile.workspace_id == workspace_id,
                IntegrationMappingProfile.source_system == system,
            )
            .values(**values)
        )
        db.commit()


def _remove_profile(workspace_id: str, system: str = "zendesk") -> None:
    with inspection_session_factory()() as db:
        db.execute(
            delete(IntegrationMappingProfile).where(
                IntegrationMappingProfile.workspace_id == workspace_id,
                IntegrationMappingProfile.source_system == system,
            )
        )
        db.commit()


class _Call:
    def __init__(self, world: _World, *, team: str | None = "a", transcript: bool = True):
        self.world = world
        self.workspace_id = world.workspace_id
        self.meeting_id = world.new_meeting(team=team)
        self.revision_id = _transcript(world, self.meeting_id) if transcript else None
        self.item_id = _item(self.workspace_id, self.meeting_id)
        self.record_id, self.external_id = _record(self.workspace_id, self.meeting_id)
        _profile(self.workspace_id)

    def item(self, **values: object) -> str:
        return _item(self.workspace_id, self.meeting_id, **values)


def _ticket_body(item_id: str, **extra: object) -> dict:
    return {
        "action_type": "ticket_create",
        "action_item_id": item_id,
        "target_system": "zendesk",
        **extra,
    }


def _crm_body(call: _Call, item_id: str | None = None, **extra: object) -> dict:
    return {
        "action_type": "crm_update",
        "action_item_id": item_id or call.item_id,
        "target_system": "hubspot",
        "target_external_record_id": call.record_id,
        **extra,
    }


def _coaching_body(world: _World, case: _Case, criterion: str = "greeting", **extra) -> dict:
    return {
        "action_type": "coaching_task",
        "review_item_id": case.id,
        "criterion_id": criterion,
        "owner_user_id": world.user_id("sup2"),
        "due_at": _due(days=3),
        "instruction": INSTRUCTION,
        **extra,
    }


def _check_view(view: dict) -> dict:
    assert set(view) == VIEW_KEYS, sorted(set(view) ^ VIEW_KEYS)
    assert view["payload_hash"] == _sha(view["payload"]), view
    for name in ("created_at", "updated_at", "decided_at", "revoked_at"):
        if view[name] is not None:
            _utc(view[name])
    if view["status"] == "pending":
        assert isinstance(view["stale"], dict), view["stale"]
        assert set(view["stale"]) == {"is_stale", "reason"}, view["stale"]
    else:
        assert view["stale"] is None, view
    if view["delivery_request"] is not None:
        assert set(view["delivery_request"]) == REQUEST_VIEW_KEYS, view["delivery_request"]
    return view


def _create(world: _World, body: dict, *, by: str = "owner"):
    return world.persona(by).post(ROOT, json=body)


def _created(world: _World, body: dict, *, by: str = "owner") -> dict:
    response = _create(world, body, by=by)
    assert response.status_code == 201, (body, response.text)
    view = _check_view(response.json())
    assert (view["status"], view["revision"], view["payload_version"]) == ("pending", 1, 1)
    assert view["created_by_user_id"] == world.user_id(by)
    return view


def _get(world: _World, proposal: dict | str, *, by: str = "owner"):
    proposal_id = proposal if isinstance(proposal, str) else proposal["id"]
    return world.persona(by).get(f"{ROOT}/{proposal_id}")


def _view(world: _World, proposal: dict | str, *, by: str = "owner") -> dict:
    response = _get(world, proposal, by=by)
    assert response.status_code == 200, response.text
    return _check_view(response.json())


def _post(world: _World, proposal: dict, action: str, *, by: str = "owner", **body):
    payload = {"expected_revision": proposal["revision"], **body}
    return world.persona(by).post(f"{ROOT}/{proposal['id']}/{action}", json=payload)


def _approve(world: _World, proposal: dict, *, by: str = "owner", **override):
    body = {
        "payload_hash": proposal["payload_hash"],
        "source_transcript_revision_id": proposal["source_transcript_revision_id"],
        **override,
    }
    return _post(world, proposal, "approve", by=by, **body)


def _approved(world: _World, proposal: dict, *, by: str = "owner", **override) -> dict:
    response = _approve(world, proposal, by=by, **override)
    assert response.status_code == 201, response.text
    view = _check_view(response.json())
    assert view["status"] == "approved" and view["revision"] == proposal["revision"] + 1
    assert view["delivery_request"] is not None
    return view


def _rejected(world: _World, proposal: dict, *, reason: str = "duplicate", by: str = "owner"):
    response = _post(world, proposal, "reject", by=by, reason=reason)
    assert response.status_code == 200, response.text
    view = _check_view(response.json())
    assert (view["status"], view["reject_reason"]) == ("rejected", reason)
    return view


def _revoked(world: _World, proposal: dict, *, reason: str = "no_longer_needed", by="owner"):
    response = _post(world, proposal, "revoke", by=by, reason=reason)
    assert response.status_code == 200, response.text
    view = _check_view(response.json())
    assert (view["status"], view["revoke_reason"]) == ("revoked", reason)
    return view


def _refreshed(world: _World, proposal: dict, *, by: str = "owner") -> dict:
    response = _post(world, proposal, "refresh", by=by)
    assert response.status_code == 200, response.text
    view = _check_view(response.json())
    assert view["payload_version"] == proposal["payload_version"] + 1
    assert view["revision"] == proposal["revision"] + 1
    assert view["stale"] == {"is_stale": False, "reason": None}, view["stale"]
    return view


def _row(proposal: dict | str) -> ActionProposal:
    proposal_id = proposal if isinstance(proposal, str) else proposal["id"]
    with inspection_session_factory()() as db:
        row = db.get(ActionProposal, proposal_id)
        assert row is not None, proposal_id
        return row


def _requests(proposal: dict | str) -> list[ActionDeliveryRequest]:
    proposal_id = proposal if isinstance(proposal, str) else proposal["id"]
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(ActionDeliveryRequest).where(
                    ActionDeliveryRequest.proposal_id == proposal_id
                )
            )
        )


def _proposal_count(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(ActionProposal)
            .where(ActionProposal.workspace_id == workspace_id)
        )


def _snapshot(workspace_id: str) -> tuple:
    with inspection_session_factory()() as db:
        proposals = db.execute(
            select(
                ActionProposal.id,
                ActionProposal.status,
                ActionProposal.revision,
                ActionProposal.payload_version,
                ActionProposal.payload_hash,
                ActionProposal.source_transcript_revision_id,
                ActionProposal.source_digest,
            )
            .where(ActionProposal.workspace_id == workspace_id)
            .order_by(ActionProposal.id)
        ).all()
        requests = db.scalars(
            select(ActionDeliveryRequest.id)
            .where(ActionDeliveryRequest.workspace_id == workspace_id)
            .order_by(ActionDeliveryRequest.id)
        ).all()
        payloads = db.scalars(
            select(ActionProposal.payload_json)
            .where(ActionProposal.workspace_id == workspace_id)
            .order_by(ActionProposal.id)
        ).all()
    return [tuple(row) for row in proposals], list(requests), [_sha(p) for p in payloads]


def _events(workspace_id: str, proposal_id: str | None = None) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        query = select(AuditEvent).where(
            AuditEvent.workspace_id == workspace_id,
            AuditEvent.event_type.startswith(EVENT_PREFIX),
        )
        if proposal_id is not None:
            query = query.where(AuditEvent.target_id == proposal_id)
        return list(db.scalars(query.order_by(AuditEvent.occurred_at, AuditEvent.id)))


def _event_ids(workspace_id: str) -> set[str]:
    return {event.id for event in _events(workspace_id)}


def _new_events(workspace_id: str, seen: set[str]) -> list[AuditEvent]:
    return [event for event in _events(workspace_id) if event.id not in seen]


def _assert_success_event(event: AuditEvent, event_type: str, proposal_id: str, actor: str):
    assert (event.event_type, event.outcome) == (event_type, "success"), event.event_type
    assert (event.target_type, event.target_id) == ("action_proposal", proposal_id)
    assert event.actor_user_id == actor
    assert set(event.details) <= AUDIT_KEYS, sorted(set(event.details) - AUDIT_KEYS)


def _assert_denied_once(workspace_id: str, seen: set[str], action: str, code: str) -> None:
    new = _new_events(workspace_id, seen)
    assert [(e.event_type, e.outcome) for e in new] == [(WRITE_EVENTS[action], "denied")], [
        (e.event_type, e.outcome, e.details) for e in new
    ]
    assert new[0].details.get("reason_code") == code, new[0].details
    assert set(new[0].details) <= AUDIT_KEYS, sorted(set(new[0].details) - AUDIT_KEYS)


def _listed(world: _World, *, by: str = "owner", params=None) -> list[dict]:
    response = world.persona(by).get(ROOT, params=params or [])
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}, body
    for item in body["items"]:
        assert "payload" not in item, item
        assert set(item) <= VIEW_KEYS, sorted(set(item) - VIEW_KEYS)
        assert item.get("stale") is None, item
    return body["items"]


def _listed_ids(world: _World, *, by: str = "owner", params=None) -> set[str]:
    return {item["id"] for item in _listed(world, by=by, params=params)}


def _ticket_on(world: _World, call: _Call | None = None, *, by: str = "owner") -> dict:
    call = call or _Call(world)
    return _created(world, _ticket_body(call.item()), by=by)


def _routes(world: _World, call: _Call, proposal: dict, approved: dict) -> list[tuple]:
    revision = {"expected_revision": proposal["revision"]}
    approve = {
        **revision,
        "payload_hash": proposal["payload_hash"],
        "source_transcript_revision_id": proposal["source_transcript_revision_id"],
    }
    path = f"{ROOT}/{proposal['id']}"
    return [
        ("create", "POST", ROOT, _ticket_body(call.item())),
        ("list", "GET", ROOT, None),
        ("get", "GET", path, None),
        ("refresh", "POST", f"{path}/refresh", revision),
        ("approve", "POST", f"{path}/approve", approve),
        ("reject", "POST", f"{path}/reject", {**revision, "reason": "duplicate"}),
        (
            "revoke",
            "POST",
            f"{ROOT}/{approved['id']}/revoke",
            {"expected_revision": approved["revision"], "reason": "no_longer_needed"},
        ),
    ]


def _send(world: _World, name: str, method: str, path: str, body: dict | None):
    persona = world.persona(name)
    if method == "GET":
        return persona.get(path)
    return persona.post(path, json=body)


def _fixture(world: _World, call: _Call | None = None) -> tuple[_Call, dict, dict]:
    call = call or _Call(world)
    proposal = _created(world, _ticket_body(call.item()))
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    return call, proposal, approved


def test_the_two_permissions_follow_the_roles_and_no_api_key_scope_carries_them(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    expected = {
        "owner": (True, True),
        "admin": (True, True),
        "sup": (True, False),
        "agent": (True, False),
        "viewer": (False, False),
    }
    for name, (propose, approve) in expected.items():
        held = _permissions(world.persona(name))
        assert ("action:propose" in held, "action:approve" in held) == (propose, approve), name
    carried = {scope.value for scope in API_KEY_SCOPES}
    assert "action:propose" not in carried and "action:approve" not in carried
    for scopes in (["action:propose"], ["action:approve"], ["meeting:read", "action:propose"]):
        _refused(
            _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes),
            422,
            "invalid_scopes",
        )
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=["meeting:read"])
    assert minted.status_code == 201, minted.text


def _placeholders(path: str) -> str:
    parts = path.split("/")
    return "/".join("{}" if part.startswith("{") and part.endswith("}") else part for part in parts)


def test_the_matrix_lists_both_permissions_and_every_session_route():
    marks = {row[0]: row[1:] for row in _doc_block("permissions")}
    owner, admin, member, viewer = marks["action:propose"][:4]
    assert (owner, admin, member) == ("✓", "✓", "✓") and viewer != "✓"
    owner, admin, member, viewer = marks["action:approve"][:4]
    assert (owner, admin) == ("✓", "✓") and member != "✓" and viewer != "✓"
    scopes = {row[0]: row[1:] for row in _doc_block("api-key-scopes")}
    for permission in ("action:propose", "action:approve"):
        assert scopes.get(permission, ["–"])[0] != "✓", permission
    routes = {(row[0], _placeholders(row[1])): row[2] for row in _doc_block("routes")}
    assert {route: routes.get(route) for route in DESIGNED_ROUTES} == DESIGNED_ROUTES


def test_each_role_gets_its_designed_outcome_on_every_route(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    allowed = {
        "owner": {"create", "list", "get", "refresh", "approve", "reject", "revoke"},
        "admin": {"create", "list", "get", "refresh", "approve", "reject", "revoke"},
        "sup": {"create", "list", "get", "refresh"},
        "viewer": set(),
    }
    success = {"create": 201, "list": 200, "get": 200, "refresh": 200, "approve": 201}
    for name, permitted in allowed.items():
        call = _Call(world)
        for label in ("create", "list", "get", "refresh", "approve", "reject", "revoke"):
            call, proposal, approved = _fixture(world, call)
            route = next(r for r in _routes(world, call, proposal, approved) if r[0] == label)
            before = _snapshot(world.workspace_id)
            response = _send(world, name, *route[1:])
            if label in permitted:
                assert response.status_code == success.get(label, 200), (name, label, response.text)
            else:
                _refused(response, 403, "permission_denied")
                assert _snapshot(world.workspace_id) == before, (name, label)


def test_an_api_key_is_refused_on_every_route_in_every_module_state(
    client, email_outbox, monkeypatch, settings
):
    world = _world(client, email_outbox, monkeypatch)
    call, proposal, approved = _fixture(world)
    scopes = sorted(scope.value for scope in API_KEY_SCOPES)
    minted = _mint(client, world.owner.headers, world.owner.cookies, scopes=scopes)
    assert minted.status_code == 201, minted.text
    keys = (
        _bearer(minted.json()["key"], **{"X-Workspace-ID": world.workspace_id}),
        _bearer("hk_unknown.not-a-real-key-at-all", **{"X-Workspace-ID": world.workspace_id}),
    )
    before = _snapshot(world.workspace_id)
    for state in ("enabled", "admin", "license", "install"):
        if state != "enabled":
            _apply_state(client, settings, world.workspace_id, "integrations", state)
        try:
            for headers in keys:
                for _, method, path, body in _routes(world, call, proposal, approved):
                    response = client.request(method, path, json=body, headers=headers)
                    _refused(response, 401, "api_key_not_accepted")
        finally:
            _restore_install(client, settings)
            _seed_admin(world.workspace_id, {})
            _license(world.workspace_id, "action_all_modules", list(MODULE_KEYS))
    assert _snapshot(world.workspace_id) == before
    assert _view(world, proposal)["status"] == "pending"


@pytest.mark.parametrize("state", ["admin", "license", "install"])
def test_the_module_off_refuses_before_the_permission_check(
    client, email_outbox, monkeypatch, settings, state
):
    world = _world(client, email_outbox, monkeypatch)
    call, proposal, approved = _fixture(world)
    reason = {
        "admin": "disabled_by_admin",
        "license": "not_licensed",
        "install": "disabled_for_install",
    }[state]
    before = _snapshot(world.workspace_id)
    _apply_state(client, settings, world.workspace_id, "integrations", state)
    try:
        for name in ("owner", "sup", "viewer"):
            for label, method, path, body in _routes(world, call, proposal, approved):
                response = _send(world, name, method, path, body)
                _refused(response, 403, "module_disabled")
                assert _details(response) == {"module": "integrations", "reason": reason}, label
    finally:
        _restore_install(client, settings)
        _seed_admin(world.workspace_id, {})
        _license(world.workspace_id, "action_all_modules", list(MODULE_KEYS))
    assert _snapshot(world.workspace_id) == before
    assert _view(world, proposal)["status"] == "pending"
    _rejected(world, proposal)


def test_every_write_without_the_csrf_token_is_refused_and_reads_still_work(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call, proposal, approved = _fixture(world)
    before = _snapshot(world.workspace_id)
    for label, method, path, body in _routes(world, call, proposal, approved):
        sent = client.request(
            method,
            path,
            json=body,
            cookies=world.owner.cookies,
            headers={"X-Workspace-ID": world.workspace_id},
        )
        if method == "POST":
            _refused(sent, 403, "csrf_failed")
        else:
            assert sent.status_code == 200, (label, sent.text)
    assert _snapshot(world.workspace_id) == before
    _rejected(world, proposal)


def test_an_unknown_body_field_is_refused_and_the_same_body_without_it_works(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    item = call.item()
    count = _proposal_count(world.workspace_id)
    _refused(_create(world, _ticket_body(item, color="red")), 422, "validation_error")
    assert _proposal_count(world.workspace_id) == count
    proposal = _created(world, _ticket_body(item))
    _refused(_approve(world, proposal, color="red"), 422, "validation_error")
    _refused(
        _post(world, proposal, "reject", reason="duplicate", color="red"), 422, "validation_error"
    )
    assert _requests(proposal) == []
    _approved(world, proposal)


def test_record_scope_team_hides_proposals_outside_the_callers_teams(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup_b", record_scope="team")
    team_a = _Call(world, team="a")
    hidden = _created(world, _ticket_body(team_a.item()))
    finding = _created(world, _crm_body(team_a, team_a.item()))
    before = _snapshot(world.workspace_id)
    for proposal in (hidden, finding):
        _refused(_get(world, proposal, by="sup_b"), 404, "action_proposal_not_found")
        _refused(_post(world, proposal, "refresh", by="sup_b"), 404, "action_proposal_not_found")
        _refused(_approve(world, proposal, by="sup_b"), 403, "permission_denied")
    assert hidden["id"] not in _listed_ids(world, by="sup_b")
    _refused(_create(world, _ticket_body(team_a.item()), by="sup_b"), 404, "action_item_not_found")
    assert _snapshot(world.workspace_id) == before

    assert _view(world, hidden, by="sup")["id"] == hidden["id"]
    assert hidden["id"] in _listed_ids(world, by="sup")
    _refreshed(world, hidden, by="sup")
    team_b = _Call(world, team="b")
    own = _created(world, _ticket_body(team_b.item()), by="sup_b")
    assert own["id"] in _listed_ids(world, by="sup_b")
    _refreshed(world, own, by="sup_b")


def test_ids_from_another_workspace_answer_404(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch, "sup_agent")
    call, proposal, approved = _fixture(world)
    case = _review(world)
    outsider = _outsider(client, email_outbox, "proposals")
    before = _snapshot(world.workspace_id)
    _refused(outsider.get(f"{ROOT}/{proposal['id']}"), 404, "action_proposal_not_found")
    for action, body in (
        ("refresh", {}),
        (
            "approve",
            {
                "payload_hash": proposal["payload_hash"],
                "source_transcript_revision_id": proposal["source_transcript_revision_id"],
            },
        ),
        ("reject", {"reason": "duplicate"}),
    ):
        sent = outsider.post(
            f"{ROOT}/{proposal['id']}/{action}",
            json={"expected_revision": proposal["revision"], **body},
        )
        _refused(sent, 404, "action_proposal_not_found")
    sent = outsider.post(
        f"{ROOT}/{approved['id']}/revoke",
        json={"expected_revision": approved["revision"], "reason": "other"},
    )
    _refused(sent, 404, "action_proposal_not_found")
    _refused(outsider.post(ROOT, json=_ticket_body(call.item())), 404, "action_item_not_found")
    _refused(outsider.post(ROOT, json=_coaching_body(world, case)), 404, "review_item_not_found")
    outsider_list = outsider.get(ROOT)
    assert outsider_list.status_code == 200, outsider_list.text
    assert outsider_list.json()["items"] == []
    assert _snapshot(world.workspace_id) == before
    assert _view(world, proposal)["id"] == proposal["id"]


def test_a_deleted_meeting_hides_its_proposals(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    gone = _Call(world)
    kept = _Call(world)
    hidden = _created(world, _ticket_body(gone.item()))
    shown = _created(world, _ticket_body(kept.item()))
    _set_meeting(gone.meeting_id, deleted_at=datetime.now(UTC))
    before = _snapshot(world.workspace_id)
    _refused(_get(world, hidden), 404, "action_proposal_not_found")
    _refused(_approve(world, hidden), 404, "action_proposal_not_found")
    _refused(_post(world, hidden, "refresh"), 404, "action_proposal_not_found")
    _refused(_post(world, hidden, "reject", reason="duplicate"), 404, "action_proposal_not_found")
    listed = _listed_ids(world)
    assert hidden["id"] not in listed and shown["id"] in listed
    assert _snapshot(world.workspace_id) == before
    _approved(world, shown)


def test_a_coaching_proposal_needs_review_queue_visibility(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch, "sup_b")
    case = _review(world)
    coaching = _created(world, _coaching_body(world, case))
    _profile(world.workspace_id)
    ticket = _created(world, _ticket_body(_item(world.workspace_id, case.meeting_id)))
    before = _snapshot(world.workspace_id)
    for name in ("agent", "sup_b"):
        _refused(_get(world, coaching, by=name), 404, "action_proposal_not_found")
        _refused(_post(world, coaching, "refresh", by=name), 404, "action_proposal_not_found")
        assert coaching["id"] not in _listed_ids(world, by=name), name
        _refused(
            _create(world, _coaching_body(world, case, "fee_explained"), by=name),
            404,
            "review_item_not_found",
        )
        assert _view(world, ticket, by=name)["id"] == ticket["id"], name
    assert _snapshot(world.workspace_id) == before
    assert _view(world, coaching, by="sup")["id"] == coaching["id"]
    assert coaching["id"] in _listed_ids(world, by="sup")
    second = _approved(
        world, _created(world, _coaching_body(world, case, "fee_explained"), by="sup")
    )

    _remove_review_item(case.id)
    for name in ("agent", "sup_b", "sup"):
        view = _view(world, coaching, by=name)
        assert view["review_item_id"] is None, name
        assert view["stale"] == {"is_stale": True, "reason": "source_removed"}, name
        assert coaching["id"] in _listed_ids(world, by=name), name
    _rejected(world, _view(world, coaching))
    _revoked(world, _view(world, second))
    assert len(_requests(second)) == 1


SHAPE_CASES = (
    ("action_type", {"action_type": "email"}),
    ("action_type", {"action_type": "Ticket_create"}),
    ("action_item_id", {"action_item_id": None}),
    ("review_item_id", {"review_item_id": "REVIEW"}),
    ("criterion_id", {"criterion_id": "greeting"}),
    ("target_system", {"target_system": None}),
    ("target_external_record_id", {"target_external_record_id": "RECORD"}),
)


def test_create_refuses_a_wrong_shape_and_names_the_field(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    case = _review(world)
    item = call.item()
    bodies = []
    for field, change in SHAPE_CASES:
        body = _ticket_body(item)
        for key, value in change.items():
            body[key] = {"REVIEW": case.id, "RECORD": call.record_id}.get(value, value)
        bodies.append((field, body))
    missing = _ticket_body(item)
    del missing["action_item_id"]
    bodies.append(("action_item_id", missing))
    crm = _crm_body(call, item)
    bodies.append(("target_external_record_id", {**crm, "target_external_record_id": None}))
    bodies.append(("target_system", {**crm, "target_system": None}))
    bodies.append(("review_item_id", {**crm, "review_item_id": case.id}))
    coaching = _coaching_body(world, case)
    bodies.append(("action_item_id", {**coaching, "action_item_id": item}))
    bodies.append(("review_item_id", {**coaching, "review_item_id": None}))
    bodies.append(("criterion_id", {**coaching, "criterion_id": None}))
    no_criterion = dict(coaching)
    del no_criterion["criterion_id"]
    bodies.append(("criterion_id", no_criterion))
    bodies.append(("target_system", {**coaching, "target_system": "zendesk"}))
    bodies.append(
        ("target_external_record_id", {**coaching, "target_external_record_id": call.record_id})
    )
    count = _proposal_count(world.workspace_id)
    for field, body in bodies:
        response = _create(world, body)
        _refused(response, 422, "action_proposal_invalid")
        assert field in response.json()["error"]["message"], (field, response.text)
    assert _proposal_count(world.workspace_id) == count

    _created(world, _ticket_body(item))
    _created(world, _crm_body(call, item))
    _created(world, _coaching_body(world, case))


def test_create_needs_a_visible_source(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    case = _review(world)
    count = _proposal_count(world.workspace_id)
    _refused(_create(world, _ticket_body(str(uuid.uuid4()))), 404, "action_item_not_found")
    _refused(
        _create(world, {**_coaching_body(world, case), "review_item_id": str(uuid.uuid4())}),
        404,
        "review_item_not_found",
    )
    assert _proposal_count(world.workspace_id) == count
    _created(world, _ticket_body(call.item()))
    _created(world, _coaching_body(world, case))


def test_create_needs_an_open_or_in_progress_action_item(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    count = _proposal_count(world.workspace_id)
    for status in (ActionStatus.DONE, ActionStatus.CANCELLED):
        _refused(
            _create(world, _ticket_body(call.item(status=status))),
            409,
            "action_item_not_actionable",
        )
    assert _proposal_count(world.workspace_id) == count
    for status in (ActionStatus.OPEN, ActionStatus.IN_PROGRESS):
        _created(world, _ticket_body(call.item(status=status)))


def _no_agent_case(world: _World) -> _Case:
    case = _Case(world, assignee="sup", agent=None)
    for criterion_id, body in REVIEW:
        case.decided(criterion_id, body)
    case.act("complete", by="sup")
    return case


def test_create_from_a_finding_runs_the_coaching_checks_with_their_codes(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "lone")
    case = _review(world)
    count = _proposal_count(world.workspace_id)
    refusals = (
        (_coaching_body(world, case, "closing"), 422, "coaching_finding_not_failed"),
        (_coaching_body(world, case, "not_a_criterion"), 404, "criterion_not_found"),
        (
            _coaching_body(world, case, owner_user_id=world.user_id("agent")),
            422,
            "coaching_owner_is_agent",
        ),
        (
            _coaching_body(world, case, owner_user_id=world.user_id("lone")),
            422,
            "review_assignee_ineligible",
        ),
        (_coaching_body(world, case, owner_user_id=None), 422, "review_assignee_ineligible"),
        (_coaching_body(world, case, due_at=_due(hours=-1)), 422, "coaching_due_invalid"),
        (_coaching_body(world, case, due_at=_due(days=91)), 422, "coaching_due_invalid"),
        (
            _coaching_body(world, case, due_at=_due_time(days=2).replace(tzinfo=None).isoformat()),
            422,
            "coaching_due_invalid",
        ),
        (_coaching_body(world, case, instruction="   "), 422, "coaching_instruction_required"),
        (
            _coaching_body(world, case, instruction="x" * 2001),
            422,
            "coaching_instruction_required",
        ),
    )
    for body, status, code in refusals:
        _refused(_create(world, body), status, code)
    in_review = _Case(world, assignee="sup", agent="agent")
    _refused(_create(world, _coaching_body(world, in_review)), 409, "review_item_not_coachable")
    _refused(_create(world, _coaching_body(world, _no_agent_case(world))), 409, "coaching_no_agent")
    owner_call = _review(world, agent="admin")
    _refused(
        _create(world, _coaching_body(world, owner_call), by="admin"),
        422,
        "coaching_actor_is_agent",
    )
    appealed = _review(world)
    _filed(world, appealed)
    _refused(_create(world, _coaching_body(world, appealed)), 409, "review_appeal_open")
    coached = _review(world)
    task = world.persona("sup").post(
        f"{COACHING_ITEMS}/{coached.id}/coaching-tasks",
        json={
            "criterion_id": "greeting",
            "owner_user_id": world.user_id("sup2"),
            "due_at": _due(days=3),
            "instruction": INSTRUCTION,
        },
    )
    assert task.status_code == 201, task.text
    _refused(_create(world, _coaching_body(world, coached)), 409, "coaching_task_exists")
    assert _proposal_count(world.workspace_id) == count

    _created(world, _coaching_body(world, case))
    _created(world, _coaching_body(world, case, "fee_explained", instruction="x" * 2000))
    _created(world, _coaching_body(world, case, "callback", due_at=_due(days=89)))
    _created(world, _coaching_body(world, coached, "fee_explained"))


def test_create_pins_the_current_transcript_revision(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    person = _created(world, _ticket_body(call.item()))
    assert person["source_transcript_revision_id"] == call.revision_id
    assert _row(person).source_transcript_revision_id == call.revision_id

    current_run = _run(world.workspace_id, call.meeting_id, call.revision_id)
    ai = _created(world, _ticket_body(call.item(analysis_run_id=current_run)))
    assert ai["source_transcript_revision_id"] == call.revision_id

    bare = _Call(world, transcript=False)
    count = _proposal_count(world.workspace_id)
    _refused(_create(world, _ticket_body(bare.item())), 409, "transcript_missing")
    newer = _new_revision(world.workspace_id, call.meeting_id)
    old_item = call.item(analysis_run_id=current_run)
    _refused(_create(world, _ticket_body(old_item)), 409, "action_proposal_source_stale")
    case = _review(world)
    _new_revision(world.workspace_id, case.meeting_id)
    _refused(_create(world, _coaching_body(world, case)), 409, "action_proposal_source_stale")
    assert _proposal_count(world.workspace_id) == count

    moved = _created(world, _ticket_body(call.item()))
    assert moved["source_transcript_revision_id"] == newer
    new_run = _run(world.workspace_id, call.meeting_id, newer)
    fresh_ai = _created(world, _ticket_body(call.item(analysis_run_id=new_run)))
    assert fresh_ai["source_transcript_revision_id"] == newer


def test_create_checks_the_target(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    outsider = _outsider(client, email_outbox, "targets")
    foreign_record, _ = _record(outsider.workspace_id, None, link=None)
    _profile(outsider.workspace_id, "freshdesk")
    unlinked, _ = _record(world.workspace_id, None, link=None)
    agent_link, _ = _record(world.workspace_id, call.meeting_id, system="pipedrive", link="agent")
    other_system, _ = _record(world.workspace_id, call.meeting_id, system="salesforce")
    inactive, _ = _record(world.workspace_id, call.meeting_id, system="odoo", active=False)
    _profile(world.workspace_id, "trello", active=False)
    item = call.item()
    count = _proposal_count(world.workspace_id)
    for record_id, system in (
        (foreign_record, "hubspot"),
        (unlinked, "hubspot"),
        (agent_link, "pipedrive"),
        (other_system, "hubspot"),
        (inactive, "odoo"),
    ):
        body = _crm_body(call, item, target_external_record_id=record_id, target_system=system)
        _refused(_create(world, body), 422, "action_proposal_target_invalid")
    for system in ("freshdesk", "hubspot", "trello"):
        _refused(
            _create(world, _ticket_body(item, target_system=system)),
            422,
            "action_proposal_target_invalid",
        )
    assert _proposal_count(world.workspace_id) == count
    _set_profile(world.workspace_id, "trello", is_active=True)
    _created(world, _ticket_body(call.item(), target_system="trello"))

    _created(world, _crm_body(call, item))
    ticket_record, _ = _record(
        world.workspace_id, call.meeting_id, system="kommo", link="ticket", record_type="ticket"
    )
    _created(
        world,
        _crm_body(
            call, call.item(), target_external_record_id=ticket_record, target_system="kommo"
        ),
    )
    _created(world, _ticket_body(item))


def test_a_second_open_proposal_for_one_source_and_type_is_refused(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    item = call.item()
    first = _created(world, _ticket_body(item))
    count = _proposal_count(world.workspace_id)
    _refused(_create(world, _ticket_body(item)), 409, "action_proposal_exists")
    assert _proposal_count(world.workspace_id) == count
    _created(world, _crm_body(call, item))
    _rejected(world, first)
    second = _created(world, _ticket_body(item))
    _refused(_create(world, _ticket_body(item)), 409, "action_proposal_exists")
    _revoked(world, _approved(world, second))
    _created(world, _ticket_body(item))

    case = _review(world)
    finding = _created(world, _coaching_body(world, case))
    _refused(_create(world, _coaching_body(world, case)), 409, "action_proposal_exists")
    _created(world, _coaching_body(world, case, "fee_explained"))
    _rejected(world, finding)
    _created(world, _coaching_body(world, case))


def test_create_refusals_follow_the_designed_order(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    bare = _Call(world, transcript=False)
    case = _review(world)
    count = _proposal_count(world.workspace_id)
    _refused(
        _create(world, _ticket_body(str(uuid.uuid4()), review_item_id=case.id)),
        422,
        "action_proposal_invalid",
    )
    _refused(
        _create(world, _ticket_body(bare.item(status=ActionStatus.DONE))),
        409,
        "action_item_not_actionable",
    )
    _refused(
        _create(world, _ticket_body(bare.item(), target_system="freshdesk")),
        409,
        "transcript_missing",
    )
    _new_revision(world.workspace_id, case.meeting_id)
    _refused(
        _create(world, _coaching_body(world, case, "closing")), 422, "coaching_finding_not_failed"
    )
    assert _proposal_count(world.workspace_id) == count
    item = call.item()
    _created(world, _ticket_body(item))
    count = _proposal_count(world.workspace_id)
    _refused(
        _create(world, _ticket_body(item, target_system="freshdesk")),
        422,
        "action_proposal_target_invalid",
    )
    assert _proposal_count(world.workspace_id) == count


def _evidence(call: _Call, spans: list[tuple[str, str]]) -> list[str]:
    base = uuid.uuid4().int >> 32 << 32
    start = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
    ids = []
    with inspection_session_factory()() as db:
        for index, (finding_type, finding_id) in enumerate(spans):
            segment = TranscriptSegment(
                workspace_id=call.workspace_id,
                meeting_id=call.meeting_id,
                revision_id=call.revision_id,
                sequence=index,
                start_ms=index * 1000,
                end_ms=index * 1000 + 900,
                text=f"turn {index}",
            )
            db.add(segment)
            db.flush()
            span = EvidenceSpan(
                id=str(uuid.UUID(int=base + index + 1)),
                workspace_id=call.workspace_id,
                meeting_id=call.meeting_id,
                analysis_run_id=None,
                transcript_revision_id=call.revision_id,
                segment_id=segment.id,
                speaker_key="agent",
                finding_type=finding_type,
                finding_id=finding_id,
                text_start=0,
                text_end=len(segment.text),
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                evidence_type="human",
                created_at=start + timedelta(seconds=index),
            )
            db.add(span)
            ids.append(span.id)
        db.commit()
    return ids


def test_the_ticket_payload_has_exactly_the_designed_keys_and_values(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    due = _due_time(days=4)
    item = call.item(due_at=due, kind="next_step", priority="low")
    other = call.item()
    spans = _evidence(
        call,
        [("action_item", item), ("summary", None), ("action_item", other), ("action_item", item)],
    )
    view = _created(world, _ticket_body(item))
    payload = view["payload"]
    assert set(payload) == TICKET_KEYS, sorted(set(payload) ^ TICKET_KEYS)
    assert payload["schema"] == "ticket_create.v1"
    assert payload["title"] == DESCRIPTION
    assert payload["description"] == DESCRIPTION
    assert (payload["priority"], payload["assignee_text"], payload["kind"]) == (
        "low",
        ASSIGNEE_TEXT,
        "next_step",
    )
    assert (payload["meeting_id"], payload["action_item_id"]) == (call.meeting_id, item)
    assert _utc(payload["due_at"]) == due
    assert view["evidence_span_ids"] == [spans[0], spans[3]]
    assert (view["action_type"], view["source_type"]) == ("ticket_create", "action_item")
    assert (view["action_item_id"], view["review_item_id"], view["criterion_id"]) == (
        item,
        None,
        None,
    )
    assert (view["target_system"], view["target_external_record_id"]) == ("zendesk", None)
    assert view["stale"] == {"is_stale": False, "reason": None}
    assert view["delivery_request"] is None
    row = _row(view)
    assert row.payload_json == payload and row.payload_hash == _sha(payload)
    assert view == _view(world, view)

    long_item = call.item(description="x" * 130, due_at=None)
    long_payload = _created(world, _ticket_body(long_item))["payload"]
    assert long_payload["title"] == "x" * 120 and long_payload["description"] == "x" * 130
    assert long_payload["due_at"] is None
    padded = call.item(description="  Call back the customer  ", kind=None)
    padded_payload = _created(world, _ticket_body(padded))["payload"]
    assert padded_payload["title"] == "Call back the customer"
    assert padded_payload["kind"] is None


def test_the_crm_payload_has_exactly_the_designed_keys_and_values(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    due = _due_time(days=6)
    item = call.item(due_at=due, kind="commitment", priority="medium")
    view = _created(world, _crm_body(call, item))
    payload = view["payload"]
    assert set(payload) == CRM_KEYS, sorted(set(payload) ^ CRM_KEYS)
    assert payload["schema"] == "crm_update.v1"
    assert (payload["source_system"], payload["record_type"], payload["external_id"]) == (
        "hubspot",
        "customer",
        call.external_id,
    )
    assert payload["note"] == DESCRIPTION
    assert (payload["priority"], payload["kind"]) == ("medium", "commitment")
    assert (payload["meeting_id"], payload["action_item_id"]) == (call.meeting_id, item)
    assert _utc(payload["due_at"]) == due
    assert (view["target_system"], view["target_external_record_id"]) == ("hubspot", call.record_id)
    assert view["evidence_span_ids"] == []


def test_the_coaching_payload_has_exactly_the_designed_keys_and_values(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    case = _review(world)
    due = _due_time(days=3)
    view = _created(world, _coaching_body(world, case, due_at=due.isoformat()))
    payload = view["payload"]
    assert set(payload) == COACHING_KEYS, sorted(set(payload) ^ COACHING_KEYS)
    assert payload["schema"] == "coaching_task.v1"
    assert (payload["review_item_id"], payload["criterion_id"]) == (case.id, "greeting")
    assert payload["agent_user_id"] == world.user_id("agent")
    assert payload["owner_user_id"] == world.user_id("sup2")
    assert payload["instruction"] == INSTRUCTION
    assert _utc(payload["due_at"]) == due
    assert (view["action_type"], view["source_type"]) == ("coaching_task", "review_finding")
    assert (view["action_item_id"], view["review_item_id"], view["criterion_id"]) == (
        None,
        case.id,
        "greeting",
    )
    assert (view["target_system"], view["target_external_record_id"]) == (None, None)
    assert view["evidence_span_ids"] == []
    assert view["source_transcript_revision_id"] == case.revision_id


def test_the_kind_label_keeps_a_next_step_apart_from_a_commitment(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    kinds = {}
    for kind in ("commitment", "next_step", None):
        for build in (_ticket_body, lambda item: _crm_body(call, item)):
            payload = _created(world, build(call.item(kind=kind)))["payload"]
            kinds.setdefault(kind, set()).add(payload["kind"])
    assert kinds == {"commitment": {"commitment"}, "next_step": {"next_step"}, None: {None}}


def test_the_source_digest_is_the_canonical_hash_of_the_designed_fields(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    item = call.item(due_at=None, kind="next_step", priority="low")
    view = _created(world, _ticket_body(item))
    fields = {
        "id": item,
        "description": DESCRIPTION,
        "assignee_text": ASSIGNEE_TEXT,
        "assignee_user_id": None,
        "due_at": None,
        "priority": "low",
        "status": ActionStatus.OPEN.value,
        "kind": "next_step",
        "analysis_run_id": None,
    }
    assert _row(view).source_digest == _sha(fields)

    case = _review(world)
    finding = _created(world, _coaching_body(world, case))
    fields = {
        "review_item_id": case.id,
        "criterion_id": "greeting",
        "scorecard_result_id": case.result_id,
        "final_outcome": "failed",
        "outcome_source": "human_result",
        "agent_user_id": world.user_id("agent"),
    }
    assert _row(finding).source_digest == _sha(fields)


def test_the_list_filters_and_pages(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    other = _Call(world)
    pending = _created(world, _ticket_body(call.item()))
    crm = _created(world, _crm_body(call, call.item()))
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    rejected = _rejected(world, _created(world, _ticket_body(other.item())))
    every = {pending["id"], crm["id"], approved["id"], rejected["id"]}
    assert _listed_ids(world) == every
    assert _listed_ids(world, params=[("status", "pending")]) == {pending["id"], crm["id"]}
    assert _listed_ids(world, params=[("status", "pending"), ("status", "approved")]) == {
        pending["id"],
        crm["id"],
        approved["id"],
    }
    assert _listed_ids(world, params=[("action_type", "crm_update")]) == {crm["id"]}
    assert _listed_ids(world, params=[("meeting_id", other.meeting_id)]) == {rejected["id"]}
    seen: list[str] = []
    cursor = None
    for _ in range(6):
        params = [("limit", "1")] + ([("cursor", cursor)] if cursor else [])
        response = world.owner.get(ROOT, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) <= 1
        seen.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert cursor is None and len(seen) == len(set(seen)) and set(seen) == every
    for params in (
        [("limit", "0")],
        [("limit", "101")],
        [("limit", "ten")],
        [("status", "done")],
        [("status", "Pending")],
        [("status", "pending"), ("status", "open")],
        [("action_type", "email")],
        [("cursor", "not-a-cursor")],
    ):
        _refused(world.owner.get(ROOT, params=params), 422, "action_proposal_invalid")
    assert world.owner.get(ROOT, params=[("limit", "100")]).status_code == 200
    assert world.owner.get(ROOT, params=[("limit", "1")]).status_code == 200


def _stale_source_removed(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _ticket_body(item))
    _remove_item(item)
    return proposal, "source_removed", (404, "action_item_not_found")


def _stale_revision_changed(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    proposal = _created(world, _ticket_body(call.item()))
    _new_revision(world.workspace_id, call.meeting_id)
    return proposal, "revision_changed", ()


def _stale_source_changed(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _ticket_body(item))
    _set_item(item, description="Send the corrected renewal contract")
    return proposal, "source_changed", ()


def _stale_item_done(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _ticket_body(item))
    _set_item(item, status=ActionStatus.DONE)
    return proposal, "source_not_actionable", (409, "action_item_not_actionable")


def _stale_revision_and_done(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _ticket_body(item))
    _new_revision(world.workspace_id, call.meeting_id)
    _set_item(item, status=ActionStatus.DONE, description="Changed and done")
    return proposal, "revision_changed", (409, "action_item_not_actionable")


def _stale_done_and_target_invalid(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _crm_body(call, item))
    _set_item(item, status=ActionStatus.CANCELLED)
    _set_record(call.record_id, is_active=False)
    return proposal, "source_not_actionable", (409, "action_item_not_actionable")


def _stale_changed_and_target_invalid(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _crm_body(call, item))
    _set_item(item, description="Changed while the record went inactive")
    _set_record(call.record_id, is_active=False)
    return proposal, "target_invalid", (422, "action_proposal_target_invalid")


def _appeal(world: _World, case: _Case, decisions: list[dict]) -> None:
    appeal = _assign(world, _filed(world, case, ("greeting", "fee_explained")), "sup_agent")
    _done(world, appeal, "decide", by="sup_agent", **_body(decisions))


def _stale_finding_overturned(world: _World) -> tuple[dict, str, tuple]:
    case = _review(world)
    proposal = _created(world, _coaching_body(world, case))
    _appeal(world, case, [_change("greeting", "passed"), _uphold("fee_explained")])
    return proposal, "finding_not_failed", (422, "coaching_finding_not_failed")


def _stale_finding_removed(world: _World) -> tuple[dict, str, tuple]:
    case = _review(world)
    proposal = _created(world, _coaching_body(world, case))
    _remove_review_item(case.id)
    return proposal, "source_removed", (404, "review_item_not_found")


def _stale_record_inactive(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    proposal = _created(world, _crm_body(call, call.item()))
    _set_record(call.record_id, is_active=False)
    return proposal, "target_invalid", (422, "action_proposal_target_invalid")


def _stale_profile_removed(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    proposal = _created(world, _ticket_body(call.item()))
    _remove_profile(world.workspace_id)
    return proposal, "target_invalid", (422, "action_proposal_target_invalid")


def _stale_profile_inactive(world: _World) -> tuple[dict, str, tuple]:
    call = _Call(world)
    proposal = _created(world, _ticket_body(call.item()))
    _set_profile(world.workspace_id, is_active=False)
    return proposal, "target_invalid", (422, "action_proposal_target_invalid")


STALE_CASES = (
    _stale_source_removed,
    _stale_revision_changed,
    _stale_source_changed,
    _stale_item_done,
    _stale_revision_and_done,
    _stale_done_and_target_invalid,
    _stale_changed_and_target_invalid,
    _stale_finding_overturned,
    _stale_finding_removed,
    _stale_record_inactive,
    _stale_profile_removed,
    _stale_profile_inactive,
)


@pytest.mark.parametrize(
    "build", STALE_CASES, ids=lambda build: build.__name__.removeprefix("_stale_")
)
def test_each_stale_reason_blocks_approval_and_refresh_decides_what_comes_next(
    client, email_outbox, monkeypatch, build
):
    world = _world(client, email_outbox, monkeypatch, "sup_agent")
    proposal, reason, refresh = build(world)
    current = _view(world, proposal)
    assert current["stale"] == {"is_stale": True, "reason": reason}, current["stale"]
    before = _snapshot(world.workspace_id)
    seen = _event_ids(world.workspace_id)
    response = _approve(world, current)
    _refused(response, 409, "action_proposal_stale")
    assert _details(response).get("reason") == reason, response.text
    assert _requests(proposal) == []
    assert _snapshot(world.workspace_id) == before
    _assert_denied_once(world.workspace_id, seen, "approve", "action_proposal_stale")

    if refresh:
        _refused(_post(world, current, "refresh"), *refresh)
        assert _snapshot(world.workspace_id) == before
        assert _requests(proposal) == []
        _rejected(world, current)
        assert _requests(proposal) == []
        return
    fresh = _refreshed(world, current)
    assert fresh["payload_hash"] == _sha(fresh["payload"])
    approved = _approved(world, fresh)
    [request] = _requests(proposal)
    assert request.payload_version == 2 and request.payload_hash == fresh["payload_hash"]
    assert approved["delivery_request"]["id"] == request.id


def test_an_all_upheld_appeal_leaves_the_coaching_proposal_approvable(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch, "sup_agent")
    case = _review(world)
    proposal = _created(world, _coaching_body(world, case))
    digest = _row(proposal).source_digest
    _appeal(world, case, [_uphold("greeting"), _uphold("fee_explained")])
    current = _view(world, proposal)
    assert current["stale"] == {"is_stale": False, "reason": None}, current["stale"]
    assert (current["revision"], current["payload_hash"]) == (
        proposal["revision"],
        proposal["payload_hash"],
    )
    assert _row(proposal).source_digest == digest
    approved = _approved(world, current)
    [request] = _requests(proposal)
    assert request.payload_version == 1 and request.payload_hash == proposal["payload_hash"]
    assert approved["delivery_request"]["id"] == request.id


def test_refresh_rebuilds_the_payload_and_takes_the_new_pin(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _ticket_body(item))
    newer = _new_revision(world.workspace_id, call.meeting_id)
    _set_item(item, description="Call the customer on Monday", priority="medium")
    old_digest = _row(proposal).source_digest
    fresh = _refreshed(world, proposal)
    assert fresh["source_transcript_revision_id"] == newer
    assert fresh["payload"]["description"] == "Call the customer on Monday"
    assert fresh["payload"]["title"] == "Call the customer on Monday"
    assert fresh["payload"]["priority"] == "medium"
    assert fresh["payload_hash"] != proposal["payload_hash"]
    row = _row(proposal)
    assert row.source_digest != old_digest
    assert (row.payload_version, row.revision, row.status) == (2, 2, "pending")
    _refused(_post(world, proposal, "refresh"), 409, "action_proposal_revision_conflict")
    again = _refreshed(world, fresh)
    assert again["payload_hash"] == fresh["payload_hash"]


def test_approve_with_an_old_hash_or_an_old_pin_is_refused(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    item = call.item()
    proposal = _created(world, _ticket_body(item))
    _set_item(item, description="Call the customer on Monday")
    fresh = _refreshed(world, proposal)
    _refused(
        _approve(world, fresh, payload_hash=proposal["payload_hash"]),
        409,
        "action_proposal_payload_changed",
    )
    assert _requests(proposal) == []
    newer = _new_revision(world.workspace_id, call.meeting_id)
    pinned = _refreshed(world, fresh)
    assert pinned["source_transcript_revision_id"] == newer
    before = _snapshot(world.workspace_id)
    _refused(
        _approve(world, pinned, source_transcript_revision_id=call.revision_id),
        409,
        "action_proposal_pin_mismatch",
    )
    _refused(_approve(world, pinned, payload_hash="0" * 64), 409, "action_proposal_payload_changed")
    assert _snapshot(world.workspace_id) == before
    assert _requests(proposal) == []
    _approved(world, pinned)


def test_approve_refusals_follow_the_designed_order(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    rejected = _rejected(world, _created(world, _ticket_body(call.item())))
    _refused(
        _approve(world, rejected, expected_revision=99, payload_hash="0" * 64),
        409,
        "action_proposal_not_pending",
    )
    item = call.item()
    proposal = _created(world, _ticket_body(item))
    _set_item(item, description="Something else entirely")
    response = _approve(
        world,
        proposal,
        expected_revision=proposal["revision"] + 5,
        payload_hash="0" * 64,
        source_transcript_revision_id=str(uuid.uuid4()),
    )
    _refused(response, 409, "action_proposal_revision_conflict")
    assert _details(response).get("current_revision") == proposal["revision"], response.text
    _refused(
        _approve(
            world, proposal, payload_hash="0" * 64, source_transcript_revision_id=str(uuid.uuid4())
        ),
        409,
        "action_proposal_payload_changed",
    )
    _refused(
        _approve(world, proposal, source_transcript_revision_id=str(uuid.uuid4())),
        409,
        "action_proposal_pin_mismatch",
    )
    response = _approve(world, proposal)
    _refused(response, 409, "action_proposal_stale")
    assert _details(response).get("reason") == "source_changed"
    assert _requests(proposal) == []
    outsider = _outsider(client, email_outbox, "order")
    sent = outsider.post(
        f"{ROOT}/{proposal['id']}/approve",
        json={
            "expected_revision": 99,
            "payload_hash": "0" * 64,
            "source_transcript_revision_id": str(uuid.uuid4()),
        },
    )
    _refused(sent, 404, "action_proposal_not_found")


def test_approve_writes_one_exact_request_and_self_approval_is_allowed(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    proposal = _created(world, _crm_body(call, call.item()), by="admin")
    stored_digest = _row(proposal).source_digest
    approved = _approved(world, proposal, by="admin")
    assert approved["decided_by_user_id"] == world.user_id("admin")
    _utc(approved["decided_at"])
    [request] = _requests(proposal)
    expected_key = _key_of(world.workspace_id, proposal["id"], proposal["payload_hash"])
    assert (request.workspace_id, request.meeting_id) == (world.workspace_id, call.meeting_id)
    assert request.payload_json == proposal["payload"]
    assert (request.payload_hash, request.payload_version) == (proposal["payload_hash"], 1)
    assert (request.action_type, request.source_type) == ("crm_update", "action_item")
    assert (request.target_system, request.target_external_record_id) == (
        "hubspot",
        call.record_id,
    )
    assert (request.source_id, request.source_criterion_id) == (proposal["action_item_id"], None)
    assert request.source_transcript_revision_id == call.revision_id
    assert request.source_digest == stored_digest
    assert request.approved_by_user_id == world.user_id("admin")
    assert request.idempotency_key == expected_key
    assert approved["delivery_request"]["id"] == request.id
    assert approved["delivery_request"]["idempotency_key"] == expected_key
    assert approved["delivery_request"]["approved_by_user_id"] == world.user_id("admin")
    _utc(approved["delivery_request"]["approved_at"])
    row = _row(proposal)
    assert (row.status, row.revision, row.decided_by_user_id) == (
        "approved",
        2,
        world.user_id("admin"),
    )
    assert _view(world, proposal)["delivery_request"] == approved["delivery_request"]

    case = _review(world)
    coaching = _created(world, _coaching_body(world, case), by="sup")
    _approved(world, coaching)
    [finding_request] = _requests(coaching)
    assert (finding_request.source_type, finding_request.source_id) == ("review_finding", case.id)
    assert finding_request.source_criterion_id == "greeting"
    assert (finding_request.target_system, finding_request.target_external_record_id) == (
        None,
        None,
    )


def _edited(payload: dict, **changes: object) -> dict:
    return {**payload, **changes}


def test_edit_then_approve_stores_the_edit_and_the_request_copies_it(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    due = _due_time(days=9)
    cases = []
    ticket = _created(world, _ticket_body(call.item()))
    cases.append(
        (
            ticket,
            _edited(
                ticket["payload"],
                title="Renewal contract",
                description="Send the renewal contract today",
                priority="low",
                due_at=due.isoformat(),
            ),
        )
    )
    crm = _created(world, _crm_body(call, call.item()))
    cases.append((crm, _edited(crm["payload"], note="Customer asked for a call", due_at=None)))
    case = _review(world)
    coaching = _created(world, _coaching_body(world, case))
    cases.append(
        (
            coaching,
            _edited(
                coaching["payload"],
                owner_user_id=world.user_id("sup"),
                due_at=due.isoformat(),
                instruction="Use the customer's name twice.",
            ),
        )
    )
    for proposal, edit in cases:
        seen = _event_ids(world.workspace_id)
        approved = _approved(world, proposal, payload=edit)
        stored = approved["payload"]
        for key, value in edit.items():
            if key == "due_at" and value is not None:
                assert _utc(stored[key]) == _utc(value), key
            else:
                assert stored[key] == value, key
        assert approved["payload_version"] == 2
        assert approved["payload_hash"] == _sha(stored) != proposal["payload_hash"]
        row = _row(proposal)
        assert (row.payload_json, row.payload_hash, row.payload_version) == (
            stored,
            approved["payload_hash"],
            2,
        )
        [request] = _requests(proposal)
        assert request.payload_json == stored
        assert (request.payload_hash, request.payload_version) == (approved["payload_hash"], 2)
        assert request.idempotency_key == _key_of(
            world.workspace_id, proposal["id"], approved["payload_hash"]
        )
        [event] = _new_events(world.workspace_id, seen)
        _assert_success_event(
            event, "action_proposal.approved", proposal["id"], world.user_id("owner")
        )
        if "edited" in event.details:
            assert event.details["edited"] is True


def test_a_non_editable_or_invalid_edit_is_refused_and_writes_nothing(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    ticket = _created(world, _ticket_body(call.item()))
    crm = _created(world, _crm_body(call, call.item()))
    case = _review(world)
    coaching = _created(world, _coaching_body(world, case))
    other = _Call(world)
    not_editable = (
        (ticket, {"meeting_id": other.meeting_id}),
        (ticket, {"action_item_id": other.item_id}),
        (ticket, {"schema": "ticket_create.v2"}),
        (ticket, {"kind": "next_step"}),
        (ticket, {"assignee_text": "Somebody else"}),
        (crm, {"external_id": "C-other"}),
        (crm, {"source_system": "salesforce"}),
        (crm, {"priority": "low"}),
        (coaching, {"criterion_id": "fee_explained"}),
        (coaching, {"agent_user_id": world.user_id("sup")}),
    )
    before = _snapshot(world.workspace_id)
    for proposal, change in not_editable:
        response = _approve(world, proposal, payload=_edited(proposal["payload"], **change))
        _refused(response, 422, "action_proposal_field_not_editable")
    invalid = (
        (ticket, {"title": ""}),
        (ticket, {"title": "x" * 121}),
        (ticket, {"description": ""}),
        (ticket, {"description": "x" * 4001}),
        (ticket, {"priority": "urgent"}),
        (crm, {"note": ""}),
        (crm, {"note": "x" * 4001}),
        (crm, {"title": "Not a crm key"}),
        (ticket, {"color": "red"}),
        (coaching, {"instruction": "   "}),
        (coaching, {"instruction": "x" * 2001}),
    )
    for proposal, change in invalid:
        response = _approve(world, proposal, payload=_edited(proposal["payload"], **change))
        _refused(response, 422, "action_proposal_invalid")
    _refused(
        _approve(
            world,
            coaching,
            payload=_edited(coaching["payload"], owner_user_id=world.user_id("agent")),
        ),
        422,
        "coaching_owner_is_agent",
    )
    _refused(
        _approve(world, coaching, payload=_edited(coaching["payload"], due_at=_due(hours=-1))),
        422,
        "coaching_due_invalid",
    )
    assert _snapshot(world.workspace_id) == before
    _approved(world, ticket, payload=_edited(ticket["payload"], title="x" * 120))
    _approved(world, crm, payload=_edited(crm["payload"], note="x" * 4000))
    _approved(world, coaching)


def test_reject_and_revoke_follow_their_reason_lists(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    pending = _created(world, _ticket_body(call.item()))
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    before = _snapshot(world.workspace_id)
    for reason in ("spam", "Duplicate", "duplicate ", "", "approved_in_error"):
        _refused(_post(world, pending, "reject", reason=reason), 422, "action_proposal_invalid")
    for reason in ("x", "Other", "other ", "", "not_needed"):
        _refused(_post(world, approved, "revoke", reason=reason), 422, "action_proposal_invalid")
    assert _snapshot(world.workspace_id) == before
    for reason in REJECT_REASONS:
        view = _rejected(world, _created(world, _ticket_body(call.item())), reason=reason)
        assert view["decided_by_user_id"] == world.user_id("owner")
        _utc(view["decided_at"])
        assert _requests(view) == []
    for reason in REVOKE_REASONS:
        view = _revoked(
            world, _approved(world, _created(world, _ticket_body(call.item()))), reason=reason
        )
        assert view["revoked_by_user_id"] == world.user_id("owner")
        _utc(view["revoked_at"])


def test_a_revoke_never_touches_the_request(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    [request] = _requests(approved)
    columns = {c: getattr(request, c) for c in ActionDeliveryRequest.__table__.columns.keys()}
    revoked = _revoked(world, approved)
    assert revoked["revision"] == approved["revision"] + 1
    assert revoked["decided_by_user_id"] == approved["decided_by_user_id"]
    [after] = _requests(approved)
    assert {c: getattr(after, c) for c in columns} == columns
    assert revoked["delivery_request"] == approved["delivery_request"]


def test_every_transition_outside_the_status_rules_is_refused(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    pending = _created(world, _ticket_body(call.item()))
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    rejected = _rejected(world, _created(world, _ticket_body(call.item())))
    revoked = _revoked(world, _approved(world, _created(world, _ticket_body(call.item()))))
    before = _snapshot(world.workspace_id)
    for proposal in (approved, rejected, revoked):
        _refused(_approve(world, proposal), 409, "action_proposal_not_pending")
        _refused(
            _post(world, proposal, "reject", reason="other"), 409, "action_proposal_not_pending"
        )
        _refused(_post(world, proposal, "refresh"), 409, "action_proposal_not_pending")
    for proposal in (pending, rejected, revoked):
        _refused(
            _post(world, proposal, "revoke", reason="other"), 409, "action_proposal_not_approved"
        )
    assert _snapshot(world.workspace_id) == before
    assert len(_requests(approved)) == 1 and len(_requests(revoked)) == 1
    assert _requests(pending) == [] and _requests(rejected) == []
    _approved(world, pending)


def test_a_wrong_expected_revision_is_refused_with_the_current_revision(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    pending = _created(world, _ticket_body(call.item()))
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    before = _snapshot(world.workspace_id)
    sends = (
        (pending, "refresh", {}),
        (pending, "approve", {}),
        (pending, "reject", {"reason": "other"}),
        (approved, "revoke", {"reason": "other"}),
    )
    for proposal, action, body in sends:
        for wrong in sorted({0, -1, proposal["revision"] - 1, proposal["revision"] + 1}):
            stale = {**proposal, "revision": wrong}
            response = (
                _approve(world, stale)
                if action == "approve"
                else _post(world, stale, action, **body)
            )
            if wrong < 1:
                _refused(response, 422, "validation_error")
                continue
            _refused(response, 409, "action_proposal_revision_conflict")
            assert _details(response).get("current_revision") == proposal["revision"]
    assert _snapshot(world.workspace_id) == before
    _refreshed(world, pending)
    _revoked(world, approved)


def test_only_an_approved_proposal_has_a_request_and_it_has_exactly_one(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    unapproved = _created(world, _ticket_body(call.item()))
    rejected = _rejected(world, _created(world, _ticket_body(call.item())))
    revoked = _revoked(world, _approved(world, _created(world, _ticket_body(call.item()))))
    stale_item = call.item()
    stale = _created(world, _ticket_body(stale_item))
    _set_item(stale_item, description="Changed after the preview")
    _refused(_approve(world, stale), 409, "action_proposal_stale")
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    for proposal in (unapproved, rejected, stale):
        assert _requests(proposal) == [], proposal["status"]
    assert len(_requests(revoked)) == 1
    [request] = _requests(approved)
    assert request.idempotency_key == _key_of(
        world.workspace_id, approved["id"], approved["payload_hash"]
    )
    with inspection_session_factory()() as db:
        total = db.scalar(
            select(func.count())
            .select_from(ActionDeliveryRequest)
            .where(ActionDeliveryRequest.workspace_id == world.workspace_id)
        )
    assert total == 2


def test_each_success_writes_one_event_with_only_the_allowed_keys(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    item = call.item()
    actor = world.user_id("owner")
    seen = _event_ids(world.workspace_id)
    proposal = _created(world, _ticket_body(item))
    [event] = _new_events(world.workspace_id, seen)
    _assert_success_event(event, "action_proposal.created", proposal["id"], actor)
    _set_item(item, description="Call the customer on Monday")
    seen = _event_ids(world.workspace_id)
    fresh = _refreshed(world, proposal)
    [event] = _new_events(world.workspace_id, seen)
    _assert_success_event(event, "action_proposal.refreshed", proposal["id"], actor)
    seen = _event_ids(world.workspace_id)
    approved = _approved(world, fresh)
    [event] = _new_events(world.workspace_id, seen)
    _assert_success_event(event, "action_proposal.approved", proposal["id"], actor)
    if "delivery_request_id" in event.details:
        assert event.details["delivery_request_id"] == approved["delivery_request"]["id"]
    seen = _event_ids(world.workspace_id)
    _revoked(world, approved)
    [event] = _new_events(world.workspace_id, seen)
    _assert_success_event(event, "action_proposal.revoked", proposal["id"], actor)
    other = _created(world, _ticket_body(call.item()))
    seen = _event_ids(world.workspace_id)
    _rejected(world, other)
    [event] = _new_events(world.workspace_id, seen)
    _assert_success_event(event, "action_proposal.rejected", other["id"], actor)


def test_each_refusal_after_visibility_writes_one_denied_event_and_a_404_writes_none(
    client, email_outbox, monkeypatch
):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    pending = _created(world, _ticket_body(call.item()))
    approved = _approved(world, _created(world, _ticket_body(call.item())))
    refusals = (
        ("approve", lambda: _approve(world, approved), "action_proposal_not_pending"),
        (
            "approve",
            lambda: _approve(world, {**pending, "revision": 9}),
            "action_proposal_revision_conflict",
        ),
        (
            "approve",
            lambda: _approve(world, pending, payload_hash="0" * 64),
            "action_proposal_payload_changed",
        ),
        (
            "approve",
            lambda: _approve(world, pending, source_transcript_revision_id=str(uuid.uuid4())),
            "action_proposal_pin_mismatch",
        ),
        (
            "approve",
            lambda: _approve(world, pending, payload=_edited(pending["payload"], kind="next_step")),
            "action_proposal_field_not_editable",
        ),
        (
            "reject",
            lambda: _post(world, pending, "reject", reason="spam"),
            "action_proposal_invalid",
        ),
        (
            "reject",
            lambda: _post(world, approved, "reject", reason="other"),
            "action_proposal_not_pending",
        ),
        (
            "revoke",
            lambda: _post(world, pending, "revoke", reason="other"),
            "action_proposal_not_approved",
        ),
        ("revoke", lambda: _post(world, approved, "revoke", reason="x"), "action_proposal_invalid"),
        ("refresh", lambda: _post(world, approved, "refresh"), "action_proposal_not_pending"),
        (
            "refresh",
            lambda: _post(world, {**pending, "revision": 9}, "refresh"),
            "action_proposal_revision_conflict",
        ),
    )
    for action, send, code in refusals:
        seen = _event_ids(world.workspace_id)
        response = send()
        assert response.status_code in (409, 422), (code, response.text)
        assert _code(response) == code, response.text
        _assert_denied_once(world.workspace_id, seen, action, code)
    missing = {**pending, "id": str(uuid.uuid4())}
    seen = _event_ids(world.workspace_id)
    _refused(_approve(world, missing), 404, "action_proposal_not_found")
    _refused(_post(world, missing, "reject", reason="other"), 404, "action_proposal_not_found")
    _refused(_post(world, missing, "refresh"), 404, "action_proposal_not_found")
    _refused(_post(world, missing, "revoke", reason="other"), 404, "action_proposal_not_found")
    assert _new_events(world.workspace_id, seen) == []


def test_no_event_holds_payload_text_or_external_ids(client, email_outbox, monkeypatch):
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    ticket = _created(world, _ticket_body(call.item()))
    _approved(world, ticket, payload=_edited(ticket["payload"], title="Edited secret title"))
    crm = _created(world, _crm_body(call, call.item()))
    _approved(world, crm, payload=_edited(crm["payload"], note="Edited secret note"))
    case = _review(world)
    coaching = _created(world, _coaching_body(world, case))
    refused = _approve(world, coaching, payload=_edited(coaching["payload"], instruction="   "))
    _refused(refused, 422, "action_proposal_invalid")
    _rejected(world, coaching)
    events = _events(world.workspace_id)
    assert events, "no audit event"
    text = json.dumps([event.details for event in events], ensure_ascii=False)
    for secret in (
        DESCRIPTION,
        "Edited secret title",
        "Edited secret note",
        INSTRUCTION,
        ASSIGNEE_TEXT,
        call.external_id,
    ):
        assert secret not in text, secret


def _one_lands(results: list, ok: set[int]) -> tuple[list, list]:
    landed = [r for r in results if r.status_code in ok]
    refused = [r for r in results if r.status_code not in ok]
    return landed, refused


@pytest.mark.postgres
def test_two_concurrent_approves_give_one_201_and_one_request(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    for _ in range(3):
        proposal = _created(world, _ticket_body(call.item()))
        body = {
            "expected_revision": proposal["revision"],
            "payload_hash": proposal["payload_hash"],
            "source_transcript_revision_id": proposal["source_transcript_revision_id"],
        }
        path = f"{ROOT}/{proposal['id']}/approve"
        results = _race(client, [(world.owner, path, body), (world.persona("admin"), path, body)])
        landed, refused = _one_lands(results, {201})
        assert len(landed) == 1 and len(refused) == 1, [r.text for r in results]
        assert refused[0].status_code == 409, refused[0].text
        assert _code(refused[0]) in {
            "action_proposal_not_pending",
            "action_proposal_revision_conflict",
        }, refused[0].text
        assert len(_requests(proposal)) == 1


@pytest.mark.postgres
def test_an_approve_racing_a_reject_lets_exactly_one_land(client, email_outbox, monkeypatch):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    for _ in range(3):
        proposal = _created(world, _ticket_body(call.item()))
        approve = {
            "expected_revision": proposal["revision"],
            "payload_hash": proposal["payload_hash"],
            "source_transcript_revision_id": proposal["source_transcript_revision_id"],
        }
        reject = {"expected_revision": proposal["revision"], "reason": "duplicate"}
        results = _race(
            client,
            [
                (world.owner, f"{ROOT}/{proposal['id']}/approve", approve),
                (world.persona("admin"), f"{ROOT}/{proposal['id']}/reject", reject),
            ],
        )
        landed, refused = _one_lands(results, {200, 201})
        assert len(landed) == 1 and len(refused) == 1, [r.text for r in results]
        assert refused[0].status_code == 409, refused[0].text
        row = _row(proposal)
        if landed[0].status_code == 201:
            assert row.status == "approved" and len(_requests(proposal)) == 1
        else:
            assert row.status == "rejected" and _requests(proposal) == []
        assert row.revision == 2


@pytest.mark.postgres
def test_two_concurrent_creates_for_one_source_give_one_201_and_one_409(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    for _ in range(3):
        body = _ticket_body(call.item())
        results = _race(client, [(world.owner, ROOT, body), (world.persona("sup"), ROOT, body)])
        landed, refused = _one_lands(results, {201})
        assert len(landed) == 1 and len(refused) == 1, [r.text for r in results]
        _refused(refused[0], 409, "action_proposal_exists")
        with inspection_session_factory()() as db:
            count = db.scalar(
                select(func.count())
                .select_from(ActionProposal)
                .where(ActionProposal.action_item_id == body["action_item_id"])
            )
        assert count == 1


@pytest.mark.postgres
def test_an_approve_with_the_old_hash_never_lands_after_a_refresh(
    client, email_outbox, monkeypatch
):
    _requires_postgres()
    world = _world(client, email_outbox, monkeypatch)
    call = _Call(world)
    for _ in range(3):
        proposal = _created(world, _crm_body(call, call.item()))
        _set_record(call.record_id, external_id=f"C-{uuid.uuid4().hex[:10]}")
        assert _view(world, proposal)["stale"] == {"is_stale": False, "reason": None}
        approve = {
            "expected_revision": proposal["revision"],
            "payload_hash": proposal["payload_hash"],
            "source_transcript_revision_id": proposal["source_transcript_revision_id"],
        }
        refresh = {"expected_revision": proposal["revision"]}
        results = _race(
            client,
            [
                (world.owner, f"{ROOT}/{proposal['id']}/approve", approve),
                (world.persona("admin"), f"{ROOT}/{proposal['id']}/refresh", refresh),
            ],
        )
        approve_result, refresh_result = results
        assert (approve_result.status_code, refresh_result.status_code) in {
            (201, 409),
            (409, 200),
        }, [r.text for r in results]
        row = _row(proposal)
        if approve_result.status_code == 201:
            _refused(refresh_result, 409, "action_proposal_not_pending")
            [request] = _requests(proposal)
            assert request.payload_hash == proposal["payload_hash"]
            assert (row.status, row.payload_version) == ("approved", 1)
        else:
            assert _requests(proposal) == []
            assert (row.status, row.payload_version) == ("pending", 2)
            assert row.payload_hash != proposal["payload_hash"]
            late = _approve(world, {**proposal, "revision": row.revision})
            _refused(late, 409, "action_proposal_payload_changed")
            assert _requests(proposal) == []
