from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, func, inspect, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import Pool

from backend.config import Settings, reset_settings_cache
from backend.database.relational import build_engine
from backend.database.saas_models import Organization, Workspace
from backend.database.tenant_context import bind_tenant_context, install_session_listeners
from backend.tests._database import (
    TEST_APP_ROLE,
    configured_database_url,
    inspection_session_factory,
    reset_postgres_schema,
)
from backend.voice.admission import (
    REJECT_CAPACITY_GLOBAL,
    REJECT_CAPACITY_NODE,
    REJECT_CAPACITY_WORKSPACE,
    AdmissionPolicy,
    VoiceControlPlane,
    VoiceNotFound,
)
from backend.voice.metrics import VoiceMetrics
from backend.voice.models import CallState, VoiceCallSession, VoiceCapacitySlot
from backend.voice.state import StaleGeneration

pytestmark = pytest.mark.postgres

HOMAYAR_ROOT = Path(__file__).resolve().parents[2]
MAKER_MODULE = "backend/tests/test_voice_admission_postgres.py"
SQLITE_PROOF_MODULES = ("backend/tests/test_voice_admission.py", "backend/tests/test_voice_api.py")
BASE_PROOF_NAMES = {
    "backend/tests/test_voice_admission.py": (
        "test_state_machine_legal_transitions_only",
        "test_limits_and_rejection_reasons",
        "test_least_loaded_node_and_idempotency",
        "test_illegal_transition_and_slot_release",
        "test_node_loss_recovery_and_fencing",
        "test_slot_lease_expiry_reclaims",
        "test_cross_tenant_session_is_invisible",
        "test_250_concurrent_admissions_never_over_admit",
        "test_concurrent_per_node_limit_is_exact",
        "test_persistent_lock_contention_backs_off_then_raises",
        "test_lock_contention_clearing_at_max_retries_succeeds",
        "test_non_contention_operational_error_is_not_retried",
        "test_integrity_error_retries_are_bounded_without_sleeping",
        "test_contention_backoff_is_bounded_deterministic_and_desynchronized",
        "test_concurrent_threads_get_distinct_identities_and_first_delays",
    ),
    "backend/tests/test_voice_api.py": (
        "test_voice_call_lifecycle_capacity_and_nodes",
        "test_voice_cross_tenant_and_permission_negatives",
    ),
}
PROBE_APPLICATION_NAME = "rec02-oracle-probe"
APP_POOL_SIZE = 40
CONNECTION_CEILING = 90
MODULE_SECONDS_CEILING = 120.0
BARRIER_SECONDS = 120
RECOVERY_SCOPE = "voice_recovery"


def _is_lock_statement(statement: str) -> bool:
    lowered = statement.lower()
    return (
        "voice_admission_locks" in lowered
        or "pg_advisory" in lowered
        or lowered.lstrip().startswith("lock table")
    )


class LockContenders:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._open: set[int] = set()
        self.peak = 0

    def started(self, conn: Any, statement: str) -> int:
        if not _is_lock_statement(statement):
            return 0
        with self._guard:
            self._open.add(id(conn))
            self.peak = max(self.peak, len(self._open))
            return len(self._open)

    def ended(self, conn: Any) -> None:
        with self._guard:
            self._open.discard(id(conn))

    def reset(self) -> None:
        with self._guard:
            self.peak = len(self._open)

    def attach(self, target: Any) -> None:
        event.listen(target, "before_cursor_execute", self._before)
        event.listen(target, "commit", self.ended)
        event.listen(target, "rollback", self.ended)

    def _before(self, conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        self.started(conn, statement)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class Deployment:
    factory: sessionmaker[Session]
    engine: Engine
    tenants: list[tuple[str, str]]
    contenders: LockContenders


def _migrate_to_head(url: str) -> None:
    reset_postgres_schema(url)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DATABASE_URL", url)
        patch.setenv("ENVIRONMENT", "test")
        reset_settings_cache()
        try:
            command.upgrade(Config(str(HOMAYAR_ROOT / "alembic.ini")), "head")
        finally:
            reset_settings_cache()
    reset_settings_cache()


def _seed_tenants(count: int) -> list[tuple[str, str]]:
    tenants: list[tuple[str, str]] = []
    with inspection_session_factory()() as db:
        for index in range(count):
            organization = Organization(name=f"Oracle org {index}", slug=f"oracle-org-{index}")
            db.add(organization)
            db.flush()
            workspace = Workspace(
                organization_id=organization.id,
                name=f"Oracle workspace {index}",
                slug=f"oracle-ws-{index}",
            )
            db.add(workspace)
            db.flush()
            tenants.append((workspace.id, organization.id))
        db.commit()
    return tenants


def _app_factory(engine: Engine) -> sessionmaker[Session]:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    install_session_listeners(factory)
    return factory


@pytest.fixture
def deployment(settings: Settings) -> Iterator[Deployment]:
    url = settings.database_url
    _migrate_to_head(url)
    tenants = _seed_tenants(3)
    engine = build_engine(
        settings.model_copy(
            update={"database_pool_size": APP_POOL_SIZE, "database_max_overflow": 0}
        )
    )
    contenders = LockContenders()
    contenders.attach(engine)
    try:
        yield Deployment(
            factory=_app_factory(engine), engine=engine, tenants=tenants, contenders=contenders
        )
    finally:
        engine.dispose()
        reset_postgres_schema(url)


@contextmanager
def tenant_session(factory: sessionmaker[Session], tenant: tuple[str, str]) -> Iterator[Session]:
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant[0], organization_id=tenant[1])
        yield db


def independent_planes(
    count: int, clock: Clock | None = None, **policy: Any
) -> list[VoiceControlPlane]:
    return [
        VoiceControlPlane(AdmissionPolicy(**policy), clock=clock, metrics=VoiceMetrics())
        for _ in range(count)
    ]


def register_nodes(dep: Deployment, plane: VoiceControlPlane, nodes: dict[str, int]) -> None:
    with tenant_session(dep.factory, dep.tenants[0]) as db:
        for node_id, capacity in nodes.items():
            plane.register_node(db, node_id=node_id, max_concurrent=capacity)


def admit(plane: VoiceControlPlane, db: Session, workspace_id: str, external_call_id: str):
    return plane.admit(
        db,
        workspace_id=workspace_id,
        external_call_id=external_call_id,
        direction="inbound",
        channel="sip",
    )


def held_slots(**filters: str) -> int:
    query = select(func.count(VoiceCapacitySlot.id)).where(VoiceCapacitySlot.released_at.is_(None))
    for key, value in filters.items():
        query = query.where(getattr(VoiceCapacitySlot, key) == value)
    with inspection_session_factory()() as db:
        return int(db.execute(query).scalar_one())


def held_by_node() -> dict[str, int]:
    with inspection_session_factory()() as db:
        rows = db.execute(
            select(VoiceCapacitySlot.node_id, func.count(VoiceCapacitySlot.id))
            .where(VoiceCapacitySlot.released_at.is_(None))
            .group_by(VoiceCapacitySlot.node_id)
        ).all()
    return {node_id: int(count) for node_id, count in rows}


def recorded_calls() -> Counter[str]:
    with inspection_session_factory()() as db:
        rows = db.execute(
            select(
                VoiceCallSession.state,
                VoiceCallSession.rejection_reason,
                func.count(VoiceCallSession.id),
            ).group_by(VoiceCallSession.state, VoiceCallSession.rejection_reason)
        ).all()
    return Counter({f"{state.value}:{reason or ''}": int(count) for state, reason, count in rows})


def call_state(session_id: str) -> tuple[str, str | None]:
    with inspection_session_factory()() as db:
        call = db.get(VoiceCallSession, session_id)
        assert call is not None
        return call.state.value, call.end_reason


def _wait(barrier: threading.Barrier) -> None:
    try:
        barrier.wait()
    except threading.BrokenBarrierError:
        pass


def _refused(attempt: Callable[[], Any]) -> bool:
    try:
        result = attempt()
    except Exception:
        return True
    return not result.admitted


@dataclass
class BurstReport:
    outcomes: Counter[str]
    by_workspace: dict[str, Counter[str]]
    errors: list[str]
    held_at_peak: int
    held_by_node_at_peak: dict[str, int]
    max_held_observed: int
    max_backends_observed: int
    lock_contenders_peak: int
    held_after_end: int


def run_burst(
    dep: Deployment,
    planes: list[VoiceControlPlane],
    tenant_per_call: list[tuple[str, str]],
    *,
    run_id: str,
    pre_read: bool = False,
) -> BurstReport:
    calls = len(tenant_per_call)
    start = threading.Barrier(calls, timeout=BARRIER_SECONDS)
    decided = threading.Barrier(calls + 1, timeout=BARRIER_SECONDS)
    measured = threading.Barrier(calls + 1, timeout=BARRIER_SECONDS)
    guard = threading.Lock()
    outcomes: Counter[str] = Counter()
    by_workspace: dict[str, Counter[str]] = defaultdict(Counter)
    errors: list[str] = []
    admitted: dict[int, tuple[str, int | None]] = {}

    def one_call(index: int) -> None:
        tenant = tenant_per_call[index]
        plane = planes[index % len(planes)]
        try:
            with tenant_session(dep.factory, tenant) as db:
                if pre_read:
                    db.execute(select(func.count(VoiceCallSession.id))).scalar_one()
                start.wait()
                result = admit(plane, db, tenant[0], f"{run_id}-{index}")
                key = "admitted" if result.admitted else str(result.reason)
                with guard:
                    outcomes[key] += 1
                    by_workspace[tenant[0]][key] += 1
                    if result.admitted:
                        admitted[index] = (result.session.id, result.session.node_generation)
        except Exception as exc:
            with guard:
                errors.append(f"admit {index}: {exc!r}")
        finally:
            _wait(decided)
            _wait(measured)
        if index not in admitted:
            return
        session_id, generation = admitted[index]
        try:
            with tenant_session(dep.factory, tenant) as db:
                for action in ("answer", "end"):
                    plane.transition(
                        db,
                        workspace_id=tenant[0],
                        session_id=session_id,
                        action=action,
                        node_generation=generation or 0,
                    )
        except Exception as exc:
            with guard:
                errors.append(f"lifecycle {index}: {exc!r}")

    stop = threading.Event()
    observed = {"held": 0, "backends": 0}

    def monitor() -> None:
        with inspection_session_factory()() as db:
            while not stop.is_set():
                held, backends = db.execute(
                    text(
                        "SELECT (SELECT count(*) FROM voice_capacity_slots "
                        "WHERE released_at IS NULL), "
                        "(SELECT count(*) FROM pg_stat_activity "
                        "WHERE datname = current_database())"
                    )
                ).one()
                db.rollback()
                observed["held"] = max(observed["held"], int(held))
                observed["backends"] = max(observed["backends"], int(backends))
                stop.wait(0.005)

    dep.contenders.reset()
    watcher = threading.Thread(target=monitor)
    watcher.start()
    threads = [threading.Thread(target=one_call, args=(index,)) for index in range(calls)]
    for thread in threads:
        thread.start()
    try:
        decided.wait()
        contenders_peak = dep.contenders.peak
        held_at_peak = held_slots()
        by_node = held_by_node()
    finally:
        stop.set()
        watcher.join()
        _wait(measured)
        for thread in threads:
            thread.join(BARRIER_SECONDS)
    return BurstReport(
        outcomes=outcomes,
        by_workspace=dict(by_workspace),
        errors=errors,
        held_at_peak=held_at_peak,
        held_by_node_at_peak=by_node,
        max_held_observed=observed["held"],
        max_backends_observed=observed["backends"],
        lock_contenders_peak=contenders_peak,
        held_after_end=held_slots(),
    )


def test_application_sessions_run_as_the_non_bypass_app_role(deployment):
    with tenant_session(deployment.factory, deployment.tenants[0]) as db:
        role, bypass = db.execute(
            text(
                "SELECT current_user, rolsuper OR rolbypassrls FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one()
        forced = db.execute(
            text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'voice_call_sessions'")
        ).scalar_one()
        migrated = db.execute(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
        assert migrated.scalar_one() is True
    assert role == TEST_APP_ROLE
    assert bypass is False
    assert forced is True


def test_global_cap_is_exact_across_independent_planes(deployment):
    planes = independent_planes(4, global_limit=200, default_workspace_limit=200)
    register_nodes(deployment, planes[0], {f"g-node-{index}": 64 for index in range(4)})
    tenants = [deployment.tenants[index % 3] for index in range(250)]

    report = run_burst(deployment, planes, tenants, run_id="global")

    assert report.errors == []
    assert report.outcomes == Counter({"admitted": 200, REJECT_CAPACITY_GLOBAL: 50})
    assert report.held_at_peak == 200
    assert report.max_held_observed <= 200
    assert all(held <= 64 for held in report.held_by_node_at_peak.values())
    assert report.lock_contenders_peak >= 2
    assert report.max_backends_observed < CONNECTION_CEILING
    assert report.held_after_end == 0
    assert recorded_calls() == Counter({"ended:": 200, f"rejected:{REJECT_CAPACITY_GLOBAL}": 50})


def test_node_cap_is_exact_across_independent_planes(deployment):
    planes = independent_planes(4, global_limit=1000, default_workspace_limit=1000)
    register_nodes(deployment, planes[0], {"n-node-a": 20, "n-node-b": 20})
    tenants = [deployment.tenants[index % 3] for index in range(60)]

    report = run_burst(deployment, planes, tenants, run_id="nodes")

    assert report.errors == []
    assert report.outcomes == Counter({"admitted": 40, REJECT_CAPACITY_NODE: 20})
    assert report.held_at_peak == 40
    assert report.held_by_node_at_peak == {"n-node-a": 20, "n-node-b": 20}
    assert report.max_held_observed <= 40
    assert report.lock_contenders_peak >= 2
    assert report.held_after_end == 0


def test_workspace_row_limit_is_exact_across_planes_and_spares_other_tenants(deployment):
    planes = independent_planes(4, global_limit=1000, default_workspace_limit=1000)
    register_nodes(deployment, planes[0], {"w-node-a": 500, "w-node-b": 500})
    limited, other = deployment.tenants[0], deployment.tenants[1]
    with tenant_session(deployment.factory, limited) as db:
        planes[1].set_workspace_limit(db, workspace_id=limited[0], limit=7)
    tenants = [other if index % 4 == 1 else limited for index in range(32)]

    report = run_burst(deployment, planes, tenants, run_id="ws-row", pre_read=True)

    assert report.errors == []
    assert report.by_workspace[limited[0]] == Counter(
        {"admitted": 7, REJECT_CAPACITY_WORKSPACE: 17}
    )
    assert report.by_workspace[other[0]] == Counter({"admitted": 8})
    assert report.held_at_peak == 15
    assert report.max_held_observed <= 15
    assert report.held_after_end == 0


def test_workspace_default_limit_is_exact_across_planes(deployment):
    planes = independent_planes(8, global_limit=1000, default_workspace_limit=5)
    register_nodes(deployment, planes[0], {"d-node": 1000})
    tenant = deployment.tenants[2]

    report = run_burst(deployment, planes, [tenant] * 40, run_id="ws-default")

    assert report.errors == []
    assert report.outcomes == Counter({"admitted": 5, REJECT_CAPACITY_WORKSPACE: 35})
    assert report.lock_contenders_peak >= 2
    assert report.held_at_peak == 5
    assert report.max_held_observed <= 5
    assert report.held_after_end == 0


def test_serialization_failures_under_repeatable_read_are_absorbed(deployment):
    repeatable_engine = deployment.engine.execution_options(isolation_level="REPEATABLE READ")
    repeatable = Deployment(
        factory=_app_factory(repeatable_engine),
        engine=deployment.engine,
        tenants=deployment.tenants,
        contenders=deployment.contenders,
    )
    planes = independent_planes(4, global_limit=30, default_workspace_limit=1000)
    register_nodes(repeatable, planes[0], {"rr-node": 1000})
    tenants = [deployment.tenants[index % 3] for index in range(40)]

    report = run_burst(repeatable, planes, tenants, run_id="rr")

    assert report.errors == []
    assert report.outcomes == Counter({"admitted": 30, REJECT_CAPACITY_GLOBAL: 10})
    assert report.held_at_peak == 30
    assert report.max_held_observed <= 30
    assert report.held_after_end == 0


def test_admission_writes_only_into_the_bound_tenant(deployment):
    plane = independent_planes(1, global_limit=10)[0]
    register_nodes(deployment, plane, {"t-node": 10})
    tenant_a, tenant_b = deployment.tenants[:2]

    with tenant_session(deployment.factory, tenant_b) as db:
        assert _refused(lambda: admit(plane, db, tenant_a[0], "foreign-context"))
    with deployment.factory() as db:
        assert _refused(lambda: admit(plane, db, tenant_a[0], "no-context"))
    assert held_slots() == 0
    assert recorded_calls() == Counter()

    with tenant_session(deployment.factory, tenant_a) as db:
        own = admit(plane, db, tenant_a[0], "own-context")
    assert own.admitted
    assert held_slots(workspace_id=tenant_a[0]) == 1
    assert recorded_calls() == Counter({"admitted:": 1})


def test_tenant_bound_session_cannot_read_another_workspace_call(deployment):
    plane = independent_planes(1, global_limit=10)[0]
    register_nodes(deployment, plane, {"r-node": 10})
    tenant_a, tenant_b = deployment.tenants[:2]
    ids: dict[str, str] = {}
    for label, tenant in (("a", tenant_a), ("b", tenant_b)):
        with tenant_session(deployment.factory, tenant) as db:
            result = admit(plane, db, tenant[0], "same-external-id")
            assert result.admitted
            ids[label] = result.session.id
    assert ids["a"] != ids["b"]

    with tenant_session(deployment.factory, tenant_a) as db:
        assert set(db.execute(select(VoiceCallSession.id)).scalars()) == {ids["a"]}
        assert db.get(VoiceCallSession, ids["b"]) is None
        with pytest.raises(VoiceNotFound):
            plane.get_session(db, workspace_id=tenant_b[0], session_id=ids["b"])
        with pytest.raises(VoiceNotFound):
            plane.transition(
                db, workspace_id=tenant_b[0], session_id=ids["b"], action="end", node_generation=1
            )
        assert plane.get_session(db, workspace_id=tenant_a[0], session_id=ids["a"]).id == ids["a"]

    with tenant_session(deployment.factory, tenant_b) as db:
        assert set(db.execute(select(VoiceCallSession.id)).scalars()) == {ids["b"]}
        assert plane.get_session(db, workspace_id=tenant_b[0], session_id=ids["b"]).id == ids["b"]

    with deployment.factory() as db:
        assert db.execute(select(func.count(VoiceCallSession.id))).scalar_one() == 0

    assert call_state(ids["b"]) == ("admitted", None)
    assert held_slots() == 2


def test_node_loss_recovery_fences_stale_writers_across_tenants(deployment):
    clock = Clock()
    ops, recoverer = independent_planes(2, clock, heartbeat_ttl_seconds=10, slot_lease_seconds=30)
    tenant_a, tenant_b = deployment.tenants[:2]
    factory = deployment.factory
    with tenant_session(factory, tenant_a) as db:
        ops.register_node(db, node_id="lost", max_concurrent=5)
        ops.register_node(db, node_id="kept", max_concurrent=5)
    calls: dict[tuple[str, str], str] = {}
    for serving, idle in (("lost", "kept"), ("kept", "lost")):
        with tenant_session(factory, tenant_a) as db:
            ops.set_draining(db, node_id=idle, draining=True)
        for label, tenant in (("a", tenant_a), ("b", tenant_b)):
            with tenant_session(factory, tenant) as db:
                result = admit(ops, db, tenant[0], f"{label}-{serving}")
            assert result.admitted
            assert result.session.node_id == serving
            assert result.session.node_generation == 1
            calls[(label, serving)] = result.session.id
        with tenant_session(factory, tenant_a) as db:
            ops.set_draining(db, node_id=idle, draining=False)

    clock.advance(8)
    with tenant_session(factory, tenant_a) as db:
        ops.heartbeat(db, node_id="kept", generation=1)
    clock.advance(5)
    with tenant_session(factory, tenant_a) as db:
        report = recoverer.recover(db)

    assert report["lost_nodes"] == ["lost"]
    assert sorted(report["failed_calls"]["node_lost"]) == sorted(
        [calls[("a", "lost")], calls[("b", "lost")]]
    )
    assert call_state(calls[("a", "lost")]) == ("failed", "node_lost")
    assert call_state(calls[("b", "lost")]) == ("failed", "node_lost")
    assert call_state(calls[("a", "kept")]) == ("admitted", None)
    assert call_state(calls[("b", "kept")]) == ("admitted", None)
    assert held_slots(node_id="lost") == 0
    assert held_slots(node_id="kept") == 2

    for label, tenant in (("a", tenant_a), ("b", tenant_b)):
        with tenant_session(factory, tenant) as db:
            with pytest.raises(StaleGeneration):
                ops.transition(
                    db,
                    workspace_id=tenant[0],
                    session_id=calls[(label, "lost")],
                    action="end",
                    node_generation=1,
                )
    with tenant_session(factory, tenant_a) as db:
        with pytest.raises(StaleGeneration):
            ops.heartbeat(db, node_id="lost", generation=1)
        with pytest.raises(StaleGeneration):
            ops.heartbeat(db, node_id="lost", generation=2)

    with tenant_session(factory, tenant_a) as db:
        node = ops.register_node(db, node_id="lost", max_concurrent=5)
        assert node.generation == 2
        assert ops.heartbeat(db, node_id="lost", generation=2).generation == 2
        with pytest.raises(StaleGeneration):
            ops.heartbeat(db, node_id="lost", generation=1)
    with tenant_session(factory, tenant_b) as db:
        with pytest.raises(StaleGeneration):
            ops.transition(
                db,
                workspace_id=tenant_b[0],
                session_id=calls[("b", "lost")],
                action="end",
                node_generation=2,
            )

    with factory() as db:
        again = recoverer.recover(db)
    assert again["lost_nodes"] == []
    assert not any(again["failed_calls"].values())
    with tenant_session(factory, tenant_a) as db:
        assert recoverer.recover(db)["lost_nodes"] == []

    with tenant_session(factory, tenant_b) as db:
        with pytest.raises(StaleGeneration):
            ops.transition(
                db,
                workspace_id=tenant_b[0],
                session_id=calls[("b", "kept")],
                action="answer",
                node_generation=2,
            )
        answered = ops.transition(
            db,
            workspace_id=tenant_b[0],
            session_id=calls[("b", "kept")],
            action="answer",
            node_generation=1,
        )
        assert answered.state == CallState.ACTIVE
    with tenant_session(factory, tenant_a) as db:
        answered = ops.transition(
            db,
            workspace_id=tenant_a[0],
            session_id=calls[("a", "kept")],
            action="answer",
            node_generation=1,
        )
        assert answered.state == CallState.ACTIVE
    assert held_slots(node_id="kept") == 2
    assert call_state(calls[("a", "lost")]) == ("failed", "node_lost")


PROBE_SQL = (
    "SELECT current_user::text, "
    "coalesce(current_setting('homayar.workspace_id', true), ''), "
    "coalesce(current_setting('homayar.service_scope', true), ''), "
    "to_regclass('public.alembic_version') IS NOT NULL"
)
PROBED_METHODS = (
    "admit",
    "recover",
    "transition",
    "heartbeat",
    "register_node",
    "set_workspace_limit",
)


def _summarize(name: str, result: Any) -> dict[str, Any]:
    if name == "admit":
        return {
            "admitted": bool(result.admitted),
            "reason": result.reason,
            "idempotent": bool(result.idempotent),
        }
    if name == "recover":
        failed = result.get("failed_calls") or {}
        return {
            "lost_nodes": list(result.get("lost_nodes") or []),
            "failed": {key: len(value) for key, value in failed.items()},
        }
    if name == "set_workspace_limit":
        return {"value": result}
    loaded = inspect(result).dict
    value = loaded.get("state" if name == "transition" else "generation")
    return {"value": getattr(value, "value", value)}


class AdmissionProbe:
    def __init__(self) -> None:
        self._guard = threading.RLock()
        self._local = threading.local()
        self._plane_index: dict[int, int] = {}
        self._planes: list[Any] = []
        self._inside_admit = 0
        self._live_connections = 0
        self._current_test: str | None = None
        self._session_started = 0.0
        self.contenders = LockContenders()
        self.data: dict[str, Any] = {"tests": {}, "items": {}, "max_live_connections": 0}

    def install(self) -> None:
        for name in PROBED_METHODS:
            setattr(VoiceControlPlane, name, self._wrap(name, getattr(VoiceControlPlane, name)))
        event.listen(Engine, "before_cursor_execute", self._before_cursor)
        event.listen(Engine, "commit", self.contenders.ended)
        event.listen(Engine, "rollback", self.contenders.ended)
        event.listen(Pool, "connect", self._connected)
        event.listen(Pool, "close", self._closed)
        event.listen(Pool, "close_detached", self._closed_detached)

    def _bucket(self) -> dict[str, Any]:
        return self.data["tests"].setdefault(
            self._current_test or "<outside>", {"calls": [], "seconds": None}
        )

    def _wrap(self, name: str, original: Callable[..., Any]) -> Callable[..., Any]:
        probe = self

        @functools.wraps(original)
        def wrapper(plane: Any, *args: Any, **kwargs: Any) -> Any:
            record = probe._open(name, plane, kwargs)
            previous = getattr(probe._local, "record", None)
            probe._local.record = record
            try:
                result = original(plane, *args, **kwargs)
            except BaseException as exc:
                record["error"] = type(exc).__name__
                raise
            else:
                record["result"] = _summarize(name, result)
                return result
            finally:
                record["end"] = time.perf_counter()
                probe._local.record = previous
                if name == "admit":
                    with probe._guard:
                        probe._inside_admit -= 1

        return wrapper

    def _open(self, name: str, plane: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
        policy = getattr(plane, "policy", None)
        with self._guard:
            index = self._plane_index.get(id(plane))
            if index is None:
                index = len(self._planes)
                self._plane_index[id(plane)] = index
                self._planes.append(plane)
            record: dict[str, Any] = {
                "method": name,
                "plane": index,
                "workspace_id": kwargs.get("workspace_id"),
                "limit": kwargs.get("limit"),
                "global_limit": getattr(policy, "global_limit", None),
                "default_workspace_limit": getattr(policy, "default_workspace_limit", None),
                "start": time.perf_counter(),
                "end": None,
                "error": None,
                "result": None,
                "concurrent": 0,
                "observations": [],
            }
            if name == "admit":
                self._inside_admit += 1
                record["concurrent"] = self._inside_admit
            self._bucket()["calls"].append(record)
            return record

    def _before_cursor(self, conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        contenders = self.contenders.started(conn, statement)
        record = getattr(self._local, "record", None)
        if record is None:
            return
        lowered = statement.lstrip().lower()
        if contenders:
            kind = "lock"
        elif lowered.startswith("insert into voice_call_sessions"):
            kind = "tenant_write"
        elif lowered.startswith("update voice_call_sessions"):
            kind = "call_update"
        else:
            return
        observation: dict[str, Any] = {
            "kind": kind,
            "dialect": conn.dialect.name,
            "contenders": contenders,
        }
        if conn.dialect.name == "postgresql":
            cursor = conn.connection.dbapi_connection.cursor()
            try:
                cursor.execute(PROBE_SQL)
                role, workspace, scope, migrated = cursor.fetchone()
            finally:
                cursor.close()
            observation.update(role=role, workspace=workspace, scope=scope, migrated=bool(migrated))
        record["observations"].append(observation)

    def _connected(self, _dbapi_connection, _record) -> None:
        with self._guard:
            self._live_connections += 1
            self.data["max_live_connections"] = max(
                self.data["max_live_connections"], self._live_connections
            )

    def _closed(self, _dbapi_connection, _record) -> None:
        with self._guard:
            self._live_connections -= 1

    def _closed_detached(self, _dbapi_connection) -> None:
        with self._guard:
            self._live_connections -= 1

    def pytest_sessionstart(self) -> None:
        self._session_started = time.perf_counter()

    def pytest_collection_modifyitems(self, items: list[pytest.Item]) -> None:
        for item in items:
            self.data["items"][item.nodeid] = {
                "name": getattr(item, "originalname", None) or item.name,
                "postgres": item.get_closest_marker("postgres") is not None,
                "phases": [],
            }

    @pytest.hookimpl(wrapper=True)
    def pytest_runtest_protocol(self, item: pytest.Item) -> Any:
        self._current_test = item.nodeid
        started = time.perf_counter()
        try:
            return (yield)
        finally:
            self._bucket()["seconds"] = time.perf_counter() - started
            self._current_test = None

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        entry = self.data["items"].setdefault(
            report.nodeid,
            {"name": report.nodeid.split("::")[-1], "postgres": False, "phases": []},
        )
        entry["phases"].append([report.when, report.outcome])

    def pytest_sessionfinish(self, exitstatus: int) -> None:
        self.data["session_seconds"] = time.perf_counter() - self._session_started
        self.data["exit_status"] = int(exitstatus)


def run_probed(output: str, arguments: list[str]) -> int:
    probe = AdmissionProbe()
    probe.install()
    exit_code = int(pytest.main(arguments, plugins=[probe]))
    probe.data["exit_code"] = exit_code
    Path(output).write_text(json.dumps(probe.data, default=str))
    return exit_code


class BackendMonitor:
    def __init__(self, url: str, application_name: str) -> None:
        self._engine = create_engine(url, pool_size=1, max_overflow=0)
        self._application_name = application_name
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._poll)
        self.peak = 0

    def __enter__(self) -> BackendMonitor:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self._engine.dispose()

    def _poll(self) -> None:
        query = text("SELECT count(*) FROM pg_stat_activity WHERE application_name = :name")
        with self._engine.connect() as connection:
            while not self._stop.is_set():
                count = connection.execute(query, {"name": self._application_name}).scalar_one()
                connection.rollback()
                self.peak = max(self.peak, int(count))
                self._stop.wait(0.02)


@dataclass
class ProbedRun:
    returncode: int
    output: str
    report: dict[str, Any]
    max_backends: int


def probed_pytest(targets: list[str], workdir: Path) -> ProbedRun:
    output = workdir / "probe.json"
    code = (
        "import sys\n"
        "from backend.tests.test_oracle_rec02 import run_probed\n"
        "sys.exit(run_probed(sys.argv[1], sys.argv[2:]))\n"
    )
    arguments = [
        sys.executable,
        "-c",
        code,
        str(output),
        *targets,
        "-q",
        "-p",
        "no:cacheprovider",
        "-rs",
    ]
    environment = {**os.environ, "PGAPPNAME": PROBE_APPLICATION_NAME}
    with BackendMonitor(configured_database_url() or "", PROBE_APPLICATION_NAME) as monitor:
        completed = subprocess.run(  # noqa: S603 - fixed argv, test-owned process
            arguments,
            cwd=HOMAYAR_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
    report = json.loads(output.read_text()) if output.is_file() else {}
    return ProbedRun(
        returncode=completed.returncode,
        output=completed.stdout[-8000:] + completed.stderr[-4000:],
        report=report,
        max_backends=monitor.peak,
    )


@pytest.fixture(scope="module")
def maker_run(tmp_path_factory: pytest.TempPathFactory) -> ProbedRun | None:
    if not (HOMAYAR_ROOT / MAKER_MODULE).is_file():
        return None
    return probed_pytest([MAKER_MODULE], tmp_path_factory.mktemp("rec02-maker"))


@pytest.fixture(scope="module")
def sqlite_proofs_run(tmp_path_factory: pytest.TempPathFactory) -> ProbedRun:
    return probed_pytest(list(SQLITE_PROOF_MODULES), tmp_path_factory.mktemp("rec02-sqlite"))


def delivered(run: ProbedRun | None) -> dict[str, Any]:
    assert run is not None, f"{MAKER_MODULE} was not delivered"
    assert run.report.get("items"), run.output
    return run.report


def passed(entry: dict[str, Any]) -> bool:
    phases = entry["phases"]
    return ["call", "passed"] in phases and all(outcome == "passed" for _, outcome in phases)


def admission_bursts(calls: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    admits = sorted(
        (call for call in calls if call["method"] == "admit" and call["end"] is not None),
        key=lambda call: call["start"],
    )
    bursts: list[list[dict[str, Any]]] = []
    horizon = float("-inf")
    for call in admits:
        if bursts and call["start"] <= horizon:
            bursts[-1].append(call)
            horizon = max(horizon, call["end"])
        else:
            bursts.append([call])
            horizon = call["end"]
    return bursts


def all_bursts(report: dict[str, Any]) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (nodeid, burst)
        for nodeid, bucket in report.get("tests", {}).items()
        for burst in admission_bursts(bucket["calls"])
    ]


def outcome_of(call: dict[str, Any]) -> str:
    if call["error"]:
        return f"raised:{call['error']}"
    return "admitted" if call["result"]["admitted"] else str(call["result"]["reason"])


def burst_problems(
    burst: list[dict[str, Any]],
    *,
    expected: Counter[str],
    min_planes: int,
    dialect: str = "postgresql",
) -> list[str]:
    problems: list[str] = []
    outcomes = Counter(outcome_of(call) for call in burst)
    if outcomes != expected:
        problems.append(f"outcomes {dict(outcomes)} != {dict(expected)}")
    planes = {call["plane"] for call in burst}
    if len(planes) < min_planes:
        problems.append(f"{len(planes)} control plane(s) < {min_planes}")
    if max(call["concurrent"] for call in burst) < 2 * min_planes:
        problems.append("admissions were not simultaneous")
    if any(not call["observations"] for call in burst):
        problems.append("an admission executed no observed statement")
    observations = [item for call in burst for item in call["observations"]]
    dialects = {item["dialect"] for item in observations}
    if dialects != {dialect}:
        problems.append(f"dialects {dialects} != {{{dialect!r}}}")
    if dialect != "postgresql":
        return problems
    roles = {item.get("role") for item in observations}
    if roles != {TEST_APP_ROLE}:
        problems.append(f"roles {roles} != {{{TEST_APP_ROLE!r}}}")
    unbound = [
        call["workspace_id"]
        for call in burst
        for item in call["observations"]
        if item.get("workspace") != call["workspace_id"] or item.get("scope")
    ]
    if unbound:
        problems.append(f"{len(unbound)} statement(s) outside the admission's own tenant context")
    if not all(item.get("migrated") for item in observations):
        problems.append("schema has no alembic_version: not the migrated schema")
    if max((item["contenders"] for item in observations), default=0) < 2:
        problems.append("the database admission lock was never contended across planes")
    return problems


def workspace_limit_for(calls: list[dict[str, Any]], burst: list[dict[str, Any]]) -> int | None:
    workspace = burst[0]["workspace_id"]
    begin = min(call["start"] for call in burst)
    configured = [
        call
        for call in calls
        if call["method"] == "set_workspace_limit"
        and call["workspace_id"] == workspace
        and call["error"] is None
        and call["end"] is not None
        and call["end"] <= begin
    ]
    if configured:
        return int(max(configured, key=lambda call: call["end"])["limit"])
    default = burst[0]["default_workspace_limit"]
    if default is not None and default < len(burst):
        return int(default)
    return None


def unmarked(report: dict[str, Any], nodeid: str) -> list[str]:
    if report["items"].get(nodeid, {}).get("postgres"):
        return []
    return [f"{nodeid} is not postgres-marked"]


def test_delivered_proof_module_is_postgres_marked_and_green(maker_run):
    report = delivered(maker_run)
    items = report["items"]
    assert [nodeid for nodeid, entry in items.items() if entry["postgres"]]
    assert {nodeid: entry["phases"] for nodeid, entry in items.items() if not passed(entry)} == {}
    assert report["exit_code"] == 0, maker_run.output


def test_delivered_proof_drives_250_admissions_across_four_planes_to_exactly_200(maker_run):
    report = delivered(maker_run)
    candidates = [(nodeid, burst) for nodeid, burst in all_bursts(report) if len(burst) == 250]
    assert candidates, "no burst of 250 simultaneous admissions reached VoiceControlPlane.admit"
    verdicts = {
        f"{nodeid}#{index}": burst_problems(
            burst,
            expected=Counter({"admitted": 200, REJECT_CAPACITY_GLOBAL: 50}),
            min_planes=4,
        )
        + unmarked(report, nodeid)
        + ([] if {call["global_limit"] for call in burst} == {200} else ["global limit is not 200"])
        for index, (nodeid, burst) in enumerate(candidates)
    }
    assert any(not problems for problems in verdicts.values()), verdicts


def test_delivered_proof_fills_two_nodes_to_exactly_20_each_across_planes(maker_run):
    report = delivered(maker_run)
    candidates = [(nodeid, burst) for nodeid, burst in all_bursts(report) if len(burst) == 60]
    assert candidates, "no burst of 60 simultaneous admissions reached VoiceControlPlane.admit"
    verdicts = {
        f"{nodeid}#{index}": burst_problems(
            burst,
            expected=Counter({"admitted": 40, REJECT_CAPACITY_NODE: 20}),
            min_planes=2,
        )
        + unmarked(report, nodeid)
        + (
            []
            if min(call["global_limit"] or 0 for call in burst) >= 60
            else ["global limit is not high"]
        )
        for index, (nodeid, burst) in enumerate(candidates)
    }
    assert any(not problems for problems in verdicts.values()), verdicts


def test_delivered_proof_caps_one_workspace_at_its_limit_across_planes(maker_run):
    report = delivered(maker_run)
    verdicts: dict[str, list[str]] = {}
    for index, (nodeid, burst) in enumerate(all_bursts(report)):
        outcomes = Counter(outcome_of(call) for call in burst)
        if len({call["workspace_id"] for call in burst}) != 1:
            continue
        if not outcomes[REJECT_CAPACITY_WORKSPACE] or not outcomes["admitted"]:
            continue
        limit = workspace_limit_for(report["tests"][nodeid]["calls"], burst)
        admitted = outcomes["admitted"] if limit is None else limit
        verdicts[f"{nodeid}#{index}"] = burst_problems(
            burst,
            expected=Counter(
                {"admitted": admitted, REJECT_CAPACITY_WORKSPACE: len(burst) - admitted}
            ),
            min_planes=2,
        ) + unmarked(report, nodeid)
    assert verdicts, "no simultaneous single-workspace burst reached the workspace-capacity cap"
    assert any(not problems for problems in verdicts.values()), verdicts


def test_delivered_proof_fences_node_loss_under_recovery_scope_as_app_role(maker_run):
    report = delivered(maker_run)
    verdicts: dict[str, list[str]] = {}
    for nodeid, bucket in report["tests"].items():
        calls = sorted(bucket["calls"], key=lambda call: call["start"])
        lossy = [
            call
            for call in calls
            if call["method"] == "recover"
            and call["error"] is None
            and call["result"]["lost_nodes"]
            and call["result"]["failed"].get("node_lost", 0) >= 1
        ]
        if not lossy:
            continue
        recovery = lossy[0]
        after = [call for call in calls if call["start"] >= recovery["end"]]
        seen = [item for item in recovery["observations"] if item["dialect"] == "postgresql"]
        problems = unmarked(report, nodeid)
        if not seen:
            problems.append("recovery ran no observed statement on PostgreSQL")
        if {item.get("role") for item in seen} != {TEST_APP_ROLE}:
            problems.append("recovery did not run as the app role")
        if {item.get("scope") for item in seen} != {RECOVERY_SCOPE}:
            problems.append("recovery did not run under the voice_recovery service scope")
        expectations = {
            "stale transition rejected": lambda call: call["method"] == "transition"
            and call["error"] == "StaleGeneration",
            "stale heartbeat rejected": lambda call: call["method"] == "heartbeat"
            and call["error"] == "StaleGeneration",
            "re-registration bumps the generation": lambda call: call["method"] == "register_node"
            and call["error"] is None
            and (call["result"]["value"] or 0) >= 2,
            "recovery is idempotent": lambda call: call["method"] == "recover"
            and call["error"] is None
            and call["result"]["lost_nodes"] == [],
            "survivor transition succeeds": lambda call: call["method"] == "transition"
            and call["error"] is None,
        }
        for label, matches in expectations.items():
            if not any(matches(call) for call in after):
                problems.append(f"missing after recovery: {label}")
        verdicts[nodeid] = problems
    assert verdicts, "no recovery declared a node lost and failed its calls"
    assert any(not problems for problems in verdicts.values()), verdicts


def test_delivered_proof_bursts_surface_no_contention_errors(maker_run):
    report = delivered(maker_run)
    loaded = [(nodeid, burst) for nodeid, burst in all_bursts(report) if len(burst) >= 20]
    assert loaded, "no simultaneous admission burst ran"
    raised = {
        nodeid: sorted({call["error"] for call in burst if call["error"]})
        for nodeid, burst in loaded
        if any(call["error"] for call in burst)
    }
    assert raised == {}


def test_delivered_proof_module_is_bounded_in_time_and_connections(maker_run):
    report = delivered(maker_run)
    assert report["session_seconds"] < MODULE_SECONDS_CEILING
    assert report["max_live_connections"] < CONNECTION_CEILING
    assert maker_run.max_backends < CONNECTION_CEILING


def test_sqlite_proofs_are_all_kept_and_pass(sqlite_proofs_run):
    report = sqlite_proofs_run.report
    assert report.get("items"), sqlite_proofs_run.output
    missing: list[str] = []
    failing: list[str] = []
    for module, names in BASE_PROOF_NAMES.items():
        for name in names:
            entries = [
                entry
                for nodeid, entry in report["items"].items()
                if nodeid.startswith(f"{module}::") and entry["name"] == name
            ]
            if not entries:
                missing.append(f"{module}::{name}")
            elif not all(passed(entry) for entry in entries):
                failing.append(f"{module}::{name}")
    assert missing == []
    assert failing == [], sqlite_proofs_run.output


def test_sqlite_load_proofs_still_hold_exactly_on_sqlite(sqlite_proofs_run):
    report = sqlite_proofs_run.report
    assert report.get("items"), sqlite_proofs_run.output
    module = SQLITE_PROOF_MODULES[0]
    for name, size, expected in (
        (
            "test_250_concurrent_admissions_never_over_admit",
            250,
            Counter({"admitted": 200, REJECT_CAPACITY_GLOBAL: 50}),
        ),
        (
            "test_concurrent_per_node_limit_is_exact",
            60,
            Counter({"admitted": 40, REJECT_CAPACITY_NODE: 20}),
        ),
    ):
        bursts = [
            burst
            for nodeid, bucket in report["tests"].items()
            if nodeid.startswith(f"{module}::")
            and report["items"].get(nodeid, {}).get("name") == name
            for burst in admission_bursts(bucket["calls"])
            if len(burst) == size
        ]
        verdicts = [
            burst_problems(burst, expected=expected, min_planes=1, dialect="sqlite")
            for burst in bursts
        ]
        assert any(not problems for problems in verdicts), (name, verdicts)
