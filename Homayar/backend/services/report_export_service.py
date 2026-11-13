"""Deterministic call report export renderers (HOM-137, F70).

Pure functions: no database, no clock (the caller passes ``generated_at``),
no network, no file reads.  ``report_export_payload`` is the single JSON
source the CSV flattening describes.  The PDF draws every line as plain text
with the bundled Vazirmatn font, Persian joined and in right-to-left order
(``backend.services.pdf_text``, HOM-138): no dynamic string is ever parsed
as markup, so stored markup can never become live markup.
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.platypus import Flowable, SimpleDocTemplate, Spacer

from backend.schemas.reports import CallReportResponse
from backend.services.export_service import (
    EXPORT_VERSION,
    ExportAnalysis,
    ExportRevision,
    RenderedExport,
    export_text_size,
)
from backend.services.pdf_text import (
    BOLD_FONT,
    REGULAR_FONT,
    LineStyle,
    PlainTextParagraph,
    clean_text,
    register_fonts,
)

__all__ = [
    "EXPORT_VERSION",
    "ExportAnalysis",
    "ExportProvenance",
    "neutralize_csv_cell",
    "render_report_export",
    "report_export_payload",
    "report_export_text_size",
]


@dataclass(frozen=True)
class ExportProvenance:
    """What one report export was rendered from (never a value)."""

    export_version: int  # EXPORT_VERSION
    artifact: str  # "report" | "transcript"
    format: str
    generated_at: datetime  # timezone-aware UTC
    meeting_id: str
    report_version: int | None  # REPORT_VERSION for a report, None for a transcript
    transcript_revision: ExportRevision | None
    analysis: ExportAnalysis | None
    privacy: dict[str, Any] | None  # export_privacy(...) output, never a value


# Characters a spreadsheet may read as the start of a formula (OWASP CSV
# injection rule), including the full-width lookalikes.
_FORMULA_STARTS = frozenset("=+-@＝＋－＠")


def _first_visible(value: str) -> str:
    """The first character after leading whitespace and invisible characters.

    Skips what ``str.lstrip()`` strips and every character of Unicode category
    ``Cc`` (controls such as NUL), ``Cf`` (format, such as U+200B or U+FEFF)
    or ``Z*`` (separators, such as NBSP), so they cannot hide a trigger
    (amendment B5).  Empty when nothing visible is left.
    """
    for char in value:
        if char.isspace():
            continue
        category = unicodedata.category(char)
        if category in ("Cc", "Cf") or category.startswith("Z"):
            continue
        return char
    return ""


def neutralize_csv_cell(value: str) -> str:
    """Prefix ``'`` when a cell could be read as a formula (OWASP rule)."""
    if value and (value[0] in "\t\r\n" or _first_visible(value) in _FORMULA_STARTS):
        return "'" + value
    return value


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def report_export_payload(report: CallReportResponse, provenance: ExportProvenance) -> dict:
    """The JSON export body: the export version, the provenance and the report."""
    return {
        "export_version": EXPORT_VERSION,
        "provenance": _json_ready(asdict(provenance)),
        "report": report.model_dump(mode="json"),
    }


def report_export_text_size(report: CallReportResponse, provenance: ExportProvenance) -> int:
    """The export text size of a report export (HOM-138, D5): the summed
    length of every string value of the JSON payload, keys excluded.  The
    same number for every format, measured before anything is rendered."""
    return export_text_size(report_export_payload(report, provenance))


# --- CSV ----------------------------------------------------------------------


def _flatten(value: Any, path: str, rows: list[tuple[str, Any]]) -> None:
    """One row per JSON leaf, in document order, with list indexes in the path."""
    if isinstance(value, dict):
        if not value:
            rows.append((path, "{}"))
            return
        for key, item in value.items():
            _flatten(item, f"{path}.{key}" if path else str(key), rows)
        return
    if isinstance(value, list):
        if not value:
            rows.append((path, "[]"))
            return
        for index, item in enumerate(value):
            _flatten(item, f"{path}.{index}", rows)
        return
    rows.append((path, value))


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return neutralize_csv_cell(value)
    return str(value)


def _render_csv(payload: dict) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(["path", "value"])
    rows: list[tuple[str, Any]] = []
    _flatten(payload, "", rows)
    for path, value in rows:
        writer.writerow([neutralize_csv_cell(path), _csv_cell(value)])
    # A BOM first, so Excel opens the Persian text as UTF-8.
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


# --- PDF ----------------------------------------------------------------------


def _pdf_title(title: str) -> str:
    """The title as one line of plain text (amendment B4).

    Every control character (newline too) becomes a space and runs of spaces
    become one, as the page prints them.  The page draws this value (in its
    visual form) and the ``/Title`` metadata is this value (logical order),
    so both say the same thing and neither carries markup.
    """
    return " ".join("".join(char if ord(char) >= 32 else " " for char in title).split())


def _leaf_lines(value: Any, path: str, lines: list[str]) -> None:
    """``path: value`` for every str, number or bool leaf (None is skipped)."""
    if isinstance(value, dict):
        for key, item in value.items():
            _leaf_lines(item, f"{path}.{key}" if path else str(key), lines)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _leaf_lines(item, f"{path}.{index}", lines)
        return
    if value is not None and not isinstance(value, dict):
        lines.append(f"{path}: {value}")


def _quality_lines(report: CallReportResponse) -> list[str]:
    quality = report.qa.quality
    lines = [
        f"Transcription: {quality.transcription}",
        f"Speaker attribution: {quality.speaker_attribution}",
        f"Analysis: {quality.analysis}"
        + (" (stale: the transcript changed after the analysis)" if quality.analysis_stale else ""),
        f"Evidence: {quality.evidence}",
    ]
    if quality.speech_coverage_ratio is not None:
        lines.append(f"Speech coverage: {round(float(quality.speech_coverage_ratio) * 100)}%")
    for degradation in quality.degradations:
        reason = degradation.reason
        lines.append(
            f"Degraded stage: {degradation.stage} = {degradation.state}"
            + (f" ({reason})" if reason else "")
        )
    if quality.is_mock:
        lines.append("Mock output: yes (development providers)")
    return lines


# Vazirmatn needs a taller line than Helvetica: its marks reach above and below.
_TITLE = LineStyle(BOLD_FONT, 18, 28, space_after=6)
_HEADING = LineStyle(BOLD_FONT, 14, 22, space_before=12, space_after=4)
_BODY = LineStyle(REGULAR_FONT, 10, 16, space_before=3)


def _render_pdf(report: CallReportResponse, provenance: ExportProvenance, *, title: str) -> bytes:
    register_fonts()
    buffer = io.BytesIO()
    plain_title = _pdf_title(title)
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        invariant=1,
        pageCompression=0,
        title=plain_title,
        initialFontName=REGULAR_FONT,
        initialFontSize=10,
        initialLeading=16,
    )
    story: list[Flowable] = [PlainTextParagraph(plain_title, _TITLE), Spacer(1, 12)]

    def heading(text: str) -> None:
        story.append(PlainTextParagraph(clean_text(text), _HEADING))

    def body(text: str) -> None:
        story.append(PlainTextParagraph(clean_text(text), _BODY))

    revision = provenance.transcript_revision
    analysis = provenance.analysis
    privacy = provenance.privacy or {}
    heading("Provenance")
    body(f"Export version: {provenance.export_version}")
    body(
        "Report version: "
        + (str(provenance.report_version) if provenance.report_version else "none")
    )
    body(f"Meeting id: {provenance.meeting_id}")
    body(
        "Transcript revision: "
        + (f"{revision.id} (number {revision.revision_number})" if revision is not None else "none")
    )
    body(
        "Analysis: "
        + (
            f"{analysis.provider} {analysis.model}, prompt version {analysis.prompt_version}"
            if analysis is not None
            else "none"
        )
    )
    body(f"View: {privacy.get('view', 'none')}")
    body(f"Privacy enforced: {privacy.get('policy_enforced', False)}")
    body(f"Generated at: {provenance.generated_at.isoformat()}")
    story.append(Spacer(1, 6))

    heading("Summary")
    body(report.summary.overview if report.summary is not None else "none")
    story.append(Spacer(1, 6))

    heading("Outcome")
    body(f"State: {report.outcome.state}")
    for decision in report.outcome.decisions:
        body(
            f"- {decision.text} (evidence {decision.evidence_status}, "
            f"supported: {decision.supported})"
        )
    story.append(Spacer(1, 6))

    heading("Quality and findings")
    for line in _quality_lines(report):
        body(line)
    for finding in report.qa.findings:
        body(
            f"- {finding.text} (evidence {finding.evidence_status}, supported: {finding.supported})"
        )
    story.append(Spacer(1, 6))

    heading("Extraction")
    extraction_lines: list[str] = []
    _leaf_lines(report.extraction.model_dump(mode="json"), "report.extraction", extraction_lines)
    for line in extraction_lines:
        body(line)
    story.append(Spacer(1, 6))

    heading("Completeness")
    completeness = report.completeness.model_dump(mode="json")
    completeness_lines: list[str] = []
    _leaf_lines(
        {key: value for key, value in completeness.items() if key != "notices"},
        "completeness",
        completeness_lines,
    )
    completeness_lines.extend(f"notice: {notice}" for notice in completeness["notices"])
    for line in completeness_lines:
        body(line)

    document.build(story)
    return buffer.getvalue()


_MEDIA_TYPES = {
    "json": ("application/json", "json"),
    "csv": ("text/csv; charset=utf-8", "csv"),
    "pdf": ("application/pdf", "pdf"),
}


def render_report_export(
    report: CallReportResponse,
    provenance: ExportProvenance,
    format_name: str,
    *,
    title: str,
) -> RenderedExport:
    """Render one built report and its provenance as JSON, CSV or PDF."""
    if format_name not in _MEDIA_TYPES:
        raise ValueError(f"Unsupported report export format: {format_name}")
    media_type, extension = _MEDIA_TYPES[format_name]
    if format_name == "json":
        content = json.dumps(report_export_payload(report, provenance), ensure_ascii=False).encode(
            "utf-8"
        )
    elif format_name == "csv":
        content = _render_csv(report_export_payload(report, provenance))
    else:
        content = _render_pdf(report, provenance, title=title)
    return RenderedExport(content, media_type, extension)
