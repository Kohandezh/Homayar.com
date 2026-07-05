# Homayar.com — Agentic AI Voice Analyzer

Production SaaS commerce website for **Homayar**, the agentic AI voice analyzer
for call centers and enterprise customer-service teams. AI agents listen,
analyze, score, and coach — every customer conversation becomes insight.

Built by [KSF Company](mailto:info@ksf.ir).

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
