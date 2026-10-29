# App Server Runbook (app.homayar.com)

This runbook covers the production server that runs the product console and the API at
`https://app.homayar.com` (HOM-328). No secret value is in this file.

The relay and the tunnel in front of this server have their own runbook:
[`relay-tunnel.md`](relay-tunnel.md). It arrives with PR #170, so this page must merge after
PR #170. General on-prem rules (secret names, image policy, database roles, upgrade rules) are in
[`deployment.md`](deployment.md).

## How a Request Arrives

```text
visitor -> relay 193.93.169.205:443 (frps, raw TCP)
        -> tunnel -> frpc (Docker, host network) on the local server
        -> host nginx 127.0.0.1:10443 (PROXY protocol; TLS ends here; site app.homayar.com)
        -> 127.0.0.1:8080 (Docker published port)
        -> "proxy" container (bundled nginx, HTTP) -> console-web (/app/) or api (/api/v1/)
```

- DNS: `app.homayar.com` points to the relay `193.93.169.205`, DNS only (grey cloud).
- The relay and frpc forward every name. They need no change for this site.
- TLS ends in the host nginx. The stack itself speaks only HTTP on `127.0.0.1:8080`.
- Port 80 through the relay answers `301` to HTTPS, except the ACME path for certificate renewal.

## Hosts

| Host | SSH alias | Role |
| --- | --- | --- |
| Local server | `homayar-local` (`192.168.100.6`, user `gpu`) | Runs the Homayar stack in Docker (user `gpu`, no `sudo`), and the host nginx, which needs the owner's `sudo`. It is shared with the live Padyar services and a GitHub Actions runner. |
| Relay VPS | `homayar-relay` (`193.93.169.205`) | Forwards raw TCP. See `relay-tunnel.md`. |

Rules on the local server:

- Never stop, restart or reconfigure nginx, fail2ban, the host PostgreSQL (`127.0.0.1:5432`), a
  Padyar service, the GitHub runner, Docker itself or the `relay-tunnel-frpc` container.
- Every `sudo` step goes to the owner as one reviewed script. An nginx change is a reload, never a
  restart, and only after `nginx -t`.
- Homayar uses only the Docker project `homayar-onprem` and the folder `/home/gpu/homayar/`.
  Never run `docker system prune`, `docker image prune`, `docker volume prune` or
  `docker network prune`: other projects live on this host.

## Folder Layout

Everything is under `/home/gpu/homayar/`, owner `gpu`:

| Path | Mode | What |
| --- | --- | --- |
| `/home/gpu/homayar/` | 700 | root of the install |
| `releases/<sha12>/` | 755 (files from `git archive`) | the source tree of one commit, plus `SOURCE_SHA` (the full commit SHA) |
| `current` | symlink | `releases/<sha12>` of the running release |
| `config/` | 700 | |
| `config/settings.env` | 600 | settings without secrets |
| `secrets/` | 700 | |
| `secrets/<name>` | 600 | one random value per file (list below) |
| `secrets/secrets.env` | 600 | the secret variables for Compose, built from the files above |
| `hc` | 700 | the one Compose command line (below) |
| `ops/` | 700 | operator scripts of the first start (`stage-*.sh`, no secret), files 600 |
| `build-mem.log` | 600 | free memory during the last image build |

Secret files, made with `openssl rand -hex <n>` on the server, never printed:
`postgres_password`, `homayar_api_db_password`, `redis_password`, `jwt_secret`, `token_pepper`
(64 hex characters each), `minio_root_user`, `storage_access_key` (20), `minio_root_password`,
`storage_secret_key` (40).

`secrets.env` holds `POSTGRES_PASSWORD`, `MIGRATION_DATABASE_URL`, `DATABASE_URL`,
`REDIS_PASSWORD`, `REDIS_URL`, `JWT_SECRET`, `TOKEN_PEPPER`, `MINIO_ROOT_USER`,
`MINIO_ROOT_PASSWORD`, `STORAGE_ACCESS_KEY` and `STORAGE_SECRET_KEY`.

Docker volumes (project `homayar-onprem`): `postgres_data`, `redis_data`, `minio_data`,
`app_data`, `tombstone_journal` (owner `10001:10001`, mode 700).

Never print a secret:

- No `cat`, `head`, `less` or plain `grep` on a secret file or an env file. Use `grep -c`, `-l`
  or `-q` only.
- Never `docker compose config` without `--quiet`. It prints every secret.
- Never a bare `docker inspect`. Use `--format` with the field you need.
- `docker compose logs minio-bootstrap` prints the MinIO access key. Pass it through
  `sed -E 's/[0-9a-f]{20,}/<redacted>/g'`.

## Settings

`config/settings.env`:

```dotenv
APP_ENVIRONMENT=production
PUBLIC_ORIGIN=https://app.homayar.com
CORS_ORIGINS=https://app.homayar.com
# 127.0.0.1: the api container health check calls http://127.0.0.1:8000/healthz.
TRUSTED_HOSTS=app.homayar.com,127.0.0.1
SECURE_COOKIES=true
BIND_ADDRESS=127.0.0.1
HTTP_PORT=8080
POSTGRES_DB=homayar
POSTGRES_USER=homayar
# Empty: DATABASE_URL logs in as homayar_api, a member of homayar_app (no SET ROLE).
DATABASE_APP_ROLE=
POSTGRES_IMAGE=postgres:17-alpine@sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b
REDIS_IMAGE=redis:7.4-alpine@sha256:8da19c7399dca900c6822a1cf30f9d2285585e2c4b457ff4095b0f4500054d3f
NGINX_IMAGE=nginx:1.28-alpine@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236
LOG_LEVEL=INFO
# Off until the owner decides (homlead, 2026-10-29). New sign-ups get no demo allowance.
ENABLE_FREE_DEMO=false
```

Why `127.0.0.1` is in `TRUSTED_HOSTS`: the api health check calls the API by that address. Without
it, the api never becomes healthy and the proxy never starts. Through the relay a request with
`Host: 127.0.0.1` never reaches the stack: the host nginx sends it to the tunnel default server,
which closes the connection.

The compose file fixes these values: `DEPLOYMENT_MODE=onprem`, `ALLOW_MOCK_PROVIDERS=false`,
`ENABLE_DEMO_ACCOUNT=false`, `AUTO_CREATE_SCHEMA=false`. The demo seed is never run.

## The Compose Command Line

Always use `~/homayar/hc`. It is the only command line for this stack:

```bash
#!/bin/bash
# The one Compose command line for app.homayar.com (docs/operations/app-server.md).
set -euo pipefail
H=/home/gpu/homayar
R="$(readlink -f "$H/current")"   # the real release path, so a new release changes the mount paths
S12="$(cut -c1-12 "$R/SOURCE_SHA")"
export API_IMAGE="homayar/api:$S12" CONSOLE_WEB_IMAGE="homayar/console-web:$S12"
cd "$R"
exec docker compose --env-file "$H/config/settings.env" --env-file "$H/secrets/secrets.env" \
  --file infra/compose/compose.onprem.yaml --file infra/compose/compose.onprem.app-server.yaml \
  --profile onprem "$@"
```

Examples: `~/homayar/hc ps`, `~/homayar/hc logs --since 10m api`, `~/homayar/hc config --quiet`.

The overlay `infra/compose/compose.onprem.app-server.yaml` adds the memory and CPU limits, the
fixed subnets and the trust of the host TLS front. A plain on-prem install that does not use the
overlay is unchanged.

## Ports and Networks

| Listener | Who | Note |
| --- | --- | --- |
| `127.0.0.1:8080` | proxy (Docker published port) | the only published port of the stack |
| `127.0.0.1:10080`, `127.0.0.1:10443` | host nginx tunnel listens | shared with the Padyar sites (HOM-332) |

PostgreSQL, Redis and MinIO of the stack have no published port. `192.168.100.6:8080` is refused.
`127.0.0.1:5432` stays the host PostgreSQL 16 of Padyar.

| Network | Subnet | Internal | Members |
| --- | --- | --- | --- |
| `edge` | `172.29.10.0/24`, gateway `172.29.10.1` | no | proxy |
| `ingress` | `172.29.11.0/24`, dynamic range `172.29.11.128/25` | yes | proxy (fixed `172.29.11.10`), console-web, api |
| `backend` | `172.29.12.0/24` | yes | api, worker, scheduler, postgres, redis, minio |
| `egress` | `172.29.13.0/24` | yes | api, worker (egress proxy later) |
| `outbound` | `172.29.14.0/24` | no | egress proxy only (profile `egress`, not running) |

The subnets are fixed so they never overlap the LAN `192.168.100.0/24`, the admin VPN address
`10.81.0.2`, or the other Docker networks (`172.17/16`, `172.18/16`). Never use `10.81.x` for
Docker on this host: SSH from the admin Mac comes from there.

## Client IP and Scheme

| Hop | Takes the client IP from | Trusts only |
| --- | --- | --- |
| host nginx | the PROXY protocol header from frpc | `127.0.0.1` (`snippets/relay-tunnel-realip.conf`) |
| host nginx to the stack | sets `X-Real-IP` and `X-Forwarded-For` to the visitor IP (overwrite, never append) and `X-Forwarded-Proto https` | |
| proxy (bundled nginx) | `X-Real-IP` | the `edge` gateway `172.29.10.1` (`infra/nginx/trusted-proxy/host-tls-front.conf`) |
| api (uvicorn) | the right-most untrusted `X-Forwarded-For` entry | the proxy at `172.29.11.10` (`FORWARDED_ALLOW_IPS`) |

So nginx rate limits, API rate limits, sessions and audit see the visitor's IP. A client cannot
set it: the host nginx overwrites both headers.

Known limit: any process on the server itself can call `127.0.0.1:8080` with its own `X-Real-IP`
and choose the IP the app sees. The per-account login limit (10 tries per 15 minutes) does not
depend on the IP.

The stack sees `http` as the scheme. Cookies and HSTS do not depend on it. Redirects from the
stack (`http://app.homayar.com:8080/...` from the bundled nginx and `http://app.homayar.com/...`
from the API) are turned into `https://app.homayar.com/...` by two `proxy_redirect` lines in the
host site.

## Memory and CPU Budget

Limits are in the overlay. Each one comes from an environment variable with this default (for
example `API_MEM_LIMIT`). `memswap_limit` equals `mem_limit`, so no container swaps. tmpfs mounts
count against the limit.

| Service | Memory | CPUs | tmpfs | Idle use (measured 2026-10-29, `docker stats`) |
| --- | --- | --- | --- | --- |
| proxy | 256 MiB | 2 | 128 MiB | 43.6 MiB (17%) |
| console-web | 128 MiB | 1 | 64 MiB | 28.6 MiB (22%) |
| api | 1280 MiB | 4 | 608 MiB | 310.8 MiB (24%) |
| worker | 2048 MiB | 4 | 1088 MiB | 269.7 MiB (13%); about 336 MiB and one CPU core for a second during its health check |
| scheduler | 256 MiB | 0.5 | none | 66.5 MiB (26%) |
| postgres | 1024 MiB | 2 | `shm_size` 256 MiB | 45.8 MiB (4%) |
| redis | 256 MiB | 1 | 32 MiB | 6.8 MiB (3%) |
| minio | 768 MiB | 2 | none | 246.1 MiB (32%) |
| **Total** | **6016 MiB (5.88 GiB)** | 16.5 of 36 | | about 1018 MiB |

One-shot services have limits too, outside the always-on total: `minio-bootstrap` 256 MiB (`mc`
needs about 150 MiB; 128 MiB was killed by the kernel on the first start) and `migrate` 768 MiB.

Host `MemAvailable` was 15,181,812 kB before the first start and 14,041,948 kB with the stack idle
(about 1.1 GiB less). During the image build it went down to 14,488,740 kB.

Later, the egress proxy (profile `egress`) adds 128 MiB, so the total becomes 6144 MiB (6 GiB).
Local Whisper would need much more worker memory. Change the budget with homlead first: the
Padyar services need the rest of the RAM.

## Health Checks

On the server:

```bash
curl -sS -H 'Host: app.homayar.com' http://127.0.0.1:8080/healthz   # the proxy itself
curl -sS -H 'Host: app.homayar.com' http://127.0.0.1:8080/readyz    # API: database and redis "ready"
~/homayar/hc ps --all --format '{{.Service}} {{.State}} {{.Health}}'
```

From anywhere:

```bash
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://app.homayar.com/healthz
curl -sS https://app.homayar.com/readyz
```

Certificate expiry, without root (works from any machine):

```bash
openssl s_client -connect app.homayar.com:443 -servername app.homayar.com </dev/null 2>/dev/null | openssl x509 -noout -enddate
```

Check it after each renewal window. `certbot.timer` shares one exit code with the Padyar renewals,
so a failed app renewal can hide behind theirs.

## First Start

This is how the stack was started on 2026-10-29 (`handoffs/turn-2.md` of mission
`20261029-hom328` has every command and its output). As `gpu`, in this order:

1. Source: from the admin Mac,
   `git archive <sha> | ssh homayar-local "tar -x -C ~/homayar/releases/<sha12> && echo <sha> > ~/homayar/releases/<sha12>/SOURCE_SHA && ln -sfn releases/<sha12> ~/homayar/current"`.
   The server has no GitHub credentials.
2. Secrets: `openssl rand -hex ...` into `secrets/<name>` (`umask 077`), then `secrets.env` with
   `printf` and `$(< file)`. Never `cat`.
3. `config/settings.env` and `hc` as above. Check: `~/homayar/hc config --quiet`.
4. Images, one build at a time, capped so Padyar keeps its CPU and RAM:

   ```bash
   cd "$(readlink -f ~/homayar/current)"; S12=$(cut -c1-12 SOURCE_SHA)
   DOCKER_BUILDKIT=0 docker build --memory 3g --memory-swap 3g --cpu-period 100000 --cpu-quota 400000 \
     -f infra/docker/api.Dockerfile -t homayar/api:$S12 .
   DOCKER_BUILDKIT=0 docker build --memory 3g --memory-swap 3g --cpu-period 100000 --cpu-quota 400000 \
     -f infra/docker/console-web.Dockerfile --build-arg VITE_API_URL=/api/v1 --build-arg VITE_BASE_PATH=/app/ \
     -t homayar/console-web:$S12 .
   ~/homayar/hc pull postgres redis minio minio-bootstrap proxy
   ```

   `docker buildx` is not installed, so the legacy builder is used. It took about 15 minutes for
   the api image and 3 minutes for the console. The legacy builder does not read the
   per-Dockerfile ignore files (`infra/docker/*.Dockerfile.dockerignore`, a BuildKit feature), so
   the api image built here also holds `Homayar/backend/tests` (unused at runtime, no secrets).
   If a Docker Hub download fails, build or pull on the admin Mac with `--platform linux/amd64`,
   then `docker save <image> | ssh homayar-local docker load`.
5. Stores: `~/homayar/hc up -d --wait --no-build --pull never postgres redis minio`, then
   `~/homayar/hc run --rm --no-deps minio-bootstrap 2>&1 | sed -E 's/[0-9a-f]{20,}/<redacted>/g'`.
6. Schema: `~/homayar/hc --profile migrate run --rm migrate`.
7. The runtime database role (once), with the password from its file through stdin, and the
   statement kept out of the server log:

   ```bash
   cd ~/homayar
   { printf "SET log_min_error_statement = 'panic';\n"
     printf "CREATE ROLE homayar_api LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION IN ROLE homayar_app PASSWORD '%s';\n" "$(< secrets/homayar_api_db_password)"
   } | ~/homayar/hc exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -q -U "$POSTGRES_USER" -d "$POSTGRES_DB"' 2>&1 | sed -E 's/[0-9a-f]{20,}/<redacted>/g'
   ```

8. The tombstone journal volume (once, before the first `up` of api and worker):

   ```bash
   ~/homayar/hc run --rm --no-deps --user 0 --cap-add CHOWN --entrypoint sh api \
     -c 'chmod 700 /app/tombstones && chown 10001:10001 /app/tombstones && stat -c "%u:%g %a" /app/tombstones'
   ```

9. Everything: `~/homayar/hc up -d --no-build --pull never`, then wait until
   `~/homayar/hc ps --all --format '{{.Service}} {{.State}} {{.Health}}'` shows every service
   `running healthy`. Two exceptions: `minio-bootstrap` is `exited` (check `Exited (0)` in
   `hc ps --all`), and the scheduler is `running` with no health value (it has no health check).

Run multi-step scripts on the server from a file with stdin set to `/dev/null`, for example
`bash ~/homayar/ops/<script>.sh </dev/null`. `hc run` without `-T` reads stdin, so a script fed
through `ssh ... 'bash -s' < script` loses its remaining lines to the container.

Database roles after this: `homayar` (owner, superuser, used only by `migrate` through
`MIGRATION_DATABASE_URL`), `homayar_app` (NOLOGIN, the role the row-level security policies
target), `homayar_audit_reader` (NOLOGIN), and `homayar_api` (LOGIN, member of `homayar_app`,
used by api, worker and scheduler through `DATABASE_URL`). The API refuses to start with a
superuser or BYPASSRLS runtime role.

## Update to a New Commit

What it interrupts: the console and the API for about 30 to 60 seconds while api and proxy are
recreated. Running jobs get 2 minutes to finish. The Padyar sites are not touched. The image build
uses up to 4 CPUs and 3 GiB.

1. On the admin Mac: `SHA=<commit on main>; S12=${SHA:0:12}`, then send the source as in
   "First Start", step 1, into a new `releases/$S12`. Keep the old release folder.
2. Build both images with the new tag (step 4). Check `docker images 'homayar/*'`.
   Shortcut: if no image input changed, re-tag instead of building. The inputs are `Homayar/`,
   `infra/docker/` and `infra/nginx/console.conf` (the `COPY` lines of the two Dockerfiles). If
   `git diff --name-only <old sha> <new sha> -- Homayar infra/docker infra/nginx/console.conf`
   prints nothing, run `docker tag homayar/api:<old sha12> homayar/api:<new sha12>`, and the same
   for `homayar/console-web`.
3. Read the release notes and `deployment.md`, "Upgrade and Rollback" (migration rules, backups).
4. Switch: `ln -sfn releases/$S12 ~/homayar/current`, then `~/homayar/hc config --quiet`.
5. `~/homayar/hc stop worker scheduler`, then `~/homayar/hc --profile migrate run --rm migrate`.
6. `~/homayar/hc up -d --no-build --pull never`, then the health checks.

The `readlink -f` in `hc` gives each release its own mount paths, so the proxy is recreated with
the new nginx files.

## Rollback

1. `ln -sfn releases/<old sha12> ~/homayar/current`
2. `~/homayar/hc up -d --no-build --pull never`, then the health checks.

Never downgrade the database. A migration that is not backward-compatible needs its own plan
(`deployment.md`, "Upgrade and Rollback").

## Restart and Stop

| Command | Interrupts |
| --- | --- |
| `docker restart homayar-onprem-api-1` | the API for about 10 to 45 s; the console shell still loads |
| `~/homayar/hc restart` | the whole site for about 1 minute |
| `~/homayar/hc down` | the whole site until `hc up -d --no-build --pull never`. The volumes stay. |

Every long-running service has `restart: unless-stopped`, so the stack comes back after a reboot
of the server (Docker starts at boot).

Full removal, Homayar only (destroys all data): `~/homayar/hc down --volumes`,
`docker rmi homayar/api:<sha12> homayar/console-web:<sha12>`, `rm -rf ~/homayar`.

## Host nginx Site and Certificate

Owner script (reviewed, run with `sudo` by the owner): `hom328-app-site.sh` in mission
`20261029-hom328`. It created:

- `/etc/nginx/sites-available/app.homayar.com.conf` and its symlink in `sites-enabled/`
- the certificate (`/etc/letsencrypt/live/app.homayar.com/`, `renewal/app.homayar.com.conf`)
- the backup `/root/hom328-backup/nginx-before.tgz` (keep it; later backups get a dated name)

The site:

- Port 80 server: `listen 127.0.0.1:10080 proxy_protocol;`, the relay real-IP snippet, the ACME
  path (`/var/www/certbot`), and `301` to HTTPS for everything else.
- Port 443 server: `listen 127.0.0.1:10443 ssl http2 proxy_protocol;` (the same options as the
  Padyar tunnel sites), the snippet, TLS 1.2 and 1.3, its own logs
  (`/var/log/nginx/app.homayar.com.access.log`, `.error.log`).
- One HSTS header `max-age=31536000` (1 year, no `includeSubDomains`, no `preload`). The API's own
  HSTS header is hidden.
- `client_max_body_size 16m`, and `300m` only in `location ^~ /api/v1/uploads`, which streams the
  body with 15-minute timeouts, like the stack.
- No LAN listens. LAN users use the public name, which goes through the relay.

Certificate: Let's Encrypt, HTTP-01 with the webroot `/var/www/certbot`, through the relay's
port 80. Renewal: `certbot.timer` runs twice a day and renews within 30 days of expiry, through the
same path. After a renewal, `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` reloads nginx.
If the tunnel is down, certbot keeps trying for 30 days. Test without changing anything (owner):
`sudo certbot renew --dry-run --cert-name app.homayar.com --no-directory-hooks`.

Retry after the script rolled back:

1. Read the output and fix the cause.
2. Move the backup aside:
   `sudo mv /root/hom328-backup/nginx-before.tgz /root/hom328-backup/nginx-before-$(date -u +%Y%m%dT%H%M%SZ).tgz`.
3. Run the script again. A certificate from the first run is reused when it is valid for more than
   30 days. If the script stops on the certificate, run
   `sudo certbot delete --cert-name app.homayar.com` first.

## First Owner Account

The owner creates the first organization, workspace and owner account by self-registration in the
console. No SMTP is needed, and nobody else sees the password.

1. Open `https://app.homayar.com/app/register`. Check that the browser shows a valid certificate.
2. Enter a name, the e-mail address and a new password (10 to 128 characters, at least one letter
   and one number), then the password again.
3. Sign in at `https://app.homayar.com/app/login`. The organization and the workspace carry the
   name you typed.
4. A banner says the e-mail is not verified. Uploads stay blocked until e-mail (SMTP) or phone
   sign-in exists. Sign-in works.

Do this right after the site goes live: anyone can register an address first (open item O4).

## Sign-up: Close and Reopen

Anyone on the internet can create an account (their own empty organization). No setting closes it.
The owner has not decided yet. To close it at the host nginx (owner, `sudo`, one nginx reload):

```bash
sudo bash ~/hom328-close-signup.sh          # close: POST /api/v1/auth/register gets 403
sudo bash ~/hom328-close-signup.sh reopen   # reopen: the block is removed again
```

**Never close it before the owner has registered.** The script adds exactly these 2 lines to the
HTTPS server of `app.homayar.com.conf`, or removes them for `reopen`:

```nginx
    # HOM-328: self-registration closed by the owner (owner-close-signup.sh).
    location = /api/v1/auth/register { return 403; }
```

It backs up the site file (`/root/hom328-backup/app.homayar.com.conf.before-<mode>-signup-<time>`),
runs `nginx -t`, reloads once, compares the Padyar LAN status codes before and after, and checks
that register gets 403 (or not, for `reopen`) and that login still reaches the app. Any failure
puts the old file back. To run the same mode a second time, move its old backup aside first.

## Free Demo

`ENABLE_FREE_DEMO=false`: new sign-ups get no demo allowance, and the plan catalog does not list
the demo. Switch it on (when the owner decides):

1. In `config/settings.env`, set `ENABLE_FREE_DEMO=true`.
2. `~/homayar/hc up -d --no-build --pull never api worker scheduler`
3. Check: `~/homayar/hc exec -T api python -c 'from backend.config import get_settings as g; print(g().enable_free_demo)'` prints `True`.

It interrupts the API for about 30 to 60 s, and a running job gets up to 2 minutes to finish. Only
workspaces created after the switch get the allowance.

## Providers (Not Configured Yet)

The stack runs without any provider key. `/healthz` and `/readyz` pass, and processing jobs fail
with a clear error until a provider is set. Keys go into `secrets/<name>` (mode 600) and
`secrets/secrets.env`. Settings without secrets go into `config/settings.env`.

| Need | Settings | Also needed |
| --- | --- | --- |
| Speech to text | Only `local_whisper` exists in the code. `ASR_PROVIDER=local_whisper`, `WHISPER_MODEL`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE_TYPE`. | Rebuild the api image with `--build-arg INSTALL_AI_LOCAL=true` (the image has no CUDA). Model weights: offline (`WHISPER_LOCAL_FILES_ONLY`, `WHISPER_MODEL_PATH`, which the compose file does not pass yet: add them in an overlay) or through the egress proxy. A model manifest. Higher worker memory and CPU limits (talk to homlead). |
| Language model | `INTELLIGENCE_PROVIDER=openai_compatible` and one of: `LOCAL_AI_BASE_URL` (private endpoint, no key); `GAPGPT_API_KEY` (secret) with `LLM_PROVIDER=gapgpt` and `LLM_MODEL`; `ZAI_API_KEY` (secret). Optional fallback: `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL`, `LLM_FALLBACK_API_KEY` (secret). | `GAPGPT_API_KEY`, `ZAI_API_KEY`, `LLM_PROVIDER` and `LLM_MODEL` are not passed by `compose.onprem.yaml`. Add them in a deployment overlay with `${NAME:?...}`. Egress (next row). A model manifest. |
| Egress (any cloud provider, Asanak) | `EGRESS_PROXY_URL=http://egress-proxy:3128`; `EGRESS_ALLOWLIST_PATH=/home/gpu/homayar/config/egress-allowlist.txt` (outside the release, so updates keep it) | Build `homayar/egress-proxy` (`infra/docker/egress-proxy.Dockerfile`). Add `--profile egress` to `hc`. Allowlist `host:port` lines from `hc run --rm --no-deps api python -m backend.jobs.model_preflight --egress-allowlist`, plus for example `gapgpt.app:443` and `panel.asanak.com:443`. |
| Model manifests | `MODEL_MANIFEST_PATHS`, `MODEL_MANIFEST_DIR` | Production is strict. Run `model_preflight`, then `model_preflight --register` (`deployment.md`). |
| Phone sign-in | `ENABLE_PHONE_AUTH=true`, `ASANAK_SENDER`, `ASANAK_API_KEY` (secret) | Egress and `panel.asanak.com:443` |
| E-mail (verify, reset, invite) | `SMTP_HOST`, `SMTP_PORT` (465 or 587), `SMTP_USERNAME`, `SMTP_FROM`, `SMTP_USE_TLS`, `SMTP_PASSWORD` (secret) | SMTP does not pass the egress proxy. It needs a mail relay on an internal network. Production refuses SMTP without TLS. |
| Optional | `PII_MAPPING_KEYS` (secret), `PII_MAPPING_ACTIVE_KEY_ID`, `METRICS_BEARER_TOKEN` (secret) | |

After any provider change: `~/homayar/hc up -d --no-build --pull never`, then
`~/homayar/hc run --rm --no-deps api python -m backend.jobs.model_preflight`.

## Open Items

Out of scope for HOM-328, each needs its own ticket:

- Provider keys and end-to-end upload processing (see "Providers").
- Invite and reset e-mails (SMTP and a mail relay).
- Scheduled backups and the restore drill (`backup-restore.md`). `backup-tool` will need a
  superuser or BYPASSRLS DSN in `BACKUP_DATABASE_URL_FILE`.
- Monitoring alerts (`/readyz`, node-exporter).
- A CI deploy workflow.
- Load and upload tests at production size.
- Any change on the relay or to frpc.

Found during HOM-328 (plan review):

- **O1.** Recording download and playback redirect the browser to a presigned URL at
  `http://minio:9000`, which a browser cannot open. Must be fixed before uploads go live
  (follow-up ticket, together with O7).
- **O2.** The owner account is not a platform operator (`system_role` stays `user`). Operator
  pages need a database change or new code.
- **O3.** The proxy and console-web run 36 nginx workers each (`worker_processes auto` on 36
  cores). Their memory is measured above.
- **O4.** Anyone can register an e-mail address first, including the owner's. The owner should
  register right after the site goes live.
- **O5.** A retry of the owner script after a rollback needs the backup moved aside (see "Host
  nginx Site and Certificate").
- **O6.** A process on the server itself can choose the client IP the app sees (see "Client IP
  and Scheme").
- **O7.** `minio-bootstrap` puts the MinIO root password and the app secret key on the `mc`
  command line for 1 to 2 seconds on every `up`, readable by any local user. The ops scripts
  `backup-minio.sh` and `restore-minio.sh` do the same. Must be fixed before uploads go live.
- A foreign `Host` header on `127.0.0.1:8080` still gets the static console HTML (the API part
  answers 400). Through the relay such a request is closed by the host nginx.
- The tombstone journal is on a local Docker volume, not replicated (backups ticket).
- The bundled nginx warns that its 2048 `worker_connections` exceed the container's open-file
  limit of 1024 (the Docker 29 default soft limit). Fine at today's traffic. A fix is
  `worker_rlimit_nofile` in `infra/nginx/nginx.conf` or `ulimits: nofile` in the overlay.
- Images built on this server with the legacy builder ignore `infra/docker/*.Dockerfile.dockerignore`.
  The api image therefore also holds `Homayar/backend/tests` (201 files, 5.1 MB, no `.env` file).
  A fix is a pinned, checksum-verified `buildx` plugin in `~/.docker/cli-plugins/` (needs
  approval), or building with BuildKit elsewhere and loading the image with `docker load`.
- The worker health check starts a full `celery inspect ping` process every 30 seconds (about one
  CPU core for a second, plus 60 MiB).
- `model_preflight` exits 2 with `intelligence_provider_unconfigured` until a language model
  provider is set. That is expected.
