# Public website (homayar.com)

Next.js 16.4.2 with the App Router and `proxy.ts`. Deployed to Cloudflare Workers with OpenNext. See `DEPLOY.md`.

## Commands (run in `website/`, use pnpm)

- `pnpm lint`
- `pnpm build`
- `pnpm build:cf` (the OpenNext build for Cloudflare)

## Boundaries

- Work only in `website/`. Do not read the root `src/` (the legacy site) or `Homayar/`. They are different apps.
- The legacy site is not a source to copy from unless the task is moving a page from it.

## Next.js

This Next.js version has breaking changes. APIs, conventions, and file structure may differ from your training data. Read the relevant guide in `website/node_modules/next/dist/docs/` before writing code. Follow deprecation notices.
