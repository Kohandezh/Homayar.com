# ADR 0024 — Routing reads, analysis and exports to the redacted or original transcript

Date: 2026-09-24 · Status: accepted · Package: WP07-T06

## Context

ADR 0023 produces a redacted version of a transcript under a workspace policy, but
nothing used it: the intelligence provider, every API read, search, exports and share
links still served the original text. F16 requires that protected originals never
enter a redacted-only provider or export path, and that the version used is
traceable. The original transcript rows remain the source of truth (corrections,
evidence offsets and audio alignment are defined on them), so "restricted original"
means access control over those rows, not their removal.

## Decision

1. **One view decision.** `choose_view` (backend/privacy/transcript_view.py) returns
   the original when the workspace does not enforce redaction, and the redacted
   version otherwise. Under an enforced policy the original needs a new permission,
   `pii:original_access` (owner only, never an API-key scope), and an explicit
   `?view=original`; each such read is audited as `transcript.original_accessed`
   with the surface. Without a policy any reader may ask for `?view=redacted`.
2. **Every surface uses it.** Transcript (any revision), evidence, summary (overview,
   key points, decisions, topics), action items (texts and evidence offsets), exports
   (all formats) and the responses of corrections and action writes render through
   `MeetingView`. Share links always get the enforced view; the share session cannot
   write, so it computes the redacted texts instead of storing them. Search under a
   policy re-checks every candidate against the redacted texts, title and speaker
   names, so searching for a redacted value finds nothing (no oracle); it has no
   original-text mode.
3. **Deterministic, consistent placeholders.** Redaction is deterministic for a
   detector version and class list, so computed texts equal the persisted version of
   the same digest. Numbering runs through the revision's segments in order, then
   the summary and the action items in a canonical order, so a value keeps its
   placeholder in the transcript, summary and actions. Evidence offsets are mapped
   into the redacted text and widened to whole placeholders (never part of a value).
4. **Placeholders written back are restored.** A person editing a redacted text
   returns placeholders; corrections and action item writes replace the placeholders
   the caller was shown with the originals they stand for before storage, so an edit
   never overwrites an original with a token. Restoration applies when the caller's
   default view is redacted.
5. **Provider input and provenance.** The processor snapshots the policy at claim
   time. The intelligence provider receives only redacted turns and text (one
   pseudonymizer in segment order, the same numbering as the redacted version stored
   with the new revision). The intelligence checkpoint identity includes the policy
   digest only when a policy applies, so existing checkpoints keep their identity and
   an analysis computed from originals is not reused under a policy.
   `analysis_runs.input_view` / `input_policy_digest` record what the provider read
   (constraint `ck_analysis_run_input_view`; pre-T06 rows become `original`, which is
   what they read).
6. **Export provenance.** Exports carry a `privacy` block: view, whether the policy is
   enforced, detector version, classes, policy digest, redacted version id, entity
   count, what the current analysis read, and the residual-risk notice (text-only,
   supported identifiers only, not anonymisation, audio not masked). `meeting.exported`
   and share access audits record the view.
7. **Reveal and policy changes.** `POST /meetings/{id}/transcript/redactions/{rid}/reveal`
   decrypts one placeholder for `pii:original_access` with CSRF and a reason, audited
   as `pii.revealed` without the value. Changing `pii_redaction_enabled` or the class
   list also needs `pii:original_access`, because whoever may switch redaction off
   could read originals.

## Consequences

- Correction to ADR 0023: the transcript segments themselves remain in the database
  as the access-controlled original (not encrypted by the application); `pii_mappings`
  hold ciphertext copies of the replaced values for scoped, audited reveal.
- Analysis outputs produced before a policy was enabled are redacted at read time,
  not rewritten; values that occur only in analysis text are numbered after the
  transcript's.
- Rendering a redacted view runs the detector over the meeting's texts on each request
  (search: at most 4× the limit, capped at 200 candidates); no caching yet.
- The console has no policy control, view switch or reveal UI (WP14); ASR checkpoints
  hold original text until the conversation is purged; a role narrower than owner for
  original access is an owner decision not taken here.
- WP08-T03 (privacy/eligibility filtering) can build on `choose_view` / `MeetingView`
  and on `analysis_runs.input_view`.
