"""Response of ``GET /api/v1/admin/system-health`` (HOM-227; the console reads it, HOM-232).

Every key is always present; a value a failed source could not give is ``null``.
Timestamps are UTC (``...Z``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DependencyStatus = Literal["up", "down", "timeout"]


class DependencyHealth(BaseModel):
    status: DependencyStatus
    probe_seconds: float | None


class DependenciesHealth(BaseModel):
    database: DependencyHealth
    redis: DependencyHealth
    storage: DependencyHealth


class SchemaHealth(BaseModel):
    status: Literal["up_to_date", "behind", "unstamped", "unknown"]
    code_head: str | None
    database_revision: str | None


class QueueHealth(BaseModel):
    status: Literal["ok", "unknown"]
    queued_due: int | None
    oldest_queued_due_age_seconds: float | None
    retry_scheduled: int | None
    broker_queue_length: int | None


class SweepHealth(BaseModel):
    task: str
    status: Literal["ok", "stale", "failing", "never_run", "unknown"]
    expected_interval_seconds: float | None
    last_run_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_failure_class: str | None
    last_duration_seconds: float | None


class SweepsHealth(BaseModel):
    status: Literal["ok", "unknown"]
    tasks: list[SweepHealth]


class BackupTargets(BaseModel):
    postgres: datetime | None
    objects: datetime | None
    offsite: datetime | None


class BackupHealth(BaseModel):
    status: Literal["ok", "stale", "missing", "not_configured", "unknown"]
    targets: BackupTargets


class AuditSinkHealth(BaseModel):
    status: Literal["ok", "stale", "failing", "never_run", "not_configured", "unknown"]
    last_verify_at: datetime | None
    last_verify_success_at: datetime | None
    last_failure_class: str | None


class BuildIdentity(BaseModel):
    version: str
    source_sha: str
    migration_head: str
    profile: str
    python_version: str


class SystemHealthResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["ok", "degraded", "down"]
    checked_at: datetime
    dependencies: DependenciesHealth
    # "schema" would shadow a BaseModel attribute.
    schema_: SchemaHealth = Field(alias="schema")
    queue: QueueHealth
    sweeps: SweepsHealth
    backup: BackupHealth
    audit_sink: AuditSinkHealth
    build: BuildIdentity
