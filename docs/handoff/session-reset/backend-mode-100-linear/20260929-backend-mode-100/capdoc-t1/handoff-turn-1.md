# Handoff: capdoc-t1, turn 1 (documentation unit)

- FROM: capdoc-t1-impl. TO: foreman.
- Worktree `/root/foreman/20260929-backend-mode-100/wt/capdoc-t1`, branch `mission/capdoc-t1` at `7112407`. **Not committed.**
- Only file changed: `Homayar/AppVision.md` (1457 → 1841 lines; +388 / −4). sha256 of the result: `dfdeb9fada7080b995df6f4daa6aa602b009cbd30e0ea6a7b5e9245afa0c7955`.
- Not read: `wt/capdoc-t1-check`, `capdoc-t1/oracle/`, `capdoc-t1/reviews/`.

## 1. What was added and where (line numbers in the new file)

| New lines | Base anchor | Change | AC |
|---|---|---|---|
| 5–6 | after :3 | Inserted "Update: 2026-09-30" note (N01–N15 added; which kinds of existing lines changed) | AC4 |
| 21 | after :18 | Contents: nested entry `6.7 Approved backend capabilities N01–N15` under item 6 (anchor `#67-approved-backend-capabilities-n01n15`) | AC4 |
| 118–119 | after :114 (§3) | Inserted paragraph: owner profile names → S/P/H/O; on-prem routing (local AI, BYOK, approved online providers, hybrid stage routing); **BYOK is credential ownership, not a fifth mode**; O denies public egress incl. fallback, retries, telemetry (§18 item 19) | AC2 |
| 166 | after :160 (§4.1) | Inserted bullet: N01–N15 PLANNED except N11 PARTIAL | AC3 |
| 243 | :237 (§6 intro) **modified** | "All rows are PLANNED / current implementation unverified" + " unless their section 6.7 record states other evidence (N11 only)" — needed because N11's row is not PLANNED | AC3/AC5 |
| 245 | after :237 | Inserted note: N-rows follow original rows; cross-referenced both ways; §6.7 holds records | AC1 |
| 251 | :243 V01 **modified** | Capability cell + "; bulk import and migration extended by N02" (reverse cross-reference) | AC1 |
| 264–266 | after :255 (§6.1) | Rows N02, N04, N14 | AC2 |
| 292 | after :280 (§6.3) | Row N07 | AC2 |
| 303 | after :290 (§6.4) | Row N03 | AC2 |
| 316–319 | after :302 (§6.5) | Rows N05, N06, N08, N15 | AC2 |
| 340 | :323 X16 **modified** | Capability cell + "; support entitlement extended by N09" | AC1 |
| 349–354 | after :331 (§6.6) | Rows N01, N09, N10, N11, N12, N13 | AC2 |
| 356–663 | before `## 7.` | **§6.7 Approved backend capabilities N01–N15**: preamble (reuse map, deployment-mode rule, status rule, shared tenant/audit/evidence rules) + 15 records `#### N01 —` … `#### N15 —` (N01 366, N02 386, N03 406, N04 426, N05 446, N06 466, N07 486, N08 506, N09 526, N10 546, N11 566, N12 585, N13 605, N14 625, N15 645) | AC1–AC3, AC6 |
| 1060–1091 | after :726 (end of §10.12) | **§10.13 Approved backend capability candidates — N01–N15**: 26 new catalog rows (3 columns, same shape as other catalog tables); candidates already in the catalog are referenced, not duplicated | §18 item 15 |
| 1211–1212 | after :844 (§11.2 table) | Inserted note: F01←N11, F12←N14, F18←N08, F20←N15, F26←N04, F59←N07, F72←N13, F79←N12, F81←N06 | AC1/AC4 |
| 1220, 1287, 1324, 1337, 1374, 1573, 1652, 1695, 1708 | §11.3 blocks F01, F12, F18, F20, F26, F59, F72, F79, F81 | One inserted bullet each: "**Extended by approved backend capability:** [Nxx — title](#anchor) (section 6.7). The acceptance baseline above is unchanged…" | AC1 |
| 1804 | after :1427 (§12.1) | Inserted source bullet: owner prompt §10/§18, Linear HOM-141..156, archived PLAN.md and coverage-ledger.json (repo copies are byte-identical to planning/) | AC4 |
| 1808 | :1431 (§12.2) **modified** | Count sentence + "15 owner-approved backend capabilities N01–N15 (four new, eleven extending an existing row)" | AC4 |
| 1835–1841 | after :1457 (§12.4) | Inserted "Update 2026-09-30" verification record (counts; rows changed; structural verification; runtime NOT_RUN). The original :1452–1457 record is untouched (§9.4: no rewriting of old evidence). | AC4 |

Each §6.7 record has these fields (checked by script, section 5): Linear (ID + URL), Matrix, ID decision, Purpose, Backend contract (the owner's §10 list verbatim in substance), Deployment modes (S/P/H/O equal to the matrix row, with explanation), Dependencies, Security and privacy, Tenant boundary, Audit, Failure behavior, Offline implications, Commercial and usage implications, Repository candidates, Acceptance criteria (numbered, observable), Completion evidence (required evidence + current evidence), Existing foundation (verified in source at `7112407`), Status (+ reason), External qualification.

## 2. Mapping decisions (verified against the Atlas row text)

All reused mappings from the SPEC are **kept**; I propose no change. The N-ID stays the capability ID everywhere; the host row is cross-referenced both ways. Linear titles read back 2026-09-30 (`list_issues parentId=HOM-141`) match: HOM-142..HOM-156 = N01..N15, titles with "(reuses …)" identical to the SPEC map.

| N | Host | Row text checked | Verdict and reason |
|---|---|---|---|
| N02 | V01 | "Import recordings, extension, time and call ID…; Replay-safe import; call ID provenance; correct tenant/channel mapping" | Keep. V01 is the only row about importing data (provenance, replay safety). N02 is broader (any source, legacy migration, dry run, rollback); recorded as extension. F13, F64 named as related. |
| N04 | F26 | "Audio normalization, VAD and quality checks… empty/music/noise cases do not fabricate transcript; quality flags exposed" | Keep. Direct overlap (quality checks, no fabrication). F12, F23, F33 related. |
| N06 | F81 | "AI regression and model/prompt promotion gates… approved rollback manifest" | Keep. Backlog `WP13-T07` (WP13 = F80/F81) explicitly says "collect production corrections separately". F80 (corpus rights/splits) is the closest neighbour; named as related. |
| N07 | F59 | "Post-call/digest alerts… only authorized recipients notified; suppressed/acknowledged/escalated states and failed delivery visible" | Keep. Preferences/escalation/ack/delivery attempts extend this row. F93 (DEFERRED channel alerts) related. |
| N08 | F18 | "Usage metering, quotas and plan limits… 100 concurrent reservation/completion attempts cannot exceed a hard configured quota or double charge" | Keep. Budget = quota axis extended to provider/model/workflow. K04, GR13, GR31, X11 related. |
| N09 | X16 | "Optional commercial remote support \| Support license; temporary authorization" | Keep. X16 depends on a support licence, which N09 defines. X09 (entitlement) and X15 (bundle) related. |
| N11 | F01 | "Release/source provenance" (row text itself does not mention legacy) | Keep. Backlog lineage: WP01 (F01, F02) contains `WP01-T03` "keep legacy routes disabled and document compatibility boundaries" (validated). Reason stated in the record. |
| N12 | F79 | "Encrypted backups and restore drills… RPO ≤24h/RTO ≤8h… tombstones reapplied before access" | Keep. Strong match. X20, D03 related. |
| N13 | F72 | "Persian RTL/English LTR, dates, digits, accessibility" (UI-level acceptance) | Keep. N13 is its backend part. X17, F70 related. |
| N14 | F12 | "Adversarial media validation… supported codec fixtures pass; forged, malformed, oversized… fail" | Keep. Codec/container/limits/rejection reasons overlap; published policy + negotiation is the extension. V05 related. |
| N15 | F20 | "…a prior approved manifest can be restored without rewriting historical results" | Keep. Rollback is already in F20's acceptance. F81, K07, GR11 related. |
| N01 | new | Checked X06, X07, X09, F04–F06, F08 | New is justified: no row covers provisioning as one resumable process. |
| N03 | new | Checked GR14, GR25, §7.3, F15 | New is justified: guardrails/journeys require consent; no capability row enforces it (`grep consent` in backend: 0 hits). |
| N05 | new | Checked F24 ("reanalysis preserves old provenance"), F45, F20, X12 | New is justified: those cover provenance/staleness, not an operator reprocessing workflow. |
| N10 | new | Checked X14, F78 | New is justified: operational observability ≠ product telemetry with opt-out/pseudonymous IDs. |

**Matrix placement** (the SPEC leaves it to the maker; reasons):
- §6.1 Voice and PBX: N02 (sits with its host V01), N04 (quality of recorded media intake), N14 (recording/upload clients; V05).
- §6.3 Communications: N07 (channels, delivery, stop handling).
- §6.4 Access and delegated authority: N03 (purpose-specific permissions enforced before action, like G-rows).
- §6.5 BYOK, providers and settings: N05, N06, N08, N15 (model/prompt/provider versions, qualification K07, provider budgets K04).
- §6.6 Product configuration, commercial delivery and support: N01 (X06/X07), N09 (X15/X16), N10 (X14), N11 (release baseline), N12 (X20), N13 (X17).
- New rows are appended after each table's original rows (no reordering).

## 3. Status decisions (AC3, AC6)

- **N01–N10, N12–N15: PLANNED.** Rule applied: a record is PLANNED until evidence for its *own* acceptance criteria exists on a named revision. **This differs from the planning ledger**, which marks N01, N02, N04, N05, N12, N13, N14, N15 as PARTIAL because foundations exist in source. I did not adopt that because (a) §4 defines PARTIAL as "some specified acceptance criteria met", and no N-criterion has run; (b) §6 and §4.1 record overlapping expanded rows as PLANNED / "overlap still needs verification"; (c) AC6. To avoid a silent parallel-status conflict (§12.3 item 2), the §6.7 preamble names the ledger, lists the eight items, and says the difference is classification, not evidence. Each record lists its "Existing foundation", re-verified by file inspection at `7112407` (no tests run). **Foreman decision point:** if you prefer the ledger's PARTIAL, only the eight Status lines, the §6 intro qualifier, the §4.1 bullet and the preamble sentence change.
- **N11: PARTIAL** (recorded 2026-09-30). Evidence: commit `d5bbe5d` (parent `692affb`) on `claude/inspiring-tesla-ie06gi`; inventory `Homayar/docs/architecture/legacy-retirement.md`; guards `test_truthful_outputs.py` (`REMOVED_MODULES`) and `test_legacy_retirement.py`; blind oracle 199/199 and full suite 0 failed / 1109 passed / 46 skipped locally on the reviewed tree (landing check: identical to `d5bbe5d`); focused 83/83 on the integration checkout (archived `docs/handoff/session-reset/.../n11-t1/red-to-green.md`, ledger). That meets N11 criteria 1–5. **Not VALIDATED:** CI has not run on the revision (GitHub Actions billing: jobs do not start; RG01, GR34); the commit is not on `main` (`git ls-remote` 2026-09-30: main = `2e834ed`; `merge-base --is-ancestor d5bbe5d origin/main` false); criteria 6 (legacy data migration) and 7 (CI on the merged revision) are open. **Not RELEASED.** Open items named: HOM-158 (README residue, `jwt_expiry_hours`), HOM-28 (WP01-T04: `ldap3`, `reportlab` pruning), console pages still calling `/dashboard/*` (`frontend/src/lib/hooks.ts:11` verified), legacy data migration need not established. Linear HOM-152 read back as In Progress.
- No record claims implemented, enabled, licensed or production-ready. Every "Existing foundation" line says it is not acceptance evidence. Runtime validation for this update is recorded as NOT_RUN in §12.4.

## 4. Evidence gathered for claims (so the checker can reproduce)

- Source (at `7112407`, file inspection only): `services/tenancy_service.py` `create_personal_workspace` (demo subscription only if `enable_free_demo`); `saas_models.py` `WorkspaceInvitation` `expires_at`/`revoked_at`; `ingestion_service.py` payload-hash conflict; `quality_service.py` + `GET /api/v1/meetings/{meeting_id}/quality` (`routers/v1/insights.py:557`); `speech/audio_profile.py:153 estimate_quality` with no caller outside its module; `notifications` written only by `voicemail/pipeline.py:252`; only retry budgets in `jobs/processor.py`/`config.py` (no provider-spend budget); `providers/registry.py:67 ManifestStatus`, `providers/hardware.py:178` and `speech/benchmark.py:129 QualificationRecord`; `main.py:72 _error_payload`; `media/validation.py:303 MediaValidationPolicy`; `jobs/replay.py:60`; `transcript_service.py` superseded revisions/runs; repo `infra/scripts/{backup,restore}-{postgres,minio}.sh`, `reapply-tombstones.sh`; `database/rls.py:60 WORKSPACE_TABLES`; no consent, telemetry or support-entitlement module.
- §10.13 URLs: all 26 confirmed via GitHub repository search on 2026-09-30 (exist, `archived: false`). Great Expectations is now `fivetran/great_expectations` (same repo id 103071520, created 2017); OpenFGC = `wso2/openfgc` (identified by web search). Licenses were **not** reviewed; the section says so.
- `docs/handoff/session-reset/backend-mode-100-linear/20260929-backend-mode-100/{PLAN.md,coverage-ledger.json}` are byte-identical (`cmp`) to `planning/`.

## 5. Check outputs (real)

### git diff --stat / status
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

### Deleted base lines (`git diff -U0 | grep '^-[^-]'`), all replaced in place
```
-Each row is an independently trackable acceptance unit. All rows are **PLANNED / current implementation unverified**. Existing overlap is mapped to WP packages without renumbering the original backlog. Expanded scope is approved as product vision, not automatically as a V1 release blocker. Guardrails GR01–GR40 apply throughout.
-| V01 | Import recordings, extension, time and call ID; nightly sales analysis | Y | Y | Y | L | WP05/09/16; PBX adapter | Replay-safe import; call ID provenance; correct tenant/channel mapping |
-| X16 | Optional commercial remote support | Y | C | C | — | Support license; temporary authorization | Expiring session, scoped access, audit and immediate disconnect |
-This document records the full vision discussed to date, including all 95 historical feature IDs, all 35 originally selected V/P/A/C/G/K IDs, three additional provider/settings requirements K05–K07, 24 expanded cross-product requirements X01–X24, 40 guardrails, 10 release gates and 18 unresolved decision records. It preserves multiple technology alternatives and clearly separates planning from delivery evidence.
```

### Structural verification script
Command: `python3 /tmp/claude-0/-home-user-Homayar-com/de27f501-1327-54de-980e-22778b73f1ba/scratchpad/verify.py . 7112407` (run from the worktree). It checks every table (column count vs header, separator row), ID uniqueness for N01–N15 and for all original ID families, line-level preservation against `git show 7112407:Homayar/AppVision.md` (difflib), §10.1–10.12 byte identity, F-rows and acceptance baselines unchanged, local link/anchor resolution (GitHub slug rules), non-English script, S/P/H/O row-vs-record agreement, and the 18 required fields per record.
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
links: 217 total; local/anchor links added by this update checked: 15
[PASS] every local link and in-document anchor resolves: []
new external links: 44 (github repos confirmed via GitHub search 2026-09-30; linear issues via Linear API)
[PASS] no Persian/Arabic-script body text: []
[PASS] code fences balanced: 4
[PASS] N01: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Provider conf
[PASS] N02: matrix S/P/H/O ['Y', 'Y', 'Y', 'L'] matches record: - **Deployment modes:** S Y · P Y · H Y · O L. In O, sources
[PASS] N03: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Policy evalua
[PASS] N04: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Metrics are c
[PASS] N05: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Only versions
[PASS] N06: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Datasets stay
[PASS] N07: matrix S/P/H/O ['Y', 'Y', 'Y', 'L'] matches record: - **Deployment modes:** S Y · P Y · H Y · O L. In O only in-
[PASS] N08: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Budgets apply
[PASS] N09: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. In O the enti
[PASS] N10: matrix S/P/H/O ['Y', 'C', 'C', 'L'] matches record: - **Deployment modes:** S Y · P C · H C · O L. P and H send 
[PASS] N11: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Retirement ap
[PASS] N12: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Objectives di
[PASS] N13: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. Message catal
[PASS] N14: matrix S/P/H/O ['Y', 'Y', 'Y', 'Y'] matches record: - **Deployment modes:** S Y · P Y · H Y · O Y. The policy is
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

Scripts kept for re-runs: `.../scratchpad/apply.py` (applies all edits by exact anchors, each asserted unique) and `.../scratchpad/verify.py`. After apply.py, I made three wording-only fixes by exact replacement: the update note, N11 evidence provenance, and the N01 demo-subscription condition. verify.py was re-run after each; the output above is from the final file.

## 6. Risks / open points for Foreman

1. **PLANNED vs ledger PARTIAL** for 8 items (section 3): decision point, disclosed in the doc.
2. **§6 intro line :237 was modified** (status qualifier). It has no ID; AC5's rule "deleted line replaced by a line with the same ID" cannot apply literally. It is a status cross-reference required by N11's non-PLANNED status. The other non-ID modification is the §12.2 count sentence (:1431), which AC4 requires.
3. N11 "Existing foundation" is folded into "Completion evidence (delivered)" (19 bullets instead of 20); the field check covers it.
4. §10.13 execution classes (LIB/SVC, ALL/CONDITIONAL) are feasibility labels per the §10 legend, not tested badges; licenses are unreviewed and marked so.
5. The Linear state for HOM-152 (In Progress) appears only in this handoff, not in AppVision; ReportAppvisionProgress.md is a separate deliverable.
