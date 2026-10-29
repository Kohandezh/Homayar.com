"""HOM-330 oracle: one lock order for media work, so the three known pairs never deadlock.

Written from the specification before the fix. Each race test pauses the first
session at a kept step (or behind a third session that holds a row lock), sees
the second session wait on a lock in ``pg_stat_activity`` at its own backend,
then lets both finish and checks that neither was aborted as a deadlock and that
the end state is correct. The controls run the same steps one after the other.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import psycopg
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ConversationPurge,
    DispatchState,
    JobDispatch,
    JobState,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    ProcessingJob,
    PurgeState,
    ShareAudience,
    ShareLink,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs import audio_derivatives
from backend.jobs.failures import release_job_reservations
from backend.jobs.leases import (
    LeasePolicy,
    LeaseVerdict,
    claim_verdict,
    database_now,
    grant_lease,
)
from backend.jobs.media_reconciliation import reconcile_media_objects
from backend.media.derivatives import (
    DERIVATIVE_SAMPLE_RATE_HZ,
    JOB_CANCELLED,
    MEETING_DELETED,
    DerivedAudio,
)
from backend.routers.v1 import meetings as meetings_router
from backend.services import conversation_purge_service, media_lifecycle_service
from backend.services.media_lifecycle_service import (
    media_by_id,
    place_legal_hold,
    register_derived,
    request_deletion,
)
from backend.storage import create_object_storage
from backend.storage.base import StoredObject
from backend.tests._cost import WIDE_BOUND_SECONDS
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload

ACTIVE = MediaLifecycleState.ACTIVE
DELETED = MediaLifecycleState.DELETED
HELD = MediaLifecycleState.LEGAL_HOLD
PENDING = MediaLifecycleState.DELETION_PENDING
REFUSED_AFTER_DELETION = {MEETING_DELETED, JOB_CANCELLED}


def _post(client, tenant: dict, path: str, payload: dict):
    return client.post(path, json=payload, headers=_headers(tenant), cookies=tenant["cookies"])


def _hold_body() -> dict:
    return {
        "reason": "litigation 2026-330",
        "review_at": (datetime.now(UTC) + timedelta(days=90)).isoformat(),
    }


def _hold_or_release(client, tenant: dict, route: str, media_id: str):
    if route == "hold":
        return _post(client, tenant, f"/api/v1/media/{media_id}/legal-hold", _hold_body())
    return _post(
        client, tenant, f"/api/v1/media/{media_id}/legal-hold/release", {"reason": "closed"}
    )


def _delete(client, tenant: dict, meeting_id: str):
    return client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )


def _conversation(client, tenant: dict, key: str) -> tuple[str, str, str]:
    """One uploaded conversation: its meeting id, its ORIGINAL's id and its job id."""
    accepted = _upload(client, tenant, key=key, content=f"staged {key}".encode())
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    with inspection_session_factory()() as db:
        original_id = db.scalar(
            select(MediaObject.id).where(
                MediaObject.meeting_id == meeting_id, MediaObject.kind == MediaKind.ORIGINAL
            )
        )
    assert original_id is not None
    return meeting_id, original_id, accepted.json()["job"]["id"]


@contextmanager
def _app_session(tenant: dict) -> Iterator[Session]:
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        yield db


def _stored(settings, workspace_id: str) -> StoredObject:
    payload = f"derived {time.monotonic_ns()}".encode()
    return create_object_storage(settings).put_stream(
        workspace_id,
        BytesIO(payload),
        content_type="audio/wav",
        original_filename="normalized.wav",
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _derive(settings, tenant: dict, source_id: str, *, now: datetime) -> str:
    stored = _stored(settings, tenant["workspace_id"])
    with _app_session(tenant) as db:
        source = media_by_id(db, workspace_id=tenant["workspace_id"], media_id=source_id)
        assert source is not None
        derived = register_derived(
            db,
            source=source,
            kind=MediaKind.NORMALIZED,
            stored=stored,
            storage_backend=settings.storage_backend,
            producer="test-derivative/1",
            job_id=None,
            actor_user_id=None,
            now=now,
            settings=settings,
            container_format="wav",
            duration_seconds=3,
            channel_count=2,
            sample_rate_hz=16_000,
        )
        db.commit()
        return derived.id


def _request(settings, media_id: str, at: datetime) -> None:
    with inspection_session_factory()() as db:
        request_deletion(
            db,
            media=db.get(MediaObject, media_id),
            reason="owner asked",
            actor_user_id=None,
            now=at,
            settings=settings,
        )
        db.commit()


def _row(media_id: str) -> MediaObject:
    with inspection_session_factory()() as db:
        media = db.get(MediaObject, media_id)
        assert media is not None
        return media


def _state(storage, workspace_id: str, media_id: str) -> MediaLifecycleState:
    """The row's state, after checking that its bytes agree with it."""
    row = _row(media_id)
    present = storage.exists(workspace_id, row.storage_key)
    if row.lifecycle_state is DELETED:
        assert not present, f"{media_id} is deleted but its bytes are still stored"
        assert row.legal_hold_at is None, f"{media_id} is deleted while a hold says otherwise"
    else:
        assert present, f"{media_id} is {row.lifecycle_state.value} without its bytes"
    return row.lifecycle_state


def _derived_rows(meeting_id: str) -> list[MediaObject]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(MediaObject).where(
                    MediaObject.meeting_id == meeting_id, MediaObject.kind != MediaKind.ORIGINAL
                )
            )
        )


def _purge(meeting_id: str) -> ConversationPurge:
    with inspection_session_factory()() as db:
        purge = db.scalar(
            select(ConversationPurge).where(ConversationPurge.meeting_id == meeting_id)
        )
        assert purge is not None
        return purge


def _purge_failures(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("MEETING_PURGE_ATTEMPT_FAILED")
    ]


def _share(tenant: dict, meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        share = ShareLink(
            workspace_id=tenant["workspace_id"],
            meeting_id=meeting_id,
            created_by_user_id=tenant["user_id"],
            token_hash=hashlib.sha256(meeting_id.encode()).hexdigest(),
            audience=ShareAudience.WORKSPACE,
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        db.add(share)
        db.commit()
        return share.id


def _assert_tombstoned(meeting_id: str, job_id: str, share_id: str | None = None) -> None:
    with inspection_session_factory()() as db:
        assert db.get(Meeting, meeting_id).deleted_at is not None
        assert db.get(ProcessingJob, job_id).state is JobState.CANCELLED
        assert (
            db.scalar(
                select(ConversationPurge.id).where(ConversationPurge.meeting_id == meeting_id)
            )
            is not None
        )
        if share_id is not None:
            assert db.get(ShareLink, share_id).revoked_at is not None
        rows = list(db.scalars(select(MediaObject).where(MediaObject.meeting_id == meeting_id)))
    assert rows
    for row in rows:
        assert row.deletion_requested_at is not None, row.id


def _deadlock(error: BaseException | None) -> bool:
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, psycopg.errors.DeadlockDetected):
            return True
        if getattr(error, "sqlstate", None) == "40P01":
            return True
        error = getattr(error, "orig", None) or error.__cause__ or error.__context__
    return False


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
        except BaseException as exc:  # noqa: BLE001
            self.error = exc

    def finish(self) -> Any:
        self.join(WIDE_BOUND_SECONDS)
        assert not self.is_alive(), "the worker did not finish"
        assert not _deadlock(self.error), f"aborted as a deadlock (40P01): {self.error!r}"
        if self.error is not None:
            raise self.error
        return self.result


@contextmanager
def _backend_pids() -> Iterator[dict[int, int]]:
    """The backend pid of the latest application transaction of each thread."""
    pids: dict[int, int] = {}
    factory = get_session_factory()

    def record(_session, _transaction, connection) -> None:
        pids[threading.get_ident()] = connection.exec_driver_sql("SELECT pg_backend_pid()").scalar()

    event.listen(factory, "after_begin", record)
    try:
        yield pids
    finally:
        event.remove(factory, "after_begin", record)


def _lock_waits() -> list[tuple[int, str, list[int]]]:
    """Backends of this database that wait on a lock: pid, statement, blocking pids."""
    with inspection_session_factory()() as db:
        return [
            (pid, query, list(blockers or []))
            for pid, query, blockers in db.execute(
                text(
                    "SELECT pid, query, pg_blocking_pids(pid) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND wait_event_type = 'Lock' "
                    "AND pid <> pg_backend_pid()"
                )
            )
        ]


def _waited(
    worker: _Worker, *, pids: dict[int, int] | None = None, behind: int | None = None
) -> str:
    """The statement a lock wait is seen on; fails when ``worker`` ends without one.

    With ``pids`` the waiting backend is ``worker``'s own; with ``behind`` the
    backend ``behind`` is among the ones it waits for.
    """
    deadline = time.monotonic() + WIDE_BOUND_SECONDS
    while time.monotonic() < deadline:
        own = pids.get(worker.ident) if pids is not None else None
        for pid, query, blockers in _lock_waits():
            if (pids is None or pid == own) and (behind is None or behind in blockers):
                return query
        if not worker.is_alive():
            pytest.fail(f"the session never waited on a lock (error: {worker.error!r})")
        time.sleep(0.01)
    pytest.fail("no session waited on a lock within the hang guard")


def _settles(worker: _Worker, pids: dict[int, int]) -> None:
    """Returns once ``worker`` has finished or its own backend waits on a lock."""
    deadline = time.monotonic() + WIDE_BOUND_SECONDS
    while time.monotonic() < deadline:
        if not worker.is_alive():
            return
        own = pids.get(worker.ident)
        if any(pid == own for pid, _query, _blockers in _lock_waits()):
            return
        time.sleep(0.01)
    pytest.fail("the session neither finished nor waited within the hang guard")


def _pause_after_first(
    monkeypatch,
    modules: tuple[ModuleType, ...],
    name: str,
    entered: threading.Event,
    gate: threading.Event,
) -> None:
    """The first call of ``name`` returns only after ``gate``; its caller keeps its locks."""
    real = getattr(modules[0], name)
    calls: list[int] = []

    def paused(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        if not calls:
            calls.append(1)
            entered.set()
            assert gate.wait(WIDE_BOUND_SECONDS)
        return result

    monkeypatch.setattr(modules[0], name, paused)
    for module in modules[1:]:
        monkeypatch.setattr(module, name, paused, raising=False)


class _PausedStorage:
    """Object storage whose first ``delete`` returns only after ``gate``."""

    def __init__(self, storage, entered: threading.Event, gate: threading.Event) -> None:
        self._storage = storage
        self._entered = entered
        self._gate = gate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._storage, name)

    def delete(self, workspace_id: str, key: str) -> Any:
        removed = self._storage.delete(workspace_id, key)
        if not self._entered.is_set():
            self._entered.set()
            assert self._gate.wait(WIDE_BOUND_SECONDS)
        return removed


@contextmanager
def _row_lock(model: type[Meeting] | type[MediaObject], row_id: str) -> Iterator[int]:
    """A third session holds ``FOR UPDATE`` on one row; yields its backend pid."""
    with inspection_session_factory()() as db:
        pid = db.scalar(text("SELECT pg_backend_pid()"))
        db.execute(select(model.id).where(model.id == row_id).with_for_update())
        try:
            yield pid
        finally:
            db.rollback()


def _locked_elsewhere(row_ids: list[str]) -> list[str]:
    """The media rows that another session holds a row lock on right now."""
    taken: list[str] = []
    for row_id in row_ids:
        with inspection_session_factory()() as db:
            try:
                db.execute(
                    select(MediaObject.id)
                    .where(MediaObject.id == row_id)
                    .with_for_update(nowait=True)
                )
            except OperationalError as exc:
                if not isinstance(exc.orig, psycopg.errors.LockNotAvailable):
                    raise
                taken.append(row_id)
            db.rollback()
    return taken


def _sweep(settings, workspace_id: str, storage=None, **kwargs: Any) -> Callable[[], dict]:
    def work() -> dict:
        return reconcile_media_objects(
            settings,
            storage=storage or create_object_storage(settings),
            workspace_ids=[workspace_id],
            now=datetime.now(UTC),
            **kwargs,
        )

    return work


def _p1_lineage(
    client, email_outbox, monkeypatch, settings, *, route: str, label: str, meeting: bool
) -> SimpleNamespace:
    """An ORIGINAL with three derived objects; ``first`` and ``second`` are pending.

    ``first`` is older than ``second`` but its deletion was requested later.
    For a release the ORIGINAL is held directly (``first`` and ``second`` stay
    unheld) and ``third`` inherits its hold with a recorded deletion request.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    meeting_id, original_id, job_id = _conversation(client, tenant, label)
    start = datetime.now(UTC)
    first = _derive(settings, tenant, original_id, now=start + timedelta(milliseconds=1))
    second = _derive(settings, tenant, original_id, now=start + timedelta(milliseconds=2))
    requested = start - timedelta(hours=3)
    _request(settings, second, requested)
    _request(settings, first, requested + timedelta(minutes=1))
    if route == "release":
        with _app_session(tenant) as db:
            assert place_legal_hold(
                db,
                media=media_by_id(db, workspace_id=tenant["workspace_id"], media_id=original_id),
                reason="litigation 2026-330",
                review_at=datetime.now(UTC) + timedelta(days=30),
                actor_user_id=tenant["user_id"],
                now=datetime.now(UTC),
                settings=settings,
            )
            db.commit()
    third = _derive(settings, tenant, original_id, now=start + timedelta(milliseconds=3))
    if route == "release":
        _request(settings, third, requested + timedelta(minutes=2))
        assert _row(third).lifecycle_state is HELD
    else:
        assert _row(third).lifecycle_state is ACTIVE
    if not meeting:
        with inspection_session_factory()() as db:
            for media_id in (original_id, first, second, third):
                db.execute(
                    text("UPDATE media_objects SET meeting_id = NULL WHERE id = :id"),
                    {"id": media_id},
                )
            db.commit()
    for media_id in (first, second):
        assert _row(media_id).lifecycle_state is PENDING
    return SimpleNamespace(
        tenant=tenant,
        workspace_id=tenant["workspace_id"],
        meeting_id=meeting_id,
        original=original_id,
        first=first,
        second=second,
        third=third,
    )


def _assert_p1_route(response, route: str) -> None:
    assert response.status_code == 200, response.text
    assert response.json()["lifecycle_state"] == ("legal_hold" if route == "hold" else "active")


@pytest.mark.postgres
@pytest.mark.parametrize("meeting", ["meeting", "no-meeting"])
@pytest.mark.parametrize("route", ["hold", "release"])
def test_p1_a_hold_or_release_by_id_during_the_sweep_waits_and_does_not_deadlock(
    client, email_outbox, monkeypatch, settings, route, meeting
):
    lineage = _p1_lineage(
        client,
        email_outbox,
        monkeypatch,
        settings,
        route=route,
        label=f"p1-{route}-{meeting}",
        meeting=meeting == "meeting",
    )
    storage = create_object_storage(settings)
    entered, gate = threading.Event(), threading.Event()
    paused = _PausedStorage(storage, entered, gate)
    with _backend_pids() as pids:
        sweep = _Worker(_sweep(settings, lineage.workspace_id, storage=paused))
        sweep.start()
        assert entered.wait(WIDE_BOUND_SECONDS)
        operation = _Worker(
            lambda: _hold_or_release(client, lineage.tenant, route, lineage.original)
        )
        operation.start()
        try:
            _waited(operation, behind=pids[sweep.ident])
        finally:
            gate.set()
        counts = sweep.finish()
        response = operation.finish()

    _assert_p1_route(response, route)
    workspace_id = lineage.workspace_id
    if route == "hold":
        states = [_state(storage, workspace_id, m) for m in (lineage.first, lineage.second)]
        assert set(states) <= {DELETED, HELD}
        assert counts["pending_closed"] == states.count(DELETED)
        assert counts["pending_closed"] + counts["pending_failed"] == 2
        assert _state(storage, workspace_id, lineage.third) is HELD
        assert _state(storage, workspace_id, lineage.original) is HELD
    else:
        for media_id in (lineage.first, lineage.second, lineage.third):
            assert _state(storage, workspace_id, media_id) is DELETED, media_id
        assert _state(storage, workspace_id, lineage.original) is ACTIVE
        assert (counts["pending_closed"], counts["pending_failed"]) == (2, 0)


@pytest.mark.postgres
@pytest.mark.parametrize("first", ["sweep-first", "route-first"])
@pytest.mark.parametrize("route", ["hold", "release"])
def test_p1_control_the_same_steps_one_after_the_other(
    client, email_outbox, monkeypatch, settings, route, first
):
    lineage = _p1_lineage(
        client,
        email_outbox,
        monkeypatch,
        settings,
        route=route,
        label=f"p1-control-{route}-{first}",
        meeting=True,
    )
    storage = create_object_storage(settings)
    sweep = _sweep(settings, lineage.workspace_id, storage=storage)
    if first == "sweep-first":
        counts = sweep()
        response = _hold_or_release(client, lineage.tenant, route, lineage.original)
    else:
        response = _hold_or_release(client, lineage.tenant, route, lineage.original)
        counts = sweep()

    _assert_p1_route(response, route)
    workspace_id = lineage.workspace_id
    pending = (lineage.first, lineage.second)
    if route == "hold" and first == "route-first":
        for media_id in pending:
            assert _state(storage, workspace_id, media_id) is HELD, media_id
            assert _row(media_id).deletion_requested_at is not None
        assert (counts["pending_closed"], counts["pending_failed"]) == (0, 0)
    else:
        for media_id in pending:
            assert _state(storage, workspace_id, media_id) is DELETED, media_id
        assert (counts["pending_closed"], counts["pending_failed"]) == (2, 0)
    if route == "hold":
        assert _state(storage, workspace_id, lineage.third) is HELD
        assert _state(storage, workspace_id, lineage.original) is HELD
    else:
        assert _state(storage, workspace_id, lineage.third) is DELETED
        assert _state(storage, workspace_id, lineage.original) is ACTIVE


@pytest.mark.postgres
def test_ac2_the_sweep_waits_on_the_meeting_row_before_it_locks_a_media_row(
    client, email_outbox, monkeypatch, settings
):
    lineage = _p1_lineage(
        client, email_outbox, monkeypatch, settings, route="hold", label="ac2-meeting", meeting=True
    )
    storage = create_object_storage(settings)
    pending = [lineage.first, lineage.second]
    with _row_lock(Meeting, lineage.meeting_id) as blocker:
        sweep = _Worker(_sweep(settings, lineage.workspace_id, storage=storage))
        sweep.start()
        _waited(sweep, behind=blocker)
        taken = _locked_elsewhere(pending)
    counts = sweep.finish()

    assert taken == []
    assert (counts["pending_closed"], counts["pending_failed"]) == (2, 0)
    for media_id in pending:
        assert _state(storage, lineage.workspace_id, media_id) is DELETED


@pytest.mark.postgres
def test_ac2_control_the_sweep_still_closes_the_oldest_request_first_within_its_batch(
    client, email_outbox, monkeypatch, settings
):
    lineage = _p1_lineage(
        client, email_outbox, monkeypatch, settings, route="hold", label="ac2-batch", meeting=True
    )
    storage = create_object_storage(settings)

    counts = _sweep(settings, lineage.workspace_id, storage=storage, limit=1)()

    assert (counts["pending_closed"], counts["pending_failed"]) == (1, 0)
    assert _state(storage, lineage.workspace_id, lineage.second) is DELETED
    assert _state(storage, lineage.workspace_id, lineage.first) is PENDING


def _p2_meeting(client, email_outbox, monkeypatch, label: str) -> SimpleNamespace:
    """A tombstoned meeting whose purge waits only for the hold on its ORIGINAL."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    meeting_id, original_id, job_id = _conversation(client, tenant, label)
    held = _hold_or_release(client, tenant, "hold", original_id)
    assert held.status_code == 200, held.text
    deleted = _delete(client, tenant, meeting_id)
    assert deleted.status_code == 204
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING
    assert purge.last_error == "legal_hold"
    assert _row(original_id).lifecycle_state is HELD
    return SimpleNamespace(
        tenant=tenant,
        meeting_id=meeting_id,
        original=original_id,
        purge_id=purge.id,
        sweep_now=purge.next_attempt_at + timedelta(minutes=1),
    )


def _attempt(settings, tenant: dict, purge_id: str, now: datetime) -> Callable[[], Any]:
    def work() -> Any:
        with _app_session(tenant) as db:
            return conversation_purge_service.run_purge_attempt(
                db,
                create_object_storage(settings),
                purge_id=purge_id,
                workspace_id=tenant["workspace_id"],
                now=now,
                settings=settings,
            )

    return work


def _assert_nudge_kept(settings, conversation: SimpleNamespace, released_by: datetime) -> None:
    purge = _purge(conversation.meeting_id)
    if purge.state is not PurgeState.COMPLETED:
        assert purge.state is PurgeState.PENDING
        assert purge.next_attempt_at <= released_by, (purge.next_attempt_at, purge.last_error)
    storage = create_object_storage(settings)
    assert _state(storage, conversation.tenant["workspace_id"], conversation.original) is DELETED


@pytest.mark.postgres
def test_p2_a_release_during_a_purge_attempt_waits_and_does_not_lose_the_nudge(
    client, email_outbox, monkeypatch, settings
):
    conversation = _p2_meeting(client, email_outbox, monkeypatch, "p2-release-race")
    entered, gate = threading.Event(), threading.Event()
    _pause_after_first(
        monkeypatch,
        (media_lifecycle_service, meetings_router),
        "release_with_descendants",
        entered,
        gate,
    )
    with _backend_pids() as pids:
        release = _Worker(
            lambda: _hold_or_release(client, conversation.tenant, "release", conversation.original)
        )
        release.start()
        assert entered.wait(WIDE_BOUND_SECONDS)
        attempt = _Worker(
            _attempt(settings, conversation.tenant, conversation.purge_id, conversation.sweep_now)
        )
        attempt.start()
        try:
            _waited(attempt, pids=pids)
        finally:
            gate.set()
        response = release.finish()
        released_by = datetime.now(UTC)
        attempt.finish()

    assert response.status_code == 200, response.text
    _assert_nudge_kept(settings, conversation, released_by)


@pytest.mark.postgres
@pytest.mark.parametrize("first", ["release-first", "attempt-first"])
def test_p2_control_the_same_steps_one_after_the_other(
    client, email_outbox, monkeypatch, settings, first
):
    conversation = _p2_meeting(client, email_outbox, monkeypatch, f"p2-control-{first}")
    attempt = _attempt(settings, conversation.tenant, conversation.purge_id, conversation.sweep_now)
    if first == "release-first":
        response = _hold_or_release(client, conversation.tenant, "release", conversation.original)
        released_by = datetime.now(UTC)
        outcome = attempt()
        assert outcome.deferred is None and not outcome.skipped
        assert _purge(conversation.meeting_id).state is PurgeState.COMPLETED
    else:
        outcome = attempt()
        assert outcome.deferred == "legal_hold"
        response = _hold_or_release(client, conversation.tenant, "release", conversation.original)
        released_by = datetime.now(UTC)
        assert _purge(conversation.meeting_id).state is PurgeState.PENDING

    assert response.status_code == 200, response.text
    _assert_nudge_kept(settings, conversation, released_by)


@pytest.mark.postgres
def test_ac3_control_two_purge_sweepers_on_one_purge_do_not_both_run_it(
    client, email_outbox, monkeypatch, settings
):
    conversation = _p2_meeting(client, email_outbox, monkeypatch, "ac3-exclusive-claim")
    released = _hold_or_release(client, conversation.tenant, "release", conversation.original)
    assert released.status_code == 200, released.text
    assert _purge(conversation.meeting_id).attempts == 0
    now = datetime.now(UTC)
    with _backend_pids() as pids:
        with _row_lock(MediaObject, conversation.original) as blocker:
            leader = _Worker(_attempt(settings, conversation.tenant, conversation.purge_id, now))
            leader.start()
            _waited(leader, pids=pids, behind=blocker)
            follower = _Worker(_attempt(settings, conversation.tenant, conversation.purge_id, now))
            follower.start()
            _settles(follower, pids)
        outcomes = [leader.finish(), follower.finish()]

    assert [outcome.skipped for outcome in outcomes] == [False, True]
    purge = _purge(conversation.meeting_id)
    assert purge.attempts == 1
    assert purge.state is PurgeState.COMPLETED


def _p3_claim(
    client, email_outbox, monkeypatch, settings, *, label: str, allowance: str
) -> SimpleNamespace:
    """A running derivative attempt (its job claimed, its message published).

    With ``allowance == "released"`` the job holds no reserved allowance any
    more, so the deletion's job cancellation writes nothing before it flushes.
    """
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    meeting_id, original_id, job_id = _conversation(client, tenant, label)
    with inspection_session_factory()() as db:
        dispatch = db.scalar(select(JobDispatch).where(JobDispatch.job_id == job_id))
        assert dispatch is not None
        dispatch.state = DispatchState.PUBLISHED
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if allowance == "released":
            release_job_reservations(db, job, reason="hom330-oracle")
        now = database_now(db)
        assert claim_verdict(job, now=now) is LeaseVerdict.CLAIMABLE
        lease = grant_lease(
            job, worker_id="hom330-oracle", policy=LeasePolicy.from_settings(settings), now=now
        )
        db.commit()
    stored = _stored(settings, tenant["workspace_id"])
    claim = SimpleNamespace(
        job_id=job_id,
        workspace_id=tenant["workspace_id"],
        meeting_id=meeting_id,
        recording_sha256=_row(original_id).sha256,
        lease=lease,
    )
    derived = DerivedAudio(
        path=Path(settings.storage_local_root) / "derived.wav",
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        record={},
        channel_count=2,
        sample_count=DERIVATIVE_SAMPLE_RATE_HZ * 3,
    )
    storage = create_object_storage(settings)

    def register() -> str:
        return audio_derivatives._register(claim, original_id, derived, stored, storage, settings)

    return SimpleNamespace(
        tenant=tenant,
        meeting_id=meeting_id,
        original=original_id,
        job_id=job_id,
        stored=stored,
        storage=storage,
        register=register,
    )


def _assert_refused_and_removed(attempt: SimpleNamespace, outcome: str) -> None:
    assert outcome in REFUSED_AFTER_DELETION, outcome
    assert not attempt.storage.exists(attempt.tenant["workspace_id"], attempt.stored.key)
    assert _derived_rows(attempt.meeting_id) == []


@pytest.mark.postgres
@pytest.mark.parametrize("allowance", ["released", "reserved"])
def test_p3_a_registration_during_a_meeting_deletion_waits_and_does_not_deadlock(
    client, email_outbox, monkeypatch, settings, caplog, allowance
):
    caplog.set_level(logging.WARNING)
    attempt = _p3_claim(
        client, email_outbox, monkeypatch, settings, label=f"p3-{allowance}", allowance=allowance
    )
    share_id = _share(attempt.tenant, attempt.meeting_id)
    entered, gate = threading.Event(), threading.Event()
    _pause_after_first(
        monkeypatch,
        (media_lifecycle_service, conversation_purge_service),
        "retire_meeting_media",
        entered,
        gate,
    )
    with _backend_pids() as pids:
        deletion = _Worker(lambda: _delete(client, attempt.tenant, attempt.meeting_id))
        deletion.start()
        assert entered.wait(WIDE_BOUND_SECONDS)
        registration = _Worker(attempt.register)
        registration.start()
        try:
            _waited(registration, pids=pids)
        finally:
            gate.set()
        deleted = deletion.finish()
        outcome = registration.finish()

    assert deleted.status_code == 204
    _assert_refused_and_removed(attempt, outcome)
    _assert_tombstoned(attempt.meeting_id, attempt.job_id, share_id)
    assert _purge_failures(caplog) == []


@pytest.mark.postgres
def test_p3_control_a_meeting_deletion_during_a_registration_requests_the_new_row_deletion(
    client, email_outbox, monkeypatch, settings, caplog
):
    caplog.set_level(logging.WARNING)
    attempt = _p3_claim(
        client, email_outbox, monkeypatch, settings, label="p3-register-first", allowance="reserved"
    )
    entered, gate = threading.Event(), threading.Event()
    _pause_after_first(
        monkeypatch, (media_lifecycle_service, audio_derivatives), "register_derived", entered, gate
    )
    with _backend_pids() as pids:
        registration = _Worker(attempt.register)
        registration.start()
        assert entered.wait(WIDE_BOUND_SECONDS)
        deletion = _Worker(lambda: _delete(client, attempt.tenant, attempt.meeting_id))
        deletion.start()
        try:
            _waited(deletion, behind=pids[registration.ident])
        finally:
            gate.set()
        outcome = registration.finish()
        deleted = deletion.finish()

    assert outcome == "registered"
    assert deleted.status_code == 204
    (derived,) = _derived_rows(attempt.meeting_id)
    assert derived.deletion_requested_at is not None
    assert derived.lifecycle_state in (PENDING, DELETED)
    _assert_tombstoned(attempt.meeting_id, attempt.job_id)
    assert _purge_failures(caplog) == []


@pytest.mark.postgres
@pytest.mark.parametrize("allowance", ["released", "reserved"])
def test_p3_control_a_registration_after_the_meeting_deletion_is_refused(
    client, email_outbox, monkeypatch, settings, allowance
):
    attempt = _p3_claim(
        client,
        email_outbox,
        monkeypatch,
        settings,
        label=f"p3-delete-first-{allowance}",
        allowance=allowance,
    )
    deleted = _delete(client, attempt.tenant, attempt.meeting_id)
    assert deleted.status_code == 204

    _assert_refused_and_removed(attempt, attempt.register())
    _assert_tombstoned(attempt.meeting_id, attempt.job_id)


@pytest.mark.postgres
def test_ac4_a_meeting_deletion_waits_on_the_meeting_row_before_it_locks_a_media_row(
    client, email_outbox, monkeypatch, settings, caplog
):
    caplog.set_level(logging.WARNING)
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "ac4-order")
    meeting_id, original_id, job_id = _conversation(client, tenant, "ac4-order")
    derived_id = _derive(settings, tenant, original_id, now=datetime.now(UTC))
    share_id = _share(tenant, meeting_id)
    with _row_lock(Meeting, meeting_id) as blocker:
        deletion = _Worker(lambda: _delete(client, tenant, meeting_id))
        deletion.start()
        _waited(deletion, behind=blocker)
        taken = _locked_elsewhere([original_id, derived_id])
    deleted = deletion.finish()

    assert taken == []
    assert deleted.status_code == 204
    _assert_tombstoned(meeting_id, job_id, share_id)
    assert _purge_failures(caplog) == []
