---
name: scoped-pr
description: Use when taking on bug fixes, reported issues, or any change that could span multiple root causes, and again before creating a branch, commit, or pull request in Homayar. Keeps each PR to one root cause and drives the branch, commit, and draft PR flow on Kohandezh/Homayar.com.
---

# Scoped PR

One reviewable idea per PR: one **root cause**, not one file and not one ticket. Apply this before writing code (decide boundaries) and again before branching, committing, or opening the PR.

## Step 1: decide the boundary

List the distinct root causes. One root cause = one PR.

- **Split** when changes have different causes, fix different symptoms, or could be reverted independently.
- **Keep together** when several files share one cause, or splitting would leave a non-running state.
- **A contract change is one PR**: backend router + schema + migration + console caller + tests.
- **Surfaces**: the public site (root), product console (`Homayar/frontend`), and backend (`Homayar/backend`) are separate PRs unless one cause spans them.
- **Records vs code**: handover/records docs (`docs/handoff/…`) go in their own PR unless they document this exact change.

## Step 2: start clean from the latest main

```bash
git fetch origin
git switch -c <prefix>/<short-slug> origin/main
```

Prefixes used here: `feat/`, `fix/`, `ci/`, `research/`, `docs/`. In a Claude-app worktree, sync with the base through the app's sync tool rather than a manual merge. Never update the original checkout in place; work in a worktree or an isolated clone.

### Big features: stack, do not bundle

Split into slices that each run on their own (migration → service → router → console). Each slice is a PR based on the previous slice's branch. Put `[n/m]` in the PR title, not in commits.

## Step 3: fix it, test first

Write the failing test (see `api-test` for the backend), then the fix. Behaviour that only PostgreSQL guarantees needs a `postgres`-marked test.

## Step 4: check and commit

Use the `commit` skill: run the surface's gates, stage deliberately, write the message in this repo's style (no AI co-author trailer).

## Step 5: open a draft PR

```bash
git push -u origin HEAD
gh pr create --draft --base main --title "<Area>: <what changes>" --body-file <file>
```

### PR body

```
## Root cause
<one paragraph>

## Change
<what and why, per file group>

## Evidence
- Gates run locally: <commands and results>
- PostgreSQL / ffmpeg / Redis suites: <ran or not, and why>
- Manual check: <surface, fa/RTL, phone width> or "no visible change"

## Out of scope
<related issues deliberately left for another PR>
```

CI (`.github/workflows/ci.yml`) has jobs for the source contract, the public web, the product console, and the backend (PostgreSQL 17, Redis 7, ffmpeg). All must be green before leaving draft. Never enable auto-merge unless the owner asks.

## Step 6: next root cause

Back to Step 2 from a fresh `origin/main` branch. Never pile an independent fix on the previous fix's branch.

## Sizing

Soft budget about 400 changed lines of source (tests and generated migrations excluded). Past that, justify or split.
