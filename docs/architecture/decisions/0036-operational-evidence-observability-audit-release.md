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
   `manifest_sha256` covers the canonical body. Validation links with credentials, a query or a
   fragment are refused; no environment variable, `.env` content or Git identity is recorded.
3. **The image proves what it was built from.** Build arguments `SOURCE_SHA`,
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
