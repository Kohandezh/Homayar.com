from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import event, select

from backend.database.relational import get_engine
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    AuditEvent,
    Entitlement,
    EvidenceSpan,
    IngestionRecord,
    IngestionSourceType,
    IngestionStatus,
    JobState,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    Membership,
    ProcessingJob,
    ProcessingStageRun,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    UsageReservation,
    Workspace,
)
from backend.services.audit_event_types import lookup_audit_event_type
from backend.services.audit_service import AUDIT_EVENT_CATALOG
from backend.tests._database import inspection_session_factory
from backend.tests.test_meeting_counts import _signup, _workspace_with_roles
from backend.tests.test_review_sampling_oracle import _reference, _sorted_shortfalls

COVERAGE = "/api/v1/admin/coverage"
SAMPLES = "/api/v1/admin/review-samples"
DRAWN = "review_sample.drawn"
MAX_SEED = 2**53 - 1
FROM = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
TO = FROM + timedelta(days=1)
COUNTER_KEYS = {
    "definition",
    "numerator_label",
    "denominator_label",
    "numerator",
    "denominator",
    "ratio",
    "status",
    "reason",
    "buckets",
}
PROCESSING_BUCKETS = ("deleted", "in_progress", "failed", "transcribed", "no_transcript")
EVALUATION_BUCKETS = (
    "fully_evaluated",
    "transcript_partial",
    "analysis_unavailable",
    "analysis_partial",
    "analysis_stale",
    "analysis_recompute_failed",
    "mock_analysis",
    "unsupported_findings",
)
CONFIG_FIELDS = {
    "random_size",
    "stratify_by",
    "per_stratum_size",
    "risk_flags",
    "risk_cap",
    "scope",
}
ALL_FLAGS = ["evidence_partial", "speaker_attribution_unknown", "transcript_corrected"]

C = MeetingStatus
SCENARIO: dict[str, tuple[dict, str | None, str | None, tuple[str, ...]]] = {
    "full": (
        {"summary": "supported", "team": "a", "agent": True, "source": "upload", "language": "fa"},
        "transcribed",
        "fully_evaluated",
        (),
    ),
    "no_summary": (
        {"team": "b", "source": "api", "language": "en"},
        "transcribed",
        "fully_evaluated",
        (),
    ),
    "user_action": (
        {"summary": "supported", "action": "user", "team": "a", "language": "en"},
        "transcribed",
        "fully_evaluated",
        ("evidence_partial",),
    ),
    "corrected": (
        {"transcript": "corrected", "summary": "supported", "team": "b", "agent": True},
        "transcribed",
        "fully_evaluated",
        ("transcript_corrected",),
    ),
    "partial_evidence": (
        {"summary": "partial", "source": "api"},
        "transcribed",
        "fully_evaluated",
        ("evidence_partial",),
    ),
    "undiarized": (
        {"diarized": False, "summary": "supported", "team": "a"},
        "transcribed",
        "fully_evaluated",
        ("speaker_attribution_unknown",),
    ),
    "asr_partial": (
        {"asr": "partial", "summary": "supported"},
        "transcribed",
        "transcript_partial",
        (),
    ),
    "no_run": ({"run": "none"}, "transcribed", "analysis_unavailable", ()),
    "failed_run": ({"run": "failed"}, "transcribed", "analysis_unavailable", ()),
    "partial_status": (
        {"status": C.PARTIAL, "summary": "supported"},
        "transcribed",
        "analysis_partial",
        (),
    ),
    "partial_run": (
        {"run": "partial", "summary": "supported"},
        "transcribed",
        "analysis_partial",
        (),
    ),
    "stale": ({"run": "stale"}, "transcribed", "analysis_stale", ()),
    "mock_run": ({"run": "mock", "summary": "supported"}, "transcribed", "mock_analysis", ()),
    "mock_revision": (
        {"revision_mock": True, "summary": "supported"},
        "transcribed",
        "mock_analysis",
        (),
    ),
    "unsupported_overview": ({"summary": "unsupported"}, "transcribed", "unsupported_findings", ()),
    "unsupported_keypoint": ({"summary": "keypoint"}, "transcribed", "unsupported_findings", ()),
    "ai_action": (
        {"summary": "supported", "action": "ai"},
        "transcribed",
        "unsupported_findings",
        (),
    ),
    "orphan": ({"ledger": "orphan"}, "deleted", None, ()),
    "soft_deleted": ({"deleted": True, "summary": "supported"}, "deleted", None, ()),
    "queued": ({"status": C.QUEUED, "transcript": "none", "run": "none"}, "in_progress", None, ()),
    "analyzing": ({"status": C.ANALYZING, "run": "none"}, "in_progress", None, ()),
    "failed_full": ({"status": C.FAILED, "summary": "supported"}, "failed", None, ()),
    "cancelled": ({"status": C.CANCELLED, "summary": "supported"}, "failed", None, ()),
    "no_revision": ({"transcript": "none", "run": "none"}, "no_transcript", None, ()),
    "partial_empty": (
        {"status": C.PARTIAL, "transcript": "empty", "run": "none"},
        "no_transcript",
        None,
        (),
    ),
    "noncurrent": ({"transcript": "noncurrent", "run": "none"}, "no_transcript", None, ()),
    "rejected_media": ({"ledger": "rejected", "reason": "media_invalid"}, None, None, ()),
    "rejected_media_again": ({"ledger": "rejected", "reason": "media_invalid"}, None, None, ()),
    "rejected_long": ({"ledger": "rejected", "reason": "too_long"}, None, None, ()),
}


def _iso(value: datetime) -> str:
    return value.isoformat()


def _code(response) -> str:
    return response.json()["error"]["code"]


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None, value
    return parsed


def _owner_of(db, workspace_id: str) -> tuple[str, str]:
    organization_id = db.scalar(
        select(Workspace.organization_id).where(Workspace.id == workspace_id)
    )
    owner_id = db.scalar(
        select(Membership.user_id)
        .where(Membership.workspace_id == workspace_id)
        .order_by(Membership.created_at.asc())
    )
    assert organization_id is not None and owner_id is not None
    return organization_id, owner_id


def _ledger(organization_id, workspace_id, *, at, status, reason=None, meeting_id=None):
    return IngestionRecord(
        organization_id=organization_id,
        workspace_id=workspace_id,
        source_type=IngestionSourceType.FILE_UPLOAD,
        source_provider="console",
        external_event_id=f"oracle-{uuid.uuid4()}",
        payload_hash="a" * 64,
        status=status,
        reason=reason,
        received_at=at,
        meeting_id=meeting_id,
    )


def _seed(
    workspace_id: str,
    *,
    at: datetime,
    ledger: str = "accepted",
    reason: str | None = None,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    transcript: str = "current",
    diarized: bool = True,
    run: str = "completed",
    revision_mock: bool = False,
    summary: str | None = None,
    action: str | None = None,
    asr: str | None = None,
    deleted: bool = False,
    team_id: str | None = None,
    agent_user_id: str | None = None,
    source: str = "upload",
    language: str = "fa",
) -> str | None:
    with inspection_session_factory()() as db:
        organization_id, owner_id = _owner_of(db, workspace_id)
        if ledger in ("rejected", "orphan"):
            db.add(
                _ledger(
                    organization_id,
                    workspace_id,
                    at=at,
                    status=IngestionStatus.ACCEPTED
                    if ledger == "orphan"
                    else IngestionStatus.REJECTED,
                    reason=reason,
                )
            )
            db.commit()
            return None
        meeting = Meeting(
            workspace_id=workspace_id,
            created_by_user_id=owner_id,
            title="Coverage call",
            status=status,
            duration_seconds=30,
            team_id=team_id,
            agent_user_id=agent_user_id,
            source=source,
            language=language,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(meeting)
        db.flush()
        if ledger == "accepted":
            db.add(
                _ledger(
                    organization_id,
                    workspace_id,
                    at=at,
                    status=IngestionStatus.ACCEPTED,
                    meeting_id=meeting.id,
                )
            )
        revisions: list[tuple[TranscriptRevision, list[TranscriptSegment]]] = []
        if transcript != "none":
            if transcript == "corrected" or run == "stale":
                plan = [(TranscriptSource.ASR, False), (TranscriptSource.CORRECTION, True)]
            else:
                plan = [(TranscriptSource.ASR, transcript != "noncurrent")]
            parent = None
            for number, (origin, current) in enumerate(plan, start=1):
                count = 0 if transcript == "empty" else 2
                revision = TranscriptRevision(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    revision_number=number,
                    parent_revision_id=parent.id if parent is not None else None,
                    source=origin,
                    reason="asr" if origin == TranscriptSource.ASR else "correction",
                    language=language,
                    segment_count=count,
                    created_by_user_id=owner_id if origin == TranscriptSource.CORRECTION else None,
                    diarization_status="available" if diarized else None,
                    is_mock=revision_mock,
                    is_current=current,
                )
                db.add(revision)
                db.flush()
                segments = [
                    TranscriptSegment(
                        workspace_id=workspace_id,
                        meeting_id=meeting.id,
                        revision_id=revision.id,
                        sequence=index,
                        speaker_key="speaker_1",
                        start_ms=index * 1000,
                        end_ms=index * 1000 + 900,
                        text=f"turn {index}",
                    )
                    for index in range(count)
                ]
                db.add_all(segments)
                db.flush()
                revisions.append((revision, segments))
                parent = revision
        analysis = None
        target = None
        if run != "none" and revisions:
            target = revisions[0] if run == "stale" else revisions[-1]
            analysis = AnalysisRun(
                workspace_id=workspace_id,
                meeting_id=meeting.id,
                transcript_revision_id=target[0].id,
                state={
                    "completed": StageState.COMPLETED,
                    "mock": StageState.COMPLETED,
                    "stale": StageState.COMPLETED,
                    "partial": StageState.PARTIAL,
                    "failed": StageState.FAILED,
                }[run],
                provider="oracle",
                model="oracle-v1",
                is_mock=run == "mock",
                is_current=True,
            )
            db.add(analysis)
            db.flush()
        if summary is not None and analysis is not None and target is not None:
            db.add(
                MeetingSummary(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    overview="overview",
                    decisions=[],
                    key_points=(
                        [
                            {
                                "category": "risk",
                                "description": "point without a citation",
                                "evidence_status": "sufficient",
                            }
                        ]
                        if summary == "keypoint"
                        else []
                    ),
                    topics=[],
                    provider="oracle",
                    model="oracle-v1",
                    analysis_run_id=analysis.id,
                    evidence_status="partial" if summary == "partial" else "sufficient",
                )
            )
            if summary != "unsupported":
                revision, segments = target
                db.add(
                    EvidenceSpan(
                        workspace_id=workspace_id,
                        meeting_id=meeting.id,
                        analysis_run_id=analysis.id,
                        transcript_revision_id=revision.id,
                        segment_id=segments[0].id,
                        speaker_key="speaker_1",
                        finding_type="summary",
                        finding_index=None,
                        text_start=0,
                        text_end=4,
                        start_ms=segments[0].start_ms,
                        end_ms=segments[0].end_ms,
                        evidence_type="turn_reference",
                    )
                )
        if action is not None:
            db.add(
                ActionItem(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    description="follow up",
                    created_by="ai" if action == "ai" else "user",
                    analysis_run_id=analysis.id if action == "ai" and analysis else None,
                    evidence_status="insufficient",
                )
            )
        if asr == "partial":
            entitlement_id = db.scalar(
                select(Entitlement.id).where(
                    Entitlement.workspace_id == workspace_id, Entitlement.metric == "audio_seconds"
                )
            )
            reservation = UsageReservation(
                workspace_id=workspace_id,
                entitlement_id=entitlement_id,
                meeting_id=meeting.id,
                idempotency_key=f"oracle:{uuid.uuid4()}",
                reserved_value=30,
            )
            db.add(reservation)
            db.flush()
            job = ProcessingJob(
                workspace_id=workspace_id,
                meeting_id=meeting.id,
                reservation_id=reservation.id,
                idempotency_key=f"oracle:{uuid.uuid4()}",
                state=JobState.COMPLETED,
                stage="completed",
                attempt_count=1,
                max_attempts=3,
            )
            db.add(job)
            db.flush()
            db.add(
                ProcessingStageRun(
                    workspace_id=workspace_id,
                    meeting_id=meeting.id,
                    job_id=job.id,
                    attempt=1,
                    sequence=1,
                    stage="asr",
                    state=StageState.PARTIAL,
                )
            )
        db.commit()
        return meeting.id


def _teams(owner) -> dict[str, str]:
    teams = {}
    for name in ("a", "b"):
        created = owner.post("/api/v1/teams", json={"name": f"Team {name}"})
        assert created.status_code == 201, created.text
        teams[name] = created.json()["id"]
    return teams


def _seed_scenario(owner, *, start: datetime = FROM, teams=None) -> dict[str, dict]:
    teams = teams or _teams(owner)
    seeded: dict[str, dict] = {}
    for offset, name in enumerate(SCENARIO):
        spec, processing, evaluation, flags = SCENARIO[name]
        kwargs = dict(spec)
        team = kwargs.pop("team", None)
        agent = kwargs.pop("agent", False)
        at = start + timedelta(minutes=offset)
        meeting_id = _seed(
            owner.workspace_id,
            at=at,
            team_id=teams[team] if team else None,
            agent_user_id=owner.user_id if agent else None,
            **kwargs,
        )
        seeded[name] = {
            "meeting_id": meeting_id,
            "at": at,
            "processing": processing,
            "evaluation": evaluation,
            "flags": flags,
            "strata": {
                "team_id": teams[team] if team else None,
                "agent_user_id": owner.user_id if agent else None,
                "source": kwargs.get("source", "upload"),
                "language": kwargs.get("language", "fa"),
            },
        }
    return seeded


def _coverage(persona, start: datetime | None = FROM, end: datetime | None = TO) -> dict:
    params = {}
    if start is not None:
        params["from"] = _iso(start)
    if end is not None:
        params["to"] = _iso(end)
    response = persona.get(COVERAGE, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _sample(persona, body: dict, status: int = 200):
    response = persona.post(SAMPLES, json=body)
    assert response.status_code == status, response.text
    return response.json()


def _body(config: dict, *, seed: int | None = 42, start: datetime = FROM, end: datetime = TO):
    body = {"from": _iso(start), "to": _iso(end), "config": config}
    if seed is not None:
        body["seed"] = seed
    return body


def _drawn_events(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.workspace_id == workspace_id, AuditEvent.event_type == DRAWN)
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def _counter(numerator, denominator, num_label, den_label, buckets):
    computable = denominator > 0
    return {
        "numerator_label": num_label,
        "denominator_label": den_label,
        "numerator": numerator if computable else 0,
        "denominator": denominator,
        "ratio": round(numerator / denominator, 4) if computable else None,
        "status": "computed" if computable else "not_computable",
        "reason": None if computable else "empty_denominator",
        "buckets": buckets,
    }


def _without_definition(counter: dict) -> dict:
    return {key: value for key, value in counter.items() if key != "definition"}


def _assert_reconciles(counters: dict) -> None:
    ingestion, processing = counters["ingestion"], counters["processing"]
    evaluation, review = counters["evaluation"], counters["review"]
    assert (
        ingestion["buckets"]["accepted"] + ingestion["buckets"]["rejected"]
        == ingestion["denominator"]
    )
    assert processing["denominator"] == ingestion["numerator"]
    assert evaluation["denominator"] == processing["numerator"]
    assert review["denominator"] == evaluation["numerator"]
    for name in ("ingestion", "processing", "evaluation"):
        counter = counters[name]
        assert sum(counter["buckets"].values()) == counter["denominator"], name
    assert sum(ingestion["rejected_reasons"].values()) == ingestion["buckets"]["rejected"]


def _specs(seeded: dict[str, dict]) -> list[tuple]:
    return [
        (row["meeting_id"], row["strata"], row["flags"])
        for row in seeded.values()
        if row["evaluation"] == "fully_evaluated"
    ]


def _assert_draw(actual: dict, specs: list[tuple], config: dict, seed: int) -> None:
    expected = _reference(specs, config, seed)
    actual = dict(actual)
    assert _sorted_shortfalls(actual.pop("shortfalls")) == _sorted_shortfalls(
        expected.pop("shortfalls")
    )
    assert actual == expected


def test_four_counters_have_the_exact_values_and_reconcile(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-values@example.com")
    _seed_scenario(owner)
    _seed(owner.workspace_id, at=FROM - timedelta(seconds=1), ledger="rejected", reason="early")
    _seed(owner.workspace_id, at=TO, summary="supported")
    _seed(owner.workspace_id, at=FROM + timedelta(hours=2), ledger="none", summary="supported")

    body = _coverage(owner)
    assert set(body) == {"coverage_version", "window", "counters"}
    assert body["coverage_version"] == "coverage/1"
    assert body["window"]["basis"] == "ingestion_received_at"
    assert set(body["window"]) == {"from", "to", "basis"}
    assert _instant(body["window"]["from"]) == FROM and _instant(body["window"]["to"]) == TO
    counters = body["counters"]
    assert set(counters) == {"ingestion", "processing", "evaluation", "review"}

    ingestion = counters["ingestion"]
    assert set(ingestion) == COUNTER_KEYS | {"rejected_reasons"}
    assert ingestion["rejected_reasons"] == {"media_invalid": 2, "too_long": 1}
    assert _without_definition({k: v for k, v in ingestion.items() if k != "rejected_reasons"}) == (
        _counter(26, 29, "valid", "ingested", {"accepted": 26, "rejected": 3})
    )
    assert ingestion["ratio"] == 0.8966

    processing = counters["processing"]
    assert set(processing) == COUNTER_KEYS
    assert _without_definition(processing) == _counter(
        17,
        26,
        "transcribed",
        "valid",
        {"deleted": 2, "in_progress": 2, "failed": 2, "transcribed": 17, "no_transcript": 3},
    )
    assert processing["ratio"] == 0.6538

    evaluation = counters["evaluation"]
    assert set(evaluation) == COUNTER_KEYS
    assert _without_definition(evaluation) == _counter(
        6,
        17,
        "fully_evaluated",
        "transcribed",
        {
            "fully_evaluated": 6,
            "transcript_partial": 1,
            "analysis_unavailable": 2,
            "analysis_partial": 2,
            "analysis_stale": 1,
            "analysis_recompute_failed": 0,
            "mock_analysis": 2,
            "unsupported_findings": 3,
        },
    )
    assert evaluation["ratio"] == 0.3529

    review = counters["review"]
    assert set(review) == COUNTER_KEYS
    assert _without_definition(review) == {
        "numerator_label": "reviewed",
        "denominator_label": "fully_evaluated",
        "numerator": None,
        "denominator": 6,
        "ratio": None,
        "status": "not_computable",
        "reason": "review_records_unavailable",
        "buckets": {},
    }
    _assert_reconciles(counters)


def test_definitions_are_plain_distinct_sentences(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-definitions@example.com")
    counters = _coverage(owner)["counters"]
    definitions = [counters[name]["definition"] for name in counters]
    assert len(set(definitions)) == 4
    for text in definitions:
        assert isinstance(text, str) and text.strip()
        assert "—" not in text and "--" not in text
    assert "HOM-73" in counters["review"]["definition"]


def test_empty_window_is_not_computable_with_zero_filled_buckets(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-empty@example.com")
    counters = _coverage(owner)["counters"]
    assert _without_definition(
        {k: v for k, v in counters["ingestion"].items() if k != "rejected_reasons"}
    ) == _counter(0, 0, "valid", "ingested", {"accepted": 0, "rejected": 0})
    assert counters["ingestion"]["rejected_reasons"] == {}
    assert _without_definition(counters["processing"]) == _counter(
        0, 0, "transcribed", "valid", dict.fromkeys(PROCESSING_BUCKETS, 0)
    )
    assert _without_definition(counters["evaluation"]) == _counter(
        0, 0, "fully_evaluated", "transcribed", dict.fromkeys(EVALUATION_BUCKETS, 0)
    )
    review = counters["review"]
    assert review["numerator"] is None and review["ratio"] is None
    assert review["denominator"] == 0 and review["buckets"] == {}
    assert review["status"] == "not_computable"
    assert review["reason"] == "review_records_unavailable"


def test_each_meeting_lands_in_one_bucket_that_agrees_with_its_call_report(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-parity@example.com")
    seeded = _seed_scenario(owner)
    for name, row in seeded.items():
        counters = _coverage(owner, row["at"], row["at"] + timedelta(seconds=1))["counters"]
        _assert_reconciles(counters)
        assert counters["ingestion"]["denominator"] == 1, name
        if row["processing"] is None:
            assert counters["ingestion"]["buckets"]["rejected"] == 1, name
            continue
        processing = {k: v for k, v in counters["processing"]["buckets"].items() if v}
        assert processing == {row["processing"]: 1}, name
        evaluation = {k: v for k, v in counters["evaluation"]["buckets"].items() if v}
        if row["evaluation"] is None:
            assert evaluation == {}, name
        else:
            assert evaluation == {row["evaluation"]: 1}, name
        if row["meeting_id"] is None or row["processing"] == "deleted":
            continue
        report = owner.get(f"/api/v1/meetings/{row['meeting_id']}/report")
        assert report.status_code == 200, report.text
        completeness = report.json()["completeness"]
        fully = counters["evaluation"]["buckets"]["fully_evaluated"] == 1
        assert completeness["is_complete"] is fully, name
        if row["evaluation"] is not None and not fully:
            assert completeness["notices"][0] == row["evaluation"], name


def test_failed_meeting_never_inflates_evaluation_but_the_completed_twin_counts(
    client, email_outbox
):
    owner = _signup(client, email_outbox, email="cov-failed@example.com")
    failed = _seed(owner.workspace_id, at=FROM, status=MeetingStatus.FAILED, summary="supported")
    completed = _seed(owner.workspace_id, at=FROM + timedelta(minutes=1), summary="supported")
    first = _coverage(owner, FROM, FROM + timedelta(seconds=1))["counters"]
    assert first["processing"]["buckets"]["failed"] == 1
    assert first["processing"]["numerator"] == 0 and first["evaluation"]["denominator"] == 0
    second = _coverage(owner, FROM + timedelta(minutes=1), TO)["counters"]
    assert second["processing"]["buckets"]["transcribed"] == 1
    assert second["evaluation"]["numerator"] == 1

    draw = _sample(owner, _body({"random_size": 500}))["draw"]
    selected = {s["item_id"] for s in draw["selections"]}
    assert failed not in selected and completed in selected
    assert draw["population_count"] == 1


def test_deleted_meeting_is_its_own_bucket_and_its_live_twin_counts_by_status(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-deleted@example.com")
    gone = _seed(owner.workspace_id, at=FROM, deleted=True, summary="supported")
    live = _seed(owner.workspace_id, at=FROM + timedelta(minutes=1), summary="supported")
    gone_failed = _seed(
        owner.workspace_id,
        at=FROM + timedelta(minutes=2),
        deleted=True,
        status=MeetingStatus.FAILED,
    )
    counters = _coverage(owner)["counters"]
    assert counters["processing"]["buckets"]["deleted"] == 2
    assert counters["processing"]["buckets"]["failed"] == 0
    assert counters["processing"]["buckets"]["transcribed"] == 1
    draw = _sample(owner, _body({"random_size": 500}))["draw"]
    selected = {s["item_id"] for s in draw["selections"]}
    assert selected == {live} and not {gone, gone_failed} & selected


def test_window_includes_from_and_excludes_to(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-bounds@example.com")
    _seed(owner.workspace_id, at=FROM, summary="supported")
    _seed(owner.workspace_id, at=TO, summary="supported")
    assert _coverage(owner, FROM, TO)["counters"]["ingestion"]["denominator"] == 1
    assert (
        _coverage(owner, FROM, TO + timedelta(seconds=1))["counters"]["ingestion"]["denominator"]
        == 2
    )
    later = _coverage(owner, FROM + timedelta(seconds=1), TO + timedelta(seconds=1))
    assert later["counters"]["ingestion"]["denominator"] == 1


def test_window_accepts_other_timezone_spellings(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-zones@example.com")
    _seed(owner.workspace_id, at=FROM, summary="supported")
    tehran = timezone(timedelta(hours=3, minutes=30))
    for start, end in (
        (FROM.strftime("%Y-%m-%dT%H:%M:%SZ"), TO.strftime("%Y-%m-%dT%H:%M:%SZ")),
        (FROM.astimezone(tehran).isoformat(), TO.astimezone(tehran).isoformat()),
    ):
        response = owner.get(COVERAGE, params={"from": start, "to": end})
        assert response.status_code == 200, (start, response.text)
        body = response.json()
        assert body["counters"]["ingestion"]["denominator"] == 1, start
        assert _instant(body["window"]["from"]) == FROM
        assert _instant(body["window"]["to"]) == TO


def test_window_defaults_to_the_last_thirty_days(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-default@example.com")
    now = datetime.now(UTC)
    _seed(owner.workspace_id, at=now - timedelta(days=29), summary="supported")
    _seed(owner.workspace_id, at=now - timedelta(days=31), summary="supported")
    _seed(owner.workspace_id, at=now + timedelta(hours=1), summary="supported")

    body = _coverage(owner, None, None)
    start, end = _instant(body["window"]["from"]), _instant(body["window"]["to"])
    assert end - start == timedelta(days=30)
    assert abs(end - now) < timedelta(minutes=5)
    assert body["counters"]["ingestion"]["denominator"] == 1

    only_to = _coverage(owner, None, TO)
    assert _instant(only_to["window"]["to"]) == TO
    assert _instant(only_to["window"]["from"]) == TO - timedelta(days=30)

    only_from = _coverage(owner, now - timedelta(days=10), None)
    assert abs(_instant(only_from["window"]["to"]) - now) < timedelta(minutes=5)


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-03-01T09:00:00", "to": _iso(TO)},
        {"from": _iso(FROM), "to": "2026-03-02T09:00:00"},
        {"from": _iso(FROM), "to": _iso(FROM)},
        {"from": _iso(TO), "to": _iso(FROM)},
        {"from": _iso(FROM), "to": _iso(FROM + timedelta(days=367))},
        {"from": _iso(FROM), "to": _iso(FROM + timedelta(days=366, seconds=1))},
        {"from": _iso(datetime.now(UTC) - timedelta(days=400))},
    ],
)
def test_bad_window_is_refused_on_both_endpoints(client, email_outbox, params):
    owner = _signup(client, email_outbox, email="cov-badwindow@example.com")
    read = owner.get(COVERAGE, params=params)
    assert read.status_code == 422, read.text
    assert _code(read) == "coverage_window_invalid"
    drawn = owner.post(SAMPLES, json={**params, "seed": 1, "config": {"random_size": 1}})
    assert drawn.status_code == 422, drawn.text
    assert _code(drawn) == "coverage_window_invalid"
    assert _drawn_events(owner.workspace_id) == []


def test_a_window_of_exactly_366_days_is_accepted(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-366@example.com")
    end = FROM + timedelta(days=366)
    _seed(owner.workspace_id, at=end - timedelta(seconds=1), summary="supported")
    assert _coverage(owner, FROM, end)["counters"]["ingestion"]["denominator"] == 1
    body = _sample(owner, _body({"random_size": 5}, start=FROM, end=end))
    assert body["draw"]["population_count"] == 1


def test_only_the_callers_workspace_is_counted_or_sampled(client, email_outbox):
    other = _signup(client, email_outbox, email="cov-tenant-b@example.com")
    foreign = _seed_scenario(other)
    owner = _signup(client, email_outbox, email="cov-tenant-a@example.com")
    mine = _seed(owner.workspace_id, at=FROM, summary="supported")

    counters = _coverage(owner)["counters"]
    assert counters["ingestion"]["denominator"] == 1
    assert counters["evaluation"]["numerator"] == 1
    assert _coverage(other)["counters"]["ingestion"]["denominator"] == len(SCENARIO)

    draw = _sample(owner, _body({"random_size": 500}))["draw"]
    assert [s["item_id"] for s in draw["selections"]] == [mine]
    foreign_ids = {row["meeting_id"] for row in foreign.values() if row["meeting_id"]}
    trespass = owner.in_workspace(other.workspace_id)
    for response in (
        trespass.get(COVERAGE, params={"from": _iso(FROM), "to": _iso(TO)}),
        trespass.post(SAMPLES, json=_body({"random_size": 500})),
    ):
        assert response.status_code in (403, 404), response.text
        assert not any(meeting_id in response.text for meeting_id in foreign_ids)
    assert _drawn_events(other.workspace_id) == []


def test_counters_use_a_fixed_number_of_statements(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-statements@example.com")
    teams = _teams(owner)
    for copy in range(4):
        _seed_scenario(owner, start=FROM + timedelta(hours=copy), teams=teams)

    def statements(start: datetime, end: datetime) -> tuple[list[str], dict]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            seen.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        try:
            body = _coverage(owner, start, end)
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return seen, body

    few, small = statements(FROM, FROM + timedelta(hours=1))
    many, large = statements(FROM, FROM + timedelta(hours=4))
    assert small["counters"]["ingestion"]["denominator"] == len(SCENARIO)
    assert large["counters"]["ingestion"]["denominator"] == 4 * len(SCENARIO)
    assert large["counters"]["evaluation"]["numerator"] == 4 * 6
    assert len(many) <= len(few), (len(few), len(many))


def test_sample_population_strata_and_flags_match_the_fully_evaluated_meetings(
    client, email_outbox
):
    owner = _signup(client, email_outbox, email="cov-sample@example.com")
    seeded = _seed_scenario(owner)
    specs = _specs(seeded)
    assert len(specs) == 6
    config = {
        "random_size": 2,
        "stratify_by": ["team_id"],
        "per_stratum_size": 1,
        "risk_flags": ALL_FLAGS,
        "risk_cap": 2,
        "scope": {"language": ["en", "fa"]},
    }
    body = _sample(owner, _body(config, seed=20261021))
    assert set(body) == {"coverage_version", "window", "draw"}
    assert body["coverage_version"] == "coverage/1"
    assert _instant(body["window"]["from"]) == FROM and _instant(body["window"]["to"]) == TO
    assert body["window"]["basis"] == "ingestion_received_at"
    _assert_draw(body["draw"], specs, config, 20261021)

    everything = _sample(owner, _body({"random_size": 500, "risk_flags": ALL_FLAGS}, seed=3))
    _assert_draw(everything["draw"], specs, {"random_size": 500, "risk_flags": ALL_FLAGS}, 3)
    selected = {s["item_id"] for s in everything["draw"]["selections"]}
    expected = {meeting_id for meeting_id, _, _ in specs}
    assert selected == expected
    review = _coverage(owner)["counters"]["review"]
    assert everything["draw"]["population_count"] == review["denominator"] == 6
    assert everything["draw"]["risk_flagged_count"] == 4


def test_sample_scope_excludes_and_admits_by_meeting_values(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-scope@example.com")
    english = _seed(owner.workspace_id, at=FROM, summary="supported", language="en")
    persian = _seed(owner.workspace_id, at=FROM + timedelta(minutes=1), summary="supported")
    only_fa = _sample(owner, _body({"random_size": 10, "scope": {"language": ["fa"]}}))["draw"]
    assert [s["item_id"] for s in only_fa["selections"]] == [persian]
    assert only_fa["scoped_count"] == 1 and only_fa["population_count"] == 2
    both = _sample(owner, _body({"random_size": 10, "scope": {"language": ["en", "fa"]}}))["draw"]
    assert {s["item_id"] for s in both["selections"]} == {english, persian}
    assert both["population_digest"] == only_fa["population_digest"]


def test_server_picks_a_seed_when_none_is_given_and_it_reproduces(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-seed@example.com")
    for minute in range(5):
        _seed(owner.workspace_id, at=FROM + timedelta(minutes=minute), summary="supported")
    config = {"random_size": 2}
    first = _sample(owner, _body(config, seed=None))["draw"]
    seed = first["seed"]
    assert type(seed) is int and 0 <= seed <= MAX_SEED
    again = _sample(owner, _body(config, seed=seed))["draw"]
    assert again["selection_digest"] == first["selection_digest"]
    assert again["population_digest"] == first["population_digest"]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"seed": -1, "config": {"random_size": 1}}, "seed_invalid"),
        ({"seed": MAX_SEED + 1, "config": {"random_size": 1}}, "seed_invalid"),
        ({"seed": True, "config": {"random_size": 1}}, "seed_invalid"),
        ({"seed": 1, "config": {"random_size": 501}}, "sampling_config_invalid"),
        ({"seed": 1, "config": {"random_size": 1, "size": 2}}, "sampling_config_invalid"),
        ({"seed": 1, "config": {"risk_flags": ["needs_review"]}}, "sampling_config_invalid"),
        (
            {"seed": 1, "config": {"stratify_by": ["team_id"], "per_stratum_size": 0}},
            "sampling_config_invalid",
        ),
        ({"seed": 1, "config": {}}, "sampling_config_empty"),
        ({"seed": 1, "config": {"random_size": 0}}, "sampling_config_empty"),
    ],
)
def test_bad_seed_or_config_is_refused_with_its_code(client, email_outbox, body, code):
    owner = _signup(client, email_outbox, email="cov-badsample@example.com")
    response = owner.post(SAMPLES, json={"from": _iso(FROM), "to": _iso(TO), **body})
    assert response.status_code == 422, response.text
    assert _code(response) == code
    assert _drawn_events(owner.workspace_id) == []


@pytest.mark.parametrize(
    ("seed", "config"),
    [
        (0, {"random_size": 500}),
        (MAX_SEED, {"random_size": 1}),
        (5, {"stratify_by": ["team_id", "language"], "per_stratum_size": 100}),
        (5, {"risk_flags": ALL_FLAGS, "risk_cap": 500}),
    ],
)
def test_boundary_seed_and_config_are_accepted(client, email_outbox, seed, config):
    owner = _signup(client, email_outbox, email="cov-goodsample@example.com")
    body = _sample(owner, _body(config, seed=seed))
    assert body["draw"]["seed"] == seed
    assert len(_drawn_events(owner.workspace_id)) == 1


def test_unknown_body_key_is_refused(client, email_outbox):
    owner = _signup(client, email_outbox, email="cov-unknown@example.com")
    response = owner.post(SAMPLES, json={**_body({"random_size": 1}), "limit": 5})
    assert response.status_code == 422, response.text
    assert _drawn_events(owner.workspace_id) == []
    assert owner.post(SAMPLES, json=_body({"random_size": 1})).status_code == 200


def _leaves(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _leaves(item, (*path, str(key)))
    else:
        yield path, value


def _tokens(path: tuple[str, ...]) -> set[str]:
    return {part for key in path for part in key.casefold().split("_")}


def _rebuild(details: dict) -> dict:
    configs = [
        value
        for _, value in _walk_dicts(details)
        if isinstance(value, dict) and set(value) == CONFIG_FIELDS
    ]
    assert len(configs) == 1, details
    seeds = [
        value for path, value in _leaves(details) if "seed" in _tokens(path) and type(value) is int
    ]
    assert len(seeds) == 1, details
    instants: dict[str, set[datetime]] = {"from": set(), "to": set()}
    for path, value in _leaves(details):
        if not isinstance(value, str):
            continue
        try:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            continue
        tokens = _tokens(path)
        if tokens & {"from", "start"}:
            instants["from"].add(moment)
        if tokens & {"to", "end", "until"}:
            instants["to"].add(moment)
    assert len(instants["from"]) == 1 and len(instants["to"]) == 1, details
    return {
        "from": _iso(instants["from"].pop()),
        "to": _iso(instants["to"].pop()),
        "seed": seeds[0],
        "config": configs[0],
    }


def _walk_dicts(value, path=()):
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk_dicts(item, (*path, str(key)))


def test_draw_is_audited_and_reproducible_from_the_stored_row_alone(client, email_outbox):
    assert DRAWN in AUDIT_EVENT_CATALOG
    assert lookup_audit_event_type(DRAWN) is not None
    owner = _signup(client, email_outbox, email="cov-audit@example.com")
    seeded = _seed_scenario(owner)
    config = {
        "random_size": 3,
        "stratify_by": ["team_id", "language"],
        "per_stratum_size": 1,
        "risk_flags": ["transcript_corrected", "evidence_partial"],
        "risk_cap": 2,
        "scope": {"language": ["fa", "en"], "team_id": ["unassigned", *_team_ids(seeded)]},
    }
    original = _sample(owner, _body(config, seed=987654321))["draw"]
    events = _drawn_events(owner.workspace_id)
    assert len(events) == 1
    stored = events[0]
    assert stored.outcome == "success"
    details = stored.details
    values = [value for _, value in _leaves(details)]
    for expected in (
        "review-sampling/1",
        "coverage/1",
        original["config_digest"],
        original["population_digest"],
        original["selection_digest"],
    ):
        assert expected in values, (expected, details)
    assert any(
        "population" in _tokens(path) and value == original["population_count"]
        for path, value in _leaves(details)
        if type(value) is int
    ), details
    assert any(
        any(token.startswith("select") for token in _tokens(path))
        and value == len(original["selections"])
        for path, value in _leaves(details)
        if type(value) is int
    ), details

    rebuilt = _rebuild(details)
    assert rebuilt["config"] == original["config"]
    assert rebuilt["seed"] == 987654321
    assert _instant(rebuilt["from"]) == FROM and _instant(rebuilt["to"]) == TO
    replay = _sample(owner, rebuilt)["draw"]
    assert replay["population_digest"] == original["population_digest"]
    assert replay["selection_digest"] == original["selection_digest"]
    assert replay["config_digest"] == original["config_digest"]
    assert len(_drawn_events(owner.workspace_id)) == 2


def _team_ids(seeded: dict[str, dict]) -> list[str]:
    return sorted({row["strata"]["team_id"] for row in seeded.values() if row["strata"]["team_id"]})


def test_only_owner_and_admin_may_read_or_draw(client, email_outbox, monkeypatch):
    owner, people = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "viewer": "viewer", "sup": "member", "agent": "member"},
    )
    meeting_id = _seed(owner.workspace_id, at=FROM, summary="supported")
    params = {"from": _iso(FROM), "to": _iso(TO)}
    body = _body({"random_size": 5})

    def refused(name: str) -> None:
        persona = people[name]
        for response in (persona.get(COVERAGE, params=params), persona.post(SAMPLES, json=body)):
            assert response.status_code == 403, (name, response.text)
            assert _code(response) == "permission_denied"
            assert meeting_id not in response.text

    refused("sup")
    refused("viewer")
    team = owner.post("/api/v1/teams", json={"name": "Desk"})
    assert team.status_code == 201, team.text
    for name, role in (("sup", "supervisor"), ("agent", "agent")):
        assigned = owner.post(
            f"/api/v1/teams/{team.json()['id']}/assignments",
            json={"user_id": people[name].user_id, "role": role},
        )
        assert assigned.status_code == 201, assigned.text
    for persona in (owner, people["admin"]):
        read = persona.get(COVERAGE, params=params)
        assert read.status_code == 200, read.text
        drawn = persona.post(SAMPLES, json=body)
        assert drawn.status_code == 200, drawn.text
    assert len(_drawn_events(owner.workspace_id)) == 2

    refused("sup")
    refused("agent")

    no_csrf = client.post(
        SAMPLES,
        json=body,
        cookies=owner.cookies,
        headers={"X-Workspace-ID": owner.workspace_id},
    )
    assert no_csrf.status_code in (401, 403), no_csrf.text
    assert meeting_id not in no_csrf.text

    minted = owner.post(
        "/api/v1/api-keys",
        json={"name": "coverage", "scopes": ["meeting:read", "audit:read"], "expires_in_days": 30},
    )
    assert minted.status_code == 201, minted.text
    key = minted.json()["key"]
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {key}"}
    for response in (
        client.get(COVERAGE, params=params, headers=bearer),
        client.post(SAMPLES, json=body, headers=bearer),
    ):
        assert response.status_code == 403, response.text
        assert _code(response) == "api_key_scope_denied"
    for response in (
        client.get(COVERAGE, params=params, headers={"X-Workspace-ID": owner.workspace_id}),
        client.post(SAMPLES, json=body, headers={"X-Workspace-ID": owner.workspace_id}),
    ):
        assert response.status_code == 401, response.text
    assert len(_drawn_events(owner.workspace_id)) == 2
