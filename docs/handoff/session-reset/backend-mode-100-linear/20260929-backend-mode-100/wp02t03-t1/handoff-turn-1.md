# Handoff: wp02t03-t1-impl, turn 1 (HOM-32, WP02-T03)

Worktree: /root/foreman/20260929-backend-mode-100/wt/wp02t03-t1 (branch mission/wp02t03-t1, base 3d64130).
Nothing was committed.

## Changed files

| File | Change |
| --- | --- |
| `Homayar/backend/tests/test_config_secrets.py` (new) | Written first. 137 tests (136 pass, 1 skips under root) for AC1 to AC6 |
| `Homayar/backend/config.py` | `*_FILE` sources, secret fields left out of repr, redacted validation errors, 2 production rules |
| `Homayar/backend/database/seed.py` | Reads Neo4j URI, user and password from `Settings` at run time. Refuses an empty password before any driver is opened |
| `Homayar/docker-compose.yml` | `NEO4J_AUTH: "neo4j/${NEO4J_PASSWORD:?...}"` (required reference, no literal) |
| `Homayar/.env.example` | Adds one comment block for `*_FILE`. No values added |
| `docs/operations/deployment.md` | New `### Secret references` subsection under "Secret Contract" |

Root cause: secrets could only arrive as raw environment strings. Two literal dev credentials were in repository
files. `Settings` repr and pydantic's `input_value` exposed secret values.

## Mechanism (config.py)

- `SECRET_FIELDS` holds the 15 contract fields. Each is annotated
  `SecretSetting = Annotated[str, Field(repr=False)]`, so its type stays plain `str`.
- `settings_customise_sources` returns
  `init > [env refs] > env > [.env refs] > .env > secrets_dir`.
  - `_SecretFileReferences` resolves the `<NAME>_FILE` references for one place. It uses that place's own
    source (`EnvSettingsSource` or `DotEnvSettingsSource`), so the variable mapping, `case_sensitive=False`
    folding and prefix are the same ones pydantic uses for plain `NAME`.
  - A reference ranks with the place it comes from, so the existing precedence
    `kwargs > environment > .env > default` is unchanged.
- `read_secret_file(variable, path)`:
  - Opens with `O_RDONLY|O_NONBLOCK|O_CLOEXEC`, then checks `fstat` for `S_ISREG` (symlinks are followed).
  - Reads bytes and decodes them as strict UTF-8.
  - Strips exactly one trailing `\r\n` or `\n`.
  - Every failure sets a reason string. The `SecretReferenceError` (subclass of `pydantic_settings.SettingsError`,
    which is a `ValueError`) is raised **outside** the `except` blocks. `__cause__` and `__context__` are therefore
    `None`: no `UnicodeDecodeError` carries file bytes, and no `OSError` is attached.
- AC3, error text:
  - `model_config` sets `hide_input_in_errors=True`.
  - `Settings.__init__` catches pydantic `ValidationError` and re-raises
    `ValidationError.from_exception_data(title, [PydanticCustomError(type, msg), loc, input="[redacted]"], hide_input=True)`.
    The raise is also outside the `except`, so nothing is chained.
  - Error types, locations and messages are kept. Input values are not: secrets, and unrelated `.env` extras such
    as `MINIO_ROOT_PASSWORD`, which pydantic-settings adds to the merged input.
  - The existing messages never contain values. `parse_keyring` messages were checked: they contain no key
    material.
- AC4 rules, in the `environment == "production"` block only (development, test and staging are unchanged):
  - `smtp_host and not smtp_use_tls and smtp_port != 465` is rejected. The message names SMTP_HOST, SMTP_USE_TLS
    and SMTP_PORT.
  - `storage_backend == "s3"` with a non-empty `storage_endpoint_url` that fails `_is_https_url` is rejected. The
    message names STORAGE_ENDPOINT_URL.
    - `_is_https_url` requires the case-insensitive prefix `https://` and a non-empty `urlsplit(...).hostname`.
    - A `ValueError` from `urlsplit` (for example `https://[::1`) counts as not https.
    - The endpoint value is never echoed.
- No existing validator rule was changed or removed.

## Edge-case choices (A1, A2 and A5; all documented in deployment.md)

| Case | Behaviour |
| --- | --- |
| Letter case | `JWT_SECRET_FILE`, `jwt_secret_file` and `Jwt_Secret_File` all work. The same reference in 2+ spellings in the environment is rejected. Errors name the canonical upper-case name, plus "(set as …)" when the spelling differs |
| Conflict | `NAME` and `NAME_FILE` together **in the same place** (environment, or the same `.env`) are rejected even when `NAME=""` (presence counts as set). The error names both variables and no value |
| Across places | Standard precedence applies. Env `NAME_FILE` beats `.env` `NAME` (so a template-copied `.env` with `JWT_SECRET=` still works with env `JWT_SECRET_FILE`). Env `NAME` beats `.env` `NAME_FILE` |
| `.env` references | Honoured at `.env` precedence. Relative paths resolve against the process working directory |
| Explicit keyword argument | Wins, as for plain `NAME`. Every `NAME_FILE` that is set is still read and checked, so a broken or conflicting reference is loud even when shadowed (this also applies to a shadowed `.env` reference) |
| Empty `NAME_FILE=""` | Rejected as "set but empty", naming the variable |
| Empty file | Empty value (production then fails the JWT length check, as tested) |
| Newlines | Only one trailing `\n` or `\r\n` is removed. A lone `\r`, spaces, tabs, a BOM and extra newlines are kept |
| Missing file, directory, FIFO, `/dev/zero`, permission denied, non-UTF-8 | Rejected with the variable and the path, never content. A FIFO does not block |
| Plain `NAME` set twice in different case | Unchanged pydantic behaviour (AC6). Only references get the multi-spelling check |

## AC5

- `seed.py`: the module-level URI and AUTH constants were removed (base `seed.py:7-8`; the literal was at
  `seed.py:8`).
  - New `neo4j_connection(settings=None)` builds `Settings()` when called, so `NEO4J_*` and
    `NEO4J_PASSWORD_FILE` set at launch are honoured.
  - It raises `SeedConfigurationError(RuntimeError)` for an empty or whitespace-only password.
  - `seed()` calls it before `GraphDatabase.driver(uri, auth=(user, password))`.
  - Under `__main__` the refusal becomes `sys.exit("seed refused: ...")`, which gives exit 1 and no traceback.
  - The module still imports without a password (`test_legacy_retirement` imports it).
- `docker-compose.yml:9` now uses a required reference, verified with `docker compose config --quiet`:
  - without `NEO4J_PASSWORD`: exit 1 with "required variable NEO4J_PASSWORD is missing a value: Set NEO4J_PASSWORD (at least 8 characters)…";
  - with a fake value: exit 0.
- The user stays `neo4j`, because Neo4j requires that name in `NEO4J_AUTH`.
- Literal scan (`git grep -i` for the removed string): the only remaining hit is **`Homayar/README.md:94`**
  (`NEO4J_PASSWORD=<literal>` in the quick-start env example). README.md is not in the maker's file ownership, so
  I did not change it. See Risks. I scrubbed my saved red-run log, which had captured the literal in assertion
  diffs; it now contains no occurrence.

## Consumers checked (field types unchanged: `str`)

`grep -rn "\.(database_url|redis_url|jwt_secret|…|sentry_dsn)\b" backend` (non-test) finds these consumers:

- `main.py:222` (redis)
- `database/relational.py:34,46`
- `database/neo4j_connection.py:14`
- `providers/factory.py:92-148`
- `services/google_oauth_service.py:126`
- `services/rate_limit_service.py:43`
- `services/email_service.py:60`
- `routers/v1/auth.py:254-528`
- `migrations/env.py:25`
- `storage/factory.py:25-26`
- `celery_app/__init__.py:30`
- `privacy/mapping_crypto.py:57-59`
- `security/tokens.py:25-101`

All of them read the attributes as plain `str`, and the values are unchanged. `repr=False` only affects
`__repr_args__`. `model_dump()` still includes the values. No backend code reads these names from `os.environ`
directly. `Settings.__pydantic_custom_init__` was already `True` at base (BaseSettings defines `__init__`), so
`model_validate` semantics do not change.

## Verification (all pytest runs under `flock /root/foreman/20260929-backend-mode-100/heavy.lock`, after `source testenv.sh`, from `Homayar/`)

1. Red, with the tests written first against base code:
   `uv run pytest backend/tests/test_config_secrets.py -q -p no:cacheprovider -rs`
   gave `95 failed, 39 passed, 1 skipped`. Log: `handoffs/impl-red/red-turn-1.txt`, with the literal scrubbed.
   The 39 that passed are allow-controls and plain-variable regressions.
2. Green: the same command gives `136 passed, 1 skipped in 1.56s`. The skip is
   `test_permission_denied_reference_is_rejected: root reads files mode 000`. The same path is covered, and
   passes, by `test_open_failure_is_reported_without_chaining`, which patches `os.open` to raise PermissionError.
3. SPEC focused command, final code:
   `flock … uv run pytest backend/tests/test_config_secrets.py backend/tests/test_api.py -q -p no:cacheprovider`
   gave `157 passed, 1 skipped, 59 warnings in 19.64s`.
4. Lint:
   - `uv run ruff check backend/config.py backend/tests/test_config_secrets.py && uv run ruff format --check …`
     gives `All checks passed!` and `2 files already formatted`.
   - `backend/database/seed.py` has 58 findings, all pre-existing (33 E501 and 25 S311 on data lines, first finding
     at line 36; none are in my changed hunks). Base had 59: my import block fixed the I001. `ruff format --check`
     would reformat seed.py at base too, and I did not reformat the whole file.
5. mypy:
   - `uv run mypy backend/config.py backend/database/seed.py backend/tests/test_config_secrets.py` reports no
     errors in config.py or the test file.
   - seed.py has 2 errors (`Record | None` not indexable), pre-existing at base (base lines 299 and 303).
   - The other reported errors are in files I did not touch (asanak.py, registry.py, …), and the counts match base.
6. Full suite, `bash …/suite.sh <wt> …/handoffs/impl-suite-turn-1 <chunk>`, each chunk in the foreground:

   | Chunk | Result |
   | --- | --- |
   | 00 | `294 passed, 1 skipped, 736 warnings in 150.58s` |
   | 01 | `282 passed, 317 warnings in 86.87s` |
   | 02 | `339 passed, 3 skipped, 459 warnings in 107.44s` |
   | 03 | `330 passed, 43 skipped, 315 warnings in 227.17s` |
   | **Total** | **0 failed / 1245 passed / 47 skipped** |

   - Base was 0 / 1109 / 46. The new file adds 136 passed and 1 skipped, so there is no new failure.
   - The skips are the same external ones as base (live S3 opt-in, the Padyar contract, the Redis container, the
     SQLite-only drill) plus the root-only permission test.
   - Logs: `handoffs/impl-suite-turn-1/full-0{0..3}.txt`.
7. Compose check: `docker compose -f docker-compose.yml config --quiet` gives exit 1 without `NEO4J_PASSWORD` and
   exit 0 with a fake value (see AC5).

## Risks and findings for Foreman

1. **AC5 scan vs ownership.** `Homayar/README.md:94` still contains the removed dev literal. The file is outside the
   "Maker may change" list, so I left it. A one-line change to a placeholder would clear the scan. It needs an
   ownership amendment or a Foreman edit.
2. **AC4 vs "infra/compose already correct".** `infra/compose/compose.onprem.yaml` defaults
   `ENVIRONMENT=${APP_ENVIRONMENT:-production}`, `STORAGE_BACKEND=s3` and a hard-coded
   `STORAGE_ENDPOINT_URL: http://minio:9000`. With AC4/A3 as specified, an on-prem **production** start of the
   bundled profile now fails validation. CI only runs `compose config`, so CI is not affected.
   - I implemented the SPEC literally, with no on-prem exemption.
   - The deployment doc states the consequence.
   - It needs a Foreman decision: a follow-up to serve MinIO over TLS in the on-prem profile, or a SPEC amendment.
3. SMTP: the compose default is port 587 with `SMTP_USE_TLS=false`. `email_service` already runs STARTTLS on 587,
   but AC4 requires the explicit flag, so production operators who set `SMTP_HOST` must also set
   `SMTP_USE_TLS=true`. This is documented.
4. The bundled compose profiles pass plain `NAME` as required variables, so they cannot use `NAME_FILE` without an
   infra change (out of scope). This is documented.
5. As AC2 requires, errors echo the `NAME_FILE` path. If an operator puts a secret itself into `NAME_FILE`, the error
   shows it. The doc warns against this.
6. Settings validation errors now omit input values and the pydantic docs URL. The messages and types are unchanged.
7. The `Homayar/Makefile` `seed` target (`cd backend && .venv/bin/python -m database.seed`) was already
   non-functional at base (there is no `backend/.venv`). The working invocation is
   `uv run python -m backend.database.seed` from `Homayar/`, which the seed.py docstring states. The Makefile is
   outside ownership.

## Turn 1b (in progress; see turn-1b.md)
A6/A7/A8 implemented; focused command 171 passed, 1 skipped; full suite running into handoffs/impl-suite-turn-1b.
