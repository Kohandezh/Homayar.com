# ADR 0011 — Stage checkpoints with input-hashed identity and attempt-scoped artifacts

Date: 2026-09-10 · Status: accepted · Package: WP06-T03

## Context

Processing runs four stages (materialise, ASR, diarization, analysis) under
an execution lease (ADR 0008) and, until now, kept every intermediate result
in the worker's memory: a retry after a transient failure in the analysis
stage, or a recovered attempt after a worker died, transcribed the same audio
again and called the same providers again. The worker wrote no objects, so
ADR 0008 could defer the rules for non-transactional artifact writes. Those
rules are needed now: a completed stage result must survive its attempt,
must be reusable only when it is genuinely the same computation, must never
be published by an attempt that lost its lease, and must not leave objects
behind that nobody can find or that cleanup could confuse with a current
result. Bookkeeping (ADR 0010) must stay exactly-once: a reused stage is not
a new provider call and a resumed job is not a new billable meeting.

## Decision

1. **A checkpoint is a committed stage result, not an object.** The new
   table `stage_checkpoints` records, per stage of one attempt, the declared
   artifact (`artifact_key`), its verified digest and size, its schema and
   version, the provider identity that produced it, and the execution
   identity (`attempt`, `attempt_id`, `lease_generation`). Its lifecycle is
   `PENDING` (declared before the object is written) → `PUBLISHED` (accepted
   by the fenced transaction) or `ABANDONED` (never published) and, later,
   `INVALIDATED` (published but unusable: artifact missing or corrupt,
   meeting deleted, superseded). Only `PUBLISHED` rows are ever reused; an
   object on disk is not a checkpoint.
2. **Identity is a hash over declared inputs, versioned.** `input_hash` is
   the SHA-256 of a canonical JSON manifest (sorted keys, no whitespace):
   contract version; workspace and meeting scope; stage, artifact schema and
   schema version; the recording SHA-256 and size plus the materialisation
   version; the provider's `ProviderIdentity` (provider, model and its
   result-affecting parameters — Whisper compute type, the OpenAI-compatible
   endpoint, temperature, prompt version and prompt digest, the mock's
   transcript digest — never a credential); the request parameters
   (language, beam size, VAD, expected speaker count); and, for the analysis
   stage, the digests of the ASR and diarization artifacts it consumed and
   the turn-building version. Attempt ids, worker ids, generations,
   heartbeat times and latencies are execution identity and are excluded.
   The manifest is stored beside the hash so an operator can see *why* a
   result was or was not reused. Matching hashes prove matching declared
   inputs, not model quality. A provider adapter that declares nothing more
   than its code path is recorded as `model = "unversioned"` — an honest
   name for that code, never a mutable label such as "latest".
3. **Reuse requires every condition.** A stage is resumed from a checkpoint
   only when reuse is enabled (`JOB_CHECKPOINT_REUSE_ENABLED`), the row is
   `PUBLISHED` with a matching identity, schema and version, the meeting and
   recording are alive and the recording still carries the digest in the
   manifest, and the artifact reads back within `JOB_ARTIFACT_MAX_BYTES`
   with the recorded size and SHA-256 and validates against the stage schema
   (strict JSON object → pydantic model; nothing is executed or unpickled).
   A missing or corrupt artifact invalidates the row under the fence and the
   stage is recomputed; storage that does not answer is "recompute now", not
   evidence of absence. Only completed results are checkpointed: an empty
   transcript and an unavailable diarization are truthful outcomes (ADR
   0003) that a later attempt asks about again.
4. **Downstream invalidation is inherent.** Changed audio changes every
   stage's manifest; a recomputed transcript changes the analysis manifest
   through its upstream digest while an unchanged transcript keeps it; a new
   prompt, model or endpoint changes only the analysis identity. A
   diarization checkpoint depends on the audio and its provider, not on the
   transcript, so a new ASR model recomputes ASR and analysis but reuses
   diarization. Distinct identities coexist as distinct published rows; a
   newer verified result for the *same* identity supersedes the older row
   in the publishing transaction, so the partial unique index
   `uq_checkpoint_published_identity` (meeting, stage, input hash where
   PUBLISHED) always holds exactly one authority.
5. **Attempt-scoped, idempotent artifact writes.** Objects go through the
   storage abstraction under a caller-chosen token `art-<attempt id>-<stage>`
   (validated: one path segment below the tenant prefix, so no traversal and
   no cross-tenant collision). The same token with identical content is
   idempotent (nothing rewritten, the stored metadata is returned); different
   content is `object_conflict`; an object whose earlier write never
   completed is `object_incomplete`. The local adapter publishes by hard
   link and never overwrites; the S3 adapter uses a conditional create
   (`If-None-Match: *`) and never deletes a caller-chosen key on an
   ambiguous failure, because it may be another attempt's completed object.
6. **Commit sequence per stage.** (1) declare the intent as a `PENDING` row
   in a fenced transaction — an attempt that already lost its lease writes
   no object at all; (2) write the object; (3) read it back and verify size,
   digest and schema; (4) re-lock the job, prove the lease, and in one
   transaction mark the row `PUBLISHED` and record the provider usage of the
   call that was made. No database lock is held across the provider call or
   the object write. A stale attempt that already wrote its object is
   rejected at (4) and leaves an unreferenced object; it can never publish
   it. A cancelled job (meeting deleted) is refused at (1) and (4).
7. **Resume without duplicate effects.** The results transaction of ADR 0008
   remains the single authoritative publication of transcript, summary,
   actions and the one settlement; checkpoints only remove recomputation.
   Provider usage of a checkpointed stage is recorded when it is published,
   so a reused stage records no call, and the results transaction records
   usage only for stages that ended without a checkpoint. Stage runs carry
   `checkpoint_id` and `reused` (exposed on the job API with the attempt
   number), and the completion audit lists the reused stages. Replay (ADR
   0009) and recovery (ADR 0008) are unchanged: a replayed or recovered
   attempt resumes from the first stage without a valid checkpoint.
8. **Deletion and cleanup are conservative.** Deleting a meeting invalidates
   its checkpoints in the deletion transaction and removes their objects
   afterwards; a cancelled job cannot recreate them because both the intent
   and the publication require ownership of a RUNNING job. The cleanup sweep
   (`stage-artifact-cleanup`, `python -m backend.jobs.checkpoints`) removes
   objects only for rows that are not `PUBLISHED` or belong to a deleted
   meeting, older than `JOB_ARTIFACT_GRACE_SECONDS`, and — for `PENDING`
   rows — whose job is not RUNNING at that row's generation. A published
   artifact of a live meeting is never removed by it; storage that does not
   answer leaves the row untouched.
9. **Tenant boundaries.** `stage_checkpoints` is a workspace table under the
   tenant policy (no service scope reads it). Discovery for cleanup and for
   the invariant checker uses the existing read-only `usage_reconciliation`
   scope on entitlements (workspace ids only); every inspection or deletion
   then runs tenant-bound. Artifacts are private objects behind the existing
   storage policy; no share or download path was widened.
10. **Invariants.** The read-only checker gains `checkpoint_state` (a
    published row carries its verified digest, was published by a
    generation the job has seen, belongs to a live meeting, is unique per
    identity, and stage runs reference checkpoints of their own meeting) and,
    only when given a storage handle, `artifact_missing`. Object evidence is
    reported as checked / missing / unverified / orphans / retired so an
    unreachable store is never reported as a missing object. Nothing is
    repaired automatically.

## Consequences

- Retries and recovered attempts stop paying for stages that already
  completed: in the validated scenarios a failed analysis retries with one
  ASR call and one diarization call in total, and a crash after all three
  checkpoints resumes with no provider call at all.
- Storage now holds worker-written objects. Deployments need the beat
  scheduler for `stage-artifact-cleanup` (or the CLI), and
  `JOB_ARTIFACT_GRACE_SECONDS` must not be shorter than the lease. Migration
  `c8d1e5f7a2b4` creates the table and columns; downgrade drops them and
  leaves objects unreferenced.
- Stale attempts may leave unreferenced objects for up to the grace period;
  the invariant checker counts them as orphans, not as violations.
- A checkpoint from a provider adapter with the default code identity is
  reusable only by the same adapter class; production adapters declare model
  and parameters. Changing the analysis prompt or output schema changes
  `PROMPT_VERSION` / the digests and therefore only the analysis identity.
- The empty-transcript charging policy (ADR 0003) is untouched: an empty
  transcript is not checkpointed and is settled exactly as before.
- Verified on PostgreSQL 16.15 as `homayar_app` (one worker):
  `test_stage_checkpoints.py` (18 — identity determinism and exclusion of
  execution metadata; reuse of committed stages with one recomputation;
  ASR-model change recomputing ASR and analysis but reusing diarization;
  analysis-contract change recomputing only the analysis; crash after the
  checkpoints resuming with no provider call and one settlement; missing
  and corrupt artifacts invalidated and recomputed; bound and schema
  enforcement and cross-tenant key rejection on load; reuse disabled
  superseding the old row; incomplete write failing the stage as transient
  with an ABANDONED row and no object; crash between object write and
  publication leaving a PENDING row cleaned only after the grace period
  and never the winner; cleanup skipping a live lease; idempotent writes
  and conflicts; stale worker superseded with only the winner's objects;
  conflicting attempt refused by the fence and a second PUBLISHED row
  refused by the index; deletion racing publication leaving no artifact;
  deletion retiring checkpoints and objects; RLS hiding and refusing
  another tenant's rows; API provenance), storage token tests (local and
  fake conditional S3), and `test_fault_injection.py` artifact-write faults
  converging with one charge per meeting plus the artifact evidence of the
  checker. Full suite 208 passed on PostgreSQL, 186 passed + 22 skipped on
  SQLite. Real S3 conditional writes and a Redis-backed transport remain
  unverified.
