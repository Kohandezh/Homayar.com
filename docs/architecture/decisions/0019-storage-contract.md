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
