"""Voice admission invariants on PostgreSQL across independent control planes (HOM-157, REC-02).

``test_voice_admission.py`` proves the caps on SQLite through ONE
``VoiceControlPlane``, whose in-process lock serializes every admission before
the database is touched. Production runs one plane per uvicorn worker process
(``routers/v1/voice.py``), so between workers only the ``voice_admission_locks``
row lock serializes admission. These cases drive barrier-released bursts
through several independent planes (each with its own engine, pool and
in-process lock: stand-ins for worker processes) on the Alembic-migrated
schema, with application SQL running as the non-bypass app role (``SET ROLE``
on checkout, as ``build_engine`` does in production) and every call session
bound to its workspace's tenant context, as an authenticated request is.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session, sessionmaker

from backend.config import Settings, reset_settings_cache
from backend.database.relational import build_engine
from backend.database.saas_models import Workspace
from backend.database.tenant_context import (
    SERVICE_SCOPE_VOICE_RECOVERY,
    bind_service_scope,
    bind_tenant_context,
    install_session_listeners,
)
from backend.tests._database import (
    TEST_APP_ROLE,
    configured_database_url,
    is_postgres_url,
    reset_postgres_schema,
)
from backend.voice.admission import (
    END_NODE_LOST,
    REJECT_CAPACITY_GLOBAL,
    REJECT_CAPACITY_NODE,
    REJECT_CAPACITY_WORKSPACE,
    AdmissionPolicy,
    VoiceControlPlane,
    VoiceNotFound,
)
from backend.voice.loadtest import run_load, seed_workspaces
from backend.voice.metrics import VoiceMetrics
from backend.voice.models import (
    CallState,
    VoiceAdmissionLock,
    VoiceCallSession,
    VoiceCapacitySlot,
)
from backend.voice.state import StaleGeneration

pytestmark = pytest.mark.postgres

REPO_ROOT = Path(__file__).resolve().parents[2]

# Independent control planes per burst (AC1: at least 4) and the connection
# budget (AC7: fewer than 90 at once; PostgreSQL's default max_connections is
# 100). Every engine has max_overflow=0, so the pools bound the total.
PLANES = 6
PLANE_POOL = 8
SETUP_POOL = 2
ADMIN_POOL = 3
CONNECTION_BUDGET = 90

VOICE_TABLES = (
    "voice_capacity_slots",
    "voice_call_sessions",
    "voice_workspace_limits",
    "voice_media_nodes",
    "voice_admission_locks",
)


def test_connection_pools_fit_the_budget() -> None:
    assert PLANES >= 4
    assert PLANES * PLANE_POOL + SETUP_POOL + ADMIN_POOL < CONNECTION_BUDGET


# -- environment -------------------------------------------------------------


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


def _app_factory(url: str, pool_size: int) -> sessionmaker[Session]:
    """A session factory built like ``configure_database``: the production
    engine (``SET ROLE homayar_app`` on every checkout) and the listener that
    re-applies the bound tenant context at the start of every transaction."""
    settings = Settings(
        environment="test",
        database_url=url,
        database_app_role=TEST_APP_ROLE,
        database_pool_size=pool_size,
        database_max_overflow=0,
        _env_file=None,
    )
    factory = sessionmaker(bind=build_engine(settings), autoflush=False, expire_on_commit=False)
    install_session_listeners(factory)
    return factory


@dataclass
class VoiceDatabase:
    admin_engine: Engine
    admin: sessionmaker[Session]  # test login role (superuser/BYPASSRLS): seed and inspect only
    setup: sessionmaker[Session]  # app role: node registration, limits, recovery sweeps
    planes: list[sessionmaker[Session]]  # app role: one engine per independent plane
    workspaces: list[str]  # two organizations x two workspaces
    organization_of: dict[str, str] = field(default_factory=dict)

    def bind(self, db: Session, workspace_id: str) -> None:
        bind_tenant_context(
            db, workspace_id=workspace_id, organization_id=self.organization_of[workspace_id]
        )

    def tenant_session(self, workspace_id: str, plane: int = 0) -> Session:
        db = self.planes[plane]()
        self.bind(db, workspace_id)
        return db

    def held(self, **filters: str) -> int:
        query = select(func.count(VoiceCapacitySlot.id)).where(
            VoiceCapacitySlot.released_at.is_(None)
        )
        for key, value in filters.items():
            query = query.where(getattr(VoiceCapacitySlot, key) == value)
        with self.admin() as db:
            return int(db.execute(query).scalar_one())


@pytest.fixture
def voice_db(migrated_database_url: str) -> Iterator[VoiceDatabase]:
    url = migrated_database_url
    admin_engine = create_engine(url, pool_size=ADMIN_POOL, max_overflow=0)
    admin = sessionmaker(bind=admin_engine, autoflush=False, expire_on_commit=False)
    with admin_engine.begin() as connection:
        # Voice capacity is deployment-wide: every case starts with no nodes,
        # no held slots and no admission-lock row.
        connection.execute(text(f"TRUNCATE {', '.join(VOICE_TABLES)}"))
    workspaces = seed_workspaces(admin, 2) + seed_workspaces(admin, 2)
    with admin() as db:
        organization_of = {
            ws: org
            for ws, org in db.execute(
                select(Workspace.id, Workspace.organization_id).where(Workspace.id.in_(workspaces))
            ).all()
        }
    setup = _app_factory(url, SETUP_POOL)
    planes = [_app_factory(url, PLANE_POOL) for _ in range(PLANES)]
    try:
        yield VoiceDatabase(admin_engine, admin, setup, planes, workspaces, organization_of)
    finally:
        for factory in (setup, *planes):
            factory.kw["bind"].dispose()
        admin_engine.dispose()


class ActivitySampler:
    """Samples this database's client connections, and how many of them wait
    on a lock, from its own autocommit connection while a burst runs."""

    _QUERY = text(
        "SELECT count(*), count(*) FILTER (WHERE wait_event_type = 'Lock') "
        "FROM pg_stat_activity "
        "WHERE datname = current_database() AND backend_type = 'client backend'"
    )

    def __init__(self, engine: Engine, interval: float = 0.002) -> None:
        self._engine = engine
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self.samples = 0
        self.peak_connections = 0
        self.peak_lock_waiters = 0
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            with self._engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                while True:
                    connections, waiters = connection.execute(self._QUERY).one()
                    self.samples += 1
                    self.peak_connections = max(self.peak_connections, int(connections))
                    self.peak_lock_waiters = max(self.peak_lock_waiters, int(waiters))
                    if self._stop.wait(self._interval):
                        return
        except BaseException as exc:  # pragma: no cover - reported by the test
            self.error = exc

    def __enter__(self) -> ActivitySampler:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    def summary(self) -> dict[str, int]:
        return {
            "samples": self.samples,
            "peak_connections": self.peak_connections,
            "peak_lock_waiters": self.peak_lock_waiters,
        }


def _burst(voice_db: VoiceDatabase, **kwargs) -> tuple[dict, ActivitySampler]:
    with ActivitySampler(voice_db.admin_engine) as sampler:
        report = run_load(
            voice_db.setup,
            plane_factories=voice_db.planes,
            bind_tenant=voice_db.bind,
            **kwargs,
        )
    print(
        f"\nvoice postgres burst ({kwargs.get('run_id', 'load')}):",
        json.dumps(
            {
                "planes": report["planes"],
                "outcomes": report["outcomes"],
                "held_after_admission": report["held_after_admission"],
                "admission_latency_ms": report["admission_latency_ms"],
                "admission_wall_seconds": report["admission_wall_seconds"],
                "total_wall_seconds": report["total_wall_seconds"],
                "database": sampler.summary(),
            }
        ),
    )
    return report, sampler


def _assert_burst_hygiene(report: dict, sampler: ActivitySampler, calls: int) -> None:
    """No error surfaced (AC5), every plane decided its share, the planes
    really contended on the database lock, and the connection budget held."""
    assert report["errors"] == []
    assert report["planes"] == PLANES
    assert [sum(outcomes.values()) for outcomes in report["outcomes_by_plane"]] == [
        len(range(plane, calls, PLANES)) for plane in range(PLANES)
    ]
    assert sampler.error is None
    assert sampler.samples > 0
    # Non-vacuity: admissions of different planes waited on each other inside
    # PostgreSQL, so the exact counts below come from the database lock.
    assert sampler.peak_lock_waiters >= 1
    assert sampler.peak_connections < CONNECTION_BUDGET


def _lock_version(voice_db: VoiceDatabase) -> int:
    with voice_db.admin() as db:
        return int(
            db.execute(
                select(VoiceAdmissionLock.version).where(VoiceAdmissionLock.id == "default")
            ).scalar_one()
        )


def _call_states(voice_db: VoiceDatabase) -> dict[tuple[str, str | None], int]:
    with voice_db.admin() as db:
        rows = db.execute(
            select(
                VoiceCallSession.state,
                VoiceCallSession.rejection_reason,
                func.count(VoiceCallSession.id),
            ).group_by(VoiceCallSession.state, VoiceCallSession.rejection_reason)
        ).all()
    return {(state.value, reason): int(count) for state, reason, count in rows}


# -- application role and tenant binding --------------------------------------


def test_admission_runs_as_the_app_role_and_needs_the_tenant_binding(voice_db):
    for factory in (voice_db.setup, *voice_db.planes):
        with factory() as db:
            role = (
                db.execute(
                    text(
                        "SELECT current_user AS name, rolsuper, rolbypassrls "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                )
                .mappings()
                .one()
            )
            assert (role["name"], role["rolsuper"], role["rolbypassrls"]) == (
                TEST_APP_ROLE,
                False,
                False,
            )
    with voice_db.admin() as db:
        security = dict(
            db.execute(
                text(
                    "SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE relkind = 'r' AND relname = ANY(:names)"
                ),
                {"names": list(VOICE_TABLES)},
            ).all()
        )
    # Tenant tables are policy-protected; capacity counts are deployment-wide.
    assert security == {
        "voice_call_sessions": True,
        "voice_workspace_limits": True,
        "voice_capacity_slots": False,
        "voice_media_nodes": False,
        "voice_admission_locks": False,
    }

    plane = VoiceControlPlane(AdmissionPolicy(), metrics=VoiceMetrics())
    ws = voice_db.workspaces[0]
    with voice_db.setup() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
    # DENY: an admission without a bound tenant is refused by the policy.
    with voice_db.planes[0]() as db, pytest.raises(ProgrammingError, match="row-level security"):
        plane.admit(
            db, workspace_id=ws, external_call_id="unbound", direction="inbound", channel="sip"
        )
    assert voice_db.held() == 0
    # ALLOW-CONTROL: the same admission bound to its workspace succeeds.
    with voice_db.tenant_session(ws) as db:
        result = plane.admit(
            db, workspace_id=ws, external_call_id="bound", direction="inbound", channel="sip"
        )
    assert result.admitted and result.session.workspace_id == ws
    assert voice_db.held(workspace_id=ws) == 1


# -- capacity caps across independent planes -----------------------------------


def test_250_admissions_across_planes_hold_exactly_200(voice_db):
    report, sampler = _burst(
        voice_db,
        workspace_ids=voice_db.workspaces,
        calls=250,
        nodes=4,
        node_capacity=64,
        policy=AdmissionPolicy(global_limit=200, default_workspace_limit=200),
        run_id="global",
    )
    _assert_burst_hygiene(report, sampler, 250)
    assert report["outcomes"] == {"admitted": 200, REJECT_CAPACITY_GLOBAL: 50}
    assert report["held_after_admission"] == 200
    assert sum(report["held_after_admission_by_workspace"].values()) == 200
    assert set(report["held_after_admission_by_workspace"]) <= set(voice_db.workspaces)
    assert report["held_after_end"] == 0
    metrics = report["metrics"]
    assert metrics["admitted_total"] == 200
    assert metrics["rejections_by_reason"] == {REJECT_CAPACITY_GLOBAL: 50}
    assert metrics["releases_by_reason"] == {"completed": 200}
    # The database agrees: 200 calls completed, 50 rejected, no slot left held,
    # and every committed admission bumped the admission lock exactly once
    # (4 node registrations + 250 admissions).
    assert _call_states(voice_db) == {
        (CallState.ENDED.value, None): 200,
        (CallState.REJECTED.value, REJECT_CAPACITY_GLOBAL): 50,
    }
    assert voice_db.held() == 0
    assert _lock_version(voice_db) == 4 + 250


def test_per_node_limit_is_exact_across_planes(voice_db):
    report, sampler = _burst(
        voice_db,
        workspace_ids=voice_db.workspaces,
        calls=60,
        nodes=2,
        node_capacity=20,
        policy=AdmissionPolicy(global_limit=1000),
        run_id="nodes",
    )
    _assert_burst_hygiene(report, sampler, 60)
    assert report["outcomes"] == {"admitted": 40, REJECT_CAPACITY_NODE: 20}
    assert report["held_after_admission"] == 40
    # DENY overflow and ALLOW-CONTROL: both nodes fill to exactly their capacity.
    assert report["held_after_admission_by_node"] == {"nodes-node-0": 20, "nodes-node-1": 20}
    assert report["held_after_end"] == 0
    assert voice_db.held() == 0


def test_per_workspace_limit_is_exact_across_planes(voice_db):
    capped, bystander = voice_db.workspaces[0], voice_db.workspaces[2]
    limit = 15
    # run_load hands each workspace in turn a block of PLANES consecutive
    # calls, so an even number of blocks splits the burst evenly.
    per_workspace = 7 * PLANES
    calls = 2 * per_workspace
    # The limit row is a tenant table: it is written, and later read by every
    # admission, only through a session bound to that workspace.
    with voice_db.setup() as db:
        voice_db.bind(db, capped)
        VoiceControlPlane(AdmissionPolicy(), metrics=VoiceMetrics()).set_workspace_limit(
            db, workspace_id=capped, limit=limit
        )
    report, sampler = _burst(
        voice_db,
        workspace_ids=[capped, bystander],
        calls=calls,
        nodes=4,
        node_capacity=64,
        policy=AdmissionPolicy(global_limit=1000, default_workspace_limit=1000),
        run_id="workspace",
    )
    _assert_burst_hygiene(report, sampler, calls)
    # Simultaneous admissions into the capped workspace admit exactly the
    # limit; the bystander workspace (another organization) is unaffected.
    assert report["outcomes_by_workspace"] == {
        capped: {"admitted": limit, REJECT_CAPACITY_WORKSPACE: per_workspace - limit},
        bystander: {"admitted": per_workspace},
    }
    assert report["outcomes"] == {
        "admitted": limit + per_workspace,
        REJECT_CAPACITY_WORKSPACE: per_workspace - limit,
    }
    assert report["held_after_admission_by_workspace"] == {capped: limit, bystander: per_workspace}
    assert report["held_after_end"] == 0


# -- fencing, recovery and tenancy --------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_node_loss_recovery_and_fencing_across_planes(voice_db):
    clock = Clock()
    policy = AdmissionPolicy(heartbeat_ttl_seconds=10, slot_lease_seconds=30)
    # Two workers: A carries the media side, B runs the recovery sweep.
    worker_a = VoiceControlPlane(policy, clock=clock, metrics=VoiceMetrics())
    worker_b = VoiceControlPlane(policy, clock=clock, metrics=VoiceMetrics())
    ws_a, ws_b = voice_db.workspaces[0], voice_db.workspaces[2]  # different organizations
    with voice_db.setup() as db:
        worker_a.register_node(db, node_id="n1", max_concurrent=5)
        worker_a.register_node(db, node_id="n2", max_concurrent=5)
    calls: list[VoiceCallSession] = []
    for index, ws in enumerate([ws_a, ws_a, ws_b, ws_b]):
        with voice_db.tenant_session(ws, plane=index % 2) as db:
            result = worker_a.admit(
                db,
                workspace_id=ws,
                external_call_id=f"c{index}",
                direction="inbound",
                channel="sip",
            )
        assert result.admitted
        calls.append(result.session)
    on_n1 = [c for c in calls if c.node_id == "n1"]
    on_n2 = [c for c in calls if c.node_id == "n2"]
    assert {c.workspace_id for c in on_n1} == {ws_a, ws_b}  # the lost node spans tenants
    assert on_n2

    # Recovery sees every tenant only under its service scope.
    with voice_db.setup() as db:
        assert db.execute(select(func.count(VoiceCallSession.id))).scalar_one() == 0
        bind_service_scope(db, SERVICE_SCOPE_VOICE_RECOVERY)
        assert db.execute(select(func.count(VoiceCallSession.id))).scalar_one() == 4

    clock.advance(8)
    with voice_db.setup() as db:
        worker_a.heartbeat(db, node_id="n2", generation=1)
    clock.advance(5)  # n1 lapsed (13 s), n2 fresh (5 s)
    with voice_db.planes[1]() as db:  # worker B's sweeper: no tenant bound
        report = worker_b.recover(db)
        db.commit()
    assert report["lost_nodes"] == ["n1"]
    assert sorted(report["failed_calls"][END_NODE_LOST]) == sorted(c.id for c in on_n1)
    assert voice_db.held(node_id="n1") == 0
    assert voice_db.held(node_id="n2") == len(on_n2)

    for call in on_n1:
        with voice_db.tenant_session(call.workspace_id) as db:
            refreshed = worker_a.get_session(db, workspace_id=call.workspace_id, session_id=call.id)
            assert refreshed.state == CallState.FAILED and refreshed.end_reason == END_NODE_LOST
            # DENY: the stale incarnation can no longer modify the call.
            with pytest.raises(StaleGeneration):
                worker_a.transition(
                    db,
                    workspace_id=call.workspace_id,
                    session_id=call.id,
                    action="end",
                    node_generation=1,
                )
    with voice_db.setup() as db, pytest.raises(StaleGeneration):
        worker_a.heartbeat(db, node_id="n1", generation=1)
    with voice_db.setup() as db:
        node = worker_b.register_node(db, node_id="n1", max_concurrent=5)
        assert node.generation == 2
        assert worker_a.heartbeat(db, node_id="n1", generation=2).generation == 2
    with voice_db.planes[0]() as db:  # idempotent, from the other worker
        assert worker_a.recover(db) == {
            "lost_nodes": [],
            "failed_calls": {END_NODE_LOST: [], "lease_expired": []},
        }
        db.commit()

    # ALLOW-CONTROL: the survivor's transition with its current generation succeeds.
    survivor = on_n2[0]
    with voice_db.tenant_session(survivor.workspace_id, plane=1) as db:
        answered = worker_b.transition(
            db,
            workspace_id=survivor.workspace_id,
            session_id=survivor.id,
            action="answer",
            node_generation=1,
        )
        assert answered.state == CallState.ACTIVE

    # DENY: a session bound to one tenant cannot read another tenant's call,
    # even when the query names that tenant. ALLOW-CONTROL: its own tenant can.
    foreign = next(c for c in calls if c.workspace_id == ws_a)
    with voice_db.tenant_session(ws_b) as db:
        assert db.get(VoiceCallSession, foreign.id) is None
        with pytest.raises(VoiceNotFound):
            worker_a.get_session(db, workspace_id=ws_a, session_id=foreign.id)
    with voice_db.tenant_session(ws_a) as db:
        own = worker_a.get_session(db, workspace_id=ws_a, session_id=foreign.id)
        assert own.id == foreign.id
