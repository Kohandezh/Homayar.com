"""Protected audit sink: export, verification and erasure records (HOM-115b, ADR 0036).

The sink is a hash chain of JSON lines outside the application's reach.  These tests
export real ``audit_events`` rows through the reader role (PostgreSQL) and check that
``verify`` reports each kind of tampering, accepts the expected changes as notices
(a deleted workspace, an owner-approved erasure) and never hides them, refuses a
wrong key and an exporter that is (or can become) the application role, keeps
nothing in the sink beyond the masked row, and writes the operator heartbeats.
"""

from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url

from backend.config import Settings
from backend.database.rls import APP_ROLE, AUDIT_READER_ROLE
from backend.observability import audit_sink, heartbeats
from backend.observability.audit_sink import (
    RECORD_COLUMNS,
    AuditSinkError,
    export_events,
    record_erasure,
    record_from_row,
    verify_sink,
)
from backend.services.audit_service import REDACTED, record_audit_event
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests._transport import configured_redis_url
from backend.tests.conftest import login_user, register_user

EXPORTER_LOGIN = "homayar_audit_exporter_probe"
APP_LOGIN = "homayar_audit_sink_app_probe"
BOTH_LOGIN = "homayar_audit_sink_both_probe"
APPROVAL = "CHG-2026-0042"
LEGACY_APPROVAL = "OWNER-2026-10-19/legacy-review"
# Personal data and credential-shaped values, built at runtime (no literal in the source).
PHONE = "0912" + "7654321"
EMAIL = "sara.ahmadi" + "@example.com"
CLIENT_IP = ".".join(("203", "0", "113", "77"))
JWT_LIKE = ".".join(("eyJ" + "hbGciOiJIUzI1NiJ9", "eyJ" + "zdWIiOiIxIn0", "c2lnbmF0dXJl" * 2))


def _postgres() -> bool:
    return is_postgres_url(configured_database_url())


def _dba(statement: str, **params: object) -> None:
    with inspection_session_factory()() as db:
        db.execute(text(statement), params)
        db.commit()


def _audit_rows() -> int:
    """Rows already in audit_events (start-up writes some, e.g. model registration)."""
    with inspection_session_factory()() as db:
        return int(db.execute(text("SELECT count(*) FROM audit_events")).scalar_one())


def _login_url(settings: Settings, role: str, password: str) -> str:
    url = make_url(settings.database_url).set(username=role, password=password)
    return url.render_as_string(hide_password=False)


@pytest.fixture
def sink_settings(client, settings: Settings, tmp_path: Path):
    """Settings of an operator's exporter: its own login, sink dir and key."""
    password = secrets.token_urlsafe(18)  # throwaway roles on a disposable test database
    if _postgres():
        _dba(f"DROP ROLE IF EXISTS {EXPORTER_LOGIN}")
        _dba(
            f"CREATE ROLE {EXPORTER_LOGIN} LOGIN PASSWORD '{password}' NOSUPERUSER "
            f"NOBYPASSRLS NOCREATEDB NOCREATEROLE IN ROLE {AUDIT_READER_ROLE}"
        )
    exporter = settings.model_copy(
        update={
            "audit_sink_dir": str(tmp_path / "sink"),
            "audit_sink_hmac_key": secrets.token_hex(32),
            "audit_export_database_url": (
                _login_url(settings, EXPORTER_LOGIN, password) if _postgres() else ""
            ),
            "audit_export_settle_seconds": 60,
        }
    )
    yield exporter
    if _postgres():
        _dba(f"DROP ROLE IF EXISTS {EXPORTER_LOGIN}")


@pytest.fixture
def memory_heartbeats():
    store = heartbeats.MemoryHeartbeatStore()
    heartbeats.use_heartbeat_store(store)
    yield store
    heartbeats.use_heartbeat_store(None)


def _seed(
    settings: Settings,
    event_type: str = "team.created",
    *,
    workspace_id: str | None = None,
    **fields: object,
) -> str:
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context

    with get_session_factory()() as db:
        if workspace_id:
            bind_tenant_context(db, workspace_id=workspace_id)
        event = record_audit_event(
            db, event_type=event_type, workspace_id=workspace_id, settings=settings, **fields
        )
        db.commit()
        return event.id


def _export(settings: Settings):
    """Export everything recorded so far (``now`` = the settle period from now)."""
    now = datetime.now(UTC) + timedelta(seconds=settings.audit_export_settle_seconds)
    return export_events(settings, now=now)


def _sink(settings: Settings) -> Path:
    return Path(settings.audit_sink_dir)


def _segment(settings: Settings, number: int = 1) -> Path:
    return _sink(settings) / "segments" / f"{number:08d}.jsonl"


def _rewrite_lines(path: Path, change) -> None:
    os.chmod(path, 0o640)  # the attacker first makes the write-once file writable
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(change(lines)) + "\n", encoding="utf-8")


def _kinds(findings) -> list[str]:
    return [finding.kind for finding in findings]


# --- export and a clean verification ----------------------------------------------------


def test_export_then_verify_is_clean(sink_settings: Settings):
    ids = [_seed(sink_settings, "team.created") for _ in range(3)]
    total = _audit_rows()
    result = _export(sink_settings)
    assert (result.exported, result.segment, result.last_seq) == (total, 1, total)
    segment = _segment(sink_settings)
    assert stat.S_IMODE(segment.stat().st_mode) == 0o440
    records = [json.loads(line) for line in segment.read_text(encoding="utf-8").splitlines()]
    assert [record["seq"] for record in records] == list(range(1, total + 1))
    assert set(ids) <= {record["row"]["id"] for record in records}
    assert records[0]["prev"] == audit_sink.GENESIS
    assert records[1]["prev"] == records[0]["hash"]
    manifest = json.loads(
        (_sink(sink_settings) / "segments" / "00000001.manifest.json").read_text("utf-8")
    )
    assert manifest["head_hash"] == records[-1]["hash"] == result.head_hash
    assert (manifest["first_seq"], manifest["last_seq"], manifest["events"]) == (1, total, total)

    report = verify_sink(sink_settings)
    assert report.ok, report.as_dict()
    assert (report.records, report.segments, report.problems, report.notices) == (
        total,
        1,
        [],
        [],
    )

    assert _export(sink_settings).exported == 0  # nothing new: the watermark moves only
    later = _seed(sink_settings, "team.deleted")
    second = _export(sink_settings)
    assert (second.exported, second.segment, second.last_seq) == (1, 2, total + 1)
    report = verify_sink(sink_settings)
    assert report.ok and report.records == total + 1 and report.last_seq == total + 1
    assert later in {
        json.loads(line)["row"]["id"]
        for line in _segment(sink_settings, 2).read_text("utf-8").splitlines()
    }


def test_events_younger_than_the_settle_period_wait_for_the_next_export(
    sink_settings: Settings,
):
    _seed(sink_settings)
    early = export_events(sink_settings, now=datetime.now(UTC))  # the row is not settled yet
    assert early.exported == 0 and early.segment is None
    assert verify_sink(sink_settings).ok  # a pending row is not a finding
    assert _export(sink_settings).exported == 1


# --- tampering in the sink --------------------------------------------------------------


def test_an_altered_line_is_record_altered(sink_settings: Settings):
    for _ in range(3):
        _seed(sink_settings)
    _export(sink_settings)

    def alter(lines: list[str]) -> list[str]:
        record = json.loads(lines[1])
        record["row"]["outcome"] = "rewritten"
        return [lines[0], audit_sink.canonical(record), lines[2]]

    _rewrite_lines(_segment(sink_settings), alter)
    report = verify_sink(sink_settings, against_database=False)
    assert not report.ok
    assert "record_altered" in _kinds(report.problems)
    altered = next(f for f in report.problems if f.kind == "record_altered")
    assert altered.seq == 2


def test_a_removed_or_reordered_line_is_a_sequence_gap(sink_settings: Settings):
    for _ in range(3):
        _seed(sink_settings)
    _export(sink_settings)
    original = _segment(sink_settings).read_text(encoding="utf-8")

    _rewrite_lines(_segment(sink_settings), lambda lines: [lines[0], lines[2]])
    report = verify_sink(sink_settings, against_database=False)
    gap = next(f for f in report.problems if f.kind == "sequence_gap")
    assert (gap.seq, gap.detail) == (2, "found 3")

    _segment(sink_settings).write_text(original, encoding="utf-8")
    _rewrite_lines(_segment(sink_settings), lambda lines: [lines[1], lines[0], lines[2]])
    assert "sequence_gap" in _kinds(verify_sink(sink_settings, against_database=False).problems)


def test_a_missing_segment_or_a_rolled_back_head_is_reported(sink_settings: Settings):
    _seed(sink_settings)
    _export(sink_settings)
    _seed(sink_settings)
    _export(sink_settings)
    head_path = _sink(sink_settings) / "HEAD.json"
    current = head_path.read_text("utf-8")
    head = json.loads(current)
    head.update(segments=1, last_seq=1)
    head_path.write_text(json.dumps(head), encoding="utf-8")
    report = verify_sink(sink_settings, against_database=False)
    assert "head_mismatch" in _kinds(report.problems)  # segment 2 exists beyond HEAD

    head_path.write_text(current, encoding="utf-8")
    _segment(sink_settings, 1).unlink()
    assert "segment_missing" in _kinds(verify_sink(sink_settings, against_database=False).problems)


# --- tampering in the database ----------------------------------------------------------


def test_rows_changed_or_deleted_in_the_database_are_reported(sink_settings: Settings):
    changed, deleted, kept = (_seed(sink_settings) for _ in range(3))
    _export(sink_settings)
    # The table owner (DBA here) can still change rows; the sink makes it visible.
    _dba("UPDATE audit_events SET outcome = 'rewritten' WHERE id = :id", id=changed)
    _dba("DELETE FROM audit_events WHERE id = :id", id=deleted)

    report = verify_sink(sink_settings)
    assert not report.ok
    problems = {(f.kind, f.event_id): f.detail for f in report.problems}
    assert problems == {
        ("row_changed", changed): "changed:outcome",
        ("row_deleted", deleted): "",
    }
    assert kept not in {f.event_id for f in report.problems}


def test_a_row_moved_past_the_watermark_is_a_changed_row(sink_settings: Settings):
    moved = _seed(sink_settings)
    _export(sink_settings)
    _dba(
        "UPDATE audit_events SET occurred_at = :moment WHERE id = :id",
        moment=datetime.now(UTC) + timedelta(days=1),
        id=moved,
    )
    report = verify_sink(sink_settings)
    assert [(f.kind, f.event_id, f.detail) for f in report.problems] == [
        ("row_changed", moved, "changed:occurred_at")
    ]


def test_a_back_dated_row_older_than_the_watermark_is_unexported(sink_settings: Settings):
    _seed(sink_settings)
    _export(sink_settings)
    late = _seed(sink_settings, "member.removed")  # newer than the watermark: pending, fine
    assert verify_sink(sink_settings).ok
    back_dated = "a1b2c3d4-0000-4000-8000-00000000b4c6"
    _dba(
        "INSERT INTO audit_events (id, event_type, outcome, details, occurred_at) "
        "VALUES (:id, 'member.removed', 'success', '{}', :moment)",
        id=back_dated,
        moment=datetime.now(UTC) - timedelta(hours=1),
    )
    report = verify_sink(sink_settings)
    assert [(f.kind, f.event_id) for f in report.problems] == [("unexported_row", back_dated)]
    assert late not in {f.event_id for f in report.problems}


# --- expected changes are notices, listed and counted -----------------------------------


def test_deleting_a_workspace_is_referential_null_not_tampering(
    client, email_outbox, sink_settings: Settings
):
    credentials = register_user(client, email_outbox, email="audit-sink-tenant@example.com")
    workspace_id = login_user(client, credentials)["workspaces"][0]["id"]
    event_id = _seed(sink_settings, "workspace.settings.updated", workspace_id=workspace_id)
    _export(sink_settings)
    _dba("DELETE FROM workspaces WHERE id = :id", id=workspace_id)

    report = verify_sink(sink_settings)
    assert report.ok, report.as_dict()
    notices = [(f.kind, f.event_id, f.detail) for f in report.notices]
    assert ("referential_null", event_id, "changed:workspace_id") in notices


def test_an_owner_approved_erasure_is_flagged_not_hidden(
    sink_settings: Settings, memory_heartbeats, monkeypatch, capsys
):
    erased, changed, kept = (
        _seed(sink_settings, target_type="team", target_id=f"t-{n}", details={"members": n})
        for n in range(3)
    )
    exported = _export(sink_settings)
    # The procedure: record the approved erasure in the chain, then the DBA acts.
    seq = record_erasure(sink_settings, event_id=erased, approval=APPROVAL)
    assert seq == exported.last_seq + 1
    record_erasure(sink_settings, event_id=changed, approval=APPROVAL)
    _dba("DELETE FROM audit_events WHERE id = :id", id=erased)
    _dba("UPDATE audit_events SET details = '{}', target_id = NULL WHERE id = :id", id=changed)

    report = verify_sink(sink_settings)
    assert report.ok, report.as_dict()
    assert report.erasures == 2
    assert sorted((f.kind, f.event_id, f.detail) for f in report.notices) == sorted(
        [
            ("accepted_erasure", erased, "deleted"),
            ("accepted_erasure", changed, "changed:details,target_id"),
        ]
    )

    monkeypatch.setattr(audit_sink, "get_settings", lambda: sink_settings)
    assert audit_sink.main(["verify"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert [n["kind"] for n in printed["notices"]] == ["accepted_erasure", "accepted_erasure"]
    counters = json.loads(memory_heartbeats.read_all()["audit_verify"]["result"])
    assert counters["accepted_erasures"] == 2 and counters["problems"] == 0

    # Without its record the same change is tampering.
    _dba("DELETE FROM audit_events WHERE id = :id", id=kept)
    assert [(f.kind, f.event_id) for f in verify_sink(sink_settings).problems] == [
        ("row_deleted", kept)
    ]


def test_an_erasure_record_cannot_be_forged_or_carry_free_text(sink_settings: Settings):
    victim = _seed(sink_settings)
    _export(sink_settings)
    for approval in ("approved by " + PHONE, "x", "CHG 42"):
        with pytest.raises(AuditSinkError, match="reference"):
            record_erasure(sink_settings, event_id=victim, approval=approval)
    with pytest.raises(AuditSinkError, match="event id"):
        record_erasure(sink_settings, event_id="'; DROP TABLE audit_events; --", approval=APPROVAL)

    # A writer of the sink without the key appends an "erasure" to hide a deletion.
    forger = audit_sink.Chain(secrets.token_hex(32))
    head = audit_sink.read_head(_sink(sink_settings))
    record = {
        "seq": head.last_seq + 1,
        "type": "erasure",
        "erasure": {"event_id": victim, "approval": APPROVAL, "recorded_at": "2026-10-18T00Z"},
    }
    digest = forger.link(head.head_hash, audit_sink.canonical(record))
    forged = audit_sink.canonical({**record, "prev": head.head_hash, "hash": digest})
    _rewrite_lines(_segment(sink_settings), lambda lines: [*lines, forged])
    _dba("DELETE FROM audit_events WHERE id = :id", id=victim)
    report = verify_sink(sink_settings)
    assert not report.ok and "record_altered" in _kinds(report.problems)


# --- keys ---------------------------------------------------------------------------------


def test_a_wrong_hmac_key_fails(sink_settings: Settings):
    _seed(sink_settings)
    _export(sink_settings)
    other = sink_settings.model_copy(update={"audit_sink_hmac_key": secrets.token_hex(32)})
    report = verify_sink(other, against_database=False)
    assert not report.ok and _kinds(report.problems) == ["key_mismatch"]
    with pytest.raises(AuditSinkError, match="key id"):
        _export(other)

    # Forging the key id in HEAD.json does not help: the chain itself fails.
    head_path = _sink(sink_settings) / "HEAD.json"
    head = json.loads(head_path.read_text("utf-8"))
    head["key_id"] = audit_sink.Chain(other.audit_sink_hmac_key).key_id
    head_path.write_text(json.dumps(head), encoding="utf-8")
    assert "record_altered" in _kinds(verify_sink(other, against_database=False).problems)

    unkeyed = sink_settings.model_copy(update={"audit_sink_hmac_key": ""})
    assert _kinds(verify_sink(unkeyed, against_database=False).problems) == ["key_mismatch"]


def test_an_unkeyed_chain_is_refused_outside_test_and_development(sink_settings: Settings):
    staging = sink_settings.model_copy(update={"environment": "staging", "audit_sink_hmac_key": ""})
    with pytest.raises(AuditSinkError, match="AUDIT_SINK_HMAC_KEY is required"):
        export_events(staging)
    with pytest.raises(AuditSinkError, match="AUDIT_SINK_HMAC_KEY is required"):
        verify_sink(staging)


# --- the legacy-row review gate, decided (owner, 2026-10-18) -------------------------------


def _settled_now(settings: Settings):
    """``_database_now`` replacement: every export sees all rows recorded so far."""
    return lambda _session: datetime.now(UTC) + timedelta(
        seconds=settings.audit_export_settle_seconds
    )


def test_the_first_staging_export_into_an_empty_sink_needs_the_legacy_review(
    sink_settings: Settings, memory_heartbeats, monkeypatch, capsys
):
    victim = _seed(sink_settings)
    staging = sink_settings.model_copy(update={"environment": "staging"})
    monkeypatch.setattr(audit_sink, "get_settings", lambda: staging)

    assert audit_sink.main(["export"]) == 2
    message = capsys.readouterr().err
    assert "--legacy-review" in message and "audit.md#legacy-row-review" in message
    assert not _sink(staging).exists()  # refused before anything was written
    assert memory_heartbeats.read_all()["audit_export"]["last_error_type"] == "AuditSinkError"

    for reference in ("reviewed by " + PHONE, "ok", ""):
        assert audit_sink.main(["export", "--legacy-review", reference]) == 2
        assert "approval reference" in capsys.readouterr().err
        assert not _sink(staging).exists()

    # An erasure record would create the head and skip the review.
    assert audit_sink.main(["record-erasure", "--event-id", victim, "--approval", APPROVAL]) == 2
    assert "first export" in capsys.readouterr().err
    assert not _sink(staging).exists()

    production = sink_settings.model_copy(update={"environment": "production"})
    with pytest.raises(AuditSinkError, match="legacy-row review"):
        export_events(production)
    assert not _sink(production).exists()


@pytest.mark.postgres
def test_the_acknowledged_review_is_recorded_once_and_reported(
    sink_settings: Settings, memory_heartbeats, monkeypatch, capsys
):
    if not _postgres():
        pytest.skip("staging reads PostgreSQL through the exporter's login")
    _seed(sink_settings)
    staging = sink_settings.model_copy(update={"environment": "staging"})
    monkeypatch.setattr(audit_sink, "get_settings", lambda: staging)
    monkeypatch.setattr(audit_sink, "_database_now", _settled_now(staging))
    total = _audit_rows()

    assert audit_sink.main(["export", "--legacy-review", LEGACY_APPROVAL]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["legacy_review"] == LEGACY_APPROVAL
    assert (printed["exported"], printed["segment"], printed["last_seq"]) == (total, 1, total + 1)
    first = json.loads(_segment(staging).read_text("utf-8").splitlines()[0])
    assert (first["seq"], first["type"], first["prev"]) == (1, "legacy_review", audit_sink.GENESIS)
    review = first["legacy_review"]
    assert (review["approval"], review["environment"]) == (LEGACY_APPROVAL, "staging")
    manifest_path = _sink(staging) / "segments" / "00000001.manifest.json"
    head_path = _sink(staging) / "HEAD.json"
    manifest = json.loads(manifest_path.read_text("utf-8"))
    assert manifest["legacy_review"] == json.loads(head_path.read_text("utf-8"))["legacy_review"]
    assert manifest["legacy_review"] == review and manifest["events"] == total

    report = verify_sink(staging)
    assert report.ok, report.as_dict()
    assert (report.legacy_review, report.records) == (review, total)
    assert audit_sink.main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["legacy_review"] == review
    verify_counters = json.loads(memory_heartbeats.read_all()["audit_verify"]["result"])
    assert verify_counters["legacy_reviewed"] == 1

    # Later exports need no acknowledgement and refuse a second one.
    later = _seed(staging)
    assert audit_sink.main(["export"]) == 0
    assert json.loads(capsys.readouterr().out)["exported"] == 1
    assert audit_sink.main(["export", "--legacy-review", LEGACY_APPROVAL]) == 2
    assert "recorded once" in capsys.readouterr().err
    assert audit_sink.main(["record-erasure", "--event-id", later, "--approval", APPROVAL]) == 0
    capsys.readouterr()
    report = verify_sink(staging)
    assert report.ok and report.legacy_review == review and report.erasures == 1

    # The copy in HEAD.json is checked against the chain.
    head = json.loads(head_path.read_text("utf-8"))
    head["legacy_review"] = {**review, "approval": "FORGED-0001"}
    head_path.write_text(json.dumps(head), encoding="utf-8")
    assert "head_mismatch" in _kinds(verify_sink(staging, against_database=False).problems)


def test_development_and_test_are_not_gated(sink_settings: Settings, tmp_path: Path):
    victim = _seed(sink_settings)
    development = sink_settings.model_copy(update={"environment": "development"})
    result = _export(development)
    assert result.exported >= 1 and result.legacy_review is None
    report = verify_sink(development)
    assert report.ok and report.legacy_review is None
    fresh = sink_settings.model_copy(update={"audit_sink_dir": str(tmp_path / "fresh-sink")})
    assert record_erasure(fresh, event_id=victim, approval=APPROVAL) == 1  # test env: no gate


# --- the exporter's identity --------------------------------------------------------------


@pytest.mark.postgres
def test_the_exporter_refuses_to_run_as_the_application_role(sink_settings: Settings):
    if not _postgres():
        pytest.skip("needs PostgreSQL roles")
    _seed(sink_settings)
    first = _export(sink_settings)  # the exporter's own login works
    assert first.exported >= 1
    _seed(sink_settings)
    password = secrets.token_urlsafe(18)
    _dba(f"DROP ROLE IF EXISTS {APP_LOGIN}")
    _dba(f"DROP ROLE IF EXISTS {BOTH_LOGIN}")
    _dba(f"CREATE ROLE {APP_LOGIN} LOGIN PASSWORD '{password}' NOSUPERUSER IN ROLE {APP_ROLE}")
    _dba(
        f"CREATE ROLE {BOTH_LOGIN} LOGIN PASSWORD '{password}' NOSUPERUSER "
        f"IN ROLE {APP_ROLE}, {AUDIT_READER_ROLE}"
    )
    try:
        cases = {
            # the application's login: not a reader member, SET ROLE is refused
            _login_url(sink_settings, APP_LOGIN, password): "cannot read as",
            # a login that could also act as the application
            _login_url(sink_settings, BOTH_LOGIN, password): "member of homayar_app",
            # the test DBA (superuser): could become anyone
            sink_settings.database_url: "superuser",
            "": "AUDIT_EXPORT_DATABASE_URL is required",
        }
        for url, message in cases.items():
            refused = sink_settings.model_copy(update={"audit_export_database_url": url})
            with pytest.raises(AuditSinkError, match=message) as caught:
                _export(refused)
            assert password not in str(caught.value)
            assert audit_sink.read_head(_sink(sink_settings)).last_seq == first.last_seq
            with pytest.raises(AuditSinkError, match=message):
                verify_sink(refused)
    finally:
        _dba(f"DROP ROLE IF EXISTS {APP_LOGIN}")
        _dba(f"DROP ROLE IF EXISTS {BOTH_LOGIN}")
    assert _export(sink_settings).exported == 1
    assert verify_sink(sink_settings).ok


def test_head_json_is_bound_to_the_key(sink_settings: Settings):
    _seed(sink_settings)
    _export(sink_settings)
    head_path = _sink(sink_settings) / "HEAD.json"
    original = head_path.read_text("utf-8")

    # A writer of the sink without the key rolls the watermark back (it is not chained).
    head = json.loads(original)
    head["watermark"] = "2000-01-01T00:00:00+00:00"
    head_path.write_text(json.dumps(head), encoding="utf-8")
    details = [
        f.detail
        for f in verify_sink(sink_settings, against_database=False).problems
        if f.kind == "head_mismatch"
    ]
    assert any("MAC" in detail for detail in details)
    with pytest.raises(AuditSinkError, match="MAC"):
        _export(sink_settings)

    # With a valid MAC (a key holder's own edit) a watermark moved back is still caught.
    chain = audit_sink.Chain(sink_settings.audit_sink_hmac_key)
    head_path.write_text(audit_sink.read_head(_sink(sink_settings)).to_json(chain), "utf-8")
    details = [
        f.detail
        for f in verify_sink(sink_settings, against_database=False).problems
        if f.kind == "head_mismatch"
    ]
    assert details == ["HEAD.json watermark is before the last export window"]

    head_path.write_text(original, encoding="utf-8")
    assert verify_sink(sink_settings).ok
    assert _export(sink_settings).exported == 0  # nothing new; the watermark moves on


@pytest.mark.postgres
def test_the_exporter_refuses_a_reader_role_that_is_not_inert(sink_settings: Settings):
    if not _postgres():
        pytest.skip("needs PostgreSQL roles")
    _seed(sink_settings)
    _export(sink_settings)
    for broken, restore in (
        (f"ALTER ROLE {AUDIT_READER_ROLE} LOGIN", f"ALTER ROLE {AUDIT_READER_ROLE} NOLOGIN"),
        (f"ALTER ROLE {AUDIT_READER_ROLE} CREATEDB", f"ALTER ROLE {AUDIT_READER_ROLE} NOCREATEDB"),
        (f"GRANT {AUDIT_READER_ROLE} TO {APP_ROLE}", f"REVOKE {AUDIT_READER_ROLE} FROM {APP_ROLE}"),
        (f"GRANT {APP_ROLE} TO {AUDIT_READER_ROLE}", f"REVOKE {APP_ROLE} FROM {AUDIT_READER_ROLE}"),
    ):
        _dba(broken)
        try:
            with pytest.raises(AuditSinkError, match="is not inert"):
                _export(sink_settings)
            with pytest.raises(AuditSinkError, match="is not inert"):
                verify_sink(sink_settings)
        finally:
            _dba(restore)
    assert verify_sink(sink_settings).ok


# --- content -------------------------------------------------------------------------------


def test_sink_lines_hold_only_what_audit_events_holds(
    sink_settings: Settings, memory_heartbeats, monkeypatch, capsys
):
    event_id = _seed(
        sink_settings,
        "pii.revealed",
        ip_address=CLIENT_IP,
        target_type="redaction",
        target_id="r-1",
        details={"reason": f"caller {PHONE}, {EMAIL}", "token": JWT_LIKE, "note": JWT_LIKE},
    )
    monkeypatch.setattr(audit_sink, "get_settings", lambda: sink_settings)
    now = datetime.now(UTC) + timedelta(seconds=120)
    monkeypatch.setattr(audit_sink, "_database_now", lambda _session: now)
    assert audit_sink.main(["export"]) == 0
    printed = capsys.readouterr().out

    from backend.database.saas_models import AuditEvent

    records = [json.loads(line) for line in _segment(sink_settings).read_text("utf-8").splitlines()]
    with inspection_session_factory()() as db:
        for record in records:  # every line is exactly its (masked) row, nothing more
            assert set(record) == {"seq", "type", "row", "prev", "hash"}
            assert set(record["row"]) == set(RECORD_COLUMNS)
            assert record["row"] == record_from_row(db.get(AuditEvent, record["row"]["id"]))
    (ours,) = [record["row"] for record in records if record["row"]["id"] == event_id]
    assert ours["details"]["reason"] == "caller [PHONE_1], [EMAIL_1]"
    assert ours["details"]["token"] == ours["details"]["note"] == REDACTED
    assert ours["ip_hash"] and CLIENT_IP not in ours["ip_hash"]

    files = "".join(path.read_text("utf-8") for path in _sink(sink_settings).rglob("*.json*"))
    secret_values = (
        sink_settings.audit_sink_hmac_key,
        make_url(sink_settings.audit_export_database_url).password
        if sink_settings.audit_export_database_url
        else None,
    )
    for raw in (PHONE, EMAIL, CLIENT_IP, JWT_LIKE, *filter(None, secret_values)):
        assert raw not in files, raw
        assert raw not in printed, raw
        assert raw not in json.dumps(memory_heartbeats.read_all())


# --- robustness, operator plumbing ---------------------------------------------------------


def test_an_interrupted_export_is_rolled_forward(sink_settings: Settings, monkeypatch):
    _seed(sink_settings)
    _export(sink_settings)
    _seed(sink_settings)
    real_replace = audit_sink._replace

    def crash_on_head(path: Path, content: str) -> None:
        if path.name == "HEAD.json" and '"segments":2' in content:
            raise OSError("power cut")
        real_replace(path, content)

    monkeypatch.setattr(audit_sink, "_replace", crash_on_head)
    with pytest.raises(OSError, match="power cut"):
        _export(sink_settings)
    monkeypatch.setattr(audit_sink, "_replace", real_replace)
    assert _segment(sink_settings, 2).is_file()  # segment and manifest written, HEAD not
    report = verify_sink(sink_settings, against_database=False)
    assert "head_mismatch" in _kinds(report.problems)

    _seed(sink_settings)
    result = _export(sink_settings)  # finishes segment 2, then writes segment 3
    assert (result.segment, result.exported, result.last_seq) == (3, 1, _audit_rows())
    report = verify_sink(sink_settings)
    assert report.ok and report.records == _audit_rows(), report.as_dict()


def test_one_writer_at_a_time(sink_settings: Settings):
    _seed(sink_settings)
    sink = _sink(sink_settings)
    (sink / "segments").mkdir(parents=True)
    descriptor = os.open(sink / ".lock", os.O_WRONLY | os.O_CREAT, 0o640)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(AuditSinkError, match="another export"):
            _export(sink_settings)
    finally:
        os.close(descriptor)
    assert _export(sink_settings).exported == 1


def test_heartbeats_record_success_and_failure(
    sink_settings: Settings, memory_heartbeats, monkeypatch, capsys
):
    _seed(sink_settings)
    total = _audit_rows()
    monkeypatch.setattr(audit_sink, "get_settings", lambda: sink_settings)
    now = datetime.now(UTC) + timedelta(seconds=120)
    monkeypatch.setattr(audit_sink, "_database_now", lambda _session: now)
    assert audit_sink.main(["export"]) == 0
    assert audit_sink.main(["verify"]) == 0
    beats = memory_heartbeats.read_all()
    assert json.loads(beats["audit_export"]["result"]) == {"exported": total, "last_seq": total}
    assert "last_success" in beats["audit_export"]
    verify_counters = json.loads(beats["audit_verify"]["result"])
    assert verify_counters["records"] == total and verify_counters["problems"] == 0

    _rewrite_lines(
        _segment(sink_settings), lambda lines: [line.replace("team", "TEAM") for line in lines]
    )
    assert audit_sink.main(["verify", "--sink-only"]) == 1
    beats = memory_heartbeats.read_all()
    assert beats["audit_verify"]["last_error_type"] == "AuditSinkError"
    assert float(beats["audit_verify"]["last_failure"]) >= float(
        beats["audit_verify"]["last_success"]
    )

    broken = sink_settings.model_copy(update={"audit_sink_dir": ""})
    monkeypatch.setattr(audit_sink, "get_settings", lambda: broken)
    assert audit_sink.main(["export"]) == 2  # configuration error: exit 2, one line on stderr
    assert "AUDIT_SINK_DIR is not configured" in capsys.readouterr().err
    assert memory_heartbeats.read_all()["audit_export"]["last_error_type"] == "AuditSinkError"


@pytest.mark.redis
def test_heartbeats_reach_redis(sink_settings: Settings, monkeypatch, capsys):
    import redis

    url = configured_redis_url()
    _seed(sink_settings)
    exporter = sink_settings.model_copy(update={"redis_url": url})
    monkeypatch.setattr(audit_sink, "get_settings", lambda: exporter)
    monkeypatch.setattr(heartbeats, "get_settings", lambda: exporter)
    client = redis.Redis.from_url(url, decode_responses=True)
    key = heartbeats.HEARTBEAT_KEY_PREFIX + "audit_export"
    try:
        client.delete(key)
        assert audit_sink.main(["export"]) == 0
        stored = client.hgetall(key)
        assert "last_success" in stored and "last_seq" in json.loads(stored["result"])
    finally:
        client.delete(key, heartbeats.HEARTBEAT_KEY_PREFIX + "audit_verify")
        client.close()
    capsys.readouterr()


def test_settings_validate_the_sink_options(monkeypatch):
    names = set(Settings.model_fields) | {f"{name}_file" for name in Settings.model_fields}
    for key in list(os.environ):
        if key.lower() in names:
            monkeypatch.delenv(key)
    base = {"_env_file": None, "environment": "development"}
    with pytest.raises(ValidationError, match="AUDIT_SINK_HMAC_KEY must be at least 32"):
        Settings(**base, audit_sink_hmac_key="k" * 16)
    for seconds in (59, 86401):
        with pytest.raises(ValidationError, match="AUDIT_EXPORT_SETTLE_SECONDS"):
            Settings(**base, audit_export_settle_seconds=seconds)
    password = secrets.token_hex(8)
    production = {
        "_env_file": None,
        "environment": "production",
        "deployment_mode": "cloud",
        "database_url": f"postgresql+psycopg://api:{password}@db.example.invalid/h"
        "?sslmode=verify-full",
        "redis_url": f"rediss://:{password}@redis.example.invalid:6380/0",
        "jwt_secret": "j" * 48,
        "token_pepper": "p" * 48,
        "secure_cookies": True,
        "enable_demo_account": False,
        "allow_mock_providers": False,
        "auto_create_schema": False,
    }
    plaintext = f"postgresql+psycopg://audit_exporter:{password}@db.example.invalid/h"
    with pytest.raises(ValidationError, match="AUDIT_EXPORT_DATABASE_URL must use") as caught:
        Settings(**production, audit_export_database_url=plaintext)
    assert password not in str(caught.value)
    assert Settings(
        **production, audit_export_database_url=plaintext + "?sslmode=verify-full"
    ).audit_export_database_url.endswith("verify-full")
