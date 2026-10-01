# Handoff: wp02t03-t1-impl, turn 2 (documentation only)

Worktree: /root/foreman/20260929-backend-mode-100/wt/wp02t03-t1. Nothing was committed. There are no code, test
or infra changes since turn 1b.

## Changes since turn 1b

### `docs/operations/deployment.md`, "Secret references"

1. **Names (low finding).** The multi-spelling rule is now limited to the process environment. The `.env` behaviour
   is stated plainly: the spelling that appears last in the file silently wins, as for any other `.env` entry, so
   each name should be written only once in `.env`.
   - Checked with fake values before writing the sentence. A `.env` with `JWT_SECRET_FILE=<a>` then
     `jwt_secret_file=<b>` gave the content of `<b>`, and the reversed order also gave `<b>`, so the last line wins.
2. **BOM.** The Newline bullet now states that a UTF-8 byte-order mark at the start of the file is kept as part of
   the value, so secret files should be written without one. Turn 1 already tests this: the `﻿…` case in
   `test_exactly_one_trailing_newline_is_removed`.
3. **Neo4j.** A new bullet explains who reads which variable:
   - `NEO4J_PASSWORD_FILE` is read by the backend and the development seed (`python -m backend.database.seed`),
     because both load Settings.
   - The Neo4j container in `Homayar/docker-compose.yml` takes its credential only from `NEO4J_PASSWORD`
     (environment or `Homayar/.env`).
   - So `NEO4J_PASSWORD` must be set for that container even when the backend uses the `_FILE` form.

### `Homayar/README.md`, line 94 only (A6 scope)

The comment now reads: `# docker-compose.yml's Neo4j needs this; the backend/seed may use NEO4J_PASSWORD_FILE
instead (docs/operations/deployment.md)`. The placeholder value `your-local-password` is unchanged.

## Verification

- Before editing, I recorded sha256 hashes of all 8 changed or new files as they were at turn 1b. Afterwards
  `sha256sum -c` reports only `Homayar/README.md: FAILED` and `docs/operations/deployment.md: FAILED`. The other 6
  files match turn 1b exactly: `.env.example`, `config.py`, `seed.py`, `docker-compose.yml`, `compose.onprem.yaml`
  and `test_config_secrets.py`.
- `diff` of README.md against turn 1b shows `94c94` only. `git diff -U0 Homayar/README.md` against base has one
  hunk, `@@ -94 +94 @@`.
- `git diff --stat`: 7 files changed, 381 insertions, 27 deletions. deployment.md went from +49 to +69; every other
  file's count is the same as turn 1b. `Homayar/backend/tests/test_config_secrets.py` is still untracked and new.
- Literal scan: `git grep -i <removed literal>` finds no occurrence in the repository.
- I did not re-run the tests, because no Python, config or test file changed (the hashes are identical). The turn 1b
  full-suite result still applies: 0 failed / 1259 passed / 47 skipped, logs in `handoffs/impl-suite-turn-1b/`.

## Turn 3 in progress (see turn-3.md): A9/A10 implemented test-first; full suite running into handoffs/impl-suite-turn-3.
