# SPEC — team wp02t03-t1 (mission 20260929-backend-mode-100)

Orchestration mode: `foreman_native_adapted`. Role separation, blind oracle, bare-diff review, two-round cap.
Trigger check: this unit touches secret handling and production configuration -> a **final reviewer**
(security-vulnerability-scanner) runs after the team reviewer's ACCEPTED and passing gates (Foreman expansion rule).

## Task
- Linear: HOM-32 [WP02-T03] Replace embedded/default credentials with secret references and mandatory secure
  production settings. https://linear.app/homayar/issue/HOM-32 (parent HOM-6 / WP02, features F03, F10).
- Type: Implementation (security hardening). Work unit: one root cause — secrets can only arrive as raw environment
  strings, some defaults/literals are embedded in repository files, and secret values are exposed by `Settings` repr.
- Dependency group 1 (trust boundaries). Priority P0.

## Base
`BASE_SHA` = `3d64130` (integration head: HOM-140 d4daf6e + HOM-152 d5bbe5d + mission records).

## Context (verified by Foreman; values never printed)
- `backend/config.py` `Settings` (pydantic-settings; `env_file=PROJECT_ROOT/.env`, `case_sensitive=False`,
  `extra="ignore"`). Secret-bearing fields: `database_url`, `redis_url`, `jwt_secret`, `token_pepper`,
  `google_client_secret`, `asanak_api_key`, `storage_access_key`, `storage_secret_key`, `pii_mapping_keys`,
  `llm_fallback_api_key`, `zai_api_key`, `gapgpt_api_key`, `smtp_password`, `neo4j_password`, `sentry_dsn`.
  All are plain `str`; none is excluded from repr. Verified by Foreman probe at BASE_SHA (fake values only):
  `repr(Settings(...))` contains the value of `jwt_secret`; a production-validator failure raises pydantic
  `ValidationError` whose text includes a truncated `input_value=` repr of the merged input dict (its head and tail
  are shown), so secret values supplied to `Settings` can appear in error text.
- Non-production validators also raise from `Settings` (e.g. `PII_MAPPING_KEYS` parsing in
  `backend/privacy/mapping_crypto.py`); AC3 applies to every error raised while constructing `Settings`.
- Production validator `Settings` model validator (`environment == "production"` branch) already requires: 32+ char
  `jwt_secret`/`token_pepper`, PostgreSQL, secure cookies, no demo account, no mock providers, strict manifests,
  no auto-schema. It does not require SMTP TLS and does not constrain the object-store endpoint scheme.
- `infra/compose/compose.{cloud,onprem}.yaml` already pass core secrets as required `${VAR:?}` references.
- Embedded literal credentials: `Homayar/docker-compose.yml:9` (`NEO4J_AUTH` literal, development Neo4j) and
  `Homayar/backend/database/seed.py:8` (literal Neo4j auth tuple, development seed).

## Acceptance criteria (observable)
1. **Secret references.** For every secret-bearing field `X` listed above, setting the environment variable
   `X_FILE` (any case, matching `case_sensitive=False`) to the path of a readable file makes `Settings().x` equal the
   file's content with exactly one trailing `\n` (or `\r\n`) removed and nothing else altered.
2. **Conflict and failure are loud, never silent, and never leak.** Setting both `X` and `X_FILE` is rejected
   (`ValueError`/validation error naming the variable, not the value). A missing or unreadable `X_FILE` path is
   rejected with an error naming the variable and the path, not any value. An empty file yields an empty value (and
   production then fails its existing checks, e.g. JWT length).
3. **No secret in repr or error text.** `repr(Settings(...))` and `str(Settings(...))` contain none of the secret values
   for the fields listed above; every production-validator error message raised by `Settings` contains none of them.
   Non-secret fields (e.g. `environment`, `api_prefix`) may still appear.
4. **Mandatory secure production settings (additions).** In `environment=production`:
   - `smtp_host` set and `smtp_use_tls` false and `smtp_port != 465` -> rejected; `smtp_host` empty -> accepted.
   - `storage_backend="s3"` with a non-empty `storage_endpoint_url` that is not `https://` -> rejected; empty endpoint
     (provider default) or `https://` -> accepted.
   Development/test behaviour for these two rules is unchanged.
5. **No embedded literal credentials.** `Homayar/docker-compose.yml` takes the Neo4j credential from a required
   environment reference (no literal); `database/seed.py` takes the Neo4j URI/user/password from `Settings`
   (`neo4j_uri`, `neo4j_user`, `neo4j_password`) instead of literals, and refuses to run with an empty password.
   A repository scan for the removed literal credential strings finds none (Foreman provides the check; the literals
   themselves are not written into any new file).
6. **Existing behaviour preserved.** Plain `X` environment variables and `.env` files keep working exactly as before;
   the full backend suite on PostgreSQL has no new failure relative to `BASE_SHA`.
7. **Documentation.** `docs/operations/deployment.md` (backend operations doc) gains a "Secret references" section:
   the `*_FILE` convention, the covered fields, the conflict rule, newline handling, and the two new production rules.
   `Homayar/.env.example` documents `*_FILE` in one comment block; no real values are added.

## Deny-cases and closest allow-controls
- DENY `JWT_SECRET` and `JWT_SECRET_FILE` both set. ALLOW-CONTROL: only `JWT_SECRET_FILE` set -> value from file.
- DENY `JWT_SECRET_FILE` pointing to a missing file. ALLOW-CONTROL: same variable pointing to an existing file.
- DENY production + `smtp_host` + no TLS + port 587. ALLOW-CONTROL: production + `smtp_host` + `smtp_use_tls=True`;
  and production + port 465.
- DENY production + s3 + `http://` endpoint. ALLOW-CONTROL: production + s3 + `https://` endpoint; and development + s3
  + `http://` endpoint (local MinIO).
- DENY a secret value appearing in `repr(settings)`. ALLOW-CONTROL: `environment` still appears.

## File ownership
Maker may change: `Homayar/backend/config.py`; `Homayar/backend/database/seed.py`; `Homayar/docker-compose.yml`;
`Homayar/.env.example` (comments only for `*_FILE`); `docs/operations/deployment.md`; new/extended tests under
`Homayar/backend/tests/` (e.g. `test_config_secrets.py`).
Must not change: migrations, `saas_models.py`, `rls.py`, `authorization.py`, `infra/compose/*` (already correct),
any frontend/website file, any file containing real secrets. Never print, log or commit a secret value; use
obviously fake fixture values in tests (e.g. `"x" * 40`).
Checker: only `Homayar/backend/tests/test_oracle_wp02t03.py` in the checker worktree and records under
`/root/foreman/20260929-backend-mode-100/wp02t03-t1/{oracle,reviews}/`.

## Verification commands (from `<worktree>/Homayar`, after `source /root/foreman/20260929-backend-mode-100/testenv.sh`)
EVERY PostgreSQL-touching command runs under `flock /root/foreman/20260929-backend-mode-100/heavy.lock`.
Note: `testenv.sh` exports test secrets and `ALLOW_MOCK_PROVIDERS=true`; tests must construct `Settings` with
explicit kwargs and `_env_file=None`, and control environment variables with `monkeypatch`.
Focused: `flock … uv run pytest backend/tests/test_config_secrets.py backend/tests/test_api.py -q -p no:cacheprovider`
Full: `flock … uv run pytest backend/tests -q -p no:cacheprovider -rs`
Lint: `uv run ruff check <files> && uv run ruff format --check <files>`

## Worktrees
- Maker: /root/foreman/20260929-backend-mode-100/wt/wp02t03-t1 (branch mission/wp02t03-t1 at BASE_SHA)
- Checker: /root/foreman/20260929-backend-mode-100/wt/wp02t03-t1-check (detached at BASE_SHA, clean)
Each has its own `Homayar/.venv` (uv sync --frozen).

## Known baseline
BASE_SHA code = d5bbe5d: full suite 0 failed / 1109 passed / 46 skipped (Foreman gate, n11-t1).

## Allowed and forbidden actions
Same as stab01-t1: no commit/push/branch/PR/Linear, no migrations, no DB reset, no network, no contact with other
agents, no reading the other role's worktree or files. Never write a real credential anywhere.

## Communication map, envelope, turn protocol, team directory
As in stab01-t1/SPEC.md, team `wp02t03-t1`, names `wp02t03-t1-impl`, `wp02t03-t1-rev`, final reviewer
`wp02t03-t1-final` (security-vulnerability-scanner, opus, xhigh), directory
`/root/foreman/20260929-backend-mode-100/wp02t03-t1/`.

## Models
Implementer: opus (xhigh: secret handling). Reviewer: opus (xhigh). Final reviewer: security-vulnerability-scanner.

## Amendments (Foreman, after phase A, before phase B)
- A1 (AC1 precision): "any case" includes mixed case (e.g. `Jwt_Secret_File`). A lone trailing `\r` (not followed by
  `\n`) is content, not a newline, and is kept. Surrounding spaces/tabs are content and are kept.
- A2 (AC2 precision): a path that exists but is not a readable regular file (for example a directory) counts as
  unreadable and is rejected like a missing file (error names the variable and the path, never a value).
- A3 (AC4 precision): only an `https://` endpoint passes the object-store rule; any other scheme (`http://` in any case,
  `ftp://`) or a scheme-less `host:port` is rejected in production. Production-rule error messages name the settings
  they concern (e.g. `SMTP_HOST`/`SMTP_USE_TLS`/`SMTP_PORT`, `STORAGE_ENDPOINT_URL`), like the existing messages.
- A4 (AC5 precision): `database/seed.py` stays a script (`if __name__ == "__main__":` entry). When run, it reads the
  Neo4j URI, user and password from `Settings` built at run time (so `NEO4J_*` and `NEO4J_PASSWORD_FILE` set in the
  environment at launch are honoured), refuses an empty or absent password by raising or exiting non-zero BEFORE
  opening any driver, and connects through the `neo4j` driver API (`GraphDatabase.driver(uri, auth=(user, password))`).
  The development compose `NEO4J_AUTH` uses a required reference of the form `${VAR:?message}` for the credential.
- A5 (scope note): secret field defaults stay as they are. Behaviour of `X_FILE` supplied through a `.env` file, an
  empty `X_FILE=""`, and `X_FILE` combined with an explicit keyword argument is the maker's documented choice
  (state it in the handoff and in the deployment doc); it must never silently prefer one source over another when both
  are set in the environment.

## Amendments (Foreman, after phase B turn 1 — contract corrections, not a review round)
- A6 (AC5 scope, Foreman ownership error): `Homayar/README.md` line 94 still documents the removed development Neo4j
  literal. The maker may change ONLY that line in `Homayar/README.md`, replacing the value with a non-secret placeholder
  and (optionally) a pointer to the deployment doc's secret-reference section. No other README edits.
- A7 (AC4 SMTP rule corrected, Foreman contract error): verified `backend/services/email_service.py:50-58` — port 465
  uses implicit TLS (`SMTP_SSL`); otherwise STARTTLS is issued when `smtp_use_tls` is true OR the port is 587, and
  `starttls()` fails if the server does not offer it. Port 587 with `smtp_use_tls=false` is therefore encrypted, and both
  compose profiles default to exactly that. The production rule is now: with `smtp_host` set, REJECT when the effective
  transport is plaintext, i.e. `smtp_port` is neither 465 nor 587 AND `smtp_use_tls` is false. ACCEPT: port 465 (any
  flag), port 587 (any flag), any port with `smtp_use_tls=true`, or `smtp_host` empty. AC4's first bullet and the SMTP
  deny/allow pair are replaced accordingly: DENY production + `smtp_host` + port 25 + no TLS flag; ALLOW-CONTROL
  production + `smtp_host` + port 587 + no TLS flag, and port 25 + `smtp_use_tls=true`.
- A8 (AC4 object-store rule, on-prem profile): `infra/compose/compose.onprem.yaml` runs production (default) against the
  bundled MinIO at `http://minio:9000` on the internal compose network. New boolean setting
  `storage_allow_insecure_endpoint` (env `STORAGE_ALLOW_INSECURE_ENDPOINT`, default false). In production with s3 and a
  non-empty non-`https://` endpoint: ACCEPT only when `storage_allow_insecure_endpoint` is true AND
  `deployment_mode == "onprem"`; otherwise REJECT (cloud production cannot opt out; the flag alone is not enough).
  The maker may add ONE setting line `STORAGE_ALLOW_INSECURE_ENDPOINT: "true"` (with a one-line comment naming the
  internal-network MinIO) to the app environment block of `infra/compose/compose.onprem.yaml`; no other infra change.
  The deployment doc states the flag, its on-prem-only scope and the residual risk (plaintext object traffic on the
  internal network). Follow-up (not this unit): serve bundled MinIO over TLS.

## Amendments (Foreman, after the final security review — fix round for findings in code this unit added)
- A9 (final review L-1 + I-2): a secret file larger than 64 KiB (65536 bytes) is rejected with an error naming the
  variable and the path (never content), without reading more than 64 KiB + 1 bytes. A path that is not a regular
  file is rejected BEFORE it is opened (stat first; keep the post-open check), and a path that resolves (after
  symlinks) under `/proc` or `/sys` is rejected. Error text follows AC2/AC3.
- A10 (final review L-3): `database/seed.py` refuses to run unless `Settings.environment` is `development` or `test`,
  raising or exiting non-zero BEFORE opening any Neo4j driver, with a message that names ENVIRONMENT (never a secret).
  (This unit made the seed follow the configured NEO4J_URI; it must not be able to wipe a production graph.)
- Out of this unit, recorded as follow-ups: L-2 (botocore `AWS_ENDPOINT_URL[_S3]`/shared-config endpoint bypasses the
  object-store rule), I-4 (`model_dump()` exposes secrets), I-8 (production accepts plaintext redis/PostgreSQL/bolt),
  bundled MinIO over TLS (A8 residual), `Homayar/Makefile` seed target broken at base.
