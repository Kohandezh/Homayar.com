# Demo Site Runbook (demo.homayar.com)

This runbook covers the demo site `https://demo.homayar.com:8080` (HOM-383). The company manager
tests the product there. It is filled with the demo seed (HOM-365: 270 scripted customer calls
in four teams). Sample data never mixes with production data. No secret value is in this file.

The demo runs on the same local server as production (`homayar-local`), as a second, separate
Homayar Compose project. Production is described in [`app-server.md`](app-server.md). The relay
and the tunnel are in [`relay-tunnel.md`](relay-tunnel.md).

## Status

- **Not on the server yet.** The scripts and this runbook come with HOM-383. The owner steps
  and the gpu steps below have not run on `homayar-local`.
- **Rehearsed** on the admin Mac on 2026-11-10 with the real release images (see "Rehearsal").

## What Is Separate From Production

| Item | Production (`app.homayar.com`) | Demo (`demo.homayar.com`) |
| --- | --- | --- |
| Compose project | `homayar-onprem` | `homayar-demo` (every Compose call passes `--project-name homayar-demo`) |
| Home folder | `/home/gpu/homayar` | `/home/gpu/homayar-demo` (mode 700) |
| Compose files | `compose.onprem.yaml` + `compose.onprem.app-server.yaml` | `compose.onprem.yaml` + `compose.onprem.demo.yaml` |
| Published port | `127.0.0.1:8080` | `127.0.0.1:8090` (the only one) |
| Subnets | `172.29.10.0/24` to `172.29.14.0/24` | `172.29.30.0/24` to `172.29.34.0/24` |
| Volumes and networks | `homayar-onprem_*` | `homayar-demo_*` |
| Images | `homayar/api:<tag>`, `homayar/console-web:<tag>` | the same image ids, with the demo's own tags `homayar-demo/api:d8e39a994f8b` and `homayar-demo/console-web:d8e39a994f8b` |
| Secrets | `~/homayar/secrets/` | `~/homayar-demo/secrets/`, generated new by `--apply` |
| Host nginx site | `app.homayar.com.conf` | `demo.homayar.com.conf` |

The demo's own image tags matter: a later production cleanup of old image tags cannot remove the
images the demo runs on. `--undo` removes only the two demo tags, never an image id.

## How a Request Arrives

```text
visitor -> relay 193.93.169.205:8080 (frps, raw TCP, the same relay port as app.homayar.com)
        -> tunnel -> frpc on the local server
        -> host nginx 127.0.0.1:10443 (PROXY protocol; TLS ends here; site demo.homayar.com, by SNI)
        -> 127.0.0.1:8090 (Docker published port of the demo proxy)
        -> demo proxy (172.29.31.10) -> console-web (/app/) or api (/api/v1/)
```

The demo proxy trusts the client IP only from the demo edge gateway `172.29.30.1`
(`infra/nginx/trusted-proxy/demo-host-tls-front.conf`). The api trusts `X-Forwarded-For` only
from the proxy's fixed address `172.29.31.10`.

## Files

### Demo home (user `gpu`)

| Path | What |
| --- | --- |
| `/home/gpu/homayar-demo/src/` | The source tree, copied from the admin Mac with `git archive` (gpu step 1). The scripts run from here. |
| `/home/gpu/homayar-demo/dc` | The one Compose command line of the demo (mode 700), like `~/homayar/hc`. |
| `/home/gpu/homayar-demo/config/settings.env` | Settings, no secret (mode 600). |
| `/home/gpu/homayar-demo/config/.steps/` | One marker per finished `--apply` step, so a second `--apply` repeats nothing. |
| `/home/gpu/homayar-demo/secrets/` | Generated secret files and `secrets.env` (mode 700, files 600). Never `cat` them. |
| `/home/gpu/homayar-demo/secrets/demo-login` | The four demo login e-mails and their one password (mode 600). |
| `/home/gpu/homayar-demo/logs/` | One log per `--apply` or `--undo --yes` run. No secret is in a log. `--check` and `--undo` without `--yes` write nothing. |

### Host nginx (root)

| Path | What |
| --- | --- |
| `/etc/nginx/sites-available/demo.homayar.com.conf` | The demo site, written by `demo-site-nginx.sh --apply` (mode 644). |
| `/etc/nginx/sites-enabled/demo.homayar.com.conf` | The symlink that enables it. |
| `/etc/nginx/sites-available/demo.homayar.com.conf.bak-<UTC stamp>` | A backup copy, written before each change. |
| `/etc/letsencrypt/live/demo.homayar.com/` | The certificate (owner step 2). |
| `/var/log/nginx/demo.homayar.com.{access,error}.log` | The site's own logs. |

## Settings

`--apply` writes `config/settings.env` once. The stack runs in production mode:

| Setting | Value | Why |
| --- | --- | --- |
| `APP_ENVIRONMENT` | `production` | The demo behaves like the real product. |
| `PUBLIC_ORIGIN`, `CORS_ORIGINS` | `https://demo.homayar.com:8080` | The public URL. |
| `TRUSTED_HOSTS` | `demo.homayar.com,127.0.0.1` | `127.0.0.1` for the container health check. |
| `SECURE_COOKIES` | `true` | The browser reaches the demo over HTTPS only. |
| `REGISTRATION_ENABLED` | `false` | Nobody can sign up on the demo. |
| `ENABLE_FREE_DEMO` | `false` | Like production. |
| `DATABASE_APP_ROLE` | empty | The api logs in as `homayar_api`, a member of `homayar_app`, without `SET ROLE`. |
| `API_IMAGE`, `CONSOLE_WEB_IMAGE` | `homayar-demo/api:d8e39a994f8b`, `homayar-demo/console-web:d8e39a994f8b` | The demo tags of the HOM-358 release. |
| Third-party images | by digest, as in `app-server.md` | The server cannot pull reliably, so every Compose call uses `--pull never`. |

Memory limits are in `compose.onprem.demo.yaml`, lower than production. See "Rehearsal" for the
measured use.

## Owner Steps

Steps 1, 2 and 4 need the owner (Cloudflare and `sudo`). The gpu steps (step 3) do not need
them, and can run before steps 1 and 2.

1. **DNS record in Cloudflare.** Add an `A` record: name `demo`, content `193.93.169.205`, proxy
   status **DNS only** (grey cloud), TTL auto. Check:

   ```bash
   dig +short demo.homayar.com   # must print 193.93.169.205
   ```

2. **Certificate by DNS-01**, without the HOM-382 Cloudflare token. On `homayar-local`:

   ```bash
   sudo certbot certonly --manual --preferred-challenges dns -d demo.homayar.com
   ```

   certbot shows a TXT value. In Cloudflare, add a `TXT` record with name `_acme-challenge.demo`
   and that value. Before you press Enter in certbot, check from another terminal that the record
   is live:

   ```bash
   dig +short TXT _acme-challenge.demo.homayar.com   # must print the value certbot showed
   ```

   Then press Enter. When certbot says the certificate is saved, remove the TXT record in
   Cloudflare.

   - A manual certificate **does not renew by itself**. It is valid for 90 days, which is enough
     for the demo. To renew, run the same command again.
   - Once HOM-382 gives a Cloudflare API token, use this instead (it can renew by itself):
     `sudo certbot certonly --dns-cloudflare --dns-cloudflare-credentials <file> -d demo.homayar.com`.

3. **gpu steps** (section "gpu Steps" below): source copy, `--check`, `--apply`. They can run
   before steps 1 and 2.

4. **Host nginx site.** Tell homlead before the reload. On `homayar-local`:

   ```bash
   sudo bash /home/gpu/homayar-demo/src/infra/scripts/demo-site-nginx.sh --check
   sudo bash /home/gpu/homayar-demo/src/infra/scripts/demo-site-nginx.sh --apply
   ```

   `--check` changes nothing. It shows the site file and the prerequisites (certificate, snippet,
   websocket map, `127.0.0.1:8090` listens, no other site uses `demo.homayar.com`). `--apply`
   writes the site file and the symlink, runs `nginx -t`, and only then runs
   `systemctl reload nginx` (never `restart`). If `nginx -t` fails, it puts the exact previous
   state back and does not reload.

5. **Check from outside** (not from the server):

   ```bash
   curl -I https://demo.homayar.com:8080/healthz   # HTTP/2 200
   ```

   Open `https://demo.homayar.com:8080/app/`. Sign in with an e-mail and the password from
   `/home/gpu/homayar-demo/secrets/demo-login` (read it on the server; do not paste it into a
   chat or a ticket). Open the issue trends page ("روند مسائل") and pick the team `Call Center`
   or `Customer Care`: the cells show percent values, not "too few calls".

## gpu Steps

As user `gpu` on `homayar-local`. Never with `sudo`. Give every command `</dev/null`.

1. **Source copy**, from the admin Mac. Use the merged `main` commit that has HOM-383:

   ```bash
   SHA=<merged main commit>
   ssh homayar-local "install -d -m 700 ~/homayar-demo ~/homayar-demo/src"
   git archive --format=tar "$SHA" | ssh homayar-local "tar -x -C ~/homayar-demo/src && echo $SHA > ~/homayar-demo/src/SOURCE_SHA"
   ```

   The script refuses to run from anywhere else than `/home/gpu/homayar-demo`, so it can never
   run from the production tree `/home/gpu/homayar`.

2. **Check** (read-only, changes nothing):

   ```bash
   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --check </dev/null
   ```

   It prints PASS or FAIL for each item and the plan. Exit 0 means ready (or already applied and
   healthy), 1 means not ready, 2 means a usage error.

   | Item | Checks |
   | --- | --- |
   | C1 | The user is `gpu`, not root. |
   | C2 | The script lives under the demo home. |
   | C3 | `docker` and `docker compose` answer. |
   | C4 | The two release images (or the demo tags) are present. |
   | C5 | The five pinned third-party images are present. |
   | C6 | Port 8090 is free, or held by this demo's proxy. |
   | C7 | No route (except this demo's own bridges) overlaps `172.29.30.0/24` to `172.29.34.0/24`. |
   | C8 | At least 4 GiB of memory is available (`free -b`). On an applied, healthy demo this is INFO only: the demo itself uses that memory. |
   | C9 | At least 5 GiB is free on the demo home's file system. INFO only on an applied demo. |
   | C10 | Whether the demo already runs and is healthy (INFO before the first `--apply`). |

3. **Apply:**

   ```bash
   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --apply </dev/null
   ```

   It runs the `--check` items first and stops (exit 2, nothing changed) when one fails. Then, in
   this order: the demo home and config (only what is missing), the two demo image tags,
   postgres, redis and minio (`--wait`), `minio-bootstrap`, `migrate`, the `homayar_api` role
   (once), the tombstone volume (once), the demo seed (once), the demo login password (once),
   `nginx -t` of the proxy, `up -d --wait`, and the health checks (`/healthz` and `/app/` on
   `127.0.0.1:8090`). It ends with a summary. In the rehearsal it took 84 seconds.

   - The seed refuses the production environment. So it runs once as a one-shot api container
     with `ENVIRONMENT=development` set only for that container, on the same database, MinIO and
     secrets. Its output prints the public seed password, so the script keeps only the
     workspace and meeting count lines.
   - The seed password is public in the repository. So, before the proxy first starts, `--apply`
     sets one new random password (32 characters) on the four seed accounts and writes
     `secrets/demo-login`. The script prints only the file path.
   - A second `--apply` on a healthy demo changes no data and keeps the password. It only
     reprints the summary.
   - If a step fails, fix the cause and run `--apply` again. Finished steps are not repeated.

4. **Daily check:**

   ```bash
   ~/homayar-demo/dc ps --all --format '{{.Service}} {{.State}} {{.Health}}'
   curl -fsS http://127.0.0.1:8090/healthz
   ```

   Every service is `running healthy`, except `minio-bootstrap` (`exited`, exit code 0) and the
   scheduler (`running`, no health check).

## Reset (New Data, New Password)

```bash
bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --undo --yes </dev/null
bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --apply </dev/null
```

The nginx site and the certificate stay. The new password is in `secrets/demo-login`.

When to reset: the demo data ages. The seed writes four closed weeks before the day it runs. The
trends page shows the last four closed weeks. So a week after the seed, the oldest seeded week
leaves the trends window, and the newest week on the page has no data. Reset before a
presentation when the seed is older than a few days.

## Remove the Demo Site

In this order:

1. Host nginx site (owner, tell homlead before the reload):

   ```bash
   sudo bash /home/gpu/homayar-demo/src/infra/scripts/demo-site-nginx.sh --undo
   ```

   It removes the symlink and the site file (it keeps a backup copy), runs `nginx -t`, then
   reloads. If `nginx -t` fails, it restores both and does not reload.

2. The demo stack (gpu):

   ```bash
   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --undo </dev/null        # plan only, changes nothing
   bash /home/gpu/homayar-demo/src/infra/scripts/demo-site.sh --undo --yes </dev/null  # removes it
   ```

   With `--yes`: `dc down --volumes --remove-orphans` for project `homayar-demo` only (all demo
   data is destroyed), removal of the two `homayar-demo/*` tags, a check that no container,
   volume or network with label `com.docker.compose.project=homayar-demo` remains, then removal
   of the demo home except `src/` and the undo log `demo-site-undo-<UTC stamp>.log`. The summary
   prints the last `rm -rf` and `rmdir` lines for you to run.

3. The DNS records in Cloudflare: `demo` (and `_acme-challenge.demo` if it is still there).

4. The certificate (owner): `sudo certbot delete --cert-name demo.homayar.com`.

## Rehearsal

Run on 2026-11-10 on the admin Mac (Docker Desktop 29.8, Compose 5.7, 8 GiB VM), mission
`20261110-hom383`, at commit `5e69f36`. The script ran in a Linux container
(`docker:29-cli` with bash, util-linux, iproute2 and curl; host network; the Docker socket
mounted) as user `gpu`, with `DEMO_TEST_HOME` set to a scratch demo home. After the run, one more
fix (`--undo` without `--yes` no longer writes a log or the lock) was checked the same way on
the emptied demo home: exit 2, nothing written. The images were the
real release images `homayar/api:d8e39a994f8b` and `homayar/console-web:d8e39a994f8b`, copied
from the server with `docker save` (linux/amd64, run under emulation). The full transcript is
in the mission handoff (`handoffs/turn-1.md`).

| Step | Result |
| --- | --- |
| `--check` (empty home) | exit 0, C1 to C9 PASS, C10 INFO |
| `--apply` | exit 0 in 84 s. Migrate ran every revision up to `a651110b03c3`. Seed: `Meetings: 270`. `demo-login-set: 4 accounts`. Every service healthy. `/healthz` 200, `/app/` 200. |
| `--check` (applied) | exit 0, C10 PASS ("already applied") |
| second `--apply` | exit 0 in 2 s, "already applied and healthy". The same container ids, the same row counts, the same hash of the user rows, and the same hashes of every secret file as before. |
| login, password from `secrets/demo-login` | `200` for all four accounts. Cookies `homayar_access`, `homayar_refresh` (HttpOnly, Secure) and `homayar_csrf` (Secure). |
| login, public seed password `Homayar-Demo-1405` | `401 invalid_credentials` for all four accounts |
| `POST /api/v1/auth/register` | `403 registration_closed` |
| `GET /api/v1/trends/issues`, four closed weeks, team `Call Center` | `200`. 138 calls. Window rows: 25 `computed`, 1 `not_computable` (the QA family, which the seed does not score). Each week: 21 `computed`. For example `unresolved_outcome` 73/93 = 78.5%, `service_complaint` 26/120 = 21.7%. |
| same, team `Customer Care` | `200`. 120 calls. Window rows: 25 `computed`, 1 `not_computable`. Each week: 21 `computed`. |
| api log | `DATABASE_RLS_POSTURE role=homayar_api rls_enabled=True rls_forced=True` |
| runtime database role | `homayar_api`: `rolsuper` false, `rolbypassrls` false |
| `--undo --yes` | exit 0. No container, volume or network with the project label remains. Only `src/` and the undo log stay in the demo home. |

Two problems the rehearsal found are fixed in the scripts: the migration DSN needs
`postgresql+psycopg://` (the image has psycopg 3 only), and a healthy demo was not seen as
"applied" (Compose prints exit code 0 for a running service; the memory gate also counted the
demo's own use).

Memory, measured in the rehearsal (`memory.peak` of each container's cgroup, which includes file
cache, and `docker stats` every 2 seconds, which does not):

| Service | Limit | `memory.peak` | `docker stats` max |
| --- | --- | --- | --- |
| api | 768m | 485 MiB | 439 MiB |
| worker | 768m | 474 MiB | 453 MiB |
| scheduler | 256m | 93 MiB | 88 MiB |
| proxy | 128m | 17 MiB | 13 MiB |
| console-web | 128m | 29 MiB | 24 MiB |
| postgres | 512m | 172 MiB | 77 MiB |
| redis | 128m | 20 MiB | 9 MiB |
| minio | 512m | 512 MiB | 478 MiB |
| seed run (one-shot api) | 1536m | | 169 MiB |
| login password run (one-shot api) | 768m | | 84 MiB |

No container was OOM-killed (`memory.events`: `oom_kill 0` for every service). MinIO reached its
limit during the seed (it reclaimed file cache 379 times) and settled at 360 MiB anonymous
memory. If MinIO is ever OOM-killed on the server, raise `MINIO_MEM_LIMIT` in
`config/settings.env` (production uses 768m).
