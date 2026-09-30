# Handoff: n11-t1, phase B, turn 2 (fix turn, SPEC amendment A5)

- FROM: n11-t1-impl. TO: foreman.
- Worktree `/root/foreman/20260929-backend-mode-100/wt/n11-t1`, branch `mission/n11-t1` at `692affb`. Not committed.
- This turn changed only two files: `Homayar/README.md` and `Homayar/docs/architecture/legacy-retirement.md`. No Python file was touched. `find . -newer handoffs/turn-1.md -type f`, excluding `.git`, `.venv`, `node_modules` and caches, lists exactly those two paths.

## Changes

`Homayar/README.md` (A5, on top of the turn-1 A4 edits):

1. **Feature list.** Removed the four lines that advertised retired capabilities: `**VOIP Integration** — Webhook receivers…`, `**LDAP/AD Sync** — …`, `**AI Copilot** — …` and `**Graph Explorer** — …` (base lines 44–47). The other feature lines are unchanged.
2. **Endpoint-count sentence.** Replaced `29 endpoints across 7 routers:` (base line 128) with a neutral pointer that states no count: "The product API is served under `/api/v1`; its route inventory is in [docs/security/authorization-matrix.md](docs/security/authorization-matrix.md) (§6)." The link resolves to `Homayar/docs/security/authorization-matrix.md`, whose "## 6. Route inventory (machine-checked)" section is the one pinned by `test_route_inventory_document_matches_code`.
3. **Webhook section.** Removed the whole "## VOIP Webhook Integration" section: heading, intro, both curl examples to `/api/webhook/voip/call-event`, the auto-create sentence and the trailing blank line (base lines 194–212). "## Security" now follows the Project Structure block directly.

`Homayar/docs/architecture/legacy-retirement.md`: the pointer in "Unchanged by this retirement" now reads "(see `Homayar/docs/handoff/session-a/adr/README.md`)" instead of "(see that directory's `README.md`)".

Cumulative README diff against base, from `git diff -U0 Homayar/README.md`: only deletions, plus two added lines, the `/api/v1` pointer and the turn-1 retirement pointer (and its blank separator).

## Verification

Required grep:
```
$ grep -n "webhook/voip\|call-event\|LDAP\|Copilot\|copilot" Homayar/README.md
115:The legacy dashboard, graph, Copilot, VoIP webhook and LDAP routers, their services and settings are retired: see [docs/architecture/legacy-retirement.md](docs/architecture/legacy-retirement.md).
148:│       │   └── copilot/        # AI chat interface
```
Neither hit advertises a retired feature:
- Line 115 is the retirement pointer that A4 requires.
- Line 148 is the Project Structure entry for the console directory `frontend/src/components/copilot/`. That directory still exists (`CopilotPage.tsx`), and frontend files are outside this unit's ownership; A5 did not list the frontend tree lines.

`webhook/voip` and `call-event` no longer appear.

Focused command, under the lock, from `wt/n11-t1/Homayar` after `source testenv.sh`:
```
$ flock /root/foreman/20260929-backend-mode-100/heavy.lock uv run pytest backend/tests/test_truthful_outputs.py backend/tests/test_legacy_retirement.py backend/tests/test_authorization_matrix.py -q -p no:cacheprovider
68 passed, 129 warnings in 10.43s
```
The 68 tests are: `test_truthful_outputs` 27, `test_legacy_retirement` 35 and `test_authorization_matrix` 6. No Python changed, so the turn-1 ruff and full-suite results (0 failed / 1109 passed / 46 skipped) still apply.

## Residual README content left as it is (outside A5's list; recorded, not fixed)

- Feature lines `**Dashboard** — 8 KPI cards…` and `**PDF Export** — …`. Their backend (`routers/dashboard.py`, `services/pdf_service.py`) is retired, but A5 names only the LDAP, Copilot, VOIP and graph lines.
- The Security bullets "Cypher injection protection (label whitelist, depth clamping)" and "Webhook secret verification".
- The API table rows `/api/auth` and `/api/calls` (retired before this unit), and the `routers/` tree that ends on `├── calls.py`.
- The frontend tree lines `graph/` and `copilot/`.
- A6 (`jwt_expiry_hours` has no consumer) is not part of this unit.
