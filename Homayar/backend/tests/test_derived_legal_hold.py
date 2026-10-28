"""HOM-313: a legal hold follows media lineage, with no race.

A derived object of a held source is created held (``media.legal_hold_inherited``).
A hold placed on one object also holds its live descendants, and a release
releases its held descendants. The meeting hold covers every object of the
conversation. Every path that changes holds or registers a derived object locks
the meeting row first and media rows after it, so a hold never misses a derived
row that is inserted at the same time.

The race tests use real PostgreSQL sessions in threads. They learn that a
session is blocked from ``pg_stat_activity`` (``wait_event_type = 'Lock'``) and
order the steps with events, never with a clock.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AuditEvent,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Meeting,
)
from backend.database.tenant_context import bind_tenant_context
from backend.media import derivatives
from backend.routers.v1 import meetings as meetings_router
from backend.services import conversation_purge_service, media_lifecycle_service
from backend.services.media_lifecycle_service import (
    MediaLifecycleError,
    mark_deleted,
    media_by_id,
    place_legal_hold,
    register_derived,
    request_deletion,
)
from backend.storage import create_object_storage
from backend.storage.base import StoredObject
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_media_lifecycle import _media_rows

PLACED = "media.legal_hold_placed"
INHERITED = "media.legal_hold_inherited"
RELEASED = "media.legal_hold_released"
REASON = "litigation 2026-42"
INHERITED_KEYS = {
    "media_id",
    "kind",
    "state",
    "meeting_id",
    "derived_from_id",
    "source_id",
    "review_at",
    "deletion_requested",
}
# Only fails a hung test; no assertion depends on how long anything took.
DEADLINE = 30.0


# --- helpers ----------------------------------------------------------------------------


def _review(days: int = 90) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def _utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive UTC timestamps."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _post(client, tenant: dict, path: str, payload: dict):
    return client.post(path, json=payload, headers=_headers(tenant), cookies=tenant["cookies"])


def _hold_body(reason: str = REASON, days: int = 90) -> dict:
    return {"reason": reason, "review_at": _review(days).isoformat()}


def _hold(client, tenant: dict, media_id: str, reason: str = REASON) -> dict:
    held = _post(client, tenant, f"/api/v1/media/{media_id}/legal-hold", _hold_body(reason))
    assert held.status_code == 200, held.text
    return held.json()


def _conversation(client, tenant: dict, key: str) -> tuple[str, str]:
    """One uploaded conversation; returns its meeting id and its ORIGINAL's id."""
    accepted = _upload(client, tenant, key=key, content=f"staged {key}".encode())
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    return meeting_id, _media_rows(meeting_id)[0].id


@contextmanager
def _app_session(tenant: dict) -> Iterator[Session]:
    """An application-role session bound to the tenant, as the job and the routes use."""
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        yield db


def _stored(settings, workspace_id: str) -> StoredObject:
    payload = f"derived {uuid.uuid4()}".encode()
    return create_object_storage(settings).put_stream(
        workspace_id,
        BytesIO(payload),
        content_type="audio/wav",
        original_filename="normalized.wav",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _register(
    db: Session,
    settings,
    source: MediaObject,
    stored: StoredObject,
    *,
    now: datetime | None = None,
    actor_user_id: str | None = None,
) -> MediaObject:
    return register_derived(
        db,
        source=source,
        kind=MediaKind.NORMALIZED,
        stored=stored,
        storage_backend=settings.storage_backend,
        producer="test-derivative/1",
        job_id=None,
        actor_user_id=actor_user_id,
        now=now or datetime.now(UTC),
        settings=settings,
        container_format="wav",
        duration_seconds=3,
        channel_count=2,
        sample_rate_hz=16_000,
    )


def _derive(settings, tenant: dict, source_id: str, **kwargs: Any) -> str:
    """Register one derived object of ``source_id`` in its own transaction."""
    stored = _stored(settings, tenant["workspace_id"])
    with _app_session(tenant) as db:
        source = media_by_id(db, workspace_id=tenant["workspace_id"], media_id=source_id)
        assert source is not None
        derived = _register(db, settings, source, stored, **kwargs)
        db.commit()
        return derived.id


def _close(settings, media_id: str) -> None:
    """Request the deletion of one object and close it (its bytes are not needed here)."""
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, media_id)
        now = datetime.now(UTC)
        request_deletion(
            db, media=media, reason="test", actor_user_id=None, now=now, settings=settings
        )
        mark_deleted(db, media=media, now=now, settings=settings)
        db.commit()


def _row(media_id: str) -> MediaObject:
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, media_id)
        assert media is not None
        return media


def _events(event_type: str, target_id: str | None = None) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        statement = select(AuditEvent).where(AuditEvent.event_type == event_type)
        if target_id is not None:
            statement = statement.where(AuditEvent.target_id == target_id)
        return list(db.scalars(statement.order_by(AuditEvent.occurred_at.asc())))


def _assert_unheld(media_id: str) -> None:
    row = _row(media_id)
    assert row.lifecycle_state is MediaLifecycleState.ACTIVE, media_id
    assert (row.legal_hold_at, row.legal_hold_reason) == (None, None)
    assert (row.legal_hold_by_user_id, row.legal_hold_review_at) == (None, None)


def _assert_inherited(media_id: str, holder_id: str) -> AuditEvent:
    """``media_id`` carries the recorded hold of ``holder_id`` and one inherited event."""
    row, holder = _row(media_id), _row(holder_id)
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD, media_id
    assert row.legal_hold_at is not None
    assert row.legal_hold_reason == holder.legal_hold_reason
    assert row.legal_hold_by_user_id == holder.legal_hold_by_user_id
    assert _utc(row.legal_hold_review_at) == _utc(holder.legal_hold_review_at)
    (event,) = _events(INHERITED, media_id)
    assert event.target_type == "media"
    assert set(event.details) == INHERITED_KEYS
    assert event.details["media_id"] == media_id
    assert event.details["kind"] == row.kind.value
    assert event.details["state"] == "legal_hold"
    assert event.details["meeting_id"] == row.meeting_id
    assert event.details["derived_from_id"] == row.derived_from_id
    assert event.details["source_id"] == holder_id
    review_at = datetime.fromisoformat(event.details["review_at"])
    assert _utc(review_at) == _utc(row.legal_hold_review_at)
    assert event.details["deletion_requested"] is (row.deletion_requested_at is not None)
    # The hold reason is free text: no detail value carries it.
    assert all(holder.legal_hold_reason not in str(value) for value in event.details.values())
    assert _events(PLACED, media_id) == []
    return event


# --- A: registration --------------------------------------------------------------------


def test_a_derived_object_of_a_held_source_is_created_held_with_one_inherited_event(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-register")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-register")
    _hold(client, tenant, original_id)

    registered_at = datetime.now(UTC)
    derived_id = _derive(settings, tenant, original_id, now=registered_at)

    row = _row(derived_id)
    assert _utc(row.legal_hold_at) == registered_at
    assert row.legal_hold_reason == REASON
    assert row.legal_hold_by_user_id == tenant["user_id"]
    event = _assert_inherited(derived_id, original_id)
    assert event.actor_user_id is None  # the registration's actor, not the hold's owner
    assert event.details["deletion_requested"] is False
    # media.registered is still written once; the row was held when it was inserted.
    (registered,) = _events("media.registered", derived_id)
    assert registered.details["derived_from_id"] == original_id
    assert registered.details["state"] == "legal_hold"


def test_a_live_unheld_source_gives_an_active_derived_object_and_a_dead_one_is_refused(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-allow")
    meeting_id, original_id = _conversation(client, tenant, "inherit-allow")

    active_id = _derive(settings, tenant, original_id)
    _assert_unheld(active_id)
    with inspection_session_factory()() as db:
        db.get(MediaObject, original_id).lifecycle_state = MediaLifecycleState.RETENTION_PENDING
        db.commit()
    pending_source_id = _derive(settings, tenant, original_id)
    _assert_unheld(pending_source_id)
    assert _events(INHERITED) == []

    # A source that is not live is still refused.
    with inspection_session_factory()() as db:
        request_deletion(
            db,
            media=db.get(MediaObject, original_id),
            reason="test",
            actor_user_id=None,
            now=datetime.now(UTC),
            settings=settings,
        )
        db.commit()
    assert _row(original_id).lifecycle_state is MediaLifecycleState.DELETION_PENDING
    with pytest.raises(MediaLifecycleError) as refused:
        _derive(settings, tenant, original_id)
    assert refused.value.code == "source_not_live"
    assert len(_media_rows(meeting_id)) == 3


def test_a_held_source_with_a_deletion_request_is_refused_and_nothing_is_inserted(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-deletion")
    meeting_id, original_id = _conversation(client, tenant, "inherit-deletion")
    _hold(client, tenant, original_id)
    with inspection_session_factory()() as db:
        assert not request_deletion(
            db,
            media=db.get(MediaObject, original_id),
            reason="owner asked",
            actor_user_id=None,
            now=datetime.now(UTC),
            settings=settings,
        )
        db.commit()
    held = _row(original_id)
    assert held.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert held.deletion_requested_at is not None
    registered_before = len(_events("media.registered"))

    with pytest.raises(MediaLifecycleError) as refused:
        _derive(settings, tenant, original_id)
    assert refused.value.code == "source_not_live"
    assert [row.id for row in _media_rows(meeting_id)] == [original_id]
    assert len(_events("media.registered")) == registered_before
    assert _events(INHERITED) == []

    # Allow-control: a held source without a deletion request gets a held derivative.
    _other_meeting, other_id = _conversation(client, tenant, "inherit-deletion-control")
    _hold(client, tenant, other_id)
    _assert_inherited(_derive(settings, tenant, other_id), other_id)


def test_registration_reads_the_committed_hold_of_a_source_loaded_earlier(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-stale")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-stale")
    stored = _stored(settings, tenant["workspace_id"])

    with _app_session(tenant) as db:
        # Loaded without a lock while the source is still active ...
        source = media_by_id(db, workspace_id=tenant["workspace_id"], media_id=original_id)
        assert source is not None and source.lifecycle_state is MediaLifecycleState.ACTIVE
        # ... then a hold commits in another session ...
        _hold(client, tenant, original_id)
        # ... and the registration still sees the committed hold.
        derived = _register(db, settings, source, stored)
        db.commit()
        derived_id = derived.id
    _assert_inherited(derived_id, original_id)


# --- B: the meeting hold ----------------------------------------------------------------


def test_the_meeting_hold_holds_every_derived_object_and_skips_deleted_ones(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-meeting")
    meeting_id, original_id = _conversation(client, tenant, "inherit-meeting")
    derived_id = _derive(settings, tenant, original_id)
    gone_id = _derive(settings, tenant, original_id)
    _close(settings, gone_id)

    held = _post(client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body())
    assert held.status_code == 200, held.text
    items = {item["id"]: item for item in held.json()["items"]}
    assert set(items) == {original_id, derived_id, gone_id}
    assert items[original_id]["lifecycle_state"] == "legal_hold"
    assert items[derived_id]["lifecycle_state"] == "legal_hold"
    assert items[gone_id]["lifecycle_state"] == "deleted"
    # The meeting route holds each row directly: placed, never inherited.
    for media_id in (original_id, derived_id):
        assert len(_events(PLACED, media_id)) == 1
        assert _row(media_id).legal_hold_reason == REASON
    assert _events(PLACED, gone_id) == []
    assert _events(INHERITED) == []

    # Idempotent: nothing is rewritten and no second event is written.
    again = _post(
        client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body("later matter")
    )
    assert again.status_code == 200, again.text
    for media_id in (original_id, derived_id):
        assert len(_events(PLACED, media_id)) == 1
        assert _row(media_id).legal_hold_reason == REASON
    assert _row(gone_id).lifecycle_state is MediaLifecycleState.DELETED


# --- C: a hold on one object ------------------------------------------------------------


def test_a_hold_on_an_object_holds_its_descendants(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-object")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-object")
    child_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)
    already_id = _derive(settings, tenant, original_id)
    gone_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, already_id, reason="earlier matter")
    _close(settings, gone_id)
    already_before = _row(already_id)

    held = _hold(client, tenant, original_id)
    assert held["id"] == original_id and held["lifecycle_state"] == "legal_hold"
    assert len(_events(PLACED, original_id)) == 1
    original = _row(original_id)
    for media_id in (child_id, grandchild_id):
        _assert_inherited(media_id, original_id)
        assert _row(media_id).legal_hold_at == original.legal_hold_at  # the same request
    # Already held: untouched, no event. Deleted: skipped.
    already = _row(already_id)
    assert already.legal_hold_reason == "earlier matter"
    assert already.legal_hold_at == already_before.legal_hold_at
    assert len(_events(PLACED, already_id)) == 1 and _events(INHERITED, already_id) == []
    assert _row(gone_id).lifecycle_state is MediaLifecycleState.DELETED
    assert _events(PLACED, gone_id) == [] and _events(INHERITED, gone_id) == []


def test_a_hold_on_an_object_holds_a_deletion_pending_descendant_and_keeps_its_request(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-pending")
    storage = create_object_storage(settings)
    _meeting_id, original_id = _conversation(client, tenant, "inherit-pending")
    child_id = _derive(settings, tenant, original_id)
    with inspection_session_factory()() as db:
        assert request_deletion(
            db,
            media=db.get(MediaObject, child_id),
            reason="owner asked",
            actor_user_id=None,
            now=datetime.now(UTC),
            settings=settings,
        )
        db.commit()
    pending = _row(child_id)
    assert pending.lifecycle_state is MediaLifecycleState.DELETION_PENDING
    assert storage.exists(tenant["workspace_id"], pending.storage_key)

    _hold(client, tenant, original_id)
    event = _assert_inherited(child_id, original_id)
    assert event.details["deletion_requested"] is True
    held = _row(child_id)
    assert held.deletion_requested_at == pending.deletion_requested_at
    assert held.deletion_reason == pending.deletion_reason

    # The release resumes the recorded request: the bytes go, the source stays.
    released = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200, released.text
    assert released.json()["lifecycle_state"] == "active"
    assert _row(child_id).lifecycle_state is MediaLifecycleState.DELETED
    assert not storage.exists(tenant["workspace_id"], pending.storage_key)
    (event,) = _events(RELEASED, child_id)
    assert event.details["deletion_pending"] is True


def test_a_repeated_hold_copies_the_recorded_hold_to_unheld_descendants(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-repeat")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-repeat")
    child_id = _derive(settings, tenant, original_id)
    # The single-row service function still holds only the row it is given.
    with _app_session(tenant) as db:
        place_legal_hold(
            db,
            media=media_by_id(db, workspace_id=tenant["workspace_id"], media_id=original_id),
            reason="first matter",
            review_at=_review(30),
            actor_user_id=tenant["user_id"],
            now=datetime.now(UTC),
            settings=settings,
        )
        db.commit()
    _assert_unheld(child_id)
    original_before = _row(original_id)

    again = _hold(client, tenant, original_id, reason="second matter")
    assert again["legal_hold_reason"] == "first matter"
    original = _row(original_id)
    assert original.legal_hold_at == original_before.legal_hold_at
    assert len(_events(PLACED, original_id)) == 1
    _assert_inherited(child_id, original_id)
    child = _row(child_id)
    assert child.legal_hold_reason == "first matter"
    assert _utc(child.legal_hold_at) > _utc(original.legal_hold_at)


def test_a_hold_on_a_derived_object_never_reaches_its_source_or_siblings(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-derived")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-derived")
    child_id = _derive(settings, tenant, original_id)
    sibling_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)

    held = _hold(client, tenant, child_id)
    assert held["id"] == child_id
    assert len(_events(PLACED, child_id)) == 1
    _assert_inherited(grandchild_id, child_id)
    for media_id in (original_id, sibling_id):
        _assert_unheld(media_id)
        assert _events(PLACED, media_id) == [] and _events(INHERITED, media_id) == []

    # Unchanged refusals: a deleted object, an unknown id, another tenant's id.
    _close(settings, sibling_id)
    late = _post(client, tenant, f"/api/v1/media/{sibling_id}/legal-hold", _hold_body())
    assert late.status_code == 409 and late.json()["error"]["code"] == "already_deleted"
    unknown = _post(client, tenant, f"/api/v1/media/{uuid.uuid4()}/legal-hold", _hold_body())
    assert unknown.status_code == 404
    other = _tenant(client, email_outbox, "inherit-derived-foreign")
    foreign = _post(client, other, f"/api/v1/media/{original_id}/legal-hold", _hold_body())
    assert foreign.status_code == 404
    _assert_unheld(original_id)


# --- D: release -------------------------------------------------------------------------


def test_a_release_releases_the_held_descendants(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-release")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-release")
    child_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)
    _hold(client, tenant, original_id)
    held_since = {
        media_id: _utc(_row(media_id).legal_hold_at) for media_id in (child_id, grandchild_id)
    }

    released = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200, released.text
    assert released.json()["id"] == original_id
    assert released.json()["lifecycle_state"] == "active"
    (own,) = _events(RELEASED, original_id)
    assert "source_id" not in own.details  # the target's event is unchanged
    for media_id in (child_id, grandchild_id):
        _assert_unheld(media_id)
        (event,) = _events(RELEASED, media_id)
        assert event.details["source_id"] == original_id
        assert event.details["release_reason"] == "closed"
        assert event.details["deletion_pending"] is False
        assert _utc(datetime.fromisoformat(event.details["held_since"])) == held_since[media_id]


def test_a_release_with_a_deletion_request_removes_the_descendants_bytes(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-release-delete")
    storage = create_object_storage(settings)
    meeting_id, original_id = _conversation(client, tenant, "inherit-release-delete")
    child_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)
    everything = (original_id, child_id, grandchild_id)
    held = _post(client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body())
    assert held.status_code == 200, held.text
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    keys = {}
    for media_id in everything:
        row = _row(media_id)
        assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
        assert row.deletion_requested_at is not None
        assert storage.exists(tenant["workspace_id"], row.storage_key)
        keys[media_id] = row.storage_key

    released = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200, released.text
    assert released.json()["lifecycle_state"] == "deleted"
    for media_id in everything:
        assert _row(media_id).lifecycle_state is MediaLifecycleState.DELETED, media_id
        assert not storage.exists(tenant["workspace_id"], keys[media_id]), media_id
        assert len(_events("media.deleted", media_id)) == 1
    for media_id in (child_id, grandchild_id):
        (event,) = _events(RELEASED, media_id)
        assert event.details["deletion_pending"] is True
        assert event.details["source_id"] == original_id


def test_a_release_of_an_unheld_object_changes_nothing_and_stays_below_a_derived_target(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-release-deny")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-release-deny")
    child_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)
    _hold(client, tenant, child_id)

    refused = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
    )
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "not_held"
    for media_id in (child_id, grandchild_id):
        assert _row(media_id).lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert _events(RELEASED) == []

    # Releasing a derived object releases it and its descendants, never its source.
    _hold(client, tenant, original_id)
    released = _post(
        client, tenant, f"/api/v1/media/{child_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200, released.text
    assert released.json()["id"] == child_id
    _assert_unheld(child_id)
    _assert_unheld(grandchild_id)
    (event,) = _events(RELEASED, grandchild_id)
    assert event.details["source_id"] == child_id
    assert _row(original_id).lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert _events(RELEASED, original_id) == []

    no_reason = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": ""}
    )
    assert no_reason.status_code == 422
    assert _row(original_id).lifecycle_state is MediaLifecycleState.LEGAL_HOLD


# --- F2: an object without a meeting ----------------------------------------------------


def test_an_object_without_a_meeting_is_held_and_released_with_its_descendants(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "inherit-no-meeting")
    _meeting_id, original_id = _conversation(client, tenant, "inherit-no-meeting")
    child_id = _derive(settings, tenant, original_id)
    with inspection_session_factory()() as db:
        for media_id in (original_id, child_id):
            db.execute(
                text("UPDATE media_objects SET meeting_id = NULL WHERE id = :id"), {"id": media_id}
            )
        db.commit()

    _hold(client, tenant, original_id)
    _assert_inherited(child_id, original_id)
    assert _row(child_id).meeting_id is None
    released = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200, released.text
    _assert_unheld(original_id)
    _assert_unheld(child_id)
    (event,) = _events(RELEASED, child_id)
    assert event.details["source_id"] == original_id


# --- E3 ---------------------------------------------------------------------------------


def test_the_source_legal_hold_reason_code_is_gone():
    assert not hasattr(derivatives, "SOURCE_LEGAL_HOLD")
    assert "source_legal_hold" not in derivatives.REASON_CODES


# --- races (PostgreSQL) -----------------------------------------------------------------


class _Worker(threading.Thread):
    """Runs ``work`` in its own thread and keeps its result or its error."""

    def __init__(self, work: Callable[[], Any]) -> None:
        super().__init__(daemon=True)
        self._work = work
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._work()
        except BaseException as exc:  # noqa: BLE001 - raised again by finish()
            self.error = exc

    def finish(self) -> Any:
        self.join(DEADLINE)
        assert not self.is_alive(), "the worker did not finish"
        if self.error is not None:
            raise self.error
        return self.result


def _lock_waits() -> list[str]:
    """The statements of the backends of this database that wait on a lock."""
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                text(
                    "SELECT query FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND pid <> pg_backend_pid()"
                )
            )
        )


def _blocked(worker: _Worker) -> str:
    """The statement ``worker`` is blocked on; fails when it ends without waiting."""
    deadline = time.monotonic() + DEADLINE
    while time.monotonic() < deadline:
        waits = _lock_waits()
        if waits:
            return waits[0]
        if not worker.is_alive():
            pytest.fail(f"the session never waited on a lock (error: {worker.error!r})")
        time.sleep(0.01)
    pytest.fail("no session waited on a lock before the deadline")


def _on_meeting_row(statement: str) -> bool:
    """F1: the blocked statement is the meeting row lock, taken before any media row."""
    return re.search(r"\bFROM meetings\b.*\bFOR (UPDATE|KEY SHARE)\b", statement, re.S) is not None


def _registration(
    settings,
    tenant: dict,
    source_id: str,
    *,
    job_locks: bool,
    inserted: threading.Event | None = None,
    gate: threading.Event | None = None,
    now: datetime | None = None,
) -> Callable[[], str]:
    """A registration that loads its source without a lock and commits at the end.

    With ``job_locks`` it first takes the derivative job's locks (the meeting row,
    then the ORIGINAL row, as ``_register`` does). With ``inserted`` and ``gate``
    it stops after the insert, its locks held and nothing committed.
    """
    stored = _stored(settings, tenant["workspace_id"])

    def work() -> str:
        with _app_session(tenant) as db:
            source = media_by_id(db, workspace_id=tenant["workspace_id"], media_id=source_id)
            assert source is not None
            if job_locks:
                db.get(Meeting, source.meeting_id, with_for_update=True, populate_existing=True)
                media_by_id(
                    db, workspace_id=tenant["workspace_id"], media_id=source_id, for_update=True
                )
            derived = _register(db, settings, source, stored, now=now)
            if inserted is not None and gate is not None:
                inserted.set()
                assert gate.wait(DEADLINE)
            db.commit()
            return derived.id

    return work


def _pause_after_first(
    monkeypatch, name: str, entered: threading.Event, gate: threading.Event
) -> None:
    """The first call of the service function ``name`` returns only after ``gate``.

    The route that called it then holds its row locks and has not committed.
    """
    real = getattr(media_lifecycle_service, name)
    calls: list[int] = []

    def paused(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if not calls:
            calls.append(1)
            entered.set()
            assert gate.wait(DEADLINE)
        return result

    monkeypatch.setattr(media_lifecycle_service, name, paused)
    monkeypatch.setattr(meetings_router, name, paused, raising=False)


@pytest.mark.postgres
def test_race_meeting_hold_waits_for_an_inflight_registration_and_holds_its_row(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race-meeting")
    meeting_id, original_id = _conversation(client, tenant, "race-meeting")
    inserted, gate = threading.Event(), threading.Event()
    registration = _Worker(
        _registration(settings, tenant, original_id, job_locks=True, inserted=inserted, gate=gate)
    )
    registration.start()
    assert inserted.wait(DEADLINE)  # the derived row is inserted, not committed

    hold = _Worker(
        lambda: _post(client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body())
    )
    hold.start()
    waited_on = _blocked(hold)
    gate.set()
    derived_id = registration.finish()
    response = hold.finish()

    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert set(items) == {original_id, derived_id}
    assert items[derived_id]["lifecycle_state"] == "legal_hold"
    for media_id in (original_id, derived_id):
        assert _row(media_id).lifecycle_state is MediaLifecycleState.LEGAL_HOLD
        assert len(_events(PLACED, media_id)) == 1
    assert _events(INHERITED) == []
    assert _on_meeting_row(waited_on), waited_on


@pytest.mark.postgres
@pytest.mark.parametrize("job_locks", [True, False], ids=["job-locks", "plain-caller"])
def test_race_registration_during_a_meeting_hold_waits_and_is_created_held(
    client, email_outbox, monkeypatch, settings, job_locks
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race-meeting-first")
    meeting_id, original_id = _conversation(client, tenant, "race-meeting-first")
    entered, gate = threading.Event(), threading.Event()
    _pause_after_first(monkeypatch, "place_legal_hold", entered, gate)
    hold = _Worker(
        lambda: _post(client, tenant, f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body())
    )
    hold.start()
    assert entered.wait(DEADLINE)  # the hold holds its locks, not committed

    registration = _Worker(_registration(settings, tenant, original_id, job_locks=job_locks))
    registration.start()
    waited_on = _blocked(registration)
    gate.set()
    response = hold.finish()
    derived_id = registration.finish()

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == [original_id]
    _assert_inherited(derived_id, original_id)
    assert _on_meeting_row(waited_on), waited_on


@pytest.mark.postgres
def test_race_object_hold_waits_for_an_inflight_registration_and_holds_its_row(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race-object")
    _meeting_id, original_id = _conversation(client, tenant, "race-object")
    inserted, gate = threading.Event(), threading.Event()
    registration = _Worker(
        _registration(settings, tenant, original_id, job_locks=True, inserted=inserted, gate=gate)
    )
    registration.start()
    assert inserted.wait(DEADLINE)

    hold = _Worker(
        lambda: _post(client, tenant, f"/api/v1/media/{original_id}/legal-hold", _hold_body())
    )
    hold.start()
    waited_on = _blocked(hold)
    gate.set()
    derived_id = registration.finish()
    response = hold.finish()

    assert response.status_code == 200, response.text
    assert response.json()["lifecycle_state"] == "legal_hold"
    assert len(_events(PLACED, original_id)) == 1
    _assert_inherited(derived_id, original_id)
    assert _on_meeting_row(waited_on), waited_on


@pytest.mark.postgres
def test_race_registration_during_an_object_hold_waits_and_is_created_held(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race-object-first")
    _meeting_id, original_id = _conversation(client, tenant, "race-object-first")
    entered, gate = threading.Event(), threading.Event()
    _pause_after_first(monkeypatch, "place_legal_hold", entered, gate)
    hold = _Worker(
        lambda: _post(client, tenant, f"/api/v1/media/{original_id}/legal-hold", _hold_body())
    )
    hold.start()
    assert entered.wait(DEADLINE)

    registration = _Worker(_registration(settings, tenant, original_id, job_locks=False))
    registration.start()
    waited_on = _blocked(registration)
    gate.set()
    response = hold.finish()
    derived_id = registration.finish()

    assert response.status_code == 200, response.text
    _assert_inherited(derived_id, original_id)
    assert _on_meeting_row(waited_on), waited_on


@pytest.mark.postgres
def test_race_release_waits_for_an_inflight_held_registration_and_releases_its_row(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "race-release")
    _meeting_id, original_id = _conversation(client, tenant, "race-release")
    _hold(client, tenant, original_id)
    registered_at = datetime.now(UTC)
    inserted, gate = threading.Event(), threading.Event()
    registration = _Worker(
        _registration(
            settings,
            tenant,
            original_id,
            job_locks=True,
            inserted=inserted,
            gate=gate,
            now=registered_at,
        )
    )
    registration.start()
    assert inserted.wait(DEADLINE)  # the held derived row is inserted, not committed

    release = _Worker(
        lambda: _post(
            client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
        )
    )
    release.start()
    waited_on = _blocked(release)
    gate.set()
    derived_id = registration.finish()
    response = release.finish()

    assert response.status_code == 200, response.text
    assert response.json()["lifecycle_state"] == "active"
    _assert_unheld(derived_id)
    assert len(_events(INHERITED, derived_id)) == 1  # created held ...
    (event,) = _events(RELEASED, derived_id)  # ... and released by the release
    assert event.details["source_id"] == original_id
    assert event.details["deletion_pending"] is False
    assert _utc(datetime.fromisoformat(event.details["held_since"])) == registered_at
    assert _on_meeting_row(waited_on), waited_on


@pytest.mark.postgres
@pytest.mark.parametrize("route", ["meeting-hold", "object-hold", "release"])
def test_race_a_hold_route_during_a_conversation_deletion_waits_and_does_not_deadlock(
    client, email_outbox, monkeypatch, settings, caplog, route
):
    """The deletion locks the media rows before it writes the meeting row (its tombstone).

    A hold route that waits on those rows must not hold a meeting lock that the
    tombstone write waits for, or one of the two would be aborted as a deadlock.
    """
    caplog.set_level(logging.WARNING)
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, f"race-delete-{route}")
    meeting_id, original_id = _conversation(client, tenant, f"race-delete-{route}")
    if route == "release":
        _hold(client, tenant, original_id)
    entered, gate = threading.Event(), threading.Event()
    real = conversation_purge_service.retire_meeting_media

    def paused(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        entered.set()
        assert gate.wait(DEADLINE)
        return result

    monkeypatch.setattr(conversation_purge_service, "retire_meeting_media", paused)
    deletion = _Worker(
        lambda: client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
        )
    )
    deletion.start()
    assert entered.wait(DEADLINE)  # the media rows are locked, the tombstone is not written
    path, body = {
        "meeting-hold": (f"/api/v1/meetings/{meeting_id}/legal-hold", _hold_body()),
        "object-hold": (f"/api/v1/media/{original_id}/legal-hold", _hold_body()),
        "release": (f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}),
    }[route]
    hold = _Worker(lambda: _post(client, tenant, path, body))
    hold.start()
    waited_on = _blocked(hold)
    gate.set()
    deleted = deletion.finish()
    response = hold.finish()

    assert deleted.status_code == 204
    assert response.status_code == 200, response.text
    # The route catches a failed inline purge attempt and only logs it: none may have failed.
    purge_failures = [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("MEETING_PURGE_ATTEMPT_FAILED")
    ]
    assert purge_failures == []
    assert re.search(r"\bFROM media_objects\b", waited_on), waited_on
    row = _row(original_id)
    assert row.deletion_requested_at is not None
    if route == "release":
        assert row.lifecycle_state is MediaLifecycleState.DELETED
    else:
        assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD


# --- HOM-314 F: registration follows the ORIGINAL of the lineage ------------------------


def _request(settings, media_id: str) -> None:
    with inspection_session_factory()() as db:
        request_deletion(
            db,
            media=db.get(MediaObject, media_id),
            reason="owner asked",
            actor_user_id=None,
            now=datetime.now(UTC),
            settings=settings,
        )
        db.commit()


def _assert_refused(settings, tenant: dict, source_id: str, meeting_id: str) -> None:
    """Registering from ``source_id`` is refused and writes nothing."""
    rows_before = [row.id for row in _media_rows(meeting_id)]
    registered_before = len(_events("media.registered"))
    inherited_before = len(_events(INHERITED))
    with pytest.raises(MediaLifecycleError) as refused:
        _derive(settings, tenant, source_id)
    assert refused.value.code == "source_not_live"
    assert [row.id for row in _media_rows(meeting_id)] == rows_before
    assert len(_events("media.registered")) == registered_before
    assert len(_events(INHERITED)) == inherited_before


def test_a_live_source_whose_original_is_not_live_or_deleting_is_refused(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "lineage-refused")

    # The ORIGINAL deletion pending, then deleted; its derived object is still active.
    meeting_id, original_id = _conversation(client, tenant, "lineage-pending")
    child_id = _derive(settings, tenant, original_id)
    _request(settings, original_id)
    assert _row(original_id).lifecycle_state is MediaLifecycleState.DELETION_PENDING
    _assert_unheld(child_id)
    _assert_refused(settings, tenant, child_id, meeting_id)
    with inspection_session_factory()() as db:
        mark_deleted(
            db, media=db.get(MediaObject, original_id), now=datetime.now(UTC), settings=settings
        )
        db.commit()
    _assert_refused(settings, tenant, child_id, meeting_id)

    # The ORIGINAL held with a recorded deletion request; its derived object released by id.
    meeting_id, original_id = _conversation(client, tenant, "lineage-held-deleting")
    child_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, original_id)
    _request(settings, original_id)
    released = _post(
        client, tenant, f"/api/v1/media/{child_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200, released.text
    _assert_unheld(child_id)
    assert _row(original_id).deletion_requested_at is not None
    _assert_refused(settings, tenant, child_id, meeting_id)

    # Fail closed: a lineage that does not reach an ORIGINAL row. A derived row always
    # has a link (ck_media_lineage), so the broken lineage is a loop.
    meeting_id, original_id = _conversation(client, tenant, "lineage-broken")
    child_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE media_objects SET derived_from_id = :loop WHERE id = :id"),
            {"loop": grandchild_id, "id": child_id},
        )
        db.commit()
    _assert_unheld(grandchild_id)
    _assert_refused(settings, tenant, grandchild_id, meeting_id)


def test_a_held_original_holds_an_object_derived_from_its_released_derivative(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "lineage-held")
    _meeting_id, original_id = _conversation(client, tenant, "lineage-held")
    child_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, original_id)
    _assert_inherited(child_id, original_id)
    released = _post(
        client, tenant, f"/api/v1/media/{child_id}/legal-hold/release", {"reason": "closed"}
    )
    assert released.status_code == 200, released.text
    _assert_unheld(child_id)

    registered_at = datetime.now(UTC)
    grandchild_id = _derive(settings, tenant, child_id, now=registered_at)

    row = _row(grandchild_id)
    assert row.derived_from_id == child_id
    assert _utc(row.legal_hold_at) == registered_at
    assert row.legal_hold_reason == REASON
    assert row.legal_hold_by_user_id == tenant["user_id"]
    event = _assert_inherited(grandchild_id, original_id)
    assert event.details["deletion_requested"] is False
    (registered,) = _events("media.registered", grandchild_id)
    assert registered.details["state"] == "legal_hold"


def test_a_held_source_gives_its_own_hold_even_when_its_original_is_held(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "lineage-nearest")
    _meeting_id, original_id = _conversation(client, tenant, "lineage-nearest")
    child_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, child_id, reason="derived matter")
    _hold(client, tenant, original_id)
    assert _row(child_id).legal_hold_reason == "derived matter"

    grandchild_id = _derive(settings, tenant, child_id)

    _assert_inherited(grandchild_id, child_id)
    assert _row(grandchild_id).legal_hold_reason == "derived matter"


def test_a_derived_source_of_a_live_unheld_original_gives_an_active_object(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "lineage-allow")
    _meeting_id, original_id = _conversation(client, tenant, "lineage-allow")
    child_id = _derive(settings, tenant, original_id)

    _assert_unheld(_derive(settings, tenant, child_id))
    with inspection_session_factory()() as db:
        db.get(MediaObject, original_id).lifecycle_state = MediaLifecycleState.RETENTION_PENDING
        db.commit()
    _assert_unheld(_derive(settings, tenant, child_id))
    assert _events(INHERITED) == []


def test_registration_reads_the_committed_state_of_an_original_loaded_earlier(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "lineage-stale")
    meeting_id, original_id = _conversation(client, tenant, "lineage-stale")
    child_id = _derive(settings, tenant, original_id)
    stored = _stored(settings, tenant["workspace_id"])

    with _app_session(tenant) as db:
        # Both loaded without a lock while the ORIGINAL is still active ...
        source = media_by_id(db, workspace_id=tenant["workspace_id"], media_id=child_id)
        original = media_by_id(db, workspace_id=tenant["workspace_id"], media_id=original_id)
        assert source is not None and original is not None
        assert original.lifecycle_state is MediaLifecycleState.ACTIVE
        # ... then a deletion request commits in another session ...
        _request(settings, original_id)
        # ... and the registration still sees it.
        with pytest.raises(MediaLifecycleError) as refused:
            _register(db, settings, source, stored)
        assert refused.value.code == "source_not_live"
        db.rollback()
    assert [row.id for row in _media_rows(meeting_id)] == [original_id, child_id]
