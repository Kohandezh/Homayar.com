"""WP05-T01: the canonical ingestion ledger (ADR 0016).

Every accepted capture event is one ``IngestionRecord`` identified by
``(workspace, source_type, external_event_id)``.  These tests prove the three
decisions of the contract end to end (replay, conflict, duplicate media under
the workspace policy), the immutability of accepted provenance, tenant and
record scoping of the ledger reads, API-key provenance, and — on PostgreSQL —
row-level security, real concurrent claims and the migration backfill.
"""

from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    DuplicateMediaPolicy,
    Entitlement,
    IngestionImmutableError,
    IngestionRecord,
    IngestionSourceType,
    IngestionStatus,
    MediaObject,
    Meeting,
    ProcessingJob,
    Recording,
    UsageReservation,
    User,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context, bind_user_context
from backend.jobs.invariants import check_workspace
from backend.security.context import CSRF_COOKIE
from backend.security.idempotency import scoped_idempotency_key
from backend.services.ingestion_service import (
    PROVIDER_CONSOLE,
    IngestionDecision,
    IngestionError,
    IngestionIdentity,
    IngestionPayload,
    accept_ingestion,
    canonical_payload,
    find_ingestion,
    payload_digest,
    resolve_existing,
)
from backend.services.meeting_service import create_upload_job
from backend.storage import create_object_storage
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _cookies, _workspace_with_roles
from backend.tests.test_uploads_api import _valid_metadata

# PostgreSQL probe role: the fixture is discovered from this module namespace.
probe_role = _rls.probe_role

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTENT = b"staged wav bytes"
OTHER_CONTENT = b"other staged bytes"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _patch_media(monkeypatch) -> list[str]:
    queued: list[str] = []
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=12.4),
    )
    monkeypatch.setattr(
        "backend.routers.v1.meetings.dispatch_job",
        lambda job_id, _settings: queued.append(job_id) or True,
    )
    return queued


def _widen_concurrency(workspace_id: str, limit: int = 10) -> None:
    """The free demo allows one queued job at a time; these tests queue several."""
    with inspection_session_factory()() as db:
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id,
                Entitlement.metric == "concurrent_jobs",
            )
        )
        assert entitlement is not None
        entitlement.limit_value = limit
        db.commit()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(
        client, email_outbox, email=f"ledger-{label}@example.com", name=f"Ledger {label}"
    )
    session = login_user(client, credentials)
    _widen_concurrency(session["workspaces"][0]["id"])
    return {
        "user_id": session["user"]["id"],
        "workspace_id": session["workspaces"][0]["id"],
        "organization_id": session["workspaces"][0]["organization_id"],
        "cookies": _cookies(client),
        "credentials": credentials,
    }


def _headers(tenant: dict, **extra: str) -> dict[str, str]:
    return {
        "X-Workspace-ID": tenant["workspace_id"],
        "X-CSRF-Token": tenant["cookies"][CSRF_COOKIE],
        **extra,
    }


def _upload(
    client,
    tenant: dict,
    *,
    key: str,
    content: bytes = CONTENT,
    title: str | None = "Weekly customer call",
    filename: str = "customer-call.wav",
    language: str = "fa",
):
    data = {"language": language}
    if title is not None:
        data["title"] = title
    return client.post(
        "/api/v1/uploads",
        headers=_headers(tenant, **{"Idempotency-Key": key}),
        cookies=tenant["cookies"],
        data=data,
        files={"file": (filename, content, "audio/wav")},
    )


def _get(client, tenant: dict, path: str, **params):
    return client.get(path, headers=_headers(tenant), cookies=tenant["cookies"], params=params)


def _counts() -> dict[str, int]:
    with inspection_session_factory()() as db:
        return {
            "meetings": db.scalar(select(func.count(Meeting.id))),
            "recordings": db.scalar(select(func.count(Recording.id))),
            "jobs": db.scalar(select(func.count(ProcessingJob.id))),
            "ingestions": db.scalar(select(func.count(IngestionRecord.id))),
        }


def _audits(event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == event_type)
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


# --- identity and payload contract ----------------------------------------------------


def test_identity_and_payload_are_validated_and_hashed_canonically():
    with pytest.raises(ValueError, match="source_type"):
        IngestionIdentity("file_upload", "console", "evt")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="source_provider"):
        IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "Console!", "evt")
    with pytest.raises(ValueError, match="external_event_id"):
        IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "console", "has space")
    with pytest.raises(ValueError, match="external_source_id"):
        IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "console", "evt", "bad id")
    with pytest.raises(ValueError, match="media_sha256"):
        IngestionPayload("abc", 1, None, None, "fa")
    with pytest.raises(ValueError, match="media_size_bytes"):
        IngestionPayload("0" * 64, 0, None, None, "fa")
    with pytest.raises(ValueError, match="extra"):
        IngestionPayload("0" * 64, 1, None, None, "fa", extra={"k": 1})  # type: ignore[dict-item]

    identity = IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "console", "evt-1")
    base = IngestionPayload(_sha256(CONTENT), 16, "a.wav", "Title", "fa", "audio/wav")
    relabelled = IngestionPayload(_sha256(CONTENT), 16, "a.wav", "Title", "fa", "audio/x-wav")
    retitled = IngestionPayload(_sha256(CONTENT), 16, "a.wav", "Other", "fa", "audio/wav")
    assert payload_digest(identity, base) == payload_digest(identity, relabelled)
    assert payload_digest(identity, base) != payload_digest(identity, retitled)
    assert payload_digest(identity, base) != payload_digest(
        IngestionIdentity(IngestionSourceType.API, "api", "evt-1"), base
    )
    with_extra = IngestionPayload(
        _sha256(CONTENT), 16, None, None, "fa", extra={"b": "2", "a": "1"}
    )
    assert canonical_payload(identity, with_extra) == (
        b'{"external_source_id":null,"extra":{"a":"1","b":"2"},"language":"fa",'
        b'"media_sha256":"' + _sha256(CONTENT).encode() + b'","media_size_bytes":16,'
        b'"original_filename":null,"source_type":"file_upload","title":null}'
    )


# --- cases A and B through the upload route -------------------------------------------


def test_upload_records_the_event_and_replays_or_conflicts_deterministically(
    client, email_outbox, monkeypatch
):
    queued = _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "one")

    first = _upload(client, tenant, key="event-0001")
    assert first.status_code == 202, first.text
    body = first.json()
    ingestion = body["ingestion"]
    assert ingestion["source_type"] == "file_upload"
    assert ingestion["source_provider"] == PROVIDER_CONSOLE
    assert ingestion["external_event_id"] == "event-0001"
    assert ingestion["external_source_id"] is None
    assert ingestion["status"] == "accepted"
    assert ingestion["reason"] is None
    assert ingestion["media_hash"] == _sha256(CONTENT)
    assert ingestion["media_size_bytes"] == len(CONTENT)
    assert ingestion["declared_content_type"] == "audio/wav"
    assert ingestion["original_filename"] == "customer-call.wav"
    assert ingestion["meeting_id"] == body["meeting"]["id"]
    assert ingestion["job_id"] == body["job"]["id"]
    assert ingestion["created_by_user_id"] == tenant["user_id"]
    assert ingestion["api_key_id"] is None
    assert ingestion["duplicate_of_id"] is None
    assert ingestion["conflict_count"] == 0 and ingestion["last_conflict_at"] is None
    assert ingestion["workspace_id"] == tenant["workspace_id"]
    assert ingestion["organization_id"] == tenant["organization_id"]
    assert len(ingestion["payload_hash"]) == 64
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    assert len(queued) == 1

    accepted = _audits("ingestion.accepted")
    assert len(accepted) == 1
    assert accepted[0].target_id == ingestion["id"]
    assert accepted[0].details["meeting_id"] == body["meeting"]["id"]
    assert accepted[0].details["job_id"] == body["job"]["id"]
    assert accepted[0].details["media_size_bytes"] == len(CONTENT)
    assert "payload" not in accepted[0].details and "media" not in accepted[0].details
    uploaded = _audits("meeting.uploaded")
    assert uploaded[0].details["ingestion_id"] == ingestion["id"]

    # Case A: same identity, same payload -> the existing ingestion, nothing new.
    replay = _upload(client, tenant, key="event-0001")
    assert replay.status_code == 202, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["job"]["id"] == body["job"]["id"]
    assert replay.json()["ingestion"]["id"] == ingestion["id"]
    assert replay.json()["ingestion"]["conflict_count"] == 0
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    assert len(queued) == 1
    replayed = _audits("ingestion.replayed")
    assert len(replayed) == 1 and replayed[0].target_id == ingestion["id"]

    # Case B: same identity, different media -> conflict; the first event stands.
    conflict = _upload(client, tenant, key="event-0001", content=OTHER_CONTENT)
    assert conflict.status_code == 409, conflict.text
    error = conflict.json()["error"]
    assert error["code"] == "ingestion_conflict"
    assert error["details"] == {"ingestion_id": ingestion["id"], "conflict_count": 1}
    # ...and a changed title is a different payload too.
    retitled = _upload(client, tenant, key="event-0001", title="Renamed call")
    assert retitled.status_code == 409
    assert retitled.json()["error"]["details"]["conflict_count"] == 2
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    assert len(queued) == 1
    conflicts = _audits("ingestion.conflict")
    assert [event.outcome for event in conflicts] == ["rejected", "rejected"]
    assert conflicts[-1].details["conflict_count"] == 2
    assert conflicts[-1].details["ingestion_id"] == ingestion["id"]
    assert "payload_hash" not in conflicts[-1].details
    with inspection_session_factory()() as db:
        record = db.get(IngestionRecord, ingestion["id"])
        assert record is not None
        assert record.conflict_count == 2 and record.last_conflict_at is not None
        assert record.media_hash == _sha256(CONTENT)  # never overwritten
        assert record.payload_hash == ingestion["payload_hash"]

    # The original payload still replays after conflicts were recorded.
    again = _upload(client, tenant, key="event-0001")
    assert again.status_code == 202 and again.json()["idempotent_replay"] is True
    assert again.json()["ingestion"]["conflict_count"] == 2

    # Malformed event ids never reach the ledger.
    bad = _upload(client, tenant, key="bad key with spaces")
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_idempotency_key"


def test_replay_of_a_deleted_conversation_is_gone_and_its_media_is_free_again(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "deleter")
    first = _upload(client, tenant, key="event-del-1")
    assert first.status_code == 202, first.text
    meeting_id = first.json()["meeting"]["id"]
    ingestion_id = first.json()["ingestion"]["id"]

    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204, deleted.text

    gone = _upload(client, tenant, key="event-del-1")
    assert gone.status_code == 410, gone.text
    assert gone.json()["error"]["code"] == "ingestion_target_deleted"
    assert gone.json()["error"]["details"]["ingestion_id"] == ingestion_id
    # The ledger keeps the provenance of the deleted conversation.
    with inspection_session_factory()() as db:
        record = db.get(IngestionRecord, ingestion_id)
        assert record is not None and record.meeting_id == meeting_id
    assert _counts()["ingestions"] == 1

    # A new event with the same media is not a duplicate of a deleted conversation.
    fresh = _upload(client, tenant, key="event-del-2")
    assert fresh.status_code == 202, fresh.text
    assert fresh.json()["ingestion"]["duplicate_of_id"] is None
    assert fresh.json()["ingestion"]["media_hash"] == _sha256(CONTENT)


# --- case C: workspace duplicate-media policy -----------------------------------------


def test_duplicate_media_follows_the_workspace_policy(client, email_outbox, monkeypatch):
    queued = _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "policy")

    first = _upload(client, tenant, key="event-c-1")
    assert first.status_code == 202, first.text
    first_id = first.json()["ingestion"]["id"]

    # allow (default): accepted and linked to the earliest live ingestion of the media
    linked = _upload(client, tenant, key="event-c-2", title="Same audio again")
    assert linked.status_code == 202, linked.text
    assert linked.json()["idempotent_replay"] is False
    assert linked.json()["ingestion"]["duplicate_of_id"] == first_id
    assert linked.json()["meeting"]["id"] != first.json()["meeting"]["id"]
    assert _counts() == {"meetings": 2, "recordings": 2, "jobs": 2, "ingestions": 2}
    link_audits = _audits("ingestion.duplicate_media")
    assert [event.outcome for event in link_audits] == ["linked"]
    assert link_audits[0].details["duplicate_of_id"] == first_id
    assert link_audits[0].details["policy"] == "allow"

    # The policy is a server-side, audited workspace setting.
    invalid = client.patch(
        "/api/v1/admin/workspace-settings",
        json={"duplicate_media_policy": "maybe"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert invalid.status_code == 422
    switched = client.patch(
        "/api/v1/admin/workspace-settings",
        json={"duplicate_media_policy": "reject"},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["duplicate_media_policy"] == "reject"
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        assert workspace.duplicate_media_policy is DuplicateMediaPolicy.REJECT

    # reject: refused, nothing created, the decision audited with ids only
    refused = _upload(client, tenant, key="event-c-3")
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "duplicate_media"
    assert error["details"] == {
        "ingestion_id": first_id,
        "meeting_id": first.json()["meeting"]["id"],
    }
    assert _counts() == {"meetings": 2, "recordings": 2, "jobs": 2, "ingestions": 2}
    assert len(queued) == 2
    rejected = _audits("ingestion.duplicate_media")[-1]
    assert rejected.outcome == "rejected"
    assert rejected.details["policy"] == "reject"
    assert rejected.details["duplicate_of_id"] == first_id
    assert rejected.details["external_event_id"] == "event-c-3"
    # No ledger row was written for the refused event (rejections are recorded
    # from WP05-T02 on), so the decision is re-evaluated deterministically.
    assert _upload(client, tenant, key="event-c-3").status_code == 409
    with inspection_session_factory()() as db:
        assert (
            db.scalar(
                select(func.count(IngestionRecord.id)).where(
                    IngestionRecord.external_event_id == "event-c-3"
                )
            )
            == 0
        )

    # Different media is still accepted under reject; a replay of an accepted
    # duplicate keeps replaying.
    other = _upload(client, tenant, key="event-c-4", content=OTHER_CONTENT)
    assert other.status_code == 202, other.text
    assert other.json()["ingestion"]["duplicate_of_id"] is None
    replay = _upload(client, tenant, key="event-c-2", title="Same audio again")
    assert replay.status_code == 202 and replay.json()["idempotent_replay"] is True
    assert _counts() == {"meetings": 3, "recordings": 3, "jobs": 3, "ingestions": 3}


# --- reads: tenant scope, record scope, filters ---------------------------------------


def test_ledger_reads_are_tenant_scoped_and_event_ids_are_independent_per_workspace(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    one = _tenant(client, email_outbox, "reader-one")
    two = _tenant(client, email_outbox, "reader-two")

    first = _upload(client, one, key="shared-event-id")
    second = _upload(client, one, key="only-in-one", content=OTHER_CONTENT)
    foreign = _upload(client, two, key="shared-event-id", content=OTHER_CONTENT)
    assert first.status_code == second.status_code == foreign.status_code == 202
    assert foreign.json()["idempotent_replay"] is False  # no cross-workspace identity
    first_id = first.json()["ingestion"]["id"]
    second_id = second.json()["ingestion"]["id"]
    foreign_id = foreign.json()["ingestion"]["id"]
    assert _counts()["ingestions"] == 3

    listed = _get(client, one, "/api/v1/ingestions")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [second_id, first_id]
    assert listed.json()["next_before"] is None
    assert {item["workspace_id"] for item in listed.json()["items"]} == {one["workspace_id"]}

    by_event = _get(client, one, "/api/v1/ingestions", external_event_id="shared-event-id")
    assert [item["id"] for item in by_event.json()["items"]] == [first_id]
    by_meeting = _get(client, one, "/api/v1/ingestions", meeting_id=second.json()["meeting"]["id"])
    assert [item["id"] for item in by_meeting.json()["items"]] == [second_id]
    by_type = _get(client, one, "/api/v1/ingestions", source_type="file_upload")
    assert len(by_type.json()["items"]) == 2
    assert _get(client, one, "/api/v1/ingestions", source_type="microphone").json()["items"] == []
    assert _get(client, one, "/api/v1/ingestions", source_type="telepathy").status_code == 422

    page = _get(client, one, "/api/v1/ingestions", limit=1)
    assert [item["id"] for item in page.json()["items"]] == [second_id]
    assert page.json()["next_before"] is not None
    rest = _get(client, one, "/api/v1/ingestions", limit=1, before=page.json()["next_before"])
    assert [item["id"] for item in rest.json()["items"]] == [first_id]

    detail = _get(client, one, f"/api/v1/ingestions/{first_id}")
    assert detail.status_code == 200 and detail.json()["external_event_id"] == "shared-event-id"
    meeting_detail = _get(client, one, f"/api/v1/meetings/{first.json()['meeting']['id']}")
    assert meeting_detail.status_code == 200
    assert meeting_detail.json()["ingestion"]["id"] == first_id

    # Cross-tenant: the other workspace's row is invisible with our own header
    # and the victim's header is refused before any lookup.
    assert _get(client, one, f"/api/v1/ingestions/{foreign_id}").status_code == 404
    assert (
        _get(client, one, f"/api/v1/ingestions/{foreign_id}").json()["error"]["code"]
        == "ingestion_not_found"
    )
    hijack = client.get(
        f"/api/v1/ingestions/{foreign_id}",
        headers={"X-Workspace-ID": two["workspace_id"]},
        cookies=one["cookies"],
    )
    assert hijack.status_code in {403, 404}
    hijack_list = client.get(
        "/api/v1/ingestions",
        headers={"X-Workspace-ID": two["workspace_id"]},
        cookies=one["cookies"],
    )
    assert hijack_list.status_code in {403, 404}
    assert [item["id"] for item in _get(client, two, "/api/v1/ingestions").json()["items"]] == [
        foreign_id
    ]


def test_team_record_scope_limits_ledger_visibility(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"sup": "member", "agent": "member", "lone": "member"},
    )
    sup, agent, lone = people["sup"], people["agent"], people["lone"]
    _widen_concurrency(owner.workspace_id)
    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200, switched.text
    team = owner.post("/api/v1/teams", json={"name": "Team A"}).json()["id"]
    for persona, role in ((sup, "supervisor"), (agent, "agent")):
        assigned = owner.post(
            f"/api/v1/teams/{team}/assignments", json={"user_id": persona.user_id, "role": role}
        )
        assert assigned.status_code == 201, assigned.text

    def persona_tenant(persona) -> dict:
        return {"workspace_id": persona.workspace_id, "cookies": persona.cookies}

    team_upload = _upload(client, persona_tenant(owner), key="team-event")
    private_upload = _upload(
        client, persona_tenant(owner), key="private-event", content=OTHER_CONTENT
    )
    agent_upload = _upload(client, persona_tenant(agent), key="agent-event", content=b"agent audio")
    assert team_upload.status_code == private_upload.status_code == agent_upload.status_code == 202
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, team_upload.json()["meeting"]["id"])
        assert meeting is not None
        meeting.team_id = team
        db.commit()

    def visible(persona) -> set[str]:
        response = persona.get("/api/v1/ingestions")
        assert response.status_code == 200, response.text
        return {item["external_event_id"] for item in response.json()["items"]}

    assert visible(owner) == {"team-event", "private-event", "agent-event"}
    assert visible(sup) == {"team-event"}  # supervised team, not the owner's private upload
    assert visible(agent) == {"agent-event"}  # own submission only (agents do not supervise)
    assert visible(lone) == set()
    team_id = team_upload.json()["ingestion"]["id"]
    assert sup.get(f"/api/v1/ingestions/{team_id}").status_code == 200
    assert lone.get(f"/api/v1/ingestions/{team_id}").status_code == 404
    private_id = private_upload.json()["ingestion"]["id"]
    assert sup.get(f"/api/v1/ingestions/{private_id}").status_code == 404
    assert agent.get(f"/api/v1/ingestions/{private_id}").status_code == 404


def test_api_key_uploads_carry_key_provenance_and_share_the_identity(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "keys")
    minted = _mint(
        client,
        _headers(tenant),
        tenant["cookies"],
        scopes=["meeting:read", "meeting:create"],
    )
    assert minted.status_code == 201, minted.text
    raw_key, key_id = minted.json()["key"], minted.json()["id"]
    read_only = _mint(
        client, _headers(tenant), tenant["cookies"], name="ro", scopes=["meeting:read"]
    )
    create_only = _mint(
        client, _headers(tenant), tenant["cookies"], name="co", scopes=["meeting:create"]
    )
    assert read_only.status_code == 201 and create_only.status_code == 201

    accepted = client.post(
        "/api/v1/uploads",
        headers=_bearer(raw_key, **{"Idempotency-Key": "key-event-1"}),
        data={"title": "Weekly customer call", "language": "fa"},
        files={"file": ("customer-call.wav", CONTENT, "audio/wav")},
    )
    assert accepted.status_code == 202, accepted.text
    ingestion = accepted.json()["ingestion"]
    assert ingestion["source_provider"] == "api"
    assert ingestion["api_key_id"] == key_id
    assert ingestion["created_by_user_id"] == tenant["user_id"]

    # The identity is (workspace, source type, event id): the console replays
    # the key's event and the key replays a console event.
    console_replay = _upload(client, tenant, key="key-event-1")
    assert console_replay.status_code == 202, console_replay.text
    assert console_replay.json()["idempotent_replay"] is True
    assert console_replay.json()["ingestion"]["id"] == ingestion["id"]
    assert console_replay.json()["ingestion"]["source_provider"] == "api"
    assert _counts()["ingestions"] == 1

    listed = client.get("/api/v1/ingestions", headers=_bearer(read_only.json()["key"]))
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [ingestion["id"]]
    detail = client.get(
        f"/api/v1/ingestions/{ingestion['id']}", headers=_bearer(read_only.json()["key"])
    )
    assert detail.status_code == 200 and detail.json()["api_key_id"] == key_id
    denied = client.get("/api/v1/ingestions", headers=_bearer(create_only.json()["key"]))
    assert denied.status_code == 403, denied.text


# --- model: immutability and constraints ----------------------------------------------


def test_accepted_ingestion_rows_are_immutable_except_their_links(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "immutable")
    accepted = _upload(client, tenant, key="event-immutable")
    assert accepted.status_code == 202, accepted.text
    ingestion_id = accepted.json()["ingestion"]["id"]

    factory = inspection_session_factory()
    for column, value in (
        ("payload_hash", "0" * 64),
        ("media_hash", "1" * 64),
        ("external_event_id", "rewritten"),
        ("source_type", IngestionSourceType.API),
        ("source_provider", "elsewhere"),
        ("workspace_id", str(uuid.uuid4())),
        ("status", IngestionStatus.REJECTED),
        ("received_at", datetime.now(UTC)),
        ("created_by_user_id", None),
    ):
        with factory() as db:
            record = db.get(IngestionRecord, ingestion_id)
            assert record is not None
            setattr(record, column, value)
            with pytest.raises(IngestionImmutableError, match=column):
                db.flush()
            db.rollback()

    with factory() as db:
        record = db.get(IngestionRecord, ingestion_id)
        assert record is not None
        record.conflict_count += 1
        record.last_conflict_at = datetime.now(UTC)
        db.commit()
        db.refresh(record)
        assert record.conflict_count == 1
        assert record.payload_hash == accepted.json()["ingestion"]["payload_hash"]

    with factory() as db:
        record = db.get(IngestionRecord, ingestion_id)
        assert record is not None
        # Identity uniqueness is a database constraint, not an application check.
        db.add(
            IngestionRecord(
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                source_type=record.source_type,
                source_provider="console",
                external_event_id=record.external_event_id,
                payload_hash="2" * 64,
                status=IngestionStatus.ACCEPTED,
                received_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
    with factory() as db:
        record = db.get(IngestionRecord, ingestion_id)
        assert record is not None
        # An accepted row cannot carry a rejection reason (check constraint).
        db.add(
            IngestionRecord(
                organization_id=record.organization_id,
                workspace_id=record.workspace_id,
                source_type=IngestionSourceType.MICROPHONE,
                source_provider="desktop",
                external_event_id="mic-1",
                payload_hash="3" * 64,
                status=IngestionStatus.ACCEPTED,
                reason="not_allowed_here",
                received_at=datetime.now(UTC),
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()


# --- PostgreSQL: row-level security ---------------------------------------------------


@pytest.mark.postgres
def test_rls_binds_ledger_rows_to_the_tenant(client, email_outbox, monkeypatch, probe_role):
    _patch_media(monkeypatch)
    one = _tenant(client, email_outbox, "rls-one")
    two = _tenant(client, email_outbox, "rls-two")
    assert _upload(client, one, key="rls-event").status_code == 202
    assert _upload(client, two, key="rls-event", content=OTHER_CONTENT).status_code == 202

    with inspection_session_factory()() as db:
        forced = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname = 'ingestion_records'"
            )
        ).one()
        assert tuple(forced) == (True, True)
        policies = (
            db.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = 'ingestion_records'")
            )
            .scalars()
            .all()
        )
        assert len(policies) == 1

    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            assert _rls._count(connection, "ingestion_records") == 0
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text(
                        "INSERT INTO ingestion_records (id, organization_id, workspace_id, "
                        "source_type, source_provider, external_event_id, payload_hash, status, "
                        "received_at, created_at, conflict_count) VALUES ('probe-1', :org, :ws, "
                        "'API', 'probe', 'probe-event', :digest, 'ACCEPTED', now(), now(), 0)"
                    ),
                    {"org": one["organization_id"], "ws": one["workspace_id"], "digest": "4" * 64},
                )
            connection.rollback()

            _rls._bind(connection, workspace_id=one["workspace_id"])
            rows = connection.execute(
                text("SELECT workspace_id, external_event_id FROM ingestion_records")
            ).all()
            assert rows == [(one["workspace_id"], "rls-event")]
            touched = connection.execute(
                text("UPDATE ingestion_records SET conflict_count = 9 WHERE workspace_id = :ws"),
                {"ws": two["workspace_id"]},
            )
            assert touched.rowcount == 0
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text("UPDATE ingestion_records SET workspace_id = :other"),
                    {"other": two["workspace_id"]},
                )
            connection.rollback()

            _rls._bind(connection, workspace_id=one["workspace_id"])
            inserted = connection.execute(
                text(
                    "INSERT INTO ingestion_records (id, organization_id, workspace_id, "
                    "source_type, source_provider, external_event_id, payload_hash, status, "
                    "received_at, created_at, conflict_count) VALUES ('probe-2', :org, :ws, "
                    "'API', 'probe', 'probe-event', :digest, 'ACCEPTED', now(), now(), 0)"
                ),
                {"org": one["organization_id"], "ws": one["workspace_id"], "digest": "4" * 64},
            )
            assert inserted.rowcount == 1
            connection.rollback()
            assert _rls._count(connection, "ingestion_records") == 0  # context died with it
    finally:
        engine.dispose()
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(IngestionRecord.id))) == 2


# --- PostgreSQL: concurrent claims of one identity ------------------------------------


@pytest.mark.postgres
def test_concurrent_claims_produce_one_ingestion_and_one_conversation(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race")
    factory = get_session_factory()

    def payload_for(title: str, content: bytes = CONTENT) -> IngestionPayload:
        return IngestionPayload(
            media_sha256=_sha256(content),
            media_size_bytes=len(content),
            original_filename="race.wav",
            title=title,
            language="fa",
            declared_content_type="audio/wav",
        )

    def claim(identity: IngestionIdentity, payload: IngestionPayload, barrier, results):
        scoped = scoped_idempotency_key(
            workspace_id=tenant["workspace_id"],
            purpose="meeting-upload",
            client_key=identity.external_event_id,
            settings=settings,
        )
        try:
            with factory() as db:
                bind_tenant_context(
                    db,
                    workspace_id=tenant["workspace_id"],
                    organization_id=tenant["organization_id"],
                )
                bind_user_context(db, tenant["user_id"])
                workspace = db.get(Workspace, tenant["workspace_id"])
                assert workspace is not None

                def create(_record: IngestionRecord):
                    return create_upload_job(
                        db,
                        workspace_id=tenant["workspace_id"],
                        user_id=tenant["user_id"],
                        title=payload.title or "race",
                        language="fa",
                        storage_key=f"race/{uuid.uuid4()}.wav",
                        original_filename="race.wav",
                        media_type="audio/wav",
                        container_format="wav",
                        size_bytes=payload.media_size_bytes,
                        checksum_sha256=payload.media_sha256,
                        duration_seconds=12,
                        channel_count=1,
                        sample_rate_hz=16_000,
                        idempotency_key=scoped,
                        retention_days=workspace.retention_days,
                        settings=settings,
                    )

                barrier.wait(timeout=10)
                now = datetime.now(UTC)
                try:
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
                    record_id = outcome.record.id
                    db.commit()
                    results.append((outcome.decision, record_id))
                except IngestionError as exc:
                    assert exc.ingestion is not None
                    duplicate_id = exc.ingestion.id
                    db.commit()
                    results.append((IngestionDecision.DUPLICATE_REJECTED, duplicate_id))
                except IntegrityError:
                    db.rollback()
                    existing = find_ingestion(
                        db, workspace_id=tenant["workspace_id"], identity=identity
                    )
                    assert existing is not None
                    outcome = resolve_existing(
                        db,
                        record=existing,
                        payload_hash=payload_digest(identity, payload),
                        actor_user_id=tenant["user_id"],
                        request_id=None,
                        now=datetime.now(UTC),
                        settings=settings,
                    )
                    record_id = outcome.record.id
                    db.commit()
                    results.append((outcome.decision, record_id))
        except BaseException as exc:  # surfaced by the assertions below
            results.append(("error", repr(exc)))

    def race(claims) -> list:
        barrier = threading.Barrier(len(claims))
        results: list = []
        threads = [
            threading.Thread(target=claim, args=(identity, payload, barrier, results))
            for identity, payload in claims
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert all(not thread.is_alive() for thread in threads), "a claim hung"
        assert not [r for r in results if r[0] == "error"], results
        return results

    def counts_for(event_id: str) -> tuple[int, int, int]:
        with inspection_session_factory()() as db:
            records = list(
                db.scalars(
                    select(IngestionRecord).where(IngestionRecord.external_event_id == event_id)
                )
            )
            meetings = db.scalar(
                select(func.count(Meeting.id)).where(
                    Meeting.id.in_([r.meeting_id for r in records if r.meeting_id])
                )
            )
            jobs = db.scalar(
                select(func.count(ProcessingJob.id)).where(
                    ProcessingJob.id.in_([r.job_id for r in records if r.job_id])
                )
            )
            return len(records), meetings, jobs

    # Two identical events at once: one identity, one conversation, one root job.
    same = IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "console", "race-same")
    results = race([(same, payload_for("Race")), (same, payload_for("Race"))])
    assert sorted(decision for decision, _ in results) == [
        IngestionDecision.ACCEPTED,
        IngestionDecision.REPLAYED,
    ]
    assert len({record_id for _, record_id in results}) == 1
    assert counts_for("race-same") == (1, 1, 1)

    # Two conflicting payloads at once: one accepted, the other is a recorded conflict.
    clash = IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "console", "race-clash")
    results = race([(clash, payload_for("First")), (clash, payload_for("Second"))])
    assert sorted(decision for decision, _ in results) == [
        IngestionDecision.ACCEPTED,
        IngestionDecision.CONFLICT,
    ]
    assert counts_for("race-clash") == (1, 1, 1)
    with inspection_session_factory()() as db:
        record = db.scalar(
            select(IngestionRecord).where(IngestionRecord.external_event_id == "race-clash")
        )
        assert record is not None and record.conflict_count == 1

    # Under the reject policy, two different events carrying the same media at
    # once: the workspace lock serialises them and exactly one is accepted.
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        workspace.duplicate_media_policy = DuplicateMediaPolicy.REJECT
        db.commit()
    dup_a = IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "console", "race-dup-a")
    dup_b = IngestionIdentity(IngestionSourceType.FILE_UPLOAD, "console", "race-dup-b")
    fresh = b"never seen media"
    results = race([(dup_a, payload_for("Dup", fresh)), (dup_b, payload_for("Dup", fresh))])
    assert sorted(decision for decision, _ in results) == [
        IngestionDecision.ACCEPTED,
        IngestionDecision.DUPLICATE_REJECTED,
    ]
    with inspection_session_factory()() as db:
        rows = list(
            db.scalars(select(IngestionRecord).where(IngestionRecord.media_hash == _sha256(fresh)))
        )
        assert len(rows) == 1 and rows[0].meeting_id is not None
        assert {record_id for _, record_id in results} == {rows[0].id}


# --- PostgreSQL: migration backfill and lossy downgrade -------------------------------


@pytest.mark.postgres
def test_migration_backfills_legacy_uploads_and_downgrade_is_lossy(
    client, email_outbox, monkeypatch, settings
):
    from alembic import command
    from alembic.config import Config

    from backend.config import reset_settings_cache

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "migration")
    original = _upload(client, tenant, key="legacy-event")
    assert original.status_code == 202, original.text
    ledger = original.json()["ingestion"]
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, ledger["job_id"])
        assert job is not None
        scoped_key = job.idempotency_key

    monkeypatch.setenv("DATABASE_URL", settings.database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    try:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        command.stamp(config, "head")
        command.downgrade(config, "e6a9c3d7f2b5")
        with inspection_session_factory()() as db:
            assert db.execute(text("SELECT to_regclass('ingestion_records')")).scalar() is None
            columns = (
                db.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'workspaces'"
                    )
                )
                .scalars()
                .all()
            )
            assert "duplicate_media_policy" not in columns
            assert db.scalar(select(func.count(Meeting.id))) == 1  # conversations survive
        command.upgrade(config, "head")
    finally:
        reset_settings_cache()

    with inspection_session_factory()() as db:
        rows = list(db.scalars(select(IngestionRecord)))
        assert len(rows) == 1
        restored = rows[0]
        assert restored.id != ledger["id"]
        assert restored.workspace_id == tenant["workspace_id"]
        assert restored.organization_id == tenant["organization_id"]
        assert restored.meeting_id == ledger["meeting_id"]
        assert restored.job_id == ledger["job_id"]
        assert restored.source_type is IngestionSourceType.FILE_UPLOAD
        assert restored.source_provider == "backfill"
        assert restored.external_event_id == scoped_key  # the only identity the job had
        assert restored.media_hash == ledger["media_hash"]
        assert restored.media_size_bytes == ledger["media_size_bytes"]
        assert restored.payload_hash == ledger["payload_hash"]  # same canonical form
        assert restored.status is IngestionStatus.ACCEPTED
        assert restored.created_by_user_id == tenant["user_id"]
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        assert workspace.duplicate_media_policy is DuplicateMediaPolicy.ALLOW
        forced = db.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'ingestion_records'")
        ).scalar()
        assert forced is True

    # The pre-ledger event id replays through the job's peppered key and now
    # answers with the backfilled ledger row; a changed payload conflicts.
    replay = _upload(client, tenant, key="legacy-event")
    assert replay.status_code == 202, replay.text
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["job"]["id"] == ledger["job_id"]
    assert replay.json()["ingestion"]["source_provider"] == "backfill"
    assert replay.json()["ingestion"]["external_event_id"] == scoped_key
    changed = _upload(client, tenant, key="legacy-event", content=OTHER_CONTENT)
    assert changed.status_code == 409 and changed.json()["error"]["code"] == "ingestion_conflict"
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(User.id))) >= 1


# --- WP05-T02: rejections in the ledger ---------------------------------------------


def test_terminal_rejections_are_ledger_rows_and_transient_ones_are_not(
    client, email_outbox, monkeypatch
):
    from backend.media import MediaDecodeError, MediaProbeTimeoutError, UnsupportedCodecError

    queued = _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "rejections")
    bad_bytes = b"bytes with an unsupported codec"
    inspections: list[str] = []

    def refuse_codec(*_args, **_kwargs):
        inspections.append("codec")
        raise UnsupportedCodecError("audio")

    monkeypatch.setattr("backend.routers.v1.meetings.validate_media_upload", refuse_codec)
    refused = _upload(client, tenant, key="bad-codec", content=bad_bytes)
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "unsupported_audio_codec"
    rejected_id = error["details"]["ingestion_id"]
    assert error["details"] == {"ingestion_id": rejected_id, "reason": "unsupported_audio_codec"}
    assert _counts() == {"meetings": 0, "recordings": 0, "jobs": 0, "ingestions": 1}
    assert queued == []
    with inspection_session_factory()() as db:
        record = db.get(IngestionRecord, rejected_id)
        assert record is not None
        assert record.status is IngestionStatus.REJECTED
        assert record.reason == "unsupported_audio_codec"
        assert record.media_hash == _sha256(bad_bytes)
        assert record.meeting_id is None and record.job_id is None
        assert record.created_by_user_id == tenant["user_id"]
        assert record.external_event_id == "bad-codec"
    rejected_audits = _audits("ingestion.rejected")
    assert [event.outcome for event in rejected_audits] == ["rejected"]
    assert rejected_audits[0].target_id == rejected_id
    assert rejected_audits[0].details["reason"] == "unsupported_audio_codec"
    assert rejected_audits[0].details["terminal"] is True

    # Same identity, same payload: the recorded refusal is replayed without
    # inspecting the bytes again (the validator would now answer differently).
    def refuse_decode(*_args, **_kwargs):
        inspections.append("decode")
        raise MediaDecodeError()

    monkeypatch.setattr("backend.routers.v1.meetings.validate_media_upload", refuse_decode)
    replay = _upload(client, tenant, key="bad-codec", content=bad_bytes)
    assert replay.status_code == 422, replay.text
    assert replay.json()["error"]["code"] == "unsupported_audio_codec"
    assert replay.json()["error"]["details"] == {
        "ingestion_id": rejected_id,
        "reason": "unsupported_audio_codec",
        "replayed": True,
    }
    assert inspections == ["codec"]
    replayed = _audits("ingestion.replayed")
    assert replayed[-1].target_id == rejected_id
    assert replayed[-1].details["status"] == "rejected"
    assert _counts()["ingestions"] == 1

    # Same identity, different payload: a conflict, exactly like an accepted event.
    conflict = _upload(client, tenant, key="bad-codec", content=OTHER_CONTENT)
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "ingestion_conflict"
    assert conflict.json()["error"]["details"]["conflict_count"] == 1
    assert inspections == ["codec"]

    # The ledger lists the refusal with its reason.
    listed = _get(client, tenant, "/api/v1/ingestions")
    assert [(item["status"], item["reason"]) for item in listed.json()["items"]] == [
        ("rejected", "unsupported_audio_codec")
    ]
    detail = _get(client, tenant, f"/api/v1/ingestions/{rejected_id}")
    assert detail.status_code == 200 and detail.json()["conflict_count"] == 1

    # A transient inspection failure leaves the identity free: 503, audit only, no row.
    def time_out(*_args, **_kwargs):
        inspections.append("timeout")
        raise MediaProbeTimeoutError(1.0)

    monkeypatch.setattr("backend.routers.v1.meetings.validate_media_upload", time_out)
    flaky = _upload(client, tenant, key="flaky-event")
    assert flaky.status_code == 503, flaky.text
    assert flaky.json()["error"]["code"] == "media_probe_timeout"
    assert "details" not in flaky.json()["error"]
    assert _counts()["ingestions"] == 1
    transient = _audits("ingestion.rejected")[-1]
    assert transient.outcome == "transient" and transient.target_id is None
    assert transient.details["reason"] == "media_probe_timeout"
    assert transient.details["terminal"] is False
    assert transient.details["external_event_id"] == "flaky-event"

    # ...so the retry with the same key succeeds once inspection works.
    _patch_media(monkeypatch)
    accepted = _upload(client, tenant, key="flaky-event")
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["idempotent_replay"] is False
    assert accepted.json()["ingestion"]["status"] == "accepted"
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 2}

    # A refusal before the payload exists (empty upload) has no identity to record.
    empty = client.post(
        "/api/v1/uploads",
        headers=_headers(tenant, **{"Idempotency-Key": "empty-event"}),
        cookies=tenant["cookies"],
        data={"title": "Empty", "language": "fa"},
        files={"file": ("empty.wav", b"", "audio/wav")},
    )
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "empty_upload"
    assert _counts()["ingestions"] == 2
    pre_staging = _audits("ingestion.rejected")[-1]
    assert pre_staging.outcome == "rejected" and pre_staging.target_id is None
    assert pre_staging.details["reason"] == "empty_upload"

    # Rejected rows are not "live" media: under the reject policy the same
    # bytes may still be ingested by a new event.
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant["workspace_id"])
        assert workspace is not None
        workspace.duplicate_media_policy = DuplicateMediaPolicy.REJECT
        db.commit()
    again = _upload(client, tenant, key="bad-bytes-fixed-elsewhere", content=bad_bytes)
    assert again.status_code == 202, again.text
    assert again.json()["ingestion"]["duplicate_of_id"] is None


# --- retry-safe finalize / abort (WP05-T04) --------------------------------------------


def _failing_commit_once(monkeypatch) -> dict[str, int]:
    """Make the first commit of a session that holds a flushed media row fail.

    The upload transaction flushes its rows before the final commit, so the
    media row sits in the identity map (not in ``session.new``); the login
    and tenant set-up commits never hold one and pass through untouched.
    """
    original = Session.commit
    fired = {"count": 0}

    def commit(self: Session) -> None:
        if fired["count"] == 0 and any(
            isinstance(obj, MediaObject) for obj in list(self.identity_map.values())
        ):
            fired["count"] += 1
            raise OperationalError("simulated", {}, Exception("simulated"))
        original(self)

    monkeypatch.setattr(Session, "commit", commit)
    return fired


def _upload_expecting_commit_failure(client, tenant: dict, *, key: str) -> None:
    try:
        response = _upload(client, tenant, key=key)
    except OperationalError:
        return
    assert response.status_code == 500, response.text


def _media_count(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count(MediaObject.id)).where(MediaObject.workspace_id == workspace_id)
        )


def _reservations(workspace_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count(UsageReservation.id)).where(
                UsageReservation.workspace_id == workspace_id
            )
        )


def _object_files(settings, workspace_id: str) -> list[str]:
    directory = Path(settings.storage_local_root) / "tenants" / workspace_id / "objects"
    if not directory.exists():
        return []
    return sorted(
        entry.name
        for entry in directory.iterdir()
        if entry.is_file() and not entry.name.endswith(".metadata.json")
    )


def test_a_commit_failure_after_the_store_aborts_cleanly_and_the_retry_makes_one_chain(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "retry-abort")
    workspace_id = tenant["workspace_id"]
    fired = _failing_commit_once(monkeypatch)

    _upload_expecting_commit_failure(client, tenant, key="retry-once")
    assert fired["count"] == 1
    # Nothing committed, nothing reserved, and the stored object was removed
    # by the route's abort path: the identity is free again.
    assert _counts() == {"meetings": 0, "recordings": 0, "jobs": 0, "ingestions": 0}
    assert _media_count(workspace_id) == 0 and _reservations(workspace_id) == 0
    assert _object_files(settings, workspace_id) == []

    retry = _upload(client, tenant, key="retry-once")
    assert retry.status_code == 202, retry.text
    assert retry.json()["idempotent_replay"] is False
    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    assert _media_count(workspace_id) == 1
    assert _reservations(workspace_id) == 2  # audio seconds and concurrency, once each
    assert len(_object_files(settings, workspace_id)) == 1


def test_replaying_one_finalization_a_hundred_times_yields_one_conversation_and_one_charge(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "replay-hundred")
    workspace_id = tenant["workspace_id"]
    first = _upload(client, tenant, key="replay-100")
    assert first.status_code == 202, first.text
    meeting_id = first.json()["meeting"]["id"]
    duration = first.json()["meeting"]["duration_seconds"]

    for _ in range(99):
        replay = _upload(client, tenant, key="replay-100")
        assert replay.status_code == 202, replay.text
        assert replay.json()["idempotent_replay"] is True
        assert replay.json()["meeting"]["id"] == meeting_id

    assert _counts() == {"meetings": 1, "recordings": 1, "jobs": 1, "ingestions": 1}
    assert _media_count(workspace_id) == 1
    assert _reservations(workspace_id) == 2
    assert len(_audits("ingestion.replayed")) == 99
    with inspection_session_factory()() as db:
        audio = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace_id, Entitlement.metric == "audio_seconds"
            )
        )
        assert audio is not None
        assert audio.reserved_value == duration and audio.used_value == 0


def test_invariants_hold_after_a_simulated_upload_failure(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "invariants")
    workspace_id = tenant["workspace_id"]
    fired = _failing_commit_once(monkeypatch)
    _upload_expecting_commit_failure(client, tenant, key="invariants-once")
    assert fired["count"] == 1
    retry = _upload(client, tenant, key="invariants-once")
    assert retry.status_code == 202, retry.text

    with inspection_session_factory()() as db:
        bind_tenant_context(db, workspace_id=workspace_id)
        violations = check_workspace(
            db, workspace_id, settings=settings, storage=create_object_storage(settings)
        )
    assert violations == []
