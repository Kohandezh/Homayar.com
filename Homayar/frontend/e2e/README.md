# Console end-to-end tests

Real-browser tests for the admin flows of the product console (HOM-189). They
drive Chromium against the running dev stack and the demo workspace seed
(HOM-175). Nothing is mocked: every click goes to the FastAPI backend and the
database.

The Vitest suite in `src/` keeps testing components in jsdom. These tests
cover what only a real browser shows: routing, the admin guard, session
cookies, and text direction.

## Sign-in sessions

The backend limits sign-ins (20 per IP, 10 per account, in 15 minutes), and
the suite used to sign in through the form in almost every test, so a full
run hit that limit. The `setup` Playwright project (`e2e/auth.setup.ts`)
now signs each demo role (owner, admin, member, viewer) in **once per run**
through the real form (the member through the Persian form) and saves the
session with `storageState` under `e2e/.auth/<role>.json`. That folder is
gitignored (the files hold live session cookies) and remade at the start of
every run, so a run never continues a session from an earlier run. The
specs pick the session up with `resumeSession` (`e2e/demo.ts`); only the
language stays per test, because the setup strips `homayar_lang` from the
saved state. See "What you need running" below for the backend's SMTP
settings, which the invitation flow still needs.

## What is covered

| File | Flow |
| --- | --- |
| `admin-overview.spec.ts` | An owner signs in and `/admin` shows every card with real numbers. |
| `processing.spec.ts` | An owner filters `/admin/processing` by state, reads a dead-letter job's drawer (failure, timeline, policy) and sees the failed jobs offer no replay. |
| `usage.spec.ts` | An owner reads `/admin/usage` (subscription, quota meters, event history with filter and paging, plan catalog with no prices) in `en` and `fa`, and a member gets `No access`. |
| `members.spec.ts` | An owner changes a member's role, removes the member, invites them back, and the invited account accepts. |
| `teams.spec.ts` | An owner reads the seeded teams with their member counts and the record-scope banner, opens `Sales`, renames and re-renames `Support`, deactivates and reactivates it, assigns the viewer to `Sales` and revokes the assignment. The page is right to left in Persian, and a member sees no write control. |
| `workspace-settings.spec.ts` | An owner changes `Public share links` and `Duplicate files`, the values survive a reload, and the audit log records `workspace.settings.updated`. An admin sees the privacy controls locked. Cancelling the immediate-retention warning saves nothing. The page is right to left in Persian. |
| `access-control.spec.ts` | A viewer is blocked from `/admin` and `/admin/settings`, and still opens the pages their role allows. |
| `ai-policy.spec.ts` | An owner edits `/admin/ai-policy` with a preset that differs from the current policy, sees the saved version in `Version history` and compares it; an admin reads the policy and history with no edit controls; the page is right to left in Persian. |
| `api-keys.spec.ts` | An owner reads the seeded key, creates one whose secret shows exactly once and revokes it through the confirm dialog; the page is right to left in Persian and a member is blocked. |
| `language-direction.spec.ts` | The language switch flips the page between `dir="rtl"` (fa) and `dir="ltr"` (en), and the choice survives a reload. |

Add a file here as each new admin page lands.

`smtp-sink.ts` is the helper the invitation flow needs, not a flow. Its own
tests (`smtp-sink.test.ts`) run under Vitest with `npm test`, because the SMTP
protocol handling and the token extraction need no browser.

## What you need running

1. **The backend**, with the SMTP settings pointed at the test mail sink:

   ```bash
   cd Homayar
   SMTP_HOST=127.0.0.1 SMTP_PORT=1025 make backend
   ```

   The SMTP part is not optional. A workspace invitation only survives when
   its e-mail is delivered: with no SMTP server the backend revokes the
   invitation it just created, writes a `member.invitation.delivery_failed`
   audit event, and answers 503 (`backend/routers/v1/members.py`). Playwright
   starts the sink itself (`e2e/smtp-sink.ts`) on port 1025 and reads the
   one-time token out of the captured message, which is how the "accept the
   invitation" step works.

   The accept step rebuilds the console URL from that token instead of
   following the link in the mail. The link points at the backend's
   `FRONTEND_URL` (port 5173), not at the server Playwright started, so a
   broken `FRONTEND_URL` path is not covered here.

2. **The demo seed**, in the same database:

   ```bash
   cd Homayar
   make demo-seed
   ```

   It is idempotent, so running it again is safe.

Playwright starts the Vite dev server itself (port 4173, `--strictPort`), so
you do not need `make frontend`.

## Cleanup

The tests change the demo workspace, so they put it back. The restore lives in
hooks, which run even when a step fails:

* `members.spec.ts`, in `afterAll`: the role goes back to administrator, a
  leftover pending invitation is revoked, and a removed member is invited and
  accepted again.
* `workspace-settings.spec.ts`, in `afterEach`: `Public share links` and `Duplicate files` go back to their seeded values.
* `teams.spec.ts`, in `afterAll`: `Support` gets its seeded name and its
  active flag back, and any assignment the run added (the viewer as a `Sales`
  agent) is revoked. A revoked assignment row stays in the drawer as history;
  the server has no way to delete it, and the member count ignores it.
* `ai-policy.spec.ts`, in `afterEach`: the policy document the test started
  from is written back over the API. AI policy versions are append-only, so the
  restore itself appends one more version; the document is back to its starting
  value, but the history keeps growing by one row per owner run. After the
  first owner run the workspace policy is a stored version, so the page badge
  reads "Custom, version N" for good: the API has no way back to version 0,
  the built-in default.
* `api-keys.spec.ts`, in `afterEach` (A2): revokes the exact key the spec created, by name, asserting each DELETE answers 204 or 404; still-active `e2e key ...` keys older than ten minutes are swept as crashed-run leftovers; the seeded `CRM integration (demo)` key is never touched.

Re-running the seed is **not** a repair. `_ensure_workspace` returns an
existing workspace untouched (`backend/database/demo_seed.py`), so `make
demo-seed` never resets the name and never revokes an invitation.

Only a run that dies without its hooks (`Ctrl-C`, a crashed runner, a machine
that sleeps) can leave the workspace changed. Undo it by hand on
`/admin/settings` and `/admin/members`:

* the workspace name, if it ends in `(e2e <number>)`;
* a pending invitation for `admin@demo.example.com`: Revoke;
* `admin@demo.example.com`: set the role back to Administrator, or invite them
  again as an administrator and accept if the row is gone.

## Running

```bash
cd Homayar/frontend
npm install
npx playwright install chromium   # once per machine
npm run test:e2e
```

Useful variants:

```bash
npm run test:e2e -- members.spec.ts        # one file
npm run test:e2e -- --headed               # watch the browser
npm run test:e2e:ui                        # Playwright's UI mode
npm run test:e2e:report                    # open the last HTML report
```

Failures keep a screenshot, a video and a trace under `test-results/`. Open a
trace with `npx playwright show-trace test-results/<path>/trace.zip`.

## Settings

All optional; the defaults match `make backend` and `make demo-seed`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `E2E_API_URL` | `http://127.0.0.1:8001` | The backend. The dev server proxies `/api` here. |
| `E2E_PORT` | `4173` | Port for the dev server Playwright starts. |
| `E2E_BASE_URL` | unset | A console that is already running. Set it and Playwright starts no server. |
| `E2E_SMTP_PORT` | `1025` | Port of the mail sink. Use the same value for the backend's `SMTP_PORT`. |

## When a run fails

* **"The backend at … is not reachable"** - start it, or point `E2E_API_URL`
  at the right port.
* **"Could not open the SMTP sink"** - something else holds port 1025 (MailHog,
  an earlier run). Free it, or set `E2E_SMTP_PORT` and the backend's
  `SMTP_PORT` to the same new port.
* **The invite step shows "The invitation could not be delivered"** - the
  backend has no `SMTP_HOST`, or it points somewhere other than the sink.
* **A sign-in fails** - the demo seed is missing from the database the backend
  uses. Run `make demo-seed`.

## CI

There is no CI job yet. It needs a runner that can start PostgreSQL, run the
migrations and the demo seed, and start the backend; that is a separate
ticket. Everything the job would need is already here: `npm run test:e2e`,
with `CI=true` for one retry and `forbidOnly`.
