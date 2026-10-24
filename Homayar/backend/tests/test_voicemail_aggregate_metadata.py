"""HOM-299: coverage, scorecard and missing-data metadata on ``GET /voicemails/analytics``.

The population is the voicemail rows that ``total`` counts (ADR 0041 decision 3): the
workspace, a live meeting, the caller's record scope and ``received_at`` in the window.  The
``processing`` and ``evaluation`` counters use the ``coverage/1`` rules over their meetings.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import update

from backend.database.saas_models import (
    Meeting,
    MeetingStatus,
    RecordScope,
    TranscriptRevision,
    TranscriptSource,
)
from backend.security.context import CSRF_COOKIE
from backend.services.aggregate_metadata import scorecard_block
from backend.services.coverage_service import EVALUATION_BUCKETS, PROCESSING_BUCKETS
from backend.tests._database import inspection_session_factory
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_organizations import _persona
from backend.tests.test_role_trend_routes import _install_member, _set_record_scope, _statements
from backend.voicemail.models import VOICEMAIL_MEETING_SOURCE, VoicemailMessage

ANALYTICS = "/api/v1/voicemails/analytics"
DAY = timedelta(days=1)


def _voicemail(
    workspace_id: str,
    *,
    created_by: str,
    received_at: datetime,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    segments: int | None = 2,
    deleted: bool = False,
    category: str | None = None,
) -> str:
    """A voicemail row and its meeting (no ledger row, as demo data), with a current
    transcript revision of ``segments`` segments unless ``segments`` is ``None``."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=created_by,
            title="Voicemail",
            status=status,
            duration_seconds=10,
            deleted_at=received_at if deleted else None,
            source=VOICEMAIL_MEETING_SOURCE,
        )
        db.add(meeting)
        db.flush()
        if segments is not None:
            db.add(
                TranscriptRevision(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    revision_number=1,
                    source=TranscriptSource.ASR,
                    reason="asr",
                    language="fa",
                    segment_count=segments,
                    is_current=True,
                )
            )
        db.add(
            VoicemailMessage(
                workspace_id=workspace_id,
                meeting_id=meeting.id,
                external_message_id=f"vm-{uuid.uuid4().hex}",
                received_at=received_at,
                duration_seconds=10,
                category=category,
            )
        )
        db.commit()
        return meeting.id


def _analytics(persona, workspace_id: str, **params: str) -> dict[str, Any]:
    response = persona.get(ANALYTICS, workspace_id=workspace_id, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _assert_reconciles(body: dict[str, Any]) -> None:
    """AC1, AC2 and AC5."""
    coverage = body["coverage"]
    assert coverage["version"] == "coverage/1"
    assert coverage["basis"] == "voicemail_population"
    assert coverage["window"] == {
        "from": body["received_from"],
        "to": body["received_to"],
        "basis": "voicemail_received_at",
    }
    processing, evaluation = coverage["processing"], coverage["evaluation"]
    assert processing["denominator"] == body["total"]
    assert list(processing["buckets"]) == list(PROCESSING_BUCKETS)
    assert sum(processing["buckets"].values()) == processing["denominator"]
    assert processing["buckets"]["deleted"] == 0
    assert evaluation["denominator"] == processing["buckets"]["transcribed"]
    assert list(evaluation["buckets"]) == list(EVALUATION_BUCKETS)
    assert sum(evaluation["buckets"].values()) == evaluation["denominator"]
    assert body["scorecard"] == scorecard_block()
    codes = body["missing_data"]["indicators"]
    assert codes == sorted(set(codes)) and "scorecard_unavailable" in codes
    assert ("empty_population" in codes) == (body["total"] == 0)


def test_voicemail_coverage_reconciles_with_total(client, email_outbox):
    owner = _persona(client, email_outbox, email="vm-owner@example.com", name="Owner")
    stranger = _persona(client, email_outbox, email="vm-stranger@example.com", name="Str")
    workspace = owner.personal_workspace_id
    start = datetime.now(UTC).replace(microsecond=0) - 20 * DAY
    end = start + 10 * DAY
    inside = start + DAY
    transcribed = _voicemail(workspace, created_by=owner.user_id, received_at=inside)
    _voicemail(workspace, created_by=owner.user_id, received_at=inside, segments=None)
    _voicemail(workspace, created_by=owner.user_id, received_at=inside, status=MeetingStatus.QUEUED)
    _voicemail(workspace, created_by=owner.user_id, received_at=inside, status=MeetingStatus.FAILED)
    _voicemail(workspace, created_by=owner.user_id, received_at=inside, deleted=True)
    _voicemail(workspace, created_by=owner.user_id, received_at=start - DAY)  # before the window
    _voicemail(
        stranger.personal_workspace_id, created_by=stranger.user_id, received_at=inside
    )  # another workspace
    window = {"received_from": start.isoformat(), "received_to": end.isoformat()}

    body = _analytics(owner, workspace, **window)
    # the existing fields, as on base
    assert body["total"] == 4
    assert body["by_status"] == {"received": 4}
    assert body["by_category"] == {"unclassified": 4}
    assert body["followed_up"] == 0 and body["median_follow_up_seconds"] is None
    assert sum(day["count"] for day in body["by_day"]) == 4
    _assert_reconciles(body)
    assert body["coverage"]["processing"]["buckets"] == {
        "deleted": 0,
        "in_progress": 1,
        "failed": 1,
        "transcribed": 1,
        "no_transcript": 1,
    }
    assert body["coverage"]["evaluation"]["denominator"] == 1
    assert body["missing_data"]["indicators"] == [
        "evaluation_incomplete",
        "processing_incomplete",
        "scorecard_unavailable",
    ]

    # the window: a voicemail before ``received_from`` adds no count; allow-control: a window
    # that holds it counts it
    wider = _analytics(
        owner,
        workspace,
        received_from=(start - 2 * DAY).isoformat(),
        received_to=end.isoformat(),
    )
    assert wider["total"] == 5
    _assert_reconciles(wider)
    assert wider["coverage"]["processing"]["buckets"]["transcribed"] == 2

    # the record scope: a restricted member sees no voicemail of the owner
    member = _persona(client, email_outbox, email="vm-member@example.com", name="Member")
    place = type("Place", (), {"workspace_id": workspace})()
    _install_member(place, member.user_id)
    _set_record_scope(place, RecordScope.TEAM)
    scoped = _analytics(member, workspace, **window)
    assert scoped["total"] == 0
    _assert_reconciles(scoped)
    assert scoped["missing_data"]["indicators"] == ["empty_population", "scorecard_unavailable"]
    assert scoped["coverage"]["processing"]["reason"] == "empty_denominator"
    # allow-control: a voicemail whose meeting has the member as agent is in their scope
    with inspection_session_factory()() as db:
        db.execute(
            update(Meeting).where(Meeting.id == transcribed).values(agent_user_id=member.user_id)
        )
        db.commit()
    scoped = _analytics(member, workspace, **window)
    assert scoped["total"] == 1
    _assert_reconciles(scoped)
    assert scoped["coverage"]["processing"]["buckets"]["transcribed"] == 1

    # an API key with meeting:read gets the same status as on base, with the metadata
    key = _mint(
        client,
        {"X-CSRF-Token": owner.cookies[CSRF_COOKIE], "X-Workspace-ID": workspace},
        owner.cookies,
        scopes=["meeting:read"],
    )
    assert key.status_code == 201, key.text
    client.cookies.clear()
    by_key = client.get(ANALYTICS, params=window, headers=_bearer(key.json()["key"]))
    assert by_key.status_code == 200, by_key.text
    assert by_key.json()["total"] == 4
    _assert_reconciles(by_key.json())


def test_voicemail_window_is_echoed_as_received(client, email_outbox):
    owner = _persona(client, email_outbox, email="vm-naive@example.com", name="Owner")
    workspace = owner.personal_workspace_id
    start = datetime.now(UTC).replace(microsecond=0) - 5 * DAY
    _voicemail(workspace, created_by=owner.user_id, received_at=start + DAY)

    # naive bounds (no timezone): the status stays as on base and the window is an echo
    naive = _analytics(
        owner,
        workspace,
        received_from=start.replace(tzinfo=None).isoformat(),
        received_to=(start + 3 * DAY).replace(tzinfo=None).isoformat(),
    )
    _assert_reconciles(naive)
    # the default window: the last 30 days
    default = _analytics(owner, workspace)
    assert default["total"] == 1
    _assert_reconciles(default)
    # an invalid window keeps its base answer
    refused = owner.get(
        ANALYTICS,
        workspace_id=workspace,
        params={
            "received_from": (start + DAY).isoformat(),
            "received_to": start.isoformat(),
        },
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_window"


def test_voicemail_statements_do_not_grow_with_the_voicemails(client, email_outbox):
    """ADR 0041 decision 25: no new statement without a completed or partial meeting, and
    the 8 coverage fact statements with one, whatever the number of voicemails."""
    owner = _persona(client, email_outbox, email="vm-count@example.com", name="Owner")
    workspace = owner.personal_workspace_id
    start = datetime.now(UTC).replace(microsecond=0) - 5 * DAY
    window = {"received_from": start.isoformat(), "received_to": (start + 3 * DAY).isoformat()}

    def count() -> int:
        return _statements(lambda: owner.get(ANALYTICS, workspace_id=workspace, params=window))[0]

    empty = count()
    _voicemail(
        workspace, created_by=owner.user_id, received_at=start + DAY, status=MeetingStatus.QUEUED
    )
    assert count() == empty  # no candidate: the existing statement only
    _voicemail(workspace, created_by=owner.user_id, received_at=start + DAY)
    one = count()
    for _ in range(5):
        _voicemail(workspace, created_by=owner.user_id, received_at=start + DAY)
    assert count() == one == empty + 8, (empty, one)
