"""HOM-134 PR 1: product alert rules, alerts, events, deliveries and preferences.

Five tables, schema only.  ``alert_rules``: one workspace rule with one condition
type, an optional criterion, a severity, recipients, a deduplication window and a
delivery mode.  ``alerts``: the grouped occurrences of one rule and one dedupe group
inside one window, with the open, acknowledged, suppressed and escalated states.
``alert_events``: one immutable occurrence of a rule's condition on one call, unique
per rule, call and condition.  ``alert_deliveries``: one message to one user on one
channel for one purpose, with its status and reason.  ``notification_preferences``:
one row per workspace and user for the digest, e-mail and mutes.  Nothing writes the
tables yet; PR 2 adds the service, the routes, the permissions and the audit events.
The tests check:

* the ORM declares the columns, the named constraints, the keys and the indexes of
  the design, the frozen lists and the check strings, which use no regex and never
  name a user column, and no column name ends in ``_key`` or holds ``token`` or
  ``secret``;
* ``database/rls.py`` lists all five tables as workspace tables; ``alert_events`` is
  in ``NO_UPDATE_TABLES``, the other four have the full grants;
* every deny case of the SPEC is refused by its named constraint, and the closest
  allow case passes (SQLite and PostgreSQL);
* the ORM refuses any update of an event, and allows its delete;
* cascades: a workspace, a rule, an alert or a call takes its rows with it; a
  removed user clears the user columns and removes the preference row; a removed
  notification clears ``notification_id``;
* on SQLite, the migration builds the same tables as the ORM, and its downgrade
  removes them and nothing else;
* on PostgreSQL: the ORM schema and the migrated schema are the same; downgrade
  removes the five tables and leaves every other table as it was; tenant isolation
  (also on write); a share-token context sees none of it; the application role may
  not UPDATE an event;
* the conversation purge deletes and counts the events, then the orphaned alerts
  with their deliveries, and keeps rules, digest deliveries and preferences; a legal
  hold keeps them; the backup reconciliation counts the events;
* the code has one migration head, this one; ADR 0085 records the design.
"""

from __future__ import annotations

import importlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, delete, event, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.backup.reconcile import CONTENT_TABLES as RECONCILED_TABLES
from backend.database.model_registry import Base
from backend.database.relational import get_session_factory
from backend.database.rls import (
    APP_ROLE,
    APPEND_ONLY_TABLES,
    NO_DELETE_TABLES,
    NO_UPDATE_TABLES,
    POLICIES,
    POLICY_NAME,
    SHARED_CONTENT_TABLES,
    TENANT,
    UNPROTECTED_TABLES,
    WORKSPACE_TABLES,
)
from backend.database.saas_models import (
    ALERT_ACTIVE_STATUSES,
    ALERT_CONDITION_TYPES,
    ALERT_DELIVERY_CHANNELS,
    ALERT_DELIVERY_MODES,
    ALERT_DELIVERY_PURPOSES,
    ALERT_DELIVERY_REASONS,
    ALERT_DELIVERY_STATUSES,
    ALERT_ESCALATION_REASONS,
    ALERT_EVENT_SOURCES,
    ALERT_RECIPIENT_ROLES,
    ALERT_SEVERITIES,
    ALERT_STATUSES,
    ALERT_SUPPRESS_REASONS,
    Alert,
    AlertDelivery,
    AlertEvent,
    AlertEventImmutableError,
    AlertRule,
    Meeting,
    Notification,
    NotificationPreference,
    PurgeState,
    ShareAudience,
    ShareLink,
    User,
    Workspace,
)
from backend.database.tenant_context import bind_capability_context, bind_tenant_context
from backend.services.conversation_purge_service import ERROR_LEGAL_HOLD, _remove_content
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_ingestion_ledger import _headers, _patch_media
from backend.tests.test_ingestion_ledger import _tenant as _ledger_tenant
from backend.tests.test_retention_purge import _media, _processed, _purge, _review, _sweep
from backend.tests.test_scorecard_results_migration import _add, _delete_conversation

NEW_REVISION = "1b1144f1d95b"
BASE_REVISION = "fc9730c5f039"  # HOM-135: action proposals
HEAD_REVISION = NEW_REVISION  # this PR holds the migration slot
RULES_TABLE = "alert_rules"
ALERTS = "alerts"
EVENTS = "alert_events"
DELIVERIES = "alert_deliveries"
PREFERENCES = "notification_preferences"
TABLES = (RULES_TABLE, ALERTS, EVENTS, DELIVERIES, PREFERENCES)
NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
CRITERION = "greeting"

_KEY_RULE = (
    "length({c}) BETWEEN 2 AND 64 "
    "AND trim(substr({c}, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
    "AND trim({c}, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
)
CONDITION_TYPES = "('qa_criterion_failed','risk_finding','commitment_missed')"
SEVERITIES = "('info','warning','critical')"
DELIVERY_MODES = "('immediate','digest_only')"
ALERT_STATUSES_SQL = "('open','acknowledged','suppressed','escalated')"
SUPPRESS_REASONS = "('false_positive','known_issue','duplicate','other','inherited')"
ESCALATION_REASONS = "('manual','unacknowledged')"
EVENT_SOURCES = "('review_result','analysis_run','action_item')"
DELIVERY_PURPOSES = "('alert','escalation','digest')"
DELIVERY_CHANNELS = "('in_app','email')"
DELIVERY_STATUSES = "('pending','sent','failed','skipped')"
DELIVERY_REASONS = (
    "('not_authorized','muted','digest_disabled','email_missing','email_send_failed')"
)

RULE_CHECKS = {
    "ck_alert_rules_name": "length(trim(name)) >= 1",
    "ck_alert_rules_condition_type": f"condition_type IN {CONDITION_TYPES}",
    "ck_alert_rules_severity": f"severity IN {SEVERITIES}",
    "ck_alert_rules_delivery_mode": f"delivery_mode IN {DELIVERY_MODES}",
    "ck_alert_rules_criterion_key": (
        f"criterion_id IS NULL OR ({_KEY_RULE.format(c='criterion_id')})"
    ),
    "ck_alert_rules_condition_shape": (
        "condition_type NOT IN ('risk_finding','commitment_missed') OR criterion_id IS NULL"
    ),
    "ck_alert_rules_dedup_window": "dedup_window_minutes >= 5 AND dedup_window_minutes <= 10080",
    "ck_alert_rules_escalate_after": (
        "escalate_after_minutes IS NULL OR "
        "(escalate_after_minutes >= 5 AND escalate_after_minutes <= 10080)"
    ),
    "ck_alert_rules_revision": "revision >= 1",
}
ALERT_CHECKS = {
    "ck_alerts_condition_type": f"condition_type IN {CONDITION_TYPES}",
    "ck_alerts_severity": f"severity IN {SEVERITIES}",
    "ck_alerts_status": f"status IN {ALERT_STATUSES_SQL}",
    "ck_alerts_suppress_reason": (
        f"suppress_reason IS NULL OR suppress_reason IN {SUPPRESS_REASONS}"
    ),
    "ck_alerts_escalation_reason": (
        f"escalation_reason IS NULL OR escalation_reason IN {ESCALATION_REASONS}"
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
    "ck_alert_events_source_type": f"source_type IN {EVENT_SOURCES}",
    "ck_alert_events_condition_ref": "length(condition_ref) >= 1",
}
DELIVERY_CHECKS = {
    "ck_alert_deliveries_purpose": f"purpose IN {DELIVERY_PURPOSES}",
    "ck_alert_deliveries_channel": f"channel IN {DELIVERY_CHANNELS}",
    "ck_alert_deliveries_status": f"status IN {DELIVERY_STATUSES}",
    "ck_alert_deliveries_reason": f"reason IS NULL OR reason IN {DELIVERY_REASONS}",
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
UNIQUE_COLUMNS = {
    "uq_alert_rules_name": (RULES_TABLE, ("workspace_id", "name")),
    "uq_alerts_window": (ALERTS, ("rule_id", "dedupe_group", "window_started_at")),
    "uq_alert_events_condition": (EVENTS, ("rule_id", "meeting_id", "condition_ref")),
    "uq_alert_deliveries_target": (
        DELIVERIES,
        ("workspace_id", "purpose", "subject_ref", "recipient_user_id", "channel"),
    ),
    "uq_notification_preferences_user": (PREFERENCES, ("workspace_id", "user_id")),
}
RULE_USER_COLUMNS = ("created_by_user_id", "updated_by_user_id")
ALERT_USER_COLUMNS = (
    "subject_user_id",
    "acknowledged_by_user_id",
    "suppressed_by_user_id",
    "escalated_by_user_id",
)
DELIVERY_USER_COLUMNS = ("recipient_user_id",)
USER_COLUMNS = RULE_USER_COLUMNS + ALERT_USER_COLUMNS + DELIVERY_USER_COLUMNS


def _on_postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _requires_postgres() -> None:
    if not _on_postgres():
        pytest.skip("needs PostgreSQL")


def _scalar(sql: str, **params):
    with inspection_session_factory()() as db:
        return db.execute(text(sql), params).scalar()


def _tenant(client, email_outbox, label: str) -> dict:
    credentials = register_user(client, email_outbox, email=f"alerts-{label}@example.com")
    session = login_user(client, credentials)
    return {"user_id": session["user"]["id"], "workspace_id": session["workspaces"][0]["id"]}


def _meeting(tenant: dict, key: str) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=tenant["workspace_id"],
            created_by_user_id=tenant["user_id"],
            title=f"Support call {key}",
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def _user(email: str) -> str:
    with inspection_session_factory()() as db:
        user = User(email=email, display_name="Agent")
        db.add(user)
        db.commit()
        return user.id


def _rule(setup: dict, **extra: object) -> dict[str, object]:
    return {
        "workspace_id": setup["workspace_id"],
        "name": "Failed greeting",
        "condition_type": "qa_criterion_failed",
        "criterion_id": CRITERION,
        "severity": "warning",
        "recipient_roles": ["owner"],
        "recipient_user_ids": [],
        "delivery_mode": "immediate",
        "dedup_window_minutes": 1440,
        "escalate_after_minutes": None,
        "enabled": True,
        "revision": 1,
        "created_by_user_id": setup.get("user_id"),
        "updated_by_user_id": setup.get("user_id"),
        **extra,
    }


def _risk_rule(setup: dict, **extra: object) -> dict[str, object]:
    values = _rule(setup, condition_type="risk_finding", name="Risk finding")
    values["criterion_id"] = None
    values.update(extra)
    return values


def _alert(setup: dict, **extra: object) -> dict[str, object]:
    return {
        "workspace_id": setup["workspace_id"],
        "rule_id": setup["rule_id"],
        "condition_type": "qa_criterion_failed",
        "condition_group": CRITERION,
        "dedupe_group": f"{setup.get('agent_id', setup.get('user_id'))}|{CRITERION}",
        "subject_user_id": setup.get("agent_id") or setup.get("user_id"),
        "severity": "warning",
        "status": "open",
        "window_started_at": NOW,
        "window_ends_at": NOW + timedelta(minutes=30),
        "first_event_at": NOW,
        "last_event_at": NOW,
        "event_count": 1,
        "revision": 1,
        **extra,
    }


def _event(setup: dict, **extra: object) -> dict[str, object]:
    return {
        "workspace_id": setup["workspace_id"],
        "alert_id": setup["alert_id"],
        "rule_id": setup["rule_id"],
        "meeting_id": setup["meeting_id"],
        "source_type": "review_result",
        "source_id": "f" * 36,
        "condition_ref": CRITERION,
        "occurred_at": NOW,
        **extra,
    }


def _delivery(setup: dict, **extra: object) -> dict[str, object]:
    return {
        "workspace_id": setup["workspace_id"],
        "alert_id": setup.get("alert_id"),
        "purpose": "alert",
        "channel": "in_app",
        "subject_ref": setup.get("alert_id"),
        "recipient_user_id": setup.get("recipient_id") or setup.get("user_id"),
        "status": "sent",
        "reason": None,
        "attempt_count": 0,
        "sent_at": NOW,
        "notification_id": None,
        **extra,
    }


def _digest_delivery(setup: dict, **extra: object) -> dict[str, object]:
    values = _delivery(
        setup,
        purpose="digest",
        channel="email",
        subject_ref="digest:2026-11-14",
        status="pending",
        sent_at=None,
    )
    values["alert_id"] = None
    values.update(extra)
    return values


def _preference(setup: dict, **extra: object) -> dict[str, object]:
    return {
        "workspace_id": setup["workspace_id"],
        "user_id": setup.get("recipient_id") or setup.get("user_id"),
        "digest_enabled": True,
        "email_enabled": False,
        "muted_alert_rule_ids": [],
        **extra,
    }


def _chain(tenant: dict, key: str, *, rule=None, alert=None) -> dict:
    """A rule, an alert and a meeting of the tenant, ready for events."""
    setup = dict(tenant)
    if "meeting_id" not in setup:
        setup["meeting_id"] = _meeting(tenant, key)
    setup["rule_id"] = _add(AlertRule, (rule or _rule)(setup, name=f"Rule {key}"))
    setup["alert_id"] = _add(Alert, (alert or _alert)(setup, dedupe_group=f"{key}|{CRITERION}"))
    return setup


def _names(constraint: str) -> str:
    if constraint.startswith("fk_") and not _on_postgres():
        return "FOREIGN KEY constraint failed"
    if constraint in UNIQUE_COLUMNS and not _on_postgres():
        table, columns = UNIQUE_COLUMNS[constraint]
        return re.escape(", ".join(f"{table}.{column}" for column in columns))
    return rf"\b{constraint}\b"


def _refused(model, values: dict[str, object], constraint: str) -> None:
    """The insert fails on ``constraint``, and on no other rule first."""
    with inspection_session_factory()() as db:
        db.add(model(**values))
        with pytest.raises(IntegrityError, match=_names(constraint)):
            db.flush()
        db.rollback()


def _ids(table: str, **where: str) -> set[str]:
    clauses = " AND ".join(f"{column} = :{column}" for column in where) or "1 = 1"
    with inspection_session_factory()() as db:
        return set(
            db.execute(text(f"SELECT id FROM {table} WHERE {clauses}"), where).scalars()  # noqa: S608
        )


# --- the deny and allow cases (SPEC section 3) ----------------------------------------------


def _assert_rule_list_rules(tenant: dict, client, email_outbox, other: dict | None = None) -> None:
    setup = {"workspace_id": tenant["workspace_id"], "user_id": tenant["user_id"]}
    _refused(AlertRule, _rule(setup, name="   "), "ck_alert_rules_name")
    _refused(AlertRule, _rule(setup, condition_type="sentiment"), "ck_alert_rules_condition_type")
    _refused(AlertRule, _rule(setup, severity="high"), "ck_alert_rules_severity")
    _refused(AlertRule, _rule(setup, delivery_mode="weekly"), "ck_alert_rules_delivery_mode")
    _refused(AlertRule, _rule(setup, revision=0), "ck_alert_rules_revision")
    _add(AlertRule, _rule(setup, name="Missed greeting", severity="critical"))
    _add(AlertRule, _risk_rule(setup, delivery_mode="digest_only"))


def _assert_rule_criterion_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = {"workspace_id": tenant["workspace_id"], "user_id": tenant["user_id"]}
    _refused(AlertRule, _rule(setup, criterion_id="9x"), "ck_alert_rules_criterion_key")
    _refused(
        AlertRule,
        _risk_rule(setup, criterion_id=CRITERION),
        "ck_alert_rules_condition_shape",
    )
    _refused(
        AlertRule,
        _rule(
            setup,
            name="Missed commitment",
            condition_type="commitment_missed",
            criterion_id="closing",
        ),
        "ck_alert_rules_condition_shape",
    )
    _add(AlertRule, _rule(setup, name="QA criterion", criterion_id=CRITERION))
    _add(AlertRule, _rule(setup, name="Any criterion", criterion_id=None))
    _add(AlertRule, _risk_rule(setup, name="Any risk", criterion_id=None))
    _add(
        AlertRule,
        _rule(
            setup,
            name="Missed commitments",
            condition_type="commitment_missed",
            criterion_id=None,
        ),
    )


def _assert_rule_window_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = {"workspace_id": tenant["workspace_id"], "user_id": tenant["user_id"]}
    for value in (4, 10081):
        _refused(AlertRule, _rule(setup, dedup_window_minutes=value), "ck_alert_rules_dedup_window")
        _refused(
            AlertRule, _rule(setup, escalate_after_minutes=value), "ck_alert_rules_escalate_after"
        )
    _add(
        AlertRule,
        _rule(setup, name="Short window", dedup_window_minutes=5, escalate_after_minutes=10080),
    )
    _add(
        AlertRule,
        _rule(setup, name="Any window", dedup_window_minutes=10080, escalate_after_minutes=5),
    )
    _add(AlertRule, _rule(setup, name="No escalation", escalate_after_minutes=None))


def _assert_rule_name_is_unique_per_workspace(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = {"workspace_id": tenant["workspace_id"], "user_id": tenant["user_id"]}
    other = other or _tenant(client, email_outbox, "rule-name-other")
    _add(AlertRule, _rule(setup, name="One-off name"))
    _refused(AlertRule, _rule(setup, name="One-off name"), "uq_alert_rules_name")
    _add(AlertRule, _rule(other, name="One-off name"))
    _add(AlertRule, _rule(setup, name="Another name"))


def _assert_alert_list_rules(tenant: dict, client, email_outbox, other: dict | None = None) -> None:
    setup = _chain(tenant, "alert_lists")
    _refused(Alert, _alert(setup, status="closed"), "ck_alerts_status")
    _refused(Alert, _alert(setup, severity="x"), "ck_alerts_severity")
    _refused(Alert, _alert(setup, condition_type="x"), "ck_alerts_condition_type")
    _refused(Alert, _alert(setup, condition_group=""), "ck_alerts_condition_group")
    _refused(Alert, _alert(setup, dedupe_group=""), "ck_alerts_dedupe_group")
    _refused(Alert, _alert(setup, event_count=0), "ck_alerts_event_count")
    _refused(Alert, _alert(setup, revision=0), "ck_alerts_revision")
    _add(Alert, _alert(setup, dedupe_group="lists|other"))
    for status in ALERT_STATUSES:
        values = {"status": status, "dedupe_group": f"lists|{status}"}
        if status == "acknowledged":
            values["acknowledged_at"] = NOW
        if status == "suppressed":
            values.update(
                suppressed_at=NOW, suppressed_until=NOW + timedelta(days=1), suppress_reason="other"
            )
        if status == "escalated":
            values.update(escalated_at=NOW, escalation_reason="manual")
        _add(Alert, _alert(setup, **values))


def _assert_alert_reason_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "reasons")
    suppressed = {
        "status": "suppressed",
        "suppressed_at": NOW,
        "suppressed_until": NOW + timedelta(days=1),
    }
    escalated = {"status": "escalated", "escalated_at": NOW}
    _refused(
        Alert,
        _alert(setup, **suppressed, suppress_reason="spam"),
        "ck_alerts_suppress_reason",
    )
    _refused(
        Alert,
        _alert(setup, **escalated, escalation_reason="x"),
        "ck_alerts_escalation_reason",
    )
    for reason in ALERT_SUPPRESS_REASONS:
        _add(
            Alert,
            _alert(
                setup,
                **suppressed,
                suppress_reason=reason,
                dedupe_group=f"reasons|s{reason}",
            ),
        )
    for reason in ALERT_ESCALATION_REASONS:
        _add(
            Alert,
            _alert(
                setup,
                **escalated,
                escalation_reason=reason,
                dedupe_group=f"reasons|e{reason}",
            ),
        )


def _assert_alert_pair_rules(tenant: dict, client, email_outbox, other: dict | None = None) -> None:
    setup = _chain(tenant, "pairs")
    acknowledged = {"status": "acknowledged", "acknowledged_at": NOW}
    rule = "ck_alerts_escalation_pair"
    _refused(Alert, _alert(setup, **acknowledged, escalated_at=NOW), rule)
    _refused(Alert, _alert(setup, **acknowledged, escalation_reason="manual"), rule)
    _add(Alert, _alert(setup, **acknowledged, escalated_at=NOW, escalation_reason="manual"))


def _assert_alert_suppression_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "suppression")
    rule = "ck_alerts_suppression_fields"
    trio = {"status": "suppressed", "suppressed_at": NOW}
    _refused(Alert, _alert(setup, **trio, suppress_reason="other"), rule)
    _refused(Alert, _alert(setup, **trio, suppressed_until=NOW + timedelta(days=1)), rule)
    _add(
        Alert,
        _alert(
            setup,
            **trio,
            suppressed_until=NOW + timedelta(days=1),
            suppress_reason="other",
        ),
    )


def _assert_alert_state_field_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "state_fields")
    acknowledged = {"status": "acknowledged", "acknowledged_at": NOW}
    suppression_trio = {
        "suppressed_at": NOW,
        "suppressed_until": NOW + timedelta(days=1),
        "suppress_reason": "known_issue",
    }
    escalation_pair = {"escalated_at": NOW, "escalation_reason": "manual"}
    _refused(Alert, _alert(setup, acknowledged_at=NOW), "ck_alerts_open_fields")
    _refused(
        Alert,
        _alert(setup, status="open", **suppression_trio),
        "ck_alerts_open_fields",
    )
    _refused(Alert, _alert(setup, status="open", **escalation_pair), "ck_alerts_open_fields")
    _refused(Alert, _alert(setup, status="acknowledged"), "ck_alerts_acknowledged_fields")
    _refused(
        Alert,
        _alert(setup, **acknowledged, **suppression_trio),
        "ck_alerts_acknowledged_fields",
    )
    _refused(
        Alert,
        _alert(setup, status="suppressed", **suppression_trio, acknowledged_at=NOW),
        "ck_alerts_suppressed_fields",
    )
    _refused(Alert, _alert(setup, status="escalated"), "ck_alerts_escalated_fields")
    _refused(
        Alert,
        _alert(setup, status="escalated", **escalation_pair, acknowledged_at=NOW),
        "ck_alerts_escalated_fields",
    )
    _add(Alert, _alert(setup, **acknowledged, dedupe_group="state_fields|ack"))
    _add(
        Alert,
        _alert(
            setup,
            **acknowledged,
            **escalation_pair,
            dedupe_group="state_fields|ack2",
        ),
    )
    _add(
        Alert,
        _alert(setup, status="suppressed", **suppression_trio, dedupe_group="state_fields|sup"),
    )
    _add(
        Alert,
        _alert(
            setup,
            status="suppressed",
            **suppression_trio,
            **escalation_pair,
            dedupe_group="state_fields|sup2",
        ),
    )
    _add(
        Alert,
        _alert(
            setup,
            status="escalated",
            **escalation_pair,
            dedupe_group="state_fields|esc",
        ),
    )


def _assert_alert_window_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "window")
    rule = "uq_alerts_window"
    _add(Alert, _alert(setup))
    _refused(Alert, _alert(setup), rule)
    later = NOW + timedelta(hours=2)
    _add(
        Alert,
        _alert(
            setup,
            window_started_at=later,
            window_ends_at=later + timedelta(minutes=30),
            first_event_at=later,
            last_event_at=later,
        ),
    )
    _add(Alert, _alert(setup, dedupe_group="window|closing"))


def _assert_alert_scope_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "scope")
    if other is None:
        other = _tenant(client, email_outbox, "alert-scope-other")
    foreign = _chain(other, "alert_foreign")
    rule = "fk_alerts_rule"
    _refused(Alert, _alert(setup, rule_id=foreign["rule_id"]), rule)
    _refused(Alert, _alert(setup, rule_id="0" * 36), rule)
    _add(Alert, _alert(setup, dedupe_group="scope|own"))


def _assert_event_rules(tenant: dict, client, email_outbox, other: dict | None = None) -> None:
    setup = _chain(tenant, "events")
    _refused(AlertEvent, _event(setup, source_type="summary"), "ck_alert_events_source_type")
    _refused(AlertEvent, _event(setup, condition_ref=""), "ck_alert_events_condition_ref")
    _add(AlertEvent, _event(setup))
    _refused(AlertEvent, _event(setup), "uq_alert_events_condition")
    _add(AlertEvent, _event(setup, source_type="analysis_run", condition_ref="risk"))
    _add(AlertEvent, _event(setup, source_type="action_item", condition_ref="0" * 36))
    other_meeting = _meeting(tenant, "events_other")
    _add(AlertEvent, _event(setup, meeting_id=other_meeting))
    _add(AlertEvent, _event(setup, condition_ref="closing"))


def _assert_event_scope_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "event_scope")
    if other is None:
        other = _tenant(client, email_outbox, "event-scope-other")
    foreign = _chain(other, "event_foreign")
    _refused(AlertEvent, _event(setup, alert_id=foreign["alert_id"]), "fk_alert_events_alert")
    _refused(AlertEvent, _event(setup, rule_id=foreign["rule_id"]), "fk_alert_events_rule")
    _add(AlertEvent, _event(setup))


def _assert_delivery_list_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "delivery_lists")
    _refused(AlertDelivery, _delivery(setup, purpose="sms"), "ck_alert_deliveries_purpose")
    _refused(AlertDelivery, _delivery(setup, channel="sms"), "ck_alert_deliveries_channel")
    _refused(AlertDelivery, _delivery(setup, status="done"), "ck_alert_deliveries_status")
    _refused(AlertDelivery, _delivery(setup, reason="x"), "ck_alert_deliveries_reason")
    _refused(AlertDelivery, _delivery(setup, subject_ref=""), "ck_alert_deliveries_subject_ref")
    _refused(AlertDelivery, _delivery(setup, attempt_count=-1), "ck_alert_deliveries_attempt_count")
    _add(AlertDelivery, _delivery(setup, channel="email"))


def _assert_delivery_shape_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "delivery_shape")
    rule = "ck_alert_deliveries_purpose_shape"
    _refused(AlertDelivery, _digest_delivery(setup, alert_id=setup["alert_id"]), rule)
    _refused(AlertDelivery, _delivery(setup, alert_id=None), rule)
    _add(AlertDelivery, _digest_delivery(setup))
    _add(AlertDelivery, _delivery(setup))
    _add(
        AlertDelivery,
        _delivery(setup, purpose="escalation", channel="email", attempt_count=1),
    )


def _assert_delivery_status_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "delivery_status")
    _refused(AlertDelivery, _delivery(setup, sent_at=None), "ck_alert_deliveries_sent_fields")
    _refused(AlertDelivery, _delivery(setup, reason="muted"), "ck_alert_deliveries_sent_fields")
    _refused(
        AlertDelivery, _delivery(setup, status="skipped"), "ck_alert_deliveries_skipped_fields"
    )
    _refused(AlertDelivery, _delivery(setup, status="failed"), "ck_alert_deliveries_failed_fields")
    _refused(
        AlertDelivery,
        _delivery(setup, status="failed", reason="email_send_failed", sent_at=NOW),
        "ck_alert_deliveries_failed_fields",
    )
    _refused(
        AlertDelivery,
        _delivery(setup, status="pending", sent_at=NOW),
        "ck_alert_deliveries_pending_fields",
    )
    _add(AlertDelivery, _delivery(setup, status="skipped", reason="muted", sent_at=None))
    _add(
        AlertDelivery,
        _delivery(
            setup,
            status="failed",
            reason="email_send_failed",
            sent_at=None,
            channel="email",
            attempt_count=3,
        ),
    )
    _add(
        AlertDelivery,
        _delivery(setup, status="pending", sent_at=None, purpose="escalation", attempt_count=1),
    )


def _assert_delivery_target_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "delivery_target")
    rule = "uq_alert_deliveries_target"
    _add(AlertDelivery, _delivery(setup))
    _refused(AlertDelivery, _delivery(setup), rule)
    _add(AlertDelivery, _delivery(setup, channel="email", status="pending", sent_at=None))
    other_recipient = _user("alerts-other-recipient@example.com")
    _add(
        AlertDelivery,
        _delivery(setup, recipient_user_id=other_recipient, purpose="escalation"),
    )


def _assert_delivery_scope_rules(
    tenant: dict, client, email_outbox, other: dict | None = None
) -> None:
    setup = _chain(tenant, "delivery_scope")
    if other is None:
        other = _tenant(client, email_outbox, "delivery-scope-other")
    foreign = _chain(other, "delivery_foreign")
    rule = "fk_alert_deliveries_alert"
    _refused(AlertDelivery, _delivery(setup, alert_id=foreign["alert_id"]), rule)
    _refused(AlertDelivery, _delivery(setup, alert_id="0" * 36), rule)
    _add(AlertDelivery, _delivery(setup))


def _assert_preference_rules(tenant: dict, client, email_outbox, other: dict | None = None) -> None:
    other = other or _tenant(client, email_outbox, "preference-other")
    _add(AlertRule, _rule(tenant, name="Muted rule"))
    _add(NotificationPreference, _preference(tenant))
    _refused(NotificationPreference, _preference(tenant), "uq_notification_preferences_user")
    _add(NotificationPreference, _preference(other, user_id=tenant["user_id"]))
    second_user = _user("alerts-second-user@example.com")
    muted = _ids(RULES_TABLE, name="Muted rule").pop()
    _add(
        NotificationPreference,
        _preference(
            {"workspace_id": tenant["workspace_id"]},
            user_id=second_user,
            muted_alert_rule_ids=[muted],
        ),
    )


RULES = (
    _assert_rule_list_rules,
    _assert_rule_criterion_rules,
    _assert_rule_window_rules,
    _assert_rule_name_is_unique_per_workspace,
    _assert_alert_list_rules,
    _assert_alert_reason_rules,
    _assert_alert_pair_rules,
    _assert_alert_suppression_rules,
    _assert_alert_state_field_rules,
    _assert_alert_window_rules,
    _assert_alert_scope_rules,
    _assert_event_rules,
    _assert_event_scope_rules,
    _assert_delivery_list_rules,
    _assert_delivery_shape_rules,
    _assert_delivery_status_rules,
    _assert_delivery_target_rules,
    _assert_delivery_scope_rules,
    _assert_preference_rules,
)


@pytest.mark.parametrize("rules", RULES, ids=lambda rules: rules.__name__.removeprefix("_assert_"))
def test_the_database_refuses_the_deny_cases_and_takes_the_allow_cases(client, email_outbox, rules):
    rules(_tenant(client, email_outbox, "rules"), client, email_outbox)


def test_the_orm_refuses_to_update_an_event(client, email_outbox):
    setup = _chain(_tenant(client, email_outbox, "immutable"), "immutable")
    event_id = _add(AlertEvent, _event(setup))
    for column, value in (
        ("source_id", "0" * 36),
        ("occurred_at", NOW + timedelta(hours=1)),
    ):
        with inspection_session_factory()() as db:
            row = db.get(AlertEvent, event_id)
            setattr(row, column, value)
            with pytest.raises(AlertEventImmutableError, match=column):
                db.flush()
            db.rollback()
    with inspection_session_factory()() as db:
        row = db.get(AlertEvent, event_id)
        assert row.condition_ref == CRITERION
        db.flush()
        db.delete(row)
        db.commit()
    assert _ids(EVENTS) == set()


# --- the ORM, the frozen lists and the RLS lists --------------------------------------------

RULE_COLUMNS = [
    "id",
    "workspace_id",
    "name",
    "condition_type",
    "criterion_id",
    "severity",
    "recipient_roles",
    "recipient_user_ids",
    "delivery_mode",
    "dedup_window_minutes",
    "escalate_after_minutes",
    "enabled",
    "revision",
    "created_by_user_id",
    "updated_by_user_id",
    "created_at",
    "updated_at",
]
RULE_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "name": 120,
    "condition_type": 30,
    "criterion_id": 64,
    "severity": 20,
    "delivery_mode": 20,
    "created_by_user_id": 36,
    "updated_by_user_id": 36,
}
RULE_NULLABLE = {
    "criterion_id",
    "escalate_after_minutes",
    "created_by_user_id",
    "updated_by_user_id",
}
RULE_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("created_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("updated_by_user_id",), ("users.id",), "SET NULL"),
}
RULE_UNIQUES = {
    "uq_alert_rules_scope": ("id", "workspace_id"),
    "uq_alert_rules_name": ("workspace_id", "name"),
}
RULE_INDEXES = {
    "ix_alert_rules_workspace_id": (["workspace_id"], False),
    "ix_alert_rules_active": (["workspace_id", "enabled", "condition_type"], False),
    "ix_alert_rules_created_by_user_id": (["created_by_user_id"], False),
    "ix_alert_rules_updated_by_user_id": (["updated_by_user_id"], False),
}
ALERT_COLUMNS = [
    "id",
    "workspace_id",
    "rule_id",
    "condition_type",
    "condition_group",
    "dedupe_group",
    "subject_user_id",
    "severity",
    "status",
    "window_started_at",
    "window_ends_at",
    "first_event_at",
    "last_event_at",
    "event_count",
    "acknowledged_at",
    "acknowledged_by_user_id",
    "suppressed_at",
    "suppressed_until",
    "suppress_reason",
    "suppressed_by_user_id",
    "escalated_at",
    "escalation_reason",
    "escalated_by_user_id",
    "revision",
    "created_at",
    "updated_at",
]
ALERT_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "rule_id": 36,
    "condition_type": 30,
    "condition_group": 80,
    "dedupe_group": 160,
    "subject_user_id": 36,
    "severity": 20,
    "status": 20,
    "suppress_reason": 30,
    "escalation_reason": 30,
    "acknowledged_by_user_id": 36,
    "suppressed_by_user_id": 36,
    "escalated_by_user_id": 36,
}
ALERT_NULLABLE = {
    "subject_user_id",
    "acknowledged_at",
    "acknowledged_by_user_id",
    "suppressed_at",
    "suppressed_until",
    "suppress_reason",
    "suppressed_by_user_id",
    "escalated_at",
    "escalation_reason",
    "escalated_by_user_id",
}
ALERT_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("subject_user_id",), ("users.id",), "SET NULL"),
    (None, ("acknowledged_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("suppressed_by_user_id",), ("users.id",), "SET NULL"),
    (None, ("escalated_by_user_id",), ("users.id",), "SET NULL"),
    (
        "fk_alerts_rule",
        ("rule_id", "workspace_id"),
        ("alert_rules.id", "alert_rules.workspace_id"),
        "CASCADE",
    ),
}
ALERT_UNIQUES = {
    "uq_alerts_scope": ("id", "workspace_id"),
    "uq_alerts_window": ("rule_id", "dedupe_group", "window_started_at"),
}
ALERT_INDEXES = {
    "ix_alerts_workspace_id": (["workspace_id"], False),
    "ix_alerts_queue": (["workspace_id", "status", "last_event_at", "id"], False),
    "ix_alerts_rule_group": (["rule_id", "dedupe_group", "window_ends_at"], False),
    "ix_alerts_subject_user_id": (["subject_user_id"], False),
    "ix_alerts_acknowledged_by_user_id": (["acknowledged_by_user_id"], False),
    "ix_alerts_suppressed_by_user_id": (["suppressed_by_user_id"], False),
    "ix_alerts_escalated_by_user_id": (["escalated_by_user_id"], False),
}
EVENT_COLUMNS = [
    "id",
    "workspace_id",
    "alert_id",
    "rule_id",
    "meeting_id",
    "source_type",
    "source_id",
    "condition_ref",
    "occurred_at",
    "created_at",
]
EVENT_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "alert_id": 36,
    "rule_id": 36,
    "meeting_id": 36,
    "source_type": 30,
    "source_id": 36,
    "condition_ref": 80,
}
EVENT_NULLABLE: set[str] = set()
EVENT_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("meeting_id",), ("meetings.id",), "CASCADE"),
    (
        "fk_alert_events_alert",
        ("alert_id", "workspace_id"),
        ("alerts.id", "alerts.workspace_id"),
        "CASCADE",
    ),
    (
        "fk_alert_events_rule",
        ("rule_id", "workspace_id"),
        ("alert_rules.id", "alert_rules.workspace_id"),
        "CASCADE",
    ),
}
EVENT_UNIQUES = {"uq_alert_events_condition": ("rule_id", "meeting_id", "condition_ref")}
EVENT_INDEXES = {
    "ix_alert_events_workspace_id": (["workspace_id"], False),
    "ix_alert_events_alert": (["alert_id", "occurred_at"], False),
    "ix_alert_events_workspace_meeting": (["workspace_id", "meeting_id"], False),
    "ix_alert_events_meeting_id": (["meeting_id"], False),
}
DELIVERY_COLUMNS = [
    "id",
    "workspace_id",
    "alert_id",
    "purpose",
    "channel",
    "subject_ref",
    "recipient_user_id",
    "status",
    "reason",
    "attempt_count",
    "next_attempt_at",
    "last_attempt_at",
    "sent_at",
    "notification_id",
    "created_at",
    "updated_at",
]
DELIVERY_LENGTHS = {
    "id": 36,
    "workspace_id": 36,
    "alert_id": 36,
    "purpose": 20,
    "channel": 20,
    "subject_ref": 80,
    "recipient_user_id": 36,
    "status": 20,
    "reason": 40,
    "notification_id": 36,
}
DELIVERY_NULLABLE = {
    "alert_id",
    "recipient_user_id",
    "reason",
    "next_attempt_at",
    "last_attempt_at",
    "sent_at",
    "notification_id",
}
DELIVERY_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("recipient_user_id",), ("users.id",), "SET NULL"),
    (None, ("notification_id",), ("notifications.id",), "SET NULL"),
    (
        "fk_alert_deliveries_alert",
        ("alert_id", "workspace_id"),
        ("alerts.id", "alerts.workspace_id"),
        "CASCADE",
    ),
}
DELIVERY_UNIQUES = {
    "uq_alert_deliveries_target": (
        "workspace_id",
        "purpose",
        "subject_ref",
        "recipient_user_id",
        "channel",
    ),
}
DELIVERY_INDEXES = {
    "ix_alert_deliveries_workspace_id": (["workspace_id"], False),
    "ix_alert_deliveries_due": (["workspace_id", "status", "next_attempt_at"], False),
    "ix_alert_deliveries_alert_id": (["alert_id"], False),
    "ix_alert_deliveries_recipient_user_id": (["recipient_user_id"], False),
    "ix_alert_deliveries_notification_id": (["notification_id"], False),
}
PREFERENCE_COLUMNS = [
    "id",
    "workspace_id",
    "user_id",
    "digest_enabled",
    "email_enabled",
    "muted_alert_rule_ids",
    "created_at",
    "updated_at",
]
PREFERENCE_LENGTHS = {"id": 36, "workspace_id": 36, "user_id": 36}
PREFERENCE_NULLABLE: set[str] = set()
PREFERENCE_FOREIGN_KEYS = {
    (None, ("workspace_id",), ("workspaces.id",), "CASCADE"),
    (None, ("user_id",), ("users.id",), "CASCADE"),
}
PREFERENCE_UNIQUES = {"uq_notification_preferences_user": ("workspace_id", "user_id")}
PREFERENCE_INDEXES = {
    "ix_notification_preferences_workspace_id": (["workspace_id"], False),
    "ix_notification_preferences_user_id": (["user_id"], False),
}


def _declared(table) -> dict:
    named = {constraint.name: constraint for constraint in table.constraints if constraint.name}
    return {
        "foreign_keys": {
            (
                constraint.name,
                tuple(constraint.column_keys),
                tuple(element.target_fullname for element in constraint.elements),
                constraint.ondelete,
            )
            for constraint in table.foreign_key_constraints
        },
        "checks": {name: str(c.sqltext) for name, c in named.items() if name.startswith("ck_")},
        "uniques": {
            name: tuple(column.name for column in c.columns)
            for name, c in named.items()
            if name.startswith("uq_")
        },
        "indexes": {
            index.name: ([column.name for column in index.columns], index.unique)
            for index in table.indexes
        },
    }


def test_the_orm_declares_the_alert_rules_table():
    table = AlertRule.__table__
    declared = _declared(table)
    assert table.name == RULES_TABLE
    assert [column.name for column in table.columns] == RULE_COLUMNS
    for name, length in RULE_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert {column.name for column in table.columns if column.nullable} == RULE_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.delivery_mode.default.arg == "immediate"
    assert table.c.dedup_window_minutes.default.arg == 1440
    assert table.c.enabled.default.arg is True
    assert table.c.revision.default.arg == 1
    assert table.c.recipient_roles.default.arg.__name__ == "list"
    assert table.c.recipient_user_ids.default.arg.__name__ == "list"
    for name in ("recipient_roles", "recipient_user_ids"):
        assert table.c[name].type.__class__.__name__ == "JSON", name
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == "utcnow", name
    assert all(column.server_default is None for column in table.columns)
    assert declared["foreign_keys"] == RULE_FOREIGN_KEYS
    assert declared["checks"] == RULE_CHECKS
    assert declared["uniques"] == RULE_UNIQUES
    assert declared["indexes"] == RULE_INDEXES
    assert "HOM-134" in AlertRule.__doc__
    assert "dedup" in AlertRule.__doc__


def test_the_orm_declares_the_alerts_table():
    table = Alert.__table__
    declared = _declared(table)
    assert table.name == ALERTS
    assert [column.name for column in table.columns] == ALERT_COLUMNS
    for name, length in ALERT_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ALERT_COLUMNS:
        if name.endswith("_at"):
            assert table.c[name].type.timezone, name
    assert {column.name for column in table.columns if column.nullable} == ALERT_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.status.default.arg == "open"
    assert table.c.event_count.default.arg == 1
    assert table.c.revision.default.arg == 1
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == "utcnow", name
    assert all(column.server_default is None for column in table.columns)
    assert declared["foreign_keys"] == ALERT_FOREIGN_KEYS
    assert declared["checks"] == ALERT_CHECKS
    assert declared["uniques"] == ALERT_UNIQUES
    assert declared["indexes"] == ALERT_INDEXES
    assert "HOM-134" in Alert.__doc__
    assert "window" in Alert.__doc__


def test_the_orm_declares_the_alert_events_table():
    table = AlertEvent.__table__
    declared = _declared(table)
    assert table.name == EVENTS
    assert [column.name for column in table.columns] == EVENT_COLUMNS
    for name, length in EVENT_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("occurred_at", "created_at"):
        assert table.c[name].type.timezone, name
    assert {column.name for column in table.columns if column.nullable} == EVENT_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.created_at.default.arg.__name__ == "utcnow"
    assert table.c.occurred_at.default is None
    assert all(column.server_default is None for column in table.columns)
    assert declared["foreign_keys"] == EVENT_FOREIGN_KEYS
    assert declared["checks"] == EVENT_CHECKS
    assert declared["uniques"] == EVENT_UNIQUES
    assert declared["indexes"] == EVENT_INDEXES
    assert not table.c.source_id.foreign_keys
    assert not table.c.condition_ref.foreign_keys
    assert "HOM-134" in AlertEvent.__doc__
    assert "immutable" in AlertEvent.__doc__


def test_the_orm_declares_the_alert_deliveries_table():
    table = AlertDelivery.__table__
    declared = _declared(table)
    assert table.name == DELIVERIES
    assert [column.name for column in table.columns] == DELIVERY_COLUMNS
    for name, length in DELIVERY_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("next_attempt_at", "last_attempt_at", "sent_at", "created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert {column.name for column in table.columns if column.nullable} == DELIVERY_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.status.default.arg == "pending"
    assert table.c.attempt_count.default.arg == 0
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == "utcnow", name
    assert all(column.server_default is None for column in table.columns)
    assert declared["foreign_keys"] == DELIVERY_FOREIGN_KEYS
    assert declared["checks"] == DELIVERY_CHECKS
    assert declared["uniques"] == DELIVERY_UNIQUES
    assert declared["indexes"] == DELIVERY_INDEXES
    assert "HOM-134" in AlertDelivery.__doc__
    assert "digest" in AlertDelivery.__doc__


def test_the_orm_declares_the_notification_preferences_table():
    table = NotificationPreference.__table__
    declared = _declared(table)
    assert table.name == PREFERENCES
    assert [column.name for column in table.columns] == PREFERENCE_COLUMNS
    for name, length in PREFERENCE_LENGTHS.items():
        assert table.c[name].type.length == length, name
    for name in ("created_at", "updated_at"):
        assert table.c[name].type.timezone, name
    assert {column.name for column in table.columns if column.nullable} == PREFERENCE_NULLABLE
    assert [column.name for column in table.primary_key] == ["id"]
    assert table.c.id.default.arg.__name__ == "new_id"
    assert table.c.digest_enabled.default.arg is True
    assert table.c.email_enabled.default.arg is False
    assert table.c.muted_alert_rule_ids.default.arg.__name__ == "list"
    assert table.c.muted_alert_rule_ids.type.__class__.__name__ == "JSON"
    for name in ("created_at", "updated_at"):
        assert table.c[name].default.arg.__name__ == "utcnow", name
    assert all(column.server_default is None for column in table.columns)
    assert declared["foreign_keys"] == PREFERENCE_FOREIGN_KEYS
    assert declared["checks"] == {}
    assert declared["uniques"] == PREFERENCE_UNIQUES
    assert declared["indexes"] == PREFERENCE_INDEXES
    assert "HOM-134" in NotificationPreference.__doc__


def test_the_frozen_lists_and_the_check_strings():
    assert ALERT_CONDITION_TYPES == ("qa_criterion_failed", "risk_finding", "commitment_missed")
    assert ALERT_SEVERITIES == ("info", "warning", "critical")
    assert ALERT_DELIVERY_MODES == ("immediate", "digest_only")
    assert ALERT_RECIPIENT_ROLES == ("owner", "admin", "supervisor")
    assert ALERT_STATUSES == ("open", "acknowledged", "suppressed", "escalated")
    assert ALERT_ACTIVE_STATUSES == ("open", "escalated")
    assert ALERT_SUPPRESS_REASONS == (
        "false_positive",
        "known_issue",
        "duplicate",
        "other",
        "inherited",
    )
    assert ALERT_ESCALATION_REASONS == ("manual", "unacknowledged")
    assert ALERT_EVENT_SOURCES == ("review_result", "analysis_run", "action_item")
    assert ALERT_DELIVERY_PURPOSES == ("alert", "escalation", "digest")
    assert ALERT_DELIVERY_CHANNELS == ("in_app", "email")
    assert ALERT_DELIVERY_STATUSES == ("pending", "sent", "failed", "skipped")
    assert ALERT_DELIVERY_REASONS == (
        "not_authorized",
        "muted",
        "digest_disabled",
        "email_missing",
        "email_send_failed",
    )
    for name, values in (
        ("ck_alert_rules_condition_type", ALERT_CONDITION_TYPES),
        ("ck_alerts_condition_type", ALERT_CONDITION_TYPES),
        ("ck_alert_rules_severity", ALERT_SEVERITIES),
        ("ck_alerts_severity", ALERT_SEVERITIES),
        ("ck_alert_rules_delivery_mode", ALERT_DELIVERY_MODES),
        ("ck_alerts_status", ALERT_STATUSES),
        ("ck_alerts_suppress_reason", ALERT_SUPPRESS_REASONS),
        ("ck_alerts_escalation_reason", ALERT_ESCALATION_REASONS),
        ("ck_alert_events_source_type", ALERT_EVENT_SOURCES),
        ("ck_alert_deliveries_purpose", ALERT_DELIVERY_PURPOSES),
        ("ck_alert_deliveries_channel", ALERT_DELIVERY_CHANNELS),
        ("ck_alert_deliveries_status", ALERT_DELIVERY_STATUSES),
        ("ck_alert_deliveries_reason", ALERT_DELIVERY_REASONS),
    ):
        source = {**RULE_CHECKS, **ALERT_CHECKS, **EVENT_CHECKS, **DELIVERY_CHECKS}[name]
        assert source.endswith(f"IN {_sql_list(values)}"), name
    all_checks = (
        *RULE_CHECKS.values(),
        *ALERT_CHECKS.values(),
        *EVENT_CHECKS.values(),
        *DELIVERY_CHECKS.values(),
    )
    for check in all_checks:
        lowered = check.lower()
        assert "~" not in lowered and "regexp" not in lowered and "similar" not in lowered
        for column in USER_COLUMNS:
            assert column not in check, column
        assert "recipient_roles" not in check
    for table in TABLES:
        for column in Base.metadata.tables[table].c:
            assert not column.name.endswith("_key"), column.name
            assert "token" not in column.name and "secret" not in column.name, column.name


def _sql_list(values: tuple[str, ...]) -> str:
    return "(" + ",".join(f"'{value}'" for value in values) + ")"


def test_the_tables_are_workspace_tables_and_the_events_take_no_update():
    for table in TABLES:
        assert table in WORKSPACE_TABLES, table
        assert table not in APPEND_ONLY_TABLES, table
        assert table not in NO_DELETE_TABLES, table
        assert table not in SHARED_CONTENT_TABLES, table
        assert table not in UNPROTECTED_TABLES, table
        assert POLICIES[table] == (f"workspace_id = {TENANT}", f"workspace_id = {TENANT}")
    assert EVENTS in NO_UPDATE_TABLES
    for table in (RULES_TABLE, ALERTS, DELIVERIES, PREFERENCES):
        assert table not in NO_UPDATE_TABLES, table
    assert WORKSPACE_TABLES.index("action_proposals") < WORKSPACE_TABLES.index(RULES_TABLE)
    first = WORKSPACE_TABLES.index(RULES_TABLE)
    for table in (ALERTS, EVENTS, DELIVERIES, PREFERENCES):
        assert first < WORKSPACE_TABLES.index(table), table


# --- cascades, removals and user removal ----------------------------------------------------


def _full_chain(tenant: dict, key: str) -> dict:
    setup = _chain(tenant, key)
    setup["event_id"] = _add(AlertEvent, _event(setup))
    setup["delivery_id"] = _add(AlertDelivery, _delivery(setup))
    return setup


def test_a_workspace_a_rule_an_alert_or_a_call_takes_its_rows(client, email_outbox):
    first = _tenant(client, email_outbox, "cascade-a")
    second = _tenant(client, email_outbox, "cascade-b")
    _add(NotificationPreference, _preference(first))
    _add(NotificationPreference, _preference(second))
    by_meeting = _full_chain(second, "cascade_meeting")
    by_alert = _full_chain(second, "cascade_alert")
    by_rule = _full_chain(second, "cascade_rule")
    kept = _full_chain(second, "cascade_kept")
    elsewhere = _full_chain(first, "cascade_elsewhere")
    other_meeting = _meeting(second, "cascade_other_meeting")
    _add(AlertEvent, _event({**by_alert, "meeting_id": other_meeting, "condition_ref": "closing"}))

    with inspection_session_factory()() as db:
        db.execute(delete(Meeting).where(Meeting.id == by_meeting["meeting_id"]))
        db.commit()
    assert _ids(EVENTS, meeting_id=by_meeting["meeting_id"]) == set()
    assert _ids(ALERTS) == {
        by_meeting["alert_id"],
        by_alert["alert_id"],
        by_rule["alert_id"],
        kept["alert_id"],
        elsewhere["alert_id"],
    }

    with inspection_session_factory()() as db:
        db.execute(delete(Alert).where(Alert.id == by_alert["alert_id"]))
        db.commit()
    assert _ids(EVENTS, alert_id=by_alert["alert_id"]) == set()
    assert _ids(DELIVERIES, alert_id=by_alert["alert_id"]) == set()

    with inspection_session_factory()() as db:
        db.execute(delete(AlertRule).where(AlertRule.id == by_rule["rule_id"]))
        db.commit()
    assert _ids(ALERTS, rule_id=by_rule["rule_id"]) == set()
    assert _ids(EVENTS, rule_id=by_rule["rule_id"]) == set()
    assert _ids(DELIVERIES, alert_id=by_rule["alert_id"]) == set()

    with inspection_session_factory()() as db:
        db.execute(delete(Workspace).where(Workspace.id == first["workspace_id"]))
        db.commit()
    for table in TABLES:
        assert _ids(table, workspace_id=first["workspace_id"]) == set(), table
    assert _ids(EVENTS) == {kept["event_id"]}
    assert _ids(ALERTS) == {by_meeting["alert_id"], kept["alert_id"]}
    assert _ids(DELIVERIES) == {by_meeting["delivery_id"], kept["delivery_id"]}


def test_deleting_a_user_clears_the_user_columns_and_removes_the_preference(client, email_outbox):
    tenant = _tenant(client, email_outbox, "user-removal")
    user_id = _user("alerts-removed-user@example.com")
    rule_id = _add(AlertRule, _rule({**tenant, "user_id": user_id}))
    acknowledged = {
        "status": "acknowledged",
        "acknowledged_at": NOW,
        "escalated_at": NOW,
        "escalation_reason": "manual",
    }
    alert_id = _add(
        Alert, _alert({**tenant, "rule_id": rule_id, "agent_id": user_id}, **acknowledged)
    )
    notification_id = _add(
        Notification,
        {
            "workspace_id": tenant["workspace_id"],
            "user_id": user_id,
            "kind": "alert.opened",
            "title": "Alert opened",
            "body": "An alert opened.",
        },
    )
    delivery_id = _add(
        AlertDelivery,
        _delivery(
            {**tenant, "alert_id": alert_id, "recipient_id": user_id},
            notification_id=notification_id,
        ),
    )
    _add(NotificationPreference, _preference({**tenant, "recipient_id": user_id}))

    with inspection_session_factory()() as db:
        db.execute(delete(User).where(User.id == user_id))
        db.commit()
    with inspection_session_factory()() as db:
        rule = db.get(AlertRule, rule_id)
        assert rule.created_by_user_id is None and rule.updated_by_user_id is None
        alert = db.get(Alert, alert_id)
        for column in ALERT_USER_COLUMNS:
            assert getattr(alert, column) is None, column
        assert alert.status == "acknowledged"
        delivery = db.get(AlertDelivery, delivery_id)
        assert delivery.recipient_user_id is None
        assert delivery.notification_id is None
        assert db.scalar(select(NotificationPreference.id).limit(1)) is None

    kept_notification = _add(
        Notification,
        {
            "workspace_id": tenant["workspace_id"],
            "user_id": tenant["user_id"],
            "kind": "alert.escalated",
            "title": "Alert escalated",
            "body": "An alert escalated.",
        },
    )
    second = _chain(tenant, "user_removal_direct")
    direct_id = _add(AlertDelivery, _delivery(second, notification_id=kept_notification))
    with inspection_session_factory()() as db:
        db.execute(delete(Notification).where(Notification.id == kept_notification))
        db.commit()
    with inspection_session_factory()() as db:
        assert db.get(AlertDelivery, direct_id).notification_id is None


# --- the migration (SQLite) -----------------------------------------------------------------


def _migration_module():
    return importlib.import_module(f"backend.migrations.versions.{NEW_REVISION}_product_alerts")


def remove_product_alerts(connection) -> None:
    """Take a ``create_all`` schema back to before HOM-134.

    Older migration tests build the full ORM, drop the newer tables and stamp an
    older revision.  The children go first: the events, the deliveries and the
    alerts name the rules.
    """
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(connection)


def test_the_migration_follows_the_previous_head():
    module = _migration_module()
    assert module.revision == NEW_REVISION
    assert module.down_revision == BASE_REVISION
    doc = module.__doc__
    assert doc.splitlines()[0] == (
        "Product alert rules, alerts, events, deliveries and notification preferences, "
        "schema only (HOM-134, WP24-T02)"
    )
    for words in ("Why", "No data changes", "Downgrade", "NO_UPDATE_TABLES"):
        assert words in doc, words
    assert module.RULE_CHECKS == RULE_CHECKS
    assert module.ALERT_CHECKS == ALERT_CHECKS
    assert module.EVENT_CHECKS == EVENT_CHECKS
    assert module.DELIVERY_CHECKS == DELIVERY_CHECKS


def test_the_code_has_one_migration_head_this_revision():
    from backend.observability.release import code_migration_head

    assert code_migration_head() == HEAD_REVISION


def test_alembic_heads_prints_exactly_this_revision():
    import io

    from alembic import command
    from alembic.config import Config

    from backend.observability.release import PROJECT_ROOT

    shown = io.StringIO()
    command.heads(Config(str(PROJECT_ROOT / "alembic.ini"), stdout=shown))
    lines = [line for line in shown.getvalue().splitlines() if line.strip()]
    assert lines == [f"{HEAD_REVISION} (head)"], shown.getvalue()


def _reflect(connection, table: str) -> dict:
    inspector = inspect(connection)
    return {
        "columns": [
            (column["name"], str(column["type"]), column["nullable"])
            for column in inspector.get_columns(table)
        ],
        "primary_key": inspector.get_pk_constraint(table)["constrained_columns"],
        "foreign_keys": sorted(
            (
                key["name"] or "",
                tuple(key["constrained_columns"]),
                key["referred_table"],
                tuple(key["referred_columns"]),
                key["options"].get("ondelete"),
            )
            for key in inspector.get_foreign_keys(table)
        ),
        "unique": sorted(
            (unique["name"] or "", tuple(unique["column_names"]))
            for unique in inspector.get_unique_constraints(table)
        ),
        "checks": sorted(
            (check["name"] or "", check["sqltext"])
            for check in inspector.get_check_constraints(table)
        ),
        "indexes": sorted(
            (index["name"], tuple(index["column_names"]), bool(index["unique"]))
            for index in inspector.get_indexes(table)
        ),
    }


def _objects(connection) -> set[tuple[str, str, str]]:
    return {
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT type, name, tbl_name FROM sqlite_master WHERE tbl_name <> 'alembic_version'"
            )
        ).all()
    }


def _all_shapes(connection) -> dict:
    return {name: _reflect(connection, name) for name in inspect(connection).get_table_names()}


def test_on_sqlite_the_migration_builds_the_orm_tables_and_drops_only_them():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            remove_product_alerts(connection)
            before = _all_shapes(connection)
            before_objects = _objects(connection)

            module = _migration_module()
            with Operations.context(MigrationContext.configure(connection)):
                module.upgrade()
            assert {table: _reflect(connection, table) for table in TABLES} == orm
            for table, checks in (
                (RULES_TABLE, RULE_CHECKS),
                (ALERTS, ALERT_CHECKS),
                (EVENTS, EVENT_CHECKS),
                (DELIVERIES, DELIVERY_CHECKS),
            ):
                assert sorted(name for name, _ in orm[table]["checks"]) == sorted(checks)
            after = _all_shapes(connection)
            assert {k: v for k, v in after.items() if k not in TABLES} == before

            with Operations.context(MigrationContext.configure(connection)):
                module.downgrade()
            assert _all_shapes(connection) == before
            assert _objects(connection) == before_objects
    finally:
        engine.dispose()


def _alembic_at(monkeypatch, url: str):
    from alembic.config import Config

    from backend.config import reset_settings_cache
    from backend.observability.release import PROJECT_ROOT

    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    return Config(str(PROJECT_ROOT / "alembic.ini"))


def test_on_sqlite_alembic_upgrades_to_the_head_checks_clean_and_downgrades_one_step(
    tmp_path, monkeypatch
):
    from alembic import command
    from alembic.util.exc import AutogenerateDiffsDetected

    from backend.config import reset_settings_cache

    url = f"sqlite:///{tmp_path / 'product-alerts.db'}"
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            orm = {table: _reflect(connection, table) for table in TABLES}
            remove_product_alerts(connection)
            before_objects = _objects(connection)

        config = _alembic_at(monkeypatch, url)
        command.stamp(config, BASE_REVISION)
        command.upgrade(config, "head")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == HEAD_REVISION
            assert {table: _reflect(connection, table) for table in TABLES} == orm
        try:
            command.check(config)
        except AutogenerateDiffsDetected as exc:
            pytest.fail(f"the migration does not match the ORM: {exc}")

        command.downgrade(config, "-1")
        with engine.connect() as connection:
            version = connection.exec_driver_sql("SELECT version_num FROM alembic_version")
            assert version.scalar() == BASE_REVISION
            assert _objects(connection) == before_objects
    finally:
        engine.dispose()
        reset_settings_cache()


# --- PostgreSQL: the schema, the migration, isolation and grants ----------------------------


def _shape(table: str) -> dict:
    with inspection_session_factory()() as db:
        columns = db.execute(
            text(
                "SELECT column_name, data_type, character_maximum_length, is_nullable, "
                "column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :t"
            ),
            {"t": table},
        ).all()
        constraints = db.execute(
            text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        indexes = db.execute(
            text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = :t"), {"t": table}
        ).all()
        policies = db.execute(
            text(
                "SELECT policyname, permissive, roles::text, cmd, qual, with_check "
                "FROM pg_policies WHERE tablename = :t"
            ),
            {"t": table},
        ).all()
        flags = db.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = to_regclass(:t)"
            ),
            {"t": f"public.{table}"},
        ).all()
        grants = db.execute(
            text(
                "SELECT privilege_type FROM information_schema.role_table_grants "
                "WHERE table_schema = 'public' AND table_name = :t AND grantee = :r"
            ),
            {"t": table, "r": APP_ROLE},
        ).all()
    return {
        "columns": {name: tuple(rest) for name, *rest in columns},
        "constraints": dict(constraints),
        "indexes": dict(indexes),
        "policies": {name: tuple(rest) for name, *rest in policies},
        "rls": [tuple(row) for row in flags],
        "grants": sorted(row[0] for row in grants),
    }


EMPTY_SHAPE = {
    "columns": {},
    "constraints": {},
    "indexes": {},
    "policies": {},
    "rls": [],
    "grants": [],
}

OTHER_TABLE_QUERIES = {
    "tables": (
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' "
        "AND c.relname NOT IN (:a, :b, :c, :d, :e)"
    ),
    "columns": (
        "SELECT table_name, column_name, data_type, character_maximum_length, "
        "is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b, :c, :d, :e)"
    ),
    "constraints": (
        "SELECT c.conrelid::regclass::text, c.conname, pg_get_constraintdef(c.oid) "
        "FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE n.nspname = 'public' AND c.conrelid <> 0 "
        "AND c.conrelid::regclass::text NOT IN (:a, :b, :c, :d, :e)"
    ),
    "indexes": (
        "SELECT tablename, indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' AND tablename NOT IN (:a, :b, :c, :d, :e)"
    ),
    "policies": (
        "SELECT tablename, policyname, permissive, roles::text, cmd, qual, with_check "
        "FROM pg_policies WHERE schemaname = 'public' AND tablename NOT IN (:a, :b, :c, :d, :e)"
    ),
    "grants": (
        "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' AND table_name NOT IN (:a, :b, :c, :d, :e)"
    ),
}


def _other_tables() -> dict:
    excluded = dict.fromkeys("abcde", "")
    for key, table in zip("abcde", TABLES, strict=True):
        excluded[key] = table
    with inspection_session_factory()() as db:
        return {
            name: sorted(tuple(row) for row in db.execute(text(sql), excluded).all())
            for name, sql in OTHER_TABLE_QUERIES.items()
        }


def _varchar(length: int, nullable: str = "NO") -> tuple:
    return ("character varying", length, nullable, None)


def _plain(kind: str, nullable: str = "NO") -> tuple:
    return (kind, None, nullable, None)


def _timestamp(nullable: str = "NO") -> tuple:
    return _plain("timestamp with time zone", nullable)


EXPECTED_COLUMNS = {
    RULES_TABLE: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "name": _varchar(120),
        "condition_type": _varchar(30),
        "criterion_id": _varchar(64, "YES"),
        "severity": _varchar(20),
        "recipient_roles": _plain("json"),
        "recipient_user_ids": _plain("json"),
        "delivery_mode": _varchar(20),
        "dedup_window_minutes": _plain("integer"),
        "escalate_after_minutes": _plain("integer", "YES"),
        "enabled": _plain("boolean"),
        "revision": _plain("integer"),
        "created_by_user_id": _varchar(36, "YES"),
        "updated_by_user_id": _varchar(36, "YES"),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
    },
    ALERTS: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "rule_id": _varchar(36),
        "condition_type": _varchar(30),
        "condition_group": _varchar(80),
        "dedupe_group": _varchar(160),
        "subject_user_id": _varchar(36, "YES"),
        "severity": _varchar(20),
        "status": _varchar(20),
        "window_started_at": _timestamp(),
        "window_ends_at": _timestamp(),
        "first_event_at": _timestamp(),
        "last_event_at": _timestamp(),
        "event_count": _plain("integer"),
        "acknowledged_at": _timestamp("YES"),
        "acknowledged_by_user_id": _varchar(36, "YES"),
        "suppressed_at": _timestamp("YES"),
        "suppressed_until": _timestamp("YES"),
        "suppress_reason": _varchar(30, "YES"),
        "suppressed_by_user_id": _varchar(36, "YES"),
        "escalated_at": _timestamp("YES"),
        "escalation_reason": _varchar(30, "YES"),
        "escalated_by_user_id": _varchar(36, "YES"),
        "revision": _plain("integer"),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
    },
    EVENTS: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "alert_id": _varchar(36),
        "rule_id": _varchar(36),
        "meeting_id": _varchar(36),
        "source_type": _varchar(30),
        "source_id": _varchar(36),
        "condition_ref": _varchar(80),
        "occurred_at": _timestamp(),
        "created_at": _timestamp(),
    },
    DELIVERIES: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "alert_id": _varchar(36, "YES"),
        "purpose": _varchar(20),
        "channel": _varchar(20),
        "subject_ref": _varchar(80),
        "recipient_user_id": _varchar(36, "YES"),
        "status": _varchar(20),
        "reason": _varchar(40, "YES"),
        "attempt_count": _plain("integer"),
        "next_attempt_at": _timestamp("YES"),
        "last_attempt_at": _timestamp("YES"),
        "sent_at": _timestamp("YES"),
        "notification_id": _varchar(36, "YES"),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
    },
    PREFERENCES: {
        "id": _varchar(36),
        "workspace_id": _varchar(36),
        "user_id": _varchar(36),
        "digest_enabled": _plain("boolean"),
        "email_enabled": _plain("boolean"),
        "muted_alert_rule_ids": _plain("json"),
        "created_at": _timestamp(),
        "updated_at": _timestamp(),
    },
}


def _foreign_key(table: str, column: str, target: str, action: str) -> tuple[str, str]:
    return (
        f"{table}_{column}_fkey",
        f"FOREIGN KEY ({column}) REFERENCES {target}(id) ON DELETE {action}",
    )


def _composite_key(
    name: str, table: str, columns: str, target: str, target_columns: str
) -> tuple[str, str]:
    return (
        name,
        f"FOREIGN KEY ({columns}) REFERENCES {target}({target_columns}) ON DELETE CASCADE",
    )


EXPECTED_CONSTRAINTS = {
    RULES_TABLE: dict(
        [
            (f"{RULES_TABLE}_pkey", "PRIMARY KEY (id)"),
            _foreign_key(RULES_TABLE, "workspace_id", "workspaces", "CASCADE"),
            *(
                _foreign_key(RULES_TABLE, column, "users", "SET NULL")
                for column in RULE_USER_COLUMNS
            ),
            ("uq_alert_rules_scope", "UNIQUE (id, workspace_id)"),
            ("uq_alert_rules_name", "UNIQUE (workspace_id, name)"),
        ]
    ),
    ALERTS: dict(
        [
            (f"{ALERTS}_pkey", "PRIMARY KEY (id)"),
            _foreign_key(ALERTS, "workspace_id", "workspaces", "CASCADE"),
            *(_foreign_key(ALERTS, column, "users", "SET NULL") for column in ALERT_USER_COLUMNS),
            _composite_key(
                "fk_alerts_rule",
                ALERTS,
                "rule_id, workspace_id",
                RULES_TABLE,
                "id, workspace_id",
            ),
            ("uq_alerts_scope", "UNIQUE (id, workspace_id)"),
            ("uq_alerts_window", "UNIQUE (rule_id, dedupe_group, window_started_at)"),
        ]
    ),
    EVENTS: dict(
        [
            (f"{EVENTS}_pkey", "PRIMARY KEY (id)"),
            _foreign_key(EVENTS, "workspace_id", "workspaces", "CASCADE"),
            _foreign_key(EVENTS, "meeting_id", "meetings", "CASCADE"),
            _composite_key(
                "fk_alert_events_alert",
                EVENTS,
                "alert_id, workspace_id",
                ALERTS,
                "id, workspace_id",
            ),
            _composite_key(
                "fk_alert_events_rule",
                EVENTS,
                "rule_id, workspace_id",
                RULES_TABLE,
                "id, workspace_id",
            ),
            ("uq_alert_events_condition", "UNIQUE (rule_id, meeting_id, condition_ref)"),
        ]
    ),
    DELIVERIES: dict(
        [
            (f"{DELIVERIES}_pkey", "PRIMARY KEY (id)"),
            _foreign_key(DELIVERIES, "workspace_id", "workspaces", "CASCADE"),
            _foreign_key(DELIVERIES, "recipient_user_id", "users", "SET NULL"),
            _foreign_key(DELIVERIES, "notification_id", "notifications", "SET NULL"),
            _composite_key(
                "fk_alert_deliveries_alert",
                DELIVERIES,
                "alert_id, workspace_id",
                ALERTS,
                "id, workspace_id",
            ),
            (
                "uq_alert_deliveries_target",
                "UNIQUE (workspace_id, purpose, subject_ref, recipient_user_id, channel)",
            ),
        ]
    ),
    PREFERENCES: dict(
        [
            (f"{PREFERENCES}_pkey", "PRIMARY KEY (id)"),
            _foreign_key(PREFERENCES, "workspace_id", "workspaces", "CASCADE"),
            _foreign_key(PREFERENCES, "user_id", "users", "CASCADE"),
            ("uq_notification_preferences_user", "UNIQUE (workspace_id, user_id)"),
        ]
    ),
}


def _index(table: str, name: str, columns: str, *, unique: bool = False) -> tuple[str, str]:
    kind = "UNIQUE INDEX" if unique else "INDEX"
    return name, f"CREATE {kind} {name} ON public.{table} USING btree ({columns})"


EXPECTED_INDEXES = {
    RULES_TABLE: dict(
        _index(RULES_TABLE, name, columns, unique=unique)
        for name, columns, unique in (
            (f"{RULES_TABLE}_pkey", "id", True),
            ("uq_alert_rules_scope", "id, workspace_id", True),
            ("uq_alert_rules_name", "workspace_id, name", True),
            ("ix_alert_rules_workspace_id", "workspace_id", False),
            ("ix_alert_rules_active", "workspace_id, enabled, condition_type", False),
            ("ix_alert_rules_created_by_user_id", "created_by_user_id", False),
            ("ix_alert_rules_updated_by_user_id", "updated_by_user_id", False),
        )
    ),
    ALERTS: dict(
        _index(ALERTS, name, columns, unique=unique)
        for name, columns, unique in (
            (f"{ALERTS}_pkey", "id", True),
            ("uq_alerts_scope", "id, workspace_id", True),
            ("uq_alerts_window", "rule_id, dedupe_group, window_started_at", True),
            ("ix_alerts_workspace_id", "workspace_id", False),
            ("ix_alerts_queue", "workspace_id, status, last_event_at, id", False),
            ("ix_alerts_rule_group", "rule_id, dedupe_group, window_ends_at", False),
            ("ix_alerts_subject_user_id", "subject_user_id", False),
            ("ix_alerts_acknowledged_by_user_id", "acknowledged_by_user_id", False),
            ("ix_alerts_suppressed_by_user_id", "suppressed_by_user_id", False),
            ("ix_alerts_escalated_by_user_id", "escalated_by_user_id", False),
        )
    ),
    EVENTS: dict(
        _index(EVENTS, name, columns, unique=unique)
        for name, columns, unique in (
            (f"{EVENTS}_pkey", "id", True),
            ("uq_alert_events_condition", "rule_id, meeting_id, condition_ref", True),
            ("ix_alert_events_workspace_id", "workspace_id", False),
            ("ix_alert_events_alert", "alert_id, occurred_at", False),
            ("ix_alert_events_workspace_meeting", "workspace_id, meeting_id", False),
            ("ix_alert_events_meeting_id", "meeting_id", False),
        )
    ),
    DELIVERIES: dict(
        _index(DELIVERIES, name, columns, unique=unique)
        for name, columns, unique in (
            (f"{DELIVERIES}_pkey", "id", True),
            (
                "uq_alert_deliveries_target",
                "workspace_id, purpose, subject_ref, recipient_user_id, channel",
                True,
            ),
            ("ix_alert_deliveries_workspace_id", "workspace_id", False),
            ("ix_alert_deliveries_due", "workspace_id, status, next_attempt_at", False),
            ("ix_alert_deliveries_alert_id", "alert_id", False),
            ("ix_alert_deliveries_recipient_user_id", "recipient_user_id", False),
            ("ix_alert_deliveries_notification_id", "notification_id", False),
        )
    ),
    PREFERENCES: dict(
        _index(PREFERENCES, name, columns, unique=unique)
        for name, columns, unique in (
            (f"{PREFERENCES}_pkey", "id", True),
            ("uq_notification_preferences_user", "workspace_id, user_id", True),
            ("ix_notification_preferences_workspace_id", "workspace_id", False),
            ("ix_notification_preferences_user_id", "user_id", False),
        )
    ),
}
EXPECTED_GRANTS = {
    RULES_TABLE: ["DELETE", "INSERT", "SELECT", "UPDATE"],
    ALERTS: ["DELETE", "INSERT", "SELECT", "UPDATE"],
    EVENTS: ["DELETE", "INSERT", "SELECT"],
    DELIVERIES: ["DELETE", "INSERT", "SELECT", "UPDATE"],
    PREFERENCES: ["DELETE", "INSERT", "SELECT", "UPDATE"],
}
EXPECTED_CHECKS = {
    RULES_TABLE: RULE_CHECKS,
    ALERTS: ALERT_CHECKS,
    EVENTS: EVENT_CHECKS,
    DELIVERIES: DELIVERY_CHECKS,
    PREFERENCES: {},
}


def _assert_head_shape(table: str) -> dict:
    shape = _shape(table)
    assert shape["columns"] == EXPECTED_COLUMNS[table], table
    constraints = dict(shape["constraints"])
    named_checks = {name for name in constraints if name.startswith("ck_")}
    assert named_checks == set(EXPECTED_CHECKS[table])
    for name in named_checks:
        constraints.pop(name)
    assert constraints == EXPECTED_CONSTRAINTS[table], table
    assert dict(shape["indexes"]) == EXPECTED_INDEXES[table], table
    ((name, policy),) = shape["policies"].items()
    permissive, roles, command, using, check = policy
    assert (name, permissive, roles, command) == (POLICY_NAME, "PERMISSIVE", "{public}", "ALL")
    assert using == check
    assert "homayar.workspace_id" in using
    assert "share" not in using and "service" not in using
    assert shape["rls"] == [(True, True)]
    assert shape["grants"] == EXPECTED_GRANTS[table], table
    return shape


@pytest.mark.postgres
def test_the_orm_schema_has_the_tables(client):
    _requires_postgres()
    for table in TABLES:
        _assert_head_shape(table)


@pytest.mark.postgres
def test_the_migration_creates_and_drops_only_the_five_tables(
    client, email_outbox, monkeypatch, settings
):
    _requires_postgres()
    from alembic import command

    from backend.config import reset_settings_cache

    tenant = _tenant(client, email_outbox, "migration")
    orm_shapes = {table: _assert_head_shape(table) for table in TABLES}
    _full_chain(tenant, "migration")
    counts = {
        table: _scalar(f"SELECT count(*) FROM {table}")  # noqa: S608
        for table in ("meetings", "users")
    }

    config = _alembic_at(monkeypatch, settings.database_url)
    try:
        command.stamp(config, "head")
        head_others = _other_tables()
        command.downgrade(config, BASE_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == BASE_REVISION
        for table in TABLES:
            assert _scalar("SELECT to_regclass(:t) IS NULL", t=f"public.{table}"), table
            assert _shape(table) == EMPTY_SHAPE, table
        assert _other_tables() == head_others
        for table, count in counts.items():
            assert _scalar(f"SELECT count(*) FROM {table}") == count, table  # noqa: S608

        command.upgrade(config, NEW_REVISION)
        assert _scalar("SELECT version_num FROM alembic_version") == NEW_REVISION
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
        assert _other_tables() == head_others

        rules_tenant = _tenant(client, email_outbox, "migration-rules")
        rules_other = _tenant(client, email_outbox, "migration-other")
        for rules in RULES:
            rules(rules_tenant, client, email_outbox, other=rules_other)

        command.downgrade(config, BASE_REVISION)
        assert _other_tables() == head_others
        command.upgrade(config, "head")
        assert {table: _shape(table) for table in TABLES} == orm_shapes
        assert all(_ids(table) == set() for table in TABLES)
    finally:
        reset_settings_cache()


def _write_rows(db, setup: dict) -> dict:
    rule = AlertRule(
        **_rule(
            setup,
            name=f"Rule {setup['key']}",
            created_by_user_id=None,
            updated_by_user_id=None,
        )
    )
    db.add(rule)
    db.flush()
    alert = Alert(**_alert({**setup, "rule_id": rule.id}, subject_user_id=None))
    db.add(alert)
    db.flush()
    event_values = _event(
        {**setup, "rule_id": rule.id, "alert_id": alert.id, "meeting_id": setup["meeting_id"]}
    )
    event = AlertEvent(**event_values)
    db.add(event)
    db.flush()
    delivery_values = _delivery(
        {**setup, "alert_id": alert.id, "recipient_id": None},
        recipient_user_id=None,
    )
    delivery = AlertDelivery(**delivery_values)
    db.add(delivery)
    db.flush()
    preference = NotificationPreference(**_preference(setup, user_id=setup["user_id"]))
    db.add(preference)
    db.flush()
    return setup | {
        "rule_id": rule.id,
        "alert_id": alert.id,
        "event_id": event.id,
        "delivery_id": delivery.id,
        "preference_id": preference.id,
    }


@pytest.mark.postgres
def test_a_session_reads_and_writes_only_its_own_workspace(client, email_outbox):
    _requires_postgres()
    first = {
        **_tenant(client, email_outbox, "tenant-a"),
        "meeting_id": None,
        "key": "tenant_a",
    }
    second = {
        **_tenant(client, email_outbox, "tenant-b"),
        "meeting_id": None,
        "key": "tenant_b",
    }
    first["meeting_id"] = _meeting(first, "tenant_a")
    second["meeting_id"] = _meeting(second, "tenant_b")
    factory = get_session_factory()

    with factory() as db:
        assert db.scalar(text("SELECT current_user")) == APP_ROLE
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        first = _write_rows(db, first)
        db.commit()

    with factory() as db:
        bind_tenant_context(db, workspace_id=second["workspace_id"])
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608
        second = _write_rows(db, second)
        db.commit()

    with factory() as db:
        bind_tenant_context(db, workspace_id=first["workspace_id"])
        for table in TABLES:
            visible = db.execute(text(f"SELECT workspace_id FROM {table}")).scalars()  # noqa: S608
            assert list(visible) == [first["workspace_id"]], table
    cross = {
        AlertRule: _rule(second, name="Rule crossed"),
        Alert: _alert(second, dedupe_group="crossed|g"),
        AlertEvent: _event(second),
        AlertDelivery: _delivery(second),
        NotificationPreference: _preference(second),
    }
    for model, values in cross.items():
        with factory() as db:
            bind_tenant_context(db, workspace_id=first["workspace_id"])
            db.add(model(**values))
            with pytest.raises(DBAPIError, match="row-level security"):
                db.flush()
            db.rollback()

    with factory() as db:
        for table in TABLES:
            assert db.scalar(text(f"SELECT count(*) FROM {table}")) == 0  # noqa: S608


@pytest.mark.postgres
def test_a_share_token_does_not_reach_the_rows(client, email_outbox):
    _requires_postgres()
    setup = _full_chain(_tenant(client, email_outbox, "share"), "share")
    _add(NotificationPreference, _preference(setup))
    token_hash = "cd" * 32
    with inspection_session_factory()() as db:
        db.add(
            ShareLink(
                workspace_id=setup["workspace_id"],
                meeting_id=setup["meeting_id"],
                created_by_user_id=setup["user_id"],
                token_hash=token_hash,
                audience=ShareAudience.LINK,
                expires_at=datetime.now(UTC) + timedelta(days=1),
            )
        )
        db.commit()
    with get_session_factory()() as db:
        bind_capability_context(db, share_token_hash=token_hash)
        assert db.scalars(select(Meeting.id)).all() == [setup["meeting_id"]]
        assert db.scalars(select(AlertRule.id)).all() == []
        assert db.scalars(select(Alert.id)).all() == []
        assert db.scalars(select(AlertEvent.id)).all() == []
        assert db.scalars(select(AlertDelivery.id)).all() == []
        assert db.scalars(select(NotificationPreference.id)).all() == []
        db.rollback()


def _privileges(table: str) -> dict[str, bool]:
    return {
        privilege: _scalar(
            "SELECT has_table_privilege(:r, :t, :p)", r=APP_ROLE, t=table, p=privilege
        )
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE")
    }


def _apply_as_owner() -> None:
    from backend.database.rls import apply_row_level_security

    with inspection_session_factory()() as db:
        try:
            apply_row_level_security(db.connection())
        except Exception:
            db.rollback()
            raise
        db.commit()


@pytest.mark.postgres
def test_the_app_role_may_not_update_an_event(client, email_outbox):
    _requires_postgres()
    tenant = _tenant(client, email_outbox, "grants")
    full = {"SELECT": True, "INSERT": True, "UPDATE": True, "DELETE": True}
    no_update = {"SELECT": True, "INSERT": True, "UPDATE": False, "DELETE": True}
    assert _privileges(EVENTS) == no_update
    for table in (RULES_TABLE, ALERTS, DELIVERIES, PREFERENCES):
        assert _privileges(table) == full, table
    _apply_as_owner()
    assert _privileges(EVENTS) == no_update
    for table in (RULES_TABLE, ALERTS, DELIVERIES, PREFERENCES):
        assert _privileges(table) == full, table

    setup = {**tenant, "meeting_id": _meeting(tenant, "grants"), "key": "grants"}
    factory = get_session_factory()
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        setup = _write_rows(db, setup)
        db.commit()

    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        with pytest.raises(DBAPIError, match=f"permission denied for table {EVENTS}"):
            db.execute(
                text(f"UPDATE {EVENTS} SET source_id = :s WHERE id = :id"),  # noqa: S608
                {"s": "0" * 36, "id": setup["event_id"]},
            )
        db.rollback()

    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        updated = db.execute(
            text(
                f"UPDATE {ALERTS} SET status = 'acknowledged', acknowledged_at = :now, "  # noqa: S608
                "revision = revision + 1 "
                "WHERE id = :id AND workspace_id = :w AND revision = 1 AND status = 'open'"
            ),
            {"now": NOW, "id": setup["alert_id"], "w": tenant["workspace_id"]},
        )
        assert updated.rowcount == 1
        db.commit()

    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        for table, row_id in (
            (EVENTS, setup["event_id"]),
            (DELIVERIES, setup["delivery_id"]),
            (PREFERENCES, setup["preference_id"]),
            (ALERTS, setup["alert_id"]),
            (RULES_TABLE, setup["rule_id"]),
        ):
            deleted = db.execute(
                text(f"DELETE FROM {table} WHERE id = :id"),  # noqa: S608
                {"id": row_id},
            )
            assert deleted.rowcount == 1, table
        db.commit()
    for table in TABLES:
        assert _ids(table) == set(), table


# --- the conversation purge and the backup reconciliation -----------------------------------


def test_the_purge_deletes_the_alert_rows_first_and_counts_them(client, email_outbox):
    tenant = _tenant(client, email_outbox, "purge-order")
    rule_id = _add(AlertRule, _rule(tenant, name="Purge rule"))
    main_meeting = _meeting(tenant, "purge_main")
    plain_meeting = _meeting(tenant, "purge_plain")
    other_meeting = _meeting(tenant, "purge_other")
    shared_id = _add(
        Alert,
        _alert({**tenant, "rule_id": rule_id}, dedupe_group="purge|shared"),
    )
    main_id = _add(
        Alert,
        _alert({**tenant, "rule_id": rule_id}, dedupe_group="purge|main"),
    )
    _add(
        AlertEvent,
        _event({**tenant, "rule_id": rule_id, "alert_id": shared_id, "meeting_id": main_meeting}),
    )
    _add(
        AlertEvent,
        _event(
            {**tenant, "rule_id": rule_id, "alert_id": shared_id, "meeting_id": other_meeting},
            condition_ref="closing",
        ),
    )
    _add(
        AlertEvent,
        _event(
            {**tenant, "rule_id": rule_id, "alert_id": main_id, "meeting_id": main_meeting},
            condition_ref="closing",
        ),
    )
    _add(AlertDelivery, _delivery({**tenant, "alert_id": shared_id}))
    _add(
        AlertDelivery,
        _delivery(
            {**tenant, "alert_id": main_id},
            channel="email",
            status="pending",
            sent_at=None,
        ),
    )
    _add(AlertDelivery, _digest_delivery(tenant))
    _add(NotificationPreference, _preference(tenant))
    statements: list[str] = []

    def record(_connection, _cursor, statement, *_args) -> None:
        statements.append(statement)

    factory = get_session_factory()
    removed = {}
    with factory() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        engine = db.get_bind()
        event.listen(engine, "before_cursor_execute", record)
        try:
            for key, meeting_id in (("main", main_meeting), ("plain", plain_meeting)):
                meeting = db.get(Meeting, meeting_id)
                removed[key] = _remove_content(
                    db, workspace_id=tenant["workspace_id"], meeting=meeting
                )
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)

    deletes = [s.split()[2] for s in statements if s.lstrip().upper().startswith("DELETE FROM")]
    assert deletes.index(EVENTS) < deletes.index(DELIVERIES)
    assert deletes.index(DELIVERIES) < deletes.index(ALERTS)
    for table in ("action_items", "review_items", "transcript_revisions"):
        assert deletes.index(EVENTS) < deletes.index(table), table
    assert removed["main"] == removed["plain"] + 4
    assert _ids(EVENTS, meeting_id=main_meeting) == set()
    assert _ids(EVENTS, meeting_id=other_meeting) != set()
    assert _ids(ALERTS) == {shared_id}
    assert _ids(DELIVERIES, alert_id=main_id) == set()
    assert len(_ids(DELIVERIES, alert_id=shared_id)) == 1
    assert _ids(DELIVERIES, purpose="digest") != set()
    assert _ids(RULES_TABLE) == {rule_id}
    assert _ids(PREFERENCES) != set()


def _alerted_conversation(tenant: dict, key: str, meeting_id: str) -> dict:
    setup = _chain({**tenant, "meeting_id": meeting_id}, key)
    _add(AlertEvent, _event(setup))
    _add(AlertDelivery, _delivery(setup))
    return setup


def test_the_conversation_purge_removes_the_rows_of_its_conversation_only(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "alerts-purge")
    queued, kept, plain = [
        _processed(client, tenant, settings, key=f"alerts-purge-{index:04d}", index=index)
        for index in range(3)
    ]
    for meeting_id, key in ((queued, "alerts_purged"), (kept, "alerts_kept")):
        _alerted_conversation(tenant, key, meeting_id)
    assert len(_ids(EVENTS, meeting_id=queued)) == 1

    for meeting_id in (queued, plain):
        _delete_conversation(client, tenant, meeting_id)
        assert _purge(meeting_id).state is PurgeState.COMPLETED
    assert _ids(EVENTS, meeting_id=queued) == set()
    assert _ids(ALERTS, rule_id=_ids(RULES_TABLE, name="Rule alerts_purged").pop()) == set()
    assert len(_ids(EVENTS, meeting_id=kept)) == 1
    kept_alert = _ids(ALERTS, dedupe_group="alerts_kept|greeting").pop()
    assert len(_ids(DELIVERIES, alert_id=kept_alert)) == 1
    assert _ids(DELIVERIES, purpose="digest") == set()
    assert _purge(queued).content_rows_removed == _purge(plain).content_rows_removed + 3


def test_a_held_conversation_keeps_its_alerts_until_the_release(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _ledger_tenant(client, email_outbox, "alerts-hold")
    meeting_id = _processed(client, tenant, settings, key="alerts-hold-0001")
    setup = _alerted_conversation(tenant, "alerts_held", meeting_id)
    held = client.post(
        f"/api/v1/meetings/{meeting_id}/legal-hold",
        json={"reason": "litigation 2026-11", "review_at": _review(30)},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert held.status_code == 200, held.text

    _delete_conversation(client, tenant, meeting_id)
    purge = _purge(meeting_id)
    assert purge.state is PurgeState.PENDING and purge.last_error == ERROR_LEGAL_HOLD
    assert len(_ids(EVENTS, meeting_id=meeting_id)) == 1
    assert len(_ids(ALERTS, id=setup["alert_id"])) == 1
    deferred = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(days=1))
    assert deferred["purges_deferred_hold"] == 1, deferred
    assert len(_ids(EVENTS, meeting_id=meeting_id)) == 1

    for media in _media(meeting_id):
        released = client.post(
            f"/api/v1/media/{media.id}/legal-hold/release",
            json={"reason": "matter closed"},
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert released.status_code == 200, released.text
    final = _sweep(settings, tenant["workspace_id"], now=datetime.now(UTC) + timedelta(seconds=1))
    assert final["purges_completed"] == 1, final
    assert _ids(EVENTS, meeting_id=meeting_id) == set()
    assert _ids(ALERTS, id=setup["alert_id"]) == set()
    assert _ids(DELIVERIES, alert_id=setup["alert_id"]) == set()


def test_the_backup_reconciliation_counts_the_events_only():
    assert EVENTS in RECONCILED_TABLES
    for table in (RULES_TABLE, ALERTS, DELIVERIES, PREFERENCES):
        assert table not in RECONCILED_TABLES, table
    columns = Base.metadata.tables[EVENTS].c
    assert "meeting_id" in columns and "workspace_id" in columns
    assert RECONCILED_TABLES.index("action_proposals") < RECONCILED_TABLES.index(EVENTS)


# --- the ADR --------------------------------------------------------------------------------


def _adr() -> str:
    from backend.observability.release import PROJECT_ROOT

    path = (
        PROJECT_ROOT.parent
        / "docs"
        / "architecture"
        / "decisions"
        / "0085-product-alert-rules-and-digest.md"
    )
    assert path.exists(), f"no {path}"
    return path.read_text(encoding="utf-8")


def test_the_adr_is_named_and_built_like_the_others():
    content = _adr()
    assert content.startswith("# ADR 0085: ")
    assert "Proposed (HOM-134, WP24-T02, F59)" in content
    assert "2026-11-14" in content
    assert "Builds on: ADR 0057, ADR 0062, ADR 0065, ADR 0082" in content
    for heading in ("Context", "Decision", "Consequences", "Alternatives", "Not built"):
        assert re.search(rf"^## {heading}", content, re.MULTILINE), heading
    for section in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9", "D10"):
        assert re.search(rf"^### {section}\.", content, re.MULTILINE), section


def test_the_adr_uses_no_em_dash_and_no_double_hyphen_dash():
    content = _adr()
    assert "—" not in content
    assert not re.search(r"(^|\s)--(\s|$)", content, re.MULTILINE)


ADR_TOPICS = {
    "rules": (RULES_TABLE,),
    "alerts": (ALERTS,),
    "events": (EVENTS,),
    "deliveries": (DELIVERIES,),
    "preferences": (PREFERENCES,),
    "statuses": ("`escalated`",),
    "final_states": ("Acknowledged and suppressed are final",),
    "window": ("dedup_window_minutes",),
    "dedupe_group": ("dedupe_group",),
    "once_per_condition": ("uq_alert_events_condition",),
    "window_backstop": ("uq_alerts_window",),
    "inherited": ("`inherited`",),
    "no_update": ("NO_UPDATE_TABLES",),
    "delivery_reasons": ("`email_send_failed`",),
    "digest_disabled": ("`digest_disabled`",),
    "muting": ("muted_alert_rule_ids",),
    "recipients": ("recipient_user_ids",),
    "agent_never_recipient": ("never a recipient", "is never a recipient"),
    "record_access": ("resolve_record_access",),
    "permissions": ("alert:read",),
    "triage": ("alert:triage",),
    "module": ('require_module("quality")',),
    "session_only": ("API keys", "API key"),
    "expected_revision": ("expected_revision",),
    "routes": ("/api/v1/alert-rules",),
    "audit": ("alert.acknowledged",),
    "notification_kinds": ("alert.opened",),
    "data_ids_only": ("never meeting text",),
    "sweep": ("product_alerts",),
    "digest_hour": ("alert_digest_hour_utc",),
    "lock": ("FOR UPDATE",),
    "escalation_auto": ("`unacknowledged`",),
    "savepoint": ("savepoint",),
    "sources": ("reviewer_outcomes",),
    "rls": ("row-level security",),
    "purge": ("purge",),
    "backup": ("reconcil",),
}


@pytest.mark.parametrize("spellings", ADR_TOPICS.values(), ids=ADR_TOPICS.keys())
def test_the_adr_records_the_design(spellings):
    content = _adr()
    assert any(spelling in content for spelling in spellings), spellings
