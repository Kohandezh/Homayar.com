---
name: homlead
description: Required for any Linear ticket work in Homayar (team HOM). The session becomes the leader of leaders (homlead), takes tickets from Linear by priority, runs up to 6 foreus leaders in parallel Herdr tabs, merges their PRs when verified, and cleans up after each one. Use when the owner asks to start, continue, or follow up on HOM tickets.
---

# Homlead: leader of leaders

## Rule

In this repo, Linear tickets are done only through this workflow. Do not implement a ticket directly in one session.
The work is done by `foreus` leaders, one per ticket, each with its own maker and checker.
Homlead plans, starts, unblocks, verifies, merges and cleans up. Homlead never edits a deliverable itself.

Inside a mission (a `foreman-<ticket>` leader, a maker or a checker), do not use this skill. Follow your mission contract.

## Needs

- The session runs inside Herdr (`test "${HERDR_ENV:-}" = 1`). If not, say so and stop.
- The `herdr` and `foreus` skills are loaded. `foreus` is a user skill, not in this repo.
- The Linear MCP and the `gh` CLI work.
- The `claudepersonal` command starts Claude Code for each leader.

## 1. Become homlead

```bash
herdr agent rename "$HERDR_PANE_ID" homlead
```

Leaders send their reports to `homlead`, so the name must match.

## 2. Pick tickets

- Read the open tickets of team HOM in Linear: assignee, cycle, priority, blockers.
- The Linear MCP may be logged in as another person. Filter by user id, not by `me`. Sina is `69bed5b6-a8c9-4af1-a673-db7fe87bc906`, Mohammad is `90e58204-03ae-47e1-8c7b-b8817c2bc9b1`.
- Order: the current cycle first, then by priority (Urgent, High, Medium, Low).
- Start a ticket only when its blockers are merged. Worktrees branch from `origin/main`, not from local changes.
- Pull main first: `git pull --ff-only`.

## 3. Shared demo backend

Missions share one demo backend to save RAM: Postgres on 4002, API on 4001, demo seed loaded. Each mission runs only its own Vite on a 4xxx port.

- If ports 4001 and 4002 are empty, open a `dashboard-demo` tab and run `scripts/demo-backend.sh`. It starts a fresh database, migrates, seeds and serves the API.
- After a backend PR merges, pull main and restart the API in that tab, or missions test old code.
- Demo sign-in: `owner@demo.example.com` / `Homayar-Demo-1405`. Missions must not reseed, reset or restart it.

## 4. Start missions (up to 6 at a time)

The owner set the limit to 6 on 2026-10-23. It comes from 16 GB of RAM and the Claude weekly limit. Each mission runs three Claude sessions and its own Postgres. Before you start one more, check swap (`sysctl vm.swapusage`) and free disk; when swap is nearly full, wait for a mission to finish.

For each ticket:

1. Move it to In Progress in Linear.
2. Run `scripts/launch.sh <TICKET> "<prompt>"`. It opens a Herdr tab named after the ticket, starts `claudepersonal`, and sends the prompt.

The prompt starts with `/foreus <TICKET> (Linear ticket in team HOM, read it with the Linear MCP).` Then it gives:

- What to build, in two or three sentences, with the endpoints or files.
- The implementer lane. A fully specified change uses GLM: `herdr agent start <maker> --kind opencode --pane <pane> --timeout 90000 -- --model zai-coding-plan/glm-5.3`. CI, infra, security or unclear work uses a Claude opus maker. The checker is always Claude opus.
- The shared backend URL and sign-in, and the mission's own Vite port.
- Which tickets run in parallel, and "rebase on latest main before you push".
- The report lines:
  - `herdr agent prompt homlead "<TICKET> done. PR: <url>. Short summary: <one line>"` when the draft PR is open and CI is green. Before that, the leader links the PR in Linear and moves the ticket to In Review.
  - `herdr agent prompt homlead "<TICKET> blocked: <question>"` when it needs a decision.
- "Leave nothing behind after merge."

## 5. Follow up every 30 minutes

Start a loop once per session:

```
/loop 30m homlead follow-up: check every foreus mission for HOM tickets. 1) List leader and worker agents (herdr agent list) and read each leader screen; cancel any "Teach auto mode about your environment?" dialog with Esc, then nudge the leader to continue; spot leaders stuck, blocked, or waiting on a lost message and unblock them within the rules (never push a leader past its own safety refusal). 2) For every open draft PR from our missions: verify and merge, mark the Linear ticket Done, ask its leader for full cleanup, verify the cleanup, exit the session and close its tab. Do not touch PRs that are not from our missions. 3) Keep up to 6 missions running. 4) Tell the owner only what changed and anything that needs their decision.
```

- `herdr agent read` with `--source recent-unwrapped` can show an old frame. Check with `herdr pane read <pane> --source visible` before you act on a dialog.
- After closing a dialog, tell the leader that a team message may have been lost behind it.

## 6. Merge

Merge only when all of these hold:

- Every CI check is green. For `website/` changes, the website job too.
- `gh pr view <n> --json mergeable,mergeStateStatus` says `MERGEABLE` and `CLEAN`.
- The changed files match the ticket. No out-of-scope or frozen files.
- The base is not stale on files that other merged PRs changed.

Then: `gh pr ready <n>` and `gh pr merge <n> --merge` (the repo uses merge commits).

Exception (owner approved): if the only red job is a failure that already exists on main, is tracked in a ticket, and the PR changes no file in that area, you may merge. Leave a PR comment that records the exception.

If anything else is off, do not merge. Tell the leader or the owner.

## 7. Clean up after each merge

The owner's rule: nothing stays behind, because old sessions use disk and memory.

1. Mark the Linear ticket Done.
2. Ask the leader to run its "Cleanup after merge" step.
3. Verify: no worktrees, Docker containers, dev servers or checker branches left, and the mission folders (`~/forus/<id>`, `~/foreman/<id>`) are deleted.
4. Exit the leader session and close its tab.
5. Start the next ticket if a slot is free.

## Safety

- Never paste API keys or tokens into a chat. The owner puts them into GitHub secrets or `.env`.
- Never push a leader past its own safety refusal. Do not do yourself an action a leader or subagent was denied.
- Only the owner can tell a leader to restart GLM with wider permissions.
- Do not touch PRs, tabs or sessions that are not from our missions.
- If Linear refuses to create a ticket (plan limit), do not work around it. Put an in-scope fix in the same PR as its own commit and record it in a Linear comment, or ask the owner.
