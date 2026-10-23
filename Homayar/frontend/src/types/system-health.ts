/**
 * The answer of GET /api/v1/admin/system-health (HOM-227, PR #108). Every
 * key is always present; a value a failed source could not give is null.
 * Timestamps are ISO 8601 UTC. A status string this file does not list (a
 * newer backend) is shown as itself, never mapped (HOM-232 P5).
 */

export interface SystemHealthDependency {
  status: "up" | "down" | "timeout";
  /** Null on timeout. */
  probe_seconds: number | null;
}

export interface SystemHealthSchema {
  status: "up_to_date" | "behind" | "unstamped" | "unknown";
  code_head: string | null;
  database_revision: string | null;
}

export interface SystemHealthQueue {
  status: "ok" | "unknown";
  /** Every numeric field is null when the queue source failed. */
  queued_due: number | null;
  oldest_queued_due_age_seconds: number | null;
  retry_scheduled: number | null;
  broker_queue_length: number | null;
}

export interface SystemHealthSweepTask {
  /** Latin task name, shown as sent. */
  task: string;
  status: "ok" | "stale" | "failing" | "never_run" | "unknown";
  expected_interval_seconds: number | null;
  last_run_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_failure_class: string | null;
  last_duration_seconds: number | null;
}

export interface SystemHealthSweeps {
  status: "ok" | "unknown";
  /** In the order the API sent them. */
  tasks: SystemHealthSweepTask[];
}

export interface SystemHealthBackup {
  status: "ok" | "stale" | "missing" | "not_configured" | "unknown";
  /** The last successful backup time per target, or null. */
  targets: {
    postgres: string | null;
    objects: string | null;
    offsite: string | null;
  };
}

export interface SystemHealthAuditSink {
  status: "ok" | "stale" | "failing" | "never_run" | "not_configured" | "unknown";
  last_verify_at: string | null;
  last_verify_success_at: string | null;
  last_failure_class: string | null;
}

export interface SystemHealthBuild {
  version: string | null;
  source_sha: string | null;
  migration_head: string | null;
  profile: string | null;
  python_version: string | null;
}

export interface SystemHealthResponse {
  status: "ok" | "degraded" | "down";
  checked_at: string;
  dependencies: {
    database: SystemHealthDependency;
    redis: SystemHealthDependency;
    storage: SystemHealthDependency;
  };
  schema: SystemHealthSchema;
  queue: SystemHealthQueue;
  sweeps: SystemHealthSweeps;
  backup: SystemHealthBackup;
  audit_sink: SystemHealthAuditSink;
  build: SystemHealthBuild;
}
