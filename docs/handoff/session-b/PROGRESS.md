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

## B2 — Console authentication states (WP03 console scope, WP22-T01/T03)

Product commit `ec29aaa` on `session-b/frontend-journey` (parent `d1cc5d4`).

- Persian-first console: `I18nProvider` defaults to `fa` unless the visitor chose English;
  auth pages, auth layout (new language toggle and website link) and session-loading screens are
  localized through `components/auth/auth-copy.ts` (fa/en), following the existing per-feature
  copy convention.
- Error codes, not English server text, choose the message (`lib/api-errors.ts` reads the
  `{"error": {...}}` envelope and `Retry-After`): invalid credentials, rate limits with minutes,
  account exists, invalid/expired verification and reset tokens, invalid phone, OTP cooldown,
  invalid OTP, 422 field errors, network failure, 5xx.
- Phone OTP: countdowns from the server's `expires_in`/`resend_after`, expired code disables
  verification, resend only after the cooldown (server `Retry-After` wins), Persian/Arabic-Indic
  digits accepted, the code is never stored or echoed. 5xx on request = "phone sign-in not
  available" (gap G7).
- Google/phone entry points hidden unless `VITE_ENABLE_GOOGLE_AUTH` / `VITE_ENABLE_PHONE_AUTH`
  (gap G1). Google callback errors `account_link_required` / `google_auth_failed` explained.
- Return path after sign-in: only in-app paths (`lib/return-path.ts`; no `//`, schemes,
  backslashes or auth pages). A live session that expires shows "your session ended" on the
  login page; a first anonymous visit does not. Passwords are cleared from state after success.
- Unverified accounts see a notice in the app layout that uploads need verification (no resend
  endpoint: gap G2). Auth CSS switched to logical properties for RTL.
- Verification (mocked UI tests with fixtures in `src/test/fixtures.tsx`; no live backend,
  Google, Asanak or email): `vitest run` 6 files / 33 tests passed (baseline was 4 / 12);
  `eslint .` clean; `npm run build` (`tsc -b` + `vite build`) succeeded.
