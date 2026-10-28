"""HOM-329: a release cascades only to holds that were inherited.

A held row does not record where its hold came from, so the release reads it
from the audit trail. A row's hold is inherited from S only when a
``media.legal_hold_inherited`` event for the row names S and records the row's
exact ``legal_hold_at``, and no ``media.legal_hold_placed`` event records it.
Every other hold counts as direct: a release of a source keeps it
(``media.legal_hold_kept``) until it is released by its own id. A direct hold
request on a row whose hold is inherited turns it into a direct hold.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.database.saas_models import AuditEvent, MediaLifecycleState, MediaObject
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import AUDIT_EVENT_CATALOG, record_audit_event
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_derived_legal_hold import (
    INHERITED,
    PLACED,
    RELEASED,
    _assert_inherited,
    _assert_unheld,
    _conversation,
    _derive,
    _events,
    _hold,
    _post,
    _review,
    _row,
    _utc,
)
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant

KEPT = "media.legal_hold_kept"
MATTER_A = "matter alpha 2026-41"
MATTER_B = "matter beta 2026-77"
KEPT_KEYS = {
    "media_id",
    "kind",
    "state",
    "meeting_id",
    "source_id",
    "legal_hold_at",
    "kept_because",
}
RELEASED_KEYS = {
    "media_id",
    "kind",
    "state",
    "meeting_id",
    "deletion_pending",
    "release_reason",
    "held_since",
    "source_id",
    "inherited_from_id",
}
PLACED_KEYS = {
    "media_id",
    "kind",
    "state",
    "meeting_id",
    "review_at",
    "deletion_requested",
    "legal_hold_at",
}
HOLD_COLUMNS = (
    "lifecycle_state",
    "legal_hold_at",
    "legal_hold_reason",
    "legal_hold_by_user_id",
    "legal_hold_review_at",
    "deletion_requested_at",
)


# --- helpers ----------------------------------------------------------------------------


def _release(client, tenant: dict, media_id: str, reason: str = "closed") -> dict:
    released = _post(
        client, tenant, f"/api/v1/media/{media_id}/legal-hold/release", {"reason": reason}
    )
    assert released.status_code == 200, released.text
    return released.json()


def _hold_at(event: AuditEvent) -> datetime:
    """The ``legal_hold_at`` an event records; it always carries its timezone."""
    value = datetime.fromisoformat(event.details["legal_hold_at"])
    assert value.tzinfo is not None, event.details["legal_hold_at"]
    return value


def _hold_state(media_id: str) -> tuple:
    """The hold columns of one row, timestamps in UTC."""
    row = _row(media_id)
    values = (getattr(row, column) for column in HOLD_COLUMNS)
    return tuple(_utc(value) if isinstance(value, datetime) else value for value in values)


def _hold_without_event(media_id: str, reason: str = MATTER_B) -> None:
    """Hold one row as a raw data fix would: the columns only, no audit event."""
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, media_id)
        now = datetime.now(UTC)
        media.lifecycle_state = MediaLifecycleState.LEGAL_HOLD
        media.legal_hold_at = now
        media.legal_hold_reason = reason
        media.legal_hold_review_at = now + timedelta(days=30)
        db.commit()


def _record(settings, media_id: str, events: list[tuple[str, str, dict]]) -> None:
    """Write hold events for ``media_id`` directly (old or unusual evidence).

    Each event is ``(workspace_id, event_type, details)``.
    """
    with inspection_session_factory()() as db:
        for workspace_id, event_type, details in events:
            record_audit_event(
                db,
                event_type=event_type,
                workspace_id=workspace_id,
                target_type="media",
                target_id=media_id,
                details={"media_id": media_id, **details},
                settings=settings,
            )
        db.commit()


def _assert_kept(
    media_id: str,
    *,
    source_id: str,
    because: str,
    before: tuple,
    inherited_from_id: str | None = None,
) -> AuditEvent:
    """``media_id`` is still held exactly as ``before`` and has one kept event, no release."""
    row = _row(media_id)
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD, media_id
    assert _hold_state(media_id) == before, media_id
    assert _events(RELEASED, media_id) == []
    (event,) = _events(KEPT, media_id)
    assert event.target_type == "media"
    assert set(event.details) == KEPT_KEYS | ({"inherited_from_id"} if inherited_from_id else set())
    assert event.details["media_id"] == media_id
    assert event.details["kind"] == row.kind.value
    assert event.details["state"] == "legal_hold"
    assert event.details["meeting_id"] == row.meeting_id
    assert event.details["source_id"] == source_id
    assert event.details["kept_because"] == because
    assert event.details.get("inherited_from_id") == inherited_from_id
    assert _utc(_hold_at(event)) == _utc(row.legal_hold_at)
    assert all(row.legal_hold_reason not in str(value) for value in event.details.values())
    return event


def _assert_released(
    media_id: str, *, source_id: str, inherited_from_id: str, deletion_pending: bool = False
) -> AuditEvent:
    """``media_id`` was released by the cascade of ``source_id`` (one event, nothing kept)."""
    (event,) = _events(RELEASED, media_id)
    assert set(event.details) == RELEASED_KEYS
    assert event.details["source_id"] == source_id
    assert event.details["inherited_from_id"] == inherited_from_id
    assert event.details["deletion_pending"] is deletion_pending
    assert _events(KEPT, media_id) == []
    return event


def _assert_own_release(media_id: str) -> AuditEvent:
    """The release target's event keeps its shape: no source, no inherited source."""
    (own,) = _events(RELEASED, media_id)
    assert "source_id" not in own.details
    assert "inherited_from_id" not in own.details
    return own


# --- AC1, AC2: a direct hold on a derived object stays; an inherited one goes -----------


def test_a_release_keeps_a_derived_object_held_directly_for_another_matter(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "provenance-direct")
    _meeting_id, original_id = _conversation(client, tenant, "provenance-direct")
    direct_id = _derive(settings, tenant, original_id)
    inherited_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, direct_id, reason=MATTER_B)
    _hold(client, tenant, original_id, reason=MATTER_A)
    # The hold on the ORIGINAL skipped the row that was already held.
    assert _row(direct_id).legal_hold_reason == MATTER_B
    assert len(_events(PLACED, direct_id)) == 1 and _events(INHERITED, direct_id) == []
    _assert_inherited(inherited_id, original_id)
    before = _hold_state(direct_id)

    released = _release(client, tenant, original_id)

    assert released["id"] == original_id and released["lifecycle_state"] == "active"
    _assert_own_release(original_id)
    # AC1: matter B's evidence stays held, untouched.
    _assert_kept(direct_id, source_id=original_id, because="direct", before=before)
    assert _row(direct_id).legal_hold_reason == MATTER_B
    # AC2 (allow-control, same release): the inherited hold is released.
    _assert_unheld(inherited_id)
    _assert_released(inherited_id, source_id=original_id, inherited_from_id=original_id)


# --- AC3 and H1: a direct hold request replaces an inherited hold -----------------------


def test_a_direct_hold_on_an_inherited_row_turns_it_direct_and_survives_the_source_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "provenance-flip")
    _meeting_id, original_id = _conversation(client, tenant, "provenance-flip")
    flipped_id = _derive(settings, tenant, original_id)
    sibling_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, original_id, reason=MATTER_A)
    _assert_inherited(flipped_id, original_id)
    inherited_at = _utc(_row(flipped_id).legal_hold_at)
    review = _review(45)

    held = _post(
        client,
        tenant,
        f"/api/v1/media/{flipped_id}/legal-hold",
        {"reason": MATTER_B, "review_at": review.isoformat()},
    )

    assert held.status_code == 200, held.text
    assert held.json()["legal_hold_reason"] == MATTER_B
    row = _row(flipped_id)
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert row.legal_hold_reason == MATTER_B
    assert row.legal_hold_by_user_id == tenant["user_id"]
    assert _utc(row.legal_hold_review_at) == review
    assert _utc(row.legal_hold_at) > inherited_at
    (placed,) = _events(PLACED, flipped_id)
    assert placed.target_type == "media"
    assert placed.actor_user_id == tenant["user_id"]
    assert set(placed.details) == PLACED_KEYS | {"inherited_from_id"}
    assert placed.details["inherited_from_id"] == original_id
    assert placed.details["state"] == "legal_hold"
    assert _utc(_hold_at(placed)) == _utc(row.legal_hold_at)
    assert _utc(datetime.fromisoformat(placed.details["review_at"])) == review
    assert all(MATTER_B not in str(value) for value in placed.details.values())
    assert len(_events(INHERITED, flipped_id)) == 1  # the replaced hold's event stays
    before = _hold_state(flipped_id)

    _release(client, tenant, original_id)

    _assert_kept(flipped_id, source_id=original_id, because="direct", before=before)
    # Allow-control: the sibling still had the inherited hold and is released.
    _assert_unheld(sibling_id)
    _assert_released(sibling_id, source_id=original_id, inherited_from_id=original_id)


def test_the_meeting_hold_turns_an_inherited_hold_into_a_direct_hold(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "provenance-meeting-flip")
    meeting_id, original_id = _conversation(client, tenant, "provenance-meeting-flip")
    derived_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, original_id, reason=MATTER_A)
    _assert_inherited(derived_id, original_id)
    original_before = _hold_state(original_id)

    held = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {"reason": MATTER_B, "review_at": _review(60).isoformat()},
    )

    assert held.status_code == 200, held.text
    items = {item["id"]: item for item in held.json()["items"]}
    # The ORIGINAL is held directly already: nothing is rewritten, no second event.
    assert items[original_id]["legal_hold_reason"] == MATTER_A
    assert _hold_state(original_id) == original_before
    assert len(_events(PLACED, original_id)) == 1
    # The derived row's inherited hold becomes the meeting hold.
    assert items[derived_id]["legal_hold_reason"] == MATTER_B
    (placed,) = _events(PLACED, derived_id)
    assert placed.details["inherited_from_id"] == original_id
    assert _utc(_hold_at(placed)) == _utc(_row(derived_id).legal_hold_at)
    before = _hold_state(derived_id)

    _release(client, tenant, original_id)
    _assert_kept(derived_id, source_id=original_id, because="direct", before=before)

    # Allow-control: the row is released by its own id.
    _release(client, tenant, derived_id)
    _assert_unheld(derived_id)
    _assert_own_release(derived_id)


# --- AC4, AC5: lineage of more than one level -------------------------------------------


@pytest.mark.parametrize("grandchild_first", [False, True], ids=["creation-order", "reversed"])
def test_a_release_follows_inherited_holds_through_every_level(
    client, email_outbox, monkeypatch, settings, grandchild_first
):
    """R2: the result does not depend on row order, so a grandchild that sorts
    before its source (``created_at``) is released too."""
    _patch_media(monkeypatch)
    key = f"provenance-chain-{grandchild_first}"
    tenant = _tenant(client, email_outbox, key)
    _meeting_id, original_id = _conversation(client, tenant, key)
    _hold(client, tenant, original_id)
    child_at = datetime.now(UTC)
    child_id = _derive(settings, tenant, original_id, now=child_at)
    grandchild_at = child_at - timedelta(hours=1) if grandchild_first else datetime.now(UTC)
    grandchild_id = _derive(settings, tenant, child_id, now=grandchild_at)
    _assert_inherited(child_id, original_id)
    _assert_inherited(grandchild_id, child_id)
    assert (_utc(_row(grandchild_id).created_at) < _utc(_row(child_id).created_at)) is (
        grandchild_first
    )

    _release(client, tenant, original_id)

    _assert_own_release(original_id)
    for media_id in (child_id, grandchild_id):
        _assert_unheld(media_id)
    _assert_released(child_id, source_id=original_id, inherited_from_id=original_id)
    _assert_released(grandchild_id, source_id=original_id, inherited_from_id=child_id)


def test_a_release_keeps_what_inherits_from_a_kept_direct_hold(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "provenance-chain-deny")
    _meeting_id, original_id = _conversation(client, tenant, "provenance-chain-deny")
    child_id = _derive(settings, tenant, original_id)
    grandchild_id = _derive(settings, tenant, child_id)
    _hold(client, tenant, child_id, reason=MATTER_B)
    _assert_inherited(grandchild_id, child_id)
    _hold(client, tenant, original_id, reason=MATTER_A)
    before = {media_id: _hold_state(media_id) for media_id in (child_id, grandchild_id)}

    _release(client, tenant, original_id)

    # Exactly one event for each held descendant: both kept, neither released.
    _assert_kept(child_id, source_id=original_id, because="direct", before=before[child_id])
    _assert_kept(
        grandchild_id,
        source_id=original_id,
        because="inherited_from_other",
        inherited_from_id=child_id,
        before=before[grandchild_id],
    )
    # Unchanged refusal: the ORIGINAL is not held any more.
    again = _post(
        client, tenant, f"/api/v1/media/{original_id}/legal-hold/release", {"reason": "closed"}
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "not_held"

    # Allow-control: releasing matter B's hold by its own id releases its lineage.
    _release(client, tenant, child_id)
    _assert_unheld(child_id)
    _assert_unheld(grandchild_id)
    _assert_own_release(child_id)
    (event,) = _events(RELEASED, grandchild_id)
    assert event.details["source_id"] == child_id
    assert event.details["inherited_from_id"] == child_id
    assert len(_events(KEPT, grandchild_id)) == 1  # only the earlier release kept it


# --- AC6: missing or unclear provenance counts as direct --------------------------------


EVIDENCE = {
    # case: (outcome, kept_because)
    "no-event": ("kept", "unknown"),
    "legacy-event": ("kept", "unknown"),
    "other-instant": ("kept", "unknown"),
    "two-sources": ("kept", "unknown"),
    "other-workspace": ("kept", "unknown"),
    "placed-at-the-same-instant": ("kept", "direct"),
    "same-instant-other-offset": ("released", None),
}


@pytest.mark.parametrize("case", list(EVIDENCE))
def test_a_hold_without_clear_inherited_evidence_stays_held(
    client, email_outbox, monkeypatch, settings, case
):
    _patch_media(monkeypatch)
    key = f"provenance-evidence-{case}"
    tenant = _tenant(client, email_outbox, key)
    _meeting_id, original_id = _conversation(client, tenant, key)
    fixed_id = _derive(settings, tenant, original_id)
    sibling_id = _derive(settings, tenant, original_id)
    _hold_without_event(fixed_id)
    _hold(client, tenant, original_id)
    _assert_inherited(sibling_id, original_id)
    held_at = _utc(_row(fixed_id).legal_hold_at)
    workspace_id = tenant["workspace_id"]
    copied = {"derived_from_id": original_id, "source_id": original_id}
    exact = {**copied, "legal_hold_at": held_at.isoformat()}
    if case == "other-workspace":
        foreign = _tenant(client, email_outbox, f"{key}-foreign")
        _record(settings, fixed_id, [(foreign["workspace_id"], INHERITED, exact)])
    else:
        later = (held_at + timedelta(microseconds=1)).isoformat()
        tehran = held_at.astimezone(timezone(timedelta(hours=3, minutes=30))).isoformat()
        evidence = {
            "no-event": [],
            "legacy-event": [(INHERITED, copied)],
            "other-instant": [(INHERITED, {**copied, "legal_hold_at": later})],
            "two-sources": [(INHERITED, exact), (INHERITED, {**exact, "source_id": sibling_id})],
            "placed-at-the-same-instant": [(INHERITED, exact), (PLACED, exact)],
            "same-instant-other-offset": [(INHERITED, {**copied, "legal_hold_at": tehran})],
        }[case]
        _record(settings, fixed_id, [(workspace_id, *event) for event in evidence])
    before = _hold_state(fixed_id)

    _release(client, tenant, original_id)

    outcome, because = EVIDENCE[case]
    if outcome == "kept":
        _assert_kept(fixed_id, source_id=original_id, because=because, before=before)
        assert _row(fixed_id).legal_hold_reason == MATTER_B
    else:
        _assert_unheld(fixed_id)
        _assert_released(fixed_id, source_id=original_id, inherited_from_id=original_id)
    # Allow-control in the same release: the sibling that inherited is released.
    _assert_unheld(sibling_id)
    _assert_released(sibling_id, source_id=original_id, inherited_from_id=original_id)


# --- AC7: a recorded deletion request ---------------------------------------------------


def test_a_release_deletes_an_inherited_row_and_keeps_the_bytes_of_a_direct_one(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "provenance-deletion")
    storage = create_object_storage(settings)
    meeting_id, original_id = _conversation(client, tenant, "provenance-deletion")
    inherited_id = _derive(settings, tenant, original_id)
    direct_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, direct_id, reason=MATTER_B)
    _hold(client, tenant, original_id, reason=MATTER_A)
    everything = (original_id, inherited_id, direct_id)
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
    before = _hold_state(direct_id)

    released = _release(client, tenant, original_id)

    assert released["lifecycle_state"] == "deleted"
    # Allow: the inherited row is released and its bytes are removed.
    for media_id in (original_id, inherited_id):
        assert _row(media_id).lifecycle_state is MediaLifecycleState.DELETED, media_id
        assert not storage.exists(tenant["workspace_id"], keys[media_id]), media_id
        assert len(_events("media.deleted", media_id)) == 1
    _assert_released(
        inherited_id, source_id=original_id, inherited_from_id=original_id, deletion_pending=True
    )
    # Deny: the direct hold keeps its state, its recorded request and its bytes.
    _assert_kept(direct_id, source_id=original_id, because="direct", before=before)
    assert storage.exists(tenant["workspace_id"], keys[direct_id])
    assert _events("media.deleted", direct_id) == []

    # Its own release resumes the recorded request.
    assert _release(client, tenant, direct_id)["lifecycle_state"] == "deleted"
    assert not storage.exists(tenant["workspace_id"], keys[direct_id])
    assert len(_events("media.deleted", direct_id)) == 1
    assert _assert_own_release(direct_id).details["deletion_pending"] is True


# --- AC9: a repeated direct hold is still a no-op ---------------------------------------


def test_a_direct_hold_request_on_a_direct_or_unclear_hold_changes_nothing(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "provenance-noop")
    _meeting_id, original_id = _conversation(client, tenant, "provenance-noop")
    direct_id = _derive(settings, tenant, original_id)
    unclear_id = _derive(settings, tenant, original_id)
    inherited_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, direct_id, reason=MATTER_A)
    _hold_without_event(unclear_id, reason=MATTER_A)
    _hold(client, tenant, original_id, reason=MATTER_A)
    before = {media_id: _hold_state(media_id) for media_id in (direct_id, unclear_id)}

    for media_id in (direct_id, unclear_id):
        again = _post(
            client,
            tenant,
            f"/api/v1/media/{media_id}/legal-hold",
            {"reason": MATTER_B, "review_at": _review(10).isoformat()},
        )
        assert again.status_code == 200, again.text
        assert again.json()["legal_hold_reason"] == MATTER_A
        assert _hold_state(media_id) == before[media_id]
    assert len(_events(PLACED, direct_id)) == 1
    assert _events(PLACED, unclear_id) == [] and _events(INHERITED, unclear_id) == []

    # Allow-control: the same request on the inherited row places a direct hold.
    flipped = _hold(client, tenant, inherited_id, reason=MATTER_B)
    assert flipped["legal_hold_reason"] == MATTER_B
    (placed,) = _events(PLACED, inherited_id)
    assert placed.details["inherited_from_id"] == original_id


# --- AC10, R5: event shape --------------------------------------------------------------


def test_hold_events_record_the_exact_hold_time_and_never_the_reason(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "provenance-shape")
    _meeting_id, original_id = _conversation(client, tenant, "provenance-shape")
    copied_id = _derive(settings, tenant, original_id)
    direct_id = _derive(settings, tenant, original_id)
    _hold(client, tenant, direct_id, reason=MATTER_B)
    _hold(client, tenant, original_id, reason=MATTER_A)  # placed; copied by the hold
    registered_id = _derive(settings, tenant, copied_id)  # copied at registration
    other_meeting_id, other_original_id = _conversation(client, tenant, "provenance-shape-2")
    held = _post(
        client,
        tenant,
        f"/api/v1/meetings/{other_meeting_id}/legal-hold",
        {"reason": MATTER_A, "review_at": _review().isoformat()},
    )
    assert held.status_code == 200, held.text

    for media_id in (original_id, direct_id, other_original_id):
        (placed,) = _events(PLACED, media_id)
        assert set(placed.details) == PLACED_KEYS
        assert _utc(_hold_at(placed)) == _utc(_row(media_id).legal_hold_at)
    for media_id, holder_id in ((copied_id, original_id), (registered_id, copied_id)):
        event = _assert_inherited(media_id, holder_id)
        assert _utc(_hold_at(event)) == _utc(_row(media_id).legal_hold_at)

    _release(client, tenant, original_id)  # a release and a kept event, too
    assert len(_events(KEPT, direct_id)) == 1

    with inspection_session_factory()() as db:
        events = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == tenant["workspace_id"],
                    AuditEvent.event_type.in_((PLACED, INHERITED, RELEASED, KEPT)),
                )
            )
        )
    assert {event.event_type for event in events} == {PLACED, INHERITED, RELEASED, KEPT}
    for event in events:
        for value in event.details.values():
            assert MATTER_A not in str(value) and MATTER_B not in str(value), event.event_type


def test_the_kept_event_is_a_catalogued_deletion_event():
    assert AuditEventType.MEDIA_LEGAL_HOLD_KEPT == KEPT
    assert AuditEventType.MEDIA_LEGAL_HOLD_KEPT.target_type == "media"
    assert AUDIT_EVENT_CATALOG[AuditEventType.MEDIA_LEGAL_HOLD_KEPT] == "deletion"
