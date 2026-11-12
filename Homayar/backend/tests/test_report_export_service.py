"""HOM-137 (F70): the report export renderers are pure and safe.

``services/report_export_service.py`` turns one built report plus its
provenance into JSON, CSV or PDF.  No database, no clock, no network: the
time and every row come in as arguments.  The CSV writer neutralizes the
OWASP CSV injection characters, and the PDF renderer escapes every dynamic
string so stored markup can never become live markup.
"""

from __future__ import annotations

import io
import json
import re
from datetime import UTC, datetime

import pytest
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate

from backend.schemas.meetings import MeetingQualityResponse, ReportCompleteness
from backend.schemas.reports import (
    CallReportResponse,
    ReportDecision,
    ReportExtraction,
    ReportExtractionField,
    ReportExtractionFields,
    ReportExtractionValue,
    ReportFinding,
    ReportFindingCounts,
    ReportOutcome,
    ReportQA,
    ReportSummary,
    ReportSummaryField,
    ReportSummaryFields,
)
from backend.services.export_service import ExportRevision
from backend.services.report_export_service import (
    EXPORT_VERSION,
    ExportAnalysis,
    ExportProvenance,
    neutralize_csv_cell,
    render_report_export,
    report_export_payload,
)

GENERATED_AT = datetime(2026, 11, 12, 9, 30, 0, tzinfo=UTC)
REVISION = ExportRevision(
    id="rev-0001",
    revision_number=3,
    source="asr",
    created_at=datetime(2026, 11, 12, 9, 0, 0, tzinfo=UTC),
    asr_provider="two_turn_asr",
    asr_model="v1",
    is_mock=False,
)
ANALYSIS = ExportAnalysis(
    run_id="run-0001",
    provider="report_intelligence",
    model="report-v1",
    prompt_version="7",
    prompt_sha256="ab" * 32,
    output_schema_sha256="cd" * 32,
    schema_version=4,
    is_mock=False,
    transcript_revision_id="rev-0001",
)


# --- helpers -----------------------------------------------------------------


def _quality() -> MeetingQualityResponse:
    return MeetingQualityResponse(
        transcription="complete",
        speaker_attribution="inferred",
        analysis="complete",
        evidence="partial",
        analysis_stale=False,
        transcript_revision_id="rev-0001",
        analysed_revision_id="rev-0001",
        analysis_run_id="run-0001",
        segment_count=2,
        speaker_count=1,
        speech_coverage_ratio=0.9,
        is_mock=False,
        degradations=[],
        usage=None,
    )


def _summary(overview: str) -> ReportSummary:
    field = ReportSummaryField(
        status="stated", text=None, basis=None, evidence_status=None, supported=None, evidence=[]
    )
    return ReportSummary(
        overview=overview,
        evidence_status="sufficient",
        supported=True,
        evidence=[],
        key_points=[],
        topics=["قیمت"],
        sentiment="neutral",
        analysis_run_id="run-0001",
        provider="report_intelligence",
        model="report-v1",
        fields=ReportSummaryFields(purpose=field, resolution=field, next_steps=field),
        key_moments_status="unavailable",
        key_moments=[],
    )


def _extraction() -> ReportExtraction:
    value = ReportExtractionValue(
        index=0,
        label="pricing",
        label_name_en="Pricing",
        label_name_fa="قیمت",
        basis="explicit",
        critical=False,
        confidence=0.8,
        review="not_required",
        review_reason=None,
        text=None,
        evidence_status="sufficient",
        supported=True,
        evidence=[],
    )
    empty = ReportExtractionField(
        status="unavailable", abstain_reason=None, review_required=False, values=[]
    )
    field = ReportExtractionField(
        status="stated", abstain_reason=None, review_required=False, values=[value]
    )
    return ReportExtraction(
        status="available",
        needs_review=False,
        review_count=0,
        analysis_run_id="run-0001",
        taxonomy_id="tax-1",
        taxonomy_version=2,
        taxonomy_digest="d" * 64,
        validator_version="v1",
        policy_version="1",
        policy_digest="e" * 64,
        fields=ReportExtractionFields(intent=field, topic=empty, entity=empty, outcome=empty),
    )


def _report(
    *,
    overview: str = "قیمت فردا اعلام می‌شود",
    decision_text: str = "جلسهٔ بعد سه‌شنبه",
) -> CallReportResponse:
    return CallReportResponse(
        meeting_id="meet-0001",
        summary=_summary(overview),
        outcome=ReportOutcome(
            state="explicit",
            decisions=[
                ReportDecision(
                    text=decision_text,
                    key_point_index=0,
                    confidence=0.9,
                    evidence_status="sufficient",
                    supported=True,
                    evidence=[],
                    basis="explicit",
                    owner={"status": "not_stated", "value": None},
                    due_date={"status": "not_stated", "value": None},
                )
            ],
        ),
        qa=ReportQA(
            quality=_quality(),
            findings=[
                ReportFinding(
                    finding_type="summary",
                    finding_id=None,
                    finding_index=None,
                    category=None,
                    text=overview,
                    evidence_status="sufficient",
                    supported=True,
                    evidence=[],
                    section="overview",
                    basis=None,
                )
            ],
            counts=ReportFindingCounts(
                total=1, supported=1, unsupported=0, sufficient=1, partial=0, insufficient=0
            ),
        ),
        review_actions=[],
        completeness=ReportCompleteness(
            is_complete=True,
            notices=[],
            meeting_status="completed",
            transcription="complete",
            speaker_attribution="inferred",
            analysis="complete",
            evidence="partial",
        ),
        extraction=_extraction(),
    )


def _provenance(**overrides) -> ExportProvenance:
    fields = {
        "export_version": EXPORT_VERSION,
        "artifact": "report",
        "format": "json",
        "generated_at": GENERATED_AT,
        "meeting_id": "meet-0001",
        "report_version": 1,
        "transcript_revision": REVISION,
        "analysis": ANALYSIS,
        "privacy": {"view": "original", "policy_enforced": False},
    }
    return ExportProvenance(**{**fields, **overrides})


# --- neutralize_csv_cell -----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('=HYPERLINK("http://x")', '\'=HYPERLINK("http://x")'),
        ("+1", "'+1"),
        ("-1+1", "'-1+1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\t=1", "'\t=1"),
        ("\r=1", "'\r=1"),
        ("\n=1", "'\n=1"),
        ("  =1", "'  =1"),
        ("＝1", "'＝1"),
        ("＋1", "'＋1"),
        ("－1", "'－1"),
        ("＠1", "'＠1"),
        # Amendment B5: invisible leading characters (Cc, Cf, Z*) do not hide a trigger.
        ("​=1", "'​=1"),  # zero width space (Cf)
        ("﻿=1", "'﻿=1"),  # byte order mark (Cf)
        ("\x00=1", "'\x00=1"),  # NUL (Cc)
        ("\xa0=1", "'\xa0=1"),  # no-break space (Zs)
        (" +1", "' +1"),  # line separator (Zl)
        ("​\xa0 \x00@x", "'​\xa0 \x00@x"),  # a mix before the trigger
        ("​＝1", "'​＝1"),  # full-width equals after a Cf
        ("hello", "hello"),
        ("​hello", "​hello"),  # invisible prefix, no trigger
        ("\x00", "\x00"),
        ("​", "​"),
        ("سلام", "سلام"),
        ("a=b", "a=b"),
        ("1-2", "1-2"),
        ("", ""),
        (" ", " "),
    ],
)
def test_neutralize_csv_cell(value, expected):
    assert neutralize_csv_cell(value) == expected


# --- JSON payload ------------------------------------------------------------


def test_report_export_payload_carries_version_provenance_and_report():
    report = _report()
    payload = report_export_payload(report, _provenance())
    assert payload["export_version"] == 1
    assert payload["report"] == report.model_dump(mode="json")
    provenance = payload["provenance"]
    assert provenance["export_version"] == 1
    assert provenance["artifact"] == "report"
    assert provenance["report_version"] == 1
    assert provenance["generated_at"] == "2026-11-12T09:30:00+00:00"
    assert provenance["transcript_revision"]["id"] == "rev-0001"
    assert provenance["transcript_revision"]["revision_number"] == 3
    assert provenance["analysis"]["run_id"] == "run-0001"
    assert provenance["analysis"]["transcript_revision_id"] == "rev-0001"
    assert provenance["privacy"] == {"view": "original", "policy_enforced": False}


def test_report_export_payload_without_revision_or_analysis():
    payload = report_export_payload(
        _report(), _provenance(transcript_revision=None, analysis=None, privacy=None)
    )
    provenance = payload["provenance"]
    assert provenance["transcript_revision"] is None
    assert provenance["analysis"] is None
    assert provenance["privacy"] is None


def test_json_export_dumps_unicode_unescaped():
    report = _report()
    provenance = _provenance(format="json")
    rendered = render_report_export(report, provenance, "json", title="R")
    assert rendered.media_type == "application/json"
    assert rendered.extension == "json"
    assert "قیمت" in rendered.content.decode("utf-8")
    # A3: the payload is JSON-ready, so the render round-trips through it
    assert json.loads(rendered.content) == report_export_payload(report, provenance)


def test_csv_export_is_deterministic():
    first = render_report_export(_report(), _provenance(format="csv"), "csv", title="R")
    second = render_report_export(_report(), _provenance(format="csv"), "csv", title="R")
    assert first.content == second.content


# --- CSV ---------------------------------------------------------------------


def _csv_rows(rendered) -> list[list[str]]:
    import csv
    import io

    text = rendered.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))


def test_csv_export_has_bom_header_one_row_per_leaf_and_formula_guard():
    report = _report(overview='=HYPERLINK("http://x") و +1 و ＝1')
    rendered = render_report_export(report, _provenance(format="csv"), "csv", title="R")
    assert rendered.media_type == "text/csv; charset=utf-8"
    assert rendered.extension == "csv"
    assert rendered.content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in rendered.content
    rows = _csv_rows(rendered)
    assert rows[0] == ["path", "value"]
    cells = {path: value for path, value in rows[1:]}
    assert cells["export_version"] == "1"
    assert cells["provenance.artifact"] == "report"
    assert cells["provenance.transcript_revision.id"] == "rev-0001"
    assert cells["report.meeting_id"] == "meet-0001"
    # leaf conversions
    assert cells["report.summary.sentiment"] == "neutral"
    assert cells["report.summary.supported"] == "true"
    assert cells["report.qa.quality.segment_count"] == "2"
    assert cells["report.qa.quality.speech_coverage_ratio"] == "0.9"
    assert cells["report.summary.analysis_run_id"] == "run-0001"
    # a formula-looking string cell is prefixed, and only string cells
    assert cells["report.summary.overview"] == '\'=HYPERLINK("http://x") و +1 و ＝1'
    # list indexes appear in the path
    assert cells["report.outcome.decisions.0.text"] == "جلسهٔ بعد سه‌شنبه"
    assert cells["report.qa.findings.0.evidence_status"] == "sufficient"
    assert cells["report.summary.key_points"] == "[]"
    assert cells["report.review_actions"] == "[]"


def test_csv_export_quotes_every_field():
    rendered = render_report_export(_report(), _provenance(format="csv"), "csv", title="R")
    first_line = rendered.content.decode("utf-8-sig").split("\r\n")[0]
    assert first_line == '"path","value"'


# --- PDF ---------------------------------------------------------------------


def test_pdf_export_is_a_pdf_and_carries_provenance_text():
    rendered = render_report_export(_report(), _provenance(format="pdf"), "pdf", title="گزارش")
    assert rendered.media_type == "application/pdf"
    assert rendered.extension == "pdf"
    content = rendered.content
    assert content.startswith(b"%PDF-")
    assert content.rstrip().endswith(b"%%EOF")
    assert b"Export version: 1" in content
    assert b"rev-0001" in content
    assert b"meet-0001" in content


def test_pdf_export_is_deterministic():
    first = render_report_export(_report(), _provenance(format="pdf"), "pdf", title="R")
    second = render_report_export(_report(), _provenance(format="pdf"), "pdf", title="R")
    assert first.content == second.content


def _pdf_unescape(literal: bytes) -> str:
    """A PDF string literal as text: ``\\ddd`` octal and ``\\X`` escapes."""
    raw = re.sub(rb"\\([0-7]{1,3})", lambda match: bytes([int(match.group(1), 8)]), literal)
    raw = re.sub(rb"\\(.)", rb"\1", raw, flags=re.S)
    return raw.decode("latin-1")


def _pdf_printed_text(content: bytes) -> str:
    """The text the page content streams print, rebuilt from their operators.

    reportlab (``pageCompression=0``) writes each line as ``(...) Tj``
    fragments and ends a line with ``T*`` (or the text object with ``ET``).
    Fragments of one line are joined as they are; lines are joined with a
    space, because a wrapped line lost the space it was wrapped at.
    """
    lines: list[str] = []
    current: list[str] = []
    for match in re.finditer(rb"\(((?:\\.|[^\\)])*)\)\s*Tj|T\*|\bET\b", content, re.S):
        if match.group(1) is not None:
            current.append(_pdf_unescape(match.group(1)))
        elif current:
            lines.append("".join(current))
            current = []
    if current:
        lines.append("".join(current))
    return " ".join(lines)


def test_pdf_text_extraction_sees_live_markup_as_markup():
    """Control for the markup test below: when markup does reach a Paragraph
    unescaped, the link is live (``/URI``) and the extracted text has no tag."""
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, invariant=1, pageCompression=0).build(
        [
            Paragraph(
                'live <a href="http://x.example">y</a> <font size="20">z</font>',
                getSampleStyleSheet()["BodyText"],
            )
        ]
    )
    content = buffer.getvalue()
    assert b"/URI" in content
    text = _pdf_printed_text(content)
    assert "live" in text
    assert "<a href" not in text
    assert "<font" not in text


def test_pdf_export_prints_stored_markup_as_text(tmp_path):
    payload = tmp_path / "payload.txt"
    marker = "HOM137-SECRET-MARKER"
    payload.write_text(marker, encoding="utf-8")
    overview = f'<img src="{payload}"/> <a href="http://x.example">y</a> <font size="99">z</font>'
    rendered = render_report_export(
        _report(overview=overview), _provenance(format="pdf"), "pdf", title="R"
    )
    content = rendered.content
    assert content.startswith(b"%PDF-")
    assert marker.encode() not in content
    assert b"/URI" not in content
    # The stored markup is printed literally, tag by tag, as page text.  The
    # long path wraps mid-word, so compare without whitespace.
    printed = "".join(_pdf_printed_text(content).split())
    assert "".join(overview.split()) in printed


def test_pdf_title_metadata_is_the_visible_title_as_plain_text():
    """Amendment B4: ``/Title`` holds the same cleaned text the page shows."""
    title = 'Call <b>bold</b> & "q"\x00\nnext\x1bline'
    rendered = render_report_export(_report(), _provenance(format="pdf"), "pdf", title=title)
    content = rendered.content
    shown = 'Call <b>bold</b> & "q" next line'
    metadata = re.search(rb"/Title \(((?:\\.|[^\\)])*)\)", content)
    assert metadata is not None
    assert _pdf_unescape(metadata.group(1)) == shown
    assert _pdf_printed_text(content).startswith(shown + " ")
    # control: a plain title is kept as it is in both places
    plain = render_report_export(_report(), _provenance(format="pdf"), "pdf", title="Weekly call")
    assert b"/Title (Weekly call)" in plain.content
    assert _pdf_printed_text(plain.content).startswith("Weekly call ")


def test_pdf_export_prints_plain_ascii_text_and_survives_controls():
    overview = "Plain summary line one\nline two\x00\x1b with controls"
    rendered = render_report_export(
        _report(overview=overview), _provenance(format="pdf"), "pdf", title="R"
    )
    content = rendered.content
    assert content.startswith(b"%PDF-")
    assert b"Plain summary line one" in content
    assert b"line two with controls" in content


def test_pdf_export_renders_every_section_without_raising():
    report = _report()
    rendered = render_report_export(report, _provenance(format="pdf"), "pdf", title="R")
    content = rendered.content
    assert b"Provenance" in content
    assert b"Summary" in content
    assert b"Outcome" in content
    assert b"Quality and findings" in content
    assert b"Extraction" in content
    assert b"Completeness" in content
    # the outcome state and the finding evidence status appear as text
    assert b"State: explicit" in content
    assert b"supported" in content
    # an extraction leaf is printed with its dotted path
    assert b"report.extraction.fields.intent.values.0.label: pricing" in content


def test_pdf_export_without_revision_analysis_or_summary():
    report = _report().model_copy(update={"summary": None})
    rendered = render_report_export(
        report,
        _provenance(format="pdf", transcript_revision=None, analysis=None, privacy=None),
        "pdf",
        title="R",
    )
    content = rendered.content
    assert content.startswith(b"%PDF-")
    assert b"Transcript revision: none" in content
    assert b"Analysis: none" in content


def test_render_report_export_refuses_an_unknown_format():
    with pytest.raises(ValueError, match="Unsupported report export format"):
        render_report_export(_report(), _provenance(format="xml"), "xml", title="R")
