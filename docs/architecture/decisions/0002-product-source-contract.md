# ADR 0002 — Product source contract for clean-checkout builds

Date: 2026-09-07. Status: **superseded** by [ADR 0033](0033-product-source-in-this-repository.md) on 2026-09-26 (the product source is now tracked in this repository; the external pin, the checkout action and `PRODUCT_SOURCE_TOKEN` are retired). Originally: proposed (implemented locally in WP01-T02). Supersedes nothing; complements [ADR 0001](0001-preserve-runtime-roots.md).

## Context

The website/infrastructure repository (`Kohandezh/Homayar.com`) contains the CI workflow, Docker templates and Compose profiles for the whole product, and they read product source from `Homayar/`:

- `.github/workflows/ci.yml` jobs `console-web`, `backend` and `container-config` require `Homayar/frontend`, `Homayar/pyproject.toml`, `Homayar/uv.lock`, `Homayar/alembic.ini` and `Homayar/backend`.
- `infra/docker/api.Dockerfile` and `infra/docker/console-web.Dockerfile` `COPY Homayar/...` from the outer build context (`context: ../..` in `infra/compose/compose.onprem.yaml`).

`Homayar/` is an **independent Git clone** of `Kohandezh/HomaYar` (branch `dev`, HEAD `605cb074`). It is not tracked by the outer repository, is not a submodule, and there is no `.gitmodules`. A clean checkout of either remote therefore cannot run the CI or build the images. Worse, the committed product HEAD does not contain `pyproject.toml`, `uv.lock`, `alembic.ini`, `backend/migrations`, `backend/routers/v1`, `backend/providers`, `backend/jobs`, `backend/security`, `backend/storage` or `backend/schemas`; those exist only as an uncommitted local overlay (see `docs/handoff/claude-fable-5.1/inventory/2026-09-07-source-state.json`). No committed ref anywhere satisfies the build today.

The blueprint (Sections 3 and 31) keeps two independently releasable repositories in the near term and asks infrastructure to consume **pinned product releases** rather than an untracked nested checkout, without deleting local work and without an immediate monorepo migration.

## Decision

1. **Ownership.** `Kohandezh/HomaYar` owns product source (API, workers, console, migrations). `Kohandezh/Homayar.com` owns the public website and, for now, the shared CI/Docker/Compose templates. The nested clone stays where it is for local development and is ignored by the outer repository (`/Homayar/` in `.gitignore`) so it can never be staged accidentally as a gitlink.
2. **Explicit pin.** `infra/product-source.lock` records the product repository URL, ref, full commit id, the `required_path` entries the build reads, and `forbidden_path` entries that must never be tracked. Changing the pin is a reviewed change to that file.
3. **Testable check.** `infra/ci/check-source-contract.sh` verifies the checkout against the lock (independent checkout, origin, pinned commit present and checked out, required paths committed at the pinned commit, forbidden paths untracked; clean tree in `ci` mode). It fails with an explicit reason when the pinned commit cannot satisfy the build, including the current "required path exists only as uncommitted overlay" state. `infra/ci/tests/test-check-source-contract.sh` covers the exit codes with synthetic repositories.
4. **CI wiring.** A composite action `.github/actions/product-source` reads the lock, checks out the product repository at the pinned commit into `Homayar/` (`actions/checkout` with `repository`/`ref`/`path`, credentials not persisted) and runs the checker. A new `source-contract` job runs first; `console-web`, `backend` and `container-config` depend on it and perform the same checkout. The existing "source is present" guards remain as a second line of defence.
5. **Target end state (later WP01 tasks).** The product repository publishes release artifacts (image digests, schema and model manifests) from its own clean checkout; this repository's Compose profiles consume those digests (`compose.cloud.yaml` already requires immutable image references). The lock file then pins a release manifest instead of a raw commit. Submodule or monorepo conversion remains a separate decision and is not implied by this ADR.

## Consequences

- The contract is now explicit and machine-checked, and it is **honestly red**: the pinned commit `605cb074` fails the checker because the v1 overlay is uncommitted. Making it green requires WP01-T03 (review and commit the overlay in `Kohandezh/HomaYar`, then move the pin), not a weaker check.
- Running the CI against a private `Kohandezh/HomaYar` requires a read-only token in the `PRODUCT_SOURCE_TOKEN` secret (a fine-grained PAT or GitHub App token); the workflow token is used when the repository is public or grants access. This is an owner action.
- Docker builds and Compose `build:` blocks still expect `Homayar/` inside the outer build context; in CI that directory now comes from the pinned checkout, locally from the developer's clone. The checker reports drift between the two.
- Nothing in this decision moves, deletes or rewrites the local product clone or its history.

## Alternatives considered

- **Git submodule (gitlink).** Simple for `actions/checkout` with `submodules: true`, but it embeds a product commit pointer in website history, invites accidental `git add Homayar` mistakes, and still requires the overlay to be committed first. Deferred; may be adopted deliberately later.
- **Monorepo import / subtree.** Rejected for now by blueprint Section 31: unnecessary migration before the source is reconciled, and it would blur product versus website provenance.
- **Release artifacts only.** The correct end state, but the product repository publishes no artifacts yet; adopting it now would leave CI with nothing to test.

## Verification

- `bash infra/ci/tests/test-check-source-contract.sh` (synthetic repositories; expected all cases pass).
- `infra/ci/check-source-contract.sh --mode local --product-dir Homayar` against the current workspace (expected exit 3 with five "uncommitted overlay" findings until WP01-T03 lands).
- CI: the `source-contract` job on GitHub is expected to fail with the same explicit finding until the overlay is committed and the pin moved; that failure is the contract working.
