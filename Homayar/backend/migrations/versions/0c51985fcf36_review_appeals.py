"""Agent appeals of review results, schema only (HOM-76, WP14-T05)

Revision ID: 0c51985fcf36
Revises: 02f616cbb655
Create Date: 2026-11-12

Adds the three tables of the agent appeal (F46, second clause): the agent of a call
files one appeal against a completed review item that has a human result
(``review_appeals``), on 1 to 20 criteria of the reviewed result
(``review_appeal_criteria``); a decider who is not a party to the call upholds or
changes each appealed criterion; when at least one criterion changed, the machine
result is scored again with the human outcomes plus the changes and stored once
(``review_appeal_results``).  The machine result and the human result are never
written.  Nothing writes the tables yet; the service, the routes, the audit events
and the notifications follow in PR 2.

* The frozen lists live in ``backend/database/saas_models.py``:
  ``REVIEW_APPEAL_STATUSES``, ``REVIEW_APPEAL_DISPOSITIONS``,
  ``REVIEW_APPEAL_CRITERION_DECISIONS``, ``REVIEW_APPEAL_MAX_CRITERIA`` and
  ``REVIEW_APPEAL_WINDOW_DAYS``.  The appealed outcome is one of the four machine
  outcomes, the changed outcome one of ``REVIEW_REVIEWER_OUTCOMES``, the change
  reason one of ``REVIEW_OVERRIDE_REASONS``, and every note is at most
  ``REVIEW_NOTE_MAX_LENGTH`` characters.  The checks copy them as frozen strings, by
  constraint name.  They use no regex, so SQLite runs the same rules.  A check
  passes when its expression is NULL, so each one is false, never NULL, for a row it
  refuses.  No check names a user column: ``appellant_user_id``,
  ``original_reviewer_user_id``, ``assignee_user_id``, ``assigned_by_user_id`` and
  ``decided_by_user_id`` are SET NULL when a user is removed, and a check on them
  would make that removal fail.
* Why the composite keys: an appeal can only name the result its item reviewed
  (``fk_review_appeals_item`` on ``uq_review_items_result``), that result's own
  workspace and meeting (``fk_review_appeals_result`` on
  ``uq_scorecard_result_scope``), and an item that has a human result
  (``fk_review_appeals_review_result`` on ``uq_review_results_item``).  A criterion
  row and an appeal result can only name their appeal's item and result
  (``uq_review_appeals_scope``), with the workspace and the meeting of that result,
  and a criterion row can only name a criterion of that result.  A buggy service
  cannot cross items, results or workspaces.
* ``uq_review_appeals_item`` keeps one appeal per review item, ever;
  ``uq_review_appeal_criteria_criterion`` keeps one row per appeal and criterion;
  ``uq_review_appeal_results_appeal`` keeps one appeal result per appeal.
* All three tables are workspace tables under row-level security (ADR 0005): tenant
  only, never through a share token.  ``review_appeal_results`` is immutable (a
  stored score after an appeal never changes, like ``review_results``), so it lands
  in ``NO_UPDATE_TABLES``; the other two get the full application grants, because
  the service of PR 2 assigns, decides and withdraws the appeals and writes the
  criterion decisions.

No data changes: no existing table's columns change and no row is written here.  The
conversation purge (ADR 0021) and the backup reconciliation are code lists, updated
with this migration: the purge deletes the appeal results, the appeal criteria and
the appeals of the purged conversation before the review decisions, the human
results and the review items whose cascades would otherwise remove them uncounted,
and ``backup/reconcile.py`` counts the three tables.

Downgrade drops the three tables with their indexes; every appeal, appealed
criterion and appeal result is lost.  The policies and the grants belong to the
tables and go with them.  No other table's grants or policies change in either
direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "0c51985fcf36"
down_revision: str | Sequence[str] | None = "02f616cbb655"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEALS = "review_appeals"
CRITERIA = "review_appeal_criteria"
RESULTS = "review_appeal_results"

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.
CHECKS = {
    "ck_review_appeals_status": "status IN ('open','decided','withdrawn')",
    "ck_review_appeals_revision": "revision >= 1",
    "ck_review_appeals_reason_note": (
        "length(trim(reason_note)) > 0 AND length(reason_note) <= 2000"
    ),
    "ck_review_appeals_disposition": (
        "disposition IS NULL OR disposition IN ('upheld','partially_granted','granted')"
    ),
    "ck_review_appeals_disposition_note_length": (
        "disposition_note IS NULL OR length(disposition_note) <= 2000"
    ),
    "ck_review_appeals_open_fields": (
        "status <> 'open' OR (disposition IS NULL AND disposition_note IS NULL "
        "AND decided_at IS NULL AND withdrawn_at IS NULL)"
    ),
    "ck_review_appeals_decided_fields": (
        "status <> 'decided' OR (disposition IS NOT NULL AND decided_at IS NOT NULL "
        "AND withdrawn_at IS NULL AND disposition_note IS NOT NULL "
        "AND length(trim(disposition_note)) > 0)"
    ),
    "ck_review_appeals_withdrawn_fields": (
        "status <> 'withdrawn' OR (withdrawn_at IS NOT NULL AND decided_at IS NULL "
        "AND disposition IS NULL AND disposition_note IS NULL)"
    ),
}
CRITERION_CHECKS = {
    "ck_review_appeal_criteria_appealed_outcome": (
        "appealed_outcome IN ('passed','failed','not_applicable','insufficient_evidence')"
    ),
    "ck_review_appeal_criteria_decision": "decision IS NULL OR decision IN ('uphold','change')",
    "ck_review_appeal_criteria_decided_outcome": (
        "decided_outcome IS NULL OR decided_outcome IN ('passed','failed','not_applicable')"
    ),
    "ck_review_appeal_criteria_reason_code": (
        "reason_code IS NULL OR reason_code IN "
        "('evidence_missed','evidence_misread','transcript_error',"
        "'context_outside_call','policy_exception','other')"
    ),
    "ck_review_appeal_criteria_note_length": "note IS NULL OR length(note) <= 2000",
    "ck_review_appeal_criteria_undecided": (
        "decision IS NOT NULL OR (decided_outcome IS NULL AND reason_code IS NULL AND note IS NULL)"
    ),
    "ck_review_appeal_criteria_uphold": (
        "decision IS NULL OR decision <> 'uphold' OR "
        "(decided_outcome IS NULL AND reason_code IS NULL)"
    ),
    "ck_review_appeal_criteria_change": (
        "decision IS NULL OR decision <> 'change' OR (decided_outcome IS NOT NULL "
        "AND decided_outcome <> appealed_outcome AND reason_code IS NOT NULL "
        "AND note IS NOT NULL AND length(trim(note)) > 0)"
    ),
    "ck_review_appeal_criteria_criterion_key": (
        "length(criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
    ),
}
RESULT_CHECKS = {
    "ck_review_appeal_result_outcome": (
        "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
    ),
    "ck_review_appeal_result_reason": (
        "(outcome = 'passed' AND reason IS NULL) "
        "OR (outcome = 'failed' AND reason IS NOT NULL "
        "AND reason IN ('auto_fail', 'below_threshold')) "
        "OR (outcome = 'not_applicable' AND reason IS NOT NULL "
        "AND reason = 'no_applicable_criteria') "
        "OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
        "AND reason = 'evidence_missing')"
    ),
    "ck_review_appeal_result_threshold": "pass_threshold BETWEEN 1 AND 100",
    "ck_review_appeal_result_weights": (
        "earned_weight >= 0 AND failed_weight >= 0 AND insufficient_weight >= 0 "
        "AND applicable_weight = earned_weight + failed_weight + insufficient_weight "
        "AND applicable_weight <= 100"
    ),
    "ck_review_appeal_result_complete": "NOT complete OR insufficient_weight = 0",
}
APPEAL_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "appellant_user_id": ("users", "SET NULL"),
    "original_reviewer_user_id": ("users", "SET NULL"),
    "assignee_user_id": ("users", "SET NULL"),
    "assigned_by_user_id": ("users", "SET NULL"),
    "decided_by_user_id": ("users", "SET NULL"),
}
CONTENT_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
}
APPEAL_INDEXES = (
    ("ix_review_appeals_workspace_id", ("workspace_id",)),
    ("ix_review_appeals_scorecard_result_id", ("scorecard_result_id",)),
    ("ix_review_appeals_appellant_user_id", ("appellant_user_id",)),
    ("ix_review_appeals_assignee_user_id", ("assignee_user_id",)),
    ("ix_review_appeals_queue", ("workspace_id", "status", "created_at", "id")),
    ("ix_review_appeals_workspace_meeting", ("workspace_id", "meeting_id")),
)
CRITERION_INDEXES = (
    ("ix_review_appeal_criteria_scorecard_result_id", ("scorecard_result_id",)),
    ("ix_review_appeal_criteria_workspace_meeting", ("workspace_id", "meeting_id")),
)
RESULT_INDEXES = (
    ("ix_review_appeal_results_scorecard_result_id", ("scorecard_result_id",)),
    ("ix_review_appeal_results_workspace_meeting", ("workspace_id", "meeting_id")),
)
ITEM_KEY = ["review_item_id", "scorecard_result_id"]
ITEM_TARGET = ["review_items.id", "review_items.scorecard_result_id"]
RESULT_KEY = ["scorecard_result_id", "workspace_id", "meeting_id"]
RESULT_TARGET = [
    "scorecard_results.id",
    "scorecard_results.workspace_id",
    "scorecard_results.meeting_id",
]
APPEAL_KEY = ["appeal_id", "review_item_id", "scorecard_result_id"]
APPEAL_TARGET = [
    "review_appeals.id",
    "review_appeals.review_item_id",
    "review_appeals.scorecard_result_id",
]
CRITERION_KEY = ["scorecard_result_id", "criterion_id"]
CRITERION_TARGET = [
    "scorecard_criterion_results.result_id",
    "scorecard_criterion_results.criterion_id",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        APPEALS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("review_item_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("appellant_user_id", sa.String(length=36), nullable=True),
        sa.Column("original_reviewer_user_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason_note", sa.Text(), nullable=False),
        sa.Column("assignee_user_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition", sa.String(length=24), nullable=True),
        sa.Column("disposition_note", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in APPEAL_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            ITEM_KEY, ITEM_TARGET, ondelete="CASCADE", name="fk_review_appeals_item"
        ),
        sa.ForeignKeyConstraint(
            RESULT_KEY, RESULT_TARGET, ondelete="CASCADE", name="fk_review_appeals_result"
        ),
        sa.ForeignKeyConstraint(
            ["review_item_id"],
            ["review_results.review_item_id"],
            ondelete="CASCADE",
            name="fk_review_appeals_review_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_item_id", name="uq_review_appeals_item"),
        *(sa.CheckConstraint(check, name=name) for name, check in CHECKS.items()),
    )
    # First, so the composite foreign keys of the criterion rows and the appeal
    # results below find their target: an appeal with its item and result (no table
    # rebuild).
    op.create_index(
        "uq_review_appeals_scope",
        APPEALS,
        ["id", "review_item_id", "scorecard_result_id"],
        unique=True,
    )
    op.create_table(
        CRITERIA,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("appeal_id", sa.String(length=36), nullable=False),
        sa.Column("review_item_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("criterion_id", sa.String(length=64), nullable=False),
        sa.Column("appealed_outcome", sa.String(length=24), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("decided_outcome", sa.String(length=24), nullable=True),
        sa.Column("reason_code", sa.String(length=32), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in CONTENT_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            APPEAL_KEY, APPEAL_TARGET, ondelete="CASCADE", name="fk_review_appeal_criteria_appeal"
        ),
        sa.ForeignKeyConstraint(
            RESULT_KEY,
            RESULT_TARGET,
            ondelete="CASCADE",
            name="fk_review_appeal_criteria_result",
        ),
        sa.ForeignKeyConstraint(
            CRITERION_KEY,
            CRITERION_TARGET,
            ondelete="CASCADE",
            name="fk_review_appeal_criteria_criterion",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "appeal_id", "criterion_id", name="uq_review_appeal_criteria_criterion"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in CRITERION_CHECKS.items()),
    )
    op.create_table(
        RESULTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("appeal_id", sa.String(length=36), nullable=False),
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
            for column, (target, action) in CONTENT_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            APPEAL_KEY, APPEAL_TARGET, ondelete="CASCADE", name="fk_review_appeal_results_appeal"
        ),
        sa.ForeignKeyConstraint(
            RESULT_KEY,
            RESULT_TARGET,
            ondelete="CASCADE",
            name="fk_review_appeal_results_scorecard_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("appeal_id", name="uq_review_appeal_results_appeal"),
        *(sa.CheckConstraint(check, name=name) for name, check in RESULT_CHECKS.items()),
    )
    # The plain indexes of the three tables (the unique scope index above and the
    # unique constraints inside the tables already exist).
    with op.batch_alter_table(APPEALS, schema=None) as batch_op:
        for name, columns in APPEAL_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    with op.batch_alter_table(CRITERIA, schema=None) as batch_op:
        for name, columns in CRITERION_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    with op.batch_alter_table(RESULTS, schema=None) as batch_op:
        for name, columns in RESULT_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    # The tenant policies of the new tables and their grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every appeal, appealed criterion and appeal result is lost)."""
    # Children before parents: the criterion rows' and the appeal results' foreign
    # keys depend on the appeals' unique scope index, so those tables drop first and
    # the index goes with the appeals.  The policies and the grants belong to the
    # tables and go with them too.
    op.drop_table(RESULTS)
    op.drop_table(CRITERIA)
    op.drop_table(APPEALS)
