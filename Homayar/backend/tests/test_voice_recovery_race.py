"""Voice recovery racing media-side end transitions (HOM-169, VOICE-01).

``VoiceControlPlane.recover()`` fails the live calls of a lapsed media node
(``node_lost``) and of expired capacity slots (``lease_expired``) while media
workers may be ending the same calls with ``transition(action="end")``. A
sweep that runs concurrently with those transitions must:

* never overwrite a call whose end transition returned ENDED: the media worker
  was told ``ENDED/completed`` and the stored call must say so;
* take its row locks in the module's documented order (call session before
  slots, as ``transition`` does), so PostgreSQL detects no deadlock and the
  contention retry loop never has to absorb one;
* report and count only the calls it actually failed, so every released call
  is counted exactly once, by the side that ended it;
* leave no slot held, each slot released with the reason its call really
  ended with.

Two deterministic PostgreSQL cases pin one interleaving each: a row-lock
blocker parks the end transition before its slot write, and a pausing clock
parks it right after it locked the call. A burst case races one sweep against
many end transitions released together from a barrier, over repeated bursts.
Sessions are built like ``configure_database`` (production engine, ``SET
ROLE`` to the app role, tenant context re-applied per transaction): media
workers bind their call's workspace and the sweeper runs unbound, as the
Celery sweeper does. A SQLite case keeps the fallback backend's recovery
accounting covered.
"""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from backend.config import Settings, reset_settings_cache
from backend.database.relational import build_engine
from backend.database.saas_models import Workspace
from backend.database.tenant_context import bind_tenant_context, install_session_listeners
from backend.tests._database import (
    TEST_APP_ROLE,
    configured_database_url,
    is_postgres_url,
    reset_postgres_schema,
)
from backend.voice.admission import (
    END_LEASE_EXPIRED,
    END_NODE_LOST,
    AdmissionPolicy,
    VoiceControlPlane,
)
from backend.voice.loadtest import seed_workspaces, sqlite_session_factory
from backend.voice.metrics import VoiceMetrics
from backend.voice.models import VoiceCallSession, VoiceCapacitySlot
from backend.voice.state import IllegalTransition, StaleGeneration, VoiceStateError

REPO_ROOT = Path(__file__).resolve().parents[2]

# Every racing engine carries an application_name so the database's view of
# its backends (lock waits, live connections) can be told apart.
MEDIA_APP = "hom169-media"
SWEEPER_APP = "hom169-sweeper"

# Burst shape: one sweep against BURST_CALLS end transitions spread over
# MEDIA_PLANES independent planes (stand-ins for worker processes).
MEDIA_PLANES = 3
BURST_CALLS = 24
BURSTS = 4
SWEEPER_STAGGER_SECONDS = (0.0, 0.002, 0.005, 0.0)
# The last burst holds back every other end transition after the release, so the
# sweep provably reaches some calls first even on a slow host (non-vacuity).
ENDER_HOLD_BACK_SECONDS = (0.0, 0.0, 0.0, 2.0)

MEDIA_POOL = BURST_CALLS // MEDIA_PLANES + 1
SWEEPER_POOL = 2
SETUP_POOL = 2
ADMIN_POOL = 4
CONNECTION_BUDGET = 90  # PostgreSQL's default max_connections is 100

WAIT_SECONDS = 15.0
JOIN_SECONDS = 120.0

VOICE_TABLES = (
    "voice_capacity_slots",
    "voice_call_sessions",
    "voice_workspace_limits",
    "voice_media_nodes",
    "voice_admission_locks",
)

COMPLETED = ("ended", "completed")


@dataclass(frozen=True)
class Sweep:
    """How a call's capacity lapses before the sweep runs."""

    reason: str
    policy: AdmissionPolicy
    lapse_seconds: float
    declares_node_lost: bool
    # What an end transition that loses the race to the sweep raises: the
    # node was fenced (stale generation), or the call is already FAILED.
    loser_error: type[VoiceStateError]


SWEEPS = {
    END_NODE_LOST: Sweep(
        END_NODE_LOST,
        AdmissionPolicy(heartbeat_ttl_seconds=10, slot_lease_seconds=30),
        lapse_seconds=13,
        declares_node_lost=True,
        loser_error=StaleGeneration,
    ),
    END_LEASE_EXPIRED: Sweep(
        END_LEASE_EXPIRED,
        AdmissionPolicy(heartbeat_ttl_seconds=1000, slot_lease_seconds=5),
        lapse_seconds=6,
        declares_node_lost=False,
        loser_error=IllegalTransition,
    ),
}
SWEEP_PARAMS = [pytest.param(sweep, id=reason) for reason, sweep in SWEEPS.items()]


def test_connection_pools_fit_the_budget() -> None:
    assert MEDIA_POOL * MEDIA_PLANES >= BURST_CALLS + MEDIA_PLANES
    total = MEDIA_POOL * MEDIA_PLANES + SWEEPER_POOL + SETUP_POOL + ADMIN_POOL
    assert total < CONNECTION_BUDGET
    assert len(SWEEPER_STAGGER_SECONDS) == BURSTS
    assert len(ENDER_HOLD_BACK_SECONDS) == BURSTS


# -- shared helpers ------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class PausingClock:
    """Reads a shared clock; once armed, the next read parks the caller.

    ``transition`` reads its clock inside the transaction, after it has
    locked the call row and checked the generation and before it writes the
    call or its slot. An armed read therefore parks a media worker while it
    holds the call's row lock and has not touched the slot yet.
    """

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._armed = False
        self.paused = threading.Event()
        self.resume = threading.Event()

    def arm(self) -> None:
        with self._lock:
            self._armed = True

    def __call__(self) -> datetime:
        with self._lock:
            park, self._armed = self._armed, False
        if park:
            self.paused.set()
            if not self.resume.wait(JOIN_SECONDS):
                raise RuntimeError("the paused media worker was never resumed")
        return self._clock()


class Worker(threading.Thread):
    """Runs ``work`` and keeps its result or the exception it raised."""

    def __init__(self, name: str, work: Callable[[], Any]) -> None:
        super().__init__(name=name, daemon=True)
        self._work = work
        self.result: Any = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self._work()
        except BaseException as exc:  # reported by the test
            self.error = exc


def _join(*workers: Worker) -> None:
    for worker in workers:
        worker.join(JOIN_SECONDS)
        assert not worker.is_alive(), f"{worker.name} did not finish"


def _wait_until(condition: Callable[[], bool], timeout: float = WAIT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.005)
    return condition()


def _releases(*planes: VoiceControlPlane) -> dict[str, int]:
    total: Counter[str] = Counter()
    for plane in planes:
        total.update(plane.metrics.snapshot()["releases_by_reason"])
    return dict(total)


def _report_by_label(report: dict, labels: dict[str, str]) -> dict:
    """The recovery report with call ids replaced by the test's call labels."""
    return {
        "lost_nodes": list(report["lost_nodes"]),
        "failed_calls": {
            reason: sorted(labels.get(call_id, call_id) for call_id in ids)
            for reason, ids in report["failed_calls"].items()
        },
    }


def _expected_report(sweep: Sweep, node_ids: list[str], failed: list[str]) -> dict:
    return {
        "lost_nodes": node_ids if sweep.declares_node_lost else [],
        "failed_calls": {
            END_NODE_LOST: sorted(failed) if sweep.reason == END_NODE_LOST else [],
            END_LEASE_EXPIRED: sorted(failed) if sweep.reason == END_LEASE_EXPIRED else [],
        },
    }


def _call_records(factory: sessionmaker[Session], ids: list[str]) -> dict[str, tuple]:
    """Stored ``(state, end_reason, slot_held, slot_release_reason)`` per call id."""
    with factory() as db:
        rows = db.execute(
            select(
                VoiceCallSession.id,
                VoiceCallSession.state,
                VoiceCallSession.end_reason,
                VoiceCapacitySlot.released_at,
                VoiceCapacitySlot.release_reason,
            )
            .join(VoiceCapacitySlot, VoiceCapacitySlot.session_id == VoiceCallSession.id)
            .where(VoiceCallSession.id.in_(ids))
        ).all()
    return {
        call_id: (state.value, end_reason, released_at is None, release_reason)
        for call_id, state, end_reason, released_at, release_reason in rows
    }


def _held(factory: sessionmaker[Session], ids: list[str]) -> int:
    with factory() as db:
        return int(
            db.execute(
                select(func.count(VoiceCapacitySlot.id)).where(
                    VoiceCapacitySlot.released_at.is_(None),
                    VoiceCapacitySlot.session_id.in_(ids),
                )
            ).scalar_one()
        )


# -- PostgreSQL environment ------------------------------------------------------


@pytest.fixture(scope="module")
def migrated_database_url() -> Iterator[str]:
    """An empty database brought to ``head`` by the Alembic chain (not ``create_all``)."""
    url = configured_database_url()
    assert url is not None and is_postgres_url(url)
    reset_postgres_schema(url)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        patch.setenv("ENVIRONMENT", "test")
        reset_settings_cache()
        try:
            command.upgrade(Config(str(REPO_ROOT / "alembic.ini")), "head")
        finally:
            reset_settings_cache()
    yield url
    reset_postgres_schema(url)


def _app_factory(url: str, pool_size: int, application_name: str) -> sessionmaker[Session]:
    """A session factory built like ``configure_database`` (production engine,
    ``SET ROLE`` to the app role on checkout, tenant context re-applied at the
    start of every transaction), tagged with ``application_name``."""
    tagged = make_url(url).update_query_dict({"application_name": application_name})
    settings = Settings(
        environment="test",
        database_url=tagged.render_as_string(hide_password=False),
        database_app_role=TEST_APP_ROLE,
        database_pool_size=pool_size,
        database_max_overflow=0,
        _env_file=None,
    )
    factory = sessionmaker(bind=build_engine(settings), autoflush=False, expire_on_commit=False)
    install_session_listeners(factory)
    return factory


@dataclass
class RaceDatabase:
    admin_engine: Engine
    admin: sessionmaker[Session]  # test login role (superuser/BYPASSRLS): seed, inspect, block
    setup: sessionmaker[Session]  # app role: node registration
    media: list[sessionmaker[Session]]  # app role, tenant-bound: admissions and end transitions
    sweeper: sessionmaker[Session]  # app role, unbound: recovery sweeps
    workspaces: list[str]  # two organizations x two workspaces
    organization_of: dict[str, str]
    # Every database error raised on a racing connection (media or sweeper),
    # including the ones the contention retry loop absorbs.
    db_errors: list[str] = field(default_factory=list)

    def bind(self, db: Session, workspace_id: str) -> None:
        bind_tenant_context(
            db, workspace_id=workspace_id, organization_id=self.organization_of[workspace_id]
        )

    def racing_factories(self) -> list[sessionmaker[Session]]:
        return [*self.media, self.sweeper]

    def lock_waiters(self, application_name: str) -> int:
        with self.admin_engine.connect() as connection:
            return int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND application_name = :name "
                        "AND wait_event_type = 'Lock'"
                    ),
                    {"name": application_name},
                ).scalar_one()
            )

    def deadlocks(self) -> int:
        """PostgreSQL's cumulative deadlock counter for this database."""
        with self.admin_engine.connect() as connection:
            connection.execute(text("SELECT pg_stat_clear_snapshot()"))
            return int(
                connection.execute(
                    text(
                        "SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()"
                    )
                ).scalar_one()
            )

    def close_racing_connections(self) -> None:
        """Close every racing connection and wait until its backend has exited.

        A backend flushes its pending statistics (the deadlock counter among
        them) when it exits, so the counter is complete afterwards.
        """
        for factory in self.racing_factories():
            factory.kw["bind"].dispose()

        def gone() -> bool:
            with self.admin_engine.connect() as connection:
                return not connection.execute(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database() AND application_name = ANY(:names)"
                    ),
                    {"names": [MEDIA_APP, SWEEPER_APP]},
                ).scalar_one()

        assert _wait_until(gone), "racing backends did not exit"


@pytest.fixture
def race_db(migrated_database_url: str) -> Iterator[RaceDatabase]:
    url = migrated_database_url
    admin_engine = create_engine(url, pool_size=ADMIN_POOL, max_overflow=0)
    admin = sessionmaker(bind=admin_engine, autoflush=False, expire_on_commit=False)
    with admin_engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {', '.join(VOICE_TABLES)}"))
    workspaces = seed_workspaces(admin, 2) + seed_workspaces(admin, 2)
    with admin() as db:
        organization_of = {
            ws: org
            for ws, org in db.execute(
                select(Workspace.id, Workspace.organization_id).where(Workspace.id.in_(workspaces))
            ).all()
        }
    setup = _app_factory(url, SETUP_POOL, "hom169-setup")
    media = [_app_factory(url, MEDIA_POOL, MEDIA_APP) for _ in range(MEDIA_PLANES)]
    sweeper = _app_factory(url, SWEEPER_POOL, SWEEPER_APP)
    race = RaceDatabase(admin_engine, admin, setup, media, sweeper, workspaces, organization_of)

    def record(context: Any) -> None:
        original = context.original_exception
        sqlstate = getattr(original, "sqlstate", None)
        first_line = str(original).strip().splitlines()[0] if str(original).strip() else ""
        race.db_errors.append(f"{sqlstate}: {first_line}")

    for factory in race.racing_factories():
        event.listen(factory.kw["bind"], "handle_error", record)
    try:
        yield race
    finally:
        for factory in (setup, *media, sweeper):
            factory.kw["bind"].dispose()
        admin_engine.dispose()


def _admit(race: RaceDatabase, plane: VoiceControlPlane, workspace_id: str, external_id: str):
    with race.media[0]() as db:
        race.bind(db, workspace_id)
        result = plane.admit(
            db,
            workspace_id=workspace_id,
            external_call_id=external_id,
            direction="inbound",
            channel="sip",
        )
    assert result.admitted, result.reason
    return result.session


def _end(
    race: RaceDatabase,
    plane: VoiceControlPlane,
    factory: sessionmaker[Session],
    call: VoiceCallSession,
    barrier: threading.Barrier | None = None,
    hold_back: float = 0.0,
) -> tuple[str, str | None]:
    """A media worker ending ``call``; returns the state and reason it was told."""
    with factory() as db:
        race.bind(db, call.workspace_id)
        if barrier is not None:
            db.connection()  # check out and begin before the release
            barrier.wait()
            if hold_back:
                time.sleep(hold_back)
        ended = plane.transition(
            db,
            workspace_id=call.workspace_id,
            session_id=call.id,
            action="end",
            node_generation=call.node_generation or 0,
        )
        return ended.state.value, ended.end_reason


def _sweep(
    race: RaceDatabase,
    plane: VoiceControlPlane,
    barrier: threading.Barrier | None = None,
    stagger: float = 0.0,
) -> dict:
    """The recovery sweeper: an unbound session, as the Celery task opens."""
    with race.sweeper() as db:
        if barrier is not None:
            db.connection()
            barrier.wait()
            if stagger:
                time.sleep(stagger)
        report = plane.recover(db)
        db.commit()
    return report


def _two_live_calls(race: RaceDatabase, sweep: Sweep, clock: Clock, admitter: VoiceControlPlane):
    """One node carrying the call a media worker ends and a bystander call
    that nobody ends; then the node (or the slot leases) lapse."""
    with race.setup() as db:
        admitter.register_node(db, node_id="n1", max_concurrent=5)
    ending = _admit(race, admitter, race.workspaces[0], "ending")
    bystander = _admit(race, admitter, race.workspaces[2], "bystander")  # another organization
    assert ending.node_id == bystander.node_id == "n1"
    clock.advance(sweep.lapse_seconds)
    return ending, bystander


# -- deterministic interleavings ----------------------------------------------------


@pytest.mark.postgres
@pytest.mark.parametrize("sweep", SWEEP_PARAMS)
def test_sweep_keeps_a_call_ended_while_it_ran(race_db, sweep):
    """DENY: the sweep read the call as live, then the media worker committed
    ENDED; the sweep must not write FAILED over it. ALLOW-CONTROL: the
    bystander call nobody ended is still failed and its slot released."""
    race = race_db
    deadlocks_before = race.deadlocks()
    clock = Clock()
    admitter = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    media = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    sweeper = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    ending, bystander = _two_live_calls(race, sweep, clock, admitter)
    labels = {ending.id: "ending", bystander.id: "bystander"}

    # A third party holds the ending call's slot row, so the end transition
    # locks the call and then parks on its slot write while the sweep starts.
    with race.admin_engine.connect() as blocker:
        blocker.execute(
            text("SELECT id FROM voice_capacity_slots WHERE session_id = :id FOR UPDATE"),
            {"id": ending.id},
        )
        end_worker = Worker("end", lambda: _end(race, media, race.media[0], ending))
        sweep_worker = Worker("sweep", lambda: _sweep(race, sweeper))
        try:
            end_worker.start()
            assert _wait_until(lambda: race.lock_waiters(MEDIA_APP) >= 1), (
                "the end transition never waited on the slot blocker"
            )
            sweep_worker.start()
            # The sweep reads the call as live (its end is not committed) and
            # then waits on a lock the media worker holds or queues for.
            assert _wait_until(lambda: race.lock_waiters(SWEEPER_APP) >= 1), (
                "the sweep never queued behind the media worker's call lock"
            )
        finally:
            blocker.rollback()
        _join(end_worker, sweep_worker)

    assert end_worker.error is None, repr(end_worker.error)
    assert sweep_worker.error is None, repr(sweep_worker.error)
    race.close_racing_connections()
    observed = {
        "end_returned": end_worker.result,
        "stored": _call_records(race.admin, [ending.id, bystander.id]),
        "report": _report_by_label(sweep_worker.result, labels),
        "media_releases": _releases(media),
        "sweeper_releases": _releases(sweeper),
        "held": _held(race.admin, [ending.id, bystander.id]),
        "db_errors": race.db_errors,
        "deadlocks": race.deadlocks() - deadlocks_before,
    }
    assert observed == {
        "end_returned": COMPLETED,
        "stored": {
            ending.id: ("ended", "completed", False, "completed"),
            bystander.id: ("failed", sweep.reason, False, sweep.reason),
        },
        "report": _expected_report(sweep, ["n1"], ["bystander"]),
        "media_releases": {"completed": 1},
        "sweeper_releases": {sweep.reason: 1},
        "held": 0,
        "db_errors": [],
        "deadlocks": 0,
    }


@pytest.mark.postgres
@pytest.mark.parametrize("sweep", SWEEP_PARAMS)
def test_sweep_and_end_transition_lock_in_one_order(race_db, sweep):
    """DENY: the media worker holds the call's row lock (taken first, as the
    documented order says) when the sweep starts; the sweep must queue behind
    it instead of locking the slot first, so no deadlock is detected and no
    retry is needed. ALLOW-CONTROL: the worker that already held the call
    ends it; the bystander is failed by the sweep."""
    race = race_db
    deadlocks_before = race.deadlocks()
    clock = Clock()
    pausing = PausingClock(clock)
    admitter = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    media = VoiceControlPlane(sweep.policy, clock=pausing, metrics=VoiceMetrics())
    sweeper = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    ending, bystander = _two_live_calls(race, sweep, clock, admitter)
    labels = {ending.id: "ending", bystander.id: "bystander"}

    pausing.arm()
    end_worker = Worker("end", lambda: _end(race, media, race.media[0], ending))
    sweep_worker = Worker("sweep", lambda: _sweep(race, sweeper))
    try:
        end_worker.start()
        assert pausing.paused.wait(WAIT_SECONDS), "the end transition never read its clock"
        sweep_worker.start()
        assert _wait_until(lambda: race.lock_waiters(SWEEPER_APP) >= 1), (
            "the sweep never queued behind the media worker's call lock"
        )
    finally:
        pausing.resume.set()
    _join(end_worker, sweep_worker)

    assert sweep_worker.error is None, repr(sweep_worker.error)
    race.close_racing_connections()
    observed = {
        "db_errors": race.db_errors,
        "deadlocks": race.deadlocks() - deadlocks_before,
        "end_returned": end_worker.result if end_worker.error is None else repr(end_worker.error),
        "stored": _call_records(race.admin, [ending.id, bystander.id]),
        "report": _report_by_label(sweep_worker.result, labels),
        "media_releases": _releases(media),
        "sweeper_releases": _releases(sweeper),
        "held": _held(race.admin, [ending.id, bystander.id]),
    }
    assert observed == {
        "db_errors": [],
        "deadlocks": 0,
        "end_returned": COMPLETED,
        "stored": {
            ending.id: ("ended", "completed", False, "completed"),
            bystander.id: ("failed", sweep.reason, False, sweep.reason),
        },
        "report": _expected_report(sweep, ["n1"], ["bystander"]),
        "media_releases": {"completed": 1},
        "sweeper_releases": {sweep.reason: 1},
        "held": 0,
    }


@pytest.mark.postgres
@pytest.mark.parametrize("sweep", SWEEP_PARAMS)
def test_sweep_rejudges_capacity_renewed_while_it_waited(race_db, sweep):
    """DENY: the sweep read the node/lease as lapsed, then waited on a call
    lock while the node heartbeated (renewing the node and its leases); after
    the wait it must not fail the call or release its slot on a healthy node."""
    race = race_db
    clock = Clock()
    admitter = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    media = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    sweeper = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    with race.setup() as db:
        node = admitter.register_node(db, node_id="n1", max_concurrent=5)
        generation = node.generation
    call = _admit(race, admitter, race.workspaces[0], "renewed")
    clock.advance(sweep.lapse_seconds)
    before = _call_records(race.admin, [call.id])

    with race.admin_engine.connect() as blocker:
        blocker.execute(
            text("SELECT id FROM voice_call_sessions WHERE id = :id FOR UPDATE"), {"id": call.id}
        )
        sweep_worker = Worker("sweep", lambda: _sweep(race, sweeper))
        try:
            sweep_worker.start()
            assert _wait_until(lambda: race.lock_waiters(SWEEPER_APP) >= 1), (
                "the sweep never waited on the call lock"
            )
            with race.setup() as db:
                media.heartbeat(db, node_id="n1", generation=generation)
        finally:
            blocker.rollback()
        _join(sweep_worker)

    assert sweep_worker.error is None, repr(sweep_worker.error)
    race.close_racing_connections()
    assert {
        "report": sweep_worker.result,
        "stored": _call_records(race.admin, [call.id]),
        "held": _held(race.admin, [call.id]),
        "sweeper_releases": _releases(sweeper),
    } == {
        "report": {"lost_nodes": [], "failed_calls": {END_NODE_LOST: [], END_LEASE_EXPIRED: []}},
        "stored": before,
        "held": 1,
        "sweeper_releases": {},
    }


# -- repeated barrier-released bursts ------------------------------------------------


def _burst(
    race: RaceDatabase,
    sweep: Sweep,
    clock: Clock,
    admitter: VoiceControlPlane,
    burst: int,
) -> dict[str, Any]:
    """One sweep and BURST_CALLS end transitions released together; returns
    the per-burst findings (empty lists when every invariant holds)."""
    label = f"{sweep.reason}-b{burst}"
    node_id = f"{label}-node"
    with race.setup() as db:
        admitter.register_node(db, node_id=node_id, max_concurrent=BURST_CALLS)
    calls = [
        _admit(race, admitter, race.workspaces[index % len(race.workspaces)], f"{label}-{index}")
        for index in range(BURST_CALLS)
    ]
    labels = {call.id: f"b{burst}-{index}" for index, call in enumerate(calls)}
    clock.advance(sweep.lapse_seconds)

    media = [
        VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
        for _ in range(MEDIA_PLANES)
    ]
    sweeper = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    barrier = threading.Barrier(BURST_CALLS + 1)

    def end_work(index: int) -> Callable[[], tuple[str, str | None]]:
        plane = index % MEDIA_PLANES
        hold_back = ENDER_HOLD_BACK_SECONDS[burst] if index % 2 else 0.0
        return lambda: _end(race, media[plane], race.media[plane], calls[index], barrier, hold_back)

    enders = [Worker(f"end-{index}", end_work(index)) for index in range(BURST_CALLS)]
    stagger = SWEEPER_STAGGER_SECONDS[burst]
    sweep_worker = Worker("sweep", lambda: _sweep(race, sweeper, barrier, stagger))
    started = time.perf_counter()
    for worker in (*enders, sweep_worker):
        worker.start()
    _join(*enders, sweep_worker)
    wall_seconds = time.perf_counter() - started

    winners = [call.id for call, w in zip(calls, enders, strict=True) if w.error is None]
    losers = [
        call.id
        for call, w in zip(calls, enders, strict=True)
        if isinstance(w.error, sweep.loser_error)
    ]
    unexpected = [
        f"{labels[call.id]}: {w.result if w.error is None else repr(w.error)}"
        for call, w in zip(calls, enders, strict=True)
        if (w.error is None and w.result != COMPLETED)
        or (w.error is not None and not isinstance(w.error, sweep.loser_error))
    ]
    stored = _call_records(race.admin, [call.id for call in calls])
    findings: dict[str, Any] = {
        "sweep_error": [] if sweep_worker.error is None else [repr(sweep_worker.error)],
        "unexpected_end_outcome": unexpected,
        # DENY: the media worker was told ENDED/completed; the store disagrees.
        "ended_call_overwritten": [
            f"{labels[cid]}: {stored[cid][:2]}" for cid in winners if stored[cid][:2] != COMPLETED
        ],
        # ALLOW-CONTROL: a call nobody ended is failed by the sweep.
        "unended_call_not_failed": [
            f"{labels[cid]}: {stored[cid][:2]}"
            for cid in losers
            if stored[cid][:2] != ("failed", sweep.reason)
        ],
        "slot_not_released_with_call_reason": [
            f"{labels[cid]}: call {record[1]!r}, slot held={record[2]} reason={record[3]!r}"
            for cid, record in stored.items()
            if record[2] or record[3] != record[1]
        ],
        "report_mismatch": [],
        "metrics_mismatch": [],
        "held_after_burst": _held(race.admin, [call.id for call in calls]),
    }
    if sweep_worker.error is None:
        report = _report_by_label(sweep_worker.result, labels)
        expected = _expected_report(sweep, [node_id], [labels[cid] for cid in losers])
        if report != expected:
            findings["report_mismatch"] = [{"reported": report, "expected": expected}]
    releases = {"media": _releases(*media), "sweeper": _releases(sweeper)}
    expected_releases = {
        "media": {"completed": len(winners)} if winners else {},
        "sweeper": {sweep.reason: len(losers)} if losers else {},
    }
    if releases != expected_releases:
        findings["metrics_mismatch"] = [{"counted": releases, "expected": expected_releases}]
    findings["_summary"] = {
        "burst": burst,
        "winners": len(winners),
        "losers": len(losers),
        "overwritten": len(findings["ended_call_overwritten"]),
        "wall_seconds": round(wall_seconds, 3),
    }
    return findings


@pytest.mark.postgres
@pytest.mark.parametrize("sweep", SWEEP_PARAMS)
def test_sweep_racing_end_transitions_in_bursts(race_db, sweep):
    race = race_db
    deadlocks_before = race.deadlocks()
    clock = Clock()
    admitter = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
    bursts = [_burst(race, sweep, clock, admitter, burst) for burst in range(BURSTS)]
    race.close_racing_connections()
    deadlocks = race.deadlocks() - deadlocks_before
    summaries = [findings.pop("_summary") for findings in bursts]
    print(
        f"\nvoice recovery race ({sweep.reason}):",
        json.dumps(
            {
                "bursts": summaries,
                "db_errors": len(race.db_errors),
                "deadlocks": deadlocks,
            }
        ),
    )
    merged: dict[str, Any] = {}
    for burst, findings in enumerate(bursts):
        for key, value in findings.items():
            if value:
                merged.setdefault(key, []).append({f"b{burst}": value})
    merged["db_errors"] = race.db_errors
    merged["deadlocks"] = deadlocks
    assert merged == {"db_errors": [], "deadlocks": 0}
    # Non-vacuity: across the bursts the sweep really raced the transitions:
    # some calls were ended by their media worker, others failed by the sweep.
    assert sum(s["winners"] for s in summaries) > 0
    assert sum(s["losers"] for s in summaries) > 0


# -- SQLite fallback -------------------------------------------------------------------


@pytest.mark.parametrize("sweep", SWEEP_PARAMS)
def test_sqlite_sweep_fails_and_counts_only_the_calls_it_ends(tmp_path, sweep):
    """The sweep's accounting on the SQLite fallback: a call ended before the
    sweep stays ENDED and is neither reported nor counted again; the live
    calls are failed, reported and counted once; a second sweep is a no-op."""
    factory = sqlite_session_factory(tmp_path / "voice.db", pool_size=4)
    try:
        ws = seed_workspaces(factory, 1)[0]
        clock = Clock()
        admitter = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
        media = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
        sweeper = VoiceControlPlane(sweep.policy, clock=clock, metrics=VoiceMetrics())
        with factory() as db:
            admitter.register_node(db, node_id="n1", max_concurrent=5)
            calls = {
                label: admitter.admit(
                    db, workspace_id=ws, external_call_id=label, direction="inbound", channel="sip"
                ).session
                for label in ("ended", "live-1", "live-2")
            }
            media.transition(
                db,
                workspace_id=ws,
                session_id=calls["ended"].id,
                action="end",
                node_generation=calls["ended"].node_generation or 0,
            )
        clock.advance(sweep.lapse_seconds)
        labels = {call.id: label for label, call in calls.items()}
        with factory() as db:
            first = _report_by_label(sweeper.recover(db), labels)
        with factory() as db:
            second = _report_by_label(sweeper.recover(db), labels)
        ids = [call.id for call in calls.values()]
        observed = {
            "first": first,
            "second": second,
            "stored": {labels[k]: v for k, v in _call_records(factory, ids).items()},
            "media_releases": _releases(media),
            "sweeper_releases": _releases(sweeper),
            "held": _held(factory, ids),
        }
        assert observed == {
            "first": _expected_report(sweep, ["n1"], ["live-1", "live-2"]),
            "second": _expected_report(sweep, [], []),
            "stored": {
                "ended": ("ended", "completed", False, "completed"),
                "live-1": ("failed", sweep.reason, False, sweep.reason),
                "live-2": ("failed", sweep.reason, False, sweep.reason),
            },
            "media_releases": {"completed": 1},
            "sweeper_releases": {sweep.reason: 2},
            "held": 0,
        }
    finally:
        factory.kw["bind"].dispose()
