"""HOM-138 (F70, D6-D8, A9-A12): retention, check order, revocation and headers.

Exports are never stored, so each download runs authorization again.  A
workspace export of a conversation the retention sweep would expire now is
``410 conversation_retention_expired``; a legal hold or an active job keeps
it exportable, exactly as it keeps the sweep away.  A share export cannot see
jobs or media rows, so it refuses any due expiry.  The order is: access
(404 for anyone who may not read), retention (410), revision pin (409).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import AuditEvent, JobState, Meeting, RetentionMode
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_authorization_matrix import _insert_meeting, _workspace_with_roles
from backend.tests.test_playback_navigation import _Playable
from backend.tests.test_share_policies import _cookies, _share, _token

TRANSCRIPT_FORMATS = ["txt", "json", "srt", "vtt"]
REPORT_FORMATS = ["pdf", "csv", "json"]
STALE_REVISION = "00000000-0000-0000-0000-000000000000"
FILENAME = re.compile(
    r'attachment; filename="homayar-(?:report-)?[0-9a-f-]{36}\.(?:pdf|csv|json|txt|srt|vtt)"'
)


def _exported_count(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "meeting.exported", AuditEvent.target_id == meeting_id)
        )


def _error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text
    assert response.headers["cache-control"] == "no-store"


def _same_refusal(export, read) -> None:
    """An export is refused exactly like the matching read route."""
    assert export.status_code == read.status_code, (export.text, read.text)
    assert export.status_code in (401, 403, 404)
    assert export.json()["error"]["code"] == read.json()["error"]["code"]
    assert export.headers["cache-control"] == "no-store"


class _Exports(_Playable):
    """The playback fixture plus a workspace share that allows exports."""

    def __init__(self, client, email_outbox, settings, monkeypatch) -> None:
        super().__init__(client, email_outbox, settings, monkeypatch)
        created = _share(
            client,
            self.owner_headers,
            self.owner_cookies,
            self.meeting_id,
            audience="workspace",
            allow_export=True,
        )
        assert created.status_code == 201, created.text
        self.export_token = _token(created.json())
        self.export_share_id = created.json()["id"]

    def transcript_export(self, format_name: str = "txt", **params):
        return self.owner_get(
            f"/api/v1/meetings/{self.meeting_id}/export", params={"format": format_name, **params}
        )

    def report_export(self, format_name: str = "pdf", **params):
        return self.owner_get(
            f"/api/v1/meetings/{self.meeting_id}/report/export",
            params={"format": format_name, **params},
        )

    def share_export(self, format_name: str = "txt"):
        return self.client.get(
            f"/api/v1/shares/{self.export_token}/export",
            params={"format": format_name},
            cookies=self.owner_cookies,
        )

    def workspace_exports(self) -> dict[str, object]:
        answers: dict[str, object] = {}
        for format_name in TRANSCRIPT_FORMATS:
            answers[f"transcript.{format_name}"] = self.transcript_export(format_name)
        for format_name in REPORT_FORMATS:
            answers[f"report.{format_name}"] = self.report_export(format_name)
        return answers

    def assert_workspace_exports(self, status: int, code: str | None = None) -> None:
        before = _exported_count(self.meeting_id)
        answers = self.workspace_exports()
        for name, response in answers.items():
            assert response.status_code == status, (name, response.text)
            assert response.headers["cache-control"] == "no-store", name
            if code is not None:
                assert response.json()["error"]["code"] == code, (name, response.text)
        expected = before + (len(answers) if status == 200 else 0)
        assert _exported_count(self.meeting_id) == expected

    def assert_share_export(self, status: int, code: str | None = None) -> None:
        response = self.share_export()
        if code is None:
            assert response.status_code == status, response.text
        else:
            _error(response, status, code)


@pytest.fixture
def exports(client, email_outbox, settings, monkeypatch) -> _Exports:
    return _Exports(client, email_outbox, settings, monkeypatch)


# --- A9: retention -------------------------------------------------------------


def test_a_due_expiry_is_410_on_every_export_route(exports):
    exports.set_expiry(timedelta(minutes=-1))
    exports.assert_workspace_exports(410, "conversation_retention_expired")
    exports.assert_share_export(410, "conversation_retention_expired")
    # allow-control: the same conversation with its expiry in the future
    exports.set_expiry(timedelta(days=1))
    exports.assert_workspace_exports(200)
    exports.assert_share_export(200)


def test_a_legal_hold_keeps_the_export_but_not_through_a_share(exports):
    exports.hold()
    exports.set_expiry(timedelta(minutes=-1))
    exports.assert_workspace_exports(200)
    exports.assert_share_export(410, "conversation_retention_expired")


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING])
def test_an_active_job_keeps_the_export_but_not_through_a_share(exports, state):
    exports.set_expiry(timedelta(minutes=-1))
    exports.set_job_state(state)
    exports.assert_workspace_exports(200)
    exports.assert_share_export(410, "conversation_retention_expired")
    # control: once the job is terminal the expiry applies
    exports.set_job_state(JobState.COMPLETED)
    exports.assert_workspace_exports(410, "conversation_retention_expired")


def test_an_indefinite_workspace_never_expires_an_export(exports):
    exports.set_expiry(timedelta(minutes=-1))
    exports.set_mode(RetentionMode.INDEFINITE)
    exports.assert_workspace_exports(200)
    exports.assert_share_export(200)


# --- A10: order of checks --------------------------------------------------------


def test_an_outsider_gets_404_not_410_for_an_expired_conversation(exports, email_outbox):
    exports.set_expiry(timedelta(minutes=-1))
    credentials = register_user(exports.client, email_outbox, email="hom138-outsider@example.com")
    session = login_user(exports.client, credentials)
    outsider_cookies = _cookies(exports.client)
    own_workspace = session["workspaces"][0]["id"]
    for path in (
        f"/api/v1/meetings/{exports.meeting_id}/export?format=txt",
        f"/api/v1/meetings/{exports.meeting_id}/report/export?format=pdf",
    ):
        own = exports.client.get(
            path, cookies=outsider_cookies, headers={"X-Workspace-ID": own_workspace}
        )
        _error(own, 404, "meeting_not_found")
        foreign = exports.client.get(
            path, cookies=outsider_cookies, headers={"X-Workspace-ID": exports.workspace_id}
        )
        read = exports.client.get(
            f"/api/v1/meetings/{exports.meeting_id}",
            cookies=outsider_cookies,
            headers={"X-Workspace-ID": exports.workspace_id},
        )
        _same_refusal(foreign, read)
    # control: the same outsider gets the same answer for a conversation that is not due
    exports.set_expiry(timedelta(days=1))
    again = exports.client.get(
        f"/api/v1/meetings/{exports.meeting_id}/export?format=txt",
        cookies=outsider_cookies,
        headers={"X-Workspace-ID": own_workspace},
    )
    _error(again, 404, "meeting_not_found")


def test_retention_is_checked_before_the_revision_pin(exports):
    exports.set_expiry(timedelta(minutes=-1))
    before = _exported_count(exports.meeting_id)
    _error(
        exports.transcript_export(revision_id=STALE_REVISION),
        410,
        "conversation_retention_expired",
    )
    _error(exports.report_export(revision_id=STALE_REVISION), 410, "conversation_retention_expired")
    # control: not due, the same wrong pin is the 409 of HOM-137
    exports.set_expiry(timedelta(days=1))
    _error(
        exports.transcript_export(revision_id=STALE_REVISION), 409, "transcript_revision_mismatch"
    )
    _error(exports.report_export(revision_id=STALE_REVISION), 409, "transcript_revision_mismatch")
    assert _exported_count(exports.meeting_id) == before


# --- A11: revocation is immediate -------------------------------------------------


def _member_paths(meeting_id: str) -> dict[str, str]:
    return {
        "transcript": f"/api/v1/meetings/{meeting_id}/export?format=txt",
        "report": f"/api/v1/meetings/{meeting_id}/report/export?format=json",
    }


def test_a_removed_member_cannot_export_the_next_time(client, email_outbox, monkeypatch):
    owner, personas, memberships = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = personas["member"]
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="جلسه")
    for path in _member_paths(meeting_id).values():
        response = member.get(path)
        assert response.status_code == 200, (path, response.text)

    removed = owner.delete(f"/api/v1/members/{memberships['member']}")
    assert removed.status_code == 204, removed.text
    read = member.get(f"/api/v1/meetings/{meeting_id}")
    for path in _member_paths(meeting_id).values():
        _same_refusal(member.get(path), read)


def test_a_member_who_loses_record_access_cannot_export(client, email_outbox, monkeypatch):
    owner, personas, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = personas["member"]
    meeting_id = _insert_meeting(owner.workspace_id, created_by=owner.user_id, title="جلسه")
    for path in _member_paths(meeting_id).values():
        assert member.get(path).status_code == 200, path

    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200, switched.text
    read = member.get(f"/api/v1/meetings/{meeting_id}")
    assert read.status_code == 404, read.text
    for path in _member_paths(meeting_id).values():
        _same_refusal(member.get(path), read)
    # control: the owner still exports
    for path in _member_paths(meeting_id).values():
        assert owner.get(path).status_code == 200, path


def test_a_tombstoned_conversation_is_404_on_every_export(exports):
    exports.assert_workspace_exports(200)
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, exports.meeting_id)
        meeting.deleted_at = datetime.now(UTC)
        db.commit()
    for name, response in exports.workspace_exports().items():
        assert response.status_code == 404, (name, response.text)
        assert response.headers["cache-control"] == "no-store"
    read = exports.owner_get(f"/api/v1/meetings/{exports.meeting_id}")
    assert read.status_code == 404
    shared = exports.share_export()
    _same_refusal(
        shared,
        exports.client.get(f"/api/v1/shares/{exports.export_token}", cookies=exports.owner_cookies),
    )


def test_a_revoked_share_cannot_export(exports):
    exports.assert_share_export(200)
    revoked = exports.client.delete(
        f"/api/v1/meetings/{exports.meeting_id}/shares/{exports.export_share_id}",
        headers=exports.owner_headers,
        cookies=exports.owner_cookies,
    )
    assert revoked.status_code == 204, revoked.text
    view = exports.client.get(
        f"/api/v1/shares/{exports.export_token}", cookies=exports.owner_cookies
    )
    _same_refusal(exports.share_export(), view)


# --- A12: headers ------------------------------------------------------------------


def test_the_filename_never_carries_the_title(exports):
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, exports.meeting_id)
        meeting.title = 'گزارش "x"\r\nX-Injected: yes; filename="evil.exe"'
        db.commit()
    answers = {**exports.workspace_exports()}
    for format_name in TRANSCRIPT_FORMATS:
        answers[f"share.{format_name}"] = exports.share_export(format_name)
    for name, response in answers.items():
        assert response.status_code == 200, (name, response.text)
        disposition = response.headers["content-disposition"]
        assert FILENAME.fullmatch(disposition), (name, disposition)
        assert disposition.isascii()
        assert exports.meeting_id in disposition
        assert "x-injected" not in response.headers
        assert response.headers["cache-control"] == "no-store"
