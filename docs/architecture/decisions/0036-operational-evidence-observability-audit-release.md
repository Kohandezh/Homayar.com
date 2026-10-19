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
   scrubbing. An exception is logged by its class and bounded fields, never its text, unless its
   type is on a short allow-list; uvicorn's access log names the route template, never the raw
   path; Celery workers keep the same JSON handler (no task-failure tracebacks). It is defence
   in depth, not permission to log sensitive data.

### Consequences

* Operators can find a failed job by `job:<id>` in logs and audit rows, and see queue depth and
  latency, outcomes, retries, durations, provider circuits, dependency health, sweep liveness and
  the schema revision in one scrape, with the release identity from the image's
  `build-info.json` (`homayar_build_info`; `source_sha="unreleased"` for an image built without
  release arguments, decision 5).
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

Review update (PR #55): every string detail value is masked by default and only id, enum,
digest, count and timestamp keys are exempt; nested values are sanitised recursively; a
refresh-token replay commits its revocation before the best-effort event; each failed
audit insert is counted once under its delivery.

## Decision 4 — Alerting (HOM-126)

1. **Rules live in the repository, tested.** `infra/observability/prometheus/homayar.rules.yml`
   holds the recording and alerting rules; `tests/homayar.rules.test.yml` gives every alert a
   promtool case where it fires on its failure and one where it stays silent in a normal state,
   with inputs shaped like the real exposition (a labelled child appears at its first value,
   `homayar_jobs_failed_15m` only while failed jobs exist, a vanished series is marked stale).
   `prometheus.example.yml` names the two scrape jobs the rules depend on (`homayar-api`,
   `homayar-node`). The *Container configuration* CI job runs `promtool check rules`,
   `promtool test rules` and `promtool check config --syntax-only` (`prom/prometheus:v3.5.0`,
   read-only mount, no network). `backend/tests/test_alert_rules.py` ties the rules to the code:
   every series a rule queries has a sample in a real `/metrics` scrape (HELP/TYPE lines alone do
   not count), or is a recording rule, the backup contract below or a Prometheus/node_exporter
   series; every label value a rule matches is one the code emits; the job names are the example
   configuration's; its `scrape_timeout` exceeds two source deadlines; and every alert has
   `severity` `warning` or `critical`, a `summary` and a `runbook` anchor that is a section of
   [alerts.md](../../operations/alerts.md).
2. **Only alerts with a producer.** An alert ships with the code that emits its series. The
   audit-write, failed-login, refresh-replay and audit-sink alerts move to HOM-114 and HOM-115,
   which add their emitters. A counter an alert reads with `increase()` or `rate()` exists at 0
   before its first event (the collection-error counters are created at start-up), because
   Prometheus does not treat a new series as starting from 0 and would miss that event.
3. **Aggregation.** Every API replica reports the same scrape-time state, so rules aggregate it
   with `max()` (`min()` for ages and the schema check) instead of `sum()`; a promtool case with
   two replicas proves nothing is counted twice. In-process counters (HTTP requests, collection
   errors) are per API container and are summed. That holds only when the container's uvicorn
   workers share `PROMETHEUS_MULTIPROC_DIR` (Decision 1). Otherwise each scrape reaches another
   worker and every drop looks like a reset. `homayar:api_counter_resets_15m:max` detects that
   (more than two resets in 15 minutes); the rate-based alerts leave such a target out and
   `HomayarApiCountersInconsistent` reports it.
4. **Sweeps: warn early, page when nothing explains it.** Staleness is
   `clamp_min(3 × interval, 240)` plus one minute: the 30-second dispatch sweep warns within
   about 5 minutes of its last success (WP20 F78), a 15-minute sweep after about 46 minutes.
   Sweeps share the `processing` queue with meeting jobs and expire after one interval, so busy
   workers delay them; that is a warning. `HomayarPeriodicSweepStopped` pages only when the
   staleness persists while no job runs on a live worker, or exceeds 2 h 15 min (longer than one
   job attempt can hold a slot), and never while due jobs wait or PostgreSQL is down. Those
   causes have their own alerts. There is no "failed twice in a row" alert: the heartbeat keeps
   only the latest run, and the timestamps cannot tell two failures from one failure after a
   skipped run. A sweep that keeps failing is stale after three intervals.
5. **Backup contract with Lane C.** The backup tooling writes
   `homayar_backup_last_success_timestamp_seconds{target="postgres"|"objects"|"offsite"}` (Unix
   seconds of the last successful backup of that target) to a node_exporter textfile. A target
   pages critical when its timestamp is older than 26 hours (RPO 24 hours plus 2 hours' margin;
   a promtool case pins the boundary) or when it is missing for an hour while node_exporter
   reads the file. An exporter that is down or cannot read the file is its own critical alert,
   so "freshness unknown" and "never ran" are told apart.
6. **Severity.** `critical` means data or the whole service is at risk now, or a safety signal
   is blind: database, Redis, object storage, the API scrape or its target, sweeps stopped, a
   stalled queue, missing media, failed purges, the tombstone journal behind, backups too old,
   missing or unobservable. `warning` means degraded or needing attention within hours.

### Not covered

* Alertmanager routing, receivers, inhibition and silences are per installation; the rules carry
  `severity` only. The expressions themselves keep one cause to one page where the code allows
  it: the sweep alerts are silent while PostgreSQL is down (and cannot be evaluated while Redis
  is), and the collection-error alert while a dependency is down.
* Workers have no scrape endpoint: a live but stuck worker shows only as queue latency, expired
  leases and stale sweeps.
* `HomayarMissingMediaObjects` counts the objects a reconciliation run newly flagged and resolves
  at the next run; no series reports the rows currently flagged missing.
* The retention, media and artifact sweeps visit at most their batch size of workspaces per run,
  always the same first ones by id, so their alerts cannot see the others (the runbook says how
  to size the batches until the sweeps page through all workspaces).
* A route's 5xx counter appears at its first value, so `rate()` misses the first 5xx of each
  route and container; negligible for a ratio over 10 minutes at 0.2 requests/s or more.
* No latency or SLO burn-rate alerts (no SLO is agreed yet), no host, disk, certificate,
  PostgreSQL or Redis internals (node_exporter and database exporters bring their own rules).
* Sweep ages compare worker timestamps with Prometheus time: clock skew beyond a minute
  distorts them (hosts run NTP).

## Decision 5 — Reproducible build and release manifest (HOM-28, HOM-29)

1. **Every input of a release comes from Git objects at one commit.**
   `infra/release/release_manifest.py generate --rev <commit>` reads files with `git cat-file`
   and `git ls-tree` (ignoring `GIT_*` variables and replace refs), never the working tree, and
   prints canonical JSON without wall-clock time (the build time is the commit time unless
   given). Untracked files, dirty edits, `.env` files and environment variables cannot change
   it; generating twice gives the same bytes. The tool is standard-library Python so it runs on
   any release host without the backend environment.
2. **The manifest is a description, not a policy engine.** It records the source commit, tree
   and commit time; application version; Python, base image and uv version; digests of
   `uv.lock`, `pyproject.toml`, `backend/requirements.txt` and the console and website locks;
   the single migration head and the revision count (parsed from the revision literals; several
   heads or a broken chain are refused); Dockerfile and `.dockerignore` digests and base images
   of every image; the digest of the code the API image ships; the profile; image digests and
   validation links passed in; `requires-python`; and what is and is not pinned.
   `manifest_sha256` covers the canonical body: an integrity checksum, not proof of authorship
   (it is unkeyed; manifests and images are not signed and carry no provenance attestation).
   Validation links with credentials, a query or a fragment are refused; no environment variable, `.env` content or Git identity is recorded.
3. **The image records what it was built from, and `verify` checks that it matches.** Build arguments `SOURCE_SHA`,
   `SOURCE_DATE_EPOCH`, `SOURCE_DATE`, `RELEASE_VERSION` and `RELEASE_PROFILE` (which must agree
   with `INSTALL_AI_LOCAL`) come from `release_manifest.py build-args`. The image writes
   `/app/build-info.json` from its own files (lock and pyproject copies in
   `/app/release-inputs/`, the installed distributions, the migration scripts, the shipped code
   files, the base image reference and uv version it was built with) and carries OCI labels.
   `release_manifest.py verify` compares a manifest with that file and the labels and lists
   every mismatch. Hashing the shipped code closes the gap of a dirty build context with a real
   `SOURCE_SHA`.
4. **Unreleased builds stay possible and visible.** Without release arguments (developers, the
   CI smoke build, `docker compose build`) the image carries the all-zero `SOURCE_SHA`;
   `homayar_build_info` reports `source_sha="unreleased"` and `verify` fails. We chose a
   visible marker over refusing such builds so that local development and the existing
   container tests keep working unchanged.
5. **Locked, not just frozen.** The API image and the backend CI job run `uv sync --locked`,
   which refuses a `uv.lock` that is stale against `pyproject.toml`. The API base image is
   pinned by the digest of its multi-platform index; re-pinning is a reviewed change.
6. **Input-reproducible, not bit-for-bit.** Debian packages installed with `apt-get`, file
   timestamps and layer digests are not pinned, so two builds of one commit produce the same
   `build-info.json` and Python environment but not the same image digest. The manifest says so
   (`reproducibility`); the artifact identity of a release is the pushed image digest recorded
   in its manifest.

### Consequences

* An operator reads `homayar_build_info` (or `python -m backend.observability.release show`)
  and the release manifest to answer which commit, lock and schema head a running backend came
  from; `verify` turns that into a pass/fail check.
* CI job `release-manifest` regenerates and byte-compares the manifest, builds the API image
  with release arguments, verifies it, checks `/metrics` of the running image and keeps the
  manifest as a 90-day artifact. It adds one API image build per run.
* `backend/requirements.txt` is now checked against `uv.lock` by a test, so the two cannot
  drift.
* The on-prem Compose template still passes the base image tag as a build argument; Compose
  builds are unreleased and `verify` reports the base-image difference. Pointing its default at
  the digest is an infrastructure change outside this decision.
* Making the image bit-for-bit reproducible (snapshot Debian archive, `rewrite-timestamp`,
  hashed uv install) is possible later without changing the manifest format.
