# ADR 0038: Role-scoped trend views and small-group disclosure

Date: 2026-10-23 · Status: proposed (HOM-82, parent HOM-19, features F49 and F61) · Builds on: ADR 0004, ADR 0005, ADR 0012, ADR 0013, ADR 0015

Parts changed after the design review carry "(D1-fix)". Parts changed by the leader
amendments A1 to A5 (HOM-82 SPEC section 9) carry "(D3)".

## Context

HOM-79 defined what an issue trend counts and the sample rules: a cell under 30 conversations
is `insufficient_sample`, and the cell of one agent under 30 also hides its numerator,
`pending_review` and `absent_pending_review`. HOM-80 built `GET /api/v1/trends/issues` on the
caller's record scope. HOM-81 added the `coverage`, `scorecard` and `missing_data` blocks.

Three things were left to HOM-82:

- *The role views.* Executives and analysts of an organization, supervisors comparing teams,
  and an agent's view of their own calls.
- *L1, the kind follows the filter.* `GET /trends/issues` used the agent cell only when the
  query had exactly one `agent_user_id`. A second id that owns no meeting turned the hidden
  agent cell into a rate cell that shows everything.
- *L2, complementary disclosure.* A hidden count could be rebuilt by subtraction: a team total
  minus the other agents, or the workspace total minus a team that is all but one person.

Two facts shape the answer:

- An organization role never gives content access (ADR 0012). An organization caller may not
  see any agent's records, so an aggregate about one person is a disclosure.
- A workspace caller only counts meetings they may open (record scope, ADR 0004). For them the
  HOM-79 rule is about fairness: nobody is judged on too little data.

## Decision

1. **Four session-only read routes (D1-fix).**
   - `GET /api/v1/organizations/{organization_id}/trends/issues` (executive aggregates):
     `organization:analytics:read`, one block per scoped workspace, no organization total.
   - `GET /api/v1/organizations/{organization_id}/workspaces/{workspace_id}/trends/issues`
     (analyst drill-down): `organization:records:read`, so not executives. The grant unit and
     one block per active team in it. A workspace outside the scope is `404
     workspace_not_found`. No list of conversations (HOM-83).
   - `GET /api/v1/trends/teams` (supervisor team comparison): `meeting:read`; owners and
     admins compare every active team, members and viewers only the teams where they hold an
     effective supervisor assignment (`403 team_scope_denied` otherwise). At most 10 teams.
   - `GET /api/v1/trends/me` (agent self-view): `meeting:read`; the meetings whose
     `agent_user_id` is the caller. Uploads do not count.
   Every API key gets one answer on all four, `401 api_key_not_accepted`. (D3) On the two
   workspace routes a dependency in `routers/v1/trends.py` depends on `require_actor`, so every
   bearer that looks like an API key (valid, unknown, revoked, expired, over its rate limit) is
   refused before any key lookup, the workspace and the permission. It then resolves the
   workspace with `require_workspace` and checks `meeting:read`, and carries the matrix marker
   `+session`. `security/context.py` does not change.
2. **One loader, judged once (D1-fix).** The trend population statement also reads the team,
   agent, language and source of each conversation. Each request judges every conversation
   once, splits cohorts in Python, and keeps per row only what the decisions need about the
   agents in the row's denominator (D3): a yes or no where that is enough, and distinct agents
   capped at 5 on organization views. The statement count does not grow with the number of
   teams. Organization
   views bind each workspace as the tenant before they read it (ADR 0005), and they always
   load the whole workspace, also for a team grant (decision 7).
3. **A small group** is a cohort whose included conversations have at least 1 and fewer than
   `MIN_COHORT_AGENTS = 5` distinct agents (`Meeting.agent_user_id`). On workspace views the
   caller is not counted, because they know their own calls. The constant is new and named; it
   is not part of `trend-sample-policy/1`. The rules have their own version,
   `trend-disclosure/1`, shown in every answer.
4. **Workspace views: the kind comes from the data (closes L1).** A cohort is kind `agent`
   when it is the caller's own calls or a small group, else `rate`. The same kind holds for
   every row and point. The filter no longer decides it.
5. **Workspace views: all or nothing.** In an `agent` cohort seen by someone other than its
   only agent, if one cell of the window rows is hidden, every other window cell is withheld;
   the same for the cells of all points. When the caller is one of the agents, the cohort
   without the caller's calls is tested too. Every row of the definition is tested, whatever
   the selectors (turn B1: so the released-team set of decision 6 does not depend on the
   selectors either). A withheld cell has status `withheld`, reason
   `complementary_disclosure`, and keeps its denominator and missing counts. HOM-79 cells and
   counts do not change, so `issue-trends/1` stays.
6. **Workspace views: one canonical released-team set (closes L2) (D1-fix).** For each
   caller, window and set of non-team filters, one set of released teams is computed over
   every team that has a conversation in the caller's scope, including inactive teams and
   teams the caller may not compare. If the scope minus the released teams minus the caller's
   own calls is a small group with a cell under 30 that holds an agent, the teams with the
   fewest conversations are withheld until it is not. `/trends/teams` shows a team only when
   it is in that set; `/trends/issues?team_id=` is released only when every requested team is
   in it. The decision does not depend on which teams a request names, so subtracting released
   answers always leaves the same safe remainder plus whole released teams.
   `GET /trends/issues` accepts only the caller's own `agent_user_id` (`403
   agent_scope_denied` otherwise), so the cohorts of other agents never exist.
7. **Organization views: one decision per workspace and week (D1-fix).** The window is whole
   closed weeks (Saturday 00:00, Asia/Tehran), at most 52, grain `none` or `week`, with a
   4-week default filled in for a missing end; no timezone, team, agent, language or source
   parameter.
   - *The week test.* A cohort passes a week when its included conversations, and the
     denominator of every row of the definition (whatever the selectors), have 0 or at least 5
     distinct agents.
   - *The weekly decision (D3).* For each workspace and week, whoever asks, over every team id
     that holds an included conversation of the workspace in that week, active or not: the
     passing teams form `Rel_k`; if the workspace minus `Rel_k` fails the week test, `Rel_k` is
     empty. Team activity is not an input, so activating or deactivating a team never changes
     the decision for a closed week.
   - *The blocks.* A whole-workspace block is released when it passes every week. A team block
     (active teams only) is released when the team is in `Rel_k` in every week where it has
     data. A team-grant block (a team grant keeps only active teams) is released when, in every
     week, each granted team with data is in `Rel_k`. An inactive team in `Rel_k` is never shown.
     A block that is not released carries no count at all.
   Every released window value is then a sum of weeks that were each released, and in each
   week only a passing remainder plus released teams can be built. This also holds across a
   grant change and between two people with different grants.
   - *Data outside the grant (D3).* For a team grant, whether a block is released depends on
     data outside the grant. It reveals one yes or no **per week** about the rest of the
     workspace, and no number from outside. This is safer than a decision on the grant alone:
     then a team grant and a whole-workspace grant would be tested apart, and the whole
     workspace minus the granted team could be one person. One yes or no per week about the
     rest of the workspace is less than the counts of a person.
   - *A caller who also holds a restricted workspace membership (turn B3, SPEC A6).* One
     person can hold an organization grant on W and a membership in W (ADR 0012). When that
     membership is effective and restricted (not owner or admin, and `record_scope=team`), the
     person knows every conversation of its scope S (supervised teams, own agent meetings, own
     uploads), so `W_k − S_k` could be one agent. On both organization views every block of W
     (the workspace block and each team block) must then also pass the week test on the block
     minus S, in every week; otherwise it is withheld (`complementary_disclosure`). No
     membership, or an unrestricted one, is decided as before: an unrestricted member can read
     every record of W already. The extra cost is a fixed 1 to 3 statements per workspace (the
     membership; for a restricted one its assignments and the meetings of S).
   - *A second release set for that caller (turn B4, SPEC A6b).* The per-block test alone is
     not enough: W minus two released team blocks minus S can still be one agent. So for that
     caller the weekly decision is also made over the conversations outside S
     (`Rel_out_k`), and a team is released only when it is in `Rel_k` and in `Rel_out_k`.
     Both remainders (of W, and of W outside S) then have 0 or at least 5 agents. The set does
     not read the grant, so a change of grant gives the same decision. No new statement.
8. **Comparisons without a ranking.** Every pair of compared teams, per window row, through
   `compare_cells` (50 per side), only between teams of the canonical set. A small or
   withheld team is never compared, on any row: the status order is a side of kind `agent`
   (`withheld`, `small_group`), then a withheld side (`withheld`,
   `complementary_disclosure`), then the `qa` row (`not_computable`), then `compare_cells`
   (turn B2). No rank field; teams in a fixed order by name, then id;
   the difference is signed by that order. Every comparison answer carries the notices
   `correlation_not_cause` and `unadjusted_comparison` and, per team, the mix of languages and
   sources.
9. **Open question 6, and two reused codes (D1-fix).** A population filter narrows (`200`):
   `GET /trends/issues` and `GET /trends/me` with a `team_id` outside the scope. A named
   cohort outside the scope is refused: `403 team_scope_denied` on the comparison, decided
   from the caller's assignments only, so an unknown id gets the same answer.
   `team_scope_denied` and `agent_scope_denied` already meant a refused attribution write; they
   now also mean a refused read scope on the trend views. The new messages do not say
   "attribution".
10. **Coverage per cohort, and one exception (D1-fix).** Every released cohort block carries
    the HOM-81 blocks over its own population. Per-team coverage comes from one grouped run,
    `compute_coverage_by_team` in `coverage_service.py` (9 statements, the same as one
    `compute_coverage`, whose result does not change). A withheld organization block has
    `coverage: null` and `missing_data: null`. This is a deliberate exception to "every
    aggregate carries coverage": the coverage counters of a small group (how many of its calls
    failed, were deleted, were evaluated) also describe that group.
11. **A breaking change for API clients (D1-fix).** An API key with `meeting:read` may call
    `GET /trends/issues` today. A client that filters by another agent's `agent_user_id` gets
    `403 agent_scope_denied` after this change. The route is not in the ingestion contract
    snapshot. The contract moves to 1.3.0 for the four new operations (additive).

## Product defaults (proposed) (D1-fix)

The ticket is silent on these. Each is the safest choice found; the owner may change them.

- 5 distinct agents as the small-group minimum.
- The uploader of a meeting is not treated as its agent.
- Managers do not get the trend of one other agent.
- Small workspaces lose numerators. The owner of a pilot workspace with 3 agents now gets kind
  `agent` on `GET /trends/issues`; if any row is under 30 conversations, every numerator of the
  window is withheld. Before HOM-82 that owner saw rate cells with numerators.
- A restricted supervisor's uploads of an agent in a team they do not supervise are in their
  scope. They can make the remainder a small group and withhold a supervised team, maybe the
  whole comparison.
- Organization owners and admins are withheld like analysts and executives. They can install
  themselves as workspace `admin` (matrix 4a, ADR 0012), so this default binds them only on
  the organization routes.
- An organization caller who also holds a restricted membership in the workspace gets a block
  only when the block minus that membership's scope also passes the week test (decision 7).
- Organization views use whole closed weeks only, never days, months, or the current week.
- On organization views every row is tested, so a cohort is withheld when one issue rests on 1
  to 4 agents in some week.
- For a team grant, the release decision reads the whole workspace (decision 7), including
  teams that are no longer active (D3).
- The drill-down goes one level only: workspace to team.
- Members and viewers compare only teams they supervise, whatever the record scope.
- An owner or admin of a workspace with no active team gets an empty comparison (`200`, no
  team), not a 403: they may compare every active team, and there is none (turn B2).
- The four routes refuse every API key.
- No organization total and no all-teams total.

## Why no migration

Everything is derived at read time from the tables HOM-80 and HOM-81 already read. The
disclosure decision depends on the conversations of the answer, so nothing is stored.

## Not built

- A per-agent view for managers. It needs the remainder rule along the agent dimension.
- A dominance rule (one agent with almost all calls of a cohort).
- Language and source filters on the organization views.
- Case-mix adjustment of comparisons. The answer shows the mix instead.

## Risks (D1-fix)

- **Differencing on workspace views.** The canonical set holds for one window and one set of
  non-team filters. Two answers with different windows, grains, languages or sources can still
  isolate a few calls. The caller may open those records anyway, so no secret is learned, but
  the HOM-79 fairness rule can be bypassed with effort.
- **Inside a rate cohort on workspace views.** The kind is decided per cohort over the window,
  and rate cells show numerators under 30. A day point where only one agent worked, or a row
  whose denominator holds one agent's calls, shows that agent's numerator under 30. Fairness
  only: the caller may open these records.
- **Over-withholding.** Small teams, holiday weeks and workspaces with few agents lose detail,
  most of all on organization views. The row test withholds a team-week in about 7% of weeks
  for a team of exactly 5 or 6 active agents where one agent takes 1 to 3 calls, below 1% for
  teams of 8 or more, and every week when missing data follows a source used by fewer than 5
  agents. These are estimates from the binomial chance; no production data exists yet.
- **A changing scope on workspace views (D3).** A restricted caller's scope changes over time:
  an assignment ends, a team becomes inactive. The canonical released set holds only within
  one scope, so two answers before and after such a change come from two different sets.
- **Processing counts of a released organization block.** Its per-row `missing` buckets and
  `population.excluded` are processing counts that the week test does not check, like its
  coverage counters.
- **Closed weeks still change.** A transcription that finishes late changes an old week; two
  answers at two times then differ by a few calls of a cohort that passed the week test.
- **Moved or deleted attribution on organization views (D3).** A meeting moved to another team
  or another agent, or a deleted team (its meetings become no-team), changes the cohorts of
  closed weeks. Two answers before and after can then differ by a single conversation.
- **One yes or no per week leaves the grant (D3).** For a team grant, the release of a week
  tells whether the rest of the workspace passed the week test (decision 7).
- **Cost.** `GET /trends/issues` with `team_id` now judges the caller's whole scope, and a team
  grant on the organization views judges the whole workspace. The statement counts do not
  change. HOM-84 measures the p95.
