# ADR 0007 — Transactional outbox for job dispatch (at-least-once)

Date: 2026-09-08 · Status: accepted · Package: WP06-T02

## Context

The upload request committed meeting, recording, quota reservations and job,
then called `apply_async` and ignored the result. A broker outage, a crash
between commit and publish, or a dropped connection left a `queued` job that
no worker would ever pick up, with reserved quota. WP06-T01 fixed the queue
contract; this package fixes durability of the *intent* to publish.

## Decision

1. **Same transaction.** `create_upload_job` writes a `job_dispatches` row
   (`PENDING`, due now) together with the job. Nothing is published before the
   commit; if the transaction rolls back (quota, storage, integrity) neither
   job nor intent exists.
2. **One publisher, two triggers.** `dispatch_job` locks the row `FOR UPDATE
   SKIP LOCKED`, publishes to the declared `processing` queue, and marks the
   row `PUBLISHED` with the broker message id. The request calls it once as
   an optimisation; the sweeper (`sweep_dispatch_outbox`, Celery beat every
   `DISPATCH_SWEEP_INTERVAL_SECONDS`, or `python -m backend.jobs.outbox`)
   calls it for every due `PENDING` row. Competing publishers see `busy`.
3. **At-least-once, stated as such.** A failure after publish but before the
   mark leaves the row `PENDING` and the message is sent again. The consumer
   tolerates it: `process_job` claims the job row and returns early for
   running/terminal jobs; results and usage settlement are idempotent
   (existing tests, plus the duplicate-delivery test here). No exactly-once
   claim is made.
4. **Bounded retries, visible failure.** Publish errors back off exponentially
   (`DISPATCH_RETRY_BASE_SECONDS` … `DISPATCH_RETRY_MAX_SECONDS`) up to
   `DISPATCH_MAX_ATTEMPTS`, then the row is `FAILED` and the job API exposes
   `dispatch.state`, attempts and `last_error`. The job itself is never lost.
5. **Cancellation.** Deleting a meeting cancels the job and withdraws a
   pending intent (`CANCELLED`); a message already in flight finds a cancelled
   job and does nothing.
6. **Under RLS.** `job_dispatches` is tenant-owned; the dispatcher discovers
   due rows across tenants under the `dispatch` service scope, which the
   policy grants on that table only (ids and state, no customer content).
   The broker message may carry the workspace id as an *assertion* that the
   consumer re-checks against the persisted job (`verify_job_payload`); the
   persisted row, never the message, binds the processing tenant.
7. **Backfill.** Migration `e5a7c3d9b1f2` creates one due `PENDING` row per
   job still `QUEUED`, so work accepted before this release is recovered by
   the first sweep.

## Consequences

- Deployments need one Celery beat replica (`scheduler` service in the
  compose files) or an external invocation of the sweep; without either,
  recovery is manual.
- Leases, fencing and a sweeper for *abandoned running* work (WP06-T04),
  cancellation/deadline/dead-letter policy (WP06-T05) and idempotent usage
  events under redelivery (WP06-T06) are not covered by this ADR.
- Verified locally with the in-memory Celery transport and a real worker
  through the outbox; the Redis variant of the smoke test runs only where
  `TEST_REDIS_URL` is set (CI), so broker-specific behaviour remains
  unverified here.
