"""HOM-72 (WP14-T01): separate coverage counters, ``GET /admin/coverage``.

Four counters (ingestion, processing, evaluation, review) over the workspace's
ingestion ledger in a window.  Each has a definition, a numerator, a
denominator and buckets that sum to it; each denominator is the previous
numerator.  A failed call never counts as transcribed or fully evaluated, and
"fully evaluated" is exactly the call report's ``completeness.is_complete``.
Rows are seeded with DBA writes so every state can be placed at a fixed time.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select

from backend.database.relational import get_engine
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    Entitlement,
    EvidenceSpan,
    IngestionRecord,
    IngestionSourceType,
    IngestionStatus,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    ProcessingStageRun,
    StageState,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    UsageReservation,
    Workspace,
)
from backend.services.coverage_service import (
    EVALUATION_BUCKETS,
    INGESTION_BUCKETS,
    PROCESSING_BUCKETS,
    CoverageWindowError,
    compute_coverage,
    parse_window,
    unsupported_finding_count,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_admin_api import _register_and_login
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _headers, _workspace_with_roles

COVERAGE = "/api/v1/admin/coverage"
BASE = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
WINDOW = {"from": BASE.isoformat(), "to": (BASE + timedelta(days=1)).isoformat()}
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


# --- seeding -------------------------------------------------------------------


class Tenant:
    def __init__(self, client, email_outbox, email: str):
        self.client = client
        self.owner_id, self.workspace_id, self.cookies = _register_and_login(
            client, email_outbox, email=email
        )
        with inspection_session_factory()() as db:
            self.organization_id = db.scalar(
                select(Workspace.organization_id).where(Workspace.id == self.workspace_id)
            )

    @property
    def headers(self) -> dict[str, str]:
        return _headers(self.cookies, self.workspace_id)

    def get(self, path: str, **params):
        return self.client.get(path, params=params, cookies=self.cookies, headers=self.headers)

    def post(self, path: str, body: dict):
        return self.client.post(path, json=body, cookies=self.cookies, headers=self.headers)


def _seed_job(db, tenant: Tenant, meeting_id: str, *, asr_state: StageState) -> None:
    entitlement_id = db.scalar(
        select(Entitlement.id).where(
            Entitlement.workspace_id == tenant.workspace_id, Entitlement.metric == "audio_seconds"
        )
    )
    reservation = UsageReservation(
        workspace_id=tenant.workspace_id,
        entitlement_id=entitlement_id,
        meeting_id=meeting_id,
        idempotency_key=f"seed:{uuid.uuid4()}",
        reserved_value=30,
    )
    db.add(reservation)
    db.flush()
    job = ProcessingJob(
        workspace_id=tenant.workspace_id,
        meeting_id=meeting_id,
        reservation_id=reservation.id,
        idempotency_key=f"seed:{uuid.uuid4()}",
        stage="completed",
        attempt_count=1,
        max_attempts=3,
    )
    db.add(job)
    db.flush()
    db.add(
        ProcessingStageRun(
            workspace_id=tenant.workspace_id,
            meeting_id=meeting_id,
            job_id=job.id,
            attempt=1,
            sequence=1,
            stage="asr",
            state=asr_state,
        )
    )


def seed_call(
    tenant: Tenant,
    *,
    received_at: datetime = BASE,
    ledger: str = "accepted",
    reason: str = "unsupported_media",
    with_meeting: bool = True,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    deleted: bool = False,
    segments: int | None = 2,
    revision_source: TranscriptSource = TranscriptSource.ASR,
    diarization: str | None = "available",
    run: str | None = "current",
    run_state: StageState = StageState.COMPLETED,
    run_mock: bool = False,
    failed_recomputes: int = 0,
    summary: bool = True,
    summary_status: str = "sufficient",
    overview_cited: bool = True,
    schema_version: int = 1,
    key_points: list[dict[str, Any]] | None = None,
    actions: list[dict[str, Any]] | None = None,
    asr_state: StageState | None = None,
    team_id: str | None = None,
    agent_user_id: str | None = None,
    source: str = "upload",
    language: str = "fa",
) -> str | None:
    """One ledger row and, when asked, its meeting with transcript and analysis.

    ``run`` is ``"current"`` (analysis of the current revision), ``"stale"``
    (of an older revision; the current one is then a correction) or ``None``.
    In ``key_points`` an ``evidence_ids`` entry may be a placeholder that
    becomes a new span id: ``@insight``, ``@summary``, ``@action_item`` (spans of
    the current run), ``@human`` (no run) or ``@other_run`` (another run).
    """
    with inspection_session_factory()() as db:
        meeting_id = None
        if ledger == "accepted" and with_meeting:
            meeting = Meeting(
                workspace_id=tenant.workspace_id,
                created_by_user_id=tenant.owner_id,
                title="Coverage call",
                status=status,
                duration_seconds=30,
                deleted_at=received_at if deleted else None,
                team_id=team_id,
                agent_user_id=agent_user_id,
                source=source,
                language=language,
            )
            db.add(meeting)
            db.flush()
            meeting_id = meeting.id
        db.add(
            IngestionRecord(
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                source_type=IngestionSourceType.FILE_UPLOAD,
                source_provider="test",
                external_event_id=str(uuid.uuid4()),
                payload_hash="0" * 64,
                status=IngestionStatus.ACCEPTED
                if ledger == "accepted"
                else IngestionStatus.REJECTED,
                reason=None if ledger == "accepted" else reason,
                received_at=received_at,
                meeting_id=meeting_id,
            )
        )
        if meeting_id is None or segments is None:
            db.commit()
            return meeting_id

        def revision(number: int, source: TranscriptSource, *, current: bool):
            row = TranscriptRevision(
                workspace_id=tenant.workspace_id,
                meeting_id=meeting_id,
                revision_number=number,
                source=source,
                reason="seed",
                language=language,
                segment_count=segments,
                diarization_status=diarization,
                is_current=current,
            )
            db.add(row)
            db.flush()
            for sequence in range(segments):
                db.add(
                    TranscriptSegment(
                        workspace_id=tenant.workspace_id,
                        meeting_id=meeting_id,
                        revision_id=row.id,
                        sequence=sequence,
                        speaker_key="speaker_1",
                        start_ms=sequence * 1000,
                        end_ms=sequence * 1000 + 900,
                        text=f"turn {sequence}",
                    )
                )
            db.flush()
            return row

        if run == "stale":
            analysed = revision(1, TranscriptSource.ASR, current=False)
            current = revision(2, TranscriptSource.CORRECTION, current=True)
        else:
            current = analysed = revision(1, revision_source, current=True)
        segment_id = db.scalar(
            select(TranscriptSegment.id).where(TranscriptSegment.revision_id == current.id)
        )

        analysis = None
        if run is not None:
            analysis = AnalysisRun(
                workspace_id=tenant.workspace_id,
                meeting_id=meeting_id,
                transcript_revision_id=analysed.id,
                state=run_state,
                is_mock=run_mock,
                is_current=True,
            )
            db.add(analysis)
        for _ in range(failed_recomputes):
            db.add(
                AnalysisRun(
                    workspace_id=tenant.workspace_id,
                    meeting_id=meeting_id,
                    transcript_revision_id=current.id,
                    state=StageState.FAILED,
                    reason_code="provider_failed",
                )
            )
        other_run = AnalysisRun(
            workspace_id=tenant.workspace_id,
            meeting_id=meeting_id,
            transcript_revision_id=current.id,
            state=StageState.COMPLETED,
        )
        db.add(other_run)
        db.flush()
        run_id = analysis.id if analysis is not None else None

        def span(finding_type: str, *, run_of: str | None, index=None, finding_id=None) -> str:
            if segment_id is None:  # no segment to cite: the citation names nothing
                return str(uuid.uuid4())
            row = EvidenceSpan(
                workspace_id=tenant.workspace_id,
                meeting_id=meeting_id,
                analysis_run_id=run_of,
                transcript_revision_id=current.id,
                segment_id=segment_id,
                speaker_key="speaker_1",
                finding_type=finding_type,
                finding_id=finding_id,
                finding_index=index,
                text_start=0,
                text_end=4,
                start_ms=0,
                end_ms=900,
                evidence_type="turn_reference" if run_of else "human",
            )
            db.add(row)
            db.flush()
            return row.id

        if summary:
            points = []
            for index, point in enumerate(key_points or []):
                point = dict(point)
                if isinstance(point.get("evidence_ids"), list):
                    ids = []
                    for value in point["evidence_ids"]:
                        if value == "@human":
                            ids.append(span("insight", run_of=None, index=index))
                        elif value == "@other_run":
                            ids.append(span("insight", run_of=other_run.id, index=index))
                        elif isinstance(value, str) and value.startswith("@"):
                            ids.append(span(value[1:], run_of=run_id, index=index))
                        else:
                            ids.append(value)
                    point["evidence_ids"] = ids
                points.append(point)
            db.add(
                MeetingSummary(
                    workspace_id=tenant.workspace_id,
                    meeting_id=meeting_id,
                    overview="An overview.",
                    provider="test",
                    model="test",
                    schema_version=schema_version,
                    analysis_run_id=run_id,
                    evidence_status=summary_status,
                    key_points=points,
                    decisions=[],
                    topics=[],
                )
            )
            if overview_cited and run_id is not None:
                span("summary", run_of=run_id)
        for action in actions or []:
            item = ActionItem(
                workspace_id=tenant.workspace_id,
                meeting_id=meeting_id,
                description="Follow up.",
                created_by=action.get("by", "ai"),
                analysis_run_id=run_id,
                evidence_status=action.get("status", "sufficient"),
            )
            db.add(item)
            db.flush()
            if action.get("cited", True):
                span("action_item", run_of=run_id, finding_id=item.id)
        if asr_state is not None:
            _seed_job(db, tenant, meeting_id, asr_state=asr_state)
        db.commit()
        return meeting_id


def counters(tenant: Tenant, **params) -> dict[str, Any]:
    response = tenant.get(COVERAGE, **(params or WINDOW))
    assert response.status_code == 200, response.text
    return response.json()["counters"]


def assert_reconciles(body: dict[str, Any]) -> None:
    ingestion, processing = body["ingestion"], body["processing"]
    evaluation, review = body["evaluation"], body["review"]
    for name, counter in body.items():
        expected = COUNTER_KEYS | ({"rejected_reasons"} if name == "ingestion" else set())
        assert set(counter) == expected, name
    assert list(ingestion["buckets"]) == list(INGESTION_BUCKETS)
    assert list(processing["buckets"]) == list(PROCESSING_BUCKETS)
    assert list(evaluation["buckets"]) == list(EVALUATION_BUCKETS)
    assert (
        ingestion["buckets"]["accepted"] + ingestion["buckets"]["rejected"]
        == (ingestion["denominator"])
    )
    assert processing["denominator"] == ingestion["numerator"]
    assert evaluation["denominator"] == processing["numerator"]
    assert review["denominator"] == evaluation["numerator"]
    for counter in (ingestion, processing, evaluation):
        assert sum(counter["buckets"].values()) == counter["denominator"]
        if counter["denominator"]:
            assert counter["status"] == "computed" and counter["reason"] is None
            assert counter["ratio"] == round(counter["numerator"] / counter["denominator"], 4)
        else:
            assert counter["status"] == "not_computable"
            assert counter["reason"] == "empty_denominator"
            assert counter["ratio"] is None and counter["numerator"] == 0
    assert review["numerator"] is None and review["ratio"] is None
    assert review["status"] == "not_computable"
    assert review["reason"] == "review_records_unavailable"
    assert review["buckets"] == {}


def report(tenant: Tenant, meeting_id: str) -> dict[str, Any]:
    response = tenant.get(f"/api/v1/meetings/{meeting_id}/report")
    assert response.status_code == 200, response.text
    return response.json()


def evaluation_buckets(tenant: Tenant, settings) -> dict[str, str]:
    """meeting id -> evaluation bucket, from the service itself."""
    with inspection_session_factory()() as db:
        coverage = compute_coverage(
            db,
            workspace_id=tenant.workspace_id,
            window=parse_window(WINDOW["from"], WINDOW["to"]),
            recompute_max_attempts=settings.recompute_max_attempts,
        )
    return {meeting.meeting_id: meeting.bucket for meeting in coverage.evaluated}


def unsupported_from_rows(tenant: Tenant, meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        actions = db.scalars(
            select(ActionItem).where(
                ActionItem.meeting_id == meeting_id, ActionItem.created_by == "ai"
            )
        ).all()
        spans = db.scalars(select(EvidenceSpan).where(EvidenceSpan.meeting_id == meeting_id)).all()
        return unsupported_finding_count(summary=summary, actions=actions, spans=spans)


# --- window --------------------------------------------------------------------


def test_window_defaults_and_refusals():
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    window = parse_window(now=now)
    assert (window.start, window.end) == (now - timedelta(days=30), now)
    window = parse_window(None, "2026-05-01T12:00:00+03:30")
    assert window.end == datetime(2026, 5, 1, 8, 30, tzinfo=UTC)
    assert window.start == window.end - timedelta(days=30)
    window = parse_window("2026-01-01T00:00:00Z", "2027-01-02T00:00:00Z")
    assert window.end - window.start == timedelta(days=366)

    for raw_from, raw_to in (
        ("2026-01-01T00:00:00", "2026-01-02T00:00:00Z"),
        ("2026-01-01T00:00:00Z", "2026-01-02"),
        ("2026-01-02T00:00:00Z", "2026-01-02T00:00:00Z"),
        ("2026-01-03T00:00:00Z", "2026-01-02T00:00:00Z"),
        ("2026-01-01T00:00:00Z", "2027-01-02T00:00:01Z"),
        ("2026-01-01T00:00:00Z", "2027-01-03T00:00:00Z"),
        ("yesterday", None),
        (5, None),
    ):
        with pytest.raises(CoverageWindowError):
            parse_window(raw_from, raw_to, now=now)


def test_window_errors_are_422_and_366_days_is_200(client, email_outbox):
    tenant = Tenant(client, email_outbox, "coverage-window@example.com")
    for params in (
        {"from": "2026-01-01T00:00:00", "to": "2026-01-02T00:00:00Z"},
        {"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        {"from": "2026-01-01T00:00:00Z", "to": "2026-01-01T00:00:00Z"},
        {"from": "2026-01-01T00:00:00Z", "to": "2027-01-03T00:00:00Z"},
        {"from": "not-a-date"},
        {"to": "2026-01-02T00:00:00"},
    ):
        refused = tenant.get(COVERAGE, **params)
        assert refused.status_code == 422, (params, refused.text)
        assert refused.json()["error"]["code"] == "coverage_window_invalid", params

    allowed = tenant.get(
        COVERAGE, **{"from": "2026-01-01T00:00:00+00:00", "to": "2027-01-02T00:00:00+00:00"}
    )
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert set(body) == {"coverage_version", "window", "counters"}
    assert body["coverage_version"] == "coverage/1"
    assert body["window"]["basis"] == "ingestion_received_at"
    assert datetime.fromisoformat(body["window"]["from"]) == datetime(2026, 1, 1, tzinfo=UTC)
    assert datetime.fromisoformat(body["window"]["to"]) == datetime(2027, 1, 2, tzinfo=UTC)
    assert set(body["counters"]) == {"ingestion", "processing", "evaluation", "review"}

    default = tenant.get(COVERAGE)
    assert default.status_code == 200, default.text
    window = default.json()["window"]
    span = datetime.fromisoformat(window["to"]) - datetime.fromisoformat(window["from"])
    assert span == timedelta(days=30)


def test_the_window_is_half_open(client, email_outbox):
    tenant = Tenant(client, email_outbox, "coverage-edges@example.com")
    start, end = BASE, BASE + timedelta(days=1)
    seed_call(tenant, received_at=start)
    seed_call(tenant, received_at=end)  # == to: outside
    seed_call(tenant, received_at=start - timedelta(microseconds=1))
    body = counters(tenant, **{"from": start.isoformat(), "to": end.isoformat()})
    assert body["ingestion"]["denominator"] == 1
    later = counters(
        tenant, **{"from": end.isoformat(), "to": (end + timedelta(days=1)).isoformat()}
    )
    assert later["ingestion"]["denominator"] == 1


# --- the counters --------------------------------------------------------------


def test_an_empty_window_is_not_computable_everywhere(client, email_outbox):
    tenant = Tenant(client, email_outbox, "coverage-empty@example.com")
    body = counters(tenant)
    assert_reconciles(body)
    for name in ("ingestion", "processing", "evaluation"):
        assert body[name]["denominator"] == 0
    assert body["review"]["denominator"] == 0
    assert body["ingestion"]["rejected_reasons"] == {}


def test_every_bucket_is_counted_separately_and_reconciles(client, email_outbox, settings):
    tenant = Tenant(client, email_outbox, "coverage-buckets@example.com")
    seed_call(tenant, ledger="rejected", reason="unsupported_media")
    seed_call(tenant, ledger="rejected", reason="unsupported_media")
    seed_call(tenant, ledger="rejected", reason="empty_media")
    seed_call(tenant, with_meeting=False)  # the meeting row is gone
    seed_call(tenant, deleted=True)
    seed_call(tenant, status=MeetingStatus.TRANSCRIBING, segments=None, run=None, summary=False)
    seed_call(tenant, status=MeetingStatus.FAILED)
    seed_call(tenant, status=MeetingStatus.CANCELLED)
    seed_call(tenant, status=MeetingStatus.COMPLETED, segments=None, run=None, summary=False)
    seed_call(tenant, status=MeetingStatus.PARTIAL, segments=0, run=None, summary=False)
    complete = seed_call(tenant)
    seed_call(tenant, asr_state=StageState.PARTIAL)
    seed_call(tenant, run=None, summary=False)
    seed_call(tenant, status=MeetingStatus.PARTIAL)
    seed_call(tenant, run="stale")
    seed_call(tenant, run_mock=True)
    seed_call(tenant, actions=[{"cited": False}])

    body = counters(tenant)
    assert_reconciles(body)
    ingestion, processing, evaluation = body["ingestion"], body["processing"], body["evaluation"]
    assert ingestion["buckets"] == {"rejected": 3, "accepted": 14}
    assert (ingestion["numerator"], ingestion["denominator"]) == (14, 17)
    assert ingestion["ratio"] == round(14 / 17, 4)
    assert ingestion["rejected_reasons"] == {"empty_media": 1, "unsupported_media": 2}
    assert processing["buckets"] == {
        "deleted": 2,
        "in_progress": 1,
        "failed": 2,
        "transcribed": 7,
        "no_transcript": 2,
    }
    assert evaluation["buckets"] == {
        "fully_evaluated": 1,
        "transcript_partial": 1,
        "analysis_unavailable": 1,
        "analysis_partial": 1,
        "analysis_stale": 1,
        "analysis_recompute_failed": 0,
        "mock_analysis": 1,
        "unsupported_findings": 1,
    }
    assert body["review"]["denominator"] == 1
    assert evaluation_buckets(tenant, settings)[complete] == "fully_evaluated"
    labels = {name: (c["numerator_label"], c["denominator_label"]) for name, c in body.items()}
    assert labels == {
        "ingestion": ("valid", "ingested"),
        "processing": ("transcribed", "valid"),
        "evaluation": ("fully_evaluated", "transcribed"),
        "review": ("reviewed", "fully_evaluated"),
    }
    for counter in body.values():
        assert counter["definition"] and "—" not in counter["definition"]


def test_failed_calls_never_inflate_evaluation_coverage(client, email_outbox, settings):
    """A FAILED or CANCELLED meeting with a transcript and a completed analysis
    is not transcribed and not fully evaluated; the same rows COMPLETED are."""
    tenant = Tenant(client, email_outbox, "coverage-failed@example.com")
    failed = seed_call(tenant, status=MeetingStatus.FAILED)
    cancelled = seed_call(tenant, status=MeetingStatus.CANCELLED)
    body = counters(tenant)
    assert_reconciles(body)
    assert body["processing"]["buckets"]["failed"] == 2
    assert body["processing"]["numerator"] == 0
    assert body["evaluation"]["denominator"] == 0
    assert body["review"]["denominator"] == 0
    assert failed not in evaluation_buckets(tenant, settings)
    assert cancelled not in evaluation_buckets(tenant, settings)

    completed = seed_call(tenant, status=MeetingStatus.COMPLETED)
    body = counters(tenant)
    assert body["processing"]["numerator"] == 1
    assert body["evaluation"]["numerator"] == 1
    assert evaluation_buckets(tenant, settings) == {completed: "fully_evaluated"}


def test_partial_meetings_follow_their_transcript(client, email_outbox, settings):
    tenant = Tenant(client, email_outbox, "coverage-partial@example.com")
    empty = seed_call(tenant, status=MeetingStatus.PARTIAL, segments=0)
    with_text = seed_call(tenant, status=MeetingStatus.PARTIAL, segments=2)
    body = counters(tenant)
    assert body["processing"]["buckets"]["no_transcript"] == 1
    assert body["processing"]["buckets"]["transcribed"] == 1
    buckets = evaluation_buckets(tenant, settings)
    assert empty not in buckets
    assert buckets[with_text] == "analysis_partial"


def test_a_recompute_that_failed_stays_stale_first(client, email_outbox, settings):
    tenant = Tenant(client, email_outbox, "coverage-recompute@example.com")
    meeting_id = seed_call(tenant, run="stale", failed_recomputes=settings.recompute_max_attempts)
    body = report(tenant, meeting_id)
    assert "analysis_recompute_failed" in body["completeness"]["notices"]
    assert evaluation_buckets(tenant, settings)[meeting_id] == "analysis_stale"


def test_another_workspace_is_never_counted(client, email_outbox):
    other = Tenant(client, email_outbox, "coverage-other@example.com")
    for _ in range(3):
        seed_call(other)
    seed_call(other, ledger="rejected")
    tenant = Tenant(client, email_outbox, "coverage-mine@example.com")
    body = counters(tenant)
    assert body["ingestion"]["denominator"] == 0

    seed_call(tenant)
    body = counters(tenant)
    assert body["ingestion"]["denominator"] == 1
    assert body["evaluation"]["numerator"] == 1
    assert counters(other)["ingestion"]["denominator"] == 4


# --- parity with the call report -----------------------------------------------

STATED = {"section": "summary_field", "status": "stated", "evidence_status": "sufficient"}
PARITY_CASES: dict[str, dict[str, Any]] = {
    "complete": {},
    "complete_partial_evidence": {"summary_status": "partial"},
    "overview_uncited": {"overview_cited": False},
    "overview_insufficient": {"summary_status": "insufficient"},
    "no_summary": {"summary": False},
    "insight_cited": {
        "key_points": [
            {"description": "i", "evidence_status": "sufficient", "evidence_ids": ["@insight"]}
        ]
    },
    "insight_human_span": {
        "key_points": [{"evidence_status": "partial", "evidence_ids": ["@human"]}]
    },
    "insight_other_run_span": {
        "key_points": [{"evidence_status": "sufficient", "evidence_ids": ["@other_run"]}]
    },
    "insight_wrong_span_type": {
        "key_points": [{"evidence_status": "sufficient", "evidence_ids": ["@summary"]}]
    },
    "insight_unknown_id": {
        "key_points": [{"evidence_status": "sufficient", "evidence_ids": ["not-a-span"]}]
    },
    "insight_no_status": {"key_points": [{"evidence_ids": ["@insight"]}]},
    "insight_insufficient_cited": {
        "key_points": [{"evidence_status": "insufficient", "evidence_ids": ["@insight"]}]
    },
    "legacy_claim_is_skipped": {
        "key_points": [{**STATED, "category": "purpose", "evidence_ids": []}]
    },
    "structured_field_cited": {
        "schema_version": 2,
        "key_points": [{**STATED, "category": "purpose", "evidence_ids": ["@summary"]}],
    },
    "structured_field_uncited": {
        "schema_version": 2,
        "key_points": [{**STATED, "category": "resolution", "evidence_ids": ["@insight"]}],
    },
    "structured_field_not_stated": {
        "schema_version": 2,
        "key_points": [{**STATED, "category": "purpose", "status": "not_stated"}],
    },
    "structured_field_duplicate": {
        "schema_version": 2,
        "key_points": [
            {**STATED, "category": "purpose", "evidence_ids": ["@summary"]},
            {**STATED, "category": "purpose", "evidence_ids": []},
        ],
    },
    "structured_field_unknown_name": {
        "schema_version": 2,
        "key_points": [{**STATED, "category": "mood", "evidence_ids": []}],
    },
    "structured_decision_uncited": {
        "schema_version": 2,
        "key_points": [
            {"section": "decision", "evidence_status": "sufficient", "basis": "explicit"}
        ],
    },
    "structured_moment_cited": {
        "schema_version": 2,
        "key_points": [
            {
                "section": "key_moment",
                "category": "objection",
                "evidence_status": "sufficient",
                "evidence_ids": ["@insight"],
            }
        ],
    },
    "structured_unknown_section": {
        "schema_version": 2,
        "key_points": [{"section": "other", "evidence_status": "insufficient"}],
    },
    "ai_action_cited": {"actions": [{"cited": True}]},
    "ai_action_uncited": {"actions": [{"cited": False}]},
    "ai_action_insufficient": {"actions": [{"status": "insufficient"}]},
    "person_action_uncited": {"actions": [{"by": "user", "cited": False}]},
    "asr_partial": {"asr_state": StageState.PARTIAL},
    "asr_failed_stage": {"asr_state": StageState.FAILED},
    "analysis_failed": {"run_state": StageState.FAILED},
    "analysis_partial_run": {"run_state": StageState.PARTIAL},
    "analysis_missing": {"run": None, "summary": False},
    "stale": {"run": "stale"},
    "mock": {"run_mock": True},
    "partial_status": {"status": MeetingStatus.PARTIAL},
    "no_diarization": {"diarization": None},
}


def test_fully_evaluated_and_buckets_match_the_call_report(client, email_outbox, settings):
    tenant = Tenant(client, email_outbox, "coverage-parity@example.com")
    seeded = {name: seed_call(tenant, **case) for name, case in PARITY_CASES.items()}
    buckets = evaluation_buckets(tenant, settings)
    assert set(buckets) == set(seeded.values())
    seen = set()
    for name, meeting_id in seeded.items():
        body = report(tenant, meeting_id)
        completeness = body["completeness"]
        bucket = buckets[meeting_id]
        assert (bucket == "fully_evaluated") is completeness["is_complete"], name
        if not completeness["is_complete"]:
            assert bucket == completeness["notices"][0], name
        assert unsupported_from_rows(tenant, meeting_id) == body["qa"]["counts"]["unsupported"], (
            name
        )
        seen.add(bucket)
    # the cases reach every reachable bucket (recompute_failed always follows stale)
    assert seen == set(EVALUATION_BUCKETS) - {"analysis_recompute_failed"}

    body = counters(tenant)
    assert_reconciles(body)
    assert body["evaluation"]["numerator"] == sum(
        1 for bucket in buckets.values() if bucket == "fully_evaluated"
    )


# --- cost ----------------------------------------------------------------------


def test_the_statement_count_does_not_grow_with_the_meetings(client, email_outbox):
    tenant = Tenant(client, email_outbox, "coverage-cost@example.com")
    small_day, large_day = BASE, BASE + timedelta(days=2)
    for _ in range(2):
        seed_call(
            tenant,
            received_at=small_day,
            actions=[{"cited": True}],
            key_points=[{"evidence_status": "sufficient", "evidence_ids": ["@insight"]}],
            asr_state=StageState.COMPLETED,
        )
    for _ in range(12):
        seed_call(
            tenant,
            received_at=large_day,
            actions=[{"cited": True}],
            key_points=[{"evidence_status": "sufficient", "evidence_ids": ["@insight"]}],
            asr_state=StageState.COMPLETED,
        )

    def statements(day: datetime) -> tuple[int, dict]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            seen.append(statement)

        engine = get_engine()
        event.listen(engine, "before_cursor_execute", record)
        try:
            response = tenant.get(
                COVERAGE, **{"from": day.isoformat(), "to": (day + timedelta(days=1)).isoformat()}
            )
        finally:
            event.remove(engine, "before_cursor_execute", record)
        assert response.status_code == 200, response.text
        return len(seen), response.json()["counters"]

    small_count, small = statements(small_day)
    large_count, large = statements(large_day)
    assert small["evaluation"]["numerator"] == 2
    assert large["evaluation"]["numerator"] == 12
    assert small_count == large_count


# --- authorization -------------------------------------------------------------


def test_only_owner_and_admin_read_coverage(client, email_outbox, monkeypatch):
    owner, personas, _ = _workspace_with_roles(
        client,
        email_outbox,
        monkeypatch,
        roles={"admin": "admin", "sup": "member", "agent": "member", "viewer": "viewer"},
    )
    team = owner.post("/api/v1/teams", json={"name": "Coverage team"}).json()["id"]
    for name, role in (("sup", "supervisor"), ("agent", "agent")):
        assigned = owner.post(
            f"/api/v1/teams/{team}/assignments",
            json={"user_id": personas[name].user_id, "role": role},
        )
        assert assigned.status_code == 201, assigned.text

    for persona in (owner, personas["admin"]):
        allowed = persona.get(COVERAGE, params=WINDOW)
        assert allowed.status_code == 200, allowed.text
    for name in ("sup", "agent", "viewer"):
        denied = personas[name].get(COVERAGE, params=WINDOW)
        assert denied.status_code == 403, (name, denied.text)
        assert denied.json()["error"]["code"] == "permission_denied"

    client.cookies.clear()
    anonymous = client.get(COVERAGE, headers={"X-Workspace-ID": owner.workspace_id})
    assert anonymous.status_code == 401, anonymous.text


def test_api_keys_cannot_read_coverage(client, email_outbox):
    tenant = Tenant(client, email_outbox, "coverage-keys@example.com")
    minted = _mint(client, tenant.headers, tenant.cookies, scopes=["meeting:read", "audit:read"])
    assert minted.status_code == 201, minted.text
    denied = client.get(
        COVERAGE,
        params=WINDOW,
        headers=_bearer(minted.json()["key"], **{"X-Workspace-ID": tenant.workspace_id}),
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "api_key_scope_denied"
    refused = _mint(client, tenant.headers, tenant.cookies, scopes=["workspace:manage"])
    assert refused.status_code == 422, refused.text


# --- turn 2: dates Python parses but cannot place in UTC ------------------------

OVERFLOW_WINDOWS = [
    {"to": "0001-01-01T00:00:00+00:00"},  # cannot step back 30 days
    {"from": "0001-01-01T00:00:00+14:00", "to": "0001-01-02T00:00:00+00:00"},  # before year 1
    {"to": "9999-12-31T23:00:00-05:00"},  # after year 9999 in UTC
]
EDGE_CONTROL = {"from": "0001-01-01T00:00:00+00:00", "to": "0001-01-31T00:00:00+00:00"}


def test_out_of_range_dates_are_window_errors_not_crashes():
    for params in OVERFLOW_WINDOWS:
        with pytest.raises(CoverageWindowError):
            parse_window(params.get("from"), params.get("to"))
    window = parse_window(EDGE_CONTROL["from"], EDGE_CONTROL["to"])
    assert window.start == datetime(1, 1, 1, tzinfo=UTC)


def test_out_of_range_dates_are_422_on_coverage(client, email_outbox):
    tenant = Tenant(client, email_outbox, "coverage-overflow@example.com")
    for params in OVERFLOW_WINDOWS:
        refused = tenant.get(COVERAGE, **params)
        assert refused.status_code == 422, (params, refused.text)
        assert refused.json()["error"]["code"] == "coverage_window_invalid", params
    allowed = tenant.get(COVERAGE, **EDGE_CONTROL)
    assert allowed.status_code == 200, allowed.text
