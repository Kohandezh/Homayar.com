---
name: code-review
description: >
  Review a Homayar diff or PR like a senior engineer on this repo: FastAPI correctness,
  tenancy and row-level security, the authorization matrix, jobs (outbox, leases, checkpoints),
  privacy and redaction, air-gap egress, append-only migrations, the product console's
  data and error handling, Persian/RTL, the Next.js 16 site, and test coverage, with
  severity-ranked findings. Use for any code review, PR review, or pre-merge check.
---

# Code Review

The diff is the source of truth. Scope the review to the PR's own root cause; untouched code in the same file is out of scope.

What "correct" means here, in order:

1. `Homayar/AppVision.md`: the canonical product contract.
2. `docs/architecture/decisions/`: ADRs 0001+ (the boundary decisions).
3. `docs/security/` and `Homayar/docs/security/`.
4. `AGENTS.md` (Next.js 16 is not the version you remember; check `node_modules/next/dist/docs/`).

A doc and the code disagreeing is itself a finding; say which one you think is wrong.

## 1. Establish the exact diff

```bash
BASE=$(git merge-base origin/main HEAD)
git diff --stat "$BASE"..HEAD
git diff "$BASE"..HEAD
```

For a stacked PR use the parent slice's tip as BASE. Do not modify the working tree during a review.

## 2. Read the PR description

A change the description never mentions is a yellow flag. A deviation from the stated root cause is a finding.

## 3. Check CI

```bash
gh pr checks <number>
gh run view <run-id> --log-failed
```

Jobs: source contract, public web (lint, build, cPanel build, Playwright), product console (lint, build, Vitest), backend (migrations + pytest on PostgreSQL 17, Redis, ffmpeg). A red job is Critical by itself. Skipped tests in the `-rs` summary that should have run (postgres, ffmpeg) are a finding.

## 4. Assess

Every finding: `file:line`, what is wrong, why it matters, how to fix.

### Tenancy, authorization, and security (backend)

- Every tenant-scoped query runs under the tenant context and RLS (ADR 0005, `backend/database/rls.py`, `tenant_context.py`). A query that bypasses it, or a new table without an RLS policy, is **Critical**.
- Authorization follows the matrix and record scope (ADR 0004, `backend/security/authorization.py`). A route with no role check, or one that relies on the console to hide a button, is **Critical**.
- Another tenant's record answers 404, not 403 or 200.
- API keys are limited to their capabilities (ADR 0013).
- Tokens, OTP codes, raw phone numbers, and transcript PII never reach logs, URLs, or error bodies. `backend/security/tokens.py` owns tokens; don't hand-roll one.
- OTP and rate limits (`otp_service.py`, `rate_limit_service.py`) not weakened.
- Uploads: size and type enforced on the server (`backend/media/validation.py`, ADR 0017).
- Secrets from the environment only; nothing server-side in the console bundle or `NEXT_PUBLIC_*`.

### Privacy and data lifecycle

- Redacted vs original transcript routing (ADRs 0023–0028): a consumer reading original text where policy says redacted is **Critical**.
- Retention, legal hold, tombstone-first deletion (ADRs 0020–0022): deletion paths go through tombstones; a hard delete that skips the journal is **Critical**.

### Deployment modes and egress

- Cloud and on-prem/air-gapped both ship. A new outbound call not declared to the egress policy (`backend/providers/egress.py`) or that fails `infra/ci/airgap_egress_check.py` is **Critical**.
- Provider fallback keeps the same policy (ADR 0030); falling back to a cloud model from on-prem is Critical.

### Jobs and consistency

- Work is dispatched through the outbox (ADR 0007), executed under a lease with fencing (0008), classified on failure (0009), checkpointed per stage (0011).
- Usage is written to the ledger with its invariants (0010).
- Stage status is truthful (0003, 0015): never report success for a degraded or skipped stage.
- Idempotency on retried writes (`backend/security/idempotency.py`).

### Backend code

- Pydantic models for request/response bodies; routers thin, logic in `backend/services/`.
- Errors use the envelope `{"error": {code, message, request_id, details?}}`.
- **Migrations are append-only.** New Alembic revision; editing one that has run anywhere is **Critical**. Downgrade exists or is explicitly refused.
- Reads over growing tables paginate.
- Ruff (`E,F,I,B,UP,S`, length 100) and mypy clean.

### Product console (`Homayar/frontend`)

- Server data through TanStack Query; requests only through `src/lib/api.ts` / `platform-api.ts`. A stray `fetch`/`axios` in a component is a finding.
- Errors normalised by `api-errors.ts` and worded by `error-messages.ts`; no raw server string on screen.
- Every async view handles loading, empty, error (with retry), 401, 403, and degraded data.
- Strings in both `fa` and `en`; logical CSS (`ms-*`, `pe-*`, `text-start`) so RTL works. `ml-*`/`left-*` is a finding.
- Accessibility: keyboard reachable, visible focus, labelled inputs and icon buttons.

### Public site (repo root, Next.js 16)

- Uses Next 16 APIs as documented in `node_modules/next/dist/docs/`, not remembered ones.
- Server components by default; `"use client"` only where needed.
- Still works with no env vars (static data fallback, graceful checkout/demo).
- Stripe webhook verifies the signature; prices come from the server, never the client.
- Zod on route-handler input. Drizzle schema changes ship with generated `drizzle/` SQL.
- Must survive the static cPanel build (`pnpm build:cpanel`).

### Tests

- New behaviour has a test that fails without the change.
- Refusal paths covered: 401, 403, other tenant, validation, limits.
- PostgreSQL-only guarantees have a `postgres`-marked test.
- A new `skip`/`xfail` or a deleted test without a written reason is a finding.

### Simplicity

- One implementation per business rule; reuse before create.
- No abstraction for a single use; no new dependency the stack already covers.
- A boundary moved without an ADR is a finding.

## 5. Severity

- **Critical**: security or tenancy hole, PII leak, egress violation, edited migration, data loss, red CI, broken user path. Block merge.
- **Important**: missing test for new behaviour, missing refusal path, missing UI state or translation, unbounded query, contract change without its caller. Fix before merge.
- **Minor**: naming, dead code, stale doc line. Note, don't block.

Don't inflate severity; over-severity destroys trust in the review.

## 6. Deliver

```
## Summary
<one paragraph: what the PR does, overall verdict: approve / changes requested>

## Critical
- file:line — problem — why — fix

## Important
## Minor
## What's good
```

## 7. Act on feedback

When the author disagrees, re-read the code with their argument in mind. Concede when they are right; hold the finding with evidence when they are not.
