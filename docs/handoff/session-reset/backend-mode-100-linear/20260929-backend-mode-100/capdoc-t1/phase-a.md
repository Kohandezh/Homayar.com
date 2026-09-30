# capdoc-t1 oracle, phase A record (capdoc-t1-rev)

- Mission 20260929-backend-mode-100, team capdoc-t1, turn 1, written 2026-09-30.
- Inputs read: `capdoc-t1/SPEC.md`, `planning/owner-approved-capabilities-N01-N15.md` (sections 10 and 18), `Homayar/AppVision.md` at the clean base
  (`/root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check`, detached at `7112407`, `git status --porcelain` empty before and after my work).
  For context only: `planning/PLAN.md`, `planning/coverage-ledger.json` (N rows), `linear/map.tsv`, `ledger.md`, `n11-t1/` records, git history of `d5bbe5d`, and a live
  Linear read (`list_issues parentId=HOM-141`, `get_issue HOM-141`) to pin the N-to-HOM identity and states.
- Not read: the maker worktree `wt/capdoc-t1`, `capdoc-t1/handoffs/`, any diff or patch of AppVision.md. No repository file was edited.
- Deliverables (all under `/root/foreman/20260929-backend-mode-100/capdoc-t1/oracle/`):

| File | Purpose | sha256 |
|---|---|---|
| `checklist.md` | acceptance checklist (AC1-AC6, owner sections 10 and 18) | `de79a50cfeb97e8a6b3ebc9743941f21156df33d9543b5753d4f96e856d9f8a4` |
| `check.py` | scripted oracle: check.py NEW BASE [--maker-repo R --base-sha S] [--dump-blocks] [--json] [-q] | `77ba71d59b97897d4f5e014470001661d3687672856466b1fe5f018f9155f613` |
| `selftest.py` | self-test: synthetic compliant doc, 44 mutants, 6 legal-edit controls, 5 file-ownership cases | `7cceb32ef892874d2599d808b1f35e8ffdc79841d79ebf7415ef62b59c3add8d` |
| `base-run.txt` | raw output of the run below | |
| `selftest-run.txt` | raw output of the self-test | |

## 1. The run on the base (real output)

Command (NEW and BASE are the same file, so nothing differs; BASE given as the checkout root is accepted):

```
python3 /root/foreman/20260929-backend-mode-100/capdoc-t1/oracle/check.py \
  /root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check/Homayar/AppVision.md \
  /root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check
```

```
NEW  = /root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check/Homayar/AppVision.md
BASE = /root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check/Homayar/AppVision.md
REPO = /root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check

[INFO   ] A1: file-ownership check skipped (no --maker-repo); run: git -C <maker-wt> diff --stat 7112407 && git -C <maker-wt> status --porcelain
[PASS   ] A2: LF line endings and final newline as in the base
[PASS   ] B1: every base line is unchanged or replaced by a same-ID line that only adds an N-ID cross-reference or updates a count (0 edited lines)
[PASS   ] B2: all 225 original ID rows ({'GR': 40, 'V': 7, 'P': 6, 'A': 6, 'C': 7, 'G': 5, 'K': 7, 'X': 24, 'RG': 10, 'D': 18, 'F': 95}) and 95 F-section headings present once, same section, same order, cell text preserved
[PASS   ] B3: all 159 original headings (numbering and titles) present in the same order
[PASS   ] B4: technology catalog (section 10) preserved: 154 table rows in base, 154 in new; every base row present
[PASS   ] B4h: catalog sub-headings (13) preserved
[PASS   ] B5s: section 4. text unchanged (insertions only)
[PASS   ] B5m: section 3. text unchanged (insertions only)
[PASS   ] C1: all 29 tables have a separator row and a constant column count (base: 29 tables, 0 pre-existing violations)
[PASS   ] C2: 0 touched table(s) well formed (blank line before/after, no empty ID/name cells)
[PASS   ] C3: code fences balanced (fenced lines: base 39, new 39)
[MISSING] D1: 15/15 N items have no definition at all
            - N01
            - N02
            - N03
            - N04
            - N05
            - N06
            - N07
            - N08
            - N09
            - N10
            - N11
            - N12
            - N13
            - N14
            - N15
[PASS   ] D2: no N-ID other than N01..N15 (two digits) appears anywhere
[PASS   ] D3: 225 original ID rows each appear exactly as often as in the base
[PASS   ] D4: no new duplicate heading anchors
[PASS   ] A3: no Persian/Arabic-script text in added lines
[PASS   ] A4: no credential-like strings in added lines
[PASS   ] A4p: no local/session paths in added lines
[PASS   ] D5: every F/V/P/A/C/G/K/X/N/GR/RG/D/WP id cited in added lines exists
[PASS   ] G4: in-document anchors and relative links added by the patch resolve
[PASS   ] F5: no fifth deployment mode and no offline/on-prem conflation in added lines
[FAIL   ] F5a: added text never states that BYOK is credential ownership, not a deployment mode (SPEC AC2 / owner 18.19)
[FAIL   ] F5b: added text never states that fully offline denies public egress including fallback, retries and telemetry (SPEC AC2 / owner 18.19)
[WARN   ] F5c: owner 18.19 on-premise routing sentence (local AI, customer BYOK, approved online providers, hybrid stage routing where policy permits) not found in added text
[PASS   ] F6c: 0 cited commit SHA(s) all exist in the checkout
[PASS   ] F6p: every cited repository path exists at base (or is labelled planned/new)
[MISSING] E-ALL: 15 of 15 N items missing
            - N01
            - N02
            - N03
            - N04
            - N05
            - N06
            - N07
            - N08
            - N09
            - N10
            - N11
            - N12
            - N13
            - N14
            - N15
[MISSING] E-N01: N01 (customer onboarding backend) is not defined anywhere in AppVision.md
[MISSING] E-N02: N02 (data import and migration reuses v01) is not defined anywhere in AppVision.md
[MISSING] E-N03: N03 (recording consent policy engine and enforcement) is not defined anywhere in AppVision.md
[MISSING] E-N04: N04 (data quality backend reuses f26) is not defined anywhere in AppVision.md
[MISSING] E-N05: N05 (reprocessing workflow) is not defined anywhere in AppVision.md
[MISSING] E-N06: N06 (feedback to evaluation loop reuses f81) is not defined anywhere in AppVision.md
[MISSING] E-N07: N07 (notification preferences reuses f59) is not defined anywhere in AppVision.md
[MISSING] E-N08: N08 (provider budget kill switch reuses f18) is not defined anywhere in AppVision.md
[MISSING] E-N09: N09 (support entitlement reuses x16) is not defined anywhere in AppVision.md
[MISSING] E-N10: N10 (privacy preserving product telemetry) is not defined anywhere in AppVision.md
[MISSING] E-N11: N11 (migration and legacy retirement reuses f01) is not defined anywhere in AppVision.md
[MISSING] E-N12: N12 (disaster recovery objectives reuses f79) is not defined anywhere in AppVision.md
[MISSING] E-N13: N13 (accessibility backend support reuses f72) is not defined anywhere in AppVision.md
[MISSING] E-N14: N14 (browserdevice support backend contract reuses f12) is not defined anywhere in AppVision.md
[MISSING] E-N15: N15 (model and prompt rollback reuses f20) is not defined anywhere in AppVision.md
[FAIL   ] G1: section 12.2 (coverage counts) was not updated to include the 15 N items (N01-N15 / '15 approved ...')
[PASS   ] G1c: stated counts agree with the recount (F=95, expanded V/P/A/C/G/K/X=62, GR=40, RG=10, D=18, N rows in section 6=0, N rows elsewhere=0)
[FAIL   ] G2: Contents block is identical to the base: the TOC was not updated for the N items (AC4)
[PASS   ] G2n: Contents numbering sequential 1..12

SUMMARY PASS=24 INFO=1 WARN=1 REVIEW=0 FAIL=4 MISSING=17
N items present: 0/15; missing: N01, N02, N03, N04, N05, N06, N07, N08, N09, N10, N11, N12, N13, N14, N15
Preservation checks (B*): ALL PASS
VERDICT: FAIL
exit=1
```

### What the output shows

| Requirement from the task | Result |
|---|---|
| N-items reported as missing | yes: `D1 MISSING` (15/15), `E-ALL MISSING`, and one `E-N01`..`E-N15 MISSING` line each (17 MISSING lines); "N items present: 0/15" |
| Preservation checks pass on the base | yes: `B1, B2, B3, B4, B4h, B5s, B5m` all PASS; summary line "Preservation checks (B*): ALL PASS" |
| Table integrity on the base | PASS: 29 tables, 0 column-count violations at base (`C1`), fences balanced (`C3`) |
| ID uniqueness on the base | PASS for the 225 original ID rows (`D3`), no stray N id (`D2`), no duplicate anchors (`D4`) |
| AC4 items not yet done | FAIL as intended: `G1` (12.2 has no N mention), `G2` (Contents unchanged), `F5a`/`F5b` (BYOK-is-credential-ownership and offline-egress sentences absent) |
| Exit code | 1 (FAIL/MISSING present) |

Independent cross-check of "preservation passes on base" with git itself: `git diff --no-index --unified=0 --stat base.md base.md` prints nothing and exits 0.
`sha256(Homayar/AppVision.md at base) = 0e1702f83bf04194927365c5570f3bbc7c37357a5898bb684d693d21d3e767f1`.

## 2. Proof that the oracle can also go green and can catch violations

Because the base can only show red for the N items, I generated a SYNTHETIC compliant document from the base plus the owner's own text (no maker material) and mutated it.
`python3 selftest.py <base AppVision.md>` result: last lines below, full output in `selftest-run.txt`.

```
GOOD synthetic document: exit=0 FAIL/MISSING=0
ok  A1 committed AppVision.md only              expect=PASS got=PASS

SELFTEST PASSED (0 unexpected result(s))  work dir: /tmp/capdoc-selftest-2tw4o5ev
```

- Good document: exit 0, no FAIL/MISSING (a few WARN/REVIEW remain by design: `G5`, `G1b`).
- 44 single-defect mutants, each flagged by the intended check id (examples: deleted F05 row -> B1/B2/D3; renamed heading -> B3; extra column -> B1/C1;
  duplicate N05 row -> D1u; N03 marked VALIDATED -> E-N03-status; N11 without `d5bbe5d` / not-merged / CI-blocked -> E-N11-status; N11 RELEASED -> E-N11-status;
  N09 with a wrong Linear slug -> E-N09-linear; missing candidate -> E-N08-cand; thin contract -> E-N01-anchors; no reverse cross-reference -> E-N06-reuse;
  TOC unchanged -> G2; stale counts -> G1/G1c; "BYOK mode" -> F5; missing offline-egress sentence -> F5b; fabricated SHA -> F6c; fabricated path -> F6p;
  local path leak -> A4p; Persian text -> A3; dangling F96/X25 -> D5; stray N16 -> D2).
- 6 legal edits stay green: count-only edit, cross-reference appended to an existing row, an extra catalog row, and the same item fields laid out as a two-column
  table, as bold paragraphs, and as sub-headings.
- 5 file-ownership cases against a throwaway git repo: no change / only AppVision.md (tracked and committed) / README also changed / untracked extra file.

## 3. Decisions I made in the checklist that Foreman may want to overrule (recorded, not hidden)

1. **"Defined exactly once" is read strictly.** One matrix row per N id (needed for AC2 "added to the relevant matrix") and at most one detail block; a second table keyed by
   an N id in its first cell is a duplicate definition (FAIL). A cross-reference index should key on the reused id or list N ids in a non-first column.
2. **Fields must be labelled.** A required field that only appears as a word inside other prose is a FAIL (`E-Nxx-fieldsp`); I can downgrade it after reading if the content is really there.
3. **Status.** Default PLANNED; PARTIAL only with cited existing evidence plus recorded gaps and my verification (REVIEW). The planner ledger marks 9 N items PARTIAL;
   AppVision line 159-161 and SPEC AC3 lean PLANNED, so I treat unverified PARTIAL as a finding. N11 is expected PARTIAL (CI/merge item BLOCKED_EXTERNAL); PLANNED, RELEASED and
   an unscoped VALIDATED fail; a scoped VALIDATED goes to REVIEW and my default is to reject it.
4. **AC4 "contents/TOC is updated" is read literally**: the Contents block must differ from the base (a new top-level entry such as "13. Approved backend capabilities")
   and every entry must resolve. Adding only sub-sections under an existing top-level heading would leave Contents unchanged and fail `G2`.
5. **Reuse mapping.** I accept the SPEC mapping but my reading of AppVision's own row text finds N06->F81 and N11->F01 weak (justified only through backlog/WP links), and N09->X16 partial
   (X09/X15/F75 are closer for entitlement). A maker deviation is REVIEW (Foreman decides), never an automatic pass or fail. See checklist table 1.3.
6. **Version/Recorded line 3** edits are REVIEW (not in AC5's allowed edit list); a new revision line is the safe form.

## 4. Known limits

- Heuristic parts: field labelling, owner-bullet anchors (70 % floor), claim wording. They lower the chance of a silent omission; they do not prove content is correct (manual items M01-M15 in the checklist).
- No network: URL reachability and Linear state are manual (M12); the Linear identity table in the checklist was read live on 2026-09-30.
- The oracle contains no repository secret and reads only the two AppVision files, the git checkout of BASE (for SHA/path existence) and `docs/planning/backlog.json`.
