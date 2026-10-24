"""HOM-61 (WP12-T04) acceptance oracle: structured, abstaining analysis fields.

Written from the accepted contract before the implementation was visible.
Summary fields, decisions and key moments either cite resolved turns or are
reported as unsupported; a detail the call does not state is ``not_stated``;
explicit and inferred outcomes stay apart; legacy rows and schema-2 artifacts
keep working.  New provider shapes are built from plain dicts so the file
collects on the base commit and each case fails on its own there.
"""

from __future__ import annotations

import asyncio
import copy
import json
from collections import Counter
from collections.abc import Callable
from datetime import date

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.contracts import openapi_contract as contract
from backend.database.saas_models import CheckpointState, MeetingSummary, StageCheckpoint
from backend.jobs import checkpoints as ckpt
from backend.jobs import outbox, processor
from backend.jobs.processor import process_job
from backend.jobs.states import StageName
from backend.providers import egress
from backend.providers.base import ProviderError
from backend.providers.mocks import MockIntelligenceProvider
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.schemas import IntelligenceRequest, IntelligenceResult, TranscriptTurn
from backend.services.report_service import outcome_state
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_call_report import GROUNDED, TOP_LEVEL, _read, _report
from backend.tests.test_call_report import _bundle as _fixed_bundle
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_evidence_spans import _processed
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_processor import _queued_upload
from backend.tests.test_stage_checkpoints import _bundle as _counting_bundle

FIELD_NAMES = ("purpose", "resolution", "next_steps")
TURN_START_MS = (500, 4000)
NOT_STATED = {"status": "not_stated", "text": None, "basis": None, "source_turn_indexes": []}
NOT_STATED_REPORT = {
    "status": "not_stated",
    "text": None,
    "basis": None,
    "evidence_status": None,
    "supported": None,
    "evidence": [],
}
UNAVAILABLE_REPORT = {**NOT_STATED_REPORT, "status": "unavailable"}
UNAVAILABLE_DETAIL = {"status": "unavailable", "value": None}
RISK = "قیمت دیر اعلام شود"
ACTION = "اعلام قیمت نهایی"


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


# --- builders -------------------------------------------------------------------


def _stated(text: str, basis: str = "explicit", turns=(0,)) -> dict:
    return {"status": "stated", "text": text, "basis": basis, "source_turn_indexes": list(turns)}


def _detail(value) -> dict:
    if value is None:
        return {"status": "not_stated", "value": None}
    return {"status": "stated", "value": value}


def _decision(text: str, basis: str = "explicit", turns=(1,), *, owner=None, due=None) -> dict:
    return {
        "description": text,
        "basis": basis,
        "owner": _detail(owner),
        "due_date": _detail(due),
        "source_turn_indexes": list(turns),
    }


def _moment(kind: str, text: str, turns=(1,)) -> dict:
    return {"kind": kind, "description": text, "source_turn_indexes": list(turns)}


def _structured(*, fields=None, decisions=(), key_moments=()) -> IntelligenceResult:
    summary_fields = {name: dict(NOT_STATED) for name in FIELD_NAMES}
    summary_fields.update(fields or {})
    return IntelligenceResult.model_validate(
        {
            "provider": "report_intelligence",
            "model": "report-v1",
            "summary": "قیمت فردا اعلام می‌شود و جلسهٔ بعد سه‌شنبه است",
            "summary_source_turn_indexes": [0, 1],
            "action_items": [{"description": ACTION, "source_turn_indexes": [0]}],
            "insights": [{"category": "risk", "description": RISK, "source_turn_indexes": [0]}],
            "summary_fields": summary_fields,
            "decisions": list(decisions),
            "key_moments": list(key_moments),
            "is_mock": False,
        }
    )


def _run(client, email_outbox, settings, monkeypatch, result) -> tuple[str, str]:
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_fixed_bundle(result)
    )
    return workspace_id, meeting_id


def _resolve(client, workspace_id: str, meeting_id: str, ref: dict) -> dict:
    resolved = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence/{ref['id']}")
    for key in ("segment_id", "start_ms", "end_ms", "text_start", "text_end", "speaker_key"):
        assert resolved[key] == ref[key], (key, ref)
    return resolved


def _findings(report: dict, section: str) -> list[dict]:
    return [f for f in report["qa"]["findings"] if f["section"] == section]


def _summary_body(client, workspace_id: str, meeting_id: str) -> dict:
    return _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")


# --- A1 -------------------------------------------------------------------------


def test_a1_stated_claims_citing_valid_turns_are_supported_and_resolve(
    client, email_outbox, settings, monkeypatch
):
    expected_fields = {
        "purpose": ("هماهنگی قیمت", "explicit", (0,)),
        "resolution": ("جلسهٔ بعد تنظیم شد", "inferred", (1,)),
        "next_steps": ("اعلام قیمت و جلسهٔ سه‌شنبه", "explicit", (0, 1)),
    }
    result = _structured(
        fields={name: _stated(*value) for name, value in expected_fields.items()},
        decisions=[_decision("جلسهٔ بعد سه‌شنبه", "explicit", (1,), owner="فروش", due="2026-11-03")],
        key_moments=[
            _moment("commitment", "قول اعلام قیمت", (0,)),
            _moment("resolution", "توافق بر جلسه", (1,)),
        ],
    )
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    report = _report(client, workspace_id, meeting_id)
    summary = report["summary"]

    fields = summary["fields"]
    assert set(fields) == set(FIELD_NAMES)
    for name, (text, basis, turns) in expected_fields.items():
        field = fields[name]
        assert (field["status"], field["text"], field["basis"]) == ("stated", text, basis)
        assert field["supported"] is True and field["evidence_status"] == "sufficient"
        resolved = [_resolve(client, workspace_id, meeting_id, ref) for ref in field["evidence"]]
        assert sorted(r["start_ms"] for r in resolved) == [TURN_START_MS[t] for t in turns]
        assert {r["finding_type"] for r in resolved} == {"summary"}

    overview = [_resolve(client, workspace_id, meeting_id, ref) for ref in summary["evidence"]]
    assert len(overview) == 2
    assert all(r["finding_type"] == "summary" and r["finding_index"] is None for r in overview)

    outcome = report["outcome"]
    assert outcome["state"] == "explicit"
    (decision,) = outcome["decisions"]
    assert decision["text"] == "جلسهٔ بعد سه‌شنبه" and decision["basis"] == "explicit"
    assert decision["owner"] == {"status": "stated", "value": "فروش"}
    assert decision["due_date"] == {"status": "stated", "value": "2026-11-03"}
    assert decision["key_point_index"] is None
    assert decision["supported"] is True and decision["evidence_status"] == "sufficient"
    resolved = [_resolve(client, workspace_id, meeting_id, ref) for ref in decision["evidence"]]
    assert [(r["finding_type"], r["start_ms"]) for r in resolved] == [("insight", 4000)]

    assert summary["key_moments_status"] == "available"
    moments = summary["key_moments"]
    assert [(m["kind"], m["text"]) for m in moments] == [
        ("commitment", "قول اعلام قیمت"),
        ("resolution", "توافق بر جلسه"),
    ]
    for moment, start in zip(moments, TURN_START_MS, strict=True):
        assert moment["supported"] is True and moment["evidence_status"] == "sufficient"
        (ref,) = moment["evidence"]
        assert _resolve(client, workspace_id, meeting_id, ref)["finding_type"] == "insight"
        assert moment["start_ms"] == ref["start_ms"] == start
        assert moment["speaker_key"] == ref["speaker_key"] and moment["speaker_key"]

    body = _summary_body(client, workspace_id, meeting_id)
    assert [p["text"] for p in summary["key_points"]] == [RISK]
    assert [p["description"] for p in body["key_points"]] == [RISK]
    assert body["overview"] == summary["overview"]
    assert body["schema_version"] == 2

    qa = report["qa"]
    sections = [f["section"] for f in qa["findings"]]
    assert sections[0] == "overview" and sections[-1] == "action_item"
    assert Counter(sections) == Counter(
        overview=1, summary_field=3, decision=1, key_moment=2, insight=1, action_item=1
    )
    for finding in qa["findings"]:
        assert finding["supported"] is True, finding
        if finding["section"] == "summary_field":
            assert finding["finding_type"] == "summary"
            assert finding["basis"] == expected_fields[finding["category"]][1]
            assert finding["text"] == expected_fields[finding["category"]][0]
        elif finding["section"] == "decision":
            assert (finding["finding_type"], finding["category"]) == ("insight", "decision")
            assert finding["basis"] == "explicit"
        elif finding["section"] == "key_moment":
            assert finding["finding_type"] == "insight"
            assert finding["category"] in {"commitment", "resolution"}
        else:
            assert finding["basis"] is None
        if finding["finding_index"] is not None:
            for ref in finding["evidence"]:
                resolved = _resolve(client, workspace_id, meeting_id, ref)
                assert resolved["finding_index"] == finding["finding_index"]
    assert qa["counts"] == {
        "total": 9,
        "supported": 9,
        "unsupported": 0,
        "sufficient": 9,
        "partial": 0,
        "insufficient": 0,
    }
    assert report["completeness"]["notices"] == []


# --- A2 -------------------------------------------------------------------------


@pytest.mark.parametrize("purpose_stated", [True, False], ids=["one_stated", "all_not_stated"])
def test_a2_not_stated_fields_have_no_text_no_evidence_and_are_not_findings(
    client, email_outbox, settings, monkeypatch, purpose_stated
):
    fields = {"purpose": _stated("هماهنگی قیمت", "explicit", (0,))} if purpose_stated else {}
    workspace_id, meeting_id = _run(
        client, email_outbox, settings, monkeypatch, _structured(fields=fields)
    )
    report = _report(client, workspace_id, meeting_id)
    summary = report["summary"]
    for name in ("resolution", "next_steps"):
        assert summary["fields"][name] == NOT_STATED_REPORT
    purpose = summary["fields"]["purpose"]
    if purpose_stated:
        assert purpose["status"] == "stated" and purpose["supported"] is True
    else:
        assert purpose == NOT_STATED_REPORT
    assert summary["key_moments_status"] == "available" and summary["key_moments"] == []
    assert report["outcome"] == {"state": "not_stated", "decisions": []}

    assert [f["category"] for f in _findings(report, "summary_field")] == (
        ["purpose"] if purpose_stated else []
    )
    total = 4 if purpose_stated else 3
    assert report["qa"]["counts"] == {
        "total": total,
        "supported": total,
        "unsupported": 0,
        "sufficient": total,
        "partial": 0,
        "insufficient": 0,
    }
    assert report["completeness"]["notices"] == []
    assert report["completeness"]["is_complete"] is True


# --- A3, deny/allow 1 and 4 -----------------------------------------------------


def _single_claim(kind: str, turns) -> IntelligenceResult:
    if kind == "summary_field":
        return _structured(fields={"resolution": _stated("جلسه تنظیم شد", "explicit", turns)})
    if kind == "decision":
        return _structured(decisions=[_decision("جلسهٔ بعد سه‌شنبه", "explicit", turns)])
    return _structured(key_moments=[_moment("commitment", "قول جلسه", turns)])


def _claim(report: dict, kind: str) -> dict:
    if kind == "summary_field":
        return report["summary"]["fields"]["resolution"]
    if kind == "decision":
        (decision,) = report["outcome"]["decisions"]
        return decision
    (moment,) = report["summary"]["key_moments"]
    return moment


@pytest.mark.parametrize("kind", ["summary_field", "decision", "key_moment"])
@pytest.mark.parametrize(
    ("turns", "supported", "rejected"),
    [((7, 9), False, 2), ((), False, 0), ((1,), True, 0)],
    ids=["out_of_range", "no_evidence", "valid_turn"],
)
def test_a3_a_stated_claim_is_supported_exactly_when_a_cited_turn_resolves(
    client, email_outbox, settings, monkeypatch, kind, turns, supported, rejected
):
    workspace_id, meeting_id = _run(
        client, email_outbox, settings, monkeypatch, _single_claim(kind, turns)
    )
    report = _report(client, workspace_id, meeting_id)
    claim = _claim(report, kind)
    assert claim["supported"] is supported
    if supported:
        assert claim["evidence_status"] == "sufficient"
        (ref,) = claim["evidence"]
        assert _resolve(client, workspace_id, meeting_id, ref)["start_ms"] == 4000
    else:
        assert claim["evidence_status"] == "insufficient" and claim["evidence"] == []

    (finding,) = _findings(report, kind)
    assert finding["supported"] is supported
    assert finding["evidence"] == claim["evidence"]
    counts = report["qa"]["counts"]
    assert counts["total"] == 4
    assert counts["unsupported"] == (0 if supported else 1)
    assert counts["insufficient"] == (0 if supported else 1)
    notices = report["completeness"]["notices"]
    assert ("unsupported_findings" in notices) is (not supported)

    run = _summary_body(client, workspace_id, meeting_id)["analysis_run"]
    assert run["rejected_reference_count"] == rejected

    if kind == "key_moment":
        assert claim["start_ms"] == (4000 if supported else None)
        assert (claim["speaker_key"] is None) is (not supported)
    if kind == "decision":
        assert report["outcome"]["state"] == ("explicit" if supported else "inferred")


# --- A4, deny/allow 3 -----------------------------------------------------------


@pytest.mark.parametrize(
    ("decisions", "state"),
    [
        ([("explicit", (1,))], "explicit"),
        ([("inferred", (1,))], "inferred"),
        ([("inferred", (1,)), ("explicit", (9,))], "inferred"),
        ([("inferred", (0,)), ("explicit", (1,))], "explicit"),
        ([("explicit", (9,))], "inferred"),
        ([], "not_stated"),
    ],
    ids=[
        "explicit_supported",
        "inferred_supported",
        "inferred_supported_explicit_unsupported",
        "inferred_and_explicit_supported",
        "explicit_unsupported",
        "no_decisions",
    ],
)
def test_a4_outcome_is_explicit_only_with_a_supported_explicit_decision(
    client, email_outbox, settings, monkeypatch, decisions, state
):
    result = _structured(
        decisions=[
            _decision(f"تصمیم {index}", basis, turns)
            for index, (basis, turns) in enumerate(decisions)
        ]
    )
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, result)
    outcome = _report(client, workspace_id, meeting_id)["outcome"]
    assert outcome["state"] == state
    assert [(d["text"], d["basis"]) for d in outcome["decisions"]] == [
        (f"تصمیم {index}", basis) for index, (basis, _) in enumerate(decisions)
    ]
    assert [d["supported"] for d in outcome["decisions"]] == [
        9 not in turns for _, turns in decisions
    ]


def test_a4_outcome_rule_with_decision_basis():
    assert outcome_state(has_summary=False, decisions_supported=[], decisions_basis=[]) == (
        "unavailable"
    )
    assert outcome_state(has_summary=True, decisions_supported=[], decisions_basis=[]) == (
        "not_stated"
    )
    assert (
        outcome_state(has_summary=True, decisions_supported=[True], decisions_basis=["inferred"])
        == "inferred"
    )
    assert (
        outcome_state(has_summary=True, decisions_supported=[True], decisions_basis=["explicit"])
        == "explicit"
    )
    assert (
        outcome_state(has_summary=True, decisions_supported=[True], decisions_basis=[None])
        == "explicit"
    )
    assert (
        outcome_state(
            has_summary=True,
            decisions_supported=[False, True],
            decisions_basis=["explicit", "inferred"],
        )
        == "inferred"
    )


# --- A5 -------------------------------------------------------------------------


def test_a5_a_meeting_without_a_summary_adds_no_key_and_stays_unavailable(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hom61-queued")
    accepted = _upload(client, tenant, key="hom61-queued-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    report = _report(client, tenant["workspace_id"], meeting_id)
    assert set(report) == TOP_LEVEL
    assert report["summary"] is None
    assert report["outcome"] == {"state": "unavailable", "decisions": []}
    assert report["qa"]["findings"] == []


# --- A6 -------------------------------------------------------------------------


def _legacy_meeting(client, email_outbox, settings, monkeypatch) -> tuple[str, str, list[dict]]:
    workspace_id, meeting_id = _run(client, email_outbox, settings, monkeypatch, GROUNDED)
    items = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence")["items"]

    def cited(index: int) -> list[str]:
        return [
            i["id"] for i in items if i["finding_type"] == "insight" and i["finding_index"] == index
        ]

    key_points = [
        {
            "category": "decision",
            "description": "جلسهٔ بعد سه‌شنبه",
            "confidence": 0.9,
            "source_turn_indexes": [1],
            "evidence_ids": cited(0),
            "evidence_status": "sufficient",
        },
        {
            "category": "risk",
            "description": RISK,
            "confidence": None,
            "source_turn_indexes": [0],
            "evidence_ids": cited(1),
            "evidence_status": "sufficient",
        },
    ]
    assert key_points[0]["evidence_ids"] and key_points[1]["evidence_ids"]
    with inspection_session_factory()() as db:
        row = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        assert row is not None
        row.key_points = key_points
        row.decisions = [key_points[0]]
        row.topics = ["decision", "risk"]
        row.schema_version = 1
        db.commit()
    return workspace_id, meeting_id, key_points


def test_a6_a_legacy_row_keeps_every_old_field(client, email_outbox, settings, monkeypatch):
    workspace_id, meeting_id, key_points = _legacy_meeting(
        client, email_outbox, settings, monkeypatch
    )
    body = _summary_body(client, workspace_id, meeting_id)
    assert body["key_points"] == key_points
    assert body["decisions"] == [key_points[0]]
    assert body["schema_version"] == 1

    report = _report(client, workspace_id, meeting_id)
    summary = report["summary"]
    assert summary["overview"] == body["overview"]
    assert len(summary["evidence"]) == 2 and summary["supported"] is True
    assert [(p["index"], p["text"]) for p in summary["key_points"]] == [
        (0, "جلسهٔ بعد سه‌شنبه"),
        (1, RISK),
    ]
    assert all(p["supported"] for p in summary["key_points"])
    assert report["outcome"]["state"] == "explicit"
    (decision,) = report["outcome"]["decisions"]
    assert decision["text"] == "جلسهٔ بعد سه‌شنبه" and decision["key_point_index"] == 0
    assert decision["supported"] is True
    kinds = [(f["finding_type"], f["finding_index"]) for f in report["qa"]["findings"]]
    assert kinds == [("summary", None), ("insight", 0), ("insight", 1), ("action_item", None)]
    assert report["qa"]["counts"]["total"] == 4 and report["qa"]["counts"]["unsupported"] == 0


def test_a6_a_legacy_row_shows_every_new_field_as_unavailable(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _legacy_meeting(client, email_outbox, settings, monkeypatch)
    report = _report(client, workspace_id, meeting_id)
    summary = report["summary"]
    assert summary["fields"] == {name: UNAVAILABLE_REPORT for name in FIELD_NAMES}
    assert summary["key_moments_status"] == "unavailable" and summary["key_moments"] == []
    (decision,) = report["outcome"]["decisions"]
    assert decision["basis"] is None
    assert decision["owner"] == decision["due_date"] == UNAVAILABLE_DETAIL
    assert report["outcome"]["state"] == "explicit"
    findings = report["qa"]["findings"]
    assert [f["section"] for f in findings] == ["overview", "insight", "insight", "action_item"]
    assert all(f["basis"] is None for f in findings)


# --- A7 -------------------------------------------------------------------------

SCHEMA_2_ARTIFACT = {
    "provider": "openai_compatible",
    "model": "m",
    "summary": "s",
    "summary_source_turn_indexes": [0],
    "action_items": [
        {
            "description": "d",
            "owner": None,
            "due_date": None,
            "priority": "high",
            "source_turn_indexes": [0],
        }
    ],
    "insights": [
        {"category": "decision", "description": "x", "confidence": 0.5, "source_turn_indexes": [0]}
    ],
    "issue": None,
    "is_mock": False,
    "usage": None,
}


def test_a7_a_schema_2_artifact_still_validates():
    parsed = ckpt.parse_artifact(json.dumps(SCHEMA_2_ARTIFACT).encode(), IntelligenceResult)
    assert parsed.summary == "s" and parsed.insights[0].category == "decision"


def test_a7_a_schema_2_artifact_reads_as_an_unstructured_result():
    parsed = ckpt.parse_artifact(json.dumps(SCHEMA_2_ARTIFACT).encode(), IntelligenceResult)
    assert getattr(parsed, "summary_fields", "absent") is None
    assert getattr(parsed, "decisions", "absent") == []
    assert getattr(parsed, "key_moments", "absent") == []


def test_a7_a_structured_result_round_trips_through_its_artifact():
    result = _structured(
        fields={"purpose": _stated("هماهنگی قیمت")},
        decisions=[_decision("جلسه", "inferred", (1,), owner="فروش", due="2026-11-03")],
        key_moments=[_moment("objection", "اعتراض به قیمت", (0,))],
    )
    assert ckpt.parse_artifact(ckpt.serialize_result(result), IntelligenceResult) == result


@pytest.mark.parametrize(
    ("stored_version", "reused"),
    [(2, False), (None, True)],
    ids=["schema_2_checkpoint", "current_checkpoint"],
)
def test_a7_a_schema_2_intelligence_checkpoint_is_not_reused(
    client, email_outbox, monkeypatch, settings, broker, stored_version, reused
):
    storage = create_object_storage(settings)
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    original = processor.commit_usage
    calls = {"n": 0}

    def flaky_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before the results transaction committed")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "commit_usage", flaky_commit)
    first = process_job(
        job_id,
        worker_id="worker-1",
        settings=settings,
        providers=_counting_bundle(),
        storage=storage,
    )
    assert first["status"] == "retry_scheduled", first
    if stored_version is not None:
        with inspection_session_factory()() as db:
            row = db.scalar(
                select(StageCheckpoint).where(
                    StageCheckpoint.workspace_id == workspace_id,
                    StageCheckpoint.stage == StageName.INTELLIGENCE.value,
                    StageCheckpoint.state == CheckpointState.PUBLISHED,
                )
            )
            assert row is not None
            row.schema_version = stored_version
            db.commit()
    _make_due(job_id)
    again = _counting_bundle()
    second = process_job(
        job_id, worker_id="worker-2", settings=settings, providers=again, storage=storage
    )
    assert second["status"] == "completed", second
    assert again.asr.calls == 0
    assert again.intelligence.calls == (0 if reused else 1)


# --- A8, deny/allow 2 -----------------------------------------------------------

ENDPOINT = "https://llm.example.test/v1"
REQUEST = IntelligenceRequest(
    transcript_text="Please call me back tomorrow. I will call you at nine.",
    turns=[
        TranscriptTurn(text="Please call me back tomorrow.", speaker_label="speaker_1"),
        TranscriptTurn(text="I will call you at nine.", speaker_label="speaker_2"),
    ],
    language="en",
    call_id="call-hom61",
)
DROP = object()


def _valid_output() -> dict:
    return {
        "summary": "The customer asked for a callback at nine.",
        "summary_source_turn_indexes": [0, 1],
        "action_items": [],
        "insights": [],
        "summary_fields": {
            "purpose": _stated("Arrange a callback", "explicit", (0,)),
            "resolution": _stated("A callback at nine was agreed", "inferred", (1,)),
            "next_steps": dict(NOT_STATED),
        },
        "decisions": [_decision("Call back at nine", "explicit", (1,), owner="Support")],
        "key_moments": [_moment("commitment", "The agent promises a callback", (1,))],
        "extraction": {
            name: {"status": "not_stated", "values": []}
            for name in ("intent", "topic", "entity", "outcome")
        },
    }


def _changed(path: tuple, value) -> dict:
    output = _valid_output()
    node = output
    for key in path[:-1]:
        node = node[key]
    if value is DROP:
        del node[path[-1]]
    else:
        node[path[-1]] = copy.deepcopy(value)
    return output


def _adapter(seen: list | None = None) -> Callable:
    def build(output: dict) -> OpenAICompatibleIntelligenceProvider:
        def respond(request: httpx.Request) -> httpx.Response:
            if seen is not None:
                seen.append(json.loads(request.content))
            content = json.dumps(output)
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        return OpenAICompatibleIntelligenceProvider(
            base_url=ENDPOINT,
            model="oracle-llm",
            client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )

    return build


def _analyze(output: dict, seen: list | None = None) -> IntelligenceResult:
    adapter = _adapter(seen)(output)
    with egress.permit(f"{ENDPOINT}/chat/completions"):
        return asyncio.run(adapter.analyze(REQUEST))


def test_a8_the_parser_accepts_a_well_formed_structured_payload():
    result = _analyze(_valid_output())
    fields = result.summary_fields
    assert (fields.purpose.status, fields.purpose.text, fields.purpose.basis) == (
        "stated",
        "Arrange a callback",
        "explicit",
    )
    assert fields.purpose.source_turn_indexes == [0]
    assert (fields.resolution.status, fields.resolution.basis) == ("stated", "inferred")
    assert (fields.next_steps.status, fields.next_steps.text, fields.next_steps.basis) == (
        "not_stated",
        None,
        None,
    )
    assert fields.next_steps.source_turn_indexes == []
    (decision,) = result.decisions
    assert (decision.description, decision.basis) == ("Call back at nine", "explicit")
    assert (decision.owner.status, decision.owner.value) == ("stated", "Support")
    assert (decision.due_date.status, decision.due_date.value) == ("not_stated", None)
    assert decision.source_turn_indexes == [1]
    (moment,) = result.key_moments
    assert (moment.kind, moment.source_turn_indexes) == ("commitment", [1])
    assert result.is_mock is False


def test_a8_the_request_asks_for_the_new_keys_under_prompt_version_4():
    seen: list = []
    _analyze(_valid_output(), seen)
    (payload,) = seen
    schema = payload["response_format"]["json_schema"]["schema"]
    assert {"summary_fields", "decisions", "key_moments"} <= set(schema["required"])
    assert "not_stated" in payload["messages"][0]["content"]
    identity = _adapter()(_valid_output()).identity()
    assert identity.params["prompt_version"] == 4  # 4: the extraction (HOM-53)


ALLOWED = [
    pytest.param((), None, id="well_formed"),
    pytest.param(("summary_fields", "purpose"), NOT_STATED, id="not_stated_without_text"),
    pytest.param(
        ("summary_fields",), {name: NOT_STATED for name in FIELD_NAMES}, id="all_abstaining"
    ),
    pytest.param(("decisions",), [], id="no_decisions"),
    pytest.param(("key_moments",), [], id="no_key_moments"),
    pytest.param(
        ("decisions", 0, "due_date"), {"status": "stated", "value": "2026-11-03"}, id="stated_due"
    ),
    pytest.param(("decisions", 0, "source_turn_indexes"), [], id="stated_claim_without_turns"),
]
REJECTED = [
    pytest.param(("summary_fields",), DROP, id="missing_summary_fields"),
    pytest.param(("decisions",), DROP, id="missing_decisions"),
    pytest.param(("key_moments",), DROP, id="missing_key_moments"),
    pytest.param(("summary_fields", "purpose", "text"), None, id="stated_field_text_null"),
    pytest.param(("summary_fields", "purpose", "text"), "   ", id="stated_field_text_blank"),
    pytest.param(("summary_fields", "purpose", "basis"), None, id="stated_field_without_basis"),
    pytest.param(("summary_fields", "next_steps", "text"), "A guess", id="not_stated_with_text"),
    pytest.param(
        ("summary_fields", "next_steps", "source_turn_indexes"), [0], id="not_stated_with_turns"
    ),
    pytest.param(("summary_fields", "next_steps", "basis"), "inferred", id="not_stated_with_basis"),
    pytest.param(("summary_fields", "purpose", "status"), "unknown", id="bad_status"),
    pytest.param(("decisions", 0, "owner", "value"), None, id="stated_owner_without_value"),
    pytest.param(
        ("decisions", 0, "due_date", "value"), "2026-11-03", id="not_stated_due_with_value"
    ),
    pytest.param(("decisions", 0, "basis"), None, id="decision_without_basis"),
    pytest.param(("decisions", 0, "source_turn_indexes"), [-1], id="negative_turn"),
    pytest.param(("key_moments", 0, "kind"), "", id="key_moment_blank_kind"),
]


@pytest.mark.parametrize(("path", "value"), ALLOWED)
def test_a8_the_parser_accepts_each_allowed_shape(path, value):
    output = _changed(path, value) if path else _valid_output()
    result = _analyze(output)
    assert result.summary_fields is not None
    if path == ("decisions", 0, "due_date"):
        assert result.decisions[0].due_date.value == date(2026, 11, 3)


@pytest.mark.parametrize(("path", "value"), REJECTED)
def test_a8_the_parser_rejects_each_invalid_shape(path, value):
    with pytest.raises(ProviderError) as refused:
        _analyze(_changed(path, value))
    assert refused.value.code == "intelligence_invalid_response"
    assert refused.value.retryable is False


def _model(**changes) -> dict:
    document = {
        "provider": "p",
        "model": "m",
        "summary": "s",
        "decisions": [_decision("d", "explicit", (0,))],
    }
    document.update(changes)
    return document


def test_a8_a_structured_claim_needs_summary_fields():
    with_fields = IntelligenceResult.model_validate(
        _model(summary_fields={name: NOT_STATED for name in FIELD_NAMES})
    )
    assert len(with_fields.decisions) == 1
    with pytest.raises(ValidationError):
        IntelligenceResult.model_validate(_model())
    with pytest.raises(ValidationError):
        IntelligenceResult.model_validate(
            _model(decisions=[], key_moments=[_moment("objection", "x", (0,))])
        )


# --- A9 -------------------------------------------------------------------------


def test_a9_the_mock_provider_returns_a_valid_abstaining_result():
    result = asyncio.run(MockIntelligenceProvider(allow_mock=True).analyze(REQUEST))
    assert result.is_mock is True
    fields = result.summary_fields
    assert fields is not None
    for name in FIELD_NAMES:
        field = getattr(fields, name)
        assert (field.status, field.text, field.basis, field.source_turn_indexes) == (
            "not_stated",
            None,
            None,
            [],
        )
    assert result.decisions == [] and result.key_moments == []
    assert result.summary_source_turn_indexes == [0, 1]
    assert IntelligenceResult.model_validate(result.model_dump(mode="json")) == result


def test_a9_a_mock_run_reports_every_field_as_not_stated(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(client, email_outbox, settings, monkeypatch)
    report = _report(client, workspace_id, meeting_id)
    summary = report["summary"]
    assert summary["fields"] == {name: NOT_STATED_REPORT for name in FIELD_NAMES}
    assert summary["key_moments_status"] == "available" and summary["key_moments"] == []
    assert report["outcome"] == {"state": "not_stated", "decisions": []}
    assert _findings(report, "summary_field") == []
    notices = report["completeness"]["notices"]
    assert "mock_analysis" in notices and "unsupported_findings" not in notices
    assert _summary_body(client, workspace_id, meeting_id)["schema_version"] == 2


# --- A10 ------------------------------------------------------------------------


def test_a10_the_openapi_contract_is_current_and_summary_keeps_its_shape():
    assert contract.main(["--check"]) == 0
    snapshot = contract.load_snapshot()
    # 1.3.0: HOM-82 added the role trend routes; SummaryResponse is unchanged.
    assert snapshot["info"]["version"] == "1.3.0"
    assert sorted(snapshot["components"]["schemas"]["SummaryResponse"]["properties"]) == [
        "analysis_run",
        "analysis_run_id",
        "created_at",
        "decisions",
        "evidence_status",
        "key_points",
        "meeting_id",
        "model",
        "overview",
        "privacy_enforced",
        "provider",
        "redaction",
        "schema_version",
        "sentiment",
        "topics",
        "updated_at",
        "view",
    ]
