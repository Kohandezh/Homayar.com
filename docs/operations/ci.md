# CI Release Gates

`.github/workflows/ci.yml` runs independent jobs so failures identify the owning surface:

| Job | Required checks |
| --- | --- |
| Website | frozen pnpm install in `website/`, lint, Next.js build, Cloudflare Workers build (`pnpm build:cf`) |
| Product console | frozen npm install, nested lint, `/app/` build, declared test script and tests |
| Product console end-to-end | PostgreSQL and Redis services, frozen uv environment, `alembic upgrade head`, demo seed, the API on `127.0.0.1:8001`, then the full Playwright suite (`npm run test:e2e`) in Chromium |
| Backend | frozen uv environment, single Alembic head, upgrade/check/current-head, pytest |
| Secret scan | full Git history scan with Gitleaks and redacted findings |
| Container config | Actionlint, on-prem (with and without the egress profile)/cloud Compose, live Neo4j health, nginx, ShellCheck, Hadolint, image builds (including the egress proxy) and runtime smoke tests, the container-boundary egress test (`infra/egress/tests/test-egress-network.sh`) and the air-gapped gate of the model preflight (`infra/ci/airgap_egress_check.py`) |

The product source is tracked in this repository under `Homayar/`
([ADR 0033](../architecture/decisions/0033-product-source-in-this-repository.md), which
supersedes the external pin of ADR 0002). The `source-contract` job runs first:
`infra/ci/check-source-contract.sh --mode ci` reads `infra/product-source.lock` and fails when
`Homayar/` is untracked, a gitlink or declared in `.gitmodules`, when a required path is not
committed, when a forbidden path (environments, runtime data, caches) is tracked, or when the
product directory is dirty or contains a nested `.git`; the job then executes the checker's own
tests. The console, backend and container jobs depend on that job and build from the same
checkout; no second repository or token is involved. Locally, run
`infra/ci/check-source-contract.sh --mode local`; it also reports whether `Homayar/` is still
identical to the imported product commit.

The backend job runs `infra/ci/check-migrations.sh` (upgrade head, `alembic check`, head
verification) and then the test suite against the PostgreSQL service: `TEST_DATABASE_URL`
selects the database and `HOMAYAR_REQUIRE_POSTGRES_TESTS=1` makes the suite refuse to fall back
to SQLite, so a green backend job always means PostgreSQL. Locally, run
`TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/db uv run pytest backend/tests`
inside `Homayar/`; without the variable the suite uses temporary SQLite files and skips the
`postgres`-marked tests. The console and backend jobs still fail explicitly when their product
files are absent from the commit.

The backend job installs `ffmpeg` (which provides `ffprobe`) from the Ubuntu 24.04 archive and
prints both versions, so the `ffmpeg`-marked media tests (`test_media_fixtures.py`,
`test_media_adversarial_api.py`) run against real binaries. `HOMAYAR_REQUIRE_MEDIA_TOOLS=1` makes
the suite stop with a usage error instead of skipping them when either binary is missing. The
version follows the distribution's security updates rather than an exact pin; the job log records
it. Locally, without the variable, the suite still skips the marker when the binaries are absent.
The pytest summary (`-rs`) lists every remaining skip with its reason: the opt-in `live_s3` tests
without a bucket, the Padyar AI Gateway tests without a contract, and SQLite-only tests.

The backend job may run for 40 minutes. The test step alone took about 22 minutes on `2f5f666`,
which left too little room under the earlier 25-minute limit.

The golden text-normalization module (`test_text_normalization_golden.py`, HOM-120/HOM-172) has a
wall-clock budget, enforced by
`test_text_normalization_owner.py::TestDeliverables::test_golden_module_is_green_fast_and_independent_of_the_registry`.
The SPEC budget of 30 s is the default everywhere, including the owner's host. Owner decision of
2026-10-10: on GitHub CI only, the backend job sets `HOMAYAR_GOLDEN_BUDGET_SECONDS=120`, because
shared runners took 37 to 77 s for the same module (51.14 s on `ecd77cc`, 38.12 s on `2ae3314`,
37.35 s and 76.51 s on PR #18). 120 s is about 1.6 times the slowest run measured, so runner
variance does not fail the job, but the gate still catches a module that gets several times
slower. The test honours the variable only together with `CI=true`. A value that is not a number
of seconds from 30 to 300 fails the gate loudly (after 300 s the test stops waiting for the timed
run). Changing the CI value needs new runner measurements and an owner decision recorded on
HOM-172. Never change the 30 s default.

The transport drills (`test_transport_drills.py`) run real Celery prefork workers against the Redis
service and are timing-sensitive by nature. When one fails, its report includes the last 40 lines
of each worker log (the task results and lease events); read them before re-running the job — a
re-run that passes is not a diagnosis.

Backend CI uses only isolated PostgreSQL and Redis service containers. It sets test mode and
mock-provider permission explicitly; it does not call email, SMS, OAuth, ASR, diarization,
LLM, S3, Neo4j, or other live providers.

The console end-to-end job (`console-e2e`, HOM-224) runs the Playwright suite in
`Homayar/frontend/e2e` against a real backend inside the runner. It starts PostgreSQL and Redis
as service containers, installs the backend with `uv sync --frozen`, runs `alembic upgrade head`
and the demo seed (`python -m backend.database.demo_seed`), and starts the API on
`127.0.0.1:8001` in the background. The API sends mail to the SMTP sink that Playwright starts
(`SMTP_HOST=127.0.0.1`, `SMTP_PORT` equal to `E2E_SMTP_PORT`); the invitation flow needs it.
Then `npm run test:e2e` runs with `CI=true`, and Playwright starts the Vite dev server itself.
The job uses only local-only test values (`ci-only-...`) and mock providers, and no secret. The
seed prints the demo logins with their password, so the job drops all of the seed's standard
output, not only the login lines; errors still show. On failure, and also when the job is
cancelled (for example by its 15-minute timeout), the job prints the last 200 lines of the API
log and uploads the artifact
`console-e2e-report` (the HTML report and `test-results/` with traces, screenshots and videos,
kept 7 days). The job may run for 15 minutes. To reproduce it locally, follow the "CI" section of
`Homayar/frontend/e2e/README.md`.

The console lane fails when `Homayar/frontend/package.json` has no `test` script. The backend
lane fails when Alembic configuration is absent, more than one migration head exists, schema
autogeneration detects drift, or the database is not at all heads. These are intentional
release gates and must not be converted to optional checks.

GitHub-maintained actions are pinned to full commit SHAs. Tool containers are pinned to
version tags in CI and should move to recorded multi-architecture digests after the registry
mirror and update process are established. Dependency or image updates require the same test
suite and a reviewed generated diff.

The workflow builds the console and API image templates and starts each final image with the
same non-root/read-only assumptions used by Compose. It verifies console `/app/index.html`, API
`/healthz`, and configured runtime users. These checks prove build and
basic process viability; they do not replace a release pipeline that emits SBOMs, scans
packages and layers, signs digest references, starts the full selected Compose profile, and
runs same-origin upload and tenant-isolation smoke tests without live providers.
