"""Stored scorecard results, schema only (HOM-56, WP11-T06)

Revision ID: 5067ea773c0b
Revises: 47676f445509
Create Date: 2026-10-30

Adds two tables, so a machine score is stored with everything needed to compute it
again and to explain it criterion by criterion (F41, WP11):

* ``scorecard_results``: one machine scoring of one conversation against one published
  scorecard version.  It names the version row and copies its digest
  (``definition_digest``), the calculation version (``scorecards_version``), the checks
  version, and the conversation type and direction used.  ``check_run`` is the input
  and ``input_digest`` its SHA-256; ``result`` is the engine output.  The outcome, the
  reason, the threshold and the four integer weights are copied into columns:
  ``applicable_weight`` is the score denominator and ``earned_weight`` the numerator.
  ``uq_scorecard_result_input`` keeps one row per version, conversation and input.
  ``transcript_revision_id`` is required: every result names the revision its evidence
  points into.
* ``scorecard_criterion_results``: one row per criterion of a stored result, in
  definition order, so later tickets can find the criteria that need a person.  It
  copies the result's ``meeting_id``.  ``uq_scorecard_criterion_result_position`` and
  ``uq_scorecard_criterion_result_criterion`` keep the position and the criterion id
  unique per result.

The checks copy the outcome and reason rules of ``backend/scorecards/README.md``.  They
use no regex, so SQLite runs the same rules.  A check passes when its expression is
NULL, so each comparison with a nullable column has an ``IS NOT NULL`` guard.
Indexes: one per foreign key of ``scorecard_results``,
``ix_scorecard_result_workspace_meeting`` for the list of a conversation's results,
``workspace_id`` of the criterion rows, and
``ix_scorecard_criterion_result_workspace_meeting`` for the purge and the backup
reconciliation.  The position unique constraint serves the ``result_id`` foreign key.

Why immutable: a stored score must never change silently (F41).  A new input or a new
scorecard version is a new row, and the ORM refuses to update or delete a stored row.
Why the conversation purge still removes them: they are conversation content derived
from a transcript, like ``analysis_extractions``.  The purge deletes them with bulk
statements as the application role, so the tables are not in ``APPEND_ONLY_TABLES``
and keep the default workspace-table grants.  Both are workspace tables under row-level
security (tenant only, never through a share token).  Cascades from a removed
workspace, conversation, scorecard version or transcript revision also remove the rows.

Why no score column: the rounded scores live in ``result`` and follow from the integer
weights, so a second copy could only disagree with them.

Why no NPS, churn or probability: a stored result holds only what the engine computes.
No column holds an NPS, a churn value, a probability or any other inferred number.  The
only numbers are the weights, the threshold and the positions.

No data changes: no existing table changes and no row is written here.

Downgrade drops the indexes and the two tables; every stored result and criterion row
is lost.  The policies and the grants belong to the tables and go with them.  No other
table's grants or policies change in either direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "5067ea773c0b"
down_revision: str | Sequence[str] | None = "47676f445509"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESULTS = "scorecard_results"
CRITERIA = "scorecard_criterion_results"

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.  The outcome check comes first in each table.
OUTCOME_CHECK = "outcome IN ('passed', 'failed', 'not_applicable', 'insufficient_evidence')"
RESULT_CHECKS = {
    "ck_scorecard_result_outcome": OUTCOME_CHECK,
    "ck_scorecard_result_reason": (
        "(outcome = 'passed' AND reason IS NULL) "
        "OR (outcome = 'failed' AND reason IS NOT NULL "
        "AND reason IN ('auto_fail', 'below_threshold')) "
        "OR (outcome = 'not_applicable' AND reason IS NOT NULL "
        "AND reason = 'no_applicable_criteria') "
        "OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
        "AND reason = 'evidence_missing')"
    ),
    "ck_scorecard_result_threshold": "pass_threshold BETWEEN 1 AND 100",
    "ck_scorecard_result_weights": (
        "earned_weight >= 0 AND failed_weight >= 0 AND insufficient_weight >= 0 "
        "AND applicable_weight = earned_weight + failed_weight + insufficient_weight "
        "AND applicable_weight <= 100"
    ),
    "ck_scorecard_result_complete": "NOT complete OR insufficient_weight = 0",
    "ck_scorecard_result_digests": ("length(definition_digest) = 64 AND length(input_digest) = 64"),
    "ck_scorecard_result_conversation_type": (
        "conversation_type IN ('call', 'meeting', 'voicemail', 'other')"
    ),
    "ck_scorecard_result_direction": (
        "direction IS NULL OR direction IN ('inbound', 'outbound', 'internal')"
    ),
}
CRITERION_CHECKS = {
    "ck_scorecard_criterion_result_outcome": OUTCOME_CHECK,
    "ck_scorecard_criterion_result_position": "position >= 0 AND position < 100",
    "ck_scorecard_criterion_result_weight": "weight BETWEEN 0 AND 100",
    "ck_scorecard_criterion_result_key": (
        "length(criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
    ),
    "ck_scorecard_criterion_result_decided_by": (
        "(outcome = 'insufficient_evidence' AND decided_by IS NULL) "
        "OR (outcome = 'not_applicable' AND decided_by IS NOT NULL AND decided_by = 'rule') "
        "OR (outcome IN ('passed', 'failed') AND decided_by IS NOT NULL "
        "AND decided_by IN ('rule', 'model'))"
    ),
    "ck_scorecard_criterion_result_reason": (
        "(outcome IN ('passed', 'failed') AND reason IS NULL) "
        "OR (outcome IN ('not_applicable', 'insufficient_evidence') AND reason IS NOT NULL)"
    ),
    "ck_scorecard_criterion_result_review": (
        "NOT review_required OR (outcome = 'insufficient_evidence' AND reason IS NOT NULL "
        "AND reason = 'review_required')"
    ),
    "ck_scorecard_criterion_result_check_outcome": (
        "check_outcome IS NULL "
        "OR check_outcome IN ('passed', 'failed', 'not_applicable', 'not_computable')"
    ),
}
RESULT_FOREIGN_KEYS = {
    "workspace_id": "workspaces",
    "meeting_id": "meetings",
    "scorecard_version_id": "scorecard_versions",
    "transcript_revision_id": "transcript_revisions",
}
WORKSPACE_MEETING_INDEX = "ix_scorecard_result_workspace_meeting"
CRITERION_WORKSPACE_MEETING_INDEX = "ix_scorecard_criterion_result_workspace_meeting"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        RESULTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_version_id", sa.String(length=36), nullable=False),
        sa.Column("transcript_revision_id", sa.String(length=36), nullable=False),
        sa.Column("scorecards_version", sa.String(length=40), nullable=False),
        sa.Column("checks_version", sa.String(length=40), nullable=False),
        sa.Column("definition_digest", sa.String(length=64), nullable=False),
        sa.Column("input_digest", sa.String(length=64), nullable=False),
        sa.Column("conversation_type", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=12), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("pass_threshold", sa.Integer(), nullable=False),
        sa.Column("applicable_weight", sa.Integer(), nullable=False),
        sa.Column("earned_weight", sa.Integer(), nullable=False),
        sa.Column("failed_weight", sa.Integer(), nullable=False),
        sa.Column("insufficient_weight", sa.Integer(), nullable=False),
        sa.Column("check_run", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete="CASCADE")
            for column, target in RESULT_FOREIGN_KEYS.items()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "scorecard_version_id", "meeting_id", "input_digest", name="uq_scorecard_result_input"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in RESULT_CHECKS.items()),
    )
    with op.batch_alter_table(RESULTS, schema=None) as batch_op:
        for column in RESULT_FOREIGN_KEYS:
            batch_op.create_index(batch_op.f(f"ix_{RESULTS}_{column}"), [column], unique=False)
        batch_op.create_index(WORKSPACE_MEETING_INDEX, ["workspace_id", "meeting_id"], unique=False)

    op.create_table(
        CRITERIA,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("result_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("criterion_id", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("auto_fail", sa.Boolean(), nullable=False),
        sa.Column("na_allowed", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=True),
        sa.Column("decided_by", sa.String(length=16), nullable=True),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("review_reasons", sa.JSON(), nullable=False),
        sa.Column("check_outcome", sa.String(length=24), nullable=True),
        sa.Column("check_reason", sa.String(length=40), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("candidate_evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["result_id"], [f"{RESULTS}.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("result_id", "position", name="uq_scorecard_criterion_result_position"),
        sa.UniqueConstraint(
            "result_id", "criterion_id", name="uq_scorecard_criterion_result_criterion"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in CRITERION_CHECKS.items()),
    )
    with op.batch_alter_table(CRITERIA, schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f(f"ix_{CRITERIA}_workspace_id"), ["workspace_id"], unique=False
        )
        batch_op.create_index(
            CRITERION_WORKSPACE_MEETING_INDEX, ["workspace_id", "meeting_id"], unique=False
        )
    # The tenant policy of the new tables and their grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every stored result and criterion row is lost)."""
    with op.batch_alter_table(CRITERIA, schema=None) as batch_op:
        batch_op.drop_index(CRITERION_WORKSPACE_MEETING_INDEX)
        batch_op.drop_index(batch_op.f(f"ix_{CRITERIA}_workspace_id"))
    op.drop_table(CRITERIA)
    with op.batch_alter_table(RESULTS, schema=None) as batch_op:
        batch_op.drop_index(WORKSPACE_MEETING_INDEX)
        for column in RESULT_FOREIGN_KEYS:
            batch_op.drop_index(batch_op.f(f"ix_{RESULTS}_{column}"))
    op.drop_table(RESULTS)
