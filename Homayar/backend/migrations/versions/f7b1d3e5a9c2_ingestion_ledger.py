"""ingestion ledger and duplicate-media policy (WP05-T01, ADR 0016)

Revision ID: f7b1d3e5a9c2
Revises: e6a9c3d7f2b5
Create Date: 2026-09-11 12:00:00.000000

Adds ``ingestion_records``: one row per accepted ingestion event with its
identity ``(workspace, source_type, external_event_id)`` (unique), the
canonical payload digest, the media digest, provenance (actor, API key,
received time) and the links to the conversation and root job it created
(``SET NULL`` when those go; the ledger outlives them).  Joined to the
tenant-only row-level-security policy set.  ``workspaces`` gains
``duplicate_media_policy`` (``ALLOW`` / ``REJECT``, default ``ALLOW``).

Backfill: every conversation that already has a recording and a root job
gets one ``ACCEPTED`` row (``source_type = FILE_UPLOAD``, ``source_provider =
backfill``) whose external event id is the job's peppered idempotency digest
— the only event identity those uploads ever had — and whose payload digest
is computed with the same canonical form the application uses (inlined
below and pinned to this revision on purpose: a later change of the
application's canonical form must not silently change what this migration
computes).

Downgrade drops the table and the column: the ledger rows are lost.  Roll
back only with a backup (master prompt §60).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from backend.database.rls import apply_row_level_security

# revision identifiers, used by Alembic.
revision: str = "f7b1d3e5a9c2"
down_revision: str | Sequence[str] | None = "e6a9c3d7f2b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCE_TYPES = (
    "FILE_UPLOAD",
    "MICROPHONE",
    "SYSTEM_AUDIO",
    "MIC_PLUS_SYSTEM_AUDIO",
    "SIP_VOIP",
    "MEETING_BOT",
    "EXTERNAL_RECORDING_URL",
    "OFFLINE_FOLDER_WATCHER",
    "API",
)


def _backfill_payload_digest(
    *,
    media_sha256: str,
    media_size_bytes: int,
    original_filename: str | None,
    title: str | None,
    language: str,
) -> str:
    """Mirror of ``backend.services.ingestion_service.canonical_payload`` for a
    FILE_UPLOAD event without source id or extra facts, as of this revision."""
    document = {
        "source_type": "file_upload",
        "external_source_id": None,
        "media_sha256": media_sha256,
        "media_size_bytes": media_size_bytes,
        "original_filename": original_filename,
        "title": title,
        "language": language,
        "extra": {},
    }
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _backfill_records(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT m.id, m.workspace_id, w.organization_id, m.created_by_user_id, "
            "m.created_at, m.title, m.language, r.checksum_sha256, r.size_bytes, "
            "r.original_filename, r.media_type "
            "FROM meetings m JOIN workspaces w ON w.id = m.workspace_id "
            "JOIN recordings r ON r.meeting_id = m.id "
            "ORDER BY m.created_at, m.id"
        )
    ).all()
    insert = sa.text(
        "INSERT INTO ingestion_records (id, organization_id, workspace_id, source_type, "
        "source_provider, external_event_id, external_source_id, payload_hash, media_hash, "
        "media_size_bytes, declared_content_type, original_filename, status, reason, "
        "received_at, created_at, created_by_user_id, api_key_id, meeting_id, job_id, "
        "duplicate_of_id, conflict_count, last_conflict_at) VALUES (:id, :organization_id, "
        ":workspace_id, 'FILE_UPLOAD', 'backfill', :external_event_id, NULL, :payload_hash, "
        ":media_hash, :media_size_bytes, :declared_content_type, :original_filename, "
        "'ACCEPTED', NULL, :received_at, :created_at, :created_by_user_id, NULL, :meeting_id, "
        ":job_id, NULL, 0, NULL)"
    )
    for (
        meeting_id,
        workspace_id,
        organization_id,
        created_by_user_id,
        created_at,
        title,
        language,
        checksum,
        size_bytes,
        original_filename,
        media_type,
    ) in rows:
        job = bind.execute(
            sa.text(
                "SELECT id, idempotency_key FROM processing_jobs WHERE meeting_id = :meeting_id "
                "ORDER BY created_at ASC, id ASC LIMIT 1"
            ),
            {"meeting_id": meeting_id},
        ).first()
        if job is None:
            continue  # never ingested through an event; nothing to record
        bind.execute(
            insert,
            {
                "id": str(uuid.uuid4()),
                "organization_id": organization_id,
                "workspace_id": workspace_id,
                "external_event_id": str(job[1])[:200],
                "payload_hash": _backfill_payload_digest(
                    media_sha256=str(checksum).lower(),
                    media_size_bytes=int(size_bytes),
                    original_filename=original_filename,
                    title=title,
                    language=language,
                ),
                "media_hash": str(checksum).lower(),
                "media_size_bytes": int(size_bytes),
                "declared_content_type": media_type,
                "original_filename": original_filename,
                "received_at": created_at,
                "created_at": created_at,
                "created_by_user_id": created_by_user_id,
                "meeting_id": meeting_id,
                "job_id": job[0],
            },
        )


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "duplicate_media_policy",
                sa.String(length=16),
                nullable=False,
                server_default="ALLOW",
            )
        )
        batch_op.create_check_constraint(
            "ck_workspace_duplicate_media_policy",
            "duplicate_media_policy IN ('ALLOW', 'REJECT')",
        )

    op.create_table(
        "ingestion_records",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("workspace_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_provider", sa.String(length=60), nullable=False),
        sa.Column("external_event_id", sa.String(length=200), nullable=False),
        sa.Column("external_source_id", sa.String(length=200), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("media_hash", sa.String(length=64), nullable=True),
        sa.Column("media_size_bytes", sa.Integer(), nullable=True),
        sa.Column("declared_content_type", sa.String(length=120), nullable=True),
        sa.Column("original_filename", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=80), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=True),
        sa.Column("api_key_id", sa.String(length=36), nullable=True),
        sa.Column("meeting_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("duplicate_of_id", sa.String(length=36), nullable=True),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("last_conflict_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "source_type IN (" + ", ".join(f"'{name}'" for name in SOURCE_TYPES) + ")",
            name="ck_ingestion_source_type",
        ),
        sa.CheckConstraint("status IN ('ACCEPTED', 'REJECTED')", name="ck_ingestion_status"),
        sa.CheckConstraint("length(payload_hash) = 64", name="ck_ingestion_payload_hash"),
        sa.CheckConstraint(
            "media_hash IS NULL OR length(media_hash) = 64", name="ck_ingestion_media_hash"
        ),
        sa.CheckConstraint(
            "media_size_bytes IS NULL OR media_size_bytes > 0", name="ck_ingestion_media_size"
        ),
        sa.CheckConstraint("conflict_count >= 0", name="ck_ingestion_conflicts"),
        sa.CheckConstraint(
            "(status = 'ACCEPTED' AND reason IS NULL) OR status <> 'ACCEPTED'",
            name="ck_ingestion_accepted_reason",
        ),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["duplicate_of_id"], ["ingestion_records.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["processing_jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id", "source_type", "external_event_id", name="uq_ingestion_event"
        ),
        sa.UniqueConstraint("meeting_id", name="uq_ingestion_meeting"),
        sa.UniqueConstraint("job_id", name="uq_ingestion_job"),
    )
    with op.batch_alter_table("ingestion_records", schema=None) as batch_op:
        for column in ("organization_id", "workspace_id", "created_by_user_id", "meeting_id"):
            batch_op.create_index(
                batch_op.f(f"ix_ingestion_records_{column}"), [column], unique=False
            )
        batch_op.create_index(
            "ix_ingestion_workspace_media", ["workspace_id", "media_hash"], unique=False
        )
        batch_op.create_index(
            "ix_ingestion_workspace_received", ["workspace_id", "received_at"], unique=False
        )

    _backfill_records(op.get_bind())
    apply_row_level_security(op.get_bind())


def downgrade() -> None:
    """Downgrade schema (the ingestion ledger is lost)."""
    with op.batch_alter_table("ingestion_records", schema=None) as batch_op:
        batch_op.drop_index("ix_ingestion_workspace_received")
        batch_op.drop_index("ix_ingestion_workspace_media")
        for column in ("meeting_id", "created_by_user_id", "workspace_id", "organization_id"):
            batch_op.drop_index(batch_op.f(f"ix_ingestion_records_{column}"))
    op.drop_table("ingestion_records")

    with op.batch_alter_table("workspaces", schema=None) as batch_op:
        batch_op.drop_constraint("ck_workspace_duplicate_media_policy", type_="check")
        batch_op.drop_column("duplicate_media_policy")
