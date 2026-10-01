# capdoc-t1 acceptance checklist (phase A)

- Author: capdoc-t1-rev (independent reviewer), written from SPEC.md, the owner text and the clean base only.
  I did not read the maker worktree or capdoc-t1/handoffs/.
- Contract: `/root/foreman/20260929-backend-mode-100/capdoc-t1/SPEC.md` (AC1-AC6).
- Owner text: `/root/foreman/20260929-backend-mode-100/planning/owner-approved-capabilities-N01-N15.md` (section 10, section 18).
- Base: `7112407`, checkout `/root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check` (`Homayar/AppVision.md`, 1457 lines,
  sha256 `0e1702f83bf04194927365c5570f3bbc7c37357a5898bb684d693d21d3e767f1`).
- Script: `/root/foreman/20260929-backend-mode-100/capdoc-t1/oracle/check.py` (stdlib only, no network).
  Self-test: `oracle/selftest.py` (builds a synthetic compliant document plus 44 violating mutants; all behave as expected).
- Check ids below (A1, B1, E-N05-status, ...) are the ids `check.py` prints, so a review finding can cite them.

## 0. How to run and how the verdict is derived

```
ORACLE=/root/foreman/20260929-backend-mode-100/capdoc-t1/oracle
CHECK=/root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check
python3 $ORACLE/check.py <PATCHED>/Homayar/AppVision.md $CHECK                      # BASE may be the checkout root or the base file
python3 $ORACLE/check.py <PATCHED>/Homayar/AppVision.md $CHECK --maker-repo <MAKER_WT> --base-sha 7112407   # adds the file-ownership check A1
python3 $ORACLE/check.py <PATCHED>/Homayar/AppVision.md $CHECK --dump-blocks -q     # prints the parsed record of every N item for reading
python3 $ORACLE/selftest.py $CHECK/Homayar/AppVision.md                             # proves the oracle itself still works
```

| Level | Meaning | Effect |
|---|---|---|
| MISSING / FAIL | acceptance criterion violated or artefact absent | blocks acceptance; exit code 1 |
| REVIEW | the script cannot decide | I must adjudicate it in the review file, with the evidence I looked at |
| WARN | heuristic doubt | I read it and either raise a finding or record why it is fine |
| INFO | facts (lists of edited lines, URLs) | I read the list; nothing to decide unless it looks wrong |
| PASS | satisfied | none |

Verdict: ACCEPT only when (1) `check.py` exits 0, (2) every REVIEW and WARN has an adjudication, (3) the manual items in
section 8 are ticked. A script FAIL may be downgraded only after I read the text and log the reason (the parser is
label-based; a field written in an exotic layout can be a false FAIL). Findings use severity high/medium/low; any
FAIL that survives is at least medium. Two-round cap: round 1 lists every finding, round 2 confirms the fixes.

## 1. Facts pinned before the patch (checked by me, not copied from the maker)

### 1.1 Base structure of AppVision.md (7112407)

| Region | Lines | Notes |
|---|---|---|
| Header, line 7 rule ("No capability becomes implemented, enabled, licensed, or production-ready merely because it appears here") | 1-9 | AC6 anchor |
| Contents (12 numbered links) | 11-24 | slugs resolve at base; AC4 |
| 3 Deployment modes (S/P/H/O legend Y/L/C/em dash) | 104-137 | must stay unchanged |
| 4 Status vocabulary (11 statuses) | 139-153 | must stay unchanged; 4.1 lines 155-162 |
| 5 Guardrails GR01-GR40, 5.4 profiles | 164-233 | |
| 6 matrices 6.1-6.6 (V/P/A/C/G/K/X = 62 rows, 8 columns, S/P/H/O + WP / dependencies + Completion evidence) | 235-331 | line 237: "All rows are PLANNED / current implementation unverified" |
| 10 Technology catalog (10.1-10.12, 154 table rows) | 496-726 | must be preserved |
| 11.2 matrix F01-F95 (6 columns) and 11.3 sections `#### Fxx - title` (3 bullets each) | 746-1416 | |
| 12 Coverage: 12.2 counts (line 1431), 12.4 record (lines 1452-1457) | 1418-1457 | counts 95 / 35 / 3 (K05-K07) / 24 (X) / 62 / 40 / 10 / 18 |

Real tables at base: 29 (7 more `|` runs are inside code fences and are not tables); 0 column-count violations; LF line endings; final newline.
No `N\d\d` token exists at base.

### 1.2 N item, Linear identity (verified against Linear on 2026-09-30 with list_issues parentId=HOM-141)

Parent `[CAP]` HOM-141 (Backlog). All children are Backlog except HOM-152 (**In Progress**, Done gate = merge + CI).
URL form: `https://linear.app/homayar/issue/HOM-142` (SPEC) or with Linear's own slug. A different slug is a fabricated URL.

| N | Owner section | Owner title | Linear | Slug (exact) |
|---|---|---|---|---|
| N01 | 10.1 | Customer onboarding backend | HOM-142 | n01-customer-onboarding-backend |
| N02 | 10.2 | Data import and migration | HOM-143 | n02-data-import-and-migration-reuses-v01 |
| N03 | 10.3 | Recording consent policy | HOM-144 | n03-recording-consent-policy-engine-and-enforcement |
| N04 | 10.4 | Data-quality backend | HOM-145 | n04-data-quality-backend-reuses-f26 |
| N05 | 10.5 | Reprocessing workflow | HOM-146 | n05-reprocessing-workflow |
| N06 | 10.6 | Feedback-to-evaluation loop | HOM-147 | n06-feedback-to-evaluation-loop-reuses-f81 |
| N07 | 10.7 | Notification preferences | HOM-148 | n07-notification-preferences-reuses-f59 |
| N08 | 10.8 | Provider budget kill-switch | HOM-149 | n08-provider-budget-kill-switch-reuses-f18 |
| N09 | 10.9 | Support entitlement | HOM-150 | n09-support-entitlement-reuses-x16 |
| N10 | 10.10 | Privacy-preserving product telemetry | HOM-151 | n10-privacy-preserving-product-telemetry |
| N11 | 10.11 | Migration and legacy retirement | HOM-152 | n11-migration-and-legacy-retirement-reuses-f01 |
| N12 | 10.12 | Disaster recovery objectives | HOM-153 | n12-disaster-recovery-objectives-reuses-f79 |
| N13 | 10.13 | Accessibility backend support | HOM-154 | n13-accessibility-backend-support-reuses-f72 |
| N14 | 10.14 | Browser/device support backend contract | HOM-155 | n14-browserdevice-support-backend-contract-reuses-f12 |
| N15 | 10.15 | Model and prompt rollback | HOM-156 | n15-model-and-prompt-rollback-reuses-f20 |

Follow-ups that are not N items and must not receive N ids: HOM-157 (REC-02), HOM-158 (N11-F1, README residue), HOM-140 (STAB-01).

### 1.3 Reuse mapping (SPEC context) with my independent reading of AppVision's own row text

The SPEC fixes the mapping; the maker may deviate only with a stated handoff reason (Foreman decides). The script marks a missing
SPEC mapping as REVIEW (`E-Nxx-reuse`), and a missing reverse reference as FAIL. My assessment, so I can judge a deviation:

| N -> reused row | What the reused row says at base | Fit | What the N record must not do |
|---|---|---|---|
| N02 -> V01 | 6.1 "Import recordings, extension, time and call ID; nightly sales analysis"; evidence "Replay-safe import; call ID provenance; correct tenant/channel mapping" | partial: V01 is PBX recording import, N02 is a general import/migration framework | widen or rewrite V01's own row; say N02 is only V01 (alternative sibling: F13 ingestion ledger) |
| N04 -> F26 | "Audio normalization, VAD and quality checks"; "quality flags exposed" | good | claim F26's audio-timeline criteria are met |
| N06 -> F81 | "AI regression and model/prompt promotion gates" (evaluation reproducibility, promotion cannot happen without held-out report and rollback manifest) | **weak by AppVision's text**: the corrections-to-datasets loop is closer to F45/F44/F46 (correction, override, calibration) and F80 (gold corpus rights, split leakage); the F81 link exists only through backlog WP13-T07 | invent that F81's row already covers corrections; a deviation to F45/F80 is plausible and must be argued |
| N07 -> F59 | "Post-call/digest alerts and review queues"; "one alert per dedup window; only authorized recipients; suppressed/acknowledged/escalated states; failed delivery visible" | good (suppression, ack, escalation, delivery); F93 (channel alerts, no acceptance recorded) and C05/A02 are siblings | drop the channel/offline side (external channels are not available in O) |
| N08 -> F18 | "Usage metering, quotas and plan limits"; concurrent reservations cannot exceed quota; reconciles with the ledger | good; K04, X11, X12, GR31 are related | fork the ledger; the Homayar usage ledger stays the authority |
| N09 -> X16 | 6.6 "Optional commercial remote support" (support license, temporary authorization; O is em dash) | partial: entitlement/tier/SLA/bundle-eligibility is closer to X09 (license, entitlement, expiry/grace), X15 (bundle) and F75 (offline license, support bundle) | mark O as available for remote support (X16 O is unavailable); let a billing system be the authorization authority |
| N11 -> F01 | "Release/source provenance" (clean checkout builds; manifest) | **weak by row text**: the link is the WP01 column (backlog WP01-T03 "keep legacy routes disabled and document compatibility boundaries") | claim F01 is reconciled or validated; F01 keeps NEEDS_RECONCILIATION |
| N12 -> F79 | "Encrypted backups and restore drills" (RPO <= 24h / RTO <= 8h proposed; tombstones reapplied) | good; X20 and D03 related | present the proposed RPO/RTO as decided values (D03 is an open owner decision) |
| N13 -> F72 | "Persian RTL/English LTR, dates, digits, accessibility" (keyboard-only flow, 200% zoom) | good but UI-heavy | claim any F72 UI criterion (keyboard, zoom, focus) is covered: N13 is backend-only, frontend untouched |
| N14 -> F12 | "Adversarial media validation" (codec fixtures pass; forged/oversized inputs fail) | fair: codec/container constraints and rejection reasons; V05 (browser voice) and X01 are related | modify frontend scope; claim browser qualification |
| N15 -> F20 | "Model/prompt registry and provider capabilities" ("prior approved manifest can be restored without rewriting historical results") | good; K07, X24, GR11 related | require replacing the existing registry (owner: prefer the existing Homayar registry) |

New entries with no reused id: N01, N03, N05, N10 (they get their own matrix row and must not overwrite an existing row).

### 1.4 N11 delivered facts (the only delivered N item; verified in git and in the Foreman ledger)

- Commit `d5bbe5d2fc794666977c436279629b48d73526c9` "Legacy retirement: remove the unmounted pre-v1 stack and its dead settings (HOM-152)".
  It is contained in `claude/inspiring-tesla-ie06gi` and `origin/claude/inspiring-tesla-ie06gi`; `git merge-base --is-ancestor d5bbe5d main` exits 1
  (`main` = `origin/main` = `2e834ed`). So: on the integration branch, **not merged to main**. PR #13 is the integration PR.
- CI: GitHub Actions jobs fail in 2-3 s with a billing/spending-limit cause since PR #7; main is red identically (ledger lines 34, 142, 157).
  Remote CI has therefore **not run** on d5bbe5d; the Done gate for HOM-152 is merge + CI, both blocked externally.
- Local evidence that exists (ledger "HOM-152 accepted"): blind oracle 199 cases, base 129 red / 70 green, patch 199/199; full suite on PostgreSQL in 4 chunks
  0 failed / 1109 passed / 46 skipped; ruff clean; 35 files changed (+390/-2321). Any number the record cites must equal one of these.
- Delivered artefacts: `Homayar/docs/architecture/legacy-retirement.md` (per-path inventory with status/evidence/replacement; unsafe webhook `!=` secret compare;
  deferred dependency pruning), guard `REMOVED_MODULES` in `Homayar/backend/tests/test_truthful_outputs.py:19`, `Homayar/backend/tests/test_legacy_retirement.py`.
- Known gaps the record must not hide: remote CI not run; not merged to main; README residue and `jwt_expiry_hours` (HOM-158, Backlog); console pages that still call
  unmounted endpoints (frontend follow-up, out of scope); dependency pruning (`ldap3` extra, `reportlab`) deferred to WP01-T04/HOM-28.
- Owner section 10.11 bullets actually covered by d5bbe5d: legacy inventory, replacement mapping, forbidden legacy-path checks, dead-route removal after proof,
  no activation of the unsafe legacy auth (webhook), status and evidence for every retired path, and (partly) dependency mapping. **Not covered** (must not be
  claimed): legacy data migration, dual-read/write, checkpoints, rollback tooling, and the candidate tools Semgrep/jscodeshift/ast-grep (not adopted).
- Linear HOM-152 is **In Progress**. The record must never call it Done.

## 2. Preservation check (AC5), exact procedure

### 2.1 Commands

```
MAKER=<maker worktree or the tree that holds the patched file>          # phase B only
git -C $MAKER diff --stat 7112407 -- .                                   # must list only Homayar/AppVision.md
git -C $MAKER status --porcelain                                         # must show only " M Homayar/AppVision.md" (no untracked, no staged extras)
git -C $CHECK show 7112407:Homayar/AppVision.md > /tmp/appvision.base.md
git diff --no-index --no-color --unified=0 /tmp/appvision.base.md $MAKER/Homayar/AppVision.md > /tmp/appvision.diff
grep -c '^-[^-]' /tmp/appvision.diff                                      # number of deleted/replaced base lines (D)
grep    '^-[^-]' /tmp/appvision.diff                                      # read every one of them
python3 $ORACLE/check.py $MAKER/Homayar/AppVision.md $CHECK --maker-repo $MAKER --base-sha 7112407 -q
```

### 2.2 The rule (SPEC AC5, made precise)

Every base line that is not byte-identical (trailing whitespace ignored) in the patched file is a "lost line" L. L is legal only if it is replaced by exactly one
new line M that carries the **same key**, where the key is:

| Line kind | Key |
|---|---|
| Table row | the normalized first cell (F01, V01, GR13, RG02, D07, `[LiveKit Agents](...)`, ...) |
| `#### Fxx - title` heading | Fxx |
| Numbered/other heading | its section number (`6.6`) or its text |
| Contents entry | its ordinal (`12.`) |
| `- **Label:**` bullet inside an F section | (Fxx, Label) |
| Any other prose line | none: it may only be edited by a count update or a cross-reference |

and M must equal L plus **only** (a) an inserted cross-reference containing an `N01`..`N15` token, or (b) digit-for-digit replacement of a count. Nothing else.
Extra checks that catch what the line rule cannot: original ID rows keep their section, order, column count and every cell text as a prefix (B2); all 159 original
headings remain in order with the same text (B3); section 10 keeps every catalog row and sub-heading (B4); sections 3 and 4 are unchanged except by insertion (B5).

### 2.3 What counts as a violation (any one is a FAIL, severity at least medium; V1-V3 high)

| # | Violation | Script id |
|---|---|---|
| V1 | a base line deleted with no same-key replacement (row, heading, F-section bullet, prose) | B1 |
| V2 | renumbering or renaming: an F/V/P/A/C/G/K/X/GR/RG/D id, a section number, a heading text, a Contents ordinal | B1, B2, B3 |
| V3 | existing text altered: a status word (NEEDS_RECONCILIATION, PLANNED), a mode cell, a title, an acceptance sentence, a catalog description | B1, B2 |
| V4 | a column added/removed/reordered in an existing table | B1, C1 |
| V5 | original rows or sections reordered or moved | B2, B1b (REVIEW) |
| V6 | catalog row or sub-heading removed or edited beyond an N cross-reference | B4, B1 |
| V7 | edit to section 3 (deployment modes) or section 4 (status vocabulary) other than insertion | B5m, B5s |
| V8 | prose reflowed, rewritten, "simplified" (any non-digit deletion in an existing prose line) | B1 |
| V9 | text appended to an existing row that is not an N cross-reference (for example N-item evidence pasted into V01's cell) | B1, B2 |
| V10 | a count edited to a wrong number, or coverage left stale | G1, G1c |
| V11 | whitespace-only churn on existing lines (git shows it, the script normalizes it) | reviewer: low finding |
| V12 | Version/Recorded line 3 edited | B1c (REVIEW; prefer a new revision line over editing line 3) |

Expected shape of a clean diff: deleted lines D is small (at least 11: one reverse cross-reference for each of V01, F26, F81, F59, F18, X16, F01, F79, F72, F12, F20;
plus the count/coverage lines and section 6 intro qualification, if any). D greater than about 40 is unexplained churn: read every line. The script prints every accepted edit under `B1a`
so I can eyeball them.

## 3. Mechanical checks (script) and their pass conditions

### 3.1 Scope and hygiene

| Id | Check | Pass |
|---|---|---|
| A1 | only `Homayar/AppVision.md` differs from 7112407 (tracked, staged, untracked) | exact one path; `vision/AppVision-Status-Notes.md` and `ReportAppvisionProgress.md` untouched |
| A2 | LF only, final newline | as base |
| A3 | no Arabic/Persian script in added lines (12.4 states an English-only body) | none |
| A4, A4p | no credential-like string; no `/root/`, `/home/user`, `/tmp/`, session URLs or `foreman/2026` in added lines | none |

### 3.2 Table integrity (AC verification bullet 2)

| Id | Check | Pass |
|---|---|---|
| C1 | every table has a separator row of the same width as its header and every row has that many cells (unescaped `|`, GFM rule); violations already present at base do not count (base has 0) | 0 new |
| C2 | every touched table has a blank line before and after and no empty ID/name cell | 0 |
| C3 | code fences balanced | balanced |
| E-Nxx-modes | an N row in a matrix with S/P/H/O columns has each of the four cells in `{Y, L, C, em dash}` | 4 valid cells |

### 3.3 ID uniqueness and reference integrity (AC verification bullet 3)

| Id | Check | Pass |
|---|---|---|
| D1 / D1u | each of N01..N15 has **exactly one** matrix row (first cell = the id) and **at most one** detail block (heading or bold-label paragraph) and at most one HTML anchor; any additional table keyed by an N id in its first cell counts as a second definition | 15 x (1 row, 0-1 block) |
| D2 | no other N token anywhere: no N00, N16+, N1, N001 | none |
| D3 | each original ID row appears exactly as often as at base | equal |
| D4 | no new duplicate heading slug that something links to | none |
| D5 | every F/V/P/A/C/G/K/X/N/GR/RG/D/WP id in added lines exists (F01-F95, V01-V07, P01-P06, A01-A06, C01-C07, G01-G05, K01-K07, X01-X24, N01-N15, GR01-GR40, RG01-RG10, D01-D18, WP01-WP24; WPxx-Txx must exist in `docs/planning/backlog.json`) | none dangling |
| Manual | `for i in $(seq -w 1 15); do echo N$i rows=$(grep -cE "^\| \**N$i\b" f) heads=$(grep -cE "^#{1,6} +N$i\b" f); done` | rows=1 for all |

## 4. Per-item checklist N01-N15 (AC1, AC2, AC3, owner section 10, section 18)

### 4.1 Required content for every N item

Each field must be recorded as a **labelled field with content** (list item `**Label:** text`, bold paragraph `**Label.** text`, sub-heading, or a table
column / two-column row). A word that only occurs in other prose does not count (script `E-Nxx-fieldsp` FAIL).

| # | Field | Source | Content rule (reviewer) |
|---|---|---|---|
| 1 | Purpose | AC2; owner 10 "purpose" | states what the backend capability is for; not a copy of the title |
| 2 | Backend contract | owner 10 | the owner's "Implement:" list for that item is represented (script anchors, 70% floor, each missing anchor a WARN) |
| 3 | Matrix membership | AC2; 18.3 | exactly one matrix row in a section 6 matrix (or the feature matrix) with S/P/H/O cells |
| 4 | Deployment modes S/P/H/O | AC2; owner 10; 18.4, 18.19 | each mode stated individually with the section 3 legend (Y/L/C/em dash); O never Y where the function needs public egress |
| 5 | Dependencies | AC2; owner 10; 18.5 | existing ids (WPxx, Fxx, Xxx, GRxx, ...) that exist; no invented ones |
| 6 | Acceptance criteria | AC2; 18.7 | testable and negative-capable (denial, cross-tenant, failure, idempotency); "works correctly" is not a criterion |
| 7 | Completion / acceptance evidence | AC2; owner 10; 18.6 | for PLANNED items: the evidence that will be required, worded as a requirement; never a result |
| 8 | Security and privacy | AC2; owner 10; 18.8 | data classes, authorization, secrets/raw content exclusions; must contain the word privacy |
| 9 | Tenant boundary | owner 10 | scope key and isolation statement; no cross-tenant access |
| 10 | Audit requirements | owner 10 | what is recorded (actor, scope, time, reason, result) |
| 11 | Failure behavior | owner 10 | explicit failed/unknown/partial states; no fabricated values (GR17) |
| 12 | Status | AC3; owner 10; 18.16 | one section 4 vocabulary token plus the reason (see 5) |
| 13 | External qualification requirements | owner 10 | what must be qualified outside the repo (live provider, legal, licence, hardware); "none" only if truly none |
| 14 | Offline implications | AC2; 18.9 | what O does: local-only equivalent or unavailable; public egress denied incl. fallback, retries, telemetry |
| 15 | Commercial / usage implications | AC2; 18.10 | ledger/plan/BYOK/cost effect; no invented prices |
| 16 | Repository candidates | AC2; 18.15 | every owner-listed candidate (table 4.2); N01 states "none/native" explicitly |
| 17 | Linear issue id and URL | AC2; 18.18 | `HOM-14x` and `https://linear.app/homayar/issue/HOM-14x` (slug, if present, exactly as in 1.2); never another item's URL |
| 18 | Cross-reference both ways | AC1; 18.17 | N block names the reused id; the reused row or its F section names the N id (11 items) |

### 4.2 Item table (script ids `E-Nxx-name`, `-modes`, `-fields`, `-fieldsp`, `-anchors`, `-owner`, `-cand`, `-linear`, `-reuse`, `-status`, `-claim`)

| N | Name must contain | Reuses | Candidates that must be recorded | Owner constraint that must survive (WARN if absent) | Reviewer expectation for O (not scripted) |
|---|---|---|---|---|---|
| N01 | onboarding | new | none stated explicitly | - | Y/L, internal mail only for invitations |
| N02 | import + migration | V01 | dlt, Meltano, Airbyte | prefer native Homayar or dlt unless connector scale justifies a larger platform | L (files/LAN sources); external connectors C |
| N03 | consent | new | OpenFGC | Homayar remains the enforcement authority | Y (local enforcement) |
| N04 | quality | F26 | Evidently, Great Expectations, Soda Core | audio-specific quality logic remains native | Y |
| N05 | reprocess | new | Temporal, Prefect, Dagster | keep Celery/outbox/lease/checkpoint; the three are candidates, not mandatory | Y with local models |
| N06 | feedback + evaluation | F81 | Label Studio, Argilla, Langfuse | no silent production training; no unauthorized customer data | L; hosted candidates unavailable |
| N07 | notification | F59 | Apprise, Novu | offline behavior stated | **L or em dash, not Y** (REVIEW `E-N07-modesO` if Y) |
| N08 | budget | F18 | LiteLLM, Helicone, Portkey Gateway | the Homayar usage ledger stays the authority | Y (local ledger); fallback/retry egress denied |
| N09 | support entitlement | X16 | Lago, Kill Bill, flagd | billing systems must not become authorization authorities | Y/L with offline signed entitlement and clock/revocation limits (see section 3 line 134); remote support stays unavailable |
| N10 | telemetry | new | OpenTelemetry Collector, PostHog, Plausible | no raw audio/transcript/secrets by default; tenant opt-out | **L or em dash, not Y**: nothing leaves the deployment (REVIEW `E-N10-modesO` if Y) |
| N11 | legacy + retirement | F01 | Semgrep, jscodeshift, ast-grep | no activation of unsafe legacy auth or graph exposure | Y (code-level) |
| N12 | disaster recovery | F79 | pgBackRest, Restic | credential exclusion; deletion-journal reapplication; legal-hold preservation; RPO/RTO are versioned per profile, D03 still open | Y; off-site copy by physical media |
| N13 | accessibility | F72 | axe-core, Pa11y (future frontend verification only) | backend only; frontend not modified; F72 UI criteria not claimed | Y |
| N14 | browser + device | F12 | Playwright, Browserslist, MDN browser compatibility data | backend only; frontend not modified | Y |
| N15 | rollback | F20 | MLflow, BentoML, KServe | prefer the existing Homayar model/provider registry; no mutable "latest" | Y (preloaded artifacts) |

The "Implement:" bullets (script `ANCHORS`) come straight from owner sections 10.1-10.15; the reviewer additionally reads each list against the record and raises a finding
for a bullet that is contradicted or watered down (for example N08 "hard stop" recorded as "warn only", N03 "enforcement before recording, processing or outbound action" recorded as advisory).

## 5. Status rules (AC3, AC6, owner 18.16, AppVision lines 7, 139-162)

| Id | Rule | Pass | Fail |
|---|---|---|---|
| E-Nxx-status (N01-N10, N12-N15) | every item is **PLANNED** (documentation-only addition) unless source evidence supports another status | PLANNED; or PARTIAL that (a) cites at least one repository path or commit that exists at base, (b) states the acceptance criteria met, (c) lists the exact gaps | VALIDATED or RELEASED (nothing but N11 is delivered); IN_PROGRESS (needs a named owner and branch; none exists); BLOCKED_EXTERNAL or PARTIAL without evidence and gaps; a token outside the 11-word vocabulary; no status at all |
| REVIEW for PARTIAL | a PARTIAL claim is never auto-accepted: I open every cited path and line at base, and confirm the criterion is really met and tested. The planner ledger marks N01, N02, N04, N05, N12, N13, N14, N15 (and N11) PARTIAL from existing primitives, but AppVision line 159-161 says overlap with a foundation "still needs verification". Safer text: PLANNED plus an "existing foundation, not yet verified against the acceptance criteria" note | | |
| E-N11-status | N11 has delivered evidence, so it is **not PLANNED**. Expected: **PARTIAL** (scoped) with the CI/merge item **BLOCKED_EXTERNAL** | PARTIAL or BLOCKED_EXTERNAL; text cites commit `d5bbe5d` (a prefix of the full SHA), names the integration branch `claude/inspiring-tesla-ie06gi`, says it is **not merged to main**, says **CI is blocked** (GitHub Actions billing/spending limit) and gives the reason for the status in words (at least 40 characters) | PLANNED; RELEASED; unscoped VALIDATED; missing SHA/branch/not-merged/CI-blocked statement; "Done" for HOM-152; a cited SHA that is not a prefix of `d5bbe5d2fc79...` |
| E-N11-status-v (REVIEW) | if the maker uses VALIDATED for N11 it must be scoped to the exact revision, mode(s) and environment (section 4: "for a specific revision, mode and environment") and must say remote CI did not run; I decide whether local-only evidence supports that word. My default: reject, use PARTIAL | | |
| E-Nxx-claim (FAIL) | no unsupported claim that anything is implemented, enabled, licensed, released or production-ready (AC6) outside N11's delivered scope | none | "already implemented", "is enabled", "production-ready" without a negation/condition |
| E-Nxx-claimw, E-Nxx-num (WARN/REVIEW) | sentences with delivered/validated/passing wording, and any cited test/CI number, are read one by one | every number equals section 1.4 or a recorded run | a test count or CI run that I cannot match |
| F6c, F6p | every commit SHA cited exists in the checkout; every cited path exists at base, or the line labels it planned/new | all resolve | fabricated commit; evidence path that does not exist |
| G5 (REVIEW) | section 6 line 237 says all rows are PLANNED; if N rows (N11 not PLANNED) sit in a section 6 matrix the sentence must be qualified by insertion | qualified | left contradicting the N11 status |
| Manual | reused rows keep their own status: F01/F20/... stay NEEDS_RECONCILIATION (line 159); the N item's status does not rewrite the reused row | unchanged | reused row promoted |

Status format: section 4 says status is stored per feature x mode x adapter x version. A single token plus reason is acceptable for a documentation-only record; any per-mode
statement must not exceed what the single token allows.

## 6. Deployment-mode rules (SPEC AC2, owner 18.19)

| Id | Rule | Pass | Fail |
|---|---|---|---|
| F5 | four profiles only: S (Online SaaS / hosted), P (Connected On-Premise), H (Hybrid), O (Fully Offline/Air-Gapped). BYOK is never a mode/profile/column; offline is never equated with on-premises | none found | "BYOK mode", "fifth mode", a BYOK column next to S/P/H/O, "offline means on-prem" |
| F5a | the added text states that BYOK describes credential ownership, not a fifth deployment mode | stated | absent |
| F5b | the added text states that fully offline processing denies public egress, including fallback, retries and telemetry | stated | absent |
| F5c (WARN) | the on-premise routing sentence: local AI, customer BYOK, approved online providers and hybrid stage routing where policy permits | present | absent |
| E-Nxx-offline (WARN), N08/N10 extra | offline text says what happens to egress in O; N08 mentions fallback/retry; N10 mentions telemetry | mentioned | silent |
| Manual | O cell honesty: a capability that needs public egress (external notification channels, hosted telemetry collectors, hosted model gateways) is L or em dash in O, never Y (X16, C01-C06 precedent) | | |

## 7. Coverage counts, cross-references, contents (AC4)

| Id | Check | Pass |
|---|---|---|
| G1 | section 12.2 (the coverage counts paragraph) mentions the 15 N items (`N01`..`N15` or "15 approved ...") | present |
| G1c | every count stated in section 12 equals a recount of the document: historical F ids = 95, expanded V/P/A/C/G/K/X rows in section 6 = 62 (or 62 + N rows if the N rows sit in a section 6 matrix, or 62 with the N items stated separately on the same line), guardrails 40, release gates 10, decision records 18 | all equal |
| G1b (WARN) | the 12.4 verification record and 12.1 sources mention the N items, so "present once in their matrices" does not understate the document | mentioned |
| G2 | the Contents block changed, every entry resolves to a heading slug, one entry names the approved backend capabilities / N01-N15 | yes |
| G2n | Contents numbering is 1..n | sequential |
| G4 | anchors and relative links added by the patch resolve; URLs added are listed (G4u INFO); I spot-check at least 3 by hand | resolve |
| E-Nxx-reuse | two-way cross-reference for the 11 reuse pairs (forward in the N record, reverse in the reused row or its F section) | 11 x present |
| Manual (non-blocking) | related rows that would benefit from an N reference: D03 and X20 with N12; D13, X09, X15, F75 with N09; GR25 with N03; K04, X11, X12, GR31 with N08; X17 with N13; V05 with N14; F44, F45, F80 with N06; F93, C05 with N07; X24, K07 with N15 | INFO |

## 8. Manual review items (the script cannot decide these; each is ticked in the review file)

- [ ] M01 Content vs owner text: for each N, read the owner's section 10.x "Implement:" list against the record; list dropped, contradicted or softened bullets.
- [ ] M02 Reuse mapping: for each of the 11 pairs, confirm the record says how N extends the reused row and does not redefine it (table 1.3); adjudicate any deviation (the reason is in the handoff, which I may read in phase B only).
- [ ] M03 Status truth: every non-PLANNED status is verified against source at base (open each cited file:line); N11 verified against section 1.4 (`git branch -a --contains d5bbe5d`, `git merge-base --is-ancestor d5bbe5d main`, ledger lines 34/142/157, numbers 199, 1109/46).
- [ ] M04 N11 gap list is honest: covered bullets vs not-covered bullets (section 1.4), HOM-158 residue, frontend follow-up, dependency pruning; nothing says all legacy is retired everywhere.
- [ ] M05 No invention: no price, SLA number, RPO/RTO value, jurisdiction rule, endpoint, table name or file path presented as existing (D03, D13 and the Gateway contract stay open owner decisions).
- [ ] M06 Candidates are candidates: no "selected", "installed", "adopted" wording (catalog rule at section 10 intro); licences not asserted; "prefer native/existing" constraints intact.
- [ ] M07 Acceptance criteria are testable (a denial case and a cross-tenant case per item where relevant) and completion evidence is a requirement, not a result.
- [ ] M08 O-mode honesty per item (section 4.2 last column) and BYOK/offline sentences (section 6).
- [ ] M09 N13 and N14 are backend-only; nothing implies frontend work in this run; F72's UI criteria are not claimed.
- [ ] M10 Read every line of `B1a` (accepted edits) and `B1b`/`B1c` REVIEW lines; confirm each is a cross-reference or a count and that the arithmetic is right.
- [ ] M11 Read the raw `git diff --no-index -U0` deletions (2.1) and confirm none is hidden by the script's normalization (whitespace).
- [ ] M12 Spot-check 3 URLs added (G4u) and the Linear ids against section 1.2; the Linear state wording for HOM-152 is "In Progress".
- [ ] M13 Rendering: the new tables/sections read correctly (column alignment, no raw pipes, headings nest sensibly, anchors work); the document stays navigable (Contents).
- [ ] M14 Scope: `git diff --stat` shows one file; no edit to `vision/AppVision-Status-Notes.md` or `ReportAppvisionProgress.md` (separate later deliverables); no claim in AppVision that they were updated.
- [ ] M15 Maker-provided table-integrity output (SPEC verification) agrees with my `C1` result.

## 9. Expected results on the clean base (phase A gate)

| Group | Expected on base | Actual (see phase-a.md) |
|---|---|---|
| N01-N15 definition, fields, status, Linear, candidates, reuse | MISSING (15 of 15) | MISSING x17 lines (D1, E-ALL, E-N01..E-N15) |
| A2, B1-B5, C1-C3, D2-D5, A3, A4, F6c, F6p, G1c, G2n, G4 | PASS (nothing changed, nothing to violate) | PASS |
| G1, G2, F5a, F5b | FAIL (AC4 and the mode sentences are not there yet) | FAIL |
| F5c | WARN | WARN |
| Exit code | 1 | 1 |

The oracle is meaningful in both directions: `selftest.py` shows a compliant synthetic document reaching exit 0 (no FAIL/MISSING) and 44 single-defect mutants each turning
the intended check red; 6 legal edits (cross-reference append, count update, extra catalog row, three field layouts) stay green.

## 10. Known limits of check.py (so I do not over-trust it)

- Field detection is label-based; content quality (M01, M05, M07) is a human judgement. The anchor test proves vocabulary, not correctness.
- The preservation rule works on characters within a matched line; a maker who moves a paragraph elsewhere is reported as a lost line (or `B1b` REVIEW), not silently accepted.
- Digit-only edits in prose pass mechanically (they are listed in `B1a`); whether the new number is right is M10 and `G1c` for section 12.
- SHA and path checks need the git checkout that holds BASE; without it they are skipped (INFO).
- No network: URLs and Linear state are checked by hand (M12).
