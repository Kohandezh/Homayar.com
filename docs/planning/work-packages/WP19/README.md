# WP19 — Offline package, licensing and upgrades

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP01, WP02, WP05, WP08, WP09, WP10, WP18, WP20.

## Implementation instructions

Features F74, F75. Owner: platform/ML. Dependencies: WP01/02/05/08–10/18/20; local model quality gates remain mandatory.

1. Define supported private CPU/GPU topologies, capacity envelope, local IdP and storage choices.
2. Build signed offline images/model bundles with pinned hashes, licenses and required runtime dependencies.
3. Add preflight for driver/VRAM/disk/ports/TLS, local model readiness and no-internet configuration.
4. Implement signed offline entitlements, expiry/grace/read-export behavior and operator-visible counters.
5. Package install, upgrade, backup/restore, rollback and sanitized support-bundle commands/runbooks.
6. Test install and representative processing/export/deletion with all outbound network access denied.
7. Rehearse upgrade from the prior supported version with real fixture data, failed migration recovery and customer-admin handoff.

Done when an operator can install and operate the exact documented offline profile without hidden downloads, cloud auth or developer intervention.

## Feature-level acceptance

### F74 — Air-gapped local STT/LLM/embed package (P1)

Current audited state: S prototype. Target: No mandatory external dependencies.

- [ ] Fresh qualified installation with outbound network denied performs login, 30-minute Persian processing, local summary/QA/search, export and deletion; no hidden downloads/cloud calls; actual hardware/profile recorded.

### F75 — Offline license, upgrade and secure support bundle (P1)

Current audited state: Missing/partial. Target: Signed artifacts; recoverable upgrades.

- [ ] Valid/expired/tampered offline license tests follow documented grace/export behavior; prior-version upgrade and failed-upgrade recovery preserve data; default support bundle contains no canary secret, transcript or identity.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
