# Final security review, round 2 — wp02t03-t1 (HOM-32), reviewer wp02t03-t1-final

- Full patch: `REVIEW-INPUT.diff`, sha256 `3745215bd17f3489aa1f34ed38624af4c017213eba47030544faffe16f1dfc16`
  (verified).
- Delta: `REVIEW-DELTA.diff`, sha256 `d7873f37508f27e2effd8d1d393ad758b4e53309dc90854b9d4a0b4592a92b5f`
  (verified).
- Method: a fresh `git archive 3d64130` in scratch with the full turn-3 patch applied. I also applied
  `gates/maker-turn-2.patch` to a second fresh copy. `diff -ru` between the two touches exactly the four files
  the delta names (`config.py`, `seed.py`, `test_config_secrets.py`, `deployment.md`), and the delta applies
  cleanly to turn 2.
- I did not read the maker worktree, `handoffs/`, or `reviews/turn-*.md`. Every probe used fake values only.
  Line numbers refer to the turn-3 files.

## Verdict

**ACCEPTED.** A9 and A10 are implemented as written and hold on every spelling I tried, and the round-1 results
did not regress.

- The new tests are real evidence: 15 of them are red on turn-2 code and green on turn 3.
- What remains is informational:
  - The `/proc`/`/sys` refusal is a path check made before `open`, so a symlink swapped in between still gets
    through.
  - The seed guard trusts the `development` default when `ENVIRONMENT` is absent.
  - Round-1 items that are unchanged or already filed as follow-ups (HOM-159..HOM-162).
- None contradicts the SPEC.

## (a) A9: secret-file cap, stat before open, `/proc` and `/sys`

- **Cap: verified.** `SECRET_FILE_MAX_BYTES = 64 * 1024` (`config.py:52`). `_read_at_most`
  (`config.py:83-92`) reads with raw `os.read` up to `LIMIT + 1`, and the oversize case sets the error and
  `data = b""` (`config.py:122-125`). Results, with a spy on `os.read`:

  | File | Result | Bytes read |
  |---|---|---|
  | 65535 bytes | accepted | 65535 |
  | 65536 bytes | accepted | 65536 |
  | 65536 bytes + `\n` | refused | 65537 |
  | 65537 bytes | refused | 65537 |
  | 512 MiB sparse | refused | 65537 |

  The round-1 64 MiB case is now refused with 0 MiB RSS growth (previously +128 MiB). The error text is
  "…points to '<path>', which is larger than 64 KiB (65536 bytes)", with no content and no chaining.
- **Stat before open: verified.** `_pre_open_problem` (`config.py:65-80`, called at `:106`) stats the path
  before `os.open` (`:110`), and the post-open `fstat` `S_ISREG` check is kept (`:119`). With a spy on
  `os.open`, the following are all refused as "is not a regular file" with **no open call**: a FIFO,
  `/dev/zero`, a symlink to `/dev/null`, and a directory. `O_NONBLOCK` is kept for the swap case.
- **`/proc` and `/sys` after resolving symlinks: verified.** 26 spellings were refused as "resolves under /proc
  or /sys" with no open call:
  - Plain paths: `/proc/self/environ`, `/proc/<pid>/environ`, `/proc/thread-self/environ`, `/proc/./self/environ`.
  - Slash and `..` variants: `//proc/…`, `///proc/…`, `/etc/../proc/…`.
  - Relative paths: `proc/self/environ` with cwd `/`, and `../../../../../../../../proc/self/environ`.
  - Magic links: `/proc/self/root/proc/self/environ`, and `/proc/self/cwd/<link into /proc>`.
  - Symlinks into `/proc`: an absolute symlink, a relative `../…` symlink, a two-level symlink chain, and a
    symlinked directory to `/proc/self` followed by `/environ`.
  - `/dev` aliases of `/proc`: `/dev/fd/0` and `/dev/stdin` (both resolve into `/proc`), and `/etc/mtab`.
  - `/sys` paths: `/sys/class/net/lo/address`, `/sys/kernel/mm/transparent_hugepage/enabled`, and a symlink
    into `/sys`.
  - `/proc` itself.

  `/procfoo` is not matched ("does not exist"), and directories merely named `proc` are allowed. Legitimate
  fd-based passing still works: a memfd secret via `/proc/self/fd/N` (realpath `/memfd:… (deleted)`) and
  `/proc/self/fd/N` pointing at an ordinary file are both accepted with the right value.
- **New bypass or TOCTOU: one residual, informational (R2-I-1).**
  - The kernel-file refusal is decided on `os.path.realpath(path)` before `open` (`config.py:68-70`). After
    `open` the only check is `S_ISREG` (`:119`), and procfs files are `S_ISREG`.
  - Simulated race (symlink swapped just before the real `os.open`): a swap to `/proc/self/environ` was
    **accepted** (7891 bytes, the process environment). Swaps to a FIFO, `/dev/zero` or a 10 MiB file were
    still refused after `open`.
  - A procfs or sysfs mounted at another path would also pass the prefix test. Only `/proc` and `/sys` are
    mounted in this container, and mounting needs `CAP_SYS_ADMIN`.
  - Why this is only informational: the race needs write access to the directory holding the secret path,
    and that access already lets an attacker replace the secret itself (for example set a known
    `JWT_SECRET`). So it adds no capability, and the A9 text ("a path that resolves … under /proc or /sys") is
    met.
- **Error leaks: none.** Every new message names the variable and the given path only. There is no chaining
  (`__cause__`/`__context__` are None). The oversize path clears `data`.

## (b) A10: seed refuses outside development/test before any driver

- `seed.py:29` raises `SeedConfigurationError("ENVIRONMENT is '<value>'; …")`. This happens inside
  `neo4j_connection`, before `GraphDatabase.driver` (`seed.py:220`) and `DETACH DELETE` (`:222`).
- I ran it as `python -m`-equivalent (`runpy`, `__main__`) with `neo4j.GraphDatabase.driver` replaced by a
  recorder:
  - **Full production configuration** (32+ character JWT and pepper, PostgreSQL, secure cookies, fake
    Neo4j): refused with exit 1 and **0 driver calls** in each of these cases:
    - `ENVIRONMENT=production` from the env.
    - All variable names lower-cased.
    - `ENVIRONMENT=staging`.
    - `ENVIRONMENT=production` from `Homayar/.env`.
    - `Environment=production` (mixed-case name) in `.env`.
    - `.env` development overridden by env production.
  - **Value spellings** `Production`, `PRODUCTION`, `' production'`, `'production '`, `prod`, `Development`,
    `DEVELOPMENT`, `dev` and `''`: `Settings` validation fails first (the `Literal` type), with exit 1, 0
    driver calls, and an error that names `environment`.
  - **Incomplete production configuration**: the existing production validator stops it first, with 0 driver
    calls.
  - **Allowed**: `ENVIRONMENT=test`, `development`, env `development` over `.env` production, and
    `ENVIRONMENT` absent. In these cases the driver is opened.
- **Leaks: none.** The refusal output contains no fake secret marker and no traceback. Exit is non-zero.
- **Residual, informational (R2-I-2):** with `ENVIRONMENT` absent everywhere, `Settings` defaults to
  `development` (`config.py:252`), so the guard passes. A host that keeps production `NEO4J_*` in the shell
  or `Homayar/.env` but injects `ENVIRONMENT` only into containers can still have its graph wiped. The
  bundled compose profiles set `ENVIRONMENT`, so `docker compose exec` inside the app container is refused.
  This meets A10.

## (c) Round-1 regressions: none

- Oracle v2 (sha256 prefix `731339af85cfcca5`) plus the maker file on the fresh turn-3 copy: **753 passed, 1
  skipped**. That is 584 oracle and 169 maker cases; the skip is the root-only permission test.
- My round-1 probes run on both copies produce **byte-identical output** on turn 2 and turn 3:
  - `probe_leak`: repr/str/logging and 14 failing constructions, no leak.
  - `probe_combo`: 21 `X`/`X_FILE` source combinations.
  - `probe_prod`: 44 SMTP and object-store spellings.
  - `probe_brace`: brace-safe message rewrap.
- `ruff check` and `ruff format --check` are clean on `config.py` and the test file. `seed.py` keeps 58
  pre-existing findings (59 on base; CI does not lint it).
- **Evidence check.** Running the turn-3 test file against turn-2 code gives 15 failed / 154 passed / 1
  skipped.
  - The 15 red tests are the new ones: size cap ×2, pre-open ×3, kernel paths ×5, symlink into `/proc`, seed
    refusal ×2, seed reads the environment at run time, and the seed script exits non-zero.
  - The 4 new tests that pass on both are allow-controls: exactly 64 KiB, directories named `proc`/`sys`, and
    the seed running in development and in test.

## Findings

### R2-I-1. info, `Homayar/backend/config.py:68-70,110,119`: the `/proc`/`/sys` refusal is path-based and happens only before `open`.
- **Why it matters.** A symlink swapped between the realpath check and `os.open` delivers a procfs file,
  because the post-open check accepts any `S_ISREG`. A procfs or sysfs mounted outside `/proc` and `/sys` is
  not recognised.
- **Trigger.** A race that replaces the secret symlink with one to `/proc/self/environ` after
  `_pre_open_problem` returns. Simulated: the value became the process environment. This needs write access
  to the secret's directory, which already allows replacing the secret.
- **Smallest fix, if wanted.** After `open`, also refuse when `os.fstat(fd).st_dev` equals
  `os.stat("/proc").st_dev` or `os.stat("/sys").st_dev` (or use a `statfs` magic check). Keep the realpath
  pre-check.

### R2-I-2. info, `Homayar/backend/database/seed.py:29` with `Homayar/backend/config.py:252`: the seed guard trusts the `development` default.
- **Why it matters.** When `ENVIRONMENT` is not set anywhere the seed process can see, the guard passes and
  the seed wipes whatever `NEO4J_URI` it is given.
- **Trigger.** `.env` or the shell holds production `NEO4J_URI`/`NEO4J_PASSWORD` and no `ENVIRONMENT`
  (verified: driver opened).
- **Smallest fix, if wanted.** Also require `"environment" in settings.model_fields_set` (set explicitly), or
  an explicit confirmation flag.

### Carried from round 1: unchanged by this delta, not re-litigated, counted once each

| ID | Severity | Location | Status |
|---|---|---|---|
| L-2 | low | `config.py:621-625` in round-1 numbering (the production object-store rule), `storage/factory.py:23` | `AWS_ENDPOINT_URL[_S3]` or the shared-config endpoint bypasses the object-store rule. Filed as a follow-up (A10 note, HOM-159..162); out of scope. |
| I-1 | info | `config.py:122,133,191` | A locals-capturing renderer still sees `data` on the non-UTF-8 path and `variables` in `_resolve`. The oversize path now clears `data`. No such renderer is in the repo. |
| I-3 | info | `config.py` `_resolve`, `deployment.md` | Documented cross-place precedence is silent. Misspelled `*_FILE` names are ignored (pre-existing `extra="ignore"`). |
| I-4 | info | `config.py:47` | `model_dump()` exposes the secrets. Filed as a follow-up. |
| I-5 | info | `Homayar/README.md:94` | The `your-local-password` placeholder works as a password if copied verbatim. |
| I-6 | info | `deployment.md` Neo4j bullet | "Set `NEO4J_PASSWORD` for that container even when the backend uses `NEO4J_PASSWORD_FILE`" still leads to a `.env` conflict if both are put in `Homayar/.env`. |
| I-7 | info | `compose.onprem.yaml:39-41` | The on-prem opt-in accepts any non-https endpoint. MinIO TLS is a filed follow-up. |
| I-8 | info | `config.py` production branch | Plaintext redis, PostgreSQL and bolt are still accepted in production. Filed as a follow-up (pre-existing). |
| I-9 | info | git history | The removed dev Neo4j literal is still in history; rotate any instance that used it (pre-existing). |
| I-10 | info | `config.py:78,236` and others | Trailing inline comments; no behavioural effect. |

Round-1 L-1, I-2 and L-3 are **resolved** by A9 and A10, as verified above.

## Confirmed by the oracle (execution-backed)

- **Foreman** (`oracle/red-to-green.md`, "Maker turn 3"): oracle v2 584/584; full suite 0 failed / 1278 passed /
  47 skipped; literal scan 0 hits; ruff clean.
- **Mine**, on a fresh copy of base plus the full turn-3 patch:
  - Oracle v2 plus the maker file: 753 passed, 1 skipped.
  - The turn-3 tests on turn-2 code: 15 red (the new A9/A10 cases), so the new tests fail without the fix.
  - Probes: A9 size, pre-open, kernel-path and race probes; A10 seed matrix (27 runs); round-1 probes, whose
    output is identical on turn 2 and turn 3.

## Not verified

- I did not re-run the full backend suite. The 1278/0 result is taken from the Foreman gate record.
  - The record notes an intermittent `test_transport_drills` failure that was reproduced on base, and I did
    not examine it.
- The race in R2-I-1 was simulated by swapping inside an `os.open` wrapper, not won against a real concurrent
  writer.
- The alternative procfs mount point was reasoned about, not mounted.
- The permission-denied branches of `_pre_open_problem` under a non-root user; I ran everything as root.
- Non-Linux behaviour (`realpath` semantics, `O_NONBLOCK`, `/proc`).

CANDIDATES critical=0 high=0 medium=0 low=1 info=11
