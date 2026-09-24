# ADR 0009 — Failure classes, retry budgets, dead-letter parking and operator replay

Date: 2026-09-09 · Status: accepted · Package: WP06-T05

## Context

After WP06-T04 a job could not be lost or double-owned, but *why* it stopped
was implicit: `ProcessingError.retryable` was a boolean, a job whose outbox
publication was exhausted stayed `queued` forever with `dispatch.state =
failed` and both quota reservations still held, retries used one fixed delay
with no overall deadline, the only exit from `dead_letter` was a hand edit
of `processing_jobs`, and the API could not tell a permanent input failure
from an exhausted retry budget or a cancellation.

## Decision

1. **One failure vocabulary, persisted.** `processing_jobs.failure_class`
   records why the job stopped: `publication` (the outbox could not deliver
   it), `transient` (a retryable execution failure: provider, storage,
   stage/attempt timeout), `permanent` (input or policy; retrying cannot
   help), `abandoned` (the lease lapsed), `deadline` (the job's overall
   deadline passed), `cancelled` (meeting deleted). `failure_code` keeps the
   specific cause. Partial results are not a failure: the job completes and
   the meeting is `partial` (ADR 0003, unchanged).
2. **Terminal representation.** `failed` is final and only for `permanent`.
   `dead_letter` parks everything that *could* have succeeded — exhausted
   publication, exhausted transient/abandoned attempts, deadline — and is the
   only replayable state. `cancelled` is final. Parking always drops the
   lease, releases every still reserved allowance (a parked job never holds
   or charges quota), sets the meeting to `failed` and writes one audit event
   (`meeting.processing.failed`, or `.cancelled`) with the class, the cause
   and a `terminal_reason`.
3. **Bounded budgets.** `JOB_MAX_ATTEMPTS` execution attempts per replay
   window; exponential back-off between attempts (`JOB_RETRY_DELAY_SECONDS`
   doubling up to `JOB_RETRY_MAX_DELAY_SECONDS`) carried by the same outbox
   row; one overall `deadline_at` per job (`JOB_DEADLINE_SECONDS` from
   creation, renewed on replay) enforced at claim time, when a retry is
   scheduled and when an abandoned attempt is recovered — a retry that would
   start after the deadline is not scheduled. Publication attempts
   (`job_dispatches`), execution attempts (`attempt_count`), stage rows and
   the single billable settlement remain separate counters.
4. **Publication exhaustion parks the job atomically.** When the publisher
   marks an intent `failed` it dead-letters the job in the same transaction
   (`failure_code = publication_failed`), binding job context and then the
   job's own tenant from the persisted row; the `dispatch` scope alone still
   cannot touch jobs. No second retry loop and no best-effort direct publish.
5. **Cancellation is one helper.** Meeting deletion (and recovery of an
   abandoned attempt whose meeting is gone) cancels through
   `backend.jobs.failures.cancel_job`: lease dropped, reservations released,
   pending publication withdrawn, class `cancelled`.
6. **Replay is the only way out of `dead_letter`** — `POST
   /api/v1/admin/jobs/{id}/replay`, `workspace:manage` + CSRF, audited as
   `meeting.processing.replayed` with the actor and the previous failure. It
   is bounded (`JOB_MAX_REPLAYS` per job; a fresh `max_attempts` budget *on
   top of* the recorded attempts so stage history and attempt numbers are
   kept; a new deadline), policy-preserving (quota is reserved again under the
   workspace's current entitlement — nothing is charged twice, a replay that
   does not fit is refused with nothing written; a deleted meeting, a
   recording that is no longer stored or an expired retention are refused, so
   deleted resources are never resurrected) and fencing/outbox compatible
   (the lease generation advances so nothing from the parked attempts can
   write; the job's own outbox row is re-armed for the new generation and
   published by the shared publisher). `failed` (permanent) and `cancelled`
   jobs are not replayable; the customer uploads again.

## Consequences

- API: `JobResponse` gains `failure_class`, `deadline_at`, `replay_count`,
  `last_replayed_at`; `JobReplayResponse` returns the requeued job and the
  publisher outcome. Refusals: `409 job_not_replayable | replay_limit_reached
  | meeting_deleted | retention_expired | recording_missing`, `429
  audio_quota_exceeded | concurrent_job_limit_reached`, `404 job_not_found`.
- Migration `a7d2c4e9f6b1` adds the four columns and `ck_job_replays`,
  classifies pre-existing terminal rows from their state and code, leaves
  open rows without a deadline (NULL = none). Downgrade drops the columns; a
  replayed job keeps its extended `max_attempts`.
- Behaviour changes: a job whose publication is exhausted is now
  `dead_letter` (was `queued`) and its reservations are released; retry
  delays grow (30 s, 60 s, 120 s … ≤ 600 s by default); a job older than
  `JOB_DEADLINE_SECONDS` (24 h) is parked instead of retried.
- The audit sanitizer redacts any detail key containing `code`; the cause is
  therefore also recorded under `cause`, and replay details are flat
  (`previous_failure`, `previous_class`, …).
- Not covered here: attempt-scoped staging of object outputs (WP06-T03),
  stable usage-event identity under redelivery and reconciliation (WP06-T06),
  fault injection (WP06-T07), and the empty-transcript charging policy
  (ADR 0003, still an open business decision).

## Evidence

Product commit recorded in `infra/product-source.lock`; `backend/tests/
test_job_failure_policy.py` (back-off and exhaustion, permanent failure,
deadline at retry time / claim time / recovery, publication exhaustion parks
atomically and replay runs the job with a single settlement on the new
reservation, replay bounded and refused for queued/running/completed,
quota/recording/retention/deleted-meeting refusals leave the job untouched,
cross-workspace job invisible, member denied / CSRF required / actor audited,
deletion cancels with the class, attempts from before a replay cannot write
after it, settings validation) on PostgreSQL 16 and the SQLite fallback;
migration upgrade / `alembic check` / downgrade / upgrade on both.
