"""Blinded calibration batches, schema only (HOM-75, WP14-T04)

Revision ID: 02f616cbb655
Revises: a651110b03c3
Create Date: 2026-11-10

Adds the five tables of the blinded calibration exercise (F46): a review admin
builds a batch of 1 to 50 examples (each one machine scorecard result of one
conversation) and 1 to 20 participants (reviewers); each participant gives their own
outcome on every reviewable criterion of every example (an answer); an admin who is
not a participant records the correct outcome (an adjudication); closing the batch
unlocks the agreement report.  Nothing writes the tables yet; the service, the routes
and the report follow in PR 2.

* The frozen lists live in ``backend/database/saas_models.py``:
  ``CALIBRATION_BATCH_STATUSES``, ``CALIBRATION_OUTCOMES`` (exactly the engine's
  ``REVIEWER_OUTCOMES``, the same words as HOM-74's reviewer outcomes),
  ``CALIBRATION_TITLE_MAX_LENGTH``, ``CALIBRATION_MAX_EXAMPLES`` and
  ``CALIBRATION_MAX_PARTICIPANTS``.  The checks copy them as frozen strings, by
  constraint name.  They use no regex, so SQLite runs the same rules.  A check passes
  when its expression is NULL, so each one is false, never NULL, for a row it refuses.
  No check names a user column: ``created_by_user_id``, ``closed_by_user_id``,
  ``cancelled_by_user_id``, ``user_id`` and ``decided_by_user_id`` are SET NULL when a
  user is removed, and a check on them would make that removal fail.
* Why the composite keys: an example can only name its own batch in its workspace
  (``fk_calibration_examples_batch`` on ``uq_calibration_batches_scope``) and its
  result's own scope (``fk_calibration_examples_scorecard_result`` on
  ``uq_scorecard_result_scope``); an answer or an adjudication can only name a
  criterion of the result its example holds, in the batch it belongs to
  (``uq_calibration_examples_scope``), with the workspace and the meeting of that
  result, and an answer can only name a participant of its own batch
  (``uq_calibration_participants_scope``).  A buggy service cannot cross batches,
  results or workspaces.
* ``uq_calibration_examples_meeting`` keeps one conversation once per batch and
  ``uq_calibration_examples_position`` keeps the admin's order (0 to 49);
  ``uq_calibration_participants_user`` keeps one participant once per batch (a
  removed user leaves the row with a NULL user id, and NULLs stay distinct);
  ``uq_calibration_answers_criterion`` and ``uq_calibration_adjudications_criterion``
  keep one current row per participant, example and criterion and per example and
  criterion; the answers and the adjudications of PR 2 are upserts until close.
* All five tables are workspace tables under row-level security (ADR 0005): tenant
  only, never through a share token.  ``calibration_examples`` is immutable (the
  batch's example list is fixed at creation), so it lands in ``NO_UPDATE_TABLES``
  like the stored results; the other four get the full application grants, because
  the service of PR 2 updates the batches and upserts the answers and the
  adjudications.

No data changes: no existing table's columns change and no row is written here.  The
conversation purge (ADR 0021) and the backup reconciliation are code lists, updated
with this migration: the purge deletes the answers, the adjudications and the
examples of the purged conversation before the review decisions and the scorecard
results whose cascades would otherwise remove them uncounted, and
``backup/reconcile.py`` counts the three content tables (batches and participants
have no conversation of their own).

Downgrade drops the five tables with their indexes; every batch, example,
participant, answer and adjudication is lost.  The policies and the grants belong to
the tables and go with them.  No other table's grants or policies change in either
direction.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "02f616cbb655"
down_revision: str | Sequence[str] | None = "a651110b03c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BATCHES = "calibration_batches"
EXAMPLES = "calibration_examples"
PARTICIPANTS = "calibration_participants"
ANSWERS = "calibration_answers"
ADJUDICATIONS = "calibration_adjudications"

# Frozen copies of the check strings in ``backend/database/saas_models.py`` at this
# revision, by constraint name.
CHECKS = {
    "ck_calibration_batches_status": "status IN ('open','closed','cancelled')",
    "ck_calibration_batches_revision": "revision >= 1",
    "ck_calibration_batches_title": "length(trim(title)) > 0 AND length(title) <= 200",
    "ck_calibration_batches_open_fields": (
        "status <> 'open' OR (closed_at IS NULL AND cancelled_at IS NULL)"
    ),
    "ck_calibration_batches_closed_fields": (
        "status <> 'closed' OR (closed_at IS NOT NULL AND cancelled_at IS NULL)"
    ),
    "ck_calibration_batches_cancelled_fields": (
        "status <> 'cancelled' OR (cancelled_at IS NOT NULL AND closed_at IS NULL)"
    ),
}
EXAMPLE_CHECKS = {
    "ck_calibration_examples_position": "position >= 0 AND position < 50",
}
ANSWER_CHECKS = {
    "ck_calibration_answers_outcome": "outcome IN ('passed','failed','not_applicable')",
    "ck_calibration_answers_criterion_key": (
        "length(criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
    ),
}
ADJUDICATION_CHECKS = {
    "ck_calibration_adjudications_outcome": "outcome IN ('passed','failed','not_applicable')",
    "ck_calibration_adjudications_criterion_key": (
        "length(criterion_id) BETWEEN 2 AND 64 "
        "AND trim(substr(criterion_id, 1, 1), 'abcdefghijklmnopqrstuvwxyz') = '' "
        "AND trim(criterion_id, 'abcdefghijklmnopqrstuvwxyz0123456789_') = ''"
    ),
    "ck_calibration_adjudications_note_length": "note IS NULL OR length(note) <= 2000",
}
BATCH_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "created_by_user_id": ("users", "SET NULL"),
    "closed_by_user_id": ("users", "SET NULL"),
    "cancelled_by_user_id": ("users", "SET NULL"),
}
EXAMPLE_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
}
PARTICIPANT_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "user_id": ("users", "SET NULL"),
}
CONTENT_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
}
ADJUDICATION_FOREIGN_KEYS = {
    "workspace_id": ("workspaces", "CASCADE"),
    "meeting_id": ("meetings", "CASCADE"),
    "decided_by_user_id": ("users", "SET NULL"),
}
BATCH_INDEXES = (
    ("ix_calibration_batches_workspace_id", ("workspace_id",)),
    ("ix_calibration_batches_list", ("workspace_id", "created_at", "id")),
)
BATCH_UNIQUE_INDEXES = (("uq_calibration_batches_scope", ("id", "workspace_id")),)
EXAMPLE_INDEXES = (
    ("ix_calibration_examples_scorecard_result_id", ("scorecard_result_id",)),
    ("ix_calibration_examples_workspace_meeting", ("workspace_id", "meeting_id")),
)
EXAMPLE_UNIQUE_INDEXES = (
    ("uq_calibration_examples_scope", ("id", "batch_id", "scorecard_result_id")),
)
PARTICIPANT_INDEXES = (("ix_calibration_participants_user_id", ("user_id",)),)
PARTICIPANT_UNIQUE_INDEXES = (("uq_calibration_participants_scope", ("id", "batch_id")),)
ANSWER_INDEXES = (
    ("ix_calibration_answers_scorecard_result_id", ("scorecard_result_id",)),
    ("ix_calibration_answers_workspace_meeting", ("workspace_id", "meeting_id")),
)
ADJUDICATION_INDEXES = (
    ("ix_calibration_adjudications_scorecard_result_id", ("scorecard_result_id",)),
    ("ix_calibration_adjudications_decided_by_user_id", ("decided_by_user_id",)),
    ("ix_calibration_adjudications_workspace_meeting", ("workspace_id", "meeting_id")),
)
BATCH_KEY = ["batch_id", "workspace_id"]
BATCH_TARGET = ["calibration_batches.id", "calibration_batches.workspace_id"]
RESULT_KEY = ["scorecard_result_id", "workspace_id", "meeting_id"]
RESULT_TARGET = [
    "scorecard_results.id",
    "scorecard_results.workspace_id",
    "scorecard_results.meeting_id",
]
EXAMPLE_KEY = ["example_id", "batch_id", "scorecard_result_id"]
EXAMPLE_TARGET = [
    "calibration_examples.id",
    "calibration_examples.batch_id",
    "calibration_examples.scorecard_result_id",
]
PARTICIPANT_KEY = ["participant_id", "batch_id"]
PARTICIPANT_TARGET = ["calibration_participants.id", "calibration_participants.batch_id"]
CRITERION_KEY = ["scorecard_result_id", "criterion_id"]
CRITERION_TARGET = [
    "scorecard_criterion_results.result_id",
    "scorecard_criterion_results.criterion_id",
]


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        BATCHES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("closed_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("cancelled_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in BATCH_FOREIGN_KEYS.items()
        ),
        sa.PrimaryKeyConstraint("id"),
        *(sa.CheckConstraint(check, name=name) for name, check in CHECKS.items()),
    )
    # First, so the composite foreign keys of the examples and the participants below
    # find their target: a batch and its workspace (no table rebuild).
    op.create_index("uq_calibration_batches_scope", BATCHES, ["id", "workspace_id"], unique=True)
    op.create_table(
        EXAMPLES,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in EXAMPLE_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            BATCH_KEY, BATCH_TARGET, ondelete="CASCADE", name="fk_calibration_examples_batch"
        ),
        sa.ForeignKeyConstraint(
            RESULT_KEY,
            RESULT_TARGET,
            ondelete="CASCADE",
            name="fk_calibration_examples_scorecard_result",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "meeting_id", name="uq_calibration_examples_meeting"),
        sa.UniqueConstraint("batch_id", "position", name="uq_calibration_examples_position"),
        *(sa.CheckConstraint(check, name=name) for name, check in EXAMPLE_CHECKS.items()),
    )
    # Again first: the answers and the adjudications name an example's batch and
    # result together.
    op.create_index(
        "uq_calibration_examples_scope",
        EXAMPLES,
        ["id", "batch_id", "scorecard_result_id"],
        unique=True,
    )
    op.create_table(
        PARTICIPANTS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in PARTICIPANT_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            BATCH_KEY, BATCH_TARGET, ondelete="CASCADE", name="fk_calibration_participants_batch"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "user_id", name="uq_calibration_participants_user"),
    )
    # And again: an answer names a participant of its own batch.
    op.create_index(
        "uq_calibration_participants_scope", PARTICIPANTS, ["id", "batch_id"], unique=True
    )
    op.create_table(
        ANSWERS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("example_id", sa.String(length=36), nullable=False),
        sa.Column("participant_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("criterion_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in CONTENT_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            EXAMPLE_KEY, EXAMPLE_TARGET, ondelete="CASCADE", name="fk_calibration_answers_example"
        ),
        sa.ForeignKeyConstraint(
            PARTICIPANT_KEY,
            PARTICIPANT_TARGET,
            ondelete="CASCADE",
            name="fk_calibration_answers_participant",
        ),
        sa.ForeignKeyConstraint(
            RESULT_KEY, RESULT_TARGET, ondelete="CASCADE", name="fk_calibration_answers_result"
        ),
        sa.ForeignKeyConstraint(
            CRITERION_KEY,
            CRITERION_TARGET,
            ondelete="CASCADE",
            name="fk_calibration_answers_criterion",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "participant_id", "example_id", "criterion_id", name="uq_calibration_answers_criterion"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in ANSWER_CHECKS.items()),
    )
    op.create_table(
        ADJUDICATIONS,
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("example_id", sa.String(length=36), nullable=False),
        sa.Column("scorecard_result_id", sa.String(length=36), nullable=False),
        sa.Column("criterion_id", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *(
            sa.ForeignKeyConstraint([column], [f"{target}.id"], ondelete=action)
            for column, (target, action) in ADJUDICATION_FOREIGN_KEYS.items()
        ),
        sa.ForeignKeyConstraint(
            EXAMPLE_KEY,
            EXAMPLE_TARGET,
            ondelete="CASCADE",
            name="fk_calibration_adjudications_example",
        ),
        sa.ForeignKeyConstraint(
            RESULT_KEY,
            RESULT_TARGET,
            ondelete="CASCADE",
            name="fk_calibration_adjudications_result",
        ),
        sa.ForeignKeyConstraint(
            CRITERION_KEY,
            CRITERION_TARGET,
            ondelete="CASCADE",
            name="fk_calibration_adjudications_criterion",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "example_id", "criterion_id", name="uq_calibration_adjudications_criterion"
        ),
        *(sa.CheckConstraint(check, name=name) for name, check in ADJUDICATION_CHECKS.items()),
    )
    # The plain indexes of the five tables (the unique scope indexes above and the
    # unique constraints inside the tables already exist).
    with op.batch_alter_table(BATCHES, schema=None) as batch_op:
        for name, columns in BATCH_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    with op.batch_alter_table(EXAMPLES, schema=None) as batch_op:
        for name, columns in EXAMPLE_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    with op.batch_alter_table(PARTICIPANTS, schema=None) as batch_op:
        for name, columns in PARTICIPANT_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    with op.batch_alter_table(ANSWERS, schema=None) as batch_op:
        for name, columns in ANSWER_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    with op.batch_alter_table(ADJUDICATIONS, schema=None) as batch_op:
        for name, columns in ADJUDICATION_INDEXES:
            batch_op.create_index(name, columns, unique=False)
    # The tenant policies of the new tables and their grants.
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (every batch, example, participant, answer and adjudication
    is lost)."""
    # Children before parents, and the tables before any index of a surviving table
    # could matter: the answers' and the adjudications' foreign keys depend on the
    # examples' and the participants' unique indexes, so those tables drop first and
    # their indexes go with them.  The policies and the grants belong to the tables
    # and go with them too.
    op.drop_table(ADJUDICATIONS)
    op.drop_table(ANSWERS)
    op.drop_table(PARTICIPANTS)
    op.drop_table(EXAMPLES)
    op.drop_table(BATCHES)
