# ADR 0014 — Immutable transcript revisions, analysis-run provenance and evidence spans

Date: 2026-09-10 (amended 2026-09-11 for WP04-T03) · Status: accepted · Package: WP04-T02, WP04-T03 (foundation for WP04-T06, WP11, WP14)

## Context

Until now a meeting had one mutable set of `transcript_segments`: every
processing outcome deleted and rewrote them (and the speaker rows, discarding
names a person had assigned), and nothing recorded which engine, model,
configuration or checkpoint produced the text or which text an analysis had
read. WP04 requires that a finding can always be traced to the exact
transcript it was made from, that a human correction never silently
overwrites the engine output, and that reprocessing keeps the old provenance
instead of erasing it. The master constraints: immutable revisioning, parent
revision, engine/model/config provenance, revision reason, auditable human
correction, tenant isolation, PostgreSQL and migration validation, no
fabricated fallback.

## Decision

1. **A meeting's transcript is its set of revisions.** `TranscriptRevision`
   rows are numbered per meeting (`uq_transcript_revision_number`), carry the
   `source` (`asr`, `correction`, `backfill`), a `reason`
   (`processing`, `reprocessing`, `human_correction`, `backfill`), an optional
   note, the language and the segment count, and provenance: the job and
   attempt, the ASR stage checkpoint, the stage input identity hash
   (`config_hash`, the same hash that decides checkpoint reuse), ASR
   provider/model, diarization provider/status and the mock label. A
   correction records the person (`created_by_user_id`) and points at the
   revision it was made from (`parent_revision_id`); an ASR revision has no
   parent because it derives from the audio, not from the previous text.
   There is no separate `Transcript` table: the meeting is the conversation
   and one revision set per meeting suffices until a conversation has more
   than one transcript (per channel), which would then justify it.
2. **Segments belong to a revision and are immutable.**
   `transcript_segments.revision_id` is mandatory; uniqueness is per
   `(revision_id, sequence)`. SQLAlchemy `before_update` guards raise
   `TranscriptImmutableError` for any column change of a segment and for any
   change of a revision or analysis run other than the *current* pointer
   (`is_current`, `superseded_at`). Nothing in the application updates or
   deletes segments; rows disappear only with their meeting.
3. **Exactly one current revision.** A partial unique index
   (`uq_transcript_revision_current`, `WHERE is_current`) enforces it. Reads
   without an explicit `revision_id` serve the current revision; search,
   action-item source segments, shares and exports use only current segments
   (`current_segments_clause`). Older revisions stay readable by id. The
   pointer moves under the row lock of the previous current revision, so two
   concurrent corrections of the same base serialise and the loser sees a
   stale base.
4. **Processing appends.** `_persist_success` creates a revision per
   completed outcome (`processing` first, `reprocessing` after), including an
   empty one when recognition found no speech — the current transcript then
   truthfully reflects the latest processing. Speaker rows are upserted by
   label (`ensure_speakers`), never deleted, so a person's display names
   survive. Analysis results (summary, AI action items) are still replaced by
   the newest outcome; their provenance is not.
5. **Human correction is a new revision.**
   `POST /meetings/{meeting_id}/transcript/corrections` (`meeting:update`,
   CSRF) takes the base revision id and per-segment edits of text and/or
   speaker. Timings, order and segment count are preserved so audio offsets
   remain valid; a corrected text drops the engine confidence; the base must
   be current (`409 revision_stale`); every edit must name a segment of the
   base (`422 invalid_segment`) and a speaker known to the meeting
   (`422 invalid_speaker`); an edit set that changes nothing is refused
   (`422 no_changes`). The audit event `transcript.revision.created` carries
   ids, the parent, the reason and counts — never text (the audit sanitizer
   also redacts any `transcript*` key). Inserting, splitting or deleting
   segments is not offered yet (WP14 transcript correction UX).
6. **Analysis runs.** `AnalysisRun` records, per completed processing, the
   revision the analysis read, job/attempt, the intelligence checkpoint, the
   stage input hash, provider and model, the prompt version and prompt/output
   schema digests the adapter *declares* (nothing is invented for adapters that
   declare none — the mock leaves them empty), the result schema version, the
   mock label and the truthful stage state (`completed`, or `skipped` with
   `no_transcript` when no analysis ran). One run per meeting is current;
   `meeting_summaries.analysis_run_id` and `action_items.analysis_run_id` link
   results to it (null for a person's action items). The summary response
   embeds the run.
7. **API surface.** `GET /meetings/{id}/transcript[?revision_id=]` returns
   the segments plus a `revision` block;
   `GET /meetings/{id}/transcript/revisions` lists all revisions newest first
   with the current id; the shared-meeting response and every export carry
   the revision (JSON: `transcript_revision`; text: a header line naming the
   revision, source and time).
8. **Tenancy and database ceiling.** Both tables carry `workspace_id` and
   `meeting_id` and join the shared-content row-level-security policy: tenant
   scope, or read-only through a presented share token; the `WITH CHECK`
   half refuses writes through a share. Migration `d4f8b2c6e9a1` is lossless
   upward (existing segments are adopted by one `backfill` revision per
   meeting, provider/model taken from the latest completed ASR stage run
   when present) and explicitly lossy downward (superseded revisions and
   analysis provenance are dropped; the current revision survives under the
   old unique constraint).

## Amendment (2026-09-11) — evidence spans (WP04-T03)

9. **Every finding cites the revision it was made from.** The intelligence
   contract gains `summary_source_turn_indexes` next to the existing per
   action-item / per insight `source_turn_indexes` (OpenAI-compatible
   prompt/schema version 2, intelligence artifact schema version 2 so older
   checkpoints are not reused for a new analysis; the mock cites the turns it
   was shown). At persistence, turn `i` is segment `i` of the revision the
   provider read; each index is resolved against exactly those segments
   (`resolve_turn_references`): duplicates collapse, anything out of range is
   **rejected and counted** (`analysis_runs.reference_count` /
   `rejected_reference_count`) — never clamped, guessed or repaired.
10. **`EvidenceSpan`.** One immutable row per valid reference: workspace,
    meeting, analysis run, transcript revision, segment, speaker key, finding
    (`summary` overview, `insight` by index in the stored key points,
    `action_item` by row id), character range within the segment's immutable
    text, the segment's original audio offsets, the provider's confidence and
    the provenance type (`turn_reference` from the provider, `human` from a
    person). Without word alignment a span covers the whole segment; WP10 may
    narrow it. The row joins the shared-content RLS policy and the ORM
    `before_update` guard.
11. **Evidence status on every finding** (§37 of the master prompt):
    `sufficient` (all references resolved, at least one), `partial` (some
    rejected, at least one resolved), `insufficient` (none). Stored on
    `meeting_summaries.evidence_status` (the overview), on every key point /
    decision entry of the summary JSON together with its `evidence_ids`, and
    on `action_items.evidence_status`. The provider's raw indexes stay visible
    in the key points so a rejected reference is auditable. A person's action
    item citing a segment of the current revision gets a `human` span and
    `sufficient`; without a citation it is `insufficient`.
12. **Resolution API.** `GET /meetings/{id}/evidence` (current run's spans
    plus human spans) and `GET /meetings/{id}/evidence/{evidence_id}` resolve
    a citation to its text (sliced from the immutable segment), speaker,
    audio range, revision number and `is_current_revision`; action responses
    embed their citations; JSON exports carry `summary_evidence_status` and
    per-action `evidence_status` + citations. Evidence remains bound to the
    analysed revision after a correction — the API says so instead of
    re-pointing it.

## Consequences

- WP04-T02 acceptance in local scope: revisions persist and are immutable;
  reprocessing keeps earlier revisions and speaker names; corrections are
  auditable and never overwrite; analysis provenance (revision, provider,
  model, prompt/schema digests, checkpoint, state) is stored and exposed;
  tenant isolation and the share ceiling hold on PostgreSQL; the migration
  round-trips with data.
- WP04-T03 acceptance in local scope (amendment): every AI finding resolves
  only to segments of the analysed revision with original audio offsets;
  malformed references are rejected and counted; findings carry an evidence
  status; citations survive corrections and reprocessing (bound to the
  immutable revision) and are tenant-scoped with the share ceiling on
  PostgreSQL.
- Not decided here (WP04-T06): quality/coverage states of transcription,
  speaker attribution and analysis in API, UI and exports; marking an
  analysis stale when the current revision is newer than the one it read;
  word-level rows and sub-segment spans (no provider emits word timings today
  — WP10 alignment); sub-segment character ranges for provider citations.
- Verified in local scope on PostgreSQL 16.15 as `homayar_app` and on SQLite;
  not verified: remote CI, the deployed stack, the console.
