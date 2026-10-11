# linear-agent

A worker that does small Linear tickets on its own (HOM-197, phase 1).

It picks one ticket labeled `agent` from Todo. It runs the Claude Agent SDK in a fresh git worktree,
runs a review pass and the checks, and opens a **draft** PR. Then it moves the ticket to In Review.
A human reviews and merges every PR. The worker never merges and never pushes to `main`.

## How a ticket flows

1. **Pick.** The ticket is in team HOM, in state Todo, and has the label `agent` (exact name).
   It does not have `agent-failed`. Every ticket that blocks it is done or canceled.
   The highest priority goes first (urgent, high, medium, low, then no priority). Then the oldest.
2. **Claim.** The worker takes a lock file for the ticket, so two runs never take the same ticket.
   Then it reads the ticket again, moves it to In Progress, and comments `Picked up by linear-agent at <time>`.
3. **Worktree.** It creates `~/linear-agent-worktrees/HOM-<n>` on a new branch `agent/hom-<n>-<slug>`
   from `origin/main`.
4. **Maker.** The Agent SDK works on the ticket. It gets the title, description, labels and comments.
5. **Review.** A second, read-only run reviews the diff. If it asks for changes, the maker gets one fix round.
6. **Checks.** The worker runs the checks for each changed surface:
   `Homayar/frontend` (npm ci, tsc, lint, tests, build), `Homayar/backend` (the related pytest files),
   `website/` (install, lint, build). Other folders have no checks.
7. **PR.** It commits, pushes the `agent/...` branch and runs `gh pr create --draft`. It comments the PR link
   on the ticket and attaches it.
8. **Status.** It moves the ticket to In Review. If the team has no In Review state, the ticket stays
   in In Progress and gets a `PR ready` comment.
9. **Follow-ups.** Each poll checks the PRs the worker opened.
   Merged: the ticket goes to Done, and the worktree and the branch are removed.
   Closed without a merge: the worker comments, moves the ticket to Todo and removes the `agent` label.

If a step fails, the worker comments what failed and the last error lines. It moves the ticket back to Todo,
removes `agent` and adds `agent-failed`, so it does not loop. The worktree is kept for debugging.

Each step writes one log line with the ticket id and the cost so far.

## Setup

You need Node.js 22.12 or newer, `git`, and the GitHub CLI `gh`. For backend and website checks you also
need `uv` and `pnpm`.

The checks run inside a sandbox (`@anthropic-ai/sandbox-runtime`). It needs:

- macOS: `ripgrep` (`brew install ripgrep`). The sandbox itself is the built-in `sandbox-exec`.
- Linux: `bubblewrap`, `socat` and `ripgrep`. On Ubuntu 24.04 and later, unprivileged user namespaces
  must be allowed (see the sandbox-runtime README).

If the sandbox cannot start, no check runs and the ticket fails with that reason.

```sh
cd tools/linear-agent
npm ci
cp .env.example .env   # then fill in the three keys
```

### Keys

All keys live only in `tools/linear-agent/.env`. That file is git-ignored.

- `ANTHROPIC_API_KEY`: create a separate Console workspace, for example `linear-agent`.
  Give it a **monthly spend limit**, then create the API key in that workspace. The limit is a hard stop
  for all tickets together. Use the API key only. The worker never uses the claude.ai login.
- `LINEAR_API_KEY`: a personal API key from Linear (Settings > Security & access).
- `GITHUB_TOKEN`: a fine-grained token for `Kohandezh/Homayar.com` only, with
  Contents read and write, and Pull requests read and write.

### Spend limits

There are two limits. Keep both.

- The Console workspace limit caps the whole month.
- `LINEAR_AGENT_MAX_BUDGET_USD` (default 5) caps one ticket. The maker, the reviewer and the fix round
  share it. When a ticket reaches it, the ticket fails with the reason `cost cap`.
  `LINEAR_AGENT_MAX_TURNS` (default 80) caps the turns of each run.

The other settings are listed in `.env.example`.

## Use it

Label a ticket `agent` and keep it in Todo. Small tickets with clear acceptance criteria work best.

```sh
npm run dry-run   # read only: lists the tickets it would pick and what it would do for open PRs
npm run once      # one poll, then exit
npm run daemon    # poll every LINEAR_AGENT_POLL_MINUTES (default 10)
```

A dry run needs only `LINEAR_API_KEY`. It changes nothing in Linear, git or GitHub.

To retry a failed ticket, fix the cause, remove `agent-failed` and add `agent` again.
The worker removes the old worktree and the old local branch of that ticket before it starts again.
It also deletes the old remote branch (`git push origin --delete`, no force) when no tracked PR uses it.
A folder at the ticket path that is not a git worktree is never deleted: the ticket fails, and you
move or delete that folder by hand.

### Stop it

- `npm run daemon` stops after the current ticket when it gets SIGTERM or SIGINT:
  `kill -TERM <pid>`. A second signal stops it at once.
- In a terminal, Ctrl+C also reaches the agent process, because it shares the terminal. The current
  ticket then fails and gets `agent-failed`. Use `kill -TERM` to stop gently. (Git, gh and the check
  commands run in their own process group, so Ctrl+C does not reach them.)
- Under launchd: `launchctl bootout gui/$(id -u)/com.homayar.linear-agent`.
- To stop all work at once without the machine: remove the `agent` label from the tickets,
  or turn the Console workspace off.

### Run it with launchd (macOS)

Copy `launchd/com.homayar.linear-agent.plist.example` to `~/Library/LaunchAgents/com.homayar.linear-agent.plist`,
change the paths inside it, and follow the steps in the comment at the top of the file.

## Safety rules

- The worker never merges, never pushes to `main` and never uses `git push --force`.
  It only pushes `agent/...` branches. Local cleanup with `git worktree remove --force` and `git branch -D`
  only runs on the ticket's own folder under the worktree root and its own `agent/...` branch.
- It never changes a ticket that does not have the `agent` label at pick time.
- The agent runs with a fixed tool list (`dontAsk` permission mode). Its Bash commands run in the SDK
  sandbox. Network access from the sandbox is limited to npm, PyPI, GitHub and Linear hosts.
  WebFetch and WebSearch are off. `git push` and `gh` are blocked for the agent.
- File tools (Read, Write, Edit) only work inside the ticket worktree.
- The agent does not get `LINEAR_API_KEY`, `GITHUB_TOKEN` or the SSH agent. Git credential helpers are
  switched off for it, so it cannot push. Its Bash commands cannot read this `.env` file or the usual
  credential folders (`~/.ssh`, `~/.config/gh`, `~/.git-credentials` and others).
- Project settings, hooks and MCP config come from the main checkout, not from the worktree the agent
  edits. MCP servers are off. The Anthropic key is unset for the agent's sandboxed Bash commands
  (the agent process itself keeps it to call the API). `api.github.com` is not reachable from any sandbox.
- The checks run code the agent wrote (installs, tests, builds), so each check runs in its own sandbox:
  network only to the same npm, PyPI, GitHub and Linear hosts; no reads of this `.env` or the credential
  folders; writes only to the ticket worktree, the temp folders and the npm, pnpm, uv and pip caches.
  The check env never holds a key. A timeout stops the whole process group of a check.
- The worker's own git calls run with repo hooks and fsmonitor switched off
  (`-c core.hooksPath=/dev/null -c core.fsmonitor=false`, `--no-verify`, `--no-ext-diff`). Before git
  runs in a worktree, the worker checks that the worktree `.git` file still points into the main
  repo's `.git/worktrees/` and back. Only fetch and push get GitHub auth; no git call gets the
  Linear or Anthropic key.

### Unsafe opt-out: `LINEAR_AGENT_UNSANDBOXED_CHECKS=1`

This runs the checks **without a sandbox**, on your machine, with your user's rights and open network.
Code the agent wrote can then read your files and send them anywhere. Use it only on a throwaway machine
when the sandbox cannot be installed. The worker logs an `UNSAFE` warning at start and on every check run.

## Before the first live run (human steps)

1. In Linear, add an **In Review** state to team Homayar (Settings > Teams > Homayar > Workflow),
   after In Progress. Create the labels `agent` and `agent-failed`.
2. Create the Console workspace `linear-agent` with a monthly spend limit, and its API key.
3. Create the Linear API key and the GitHub fine-grained token. Put all three keys in `.env`.
4. Run `npm run dry-run` and check the list it prints.
5. Smoke test: label one tiny ticket `agent`, run `npm run once`, and check that the ticket moves
   Todo, In Progress, In Review and that a draft PR exists.

## Development

```sh
npm run typecheck
npm run lint
npm test
```

The tests use fakes for Linear, GitHub and the SDK. They never call a real API.
The git adapter tests use a throwaway repository in the system temp folder.
