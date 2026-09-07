# WP11 — Measured signals and versioned quality evaluation

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP04, WP08, WP09, WP10, WP13.

## Implementation instructions

Features F34, F36, F41, F42. Owner: ML/backend with QA domain owner. Dependencies: WP04/08–10/13.

1. Compute speech/silence/talk ratio/overlap/rate from intervals with precise metric definitions and denominators.
2. Define supported intent/topic/entity/outcome taxonomy and schema with evidence and unknown values.
3. Implement structured extraction validation, confidence/abstention policy and critical-entity review routing.
4. Create versioned scorecards, weights, applicability, thresholds and pass/fail/NA/insufficient-evidence semantics.
5. Add deterministic keyword/script checks and contextual criteria using qualified targeted model evaluation.
6. Persist criterion evidence, calculation version and score denominator; prohibit inferred NPS or unsupported churn probabilities.
7. Compare scores with adjudicated human QA and add regressions for negation, missing speakers, low ASR confidence and contradictory evidence.

Done when scores are reproducible from a frozen rule/model version, explainable criterion by criterion and within the Section 38 accuracy gates.

## Feature-level acceptance

### F34 — Silence, talk ratio, overtalk and speaking rate (P1)

Current audited state: D prompt estimates. Target: Deterministic metrics with denominators.

- [ ] Known synthetic interval fixtures reconcile duration/silence/talk/overlap exactly to defined rounding; on annotated real clips, talk/silence ratio MAE ≤5 percentage points; no LLM-generated acoustic measurements.

### F36 — Intent, topics, entities and resolution status (P1)

Current audited state: D prototype; W generic. Target: Evidence-backed structured extraction.

- [ ] Supported intent taxonomy macro-F1 ≥0.85; critical high-cost findings precision ≥0.95 or human confirmation; every field has evidence/unknown semantics and contradictory fixtures abstain correctly.

### F41 — Versioned custom weighted QA scorecards (P1)

Current audited state: Missing. Target: Organization/campaign policy and NA rules.

- [ ] Weights/applicability/NA/insufficient evidence calculations pass fixed examples; adjudicated QA kappa ≥0.75 and score MAE ≤8/100; scorecard versions remain reproducible and cannot silently change past scores.

### F42 — Keyword, script and compliance evidence rules (P1)

Current audited state: D fixed prompts. Target: Deterministic plus semantic reviewed checks.

- [ ] Exact phrase rules are deterministic; contextual critical findings precision ≥0.95 or require review; negation, quoted speech, wrong speaker and poor ASR regression cases do not create automatic confirmed violations.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
