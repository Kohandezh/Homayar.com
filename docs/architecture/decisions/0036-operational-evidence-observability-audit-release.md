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
   (`PROMETHEUS_MULTIPROC_DIR`, an empty tmpfs of its own per container, apart from the upload
   spool in `/tmp`), so every scrape sums all workers instead of reading whichever worker
   answered. A failed metric write is logged (at most once a minute) and never fails the
   request it counts.
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
   uvicorn's own record of an exception that escaped the app goes through the same scrubbing,
   without a traceback, and carries the request id. Text is cut to its length limit only after
   scrubbing. It is defence in depth, not permission to log sensitive data.

### Consequences

* Operators can find a failed job by `job:<id>` in logs and audit rows, and see queue depth and
  latency, outcomes, retries, durations, provider circuits, dependency health, sweep liveness and
  the schema revision in one scrape; the release identity joins them once the HOM-28 image
  build writes `build-info.json` (until then `homayar_build_info` reads `unknown`).
* A scrape costs about ten aggregate queries every `METRICS_CACHE_SECONDS`; the 15-minute windows
  read `completed_at`, which has no dedicated index (acceptable at current volumes; an index is a
  migration for later if scrapes get slow).
* The `recovery` scope now has a second, read-only consumer; its policy is unchanged.

## Decision 2 — Audit event model (HOM-114)

1. **One catalogue.** `AUDIT_EVENT_CATALOG` in `audit_service.py` lists every event type the
   application records, in seven categories (security, access, privacy_access, configuration,
   administration, deletion, processing). A test scans the backend for every literal event type
   and fails when one is missing from the catalogue or the catalogue lists one nobody records.
2. **Gaps closed.** `auth.login` (success, and failure without the raw identifier),
   `auth.logout`, `auth.refresh_replay_detected` (the session is revoked; the row commits with
   the revocation) and `model_manifest.registered` (first registration of a manifest document,
   a platform configuration change).
3. **Actor, tenant, action, target, time, result, correlation** on every row; the system actor
   is `NULL` and is named by the correlation id (`job:<id>`, `sweep:<task>:<run>`).
4. **Write-time sanitisation.** Secret keys and credential-shaped values become
   `[redacted]` before the row is written (the admin API's read-time sanitiser stays as a
   second layer).
5. **Defined failure behaviour.** Audit rows of operations that happened are transactional
   (fail closed); refused attempts are recorded best effort in their own commit (the response
   does not change). Both failure kinds are counted for alerting.
6. **Free-text masking (owner decision 2026-10-17, option B).** Reasons, an API key's name and
   integration identifiers go through the text PII detector before they are written (class
   placeholders), then are capped at 120 characters. This keeps personal data out of an
   append-only table instead of having to erase it later.
