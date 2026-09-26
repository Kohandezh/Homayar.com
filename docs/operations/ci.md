# CI Release Gates

`.github/workflows/ci.yml` runs independent jobs so failures identify the owning surface:

| Job | Required checks |
| --- | --- |
| Public web | reviewed pnpm lifecycle scripts, frozen install, lint, server build, cPanel export, Playwright tests (the last two once committed, see below) |
| Product console | frozen npm install, nested lint, `/app/` build, declared test script and tests |
| Backend | frozen uv environment, single Alembic head, upgrade/check/current-head, pytest |
| Secret scan | full Git history scan with Gitleaks and redacted findings |
| Container config | Actionlint, on-prem/cloud Compose, live Neo4j health, nginx, ShellCheck, Hadolint, image builds and runtime smoke tests |

Product source comes from `Kohandezh/HomaYar` at the commit pinned in
`infra/product-source.lock`. The `source-contract` job runs first: the composite action
`.github/actions/product-source` reads the lock, checks the product repository out into
`Homayar/` at that commit (credentials are not persisted) and runs
`infra/ci/check-source-contract.sh --mode ci`, then executes the checker's own tests. The
console, backend and container jobs depend on that job and repeat the pinned checkout. In the
local workspace `Homayar/` is an independent, ignored Git clone; run
`infra/ci/check-source-contract.sh --mode local` to compare it with the pin. The checker fails
explicitly when the pinned commit does not contain every required path, when a forbidden path is
tracked, or when the pinned commit is not reachable from the declared `ref=` (the action fetches
that ref, commits and trees only, before verifying; a pin that exists only in a local clone
therefore fails remote CI until the ref is published). A private product repository needs a
read-only token in the `PRODUCT_SOURCE_TOKEN` secret. Since WP01-T03 the pin points at
`feat/commercial-v1-overlay`; the branch was published on 2026-09-25, so remote CI can fetch the
pin once `PRODUCT_SOURCE_TOKEN` is configured (the default workflow token cannot read the private
product repository).

The backend job runs `infra/ci/check-migrations.sh` (upgrade head, `alembic check`, head
verification) and then the test suite against the PostgreSQL service: `TEST_DATABASE_URL`
selects the database and `HOMAYAR_REQUIRE_POSTGRES_TESTS=1` makes the suite refuse to fall back
to SQLite, so a green backend job always means PostgreSQL. Locally, run
`TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/db uv run pytest backend/tests`
inside `Homayar/`; without the variable the suite uses temporary SQLite files and skips the
`postgres`-marked tests. See
[ADR 0002](../architecture/decisions/0002-product-source-contract.md). The workflow still emits
its older explicit source-contract error when the nested console or backend artifacts are absent.

Backend CI uses only isolated PostgreSQL and Redis service containers. It sets test mode and
mock-provider permission explicitly; it does not call email, SMS, OAuth, ASR, diarization,
LLM, S3, Neo4j, or other live providers. Root browser tests are expected to stub any external
commercial integration they exercise.

The cPanel export (`scripts/build-cpanel.mjs`, `pnpm build:cpanel`) and the Playwright suite
(`tests/*.spec.ts`) belong to the website overlay recorded in WP01-T03, which is not committed to
this repository yet. Until each is committed, its steps are skipped and the public-web job carries
a "Website gates pending" warning annotation; they run automatically as soon as the files exist.
This is a temporary exception for files that do not exist, not an optional gate: once the overlay
lands, remove the conditions from `.github/workflows/ci.yml`.

The console lane fails when `Homayar/frontend/package.json` has no `test` script. The backend
lane fails when Alembic configuration is absent, more than one migration head exists, schema
autogeneration detects drift, or the database is not at all heads. These are intentional
release gates and must not be converted to optional checks.

GitHub-maintained actions are pinned to full commit SHAs. Tool containers are pinned to
version tags in CI and should move to recorded multi-architecture digests after the registry
mirror and update process are established. Dependency or image updates require the same test
suite and a reviewed generated diff.

The workflow builds all three image templates and starts each final image with the same
non-root/read-only assumptions used by Compose. It verifies the public root, console
`/app/index.html`, API `/healthz`, and configured runtime users. These checks prove build and
basic process viability; they do not replace a release pipeline that emits SBOMs, scans
packages and layers, signs digest references, starts the full selected Compose profile, and
runs same-origin upload and tenant-isolation smoke tests without live providers.
