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

## Decision 3 — Append-only audit trail and protected sink (HOM-115)

Owner decision 2026-10-17, option B: `audit_events` is append-only for the application;
personal data in free text is masked at write time (Decision 2.6); a historical row may
be erased only through an owner-approved DBA procedure run as the table owner, and the
sink verifier reports it as `accepted_erasure`.

1. **Two layers, one migration.** The ORM refuses to flush a changed or deleted
   `AuditEvent` and refuses ORM bulk `UPDATE`/`DELETE` (covers SQLite, which has no
   roles); on PostgreSQL `homayar_app` loses `UPDATE` and `DELETE` on `audit_events`
   (`APPEND_ONLY_TABLES`, migration `e6c3a9f1d4b8`, down revision `d8a2f4c6b1e3`). The
   foreign keys keep `ON DELETE SET NULL`: deleting a workspace or a user must keep
   working, and the referential action runs as the table owner. We rejected
   `ON DELETE RESTRICT` (it would block account deletion) and a trigger that refuses
   updates (the owner can drop a trigger as easily as change a row; it adds nothing the
   privilege does not already give against the application).
2. **A reader role, not a service scope.** The sink reads through `homayar_audit_reader`
   (`NOLOGIN`, `SELECT` on `audit_events` only, policy `homayar_audit_export FOR SELECT
   TO homayar_audit_reader USING (true)`). A service scope is a setting any session can
   set, so a scope that unlocked every tenant's audit rows would be reachable from any
   request handler; a role membership is not. `homayar_app` and the application's login
   are never members; the exporter logs in with its own login that is
   (`AUDIT_EXPORT_DATABASE_URL`). The tenant policy is unchanged.
3. **Tamper-evident, not tamper-proof.** The owner and a superuser can still change
   rows. The append-only rule removes the application's ability to rewrite history;
   detecting a change made by anyone else is the protected sink's job. No document may
   describe either layer as immutable.
4. **A hash-chained sink outside the application's reach.** An operator job
   (`python -m backend.observability.audit_sink export`) appends settled rows
   (`AUDIT_EXPORT_SETTLE_SECONDS`) to write-once JSON-lines segments in `AUDIT_SINK_DIR`,
   each record chained with HMAC-SHA256 under `AUDIT_SINK_HMAC_KEY` (required outside
   test and development; held by the operator, not by the application). `verify` checks
   the chain and compares every exported row with the database: `record_altered`,
   `sequence_gap`, `row_changed`, `row_deleted` and `unexported_row` are failures.
   We chose files the operator controls over a second database table (which the same
   owner could rewrite) and over an external log service (a new dependency for both
   deployment profiles); write-once storage for the directory is the deployment's choice.
5. **Expected changes are notices, never hidden.** A `NULL` reference left by a deleted
   workspace or user is `referential_null`. An owner-approved erasure is recorded *in the
   chain* (`record-erasure --event-id --approval`, an approval reference and no free text)
   before the DBA runs it as the table owner, and every later `verify` reports it as
   `accepted_erasure`. Recording it in the chain, not in a side file, means a writer of
   the sink without the key cannot forge an erasure to hide a deletion. The exported copy
   stays in its segment; it holds only the masked row.
6. **The head is anchored outside the sink.** Cutting the newest segments together with
   `HEAD.json` is invisible to the sink alone, so every export and verify prints
   `head_hash` and `last_seq`, and the heartbeat keeps `last_seq`; the runbook asks the
   operator to keep that copy. Sink segments are kept for as long as the rows (no audit
   retention purge exists); pruning needs a checkpoint procedure that is not built.
7. **The first export is gated on a legacy-row review**, decided (owner, 2026-10-18).
   Rows written before HOM-114 masking may hold unmasked free text, and an
   exported copy cannot be removed from a write-once segment. In staging and production
   the first export into an empty sink therefore refuses to run until it is given the
   approval reference of that review (`export --legacy-review <ref>`); the reference is
   record 1 of the chain (HMAC-covered, so a writer of the sink without the key cannot
   forge it), copied into the first manifest and `HEAD.json`, and reported by every
   `verify`. A recording into an empty sink by `record-erasure` is refused there for the
   same reason. We rejected a boolean setting: it records neither who acknowledged the
   review nor when. Test and development are not gated.

### Consequences

* An application bug or a compromised request handler cannot rewrite or erase audit
  history; it can still insert rows (which is how auditing works).
* Deleting a user or a workspace leaves its audit rows with a `NULL` reference.
* Granting the reader role to a login is a privileged act: it reads every tenant's
  audit rows. Only the exporter's login holds it.
* The operator runs and schedules export and verify (heartbeats `audit_export` /
  `audit_verify`); a change by the table owner is detected at the next `verify`, not
  prevented, and a change made before a row's first export is not detected.
* `verify` is linear in the number of events; acceptable at current volumes.
