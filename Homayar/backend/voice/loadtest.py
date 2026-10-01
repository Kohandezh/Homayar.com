"""Deterministic voice admission load harness.

Drives N concurrent simulated calls through admit -> answer -> end, one thread
per call, all released from a barrier at once so admission contention peaks.
Every call is held until every admission has been decided, so the number of
simultaneously held slots equals the number admitted and any over-admission
would be visible.

By default every call goes through one ``VoiceControlPlane``, whose in-process
lock serializes admissions before the database is touched. Passing several
``plane_factories`` gives each one its own plane (and so its own in-process
lock) and its own session factory: a stand-in for separate worker processes,
between which only the database admission lock serializes.

    python -m backend.voice.loadtest --calls 250 --global-limit 200
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.database.saas_models import Base, Organization, Workspace
from backend.voice.admission import AdmissionPolicy, VoiceControlPlane
from backend.voice.metrics import VoiceMetrics, percentile
from backend.voice.models import VoiceCapacitySlot


def sqlite_session_factory(path: Path, *, pool_size: int) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 60},
        pool_size=pool_size,
        max_overflow=0,
        pool_timeout=120,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def seed_workspaces(factory: sessionmaker[Session], count: int) -> list[str]:
    with factory() as db:
        org = Organization(name="Load test", slug=f"load-{time.time_ns()}")
        db.add(org)
        db.flush()
        workspaces = [
            Workspace(organization_id=org.id, name=f"ws{i}", slug=f"ws{i}") for i in range(count)
        ]
        db.add_all(workspaces)
        db.commit()
        return [w.id for w in workspaces]


def _held_by(db: Session, column: Any, run_id: str) -> dict[str, int]:
    """Held slots of this run's nodes, grouped by ``column``."""
    rows = db.execute(
        select(column, func.count(VoiceCapacitySlot.id))
        .where(
            VoiceCapacitySlot.released_at.is_(None),
            VoiceCapacitySlot.node_id.like(f"{run_id}-node-%"),
        )
        .group_by(column)
    ).all()
    return {str(key): int(count) for key, count in rows}


def run_load(
    factory: sessionmaker[Session],
    *,
    workspace_ids: list[str],
    calls: int = 250,
    nodes: int = 4,
    node_capacity: int = 64,
    policy: AdmissionPolicy | None = None,
    run_id: str = "load",
    plane_factories: Sequence[sessionmaker[Session]] | None = None,
    bind_tenant: Callable[[Session, str], None] | None = None,
) -> dict[str, Any]:
    """Run one barrier-released admission burst and report what was held.

    ``factory`` registers the nodes and counts held slots. ``plane_factories``
    (default: ``[factory]``) creates one independent control plane per factory;
    call ``i`` goes to plane ``i % planes`` and to workspace
    ``(i // planes) % len(workspace_ids)``, so every plane serves every
    workspace. ``bind_tenant(db, workspace_id)``, when given, binds each call's
    sessions to the call's workspace before the admission and before the
    lifecycle transitions, as an authenticated request does.
    """
    metrics = VoiceMetrics()
    policy = policy or AdmissionPolicy()
    factories = list(plane_factories) if plane_factories else [factory]
    planes = [VoiceControlPlane(policy, metrics=metrics) for _ in factories]
    with factory() as db:
        for index in range(nodes):
            planes[0].register_node(
                db, node_id=f"{run_id}-node-{index}", max_concurrent=node_capacity
            )

    start_barrier = threading.Barrier(calls)
    admitted_barrier = threading.Barrier(calls + 1)
    counted_barrier = threading.Barrier(calls + 1)
    latencies: list[float] = []
    outcomes: Counter[str] = Counter()
    outcomes_by_workspace: defaultdict[str, Counter[str]] = defaultdict(Counter)
    outcomes_by_plane: list[Counter[str]] = [Counter() for _ in planes]
    errors: list[str] = []
    lock = threading.Lock()
    peak_held = {"value": 0}
    peak_by_node: dict[str, int] = {}
    peak_by_workspace: dict[str, int] = {}

    def one_call(index: int) -> None:
        plane_index = index % len(planes)
        plane, call_factory = planes[plane_index], factories[plane_index]
        workspace_id = workspace_ids[(index // len(planes)) % len(workspace_ids)]
        result = None
        try:
            with call_factory() as db:
                start_barrier.wait()
                if bind_tenant is not None:
                    bind_tenant(db, workspace_id)
                result = plane.admit(
                    db,
                    workspace_id=workspace_id,
                    external_call_id=f"{run_id}-call-{index}",
                    direction="inbound",
                    channel="sip",
                )
                outcome = "admitted" if result.admitted else result.reason or "?"
                with lock:
                    latencies.append(result.latency_ms)
                    outcomes[outcome] += 1
                    outcomes_by_workspace[workspace_id][outcome] += 1
                    outcomes_by_plane[plane_index][outcome] += 1
        except Exception as exc:  # pragma: no cover - reported, not raised
            with lock:
                errors.append(f"admit {index}: {exc!r}")
        finally:
            admitted_barrier.wait()
            counted_barrier.wait()  # hold every slot until the peak was measured
        if result is None or not result.admitted:
            return
        try:
            with call_factory() as db:
                call = result.session
                if bind_tenant is not None:
                    bind_tenant(db, call.workspace_id)
                plane.transition(
                    db,
                    workspace_id=call.workspace_id,
                    session_id=call.id,
                    action="answer",
                    node_generation=call.node_generation or 0,
                )
                plane.transition(
                    db,
                    workspace_id=call.workspace_id,
                    session_id=call.id,
                    action="end",
                    node_generation=call.node_generation or 0,
                )
        except Exception as exc:  # pragma: no cover
            with lock:
                errors.append(f"lifecycle {index}: {exc!r}")

    threads = [threading.Thread(target=one_call, args=(i,)) for i in range(calls)]
    wall_start = time.perf_counter()
    for thread in threads:
        thread.start()
    admitted_barrier.wait()
    admit_wall = time.perf_counter() - wall_start
    with factory() as db:
        peak_held["value"] = int(
            db.execute(
                select(func.count(VoiceCapacitySlot.id)).where(
                    VoiceCapacitySlot.released_at.is_(None),
                    VoiceCapacitySlot.node_id.like(f"{run_id}-node-%"),
                )
            ).scalar_one()
        )
        peak_by_node.update(_held_by(db, VoiceCapacitySlot.node_id, run_id))
        peak_by_workspace.update(_held_by(db, VoiceCapacitySlot.workspace_id, run_id))
    counted_barrier.wait()
    for thread in threads:
        thread.join()
    total_wall = time.perf_counter() - wall_start
    with factory() as db:
        still_held = int(
            db.execute(
                select(func.count(VoiceCapacitySlot.id)).where(
                    VoiceCapacitySlot.released_at.is_(None),
                    VoiceCapacitySlot.node_id.like(f"{run_id}-node-%"),
                )
            ).scalar_one()
        )
    return {
        "calls": calls,
        "global_limit": policy.global_limit,
        "nodes": nodes,
        "node_capacity": node_capacity,
        "workspaces": len(workspace_ids),
        "planes": len(planes),
        "outcomes": dict(outcomes),
        "outcomes_by_workspace": {ws: dict(c) for ws, c in outcomes_by_workspace.items()},
        "outcomes_by_plane": [dict(c) for c in outcomes_by_plane],
        "held_after_admission": peak_held["value"],
        "held_after_admission_by_node": peak_by_node,
        "held_after_admission_by_workspace": peak_by_workspace,
        "held_after_end": still_held,
        "errors": errors,
        "admission_latency_ms": {
            "p50": round(percentile(latencies, 50), 3),
            "p95": round(percentile(latencies, 95), 3),
            "p99": round(percentile(latencies, 99), 3),
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
        "admission_wall_seconds": round(admit_wall, 4),
        "admissions_per_second": round(calls / admit_wall, 1) if admit_wall else None,
        "total_wall_seconds": round(total_wall, 4),
        "metrics": metrics.snapshot(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls", type=int, default=250)
    parser.add_argument("--global-limit", type=int, default=200)
    parser.add_argument("--workspace-limit", type=int, default=200)
    parser.add_argument("--nodes", type=int, default=4)
    parser.add_argument("--node-capacity", type=int, default=64)
    parser.add_argument("--workspaces", type=int, default=1)
    parser.add_argument("--db", type=Path, default=None, help="SQLite file (default: temp)")
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory() as tmp:
        path = args.db or Path(tmp) / "voice-load.db"
        factory = sqlite_session_factory(path, pool_size=args.calls + 5)
        workspace_ids = seed_workspaces(factory, args.workspaces)
        report = run_load(
            factory,
            workspace_ids=workspace_ids,
            calls=args.calls,
            nodes=args.nodes,
            node_capacity=args.node_capacity,
            policy=AdmissionPolicy(
                global_limit=args.global_limit, default_workspace_limit=args.workspace_limit
            ),
        )
        factory.kw["bind"].dispose()
    print(json.dumps(report, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
