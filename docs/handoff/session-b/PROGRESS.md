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

## B3 — Workspace selection and access loss (WP03-T03/T04 console scope)

Product commit `34f3dfd` (parent `ec29aaa`).

- `lib/workspace-selection.ts`: the remembered id only selects among `/auth/me` workspaces; one
  workspace is selected, several need an explicit choice (`WorkspaceGate`), none shows an honest
  empty state with a support link. A remembered workspace that vanished is reported, never
  replaced by another tenant.
- `api.ts` emits `homayar:workspace-denied` for `403 workspace_access_denied` /
  `permission_denied`; `AuthProvider` re-reads the session (coalesced; permission refresh at most
  every 30 s), marks a refused workspace unavailable for the session (gap G9), clears cached
  content when the active workspace changes or is lost, and updates the role in place after a
  permission reduction. Invitation acceptance stays reachable without a selected workspace.
- Role-derived affordances are centralised in `lib/roles.ts` from the product matrix (gap G4);
  the header selector is localized and disables refused workspaces; search is disabled with no
  workspace.
- Verification (mocked): `vitest run` 7 files / 43 tests passed; `eslint` clean; `npm run build`
  succeeded.

## B4 — Upload and validation feedback (WP05-T01 console scope)

Product commit `ca5c99b` (parent `34f3dfd`).

- `components/platform/UploadPanel.tsx` replaces the inline form: byte transfer (upload
  progress, explicitly "transfer only"), then "server is checking the recording", then the
  accepted meeting opens (idempotent replay passed in router state). A cancel button aborts the
  transfer; leaving the page aborts it and `beforeunload` warns while sending.
- `lib/upload-errors.ts` classifies the documented refusal codes. Retry rules: `same_key`
  (network, cancel, 503 + `Retry-After`, `upload_rate_limited`, `concurrent_job_limit_reached`,
  other 5xx) is user-initiated only, after `Retry-After`, with the same `Idempotency-Key`;
  `new_file` (413/422 media refusals, `ingestion_conflict`, `ingestion_target_deleted`) requires
  choosing a file again, which issues a new key; `none` for verification, permission, quota and
  duplicate (with a link to the existing meeting). Quota refusals show remaining/requested from
  the server's `details` in minutes and seconds (floored), plus a contact-sales link; no price.
- Formats shown are the product default `ALLOWED_EXTENSIONS`; numeric size/duration limits are not
  printed (gap G3). Owners/admins see the server's remaining and reserved allowance; members never
  call `/billing/usage` (gap G5).
- Status badges and the meeting list use `lib/status-labels.ts` (Persian and English).
- Verification (mocked): `vitest run` 8 files / 53 tests passed; `eslint` clean; build succeeded.
  A countdown clock-sync bug found by the new test (stale clock showed +1 s) was fixed in both the
  upload panel and the OTP page.

## B5 — Truthful processing status and recovery (WP04-T01, WP06-T05 console scope)

Product commit `955bdd4` (parent `ca5c99b`).

- `ProcessingStatus`: meeting status, job state, "attempt n of m", one row per stage in the
  backend `STAGE_ORDER` with the API's state and localized reason code (unknown reasons shown as
  sent), "not started" for stages without a run while active, mock output disclosed. No
  percentage anywhere (the contract has none).
- `queued` + `retry_scheduled`: the failed attempt's `failure_code` and the scheduled
  `retry_after` time. `failed` with `failure_class: permanent`: no retry offered. `dead_letter`:
  owners/admins get `POST /admin/jobs/{id}/replay` (refusals `job_not_replayable`,
  `replay_limit_reached`, `meeting_deleted`, `retention_expired`, `recording_missing`, quota and
  concurrency explained); other roles are told who can retry. No cancel control (gap G6).
- Polling (`platform-api.ts`): 2.5 s → 5 s → 15 s → 30 s while the observed status is unchanged,
  stops on terminal states and on 401/403/404/410, waits at least `Retry-After`, requests carry
  the query `signal` so navigation/unmount aborts them; React Query never overlaps a query's
  fetches.
- A deleted/tombstoned/inaccessible meeting (404/410) shows an honest message and clears its
  cached transcript, summary, actions, shares, job and search results; share links are fetched
  only after the meeting itself loaded (a 404 previously still fetched them).
- Verification (mocked): `vitest run` 9 files / 63 tests passed; `eslint` clean; build succeeded.
