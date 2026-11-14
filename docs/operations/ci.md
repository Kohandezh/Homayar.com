# CI Release Gates

`.github/workflows/ci.yml` runs independent jobs so failures identify the owning surface:

| Job | Required checks |
| --- | --- |
| Website | frozen pnpm install in `website/`, lint, Next.js build, Cloudflare Workers build (`pnpm build:cf`) |
| Product console | frozen npm install, nested lint, `/app/` build, declared test script and tests |
| Product console end-to-end | PostgreSQL and Redis services, frozen uv environment, `alembic upgrade head`, demo seed, the API on `127.0.0.1:8001`, then the full Playwright suite (`npm run test:e2e`) in Chromium |
| Backend | locked uv environment (`uv sync --locked`), single Alembic head, upgrade/check/current-head, pytest |
| Release manifest | manifest of the commit generated twice and byte-compared, API image built with the commit's release arguments, its `build-info.json` and OCI labels verified against the manifest, `homayar_build_info` read from the running image, manifest uploaded as the `release-manifest` artifact ([release.md](release.md)) |
| Secret scan | full Git history scan with Gitleaks and redacted findings |
| Container config | Actionlint, on-prem (with and without the egress profile)/cloud Compose, live Neo4j health, nginx, ShellCheck, Hadolint, image builds (including the egress proxy) and runtime smoke tests, the container-boundary egress test (`infra/egress/tests/test-egress-network.sh`) the air-gapped gate of the model preflight (`infra/ci/airgap_egress_check.py`), and promtool on the alert rules, their unit tests and the example Prometheus configuration (`infra/observability/prometheus`, HOM-126) |

Two other workflows deploy the public website: **Auto-deploy website**
(`auto-deploy-website.yml`, after CI passes on `main`) and **Release website**
(`release-website.yml`, by hand). Both call `deploy-website.yml`. See
[website/DEPLOY.md](../../website/DEPLOY.md).

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

Each backend test part runs `infra/ci/check-migrations.sh` (upgrade head, `alembic check`, head
verification) and then the test suite against the PostgreSQL service: `TEST_DATABASE_URL`
selects the database and `HOMAYAR_REQUIRE_POSTGRES_TESTS=1` makes the suite refuse to fall back
to SQLite, so a green backend gate always means PostgreSQL. Locally, run
`TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/db uv run pytest backend/tests`
inside `Homayar/`; without the variable the suite uses temporary SQLite files and skips the
`postgres`-marked tests. The console and backend jobs still fail explicitly when their product
files are absent from the commit.

On PostgreSQL the suite builds the schema once per process (HOM-292,
`backend/tests/_isolation.py`). Before each test that uses `client`, the `settings` fixture
compares a catalog fingerprint with the one taken after the last build and, when it is equal,
only removes the rows. A test that changed the schema, ran a migration, dropped the tables or
changed a role gets a full rebuild before the next test, without a marker. A test with
`settings` but without `client` starts from an empty schema, as before.
`backend/tests/test_database_isolation.py` proves these rules.

The backend tests run in four parts (HOM-292,
[ADR 0081](../architecture/decisions/0081-backend-ci-timed-parts-behind-gate-checks.md)). The job
`backend-tests` is a matrix, `Backend tests part 1/4` to `Backend tests part 4/4`, each on its own
runner with its own PostgreSQL and Redis. The parts are not required checks. Branch protection
requires `Backend tests and migrations (shard 1/2)` and `(shard 2/2)`. These two names are now
gate jobs (job id `backend`) that run no tests. Each gate runs with `if: always()` and fails
unless both `source-contract` and `backend-tests` report `success`. GitHub reports one result for
the whole part matrix, so a failed, cancelled or skipped part fails both gates.

The pytest option `--test-shard=<index>/<count>` keeps the tests of the files whose part is
`index`. Write both HOM-292 options in the `--opt=value` form: pytest reads the command line
before `backend/tests/conftest.py` adds them, so in the `--opt value` form a value before the
test paths is taken as a path and the options are "unrecognized". The part of a file comes from the checked-in timing map
`backend/tests/shard_timings.json`: measured seconds per test file name. A duration plan sorts
the map by seconds, largest first, and gives each file to the part with the smallest total so
far (`part_of` in `backend/tests/_shard.py`). The plan reads the map only, never what a run
collected. So all parts and every xdist worker agree on each file, and every test runs in exactly
one part. A file that is not in the map falls back to CRC-32 of a fixed prefix and its file name,
modulo the count (`shard_of`), so a new file runs without a map update. The map is keyed by file
name, so test file names must be unique under `backend/tests` (a test checks this). Each part
prints `shard i/4: collected N tests, selected M in F files (K files not in the timing map)`.
Without the option every test runs; a nested pytest run that a test starts gets no option and is
not split. The MinIO contract step runs in part 1 only.

`backend/tests/test_shard_assignment.py` checks the map against the test files (the drift
check). It fails when the map names a file that no longer exists, when more than 25 test files
(`MAX_MISSING_FILES`) are missing from the map, or when the plan for four parts gives the
heaviest part more than 1.10 times the seconds of the lightest one. Then refresh the map. Each
part writes its measured seconds per file (setup, call and teardown) with
`--shard-timings-out=<file>` and uploads them as the artifact `backend-shard-timings-part-<n>`, kept
14 days (a re-run of one part overwrites its artifact). Take the four artifacts of one run where all four parts finished, and in `Homayar/`:

```bash
gh run download <run id> --repo Kohandezh/Homayar.com \
  --pattern 'backend-shard-timings-part-*' --dir "$TMPDIR/shard-timings"
uv run python -m backend.tests._shard merge backend/tests/shard_timings.json \
  "$TMPDIR"/shard-timings/backend-shard-timings-part-*/shard-timings-part-*.json \
  --source "CI run <run id>"
```

Then commit the new map. The merge stops with exit code 2 when a file name is in two inputs,
because parts never share a file. A refresh moves files between parts; the next run measures
the new split.

Each part runs its tests with four pytest-xdist workers (`-n 4 --dist loadfile`). The files with
the most seconds in the map start first, and files that are not in the map keep xdist's order
after them, so a slow file does not end the run alone. Each worker uses its own database
(`homayar_test_gw0` to `homayar_test_gw3`) and Redis index, and a test whose source changes
roles, role memberships or runs an Alembic upgrade or downgrade runs alone under a cluster-wide
lock (`backend/tests/_parallel.py`). A test that reads a wall clock and checks a time budget
(the golden-module budget below) also runs alone, so the other workers do not slow it down. The
scan reads the test, the functions and classes it reaches in `backend/tests` and its fixtures.
A guard compares the roles when a shared test starts with the roles after its teardown and fails
the test when they differ. Known limit: a test whose role change the scan cannot see (SQL built
at run time, lower-case SQL, code outside `backend/tests`) and that undoes the change before it
ends is not caught; other workers may see the role while it exists. Without `-n` the suite runs
serially exactly as before.
Before the full test step, the step "Qualify the storage contract against MinIO" starts a
disposable MinIO container with `infra/ci/minio-contract-target.sh` and runs the `minio_s3`
cases with `HOMAYAR_REQUIRE_MINIO_TESTS=1`, so it stops with a usage error instead of skipping
them ([ADR 0019](../architecture/decisions/0019-storage-contract.md), HOM-36). The credentials
stay in that step, so the full test step skips the `s3-minio` cases as NOT_RUN.

Each backend test part installs `ffmpeg` (which provides `ffprobe`) from the Ubuntu 24.04 archive and
prints both versions, so the `ffmpeg`-marked media tests (`test_media_fixtures.py`,
`test_media_adversarial_api.py`) run against real binaries. `HOMAYAR_REQUIRE_MEDIA_TOOLS=1` makes
the suite stop with a usage error instead of skipping them when either binary is missing. The
version follows the distribution's security updates rather than an exact pin; the job log records
it. Locally, without the variable, the suite still skips the marker when the binaries are absent.
The pytest summary (`-rs`) lists every remaining skip with its reason: the opt-in `live_s3` tests
without a bucket, the Padyar AI Gateway tests without a contract, SQLite-only tests, and the three
promtool cases of `test_alert_rules.py` (`check-rules`, `test-rules`, `check-config`), because the
backend test parts have no `prom/prometheus` image. The *Container configuration* job runs the same three
promtool commands and is the gate for the alert rules; do not drop its steps because pytest seems
to cover them.

Each backend test part may run for 40 minutes, each gate job for 5. The rule: about 1.5 times
the slowest measured part job (24:26 on run 43542467792, below), rounded up to a multiple of 5.
The history (HOM-292): the
test step took 1:19:49 on run 40769280687, when every test rebuilt the schema, and 48:00 on run
40795356019 with the schema built once and the tests run serially. One job with four xdist
workers took 27:10, 18:43 and 28:09 (runs 40805797597, 40808843162 and 40809285732): shared
runners vary by about 1.5 times. Two shards by CRC took steps of 10:21 and 13:29 on run
40817325867. Then the suite grew. By 2026-11-14 the two shards took 26 to 36 minutes (run
43498411851 on `main`: 27:10 and 35:55), and shard 2 hit the 45 minute limit three times. The
workers already use about 68 % of the four vCPU next to PostgreSQL, so even a perfect two-way
split would take about 31 minutes. Four timed parts aim at a test step under 25 minutes.

The first measurement was run 43539360099: four parts by the CRC fallback, with an empty map.
The test steps took 14:29, 20:24, 19:26 and 19:43, and pytest measured 14 278 s over 340
files. That run's artifacts built the first map. Its plan gives each part 3 570 s.

The first run with that map, run 43542467792, was green. The test steps took 22:55, 19:30,
16:31 and 15:38 for parts 1 to 4 (jobs 24:26, 20:22, 17:27 and 16:57); the gates took 0:02 and
0:03. Inside each part the four workers ended within 2 s of each other. The spread between the
parts comes from runner speed: nearly every file of a part ran slower or faster together.
Compared with the map, the median file ran 1.22, 1.06, 0.86 and 0.86 times as long in parts 1
to 4, so about 1.4 to 1.5 times between the slowest and the fastest part runner. The map
balances work, not runners; no map can fix that spread.

The golden text-normalization module (`test_text_normalization_golden.py`, HOM-120/HOM-172) has a
wall-clock budget, enforced by
`test_text_normalization_owner.py::TestDeliverables::test_golden_module_is_green_fast_and_independent_of_the_registry`.
The SPEC budget of 30 s is the default everywhere, including the owner's host. Owner decision of
2026-10-10: on GitHub CI only, the backend test parts set `HOMAYAR_GOLDEN_BUDGET_SECONDS=120`, because
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
basic process viability. SBOMs and package and layer scans run in the separate Security scans
workflow (next section). No workflow yet signs digest references, starts the full selected
Compose profile, or runs same-origin upload and tenant-isolation smoke tests without live
providers.

## Security scans

`.github/workflows/security-scans.yml` (`name: Security scans`, HOM-130) runs on every pull
request, on each push to `main`, every Monday, and by hand. It is separate from CI on purpose:
the website auto-deploy waits only for the workflow named `CI`, so a newly published CVE does not
stop a deploy. Both jobs call `infra/ci/security-scan.sh`, so a local run with the same command is
the CI run. Syft and Grype run from the official `anchore/syft` and `anchore/grype` images,
pinned by tag and digest in that script.

Its two checks, `Dependency scan (lockfiles)` and `Container scan (images)`, are not required
checks in `infra/ci/branch-protection-main.json`. Making them required is the owner's decision.
If that happens, that file and the table in the branch protection section below must change in
the same PR.

| Job | What it scans |
| --- | --- |
| Dependency scan | `Homayar/uv.lock`, `Homayar/frontend/package-lock.json`, `website/pnpm-lock.yaml`, `tools/linear-agent/package-lock.json`. Syft reads `uv.lock` directly. Dev dependencies are included. |
| Container scan | The API, console and egress-proxy images, built with default ARGs as in the container-config job, then saved with `docker save` and read from disk. |

For each target the job writes a CycloneDX JSON SBOM and a Grype JSON report.

- **Gate.** A target fails when Grype reports a **high or critical** vulnerability that **has a
  fix** and **no ignore rule** in `.grype.yaml`. Findings without a fix, and medium or low
  findings, stay in the report but do not fail the gate.
- **SBOM guard.** A target fails when its SBOM lacks a known component (`fastapi`, `react`,
  `next`, `@anthropic-ai/claude-agent-sdk`, and at least one OS package in each image). This
  catches a Syft that silently stops reading a lockfile format.
- **Canary.** Each job first gates `infra/ci/fixtures/canary-sbom.cdx.json` (lodash 4.17.20). The
  step passes only when the gate fails with exit code 1 and names `GHSA-35jh-r3h4-6jhm`. This
  proves the scanner, its database and the gate are live.
- **Artifacts.** `security-scan-dependencies` and `security-scan-containers` hold every SBOM and
  report, also when the gate fails (kept 30 days).

Run locally from the repository root (Docker and `jq` needed):

```bash
infra/ci/security-scan.sh canary /tmp/scan/canary
infra/ci/security-scan.sh deps /tmp/scan/deps
infra/ci/security-scan.sh images /tmp/scan/images
```

When the gate fails on a new finding:

1. Fix it if the fix is a patch or minor bump through the package manager (or a base-image
   digest refresh within the same tag), and the existing CI jobs stay green.
2. Otherwise check if the product can reach the vulnerable code. Add a row to
   [vulnerability-triage.md](../security/vulnerability-triage.md) with the evidence and the
   decision (`accepted: not reachable` or `follow-up`), then add one ignore rule to `.grype.yaml`
   with exactly that vulnerability ID and package name, and a comment that names the row.

An ignore rule hides that ID for that package in every target and every version. So when a
package moves from a dev dependency into runtime code, or shows up in a new target, check its row
again.

## Branch protection on main (HOM-270)

`main` is protected. A pull request is required before merging, with 0 approving reviews. All
10 required checks below must pass before a collaborator can merge (missions merge as
collaborators). Force pushes to `main` are blocked and `main` cannot be deleted. Merge
commits stay allowed (`required_linear_history` is false); the repo merge settings are not
touched.

The rule exists because of HOM-223: PR #67 merged while its own CI was red, and `main`
stayed red after it. A required check now blocks that merge. Only the owner account can
still merge past it, by ticking the bypass box on purpose (see the emergency paragraph).

The 10 required checks, next to the job id in `.github/workflows/ci.yml` that reports each:

| Job id | Required check name |
| --- | --- |
| `source-contract` | `Product source contract` |
| `website` | `Website lint and Cloudflare build` |
| `console-web` | `Product console lint, build, and tests` |
| `console-e2e` | `Product console end-to-end tests` |
| `linear-agent` | `Linear agent typecheck, lint, and tests` |
| `backend` (gate, shard 1) | `Backend tests and migrations (shard 1/2)` |
| `backend` (gate, shard 2) | `Backend tests and migrations (shard 2/2)` |
| `secret-scan` | `Secret scan` |
| `container-config` | `Container configuration` |
| `release-manifest` | `Release manifest and build identity` |

Each check is pinned to the GitHub Actions app (app id 15368) in the payload. Only a GitHub
Actions check run with that exact name can satisfy it. The payload file is
`infra/ci/branch-protection-main.json`: the source of truth for the rule. This section and
that file must always list the same checks.

`strict` (the branch must be up to date with `main` before merge) is false on purpose during
the sprint. Six or more missions run at once. With strict on, every merge would force every
other open PR to merge `main` in and re-run CI, and the backend test parts take many minutes.
Homlead already tests the merged state when two PRs touch the same files. To turn it on
later, set `"strict": true` in the file in a PR, then apply it again with the command below.

In an emergency, `enforce_admins` is false, so the owner account can merge past a red check
with the "bypass branch protections" box on the merge button. Missions and other
collaborators cannot bypass.

Only the owner account `Kohandezh` can apply or change the rule. The repo belongs to a
personal account; protecting a branch needs admin or owner permission, and collaborators
have write. For a collaborator the protection endpoint returns 404.

To apply, from the repository root, logged in as `Kohandezh`:

```bash
gh api --method PUT -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  repos/Kohandezh/Homayar.com/branches/main/protection \
  --input infra/ci/branch-protection-main.json
```

PUT replaces the whole rule, so always send the full file. To read the rule back:

- the owner: `gh api repos/Kohandezh/Homayar.com/branches/main/protection`
- anyone: `gh api repos/Kohandezh/Homayar.com/branches/main --jq '{protected: .protected, checks: .protection.required_status_checks.contexts}'`

How to change the list of required checks, and the traps:

- A check name is the job's `name:` after GitHub fills in the matrix values. Renaming a job,
  or changing the `shard` matrix of the `backend` gate job, changes the check name. The
  backend test parts (`backend-tests`) are not required, so their count can change freely;
  the gates check all of them (ADR 0081).
- If a required name is no longer reported, every PR waits for it ("Expected, waiting for
  status to be reported") and cannot merge. A PR that renames, adds or removes a CI job must
  update `infra/ci/branch-protection-main.json` and this section in the same PR. The owner
  applies the new file when that PR merges. Other open PRs then merge `main` in so they
  report the new names.
- Do not add a path, branch or commit-message filter to the `pull_request` trigger in
  `ci.yml` while its jobs are required. A skipped workflow leaves its required checks pending
  and blocks every PR it skips. The existing `push: branches: [main, master]` filter is fine.
- A job skipped because a job in its `needs` failed counts as passing. That is safe here
  only because `Product source contract`, the job the others need, is required itself. A new
  job with `needs` keeps that property only if every job it needs is also required.
