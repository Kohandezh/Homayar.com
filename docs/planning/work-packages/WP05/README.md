# WP05 — Ingestion and private object lifecycle

Status: IN PROGRESS — T01 validated in local scope on 2026-09-11 ([ADR 0016](../../../architecture/decisions/0016-ingestion-ledger.md)); T02–T06 not started. Not production-validated: remote CI, the deployed stack and a real S3-compatible bucket have not run.

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

## Progress (local scope)

### T01 — Canonical ingestion ledger (validated locally, 2026-09-11)

- Product files: `backend/database/saas_models.py` (`IngestionSourceType`, `IngestionStatus`, `DuplicateMediaPolicy`, `Workspace.duplicate_media_policy`, `IngestionRecord`, immutability guard), `backend/services/ingestion_service.py` (new), `backend/routers/v1/ingestions.py` (new), `backend/schemas/ingestions.py` (new), `backend/routers/v1/meetings.py` (upload claim, replay/conflict settlement, detail `ingestion`), `backend/schemas/meetings.py`, `backend/services/admin_service.py` + `backend/schemas/admin.py` (policy setting), `backend/database/rls.py`, `backend/main.py`, migration `f7b1d3e5a9c2`, `docs/security/authorization-matrix.md` (route rows, §6d, §8), `backend/tests/test_ingestion_ledger.py` (new, 11), `backend/tests/test_admin_api.py`, `frontend/src/types/platform.ts` (types only).
- Commands/results: ruff clean on changed files; SQLite focused + whole suite 224 passed / 34 skipped; PostgreSQL 16.15 as `homayar_app` — ledger, uploads, RLS, tenant scope, API keys, matrix, postgres guarantees, admin, processor, meetings → 49 passed (RLS probe as a non-bypass login role, two-thread concurrent claims, migration round trip with a real upload); `infra/ci/check-migrations.sh` exit 0; explicit `alembic downgrade -1` (table and column dropped) / `upgrade head` / `alembic check` clean, 34 forced policies; frontend `npm run build`, `npm run lint`, `npm test` green.
- Security: identity uniqueness and tenant binding are database constraints/policies; a foreign tenant's row is `404`, the victim's header is refused before lookup; audit details carry ids, counts, sizes and policy names only; no payload, media or secret is logged.
- Limitations: rejected ingestions are not yet ledger rows (T02); only `FILE_UPLOAD` events are produced; rejections and replays are not rate-limited separately; no console UI for the ledger.
- Reviewer decision: self-review only; product commit `e7a596e1`, nothing pushed.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
