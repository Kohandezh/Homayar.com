from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.voice.admission import (
    REJECT_CAPACITY_GLOBAL,
    REJECT_CAPACITY_NODE,
    REJECT_CAPACITY_WORKSPACE,
    REJECT_NO_HEALTHY_NODE,
    REJECT_NODE_DRAINING,
    AdmissionPolicy,
    VoiceControlPlane,
)
from backend.voice.loadtest import run_load, seed_workspaces, sqlite_session_factory
from backend.voice.metrics import VoiceMetrics
from backend.voice.models import CallState, VoiceCapacitySlot
from backend.voice.state import IllegalTransition, StaleGeneration, is_legal


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def factory(tmp_path: Path):
    factory = sqlite_session_factory(tmp_path / "voice.db", pool_size=260)
    yield factory
    factory.kw["bind"].dispose()


@pytest.fixture
def workspaces(factory):
    return seed_workspaces(factory, 2)


def _plane(clock=None, **policy) -> VoiceControlPlane:
    return VoiceControlPlane(AdmissionPolicy(**policy), clock=clock, metrics=VoiceMetrics())


def _admit(plane, db, ws, ext, **kw):
    return plane.admit(
        db, workspace_id=ws, external_call_id=ext, direction="inbound", channel="sip", **kw
    )


def _held(db, **filters) -> int:
    query = select(func.count(VoiceCapacitySlot.id)).where(VoiceCapacitySlot.released_at.is_(None))
    for key, value in filters.items():
        query = query.where(getattr(VoiceCapacitySlot, key) == value)
    return db.execute(query).scalar_one()


def test_state_machine_legal_transitions_only():
    assert is_legal(CallState.ADMITTED, CallState.ACTIVE)
    assert is_legal(CallState.RINGING, CallState.ACTIVE)
    assert not is_legal(CallState.ACTIVE, CallState.RINGING)
    assert not is_legal(CallState.ENDED, CallState.ACTIVE)
    assert not is_legal(CallState.REJECTED, CallState.ADMITTED)


def test_limits_and_rejection_reasons(factory, workspaces):
    clock = Clock()
    plane = _plane(clock, global_limit=3, default_workspace_limit=2)
    ws_a, ws_b = workspaces
    with factory() as db:
        assert _admit(plane, db, ws_a, "none").reason == REJECT_NO_HEALTHY_NODE
        plane.register_node(db, node_id="n1", max_concurrent=2)
        plane.set_draining(db, node_id="n1", draining=True)
        assert _admit(plane, db, ws_a, "drain").reason == REJECT_NODE_DRAINING
        plane.set_draining(db, node_id="n1", draining=False)

        assert _admit(plane, db, ws_a, "a1").admitted
        assert _admit(plane, db, ws_a, "a2").admitted
        assert _admit(plane, db, ws_a, "a3").reason == REJECT_CAPACITY_WORKSPACE
        assert _admit(plane, db, ws_b, "b1").reason == REJECT_CAPACITY_NODE
        plane.register_node(db, node_id="n2", max_concurrent=5)
        assert _admit(plane, db, ws_b, "b2").admitted
        assert _admit(plane, db, ws_b, "b3").reason == REJECT_CAPACITY_GLOBAL
        assert _held(db) == 3

        # A rejected call is re-evaluated when retried once capacity frees up.
        a1 = _admit(plane, db, ws_a, "a1").session
        plane.transition(
            db,
            workspace_id=ws_a,
            session_id=a1.id,
            action="end",
            node_generation=a1.node_generation,
        )
        retried = _admit(plane, db, ws_b, "b3")
        assert retried.admitted and not retried.idempotent
    snap = plane.metrics.snapshot()
    assert snap["rejections_by_reason"][REJECT_CAPACITY_GLOBAL] == 1
    assert snap["admitted_total"] == 4


def test_least_loaded_node_and_idempotency(factory, workspaces):
    plane = _plane(Clock())
    ws = workspaces[0]
    with factory() as db:
        plane.register_node(db, node_id="small", max_concurrent=2)
        plane.register_node(db, node_id="big", max_concurrent=10)
        first = _admit(plane, db, ws, "x1")
        assert first.session.node_id == "big"  # both empty: tie -> lower load ratio then id
        again = _admit(plane, db, ws, "x1")
        assert again.idempotent and again.session.id == first.session.id
        assert _held(db) == 1
        nodes = [_admit(plane, db, ws, f"x{i}").session.node_id for i in range(2, 7)]
        assert nodes.count("small") >= 1  # load ratio balances across nodes
        assert _held(db, node_id="small") <= 2


def test_illegal_transition_and_slot_release(factory, workspaces):
    plane = _plane(Clock())
    ws = workspaces[0]
    with factory() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
        call = _admit(plane, db, ws, "c1").session
        gen = call.node_generation
        plane.transition(
            db, workspace_id=ws, session_id=call.id, action="ring", node_generation=gen
        )
        plane.transition(
            db, workspace_id=ws, session_id=call.id, action="answer", node_generation=gen
        )
        with pytest.raises(IllegalTransition):
            plane.transition(
                db, workspace_id=ws, session_id=call.id, action="ring", node_generation=gen
            )
        ended = plane.transition(
            db, workspace_id=ws, session_id=call.id, action="end", node_generation=gen
        )
        assert ended.state == CallState.ENDED and ended.end_reason == "completed"
        assert _held(db) == 0
        with pytest.raises(IllegalTransition):
            plane.transition(
                db, workspace_id=ws, session_id=call.id, action="end", node_generation=gen
            )


def test_node_loss_recovery_and_fencing(factory, workspaces):
    clock = Clock()
    plane = _plane(clock, heartbeat_ttl_seconds=10, slot_lease_seconds=30)
    ws = workspaces[0]
    with factory() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
        plane.register_node(db, node_id="n2", max_concurrent=5)
        calls = [_admit(plane, db, ws, f"c{i}").session for i in range(4)]
        on_n1 = [c for c in calls if c.node_id == "n1"]
        assert on_n1
        clock.advance(8)
        plane.heartbeat(db, node_id="n2", generation=1)
        clock.advance(5)  # n1 lapsed (13s), n2 fresh (5s)
        report = plane.recover(db)
        assert report["lost_nodes"] == ["n1"]
        assert sorted(report["failed_calls"]["node_lost"]) == sorted(c.id for c in on_n1)
        assert _held(db, node_id="n1") == 0
        for call in on_n1:
            refreshed = plane.get_session(db, workspace_id=ws, session_id=call.id)
            db.refresh(refreshed)
            assert refreshed.state == CallState.FAILED and refreshed.end_reason == "node_lost"
            # The stale incarnation can no longer modify the call.
            with pytest.raises(StaleGeneration):
                plane.transition(
                    db, workspace_id=ws, session_id=call.id, action="end", node_generation=1
                )
        with pytest.raises(StaleGeneration):
            plane.heartbeat(db, node_id="n1", generation=1)
        node = plane.register_node(db, node_id="n1", max_concurrent=5)
        assert node.generation == 2
        assert plane.recover(db)["lost_nodes"] == []  # idempotent
        # Surviving node's calls are unaffected.
        survivor = next(c for c in calls if c.node_id == "n2")
        plane.transition(
            db, workspace_id=ws, session_id=survivor.id, action="answer", node_generation=1
        )


def test_slot_lease_expiry_reclaims(factory, workspaces):
    clock = Clock()
    plane = _plane(clock, heartbeat_ttl_seconds=1000, slot_lease_seconds=5)
    ws = workspaces[0]
    with factory() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
        call = _admit(plane, db, ws, "c1").session
        clock.advance(6)
        report = plane.recover(db)
        assert report["failed_calls"]["lease_expired"] == [call.id]
        assert _held(db) == 0


def test_cross_tenant_session_is_invisible(factory, workspaces):
    plane = _plane(Clock())
    ws_a, ws_b = workspaces
    with factory() as db:
        plane.register_node(db, node_id="n1", max_concurrent=5)
        call = _admit(plane, db, ws_a, "shared-id").session
        from backend.voice.admission import VoiceNotFound

        with pytest.raises(VoiceNotFound):
            plane.get_session(db, workspace_id=ws_b, session_id=call.id)
        with pytest.raises(VoiceNotFound):
            plane.transition(
                db, workspace_id=ws_b, session_id=call.id, action="end", node_generation=1
            )
        # Same external id in another workspace is a different call.
        other = _admit(plane, db, ws_b, "shared-id")
        assert other.admitted and other.session.id != call.id


def test_250_concurrent_admissions_never_over_admit(factory, workspaces):
    report = run_load(
        factory,
        workspace_ids=workspaces,
        calls=250,
        nodes=4,
        node_capacity=64,
        policy=AdmissionPolicy(global_limit=200, default_workspace_limit=200),
    )
    print(
        "\nvoice loadtest:",
        json.dumps(
            {
                k: report[k]
                for k in (
                    "outcomes",
                    "held_after_admission",
                    "admission_latency_ms",
                    "admissions_per_second",
                    "total_wall_seconds",
                )
            }
        ),
    )
    assert report["errors"] == []
    assert report["outcomes"] == {"admitted": 200, REJECT_CAPACITY_GLOBAL: 50}
    assert report["held_after_admission"] == 200
    assert report["held_after_end"] == 0


def test_concurrent_per_node_limit_is_exact(factory, workspaces):
    report = run_load(
        factory,
        workspace_ids=workspaces,
        calls=60,
        nodes=2,
        node_capacity=20,
        policy=AdmissionPolicy(global_limit=1000),
        run_id="nodes",
    )
    assert report["errors"] == []
    assert report["outcomes"] == {"admitted": 40, REJECT_CAPACITY_NODE: 20}
    assert report["held_after_admission"] == 40
