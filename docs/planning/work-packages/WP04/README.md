# WP04 — Truthful states and evidence contracts

Status: IN PROGRESS since 2026-09-08 (T01 validated in local scope; see [ADR 0003](../../../architecture/decisions/0003-truthful-stage-states.md)). Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP01.

## Implementation instructions

Features F23, F24. Owner: backend/ML. Dependencies: WP01; schema work coordinates with WP03.

1. Define run/stage states including queued, running, completed, partial, failed, unavailable and insufficient evidence, with typed reasons.
2. Introduce immutable transcript revisions, evidence spans, analysis runs and model/prompt/schema provenance.
3. Make every finding reference existing authorized turns and original audio offsets; reject malformed references.
4. Exclude mock providers from production configuration and preserve explicit mock labels in any development fixtures.
5. Remove random/default analytics from production result serialization; unavailable metrics remain unavailable.
6. Expose quality, coverage and degradation consistently through API, UI, exports and usage eligibility rules.

Done when injected provider failures cannot produce completed-looking invented scores and all displayed findings resolve to their exact source revision.

## Feature-level acceptance

### F23 — Honest stage states and mock exclusion (P0)

Current audited state: D unsafe; W partial. Target: Visible partial/unavailable; zero fake scores.

- [ ] Each forced missing/failing stage is represented in API/UI/export; unavailable diarization or failed QA cannot appear fully successful; production mock configuration is rejected and no random/default analytics are substituted.

### F24 — Evidence/revision/provenance schema (P0)

Current audited state: W partial. Target: Immutable spans and reproducible findings.

- [ ] 100% of returned citations resolve to an authorized immutable transcript revision and valid original audio range; correction/reanalysis preserves old provenance and explicitly supersedes dependent results.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
