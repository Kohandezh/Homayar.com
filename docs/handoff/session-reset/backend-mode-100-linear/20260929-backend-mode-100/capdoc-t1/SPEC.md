# SPEC — team capdoc-t1 (mission 20260929-backend-mode-100) — documentation unit

Orchestration mode: `foreman_native_adapted`. Documentation-only unit: the checker writes an observable CHECKLIST
(phase A) from this SPEC before seeing the patch, instead of a test file. Two-round cap. No lane (no database).

## Task
- Owner master prompt §18 "APPVISION UPDATE" and §10 "APPROVED MISSING BACKEND CAPABILITIES" (verbatim copy:
  /root/foreman/20260929-backend-mode-100/planning/owner-approved-capabilities-N01-N15.md — authoritative text).
- Linear: capability parent HOM-141 [CAP]; N01..N15 = HOM-142..HOM-156 (https://linear.app/homayar/issue/HOM-142 ...
  HOM-156). Delivered so far: N11 (HOM-152, commit d5bbe5d, legacy retirement).
- Type: documentation. Work unit: AppVision.md lacks the 15 approved backend capabilities.

## Base
`BASE_SHA` = `7112407` (integration head).

## Context (verified by Foreman)
- `Homayar/AppVision.md` (1457 lines): status vocabulary at :139-160 (PLANNED, ..., VALIDATED, RELEASED; per feature x
  mode x adapter x version); §6 expanded capability matrices at :235-330 (rows like `| V01 | ... |` with S/P/H/O
  columns); feature table F01-F95 from ~:750; deployment modes §3 (:104-137).
- Dedup decided in the mission plan (reuse existing IDs where an approved capability already exists, per §18 item 17):
  N02->V01, N04->F26, N06->F81, N07->F59, N08->F18, N09->X16, N11->F01, N12->F79, N13->F72, N14->F12, N15->F20;
  N01, N03, N05, N10 are new. Planner evidence: /root/foreman/20260929-backend-mode-100/planning/PLAN.md and
  coverage-ledger.json (N-rows). The maker must verify each mapping against AppVision's own row text and may propose
  a different mapping ONLY with a stated reason in the handoff (Foreman decides).

## Acceptance criteria (observable in the document)
1. All 15 approved capabilities from §10 appear in AppVision.md with stable IDs N01-N15 (the N-ID is kept as the
   capability ID even where it reuses/extends an existing row; the reused row is cross-referenced both ways).
2. Each N-item records, per §18: purpose; the capability matrix it belongs to (added to the relevant §6/feature
   matrix); deployment-mode applicability for S/P/H/O (Online SaaS, Connected On-Premise, Hybrid, Fully
   Offline/Air-Gapped; BYOK is credential ownership, not a fifth mode; fully offline denies public egress incl.
   fallback, retries and telemetry); dependencies; acceptance criteria; completion evidence; security/privacy
   implications; offline implications; commercial/usage implications; repository candidates where relevant; the
   Linear issue ID and URL.
3. Status: every N-item is PLANNED unless source evidence supports another status; N11 records its delivered evidence
   (commit d5bbe5d on the integration branch; not merged to main; CI blocked) without claiming VALIDATED/RELEASED
   beyond what the status rules allow — state the exact status and why.
4. Coverage counts and cross-references in AppVision.md are updated to include the N-items; the contents/TOC is
   updated.
5. Preservation: no original feature row (F01-F95, §6 rows, GR/RG rows) is removed or renumbered; the technology
   catalog is preserved; the diff to existing lines is limited to counts, cross-references, TOC and inserted rows.
   (Checker verifies with a line-level diff: every deleted line is replaced by a line carrying the same ID.)
6. No capability is claimed implemented, enabled, licensed or production-ready without evidence (AppVision :7 rule).

## File ownership
Maker may change ONLY `Homayar/AppVision.md`. Must not change any other file (vision/AppVision-Status-Notes.md and
ReportAppvisionProgress.md are separate, later deliverables).
Checker: only its checklist and records under /root/foreman/20260929-backend-mode-100/capdoc-t1/{oracle,reviews}/.

## Verification
- `git diff --stat` shows only Homayar/AppVision.md.
- Markdown table integrity: every table row in touched tables has the same column count as its header (maker provides
  a small script output).
- ID uniqueness: each of N01..N15 is defined exactly once as a row/section anchor.

## Worktrees
- Maker: /root/foreman/20260929-backend-mode-100/wt/capdoc-t1 (branch mission/capdoc-t1 at 7112407)
- Checker: /root/foreman/20260929-backend-mode-100/wt/capdoc-t1-check (detached at 7112407)

## Envelope / turn protocol
As in stab01-t1/SPEC.md, team `capdoc-t1`, names `capdoc-t1-impl`, `capdoc-t1-rev`.

## Models
Maker: opus (product atlas; correctness of scope claims matters). Checker: sonnet.
- Foreman ruling (capdoc): N01,N02,N04,N05,N12,N13,N14,N15 stay PLANNED (AppVision PARTIAL = some acceptance criteria met; none ran); ledger PARTIAL means code foundations only.
