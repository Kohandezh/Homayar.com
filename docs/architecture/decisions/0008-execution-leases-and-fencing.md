# ADR 0008 — Execution leases, fencing generations and abandoned-attempt recovery

Date: 2026-09-08 · Status: accepted · Package: WP06-T04

## Context

After WP06-T02 a job could not be lost between commit and publication, but a
*running* attempt had no durable ownership: `locked_by`/`locked_at` were set
once at claim time, never renewed, and compared against the worker's own
clock; a second delivery could reclaim a job after a fixed timeout while the
first worker was still healthy; a worker that resumed after a pause could
still write results, complete the meeting and settle usage on top of a newer
attempt; nothing ever noticed a RUNNING attempt whose worker had died; and
retries used the broker's `task.retry`, a second retry loop outside the
outbox.

## Decision

1. **Ownership is persisted on the job row, not in a second state machine.**
   `processing_jobs` gains `lease_generation` (fencing token, advances on
   every claim and every recovery fence), `attempt_id`, `lease_expires_at`
   and `lease_renewed_at`; `locked_by` is the owner and `locked_at` the
   attempt's start (its deadline anchor). A check constraint makes "RUNNING
   without owner and lease" unrepresentable.
2. **Database time decides.** Claim, renewal, fencing and recovery compare
   against PostgreSQL `now()`; worker clocks do not participate.
3. **Atomic claim, fenced writes.** A claim locks the row `FOR UPDATE SKIP
   LOCKED`, takes its verdict (terminal / running with a live lease / lease
   expired / not yet due / attempts exhausted / claimable) and writes the new
   generation in the same transaction. Every protected write — stage
   transition, results, meeting completion, failure record, usage
   settlement — begins by re-locking the row and requiring
   `state = RUNNING ∧ same generation ∧ same owner ∧ lease not expired`;
   the lock is held to commit, so a stale attempt is rejected before its
   first row and never commits partially. The rejected attempt reports
   `superseded` and writes nothing.
4. **Renewal never resurrects.** The heartbeat (`LeaseHeartbeat`, every
   `JOB_LEASE_RENEW_INTERVAL_SECONDS`) extends the lease only under the same
   four conditions and never past `locked_at + JOB_ATTEMPT_TIMEOUT_SECONDS`.
   An expired lease is not renewed even if nobody has taken the job yet;
   reacquisition only happens through the recovery fence and a fresh claim,
   both of which advance the generation.
5. **Bounded attempts, one policy.** `LeasePolicy` (from settings) holds
   lease, renew interval, stage timeout (`asyncio.wait_for` around each
   provider call), attempt timeout, retry delay and the recovery batch size.
   Celery `task_soft_time_limit` / `task_time_limit` are the last bound for
   a provider that blocks the loop. Nothing else keeps timing constants.
6. **Retries and recovery re-arm the outbox.** A retryable failure requeues
   the job (`retry_after`) and re-arms its `job_dispatches` row for the
   current generation; the recovery sweeper fences an expired attempt
   (generation + 1, abandoned stage recorded as `failed/lease_expired`,
   earlier stages `completed`, later ones `not_reached`), re-arms the intent
   due now, and the ordinary publisher delivers it. The row records the
   `generation` it was armed for, so a duplicate scan cannot arm it twice.
   When the abandoned attempt was the last one, the job is parked
   `dead_letter` with `failure_code = lease_expired` and its reservations are
   released. There is no broker-level retry any more, so publication
   attempts, processing attempts, stage attempts and the one billable
   settlement per job are separate counters.
7. **Two sweepers, two responsibilities.** The dispatch sweep (T02)
   publishes due intents; the lease sweep (`backend.jobs.recovery`, beat
   entry `job-lease-recovery`, `python -m backend.jobs.recovery`) fences
   expired RUNNING attempts. Neither retries the other's work.
8. **Under RLS.** Discovery runs under a new read-only `recovery` service
   scope on `processing_jobs` only (ids, state, lease columns); the policy's
   `WITH CHECK` stays tenant-bound, so the scope cannot fence or requeue.
   Each recovery binds the job id and the workspace read from the persisted
   row, never from a message. Context remains transaction-scoped.
9. **Non-transactional side effects.** At this revision the worker wrote no
   objects; its only object side effect, deleting the recording after a
   successful run, is performed only by the attempt whose generation
   completed the job. (Stage artifacts written by WP06-T03 follow ADR 0011:
   declared under the fence, attempt-scoped keys, published only by the
   owning attempt.) A provider call already in flight when a lease lapses cannot be
   cancelled by the fence: the supplier operation may be repeated by the
   next attempt (bounded by `max_attempts`), but its result is rejected at
   the transaction boundary and the customer is settled at most once.
10. **Early delivery is not dropped.** A message that arrives before
    `retry_after` (clock skew) re-arms the intent for that time instead of
    consuming the only delivery.

## Consequences

- Deployments need the beat scheduler (compose `scheduler`) for both
  sweeps; without it, recovery is the CLI. Abandoned work resumes within
  `JOB_LEASE_SECONDS + JOB_RECOVERY_SWEEP_INTERVAL_SECONDS` of the last
  renewal (defaults 120 s + 60 s).
- Drain workers before upgrading to this revision: migration
  `f1b6d8e4a2c7` marks every RUNNING job's lease as already expired (the
  old code never renewed), so the first sweep fences it; a pre-lease worker
  still writing would not be fenced by its own code.
- `attempt_count` now also counts abandoned attempts; a job can reach
  `dead_letter` without any provider error. WP06-T05 owns the retry-budget
  and dead-letter policy (including operator replay); WP06-T06 owns
  usage-event identity under redelivery; WP06-T03 stage checkpoints and
  attempt-scoped artifacts are decided in ADR 0011, which extends (9).
- Verified on PostgreSQL 16.15 with one worker (`test_job_leases.py`:
  competing claims, renewal rules, heartbeat under a blocked stage,
  recovery + fence + re-arm, stale worker rejected for results/terminal
  status/failure/usage/audio deletion, resumed stale worker after recovery,
  late messages, deletion racing renewal/recovery, exhaustion, stage
  timeout, recovery-scope isolation and no context leak). Not verified:
  Redis transport, deployed beat, worker hard time limit in a real Celery
  pool, PgBouncer transaction pooling.
