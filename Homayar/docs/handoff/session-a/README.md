# Session A — backend and AI-provider foundations (WP08)

Single writer: Session A. Shared records (website backlog/progress, source
lock, CI, Compose/nginx, Graphify) are **not** edited here; the exact changes
they need are listed under "Integration" for the final integration pass.

## Working copy

| Item | Value |
|---|---|
| Product repository | `Kohandezh/HomaYar` (local clone only for this line of work) |
| Session A checkout | `<website worktree>/HomaYar-session-a` (git worktree of the product clone; excluded locally via the website `.git/info/exclude`) |
| Branch | `session-a/wp08-backend` |
| Base | `42543aa` (WP08-T01, on top of `d1cc5d46` WP07-T07) |
| Test database | disposable PostgreSQL 16, `127.0.0.1:54341`, DBs `homayar_sa_test`, `homayar_sa_mig` (scratchpad cluster `pg16sa`) |

## Post-merge state verified on 2026-09-24

- Website `origin/main` = `cfcc2c4` (merge of PR #1, head `b51bc60`). Its
  source lock pins product `d1cc5d464c6e…` on ref `feat/commercial-v1-overlay`.
- Product remote `Kohandezh/HomaYar` has `dev`, `main`, `master` only:
  `feat/commercial-v1-overlay`, `d1cc5d46` and `42543aa` exist **only in the
  local clone**. Nothing of WP03–WP08 product work is on GitHub.
- CI on website `main` (run 36052615211) failed: *Product source contract*
  (`repository … not found` fetching the pinned SHA — commit not pushed and/or
  `PRODUCT_SOURCE_TOKEN` missing or without access), *Public web lint*
  (React-compiler rules, website code — Session B/integration), *Secret scan*
  (Gitleaks `generic-api-key` false positives in
  `docs/handoff/claude-fable-5.1/graphify/source-manifest.json`, file digests
  keyed by paths — integration). Backend/console/container jobs were skipped.
- WP07 on `main`: PARTIAL (T01–T07 validated; F16 accuracy BLOCKED_EXTERNAL).

## Task status

| Task | Status | Evidence |
|---|---|---|
| WP08-T01 manifests + preflight | validated (local) | see log below |
| WP08-T02 stage policies + run snapshots | validated (local) | ADR 0027 draft; migration `d2b6f8a4c1e7`; see log |
| WP08-T03 privacy/eligibility filtering | validated (local) | ADR 0028 draft; `backend/providers/routing.py`; see log |
| WP08-T04 adapter qualification contract tests | validated (local) for the adapters that exist; Padyar adapter BLOCKED_EXTERNAL (API contract not supplied) | ADR 0029 draft; migration `e3a7c5f9b2d4`; see log |
| WP08-T05 deadlines/rate limits/circuits/fallback | validated (local) | ADR 0030 draft; migration `f5b1d7c3a9e2`; see log |
| WP08-T06 egress denial | application layer validated (local); network layer requires deployment integration (below) | ADR 0031 draft; see log |

## Verification log

Commands run from the Session A checkout with the product venv
(`PY=<website worktree>/Homayar/.venv/bin/python`), `-p no:cacheprovider`,
`PYTHONDONTWRITEBYTECODE=1`, one worker. PostgreSQL runs add
`TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:54341/homayar_sa_test
HOMAYAR_REQUIRE_POSTGRES_TESTS=1`.

| Revision | Command | Result |
|---|---|---|
| `42543aa` | `pytest backend/tests/test_model_registry.py` (SQLite) | 17 passed |
| `42543aa` | same on PostgreSQL | 17 passed |
| T01 reconcile | `pytest backend/tests/test_model_registry.py` (SQLite) | 21 passed |
| T02 | `pytest test_ai_policy.py` (SQLite) | 14 passed |
| T02 | `pytest test_job_processor.py test_stage_checkpoints.py test_job_failure_policy.py test_job_leases.py test_privacy_routing.py test_admin_api.py test_authorization_matrix.py test_model_registry.py test_ai_policy.py` (SQLite) | 96 passed, 9 skipped (PostgreSQL-only cases) |
| T02 | `pytest test_ai_policy.py test_model_registry.py test_row_level_security.py test_postgres_guarantees.py test_job_processor.py` (PostgreSQL, app role `homayar_app`) | 50 passed |
| T03 (tree on `5defa71`) | `pytest test_provider_routing.py` (SQLite) | 21 passed |
| T03 (tree on `5defa71`) | full `pytest backend/tests` (SQLite) | 559 passed, 83 skipped (PostgreSQL-only, ffmpeg, live S3 markers), 0 failed, 5m06s |
| T03 (tree on `5defa71`) | `pytest test_provider_routing.py test_ai_policy.py test_model_registry.py test_row_level_security.py test_postgres_guarantees.py test_job_processor.py test_stage_checkpoints.py test_privacy_routing.py test_job_failure_policy.py` (PostgreSQL) | 110 passed |
| T04 (tree on `1e0f5b6`) | `pytest test_provider_contracts.py test_provider_factory.py test_storage_providers.py test_stage_checkpoints.py test_job_processor.py test_provider_routing.py` (SQLite) | 134 passed, 7 skipped (Padyar slots and PostgreSQL-only cases) |
| T04 (tree on `1e0f5b6`) | `pytest test_provider_contracts.py test_stage_checkpoints.py test_job_processor.py test_provider_routing.py` (PostgreSQL) | 101 passed, 3 skipped (Padyar slots) |
| T04 | Alembic on `homayar_sa_mig` to `e3a7c5f9b2d4`: upgrade → check → downgrade -1 → upgrade → check | clean; token columns nullable after upgrade, NOT NULL after downgrade; `USING COALESCE(x, 0)` rewrite verified on a scratch table |
| T05 (tree on `552ccea`) | `pytest test_provider_resilience.py` (SQLite) | 14 passed |
| T05 (tree on `552ccea`) | full `pytest backend/tests` (SQLite) | 627 passed, 86 skipped, 1 failed (`test_truthful_outputs`: `random` is forbidden outside an allowlist) → fixed by drawing jitter through `secrets`; rerun `test_truthful_outputs.py test_provider_resilience.py test_job_failure_policy.py`: 33 passed |
| T05 (tree on `552ccea`) | `pytest test_provider_resilience.py test_provider_routing.py test_provider_contracts.py test_ai_policy.py test_job_processor.py test_job_failure_policy.py test_job_leases.py test_stage_checkpoints.py test_row_level_security.py test_postgres_guarantees.py` (PostgreSQL) | 161 passed, 3 skipped, 1 failed (test helper assumed stage-list order by attempt) → helper fixed; rerun `test_provider_resilience.py test_provider_routing.py`: 35 passed |
| T05 | Alembic on `homayar_sa_mig` to `f5b1d7c3a9e2`: upgrade → check → downgrade -1 → upgrade → check | clean; `provider_health` without RLS, `homayar_app` may update it |
| T06 (tree on `da63f1f`) | `pytest test_egress_denial.py` (SQLite) | 22 passed |
| T06 (tree on `da63f1f`) | full `pytest backend/tests` (SQLite) | 650 passed, 86 skipped, 0 failed, 3m46s |
| T06 (tree on `da63f1f`) | full `pytest backend/tests` (PostgreSQL 16, app role `homayar_app`, forced RLS) | 686 passed, 50 skipped, 0 failed, 7m14s; skip reasons not itemised in this run (expected: Redis transport drills without `TEST_REDIS_URL`, live S3, Padyar slots) |
| T02 | Alembic on `homayar_sa_mig`: `upgrade head` → `check` → `downgrade -1` → `upgrade head` → `check` | clean; `homayar_app` has INSERT but no UPDATE/DELETE on `ai_policy_versions`, `run_policy_snapshots`, `model_manifests`; both new tables FORCE RLS; downgrade restores UPDATE/DELETE on `model_manifests` |

## Completion (2026-09-25)

- **Commit range ready for integration** (branch `session-a/wp08-backend`, on
  top of `feat/commercial-v1-overlay` at `42543aa`): `42543aa..1527eee` plus
  the handoff commit that records this section — `f72e091` T01 reconcile,
  `5defa71` T02, `1e0f5b6` T03, `552ccea` T04, `da63f1f` T05, `1527eee` T06.
  42 files, +5,277/−118 up to `1527eee`.
- **Migrations (in order):** `d2b6f8a4c1e7` AI policies + snapshots,
  `e3a7c5f9b2d4` nullable reported tokens, `f5b1d7c3a9e2` provider health.
  Each round-tripped (upgrade → check → downgrade -1 → upgrade → check) on
  PostgreSQL 16.
- **WP08 status: PARTIAL.** T01–T06 validated locally for the adapters that
  exist. Remaining: the Padyar AI Gateway adapter (BLOCKED_EXTERNAL: API
  contract), network-layer egress denial (deployment integration, item 7
  below), qualification of real models (pinned weights + representative
  evaluation data, WP13), provider credentials. No live provider was called.
- **Suggested shared-record statuses:** WP08-T01…T05 `validated`; WP08-T06
  `partial` (application layer validated, network layer pending
  integration); WP08 `partial`. F20/F21/F22 acceptance: met in local tests
  for the application; F21's network half pending.
- **Next backend package:** WP09 (channel-aware speech and persistent
  inference), starting with WP09-T01 (channel metadata and PBX mapping).

## Integration (for the final integration pass — not done by Session A)

1. **Push the product line.** `feat/commercial-v1-overlay` (up to `42543aa`)
   and then `session-a/wp08-backend` must reach `Kohandezh/HomaYar`; until
   then the website lock points at a commit GitHub does not have. The product
   commits exist only in the local clone inside the website worktree
   (`Homayar/.git`): deleting that worktree deletes them.
2. **CI secret.** Configure `PRODUCT_SOURCE_TOKEN` (read access to
   `Kohandezh/HomaYar`) in `Kohandezh/Homayar.com`.
3. **Source lock.** Bump `infra/product-source.lock` `ref`/`sha` to the
   Session A head recorded at completion; run
   `infra/ci/check-source-contract.sh` locally.
4. **ADR.** Copy `docs/handoff/session-a/adr/0026…0031` into the website
   `docs/architecture/decisions/` (0026 manifests, 0027 AI policies and
   snapshots, 0028 eligibility routing, 0029 adapter contract / usage /
   Padyar boundary, 0030 resilience and fallback, 0031 egress denial).
5. **Compose** (`infra/compose/compose.onprem.yaml`, `compose.cloud.yaml`,
   `x-app-environment`): pass `MODEL_MANIFEST_PATHS`
   (e.g. `/app/manifests/operator.json`), `MODEL_MANIFEST_PINS` (default
   empty); leave `MODEL_MANIFEST_STRICT` unset (strict by default outside
   dev/test; production refuses `false`). Mount the operator manifest
   directory read-only (e.g. `${MODEL_MANIFEST_DIR:-./manifests}:/app/manifests:ro`)
   on `api` and every worker. Document in `docs/operations/deployment.md`:
   run `python -m backend.jobs.model_preflight --register` after deploy;
   exit 1 means a stage has no qualified manifest.
6. **Compose (WP08-T05)**: pass through, with these defaults, on `api` and
   every worker: `JOB_RETRY_JITTER_RATIO` (0.1),
   `PROVIDER_CIRCUIT_FAILURE_THRESHOLD` (5),
   `PROVIDER_CIRCUIT_COOLDOWN_SECONDS` (60), `PROVIDER_RATE_LIMITS` (empty),
   `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL` (both empty = no fallback),
   `LLM_FALLBACK_API_KEY` (secret; from the secret store, never committed).
   Document them in `docs/operations/deployment.md` together with the
   operator steps: a fallback needs its own manifest and an owner's
   `fallback: true` in the workspace AI policy.
7. **Network-layer egress (WP08-T06)**: in `compose.onprem.yaml` /
   `compose.cloud.yaml` put `api` and every worker on an `internal: true`
   network (no default route); give AI traffic a single egress proxy
   (allowlist = hosts:ports from `python -m backend.jobs.model_preflight`
   → `egress`); nginx stays the only ingress. The air-gapped profile has no
   external network at all. CI: add a job that fails if the preflight's
   `egress` list contains a `cloud` endpoint for an air-gapped profile.
8. **Records.** WP08 README/tasks/backlog statuses and PROGRESS from the
   task table above; refresh Graphify once on the integrated tree.
9. **Session B.** See `API_CONTRACT.md`.

## External inputs required

- **Padyar AI Gateway API contract** (blocks the adapter; the boundary and a
  typed unconfigured state exist): base URL and paths per capability; auth
  scheme and credential scope; model ids and revision pinning; supported
  capabilities (analysis, STT, diarization, embeddings); structured-output
  mode; usage fields; error model, rate limits, Retry-After; processing
  regions, retention, subprocessors; cloud-only or privately deployable.
- Provider credentials/access for any real endpoint (none used or tested).
- Representative evaluation data for model qualification (WP13).

## Open owner decisions (unchanged)

Retention defaults, backup retention, empty-transcript charging, live S3
authorization, representative evaluation data, provider credentials/access
(including the Padyar AI Gateway API contract).
