"""HOM-49 (WP10-T06): the speaker timeline of one transcript revision.

The timeline answers, for one revision of one meeting, who speaks when, where
turns overlap, which speakers are unknown, anonymous or identified, which
turns need a person to look at them, and everything the console needs to send
a speaker correction through the existing correction route.  Overlap is
measured from turn time ranges (``overlap_basis == "turns"``): raw
diarization segments are not stored yet (HOM-45 adds them as a new basis).
The response carries no transcript text: the console joins turns to text by
``segment_id`` through ``GET /transcript``, so the timeline needs no redaction
view and writes no original-access audit row.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from backend.database.saas_models import (
    AuditEvent,
    Meeting,
    MeetingSpeaker,
    MeetingStatus,
    Membership,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    User,
    Workspace,
)
from backend.services.speaker_timeline import (
    UNKNOWN_SPEAKER_KEYS,
    TimelineSegmentData,
    TimelineSpeakerData,
    build_timeline,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_uploads_api import _authenticated_workspace

# A turn of the stored transcript, as the builder sees it (plain values).
TURN_TEXT = "متن این نوبت گفتار"
# _workspace_with_roles registers its owner with register_user's defaults.
OWNER_LOGIN = {"email": "matrix-owner@example.com", "password": "SecurePass123"}


def _read_headers(workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id}


def _timeline(client, workspace_id: str, meeting_id: str, **params) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript/timeline",
        headers=_read_headers(workspace_id),
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _tenant(client, email_outbox) -> tuple[dict, str, str]:
    """A logged-in owner: credentials, workspace id, user id."""
    credentials, workspace_id = _authenticated_workspace(client, email_outbox)
    with inspection_session_factory()() as db:
        user_id = db.scalar(select(User.id).where(User.email == credentials["email"]))
        assert user_id is not None
    return credentials, workspace_id, user_id


def _workspace_of_user(email: str) -> str:
    with inspection_session_factory()() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        assert user_id is not None
        workspace_id = db.scalar(
            select(Workspace.id)
            .join(Membership, Membership.workspace_id == Workspace.id)
            .where(Membership.user_id == user_id)
        )
        assert workspace_id is not None
        return workspace_id


def _response_keys(body) -> set[str]:
    """Every key name of the JSON body, at any depth."""
    keys: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                keys.add(key)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(body)
    return keys


def _insert_timeline_meeting(
    workspace_id: str,
    user_id: str,
    *,
    title: str = "جلسه فروش",
    duration_seconds: int = 0,
    turns: tuple[tuple[int, str, int, int, float | None], ...] = (),
    speakers: tuple[tuple[str, str | None, str, float | None], ...] = (),
    diarization_status: str | None = "available",
    diarization_provider: str | None = "pyannote",
    deleted: bool = False,
) -> tuple[str, str]:
    """A meeting with one current ``asr`` revision, its turns and speakers."""
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            title=title,
            status=MeetingStatus.COMPLETED,
            duration_seconds=duration_seconds,
        )
        db.add(meeting)
        db.flush()
        revision = TranscriptRevision(
            workspace_id=workspace_id,
            meeting_id=meeting.id,
            revision_number=1,
            source=TranscriptSource.ASR,
            reason="processing",
            language="fa",
            segment_count=len(turns),
            diarization_status=diarization_status,
            diarization_provider=diarization_provider,
            is_current=True,
        )
        db.add(revision)
        db.flush()
        for sequence, key, start_ms, end_ms, confidence in turns:
            db.add(
                TranscriptSegment(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    revision_id=revision.id,
                    sequence=sequence,
                    speaker_key=key,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=TURN_TEXT,
                    confidence=confidence,
                )
            )
        for key, display_name, identity_type, confidence in speakers:
            db.add(
                MeetingSpeaker(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    speaker_key=key,
                    display_name=display_name,
                    identity_type=identity_type,
                    confidence=confidence,
                )
            )
        if deleted:
            meeting.deleted_at = datetime.now(UTC)
        db.commit()
        return meeting.id, revision.id


def _counts() -> tuple[int, int, int, int]:
    """Row counts of every table the route must never write to."""
    with inspection_session_factory()() as db:
        return tuple(
            int(db.scalar(select(func.count()).select_from(table)))
            for table in (AuditEvent, TranscriptRevision, TranscriptSegment, MeetingSpeaker)
        )


# --- the builder (pure) -------------------------------------------------------


def _build(**kwargs) -> dict:
    """``build_timeline`` in the JSON shape the route returns."""
    return build_timeline(**kwargs).model_dump()


def test_builder_overlap_sweep_kinds_and_totals():
    parts = _build(
        segments=[
            TimelineSegmentData("s0", 0, "SPEAKER_00", 0, 5000, 0.9),
            TimelineSegmentData("s1", 1, "SPEAKER_01", 3000, 8000, 0.8),
            TimelineSegmentData("s2", 2, "speaker_unknown", 4000, 6000, 0.75),
            # touches s1 at 8000: no overlap with it
            TimelineSegmentData("s3", 3, "SPEAKER_00", 8000, 8500, None),
            # zero length: takes part in no overlap, adds no speech
            TimelineSegmentData("s4", 4, "SPEAKER_01", 8600, 8600, None),
            # one known speaker overlapping itself: same_speaker, not cross
            TimelineSegmentData("s5", 5, "SPEAKER_00", 10000, 12000, 0.5),
            TimelineSegmentData("s6", 6, "SPEAKER_00", 11000, 13000, 0.5),
        ],
        speakers=[TimelineSpeakerData("SPEAKER_00", "مدیر", "person", 0.95)],
        baseline_speaker_by_sequence={},
        baseline_source="asr",
        baseline_diarization_status="available",
        recording_duration_ms=None,
    )
    overlaps = parts["overlaps"]
    assert [
        (o["index"], o["start_ms"], o["end_ms"], o["kind"], o["speaker_keys"]) for o in overlaps
    ] == [
        (0, 3000, 4000, "cross_speaker", ["SPEAKER_00", "SPEAKER_01"]),
        (1, 4000, 5000, "cross_speaker", ["SPEAKER_00", "SPEAKER_01", "speaker_unknown"]),
        (2, 5000, 6000, "cross_speaker", ["SPEAKER_01", "speaker_unknown"]),
        (3, 11000, 12000, "same_speaker", ["SPEAKER_00"]),
    ]
    assert overlaps[1]["includes_unknown"] is True
    assert overlaps[1]["segment_ids"] == ["s0", "s1", "s2"]  # by sequence
    assert overlaps[3]["includes_unknown"] is False
    # the union of cross_speaker intervals only; same_speaker adds nothing
    assert parts["overlap_ms"] == 1000 + 1000 + 1000
    assert parts["unknown_speech_ms"] == 2000
    lanes = {lane["speaker_key"]: lane for lane in parts["lanes"]}
    assert lanes["SPEAKER_00"]["overlap_ms"] == 1000 + 1000  # intervals 0 and 1
    assert lanes["SPEAKER_01"]["overlap_ms"] == 1000 + 1000 + 1000
    assert lanes["speaker_unknown"]["overlap_ms"] == 1000 + 1000
    assert lanes["SPEAKER_00"]["speech_ms"] == 5000 + 500 + 2000 + 2000
    assert lanes["SPEAKER_01"]["speech_ms"] == 5000  # the zero-length turn adds 0
    assert lanes["SPEAKER_00"]["turn_count"] == 4
    # the self-overlap gives overlap_ids but no "overlap" review reason
    turns = {turn["segment_id"]: turn for turn in parts["turns"]}
    assert turns["s5"]["overlap_ids"] == [3]
    assert turns["s5"]["review_reasons"] == []
    assert turns["s1"]["overlap_ids"] == [0, 1, 2]
    assert turns["s1"]["review_reasons"] == ["overlap"]
    assert turns["s2"]["review_reasons"] == ["unknown_speaker", "overlap"]
    assert turns["s3"]["review_reasons"] == [] and turns["s3"]["overlap_ids"] == []
    assert turns["s4"]["review_reasons"] == [] and turns["s4"]["overlap_ids"] == []


def test_builder_lane_kinds_order_and_names():
    parts = _build(
        segments=[
            TimelineSegmentData("s0", 0, "SPEAKER_01", 0, 1000, None),
            TimelineSegmentData("s1", 1, "speaker_unknown", 1000, 2000, None),
            TimelineSegmentData("s2", 2, "unknown", 2000, 3000, None),
            TimelineSegmentData("s3", 3, "SPEAKER_00", 3000, 4000, None),
        ],
        speakers=[
            # named: identified
            TimelineSpeakerData("SPEAKER_00", "مدیر فروش", "unknown", None),
            # a row, but nobody named it: anonymous
            TimelineSpeakerData("SPEAKER_01", None, "unknown", 0.6),
            # identified through identity_type without a name
            TimelineSpeakerData("SPEAKER_02", None, "person", 0.7),
            # a speaker row with no turn in this revision
            TimelineSpeakerData("SPEAKER_03", "مهمان", "person", None),
            # an unknown row without a turn
            TimelineSpeakerData("unknown", None, "unknown", None),
        ],
        baseline_speaker_by_sequence={},
        baseline_source="asr",
        baseline_diarization_status="available",
        recording_duration_ms=None,
    )
    assert [(lane["speaker_key"], lane["kind"]) for lane in parts["lanes"]] == [
        ("SPEAKER_00", "identified"),
        ("SPEAKER_01", "anonymous"),
        ("SPEAKER_02", "identified"),
        ("SPEAKER_03", "identified"),
        ("speaker_unknown", "unknown"),
        ("unknown", "unknown"),
    ]
    by_key = {lane["speaker_key"]: lane for lane in parts["lanes"]}
    # never fabricate a name: no row, or a row without one, stays None
    assert by_key["SPEAKER_00"]["display_name"] == "مدیر فروش"
    assert by_key["speaker_unknown"]["display_name"] is None
    assert by_key["speaker_unknown"]["identity_type"] is None
    assert by_key["speaker_unknown"]["speaker_confidence"] is None
    assert by_key["SPEAKER_01"]["identity_type"] == "unknown"
    assert by_key["SPEAKER_01"]["speaker_confidence"] == 0.6
    # a lane with no turn in the revision still appears, with zero totals
    assert by_key["SPEAKER_03"]["turn_count"] == 0
    assert by_key["SPEAKER_03"]["speech_ms"] == 0
    assert by_key["SPEAKER_03"]["overlap_ms"] == 0
    assert parts["unknown_speech_ms"] == 2000
    assert UNKNOWN_SPEAKER_KEYS == {"speaker_unknown", "unknown"}


def test_builder_attribution_rules():
    segments = [
        TimelineSegmentData("s0", 0, "speaker_unknown", 0, 1000, None),
        TimelineSegmentData("s1", 1, "SPEAKER_01", 1000, 2000, None),
        TimelineSegmentData("s2", 2, "SPEAKER_02", 2000, 3000, None),
        TimelineSegmentData("s3", 3, "SPEAKER_03", 3000, 4000, None),
        TimelineSegmentData("s4", 4, "SPEAKER_04", 4000, 5000, None),
        TimelineSegmentData("s5", 5, "SPEAKER_05", 5000, 6000, None),
    ]
    baseline = {0: "SPEAKER_00", 1: "SPEAKER_99", 2: "SPEAKER_02", 3: "SPEAKER_03", 4: "SPEAKER_04"}
    # sequence 5 is missing from the baseline, sequence 4 is unchanged in it
    parts = _build(
        segments=segments,
        speakers=[],
        baseline_speaker_by_sequence=baseline,
        baseline_source="asr",
        baseline_diarization_status="available",
        recording_duration_ms=None,
    )
    turns = {turn["segment_id"]: turn for turn in parts["turns"]}
    assert turns["s0"]["attribution"] == "none"  # unknown speaker first
    assert turns["s1"]["attribution"] == "person"  # a person changed the key
    assert turns["s2"]["attribution"] == "diarization"  # baseline had it available
    assert turns["s3"]["attribution"] == "diarization"
    assert turns["s4"]["attribution"] == "diarization"
    assert turns["s5"]["attribution"] == "person"  # no turn with that sequence
    # without diarization the same unchanged turns have no attribution
    parts = _build(
        segments=segments,
        speakers=[],
        baseline_speaker_by_sequence={i: s.speaker_key for i, s in enumerate(segments)},
        baseline_source="asr",
        baseline_diarization_status=None,
        recording_duration_ms=None,
    )
    turns = {turn["segment_id"]: turn for turn in parts["turns"]}
    assert turns["s2"]["attribution"] == "none"
    assert turns["s3"]["attribution"] == "none"
    # a backfill baseline names its turns
    parts = _build(
        segments=segments,
        speakers=[],
        baseline_speaker_by_sequence={i: s.speaker_key for i, s in enumerate(segments)},
        baseline_source="backfill",
        baseline_diarization_status=None,
        recording_duration_ms=None,
    )
    assert {t["segment_id"]: t["attribution"] for t in parts["turns"]}["s3"] == "backfill"
    # the unknown speaker is "none" even under a backfill baseline
    assert {t["segment_id"]: t["attribution"] for t in parts["turns"]}["s0"] == "none"


def test_builder_review_reasons_and_recording_bounds():
    parts = _build(
        segments=[TimelineSegmentData("s0", 0, "SPEAKER_00", 0, 31000, None)],
        speakers=[],
        baseline_speaker_by_sequence={0: "SPEAKER_00"},
        baseline_source="asr",
        baseline_diarization_status="available",
        recording_duration_ms=30000,
    )
    assert parts["turns"][0]["review_reasons"] == ["outside_recording"]
    # a turn ending exactly at the recording end is inside it
    parts = _build(
        segments=[TimelineSegmentData("s1", 1, "SPEAKER_01", 25000, 30000, None)],
        speakers=[],
        baseline_speaker_by_sequence={1: "SPEAKER_01"},
        baseline_source="asr",
        baseline_diarization_status="available",
        recording_duration_ms=30000,
    )
    assert parts["turns"][0]["review_reasons"] == []
    parts = _build(
        segments=[
            TimelineSegmentData("s0", 0, "speaker_unknown", 0, 31000, None),
        ],
        speakers=[],
        baseline_speaker_by_sequence={},
        baseline_source="asr",
        baseline_diarization_status=None,
        recording_duration_ms=None,  # no known duration: never outside
    )
    assert parts["turns"][0]["review_reasons"] == ["unknown_speaker"]


def test_builder_of_a_revision_without_turns_keeps_speaker_lanes():
    # an existing revision with no segments; the no-revision case (empty
    # lanes even with speaker rows) is the route's, covered further below
    parts = _build(
        segments=[],
        speakers=[TimelineSpeakerData("SPEAKER_00", "مدیر", "person", None)],
        baseline_speaker_by_sequence={},
        baseline_source="asr",
        baseline_diarization_status=None,
        recording_duration_ms=12000,
    )
    assert parts["lanes"] == [
        {
            "speaker_key": "SPEAKER_00",
            "display_name": "مدیر",
            "kind": "identified",
            "identity_type": "person",
            "speaker_confidence": None,
            "turn_count": 0,
            "speech_ms": 0,
            "overlap_ms": 0,
        }
    ]
    assert parts["turns"] == [] and parts["overlaps"] == []
    assert parts["overlap_ms"] == 0 and parts["unknown_speech_ms"] == 0


# --- the route ----------------------------------------------------------------


def test_timeline_of_diarized_meeting(client, email_outbox):
    _, workspace_id, user_id = _tenant(client, email_outbox)
    meeting_id, revision_id = _insert_timeline_meeting(
        workspace_id,
        user_id,
        duration_seconds=30,
        turns=(
            (0, "SPEAKER_00", 0, 5000, 0.9),
            (1, "SPEAKER_01", 3000, 8000, 0.8),
            (2, "speaker_unknown", 4000, 6000, 0.75),
            (3, "SPEAKER_00", 8000, 8500, None),
            (4, "SPEAKER_01", 8600, 8600, None),
        ),
        speakers=(
            ("SPEAKER_00", "مدیر فروش", "person", 0.95),
            ("SPEAKER_01", None, "unknown", None),
        ),
    )
    body = _timeline(client, workspace_id, meeting_id)
    assert body["meeting_id"] == meeting_id
    assert body["revision"] == {
        "id": revision_id,
        "revision_number": 1,
        "source": "asr",
        "is_current": True,
        "diarization_status": "available",
        "diarization_provider": "pyannote",
        "is_mock": False,
    }
    assert body["overlap_basis"] == "turns"
    assert body["recording_duration_ms"] == 30000
    assert body["timeline_end_ms"] == 30000  # max(recording, turn ends) = 30000
    assert body["confidence"] == {"origin": "asr", "calibrated": False}
    assert [(lane["speaker_key"], lane["kind"]) for lane in body["lanes"]] == [
        ("SPEAKER_00", "identified"),
        ("SPEAKER_01", "anonymous"),
        ("speaker_unknown", "unknown"),
    ]
    turns = body["turns"]
    assert [t["sequence"] for t in turns] == [0, 1, 2, 3, 4]
    assert [t["speaker_key"] for t in turns] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "speaker_unknown",
        "SPEAKER_00",
        "SPEAKER_01",
    ]
    assert [t["attribution"] for t in turns] == [
        "diarization",
        "diarization",
        "none",
        "diarization",
        "diarization",
    ]
    # the exact turn list, one field at a time, above and below
    assert [t["overlap_ids"] for t in turns] == [[0, 1], [0, 1, 2], [1, 2], [], []]
    assert [t["review_reasons"] for t in turns] == [
        ["overlap"],
        ["overlap"],
        ["unknown_speaker", "overlap"],
        [],
        [],
    ]
    assert body["overlaps"] == [
        {
            "index": 0,
            "start_ms": 3000,
            "end_ms": 4000,
            "kind": "cross_speaker",
            "speaker_keys": ["SPEAKER_00", "SPEAKER_01"],
            "segment_ids": [turns[0]["segment_id"], turns[1]["segment_id"]],
            "includes_unknown": False,
        },
        {
            "index": 1,
            "start_ms": 4000,
            "end_ms": 5000,
            "kind": "cross_speaker",
            "speaker_keys": ["SPEAKER_00", "SPEAKER_01", "speaker_unknown"],
            "segment_ids": [
                turns[0]["segment_id"],
                turns[1]["segment_id"],
                turns[2]["segment_id"],
            ],
            "includes_unknown": True,
        },
        {
            "index": 2,
            "start_ms": 5000,
            "end_ms": 6000,
            "kind": "cross_speaker",
            "speaker_keys": ["SPEAKER_01", "speaker_unknown"],
            "segment_ids": [turns[1]["segment_id"], turns[2]["segment_id"]],
            "includes_unknown": True,
        },
    ]
    assert body["overlap_ms"] == 3000  # three cross intervals of 1000 each
    assert body["unknown_speech_ms"] == 2000
    lanes = {lane["speaker_key"]: lane for lane in body["lanes"]}
    assert lanes["SPEAKER_00"]["speech_ms"] == 5500
    assert lanes["SPEAKER_00"]["overlap_ms"] == 2000
    assert lanes["SPEAKER_01"]["speech_ms"] == 5000
    assert lanes["SPEAKER_01"]["overlap_ms"] == 3000
    assert lanes["speaker_unknown"]["speech_ms"] == 2000
    assert lanes["speaker_unknown"]["overlap_ms"] == 2000
    assert body["correction"] == {
        "base_revision_id": revision_id,
        "correctable": True,
        "speaker_choices": ["SPEAKER_00", "SPEAKER_01", "speaker_unknown"],
    }
    # no transcript text anywhere in the body
    assert "text" not in _response_keys(body)
    raw = str(body)
    assert TURN_TEXT not in raw


def test_timeline_without_recording_duration_and_outside_turns(client, email_outbox):
    _, workspace_id, user_id = _tenant(client, email_outbox)
    meeting_id, _ = _insert_timeline_meeting(
        workspace_id,
        user_id,
        duration_seconds=0,  # unknown duration: recording_duration_ms is null
        turns=((0, "SPEAKER_00", 0, 31000, None),),
        speakers=(("SPEAKER_00", "مدیر", "person", None),),
    )
    body = _timeline(client, workspace_id, meeting_id)
    assert body["recording_duration_ms"] is None
    assert body["timeline_end_ms"] == 31000
    assert body["turns"][0]["review_reasons"] == []  # no duration: never outside
    meeting_with_duration, _ = _insert_timeline_meeting(
        workspace_id,
        user_id,
        title="با مدت",
        duration_seconds=30,
        turns=((0, "SPEAKER_00", 29000, 31000, None),),
        speakers=(("SPEAKER_00", "مدیر", "person", None),),
    )
    body = _timeline(client, workspace_id, meeting_with_duration)
    assert body["recording_duration_ms"] == 30000
    assert body["turns"][0]["review_reasons"] == ["outside_recording"]
    assert body["timeline_end_ms"] == 31000


def test_timeline_reflects_corrections_and_correction_readiness(client, email_outbox):
    _, workspace_id, user_id = _tenant(client, email_outbox)
    meeting_id, revision_id = _insert_timeline_meeting(
        workspace_id,
        user_id,
        turns=(
            (0, "SPEAKER_00", 0, 4000, 0.9),
            (1, "SPEAKER_01", 5000, 9000, 0.8),
            (2, "SPEAKER_00", 9500, 12000, 0.7),
        ),
        speakers=(
            ("SPEAKER_00", "مدیر", "person", None),
            ("SPEAKER_01", "کارشناس", "person", None),
        ),
    )
    first = _timeline(client, workspace_id, meeting_id)
    assert first["correction"]["correctable"] is True
    assert first["correction"]["base_revision_id"] == revision_id
    assert first["correction"]["speaker_choices"] == [
        "SPEAKER_00",
        "SPEAKER_01",
        "speaker_unknown",
    ]
    segment_id = first["turns"][1]["segment_id"]

    # a speaker choice offered by the timeline is accepted by the correction route
    corrected = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": revision_id,
            "reason": "اصلاح گوینده",
            "edits": [{"segment_id": segment_id, "speaker_key": "SPEAKER_00"}],
        },
    )
    assert corrected.status_code == 201, corrected.text
    second_revision_id = corrected.json()["revision"]["id"]

    second = _timeline(client, workspace_id, meeting_id)
    assert second["revision"]["id"] == second_revision_id
    assert second["revision"]["source"] == "correction"
    # a correction stores no diarization of its own: the baseline's values
    assert second["revision"]["diarization_status"] == "available"
    assert second["revision"]["diarization_provider"] == "pyannote"
    assert [t["attribution"] for t in second["turns"]] == [
        "diarization",
        "person",  # the corrected turn only
        "diarization",
    ]
    assert second["turns"][1]["speaker_key"] == "SPEAKER_00"
    assert second["correction"] == {
        "base_revision_id": second_revision_id,
        "correctable": True,
        "speaker_choices": ["SPEAKER_00", "SPEAKER_01", "speaker_unknown"],
    }
    # a lane with no turn left in the revision stays, with zero totals
    lanes = {lane["speaker_key"]: lane for lane in second["lanes"]}
    assert lanes["SPEAKER_01"]["turn_count"] == 0
    assert lanes["SPEAKER_01"]["speech_ms"] == 0

    # a correction of the correction walks the baseline up to revision 1
    corrected_again = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": second_revision_id,
            "reason": "اصلاح دیگر",
            "edits": [
                {
                    "segment_id": second["turns"][2]["segment_id"],
                    "speaker_key": "SPEAKER_01",
                }
            ],
        },
    )
    assert corrected_again.status_code == 201, corrected_again.text
    third = _timeline(client, workspace_id, meeting_id)
    assert [t["attribution"] for t in third["turns"]] == [
        "diarization",
        "person",
        "person",
    ]

    # the old revision is still readable by id and is not correctable anymore
    old = _timeline(client, workspace_id, meeting_id, revision_id=revision_id)
    assert old["revision"]["id"] == revision_id
    assert old["revision"]["is_current"] is False
    assert old["turns"][1]["speaker_key"] == "SPEAKER_01"
    assert [t["attribution"] for t in old["turns"]] == [
        "diarization",
        "diarization",
        "diarization",
    ]
    assert old["correction"] == {
        "base_revision_id": None,
        "correctable": False,
        "speaker_choices": ["SPEAKER_00", "SPEAKER_01", "speaker_unknown"],
    }


def test_baseline_walks_a_chain_of_two_corrections(client, email_outbox):
    # ASR -> correction -> correction: the baseline of the last correction is
    # the ASR revision, so a turn corrected in the *first* correction still
    # shows "person" on the second correction's timeline, and a turn the
    # second correction did not touch keeps "diarization"
    _, workspace_id, user_id = _tenant(client, email_outbox)
    meeting_id, revision_id = _insert_timeline_meeting(
        workspace_id,
        user_id,
        turns=((0, "SPEAKER_00", 0, 4000, None), (1, "SPEAKER_01", 5000, 9000, None)),
        speakers=(
            ("SPEAKER_00", "مدیر", "person", None),
            ("SPEAKER_01", "کارشناس", "person", None),
        ),
    )
    first = _timeline(client, workspace_id, meeting_id)
    corrected_speaker = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": revision_id,
            "reason": "اصلاح گوینده",
            "edits": [{"segment_id": first["turns"][0]["segment_id"], "speaker_key": "SPEAKER_01"}],
        },
    )
    assert corrected_speaker.status_code == 201, corrected_speaker.text
    second_revision_id = corrected_speaker.json()["revision"]["id"]
    second = _timeline(client, workspace_id, meeting_id)
    # the second correction touches only the other turn's text
    corrected_text = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": second_revision_id,
            "reason": "اصلاح متن",
            "edits": [{"segment_id": second["turns"][1]["segment_id"], "text": "متن اصلاح‌شده"}],
        },
    )
    assert corrected_text.status_code == 201, corrected_text.text
    body = _timeline(client, workspace_id, meeting_id)
    assert [t["attribution"] for t in body["turns"]] == ["person", "diarization"]
    # the diarization fields still come from the ASR baseline two links up
    assert body["revision"]["diarization_status"] == "available"
    assert body["revision"]["diarization_provider"] == "pyannote"


def test_timeline_of_meeting_without_revision(client, email_outbox):
    _, workspace_id, user_id = _tenant(client, email_outbox)
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=user_id,
            title="بدون نسخه",
            status=MeetingStatus.COMPLETED,
            duration_seconds=120,
        )
        db.add(meeting)
        db.flush()
        db.add(
            MeetingSpeaker(
                workspace_id=workspace_id,
                meeting_id=meeting.id,
                speaker_key="SPEAKER_00",
                display_name="مدیر",
                identity_type="person",
            )
        )
        db.commit()
        meeting_id = meeting.id
    body = _timeline(client, workspace_id, meeting_id)
    assert body["revision"] is None
    assert body["overlap_basis"] == "turns"
    assert body["recording_duration_ms"] == 120000
    assert body["timeline_end_ms"] == 120000
    # amendment 1: with no revision the lanes are empty, even though the
    # meeting has a MeetingSpeaker row
    assert body["lanes"] == []
    assert body["turns"] == [] and body["overlaps"] == []
    assert body["overlap_ms"] == 0 and body["unknown_speech_ms"] == 0
    # speaker_choices is still computed, with the unknown speaker added
    assert body["correction"] == {
        "base_revision_id": None,
        "correctable": False,
        "speaker_choices": ["SPEAKER_00", "speaker_unknown"],
    }


def test_timeline_writes_nothing(client, email_outbox):
    _, workspace_id, user_id = _tenant(client, email_outbox)
    meeting_id, _ = _insert_timeline_meeting(
        workspace_id,
        user_id,
        turns=((0, "SPEAKER_00", 0, 1000, None),),
        speakers=(("SPEAKER_00", "مدیر", "person", None),),
    )
    before = _counts()
    for _ in range(2):
        _timeline(client, workspace_id, meeting_id)
    assert _counts() == before


def test_timeline_denies_and_allows(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"member": "member", "viewer": "viewer"},
    )
    workspace_id = owner.workspace_id
    meeting_id, revision_id = _insert_timeline_meeting(
        workspace_id,
        owner.user_id,
        turns=((0, "SPEAKER_00", 0, 1000, None),),
    )
    other_meeting_id, other_revision_id = _insert_timeline_meeting(
        workspace_id,
        owner.user_id,
        title="جلسه دیگر",
        turns=((0, "SPEAKER_00", 0, 1000, None),),
    )
    assert other_revision_id != revision_id
    deleted_meeting_id, _ = _insert_timeline_meeting(
        workspace_id,
        owner.user_id,
        title="حذف‌شده",
        turns=((0, "SPEAKER_00", 0, 1000, None),),
        deleted=True,
    )
    # a viewer role (meeting:read) reads the timeline
    assert (
        people["viewer"]
        .get(
            f"/api/v1/meetings/{meeting_id}/transcript/timeline",
        )
        .status_code
        == 200
    )
    # no session: 401
    client.cookies.clear()
    assert (
        client.get(
            f"/api/v1/meetings/{meeting_id}/transcript/timeline",
            headers=_read_headers(workspace_id),
        ).status_code
        == 401
    )
    # the same request with a session again: 200
    login_user(client, OWNER_LOGIN)
    assert (
        client.get(
            f"/api/v1/meetings/{meeting_id}/transcript/timeline",
            headers=_read_headers(workspace_id),
        ).status_code
        == 200
    )
    # a revision of another meeting of the same workspace: 404 revision_not_found
    foreign = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript/timeline",
        headers=_read_headers(workspace_id),
        params={"revision_id": other_revision_id},
    )
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "revision_not_found"
    own = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript/timeline",
        headers=_read_headers(workspace_id),
        params={"revision_id": revision_id},
    )
    assert own.status_code == 200
    # a soft-deleted meeting: 404 meeting_not_found
    deleted = client.get(
        f"/api/v1/meetings/{deleted_meeting_id}/transcript/timeline",
        headers=_read_headers(workspace_id),
    )
    assert deleted.status_code == 404
    assert deleted.json()["error"]["code"] == "meeting_not_found"

    # a meeting of another workspace: 404 meeting_not_found; same workspace: 200
    outsider = register_user(client, email_outbox, email="timeline-outsider@example.com")
    login_user(client, outsider)
    outsider_workspace = _workspace_of_user("timeline-outsider@example.com")
    foreign_meeting = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript/timeline",
        headers=_read_headers(outsider_workspace),
    )
    assert foreign_meeting.status_code == 404
    assert foreign_meeting.json()["error"]["code"] == "meeting_not_found"
    login_user(client, OWNER_LOGIN)
    assert (
        client.get(
            f"/api/v1/meetings/{meeting_id}/transcript/timeline",
            headers=_read_headers(workspace_id),
        ).status_code
        == 200
    )

    # team scope: a member outside the meeting's team sees 404, inside it 200
    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200, switched.text
    team_id = owner.post("/api/v1/teams", json={"name": "فروش"}).json()["id"]
    # a supervisor assignment confers the team's records (an agent assignment
    # alone does not); the meeting below is not created by or attributed to
    # the member, so only the team scope lets them through
    assert (
        owner.post(
            f"/api/v1/teams/{team_id}/assignments",
            json={"user_id": people["member"].user_id, "role": "supervisor"},
        ).status_code
        == 201
    )
    with inspection_session_factory()() as db:
        db.execute(
            Meeting.__table__.update().where(Meeting.id == meeting_id).values(team_id=team_id)
        )
        db.commit()
    member = people["member"]
    assert (
        member.get(
            f"/api/v1/meetings/{meeting_id}/transcript/timeline",
        ).status_code
        == 200
    )
    outside = member.get(f"/api/v1/meetings/{other_meeting_id}/transcript/timeline")
    assert outside.status_code == 404
    assert outside.json()["error"]["code"] == "meeting_not_found"


def test_timeline_reports_the_unknown_lane_for_both_unknown_keys(client, email_outbox):
    _, workspace_id, user_id = _tenant(client, email_outbox)
    meeting_id, _ = _insert_timeline_meeting(
        workspace_id,
        user_id,
        turns=(
            (0, "speaker_unknown", 0, 1000, None),
            (1, "unknown", 1000, 2000, None),  # the column default key
        ),
    )
    body = _timeline(client, workspace_id, meeting_id)
    assert [(lane["speaker_key"], lane["kind"]) for lane in body["lanes"]] == [
        ("speaker_unknown", "unknown"),
        ("unknown", "unknown"),
    ]
    assert [t["attribution"] for t in body["turns"]] == ["none", "none"]
    assert [t["review_reasons"] for t in body["turns"]] == [
        ["unknown_speaker"],
        ["unknown_speaker"],
    ]
    assert body["unknown_speech_ms"] == 2000
    # no overlap is claimed between the two unknown turns (cross_speaker rule
    # is not needed here: they do not overlap in time), but the unknown turns
    # of one meeting are never merged into one lane per key
    assert body["overlaps"] == []


def test_timeline_of_two_overlapping_unknown_turns_is_cross_speaker(client, email_outbox):
    _, workspace_id, user_id = _tenant(client, email_outbox)
    meeting_id, _ = _insert_timeline_meeting(
        workspace_id,
        user_id,
        turns=(
            (0, "speaker_unknown", 0, 3000, None),
            (1, "unknown", 2000, 4000, None),
        ),
    )
    body = _timeline(client, workspace_id, meeting_id)
    # two unknown turns may not be the same person: the overlap is cross_speaker
    assert [o["kind"] for o in body["overlaps"]] == ["cross_speaker"]
    assert body["overlaps"][0]["start_ms"] == 2000
    assert body["overlaps"][0]["end_ms"] == 3000
    assert body["overlaps"][0]["includes_unknown"] is True
    assert body["overlaps"][0]["speaker_keys"] == ["speaker_unknown", "unknown"]
    assert body["overlap_ms"] == 1000
    assert all("overlap" in t["review_reasons"] for t in body["turns"])


def test_unknown_key_set_is_exactly_the_two_stored_labels():
    # the providers' label and the column default of the speaker key
    assert UNKNOWN_SPEAKER_KEYS == {"speaker_unknown", "unknown"}
