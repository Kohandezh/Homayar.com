"""Action proposals and approved delivery requests, schema only (HOM-135, WP24-T03)

Revision ID: fc9730c5f039
Revises: d3d1c7aa3cd8
Create Date: 2026-11-14

Adds the two tables of approval-bound outbound actions (F60, ADR 0082).
``action_proposals``: one proposal previews one exact payload (a CRM update, a ticket
or a coaching task) built from one source (an action item or a review finding).  It
pins the transcript revision the source was read from and a digest of the source
fields, and it stores the payload, its hash and its version.  A person approves,
rejects or revokes it.  ``action_delivery_requests``: the immutable copy of an
approved payload that HOM-136 delivers.  Nothing writes the tables yet; the service,
the routes, the permissions and the audit events follow in PR 2.

* The frozen lists live in ``backend/database/saas_models.py``:
  ``ACTION_PROPOSAL_TYPES``, ``ACTION_PROPOSAL_SOURCES``,
  ``ACTION_PROPOSAL_STATUSES``, ``ACTION_PROPOSAL_OPEN_STATUSES``,
  ``ACTION_PROPOSAL_REJECT_REASONS`` and ``ACTION_PROPOSAL_REVOKE_REASONS``.  The
  checks copy them as frozen strings, by constraint name.  They use no regex, so
  SQLite runs the same rules.  A check passes when its expression is NULL, so each
  one is false, never NULL, for a row it refuses.  Each cross-field rule passes when
  a value that another check owns is outside its list, so a row that breaks one field
  fails only that field's own check.  No check names a user column: the user columns
  are SET NULL when a user is removed, and a check on them would make that removal
  fail.
* Why the keys: ``uq_action_proposals_scope`` (``id``, ``workspace_id``,
  ``meeting_id``) is the target of ``fk_action_delivery_requests_proposal``, so a
  request stays in its proposal's own workspace and meeting.  One request per proposal
  (``uq_action_delivery_requests_proposal``) and per idempotency key.  The source
  keys are single-column and SET NULL: a removed action item or review item leaves the
  proposal with a NULL id, and PR 2 treats it as stale.  The pinned transcript
  revision is CASCADE.  A named external record is RESTRICT.  The request's source
  columns are plain strings, no keys: the copy outlives its source.
* ``uq_action_proposals_open_item`` and ``uq_action_proposals_open_finding`` are
  partial unique indexes over the open statuses (``pending``, ``approved``): at most
  one open proposal per action item and action type, and per finding.  A rejected or
  revoked one does not block a new one.
* Both tables are workspace tables under row-level security (ADR 0005): tenant only,
  never through a share token.  ``action_proposals`` gets the full application grants,
  because the service of PR 2 changes its status and revision.
  ``action_delivery_requests`` is in ``NO_UPDATE_TABLES``: SELECT, INSERT and DELETE,
  never UPDATE, because an approved request never changes.

No data changes: no existing table's columns change and no row is written here.  The
conversation purge (ADR 0021) and the backup reconciliation are code lists, updated
with this migration: the purge deletes the delivery requests and then the proposals
of the purged conversation before the action items, the review items and the
transcript revisions whose cascades would otherwise remove them uncounted, and
``backup/reconcile.py`` counts both tables.

Downgrade drops both tables with their indexes (the requests first); every proposal
and request is lost.  The policies and the grants belong to the tables and go with
them.  No other table's grants or policies change in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "fc9730c5f039"
down_revision: str | Sequence[str] | None = "d3d1c7aa3cd8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROPOSALS = "action_proposals"
REQUESTS = "action_delivery_requests"

_TYPE_SOURCE = (
    "NOT ((action_type IN ('crm_update','ticket_create') AND source_type = 'review_finding') "
    "OR (action_type = 'coaching_task' AND source_type = 'action_item'))"
)
_TARGET_SHAPE = (
    "(action_type <> 'crm_update' OR (target_system IS NOT NULL "
    "AND target_external_record_id IS NOT NULL)) "
    "AND (action_type <> 'ticket_create' OR (target_system IS NOT NULL "
    "AND target_external_record_id IS NULL)) "
    "AND (action_type <> 'coaching_task' OR (target_system IS NULL "
    "AND target_external_record_id IS NULL))"
)

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.
PROPOSAL_CHECKS = {
    "ck_action_proposals_action_type": (
        "action_type IN ('crm_update','ticket_create','coaching_task')"
    ),
    "ck_action_proposals_source_type": "source_type IN ('action_item','review_finding')",
    "ck_action_proposals_status": "status IN ('pending','approved','rejected','revoked')",
    "ck_action_proposals_revision": "revision >= 1",
    "ck_action_proposals_payload_version": "payload_version >= 1",
    "ck_action_proposals_payload_hash": (
        "length(payload_hash) = 64 AND trim(payload_hash, '0123456789abcdef') = ''"
    ),
    "ck_action_proposals_source_digest": (
        "length(source_digest) = 64 AND trim(source_digest, '0123456789abcdef') = ''"
    ),
    "ck_action_proposals_criterion_key": (
        "criterion_id IS NULL OR (length(criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = '')"
    ),
    "ck_action_proposals_target_system": (
        "target_system IS NULL OR (length(target_system) BETWEEN 1 AND 60 "
        "AND trim(substr(target_system, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(target_system, 'abcdefghijklmnopqrstuvwxyz0123456789_.-') = '')"
    ),
    "ck_action_proposals_source_shape": (
        "(source_type <> 'action_item' OR (review_item_id IS NULL AND criterion_id IS NULL)) "
        "AND (source_type <> 'review_finding' OR (action_item_id IS NULL "
        "AND criterion_id IS NOT NULL))"
    ),
    "ck_action_proposals_type_source": _TYPE_SOURCE,
    "ck_action_proposals_target_shape": _TARGET_SHAPE,
    "ck_action_proposals_reject_reason": (
        "reject_reason IS NULL OR reject_reason IN ('not_needed','incorrect','duplicate','other')"
    ),
    "ck_action_proposals_revoke_reason": (
        "revoke_reason IS NULL OR revoke_reason IN ('approved_in_error','no_longer_needed','other')"
    ),
    "ck_action_proposals_pending_fields": (
        "status <> 'pending' OR (decided_at IS NULL AND reject_reason IS NULL "
        "AND revoked_at IS NULL AND revoke_reason IS NULL)"
    ),
    "ck_action_proposals_approved_fields": (
        "status <> 'approved' OR (decided_at IS NOT NULL AND reject_reason IS NULL "
        "AND revoked_at IS NULL AND revoke_reason IS NULL)"
    ),
    "ck_action_proposals_rejected_fields": (
        "status <> 'rejected' OR (decided_at IS NOT NULL AND reject_reason IS NOT NULL "
        "AND revoked_at IS NULL AND revoke_reason IS NULL)"
    ),
    "ck_action_proposals_revoked_fields": (
        "status <> 'revoked' OR (decided_at IS NOT NULL AND revoked_at IS NOT NULL "
        "AND revoke_reason IS NOT NULL AND reject_reason IS NULL)"
    ),
}
REQUEST_CHECKS = {
    "ck_action_delivery_requests_action_type": (
        "action_type IN ('crm_update','ticket_create','coaching_task')"
    ),
    "ck_action_delivery_requests_source_type": "source_type IN ('action_item','review_finding')",
    "ck_action_delivery_requests_payload_version": "payload_version >= 1",
    "ck_action_delivery_requests_payload_hash": (
        "length(payload_hash) = 64 AND trim(payload_hash, '0123456789abcdef') = ''"
    ),
    "ck_action_delivery_requests_source_digest": (
        "length(source_digest) = 64 AND trim(source_digest, '0123456789abcdef') = ''"
    ),
    "ck_action_delivery_requests_idempotency_key": (
        "length(idempotency_key) = 64 AND trim(idempotency_key, '0123456789abcdef') = ''"
    ),
    "ck_action_delivery_requests_criterion_key": (
        "source_criterion_id IS NULL OR (length(source_criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(source_criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(source_criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = '')"
    ),
    "ck_action_delivery_requests_type_source": _TYPE_SOURCE,
    "ck_action_delivery_requests_target_shape": _TARGET_SHAPE,
}
# The open statuses, as in ``ACTION_PROPOSAL_OPEN_ITEM_WHERE`` and
# ``ACTION_PROPOSAL_OPEN_FINDING_WHERE``.
OPEN_ITEM_WHERE = sa.text("status IN ('pending','approved') AND action_item_id IS NOT NULL")
OPEN_FINDING_WHERE = sa.text("status IN ('pending','approved') AND review_item_id IS NOT NULL")
PROPOSAL_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "action_item_id": ("action_items", "SET NULL"),
    "review_item_id": ("review_items", "SET NULL"),
    "source_transcript_revision_id": ("transcript_revisions", "CASCADE"),
    "target_external_record_id": ("external_records", "RESTRICT"),
    "created_by_user_id": ("users", "SET NULL"),
    "decided_by_user_id": ("users", "SET NULL"),
    "revoked_by_user_id": ("users", "SET NULL"),
}
PROPOSAL_INDEXES = (
    ("ix_action_proposals_workspace_id", ("workspace_id",)),
    ("ix_action_proposals_action_item_id", ("action_item_id",)),
    ("ix_action_proposals_review_item_id", ("review_item_id",)),
    ("ix_action_proposals_source_transcript_revision_id", ("source_transcript_revision_id",)),
    ("ix_action_proposals_target_external_record_id", ("target_external_record_id",)),
    ("ix_action_proposals_queue", ("workspace_id", "status", "created_at", "id")),
    ("ix_action_proposals_workspace_meeting", ("workspace_id", "meeting_id")),
)
REQUEST_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "target_external_record_id": ("external_records", "RESTRICT"),
    "approved_by_user_id": ("users", "SET NULL"),
}
REQUEST_INDEXES = (
    ("ix_action_delivery_requests_workspace_id", ("workspace_id",)),
    ("ix_action_delivery_requests_target_external_record_id", ("target_external_record_id",)),
    ("ix_action_delivery_requests_workspace_meeting", ("workspace_id", "meeting_id")),
    ("ix_action_delivery_requests_approved", ("workspace_id", "approved_at", "id")),
)
SCOPE = ["id", "workspace_id", "meeting_id"]
PROPOSAL_KEY = ["proposal_id", "workspace_id", "meeting_id"]
PROPOSAL_TARGET = [f"{PROPOSALS}.id", f"{PROPOSALS}.workspace_id", f"{PROPOSALS}.meeting_id"]


def _foreign_keys(keys: dict[str, tuple[str, str]]) -> list[sa.ForeignKeyConstraint]:
    return [
        sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
        for column, (target, action) in keys.items()
    ]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        PROPOSALS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("action_item_id", sa.String(length=36), nullable=True),
        sa.Column("review_item_id", sa.String(length=36), nullable=True),
        sa.Column("criterion_id", sa.String(length=64), nullable=True),
        sa.Column("source_transcript_revision_id", sa.String(length=36), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("target_system", sa.String(length=60), nullable=True),
        sa.Column("target_external_record_id", sa.String(length=36), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reject_reason", sa.String(length=30), nullable=True),
        sa.Column("revoke_reason", sa.String(length=30), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_foreign_keys(PROPOSAL_FOREIGN_KEYS),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(*SCOPE, name="uq_action_proposals_scope"),
        *(sa.CheckConstraint(check, name=name) for name, check in PROPOSAL_CHECKS.items()),
    )
    with op.batch_alter_table(PROPOSALS, schema=None) as batch_op:
        for name, columns in PROPOSAL_INDEXES:
            batch_op.create_index(name, columns, unique=False)
        batch_op.create_index(
            "uq_action_proposals_open_item",
            ["action_item_id", "action_type"],
            unique=True,
            postgresql_where=OPEN_ITEM_WHERE,
            sqlite_where=OPEN_ITEM_WHERE,
        )
        batch_op.create_index(
            "uq_action_proposals_open_finding",
            ["review_item_id", "criterion_id"],
            unique=True,
            postgresql_where=OPEN_FINDING_WHERE,
            sqlite_where=OPEN_FINDING_WHERE,
        )

    op.create_table(
        REQUESTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("proposal_id", sa.String(length=36), nullable=False),
        sa.Column("action_type", sa.String(length=20), nullable=False),
        sa.Column("target_system", sa.String(length=60), nullable=True),
        sa.Column("target_external_record_id", sa.String(length=36), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_version", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("source_criterion_id", sa.String(length=64), nullable=True),
        sa.Column("source_transcript_revision_id", sa.String(length=36), nullable=False),
        sa.Column("source_digest", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("approved_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_foreign_keys(REQUEST_FOREIGN_KEYS),
        sa.ForeignKeyConstraint(
            PROPOSAL_KEY,
            PROPOSAL_TARGET,
            ondelete="CASCADE",
            name="fk_action_delivery_requests_proposal",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", name="uq_action_delivery_requests_proposal"),
        sa.UniqueConstraint("idempotency_key", name="uq_action_delivery_requests_idempotency_key"),
        sa.UniqueConstraint(*SCOPE, name="uq_action_delivery_requests_scope"),
        *(sa.CheckConstraint(check, name=name) for name, check in REQUEST_CHECKS.items()),
    )
    with op.batch_alter_table(REQUESTS, schema=None) as batch_op:
        for name, columns in REQUEST_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    # The tenant policies of the new tables and their grants; the requests lose UPDATE
    # (``NO_UPDATE_TABLES``).
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every proposal and every delivery request is lost)."""
    # The requests first: they name the proposals.  The indexes, the policies and the
    # grants belong to the tables and go with them.
    op.drop_table(REQUESTS)
    op.drop_table(PROPOSALS)
