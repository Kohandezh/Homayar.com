# WP17 — Scoped retrieval and analytical Copilot

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP04, WP07, WP11, WP13, WP15.

## Implementation instructions

Features F57, F58. Owner: backend/ML/frontend. Dependencies: WP03/04/07/11/15 and WP13 evaluation.

1. Define query intents that require exact SQL aggregates versus evidence retrieval.
2. Index normalized lexical text and versioned embeddings with tenant, record ACL and revision metadata.
3. Apply authorization filters before retrieval and enforce them again on evidence access; test approximate filtered recall.
4. Add bounded hybrid search/reranking and visible source/time/quality filters.
5. Implement read-only allowlisted analytical tools with row/time limits, deterministic numeric results and cited answers.
6. Add abstention, unsupported-question handling and transcript prompt-injection defenses.
7. Evaluate 100 approved business questions plus adversarial tenant/team cases and deletion/reindex scenarios.

Done when exact counts are exact, narrative answers cite authorized evidence and the system refuses questions that cannot be answered reliably.

## Feature-level acceptance

### F57 — Scoped lexical and semantic search (P1)

Current audited state: W LIKE search. Target: Permission-safe hybrid retrieval.

- [ ] Approved relevant-evidence benchmark meets proposed recall@10 ≥0.90; all returned evidence is authorized/current or explicitly historical; deletion and membership changes remove access immediately even before reindex.

### F58 — Evidence-backed analytical Copilot (P1)

Current audited state: D template prototype. Target: Typed queries, citations, bounded retrieval.

- [ ] At least 95 of 100 frozen approved business questions answered correctly; deterministic counts 100% exact; unsupported questions abstain; zero cross-tenant/team leaks under prompt-injection and guessed-ID tests.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
