# WP02 — Credential containment and deployment security

Status: IN PROGRESS since 2026-09-07 (T01 blocked on credential owners, T02/T04 partial; see [credential-containment-record.md](../../../security/credential-containment-record.md)). Existing partial code must be reviewed and reused where suitable.

Completion dependencies: none; external prerequisites still apply.

Credential containment can start immediately; actual rotation needs the credential owner. No credentials were tested or rotated in this handoff.

## Implementation instructions

Features F03, F10. Owner: security/platform. Dependencies: inventory available; containment precedes external pilot access.

1. Give credential owners the safe path/history inventory; revoke/rotate affected credentials through their approved process and review provider usage.
2. Identify copied artifacts/forks/caches and prepare a separately approved history-cleanup plan; preserve incident evidence.
3. Replace embedded/default credentials with secret references and mandatory secure production settings.
4. Enable redacted secret scanning for new commits, full release content, image layers and support bundles.
5. Configure actual ingress TLS, internal network exposure limits and database/object/backup encryption with key rotation ownership.
6. Test invalid production configuration, exposed service ports, log redaction and certificate/key renewal on the supported topology.

Done when containment is documented, release scans have no unreviewed secret findings, and deployment controls are demonstrated. History rewriting does not substitute for revocation.

## Feature-level acceptance

### F03 — Secret containment and scanning (P0)

Current audited state: M leaked material. Target: Rotated credentials; redacted scans.

- [ ] Credential-owner containment is recorded for each suspected credential family; new source/history-distribution/image/support-bundle scans have zero unreviewed credential findings; no values enter reports/logs.

### F10 — TLS, encryption, key rotation, restricted networks (P0)

Current audited state: S partial. Target: Tested deployment controls.

- [ ] External access uses validated TLS; DB/broker/object consoles are unreachable from public ingress; encrypted data/backup access fails without authorized keys; renewal/rotation drill succeeds.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
