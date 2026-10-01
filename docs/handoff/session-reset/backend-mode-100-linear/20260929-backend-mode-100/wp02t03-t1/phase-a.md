# Phase A oracle record — wp02t03-t1 (HOM-32 / WP02-T03)

Reviewer: wp02t03-t1-rev. Written from SPEC.md only, against the clean checker worktree at BASE_SHA 3d64130.
No maker worktree, handoff, diff, or other pane was read.

- Oracle: `/root/foreman/20260929-backend-mode-100/wt/wp02t03-t1-check/Homayar/backend/tests/test_oracle_wp02t03.py`
  (951 lines, sha256 `c051cdb0676a70e596ff242eb21d37c6b8646656a5a16c661c4da6545ecf548e`). The only change in the
  checker worktree (`git status`: `?? Homayar/backend/tests/test_oracle_wp02t03.py`). `ruff check` and
  `ruff format --check` pass on it.
- No source file, SPEC.md, or anything else was modified.

## Command and real output (base, under the mission lock)

```
cd /root/foreman/20260929-backend-mode-100/wt/wp02t03-t1-check/Homayar
source /root/foreman/20260929-backend-mode-100/testenv.sh
flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_oracle_wp02t03.py -q -p no:cacheprovider
```

```
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF [ 13%]
FFFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...F [ 27%]
FFFFFFFFFF...FFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFF [ 41%]
FFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF [ 55%]
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF......FFFFFFFFF.FF.FF.FF.FF.FF.FF.F [ 69%]
FFFF.FFFFF.FF.FF.FF.FF.FFFFF.FF.FFFFF.........F.FFFF......F.F..FFFF..... [ 82%]
........................................................................ [ 96%]
..............FF.                                                        [100%]
350 failed, 171 passed, 8 warnings in 7.15s
```

Exit status 1. The 8 warnings are `datetime.utcnow()` DeprecationWarnings raised by the base `backend/database/seed.py`
when the seed tests execute it; they are not from the oracle.

Hygiene checked after the run (booleans only, no values printed): the captured pytest output contains neither
the base's embedded Neo4j literals (compose password part, seed tuple strings) nor any testenv-exported secret
(`JWT_SECRET`, `TOKEN_PEPPER`, `NEO4J_PASSWORD`, `DATABASE_URL`, `TEST_DATABASE_URL`, `REDIS_URL`). The oracle file
contains none of those literals either. The seed-recorder fixture has a masked `repr` so pytest's long traceback
cannot render driver arguments.

## Red / green per test (base)

| Test | red | green |
| --- | --- | --- |
| TestSecretReferenceFiles::test_file_variable_supplies_the_value (15 fields x upper/lower/mixed) | 45 | 0 |
| TestSecretReferenceFiles::test_exactly_one_trailing_newline_is_removed (15 fields x 14 variants, pii minus 2) | 169 | 39 |
| TestSecretReferenceFiles::test_file_for_one_field_and_value_for_another_are_both_used | 1 | 0 |
| TestSecretReferenceFiles::test_file_values_satisfy_production | 1 | 0 |
| TestSecretReferenceFailures::test_value_and_file_together_are_rejected (15 fields) | 15 | 0 |
| TestSecretReferenceFailures::test_conflict_is_detected_in_any_spelling (3x3 spellings) | 9 | 0 |
| TestSecretReferenceFailures::test_only_the_file_variable_is_accepted | 1 | 0 |
| TestSecretReferenceFailures::test_missing_file_is_rejected_naming_variable_and_path (15) | 15 | 0 |
| TestSecretReferenceFailures::test_existing_file_for_the_same_variable_is_accepted (15) | 15 | 0 |
| TestSecretReferenceFailures::test_unreadable_path_is_rejected_naming_variable_and_path (15) | 15 | 0 |
| TestSecretReferenceFailures::test_empty_file_fails_the_production_length_check (2 fields x 3 contents) | 0 | 6 |
| TestSecretReferenceFailures::test_long_enough_file_passes_the_production_length_check (2) | 2 | 0 |
| TestNoSecretExposure::test_repr_and_str_hide_every_secret (staging/production x 3 sources) | 6 | 0 |
| TestNoSecretExposure::test_production_validator_errors_hide_every_secret (12 rules x 3 sources) | 26 | 10 |
| TestNoSecretExposure::test_non_production_validator_errors_hide_every_secret (6 rules x 3 sources) | 13 | 5 |
| TestProductionSmtpTransport::test_production_rejects_smtp_without_tls (587/25/2525/default) | 4 | 0 |
| TestProductionSmtpTransport::test_production_rejects_plaintext_smtp_from_environment | 1 | 0 |
| TestProductionSmtpTransport::test_production_accepts_smtp_with_tls (587/25/465) | 0 | 3 |
| TestProductionSmtpTransport::test_production_accepts_implicit_tls_port | 0 | 1 |
| TestProductionSmtpTransport::test_production_accepts_smtp_disabled | 0 | 3 |
| TestProductionSmtpTransport::test_non_production_keeps_plaintext_smtp (development/test) | 0 | 2 |
| TestProductionSmtpTransport::test_production_accepts_tls_smtp_from_environment | 0 | 1 |
| TestProductionObjectStoreEndpoint::test_production_rejects_non_https_s3_endpoint (http/HTTP/schemeless/ftp) | 4 | 0 |
| TestProductionObjectStoreEndpoint::test_production_rejects_plain_http_endpoint_from_environment | 1 | 0 |
| TestProductionObjectStoreEndpoint::test_production_accepts_https_or_provider_default (""/https x2) | 0 | 3 |
| TestProductionObjectStoreEndpoint::test_non_production_keeps_plain_http_endpoint (development/test) | 0 | 2 |
| TestProductionObjectStoreEndpoint::test_production_local_storage_ignores_the_endpoint | 0 | 1 |
| TestProductionObjectStoreEndpoint::test_production_accepts_https_endpoint_from_environment | 0 | 1 |
| TestNoEmbeddedCredentials::test_development_compose_takes_neo4j_auth_from_a_required_reference | 1 | 0 |
| TestNoEmbeddedCredentials::test_infra_compose_jwt_secret_is_recognised_as_a_required_reference (cloud/onprem) | 0 | 2 |
| TestNoEmbeddedCredentials::test_seed_connects_with_the_configured_neo4j_settings | 1 | 0 |
| TestNoEmbeddedCredentials::test_seed_reads_the_password_through_a_secret_reference | 1 | 0 |
| TestNoEmbeddedCredentials::test_seed_refuses_to_run_without_a_password (empty/absent) | 2 | 0 |
| TestPlainValuesStillWork::test_plain_environment_variable (15 x upper/lower/mixed) | 0 | 45 |
| TestPlainValuesStillWork::test_dotenv_file (15 x upper/lower key) | 0 | 30 |
| TestPlainValuesStillWork::test_unset_field_keeps_its_default (15) | 0 | 15 |
| TestPlainValuesStillWork::test_plain_values_satisfy_production | 0 | 1 |
| TestDocumentation::test_deployment_doc_has_a_secret_references_section | 1 | 0 |
| TestDocumentation::test_env_example_documents_file_references_in_one_comment_block | 1 | 0 |
| TestDocumentation::test_env_example_assigns_no_file_reference | 0 | 1 |

Red reasons were spot-checked with `--tb=line`. Each failure has the intended cause, not a harness fault:
- The base ignores `X_FILE`, so the value is `''` or the default. Conflict, missing and directory cases show
  `DID NOT RAISE`.
- `repr` and `str` show all 15 secrets.
- Argument-sourced validator errors leak the tail of the truncated `input_value`, which is the `sentry_dsn`
  credential. File-sourced cases fail the paired valid-baseline gate because the files are never read.
- The SMTP and S3 rules show `DID NOT RAISE`.
- `NEO4J_AUTH` is not a required reference.
- The seed connects with its literal URI and auth, and runs with an empty password.
- The docs section and the `.env.example` block are absent.

### Every green case, and why it is green on the base

- **Allow-controls and regression controls (intended green before and after):**
  - All `TestPlainValuesStillWork` cases (AC6: plain env in any case, `.env` upper/lower keys, unchanged defaults,
    plain values satisfy production).
  - All AC4 accept cases:
    - production SMTP with TLS, port 465, or no host;
    - production S3 with an empty or `https://` endpoint;
    - local storage in production ignores the endpoint;
    - development and test keep plaintext SMTP and an `http://` endpoint;
    - the env-sourced TLS and `https` accepts.
  - The infra compose `JWT_SECRET` references pass the same classifier the development compose must pass (AC5
    control).
  - `.env.example` has no `*_FILE=` assignment (AC7 "no real values" control).
- **Inherently indistinguishable on the base (kept on purpose, they guard the fix):**
  - `test_exactly_one_trailing_newline_is_removed[<field>-empty|only_lf|only_crlf]` for the 13 fields whose default
    is `""`. The expected value `""` equals the unset default, so a base that ignores the file looks correct. The
    same variants are red for `database_url` and `redis_url` (non-empty defaults). After the fix they pin
    "empty file -> empty value".
  - `test_empty_file_fails_the_production_length_check` (6). An empty file and an unset variable give the same `""`
    on the base, so production is already refused. The paired allow-control
    `test_long_enough_file_passes_the_production_length_check` is red on the base.
  - `test_production_validator_errors_hide_every_secret[*-environment]` (10) and
    `test_non_production_validator_errors_hide_every_secret[*-environment]` (5). With env-sourced secrets, the
    base's merged input puts env keys first and init kwargs last. Pydantic shows only about 25 leading and 24
    trailing characters of `input_value`, so no 12-character window of a secret reaches the text. The base does
    not leak for these inputs; it is a true negative, not a harness gap. After the fix they catch an
    implementation whose own error or chained cause renders env-sourced values. The same rules are red for
    argument-sourced and file-sourced input. The env-sourced rules that need new behaviour are also red
    (SMTP, S3 `http`, and `pii_keyring_malformed`, where the rule value is the trailing kwarg).

## Design notes a Phase-C reader needs

- **Isolation.** An autouse fixture deletes every environment variable, in any case, whose name, or name minus
  `_FILE`, is a `Settings` field. It resets the settings cache before and after each case. Every `Settings(...)`
  in the oracle uses `_env_file=None` except the `.env` cases, which pass an explicit temporary file.
- **Fake values.** All values are fake, distinct per field, and attributable:
  - the pattern is `fake-<field>-<field letters repeated>`;
  - URL fields carry the fake value in the credential position, on `.invalid` hosts;
  - `pii_mapping_keys` is `fakekey<tag>:<base64 of b"fake-pii-" padding>`, with the matching active key id
    passed alongside.
- **Leak detector.** A field counts as leaked if its whole value, or any 12-character window of its sensitive part,
  appears in the text.
  - The sensitive part of `database_url` and `redis_url` is the password. For `sentry_dsn` it is the whole DSN,
    on a unique fake host. For `pii_mapping_keys` it is the whole value plus the key material. For every other
    field it is the whole value.
  - "Text" means `str()` and `repr()` of the exception and of every exception in its printed chain: `__cause__`,
    or `__context__` unless suppressed. This matches what an uncaught startup error writes to logs.
  - Windows are needed because pydantic truncates `input_value` and exposes only fragments.
- **Validator errors, AC3.** Each error case first builds the same inputs without the rule and asserts that they
  are accepted and that every secret loaded (the allow gate). It then applies the rule and asserts a `ValueError`
  whose text leaks nothing. Covered rules:
  - production: every existing production rule, the two AC4 rules, and the Google and S3 credential rules raised
    in production;
  - non-production: fallback pair, unknown PII active key, malformed PII keyring, retention range, Google
    credentials, S3 credentials.
- **Minimal valid production set.** Copied from existing tests (`test_legacy_retirement._settings("production")`):
  - `database_url` is PostgreSQL, and `jwt_secret` and `token_pepper` are each at least 32 characters;
  - `secure_cookies=True`;
  - `enable_demo_account`, `allow_mock_providers` and `auto_create_schema` are all `False`.
- **Seed (AC5).**
  - `neo4j.GraphDatabase.driver` is replaced with a recorder that returns an inert fake driver, so no real Neo4j
    connection is made. The script runs as `__main__` via `runpy.run_path`, with `sys.argv` reset.
  - Pass means the driver received `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` (or `NEO4J_PASSWORD_FILE`) from
    the environment through `Settings`. The auth may be a tuple or a neo4j `Auth`.
  - Refusal means an exception or a non-zero `SystemExit`, with no driver opened, for both an empty and an absent
    password.
- **Compose (AC5).** Structural only, and no literal is written. `NEO4J_AUTH` must be a required reference, either
  `${VAR:?msg}` / `${VAR?msg}` as the whole value or as the part after `user/`. A `${VAR:-default}` fails.

## Interpretations the implementer could contest (all derived from SPEC wording)

1. "exactly one trailing `\n` (or `\r\n`) removed and nothing else altered" means:
   - a lone trailing `\r` is kept;
   - `v\n\r\n` becomes `v\n`;
   - an internal `\r\n` is kept, so reading in universal-newline text mode fails;
   - surrounding spaces and tabs are kept.
   `pii_mapping_keys` skips only the two internal-newline variants, because they would be invalid keyrings rather
   than a newline question.
2. "any case" includes mixed case, both for `X_FILE` and for the plain `X` in the conflict matrix.
3. "A missing or unreadable `X_FILE`" includes a directory path. The error must contain `str(path)` verbatim and
   the field name, in any case.
4. S3 "not `https://`" rejects `HTTP://`, a schemeless `host:port`, and `ftp://`.
5. The SMTP deny cases also require the word "smtp" in the error text. AC6 checks that unset secret fields keep the
   base defaults (`sqlite:///./homayar-dev.db`, `redis://localhost:6379/0`, and `""` for the rest).
6. The seed "refuses to run" only if it fails (an exception or a non-zero exit) before opening a driver. A silent
   exit 0 counts as not refusing.
7. For AC7, the doc section is the heading containing "secret references" through the next heading of the same or
   a higher level. It must mention `_file`, all 15 field names, a newline term, a conflict term, SMTP and TLS, and
   https and endpoint. In `.env.example`, every line mentioning `_FILE` must be a comment, and all of them must
   sit in one contiguous comment block (blank lines allowed).

## Not specified by SPEC, deliberately not tested

- `X=""` together with `X_FILE`: is it a conflict or not?
- An empty `X_FILE=""` path.
- `X_FILE` given inside a `.env` file.
- An init keyword argument together with `X_FILE`.
- Non-ASCII or other file encodings.
- An upper-case `HTTPS://` endpoint.
- Staging behaviour of the AC4 rules.
- A whitespace-only Neo4j password.

## Acceptance criteria -> covering cases

- **AC1 (secret references for all 15 fields, any case, one trailing LF/CRLF removed):**
  - `TestSecretReferenceFiles::test_file_variable_supplies_the_value`;
  - `::test_exactly_one_trailing_newline_is_removed`;
  - `::test_file_for_one_field_and_value_for_another_are_both_used`;
  - `::test_file_values_satisfy_production`.
- **AC2 (conflict and failure are loud and do not leak; empty file gives an empty value):**
  - `TestSecretReferenceFailures::test_value_and_file_together_are_rejected` and
    `::test_conflict_is_detected_in_any_spelling`, allow `::test_only_the_file_variable_is_accepted`;
  - `::test_missing_file_is_rejected_naming_variable_and_path` and
    `::test_unreadable_path_is_rejected_naming_variable_and_path`, allow
    `::test_existing_file_for_the_same_variable_is_accepted`;
  - `::test_empty_file_fails_the_production_length_check`, allow
    `::test_long_enough_file_passes_the_production_length_check`;
  - empty value in `test_exactly_one_trailing_newline_is_removed[*-empty]`.
- **AC3 (no secret in repr/str or error text; environment still shown):**
  - `TestNoSecretExposure::test_repr_and_str_hide_every_secret`;
  - `::test_production_validator_errors_hide_every_secret`;
  - `::test_non_production_validator_errors_hide_every_secret`;
  - plus the leak assertions in the AC2 and AC4 deny cases.
- **AC4 (production SMTP TLS and S3 https endpoint; development and test unchanged):**
  - `TestProductionSmtpTransport` (all 7 tests);
  - `TestProductionObjectStoreEndpoint` (all 6 tests).
- **AC5 (no embedded literal credentials):**
  - `TestNoEmbeddedCredentials::test_development_compose_takes_neo4j_auth_from_a_required_reference`, control
    `::test_infra_compose_jwt_secret_is_recognised_as_a_required_reference`;
  - `::test_seed_connects_with_the_configured_neo4j_settings`;
  - `::test_seed_reads_the_password_through_a_secret_reference`;
  - `::test_seed_refuses_to_run_without_a_password`;
  - the repository literal-string scan has no oracle case; Foreman runs it.
- **AC6 (existing behaviour preserved):**
  - `TestPlainValuesStillWork` (all 4 tests);
  - the "full backend suite, no new failure" part has no oracle case; it is Foreman's full-suite gate.
- **AC7 (documentation):**
  - `TestDocumentation::test_deployment_doc_has_a_secret_references_section`;
  - `::test_env_example_documents_file_references_in_one_comment_block`;
  - control `::test_env_example_assigns_no_file_reference`.

---

## Phase A update (SPEC amendments A6–A8; still blind)

Trigger: `briefs/rev-phase-a-update.md`. I read only SPEC.md Amendments A1–A8 and the base checker worktree. I did
not read the maker worktree, any handoff, or any diff. Only the oracle file changed; `git status` in the checker
worktree still shows only `?? Homayar/backend/tests/test_oracle_wp02t03.py`. `ruff check` and `ruff format --check`
are clean.

Oracle after the update: 1100 lines, sha256 `731339af85cfcca57381a51e9b0b639a691b22c901bea48e04e5f9f74e23c4ed`
(replaces `c051cdb0…`).

### Command and real output (base 3d64130, under the mission lock)

```
cd /root/foreman/20260929-backend-mode-100/wt/wp02t03-t1-check/Homayar
source /root/foreman/20260929-backend-mode-100/testenv.sh
flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_oracle_wp02t03.py -q -p no:cacheprovider
```

```
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF [ 12%]
FFFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...F [ 24%]
FFFFFFFFFF...FFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFF [ 36%]
FFFFFF...FFFFFFFFFFF...FFFFFFFFFFF...FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF [ 49%]
FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF......FFFFFFFFF.FF.FF.FF.FF.FF.FF.F [ 61%]
FFFFFFFFFF.FFFFF.FF.FF.FF.FF.FFFFF.FF.FFFFFFFFF..............FFFF.....FF [ 73%]
FFFFFFFFFFFFFFFFFFFFFF................FFFFFFFFF.F..FFFFF................ [ 86%]
........................................................................ [ 98%]
.....FF.                                                                 [100%]
392 failed, 192 passed, 8 warnings in 8.68s
```

Exit 1. Totals were 521 cases (350 red, 171 green) and are now 584 cases (392 red, 192 green). The 8 warnings are
still the base seed.py `utcnow` deprecations.

Hygiene was re-checked with booleans only. Neither the oracle nor the captured output contains:
- the base compose, seed or README Neo4j literals;
- any testenv secret.

### Changes by amendment

**A7 (SMTP: port 587 without the flag is STARTTLS, so it must be accepted).** The two invalidated cases were
`test_production_rejects_smtp_without_tls[587]` and the port-587 `test_production_rejects_plaintext_smtp_from_environment`.
Deny cases, red on base (DID NOT RAISE):
- `test_production_rejects_smtp_without_tls` -> `test_production_rejects_plaintext_smtp`: ports 25, 2525, 1025 and
  the default, each with TLS false and with TLS unset (8 cases).
- `test_production_rejects_plaintext_smtp_from_environment`: now 4 environment spellings (25/`false`, 2525/unset,
  unset/unset, 1025/`0`).
- Per A3, SMTP deny errors must now name a setting (`smtp_host`, `smtp_port` or `smtp_use_tls`), not just "smtp".
- AC3 rule `smtp_without_tls`: port 587 -> 25. Still red for arguments and files sources, green for environment,
  as before.

Allow-controls, green on base:
- `test_production_accepts_smtp_with_tls` and `test_production_accepts_implicit_tls_port` are replaced by
  `test_production_accepts_starttls_and_implicit_tls_ports_without_the_flag` (587 and 465, TLS false or unset, 4)
  and `test_production_accepts_any_port_with_the_tls_flag` (25, 2525, 587, 465, default, 5).
- `test_production_accepts_tls_smtp_from_environment` -> `test_production_accepts_encrypted_smtp_from_environment`
  (587/unset, 587/`false`, 465/`false`, 25/`true`, 2525/`TRUE`, 5).
- `test_non_production_keeps_plaintext_smtp`: port 587 -> 25, so it stays a real plaintext control.
- `test_production_accepts_smtp_disabled`: unchanged.

**A8 (S3 endpoint: on-prem opt-in `storage_allow_insecure_endpoint`, default false).**

Deny cases, red on base:
- `test_production_rejects_non_https_s3_endpoint` -> `test_production_rejects_non_https_endpoint_without_the_onprem_opt_in`:
  24 cases, DID NOT RAISE. Mode/flag combinations are cloud with no flag, cloud with flag false, cloud with flag
  true, default mode with flag true, onprem with no flag, and onprem with flag false. Each runs against `http://`,
  `HTTP://`, a scheme-less `host:port`, and `ftp://`. Per A3 the error must name `storage_endpoint_url`, and it must
  leak no secret.
- `test_production_rejects_plain_http_endpoint_from_environment`: 4 cases, DID NOT RAISE (cloud with `true`,
  default mode with `true`, onprem with the flag unset, onprem with `false`).
- AC3 adds rules `s3_plain_http_endpoint_flag_in_cloud` and `s3_plain_http_endpoint_onprem_without_flag` across 3
  sources: 6 cases, all red.

New-setting cases, red on base with AttributeError:
- `test_insecure_endpoint_flag_defaults_to_false` (1).
- `test_production_onprem_opt_in_from_environment`: onprem, `http://minio:9000`, the flag set through
  `STORAGE_ALLOW_INSECURE_ENDPOINT` or lower-case `storage_allow_insecure_endpoint`, with values `true`, `TRUE` and
  `1` (4). It asserts the flag reads `True` and the endpoint is accepted.

Allow-controls, green on base:
- `test_production_onprem_opt_in_accepts_non_https_endpoint`: onprem with flag true against `http://minio:9000` and
  the four non-https forms (5). Green on base because the base has no rule and ignores the unknown keyword.
- `test_production_accepts_https_or_provider_default_without_the_flag`: empty or `https://`, cloud and onprem (6).
- `test_non_production_keeps_plain_http_endpoint`: development and test, cloud and onprem (4).
- Local storage ignores the endpoint, and the env `https://` accept: unchanged.

The isolation fixture now also clears `STORAGE_ALLOW_INSECURE_ENDPOINT` in any case. It is not a `Settings` field
on the base, so the field-based sweep would miss it.

**A6 (README `NEO4J_PASSWORD`).** Implemented structurally, without writing the literal, as
`TestReadmeCredentialPlaceholders`:
- `test_readme_documents_neo4j_password_as_a_placeholder` (red on base). Every `NEO4J_PASSWORD=` line in
  `Homayar/README.md` must exist, and its value, before any ` #` comment, must be placeholder-shaped:
  - empty; or
  - wrapped in `<>`, `[]` or `${}`/`{}`; or
  - containing one of your, change, placeholder, example, replace, random, generate, set-, set_, xxx, `***`,
    `...`, redacted, required, strong, secret-ref, `see `.
  Before relying on it, I confirmed with a boolean only that the base value is not placeholder-shaped.
- Control `test_readme_existing_placeholders_are_recognised`: the README's own `ZAI_API_KEY`/`GAPGPT_API_KEY`
  `your-key` placeholders pass the same classifier (2, green).
- This is a heuristic. Foreman's literal-string repository scan stays authoritative for A6/AC5. A placeholder
  outside that vocabulary would be a false red to look at in Phase C.

A1–A5 needed no oracle change. They confirm interpretations already encoded: mixed case, lone `\r` kept, a
directory counts as unreadable, `HTTP://`/`ftp://`/scheme-less rejected, the seed raises or exits non-zero before
opening a driver and uses `GraphDatabase.driver(uri, auth=(user, password))`, `${VAR:?message}` compose, and
defaults unchanged. The A5 maker-choice items were already untested.

### Updated AC mapping (only changed lines)

- **AC4 (as amended by A3, A7 and A8):**
  - `TestProductionSmtpTransport` (7 tests): test_production_rejects_plaintext_smtp,
    test_production_rejects_plaintext_smtp_from_environment,
    test_production_accepts_starttls_and_implicit_tls_ports_without_the_flag,
    test_production_accepts_any_port_with_the_tls_flag, test_production_accepts_smtp_disabled,
    test_production_accepts_encrypted_smtp_from_environment, test_non_production_keeps_plaintext_smtp.
  - `TestProductionObjectStoreEndpoint` (9 tests):
    test_production_rejects_non_https_endpoint_without_the_onprem_opt_in,
    test_production_rejects_plain_http_endpoint_from_environment,
    test_production_onprem_opt_in_accepts_non_https_endpoint, test_production_onprem_opt_in_from_environment,
    test_insecure_endpoint_flag_defaults_to_false,
    test_production_accepts_https_or_provider_default_without_the_flag,
    test_production_accepts_https_endpoint_from_environment, test_non_production_keeps_plain_http_endpoint,
    test_production_local_storage_ignores_the_endpoint.
  - AC3 leak coverage for both rules: `TestNoSecretExposure::test_production_validator_errors_hide_every_secret`
    `[smtp_without_tls|s3_plain_http_endpoint|s3_plain_http_endpoint_flag_in_cloud|s3_plain_http_endpoint_onprem_without_flag-*]`.
- **AC5 plus A6:** add `TestReadmeCredentialPlaceholders::test_readme_documents_neo4j_password_as_a_placeholder`
  (control `::test_readme_existing_placeholders_are_recognised`). The repository literal scan is still Foreman's.
