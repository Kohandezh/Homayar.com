"""HOM-58: the conversation model on ``meetings`` rows, through the API.

A conversation is a ``meetings`` row with the same id.  The meeting responses
show the five conversation columns, ``/meetings`` and the new ``/conversations``
routes filter on them with the same access rules, PATCH changes the three
editable ones, and every write path fills them.  The source reference is never
editable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    ConversationPurge,
    ConversationType,
    IngestionRecord,
    IngestionSourceType,
    Meeting,
    MeetingStatus,
    PurgeState,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context, bind_user_context
from backend.security.idempotency import scoped_idempotency_key
from backend.services.audit_event_types import AuditEventType
from backend.services.ingestion_service import (
    IngestionIdentity,
    IngestionPayload,
    accept_ingestion,
)
from backend.services.meeting_service import conversation_type_for_source, create_upload_job
from backend.tests import test_voicemail_api as _vm
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import (
    CONTENT,
    _patch_media,
    _sha256,
    _tenant,
    _upload,
    _widen_concurrency,
)
from backend.tests.test_meeting_counts import _Persona, _signup, _workspace_with_roles
from backend.tests.test_uploads_api import _authenticated_workspace

vm_client = _vm.vm_client

MEETINGS = "/api/v1/meetings"
CONVERSATIONS = "/api/v1/conversations"
BOTH = pytest.mark.parametrize("base", [MEETINGS, CONVERSATIONS])
CONVERSATION_FIELDS = {
    "conversation_type",
    "direction",
    "external_source_system",
    "external_conversation_id",
    "started_at",
}


# --- helpers -----------------------------------------------------------------


def _insert(
    workspace_id: str,
    *,
    created_by: str,
    title: str = "Call",
    status: MeetingStatus = MeetingStatus.COMPLETED,
    conversation_type: str = "call",
    direction: str | None = None,
    external: tuple[str, str] | None = None,
    started_at: datetime | None = None,
    team_id: str | None = None,
    deleted: bool = False,
) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=created_by,
            title=title,
            status=status,
            duration_seconds=30,
            conversation_type=conversation_type,
            direction=direction,
            external_source_system=external[0] if external else None,
            external_conversation_id=external[1] if external else None,
            started_at=started_at,
            team_id=team_id,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def _row(meeting_id: str) -> Meeting:
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None
        return meeting


def _ids(persona: _Persona, base: str, **params) -> list[str]:
    response = persona.get(base, params=params)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def _error(response) -> dict:
    return response.json()["error"]


def _validation_fields(response) -> set[str]:
    assert response.status_code == 422, response.text
    error = _error(response)
    assert error["code"] == "validation_error", error
    return {detail["field"] for detail in error["details"]}


def _updated_fields(meeting_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        events = db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.event_type == AuditEventType.MEETING_UPDATED.value,
                AuditEvent.target_id == meeting_id,
            )
            .order_by(AuditEvent.occurred_at.asc())
        ).all()
        return [event.details["fields"] for event in events]


def _at(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


# --- AC1: response fields ----------------------------------------------------


@BOTH
def test_list_detail_and_patch_show_the_conversation_fields(client, email_outbox, base):
    owner = _signup(client, email_outbox, email="conv-fields@example.com")
    started = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)
    meeting_id = _insert(
        owner.workspace_id,
        created_by=owner.user_id,
        conversation_type="meeting",
        direction="outbound",
        external=("odoo", "C-1"),
        started_at=started,
    )

    listed = owner.get(base)
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    detail = owner.get(f"{base}/{meeting_id}")
    assert detail.status_code == 200, detail.text
    patched = owner.patch(f"{base}/{meeting_id}", json={"title": "Renamed"})
    assert patched.status_code == 200, patched.text

    for body in (item, detail.json(), patched.json()):
        assert CONVERSATION_FIELDS <= set(body)
        assert body["id"] == meeting_id
        assert body["conversation_type"] == "meeting"
        assert body["direction"] == "outbound"
        assert body["external_source_system"] == "odoo"
        assert body["external_conversation_id"] == "C-1"
        assert _at(body["started_at"]) == started
        # the old fields keep their names and values
        assert body["source"] == "upload" and body["duration_seconds"] == 30
        assert body["status"] == "completed"


def test_a_default_row_shows_call_and_nulls(client, email_outbox):
    owner = _signup(client, email_outbox, email="conv-default@example.com")
    meeting_id = _insert(owner.workspace_id, created_by=owner.user_id)
    body = owner.get(f"{MEETINGS}/{meeting_id}").json()
    assert body["conversation_type"] == "call"
    assert body["direction"] is None
    assert body["external_source_system"] is None
    assert body["external_conversation_id"] is None
    assert body["started_at"] is None


def test_upload_answer_carries_the_conversation_fields(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "conv-upload")
    accepted = _upload(client, tenant, key="conv-upload-0001")
    assert accepted.status_code == 202, accepted.text
    meeting = accepted.json()["meeting"]
    assert meeting["conversation_type"] == "call"
    assert meeting["direction"] is None
    assert meeting["external_source_system"] is None
    assert meeting["external_conversation_id"] is None
    assert meeting["started_at"] is None
    row = _row(meeting["id"])
    assert row.conversation_type == "call" and row.external_source_system is None


# --- AC2, AC3: list filters --------------------------------------------------


@BOTH
def test_list_filters_combine_with_and(client, email_outbox, base):
    owner = _signup(client, email_outbox, email="conv-filters@example.com")
    ws, me = owner.workspace_id, owner.user_id
    call_in = _insert(ws, created_by=me, direction="inbound", external=("odoo", "C-1"))
    call_out = _insert(ws, created_by=me, direction="outbound", external=("odoo", "C-2"))
    meeting = _insert(ws, created_by=me, conversation_type="meeting", direction="internal")
    voicemail = _insert(
        ws,
        created_by=me,
        conversation_type="voicemail",
        direction="inbound",
        external=("asterisk", "C-1"),
        status=MeetingStatus.FAILED,
    )
    _insert(ws, created_by=me, conversation_type="voicemail", deleted=True)

    assert set(_ids(owner, base, conversation_type="call")) == {call_in, call_out}
    assert _ids(owner, base, conversation_type="meeting") == [meeting]
    assert _ids(owner, base, conversation_type="voicemail") == [voicemail]
    assert _ids(owner, base, conversation_type="other") == []
    assert set(_ids(owner, base, direction="inbound")) == {call_in, voicemail}
    assert _ids(owner, base, direction="inbound", conversation_type="call") == [call_in]
    assert set(_ids(owner, base, external_source_system="odoo")) == {call_in, call_out}
    assert _ids(owner, base, external_source_system="odoo", external_conversation_id="C-1") == [
        call_in
    ]
    assert _ids(owner, base, external_source_system="asterisk", external_conversation_id="C-1") == [
        voicemail
    ]
    assert _ids(owner, base, external_source_system="odoo", external_conversation_id="c-1") == []
    assert _ids(owner, base, status="failed") == [voicemail]
    assert _ids(owner, base, status="failed", conversation_type="call") == []


@BOTH
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"conversation_type": "Call"}, "query.conversation_type"),
        ({"conversation_type": "sms"}, "query.conversation_type"),
        ({"direction": "unknown"}, "query.direction"),
        ({"direction": "INBOUND"}, "query.direction"),
        ({"status": "done"}, "query.status"),
    ],
)
def test_a_filter_value_outside_its_enum_is_422(client, email_outbox, base, params, field):
    owner = _signup(client, email_outbox, email="conv-enum@example.com")
    _insert(owner.workspace_id, created_by=owner.user_id)
    assert _validation_fields(owner.get(base, params=params)) == {field}
    allowed = {key: value.lower() for key, value in params.items()}
    if allowed != params:
        assert owner.get(base, params=allowed).status_code == 200


@BOTH
def test_an_external_id_without_its_source_system_is_422(client, email_outbox, base):
    owner = _signup(client, email_outbox, email="conv-extid@example.com")
    match = _insert(owner.workspace_id, created_by=owner.user_id, external=("odoo", "C-1"))

    alone = owner.get(base, params={"external_conversation_id": "C-1"})
    assert alone.status_code == 422, alone.text
    assert _error(alone)["code"] == "external_source_system_required"

    paired = owner.get(
        base, params={"external_source_system": "odoo", "external_conversation_id": "C-1"}
    )
    assert paired.status_code == 200, paired.text
    assert [item["id"] for item in paired.json()["items"]] == [match]


def test_no_filter_gives_the_same_page_on_both_paths(client, email_outbox):
    owner = _signup(client, email_outbox, email="conv-same@example.com")
    for index in range(5):
        _insert(
            owner.workspace_id,
            created_by=owner.user_id,
            title=f"Call {index}",
            conversation_type=("call", "meeting", "voicemail", "other", "call")[index],
        )
    first_meetings = owner.get(MEETINGS, params={"limit": 2})
    first_conversations = owner.get(CONVERSATIONS, params={"limit": 2})
    assert first_meetings.status_code == first_conversations.status_code == 200
    assert first_meetings.json() == first_conversations.json()
    next_before = first_meetings.json()["next_before"]
    assert next_before is not None
    rest_meetings = owner.get(MEETINGS, params={"limit": 50, "before": next_before})
    rest_conversations = owner.get(CONVERSATIONS, params={"limit": 50, "before": next_before})
    assert rest_meetings.json() == rest_conversations.json()
    assert rest_meetings.json()["next_before"] is None
    assert len(first_meetings.json()["items"]) + len(rest_meetings.json()["items"]) == 5
    everything = _ids(owner, MEETINGS)
    assert len(everything) == 5 and everything == _ids(owner, CONVERSATIONS)
    for bad in (0, 101):
        assert _validation_fields(owner.get(CONVERSATIONS, params={"limit": bad})) == {
            "query.limit"
        }


# --- AC2, AC4: tenancy and record scope --------------------------------------


def test_another_workspace_conversation_is_404_and_never_listed(client, email_outbox):
    owner = _signup(client, email_outbox, email="conv-owner-a@example.com")
    stranger = _signup(client, email_outbox, email="conv-owner-b@example.com")
    mine = _insert(owner.workspace_id, created_by=owner.user_id)
    theirs = _insert(stranger.workspace_id, created_by=stranger.user_id)

    assert owner.get(f"{CONVERSATIONS}/{mine}").status_code == 200
    for path in (f"{CONVERSATIONS}/{theirs}", f"{CONVERSATIONS}/{uuid.uuid4()}"):
        denied = owner.get(path)
        assert denied.status_code == 404, path
        assert _error(denied)["code"] == "meeting_not_found"
    assert _ids(owner, CONVERSATIONS) == [mine]
    blocked = owner.patch(f"{CONVERSATIONS}/{theirs}", json={"direction": "inbound"})
    assert blocked.status_code == 404 and _error(blocked)["code"] == "meeting_not_found"
    assert _row(theirs).direction is None


def test_team_scope_applies_to_conversations(client, email_outbox, monkeypatch):
    owner, people = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"sup": "member"}
    )
    sup = people["sup"]
    ws = owner.workspace_id
    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200, switched.text
    team_a = owner.post("/api/v1/teams", json={"name": "Team A"}).json()["id"]
    team_b = owner.post("/api/v1/teams", json={"name": "Team B"}).json()["id"]
    assigned = owner.post(
        f"/api/v1/teams/{team_a}/assignments", json={"user_id": sup.user_id, "role": "supervisor"}
    )
    assert assigned.status_code == 201, assigned.text
    own_team = _insert(ws, created_by=owner.user_id, team_id=team_a, direction="inbound")
    other_team = _insert(ws, created_by=owner.user_id, team_id=team_b, direction="inbound")

    assert _ids(sup, CONVERSATIONS) == [own_team]
    assert _ids(sup, CONVERSATIONS, direction="inbound") == [own_team]
    assert sup.get(f"{CONVERSATIONS}/{own_team}").status_code == 200
    hidden = sup.get(f"{CONVERSATIONS}/{other_team}")
    assert hidden.status_code == 404 and _error(hidden)["code"] == "meeting_not_found"
    assert set(_ids(owner, CONVERSATIONS)) == {own_team, other_team}


# --- AC4: same id, same answer -----------------------------------------------


def test_conversation_detail_is_the_meeting_detail(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "conv-detail")
    accepted = _upload(client, tenant, key="conv-detail-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    persona = _Persona(
        client,
        tenant["workspace_id"],
        {"user": {"id": tenant["user_id"]}},
        tenant["cookies"],
    )

    meeting = persona.get(f"{MEETINGS}/{meeting_id}")
    conversation = persona.get(f"{CONVERSATIONS}/{meeting_id}")
    assert meeting.status_code == conversation.status_code == 200
    assert conversation.json() == meeting.json()
    assert conversation.json()["recording"] is not None
    assert conversation.json()["ingestion"] is not None

    assert persona.delete(f"{MEETINGS}/{meeting_id}").status_code == 204
    for path in (f"{MEETINGS}/{meeting_id}", f"{CONVERSATIONS}/{meeting_id}"):
        gone = persona.get(path)
        assert gone.status_code == 404 and _error(gone)["code"] == "meeting_not_found"


# --- AC5, AC6: PATCH ---------------------------------------------------------


@BOTH
def test_patch_sets_and_clears_the_editable_fields(client, email_outbox, base):
    owner = _signup(client, email_outbox, email="conv-patch@example.com")
    meeting_id = _insert(owner.workspace_id, created_by=owner.user_id)

    changed = owner.patch(
        f"{base}/{meeting_id}",
        json={
            "conversation_type": "meeting",
            "direction": "outbound",
            "started_at": "2026-10-01T09:30:00+00:00",
        },
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["conversation_type"] == "meeting" and body["direction"] == "outbound"
    assert _at(body["started_at"]) == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)
    row = _row(meeting_id)
    assert row.conversation_type == "meeting" and row.direction == "outbound"
    assert row.started_at == datetime(2026, 10, 1, 9, 30, tzinfo=UTC)

    cleared = owner.patch(f"{base}/{meeting_id}", json={"direction": None, "started_at": None})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["direction"] is None and cleared.json()["started_at"] is None
    assert cleared.json()["conversation_type"] == "meeting"
    row = _row(meeting_id)
    assert row.direction is None and row.started_at is None

    assert _updated_fields(meeting_id) == [
        "conversation_type,direction,started_at",
        "direction,started_at",
    ]


@BOTH
def test_patch_audits_each_named_field_next_to_the_old_ones(client, email_outbox, base):
    owner = _signup(client, email_outbox, email="conv-audit@example.com")
    meeting_id = _insert(owner.workspace_id, created_by=owner.user_id)
    response = owner.patch(f"{base}/{meeting_id}", json={"title": "New", "direction": "inbound"})
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "New"
    assert _updated_fields(meeting_id) == ["title,direction"]


@BOTH
@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"conversation_type": "sms"}, "body.conversation_type"),
        ({"conversation_type": "Meeting"}, "body.conversation_type"),
        ({"conversation_type": None}, "body.conversation_type"),
        ({"direction": "sideways"}, "body.direction"),
        ({"started_at": "yesterday"}, "body.started_at"),
        ({"started_at": "2026-10-01T09:30:00"}, "body.started_at"),
        ({"external_conversation_id": "X"}, "body.external_conversation_id"),
        ({"external_source_system": "odoo"}, "body.external_source_system"),
        (
            {"direction": "inbound", "external_conversation_id": "X"},
            "body.external_conversation_id",
        ),
    ],
)
def test_patch_refuses_bad_values_and_the_source_reference(client, email_outbox, base, body, field):
    owner = _signup(client, email_outbox, email="conv-patch-bad@example.com")
    meeting_id = _insert(owner.workspace_id, created_by=owner.user_id, external=("odoo", "C-1"))
    response = owner.patch(f"{base}/{meeting_id}", json=body)
    assert _validation_fields(response) == {field}
    row = _row(meeting_id)
    assert row.conversation_type == "call" and row.direction is None
    assert (row.external_source_system, row.external_conversation_id) == ("odoo", "C-1")
    assert _updated_fields(meeting_id) == []


@BOTH
def test_patch_without_an_updatable_field_keeps_no_changes(client, email_outbox, base):
    owner = _signup(client, email_outbox, email="conv-nochange@example.com")
    meeting_id = _insert(owner.workspace_id, created_by=owner.user_id)
    empty = owner.patch(f"{base}/{meeting_id}", json={})
    assert empty.status_code == 422
    assert _error(empty)["code"] == "validation_error"
    title_null = owner.patch(f"{base}/{meeting_id}", json={"title": None})
    assert title_null.status_code == 422 and _error(title_null)["code"] == "no_changes"
    allowed = owner.patch(f"{base}/{meeting_id}", json={"conversation_type": "call"})
    assert allowed.status_code == 200, allowed.text
    assert _updated_fields(meeting_id) == ["conversation_type"]


def test_patch_needs_meeting_update(client, email_outbox, monkeypatch):
    owner, people = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"viewer": "viewer", "member": "member"}
    )
    meeting_id = _insert(owner.workspace_id, created_by=people["member"].user_id)
    for base in (CONVERSATIONS, MEETINGS):
        denied = people["viewer"].patch(f"{base}/{meeting_id}", json={"direction": "inbound"})
        assert denied.status_code == 403, denied.text
        assert _error(denied)["code"] == "permission_denied"
    assert _row(meeting_id).direction is None
    allowed = people["member"].patch(f"{CONVERSATIONS}/{meeting_id}", json={"direction": "inbound"})
    assert allowed.status_code == 200, allowed.text
    assert _row(meeting_id).direction == "inbound"


def test_patch_of_a_deleted_conversation_is_404(client, email_outbox):
    owner = _signup(client, email_outbox, email="conv-patch-deleted@example.com")
    meeting_id = _insert(owner.workspace_id, created_by=owner.user_id)
    assert owner.get(f"{CONVERSATIONS}/{meeting_id}").status_code == 200
    assert owner.delete(f"{MEETINGS}/{meeting_id}").status_code == 204
    gone = owner.patch(f"{CONVERSATIONS}/{meeting_id}", json={"direction": "inbound"})
    assert gone.status_code == 404 and _error(gone)["code"] == "meeting_not_found"


# --- AC7: counts -------------------------------------------------------------


def test_conversation_counts_mirror_meeting_counts(client, email_outbox):
    owner = _signup(client, email_outbox, email="conv-counts@example.com")
    for status, kind in (
        (MeetingStatus.COMPLETED, "call"),
        (MeetingStatus.COMPLETED, "voicemail"),
        (MeetingStatus.FAILED, "meeting"),
    ):
        _insert(owner.workspace_id, created_by=owner.user_id, status=status, conversation_type=kind)
    meetings = owner.get(f"{MEETINGS}/counts")
    conversations = owner.get(f"{CONVERSATIONS}/counts")
    assert meetings.status_code == conversations.status_code == 200
    assert conversations.json() == meetings.json()
    assert conversations.json()["total"] == 3
    assert conversations.json()["counts"]["completed"] == 2


# --- AC8: write paths ---------------------------------------------------------


@pytest.mark.parametrize(
    ("source_type", "expected"),
    [
        (None, ConversationType.CALL),
        (IngestionSourceType.VOICEMAIL, ConversationType.VOICEMAIL),
        ("voicemail", ConversationType.VOICEMAIL),
        (IngestionSourceType.MEETING_BOT, ConversationType.MEETING),
        ("meeting_bot", ConversationType.MEETING),
        *[
            (member, ConversationType.CALL)
            for member in IngestionSourceType
            if member not in (IngestionSourceType.VOICEMAIL, IngestionSourceType.MEETING_BOT)
        ],
    ],
)
def test_source_type_maps_to_conversation_type(source_type, expected):
    assert conversation_type_for_source(source_type) is expected


def _accept(
    settings,
    tenant: dict,
    *,
    identity: IngestionIdentity,
    content: bytes,
    **create_kwargs,
) -> str:
    """One ingestion through the ledger and ``create_upload_job``; the meeting id."""
    payload = IngestionPayload(
        media_sha256=_sha256(content),
        media_size_bytes=len(content),
        original_filename="ledger.wav",
        title="Ledger call",
        language="fa",
        declared_content_type="audio/wav",
    )
    with get_session_factory()() as db:
        bind_tenant_context(
            db, workspace_id=tenant["workspace_id"], organization_id=tenant["organization_id"]
        )
        bind_user_context(db, tenant["user_id"])
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None

        def create(record: IngestionRecord):
            return create_upload_job(
                db,
                workspace_id=tenant["workspace_id"],
                user_id=tenant["user_id"],
                title="Ledger call",
                language="fa",
                storage_key=f"ledger/{uuid.uuid4()}.wav",
                original_filename="ledger.wav",
                media_type="audio/wav",
                container_format="wav",
                size_bytes=len(content),
                checksum_sha256=_sha256(content),
                duration_seconds=12,
                channel_count=1,
                sample_rate_hz=16_000,
                idempotency_key=scoped_idempotency_key(
                    workspace_id=tenant["workspace_id"],
                    purpose="meeting-upload",
                    client_key=identity.external_event_id,
                    settings=settings,
                ),
                retention_days=workspace.retention_days,
                settings=settings,
                ingestion_id=record.id,
                producer=identity.source_provider,
                source_type=identity.source_type.value,
                **create_kwargs,
            )

        now = datetime.now(UTC)
        outcome = accept_ingestion(
            db,
            workspace=workspace,
            identity=identity,
            payload=payload,
            received_at=now,
            now=now,
            actor_user_id=tenant["user_id"],
            api_key_id=None,
            request_id=None,
            settings=settings,
            create=create,
        )
        meeting_id = outcome.created.meeting.id
        db.commit()
        return meeting_id


def test_ledger_path_copies_the_external_source_id(client, email_outbox, settings):
    tenant = _tenant(client, email_outbox, "conv-ledger")
    with_source = _accept(
        settings,
        tenant,
        identity=IngestionIdentity(
            source_type=IngestionSourceType.SIP_VOIP,
            source_provider="odoo",
            external_event_id="evt-0001",
            external_source_id="C-1",
        ),
        content=CONTENT,
    )
    row = _row(with_source)
    assert (row.external_source_system, row.external_conversation_id) == ("odoo", "C-1")
    assert row.conversation_type == "call"

    without_source = _accept(
        settings,
        tenant,
        identity=IngestionIdentity(
            source_type=IngestionSourceType.MEETING_BOT,
            source_provider="bot",
            external_event_id="evt-0002",
        ),
        content=CONTENT + b"-2",
    )
    row = _row(without_source)
    assert row.external_source_system is None and row.external_conversation_id is None
    assert row.conversation_type == "meeting"


def test_create_upload_job_takes_explicit_conversation_fields(client, email_outbox, settings):
    tenant = _tenant(client, email_outbox, "conv-explicit")
    started = datetime(2026, 8, 3, 7, 0, tzinfo=UTC)
    meeting_id = _accept(
        settings,
        tenant,
        identity=IngestionIdentity(
            source_type=IngestionSourceType.API,
            source_provider="api",
            external_event_id="evt-explicit",
        ),
        content=CONTENT,
        conversation_type="other",
        direction="inbound",
        started_at=started,
        external_source_system="crm",
        external_conversation_id="crm-7",
    )
    row = _row(meeting_id)
    assert row.conversation_type == "other" and row.direction == "inbound"
    assert row.started_at == started
    assert (row.external_source_system, row.external_conversation_id) == ("crm", "crm-7")


def test_create_upload_job_refuses_half_a_source_reference(client, email_outbox, settings):
    tenant = _tenant(client, email_outbox, "conv-half")
    identity = IngestionIdentity(
        source_type=IngestionSourceType.API,
        source_provider="api",
        external_event_id="evt-half",
    )
    with pytest.raises(ValueError, match="external_source_system"):
        _accept(settings, tenant, identity=identity, content=CONTENT, external_conversation_id="x")
    with pytest.raises(ValueError, match="direction"):
        _accept(settings, tenant, identity=identity, content=CONTENT, direction="up")


def test_voicemail_import_is_a_voicemail_conversation(vm_client, email_outbox):
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    _widen_concurrency(workspace_id)
    received = datetime(2026, 10, 20, 14, 5, tzinfo=UTC)
    timed = _vm.import_vm(vm_client, workspace_id, external_id="msg-timed", received_at=received)
    assert timed.status_code == 202, timed.text
    untimed = _vm.import_vm(
        vm_client, workspace_id, external_id="msg-untimed", audio=b"other voicemail bytes"
    )
    assert untimed.status_code == 202, untimed.text

    timed_row = _row(timed.json()["voicemail"]["meeting_id"])
    assert timed_row.conversation_type == "voicemail" and timed_row.source == "voicemail"
    assert timed_row.started_at == received
    untimed_row = _row(untimed.json()["voicemail"]["meeting_id"])
    assert untimed_row.conversation_type == "voicemail"
    assert untimed_row.started_at is None
    for row in (timed_row, untimed_row):
        assert row.external_source_system is None and row.external_conversation_id is None

    listed = vm_client.get(
        CONVERSATIONS,
        params={"conversation_type": "voicemail"},
        headers=_vm.headers(vm_client, workspace_id, write=False),
    )
    assert listed.status_code == 200, listed.text
    assert {item["id"] for item in listed.json()["items"]} == {timed_row.id, untimed_row.id}


# --- AC9: purge keeps the metadata --------------------------------------------


def test_purge_keeps_the_conversation_metadata_and_clears_the_title(client, email_outbox):
    owner = _signup(client, email_outbox, email="conv-purge@example.com")
    started = datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    meeting_id = _insert(
        owner.workspace_id,
        created_by=owner.user_id,
        title="Contract call with a named customer",
        conversation_type="meeting",
        direction="outbound",
        external=("odoo", "C-9"),
        started_at=started,
    )
    assert owner.delete(f"{MEETINGS}/{meeting_id}").status_code == 204

    with inspection_session_factory()() as db:
        purge = db.scalar(
            select(ConversationPurge).where(ConversationPurge.meeting_id == meeting_id)
        )
        assert purge is not None and purge.state is PurgeState.COMPLETED
    row = _row(meeting_id)
    assert row.deleted_at is not None and row.title == ""
    assert row.conversation_type == "meeting" and row.direction == "outbound"
    assert (row.external_source_system, row.external_conversation_id) == ("odoo", "C-9")
    assert row.started_at == started
    assert row.duration_seconds == 30 and row.source == "upload"
