# ADR 0019 — One storage contract suite, distinct failure outcomes and the bounded single-request upload

Date: 2026-09-16 · Status: accepted · Package: WP05-T05

## Context

`ObjectStorage` (ADR 0017/0018) is implemented twice — a local adapter with
atomic publication and an S3-compatible adapter — and is read by the upload
route, the download service, the stage checkpoints, the media lifecycle
service and the reconciliation sweep. Each adapter had focused tests, but no
single statement of what *every* backend guarantees, and two classifications
were wrong in ways the callers could not see: the S3 adapter treated any
HTTP 404 as "object missing" (a `NoSuchBucket` response would have let
`verify_integrity` flag live rows as `MISSING` and `exists()` answer `False`
while the store itself was misconfigured), and the local adapter reported a
permission or I/O fault on `open`/`lstat`/`unlink` either as an invalid key
or as a raw `OSError`. WP05-T05 asks for one supported S3/private
implementation to be qualified through contract tests, including the upload
path (multipart) and access denial. A real S3-compatible bucket is not
available to this environment.

## Decision

1. **One suite, three targets, no second abstraction.**
   `backend/tests/test_storage_contract.py` runs the same guarantees against
   the real `LocalObjectStorage` on a disposable directory, `S3ObjectStorage`
   over a controlled fake client, and — opt-in only — a designated
   disposable S3-compatible bucket (`backend/tests/_live_s3.py`:
   `HOMAYAR_LIVE_S3_TEST=1` plus bucket, tenant prefix and keys; small
   fixtures; exact created keys removed afterwards; SDK loggers muted). The
   live parametrization is skipped by default and is reported as
   NOT_RUN / BLOCKED_EXTERNAL, never as a pass; fault-injection tests skip
   the live target rather than falling back to the fake. Fake-client results
   are adapter-contract evidence, not evidence about cloud IAM, bucket
   policy or a particular S3-compatible service.
2. **Outcomes stay distinct.** *Missing* (`ObjectNotFoundError`,
   `exists() is False`, `delete() is False`) is reserved for a 404 that is
   not a bucket-level code. *Denied*, *transient* and *unavailable* surface as
   `StorageError` with the operation's stable code (`s3_read_failed`,
   `s3_head_failed`, `s3_delete_failed`, `s3_list_failed`,
   `s3_upload_failed`; `local_read_failed`, `local_stat_failed`,
   `local_delete_failed`, `local_list_failed`, `local_purge_failed`). A
   missing or misconfigured bucket (`NoSuchBucket`, `PermanentRedirect`) is
   `s3_bucket_unavailable`. On the local adapter only `ELOOP`, `ENOTDIR`,
   `EISDIR` and `ENAMETOOLONG` mean an invalid key. `exists()` never answers
   `False` on a fault; a failed deletion preserves the object for the retry.
   Existing codes are unchanged; the two new ones are additions.
3. **Scope before any backend call.** Another tenant's key, traversal, extra
   segments, invalid tokens, backslashes, absolute keys and a wrong prefix
   are refused with no client operation issued; invalid tenant ids are
   refused for write, listing and purge, and the default `purge_incomplete`
   now validates the tenant so a missing tenant context can never become a
   global purge.
4. **The upload is one bounded `PutObject`, proven on the real SDK path.**
   A real boto3 client under `botocore.stub.Stubber` records every API
   operation the adapter issues: the size cap is enforced while streaming
   (an oversized or checksum-mismatched input issues no request),
   unknown-length input is spooled to a bounded temporary file and sent with
   the exact `ContentLength`, caller-chosen tokens use `If-None-Match: *`
   and a 412 is resolved by comparing the recorded SHA-256 (a conflict
   preserves the existing object; nothing is deleted), a failed generated-key
   upload is cleaned up while a failed token upload never deletes, and no
   multipart operation or transfer helper (`upload_fileobj`,
   `TransferConfig`) is ever used. The S3 ETag is never treated as the
   digest. Single-object retries by the SDK are not disabled and are not
   confused with multipart: the proof counts API operations by name, not
   HTTP attempts.

## Consequences

- Callers that already catch `StorageError` (checkpoints, reconciliation)
  now see bucket-level and local filesystem faults as retryable storage
  errors instead of raw exceptions or false "missing" answers; the
  reconciliation sweep counts them as `unavailable` and never flags
  `MISSING` for them.
- The live run remains the owner's decision: credentials alone are not
  authorization, and the target must be disposable. Until it runs, T05 is
  `partial` (adapter contract validated locally; live qualification
  BLOCKED_EXTERNAL).
- The local adapter still lets a filesystem fault during a *write*
  (`mkstemp`, hard link) propagate as `OSError`; no object is ever published
  in that case and temporaries are removed, so the invariant holds, but the
  code is not a `StorageError` (recorded in the authorization matrix §8).

## Amendment (2026-10-23, HOM-36): MinIO qualification

The suite now has a fourth target, `s3-minio` (marker `minio_s3`). It runs
`S3ObjectStorage` with a real boto3 client against a disposable local MinIO
container. `infra/ci/minio-contract-target.sh up <port> <env-file>` starts it on
`127.0.0.1:<port>` and bootstraps it like `minio-bootstrap` in
`infra/compose/compose.onprem.yaml`: a versioned bucket, anonymous access `none`,
and an app user with the same `homayar-app` policy. A static test fails when the
script's policy drifts from the Compose one. The script also adds a user with no
policy and a user whose policy covers only a bucket that is never created. It
generates every credential fresh on each run, checks versioning and anonymous
access with the root key, and writes only the user keys to the env file
(`backend/tests/_minio_s3.py` reads it and accepts only a loopback endpoint).

Images: the official `quay.io/minio` images now need a login and the Docker Hub
copies are gone. The script uses `bitnamilegacy/minio:2025.4.22` and
`bitnamilegacy/minio-client:2025.4.16`, pinned by digest. They run the same
MinIO and mc commits as the Compose defaults
(`RELEASE.2025-04-22T22-12-26Z`, `RELEASE.2025-04-16T18-13-26Z`).

What MinIO proves: every shared case except fault injection, plus real
decisions. A user with no policy is refused for write, read, HEAD, delete and
listing (`AccessDenied` / 403, never "missing"). Unsigned, expired and tampered
GETs answer 403 while a valid presigned GET answers 200. The app policy cannot
create buckets, set a bucket policy or reach another bucket. A bucket outside the
policy is a 403 `StorageError`, never "missing". An aborted multipart upload
leaves no readable, listed or pending object. The adapter itself issues no
multipart operation.

CI: the backend job runs the step "Qualify the storage contract against MinIO"
before the full test step, with `HOMAYAR_REQUIRE_MINIO_TESTS=1`, so the run
stops with a usage error instead of skipping. The credentials stay in that
step's shell. A cleanup step with `if: always()` runs `down`.

Still NOT_RUN on MinIO: the 12 cases that call `require_faults()`, because a
real endpoint cannot inject faults. They are:

- `test_read_faults_are_not_reported_as_missing` (deny, transient);
- `test_metadata_faults_never_answer_false_for_exists` (deny, transient);
- `test_delete_faults_preserve_the_object_for_a_retry` (deny, transient);
- `test_listing_faults_surface_as_storage_errors` (deny, transient);
- `test_listing_is_lazy_so_a_budget_bounds_the_backend_work`;
- `test_listing_skips_entries_that_are_not_owned_objects`;
- `test_purge_incomplete_removes_only_stale_leftovers`;
- `test_provider_denies_the_write_and_nothing_authoritative_remains`.

The injected faults are deny and transient only. Real denial and a real missing
bucket are covered by the MinIO-only cases instead.

Known gap, not fixed here: `exists()` and `delete()` on a bucket that does not
exist answer `False`. HEAD has no body, so botocore reports code `404` and the
adapter reads it as a missing object, against decision 2. The case is marked
`xfail(strict=True)` and turns red when the adapter is fixed in a follow-up.
`open_reader` and `iter_objects` already answer `s3_bucket_unavailable`.

Not proven: an external S3 provider and cloud IAM. `s3-live` stays opt-in and
BLOCKED_EXTERNAL.

Local run, from `Homayar/`:

```bash
ENVF="$(mktemp -d)/minio.env"
../infra/ci/minio-contract-target.sh up 4360 "$ENVF"
( set -a; . "$ENVF"; set +a; HOMAYAR_REQUIRE_MINIO_TESTS=1 \
  uv run pytest backend/tests/test_storage_contract.py -m minio_s3 -v -rs )
../infra/ci/minio-contract-target.sh down 4360
```
