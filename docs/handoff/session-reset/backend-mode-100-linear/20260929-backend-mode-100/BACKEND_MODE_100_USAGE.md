# Backend Mode 100 — usage (mission 20260929-backend-mode-100)

Stopped by owner request (credit) at 2026-09-30T03:00Z.

| Metric | Value | Source |
|---|---|---|
| Reported session cost | **$178.66** (cost_usd) | get_session usage, 2026-09-30T02:55Z |
| Output tokens | 2,759,530 | same |
| Input tokens | 33,436 | same |
| Cache read / write tokens | 376,627,078 / 10,685,711 | same |
| Subagent runs recorded | 30 | usage/agents.jsonl (task-notification usage; a few early runs unrecorded) |
| Subagent tokens recorded | 8,555,650 | same |

Whether cost_usd includes subagent usage is NOT verified.

Subagent tokens by unit: HOM-32 2.45M (3 maker turns, team review, 2 final security reviews); AppVision N01-N15 2.32M;
HOM-152 0.93M; HOM-120 0.62M (stopped); HOM-101 0.60M (stopped); HOM-157 0.51M (stopped in review); planning/other
0.46M; HOM-140 0.36M; HOM-163 0.30M (stopped).

Snapshots: $1.85 (02:37Z 09-29) -> $33.41 (20:49Z) -> $59.79 (22:57Z) -> $93.45 (01:06Z 09-30) -> $178.66 (02:55Z).
The parallel batch (4 lanes + docs, from 01:05Z) roughly doubled the burn rate (~$45/h vs ~$13-17/h).
