# Security work remaining

Preparation status: no credentials were tested, rotated or revoked. No penetration test or production access was performed.



The secret scan traversed **15 reachable commits, 8,683 blobs and approximately 72 MB** in the mirrored product refs. It detected credential-shaped material at 11 source/binary locations in master history. This is a pattern-based audit, not proof of complete absence of other secrets. Credential validity and ownership were not tested. No secret values are included here.

| Location in master history | Historical scope | Severity | Remediation |
|---|---|---|---|
| app.py:17; chat_intell.py:6; org_intell.py:7 | Present in master tip 3766bc90 and initial commit 19a2b2ae, with intervening master history | P0 | Revoke/rotate associated provider credentials, review usage/access, replace code defaults with secret references. |
| codes/common_issues/common_issues_Json.py:8; codes/nlp_db_acces.py:6 | Same reachable master history | P0 | Same response; review copied examples and downstream repositories. |
| __pycache__/chat_intell.cpython-313.pyc; __pycache__/org_intell.cpython-313.pyc; codes/QC/Agentic/__pycache__/qc_agentic.cpython-313.pyc | Same reachable master history | P0 | Binary artifacts can preserve credentials; include them in cleanup and redistribution inventory. |
| codes/__pycache__/nlp_db_acces.cpython-313.pyc; codes/common_issues/__pycache__/common_issues_Json.cpython-313.pyc; codes/resume_analysis/__pycache__/resume_analysis.cpython-313.pyc | Same reachable master history | P0 | Same response; exclude compiled caches from future source artifacts. |

The scanner also identified known defaults/placeholders and short hardcoded assignments requiring review. These are not all live leaked credentials. Historical bootstrap password removal in a1ad61a does not erase earlier history. The untracked local product environment contains nonempty provider/auth/database credential settings; that alone is not a leak, but those files must never enter source, container layers or the website ZIP. Rotation and history rewriting are recommendations only; neither was performed.

Legacy authentication only decodes JWT in its common dependency and does not consult the Redis blacklist. Main/dev overlapping auth implementations therefore do not share a single revocation boundary. Legacy self-update accepts a broad UserUpdate object including role-related fields, while authorization compares token subject and path identity; treat privilege-field editing as a security defect to remove and test, without assuming the ID mismatch is a proven live escalation. The legacy graph's default MATCH-all path returns complete node properties, potentially including user credential hashes. [Legacy auth](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/middleware/auth.py:12), [update route](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/routers/auth.py:88), [graph serializer](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/services/graph_service.py:22)

The W v1 stack is materially better: session hashes, active/revoked checks, session version, workspace membership checks, permission dependencies and CSRF protection. Preserve it, then prove refresh reuse detection, concurrent revocation, password reset invalidation, verified account linking and secure cookie behavior. A public share requiring “authentication” currently checks that a user exists, not necessarily membership in the source workspace; that can be a deliberate bearer-link product policy, but must not be described as organization-only access. Default private contact-center sharing to disabled or named authorized recipients. [Actor/workspace boundary](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/security/context.py:98), [share access](/Users/emperor/Documents/AI/Homayar.com/Homayar/backend/routers/v1/shares.py:247)


## Containment record

The owner-facing inventory and runbook (families CF-01..CF-07, artifact inventory checklist, cleanup outline, scanning plan) is in [credential-containment-record.md](credential-containment-record.md) (draft v0.1, 2026-09-07).

## Start here

Use [WP02](../planning/work-packages/WP02/README.md) for credential-owner containment and deployment security, [WP03](../planning/work-packages/WP03/README.md) for unified identity/tenancy and [WP23](../planning/work-packages/WP23/README.md) for enterprise/security qualification. Legacy paths are disabled by default in W; preserve that boundary. Never paste secret values into Claude, source, plans, logs or test artifacts.
