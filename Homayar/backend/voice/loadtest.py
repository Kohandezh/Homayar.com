"""Deterministic voice admission load harness.

Drives N concurrent simulated calls through admit -> answer -> end, one thread
per call, all released from a barrier at once so admission contention peaks.
Every call is held until every admission has been decided, so the number of
simultaneously held slots equals the number admitted and any over-admission
would be visible.

    python -m backend.voice.loadtest --calls 250 --global-limit 200
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
import time
from collections import Counter
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


def run_load(
    factory: sessionmaker[Session],
    *,
    workspace_ids: list[str],
    calls: int = 250,
    nodes: int = 4,
    node_capacity: int = 64,
    policy: AdmissionPolicy | None = None,
    run_id: str = "load",
) -> dict[str, Any]:
    metrics = VoiceMetrics()
    plane = VoiceControlPlane(policy or AdmissionPolicy(), metrics=metrics)
    with factory() as db:
        for index in range(nodes):
            plane.register_node(db, node_id=f"{run_id}-node-{index}", max_concurrent=node_capacity)

    start_barrier = threading.Barrier(calls)
    admitted_barrier = threading.Barrier(calls + 1)
    counted_barrier = threading.Barrier(calls + 1)
    latencies: list[float] = []
    outcomes: Counter[str] = Counter()
    errors: list[str] = []
    lock = threading.Lock()
    peak_held = {"value": 0}

    def one_call(index: int) -> None:
        result = None
        try:
            with factory() as db:
                start_barrier.wait()
                result = plane.admit(
                    db,
                    workspace_id=workspace_ids[index % len(workspace_ids)],
                    external_call_id=f"{run_id}-call-{index}",
                    direction="inbound",
                    channel="sip",
                )
                with lock:
                    latencies.append(result.latency_ms)
                    outcomes["admitted" if result.admitted else result.reason or "?"] += 1
        except Exception as exc:  # pragma: no cover - reported, not raised
            with lock:
                errors.append(f"admit {index}: {exc!r}")
        finally:
            admitted_barrier.wait()
            counted_barrier.wait()  # hold every slot until the peak was measured
        if result is None or not result.admitted:
            return
        try:
            with factory() as db:
                call = result.session
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
        "global_limit": plane.policy.global_limit,
        "nodes": nodes,
        "node_capacity": node_capacity,
        "workspaces": len(workspace_ids),
        "outcomes": dict(outcomes),
        "held_after_admission": peak_held["value"],
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
