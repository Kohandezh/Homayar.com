# Privacy gates (HOM-321)

Every `text_pii` privacy ticket (HOM-291, HOM-303, HOM-308, HOM-302, HOM-298)
built the same gate harness from scratch, and mission cleanup then deleted it.
This tool keeps one harness in the repo. Use it for any change to
`backend/privacy/text_pii.py`, `backend/privacy/subject_export.py`, or the
normalization they use (`backend/speech/text_normalize.py`). It runs the same
synthetic corpus through the privacy code of a base commit and of your working
tree, compares the results, and prints a pass or fail table.

Run every command from the `Homayar/` folder (the one with `pyproject.toml`).

## Commands

Run the gates against `origin/main` (the usual check before a PR):

```bash
uv run python -m backend.tests.tools.privacy_gates run --base origin/main
```

Run them against pre-HOM-291 `main` (`4577b25`) to see the harness detect a
real difference (the base side runs detector `text-pii/4`, the tree runs a
newer one, so the D lines are non-zero):

```bash
uv run python -m backend.tests.tools.privacy_gates run --base 4577b25
```

A bigger corpus (more rows per corpus) and a different seed:

```bash
uv run python -m backend.tests.tools.privacy_gates run --base origin/main --rows 50000 --seed 77
```

Write only the corpus files, without running any gate:

```bash
uv run python -m backend.tests.tools.privacy_gates corpus --out "$(mktemp -d)" --rows 50
```

Useful `run` options:

| Option | Default | Meaning |
| --- | --- | --- |
| `--base REV` | none | git revision whose `<prefix>/backend` is the base side |
| `--base-dir DIR` | none | a folder that holds `backend/privacy/text_pii.py`, used as the base side as is |
| `--tree-dir DIR` | the tree that holds this tool | the patch side |
| `--seed N` | `321` | corpus seed |
| `--rows N` | `20000` | rows per corpus |
| `--cost-rows N` | `500` | rows per cost workload (G5) |
| `--cost-limit X` | `1.20` | G5 limit for patch / base lines run |
| `--out DIR` | a new temp folder | where the corpus, the results and the reports go |

Exactly one of `--base` and `--base-dir` is required.

## What the tool checks

### Corpora

All values are synthetic and grow from one seeded random source, so the same
seed and row count give byte-identical files on any machine. The corpus holds
no real personal data.

| Name | Content |
| --- | --- |
| `dense` | number-dense rows: planted identifiers of every class, glued pairs (a national ID directly followed by a mobile), glued mobiles, and noise numbers |
| `words` | word-heavy rows with 0 to 2 planted identifiers and at most one noise number |
| `dsar` | subject export rows: 1 to 3 subject identifiers, some planted in the text, plus other people's identifiers, near misses (one digit changed) and noise |
| `prices` | price lists with no planted identifier, in three buckets by total digit count (20-25, 26-35, 36-60) |

Identifiers are written in plain and grouped forms, with the separators
`text_pii` reads (space, NBSP, hyphen, en dash), in ASCII, Persian and
Arabic-Indic digits. An e-mail address is always written as is, in ASCII. An
IBAN is compact or in groups of 4 joined by single spaces, and only its digit
script may change.

Noise numbers are not planted, and on purpose they can hold chance
identifiers (for example a random 10-digit run that passes the national-ID
check). They are how the gates see over-redaction. Recall counts planted
items only.

With `--rows` below 3 the `prices` corpus still holds 3 rows, one per bucket,
because every bucket needs at least one row.

### Class lists

Each row is read once per class list. The lists are fixed on both sides.

| Name | Classes |
| --- | --- |
| `all` | every `PiiClass` value |
| `speech` | phone, ir_national_id, email |
| `export` | email, phone, ir_national_id, bank_card, iban |
| `phone` | phone |
| `id` | ir_national_id |
| `card` | bank_card |
| `postal` | ir_postal_code |
| `phone+card` | phone, bank_card |

### Gates

A planted item is covered when every letter or digit position of it lies
inside some detected span of that side for that class list.

| Gate | Meaning | Pass when |
| --- | --- | --- |
| G1 | planted identifiers the patch leaves raw (covered on base, raw on patch) | count is 0 |
| G2 | positions inside a span on base and outside every span on patch | count is 0 |
| G3 | on dsar rows: new false subject-export matches, and lost true matches | both counts are 0 |
| G4 | price rows with at least one span, per bucket and class list | patch share is at most base share everywhere |
| G5 | lines run by the patch side, per workload | every patch/base ratio is at most `--cost-limit` |
| D | rows whose spans or export classes differ from base | information only, never fails |
| R | recall of planted items per side (covered/planted) | information only, never fails |

The table shows `Base`, `Patch`, the `Limit` and a `Result` of `PASS`, `FAIL`
or `info` per line. The last stdout line is `RESULT: PASS` or
`RESULT: FAIL (G1, G2)` with every failed gate. `report.json` and `report.md`
in the out folder hold the same numbers, plus up to 20 examples per failing
gate.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | every gate passed |
| 1 | at least one gate failed |
| 2 | usage or setup error: bad arguments, bad revision, missing `text_pii.py`, the module path guard (a side loaded the code from outside its own tree), or a worker crash. A usage error that argparse itself rejects (for example `--rows 0` or `--rows abc`) prints the usage line and one `error:` line. Every other setup error prints one stderr line and no table; for a worker failure that line ends with the worker's last stderr line. |

## Cost is lines run, not time

G5 counts the lines of Python code that one call runs in
`backend.privacy.text_pii`, `backend.privacy.subject_export` and
`backend.speech.text_normalize` (`backend/tests/_cost.py`, `lines_run`, from
HOM-310). The count is the same on every machine, so a slow CI runner cannot
fail a gate whose code did not change (HOM-307). Work the base code does in C
inside the regex engine is not Python code, so it is not counted.

## Where the output goes

The corpus, the per-side results and the reports go to the `--out` folder, a
new temp folder by default, never inside the repo by default. The base side
of `--base REV` is exported with `git archive` into a temp folder that is
removed when the run ends, also on failure. No corpus or result file is ever
committed.

## Run time

The default size (`run --base origin/main`, 20000 rows per corpus, 500 cost
rows) took 91 seconds on the maker's machine (Apple M3, Python 3.12.13),
measured with `/usr/bin/time -p` on 2026-10-29.

## CI smoke test

`test_smoke.py` runs in CI as part of `uv run pytest backend/tests --quiet -rs`
(no workflow change needed). It uses only pytest built-ins such as `tmp_path`,
no fixture from `backend/tests/conftest.py`, no database, no Redis, no network
and no time budget. It checks:

1. corpus determinism: the same seed and rows give byte-identical files, another seed gives different files;
2. ground truth: every planted item slices its raw text, and planted national IDs, cards and IBANs pass the `text_pii` validators;
3. every corpus exists and is not empty, price rows sit inside their bucket, dsar rows hold 1 to 3 subject identifiers;
4. end to end with base equal to the tree: exit 0 and `RESULT: PASS`;
5. a negative control where `detect` drops every phone span: exit 1, G1 and G2 fail;
6. a base dir without `text_pii.py`: exit 2, one stderr line, no table.

Run it alone:

```bash
uv run pytest backend/tests/tools -q -rs
```
