# Backend Mode 100 — handover (paused by owner, 2026-09-30T03:00Z)

Integration branch `claude/inspiring-tesla-ie06gi` (draft PR #13 -> main 2e834ed). **Not merged:** GitHub Actions
cannot start jobs (billing/spending limit: every job ends in 2-4 s, logs 404, main red the same way). The owner's
standing rule forbids merging with red/missing CI; merging needs an explicit owner decision.

## Delivered on the integration branch (independently reviewed, landed diff == reviewed diff)
| Commit | Linear | What | Evidence |
|---|---|---|---|
| d4daf6e | HOM-140 STAB-01 | main was red (voice/admission.py imported random) -> deterministic backoff | oracle 56/56; suite 0 failed/1055 |
| d5bbe5d | HOM-152 N11 | legacy pre-v1 stack retired (29 files, unsafe webhook secret compare) | oracle 199/199; suite 0/1109 |
| 7112407 | HOM-32 WP02-T03 | `*_FILE` secret refs, secret redaction, production SMTP/S3 rules, embedded Neo4j credential removed | oracle 584/584; suite 0/1278; 2 final security reviews |
| 6b311e0 | HOM-141 CAP | AppVision.md: N01-N15 approved capabilities | scripted oracle PASS=157 FAIL=0; 2 review rounds |

## Paused mid-flight (unreviewed; saved as patches in `wip/<team>/maker-wip.patch`, NOT applied to product code)
| Linear | Unit | Stage at stop | Approx. progress |
|---|---|---|---|
| HOM-157 REC-02 | voice admission caps proven on PostgreSQL | maker patch complete; oracle 17/18 (one case = AC3 interpretation); team review started | ~85% |
| HOM-101 WP18-T03 | versioned plans; stop startup/sign-up overwrite | oracle 98 red/28 green on base; maker mid-implementation (migration written) | ~45% |
| HOM-120 WP22-T02 | single Persian/Arabic normalization owner | oracle 22 red/374 green on base; maker building golden corpus | ~35% |
| HOM-163 STAB-02 | transport-drill race | root cause measured; SPEC with recommended 4-line fix; maker just started | ~40% |
Resume: create a worktree at 6b311e0 (or later), `git apply wip/<team>/maker-wip.patch`, re-run the team oracle
(`wip/<team>/test_oracle_*.py`) and the SPEC gates, then phase C review. Lanes: `services.sh`, `testenv-l<k>.sh`,
`LANE=k suite.sh`.

## Found and filed (Backlog)
HOM-158 README residue; HOM-159 AWS endpoint override bypasses S3 rule (High); HOM-160 model_dump exposes secrets;
HOM-161 plaintext internal transports + MinIO TLS; HOM-162 Makefile seed; HOM-164 postal-code redaction leak (High,
privacy); HOM-165 digit scripts; HOM-166 locale formatting; HOM-167 entitlement integrity gaps (High); HOM-168 retention
gap UNVERIFIED (High). Not yet filed: REC-02 maker's reproduced recover()/transition() race (deadlocks absorbed by
retry; can overwrite a just-ENDED call to FAILED/node_lost) — see wip/rec02-t1/handoff-turn-1.md.

## Not done
Remaining ~155 of the 160-item queue; ReportAppvisionProgress.md and vision/AppVision-Status-Notes.md updates; the
27-section final report; merge to main (blocked by CI billing).
