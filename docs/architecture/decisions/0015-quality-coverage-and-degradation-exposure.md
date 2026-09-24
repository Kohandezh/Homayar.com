# ADR 0015 — Quality, coverage and degradation exposed as one assessment

Date: 2026-09-11 · Status: accepted · Package: WP04-T06 (closes WP04 in local scope)

## Context

WP04-T01 made stage outcomes truthful (ADR 0003), T02 made transcripts
immutable and analyses traceable, T03 bound findings to evidence (ADR 0014).
What was still missing was a single, consistent statement of *how good* a
meeting's results are — across the API, the console, the shared view, the
exports and the audit trail — so that a partial or degraded result can never
look complete on one surface while another surface says otherwise (master
prompt §37: transcription COMPLETE/PARTIAL/UNAVAILABLE, speaker attribution
VERIFIED/INFERRED/UNKNOWN, evidence SUFFICIENT/PARTIAL/INSUFFICIENT, analysis
COMPLETE/PARTIAL/UNAVAILABLE; §57 no fake features; §58 no fake progress).

## Decision

1. **One assessment, derived only from persisted facts.**
   `backend/services/quality_service.py` computes `MeetingQuality` from the
   current transcript revision, the current analysis run, the stored evidence
   statuses of the summary and the action items, the stage runs of the latest
   attempt, the segments' time ranges, the speaker rows and the usage
   reservation of the latest job. The pure function `assess_quality` takes
   those parts; `assess_meeting` loads them from the database. The processor
   calls the pure function on its in-memory rows at completion so the audit
   event carries the same states the API will report.
2. **States and rules.**
   - `transcription`: `unavailable` when there is no current revision or it
     has no segments (the empty-transcript outcome is persisted as an empty
     current revision, so stale text never stands in); `partial` when the ASR
     stage ended `partial`; otherwise `complete`.
   - `speaker_attribution`: `inferred` when the revision's diarization was
     available (labels come from an engine), otherwise `unknown`. `verified`
     is reserved for a person confirming the mapping (WP14) and is never
     claimed today — renaming a speaker does not verify attribution.
   - `analysis`: from the current run's stage state — `complete`, `partial`,
     or `unavailable` (skipped/unavailable/failed/none). `analysis_stale` is
     true when the current revision is newer than the one the run read (a
     correction after analysis); the results stay, labelled.
   - `evidence`: the aggregate of the per-finding statuses (summary overview
     and every action item): all `sufficient` → `sufficient`; any resolved →
     `partial`; none (or no findings) → `insufficient`.
   - `speech_coverage_ratio`: covered milliseconds of the current revision's
     segments over the recording duration, clipped to 1; null without a
     duration.
   - `degradations`: every stage of the latest attempt that did not complete,
     with its state and reason code; `is_mock` when the revision or the run
     came from development providers; `usage`: the reservation state and the
     committed audio seconds of the latest job — exposed as a fact, not a
     charging decision (the empty-transcript charging policy remains an open
     business decision; today the audio is charged as processed, and the
     assessment shows it).
3. **Surfaces.** `GET /meetings/{id}` embeds `quality`;
   `GET /meetings/{id}/quality` returns it alone (`meeting:read`); the
   shared-meeting response and export embed it *without* the job-derived
   parts (stage degradations and usage) because jobs, stage runs and usage
   reservations lie outside the share ceiling; the JSON export copies it verbatim; the
   text export renders a `Quality` block; the WebVTT export carries a `NOTE`
   block; SubRip has no comment syntax and carries nothing (documented). The
   completion audit event carries `asr_state`, `speaker_state`,
   `analysis_state`, `evidence_state` and `speech_coverage_ratio` (the
   audit sanitizer redacts keys containing `transcript`, hence `asr_state`).
   The console renders the assessment as a quality panel on the meeting
   detail and the shared page, and an evidence badge on the summary, each key
   point and each action item — from the API's values, never inferred
   client-side.
4. **Usage eligibility.** The assessment exposes what was charged; it does
   not change settlement. Changing what an empty or degraded outcome costs is
   a business decision recorded in WP06/WP18, not a side effect of this ADR.

## Consequences

- WP04 completion conditions (master prompt §66) in local scope: transcript
  revisions persist (1); evidence survives reprocessing and corrections,
  bound to the immutable revision (2); audio offsets exist at segment level
  wherever recognition produced them (3); findings reference evidence with a
  status (4); human correction is a new audited revision (5); the API reports
  quality state on the detail, quality, shared and export surfaces (6);
  exports preserve revision, analysis and evidence provenance (7); no
  fabricated fallback — every degraded state is named (8); tenant isolation
  and the share ceiling hold for the new tables (9); PostgreSQL tests pass
  (10); migration validation passes (11). WP04 is therefore VALIDATED_LOCAL,
  not production-validated: remote CI, the deployed stack and real providers
  have not run.
- Not decided here: word-level and sub-segment spans (WP10), human speaker
  verification (WP14), quality thresholds that gate downstream automation
  (WP11/WP24), charging rules for degraded outcomes (WP18).
