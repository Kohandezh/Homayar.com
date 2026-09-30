---
name: foreman
description: Use when delivering one or more scoped tasks through Herdr-managed agent teams under a leader agent, such as Linear tickets, bug fixes, security fixes, root-cause debugging, code tracing, or research that ends in a spike or a spec. Use when several tickets arrive at once, or when an incorrect result is expensive. Every agent is Claude Code started on the claudepersonal configuration; never start Pi, OpenCode, or an OpenRouter-backed session. Not for a lone change that one session can finish and verify alone.
---

# Foreman

## Purpose

Foreman is a leader. It takes one or more tasks, builds the teams they need, and owns
every outcome. It writes each contract before any worker acts, resolves ambiguity,
verifies claimed results, and accepts or rejects the work. It never edits a deliverable.

Every Foreman run uses Herdr for worktrees, sidebar workspaces, tabs, panes, agent
sessions, messaging, and cleanup. Load and follow the `$herdr` skill before issuing any
Herdr control command.

## Vocabulary

| Term           | Meaning                                                                           |
| -------------- | --------------------------------------------------------------------------------- |
| Mission        | One Foreman run. It may hold several tasks.                                       |
| Task           | One Linear ticket or one user request.                                            |
| Work unit      | One root cause, one question, or one document. Never two.                         |
| Team           | One maker and one checker, paired on one work unit.                               |
| Maker          | Produces the deliverable: a patch, a diagnosis, a trace, or a document.           |
| Checker        | Judges the maker's work independently. Never edits it.                            |
| Expansion role | A session added beyond the pair: scout, second checker, final reviewer, and more. |

A team is the unit Foreman sizes, starts, and accepts. A task needs one team or more. A
mission runs as many teams as its tasks need, within the limits in
[references/team-sizing.md](references/team-sizing.md).

## Task types

Classify every task before sizing it. Load the reference for every type present in the
mission.

| Type           | Signal                                     | Maker        | Checker  | Checker's blind first artifact | Deliverable                     | Reference                                         |
| -------------- | ------------------------------------------ | ------------ | -------- | ------------------------------ | ------------------------------- | ------------------------------------------------- |
| Implementation | fix, feature, security fix, refactor       | implementer  | reviewer | oracle test                    | draft PR                        | [implementation.md](references/implementation.md) |
| Debugging      | symptom known, cause unknown               | investigator | verifier | prediction record              | diagnosis and a failing test    | [debugging.md](references/debugging.md)           |
| Tracing        | "how does X flow", "where is Y decided"    | tracer       | verifier | coverage checklist             | trace map with `file:line` hops | [debugging.md](references/debugging.md)           |
| Research       | "which X", "should we", a spike, or a spec | researcher   | critic   | question checklist             | spike or spec in a draft PR     | [research.md](references/research.md)             |

The checker writes its first artifact from the task alone, before it sees any of the
maker's work. That ordering is the independence. A checker that reads the maker's output
first is a second opinion, not a check.

A task that mixes types splits into one work unit per type. A bug with an unknown cause
becomes a debugging unit, then an implementation unit in the next wave.

## Preconditions

Verify the current agent is inside Herdr:

```bash
test "${HERDR_ENV:-}" = 1
```

If this fails, stop. Do not create worktrees or start agents outside Herdr.

Verify the Claude integration is installed in the personal config directory:

```bash
herdr integration status | grep '^claude:'
```

It must read `current` and its path must be under `.claude-personal`. Without it, a worker
never reports its state and every `herdr agent wait` hangs until it times out. The fix is
in [references/agents-and-models.md](references/agents-and-models.md). If it cannot be
made current, stop. Never fall back to another agent kind.

Learn the installed command surface before using it:

```bash
herdr --help
herdr worktree
herdr workspace
herdr tab
herdr pane
herdr agent
herdr agent start --help
herdr integration status
```

The installed CLI is authoritative. Parse IDs from its JSON responses. Do not predict
workspace, tab, or pane IDs. Where a command in this skill disagrees with the installed
CLI, follow the CLI and fix this skill in the same change.

## Leader pane

Name the leader pane `foreman` before briefing anyone, and again after any Herdr server
restart (the name is lost): `herdr agent rename "$HERDR_PANE_ID" foreman`. After a restart,
also rerun `herdr integration status`, and reinstall the Claude integration if it reads
`outdated`.

Foreman's pane can be anywhere in the current Herdr session. Foreman coordinates through
stable agent names, so its location never constrains the topology. Keep the leader pane
alive for the whole mission.

Never change the user's current view. Every worktree, workspace, tab, and pane Foreman
creates uses `--no-focus`. Never run a focus command. The user switches views.

## Mission flow

1. **Intake.** Start every new task from the latest `main`: in the repository checkout, run
   `git checkout main` and then `git pull`. Why: a stale local `main` gives workers an old base,
   and an owner merge since the last pull is missed. If `git status --short` shows changes to
   tracked files, stop and ask the owner before switching; never stash or discard them.
   Untracked files do not block the switch. Then resolve the base ref. For Linear tickets, read each
   ticket: description, priority, labels, and blocking relations. Reading is free. Any
   Linear write needs the user's explicit authority for that action.
2. **Classify.** Give each task a type. Split mixed tasks into work units.
3. **Size.** Choose teams, waves, and expansion roles per
   [references/team-sizing.md](references/team-sizing.md). Tell the user the plan in a
   few lines: tasks, types, teams, waves, and agent kinds.
4. **Contract.** Create the mission directory. Write one `SPEC.md` per team before any
   worker acts.
5. **Build.** Create worktrees, tabs, and panes per
   [references/herdr-topology.md](references/herdr-topology.md). Start sessions per
   [references/agents-and-models.md](references/agents-and-models.md).
6. **Run.** Drive each team through its type reference. Run independent teams at the same
   time. Run dependent teams in waves.
7. **Expand.** Add a role only when a trigger in
   [references/expansion.md](references/expansion.md) fires.
8. **Accept.** Accept each work unit on its evidence. Run the gates. Open draft PRs.
9. **Report.** Give the user one short result per task.
10. **Clean up** after merge.

## Contract

`SPEC.md` is the authoritative contract for one team. No worker acts until it is
complete. It must contain:

- the task, the work unit, and enough context to work without oral history
- observable acceptance criteria
- deny-cases and their closest allow-controls, when behavior rejects or restricts input
- file ownership: allowed and forbidden file changes, per role
- allowed and forbidden actions, such as no migrations, service resets, or Linear writes
- exact verification commands and their working directories
- known baseline test counts
- the communication map: every agent name, the leader target, authorized contacts, and
  the envelope
- the turn protocol for the task type: what each turn produces and what gates the next
- the team directory path and the artifact each role writes there
- the additions the type reference names

## Communication protocol

Every role communicates through Herdr with `herdr agent prompt <target> "<text>"`, which
sends and submits in one call. Teach the protocol to every worker in
its first prompt. Give every worker:

- its stable agent name, unique among live agents, matching `[a-z][a-z0-9_-]{0,31}`
- the leader target: Foreman's agent name
- its only authorized contact: the leader. Makers and checkers never message each other.
  Foreman carries every handoff, so a checker receives only what its type reference allows.
- this message envelope:

```text
MISSION: <mission-id>
TEAM: <team-id>
TURN: <number>
FROM: <agent>
TO: <agent>
TYPE: ACK | STATUS | BLOCKED | EXPAND_REQUEST | REVIEW_NEEDED | CHANGES_REQUESTED | ACCEPTED | FAILED
STATE: ready | working | blocked | awaiting_review | changes_requested | accepted | failed
SUMMARY: <concise factual update>
ARTIFACTS: <absolute paths, commands, diffs, or none>
NEXT: <requested action or none>
RISKS: <unresolved risks or none>
```

A worker that finds the contract incomplete, ambiguous, or contradictory never guesses.
It sends `BLOCKED` to Foreman and waits for a revised contract.

A worker that needs help beyond its pair sends `EXPAND_REQUEST` to Foreman, naming the
trigger it hit. Only Foreman starts, stops, or adds sessions.

Teams never message other teams. A fact one team needs from another goes through
Foreman, and Foreman checks it against the files before passing it on.

## Mission directory

Create one durable directory per mission at `~/foreman/<mission-id>/`. Never place it
under `/tmp` and never inside a repository. Create only the state the mission needs:

```text
~/foreman/<mission-id>/
├── ledger.md              Foreman: every task, type, team, agent, pane, worktree, wave, state
├── MISSION-SUMMARY.md     Foreman: the result and evidence per task
├── heavy.lock             lock file for heavy test suites, see team-sizing.md
└── <team-id>/
    ├── SPEC.md            Foreman: the contract
    ├── briefs/            Foreman: every prompt block sent, archived
    ├── handoffs/          maker: turn-N.md
    ├── reviews/           checker: turn-N.md
    └── oracle/ | predictions/ | questions/   checker: the blind first artifact
```

Agents read these files by absolute path. Coordination artifacts live outside every
worktree, so a diff contains only real work. One temporary exception: `REVIEW-INPUT.diff`
is placed in a clean checker worktree and removed before worktree removal.

## Acceptance

A worker's `ACCEPTED` is a request for Foreman's verification, not completion. Foreman
accepts a work unit only with the evidence its type requires:

| Type           | Required evidence                                                                                         |
| -------------- | --------------------------------------------------------------------------------------------------------- |
| Implementation | oracle cases red on the base and green on the patch, a reviewer report, and passing gates                 |
| Debugging      | a failing test the verifier ran on the base, failing for the reported reason, and a resolved causal chain |
| Tracing        | every hop resolved by the verifier against the base, and the coverage checklist closed                    |
| Research       | every checklist question answered or listed as open, and every load-bearing claim confirmed by the critic |

Foreman also checks, before a PR:

- every changed file is inside the ownership `SPEC.md` gives. A file outside it goes back to
  the team or becomes a new work unit.
- the change is still one root cause. Two root causes split into two work units.
- any expansion trigger that fired has its role's report.

## Gates and PR

For a code change, run and retain real output for:

- the suites named in `SPEC.md`, compared with their baselines
- the type check of every workspace the diff touches
- the changed-files lint gate over every changed file
- any repository-specific required gates

Open a draft PR with `gh pr create --draft --base main` once the checker has accepted and
the gates pass. Get the user's approval of the exact content and destination before any of
these: a ready-for-review PR, flipping a draft to ready, a PR or Linear comment, or any
other Linear write. Ask again if the content or destination changes.

**No stacked PRs (owner rule, 2026-09-26).** Every PR targets `main`. When one work unit
depends on another, Foreman opens the second PR only after the first is merged: it rebases the
second branch on the new `origin/main`, re-runs the gates, and then opens the PR against `main`.
The team may build both commits at once; only the PR waits.

Why: a PR whose base is another PR's branch merges into that branch, not into `main`. GitHub
retargets it only when the parent branch is deleted on merge. It went wrong three times even with
the merge order written down: on 2026-09-24 four PRs of a stack of seven never reached `main`, and
on 2026-09-25 and 2026-09-26 #33 and #41 landed in their parent branches and needed the re-land
PRs #34 and #42.

After every owner merge, check that each commit reached `main`
(`git merge-base --is-ancestor <sha> origin/main`) before cleanup, and report anything stranded
with the exact PR that would land it.

## Escalation

When the two-round cap in a type reference is hit, first check the triggers in
[references/expansion.md](references/expansion.md). If no trigger applies, or the
expansion role does not settle it, stop that team. Record `escalated` in the ledger, keep
its panes and worktrees, and give the user both positions with their artifact paths.
Other teams keep running. Do not decide the specification is wrong merely because the
work is unresolved.

## Cleanup after merge

Clean up one team at a time, after its PR is verified merged. A team with no PR, such as a
diagnosis-only or tracing task, is cleaned up after the user has its report.

Before removal:

1. Read every agent state in the team with `herdr agent get` and preserve any required
   final output.
2. Check every team worktree with `git status --short`.
3. Confirm the required evidence is preserved in the team directory: the contract, the
   blind first artifact, the final review, and the summary entry.
4. Remove only known Foreman-generated files: the temporary `REVIEW-INPUT.diff`, removed
   before any worktree removal. Never delete unrelated files. If any other uncommitted or
   untracked file remains, stop and ask the user.
5. Ask each agent to exit its normal session. Verify with `herdr agent get`. Use
   `herdr agent send-keys <agent-name> ctrl+c` only if a session does not exit normally.
6. Close only sessions, panes, tabs, and workspaces this mission created. Never close
   anything else.

Remove clean checker worktrees first, then the maker worktree:

```bash
herdr worktree remove --workspace <checker-workspace-id> --trust-repository
herdr worktree remove --workspace <maker-workspace-id> --trust-repository
```

Do not use `--force` unless the user explicitly approves the exact dirty worktree and the
data that will be discarded. Never run `git worktree prune` from the development
container.

Delete the mission directory only after every team is cleaned up and the user has the
summary.

## Verification discipline

Keep these controls on every task type:

1. The checker's blind first artifact is written before it sees the maker's work.
2. Every deny-case has an allow-control.
3. A denial must come from the intended decision, not a collision or an earlier validation
   failure.
4. Every claimed command result is reproduced or marked unverified.
5. A prose risk is counted in the review census.
6. A checker that times out or produces no report has failed its phase.
7. A `file:line` citation is resolved against the file, not trusted from the report.

## Outputs

| Artifact               | Writer                       | Location                                   |
| ---------------------- | ---------------------------- | ------------------------------------------ |
| `ledger.md`            | Foreman                      | `~/foreman/<mission-id>/`                  |
| `MISSION-SUMMARY.md`   | Foreman                      | `~/foreman/<mission-id>/`                  |
| `SPEC.md`, `briefs/`   | Foreman                      | `~/foreman/<mission-id>/<team-id>/`        |
| `handoffs/turn-N.md`   | maker                        | `~/foreman/<mission-id>/<team-id>/`        |
| blind first artifact   | checker, archived by Foreman | `~/foreman/<mission-id>/<team-id>/`        |
| `reviews/turn-N.md`    | checker                      | `~/foreman/<mission-id>/<team-id>/`        |
| expansion role reports | expansion role               | `~/foreman/<mission-id>/<team-id>/<role>/` |

`MISSION-SUMMARY.md` has one entry per task. An implementation entry names every oracle
case that was red on the base and green on the patch. A debugging entry names the failing
test and the command the verifier ran. Without that evidence, the team produced opinions
rather than acceptance proof.

## Fan-out verification runs

Foreman sometimes needs a wide fan-out that is not a team: an intake that checks a pasted
document or a proposal against the repository before a contract is written, or a swarm of
skeptics that tries to refute each finding. These runs are scripts, not sessions.

They run on Claude models, through the Workflow tool or Agent subagents. Because they share
the owner's Claude limit with every Herdr session, they are the first thing to bound.

**Why these rules exist.** Unbounded fan-out runs, where readers return dozens of findings and
multiple skeptics re-read entire files from scratch, quickly spawn hundreds of agents, burn
through token quotas, and trigger usage-limit pauses that drop in-flight work.

**Hard ceilings, written into every fan-out script:**

- **Prioritize findings instead of dropping them.** Readers return all valid findings ranked
  by severity rather than being sliced to a fixed cap.
- **One skeptic per finding.** A second skeptic runs only when the first says `REFUTED` or
  `UNVERIFIABLE`. Never two by default.
- **At most 40 agents per intake**, counted in the script. Findings are verified in priority
  order up to this cap; any remaining findings are returned unverified and labelled, never
  silently dropped.
- **Models do not re-read files to check a quote.** The reader returns `file:line`; the script
  extracts those lines itself and hands the text to the skeptic. The citation check is a small
  prompt on `haiku`. The reasoning check runs on `opus`; readers and the critic run on `sonnet`.
- **Set `effort` per stage:** `low` for extraction and citation checks, and the session default
  only for the reasoning skeptic and the critic.

**Before launching:**

- Skip the intake when Foreman can check the facts by hand in a few reads. Intake is for a
  pasted document or a proposal Foreman cannot verify itself, not for every code change.
- Write the token budget for the mission in `ledger.md` before the first fan-out, and record
  the workflow's `totalTokens` when it returns. A second fan-out in one mission needs a reason
  in the ledger.
- Check the remaining quota (`/usage` in the CLI) and never launch a fan-out within an hour of
  a limit reset. A run that pauses on the limit loses its in-flight agents.

Herdr teams (maker plus checker) stay the unit for every deliverable. The fan-out only feeds
the contract, and the contract is written from confirmed findings plus Foreman's own checks.
