"""Product alert rules, alerts, events, deliveries and notification preferences, schema only (HOM-134, WP24-T02)

Revision ID: 1b1144f1d95b
Revises: fc9730c5f039
Create Date: 2026-11-14

Adds the five tables of product alerts on calls (F59, ADR 0085).  ``alert_rules``:
one workspace rule with one condition type (a failed QA criterion, a risk finding or
a missed commitment), a severity, recipient roles and users, a deduplication window,
an optional auto-escalation delay and a delivery mode.  ``alerts``: the grouped
occurrences of one rule and one dedupe group inside one window, with the open,
acknowledged, suppressed and escalated states.  ``alert_events``: one immutable
occurrence of a condition on one call, unique per rule, call and condition.
``alert_deliveries``: one message to one user on one channel for one purpose, with
its status and reason.  ``notification_preferences``: the digest, e-mail and mute
choices of one user.  Nothing writes the tables yet; the service, the routes, the
permissions and the audit events follow in PR 2.

* Why the keys: ``uq_alert_rules_scope`` and ``uq_alerts_scope`` are the targets of
  the composite foreign keys ``fk_alerts_rule``, ``fk_alert_events_alert``,
  ``fk_alert_events_rule`` and ``fk_alert_deliveries_alert``, so an alert, an event
  and a delivery stay in their rule's and alert's own workspace.
  ``uq_alert_events_condition`` makes the same condition on the same call one event,
  ever, so a rerun or a recompute never counts twice.  ``uq_alerts_window`` is the
  database backstop of the deduplication window: one alert per rule, dedupe group
  and window start.  ``uq_alert_rules_name`` keeps rule names unique per workspace.
  ``uq_alert_deliveries_target`` makes a delivery once per workspace, purpose,
  subject, recipient and channel, so the daily digest reaches a user once per day
  and channel.  ``uq_notification_preferences_user`` keeps one preference row per
  workspace and user.
* The frozen lists live in ``backend/database/saas_models.py``: the condition types,
  severities, delivery modes, statuses, suppress and escalation reasons, event
  sources, delivery purposes, channels, statuses and reasons.  The checks copy them
  as frozen strings, by constraint name.  They use no regex, so SQLite runs the same
  rules.  Each cross-field rule passes when a value that another check owns is
  outside its list, so a row that breaks one field fails only that field's own
  check.  No check names a user column: the user columns are SET NULL when a user
  is removed, and a check on them would make the removal fail.
* All five tables are workspace tables under row-level security (ADR 0005): tenant
  only, never through a share token.  Four of them get the full application grants.
  ``alert_events`` is in ``NO_UPDATE_TABLES``: SELECT, INSERT and DELETE, never
  UPDATE, because an event is a fact of the past; the conversation purge removes the
  rows as the application role, and an ORM guard refuses an update as well.

No data changes: no existing table's columns change and no row is written here.  The
conversation purge (ADR 0021) and the backup reconciliation are code lists, updated
with this migration: the purge deletes the conversation's events, then the alerts of
the workspace that have no event left with their deliveries, and
``backup/reconcile.py`` counts the events next to the action proposals.

Downgrade drops the five tables in reverse order (the preferences, the deliveries,
the events, the alerts and the rules); every rule, alert, event, delivery and
preference is lost.  The policies and the grants belong to the tables and go with
them.  No other table's grants or policies change in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "1b1144f1d95b"
down_revision: str | Sequence[str] | None = "fc9730c5f039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULES_TABLE = "alert_rules"
ALERTS = "alerts"
EVENTS = "alert_events"
DELIVERIES = "alert_deliveries"
PREFERENCES = "notification_preferences"

_KEY_RULE = (
    "length(criterion_id) BETWEEN 2 AND 64 "
    "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
    "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
)

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.
RULE_CHECKS = {
    "ck_alert_rules_name": "length(trim(name)) >= 1",
    "ck_alert_rules_condition_type": (
        "condition_type IN ('qa_criterion_failed','risk_finding','commitment_missed')"
    ),
    "ck_alert_rules_severity": "severity IN ('info','warning','critical')",
    "ck_alert_rules_delivery_mode": "delivery_mode IN ('immediate','digest_only')",
    "ck_alert_rules_criterion_key": f"criterion_id IS NULL OR ({_KEY_RULE})",
    "ck_alert_rules_condition_shape": (
        "condition_type NOT IN ('risk_finding','commitment_missed') OR criterion_id IS NULL"
    ),
    "ck_alert_rules_dedup_window": ("dedup_window_minutes >= 5 AND dedup_window_minutes <= 10080"),
    "ck_alert_rules_escalate_after": (
        "escalate_after_minutes IS NULL OR "
        "(escalate_after_minutes >= 5 AND escalate_after_minutes <= 10080)"
    ),
    "ck_alert_rules_revision": "revision >= 1",
}
ALERT_CHECKS = {
    "ck_alerts_condition_type": (
        "condition_type IN ('qa_criterion_failed','risk_finding','commitment_missed')"
    ),
    "ck_alerts_severity": "severity IN ('info','warning','critical')",
    "ck_alerts_status": "status IN ('open','acknowledged','suppressed','escalated')",
    "ck_alerts_suppress_reason": (
        "suppress_reason IS NULL OR suppress_reason IN "
        "('false_positive','known_issue','duplicate','other','inherited')"
    ),
    "ck_alerts_escalation_reason": (
        "escalation_reason IS NULL OR escalation_reason IN ('manual','unacknowledged')"
    ),
    "ck_alerts_condition_group": "length(condition_group) >= 1",
    "ck_alerts_dedupe_group": "length(dedupe_group) >= 1",
    "ck_alerts_event_count": "event_count >= 1",
    "ck_alerts_revision": "revision >= 1",
    "ck_alerts_escalation_pair": (
        "(escalated_at IS NULL AND escalation_reason IS NULL) "
        "OR (escalated_at IS NOT NULL AND escalation_reason IS NOT NULL)"
    ),
    "ck_alerts_suppression_fields": (
        "(suppressed_at IS NULL AND suppressed_until IS NULL AND suppress_reason IS NULL) "
        "OR (suppressed_at IS NOT NULL AND suppressed_until IS NOT NULL "
        "AND suppress_reason IS NOT NULL)"
    ),
    "ck_alerts_open_fields": (
        "status <> 'open' OR (acknowledged_at IS NULL AND suppressed_at IS NULL "
        "AND escalated_at IS NULL)"
    ),
    "ck_alerts_acknowledged_fields": (
        "status <> 'acknowledged' OR (acknowledged_at IS NOT NULL AND suppressed_at IS NULL)"
    ),
    "ck_alerts_suppressed_fields": (
        "status <> 'suppressed' OR (suppressed_at IS NOT NULL AND acknowledged_at IS NULL)"
    ),
    "ck_alerts_escalated_fields": (
        "status <> 'escalated' OR (escalated_at IS NOT NULL AND acknowledged_at IS NULL "
        "AND suppressed_at IS NULL)"
    ),
}
EVENT_CHECKS = {
    "ck_alert_events_source_type": "source_type IN ('review_result','analysis_run','action_item')",
    "ck_alert_events_condition_ref": "length(condition_ref) >= 1",
}
DELIVERY_CHECKS = {
    "ck_alert_deliveries_purpose": "purpose IN ('alert','escalation','digest')",
    "ck_alert_deliveries_channel": "channel IN ('in_app','email')",
    "ck_alert_deliveries_status": "status IN ('pending','sent','failed','skipped')",
    "ck_alert_deliveries_reason": (
        "reason IS NULL OR reason IN "
        "('not_authorized','muted','digest_disabled','email_missing','email_send_failed')"
    ),
    "ck_alert_deliveries_subject_ref": "length(subject_ref) >= 1",
    "ck_alert_deliveries_attempt_count": "attempt_count >= 0",
    "ck_alert_deliveries_purpose_shape": (
        "(purpose <> 'digest' OR alert_id IS NULL) "
        "AND (purpose NOT IN ('alert','escalation') OR alert_id IS NOT NULL)"
    ),
    "ck_alert_deliveries_sent_fields": (
        "status <> 'sent' OR (sent_at IS NOT NULL AND reason IS NULL)"
    ),
    "ck_alert_deliveries_skipped_fields": (
        "status <> 'skipped' OR (reason IS NOT NULL AND sent_at IS NULL)"
    ),
    "ck_alert_deliveries_failed_fields": (
        "status <> 'failed' OR (reason IS NOT NULL AND sent_at IS NULL)"
    ),
    "ck_alert_deliveries_pending_fields": "status <> 'pending' OR sent_at IS NULL",
}
RULE_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "created_by_user_id": ("users", "SET NULL"),
    "updated_by_user_id": ("users", "SET NULL"),
}
ALERT_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "subject_user_id": ("users", "SET NULL"),
    "acknowledged_by_user_id": ("users", "SET NULL"),
    "suppressed_by_user_id": ("users", "SET NULL"),
    "escalated_by_user_id": ("users", "SET NULL"),
}
EVENT_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
}
DELIVERY_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "recipient_user_id": ("users", "SET NULL"),
    "notification_id": ("notifications", "SET NULL"),
}
PREFERENCE_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "user_id": ("users", "CASCADE"),
}
RULE_INDEXES = (
    ("ix_alert_rules_workspace_id", ("workspace_id",)),
    ("ix_alert_rules_active", ("workspace_id", "enabled", "condition_type")),
    ("ix_alert_rules_created_by_user_id", ("created_by_user_id",)),
    ("ix_alert_rules_updated_by_user_id", ("updated_by_user_id",)),
)
ALERT_INDEXES = (
    ("ix_alerts_workspace_id", ("workspace_id",)),
    ("ix_alerts_queue", ("workspace_id", "status", "last_event_at", "id")),
    ("ix_alerts_rule_group", ("rule_id", "dedupe_group", "window_ends_at")),
    ("ix_alerts_subject_user_id", ("subject_user_id",)),
    ("ix_alerts_acknowledged_by_user_id", ("acknowledged_by_user_id",)),
    ("ix_alerts_suppressed_by_user_id", ("suppressed_by_user_id",)),
    ("ix_alerts_escalated_by_user_id", ("escalated_by_user_id",)),
)
EVENT_INDEXES = (
    ("ix_alert_events_workspace_id", ("workspace_id",)),
    ("ix_alert_events_alert", ("alert_id", "occurred_at")),
    ("ix_alert_events_workspace_meeting", ("workspace_id", "meeting_id")),
    ("ix_alert_events_meeting_id", ("meeting_id",)),
)
DELIVERY_INDEXES = (
    ("ix_alert_deliveries_workspace_id", ("workspace_id",)),
    ("ix_alert_deliveries_due", ("workspace_id", "status", "next_attempt_at")),
    ("ix_alert_deliveries_alert_id", ("alert_id",)),
    ("ix_alert_deliveries_recipient_user_id", ("recipient_user_id",)),
    ("ix_alert_deliveries_notification_id", ("notification_id",)),
)
PREFERENCE_INDEXES = (
    ("ix_notification_preferences_workspace_id", ("workspace_id",)),
    ("ix_notification_preferences_user_id", ("user_id",)),
)
RULE_SCOPE = ["id", "workspace_id"]
ALERT_SCOPE = ["id", "workspace_id"]
RULE_TARGET = [f"{RULES_TABLE}.id", f"{RULES_TABLE}.workspace_id"]
ALERT_TARGET = [f"{ALERTS}.id", f"{ALERTS}.workspace_id"]


def _foreign_keys(keys: dict[str, tuple[str, str]]) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
        for column, (target, action) in keys.items()
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        RULES_TABLE,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("condition_type", sa.String(length=30), nullable=False),
        sa.Column("criterion_id", sa.String(length=64), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("recipient_roles", sa.JSON(), nullable=False),
        sa.Column("recipient_user_ids", sa.JSON(), nullable=False),
        sa.Column("delivery_mode", sa.String(length=20), nullable=False),
        sa.Column("dedup_window_minutes", sa.Integer(), nullable=False),
        sa.Column("escalate_after_minutes", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_foreign_keys(RULE_FOREIGN_KEYS),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(*RULE_SCOPE, name="uq_alert_rules_scope"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_alert_rules_name"),
        *(sa.CheckConstraint(check, name=name) for name, check in RULE_CHECKS.items()),
    )
    with op.batch_alter_table(RULES_TABLE, schema=None) as batch_op:
        for name, columns in RULE_INDEXES:
            batch_op.create_index(name, columns, unique=False)

    op.create_table(
        ALERTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("condition_type", sa.String(length=30), nullable=False),
        sa.Column("condition_group", sa.String(length=80), nullable=False),
        sa.Column("dedupe_group", sa.String(length=160), nullable=False),
        sa.Column("subject_user_id", sa.String(length=36), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("suppressed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppressed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suppress_reason", sa.String(length=30), nullable=True),
        sa.Column("suppressed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_reason", sa.String(length=30), nullable=True),
        sa.Column("escalated_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_foreign_keys(ALERT_FOREIGN_KEYS),
        sa.ForeignKeyConstraint(
            ["rule_id", "workspace_id"],
            RULE_TARGET,
            ondelete="CASCADE",
            name="fk_alerts_rule",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(*ALERT_SCOPE, name="uq_alerts_scope"),
        sa.UniqueConstraint(
            "rule_id", "dedupe_group", "window_started_at", name="uq_alerts_window"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in ALERT_CHECKS.items()),
    )
    with op.batch_alter_table(ALERTS, schema=None) as batch_op:
        for name, columns in ALERT_INDEXES:
            batch_op.create_index(name, columns, unique=False)

    op.create_table(
        EVENTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("alert_id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("condition_ref", sa.String(length=80), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_foreign_keys(EVENT_FOREIGN_KEYS),
        sa.ForeignKeyConstraint(
            ["alert_id", "workspace_id"],
            ALERT_TARGET,
            ondelete="CASCADE",
            name="fk_alert_events_alert",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id", "workspace_id"],
            RULE_TARGET,
            ondelete="CASCADE",
            name="fk_alert_events_rule",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "rule_id", "meeting_id", "condition_ref", name="uq_alert_events_condition"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in EVENT_CHECKS.items()),
    )
    with op.batch_alter_table(EVENTS, schema=None) as batch_op:
        for name, columns in EVENT_INDEXES:
            batch_op.create_index(name, columns, unique=False)

    op.create_table(
        DELIVERIES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("alert_id", sa.String(length=36), nullable=True),
        sa.Column("purpose", sa.String(length=20), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("subject_ref", sa.String(length=80), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_foreign_keys(DELIVERY_FOREIGN_KEYS),
        sa.ForeignKeyConstraint(
            ["alert_id", "workspace_id"],
            ALERT_TARGET,
            ondelete="CASCADE",
            name="fk_alert_deliveries_alert",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "purpose",
            "subject_ref",
            "recipient_user_id",
            "channel",
            name="uq_alert_deliveries_target",
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in DELIVERY_CHECKS.items()),
    )
    with op.batch_alter_table(DELIVERIES, schema=None) as batch_op:
        for name, columns in DELIVERY_INDEXES:
            batch_op.create_index(name, columns, unique=False)

    op.create_table(
        PREFERENCES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("digest_enabled", sa.Boolean(), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False),
        sa.Column("muted_alert_rule_ids", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_foreign_keys(PREFERENCE_FOREIGN_KEYS),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_notification_preferences_user"),
    )
    with op.batch_alter_table(PREFERENCES, schema=None) as batch_op:
        for name, columns in PREFERENCE_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    # The tenant policies of the five new tables and their grants; the events lose
    # UPDATE (``NO_UPDATE_TABLES``).
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every rule, alert, event, delivery and preference is lost)."""
    # The children first: the preferences, the deliveries, the events and the alerts
    # name the rules.  The indexes, the policies and the grants belong to the tables
    # and go with them.
    op.drop_table(PREFERENCES)
    op.drop_table(DELIVERIES)
    op.drop_table(EVENTS)
    op.drop_table(ALERTS)
    op.drop_table(RULES_TABLE)
