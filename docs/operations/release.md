# Release: build identity and release manifest

Scope: HOM-28 (WP01-T04, reproducible backend artifacts from an exact source revision) and
HOM-29 (WP01-T06, machine-readable release manifest). Decision record:
[ADR 0036, decision 5](../architecture/decisions/0036-operational-evidence-observability-audit-release.md).

The goal: an operator can answer "exactly what source and schema produced this running
backend?", and a developer's local environment cannot silently change release inputs.

## The pieces

| Piece | Where | What it records |
| --- | --- | --- |
| Release manifest | `infra/release/release_manifest.py generate --rev <commit>` | source commit, tree and commit time; application name and version; Python, base image and uv version; digests of every lock file; migration head and count; Dockerfile and `.dockerignore` digests and base images of every image; digest of the code the API image ships; profile; image digests and validation links you pass in; `requires-python`; what is and is not pinned; `manifest_sha256` |
| `build-info.json` | `/app/build-info.json` in the API image, written at build time by `python -m backend.observability.release write-build-info` | source commit and date, profile, Python version, `uv.lock` and `pyproject.toml` digests, digest of the installed distribution set, digest of the shipped code files, migration head, base image reference and uv version the build used |
| OCI labels | API image config | `org.opencontainers.image.revision`, `.version`, `.created` (the commit time), `.source`, `.title`, `.base.name` |
| `homayar_build_info` | `GET /metrics` on the API port | `version`, `source_sha`, `profile`, `migration_head`, `python_version`, `uv_lock_sha256` (first 16 hex) |

The manifest generator reads every input from the Git object database at the requested commit
(`git cat-file`, `git ls-tree`), never from the working tree: untracked files, uncommitted edits,
`.env` files and the caller's environment variables cannot change it. It ignores `GIT_*`
variables and replace refs. Generating twice for the same commit gives byte-identical output; the
build time is the commit time unless `--build-time` is given. It refuses a revision with more
than one migration head, a broken `down_revision` chain or a missing input.

`uv sync --locked` (API image and the backend CI job) refuses a `uv.lock` that is stale
against `pyproject.toml`; `--frozen` only installed whatever the lock said.

## Cut a release

Run from a checkout of the repository; the manifest does not care about its working tree, but
the image build context does, so build from a clean checkout of the release commit.

```bash
git worktree add ../homayar-release <commit>
cd ../homayar-release
python3 infra/release/release_manifest.py build-args --rev HEAD > build-args.txt
cat build-args.txt
```

`build-args.txt` holds `SOURCE_SHA`, `SOURCE_DATE_EPOCH`, `SOURCE_DATE`, `RELEASE_VERSION`,
`RELEASE_PROFILE` and `INSTALL_AI_LOCAL` (`--profile ai-local` for the local-Whisper image).
Build with exactly these and nothing else; in particular do not override `PYTHON_IMAGE` or
`UV_VERSION` (verification compares them with the manifest):

```bash
build_args=()
while IFS= read -r build_arg; do build_args+=(--build-arg "$build_arg"); done < build-args.txt
docker build --file infra/docker/api.Dockerfile "${build_args[@]}" --tag homayar/api:release .
```

Push the image and take its registry digest (`docker buildx imagetools inspect <ref>` or
`docker inspect --format '{{index .RepoDigests 0}}' <ref>`), then write the manifest with the
digest and links to the validation evidence (CI run, drill report). Links must be plain
`http(s)` URLs: credentials, query strings and fragments are refused because they can carry
secrets.

```bash
python3 infra/release/release_manifest.py generate --rev HEAD \
  --image api=sha256:<digest> \
  --validation ci=https://github.com/Kohandezh/Homayar.com/actions/runs/<id> \
  --output release-manifest.json
```

Verify the image against the manifest and keep `release-manifest.json` with the release:

```bash
container="$(docker create homayar/api:release)"
docker cp "$container:/app/build-info.json" build-info.json && docker rm "$container"
docker inspect --format '{{json .Config.Labels}}' homayar/api:release > image-labels.json
python3 infra/release/release_manifest.py verify --manifest release-manifest.json \
  --build-info build-info.json --image-labels image-labels.json
```

`verify` exits 0 with `OK: ...`, or 1 with one line per mismatch, for example
`dependencies.uv_lock.sha256: manifest='…' build-info dependencies.uv_lock_sha256='…'`. It
checks the manifest's own `manifest_sha256` (an integrity checksum, not proof of authorship: it is
unkeyed, so whoever edits a manifest can recompute it), the source commit and
date, version, profile, `uv.lock` and `pyproject.toml` digests, migration head, shipped-code
digest and file count, base image reference, uv version and the Python minor version, and with
`--image-labels` the revision, version, created and base-image labels.

CI does the same on every push and pull request (job `release-manifest`): it generates the
manifest twice and compares the bytes, builds the API image with the `build-args` output,
verifies `build-info.json` and the labels, starts the image and checks that `/metrics` reports
the commit, and uploads `release-manifest.json`, `build-info.json`, `image-labels.json` and
`build-args.txt` as the `release-manifest` artifact (90 days). The CI manifest records the local
image id, because CI does not push images.

## Read the identity of a running backend

* Prometheus: `homayar_build_info{source_sha="<commit>",migration_head="<rev>",...} 1`, next to
  `homayar_schema_up_to_date` (1 when the database's `alembic_version` equals the code's head).
  `source_sha="unreleased"` means the image was built without release arguments (developer and
  CI smoke builds: all-zero `SOURCE_SHA`); `source_sha="unknown"` means there is no
  `build-info.json` (not running from an image).
* The full document from inside a container:

  ```bash
  docker compose --file infra/compose/compose.onprem.yaml --profile onprem exec api \
    python -m backend.observability.release show
  ```

  Save that output as `build-info.json` and run `verify` against the release manifest to prove
  the running container is the release.
* The image labels: `docker inspect --format '{{json .Config.Labels}}' <image>`.

## What is and is not reproducible

The API image is **input-reproducible, not bit-for-bit reproducible**: the same commit, the
same `uv.lock`, the same base image digest and the same uv version give the same
`build-info.json` and the same installed Python distributions, but not the same image digest.

Pinned:

* the source revision (`SOURCE_SHA`, `SOURCE_DATE_EPOCH`; the image `created` time is the
  commit time);
* Python dependencies (`Homayar/uv.lock`, enforced by `uv sync --locked`); the
  `installed_set_sha256` in `build-info.json` records the result;
* the uv version (`UV_VERSION=0.11.24`, installed with pip by version, not by hash);
* the API base image by digest (`python:3.12-slim-bookworm@sha256:34386e…`, the multi-platform
  index resolved on 2026-10-18, Python 3.12.15).

Not pinned (recorded as such under `reproducibility.not_pinned` in every manifest):

* Debian packages installed with `apt-get` (`build-essential`, `libpq-dev`, `ca-certificates`,
  `ffmpeg`, `libgomp1`, `libmagic1`, `libpq5`): they come from the Debian archive on the day of
  the build;
* file timestamps and layer digests;
* the base images of `console-web` and `egress-proxy` (tags; the manifest records
  them with `pinned_by_digest: false`);
* signatures and provenance: the manifest and images are not signed and carry no provenance
  attestation, and the image ids recorded by CI are local ids, not registry digests.

Known gaps (documented, not addressed here):

* `verify` does not compare the installed distribution set (`installed_set_sha256`); it is
  recorded in `build-info.json` for comparison by hand between builds;
* uv is installed with `pip install uv==<version>`, by version, not by hash;
* the on-prem Compose file's `PYTHON_IMAGE` default is a tag (see below);
* `SOURCE_DATE_EPOCH` sets the image `created` time and labels, not the timestamps of files in
  the layers.

`build-info.json` hashes the code the image ships (`alembic.ini` and `backend/` without tests,
environments and caches, the same rule as `infra/docker/api.Dockerfile.dockerignore`), so an
image built from a dirty checkout with a real `SOURCE_SHA` fails `verify`. It does not hash the
installed packages' files, only their names and versions.

The on-prem Compose file passes `PYTHON_IMAGE` with the tag `python:3.12-slim-bookworm` as its
default build argument, so `docker compose build` does not use the digest pin; such images are
unreleased anyway (no `SOURCE_SHA`) and `verify` reports the base-image difference. Release
images are built as above.

## Maintenance

* **Re-pin the base image** on purpose (for a Python patch or Debian security update): run
  `docker buildx imagetools inspect python:3.12-slim-bookworm`, take the index `Digest`, update
  `PYTHON_IMAGE` in `infra/docker/api.Dockerfile` and the date and Python version in its comment,
  and rebuild. Keep the tag's Python minor version equal to `Homayar/.python-version`: the
  manifest refuses a mismatch.
* **Change dependencies** with `uv lock` (never by hand), then regenerate
  `Homayar/backend/requirements.txt` with
  `uv export --format requirements-txt --no-dev --frozen --no-hashes -o backend/requirements.txt`
  from `Homayar/`. `test_release_manifest.py` fails when the file does not list exactly the
  locked runtime packages.
* **Migrations**: one head only. The manifest parses `revision`/`down_revision` literals at the
  commit and refuses several heads; the tests compare the result with Alembic's
  `ScriptDirectory` and `backend.observability.release.code_migration_head()`.
