# WP05 — Ingestion and private object lifecycle

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP04.

## Implementation instructions

Features F12, F13, F14. Owner: backend/platform. Dependencies: WP03/04.

1. Define an ingestion ledger keyed by workspace, source and external event ID, with payload-hash conflict behavior.
2. Harden streamed/resumable upload limits, MIME/signature checks and bounded ffprobe/decode validation.
3. Store quarantined originals privately; authorize object promotion only after validation and record checksums/channel metadata.
4. Implement retry-safe finalize/abort and orphan reconciliation for database/object partial failures.
5. Qualify one supported S3/private storage implementation through contract tests, including multipart and access denial.
6. Add malicious/corrupt/oversized/truncated media fixtures and retries, including a forged extension and decoding timeout.

Done when duplicates do not create extra analyses/charges, invalid media is contained, and unauthorized object access remains impossible through both API and storage URLs.

## Feature-level acceptance

### F12 — Adversarial media validation (P0)

Current audited state: W functional immature. Target: Bounded decode and channel-safe validation.

- [ ] Supported codec fixtures pass; forged, malformed, oversized and duration-expansion inputs fail within configured time/memory bounds and never reach inference; original checksums remain intact.

### F13 — Idempotent upload and ingestion ledger (P1)

Current audited state: W partial. Target: Retry-safe ingest and source deduplication.

- [ ] Repeating one source event/upload finalization 100 times creates one logical conversation and one eligible usage effect; same key with changed payload returns a conflict.

### F14 — Private object storage lifecycle (P0)

Current audited state: W partial. Target: Qualified S3/private-storage adapters.

- [ ] Unauthorized/expired downloads fail; multipart abort and DB/storage partial failure reconcile without accessible orphan objects; supported storage contract suite passes.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
