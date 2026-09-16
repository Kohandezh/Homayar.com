# WP07 — Retention, deletion and text privacy

Status: IN PROGRESS — T01 validated in local scope on 2026-09-16 ([ADR 0020](../../../architecture/decisions/0020-retention-semantics.md)); T02–T07 not started. Dependency note: WP08-T01/T02 do not need WP07 behaviour; WP08-T03 requires WP07-T05/T06. Not production-validated.

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

## Progress (local scope)

### T01 — Explicit retention semantics and the legacy zero (validated locally, 2026-09-16)

- Product files: `backend/database/saas_models.py` (`RetentionMode`, `Workspace.retention_mode`, `ck_workspace_retention_mode`), `backend/services/retention_policy.py` (new: `validate_retention_policy`, `retention_expiry_for`, `is_age_based_deletion_enabled`), `backend/services/admin_service.py` (`_apply_retention_policy`), `backend/schemas/admin.py`, `backend/services/meeting_service.py` (expiry from the workspace policy), `backend/config.py` (`RETENTION_DAYS_DEFAULT` 1..3650), migration `c3e7a9d2b5f1`, `backend/tests/test_retention_policy.py` (new), `backend/tests/test_admin_api.py`, `frontend/src/types/platform.ts`, `frontend/src/components/platform/SettingsPage.tsx`, `docs/security/authorization-matrix.md` (§6h, §8). Website: ADR 0020, WP07/WP08 records, lock, PROGRESS.
- Commands/results: ruff check/format clean on changed files; SQLite retention + admin API 18 passed / 1 skipped; PostgreSQL 16.15 batch (retention policy incl. migration round trip with a legacy zero row and constraint check, admin API, RLS, tenant scope, media lifecycle) 36 passed; `infra/ci/check-migrations.sh` exit 0; `alembic downgrade -1` / `upgrade head` / `check` clean on `homayar_mig`; frontend `tsc -b && vite build`, `eslint`, `vitest` 12 passed.
- Security: policy changes need `workspace:manage` + CSRF and are audited by field name; a member is refused (403); the legacy zero is never destructive and never choosable; a client cannot supply an expiry.
- Limitations: no job deletes by age yet (T03); historical expiries under a resolved legacy workspace are not rewritten; business defaults undecided (fixtures use explicit policies); `delete_audio_after_processing` remains the only automatic removal today.
- Reviewer decision: self-review only (single-agent session by owner instruction).

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
