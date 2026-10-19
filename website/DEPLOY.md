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

Never write the token or the account ID in a file in this repository.

## Run a release

1. Open the repository on GitHub, then Actions › **Release website** › Run workflow.
2. `ref` is the branch, tag or commit SHA to release. The default is `main`.
   It must be on `main` (an older commit or tag on `main` is fine, for a rollback).
   If it is not, the job fails before it installs anything.
3. The job installs, lints, builds (`pnpm build:cf`), deploys (`pnpm run deploy`),
   and then runs a smoke test against the live site:
   - `https://homayar.com/` answers 307 or 200
   - `https://homayar.com/fa` answers 200
   - `POST https://homayar.com/api/contact` with a valid body answers 202
   - `https://www.homayar.com/fa` answers 301 to `https://homayar.com/fa`

   Each check retries for up to 5 minutes, because DNS and the certificate of a
   new custom domain can take a few minutes. The job fails if a check still fails.

Only one release runs at a time. A second run waits for the first one.

## Expected error lines in the logs

No error lines are expected for normal requests.

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
