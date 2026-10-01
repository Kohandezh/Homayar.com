# n11-t1 review, phase C turn 2 (n11-t1-rev, team reviewer)

- Patch: the refreshed /root/foreman/20260929-backend-mode-100/wt/n11-t1-check/REVIEW-INPUT.diff (bare `git diff -D`)
- SPEC: /root/foreman/20260929-backend-mode-100/n11-t1/SPEC.md, including A5 (the AC9 residual plus the dangling pointer) and A6 (jwt_expiry_hours recorded as a follow-up)
- Method: I built a fresh throwaway copy (`git archive 692affb`, the same 29 deletions removed, the 6 content hunks applied cleanly with `git apply`, package directories removed). I then ran `diff -r` against my turn-1 patched copy. My checkout's tracked files were not touched. I did not read the maker worktree or any handoff.

## Verdict

Mergeable as-is. The revision does what A5 lists, and nothing else:

- The four named Features bullets are gone: VOIP Integration, LDAP/AD Sync, AI Copilot, Graph Explorer.
- "29 endpoints across 7 routers" is replaced by a neutral pointer to the v1 route inventory. The link resolves to `Homayar/docs/security/authorization-matrix.md`, whose §6 is "Route inventory (machine-checked)".
- The whole "## VOIP Webhook Integration" section and its curl examples are removed.
- The inventory pointer now names `Homayar/docs/handoff/session-a/adr/README.md`.

The tree diff against turn 1 shows only `Homayar/README.md` and `Homayar/docs/architecture/legacy-retirement.md` changed. Every Python file is byte-identical, which independently confirms Foreman's statement.

Two low residuals remain in the README. They fall outside A5's enumerated list, which again says "no other README rewrites", so the maker could not touch them. I recommend Foreman add them to the recorded README follow-up, or amend if it wants them in this unit. They do not block the merge.

## Findings

1. **low, Homayar/README.md:162–163 (Security section; same file as the diff's README hunks).** The README still lists "Cypher injection protection (label whitelist, depth clamping)" and "Webhook secret verification" as security properties. Both describe code this change deletes. The depth clamp is `services/graph_service.py:75` at base (`safe_depth = max(1, min(int(depth), 3))`), and the Cypher templates were in `services/copilot_service.py`. The webhook secret check is `routers/webhook.py:46–48,107–109`.
   - Why it matters: AC9 says the README must not keep documenting what this change removes. "Webhook secret verification" also advertises, as a control, the very check the new inventory records as unsafe (not constant-time, fail-open when the secret is empty). There is no runtime effect.
   - I missed these two lines in turn 1. They are not on A4's list or A5's.
   - Trigger: `sed -n 156,164p Homayar/README.md` on the patched tree.
   - Smallest fix: Foreman adds README lines 162–163 to the README follow-up, or amends so the maker deletes both bullets.

2. **low, Homayar/README.md:40 and :42 (Features).** "**Dashboard** — 8 KPI cards, QC trend charts, sentiment distribution, issue tracking" and "**PDF Export** — Generate and download professional call reports" remain.
   - Why it matters: the Dashboard bullet mirrors `routers/dashboard.py`'s eight GET endpoints (kpis, qc-trend, sentiment, issues, …). PDF Export is `services/pdf_service.py`. This change retires both. The inventory itself says there is no cross-call KPI dashboard and no PDF export. The console's `DashboardPage` is not imported anywhere (finding 3).
   - A5's parenthetical named four capabilities, and the maker followed it literally. These two lines sit inside A5's own "around lines 40-47" range, so the list was probably meant to be broader.
   - Trigger: `sed -n 38,43p Homayar/README.md` on the patched tree.
   - Smallest fix: the same as finding 1. Add both lines to the follow-up, or amend and delete them.

3. **info, pre-existing, widens turn-1 finding 4 (frontend follow-up).** The unreachable legacy console is larger than `CopilotPage`. `components/graph/GraphPage.tsx:83` calls `/graph/entities`. `components/incidents/IncidentsPage.tsx:31` calls `/dashboard/call-problems`. `components/analytics/AnalyticsPage.tsx:21,29` call `/dashboard/issues` and `/dashboard/call-qc-details`. `lib/hooks.ts:7–58` (`useKpis`, `useQcTrend`, `useSentiment`, `useIssues`, `useRecentCalls`, `useAggregatedIssues`) call `/dashboard/*` for `components/dashboard/DashboardPage.tsx`. `CallsPage` and `ReportPage` use the `/calls` hooks, which were retired earlier.
   - None of these pages is imported anywhere in `Homayar/frontend/src`, so none is reachable. They were dead at base.
   - Why it matters: the frontend follow-up Foreman recorded for `CopilotPage` should cover this whole legacy component tree and the legacy hooks in `lib/hooks.ts`, not one file.
   - Fix: scope that frontend ticket accordingly. It is outside this unit's ownership.

4. **info, pre-existing content next to a line this diff added: Homayar/README.md:108–113.** The new line 108 says the product API is under `/api/v1`. The table directly below it still lists `/api/auth` and `/api/calls`, which were removed in WP03/WP04. The project-structure tree (lines 127–135) still lists `middleware/auth.py`, `routers/auth.py`, `routers/calls.py` and `services/analysis_service.py`.
   - A4 explicitly defers other stale README content, so this is recorded here only so the follow-up includes it.

## Confirmed by the oracle (execution-backed)

On the turn-2 throwaway copy, under `flock /root/foreman/20260929-backend-mode-100/heavy.lock`, with the checker venv, `PYTHONDONTWRITEBYTECODE=1`, and the copy verified as the import root in turn 1:

```
python -m pytest backend/tests/test_oracle_n11.py backend/tests/test_truthful_outputs.py \
  backend/tests/test_legacy_retirement.py backend/tests/test_authorization_matrix.py -q -p no:cacheprovider
267 passed, 129 warnings in 13.49s
```

- That is my oracle's 199 (all 129 base-red cases green, all 70 controls green), plus test_truthful_outputs 27, test_legacy_retirement 35 and test_authorization_matrix 6.
- A3 still holds: all 29 deleted files are listed individually by full repository path in the inventory.
- AC9/A5 by script: none of `LLM_MODEL_QC`, `LDAP_`, `VOIP_WEBHOOK`, `/api/dashboard|graph|copilot|webhook|ldap`, "endpoints across", or the four A5 feature names remain in the README. Both README links resolve.
- Because the Python files are byte-identical to turn 1, the turn-1 execution evidence carries over unchanged. That covers the import sweep of 162 live modules with 0 failures and clean ruff on the changed files.

## Not verified

- Full suite: not re-run by me. Foreman's turn-1 gate (0 failed / 1109 passed / 46 skipped) stays valid because no Python changed between turns, which my tree diff confirms.
- A2 in the maker's own worktree (no `__pycache__`-only leftovers): not visible from a bare diff. I rely on Foreman's record.
- Finding 1's claim that no live code does Cypher label allowlisting is based on a grep for Cypher and label handling across non-test `backend/`. It found only retired services, `seed.py`, `jobs/processor.py`, `providers/schemas.py` and `quality_service.py`. I did not read those four live files in depth.

CANDIDATES critical=0 high=0 medium=0 low=2 info=2
