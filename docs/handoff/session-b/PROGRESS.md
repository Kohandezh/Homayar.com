# Session B progress and verification

Requirement mapping uses the existing backlog ids (no renumbering). "Mocked" means frontend tests
with explicit fixtures; no step below ran against a live backend, a real AI provider, Google,
Asanak, an email service or a deployment unless stated.

## B1 — Website → console entry points (WP21-T02/T03 scope, website only)

- Header (inner pages, desktop + mobile menu) and the homepage's own header gain **Sign in**
  (`/app/login`); mobile menus and the homepage final CTA gain **Create account**
  (`/app/register`). Labels for all six site locales in `src/lib/console-links.ts`.
- Plain `<a>` links (the console is another application; Next `multi-zones` guidance), base from
  `NEXT_PUBLIC_CONSOLE_URL`, default `/app` (same-origin nginx route). No trial or price claim.
  Contact sales stays `/contact`; the simulated `/demo` is unchanged.
- `tsconfig.json` excludes and `eslint.config.mjs` ignores the nested `Homayar/` product clone
  (it was type-checked and linted by the website tooling; `docs/architecture/workspace-structure.md`
  already stated the exclusion).
- Verification (website `cfcc2c4` + this change): `tsc --noEmit` clean; `eslint` clean on the
  changed files; `next build --webpack` succeeded (71 routes) and the prerendered HTML contains the
  `/app/login` and `/app/register` hrefs. The default Turbopack build could not fetch Google Fonts
  through the sandbox proxy (network limitation of this environment, not a code error).
- Pre-existing, not changed: `src/components/header.tsx` `react-hooks/set-state-in-effect` lint
  error (menu close on route change).
