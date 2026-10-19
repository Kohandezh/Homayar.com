"""WP12-T05 (HOM-62): a transcript correction needs a reason and records who made it.

A correction is refused without a reason (or with one that is blank or too
short once whitespace is collapsed); the reason is stored on the new revision
and in the audit event, never the transcript text. The older ``note`` field is
still accepted. A speaker-only edit moves a turn to another meeting speaker in
a new revision. Viewers cannot correct, members with access can, other
workspaces see nothing, and a key acts as the user it is bound to.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import (
    AuditEvent,
    Meeting,
    MeetingSpeaker,
    Membership,
    TranscriptRevision,
    WorkspaceRole,
)
from backend.security.context import CSRF_COOKIE
from backend.services.transcript_service import SegmentEdit, TranscriptError, correct_revision
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_insights_shares_api import _processed_meeting, _write_headers
from backend.tests.test_share_policies import _cookies


def _transcript(client, workspace_id: str, meeting_id: str, **params) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers={"X-Workspace-ID": workspace_id},
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _correct(client, workspace_id: str, meeting_id: str, payload: dict, headers=None):
    return client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=headers or _write_headers(client, workspace_id),
        json=payload,
    )


def _revision_count(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(TranscriptRevision)
            .where(TranscriptRevision.meeting_id == meeting_id)
        )


def _correction_audits(meeting_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        events = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type == "transcript.revision.created")
            .order_by(AuditEvent.occurred_at.asc())
        ).all()
        return [event for event in events if event.details.get("meeting_id") == meeting_id]


def _set_role(workspace_id: str, user_id: str, role: WorkspaceRole) -> None:
    with inspection_session_factory()() as db:
        membership = db.scalar(
            select(Membership).where(
                Membership.workspace_id == workspace_id, Membership.user_id == user_id
            )
        )
        assert membership is not None
        membership.role = role
        db.commit()


def _text_edit(transcript: dict, text: str = "پیگیری فاکتور انجام شود") -> dict:
    """A valid body against the current revision that really changes the text."""
    return {
        "base_revision_id": transcript["revision"]["id"],
        "edits": [{"segment_id": transcript["segments"][0]["id"], "text": text}],
    }


def test_a_correction_without_a_usable_reason_is_refused_and_writes_nothing(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    original = _transcript(client, workspace_id, meeting_id)
    body = _text_edit(original)

    missing = _correct(client, workspace_id, meeting_id, body)
    assert missing.status_code == 422, missing.text
    for reason in ("", "     ", "ab", "  a    ", " a\n\t", "x" * 501):
        refused = _correct(client, workspace_id, meeting_id, {**body, "reason": reason})
        assert refused.status_code == 422, (reason, refused.text)
    blank_note = _correct(client, workspace_id, meeting_id, {**body, "note": "   "})
    assert blank_note.status_code == 422, blank_note.text
    assert _revision_count(meeting_id) == 1
    assert _correction_audits(meeting_id) == []

    # allow-control: the same base and the same edit pass once a reason is given
    allowed = _correct(client, workspace_id, meeting_id, {**body, "reason": "typo"})
    assert allowed.status_code == 201, allowed.text
    assert _revision_count(meeting_id) == 2


def test_the_reason_is_stored_normalised_and_audited_with_the_reviewer(
    client, email_outbox, settings, monkeypatch
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    session = login_user(client, credentials)
    original = _transcript(client, workspace_id, meeting_id)

    corrected = _correct(
        client,
        workspace_id,
        meeting_id,
        {**_text_edit(original), "reason": "  wrong   speaker "},
    )
    assert corrected.status_code == 201, corrected.text
    revision = corrected.json()["revision"]
    assert revision["note"] == "wrong speaker"
    assert revision["reason"] == "human_correction"  # the machine code is unchanged
    assert revision["created_by_user_id"] == session["user"]["id"]
    assert revision["parent_revision_id"] == original["revision"]["id"]

    listed = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript/revisions",
        headers={"X-Workspace-ID": workspace_id},
    ).json()["items"][0]
    assert (listed["id"], listed["note"], listed["created_by_user_id"]) == (
        revision["id"],
        "wrong speaker",
        session["user"]["id"],
    )

    (audit,) = _correction_audits(meeting_id)
    assert audit.actor_user_id == session["user"]["id"]
    assert audit.target_id == revision["id"]
    assert audit.details["correction_reason"] == "wrong speaker"
    assert audit.details["reason"] == "human_correction"
    assert audit.details["segments_changed"] == 1
    assert "فاکتور" not in str(audit.details) and "قرارداد" not in str(audit.details)


def test_the_legacy_note_field_is_still_accepted_as_the_reason(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    original = _transcript(client, workspace_id, meeting_id)

    corrected = _correct(
        client,
        workspace_id,
        meeting_id,
        {**_text_edit(original), "note": "اصلاح   نام محصول"},
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["revision"]["note"] == "اصلاح نام محصول"
    (audit,) = _correction_audits(meeting_id)
    assert audit.details["correction_reason"] == "اصلاح نام محصول"


def test_a_speaker_only_edit_moves_the_turn_in_a_new_revision(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    original = _transcript(client, workspace_id, meeting_id)
    segment = original["segments"][0]
    with inspection_session_factory()() as db:
        db.add(
            MeetingSpeaker(
                workspace_id=workspace_id, meeting_id=meeting_id, speaker_key="speaker_moved"
            )
        )
        db.commit()
    assert segment["speaker_key"] != "speaker_moved"

    moved = _correct(
        client,
        workspace_id,
        meeting_id,
        {
            "base_revision_id": original["revision"]["id"],
            "reason": "turn belongs to the customer",
            "edits": [{"segment_id": segment["id"], "speaker_key": "speaker_moved"}],
        },
    )
    assert moved.status_code == 201, moved.text
    payload = moved.json()
    assert payload["revision"]["revision_number"] == original["revision"]["revision_number"] + 1
    new_segment = payload["segments"][0]
    assert new_segment["speaker_key"] == "speaker_moved"
    assert new_segment["text"] == segment["text"]
    assert new_segment["confidence"] == segment["confidence"]  # the text was not touched
    assert (new_segment["start_ms"], new_segment["end_ms"], new_segment["sequence"]) == (
        segment["start_ms"],
        segment["end_ms"],
        segment["sequence"],
    )

    old = _transcript(client, workspace_id, meeting_id, revision_id=original["revision"]["id"])
    assert old["revision"]["is_current"] is False
    assert old["segments"] == original["segments"]
    assert old["segments"][0]["speaker_key"] == segment["speaker_key"]


def test_a_viewer_cannot_correct_and_a_member_with_access_can(
    client, email_outbox, settings, monkeypatch
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    user_id = login_user(client, credentials)["user"]["id"]
    original = _transcript(client, workspace_id, meeting_id)
    body = {**_text_edit(original), "reason": "wrong product name"}

    _set_role(workspace_id, user_id, WorkspaceRole.VIEWER)
    denied = _correct(client, workspace_id, meeting_id, body)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "permission_denied"
    assert _revision_count(meeting_id) == 1
    assert _correction_audits(meeting_id) == []

    # allow-control: the same caller, meeting and body, as a member who created it
    _set_role(workspace_id, user_id, WorkspaceRole.MEMBER)
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None and meeting.created_by_user_id == user_id
    allowed = _correct(client, workspace_id, meeting_id, body)
    assert allowed.status_code == 201, allowed.text
    assert allowed.json()["revision"]["created_by_user_id"] == user_id
    assert _revision_count(meeting_id) == 2


def test_another_workspace_gets_404_and_nothing_is_written(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    body = {
        **_text_edit(_transcript(client, workspace_id, meeting_id)),
        "reason": "wrong product name",
    }

    outsider = register_user(client, email_outbox, email="correction-outsider@example.com")
    other_workspace = login_user(client, outsider)["workspaces"][0]["id"]
    headers = {"X-Workspace-ID": other_workspace, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)}
    refused = _correct(client, other_workspace, meeting_id, body, headers=headers)
    assert refused.status_code == 404, refused.text
    assert refused.json()["error"]["code"] == "meeting_not_found"
    assert _revision_count(meeting_id) == 1


def test_a_correction_through_a_key_records_the_bound_user(
    client, email_outbox, settings, monkeypatch
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    user_id = login_user(client, credentials)["user"]["id"]
    cookies = _cookies(client)
    minted = _mint(
        client,
        {"X-Workspace-ID": workspace_id, "X-CSRF-Token": cookies[CSRF_COOKIE]},
        cookies,
        scopes=("meeting:read", "meeting:update"),
    )
    assert minted.status_code == 201, minted.text
    original = _transcript(client, workspace_id, meeting_id)
    client.cookies.clear()

    corrected = _correct(
        client,
        workspace_id,
        meeting_id,
        {**_text_edit(original), "reason": "fixed by the CRM sync"},
        headers=_bearer(minted.json()["key"]),
    )
    assert corrected.status_code == 201, corrected.text
    assert corrected.json()["revision"]["created_by_user_id"] == user_id
    (audit,) = _correction_audits(meeting_id)
    assert audit.actor_user_id == user_id
    assert audit.details["correction_reason"] == "fixed by the CRM sync"


@pytest.mark.parametrize("note", [None, "", "   "])
def test_the_service_refuses_a_correction_without_a_reason(
    client, email_outbox, settings, monkeypatch, note
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    user_id = login_user(client, credentials)["user"]["id"]
    original = _transcript(client, workspace_id, meeting_id)
    segment_id = original["segments"][0]["id"]

    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        with pytest.raises(TranscriptError) as refused:
            correct_revision(
                db,
                meeting=meeting,
                base_revision_id=original["revision"]["id"],
                edits=[SegmentEdit(segment_id, text="پیگیری فاکتور انجام شود")],
                actor_user_id=user_id,
                now=datetime.now(UTC),
                note=note,
            )
        db.rollback()
    assert (refused.value.code, refused.value.status_code) == ("reason_required", 422)
    assert _revision_count(meeting_id) == 1
