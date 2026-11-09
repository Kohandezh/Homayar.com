# ADR 0047: Private topologies, capacity envelope, local identity and storage

Date: 2026-10-30 · Status: Accepted 2026-10-30, owner sign-off 2026-10-30 (HOM-106, WP19-T01, parent HOM-23, features F74, F75; recorded by HOM-362) · Amendment 1 (HOM-351) drafted 2026-11-10, owner sign-off open · Builds on: ADR 0019, ADR 0026, ADR 0031, ADR 0035, ADR 0044

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

## Owner sign-off for Amendment 1 (HOM-351), open

These items belong to "Amendment 1 (HOM-351): D5 local models picked" at the end of this file.
They are open. The 7 approved points above do not change. Every pick below is **provisional**:
it comes from the sources. Only the embedding model and the picked reranker have run, on a
development Mac, on a 12-query smoke test (a proxy, not a P40). No P40 number exists.

- **A1. Which local language model?** Recommended: `Qwen3-8B` at `Q4_K_M`
  (`Qwen/Qwen3-8B-GGUF`, the official Qwen file), with thinking turned off. Reason: a 30-minute
  call fits in its 32,768-token native context, it fits on one P40 next to the embedding model
  and the reranker (about 15 GiB planned of 24 GB), and it avoids the risks that could stop the
  other candidate on a P40. It is Apache-2.0, and its technical report lists Persian among its
  test languages. This pick is provisional and comes from the sources only: no LLM ran. It is
  not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step.
  P40 risk: llama.cpp's CUDA kernels for its architecture are not verified on Pascal (compute
  capability 6.1); the release build carries Pascal code only as PTX that is compiled on the
  first start. What it loses: recordings longer than 32,768 tokens (the setting allows 4 hours)
  need chunking or a longer context, which is not built or tested. If the owner says no:
  `Qwen3.5-9B` at `Q4_K_M` (`unsloth/Qwen3.5-9B-GGUF`). It has a 262,144-token context and a
  smaller KV cache, but more P40 risk: its Gated DeltaNet CUDA kernels are new and not verified
  on Pascal, the GGUF is a third-party conversion, and Persian is not named on its card.
- **A2. Which embedding model and reranker?** Recommended: `bge-m3` at `Q8_0` for embeddings and
  `Qwen3-Reranker-0.6B` at `Q8_0` for reranking. `bge-m3` is the only candidate with a published
  Persian retrieval number. If the owner says no: `Qwen3-Embedding-0.6B` and
  `bge-reranker-v2-m3`, the other two candidates. The embedding choice fixes the vector size of
  the HOM-93 index (1024 for both candidates). A later change of the embedding model means a full
  re-index.
- **A3. Which server runtime?** Recommended: llama.cpp `llama-server`, release `b11960`, the
  `ubuntu-cuda-12.8-x64` build. One runtime serves chat, `/v1/embeddings` and `/v1/rerank`. If
  the owner says no: Ollama. It lists the Tesla P40, but its OpenAI-compatible API has no rerank
  route, so the reranker would need a second runtime.
- **A4. Which GPU host runs the models?** Recommended: one operator-provided model host with its
  own P40 (approved point 3 above). The planned VRAM for all three models is about 15 GiB of the
  card's 24 GB. If the owner says no and wants the second P40 of the shared app server: that needs
  Padyar's agreement first, because Padyar shares both cards, and D5 placement changes to "on the
  Homayar host".
- **A5. Licences.** Recommended: accept. All picked models are Apache-2.0 or MIT. None has a use
  policy, a "Built with" naming duty or a user limit. The duty is to ship a copy of the licence
  (and the copyright notice for MIT) with the weights. HOM-107 writes these notices. Two GGUF
  files are conversions by third parties (`unsloth`, `ggml-org`), under the same licence as the
  base model by their cards. The runtime adds two more items: llama.cpp is under the MIT licence
  (`LICENSE` at `b11960`: "MIT License", read 2026-11-10), and the CUDA build needs NVIDIA's CUDA
  runtime libraries (`libcudart`, `libcublas`, `libcublasLt`), which come under NVIDIA's own
  terms. Those NVIDIA terms are not read here: to be checked by HOM-107. Territory and
  sanctions: the Apache-2.0 and MIT texts have no territorial clause. Whether the NVIDIA terms
  have one is part of the HOM-107 check. Whether operators in Homayar's market can download from
  Hugging Face and GitHub is not verified; if they cannot, HOM-107 must ship the weights and the
  runtime in the offline bundle. If the owner says no: HOM-107 must use files converted by
  Homayar from the base repositories, at the cost of one more build step.
- **A6. Start HOM-93 and HOM-95 on a provisional pick?** Recommended: yes. They may start with
  the A2 models, and a P40 run in a follow-up ticket qualifies them. If the owner says no:
  HOM-93 and HOM-95 wait for that run.

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
  Update 2026-11-10: HOM-351 is that ticket. Its provisional pick is in "Amendment 1 (HOM-351):
  D5 local models picked" at the end of this file, with owner items A1 to A6 at the top.

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
  Update 2026-11-10: HOM-351 picks them, provisionally, in "Amendment 1 (HOM-351): D5 local
  models picked" below. The P40 run that qualifies them is still deferred.
- Control of the break-glass account (HOM-110 install, HOM-127 IdP): rotation and audit of its
  password, keeping it out of daily use, what happens to it when HOM-127 adds an IdP, and a safe
  first-owner step at install (today anyone can register first,
  `docs/operations/app-server.md:677-678`, open item O4). No auth code change here.
- A replacement for MinIO, and a broker licence change.
- A bundled identity broker (HOM-127).

## Amendment 1 (HOM-351): D5 local models picked

Date: 2026-11-10. Status: **provisional**, owner sign-off open (items A1 to A6 at the top). This
amendment fills the model and server pick that D5 left open. It does not change D1 to D4, the D2
tables or the 7 approved points.

### What is measured and what is not

- The picks below come from primary sources only: model cards, licence files, papers, the
  llama.cpp source and release, and the Hugging Face API. Each source has its date read.
- Two models ran, one at a time, on a development Mac (Apple M-series, Metal) with the official
  llama.cpp `b11960` macOS arm64 build, on 2026-11-10: the embedding model bge-m3 `Q8_0` and the
  reranker Qwen3-Reranker-0.6B `Q8_0`. Their raw files are in
  `docs/research/hom-351-local-models/results/bge-m3-q8_0/` and
  `.../results/qwen3-reranker-0.6b-q8_0/`. Every other cell says "not measured", with the
  reason. A cell gets a number only from a raw file in that folder.
- Why only two: the mission rule keeps free disk above 8 GiB after any download. Free disk was
  8.27 GiB after the reranker run (other users of the Mac took disk during the work), and each
  other retrieval model is about 0.6 GB. bge-reranker-v2-m3 and Qwen3-Embedding-0.6B are
  "not measured, disk". No LLM ran: each needs about 14 GiB free.
- A Mac number is **measured on Apple M-series with Metal, proxy only**. It is not a P40 number
  and never stands for one.
- Every P40 number here is **planned, not measured**. A run on the GPU server is out of scope for
  HOM-351: it needs a reviewed script in a follow-up ticket (see "Model host sizing").
- The harness and the synthetic fixtures for the runs are ready:
  `docs/research/hom-351-local-models/` (`README.md`, `run_eval.py`, `fixtures/`). The fixtures
  are 12 synthetic calls (8 Persian, 4 English) and a 20-passage, 12-query retrieval set. They
  are a smoke test. The real WP13 gold corpus replaces them later.

### Shortlist

Hugging Face facts (repository commit, file size, LFS sha256) come from the Hugging Face API on
2026-11-10. Every pinned commit below was still the head of its repository on that day. Sizes
are in bytes, with GiB in brackets.

| Role | Model | GGUF repository and commit | GGUF file | Size | Licence (source, date read, commercial use) | Persian support evidence |
| --- | --- | --- | --- | --- | --- | --- |
| LLM | Qwen3.5-9B (base `Qwen/Qwen3.5-9B` `c202236235762e1c871ad0ccb60c8ee5ba337b9a`) | `unsloth/Qwen3.5-9B-GGUF` `3885219b6810b007914f3a7950a8d1b469d598a5` | `Qwen3.5-9B-Q4_K_M.gguf` | 5680522464 (5.29) | Apache-2.0: `LICENSE` in the base repository (https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/LICENSE, read 2026-11-10); the GGUF card says `apache-2.0`. Commercial use: yes. Duty: ship the licence text. No use policy, no naming duty. | Card: "201 languages and dialects". Persian is not named on the card: not verified. |
| LLM | Gemma 4 E4B, instruction tuned, QAT | `google/gemma-4-E4B-it-qat-q4_0-gguf` `4b4a2c1d584be7264f87aac328a1bc739ce81b6c` | `gemma-4-E4B_q4_0-it.gguf` | 5154941280 (4.80) | Apache-2.0: card `license: apache-2.0`, `license_link` https://ai.google.dev/gemma/docs/gemma_4_license (read 2026-10-30, page updated 2026-04-01, no use policy in the text). Commercial use: yes. Duty: ship the licence text. | Card: "pre-trained on 140+ languages", "out-of-the-box support for 35+ languages". Persian is not named: not verified. |
| LLM | Qwen3-8B | `Qwen/Qwen3-8B-GGUF` `7c41481f57cb95916b40956ab2f0b139b296d974` (official Qwen file) | `Qwen3-8B-Q4_K_M.gguf` | 5027783488 (4.68) | Apache-2.0: `LICENSE` in the GGUF repository (https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/7c41481f57cb95916b40956ab2f0b139b296d974/LICENSE, read 2026-11-10). Commercial use: yes. Duty: ship the licence text. | Card: "100+ languages and dialects". The Qwen3 technical report lists `fa` (Persian) among the test languages of its INCLUDE and MT-AIME2024 benchmarks (arXiv 2505.09388, read 2026-11-10). No Persian score is printed for it. |
| LLM, sources only | Gemma 4 12B, QAT | `google/gemma-4-12B-it-qat-q4_0-gguf` `29d097773436b69ff9feafd636ab4cf873786537` | `gemma-4-12b-it-qat-q4_0.gguf` | 6975879296 (6.50) | Apache-2.0 (as Gemma 4 E4B). | As Gemma 4 E4B. |
| Embedding | bge-m3 (base `BAAI/bge-m3` `5617a9f61b028005a4858fdac845db406aefb181`) | `ggml-org/bge-m3-Q8_0-GGUF` `9eba04c5d75ba5a1595e45de734d36bef4e5cb98` | `bge-m3-q8_0.gguf` | 634553760 (0.59) | MIT: card `license: mit` (https://huggingface.co/BAAI/bge-m3, read 2026-11-10). The repository has no licence file, so the card is the only primary source. Commercial use: yes. Duty: ship the copyright and permission notice. | MIRACL dev, Persian (`fa`), nDCG@10: dense 60.9, all modes 63.4; BM25 28.7 (BGE-M3 paper, Table 1, arXiv 2402.03216, read 2026-11-10). |
| Embedding | Qwen3-Embedding-0.6B | `Qwen/Qwen3-Embedding-0.6B-GGUF` `370f27d7550e0def9b39c1f16d3fbaa13aa67728` | `Qwen3-Embedding-0.6B-Q8_0.gguf` | 639150592 (0.60) | Apache-2.0: card metadata (https://huggingface.co/Qwen/Qwen3-Embedding-0.6B, read 2026-11-10); no licence file. Commercial use: yes. | Card: "100+ languages", MMTEB mean (task) 64.33. Persian is not named: not verified. |
| Reranker | Qwen3-Reranker-0.6B (base `Qwen/Qwen3-Reranker-0.6B` `e61197ed45024b0ed8a2d74b80b4d909f1255473`) | `ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF` `a02f48bb4f057028298c21fa033da2b30d7742d5` | `qwen3-reranker-0.6b-q8_0.gguf` | 639153184 (0.60) | Apache-2.0: card metadata (https://huggingface.co/Qwen/Qwen3-Reranker-0.6B, read 2026-11-10); no licence file. Commercial use: yes. | Card: "100+ languages"; MMTEB-R 66.36 against 58.36 for BGE-reranker-v2-m3 (Qwen's own runs). Persian is not named: not verified. |
| Reranker | bge-reranker-v2-m3 (base `BAAI/bge-reranker-v2-m3` `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`) | `gpustack/bge-reranker-v2-m3-GGUF` `3093af03b1a635e67b084b1d8c03c5f5e020fd05` | `bge-reranker-v2-m3-Q8_0.gguf` | 635676416 (0.59) | Apache-2.0: card metadata (https://huggingface.co/BAAI/bge-reranker-v2-m3, read 2026-11-10); no licence file. Commercial use: yes. | Card: "Multilingual", trained with MIRACL (multi-language), which has a Persian split. No Persian score in the card text (its MIRACL chart is an image, not read). |

Not shortlisted: `Qwen/Qwen3.6-35B-A3B` (commit `995ad96eacd98c81ed38be0c5b274b04031597b0`,
Apache-2.0, read 2026-10-30). It has no official GGUF file, and at about 4 bits its weights
alone would fill most of one P40 (inference from 35B parameters).

### Runtime for the P40

Facts, from primary sources:

- vLLM needs compute capability 7.5 or higher (D5 above). It does not run on a P40.
- Ollama supports compute capability 5.0 and up and lists the Tesla P40 (D5 above). Its
  OpenAI-compatible API lists `/v1/chat/completions`, `/v1/completions`, `/v1/models`,
  `/v1/embeddings` and audio routes, and no rerank route
  (https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx, read 2026-11-10).
- Hugging Face TGI: the repository is archived, and its README says it "is now in maintenance
  mode" (https://github.com/huggingface/text-generation-inference, read 2026-11-10).
- llama.cpp release `b11960` (published 2026-10-28, commit
  `8642541b255bfb5dc39cd351a178be09013a55d6`) ships
  `llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz`, sha256
  `64d57beb9abb637c177c6afb313760a0c0782224cac71e74869eed5b8f9a1ea4`
  (https://github.com/ggml-org/llama.cpp/releases/tag/b11960, read 2026-11-10).
- The CUDA runtime libraries are in a separate archive of the same release:
  `cudart-llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz`, 594,371,478 bytes, sha256
  `7c4d5974bd39649df649649e5d71950688a70e37aaae00bf11afbb656ea2cc56`
  (`gh release view b11960 -R ggml-org/llama.cpp`, read 2026-11-10). The release workflow packs
  `libcudart`, `libcublas` and `libcublasLt` into it, "extract next to the binaries ($ORIGIN
  rpath)"; the archive puts them in a folder named like the archive
  (`.github/workflows/release.yml` at `b11960`, lines 434-447, read 2026-11-10). The CUDA builds
  load their backends at run time (`-DGGML_BACKEND_DL=ON`, line 414). Without these libraries,
  or without a CUDA 12.8 runtime installed on the host, the CUDA backend cannot load. Whether
  `llama-server` then stops or runs on the CPU only is not verified. The folder names inside
  both archives are not checked on a Linux host.
- Structured output: at `b11960`, `llama-server` reads the product's `response_format` shape
  (`{"type": "json_schema", "json_schema": {"schema": ...}}`) and uses the inner `schema`
  (`tools/server/server-common.cpp` lines 1392-1394, read 2026-11-10). The `strict` flag is not
  read in that code. The schema becomes a grammar, and the README points to
  `tests/test-json-schema-to-grammar.cpp` "for supported features" (`tools/server/README.md`
  line 581). Whether every feature of the product schema (enums, formats, length limits,
  nullable types) converts is **not verified**. The schema valid rate of the first P40 run checks
  it.
- The release CUDA builds set `-DGGML_NATIVE=OFF` (`.github/workflows/release.yml` at `b11960`,
  read 2026-10-30). With that, `ggml/src/ggml-cuda/CMakeLists.txt` lines 43-45 at `b11960` add
  `50-virtual 61-virtual 70-virtual` only when the CUDA toolkit is older than 13 (read
  2026-11-10). So the CUDA 12.8 build carries PTX for compute capability 6.1, and the CUDA 13.4
  build does not. Inference from the build files; not verified on a P40.
- `b11960` knows the `qwen35` and `gemma4` architectures (`src/llama-arch.cpp`) and has CUDA
  kernels for the Gated DeltaNet layers of Qwen3.5 (`ggml/src/ggml-cuda/gated_delta_net.cu`)
  (read 2026-11-10). Whether those kernels run on Pascal is not verified.
- `llama-server` at `b11960` has `--embedding`, `--pooling`, `--rerank`, `/v1/embeddings`, and
  `/v1/rerank`, which returns `results` as `{index, relevance_score}`
  (`tools/server/README.md` at `b11960`, read 2026-11-10). This is the shape U2's rerank client
  reads.

**Pick: llama.cpp `llama-server`, release `b11960`, the `ubuntu-cuda-12.8-x64` build.**

- Why it wins: one runtime serves all three routes (chat, embeddings, rerank) from GGUF files.
  The CUDA 12.8 build carries Pascal PTX. Ollama has no rerank route, and vLLM and TGI are out
  (above).
- What it loses: the PTX is compiled on the first run on the P40, so the first start is slow
  (inference). Pascal speed and kernel coverage are not verified. If the release build fails on
  the P40, the fallback is a source build with `-DCMAKE_CUDA_ARCHITECTURES=61`, which the build
  docs describe (`docs/build.md` at `b11960`, "Override Compute Capability Specifications").

### The pick (provisional, from sources)

**LLM: Qwen3-8B, `Q4_K_M`** (owner item A1).

- Why it wins: it is enough for the workload and carries the least P40 risk. A 30-minute
  Persian call is far below its 32,768-token native context (see the VRAM plan), and it fits on
  one P40 with the other two models. The GGUF is Qwen's own file. Its GGUF architecture is
  `qwen3` (Hugging Face API, read 2026-11-10): plain attention layers, the same kind as the
  reranker that ran on the Mac, with no Gated DeltaNet layers (inference: so it needs no new
  kernel type). Its technical
  report lists Persian (`fa`) among its test languages. Apache-2.0 with no use policy.
- What it loses: recordings longer than about 32,768 tokens need chunking, or YaRN context
  extension, which its card says was validated up to 131,072 tokens (card, "Processing Long
  Texts"). Homayar has neither path built or tested. Its KV cache is larger per token (36 layers
  with 8 KV heads). No Persian score is printed for it. It thinks by default, so the server must
  turn thinking off. It is not verified on Pascal either: no candidate is.
- Against `Qwen3.5-9B` (the "if no" choice): a 262,144-token context and a small KV cache (only
  8 of its 32 layers keep one; `config.json` at `c202236`, read 2026-11-10), and the widest
  language claim (201). By this document's own sizing the long context buys nothing for a
  30-minute call. It loses on P40 risk: its Gated DeltaNet CUDA kernels are new and not verified
  on Pascal, the GGUF is a third-party conversion (`unsloth`), and Persian is not named on its
  card. It is the better choice if long recordings without chunking become a requirement.
- Against `Gemma 4 E4B`: an official Google QAT file. It loses on size (4.5B effective
  parameters), and Persian is not named.

**Embedding: bge-m3, `Q8_0`** (owner item A2).

- Why it wins: the only candidate with a published Persian retrieval number (MIRACL `fa`, dense
  nDCG@10 60.9). MIT. 1024 dimensions and 8,192 tokens. No query instruction needed.
- What it loses: the GGUF gives only the dense vector, not bge-m3's sparse or multi-vector modes
  (the 63.4 "all modes" number needs them). `Qwen3-Embedding-0.6B` has the higher MMTEB mean on
  its own card. The GGUF is a conversion by `ggml-org` (the llama.cpp project), not by BAAI.

**Reranker: Qwen3-Reranker-0.6B, `Q8_0`** (owner item A2).

- Why it wins: on the multilingual MMTEB retrieval subset its card shows 66.36 against 58.36 for
  `bge-reranker-v2-m3`. Apache-2.0. The GGUF is converted by `ggml-org`, while the
  `bge-reranker-v2-m3` GGUF is by `gpustack` on an older llama.cpp commit (`f4d2b88`, its card).
  In the same table, `bge-reranker-v2-m3` scores below the plain `Qwen3-Embedding-0.6B` ranking
  it reranks (64.64), while `Qwen3-Reranker-0.6B` scores above it.
- What it loses: the comparison is Qwen's own run, on candidates from Qwen's own embedding model.
  No Persian number. It is a decoder model, so it keeps a KV cache, which the encoder
  `bge-reranker-v2-m3` does not.

### P40 VRAM plan (planned, not measured)

One P40 has 24 GB. VRAM = weights + KV cache + compute buffers + one CUDA context per server.
Weights are the GGUF file sizes. KV cache per token = layers with KV × 2 (K and V) × KV heads ×
head size × 2 bytes (`f16`, the `llama-server` default). Compute buffers and CUDA contexts are
estimates (inference), to be measured.

| Server | Weights | KV cache or state | Compute buffer (estimate) | Planned total |
| --- | --- | --- | --- | --- |
| LLM, Qwen3-8B `Q4_K_M`, `-c 32768 -np 1` (one slot of 32,768 tokens) | 4.68 GiB | 36 × 2 × 8 × 128 × 2 B = 144 KiB per token, × 32,768 = 4.5 GiB | 1.0 GiB | about 10.2 GiB |
| Embedding, bge-m3 `Q8_0`, `-c 8192` | 0.59 GiB | none kept (encoder) | 1.0 GiB | about 1.6 GiB |
| Reranker, Qwen3-Reranker-0.6B `Q8_0`, `-c 8192` | 0.60 GiB | 28 × 2 × 8 × 128 × 2 B = 112 KiB per token, × 8,192 = 0.88 GiB | 0.5 GiB | about 2.0 GiB |
| CUDA context, 3 servers | | | 0.3 GiB each | about 0.9 GiB |
| **Total** | | | | **about 14.7 GiB of 24 GB** |

One slot means one chat request at a time; the next request waits. A 30-minute Persian call is
far below 32,768 tokens (inference: about 4,500 spoken words; the real count depends on the
tokenizer, and the P40 run must count it). The rest of the card is margin. It is not offered to
ASR: D5 keeps the language model off the ASR host.

For comparison, the "if no" choice `Qwen3.5-9B` with `-c 65536 -np 2` (two slots of 32,768
tokens): 5.29 GiB of weights, 8 × 2 × 4 × 256 × 2 B = 32 KiB per token × 65,536 = 2.0 GiB of KV
cache, plus the DeltaNet state, 24 × 32 × 128 × 128 × 4 B = 48 MiB per slot (inference: assumes
`f32` state), plus 1.0 GiB of compute buffer: about 8.4 GiB, for a total of about 12.9 GiB.

### Operator commands

On the model host, unpack both release archives into the same folder. The `cudart` archive holds
the CUDA runtime libraries that the binaries load from their own folder. The alternative is a
CUDA 12.8 runtime installed on the host. Replace `<private-ip>` with the model host's private
address. Do not bind to a public address. Each command is one server; run each as its own
service.

```bash
# Runtime archives of llama.cpp b11960 (GitHub release digests, read 2026-11-10).
#   64d57beb9abb637c177c6afb313760a0c0782224cac71e74869eed5b8f9a1ea4  llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz
#   7c4d5974bd39649df649649e5d71950688a70e37aaae00bf11afbb656ea2cc56  cudart-llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz
sha256sum llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz cudart-llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz
tar xzf llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz
tar xzf cudart-llama-b11960-bin-ubuntu-cuda-12.8-x64.tar.gz
# The cudart archive unpacks into its own folder. Copy its libraries next to llama-server
# (the binaries look for them in their own folder, $ORIGIN rpath).
cp -P cudart-llama-b11960-bin-ubuntu-cuda-12.8-x64/lib* <folder with llama-server>/

# Check each model file before the first start. These sha256 values are the ones Hugging Face
# lists for the LFS files at the pinned commits (read 2026-11-10). bge-m3 and the reranker were
# also measured on the downloaded files (shasum -a 256, 2026-11-10): the same values. The LLM
# file is not measured.
#   d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785  Qwen3-8B-Q4_K_M.gguf
#   aa473d51f451a22f0fcf39ba3330c14bed38a385712b1113440f69df4047a173  bge-m3-q8_0.gguf
#   22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48  qwen3-reranker-0.6b-q8_0.gguf
sha256sum /srv/models/*.gguf

# Chat (LLM). Thinking off.
./llama-server -m /srv/models/Qwen3-8B-Q4_K_M.gguf --alias qwen3-8b-q4_k_m \
  --host <private-ip> --port 8080 -ngl all -c 32768 -np 1 --reasoning off

# Embeddings.
./llama-server -m /srv/models/bge-m3-q8_0.gguf --alias bge-m3-q8_0 --embedding \
  --host <private-ip> --port 8081 -ngl all -c 8192 -b 8192 -ub 8192

# Rerank.
./llama-server -m /srv/models/qwen3-reranker-0.6b-q8_0.gguf --alias qwen3-reranker-0.6b-q8_0 \
  --rerank --host <private-ip> --port 8082 -ngl all -c 8192 -b 8192 -ub 8192
```

Homayar then points `LOCAL_AI_BASE_URL` at `http://<private-ip>:8080/v1`, and the embedding and
rerank settings that HOM-351 U2 adds at ports 8081 and 8082, each with the `--alias` above as
the model name. Each endpoint needs an operator manifest with `endpoint_kind: private` (D5).

Notes on these commands, all not verified on a P40:

- The flags exist at `b11960` (`tools/server/README.md`, read 2026-11-10). `-ngl all` puts every
  layer on the GPU.
- `--embedding` leaves the pooling at the model default from the GGUF. `-b` and `-ub` equal to
  `-c` let one input of up to 8,192 tokens fit in one batch (inference).
- Thinking off, from reading the sources (not run). At `b11960`, `--reasoning off` sets
  `enable_thinking` to `false` for the chat template (`common/arg.cpp` lines 3746-3754), and
  setting `enable_thinking` through `--chat-template-kwargs` is deprecated with a warning that
  says to use `--reasoning off` (`common/arg.cpp` lines 3588-3591). The chat template that the
  Hugging Face API reports for `Qwen/Qwen3-8B-GGUF` at `7c41481` writes an empty `<think>` block
  when `enable_thinking` is `false`. For the "if no" choice: the Qwen3.5 base template does the
  same (`chat_template.jinja` at `c202236`, lines 149-150), and the template that the Hugging
  Face API reports for the `unsloth` GGUF files at `3885219` thinks only when `enable_thinking`
  is `true` (its line 150). With `--reasoning off`, all three give the empty `<think>` block
  (all read 2026-11-10). The first run must still check that no `<think>` text comes back.
- Access: `llama-server` logs "no API key is set and CORS allows all origins" when started like
  this (both `server.log` files under `docs/research/hom-351-local-models/results/`). Without more
  steps, any host or browser page on the customer's private network can use the three servers.
  Choice for now: the operator firewalls ports 8080 to 8082 so that only the Homayar host can
  reach them. `--api-key` and `--api-key-file` exist at `b11960` (`tools/server/README.md`
  lines 215-216), but Homayar has no key setting for the embedding and rerank endpoints yet
  (HOM-351 U2 adds the URL and model settings only). Adding those key settings is a follow-up.
- Downloads and telemetry: every command loads a local file with `-m`. None uses `-hf` or
  `--model-url`, so the server needs no outbound path. Whether `llama-server` sends any
  telemetry is not verified; none is named in its README.

### Model host sizing (planned, not measured)

| Item | Planned | Why |
| --- | --- | --- |
| GPU | 1 NVIDIA GPU with compute capability 6.1 (Tesla P40, 24 GB) | the VRAM plan above, about 15 GiB, leaves margin |
| CPU | 8 cores | every layer is on the GPU, so the CPU only tokenises and serves HTTP (inference) |
| RAM | 16 GiB | the three GGUF files are 5.9 GiB and are memory-mapped at load (inference) |
| Disk | 20 GiB free | 5.9 GiB of models, one older set during an upgrade, the binaries, the 0.6 GB `cudart` archive and logs |
| OS | Linux x86-64 with an NVIDIA driver for CUDA 12.8 | the release build; the driver version is not verified |

Who measures it: a P40 run in a follow-up ticket that homlead files. That run needs a reviewed
script on the GPU server (out of scope for HOM-351). HOM-111 then records the model host on the
full F74 install. Until then, these values are not in the D2 minimums, and the F74 summary, QA
and search steps cannot pass.

### For HOM-93 and HOM-95

Facts the embedding index (HOM-93) and the reranking step (HOM-95) need. "Verified" means read in
a primary source on 2026-11-10; it does not mean tested on a P40.

| Fact | Value | Status |
| --- | --- | --- |
| bge-m3 vector size | 1024 | verified (bge-m3 card; and every vector of the Mac run had 1024 values, `results/bge-m3-q8_0/result.json`, `dimensions`) |
| bge-m3 longest input | 8,192 tokens | verified (bge-m3 card) |
| bge-m3 query or passage prefix | none | verified (bge-m3 card: no instruction needed) |
| Vector normalisation | `llama-server` returns Euclidean (L2) normalised vectors by default (`--embd-normalize`, default 2) | verified in `tools/server/README.md` at `b11960` (line 192); not checked on saved vectors (the Mac run saved no raw vectors) |
| Pooling | the default from the GGUF file; the operator command sets none | not verified: the vectors were not compared with the original BAAI model's vectors |
| Qwen3-Reranker instruction | the model is instruction aware; its card's default instruction is "Given a web search query, retrieve relevant passages that answer the query", and the card says an instruction gives 1% to 5% | verified (Qwen3-Reranker-0.6B card) |
| Does `llama-server` add that instruction? | the server builds each rerank prompt from the GGUF's `rerank` template, filling only `{query}` and `{document}` (`tools/server/server-common.cpp` lines 1953-2002 at `b11960`). The converter at `b11960` writes a `rerank` template that holds the default instruction (`conversion/qwen.py` lines 233-236). | not verified for the pinned file: the `ggml-org` GGUF was made with an older converter, and its embedded `rerank` template was not read. HOM-95 cannot change the instruction through the API. |
| Rerank response | `results` of `{index, relevance_score}` | verified (`tools/server/README.md` at `b11960`); the Mac run parsed it, but no raw response was saved |

### Operations and failure modes

- **Health.** `GET /health` exists and needs no API key (`tools/server/README.md` lines 480-482
  at `b11960`). The Mac runs waited for it before each run. The operator runbook for the model
  host (service files, start order, logs, upgrades) belongs to HOM-110.
- **Out of GPU memory.** If the three servers do not fit, the server that loads last fails to
  load its model, or runs with fewer layers on the GPU. Which one happens with `-ngl all` is not
  verified. HOM-110's runbook must check `/health` of all three after a start.
- **A model change behind the same URL.** The manifest pin holds the model name and the endpoint
  (HOM-351 U2). If an operator swaps the GGUF file but keeps the `--alias`, the pin still
  matches, and Homayar cannot see the change. Rule: every model file gets its own alias (the
  aliases above name the model and its quantisation), and a new alias needs a new manifest.
- **Reversing the LLM pick.** A different LLM may need prompt work. The prompt contract is
  versioned (`PROMPT_VERSION = 4`, `Homayar/backend/providers/openai_compatible.py:34`); a prompt
  change needs a new version and a new evaluation, not only a new owner answer.
- **Reversing the embedding pick.** A full re-index (owner item A2).

### Results

Commands run from `Homayar/` against one `llama-server` on `127.0.0.1` (see
`docs/research/hom-351-local-models/README.md`). Every Mac number is **measured on Apple
M-series with Metal, proxy only**.

LLM quality and speed. Command form (not run):
`uv run python ../docs/research/hom-351-local-models/run_eval.py llm --base-url http://127.0.0.1:8080/v1 --label <name> --out ../docs/research/hom-351-local-models/results/<name> --server-pid <pid>`

| Candidate | Schema valid rate | Intent accuracy | Outcome accuracy | Entity recall / precision | Key fact support rate | Citation validity | QA accuracy | Prompt tokens/s | Generation tokens/s | Peak memory |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Qwen3.5-9B `Q4_K_M` | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step |
| Gemma 4 E4B `q4_0` | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step |
| Qwen3-8B `Q4_K_M` | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step |
| Gemma 4 12B `q4_0` | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step | not measured on the Mac (disk); real qualification on the P40 in a later reviewed-script step |

Retrieval quality and speed, on the 12-query synthetic set (8 Persian, 4 English).

| Candidate | MRR (all / fa / en) | nDCG@5 (all / fa / en) | Recall@3 (all / fa / en) | Hit@1 (all / fa / en) | Throughput (proxy) | Peak memory (proxy) |
| --- | --- | --- | --- | --- | --- | --- |
| bge-m3 `Q8_0` (dense) | 1.000 / 1.000 / 1.000 | 0.986 / 0.998 / 0.962 | 0.944 / 0.958 / 0.917 | 1.000 / 1.000 / 1.000 | 94.0 passages/s, 1964.3 tokens/s (3 rounds of 20 passages) | RSS 696.6 MiB (2 samples only); `top` memory 182 MiB |
| Qwen3-Embedding-0.6B `Q8_0` (dense) | not measured, disk | not measured, disk | not measured, disk | not measured, disk | not measured, disk | not measured, disk |
| Qwen3-Reranker-0.6B `Q8_0`, over all 20 passages | 1.000 / 1.000 / 1.000 | 0.977 / 0.973 / 0.984 | 0.903 / 0.896 / 0.917 | 1.000 / 1.000 / 1.000 | 11.7 pairs/s (360 pairs, both modes) | RSS 1595.5 MiB (36 samples); `top` memory 1034 MiB |
| Qwen3-Reranker-0.6B `Q8_0`, over the bge-m3 dense top 10 | 1.000 / 1.000 / 1.000 | 0.986 / 0.987 / 0.984 | 0.944 / 0.958 / 0.917 | 1.000 / 1.000 / 1.000 | (same run) | (same run) |
| bge-reranker-v2-m3 `Q8_0` (all 20 passages; dense top 10) | not measured, disk | not measured, disk | not measured, disk | not measured, disk | not measured, disk | not measured, disk |

The bge-m3 row comes from `docs/research/hom-351-local-models/results/bge-m3-q8_0/`
(`result.json`, `eval/retrieval_dense.json`, `retrieval_dense.jsonl`, `server.log`). Commands:

```bash
# Server (official llama.cpp b11960 macOS arm64 build, model file sha256 checked first):
llama-server -m bge-m3-q8_0.gguf --alias bge-m3-q8_0 --embedding --host 127.0.0.1 --port 8081 \
  -ngl all -c 8192 -b 8192 -ub 8192
# Harness (writes the files above and runs the evaluator):
uv run python ../docs/research/hom-351-local-models/run_eval.py embed \
  --base-url http://127.0.0.1:8081/v1 --label bge-m3-q8_0 --model bge-m3-q8_0 \
  --out ../docs/research/hom-351-local-models/results/bge-m3-q8_0 --server-pid 57761 \
  --note '<the server command>'
# The HOM-69 evaluator call inside it:
uv run python -m backend.evaluation retrieval \
  ../docs/research/hom-351-local-models/results/bge-m3-q8_0/retrieval_dense.jsonl \
  --ks 1,3,5 --slices language
```

How to read the bge-m3 row:

- It is a smoke test. Every query finds a relevant passage first (Hit@1 = 1.000), so the set is
  too small and too easy to rank models by. It shows that the GGUF file, the server route and the
  Persian text path work end to end. It does not qualify bge-m3.
- The peak RSS has only 2 samples, because the whole run took 1.1 seconds. It is a rough value.

The Qwen3-Reranker-0.6B rows come from
`docs/research/hom-351-local-models/results/qwen3-reranker-0.6b-q8_0/` (`result.json`,
`eval/retrieval_rerank_all.json`, `eval/retrieval_rerank_dense_top10.json`, the two input
`.jsonl` files, `server.log`). The dense top 10 mode reads the saved bge-m3 file
`results/bge-m3-q8_0/retrieval_dense.jsonl`, so bge-m3 did not run at the same time. Commands:

```bash
# Server (official llama.cpp b11960 macOS arm64 build, model file sha256 checked first):
llama-server -m qwen3-reranker-0.6b-q8_0.gguf --alias qwen3-reranker-0.6b-q8_0 --rerank \
  --host 127.0.0.1 --port 8082 -ngl all -c 8192 -b 8192 -ub 8192
# Harness (both modes in one run):
uv run python ../docs/research/hom-351-local-models/run_eval.py rerank \
  --base-url http://127.0.0.1:8082/v1 --label qwen3-reranker-0.6b-q8_0 \
  --model qwen3-reranker-0.6b-q8_0 \
  --out ../docs/research/hom-351-local-models/results/qwen3-reranker-0.6b-q8_0 \
  --dense ../docs/research/hom-351-local-models/results/bge-m3-q8_0/retrieval_dense.jsonl \
  --server-pid 61370 --note '<the server command>'
# The HOM-69 evaluator calls inside it:
uv run python -m backend.evaluation retrieval \
  ../docs/research/hom-351-local-models/results/qwen3-reranker-0.6b-q8_0/retrieval_rerank_all.jsonl \
  --ks 1,3,5 --slices language
uv run python -m backend.evaluation retrieval \
  ../docs/research/hom-351-local-models/results/qwen3-reranker-0.6b-q8_0/retrieval_rerank_dense_top10.jsonl \
  --ks 1,3,5 --slices language
```

How to read the reranker rows:

- The reranker works end to end through `/v1/rerank` on Persian and English text.
- It does not improve on bge-m3 here. Over the dense top 10, nDCG@5 is 0.986 for both, and
  Recall@3 is 0.944 for both. Per language the gap goes both ways: Persian nDCG@5 0.987 against
  0.998 for bge-m3 alone, English 0.984 against 0.962. Over all 20 passages it is a little lower
  than bge-m3 alone (nDCG@5 0.977, Recall@3 0.903).
- These gaps are one or two passages on 12 queries. The set is too easy (Hit@1 is 1.000
  everywhere), so it cannot show what a reranker adds. It does not change the pick. Without a
  bge-reranker-v2-m3 run there is no measured comparison between the two rerankers.
- Speed is 11.7 query-passage pairs per second, much slower than the embedding model, as
  expected for a cross-scoring model (proxy).

P40 numbers: none. Planned, not measured.

### Limits of this amendment

- The picks are provisional. A measured run can change any of them; then this amendment is
  updated and the owner items are asked again.
- Persian support is a claim on the card for every LLM and for the Qwen embedding and reranker.
  Only bge-m3 has a published Persian number, and it is on MIRACL (Wikipedia text), not on call
  transcripts.
- The fixtures are synthetic and small. Their numbers will rank candidates, not qualify them.
  WP13's gold corpus and HOM-70's confidence intervals qualify them.
- No P40 run exists. Kernel coverage, speed and real VRAM on Pascal are not verified.
- **The product's 60-second timeout is an open requirement for the P40 run.** The chat adapter
  gives up after 60 seconds by default (`timeout_seconds: float = 60.0`,
  `Homayar/backend/providers/openai_compatible.py:339`). Planned time for one analysis call =
  prompt tokens / prompt tokens per second + output tokens / generation tokens per second. For a
  30-minute Persian call, the prompt has thousands to tens of thousands of tokens and the answer
  is a long JSON object, so the call may take more than 60 seconds on a P40 (inference; nothing
  is measured). The harness keeps a longer transport timeout so a slow call still finishes and is
  measured, and it counts every call over 60 seconds as "would time out in the product"
  (`docs/research/hom-351-local-models/run_eval.py`, `PRODUCT_TIMEOUT_SECONDS`). If calls go over
  60 seconds on the P40, the product needs a longer timeout or shorter calls before F74.
- **Strict JSON output is not verified.** See "Runtime for the P40": `llama-server` uses the
  product schema as a grammar but does not read `strict`, and which schema features convert is
  not checked. The schema valid rate is the first check of the P40 run.
- **Script and digit variants are not tested.** The retrieval fixtures use the same letters and
  digit forms in queries and passages, and the runs slice by language only. Arabic `ي`/`ك`
  against Persian `ی`/`ک`, the zero-width non-joiner, and Persian `۱۲۳` against `123` are
  untested. HOM-93 or the WP13 corpus must test them.
- **CPU fallback is not measured.** No model ran on a CPU only. A `single-host-cpu` install has
  no GPU, and D5 already puts the language model on a separate model host, so local summary and
  QA on such an install still need a model host with a GPU. A CPU-only model host would be much
  slower (inference) and would likely break the 60-second timeout for the LLM. The two small
  retrieval models may be usable on a CPU, but that is not measured either.

### Sources (Amendment 1)

Read on 2026-11-10 unless marked.

- Hugging Face API, model info and `paths-info` for every repository in the shortlist:
  `https://huggingface.co/api/models/<repo>` and
  `https://huggingface.co/api/models/<repo>/paths-info/<commit>`.
- Model cards: https://huggingface.co/Qwen/Qwen3.5-9B, https://huggingface.co/google/gemma-4-E4B-it,
  https://huggingface.co/Qwen/Qwen3-8B, https://huggingface.co/BAAI/bge-m3,
  https://huggingface.co/Qwen/Qwen3-Embedding-0.6B, https://huggingface.co/Qwen/Qwen3-Reranker-0.6B,
  https://huggingface.co/BAAI/bge-reranker-v2-m3, and the GGUF repositories in the table.
- Model configs: `config.json` of `Qwen/Qwen3.5-9B`, `google/gemma-4-E4B-it`, `Qwen/Qwen3-8B`,
  `Qwen/Qwen3-Reranker-0.6B` at the commits in the table.
- Chat templates: `chat_template.jinja` of `Qwen/Qwen3.5-9B` at `c202236`, and the GGUF chat
  template of `unsloth/Qwen3.5-9B-GGUF` at `3885219` from
  `https://huggingface.co/api/models/unsloth/Qwen3.5-9B-GGUF/revision/3885219b6810b007914f3a7950a8d1b469d598a5?expand[]=gguf`,
  and the GGUF chat template of `Qwen/Qwen3-8B-GGUF` at `7c41481` from the same API
  (`.../Qwen/Qwen3-8B-GGUF/revision/7c41481f57cb95916b40956ab2f0b139b296d974?expand[]=gguf`).
- Licence files: https://huggingface.co/Qwen/Qwen3.5-9B/blob/c202236235762e1c871ad0ccb60c8ee5ba337b9a/LICENSE,
  https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/7c41481f57cb95916b40956ab2f0b139b296d974/LICENSE.
- Gemma 4 licence: https://ai.google.dev/gemma/docs/gemma_4_license (read 2026-10-30).
- Qwen3 technical report: https://arxiv.org/abs/2505.09388.
- BGE-M3 paper: https://arxiv.org/abs/2402.03216.
- llama.cpp release `b11960`: https://github.com/ggml-org/llama.cpp/releases/tag/b11960; at tag
  `b11960`: `tools/server/README.md`, `docs/build.md`, `src/llama-arch.cpp`, `common/arg.cpp`,
  `ggml/src/ggml-cuda/CMakeLists.txt`, the `ggml/src/ggml-cuda/` file list,
  `tools/server/server-common.cpp`, `conversion/qwen.py`, `LICENSE`, and
  `.github/workflows/release.yml` (first read 2026-10-30, read again 2026-11-10).
- Ollama OpenAI compatibility: https://github.com/ollama/ollama/blob/main/docs/api/openai-compatibility.mdx.
- TGI repository: https://github.com/huggingface/text-generation-inference.

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
