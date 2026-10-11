"""HOM-183: the workspace administrator's list of processing jobs.

``GET /admin/jobs`` pages the workspace's jobs newest first, with state and
stage filters and a ``replayable`` flag that follows the replay rules of
``jobs/replay.py``; ``GET /admin/jobs/summary`` counts them per state.  Both
need ``workspace:manage``, are bound to the caller's workspace and leave out
jobs of soft-deleted meetings.  Most jobs are seeded with DBA writes: the
demo plan allows one concurrent job, and these cases need many jobs in fixed
states and at fixed times.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import event, select

from backend.database.relational import get_engine
from backend.database.saas_models import (
    Entitlement,
    JobState,
    Meeting,
    MeetingStatus,
    Membership,
    ProcessingJob,
    Recording,
    UsageReservation,
)
from backend.jobs import outbox
from backend.services.admin_jobs_service import job_is_replayable, sanitize_failure_message
from backend.tests._database import inspection_session_factory
from backend.tests.test_admin_api import _register_and_login, _session_cookies
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_dispatch_outbox import FakeBroker, _meeting_of
from backend.tests.test_job_failure_policy import _dead_letter_after_one_attempt, _replay

LIST = "/api/v1/admin/jobs"
SUMMARY = "/api/v1/admin/jobs/summary"
BASE_TIME = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
ITEM_KEYS = {
    "id",
    "meeting_id",
    "meeting_title",
    "state",
    "stage",
    "attempt_count",
    "max_attempts",
    "failure_code",
    "failure_class",
    "failure_message",
    "replay_count",
    "max_replays",
    "replayable",
    "created_at",
    "updated_at",
}
STATE_KEYS = {state.value for state in JobState}


# --- helpers -----------------------------------------------------------------


def _owner(client, email_outbox, email: str) -> tuple[str, dict[str, str]]:
    _, workspace_id, cookies = _register_and_login(client, email_outbox, email=email)
    return workspace_id, cookies


def _get(client, path: str, workspace_id: str, cookies: dict[str, str], **params):
    return client.get(
        path, params=params, cookies=cookies, headers={"X-Workspace-ID": workspace_id}
    )


def _ids(response) -> list[str]:
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def _flags(client, workspace_id: str, cookies: dict[str, str]) -> dict[str, bool]:
    response = _get(client, LIST, workspace_id, cookies, limit=100)
    assert response.status_code == 200, response.text
    return {item["id"]: item["replayable"] for item in response.json()["items"]}


def _walk(client, workspace_id: str, cookies: dict[str, str], **params) -> list[str]:
    """Follow ``next_cursor`` to the last page; return every id in order."""
    ids: list[str] = []
    cursor: str | None = None
    for _ in range(50):
        query = dict(params)
        if cursor is not None:
            query["cursor"] = cursor
        response = _get(client, LIST, workspace_id, cookies, **query)
        assert response.status_code == 200, response.text
        body = response.json()
        ids.extend(item["id"] for item in body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            return ids
        assert isinstance(cursor, str) and body["items"]
    raise AssertionError("paging did not reach the last page")


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _instant(value: str) -> datetime:
    """A response timestamp as UTC (the SQLite fallback returns naive values)."""
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _seed_job(
    workspace_id: str,
    *,
    state: JobState = JobState.DEAD_LETTER,
    stage: str = "transcription",
    created_at: datetime = BASE_TIME,
    replay_count: int = 0,
    failure_message: str | None = None,
    title: str = "Seeded meeting",
    meeting_deleted: bool = False,
    retention_expires_at: datetime | None = None,
    recording: str = "live",
) -> str:
    """One job with its own meeting, recording and reservation (DBA write).

    ``recording`` is ``"live"``, ``"deleted"`` (soft-deleted) or ``"missing"``.
    """
    now = datetime.now(UTC)
    failed = state in (JobState.FAILED, JobState.DEAD_LETTER)
    running = state == JobState.RUNNING
    with inspection_session_factory()() as db:
        owner_id = db.scalar(
            select(Membership.user_id)
            .where(Membership.workspace_id == workspace_id)
            .order_by(Membership.created_at.asc())
        )
        entitlement_id = db.scalar(
            select(Entitlement.id).where(
                Entitlement.workspace_id == workspace_id, Entitlement.metric == "audio_seconds"
            )
        )
        assert owner_id is not None and entitlement_id is not None
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=owner_id,
            title=title,
            status=MeetingStatus.FAILED if failed else MeetingStatus.QUEUED,
            duration_seconds=30,
            retention_expires_at=retention_expires_at,
            deleted_at=now if meeting_deleted else None,
        )
        db.add(meeting)
        db.flush()
        if recording != "missing":
            db.add(
                Recording(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    storage_key=f"seed/{uuid.uuid4()}",
                    original_filename="seed.wav",
                    media_type="audio/wav",
                    container_format="wav",
                    size_bytes=16,
                    checksum_sha256="0" * 64,
                    duration_seconds=30,
                    deleted_at=now if recording == "deleted" else None,
                )
            )
        reservation = UsageReservation(
            workspace_id=workspace_id,
            entitlement_id=entitlement_id,
            meeting_id=meeting.id,
            idempotency_key=f"seed:{uuid.uuid4()}",
            reserved_value=30,
        )
        db.add(reservation)
        db.flush()
        job = ProcessingJob(
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            reservation_id=reservation.id,
            idempotency_key=f"seed:{uuid.uuid4()}",
            state=state,
            stage=stage,
            attempt_count=1,
            max_attempts=3,
            failure_code="asr_unavailable" if failed else None,
            failure_class="transient" if failed else None,
            failure_message=failure_message,
            replay_count=replay_count,
            locked_by="seed-worker" if running else None,
            lease_expires_at=now + timedelta(minutes=5) if running else None,
            created_at=created_at,
            updated_at=created_at,
        )
        db.add(job)
        db.commit()
        return job.id


# --- shape and order ---------------------------------------------------------


def test_list_returns_typed_items_newest_first_and_nothing_else(client, email_outbox, settings):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-shape@example.com")
    older = _seed_job(
        workspace_id,
        state=JobState.COMPLETED,
        stage="completed",
        created_at=BASE_TIME,
        title="Older call",
    )
    newer = _seed_job(
        workspace_id,
        created_at=BASE_TIME + timedelta(minutes=5),
        replay_count=1,
        failure_message="ASR provider timed out",
        title="Newer call",
    )

    response = _get(client, LIST, workspace_id, cookies)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["next_cursor"] is None
    assert [item["id"] for item in body["items"]] == [newer, older]

    item = body["items"][0]
    assert set(item) == ITEM_KEYS
    assert {key: item[key] for key in ITEM_KEYS - {"created_at", "updated_at"}} == {
        "id": newer,
        "meeting_id": _meeting_of(newer),
        "meeting_title": "Newer call",
        "state": "dead_letter",
        "stage": "transcription",
        "attempt_count": 1,
        "max_attempts": 3,
        "failure_code": "asr_unavailable",
        "failure_class": "transient",
        "failure_message": "ASR provider timed out",
        "replay_count": 1,
        "max_replays": settings.job_max_replays,
        "replayable": True,
    }
    assert _instant(item["created_at"]) == BASE_TIME + timedelta(minutes=5)
    assert _instant(item["updated_at"]) == BASE_TIME + timedelta(minutes=5)

    completed = body["items"][1]
    assert set(completed) == ITEM_KEYS
    assert completed["state"] == "completed" and completed["replayable"] is False
    assert completed["failure_code"] is None and completed["failure_message"] is None


def test_an_empty_workspace_lists_nothing(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-empty@example.com")
    response = _get(client, LIST, workspace_id, cookies)
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "next_cursor": None}


# --- filters -----------------------------------------------------------------


def test_state_and_stage_filters_combine_with_and(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-filters@example.com")
    failed_asr = _seed_job(workspace_id, state=JobState.FAILED, stage="transcription")
    failed_ai = _seed_job(workspace_id, state=JobState.FAILED, stage="analysis")
    parked_asr = _seed_job(workspace_id, state=JobState.DEAD_LETTER, stage="transcription")
    parked_ai = _seed_job(workspace_id, state=JobState.DEAD_LETTER, stage="analysis")
    queued = _seed_job(workspace_id, state=JobState.QUEUED, stage="queued")
    done = _seed_job(workspace_id, state=JobState.COMPLETED, stage="completed")

    def listed(**params) -> set[str]:
        return set(_ids(_get(client, LIST, workspace_id, cookies, limit=100, **params)))

    everything = {failed_asr, failed_ai, parked_asr, parked_ai, queued, done}
    assert listed() == everything
    assert listed(state="dead_letter") == {parked_asr, parked_ai}
    assert listed(state="queued") == {queued}
    assert listed(state="running") == set()
    assert listed(state=["failed", "dead_letter"]) == {failed_asr, failed_ai, parked_asr, parked_ai}
    assert listed(stage="analysis") == {failed_ai, parked_ai}
    assert listed(stage="no-such-stage") == set()
    assert listed(state="dead_letter", stage="transcription") == {parked_asr}
    assert listed(state=["failed", "completed"], stage="completed") == {done}

    for bad in ({"state": "bogus"}, {"state": ["dead_letter", "bogus"]}, {"state": "DEAD_LETTER"}):
        refused = _get(client, LIST, workspace_id, cookies, **bad)
        assert refused.status_code == 422, (bad, refused.text)
        assert _error_code(refused) == "validation_error"
    for bad_stage in ("", "s" * 41):
        refused = _get(client, LIST, workspace_id, cookies, stage=bad_stage)
        assert refused.status_code == 422, (bad_stage, refused.text)


# --- paging ------------------------------------------------------------------


def test_paging_returns_every_job_once_including_created_at_ties(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-paging@example.com")
    tie_time = BASE_TIME + timedelta(minutes=10)
    newest = _seed_job(workspace_id, created_at=BASE_TIME + timedelta(minutes=20))
    ties = [
        _seed_job(workspace_id, state=state, created_at=tie_time)
        for state in (JobState.FAILED, JobState.DEAD_LETTER, JobState.COMPLETED, JobState.FAILED)
    ]
    older = _seed_job(workspace_id, state=JobState.FAILED, created_at=BASE_TIME)
    oldest = _seed_job(workspace_id, created_at=BASE_TIME - timedelta(days=1))
    expected = [newest, *sorted(ties, reverse=True), older, oldest]

    single_page = _get(client, LIST, workspace_id, cookies, limit=100)
    assert _ids(single_page) == expected
    assert single_page.json()["next_cursor"] is None

    for limit in (1, 2, 3, 6):
        assert _walk(client, workspace_id, cookies, limit=limit) == expected, limit

    exact = _get(client, LIST, workspace_id, cookies, limit=7)
    assert _ids(exact) == expected and exact.json()["next_cursor"] is None
    one_short = _get(client, LIST, workspace_id, cookies, limit=6)
    assert _ids(one_short) == expected[:6]
    assert isinstance(one_short.json()["next_cursor"], str)

    # the same filters on every page: each matching job exactly once
    failing = {ties[0], ties[1], ties[3], older, newest, oldest}
    walked = _walk(client, workspace_id, cookies, limit=1, state=["failed", "dead_letter"])
    assert walked == [job for job in expected if job in failing]
    walked_failed = _walk(client, workspace_id, cookies, limit=1, state="failed")
    assert walked_failed == [job for job in expected if job in {ties[0], ties[3], older}]


def test_invalid_cursors_and_limits_are_refused(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-cursor@example.com")
    first = _seed_job(workspace_id, created_at=BASE_TIME + timedelta(minutes=1))
    second = _seed_job(workspace_id, created_at=BASE_TIME)

    def encoded(payload: object) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    stamp = BASE_TIME.isoformat()
    bad_cursors = [
        "not-a-cursor!!",
        "AAAA",
        encoded(["v", 1]),
        # an audit-events cursor is not a jobs cursor
        encoded({"v": 1, "occurred_at": stamp, "id": str(uuid.uuid4())}),
        encoded({"v": 2, "created_at": stamp, "id": str(uuid.uuid4())}),
        encoded({"v": 1, "created_at": stamp, "id": "not-a-uuid"}),
        encoded({"v": 1, "created_at": "2026-01-01T09:00:00", "id": str(uuid.uuid4())}),
        encoded({"v": 1, "created_at": "yesterday", "id": str(uuid.uuid4())}),
        encoded({"v": 1, "created_at": stamp, "id": str(uuid.uuid4()), "extra": 1}),
    ]
    for cursor in bad_cursors:
        refused = _get(client, LIST, workspace_id, cookies, cursor=cursor)
        assert refused.status_code == 400, (cursor, refused.text)
        assert _error_code(refused) == "invalid_cursor"

    for cursor in ("", "a" * 513):
        assert _get(client, LIST, workspace_id, cookies, cursor=cursor).status_code == 422
    for limit in (0, 101, -1, "many"):
        refused = _get(client, LIST, workspace_id, cookies, limit=limit)
        assert refused.status_code == 422, (limit, refused.text)
        assert _error_code(refused) == "validation_error"
    for limit in (1, 100):
        assert _get(client, LIST, workspace_id, cookies, limit=limit).status_code == 200

    # allow-control: a real next_cursor gives the next page
    page = _get(client, LIST, workspace_id, cookies, limit=1)
    assert _ids(page) == [first]
    following = _get(
        client, LIST, workspace_id, cookies, limit=1, cursor=page.json()["next_cursor"]
    )
    assert _ids(following) == [second]
    assert following.json()["next_cursor"] is None


# --- replayable flag ---------------------------------------------------------


def test_replayable_flag_needs_every_replay_rule(client, email_outbox, settings):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-replayable@example.com")
    limit = settings.job_max_replays
    now = datetime.now(UTC)
    expected = {
        _seed_job(workspace_id, replay_count=limit - 1): True,
        _seed_job(workspace_id, retention_expires_at=now + timedelta(days=1)): True,
        _seed_job(workspace_id, replay_count=limit): False,
        _seed_job(workspace_id, replay_count=limit + 1): False,
        _seed_job(workspace_id, retention_expires_at=now - timedelta(minutes=1)): False,
        _seed_job(workspace_id, recording="deleted"): False,
        _seed_job(workspace_id, recording="missing"): False,
    }
    for state in JobState:
        if state != JobState.DEAD_LETTER:
            expected[_seed_job(workspace_id, state=state)] = False
    # a deleted meeting's job is never listed, so it can never look replayable
    deleted = _seed_job(workspace_id, meeting_deleted=True)

    flags = _flags(client, workspace_id, cookies)
    assert flags == expected
    assert deleted not in flags


def test_replayable_flag_reads_the_replay_limit_from_settings(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-limit@example.com")
    fresh = _seed_job(workspace_id, replay_count=0)
    replayed_once = _seed_job(workspace_id, replay_count=1)
    monkeypatch.setattr(settings, "job_max_replays", 1)

    response = _get(client, LIST, workspace_id, cookies)
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[fresh]["replayable"] is True and items[fresh]["max_replays"] == 1
    assert items[replayed_once]["replayable"] is False


def test_replayable_helper_refuses_each_rule_on_its_own():
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    def flag(
        *,
        state: JobState = JobState.DEAD_LETTER,
        replay_count: int = 2,
        meeting: str = "live",
        retention: datetime | None = None,
        recording: str = "live",
    ) -> bool:
        job = ProcessingJob(state=state, replay_count=replay_count)
        meeting_row = (
            None
            if meeting == "missing"
            else Meeting(
                deleted_at=now if meeting == "deleted" else None,
                retention_expires_at=retention,
            )
        )
        recording_row = (
            None
            if recording == "missing"
            else Recording(deleted_at=now if recording == "deleted" else None)
        )
        return job_is_replayable(job, meeting_row, recording_row, max_replays=3, now=now)

    assert flag() is True
    assert flag(retention=now + timedelta(seconds=1)) is True
    # naive timestamps (SQLite) are read as UTC
    assert flag(retention=(now + timedelta(hours=1)).replace(tzinfo=None)) is True
    for state in JobState:
        if state != JobState.DEAD_LETTER:
            assert flag(state=state) is False, state
    assert flag(replay_count=3) is False
    assert flag(meeting="missing") is False
    assert flag(meeting="deleted") is False
    assert flag(retention=now) is False  # the replay route refuses at the instant itself
    assert flag(retention=now - timedelta(seconds=1)) is False
    assert flag(retention=(now - timedelta(hours=1)).replace(tzinfo=None)) is False
    assert flag(recording="missing") is False
    assert flag(recording="deleted") is False


def test_replayable_flag_agrees_with_the_replay_route(client, email_outbox, monkeypatch, settings):
    monkeypatch.setattr(outbox, "publish_processing_job", FakeBroker())
    parked, workspace_id = _dead_letter_after_one_attempt(
        client, email_outbox, monkeypatch, settings
    )
    cookies = _session_cookies(client)
    refused = {
        _seed_job(workspace_id, state=JobState.FAILED): "job_not_replayable",
        _seed_job(workspace_id, replay_count=settings.job_max_replays): "replay_limit_reached",
        _seed_job(
            workspace_id, retention_expires_at=datetime.now(UTC) - timedelta(minutes=1)
        ): "retention_expired",
        _seed_job(workspace_id, recording="deleted"): "recording_missing",
        _seed_job(workspace_id, recording="missing"): "recording_missing",
    }
    deleted = _seed_job(workspace_id, meeting_deleted=True)

    flags = _flags(client, workspace_id, cookies)
    assert flags[parked] is True
    for job_id, code in refused.items():
        assert flags[job_id] is False
        response = _replay(client, workspace_id, job_id, cookies=cookies)
        assert response.status_code == 409, response.text
        assert _error_code(response) == code
    assert deleted not in flags
    gone = _replay(client, workspace_id, deleted, cookies=cookies)
    assert gone.status_code == 409 and _error_code(gone) == "meeting_deleted"

    accepted = _replay(client, workspace_id, parked, cookies=cookies)
    assert accepted.status_code == 202, accepted.text
    after = _get(client, LIST, workspace_id, cookies, state="queued")
    assert _ids(after) == [parked]
    assert after.json()["items"][0]["replayable"] is False
    assert after.json()["items"][0]["replay_count"] == 1


# --- failure message ---------------------------------------------------------


def test_failure_message_is_redacted_when_it_is_a_secret(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-redact@example.com")
    # built at runtime from short parts so no literal looks like a real secret
    bearer = "Bearer " + "abc" * 4
    jwt = ".".join(["eyJ" + "hbGciOiJub25lIn0", "eyJ" + "zdWIiOiIxIn0", "c2ln" + "bmF0dXJl"])
    api_key = "sk-" + "x" * 12
    address = ".".join(["203", "0", "113", "7"])
    plain = "ASR provider timed out"
    jobs = {
        _seed_job(workspace_id, failure_message=bearer): "[redacted]",
        _seed_job(workspace_id, failure_message=jwt): "[redacted]",
        _seed_job(workspace_id, failure_message=api_key): "[redacted]",
        _seed_job(workspace_id, failure_message=address): "[redacted]",
        _seed_job(workspace_id, failure_message=plain): plain,
        _seed_job(workspace_id, failure_message="x" * 500): "x" * 500,
        _seed_job(workspace_id, failure_message=None): None,
    }

    response = _get(client, LIST, workspace_id, cookies)
    assert response.status_code == 200, response.text
    messages = {item["id"]: item["failure_message"] for item in response.json()["items"]}
    assert messages == jobs
    for secret in (bearer, jwt, api_key, address):
        assert secret not in response.text


def test_failure_message_sanitizer_uses_the_audit_string_rule():
    assert sanitize_failure_message(None) is None
    assert sanitize_failure_message("ASR provider timed out") == "ASR provider timed out"
    assert sanitize_failure_message("y" * 600) == "y" * 500
    assert sanitize_failure_message("  Bearer " + "abc" * 4 + "  ") == "[redacted]"
    assert sanitize_failure_message("sk_" + "z" * 10) == "[redacted]"
    assert sanitize_failure_message("::1") == "[redacted]"


# --- soft-deleted meetings ---------------------------------------------------


def test_jobs_of_soft_deleted_meetings_are_not_listed_or_counted(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-deleted@example.com")
    live_parked = _seed_job(workspace_id, title="Live")
    live_done = _seed_job(workspace_id, state=JobState.COMPLETED)
    hidden = {
        _seed_job(workspace_id, meeting_deleted=True),
        _seed_job(workspace_id, state=JobState.COMPLETED, meeting_deleted=True),
        _seed_job(workspace_id, state=JobState.FAILED, meeting_deleted=True),
    }

    assert set(_walk(client, workspace_id, cookies, limit=1)) == {live_parked, live_done}
    parked = _get(client, LIST, workspace_id, cookies, state="dead_letter")
    assert _ids(parked) == [live_parked]
    failed = _get(client, LIST, workspace_id, cookies, state="failed")
    assert _ids(failed) == []
    for job_id in hidden:
        assert job_id not in parked.text

    summary = _get(client, SUMMARY, workspace_id, cookies)
    assert summary.status_code == 200, summary.text
    assert summary.json()["counts"] == {
        "queued": 0,
        "running": 0,
        "completed": 1,
        "failed": 0,
        "cancelled": 0,
        "dead_letter": 1,
    }
    assert summary.json()["total"] == 2


# --- summary -----------------------------------------------------------------


def test_summary_counts_every_state_and_is_not_a_job_id(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-summary@example.com")

    empty = _get(client, SUMMARY, workspace_id, cookies)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"counts": {key: 0 for key in STATE_KEYS}, "total": 0}

    planned = {
        JobState.QUEUED: 2,
        JobState.RUNNING: 1,
        JobState.COMPLETED: 3,
        JobState.FAILED: 1,
        JobState.CANCELLED: 1,
        JobState.DEAD_LETTER: 2,
    }
    seeded = [
        _seed_job(workspace_id, state=state)
        for state, count in planned.items()
        for _ in range(count)
    ]

    summary = _get(client, SUMMARY, workspace_id, cookies)
    assert summary.status_code == 200, summary.text
    body = summary.json()
    assert set(body) == {"counts", "total"}
    assert body["counts"] == {state.value: count for state, count in planned.items()}
    assert body["total"] == sum(planned.values()) == sum(body["counts"].values())

    # "summary" is never read as a job id, and the {job_id} routes still work
    snapshots = _get(
        client, f"/api/v1/admin/jobs/{seeded[0]}/policy-snapshots", workspace_id, cookies
    )
    assert snapshots.status_code == 200, snapshots.text
    assert snapshots.json()["job_id"] == seeded[0]
    not_a_job = _get(client, "/api/v1/admin/jobs/summary/policy-snapshots", workspace_id, cookies)
    assert not_a_job.status_code == 404 and _error_code(not_a_job) == "job_not_found"


# --- permission and tenant boundary ------------------------------------------


def test_only_workspace_administrators_may_read_jobs(client, email_outbox, monkeypatch):
    owner, personas, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    job_id = _seed_job(owner.workspace_id)

    for persona in (owner, personas["admin"]):
        listed = persona.get(LIST)
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()["items"]] == [job_id]
        summary = persona.get(SUMMARY)
        assert summary.status_code == 200, summary.text
        assert summary.json()["total"] == 1

    for name in ("member", "viewer"):
        for path in (LIST, SUMMARY):
            denied = personas[name].get(path)
            assert denied.status_code == 403, (name, path, denied.text)
            assert _error_code(denied) == "permission_denied"
            assert job_id not in denied.text

    client.cookies.clear()
    for path in (LIST, SUMMARY):
        anonymous = client.get(path, headers={"X-Workspace-ID": owner.workspace_id})
        assert anonymous.status_code == 401, (path, anonymous.text)
        assert job_id not in anonymous.text


def test_list_summary_and_cursor_stay_inside_the_workspace(client, email_outbox):
    b_workspace, b_cookies = _owner(client, email_outbox, "jobs-tenant-b@example.com")
    b_jobs = {
        _seed_job(b_workspace, created_at=BASE_TIME + timedelta(hours=hour), state=state)
        for hour, state in enumerate(
            (JobState.DEAD_LETTER, JobState.FAILED, JobState.DEAD_LETTER, JobState.COMPLETED)
        )
    }
    a_workspace, a_cookies = _owner(client, email_outbox, "jobs-tenant-a@example.com")
    a_jobs = {
        _seed_job(a_workspace, created_at=BASE_TIME - timedelta(hours=hour), state=state)
        for hour, state in enumerate((JobState.DEAD_LETTER, JobState.FAILED, JobState.QUEUED))
    }

    for params in (
        {},
        {"state": "dead_letter"},
        {"state": ["failed", "dead_letter"]},
        {"stage": "transcription"},
    ):
        walked = _walk(client, a_workspace, a_cookies, limit=1, **params)
        assert set(walked) <= a_jobs and not set(walked) & b_jobs, params
    assert set(_walk(client, a_workspace, a_cookies, limit=2)) == a_jobs
    assert set(_walk(client, b_workspace, b_cookies, limit=2)) == b_jobs

    a_summary = _get(client, SUMMARY, a_workspace, a_cookies).json()
    assert a_summary["total"] == 3
    assert a_summary["counts"]["dead_letter"] == 1 and a_summary["counts"]["completed"] == 0

    # a cursor taken from B's list, used in A, only ever pages through A's jobs
    b_page = _get(client, LIST, b_workspace, b_cookies, limit=1)
    b_cursor = b_page.json()["next_cursor"]
    assert isinstance(b_cursor, str)
    reused = _get(client, LIST, a_workspace, a_cookies, limit=100, cursor=b_cursor)
    assert set(_ids(reused)) == a_jobs
    for job_id in b_jobs:
        assert job_id not in reused.text

    # A's administrator naming B's workspace sees nothing of it
    for path in (LIST, SUMMARY):
        foreign = _get(client, path, b_workspace, a_cookies)
        assert foreign.status_code in (403, 404), foreign.text
        for job_id in b_jobs:
            assert job_id not in foreign.text


# --- efficiency and OpenAPI --------------------------------------------------


def test_each_route_reads_with_one_workspace_bound_query(client, email_outbox):
    workspace_id, cookies = _owner(client, email_outbox, "jobs-queries@example.com")
    for minute in range(6):
        _seed_job(workspace_id, created_at=BASE_TIME + timedelta(minutes=minute))

    def job_statements(path: str, **params) -> list[str]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            if any(table in statement for table in ("processing_jobs", "meetings", "recordings")):
                seen.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = _get(client, path, workspace_id, cookies, **params)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return seen

    # no query per item (meeting, recording): the page size does not matter
    for limit in (1, 6):
        statements = job_statements(LIST, limit=limit)
        assert len(statements) == 1, statements
    statements += job_statements(SUMMARY)
    assert len(statements) == 2, statements
    # the tenant filter is in the query itself, not left to row-level security
    for statement in statements:
        assert "processing_jobs.workspace_id = " in statement, statement
        assert "meetings.deleted_at IS NULL" in statement, statement


def _resolve(spec: dict, schema: dict) -> dict:
    while "$ref" in schema:
        schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]
    return schema


def test_openapi_declares_typed_job_schemas(client):
    spec = client.app.openapi()

    list_operation = spec["paths"][LIST]["get"]
    assert "429" in list_operation["description"]  # quota is not part of the flag
    assert {"limit", "cursor", "state", "stage"} <= {
        parameter["name"] for parameter in list_operation["parameters"]
    }
    page = _resolve(
        spec, list_operation["responses"]["200"]["content"]["application/json"]["schema"]
    )
    assert set(page["properties"]) == {"items", "next_cursor"}
    item = _resolve(spec, page["properties"]["items"]["items"])
    assert item["type"] == "object"
    assert set(item["properties"]) == ITEM_KEYS
    assert "additionalProperties" not in item or item["additionalProperties"] is False
    state = _resolve(spec, item["properties"]["state"])
    assert state["type"] == "string" and set(state["enum"]) == STATE_KEYS
    assert item["properties"]["replayable"]["type"] == "boolean"
    for key in ("attempt_count", "max_attempts", "replay_count", "max_replays"):
        assert item["properties"][key]["type"] == "integer", key

    summary_operation = spec["paths"][SUMMARY]["get"]
    summary = _resolve(
        spec, summary_operation["responses"]["200"]["content"]["application/json"]["schema"]
    )
    assert set(summary["properties"]) == {"counts", "total"}
    assert summary["properties"]["total"]["type"] == "integer"
    counts = _resolve(spec, summary["properties"]["counts"])
    assert counts["type"] == "object" and set(counts["properties"]) == STATE_KEYS
    assert "additionalProperties" not in counts or counts["additionalProperties"] is False
    for key in STATE_KEYS:
        assert counts["properties"][key]["type"] == "integer", key
