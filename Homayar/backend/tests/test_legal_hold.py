"""WP07-T02: legal hold with owner, reason, review date and audited release.

A hold is a compliance action, not a user preference: only ``legal_hold:manage``
(owner) may place or release one, every hold names who placed it, why and by
when it must be reviewed, a release needs its own audited reason, and a hold
overrides deletion until released — but never resurrects deleted bytes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from backend.database.saas_models import MediaLifecycleState, MediaObject, Recording
from backend.jobs.media_reconciliation import reconcile_media_objects
from backend.storage import create_object_storage
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import (
    OTHER_CONTENT,
    _audits,
    _headers,
    _patch_media,
    _tenant,
    _upload,
)
from backend.tests.test_media_lifecycle import _media_rows

probe_role = _rls.probe_role


def _review(days: int = 90) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


def _post(client, tenant: dict, path: str, payload: dict):
    return client.post(path, json=payload, headers=_headers(tenant), cookies=tenant["cookies"])


def test_owner_places_a_reviewed_hold_that_overrides_deletion_and_release_is_audited(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hold-owner")
    storage = create_object_storage(settings)
    accepted = _upload(client, tenant, key="hold-meeting-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    media_id = _media_rows(meeting_id)[0].id

    # Validation: the review date must be in the future; the reason is required.
    past = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {
            "reason": "litigation 2026-42",
            "review_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )
    assert past.status_code == 409 and past.json()["error"]["code"] == "hold_review_invalid"
    naive = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {"reason": "litigation 2026-42", "review_at": "2030-01-01T00:00:00"},
    )
    assert naive.status_code == 409 and naive.json()["error"]["code"] == "hold_review_invalid"
    short = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {"reason": "x", "review_at": _review()},
    )
    assert short.status_code == 422
    assert _media_rows(meeting_id)[0].lifecycle_state is MediaLifecycleState.ACTIVE

    held = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {"reason": "  litigation   2026-42 ", "review_at": _review(90)},
    )
    assert held.status_code == 200, held.text
    item = held.json()["items"][0]
    assert item["lifecycle_state"] == "legal_hold"
    assert item["legal_hold_reason"] == "litigation 2026-42"
    assert item["legal_hold_by_user_id"] == tenant["user_id"]
    assert item["legal_hold_review_at"] is not None
    assert "storage_key" not in item
    placed = _audits("media.legal_hold_placed")
    assert len(placed) == 1 and placed[0].target_id == media_id
    audited_review = datetime.fromisoformat(placed[0].details["review_at"])
    shown_review = datetime.fromisoformat(item["legal_hold_review_at"].replace("Z", "+00:00"))
    if shown_review.tzinfo is None:  # SQLite hands back naive UTC timestamps
        shown_review = shown_review.replace(tzinfo=UTC)
    assert audited_review == shown_review
    assert placed[0].details["deletion_requested"] is False

    # Repeating the hold is idempotent: nothing rewritten, no second audit.
    again = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {"reason": "someone else later", "review_at": _review(10)},
    )
    assert again.status_code == 200
    assert again.json()["items"][0]["legal_hold_reason"] == "litigation 2026-42"
    assert len(_audits("media.legal_hold_placed")) == 1

    # Deleting the conversation revokes access but keeps the held bytes.
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    row = _media_rows(meeting_id)[0]
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert row.deletion_requested_at is not None and row.deleted_at is None
    assert storage.exists(tenant["workspace_id"], row.storage_key)
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None and recording.deleted_at is not None
    # The meeting-level route no longer sees the deleted conversation ...
    gone = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        {"reason": "again", "review_at": _review()},
    )
    assert gone.status_code == 404
    # ... but the object-level route still reaches the held object.
    still = _post(
        client,
        tenant,
        f"/api/v1/media/{media_id}/legal-hold",
        {"reason": "again", "review_at": _review()},
    )
    assert still.status_code == 200 and still.json()["lifecycle_state"] == "legal_hold"

    # Release needs a reason; the recorded deletion request then runs to the bytes.
    no_reason = _post(
        client, tenant, f"/api/v1/media/{media_id}/legal-hold/release", {"reason": ""}
    )
    assert no_reason.status_code == 422
    released = _post(
        client, tenant, f"/api/v1/media/{media_id}/legal-hold/release", {"reason": "matter closed"}
    )
    assert released.status_code == 200, released.text
    assert released.json()["lifecycle_state"] == "deleted"
    assert (
        released.json()["legal_hold_at"] is None and released.json()["legal_hold_review_at"] is None
    )
    assert not storage.exists(tenant["workspace_id"], row.storage_key)
    release_audit = _audits("media.legal_hold_released")[-1]
    assert release_audit.details["release_reason"] == "matter closed"
    assert release_audit.details["deletion_pending"] is True
    assert release_audit.details["held_since"] is not None
    assert _audits("media.deleted")[-1].target_id == media_id

    # Irreversible boundary: a hold cannot restore deleted bytes; release twice is refused.
    late = _post(
        client,
        tenant,
        f"/api/v1/media/{media_id}/legal-hold",
        {"reason": "too late", "review_at": _review()},
    )
    assert late.status_code == 409 and late.json()["error"]["code"] == "already_deleted"
    twice = _post(
        client, tenant, f"/api/v1/media/{media_id}/legal-hold/release", {"reason": "again"}
    )
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "not_held"


def test_release_without_a_deletion_request_returns_the_object_to_active(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hold-release")
    accepted = _upload(client, tenant, key="hold-release-0001")
    media_id = _media_rows(accepted.json()["meeting"]["id"])[0].id
    held = _post(
        client,
        tenant,
        f"/api/v1/media/{media_id}/legal-hold",
        {"reason": "review", "review_at": _review()},
    )
    assert held.status_code == 200
    released = _post(
        client, tenant, f"/api/v1/media/{media_id}/legal-hold/release", {"reason": "done"}
    )
    assert released.status_code == 200 and released.json()["lifecycle_state"] == "active"
    assert create_object_storage(settings).exists(
        tenant["workspace_id"], _media_rows(accepted.json()["meeting"]["id"])[0].storage_key
    )
    assert _audits("media.legal_hold_released")[-1].details["deletion_pending"] is False


def test_only_the_owner_may_hold_or_release(client, email_outbox, monkeypatch):
    from backend.tests.test_authorization_matrix import _workspace_with_roles

    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"admin": "admin", "member": "member"}
    )
    tenant = {
        "user_id": owner.user_id,
        "workspace_id": owner.workspace_id,
        "cookies": owner.cookies,
    }
    accepted = _upload(client, tenant, key="hold-roles-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    media_id = _media_rows(meeting_id)[0].id
    payload = {"reason": "compliance", "review_at": _review()}

    for name in ("admin", "member"):
        refused = personas[name].post(f"/api/v1/meetings/{meeting_id}/legal-hold", json=payload)
        assert refused.status_code == 403, (name, refused.text)
        refused = personas[name].post(f"/api/v1/media/{media_id}/legal-hold", json=payload)
        assert refused.status_code == 403, (name, refused.text)
    assert _media_rows(meeting_id)[0].lifecycle_state is MediaLifecycleState.ACTIVE

    held = owner.post(f"/api/v1/media/{media_id}/legal-hold", json=payload)
    assert held.status_code == 200, held.text
    for name in ("admin", "member"):
        refused = personas[name].post(
            f"/api/v1/media/{media_id}/legal-hold/release", json={"reason": "nope"}
        )
        assert refused.status_code == 403, (name, refused.text)
    assert _media_rows(meeting_id)[0].lifecycle_state is MediaLifecycleState.LEGAL_HOLD

    # A foreign tenant's object id is not found, never held.
    other = _tenant(client, email_outbox, "hold-foreign")
    foreign = _post(client, other, f"/api/v1/media/{media_id}/legal-hold", payload)
    assert foreign.status_code == 404
    assert _media_rows(meeting_id)[0].legal_hold_by_user_id == owner.user_id


def test_overdue_reviews_are_reported_by_the_sweep_and_the_hold_stays(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hold-review")
    storage = create_object_storage(settings)
    accepted = _upload(client, tenant, key="hold-review-0001", content=OTHER_CONTENT)
    meeting_id = accepted.json()["meeting"]["id"]
    media_id = _media_rows(meeting_id)[0].id
    assert (
        _post(
            client,
            tenant,
            f"/api/v1/media/{media_id}/legal-hold",
            {"reason": "review soon", "review_at": _review(1)},
        ).status_code
        == 200
    )
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE media_objects SET legal_hold_review_at = :past WHERE id = :id"),
            {"past": datetime.now(UTC) - timedelta(days=2), "id": media_id},
        )
        db.commit()

    counts = reconcile_media_objects(
        settings,
        storage=storage,
        workspace_ids=[tenant["workspace_id"]],
        now=datetime.now(UTC) + timedelta(hours=2),
    )
    assert counts["holds_review_due"] == 1
    assert counts["pending_closed"] == 0 and counts["orphans_found"] == 0
    row = _media_rows(meeting_id)[0]
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD  # reported, never acted on
    assert storage.exists(tenant["workspace_id"], row.storage_key)
    with inspection_session_factory()() as db:
        assert db.get(MediaObject, media_id).legal_hold_review_at is not None
