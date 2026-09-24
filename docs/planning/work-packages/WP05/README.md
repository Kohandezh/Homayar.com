# WP05 — Ingestion and private object lifecycle

Status: PARTIAL (local scope complete; live S3-compatible qualification BLOCKED_EXTERNAL) — T01, T02, T03 and T04 validated in local scope on 2026-09-11 ([ADR 0016](../../../architecture/decisions/0016-ingestion-ledger.md) and its amendment, [ADR 0017](../../../architecture/decisions/0017-media-objects-and-lifecycle.md), [ADR 0018](../../../architecture/decisions/0018-media-reconciliation.md)); T05 partial on 2026-09-16 ([ADR 0019](../../../architecture/decisions/0019-storage-contract.md): adapter contract validated locally, live S3-compatible qualification BLOCKED_EXTERNAL); T06 validated in local scope on 2026-09-16 (adversarial fixtures and retries through the real route). Not production-validated: remote CI, the deployed stack and a real S3-compatible bucket have not run.

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

### T02 — Media validation (validated locally, 2026-09-11)

- Product files: `backend/media/validation.py` (signature pre-check, plausibility floor, bounded ffmpeg decode, `terminal` classification, new errors), `backend/media/__init__.py`, `backend/config.py` + `.env.example` (`FFMPEG_PATH`, `FFMPEG_TIMEOUT_SECONDS`, `MEDIA_DECODE_PROBE_SECONDS`, `MEDIA_MIN_BYTES_PER_SECOND`), `backend/services/ingestion_service.py` (`record_rejection`, `audit_rejection`, rejected replay), `backend/routers/v1/meetings.py` (`_record_refusal`, `_refusal_status`, rejected replay answer, ffmpeg arguments), `backend/database/saas_models.py` + migration `a8c4e2f6d1b9` (rejected rows need a reason), `docs/security/authorization-matrix.md` (§6d rows, §8), `pyproject.toml` + `backend/tests/conftest.py` (`ffmpeg` marker), tests `backend/tests/test_media_validation.py` (+13), `backend/tests/test_media_fixtures.py` (new, 21, real binaries), `backend/tests/test_ingestion_ledger.py` (+1).
- Measured before designing: ffprobe clamps an over-claiming WAV header to the bytes present; random/text/PDF/zip bytes fail the probe; the bounded decode refuses corrupted MP3/FLAC and garbage-tailed MP3 that the probe accepts (and for which it over-reports duration), ~50 ms per file; truncated files decode.
- Commands/results: ruff clean on changed files; media unit + fixtures + ledger + uploads + admin + matrix on SQLite 83 passed / 3 skipped; SQLite whole suite 266 passed / 34 skipped (4 min 10 s); PostgreSQL 16.15 as `homayar_app` — ledger, uploads, RLS, tenant scope, API keys, matrix, postgres guarantees, admin, processor, meetings → 50 passed; `infra/ci/check-migrations.sh` exit 0; `alembic downgrade -1` / `upgrade head` / `alembic check` clean on `homayar_mig`, the tightened constraint verified by a refused insert.
- Security: fixed argv and no shell for both binaries; stdin/stdout/stderr detached; caps on probe size, analysis duration, allocation, decode length, threads and wall time; no raw probe output, path or stderr in any error; rejections audited with ids, codes and sizes only.
- Limitations: an understated duration (more audio than claimed) is bounded by the worker deadline, not refused; the decode verifies a prefix, not the whole file; no `resource` rlimits on the child processes (the caps above bound them); no resumable/streamed multipart protocol (the upload is still one request bounded by `MAX_UPLOAD_SIZE_MB`).
- Reviewer decision: self-review only; product commit `4e82281e`, nothing pushed.

### T03 — Original and derived media objects (validated locally, 2026-09-11)

- Product files: `backend/database/saas_models.py` (`MediaKind`, `MediaLifecycleState`, `MediaIntegrity`, `MediaObject`, `Recording.media_object_id`, `MediaImmutableError` guard), `backend/services/media_lifecycle_service.py` (new), `backend/services/meeting_service.py` (`register_original` inside `create_upload_job`, `CreatedUpload.media`), `backend/jobs/processor.py` (`_delete_audio_after_success` through the lifecycle), `backend/routers/v1/meetings.py` (deletion through `retire_meeting_media`/`delete_objects`, `GET …/media`, `POST …/media/{media_id}/verify`), `backend/schemas/meetings.py` (`MediaObjectResponse`, list), `backend/database/rls.py`, migration `b9d5f3a7c2e8`, `docs/security/authorization-matrix.md` (route rows, §6e, §8), `backend/tests/test_media_lifecycle.py` (new, 8).
- Commands/results: ruff clean on changed files; SQLite whole suite 272 passed / 36 skipped (3 min 59 s) and the affected suites (media lifecycle, processor, meetings, fault injection, uploads) 22 passed / 4 skipped after the review fix; PostgreSQL 16.15 as `homayar_app` — 12 focused modules (media lifecycle, ledger, RLS, processor, uploads, meetings, matrix, tenant scope, stage checkpoints, fault injection, PostgreSQL guarantees, storage providers) 94 passed (3 min 11 s) (RLS probe: nothing visible without context, tenant-bound rows only, re-homing refused, policy forced; migration round trip head → `a8c4e2f6d1b9` → head with a live and a deleted conversation: one ORIGINAL per recording with the same key/digest/size, ingestion and job links, recording relinked, `active`/`deleted` states); `infra/ci/check-migrations.sh` exit 0; explicit `alembic downgrade -1` (table and column gone) / `upgrade head` / `alembic check` clean on `homayar_mig`, 35 forced policies, partial unique index verified.
- Security: media responses never carry the storage key; reads are tenant- and record-scoped through the conversation; verification requires `meeting:update` + CSRF and answers `404` for a foreign object; audits carry ids, kind, state, reason, size and integrity only; on meeting deletion the state changes before the bytes go, under delete-after-processing the recording is marked deleted only once the bytes are gone (the WP06 fault-injection invariant is kept).
- Limitations: no producer writes derived kinds yet; `retention_pending` is not entered automatically; failed removals stay `deletion_pending` without a retry sweep; legal holds have no route or permission yet (service + audit only); the storage backend recorded by the backfill is the one configured at migration time.
- Reviewer decision: self-review plus two independent model reviews (Claude Opus 4.6, Gemini 3.1 Pro through the antigravity CLI): one accepted finding fixed (a closed ORIGINAL retires its recording), three refuted against the code — details in PROGRESS session 10 part C; product commit `d8066f90`, nothing pushed.

### T04 — Retry-safe finalize/abort and reconciliation (validated locally, 2026-09-11)

- Product files: `backend/storage/base.py` (`ObjectListing`, `iter_objects`, `purge_incomplete`), `backend/storage/local.py`, `backend/storage/s3.py`, `backend/jobs/media_reconciliation.py` (new: `reconcile_media_objects`), `backend/config.py` (four `MEDIA_RECONCILIATION_*` settings), `backend/celery_app/__init__.py` + `backend/jobs/tasks.py` (beat entry `media-reconciliation`), `docs/security/authorization-matrix.md` (§6f, §8), `backend/tests/test_media_reconciliation.py` (new), `backend/tests/test_storage_providers.py` (listing/purge tests), `backend/tests/test_ingestion_ledger.py` (abort/retry, 100× replay, invariants). Website: ADR 0018 (new), WP05 records, lock, PROGRESS.
- Commands/results: ruff clean on changed files; whole SQLite suite 286 passed / 36 skipped (two halves, 281 s + 178 s); focused reconciliation/storage/lifecycle/ledger 50 passed / 5 skipped; PostgreSQL 16.15 as `homayar_app` — PostgreSQL 16 focused batch 71 passed (reconciliation, storage providers, media lifecycle, RLS, ingestion ledger incl. 100x replay + abort/retry, fault injection, tenant scope); earlier 14-module batch 114 passed; alembic check: no schema change. No migration (no schema change).
- Security: the sweep binds the tenant context per workspace and lists only that tenant's prefix (ownership-checked keys); orphan removal is report-only unless enabled; audits carry counts, sizes and ages, never keys; missing bytes are flagged, never fabricated; a listing failure leaves the workspace untouched for the pass.
- Limitations: real S3 listing is unqualified (BLOCKED_EXTERNAL, fake client only); one prefix walk per workspace per pass (no inventory); orphan removal stays disabled until an owner enables it; pre-ledger recordings all carry a media row after the T03 backfill, the route fallback is defensive only.
- Reviewer decision: Gemini Flash 3.8 High (agy, non-blocking) raised 5 findings: 4 confirmed and fixed (StorageError in missing step, budget fall-through after pending retry, unwrapped OSError in local listing/purge, purge tenant validation) plus 2 test renames; nullable-integrity item refuted (column NOT NULL). Reviewed and verified by the orchestrator; product commit `01232be6f7a3abdb4e3c6e24b903bc7c6de69cd6`, nothing pushed.

### T05 — Storage contract suite (partial: adapter contract validated locally, live target BLOCKED_EXTERNAL, 2026-09-16)

- Product files: `backend/tests/test_storage_contract.py` (new: shared suite parametrised over local / S3 fake / opt-in live, controlled `ContractS3Client`, `LocalFaults`, `botocore.stub.Stubber` probe of the real SDK path), `backend/tests/_live_s3.py` (new: opt-in live target, bounds, SDK logging), `backend/tests/conftest.py` + `pyproject.toml` (`live_s3` marker skipped as NOT_RUN/BLOCKED_EXTERNAL unless configured), `backend/storage/s3.py` (bucket-level 404 codes are `s3_bucket_unavailable`, never object-missing), `backend/storage/local.py` (`local_read_failed` / `local_stat_failed` / `local_delete_failed`; only key-shape errnos are `InvalidObjectKeyError`; idempotent delete under concurrent removal), `backend/storage/base.py` (default `purge_incomplete` validates the tenant), `docs/security/authorization-matrix.md` (§6g, §8). Website: ADR 0019 (new), WP05 records, lock, PROGRESS.
- Commands/results: ruff check/format clean on changed files (pre-existing findings in untouched lines only); contract suite 93 passed / 41 skipped (the live parametrisation); relevant SQLite regression (storage providers, media reconciliation, media lifecycle, stage checkpoints, fault injection, uploads API, ingestion ledger, meetings API) 73 passed / 11 skipped; PostgreSQL 16.15 focused batch 184 passed / 41 skipped (contract suite, storage providers, media reconciliation, media lifecycle, RLS, stage checkpoints, ingestion ledger incl. abort/retry and 100x replay, fault injection, tenant scope, PostgreSQL guarantees; 113 s; the skips are the live-target parametrisation); no schema change (no migration, `alembic check` not affected).
- Security: missing / denied / transient / unavailable / conflict / incomplete outcomes are distinct and tested for every operation; `exists()` never answers `False` on a fault; a missing bucket is never a missing object (the reconciliation sweep counts it as unavailable instead of flagging `MISSING`); scope (tenant, key shape, token) is validated before any backend call; a purge is never unscoped; the live run needs explicit opt-in plus a disposable target, removes only exact created keys and keeps credentials out of logs and reprs.
- Limitations: the live S3-compatible run has NOT been executed (BLOCKED_EXTERNAL); fake-client and Stubber evidence covers the adapter, not IAM/bucket policy/encryption or a specific S3-compatible service; local write-path filesystem faults (`mkstemp`, link) propagate as `OSError` (nothing is published, temporaries removed); no multipart protocol exists by design (single bounded `PutObject`, cap `MAX_UPLOAD_SIZE_MB`).
- Reviewer decision: self-review only (single-agent session by owner instruction); two adapter defects found by the suite and fixed before the commit; status `partial` until an owner-designated disposable bucket runs the live parametrisation.

### T06 — Adversarial media fixtures and retries (validated locally, 2026-09-16)

- Product files: `backend/tests/test_media_adversarial_api.py` (new: forged/corrupt/truncated/forged-tag/shape/duration fixtures through `POST /uploads` with real tools, oversize refused while staging, real decoding timeout retried), `backend/tests/test_media_fixtures.py` (`build_media_fixtures`, five new fixtures and three tests), `backend/routers/v1/meetings.py` (`Retry-After: 30` on non-terminal media refusals and storage unavailability), `docs/security/authorization-matrix.md` (§6d row, §8 note). Website: WP05 records, lock, PROGRESS.
- Commands/results: ruff check/format clean on changed files; fixtures + adversarial API 35 passed with the real binaries; SQLite regression (ingestion ledger, uploads API, meetings API, media validation, media lifecycle, fault injection) 67 passed / 7 skipped; PostgreSQL 16.15 batch 61 passed (adversarial API, media fixtures, ingestion ledger, uploads API, media lifecycle; 57 s); no schema change.
- Security: every terminal refusal is contained (no meeting/job/object/quarantine file, original bytes unchanged) and replays deterministically without re-running tools; the byte cap refuses before any subprocess; a tooling timeout never records a rejection and the retry creates exactly one chain; the polyglot is stored as submitted and only served as an attachment.
- Limitations: child-process rlimits are not set (timeouts and probe caps bound the tools); the decode verifies a prefix; the polyglot relies on the attachment disposition; no production run.
- Reviewer decision: self-review only (single-agent session by owner instruction).

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
