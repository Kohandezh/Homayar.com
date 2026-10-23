"""HOM-53 part 3 acceptance oracle: the extraction block of the call report.

Written from the contract (SPEC sections 2 and 4) before the implementation was visible.
Meetings run through the real full job and the recompute with fake intelligence providers
that return chosen ``extraction`` objects; the report is read through the API and compared
with the stored ``analysis_extractions`` row, its evidence spans and the taxonomy.
"""

from __future__ import annotations

import base64
import copy
import json
import os
import re
from urllib.parse import urlparse

import pytest
from sqlalchemy import delete, select

from backend.analysis_taxonomy import DIMENSIONS
from backend.database import saas_models
from backend.database.saas_models import AnalysisRun, AuditEvent, EvidenceSpan, StageState
from backend.jobs import outbox, recompute
from backend.jobs.processor import process_job
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_analysis_recompute import _correct, _current_run
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_evidence_spans import _processed
from backend.tests.test_hom53_extraction_oracle import (
    CORRECTED,
    LEGACY,
    NOT_STATED,
    ORIGINAL,
    PHONE,
    RICH,
    TAXONOMY,
    TURNS,
    _add_extraction,
    _add_run,
    _by_revision,
    _changed,
    _expected,
    _Extracting,
    _extraction,
    _Failing,
    _mock_asr_meeting,
    _placeholder,
    _quote_placeholder,
    _recompute_workspace,
    _redaction_run,
    _row,
    _rows,
    _run,
    _ScriptASR,
    _stated,
    _value,
)
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant
from backend.tests.test_ingestion_ledger import _upload as _tenant_upload
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_job_processor import SilentASRProvider, SingleSpeakerDiarizationProvider
from backend.tests.test_privacy_routing import (
    NID_FA,
    SEGMENTS,
    _no_raw,
    _SegmentedASR,
    _set_policy,
)
from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata

BLOCK_KEYS = {
    "status",
    "needs_review",
    "review_count",
    "analysis_run_id",
    "taxonomy_id",
    "taxonomy_version",
    "taxonomy_digest",
    "validator_version",
    "policy_version",
    "policy_digest",
    "fields",
}
ROW_COLUMNS = (
    "taxonomy_id",
    "taxonomy_version",
    "taxonomy_digest",
    "validator_version",
    "policy_version",
    "policy_digest",
)
FIELD_KEYS = {"status", "abstain_reason", "review_required", "values"}
VALUE_KEYS = {
    "index",
    "label",
    "label_name_en",
    "label_name_fa",
    "basis",
    "critical",
    "confidence",
    "review",
    "review_reason",
    "text",
    "evidence_status",
    "supported",
    "evidence",
}
REF_KEYS = {
    "id",
    "transcript_revision_id",
    "is_current_revision",
    "segment_id",
    "speaker_key",
    "text_start",
    "text_end",
    "text",
    "start_ms",
    "end_ms",
    "evidence_type",
}
UNCHANGED = (
    "summary",
    "outcome",
    "qa",
    "review_actions",
    "completeness",
    "view",
    "privacy_enforced",
    "redaction",
)
EMAIL = "ali.rezaei@example.com"
SEGMENT_PHONE = "۰۹۱۲۳۴۵۶۷۸۹"
ENTITIES = _extraction(
    entity=_stated(
        _value("national_id", [0], 0.9, text=NID_FA),
        _value("email", [1], 0.9, text=EMAIL),
        _value("phone", [1], 0.9, text=SEGMENT_PHONE),
    )
)
PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
_GENERATED = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?"
)


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
            "pii_mapping_keys": f"k1:{base64.b64encode(os.urandom(32)).decode()}",
            "pii_mapping_active_key_id": "k1",
        }
    )


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


@pytest.fixture
def analysis_provider(monkeypatch):
    holder = {"intelligence": _Extracting(_by_revision)}
    monkeypatch.setattr(
        recompute,
        "create_provider_bundle",
        lambda _settings: ProviderBundle(
            asr=MockASRProvider(allow_mock=True, transcript=ORIGINAL),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=holder["intelligence"],
        ),
    )
    return holder


def _report_response(client, workspace_id: str, meeting_id: str, **params):
    return client.get(
        f"/api/v1/meetings/{meeting_id}/report",
        headers={"X-Workspace-ID": workspace_id},
        params=params,
    )


def _report(client, workspace_id: str, meeting_id: str, **params) -> dict:
    response = _report_response(client, workspace_id, meeting_id, **params)
    assert response.status_code == 200, response.text
    return response.json()


def _get(client, workspace_id: str, path: str, **params) -> dict:
    response = client.get(path, headers={"X-Workspace-ID": workspace_id}, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _meeting(client, workspace_id: str, settings, *, key: str, build, turns=TURNS) -> str:
    upload = _upload(client, workspace_id=workspace_id, key=key)
    assert upload.status_code == 202, upload.text
    outcome = process_job(
        upload.json()["job"]["id"],
        worker_id=key,
        settings=settings,
        providers=ProviderBundle(
            asr=_ScriptASR(turns),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=_Extracting(build),
        ),
    )
    assert outcome["status"] == "completed", outcome
    return upload.json()["meeting"]["id"]


def _label(dimension: str, label_id: str):
    return next(label for label in TAXONOMY.dimension(dimension).labels if label.id == label_id)


def _unavailable_block() -> dict:
    return {
        "status": "unavailable",
        "needs_review": False,
        "review_count": 0,
        "analysis_run_id": None,
        **dict.fromkeys(ROW_COLUMNS),
        "fields": {
            name: {
                "status": "unavailable",
                "abstain_reason": None,
                "review_required": False,
                "values": [],
            }
            for name in DIMENSIONS
        },
    }


def _expected_block(row: dict, *, names: bool = True, texts: dict[int, str] | None = None) -> dict:
    spans = _extraction_spans(row["analysis_run_id"])
    fields = {}
    index = 0
    for name in DIMENSIONS:
        stored = row["result"]["fields"][name]
        if stored["verdict"] == "rejected":
            fields[name] = {
                "status": "unavailable",
                "abstain_reason": None,
                "review_required": False,
                "values": [],
            }
            continue
        values = []
        for value in stored["values"]:
            label = _label(name, value["label"])
            text = value["text"] if name == "entity" else None
            if texts is not None and index in texts:
                text = texts[index]
            values.append(
                {
                    "index": index,
                    "label": value["label"],
                    "label_name_en": label.name_en if names else None,
                    "label_name_fa": label.name_fa if names else None,
                    "basis": value["basis"],
                    "critical": value["critical"],
                    "confidence": value["confidence"],
                    "review": "required"
                    if value["disposition"] == "review_required"
                    else "not_required",
                    "review_reason": value["review_reason"],
                    "text": text,
                    "evidence_status": "sufficient" if spans.get(index) else "insufficient",
                    "supported": bool(spans.get(index)),
                }
            )
            index += 1
        fields[name] = {
            "status": stored["status"],
            "abstain_reason": stored["abstain_reason"],
            "review_required": stored["review_required"],
            "values": values,
        }
    return {
        "status": "available" if row["valid"] else "invalid",
        "needs_review": row["needs_review"],
        "review_count": row["review_count"],
        "analysis_run_id": row["analysis_run_id"],
        **{column: row[column] for column in ROW_COLUMNS},
        "fields": fields,
    }


def _without_evidence(block: dict) -> dict:
    stripped = copy.deepcopy(block)
    for field in stripped["fields"].values():
        for value in field["values"]:
            value.pop("evidence", None)
    return stripped


def _values(block: dict) -> list[dict]:
    return [value for name in DIMENSIONS for value in block["fields"][name]["values"]]


def _extraction_spans(run_id: str) -> dict[int, set[str]]:
    with inspection_session_factory()() as db:
        found = db.execute(
            select(EvidenceSpan.finding_index, EvidenceSpan.id).where(
                EvidenceSpan.analysis_run_id == run_id,
                EvidenceSpan.finding_type == "extraction",
            )
        ).all()
    spans: dict[int, set[str]] = {}
    for index, span_id in found:
        spans.setdefault(index, set()).add(span_id)
    return spans


def _check_evidence(
    client, workspace_id: str, meeting_id: str, block: dict, *, current: bool, **params
) -> None:
    spans = _extraction_spans(block["analysis_run_id"])
    listed = [
        item["id"]
        for item in _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence", **params)[
            "items"
        ]
    ]
    cited: set[str] = set()
    for value in _values(block):
        ids = [ref["id"] for ref in value["evidence"]]
        assert len(ids) == len(set(ids)), ids
        assert set(ids) == spans.get(value["index"], set()), value["index"]
        assert ids == [span_id for span_id in listed if span_id in set(ids)], value["index"]
        cited |= set(ids)
        for ref in value["evidence"]:
            assert set(ref) == REF_KEYS, ref
            assert ref["is_current_revision"] is current, ref
            resolved = _get(
                client,
                workspace_id,
                f"/api/v1/meetings/{meeting_id}/evidence/{ref['id']}",
                **params,
            )
            assert (resolved["finding_type"], resolved["finding_index"]) == (
                "extraction",
                value["index"],
            )
            assert {key: resolved[key] for key in REF_KEYS} == ref
    assert cited == set().union(set(), *spans.values())


def _keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | set().union(*(_keys(item) for item in node.values()))
    if isinstance(node, list):
        return set().union(*(_keys(item) for item in node)) if node else set()
    return set()


def _finding_types(node) -> set[str]:
    if isinstance(node, dict):
        found = {node["finding_type"]} if isinstance(node.get("finding_type"), str) else set()
        return found.union(*(_finding_types(item) for item in node.values()))
    if isinstance(node, list):
        return set().union(*(_finding_types(item) for item in node)) if node else set()
    return set()


def _canonical(node):
    if isinstance(node, dict):
        return {
            key: sorted((_canonical(item) for item in item_list), key=_normalized)
            if key == "evidence" and isinstance(item_list, list)
            else _canonical(item_list)
            for key, item_list in node.items()
        }
    if isinstance(node, list):
        return [_canonical(item) for item in node]
    return node


def _normalized(value) -> str:
    return _GENERATED.sub("<generated>", json.dumps(value, ensure_ascii=False, sort_keys=True))


def _replace_row(workspace_id: str, meeting_id: str, **overrides) -> dict:
    row = _row(meeting_id)
    with inspection_session_factory()() as db:
        db.execute(
            delete(saas_models.AnalysisExtraction).where(
                saas_models.AnalysisExtraction.id == row["id"]
            )
        )
        db.commit()
    columns = {column: row[column] for column in (*ROW_COLUMNS, "valid", "needs_review")}
    columns.update(review_count=row["review_count"], result=row["result"])
    columns.update(overrides)
    _add_extraction(workspace_id, meeting_id, row["analysis_run_id"], **columns)
    return _row(meeting_id)


def test_the_report_carries_the_extraction_block_with_the_exact_keys(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, RICH)
    report = _report(client, workspace_id, meeting_id)
    assert report["report_version"] == 1
    assert set(report) == {
        "meeting_id",
        "report_version",
        "summary",
        "outcome",
        "qa",
        "review_actions",
        "completeness",
        "view",
        "privacy_enforced",
        "redaction",
        "extraction",
    }
    block = report["extraction"]
    assert isinstance(block, dict)
    assert set(block) == BLOCK_KEYS
    assert set(block["fields"]) == set(DIMENSIONS)
    for name in DIMENSIONS:
        assert set(block["fields"][name]) == FIELD_KEYS, name
    values = _values(block)
    assert values
    for value in values:
        assert set(value) == VALUE_KEYS, value
        for ref in value["evidence"]:
            assert set(ref) == REF_KEYS, ref
    summary_refs = report["summary"]["evidence"]
    assert summary_refs and all(set(ref) == REF_KEYS for ref in summary_refs)


def test_the_block_shows_the_current_runs_row_with_indexes_names_and_evidence(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, RICH)
    run = _current_run(meeting_id)
    row = _row(meeting_id)
    report = _report(client, workspace_id, meeting_id)
    block = report["extraction"]
    assert block["analysis_run_id"] == run.id == report["summary"]["analysis_run_id"]
    assert _without_evidence(block) == _expected_block(row)
    assert (block["status"], block["needs_review"], block["review_count"]) == ("available", True, 1)
    assert [(v["index"], v["label"]) for v in _values(block)] == [
        (0, "resolve_billing"),
        (1, "invoice"),
        (2, "payment"),
        (3, "phone"),
        (4, "date"),
    ]
    phone = block["fields"]["entity"]["values"][0]
    assert (phone["text"], phone["label_name_en"], phone["label_name_fa"]) == (
        PHONE,
        "Phone number",
        "شماره تلفن",
    )
    for name in ("intent", "topic"):
        assert all(v["text"] is None for v in block["fields"][name]["values"])
    _check_evidence(client, workspace_id, meeting_id, block, current=True)
    for value in _values(block):
        assert value["evidence"], value
        assert (value["evidence_status"], value["supported"]) == ("sufficient", True)
    assert "issues" not in _keys(block) and "withheld" not in _keys(block)


@pytest.mark.parametrize("source", ["legacy_result", "no_analysis"])
def test_no_row_is_an_unavailable_block(client, email_outbox, settings, monkeypatch, source):
    if source == "legacy_result":
        workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, LEGACY)
    else:
        _, workspace_id = _authenticated_workspace(client, email_outbox)
        monkeypatch.setattr(
            "backend.routers.v1.meetings.validate_media_upload",
            lambda *args, **kwargs: _valid_metadata(duration_seconds=10.0),
        )
        monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
        upload = _upload(client, workspace_id=workspace_id, key="hom53-report-silent")
        assert upload.status_code == 202, upload.text
        outcome = process_job(
            upload.json()["job"]["id"],
            worker_id="hom53-report-silent",
            settings=settings,
            providers=ProviderBundle(
                asr=SilentASRProvider(),
                diarization=SingleSpeakerDiarizationProvider(),
                intelligence=_Extracting(RICH),
            ),
        )
        assert outcome["status"] in {"completed", "partial"}, outcome
        meeting_id = upload.json()["meeting"]["id"]
    assert _rows(meeting_id) == []
    report = _report(client, workspace_id, meeting_id)
    assert report["extraction"] == _unavailable_block()


def test_a_mock_extraction_is_an_available_block_with_the_row_columns(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(client, email_outbox, settings, monkeypatch)
    row = _row(meeting_id)
    block = _report(client, workspace_id, meeting_id)["extraction"]
    assert block["status"] == "available"
    assert block["analysis_run_id"] == row["analysis_run_id"] == _current_run(meeting_id).id
    assert {column: block[column] for column in ROW_COLUMNS} == {
        column: row[column] for column in ROW_COLUMNS
    }
    assert block == _expected_block(row)
    for name in DIMENSIONS:
        assert block["fields"][name] == {
            "status": "not_stated",
            "abstain_reason": None,
            "review_required": False,
            "values": [],
        }


class _Down(MockIntelligenceProvider):
    async def analyze(self, request):
        raise ProviderError("provider_unavailable", "The analysis provider is down")


@pytest.mark.parametrize("down", [True, False], ids=["analysis_failed", "analysis_control"])
def test_a_full_job_whose_analysis_failed_is_an_unavailable_block(
    client, email_outbox, settings, monkeypatch, down
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=10.0),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
    upload = _upload(client, workspace_id=workspace_id, key="hom53-report-down")
    assert upload.status_code == 202, upload.text
    outcome = process_job(
        upload.json()["job"]["id"],
        worker_id="hom53-report-down",
        settings=settings,
        providers=ProviderBundle(
            asr=_ScriptASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=_Down(allow_mock=True) if down else _Extracting(RICH),
        ),
    )
    meeting_id = upload.json()["meeting"]["id"]
    if down:
        assert outcome["status"] in {"partial", "failed"}, outcome
        assert _rows(meeting_id) == []
    else:
        assert outcome["status"] == "completed", outcome
    response = _report_response(client, workspace_id, meeting_id)
    assert response.status_code == 200, (outcome, response.text)
    block = response.json()["extraction"]
    if down:
        assert block == _unavailable_block()
    else:
        assert _without_evidence(block) == _expected_block(_row(meeting_id))


@pytest.mark.parametrize(
    ("change", "rejected"),
    [
        pytest.param(
            lambda e: e["entity"]["values"][0].update(text="09120000000"),
            {"entity"},
            id="entity_text_not_in_the_turn",
        ),
        pytest.param(lambda e: e.update(estimated_nps=9), set(), id="unknown_top_level_key"),
    ],
)
def test_an_invalid_row_is_invalid_and_shows_only_its_accepted_fields(
    client, email_outbox, settings, monkeypatch, change, rejected
):
    workspace_id = _recompute_workspace(client, email_outbox, monkeypatch)
    extraction = _changed(change)
    invalid_id = _meeting(client, workspace_id, settings, key="hom53-invalid", build=extraction)
    row = _row(invalid_id)
    assert row["valid"] is False
    verdicts = {n: row["result"]["fields"][n]["verdict"] for n in DIMENSIONS}
    assert {n for n, verdict in verdicts.items() if verdict == "rejected"} == rejected
    report = _report(client, workspace_id, invalid_id)
    block = report["extraction"]
    assert block["status"] == "invalid"
    assert _without_evidence(block) == _expected_block(row)
    for name in rejected:
        assert block["fields"][name] == {
            "status": "unavailable",
            "abstain_reason": None,
            "review_required": False,
            "values": [],
        }
    assert [v["label"] for v in block["fields"]["topic"]["values"]] == ["invoice", "payment"]
    _check_evidence(client, workspace_id, invalid_id, block, current=True)
    assert "issues" not in _keys(block)
    assert "estimated_nps" not in json.dumps(report, ensure_ascii=False)
    assert "entity_text_not_found" not in json.dumps(report, ensure_ascii=False)

    control = copy.deepcopy(extraction)
    control.pop("estimated_nps", None)
    if rejected:
        control["entity"] = copy.deepcopy(NOT_STATED)
    valid_id = _meeting(client, workspace_id, settings, key="hom53-valid", build=control)
    valid_row = _row(valid_id)
    assert valid_row["valid"] is True
    valid = _report(client, workspace_id, valid_id)["extraction"]
    assert valid["status"] == "available"
    for name in set(DIMENSIONS) - rejected:
        assert _without_evidence(valid)["fields"][name] == _without_evidence(block)["fields"][name]


@pytest.mark.parametrize(
    ("value", "kept"),
    [
        pytest.param(("entity", _value("phone", [0], 0.49, text=PHONE)), False, id="explicit_0_49"),
        pytest.param(("entity", _value("phone", [0], 0.5, text=PHONE)), True, id="explicit_0_5"),
        pytest.param(("entity", _value("phone", [0], None, text=PHONE)), False, id="no_confidence"),
        pytest.param(("topic", _value("refund", [1], 0.69, "inferred")), False, id="inferred_0_69"),
        pytest.param(("topic", _value("refund", [1], 0.7, "inferred")), True, id="inferred_0_7"),
    ],
)
def test_a_withheld_value_is_never_shown_and_a_value_at_the_threshold_is(
    client, email_outbox, settings, monkeypatch, value, kept
):
    name, stated = value
    workspace_id, meeting_id, _ = _run(
        client, email_outbox, settings, monkeypatch, _extraction(**{name: _stated(stated)})
    )
    row = _row(meeting_id)
    report = _report(client, workspace_id, meeting_id)
    block = report["extraction"]
    assert block["status"] == "available"
    assert _without_evidence(block) == _expected_block(row)
    values = block["fields"][name]["values"]
    if kept:
        assert [(v["index"], v["label"], v["confidence"]) for v in values] == [
            (0, stated["label"], stated["confidence"])
        ]
        _check_evidence(client, workspace_id, meeting_id, block, current=True)
    else:
        assert values == [] and _values(block) == []
        assert "withheld" not in _keys(block)
        assert stated["label"] not in json.dumps(block, ensure_ascii=False)
        assert PHONE not in json.dumps(block, ensure_ascii=False)
    critical = kept and name == "entity"
    assert (block["needs_review"], block["review_count"]) == (critical, int(critical))
    assert block["fields"][name]["review_required"] is critical


@pytest.mark.parametrize(
    ("name", "value", "critical"),
    [
        pytest.param("entity", _value("phone", [0], 0.9, text=PHONE), True, id="entity_phone"),
        pytest.param("intent", _value("cancel_service", [1], 0.9), True, id="intent_cancel"),
        pytest.param("entity", _value("date", [2], 0.9, text="فردا"), False, id="entity_date"),
        pytest.param("intent", _value("request_support", [1], 0.9), False, id="intent_support"),
    ],
)
def test_a_kept_critical_value_is_for_review_and_a_non_critical_one_is_not(
    client, email_outbox, settings, monkeypatch, name, value, critical
):
    workspace_id, meeting_id, _ = _run(
        client, email_outbox, settings, monkeypatch, _extraction(**{name: _stated(value)})
    )
    row = _row(meeting_id)
    block = _report(client, workspace_id, meeting_id)["extraction"]
    assert _without_evidence(block) == _expected_block(row)
    (shown,) = block["fields"][name]["values"]
    assert (shown["review"], shown["review_reason"], shown["critical"]) == (
        ("required", "critical_label", True) if critical else ("not_required", None, False)
    )
    assert block["fields"][name]["review_required"] is critical
    assert (block["needs_review"], block["review_count"]) == (critical, int(critical))
    if name == "entity":
        assert shown["text"] == value["text"]
    else:
        assert shown["text"] is None


@pytest.mark.parametrize(
    ("fields", "name", "status", "reason"),
    [
        pytest.param(
            {"topic": {"status": "insufficient_evidence", "values": []}},
            "topic",
            "insufficient_evidence",
            "model",
            id="model_insufficient_evidence",
        ),
        pytest.param(
            {"intent": {"status": "contradictory", "values": []}},
            "intent",
            "contradictory",
            "model",
            id="model_contradictory",
        ),
        pytest.param(
            {
                "intent": _stated(
                    _value("resolve_billing", [1], 0.9), _value("cancel_service", [1], 0.9)
                )
            },
            "intent",
            "contradictory",
            "contradictory",
            id="two_single_values",
        ),
        pytest.param(
            {"outcome": _stated(_value("follow_up_scheduled", [2], 0.4))},
            "outcome",
            None,
            "low_confidence",
            id="low_confidence",
        ),
        pytest.param({}, "topic", "not_stated", None, id="not_stated_control"),
    ],
)
def test_an_abstained_field_is_shown_as_stored_never_as_not_stated(
    client, email_outbox, settings, monkeypatch, fields, name, status, reason
):
    workspace_id, meeting_id, _ = _run(
        client, email_outbox, settings, monkeypatch, _extraction(**fields)
    )
    row = _row(meeting_id)
    stored = row["result"]["fields"][name]
    if status is not None:
        assert stored["status"] == status
    block = _report(client, workspace_id, meeting_id)["extraction"]
    assert _without_evidence(block) == _expected_block(row)
    field = block["fields"][name]
    assert (field["status"], field["abstain_reason"], field["values"]) == (
        stored["status"],
        reason,
        [],
    )
    if reason is not None:
        assert field["status"] != "not_stated"


def test_qa_completeness_and_summary_are_unchanged_by_the_block(
    client, email_outbox, settings, monkeypatch
):
    workspace_id = _recompute_workspace(client, email_outbox, monkeypatch)
    with_block = _meeting(client, workspace_id, settings, key="hom53-same-a", build=RICH)
    without = _meeting(client, workspace_id, settings, key="hom53-same-b", build=LEGACY)
    first = _report(client, workspace_id, with_block)
    second = _report(client, workspace_id, without)
    for key in UNCHANGED:
        assert _normalized(_canonical(first[key])) == _normalized(_canonical(second[key])), key
    assert first["extraction"]["status"] == "available"
    assert second["extraction"]["status"] == "unavailable"
    extraction_ids = set().union(*_extraction_spans(_current_run(with_block).id).values())
    assert extraction_ids
    assert "extraction" not in _finding_types(first["qa"])
    qa_refs = {ref["id"] for f in first["qa"]["findings"] for ref in f["evidence"]}
    assert not qa_refs & extraction_ids
    assert first["qa"]["counts"]["total"] == len(first["qa"]["findings"])


def test_a_redacted_view_shows_placeholders_and_the_original_view_the_stored_text(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hom53-report-view")
    accepted = _tenant_upload(client, tenant, key="hom53-report-view-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    outcome = process_job(
        accepted.json()["job"]["id"],
        worker_id="hom53-report-view",
        settings=settings,
        providers=ProviderBundle(
            asr=_SegmentedASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=_Extracting(ENTITIES),
        ),
    )
    assert outcome["status"] == "completed", outcome
    row = _row(meeting_id)
    assert row["valid"] is True
    assert [v["text"] for v in row["result"]["fields"]["entity"]["values"]] == [
        NID_FA,
        EMAIL,
        SEGMENT_PHONE,
    ]
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    base = f"/api/v1/meetings/{meeting_id}"

    def read(path: str, **params):
        return client.get(
            f"{base}/{path}", params=params, headers=_headers(tenant), cookies=tenant["cookies"]
        )

    redacted = read("report")
    assert redacted.status_code == 200, redacted.text
    body = redacted.json()
    assert (body["view"], body["privacy_enforced"]) == ("redacted", True)
    assert _no_raw(redacted.text), redacted.text
    assert body["summary"]["overview"] == read("summary").json()["overview"]
    segments = [s["text"] for s in read("transcript").json()["segments"]]
    assert _no_raw(json.dumps(segments, ensure_ascii=False))
    shown = body["extraction"]["fields"]["entity"]["values"]
    assert [v["label"] for v in shown] == ["national_id", "email", "phone"]
    for value, turn in zip(shown, (0, 1, 1), strict=True):
        assert PLACEHOLDER.fullmatch(value["text"]), value["text"]
        assert value["text"] in PLACEHOLDER.findall(segments[turn]), (value["text"], turn)
    assert read("report").json()["extraction"] == body["extraction"]

    with inspection_session_factory()() as db:
        before = set(db.scalars(select(AuditEvent.id).where(AuditEvent.target_id == meeting_id)))
    original = read("report", view="original")
    assert original.status_code == 200, original.text
    assert original.json()["view"] == "original"
    assert [v["text"] for v in original.json()["extraction"]["fields"]["entity"]["values"]] == [
        NID_FA,
        EMAIL,
        SEGMENT_PHONE,
    ]
    with inspection_session_factory()() as db:
        added = [
            (event.event_type, event.details.get("surface"))
            for event in db.scalars(select(AuditEvent).where(AuditEvent.target_id == meeting_id))
            if event.id not in before
        ]
    assert added == [("transcript.original_accessed", "report")]


def test_an_entity_judged_on_redacted_turns_shows_its_stored_placeholder_in_both_views(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, intelligence = _redaction_run(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom53-report-redacted-run",
        _quote_placeholder,
        policy=True,
    )
    placeholder = _placeholder(intelligence.requests[-1])
    assert re.fullmatch(r"\[PHONE_\d+\]", placeholder)
    for params in ({}, {"view": "original"}):
        response = client.get(
            f"/api/v1/meetings/{meeting_id}/report",
            params=params,
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert response.status_code == 200, response.text
        (value,) = response.json()["extraction"]["fields"]["entity"]["values"]
        assert (value["label"], value["text"], value["review"]) == (
            "phone",
            placeholder,
            "required",
        )
        if not params:
            assert _no_raw(response.text), response.text


def test_the_tenant_report_has_the_block_for_a_shared_meeting(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, RICH)
    share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_write_headers(client, workspace_id),
        json={"expires_in_hours": 24, "require_authentication": True, "allow_export": True},
    )
    assert share.status_code == 201, share.text
    block = _report(client, workspace_id, meeting_id)["extraction"]
    assert block["status"] == "available" and _values(block)


def test_share_surfaces_carry_no_extraction_block_or_span(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, RICH)
    assert _extraction_spans(_current_run(meeting_id).id)
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_write_headers(client, workspace_id),
        json={"public_share_links_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_write_headers(client, workspace_id),
        json={"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
    )
    assert share.status_code == 201, share.text
    token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]
    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{token}")
    assert shared.status_code == 200, shared.text
    exported = client.get(f"/api/v1/shares/{token}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    for body in (shared.json(), exported.json()):
        assert "extraction" not in _keys(body)
        assert "extraction" not in _finding_types(body)
    span_ids = set().union(*_extraction_spans(_current_run(meeting_id).id).values())
    for text in (shared.text, exported.text):
        assert not any(span_id in text for span_id in span_ids)


def test_another_workspace_gets_404_and_the_owner_gets_the_block(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, RICH)
    assert _report(client, workspace_id, meeting_id)["extraction"]["status"] == "available"
    outsider = register_user(client, email_outbox, email="hom53-report-outsider@example.com")
    other_workspace = login_user(client, outsider)["workspaces"][0]["id"]
    denied = _report_response(client, other_workspace, meeting_id)
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "meeting_not_found"
    assert "extraction" not in denied.text


def test_a_stale_analysis_shows_the_current_row_with_old_revision_refs(
    client, email_outbox, settings, monkeypatch
):
    monkeypatch.setattr(settings, "recompute_on_correction", False)
    workspace_id = _recompute_workspace(client, email_outbox, monkeypatch)
    meeting_id = _mock_asr_meeting(
        client, workspace_id, settings, key="hom53-report-stale", transcript=ORIGINAL
    )
    row = _row(meeting_id)
    fresh = _report(client, workspace_id, meeting_id)["extraction"]
    assert _without_evidence(fresh) == _expected_block(row)
    _check_evidence(client, workspace_id, meeting_id, fresh, current=True)

    _correct(client, workspace_id, meeting_id, CORRECTED)
    report = _report(client, workspace_id, meeting_id)
    stale = report["extraction"]
    assert _current_run(meeting_id).id == row["analysis_run_id"]
    assert _without_evidence(stale) == _without_evidence(fresh)
    _check_evidence(client, workspace_id, meeting_id, stale, current=False)
    assert report["completeness"]["notices"].count("analysis_stale") == 1
    assert not any("extraction" in notice for notice in report["completeness"]["notices"])


def test_a_failed_recompute_keeps_the_old_row_and_a_successful_one_shows_the_new_row(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    monkeypatch.setattr(settings, "recompute_on_correction", False)
    workspace_id = _recompute_workspace(client, email_outbox, monkeypatch)
    meeting_id = _mock_asr_meeting(
        client, workspace_id, settings, key="hom53-report-recompute", transcript=ORIGINAL
    )
    old_run = _current_run(meeting_id)
    old_row = _row(meeting_id)
    corrected = _correct(client, workspace_id, meeting_id, CORRECTED)

    analysis_provider["intelligence"] = _Failing(allow_mock=True)
    outcome = recompute.recompute_meeting_analysis(
        meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "failed"
    with inspection_session_factory()() as db:
        failed = set(
            db.scalars(
                select(AnalysisRun.id).where(
                    AnalysisRun.meeting_id == meeting_id, AnalysisRun.state == StageState.FAILED
                )
            )
        )
    assert len(failed) == 1
    kept = _report(client, workspace_id, meeting_id)["extraction"]
    assert kept["analysis_run_id"] == old_run.id and kept["analysis_run_id"] not in failed
    assert _without_evidence(kept) == _expected_block(old_row)
    assert [v["label"] for v in kept["fields"]["topic"]["values"]] == ["contract"]
    _check_evidence(client, workspace_id, meeting_id, kept, current=False)

    analysis_provider["intelligence"] = _Extracting(_by_revision)
    outcome = recompute.recompute_meeting_analysis(
        meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
    )
    assert outcome == "completed"
    run = _current_run(meeting_id)
    assert run.id != old_run.id
    rows = {row["analysis_run_id"]: row for row in _rows(meeting_id)}
    assert set(rows) == {old_run.id, run.id}
    report = _report(client, workspace_id, meeting_id)
    shown = report["extraction"]
    assert shown["analysis_run_id"] == run.id == report["summary"]["analysis_run_id"]
    assert _without_evidence(shown) == _expected_block(rows[run.id])
    assert [v["label"] for v in shown["fields"]["topic"]["values"]] == ["invoice"]
    _check_evidence(client, workspace_id, meeting_id, shown, current=True)
    old_ids = set().union(*_extraction_spans(old_run.id).values())
    shown_ids = {ref["id"] for value in _values(shown) for ref in value["evidence"]}
    assert old_ids and not old_ids & shown_ids


def test_a_correction_with_a_recompute_shows_the_new_row(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    workspace_id = _recompute_workspace(client, email_outbox, monkeypatch)
    meeting_id = _mock_asr_meeting(
        client, workspace_id, settings, key="hom53-report-auto", transcript=ORIGINAL
    )
    old_run = _current_run(meeting_id)
    _correct(client, workspace_id, meeting_id, CORRECTED)
    run = _current_run(meeting_id)
    assert run.id != old_run.id
    shown = _report(client, workspace_id, meeting_id)["extraction"]
    assert shown["analysis_run_id"] == run.id
    assert [v["label"] for v in shown["fields"]["topic"]["values"]] == ["invoice"]
    _check_evidence(client, workspace_id, meeting_id, shown, current=True)


def test_a_row_of_a_non_current_run_is_never_shown_and_the_current_runs_row_is(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, LEGACY)
    assert _rows(meeting_id) == []
    older = _add_run(workspace_id, meeting_id)
    _add_extraction(workspace_id, meeting_id, older)
    assert _report(client, workspace_id, meeting_id)["extraction"] == _unavailable_block()

    current = _current_run(meeting_id).id
    _add_extraction(workspace_id, meeting_id, current)
    rows = {row["analysis_run_id"]: row for row in _rows(meeting_id)}
    block = _report(client, workspace_id, meeting_id)["extraction"]
    assert block["analysis_run_id"] == current
    assert block == _expected_block(rows[current])


@pytest.mark.parametrize(
    ("overrides", "names"),
    [
        pytest.param({"taxonomy_version": 2}, False, id="unknown_version"),
        pytest.param({"taxonomy_digest": "0" * 64}, False, id="digest_mismatch"),
        pytest.param({"taxonomy_id": "support_calls"}, False, id="unknown_taxonomy"),
        pytest.param({}, True, id="normal_row"),
    ],
)
def test_names_come_from_the_rows_taxonomy_version_or_are_null(
    client, email_outbox, settings, monkeypatch, overrides, names
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, RICH)
    row = _replace_row(workspace_id, meeting_id, **overrides)
    response = _report_response(client, workspace_id, meeting_id)
    assert response.status_code == 200, response.text
    block = response.json()["extraction"]
    assert _without_evidence(block) == _expected_block(row, names=names)
    assert {column: block[column] for column in ROW_COLUMNS} == {
        column: row[column] for column in ROW_COLUMNS
    }
    values = _values(block)
    assert len(values) == 5
    assert all((v["label_name_en"] is None) is (not names) for v in values)
    assert all((v["label_name_fa"] is None) is (not names) for v in values)
    _check_evidence(client, workspace_id, meeting_id, block, current=True)


def test_a_kept_value_without_spans_is_insufficient_and_unsupported(
    client, email_outbox, settings, monkeypatch
):
    workspace_id, meeting_id, _ = _run(client, email_outbox, settings, monkeypatch, LEGACY)
    run_id = _current_run(meeting_id).id
    assert _rows(meeting_id) == [] and _extraction_spans(run_id) == {}
    result = _expected(RICH, TURNS)
    _add_extraction(
        workspace_id,
        meeting_id,
        run_id,
        **{column: result[column] for column in ROW_COLUMNS},
        valid=result["valid"],
        needs_review=result["needs_review"],
        review_count=1,
        result=result,
    )
    row = _row(meeting_id)
    block = _report(client, workspace_id, meeting_id)["extraction"]
    assert _without_evidence(block) == _expected_block(row)
    values = _values(block)
    assert len(values) == 5
    for value in values:
        assert (value["evidence_status"], value["supported"], value["evidence"]) == (
            "insufficient",
            False,
            [],
        )


def _redacted_later(client, email_outbox, monkeypatch, settings, extraction) -> tuple[dict, str]:
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hom53-report-span")
    accepted = _tenant_upload(client, tenant, key="hom53-report-span-0001")
    assert accepted.status_code == 202, accepted.text
    outcome = process_job(
        accepted.json()["job"]["id"],
        worker_id="hom53-report-span",
        settings=settings,
        providers=ProviderBundle(
            asr=_SegmentedASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=_Extracting(extraction),
        ),
    )
    assert outcome["status"] == "completed", outcome
    return tenant, accepted.json()["meeting"]["id"]


def _tenant_read(client, tenant: dict, path: str, **params):
    return client.get(path, params=params, headers=_headers(tenant), cookies=tenant["cookies"])


@pytest.mark.parametrize(
    ("label", "turn", "quoted", "kind"),
    [
        pytest.param("phone", 0, "۰۹۱۲ ۳۴۵", "PHONE", id="phone_two_tokens"),
        pytest.param("phone", 0, "۰۹۱۲", "PHONE", id="phone_one_token"),
        pytest.param("email", 1, "example.com", "EMAIL", id="email_domain"),
        pytest.param("phone", 0, "۰۹۱۲ ۳۴۵ ۶۷۸۹", "PHONE", id="phone_full_control"),
        pytest.param("email", 1, EMAIL, "EMAIL", id="email_full_control"),
        pytest.param("person_name", 0, "علی", None, id="non_pii_control"),
    ],
)
def test_a_redacted_view_renders_the_quoted_region_with_whole_placeholders(
    client, email_outbox, monkeypatch, settings, label, turn, quoted, kind
):
    extraction = _extraction(entity=_stated(_value(label, [turn], 0.9, text=quoted)))
    tenant, meeting_id = _redacted_later(client, email_outbox, monkeypatch, settings, extraction)
    row = _row(meeting_id)
    assert row["valid"] is True
    (stored,) = row["result"]["fields"]["entity"]["values"]
    assert (stored["label"], stored["text"], stored["source_turn_indexes"]) == (
        label,
        quoted,
        [turn],
    )
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    base = f"/api/v1/meetings/{meeting_id}"
    segments = [
        s["text"] for s in _tenant_read(client, tenant, f"{base}/transcript").json()["segments"]
    ]

    redacted = _tenant_read(client, tenant, f"{base}/report")
    assert redacted.status_code == 200, redacted.text
    assert redacted.json()["view"] == "redacted"
    assert _no_raw(redacted.text), redacted.text
    (value,) = redacted.json()["extraction"]["fields"]["entity"]["values"]
    if kind is None:
        assert value["text"] == quoted
    else:
        assert PLACEHOLDER.fullmatch(value["text"]), value["text"]
        assert value["text"].startswith(f"[{kind}_"), value["text"]
        assert value["text"] in PLACEHOLDER.findall(segments[turn]), (value["text"], segments)
    assert all(_no_raw(ref["text"]) for ref in value["evidence"])
    assert (
        _tenant_read(client, tenant, f"{base}/report").json()["extraction"]
        == (redacted.json()["extraction"])
    )

    original = _tenant_read(client, tenant, f"{base}/report", view="original")
    assert original.status_code == 200, original.text
    assert original.json()["view"] == "original"
    (value,) = original.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == quoted


@pytest.mark.parametrize(
    ("label", "turns", "quoted", "kind", "found"),
    [
        pytest.param("email", [0, 1], EMAIL, "EMAIL", 1, id="email_found_in_the_second_cited_turn"),
        pytest.param(
            "person_name", [1, 0], "علی", None, 0, id="name_found_in_the_second_cited_turn"
        ),
    ],
)
def test_a_redacted_view_uses_the_first_cited_turn_that_holds_the_region(
    client, email_outbox, monkeypatch, settings, label, turns, quoted, kind, found
):
    extraction = _extraction(entity=_stated(_value(label, turns, 0.9, text=quoted)))
    tenant, meeting_id = _redacted_later(client, email_outbox, monkeypatch, settings, extraction)
    row = _row(meeting_id)
    assert row["valid"] is True
    (stored,) = row["result"]["fields"]["entity"]["values"]
    assert (stored["text"], stored["source_turn_indexes"]) == (quoted, turns)
    assert _current_run(meeting_id).input_view == "original"
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    base = f"/api/v1/meetings/{meeting_id}"
    segments = [
        s["text"] for s in _tenant_read(client, tenant, f"{base}/transcript").json()["segments"]
    ]

    redacted = _tenant_read(client, tenant, f"{base}/report")
    assert redacted.status_code == 200, redacted.text
    assert _no_raw(redacted.text), redacted.text
    (value,) = redacted.json()["extraction"]["fields"]["entity"]["values"]
    if kind is None:
        assert value["text"] == quoted
    else:
        assert PLACEHOLDER.fullmatch(value["text"]), value["text"]
        assert value["text"].startswith(f"[{kind}_"), value["text"]
        assert value["text"] in PLACEHOLDER.findall(segments[found]), (value["text"], segments)

    original = _tenant_read(client, tenant, f"{base}/report", view="original")
    assert original.status_code == 200, original.text
    (value,) = original.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == quoted


@pytest.mark.parametrize(
    ("label", "turn", "quoted", "kind"),
    [
        pytest.param("phone", 0, "0912 345", "PHONE", id="phone_fragment_in_ascii_digits"),
        pytest.param("phone", 1, "09123456789", "PHONE", id="phone_in_ascii_digits"),
        pytest.param("email", 1, "ALI.REZAEI@EXAMPLE.COM", "EMAIL", id="email_in_capitals"),
    ],
)
def test_a_redacted_view_never_shows_a_spelling_the_validator_folded(
    client, email_outbox, monkeypatch, settings, label, turn, quoted, kind
):
    extraction = _extraction(entity=_stated(_value(label, [turn], 0.9, text=quoted)))
    tenant, meeting_id = _redacted_later(client, email_outbox, monkeypatch, settings, extraction)
    row = _row(meeting_id)
    assert row["valid"] is True
    (stored,) = row["result"]["fields"]["entity"]["values"]
    assert stored["text"] == quoted and quoted not in SEGMENTS[turn]
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    base = f"/api/v1/meetings/{meeting_id}"
    segments = [
        s["text"] for s in _tenant_read(client, tenant, f"{base}/transcript").json()["segments"]
    ]

    redacted = _tenant_read(client, tenant, f"{base}/report")
    assert redacted.status_code == 200, redacted.text
    assert _no_raw(redacted.text), redacted.text
    assert quoted not in redacted.text
    (value,) = redacted.json()["extraction"]["fields"]["entity"]["values"]
    if value["text"] is not None:
        assert PLACEHOLDER.fullmatch(value["text"]), value["text"]
        assert value["text"].startswith(f"[{kind}_"), value["text"]
        assert value["text"] in PLACEHOLDER.findall(segments[turn]), (value["text"], segments)

    original = _tenant_read(client, tenant, f"{base}/report", view="original")
    assert original.status_code == 200, original.text
    (value,) = original.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == quoted


def test_a_stored_text_no_cited_turn_holds_is_null_under_a_redacted_view(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_later(client, email_outbox, monkeypatch, settings, LEGACY)
    assert _rows(meeting_id) == []
    run = _current_run(meeting_id)
    assert run.input_view == "original"
    absent = "۰۹۱۲۰۰۰۰۰۰۰"
    assert all(absent not in segment for segment in SEGMENTS)
    result = _expected(
        _extraction(entity=_stated(_value("phone", [1], 0.9, text=SEGMENT_PHONE))), SEGMENTS
    )
    result["fields"]["entity"]["values"][0]["text"] = absent
    _add_extraction(
        tenant["workspace_id"],
        meeting_id,
        run.id,
        **{column: result[column] for column in ROW_COLUMNS},
        valid=True,
        needs_review=True,
        review_count=1,
        result=result,
    )
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    base = f"/api/v1/meetings/{meeting_id}"

    redacted = _tenant_read(client, tenant, f"{base}/report")
    assert redacted.status_code == 200, redacted.text
    assert _no_raw(redacted.text), redacted.text
    (value,) = redacted.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] is None

    original = _tenant_read(client, tenant, f"{base}/report", view="original")
    assert original.status_code == 200, original.text
    (value,) = original.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == absent


ROOM_THEN_PHONE = ("اتاق ۳۴۵ است و موبایل من ۰۹۱۲ ۳۴۵ ۶۷۸۹ است", "صورتحساب این ماه", "فردا")
ROOM_TURN_PHONE_TURN = ("اتاق ۳۴۵ است", "موبایل من ۰۹۱۲ ۳۴۵ ۶۷۸۹ است", "فردا")
CODE_TWICE = ("کد 48213 ثبت شد و کد 48213 را دوباره گفتم", "صورتحساب این ماه", "فردا")
TWO_SPELLINGS = ("اتاق ۳۴۵ است و طبقه 345 است", "صورتحساب این ماه", "فردا")
ROOM_AND_OTHER_PHONE = ("اتاق ۳۴۵ است و موبایل من ۰۹۱۲ ۷۷۷ ۶۷۸۹ است", "صورتحساب این ماه", "فردا")


@pytest.mark.parametrize(
    ("turns", "label", "cited", "quoted", "expected", "found"),
    [
        pytest.param(ROOM_THEN_PHONE, "phone", [0], "۳۴۵", "PHONE", 0, id="room_then_phone"),
        pytest.param(ROOM_THEN_PHONE, "phone", [0], "345", "PHONE", 0, id="room_then_phone_ascii"),
        pytest.param(
            ROOM_TURN_PHONE_TURN, "phone", [0, 1], "۳۴۵", "PHONE", 1, id="room_turn_then_phone_turn"
        ),
        pytest.param(CODE_TWICE, "reference_id", [0], "48213", "48213", 0, id="non_pii_twice"),
        pytest.param(
            TWO_SPELLINGS, "reference_id", [0], "345", "۳۴۵", 0, id="first_of_two_spellings"
        ),
        pytest.param(
            ROOM_AND_OTHER_PHONE, "phone", [0], "۳۴۵", "۳۴۵", 0, id="only_spot_outside_the_phone"
        ),
    ],
)
def test_a_redacted_view_prefers_the_first_region_that_renders_a_placeholder(
    client, email_outbox, settings, monkeypatch, turns, label, cited, quoted, expected, found
):
    extraction = _extraction(entity=_stated(_value(label, cited, 0.9, text=quoted)))
    workspace_id, meeting_id, _ = _run(
        client, email_outbox, settings, monkeypatch, extraction, turns=turns
    )
    row = _row(meeting_id)
    assert row["valid"] is True
    (stored,) = row["result"]["fields"]["entity"]["values"]
    assert (stored["text"], stored["source_turn_indexes"]) == (quoted, cited)
    assert _current_run(meeting_id).input_view == "original"
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_write_headers(client, workspace_id),
        json={"pii_redaction_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    base = f"/api/v1/meetings/{meeting_id}"
    segments = [s["text"] for s in _get(client, workspace_id, f"{base}/transcript")["segments"]]
    if "۰۹۱۲" in "".join(turns):
        assert any(PLACEHOLDER.search(segment) for segment in segments), segments

    response = _report_response(client, workspace_id, meeting_id)
    assert response.status_code == 200, response.text
    assert response.json()["view"] == "redacted"
    assert _no_raw(response.text), response.text
    (value,) = response.json()["extraction"]["fields"]["entity"]["values"]
    if expected == "PHONE":
        assert PLACEHOLDER.fullmatch(value["text"]), value["text"]
        assert value["text"].startswith("[PHONE_"), value["text"]
        assert value["text"] in PLACEHOLDER.findall(segments[found]), (value["text"], segments)
    else:
        assert value["text"] == expected

    original = _report(client, workspace_id, meeting_id, view="original")
    (value,) = original["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == quoted


EMAIL_THEN_PHONE = (
    f"ایمیل {EMAIL} و موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹ است",
    "صورتحساب این ماه",
    "فردا",
)
QUOTED_FROM_EMAIL_ONLY = "[EMAIL_1] و موبایل ۰۹۱۲"


@pytest.mark.parametrize(
    ("classes_after", "expected"),
    [
        pytest.param(["email", "phone"], None, id="classes_added_after_the_analysis"),
        pytest.param(["email"], QUOTED_FROM_EMAIL_ONLY, id="same_policy_control"),
    ],
)
def test_the_redacted_input_fallback_needs_the_policy_the_run_was_redacted_with(
    client, email_outbox, monkeypatch, settings, classes_after, expected
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "hom53-report-policy")
    assert (
        _set_policy(
            client, tenant, pii_redaction_enabled=True, pii_redaction_classes=["email"]
        ).status_code
        == 200
    )
    accepted = _tenant_upload(client, tenant, key="hom53-report-policy-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    intelligence = _Extracting(
        _extraction(entity=_stated(_value("phone", [0], 0.9, text=QUOTED_FROM_EMAIL_ONLY)))
    )
    outcome = process_job(
        accepted.json()["job"]["id"],
        worker_id="hom53-report-policy",
        settings=settings,
        providers=ProviderBundle(
            asr=_ScriptASR(EMAIL_THEN_PHONE),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=intelligence,
        ),
    )
    assert outcome["status"] == "completed", outcome
    seen = intelligence.requests[-1].turns[0].text
    assert "[EMAIL_1]" in seen and "۰۹۱۲ ۳۴۵ ۶۷۸۹" in seen, seen
    assert _current_run(meeting_id).input_view != "original"
    row = _row(meeting_id)
    assert row["valid"] is True
    (stored,) = row["result"]["fields"]["entity"]["values"]
    assert stored["text"] == QUOTED_FROM_EMAIL_ONLY
    if classes_after != ["email"]:
        assert _set_policy(client, tenant, pii_redaction_classes=classes_after).status_code == 200
    base = f"/api/v1/meetings/{meeting_id}"
    transcript = _tenant_read(client, tenant, f"{base}/transcript").json()["segments"][0]["text"]
    assert "[EMAIL_" in transcript
    assert ("[PHONE_" in transcript) is ("phone" in classes_after)

    redacted = _tenant_read(client, tenant, f"{base}/report")
    assert redacted.status_code == 200, redacted.text
    assert redacted.json()["view"] == "redacted"
    (value,) = redacted.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == expected
    if "phone" in classes_after:
        assert _no_raw(redacted.text), redacted.text

    original = _tenant_read(client, tenant, f"{base}/report", view="original")
    assert original.status_code == 200, original.text
    (value,) = original.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == QUOTED_FROM_EMAIL_ONLY
