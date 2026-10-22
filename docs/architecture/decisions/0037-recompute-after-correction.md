# ADR 0037 — Recompute the analysis after a transcript correction

Date: 2026-10-21 · Status: proposed (HOM-63, parent HOM-16, feature F45) · Builds on: ADR 0014, ADR 0015, ADR 0024, ADR 0030, ADR 0034, ADR 0036

## Context

A person corrects a transcript with `POST /meetings/{meeting_id}/transcript/corrections`.
`correct_revision()` appends a new current revision (source `correction`) and its segments,
and the route audits `transcript.revision.created`. Nothing else happened. The analysis that read
the old revision stayed current forever, so the summary, the AI action items and the evidence
described a transcript the person had just fixed.

What already existed:

- *Stale is derived, not stored.* `assess_quality()` reports `analysis_stale` when the current
  analysis run read another revision than the current one, and the report adds the
  `analysis_stale` notice. Evidence refs carry `is_current_revision`.
- *Dependent outputs:* `analysis_runs` (append-only, one current per meeting),
  `meeting_summaries` (replaced, linked by `analysis_run_id`), AI `action_items` (AI rows
  replaced, a person's rows kept), `evidence_spans` (immutable, bound to a revision and a run) and
  the derived text view (ADR 0034, per revision).
- *Search* reads the current segments live (`current_segments_clause`), so it is already right
  after a correction.
- *The full job is the wrong tool.* `process_job` always reruns ASR and calls
  `create_revision(source=ASR)`, which would replace the person's correction. The intelligence
  checkpoint identity does not name the revision it read, so a stage checkpoint would hand back
  the old analysis. `ProcessingJob` needs a usage reservation and has no "kind" column.

## Decision

1. **A recompute unit, not a job.** `backend/jobs/recompute.py` holds
   `recompute_meeting_analysis(meeting_id, target_revision_id) -> outcome`, with the outcomes
   `completed`, `up_to_date`, `superseded`, `no_prior_analysis` and `failed`. The Celery task
   `backend.jobs.recompute_meeting_analysis` (processing queue) runs it under the correlation id
   `recompute:<meeting id>:<delivery>`. It opens its own sessions, like `process_job`.
2. **Only the stages that read text.** The recompute derives the text view of the target
   revision (and its redacted version under a text policy), runs the intelligence pass over the
   target revision's segments, builds the evidence plan, appends a new current analysis run, and
   replaces the summary and the AI action items. It never fetches media, reruns ASR or
   diarization, writes a transcript revision, a stage checkpoint, a `ProcessingJob` or a usage
   reservation, and never changes `Meeting.status`.
3. **The full job's rules, shared code.** The processor's analysis code was split so both paths
   call it: `analysis_candidates` (the text each eligible candidate may receive),
   `call_analysis` (rank order, circuit, request window, egress permit, same-policy fallback),
   `derive_revision_views` and `persist_analysis` (evidence plan, run, summary, action items).
   The recompute therefore sends the provider the same input view (original or redacted under
   the policy) that the intelligence stage would choose today and records `input_view` and
   `input_policy_digest` on the run the same way. A policy that refuses analysis gives an
   `unavailable` run, as in the full job.
4. **Provider call without a lock, writes under the lock.** Before writing, the recompute takes
   the meeting row lock (`FOR NO KEY UPDATE`) and the lock `correct_revision` takes on the
   current revision row, then checks again: a target that is no longer current is `superseded` (only the audit event is
   written); a current run that already read the target is `up_to_date` (nothing is written).
   Two recomputes of one target therefore write one run, and a newer correction's recompute wins.
   The meeting lock is `FOR NO KEY UPDATE`, not `FOR UPDATE`: a correction holds the revision
   lock and then inserts its revision, whose foreign key takes `FOR KEY SHARE` on the meeting.
   `FOR UPDATE` conflicts with that and deadlocked; `FOR NO KEY UPDATE` does not, and it still
   waits for a tombstone (an `UPDATE` of the meeting row).
5. **Failure is recorded, not hidden.** A provider or processing error appends a non-current
   `analysis_runs` row with `state=failed`, the target revision and a `reason_code`. The current
   run, the summary and the action items stay as they were.
6. **Status, derived.** The quality response (`/quality`, the report's `qa.quality`, the meeting
   detail, shares and exports) gains `recompute_status`: `up_to_date` (not stale), `pending`
   (stale, fewer failed runs for the current revision than `recompute_max_attempts`) or `failed`
   (stale, the cap reached), and `recompute_failure_code` (the newest failed run's reason, only
   when `failed`). When `failed`, the completeness notices also hold `analysis_recompute_failed`.
   No existing field changes meaning; the ingestion contract moves to 1.2.0 (additive).
7. **Three triggers.**
   - *Correction:* after the correction commits, the route dispatches the recompute, unless
     `recompute_on_correction` is false. The request is audited in the correction's transaction.
     A failed dispatch never fails the correction.
   - *Sweeper:* the beat task `backend.jobs.sweep_stale_analyses` (every
     `recompute_sweep_interval_seconds`, heartbeat `analysis_recompute`) dispatches every stale
     meeting whose current revision has fewer failed recomputes than the cap. It covers a lost
     dispatch.
   - *Manual:* `POST /api/v1/meetings/{meeting_id}/analysis/recompute` with `meeting:update`
     (+CSRF or API key), `404` for another workspace's meeting. Not stale: `200 up_to_date`, no
     rows. Stale: `202` with the status after the dispatch. A person may ask after `failed`; that
     runs one more attempt. Every request counts against the workspace's
     `recompute_manual_rate_limit_per_minute` (default 10), an `up_to_date` answer too; over it
     the route answers `429 recompute_rate_limited` with `Retry-After`, in the upload route's
     error shape, and writes nothing.
   - `recompute_on_correction=false` turns off all automatic recompute: the correction does not
     dispatch and the sweeper dispatches nothing. Only the manual route recomputes then.
8. **Audit.** Four `processing` events with target `meeting`: `meeting.recompute.requested`
   (detail `trigger`: `correction` or `manual`, with the person as actor),
   `meeting.recompute.completed`, `meeting.recompute.failed` and `meeting.recompute.superseded`
   (system events, no actor). Each is written with `record_audit_event` in the transaction of the
   rows it describes. Details hold ids, counts and enum values, never transcript text. The audit
   sanitizer redacts every key that contains `code`, so no detail key contains `code`: the
   failure reason is stored as `cause` (the processor does the same).

## Why no migration

Everything fits the existing tables. The status is derived from `analysis_runs` (failed,
non-current runs on the current revision), so no stored flag or counter is needed. A failed run
is an ordinary append-only row: its `job_id`, `attempt` and `checkpoint_id` are null, which the
columns already allow. Recompute runs carry `job_id = null`, which tells them apart from runs of
the full job.

## Not built

- **Scores.** No meeting scores exist yet (only demo seed values), so there is nothing to
  recompute. When scores arrive, they belong in `persist_analysis` so both paths write them.
- **A search projection.** Search reads the current segments live and is already right after a
  correction. A future projection (index, embeddings) must be rebuilt from the same trigger.
- **Provider usage for recompute calls.** A recompute writes no `provider_usage` row and touches
  no reservation (HOM-63 asks for no usage change). Recompute provider calls are therefore not
  metered yet.

## Risks

- **An operator replay of the full job replaces a human correction.** This is older than this
  ADR. `_persist_success` in `backend/jobs/processor.py` always calls
  `create_revision(source=TranscriptSource.ASR)`, so replaying a job of a corrected meeting makes
  the new ASR revision current and the correction becomes history. Nothing is lost (revisions are
  append-only), but the correction is no longer what readers see. A follow-up should make the
  replay keep (or re-apply) the latest correction, or refuse while one exists.
- **Duplicate work.** A sweep that runs while a dispatched recompute is still calling the
  provider dispatches the same target again. The lock and the recheck keep the result correct;
  the cost is one extra provider call.
- **Direct calls without a workspace.** `recompute_meeting_analysis` without `workspace_id` binds
  each workspace in turn to find the meeting (row-level security hides it otherwise). Every
  trigger passes the workspace; only a direct operator call takes this path.
