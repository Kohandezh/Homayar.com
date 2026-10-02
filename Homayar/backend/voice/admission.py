"""Voice call capacity admission and runtime control plane.

Concurrency model
-----------------
Every admission (and node-loss recovery) first performs a conditional
``UPDATE voice_admission_locks SET version = version + 1 WHERE id = :profile``.
That statement takes a row lock on PostgreSQL and the database write lock on
SQLite, so admissions for one deployment profile are serialized. Under that
lock the held-slot counts (global, per workspace, per node) are exact, which
makes "check then insert" race free without counters that could drift. The
lock is held only for a handful of indexed statements; call media never waits
on it.

Lock order is always: admission lock -> call session -> node -> slots.

Fencing
-------
A media node holds a ``generation``. Admission stamps it on the call and on
the slot. When the node's heartbeat lapses, recovery increments the node
generation, fails its live calls (``node_lost``) and frees their slots, so any
later write from the old incarnation presents a stale generation and is
rejected.
"""

from __future__ import annotations

import hashlib
import os
import platform
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from backend.database.tenant_context import SERVICE_SCOPE_VOICE_RECOVERY, bind_service_scope
from backend.voice.metrics import VoiceMetrics
from backend.voice.metrics import metrics as default_metrics
from backend.voice.models import (
    LIVE_STATES,
    TERMINAL_STATES,
    CallChannel,
    CallDirection,
    CallState,
    VoiceAdmissionLock,
    VoiceCallSession,
    VoiceCapacitySlot,
    VoiceMediaNode,
    VoiceWorkspaceLimit,
)
from backend.voice.state import (
    ACTION_TARGETS,
    StaleGeneration,
    VoiceStateError,
    assert_generation,
    assert_transition,
)

T = TypeVar("T")

REJECT_CAPACITY_GLOBAL = "capacity_global"
REJECT_CAPACITY_NODE = "capacity_node"
REJECT_CAPACITY_WORKSPACE = "capacity_workspace"
REJECT_NODE_DRAINING = "node_draining"
REJECT_NO_HEALTHY_NODE = "no_healthy_node"
REJECTION_REASONS = (
    REJECT_CAPACITY_GLOBAL,
    REJECT_CAPACITY_NODE,
    REJECT_CAPACITY_WORKSPACE,
    REJECT_NODE_DRAINING,
    REJECT_NO_HEALTHY_NODE,
)

END_NODE_LOST = "node_lost"
END_LEASE_EXPIRED = "lease_expired"


class VoiceNotFound(VoiceStateError):
    code = "not_found"
    status_code = 404


class VoiceInvalidRequest(VoiceStateError):
    code = "invalid_request"
    status_code = 422


@dataclass(frozen=True)
class AdmissionPolicy:
    """Deployment profile limits. Defaults target the Priority-0 >=200 calls."""

    global_limit: int = 200
    default_workspace_limit: int = 200
    heartbeat_ttl_seconds: float = 15.0
    slot_lease_seconds: float = 30.0
    profile: str = "default"
    max_retries: int = 200


@dataclass
class AdmissionResult:
    admitted: bool
    session: VoiceCallSession
    reason: str | None
    idempotent: bool
    latency_ms: float


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_lock_contention(exc: OperationalError) -> bool:
    text = str(exc.orig).lower() if exc.orig is not None else str(exc).lower()
    return (
        "database is locked" in text
        or "database table is locked" in text
        or "deadlock detected" in text
        or "could not serialize" in text
    )


def _contention_backoff(attempt: int, retrier: str) -> float:
    """Seconds to sleep before retrying after lock contention on ``attempt`` (1-based).

    The base delay ``min(0.05, 0.001 * attempt)`` is scaled by a factor in
    [0.5, 1.5) so retriers that collided do not wake together. The factor is a
    hash of the retrier's identity and the attempt, so the same inputs always
    give the same delay: production code fabricates no numbers (WP04/F23).
    """
    digest = hashlib.blake2b(f"{retrier}:{attempt}".encode(), digest_size=8).digest()
    spread = (int.from_bytes(digest, "big") >> 12) / 2**52  # 52 bits: [0, 1), never 1.0
    return min(0.05, 0.001 * attempt) * (0.5 + spread)


def _retrier_identity() -> str:
    """Host, process and thread of the caller, which tell concurrent retriers apart."""
    return f"{platform.node()}:{os.getpid()}:{threading.get_ident()}"


class VoiceControlPlane:
    def __init__(
        self,
        policy: AdmissionPolicy | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        metrics: VoiceMetrics | None = None,
    ) -> None:
        self.policy = policy or AdmissionPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.metrics = metrics or default_metrics
        # In-process pre-serialization of admission-lock holders: the database
        # lock stays authoritative across processes; this only stops threads
        # of one process from thrashing on it (SQLite busy-retries).
        self._local_lock = threading.Lock()

    # -- infrastructure -------------------------------------------------------

    def now(self) -> datetime:
        return _utc(self._clock())  # type: ignore[return-value]

    def _serialized(self, db: Session, work: Callable[[], T]) -> T:
        with self._local_lock:
            return self._retrying(db, work)

    def _retrying(self, db: Session, work: Callable[[], T]) -> T:
        attempt = 0
        while True:
            try:
                result = work()
                db.commit()
                return result
            except OperationalError as exc:
                db.rollback()
                attempt += 1
                if not _is_lock_contention(exc) or attempt > self.policy.max_retries:
                    raise
                time.sleep(_contention_backoff(attempt, _retrier_identity()))
            except IntegrityError:
                # A concurrent insert won (e.g. first creation of the lock row);
                # the retry observes the committed row.
                db.rollback()
                attempt += 1
                if attempt > self.policy.max_retries:
                    raise
            except BaseException:
                db.rollback()
                raise

    def _take_admission_lock(self, db: Session) -> None:
        result = db.execute(
            update(VoiceAdmissionLock)
            .where(VoiceAdmissionLock.id == self.policy.profile)
            .values(version=VoiceAdmissionLock.version + 1)
        )
        if result.rowcount == 0:
            db.add(VoiceAdmissionLock(id=self.policy.profile, version=1))
            db.flush()

    def _healthy_cutoff(self, now: datetime) -> datetime:
        return now - timedelta(seconds=self.policy.heartbeat_ttl_seconds)

    def _is_healthy(self, node: VoiceMediaNode, now: datetime) -> bool:
        return node.lost_at is None and _utc(node.heartbeat_at) >= self._healthy_cutoff(now)  # type: ignore[operator]

    @staticmethod
    def _held_count(db: Session, *criteria: Any) -> int:
        return int(
            db.execute(
                select(func.count(VoiceCapacitySlot.id)).where(
                    VoiceCapacitySlot.released_at.is_(None), *criteria
                )
            ).scalar_one()
        )

    def _workspace_limit(self, db: Session, workspace_id: str) -> int:
        row = db.get(VoiceWorkspaceLimit, workspace_id)
        return row.max_concurrent_calls if row is not None else self.policy.default_workspace_limit

    # -- admission ------------------------------------------------------------

    def admit(
        self,
        db: Session,
        *,
        workspace_id: str,
        external_call_id: str,
        direction: CallDirection | str,
        channel: CallChannel | str,
        language: str | None = None,
    ) -> AdmissionResult:
        if not external_call_id or len(external_call_id) > 255:
            raise VoiceInvalidRequest("external_call_id must be 1..255 characters")
        try:
            direction = CallDirection(direction)
            channel = CallChannel(channel)
        except ValueError as exc:
            raise VoiceInvalidRequest(str(exc)) from exc
        started = time.perf_counter()

        def work() -> tuple[VoiceCallSession, str | None, bool]:
            self._take_admission_lock(db)
            existing = db.execute(
                select(VoiceCallSession).where(
                    VoiceCallSession.workspace_id == workspace_id,
                    VoiceCallSession.external_call_id == external_call_id,
                )
            ).scalar_one_or_none()
            if existing is not None and existing.state != CallState.REJECTED:
                return existing, None, True
            now = self.now()
            reason, node = self._decide(db, workspace_id=workspace_id, now=now)
            call = existing or VoiceCallSession(
                workspace_id=workspace_id,
                external_call_id=external_call_id,
                direction=direction,
                channel=channel,
            )
            call.language = language
            if reason is not None:
                call.state = CallState.REJECTED
                call.rejection_reason = reason
                call.node_id = None
                call.node_generation = None
                call.ended_at = now
                call.end_reason = "rejected"
                db.add(call)
                db.flush()
                return call, reason, False
            assert node is not None
            call.state = CallState.ADMITTED
            call.rejection_reason = None
            call.end_reason = None
            call.ended_at = None
            call.node_id = node.id
            call.node_generation = node.generation
            call.admitted_at = now
            db.add(call)
            db.flush()
            db.add(
                VoiceCapacitySlot(
                    session_id=call.id,
                    workspace_id=workspace_id,
                    node_id=node.id,
                    generation=node.generation,
                    acquired_at=now,
                    lease_expires_at=now + timedelta(seconds=self.policy.slot_lease_seconds),
                )
            )
            db.flush()
            return call, None, False

        call, reason, idempotent = self._serialized(db, work)
        latency_ms = (time.perf_counter() - started) * 1000.0
        if not idempotent:
            self.metrics.observe_admission(latency_ms, admitted=reason is None, reason=reason)
        return AdmissionResult(
            admitted=call.state != CallState.REJECTED,
            session=call,
            reason=call.rejection_reason,
            idempotent=idempotent,
            latency_ms=latency_ms,
        )

    def _decide(
        self, db: Session, *, workspace_id: str, now: datetime
    ) -> tuple[str | None, VoiceMediaNode | None]:
        if self._held_count(db) >= self.policy.global_limit:
            return REJECT_CAPACITY_GLOBAL, None
        if self._held_count(
            db, VoiceCapacitySlot.workspace_id == workspace_id
        ) >= self._workspace_limit(db, workspace_id):
            return REJECT_CAPACITY_WORKSPACE, None
        nodes = db.execute(select(VoiceMediaNode).order_by(VoiceMediaNode.id)).scalars().all()
        healthy = [node for node in nodes if self._is_healthy(node, now)]
        if not healthy:
            return REJECT_NO_HEALTHY_NODE, None
        serving = [node for node in healthy if not node.draining]
        if not serving:
            return REJECT_NODE_DRAINING, None
        held_by_node = dict(
            db.execute(
                select(VoiceCapacitySlot.node_id, func.count(VoiceCapacitySlot.id))
                .where(VoiceCapacitySlot.released_at.is_(None))
                .group_by(VoiceCapacitySlot.node_id)
            ).all()
        )
        open_nodes = [n for n in serving if held_by_node.get(n.id, 0) < n.max_concurrent]
        if not open_nodes:
            return REJECT_CAPACITY_NODE, None
        best = min(
            open_nodes,
            key=lambda n: (
                held_by_node.get(n.id, 0) / n.max_concurrent,
                held_by_node.get(n.id, 0),
                n.id,
            ),
        )
        return None, best

    # -- call lifecycle -------------------------------------------------------

    def get_session(self, db: Session, *, workspace_id: str, session_id: str) -> VoiceCallSession:
        call = db.execute(
            select(VoiceCallSession).where(
                VoiceCallSession.id == session_id, VoiceCallSession.workspace_id == workspace_id
            )
        ).scalar_one_or_none()
        if call is None:
            raise VoiceNotFound("Call not found")
        return call

    def transition(
        self,
        db: Session,
        *,
        workspace_id: str,
        session_id: str,
        action: str,
        node_generation: int,
        reason: str | None = None,
    ) -> VoiceCallSession:
        target = ACTION_TARGETS.get(action)
        if target is None:
            raise VoiceInvalidRequest(f"Unknown action {action!r}")

        def work() -> tuple[VoiceCallSession, bool]:
            call = db.execute(
                select(VoiceCallSession)
                .where(
                    VoiceCallSession.id == session_id,
                    VoiceCallSession.workspace_id == workspace_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if call is None:
                raise VoiceNotFound("Call not found")
            if call.node_id is None:
                assert_transition(call.state, target)  # rejected: always illegal
            node = db.get(VoiceMediaNode, call.node_id)
            assert node is not None
            assert_generation(
                presented=node_generation,
                session_generation=call.node_generation,
                node_generation=node.generation,
                node_lost=node.lost_at is not None,
            )
            assert_transition(call.state, target)
            now = self.now()
            call.state = target
            if target == CallState.RINGING:
                call.started_at = now
            elif target == CallState.ACTIVE:
                call.answered_at = now
                call.started_at = call.started_at or now
            if target in TERMINAL_STATES:
                call.ended_at = now
                call.end_reason = (
                    reason or ("completed" if target == CallState.ENDED else "failed")
                )[:64]
                released = self._release_slots(db, [call.id], now=now, reason=call.end_reason)
                db.flush()
                return call, released > 0
            db.flush()
            return call, False

        call, released = self._retrying(db, work)
        if released:
            self.metrics.observe_release(call.end_reason or "completed")
        return call

    @staticmethod
    def _release_slots(db: Session, session_ids: list[str], *, now: datetime, reason: str) -> int:
        if not session_ids:
            return 0
        result = db.execute(
            update(VoiceCapacitySlot)
            .where(
                VoiceCapacitySlot.session_id.in_(session_ids),
                VoiceCapacitySlot.released_at.is_(None),
            )
            .values(released_at=now, release_reason=reason)
        )
        return int(result.rowcount or 0)

    # -- nodes ----------------------------------------------------------------

    def register_node(self, db: Session, *, node_id: str, max_concurrent: int) -> VoiceMediaNode:
        if not node_id or len(node_id) > 128 or max_concurrent < 0:
            raise VoiceInvalidRequest("node_id must be 1..128 chars and max_concurrent >= 0")

        def work() -> VoiceMediaNode:
            self._take_admission_lock(db)
            now = self.now()
            node = db.get(VoiceMediaNode, node_id)
            if node is None:
                node = VoiceMediaNode(id=node_id, max_concurrent=max_concurrent, generation=1)
                db.add(node)
            node.max_concurrent = max_concurrent
            # Re-registration after loss starts a new incarnation; the generation
            # was already advanced when the node was declared lost.
            node.lost_at = None
            node.heartbeat_at = now
            db.flush()
            return node

        return self._retrying(db, work)

    def heartbeat(self, db: Session, *, node_id: str, generation: int) -> VoiceMediaNode:
        def work() -> VoiceMediaNode:
            node = db.get(VoiceMediaNode, node_id, with_for_update=True)
            if node is None:
                raise VoiceNotFound("Node not found")
            now = self.now()
            if node.lost_at is not None or node.generation != generation:
                raise StaleGeneration("The media node generation is stale; re-register")
            node.heartbeat_at = now
            db.execute(
                update(VoiceCapacitySlot)
                .where(
                    VoiceCapacitySlot.node_id == node_id,
                    VoiceCapacitySlot.generation == generation,
                    VoiceCapacitySlot.released_at.is_(None),
                )
                .values(lease_expires_at=now + timedelta(seconds=self.policy.slot_lease_seconds))
            )
            db.flush()
            return node

        return self._retrying(db, work)

    def set_draining(self, db: Session, *, node_id: str, draining: bool) -> VoiceMediaNode:
        def work() -> VoiceMediaNode:
            self._take_admission_lock(db)
            node = db.get(VoiceMediaNode, node_id)
            if node is None:
                raise VoiceNotFound("Node not found")
            node.draining = draining
            db.flush()
            return node

        return self._retrying(db, work)

    def recover(self, db: Session) -> dict[str, Any]:
        """Declare lapsed nodes lost and reclaim expired slots (idempotent).

        Locks follow the module order (admission lock -> call session -> node
        -> slots), the order ``transition`` uses, so a sweep racing media-side
        end transitions cannot deadlock with them. Candidate nodes and expired
        slots are read first without locks; the live calls they cover are then
        locked FOR UPDATE, which on PostgreSQL waits for a concurrent
        transition and re-checks the live-state filter, so a call that was
        just ended is neither overwritten nor counted by the sweep.
        """

        def work() -> dict[str, Any]:
            bind_service_scope(db, SERVICE_SCOPE_VOICE_RECOVERY)
            self._take_admission_lock(db)
            now = self.now()
            cutoff = self._healthy_cutoff(now)
            lapsed = list(
                db.execute(
                    select(VoiceMediaNode.id).where(
                        VoiceMediaNode.lost_at.is_(None), VoiceMediaNode.heartbeat_at < cutoff
                    )
                ).scalars()
            )
            expired = list(
                db.execute(
                    select(VoiceCapacitySlot.session_id).where(
                        VoiceCapacitySlot.released_at.is_(None),
                        VoiceCapacitySlot.lease_expires_at < now,
                    )
                ).scalars()
            )
            failed: dict[str, list[str]] = {END_NODE_LOST: [], END_LEASE_EXPIRED: []}
            if not lapsed and not expired:
                return {"lost_nodes": [], "failed_calls": failed}
            covered = []
            if lapsed:
                covered.append(VoiceCallSession.node_id.in_(lapsed))
            if expired:
                covered.append(VoiceCallSession.id.in_(expired))
            live_calls = list(
                db.execute(
                    select(VoiceCallSession)
                    .where(VoiceCallSession.state.in_(list(LIVE_STATES)), or_(*covered))
                    .order_by(VoiceCallSession.id)
                    .with_for_update()
                ).scalars()
            )
            lost_nodes: list[str] = []
            if lapsed:
                nodes = (
                    db.execute(
                        select(VoiceMediaNode)
                        .where(
                            VoiceMediaNode.id.in_(lapsed),
                            VoiceMediaNode.lost_at.is_(None),
                            VoiceMediaNode.heartbeat_at < cutoff,
                        )
                        .order_by(VoiceMediaNode.id)
                        .with_for_update()
                    )
                    .scalars()
                    .all()
                )
                for node in nodes:
                    node.generation += 1
                    node.lost_at = now
                    lost_nodes.append(node.id)
            lost = set(lost_nodes)
            on_lost = [call for call in live_calls if call.node_id in lost]
            failed[END_NODE_LOST] = self._fail_calls(on_lost, now, END_NODE_LOST)
            if lost_nodes:
                self._release_slots_for_nodes(db, lost_nodes, now)
            still_expired: list[str] = []
            if expired:
                # Slots are locked last (module order) and the lease is judged
                # again: a heartbeat may have renewed it while the sweep waited
                # for a call lock.
                still_expired = list(
                    db.execute(
                        select(VoiceCapacitySlot.session_id)
                        .where(
                            VoiceCapacitySlot.session_id.in_(expired),
                            VoiceCapacitySlot.released_at.is_(None),
                            VoiceCapacitySlot.lease_expires_at < now,
                        )
                        .order_by(VoiceCapacitySlot.session_id)
                        .with_for_update()
                    ).scalars()
                )
            lapsed_set = set(still_expired)
            lapsed_leases = [
                call for call in live_calls if call.id in lapsed_set and call.node_id not in lost
            ]
            failed[END_LEASE_EXPIRED] = self._fail_calls(lapsed_leases, now, END_LEASE_EXPIRED)
            self._release_slots(db, still_expired, now=now, reason=END_LEASE_EXPIRED)
            db.flush()
            return {"lost_nodes": lost_nodes, "failed_calls": failed}

        report = self._retrying(db, work)
        for reason, ids in report["failed_calls"].items():
            for _ in ids:
                self.metrics.observe_release(reason)
        return report

    @staticmethod
    def _fail_calls(calls: list[VoiceCallSession], now: datetime, reason: str) -> list[str]:
        """Fail calls the caller locked FOR UPDATE while still live."""
        for call in calls:
            call.state = CallState.FAILED
            call.end_reason = reason
            call.ended_at = now
        return [call.id for call in calls]

    @staticmethod
    def _release_slots_for_nodes(db: Session, node_ids: list[str], now: datetime) -> None:
        db.execute(
            update(VoiceCapacitySlot)
            .where(
                VoiceCapacitySlot.node_id.in_(node_ids),
                VoiceCapacitySlot.released_at.is_(None),
            )
            .values(released_at=now, release_reason=END_NODE_LOST)
        )

    # -- configuration & observation ------------------------------------------

    def set_workspace_limit(self, db: Session, *, workspace_id: str, limit: int) -> int:
        if limit < 0:
            raise VoiceInvalidRequest("limit must be >= 0")

        def work() -> int:
            row = db.get(VoiceWorkspaceLimit, workspace_id)
            if row is None:
                db.add(VoiceWorkspaceLimit(workspace_id=workspace_id, max_concurrent_calls=limit))
            else:
                row.max_concurrent_calls = limit
            db.flush()
            return limit

        return self._retrying(db, work)

    def capacity_snapshot(
        self, db: Session, *, workspace_id: str, include_deployment: bool
    ) -> dict[str, Any]:
        now = self.now()
        snapshot: dict[str, Any] = {
            "workspace": {
                "workspace_id": workspace_id,
                "held": self._held_count(db, VoiceCapacitySlot.workspace_id == workspace_id),
                "limit": self._workspace_limit(db, workspace_id),
            }
        }
        if include_deployment:
            held_by_node = dict(
                db.execute(
                    select(VoiceCapacitySlot.node_id, func.count(VoiceCapacitySlot.id))
                    .where(VoiceCapacitySlot.released_at.is_(None))
                    .group_by(VoiceCapacitySlot.node_id)
                ).all()
            )
            nodes = db.execute(select(VoiceMediaNode).order_by(VoiceMediaNode.id)).scalars().all()
            snapshot["deployment"] = {
                "profile": self.policy.profile,
                "held": self._held_count(db),
                "limit": self.policy.global_limit,
                "nodes": [
                    {
                        "node_id": node.id,
                        "max_concurrent": node.max_concurrent,
                        "held": int(held_by_node.get(node.id, 0)),
                        "draining": node.draining,
                        "healthy": self._is_healthy(node, now),
                        "generation": node.generation,
                        "heartbeat_at": _utc(node.heartbeat_at).isoformat(),  # type: ignore[union-attr]
                    }
                    for node in nodes
                ],
            }
            snapshot["metrics"] = self.metrics.snapshot()
        return snapshot
