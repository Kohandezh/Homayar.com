# ADR 0034 — Derived normalized text views and entities of transcript revisions

Date: 2026-10-10 · Status: proposed (Lane B, HOM-195) · Packages: WP22 (F26, F36, F72) · Builds on: ADR 0014, ADR 0021, ADR 0022, ADR 0023, ADR 0024

## Context

HOM-120 made `backend/speech/text_normalize.py` the single owner of Persian/Arabic text
normalization, and `backend/speech/entities.py` extracts phones, national IDs, e-mails, amounts,
dates, times, reference IDs and person-name slots. The post-call processor used neither: it stored
the raw transcript, redacted it under a workspace policy and sent it to analysis. Search, entities
(F36) and locale-aware handling of digits (F72) need a normalized form, and HOM-120's rule stands:
normalization never changes raw evidence.

## Decision

1. **A derived view per revision, in the revision's transaction.** `_persist_success` calls
   `backend.speech.text_view.derive_text_view` right after the revision (and its redacted version)
   is created, under the same job fence and meeting row lock. The view is new rows; the transcript
   segments are not touched. A tombstone committed before publication therefore also prevents the
   view, and a crash before the commit rolls both back.
2. **Three tenant tables** (migration `d8a2f4c6b1e3`):
   - `transcript_text_views`: one row per revision and `view_digest`, with `input_view`
     (`original`/`redacted`), `input_policy_digest`, `redaction_id`, `language`,
     `normalizer_version` (`speech-normalize/1`), `extractor_version` (`speech-entities/1`),
     `detector_version` (the PII detector the extractor uses), `reference_at` and `timezone`,
     `state`, `reason_code`, counts, `job_id` and `attempt`;
   - `text_view_segments`: the normalized text of each segment;
   - `text_view_entities`: type, offsets into the view's source text of the segment, JSON value
     and candidates (ISO 8601 dates and times), confidence (a rule prior), `ambiguous`, source rule.
   The digest covers every version, the value encoding, the language, the source view and policy,
   and the reference time, so a version change produces a new view and an old one is never
   rewritten. All three tables are immutable through the ORM (same guard as transcript segments).
3. **Source and PII decision: the reader's default view.** Under an enforced workspace redaction
   policy the view is derived from the persisted redacted version (`redaction_id`), which is the
   text every reader receives by default (ADR 0024). Without a policy it is derived from the
   original segments, which every transcript reader may read. Reasons:
   - the derived rows never hold more than the default view shows, so they need no new access
     rule, no new key and no reveal path, and they cannot become an oracle for a redacted value;
   - entity extraction reuses `text_pii.detect`, so on original text it would copy identifiers into
     a second table; storing them "under the PII-mapping rules" would mean ciphertext, key custody
     and audited reveal for every entity value: a second restricted store for no current consumer;
   - it matches `analysis_runs.input_view`: provenance says which text the derivation read.
   Entity offsets refer to that source text (the original segment or its redacted version, both
   immutable), never to the normalized text. The analysis endpoint's stricter cloud redaction does
   not change the source: the view follows the workspace view captured at claim time
   (`ClaimedJob.view_classes`).
4. **Honest states (F23).** `completed`; `partial` when normalization succeeded and extraction
   failed (no entity kept); `failed` when the text could not be normalized, read or stored
   (`normalization_failed`, `source_unavailable`, `persist_failed`); `skipped` for an empty
   transcript (`no_transcript`) or a language without a normalizer (`normalizer_unavailable`).
   A fault never fails the transcript: derivation and storage run in a savepoint and become a
   recorded state; the completion audit carries `text_view_id`, `text_view_state`,
   `text_view_reason`, `text_view_input` and `text_view_entities` (never text or values). The
   view is not a processing stage of the stage list (the console shows four fixed stages), and a
   degraded view does not change the meeting status.
5. **Idempotency.** One view per (revision, digest): `derive_text_view` returns an existing view,
   and a concurrent creator is resolved by the unique constraint and a re-read. A retried or
   replayed job creates the view with the revision it publishes; reused checkpoints give the same
   text and therefore the same view.
6. **Tenancy and deletion.** The tables are `WORKSPACE_TABLES` under forced row-level security and
   are never reachable through a share token. The conversation purge removes them first (before
   redacted versions, which they may reference); tombstone reapply reopens the purge and so removes
   rows a restore brought back. They follow the conversation's retention; there is no separate
   retention setting.
7. **No read surface yet.** No API, export or search reads the tables in this unit. Any future
   reader must serve only a view whose `input_view` and `input_policy_digest` equal the caller's
   current view (ADR 0024), and update the authorization matrix.

## Consequences

- Conversations processed before this change, and revisions created by human corrections, have no
  view until they are processed again; there is no backfill.
- After a workspace changes its redaction policy, views derived under the old policy remain (as do
  the original segments) until the conversation is reprocessed; decision 7 keeps them unread.
- "Today" for relative dates is the conversation's creation time in Asia/Tehran; a conversation
  uploaded long after the call resolves "فردا" against the upload date. `reference_at` records it.
- Extraction is per segment: numbers or dates split across segments are not found, as in ADR 0023.
- The subject-scope export (ADR 0025) is unchanged: it returns every transcript revision, from which
  each view is derived; it does not list the derived rows separately.
- Normalization behaviour is frozen by the HOM-120 golden tests; `test_text_view.py` pins the
  speech profile's output to `NORMALIZER_VERSION`, so an intended change must bump the version.
- A PII detector change (for example HOM-171) changes `detector_version` and the view digest, so
  new revisions get views under the new detector; existing views keep theirs.
