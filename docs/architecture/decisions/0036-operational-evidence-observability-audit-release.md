# ADR 0036 — Operational evidence: observability, protected audit and release identity

Date: 2026-10-17 · Status: proposed (Lane D: HOM-113, HOM-114, HOM-115, HOM-126, HOM-28, HOM-29) ·
Packages: WP20 (F09, F78), WP01 (F01) · Builds on: ADR 0026, ADR 0030, ADR 0033

## Context

Before Infrastructure Freeze the backend must be observable, auditable, diagnosable and
reproducibly buildable from an exact source revision. The starting point: JSON logs, `/healthz`
and `/readyz`, durable job and outbox state in PostgreSQL, a `provider_health` table, sweeps that
return outcome counters, an `audit_events` table written by about 80 call sites (41 of them without
a request id), and `prometheus-client` locked but unused.

This ADR is extended section by section as each Lane D change lands.

## Decision 1 — Metrics and correlation ids (HOM-113)

1. **One correlation id per unit of work**, held in a context variable: the validated
   `X-Request-ID` (or a UUID) per request, `job:<id>:<delivery>` per processing attempt
   (`job:<id>` is the searchable prefix), `sweep:<task>:<run>` per periodic sweep run. `record_audit_event` stores it when the caller
   passes no request id; every JSON log line carries it.
2. **`/metrics` on the API port, never through the proxy.** Bearer token when configured; in
   staging and production without a token the endpoint *refuses* (404, startup warning
   `METRICS_ENDPOINT_DISABLED`). We chose refusal over a startup failure so that enabling the
   feature cannot take down an existing production configuration; a missing scrape target is
   itself visible to Prometheus. The unauthenticated opt-out `METRICS_ALLOW_UNAUTHENTICATED`
   follows the HOM-161 rule: refused with `DEPLOYMENT_MODE=cloud`, logged by name at startup
   on-premise. `/metrics` alone skips the trusted-host check (scraped as `api:8000`).
3. **Workers need no scrape endpoint.** Everything a worker decides is durable (jobs, outbox,
   provider health) or reported as a sweep heartbeat in Redis. The API reads that state at scrape
   time, cached for `METRICS_CACHE_SECONDS`. Each source has a deadline on a dedicated small
   engine with connect and statement timeouts; a hung source is reported failed and not
   restarted while it hangs, so a black-holed dependency bounds a scrape (about 6 s at most)
   and never takes the API's request threadpool (rendering runs on a one-thread limiter).
   The API's own counters use Prometheus multiprocess mode under `uvicorn --workers`
   (`PROMETHEUS_MULTIPROC_DIR`, an empty tmpfs directory per container), so every scrape sums
   all workers instead of reading whichever worker answered.
4. **Bounded labels only.** States, failure classes, a fixed failure *category* derived from the
   open-ended `failure_code`, provider *stage*, task names, dependency names, route *templates*
   and status classes. Never a tenant, user, meeting, job or key id, a provider endpoint or model,
   free text or content. Tenant impact is a count of affected workspaces; the per-workspace view
   stays behind the tenant-scoped admin API.
5. **Cross-tenant reads reuse reviewed scopes.** Aggregates over `processing_jobs` use the
   read-only `recovery` scope and over `job_dispatches` the `dispatch` scope; `provider_health` is
   platform state. No new row-level-security policy was needed for metrics.
6. **Diagnostics are scrubbed.** Log messages and exception texts are bounded and pass through a
   credential scrubber and the text PII detector; database exceptions are logged by class,
   SQLSTATE and constraint only; the 500 log line names the route template, not the raw path.
   It is defence in depth, not permission to log sensitive data.

### Consequences

* Operators can find a failed job by `job:<id>` in logs and audit rows, and see queue depth and
  latency, outcomes, retries, durations, provider circuits, dependency health, sweep liveness and
  the running release and schema revision in one scrape.
* A scrape costs about ten aggregate queries every `METRICS_CACHE_SECONDS`; the 15-minute windows
  read `completed_at`, which has no dedicated index (acceptable at current volumes; an index is a
  migration for later if scrapes get slow).
* The `recovery` scope now has a second, read-only consumer; its policy is unchanged.

## Decision 4 — Alerting (HOM-126)

1. **Rules live in the repository, tested.** `infra/observability/prometheus/homayar.rules.yml`
   holds the recording and alerting rules; `tests/homayar.rules.test.yml` gives every alert a
   promtool case where it fires on its failure and one where it stays silent in a normal state.
   The *Container configuration* CI job runs `promtool check rules` and `promtool test rules`
   (`prom/prometheus:v3.5.0`, read-only mount, no network). `backend/tests/test_alert_rules.py`
   ties the rules to the code: every series a rule queries appears in a real `/metrics` scrape
   (or is a recording rule or the backup contract below), every label value a rule matches is
   one the code emits (task, sweep result field, outcome, category, reason, dependency, stage,
   delivery), and every alert has `severity` `warning` or `critical`, a `summary` and a
   `runbook` anchor that is a section of [alerts.md](../../operations/alerts.md).
2. **`max()` across replicas for database-derived state.** Every API replica reports the same
   scrape-time state, so rules aggregate with `max()` (`min()` for ages and the schema check)
   instead of `sum()`; a promtool case with two replicas proves nothing is counted twice. Only
   in-process counters (HTTP, authentication, audit writes, collection errors) are summed.
3. **Sweep staleness = max(3 intervals, 4 minutes).** `homayar:sweep_stale_after_seconds` is
   `clamp_min(3 × interval, 240)` and the alert waits one more minute, so the 30-second dispatch
   sweep alerts within about 5 minutes of its last success (WP20 F78) and a 15-minute sweep after
   about 46 minutes. A *failing* sweep alerts after two failed runs in a row (its latest failure
   more than 1.5 intervals newer than its latest success): one transient failure never pages.
4. **Backup contract with Lane C.** The backup tooling writes
   `homayar_backup_last_success_timestamp_seconds{target="postgres"|"objects"|"offsite"}` (Unix
   seconds of the last successful backup of that target) to the node_exporter textfile
   directory. `HomayarBackupTooOld` fires when a target's timestamp is older than 26 hours (RPO
   24 hours plus 2 hours' margin), `HomayarBackupMetricMissing` when a target reports nothing
   for an hour.
5. **Severity.** `critical` means data, security or the whole service is at risk now (database,
   Redis, object storage or the scrape down, stale sweeps, a stalled queue, missing media, failed
   purges, the tombstone journal behind, audit writes failing, the audit sink not matching,
   backups too old); `warning` means degraded or needing attention within hours.

### Not covered

* Alertmanager routing, receivers, inhibition and silences are per installation; the rules carry
  `severity` only. Inhibiting the sweep and metrics-collection alerts while
  `HomayarRedisUnavailable` fires is recommended.
* Workers have no scrape endpoint: a live but stuck worker shows only as queue latency, expired
  leases and stale sweeps.
* `HomayarMissingMediaObjects` counts the objects a reconciliation run newly flagged and resolves
  at the next run; no series reports the rows currently flagged missing.
* The login-failure, refresh-replay and audit-write alerts are only as complete as the call
  sites that increment their counters; the tests check series and vocabularies, not call sites.
* `HomayarAuditExportStale` needs a first export: an installation that never ran the export has
  no heartbeat and gets no alert.
* No latency or SLO burn-rate alerts (no SLO is agreed yet), no host, disk, certificate,
  PostgreSQL or Redis internals (node_exporter and database exporters bring their own rules).
* Sweep ages compare worker timestamps with Prometheus time: clock skew beyond a minute
  distorts them (hosts run NTP).
