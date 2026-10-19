# Observability: metrics, correlation ids and heartbeats

Scope: HOM-113 (WP20-T01). Decision record: [ADR 0036](../architecture/decisions/0036-operational-evidence-observability-audit-release.md).
Alerts on these series, their runbook and an example scrape configuration: [alerts.md](alerts.md) (HOM-126).

## Correlation ids

Every unit of work has one correlation id:

| Work | Correlation id | Where it appears |
| --- | --- | --- |
| HTTP request | the client's `X-Request-ID` if it matches `^[A-Za-z0-9._:-]{8,80}$`, else a fresh UUID | `X-Request-ID` response header, error bodies (`error.request_id`), JSON logs (`correlation_id`), `audit_events.request_id` |
| Processing attempt | `job:<job id>:<delivery>` (first 8 characters of the broker delivery id) | worker JSON logs, audit events written by the attempt (`meeting.processing.*`) |
| Periodic sweep run | `sweep:<task>:<12 hex>` | worker JSON logs, audit events written by the sweep |

The id is bound in a context variable (`backend/observability/correlation.py`), so code
that records an audit event without a request id still stores the right one.

Find a failed job: take the job id from `GET /api/v1/admin/jobs` (workspace admin) or the
triage SQL in [health-and-recovery.md](health-and-recovery.md), then search the worker
logs and `audit_events.request_id` for the prefix `job:<id>` (each attempt adds its own
`:<delivery>` suffix, so a fenced attempt and its replacement can be told apart).

Jobs parked by a sweeper never ran on a worker under that id: a publication that
exhausted its attempts (`category="broker"`) is parked by the dispatch sweep, an
abandoned attempt (`category="worker_lost"`) by the lease-recovery sweep. Their terminal
`meeting.processing.failed` event carries `request_id = sweep:<task>:<run>` and
`target_id = <meeting id>`: search `audit_events` by the meeting id (the job's
`meeting_id`) and `event_type`, then the worker logs by that sweep run's id.

## The `/metrics` endpoint

* Served by the API process on its own port (`http://api:8000/metrics`). The nginx proxy
  does not route it (`location /` goes to the public website; a test asserts this).
* `/metrics` is the one path exempt from `TRUSTED_HOSTS`: Prometheus reaches the API by its
  internal name (`Host: api:8000`), and the endpoint returns nothing derived from the host.
  Every other path still answers 400 for an untrusted host.
* With `uvicorn --workers N` (the compose default is 2) set `PROMETHEUS_MULTIPROC_DIR` to an
  empty per-container directory. The cloud compose profile mounts a tmpfs of its own for it,
  `/run/homayar-metrics` (32 MB), separate from `/tmp` where uploads are spooled, so a full
  spool cannot stop the counters and the counters cannot fill the spool. Every worker writes
  its counters there and each scrape sums all workers, whichever worker answers it. Sizing:
  each worker process keeps one counter file and one histogram file (sparse, 1 MiB each when
  created, a few KiB actually used); files of a worker that exited stay until the container
  restarts, which is correct for counters. The directory must be empty when the container
  starts (tmpfs or a fresh container filesystem). In this mode the per-process `process_*`
  series are not exported. Without the variable a single process serves its own counters.
* A metric write that fails (full or missing directory) never fails the request it counts:
  it is logged as `METRICS_WRITE_FAILED error_type=…` at most once a minute.
* On-premise (`compose.onprem.yaml`) passes the same `METRICS_*` settings (plus the
  on-premise-only `METRICS_ALLOW_UNAUTHENTICATED`) and the same dedicated
  `/run/homayar-metrics` tmpfs. The opt-in `monitoring` profile adds `node-exporter`,
  which reads the backup freshness gauge from `NODE_EXPORTER_TEXTFILE_DIR` (written by
  `backup-tool`, kept outside the 0700 backup root).
* Access (`backend.observability.metrics.metrics_access`):

  | Setting | development / test | staging / production |
  | --- | --- | --- |
  | `METRICS_BEARER_TOKEN` set | bearer token required | bearer token required |
  | no token | open | **404** and `METRICS_ENDPOINT_DISABLED` at startup |
  | no token, `METRICS_ALLOW_UNAUTHENTICATED=true` | open | on-premise: open, logged at startup as `METRICS_UNAUTHENTICATED_OPT_OUT`; cloud: refused (startup fails, as for the HOM-161 opt-outs) |
  | `METRICS_ENABLED=false` | 404 | 404 |

  The token is compared in constant time (surrounding whitespace ignored), must be at least
  32 characters and may come from `METRICS_BEARER_TOKEN_FILE`. Startup does not fail without
  a token: the endpoint refuses instead, so an existing production configuration keeps
  serving traffic. The cloud compose profile passes `METRICS_ENABLED`,
  `METRICS_BEARER_TOKEN` and `METRICS_CACHE_SECONDS` to the API.
* Scrape config sketch (Prometheus): `metrics_path: /metrics`, `authorization: {type: Bearer,
  credentials_file: /run/secrets/homayar_metrics_token}`, target `api:8000`, interval 30 s.

### Series

In-process (this API process):

| Series | Labels |
| --- | --- |
| `homayar_http_requests_total` | `method`, `route` (path template, `unmatched` for 404s outside the routes), `status_class` |
| `homayar_http_request_duration_seconds` (histogram) | `method`, `route` |
| `homayar_metrics_collection_errors_total` | `source` (a source that raised or missed its deadline; every source is exported at 0 from start-up, so `increase()` sees the first failure) |
| `homayar_auth_failures_total` | `reason`: `invalid_credentials`, `invalid_refresh`, `refresh_replay`, `csrf_failed`, `other` (HOM-114; every reason exported at 0 from start-up) |
| `homayar_audit_write_failures_total` | `delivery`: `transactional` (the action was rolled back), `best_effort` (the action stood, the event was lost), `other` (HOM-114; exported at 0 from start-up) |
| `process_*`, `python_info` | standard client collectors (single-process mode only) |

Scrape-time state (cached `METRICS_CACHE_SECONDS`, default 15 s). Every source runs in its
own thread with a 3 s deadline on a dedicated small database engine (connect timeout 2 s,
statement timeout 2 s); the object-store probe uses 2 s connect/read timeouts and a single
attempt. A source that misses its deadline counts in
`homayar_metrics_collection_errors_total{source}`, a dependency probe then reports the
dependency as down, and the hung source is not started again until it returns. A scrape
therefore waits at most about 6 s, and concurrent scrapes wait on one dedicated thread
instead of the API's request threadpool.

| Series | Labels | Source |
| --- | --- | --- |
| `homayar_dependency_up`, `homayar_dependency_probe_seconds` | `dependency` = `database`, `redis`, `storage` | `SELECT 1`; Redis `PING`; object-store `HEAD` of a fixed absent key |
| `homayar_schema_up_to_date` | – | `alembic_version` vs the code's migration head (-1 when the database has no stamp) |
| `homayar_build_info` (=1) | `version`, `source_sha`, `profile`, `migration_head`, `python_version`, `uv_lock_sha256` | image `build-info.json`, written at build time ([release.md](release.md)); `source_sha="unreleased"` for an image built without release arguments; outside an image (no `build-info.json`) `source_sha`, `profile` and `uv_lock_sha256` read `unknown` |
| `homayar_jobs` | `state` | `processing_jobs` |
| `homayar_jobs_queued_due`, `homayar_jobs_oldest_queued_due_age_seconds` | – | durable queue depth and latency |
| `homayar_jobs_retry_scheduled`, `homayar_jobs_running_lease_expired` | – | retries waiting, attempts whose worker vanished |
| `homayar_jobs_finished_15m` | `outcome` | terminal jobs of the last 15 min |
| `homayar_jobs_failed_15m` | `outcome`, `category` (`provider`, `storage`, `media`, `timeout`, `broker`, `worker_lost`, `deadline`, `cancelled`, `other`) | failed / dead-lettered jobs |
| `homayar_jobs_failed_by_class_15m` | `outcome`, `failure_class` | idem |
| `homayar_jobs_retried_15m`, `homayar_workspaces_with_failed_jobs_15m` | – | jobs needing more than one attempt; tenant impact as a count only |
| `homayar_job_turnaround_seconds_15m`, `homayar_job_processing_seconds_15m`, `homayar_job_queue_wait_seconds_15m` | `quantile` (0.5, 0.95, 1) | completed jobs of the last 15 min (at most 5000) |
| `homayar_dispatch_outbox` | `state` | `job_dispatches` |
| `homayar_dispatch_oldest_due_age_seconds` | none (age of the oldest due, unpublished row; 0 when none is due) | `job_dispatches` |
| `homayar_provider_endpoints`, `homayar_provider_circuits_open`, `homayar_provider_endpoints_failing`, `homayar_provider_endpoints_failed_15m`, `homayar_provider_consecutive_failures_max` | `stage` (`asr`, `diarization`, `intelligence`, `tts`, `other`) | `provider_health` |
| `homayar_broker_queue_length` | `queue` | Redis `LLEN processing` |
| `homayar_periodic_task_last_success_timestamp_seconds`, `..._last_failure_...`, `..._last_run_...`, `..._last_duration_seconds` | `task` | sweep heartbeats |
| `homayar_periodic_task_result` | `task`, `field` | the last run's outcome counters (for example `retention_purge`/`purges_failed`) |
| `homayar_periodic_task_interval_seconds` | `task` | configured beat interval |

### What never becomes a label or a value

No tenant, user, meeting, job or API key id; no provider endpoint or model name; no
`failure_code` (open-ended, mapped to `category`); no free text; no transcript; no
credential. Tenant impact is the count `homayar_workspaces_with_failed_jobs_15m`; the
per-workspace view stays behind the tenant-scoped admin API. Cross-tenant reads use the
existing read-only service scopes (`recovery` on `processing_jobs`, `dispatch` on
`job_dispatches`) and select aggregates only.

## Sweep heartbeats

After every run of a periodic task the worker writes one Redis hash
`homayar:obs:sweep:<task>` (7-day TTL): `last_run`, `last_success` or `last_failure`
with `last_error_type` (class name only), `last_duration_seconds` and the run's integer
counters. Tasks: `dispatch_outbox`, `lease_recovery`, `usage_reconciliation`,
`artifact_cleanup`, `media_reconciliation`, `retention_purge`, `voice_recovery`.

A stale `last_success` means beat stopped, no worker consumes the `processing` queue, or
the sweep keeps failing. Check in this order: `celery -A backend.celery_app inspect ping`,
the scheduler container, then `last_error_type` and the worker logs for `sweep:<task>:`.
A heartbeat that cannot be written never fails the sweep (`SWEEP_HEARTBEAT_UNAVAILABLE`
warning; the Redis-down alert covers it).

## Logs

JSON lines on stdout with `correlation_id` (also on the `UNHANDLED_ERROR` line of a 500).
The message passes through two scrubbers, and is cut at 2000 characters only afterwards
(`backend/observability/scrub.py`): credentials (`Authorization`/`Cookie` header values,
bearer and basic credentials, JWTs, Homayar and provider API keys, `password=`/`token=`-style
pairs in `key=value`, JSON and Python-repr spelling, every secret setting name of
`config.SECRET_FIELDS`, URL passwords and DSN users), then personal data (phone, e-mail,
national ID, bank card, IBAN, postal code become `[PHONE_1]`-style placeholders). A database
exception is logged by its classes, SQLSTATE and constraint/table/column only, never by its
text (which quotes the statement, bound parameters and psycopg's `DETAIL` row values); other
exception texts pass through the same scrubbers. `UNHANDLED_ERROR` names the route template,
never the raw path (share and invitation tokens live in paths). No traceback is written.
uvicorn writes its own line for an exception that escaped the app (`Exception in ASGI
application`, logger `uvicorn.error`, uvicorn's plain-text handler); a filter on uvicorn's
loggers scrubs that record the same way, replaces the traceback with the exception summary
and adds `request_id=`, so the line can be matched to the `UNHANDLED_ERROR` record.
This is defence in depth: code still must not log headers, bodies, transcripts or settings.
Known gap outside the application: uvicorn's access log and the proxy's access log print
raw request paths (follow-up recorded in the PR).
