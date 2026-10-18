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
   `X-Request-ID` (or a UUID) per request, `job:<id>` per processing attempt,
   `sweep:<task>:<run>` per periodic sweep run. `record_audit_event` stores it when the caller
   passes no request id; every JSON log line carries it.
2. **`/metrics` on the API port, never through the proxy.** Bearer token when configured; in
   staging and production without a token the endpoint *refuses* (404, startup warning
   `METRICS_ENDPOINT_DISABLED`) unless `METRICS_ALLOW_UNAUTHENTICATED=true` opts out explicitly.
   We chose refusal over a startup failure so that enabling the feature cannot take down an
   existing production configuration; a missing scrape target is itself visible to Prometheus.
3. **Workers need no scrape endpoint.** Everything a worker decides is durable (jobs, outbox,
   provider health) or reported as a sweep heartbeat in Redis. The API reads that state at scrape
   time, cached for `METRICS_CACHE_SECONDS`, each query bounded by a 2 s statement timeout. This
   avoids Prometheus multi-process mode for Celery's prefork pool and gives one consistent view
   whichever API replica is scraped.
4. **Bounded labels only.** States, failure classes, a fixed failure *category* derived from the
   open-ended `failure_code`, provider *stage*, task names, dependency names, route *templates*
   and status classes. Never a tenant, user, meeting, job or key id, a provider endpoint or model,
   free text or content. Tenant impact is a count of affected workspaces; the per-workspace view
   stays behind the tenant-scoped admin API.
5. **Cross-tenant reads reuse reviewed scopes.** Aggregates over `processing_jobs` use the
   read-only `recovery` scope and over `job_dispatches` the `dispatch` scope; `provider_health` is
   platform state. No new row-level-security policy was needed for metrics.
6. **Diagnostics are scrubbed.** Log messages and exception texts pass through a credential
   scrubber (headers, bearer/basic credentials, JWTs, API keys, `password=`-style pairs, URL
   passwords). It is defence in depth, not permission to log sensitive data.

### Consequences

* Operators can find a failed job by `job:<id>` in logs and audit rows, and see queue depth and
  latency, outcomes, retries, durations, provider circuits, dependency health, sweep liveness and
  the running release and schema revision in one scrape.
* A scrape costs about ten aggregate queries every `METRICS_CACHE_SECONDS`; the 15-minute windows
  read `completed_at`, which has no dedicated index (acceptable at current volumes; an index is a
  migration for later if scrapes get slow).
* The `recovery` scope now has a second, read-only consumer; its policy is unchanged.
