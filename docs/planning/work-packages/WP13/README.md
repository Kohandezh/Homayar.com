# WP13 — Corpus and promotion evaluation

Status: IN PROGRESS since 2026-09-07 (T01 blocked on owners, T02 partial; draft protocol in [annotation-protocol.md](annotation-protocol.md)). Existing partial code must be reviewed and reused where suitable.

Completion dependencies: none; external prerequisites still apply.

Corpus rights/protocol starts immediately. Later quality promotion is iterative and gates model-dependent packages; do not block baseline corpus preparation on models that consume it.

## Implementation instructions

Features F80, F81. Owner: ML/QA/product. Dependencies: authorized data access; starts immediately and gates later packages.

1. Obtain rights, retention/access rules and a sampling plan across accents, channels, intents and sensitive fields.
2. Create annotation guidelines, normalized/verbatim text policy, speaker/overlap labels, scorecards and evidence labels.
3. Assemble at least 20 Persian hours with a frozen 5-hour test set and separate English/mixed-language coverage.
4. Double-annotate at least 20%, adjudicate disagreements and prevent speaker/customer/time leakage between splits.
5. Implement WER/CER, DER/JER, entity/intent, QA agreement, factual-support and retrieval evaluators.
6. Publish slice sample sizes, uncertainty, abstention, coverage, latency and cost with each candidate result.
7. Gate model/prompt promotion on the frozen release criteria and maintain a rollback manifest; collect production corrections separately.

Done when evaluation is reproducible from an access-controlled manifest, data rights are recorded and every promoted model has a comparable signed-off report.

## Feature-level acceptance

### F80 — Persian gold corpus and slice benchmarks (P0)

Current audited state: Missing. Target: Consent, held-out calls and adjudication.

- [ ] Rights/access/split manifests exist; ≥20 Persian hours with frozen ≥5-hour held-out subset and separate English/mixed coverage; ≥20% double annotation/adjudication; no split leakage detected by identity/source checks.

### F81 — AI regression and model/prompt promotion gates (P1)

Current audited state: Missing. Target: Versioned quality reports and rollback.

- [ ] Evaluation command reproduces metrics within documented stochastic tolerance; model/prompt change cannot promote without held-out quality/slice/cost report and approved rollback manifest.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
