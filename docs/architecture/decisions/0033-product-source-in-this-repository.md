# ADR 0033 — Product source tracked in this repository

Date: 2026-09-26 · Status: **accepted** (owner approval 2026-09-26; merged to `main` in PR #3, `edfcb78`) · Supersedes: [ADR 0002](0002-product-source-contract.md) (external pin) · Packages: WP01

## Context

ADR 0002 kept the product source (`Kohandezh/HomaYar`) in a separate repository and made this
repository's CI check it out at a commit pinned in `infra/product-source.lock`. In practice every
change to the product had to be published in two repositories in order (product branch first,
then the website pin), CI needed a cross-repository token (`PRODUCT_SOURCE_TOKEN`), and a pin that
existed only locally failed remote CI. The CI, Docker and Compose templates that build the product
already live here. The owner asked for the `/Homayar/` ignore rule to be removed and the product
source to be part of this repository.

## Decision

1. **One repository.** The product tree at `Kohandezh/HomaYar` commit
   `e7eeef53d6e864488405078e6eb93207bd3db051` (`feat/commercial-v1-overlay`) is committed under
   `Homayar/` as ordinary files, without its history. From this point, product changes are made
   and reviewed here, in the same commit as the infrastructure that builds them.
2. **Never a gitlink.** `Homayar/` is a tracked directory, not a submodule. The `/Homayar/` ignore
   rule is removed; the product's own `.gitignore` files keep environments, dependencies, caches
   and runtime data out.
3. **Contract kept, pin dropped.** `infra/product-source.lock` now declares `product_dir`, the
   same `required_path` and `forbidden_path` lists, and the import provenance (`imported_from`,
   `imported_ref`, `imported_sha`, informational). `infra/ci/check-source-contract.sh` verifies at
   a revision (default `HEAD`) that the product directory is an ordinary tracked tree (a gitlink
   or a `.gitmodules` entry is a violation), that every required path is committed and that no
   forbidden path is tracked; `ci` mode also requires a clean product directory without a nested
   `.git`. It reports whether the tree is still identical to the imported commit.
4. **CI.** The `source-contract` job runs the checker and its tests on the repository checkout.
   The console, backend and container jobs build from the same checkout; the composite action
   `.github/actions/product-source` is removed and CI no longer reads `PRODUCT_SOURCE_TOKEN`.
5. **History.** Product history before the import stays in `Kohandezh/HomaYar`. That repository
   is no longer a build input and receives no further changes (no dual writes); archiving it is an
   owner decision.
6. **Final synchronization (2026-09-26).** Once, at the closeout, the product changes made here
   after the import (the transport drill fix) were committed to `Kohandezh/HomaYar`
   `feat/commercial-v1-overlay` (`f29d04a`) and merged into its development branch `dev`
   (`Kohandezh/HomaYar` PR #1, `c567a91`). At that point `dev^{tree}` equals `main:Homayar` of this
   repository (`4b81c5dd`); there are no intentional differences. The product repository's default
   branch `master` is a disconnected legacy history (no common ancestor) and was left untouched.
   Every later product change is made only here.

## Consequences

- One review and one CI run cover a product change and its infrastructure. Nothing
  cross-repository needs publishing in order.
- Gitleaks now scans the product files as part of this repository's history.
- The `PRODUCT_SOURCE_TOKEN` secret is unused; the owner may revoke it.
- **Existing local checkouts.** A workspace where `Homayar/` is still a separate clone keeps
  working (Git does not descend into the nested `.git`, and the checker warns about it in local
  mode), but Git commands run inside `Homayar/` then address that clone. Because `/Homayar/` was
  ignored before this change, `git checkout`, `git merge` and `git pull` of a commit that tracks
  `Homayar/` **silently overwrite** ignored files there. Before updating such a checkout, commit
  or back up any uncommitted work in its `Homayar/` clone (or run the update with
  `--no-overwrite-ignore`, which refuses instead of overwriting), then retire the nested `.git`
  deliberately.
- Blueprint Section 31 (two independently releasable repositories in the near term) is
  overridden for the product source; release artefacts (image digests, manifests) remain the
  deployment contract of ADR 0002 item 5.

## Alternatives considered

- **Keep the external pin (ADR 0002).** Rejected by the owner: two-step publication and a
  cross-repository token for every change.
- **Git submodule.** Keeps the two-step publication and the gitlink hazards ADR 0002 described.
- **Subtree merge with history.** Would bring the full product history (including files removed
  later) into this repository; the history stays available in `Kohandezh/HomaYar` instead.

## Verification

- `bash infra/ci/tests/test-check-source-contract.sh` (synthetic repositories, 29 cases).
- `infra/ci/check-source-contract.sh --mode local` in a workspace (satisfied; warns while a nested
  clone remains) and `--mode ci` in CI.
- `git ls-tree HEAD Homayar` shows `040000 tree`, and the tree equals `e7eeef53^{tree}` at the
  import commit.
