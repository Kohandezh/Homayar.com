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
be treated as durable cloud state.

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
3. Pull or build candidate images, validate Compose, and run the migration job once.
4. Start API and worker, wait for health, then expose traffic through nginx.
5. Run authentication, upload, job, and tenant-isolation smoke tests.

Application rollback uses the previously recorded image digests. Do not automatically
downgrade the database. Every release migration must be backward-compatible with the prior
application during the rollback window, or the release plan must include an independently
tested restore procedure and accepted data-loss boundary.
