# Homayar Production Architecture Assessment

Status: accepted implementation baseline  
Date: 2026-08-18

## Decision

Use an incremental integration architecture (master prompt Option C):

- Keep the existing Next.js application as the public, SEO-facing website.
- Keep the existing React application as the authenticated product and serve it at `/app`.
- Expose the FastAPI application at `/api/v1` behind the same reverse proxy and origin.
- Make PostgreSQL the source of truth for identity, tenants, permissions, meetings,
  processing jobs, entitlements, usage, billing metadata, and audit records.
- Keep Neo4j as an optional analytical projection for relationship exploration. It is
  not authoritative for authentication, authorization, quota, or billing.
- Use Redis and Celery for asynchronous processing, coordination, rate limiting, and
  short-lived state. Durable job state remains in PostgreSQL.
- Use an object-storage interface with S3-compatible storage for SaaS and MinIO or a
  protected local filesystem for on-premise installations.

This choice preserves the public URLs and the useful analysis UI while replacing the
unsafe transactional core in controlled stages. A full Next.js rewrite would add risk
without improving tenant isolation or processing reliability.

## Current State

### Public website

- Next.js 16 App Router with static-export support and localized commercial pages.
- Product catalog and dual USD/IRR display already exist.
- Demo and contact requests can persist to the current website database when configured.
- The website has no shared identity or entitlement boundary with the product app.
- Root lint currently traverses the embedded application and generated assets; repository
  boundaries and lint inputs need to be made explicit.

### Product application

- React/Vite frontend uses a bearer token in `localStorage` and decodes roles client-side.
- FastAPI exposes two overlapping authentication systems and uses one JWT secret for
  several unrelated token purposes.
- Users, calls, and auth state are stored directly in Neo4j without tenant identifiers.
- Authorization is global-role based (`admin`, `supervisor`, `agent`) and cannot enforce
  workspace isolation.
- Uploads accept base64 payloads, trust filename extensions, write directly to a shared
  directory, and do not verify media content or duration before processing.
- The worker can enqueue duplicate work, has no durable idempotency key, and records
  progress only in Redis.
- Speaker labels are inferred from timestamp parity rather than diarization.
- AI failures silently produce random mock results in normal runtime paths.
- No relational migrations, entitlement ledger, billing domain, retention jobs, share
  controls, API keys, webhook delivery ledger, or production-ready deployment stack exist.
- Baseline verification on 2026-08-18: frontend build fails with TypeScript errors,
  frontend lint fails, and all 22 backend tests error during fixture setup.

## Target Runtime Topology

```text
Internet
  |
  v
Reverse proxy / TLS / request limits / security headers
  |-- /              -> Next.js public web
  |-- /app/*         -> Vite product assets and SPA fallback
  |-- /api/v1/*      -> FastAPI
  |-- /healthz       -> liveness
  `-- /readyz        -> dependency-aware readiness

FastAPI
  |-- PostgreSQL     -> transactional source of truth
  |-- Redis          -> broker, rate limits, ephemeral coordination
  |-- S3 / MinIO     -> private recordings and exports
  |-- Neo4j          -> optional analytics projection
  `-- provider ports -> ASR, diarization, LLM, SMS, email

Celery workers
  `-- validate -> transcribe -> diarize -> analyze -> index -> finalize usage
```

All application requests carry an authenticated user and an explicitly resolved active
workspace. Repository methods require a workspace identifier; endpoints never accept a
workspace identifier as authorization on its own.

## Transactional Domain

The initial PostgreSQL model will include:

- `users`, `auth_identities`, `sessions`, `verification_tokens`, `otp_challenges`
- `workspaces`, `memberships`, `workspace_invitations`
- `plans`, `subscriptions`, `entitlements`, `usage_reservations`, `usage_events`
- `meetings`, `processing_jobs`, `transcript_segments`, `meeting_summaries`, `action_items`
- `share_links`, `audit_events`, `api_keys`, `webhook_endpoints`, `webhook_deliveries`

Workspace roles are `owner`, `admin`, `member`, and `viewer`. A user may have different
roles in different workspaces. Legacy `admin`, `supervisor`, and `agent` values are mapped
during migration and are not trusted as tenant authorization.

## Authentication Boundary

- Short-lived access sessions are delivered in secure, HttpOnly, same-site cookies for
  browser clients. Bearer access remains available for API clients.
- Rotating refresh sessions are persisted as hashes and can be revoked per device.
- Email/password signup requires verification; reset and verification tokens are hashed,
  single-use, scoped, and independently signed.
- Google OAuth uses authorization code flow with state and PKCE.
- Iranian mobile login uses an SMS provider interface; Asanak is one implementation.
- Identity linking requires proof of control of both identities. Email or phone similarity
  alone never merges accounts.
- Development/demo credentials and provider mocks are enabled only by explicit environment
  flags and fail closed in production.

## Entitlement and Usage Invariant

The free demo grants exactly 1,800 processing seconds to the workspace. Processing follows
an atomic reservation protocol:

1. Validate the server-measured media duration.
2. Lock the workspace entitlement row in PostgreSQL.
3. Reject when committed plus active reservations plus requested seconds exceeds the grant.
4. Insert a unique reservation tied to the meeting and processing attempt.
5. Queue only after the reservation commits.
6. Commit actual processed seconds exactly once on success.
7. Release the reservation on terminal failure or cancellation.

Unique constraints and row locking are the correctness boundary. Redis is not the quota
source of truth. Retries reuse the same idempotency key and cannot consume quota twice.

## Processing Boundary

Provider-neutral stages:

1. Upload to a quarantine key using streaming multipart input.
2. Enforce size, extension, MIME signature, duration, and decompression limits.
3. Move validated media to tenant-scoped private storage.
4. Reserve quota and create a durable processing attempt.
5. Transcribe through an ASR provider.
6. Diarize through a diarization provider; never fabricate speaker identity.
7. Produce schema-validated summary, evidence-linked findings, and action items.
8. Index tenant-scoped text for search and project optional relationships to Neo4j.
9. Commit usage and publish an auditable completion event.

Mocks are permitted in tests and explicitly labeled demo environments only. Provider
failure in production produces an actionable failure state, never random business data.

## Deployment Profiles

### SaaS

- Managed PostgreSQL, Redis, and S3-compatible object storage.
- Horizontally scalable API and worker services.
- External Google, Asanak, email, ASR, diarization, and LLM providers selected through
  environment configuration and feature flags.

### On-premise

- Docker Compose baseline containing reverse proxy, public web, product web, API, worker,
  PostgreSQL, Redis, MinIO, and optional Neo4j.
- Provider profile supports local Whisper-compatible ASR, local diarization, and local LLM
  endpoints without outbound data transfer.
- Customer-managed TLS, encryption keys, retention, backup targets, and enterprise OIDC.

## Migration Sequence

1. Establish configuration validation, relational models, Alembic migrations, and test DB.
2. Implement unified identity, sessions, workspace resolution, RBAC, and audit logging.
3. Add entitlement reservation and usage metering with concurrency tests.
4. Replace upload and worker paths with storage/provider abstractions and durable jobs.
5. Migrate the current read APIs and UI to workspace-scoped `/api/v1` contracts.
6. Add search, export, sharing, actions, billing/upgrade surfaces, and administration.
7. Connect public signup/login/demo CTAs to `/app` while preserving public SEO routes.
8. Add SaaS and on-premise containers, CI, backup/restore, runbooks, and release gates.
9. Import legacy data with deterministic mappings and optional Neo4j projection rebuild.

Backward compatibility is provided through temporary `/api` adapters where necessary.
No destructive legacy migration runs automatically in production.

## Release Gates

- No cross-tenant reads, writes, search results, exports, shares, job status, or files.
- Quota concurrency tests prove the 1,800-second demo cap cannot be exceeded.
- Auth reset, verification, OAuth state, OTP throttling, session rotation, and account-linking
  tests pass.
- Upload adversarial tests pass and private objects are never directly enumerable.
- Worker retry and duplicate-delivery tests prove idempotency and exact usage accounting.
- Frontend build, lint, unit tests, and commercial-to-product Playwright journeys pass.
- API test suite, migration tests, secret scan, dependency scan, and container health checks
  pass.
- Both SaaS and on-premise profiles start from documented commands on a clean machine.

