# ADR 0047: Private topologies, capacity envelope, local identity and storage

Date: 2026-10-30 · Status: Accepted 2026-10-30, owner sign-off 2026-10-30 (HOM-106, WP19-T01, parent HOM-23, features F74, F75; recorded by HOM-362) · Builds on: ADR 0019, ADR 0026, ADR 0031, ADR 0035, ADR 0044

## Owner sign-off (2026-10-30)

The owner approved all 7 points on 2026-10-30 (owner sign-off 2026-10-30). The status is
Accepted. The recommendations in D1 to D5 below are the decisions.

1. **Which private installs do we support?** Approved: two only, `single-host-p40` (the
   reference, the shape of the app server) and `single-host-cpu`. Both are one Linux host with
   Docker Compose. No Kubernetes, no second host, no high availability.
2. **May Homayar use one P40 and more RAM on the shared app server for HOM-42?** Approved: one
   P40 with at least 4096 MiB free VRAM for speech to text, and the worker memory limit raised
   from 2048 MiB to 4096 MiB (Homayar total from 6016 MiB to 8064 MiB). Padyar keeps the rest.
3. **Where does the local language model run?** Approved: in a customer install, on one
   operator-provided model host with its own GPU, on the private network, reached through
   `LOCAL_AI_BASE_URL`. An install that needs local summary, QA and search needs a second
   machine. Not on the shared app server until a GPU is agreed with Padyar. The model is picked
   by a follow-up ticket.
4. **Do we ship MinIO in the first offline release?** Approved: yes, the pinned image we ship
   today, despite the two known risks: the upstream project is archived and gets no fixes, and
   its licence is AGPL-3.0. HOM-340 must be fixed before uploads go live. The fallback is the
   local file storage backend: it exists and passes the storage contract suite, and a switch to
   it would rework HOM-110's backup steps.
5. **How do users sign in on an offline install?** Approved: built-in e-mail and password
   accounts, with the first owner account as the break-glass account. No bundled Keycloak. The
   customer's own IdP comes later through OIDC (HOM-127).
6. **May we publish the capacity numbers in this ADR?** Approved: no. They are planned, not
   measured, until HOM-42, HOM-41 and HOM-111 measure them.
7. **Redis licence.** Approved: we keep `redis:7.4-alpine`, which is under RSALv2 or SSPLv1,
   not an open source licence. It stays for now. HOM-107 decides between Redis 8 (adds
   AGPL-3.0) and Valkey (BSD-3-Clause) when it writes the licence notices.

## Context

- **The app server.** One host: Ubuntu 24.04, 36 cores, 27 GiB RAM, Docker 29.1.3, Compose
  2.40.3. It is shared with live Padyar services and a GitHub Actions runner
  (`docs/operations/app-server.md:42`, "Hosts").
- **Homayar's budget there.** 6016 MiB and 16.5 of 36 CPUs in limits. The worker has 2048 MiB.
  The stack uses about 1018 MiB when idle. Host `MemAvailable` was about 14 GB with the stack
  idle (`docs/operations/app-server.md:228-243`, "Memory and CPU Budget"). The same section says
  "Local Whisper would need much more worker memory" and that the Padyar services need the rest
  of the RAM (`docs/operations/app-server.md:245-246`).
- **GPUs.** Two Tesla P40, 24 GB each, Pascal, compute capability 6.1
  (`docs/architecture/decisions/0044-asr-model-profiles-and-language-policy.md:24-25`). They are
  shared with other services, so free VRAM changes over time (`docs/operations/app-server.md:650-653`).
  ADR 0044 decision 2: use `int8_float32`, never `float16`, on a P40
  (`docs/architecture/decisions/0044-asr-model-profiles-and-language-policy.md:60-84`).
- **ASR profiles.** `cpu-base`, `cpu-small`, `cpu-medium`, `cpu-large-v3-turbo`, `cpu-large-v3`
  and `gpu-p40-large-v3` (`Homayar/backend/providers/asr_profiles.py:34-80`). No profile is
  qualified. Production sets none (ADR 0044, "Product rules", P1, line 153). The app server image
  has no CUDA, so only the `cpu-*` profiles can work there (`docs/operations/app-server.md:604`).
- **Host preflight (HOM-108).** `model_preflight --host` checks the ASR runtime, the NVIDIA
  driver, free VRAM, disk, dependency ports, a TLS certificate, the no-internet configuration
  and an optional outbound probe (`Homayar/backend/providers/host_checks.py:37-46`,
  `docs/operations/deployment.md`, "Host preflight (`--host`)"). `--offline` also evaluates
  deployment profile O (`Homayar/backend/jobs/model_preflight.py:18-22`,
  `Homayar/backend/providers/offline_profile.py:1-17`).
- **Manifests.** A manifest declares `endpoint_kind` (`in_process`, `private`, `cloud`)
  (`docs/architecture/decisions/0026-model-manifests-and-preflight.md:22-23`).
- **Egress.** The air-gapped default is no egress proxy and no outbound path. A local model
  server counts as private only through an operator manifest
  (`docs/architecture/decisions/0031-egress-denial.md:58-71`, `infra/egress/allowlist.txt`).
- **Storage.** The on-prem Compose file sets `STORAGE_BACKEND: s3` at the bundled MinIO
  (`infra/compose/compose.onprem.yaml:43-45`), pinned to `bitnamilegacy/minio:2025.4.22` by
  digest (`infra/compose/compose.onprem.yaml:515-522`, HOM-286 commit `68c298f`). The storage
  contract suite runs against MinIO in CI (ADR 0019, "Amendment (2026-10-23, HOM-36)"). HOM-340
  is open: MinIO keys on the `mc` command line and download links to `http://minio:9000`
  (`docs/operations/app-server.md:671-689`, items O1 and O7).
- **Backups.** Encrypted, snapshot-consistent sets (ADR 0035). PostgreSQL and MinIO are backed
  up. Redis is not. The tombstone journal is replicated separately
  (`docs/operations/backup-restore.md:12-15`). The off-site target is an S3 bucket through the
  egress proxy or a mounted directory (`docs/operations/backup-restore.md:64-68`).
- **Identity today.** E-mail and password, phone and OTP through Asanak SMS, and Google OIDC
  (`docs/architecture/auth-session-contract.md:16-22`). Uploads need a verified e-mail or phone
  (`Homayar/backend/routers/v1/meetings.py:229`). The blueprint asks for customer OIDC/AD
  through a broker, a controlled break-glass account, and no mandatory Google login or SMS
  (`docs/planning/homayar-commercial-blueprint.md:458`). It says to qualify Keycloak only when
  needed and not to write SAML ourselves (`docs/planning/homayar-commercial-blueprint.md:615`,
  `docs/planning/homayar-commercial-blueprint.md:948`).
- **Settings.** `deployment_mode: Literal["cloud", "onprem"]` (`Homayar/backend/config.py:431`).
  `ENABLE_GOOGLE_AUTH` and `ENABLE_PHONE_AUTH` default to `false` in the on-prem Compose file
  (`infra/compose/compose.onprem.yaml:108`, `infra/compose/compose.onprem.yaml:112`).
- **Language model.** Only through settings: `LOCAL_AI_BASE_URL` (a private endpoint, no key)
  or a cloud key. There is no local model server in the stack
  (`docs/operations/app-server.md:605`, `infra/compose/compose.onprem.yaml:84`).
- **The goal.** F74: "Fresh qualified installation with outbound network denied performs login,
  30-minute Persian processing, local summary/QA/search, export and deletion; no hidden
  downloads/cloud calls; actual hardware/profile recorded"
  (`docs/planning/homayar-commercial-blueprint.md:1574`).

## Decision

### D1 Supported topologies

**Recommendation:** support two single-host Docker Compose topologies, `single-host-p40`
(reference) and `single-host-cpu` (supported). Everything else is "not supported yet".

| Topology | Status | Hosts | CPU / GPU | ASR profiles | PostgreSQL, Redis, objects | Language model | Compose |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `single-host-p40` | reference | 1 Linux x86-64 host | at least 1 NVIDIA GPU, compute capability 6.1 (Tesla P40) | `gpu-p40-large-v3` | bundled containers on the same host: `postgres:17-alpine`, `redis:7.4-alpine`, MinIO | private OpenAI-compatible endpoint on one operator-provided model host (D5) | `compose.onprem.yaml`, profile `onprem`; profile `egress` only when a customer allows an outbound path |
| `single-host-cpu` | supported | 1 Linux x86-64 host | no GPU | the five `cpu-*` profiles | same | same | same |

Both use `DEPLOYMENT_MODE=onprem`, which the Compose file already sets
(`infra/compose/compose.onprem.yaml:11`). The app server is the reference shape of
`single-host-p40`. Its own overlay (`compose.onprem.app-server.yaml`) only adds limits and ports.

Why these two:

- They are what the repository already runs. The Compose file, the profiles, the preflight and
  the backup tools all assume one host. No new install path is needed.
- The P40 topology allows only the P40 profile. That profile is the only one whose compute type
  is right for compute capability 6.1 (ADR 0044 decision 2). A CPU profile on a GPU host is the
  CPU topology on that host.
- The CPU topology is the fallback when a customer has no GPU. The blueprint calls a CPU fallback
  "an explicit low-throughput profile", not a promise of GPU speed
  (`docs/planning/homayar-commercial-blueprint.md:456`).

Not supported yet (HOM-110 and HOM-111 must not plan for them):

- Kubernetes, Docker Swarm or any other orchestrator. The blueprint starts with Compose and adds
  Kubernetes only on proven need (`docs/planning/homayar-commercial-blueprint.md:448`).
- More than one host (except the one model host of D5), a separate GPU host for ASR, replicas,
  failover or any high availability.
- GPUs other than compute capability 6.1, for example Volta, Turing or Ampere cards. They can
  run faster compute types, but no profile or manifest exists for them.
- `float16` or plain `int8` on a Pascal GPU (ADR 0044 decision 2).
- ARM64 hosts, and macOS or Windows hosts (development only).
- An external or managed PostgreSQL, Redis or S3 service for a private install. The settings
  allow it, but backups and restore drills cover only the bundled services.
- The host PostgreSQL on the app server (`127.0.0.1:5432`). It belongs to other services
  (`docs/operations/app-server.md:47`).
- A language model or embedding server inside the Homayar Compose stack (D5).
- Speaker diarization on the GPU: there is no diarization provider yet (WP10).

Alternatives:

- **One topology only (the P40 reference).** Simpler, but a customer without a P40 has nothing.
  The CPU path costs no new code: the profiles and the preflight already exist.
- **Do nothing (no catalog, only `DEPLOYMENT_MODE=onprem` and the existing overlay).** Loses:
  an operator cannot tell before HOM-111 whether a host is big enough or has the right GPU, and
  HOM-110 has no named shape to install.
- **A generic "any NVIDIA GPU" topology.** Loses: no profile, compute type or manifest exists
  for other GPUs, so preflight could not check anything real.
- **Kubernetes or a two-host split now.** Loses on the speed rule: new manifests, new network
  rules and new backup paths, with no customer asking yet.

### D2 Capacity envelope

**Recommendation:** use the minimums below as the checkable floor, keep Homayar's own budget on
a shared host, and promise no work envelope until it is measured. Every number below that is
not marked "measured" is **planned, not measured**.

Catalog values (the code in `Homayar/backend/providers/deployment_topologies.py` must match this
table; a test fails when they drift):

| Topology | Status | Mode | Min host CPUs | Min host memory (MiB) | Min GPUs | Compute capability | Min free VRAM (MiB) | Allowed ASR profiles |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `single-host-p40` | reference | `onprem` | 16 | 24576 | 1 | 6.1 | 4096 | `gpu-p40-large-v3` |
| `single-host-cpu` | supported | `onprem` | 16 | 24576 | 0 | any | 0 | `cpu-base`, `cpu-small`, `cpu-medium`, `cpu-large-v3-turbo`, `cpu-large-v3` |

How to read it:

- CPUs and memory are **host totals**, because that is what a container can read. They are a
  floor, not a promise of speed. 16 CPUs matches the blueprint's pilot proposal
  (`docs/planning/homayar-commercial-blueprint.md:456`). 24576 MiB leaves a margin under the app
  server's 27 GiB, so the reference host passes. Planned, not measured: HOM-111 measures a real
  install, HOM-42 the GPU run.
- The minimum free VRAM equals the existing preflight default `4096`
  (`Homayar/backend/providers/host_checks.py:49`). Planned, not measured: HOM-42 measures what
  `large-v3` with `int8_float32` really needs on a P40.
- Disk: at least 100 GiB free for images, model weights, the database, objects and backups at
  install time, plus growth from recordings. Planned, not measured: HOM-111 measures it. The
  `disk` check already checks free space per path (`--min-free-disk-mib`).

Homayar's memory and CPU budget per service. "Today" is measured on the app server
(`docs/operations/app-server.md:228-238`, 2026-10-29). "Planned" is not measured.

| Service | Memory today | CPUs today | Planned `single-host-p40` | Planned `single-host-cpu` | Measured by |
| --- | --- | --- | --- | --- | --- |
| proxy | 256 MiB | 2 | same | same | HOM-111 |
| console-web | 128 MiB | 1 | same | same | HOM-111 |
| api | 1280 MiB | 4 | same | same | HOM-111 |
| worker | 2048 MiB | 4 | 4096 MiB, 4 CPUs | 6144 MiB, 8 CPUs | HOM-42 (GPU), HOM-41 (CPU, concurrency) |
| scheduler | 256 MiB | 0.5 | same | same | HOM-111 |
| postgres | 1024 MiB | 2 | same | same | HOM-111 |
| redis | 256 MiB | 1 | same | same | HOM-111 |
| minio | 768 MiB | 2 | same | same | HOM-111 |
| **Total** | **6016 MiB**, 16.5 CPUs | | **8064 MiB**, 16.5 CPUs | **10112 MiB**, 20.5 CPUs | |

On the shared app server, Homayar takes only its budget. The Padyar services and the runner keep
the rest of the RAM and share the GPUs (`docs/operations/app-server.md:245-246`). Any raise
goes to homlead first (owner sign-off item 2).

Work envelope (what we would promise a customer). All planned, not measured:

| Item | `single-host-p40` | `single-host-cpu` | Measured by |
| --- | --- | --- | --- |
| Processing jobs at the same time | 1 ASR job on the GPU | 1 ASR job | HOM-41 (concurrency, warm workers) |
| Audio hours per day | not promised yet | not promised yet | HOM-42 (P40 real-time factor), HOM-41 (CPU) |
| Longest single recording | 4 hours, the current setting limit `MAX_AUDIO_DURATION_SECONDS=14400` (`Homayar/backend/config.py:506`); a 30-minute call is the F74 test | same | HOM-111 (the 30-minute F74 run) |
| Console users at the same time | not promised yet | not promised yet | HOM-111 |

Why no hours-per-day number: ADR 0044 has no GPU run and no measured real-time factor on the
production hardware (`docs/architecture/decisions/0044-asr-model-profiles-and-language-policy.md`,
"Limits"). A number now would be a guess.

Alternatives:

- **Take the blueprint's pilot host (16 cores, 64 GB) as the minimum.** Loses: the reference
  host has 27 GiB and could never pass its own topology.
- **Set the minimums from today's 6016 MiB budget alone.** Loses: that budget runs no speech to
  text (`docs/operations/app-server.md:245`).

### D3 Local identity (IdP)

**Recommendation:** an offline install uses the built-in Homayar accounts (e-mail and password).
The first owner account is the break-glass account. Google sign-in and phone sign-in stay off.
A customer IdP comes later through OIDC (HOM-127, WP23). No IdP is bundled.

- **Built-in accounts.** They work with outbound network denied: passwords are Argon2 hashes in
  PostgreSQL (`docs/architecture/auth-session-contract.md:20`). The first owner registers in the
  console, as on the app server (`docs/operations/app-server.md`, "First Owner Account").
- **Break-glass account.** The owner account created at install. Its password goes into the
  customer's secret store, and it stays in use when a customer IdP is added later (HOM-127 keeps
  one local account working).
- **E-mail verification.** Uploads need a verified e-mail or phone
  (`Homayar/backend/routers/v1/meetings.py:229`). Offline, phone is off, so verification needs a
  mail relay on the customer network (`SMTP_HOST` on a local host). The `--offline` check already
  accepts a local `SMTP_HOST` (`Homayar/backend/providers/host_checks.py:669-670`). An install
  without any mail relay cannot upload. That is an open item for HOM-110 (see "Limits").
- **Off in the offline profile.** Google sign-in needs Google. Phone sign-in sends SMS through
  Asanak on the internet. Both default to off in `compose.onprem.yaml` (lines 108 and 112). They
  are checked in two places:
  - `--offline` (`no_internet_config`): `fail` for Google sign-in on, and for phone sign-in
    through a public SMS host (`Homayar/backend/providers/host_checks.py:659-664`).
  - The new `topology` check: `warn` for either one. A warning, not a failure, because the
    reference shape is also the hosted app server, which may use SMS sign-in later. Outbound
    denial is declared with `--offline`, and there it is a failure.
  This PR does not change auth code.

Alternatives:

- **Bundle Keycloak as the local IdP.** Keycloak is Apache-2.0
  (https://github.com/keycloak/keycloak/blob/main/LICENSE.txt, read 2026-10-30). It loses for
  now: one more Java service, database, upgrade path and admin burden
  (`docs/planning/homayar-commercial-blueprint.md:975`), and no customer has asked for it yet.
  HOM-127 may still pick it as the broker when a customer IdP needs one.
- **Customer IdP through OIDC as the only login.** Loses: it needs HOM-127 first, and the
  blueprint keeps a controlled break-glass account anyway (`docs/planning/homayar-commercial-blueprint.md:458`).
- **LDAP directly.** Loses: the blueprint routes LDAP/AD through a broker, not new code
  (`docs/planning/homayar-commercial-blueprint.md:617`).

### D4 Storage

**Recommendation:** keep the bundled services as shipped: MinIO for objects, PostgreSQL 17 and
Redis 7.4 as containers on Docker volumes of the same host, the tombstone journal on its own
mount, and backups to another disk plus an off-site directory. Fix HOM-340 before uploads go
live. Track the MinIO and Redis licence and upstream risks as owner items.

| Data | Where it lives | Backed up | Source |
| --- | --- | --- | --- |
| PostgreSQL (source of truth) | container `postgres:17-alpine`, volume `postgres_data` | yes (ADR 0035) | `infra/compose/compose.onprem.yaml:461`, `infra/compose/compose.onprem.yaml:469` |
| Objects (recordings, derived media, stage artifacts) | MinIO, volume `minio_data` | yes (ADR 0035) | `infra/compose/compose.onprem.yaml:522`, `infra/compose/compose.onprem.yaml:529` |
| Redis (broker, locks, cache) | container `redis:7.4-alpine`, volume `redis_data` | no, ephemeral | `infra/compose/compose.onprem.yaml:486`, `docs/operations/backup-restore.md:12-13` |
| Tombstone journal | `TOMBSTONE_JOURNAL_DIR=/app/tombstones`, bound to `TOMBSTONE_JOURNAL_PATH` | replicated separately, never in a backup | `infra/compose/compose.onprem.yaml:55-58`, `docs/operations/backup-restore.md:13-15` |
| Backups | `BACKUP_ROOT` on another disk; off-site: `BACKUP_OFFSITE_PATH` (a mounted directory) when outbound is denied | | `docs/operations/backup-restore.md:60-68` |

MinIO facts and risks:

- The upstream repository "was archived by the owner on Apr 25, 2026" and says "THIS REPOSITORY
  IS NO LONGER MAINTAINED". The community edition "is now distributed as source code only"
  (https://github.com/minio/minio, read 2026-10-30).
- The licence is "GNU AFFERO GENERAL PUBLIC LICENSE Version 3"
  (https://github.com/minio/minio/blob/master/LICENSE, read 2026-10-30). We ship the unchanged
  image as its own container. What AGPL-3.0 asks of us when we distribute it is a question for
  HOM-107 and its licence notices: not verified here.
- We use the Bitnami legacy copy. The repository says these images "get no more updates"
  (`infra/compose/compose.onprem.yaml:515-518`). The Bitnami page I read says nothing about
  updates for `bitnamilegacy` (https://github.com/bitnami/containers, read 2026-10-30): not
  verified upstream.
- HOM-340 (keys on the `mc` command line, download links to `http://minio:9000`) blocks uploads
  going live (`docs/operations/app-server.md:687-689`).

Redis licence: Redis Community Edition 7.4 is under "RSALv2 or SSPLv1". Redis 8.0 and later add
AGPLv3 as an option (https://redis.io/legal/licenses/, read 2026-10-30). Valkey is "BSD
3-Clause" (https://github.com/valkey-io/valkey/blob/unstable/COPYING, read 2026-10-30). This ADR
does not change the broker (owner sign-off item 7).

The topology check warns, in `ENVIRONMENT=production` only, when `STORAGE_BACKEND` is not `s3`
or `TOMBSTONE_JOURNAL_DIR` is empty. The `compose.onprem.yaml` defaults pass. Development and
test keep the local backend without a warning.

Alternatives:

- **The local file storage backend (`STORAGE_BACKEND=local`).** It exists and passes the same
  contract suite (ADR 0019). No MinIO risk. It loses for now: the Compose file, the backups
  runbook and the restore drill are built around MinIO (`docs/operations/backup-restore.md:12`),
  so a switch reworks HOM-110. It is the fallback named in owner item 4.
- **Another S3-compatible server.** Not evaluated here. A replacement needs the contract suite
  and the restore drill again.
- **The customer's own S3 or PostgreSQL.** Not supported yet (D1): backups and drills do not
  cover them.

### D5 Local language model and embeddings placement

**Recommendation:** the language model and embeddings run on a private OpenAI-compatible server
outside the Homayar Compose stack, on one operator-provided model host. Homayar reaches it
through `LOCAL_AI_BASE_URL` and an operator manifest with `endpoint_kind: private`. This ADR
picks no model and no server.

Where it runs in a customer install: on one model host with its own GPU, on the customer's
private network. D1 allows this one extra host for this one purpose. Why not the Homayar host:
the blueprint warns that "a 24 GB card may not hold all models concurrently"
(`docs/planning/homayar-commercial-blueprint.md:456`), the ASR GPU must keep its free VRAM, and
the model size is not known yet, so the D2 minimums of the Homayar host would be a guess. The
model host's CPU, RAM and VRAM are not in the D2 minimums: planned, not measured. The follow-up
model ticket (see "Deferred") measures them, and HOM-111 records them on the full install. The
F74 summary, QA and search steps cannot pass until that ticket has picked and qualified the
model.

- **The contract.** `INTELLIGENCE_PROVIDER=openai_compatible` with `LOCAL_AI_BASE_URL`
  (`docs/operations/app-server.md:605`). Profile O accepts a `private` endpoint on a loopback,
  private or internal host (`Homayar/backend/providers/offline_profile.py:9-10`). Without a
  manifest the endpoint counts as `cloud` (`docs/architecture/decisions/0031-egress-denial.md:69-70`).
  The `dependency_ports` check already connects to `LOCAL_AI_BASE_URL`.
- **What a P40 can run** (compute capability 6.1):
  - vLLM needs "compute capability 7.5 or higher"
    (https://docs.vllm.ai/en/stable/getting_started/installation/gpu/, read 2026-10-30). It does
    not run on a P40.
  - Ollama "supports Nvidia GPUs with compute capability 5.0+", lists the Tesla P40 under 6.1,
    and needs driver 570 or newer for 5.0 to 6.2
    (https://github.com/ollama/ollama/blob/main/docs/gpu.mdx, read 2026-10-30). The app
    server's driver version is not recorded in the repository: not verified.
  - llama.cpp's `llama-server` offers "OpenAI API compatible chat completions, responses, and
    embeddings routes" (https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md,
    read 2026-10-30). Its build docs state no minimum compute capability
    (https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md, read 2026-10-30): P40
    support is not verified there.
- **Placement on the reference host.** Not on the shared app server until the owner agrees a GPU
  with Padyar (owner sign-off item 3). A P40 has 24 GB, and the blueprint warns that "a 24 GB
  card may not hold all models concurrently" (`docs/planning/homayar-commercial-blueprint.md:456`).
- **Who picks the model.** No ticket picks or qualifies the local language model or the
  embedding model yet. Model qualification belongs to WP08 and WP13
  (`docs/planning/work-packages/WP08/README.md:3`). HOM-93 (the embedding index) will need the
  embedding model. The pick is a follow-up ticket (see "Deferred").

Alternatives:

- **A model server inside the Compose stack.** Loses: it ties the GPU, the image and the
  upgrade of a model server to every Homayar release, and HOM-107 would bundle its weights before
  a model is picked.
- **Pick llama.cpp or Ollama now.** Loses: no measurement on a P40 exists in the repository, and
  the model choice drives the server choice.

## The hooks

They make D1 and D2 checkable. They are opt-in: with `DEPLOYMENT_TOPOLOGY` empty, nothing
changes.

- **Catalog:** `Homayar/backend/providers/deployment_topologies.py`, `TOPOLOGIES` and
  `get_topology(name)`. The values are the D2 table.
- **Setting:** `DEPLOYMENT_TOPOLOGY` (default empty). An unknown name stops the settings from
  loading, and the message lists the valid names. Spaces around the name are removed.
- **Host check `topology`:** the last check of `model_preflight --host`. `--topology NAME`
  chooses a topology for one run and wins over the setting.
  - `skip`: no topology is chosen.
  - `fail`: the host has fewer CPUs or less memory than the minimum, fewer NVIDIA GPUs than
    needed (nvidia-smi runs and lists too few), no GPU with the required compute capability,
    an ASR profile outside the allowed list, a different `DEPLOYMENT_MODE`, or an unknown
    `--topology` name. `detail` names every failed requirement. A `fail` makes the report not
    ready (exit 1), like the other host checks.
  - `warn`: a value cannot be read (CPU count, memory, nvidia-smi missing, timed out or failing,
    an unreadable compute capability), `ASR_PROFILE` is empty (the `WHISPER_*` settings cannot
    be checked against the list), Google or phone sign-in is on, or, in production, storage is
    not S3 or the tombstone journal directory is empty. A `fail` wins over a `warn`.
  - `pass`: everything is met.
  The check reads GPUs with the same nvidia-smi query as `gpu_driver`, once per run, and only for
  a topology that needs a GPU. Free VRAM stays in `gpu_memory`: run it with
  `--min-free-gpu-mib` set to the topology's minimum.
- **Compose:** `compose.onprem.yaml` passes `DEPLOYMENT_TOPOLOGY` in the shared
  `x-app-environment` block, empty by default, next to `ASR_PROFILE`. HOM-343 added
  `ASR_PROFILE` there; this change does not.

Run it on the host the stack runs on:

```bash
DEPLOYMENT_TOPOLOGY=single-host-p40 ASR_PROFILE=gpu-p40-large-v3 \
  docker compose --file infra/compose/compose.onprem.yaml --profile onprem run --rm -T --no-deps api \
  python -m backend.jobs.model_preflight --host --offline --min-free-gpu-mib 4096
```

## Consequences

- **HOM-107 (signed offline bundles):** bundle the images of one topology at a time; the model
  weights are the `gpu-p40-large-v3` or `cpu-*` profile weights; write the MinIO (AGPL-3.0) and
  Redis (RSALv2/SSPLv1) notices and settle owner item 7.
- **HOM-109 (offline licence):** licence per installation, not per GPU (blueprint line 460); the
  topology name goes into the licence and the support bundle.
- **HOM-110 (install and upgrade):** two install paths only; set `DEPLOYMENT_TOPOLOGY` and run
  the preflight above as an install step; plan a local mail relay for e-mail verification.
- **HOM-111 (offline install test):** after the follow-up model ticket has picked and qualified
  the local language model (D5), run F74 on one of the two topologies plus the D5 model host
  (before that, the summary, QA and search steps cannot pass), record the
  `topology` check output as "actual hardware/profile recorded", and measure the D2 planned
  numbers.
- **HOM-112 (upgrade rehearsal):** rehearse on the same topology as HOM-111.
- **HOM-127 (customer OIDC/SAML):** keep the built-in owner account as break-glass; choose a
  broker (Keycloak or none) then.
- **HOM-42 (GPU image and P40 run):** pass `ASR_PROFILE=gpu-p40-large-v3` and
  `DEPLOYMENT_TOPOLOGY=single-host-p40`, measure free VRAM, worker memory and the real-time
  factor, and update the D2 table.
- **HOM-41 (warm workers and concurrency):** set the number of ASR jobs at the same time and the
  CPU topology's worker budget.
- **Rollback:** the hooks are opt-in. A revert of this PR removes them. Nothing is migrated.

## Limits

- No D2 number except the "today" budget is measured. None may be promised to a customer yet.
- The memory check reads the host total, not the memory still free. On a shared host the
  operator must also look at `MemAvailable` before raising Homayar's limits.
- An install without a local mail relay cannot verify e-mail, so uploads stay blocked. No
  operator command marks an account verified today. HOM-110 must provide a relay or a reviewed
  admin step.
- The topology check counts GPUs as nvidia-smi lists them inside the container. It does not
  check that `CUDA_VISIBLE_DEVICES` points at a matching GPU; `gpu_memory` does that for the
  ASR runtime.
- The MinIO risk (archived upstream, AGPL-3.0) is accepted for the first offline release
  (owner item 4).
- The Redis licence choice stays open until HOM-107 decides between Redis 8 and Valkey
  (owner item 7).
- The D5 model host's CPU, RAM and VRAM are not in the D2 minimums and not checked by the
  `topology` check. Planned, not measured: the follow-up model ticket measures them. Until the
  model is picked and qualified, the F74 summary, QA and search steps cannot pass.
- Failure modes per component (GPU, MinIO, PostgreSQL disk, the model endpoint), moving between
  topologies (for example from the local storage backend to MinIO) and lock-in costs are not
  analysed here. They are left to HOM-110 and HOM-111.

## Deferred

- Kubernetes, multi-host and high availability topologies.
- Profiles and a topology for GPUs newer than Pascal.
- A follow-up ticket, to be filed by homlead: pick and qualify the local language model and
  embedding server for the private topologies. HOM-93 is the embedding consumer.
- Control of the break-glass account (HOM-110 install, HOM-127 IdP): rotation and audit of its
  password, keeping it out of daily use, what happens to it when HOM-127 adds an IdP, and a safe
  first-owner step at install (today anyone can register first,
  `docs/operations/app-server.md:677-678`, open item O4). No auth code change here.
- A replacement for MinIO, and a broker licence change.
- A bundled identity broker (HOM-127).

## Sources

All read on 2026-10-30.

- MinIO repository README: https://github.com/minio/minio
- MinIO licence: https://github.com/minio/minio/blob/master/LICENSE
- Bitnami containers README: https://github.com/bitnami/containers
- Redis licences: https://redis.io/legal/licenses/
- Valkey licence: https://github.com/valkey-io/valkey/blob/unstable/COPYING
- Keycloak licence: https://github.com/keycloak/keycloak/blob/main/LICENSE.txt
- vLLM GPU installation: https://docs.vllm.ai/en/stable/getting_started/installation/gpu/
- Ollama GPU support: https://github.com/ollama/ollama/blob/main/docs/gpu.mdx
- llama.cpp server README: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
- llama.cpp build docs: https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md
- Repository facts: cited inline at base `cfbc214`.
