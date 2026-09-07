# WP07 — Retention, deletion and text privacy

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP04, WP05, WP06.

## Implementation instructions

Features F15, F16. Owner: backend/security. Dependencies: WP03–06.

1. Define explicit immediate/finite/indefinite retention semantics and migrate the current ambiguous zero value safely.
2. Add legal-hold ownership, reason, expiry/review and audited release; separate hold from ordinary user preference.
3. Implement tombstone-first access denial, durable object/derived-data purge jobs and retry reconciliation.
4. Reapply deletion tombstones after restore and document backup expiry rather than promising instant backup erasure.
5. Add Persian/English text PII detection, configurable classes and restricted original-to-redacted mappings.
6. Route permitted analysis/retrieval/export to the correct redacted or original version and record policy provenance.
7. Test retention with a controllable clock, held records, failed storage deletion, mixed-language identifiers and subject-scope export.

Done when deletion completes across every store or exposes a retryable failure, holds are respected, and text redaction is never represented as audio masking or guaranteed anonymization.

## Feature-level acceptance

### F15 — Retention, deletion, legal hold and subject export (P0)

Current audited state: W fields only. Target: Enforced purge and restore tombstones.

- [ ] Finite expiry and explicit immediate/indefinite settings behave as documented; access denied immediately on accepted deletion; active-store purge completes within 24h under normal conditions or raises retry alert; held data remains; restore reapplies tombstones.

### F16 — Text PII redaction and access policy (P1)

Current audited state: Missing. Target: Redacted downstream copy; restricted original.

- [ ] On a separately annotated supported-class Persian/English set, proposed recall ≥95% and precision ≥90%; unsupported classes and residual risk disclosed; protected originals never enter a redacted-only provider/export path.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
