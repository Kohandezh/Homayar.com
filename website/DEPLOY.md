# Deploy the website to Cloudflare Workers

The public site runs on Cloudflare Workers through the OpenNext adapter
(`@opennextjs/cloudflare`). It is served on `https://homayar.com`.

There are two ways to deploy. Both run the same job, in
`.github/workflows/deploy-website.yml`:

- **Auto-deploy website** starts after every completed CI run on the `main`
  branch. It deploys only when CI passed on a merged push to `main` and the
  commit changes the website. Then it waits for an approval. When CI passed on
  a merged push but the website did not change, only the Deploy job is
  skipped. Any other run (a failed CI run, a CI run started by hand, a fork
  pull request whose branch is named `main`) shows both jobs as skipped. See
  "Automatic deploy after a merge".
- **Release website** is the manual backup. A person starts it by hand. See
  "Run a release by hand".

A pull request never deploys.

## One-time setup (a person does this once)

1. **Zone.** Add `homayar.com` to Cloudflare and point the domain's nameservers
   to the two Cloudflare nameservers. Wait until the zone is "Active".
2. **Old DNS records.** Remove any `A`, `AAAA` or `CNAME` record for `homayar.com`
   and `www.homayar.com`. Cloudflare cannot create a Worker custom domain on a
   hostname that already has a CNAME record. The first deploy creates the new
   records and the certificates.

   After you remove the old records, the site stops answering until the first
   release finishes. So do this step right before the first release, not earlier.
   When wrangler runs in CI (not in a terminal), it also asks Cloudflare to replace
   conflicting DNS records for the custom domains. Removing them by hand first is
   still the safer way.
3. **API token.** In the Cloudflare dashboard, create an API token with these
   permissions, and limit the zone permissions to `homayar.com`:
   - Account › Workers Scripts › Edit
   - Account › D1 › Edit (the contact form database, see "Contact form delivery")
   - Zone › Workers Routes › Edit
   - Zone › DNS › Edit
4. **Account ID.** Copy the account ID from the Cloudflare dashboard (Workers &
   Pages overview, right side).
5. **GitHub environment.** In the GitHub repository, open Settings › Environments
   and create the environment `production`. Add two environment secrets:
   - `CLOUDFLARE_API_TOKEN`: the token from step 3
   - `CLOUDFLARE_ACCOUNT_ID`: the account ID from step 4

   In the same environment, set **Deployment branches and tags** to
   "Selected branches and tags" and add only `main`.

   Why: the branch rule stops a changed workflow on another branch from getting
   the secret. The workflow also fails before install when `ref` is not on `main`,
   so a branch that nobody reviewed is never deployed.

   Also add required reviewers to `production` (see "Approval gate" below).
   Without a reviewer, an automatic deploy starts as soon as CI passes.

   Do this before the change that adds the automatic deploy is merged, because
   that merge is itself a website change. If `production` does not exist, the
   first run that uses it creates it with no rules and no secrets. So create
   `production` and add the required reviewers and the `main` branch rule first.
   Only then add the two secrets and the variable `TURNSTILE_SITE_KEY`.

6. **Contact form.** Do the steps in "Contact form delivery" below before the
   first release that contains the contact form code.

Never write the token or the account ID in a file in this repository.

## Automatic deploy after a merge

The workflow **Auto-deploy website** (`.github/workflows/auto-deploy-website.yml`)
works like this:

1. A pull request is merged. This is a push to `main`.
2. **CI** runs on that commit and passes.
3. GitHub starts **Auto-deploy website** for the same commit. The run name shows
   the commit SHA, so the approver sees which commit waits.
4. The job **Find website changes** checks if the commit changes the website.
   This job gets no secret.
5. If it does, the job **Deploy** starts and waits for an approval on the
   environment `production`.
6. A reviewer approves. Then the job builds, deploys and runs the smoke test,
   the same steps as a manual release (see "Run a release by hand").

When the commit has no website change, the run stops after step 4, and the job
**Deploy** shows as skipped.

### What counts as a website change

A commit is a website change when it changes one of these paths:

- any file under `website/`
- `.github/workflows/deploy-website.yml`
- `.github/workflows/auto-deploy-website.yml`

A deleted file counts. A file moved out of `website/` counts too. A path that only
looks similar does not count, for example `docs/website/x` or `website-old/x`.
A change to `ci.yml` or `release-website.yml` does not count.

The job compares the commit with the nearest earlier commit on `main` whose CI
passed. Why: when two merges land close together, CI cancels the older run. So
the newer merge still counts the website change of the older one. If there is
no such commit, the job compares with the parent commit and writes a notice in
the log.

### What never deploys

- A CI run that failed or was cancelled.
- A CI run of a pull request. This includes a pull request from a fork, even
  when its branch is named `main`.
- A CI run started by hand (Actions › CI › Run workflow). It is not a merge.
- A commit with no website change.
- A commit that is not on `main`. The deploy job also checks this before it
  installs anything.

### Approval gate (one-time setup)

Open Settings › Environments › `production`:

1. Turn on **Required reviewers**. Add the people who may approve a deploy. For
   now this is the owner. One approval is enough.
2. Keep **Deployment branches and tags** on "Selected branches and tags", with
   only `main`. An automatic deploy passes this rule, because a run that another
   workflow starts runs on the default branch, `main`.
3. **Prevent self-review**: with this setting on, nobody can approve a deploy
   from a run that they started. For a manual release, that is the person who
   clicked Run workflow. An automatic deploy starts from the merge, so GitHub
   counts the person who merged the pull request (check the name on the page of
   the first automatic run). So when the only reviewer also merged the pull
   request, GitHub blocks the approval. Turn this setting on only when a second
   reviewer exists.

To approve, open the run, click **Review deployments**, select `production`, and
click **Approve and deploy**. **Reject** stops the deploy, and the run fails.
A deploy that nobody approves fails after 30 days.

## Run a release by hand

1. Open the repository on GitHub, then Actions › **Release website** › Run workflow.
2. `ref` is the branch, tag or commit SHA to release. The default is `main`.
   It must be on `main` (an older commit or tag on `main` is fine, for a rollback).
   If it is not, the job fails before it installs anything.
3. The job installs, lints, builds (`pnpm build:cf`, with the Turnstile site key from
   the GitHub variable `TURNSTILE_SITE_KEY`; the job stops if it is empty), deploys (`pnpm run deploy`),
   and then runs a smoke test against the live site:
   - `https://homayar.com/` answers 307 or 200
   - `https://homayar.com/fa` answers 200
   - `POST https://homayar.com/api/contact` with a valid body and **no** Turnstile
     token answers 403. This proves the route is live and protected, and it stores
     no fake lead. A 503 here means `TURNSTILE_SECRET_KEY` is not set.
   - `https://www.homayar.com/fa` answers 301 to `https://homayar.com/fa`

   Each check retries for up to 5 minutes, because DNS and the certificate of a
   new custom domain can take a few minutes. The job fails if a check still fails.

With required reviewers on `production`, a manual release also waits for an
approval.

## Only one deploy at a time

Manual and automatic deploys use one concurrency group, `deploy-website`, on
the deploy job. A concurrency group lets only one job of the group run at a
time. A running deploy is never cancelled.

- While a deploy runs, the next one waits with the status **Pending**.
- GitHub keeps only one pending job in the group. When one more deploy comes,
  GitHub cancels the pending one, and the new one takes its place. An automatic
  deploy ships all of `main` up to its commit, so the cancelled change still
  ships with it. (A manual release of an older `ref` can also take that place.)
- A deploy that waits for approval holds the group. GitHub's docs do not say
  this directly, but GitHub users report it. So a newer deploy waits as
  **Pending** behind an older one that waits for approval, and you cannot
  approve the newer one yet. Reject the older one first. Then the newer one asks
  for approval.

## Re-run a deploy

- **Re-run jobs** on an **Auto-deploy website** run deploys the same commit
  again. It waits for an approval again. With **Prevent self-review** on, the
  person who clicks **Re-run jobs** starts the deployment, so that person cannot
  approve it.
- Or run **Release website** by hand with any `ref` on `main`.

## Known limits

- A later merge that does not change the website does not retry a failed or
  rejected deploy. The later merge compares with the last commit whose CI
  passed, so it finds no website change. Re-run the failed run, or run
  **Release website** by hand.
- Approving an old waiting deploy deploys that old commit. Re-running an old CI
  run on `main` also starts an automatic deploy of that old commit. So approve
  the newest commit, and reject an older one.
- Nothing skips a deploy because a newer commit is on `main`. The approval is
  that check.

## Expected error lines in the logs

No error lines are expected for normal requests.

The contact form writes these lines when something goes wrong. They hold only the
request id, the attempts count and a short error code, never the visitor's data:

- `contact_delivery_failed {"id":"...","attempts":1,"error":"..."}`: the e-mail
  to the owner failed. The request is safe in D1 and the cron sends it again.
- `contact_store_failed {"id":null,"attempts":0,"error":"store_failed"}`: D1
  failed. The visitor saw an error and can try again.
- Warnings (`console.warn`): `contact_retry_failed` (a retry failed, it is tried
  again later), `contact_alert_failed` (the alert webhook did not answer 2xx),
  `contact_update_failed` (the row could not be updated after a send).

## Contact form delivery

The contact form (`/fa/contact`, `/en/contact`) works like this:

1. Cloudflare Turnstile checks that the visitor is a person.
2. The request is stored in the D1 database `homayar-website-contact` (table
   `contact_requests`). The code creates the table. There is no migration step.
3. The Worker e-mails the request to the owner through Cloudflare Email Service.
   The reply-to address is the visitor's e-mail, so "Reply" answers the visitor.
4. If the e-mail fails, the visitor still sees "Request received" with a reference
   number, the row is marked `failed`, and an alert goes out. A cron trigger runs
   every 10 minutes and sends failed requests again, up to 5 attempts in total.
   After the 5th failed attempt the row is `dead` and a second alert goes out.
5. The same cron deletes every request older than 180 days.

The visitor gets no e-mail. Sending mail to an address a visitor typed would let
anyone use the form to send mail to strangers.

### Before you start: plan and beta

- Cloudflare Email Service sending is in **open beta**.
- Cloudflare says sending to any recipient needs the **Workers Paid** plan. On the
  Workers Free plan you can only send to a **verified destination address** in
  your account. The form sends only to one address (`CONTACT_TO`), so either
  plan works: on Free, verify that address first (Compute › Email Service, or
  Email Routing › Destination addresses) and wait for the confirmation e-mail.
- Your zone must use Cloudflare DNS (it already does for `homayar.com`).

Docs: https://developers.cloudflare.com/email-service/

### One-time setup

Run the `wrangler` commands in `website/`, logged in to the Cloudflare account
that owns `homayar.com` (`npx wrangler login`, or a token in `CLOUDFLARE_API_TOKEN`).

1. **Email Sending for homayar.com.** Either run
   `npx wrangler email sending enable homayar.com`, or in the dashboard open
   Compute › Email Service › Email Sending › Onboard Domain and choose
   `homayar.com`. Cloudflare adds these DNS records: MX and an SPF TXT record on
   `cf-bounce.homayar.com`, a DKIM TXT record on `cf-bounce._domainkey.homayar.com`,
   and a DMARC TXT record on `_dmarc.homayar.com`. Keep them. They are usually
   active within 5 to 15 minutes. Check with
   `npx wrangler email sending list homayar.com`.
   The sender address is `website@homayar.com` (set in `wrangler.jsonc` as
   `vars.CONTACT_FROM`). The binding may only send from that address.
2. **Turnstile widget.** In the dashboard open Turnstile › Add widget. Name it
   `homayar.com contact`, add the hostname `homayar.com`, and keep the mode
   "Managed". Do not add `localhost`: local development uses Cloudflare's test
   keys. You get a **site key** (public) and a **secret key** (secret).
3. **GitHub variable.** In the repository, open Settings › Secrets and variables ›
   Actions › Variables and add `TURNSTILE_SITE_KEY` with the site key. It is a
   variable, not a secret: the key is public and is built into the page.
   The release job fails if it is empty.
4. **Worker secrets.** In `website/`:

   ```bash
   npx wrangler secret put TURNSTILE_SECRET_KEY        # the Turnstile secret key
   npx wrangler secret put CONTACT_TO                  # the inbox that gets the requests
   npx wrangler secret put CONTACT_ALERT_WEBHOOK_URL   # optional, see below
   ```

   Without `TURNSTILE_SECRET_KEY` every request is refused (503). Without
   `CONTACT_TO` every request is stored and marked failed, and nothing is sent.
5. **Alert webhook (optional).** `CONTACT_ALERT_WEBHOOK_URL` gets a POST with the
   JSON body `{"text": "..."}` for every failed delivery. A Slack incoming-webhook
   URL works as it is. For Telegram use
   `https://api.telegram.org/bot<BOT_TOKEN>/sendMessage?chat_id=<CHAT_ID>`.
   The text has only the request id, the attempts count and an error code.
   Without the webhook, failures are still in the Worker logs (see above).
6. **API token.** The release token needs **Account › D1 › Edit** (listed in the
   one-time setup above). On the first deploy wrangler finds the database
   `homayar-website-contact` by name, or creates it (automatic provisioning,
   https://developers.cloudflare.com/workers/wrangler/configuration/#automatic-provisioning).
   There is no database id in the repository and you do not need to add one.
   The deploy needs D1 Edit to create or find the database. If the release fails
   with a permission or authorization error about D1, add D1 Edit to the token
   and run the release again.

   For the e-mail binding, Cloudflare's docs name no token permission
   (not confirmed). The only e-mail permission the docs name is "Email Sending:
   Edit", and only for the REST API
   (https://developers.cloudflare.com/email-service/get-started/send-emails/).
   If the release fails with an authorization error about the `send_email`
   binding or Email Sending, add that permission to the token (if your
   dashboard lists it) and run the release again.

### See failed deliveries

```bash
cd website
npx wrangler d1 execute homayar-website-contact --remote --command "SELECT id, created_at, status, attempts, last_error FROM contact_requests WHERE status != 'sent'"
```

`status` is `pending` (not sent yet, or the Worker stopped half-way), `failed`
(tried, will be tried again) or `dead` (5 attempts failed, never tried again).
`last_error` is a short code: `not_configured` (`CONTACT_TO` is missing), an
Email Service code such as `E_SENDER_NOT_VERIFIED`, or `send_failed`.
To send a `dead` row again, set it back to `failed` with fewer attempts, for
example `UPDATE contact_requests SET status = 'failed', attempts = 4 WHERE id = '<id>'`.

### Live check after the first release

1. Send a test request from https://homayar.com/en/contact. It arrives in the
   `CONTACT_TO` inbox within a minute, and the page shows a reference number.
2. Break delivery on purpose: `npx wrangler secret delete CONTACT_TO`. Send
   another test request. The page still shows a reference number.
   You get the alert (if the webhook is set), and the query above shows the row
   with `status = failed` and `last_error = not_configured`.
3. Fix it: `npx wrangler secret put CONTACT_TO` with the real inbox again. A row
   is retried 5 minutes after its last attempt, and the cron runs every 10 minutes.
   So once 5 minutes have passed since step 2, the cron sends the request within
   10 minutes, and the row becomes `sent`.

### Local development

```bash
cd website
cp .dev.vars.example .dev.vars   # Cloudflare's always-pass test keys, a placeholder inbox
npx -y pnpm@11.1.2 dev            # http://localhost:3000/en/contact
```

`next dev` gets the D1 and e-mail bindings through `initOpenNextCloudflareForDev()`
in `next.config.ts`. Without `NEXT_PUBLIC_TURNSTILE_SITE_KEY` the page uses the
test site key `1x00000000000000000000AA`, which always passes.
Nothing is e-mailed locally. The local e-mail binding saves each message as a
`.txt` and an `.html` file in a `miniflare-*/email/` folder under the system temp
folder. Local D1 rows: `npx wrangler d1 execute homayar-website-contact --local --command "SELECT * FROM contact_requests"`.
To run the cron locally: `npx -y pnpm@11.1.2 build:cf`, then
`npx -y pnpm@11.1.2 preview --test-scheduled`, then open
`http://localhost:8787/__scheduled?cron=*/10+*+*+*+*`.

Tests: `npx -y pnpm@11.1.2 test` (Vitest, no network).

## www.homayar.com

`www.homayar.com` is a second custom domain on the same Worker. `worker.ts`
answers every request on that host with **301** to `https://homayar.com` and the
same path and query. It runs before the Next.js app, so it covers every path,
also `/api/*` and `/_next/*`.

For this reason `wrangler.jsonc` sets `assets.run_worker_first: true`. Without it,
Cloudflare serves static files (for example `/_next/static/*`) before the Worker
runs, and `www.homayar.com/_next/static/...` would answer 200. Note: with this
setting every request, also a static file, counts as a Worker request.

`workers_dev: false` and `preview_urls: false` turn off the `*.workers.dev` URLs.
The site is only public on the two custom domains.

## Rollback

Pick one:

- Roll back to the previous Worker version (needs the same token and account ID
  in your shell):

  ```bash
  cd website
  npx wrangler rollback
  ```

  `npx wrangler versions list` shows the versions if you need a specific one
  (`npx wrangler rollback <VERSION_ID>`).

- Or run **Release website** again with `ref` set to an older commit SHA or tag on `main`.

A rollback lasts only until the next automatic deploy. The next merge that
changes the website ships the newest `main` again. So fix `main` first (for
example, revert the bad pull request), or reject that deploy.

## Local build and preview

Use pnpm 11.1.2. No Cloudflare login is needed for these commands.

```bash
cd website
npx -y pnpm@11.1.2 install --frozen-lockfile
npx -y pnpm@11.1.2 lint
npx -y pnpm@11.1.2 test
npx -y pnpm@11.1.2 build:cf        # writes .open-next/worker.js and .open-next/assets/
npx -y pnpm@11.1.2 preview         # local Workers runtime on http://localhost:8787
```

`pnpm preview` serves the last `build:cf` output, so run `build:cf` first.
Add `--port <port>` to use another port:
`npx -y pnpm@11.1.2 exec opennextjs-cloudflare preview --port 8802`.

### Check the www redirect locally

In `pnpm preview`, wrangler takes the first entry of `routes` (`homayar.com`) and
rewrites the host of every local request to it. So a request with
`Host: www.homayar.com` reaches the Worker as `homayar.com`, and the 301 does not
show. To test the redirect, write a temporary config without `routes` into
`.wrangler/` (git ignores it) and preview with that config:

```bash
cd website
node -e '
const fs = require("fs");
const ts = require("typescript");
const { config, error } = ts.parseConfigFileTextToJson("wrangler.jsonc", fs.readFileSync("wrangler.jsonc", "utf8"));
if (error) throw new Error(ts.flattenDiagnosticMessageText(error.messageText, "\n"));
delete config.$schema;
delete config.routes;
config.main = "../../worker.ts";
config.assets.directory = "../../.open-next/assets";
fs.mkdirSync(".wrangler/www-check", { recursive: true });
fs.writeFileSync(".wrangler/www-check/wrangler.json", JSON.stringify(config, null, 2) + "\n");
'
npx -y pnpm@11.1.2 exec opennextjs-cloudflare preview --config .wrangler/www-check/wrangler.json --port 8802
```

Then, in a second terminal:

```bash
curl -sI -H 'Host: www.homayar.com' 'http://127.0.0.1:8802/fa?x=1'   # 301, Location: https://homayar.com/fa?x=1
curl -sI -H 'Host: homayar.com' 'http://127.0.0.1:8802/fa?x=1'       # 200
```

The temporary config is made from `wrangler.jsonc` (the TypeScript parser reads
the comments). It differs only in `routes`, `$schema`, and the two paths, which
point back to `website/` from `.wrangler/www-check/`.
