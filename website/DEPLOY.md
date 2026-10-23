# Deploy the website to Cloudflare Workers

The public site runs on Cloudflare Workers through the OpenNext adapter
(`@opennextjs/cloudflare`). It is served on `https://homayar.com`.

A deploy happens only when a person runs the GitHub Actions workflow
**Release website** by hand. A push or a pull request never deploys.

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

   Optional: add required reviewers to `production`. Then every release waits
   until a reviewer approves it. Turn this on when anyone besides the owner has
   write access to the repository.

6. **Contact form.** Do the steps in "Contact form delivery" below before the
   first release that contains the contact form code.

Never write the token or the account ID in a file in this repository.

## Run a release

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

Only one release runs at a time. A second run waits for the first one.

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
