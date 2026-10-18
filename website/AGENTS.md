# Public website (homayar.com)

Next.js 16.4.2 with the App Router and `proxy.ts`. Deployed to Cloudflare Workers with OpenNext. See `DEPLOY.md`.

## Commands (run in `website/`, use pnpm)

- `pnpm lint`
- `pnpm build`
- `pnpm build:cf` (the OpenNext build for Cloudflare)

## Boundaries

- Work only in `website/`. Do not read `Homayar/`. It is a different app.

## Next.js

This Next.js version has breaking changes. APIs, conventions, and file structure may differ from your training data. Read the relevant guide in `website/node_modules/next/dist/docs/` before writing code. Follow deprecation notices.
