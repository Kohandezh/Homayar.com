"""Protected audit sink: hash-chained export and verification (WP20-T03, HOM-115; ADR 0036).

``audit_events`` is append-only for the application (no UPDATE/DELETE privilege,
ORM guard).  The table owner and a superuser can still change rows, so the sink
keeps an independent, tamper-evident copy **outside the application's reach**::

    python -m backend.observability.audit_sink export          # append settled events
    python -m backend.observability.audit_sink export --legacy-review REF  # first, staging/prod
    python -m backend.observability.audit_sink verify          # chain + database
    python -m backend.observability.audit_sink verify --sink-only
    python -m backend.observability.audit_sink record-erasure --event-id ID --approval REF

Layout of ``AUDIT_SINK_DIR`` (a location the application containers do not mount:
a separate host or volume, ideally write-once storage)::

    segments/00000001.jsonl          one canonical JSON record per line, never rewritten
    segments/00000001.manifest.json  counts, window, first/last sequence, chain heads
    HEAD.json                        last sequence, chain head, export watermark

Every record carries ``seq`` (global, contiguous from 1), its ``type`` (``event``: an
audit row's columns as stored; ``erasure``: an owner-approved erasure of one row;
``legacy_review``: the acknowledgement of the legacy-row review, see below),
the previous record's hash and its own hash ``H(prev || canonical record)``, where H
is HMAC-SHA256 under ``AUDIT_SINK_HMAC_KEY`` (plain SHA-256 without a key, accepted
only in test and development).

``verify`` detects, in the sink: an altered, inserted, removed or reordered record
(``record_altered``, ``sequence_gap``), a missing segment, a manifest or ``HEAD.json``
that disagrees with the chain, and a chain made with another key; against the
database: a row changed or deleted after export (``row_changed``, ``row_deleted``)
and a row older than the export watermark that never reached the sink
(``unexported_row``: inserted late or back-dated).  Expected changes are notices,
listed and counted but not failures: ``referential_null`` (the workspace or user was
deleted: ``ON DELETE SET NULL``) and ``accepted_erasure`` (an erasure recorded in the
chain before the DBA ran it).

It is tamper-*evident*, not tamper-proof: whoever holds both write access to the
sink and the HMAC key can rewrite it undetected, and cutting the newest segments
together with ``HEAD.json`` is visible only against an independent copy of the head
(``head_hash`` in every export and verify output).  Keep the key with the operator,
never in the application's secrets.

The exporter reads through its own login (``AUDIT_EXPORT_DATABASE_URL``) and
``SET ROLE homayar_audit_reader`` in read-only transactions: SELECT on
``audit_events`` only, across tenants by a policy granted to that role alone.  It
refuses a login that is (or can become) ``homayar_app``.  Events younger than
``AUDIT_EXPORT_SETTLE_SECONDS`` wait for the next run, so a transaction that commits
late is not skipped.

Rows written before HOM-114 masking may hold unmasked free text.  In staging and
production the first export into an empty sink therefore refuses to run until the
legacy-row review (``docs/operations/audit.md#legacy-row-review``) is acknowledged with
``--legacy-review <approval reference>``; the reference becomes record 1 of the chain
and is copied into the first manifest and ``HEAD.json``, and ``verify`` reports it.
Test and development are not gated.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import hmac
import itertools
import json
import os
import re
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from backend.config import Settings, get_settings
from backend.database.rls import APP_ROLE, AUDIT_READER_ROLE
from backend.jobs.leases import as_utc

SINK_FORMAT = 1
GENESIS = "0" * 64
BATCH_SIZE = 1000
RECORD_COLUMNS: tuple[str, ...] = (
    "id",
    "occurred_at",
    "event_type",
    "actor_user_id",
    "workspace_id",
    "target_type",
    "target_id",
    "outcome",
    "request_id",
    "ip_hash",
    "details",
)
# Columns PostgreSQL sets to NULL on its own (ON DELETE SET NULL).
REFERENTIAL_COLUMNS = frozenset({"workspace_id", "actor_user_id"})
EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,36}$")
# An approval is a reference (ticket, change id), never free text.
APPROVAL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/#-]{2,79}$")
KEY_ID_LABEL = b"homayar-audit-sink/key-id/v1"
HEAD_MAC_LABEL = "HEAD.json"
UNKEYED_ENVIRONMENTS = frozenset({"test", "development"})
# Where the first export into an empty sink needs the legacy-row review acknowledged.
LEGACY_REVIEW_ENVIRONMENTS = frozenset({"staging", "production"})
LEGACY_REVIEW_RUNBOOK = "docs/operations/audit.md#legacy-row-review"

EVENT = "event"
ERASURE = "erasure"
LEGACY_REVIEW = "legacy_review"


class AuditSinkError(RuntimeError):
    """Configuration or consistency error; messages never carry URLs or keys."""


# --- canonical records -------------------------------------------------------------


def _iso(value: datetime) -> str:
    moment = as_utc(value)
    if moment is None:  # occurred_at is NOT NULL
        raise AuditSinkError("audit row without occurred_at")
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def record_from_row(row: Any) -> dict[str, Any]:
    record = {column: getattr(row, column) for column in RECORD_COLUMNS}
    record["occurred_at"] = _iso(record["occurred_at"])
    record["details"] = record["details"] or {}
    return record


class Chain:
    """``H(prev || canonical)``: HMAC-SHA256 with a key, SHA-256 without."""

    def __init__(self, key: str = "") -> None:
        self._key = key.encode("utf-8") if key else b""
        self.algorithm = "hmac-sha256" if key else "sha256"
        # Names the key without revealing it, so a wrong key is reported as such.
        self.key_id = (
            hmac.new(self._key, KEY_ID_LABEL, hashlib.sha256).hexdigest()[:16] if key else None
        )

    def link(self, previous: str, body: str) -> str:
        payload = previous.encode("ascii") + b"\n" + body.encode("utf-8")
        if self._key:
            return hmac.new(self._key, payload, hashlib.sha256).hexdigest()
        return hashlib.sha256(payload).hexdigest()


def sink_chain(settings: Settings) -> Chain:
    if not settings.audit_sink_hmac_key and settings.environment not in UNKEYED_ENVIRONMENTS:
        raise AuditSinkError(
            "AUDIT_SINK_HMAC_KEY is required outside test and development: an unkeyed "
            "chain can be recomputed by anyone who can write the sink"
        )
    return Chain(settings.audit_sink_hmac_key)


# --- sink files ----------------------------------------------------------------------


@dataclass
class Head:
    last_seq: int = 0
    head_hash: str = GENESIS
    watermark: str | None = None  # ISO time; every event before it has been exported
    segments: int = 0
    algorithm: str = "sha256"
    key_id: str | None = None
    legacy_review: dict[str, str] | None = None  # copy of the chain's record 1, if any
    mac: str | None = None  # as read from HEAD.json

    def fields(self) -> dict[str, Any]:
        return {
            "format": SINK_FORMAT,
            "last_seq": self.last_seq,
            "head_hash": self.head_hash,
            "watermark": self.watermark,
            "segments": self.segments,
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "legacy_review": self.legacy_review,
        }

    def expected_mac(self, chain: Chain) -> str:
        """Binds every field, the watermark included (it is not in the chain), to the key."""
        return chain.link(HEAD_MAC_LABEL, canonical(self.fields()))

    def to_json(self, chain: Chain) -> str:
        return canonical({**self.fields(), "mac": self.expected_mac(chain)}) + "\n"


def _segments_dir(sink: Path) -> Path:
    return sink / "segments"


def _segment_paths(sink: Path, number: int) -> tuple[Path, Path, Path]:
    stem = f"{number:08d}"
    directory = _segments_dir(sink)
    return (
        directory / f"{stem}.jsonl",
        directory / f"{stem}.manifest.json",
        directory / f"{stem}.jsonl.partial",
    )


def read_head(sink: Path) -> Head | None:
    path = sink / "HEAD.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return Head(
        last_seq=int(data["last_seq"]),
        head_hash=str(data["head_hash"]),
        watermark=data.get("watermark"),
        segments=int(data["segments"]),
        algorithm=str(data.get("algorithm", "sha256")),
        key_id=data.get("key_id"),
        legacy_review=data.get("legacy_review"),
        mac=data.get("mac"),
    )


def sink_is_empty(sink: Path) -> bool:
    """No committed head and no half-committed first segment."""
    return not (sink / "HEAD.json").exists() and not _segment_paths(sink, 1)[1].exists()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new(path: Path, content: str) -> None:
    """Create ``path`` (never overwrite) read-only and fsync it."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o440)
    try:
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace(path: Path, content: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    try:
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sink_dir(settings: Settings) -> Path:
    if not settings.audit_sink_dir:
        raise AuditSinkError("AUDIT_SINK_DIR is not configured")
    return Path(settings.audit_sink_dir)


@contextmanager
def _sink_lock(sink: Path) -> Iterator[None]:
    """One writer at a time (export or record-erasure)."""
    _segments_dir(sink).mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor = os.open(sink / ".lock", os.O_WRONLY | os.O_CREAT, 0o640)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise AuditSinkError("another export or erasure record is running") from exc
        yield
    finally:
        os.close(descriptor)


def _open_head(sink: Path, chain: Chain) -> Head:
    """The committed head, after finishing an export interrupted between its steps."""
    stored = read_head(sink)
    head = stored or Head(algorithm=chain.algorithm, key_id=chain.key_id)
    if head.algorithm != chain.algorithm or head.key_id != chain.key_id:
        raise AuditSinkError(
            f"The sink chain uses {head.algorithm} with key id {head.key_id}; the configured "
            f"AUDIT_SINK_HMAC_KEY gives {chain.algorithm} with key id {chain.key_id}"
        )
    if stored is not None and stored.mac != stored.expected_mac(chain):
        raise AuditSinkError(
            "HEAD.json fails its MAC (changed without the key); the sink needs an "
            "operator's inspection: run verify"
        )
    segment, manifest_path, partial = _segment_paths(sink, head.segments + 1)
    if not manifest_path.is_file():
        return head  # nothing half-committed (a stale partial is simply rewritten)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = segment if segment.is_file() else partial
    if not (
        source.is_file()
        and manifest.get("file_sha256") == _sha256_file(source)
        and manifest.get("prev_head") == head.head_hash
        and manifest.get("first_seq") == head.last_seq + 1
    ):
        raise AuditSinkError(
            f"Segment {head.segments + 1} has a manifest without a matching segment file; "
            "the sink needs an operator's inspection"
        )
    if source is partial:
        os.replace(partial, segment)
        os.chmod(segment, 0o440)
        _fsync_directory(segment.parent)
    head = Head(
        last_seq=int(manifest["last_seq"]),
        head_hash=str(manifest["head_hash"]),
        watermark=manifest.get("window_end") or head.watermark,
        segments=head.segments + 1,
        algorithm=chain.algorithm,
        key_id=chain.key_id,
        legacy_review=manifest.get("legacy_review") or head.legacy_review,
    )
    _replace(sink / "HEAD.json", head.to_json(chain))
    return head


def _append_segment(
    sink: Path,
    head: Head,
    chain: Chain,
    records: Iterator[dict[str, Any]],
    *,
    window: tuple[str | None, str] | None,
) -> tuple[Head, dict[str, int]]:
    """Write ``records`` as the next segment and commit it; returns the new head and
    the number of records of each type.

    Order: segment written as ``.partial`` and fsynced, then its manifest (created
    exclusively), then the rename to ``.jsonl``, then ``HEAD.json``.  An interruption
    leaves either nothing committed or a manifest that ``_open_head`` rolls forward.
    """
    number = head.segments + 1
    segment, manifest_path, partial = _segment_paths(sink, number)
    if segment.exists() or manifest_path.exists():
        raise AuditSinkError(f"Segment {number} already exists; the sink is inconsistent")
    previous, seq = head.head_hash, head.last_seq
    counts = {EVENT: 0, ERASURE: 0, LEGACY_REVIEW: 0}
    review: dict[str, str] | None = None
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o640)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        for record in records:
            seq += 1
            body = canonical({"seq": seq, **record})
            digest = chain.link(previous, body)
            handle.write(canonical({"seq": seq, **record, "prev": previous, "hash": digest}))
            handle.write("\n")
            previous = digest
            counts[record["type"]] += 1
            if record["type"] == LEGACY_REVIEW:
                review = record[LEGACY_REVIEW]
        handle.flush()
        os.fsync(handle.fileno())
    count = sum(counts.values())
    if count == 0:
        partial.unlink()
        if window is not None:
            head.watermark = window[1]
            _replace(sink / "HEAD.json", head.to_json(chain))
        return head, counts
    manifest = {
        "format": SINK_FORMAT,
        "segment": number,
        "file": segment.name,
        "file_sha256": _sha256_file(partial),
        "first_seq": head.last_seq + 1,
        "last_seq": seq,
        "count": count,
        "events": counts[EVENT],
        "erasures": counts[ERASURE],
        "legacy_review": review,
        "window_start": window[0] if window else None,
        "window_end": window[1] if window else None,
        "prev_head": head.head_hash,
        "head_hash": previous,
        "algorithm": chain.algorithm,
        "key_id": chain.key_id,
    }
    _write_new(manifest_path, canonical(manifest) + "\n")
    os.replace(partial, segment)
    os.chmod(segment, 0o440)
    _fsync_directory(segment.parent)
    new_head = Head(
        last_seq=seq,
        head_hash=previous,
        watermark=window[1] if window else head.watermark,
        segments=number,
        algorithm=chain.algorithm,
        key_id=chain.key_id,
        legacy_review=review or head.legacy_review,
    )
    _replace(sink / "HEAD.json", new_head.to_json(chain))
    return new_head, counts


# --- database access ------------------------------------------------------------------


def export_engine(settings: Settings) -> Engine:
    """The exporter's own connection: never the application role on PostgreSQL."""
    url = settings.audit_export_database_url
    if not url:
        if make_url(settings.database_url).get_backend_name() == "postgresql":
            raise AuditSinkError(
                "AUDIT_EXPORT_DATABASE_URL is required on PostgreSQL: a login that is a "
                f"member of {AUDIT_READER_ROLE}, never the application's login"
            )
        if settings.environment not in UNKEYED_ENVIRONMENTS:
            raise AuditSinkError("The audit sink reads PostgreSQL outside test and development")
        url = settings.database_url  # SQLite development database: no roles
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "postgresql":

        @event.listens_for(engine, "connect")
        def _reader_role(dbapi_connection: Any, _record: Any) -> None:
            with dbapi_connection.cursor() as cursor:
                cursor.execute(f"SET ROLE {AUDIT_READER_ROLE}")
                cursor.execute("SET default_transaction_read_only = on")
            dbapi_connection.commit()

    return engine


def assert_reader_identity(session: Session) -> None:
    """Refuse to read unless this is the reader role, inert (``NOLOGIN``, no role
    attribute, a member of no role, unreachable from the application role), reached
    from a login that is neither a superuser nor able to become the application role."""
    if session.get_bind().dialect.name != "postgresql":
        return
    row = session.execute(
        text(
            "SELECT current_user AS acting_role, "
            "s.rolsuper OR s.rolbypassrls AS privileged, "
            "EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :app) "
            "AND pg_has_role(session_user, :app, 'MEMBER') AS app_member, "
            "current_setting('transaction_read_only') AS read_only "
            "FROM pg_roles s WHERE s.rolname = session_user"
        ),
        {"app": APP_ROLE},
    ).one()
    if row.acting_role != AUDIT_READER_ROLE:
        raise AuditSinkError(f"The audit export must run as {AUDIT_READER_ROLE}")
    reader = session.execute(
        text(
            "SELECT r.rolcanlogin OR r.rolsuper OR r.rolbypassrls OR r.rolcreatedb "
            "OR r.rolcreaterole AS privileged, "
            "EXISTS (SELECT 1 FROM pg_auth_members m WHERE m.member = r.oid) AS member_of, "
            "EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :app) "
            "AND pg_has_role(:app, r.rolname, 'MEMBER') AS app_reaches "
            "FROM pg_roles r WHERE r.rolname = :reader"
        ),
        {"app": APP_ROLE, "reader": AUDIT_READER_ROLE},
    ).one()
    if reader.privileged or reader.member_of or reader.app_reaches:
        raise AuditSinkError(
            f"{AUDIT_READER_ROLE} is not inert: it can log in, holds a role attribute, is a "
            f"member of another role, or {APP_ROLE} reaches it; upgrade the schema again or "
            "fix the role (docs/operations/audit.md#the-audit-reader-role)"
        )
    if row.privileged or row.app_member:
        raise AuditSinkError(
            f"The audit export login must not be a superuser, have BYPASSRLS or be a "
            f"member of {APP_ROLE}; give the exporter its own login"
        )
    if row.read_only != "on":
        raise AuditSinkError("The audit export transaction is not read-only")


@contextmanager
def _reader_session(settings: Settings, engine: Engine | None) -> Iterator[Session]:
    owned = engine is None
    engine = engine or export_engine(settings)
    try:
        try:
            session = Session(engine)
            assert_reader_identity(session)
        except DBAPIError as exc:
            # e.g. "permission denied to set role": the login is not a reader member.
            raise AuditSinkError(
                f"The audit export login cannot read as {AUDIT_READER_ROLE} "
                f"({type(exc.orig).__name__ if exc.orig else type(exc).__name__})"
            ) from None
        try:
            yield session
        finally:
            session.rollback()
            session.close()
    finally:
        if owned:
            engine.dispose()


def _audit_columns() -> tuple[Any, list[Any]]:
    from backend.database.saas_models import AuditEvent

    table = AuditEvent.__table__
    return table, [table.c[column] for column in RECORD_COLUMNS]


def _rows(
    session: Session, *, start: datetime | None, end: datetime | None
) -> Iterator[dict[str, Any]]:
    """Audit rows in ``[start, end)`` by (occurred_at, id), read in batches (Core rows)."""
    table, columns = _audit_columns()
    query = select(*columns).order_by(table.c.occurred_at.asc(), table.c.id.asc())
    if start is not None:
        query = query.where(table.c.occurred_at >= start)
    if end is not None:
        query = query.where(table.c.occurred_at < end)
    for row in session.execute(query.execution_options(yield_per=BATCH_SIZE)):
        yield record_from_row(row)


def _rows_by_id(session: Session, ids: list[str]) -> dict[str, dict[str, Any]]:
    table, columns = _audit_columns()
    found: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(ids), BATCH_SIZE):
        chunk = ids[offset : offset + BATCH_SIZE]
        for row in session.execute(select(*columns).where(table.c.id.in_(chunk))):
            found[row.id] = record_from_row(row)
    return found


def _database_now(session: Session) -> datetime:
    if session.get_bind().dialect.name == "postgresql":
        moment = as_utc(session.execute(text("SELECT now()")).scalar_one())
        if moment is not None:
            return moment
    return datetime.now(UTC)


# --- export and erasure records ---------------------------------------------------------


@dataclass
class ExportResult:
    exported: int
    segment: int | None
    last_seq: int
    watermark: str | None
    head_hash: str
    legacy_review: str | None = None  # the approval reference this export recorded

    def as_dict(self) -> dict[str, Any]:
        values = {
            "exported": self.exported,
            "segment": self.segment,
            "last_seq": self.last_seq,
            "watermark": self.watermark,
            "head_hash": self.head_hash,
        }
        if self.legacy_review:
            values["legacy_review"] = self.legacy_review
        return values


def _check_legacy_review(settings: Settings, sink: Path, legacy_review: str | None) -> bool:
    """Whether this export records the legacy-row review; refuses before any write.

    The first export into an empty sink in staging or production needs the review
    acknowledged by an approval reference: rows written before HOM-114 masking are
    reviewed before the first export, decided (owner, 2026-10-18).  Later exports
    need nothing, and the reference cannot be recorded twice.
    """
    if legacy_review is not None and not APPROVAL_PATTERN.fullmatch(legacy_review):
        raise AuditSinkError(
            "--legacy-review must be an approval reference (letters, digits and ._:/#-, "
            "3-80 characters), not free text"
        )
    empty = sink_is_empty(sink)
    if legacy_review is not None and not empty:
        raise AuditSinkError(
            "--legacy-review is recorded once, by the first export into an empty sink; "
            "this sink already has one"
        )
    if empty and legacy_review is None and settings.environment in LEGACY_REVIEW_ENVIRONMENTS:
        raise AuditSinkError(
            f"The first export into an empty sink in {settings.environment} needs the "
            "legacy-row review: review the rows written before HOM-114 masking "
            f"({LEGACY_REVIEW_RUNBOOK}), then run export --legacy-review <approval reference>"
        )
    return legacy_review is not None


def export_events(
    settings: Settings | None = None,
    *,
    sink: Path | None = None,
    engine: Engine | None = None,
    now: datetime | None = None,
    legacy_review: str | None = None,
) -> ExportResult:
    """Append every settled event after the watermark as one new segment.

    ``legacy_review`` is the approval reference of the legacy-row review; required for
    the first export into an empty sink in staging and production, refused afterwards.
    """
    settings = settings or get_settings()
    sink = sink or sink_dir(settings)
    chain = sink_chain(settings)
    _check_legacy_review(settings, sink, legacy_review)  # before anything is written
    with _sink_lock(sink):
        reviewed = _check_legacy_review(settings, sink, legacy_review)  # under the lock
        head = _open_head(sink, chain)
        with _reader_session(settings, engine) as session:
            current = as_utc(now) or _database_now(session)
            end = current - timedelta(seconds=settings.audit_export_settle_seconds)
            start = datetime.fromisoformat(head.watermark) if head.watermark else None
            if start is not None and end <= start:
                return ExportResult(0, None, head.last_seq, head.watermark, head.head_hash)
            first: list[dict[str, Any]] = []
            if reviewed:
                review = {
                    "approval": legacy_review,
                    "environment": settings.environment,
                    "recorded_at": _iso(datetime.now(UTC)),
                }
                first.append({"type": LEGACY_REVIEW, LEGACY_REVIEW: review})
            rows = ({"type": EVENT, "row": row} for row in _rows(session, start=start, end=end))
            head, counts = _append_segment(
                sink,
                head,
                chain,
                itertools.chain(first, rows),
                window=(head.watermark, end.isoformat()),
            )
    return ExportResult(
        counts[EVENT],
        head.segments if sum(counts.values()) else None,
        head.last_seq,
        head.watermark,
        head.head_hash,
        legacy_review if reviewed else None,
    )


def _refuse_erasure_before_first_export(settings: Settings, sink: Path) -> None:
    # An erasure record would create the sink's head and skip the legacy-row review.
    if settings.environment in LEGACY_REVIEW_ENVIRONMENTS and sink_is_empty(sink):
        raise AuditSinkError(
            "The sink is empty: run the first export (export --legacy-review) before "
            "recording an erasure; rows erased before the first export never reach the "
            f"sink and belong to the legacy-row review ({LEGACY_REVIEW_RUNBOOK})"
        )


def record_erasure(
    settings: Settings | None = None,
    *,
    event_id: str,
    approval: str,
    sink: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Append one owner-approved erasure to the chain (before the DBA runs it).

    Returns the record's sequence number.  ``verify`` then reports the row as
    ``accepted_erasure`` (a notice, never hidden) instead of ``row_changed`` or
    ``row_deleted``.
    """
    if not EVENT_ID_PATTERN.fullmatch(event_id):
        raise AuditSinkError("--event-id must be an audit event id")
    if not APPROVAL_PATTERN.fullmatch(approval):
        raise AuditSinkError(
            "--approval must be a reference (letters, digits and ._:/#-, 3-80 characters), "
            "not free text"
        )
    settings = settings or get_settings()
    sink = sink or sink_dir(settings)
    chain = sink_chain(settings)
    _refuse_erasure_before_first_export(settings, sink)
    recorded_at = _iso(now or datetime.now(UTC))
    with _sink_lock(sink):
        _refuse_erasure_before_first_export(settings, sink)
        head = _open_head(sink, chain)
        record = {
            "type": ERASURE,
            "erasure": {"event_id": event_id, "approval": approval, "recorded_at": recorded_at},
        }
        head, _counts = _append_segment(sink, head, chain, iter([record]), window=None)
    return head.last_seq


# --- verification ---------------------------------------------------------------------


@dataclass
class Finding:
    kind: str
    seq: int | None = None
    event_id: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        values = {"kind": self.kind, "seq": self.seq, "event_id": self.event_id}
        values["detail"] = self.detail
        return {key: value for key, value in values.items() if value not in (None, "")}


@dataclass
class VerifyReport:
    records: int = 0
    erasures: int = 0
    segments: int = 0
    last_seq: int = 0
    head_hash: str = GENESIS
    against_database: bool = False
    legacy_review: dict[str, str] | None = None
    problems: list[Finding] = field(default_factory=list)
    notices: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def kinds(self) -> list[str]:
        return [finding.kind for finding in (*self.problems, *self.notices)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "records": self.records,
            "erasures": self.erasures,
            "segments": self.segments,
            "last_seq": self.last_seq,
            "head_hash": self.head_hash,
            "against_database": self.against_database,
            "legacy_review": self.legacy_review,
            "problems": [finding.as_dict() for finding in self.problems],
            "notices": [finding.as_dict() for finding in self.notices],
        }


@dataclass
class _SinkContents:
    head: Head | None = None
    events: dict[str, dict[str, Any]] = field(default_factory=dict)
    erasures: dict[str, str] = field(default_factory=dict)  # event id -> approval


def _read_sink(sink: Path, chain: Chain, report: VerifyReport) -> _SinkContents:
    """Every record of the sink in order, checking the chain on the way."""
    contents = _SinkContents(head=read_head(sink))
    head = contents.head
    if head is None:
        report.problems.append(Finding("head_missing", detail="HEAD.json is missing"))
        return contents
    if head.algorithm != chain.algorithm or head.key_id != chain.key_id:
        report.problems.append(
            Finding(
                "key_mismatch",
                detail=f"sink {head.algorithm}/{head.key_id}, configured "
                f"{chain.algorithm}/{chain.key_id}",
            )
        )
        return contents
    if head.mac != head.expected_mac(chain):
        report.problems.append(
            Finding("head_mismatch", detail="HEAD.json fails its MAC (changed without the key)")
        )
    previous, expected_seq, last_window_end = GENESIS, 1, None
    for number in range(1, head.segments + 1):
        segment, manifest_path, _ = _segment_paths(sink, number)
        if not segment.is_file() or not manifest_path.is_file():
            report.problems.append(Finding("segment_missing", detail=f"segment {number}"))
            return contents
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        report.segments += 1
        if manifest.get("window_end"):
            last_window_end = manifest["window_end"]
        if manifest.get("prev_head") != previous or manifest.get("first_seq") != expected_seq:
            report.problems.append(Finding("manifest_mismatch", detail=f"segment {number}"))
        if manifest.get("file_sha256") != _sha256_file(segment):
            report.problems.append(Finding("segment_file_changed", detail=f"segment {number}"))
        count, segment_review = 0, None
        with segment.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    stored = json.loads(line)
                except ValueError:
                    report.problems.append(Finding("record_altered", seq=expected_seq))
                    return contents
                seq = stored.get("seq") if isinstance(stored, dict) else None
                if seq != expected_seq:
                    report.problems.append(
                        Finding("sequence_gap", seq=expected_seq, detail=f"found {seq}")
                    )
                    return contents
                claimed_prev, claimed_hash = stored.pop("prev", None), stored.pop("hash", None)
                if claimed_prev != previous or chain.link(previous, canonical(stored)) != (
                    claimed_hash
                ):
                    row = stored.get("row") if isinstance(stored.get("row"), dict) else {}
                    report.problems.append(
                        Finding("record_altered", seq=seq, event_id=row.get("id"))
                    )
                    return contents
                previous, expected_seq, count = claimed_hash, expected_seq + 1, count + 1
                if stored.get("type") == EVENT:
                    row = stored["row"]
                    if row["id"] in contents.events:
                        report.problems.append(
                            Finding("record_duplicated", seq=seq, event_id=row["id"])
                        )
                    contents.events[row["id"]] = row
                    report.records += 1
                elif stored.get("type") == ERASURE:
                    erasure = stored["erasure"]
                    contents.erasures[erasure["event_id"]] = erasure["approval"]
                    report.erasures += 1
                elif stored.get("type") == LEGACY_REVIEW:
                    segment_review = stored[LEGACY_REVIEW]
                    report.legacy_review = segment_review
        if (
            count != manifest.get("count")
            or manifest.get("last_seq") != expected_seq - 1
            or manifest.get("head_hash") != previous
            or manifest.get("legacy_review") != segment_review
        ):
            report.problems.append(Finding("manifest_mismatch", detail=f"segment {number}"))
    report.last_seq, report.head_hash = expected_seq - 1, previous
    if (
        head.last_seq != expected_seq - 1
        or head.head_hash != previous
        or head.legacy_review != report.legacy_review
    ):
        report.problems.append(
            Finding("head_mismatch", detail="HEAD.json disagrees with the chain")
        )
    if last_window_end is not None and not _not_before(head.watermark, last_window_end):
        # Exports with no new rows move the watermark past the last window; nothing moves
        # it back.
        report.problems.append(
            Finding("head_mismatch", detail="HEAD.json watermark is before the last export window")
        )
    extra = [path for path in _segment_paths(sink, head.segments + 1)[:2] if path.exists()]
    if extra:
        report.problems.append(
            Finding(
                "head_mismatch",
                detail=f"segment {head.segments + 1} exists beyond HEAD.json "
                "(an interrupted export: run export to finish it)",
            )
        )
    return contents


def _not_before(watermark: str | None, window_end: str) -> bool:
    try:
        return watermark is not None and datetime.fromisoformat(
            watermark
        ) >= datetime.fromisoformat(window_end)
    except ValueError:
        return False


def _changed(row: dict[str, Any], stored: dict[str, Any]) -> list[str]:
    return sorted(column for column in RECORD_COLUMNS if row.get(column) != stored.get(column))


def _compare_with_database(session: Session, contents: _SinkContents, report: VerifyReport) -> None:
    head = contents.head
    watermark = datetime.fromisoformat(head.watermark) if head and head.watermark else None
    erasures = dict(contents.erasures)
    seen: set[str] = set()

    def classify(event_id: str, row: dict[str, Any], stored: dict[str, Any]) -> None:
        changed = _changed(row, stored)
        if not changed:
            return
        detail = "changed:" + ",".join(changed)
        if event_id in erasures:
            erasures.pop(event_id)
            report.notices.append(Finding("accepted_erasure", event_id=event_id, detail=detail))
        elif set(changed) <= REFERENTIAL_COLUMNS and all(row[c] is None for c in changed):
            report.notices.append(Finding("referential_null", event_id=event_id, detail=detail))
        else:
            report.problems.append(Finding("row_changed", event_id=event_id, detail=detail))

    if watermark is not None:
        for row in _rows(session, start=None, end=watermark):
            seen.add(row["id"])
            stored = contents.events.get(row["id"])
            if stored is None:
                if row["id"] in erasures:
                    erasures.pop(row["id"])
                    report.notices.append(
                        Finding("accepted_erasure", event_id=row["id"], detail="never exported")
                    )
                else:
                    report.problems.append(Finding("unexported_row", event_id=row["id"]))
                continue
            classify(row["id"], row, stored)
    missing = sorted(contents.events.keys() - seen)
    moved = _rows_by_id(session, missing) if missing else {}
    for event_id in missing:
        if event_id in moved:  # occurred_at moved past the watermark
            classify(event_id, moved[event_id], contents.events[event_id])
        elif event_id in erasures:
            erasures.pop(event_id)
            report.notices.append(Finding("accepted_erasure", event_id=event_id, detail="deleted"))
        else:
            report.problems.append(Finding("row_deleted", event_id=event_id))
    for event_id in sorted(erasures):
        state = "row unchanged" if event_id in contents.events else "not in the sink"
        report.notices.append(Finding("accepted_erasure", event_id=event_id, detail=state))


def verify_sink(
    settings: Settings | None = None,
    *,
    sink: Path | None = None,
    engine: Engine | None = None,
    against_database: bool = True,
) -> VerifyReport:
    """Check the chain and, unless ``against_database`` is false, every exported row."""
    settings = settings or get_settings()
    sink = sink or sink_dir(settings)
    chain = sink_chain(settings)
    report = VerifyReport(against_database=against_database)
    contents = _read_sink(sink, chain, report)
    if not report.ok:
        return report
    if not against_database:
        for event_id in sorted(contents.erasures):
            report.notices.append(Finding("accepted_erasure", event_id=event_id, detail="recorded"))
        return report
    with _reader_session(settings, engine) as session:
        _compare_with_database(session, contents, report)
    return report


# --- command line ------------------------------------------------------------------------


def _verify_counters(report: VerifyReport) -> dict[str, int]:
    kinds = report.kinds()
    return {
        "records": report.records,
        "erasures": report.erasures,
        "segments": report.segments,
        "last_seq": report.last_seq,
        "problems": len(report.problems),
        "notices": len(report.notices),
        "accepted_erasures": kinds.count("accepted_erasure"),
        "referential_nulls": kinds.count("referential_null"),
        "legacy_reviewed": int(report.legacy_review is not None),
    }


def main(argv: list[str] | None = None) -> int:
    from backend.observability.heartbeats import record_run

    parser = argparse.ArgumentParser(
        prog="python -m backend.observability.audit_sink",
        description="Protected audit sink (HOM-115): export, verify, record an erasure.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="append settled audit events to the sink")
    export.add_argument(
        "--legacy-review",
        metavar="APPROVAL_REF",
        help="first export into an empty sink in staging/production: the approval reference "
        f"of the legacy-row review ({LEGACY_REVIEW_RUNBOOK})",
    )
    verify = commands.add_parser("verify", help="check the chain and the database")
    verify.add_argument(
        "--sink-only", action="store_true", help="check the chain without reading the database"
    )
    erasure = commands.add_parser(
        "record-erasure", help="record an owner-approved erasure before the DBA runs it"
    )
    erasure.add_argument("--event-id", required=True)
    erasure.add_argument("--approval", required=True, help="the owner's approval reference")
    args = parser.parse_args(argv)
    settings = get_settings()
    started = time.time()
    try:
        if args.command == "export":
            try:
                result = export_events(settings, legacy_review=args.legacy_review)
            except Exception as exc:
                record_run("audit_export", started=started, finished=time.time(), error=exc)
                raise
            record_run(
                "audit_export",
                started=started,
                finished=time.time(),
                result={"exported": result.exported, "last_seq": result.last_seq},
            )
            print(json.dumps(result.as_dict(), sort_keys=True))
            return 0
        if args.command == "record-erasure":
            seq = record_erasure(settings, event_id=args.event_id, approval=args.approval)
            print(json.dumps({"recorded": "erasure", "seq": seq, "event_id": args.event_id}))
            return 0
        try:
            report = verify_sink(settings, against_database=not args.sink_only)
        except Exception as exc:
            record_run("audit_verify", started=started, finished=time.time(), error=exc)
            raise
        finished = time.time()
        if report.ok:
            record_run(
                "audit_verify", started=started, finished=finished, result=_verify_counters(report)
            )
        else:
            record_run(
                "audit_verify",
                started=started,
                finished=finished,
                error=AuditSinkError("verification failed"),
            )
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
        return 0 if report.ok else 1
    except AuditSinkError as exc:
        print(f"audit_sink: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover - operator entrypoint
    sys.exit(main())
