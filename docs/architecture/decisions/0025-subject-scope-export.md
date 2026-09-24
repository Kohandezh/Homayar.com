# ADR 0025 — Subject-scope export

Date: 2026-09-24 · Status: accepted · Package: WP07-T07

## Context

F15 includes subject export: a customer answering a data-subject request must find
what a workspace holds about one person. Conversations are the unit of storage and
deletion, but a person appears in them through identifiers spoken in many spellings
(Persian, Arabic-Indic and ASCII digits, separators, `+98`/`0098`/`0` prefixes). The
blueprint warns that right-to-delete workflows must resolve identity and scope before
deleting unrelated callers; a conversation that mentions the subject also holds other
people's data.

## Decision

1. **Structured identifiers only.** A request carries 1–10 identifiers, each exactly
   one e-mail, phone, national ID, bank card or IBAN; the redaction detector
   (ADR 0023) canonicalises them, so every spelling is one subject. Names, addresses,
   postal codes (which need a keyword to be recognised) and fragments are refused
   (`422 invalid_subject_identifier`).
2. **Matching on detected spans.** Texts are scanned with the same detector and a
   span matches when its canonical value equals a requested one; substrings never
   match.
3. **Scope follows normal access.** Live conversations of the workspace (newest first,
   at most 500 per export, `truncated` reported), every transcript revision (older
   revisions are data the workspace still holds), the summary and the action items.
   A tombstoned conversation is out, held or not, like every normal read. The
   document lists what it does not include: audio, backups, audit metadata, deleted
   conversations and mentions without a supported identifier.
4. **Originals, owner only, audited without values.** The export contains the
   subject's data as recorded, so it needs `pii:original_access` (owner; never an API
   key) with CSRF and a reason. Matching segments are returned whole and may contain
   other people's words; the document says so and the owner reviews it before release.
   The audit `subject.exported` carries identifier classes and counts, scan size,
   matches and the reason, never an identifier or text.
5. **No subject-scope deletion.** Deletion stays per conversation (ADR 0021): the
   export gives the operator the scope, and the operator decides about conversations
   that also belong to other callers.

## Consequences

- A person named without a supported identifier is not found; the notice says so.
- The export is synchronous and bounded; very large workspaces need several exports
  or a background job later.
- T07's lifecycle test runs one workspace through a whole retention cycle on a
  controllable clock (hold, correction, failing then recovering storage, release) and
  shows that the subject export always matches what normal access may still see.
