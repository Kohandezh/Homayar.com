# Handoff: capdoc-t1, turn 2 (fix round; last round)

- FROM: capdoc-t1-impl. TO: foreman. Brief: `capdoc-t1/briefs/impl-turn-2.md`.
- Worktree `/root/foreman/20260929-backend-mode-100/wt/capdoc-t1`, branch `mission/capdoc-t1` at `7112407`. **Not committed.**
- Only file changed: `Homayar/AppVision.md`. Relative to turn 1 (sha256 `dfdeb9fa…7955`), **17 lines changed in place, 0 added, 0 removed** (still 1841 lines). New sha256: `d36daf49b04770cf5c0c66d0aba557503c98115bfcfd4b817e015e5ba369c599`.
- Scope: findings 1–5 (low) with the smallest fix each names, plus info 7 (one-clause link). Info 6 needs no change. Nothing else was edited.
- Verification before editing: finding 1 confirmed (`config.py:457`, `processor.py:445`, `resilience.py:15-17`, `ProviderHealth.window_count`, `_FALLBACK_CODES` includes `RATE_LIMITED` at `processor.py:436`). Finding 2 confirmed: ledger WP01-T03 is `verified_status: VALIDATED`. Finding 3 confirmed: the i18n grep returns nothing, and ledger WP22-T01 is PLANNED, "messages English-only". Finding 4 confirmed: the plan orders `WP13-T07 → N15 → {N05, X24}`. Finding 5 confirmed: the §10 legend is LIB/SVC/MODEL/API/CUSTOM. Finding 7: the archived ledger in the repo (`docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/ledger.md:104`) contains "landed diff == reviewed diff (script)".

## Fixes (line numbers in the current file; before → after)

### Finding 1 (N08 foundation): line 522
```diff
- - **Existing foundation:** processing-quota reservation before work and single settlement (`backend/services/entitlement_service.py`, tables `usage_reservations` and `usage_events`). The only budgets in the job pipeline are retry budgets; no provider-spend budget exists yet.
+ - **Existing foundation:** processing-quota reservation before work and single settlement (`backend/services/entitlement_service.py`, tables `usage_reservations` and `usage_events`); a per-endpoint request-per-minute window (`provider_rate_limits`, `backend/providers/resilience.py`), which limits call count, not spend. No provider-spend budget exists yet.
```

### Finding 2 (N11 WP01-T03): line 568
```diff
- - **Linear:** [HOM-152](https://linear.app/homayar/issue/HOM-152); follow-up [HOM-158](https://linear.app/homayar/issue/HOM-158). **Matrix:** 6.6. **ID decision:** extends F01 (release/source provenance). F01's work package WP01 holds task WP01-T03, "keep legacy routes disabled and document compatibility boundaries", which this capability completes. D18 (existing code recovery) is related.
+ - **Linear:** [HOM-152](https://linear.app/homayar/issue/HOM-152); follow-up [HOM-158](https://linear.app/homayar/issue/HOM-158). **Matrix:** 6.6. **ID decision:** extends F01 (release/source provenance). F01's work package WP01 holds task WP01-T03, "keep legacy routes disabled and document compatibility boundaries", which this capability extends (the mission planning ledger records WP01-T03 as validated before N11). D18 (existing code recovery) is related.
```

### Finding 3 (N03 mode bullet): line 411
```diff
- - **Deployment modes:** S Y · P Y · H Y · O Y. Policy evaluation and receipts are local and need no call-out.
+ - **Deployment modes:** S Y · P Y · H Y · O Y. Policy evaluation and receipts must be local and need no call-out.
```

### Finding 3 (N04 mode bullet): line 431
```diff
- - **Deployment modes:** S Y · P Y · H Y · O Y. Metrics are computed locally.
+ - **Deployment modes:** S Y · P Y · H Y · O Y. Metrics must be computed locally.
```

### Finding 3 (N13 mode bullet): line 610
```diff
- - **Deployment modes:** S Y · P Y · H Y · O Y. Message catalogs ship with the release.
+ - **Deployment modes:** S Y · P Y · H Y · O Y. Message catalogs must ship with the release.
```

### Finding 3 (N14 mode bullet): line 630
```diff
- - **Deployment modes:** S Y · P Y · H Y · O Y. The policy is served locally.
+ - **Deployment modes:** S Y · P Y · H Y · O Y. The policy must be served locally.
```

### Finding 4 (N08 dependencies): line 512
```diff
- - **Dependencies:** F18 (WP18-T02); K04; GR13; GR29; GR31; X11; G04 (override approval); N05 (estimates).
+ - **Dependencies:** F18 (WP18-T02); K04; GR13; GR29; GR31; X11; G04 (override approval). Consumers include N05 (estimates).
```

### Finding 4 (N15 dependencies): line 651
```diff
- - **Dependencies:** F20; F81 (WP13-T07); K07; GR11; GR32; X24 (rights inventory); N05.
+ - **Dependencies:** F20; F81 (WP13-T07); K07; GR11; GR32; X24 (rights inventory). Consumers include N05.
```

### Finding 4 (N15 matrix row): line 319
```diff
- | N15 | Model and prompt rollback: immutable versions, active-version pointer, staged promotion and rollback with affected-result lineage; extends F20 | Y | Y | Y | Y | F20; F81; K07; GR11; N05 | No mutable "latest" in production; audited rollback to the previous accepted version; affected results listed |
+ | N15 | Model and prompt rollback: immutable versions, active-version pointer, staged promotion and rollback with affected-result lineage; extends F20 | Y | Y | Y | Y | F20; F81; K07; GR11 | No mutable "latest" in production; audited rollback to the previous accepted version; affected results listed |
```

### Finding 5 (Meltano): line 1067
```diff
- | [Meltano](https://github.com/meltano/meltano) | Tool / ALL conditional | N02 alternative built on Singer connectors |
+ | [Meltano](https://github.com/meltano/meltano) | LIB / ALL conditional | N02 alternative built on Singer connectors |
```

### Finding 5 (Semgrep): line 1081
```diff
- | [Semgrep](https://github.com/semgrep/semgrep) | Tool / ALL with local rules | N11 forbidden-path and code-pattern checks |
+ | [Semgrep](https://github.com/semgrep/semgrep) | LIB / ALL with local rules | N11 forbidden-path and code-pattern checks |
```

### Finding 5 (jscodeshift): line 1082
```diff
- | [jscodeshift](https://github.com/facebook/jscodeshift) | Tool / ALL | N11 JavaScript codemods; frontend use is outside this backend scope |
+ | [jscodeshift](https://github.com/facebook/jscodeshift) | LIB / ALL | N11 JavaScript codemods; frontend use is outside this backend scope |
```

### Finding 5 (ast-grep): line 1083
```diff
- | [ast-grep](https://github.com/ast-grep/ast-grep) | Tool / ALL | N11 structural code search and rewrite |
+ | [ast-grep](https://github.com/ast-grep/ast-grep) | LIB / ALL | N11 structural code search and rewrite |
```

### Finding 5 (Pa11y): line 1085
```diff
- | [Pa11y](https://github.com/pa11y/pa11y) | Tool / ALL conditional | N13 future frontend accessibility verification only |
+ | [Pa11y](https://github.com/pa11y/pa11y) | LIB / ALL conditional | N13 future frontend accessibility verification only |
```

### Finding 5 (Playwright): line 1086
```diff
- | [Playwright](https://github.com/microsoft/playwright) | Tool / ALL with preloaded browsers | N14 browser verification of the support policy |
+ | [Playwright](https://github.com/microsoft/playwright) | LIB / ALL with preloaded browsers | N14 browser verification of the support policy |
```

### Finding 5 (MDN): line 1088
```diff
- | [MDN browser-compat-data](https://github.com/mdn/browser-compat-data) | Data / ALL as a dated snapshot | N14 reference for browser capability data |
+ | [MDN browser-compat-data](https://github.com/mdn/browser-compat-data) | LIB / ALL as a dated snapshot | N14 reference for browser capability data |
```

### Finding 7 (N11 landing-check link): line 581
```diff
- - **Completion evidence (delivered):** commit `d5bbe5d` on the integration branch `claude/inspiring-tesla-ie06gi` removed the unmounted pre-v1 routers and services, the legacy LLM router, the Pydantic models module, the WebSocket stub, the prompt package and 15 dead settings. The per-path inventory is [docs/architecture/legacy-retirement.md](docs/architecture/legacy-retirement.md). The guards are `backend/tests/test_truthful_outputs.py` (`REMOVED_MODULES`) and `backend/tests/test_legacy_retirement.py`. On a local PostgreSQL environment, the reviewed tree (base `692affb` plus the patch, which the mission's landing check found identical to `d5bbe5d`, whose parent is `692affb`) passed the blind oracle 199 of 199 and the full backend suite with 0 failed, 1109 passed and 46 skipped ([red-to-green record](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/n11-t1/red-to-green.md)); focused tests passed 83 of 83 on the integration checkout after landing.
+ - **Completion evidence (delivered):** commit `d5bbe5d` on the integration branch `claude/inspiring-tesla-ie06gi` removed the unmounted pre-v1 routers and services, the legacy LLM router, the Pydantic models module, the WebSocket stub, the prompt package and 15 dead settings. The per-path inventory is [docs/architecture/legacy-retirement.md](docs/architecture/legacy-retirement.md). The guards are `backend/tests/test_truthful_outputs.py` (`REMOVED_MODULES`) and `backend/tests/test_legacy_retirement.py`. On a local PostgreSQL environment, the reviewed tree (base `692affb` plus the patch, which the mission's landing check ([ledger](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/ledger.md), HOM-152 entry) found identical to `d5bbe5d`, whose parent is `692affb`) passed the blind oracle 199 of 199 and the full backend suite with 0 failed, 1109 passed and 46 skipped ([red-to-green record](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/n11-t1/red-to-green.md)); focused tests passed 83 of 83 on the integration checkout after landing.
```

- Fix 2 uses the review's wording, with "the ledger" spelled "the mission planning ledger" so it matches the §6.7 preamble's name for coverage-ledger.json.
- Fix 4 moves N05 out of the N08 and N15 Dependencies into a "Consumers include N05" sentence, and removes `N05` from the N15 matrix row's WP/dependencies cell (the review noted the loop is repeated there). N05 keeps N15 and N08 as dependencies.
- Fix 5 used the review's first option: CLI tools and the npm-distributed MDN dataset are typed `LIB`, which changes only the new §10.13 rows. I did not add Tool/Data to the legend, because that would insert a line into the preserved §10 intro (§10.1–10.12 stays byte-identical to base).

## Check output (real, final file)

### git diff --stat / status (against base 7112407)
```
 Homayar/AppVision.md | 392 ++++++++++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 388 insertions(+), 4 deletions(-)

 M Homayar/AppVision.md
```

### ID uniqueness (plain grep: `^| Nxx |` rows, `^#### Nxx — ` records)
```
N01 rows=1 records=1
N02 rows=1 records=1
N03 rows=1 records=1
N04 rows=1 records=1
N05 rows=1 records=1
N06 rows=1 records=1
N07 rows=1 records=1
N08 rows=1 records=1
N09 rows=1 records=1
N10 rows=1 records=1
N11 rows=1 records=1
N12 rows=1 records=1
N13 rows=1 records=1
N14 rows=1 records=1
N15 rows=1 records=1
```

### Structural verification: `python3 .../scratchpad/verify.py . 7112407` (from the worktree; exit 0)
It covers table column counts, IDs, preservation against base, §10 identity, links/anchors (the new ledger link is included: 16 added local links checked), S/P/H/O agreement, and the 18 fields per record.
```
tables: 30; data+separator rows checked: 494
[PASS] every table row has its header's column count: []
[PASS] every table has a --- separator row after its header: []
  touched table at line 249: header 8 cols, 16 data rows, row col counts [8]
  touched table at line 283: header 8 cols, 8 data rows, row col counts [8]
  touched table at line 296: header 8 cols, 6 data rows, row col counts [8]
  touched table at line 307: header 8 cols, 11 data rows, row col counts [8]
  touched table at line 323: header 8 cols, 30 data rows, row col counts [8]
  touched table at line 1064: header 3 cols, 26 data rows, row col counts [3]
[PASS] N01: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N02: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N03: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N04: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N05: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N06: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N07: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N08: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N09: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N10: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N11: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N12: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N13: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N14: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N15: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] no N-ID outside N01-N15 defined: []
[PASS] F: all 95 IDs appear exactly once as a table row: {}
[PASS] V/P/A/C/G/K/X: all 62 IDs appear exactly once as a table row: {}
[PASS] GR: all 40 IDs appear exactly once as a table row: {}
[PASS] RG: all 10 IDs appear exactly once as a table row: {}
[PASS] D: all 18 IDs appear exactly once as a table row: {}
[PASS] F01-F95: each has exactly one section 11.3 record
[PASS] no base line deleted without a replacement: []
  replaced non-ID line: Each row is an independently trackable acceptance unit. All rows are **PLANNED / current i...
  replaced ID line V01: carried by exactly one new line with same ID -> True
  replaced ID line X16: carried by exactly one new line with same ID -> True
  replaced non-ID line: This document records the full vision discussed to date, including all 95 historical featu...
[PASS] every replaced ID line is replaced by a line carrying the same ID
base lines: 1457; kept verbatim in order: 1453; replaced: 4
[PASS] base lines = kept verbatim + replaced
[PASS] section 10.1-10.12 catalog byte-identical to base: 232 lines
[PASS] F01-F95 matrix rows and acceptance baselines unchanged: 190 lines
links: 218 total; local/anchor links added by this update checked: 16
[PASS] every local link and in-document anchor resolves: []
new external links: 44 (github repos confirmed via GitHub search 2026-09-30; linear issues via Linear API)
[PASS] no Persian/Arabic-script body text: []
[PASS] code fences balanced: 4
[PASS] N01: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Provider conf
[PASS] N02: matrix S/P/H/O ['Y', 'Y', 'Y', 'L'] matches record: - **Deployment modes:** S Y · P Y · H Y · O L. In O, sources
[PASS] N03: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Policy evalua
[PASS] N04: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Metrics must 
[PASS] N05: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Only versions
[PASS] N06: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Datasets stay
[PASS] N07: matrix S/P/H/O ['Y', 'Y', 'Y', 'L'] matches record: - **Deployment modes:** S Y · P Y · H Y · O L. In O only in-
[PASS] N08: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Budgets apply
[PASS] N09: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. In O the enti
[PASS] N10: matrix S/P/H/O ['Y', 'C', 'C', 'L'] matches record: - **Deployment modes:** S Y · P C · H C · O L. P and H send 
[PASS] N11: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Retirement ap
[PASS] N12: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Objectives di
[PASS] N13: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Message catal
[PASS] N14: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. The policy mu
[PASS] N15: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. In O every ar
[PASS] N01: all 18 fields, Linear HOM-142 link, status PLANNED: missing=[]
[PASS] N02: all 18 fields, Linear HOM-143 link, status PLANNED: missing=[]
[PASS] N03: all 18 fields, Linear HOM-144 link, status PLANNED: missing=[]
[PASS] N04: all 18 fields, Linear HOM-145 link, status PLANNED: missing=[]
[PASS] N05: all 18 fields, Linear HOM-146 link, status PLANNED: missing=[]
[PASS] N06: all 18 fields, Linear HOM-147 link, status PLANNED: missing=[]
[PASS] N07: all 18 fields, Linear HOM-148 link, status PLANNED: missing=[]
[PASS] N08: all 18 fields, Linear HOM-149 link, status PLANNED: missing=[]
[PASS] N09: all 18 fields, Linear HOM-150 link, status PLANNED: missing=[]
[PASS] N10: all 18 fields, Linear HOM-151 link, status PLANNED: missing=[]
[PASS] N11: all 18 fields, Linear HOM-152 link, status PARTIAL: missing=[]
[PASS] N12: all 18 fields, Linear HOM-153 link, status PLANNED: missing=[]
[PASS] N13: all 18 fields, Linear HOM-154 link, status PLANNED: missing=[]
[PASS] N14: all 18 fields, Linear HOM-155 link, status PLANNED: missing=[]
[PASS] N15: all 18 fields, Linear HOM-156 link, status PLANNED: missing=[]

RESULT: ALL PASS
```

### Targeted checks for findings 1–5 (script output)
```
N-dependency edges (record Dependencies + matrix WP/dependencies column): {'N02': ['N03', 'N04'], 'N03': ['N02'], 'N05': ['N08', 'N15'], 'N06': ['N03'], 'N07': ['N03', 'N08']}
dependency cycle among N-items: ['N02', 'N03', 'N02']
10.13 integration types used: ['LIB', 'LIB/SVC', 'SVC'] -> all in legend {LIB,SVC,MODEL,API,CUSTOM}: True
N03 - **Deployment modes:** S Y · P Y · H Y · O Y. Policy evaluation and receipts must be local and need no call-out.
N04 - **Deployment modes:** S Y · P Y · H Y · O Y. Metrics must be computed locally.
N13 - **Deployment modes:** S Y · P Y · H Y · O Y. Message catalogs must ship with the release.
N14 - **Deployment modes:** S Y · P Y · H Y · O Y. The policy must be served locally.
N08 foundation: - **Existing foundation:** processing-quota reservation before work and single settlement (`backend/services/entitlement_service.py`, tables `usage_reservations` and `usage_events`); a per-endpoint request-per-minute window (`provider_rate_limits`, `backend/providers/resilience.py`), which limits call count, not spend. No provider-spend budget exists yet.
"completes" in N11 record: False
```

## Residuals for Foreman (not changed; brief says change nothing else)

1. **Another dependency loop of finding 4's class: N02 ↔ N03.** The loop check above found it. N02's Dependencies lists `N03` ("imported recordings need a consent basis"), and N03's Dependencies lists "ingestion paths (uploads, voicemail, V01 and N02 imports)". The N03 mention is an enforcement point, not a prerequisite, and the plan gives N03 no N02 prerequisite. Smallest fix, one clause at `Homayar/AppVision.md:412`: change "(uploads, voicemail, V01 and N02 imports)" to "(uploads, voicemail and V01 imports)" and add N02 to that record's existing "Consumers include …" sentence. I did not apply it because the review did not name it.
2. Finding 3 named four bullets. Other design-rule sentences in present tense remain, and the §6.7 preamble still covers them: N06 :471 "Datasets stay inside…", N11 :571 "Retirement applies…", N12 :590 "Objectives differ…", N15 :650 "In O every artifact is local…", and N13 :616 / N14 :636 "Offline implications: none; …".
3. Finding 5: pre-existing catalog rows in §10.11 (Promptfoo, Cosign, Syft, Trivy, pgBackRest, Restic) already use the undefined type "Tool". They are untouched to keep §10.1–10.12 byte-identical.
4. Info 6, matrix placement by area, is unchanged; each record names its matrix.

## Addendum: Foreman-approved N02↔N03 cycle fix (same class as finding 4)

- Foreman approved residual 1. I changed exactly two clauses on one line and nothing else. The present-tense sentences under the §6.7 preamble and the pre-existing §10.11 "Tool" rows are unchanged, as instructed. Not committed.
- Relative to turn 1: 18 lines changed in place (the 17 above plus this one); line count still 1841. New sha256: `ec3f15002c24a2d8d1bbe739fd2b0ef03bf88d175f3880cc4570b606571d9b48`.
- This supersedes residual 1 in the "Residuals for Foreman" list above. Residuals 2–4 stand.

### N03 Dependencies: line 412
```diff
- - **Dependencies:** GR12, GR14, GR25; F15 retention and subject export; ingestion paths (uploads, voicemail, V01 and N02 imports); the processing claim in the job pipeline (F17); exports (F70); outbound V03 and channels C01–C06. Consumers include V02–V04, N06 and N07.
+ - **Dependencies:** GR12, GR14, GR25; F15 retention and subject export; ingestion paths (uploads, voicemail and V01 imports); the processing claim in the job pipeline (F17); exports (F70); outbound V03 and channels C01–C06. Consumers include V02–V04, N02, N06 and N07.
```

### git diff --stat / status (against base 7112407)
```
 Homayar/AppVision.md | 392 ++++++++++++++++++++++++++++++++++++++++++++++++++-
 1 file changed, 388 insertions(+), 4 deletions(-)

 M Homayar/AppVision.md
```

### ID uniqueness
```
N01 rows=1 records=1
N02 rows=1 records=1
N03 rows=1 records=1
N04 rows=1 records=1
N05 rows=1 records=1
N06 rows=1 records=1
N07 rows=1 records=1
N08 rows=1 records=1
N09 rows=1 records=1
N10 rows=1 records=1
N11 rows=1 records=1
N12 rows=1 records=1
N13 rows=1 records=1
N14 rows=1 records=1
N15 rows=1 records=1
```

### Dependency-cycle check (script output)
```
N-dependency edges (record Dependencies + matrix WP/dependencies column): {'N02': ['N03', 'N04'], 'N05': ['N08', 'N15'], 'N06': ['N03'], 'N07': ['N03', 'N08']}
dependency cycle among N-items: None
```

### `python3 .../scratchpad/verify.py . 7112407` (from the worktree; exit 0)
```
tables: 30; data+separator rows checked: 494
[PASS] every table row has its header's column count: []
[PASS] every table has a --- separator row after its header: []
  touched table at line 249: header 8 cols, 16 data rows, row col counts [8]
  touched table at line 283: header 8 cols, 8 data rows, row col counts [8]
  touched table at line 296: header 8 cols, 6 data rows, row col counts [8]
  touched table at line 307: header 8 cols, 11 data rows, row col counts [8]
  touched table at line 323: header 8 cols, 30 data rows, row col counts [8]
  touched table at line 1064: header 3 cols, 26 data rows, row col counts [3]
[PASS] N01: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N02: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N03: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N04: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N05: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N06: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N07: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N08: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N09: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N10: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N11: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N12: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N13: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N14: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] N15: exactly one matrix row and one section 6.7 record: rows=1 records=1
[PASS] no N-ID outside N01-N15 defined: []
[PASS] F: all 95 IDs appear exactly once as a table row: {}
[PASS] V/P/A/C/G/K/X: all 62 IDs appear exactly once as a table row: {}
[PASS] GR: all 40 IDs appear exactly once as a table row: {}
[PASS] RG: all 10 IDs appear exactly once as a table row: {}
[PASS] D: all 18 IDs appear exactly once as a table row: {}
[PASS] F01-F95: each has exactly one section 11.3 record
[PASS] no base line deleted without a replacement: []
  replaced non-ID line: Each row is an independently trackable acceptance unit. All rows are **PLANNED / current i...
  replaced ID line V01: carried by exactly one new line with same ID -> True
  replaced ID line X16: carried by exactly one new line with same ID -> True
  replaced non-ID line: This document records the full vision discussed to date, including all 95 historical featu...
[PASS] every replaced ID line is replaced by a line carrying the same ID
base lines: 1457; kept verbatim in order: 1453; replaced: 4
[PASS] base lines = kept verbatim + replaced
[PASS] section 10.1-10.12 catalog byte-identical to base: 232 lines
[PASS] F01-F95 matrix rows and acceptance baselines unchanged: 190 lines
links: 218 total; local/anchor links added by this update checked: 16
[PASS] every local link and in-document anchor resolves: []
new external links: 44 (github repos confirmed via GitHub search 2026-09-30; linear issues via Linear API)
[PASS] no Persian/Arabic-script body text: []
[PASS] code fences balanced: 4
[PASS] N01: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Provider conf
[PASS] N02: matrix S/P/H/O ['Y', 'Y', 'Y', 'L'] matches record: - **Deployment modes:** S Y · P Y · H Y · O L. In O, sources
[PASS] N03: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Policy evalua
[PASS] N04: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Metrics must 
[PASS] N05: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Only versions
[PASS] N06: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Datasets stay
[PASS] N07: matrix S/P/H/O ['Y', 'Y', 'Y', 'L'] matches record: - **Deployment modes:** S Y · P Y · H Y · O L. In O only in-
[PASS] N08: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Budgets apply
[PASS] N09: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. In O the enti
[PASS] N10: matrix S/P/H/O ['Y', 'C', 'C', 'L'] matches record: - **Deployment modes:** S Y · P C · H C · O L. P and H send 
[PASS] N11: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Retirement ap
[PASS] N12: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Objectives di
[PASS] N13: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Message catal
[PASS] N14: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. The policy mu
[PASS] N15: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. In O every ar
[PASS] N01: all 18 fields, Linear HOM-142 link, status PLANNED: missing=[]
[PASS] N02: all 18 fields, Linear HOM-143 link, status PLANNED: missing=[]
[PASS] N03: all 18 fields, Linear HOM-144 link, status PLANNED: missing=[]
[PASS] N04: all 18 fields, Linear HOM-145 link, status PLANNED: missing=[]
[PASS] N05: all 18 fields, Linear HOM-146 link, status PLANNED: missing=[]
[PASS] N06: all 18 fields, Linear HOM-147 link, status PLANNED: missing=[]
[PASS] N07: all 18 fields, Linear HOM-148 link, status PLANNED: missing=[]
[PASS] N08: all 18 fields, Linear HOM-149 link, status PLANNED: missing=[]
[PASS] N09: all 18 fields, Linear HOM-150 link, status PLANNED: missing=[]
[PASS] N10: all 18 fields, Linear HOM-151 link, status PLANNED: missing=[]
[PASS] N11: all 18 fields, Linear HOM-152 link, status PARTIAL: missing=[]
[PASS] N12: all 18 fields, Linear HOM-153 link, status PLANNED: missing=[]
[PASS] N13: all 18 fields, Linear HOM-154 link, status PLANNED: missing=[]
[PASS] N14: all 18 fields, Linear HOM-155 link, status PLANNED: missing=[]
[PASS] N15: all 18 fields, Linear HOM-156 link, status PLANNED: missing=[]

RESULT: ALL PASS
```
