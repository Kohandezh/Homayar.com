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

## B6 — Results and privacy (WP07-T06, WP12-T02/T03 console scope)

Product commit `63eb30d` (parent `955bdd4`).

- `MeetingResults`: accessible tabs (roving tabindex, RTL-aware arrow keys, Home/End, labelled
  tab panels); summary overview, decisions and key points with localized evidence badges and
  "show in transcript" links resolved through `GET /meetings/{id}/evidence` (findings citing an
  earlier revision say so); `summary_not_ready` is shown as "no summary" with the API's
  analysis reason, not as an error; mock analysis disclosed.
- Transcript: speaker filter, focusable segments (`dir="auto"` for mixed text), speaker rename
  for editors, placeholders marked in transcript, summary and actions.
- Redaction (enforced policy): notice with masked count and classes, the detector's limits and
  "audio is not masked". Owner-only original view needs an explicit confirmation that states it
  is audited; it is fetched once (`?view=original`, no background refetch, `gcTime: 0`) and
  removed on hide/unmount; 403 `original_access_denied` explained. Single-value reveal uses the
  audited reveal route with a reason, keeps the value only in component state (verified absent
  from the query cache) and clears it on "hide"; 404/409 refusals explained. Speaker renames
  invalidate the transcript key exactly so the original is not re-read.
- Share links: localized, honest revocation note (no claim about already-downloaded copies).
- Gaps recorded: G10 (summary/actions/evidence do not declare their view), G11 (no
  workspace-bound media URL for inline audio playback; download remains).
- Verification (mocked): `vitest run` 10 files / 71 tests passed; `eslint` clean; build succeeded.

## B7 — Trial and usage presentation (WP18 console scope; backend owns entitlement)

Product commit `68e081f` (parent `63eb30d`).

- Audio usage comes only from `GET /billing/usage` (`audio_seconds`, seconds) and is shown in
  whole minutes and seconds, floored (`formatAudioSeconds`); the previous one-decimal minutes
  could show 1,799 s as "30 min".
- `AllowanceNotice` (overview and usage page, owners/admins): total allowance when the
  entitlement has no period end (the demo grant), what "reserved" means (held for recordings in
  processing, settled or released by the server — the empty-transcript charging policy is not
  described), and an exhausted state ("new uploads will be refused") with a contact-sales link
  and no price. The existing usage page already states that pricing is unpublished and billing
  manual; it now uses role affordances from `lib/roles.ts`.
- Members no longer call `/billing/usage` (it returned 403); the overview says usage is visible
  to owners/admins (gap G5). Viewers no longer see an upload button. Subscription states
  (`trialing`, `past_due`, `expired`) are labelled.
- The commercial trial is **not** marked complete: the 1,800 s grant exists server-side
  (`ENABLE_FREE_DEMO`, `DEMO_QUOTA_SECONDS`) but live email verification, real AI providers and
  the charging policy are open.
- Verification (mocked): `vitest run` 11 files / 77 tests passed; `eslint` clean; build succeeded.

## B8 — Responsive, accessible, Persian-first verification

Product commit `71e36aa` (parent `68e081f`).

Browser checks (Claude built-in browser, 2026-09-25). **Mocked:** the console ran on Vite
(`127.0.0.1:5183`) against a Session B fixture HTTP server (`127.0.0.1:5184`, script kept in the
session scratchpad, not committed; every fixture title says FIXTURE). No product backend, AI
provider, email, Google or Asanak was involved. The website ran from its production build
(`next start -p 5185`).

| Check | Result |
| --- | --- |
| Console, Persian, 375 px and ~470 px: workspace chooser, overview, meeting list, upload refusal, results, dead-letter replay, login, phone-unavailable | Rendered RTL with Persian digits and Solar Hijri dates; no horizontal page scroll (`scrollWidth == clientWidth`) |
| Masked-value chips inside RTL sentences | Measured glyph order `[EMAIL_1]` left-to-right (bidi isolation works) |
| Audited original view | One `GET …/transcript?view=original` after confirmation; hidden again with no further original request; no console errors |
| Sign-in journey (fixture) | Wrong password → Persian error; success → workspace chooser; `localStorage` holds only `homayar_lang` (no password or token) |
| Keyboard | Found and fixed: closed mobile drawer links were focusable off-screen → now `visibility: hidden`; Escape closes it; focus moves into the original-view confirmation |
| RTL details | Found and fixed: back arrow mirrored in RTL; clock durations in Persian digits; privacy region labelled |
| Website, 375 px (en, fa) and 768 px `/pricing` | New links present (`ورود`/`ساخت حساب`, `Sign in`/`Create account` → `/app/login`, `/app/register`); no overflow at 375/768; header fits on one row at 768 |

Automated (mocked): `vitest run` 12 files / 80 tests passed; `eslint .` clean; `npm run build`
succeeded.

Not run: any live backend or deployment; real email/SMS/Google flows; real AI processing; a
committed-baseline local backend (the product needs a Python environment that was not created in
this session, and Session A's runtime was not used); desktop-width screenshots were too scaled in
the pane to judge by eye (layout checked by measurement at 375/768 px).

## Outstanding visual / accessibility issues

- The console's font (Geist) has no Persian glyphs; Persian text uses the system fallback. The
  website uses Vazirmatn. A Persian webfont for the console is a dependency decision (not added).
- Console pages outside this journey (members, settings, search, shared meeting, invitation) are
  only partly localized and still show English server messages through `ErrorState`.
- Website: pre-existing `react-hooks/set-state-in-effect` lint error in `src/components/header.tsx`;
  the homepage header overflows below ~300 px (pre-existing).
- Website `/pricing` and `/catalog` advertise USD prices with cart/checkout, while the product
  console states pricing is unpublished and billing manual — a WP21-T01/T02 claims decision for
  the owner; Session B did not change it.

## Integration requirements (shared files Session B did not edit)

1. Merge/rebase: Session B's seven product commits sit on `d1cc5d4`; Session A's `42543aa`
   (WP08-T01) also sits on `d1cc5d4` and touches only backend files, so no textual conflict is
   expected. Pin the integrated product commit in `infra/product-source.lock` afterwards.
2. Push the product overlay branch before any CI run in `ci` mode (gap G8).
3. `infra/docker/console-web.Dockerfile`: pass `VITE_ENABLE_GOOGLE_AUTH` / `VITE_ENABLE_PHONE_AUTH`
   only when the backend enables and configures those providers (until gap G1 exists).
4. A website deployment without the `/app/` proxy must set `NEXT_PUBLIC_CONSOLE_URL`.
5. Backend gaps G1–G7, G9–G11 in `BACKEND_GAPS.md` (proposals, not accepted contracts).
6. Refresh Graphify against the integrated source (not done here).

## Next frontend task

Localize the remaining console pages and `ErrorState` by error code (members, settings, search,
shared meeting, invitation), then run the journey against a committed local backend baseline in
an isolated environment; after gap G11, add synchronized audio/transcript playback (WP12-T03).
