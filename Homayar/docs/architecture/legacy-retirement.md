# Legacy stack retirement

Status: RETIRED, 2026-09-29. Linear HOM-152 (N11, reuses F01), parent HOM-141.
Base: `692affb`.

The product backend used to carry a second, older application next to the v1
API: Neo4j-backed dashboard, graph, Copilot, LDAP sync and VoIP webhook
routers, the services only they used, a Pydantic model module, a WebSocket stub
and a prompt loader. None of it was mounted, but all of it could still be
imported, and the webhook router compared its shared secret unsafely. This
change deletes it. The v1 API (`backend/routers/v1/`) is the only HTTP surface.

## What keeps it retired

- `backend/tests/test_truthful_outputs.py` lists every retired module in
  `REMOVED_MODULES`. `test_fabricated_analytics_modules_are_gone` fails if
  `importlib.util.find_spec` finds any of them again.
- `backend/tests/test_legacy_retirement.py` checks that the retired settings are
  not `Settings` fields and that their environment variables are ignored in
  development and production. It checks that the production validator no longer
  mentions the webhook, that the live modules listed below still import, and
  that no dashboard, graph, Copilot, webhook, LDAP, VoIP or WebSocket route is
  mounted.
- `backend/tests/test_authorization_matrix.py::test_route_inventory_document_matches_code`
  pins the whole mounted route inventory to `docs/security/authorization-matrix.md`.

Do not restore these files or copy code out of them. If a capability listed
here as "none" comes back, build it as new v1 work under its own ticket, with an
ADR and an authorization-matrix entry.

## How "dead" was established

Checked at `692affb` before deleting anything:

1. **Not mounted.** `backend/main.py` includes 16 routers, all from
   `backend.routers.v1`: auth, admin, ai_policy, api_keys, billing, ingestions,
   meetings, insights, members, organizations, privacy, shares, teams,
   locations, voice and voicemail. The app serves 127 `(method, path)` pairs,
   and none of them is under `/api/dashboard`, `/api/graph`, `/api/copilot`,
   `/api/webhook` or `/api/ldap`. After the removal the route set is
   byte-identical (127 pairs, same sha256
   `7e7af36a2863581c67b53459e9dacd4961b346ad07365f7f1dc2ec89d79243aa`).
2. **Closed import set.** Grepping `Homayar/` for importers found no live module
   that imports any member of the set. The search covered `backend/` including
   `tests/`, plus the Makefile, `docker-compose.yml` and `.env.example`, and the
   repository's `infra/` (Dockerfiles, Compose files, `product-source.lock`,
   CI scripts) and `.github/`. It used dotted, `from … import`, relative and
   `importlib` forms. Members import only each other. The importers named below
   are all retired.
3. **Some modules could not even be imported.** The five routers import
   `backend.middleware.auth`, which was removed in WP03-T01, so each one raised
   `ModuleNotFoundError: No module named 'backend.middleware'`.
   `services/user_service.py` raised `ModuleNotFoundError: No module named 'passlib'`
   because `passlib` is not a declared dependency. The other modules imported,
   but nothing called them.
4. **Nothing else named them.** No test, env example, Compose file, Dockerfile,
   CI job or product-source lock entry referenced a retired module or setting.

## Retired paths

Each file below was deleted. The `backend/websocket/` and `backend/prompts/`
package directories were deleted entirely, including `__pycache__`. A
directory left with only `__pycache__` in it would still import as a namespace
package.

### Routers

| Path | What it was | Evidence it was dead | Canonical replacement | Status |
|---|---|---|---|---|
| `Homayar/backend/routers/copilot.py` | `POST /chat`: Copilot question answering over the Neo4j call graph | Not mounted; importers: none; import failed (`backend.middleware.auth` removed) | none — capability not offered. The nearest live surfaces are `GET /api/v1/search` and `GET /api/v1/meetings/{meeting_id}/summary` (`routers/v1/insights.py`). | RETIRED |
| `Homayar/backend/routers/dashboard.py` | Eight `GET` endpoints for KPIs, QC trend, sentiment, issues, aggregated issues, recent calls, call problems and call QC details, read from Neo4j | Not mounted; importers: none; import failed (`backend.middleware.auth` removed) | `GET /api/v1/meetings/{meeting_id}/quality` (`services/quality_service.py`) and `GET /api/v1/organizations/{organization_id}/aggregates` (`routers/v1/organizations.py`). There is no cross-call KPI dashboard. | RETIRED |
| `Homayar/backend/routers/graph.py` | Entity search, entity detail, neighbourhood and stats over Neo4j | Not mounted; importers: none; import failed (`backend.middleware.auth` removed) | none — capability not offered. Neo4j is only an optional projection (`database/neo4j_connection.py`, `ENABLE_NEO4J_PROJECTION`). | RETIRED |
| `Homayar/backend/routers/ldap_sync.py` | `GET /test` and `POST /sync`: LDAP connectivity test and Active Directory user sync into Neo4j | Not mounted; importers: none; import failed (`backend.middleware.auth` removed) | none — capability not offered. Sign-in is email/password, Google and phone OTP (`routers/v1/auth.py`, `services/auth_service.py`). | RETIRED |
| `Homayar/backend/routers/webhook.py` | `POST /voip/call-event` and `POST /voip/batch`: normalized Asterisk, 3CX and FreePBX events, auto-created users by extension and stored base64 recordings on disk | Not mounted; importers: none; import failed (`backend.middleware.auth` removed); unsafe secret comparison (see below) | none — capability not offered (no PBX adapter). Recordings enter through `POST /api/v1/ingestions` and `/api/v1/uploads` (`services/ingestion_service.py`, `services/upload_service.py`), and voicemail through `/api/v1/voicemails`. | RETIRED |

### Services

| Path | What it was | Evidence it was dead | Canonical replacement | Status |
|---|---|---|---|---|
| `Homayar/backend/services/copilot_service.py` | Copilot intent classification, fixed Cypher templates and LLM answer generation | Never called by live code; importers: `routers/copilot.py` (retired) | none — capability not offered (see `routers/copilot.py`) | RETIRED |
| `Homayar/backend/services/dashboard_service.py` | Neo4j KPI, trend, sentiment, issue and recent-call queries | Never called by live code; importers: `routers/dashboard.py`, `services/copilot_service.py` (both retired) | `services/quality_service.py` and the organization aggregates in `routers/v1/organizations.py` | RETIRED |
| `Homayar/backend/services/graph_service.py` | Neo4j entity and neighbourhood queries (unscoped, full node properties) | Never called by live code; importers: `routers/graph.py` (retired) | none — capability not offered | RETIRED |
| `Homayar/backend/services/issue_aggregation_service.py` | LLM clustering of Neo4j problem detections into aggregated issues | Never called by live code; importers: `routers/dashboard.py` (retired) | none — capability not offered. Per-meeting analysis runs in the v1 job pipeline (`jobs/processor.py`, `providers/`). | RETIRED |
| `Homayar/backend/services/call_service.py` | Call upload to local disk and call, transcript and analysis CRUD in Neo4j | Never called by live code; importers: `routers/webhook.py`, `services/pdf_service.py` (both retired) | `services/meeting_service.py`, `services/ingestion_service.py`, `services/upload_service.py` and `services/transcript_service.py` over the relational models in `database/saas_models.py` | RETIRED |
| `Homayar/backend/services/ad_service.py` | LDAP bind, directory search, LDAP authentication and AD-to-Neo4j user sync (`ldap3`) | Never called by live code; importers: `routers/ldap_sync.py`, `services/user_service.py` (both retired) | none — capability not offered | RETIRED |
| `Homayar/backend/services/user_service.py` | Neo4j user CRUD, bcrypt hashing, LDAP fallback login, VoIP auto-created users and an `ADMIN_DEFAULT_PASSWORD` bootstrap admin | Never called by live code; importers: `routers/webhook.py` (retired); import failed (`passlib` not a dependency) | `services/auth_service.py`, `services/member_service.py` and `security/tokens.py` | RETIRED |
| `Homayar/backend/services/pdf_service.py` | ReportLab PDF call report built from `call_service` | Never called by live code; importers: none | `GET /api/v1/meetings/{meeting_id}/export` (`services/export_service.py`: txt, json, srt, vtt; no PDF) | RETIRED |
| `Homayar/backend/services/redis_service.py` | Redis token blacklist and per-call job-status cache | Never called by live code; importers: none | Session revocation through `LoginSession.revoked_at` (`services/auth_service.py`); job state through `GET /api/v1/meetings/{meeting_id}/job` (`backend/jobs/`) | RETIRED |
| `Homayar/backend/services/llm_router.py` | Multi-provider LLM routing (Z.AI, GapGPT) with per-agent model overrides | Never called by live code; importers: `services/copilot_service.py`, `services/issue_aggregation_service.py` (both retired) | `backend/providers/`: `factory.py`, `routing.py`, `registry.py`, `egress.py` and `openai_compatible.py` (ADRs 0026–0031) | RETIRED |

### Other modules and packages

| Path | What it was | Evidence it was dead | Canonical replacement | Status |
|---|---|---|---|---|
| `Homayar/backend/database/models.py` | Pydantic models for the legacy API (users, calls, transcripts, QC, problems, chat) | Importers: `routers/copilot.py`, `services/user_service.py` (both retired) | `backend/schemas/` (v1 API models) and `database/saas_models.py` (SQLAlchemy) | RETIRED |
| `Homayar/backend/websocket/` | Package directory | Importers: none | none — capability not offered | RETIRED |
| `Homayar/backend/websocket/__init__.py` | Empty package marker | Importers: none | none — capability not offered | RETIRED |
| `Homayar/backend/websocket/manager.py` | Two-line comment stub ("WebSocket manager removed") | Importers: none | none — capability not offered | RETIRED |
| `Homayar/backend/prompts/` | Package directory holding the prompt loader and text prompts | Only importer: `services/copilot_service.py` (retired) | `providers/openai_compatible.py` (`SYSTEM_PROMPT`, versioned by `PROMPT_VERSION`) | RETIRED |
| `Homayar/backend/prompts/__init__.py` | `load_prompt(name)`: read `<name>.txt` next to the module | Only importer: `services/copilot_service.py` (retired) | `providers/openai_compatible.py` | RETIRED |
| `Homayar/backend/prompts/copilot_intent.txt` | Copilot intent-classification system prompt | Read only by `services/copilot_service.py` (retired) | none — capability not offered | RETIRED |
| `Homayar/backend/prompts/copilot_response.txt` | Copilot answer system prompt | Read only by `services/copilot_service.py` (retired) | none — capability not offered | RETIRED |
| `Homayar/backend/prompts/insight_en.txt` | English insight-agent prompt from the removed four-agent analysis pipeline | No reader at `692affb` (its caller `services/analysis_service.py` was removed in WP04) | `providers/openai_compatible.py` | RETIRED |
| `Homayar/backend/prompts/insight_fa.txt` | Persian insight-agent prompt | No reader at `692affb` | `providers/openai_compatible.py` | RETIRED |
| `Homayar/backend/prompts/problem_en.txt` | English problem-detection prompt | No reader at `692affb` | `providers/openai_compatible.py` | RETIRED |
| `Homayar/backend/prompts/problem_fa.txt` | Persian problem-detection prompt | No reader at `692affb` | `providers/openai_compatible.py` | RETIRED |
| `Homayar/backend/prompts/qc_en.txt` | English quality-control prompt | No reader at `692affb` | `providers/openai_compatible.py`; quality in `services/quality_service.py` | RETIRED |
| `Homayar/backend/prompts/qc_fa.txt` | Persian quality-control prompt | No reader at `692affb` | `providers/openai_compatible.py`; quality in `services/quality_service.py` | RETIRED |
| `Homayar/backend/prompts/teacher_en.txt` | English agent-coaching prompt | No reader at `692affb` | none — capability not offered | RETIRED |
| `Homayar/backend/prompts/teacher_fa.txt` | Persian agent-coaching prompt | No reader at `692affb` | none — capability not offered | RETIRED |

### Settings

These fields were removed from `Settings` in `backend/config.py`. `Settings`
uses `extra="ignore"`, so a deployment that still exports these variables starts
normally, and the values are dropped rather than exposed. `LLM_MODEL` and
`REDIS_URL` stay: `providers/factory.py` reads `llm_model`, and `redis_url` feeds the
live rate limiter, the Celery broker and the readiness check.

| Setting (environment variable) | Only consumer | Status |
|---|---|---|
| `llm_model_qc` (`LLM_MODEL_QC`) | `services/llm_router.py` | RETIRED |
| `llm_model_problem` (`LLM_MODEL_PROBLEM`) | `services/llm_router.py` | RETIRED |
| `llm_model_insight` (`LLM_MODEL_INSIGHT`) | `services/llm_router.py` | RETIRED |
| `llm_model_teacher` (`LLM_MODEL_TEACHER`) | `services/llm_router.py` | RETIRED |
| `llm_model_copilot` (`LLM_MODEL_COPILOT`) | `services/llm_router.py` | RETIRED |
| `ldap_server` (`LDAP_SERVER`) | `services/ad_service.py` | RETIRED |
| `ldap_port` (`LDAP_PORT`) | `services/ad_service.py` | RETIRED |
| `ldap_use_ssl` (`LDAP_USE_SSL`) | `services/ad_service.py` | RETIRED |
| `ldap_bind_dn` (`LDAP_BIND_DN`) | `services/ad_service.py` | RETIRED |
| `ldap_bind_password` (`LDAP_BIND_PASSWORD`) | `services/ad_service.py` | RETIRED |
| `ldap_search_base` (`LDAP_SEARCH_BASE`) | `services/ad_service.py` | RETIRED |
| `ldap_user_ou` (`LDAP_USER_OU`) | `services/ad_service.py` | RETIRED |
| `voip_webhook_secret` (`VOIP_WEBHOOK_SECRET`) | `routers/webhook.py` | RETIRED |
| `enable_voip_webhook` (`ENABLE_VOIP_WEBHOOK`) | the production validator only; `routers/webhook.py` never read it | RETIRED |
| `admin_default_password` (`ADMIN_DEFAULT_PASSWORD`) | `services/user_service.py` | RETIRED |
| Production validator clause "`VOIP_WEBHOOK_SECRET` is required when the webhook is enabled" | `Settings.validate_security_configuration` | RETIRED |

## Unsafe pattern: the webhook shared-secret check

`Homayar/backend/routers/webhook.py` (lines 46–48 and 107–109 at `692affb`)
checked callers like this:

```python
if settings.voip_webhook_secret:
    auth = request.headers.get("X-Webhook-Secret", "")
    if auth != settings.voip_webhook_secret:
        raise HTTPException(401, "Invalid webhook secret")
```

- **Not constant-time.** `!=` on strings returns as soon as a character
  differs, so response timing leaks how much of a guess is correct. Live code
  compares secrets with `hmac.compare_digest` (`services/auth_service.py`,
  `services/otp_service.py`, `security/context.py`).
- **Fail-open.** With `VOIP_WEBHOOK_SECRET` empty, the check was skipped and
  anyone could post events. `ENABLE_VOIP_WEBHOOK` gated nothing: the router
  never read it, and only the production validator did.
- **No tenant, no authorization-matrix entry.** The handler auto-created users
  from any extension number and wrote the decoded recordings to local disk.

The router was never mounted, so none of this was reachable. It is deleted so
that it cannot be re-activated by adding one `include_router` line. A future PBX
integration is new work. It must fail closed when unconfigured, verify a
signature with a constant-time comparison, bind every event to a workspace,
enter media through the v1 ingestion path and appear in
`docs/security/authorization-matrix.md`.

## Deferred: dependency pruning

The removal leaves some declared dependencies without an importer in `backend/`.
They are **not** removed here. `pyproject.toml` and `uv.lock` are pruned in
WP01-T04 (HOM-28), which owns reproducible, locked image builds:

- the `ldap3` optional extra (`[project.optional-dependencies] ldap`), used only
  by `services/ad_service.py`;
- `reportlab`, used only by `services/pdf_service.py`.

`neo4j` stays because `database/neo4j_connection.py` and `database/seed.py`
still use it.

## Unchanged by this retirement

- `Homayar/backend/database/neo4j_connection.py`: live, the optional Neo4j
  projection opened in the `main.py` lifespan.
- `Homayar/backend/database/seed.py`: the development seed. It is the only entry
  in the truthful-output guard's `RANDOM_ALLOWLIST`.
- `Homayar/backend/providers/factory.py` and
  `Homayar/backend/services/rate_limit_service.py`: live.
- The draft ADRs 0026–0031 under `Homayar/docs/handoff/session-a/adr/`. They
  are superseded by `docs/architecture/decisions/0026…0031` (see
  `Homayar/docs/handoff/session-a/adr/README.md`) and are not edited here.
