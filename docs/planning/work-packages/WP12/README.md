# WP12 — Call report, summaries and correction

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP04, WP09, WP10, WP11.

## Implementation instructions

Features F37, F45. Owner: frontend/backend/ML. Dependencies: WP03/04/09–11.

1. Evolve meeting records into a compatible Conversation model and call-oriented navigation without losing existing IDs.
2. Build the primary report around summary, outcome, QA/evidence and review action; progressively disclose advanced panels.
3. Add audio/transcript synchronized navigation, speaker/time filters and explicit incomplete-analysis notices.
4. Generate supported summary/decision/key-moment fields with evidence, abstaining on unspecified details.
5. Implement transcript text and turn-to-speaker correction as new immutable revisions, with reason and reviewer identity.
6. Mark dependent scores/summaries/search projections stale and selectively recompute against the corrected revision.
7. Test Persian/English report usability and keyboard playback/evidence navigation with representative supervisors.

Done when a supervisor can find a cited moment, correct attribution/text, understand version history and see consistent updated findings without developer help.

## Feature-level acceptance

### F37 — Summaries, decisions and key moments (P1)

Current audited state: W immature. Target: Grounded concise summary with abstention.

- [ ] ≥95% audited atomic claims supported by cited evidence, 100% structural citation validity and no invented severe allegation in release set; summaries distinguish explicit outcome from inference.

### F45 — Transcript and turn/speaker correction (P1)

Current audited state: W rename only. Target: Versioned edits and dependent recomputation.

- [ ] Text and speaker-turn corrections create a new revision; playback/evidence remain navigable; dependent outputs become stale then recompute; old export and decision retain original provenance.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
