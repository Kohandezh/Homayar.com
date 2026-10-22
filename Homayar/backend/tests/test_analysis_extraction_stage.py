"""HOM-53 part 2: the analysis stage judges, cites and stores the extraction.

After the analysis, ``persist_analysis`` judges the provider's extraction with the
pinned taxonomy and policy, against the turns the model received.  It cites every
kept value with an ``extraction`` evidence span and stores one immutable
``analysis_extractions`` row for the run.  The full job and the recompute share that
code.  A legacy result (no extraction) stores no row.  A bad extraction is stored as
invalid and the analysis still completes.  The completion audit events say how it
went, without a label or a text.  Mock providers only; never the network.
"""

from __future__ import annotations

import base64
import json
import logging
import os

import httpx
import pytest
from sqlalchemy import select

from backend.analysis_taxonomy import (
    DIMENSIONS,
    POLICY_V1,
    POLICY_VERSION,
    VALIDATOR_VERSION,
    evaluate_extraction,
    load_taxonomy,
    policy_digest,
)
from backend.database.saas_models import (
    ActionItem,
    AnalysisExtraction,
    AnalysisRun,
    AuditEvent,
    EvidenceSpan,
    JobState,
    Meeting,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    StageState,
    TranscriptSegment,
)
from backend.jobs import processor, recompute
from backend.jobs.processor import process_job
from backend.providers.base import ASRProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.schemas import ASRResult, ASRSegment
from backend.services.audit_event_types import AuditEventType
from backend.tests._database import inspection_session_factory
from backend.tests.test_analysis_recompute import (
    CORRECTED_TEXT,
    ORIGINAL_TEXT,
    _correct,
    _FailingIntelligence,
)
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_processor import SilentASRProvider, SingleSpeakerDiarizationProvider
from backend.tests.test_privacy_routing import SEGMENTS, _no_raw, _SegmentedASR, _set_policy
from backend.tests.test_stage_checkpoints import broker  # noqa: F401

TAXONOMY = load_taxonomy("conversation", 1)
PHONE = "۰۹۱۲۳۴۵۶۷۸۹"
TUESDAY = "سه‌شنبه"
TURNS = (
    f"سلام، می‌خواهم سرویسم را لغو کنم. شماره من {PHONE} است.",
    f"فاکتور آخر را بررسی می‌کنم و {TUESDAY} با شما تماس می‌گیرم.",
)
NOT_STATED = {"status": "not_stated", "values": []}


@pytest.fixture
def settings(settings):
    # Mapping keys for the redacted runs; mock providers for the recompute, which
    # builds its providers from the settings like the worker.
    key = base64.b64encode(os.urandom(32)).decode()
    return settings.model_copy(
        update={
            "pii_mapping_keys": f"k1:{key}",
            "pii_mapping_active_key_id": "k1",
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
            "recompute_on_correction": False,
        }
    )


# --- extraction builders --------------------------------------------------------------


def _value(label: str, turns, confidence, *, basis: str = "explicit", text: str | None = None):
    value = {
        "label": label,
        "basis": basis,
        "source_turn_indexes": list(turns),
        "confidence": confidence,
    }
    if text is not None:
        value["text"] = text
    return value


def _stated(*values: dict) -> dict:
    return {"status": "stated", "values": list(values)}


def _extraction(**fields: dict) -> dict:
    return {name: fields.get(name, dict(NOT_STATED)) for name in DIMENSIONS}


# Kept, in citation order: cancel_service (0, critical), invoice (1), contract (2),
# phone (3, critical), date (4), follow_up_scheduled (5).  pricing is withheld.
MIXED = _extraction(
    intent=_stated(_value("cancel_service", [0], 0.9)),
    topic=_stated(
        _value("invoice", [1], 0.8),
        _value("pricing", [1], 0.3),
        _value("contract", [0], 0.75, basis="inferred"),
    ),
    entity=_stated(
        _value("phone", [0], 0.95, text=PHONE),
        _value("date", [1], 0.9, text=TUESDAY),
    ),
    outcome=_stated(_value("follow_up_scheduled", [1], 0.8)),
)


# --- doubles --------------------------------------------------------------------------


class _TurnsASR(ASRProvider):
    """The two turns of ``TURNS`` as timed segments."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="extraction_asr", model="v1")

    async def transcribe(self, request) -> ASRResult:
        return ASRResult(
            provider="extraction_asr",
            model="v1",
            language="fa",
            text=" ".join(TURNS),
            segments=[
                ASRSegment(index=i, text=text, start_seconds=i * 5, end_seconds=i * 5 + 4)
                for i, text in enumerate(TURNS)
            ],
            duration_seconds=10,
            is_mock=True,
        )


class _Extracting(MockIntelligenceProvider):
    """The mock analysis with the extraction ``build`` makes from the request
    (``None``: a legacy result).  Records every request it answers."""

    def __init__(self, build) -> None:
        super().__init__(allow_mock=True)
        self._build = build
        self.requests = []

    async def analyze(self, request):
        self.requests.append(request)
        result = await super().analyze(request)
        return result.model_copy(update={"extraction": self._build(request)})


def _fixed(extraction: dict | None):
    return _Extracting(lambda _request: extraction)


def _phone_quote(text: str) -> _Extracting:
    """An extraction whose only value is a phone that quotes ``text`` from turn 0."""
    return _fixed(_extraction(entity=_stated(_value("phone", [0], 0.9, text=text))))


# --- helpers --------------------------------------------------------------------------


def _process(
    client,
    email_outbox,
    monkeypatch,
    settings,
    intelligence,
    *,
    label: str,
    asr=None,
    redacted: bool = False,
    expected_status: str = "completed",
) -> tuple[dict, str]:
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, f"extraction-{label}")
    if redacted:
        assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    accepted = _upload(client, tenant, key=f"extraction-{label}-0001")
    assert accepted.status_code == 202, accepted.text
    result = process_job(
        accepted.json()["job"]["id"],
        worker_id=f"extraction-{label}",
        settings=settings,
        providers=ProviderBundle(
            asr=asr or _TurnsASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=intelligence,
        ),
    )
    assert result["status"] == expected_status, result
    return tenant, accepted.json()["meeting"]["id"]


def _rows(meeting_id: str) -> list[AnalysisExtraction]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AnalysisExtraction)
            .where(AnalysisExtraction.meeting_id == meeting_id)
            .order_by(AnalysisExtraction.created_at.asc(), AnalysisExtraction.id.asc())
        ).all()
        return list(rows)


def _runs(meeting_id: str) -> list[AnalysisRun]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.meeting_id == meeting_id)
                .order_by(AnalysisRun.created_at.asc(), AnalysisRun.id.asc())
            )
        )


def _current_run(meeting_id: str) -> AnalysisRun:
    (run,) = [run for run in _runs(meeting_id) if run.is_current]
    return run


def _citations(run_id: str) -> dict[int, list[tuple[int, float | None, str]]]:
    """finding_index -> [(segment sequence, span confidence, revision id)]."""
    with inspection_session_factory()() as db:
        rows = db.execute(
            select(
                EvidenceSpan.finding_index,
                TranscriptSegment.sequence,
                EvidenceSpan.confidence,
                EvidenceSpan.transcript_revision_id,
            )
            .join(TranscriptSegment, TranscriptSegment.id == EvidenceSpan.segment_id)
            .where(
                EvidenceSpan.analysis_run_id == run_id, EvidenceSpan.finding_type == "extraction"
            )
            .order_by(EvidenceSpan.finding_index.asc(), TranscriptSegment.sequence.asc())
        ).all()
    cited: dict[int, list[tuple[int, float | None, str]]] = {}
    for index, sequence, confidence, revision_id in rows:
        cited.setdefault(index, []).append((sequence, confidence, revision_id))
    return cited


def _audit(meeting_id: str, event_type: AuditEventType) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.target_id == meeting_id, AuditEvent.event_type == event_type.value
                )
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def _extraction_audit(event: AuditEvent) -> tuple[object, object, object]:
    details = event.details
    return (
        details["extraction_state"],
        details["extraction_review_count"],
        details["extraction_reason"],
    )


def _expected(extraction: dict, request) -> dict:
    turns = [turn.text for turn in request.turns]
    return evaluate_extraction(extraction, TAXONOMY, turns, POLICY_V1).to_json()


# --- the full job ---------------------------------------------------------------------


def test_a_mock_analysis_stores_one_accepted_extraction(
    client, email_outbox, monkeypatch, settings
):
    _, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        MockIntelligenceProvider(allow_mock=True),
        label="mock",
    )
    run = _current_run(meeting_id)
    (row,) = _rows(meeting_id)
    assert (row.analysis_run_id, row.meeting_id, row.workspace_id) == (
        run.id,
        run.meeting_id,
        run.workspace_id,
    )
    assert (row.taxonomy_id, row.taxonomy_version, row.taxonomy_digest) == (
        "conversation",
        1,
        TAXONOMY.digest,
    )
    assert row.validator_version == VALIDATOR_VERSION
    assert (row.policy_version, row.policy_digest) == (POLICY_VERSION, policy_digest(POLICY_V1))
    assert (row.valid, row.needs_review, row.review_count) == (True, False, 0)
    assert row.created_at is not None
    assert row.result == evaluate_extraction(_extraction(), TAXONOMY, list(TURNS)).to_json()
    assert _citations(run.id) == {}  # nothing stated, nothing cited
    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("accepted", 0, None)


def test_a_legacy_result_stores_no_extraction(client, email_outbox, monkeypatch, settings):
    _, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(None), label="legacy"
    )
    run = _current_run(meeting_id)
    assert run.state == StageState.COMPLETED
    assert _rows(meeting_id) == []
    assert _citations(run.id) == {}
    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("unavailable", 0, "not_requested")
    with inspection_session_factory()() as db:  # the rest of the analysis is stored
        assert db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))


def test_kept_values_are_cited_in_order_and_withheld_values_are_not(
    client, email_outbox, monkeypatch, settings
):
    intelligence = _fixed(MIXED)
    _, meeting_id = _process(client, email_outbox, monkeypatch, settings, intelligence, label="mix")
    run = _current_run(meeting_id)
    (row,) = _rows(meeting_id)
    (request,) = intelligence.requests
    assert row.result == _expected(MIXED, request)  # stored as the policy returned it
    assert (row.valid, row.needs_review, row.review_count) == (True, True, 2)

    fields = row.result["fields"]
    dispositions = [
        (name, value["label"], value["disposition"])
        for name in DIMENSIONS
        for value in fields[name]["values"]
    ]
    assert dispositions == [
        ("intent", "cancel_service", "review_required"),
        ("topic", "invoice", "auto_accepted"),
        ("topic", "contract", "auto_accepted"),
        ("entity", "phone", "review_required"),
        ("entity", "date", "auto_accepted"),
        ("outcome", "follow_up_scheduled", "auto_accepted"),
    ]
    assert [value["label"] for value in fields["topic"]["withheld"]] == ["pricing"]

    revision = run.transcript_revision_id
    assert _citations(run.id) == {
        0: [(0, 0.9, revision)],
        1: [(1, 0.8, revision)],
        2: [(0, 0.75, revision)],
        3: [(0, 0.95, revision)],
        4: [(1, 0.9, revision)],
        5: [(1, 0.8, revision)],
    }
    # The run counts the extraction's references with the others: the mock summary
    # cites both turns, each kept value one turn.
    assert (run.reference_count, run.rejected_reference_count) == (2 + 6, 0)

    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("review_required", 2, None)
    details = json.dumps(event.details, ensure_ascii=False)
    for secret in (
        PHONE,
        "09123456789",
        TUESDAY,
        "cancel_service",
        "follow_up_scheduled",
        "invoice",
        "contract",
        "pricing",
    ):
        assert secret not in details, secret


@pytest.mark.parametrize(
    ("value", "state", "count"),
    [
        pytest.param(_value("date", [1], 0.9, text=TUESDAY), "accepted", 0, id="non-critical"),
        pytest.param(_value("phone", [0], 0.9, text=PHONE), "review_required", 1, id="critical"),
        pytest.param(_value("phone", [0], 0.2, text=PHONE), "accepted", 0, id="withheld"),
    ],
)
def test_only_a_kept_critical_value_needs_review(
    client, email_outbox, monkeypatch, settings, value, state, count
):
    _, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(_extraction(entity=_stated(value))),
        label=f"critical-{count}-{round(value['confidence'] * 10)}",
    )
    (row,) = _rows(meeting_id)
    assert (row.valid, row.needs_review, row.review_count) == (True, count > 0, count)
    cited = _citations(row.analysis_run_id)
    kept = row.result["fields"]["entity"]["values"]
    assert len(cited) == len(kept)  # a withheld value has no evidence
    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == (state, count, None)


def test_an_invalid_extraction_is_stored_as_invalid_and_the_analysis_completes(
    client, email_outbox, monkeypatch, settings
):
    bad = {
        **_extraction(
            intent=_stated(_value("cancel_service", [0], 0.9)),
            topic={"status": "stated", "values": "invoice"},
        ),
        "estimated_nps": 9,
    }
    _, meeting_id = _process(client, email_outbox, monkeypatch, settings, _fixed(bad), label="bad")
    with inspection_session_factory()() as db:
        assert db.get(Meeting, meeting_id).status == MeetingStatus.COMPLETED
        assert db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
    run = _current_run(meeting_id)
    assert run.state == StageState.COMPLETED
    (row,) = _rows(meeting_id)
    assert row.valid is False
    assert row.result["issues"] == [{"code": "unknown_key", "path": "estimated_nps"}]
    assert row.result["fields"]["topic"]["verdict"] == "rejected"
    # The intent field is still judged on its own: its kept value is cited and counted.
    assert (row.needs_review, row.review_count) == (True, 1)
    assert list(_citations(run.id)) == [0]
    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("invalid", 1, None)


# --- Amendment 1 (SPEC 11): a missing or non-object extraction never fails the analysis --

ABSENT = object()


def _model_output(extraction: object = ABSENT) -> dict:
    """A complete model output for ``TURNS``, citing turn 0; ``ABSENT``: no extraction key."""
    output = {
        "summary": "The customer cancels; a callback follows.",
        "summary_source_turn_indexes": [0],
        "action_items": [
            {
                "description": "Call the customer back",
                "owner": None,
                "due_date": None,
                "priority": "medium",
                "source_turn_indexes": [0],
            }
        ],
        "insights": [
            {
                "category": "risk",
                "description": "The customer may leave",
                "confidence": 0.8,
                "source_turn_indexes": [0],
            }
        ],
        "summary_fields": {
            "purpose": {
                "status": "stated",
                "text": "Cancel the service",
                "basis": "explicit",
                "source_turn_indexes": [0],
            },
            "resolution": {
                "status": "not_stated",
                "text": None,
                "basis": None,
                "source_turn_indexes": [],
            },
            "next_steps": {
                "status": "not_stated",
                "text": None,
                "basis": None,
                "source_turn_indexes": [],
            },
        },
        "decisions": [
            {
                "description": "Cancel the service",
                "basis": "explicit",
                "owner": {"status": "not_stated", "value": None},
                "due_date": {"status": "not_stated", "value": None},
                "source_turn_indexes": [0],
            }
        ],
        "key_moments": [
            {"kind": "complaint", "description": "The customer cancels", "source_turn_indexes": [0]}
        ],
    }
    if extraction is not ABSENT:
        output["extraction"] = extraction
    return output


KEPT = _extraction(intent=_stated(_value("cancel_service", [0], 0.9)))


class _Parsed(MockIntelligenceProvider):
    """The mock's identity, but the answer is ``output`` read by the real
    OpenAI-compatible parser, as a server would send it."""

    def __init__(self, output: dict) -> None:
        super().__init__(allow_mock=True)
        self._content = json.dumps(output, ensure_ascii=False)
        self.requests = []

    async def analyze(self, request):
        self.requests.append(request)
        response = httpx.Response(200, json={"choices": [{"message": {"content": self._content}}]})
        return OpenAICompatibleIntelligenceProvider._parse_result(response, "parsed-llm")


def _without_ids(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key != "evidence_ids"}


def _analysis_shape(meeting_id: str) -> dict:
    """The stored analysis except the extraction and the generated ids."""
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        actions = db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).all()
        spans = db.execute(
            select(
                EvidenceSpan.finding_type,
                EvidenceSpan.finding_index,
                TranscriptSegment.sequence,
                EvidenceSpan.confidence,
            )
            .join(TranscriptSegment, TranscriptSegment.id == EvidenceSpan.segment_id)
            .where(EvidenceSpan.meeting_id == meeting_id, EvidenceSpan.finding_type != "extraction")
        ).all()
        return {
            "overview": summary.overview,
            "evidence_status": summary.evidence_status,
            "schema_version": summary.schema_version,
            "key_points": [_without_ids(point) for point in summary.key_points],
            "decisions": [_without_ids(decision) for decision in summary.decisions],
            "actions": sorted((a.description, a.created_by, a.evidence_status) for a in actions),
            "spans": sorted(spans, key=repr),
        }


def _missing_warnings(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.getMessage().startswith("ANALYSIS_EXTRACTION_MISSING")
    ]


@pytest.mark.parametrize("missing", [ABSENT, None], ids=["key-absent", "value-null"])
def test_an_output_without_the_extraction_keeps_the_rest_of_the_analysis(
    client, email_outbox, monkeypatch, settings, caplog, missing
):
    caplog.set_level(logging.WARNING)
    _, complete = _process(
        client, email_outbox, monkeypatch, settings, _Parsed(_model_output(KEPT)), label="with"
    )
    assert _missing_warnings(caplog) == []
    _, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _Parsed(_model_output(missing)),
        label="without",
    )

    run = _current_run(meeting_id)
    assert run.state == StageState.COMPLETED
    assert _rows(meeting_id) == [] and _citations(run.id) == {}
    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("unavailable", 0, "missing_from_output")
    with inspection_session_factory()() as db:
        assert db.get(Meeting, meeting_id).status == MeetingStatus.COMPLETED
    # One content-free warning: the meeting id and the fixed reason, nothing else.
    assert _missing_warnings(caplog) == [
        f"ANALYSIS_EXTRACTION_MISSING meeting_id={meeting_id} reason=missing_from_output"
    ]

    # Allow-control: the same output with the extraction has one row, reason null, and
    # exactly the same summary, actions, insights, claims and their evidence.
    (row,) = _rows(complete)
    assert (row.valid, row.review_count) == (True, 1)
    (complete_event,) = _audit(complete, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(complete_event) == ("review_required", 1, None)
    shape = _analysis_shape(meeting_id)
    assert shape == _analysis_shape(complete)
    assert len(shape["key_points"]) == 1 + 3 + 1 + 1  # insight, fields, decision, moment
    assert len(shape["actions"]) == 1 and len(shape["spans"]) == 6


def test_a_run_without_an_analysis_stores_no_extraction(
    client, email_outbox, monkeypatch, settings, caplog
):
    """Silent audio: no transcript, so no provider call and no result (SPEC 11.7)."""
    caplog.set_level(logging.WARNING)
    intelligence = _Parsed(_model_output())  # would be a missing extraction if called
    _, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        intelligence,
        label="silent",
        asr=SilentASRProvider(),
        expected_status="partial",
    )
    assert intelligence.requests == []
    assert _rows(meeting_id) == []
    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("unavailable", 0, "not_requested")
    assert _missing_warnings(caplog) == []


def _nested(depth: int) -> list:
    value: list = []
    for _ in range(depth - 1):
        value = [value]
    return value


@pytest.mark.parametrize(
    "value", [[], ["intent"], "not stated", 7, 1.5, True, {}, _nested(32)], ids=repr
)
def test_an_extraction_that_is_not_a_valid_object_is_stored_as_invalid(
    client, email_outbox, monkeypatch, settings, caplog, value
):
    caplog.set_level(logging.WARNING)
    _, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _Parsed(_model_output(value)),
        label=f"shape-{type(value).__name__}-{len(repr(value))}",
    )
    assert _current_run(meeting_id).state == StageState.COMPLETED
    (row,) = _rows(meeting_id)
    assert (row.valid, row.needs_review, row.review_count) == (False, False, 0)
    fields = row.result["fields"]
    assert [fields[name]["verdict"] for name in DIMENSIONS] == ["rejected"] * 4
    if isinstance(value, dict):  # an object without its four fields
        assert row.result["issues"] == []
        assert [fields[name]["issues"][0]["code"] for name in DIMENSIONS] == ["missing_field"] * 4
    else:
        assert row.result["issues"] == [{"code": "invalid_value", "path": ""}]
    (event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("invalid", 0, None)
    assert _missing_warnings(caplog) == []
    with inspection_session_factory()() as db:
        assert db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"estimated_nps": 9}, id="unknown-extra-key"),
        pytest.param({"summary": ABSENT}, id="summary-missing"),
    ],
)
def test_any_other_key_set_still_fails_the_analysis(
    client, email_outbox, monkeypatch, settings, change
):
    output = _model_output(KEPT)
    for key, value in change.items():
        if value is ABSENT:
            del output[key]
        else:
            output[key] = value
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "extraction-refused")
    accepted = _upload(client, tenant, key="extraction-refused-0001")
    result = process_job(
        accepted.json()["job"]["id"],
        worker_id="extraction-refused",
        settings=settings,
        providers=ProviderBundle(
            asr=_TurnsASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=_Parsed(output),
        ),
    )
    assert result["status"] == "failed", result
    assert result["code"] == "intelligence_invalid_response"
    assert _rows(accepted.json()["meeting"]["id"]) == []


def test_a_non_finite_extraction_fails_the_job_without_a_retry(
    client, email_outbox, monkeypatch, settings
):
    """SPEC 11.8: NaN is not plain JSON, so the parser refuses the output; the error is
    permanent, so the job fails at once instead of trying the provider again."""
    extraction = _extraction(intent=_stated(_value("cancel_service", [0], float("nan"))))
    intelligence = _Parsed(_model_output(extraction))
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "extraction-nan")
    accepted = _upload(client, tenant, key="extraction-nan-0001")
    job_id = accepted.json()["job"]["id"]
    result = process_job(
        job_id,
        worker_id="extraction-nan",
        settings=settings,
        providers=ProviderBundle(
            asr=_TurnsASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=intelligence,
        ),
    )
    assert result["status"] == "failed", result  # not retry_scheduled
    assert result["code"] == "intelligence_invalid_response"
    assert len(intelligence.requests) == 1
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert (job.state, job.attempt_count, job.failure_code) == (
            JobState.FAILED,
            1,
            "intelligence_invalid_response",
        )
    assert _rows(accepted.json()["meeting"]["id"]) == []


# --- a redacted run -------------------------------------------------------------------

ORIGINAL_PHONE = "۰۹۱۲ ۳۴۵ ۶۷۸۹"  # as SEGMENTS[0] says it


def test_a_redacted_run_is_judged_against_the_redacted_turns(
    client, email_outbox, monkeypatch, settings
):
    """The model saw placeholders, so an entity is checked against those: quoting the
    original value is rejected, quoting the placeholder is kept.  The stored text is
    the placeholder, never the original value."""
    assert ORIGINAL_PHONE in SEGMENTS[0]
    original = _phone_quote(ORIGINAL_PHONE)
    _, denied = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        original,
        label="red-deny",
        asr=_SegmentedASR(),
        redacted=True,
    )
    (request,) = original.requests
    assert "[PHONE_1]" in request.turns[0].text and ORIGINAL_PHONE not in request.turns[0].text
    (row,) = _rows(denied)
    entity = row.result["fields"]["entity"]
    assert row.valid is False
    assert entity["verdict"] == "rejected" and entity["values"] == []
    assert [issue["code"] for issue in entity["issues"]] == ["entity_text_not_found"]
    assert _no_raw(json.dumps(row.result, ensure_ascii=False))
    (event,) = _audit(denied, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("invalid", 0, None)

    placeholder = _phone_quote("[PHONE_1]")
    _, kept = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        placeholder,
        label="red-allow",
        asr=_SegmentedASR(),
        redacted=True,
    )
    (row,) = _rows(kept)
    entity = row.result["fields"]["entity"]
    assert row.valid is True and entity["verdict"] == "accepted"
    assert [(v["label"], v["text"], v["disposition"]) for v in entity["values"]] == [
        ("phone", "[PHONE_1]", "review_required")
    ]
    assert _no_raw(json.dumps(row.result, ensure_ascii=False))
    run = _current_run(kept)
    assert run.input_view == "redacted"
    # The span cites the analysed revision's segment, whose text is the original.
    assert [sequence for sequence, _c, _r in _citations(run.id)[0]] == [0]
    (event,) = _audit(kept, AuditEventType.MEETING_PROCESSING_COMPLETED)
    assert _extraction_audit(event) == ("review_required", 1, None)


def test_a_reused_analysis_checkpoint_is_judged_against_the_turns_it_was_given(
    client,
    email_outbox,
    monkeypatch,
    settings,
    broker,  # noqa: F811
):
    """A retry reuses the committed analysis; its extraction quotes the placeholder,
    so it is kept only when the stage gets the reused candidate's redacted turns."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "extraction-reuse")
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    accepted = _upload(client, tenant, key="extraction-reuse-0001")
    job_id, meeting_id = accepted.json()["job"]["id"], accepted.json()["meeting"]["id"]
    intelligence = _phone_quote("[PHONE_1]")
    providers = ProviderBundle(
        asr=_SegmentedASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=intelligence,
    )
    original = processor.commit_usage
    calls = {"n": 0}

    def flaky_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash after the checkpoints were committed")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "commit_usage", flaky_commit)
    first = process_job(job_id, worker_id="reuse-1", settings=settings, providers=providers)
    assert first["status"] == "retry_scheduled"
    assert _rows(meeting_id) == []  # the crashed attempt stored nothing

    _make_due(job_id)
    second = process_job(job_id, worker_id="reuse-2", settings=settings, providers=providers)
    assert second["status"] == "completed"
    assert len(intelligence.requests) == 1  # the analysis was reused, not called again
    (row,) = _rows(meeting_id)
    entity = row.result["fields"]["entity"]
    assert entity["verdict"] == "accepted"
    assert [value["text"] for value in entity["values"]] == ["[PHONE_1]"]


# --- the recompute --------------------------------------------------------------------

# Independent of the wording, so the corrected revision gives the same result.
STEADY = _extraction(
    intent=_stated(_value("cancel_service", [0], 0.9)),
    topic=_stated(_value("contract", [0], 0.8, basis="inferred")),
    outcome=_stated(_value("unresolved", [0], 0.7, basis="inferred")),
)


@pytest.fixture
def analysis_provider(monkeypatch):
    """The intelligence adapter every recompute in the test uses (swappable)."""
    holder = {"intelligence": _fixed(STEADY)}

    def bundle(_settings):
        return ProviderBundle(
            asr=MockASRProvider(allow_mock=True, transcript=ORIGINAL_TEXT),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=holder["intelligence"],
        )

    monkeypatch.setattr(recompute, "create_provider_bundle", bundle)
    return holder


def _recompute_ready(client, email_outbox, monkeypatch, settings) -> tuple[str, str, dict]:
    """A meeting analysed by the full job, then corrected (no recompute yet)."""
    tenant, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(STEADY),
        label="recompute",
        asr=MockASRProvider(allow_mock=True, transcript=ORIGINAL_TEXT),
    )
    corrected = _correct(client, tenant["workspace_id"], meeting_id)
    return tenant["workspace_id"], meeting_id, corrected


def test_a_recompute_writes_the_full_jobs_extraction_for_the_corrected_revision(
    client, email_outbox, monkeypatch, settings, analysis_provider
):
    workspace_id, meeting_id, corrected = _recompute_ready(
        client, email_outbox, monkeypatch, settings
    )
    (full_job,) = _rows(meeting_id)
    old_run = _current_run(meeting_id)
    old_citations = _citations(old_run.id)

    outcome = recompute.recompute_meeting_analysis(
        meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "completed"
    run = _current_run(meeting_id)
    assert run.id != old_run.id and run.transcript_revision_id == corrected["id"]
    (request,) = analysis_provider["intelligence"].requests
    assert [turn.text for turn in request.turns] == [CORRECTED_TEXT]

    stored, recomputed = _rows(meeting_id)
    assert stored.id == full_job.id and stored.result == full_job.result  # the old row stays
    assert recomputed.analysis_run_id == run.id
    assert recomputed.result == full_job.result  # same extraction, same judgement
    assert (
        (recomputed.valid, recomputed.needs_review, recomputed.review_count)
        == (
            full_job.valid,
            full_job.needs_review,
            full_job.review_count,
        )
        == (True, True, 1)
    )

    # Same citations, but on the corrected revision and through the new run.
    citations = _citations(run.id)
    assert (
        {index: [(s, c) for s, c, _r in cited] for index, cited in citations.items()}
        == {index: [(s, c) for s, c, _r in cited] for index, cited in old_citations.items()}
        == {0: [(0, 0.9)], 1: [(0, 0.8)], 2: [(0, 0.7)]}
    )
    assert {r for cited in citations.values() for _s, _c, r in cited} == {corrected["id"]}
    assert _citations(old_run.id) == old_citations

    (job_event,) = _audit(meeting_id, AuditEventType.MEETING_PROCESSING_COMPLETED)
    (event,) = _audit(meeting_id, AuditEventType.MEETING_RECOMPUTE_COMPLETED)
    assert _extraction_audit(job_event) == _extraction_audit(event) == ("review_required", 1, None)
    assert CORRECTED_TEXT not in json.dumps(event.details, ensure_ascii=False)


def test_a_recompute_of_a_legacy_result_stores_no_extraction(
    client, email_outbox, monkeypatch, settings, analysis_provider
):
    workspace_id, meeting_id, corrected = _recompute_ready(
        client, email_outbox, monkeypatch, settings
    )
    analysis_provider["intelligence"] = _fixed(None)
    outcome = recompute.recompute_meeting_analysis(
        meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "completed"
    (row,) = _rows(meeting_id)  # only the full job's
    assert row.analysis_run_id != _current_run(meeting_id).id
    (event,) = _audit(meeting_id, AuditEventType.MEETING_RECOMPUTE_COMPLETED)
    assert _extraction_audit(event) == ("unavailable", 0, "not_requested")


def test_a_failed_recompute_stores_no_extraction_and_keeps_the_current_one(
    client, email_outbox, monkeypatch, settings, analysis_provider
):
    workspace_id, meeting_id, corrected = _recompute_ready(
        client, email_outbox, monkeypatch, settings
    )
    (full_job,) = _rows(meeting_id)
    old_run = _current_run(meeting_id)
    analysis_provider["intelligence"] = _FailingIntelligence(allow_mock=True)

    outcome = recompute.recompute_meeting_analysis(
        meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "failed"
    assert _current_run(meeting_id).id == old_run.id
    (failed,) = [run for run in _runs(meeting_id) if run.state == StageState.FAILED]
    (row,) = _rows(meeting_id)
    assert row.id == full_job.id and row.result == full_job.result
    assert row.analysis_run_id == old_run.id != failed.id
    assert _citations(failed.id) == {}


def test_a_recompute_without_the_extraction_keeps_the_rest_of_the_analysis(
    client, email_outbox, monkeypatch, settings, analysis_provider, caplog
):
    workspace_id, meeting_id, corrected = _recompute_ready(
        client, email_outbox, monkeypatch, settings
    )
    (full_job,) = _rows(meeting_id)
    analysis_provider["intelligence"] = _Parsed(_model_output())
    caplog.set_level(logging.WARNING)

    outcome = recompute.recompute_meeting_analysis(
        meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "completed"
    run = _current_run(meeting_id)
    assert run.transcript_revision_id == corrected["id"] and run.state == StageState.COMPLETED
    (row,) = _rows(meeting_id)  # only the full job's
    assert row.id == full_job.id and row.analysis_run_id != run.id
    (event,) = _audit(meeting_id, AuditEventType.MEETING_RECOMPUTE_COMPLETED)
    assert _extraction_audit(event) == ("unavailable", 0, "missing_from_output")
    assert _missing_warnings(caplog) == [
        f"ANALYSIS_EXTRACTION_MISSING meeting_id={meeting_id} reason=missing_from_output"
    ]
    # The rest of the analysis is the new run's.
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        assert summary.analysis_run_id == run.id
        assert summary.overview == "The customer cancels; a callback follows."
