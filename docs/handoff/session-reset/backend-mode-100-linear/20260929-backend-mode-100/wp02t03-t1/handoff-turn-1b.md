# Handoff: wp02t03-t1-impl, turn 1b (contract corrections A6 to A8)

Worktree: /root/foreman/20260929-backend-mode-100/wt/wp02t03-t1. Nothing was committed. Everything from turn 1
(`handoffs/turn-1.md`) is kept. This file covers only what changed since turn 1.

## Changes since turn 1

### A6: `Homayar/README.md`

- Only line 94 changed.
- The old value is replaced by the placeholder `your-local-password`, plus a trailing comment that points to
  `NEO4J_PASSWORD_FILE` and to the "Secret references" section of `docs/operations/deployment.md`.
- The old value is not written anywhere. I did the replacement from a shell variable, and my output shows only
  the new line.

### A7: SMTP production rule (`Homayar/backend/config.py`)

- New rule: `if self.smtp_host and self.smtp_port not in (465, 587) and not self.smtp_use_tls: reject`.
- A comment points to `email_service`: 465 means implicit TLS, 587 always issues STARTTLS, and any other port uses
  TLS only when the flag is set.
- The message names SMTP_HOST, SMTP_PORT and SMTP_USE_TLS.
- Tests are updated:
  - Rejected: production with `smtp_host` set, `smtp_use_tls=False`, on ports 25, 1025 and 2525.
  - Accepted (A7 allow-controls):
    - port 587 with no flag, and 587 with the flag;
    - 465 with and without the flag;
    - port 25 with `smtp_use_tls=True`;
    - an empty `smtp_host`.
  - The no-secret-in-errors case and the development/test/staging case now use port 25.

### A8: object-store opt-in

- `config.py` has a new field `storage_allow_insecure_endpoint: bool = False` (env `STORAGE_ALLOW_INSECURE_ENDPOINT`),
  placed next to `storage_endpoint_url`. It is not a secret field.
- The production rule now rejects s3 with a non-empty, non-`https://` endpoint unless
  `storage_allow_insecure_endpoint and deployment_mode == "onprem"`. The message names STORAGE_ENDPOINT_URL,
  DEPLOYMENT_MODE=onprem and STORAGE_ALLOW_INSECURE_ENDPOINT=true.
- `infra/compose/compose.onprem.yaml`: I added one setting line with a one-line comment, directly after
  `STORAGE_ENDPOINT_URL: http://minio:9000` in `x-app-environment`. The comment is
  `# The bundled MinIO is reached over plain HTTP on the internal compose network only.`, and the setting is
  `STORAGE_ALLOW_INSECURE_ENDPOINT: "true"`. There is no other infra change.
- New tests:
  - Accepted: on-prem with the flag and an http endpoint (`http://` and `HTTP://`).
  - Rejected: cloud with the flag and an http endpoint.
  - Rejected: on-prem with an http endpoint and no flag; the error names STORAGE_ALLOW_INSECURE_ENDPOINT.
  - Accepted without the flag, in both cloud and on-prem: an https endpoint or an empty endpoint.
  - The flag defaults to off, and it is read from the environment (`STORAGE_ALLOW_INSECURE_ENDPOINT=true` with
    `DEPLOYMENT_MODE=onprem`).
  - The on-prem compose profile's own `DEPLOYMENT_MODE`, `STORAGE_ENDPOINT_URL` and
    `STORAGE_ALLOW_INSECURE_ENDPOINT` values pass the production rule. The test parses them from the file and
    skips if `infra/` is absent.
  - The existing rejection cases are unchanged. They default to `deployment_mode="cloud"`.

### Deployment doc (`docs/operations/deployment.md`)

- The SMTP bullet is rewritten to the A7 transport rule. The Compose default, 587 with `SMTP_USE_TLS=false`, is
  now stated as accepted.
- The object-store bullet documents:
  - the `STORAGE_ALLOW_INSECURE_ENDPOINT` flag, which defaults to false;
  - its on-prem-only scope: it has no effect with `DEPLOYMENT_MODE=cloud`, and without it the endpoint is refused;
  - why `compose.onprem.yaml` sets it;
  - the residual risk: plaintext object traffic, including recordings and credentials, on the internal network, so
    keep MinIO off published and shared networks;
  - the planned follow-up to serve MinIO over TLS.

## Verification (real output; pytest under `flock …/heavy.lock` after `source testenv.sh`, from `Homayar/`)

1. `uv run pytest backend/tests/test_config_secrets.py -q -p no:cacheprovider -rs` gives `150 passed, 1 skipped`.
   The skip is the root-only mode-000 test, as in turn 1.
2. The SPEC focused command
   `flock … uv run pytest backend/tests/test_config_secrets.py backend/tests/test_api.py -q -p no:cacheprovider`
   gives `171 passed, 1 skipped, 59 warnings in 18.94s`.
3. `uv run ruff check backend/config.py backend/tests/test_config_secrets.py && uv run ruff format --check …` gives
   `All checks passed!` and `2 files already formatted`. `seed.py` did not change in 1b and still has its 58
   pre-existing findings.
4. `uv run mypy backend/config.py backend/tests/test_config_secrets.py` reports no errors in either file. The other
   reported errors are pre-existing ones in untouched files.
5. On-prem Compose, using docker compose v5.1.1, with fake values exported for the required variables:
   - `docker compose --file infra/compose/compose.onprem.yaml --profile onprem config --quiet` gives exit 0.
   - The same with `--profile egress` gives exit 0.
   - The resolved environment of `api`, `worker` and `scheduler` is
     `ENVIRONMENT=production, DEPLOYMENT_MODE=onprem, STORAGE_BACKEND=s3, STORAGE_ENDPOINT_URL=http://minio:9000,
     STORAGE_ALLOW_INSECURE_ENDPOINT=true`.
   - I built `Settings(_env_file=None)` in a subprocess from the resolved `api` environment:
     - as resolved: `valid: production onprem http://minio:9000 True`;
     - with the flag line removed: `rejected: ValidationError … STORAGE_ENDPOINT_URL must be an https:// URL in production …`.
6. Full suite: `bash …/suite.sh <wt> …/handoffs/impl-suite-turn-1b <chunk>`, each chunk run in the foreground.

   | Chunk | Result |
   | --- | --- |
   | 00 | `308 passed, 1 skipped, 736 warnings in 150.49s` |
   | 01 | `282 passed, 317 warnings in 84.06s` |
   | 02 | `339 passed, 3 skipped, 459 warnings in 109.02s` |
   | 03 | `330 passed, 43 skipped, 315 warnings in 223.50s` |
   | **Total** | **0 failed / 1259 passed / 47 skipped** |

   Base was 0 / 1109 / 46, and the new test file adds 150 passed and 1 skipped. No `FAILED` or `ERROR` lines
   appear in any chunk log.
7. Literal scan: `git grep -i <removed literal>` over the worktree finds no occurrence. The `handoffs/` directory
   also has no occurrence.

## Change set now (7 files plus 1 new test)

- `Homayar/.env.example`
- `Homayar/README.md` (line 94 only)
- `Homayar/backend/config.py`
- `Homayar/backend/database/seed.py`
- `Homayar/docker-compose.yml`
- `docs/operations/deployment.md`
- `infra/compose/compose.onprem.yaml` (1 setting line and 1 comment line)
- `Homayar/backend/tests/test_config_secrets.py` (new)

## Remaining risks

- Plaintext object traffic on the on-prem internal network is accepted by design under A8. It is documented, and
  the follow-up is MinIO over TLS.
- These turn-1 items are unchanged:
  - The bundled compose profiles cannot use `NAME_FILE` without an infra change.
  - Errors echo the `NAME_FILE` path, as AC2 requires; the doc warns against putting a secret into `NAME_FILE`.
  - The `Homayar/Makefile` `seed` target was already stale at base.
