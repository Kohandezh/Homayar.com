# Alert runbook

Rules: [`infra/observability/prometheus/homayar.rules.yml`](../../infra/observability/prometheus/homayar.rules.yml)
(HOM-126, [ADR 0036](../architecture/decisions/0036-operational-evidence-observability-audit-release.md)).
Metrics: [observability.md](observability.md). Scrape configuration:
[`prometheus.example.yml`](../../infra/observability/prometheus/prometheus.example.yml). The rules
need its two job names: `homayar-api` (the API's `/metrics`) and `homayar-node` (node_exporter
with the backup textfile).

Every alert below is unit-tested with `promtool test rules`
([`tests/homayar.rules.test.yml`](../../infra/observability/prometheus/tests/homayar.rules.test.yml))
in a normal state (no alert) and in the failure it describes. `Homayar/backend/tests/test_alert_rules.py`
enforces that, and that the rules query only series with real samples and label values the API
really emits. CI runs `promtool check rules`, `promtool test rules` and `promtool check config` in
the *Container configuration* job.
Thresholds are defaults; tune them per installation in a copy of the rules file and keep the
tests in step.

Only alerts whose series the code emits today are here. The protected audit sink's alerts read
the heartbeats its operator job reports (`audit_export`, `audit_verify`; HOM-115).

First response for any alert: note the time, the release (`homayar_build_info`), the
schema state (`homayar_schema_up_to_date`) and the correlation ids involved; preserve logs
before restarting anything ([health-and-recovery.md](health-and-recovery.md)).

## HomayarApiScrapeFailing

Prometheus gets no `/metrics` from an API replica for 5 minutes (`up == 0`), or has no
`homayar-api` target at all for 10 minutes (`HomayarApiScrapeTargetMissing`). Either way every
other alert on the API's series is blind. Check Prometheus' *Targets* page for the error, then:

1. **No target**: the scrape job is missing, has another name, or service discovery found no
   API container. The job must be called `homayar-api` (`prometheus.example.yml`).
2. **Connection refused or timeout**: the API container is down (`/healthz`). A refresh of the
   scrape-time state waits at most about 6 s (two stages of `SOURCE_TIMEOUT_SECONDS` = 3 s), so
   `scrape_timeout` must stay above that; the example uses 10 s.
3. **404**: `/metrics` is disabled. In staging and production it needs `METRICS_BEARER_TOKEN`
   (at least 32 characters) inside the API container; the startup log says
   `METRICS_ENDPOINT_DISABLED`. Both compose profiles pass the `METRICS_*` settings from the
   environment, so check that the variable is set there (or, on-premise only, that
   `METRICS_ALLOW_UNAUTHENTICATED=true` is intended).
4. **401**: the token in Prometheus' `credentials_file` differs from the API's.
5. **400 "Invalid host header"**: the host check (`TRUSTED_HOSTS`) answered. The API exempts
   exactly the path `/metrics` from it, so the request went somewhere else: through the public
   proxy, to another path (`/metrics/`, a prefix), or to an image older than HOM-113's review
   fixes. Scrape `http://api:8000/metrics` directly.

## HomayarDatabaseUnavailable

`SELECT 1` from the API failed for 2 minutes. `/readyz` is 503 at the same time. Follow
*API Not Ready* in [health-and-recovery.md](health-and-recovery.md): DNS, TLS, credentials,
connection limits, recovery mode, disk space. Do not restart PostgreSQL as a generic fix.
While it fires, the sweep alerts stay silent: no sweep can run without the database.

## HomayarRedisUnavailable

The broker does not answer `PING`. Uploads still commit their job and outbox row; nothing is
published or consumed until Redis returns, then the dispatch sweep republishes. Check memory
pressure, AOF state and the Redis password/TLS settings. The sweep heartbeats live in Redis, so
the sweep alerts cannot be evaluated meanwhile.

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
for `UNHANDLED_ERROR` and the correlation ids; uvicorn's own `Exception in ASGI application`
line carries the same `request_id=`. Targets with inconsistent counters
(`HomayarApiCountersInconsistent`) are left out of the ratio.

## HomayarApiCountersInconsistent

The API's request counters reset more than twice in 15 minutes. A restart resets them once; this
many means one of two things:

- **Several uvicorn workers without a shared counter directory.** With `UVICORN_WORKERS` above 1,
  every worker needs `PROMETHEUS_MULTIPROC_DIR` set to the same empty, per-container directory.
  Without it each scrape reaches another worker and every drop to a smaller count looks like a
  reset, so rates and ratios measure nothing. Both compose profiles set `/run/homayar-metrics`,
  a 32 MB tmpfs of its own; a custom deployment needs the same. Set the directory, or run one
  worker. A write into that directory that fails (full or missing) does not fail the request:
  the API logs `METRICS_WRITE_FAILED error_type=…` at most once a minute, and the counters stop
  moving. That is a log signal, not a metric: search for it whenever the request counters look
  wrong.
- **The API keeps restarting.** Check the container's restart count and the logs before each
  restart.

While it fires, `HomayarApiErrorRate` and `HomayarMetricsCollectionErrors` ignore that target.

## HomayarPeriodicSweepStale

Three alerts share this section:

- `HomayarPeriodicSweepStale` (warning): a periodic sweep has no successful run for three of its
  intervals, at least 4 minutes. A 30-second sweep warns within about 5 minutes. It also warns
  when long meeting jobs hold every worker slot: sweeps share the `processing` queue and their
  messages expire after one interval. That is expected under load and resolves when a slot
  frees.
- `HomayarPeriodicSweepStopped` (critical): the same staleness for 10 more minutes while no job
  runs on a live worker, or no success for more than 2 h 15 min. That is longer than one job
  attempt can hold a slot (`JOB_ATTEMPT_TIMEOUT_SECONDS`, 7200 s, plus grace). It stays silent
  while due jobs wait, because `HomayarQueueBacklog` / `HomayarQueueStalled` then name the cause,
  and while PostgreSQL is down.
- `HomayarPeriodicSweepNeverReported` (warning): a sweep with a configured interval never
  reported a success, for 30 minutes. Silent while Redis or PostgreSQL is down.

Either Celery beat stopped, no worker consumes the `processing` queue, or the sweep fails:

1. `celery -A backend.celery_app inspect ping` and `inspect active_queues` (expects
   `processing`).
2. Check the `scheduler` container (one beat replica).
3. Compare `homayar_periodic_task_last_failure_timestamp_seconds` with
   `..._last_success_...`. A newer failure means the sweep runs and fails. The heartbeat holds
   only the exception class (`last_error_type` in Redis `homayar:obs:sweep:<task>`); the worker
   log line with the correlation id `sweep:<task>:<run>` has the traceback. There is no separate
   "failed twice in a row" alert: the heartbeat keeps only the latest run, and a sweep that keeps
   failing is stale after three intervals.
4. Busy workers: `homayar_jobs{state="running"}` at the worker concurrency and
   `homayar_broker_queue_length` above 0. Add workers rather than lengthening the intervals.
5. Run the sweep once by hand (each module has `python -m backend.jobs.<module>`). A manual
   run writes no heartbeat: the alert clears only after the next run started by beat.

## HomayarQueueBacklog

A due job waited more than 15 minutes (`HomayarQueueStalled`: an hour). Compare
`homayar_jobs_queued_due` with `homayar_broker_queue_length`: messages in the broker but not
consumed means workers are missing or saturated (*Worker Backlog or Failure*); due jobs
without broker messages means publication is stuck (see `HomayarDispatchOutboxStuck`).
Scale by the measured bottleneck and keep workspace concurrency limits. While due jobs wait,
`HomayarPeriodicSweepStopped` stays silent, so check the sweeps here too.

## HomayarExpiredLeasesNotRecovered

Running jobs kept an expired lease for 10 minutes. The lease sweep should fence them within
one lease plus one sweep interval (about 3 minutes). Check `HomayarPeriodicSweepStale` for
`lease_recovery`, then *Abandoned Running Jobs* in the health runbook.

## HomayarDispatchOutboxStuck

A due outbox row stayed unpublished for 5 minutes: the broker refuses publications or the
dispatch sweep does not run. `GET /meetings/{id}/job` shows `dispatch.last_error`. Fix the
broker; the sweep republishes. Never edit `job_dispatches` by hand.

## HomayarProcessingFailureRate

More than 20% of the jobs that finished in 15 minutes failed (at least 5 finished; cancelled
jobs do not count). Break it down with `homayar_jobs_failed_15m` by `category` and
`homayar_jobs_failed_by_class_15m` by `failure_class`; `homayar_workspaces_with_failed_jobs_15m`
says whether one tenant or many are affected. Workspace admins see their own jobs in the console
(admin → processing).

## HomayarDeadLetterJobs

Jobs were parked in dead-letter: three or more in 15 minutes, or five or more in 6 hours
(`HomayarDeadLetterTrickle`, which catches a steady trickle). Each one is a meeting that is not
processed until someone replays it. Find the cause first (provider, broker, storage, deadline),
then replay through `POST /api/v1/admin/jobs/{id}/replay` (*Dead-Letter Jobs and Replay*). Never
bulk-update `processing_jobs`.

## HomayarRetryBacklog

More than 10 jobs wait for a retry for 15 minutes: a transient cause (provider, storage,
timeouts) persists. Check provider circuits and storage health before adding workers.

## HomayarProviderCircuitOpen

A provider endpoint of the stage failed 5 or more calls in a row for 10 minutes, and failed
within the last 45 minutes. Its circuit opens at `PROVIDER_CIRCUIT_FAILURE_THRESHOLD` (5; change
the rule with the setting) for `PROVIDER_CIRCUIT_COOLDOWN_SECONDS`, admits one call, and opens
again when that call fails. `homayar_provider_circuits_open` therefore flaps during an outage;
the failure count does not. The alert resolves after a successful call, or 45 minutes after the
last failure when nothing calls the endpoint any more. Inspect `provider_health`
(`last_failure_code`, `open_until`) as a DBA; check egress policy, credentials, quotas and the
provider's status.

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

Deleted or expired conversations still hold content or bytes.

**`HomayarPurgeFailed`** (critical): purges exhausted their attempts (`purges_failed_open`).
Find them as a DBA, read-only:

```sql
SELECT id, workspace_id, meeting_id, attempts, last_error
FROM conversation_purges WHERE state = 'FAILED';
```

Fix the cause first (usually storage: `HomayarObjectStorageUnavailable`,
`HomayarStorageReconciliationFailing`). Then the purge needs a fresh attempt budget.
**There is no operator command or route for that yet.** `requeue_failed_purge()` in
`backend/services/conversation_purge_service.py` does it and records `meeting.purge_requeued`,
but only tests call it (follow-up). Escalate to engineering. Do not `UPDATE conversation_purges`
by hand: that skips the audit event and the service's state checks.

**`HomayarPurgeOverdue`** (warning): purges have been pending for more than 24 hours without a
legal hold (`purges_overdue`). They are still retried on every run:

```sql
SELECT id, workspace_id, meeting_id, requested_at, attempts, next_attempt_at, last_error
FROM conversation_purges
WHERE state = 'PENDING' AND requested_at <= now() - interval '24 hours'
  AND (last_error IS NULL OR last_error <> 'legal_hold');
```

`last_error` names the cause. Usual causes: the retention sweep does not run
(`HomayarPeriodicSweepStale` for `retention_purge`), storage errors, or a processing job of the
meeting that is still running and defers the purge.

**Scope.** The retention, media and artifact sweeps visit at most their batch size of workspaces
per run, always the same first ones by id (`RETENTION_PURGE_BATCH_SIZE` 100,
`MEDIA_RECONCILIATION_BATCH_SIZE` and `JOB_ARTIFACT_SWEEP_BATCH_SIZE` 200). Workspaces beyond
that are neither swept nor counted, so these alerts cannot see them. Until the sweeps page
through all workspaces, keep the batch sizes above the number of workspaces.

## HomayarTombstoneJournalBacklog

Tombstones are not reaching the journal outside the backups (`journal_backlog > 0` for an
hour). A restore made now could resurrect deleted conversations. Check
`TOMBSTONE_JOURNAL_DIR` (mounted, writable, replicated) and the retention sweep logs. The
retention sweep's workspace limit (see `HomayarPurgeFailed`) applies here too.

## HomayarMetricsCollectionErrors

Scrape-time state sources failed more than 5 times in 15 minutes
(`homayar_metrics_collection_errors_total{source}`): some series are missing, so other
alerts may stay silent. The API logs `METRICS_SOURCE_FAILED source=<name> error_type=<class>`;
`Timeout` and `StillRunning` mean the source missed its 3-second deadline and is still hanging.
The dependency probes (`database`, `redis`, `storage`) are left out because their own alerts
report them, and the alert is silent while a dependency is down.

## HomayarBackupTooOld

The backup tooling (Lane C) writes `homayar_backup_last_success_timestamp_seconds{target}` for
`postgres`, `objects` and `offsite` to a node_exporter textfile (`HOMAYAR_BACKUP_TEXTFILE`,
by default `$BACKUP_ROOT/metrics/homayar_backup.prom`). All three alerts here are critical,
because the RPO of 24 hours is either broken or unknown:

- `HomayarBackupTooOld`: the target's last success is more than 26 hours old (RPO plus 2 hours).
- `HomayarBackupMetricMissing`: node_exporter reads the textfile, but the target is not in it
  for an hour. That backup never succeeded, is not scheduled (the off-site copy needs its own
  `backup replicate` run), or writes to another file.
- `HomayarBackupExporterDown`: the backup textfile cannot reach Prometheus for 15 minutes. Either
  the `homayar-node` target is down or missing, or `node_textfile_scrape_error` is 1 because
  node_exporter cannot read the file or a directory on its way. On-premise, the `monitoring`
  compose profile runs `node-exporter` (job `homayar-node`) on `NODE_EXPORTER_TEXTFILE_DIR`, where
  `backup-tool` writes `homayar_backup.prom`, outside the 0700 backup root. Elsewhere, keep
  `HOMAYAR_BACKUP_TEXTFILE` outside that root too and start node_exporter with
  `--collector.textfile.directory` set to the file's directory. `HomayarBackupMetricMissing` is
  silent meanwhile.

Then follow [backup-restore.md](backup-restore.md).

## HomayarAuditWriteFailures

An audit row could not be written (`homayar_audit_write_failures_total{delivery}`, HOM-114).
`transactional`: the operation being audited was rolled back and the caller got an error. A
successful login fails closed this way (owner decision 2026-10-18). `best_effort`: a refused
attempt (a failed login, a refresh-token replay) happened without its row.

1. Check the API logs for `AUDIT_WRITE_FAILED` and database errors (SQLSTATE, constraint).
2. Check PostgreSQL: disk, connection limits, `audit_events` privileges (after HOM-115 the
   application may only INSERT and SELECT).
3. Once writes succeed, the alert clears after 10 minutes; nothing needs replaying.

## HomayarRefreshTokenReplay

A refresh token that was already used came back. The session was revoked in the same request
(the revocation commits before the audit row). Treat it as a likely stolen session: find the
`auth.refresh_replay_detected` audit event (actor and `login_session` target), contact the
user, and review their other sessions and recent activity.

## HomayarLoginFailureSpike

More than 50 refused logins in 10 minutes for 10 minutes. Look at `auth.login_failed` events
(only an identifier-hash prefix is stored) and the proxy's request rate per client address.
Rate-limit or block at the proxy for credential stuffing; check a client release if one
identifier fails repeatedly.

## HomayarAuditExportStale

The protected audit sink ([audit.md](audit.md#protected-audit-sink)) has not had a successful
export for 2 hours, its first export keeps failing, or no export has ever reported a heartbeat
(the operator job `python -m backend.observability.audit_sink export` is not scheduled). Until it
runs again, rows written since the last export have no independent copy, and a row changed,
deleted or back-dated by the table owner in that window cannot be detected. The threshold
assumes the 15-minute schedule of the [export runbook](audit.md#export-runbook).

1. On the operator's host, run the export by hand and read its last line on stderr (exit 2 is a
   configuration error or a refusal; the message names the cause, never a URL or key):
   `AUDIT_EXPORT_DATABASE_URL`, `AUDIT_SINK_HMAC_KEY`, `AUDIT_SINK_DIR`, the exporter's login and
   `homayar_audit_reader` ([exporter setup](audit.md#exporter-setup)).
2. A refused first export in staging or production needs the
   [legacy-row review](audit.md#legacy-row-review) and its approval reference
   (`export --legacy-review <ref>`).
3. `another export or erasure record is running`: a stuck run holds `.lock`; find and stop it.
   `HEAD.json fails its MAC` or a manifest without its segment: run `verify --sink-only` and
   follow [verification findings](audit.md#verification-findings) before exporting again.
4. Check the scheduler on the operator's host (cron or timer) and that `REDIS_URL` there points
   at the deployment's Redis, or the heartbeat never reaches `/metrics`.

## HomayarAuditSinkVerificationFailed

The last `python -m backend.observability.audit_sink verify` run failed: it found a problem or
could not run. Treat it as a possible security incident until the report says otherwise.

1. Run `verify` again on the operator's host and keep its JSON report; it lists each problem with
   its kind, sequence number and event id, never a value.
2. Act on each kind as [verification findings](audit.md#verification-findings) says: an altered,
   missing or reordered record, a `HEAD.json` changed without the key, a row changed, deleted or
   back-dated in `audit_events`. Compare with the sink's backup copy (`verify --sink-only`) to
   tell which side changed, and preserve both before changing anything.
3. Exit 2 (configuration: key, sink directory, exporter login, `homayar_audit_reader` not inert)
   is not tampering: fix the setup and run `verify` again.
4. The alert clears when a `verify` run succeeds. Accepted erasures and deleted workspaces are
   notices, not failures; they never raise this alert.
