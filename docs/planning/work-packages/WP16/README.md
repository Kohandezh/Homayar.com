# WP16 — Supported ingestion/API/integration contracts

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP05, WP06, WP08.

## Implementation instructions

Features F63, F64, F68, F69. Owner: integration/backend. Dependencies: WP03/05/06/08.

1. Publish versioned asynchronous REST/OpenAPI contracts and a minimal Python client/example with retries and idempotency.
2. Add scoped, expiring, hashed API credentials, rotation and usage/rate limits.
3. Qualify one actual Asterisk/FreePBX/Issabel deployment's recording/CDR export and source ID/channel mapping.
4. Add configurable CRM/ticket/customer/agent/outcome mappings, reconciliation and delayed-record handling.
5. Persist outbound webhook deliveries and sign raw payloads with timestamp/key ID; provide replay/deduplication guidance.
6. Add retry/dead-letter/admin diagnostics without exposing credentials or transcript text.
7. Run contract fixtures for duplicates, transfers, missing recordings, clock skew, stale signatures and revoked service identities.

Done when one supported PBX plus one generic CRM outcome path works end to end, with explicit supported versions rather than a universal integration claim.

## Feature-level acceptance

### F63 — Asterisk/FreePBX/Issabel recording adapter (P1)

Current audited state: D generic webhook. Target: Qualified channel/CDR ingestion package.

- [ ] One named supported PBX/version imports 100 representative recordings/CDRs including transfers/duplicates/missing files; IDs and channel roles reconcile; unsupported versions are documented.

### F64 — Generic CRM/ticket ID and outcome mapping (P1)

Current audited state: Missing. Target: Stable external IDs and reconciliation.

- [ ] Generic mapping links known call/customer/ticket/agent/outcome fixtures correctly; missing/changed/late IDs enter reconciliation rather than false joins; no phone-number-only global identity merge.

### F68 — Versioned REST, scoped API keys and SDK (P1)

Current audited state: W partial models. Target: Ingest/status/results with machine auth.

- [ ] Published OpenAPI/client example completes upload→202 job→results; expired/revoked/out-of-scope key fails; rate limits and idempotency behavior verified through contract tests.

### F69 — Signed webhooks and delivery ledger (P1)

Current audited state: D weak; W model. Target: Replay-safe events and bounded delivery.

- [ ] Raw-body HMAC/timestamp verification examples pass; altered/stale/replayed messages rejected or deduplicated; retry/dead-letter records allow safe redelivery without duplicate recipient effects.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
