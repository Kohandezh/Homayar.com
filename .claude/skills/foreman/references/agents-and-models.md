# Agents and models

Read this before starting any session.

## Two harnesses

| Harness | Who runs on it | Paid by |
| --- | --- | --- |
| Claude Code on the `claudepersonal` config | the leader, every checker, every expansion role, every other making role, and an implementer after escalation | the owner's Claude subscription limit |
| OpenCode with `zai-coding-plan/glm-5.3` | every implementer | the owner's Z.AI Coding Plan (flat) |

Why two: the implementer writes most of a mission's tokens, and the Claude weekly limit
ran out mid-mission before. Moving the implementer to GLM keeps the Claude limit for the
judgment roles. Pi and OpenRouter are never started by this skill.

### The implementer is always GLM

Owner order, 2026-10-30: every implementer starts on GLM-5.3, for every kind of work,
including security, infra, CI, auth, secrets, concurrency and database migrations. Do not
pick Claude for the implementer at the start. The reason is the Claude weekly limit: the
implementer writes most of a mission's tokens.

To keep quality, the checker gets stricter, not the maker weaker:

- the checker is always Claude `opus`;
- when the work unit touches authentication, authorization, session or token handling,
  secrets, concurrency, a database migration, or a frozen-file hash, the reviewer runs at
  `xhigh`;
- escalation is the only way an implementer moves to Claude: two `CHANGES_REQUESTED` on
  the same work unit, or a stall with no handoff after two nudges. Restart the maker on
  Claude `opus`, from the same worktree, with the same brief plus the review reports.
  Record the switch in `ledger.md`.

### Claude Code on the personal config

`claudepersonal` is a shell alias:

```text
claudepersonal = CLAUDE_CONFIG_DIR=~/.claude-personal claude
```

An alias only exists in an interactive shell. Herdr spawns the program directly, so the
alias never resolves. Pass the variable instead, with an absolute path:

```bash
--env CLAUDE_CONFIG_DIR="$HOME/.claude-personal"
```

That is not a style preference. Herdr's Claude integration hook is installed inside the
personal config directory:

```text
~/.claude-personal/hooks/herdr-agent-state.sh
~/.claude-personal/settings.json   -> hooks.SessionStart runs it
```

A Claude session started without `CLAUDE_CONFIG_DIR` reads `~/.claude`, never fires that
hook, and never reports its state. `herdr agent wait --until idle` then blocks until it
times out, and Foreman cannot drive a turn. Missing env is a silent hang, not an error.

Check the integration before starting any agent:

```bash
herdr integration status | grep '^claude:'
```

It must read `current` and the path must be under `.claude-personal`. If it does not,
install it from a shell that already has the variable set:

```bash
CLAUDE_CONFIG_DIR="$HOME/.claude-personal" herdr integration install claude
```

If the integration cannot be made current, stop and report it. Do not fall back to
another agent kind.

## Starting a session

The installed CLI is authoritative. On herdr 0.9.1 `herdr agent start` needs an existing pane
at an interactive shell prompt, and it cannot set environment variables. So a session starts
in two calls: `herdr pane split` makes the pane with the working directory and
`CLAUDE_CONFIG_DIR`, then `herdr agent start` runs the agent in it. Every implementer
starts in OpenCode instead, per "Starting a GLM implementer (OpenCode)" below.

```text
herdr pane split <pane> --direction right|down --cwd <path> --env KEY=VALUE --no-focus
herdr agent start <name> --kind claude --pane <pane-id> [--timeout MS] -- <claude args>
```

Claude example, a checker started below its team's root pane:

```bash
herdr pane split <team-root-pane> --direction down --cwd <checker-path> \
  --env CLAUDE_CONFIG_DIR="$HOME/.claude-personal" --no-focus      # -> .result.pane.pane_id
herdr agent start <checker-name> --kind claude --pane <checker-pane> -- --model opus --effort high
```

A Claude making role, such as an investigator, researcher or integrator, starts with the
same two calls and its model from the table below.

`--kind claude` names the executable, so the arguments after `--` start with the first flag.
Re-verify with `herdr agent start --help` and `herdr pane split --help` before a mission. If
they differ from the block above, follow the installed CLI and fix this file in the same change.

These are normal interactive sessions. Never use `claude -p`, `claude --print`, or any
other one-shot runner to force a model or a level.

### Starting a GLM implementer (OpenCode)

A GLM pane needs no `CLAUDE_CONFIG_DIR`. Its state hook is the OpenCode plugin at
`~/.config/opencode/plugins/herdr-agent-state.js`, installed by
`herdr integration install opencode` (check `herdr integration status | grep '^opencode:'`
reads `current`).

```bash
# -> .result.pane.pane_id
herdr pane split <team-root-pane> --direction right --cwd <implementer-path> --no-focus
herdr agent start <implementer-name> --kind opencode --pane <implementer-pane> --timeout 90000 \
  -- --model zai-coding-plan/glm-5.3
```

The session reports `idle`, then `working`, then `done`, and `herdr agent prompt` delivers
briefs the same way as for Claude. OpenCode reads the worktree's `CLAUDE.md` and the
project skills in `.claude/skills/`, so the repository's rules reach the GLM implementer
too. The status line must read `GLM-5.3 Z.AI Coding Plan`. If it shows another model or
provider, stop the session and start it again with the `--model` flag above.

**Permissions.** The owner's global OpenCode config has `"permission": "allow"`, so a GLM
session never asks. If a GLM implementer shows an `Allow once / Allow always / Reject`
prompt, restart it once and resend its brief. Do not click through prompts one by one.

No prompts does not mean more freedom. The brief still limits a GLM implementer to its
own worktree, and it never pushes, opens PRs, comments on issues or tickets, or merges.
Foreman does those steps itself.

To end a GLM session, send `ctrl+c` twice (`herdr agent send-keys <name> ctrl+c`,
twice). OpenCode has no `/exit` command.

## Model and effort by role

Claude aliases: `opus`, `sonnet`, `haiku`, `fable`. Effort levels: `low`, `medium`,
`high`, `xhigh`, `max`.

The rule behind the table: **judgment goes to Opus, volume goes to Sonnet, search goes to
Haiku, visual work goes to Fable.** Where the decision matters more than the output,
spend. Where the decision is already made and only needs typing, do not.

| Role         | Group    | Model    | Effort   | Why                                                       |
| ------------ | -------- | -------- | -------- | --------------------------------------------------------- |
| implementer  | making   | GLM-5.3 in OpenCode | default | every work unit, every kind of work; owner order 2026-10-30 |
| investigator | making   | `opus`   | `high`   | root cause, not symptom                                   |
| tracer       | making   | `sonnet` | `medium` | multi-hop reading against a fixed question                |
| researcher   | making   | `opus`   | `high`   | synthesis and the recommendation                          |
| integrator   | making   | `sonnet` | `medium` | applies decisions already accepted                        |
| reviewer     | checking | `opus`   | `high`   | review is pure judgment                                   |
| verifier     | checking | `opus`   | `high`   | must reproduce, not agree                                 |
| critic       | checking | `opus`   | `high`   | must find the unasked question                            |
| second checker | expansion | `opus` | `xhigh`  | added only because the first pass disagreed                |
| final reviewer | expansion | `opus` | `xhigh`  | last gate before a PR                                      |
| scout        | expansion | `haiku` | `low`    | wide fan-out search; returns paths, not verdicts           |

The implementer row is fixed: GLM-5.3 in OpenCode for every work unit (owner order
2026-10-30). The Claude models in "Choosing the implementer model" apply to an implementer
only after escalation. Every checking and expansion row stays on Claude, always.

Raise the reviewer, and a Claude implementer after escalation, to `xhigh` when the work
unit touches authentication, authorization, session or token handling, secrets,
concurrency, a database migration, or a frozen-file hash. Those are the cases where a
cheap wrong answer is most expensive.

Reserve `max` for one situation: an escalated disagreement that a second checker did not
settle, run by a final reviewer. Running everything at `max` buys nothing and hides which
decision actually needed it.

Foreman itself runs `opus` at `high`. It holds the plan, the ledger, and acceptance.

### Choosing the implementer model

The implementer is GLM-5.3, for every work unit (owner order 2026-10-30). Write `SPEC.md`
so a GLM maker can follow it: name the files, the behaviour, the acceptance criteria and
the pattern to copy. For hard or risky work, put more detail in the brief instead of
picking another model.

Use the Claude choice below only after escalation (two `CHANGES_REQUESTED` on the same
work unit, or a stall after two nudges). On Claude, `opus` is the default. Drop to
`sonnet` only when all of these are true:

- `SPEC.md` names the exact files and the exact change
- the repository already contains the pattern to copy
- no security boundary, no schema change, no concurrency
- the acceptance criteria are mechanical

If any one of them is false, use `opus`. When in doubt, use `opus`. A rejected patch costs
more than the difference in model.

Use `fable` when the acceptance criteria are about how something looks or moves:
animation, transition timing, layout, or theme work. Keep `opus` or `sonnet` for the logic
behind that surface, and split the work unit if a task has both.

## Confirm after start

`herdr agent start` returning `idle` proves nothing about the model or the level. Read the
session and confirm both before sending any brief:

```bash
herdr agent get <name>
herdr agent read <name> --source recent-unwrapped --lines 40
```

Confirm three things, in this order:

1. the session reports state to Herdr at all. If `herdr agent get` never leaves `unknown`,
   `CLAUDE_CONFIG_DIR` did not reach the process (Claude), or the OpenCode plugin is not
   current (GLM). Stop and fix that, not the prompt.
2. the model line names the model this table assigns to the role. For a GLM implementer
   the status line reads `GLM-5.3 Z.AI Coding Plan`.
3. the effort line names the assigned level.

A session running the wrong model or the wrong level is restarted, not corrected mid-turn.
Record the harness, the model and the effort of every agent in `ledger.md` at start time,
so an escalation can name what produced each position.

## Delivering a brief

Write the prompt block to `briefs/` first, then send the pointer. On herdr 0.9.1
`herdr agent prompt` sends the text and submits it in one call (`agent send` is gone):

```bash
herdr agent prompt <agent-name> "Read <absolute-brief-path>, acknowledge with the envelope, then do only that assignment."
```

`--wait` waits for the next settled state and may time out while the agent works; the prompt
was still delivered. Never assume a brief landed: read the pane after about 15 seconds. A
Claude Code dialog, "Teach auto mode about your environment?", appears after a session's first
turn and swallows the next prompt (a worker that shows `done` with no new handoff is the tell).
Cancel it with `herdr agent send-keys <agent-name> esc`, never accept it (it offers to scan the
owner's shell history and other repositories), then send the prompt again.

To end a Claude session: `herdr agent prompt <agent-name> "/exit"`. A GLM session has no
`/exit`; send `ctrl+c` twice (see "Starting a GLM implementer (OpenCode)").
