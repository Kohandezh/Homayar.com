# Observability: metrics, correlation ids and heartbeats

Scope: HOM-113 (WP20-T01). Decision record: [ADR 0036](../architecture/decisions/0036-operational-evidence-observability-audit-release.md).

## Correlation ids

Every unit of work has one correlation id:

| Work | Correlation id | Where it appears |
| --- | --- | --- |
| HTTP request | the client's `X-Request-ID` if it matches `^[A-Za-z0-9._:-]{8,80}$`, else a fresh UUID | `X-Request-ID` response header, error bodies (`error.request_id`), JSON logs (`correlation_id`), `audit_events.request_id` |
| Processing attempt | `job:<job id>` | worker JSON logs, audit events written by the attempt (`meeting.processing.*`) |
| Periodic sweep run | `sweep:<task>:<12 hex>` | worker JSON logs, audit events written by the sweep |

The id is bound in a context variable (`backend/observability/correlation.py`), so code
that records an audit event without a request id still stores the right one.

Find a failed job: take the job id from `GET /api/v1/admin/jobs` (workspace admin) or the
triage SQL in [health-and-recovery.md](health-and-recovery.md), then search the worker
logs and `audit_events.request_id` for `job:<id>`.

## The `/metrics` endpoint

* Served by the API process on its own port (`http://api:8000/metrics`). The nginx proxy
  does not route it (`location /` goes to the public website; a test asserts this).
* Access (`backend.observability.metrics.metrics_access`):

  | Setting | development / test | staging / production |
  | --- | --- | --- |
  | `METRICS_BEARER_TOKEN` set | bearer token required | bearer token required |
  | no token | open | **404** and `METRICS_ENDPOINT_DISABLED` at startup |
  | no token, `METRICS_ALLOW_UNAUTHENTICATED=true` | open | open (private scrape network only) |
  | `METRICS_ENABLED=false` | 404 | 404 |

  The token is compared in constant time, must be at least 32 characters and may come from
  `METRICS_BEARER_TOKEN_FILE`. Startup does not fail without a token: the endpoint refuses
  instead, so an existing production configuration keeps serving traffic.
* Scrape config sketch (Prometheus): `metrics_path: /metrics`, `authorization: {type: Bearer,
  credentials_file: /run/secrets/homayar_metrics_token}`, target `api:8000`, interval 30 s.

### Series

In-process (this API process):

| Series | Labels |
| --- | --- |
| `homayar_http_requests_total` | `method`, `route` (path template, `unmatched` for 404s outside the routes), `status_class` |
| `homayar_http_request_duration_seconds` (histogram) | `method`, `route` |
| `homayar_auth_failures_total` | `reason` (`invalid_credentials`, `invalid_refresh`, `refresh_replay`, `csrf_failed`) |
| `homayar_audit_write_failures_total` | `delivery` (`transactional`, `best_effort`) |
| `homayar_metrics_collection_errors_total` | `source` |
| `process_*`, `python_info` | standard client collectors |

Scrape-time state (cached `METRICS_CACHE_SECONDS`, default 15 s; every query has a 2 s
statement timeout):

| Series | Labels | Source |
| --- | --- | --- |
| `homayar_dependency_up`, `homayar_dependency_probe_seconds` | `dependency` = `database`, `redis`, `storage` | `SELECT 1`; Redis `PING`; object-store `HEAD` of a fixed absent key |
| `homayar_schema_up_to_date` | – | `alembic_version` vs the code's migration head (-1 when the database has no stamp) |
| `homayar_build_info` (=1) | `version`, `source_sha`, `profile`, `migration_head`, `python_version`, `uv_lock_sha256` | image `build-info.json` |
| `homayar_jobs` | `state` | `processing_jobs` |
| `homayar_jobs_queued_due`, `homayar_jobs_oldest_queued_due_age_seconds` | – | durable queue depth and latency |
| `homayar_jobs_retry_scheduled`, `homayar_jobs_running_lease_expired` | – | retries waiting, attempts whose worker vanished |
| `homayar_jobs_finished_15m` | `outcome` | terminal jobs of the last 15 min |
| `homayar_jobs_failed_15m` | `outcome`, `category` (`provider`, `storage`, `media`, `timeout`, `broker`, `worker_lost`, `deadline`, `cancelled`, `other`) | failed / dead-lettered jobs |
| `homayar_jobs_failed_by_class_15m` | `outcome`, `failure_class` | idem |
| `homayar_jobs_retried_15m`, `homayar_workspaces_with_failed_jobs_15m` | – | jobs needing more than one attempt; tenant impact as a count only |
| `homayar_job_turnaround_seconds_15m`, `homayar_job_processing_seconds_15m`, `homayar_job_queue_wait_seconds_15m` | `quantile` (0.5, 0.95, 1) | completed jobs of the last 15 min (at most 5000) |
| `homayar_dispatch_outbox`, `homayar_dispatch_oldest_due_age_seconds` | `state` | `job_dispatches` |
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

JSON lines on stdout with `correlation_id`. The message and exception text pass through
`backend.observability.scrub.scrub_text`, which replaces credential-shaped values
(`Authorization`/`Cookie` header values, bearer and basic credentials, JWTs, Homayar and
provider API keys, `password=`/`token=`/`secret=` values, the password of a
`scheme://user:password@host` URL). This is defence in depth: code still must not log
headers, bodies, transcripts or settings.
