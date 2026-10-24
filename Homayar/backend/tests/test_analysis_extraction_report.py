"""HOM-53 part 3: the extraction block of the call report.

``GET /meetings/{id}/report`` shows the stored extraction of the current analysis
run as one ``extraction`` block: the block status, the review counts, the versions,
and per field its status and kept values with their evidence.  Withheld values and
validator issues are never shown.  The block adds nothing to the QA findings, the
notices or the review actions, and it stays off every share surface.  Every meeting
here goes through the real full job (and recompute) with fake providers.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import pytest
from sqlalchemy import delete, func, select, update

from backend.analysis_taxonomy import POLICY_V1, POLICY_VERSION, VALIDATOR_VERSION, policy_digest
from backend.database.saas_models import AnalysisExtraction, AuditEvent, EvidenceSpan, StageState
from backend.jobs import recompute
from backend.jobs.processor import process_job
from backend.providers.base import ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRResult, ASRSegment
from backend.services import analysis_extraction
from backend.services.analysis_extraction import quoted_region, quoted_regions
from backend.tests._database import inspection_session_factory
from backend.tests.test_analysis_extraction_stage import (
    MIXED,
    PHONE,
    TAXONOMY,
    TUESDAY,
    _current_run,
    _Extracting,
    _extraction,
    _fixed,
    _phone_quote,
    _process,
    _rows,
    _stated,
    _value,
    analysis_provider,  # noqa: F401
    settings,  # noqa: F401
)
from backend.tests.test_analysis_recompute import ORIGINAL_TEXT, _correct, _FailingIntelligence
from backend.tests.test_call_report import _report
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_job_processor import SilentASRProvider, SingleSpeakerDiarizationProvider
from backend.tests.test_privacy_routing import SEGMENTS, _no_raw, _SegmentedASR, _set_policy

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
FIELDS = ("intent", "topic", "entity", "outcome")
ROW_COLUMNS = (
    "analysis_run_id",
    "taxonomy_id",
    "taxonomy_version",
    "taxonomy_digest",
    "validator_version",
    "policy_version",
    "policy_digest",
)
UNAVAILABLE_FIELD = {
    "status": "unavailable",
    "abstain_reason": None,
    "review_required": False,
    "values": [],
}
UNAVAILABLE_BLOCK = {
    "status": "unavailable",
    "needs_review": False,
    "review_count": 0,
    **{column: None for column in ROW_COLUMNS},
    "fields": {name: UNAVAILABLE_FIELD for name in FIELDS},
}
# The sections of the report the block must never change.
UNCHANGED = ("summary", "outcome", "qa", "review_actions", "completeness")


# --- helpers --------------------------------------------------------------------------


def _meeting(client, email_outbox, monkeypatch, settings, intelligence, label, **kwargs):  # noqa: F811
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, intelligence, label=label, **kwargs
    )
    return tenant, meeting_id, _report(client, tenant["workspace_id"], meeting_id)


def _values(block: dict) -> list[tuple[str, int, str]]:
    return [
        (name, value["index"], value["label"])
        for name in FIELDS
        for value in block["fields"][name]["values"]
    ]


def _row_columns(row: AnalysisExtraction) -> dict:
    return {column: getattr(row, column) for column in ROW_COLUMNS}


def _rewrite_field(meeting_id: str, name: str, **change) -> None:
    """Rewrite keys of one stored field of the row through a SQL UPDATE: the
    ORM refuses to update the stored extraction row, a statement does not."""
    (row,) = _rows(meeting_id)
    result = dict(row.result)
    fields = dict(result["fields"])
    fields[name] = {**fields[name], **change}
    result["fields"] = fields
    with inspection_session_factory()() as db:
        db.execute(
            update(AnalysisExtraction)
            .where(AnalysisExtraction.meeting_id == meeting_id)
            .values(result=result)
        )
        db.commit()


def _span_ids(run_id: str) -> dict[int, list[str]]:
    """finding_index -> extraction span ids of ``run_id``, in the report's ref order."""
    with inspection_session_factory()() as db:
        rows = db.execute(
            select(EvidenceSpan.finding_index, EvidenceSpan.id)
            .where(
                EvidenceSpan.analysis_run_id == run_id, EvidenceSpan.finding_type == "extraction"
            )
            .order_by(EvidenceSpan.created_at.asc(), EvidenceSpan.id.asc())
        ).all()
    cited: dict[int, list[str]] = {}
    for index, span_id in rows:
        cited.setdefault(index, []).append(span_id)
    return cited


def _refs(block: dict) -> list[dict]:
    return [
        ref
        for name in FIELDS
        for value in block["fields"][name]["values"]
        for ref in value["evidence"]
    ]


def _keys(document: object) -> set[str]:
    """Every object key anywhere in ``document``."""
    if isinstance(document, dict):
        return set(document) | {key for item in document.values() for key in _keys(item)}
    if isinstance(document, list):
        return {key for item in document for key in _keys(item)}
    return set()


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T[0-9:.]+(Z|[+-]\d{2}:\d{2})?")


def _ref_order(document: object) -> object:
    """Refs written in one flush share ``created_at`` and then sort by a random id,
    so two meetings can list them in another order: sort them by place."""
    if isinstance(document, dict):
        return {key: _ref_order(item) for key, item in document.items()}
    if isinstance(document, list):
        items = [_ref_order(item) for item in document]
        if items and all(isinstance(item, dict) and "segment_id" in item for item in items):
            items.sort(key=lambda ref: (ref["start_ms"], ref["end_ms"], ref["text_start"]))
        return items
    return document


def _normalized(section: object) -> str:
    """``section`` with its ids and timestamps replaced by stable tokens, so two
    meetings analysed the same way compare equal."""
    text = json.dumps(_ref_order(section), ensure_ascii=False, sort_keys=True)
    seen: dict[str, str] = {}
    text = _UUID.sub(lambda m: seen.setdefault(m.group(0), f"id-{len(seen)}"), text)
    return _TIMESTAMP.sub("<time>", text)


# --- shape, index and evidence --------------------------------------------------------


def test_a_mixed_extraction_shows_its_kept_values_with_their_evidence(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    tenant, meeting_id, report = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), "report-mixed"
    )
    block = report["extraction"]
    run = _current_run(meeting_id)
    (row,) = _rows(meeting_id)
    assert set(block) == BLOCK_KEYS
    assert set(block["fields"]) == set(FIELDS)
    for name in FIELDS:
        assert set(block["fields"][name]) == FIELD_KEYS
        for value in block["fields"][name]["values"]:
            assert set(value) == VALUE_KEYS

    assert block["status"] == "available"
    assert {column: block[column] for column in ROW_COLUMNS} == _row_columns(row)
    assert block["analysis_run_id"] == run.id
    assert (block["needs_review"], block["review_count"]) == (True, 2)
    assert (block["taxonomy_id"], block["taxonomy_version"], block["taxonomy_digest"]) == (
        "conversation",
        1,
        TAXONOMY.digest,
    )
    assert block["validator_version"] == VALIDATOR_VERSION
    assert (block["policy_version"], block["policy_digest"]) == (
        POLICY_VERSION,
        policy_digest(POLICY_V1),
    )

    # The index is the finding_index over the whole row; the withheld pricing is gone.
    assert _values(block) == [
        ("intent", 0, "cancel_service"),
        ("topic", 1, "invoice"),
        ("topic", 2, "contract"),
        ("entity", 3, "phone"),
        ("entity", 4, "date"),
        ("outcome", 5, "follow_up_scheduled"),
    ]
    serialized = json.dumps(block, ensure_ascii=False)
    assert "pricing" not in serialized
    assert not {"withheld", "issues", "disposition", "reason"} & _keys(block)

    fields = block["fields"]
    assert {name: fields[name]["status"] for name in FIELDS} == dict.fromkeys(FIELDS, "stated")
    assert {name: fields[name]["review_required"] for name in FIELDS} == {
        "intent": True,
        "topic": False,
        "entity": True,
        "outcome": False,
    }
    assert {fields[name]["abstain_reason"] for name in FIELDS} == {None}

    cancel = fields["intent"]["values"][0]
    phone, date = fields["entity"]["values"]
    invoice, contract = fields["topic"]["values"]
    # A critical kept value goes to review; a non-critical one does not.
    for critical in (cancel, phone):
        assert (critical["critical"], critical["review"], critical["review_reason"]) == (
            True,
            "required",
            "critical_label",
        )
    for plain in (invoice, contract, date, fields["outcome"]["values"][0]):
        assert (plain["critical"], plain["review"], plain["review_reason"]) == (
            False,
            "not_required",
            None,
        )
    assert (contract["basis"], contract["confidence"]) == ("inferred", 0.75)
    assert (phone["basis"], phone["confidence"]) == ("explicit", 0.95)
    # Entity values carry their text (the original view here); the others none.
    assert (phone["text"], date["text"]) == (PHONE, TUESDAY)
    assert {cancel["text"], invoice["text"], contract["text"]} == {None}
    # Names from the taxonomy version the row was judged with.
    for name, _index, label in _values(block):
        definition = TAXONOMY.label(name, label)
        (value,) = [v for v in fields[name]["values"] if v["label"] == label]
        assert (value["label_name_en"], value["label_name_fa"]) == (
            definition.name_en,
            definition.name_fa,
        )

    # Each value cites exactly the extraction spans of its index, in evidence order.
    cited = _span_ids(run.id)
    for name in FIELDS:
        for value in fields[name]["values"]:
            assert [ref["id"] for ref in value["evidence"]] == cited[value["index"]]
            assert (value["evidence_status"], value["supported"]) == ("sufficient", True)
    for ref in _refs(block):
        assert ref["is_current_revision"] is True
        resolved = client.get(
            f"/api/v1/meetings/{meeting_id}/evidence/{ref['id']}",
            headers={"X-Workspace-ID": tenant["workspace_id"]},
        ).json()
        assert resolved["finding_type"] == "extraction"
        assert (resolved["segment_id"], resolved["text"]) == (ref["segment_id"], ref["text"])


def test_names_come_from_the_rows_own_taxonomy_version(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    _, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label="report-names"
    )
    (row,) = _rows(meeting_id)
    calls = []
    load = analysis_extraction.load_taxonomy

    def recording(taxonomy_id, version, *, expected_digest=None):
        calls.append((taxonomy_id, version, expected_digest))
        return load(taxonomy_id, version, expected_digest=expected_digest)

    monkeypatch.setattr(analysis_extraction, "load_taxonomy", recording)
    tenant_workspace = _current_run(meeting_id).workspace_id
    block = _report(client, tenant_workspace, meeting_id)["extraction"]
    assert calls == [(row.taxonomy_id, row.taxonomy_version, row.taxonomy_digest)]
    assert block["fields"]["intent"]["values"][0]["label_name_en"] == "Cancellation request"


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"taxonomy_version": 99}, id="unknown-version"),
        pytest.param({"taxonomy_digest": "0" * 64}, id="digest-mismatch"),
    ],
)
def test_a_row_whose_taxonomy_cannot_be_loaded_shows_no_names(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
    change,
):
    tenant, meeting_id, before = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(MIXED),
        f"report-taxonomy-{next(iter(change))}",
    )
    assert before["extraction"]["fields"]["intent"]["values"][0]["label_name_en"]
    # The ORM refuses to update the row; a statement stands for a row judged with a
    # version this build does not ship.
    with inspection_session_factory()() as db:
        db.execute(
            update(AnalysisExtraction)
            .where(AnalysisExtraction.meeting_id == meeting_id)
            .values(**change)
        )
        db.commit()
    block = _report(client, tenant["workspace_id"], meeting_id)["extraction"]
    names = {
        (value["label_name_en"], value["label_name_fa"])
        for name in FIELDS
        for value in block["fields"][name]["values"]
    }
    assert names == {(None, None)}
    # Everything else is still shown.
    assert _values(block) == _values(before["extraction"])
    assert block["status"] == "available" and block["review_count"] == 2
    for key, value in change.items():
        assert block[key] == value


# --- deny-cases and allow-controls ----------------------------------------------------


def test_no_row_is_unavailable(client, email_outbox, monkeypatch, settings):  # noqa: F811
    _, meeting_id, report = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(None), "report-legacy"
    )
    assert _rows(meeting_id) == []
    assert report["summary"] is not None  # the rest of the analysis is there
    assert report["extraction"] == UNAVAILABLE_BLOCK


class _RejectedAnalysis(MockIntelligenceProvider):
    """An analysis the provider refuses for good: the full job fails."""

    async def analyze(self, request):
        raise ProviderError("provider_rejected", "The request was rejected", retryable=False)


def test_a_run_that_analysed_nothing_is_unavailable(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    # Empty transcript: the current run is stored as skipped, with no row.
    tenant, skipped_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(MIXED),
        label="report-skipped",
        asr=SilentASRProvider(),
        expected_status="partial",
    )
    run = _current_run(skipped_id)
    assert run.state == StageState.SKIPPED
    assert _report(client, tenant["workspace_id"], skipped_id)["extraction"] == UNAVAILABLE_BLOCK

    # A real analysis failure: the job fails and the meeting has no current run.
    tenant, failed_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _RejectedAnalysis(allow_mock=True),
        label="report-failed",
        expected_status="failed",
    )
    assert _rows(failed_id) == []
    report = _report(client, tenant["workspace_id"], failed_id)
    assert report["extraction"] == UNAVAILABLE_BLOCK


def test_a_value_without_evidence_is_insufficient_and_unsupported(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    tenant, meeting_id, before = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), "report-no-evidence"
    )
    run = _current_run(meeting_id)
    with inspection_session_factory()() as db:  # the spans of value 1 (invoice) are gone
        db.execute(
            delete(EvidenceSpan).where(
                EvidenceSpan.analysis_run_id == run.id,
                EvidenceSpan.finding_type == "extraction",
                EvidenceSpan.finding_index == 1,
            )
        )
        db.commit()
    block = _report(client, tenant["workspace_id"], meeting_id)["extraction"]
    status = {
        value["index"]: (value["evidence_status"], value["supported"], len(value["evidence"]))
        for name in FIELDS
        for value in block["fields"][name]["values"]
    }
    assert status == {
        0: ("sufficient", True, 1),
        1: ("insufficient", False, 0),
        2: ("sufficient", True, 1),
        3: ("sufficient", True, 1),
        4: ("sufficient", True, 1),
        5: ("sufficient", True, 1),
    }
    assert _values(block) == _values(before["extraction"])  # the value is still shown


def test_a_mock_extraction_is_available_with_the_row_columns(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    _, meeting_id, report = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        MockIntelligenceProvider(allow_mock=True),
        "report-mock",
    )
    (row,) = _rows(meeting_id)
    block = report["extraction"]
    assert block["status"] == "available"
    assert {column: block[column] for column in ROW_COLUMNS} == _row_columns(row)
    assert (block["needs_review"], block["review_count"]) == (False, 0)
    assert {name: block["fields"][name]["status"] for name in FIELDS} == dict.fromkeys(
        FIELDS, "not_stated"
    )


ACCEPTED_PART = {
    "intent": _stated(_value("request_support", [0], 0.9)),
    "entity": _stated(_value("date", [1], 0.9, text=TUESDAY)),
}


def test_an_invalid_row_hides_the_rejected_field_and_shows_the_others(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    invalid = _extraction(
        **ACCEPTED_PART,
        topic=_stated(_value("Refund", [0], 0.9)),  # not a label: the field is rejected
    )
    _, meeting_id, report = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(invalid), "report-invalid"
    )
    (row,) = _rows(meeting_id)
    assert row.valid is False
    block = report["extraction"]
    assert block["status"] == "invalid"
    assert block["fields"]["topic"] == UNAVAILABLE_FIELD
    assert _values(block) == [("intent", 0, "request_support"), ("entity", 1, "date")]
    assert block["fields"]["outcome"]["status"] == "not_stated"
    assert "Refund" not in json.dumps(block) and "issues" not in _keys(block)
    cited = _span_ids(row.analysis_run_id)
    for name in ("intent", "entity"):
        (value,) = block["fields"][name]["values"]
        assert [ref["id"] for ref in value["evidence"]] == cited[value["index"]]

    # Allow-control: the same accepted fields in a valid row.
    _, _valid_id, valid = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(_extraction(**ACCEPTED_PART)),
        "report-invalid-control",
    )
    assert valid["extraction"]["status"] == "available"
    assert _values(valid["extraction"]) == _values(block)


def test_withheld_values_are_never_shown_and_threshold_values_are(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    extraction = _extraction(
        intent=_stated(_value("cancel_service", [0], 0.49)),  # every value withheld
        topic=_stated(
            _value("invoice", [1], 0.69, basis="inferred"),  # withheld
            _value("contract", [0], 0.7, basis="inferred"),  # at the threshold: kept
        ),
        entity=_stated(
            _value("phone", [0], 0.49, text=PHONE),  # withheld, critical
            _value("date", [1], 0.5, text=TUESDAY),  # at the threshold: kept
        ),
    )
    _, meeting_id, report = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(extraction), "report-withheld"
    )
    block = report["extraction"]
    assert _values(block) == [("topic", 0, "contract"), ("entity", 1, "date")]
    assert (block["needs_review"], block["review_count"]) == (False, 0)
    assert block["fields"]["intent"] == {
        "status": "insufficient_evidence",
        "abstain_reason": "low_confidence",
        "review_required": False,
        "values": [],
    }
    assert block["fields"]["entity"]["review_required"] is False
    # Evidence texts quote whole turns (turn 0 says the phone); the values do not.
    without_evidence = {
        name: [{**value, "evidence": []} for value in block["fields"][name]["values"]]
        for name in FIELDS
    }
    serialized = json.dumps(without_evidence, ensure_ascii=False)
    for withheld in ("cancel_service", "invoice", "phone", PHONE):
        assert withheld not in serialized, withheld
    assert [value["text"] for value in block["fields"]["entity"]["values"]] == [TUESDAY]
    cited = _span_ids(_current_run(meeting_id).id)
    assert sorted(cited) == [0, 1]  # withheld values have no evidence either


def test_abstained_fields_are_shown_as_they_are(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    extraction = _extraction(
        # Two different labels in a single-valued field: contradictory by the validator.
        intent=_stated(_value("request_support", [0], 0.9), _value("purchase_inquiry", [1], 0.9)),
        topic={"status": "insufficient_evidence", "values": []},
        outcome={"status": "contradictory", "values": []},
    )
    _, _meeting_id, report = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(extraction), "report-abstained"
    )
    fields = report["extraction"]["fields"]
    assert report["extraction"]["status"] == "available"
    assert {
        name: (fields[name]["status"], fields[name]["abstain_reason"], fields[name]["values"])
        for name in FIELDS
    } == {
        "intent": ("contradictory", "contradictory", []),
        "topic": ("insufficient_evidence", "model", []),
        "entity": ("not_stated", None, []),
        "outcome": ("contradictory", "model", []),
    }


ABSTAINED = _extraction(
    intent=_stated(_value("cancel_service", [0], 0.9)),
    topic={"status": "insufficient_evidence", "values": []},
)


@pytest.mark.parametrize(
    ("status", "label"),
    [
        pytest.param(["stated"], "report-status-list", id="list"),
        pytest.param({"value": "stated"}, "report-status-dict", id="dict"),
    ],
)
def test_a_field_whose_status_is_not_a_string_is_shown_as_unavailable(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
    status,
    label,
):
    # HOM-309: a stored status that is not hashable is not a 500; the field is
    # unavailable, exactly like an unknown status string, the others unchanged.
    tenant, meeting_id, before = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), label
    )
    assert before["extraction"]["fields"]["intent"]["status"] == "stated"
    _rewrite_field(meeting_id, "intent", status=status)
    block = _report(client, tenant["workspace_id"], meeting_id)["extraction"]
    assert block["fields"]["intent"] == UNAVAILABLE_FIELD
    for name in FIELDS[1:]:
        assert block["fields"][name] == before["extraction"]["fields"][name]


@pytest.mark.parametrize(
    ("reason", "label"),
    [
        pytest.param(["model"], "report-reason-list", id="list"),
        pytest.param({"value": "model"}, "report-reason-dict", id="dict"),
    ],
)
def test_a_field_whose_abstain_reason_is_not_a_string_keeps_its_status(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
    reason,
    label,
):
    # HOM-309: a stored abstain_reason that is not hashable is not a 500; the
    # reason reads as null, the status and the other fields are unchanged.
    tenant, meeting_id, before = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(ABSTAINED), label
    )
    assert before["extraction"]["fields"]["topic"]["abstain_reason"] == "model"
    _rewrite_field(meeting_id, "topic", abstain_reason=reason)
    block = _report(client, tenant["workspace_id"], meeting_id)["extraction"]
    topic = block["fields"]["topic"]
    assert topic["status"] == "insufficient_evidence" and topic["abstain_reason"] is None
    assert topic["values"] == []
    for name in ("intent", "entity", "outcome"):
        assert block["fields"][name] == before["extraction"]["fields"][name]


def test_a_known_status_and_a_known_abstain_reason_still_show(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    # Allow-control for the two tests above: the same SQL rewrite with the
    # known values changes nothing.
    tenant, meeting_id, before = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(ABSTAINED), "report-known-values"
    )
    _rewrite_field(meeting_id, "intent", status="stated")
    _rewrite_field(meeting_id, "topic", abstain_reason="model")
    block = _report(client, tenant["workspace_id"], meeting_id)["extraction"]
    assert block["fields"] == before["extraction"]["fields"]


def test_the_block_changes_no_other_part_of_the_report(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    _, _with_id, with_block = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), "report-control-with"
    )
    _, _without_id, control = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(None), "report-control-without"
    )
    assert with_block["extraction"]["status"] == "available"
    assert control["extraction"]["status"] == "unavailable"
    for section in UNCHANGED:
        assert _normalized(with_block[section]) == _normalized(control[section]), section
    findings = with_block["qa"]["findings"]
    assert "extraction" not in {finding["finding_type"] for finding in findings}
    extraction_ids = {ref["id"] for ref in _refs(with_block["extraction"])}
    finding_ids = {ref["id"] for finding in findings for ref in finding["evidence"]}
    assert extraction_ids and not extraction_ids & finding_ids


# --- privacy --------------------------------------------------------------------------


def test_a_redacted_view_shows_a_placeholder_for_the_phone(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    """The run read the original text, so the row holds the raw phone.  Once the
    workspace redacts, the report never shows it; the owner's original view does."""
    phone_only = _extraction(entity=_stated(_value("phone", [0], 0.95, text=PHONE)))
    tenant, meeting_id = _process(
        client, email_outbox, monkeypatch, settings, _fixed(phone_only), label="report-redact"
    )
    (row,) = _rows(meeting_id)
    assert row.result["fields"]["entity"]["values"][0]["text"] == PHONE
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    workspace_id = tenant["workspace_id"]

    response = client.get(
        f"/api/v1/meetings/{meeting_id}/report", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["view"] == "redacted"
    assert _no_raw(response.text), response.text
    (phone,) = body["extraction"]["fields"]["entity"]["values"]
    assert phone["text"] == "[PHONE_1]"
    # The same value has the same placeholder in the transcript.
    transcript = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers={"X-Workspace-ID": workspace_id},
        params={"view": "redacted"},
    ).json()
    assert "[PHONE_1]" in transcript["segments"][0]["text"]
    assert all(_no_raw(ref["text"]) for ref in phone["evidence"])
    # Deterministic: a second read gives the same block.
    again = _report(client, workspace_id, meeting_id)
    assert again["extraction"] == body["extraction"]

    def original_accesses() -> int:
        with inspection_session_factory()() as db:
            return db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.event_type == "transcript.original_accessed",
                    AuditEvent.target_id == meeting_id,
                )
            )

    before = original_accesses()
    original = _report(client, workspace_id, meeting_id, view="original")
    assert original["view"] == "original"
    (phone,) = original["extraction"]["fields"]["entity"]["values"]
    assert phone["text"] == PHONE
    assert original_accesses() == before + 1  # one event, as without the block


def test_a_placeholder_stored_by_a_redacted_run_stays_one_in_the_original_view(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    """The model saw placeholders, so the row holds ``[PHONE_1]``; the original
    view shows the stored text unchanged, never the value behind it."""
    tenant, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _phone_quote("[PHONE_1]"),
        label="report-redacted-run",
        asr=_SegmentedASR(),
        redacted=True,
    )
    assert _current_run(meeting_id).input_view == "redacted"
    workspace_id = tenant["workspace_id"]
    for params in ({}, {"view": "original"}):
        report = _report(client, workspace_id, meeting_id, **params)
        (phone,) = report["extraction"]["fields"]["entity"]["values"]
        assert phone["text"] == "[PHONE_1]", params
    assert report["view"] == "original"


# --- entity text under a redacted view (SPEC 11.4) ------------------------------------

FULL_PHONE = "۰۹۱۲ ۳۴۵ ۶۷۸۹"  # as SEGMENTS[0] says it
FULL_EMAIL = "ali.rezaei@example.com"
# label, text, cited turns.  The first four quote part of a covered value.
QUOTES = (
    ("phone", "۰۹۱۲ ۳۴۵", [0]),  # two tokens of the phone
    ("phone", "۶۷۸۹", [0]),  # one token of the phone
    ("email", "example.com", [1]),  # part of the email
    ("email", "ALI.rezaei", [1]),  # another part, matched only after folding
    ("phone", FULL_PHONE, [0]),  # allow-control: the whole value
    ("email", FULL_EMAIL, [1]),
    ("date", "دوباره", [1]),  # allow-control: a quote no redaction class covers
    # Only turn 1 holds it: turn 0 (cited first) has no region, so turn 1 is used.
    ("phone", "۰۹۱۲۳۴۵۶۷۸۹", [0, 1]),
)


def test_quoted_region_follows_the_validators_match():
    assert SEGMENTS[0][slice(*quoted_region(SEGMENTS[0], "۰۹۱۲ ۳۴۵"))] == "۰۹۱۲ ۳۴۵"
    # Folded digits match; the region is the word as said.
    turn = "شماره من ۰۹۱۲۳۴۵۶۷۸۹ است."
    assert turn[slice(*quoted_region(turn, "09123456789"))] == "۰۹۱۲۳۴۵۶۷۸۹"
    # Part of a word: the exact quote when it is there, else the whole word.
    assert SEGMENTS[1][slice(*quoted_region(SEGMENTS[1], "example.com"))] == "example.com"
    assert SEGMENTS[1][slice(*quoted_region(SEGMENTS[1], "EXAMPLE com"))] == FULL_EMAIL
    assert quoted_region("سه‌شنبه می‌آیم", "سهشنبه") == (0, 7)  # ZWNJ folds away
    # Not a whole run of tokens: no region, like the validator.
    assert quoted_region("کد 48213 ثبت شد", "4821") is None
    assert quoted_region(SEGMENTS[0], "۰۹۱۲۳۴۵۶۷۸۹") is None
    assert quoted_region(SEGMENTS[0], "") is None


def test_quoted_region_falls_back_to_the_exact_quote(monkeypatch):
    """When the words cannot be lined up with the folded turn, the first exact
    occurrence is the region; a folded-only match is then not found."""
    fold = analysis_extraction.normalize_for_matching

    def uneven(text, language="fa"):
        folded = fold(text, language)
        return f"{folded} x" if " " in text else folded

    monkeypatch.setattr(analysis_extraction, "normalize_for_matching", uneven)
    assert SEGMENTS[0][slice(*quoted_region(SEGMENTS[0], "۰۹۱۲ ۳۴۵"))] == "۰۹۱۲ ۳۴۵"
    assert quoted_region("شماره من ۰۹۱۲۳۴۵۶۷۸۹ است.", "09123456789") is None


def test_a_redacted_view_shows_a_quoted_part_of_a_value_as_its_placeholder(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    """Analysed without redaction, so the row holds the raw quotes; the workspace
    then redacts.  A part of a phone or email shows the placeholder the transcript
    shows for the whole value."""
    extraction = _extraction(
        entity=_stated(*(_value(label, turns, 0.9, text=text) for label, text, turns in QUOTES))
    )
    tenant, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(extraction),
        label="report-partial",
        asr=_SegmentedASR(),
    )
    assert _current_run(meeting_id).input_view == "original"
    (row,) = _rows(meeting_id)
    stored = [value["text"] for value in row.result["fields"]["entity"]["values"]]
    assert stored == [text for _label, text, _turns in QUOTES]  # all kept, raw
    workspace_id = tenant["workspace_id"]

    # Allow-control: the owner's original view shows the stored texts.
    original = _report(client, workspace_id, meeting_id)
    assert original["view"] == "original"
    assert [v["text"] for v in original["extraction"]["fields"]["entity"]["values"]] == stored

    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/report", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["view"] == "redacted"
    assert _no_raw(response.text), response.text
    for fragment in ("۳۴۵", "۶۷۸۹", "rezaei", "example"):
        assert fragment not in response.text, fragment
    transcript = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript", headers={"X-Workspace-ID": workspace_id}
    ).json()
    (phone,) = re.findall(r"\[PHONE_\d+\]", transcript["segments"][0]["text"])
    (email,) = re.findall(r"\[EMAIL_\d+\]", transcript["segments"][1]["text"])
    (phone_turn_1,) = re.findall(r"\[PHONE_\d+\]", transcript["segments"][1]["text"])
    texts = [v["text"] for v in body["extraction"]["fields"]["entity"]["values"]]
    assert texts == [phone, phone, email, email, phone, email, "دوباره", phone_turn_1]

    # The owner's original view still shows the stored texts.
    original = _report(client, workspace_id, meeting_id, view="original")
    assert [v["text"] for v in original["extraction"]["fields"]["entity"]["values"]] == stored


def test_shares_never_carry_the_extraction(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    tenant, meeting_id, report = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), "report-share"
    )
    workspace_id = tenant["workspace_id"]
    assert report["extraction"]["status"] == "available"  # the tenant report has it
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_write_headers(client, workspace_id),
        json={"public_share_links_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    created = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_write_headers(client, workspace_id),
        json={"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
    )
    assert created.status_code == 201, created.text
    token = urlparse(created.json()["url"]).path.rsplit("/", 1)[-1]

    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{token}")
    assert shared.status_code == 200, shared.text
    exported = client.get(f"/api/v1/shares/{token}/export", params={"format": "json"})
    assert exported.status_code == 200, exported.text
    for body in (shared.text, exported.text):
        document = json.loads(body)
        assert "extraction" not in _keys(document)
        assert '"finding_type": "extraction"' not in body
        assert '"finding_type":"extraction"' not in body
        for label in ("cancel_service", "follow_up_scheduled", "critical_label"):
            assert label not in body, label


def test_another_workspace_gets_404(client, email_outbox, monkeypatch, settings):  # noqa: F811
    _, meeting_id, _report_body = _meeting(
        client, email_outbox, monkeypatch, settings, _fixed(MIXED), "report-owner"
    )
    other = _tenant(client, email_outbox, "report-other")
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/report",
        headers={"X-Workspace-ID": other["workspace_id"]},
    )
    assert response.status_code == 404


# --- the current run: stale, failed and successful recompute -------------------------

STEADY = _extraction(
    intent=_stated(_value("cancel_service", [0], 0.9)),
    topic=_stated(_value("contract", [0], 0.8, basis="inferred")),
)


def _currency(block: dict) -> set[bool]:
    return {ref["is_current_revision"] for ref in _refs(block)}


def test_the_block_follows_the_current_run(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
    analysis_provider,  # noqa: F811
):
    tenant, meeting_id, fresh = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(STEADY),
        "report-recompute",
        asr=MockASRProvider(allow_mock=True, transcript=ORIGINAL_TEXT),
    )
    workspace_id = tenant["workspace_id"]
    first_run = _current_run(meeting_id)
    assert fresh["extraction"]["analysis_run_id"] == first_run.id
    assert _currency(fresh["extraction"]) == {True}  # fresh analysis

    corrected = _correct(client, workspace_id, meeting_id)
    stale = _report(client, workspace_id, meeting_id)
    assert stale["extraction"]["analysis_run_id"] == first_run.id
    assert _currency(stale["extraction"]) == {False}  # stale analysis
    assert _values(stale["extraction"]) == _values(fresh["extraction"])

    # A failed recompute adds a non-current run and no row: the old row stays.
    analysis_provider["intelligence"] = _FailingIntelligence(allow_mock=True)
    assert (
        recompute.recompute_meeting_analysis(
            meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
        )
        == "failed"
    )
    failed = _report(client, workspace_id, meeting_id)
    assert failed["extraction"]["analysis_run_id"] == first_run.id
    assert _currency(failed["extraction"]) == {False}
    assert failed["extraction"] == stale["extraction"]

    # A successful recompute: the new run's row; the old run's row is never shown.
    analysis_provider["intelligence"] = _fixed(STEADY)
    assert (
        recompute.recompute_meeting_analysis(
            meeting_id, corrected["id"], workspace_id=workspace_id, settings=settings
        )
        == "completed"
    )
    new_run = _current_run(meeting_id)
    assert new_run.id != first_run.id
    assert {row.analysis_run_id for row in _rows(meeting_id)} == {first_run.id, new_run.id}
    recomputed = _report(client, workspace_id, meeting_id)
    block = recomputed["extraction"]
    assert block["analysis_run_id"] == new_run.id
    assert _currency(block) == {True}
    assert _values(block) == _values(fresh["extraction"])
    cited = _span_ids(new_run.id)
    for name in FIELDS:
        for value in block["fields"][name]["values"]:
            assert [ref["id"] for ref in value["evidence"]] == cited[value["index"]]
    old_ids = {span for spans in _span_ids(first_run.id).values() for span in spans}
    assert old_ids and not old_ids & {ref["id"] for ref in _refs(block)}


def test_with_no_region_in_any_cited_turn_a_text_of_an_unredacted_run_is_null(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    """The validator kept ``09123456789`` because it folds to the turn's
    ``۰۹۱۲۳۴۵۶۷۸۹``.  If the words of the turn cannot be lined up with the folded
    turn, the report has no region (no exact quote either).  The run read the
    original text, so the stored text is raw: it is never shown, ``text`` is null."""
    extraction = _extraction(entity=_stated(_value("phone", [1], 0.9, text="09123456789")))
    tenant, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(extraction),
        label="report-no-region",
        asr=_SegmentedASR(),
    )
    assert _current_run(meeting_id).input_view == "original"
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    workspace_id = tenant["workspace_id"]
    redacted = _report(client, workspace_id, meeting_id)
    # Allow-control: with the real fold the region is found, so the placeholder shows.
    (value,) = redacted["extraction"]["fields"]["entity"]["values"]
    transcript = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript", headers={"X-Workspace-ID": workspace_id}
    ).json()
    assert value["text"] == re.findall(r"\[PHONE_\d+\]", transcript["segments"][1]["text"])[0]

    fold = analysis_extraction.normalize_for_matching

    def uneven(text, language="fa"):
        folded = fold(text, language)
        return f"{folded} x" if " " in text else folded

    monkeypatch.setattr(analysis_extraction, "normalize_for_matching", uneven)
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/report", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    (value,) = response.json()["extraction"]["fields"]["entity"]["values"]
    assert value["text"] is None
    assert "09123456789" not in response.text and _no_raw(response.text)
    # The owner's original view shows the stored text.
    original = _report(client, workspace_id, meeting_id, view="original")
    (value,) = original["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == "09123456789"


# --- a fragment said in more than one place (SPEC 11.6) -------------------------------

ROOM_AND_PHONE = (
    "اتاق ۳۴۵ را گرفتم. شماره من ۰۹۱۲ ۳۴۵ ۶۷۸۹ است. ساعت ۱۲ میام، یا 12 و نیم.",
    "اتاق ۳۴۵ خالی است.",
)


class _RoomAndPhoneASR(MockASRProvider):
    """The two turns of ``ROOM_AND_PHONE`` as timed segments."""

    def __init__(self) -> None:
        super().__init__(allow_mock=True, transcript=" ".join(ROOM_AND_PHONE), language="fa")

    async def transcribe(self, request):
        return ASRResult(
            provider="mock_asr",
            model="deterministic-v1",
            language="fa",
            text=" ".join(ROOM_AND_PHONE),
            segments=[
                ASRSegment(index=i, text=text, start_seconds=i * 5, end_seconds=i * 5 + 4)
                for i, text in enumerate(ROOM_AND_PHONE)
            ],
            duration_seconds=10,
            is_mock=True,
        )


def test_quoted_regions_lists_every_spot_in_text_order():
    turn = ROOM_AND_PHONE[0]
    assert [turn[start:end] for start, end in quoted_regions(turn, "۳۴۵")] == ["۳۴۵", "۳۴۵"]
    room, phone = quoted_regions(turn, "۳۴۵")
    assert room[0] < turn.index("۰۹۱۲") < phone[0]
    # Two spots that fold alike: each is the word as said there.
    assert [turn[start:end] for start, end in quoted_regions(turn, "12")] == ["۱۲", "12"]
    assert quoted_regions(turn, "۴۵") == []


def test_a_redacted_view_prefers_the_spot_inside_a_covered_value(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    """``۳۴۵`` is a room number first and part of the phone later.  The report shows
    the phone's placeholder, from the same turn or from a later cited turn.  A quote
    no redaction class covers shows its first spot."""
    extraction = _extraction(
        entity=_stated(
            _value("phone", [0], 0.9, text="۳۴۵"),  # room first, then the phone
            # Turn 1 (cited first) holds only the room; turn 0 holds the phone.
            _value("reference_id", [1, 0], 0.9, text="۳۴۵"),
            # Allow-control: not covered, said as ۱۲ first and 12 later.
            _value("time", [0], 0.9, text="12"),
        )
    )
    tenant, meeting_id = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        _fixed(extraction),
        label="report-room-phone",
        asr=_RoomAndPhoneASR(),
    )
    (row,) = _rows(meeting_id)
    stored = [value["text"] for value in row.result["fields"]["entity"]["values"]]
    assert stored == ["۳۴۵", "۳۴۵", "12"]  # all kept
    workspace_id = tenant["workspace_id"]
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200

    transcript = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript", headers={"X-Workspace-ID": workspace_id}
    ).json()
    (phone,) = re.findall(r"\[PHONE_\d+\]", transcript["segments"][0]["text"])
    assert transcript["segments"][0]["text"].startswith("اتاق ۳۴۵ ")  # the room stays
    redacted = _report(client, workspace_id, meeting_id)
    assert redacted["view"] == "redacted"
    texts = [v["text"] for v in redacted["extraction"]["fields"]["entity"]["values"]]
    assert texts == [phone, phone, "۱۲"]

    # Under the original view nothing changes.
    original = _report(client, workspace_id, meeting_id, view="original")
    assert [v["text"] for v in original["extraction"]["fields"]["entity"]["values"]] == stored


# --- a redacted-input text under another policy (SPEC 11.7) ---------------------------

EMAIL_AND_PHONE = "ایمیل ali.rezaei@example.com و موبایل ۰۹۱۲ ۳۴۵ ۶۷۸۹ است."


class _OneTurnASR(MockASRProvider):
    """``EMAIL_AND_PHONE`` as one timed segment."""

    def __init__(self) -> None:
        super().__init__(allow_mock=True, transcript=EMAIL_AND_PHONE, language="fa")

    async def transcribe(self, request):
        return ASRResult(
            provider="mock_asr",
            model="deterministic-v1",
            language="fa",
            text=EMAIL_AND_PHONE,
            segments=[ASRSegment(index=0, text=EMAIL_AND_PHONE, start_seconds=0, end_seconds=4)],
            duration_seconds=5,
            is_mock=True,
        )


def _placeholder_and_phone_part(request) -> dict:
    """A phone text that quotes the email placeholder the model saw and the first
    part of the phone, which the email-only run left raw."""
    (email,) = re.findall(r"\[EMAIL_\d+\]", request.turns[0].text)
    return _extraction(entity=_stated(_value("phone", [0], 0.9, text=f"{email} و موبایل ۰۹۱۲")))


def test_a_redacted_input_text_is_shown_only_under_the_policy_of_its_run(
    client,
    email_outbox,
    monkeypatch,
    settings,  # noqa: F811
):
    """Analysed with email-only redaction; the stored text holds the email
    placeholder and a raw part of the phone, and no region of the original turn.
    Under the same policy it is shown through ``view.text``.  Once the workspace
    adds phone, the run's policy is not the view's, so the text is null."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "report-widened")
    email_only = _set_policy(
        client, tenant, pii_redaction_enabled=True, pii_redaction_classes=["email"]
    )
    assert email_only.status_code == 200, email_only.text
    accepted = _upload(client, tenant, key="report-widened-0001")
    assert accepted.status_code == 202, accepted.text
    result = process_job(
        accepted.json()["job"]["id"],
        worker_id="report-widened",
        settings=settings,
        providers=ProviderBundle(
            asr=_OneTurnASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=_Extracting(_placeholder_and_phone_part),
        ),
    )
    assert result["status"] == "completed", result
    meeting_id = accepted.json()["meeting"]["id"]
    run = _current_run(meeting_id)
    assert run.input_view == "redacted" and run.input_policy_digest is not None
    (row,) = _rows(meeting_id)
    (stored,) = [value["text"] for value in row.result["fields"]["entity"]["values"]]
    assert stored.endswith(" و موبایل ۰۹۱۲") and "[EMAIL_" in stored
    assert quoted_region(EMAIL_AND_PHONE, stored) is None  # no region to render
    workspace_id = tenant["workspace_id"]

    # Allow-control: the unchanged policy shows view.text(stored), here the stored text.
    same = _report(client, workspace_id, meeting_id)
    assert same["view"] == "redacted"
    (value,) = same["extraction"]["fields"]["entity"]["values"]
    assert value["text"] == stored

    widened = _set_policy(client, tenant, pii_redaction_classes=["email", "phone"])
    assert widened.status_code == 200, widened.text
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/report", headers={"X-Workspace-ID": workspace_id}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["view"] == "redacted"
    (value,) = body["extraction"]["fields"]["entity"]["values"]
    assert value["text"] is None
    assert _no_raw(response.text), response.text
    transcript = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript", headers={"X-Workspace-ID": workspace_id}
    ).json()
    assert re.search(r"\[PHONE_\d+\]", transcript["segments"][0]["text"])
