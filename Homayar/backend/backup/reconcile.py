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
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from psycopg import sql

from backend.backup.crypto import BackupKey
from backend.backup.logical import (
    alembic_revisions,
    connect,
    list_tables,
    table_columns,
    with_database,
)
from backend.backup.manifest import BackupInvalid, read_manifest
from backend.backup.release import code_heads
from backend.backup.restore import RestoreRefused, read_marker, read_state, write_marker
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
        per_tenant_tables_verified_at_load=load.get("tenant_tables_verified", 0),
        foreign_keys_verified_at_load=bool(load.get("foreign_keys_verified")),
        problems=problems[:50],
    )


def _references(connection) -> dict[str, dict[str, Any]]:
    """Live expectations by key in the restored database (after the replay)."""
    refs: dict[str, dict[str, Any]] = {}
    present = set(list_tables(connection))
    if "media_objects" in present:
        for key, workspace_id, meeting_id, state in connection.execute(
            "SELECT storage_key, workspace_id, meeting_id, lifecycle_state FROM media_objects"
        ):
            required = str(state).upper() in {"ACTIVE", "RETENTION_PENDING", "LEGAL_HOLD"}
            gone = str(state).upper() == "DELETED"
            refs[key] = {
                "workspace_id": workspace_id,
                "meeting_id": meeting_id,
                "expectation": "required" if required else ("gone" if gone else "optional"),
            }
    if "recordings" in present:
        for key, workspace_id, meeting_id, deleted in connection.execute(
            "SELECT storage_key, workspace_id, meeting_id, deleted_at IS NOT NULL FROM recordings"
        ):
            if key in refs:
                continue
            refs[key] = {
                "workspace_id": workspace_id,
                "meeting_id": meeting_id,
                "expectation": "optional" if deleted else "required",
            }
    if "stage_checkpoints" in present:
        for key, workspace_id, meeting_id, state, removed in connection.execute(
            "SELECT artifact_key, workspace_id, meeting_id, state, object_deleted_at IS NOT NULL "
            "FROM stage_checkpoints"
        ):
            expectation = (
                "gone"
                if removed
                else ("required" if str(state).upper() == "PUBLISHED" else "optional")
            )
            if key not in refs or expectation == "required":
                refs[key] = {
                    "workspace_id": workspace_id,
                    "meeting_id": meeting_id,
                    "expectation": expectation,
                }
    return refs


def _objects(
    admin_url: str,
    storage: ObjectStorage | None,
    workspaces: list[str],
    *,
    accept_missing: str | None,
) -> dict[str, Any]:
    if storage is None:
        return _check("WARN", note="object store not restored (no storage configured)")
    with connect(admin_url) as connection:
        refs = _references(connection)
        connection.rollback()
    stored: dict[str, str] = {}
    for workspace_id in sorted(workspaces):
        for listing in storage.iter_objects(workspace_id):
            stored[listing.key] = workspace_id
    missing = sorted(
        k for k, r in refs.items() if r["expectation"] == "required" and k not in stored
    )
    orphans = sorted(k for k in stored if k not in refs)
    lingering = sorted(k for k, r in refs.items() if r["expectation"] == "gone" and k in stored)
    status = "PASS"
    if missing:
        status = "PASS" if accept_missing else "FAIL"
    return _check(
        status,
        stored=len(stored),
        referenced=len(refs),
        required_missing=len(missing),
        required_missing_keys=missing[:100],
        orphans=len(orphans),
        orphan_keys=orphans[:100],
        removed_but_present=len(lingering),
        removed_but_present_keys=lingering[:100],
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
                continue  # the backup predates the conversation: nothing came back
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
            if storage is not None:
                for key in record.object_keys:
                    try:
                        if storage.exists(record.workspace_id, key):
                            bytes_left.append(key)
                    except StorageError:
                        bytes_left.append(key)
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


def reconcile_restore(
    *,
    set_dir: Path,
    key: BackupKey,
    admin_url: str,
    staging_database: str,
    settings: Any,
    storage: ObjectStorage,
    work_dir: Path,
    app_role: str = APP_ROLE,
    accept_held: str | None = None,
    accept_missing: str | None = None,
    accept_no_journal: str | None = None,
    revoke_share_links: bool = False,
    max_tenants: int = 500,
) -> dict[str, Any]:
    """Run every check, replay the tombstones, write the sealed report."""
    from backend.jobs.tombstone_reapply import reapply_tombstones, verify_tombstones
    from backend.services.tombstone_journal import journal_for

    started = time.monotonic()
    started_at = _now()
    manifest = read_manifest(set_dir)
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
        or marker.get("state") not in {"loaded", "reconciled"}
    ):
        raise RestoreRefused(
            "staging_not_loaded", f"{staging_database} is not a loaded restore of {backup_id}"
        )
    if state.get("backup_id") != backup_id or state.get("staging_database") != staging_database:
        raise RestoreRefused("state_mismatch", "the work dir belongs to another restore")
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
    if journal is None:
        checks["tombstones"] = _check(
            "PASS" if accept_no_journal else "FAIL",
            note=(
                "TOMBSTONE_JOURNAL_DIR is not configured: deletions after the backup "
                "cannot be re-applied"
            ),
            accepted=accept_no_journal,
        )
    else:
        with bound_database(staging_settings):
            replay = reapply_tombstones(staging_settings, journal=journal, storage=storage)
            verified = verify_tombstones(staging_settings, journal=journal)
        records, _ = journal.read()
        failed = verified["alive"] or verified["rejected_lines"] or verified["purges_failed"]
        checks["tombstones"] = _check("FAIL" if failed else "PASS", replay=replay, verify=verified)
    timings["tombstones"] = round(time.monotonic() - mark, 3)

    mark = time.monotonic()
    checks["non_resurrection"] = _non_resurrection(staging_url, storage, records)
    if checks["non_resurrection"]["status"] == "REVIEW" and accept_held:
        checks["non_resurrection"]["status"] = "PASS"
        checks["non_resurrection"]["details"]["accepted_held"] = accept_held
    checks["objects"] = _objects(staging_url, storage, workspaces, accept_missing=accept_missing)
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
    report = _seal(
        {
            "kind": "homayar-reconciliation/1",
            "backup_id": backup_id,
            "backup_created_at": manifest["created_at"],
            "staging_database": staging_database,
            "started_at": started_at,
            "finished_at": _now(),
            "status": overall,
            "statuses": statuses,
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
    write_marker(
        admin_url,
        staging_database,
        {
            "backup_id": backup_id,
            "state": "reconciled",
            "status": overall,
            "report_sha256": report_digest(path),
            "at": _now(),
        },
    )
    return {**report, "path": str(path)}


def promote(
    *,
    admin_url: str,
    staging_database: str,
    live_database: str,
    report_path: Path,
    key: BackupKey,
    retire_existing: bool = False,
) -> dict[str, Any]:
    """Rename the reconciled staging database to the live name (PASS only)."""
    from backend.backup.restore import validate_database_name

    staging = validate_database_name(staging_database)
    live = validate_database_name(live_database)
    report = load_report(report_path, key)
    if report.get("status") != "PASS":
        raise RestoreRefused("not_reconciled", f"reconciliation status is {report.get('status')}")
    if report.get("staging_database") != staging:
        raise RestoreRefused("report_mismatch", "the report belongs to another staging database")
    marker = read_marker(admin_url, staging)
    if not isinstance(marker, dict) or marker.get("state") != "reconciled":
        raise RestoreRefused("staging_not_reconciled", f"{staging} is not in state reconciled")
    if marker.get("backup_id") != report["backup_id"] or marker.get(
        "report_sha256"
    ) != report_digest(report_path):
        raise RestoreRefused(
            "report_mismatch", "the report is not the last reconciliation of this database"
        )
    retired = None
    with connect(with_database(admin_url, "postgres"), autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (live,)
        ).fetchone()
        if exists:
            if not retire_existing:
                raise RestoreRefused(
                    "live_database_exists",
                    f"{live} exists; pass retire_existing to keep it renamed",
                )
            retired = f"{live[:40]}_retired_{datetime.now(UTC):%Y%m%d%H%M%S}"
            connection.execute(f'ALTER DATABASE "{live}" RENAME TO "{retired}"')
        connection.execute(f'ALTER DATABASE "{staging}" RENAME TO "{live}"')
    write_marker(
        admin_url,
        live,
        {
            "backup_id": report["backup_id"],
            "state": "promoted",
            "report_sha256": marker["report_sha256"],
            "promoted_from": staging,
            "at": _now(),
        },
    )
    return {"backup_id": report["backup_id"], "live_database": live, "retired_database": retired}


__all__ = ["BackupInvalid", "promote", "reconcile_restore", "load_report", "bound_database"]
