# App Server Runbook (app.homayar.com)

This runbook covers the production server that runs the product console and the API at
`https://app.homayar.com` (HOM-328). No secret value is in this file.

The relay and the tunnel in front of this server have their own runbook:
[`relay-tunnel.md`](relay-tunnel.md). General on-prem rules (secret names, image policy, database
roles, upgrade rules) are in [`deployment.md`](deployment.md).

## Status and How to Read This Page

- **Running since 2026-10-29:** the Homayar stack on the local server, release `019a906aa9ef`
  (commit `019a906aa9ef2edefc8223d478eeb317e4f55b0c`). Later commits on the HOM-328 branch change
  only `docs/` and `compose.cloud.yaml`, which this server does not use.
- **Live since 2026-10-29 14:03 UTC:** the owner ran the go-live script (see "Go-Live"). It
  created the host nginx site and the certificate, and every check passed.
- **Measured** means it was run or read on this server on 2026-10-29 (mission `20261029-hom328`;
  `handoffs/turn-2.md` has every stack command and its output, and the go-live output is in
  homlead's report).
- **Not measured yet** marks what has not happened yet (for example the first renewal). The line
  says how to check it.

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
| Local server | `homayar-local` (`192.168.100.6`, user `gpu`) | Runs the Homayar stack in Docker (user `gpu`, no `sudo`), and the host nginx, which needs the owner's `sudo`. It is shared with the live Padyar services and a GitHub Actions runner. Ubuntu 24.04, 36 cores, 27 GiB RAM, Docker 29.1.3, Compose 2.40.3, runc 1.3.4. |
| Relay VPS | `homayar-relay` (`193.93.169.205`) | Forwards raw TCP. See `relay-tunnel.md`. |

Rules on the local server:

- Never stop, restart or reconfigure nginx, fail2ban, the host PostgreSQL (`127.0.0.1:5432`), a
  Padyar service, the GitHub runner, Docker itself or the `relay-tunnel-frpc` container.
- Every `sudo` step goes to the owner as one reviewed script. An nginx change is a reload, never a
  restart, and only after `nginx -t`.
- Homayar uses only the Docker project `homayar-onprem` and the folder `/home/gpu/homayar/`.
  Never run `docker system prune`, `docker image prune`, `docker volume prune` or
  `docker network prune`: other projects live on this host.

## Files

### Homayar stack (user `gpu`)

Everything is under `/home/gpu/homayar/`, owner `gpu`:

| Path | Mode | What |
| --- | --- | --- |
| `/home/gpu/homayar/` | 700 | root of the install |
| `releases/<sha12>/` | 755 (files from `git archive`) | the source tree of one commit, plus `SOURCE_SHA` (the full commit SHA) |
| `current` | symlink | `releases/<sha12>` of the running release (now `releases/019a906aa9ef`) |
| `config/` | 700 | |
| `config/settings.env` | 600 | settings without secrets (below) |
| `secrets/` | 700 | |
| `secrets/<name>` | 600 | one random value per file (list below) |
| `secrets/secrets.env` | 600 | the secret variables for Compose, built from the files above |
| `hc` | 700 | the one Compose command line (below) |
| `ops/` | 700 | the operator scripts of the first start (`stage-*.sh`, no secret), files 600 |
| `build-mem.log` | 600 | free memory during the first image build |

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

### Host nginx and certbot (go-live, root)

| Path | What |
| --- | --- |
| `/home/gpu/hom328-app-site.sh` | the reviewed owner script, sha256 `2d3b2846caddc68885291416883f789c9fb14c84a99c0cf4e80dae920866dd69` |
| `/etc/nginx/sites-available/app.homayar.com.conf` | the site (content below), `root 0644`, sha256 `e6b37f5a83d16328eaf0bd77a64fe263a4617c69f50cb67301904dd1e9e1d2b5` (measured, the block below without the HOM-340 recording location) |
| `/etc/nginx/sites-enabled/app.homayar.com.conf` | symlink to the file above |
| `/etc/letsencrypt/live/app.homayar.com/` | the certificate (root only) |
| `/etc/letsencrypt/renewal/app.homayar.com.conf` | renewal settings (measured: `authenticator = webroot`, `webroot_path = /var/www/certbot`, `key_type = ecdsa`, the same ACME account as the Padyar certificates) |
| `/root/hom328-backup/nginx-before.tgz` | backup of `/etc/nginx` from before go-live. Keep it; later backups get a dated name. |
| `/var/log/nginx/app.homayar.com.access.log`, `.error.log` | the site's logs. Now `root 0644`; after log rotation `www-data adm 0640`, which `gpu` reads through the `adm` group. |

No other nginx, fail2ban or certbot file changes.

## Settings

`config/settings.env` (measured, the live file):

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
NGINX_IMAGE=nginx:1.30-alpine-slim@sha256:dede8895648b461178c3bc604f7eed4423efe5220b2c4d839f5eb3d34ac3f8f7
LOG_LEVEL=INFO
# Off until the owner decides (homlead, 2026-10-29). New sign-ups get no demo allowance.
ENABLE_FREE_DEMO=false
# Closed by the owner decision of 2026-10-29 (HOM-336). Only invited people can join.
REGISTRATION_ENABLED=false
```

- Why `127.0.0.1` is in `TRUSTED_HOSTS`: the api health check calls the API by that address.
  Without it, the api never becomes healthy and the proxy never starts. Through the relay a
  request with `Host: 127.0.0.1` never reaches the stack: the host nginx sends it to the tunnel
  default server, which closes the connection.
- The compose file fixes these values: `DEPLOYMENT_MODE=onprem`, `ALLOW_MOCK_PROVIDERS=false`,
  `ENABLE_DEMO_ACCOUNT=false`, `AUTO_CREATE_SCHEMA=false`. The demo seed is never run.
- Free demo: `ENABLE_FREE_DEMO=false` now, so new sign-ups get no demo allowance. Turning it on
  is HOM-337.

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

- The overlay `infra/compose/compose.onprem.app-server.yaml` adds the memory and CPU limits, the
  fixed subnets and the trust of the host TLS front. A plain on-prem install that does not use the
  overlay is unchanged.
- Run multi-step scripts on the server from a file with stdin set to `/dev/null`, for example
  `bash ~/homayar/ops/<script>.sh </dev/null`, and give every `hc run` the `-T` flag. `hc run`
  without `-T` reads stdin, so a script fed through `ssh ... 'bash -s' < script` loses its
  remaining lines to the container.

## Ports and Networks

| Listener | Who | Note |
| --- | --- | --- |
| `127.0.0.1:8080` | proxy (Docker published port) | the only published port of the stack |
| `127.0.0.1:10080`, `127.0.0.1:10443` | host nginx tunnel listens | shared with the Padyar sites (HOM-332) |

Measured: PostgreSQL, Redis and MinIO of the stack have no published port. `192.168.100.6:8080`
is refused. `127.0.0.1:5432` is still the host PostgreSQL 16 of Padyar. The only new listener is
`127.0.0.1:8080`.

| Network | Subnet | Internal | Members |
| --- | --- | --- | --- |
| `edge` | `172.29.10.0/24`, gateway `172.29.10.1` | no | proxy |
| `ingress` | `172.29.11.0/24`, dynamic range `172.29.11.128/25` | yes | proxy (fixed `172.29.11.10`), console-web, api |
| `backend` | `172.29.12.0/24` | yes | api, worker, scheduler, postgres, redis, minio |
| `egress` | `172.29.13.0/24` | yes | api, worker (egress proxy later) |
| `outbound` | `172.29.14.0/24` | no | egress proxy only (profile `egress`, not running, so not created) |

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

- So nginx rate limits, API rate limits, sessions and audit see the visitor's IP. A client cannot
  set it: the host nginx overwrites both headers.
- Measured on `127.0.0.1:8080`: a request without headers is `172.29.10.1` in the proxy log and
  the api log; a request with `X-Real-IP: 198.51.100.9` is `198.51.100.9` in both.
- Measured after go-live: requests through the relay show the sender's public IP in the site's
  nginx logs, not `127.0.0.1`. To check the whole chain, send a request with a unique path and
  look for it in `/var/log/nginx/app.homayar.com.access.log` and in
  `~/homayar/hc logs --since 5m api` (the api log shows the route, not the raw path).
- Known limit: any process on the server itself can call `127.0.0.1:8080` with its own
  `X-Real-IP` and choose the IP the app sees. The per-account login limit (10 tries per
  15 minutes) does not depend on the IP.

The stack sees `http` as the scheme. Cookies and HSTS do not depend on it. Measured: the stack's
redirects are `http://app.homayar.com:8080/app/` (for `/app` and `/`) and
`http://app.homayar.com:8080/api/v1/` (for `/api/v1`). The API's trailing-slash redirects are
`http://app.homayar.com/...` (read from the code, not measured). Two `proxy_redirect` lines in
the host site turn both into `https://app.homayar.com/...`.

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

- One-shot services have limits too, outside the always-on total: `minio-bootstrap` 256 MiB and
  `migrate` 768 MiB. `mc` needs about 150 MiB. At 128 MiB the kernel killed it on the first start.
- Measured: host `MemAvailable` was 15,181,812 kB before the first start and 14,041,948 kB with
  the stack idle (about 1.1 GiB less). During the image build it went down to 14,488,740 kB.
- Later, the egress proxy (profile `egress`) adds 128 MiB, so the total becomes 6144 MiB (6 GiB).
  Local Whisper would need much more worker memory. Change the budget with homlead first: the
  Padyar services need the rest of the RAM.

## Health Checks

On the server (measured: 200, and `/readyz` says database and redis `ready`):

```bash
curl -sS -H 'Host: app.homayar.com' http://127.0.0.1:8080/healthz   # the proxy itself
curl -sS -H 'Host: app.homayar.com' http://127.0.0.1:8080/readyz    # API: database and redis "ready"
~/homayar/hc ps --all --format '{{.Service}} {{.State}} {{.Health}}'
```

Every service shows `running healthy`, with two exceptions: `minio-bootstrap` is `exited` with
exit code 0 (`hc ps --all` shows `Exited (0)`), and the scheduler is `running` with no health
value (it has no health check).

From anywhere (measured 2026-10-29 14:05 UTC):

```bash
curl -sS -o /dev/null -w '%{http_code} verify=%{ssl_verify_result}\n' https://app.homayar.com/healthz
curl -sS https://app.homayar.com/readyz
curl -sS -o /dev/null -w '%{http_code} %{redirect_url}\n' http://app.homayar.com/
```

Result: `200 verify=0`, the `ready` JSON, and `301 https://app.homayar.com/`. Also measured:
`https://app.homayar.com/app/` is `200 verify=0`, `/app` is `308` to `https://app.homayar.com/app/`,
the ACME path answers `404`, and HTTPS answers carry one header
`Strict-Transport-Security: max-age=31536000`.

Certificate expiry, without root (works from any machine):

```bash
openssl s_client -connect app.homayar.com:443 -servername app.homayar.com </dev/null 2>/dev/null | openssl x509 -noout -subject -issuer -enddate
```

- Measured 2026-10-29: subject and SAN `app.homayar.com`, issuer Let's Encrypt `YE2`, valid from
  2026-10-29 13:04:57 UTC until **2027-01-27 13:04:56 UTC**. The first automatic renewal is due
  from about 2026-12-28 (30 days before expiry). Not measured yet: check the new date after that.
- Check it after each renewal window. `certbot.timer` shares one exit code with the Padyar
  renewals, which fail today ([HOM-335](https://linear.app/homayar/issue/HOM-335)), so a failed
  app renewal can hide behind theirs.

## First Start

How the stack was started on 2026-10-29 (measured). As `gpu`, in this order:

1. Source, from the admin Mac (the server has no GitHub credentials):

   ```bash
   SHA=<commit>; S12=${SHA:0:12}
   ssh homayar-local "install -d -m 700 ~/homayar ~/homayar/secrets ~/homayar/config && install -d -m 755 ~/homayar/releases ~/homayar/releases/$S12"
   git archive --format=tar "$SHA" | ssh homayar-local "tar -x -C ~/homayar/releases/$S12 && echo $SHA > ~/homayar/releases/$S12/SOURCE_SHA && ln -sfn releases/$S12 ~/homayar/current"
   ```

2. Secrets: `openssl rand -hex 32` (or `10`, `20`) into `secrets/<name>` with `umask 077`, then
   `secrets.env` with the `printf` builtin and `$(< "secrets/<name>")`. Never `cat`, never echo.
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

   - `docker buildx` is not installed, so the legacy builder is used. Measured: about 15 minutes
     for the api image (865 s) and 3 minutes for the console (194 s).
   - The legacy builder does not read the per-Dockerfile ignore files
     (`infra/docker/*.Dockerfile.dockerignore`, a BuildKit feature). So the api image built here
     also holds `Homayar/backend/tests` (open items).
   - If a Docker Hub download fails, build or pull on the admin Mac with `--platform linux/amd64`,
     then `docker save <image> | ssh homayar-local docker load`. It was not needed.

5. Stores and the MinIO bucket and user:

   ```bash
   ~/homayar/hc up -d --wait --no-build --pull never postgres redis minio
   ~/homayar/hc run --rm -T --no-deps minio-bootstrap 2>&1 | sed -E 's/[0-9a-f]{20,}/<redacted>/g'
   ```

6. Schema (the filter hides any long hex value in the output):
   `~/homayar/hc --profile migrate run --rm -T migrate 2>&1 | sed -E 's/[0-9a-f]{20,}/<redacted>/g'`.
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
   ~/homayar/hc run --rm -T --no-deps --user 0 --cap-add CHOWN --entrypoint sh api \
     -c 'chmod 700 /app/tombstones && chown 10001:10001 /app/tombstones && stat -c "%u:%g %a" /app/tombstones'
   ```

9. Check the bundled nginx config, then start everything:

   ```bash
   ~/homayar/hc run --rm -T --no-deps --entrypoint nginx proxy -t
   ~/homayar/hc up -d --no-build --pull never
   ```

   Then wait for the health checks (section "Health Checks").

Database roles after this:

- `homayar`: the owner, a superuser, used only by `migrate` (through `MIGRATION_DATABASE_URL`).
- `homayar_app`: NOLOGIN. The row-level security policies target this role.
- `homayar_audit_reader`: NOLOGIN.
- `homayar_api`: LOGIN, a member of `homayar_app`. The api, the worker and the scheduler use it
  (through `DATABASE_URL`). Measured: not a superuser, no BYPASSRLS. The API refuses to start
  with a superuser or BYPASSRLS runtime role, and its log shows
  `DATABASE_RLS_POSTURE role=homayar_api rls_enabled=True rls_forced=True`.

## Update to a New Commit

What it interrupts: the console and the API for about 30 to 60 seconds while api and proxy are
recreated. Running jobs get 2 minutes to finish. The Padyar sites are not touched. An image build
uses up to 4 CPUs and 3 GiB.

1. On the admin Mac, send the new source as in "First Start", step 1, but only create the new
   `releases/$S12` folder and do not switch `current` yet. Keep the old release folder.
2. Images: build both with the new tag (step 4, on the server), or re-tag if no image input
   changed. The inputs are `Homayar/`, `infra/docker/` and `infra/nginx/console.conf` (the `COPY`
   lines of the two Dockerfiles). On the admin Mac, in the repository checkout, run
   `git diff --name-only <old sha> <new sha> -- Homayar infra/docker infra/nginx/console.conf`.
   If it prints nothing, run on the server, as `gpu`:
   `docker tag homayar/api:<old sha12> homayar/api:<new sha12>`, and the same for
   `homayar/console-web`. Check `docker images 'homayar/*'` on the server.
3. Read the release notes and `deployment.md`, "Upgrade and Rollback" (migration rules, backups).
4. Switch: `ln -sfn releases/$S12 ~/homayar/current`, then `~/homayar/hc config --quiet`.
5. If the commit has a migration: `~/homayar/hc stop worker scheduler`, then
   `~/homayar/hc --profile migrate run --rm -T migrate`.
6. `~/homayar/hc up -d --no-build --pull never`, then the health checks.

- The `readlink -f` in `hc` gives each release its own mount paths, so the proxy is recreated with
  the new nginx files.
- Measured: the two config-only updates on 2026-10-29 (`07e4fde`, `019a906`) used steps 1, 2
  (re-tag), 4 and 6. Step 5 has not run on this server yet.

## Rollback

1. `ln -sfn releases/<old sha12> ~/homayar/current`
2. `~/homayar/hc up -d --no-build --pull never`, then the health checks.

Never downgrade the database. A migration that is not backward-compatible needs its own plan
(`deployment.md`, "Upgrade and Rollback"). Not run on this server yet.

## Restart and Stop

| Command | Interrupts |
| --- | --- |
| `docker restart homayar-onprem-api-1` | the API for about 17 s (measured); the console shell still loads |
| `~/homayar/hc restart` | the whole site for about 1 minute (estimate, not run) |
| `~/homayar/hc down` | the whole site until `~/homayar/hc up -d --no-build --pull never`. The volumes stay. (Not run.) |

- Every long-running service has `restart: unless-stopped`, so the stack comes back after a
  reboot of the server (Docker starts at boot). A server reboot has not been tested.
- Measured on 2026-10-29 (release `019a906`): one `docker restart` of each service, one at a
  time. Each came back by itself: api 17 s, worker 14 s, scheduler 3 s, proxy 7 s, console-web
  6 s, postgres 9 s, redis 6 s, minio 9 s. `/readyz` answered 200 at the end. The checker's own
  api restart came back in 14 s.
- Before release `019a906`, a restart of the api broke it. Every uvicorn worker died with
  `PermissionError` on `/run/homayar-metrics/counter_<pid>.db`. The master kept starting new
  workers, which used about 2 CPUs. The cause is runc 1.3: when a tmpfs mount point already
  exists, the tmpfs gets that folder's mode, owned by root. After the first start that folder is
  root 755. The api and worker tmpfs mounts now say `uid=10001,gid=10001,mode=0700`
  (`compose.onprem.yaml`).
  If the loop ever comes back (for example with an older release), end it with
  `~/homayar/hc up -d --no-build --pull never --force-recreate --no-deps api`.

Full removal, Homayar only (destroys all data): `~/homayar/hc down --volumes`,
`docker rmi homayar/api:<sha12> homayar/console-web:<sha12>`, `rm -rf ~/homayar`.

## Go-Live (Owner Script)

The host nginx site and the certificate need `sudo`, so the owner runs one reviewed script. It is
on the server as `/home/gpu/hom328-app-site.sh`.

**Done on 2026-10-29 at 14:03 UTC, all green** (homlead's report): the certificate was issued, the
renewal dry run passed (webroot), nginx kept its MainPID (784486), the Padyar LAN codes were the
same after both reloads, `/app` answered `308` to `https://app.homayar.com/app/`, and there was one
HSTS header. The steps below stay for a retry or a new server.

1. Check the copy: `sha256sum ~/hom328-app-site.sh` must print
   `2d3b2846caddc68885291416883f789c9fb14c84a99c0cf4e80dae920866dd69`.
2. Run: `sudo bash ~/hom328-app-site.sh`. Send the whole output to homlead.
3. Right after it ends, the owner registers (section "First Owner Account").

What the script does, and what each step interrupts:

| Step | What | Interrupts |
| --- | --- | --- |
| 0 | Checks: root, no earlier run, `nginx -t`, the stack's `/readyz` is 200. Backup of `/etc/nginx`. Records nginx's MainPID and the Padyar LAN status codes. | nothing |
| 1 | Writes the port 80 server only, `nginx -t`, **reload 1**. Compares the Padyar LAN codes with step 0. Checks the ACME path (404) and the redirect (301) locally and through the relay. | an nginx reload (graceful: open connections stay; all 5 Padyar sites keep running) |
| 2 | `certbot certonly --webroot` (HTTP-01 through the relay). It reuses a valid certificate from an earlier run. | nothing |
| 3 | Writes the full site (below), `nginx -t`, **reload 2**. Compares the Padyar LAN codes again. Checks `/healthz`, `/readyz` and `/app/` (200 with a valid certificate), the `/app` redirect, the ACME path, the HTTP redirect and one HSTS header. | an nginx reload; `app.homayar.com` goes live |
| 4 | `certbot renew --dry-run --no-directory-hooks` (staging; it saves nothing and runs no hook). | nothing |
| 5 | Final checks: the MainPID is the same (a reload keeps it), the listeners, the new files, the LAN codes. | nothing |

- If `nginx -t` or any check fails, the script removes the new site and runs `nginx -t`. If a
  reload already happened, it reloads again. Then it stops. The certificate stays. It is
  harmless, and a retry reuses it.
- Retry after a rollback: read the output and fix the cause. Move the backup aside with
  `sudo mv /root/hom328-backup/nginx-before.tgz /root/hom328-backup/nginx-before-$(date -u +%Y%m%dT%H%M%SZ).tgz`,
  then run the script again. If it stops on the certificate, run
  `sudo certbot delete --cert-name app.homayar.com` first.
- The script's full output is in homlead's go-live report (HOM-328). The certificate dates are in
  "Health Checks" above.

## Host nginx Site and Certificate

`/etc/nginx/sites-available/app.homayar.com.conf`, as the owner script (step 3) writes it, plus
the HOM-340 recording location.
Measured: the live file has sha256
`e6b37f5a83d16328eaf0bd77a64fe263a4617c69f50cb67301904dd1e9e1d2b5`, the same as this block
**without** the HOM-340 recording location (`location ~ ^/api/v1/(...)/recording$`).
Not measured yet: that location was added by HOM-340 and is applied in rollout step 6 of
[recording-delivery-on-prem.md](../architecture/recording-delivery-on-prem.md). Update the hash
after that step.

```nginx
# HOM-328 app.homayar.com: the Homayar console (/app/) and API (/api/v1/).
# Runbook: docs/operations/app-server.md in the Homayar repository.
# Public only through the relay tunnel (HOM-332): frpc sends relay traffic to
# 127.0.0.1:10080 and 127.0.0.1:10443 with a PROXY protocol header. There are no
# LAN listens: LAN users use the public name, which goes through the relay.
# TLS ends here. The Homayar stack (Docker, user gpu) listens on 127.0.0.1:8080.

server {
    listen 127.0.0.1:10080 proxy_protocol;
    include snippets/relay-tunnel-realip.conf;
    server_name app.homayar.com;

    access_log /var/log/nginx/app.homayar.com.access.log;
    error_log  /var/log/nginx/app.homayar.com.error.log;

    # HTTP-01 for the certificate renewal (certbot webroot).
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type "text/plain";
    }

    location / { return 301 https://$host$request_uri; }
}

server {
    listen 127.0.0.1:10443 ssl http2 proxy_protocol;
    include snippets/relay-tunnel-realip.conf;
    server_name app.homayar.com;

    ssl_certificate     /etc/letsencrypt/live/app.homayar.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/app.homayar.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    access_log /var/log/nginx/app.homayar.com.access.log;
    error_log  /var/log/nginx/app.homayar.com.error.log;

    # One HSTS header, set here at the TLS boundary. The API sends its own with
    # includeSubDomains and preload; hide it so only this one reaches the browser.
    proxy_hide_header Strict-Transport-Security;
    add_header Strict-Transport-Security "max-age=31536000" always;

    # The stack refuses bodies over 16m outside the upload route
    # (infra/nginx/conf.d/homayar.conf). A larger limit here would let anyone make
    # this host buffer a big body to disk before the stack's 413.
    client_max_body_size 16m;

    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    # OVERWRITE, never append: a client value must not reach the stack.
    proxy_set_header X-Forwarded-For   $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-Host  $host;
    proxy_set_header Upgrade           $http_upgrade;
    proxy_set_header Connection        $connection_upgrade;
    # The stack's nginx and the API build absolute redirects with http:// (and the
    # nginx one with port 8080). Send them to the browser as https.
    proxy_redirect http://app.homayar.com:8080/ https://app.homayar.com/;
    proxy_redirect http://app.homayar.com/ https://app.homayar.com/;
    proxy_connect_timeout 5s;

    # Recording uploads (POST /api/v1/uploads): 300m and streaming, with the stack's
    # limit and timeouts (infra/nginx/includes/upload-proxy.conf).
    location ^~ /api/v1/uploads {
        client_max_body_size 300m;
        proxy_request_buffering off;
        proxy_buffering off;
        client_body_timeout 15m;
        proxy_send_timeout 15m;
        proxy_read_timeout 15m;
        proxy_pass http://127.0.0.1:8080;
    }

    # Recording download, inline playback and share link (HOM-340): the API streams them.
    # Not applied on the live host yet (rollout step 6, recording-delivery-on-prem.md).
    # With 0, nginx reads only as fast as the visitor takes the bytes, instead of copying
    # up to 1024m per response to this host's disk. Same limits as "location /".
    location ~ ^/api/v1/(meetings/[^/]+|workspaces/[^/]+/meetings/[^/]+|shares/[^/]+)/recording$ {
        proxy_max_temp_file_size 0;
        proxy_pass http://127.0.0.1:8080;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
    }
}
```

- The listen options are the same as on the Padyar tunnel sites, so nginx does not warn
  "protocol options redefined". `ssl_session_cache shared:SSL:10m` is the same zone and size as
  theirs.
- `$connection_upgrade` comes from `/etc/nginx/conf.d/00-websocket-map.conf`, and the snippet from
  `/etc/nginx/snippets/relay-tunnel-realip.conf` (HOM-332).
- One HSTS header `max-age=31536000` (1 year, no `includeSubDomains`, no `preload`). The API's own
  HSTS header (`max-age=63072000; includeSubDomains; preload`) is hidden.
- `client_max_body_size 16m` for every path, and `300m` only for `/api/v1/uploads`, the recording
  upload route. That route streams the body with 15-minute timeouts, like the stack.
- The three recording routes (download, inline playback, share link) have
  `proxy_max_temp_file_size 0`, like the stack's nginx (HOM-340). The API streams recordings
  on-prem (`STORAGE_PRESIGNED_DELIVERY=false`). Without it, each slow download could use up to
  1024m of this host's disk. A regex location wins over `location /`, and it inherits the
  server's `proxy_set_header` lines and the 16m body limit.
- No LAN listens. LAN users use the public name, which goes through the relay.

Certificate: Let's Encrypt, HTTP-01 with the webroot `/var/www/certbot`, through the relay's
port 80, with the ACME account the Padyar certificates use. Renewal:

- `certbot.timer` runs twice a day and renews within 30 days of expiry, through the same path.
- After a renewal, `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` reloads nginx.
- If the tunnel is down, certbot keeps trying for 30 days.
- The timer's exit code is shared with the Padyar renewals, which fail today
  ([HOM-335](https://linear.app/homayar/issue/HOM-335)). Check the app certificate with the
  `openssl` command in "Health Checks".
- Test without changing anything (owner):
  `sudo certbot renew --dry-run --cert-name app.homayar.com --no-directory-hooks`.

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

Do this right after go-live: anyone can register an address first (open item O4). Measured before
go-live: 0 organizations, 0 users, 0 workspaces. Not measured yet: each must be 1 after the owner
registers (`~/homayar/hc exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select (select count(*) from organizations),(select count(*) from users),(select count(*) from workspaces)"'`).

Sign-up is open at go-live: anyone on the internet can create an account with its own empty
organization. It is closed now: see "Sign-up (Closed)" below.

## Sign-up (Closed)

`REGISTRATION_ENABLED=false` in `config/settings.env` closes self-sign-up (HOM-336). The api
refuses `POST /api/v1/auth/register` with `403 registration_closed`, and phone OTP and Google
never create a new account. Everything an existing account needs still works: login, sessions,
password reset, e-mail verification, workspace invitations.

An invited person still joins:

1. The workspace owner invites them from the console (Members page); the e-mail links to
   `https://app.homayar.com/app/invitations/accept?token=<token>`.
2. Signed out, the page offers "Create an account", which opens
   `/app/register?invitation=<token>`. The register form accepts exactly that link.
3. They create the account (the api checks the invitation token and the e-mail match), verify
   the e-mail, and sign in. After sign-in they land back on the invitation page and accept.

How to reopen:

1. Set `REGISTRATION_ENABLED=true` in `config/settings.env`.
2. Recreate the api container with the runbook's own command from "Restart and Stop":
   `~/homayar/hc up -d --no-build --pull never --force-recreate --no-deps api`.
3. Check: `curl -s https://app.homayar.com/api/v1/auth/methods` shows `"registration":true`.

How to close again: set `REGISTRATION_ENABLED=false` and recreate the api the same way; the
check must show `"registration":false`.

## Providers (Not Configured Yet)

The stack runs without any provider key. `/healthz` and `/readyz` pass, and processing jobs fail
with a clear error until a provider is set. Keys go into `secrets/<name>` (mode 600) and
`secrets/secrets.env`. Settings without secrets go into `config/settings.env`.

| Need | Settings | Also needed |
| --- | --- | --- |
| Speech to text | Only `local_whisper` exists in the code. `ASR_PROVIDER=local_whisper`, `WHISPER_MODEL`, `WHISPER_DEVICE=cpu`, `WHISPER_COMPUTE_TYPE`. A named `ASR_PROFILE` (ADR 0044) replaces the three `WHISPER_*` values; empty keeps them. Only the `cpu-*` profiles work on this server: the image has no CUDA, and `gpu-p40-large-v3` needs it. The language settings `ASR_ALLOWED_LANGUAGES` (default `fa,en`), `ASR_DEFAULT_LANGUAGE` (default `fa`) and `ASR_LANGUAGE_MIN_PROBABILITY` (default `0.5`) are settable in the env file. | Rebuild the api image with `--build-arg INSTALL_AI_LOCAL=true` (the image has no CUDA). Model weights: offline (`WHISPER_LOCAL_FILES_ONLY`, `WHISPER_MODEL_PATH`, which the compose file does not pass yet: add them in an overlay) or through the egress proxy. A model manifest. Higher worker memory and CPU limits (talk to homlead). |
| Language model | `INTELLIGENCE_PROVIDER=openai_compatible` and one of: `LOCAL_AI_BASE_URL` (private endpoint, no key); `GAPGPT_API_KEY` (secret) with `LLM_PROVIDER=gapgpt` and `LLM_MODEL`; `ZAI_API_KEY` (secret). Optional fallback: `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL`, `LLM_FALLBACK_API_KEY` (secret). | `GAPGPT_API_KEY`, `ZAI_API_KEY`, `LLM_PROVIDER` and `LLM_MODEL` are not passed by `compose.onprem.yaml`. Add them in a deployment overlay with `${NAME:?...}`. Egress (next row). A model manifest. |
| Egress (any cloud provider, Asanak) | `EGRESS_PROXY_URL=http://egress-proxy:3128`; `EGRESS_ALLOWLIST_PATH=/home/gpu/homayar/config/egress-allowlist.txt` (outside the release, so updates keep it) | Build `homayar/egress-proxy` (`infra/docker/egress-proxy.Dockerfile`). Add `--profile egress` to `hc`. Allowlist `host:port` lines from `~/homayar/hc run --rm -T --no-deps api python -m backend.jobs.model_preflight --egress-allowlist`, plus for example `gapgpt.app:443` and `panel.asanak.com:443`. |
| Model manifests | `MODEL_MANIFEST_PATHS`, `MODEL_MANIFEST_DIR` | Production is strict. Run `model_preflight`, then `model_preflight --register` (`deployment.md`). |
| Phone sign-in | `ENABLE_PHONE_AUTH=true`, `ASANAK_SENDER`, `ASANAK_API_KEY` (secret) | Egress and `panel.asanak.com:443` |
| E-mail (verify, reset, invite) | `SMTP_HOST`, `SMTP_PORT` (465 or 587), `SMTP_USERNAME`, `SMTP_FROM`, `SMTP_USE_TLS`, `SMTP_PASSWORD` (secret) | SMTP does not pass the egress proxy. It needs a mail relay on an internal network. Production refuses SMTP without TLS. |
| Optional | `PII_MAPPING_KEYS` (secret), `PII_MAPPING_ACTIVE_KEY_ID`, `METRICS_BEARER_TOKEN` (secret) | |
| Outbound webhooks | `WEBHOOK_SECRET_KEYS` (secret), `WEBHOOK_SECRET_ACTIVE_KEY_ID` (ADR 0050) | Without them no webhook endpoint can be created. |

After any provider change: `~/homayar/hc up -d --no-build --pull never`, then
`~/homayar/hc run --rm -T --no-deps api python -m backend.jobs.model_preflight 2>&1 | sed -E 's/[0-9a-f]{20,}/<redacted>/g'`.
Measured today (no provider): it exits 2 with `intelligence_provider_unconfigured`. That is
expected.

### Host preflight

`--host` also checks this server: the local speech runtime, the GPU driver and free VRAM, free
disk, the connections to PostgreSQL and Redis, and the certificate. Flags and checks are in
`deployment.md` ("Host preflight"). Run it as user `gpu`:

```bash
~/homayar/hc run --rm -T --no-deps api python -m backend.jobs.model_preflight --host
```

The certificate is readable only by root. To check it, the owner passes it on stdin. Pass only
`fullchain.pem`. Never pass `privkey.pem`: the check fails when the input holds a private key.
`--tls-host` works only together with `--tls-cert`.

```bash
sudo cat /etc/letsencrypt/live/app.homayar.com/fullchain.pem \
  | ~/homayar/hc run --rm -T --no-deps api python -m backend.jobs.model_preflight --host \
    --tls-cert - --tls-host app.homayar.com
```

**Not measured yet.** Expected result today:

- The api image is built without `INSTALL_AI_LOCAL=true` and `WHISPER_DEVICE=cpu`. So
  `gpu_driver` and `gpu_memory` are `skip`, and `asr_runtime` is `fail` (no faster-whisper)
  until the image is built with `INSTALL_AI_LOCAL=true`.
- No language model is configured yet, so the command exits 2 with
  `intelligence_provider_unconfigured` (see above). It still prints the `host` report. Exit 2
  stays exit 2, whatever the host checks say.

This server is the reference shape of topology `single-host-p40` (ADR 0047). **Not run on this
server yet.** To check it later:

```bash
~/homayar/hc run --rm -T --no-deps api python -m backend.jobs.model_preflight --host \
  --topology single-host-p40 --min-free-gpu-mib 4096
```

GPU mode (`WHISPER_DEVICE=cuda`, or a GPU ASR profile) needs more than a setting:

- GPU access for the container (a Compose `gpus` or device reservation for the api and worker).
- CUDA in the image (an image built with `INSTALL_AI_LOCAL=true` and the CUDA libraries).
- The two Tesla P40 GPUs are shared with other services, so free VRAM is less than 24 GiB and
  changes over time. Measure the free VRAM the model needs first, then set
  `--min-free-gpu-mib` from that number. The P40 is a Pascal GPU (compute capability 6.1): use
  the compute type `int8_float32`, not `float16`.

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
- Closing sign-up (HOM-336) and turning the free demo on (HOM-337).

Found during HOM-328:

- **O1.** Recording download and playback redirect the browser to a presigned URL at
  `http://minio:9000`, which a browser cannot open. Must be fixed before uploads go live.
- **O2.** The owner account is not a platform operator (`system_role` stays `user`). Operator
  pages need a database change or new code.
- **O3.** The proxy and console-web run 36 nginx workers each (`worker_processes auto` on 36
  cores). Their memory is measured above (17% and 22% of their limits).
- **O4.** Anyone can register an e-mail address first, including the owner's. The owner should
  register right after go-live.
- **O5.** A retry of the owner script after a rollback needs the backup moved aside (see
  "Go-Live").
- **O6.** A process on the server itself can choose the client IP the app sees (see "Client IP
  and Scheme").
- **O7.** `minio-bootstrap` puts the MinIO root password and the app secret key on the `mc`
  command line. This lasts 1 to 2 seconds on every `up`, and any local user can read it. The ops
  scripts `backup-minio.sh` and `restore-minio.sh` do the same. Must be fixed before uploads go
  live.
- **O1 and O7 follow-up: [HOM-340](https://linear.app/homayar/issue/HOM-340).** MinIO keys on the
  command line and download links to `http://minio:9000`. Marked "must be fixed before uploads go
  live".
- The bundled nginx warns that its 2048 `worker_connections` exceed the container's open-file
  limit of 1024 (the Docker 29 default soft limit). Fine at today's traffic. A fix is
  `worker_rlimit_nofile` in `infra/nginx/nginx.conf` or `ulimits: nofile` in the overlay.
- Images built on this server with the legacy builder ignore
  `infra/docker/*.Dockerfile.dockerignore`. The api image therefore also holds
  `Homayar/backend/tests` (201 files, 5.1 MB, no `.env` file, not used at runtime). A fix is a
  pinned, checksum-verified `buildx` plugin in `~/.docker/cli-plugins/` (needs approval), or a
  BuildKit build elsewhere loaded with `docker load`.
- The worker health check starts a full `celery inspect ping` process every 30 seconds (about one
  CPU core for a second, plus 60 MiB).
- A foreign `Host` header on `127.0.0.1:8080` still gets the static console HTML (the API part
  answers 400). Through the relay such a request is closed by the host nginx.
- The tombstone journal is on a local Docker volume, not replicated (backups ticket).
- A reboot of the local server has not been tested.

Fixed during HOM-328 (in this branch):

- The scheduler had no restart policy (`compose.onprem.yaml`, and `compose.cloud.yaml`).
- The api and worker tmpfs mounts were owned by root after a restart (`compose.onprem.yaml`, and
  `compose.cloud.yaml`). Now `uid=10001,gid=10001,mode=0700`.
- `minio-bootstrap` was killed at 128 MiB. The overlay gives it 256 MiB.
