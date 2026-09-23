# WP07 — Retention, deletion and text privacy

Status: IN PROGRESS — T01, T02 and T03 validated in local scope ([ADR 0020](../../../architecture/decisions/0020-retention-semantics.md), [ADR 0021](../../../architecture/decisions/0021-tombstone-first-deletion-and-durable-purge.md); holds on the ADR 0017 lifecycle); T04–T07 not started. Dependency note: WP08-T01/T02 do not need WP07 behaviour; WP08-T03 requires WP07-T05/T06. Not production-validated.

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

### T02 — Legal hold ownership, reason, review and audited release (validated locally, 2026-09-16)

- Product files: `backend/security/authorization.py` (`Permission.LEGAL_HOLD_MANAGE`, owner only, not an API-key scope), `backend/database/saas_models.py` (`legal_hold_review_at`, `ck_media_hold_review`, immutability allow-list), `backend/services/media_lifecycle_service.py` (`place_legal_hold` with review date and idempotent return, `release_legal_hold` with reason, `holds_due_for_review`, validators), `backend/routers/v1/meetings.py` (three hold routes), `backend/schemas/meetings.py` (`LegalHoldRequest`, `LegalHoldReleaseRequest`, response fields), `backend/jobs/media_reconciliation.py` (`holds_review_due`), migration `d4a8c1e7b2f9`, `backend/tests/test_legal_hold.py` (new), `backend/tests/test_media_lifecycle.py` (signatures), `docs/security/authorization-matrix.md` (§3, §6b scopes, route inventory, §6e, §8). Website: WP07 records, lock, PROGRESS.
- Commands/results: ruff check/format clean on changed files; SQLite (legal hold, media lifecycle, authorization matrix, reconciliation, API keys, meetings API, job processor) 37 passed / 3 skipped; PostgreSQL 16.15 batch (legal hold, lifecycle, RLS, reconciliation, matrix, tenant scope, retention) 50 passed; `infra/ci/check-migrations.sh` exit 0; `alembic downgrade -1` / `upgrade head` / `check` clean on `homayar_mig`, both `ck_media_hold_review` and `ck_workspace_retention_mode` present.
- Security: owner-only permission checked by the machine-verified matrix; admin and member receive 403 for place and release; a foreign tenant's object id is 404; audits carry ids, reason, review/held-since timestamps only; responses never carry the storage key; a hold never resurrects deleted bytes.
- Limitations: no organisation-level or platform-operator hold; overdue reviews are reported, not escalated; no console UI for holds; a hold does not stop processing jobs (the object stays readable to the pipeline).
- Reviewer decision: self-review only (single-agent session by owner instruction); one guard fix found by the tests (the new column had to join the media immutability allow-list).

### T03 — Tombstone-first access denial, durable purge and retention sweep (validated locally, 2026-09-23)

- Product files: `backend/services/conversation_purge_service.py` (new: `tombstone_meeting`, `run_purge_attempt`, `requeue_failed_purge`, `nudge_purge`), `backend/jobs/retention_purge.py` (new: `run_retention_purge`, `expire_meeting`), `backend/database/saas_models.py` (`PurgeState`, `ConversationPurge`), `backend/database/rls.py` (`conversation_purges`), `backend/config.py` (`RETENTION_PURGE_*`), `backend/celery_app/__init__.py` + `backend/jobs/tasks.py` (beat `retention-purge`), `backend/routers/v1/meetings.py` (delete route on the tombstone + inline attempt; hold routes lock rows; release nudges the purge), `backend/services/media_lifecycle_service.py` (row locks; locked re-read in `delete_objects`), `backend/jobs/processor.py` and `backend/jobs/checkpoints.py` (meeting lock at publication; artifact sweep skips held deleted conversations), migration `e6c2a9f4d1b8`, `backend/tests/test_retention_purge.py` (new), `docs/security/authorization-matrix.md` (§6i, §8). Website: ADR 0021, WP07 records, lock, PROGRESS.
- Eligibility rule (tested): tombstone by retention only when, under workspace (shared) and meeting (exclusive) row locks, the mode is `finite`/`immediate`, `retention_expires_at <= now`, no job is `queued`/`running`, and no object is held. Terminal processing jobs are never mutated or replayed and do not block; a skipped conversation is re-evaluated next pass. Purge jobs: `PENDING` → `COMPLETED` (receipt) or `FAILED` after the attempt budget (operator requeue); a hold defers without spending an attempt.
- Commands/results: ruff check/format clean on changed files (the pre-existing `checkpoints.py` format drift left untouched); `test_retention_purge.py` SQLite 12 passed / 3 skipped (PostgreSQL-only), PostgreSQL 16.15 15 passed; PostgreSQL batch (19 files) 149 passed; full SQLite suite 423 passed / 81 skipped; `infra/ci/check-migrations.sh` exit 0; `alembic downgrade -1` / `upgrade head` / `check` clean on `homayar_mig`, `conversation_purges` with forced RLS and `homayar_tenant_isolation`.
- Guarantees tested: denial right after the committed tombstone across detail, list, transcript, revisions, summary, actions, evidence, quality, export, recording, job, search, public share and API-key routes; finite boundary (−1 s, =, after); `immediate` waits for terminal processing; `indefinite` and `legacy_unresolved` never act on a stale expiry; hold blocks expiry and defers the purge (content, stage outputs, bytes), overdue review does not release, release resumes; stale-scan hold race keeps bytes; PostgreSQL removal/hold race refuses the hold `already_deleted`; PostgreSQL simultaneous attempts claim once; denied and unavailable storage keep `deletion_pending` (never `missing`), backoff respected, retry completes; exhausted budget → `FAILED` + audit + alert count, requeue completes; repeated sweeps idempotent (one receipt); deletion during processing never republishes; foreign tenant cannot delete, sweep or claim; RLS probe; usage events and reservations unchanged; purge audits carry no content.
- Security: no new route or permission; the sweep binds each workspace's tenant context (no BYPASSRLS, no migration-owner credentials); audits carry ids, counters and codes only.
- Limitations: presigned URLs are revoked only by expiry; conversations tombstoned before T03 get no purge row (operator decision, no backfill); original filenames remain on immutable media/ingestion rows; purge-time media audits have no actor; `requeue_failed_purge` has no route; the sweep discovers workspaces through entitlements like the other sweepers; restores (T04) not handled yet; no live S3 run.
- Reviewer decision: self-review only (single-agent session by owner instruction); one gap found in review and fixed before commit (stage outputs of a held deleted conversation were removable by the purge and by the WP06 sweep).

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
