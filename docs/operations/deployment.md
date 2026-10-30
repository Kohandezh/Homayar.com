# Deployment Runbook

## Scope

The infrastructure baseline exposes one origin through nginx:

- `/app/` routes to the Vite console and falls back to `/app/index.html` for client routes.
- `/api/v1/` routes unchanged to FastAPI. Both `/api/v1/uploads` and
  `/api/v1/uploads/` use the streaming 300 MB upload policy.
- `/healthz` reports nginx liveness without testing dependencies.
- `/readyz` proxies FastAPI's dependency-aware readiness response.
- Every other path, including `/`, answers `302` with a redirect to `/app/`.

The public website (homayar.com) is not part of a self-hosted install. It lives in
`website/` and is deployed to Cloudflare Workers (see `website/DEPLOY.md`).

The relay VPS and frp tunnel for the local production server: see [relay-tunnel.md](relay-tunnel.md).

`compose.onprem.yaml` runs PostgreSQL, Redis, MinIO, API, worker, the console web
service, and nginx. `compose.cloud.yaml` runs only application services and nginx; PostgreSQL,
Redis, and S3-compatible storage are externally managed. Neo4j is an optional overlay on
the isolated `graph` network and is never a transactional dependency.

## Artifact Contract

| Artifact | Build context | Template | Runtime contract |
| --- | --- | --- | --- |
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
5. The selected provider implementation is installed. Set `INSTALL_AI_LOCAL=true` when
   the on-premise image must contain the optional local Whisper dependency.

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
| `DATABASE_URL` | PostgreSQL SQLAlchemy DSN; URL-encode credentials. Production needs `?sslmode=require`, `verify-ca` or `verify-full` (see below) |
| `REDIS_URL` | Redis DSN; URL-encode credentials. Production needs `rediss://`, with the `ssl_cert_reqs` query parameter that Celery requires (for example `?ssl_cert_reqs=required`) |
| `JWT_SECRET` | Random signing secret, at least 32 characters |
| `TOKEN_PEPPER` | Independent random token pepper, at least 32 characters |
| `STORAGE_ACCESS_KEY` | Dedicated application object-store identity |
| `STORAGE_SECRET_KEY` | Dedicated application object-store credential |

The on-premise profile also requires `POSTGRES_DB`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `MINIO_ROOT_USER`, and
`MINIO_ROOT_PASSWORD`. `DATABASE_URL` and `REDIS_URL` must describe those same services;
they remain explicit because composing a URL from raw passwords is unsafe when credentials
contain reserved characters.

MinIO keys reach `mc` through the environment and a pipe, never as process arguments (HOM-340).
So `minio-bootstrap`, `backup-minio.sh` and `restore-minio.sh` refuse a `:`, a line break or a
carriage return in `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD`, and a line break or a carriage
return in `STORAGE_ACCESS_KEY` and `STORAGE_SECRET_KEY`. The bootstrap then stops with exit code 2,
and `api` and `worker` do not start. Keys made with `openssl rand -hex` never hit this. Save env
files with LF line ends, not CRLF.

Redis runs directly as the non-root UID/GID shipped by the pinned Alpine image
(`REDIS_UID=999`, `REDIS_GID=1000` by default). When replacing that image, verify its `redis`
identity and override both values before first start. Existing volumes created with different
ownership require an explicit, reviewed ownership migration while Redis is stopped.

The cloud profile additionally requires `STORAGE_BUCKET`, `STORAGE_REGION`, and immutable
`CONSOLE_WEB_IMAGE` and `API_IMAGE` references. The current storage
settings require static S3 credentials. Workload identity should replace them only after
the application supports a credential provider chain without requiring access keys.

### Secret references

Every process that loads the backend settings (API, worker, scheduler, migration job) accepts
each secret-bearing setting either as the plain variable `NAME` or as a reference `NAME_FILE`
whose value is the path of a file holding the secret (a secret-manager mount, a Docker or
Kubernetes secret, a systemd credential). The covered settings are `DATABASE_URL`,
`REDIS_URL`, `JWT_SECRET`, `TOKEN_PEPPER`, `GOOGLE_CLIENT_SECRET`, `ASANAK_API_KEY`,
`STORAGE_ACCESS_KEY`, `STORAGE_SECRET_KEY`, `PII_MAPPING_KEYS`, `WEBHOOK_SECRET_KEYS`,
`LLM_FALLBACK_API_KEY`, `ZAI_API_KEY`, `GAPGPT_API_KEY`, `SMTP_PASSWORD`, `NEO4J_PASSWORD`,
and `SENTRY_DSN`.
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

Production also refuses insecure transports (development, test, and staging are
unchanged):

- **Internal hops (HOM-161).** PostgreSQL, Redis, Neo4j (with the projection enabled) and object
  storage need TLS: an accepted `sslmode`, `rediss://`, `bolt+s://`/`neo4j+s://`, and `https://`.
  Cloud mode has no opt-out. On-premise, an explicit per-hop opt-out
  (`DATABASE_ALLOW_INSECURE_TRANSPORT`, `REDIS_ALLOW_INSECURE_TRANSPORT`,
  `NEO4J_ALLOW_INSECURE_TRANSPORT`, `STORAGE_ALLOW_INSECURE_ENDPOINT`) allows plaintext, but only
  to a server on the same host (loopback, a unix socket, or a single-label Compose service name).
  Each opt-out in use is logged at startup as `TRANSPORT_TLS_OPT_OUT` with hop names only. The
  matrix, the accepted forms and the residual risk are in
  [transport-policy.md](../security/transport-policy.md).

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
  - `STORAGE_ALLOW_INSECURE_ENDPOINT=true` (default `false`) lets production use an `http://`
    endpoint on the same host, but only together with `DEPLOYMENT_MODE=onprem`. With
    `DEPLOYMENT_MODE=cloud` the flag has no effect. Without the flag, or for another host,
    the endpoint is refused.
  - `compose.onprem.yaml` sets the flag because its bundled MinIO is reached at
    `http://minio:9000` on the internal compose network.
  - Residual risk: object traffic between the API or worker and MinIO, including
    recordings and credentials, is plaintext on that internal network. Keep MinIO off
    published ports and shared networks. Serving the bundled MinIO over TLS, which retires
    the flag, needs the certificate decision in HOM-196.
  - Ambient endpoint overrides never reach the S3 client: it is built with
    `ignore_configured_endpoint_urls`, so `AWS_ENDPOINT_URL`, `AWS_ENDPOINT_URL_S3` and an
    `endpoint_url` in the shared AWS config (profile or `services` section) are ignored and
    only `STORAGE_ENDPOINT_URL` (or the provider default) is used. Production with
    `STORAGE_BACKEND=s3` still refuses to start while any of them is set to something other
    than an `https://` URL, or when the shared AWS config cannot be read; the error names the
    source, never its value. `STORAGE_ALLOW_INSECURE_ENDPOINT` does not cover these overrides.
- **Recording delivery.** `STORAGE_PRESIGNED_DELIVERY` (default `true`) decides how the
  download, inline playback and share routes send recording bytes from S3 storage. `true`
  answers `307` to a presigned URL on the storage endpoint. `false` streams the bytes through
  the API, with `Range` served by a ranged `GetObject`. `compose.onprem.yaml` sets `false`: the
  bundled MinIO at `http://minio:9000` has no published port, so a browser cannot load a
  presigned URL for it. Local storage always streams. Design and bandwidth notes:
  [recording-delivery-on-prem.md](../architecture/recording-delivery-on-prem.md).

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
| `ASR_PROFILE` | empty | Named Whisper profile (HOM-39, ADR 0044). Empty keeps the `WHISPER_*` values above. A profile replaces `WHISPER_MODEL`, `WHISPER_DEVICE` and `WHISPER_COMPUTE_TYPE`, and needs `ASR_PROVIDER=local_whisper`. The profile list is in `docs/architecture/decisions/0044-asr-model-profiles-and-language-policy.md`. |
| `ASR_ALLOWED_LANGUAGES`, `ASR_DEFAULT_LANGUAGE`, `ASR_LANGUAGE_MIN_PROBABILITY` | `fa,en`, `fa`, `0.5` | Language policy for auto speech requests. A detected language outside the allowed list, or under the probability threshold, falls back to the default language. The default language must be in the allowed list. |
| `PII_MAPPING_KEYS`, `PII_MAPPING_ACTIVE_KEY_ID` (secret) | empty | Restricted PII mappings (ADR 0023); empty keeps redaction but retains no originals |
| `WEBHOOK_SECRET_KEYS` (secret), `WEBHOOK_SECRET_ACTIVE_KEY_ID` | empty | Keyring that encrypts webhook signing secrets (ADR 0050), same `key_id:base64(32 bytes)` format as `PII_MAPPING_KEYS`. Empty: no webhook endpoint can be created or rotated (503 `webhook_signing_unavailable`) and queued deliveries move to the dead letter with `signing_unavailable`. Rotate by adding a key and making it active; keep the old key while secrets encrypted with it exist. |
| `WEBHOOK_DELIVERY_TIMEOUT_SECONDS`, `WEBHOOK_SWEEP_INTERVAL_SECONDS`, `WEBHOOK_MAX_ENDPOINTS_PER_WORKSPACE`, `WEBHOOK_TEST_RATE_LIMIT_PER_HOUR`, `WEBHOOK_REDELIVER_RATE_LIMIT_PER_HOUR` | `10`, `30`, `20`, `30`, `60` | One webhook request's total timeout, the due-delivery sweeper period, the active endpoints allowed per workspace, the test events per workspace per hour, and the admin redelivery calls (one delivery or one bulk batch) per workspace per hour |
| `WEBHOOK_MAX_ATTEMPTS`, `WEBHOOK_RETRY_DELAYS_SECONDS` | `8`, `60,300,1800,7200,18000,36000,36000` | Webhook retries (ADR 0050 D9). A failed request is sent again after the delay for its attempt number (the last delay repeats); after the maximum attempts (1 to 20) the delivery moves to the dead letter. Delays are whole seconds, 1 to 604800, comma separated. The default spreads 8 attempts over about 27 hours. A bad value refuses to start. |
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
edits included. Sign-up grants the demo, and the plan catalog lists it, only while
`ENABLE_FREE_DEMO` is on and the demo plan is active.

Seats: the `seats` term of the plan version a workspace's subscription was granted under
limits its owner and admin memberships; members and viewers are free. The demo has one
seat, which its owner holds. A pending admin invitation reserves a seat; inviting,
accepting, promoting or a delegated installation beyond the limit answers 409
`seat_limit_reached`. A version without `seats`, or a workspace without a current
subscription, has no seat limit. Lowering a limit keeps the current admins and refuses
new ones. To give one tenant more seats, pin its subscription to a plan version that has them.

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

### Host preflight (`--host`)

`--host` adds checks of the machine and the configuration the stack really runs on (HOM-108).
Run it inside the api container, because the checks look at that container: its packages, its
GPU access and its network. The result is under `host` in the JSON report.

```bash
docker compose --file infra/compose/compose.onprem.yaml --profile onprem run --rm -T --no-deps api \
  python -m backend.jobs.model_preflight --host [--offline] [flags below]
```

| Flag | Meaning |
| --- | --- |
| `--host` | Run the host checks. Every flag below needs it, except `--offline`. It cannot be combined with `--egress-allowlist`. |
| `--min-free-gpu-mib N` | Free VRAM the target GPU needs. Default `4096`. |
| `--min-free-disk-mib N` | Free disk each checked path needs. Default `2048`. |
| `--tls-cert PATH` | A PEM certificate to check. `-` reads it from stdin. Pass only the certificate (for example `fullchain.pem`), never the key. |
| `--tls-host NAME` | The name the certificate must cover (SAN DNS names; `*.example.com` covers one extra label). |
| `--tls-min-days N` | Days the certificate must still be valid. Default `14`. |
| `--offline` | Works with or without `--host`. With `--host` it also checks the no-internet configuration. It always checks deployment profile O. |
| `--probe-internet` | Tries real outbound connections. Without it, no internet connection is tried. |
| `--probe-target HOST:PORT` | With `--probe-internet`: the targets to try (repeat the flag). It replaces the defaults `1.1.1.1:443`, `8.8.8.8:53` and `huggingface.co:443`. |
| `--topology NAME` | The supported topology to check for this run (ADR 0047). It wins over the `DEPLOYMENT_TOPOLOGY` setting. |

A wrong flag combination, a negative number or a bad `HOST:PORT` is a usage error (exit 2).

Each check has a `status`:

- `pass`: the check is fine.
- `fail`: a real problem. The report is not ready.
- `warn`: something to look at. It does not change the exit code.
- `skip`: the check does not apply here. `detail` says why. It does not change the exit code.

| Check | What it looks at |
| --- | --- |
| `asr_runtime` | Local Whisper only (else `skip`). `faster_whisper` is installed (the image is built with `INSTALL_AI_LOCAL=true`), CTranslate2 loads, it sees a CUDA device in GPU mode, and it supports the compute type. With `WHISPER_LOCAL_FILES_ONLY=true`: `WHISPER_MODEL_PATH` is a directory with `model.bin` (`warn` when the path is not set). It never loads a model. It reads the adapter the stack builds, so a named ASR profile is reported correctly. |
| `gpu_driver` | GPU mode only (device `cuda`, else `skip`). One `nvidia-smi` call. `fail` when `nvidia-smi` is missing or fails, or the driver is older than `525.60.13` (the CUDA 12 minimum). `warn` for a `float16` compute type on a GPU below compute capability 7.0 (Pascal, for example the P40): use `int8_float32` there. |
| `gpu_memory` | GPU mode only. Free VRAM of the GPU the model will use (by the device index and `CUDA_VISIBLE_DEVICES`). `fail` below `--min-free-gpu-mib`. Shared GPUs change all the time, so the number is a snapshot. |
| `disk` | Free space on the temp directory, `MODEL_ARTIFACT_ROOT`, `WHISPER_MODEL_PATH` and `STORAGE_LOCAL_ROOT` (each when it applies). `fail` when a path is missing or below `--min-free-disk-mib`. |
| `dependency_ports` | A TCP connection (2 seconds) to the database, Redis, S3 storage, `LOCAL_AI_BASE_URL` and the outbound proxy (`HTTPS_PROXY`/`HTTP_PROXY`), each when configured. `fail` when one does not connect. The report shows only the name, host and port, never a user name, password or URL. |
| `tls_certificate` | Only with `--tls-cert`. `fail` when the input holds a private key, holds no certificate, the certificate is expired or not valid yet, has fewer than `--tls-min-days` days left, or does not cover `--tls-host`. |
| `no_internet_config` | Only with `--offline`. `fail` for `WHISPER_LOCAL_FILES_ONLY` off (local Whisper), Google sign-in on, phone sign-in through a public SMS host, a public `SENTRY_DSN` host or a public `SMTP_HOST`. `warn` for `HF_HUB_OFFLINE` not `1` (local Whisper) and for an outbound proxy setting. Cloud AI endpoints are judged by profile O, not here. |
| `topology` | Only with a topology (`DEPLOYMENT_TOPOLOGY` or `--topology`, else `skip`). Compares the host and the settings with the topology's minimums (ADR 0047, D2). `fail` for too few CPUs, too little memory (host totals), too few NVIDIA GPUs or none with the required compute capability, an `ASR_PROFILE` the topology does not allow, a different `DEPLOYMENT_MODE`, or an unknown topology name. `warn` when a value cannot be read (CPU count, memory, nvidia-smi), when `ASR_PROFILE` is empty, when Google or phone sign-in is on, and, with `ENVIRONMENT=production`, when storage is not `s3` or `TOMBSTONE_JOURNAL_DIR` is empty. A `fail` wins over a `warn`. Free VRAM stays in `gpu_memory`. |
| `internet_probe` | Only with `--probe-internet`. A TCP connection (3 seconds) to each target. `fail` when any target is reachable, because then the host is not offline. It tries direct TCP connections only, not through `HTTPS_PROXY`/`HTTP_PROXY`. When a proxy is set, `no_internet_config` warns about it, so `internet_probe: pass` does not prove there is no outbound path through the proxy. |

`DEPLOYMENT_TOPOLOGY` (empty by default) names a supported topology: `single-host-p40` (the
reference) or `single-host-cpu` (ADR 0047). Empty means no topology check and no other change.
An unknown name stops the settings from loading. `compose.onprem.yaml` passes it in the shared
app environment, next to `ASR_PROFILE` (HOM-343). Example for the reference shape, with the
topology's free VRAM minimum:

```bash
DEPLOYMENT_TOPOLOGY=single-host-p40 ASR_PROFILE=gpu-p40-large-v3 \
  docker compose --file infra/compose/compose.onprem.yaml --profile onprem run --rm -T --no-deps api \
  python -m backend.jobs.model_preflight --host --min-free-gpu-mib 4096
```

Exit codes keep their meaning: `0` ready, `1` a stage or a host check failed, `2` a
configuration, manifest or usage error. `--register` still runs only after exit `0`.

With `--host`, a configuration or manifest error (exit 2) still prints the host checks under
`host`, next to `error`. This helps on a server where the providers are not configured yet.
The exit code stays 2. When the full provider bundle cannot be built, the checks use the ASR
adapter alone. When even that cannot be built, `asr_runtime`, `gpu_driver` and `gpu_memory` are
`skip`, and the other checks still run.

## Network Egress

Application-level permits (ADR 0031) refuse any provider request no routing decision
covers; the network layer below makes sure a process that bypasses them has nowhere to go.

On-premise (`compose.onprem.yaml`):

| Network | Internal | Members |
| --- | --- | --- |
| `edge` | no | `proxy` (nginx) only: the published port |
| `ingress` | yes | `proxy`, `console-web`, `api` |
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
`scheduler` and `console-web` containers as Compose attaches them. It is a
container-network test on one Docker host, not a validation of a production deployment.

## Image Policy

The base images of the built images (`PYTHON_IMAGE`, `NODE_IMAGE`, `NGINX_IMAGE`,
`ALPINE_IMAGE`) are pinned by digest in the Dockerfiles and in the Compose build-argument
defaults. Do not override them for a release; re-pin them on purpose
(`docs/operations/release.md`). The release manifest records these digests. The runtime
`proxy` service also reads `NGINX_IMAGE`, and its default is the same digest pin
(`nginx:1.30-alpine-slim@sha256:dede88…`, HOM-345, re-pinned to the slim variant in
HOM-350); a value you set changes both the proxy and
the console-web build. The release manifest records the proxy default under `bundled_images`.

The state images (`POSTGRES_IMAGE`, `REDIS_IMAGE`, `NEO4J_IMAGE`) still default to tags.
Production deployments must override them with reviewed digest references and record the
digests with the release.

`MINIO_IMAGE` and `MINIO_MC_IMAGE` default to the Bitnami legacy references
`bitnamilegacy/minio:2025.4.22@sha256:50cec18…` and
`bitnamilegacy/minio-client:2025.4.16@sha256:8a86e44…` (tag and short digest), the same
images CI's storage contract runs. They are public: `docker compose ... --profile onprem
pull` needs no registry login. The official `quay.io/minio` images now need a login and
their Docker Hub copies are gone. **These images get no more updates (no security fixes);
a later ticket may move them to our own registry.** Do not override them for a release;
the release manifest records both digests under `bundled_images`, and `verify` refuses a
manifest where one is not pinned.

For an existing install: the image runs as uid 1001 and keeps its data in
`/bitnami/minio/data`. A `minio_data` volume written by the old image, which ran as
root, is owned by root. Give it to uid 1001 before the first start with the new image:

```bash
docker run --rm --user 0 --volume <project>_minio_data:/v <MINIO_IMAGE reference> chown -R 1001:0 /v
```

`<project>` is the Compose project name, `homayar-onprem` by default (line 1 of the
Compose file, `name: homayar-onprem`), so the volume is usually
`homayar-onprem_minio_data`; `docker volume ls` shows the real name.

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

The production server for `app.homayar.com` (one shared host behind a host nginx TLS front) has its own runbook: [app-server.md](app-server.md). Its relay part is in [relay-tunnel.md](relay-tunnel.md).

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
development environment. Production cloud refuses the overlay's plaintext `bolt://neo4j:7687`
(HOM-161): use a managed or separately operated Neo4j endpoint over `bolt+s://` or
`neo4j+s://` instead of coupling graph storage to an application host:

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

**Mandatory internal TLS (HOM-161).** From this release, production refuses to start with
a plaintext internal hop. Before upgrading:

- Installs that use the bundled `compose.onprem.yaml` need no action. The profile sets the
  per-hop opt-outs, and `DATABASE_URL`/`REDIS_URL` already name the `postgres` and `redis`
  services. Check that they do.
- Other on-premise installs either move each hop to TLS, or set its opt-out
  (`DATABASE_ALLOW_INSECURE_TRANSPORT`, `REDIS_ALLOW_INSECURE_TRANSPORT`,
  `NEO4J_ALLOW_INSECURE_TRANSPORT`, `STORAGE_ALLOW_INSECURE_ENDPOINT`) for a server on the same
  host. An existing `STORAGE_ALLOW_INSECURE_ENDPOINT=true` with an `http://` endpoint on another
  host is now refused: serve that endpoint over TLS.
- Cloud installs need `DATABASE_URL` with `sslmode=require`, `verify-ca` or `verify-full`, a
  `rediss://` `REDIS_URL` (with `ssl_cert_reqs`), and `bolt+s://`/`neo4j+s://` when the Neo4j
  projection is enabled.
- After the upgrade, check the API, worker and scheduler logs for `TRANSPORT_TLS_OPT_OUT`. It
  lists every hop that still runs without TLS.

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
