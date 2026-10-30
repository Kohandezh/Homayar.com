"""CRM mapping tables, schema only (HOM-88, WP16-T04)

Revision ID: 565d2ccd4fca
Revises: 47676f445509
Create Date: 2026-10-30

Adds five workspace tables for the generic CRM mapping contract (F64).  A CRM or
ticket system (the "source system", for example ``odoo``) sends records such as "call
X belongs to customer C, ticket T, agent A, outcome WON".  Homayar links them to the
conversation by stable external ids, and sends every case it must not decide alone to
a human:

* ``integration_mapping_profiles``: per workspace and source system, how to read that
  system's records (field paths, outcome codes, agent ids, the late-record window);
* ``external_records``: one customer, ticket or agent, known only by its external id;
* ``integration_inbound_records``: the ledger of received records, one row per
  ``(workspace, source_system, event_id)``;
* ``conversation_external_links``: the current customer, ticket, agent or outcome of a
  conversation in one source system;
* ``integration_reconciliation_items``: the queue of cases a human resolves.

Why the raw payload is never stored: a CRM payload carries names, phone numbers and
notes.  The ledger keeps only ``payload_hash`` (to tell a replay from a conflict) and
``extracted`` (the mapped ids, the outcome code and the active flags).

Why external ids are unique per source system and record type: the identity of an
external record is ``(workspace, source_system, record_type, external_id)``.  The same
id in two source systems, or as a customer and as a ticket, is two records.  Records are
never merged across source systems, and nothing is matched by phone number.

Why the agent link does not touch ``meetings.agent_user_id``: that column drives record
scope (who may see a conversation).  A CRM must not change authorization, so the agent
link only stores the Homayar user it maps to.

The service scope: the delayed-record sweep (PR 2) must find pending records in every
workspace.  The ``integration_inbound_records`` policy also allows SELECT under the
``crm_reconciliation`` service scope, exactly like ``usage_reconciliation`` on
``entitlements``.  The scope is read-only: the WITH CHECK clause stays bound to the
workspace, so it cannot INSERT or UPDATE, and the application role holds no DELETE on
that table, so it cannot delete either.  It gives no other table.  Every change the
sweep makes binds the workspace read from the row.

All five tables are tenant-only under row-level security and never reachable through a
share token.  No existing table, column, constraint, policy or grant changes, and no row
is written.

Downgrade drops the five tables with their indexes, policies and grants.  Every profile,
external record, received record, link and reconciliation item is lost.  No other table
changes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "565d2ccd4fca"
down_revision: str | Sequence[str] | None = "47676f445509"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROFILES = "integration_mapping_profiles"
RECORDS = "external_records"
LEDGER = "integration_inbound_records"
LINKS = "conversation_external_links"
ITEMS = "integration_reconciliation_items"

# Frozen copies of the check constants in ``backend/database/saas_models.py`` at this
# revision.  Slugs: ``^[a-z][a-z0-9_.-]{0,59}$`` without a regex, so SQLite runs the
# same check.
SOURCE_SYSTEM_CHECK = (
    "length(source_system) BETWEEN 1 AND 60 "
    "AND trim(substr(source_system, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
    "AND trim(source_system, 'abcdefghijklmnopqrstuvwxyz0123456789_.-') = ''"
)
CONVERSATION_SOURCE_CHECK = (
    "length(conversation_source_system) BETWEEN 1 AND 60 "
    "AND trim(substr(conversation_source_system, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
    "AND trim(conversation_source_system, 'abcdefghijklmnopqrstuvwxyz0123456789_.-') = ''"
)
LATE_WINDOW_CHECK = "late_record_window_seconds BETWEEN 3600 AND 2592000"
RECORD_TYPE_CHECK = "record_type IN ('customer', 'ticket', 'agent')"
STATE_CHECK = "state IN ('applied', 'pending', 'reconciliation')"
PENDING_CHECK = "state <> 'pending' OR expires_at IS NOT NULL"
LINK_TYPE_CHECK = "link_type IN ('customer', 'ticket', 'agent', 'outcome')"
OUTCOME_CHECK = (
    "outcome IS NULL OR outcome IN "
    "('resolved', 'unresolved', 'escalated', 'follow_up', 'won', 'lost', 'other')"
)
SHAPE_CHECK = (
    "(link_type = 'outcome' AND external_record_id IS NULL "
    "AND outcome IS NOT NULL AND external_outcome_code IS NOT NULL) OR "
    "(link_type <> 'outcome' AND external_record_id IS NOT NULL "
    "AND outcome IS NULL AND external_outcome_code IS NULL)"
)
AGENT_CHECK = "agent_user_id IS NULL OR link_type = 'agent'"
ITEM_LINK_TYPE_CHECK = (
    "link_type IS NULL OR link_type IN ('customer', 'ticket', 'agent', 'outcome')"
)
REASON_CHECK = (
    "reason IN ('conversation_not_found', 'ambiguous_conversation', 'changed_id', "
    "'unknown_agent', 'unknown_outcome', 'inactive_record')"
)
STATUS_CHECK = "status IN ('open', 'resolved', 'dismissed')"
RESOLUTION_CHECK = "resolution IS NULL OR resolution IN ('link', 'keep_current', 'dismiss')"
CLOSED_CHECK = (
    "(status = 'open' AND resolution IS NULL AND resolved_at IS NULL) OR "
    "(status <> 'open' AND resolution IS NOT NULL AND resolved_at IS NOT NULL)"
)

# In creation order: a table comes after the tables it references.
TABLES = (PROFILES, RECORDS, LEDGER, LINKS, ITEMS)
# Indexes beyond the two tenancy indexes every table has.
EXTRA_INDEXES: dict[str, list[tuple[str, list[str]]]] = {
    LEDGER: [
        (f"ix_{LEDGER}_meeting_id", ["meeting_id"]),
        # The sweep's lookup of pending records by expiry.
        ("ix_inbound_records_pending", ["state", "expires_at"]),
    ],
    ITEMS: [
        (f"ix_{ITEMS}_inbound_record_id", ["inbound_record_id"]),
        ("ix_reconciliation_workspace_status_created", ["workspace_id", "status", "created_at"]),
    ],
}


def _indexes(table: str) -> list[tuple[str, list[str]]]:
    return [
        (f"ix_{table}_organization_id", ["organization_id"]),
        (f"ix_{table}_workspace_id", ["workspace_id"]),
        *EXTRA_INDEXES.get(table, []),
    ]


def _tenancy() -> list[sa.SchemaItem]:
    """``id``, ``organization_id`` and ``workspace_id`` as on ``ingestion_records``."""
    return [
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        PROFILES,
        *_tenancy(),
        sa.Column("source_system", sa.String(length=60), nullable=False),
        sa.Column("conversation_source_system", sa.String(length=60), nullable=False),
        sa.Column("field_map", sa.JSON(), nullable=False),
        sa.Column("outcome_map", sa.JSON(), nullable=False),
        sa.Column("agent_map", sa.JSON(), nullable=False),
        sa.Column("late_record_window_seconds", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("workspace_id", "source_system", name="uq_integration_profile_source"),
        sa.CheckConstraint(SOURCE_SYSTEM_CHECK, name="ck_integration_profile_source_system"),
        sa.CheckConstraint(
            CONVERSATION_SOURCE_CHECK, name="ck_integration_profile_conversation_source"
        ),
        sa.CheckConstraint(LATE_WINDOW_CHECK, name="ck_integration_profile_late_window"),
        sa.CheckConstraint("version >= 1", name="ck_integration_profile_version"),
    )
    op.create_table(
        RECORDS,
        *_tenancy(),
        sa.Column("source_system", sa.String(length=60), nullable=False),
        sa.Column("record_type", sa.String(length=16), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workspace_id",
            "source_system",
            "record_type",
            "external_id",
            name="uq_external_record_identity",
        ),
        sa.CheckConstraint(RECORD_TYPE_CHECK, name="ck_external_record_type"),
        sa.CheckConstraint("length(external_id) >= 1", name="ck_external_record_id"),
    )
    op.create_table(
        LEDGER,
        *_tenancy(),
        sa.Column("profile_id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=60), nullable=False),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("extracted", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("last_conflict_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("api_key_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], [f"{PROFILES}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "workspace_id", "source_system", "event_id", name="uq_inbound_record_event"
        ),
        sa.CheckConstraint("length(event_id) >= 1", name="ck_inbound_record_event_id"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_inbound_record_payload_hash"),
        sa.CheckConstraint(STATE_CHECK, name="ck_inbound_record_state"),
        sa.CheckConstraint(PENDING_CHECK, name="ck_inbound_record_pending_expiry"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_inbound_record_attempts"),
        sa.CheckConstraint("conflict_count >= 0", name="ck_inbound_record_conflicts"),
    )
    op.create_table(
        LINKS,
        *_tenancy(),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=60), nullable=False),
        sa.Column("link_type", sa.String(length=16), nullable=False),
        sa.Column("external_record_id", sa.String(length=36), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=True),
        sa.Column("external_outcome_code", sa.String(length=200), nullable=True),
        sa.Column("agent_user_id", sa.String(length=36), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("inbound_record_id", sa.String(length=36), nullable=True),
        sa.Column("linked_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["external_record_id"], [f"{RECORDS}.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["agent_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["inbound_record_id"], [f"{LEDGER}.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["linked_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "meeting_id", "source_system", "link_type", name="uq_conversation_external_link"
        ),
        sa.CheckConstraint(LINK_TYPE_CHECK, name="ck_conversation_link_type"),
        sa.CheckConstraint(OUTCOME_CHECK, name="ck_conversation_link_outcome"),
        sa.CheckConstraint(SHAPE_CHECK, name="ck_conversation_link_shape"),
        sa.CheckConstraint(AGENT_CHECK, name="ck_conversation_link_agent"),
    )
    op.create_table(
        ITEMS,
        *_tenancy(),
        sa.Column("source_system", sa.String(length=60), nullable=False),
        sa.Column("inbound_record_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=True),
        sa.Column("link_type", sa.String(length=16), nullable=True),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("current_value", sa.String(length=200), nullable=True),
        sa.Column("candidate_value", sa.String(length=200), nullable=True),
        sa.Column("candidate_meeting_ids", sa.JSON(), nullable=False),
        sa.Column("resolution", sa.String(length=16), nullable=True),
        sa.Column("resolved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["inbound_record_id"], [f"{LEDGER}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(ITEM_LINK_TYPE_CHECK, name="ck_reconciliation_link_type"),
        sa.CheckConstraint(REASON_CHECK, name="ck_reconciliation_reason"),
        sa.CheckConstraint(STATUS_CHECK, name="ck_reconciliation_status"),
        sa.CheckConstraint(RESOLUTION_CHECK, name="ck_reconciliation_resolution"),
        sa.CheckConstraint(CLOSED_CHECK, name="ck_reconciliation_closed"),
    )
    for table in TABLES:
        for name, columns in _indexes(table):
            op.create_index(name, table, columns, unique=False)
    # The tenant policies of the new tables, the ledger's read-only scope clause, the
    # grants, and the revoked DELETE on the ledger.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every row of the five tables is lost)."""
    for table in reversed(TABLES):
        for name, _columns in _indexes(table):
            op.drop_index(name, table_name=table)
        op.drop_table(table)
