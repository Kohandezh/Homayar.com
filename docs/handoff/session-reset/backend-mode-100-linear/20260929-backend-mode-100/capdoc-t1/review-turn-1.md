# capdoc-t1 review, phase C turn 1 (capdoc-t1-rev, team reviewer)

- Patch reviewed: `/root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check/REVIEW-INPUT.diff` (sha256 prefix `58f8847e79886063`, confirmed; bare `git diff` of `Homayar/AppVision.md`,
  `index ca8d72d..753d306`, +388/-4). Base blob `ca8d72d` equals `git rev-parse 7112407:Homayar/AppVision.md`.
- Method: applied the diff to a throwaway copy (scratchpad, with `docs` symlinks so the relative links resolve as in the tree); never edited a tracked file.
  Read the whole diff (569 lines), the SPEC including the Foreman ruling (non-N11 items stay PLANNED), and checked every factual claim I could against source, git, the ledger and the network.
- I did not read the maker worktree or `capdoc-t1/handoffs/`.
- Line references below are line numbers in the patched `Homayar/AppVision.md`; `REVIEW-INPUT.diff` line numbers are in brackets.

## Verdict

Mergeable as-is. All six acceptance criteria are met and my oracle is fully green: 0 FAIL, 0 MISSING, preservation ALL PASS, and the raw diff has exactly 4 deleted lines, each a legal count/cross-reference edit.
The N11 record is exact and true (PARTIAL, commit `d5bbe5d` on the integration branch, not merged to `main`, CI blocked, gaps listed; RELEASED and VALIDATED are explicitly denied). All other 14 items are PLANNED per the Foreman ruling.
I found no critical, high or medium issue. Five low findings are wording/accuracy points in the new text (an understated "existing foundation", one overclaiming verb, one present-tense claim about a catalog that does not exist, mutual
dependency lists, catalog type labels outside the legend) and three info notes. None blocks the merge; L1-L3 are cheap to fix if Foreman wants a round 2.

## Findings

1. **low, Homayar/AppVision.md:522 [diff:280], N08 "Existing foundation" understates what already exists.**
   The record says "The only budgets in the job pipeline are retry budgets; no provider-spend budget exists yet." The source has a per-endpoint, per-minute provider request window: `provider_rate_limits`
   (`backend/config.py:457`, parsed at `backend/jobs/processor.py:445`, admitted in `backend/providers/resilience.py:15-17` as "at most N calls per minute per endpoint across all workers", ending the attempt with `provider_rate_limited`),
   with `provider_health.window_count` persisted (`backend/database/saas_models.py`, `ProviderHealth`), and `RATE_LIMITED` is one of the codes that lets an approved same-policy fallback run (`processor.py:433-436`).
   Why it matters: N08's owner bullets are "provider budget", "time-window limits" and "fallback protection"; this is the nearest existing mechanism, and the record tells a future implementer it does not exist.
   Trigger: `grep -n provider_rate_limits -r Homayar/backend --include=*.py` at `7112407`.
   Smallest fix: add "a per-endpoint request-per-minute window (`provider_rate_limits`, `backend/providers/resilience.py`), which limits call count, not spend" and keep "no provider-spend budget exists yet".

2. **low, Homayar/AppVision.md:568 [diff:326], N11 says it "completes" WP01-T03, which the planning ledger already records as VALIDATED.**
   The sentence is "F01's work package WP01 holds task WP01-T03, 'keep legacy routes disabled and document compatibility boundaries', which this capability completes."
   `coverage-ledger.json` (work item WP01-T03) has `verified_status: VALIDATED` (routers unmounted, removed-module guard present). "Completes" reads as a completion claim on a task and contradicts the ledger.
   Why it matters: AC6 and AppVision line 7: task/status claims need evidence; this one has none and conflicts with the cited source.
   Trigger: read the sentence next to the ledger row.
   Smallest fix: "which this capability extends (the ledger records WP01-T03 as validated before N11)".

3. **low, Homayar/AppVision.md:610 [diff:368], N13 states a message catalog as fact: "Message catalogs ship with the release."**
   It sits in the N13 "Deployment modes" bullet. At `7112407` there is no catalog and no Accept-Language handling (`grep -rIl -i "accept-language\|message_catalog\|i18n\|gettext" Homayar/backend --include=*.py` returns nothing);
   the ledger has WP22-T01 PLANNED ("Stable error codes exist; messages English-only"). The same present-tense style appears at lines 411 (N03 "Policy evaluation and receipts are local and need no call-out"),
   431 (N04 "Metrics are computed locally") and 630 (N14 "The policy is served locally").
   Why it matters: the section 6.7 rules (line 362) say the mode values are design targets, but a reader of the bullet alone sees a delivered artifact; N13's own foundation line says only an English envelope exists.
   Trigger: read the N13 bullet without the preamble.
   Smallest fix: "must ship" / "must be served locally" / "must be computed locally", or prefix each with "Design:".

4. **low, Homayar/AppVision.md:452, 512, 651 [diff:210, 270, 409], mutual "Dependencies" between N05, N15 and N08.**
   N05 lists N15 and N08 as dependencies; N15 lists N05; N08 lists N05 ("estimates"). The matrix rows repeat the N05<->N15 loop (rows at 316 and 319). The mission plan orders N15 before N05 (`WP13-T07 -> N15 -> {N05, X24}`), and N08 has no N05 prerequisite.
   Why it matters: the Dependencies field is what a scheduler reads; a cycle cannot be ordered. The owner bullet "reprocessing linkage" (N15) and "estimates" (N08) are consumers, not prerequisites.
   Trigger: follow N05 -> N15 -> N05.
   Smallest fix: move N05 out of N15's and N08's Dependencies into a "Consumers include N05" sentence (the N03 record at line 412 already uses that form).

5. **low, Homayar/AppVision.md:1067, 1081-1083, 1085-1086, 1088 [diff:436, 450-452, 454-455, 457], new catalog rows use integration types that the catalog legend does not define.**
   The section 10 intro legend is LIB, SVC, MODEL, API, CUSTOM (line 832). Meltano, Semgrep, jscodeshift, ast-grep, Pa11y and Playwright are typed "Tool" and MDN browser-compat-data "Data".
   Why it matters: the catalog is a structured reference; an undefined type breaks filtering and the "type / execution" convention for the rows next to it.
   Trigger: compare the new rows with the legend under `## 10`.
   Smallest fix: use LIB or SVC (CLI tools as LIB), and MODEL/`CUSTOM`-free wording for the MDN dataset (e.g. "LIB / ALL as a dated snapshot"), or add "Tool" and "Data" to the legend by insertion.

6. **info, Homayar/AppVision.md:265-266, 316-317 [diff:62-63, 87-88], matrix placement is by nearest area, not by subject.**
   N04 (data quality) and N14 (browser/device policy) sit in 6.1 "Voice and PBX"; N05 (reprocessing) and N06 (feedback loop) sit in 6.5 "BYOK, providers and settings". Each record names its matrix (the 6.7 intro explains "the area it serves"), so AC2 is met.
   A reader browsing 6.1 will see two non-voice capabilities. Acceptable; noted so Foreman can decide whether a dedicated matrix is preferred later.

7. **info, provenance of one N11 sentence: "the mission's landing check found identical to `d5bbe5d`" (line 581 [diff:339]).**
   The claim is true (ledger.md line 104: "landed diff == reviewed diff (script)"), but the sentence links no artifact for the landing check; only the red-to-green record is linked. Verified by me from the ledger, not from the document.

8. **info, Homayar/AppVision.md (GR25, D03, D13, X20, X09, X15 and other rows): references are one-directional.**
   The N records cite GR25, D03, D13 and the other related rows, but those rows do not name the N records (expected, because AC5 limits existing-line edits to the reused rows and counts). Non-blocking; my checklist listed this as INFO only.

## WARN / REVIEW items cleared

| Item | Verdict | Evidence |
|---|---|---|
| WARN E-N08-offlineb | cleared | N08's own Offline bullet only says the ledger is local, but the same record says "no fallback may reach a public provider" (Deployment modes bullet) and acceptance (2) covers retries and fallback; the shared rule at 6.7 (line 362) and section 3 (line 118) state "O denies public egress, including provider fallback, retries and telemetry" |
| WARN E-N09-offline | cleared | "no remote renewal check in O; renewal arrives as a signed update" plus the Deployment modes bullet ("signed offline licence", remote support unavailable, bundles as files) say what O does; no egress path is left open |
| WARN E-N13-offline | cleared | "none; no external dependency" is true for a catalog/metadata capability; no egress involved |
| REVIEW E-N11-claims | cleared | every merged/released/deployed wording is negated ("not merged to `main`", "not RELEASED") or prospective ("then a merge to `main`") |
| REVIEW E-N11-claimw | cleared | the two sentences ("...passed the blind oracle 199 of 199 and the full backend suite 0 failed, 1109 passed, 46 skipped"; "focused tests passed 83 of 83 on the integration checkout after landing") match `n11-t1/red-to-green.md` and ledger.md lines 94-104 |
| REVIEW E-N11-num | cleared | 199/199, 0 failed / 1109 passed / 46 skipped (165+285+358+301 passed, 3+43 skipped), 83/83 all agree with `docs/handoff/session-reset/.../n11-t1/red-to-green.md` and the ledger |
| REVIEW G5 | cleared | line 243 now reads "...unless their section 6.7 record states other evidence (N11 only)"; §4.1 line 166 states "PLANNED except N11, which is PARTIAL"; consistent |
| INFO B1a (4 accepted edits) | cleared | see M10 |
| INFO G4u (44 URLs) | cleared | see M12 |

## Manual list M01-M15

| # | Verdict | Basis |
|---|---|---|
| M01 content vs owner text | PASS | For each of N01-N15 I compared the owner's section 10.x "Implement:" bullets with the record's "Backend contract" line word by word: no bullet missing (N05's "selecting ..." appears as "select ..."). Owner constraints kept: N02 native/dlt/scale, N03 enforcement authority, N04 native audio logic, N05 Celery kept and Temporal/Prefect/Dagster "not mandatory", N08 usage ledger authority, N09 billing not authorization authority, N13/N14 backend only and no frontend change, N15 existing registry preferred. No bullet is softened (N08 "hard budget", N03 "every entry point ... refuses server-side") |
| M02 reuse mapping | PASS with L2 | All 11 pairs follow the SPEC mapping. Each record names the reused row with quoted text; V01 and X16 rows and nine F-sections (11.3) point back; the 11.2 paragraph at line 1211 lists the nine F rows. My weak-mapping concerns are handled: N06 cites WP13-T07 as the link and adds F80/F44/F45; N09 adds X09/X15/F75; N11 is honest that the link is WP01-T03 (but see L2). No deviation |
| M03 status truth | PASS with L1 | 14 items PLANNED; each "Existing foundation" I checked against `7112407`: tenancy_service/`enable_free_demo` (line 124), `WorkspaceInvitation.expires_at/revoked_at`, `POST /{organization_id}/workspaces` (organizations.py:350), ingestion conflict semantics, checkpoints, replay, transcript revisions, quality route (`insights.py:557`), `estimate_quality` (called only from tests), the single writer of `Notification` (`voicemail/pipeline.py:252`), `ManifestStatus`, two `QualificationRecord`s, `MediaValidationPolicy`, `_error_payload`, `infra/scripts` backup/restore/tombstone scripts and `jobs/tombstone_reapply.py`; "none" claims verified (0 matches for consent, telemetry, support tier/SLA). N11 verified against git (below). One understatement: L1 |
| M04 N11 gap list | PASS | Open items are all named: CI not run (Actions billing), not merged (`main` = `2e834ed`, verified locally and with `git ls-remote origin refs/heads/main`), criteria 6-7 open, HOM-158 README residue and `jwt_expiry_hours`, WP01-T04/HOM-28 pruning (`ldap3`, `reportlab`), console pages calling retired `/dashboard` endpoints, and "whether any legacy data store needs migration ... not yet established". Not-covered owner bullets (legacy data migration, dual read/write, checkpoints, rollback tooling, Semgrep/jscodeshift/ast-grep) are recorded as open, never claimed. "Criteria 1-5 met" is supported by `docs/architecture/legacy-retirement.md`, `REMOVED_MODULES` (`test_truthful_outputs.py:19`) and `test_legacy_retirement.py` |
| M05 no invention | PASS | No price, SLA figure or RPO/RTO value (N12 leaves D03 open); decision ids D03, D04, D05, D09, D10, D12, D13, D17, D18 match their section 9.1 rows; GR ids used match their text; WP task labels match `backlog.json` (WP09-T02 normalized derivatives/VAD, WP19-T04 signed offline entitlements, WP16-T05 delivery ledger, WP22-T01 message catalog, WP24-T05 exports, WP12-T06, WP13-T07, WP14-T03, WP20-T05/T06, WP18-T02/T03); Linear ids HOM-141..156, HOM-28, HOM-158 correct; every cited path exists (F6p PASS), cited SHAs exist (F6c PASS) |
| M06 candidates are candidates | PASS with L5 | 10.13 says its rows were not licence-reviewed and defers to section 10.12; role text uses "alternative", "reference", "only if"; preferences match the owner (dlt after native, Airbyte only at connector scale, existing registry preferred). The intro's cross-references to already-catalogued candidates are exact (Celery/Temporal 10.7, Label Studio/Langfuse/OpenTelemetry Collector/pgBackRest/Restic 10.11, Apprise 10.8, LiteLLM 10.5, Lago/Kill Bill 10.10, flagd 10.9) and none of the 26 new rows duplicates a base row. Existence claim verified (M12) |
| M07 acceptance criteria testable | PASS | Every record has 5-7 numbered criteria with a denial or negative case, cross-tenant/two-tenant tests where relevant, idempotency/failure cases; completion evidence is worded as a requirement and ends "Current evidence: none" (N11 excepted) |
| M08 O-mode honesty | PASS | N07 O=L and N10 P=C H=C O=L (no `Y`); N02 O=L; N09 O=Y justified by a signed offline licence and remote support explicitly unavailable; N06/N12/N15 O=Y with local-only text; matrix cell text and the "Deployment modes" bullet agree for all 15 (script-checked). BYOK-not-a-mode and offline-egress sentences present at lines 118, 362 |
| M09 N13/N14 backend-only | PASS | Both contracts are labelled "(backend only)" and "changes no frontend code"; N13 states F72's acceptance is UI behaviour and that keyboard/screen-reader verification is "later frontend work" (external qualification); no F72 UI criterion is claimed |
| M10 accepted edits B1a/B1b/B1c | PASS | Exactly 4 deleted lines: §6 intro (adds "unless their section 6.7 record states other evidence (N11 only)"), V01 row (adds "; bulk import and migration extended by N02"), X16 row (adds "; support entitlement extended by N09"), 12.2 (adds "15 owner-approved backend capabilities N01-N15 (four new, eleven extending an existing row), "). No B1b move, no B1c version-line edit (line 3 untouched; a new "Update:" line was inserted instead). 4 new + 11 extending = 15; other counts unchanged (95, 35, 3, 24, 40, 10, 18) |
| M11 raw diff deletions | PASS | `grep -c '^-[^-]'` on the diff = 4; identical to the script's list; no whitespace-only churn (the diff would show it) |
| M12 URLs and Linear | PASS | All 26 repositories in 10.13 checked through the GitHub search API today: each exists, is public and `archived:false` (including `wso2/openfgc` and `fivetran/great_expectations`, whose move to the `fivetran` org the row states correctly). Linear ids/URLs match the phase-A table (bare `https://linear.app/homayar/issue/HOM-1xx`); HOM-152 is not called Done anywhere |
| M13 rendering | PASS (structural) | Tables well formed (C1/C2), heading slugs match all added anchors (`#n11--migration-and-legacy-retirement`, `#n14--browserdevice-support-backend-contract`, `#67-approved-backend-capabilities-n01n15`), the Contents entry is a properly indented sub-bullet, relative links resolve. Not rendered on GitHub |
| M14 scope | PASS | One file changed; no mention or claim about `vision/AppVision-Status-Notes.md` or `ReportAppvisionProgress.md` |
| M15 maker table-integrity output | PASS (independent) | I was not shown the maker's own output; my `C1`/`C2` (all tables constant column count, touched tables well formed) is independent and agrees with Foreman's gate |

### N11 facts re-verified in git (checker worktree and `/home/user/Homayar.com`)

- `git log --oneline claude/inspiring-tesla-ie06gi`: `7112407`, `3d64130`, `d5bbe5d`, `692affb`, ... ; `git rev-parse d5bbe5d^` = `692affb...` (matches the record).
- `git merge-base --is-ancestor d5bbe5d main` exits 1; `main` = `origin/main` = `2e834ed` locally, and `git ls-remote origin refs/heads/main` returns `2e834ed`.
- `git branch -a --contains d5bbe5d` lists the integration branch and `origin/claude/inspiring-tesla-ie06gi`, not `main`.
- Numbers (199, 1109/46, 83) match `n11-t1/red-to-green.md` and `ledger.md`. "15 dead settings" matches the 15 rows of the retirement doc's Settings table.

## Confirmed by the oracle (execution, not reading)

| Run | Result |
|---|---|
| Phase A, base `7112407` (`oracle/base-run.txt`) | exit 1; MISSING 17 (D1, E-ALL, E-N01..N15), FAIL 4 (F5a, F5b, G1, G2), preservation ALL PASS |
| Phase C, patch applied to a throwaway copy, in-situ links | exit 0; PASS 156, INFO 7, WARN 3, REVIEW 4, FAIL 0, MISSING 0; N items 15/15; preservation ALL PASS (Foreman's run adds `A1` PASS = 157); all base-red checks (D1, E-ALL, E-N01..N15, F5a, F5b, G1, G2) are green on the patch |
| Same run with the file copied outside `Homayar/` | one `G4` FAIL for relative links that only resolve next to the real document; not a patch defect (re-run with the tree layout is clean) |
| `oracle/selftest.py` | 55/55 expected results (synthetic compliant doc exit 0, 44 mutants red, 6 legal edits green, 5 file-ownership cases) |
| Independent of the script | `git apply --check` clean; raw deletions = 4; `mismatches 0` between each N matrix row's S/P/H/O and the record's "Deployment modes" line for all 15; owner-bullet word check 15/15 |

## Not verified (asserted from reading or from records only)

- GitHub rendering of the new sections and the nested Contents bullet (structure checked, not rendered).
- That GitHub Actions still cannot start jobs and PR #13 is still red: relied on the Foreman ledger (lines 34, 104, 142, 157); I re-verified only the git side (`main` unchanged on the remote).
- Test results cited in N11 (199/199, 1109/46, 83/83): confirmed to match the archived records; I did not re-run the backend suite (documentation unit, and the suite needs the mission database lane).
- Licence, edition and offline behaviour of the 26 candidates: the document itself says they were not reviewed; I confirmed only existence and non-archived status on 2026-09-30, which can change.
- Semantic fitness of each "Existing foundation" beyond file/symbol existence and the negative greps I ran (for example, I did not read every function body cited).
- The wording of the Linear issue descriptions is not compared with the AppVision records (only ids, urls and states).

CANDIDATES critical=0 high=0 medium=0 low=5 info=3
