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
| `ingestions.spec.ts` | An owner reads the real `/admin/ingestions` (table or empty state); with the ledger mocked: paging by cursor, the state filter, the source filter reaching the server, the rejected row's reason, the duplicate row's earlier ingestion and the meeting link, and no hash in the page. Persian is right to left; a member gets `No access` with no list request. |
| `usage.spec.ts` | An owner reads `/admin/usage` (subscription, quota meters, event history with filter and paging, plan catalog with no prices) in `en` and `fa`, and a member gets `No access`. |
| `members.spec.ts` | An owner changes a member's role, removes the member, invites them back, and the invited account accepts. |
| `teams.spec.ts` | An owner reads the seeded teams with their member counts and the record-scope banner, opens `Sales`, renames and re-renames `Support`, deactivates and reactivates it, assigns the viewer to `Sales` and revokes the assignment. The page is right to left in Persian, and a member sees no write control. |
| `locations.spec.ts` | An owner creates a dealer location in `Atlantis` (seeing both save warnings), filters `/admin/locations` by kind, city and status, renames the location and deletes it through the confirm. A member gets `No access`. |
| `workspace-settings.spec.ts` | An owner changes `Public share links` and `Duplicate files`, the values survive a reload, and the audit log records `workspace.settings.updated`. An admin sees the privacy controls locked. Cancelling the immediate-retention warning saves nothing. The page is right to left in Persian. |
| `access-control.spec.ts` | A viewer is blocked from `/admin` and `/admin/settings`, and still opens the pages their role allows. |
| `ai-policy.spec.ts` | An owner edits `/admin/ai-policy` with a preset that differs from the current policy, sees the saved version in `Version history` and compares it; an admin reads the policy and history with no edit controls; the page is right to left in Persian. |
| `scorecards.spec.ts` | An owner creates a scorecard under a per-run key with two criteria, sees `Publish` disabled at a total of 90, fixes it to 100 and publishes; a version published through the API during an edit shows the conflict alert, and `Load version N` refills the draft; a member reads the scorecard with no `Edit` or `New scorecard`; the page is right to left in Persian. |
| `api-keys.spec.ts` | An owner reads the seeded key, creates one whose secret shows exactly once and revokes it through the confirm dialog; the page is right to left in Persian and a member is blocked. |
| `privacy.spec.ts` | An owner places a legal hold on a seeded meeting from its `Data and retention` panel, finds it on `/admin/privacy` under `All holds` and `Due in 30 days` (not `Review due`), releases it from the register, and downloads a data subject export after the warning; an admin gets `No access` and sees no data panel on the meeting. |
| `shared-links.spec.ts` | An owner creates a workspace share through the API, finds it on `/admin/privacy` under `Active` (the token is never on the page), cancels and then confirms `Revoke`, and sees the revoked row under `Revoked` with no Revoke button; the card is right to left in Persian. |
| `voice-operations.spec.ts` | The demo owner (not a platform operator) sees `0 / 200` on the `Voice capacity` card, no `Voice operations` link, and `No access` on `/admin/voice` with no capacity request. With `/auth/me` changed to `system_role: "operator"` and every voice request mocked, the owner drains and resumes nodes through the confirm dialog, reads the runtime metrics, runs `Recover now` through the confirm dialog (Cancel sends nothing, confirm one mocked POST and the toast), and a deployment limit of 0 shows the off text instead of the `All nodes` meter; the Persian page is right to left with Persian digits. Any unmocked API write is aborted in the browser, so nothing is written to the backend. |
| `voicemails.spec.ts` | The owner reads the real, empty `/voicemails` inbox and the `Voicemail` card's empty text on `/admin`. With every voicemail request mocked (the seed has none), the owner filters by priority, opens a row, reads the transcript and the follow-up action, and marks it handled through to `Reopen`; the Persian page is right to left, and a viewer gets no write control. Any unmocked API write is aborted in the browser. |
| `language-direction.spec.ts` | The language switch flips the page between `dir="rtl"` (fa) and `dir="ltr"` (en), and the choice survives a reload. |
| `organization.spec.ts` | Read only. The owner picks the demo organization in the sidebar switcher and reads the overview (30 meetings for the workspace), the members table (the owner row has no row action), the workspaces table and the empty scope grants page; one page is right to left in Persian. A member, who belongs to no organization, sees no switcher and gets `No access` on the organization URL. Any organization request that is not a GET is aborted in the browser and fails the test. |
| `call-report.spec.ts` | Read only. The owner opens a completed meeting (found through `GET /meetings?status=completed`, no fixed id) and reads the call report: the five sections in order, the `mock_analysis` notice, a `Play evidence at` button that sets `?at=` and moves the audio to that time without opening the advanced area, and `Show in transcript` opening the transcript. A failed meeting says its report is incomplete; the viewer sees `Correct transcript` disabled with its reason; the Persian page is right to left. Any meeting request that is not a GET is aborted in the browser and fails the test. |
| `playback.spec.ts` | Read only. The owner opens a completed meeting (found through `GET /meetings?status=completed`, no fixed id), sees the `Keyboard shortcuts` list, opens `Transcript and tools`, plays from a turn's `Play from m:ss` (the audio moves to that time and `?at=` is set), seeks to another turn and sees it marked `aria-current`, toggles play with `K`, filters by speaker (the request carries `speaker_key` and every turn shown is that speaker's) and by a time range (`start_ms` and `end_ms`), and gets the alert for an inverted range. The Persian page is right to left with Persian shortcut labels. With the playback route answered `410` and `404` in the browser, the matching message shows and there is no `<audio>`. Any meeting request that is not a GET is aborted in the browser and fails the test. |
| `transcript-correction.spec.ts` | The owner corrects one turn's text of `Quarterly pricing review #8` (found by title through `GET /meetings?status=completed&limit=50`) with a reason, sees `Saved as revision N` (N read from the API first, never assumed), walks `Revision history` (source, `You`, the reason, `Current`), views the previous revision read-only and comes back, and the call report shows `Based on an earlier transcript`. A speaker move sends only the new speaker and shows it on the segment; a viewer gets no `Edit turn at` but opens the history; the Persian page is right to left with Persian labels. |
| `trends.spec.ts` | Read only. The owner opens the real `/trends`: the `Issue trends` heading, `Processing coverage` with its `of`, the `Call reasons` row, and every percent with an `of` in the same cell (the seed is below the 30-call sample, so cells show `Too few calls`; no count is fixed). The Persian page is right to left. With `/api/v1/trends/**` answered in the browser, a computed cell's `Show calls` opens `Supporting calls`, its link goes to a real completed meeting (found through `GET /meetings?status=completed`) and the meeting page loads, and a withheld cell has no `Show calls`. Any API request that is not a GET is aborted in the browser and fails the test. |
| `organization-writes.spec.ts` | With the real owner session and a mocked organization API: add an analyst with a validity window, change the end date, revoke; create a workspace; add a member to a workspace; add and revoke a scope grant; an administrator gets no `Administrator` role to grant. Each test asserts that no organization request reached the real API. |

Add a file here as each new admin page lands.

`organization-writes.spec.ts` is the one spec that mocks: it answers every
`/api/v1/organizations**` request with `page.route`, from in-memory state.
The demo backend is shared, so these writes must not reach it, and the seed
has no analyst or executive to give a scope grant to. The sign-in and every
other request stay real.

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
* `privacy.spec.ts`, in `afterEach`: the run notes the legal holds that were
  already placed before it did anything, and releases over the API (with the
  same owner session) only the holds it added. Holds placed by someone else
  stay as they were. A failed release does not stop the others; the test fails
  once after the loop. The restore never requests a deletion. The audit log
  keeps the `media.legal_hold_placed`, `media.legal_hold_released` and
  `subject.exported` events of every run; the API has no way to remove them.
* `locations.spec.ts`, in `afterAll`: every location whose `code` starts with
  this run's `e2e-loc-` prefix is deleted over the API, so a failed step
  leaves nothing behind.
* `ai-policy.spec.ts`, in `afterEach`: the policy document the test started
  from is written back over the API. AI policy versions are append-only, so the
  restore itself appends one more version; the document is back to its starting
  value, but the history keeps growing by one row per owner run. After the
  first owner run the workspace policy is a stored version, so the page badge
  reads "Custom, version N" for good: the API has no way back to version 0,
  the built-in default.
* `api-keys.spec.ts`, in `afterEach` (A2): revokes the exact key the spec created, by name, asserting each DELETE answers 204 or 404; still-active `e2e key ...` keys older than ten minutes are swept as crashed-run leftovers; the seeded `CRM integration (demo)` key is never touched.
* `shared-links.spec.ts`, in `afterEach`: revokes over the API (with the same owner session) only the share this run created on `Customer portal login issue #10`, by its id, asserting the DELETE answers 204 or 404. The route is idempotent, so a share the test already revoked answers 204 again. Seeded shares are never touched. A revoked share stays in the list under Revoked as history; the API has no way to delete it. The audit log keeps the `meeting.share_created` and `meeting.share_revoked` events of every run.
* `transcript-correction.spec.ts`, in `afterEach`: for every correction the run made on `Quarterly pricing review #8`, posts one more correction over the API (same owner session, the fresh current revision as base) that puts each touched segment's text and speaker back, then re-reads the transcript and asserts every touched segment is exactly as it started. Transcript revisions are immutable, so the restore appends new revisions and the history of that meeting grows by a few rows per run; the API has no way to remove them. No other meeting is touched.

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

The `console-e2e` job in `.github/workflows/ci.yml` (HOM-224) runs the full
suite on every pull request and every push to `main`. Inside the runner it:

1. starts PostgreSQL and Redis as service containers;
2. installs the backend with `uv sync --frozen`, runs `alembic upgrade head`
   and the demo seed. The seed prints the demo logins with their password, so
   the job drops all of the seed's standard output. Errors still show;
3. starts the API on `127.0.0.1:8001`, with `SMTP_HOST=127.0.0.1` and
   `SMTP_PORT` set to `E2E_SMTP_PORT`, and waits for `/readyz`;
4. runs `npm run test:e2e` with `CI=true` (one retry, no `test.only`).
   Playwright starts the dev server and the SMTP sink itself.

The browser install has 15 minutes, the tests 12 and the whole job 25. The
install step also sets `Acquire::Retries "3"` for apt, because `--with-deps`
downloads the system libraries from the Ubuntu mirror on every run.

Everything runs on `127.0.0.1` with mock providers. No shared backend is used,
and no secret is needed.

When the job fails, and also when it is cancelled by a timeout, it prints the last 200 lines of the API log and uploads
the artifact `console-e2e-report` (kept 7 days). It holds `playwright-report/`
and `test-results/` (traces, screenshots, videos). Download it from the run's
Summary page, unzip it, and open the report with
`npx playwright show-report playwright-report`.

To reproduce the job locally, start PostgreSQL and Redis, export the job's
`env` values (point the URLs at your ports), and run the same steps:

```bash
cd Homayar
uv sync --frozen
uv run alembic upgrade head
uv run python -m backend.database.demo_seed
SMTP_HOST=127.0.0.1 SMTP_PORT=1025 uv run uvicorn backend.main:app --host 127.0.0.1 --port 8001 &
cd frontend
npm ci
npx playwright install chromium
CI=true npm run test:e2e
```

Use a fresh database: the suite changes the demo workspace (see "Cleanup").
