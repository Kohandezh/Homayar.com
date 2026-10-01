# Final security review — wp02t03-t1 (HOM-32), reviewer wp02t03-t1-final

- Patch: `/root/foreman/20260929-backend-mode-100/wt/wp02t03-t1-check/REVIEW-INPUT.diff`, sha256
  `d65e0270dfec4647756d53aecaad5788360e54c3da36364802ca11591720056d` (verified). Base `3d64130`.
- Method: `git archive 3d64130` into my scratch directory, then `git apply` of the review input. The venv is the
  checker's (read-only symlink). I did not read the maker worktree, `handoffs/`, or other files in `reviews/`.
- Every probe used fake values only (`fake-…-ZZLEAK`, `"j"*40`). Line numbers below refer to the patched files.

## Verdict

**ACCEPTED.** The patch meets AC1–AC7 as amended (A1–A8). The oracle is green on my own copy. I tried every leak
path named in question 1 and none of the 15 secret values reached repr/str, exception text, the exception chain,
pydantic `input`/`ctx`/`url`, or stdlib logging. No existing production rule was weakened. Neither new transport
rule can be passed with another spelling of `STORAGE_ENDPOINT_URL`/`SMTP_*`. What remains is three low-severity
defence-in-depth gaps (unbounded secret-file read, AWS SDK endpoint override of the "provider default" endpoint, no
environment guard on the destructive seed) and informational residuals. None contradicts the contract or blocks a
merge. I recommend them as follow-ups.

## Answers to the six security questions

1. **Secret values in repr/str/exceptions/logging/tracebacks: NO leak (verified by execution).**
   Evidence: `probe_leak.py` on the patched copy, with all 15 secrets set to fake values.
   - `repr`, `str`, `__repr_args__`, `__rich_repr__`, `__pretty__`, `deepcopy`/`model_copy` repr, and
     `logging` `%s`/`%r`: no leak.
   - I raised 14 failing constructions: 5 production rules, `jwt_secret` given as int/list/dict/non-UTF-8 bytes,
     a bad PII keyring (both kinds), a bad port, a bad Literal, and a `{…}` message. Each time I checked `str`,
     `repr`, `.json()`, `errors(include_input=True, include_context=True, include_url=True)` and
     `traceback.format_exception` along the whole `__cause__`/`__context__` chain. None contained a secret, and
     `__cause__`/`__context__` were `None` every time.
   - `Settings.model_validate(...)` also goes through the overridden `__init__` (custom-init models) and is
     redacted.
   - Braces in messages (for example `MODEL_MANIFEST_PINS='{jwt_secret}'`) are not formatted by the
     `PydanticCustomError` rewrap (`config.py:176-189`).
   - `SecretReferenceError` text contains only variable names and the path (`config.py:61-98`, `150-173`).
   - Residual (info I-1): `traceback.TracebackException(..., capture_locals=True)` does expose `data` (the
     file's bytes, `config.py:87`) and `variables` (the whole env/.env map, `config.py:153`). No in-repo
     reporter captures locals: `logging_service` uses stdlib `logging` with a JSON formatter, and Sentry is
     declared but never initialised.
2. **`X` + `X_FILE` across places: no silent resolution inside one place; cross-place precedence is silent but
   documented accurately (verified).** Evidence: `probe_combo.py`, 21 cases.
   - Rejected, naming both variables:
     - env `X` + env `X_FILE`, including `X=''` and a lower-case `x`.
     - Env `X_FILE` spelled twice with different case.
     - `.env` `X` + `.env` `X_FILE`.
     - `.env` `X_FILE=` (empty).
     - A broken reference shadowed by a higher-ranked value.
   - Silent, and documented at `deployment.md:98-111`:
     - env `X` (even empty) beats `.env` `X_FILE`.
     - env `X_FILE` beats `.env` `X`.
     - kwarg beats env `X_FILE`.
     - The last `.env` spelling wins.
   - All of these outcomes match the doc and `.env.example`.
   - Not documented: a misspelled reference (`X__FILE`, `X_PATH`, or `x_file` as a kwarg) is silently ignored
     because of `extra="ignore"`. That is pre-existing behaviour, the same as for a misspelled plain variable.
     See I-3.
3. **`X_FILE` path handling: safe against blocking and TOCTOU; unbounded in size (low L-1).** Evidence:
   `probe_paths.py`.
   - Rejected without blocking, naming the variable and the path: FIFO (with and without a writer),
     `/dev/zero`, `/dev/null`, `/dev/stdin` (a pipe), a symlink to a device, a symlink loop, a UNIX socket, a
     directory, and a 5000-character path.
   - Non-UTF-8 content is rejected with no bytes echoed.
   - Relative paths resolve against the process CWD, as the doc states. `'ok'` was accepted with `cwd=D` and
     rejected with `cwd=/`.
   - `os.open` then `fstat(fd)` then a read from the same fd means there is no check/use TOCTOU. There is no
     privilege boundary either: the process reads a path from its own environment with its own rights.
   - No mode check is performed. That is correct, because Docker secrets are mounted 0444.
   - A 64 MiB sparse file was read whole (+128 MiB RSS), so there is no size cap (L-1).
   - `/proc/self/environ` (a regular file per `stat`) is accepted and yields the initial process environment
     as the "secret" (I-2).
   - NUL in a path is unreachable through the OS environment.
4. **Production transport rules: no bypass via `STORAGE_ENDPOINT_URL`/`SMTP_*` spellings; no pre-existing rule
   weakened (verified); the effective S3 endpoint can still be redirected outside the rule (low L-2).**
   Evidence: `probe_prod.py` and `probe_boto.py`.
   - SMTP:
     - Rejected: `'25'`, `' 25 '`, `'025'`, `'0'`, `'2525'`, `'25.0'`; `use_tls` of `'0'`, `'off'`, `''`;
       host `' '`; and host `'h:25'` with port 0.
     - Accepted: port 587/465 (including `' 587'`) and `use_tls` of `1`/`yes`/`on`/`TRUE`/`t`/`y`.
     - The rule at `config.py:616` mirrors the only SMTP consumer exactly. `email_service._deliver` uses
       `ssl.create_default_context()`, which verifies certificates.
   - Object store:
     - Rejected: `HTTP://`, `s3://`, `//h`, `https//h`, `https:\\h`, `https:///p`, `https://:443`,
       `https://[::1`, and a leading space or tab.
     - Accepted: `HTTPS://`, `hTtPs://`, `https://[::1]:9000`, and `https://x@http://evil.invalid`. The
       userinfo trick still gives botocore an `https` endpoint (`client.meta.endpoint_url` checked), so it is
       not a downgrade.
     - `STORAGE_BACKEND=S3` and `DEPLOYMENT_MODE=OnPrem` are refused by their `Literal` types.
     - Cloud plus `STORAGE_ALLOW_INSECURE_ENDPOINT=true` is rejected; the opt-in works only with `onprem`.
   - The patch adds lines only at `config.py:614-632` in the production branch (`599-632`), with no deletions.
   - Walk-around (L-2): with `STORAGE_ENDPOINT_URL` empty ("provider default", accepted), botocore 1.40.76
     honours `AWS_ENDPOINT_URL_S3`/`AWS_ENDPOINT_URL`. `http://plain.invalid:9000` became the client endpoint
     in production.
5. **Seed and dev compose: no embedded credential; empty password refused before any connection (verified);
   the seed has no production guard (low L-3).**
   - My own boolean-only scan used the literal derived from base `seed.py` `AUTH` and base `NEO4J_AUTH`: 944
     patched-tree files, **0 hits**.
   - `docker compose config` on the patched `docker-compose.yml`:
     - Without `NEO4J_PASSWORD` it fails: "required variable NEO4J_PASSWORD is missing a value: Set
       NEO4J_PASSWORD (at least 8 characters)…".
     - With a fake value it interpolates `NEO4J_AUTH: neo4j/<value>`.
   - `python -m backend.database.seed`:
     - `NEO4J_PASSWORD='   '` exits 1 with "seed refused: …", no traceback.
     - A missing `NEO4J_PASSWORD_FILE` exits 1 with a traceback naming only the variable and the path.
     - `X` together with `X_FILE` is a conflict, and the value is not echoed.
   - `seed.py:25` runs before `GraphDatabase.driver` (`seed.py:211`).
   - L-3: nothing stops the seed in `environment=production`. It now authenticates with the live configured
     Neo4j and runs `MATCH (n) DETACH DELETE n` (`seed.py:213`).
6. **`model_dump()`/`model_dump_json()`: outside AC3 as written; no production path serializes Settings (verified
   by grep); both return all 15 secrets in clear (info I-4).**
   - AC3 names only `repr`/`str` and error text. `Field(repr=False)` (`config.py:47`) does not affect dumps.
   - There is no `model_dump`/`dict()`/`vars()`/`jsonable_encoder` of Settings in non-test code, no endpoint
     returns Settings, and Celery config is built from individual fields.
   - Tests do dump: `test_job_failure_policy.py:639`, `test_usage_ledger.py:521` and `test_job_leases.py:690,699`
     rebuild Settings from `model_dump()`, and `test_model_registry.py:266` and `test_text_pii.py:270` also dump.
     Excluding secrets from dumps would break those tests, so it is correctly out of scope here.

## Findings (highest severity first)

### L-1. low, `Homayar/backend/config.py:87`: `read_secret_file` reads the whole file with no size bound.
- **Why it matters.** A mistyped or mis-mounted reference turns one config line into an unbounded allocation
  at startup of every API, worker and migration process. Examples: a log file, a large dataset, a procfs file
  with a huge `st_size`. Secrets are at most a few KiB, so a cap costs nothing.
- **Trigger.** `SMTP_PASSWORD_FILE=<64 MiB file>` is accepted: `len(value) == 67108864` and RSS grew 128 MiB.
  A multi-GiB file OOMs the container instead of giving a clean `SecretReferenceError`.
- **Smallest fix.** `data = handle.read(LIMIT + 1)` with, for example, `LIMIT = 64 * 1024`. When
  `len(data) > LIMIT`, set `problem = "is larger than 64 KiB"`. Add a test using a sparse file.

### L-2. low, `Homayar/backend/config.py:621-625` (with `Homayar/backend/storage/factory.py:23`): the production object-store rule only judges `STORAGE_ENDPOINT_URL`, but with it empty botocore still takes an `http://` endpoint from `AWS_ENDPOINT_URL_S3`, `AWS_ENDPOINT_URL` or the shared AWS config `endpoint_url`.
- **Why it matters.** The rule's intent (AC4/A3/A8: in cloud production, only TLS to the object store) is
  bypassed by a second spelling of the same setting. Recordings and signed requests would travel in
  plaintext, and the operator would believe the https-only rule protects them. The value is set by the
  operator, so this is misconfiguration protection, not an attacker path.
- **Trigger.**
  1. Set `ENVIRONMENT=production`, `DEPLOYMENT_MODE=cloud`, `STORAGE_BACKEND=s3` and
     `STORAGE_ENDPOINT_URL=` (Settings accepts this).
  2. Set `AWS_ENDPOINT_URL_S3=http://plain.invalid:9000`.
  3. `S3ObjectStorage(...)._client.meta.endpoint_url == 'http://plain.invalid:9000'` (botocore 1.40.76).
- **Smallest fix.** One of:
  - In the production branch, when `storage_endpoint_url` is empty, reject a non-https
    `AWS_ENDPOINT_URL_S3`/`AWS_ENDPOINT_URL` from `os.environ`, and add a test.
  - Or pass `Config(ignore_configured_endpoint_urls=True)` in `S3ObjectStorage._create_client` so that
    Settings stays the single source of the endpoint.
  - Also document the choice in the "Object store" bullet of `deployment.md`.

### L-3. low, `Homayar/backend/database/seed.py:22-30,209-213`: the "development only" seed now connects with the live configured Neo4j credentials and runs `MATCH (n) DETACH DELETE n` without refusing `environment=production`.
- **Why it matters.** Before the patch, the seed could only reach `bolt://localhost:7687` with a dev literal,
  so it failed to authenticate against any real instance. Now `Settings()` supplies the production
  `NEO4J_URI`/`NEO4J_USER`/`NEO4J_PASSWORD[_FILE]` from the environment or `.env`. Running the seed on a
  production or on-prem host therefore wipes the graph projection and loads fake calls into it. This is
  newly reachable because of the patch, though A4 did not ask for a guard.
- **Trigger.** On a host with a complete production configuration, run
  `uv run python -m backend.database.seed`. `neo4j_connection(settings)` returns the production URI and
  credentials. Probe: `neo4j_connection(Settings(environment='production', …, neo4j_uri='bolt://prod-graph.invalid:7687'))`
  returns the production endpoint and nothing refuses.
- **Smallest fix.** In `neo4j_connection`, raise `SeedConfigurationError` when
  `settings.environment not in ("development", "test")`, before building the driver. Add one test beside the
  existing seed tests.

### I-1. info, `Homayar/backend/config.py:87,153`: a locals-capturing traceback renderer would expose secret material.
- **Detail.** Two locals are affected:
  - `data` holds the reference file's bytes (on the non-UTF-8 path).
  - `variables` holds the full environment or `.env` map (on the conflict and missing paths).
- **Trigger.** `TracebackException.from_exception(exc, capture_locals=True)` on either error contains a fake
  file mark and an unrelated env secret. Sentry's default `include_local_variables` would do the same.
- **Status.** No such reporter exists in the repo today (stdlib logging; Sentry never initialised), so AC3 holds.
- **Fix, if wanted.** Clear `data` after decoding, and read `self._variables.env_vars` inline rather than
  binding it to a local.

### I-2. info, `Homayar/backend/config.py:72-83`: special files are opened before the type check, and procfs pseudo-files pass as regular files.
- **Detail.**
  - A device node is opened (with `O_NONBLOCK`) before `fstat` rejects it. Opening can have side effects on
    some devices, for example `/dev/watchdog` or tape rewind.
  - `/proc/self/environ` and `/proc/self/cmdline` are accepted, so the whole initial environment becomes the
    secret value. If that field is an LLM API key, the environment would be sent to the provider as the
    bearer token.
- **Status.** Operator-only input.
- **Optional hardening.** Run `os.stat(path)` first and reject a non-regular file before `open` (keep the
  `fstat` check after `open`). Also refuse paths under `/proc` and `/sys`, or rely on the L-1 size cap plus
  the doc.

### I-3. info, `Homayar/backend/config.py:150-173` and `docs/operations/deployment.md:98-111`: some cross-place resolutions stay silent.
- **Documented cases.**
  - Env `X` shadows `.env` `X_FILE`, even when `X` is empty.
  - The last `.env` case variant wins.
  - A kwarg shadows an env `X_FILE`.
- **Undocumented case.** A misspelled reference name (`X__FILE`, `X_PATH`) is ignored (`extra="ignore"`,
  pre-existing, the same as for plain names). For `PII_MAPPING_KEYS` or `SENTRY_DSN` this silently disables a
  feature rather than failing, because an empty value is valid there.
- **Status.** Matches A5 and the doc.

### I-4. info, `Homayar/backend/config.py:47`: `model_dump()`/`model_dump_json()` return all 15 secrets in clear.
- **Status.** Out of AC3 as written, and no production path uses them (Q6).
- **Risk.** A future diagnostics endpoint or log that dumps Settings would leak everything.
- **Follow-up.** A lint or test guard against `Settings.model_dump` outside tests, or a `SecretStr`-style type
  with a test-only clone helper.

### I-5. info, `Homayar/README.md:94` (with `Homayar/docker-compose.yml:6-9`): the new placeholder works as a password if copied verbatim.
- **Detail.** `NEO4J_PASSWORD=your-local-password` is 19 characters, so both Neo4j and `${NEO4J_PASSWORD:?}`
  accept it. The dev Neo4j publishes 7474/7687 on all interfaces (pre-existing).
- **Suggested change.** Leave the value empty, as `.env.example:168` does, so that the `:?` guard fires until
  a developer chooses a password.

### I-6. info, `docs/operations/deployment.md:124-128`: the Neo4j advice can lead a developer into the conflict rule.
- **Detail.** The doc says: "set `NEO4J_PASSWORD` for that container even when the backend uses
  `NEO4J_PASSWORD_FILE`". Compose reads `Homayar/.env`, and so do the backend and the seed. Putting both lines
  there makes backend and seed startup fail: `.env` `X` + `.env` `X_FILE` is rejected (verified).
- **Status.** The failure is loud, not insecure.
- **Suggested change.** Tell developers to supply the container's value from the shell environment, or to
  use the same plain variable for both.

### I-7. info, `Homayar/backend/config.py:625` and `infra/compose/compose.onprem.yaml:39-41`: the on-prem opt-in accepts any non-https endpoint, not only the bundled MinIO.
- **Detail.** `http://objects.example.com` is accepted with `onprem` and the flag set. The bundled profile
  hard-codes `STORAGE_ENDPOINT_URL: http://minio:9000`, so reaching an external host requires editing the
  compose file, and the flag stays on silently.
- **Status.** As specified by A8, and the residual risk is documented. The follow-up (TLS for the bundled
  MinIO) is already noted.

### I-8. info (pre-existing, outside this spec), `Homayar/backend/config.py:599-632`: production still accepts plaintext transports to other backends.
- **Detail.** Production accepts `redis://` (`REDIS_URL`), a PostgreSQL `DATABASE_URL` without
  `sslmode=require`, and plaintext `bolt://` for Neo4j. The cloud compose prompt says "TLS-enabled managed
  Redis DSN", but nothing enforces it.
- **Suggestion.** Candidate follow-up rules in the same style as AC4.

### I-9. info (pre-existing), repository history: the removed development Neo4j literal remains in git history.
- **Detail.** It is in `3d64130` and earlier (`docker-compose.yml`, `seed.py`, `README.md`).
- **Action.** Treat any Neo4j instance that ever ran with it as using a public password and rotate it. No
  history rewrite is needed for a dev default.

### I-10. info (code quality per reviewer policy), `Homayar/backend/config.py:79,129,198,464`: trailing inline comments added.
- **Detail.** The added comments are `# e.g. an embedded NUL character`,
  `# __call__ resolves every reference at once`, `# e.g. an unterminated IPv6 literal` and
  `# outside the except block: no chained original error`. The test file adds one at line 865.
- **Status.** No behavioural effect. `config.py` already uses explanatory comments, so this is not a repo
  convention breach.
- **Not counted.** `# noqa: S603` on the fixed-argv `subprocess.run` follows existing usage
  (`backend/media/validation.py:533`, with ruff `S` enabled), so it is not listed as a finding.

## Scope (one root cause?)

- The contract bundles two concerns:
  - Secret delivery and redaction plus literal removal (AC1–AC3, AC5).
  - Production transport rules (AC4 with A7/A8, including the on-prem flag and the `compose.onprem.yaml` line).
- By the repository's one-root-cause convention these are two root causes. The patch follows the SPEC, which
  mandates both in this unit, so I do not count this against the maker.
- All eight touched files are within SPEC ownership, including A6 (README line 94 only) and A8 (one compose
  line plus comment).

## Confirmed by the oracle (execution-backed)

- **Foreman record** (`oracle/red-to-green.md`, gates): oracle v2 went from 392 failed / 192 passed on base
  `3d64130` to 584 passed / 0 failed on the patch (turn 1b and turn 2).
- **My re-run** on my own patched copy: `test_oracle_wp02t03.py` (sha256 prefix `731339af85cfcca5`, matches v2)
  plus the maker's `test_config_secrets.py` gave **734 passed, 1 skipped**. The skip is the permission test,
  which cannot run as root.
- The config.py, seed.py and test-file hunks are byte-identical between `gates/maker-turn-1b.patch` and the
  review input. The turn-1b full-suite result therefore applies: 0 failed / 1259 passed / 47 skipped across 4
  chunks in `gates/suite-turn-1b/`.
- **My own probes on the patched copy** (execution-backed): Q1 leak matrix, Q2 combination matrix (21 cases),
  Q3 special-path matrix, Q4 spelling matrix (SMTP 20 cases, object store 24 cases), botocore effective
  endpoint, seed CLI refusals, `docker compose config` interpolation, and a literal scan (0 hits in 944 files).

## Not verified

- I did not re-run the full backend suite. The 0-failed result comes from the Foreman gate logs only.
- The botocore request-time behaviour for the accepted `https://x@http://evil.invalid` endpoint: I checked
  only `client.meta.endpoint_url` (scheme `https`) and made no network call.
- The permission-denied branch with a real non-root user: it is skipped as root here and covered only by the
  maker's monkeypatched `os.open` test.
- The Sentry/`capture_locals` exposure in I-1: demonstrated with `TracebackException(capture_locals=True)`,
  not with Sentry itself (Sentry is not wired up in the repo).
- Behaviour on non-Linux platforms (`O_NONBLOCK`/`mkfifo` availability, `/proc`).
- Whether any production host actually runs the dev seed (L-3 impact is inferred from reachability).

CANDIDATES critical=0 high=0 medium=0 low=3 info=10
