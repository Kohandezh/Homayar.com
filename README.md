# Homayar — Conversation Intelligence Platform

**One backend platform · Four deployment modes · Governed AI routing**

![Homayar full platform feature and deployment map](docs/assets/homayar-platform-map.webp)

Homayar is a multi-tenant, Persian-first conversation intelligence platform for
call centers and enterprise customer-service teams. AI agents listen, transcribe,
analyze, score, and coach, and every customer conversation becomes insight.

Built by [KSF Company](mailto:info@ksf.ir).

## One product, policy-driven deployment

The same contracts, tenant isolation, and audit trail run in every mode. Only
the execution boundary changes.

| Mode | Data plane | AI processing |
| --- | --- | --- |
| **Online SaaS** | Homayar Cloud (managed control and data plane) | Approved providers via governance, optional customer BYOK |
| **Connected on-premise** | Customer-controlled data and storage | AI routing selector: local AI, customer BYOK, approved online provider, or hybrid stage routing; policy-controlled egress |
| **Hybrid** | Local audio and sensitive data | Approved remote processing per stage over an encrypted, governed exchange |
| **Fully offline / air-gapped** | Isolated on-premises environment | Local models only, offline license and update bundles, **zero public egress** |

## Platform capabilities

- **Identity and tenancy**: tenant provisioning, RBAC, row-level security, MFA, SSO, SCIM, delegated admin
- **Audio and media ingestion**: upload, streaming, SIP, WebRTC, PBX, media validation, object storage
- **Persian and multilingual speech**: STT, VAD, diarization, overlap, timestamps, Persian normalization, WER/CER
- **Voice automation**: inbound/outbound voice agent, TTS, voicemail, human transfer, call admission control
- **Quality management**: scorecards, evidence, transcript and speaker correction, coaching, calibration
- **Analytics and copilot**: search, trends, root cause, dashboards, executive briefs, conversation copilot
- **Workflows and integrations**: rules, approvals, actions, tasks, email, calendar, messaging, Odoo
- **AI provider governance**: provider registry, BYOK, model and prompt versions, rollback, routing policy, budget kill-switch
- **Usage and commercial control**: metering, quotas, plans, billing, licenses, entitlements
- **Privacy, security, and compliance**: PII redaction, consent, encryption, retention, legal hold, deletion, immutable audit
- **Data lifecycle**: import/migration, data quality, reprocessing, feedback-to-evaluation
- **Operations and resilience**: health, logs, traces, cost, backup/restore, RPO/RTO, disaster recovery
- **Developer platform**: REST API, webhooks, SDK, connectors, export, idempotency
- **Safety and release gates**: security checks, migration checks, contract tests, capacity tests, model qualification, rollback drills

The product contract is [`Homayar/AppVision.md`](Homayar/AppVision.md); boundary
decisions are recorded as ADRs in [`docs/architecture/decisions/`](docs/architecture/decisions/).

## Repository layout

| Path | What it is | Stack |
| --- | --- | --- |
| [`Homayar/backend`](Homayar/backend) | Product backend | FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL (RLS), Celery + Redis, Neo4j projection, local and governed AI providers |
| [`Homayar/frontend`](Homayar/frontend) | Product console | Vite, React 19, TanStack Query, Vitest |
| `src/`, `drizzle/` (root) | Public website (homayar.com) | Next.js 16, Tailwind v4, Drizzle + Neon, Stripe, Playwright |
| [`infra/`](infra/) | Deployment and CI | Compose, Docker, nginx, egress policy, backups, CI checks |
| [`docs/`](docs/) | Architecture, security, operations, research, handoff records | |
| `.claude/` | Shared Claude Code skills and agents for this repo | |

### Product quick start

```bash
cd Homayar
uv sync --frozen
uv run pytest backend/tests -q -rs          # backend tests (SQLite; set TEST_DATABASE_URL for PostgreSQL)
npm --prefix frontend ci && npm --prefix frontend test
```

---

# Public website

The rest of this document covers the public commerce website at the repository root.

## Stack

- **Next.js 16** (App Router, React Server Components, TypeScript)
- **Tailwind CSS v4** — custom dark enterprise design system
- **Neon PostgreSQL** + **Drizzle ORM** (with static-data fallback when no DB is configured)
- **Stripe Checkout** (test mode) — hosted payment page + webhook order recording
- **Playwright** — full E2E QA suite (desktop + mobile projects)
- Programmatic product artwork — every product image is generated in-repo
  (`scripts/generate-images.mjs`, SVG → PNG via sharp; no external image APIs)

## Getting started

```bash
pnpm install
cp .env.example .env.local   # fill in values (see below)
pnpm dev                     # http://localhost:3000
```

The site is fully functional **without** any environment variables — the
catalog serves the static seed data in `src/data/`, and checkout/demo forms
degrade gracefully with clear messages.

## Environment variables (`.env.local`)

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Neon Postgres connection string |
| `STRIPE_SECRET_KEY` | Stripe **test** secret key (`sk_test_…`) |
| `NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY` | Stripe **test** publishable key (`pk_test_…`) |
| `STRIPE_WEBHOOK_SECRET` | Signing secret for the `checkout.session.completed` webhook |
| `NEXT_PUBLIC_APP_URL` | Canonical URL, e.g. `https://homayar.vercel.app` |

## Database (Neon)

```bash
pnpm db:push       # create tables from src/db/schema.ts
pnpm db:seed       # load 30 products, categories, blog posts, case studies
```

Schema lives in `src/db/schema.ts`; generated SQL migrations in `drizzle/`.
Entities: categories, products, product_images, leads, demo_requests,
checkout_sessions, orders, order_items, blog_posts, case_studies.

## Stripe (test mode)

1. Put test keys in `.env.local` (dashboard.stripe.com/test/apikeys).
2. Register a webhook endpoint `https://<your-domain>/api/webhooks/stripe`
   for the `checkout.session.completed` event; copy its signing secret into
   `STRIPE_WEBHOOK_SECRET`.
3. Test card: `4242 4242 4242 4242`, any future expiry, any CVC.

Carts mixing monthly **and** yearly subscriptions fall back to a one-time
"first period" payment (Stripe Checkout cannot mix intervals in one session);
single-interval carts create real subscriptions.

## Product images

```bash
pnpm images        # regenerates public/hero.png, public/og.png, public/products/*.png
```

Deterministic per product slug — re-runs produce identical art.

## QA

```bash
pnpm build && pnpm test:e2e                                  # local production build
PLAYWRIGHT_BASE_URL=https://<deployment> pnpm test:e2e       # against deployed site
```

## Deploy (Vercel)

```bash
npx vercel --prod
```

Set the environment variables above in the Vercel project settings.
`NEXT_PUBLIC_APP_URL` must match the deployment URL (used for SEO canonical
URLs, OG tags, sitemap, and Stripe redirect URLs).
