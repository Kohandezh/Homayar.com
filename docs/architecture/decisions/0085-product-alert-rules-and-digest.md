# ADR 0085: Product alert rules and the alert digest

Date: 2026-11-14 · Status: Proposed (HOM-134, WP24-T02, F59) · Builds on: ADR 0057, ADR 0062, ADR 0065, ADR 0082

All code paths are under `Homayar/backend/`. HOM-134 lands in two PRs. PR 1 adds the
five tables of product alerts (migration `1b1144f1d95b`), the row-level security
lists, the conversation purge step and the backup reconciliation. PR 2 adds the
service, the evaluation hooks, the sweep, the routes, the permissions and the audit
events. This ADR records the decisions of PR 1 and PR 2, so the schema serves the
rules that come after it.

## Context

F59 (parent HOM-27) asks for this: "Repeated same-condition events create one alert
per configured deduplication window; only authorized recipients notified;
suppressed/acknowledged/escalated states and failed delivery visible." WP24 step 2
says: "Build product alert rules on calls with deduplication, suppression and a daily
digest."

Homayar already judges calls against scorecards in reviews (ADR 0057), extracts
action items, and turns findings into coaching tasks (ADR 0062). Three conditions
are visible today and nothing reacts to them:

1. A failed QA criterion: the live source is the human review result
   (`reviewer_outcomes` of `review_results`), not machine scoring.
2. A risk finding: an insight of the meeting analysis with category `risk`.
3. A missed commitment: an `action_items` row of kind `commitment`, past due and
   still open or in progress.

Supervisors learn about these by looking at screens. Four risks shape the design:

1. The same failure on the same call must not notify anybody twice, no matter how
   often a review or an analysis runs again.
2. A person who cannot see the call must not be told about it.
3. A rule that floods a workspace will be muted and then ignored.
4. A delivery that failed silently is worse than one that failed loudly.

## Definitions

* A **rule** is one row of `alert_rules`: a workspace, a name unique in it, one
  condition type (`qa_criterion_failed`, `risk_finding`, `commitment_missed`), an
  optional criterion id (only on a QA rule; NULL means any criterion), a severity
  (`info`, `warning`, `critical`), recipient roles plus explicit recipient users, a
  `dedup_window_minutes` between 5 and 10080, an optional `escalate_after_minutes`,
  a delivery mode (`immediate` or `digest_only`) and an `enabled` flag.
* An **event** is one row of `alert_events`: one occurrence of a rule's condition on
  one call, with its source (`review_result`, `analysis_run`, `action_item`), the
  source id and the condition ref (the criterion id, `risk`, or the action item id).
* An **alert** is one row of `alerts`: the events of one rule and one dedupe group
  inside one window. The dedupe group is `<agent user id or '-'>|<condition group>`,
  where the condition group is the criterion id, `risk` or `commitment`.
* A **delivery** is one row of `alert_deliveries`: one message to one user on one
  channel (`in_app`, `email`) for one purpose (`alert`, `escalation`, `digest`).
* A **preference** is one row of `notification_preferences`: the digest, e-mail and
  mute choices of one user of one workspace.

## Decision

### D1. A rule is workspace data, not conversation content

A rule belongs to its workspace and survives every conversation purge. Its name is
unique in the workspace. `recipient_roles` and `recipient_user_ids` are JSON lists;
PR 2 validates the roles against `ALERT_RECIPIENT_ROLES` (`owner`, `admin`,
`supervisor`), so no column check names them. `revision` starts at 1 and grows by
one with each change, so a PATCH is a compare-and-set on it with
`expected_revision`, like `coaching_tasks`. A rule is never deleted through the API;
it is disabled with `enabled: false`, so an alert can always name the rule that made
it.

### D2. An event is a fact of the past, written once

The same condition on the same call is one event, ever:
`uq_alert_events_condition` on (`rule_id`, `meeting_id`, `condition_ref`). A rerun
of a review or a recompute of an analysis finds the row already there and counts
nothing new. The table is immutable: it is in `NO_UPDATE_TABLES` (the application
role gets SELECT, INSERT and DELETE, never UPDATE), and an ORM guard raises
`AlertEventImmutableError` on any update, like the delivery request guard of ADR
0082. `source_id` and `condition_ref` are plain strings, not keys: the event
outlives its source. The conversation purge deletes the row and the backup
reconciliation counts it.

### D3. An alert groups events of one dedupe group inside one window

The window opens at the first event and lasts the rule's `dedup_window_minutes`. A
later event of the same rule and dedupe group inside the window joins the open
alert: `event_count` grows by one, `last_event_at` moves, nobody is notified. An
event at or after `window_ends_at` opens a new alert. `uq_alerts_window` on
(`rule_id`, `dedupe_group`, `window_started_at`) is the database backstop, so a race
fails instead of double counting. PR 2 runs the grouping under a row lock on the
rule (`SELECT ... FOR UPDATE`): one worker at a time opens or joins the alert of a
group.

### D4. Four states, two of them final

An alert is `open`, `acknowledged`, `suppressed` or `escalated`. Allowed moves:
open to acknowledged, suppressed or escalated; escalated to acknowledged or
suppressed. Acknowledged and suppressed are final. A suppression carries an end
time (`suppressed_until`); a new alert of the same rule and group that opens before
that time starts as `suppressed` with reason `inherited` and notifies nobody. An
acknowledged or suppressed alert may still carry its escalation fields: it was
escalated first. No check names a user column: the four user columns are SET NULL
when a user is removed, and `escalated_by_user_id` NULL with `escalated_at` set
means the escalation was automatic.

### D5. Every message and every skip is a row

A delivery names its purpose, channel, subject ref (the alert id, or
`digest:YYYY-MM-DD` for a digest) and recipient. The rule's delivery mode decides
when it notifies: `immediate` rules notify at open; `digest_only` rules notify only
through the digest. Statuses: `pending`, `sent`, `failed`, `skipped`, each with the
fields it requires. In-app deliveries go through `services/notification_service.py`
(kinds `alert.opened`, `alert.escalated`, `alert.digest`; fixed English texts;
`data` holds ids only, never meeting text) and are `sent` or `skipped` at once, with
`notification_id` naming the in-app row. An `immediate` alert also writes a
`pending` e-mail delivery at open for every recipient whose `email_enabled` is
true; when the user has no address the row is `skipped` with `email_missing`.
E-mail deliveries start `pending`; the sweep claims one by a compare-and-set on
`attempt_count`, sends with `send_email_sync`, retries after 5 and 30 minutes, and
after the third failure marks the row `failed` with reason `email_send_failed`. A
user who got nothing gets
a `skipped` row with the reason: `not_authorized`, `muted`, `digest_disabled` or
`email_missing`. This is how failed delivery and per-user suppression become
visible. `uq_alert_deliveries_target` makes a delivery once per workspace, purpose,
subject, recipient and channel, so the digest reaches a user once per day and
channel.

### D6. One preference row per user, defaults without a row

`notification_preferences` holds `digest_enabled` (default true), `email_enabled`
(default false) and `muted_alert_rule_ids`, the JSON list of rule ids the user mutes
for themselves. No row means the defaults. A muted rule gives the user a `skipped`
delivery with reason `muted`. The digest ignores muted rules per user. N07 (quiet
hours, timezones) extends this table later.

### D7. Only authorized recipients, the agent never a recipient

The recipients of an alert are the union of the workspace owners, the admins, the
supervisors of the call's team and the rule's `recipient_user_ids`; at most 50, in
that order, by user id inside a group. The call's own agent is never a recipient.
At delivery time a recipient must still be an active member with the `alert:read`
permission and record access to the call (`resolve_record_access`); otherwise the
row is `skipped` with `not_authorized`. Routes live behind
`require_module("quality")`, session only:
API keys are refused. `GET/POST /api/v1/alert-rules` and `GET/PATCH
/api/v1/alert-rules/{id}` need `alert_rule:manage` (owner, admin); `alert:read` and
`alert:triage` go to owner, admin and member, and no API key holds any of these
permissions. `GET /api/v1/alerts`, `GET /api/v1/alerts/{id}`, its events and its
deliveries, and `POST /api/v1/alerts/{id}/acknowledge|suppress|escalate` (with
`expected_revision`) need `alert:read` and, for the moves, `alert:triage`; a user
never triages an alert whose subject is themselves (403), and sees an alert only
when one of its event calls is in the user's record scope. `GET
/api/v1/alerts/{id}` shows only events of calls in the caller's record scope, plus
a delivery summary. `GET /api/v1/alert-deliveries` is for admins, can filter by
status, and includes digests. `GET/PUT /api/v1/notification-preferences` reads and
writes the caller's own row. Audit events (`alert_rule.created`,
`alert_rule.updated`, `alert.acknowledged`, `alert.suppressed`, `alert.escalated`,
`notification_preferences.updated`) record ids and codes only.

### D8. Three sources, three hooks, all best effort

QA: when `complete_item` stores a human result, every criterion whose reviewer
outcome is `failed` is an event for each enabled `qa_criterion_failed` rule whose
`criterion_id` is NULL or equal. It runs in a savepoint inside the same transaction,
best effort: a failure is logged and never blocks the review. Risk: after the
analysis of a meeting commits (the place of the voicemail hook in `process_job`,
and after a recompute), in its own tenant-bound session, best effort; at most one
event per rule and meeting (`condition_ref = 'risk'`). Commitment: the sweep finds
commitments past due and still `open` or `in_progress`; one event per action item
(`condition_ref` is the action item id). Machine scoring is not a source in v1
because `record_result` is not called in production.

### D9. Escalation and the daily digest

Escalation is manual (a person, `escalation_reason = 'manual'`) or automatic: the
sweep escalates an `open` alert older than the rule's `escalate_after_minutes` when
nobody acknowledged it (reason `unacknowledged`, actor NULL). Both notify the
workspace owners and admins, not the call's agent, and ignore mutes. The digest
runs once a day at `alert_digest_hour_utc` (setting, default 5): each user with the
digest on gets one in-app digest (and an e-mail when `email_enabled`) of the active
alerts whose last event is in the 24 hours before the run and for which the user is
an authorized recipient, leaving out muted rules. Zero alerts means no digest row.
`digest_enabled` false still records one `skipped` row with `digest_disabled`, so
the silence is explainable.

### D10. Isolation, purge and backup

All five tables are workspace tables under row-level security (ADR 0005), tenant
only, never through a share token. `alert_events` is in `NO_UPDATE_TABLES`; the
other four have the full grants. The conversation purge deletes the conversation's
events first, then the alerts of the workspace that have no event left, their
deliveries before them, and counts every deleted row; an alert that still has an
event of another conversation stays, and rules, digest deliveries and preferences
are not conversation content and stay. A legal hold keeps them like the other
content rows. The backup reconciliation counts `alert_events` next to the action
proposals (Amendment 1 of the SPEC: only tables with a `meeting_id` are content
tables).

## Consequences

* The schema pins the PR 2 contract: the dedupe window, the per-condition
  uniqueness, the delivery reasons and the mute list all live in constraints and
  lists that the migration freezes, so the service cannot drift from them.
* A rerun or a recompute never notifies twice: the event unique key refuses it.
* Silence is always explainable: every user who got nothing has a row that says
  why.
* The digest and the e-mail retry land in PR 2 behind one Celery beat task
  (`product_alerts`, interval setting `product_alert_sweep_interval_seconds`,
  default 300): missed commitments, auto-escalation, pending e-mails and the digest
  when due, per workspace, capped at 500 items, time from `database_now`.
* PR 1 writes no rows: no route, no service, no permission, no audit type, no
  Celery task touches the tables yet.

## Alternatives

* A notification counter per meeting instead of event rows: cheaper, but it cannot
  tell which condition fired, so it cannot dedupe per condition or survive a
  recompute.
* Deduping in the service only, without `uq_alerts_window` and
  `uq_alert_events_condition`: simpler schema, but two racing workers could double
  notify; the unique keys make the race fail instead.
* A `recipients` JSON on the alert (who was notified): one row per alert instead of
  per user, but then a skip, a failure and a retry could not be recorded per user.
* Soft-deleting rules: keeps history, but a disabled rule already keeps it, and the
  console never needs a deleted rule back.
* Machine scoring as a fourth source: refused for v1 because it is not called in
  production; the event source list leaves room for it.

## Not built in HOM-134

Machine scoring as a source, quiet hours and timezones (N07), webhook or chat
channels, console screens, a rule delete route. PR 2 (a later SPEC) builds the
service, the hooks, the sweep, the routes, the permissions and the audit events.
