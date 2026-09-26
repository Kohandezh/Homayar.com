"""transcript revisions and analysis runs (WP04-T02, ADR 0014)

Revision ID: d4f8b2c6e9a1
Revises: c3e9a7b5d1f4
Create Date: 2026-09-10 21:30:00.000000

Adds ``transcript_revisions`` — immutable, numbered per meeting, exactly one
current row per meeting (partial unique index) — and ``analysis_runs`` — the
provenance of each analysis pass: the revision it read, provider, model,
prompt/schema digests, checkpoint and truthful stage state.
``transcript_segments`` gains ``revision_id`` and is unique per
(revision, sequence) instead of (meeting, sequence); ``meeting_summaries`` and
``action_items`` gain ``analysis_run_id``.  Both new tables join the shared
meeting-content row-level-security policy.

Upgrade is lossless: every meeting that already has segments receives one
BACKFILL revision (number 1, current) that adopts them, carrying the ASR
provider/model of its latest completed ASR stage run when one exists.

Downgrade is lossy by nature: segments of superseded revisions are deleted
(only the current revision fits the old unique constraint) and analysis
provenance is dropped.  It exists for the migration round-trip check, not as
an operational path.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "d4f8b2c6e9a1"
down_revision: str | Sequence[str] | None = "c3e9a7b5d1f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_WHERE = sa.text("is_current")
STAGE_STATES = (
    "QUEUED",
    "RUNNING",
    "COMPLETED",
    "PARTIAL",
    "FAILED",
    "UNAVAILABLE",
    "INSUFFICIENT_EVIDENCE",
    "SKIPPED",
)


def _backfill_revisions(bind: sa.Connection) -> None:
    """Adopt pre-revision segments into one current BACKFILL revision per meeting."""
    meetings = bind.execute(
        sa.text(
            "SELECT meeting_id, workspace_id, count(*), min(created_at) "
            "FROM transcript_segments GROUP BY meeting_id, workspace_id"
        )
    ).all()
    insert = sa.text(
        "INSERT INTO transcript_revisions (id, workspace_id, meeting_id, revision_number, "
        "parent_revision_id, source, reason, note, language, segment_count, created_at, "
        "created_by_user_id, job_id, attempt, checkpoint_id, config_hash, asr_provider, "
        "asr_model, diarization_provider, diarization_status, is_mock, is_current, "
        "superseded_at) VALUES (:id, :workspace_id, :meeting_id, 1, NULL, 'BACKFILL', "
        "'backfill', NULL, :language, :segment_count, :created_at, NULL, NULL, NULL, NULL, "
        "NULL, :asr_provider, :asr_model, NULL, NULL, :is_mock, :is_current, NULL)"
    ).bindparams(
        sa.bindparam("is_mock", type_=sa.Boolean()),
        sa.bindparam("is_current", type_=sa.Boolean()),
    )
    for meeting_id, workspace_id, segment_count, first_created in meetings:
        meeting = bind.execute(
            sa.text("SELECT language, created_at FROM meetings WHERE id = :meeting_id"),
            {"meeting_id": meeting_id},
        ).first()
        stage_run = bind.execute(
            sa.text(
                "SELECT provider, model, is_mock FROM processing_stage_runs "
                "WHERE meeting_id = :meeting_id AND stage = 'asr' AND state = 'COMPLETED' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            {"meeting_id": meeting_id},
        ).first()
        revision_id = str(uuid.uuid4())
        bind.execute(
            insert,
            {
                "id": revision_id,
                "workspace_id": workspace_id,
                "meeting_id": meeting_id,
                "language": ((meeting[0] if meeting else None) or "fa")[:12],
                "segment_count": int(segment_count),
                "created_at": first_created or (meeting[1] if meeting else None),
                "asr_provider": stage_run[0] if stage_run else None,
                "asr_model": stage_run[1] if stage_run else None,
                "is_mock": bool(stage_run[2]) if stage_run else False,
                "is_current": True,
            },
        )
        bind.execute(
            sa.text(
                "UPDATE transcript_segments SET revision_id = :revision_id "
                "WHERE meeting_id = :meeting_id AND revision_id IS NULL"
            ),
            {"revision_id": revision_id, "meeting_id": meeting_id},
        )


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "transcript_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(length=36), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "ASR",
                "CORRECTION",
                "BACKFILL",
                name="transcriptsource",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("language", sa.String(length=12), nullable=False),
        sa.Column("segment_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("checkpoint_id", sa.String(length=36), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=True),
        sa.Column("asr_provider", sa.String(length=128), nullable=True),
        sa.Column("asr_model", sa.String(length=256), nullable=True),
        sa.Column("diarization_provider", sa.String(length=128), nullable=True),
        sa.Column("diarization_status", sa.String(length=16), nullable=True),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("revision_number >= 1", name="ck_transcript_revision_number"),
        sa.CheckConstraint("segment_count >= 0", name="ck_transcript_revision_segments"),
        sa.CheckConstraint(
            "attempt IS NULL OR attempt >= 0", name="ck_transcript_revision_attempt"
        ),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["stage_checkpoints.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_revision_id"], ["transcript_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("meeting_id", "revision_number", name="uq_transcript_revision_number"),
    )
    with op.batch_alter_table("transcript_revisions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_transcript_revisions_meeting_id"), ["meeting_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_transcript_revisions_parent_revision_id"),
            ["parent_revision_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_transcript_revisions_workspace_id"), ["workspace_id"], unique=False
        )
        batch_op.create_index(
            "ix_transcript_revision_workspace_meeting",
            ["workspace_id", "meeting_id"],
            unique=False,
        )
        batch_op.create_index(
            "uq_transcript_revision_current",
            ["meeting_id"],
            unique=True,
            postgresql_where=CURRENT_WHERE,
            sqlite_where=CURRENT_WHERE,
        )

    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=False),
        sa.Column("transcript_revision_id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("checkpoint_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=40), nullable=False),
        sa.Column(
            "state",
            sa.Enum(*STAGE_STATES, name="stagestate", native_enum=False, length=24),
            nullable=False,
        ),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("provider", sa.String(length=128), nullable=True),
        sa.Column("model", sa.String(length=256), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=True),
        sa.Column("prompt_sha256", sa.String(length=64), nullable=True),
        sa.Column("output_schema_sha256", sa.String(length=64), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("is_mock", sa.Boolean(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt IS NULL OR attempt >= 0", name="ck_analysis_run_attempt"),
        sa.CheckConstraint("schema_version >= 1", name="ck_analysis_run_schema_version"),
        sa.ForeignKeyConstraint(["checkpoint_id"], ["stage_checkpoints.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["transcript_revision_id"], ["transcript_revisions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_analysis_runs_meeting_id"), ["meeting_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_analysis_runs_transcript_revision_id"),
            ["transcript_revision_id"],
            unique=False,
        )
        batch_op.create_index(
            batch_op.f("ix_analysis_runs_workspace_id"), ["workspace_id"], unique=False
        )
        batch_op.create_index(
            "ix_analysis_run_workspace_meeting", ["workspace_id", "meeting_id"], unique=False
        )
        batch_op.create_index(
            "uq_analysis_run_current",
            ["meeting_id"],
            unique=True,
            postgresql_where=CURRENT_WHERE,
            sqlite_where=CURRENT_WHERE,
        )

    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.add_column(sa.Column("revision_id", sa.String(length=36), nullable=True))

    _backfill_revisions(op.get_bind())

    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.alter_column("revision_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.drop_constraint("uq_segment_meeting_sequence", type_="unique")
        batch_op.create_unique_constraint(
            "uq_segment_revision_sequence", ["revision_id", "sequence"]
        )
        batch_op.create_index(
            batch_op.f("ix_transcript_segments_revision_id"), ["revision_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_transcript_segments_revision_id_transcript_revisions",
            "transcript_revisions",
            ["revision_id"],
            ["id"],
            ondelete="CASCADE",
        )

    for table in ("meeting_summaries", "action_items"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column("analysis_run_id", sa.String(length=36), nullable=True))
            batch_op.create_index(
                batch_op.f(f"ix_{table}_analysis_run_id"), ["analysis_run_id"], unique=False
            )
            batch_op.create_foreign_key(
                f"fk_{table}_analysis_run_id_analysis_runs",
                "analysis_runs",
                ["analysis_run_id"],
                ["id"],
                ondelete="SET NULL",
            )

    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (superseded revisions and analysis provenance are lost)."""
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM transcript_segments WHERE revision_id IN "
            "(SELECT id FROM transcript_revisions WHERE NOT is_current)"
        )
    )
    for table in ("action_items", "meeting_summaries"):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table}_analysis_run_id_analysis_runs", type_="foreignkey"
            )
            batch_op.drop_index(batch_op.f(f"ix_{table}_analysis_run_id"))
            batch_op.drop_column("analysis_run_id")

    with op.batch_alter_table("transcript_segments", schema=None) as batch_op:
        batch_op.drop_constraint(
            "fk_transcript_segments_revision_id_transcript_revisions", type_="foreignkey"
        )
        batch_op.drop_index(batch_op.f("ix_transcript_segments_revision_id"))
        batch_op.drop_constraint("uq_segment_revision_sequence", type_="unique")
        batch_op.create_unique_constraint("uq_segment_meeting_sequence", ["meeting_id", "sequence"])
        batch_op.drop_column("revision_id")

    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.drop_index("uq_analysis_run_current")
        batch_op.drop_index("ix_analysis_run_workspace_meeting")
        batch_op.drop_index(batch_op.f("ix_analysis_runs_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_analysis_runs_transcript_revision_id"))
        batch_op.drop_index(batch_op.f("ix_analysis_runs_meeting_id"))
    op.drop_table("analysis_runs")

    with op.batch_alter_table("transcript_revisions", schema=None) as batch_op:
        batch_op.drop_index("uq_transcript_revision_current")
        batch_op.drop_index("ix_transcript_revision_workspace_meeting")
        batch_op.drop_index(batch_op.f("ix_transcript_revisions_workspace_id"))
        batch_op.drop_index(batch_op.f("ix_transcript_revisions_parent_revision_id"))
        batch_op.drop_index(batch_op.f("ix_transcript_revisions_meeting_id"))
    op.drop_table("transcript_revisions")
