# capdoc-t1 review, phase C round 2 (capdoc-t1-rev, team reviewer)

- Patch: `/root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check/REVIEW-INPUT.diff` (sha256 prefix `f2915672ff4be2c9`, confirmed) and `REVIEW-DELTA.diff` (turn 1 -> turn 2, sha256 prefix `113eaf003615997c`, confirmed).
- Method: applied the full diff to a throwaway copy of the base `AppVision.md` (scratchpad, with `docs` symlinks for the relative links); built my own turn-1 -> turn-2 delta from my turn-1 copy and compared it with Foreman's; re-ran `oracle/check.py`; parsed every N record's "Dependencies" field and every N matrix row into a graph and searched it for cycles.
  No tracked file was edited. I did not read the maker worktree or `capdoc-t1/handoffs/`.
- Line references are to the turn-2 patched `Homayar/AppVision.md`.

## Verdict

Mergeable as-is. All five turn-1 low findings and the approved N02<->N03 fix are applied exactly as named, nothing changed outside the delta, the oracle is green, and there is no dependency cycle among N01-N15.
Checking the dependency fields more widely than the N-only scope I was asked about, I found one leftover of the same class (N03's own matrix row and record disagree about V02-V04) and two smaller notes. Round 2 is the last round, so I record the leftover as low for a follow-up; it does not touch an acceptance criterion.

## Turn-1 findings, verified on turn 2

| Turn-1 finding | Status | Evidence (patched file) |
|---|---|---|
| 1 low, N08 existing foundation understated | FIXED as named | line 522 now reads "...`usage_reservations` and `usage_events`); a per-endpoint request-per-minute window (`provider_rate_limits`, `backend/providers/resilience.py`), which limits call count, not spend. No provider-spend budget exists yet." The cited path exists; the wording matches what the source does (`resilience.py:15-17`) |
| 2 low, N11 "completes" WP01-T03 | FIXED as named | line 568: "which this capability extends (the mission planning ledger records WP01-T03 as validated before N11)"; `grep -c "which this capability completes"` = 0 |
| 3 low, present-tense claims (N13 catalog and three siblings) | FIXED as named | line 610 "Message catalogs must ship with the release."; line 411 "Policy evaluation and receipts must be local ..."; line 431 "Metrics must be computed locally."; line 630 "The policy must be served locally." |
| 4 low, mutual dependencies N05<->N15, N05<->N08 | FIXED as named | N15 matrix row now `F20; F81; K07; GR11`; N15 record line 651 "...X24 (rights inventory). Consumers include N05."; N08 record line 512 "...G04 (override approval). Consumers include N05 (estimates)." The N08 matrix row already had no N05. N05 still depends on N15 and N08, so the order is N15/N08 first |
| 5 low, catalog types outside the legend | FIXED as named | all seven rows (Meltano, Semgrep, jscodeshift, ast-grep, Pa11y, Playwright, MDN browser-compat-data) now say `LIB / ...`; the 10.13 type column contains only LIB, LIB/SVC and SVC |
| 7 info, unlinked landing-check provenance | FIXED | line 581 now links `[ledger](../docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/ledger.md), HOM-152 entry`; the file is tracked, the link resolves (G4 PASS), and ledger.md lines 93-104 contain the HOM-152 entry with "landed diff == reviewed diff (script)" |
| Approved extra: N02<->N03 loop | FIXED as named | N03 record line 412: "ingestion paths (uploads, voicemail and V01 imports)" (N02 removed) and "Consumers include V02–V04, N02, N06 and N07"; N02 still depends on N03 (line 391 area and its matrix row) |

## Nothing else changed

- My own delta (turn-1 copy vs turn-2 copy) and `REVIEW-DELTA.diff` have identical changed-line sets (36 lines: 18 removed, 18 added). Every delta hunk belongs to one of the fixes above (N15 row, N03 record, N04 bullet, N08 record, N11 bullet, N13 bullet, N14 bullet, N15 record, 7 catalog type cells).
- Base -> turn 2 still deletes exactly the same 4 lines as turn 1 (the section 6 intro, V01, X16 and the 12.2 count line), each a legal cross-reference/count edit; `grep -c '^-[^-]' REVIEW-INPUT.diff` = 4.
- The N03 matrix-row text, the four legal existing-row edits, all 15 N matrix rows' S/P/H/O values, status text (14 PLANNED, N11 PARTIAL), the 10.13 URLs and the F-section bullets are untouched by the delta.

## Dependency graph among N01-N15 (parsed from the patched file)

Edges are the N ids in each record's "Dependencies" field (the "Consumers include" sentence read as the reverse edge) and in each N matrix row's "WP / dependencies" cell.

| N | depends on (records) | depends on (matrix rows) |
|---|---|---|
| N02 | N03, N04 | N03 |
| N05 | N08, N15 | N08, N15 |
| N06 | N03 | N03 |
| N07 | N03, N08 | N08 |
| N01, N03, N04, N08, N09, N10, N11, N12, N13, N14, N15 | none | none |

- No cycle: a depth-first search over records, over matrix rows, and over both combined finds none among N01-N15. A topological order exists (N03, N04, N08, N15 first; then N02, N05, N06, N07; the rest are leaves).
- One false positive in my first parse, explained: the N01 Dependencies line contains the text "X07 hands its RFP/profile output to N01", which is a mention of N01 as the target of a hand-off, not a self-dependency.

## Findings

1. **low, Homayar/AppVision.md:303 and :412, N03's matrix row and record disagree about V02-V04 (same class as turn-1 finding 4, not covered by the N-only scope of this round).**
   The N03 matrix row lists `V02–V04` in "WP / dependencies", and the record's Dependencies say "outbound V03 and channels C01–C06" and then "Consumers include V02–V04, N02, N06 and N07". So V03 is both a dependency and a consumer of N03 inside its own record, and the matrix row makes V02-V04 prerequisites of the capability that the mission plan orders before them (`N03 -> {V02, V03, N06}`).
   Why it matters: a reader building a schedule from the row sees a loop N03 <-> V02-V04. The existing V02-V04 rows are unchanged and do not cite N03, so there is no cycle across the whole document, only a contradiction inside N03's own entries.
   Trigger: read line 303 and line 412 next to each other.
   Smallest fix: change the row's dependencies to `GR12; GR14; GR25; F15` and, in the record, keep "outbound V03" only as an enforcement point ("enforced at outbound V03 ...") rather than in the Dependencies list.
2. **info, Homayar/AppVision.md:292 and :349, N07 and N01 list rows that the mission plan orders after them.**
   N07 lists C01–C05 and A02 as dependencies (the plan has `N07 -> {A02, C01–C04, C06, X19, V03}`), and N01 lists X07 (the plan has `X06 -> N01 -> X07`; the N01 record explains it as data flow). Those existing rows do not cite N07 or N01, so there is no loop; the direction is only debatable. Left for the sequencer.
3. **info, Homayar/AppVision.md:371-650, Deployment-mode bullets still describe O in the present tense.**
   For example N15 line 650 "every artifact is local and checksum-verified, and nothing is pulled from a registry at runtime", N12 line 590, N09 line 531. The section 6.7 rule (line 362) says these are design targets, and none names an artifact that is absent from source (turn-1 finding 3 was the only such case, now fixed). Recorded because it is the same style.
4. **info, carried from turn 1 (unchanged), Homayar/AppVision.md:265-266 and :316-317, matrix placement is by nearest area.**
   N04 and N14 sit in 6.1 "Voice and PBX"; N05 and N06 sit in 6.5. Each record names its matrix and the 6.7 intro explains the rule; acceptable.
5. **info, carried from turn 1 (unchanged), references from GR25, D03, D13, X20 and similar rows to the N records are one-directional.**
   Expected under AC5 (existing rows may only gain reused-row cross-references and counts). Non-blocking.

## WARN / REVIEW / manual list

The seven WARN/REVIEW items from turn 1 are unchanged in kind and in text (E-N08-offlineb, E-N09-offline, E-N13-offline, E-N11-claims, E-N11-claimw, E-N11-num, G5); the delta touches none of the sentences behind them except N11's provenance link, which only adds the ledger reference. My turn-1 clearances stand. M01-M15 stand as PASS (M02 and M06 now without their turn-1 caveats; M03 without L1). I re-ran the mechanical parts: owner-bullet coverage unchanged (delta does not touch any Backend contract line), matrix row vs "Deployment modes" S/P/H/O consistency still 15/15.

## Confirmed by the oracle

| Run | Result |
|---|---|
| Phase A base | exit 1, MISSING 17, FAIL 4, preservation ALL PASS |
| Turn 1 patch (round 1) | exit 0, FAIL 0, MISSING 0, WARN 3, REVIEW 4 |
| Turn 2 patch (this round, in-situ links) | exit 0; PASS 156, INFO 7, WARN 3, REVIEW 4, FAIL 0, MISSING 0 (Foreman's run adds `A1` = 157); `B1` shows 4 edited lines; `D5`, `G4`, `C1`, `C2`, all `E-Nxx-modes` and `E-Nxx-reuse` PASS |
| Independent of the script | own-delta vs Foreman delta identical; raw deletions = 4; dependency graph acyclic among N01-N15; 10.13 type column free of undefined types |

## Not verified

- GitHub rendering of the changed lines (they are text substitutions inside existing bullets and cells; table column counts unchanged, `C1` PASS).
- That the phrase "the mission planning ledger records WP01-T03 as validated" will stay true if the ledger is later revised; it is true of `coverage-ledger.json` today.
- I did not re-run the backend suite or re-query GitHub Actions; the N11 test numbers and CI state rest on the archived records and the Foreman ledger, as in round 1.

CANDIDATES critical=0 high=0 medium=0 low=1 info=4
