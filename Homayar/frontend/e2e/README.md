# Console end-to-end tests

Real-browser tests for the admin flows of the product console (HOM-189). They
drive Chromium against the running dev stack and the demo workspace seed
(HOM-175). Nothing is mocked: every click goes to the FastAPI backend and the
database.

The Vitest suite in `src/` keeps testing components in jsdom. These tests
cover what only a real browser shows: routing, the admin guard, session
cookies, and text direction.

## What is covered

| File | Flow |
| --- | --- |
| `admin-overview.spec.ts` | An owner signs in and `/admin` shows every card with real numbers. |
| `processing.spec.ts` | An owner filters `/admin/processing` by state, reads a dead-letter job's drawer (failure, timeline, policy) and sees the failed jobs offer no replay. |
| `members.spec.ts` | An owner changes a member's role, removes the member, invites them back, and the invited account accepts. |
| `workspace-settings.spec.ts` | An owner edits a workspace setting and the audit log records `workspace.settings.updated`. |
| `access-control.spec.ts` | A viewer is blocked from `/admin` and `/admin/settings`, and still opens the pages their role allows. |
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
* `workspace-settings.spec.ts`, in `afterEach`: the workspace name goes back.

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
