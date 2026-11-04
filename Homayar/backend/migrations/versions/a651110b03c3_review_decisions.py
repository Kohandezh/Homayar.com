"""Criterion decisions and the human result, schema only (HOM-74, WP14-T03)

Revision ID: a651110b03c3
Revises: 484a6df74abf
Create Date: 2026-11-04

Adds the two tables of the criterion override (F44).  ``review_criterion_decisions``
holds one human decision on one criterion of the machine result under review, inside
one review item: an ``accept`` confirms the machine outcome, an ``override`` changes it
and needs a reason code from a frozen list and a non-blank note.  ``review_results``
holds the human score of one completed review: the machine result scored again by the
same engine with the reviewer outcomes, one row per completed item that has a
scorecard result.  Nothing writes the tables yet; the service and the routes follow
in PR 2.

* The frozen lists live in ``backend/database/saas_models.py``: ``REVIEW_DECISIONS``,
  ``REVIEW_REVIEWER_OUTCOMES`` (exactly the engine's ``REVIEWER_OUTCOMES``),
  ``REVIEW_OVERRIDE_REASONS`` and ``REVIEW_NOTE_MAX_LENGTH``.  The checks copy them as
  frozen strings, by constraint name.  They use no regex, so SQLite runs the same
  rules.  A check passes when its expression is NULL, so each one is false, never
  NULL, for a row it refuses.  No check names a user column: ``decided_by_user_id``
  is SET NULL when a user is removed, and a check on it would make that removal fail.
* Three composite foreign keys tie a decision to its item's result
  (``fk_review_criterion_decisions_item``, on the new ``uq_review_items_result`` of
  ``review_items``), to its result's own workspace and meeting
  (``fk_review_criterion_decisions_result``, on ``uq_scorecard_result_scope``) and to
  a criterion of that result (``fk_review_criterion_decisions_criterion``, on
  ``uq_scorecard_criterion_result_criterion``).  ``uq_review_criterion_decisions_criterion``
  keeps one current decision per criterion per item.  The human result carries the
  first two of those keys and ``uq_review_results_item`` keeps one row per item.
* ``review_results`` reuses the machine result's own outcome, reason, threshold,
  weights and completeness rules under its own constraint names, with the outcome
  check first, so SQLite names it when an unknown outcome also breaks the reason rule.

Why ``review_criterion_decisions`` is not immutable: the service of PR 2 upserts the
row on the item's compare-and-set, and the conversation purge deletes the rows.  So
both tables are workspace tables under row-level security (tenant only, never through
a share token) with the full application grants, and neither is in
``APPEND_ONLY_TABLES``, ``NO_DELETE_TABLES`` or (for the decisions) ``NO_UPDATE_TABLES``.
Why ``review_results`` is immutable, like the machine results: a stored human score
never changes, so it lands in ``NO_UPDATE_TABLES`` and an ORM guard refuses updates
and deletes; only the conversation purge removes rows, and it removes the decisions
and the human results before the review items and the scorecard results they name,
whose cascades would otherwise remove them uncounted.

No data changes: no existing table's columns change and no row is written here.  The
one change to an existing table is the new unique index ``uq_review_items_result`` on
``review_items (id, scorecard_result_id)``, created with ``create_index`` (no table
rebuild); it is the target of the composite foreign keys above, and a NULL
``scorecard_result_id`` stays fine because ``id`` alone is unique.

Downgrade drops both tables with their indexes and drops ``uq_review_items_result``.
Every decision and every human result is lost.  The policies and the grants belong to
the tables and go with them.  No other table's grants or policies change in either
direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "a651110b03c3"
down_revision: str | Sequence[str] | None = "484a6df74abf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DECISIONS = "review_criterion_decisions"
RESULTS = "review_results"
ITEMS = "review_items"
ITEM_INDEX = "uq_review_items_result"

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.
CHECKS = {
    "ck_review_criterion_decisions_decision": "decision IN ('accept','override')",
    "ck_review_criterion_decisions_machine_outcome": (
        "machine_outcome IN ('passed','failed','not_applicable','insufficient_evidence')"
    ),
    "ck_review_criterion_decisions_reviewer_outcome": (
        "reviewer_outcome IN ('passed','failed','not_applicable')"
    ),
    "ck_review_criterion_decisions_reason_code": (
        "reason_code IS NULL OR reason_code IN "
        "('evidence_missed','evidence_misread','transcript_error',"
        "'context_outside_call','policy_exception','other')"
    ),
    "ck_review_criterion_decisions_note_length": "note IS NULL OR length(note) <= 2000",
    "ck_review_criterion_decisions_accept": (
        "decision <> 'accept' OR (reviewer_outcome = machine_outcome AND reason_code IS NULL)"
    ),
    "ck_review_criterion_decisions_override": (
        "decision <> 'override' OR (reviewer_outcome <> machine_outcome "
        "AND reason_code IS NOT NULL AND note IS NOT NULL AND length(trim(note)) > 0)"
    ),
    "ck_review_criterion_decisions_criterion_key": (
        "length(criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
    ),
}
RESULT_CHECKS = {
    "ck_review_result_outcome": (
        "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
    ),
    "ck_review_result_reason": (
        "(outcome = 'passed' AND reason IS NULL) "
        "OR (outcome = 'failed' AND reason IS NOT NULL "
        "AND reason IN ('auto_fail', 'below_threshold')) "
        "OR (outcome = 'not_applicable' AND reason IS NOT NULL "
        "AND reason = 'no_applicable_criteria') "
        "OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
        "AND reason = 'evidence_missing')"
    ),
    "ck_review_result_threshold": "pass_threshold BETWEEN 1 AND 100",
    "ck_review_result_weights": (
        "earned_weight >= 0 AND failed_weight >= 0 AND insufficient_weight >= 0 "
        "AND applicable_weight = earned_weight + failed_weight + insufficient_weight "
        "AND applicable_weight <= 100"
    ),
    "ck_review_result_complete": "NOT complete OR insufficient_weight = 0",
}
DECISION_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "decided_by_user_id": ("users", "SET NULL"),
}
RESULT_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
}
DECISION_INDEXES = (
    ("ix_review_criterion_decisions_scorecard_result_id", ("scorecard_result_id",)),
    ("ix_review_criterion_decisions_decided_by_user_id", ("decided_by_user_id",)),
)
RESULT_INDEXES = (("ix_review_results_scorecard_result_id", ("scorecard_result_id",)),)
SCOPE_KEY = ["scorecard_result_id", "workspace_id", "meeting_id"]
SCOPE_TARGET = [
    "scorecard_results.id",
    "scorecard_results.workspace_id",
    "scorecard_results.meeting_id",
]
ITEM_KEY = ["review_item_id", "scorecard_result_id"]
ITEM_TARGET = ["review_items.id", "review_items.scorecard_result_id"]


def upgrade() -> None:
    """Upgrade schema."""
    # First, so the composite foreign keys below find their target: an item and the
    # result it is reviewing (no table rebuild).
    op.create_index(ITEM_INDEX, ITEMS, ["id", "scorecard_result_id"], unique=True)
    op.create_table(
        DECISIONS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("review_item_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("criterion_id", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("machine_outcome", sa.String(length=24), nullable=False),
        sa.Column("reviewer_outcome", sa.String(length=24), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in DECISION_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            ITEM_KEY, ITEM_TARGET, ondelete="CASCADE", name="fk_review_criterion_decisions_item"
        ),
        sa.ForeignKeyConstraint(
            SCOPE_KEY,
            SCOPE_TARGET,
            ondelete="CASCADE",
            name="fk_review_criterion_decisions_result",
        ),
        sa.ForeignKeyConstraint(
            ["scorecard_result_id", "criterion_id"],
            ["scorecard_criterion_results.result_id", "scorecard_criterion_results.criterion_id"],
            ondelete="CASCADE",
            name="fk_review_criterion_decisions_criterion",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "review_item_id", "criterion_id", name="uq_review_criterion_decisions_criterion"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in CHECKS.items()),
    )
    op.create_table(
        RESULTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("review_item_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("scorecards_version", sa.String(length=40), nullable=False),
        sa.Column("reviewer_outcomes", sa.JSON(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("pass_threshold", sa.Integer(), nullable=False),
        sa.Column("applicable_weight", sa.Integer(), nullable=False),
        sa.Column("earned_weight", sa.Integer(), nullable=False),
        sa.Column("failed_weight", sa.Integer(), nullable=False),
        sa.Column("insufficient_weight", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in RESULT_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            ITEM_KEY, ITEM_TARGET, ondelete="CASCADE", name="fk_review_results_item"
        ),
        sa.ForeignKeyConstraint(
            SCOPE_KEY,
            SCOPE_TARGET,
            ondelete="CASCADE",
            name="fk_review_results_scorecard_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_item_id", name="uq_review_results_item"),
        *(sa.CheckConstraint(check, name=name) for name, check in RESULT_CHECKS.items()),
    )
    with op.batch_alter_table(DECISIONS, schema=None) as batch_op:
        for name, columns in DECISION_INDEXES:
            batch_op.create_index(name, columns, unique=False)
        batch_op.create_index(
            "ix_review_criterion_decisions_workspace_meeting",
            ["workspace_id", "meeting_id"],
            unique=False,
        )
    with op.batch_alter_table(RESULTS, schema=None) as batch_op:
        for name, columns in RESULT_INDEXES:
            batch_op.create_index(name, columns, unique=False)
        batch_op.create_index(
            "ix_review_results_workspace_meeting",
            ["workspace_id", "meeting_id"],
            unique=False,
        )
    # The tenant policies of the new tables and their grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every decision and human result is lost)."""
    with op.batch_alter_table(RESULTS, schema=None) as batch_op:
        batch_op.drop_index("ix_review_results_workspace_meeting")
        for name, _columns in RESULT_INDEXES:
            batch_op.drop_index(name)
    with op.batch_alter_table(DECISIONS, schema=None) as batch_op:
        batch_op.drop_index("ix_review_criterion_decisions_workspace_meeting")
        for name, _columns in DECISION_INDEXES:
            batch_op.drop_index(name)
    op.drop_table(RESULTS)
    op.drop_table(DECISIONS)
    # Last: on PostgreSQL the dropped tables' foreign keys depended on it.
    op.drop_index(ITEM_INDEX, table_name=ITEMS)
