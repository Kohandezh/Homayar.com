# Deployment Runbook

## Scope

The infrastructure baseline exposes one origin through nginx:

- `/` routes to the Next.js public web service.
- `/app/` routes to the Vite console and falls back to `/app/index.html` for client routes.
- `/api/v1/` routes unchanged to FastAPI. Both `/api/v1/uploads` and
  `/api/v1/uploads/` use the streaming 300 MB upload policy.
- `/healthz` reports nginx liveness without testing dependencies.
- `/readyz` proxies FastAPI's dependency-aware readiness response.

`compose.onprem.yaml` runs PostgreSQL, Redis, MinIO, API, worker, both web services,
and nginx. `compose.cloud.yaml` runs only application services and nginx; PostgreSQL,
Redis, and S3-compatible storage are externally managed. Neo4j is an optional overlay on
the isolated `graph` network and is never a transactional dependency.

## Artifact Contract

| Artifact | Build context | Template | Runtime contract |
| --- | --- | --- | --- |
| Public web | repository root | `infra/docker/public-web.Dockerfile` | Next.js on port 3000 |
| Product console | repository root | `infra/docker/console-web.Dockerfile` | static `/app/` tree on port 8080 |
| API and worker | repository root | `infra/docker/api.Dockerfile` | FastAPI on 8000; Celery from the same image |
| Reverse proxy | official nginx image plus bind-mounted config | `infra/nginx/` | HTTP on port 8080 |

The Dockerfiles are infrastructure-owned templates. They are not release artifacts until
CI builds them, scans them, starts the selected profile, and exercises health checks. In
particular, do not infer image viability from a successful `docker compose config` check.

Before promotion, verify these application-owned contracts:

1. `Homayar/` is present in the release checkout. It is currently an embedded Git working
   tree; integrate it into the monorepo or configure and initialize it as a real submodule.
2. The console is built with Vite base `/app/`, uses a router basename derived from that
   base, and sends API requests to `/api/v1`.
3. FastAPI serves its versioned routes below `/api/v1`, liveness at `/healthz`, and
   dependency-aware readiness at `/readyz`.
4. `Homayar/pyproject.toml`, `Homayar/uv.lock`, `Homayar/alembic.ini`, and all migration
   revisions are present in the API build context and synchronized.
5. The root Next.js application completes a production build and starts with `next start`.
6. The selected provider implementation is installed. Set `INSTALL_AI_LOCAL=true` when
   the on-premise image must contain the optional local Whisper dependency.

The public image uses pnpm's explicit `allowBuilds` policy for `esbuild`, `sharp`, and
`unrs-resolver`. Do not replace it with `dangerouslyAllowAllBuilds`; newly introduced
dependency lifecycle scripts must fail closed until reviewed.

## Secret Contract

Compose uses required `${VARIABLE:?message}` substitutions for every credential. Keep
values in the deployment secret manager or an operator-owned file with mode `0600`; no
runtime secret file is committed by this infrastructure baseline. Avoid `docker compose
config` in shared logs because expanded output contains resolved secrets.

Common required values:

| Variable | Purpose |
| --- | --- |
| `PUBLIC_ORIGIN` | Canonical browser origin, with no trailing slash |
| `CORS_ORIGINS` | Exact comma-separated allowed origins |
| `TRUSTED_HOSTS` | Exact comma-separated HTTP hostnames |
| `DATABASE_URL` | PostgreSQL SQLAlchemy DSN; URL-encode credentials |
| `REDIS_URL` | Redis DSN; URL-encode credentials and use `rediss://` for managed TLS |
| `JWT_SECRET` | Random signing secret, at least 32 characters |
| `TOKEN_PEPPER` | Independent random token pepper, at least 32 characters |
| `STORAGE_ACCESS_KEY` | Dedicated application object-store identity |
| `STORAGE_SECRET_KEY` | Dedicated application object-store credential |

The on-premise profile also requires `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `MINIO_ROOT_USER`, and
`MINIO_ROOT_PASSWORD`. `DATABASE_URL` and `REDIS_URL` must describe those same services;
they remain explicit because composing a URL from raw passwords is unsafe when credentials
contain reserved characters.

Redis runs directly as the non-root UID/GID shipped by the pinned Alpine image
(`REDIS_UID=999`, `REDIS_GID=1000` by default). When replacing that image, verify its `redis`
identity and override both values before first start. Existing volumes created with different
ownership require an explicit, reviewed ownership migration while Redis is stopped.

The cloud profile additionally requires `STORAGE_BUCKET`, `STORAGE_REGION`, and immutable
`PUBLIC_WEB_IMAGE`, `CONSOLE_WEB_IMAGE`, and `API_IMAGE` references. The current storage
settings require static S3 credentials. Workload identity should replace them only after
the application supports a credential provider chain without requiring access keys.

### Secret references

Every process that loads the backend settings (API, worker, scheduler, migration job) accepts
each secret-bearing setting either as the plain variable `NAME` or as a reference `NAME_FILE`
whose value is the path of a file holding the secret (a secret-manager mount, a Docker or
Kubernetes secret, a systemd credential). The covered settings are `DATABASE_URL`,
`REDIS_URL`, `JWT_SECRET`, `TOKEN_PEPPER`, `GOOGLE_CLIENT_SECRET`, `ASANAK_API_KEY`,
`STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, `PII_MAPPING_KEYS`, `LLM_FALLBACK_API_KEY`,
`ZAI_API_KEY`, `GAPGPT_API_KEY`, `SMTP_PASSWORD`, `NEO4J_PASSWORD`, and `SENTRY_DSN`.
For example, `JWT_SECRET_FILE=/run/secrets/jwt_secret` replaces `JWT_SECRET`.

- **Names.** Like every setting, names are case-insensitive (`JWT_SECRET_FILE`,
  `jwt_secret_file`). In the process environment, the same reference set twice in different
  letter case is rejected. In the `.env` file it is not: the spelling that appears last in
  the file silently wins, as for any other `.env` entry. Write each name only once in `.env`.
- **Conflict rule.** `NAME` and `NAME_FILE` set together in the process environment, or
  together in the `.env` file, stop startup with an error that names both variables. This
  holds even when `NAME` is empty, so remove `NAME` when you add `NAME_FILE`. An empty
  `NAME_FILE=` is also rejected. The bundled Compose profiles pass the plain variables; a
  deployment that uses references must provide `NAME_FILE` instead of `NAME`, not beside it.
- **Precedence between places** is unchanged: explicit keyword arguments (code and tests),
  then the process environment, then the `.env` file, then defaults. A reference ranks with
  the place it is set in: `NAME_FILE` in the environment overrides `NAME` in the `.env` file,
  and `NAME` in the environment overrides `NAME_FILE` in the `.env` file. Every reference
  that is set is read and checked, even when a higher-ranked value wins.
- **The file** must be a readable regular UTF-8 file. Symbolic links are followed, so
  Kubernetes projected secrets work. Use absolute paths; a relative path resolves against the
  process working directory. A missing or unreadable file, a directory, a FIFO or device,
  or content that is not UTF-8 stops startup with an error that names the variable and the
  path, never the content. The error shows the path, so never put a secret itself into
  `NAME_FILE`. Some paths are refused before they are opened: a path that is not a regular
  file, and any path that resolves under `/proc` or `/sys` after symbolic links (for example
  `/proc/self/environ`). A file larger than 64 KiB (65536 bytes) is refused after reading at
  most 64 KiB plus one byte.
- **Newline handling.** Exactly one trailing newline (`\n` or `\r\n`) is removed and nothing
  else is changed: leading or trailing spaces, a lone trailing `\r`, and any further
  newlines are part of the value. A UTF-8 byte-order mark at the start of the file is also
  kept as part of the value, so write secret files without a BOM. An empty file yields an
  empty value, which production then rejects through its existing checks (for example the
  32-character `JWT_SECRET`).
- **Neo4j.** `NEO4J_PASSWORD_FILE` is read by the backend and the development seed
  (`python -m backend.database.seed`), which load these settings. The Neo4j container in
  the development `Homayar/docker-compose.yml` does not load them: it takes its credential
  from `NEO4J_PASSWORD` (environment or `Homayar/.env`), so set `NEO4J_PASSWORD` for that
  container even when the backend uses `NEO4J_PASSWORD_FILE`. The seed deletes the whole
  graph before loading sample data. It therefore refuses to run unless `ENVIRONMENT` is
  `development` or `test`, and it refuses to run without a password.
- **Redaction.** None of these values appears in `repr()` or `str()` of the settings object,
  and settings validation errors keep their messages but carry no input values.

Production also refuses two insecure transports (development, test, and staging are
unchanged):

- **SMTP.** With `SMTP_HOST` set, plaintext mail is refused. The rule matches how mail is
  sent: port 465 uses implicit TLS, port 587 always issues STARTTLS (and fails if the server
  does not offer it), and any other port uses TLS only with `SMTP_USE_TLS=true`. Accepted are
  therefore `SMTP_PORT=465` or `587` (with either `SMTP_USE_TLS` value), any port with
  `SMTP_USE_TLS=true`, or an empty `SMTP_HOST` (no e-mail). Refused is any other port (for
  example 25) with `SMTP_USE_TLS=false`. The Compose default, port 587 with
  `SMTP_USE_TLS=false`, is accepted.
- **Object store.** `STORAGE_BACKEND=s3` with a non-empty `STORAGE_ENDPOINT_URL` requires an
  `https://` URL with a host. `http://` in any letter case, any other scheme, and a
  scheme-less `host:port` are refused. An empty endpoint (the provider's default) is
  accepted. The one exception is on-premise:
  - `STORAGE_ALLOW_INSECURE_ENDPOINT=true` (default `false`) lets production use a non-https
    endpoint, but only together with `DEPLOYMENT_MODE=onprem`. With `DEPLOYMENT_MODE=cloud`
    the flag has no effect, and without the flag the endpoint is refused.
  - `compose.onprem.yaml` sets the flag because its bundled MinIO is reached at
    `http://minio:9000` on the internal compose network.
  - Residual risk: object traffic between the API or worker and MinIO, including
    recordings and credentials, is plaintext on that internal network. Keep MinIO off
    published ports and shared networks. Serving the bundled MinIO over TLS, which retires
    the flag, is a planned follow-up.
  - Ambient endpoint overrides never reach the S3 client: it is built with
    `ignore_configured_endpoint_urls`, so `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_S3` and an
    `endpoint_url` in the shared AWS config (profile or `services` section) are ignored and
    only `STORAGE_ENDPOINT_URL` (or the provider default) is used. Production with
    `STORAGE_BACKEND=s3` still refuses to start while any of them is set to something other
    than an `https://` URL, or when the shared AWS config cannot be read; the error names the
    source, never its value. `STORAGE_ALLOW_INSECURE_ENDPOINT` does not cover these overrides.

### Optional runtime settings (defaults keep them off)

Both Compose files pass these through `x-app-environment`; empty means disabled or the
product default. Secrets among them come from the secret manager, never from build args.

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL_MANIFEST_PATHS` | empty | Operator manifests, container paths such as `/app/manifests/operator.json` (ADR 0026). `MODEL_MANIFEST_DIR` (default `infra/manifests`) is mounted read-only at `/app/manifests` on `api`, `worker` and `scheduler`. Leave `MODEL_MANIFEST_STRICT` unset: strict outside development/test, and production refuses `false`. |
| `MODEL_MANIFEST_PINS` | empty | `stage=manifest_id@version` pins |
| `JOB_RETRY_JITTER_RATIO` | `0.1` | Positive jitter on retry back-off (ADR 0030) |
| `PROVIDER_CIRCUIT_FAILURE_THRESHOLD` / `PROVIDER_CIRCUIT_COOLDOWN_SECONDS` | `5` / `60` | Shared endpoint circuit |
| `PROVIDER_RATE_LIMITS` | empty | `manifest_id=60,...` requests per minute |
| `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL`, `LLM_FALLBACK_API_KEY` (secret) | empty | Same-policy analysis fallback; needs its own manifest and an owner's `fallback: true` in the workspace AI policy |
| `ASR_PROVIDER`, `DIARIZATION_PROVIDER`, `INTELLIGENCE_PROVIDER`, `LOCAL_AI_BASE_URL`, `WHISPER_*` | product defaults | AI providers. `local_whisper` needs an image built with `INSTALL_AI_LOCAL=true`. `padyar` fails jobs with `provider_contract_unavailable` until the Padyar AI Gateway API contract exists. |
| `PII_MAPPING_KEYS`, `PII_MAPPING_ACTIVE_KEY_ID` (secret) | empty | Restricted PII mappings (ADR 0023); empty keeps redaction but retains no originals |
| `ENABLE_GOOGLE_AUTH`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (secret), `GOOGLE_REDIRECT_URI` | off | Google sign-in; the redirect URI must be `${PUBLIC_ORIGIN}/api/v1/auth/google/callback` |
| `ENABLE_PHONE_AUTH`, `ASANAK_API_KEY` (secret), `ASANAK_SENDER` | off | Phone OTP through Asanak |
| `SMTP_HOST`, `SMTP_PORT` (587), `SMTP_USERNAME`, `SMTP_PASSWORD` (secret), `SMTP_FROM`, `SMTP_USE_TLS` | empty | Verification, password-reset and invitation e-mail (STARTTLS on 587, TLS on 465). Without `SMTP_HOST` no e-mail is sent: registration and resend still answer, invitations fail with `invitation_delivery_failed` and are revoked. |
| `ENABLE_FREE_DEMO`, `DEMO_QUOTA_SECONDS` | `true`, `1800` | Demo allowance of new workspaces |
| `EGRESS_PROXY_URL` | empty | `http://egress-proxy:3128` when the egress profile runs (see "Network egress") |

Plan catalog: the API start creates missing catalog plans and never rewrites an existing
one. A plan's terms (name, `is_public`, entitlements) change only by appending a plan
version, and each subscription stays on the version it was granted. The one version a
start appends is the demo allowance, which `DEMO_QUOTA_SECONDS` owns: after a change, the
next start appends a demo version with the new value, and only later sign-ups get it.
Retire a plan with `is_active = false`. A direct SQL edit of a plan's terms persists but
is not a version: the demo grant at sign-up still follows the current version, and each
start logs `PLAN_CATALOG_DRIFT plan=<key> fields=<names>`. One exception: the demo version
a `DEMO_QUOTA_SECONDS` change appends carries the demo row's terms as they are, direct
edits included. Sign-up grants the demo only while `ENABLE_FREE_DEMO` is on and the demo
plan is active.

The API refuses to start with Google or phone sign-in enabled but its credentials
missing. The console no longer has `VITE_ENABLE_GOOGLE_AUTH` / `VITE_ENABLE_PHONE_AUTH`
build flags: it asks `GET /api/v1/auth/methods`, which reports a method only when it is
enabled and configured, so the offered methods always match this configuration.

After deploying, and after any manifest or provider change, run the model preflight
(`exit 1` means a stage has no qualified manifest; nothing is registered then):

```bash
docker compose --file infra/compose/compose.onprem.yaml --profile onprem run --rm --no-deps api python -m backend.jobs.model_preflight
docker compose --file infra/compose/compose.onprem.yaml --profile onprem run --rm --no-deps api python -m backend.jobs.model_preflight --register
```

## Network Egress

Application-level permits (ADR 0031) refuse any provider request no routing decision
covers; the network layer below makes sure a process that bypasses them has nowhere to go.

On-premise (`compose.onprem.yaml`):

| Network | Internal | Members |
| --- | --- | --- |
| `edge` | no | `proxy` (nginx) only: the published port |
| `ingress` | yes | `proxy`, `public-web`, `console-web`, `api` |
| `backend` | yes | `api`, `worker`, `scheduler`, PostgreSQL, Redis, MinIO, jobs |
| `egress` | yes | `api`, `worker`, `egress-proxy` |
| `outbound` | no | `egress-proxy` only |

No application container has a route out. Outbound HTTP(S) goes only through
`egress-proxy` (profile `egress`, deny-by-default Squid), which forwards CONNECT and plain
HTTP to the exact `host:port` lines of `infra/egress/allowlist.txt` (or
`EGRESS_ALLOWLIST_PATH`) and refuses everything else, loopback and link-local (cloud
metadata) addresses and its own manager pages. It publishes no port. The air-gapped default
is to not run the profile and leave `EGRESS_PROXY_URL` empty: there is then no outbound path
at all.

```bash
docker compose --file infra/compose/compose.onprem.yaml --profile onprem run --rm --no-deps api \
  python -m backend.jobs.model_preflight --egress-allowlist            # review, then add to allowlist.txt
docker compose --file infra/compose/compose.onprem.yaml --profile onprem --profile egress up --detach
```

`--egress-allowlist --require-no-cloud` exits 3 when a cloud AI endpoint is configured; CI
runs it for the default on-prem configuration. Add non-AI providers explicitly and only when
enabled (Google: `oauth2.googleapis.com:443`, `www.googleapis.com:443`; Asanak:
`panel.asanak.com:443`).

Limits, stated plainly:

- **SMTP does not pass through the proxy** (it is not HTTP) and the isolated topology gives the
  API no other route out. An on-premise deployment that sends e-mail needs a mail relay
  reachable on an internal network (a deployment-specific addition that is not provided);
  otherwise leave `SMTP_HOST` empty and accept that no e-mail is sent.
- **Private AI endpoints on the customer LAN** are reached through the proxy too (list them);
  a local model server can instead run as a Compose service on the `egress` or `backend`
  network.
- **Cloud profile:** PostgreSQL, Redis and object storage are external services, so Compose
  cannot make the application networks internal. Network-level egress control there must be
  enforced by the platform (security groups / firewall allowing only the managed data
  services and the egress proxy); `EGRESS_PROXY_URL` routes HTTP(S) through a proxy you
  provide. This is not verified by the repository's tests.

Evidence: `infra/egress/tests/test-egress-network.sh` starts the real `egress-proxy` of the
on-prem file plus two disposable HTTP servers and probes from inside the `api`, `worker`,
`scheduler`, `public-web` and `console-web` containers as Compose attaches them. It is a
container-network test on one Docker host, not a validation of a production deployment.

## Image Policy

Defaults are conservative local tags. Production deployments must override base and state
images with reviewed digest references and record the digests with the release. Relevant
overrides include `NODE_IMAGE`, `PYTHON_IMAGE`, `NGINX_IMAGE`, `POSTGRES_IMAGE`,
`REDIS_IMAGE`, `MINIO_IMAGE`, `MINIO_MC_IMAGE`, and `NEO4J_IMAGE`.

Never place provider API keys in Docker build arguments. Add optional runtime providers in
a deployment-specific Compose override whose secret values also use required substitutions.

## On-Premise Deployment

Commands below run from the repository root and assume the required environment is already
present in the operator session.

```bash
docker compose --file infra/compose/compose.onprem.yaml --profile onprem config --quiet
docker compose --file infra/compose/compose.onprem.yaml --profile onprem build
docker compose --file infra/compose/compose.onprem.yaml --profile onprem up --detach postgres redis minio minio-bootstrap
docker compose --file infra/compose/compose.onprem.yaml --profile onprem --profile migrate run --rm migrate
docker compose --file infra/compose/compose.onprem.yaml --profile onprem up --detach
docker compose --file infra/compose/compose.onprem.yaml --profile onprem ps
curl --fail --silent --show-error http://127.0.0.1:8080/healthz
curl --fail --silent --show-error http://127.0.0.1:8080/readyz
```

For an HTTP-only local session, bind to loopback, set `APP_ENVIRONMENT=development`, and
set `SECURE_COOKIES=false`. Production remains `APP_ENVIRONMENT=production` with secure
cookies and TLS. Do not expose PostgreSQL, Redis, MinIO, or Neo4j ports publicly; the base
profile publishes only nginx.

### Optional Neo4j Projection

Set `NEO4J_PASSWORD` and include the overlay only when relationship projection is enabled:

```bash
docker compose \
  --file infra/compose/compose.onprem.yaml \
  --file infra/compose/compose.neo4j.yaml \
  --profile onprem \
  --profile neo4j \
  config --quiet
docker compose \
  --file infra/compose/compose.onprem.yaml \
  --file infra/compose/compose.neo4j.yaml \
  --profile onprem \
  --profile neo4j \
  up --detach
```

APOC is intentionally not downloaded at startup. Bake and scan any required plugin in a
derived Neo4j image so on-premise startup does not depend on outbound package downloads.

The same overlay can be composed with `compose.cloud.yaml` for a single-host parity or
development environment. A production cloud deployment should normally use a managed or
separately operated Neo4j endpoint instead of coupling graph storage to an application host:

```bash
docker compose \
  --file infra/compose/compose.cloud.yaml \
  --file infra/compose/compose.neo4j.yaml \
  --profile cloud \
  --profile neo4j \
  config --quiet
```

## Cloud-Like Deployment

The cloud Compose profile is a single-host parity environment, not a replacement for a
multi-zone scheduler. Managed service DSNs must use TLS and restrict network access to the
API and worker hosts.

```bash
docker compose --file infra/compose/compose.cloud.yaml --profile cloud config --quiet
docker compose --file infra/compose/compose.cloud.yaml --profile migrate run --rm migrate
docker compose --file infra/compose/compose.cloud.yaml --profile cloud up --detach
docker compose --file infra/compose/compose.cloud.yaml --profile cloud ps
```

For horizontal production deployment, translate the same contracts to the chosen scheduler:
immutable images, a separate migration job, rolling readiness gates, worker drain time, and
managed secrets. The `app_data` volume is only transient compatibility storage and must not
be treated as durable cloud state. The tombstone journal (`TOMBSTONE_JOURNAL_DIR`, Compose
volume or `TOMBSTONE_JOURNAL_PATH` bind mount at `/app/tombstones`, shared by `api` and
`worker`) is the opposite: durable, append-only state that must live outside the database and
object-store backups and be replicated off-site (see the backup-and-restore runbook).

## TLS and Proxy Trust

The bundled nginx listener is HTTP on 8080. Terminate TLS at a trusted load balancer or a
customer-managed ingress, bind nginx to a private interface, and add HSTS at that TLS
boundary. Preserve the original host and request ID. Do not trust arbitrary client-supplied
forwarding headers; configure the ingress and nginx trusted-proxy ranges together before
using forwarded client addresses for security decisions.

## cPanel Static Public Site

cPanel can host only the static public site. The SaaS console under `/app/`, FastAPI under
`/api/v1/`, workers, PostgreSQL, Redis, and object storage still require the container or
equivalent VPS/cloud deployment described above.

Build from the repository root with the canonical public origin:

```bash
corepack enable
corepack prepare pnpm@11.1.2 --activate
pnpm config set --location=project --json allowBuilds \
  '{"esbuild":true,"sharp":true,"unrs-resolver":true}'
pnpm install --frozen-lockfile
NEXT_PUBLIC_APP_URL=https://homayar.com pnpm build:cpanel
```

The build removes any previous `out/`, temporarily excludes Next route handlers, forces
commerce off, restores the source tree, and validates `index.html`, `404.html`, and
`.htaccess`. It also rejects an exported API directory, symbolic links, and environment
files. The manually maintained public pricing is compiled into the static pages; no price
or secret is fetched from a deployment provider.

Upload the **contents** of `out/` to the domain's cPanel document root, normally
`public_html/`. Ensure hidden files are included so `out/.htaccess` becomes
`public_html/.htaccess`; it supplies static security headers, cache rules, directory-index
protection, and the 404 document. Do not upload the repository, `.env` files,
`node_modules`, `.next`, or the nested `Homayar/` application tree.

## Database Roles and Row-Level Security

Tenant isolation is enforced twice: by the application authorization matrix and by
PostgreSQL row-level security (WP03-T05, migration `c4e8b2a7d519`, policies in
`backend/database/rls.py`). The database side only holds if the runtime connects as a
role the policies apply to.

| Role | Purpose | Requirements |
| --- | --- | --- |
| Migration / owner role | Runs `alembic upgrade`; owns every table | May be the provisioned database owner. Never used by the API or workers at runtime unless `DATABASE_APP_ROLE` switches away from it. |
| `homayar_app` | Application role the policies target | Created `NOLOGIN NOSUPERUSER NOBYPASSRLS` by the migration; granted DML on all tables. |
| Runtime login role | What `DATABASE_URL` authenticates as | Either a login role `IN ROLE homayar_app` (recommended; set `DATABASE_APP_ROLE=""`), or the owner role with `DATABASE_APP_ROLE=homayar_app` so every pooled connection runs `SET ROLE` on checkout. Never a superuser or `BYPASSRLS` role: production start-up refuses both. |

Provisioning the recommended shape after the first migration:

```sql
CREATE ROLE homayar_api LOGIN PASSWORD '<secret>' NOSUPERUSER NOBYPASSRLS IN ROLE homayar_app;
```

Tenant context is bound per transaction (`set_config(..., true)`) from the validated
session, job or share/invitation token; a connection with no bound context sees no tenant
rows. Policies are `FORCE`d, so even the owner role is subject to them: a data repair run
as the owner must bind `homayar.workspace_id` explicitly or temporarily `ALTER TABLE ...
NO FORCE ROW LEVEL SECURITY` inside a reviewed change.

Connection poolers: the transaction-scoped context is safe under PgBouncer transaction
pooling by construction. The `SET ROLE` shape is **not** (the role switch is issued outside
the transaction that follows), so behind a transaction-mode pooler use the direct login
role and leave `DATABASE_APP_ROLE` empty.

## Upgrade and Rollback

1. Take PostgreSQL and object-store backups and verify their checksums.
2. Record current image digests and migration revision.
3. Drain the workers (stop consuming, let running attempts finish or time out)
   **before** the migration job. Since WP06-T04 running attempts hold an execution
   lease; migration `f1b6d8e4a2c7` marks any job still RUNNING as expired so the
   lease sweeper requeues it — a pre-lease worker that keeps writing would not be
   fenced by its own code.
4. Pull or build candidate images, validate Compose, and run the migration job once.
5. Start API, worker and the `scheduler` (Celery beat: dispatch sweep and lease
   recovery), wait for health, then expose traffic through nginx.
6. Run authentication, upload, job, and tenant-isolation smoke tests.

Application rollback uses the previously recorded image digests. Do not automatically
downgrade the database. Every release migration must be backward-compatible with the prior
application during the rollback window, or the release plan must include an independently
tested restore procedure and accepted data-loss boundary.

Migration `c5e7a9b1d3f6` (versioned plans, WP18-T03) is not backward-compatible:
`subscriptions.plan_version` and `plans.current_version` are NOT NULL without a default,
so the prior image fails every demo sign-up. For that release, stop the API as well as the
workers before step 4. To roll the application back past it, export `plan_versions`, run
`alembic downgrade a1c3e5f7b9d2` (it drops the version history and the subscription pins),
then deploy the prior images. Their start rewrites every catalog plan row to its code
defaults, so restore published terms from that export or the step 1 backup.
