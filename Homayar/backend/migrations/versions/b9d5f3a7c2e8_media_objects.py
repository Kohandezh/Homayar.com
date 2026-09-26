"""media objects and their lifecycle (WP05-T03, ADR 0017)

Revision ID: b9d5f3a7c2e8
Revises: a8c4e2f6d1b9
Create Date: 2026-09-11 16:00:00.000000

Adds ``media_objects``: one row per stored byte stream of a conversation —
the ORIGINAL upload (at most one per conversation) and derived kinds that
name the object they were made from — with digest, size, type, duration,
storage location, tenant, provenance and lifecycle (state, retention,
deletion request, deletion, legal hold, integrity).  ``recordings`` gains
``media_object_id`` pointing at the ORIGINAL.  Joined to the tenant-only
row-level-security policy set.

Backfill: every existing recording gets one ORIGINAL row copied from its
columns (``producer = backfill``), linked to its conversation, to the
ingestion row of the conversation when one exists and to the earliest job;
a recording already soft-deleted becomes a ``DELETED`` row with the same
timestamp.  The storage backend recorded is the one configured at migration
time (the only backend those objects could have been written to by this
deployment).

Downgrade drops the column and the table: lifecycle, holds and integrity
results are lost (documented lossy).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.config import get_settings
from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "b9d5f3a7c2e8"
down_revision: str | Sequence[str] | None = "a8c4e2f6d1b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = ("ORIGINAL", "NORMALIZED", "ENHANCED", "CHANNEL_DERIVED", "CLIP", "EXPORT")
STATES = ("ACTIVE", "RETENTION_PENDING", "DELETION_PENDING", "DELETED", "LEGAL_HOLD")
INTEGRITY = ("UNVERIFIED", "VERIFIED", "MISSING", "CORRUPT")
ORIGINAL_WHERE = sa.text("kind = 'ORIGINAL'")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _backfill(bind: sa.Connection) -> None:
    backend = str(get_settings().storage_backend)
    rows = bind.execute(
        sa.text(
            "SELECT r.id, r.workspace_id, w.organization_id, r.meeting_id, r.storage_key, "
            "r.media_type, r.container_format, r.size_bytes, r.checksum_sha256, "
            "r.duration_seconds, r.channel_count, r.sample_rate_hz, r.original_filename, "
            "r.created_at, r.deleted_at, m.created_by_user_id, m.retention_expires_at "
            "FROM recordings r JOIN meetings m ON m.id = r.meeting_id "
            "JOIN workspaces w ON w.id = r.workspace_id ORDER BY r.created_at, r.id"
        )
    ).all()
    insert = sa.text(
        "INSERT INTO media_objects (id, organization_id, workspace_id, meeting_id, ingestion_id, "
        "job_id, derived_from_id, kind, storage_backend, storage_key, content_type, "
        "container_format, size_bytes, sha256, duration_seconds, channel_count, sample_rate_hz, "
        "original_filename, producer, created_at, created_by_user_id, lifecycle_state, "
        "retention_expires_at, deletion_requested_at, deletion_reason, deleted_at, legal_hold_at, "
        "legal_hold_reason, legal_hold_by_user_id, integrity, last_verified_at) VALUES (:id, "
        ":organization_id, :workspace_id, :meeting_id, :ingestion_id, :job_id, NULL, 'ORIGINAL', "
        ":backend, :storage_key, :content_type, :container_format, :size_bytes, :sha256, "
        ":duration_seconds, :channel_count, :sample_rate_hz, :original_filename, 'backfill', "
        ":created_at, :created_by_user_id, :state, :retention_expires_at, :deletion_requested_at, "
        ":deletion_reason, :deleted_at, NULL, NULL, NULL, 'UNVERIFIED', NULL)"
    )
    link = sa.text("UPDATE recordings SET media_object_id = :media_id WHERE id = :recording_id")
    for row in rows:
        (
            recording_id,
            workspace_id,
            organization_id,
            meeting_id,
            storage_key,
            media_type,
            container_format,
            size_bytes,
            checksum,
            duration_seconds,
            channel_count,
            sample_rate_hz,
            original_filename,
            created_at,
            deleted_at,
            created_by_user_id,
            retention_expires_at,
        ) = row
        ingestion_id = bind.execute(
            sa.text(
                "SELECT id FROM ingestion_records WHERE meeting_id = :meeting_id "
                "ORDER BY created_at ASC, id ASC LIMIT 1"
            ),
            {"meeting_id": meeting_id},
        ).scalar()
        job_id = bind.execute(
            sa.text(
                "SELECT id FROM processing_jobs WHERE meeting_id = :meeting_id "
                "ORDER BY created_at ASC, id ASC LIMIT 1"
            ),
            {"meeting_id": meeting_id},
        ).scalar()
        media_id = str(uuid.uuid4())
        bind.execute(
            insert,
            {
                "id": media_id,
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "meeting_id": meeting_id,
                "ingestion_id": ingestion_id,
                "job_id": job_id,
                "backend": backend,
                "storage_key": storage_key,
                "content_type": media_type,
                "container_format": container_format,
                "size_bytes": int(size_bytes),
                "sha256": str(checksum).lower(),
                "duration_seconds": duration_seconds,
                "channel_count": channel_count,
                "sample_rate_hz": sample_rate_hz,
                "original_filename": original_filename,
                "created_at": created_at,
                "created_by_user_id": created_by_user_id,
                "state": "DELETED" if deleted_at is not None else "ACTIVE",
                "retention_expires_at": retention_expires_at,
                "deletion_requested_at": deleted_at,
                "deletion_reason": "backfill" if deleted_at is not None else None,
                "deleted_at": deleted_at,
            },
        )
        bind.execute(link, {"media_id": media_id, "recording_id": recording_id})


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "media_objects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("meeting_id", sa.String(length=36), nullable=True),
        sa.Column("ingestion_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("derived_from_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("storage_backend", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=600), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("container_format", sa.String(length=80), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("channel_count", sa.Integer(), nullable=True),
        sa.Column("sample_rate_hz", sa.Integer(), nullable=True),
        sa.Column("original_filename", sa.String(length=300), nullable=True),
        sa.Column("producer", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("lifecycle_state", sa.String(length=20), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deletion_reason", sa.String(length=40), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_hold_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("legal_hold_reason", sa.String(length=200), nullable=True),
        sa.Column("legal_hold_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("integrity", sa.String(length=12), nullable=False),
        sa.Column("last_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(_in("kind", KINDS), name="ck_media_kind"),
        sa.CheckConstraint(_in("lifecycle_state", STATES), name="ck_media_state"),
        sa.CheckConstraint(_in("integrity", INTEGRITY), name="ck_media_integrity"),
        sa.CheckConstraint("storage_backend IN ('local', 's3')", name="ck_media_backend"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_media_sha256"),
        sa.CheckConstraint("size_bytes > 0", name="ck_media_size"),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="ck_media_duration"
        ),
        sa.CheckConstraint(
            "(kind = 'ORIGINAL' AND derived_from_id IS NULL) OR "
            "(kind <> 'ORIGINAL' AND derived_from_id IS NOT NULL)",
            name="ck_media_lineage",
        ),
        sa.CheckConstraint(
            "(lifecycle_state = 'DELETED') = (deleted_at IS NOT NULL)", name="ck_media_deleted"
        ),
        sa.CheckConstraint(
            "(lifecycle_state = 'LEGAL_HOLD') = (legal_hold_at IS NOT NULL)",
            name="ck_media_hold",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["derived_from_id"], ["media_objects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["ingestion_id"], ["ingestion_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["legal_hold_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    with op.batch_alter_table("media_objects", schema=None) as batch_op:
        for column in ("organization_id", "workspace_id", "meeting_id", "derived_from_id"):
            batch_op.create_index(batch_op.f(f"ix_media_objects_{column}"), [column], unique=False)
        batch_op.create_index(
            "uq_media_original_per_meeting",
            ["meeting_id"],
            unique=True,
            postgresql_where=ORIGINAL_WHERE,
            sqlite_where=ORIGINAL_WHERE,
        )
        batch_op.create_index(
            "ix_media_workspace_state", ["workspace_id", "lifecycle_state"], unique=False
        )
        batch_op.create_index("ix_media_workspace_sha256", ["workspace_id", "sha256"], unique=False)

    with op.batch_alter_table("recordings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("media_object_id", sa.String(length=36), nullable=True))
        batch_op.create_unique_constraint("uq_recording_media_object", ["media_object_id"])
        batch_op.create_foreign_key(
            "fk_recording_media_object",
            "media_objects",
            ["media_object_id"],
            ["id"],
            ondelete="SET NULL",
        )

    _backfill(op.get_bind())
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (media lifecycle, holds and integrity results are lost)."""
    with op.batch_alter_table("recordings", schema=None) as batch_op:
        batch_op.drop_constraint("fk_recording_media_object", type_="foreignkey")
        batch_op.drop_constraint("uq_recording_media_object", type_="unique")
        batch_op.drop_column("media_object_id")

    with op.batch_alter_table("media_objects", schema=None) as batch_op:
        batch_op.drop_index("ix_media_workspace_sha256")
        batch_op.drop_index("ix_media_workspace_state")
        batch_op.drop_index("uq_media_original_per_meeting")
        for column in ("derived_from_id", "meeting_id", "workspace_id", "organization_id"):
            batch_op.drop_index(batch_op.f(f"ix_media_objects_{column}"))
    op.drop_table("media_objects")
