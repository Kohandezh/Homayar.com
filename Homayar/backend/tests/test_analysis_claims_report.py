"""HOM-61 (WP12-T04): structured analysis claims, end to end.

A provider result with summary fields, decisions and key moments goes through
the processor (resolve and cite the turns) and comes out of ``/report``: each
stated claim is supported only with valid evidence, a ``not_stated`` detail is
shown as such with no text and no evidence, and the outcome is ``explicit``
only for a supported decision whose basis is explicit.  ``/summary`` keeps its
shape: the new claims never appear among its key points.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select, true

from backend.database.saas_models import MeetingSummary
from backend.jobs import checkpoints as ckpt
from backend.jobs import processor
from backend.jobs.processor import process_job
from backend.jobs.states import StageName
from backend.privacy.subject_export import build_subject_export, parse_subject_identifier
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockIntelligenceProvider
from backend.providers.schemas import (
    SUMMARY_FIELD_NAMES,
    Decision,
    DecisionDueDate,
    DecisionOwner,
    IntelligenceInsight,
    IntelligenceResult,
    KeyMoment,
    SummaryField,
    SummaryFields,
)
from backend.services.analysis_claims import NO_CLAIMS, StructuredClaims
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_call_report import (
    _assert_refs_resolve,
    _bundle,
    _read,
    _RealASR,
    _RealDiarization,
    _report,
    _result,
)
from backend.tests.test_evidence_spans import _processed
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_leases import _job
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider, _queued_upload
from backend.tests.test_privacy_routing import _no_raw, _SegmentedASR, _set_policy
from backend.tests.test_stage_checkpoints import _bundle as _checkpoint_bundle
from backend.tests.test_stage_checkpoints import _published, broker  # noqa: F401

NOT_STATED = SummaryField(status="not_stated")


def _stated(text: str, turns, basis: str = "explicit") -> SummaryField:
    return SummaryField(status="stated", text=text, basis=basis, source_turn_indexes=list(turns))


def _fields(**overrides: SummaryField) -> SummaryFields:
    return SummaryFields(**{name: overrides.get(name, NOT_STATED) for name in SUMMARY_FIELD_NAMES})


def _structured(*, fields=None, decisions=(), moments=(), insights=()) -> IntelligenceResult:
    base = _result(insights=list(insights), action_items=[])
    return base.model_copy(
        update={
            "summary_fields": fields if fields is not None else _fields(),
            "decisions": list(decisions),
            "key_moments": list(moments),
        }
    )


def _decision(basis: str, turns, **details) -> Decision:
    return Decision(
        description=f"تصمیم {basis}", basis=basis, source_turn_indexes=list(turns), **details
    )


FULL = _structured(
    fields=_fields(
        purpose=_stated("توافق بر سر قیمت", [0]),
        next_steps=_stated("جلسهٔ بعد سه‌شنبه", [1], basis="inferred"),
    ),
    decisions=[
        _decision(
            "explicit",
            [1],
            owner=DecisionOwner(status="stated", value="فروش"),
            due_date=DecisionDueDate(status="stated", value="2026-11-03"),
        )
    ],
    moments=[KeyMoment(kind="commitment", description="قول اعلام قیمت", source_turn_indexes=[1])],
)


def _run(client, email_outbox, settings, monkeypatch, result: IntelligenceResult):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(result)
    )
    return workspace_id, meeting_id


def _sections(report: dict) -> list[tuple[str, str, int | None]]:
    return [(f["section"], f["finding_type"], f["finding_index"]) for f in report["qa"]["findings"]]


# --- A1, A2: stated claims with valid turns, not_stated details -------------------


def test_stated_claims_are_supported_and_cite_resolvable_evidence(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, FULL)
    report = _report(client, workspace_id, meeting_id)
    summary = report["summary"]

    purpose = summary["fields"]["purpose"]
    assert purpose["status"] == "stated" and purpose["text"] == "توافق بر سر قیمت"
    assert purpose["basis"] == "explicit" and purpose["supported"] is True
    assert purpose["evidence_status"] == "sufficient"
    assert [ref["start_ms"] for ref in purpose["evidence"]] == [500]
    next_steps = summary["fields"]["next_steps"]
    assert next_steps["basis"] == "inferred" and next_steps["supported"] is True

    # A2: not_stated has no text, no basis, no evidence and is not a finding.
    assert summary["fields"]["resolution"] == {
        "status": "not_stated",
        "text": None,
        "basis": None,
        "evidence_status": None,
        "supported": None,
        "evidence": [],
    }

    (decision,) = report["outcome"]["decisions"]
    assert decision["basis"] == "explicit" and decision["supported"] is True
    assert decision["key_point_index"] is None
    assert decision["owner"] == {"status": "stated", "value": "فروش"}
    assert decision["due_date"] == {"status": "stated", "value": "2026-11-03"}
    assert report["outcome"]["state"] == "explicit"

    assert summary["key_moments_status"] == "available"
    (moment,) = summary["key_moments"]
    assert moment["kind"] == "commitment" and moment["text"] == "قول اعلام قیمت"
    assert moment["supported"] is True and len(moment["evidence"]) == 1
    assert moment["start_ms"] == 4000 and moment["speaker_key"] == "speaker_1"

    # The overview keeps only its own two citations.
    assert len(summary["evidence"]) == 2 and summary["supported"] is True
    assert summary["key_points"] == []

    assert _sections(report) == [
        ("overview", "summary", None),
        ("summary_field", "summary", 0),
        ("summary_field", "summary", 2),
        ("decision", "insight", 3),
        ("key_moment", "insight", 4),
    ]
    bases = [f["basis"] for f in report["qa"]["findings"]]
    assert bases == [None, "explicit", "inferred", "explicit", None]
    assert report["qa"]["counts"]["total"] == 5
    assert report["qa"]["counts"]["unsupported"] == 0
    assert "unsupported_findings" not in report["completeness"]["notices"]

    _assert_refs_resolve(client, workspace_id, meeting_id, report)
    for ref in purpose["evidence"]:
        span = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence/{ref['id']}")
        assert (span["finding_type"], span["finding_index"]) == ("summary", 0)
    for ref in decision["evidence"]:
        span = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence/{ref['id']}")
        assert (span["finding_type"], span["finding_index"]) == ("insight", 3)


def test_summary_keeps_its_shape_and_hides_the_new_claims(
    client, email_outbox, settings, monkeypatch
):
    insight = IntelligenceInsight(category="risk", description="ریسک", source_turn_indexes=[0])
    result = FULL.model_copy(update={"insights": [insight]})
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    body = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert [p["description"] for p in body["key_points"]] == ["ریسک"]
    assert body["schema_version"] == 2
    (decision,) = body["decisions"]
    assert decision["description"] == "تصمیم explicit" and decision["basis"] == "explicit"
    assert decision["owner"] == {"status": "stated", "value": "فروش"}
    report = _report(client, workspace_id, meeting_id)
    assert [p["text"] for p in report["summary"]["key_points"]] == ["ریسک"]
    assert report["summary"]["key_points"][0]["index"] == 0


# --- A3: invalid citations ----------------------------------------------------------


def test_stated_claims_with_only_bad_turns_are_unsupported_and_counted(
    client, email_outbox, settings, monkeypatch
):
    result = _structured(
        fields=_fields(purpose=_stated("بدون مرجع", [7, 9])),
        decisions=[_decision("explicit", [8])],
        moments=[KeyMoment(kind="objection", description="اعتراض", source_turn_indexes=[])],
    )
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    report = _report(client, workspace_id, meeting_id)
    purpose = report["summary"]["fields"]["purpose"]
    assert purpose["status"] == "stated" and purpose["text"] == "بدون مرجع"
    assert purpose["supported"] is False and purpose["evidence"] == []
    assert purpose["evidence_status"] == "insufficient"
    (decision,) = report["outcome"]["decisions"]
    assert decision["supported"] is False and decision["evidence"] == []
    (moment,) = report["summary"]["key_moments"]
    assert moment["supported"] is False
    assert moment["start_ms"] is None and moment["speaker_key"] is None
    # An explicit decision without evidence never makes the outcome explicit.
    assert report["outcome"]["state"] == "inferred"
    assert report["qa"]["counts"]["unsupported"] == 3
    assert "unsupported_findings" in report["completeness"]["notices"]
    run = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")["analysis_run"]
    assert run["rejected_reference_count"] == 3  # 7, 9 and 8; never repaired


# --- A4: outcome ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decisions", "state"),
    [
        pytest.param([_decision("explicit", [1])], "explicit", id="explicit-supported"),
        pytest.param([_decision("inferred", [1])], "inferred", id="inferred-supported"),
        pytest.param(
            [_decision("inferred", [1]), _decision("explicit", [9])],
            "inferred",
            id="explicit-unsupported",
        ),
        pytest.param([], "not_stated", id="no-decisions"),
    ],
)
def test_outcome_is_explicit_only_for_a_supported_explicit_decision(
    client, email_outbox, settings, monkeypatch, decisions, state
):
    result = _structured(decisions=decisions)
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    outcome = _report(client, workspace_id, meeting_id)["outcome"]
    assert outcome["state"] == state
    assert len(outcome["decisions"]) == len(decisions)


@pytest.mark.parametrize(
    ("turns", "supported"),
    [pytest.param([0], True, id="supported"), pytest.param([9], False, id="unsupported")],
)
def test_a_structured_result_counts_insights_labeled_decision_as_inferred_decisions(
    client, email_outbox, settings, monkeypatch, turns, supported
):
    # F1: a mislabeled insight is never dropped and never makes the outcome
    # explicit; it is an inferred decision with unavailable details.
    insight = IntelligenceInsight(category="Decision", description="ت", source_turn_indexes=turns)
    result = _structured(insights=[insight])
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    report = _report(client, workspace_id, meeting_id)
    assert report["outcome"]["state"] == "inferred"
    (decision,) = report["outcome"]["decisions"]
    assert decision["text"] == "ت" and decision["basis"] == "inferred"
    assert decision["key_point_index"] == 0
    assert decision["owner"] == decision["due_date"] == {"status": "unavailable", "value": None}
    assert decision["supported"] is supported
    (key_point,) = report["summary"]["key_points"]
    assert key_point["category"] == "Decision" and key_point["supported"] is supported
    assert decision["evidence"] == key_point["evidence"]
    # One finding, as an insight; never counted twice.
    assert _sections(report) == [("overview", "summary", None), ("insight", "insight", 0)]
    assert report["qa"]["counts"]["total"] == 2
    body = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert [d["description"] for d in body["decisions"]] == ["ت"]


def test_structured_decisions_come_before_insights_labeled_decision(
    client, email_outbox, settings, monkeypatch
):
    insight = IntelligenceInsight(category="decision", description="ت", source_turn_indexes=[0])
    result = _structured(insights=[insight], decisions=[_decision("explicit", [1])])
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    report = _report(client, workspace_id, meeting_id)
    decisions = report["outcome"]["decisions"]
    assert [(d["text"], d["basis"], d["key_point_index"]) for d in decisions] == [
        ("تصمیم explicit", "explicit", None),
        ("ت", "inferred", 0),
    ]
    assert report["outcome"]["state"] == "explicit"
    assert [section for section, _type, _index in _sections(report)] == [
        "overview",
        "insight",
        "decision",
    ]


def test_a_structured_decision_with_an_unreadable_basis_is_never_explicit(
    client, email_outbox, settings, monkeypatch
):
    result = _structured(decisions=[_decision("explicit", [1])])
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    assert _report(client, workspace_id, meeting_id)["outcome"]["state"] == "explicit"
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        summary.decisions = [{**summary.decisions[0], "basis": "certain"}]
        db.commit()
    report = _report(client, workspace_id, meeting_id)
    (decision,) = report["outcome"]["decisions"]
    assert decision["basis"] is None and decision["supported"] is True
    assert report["outcome"]["state"] == "inferred"


# --- A6: legacy results ---------------------------------------------------------------


def test_a_legacy_result_shows_every_new_field_as_unavailable(
    client, email_outbox, settings, monkeypatch
):
    insight = IntelligenceInsight(category="decision", description="ق", source_turn_indexes=[1])
    workspace_id, meeting_id = _run(
        client, email_outbox, settings, monkeypatch, _result(insights=[insight])
    )
    report = _report(client, workspace_id, meeting_id)
    summary = report["summary"]
    for name in SUMMARY_FIELD_NAMES:
        assert summary["fields"][name] == {
            "status": "unavailable",
            "text": None,
            "basis": None,
            "evidence_status": None,
            "supported": None,
            "evidence": [],
        }
    assert summary["key_moments_status"] == "unavailable" and summary["key_moments"] == []
    (decision,) = report["outcome"]["decisions"]
    assert decision["basis"] is None and decision["key_point_index"] == 0
    assert decision["owner"] == decision["due_date"] == {"status": "unavailable", "value": None}
    assert report["outcome"]["state"] == "explicit"  # the legacy rule (D3)
    assert _sections(report) == [
        ("overview", "summary", None),
        ("insight", "insight", 0),
        ("action_item", "action_item", None),
    ]
    body = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert body["schema_version"] == 1


# --- A5, D8: no summary, mock run -------------------------------------------------------


def test_a_meeting_without_summary_has_no_new_fields(client, email_outbox, monkeypatch):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "claims-queued")
    accepted = _upload(client, tenant, key="claims-queued-0001")
    assert accepted.status_code == 202, accepted.text
    report = _report(client, tenant["workspace_id"], accepted.json()["meeting"]["id"])
    assert report["summary"] is None
    assert report["outcome"] == {"state": "unavailable", "decisions": []}


def test_a_mock_run_abstains_on_every_claim(client, email_outbox, settings, monkeypatch):
    providers = ProviderBundle(
        asr=_RealASR(),
        diarization=_RealDiarization(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=providers
    )
    report = _report(client, workspace_id, meeting_id)
    for name in SUMMARY_FIELD_NAMES:
        assert report["summary"]["fields"][name]["status"] == "not_stated"
    assert report["summary"]["key_moments_status"] == "available"
    assert report["summary"]["key_moments"] == []
    assert report["outcome"] == {"state": "not_stated", "decisions": []}
    assert _sections(report) == [("overview", "summary", None)]
    body = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert body["key_points"] == [] and body["decisions"] == []


# --- privacy -------------------------------------------------------------------------


class _PiiClaims(MockIntelligenceProvider):
    """Claims that repeat personal data from the transcript."""

    def __init__(self) -> None:
        super().__init__(allow_mock=True)

    async def analyze(self, request):
        result = await super().analyze(request)
        return result.model_copy(
            update={
                "is_mock": False,
                "summary_fields": _fields(purpose=_stated("تماس با ۰۹۱۲۳۴۵۶۷۸۹", [0])),
                "decisions": [
                    _decision(
                        "explicit",
                        [1],
                        owner=DecisionOwner(status="stated", value="ali.rezaei@example.com"),
                    )
                ],
                "key_moments": [
                    KeyMoment(
                        kind="commitment",
                        description="ایمیل ali.rezaei@example.com",
                        source_turn_indexes=[1],
                    )
                ],
            }
        )


def test_claim_texts_follow_the_redacted_view(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "claims-privacy")
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    accepted = _upload(client, tenant, key="claims-privacy-0001")
    assert accepted.status_code == 202, accepted.text
    providers = ProviderBundle(
        asr=_SegmentedASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=_PiiClaims(),
    )
    result = process_job(
        accepted.json()["job"]["id"], worker_id="claims", settings=settings, providers=providers
    )
    assert result["status"] == "completed", result
    meeting_id = accepted.json()["meeting"]["id"]
    workspace_id = tenant["workspace_id"]
    headers = {"X-Workspace-ID": workspace_id}
    report = client.get(f"/api/v1/meetings/{meeting_id}/report", headers=headers)
    summary = client.get(f"/api/v1/meetings/{meeting_id}/summary", headers=headers)
    assert report.status_code == summary.status_code == 200, report.text
    assert _no_raw(report.text), report.text
    assert _no_raw(summary.text), summary.text
    owner = report.json()["outcome"]["decisions"][0]["owner"]
    assert owner["status"] == "stated" and owner["value"].startswith("[EMAIL_")
    assert summary.json()["decisions"][0]["owner"] == owner  # same placeholder (Q4)


# --- subject export (F4) ------------------------------------------------------------


def _subject_matches(workspace_id: str, identifier: str) -> list[dict]:
    with inspection_session_factory()() as db:
        document, _audit = build_subject_export(
            db,
            workspace_id=workspace_id,
            identifiers=[parse_subject_identifier(identifier)],
            meeting_clause=true(),
            now=datetime.now(UTC),
        )
    return document["matches"]


def test_subject_export_finds_a_phone_number_only_in_a_decision_owner(
    client, email_outbox, settings, monkeypatch
):
    owner = DecisionOwner(status="stated", value="پشتیبانی ۰۹۱۲۱۱۱۲۲۳۳")
    result = _structured(decisions=[_decision("explicit", [1], owner=owner)])
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    (match,) = _subject_matches(workspace_id, "09121112233")
    assert match["meeting_id"] == meeting_id
    assert match["revisions"] == [] and match["actions"] == []
    assert match["summary"] == [
        {"kind": "decision_owner", "text": "پشتیبانی ۰۹۱۲۱۱۱۲۲۳۳", "matched": ["phone"]}
    ]
    # Control: a number that appears nowhere finds nothing.
    assert _subject_matches(workspace_id, "09124445566") == []


def test_decisions_for_keeps_the_legacy_rule_and_adds_labeled_insights_after_claims():
    insight = {"category": "Decision", "description": "ت"}
    other = {"category": "risk", "description": "ر"}
    claim = {"section": "decision", "category": "decision", "description": "ق"}
    assert NO_CLAIMS.decisions_for([insight, other]) == [insight]
    structured = StructuredClaims(structured=True, entries=[claim], decisions=[claim])
    assert structured.decisions_for([insight, other, claim]) == [claim, insight]


# --- A7: a schema-2 checkpoint is not reused ---------------------------------------------


def test_an_intelligence_checkpoint_of_schema_2_is_not_reused(
    client,
    email_outbox,
    monkeypatch,
    settings,
    broker,  # noqa: F811
):
    storage = create_object_storage(settings)
    job_id, _workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    monkeypatch.setitem(ckpt.ARTIFACT_SCHEMAS, StageName.INTELLIGENCE, (IntelligenceResult, 2))
    original = processor.commit_usage
    calls = {"n": 0}

    def flaky_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash after the checkpoints were committed")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "commit_usage", flaky_commit)
    first = process_job(
        job_id,
        worker_id="worker-1",
        settings=settings,
        providers=_checkpoint_bundle(),
        storage=storage,
    )
    assert first["status"] == "retry_scheduled"
    meeting_id = _job(job_id).meeting_id
    old = _published(meeting_id)["intelligence"]
    assert old.schema_version == 2

    monkeypatch.setitem(ckpt.ARTIFACT_SCHEMAS, StageName.INTELLIGENCE, (IntelligenceResult, 3))
    _make_due(job_id)
    providers = _checkpoint_bundle()
    second = process_job(
        job_id, worker_id="worker-2", settings=settings, providers=providers, storage=storage
    )
    assert second["status"] == "completed"
    assert providers.asr.calls == 0  # the other stages are still reused
    assert providers.intelligence.calls == 1  # the schema-2 analysis is not
    assert _published(meeting_id)["intelligence"].schema_version == 3
