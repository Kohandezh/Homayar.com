"""Workspace AI processing policies and per-attempt snapshots (WP08-T02, ADR 0027).

Every read and write runs in the caller's tenant-bound session; the rows are
workspace tables under row-level security, so a caller-supplied workspace id
never reaches another tenant's policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.database.saas_models import AiPolicyVersion, RunPolicySnapshot
from backend.providers.policy import DEFAULT_POLICY, DEFAULT_VERSION, POLICY_SCHEMA, AiPolicy


@dataclass(frozen=True)
class EffectivePolicy:
    """The policy a workspace currently runs under (version 0 = the default)."""

    version: int
    policy: AiPolicy
    created_at: datetime | None = None
    created_by_user_id: str | None = None
    reason: str | None = None

    @property
    def source(self) -> str:
        return "default" if self.version == DEFAULT_VERSION else "workspace"

    @property
    def digest(self) -> str:
        return self.policy.digest


DEFAULT_EFFECTIVE = EffectivePolicy(version=DEFAULT_VERSION, policy=DEFAULT_POLICY)


class PolicyVersionConflict(Exception):
    def __init__(self, current_version: int) -> None:
        super().__init__("The policy changed since it was read")
        self.current_version = current_version


def _effective(row: AiPolicyVersion) -> EffectivePolicy:
    return EffectivePolicy(
        version=row.version,
        policy=AiPolicy.model_validate(row.document),
        created_at=row.created_at,
        created_by_user_id=row.created_by_user_id,
        reason=row.reason,
    )


def current_policy(db: Session, *, workspace_id: str) -> EffectivePolicy:
    row = db.scalar(
        select(AiPolicyVersion)
        .where(AiPolicyVersion.workspace_id == workspace_id)
        .order_by(AiPolicyVersion.version.desc())
        .limit(1)
    )
    return _effective(row) if row is not None else DEFAULT_EFFECTIVE


def current_policy_version(db: Session, *, workspace_id: str) -> int:
    latest = db.scalar(
        select(func.max(AiPolicyVersion.version)).where(
            AiPolicyVersion.workspace_id == workspace_id
        )
    )
    return int(latest or DEFAULT_VERSION)


def list_policy_versions(
    db: Session, *, workspace_id: str, limit: int = 50
) -> list[EffectivePolicy]:
    rows = db.scalars(
        select(AiPolicyVersion)
        .where(AiPolicyVersion.workspace_id == workspace_id)
        .order_by(AiPolicyVersion.version.desc())
        .limit(limit)
    )
    return [_effective(row) for row in rows]


def set_policy(
    db: Session,
    *,
    workspace_id: str,
    policy: AiPolicy,
    expected_version: int,
    actor_user_id: str,
    reason: str,
    now: datetime,
) -> tuple[EffectivePolicy, bool]:
    """Append a new version when the document differs from the current one.

    ``expected_version`` is the version the caller read (optimistic
    concurrency); a concurrent writer makes one of two requests fail with
    ``PolicyVersionConflict`` (checked here and by the unique constraint).
    Returns the current policy and whether a version was added.
    """
    current = current_policy(db, workspace_id=workspace_id)
    if expected_version != current.version:
        raise PolicyVersionConflict(current.version)
    if policy.digest == current.digest:
        return current, False
    row = AiPolicyVersion(
        workspace_id=workspace_id,
        version=current.version + 1,
        policy_schema=POLICY_SCHEMA,
        document=policy.document(),
        digest=policy.digest,
        reason=reason,
        created_by_user_id=actor_user_id,
        created_at=now,
    )
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        raise PolicyVersionConflict(current_policy_version(db, workspace_id=workspace_id)) from None
    return _effective(row), True


def changed_stages(before: AiPolicy, after: AiPolicy) -> list[str]:
    return [
        stage
        for stage in ("asr", "diarization", "intelligence")
        if before.stage(stage) != after.stage(stage)
    ]


def record_run_snapshot(
    db: Session,
    *,
    workspace_id: str,
    meeting_id: str,
    job_id: str,
    attempt: int,
    effective: EffectivePolicy,
    text_privacy_digest: str | None,
    strict_manifests: bool,
    stages: list[dict[str, Any]],
    now: datetime,
) -> RunPolicySnapshot:
    snapshot = RunPolicySnapshot(
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        job_id=job_id,
        attempt=attempt,
        policy_version=effective.version,
        policy_digest=effective.digest,
        policy_document=effective.policy.document(),
        text_privacy_digest=text_privacy_digest,
        strict_manifests=strict_manifests,
        stages=stages,
        created_at=now,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def list_run_snapshots(db: Session, *, workspace_id: str, job_id: str) -> list[RunPolicySnapshot]:
    return list(
        db.scalars(
            select(RunPolicySnapshot)
            .where(
                RunPolicySnapshot.workspace_id == workspace_id,
                RunPolicySnapshot.job_id == job_id,
            )
            .order_by(RunPolicySnapshot.attempt.asc())
        )
    )
