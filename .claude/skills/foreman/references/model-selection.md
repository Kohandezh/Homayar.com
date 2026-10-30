# Model guidance

This file explains why each role gets the model and effort it gets. The mapping itself is
the table in [agents-and-models.md](agents-and-models.md). This file does not choose the
harness: the implementer runs GLM-5.3 in OpenCode (owner order 2026-10-30), and every
other role runs Claude Code on the `claudepersonal` configuration.

## The implementer is always GLM

Owner order, 2026-10-30: every implementer runs GLM-5.3 in OpenCode, for every kind of
work, including security, infra, CI, auth, secrets, concurrency and database migrations.
The reason is the Claude weekly limit: the implementer writes most of a mission's tokens.

To keep quality, the checker gets stricter, not the maker weaker:

- the checker is always Claude `opus`;
- when the work unit touches authentication, authorization, session or token handling,
  secrets, concurrency, a database migration, or a frozen-file hash, the reviewer runs at
  `xhigh`;
- escalation is the only way an implementer moves to Claude: two `CHANGES_REQUESTED` on
  the same work unit, or a stall with no handoff after two nudges.

## The rule

Four kinds of work, four models. This choice is for the other making roles and for an
implementer after escalation. Every new implementer is GLM-5.3 in OpenCode:

| Kind of work | Model    | Example in a mission                                  |
| ------------ | -------- | ----------------------------------------------------- |
| Judgment     | `opus`   | review, root cause, architecture, the recommendation  |
| Volume       | `sonnet` | a specified patch, a trace, a test on a known pattern |
| Search       | `haiku`  | scout fan-out: find every caller, return paths        |
| Visual       | `fable`  | animation, transition timing, layout, theme           |

Ask one question about a work unit: **is the decision harder than the output?** If yes,
`opus`. If the decision is already made in `SPEC.md` and only needs typing, `sonnet`. If
nothing needs deciding and something needs finding, `haiku`. If the acceptance criteria
are about how it looks, `fable`.

Effort is the second dial, not a substitute for the first. `opus` at `low` is both more
expensive and worse than `sonnet` at `high`. Set the model from the kind of work, then set
the effort from the blast radius.

## Why checking outranks making

A checker produces almost no output and makes nothing but decisions. That is the cheapest
place in the mission to buy the most thinking, so every checking role runs `opus`, and a
checker is never downgraded to save cost. A making role can sometimes drop to `sonnet`
because `SPEC.md` has already absorbed the judgment. A checking role cannot: its whole job
is the judgment that `SPEC.md` could not contain.

This is also why `scout` is the only `haiku` role. A scout returns `file:line` paths for
someone else to judge. It never returns a verdict, and no acceptance decision rests on it.

## Why the escalation ladder stops where it does

`xhigh` is for a work unit whose failure is expensive and quiet: authentication,
authorization, session and token handling, concurrency, and migrations. Those defects pass
tests and surface in production.

`max` is for exactly one case: a second checker did not settle a disagreement and a final
reviewer is the last gate. Running `max` everywhere removes the signal that a specific
decision needed more than the default, and Foreman's escalation record depends on that
signal being real.

## Retired benchmarks

Three benchmarks on this repository previously selected DeepSeek V4.1 Flash for
implementation and GLM-5.3 on the Z.AI Coding Plan for review, on a Pi or OpenCode
harness. Those results are retired. The old numbers are kept out of this file rather than
reused, because a benchmark that measured different models on a different harness is not
evidence about this one. The GLM implementer is an owner cost decision from 2026-10-30,
not a benchmark result.

## Not measured

The table in [agents-and-models.md](agents-and-models.md) is a reasoned default, not a
measured result. No benchmark on this repository has yet compared GLM-5.3, Opus, Sonnet,
Haiku, and Fable role by role. Say so when the mapping is questioned. Do not describe it
as measured.

To make it measured, the instrument the retired benchmarks used still applies, and only the
lanes change:

1. Pick one defect with held-out behaviour tests that are red on the base.
2. Write the oracle before any model sees the task.
3. Run one lane per candidate model, each in its own worktree, from an identical brief.
4. Score against the oracle, not against a reading of the diff.

Two comparisons are worth the cost first, because both are places this mapping guesses:

- GLM-5.3 in OpenCode against Claude `opus` at `high` on a fully specified implementation
  unit. GLM is the default implementer by owner order, so this is the pair that needs
  proof.
- `opus` at `high` against `opus` at `xhigh` on a security review with a known-true
  vulnerability. This sets where the escalation line belongs.

Record the result here and change the table in the same commit.
