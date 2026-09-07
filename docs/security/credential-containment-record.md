<!--
Provenance: draft v0.1 generated on 2026-09-07 by `zai-coding-plan/glm-5.2` through the `opencode` CLI
from three redacted inputs only (WP02/README.md; docs/security/REMAINING-P0.md; blueprint Section 17
"Current findings and scope" / "Target threat controls"). Reviewed by Claude (Fable 5.1): screened for
credential-shaped strings (none), commit ids and scan statistics cross-checked against the inputs, two
factual additions made (docker-compose default path; legacy master not in local clone). No credential
was tested, rotated or revoked. Owner review pending; every "OWNER ACTION REQUIRED" line is open.
-->

# WP02 — Credential containment record and owner runbook (draft v0.1)

## 1. Purpose, scope, non-goals and status

This document is the WP02-T01 credential-owner inventory and containment record, the WP02-T02 copied-artifacts and history-cleanup outline, and the WP02-T04 secret-scanning plan for Homayar. Its purpose is to give each credential owner the safe, value-free inventory they need to act: where credential-shaped material exists, in which commits, and what the owner must do.

Scope: the product repository master history and the artifacts derived from it, as described in `security_REMAINING-P0.md` and `blueprint_security_findings_controls.md`. The underlying audit traversed 15 reachable commits, 8,683 blobs and approximately 72 MB in the mirrored product refs and detected credential-shaped material at 11 source/binary locations. It is a pattern-based audit, not proof of absence of other secrets.

Non-goals: no credential was tested, rotated or revoked in preparing this draft. No penetration test or production access was performed. This document contains no credential values, prefixes or examples; credentials are referenced only by family ID, file path, commit ID and category. History rewriting is discussed as a separately approved plan and explicitly does not substitute for revocation.

Status: draft for owner review. All containment items are open until the owners listed below act and record evidence.

## 2. Credential family inventory (WP02-T01)

| Family ID | Location class | Paths and commits | History scope | Severity | Owner role | Required owner action | Containment status | Evidence reference |
|---|---|---|---|---|---|---|---|---|
| CF-01 | Source file | `app.py:17`; `chat_intell.py:6`; `org_intell.py:7` — present in master tip `3766bc90` and initial commit `19a2b2ae`, with intervening master history | Reachable master history, initial commit through tip | P0 | AI/model provider account owner (exact provider and account to be confirmed by repository administrator) | Identify the credential in the provider console; review usage/access logs for the exposure window; revoke/rotate through the approved process; replace code defaults with secret references | Open | security_REMAINING-P0.md, findings table, row 1 |
| CF-02 | Source file | `codes/common_issues/common_issues_Json.py:8`; `codes/nlp_db_acces.py:6` — same reachable master history | Same reachable master history | P0 | Database administrator and provider account owner (identities to be confirmed) | Same response as CF-01; additionally review copied examples and downstream repositories | Open | security_REMAINING-P0.md, findings table, row 2 |
| CF-03 | Compiled cache | `__pycache__/chat_intell.cpython-313.pyc`; `__pycache__/org_intell.cpython-313.pyc`; `codes/QC/Agentic/__pycache__/qc_agentic.cpython-313.pyc` | Same reachable master history | P0 | Repository administrator | Binary artifacts can preserve credentials; include them in the cleanup and redistribution inventory; confirm no downstream copies | Open | security_REMAINING-P0.md, findings table, row 3 |
| CF-04 | Compiled cache | `codes/__pycache__/nlp_db_acces.cpython-313.pyc`; `codes/common_issues/__pycache__/common_issues_Json.cpython-313.pyc`; `codes/resume_analysis/__pycache__/resume_analysis.cpython-313.pyc` | Same reachable master history | P0 | Repository administrator | Same response as CF-03; exclude compiled caches from future source artifacts | Open | security_REMAINING-P0.md, findings table, row 4 |
| CF-05 | Config default (historical) | Bootstrap admin password; removal in commit `a1ad61a` does not erase earlier history | All reachable history prior to and including the removal commit | Under review — treat as P0 until owner confirms | Repository administrator / application owner | Confirm whether the historical value was ever used in a deployed environment; if so, treat as a rotated-admin-credential case and review privileged access for the exposure window | Open | security_REMAINING-P0.md, narrative paragraph on defaults |
| CF-06 | Config default (committed) | Development defaults in committed compose/config files, including the literal graph-database bootstrap credential in `Homayar/docker-compose.yml:9` (`NEO4J_AUTH`, value not reproduced) | Reachable master history wherever the files exist | Under review — defaults/placeholders requiring review, not confirmed live leaks | Repository administrator / deployment owner | Review each default; replace with secret references and mandatory secure production settings before external pilot access | Open | security_REMAINING-P0.md, narrative paragraph on defaults |
| CF-07 | Environment file (untracked, local) | Untracked local product environment files containing nonempty provider/auth/database credential settings | No Git history scope; local only — not a leak by itself | Conditional — becomes an incident only if propagated | Repository administrator and each affected credential owner | Ensure these files never enter source, container layers or the website ZIP; verify ignore rules, image build context and ZIP packaging | Open | security_REMAINING-P0.md, narrative paragraph on untracked environment |

OWNER ACTION REQUIRED: The repository administrator must confirm the accountable owner (provider account owner vs. database administrator) for CF-01 and CF-02 before any rotation can proceed, since credential validity and ownership were not tested by the audit.

## 3. Owner runbook per family

One generic runbook, applied per provider type. Evidence recorded at each step must reference only family ID, path, commit and category — never values.

1. **Identify** the credential in the provider console (API-keys/credentials page, database user list, or admin account page) by matching creation dates, description fields and the consuming application, not by pasting the value anywhere.
2. **Review usage and access logs** for the exposure window (from initial commit `19a2b2ae` or first known introduction through today) for anomalous use.
3. **Revoke/rotate through the approved process** for that provider type: issue a replacement, update the secret manager, then revoke the old value.
4. **Replace consumers** with secret references; the code defaults in CF-01/CF-02 locations must read from the secret reference, and CI/deploy tooling must inject it.
5. **Record evidence**: who acted, when, ticket reference, provider console confirmation, and log-review result.
6. **Confirm no remaining use**: search the product repository (including compiled caches) for the retired credential's identifier, and verify dependent services still function.

Per provider type:
- **AI/model provider account owner (CF-01):** rotate the API credential in the provider console; check billing/usage logs for unfamiliar consumption; scope the replacement by project and environment.
- **Database administrator (CF-02, graph/relational as deployed):** rotate the database user's password or the provider credential; check connection and admin-access logs; apply least-privilege roles to the replacement.
- **Repository administrator (CF-03, CF-04, CF-05, CF-06):** for bootstrap/default credentials, force a documented admin-password change on every environment that ever ran the historical value; confirm committed defaults are replaced by secret references with mandatory secure production settings.
- **All owners (CF-07):** confirm the local environment files are ignored by version control, excluded from image build contexts, and absent from every produced archive.

OWNER ACTION REQUIRED: Each owner listed above must complete steps 1–6 for their families and return the evidence record; no rotation is performed by this draft.

## 4. Copied artifacts, forks and caches inventory (WP02-T02)

History or compiled artifacts may have been copied beyond the mirrored refs. Each item below is a check with a record to keep. Treat all items as inventory work, not as proven leaks.

| Copy surface | How to check | What to record |
|---|---|---|
| Remote forks/mirrors | List forks and mirrors of the product repository in the hosting platform; check for refs containing commits `19a2b2ae`..`3766bc90` and `a1ad61a` ancestry | Remote URLs, owner contacts, whether the 11 locations are reachable, takedown/request outcome |
| Other clones on developer machines | Ask each contributor whether a local clone exists; run the history scan (§6) on each clone | Machine owner, clone path, scan result, re-clone date after rewrite |
| Deployment ZIPs | A cPanel deployment ZIP and a handoff ZIP exist in the workspace root; unzip into a quarantine directory and scan contents, including any `__pycache__` directories and environment files | ZIP name/hash, whether CF-01..CF-07 material is present, disposition decision |
| Compiled `__pycache__` files | Scan working trees, archives and extracted images for `.pyc` files matching the CF-03/CF-04 paths | Location list, deletion/rewrite completion |
| Container images and registries | Inspect image layers in local and remote registries for the CF-03/CF-04 caches and CF-07 environment material | Registry/repo/tags, layer-scan result, rebuild or deletion status |
| CI caches | Inspect CI cache entries (dependency caches, workspace caches) that may embed historical file trees | CI system, cache keys, purge confirmation |
| Backup archives | Check backup locations of the repository and workspace for pre-cleanup snapshots | Backup location, date range, retention decision |
| Chat/AI tool transcripts | Review assistant transcripts and shared snippets for pasted credential material from the affected files | Presence/absence, deletion request where applicable |

OWNER ACTION REQUIRED: The repository administrator must inventory the two workspace-root ZIPs and every fork/mirror, and record disposition for each before the history rewrite is scheduled.

## 5. History-cleanup plan outline

This plan is an outline only and requires separate approval. Note: the legacy `master` ref (`3766bc90`) is not present in the local product clone; the audit inspected it through a separate mirror, so every step below must first fetch that history deliberately into an isolated mirror. Preserve incident evidence first: before any rewrite, snapshot the pre-rewrite refs, the scan report and owner evidence, so the incident remains reconstructable.

Sequence:
1. Owners revoke/rotate affected credentials (§3). Rotation comes first and is never replaced by rewriting.
2. Evidence snapshot archived by the repository administrator.
3. Rewrite plan finalized; OWNER ACTION REQUIRED: the repository administrator selects and approves the history-rewrite tooling and target refs (all 11 locations plus the `a1ad61a` pre-history).
4. Force-push authorization: OWNER ACTION REQUIRED: the repository administrator authorizes the force-push window and freezes writes.
5. Collaborators re-clone; stale clones and caches are purged per §4.
6. Verification scan (§6) over the rewritten history confirms the locations are gone.

History rewriting does not substitute for revocation; any credential reachable in old history must be considered retired only after rotation.

## 6. Secret scanning plan (WP02-T04)

Existing coverage: the outer repository's CI includes a "Secret scan" gate that runs Gitleaks over the full Git history with redaction. It covers only the website repository. Still needed:

- **Product repository history scan**: the same full-history, redacted scanning approach applied to the product repository (including mirrored refs), scheduled and on demand.
- **Pre-commit or pre-push hook**: developer-side redacted scanning so new commits never reintroduce credential material; hook installation documented for all contributors.
- **Release-content scan**: every release artifact (source archives, handoff packages, the cPanel ZIP class of artifacts) scanned before publication.
- **Container image layer scan**: scan built image layers, matching CF-03/CF-04/CF-07 concerns.
- **Support-bundle scan**: diagnostic/support bundles scanned before leaving the environment, consistent with the canary-secret proof in the blueprint controls.
- **Allowlist/review process**: a reviewed allowlist for known placeholders/false positives, with owner approval per entry and no silent suppressions.
- **Gate definition**: "zero unreviewed findings" — a scan passes when every finding is either resolved or explicitly allowlisted with a recorded reviewer decision; any unreviewed finding blocks merge/release.

OWNER ACTION REQUIRED: The repository administrator and security owner must approve the gate definition and the allowlist review process before F03 can be declared satisfied.

## 7. F03 acceptance mapping

F03: "Credential-owner containment is recorded for each suspected credential family; new source/history-distribution/image/support-bundle scans have zero unreviewed credential findings; no values enter reports/logs."

| F03 clause | Satisfying artifact/evidence |
|---|---|
| Containment recorded per suspected family | §2 inventory table (CF-01..CF-07) plus completed §3 runbook evidence records per family |
| Source/history-distribution scans: zero unreviewed findings | §6 product-history scan report, hook enforcement record, release-content scan reports, §4 fork/clone/ZIP inventory results |
| Image/support-bundle scans: zero unreviewed findings | §6 image-layer and support-bundle scan reports with allowlisted findings and reviewer decisions |
| No values enter reports/logs | This document's redaction rule; evidence templates referencing only family ID/path/commit/category; redacted CI gate output |

## 8. Open owner decisions

1. OWNER ACTION REQUIRED: Confirm accountable owners for CF-01 and CF-02 (repository administrator, with provider console access).
2. OWNER ACTION REQUIRED: Each owner completes the §3 runbook (identify, review logs, revoke/rotate, replace consumers, record evidence, confirm no remaining use).
3. OWNER ACTION REQUIRED: Inventory and record disposition of the workspace-root cPanel and handoff ZIPs and all forks/mirrors (repository administrator).
4. OWNER ACTION REQUIRED: Select and approve history-rewrite tooling and refs for the §5 plan.
5. OWNER ACTION REQUIRED: Authorize the force-push window and coordinate collaborator re-clones.
6. OWNER ACTION REQUIRED: Approve the §6 gate definition and allowlist review process (repository administrator and security owner).
