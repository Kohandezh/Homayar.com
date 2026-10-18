"""Reconcile a restored staging database before anyone may use it (HOM-117).

Runs after ``restore_backup`` against the staging database and the isolated
object store, as the administrative (superuser) role, and writes a report
sealed with the backup key.  ``promote`` accepts only a PASS report.

Checks, in order:

``marker``            the staging database is a loaded restore of this backup
``schema``            the Alembic revision is the code's single head
``rls_structure``     every policy table has RLS enabled and forced and the
                      Homayar policy; the application role cannot bypass RLS
``rls_behavior``      as the application role (in a rolled-back transaction):
                      no tenant row is visible without a bound tenant, and a
                      bound tenant sees none of another tenant's rows
``tenant_boundaries`` rows that point at a conversation or an organization
                      agree on the tenant; every object key sits under its
                      own tenant prefix; per-tenant counts equal the backup's
                      when the schema did not move forward
``tombstones``        the journal is replayed (re-tombstone, reopen and run
                      the purge); nothing journaled is alive, no journal line
                      was rejected, no purge failed
``non_resurrection``  for every journaled conversation: no content rows, no
                      active share link, no journaled object left in the store
                      (held conversations are listed for review instead)
``objects``           every object a live row requires is present; objects no
                      row references are listed as orphans (never deleted)
``secrets``           TOKEN_PEPPER / PII keys match the backup's fingerprints
``post_backup_state`` revocations made after the backup (share links, API
                      keys, invitations, sessions) cannot be replayed: counts
                      are reported; ``revoke_share_links`` revokes them all

Status per check: PASS, FAIL, REVIEW (owner decision needed; an explicit
acceptance turns it into PASS and is recorded) or WARN (does not block).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from backend.backup.crypto import BackupKey
from backend.backup.logical import (
    alembic_revisions,
    connect,
    database_name,
    list_tables,
    table_columns,
    with_database,
)
from backend.backup.manifest import BackupInvalid, check_manifest_mac, read_manifest
from backend.backup.objects import LOSS_REASONS
from backend.backup.release import code_heads
from backend.backup.restore import (
    RestoreRefused,
    cas_marker,
    marker_payload,
    marker_statement,
    read_marker,
    read_state,
    staging_lock,
)
from backend.backup.verify import load_catalog
from backend.database import relational
from backend.database.rls import APP_ROLE, POLICIES, POLICY_NAME
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    EvidenceSpan,
    MeetingSpeaker,
    MeetingSummary,
    PiiMapping,
    RedactedSegment,
    TextViewEntity,
    TextViewSegment,
    TranscriptRedaction,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptTextView,
)
from backend.storage import ObjectStorage, StorageError

REPORT_FILE = "reconciliation-report.json"
CONTENT_TABLES = tuple(
    model.__tablename__
    for model in (
        TranscriptRevision,
        TranscriptSegment,
        AnalysisRun,
        EvidenceSpan,
        MeetingSummary,
        ActionItem,
        MeetingSpeaker,
        TranscriptTextView,
        TextViewSegment,
        TextViewEntity,
        PiiMapping,
        RedactedSegment,
        TranscriptRedaction,
    )
)
_ORG_TABLES = frozenset(
    {"workspaces", "organizations", "organization_memberships", "organization_scope_grants"}
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _check(status: str, **details: Any) -> dict[str, Any]:
    return {"status": status, "details": details}


@contextmanager
def bound_database(settings: Any) -> Iterator[None]:
    """Point the application's session factory at the staging database for
    the duration (the tombstone replay uses it), then restore the caller's."""
    saved = (relational._engine, relational._session_factory)
    relational._engine = None
    relational._session_factory = None
    try:
        relational.configure_database(settings)
        yield
    finally:
        if relational._engine is not None:
            relational._engine.dispose()
        relational._engine, relational._session_factory = saved


# --- individual checks ----------------------------------------------------------


def _rls_structure(connection, app_role: str) -> dict[str, Any]:
    present = set(list_tables(connection))
    problems = []
    for table in sorted(POLICIES):
        if table not in present:
            problems.append({"table": table, "problem": "missing"})
            continue
        row = connection.execute(
            "SELECT c.relrowsecurity, c.relforcerowsecurity, "
            "EXISTS (SELECT 1 FROM pg_policies p WHERE p.schemaname = 'public' "
            "AND p.tablename = c.relname AND p.policyname = %s) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = %s",
            (POLICY_NAME, table),
        ).fetchone()
        if not (row and row[0] and row[1] and row[2]):
            problems.append({"table": table, "problem": "rls not enabled, forced and present"})
    role = connection.execute(
        "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = %s", (app_role,)
    ).fetchone()
    if role is None:
        problems.append({"role": app_role, "problem": "missing"})
    elif role[0] or role[1]:
        problems.append({"role": app_role, "problem": "superuser or BYPASSRLS"})
    status = "FAIL" if problems else "PASS"
    return _check(status, policy_tables=len(POLICIES), problems=problems)


def _visible(connection, table: str, where: sql.Composable) -> int:
    return int(
        connection.execute(
            sql.SQL("SELECT count(*) FROM {} WHERE {}").format(sql.Identifier(table), where)
        ).fetchone()[0]
    )


def _rls_behavior(
    admin_url: str, app_role: str, workspaces: list[str], max_tenants: int
) -> dict[str, Any]:
    leaks: list[dict[str, Any]] = []
    sampled = sorted(workspaces)[:max_tenants]
    own_rows_seen = 0
    with connect(admin_url) as connection:
        tables = [t for t in sorted(POLICIES) if t in set(list_tables(connection))]
        columns = {t: {c[0] for c in table_columns(connection, t)} for t in tables}
        own_meetings = dict(
            connection.execute("SELECT workspace_id, count(*) FROM meetings GROUP BY 1").fetchall()
        )
        try:
            connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(app_role)))
            # 1. No bound context: no tenant row at all.
            for table in tables:
                if "workspace_id" in columns[table]:
                    count = _visible(connection, table, sql.SQL("workspace_id IS NOT NULL"))
                elif table in _ORG_TABLES:
                    count = _visible(connection, table, sql.SQL("true"))
                else:
                    continue
                if count:
                    leaks.append({"context": "none", "table": table, "rows": count})
            # 2. Each bound tenant: nothing of another tenant.
            for workspace_id in sampled:
                connection.execute(
                    "SELECT set_config('homayar.workspace_id', %s, true)", (workspace_id,)
                )
                for table in tables:
                    if "workspace_id" in columns[table]:
                        foreign = sql.SQL("workspace_id IS NOT NULL AND workspace_id <> {}").format(
                            sql.Literal(workspace_id)
                        )
                    elif table == "workspaces":
                        foreign = sql.SQL("id <> {}").format(sql.Literal(workspace_id))
                    else:
                        continue
                    count = _visible(connection, table, foreign)
                    if count:
                        leaks.append({"context": "tenant", "table": table, "rows": count})
                seen = _visible(connection, "meetings", sql.SQL("true"))
                if seen != int(own_meetings.get(workspace_id, 0)):
                    leaks.append(
                        {"context": "tenant", "table": "meetings", "problem": "own rows hidden"}
                    )
                own_rows_seen += seen
        finally:
            connection.rollback()
    status = "FAIL" if leaks else "PASS"
    return _check(
        status,
        tenants=len(workspaces),
        tenants_checked=len(sampled),
        tables_checked=len(tables),
        own_meetings_visible=own_rows_seen,
        problems=leaks[:50],
    )


def _tenant_boundaries(admin_url: str, load: dict[str, Any]) -> dict[str, Any]:
    problems: list[dict[str, Any]] = []
    with connect(admin_url) as connection:
        present = set(list_tables(connection))
        for table in sorted(present):
            columns = {c[0] for c in table_columns(connection, table)}
            if table != "meetings" and {"workspace_id", "meeting_id"} <= columns:
                count = int(
                    connection.execute(
                        sql.SQL(
                            "SELECT count(*) FROM {} t JOIN meetings m ON m.id = t.meeting_id "
                            "WHERE t.workspace_id <> m.workspace_id"
                        ).format(sql.Identifier(table))
                    ).fetchone()[0]
                )
                if count:
                    problems.append(
                        {"table": table, "problem": "meeting of another tenant", "rows": count}
                    )
            if table != "workspaces" and {"workspace_id", "organization_id"} <= columns:
                count = int(
                    connection.execute(
                        sql.SQL(
                            "SELECT count(*) FROM {} t JOIN workspaces w ON w.id = t.workspace_id "
                            "WHERE t.organization_id <> w.organization_id"
                        ).format(sql.Identifier(table))
                    ).fetchone()[0]
                )
                if count:
                    problems.append(
                        {
                            "table": table,
                            "problem": "organization differs from tenant's",
                            "rows": count,
                        }
                    )
        # Every foreign key between two tenant-keyed tables must join rows of
        # the same workspace (the load proved only that the parent exists).
        tenant_keyed = {
            t for t in present if "workspace_id" in {c[0] for c in table_columns(connection, t)}
        }
        constraints = connection.execute(
            "SELECT con.conname, child.relname, parent.relname, "
            "ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i) "
            "      JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n "
            "      ORDER BY k.i), "
            "ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i) "
            "      JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n "
            "      ORDER BY k.i) "
            "FROM pg_constraint con JOIN pg_class child ON child.oid = con.conrelid "
            "JOIN pg_class parent ON parent.oid = con.confrelid "
            "JOIN pg_namespace n ON n.oid = con.connamespace "
            "WHERE con.contype = 'f' AND n.nspname = 'public' ORDER BY con.conname"
        ).fetchall()
        tenant_links = 0
        for name, child, parent, child_columns, parent_columns in constraints:
            if child not in tenant_keyed or parent not in tenant_keyed:
                continue
            if "workspace_id" in child_columns:
                continue  # composite with the tenant: already the same workspace
            tenant_links += 1
            joined = sql.SQL(" AND ").join(
                sql.SQL("p.{} = c.{}").format(sql.Identifier(pc), sql.Identifier(cc))
                for cc, pc in zip(child_columns, parent_columns, strict=True)
            )
            count = int(
                connection.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {} c JOIN {} p ON {} "
                        "WHERE c.workspace_id IS DISTINCT FROM p.workspace_id"
                    ).format(sql.Identifier(child), sql.Identifier(parent), joined)
                ).fetchone()[0]
            )
            if count:
                problems.append(
                    {"table": child, "problem": f"{name} points into another tenant", "rows": count}
                )
        for table, column in (
            ("media_objects", "storage_key"),
            ("recordings", "storage_key"),
            ("stage_checkpoints", "artifact_key"),
        ):
            if table not in present:
                continue
            count = int(
                connection.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {} WHERE left({}, length('tenants/' || workspace_id "
                        "|| '/objects/')) <> 'tenants/' || workspace_id || '/objects/'"
                    ).format(sql.Identifier(table), sql.Identifier(column))
                ).fetchone()[0]
            )
            if count:
                problems.append(
                    {"table": table, "problem": "object key outside tenant prefix", "rows": count}
                )
        connection.rollback()
    # Per-tenant row counts were compared with the backup's at load time,
    # before the tombstone replay changed them (restore state).
    if not load.get("tenant_tables_verified"):
        problems.append({"problem": "per-tenant counts were not verified at load"})
    return _check(
        "FAIL" if problems else "PASS",
        tenant_foreign_keys_checked=tenant_links,
        per_tenant_tables_verified_at_load=load.get("tenant_tables_verified", 0),
        foreign_keys_verified_at_load=bool(load.get("foreign_keys_verified")),
        problems=problems[:50],
    )


def _references(connection) -> dict[str, dict[str, Any]]:
    """Live expectations (and expected SHA-256) by key in the restored database."""
    refs: dict[str, dict[str, Any]] = {}
    present = set(list_tables(connection))
    if "media_objects" in present:
        for key, workspace_id, meeting_id, state, sha256 in connection.execute(
            "SELECT storage_key, workspace_id, meeting_id, lifecycle_state, sha256 "
            "FROM media_objects"
        ):
            required = str(state).upper() in {"ACTIVE", "RETENTION_PENDING", "LEGAL_HOLD"}
            gone = str(state).upper() == "DELETED"
            refs[key] = {
                "workspace_id": workspace_id,
                "meeting_id": meeting_id,
                "expectation": "required" if required else ("gone" if gone else "optional"),
                "sha256": sha256,
                "held": str(state).upper() == "LEGAL_HOLD" or bool(refs.get(key, {}).get("held")),
            }
    if "recordings" in present:
        for key, workspace_id, meeting_id, deleted, sha256 in connection.execute(
            "SELECT storage_key, workspace_id, meeting_id, deleted_at IS NOT NULL, "
            "checksum_sha256 FROM recordings"
        ):
            if key in refs:
                continue
            refs[key] = {
                "workspace_id": workspace_id,
                "meeting_id": meeting_id,
                "expectation": "optional" if deleted else "required",
                "sha256": sha256,
            }
    if "stage_checkpoints" in present:
        held_meetings = {
            ref["meeting_id"] for ref in refs.values() if ref.get("held") and ref["meeting_id"]
        }
        query = (
            "SELECT c.artifact_key, c.workspace_id, c.meeting_id, c.state, "
            "c.object_deleted_at IS NOT NULL, c.artifact_sha256, c.invalidation_reason, "
            "m.id IS NULL OR m.deleted_at IS NOT NULL "
            "FROM stage_checkpoints c LEFT JOIN meetings m ON m.id = c.meeting_id"
            if "meetings" in present
            else "SELECT artifact_key, workspace_id, meeting_id, state, "
            "object_deleted_at IS NOT NULL, artifact_sha256, invalidation_reason, false "
            "FROM stage_checkpoints"
        )
        for (
            key,
            workspace_id,
            meeting_id,
            state,
            removed,
            sha256,
            reason,
            meeting_deleted,
        ) in connection.execute(query):
            held = meeting_id in held_meetings and str(reason or "").lower() not in LOSS_REASONS
            if removed:
                expectation = "gone"
            elif str(state).upper() == "PUBLISHED":
                expectation = "required"
            elif held and meeting_deleted and str(state).upper() == "INVALIDATED":
                expectation = "required"  # a legal hold keeps a deleted meeting's outputs
            else:
                expectation = "optional"
            if key not in refs or expectation == "required":
                refs[key] = {
                    "workspace_id": workspace_id,
                    "meeting_id": meeting_id,
                    "expectation": expectation,
                    "sha256": sha256,
                    "held": held and expectation == "required",
                }
    return refs


def _stored_sha256(storage: ObjectStorage, workspace_id: str, key: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with closing(storage.open_reader(workspace_id, key)) as reader:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(block)
    except StorageError:
        return None
    return digest.hexdigest()


def _objects(
    admin_url: str,
    storage: ObjectStorage | None,
    workspaces: list[str],
    *,
    catalog: dict[str, Any],
    accept_missing: str | None,
) -> dict[str, Any]:
    """Every required object is present AND hashes to its restored row's SHA-256.

    Bytes are re-read from the target store (corruption after the restore is
    caught too).  A required object the backup itself recorded as deleted
    after the snapshot (its own row was gone or being deleted when the copy
    ran, and it was not under legal hold) is explained, listed and not a
    failure; anything else missing or different fails unless the owner
    accepts the loss.
    """
    if storage is None:
        return _check("FAIL", note="no object store: a restore without objects is not usable")
    with connect(admin_url) as connection:
        refs = _references(connection)
        connection.rollback()
    stored: dict[str, str] = {}
    for workspace_id in sorted(workspaces):
        for listing in storage.iter_objects(workspace_id):
            stored[listing.key] = workspace_id
    explained = {
        item["key"]: item["reason"] for item in catalog["objects"].get("missing_explained", [])
    }
    # A legally held object is never an intended deletion, whatever an older
    # set's catalog says.
    explained = {k: v for k, v in explained.items() if not refs.get(k, {}).get("held")}
    required = {k: r for k, r in refs.items() if r["expectation"] == "required"}
    missing = sorted(k for k in required if k not in stored and k not in explained)
    missing_explained = sorted(k for k in required if k not in stored and k in explained)
    corrupt = []
    verified = 0
    for key, ref in sorted(required.items()):
        if key not in stored or not ref.get("sha256"):
            continue
        actual = _stored_sha256(storage, stored[key], key)
        if actual != ref["sha256"]:
            corrupt.append(key)
        else:
            verified += 1
    orphans = sorted(k for k in stored if k not in refs)
    lingering = sorted(k for k, r in refs.items() if r["expectation"] == "gone" and k in stored)
    # Bytes copied while their deletion was in flight: the restored row says
    # they are gone, so they are removed again (deletion state wins).
    removal_failed = []
    for key in lingering:
        try:
            storage.delete(stored[key], key)
        except StorageError:
            removal_failed.append(key)
    status = "PASS"
    if missing or corrupt:
        status = "PASS" if accept_missing else "FAIL"
    elif missing_explained:
        status = "WARN"
    if removal_failed:
        status = "FAIL"
    return _check(
        status,
        stored=len(stored),
        referenced=len(refs),
        required=len(required),
        content_verified=verified,
        required_missing=len(missing),
        required_missing_keys=missing[:100],
        content_mismatch=len(corrupt),
        content_mismatch_keys=corrupt[:100],
        deleted_after_snapshot=[
            {"key": key, "reason": explained[key]} for key in missing_explained[:100]
        ],
        backup_integrity=catalog["objects"].get("integrity", "complete"),
        orphans=len(orphans),
        orphan_keys=orphans[:100],
        removed_again=len(lingering) - len(removal_failed),
        removal_failed_keys=removal_failed[:100],
        accepted_missing=accept_missing,
    )


def _held_meetings(connection) -> set[str]:
    if "media_objects" not in set(list_tables(connection)):
        return set()
    return {
        row[0]
        for row in connection.execute(
            "SELECT DISTINCT meeting_id FROM media_objects WHERE lifecycle_state = 'LEGAL_HOLD' "
            "AND meeting_id IS NOT NULL"
        )
    }


def _bytes_left(storage: ObjectStorage | None, record: Any, found: list[str]) -> None:
    if storage is None:
        return
    for key in record.object_keys:
        try:
            if storage.exists(record.workspace_id, key):
                found.append(key)
        except StorageError:
            found.append(key)


def _non_resurrection(
    admin_url: str, storage: ObjectStorage | None, records: list[Any]
) -> dict[str, Any]:
    alive: list[str] = []
    content: list[dict[str, Any]] = []
    shares: list[str] = []
    bytes_left: list[str] = []
    held_review: list[dict[str, str]] = []
    with connect(admin_url) as connection:
        present = set(list_tables(connection))
        content_tables = [t for t in CONTENT_TABLES if t in present]
        held = _held_meetings(connection)
        for record in records:
            row = connection.execute(
                "SELECT deleted_at IS NOT NULL FROM meetings WHERE id = %s AND workspace_id = %s",
                (record.meeting_id, record.workspace_id),
            ).fetchone()
            if row is None:
                # The backup predates the conversation, but its bytes may have
                # been copied after the snapshot (the replay removes them).
                _bytes_left(storage, record, bytes_left)
                continue
            if record.meeting_id in held:
                held_review.append(
                    {"meeting_id": record.meeting_id, "workspace_id": record.workspace_id}
                )
                continue
            if not row[0]:
                alive.append(record.meeting_id)
            for table in content_tables:
                count = int(
                    connection.execute(
                        sql.SQL("SELECT count(*) FROM {} WHERE meeting_id = %s").format(
                            sql.Identifier(table)
                        ),
                        (record.meeting_id,),
                    ).fetchone()[0]
                )
                if count:
                    content.append({"meeting_id": record.meeting_id, "table": table, "rows": count})
            if "share_links" in present:
                active = int(
                    connection.execute(
                        "SELECT count(*) FROM share_links "
                        "WHERE meeting_id = %s AND revoked_at IS NULL",
                        (record.meeting_id,),
                    ).fetchone()[0]
                )
                if active:
                    shares.append(record.meeting_id)
            _bytes_left(storage, record, bytes_left)
        connection.rollback()
    failed = alive or content or shares or bytes_left
    status = "FAIL" if failed else ("REVIEW" if held_review else "PASS")
    return _check(
        status,
        journaled=len(records),
        alive=alive[:50],
        content_rows=content[:50],
        active_share_links=shares[:50],
        object_bytes_left=bytes_left[:50],
        held_for_review=held_review[:50],
    )


def _secrets(catalog: dict[str, Any], settings: Any, key: BackupKey) -> dict[str, Any]:
    expected = catalog["config"].get("secret_fingerprints", {})
    mismatched = []
    pepper = str(getattr(settings, "token_pepper", "") or "")
    if (
        "TOKEN_PEPPER" in expected
        and key.mac("secret-fingerprint", pepper.encode("utf-8")) != expected["TOKEN_PEPPER"]
    ):
        mismatched.append("TOKEN_PEPPER")
    configured = {}
    for entry in filter(
        None, (p.strip() for p in str(getattr(settings, "pii_mapping_keys", "") or "").split(","))
    ):
        key_id, _, material = entry.partition(":")
        configured[key_id] = material
    missing_pii = []
    for key_id in catalog["config"].get("pii_mapping_key_ids_in_use", []):
        name = f"PII_MAPPING_KEYS:{key_id}"
        if key_id not in configured:
            missing_pii.append(key_id)
        elif (
            name in expected
            and key.mac("secret-fingerprint", configured[key_id].encode("utf-8")) != expected[name]
        ):
            mismatched.append(name)
    status = "WARN" if (mismatched or missing_pii) else "PASS"
    return _check(status, mismatched=mismatched, pii_key_ids_missing=missing_pii)


def _post_backup_state(
    admin_url: str, *, revoke_share_links: bool, now: datetime
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    revoked = 0
    with connect(admin_url) as connection:
        present = set(list_tables(connection))
        queries = {
            "active_share_links": ("share_links", "revoked_at IS NULL"),
            "active_api_keys": ("api_keys", "revoked_at IS NULL"),
            "pending_invitations": (
                "workspace_invitations",
                "accepted_at IS NULL AND revoked_at IS NULL",
            ),
        }
        for name, (table, where) in queries.items():
            if table not in present:
                continue
            columns = {c[0] for c in table_columns(connection, table)}
            needed = {part.split()[0] for part in where.split(" AND ")}
            if not needed <= columns:
                continue
            counts[name] = int(
                connection.execute(
                    sql.SQL("SELECT count(*) FROM {} WHERE {}").format(
                        sql.Identifier(table), sql.SQL(where)
                    )
                ).fetchone()[0]
            )
        if revoke_share_links and "share_links" in present:
            revoked = connection.execute(
                "UPDATE share_links SET revoked_at = %s WHERE revoked_at IS NULL", (now,)
            ).rowcount
        connection.commit()
    return _check(
        "WARN",
        counts_before=counts,
        share_links_revoked=revoked,
        note=(
            "revocations, password changes and sign-outs made after the backup are not "
            "journaled and are not replayed; rotate JWT_SECRET to end every session and "
            "review API keys and share links with the owner"
        ),
    )


# --- orchestration ---------------------------------------------------------------


def _seal(report: dict[str, Any], key: BackupKey) -> dict[str, Any]:
    body = json.dumps(
        {k: v for k, v in report.items() if k != "mac"}, sort_keys=True, separators=(",", ":")
    )
    return {**report, "mac": key.mac("reconciliation", body.encode("utf-8"))}


def load_report(path: Path, key: BackupKey) -> dict[str, Any]:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RestoreRefused("report_unreadable", str(path)) from exc
    if not isinstance(report, dict) or _seal(report, key).get("mac") != report.get("mac"):
        raise RestoreRefused("report_tampered", "reconciliation report MAC does not verify")
    return report


def report_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _journal_identity(
    journal: Any, watermark: dict[str, Any], workspaces: list[str]
) -> tuple[list[str], str, dict[str, int]]:
    """Positive evidence that the replica is THIS deployment's journal, at
    least as new as the backup.

    ``anchored``: the backup fingerprinted the journal files (name, size,
    SHA-256 of their content then); they are append-only, so the replica must
    hold each one with the same first ``size`` bytes.  A file that was empty
    at backup time proves nothing, so it only has to be present.

    ``unanchored``: no non-empty file existed at backup time; the replica is
    accepted on records of tenants the restored database knows.  Records of
    tenants created after the backup are expected there and skipped by the
    replay (their data is not in the restore).

    ``none``: no evidence at all (no records, or only records of tenants the
    backup does not know, i.e. possibly another deployment's journal).  The
    caller fails it unless the owner accepts it explicitly.
    """
    problems: list[str] = []
    present = {path.name: path for path in journal.files()}
    recorded = watermark.get("files")
    entries = recorded if isinstance(recorded, list) else []
    anchors = 0
    for entry in entries:
        path = present.get(entry["name"])
        if path is None:
            problems.append(f"{entry['name']}: missing (stale or foreign replica)")
            continue
        size = int(entry["size"])
        if size <= 0:
            continue
        anchors += 1
        with open(path, "rb") as handle:
            prefix = handle.read(size)
        if len(prefix) < size:
            problems.append(f"{entry['name']}: shorter than at backup time (stale replica)")
        elif hashlib.sha256(prefix).hexdigest() != entry["sha256"]:
            problems.append(f"{entry['name']}: differs from the backup's journal")
    records, _ = journal.read()
    known = set(workspaces)
    unknown = [r for r in records if r.workspace_id not in known]
    info = {
        "records": len(records),
        "records_of_tenants_unknown_to_backup": len(unknown),
        "tenants_unknown_to_backup": len({r.workspace_id for r in unknown}),
    }
    if anchors:
        return problems, "anchored", info
    if len(unknown) < len(records):
        return problems, "unanchored", info
    return problems, "none", info


def reconcile_restore(
    *,
    set_dir: Path,
    key: BackupKey,
    admin_url: str,
    staging_database: str,
    settings: Any,
    storage: ObjectStorage,
    work_dir: Path,
    storage_target: str,
    app_role: str = APP_ROLE,
    accept_held: str | None = None,
    accept_missing: str | None = None,
    accept_no_journal: str | None = None,
    revoke_share_links: bool = False,
    max_tenants: int = 500,
) -> dict[str, Any]:
    """Run every check, replay the tombstones, write the sealed report."""
    manifest = read_manifest(set_dir)
    check_manifest_mac(manifest, key)  # its journal fingerprint decides below
    with staging_lock(admin_url, staging_database):
        return _reconcile_locked(
            set_dir=Path(set_dir),
            key=key,
            manifest=manifest,
            admin_url=admin_url,
            staging_database=staging_database,
            settings=settings,
            storage=storage,
            work_dir=Path(work_dir),
            storage_target=storage_target,
            app_role=app_role,
            accept_held=accept_held,
            accept_missing=accept_missing,
            accept_no_journal=accept_no_journal,
            revoke_share_links=revoke_share_links,
            max_tenants=max_tenants,
        )


def _reconcile_locked(
    *,
    set_dir: Path,
    key: BackupKey,
    manifest: dict[str, Any],
    admin_url: str,
    staging_database: str,
    settings: Any,
    storage: ObjectStorage,
    work_dir: Path,
    storage_target: str,
    app_role: str,
    accept_held: str | None,
    accept_missing: str | None,
    accept_no_journal: str | None,
    revoke_share_links: bool,
    max_tenants: int,
) -> dict[str, Any]:
    from backend.jobs.tombstone_reapply import reapply_tombstones, verify_tombstones
    from backend.services.tombstone_journal import journal_for

    started = time.monotonic()
    started_at = _now()
    backup_id = manifest["backup_id"]
    catalog = load_catalog(set_dir, manifest, key)
    staging_url = with_database(admin_url, staging_database)
    checks: dict[str, Any] = {}
    timings: dict[str, float] = {}

    marker = read_marker(admin_url, staging_database)
    state = read_state(work_dir) or {}
    if (
        not isinstance(marker, dict)
        or marker.get("backup_id") != backup_id
        or marker.get("state") not in {"loaded", "reconciling", "reconciled"}
    ):
        raise RestoreRefused(
            "staging_not_loaded", f"{staging_database} is not a loaded restore of {backup_id}"
        )
    if state.get("backup_id") != backup_id or state.get("staging_database") != staging_database:
        raise RestoreRefused("state_mismatch", "the work dir belongs to another restore")
    if not (state.get("manifest_mac") == marker.get("manifest_mac") == manifest["mac"]):
        raise RestoreRefused(
            "manifest_mismatch", "reconcile only the set this restore verified and loaded"
        )
    if state.get("storage_target") is None:
        raise RestoreRefused(
            "db_only_restore",
            "a restore without objects cannot be reconciled (journaled bytes and required "
            "objects are unverifiable); restore the set with its objects",
        )
    if state.get("storage_target") != storage_target:
        raise RestoreRefused(
            "storage_target_mismatch",
            "reconcile against the object store the restore wrote into",
        )
    # Any earlier report stops being promotable the moment this run starts
    # changing the database; only this run's final marker can make it so again.
    reconciling = {
        "backup_id": backup_id,
        "manifest_mac": manifest["mac"],
        "state": "reconciling",
        "run": secrets.token_hex(8),
        "at": _now(),
    }
    cas_marker(admin_url, staging_database, marker, reconciling)
    checks["marker"] = _check("PASS", state=marker.get("state"))

    mark = time.monotonic()
    with connect(staging_url) as connection:
        revisions = alembic_revisions(connection)
        heads = code_heads()
        checks["schema"] = _check(
            "PASS" if revisions == heads else "FAIL", database=revisions, code=heads
        )
        checks["rls_structure"] = _rls_structure(connection, app_role)
        workspaces = [row[0] for row in connection.execute("SELECT id FROM workspaces ORDER BY id")]
        connection.rollback()
    checks["rls_behavior"] = _rls_behavior(staging_url, app_role, workspaces, max_tenants)
    checks["tenant_boundaries"] = _tenant_boundaries(staging_url, state.get("database", {}))
    timings["integrity"] = round(time.monotonic() - mark, 3)

    mark = time.monotonic()
    staging_settings = settings.model_copy(
        update={"database_url": staging_url, "database_app_role": app_role}
    )
    journal = journal_for(staging_settings)
    records: list[Any] = []
    watermark = manifest.get("tombstone_journal", {})
    if journal is None or not journal.files():
        # Unset, unmounted or empty: nothing proves that later deletions are
        # re-applied.  Only an explicit owner decision lets this pass.
        checks["tombstones"] = _check(
            "PASS" if accept_no_journal else "FAIL",
            note="no tombstone journal files: deletions after the backup cannot be re-applied",
            journal_records_at_backup=watermark.get("records"),
            owner_override_no_journal=accept_no_journal,
        )
    else:
        identity_problems, anchoring, identity = _journal_identity(journal, watermark, workspaces)
        records, _ = journal.read()
        latest = max((r.tombstoned_at for r in records), default=None)
        stale = list(identity_problems)
        if anchoring == "none" and not accept_no_journal:
            # Empty files, or only tenants this backup does not know: nothing
            # shows that this is the deployment's journal.
            stale.append("no evidence that this is this deployment's journal")
        if len(records) < int(watermark.get("records") or 0):
            stale.append("fewer records than at backup time")
        recorded_latest = watermark.get("latest_tombstoned_at")
        if recorded_latest and (latest is None or latest < datetime.fromisoformat(recorded_latest)):
            stale.append("newest record older than at backup time")
        replay: dict[str, Any] = {}
        verified: dict[str, Any] = {}
        if not stale:
            with bound_database(staging_settings):
                replay = reapply_tombstones(
                    staging_settings, journal=journal, storage=storage, remove_unknown_keys=True
                )
                verified = verify_tombstones(staging_settings, journal=journal)
        failed = (
            stale
            or verified.get("alive")
            or verified.get("rejected_lines")
            or verified.get("purges_failed")
        )
        checks["tombstones"] = _check(
            "FAIL" if failed else "PASS",
            journal_identity=anchoring,
            journal_identity_details=identity,
            owner_override_no_journal=accept_no_journal if anchoring == "none" else None,
            replay=replay,
            verify=verified,
            journal_records=len(records),
            journal_records_at_backup=watermark.get("records"),
            stale_or_foreign_journal=stale,
        )
    timings["tombstones"] = round(time.monotonic() - mark, 3)

    mark = time.monotonic()
    checks["non_resurrection"] = _non_resurrection(staging_url, storage, records)
    if checks["non_resurrection"]["status"] == "REVIEW" and accept_held:
        checks["non_resurrection"]["status"] = "PASS"
        checks["non_resurrection"]["details"]["accepted_held"] = accept_held
    checks["objects"] = _objects(
        staging_url, storage, workspaces, catalog=catalog, accept_missing=accept_missing
    )
    checks["secrets"] = _secrets(catalog, settings, key)
    checks["post_backup_state"] = _post_backup_state(
        staging_url, revoke_share_links=revoke_share_links, now=datetime.now(UTC)
    )
    timings["reconcile"] = round(time.monotonic() - mark, 3)
    timings["total"] = round(time.monotonic() - started, 3)

    statuses = {name: check["status"] for name, check in checks.items()}
    if "FAIL" in statuses.values():
        overall = "FAIL"
    elif "REVIEW" in statuses.values():
        overall = "REVIEW_REQUIRED"
    else:
        overall = "PASS"
    overrides = {
        name: value
        for name, value in (
            ("accept_no_journal", accept_no_journal),
            ("accept_missing", accept_missing),
            ("accept_held", accept_held),
            ("revoke_share_links", revoke_share_links or None),
        )
        if value
    }
    report = _seal(
        {
            "kind": "homayar-reconciliation/1",
            "backup_id": backup_id,
            "manifest_mac": manifest["mac"],
            "backup_created_at": manifest["created_at"],
            "staging_database": staging_database,
            "started_at": started_at,
            "finished_at": _now(),
            "status": overall,
            "statuses": statuses,
            "owner_overrides": overrides,
            "checks": checks,
            "timings_seconds": timings,
        },
        key,
    )
    path = Path(work_dir) / REPORT_FILE
    temporary = path.with_name(REPORT_FILE + ".tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    cas_marker(
        admin_url,
        staging_database,
        reconciling,
        {
            "backup_id": backup_id,
            "manifest_mac": manifest["mac"],
            "state": "reconciled",
            "status": overall,
            "report_sha256": report_digest(path),
            "at": _now(),
        },
    )
    return {**report, "path": str(path)}


RETIRED_PREFIX = "homayar-retired "
RETIRED_DAYS = 7
_RETIRED_NAME = re.compile(r".+_retired_\d{14}")


def _database_is_empty(admin_url: str, database: str) -> bool:
    """No user relation in any schema (the database a postgres image creates)."""
    with connect(with_database(admin_url, database), autocommit=True) as connection:
        return not connection.execute(
            "SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname <> 'information_schema' AND n.nspname !~ '^pg_' "
            "AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f') "
            "LIMIT 1"
        ).fetchone()


def promote(
    *,
    admin_url: str,
    staging_database: str,
    live_database: str,
    report_path: Path,
    key: BackupKey,
    retire_existing: bool = False,
) -> dict[str, Any]:
    """Rename the reconciled staging database to the live name (PASS only).

    An existing live-name database is renamed ``<live>_retired_<ts>`` and
    marked with a ``drop_after`` deadline in the same transaction.  After the
    commit it is dropped only if it is empty (the one the postgres image
    creates), judged on the retired database itself; one with data is kept:
    it is outside backups, retention and tombstone replay, so the operator
    must ``drop-retired`` it by its deadline (``retired`` flags overdue ones).
    Promotes onto one live name are serialised by an advisory lock.
    """
    from backend.backup.restore import live_lock, validate_database_name

    staging = validate_database_name(staging_database)
    live = validate_database_name(live_database)
    report = load_report(report_path, key)
    if report.get("status") != "PASS":
        raise RestoreRefused("not_reconciled", f"reconciliation status is {report.get('status')}")
    if report.get("staging_database") != staging:
        raise RestoreRefused("report_mismatch", "the report belongs to another staging database")
    with staging_lock(admin_url, staging), live_lock(admin_url, live):
        marker = read_marker(admin_url, staging)
        if not isinstance(marker, dict) or marker.get("state") != "reconciled":
            raise RestoreRefused("staging_not_reconciled", f"{staging} is not in state reconciled")
        if (
            marker.get("backup_id") != report["backup_id"]
            or marker.get("report_sha256") != report_digest(report_path)
            or marker.get("manifest_mac") != report.get("manifest_mac")
        ):
            raise RestoreRefused(
                "report_mismatch", "the report is not the last reconciliation of this database"
            )
        live_exists = read_marker(admin_url, live) is not False
        if live_exists and not retire_existing:
            raise RestoreRefused(
                "live_database_exists", f"{live} exists; pass retire_existing to keep it renamed"
            )
        retired = None
        now = datetime.now(UTC)
        promoted_marker = {
            "backup_id": report["backup_id"],
            "manifest_mac": marker["manifest_mac"],
            "state": "promoted",
            "report_sha256": marker["report_sha256"],
            "promoted_from": staging,
            "at": _now(),
        }
        # Renames and markers commit together or not at all.  The replaced
        # database is marked retired before anything decides to drop it, so a
        # crash after the commit leaves it listed by ``retired``.
        with connect(with_database(admin_url, "postgres")) as connection:
            if live_exists:
                retired = f"{live[:40]}_retired_{now:%Y%m%d%H%M%S}"
                connection.execute(f'ALTER DATABASE "{live}" RENAME TO "{retired}"')
                note = {
                    "retired_at": now.isoformat(),
                    "drop_after": (now + timedelta(days=RETIRED_DAYS)).isoformat(),
                    "replaced_by_backup": report["backup_id"],
                }
                payload = RETIRED_PREFIX + json.dumps(note, sort_keys=True)
                connection.execute(
                    f'COMMENT ON DATABASE "{retired}" IS ' + "'" + payload.replace("'", "''") + "'"
                )
            connection.execute(f'ALTER DATABASE "{staging}" RENAME TO "{live}"')
            # The rename holds the database's lock: re-read the marker.
            current = connection.execute(
                "SELECT shobj_description(oid, 'pg_database') FROM pg_database WHERE datname = %s",
                (live,),
            ).fetchone()[0]
            if current != marker_payload(marker):
                connection.rollback()
                raise RestoreRefused("staging_changed", f"{staging} changed while promoting")
            connection.execute(marker_statement(live, promoted_marker))
            connection.commit()
        dropped = None
        drop_error = None
        if retired:
            # Judged now, on the retired name nothing serves any more; the
            # promote itself has committed whatever happens here.
            try:
                if _database_is_empty(admin_url, retired):
                    with connect(
                        with_database(admin_url, "postgres"), autocommit=True
                    ) as connection:
                        connection.execute(f'DROP DATABASE "{retired}"')
                    dropped, retired = retired, None
            except psycopg.Error as exc:
                drop_error = type(exc).__name__
    return {
        "backup_id": report["backup_id"],
        "live_database": live,
        "retired_database": retired,
        "dropped_empty_database": dropped,
        "retired_drop_error": drop_error,
    }


def _retired_entry(name: str, comment: str, now: datetime) -> dict[str, Any]:
    try:
        note = json.loads(comment[len(RETIRED_PREFIX) :])
    except ValueError:
        note = None
    if not isinstance(note, dict):
        return {"database": name, "malformed_note": True, "overdue": True}
    entry = {k: v for k, v in note.items() if k not in {"database", "overdue", "malformed_note"}}
    try:
        deadline = datetime.fromisoformat(str(note.get("drop_after")))
        overdue = deadline.tzinfo is None or now > deadline
    except ValueError:
        overdue = True
    # The real catalog name always wins over anything the comment says.
    return {**entry, "database": name, "overdue": overdue}


def list_retired(admin_url: str) -> list[dict[str, Any]]:
    """Databases promote kept, each flagged ``overdue`` once past its ``drop_after``."""
    with connect(with_database(admin_url, "postgres"), autocommit=True) as connection:
        rows = connection.execute(
            "SELECT datname, shobj_description(oid, 'pg_database') FROM pg_database "
            "ORDER BY datname"
        ).fetchall()
    now = datetime.now(UTC)
    return [
        _retired_entry(name, comment, now)
        for name, comment in rows
        if comment and comment.startswith(RETIRED_PREFIX)
    ]


def drop_retired(
    admin_url: str, database: str, *, confirm: str, live_database: str | None = None
) -> dict[str, Any]:
    """Drop one database promote retired, never the live or a staging one.

    The comment alone is not trusted (any database owner can write one): the
    name must have the retired form, must not be the live database (given, or
    the one the admin URL names) or a ``restoring`` staging database, and no
    session may be using it.
    """
    from backend.backup.restore import validate_database_name

    database = validate_database_name(database)
    if confirm != database:
        raise RestoreRefused("confirmation_mismatch", "--confirm must repeat the database name")
    protected = {database_name(admin_url), "postgres", "template0", "template1"}
    if live_database:
        protected.add(live_database)
    if database in protected:
        raise RestoreRefused("live_database", f"{database} is the live or a system database")
    if "restoring" in database:
        raise RestoreRefused("staging_database", f"{database} is a restore staging database")
    if not _RETIRED_NAME.fullmatch(database) or database not in {
        item["database"] for item in list_retired(admin_url)
    }:
        raise RestoreRefused("not_retired", f"{database} is not a database retired by promote")
    with connect(with_database(admin_url, "postgres"), autocommit=True) as connection:
        sessions = connection.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = %s", (database,)
        ).fetchone()[0]
        if sessions:
            raise RestoreRefused(
                "database_in_use", f"{database} has {sessions} session(s); it may be serving"
            )
        connection.execute(f'DROP DATABASE "{database}"')
    return {"dropped": database}


__all__ = ["BackupInvalid", "promote", "reconcile_restore", "load_report", "bound_database"]
