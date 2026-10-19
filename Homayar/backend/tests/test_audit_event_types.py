"""HOM-185: the audit event type catalog.

``backend/services/audit_event_types.py`` is the one list of audit event types
the code can write, each with its group and the target type stored with it.
``GET /admin/audit-event-types`` returns that list under ``audit:read``, with
the access rules of ``GET /admin/audit-events``.

Two guards keep the list complete.  The runtime guard (``conftest.py``) checks
every ``AuditEvent`` row flushed during the test run, so it also covers event
names built at run time.  The static guard below parses the backend source and
checks every string literal passed as an event type, so it also covers code
paths no test runs.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from backend.database.saas_models import AuditEvent
from backend.services.audit_event_types import AuditEventType
from backend.services.audit_service import record_audit_event
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_api_keys import _owner as _key_owner
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_text_normalization_owner import FROZEN_FILES

CATALOG = "/api/v1/admin/audit-event-types"
AUDIT_LIST = "/api/v1/admin/audit-events"
BACKEND = Path(__file__).resolve().parents[1]
HOMAYAR = BACKEND.parent
REGISTERED = {member.value for member in AuditEventType}


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _audit_row_count() -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count()).select_from(AuditEvent)) or 0


# --- the registry ------------------------------------------------------------


def test_every_registry_entry_has_its_prefix_group_and_a_target_type():
    assert len(REGISTERED) == len(AuditEventType)
    for member in AuditEventType:
        assert member == member.value  # a member is the stored string itself
        assert member.name == member.value.upper().replace(".", "_"), member
        assert member.group == member.value.split(".", 1)[0], member
        assert member.target_type is None or isinstance(member.target_type, str), member


# --- the endpoint ------------------------------------------------------------


def _expected_items() -> list[dict[str, str | None]]:
    return [
        {"event_type": member.value, "group": member.group, "target_type": member.target_type}
        for member in sorted(AuditEventType, key=lambda member: member.value)
    ]


def _assert_catalog(response) -> list[dict]:
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items"}
    items = body["items"]
    assert all(set(item) == {"event_type", "group", "target_type"} for item in items)
    types = [item["event_type"] for item in items]
    assert types == sorted(types)
    assert len(types) == len(set(types))
    assert items == _expected_items()
    return items


def test_owner_and_admin_read_the_catalog_member_and_viewer_are_denied(
    client, email_outbox, monkeypatch
):
    owner, personas, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "member": "member", "viewer": "viewer"},
    )
    before = _audit_row_count()
    owner_items = _assert_catalog(owner.get(CATALOG))
    assert _assert_catalog(personas["admin"].get(CATALOG)) == owner_items
    assert _audit_row_count() == before  # reading the catalog is not audited

    for name in ("member", "viewer"):
        denied = personas[name].get(CATALOG)
        reference = personas[name].get(AUDIT_LIST)
        assert denied.status_code == reference.status_code == 403, (name, denied.text)
        assert _error_code(denied) == _error_code(reference) == "permission_denied"
        assert "event_type" not in denied.text

    client.cookies.clear()
    headers = {"X-Workspace-ID": owner.workspace_id}
    anonymous = client.get(CATALOG, headers=headers)
    reference = client.get(AUDIT_LIST, headers=headers)
    assert anonymous.status_code == reference.status_code == 401, anonymous.text
    assert _error_code(anonymous) == _error_code(reference)


def test_api_keys_get_exactly_the_audit_list_behaviour(client, email_outbox):
    _, _, cookies, headers = _key_owner(client, email_outbox)
    audit_key = _mint(client, headers, cookies, name="audit", scopes=["audit:read"])
    assert audit_key.status_code == 201, audit_key.text
    meeting_key = _mint(client, headers, cookies, name="meetings", scopes=["meeting:read"])
    assert meeting_key.status_code == 201, meeting_key.text
    client.cookies.clear()

    allowed = client.get(CATALOG, headers=_bearer(audit_key.json()["key"]))
    assert client.get(AUDIT_LIST, headers=_bearer(audit_key.json()["key"])).status_code == 200
    _assert_catalog(allowed)

    denied = client.get(CATALOG, headers=_bearer(meeting_key.json()["key"]))
    reference = client.get(AUDIT_LIST, headers=_bearer(meeting_key.json()["key"]))
    assert denied.status_code == reference.status_code == 403, denied.text
    assert _error_code(denied) == _error_code(reference) == "api_key_scope_denied"


def test_catalog_lists_the_event_types_the_code_writes():
    required = {
        # members and meetings
        "member.invitation.created",
        "meeting.uploaded",
        # auth router requests (auth_service, otp_service, google_oauth_service)
        "auth.register",
        "auth.email_verified",
        "auth.verification_resent",
        "auth.password_reset_requested",
        "auth.password_reset_completed",
        "auth.otp_requested",
        "auth.otp_verified",
        "auth.google_completed",
        # routers/v1/admin.py
        "workspace.settings.updated",
        # jobs/recovery.py
        "meeting.processing.cancelled",
        "meeting.processing.lease_expired",
        "meeting.processing.failed",
        # jobs/replay.py
        "meeting.processing.replayed",
        # jobs/usage_reconciliation.py
        "usage.reconciled",
        # locations/service.py
        "service_location.created",
        "service_location.updated",
        "service_location.deleted",
    }
    assert required <= {item["event_type"] for item in _expected_items()}


# --- runtime guard -----------------------------------------------------------


def _write(event_type: str, target_type: str | None) -> None:
    with inspection_session_factory()() as db:
        record_audit_event(db, event_type=event_type, target_type=target_type, target_id="t-1")
        db.commit()


def test_runtime_guard_reports_an_unregistered_event_type(client, audit_event_guard):
    _write("not.registered", "meeting")
    assert len(audit_event_guard) == 1
    assert "'not.registered'" in audit_event_guard[0]
    assert "'meeting'" in audit_event_guard[0]
    assert "not in the registry" in audit_event_guard[0]
    audit_event_guard.clear()  # the violation was the point of this test


def test_runtime_guard_reports_a_registered_type_with_another_target_type(
    client, audit_event_guard
):
    _write(AuditEventType.MEETING_UPLOADED, "workspace")
    assert len(audit_event_guard) == 1
    assert "'meeting.uploaded'" in audit_event_guard[0]
    assert "'workspace'" in audit_event_guard[0]
    assert "registered target_type is 'meeting'" in audit_event_guard[0]
    audit_event_guard.clear()


def test_runtime_guard_passes_a_registered_type_with_its_target_type(client, audit_event_guard):
    _write(AuditEventType.MEETING_UPLOADED, "meeting")
    _write("meeting.uploaded", "meeting")  # a plain string with the same value
    assert audit_event_guard == []
    with inspection_session_factory()() as db:
        stored = db.scalars(
            select(AuditEvent.event_type).where(AuditEvent.target_id == "t-1")
        ).all()
    assert stored == ["meeting.uploaded", "meeting.uploaded"]
    assert all(type(value) is str for value in stored)


def test_record_audit_event_passes_the_event_type_through_without_stringifying(
    client, audit_event_guard
):
    with inspection_session_factory()() as db:
        member = record_audit_event(db, event_type=AuditEventType.MEETING_UPLOADED)
        assert type(member.event_type) is str and member.event_type == "meeting.uploaded"
        db.expunge(member)
        missing = record_audit_event(db, event_type=None)  # type: ignore[arg-type]
        assert missing.event_type is None  # not the text "None"
        with pytest.raises(IntegrityError):
            db.flush()  # NOT NULL refuses it, as before HOM-185
        db.rollback()
    audit_event_guard.clear()  # the guard also saw the refused row


PROBE = """
from backend.services.audit_service import record_audit_event
from backend.tests._database import inspection_session_factory


def test_probe(client):
    with inspection_session_factory()() as db:
        record_audit_event(
            db, event_type={event_type!r}, target_type={target_type!r}, target_id="probe"
        )
        db.commit()
"""


def _run_probe(tmp_path: Path, event_type: str, target_type: str) -> tuple[int, str]:
    """Run one generated test in a separate pytest, with the backend conftest loaded.

    The probe test writes one audit row and does nothing else, so its outcome
    is the runtime guard's verdict on that row.
    """
    probe = tmp_path / "audit_guard_probe.py"
    probe.write_text(PROBE.format(event_type=event_type, target_type=target_type))
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, test-owned process
            [
                sys.executable,
                "-m",
                "pytest",
                str(probe),
                "-p",
                "backend.tests.conftest",
                "-c",
                str(HOMAYAR / "pyproject.toml"),
                "--rootdir",
                str(HOMAYAR),
                "-p",
                "no:cacheprovider",
                "-q",
            ],
            cwd=HOMAYAR,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    finally:
        probe.unlink(missing_ok=True)
    return completed.returncode, completed.stdout + completed.stderr


def test_runtime_guard_fails_a_test_that_writes_an_unregistered_type(tmp_path):
    code, output = _run_probe(tmp_path, "not.registered", "meeting")
    assert code != 0, output
    assert "1 error" in output, output
    assert "unregistered audit writes" in output, output
    assert "'not.registered'" in output and "'meeting'" in output, output


def test_runtime_guard_fails_a_test_that_writes_another_target_type(tmp_path):
    code, output = _run_probe(tmp_path, "meeting.uploaded", "workspace")
    assert code != 0, output
    assert "1 error" in output, output
    assert "'meeting.uploaded'" in output and "'workspace'" in output, output
    assert "registered target_type is 'meeting'" in output, output


def test_runtime_guard_passes_a_test_that_writes_a_registered_type(tmp_path):
    code, output = _run_probe(tmp_path, "meeting.uploaded", "meeting")
    assert code == 0, output
    assert "1 passed" in output and "error" not in output, output


# --- static guard ------------------------------------------------------------


def _source_files() -> list[Path]:
    return sorted(
        path
        for path in BACKEND.rglob("*.py")
        if not {"tests", "migrations"} & set(path.relative_to(BACKEND).parts)
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _audit_helpers(tree: ast.Module) -> dict[str, tuple[int | None, str]]:
    """Local helpers that pass one of their parameters on as ``event_type``.

    Maps the helper name to (positional index or ``None``, parameter name).
    """
    helpers: dict[str, tuple[int | None, str]] = {}
    for function in ast.walk(tree):
        if not isinstance(function, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        positional = [arg.arg for arg in function.args.posonlyargs + function.args.args]
        names = set(positional) | {arg.arg for arg in function.args.kwonlyargs}
        for call in ast.walk(function):
            if not (isinstance(call, ast.Call) and _call_name(call) == "record_audit_event"):
                continue
            for keyword in call.keywords:
                value = keyword.value
                if keyword.arg == "event_type" and isinstance(value, ast.Name):
                    if value.id in names:
                        index = positional.index(value.id) if value.id in positional else None
                        helpers[function.name] = (index, value.id)
    return helpers


def _module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    """Module-level ``NAME = value`` assignments, so ``event_type=NAME`` can be resolved."""
    constants: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                constants[node.target.id] = node.value
    return constants


def _event_literals(source: str) -> list[ast.Constant]:
    """Every string literal the source passes, or assigns, as an audit event type.

    A name passed as the event type resolves to a module-level assignment of
    the same file.
    """
    tree = ast.parse(source)
    helpers = _audit_helpers(tree)
    constants = _module_constants(tree)
    values: list[ast.expr] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name == "record_audit_event":
                values += [kw.value for kw in node.keywords if kw.arg == "event_type"]
            elif name in helpers:
                index, parameter = helpers[name]
                values += [kw.value for kw in node.keywords if kw.arg == parameter]
                if index is not None and len(node.args) > index:
                    values.append(node.args[index])
        elif isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "event_type" for target in node.targets
        ):
            values.append(node.value)
    resolved = [
        constants.get(value.id, value) if isinstance(value, ast.Name) else value for value in values
    ]
    return [
        value
        for value in resolved
        if isinstance(value, ast.Constant) and isinstance(value.value, str)
    ]


def _unregistered(source: str, label: str) -> list[str]:
    return [
        f"{label}:{literal.lineno} {literal.value!r}"
        for literal in _event_literals(source)
        if literal.value not in REGISTERED
    ]


GUARD_SAMPLE = """
from backend.services.audit_event_types import AuditEventType

CONSTANT_EVENT = "{constant}"
HELPER_CONSTANT_EVENT = "{helper_constant}"

def _audit(db, request, event_type, target_id):
    record_audit_event(db, event_type=event_type, target_type="meeting", target_id=target_id)

def _keyword_audit(db, *, event):
    record_audit_event(db, event_type=event, target_type="meeting")

def handler(db, request):
    record_audit_event(db, event_type="{direct}", target_type="meeting")
    record_audit_event(db, event_type=AuditEventType.MEETING_UPLOADED, target_type="meeting")
    _audit(db, request, "{positional}", "m-1")
    _keyword_audit(db, event="{keyword}")
    event_type = "{assigned}"
    record_audit_event(db, event_type=CONSTANT_EVENT, target_type="meeting")
    _audit(db, request, HELPER_CONSTANT_EVENT, "m-2")
"""
GUARD_SAMPLE_KEYS = ("direct", "positional", "keyword", "assigned", "constant", "helper_constant")


def _sample(**names: str) -> str:
    return GUARD_SAMPLE.format(
        **{key: names.get(key, "meeting.uploaded") for key in GUARD_SAMPLE_KEYS}
    )


@pytest.mark.parametrize("key", GUARD_SAMPLE_KEYS)
def test_static_guard_reports_an_unregistered_literal(key):
    found = _unregistered(_sample(**{key: "not.registered"}), "sample.py")
    assert len(found) == 1 and "'not.registered'" in found[0], found


def test_static_guard_passes_registered_literals_and_registry_members():
    sample = _sample()
    assert _unregistered(sample, "sample.py") == []
    assert len(_event_literals(sample)) == len(GUARD_SAMPLE_KEYS)


def test_backend_source_writes_only_registered_event_type_literals():
    found: list[str] = []
    checked: set[str] = set()
    for path in _source_files():
        source = path.read_text(encoding="utf-8")
        label = path.relative_to(BACKEND.parent).as_posix()
        found += _unregistered(source, label)
        checked |= {literal.value for literal in _event_literals(source)}
    assert found == [], "audit event types missing from the registry: " + ", ".join(found)
    # the frozen files keep their literals; the guard must still see them
    assert {
        "service_location.created",
        "transcript.redaction_created",
        "auth.otp_requested",
        "voicemail.classified",
    } <= checked


def test_non_frozen_backend_source_uses_registry_members_not_literals():
    frozen = {BACKEND.parent / relative for relative in FROZEN_FILES}
    registry = BACKEND / "services" / "audit_event_types.py"
    found: list[str] = []
    for path in _source_files():
        if path in frozen or path == registry:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and node.value in REGISTERED:
                found.append(f"{path.relative_to(BACKEND.parent).as_posix()}:{node.lineno}")
    assert found == [], "use AuditEventType members instead of literals: " + ", ".join(found)
