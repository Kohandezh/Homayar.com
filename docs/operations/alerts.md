# Alert runbook

Rules: [`infra/observability/prometheus/homayar.rules.yml`](../../infra/observability/prometheus/homayar.rules.yml)
(HOM-126, [ADR 0036](../architecture/decisions/0036-operational-evidence-observability-audit-release.md)).
Metrics: [observability.md](observability.md). Scrape the API as job `homayar-api`.

Every alert below is unit-tested with `promtool test rules`
([`tests/homayar.rules.test.yml`](../../infra/observability/prometheus/tests/homayar.rules.test.yml))
in a normal state (no alert) and in the failure it describes. `Homayar/backend/tests/test_alert_rules.py`
enforces that, and that the rules query only series and label values the API really exposes.
CI runs `promtool check rules` and `promtool test rules` in the *Container configuration* job.
Thresholds are defaults; tune them per installation in a copy of the rules file and keep the
tests in step.

First response for any alert: note the time, the release (`homayar_build_info`), the
schema state (`homayar_schema_up_to_date`) and the correlation ids involved; preserve logs
before restarting anything ([health-and-recovery.md](health-and-recovery.md)).

## HomayarApiScrapeFailing

Prometheus gets no `/metrics` from an API replica for 5 minutes. Check the API container
(`/healthz`), then the scrape credentials: in staging/production `/metrics` answers 404
without `METRICS_BEARER_TOKEN` (startup logs `METRICS_ENDPOINT_DISABLED`) and 401 with a
wrong token.

## HomayarDatabaseUnavailable

`SELECT 1` from the API failed for 2 minutes. `/readyz` is 503 at the same time. Follow
*API Not Ready* in [health-and-recovery.md](health-and-recovery.md): DNS, TLS, credentials,
connection limits, recovery mode, disk space. Do not restart PostgreSQL as a generic fix.

## HomayarRedisUnavailable

The broker does not answer `PING`. Uploads still commit their job and outbox row; nothing is
published or consumed until Redis returns, then the dispatch sweep republishes. Check memory
pressure, AOF state and the Redis password/TLS settings.

## HomayarObjectStorageUnavailable

A `HEAD` of a fixed absent key failed for 5 minutes. Uploads and processing fail. See
*Object Storage Failure* in [health-and-recovery.md](health-and-recovery.md); check the
endpoint, credentials, bucket policy (ListBucket is needed for a 404 instead of 403) and
certificate trust.

## HomayarSchemaBehindCode

The database's `alembic_version` differs from the code's migration head for 15 minutes:
a release was deployed without its migration, or rolled back without a downgrade. Run the
migration job for the deployed release, or roll the image back. Never edit
`alembic_version` by hand.

## HomayarApiErrorRate

More than 5% of requests answered 5xx for 10 minutes (with at least 0.2 requests/s).
Group `homayar_http_requests_total{status_class="5xx"}` by `route`, then search the API logs
for `UNHANDLED_ERROR` and the correlation ids.

## HomayarPeriodicSweepStale

A periodic sweep has no successful run for three of its intervals (at least 4 minutes, so a
30-second sweep alerts within about 5 minutes), or never reported one
(`HomayarPeriodicSweepNeverReported`, 30 minutes; silent while Redis is down, because the
heartbeats live there and `HomayarRedisUnavailable` names the cause). Either Celery beat
stopped, no worker consumes the `processing` queue, or the sweep fails before reporting.

1. `celery -A backend.celery_app inspect ping` and `inspect active_queues` (expects
   `processing`).
2. Check the `scheduler` container (one beat replica).
3. Read `homayar_periodic_task_last_failure_timestamp_seconds` and the worker logs for
   `sweep:<task>:`.
4. Sweeps share the `processing` queue with meeting jobs and expire after one interval: when
   every worker slot is busy with long jobs (`homayar_broker_queue_length` high), sweep
   messages expire unread. Add workers rather than lengthening the intervals.
5. Run the sweep once by hand (each module has `python -m backend.jobs.<module>`). A manual
   run writes no heartbeat: the alert clears only after the next run started by beat.

## HomayarPeriodicSweepFailing

At least two runs of a sweep failed in a row: its latest failure is more than 1.5 intervals
newer than its latest success, for 5 minutes. One transient failure does not alert, whatever
the interval. The heartbeat holds only the exception class (`last_error_type` in Redis
`homayar:obs:sweep:<task>`); the worker log line with the same `sweep:<task>:<run>`
correlation id has the traceback. A sweep that keeps failing also raises
`HomayarPeriodicSweepStale` after three intervals.

## HomayarQueueBacklog

A due job waited more than 15 minutes (`HomayarQueueStalled`: an hour). Compare
`homayar_jobs_queued_due` with `homayar_broker_queue_length`: messages in the broker but not
consumed means workers are missing or saturated (*Worker Backlog or Failure*); due jobs
without broker messages means publication is stuck (see `HomayarDispatchOutboxStuck`).
Scale by the measured bottleneck and keep workspace concurrency limits.

## HomayarExpiredLeasesNotRecovered

Running jobs kept an expired lease for 10 minutes. The lease sweep should fence them within
one lease plus one sweep interval (about 3 minutes). Check `HomayarPeriodicSweepStale` for
`lease_recovery`, then *Abandoned Running Jobs* in the health runbook.

## HomayarDispatchOutboxStuck

A due outbox row stayed unpublished for 5 minutes: the broker refuses publications or the
dispatch sweep does not run. `GET /meetings/{id}/job` shows `dispatch.last_error`. Fix the
broker; the sweep republishes. Never edit `job_dispatches` by hand.

## HomayarProcessingFailureRate

More than 20% of the jobs that finished in 15 minutes failed (at least 5 finished). Break
it down with `homayar_jobs_failed_15m` by `category` and `homayar_jobs_failed_by_class_15m`
by `failure_class`; `homayar_workspaces_with_failed_jobs_15m` says whether one tenant or many
are affected. Workspace admins see their own jobs in the console (admin → processing).

## HomayarDeadLetterJobs

Three or more jobs were parked in dead-letter in 15 minutes. Find the cause first (provider,
broker, storage, deadline), then replay through `POST /api/v1/admin/jobs/{id}/replay`
(*Dead-Letter Jobs and Replay*). Never bulk-update `processing_jobs`.

## HomayarRetryBacklog

More than 10 jobs wait for a retry for 15 minutes: a transient cause (provider, storage,
timeouts) persists. Check provider circuits and storage health before adding workers.

## HomayarProviderCircuitOpen

A provider endpoint's circuit stayed open for 10 minutes (it opens after
`PROVIDER_CIRCUIT_FAILURE_THRESHOLD` consecutive transient failures for
`PROVIDER_CIRCUIT_COOLDOWN_SECONDS`). Inspect `provider_health` (`last_failure_code`,
`open_until`) as a DBA; check egress policy, credentials, quotas and the provider's status.

## HomayarProviderJobFailures

Provider failures stopped three or more jobs in 15 minutes. Same checks as
`HomayarProviderCircuitOpen`; local providers (Whisper) also need their model artifacts.

## HomayarStorageReconciliationFailing

The media reconciliation or artifact cleanup sweep keeps hitting object-store errors
(`unavailable`, `pending_failed`, `orphans_failed`) for 30 minutes. Check storage health and
permissions; pending deletions are retried automatically once storage works.

## HomayarMissingMediaObjects

Media objects recorded in PostgreSQL are missing from object storage
(`missing_flagged > 0`). Treat as an integrity incident: stop deletions you did not plan,
compare with the last object manifest and backup, see
[backup-restore.md](backup-restore.md).

The alert fires on the reconciliation run that flags the objects and **resolves at the next
run**, because rows already flagged missing (`media_objects.integrity`) are not counted
again. Resolved does not mean repaired: the flagged rows stay until an operator restores the
bytes or closes the incident.

## HomayarPurgeFailed

Conversation purges exhausted their attempts (`purges_failed_open`), or stayed pending for
more than 24 hours without a hold (`HomayarPurgeOverdue`). Deleted or expired conversations
still hold content or bytes. Find the rows with
`SELECT id, workspace_id, last_error, attempts FROM conversation_purges WHERE state = 'FAILED'`
(DBA, read-only), fix the cause (usually storage), then requeue through the documented
purge requeue path; the requeue is audited as `meeting.purge_requeued`.

## HomayarTombstoneJournalBacklog

Tombstones are not reaching the journal outside the backups (`journal_backlog > 0` for an
hour). A restore made now could resurrect deleted conversations. Check
`TOMBSTONE_JOURNAL_DIR` (mounted, writable, replicated) and the retention sweep logs.

## HomayarAuditWriteFailures

An audit event could not be written. With `delivery="transactional"` the operation it
described was rolled back and the user got an error; with `delivery="best_effort"` a refused
attempt (failed login, replayed refresh token) was not recorded. Check the database (disk,
permissions of `homayar_app` on `audit_events`: INSERT and SELECT must remain) and the API
logs for `AUDIT_WRITE_FAILED`. This is a security signal: do not silence it.

## HomayarAuditExportStale

No export into the protected audit sink for 2 hours. Check the operator job that runs
`python -m backend.observability.audit_sink export` (cron or Kubernetes CronJob), its
database login (`AUDIT_EXPORT_DATABASE_URL`, member of `homayar_audit_reader`) and the sink
volume. See [audit.md](audit.md).

## HomayarAuditSinkVerificationFailed

The latest `audit_sink verify` run failed: the database and the protected sink disagree, or
the hash chain is broken. Treat as a security incident until explained. Keep the sink and the
database untouched, read the verifier's report (changed, missing and unexported events by id)
and follow *Integrity incident* in [audit.md](audit.md). An owner-approved DBA erasure shows up
here by design and is recorded in the erasure log.

## HomayarLoginFailureSpike

More than 60 failed logins per minute for 10 minutes. The API and the proxy rate-limit per IP
and per identifier, so a spike usually means a distributed attempt. Review the
`auth.login` audit rows with `outcome = 'failure'` (identifier digests and IP hashes only) and
consider tightening the proxy limits.

## HomayarRefreshTokenReplay

A refresh token was presented twice; the session was revoked. One event can be a client bug
(two tabs refreshing at once); repeated events for one user suggest token theft. The audit
row `auth.refresh_replay_detected` names the user and session.

## HomayarMetricsCollectionErrors

Scrape-time state sources failed more than 5 times in 15 minutes
(`homayar_metrics_collection_errors_total{source}`): some series are missing, so other
alerts may stay silent. The API logs `METRICS_SOURCE_FAILED source=<name> error_type=<class>`.

## HomayarBackupTooOld

The last successful backup of a target is older than 26 hours (RPO 24 hours plus margin), or a
target reports no timestamp at all (`HomayarBackupMetricMissing`). The backup tooling writes
`homayar_backup_last_success_timestamp_seconds{target}` to the node_exporter textfile
directory. Follow [backup-restore.md](backup-restore.md).
