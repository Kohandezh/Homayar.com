# WP10 — Diarization, overlap and alignment

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP04, WP08, WP09, WP13.

## Implementation instructions

Features F30, F31, F33. Owner: audio/ML. Dependencies: WP04/08/09/13.

1. Implement a real mono diarization adapter with pinned community-1 or a better measured qualified model.
2. Preserve anonymous speaker clusters and overlap intervals separately from display-exclusive turns.
3. Reconcile speaker identities across long-call chunks and constrain counts only when source evidence supports them.
4. Align words/turns to speaker intervals and original recording offsets; retain ambiguous/unknown assignments.
5. Calibrate confidence/quality flags using held-out annotations; do not convert similarity into unsupported probability.
6. Add timeline representation for overlapping speech and correction-ready turn attribution.
7. Evaluate DER/JER, count errors, role accuracy and span offsets, publishing collar/overlap/unknown policies.

Done when real attribution replaces fabrication, overlap survives serialization, and the stated diarization/alignment gates pass on the supported corpus.

## Feature-level acceptance

### F30 — Mono and multiparty diarization (P1)

Current audited state: Missing. Target: Real anonymous speakers with uncertainty.

- [ ] Mono DER ≤15% on agreed held-out contact-center set, with overlap/collar policy and JER/count errors published; ambiguous clusters remain anonymous/unknown rather than fabricated agent identities.

### F31 — Overlap detection and attribution (P1)

Current audited state: Missing. Target: Preserve simultaneous speech and unknowns.

- [ ] Overlap intervals survive storage/UI/export; proposed overlap detection F1 ≥0.80 on an adequately annotated supported set; display-exclusive turns do not erase measured overlapping speech.

### F33 — Timestamp alignment and calibrated confidence (P1)

Current audited state: W partial. Target: Word/turn alignment, quality gates.

- [ ] ≥95% audited key-moment starts within ±1 second and all spans in recording bounds; confidence origin and calibrated/uncalibrated status visible; ambiguous alignment routes to review.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
