"""WP07-T03: tombstone-first access denial and the durable retention purge (ADR 0021).

A committed tombstone denies every application-mediated path at once; the
durable purge then removes derived content rows and bytes (state first,
bytes second, behind the row lock), respects legal holds, survives storage
failures with a retryable state and an attempt budget, and closes with a
non-content receipt.  The retention sweep only acts on FINITE/IMMEDIATE
policies, at or after the stored expiry, once processing is terminal.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text

from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    AuditEvent,
    ConversationPurge,
    EvidenceSpan,
    JobState,
    MediaIntegrity,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    MeetingSpeaker,
    MeetingSummary,
    ProcessingJob,
    PurgeState,
    Recording,
    RetentionMode,
    ShareLink,
    StageCheckpoint,
    TranscriptRevision,
    TranscriptSegment,
    UsageEvent,
    UsageReservation,
    Workspace,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.checkpoints import sweep_abandoned_artifacts
from backend.jobs.leases import as_utc
from backend.jobs.processor import process_job
from backend.jobs.retention_purge import run_retention_purge
from backend.services.conversation_purge_service import (
    ERROR_LEGAL_HOLD,
    ERROR_STORAGE_PENDING,
    requeue_failed_purge,
    run_purge_attempt,
)
from backend.services.media_lifecycle_service import (
    MediaLifecycleError,
    delete_objects,
    media_for_meeting,
    place_legal_hold,
)
from backend.storage import StorageError, create_object_storage
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import (
    CONTENT,
    _audits,
    _get,
    _headers,
    _patch_media,
    _tenant,
    _upload,
)
from backend.tests.test_job_processor import _providers

probe_role = _rls.probe_role

CONTENT_TABLES = (
    TranscriptRevision,
    TranscriptSegment,
    AnalysisRun,
    EvidenceSpan,
    MeetingSummary,
    ActionItem,
    MeetingSpeaker,
)


# --- helpers ----------------------------------------------------------------


def _set_policy(workspace_id: str, mode: RetentionMode, days: int = 0) -> None:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, workspace_id)
        assert workspace is not None
        workspace.retention_mode = mode
        workspace.retention_days = days
        db.commit()


def _processed(client, tenant: dict, settings, *, key: str, index: int = 0) -> str:
    accepted = _upload(client, tenant, key=key, content=CONTENT + bytes([index]) * (index + 1))
    assert accepted.status_code == 202, accepted.text
    result = process_job(
        accepted.json()["job"]["id"],
        worker_id=f"purge-worker-{key}",
        settings=settings,
        providers=_providers(),
    )
    assert result["status"] == "completed", result
    return accepted.json()["meeting"]["id"]


def _content_rows(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return sum(
            db.scalar(select(func.count()).select_from(model).where(model.meeting_id == meeting_id))
            or 0
            for model in CONTENT_TABLES
        )


def _purge(meeting_id: str) -> ConversationPurge | None:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(ConversationPurge).where(ConversationPurge.meeting_id == meeting_id)
        )


def _meeting(meeting_id: str) -> Meeting:
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None
        return meeting


def _media(meeting_id: str) -> list[MediaObject]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(MediaObject)
                .where(MediaObject.meeting_id == meeting_id)
                .order_by(MediaObject.created_at)
            )
        )


def _checkpoint_keys(meeting_id: str) -> list[tuple[str, datetime | None]]:
    with inspection_session_factory()() as db:
        return [
            (row.artifact_key, row.object_deleted_at)
            for row in db.scalars(
                select(StageCheckpoint).where(StageCheckpoint.meeting_id == meeting_id)
            )
        ]


def _usage(workspace_id: str) -> tuple[list[tuple], list[tuple]]:
    with inspection_session_factory()() as db:
        events = sorted(
            (e.id, e.metric, e.value, e.event_type)
            for e in db.scalars(select(UsageEvent).where(UsageEvent.workspace_id == workspace_id))
        )
        reservations = sorted(
            (r.id, r.state.value, r.reserved_value, r.committed_value)
            for r in db.scalars(
                select(UsageReservation).where(UsageReservation.workspace_id == workspace_id)
            )
        )
        return events, reservations


def _expiry(meeting_id: str) -> datetime:
    value = as_utc(_meeting(meeting_id).retention_expires_at)
    assert value is not None
    return value


def _sweep(settings, workspace_id: str, *, now: datetime, storage=None) -> dict[str, int]:
    return run_retention_purge(settings, storage=storage, now=now, workspace_ids=[workspace_id])


class FlakyStorage:
    """Wraps real storage; ``delete`` raises a classified error while armed."""

    def __init__(self, inner, *, code: str, failures: int) -> None:
        self._inner = inner
        self.code = code
        self.failures = failures
        self.calls = 0

    def delete(self, tenant_id: str, key: str) -> bool:
        self.calls += 1
        if self.failures > 0:
            self.failures -= 1
            raise StorageError("simulated", code=self.code)
        return self._inner.delete(tenant_id, key)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def _review(days: int = 90) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


# --- tombstone-first access denial -------------------------------------------


def test_deletion_denies_every_path_at_once_and_the_purge_leaves_a_receipt_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-deny")
    workspace_id = tenant["workspace_id"]
    meeting_id = _processed(client, tenant, settings, key="purge-deny-0001")
    storage = create_object_storage(settings)
    assert _content_rows(meeting_id) > 0
    objects = [m.storage_key for m in _media(meeting_id)] + [
        key for key, _ in _checkpoint_keys(meeting_id)
    ]
    assert objects and all(storage.exists(workspace_id, key) for key in objects)

    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
        json={"public_share_links_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_headers(tenant),
        cookies=tenant["cookies"],
        json={"expires_in_hours": 24, "require_authentication": False},
    )
    assert share.status_code == 201, share.text
    token = share.json()["url"].rsplit("/", 1)[-1]
    minted = client.post(
        "/api/v1/api-keys",
        json={"name": "ci", "scopes": ["meeting:read"], "expires_in_days": 30},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert minted.status_code == 201, minted.text
    bearer = {"Authorization": f"Bearer {minted.json()['key']}"}

    paths = [
        f"/api/v1/meetings/{meeting_id}",
        f"/api/v1/meetings/{meeting_id}/transcript",
        f"/api/v1/meetings/{meeting_id}/transcript/revisions",
        f"/api/v1/meetings/{meeting_id}/summary",
        f"/api/v1/meetings/{meeting_id}/actions",
        f"/api/v1/meetings/{meeting_id}/evidence",
        f"/api/v1/meetings/{meeting_id}/quality",
        f"/api/v1/meetings/{meeting_id}/recording",
        f"/api/v1/meetings/{meeting_id}/job",
    ]
    for path in paths:
        assert _get(client, tenant, path).status_code == 200, path
    search = _get(client, tenant, "/api/v1/search", q="قرارداد")
    assert [item["id"] for item in search.json()["items"]] == [meeting_id]
    export = f"/api/v1/meetings/{meeting_id}/export"
    assert _get(client, tenant, export, format="json").status_code == 200
    assert client.get(f"/api/v1/meetings/{meeting_id}", headers=bearer).status_code == 200
    assert client.get(f"/api/v1/shares/{token}", cookies={}).status_code == 200
    usage_before = _usage(workspace_id)

    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204

    for path in paths:
        assert _get(client, tenant, path).status_code == 404, path
    assert _get(client, tenant, "/api/v1/search", q="قرارداد").json()["items"] == []
    assert _get(client, tenant, export, format="json").status_code == 404
    assert client.get(f"/api/v1/meetings/{meeting_id}", headers=bearer).status_code == 404
    assert (
        client.get(f"/api/v1/meetings/{meeting_id}/transcript", headers=bearer).status_code == 404
    )
    assert client.get(f"/api/v1/shares/{token}", cookies={}).status_code in {404, 410}
    listed = _get(client, tenant, "/api/v1/meetings")
    assert meeting_id not in {item["id"] for item in listed.json()["items"]}

    # The first purge attempt ran inside the request and finished.
    purge = _purge(meeting_id)
    assert purge is not None and purge.state is PurgeState.COMPLETED
    assert purge.reason == "meeting_deleted" and purge.attempts == 1
    assert purge.content_rows_removed > 0 and purge.objects_removed == len(objects)
    assert purge.requested_by_user_id == tenant["user_id"]
    assert _content_rows(meeting_id) == 0
    assert all(m.lifecycle_state is MediaLifecycleState.DELETED for m in _media(meeting_id))
    assert all(deleted_at is not None for _, deleted_at in _checkpoint_keys(meeting_id))
    assert not any(storage.exists(workspace_id, key) for key in objects)
    meeting = _meeting(meeting_id)
    assert meeting.deleted_at is not None and meeting.title == ""
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        assert recording is not None and recording.original_filename == ""
        assert recording.deleted_at is not None
        link = db.scalar(select(ShareLink).where(ShareLink.meeting_id == meeting_id))
        assert link is not None and link.revoked_at is not None
    # Usage and settlement are ledger history: untouched by the purge.
    assert _usage(workspace_id) == usage_before
    receipt = _audits("meeting.purge_completed")
    assert len(receipt) == 1 and receipt[0].target_id == meeting_id
    assert set(receipt[0].details) <= {
        "purge_id",
        "reason",
        "attempts",
        "content_rows_removed",
        "objects_removed",
    }

    # A second sweep finds nothing to do; the receipt is not duplicated.
    later = _sweep(settings, workspace_id, now=datetime.now(UTC) + timedelta(days=1))
    assert later["purge_attempts"] == 0 and later["expired"] == 0
    assert len(_audits("meeting.purge_completed")) == 1


# --- retention expiry ---------------------------------------------------------


def test_finite_retention_acts_at_the_boundary_and_not_before(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-finite")
    workspace_id = tenant["workspace_id"]
    _set_policy(workspace_id, RetentionMode.FINITE, 30)
    meeting_id = _processed(client, tenant, settings, key="purge-finite-0001")
    expires = _expiry(meeting_id)
    usage_before = _usage(workspace_id)

    before = _sweep(settings, workspace_id, now=expires - timedelta(seconds=1))
    assert before["expired"] == 0 and _meeting(meeting_id).deleted_at is None
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 200

    at = _sweep(settings, workspace_id, now=expires)
    assert at["expired"] == 1 and at["purges_completed"] == 1, at
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 404
    purge = _purge(meeting_id)
    assert purge is not None and purge.reason == "retention_expired"
    assert purge.state is PurgeState.COMPLETED and purge.requested_by_user_id is None
    assert _content_rows(meeting_id) == 0
    assert all(m.lifecycle_state is MediaLifecycleState.DELETED for m in _media(meeting_id))
    expired = _audits("meeting.retention_expired")
    assert expired[-1].details["retention_mode"] == "finite"
    assert _usage(workspace_id) == usage_before

    after = _sweep(settings, workspace_id, now=expires + timedelta(days=5))
    assert after["expired"] == 0 and after["purge_attempts"] == 0


def test_indefinite_and_unresolved_legacy_policies_never_purge_by_age(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-keep")
    workspace_id = tenant["workspace_id"]
    _set_policy(workspace_id, RetentionMode.FINITE, 1)
    meeting_id = _processed(client, tenant, settings, key="purge-keep-0001")
    far_future = _expiry(meeting_id) + timedelta(days=3650)
    rows = _content_rows(meeting_id)

    # A policy change outranks the expiry stamped at ingestion.
    _set_policy(workspace_id, RetentionMode.INDEFINITE)
    result = _sweep(settings, workspace_id, now=far_future)
    assert result["expired"] == 0 and _meeting(meeting_id).deleted_at is None

    # The ambiguous legacy value is never destructive either.
    _set_policy(workspace_id, RetentionMode.LEGACY_UNRESOLVED)
    result = _sweep(settings, workspace_id, now=far_future)
    assert result["expired"] == 0 and _meeting(meeting_id).deleted_at is None
    assert _content_rows(meeting_id) == rows
    assert all(m.lifecycle_state is MediaLifecycleState.ACTIVE for m in _media(meeting_id))
    assert _purge(meeting_id) is None


def test_immediate_retention_waits_for_terminal_processing(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-now")
    workspace_id = tenant["workspace_id"]
    _set_policy(workspace_id, RetentionMode.IMMEDIATE)
    accepted = _upload(client, tenant, key="purge-now-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id, job_id = accepted.json()["meeting"]["id"], accepted.json()["job"]["id"]
    now = datetime.now(UTC) + timedelta(minutes=1)

    queued = _sweep(settings, workspace_id, now=now)
    assert queued["expired"] == 0 and _meeting(meeting_id).deleted_at is None

    result = process_job(job_id, worker_id="immediate", settings=settings, providers=_providers())
    assert result["status"] == "completed"
    done = _sweep(settings, workspace_id, now=now)
    assert done["expired"] == 1 and done["purges_completed"] == 1, done
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        # The terminal processing job is neither replayed nor rewritten.
        assert job is not None and job.state is JobState.COMPLETED
    assert _content_rows(meeting_id) == 0


# --- legal hold ---------------------------------------------------------------


def test_hold_blocks_expiry_overdue_review_never_releases_and_release_resumes(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-hold")
    workspace_id = tenant["workspace_id"]
    _set_policy(workspace_id, RetentionMode.FINITE, 1)
    meeting_id = _processed(client, tenant, settings, key="purge-hold-0001")
    storage = create_object_storage(settings)
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-7", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text
    rows = _content_rows(meeting_id)
    late = _expiry(meeting_id) + timedelta(days=400)  # also past the review date

    result = _sweep(settings, workspace_id, now=late)
    assert result["expired"] == 0 and _meeting(meeting_id).deleted_at is None
    assert _content_rows(meeting_id) == rows
    media = _media(meeting_id)
    assert all(m.lifecycle_state is MediaLifecycleState.LEGAL_HOLD for m in media)
    assert all(storage.exists(workspace_id, m.storage_key) for m in media)

    # An explicit deletion of a held conversation: access is denied at once,
    # the held bytes and the derived content stay, the purge waits.
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}/transcript").status_code == 404
    purge = _purge(meeting_id)
    assert purge is not None and purge.state is PurgeState.PENDING
    assert purge.last_error == ERROR_LEGAL_HOLD and purge.attempts == 0
    assert purge.objects_held == len(media)
    assert _content_rows(meeting_id) == rows
    deferred = _sweep(settings, workspace_id, now=late + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1 and deferred["purges_failed"] == 0
    assert _content_rows(meeting_id) == rows
    assert _media(meeting_id)[0].lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    # Stage outputs are derived content: neither the purge nor the WP06
    # artifact sweep removes them while the conversation is held.
    stage_keys = [key for key, _ in _checkpoint_keys(meeting_id)]
    assert stage_keys
    sweep_abandoned_artifacts(settings, now=late + timedelta(days=2))
    assert all(gone is None for _, gone in _checkpoint_keys(meeting_id))
    assert all(storage.exists(workspace_id, key) for key in stage_keys)

    # Release (audited, owner only) resumes the recorded deletion; the next
    # sweep finishes the purge.
    for m in media:
        released = client.post(
            f"/api/v1/media/{m.id}/legal-hold/release",
            json={"reason": "matter closed"},
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert released.status_code == 200, released.text
    final = _sweep(settings, workspace_id, now=datetime.now(UTC) + timedelta(seconds=1))
    assert final["purges_completed"] == 1, final
    assert _content_rows(meeting_id) == 0
    assert not any(storage.exists(workspace_id, m.storage_key) for m in media)
    assert not any(storage.exists(workspace_id, key) for key in stage_keys)
    assert _purge(meeting_id).state is PurgeState.COMPLETED


def test_a_hold_committed_after_the_rows_were_loaded_still_keeps_the_bytes(
    client, email_outbox, monkeypatch, settings
):
    """The stale-scan race: the deleter's copy says deletion_pending, a hold
    commits in between, and the locked re-read at the point of no return wins."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-stale")
    workspace_id = tenant["workspace_id"]
    accepted = _upload(client, tenant, key="purge-stale-0001")
    meeting_id = accepted.json()["meeting"]["id"]
    storage = create_object_storage(settings)
    factory = inspection_session_factory()
    with factory() as db:
        for row in db.scalars(select(MediaObject).where(MediaObject.meeting_id == meeting_id)):
            row.lifecycle_state = MediaLifecycleState.DELETION_PENDING
            row.deletion_requested_at = datetime.now(UTC)
            row.deletion_reason = "meeting_deleted"
        db.commit()

    with factory() as deleter, factory() as holder:
        bind_tenant_context(deleter, workspace_id=workspace_id)
        bind_tenant_context(holder, workspace_id=workspace_id)
        stale = media_for_meeting(deleter, workspace_id=workspace_id, meeting_id=meeting_id)
        assert stale[0].lifecycle_state is MediaLifecycleState.DELETION_PENDING
        fresh = media_for_meeting(
            holder, workspace_id=workspace_id, meeting_id=meeting_id, for_update=True
        )
        place_legal_hold(
            holder,
            media=fresh[0],
            reason="late hold",
            review_at=datetime.now(UTC) + timedelta(days=5),
            actor_user_id=tenant["user_id"],
            now=datetime.now(UTC),
            settings=settings,
        )
        holder.commit()
        closed = delete_objects(
            deleter, storage, objects=stale, now=datetime.now(UTC), settings=settings
        )
        deleter.commit()
    assert closed == []
    row = _media(meeting_id)[0]
    assert row.lifecycle_state is MediaLifecycleState.LEGAL_HOLD
    assert storage.exists(workspace_id, row.storage_key)


# --- storage failures, retries and the attempt budget -------------------------


@pytest.mark.parametrize("code", ["s3_access_denied", "s3_unavailable"])
def test_storage_failure_keeps_a_retryable_state_and_a_retry_completes(
    client, email_outbox, monkeypatch, settings, code
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, f"purge-flaky-{code[-6:]}")
    workspace_id = tenant["workspace_id"]
    meeting_id = _processed(client, tenant, settings, key=f"purge-flaky-{code}")
    real = create_object_storage(settings)
    flaky = FlakyStorage(real, code=code, failures=10_000)
    monkeypatch.setattr("backend.routers.v1.meetings.create_object_storage", lambda _s: flaky)

    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    assert _get(client, tenant, f"/api/v1/meetings/{meeting_id}").status_code == 404
    purge = _purge(meeting_id)
    assert purge is not None and purge.state is PurgeState.PENDING
    assert purge.attempts == 1 and purge.last_error == ERROR_STORAGE_PENDING
    assert purge.objects_pending > 0
    # Content rows are gone (database); the bytes are not, and a failed
    # removal is never read as "missing" or "deleted".
    assert _content_rows(meeting_id) == 0
    media = _media(meeting_id)
    assert all(m.lifecycle_state is MediaLifecycleState.DELETION_PENDING for m in media)
    assert all(m.integrity is not MediaIntegrity.MISSING for m in media)
    assert all(real.exists(workspace_id, m.storage_key) for m in media)

    # Not due yet: the backoff is respected.
    early = _sweep(settings, workspace_id, now=purge.next_attempt_at - timedelta(seconds=1))
    assert early["purge_attempts"] == 0
    # Still failing: the attempt is counted and rescheduled with a longer delay.
    due = as_utc(purge.next_attempt_at)
    again = _sweep(settings, workspace_id, now=due, storage=flaky)
    assert again["purges_retry"] == 1
    second = _purge(meeting_id)
    assert second.attempts == 2
    assert as_utc(second.next_attempt_at) - due > timedelta(
        seconds=settings.retention_purge_retry_base_seconds
    )
    # Storage recovers: the retry completes and closes every row.
    flaky.failures = 0
    done = _sweep(settings, workspace_id, now=as_utc(second.next_attempt_at), storage=flaky)
    assert done["purges_completed"] == 1, done
    assert all(m.lifecycle_state is MediaLifecycleState.DELETED for m in _media(meeting_id))
    assert not any(real.exists(workspace_id, m.storage_key) for m in media)


def test_exhausted_attempts_fail_loudly_and_an_operator_requeue_finishes(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tight = settings.model_copy(update={"retention_purge_max_attempts": 2})
    tenant = _tenant(client, email_outbox, "purge-exhaust")
    workspace_id = tenant["workspace_id"]
    meeting_id = _processed(client, tenant, settings, key="purge-exhaust-0001")
    real = create_object_storage(settings)
    flaky = FlakyStorage(real, code="s3_delete_failed", failures=10_000)
    monkeypatch.setattr("backend.routers.v1.meetings.create_object_storage", lambda _s: flaky)
    assert (
        client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
        ).status_code
        == 204
    )
    now = as_utc(_purge(meeting_id).next_attempt_at)
    result = _sweep(tight, workspace_id, now=now, storage=flaky)
    assert result["purges_failed"] == 1 and result["purges_failed_open"] == 1, result
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.FAILED and purge.attempts == 2
    failed = _audits("meeting.purge_failed")
    assert failed and failed[-1].details["last_error"] == ERROR_STORAGE_PENDING
    # FAILED is terminal for the sweep: no further automatic attempts.
    idle = _sweep(tight, workspace_id, now=now + timedelta(days=30), storage=flaky)
    assert idle["purge_attempts"] == 0 and idle["purges_failed_open"] == 1

    with inspection_session_factory()() as db:
        row = db.get(ConversationPurge, purge.id)
        assert requeue_failed_purge(db, purge=row, actor_user_id=None, now=now, settings=settings)
        db.commit()
    flaky.failures = 0
    done = _sweep(tight, workspace_id, now=now + timedelta(seconds=1), storage=flaky)
    assert done["purges_completed"] == 1, done
    assert _audits("meeting.purge_requeued")


# --- tenancy ------------------------------------------------------------------


def test_a_sweep_or_attempt_for_one_tenant_never_touches_another(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    owner = _tenant(client, email_outbox, "purge-a")
    _set_policy(owner["workspace_id"], RetentionMode.FINITE, 1)
    meeting_id = _processed(client, owner, settings, key="purge-a-0001")
    other = _tenant(client, email_outbox, "purge-b")
    _set_policy(other["workspace_id"], RetentionMode.FINITE, 1)
    late = _expiry(meeting_id) + timedelta(days=10)

    foreign = _sweep(settings, other["workspace_id"], now=late)
    assert foreign["expired"] == 0 and _meeting(meeting_id).deleted_at is None
    denied = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(other), cookies=other["cookies"]
    )
    assert denied.status_code == 404

    # A purge id of tenant A presented under tenant B's binding is not claimed.
    assert (
        client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(owner), cookies=owner["cookies"]
        ).status_code
        == 204
    )
    purge = _purge(meeting_id)
    with inspection_session_factory()() as db:
        row = db.get(ConversationPurge, purge.id)
        row.state = PurgeState.PENDING
        row.completed_at = None
        db.commit()
    with inspection_session_factory()() as db:
        bind_tenant_context(db, workspace_id=other["workspace_id"])
        outcome = run_purge_attempt(
            db,
            create_object_storage(settings),
            purge_id=purge.id,
            workspace_id=other["workspace_id"],
            now=late,
            settings=settings,
        )
    assert outcome.skipped


# --- races (PostgreSQL: real row locks) ----------------------------------------


@pytest.mark.postgres
def test_simultaneous_attempts_claim_a_purge_once(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-race")
    workspace_id = tenant["workspace_id"]
    meeting_id = _processed(client, tenant, settings, key="purge-race-0001")
    real = create_object_storage(settings)
    gate = threading.Event()
    entered = threading.Event()

    class SlowStorage(FlakyStorage):
        def delete(self, tenant_id, key):
            entered.set()
            assert gate.wait(10)
            return super().delete(tenant_id, key)

    # Tombstone without the inline attempt so both workers race on a due row.
    monkeypatch.setattr("backend.routers.v1.meetings.run_purge_attempt", lambda *_a, **_k: None)
    assert (
        client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
        ).status_code
        == 204
    )
    purge_id = _purge(meeting_id).id
    now = datetime.now(UTC) + timedelta(seconds=1)
    outcomes: list = []
    errors: list[BaseException] = []

    def worker(storage) -> None:
        try:
            with inspection_session_factory()() as db:
                bind_tenant_context(db, workspace_id=workspace_id)
                outcomes.append(
                    run_purge_attempt(
                        db,
                        storage,
                        purge_id=purge_id,
                        workspace_id=workspace_id,
                        now=now,
                        settings=settings,
                    )
                )
        except BaseException as exc:  # surfaced below
            errors.append(exc)

    slow = threading.Thread(target=worker, args=(SlowStorage(real, code="x", failures=0),))
    slow.start()
    assert entered.wait(10)  # the first worker holds the claim and a media row lock
    fast = threading.Thread(target=worker, args=(real,))
    fast.start()
    fast.join(10)
    gate.set()
    slow.join(10)
    assert not errors, errors
    assert sorted(o.skipped for o in outcomes) == [False, True]
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.COMPLETED and purge.attempts == 1
    assert len(_audits("meeting.purge_completed")) == 1


@pytest.mark.postgres
def test_a_hold_racing_the_physical_removal_waits_and_finds_it_deleted(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-norestore")
    workspace_id = tenant["workspace_id"]
    accepted = _upload(client, tenant, key="purge-norestore-01")
    meeting_id = accepted.json()["meeting"]["id"]
    real = create_object_storage(settings)
    factory = inspection_session_factory()
    with factory() as db:
        for row in db.scalars(select(MediaObject).where(MediaObject.meeting_id == meeting_id)):
            row.lifecycle_state = MediaLifecycleState.DELETION_PENDING
            row.deletion_requested_at = datetime.now(UTC)
            row.deletion_reason = "meeting_deleted"
        db.commit()
    gate, entered = threading.Event(), threading.Event()
    results: dict[str, object] = {}

    class SlowStorage(FlakyStorage):
        def delete(self, tenant_id, key):
            entered.set()
            assert gate.wait(10)
            return super().delete(tenant_id, key)

    def remover() -> None:
        with factory() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            rows = media_for_meeting(db, workspace_id=workspace_id, meeting_id=meeting_id)
            results["closed"] = delete_objects(
                db,
                SlowStorage(real, code="x", failures=0),
                objects=rows,
                now=datetime.now(UTC),
                settings=settings,
            )
            db.commit()

    def holder() -> None:
        with factory() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            rows = media_for_meeting(
                db, workspace_id=workspace_id, meeting_id=meeting_id, for_update=True
            )
            try:
                place_legal_hold(
                    db,
                    media=rows[0],
                    reason="too late",
                    review_at=datetime.now(UTC) + timedelta(days=5),
                    actor_user_id=tenant["user_id"],
                    now=datetime.now(UTC),
                    settings=settings,
                )
                db.commit()
                results["hold"] = "placed"
            except MediaLifecycleError as exc:
                db.rollback()
                results["hold"] = exc.code

    first = threading.Thread(target=remover)
    first.start()
    assert entered.wait(10)
    second = threading.Thread(target=holder)
    second.start()
    second.join(0.5)
    assert second.is_alive()  # blocked on the row lock at the point of no return
    gate.set()
    first.join(10)
    second.join(10)
    assert results["hold"] == "already_deleted"
    assert len(results["closed"]) == 1
    assert _media(meeting_id)[0].lifecycle_state is MediaLifecycleState.DELETED


def test_deletion_during_processing_never_republishes_content(
    client, email_outbox, monkeypatch, settings
):
    """A stage that finishes after the tombstone cannot recreate results."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-publish")
    workspace_id = tenant["workspace_id"]
    accepted = _upload(client, tenant, key="purge-publish-01")
    meeting_id, job_id = accepted.json()["meeting"]["id"], accepted.json()["job"]["id"]
    providers = _providers()
    real_transcribe = providers.asr.transcribe

    def delete_now() -> None:
        deleted = client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
        )
        assert deleted.status_code == 204

    async def transcribe_then_delete(request):
        result = await real_transcribe(request)
        # The deletion commits while this attempt still holds its results.
        remover = threading.Thread(target=delete_now)
        remover.start()
        remover.join(30)
        return result

    monkeypatch.setattr(providers.asr, "transcribe", transcribe_then_delete)
    result = process_job(job_id, worker_id="racer", settings=settings, providers=providers)
    assert result["status"] != "completed"
    assert _content_rows(meeting_id) == 0
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.state is JobState.CANCELLED
        published = db.scalar(
            select(func.count(StageCheckpoint.id)).where(
                StageCheckpoint.meeting_id == meeting_id,
                StageCheckpoint.published_at.is_not(None),
                StageCheckpoint.invalidated_at.is_(None),
            )
        )
        assert published == 0
    swept = _sweep(settings, workspace_id, now=datetime.now(UTC) + timedelta(hours=1))
    assert _purge(meeting_id).state is PurgeState.COMPLETED, swept
    assert _content_rows(meeting_id) == 0
    assert all(m.lifecycle_state is MediaLifecycleState.DELETED for m in _media(meeting_id))


@pytest.mark.postgres
def test_rls_binds_purge_rows_to_the_tenant(client, email_outbox, monkeypatch, probe_role):
    _patch_media(monkeypatch)
    one = _tenant(client, email_outbox, "purge-rls-one")
    two = _tenant(client, email_outbox, "purge-rls-two")
    accepted = _upload(client, one, key="purge-rls-0001")
    meeting_id = accepted.json()["meeting"]["id"]
    assert (
        client.delete(
            f"/api/v1/meetings/{meeting_id}", headers=_headers(one), cookies=one["cookies"]
        ).status_code
        == 204
    )
    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            assert _rls._count(connection, "conversation_purges") == 0
            _rls._bind(connection, workspace_id=two["workspace_id"])
            assert _rls._count(connection, "conversation_purges") == 0
            touched = connection.execute(
                text("UPDATE conversation_purges SET state = 'PENDING' WHERE meeting_id = :m"),
                {"m": meeting_id},
            )
            assert touched.rowcount == 0
            connection.rollback()
            _rls._bind(connection, workspace_id=one["workspace_id"])
            assert _rls._count(connection, "conversation_purges") == 1
            connection.rollback()
    finally:
        engine.dispose()


def test_audits_of_the_purge_carry_no_content(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "purge-audit")
    meeting_id = _processed(client, tenant, settings, key="purge-audit-0001")
    client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    with inspection_session_factory()() as db:
        events = list(
            db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("meeting.purge%")))
        )
    assert events
    for event in events:
        blob = repr(event.details)
        assert "قرارداد" not in blob and "Weekly customer call" not in blob
