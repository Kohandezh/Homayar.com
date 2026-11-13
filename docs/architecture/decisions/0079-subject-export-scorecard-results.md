# ADR 0079 — Subject export includes scorecard results and criterion evidence

Date: 2026-11-13 · Status: accepted · Package: HOM-354 · Extends: ADR 0025

## Context

HOM-56 stores the machine QA score of every conversation in `scorecard_results`
with one `scorecard_criterion_results` row per criterion, including the evidence
spans (quotes and facts from the call). This is personal data about the call's
agent and about the customer of the call. The subject-scope export of ADR 0025
read none of it, so an export missed the subject's QA results and evidence: the
bug of HOM-354.

## Decision

1. **Customer path.** Every conversation in `matches` (matched as before, by an
   identifier in a transcript segment, the summary, an action item or an
   extraction entity value) also carries `scorecards`: its stored results,
   oldest first (`created_at`, then `id`), each with its criteria by position.
   A matched conversation with no results gets an empty list. A result never
   makes a conversation match on its own.
2. **Agent path (new matching rule).** A user is an agent subject when the
   canonical value of their `users.email` or `users.phone_e164` equals a
   requested identifier (canonicalised by the same detector as the request; a
   stored value it cannot read one-of is skipped). Only agents of the
   workspace's live meetings are candidates. When any agent subject exists the
   document gains a top-level `agent` key: the live conversations they agented,
   under the caller's record access, newest first, bounded like the customer
   path (at most 500 scanned, `truncated` reported), scorecard rows only (no
   transcript, summary, action or extraction text). Meetings without a result
   still count toward the scan (`meetings_scanned`, `truncated`). Without an
   agent subject the key is absent, so older documents keep their shape.
3. **Row shape.** Stored columns only: the result's outcome, reason, weights and
   versions, and per criterion its outcome, decision and evidence spans exactly
   as stored. The `check_run` and `result` JSON blobs and the digests never
   leave. Nothing is resolved from an evidence `turn_index`: that text may be
   other people's words, and ADR 0025 returns only segments that hold the
   identifier.
4. **Same scope and redaction rules.** Every query filters by `workspace_id`
   (row-level security also applies on PostgreSQL); tombstoned conversations are
   out on both paths; the export never reads `pii_mappings` and never reverses a
   placeholder, so a redacted evidence phrase such as `[PHONE_1]` is exported
   redacted.
5. **Audit.** `subject.exported` adds `scorecard_results`, `agent_subjects`,
   `agent_meetings_scanned` and `agent_truncated`, all counts and booleans
   only, never an id, an e-mail, a phone or text.
6. **Notice.** The export's notice and the module docstring state the new scope
   and the follow-ups that stay out (section "Out of scope").
7. **Deletion unchanged.** ADR 0025 decision 5 stands: no subject-scope
   deletion; deletion is per conversation (ADR 0021) and the conversation purge
   already removes both tables, which a test now proves end to end for the
   export.

No migration: both tables already exist (HOM-56).

## Consequences

- An owner answering a data-subject request now sees the subject's QA results
  and criterion evidence, for a customer through the call's identifiers and for
  an agent through the account e-mail or phone.
- The agent path can list conversations whose transcripts never mention the
  agent's identifier; that is intended, because the scorecard rows are about the
  agent.
- The export runs one more bounded meeting scan (only when an agent subject
  exists) and one results query per scanned conversation.
- Documents grow by the `scorecards` key on every match, and by the `agent` key
  when an agent subject is named; no existing consumer asserted an exact match
  shape.

## Out of scope

- `coaching_tasks` (HOM-77): per-agent rows with free-text follow-ups.
- Supervisor review items and review decisions (HOM-74, ADR 0053), appeals
  (ADR 0057) and calibration rows (ADR 0056): each of those ADRs lists the
  data-subject export as out of scope for its tables.
- HOM-138 pending item 15: the export still serves text after retention expiry.
