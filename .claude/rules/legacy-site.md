---
paths:
  - "src/**"
  - "content/**"
  - "drizzle/**"
  - "scripts/**"
  - "next.config.ts"
  - "drizzle.config.ts"
---

# Legacy public site

This is the old public site. It runs on-prem in Docker and is being retired.

- Fix bugs here only. New public-site features go in `website/`.
- Do not read `website/` or `Homayar/` while working here, unless the task is moving a page to `website/`.
- Commands (run at the repo root, use pnpm): `pnpm lint`, `pnpm build`, `pnpm test:e2e`.
- This Next.js version has breaking changes. Read the relevant guide in `node_modules/next/dist/docs/` before writing code.
