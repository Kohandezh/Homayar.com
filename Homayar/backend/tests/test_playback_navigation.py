"""HOM-60 (WP12): synchronized audio and transcript navigation.

The console player (HOM-247) seeks the recording and the transcript together.
``GET /meetings/{id}/playback`` describes an authorized, time-ranged media
URL; ``GET /meetings/{id}/transcript`` filters by speaker and time; both say
how complete the analysis is with the report's own completeness rule.  Every
route that sends recording bytes applies one playability rule: a live media
object and no retention expiry the sweep would act on.  The share route uses
a stricter rule, because a share session cannot read jobs or media objects.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from backend.database.saas_models import (
    AuditEvent,
    JobState,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    Recording,
    RetentionMode,
    TranscriptSource,
    User,
    Workspace,
)
from backend.services.audit_event_types import AuditEventType
from backend.services.report_service import (
    NOTICE_UNSUPPORTED_FINDINGS,
    completeness_notices,
    is_report_complete,
)
from backend.services.transcript_service import SegmentDraft, create_revision, ensure_speakers
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_call_report import GROUNDED, _bundle, _report, _set_status
from backend.tests.test_call_report import _quality as _report_quality
from backend.tests.test_evidence_spans import _citing_bundle, _processed
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_privacy_routing import SEGMENTS as PII_TEXTS
from backend.tests.test_privacy_routing import _no_raw
from backend.tests.test_share_policies import (
    STAGED_BYTES,
    _anonymous,
    _cookies,
    _settings,
    _share,
    _signup,
    _token,
)
from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata

PLAYBACK_KEYS = {
    "meeting_id",
    "media_url",
    "url",
    "start_ms",
    "end_ms",
    "duration_ms",
    "content_type",
    "size_bytes",
    "accept_ranges",
    "completeness",
    "privacy_enforced",
    "audio_redacted",
}
SEGMENT_KEYS = {
    "id",
    "sequence",
    "speaker_key",
    "speaker_name",
    "start_ms",
    "end_ms",
    "text",
    "confidence",
    "language",
}
# speaker, start_ms, end_ms, text: sequence is the position.
TIMELINE = (
    ("spk_0", 0, 10_000, "سلام و وقت بخیر"),
    ("spk_1", 10_000, 20_000, "قیمت را فردا می‌گوییم"),
    ("spk_0", 15_000, 30_000, "باشد، منتظر می‌مانیم"),
    ("spk_2", 30_000, 40_000, "جلسهٔ بعد سه‌شنبه"),
    ("spk_1", 40_000, 50_000, "خداحافظ"),
)


# --- helpers -------------------------------------------------------------------


def _workspace(client, email_outbox) -> tuple[str, str]:
    credentials, workspace_id = _authenticated_workspace(client, email_outbox)
    with inspection_session_factory()() as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        assert user is not None
        return workspace_id, user.id


def _add_revision(db, meeting: Meeting, timeline) -> None:
    create_revision(
        db,
        meeting=meeting,
        source=TranscriptSource.ASR,
        reason="asr",
        language="fa",
        drafts=[
            SegmentDraft(sequence=index, speaker_key=speaker, start_ms=start, end_ms=end, text=text)
            for index, (speaker, start, end, text) in enumerate(timeline)
        ],
        now=datetime.now(UTC),
    )
    ensure_speakers(
        db,
        workspace_id=meeting.workspace_id,
        meeting_id=meeting.id,
        labels=[speaker for speaker, *_ in timeline],
    )


def _seed(workspace_id: str, user_id: str, timeline=TIMELINE) -> str:
    """A completed meeting whose current revision is ``timeline``."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            title="Navigation call",
            status=MeetingStatus.COMPLETED,
            duration_seconds=50,
        )
        db.add(meeting)
        db.flush()
        _add_revision(db, meeting, timeline)
        db.commit()
        return meeting.id


def _transcript(client, workspace_id: str, meeting_id: str, **params):
    return client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers={"X-Workspace-ID": workspace_id},
        params=params,
    )


def _playback(client, workspace_id: str, meeting_id: str, **params):
    return client.get(
        f"/api/v1/meetings/{meeting_id}/playback",
        headers={"X-Workspace-ID": workspace_id},
        params=params,
    )


def _ok(response) -> dict:
    assert response.status_code == 200, response.text
    return response.json()


def _error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code, response.text


def _sequences(body: dict) -> list[int]:
    return [segment["sequence"] for segment in body["segments"]]


def _without_unsupported(completeness: dict) -> dict:
    """The report's completeness with ``unsupported=0``: what the transcript
    and playback must say (notices are empty exactly when complete)."""
    notices = [n for n in completeness["notices"] if n != NOTICE_UNSUPPORTED_FINDINGS]
    return {**completeness, "notices": notices, "is_complete": not notices}


def _original_reads(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.event_type == AuditEventType.TRANSCRIPT_ORIGINAL_ACCESSED.value,
                AuditEvent.target_id == meeting_id,
            )
        )


def _patch_upload(monkeypatch, *, duration_seconds: float = 10.0) -> None:
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=duration_seconds),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)


def _persona_upload(client, persona, key: str) -> str:
    response = client.post(
        "/api/v1/uploads",
        headers={**persona.headers, "Idempotency-Key": key},
        cookies=persona.cookies,
        data={"title": "Weekly customer call", "language": "fa"},
        files={"file": ("customer-call.wav", STAGED_BYTES, "audio/wav")},
    )
    assert response.status_code == 202, response.text
    return response.json()["meeting"]["id"]


# --- transcript filter (AC1, AC2, AC3, AC4) ---------------------------------------


def test_speaker_filter_keeps_only_the_named_speakers(client, email_outbox):
    workspace_id, user_id = _workspace(client, email_outbox)
    meeting_id = _seed(workspace_id, user_id)
    base = _ok(_transcript(client, workspace_id, meeting_id))

    one = _ok(_transcript(client, workspace_id, meeting_id, speaker_key="spk_1"))
    assert _sequences(one) == [1, 4]
    assert one["filters"] == {"speaker_keys": ["spk_1"], "start_ms": None, "end_ms": None}

    two = _ok(
        _transcript(client, workspace_id, meeting_id, speaker_key=["spk_2", "spk_0", "spk_2"])
    )
    assert _sequences(two) == [0, 2, 3]
    # sorted and de-duplicated
    assert two["filters"] == {"speaker_keys": ["spk_0", "spk_2"], "start_ms": None, "end_ms": None}

    for body in (one, two):
        assert body["total_segments"] == 5
        # every chip stays available to the console
        assert body["speakers"] == base["speakers"]
        assert [s["speaker_key"] for s in body["speakers"]] == ["spk_0", "spk_1", "spk_2"]
        by_id = {segment["id"]: segment for segment in base["segments"]}
        assert all(segment == by_id[segment["id"]] for segment in body["segments"])


def test_time_window_keeps_overlapping_segments_only(client, email_outbox):
    workspace_id, user_id = _workspace(client, email_outbox)
    meeting_id = _seed(workspace_id, user_id)

    window = _ok(_transcript(client, workspace_id, meeting_id, start_ms=10_000, end_ms=30_000))
    # segment 0 ends exactly at start_ms and segment 3 starts exactly at end_ms: both out
    assert _sequences(window) == [1, 2]
    assert window["filters"] == {"speaker_keys": [], "start_ms": 10_000, "end_ms": 30_000}
    assert window["total_segments"] == 5

    # a segment that starts before start_ms and ends after it is kept
    crossing = _ok(_transcript(client, workspace_id, meeting_id, start_ms=5_000, end_ms=12_000))
    assert _sequences(crossing) == [0, 1]
    open_end = _ok(_transcript(client, workspace_id, meeting_id, start_ms=35_000))
    assert _sequences(open_end) == [3, 4]
    assert open_end["filters"] == {"speaker_keys": [], "start_ms": 35_000, "end_ms": None}
    open_start = _ok(_transcript(client, workspace_id, meeting_id, end_ms=10_001))
    assert _sequences(open_start) == [0, 1]

    # filters combine with AND
    combined = _ok(
        _transcript(client, workspace_id, meeting_id, speaker_key="spk_0", start_ms=12_000)
    )
    assert _sequences(combined) == [2]


def test_a_bad_window_is_invalid_time_range(client, email_outbox):
    workspace_id, user_id = _workspace(client, email_outbox)
    meeting_id = _seed(workspace_id, user_id)
    for start_ms, end_ms in ((10_000, 10_000), (20_000, 10_000)):
        _error(
            _transcript(client, workspace_id, meeting_id, start_ms=start_ms, end_ms=end_ms),
            422,
            "invalid_time_range",
        )
    # allow-control: the smallest valid window
    allowed = _ok(_transcript(client, workspace_id, meeting_id, start_ms=10_000, end_ms=10_001))
    assert _sequences(allowed) == [1]


def test_no_filter_returns_every_segment_unchanged(client, email_outbox):
    workspace_id, user_id = _workspace(client, email_outbox)
    meeting_id = _seed(workspace_id, user_id)
    body = _ok(_transcript(client, workspace_id, meeting_id))
    assert body["filters"] == {"speaker_keys": [], "start_ms": None, "end_ms": None}
    assert body["total_segments"] == 5
    assert all(set(segment) == SEGMENT_KEYS for segment in body["segments"])
    assert [
        (s["sequence"], s["speaker_key"], s["start_ms"], s["end_ms"], s["text"])
        for s in body["segments"]
    ] == [(index, *row) for index, row in enumerate(TIMELINE)]
    # a window that holds everything returns the same segments
    whole = _ok(_transcript(client, workspace_id, meeting_id, start_ms=0, end_ms=50_000))
    assert whole["segments"] == body["segments"]


def test_an_unknown_speaker_matches_nothing(client, email_outbox):
    workspace_id, user_id = _workspace(client, email_outbox)
    meeting_id = _seed(workspace_id, user_id)
    body = _ok(_transcript(client, workspace_id, meeting_id, speaker_key="nobody"))
    assert body["segments"] == [] and body["total_segments"] == 5
    assert len(body["speakers"]) == 3


def test_filter_input_is_validated(client, email_outbox):
    workspace_id, user_id = _workspace(client, email_outbox)
    meeting_id = _seed(workspace_id, user_id)
    for params in (
        {"speaker_key": [f"spk_{n}" for n in range(21)]},
        {"speaker_key": "x" * 65},
        {"speaker_key": ""},
        {"start_ms": -1},
        {"end_ms": 0},
    ):
        _error(_transcript(client, workspace_id, meeting_id, **params), 422, "validation_error")
    # allow-controls at the limits
    twenty = _ok(
        _transcript(client, workspace_id, meeting_id, speaker_key=[f"spk_{n}" for n in range(20)])
    )
    assert _sequences(twenty) == [0, 1, 2, 3, 4]
    assert (
        _ok(_transcript(client, workspace_id, meeting_id, speaker_key="x" * 64))["segments"] == []
    )
    assert _sequences(_ok(_transcript(client, workspace_id, meeting_id, start_ms=0))) == [
        0,
        1,
        2,
        3,
        4,
    ]


def test_filters_apply_to_an_older_revision(client, email_outbox):
    workspace_id, user_id = _workspace(client, email_outbox)
    meeting_id = _seed(workspace_id, user_id)
    first = _ok(_transcript(client, workspace_id, meeting_id))["revision"]["id"]
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        _add_revision(db, meeting, (("spk_1", 0, 5_000, "نسخهٔ دوم"),))
        db.commit()
    current = _ok(_transcript(client, workspace_id, meeting_id, speaker_key="spk_1"))
    assert current["total_segments"] == 1 and _sequences(current) == [0]
    older = _ok(
        _transcript(client, workspace_id, meeting_id, revision_id=first, speaker_key="spk_1")
    )
    assert older["revision"]["id"] == first
    assert older["total_segments"] == 5 and _sequences(older) == [1, 4]


# --- PII view and audit (AC5) ------------------------------------------------------


def _enforced(client, email_outbox) -> tuple[str, str]:
    workspace_id, user_id = _workspace(client, email_outbox)
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        json={"pii_redaction_enabled": True},
        headers=_write_headers(client, workspace_id),
    )
    assert enabled.status_code == 200, enabled.text
    timeline = (
        ("spk_0", 0, 4_000, PII_TEXTS[0]),
        ("spk_1", 4_000, 9_000, PII_TEXTS[1]),
        ("spk_0", 9_000, 12_000, "بدون شناسه"),
    )
    return workspace_id, _seed(workspace_id, user_id, timeline)


def test_filtered_text_is_the_redacted_text_under_an_enforced_policy(client, email_outbox):
    workspace_id, meeting_id = _enforced(client, email_outbox)
    base = _ok(_transcript(client, workspace_id, meeting_id))
    assert base["view"] == "redacted" and base["privacy_enforced"] is True
    by_id = {segment["id"]: segment["text"] for segment in base["segments"]}
    for params, kept in (
        ({"speaker_key": "spk_1"}, [1]),
        ({"start_ms": 0, "end_ms": 5_000}, [0, 1]),
    ):
        filtered = _ok(_transcript(client, workspace_id, meeting_id, **params))
        assert filtered["view"] == "redacted" and _sequences(filtered) == kept
        for segment in filtered["segments"]:
            assert segment["text"] == by_id[segment["id"]]
            assert _no_raw(segment["text"]), segment["text"]


def test_an_original_read_with_a_filter_is_audited_once_and_a_bad_window_not_at_all(
    client, email_outbox
):
    workspace_id, meeting_id = _enforced(client, email_outbox)
    before = _original_reads(meeting_id)
    original = _ok(
        _transcript(client, workspace_id, meeting_id, view="original", speaker_key="spk_1")
    )
    assert original["view"] == "original" and _sequences(original) == [1]
    assert original["segments"][0]["text"] == PII_TEXTS[1]
    assert _original_reads(meeting_id) == before + 1
    _error(
        _transcript(client, workspace_id, meeting_id, view="original", start_ms=5, end_ms=5),
        422,
        "invalid_time_range",
    )
    assert _original_reads(meeting_id) == before + 1


# --- completeness (AC6, AC7, AC16) -------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        None,
        MeetingStatus.FAILED,
        MeetingStatus.CANCELLED,
        MeetingStatus.ANALYZING,
        MeetingStatus.PARTIAL,
    ],
)
def test_completeness_equals_the_report_in_every_status(
    client, email_outbox, settings, monkeypatch, status
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    if status is not None:
        _set_status(meeting_id, status)
    expected = _without_unsupported(_report(client, workspace_id, meeting_id)["completeness"])
    if status is None:
        assert expected["is_complete"] is True and expected["notices"] == []
    assert _ok(_transcript(client, workspace_id, meeting_id))["completeness"] == expected
    assert _ok(_playback(client, workspace_id, meeting_id))["completeness"] == expected


def test_unsupported_findings_never_reach_transcript_or_playback(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_citing_bundle()
    )
    report = _report(client, workspace_id, meeting_id)["completeness"]
    assert report["notices"] == ["mock_analysis", "unsupported_findings"]
    for body in (
        _ok(_transcript(client, workspace_id, meeting_id)),
        _ok(_playback(client, workspace_id, meeting_id)),
    ):
        assert body["completeness"] == {**report, "notices": ["mock_analysis"]}
        assert body["completeness"]["is_complete"] is False


def test_a_correction_returns_the_navigation_fields_and_marks_analysis_stale(
    client, email_outbox, settings, monkeypatch
):
    # HOM-63: this test is about the stale state, so no recompute runs.
    monkeypatch.setattr(settings, "recompute_on_correction", False)
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    transcript = _ok(_transcript(client, workspace_id, meeting_id))
    corrected = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": transcript["revision"]["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": transcript["segments"][1]["id"], "text": "متن اصلاح‌شده"}],
        },
    )
    assert corrected.status_code == 201, corrected.text
    body = corrected.json()
    assert body["filters"] == {"speaker_keys": [], "start_ms": None, "end_ms": None}
    assert body["total_segments"] == len(body["segments"]) == 2
    report = _report(client, workspace_id, meeting_id)["completeness"]
    assert report["notices"] == ["analysis_stale"]
    assert body["completeness"] == report
    assert _ok(_transcript(client, workspace_id, meeting_id))["completeness"] == report
    assert _ok(_playback(client, workspace_id, meeting_id))["completeness"] == report


def test_a_meeting_still_processing_is_200_with_processing_in_progress(
    client, email_outbox, monkeypatch
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    _patch_upload(monkeypatch)
    upload = _upload(client, workspace_id=workspace_id, key="navigation-queued-0001")
    assert upload.status_code == 202, upload.text
    meeting_id = upload.json()["meeting"]["id"]

    transcript = _ok(_transcript(client, workspace_id, meeting_id))
    assert transcript["revision"] is None and transcript["segments"] == []
    assert transcript["total_segments"] == 0
    completeness = transcript["completeness"]
    assert "processing_in_progress" in completeness["notices"]
    assert "transcript_unavailable" in completeness["notices"]
    assert completeness["is_complete"] is False

    playback = _ok(_playback(client, workspace_id, meeting_id))
    assert playback["completeness"] == completeness
    assert playback["completeness"]["is_complete"] is False


def test_meeting_completeness_is_the_report_rule_without_findings():
    from backend.services.report_service import meeting_completeness

    for status in MeetingStatus:
        for overrides in ({}, {"analysis_stale": True}, {"is_mock": True}, {"analysis": "partial"}):
            quality = _report_quality(**overrides)
            built = meeting_completeness(status=status, quality=quality)
            assert NOTICE_UNSUPPORTED_FINDINGS not in built.notices
            assert built.notices == completeness_notices(
                status=status, quality=quality, unsupported=0
            )
            assert built.is_complete is is_report_complete(
                status=status, quality=quality, unsupported=0
            )
            assert built.meeting_status == status.value
            assert (built.transcription, built.analysis) == (
                quality.transcription,
                quality.analysis,
            )
            assert (built.speaker_attribution, built.evidence) == (
                quality.speaker_attribution,
                quality.evidence,
            )
    quality = _report_quality()
    with_findings = meeting_completeness(
        status=MeetingStatus.COMPLETED, quality=quality, unsupported=2
    )
    assert with_findings.notices == [NOTICE_UNSUPPORTED_FINDINGS]
    assert with_findings.is_complete is False


def test_the_share_view_carries_no_navigation_fields(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    cookies = _cookies(client)
    headers = _write_headers(client, workspace_id)
    _settings(client, headers, cookies, public_share_links_enabled=True)
    created = _share(client, headers, cookies, meeting_id, audience="link")
    assert created.status_code == 201, created.text
    _anonymous(client)
    shared = _ok(client.get(f"/api/v1/shares/{_token(created.json())}"))
    transcript = shared["transcript"]
    assert transcript["segments"]
    assert transcript["filters"] is None
    assert transcript["total_segments"] is None
    assert transcript["completeness"] is None


# --- playback descriptor (AC8, AC9) ------------------------------------------------


def test_playback_descriptor_shape_and_fragment(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == meeting_id))
        content_type, size_bytes = recording.media_type, recording.size_bytes
        assert recording.duration_seconds == 10
    media_url = f"/api/v1/workspaces/{workspace_id}/meetings/{meeting_id}/recording"
    completeness = _ok(_transcript(client, workspace_id, meeting_id))["completeness"]

    body = _ok(_playback(client, workspace_id, meeting_id, start_ms=1_000, end_ms=5_500))
    assert set(body) == PLAYBACK_KEYS
    assert body == {
        "meeting_id": meeting_id,
        "media_url": media_url,
        "url": f"{media_url}#t=1,5.5",
        "start_ms": 1_000,
        "end_ms": 5_500,
        "duration_ms": 10_000,
        "content_type": content_type,
        "size_bytes": size_bytes,
        "accept_ranges": "bytes",
        "completeness": completeness,
        "privacy_enforced": False,
        "audio_redacted": False,
    }

    whole = _ok(_playback(client, workspace_id, meeting_id))
    assert (whole["start_ms"], whole["end_ms"]) == (0, 10_000)
    assert whole["url"] == f"{media_url}#t=0,10"
    clamped = _ok(_playback(client, workspace_id, meeting_id, start_ms=1, end_ms=99_999))
    assert (clamped["start_ms"], clamped["end_ms"]) == (1, 10_000)
    assert clamped["url"] == f"{media_url}#t=0.001,10"

    # the media URL plays with the same session, ranges included
    full = client.get(media_url)
    assert full.status_code == 200 and full.content == STAGED_BYTES
    ranged = client.get(media_url, headers={"Range": "bytes=2-5"})
    assert ranged.status_code == 206 and ranged.content == STAGED_BYTES[2:6]


def test_playback_window_rules(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    for params in (
        {"start_ms": 10_000},  # start at the end of a 10 s recording
        {"start_ms": 20_000, "end_ms": 30_000},
        {"start_ms": 2_000, "end_ms": 2_000},
        {"start_ms": 3_000, "end_ms": 2_000},
    ):
        _error(_playback(client, workspace_id, meeting_id, **params), 422, "invalid_time_range")
    last = _ok(_playback(client, workspace_id, meeting_id, start_ms=9_999))
    assert (last["start_ms"], last["end_ms"]) == (9_999, 10_000)
    assert last["url"].endswith("#t=9.999,10")
    smallest = _ok(_playback(client, workspace_id, meeting_id, start_ms=2_000, end_ms=2_001))
    assert smallest["url"].endswith("#t=2,2.001")
    for params in ({"start_ms": -1}, {"end_ms": 0}):
        _error(_playback(client, workspace_id, meeting_id, **params), 422, "validation_error")


def test_fragment_and_window_helpers():
    from backend.routers.v1.meetings import (
        _media_fragment,
        _playback_duration_ms,
        _playback_window,
    )

    assert _media_fragment(10_000, 30_500) == "#t=10,30.5"
    assert _media_fragment(1, None) == "#t=0.001"
    assert _media_fragment(0, None) == "#t=0"
    assert _media_fragment(0, 61_000) == "#t=0,61"
    assert _media_fragment(123_456, 200_010) == "#t=123.456,200.01"
    assert _media_fragment(100_000, 100_100) == "#t=100,100.1"

    assert _playback_window(start_ms=0, end_ms=None, duration_ms=None) == (0, None)
    assert _playback_window(start_ms=5, end_ms=99_999, duration_ms=None) == (5, 99_999)
    assert _playback_window(start_ms=5_000, end_ms=None, duration_ms=10_000) == (5_000, 10_000)
    assert _playback_window(start_ms=5_000, end_ms=20_000, duration_ms=10_000) == (5_000, 10_000)
    for start_ms, end_ms, duration_ms in ((10_000, None, 10_000), (5, 5, None), (6, 5, 10_000)):
        with pytest.raises(HTTPException) as denied:
            _playback_window(start_ms=start_ms, end_ms=end_ms, duration_ms=duration_ms)
        assert denied.value.status_code == 422
        assert denied.value.detail["code"] == "invalid_time_range"

    def duration(recording_seconds, meeting_seconds):
        return _playback_duration_ms(
            SimpleNamespace(duration_seconds=recording_seconds),
            SimpleNamespace(duration_seconds=meeting_seconds),
        )

    assert duration(61, 70) == 61_000
    assert duration(0, 70) == 70_000
    assert duration(0, 0) is None
    assert duration(None, None) is None


def test_playback_never_shows_a_storage_location(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    with inspection_session_factory()() as db:
        storage_key = db.scalar(
            select(Recording.storage_key).where(Recording.meeting_id == meeting_id)
        )
    assert storage_key
    response = _playback(client, workspace_id, meeting_id, start_ms=1_000)
    assert response.status_code == 200, response.text
    for secret in (storage_key, "tenants/", settings.storage_bucket, settings.storage_local_root):
        assert secret not in response.text


# --- authorization (AC10, AC14, AC15) ----------------------------------------------


def test_playback_follows_roles_and_record_scope(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"lone": "member", "viewer": "viewer"}
    )
    lone, viewer = people["lone"], people["viewer"]
    _patch_upload(monkeypatch)
    meeting_id = _persona_upload(client, owner, "navigation-scope-0001")
    playback = f"/api/v1/meetings/{meeting_id}/playback"
    transcript = f"/api/v1/meetings/{meeting_id}/transcript"

    assert viewer.get(transcript).status_code == 200
    assert viewer.get(playback).status_code == 200

    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200, switched.text
    _error(lone.get(playback), 404, "meeting_not_found")
    _error(lone.get(transcript), 404, "meeting_not_found")
    assert owner.get(playback).status_code == 200  # allow-control

    credentials = register_user(client, email_outbox, email="navigation-outsider@example.com")
    login_user(client, credentials)
    outsider = {"X-Workspace-ID": owner.workspace_id}
    denied_transcript = client.get(transcript, headers=outsider)
    denied_playback = client.get(playback, headers=outsider)
    assert 400 <= denied_playback.status_code < 500
    assert denied_playback.status_code == denied_transcript.status_code
    assert denied_playback.json()["error"]["code"] == denied_transcript.json()["error"]["code"]


def test_playback_under_an_enforced_policy_never_audits_an_original_read(
    client, email_outbox, monkeypatch
):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = people["member"]
    enabled = owner.patch("/api/v1/admin/workspace-settings", json={"pii_redaction_enabled": True})
    assert enabled.status_code == 200, enabled.text
    _patch_upload(monkeypatch)
    meeting_id = _persona_upload(client, owner, "navigation-privacy-0001")
    for persona in (member, owner):
        body = _ok(persona.get(f"/api/v1/meetings/{meeting_id}/playback"))
        assert body["privacy_enforced"] is True
        assert body["audio_redacted"] is False
    assert _original_reads(meeting_id) == 0
    # control: the member is really refused the original transcript
    _error(
        member.get(f"/api/v1/meetings/{meeting_id}/transcript", params={"view": "original"}),
        403,
        "original_access_denied",
    )


# --- one playability rule (AC11, AC12, AC13; SPEC s11 and s12) ---------------------


class _Playable:
    """A processed meeting, its owner, and a recipient share with audio.

    A recipients share binds no tenant, so on PostgreSQL the share route runs
    under the share ceiling of row-level security."""

    def __init__(self, client, email_outbox, settings, monkeypatch) -> None:
        self.client = client
        _, self.workspace_id, self.meeting_id = _processed(
            client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
        )
        self.owner_cookies = _cookies(client)
        self.owner_headers = _write_headers(client, self.workspace_id)
        created = _share(
            client,
            self.owner_headers,
            self.owner_cookies,
            self.meeting_id,
            audience="recipients",
            recipients=["navigation-guest@example.com"],
            allow_download=True,
        )
        assert created.status_code == 201, created.text
        self.token = _token(created.json())
        _, self.guest_cookies, _ = _signup(
            client, email_outbox, email="navigation-guest@example.com"
        )
        assert (
            client.get(f"/api/v1/shares/{self.token}", cookies=self.guest_cookies).status_code
            == 200
        )

    def owner_get(self, path: str, **kwargs):
        return self.client.get(
            path,
            cookies=self.owner_cookies,
            headers={"X-Workspace-ID": self.workspace_id, **kwargs.pop("headers", {})},
            **kwargs,
        )

    def workspace_routes(self) -> dict[str, object]:
        ws, m = self.workspace_id, self.meeting_id
        return {
            "playback": self.owner_get(f"/api/v1/meetings/{m}/playback"),
            "inline": self.owner_get(f"/api/v1/workspaces/{ws}/meetings/{m}/recording"),
            "inline_range": self.owner_get(
                f"/api/v1/workspaces/{ws}/meetings/{m}/recording", headers={"Range": "bytes=0-3"}
            ),
            "download": self.owner_get(f"/api/v1/meetings/{m}/recording"),
        }

    def share_route(self):
        return self.client.get(f"/api/v1/shares/{self.token}/recording", cookies=self.guest_cookies)

    def assert_workspace_routes_succeed(self) -> None:
        responses = self.workspace_routes()
        assert responses["playback"].status_code == 200, responses["playback"].text
        assert responses["inline"].status_code == 200, responses["inline"].text
        assert responses["inline"].content == STAGED_BYTES
        assert responses["inline_range"].status_code == 206, responses["inline_range"].text
        assert responses["download"].status_code == 200, responses["download"].text
        assert responses["download"].content == STAGED_BYTES

    def assert_workspace_routes_denied(self, status: int, code: str) -> None:
        for name, response in self.workspace_routes().items():
            assert response.status_code == status, (name, response.text)
            assert response.json()["error"]["code"] == code, (name, response.text)

    def assert_share_succeeds(self) -> None:
        response = self.share_route()
        assert response.status_code == 200, response.text
        assert response.content == STAGED_BYTES

    def assert_share_expired(self) -> None:
        _error(self.share_route(), 410, "recording_retention_expired")

    # --- state changes (DBA session) ---

    def set_expiry(self, delta: timedelta) -> None:
        with inspection_session_factory()() as db:
            meeting = db.get(Meeting, self.meeting_id)
            meeting.retention_expires_at = datetime.now(UTC) + delta
            db.commit()

    def set_mode(self, mode: RetentionMode) -> None:
        with inspection_session_factory()() as db:
            workspace = db.get(Workspace, self.workspace_id)
            workspace.retention_mode = mode
            workspace.retention_days = 90 if mode is RetentionMode.FINITE else 0
            db.commit()

    def set_job_state(self, state: JobState) -> None:
        with inspection_session_factory()() as db:
            job = db.scalar(
                select(ProcessingJob).where(ProcessingJob.meeting_id == self.meeting_id)
            )
            job.state = state
            # a running attempt always holds a lease (ck_job_running_has_lease)
            running = state is JobState.RUNNING
            job.locked_by = "navigation-worker" if running else None
            job.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5) if running else None
            db.commit()

    def set_media_state(self, state: MediaLifecycleState) -> None:
        with inspection_session_factory()() as db:
            media = db.scalar(
                select(MediaObject).where(
                    MediaObject.meeting_id == self.meeting_id,
                    MediaObject.kind == MediaKind.ORIGINAL,
                )
            )
            media.lifecycle_state = state
            if state is MediaLifecycleState.DELETED:
                media.deleted_at = datetime.now(UTC)
            db.commit()

    def hold(self) -> None:
        held = self.client.post(
            f"/api/v1/meetings/{self.meeting_id}/legal-hold",
            json={
                "reason": "litigation request",
                "review_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            },
            headers=self.owner_headers,
            cookies=self.owner_cookies,
        )
        assert held.status_code == 200, held.text


@pytest.fixture
def playable(client, email_outbox, settings, monkeypatch) -> _Playable:
    return _Playable(client, email_outbox, settings, monkeypatch)


def test_live_media_with_a_future_expiry_plays_everywhere(playable):
    playable.set_expiry(timedelta(days=1))
    playable.assert_workspace_routes_succeed()
    playable.assert_share_succeeds()


def test_an_expired_recording_is_410_on_every_recording_route(playable):
    playable.set_expiry(timedelta(minutes=-1))
    playable.assert_workspace_routes_denied(410, "recording_retention_expired")
    playable.assert_share_expired()
    # allow-control: the same meeting with its expiry in the future
    playable.set_expiry(timedelta(days=1))
    playable.assert_workspace_routes_succeed()
    playable.assert_share_succeeds()


def test_an_indefinite_workspace_never_expires_a_recording(playable):
    playable.set_expiry(timedelta(minutes=-1))
    playable.set_mode(RetentionMode.INDEFINITE)
    playable.assert_workspace_routes_succeed()
    playable.assert_share_succeeds()
    playable.set_mode(RetentionMode.LEGACY_UNRESOLVED)
    playable.assert_workspace_routes_succeed()
    playable.assert_share_succeeds()


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING])
def test_an_active_job_keeps_the_recording_playable_but_not_through_a_share(playable, state):
    playable.set_expiry(timedelta(minutes=-1))
    playable.set_job_state(state)
    playable.assert_workspace_routes_succeed()
    # the share session cannot see jobs: an expired recording never leaves through a share
    playable.assert_share_expired()
    # control: once the job is terminal the expiry applies on every route
    playable.set_job_state(JobState.COMPLETED)
    playable.assert_workspace_routes_denied(410, "recording_retention_expired")


def test_a_legal_hold_keeps_the_recording_playable_but_not_through_a_share(playable):
    playable.hold()
    playable.set_expiry(timedelta(minutes=-1))
    playable.assert_workspace_routes_succeed()
    playable.assert_share_expired()


@pytest.mark.parametrize(
    "state", [MediaLifecycleState.DELETION_PENDING, MediaLifecycleState.DELETED]
)
def test_media_that_is_being_deleted_is_not_found(playable, state):
    playable.assert_workspace_routes_succeed()  # allow-control: ACTIVE
    playable.set_media_state(state)
    playable.assert_workspace_routes_denied(404, "recording_not_found")


def test_a_recording_without_a_media_object_follows_the_meeting_expiry(playable):
    with inspection_session_factory()() as db:
        recording = db.scalar(select(Recording).where(Recording.meeting_id == playable.meeting_id))
        recording.media_object_id = None
        db.commit()
    playable.set_expiry(timedelta(days=1))
    playable.assert_workspace_routes_succeed()
    playable.set_expiry(timedelta(minutes=-1))
    playable.assert_workspace_routes_denied(410, "recording_retention_expired")


def test_expiry_due_compares_naive_and_aware_times_as_utc():
    from backend.services.retention_policy import is_expiry_due

    now = datetime.now(UTC)
    finite = SimpleNamespace(retention_mode=RetentionMode.FINITE)
    immediate = SimpleNamespace(retention_mode=RetentionMode.IMMEDIATE)
    indefinite = SimpleNamespace(retention_mode=RetentionMode.INDEFINITE)
    naive_past = (now - timedelta(minutes=1)).replace(tzinfo=None)
    naive_future = (now + timedelta(minutes=1)).replace(tzinfo=None)

    def meeting(expires):
        return SimpleNamespace(retention_expires_at=expires)

    assert is_expiry_due(finite, meeting(naive_past), now=now) is True
    assert is_expiry_due(immediate, meeting(now), now=now) is True  # at the boundary
    assert is_expiry_due(finite, meeting(naive_future), now=now) is False
    assert is_expiry_due(finite, meeting(None), now=now) is False
    assert is_expiry_due(indefinite, meeting(naive_past), now=now) is False
