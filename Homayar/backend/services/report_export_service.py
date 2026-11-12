"""Deterministic call report export renderers (HOM-137, F70).

Pure functions: no database, no clock (the caller passes ``generated_at``),
no network, no file reads.  ``report_export_payload`` is the single JSON
source the CSV flattening describes; the PDF renderer escapes every dynamic
string so stored markup can never become live markup.  Persian text renders
without raising, but Helvetica shows no Persian glyphs (HOM-138).
"""

from __future__ import annotations

import csv
import io
import json
import unicodedata
import xml.sax.saxutils
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from backend.schemas.reports import CallReportResponse
from backend.services.export_service import (
    EXPORT_VERSION,
    ExportAnalysis,
    ExportRevision,
    RenderedExport,
)

__all__ = [
    "EXPORT_VERSION",
    "ExportAnalysis",
    "ExportProvenance",
    "neutralize_csv_cell",
    "render_report_export",
    "report_export_payload",
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


def _pdf_text(value: Any) -> str:
    """The one helper every dynamic PDF string passes through.

    C0 control characters except ``\\n`` become a space, then XML escaping,
    then newlines become ``<br/>``.  No dynamic text reaches Paragraph
    markup unescaped, so stored ``<img>``, ``<a>`` or ``<font>`` prints as
    text and never loads a file or adds a link.
    """
    if not isinstance(value, str):
        value = str(value)
    cleaned = "".join(char if char == "\n" or ord(char) >= 32 else " " for char in value)
    return xml.sax.saxutils.escape(cleaned).replace("\n", "<br/>")


def _pdf_title(title: str) -> str:
    """The title as one line of plain text (amendment B4).

    Every control character (newline too) becomes a space and runs of spaces
    become one, as the page prints them.  The visible title goes through
    ``_pdf_text`` from this value and the ``/Title`` metadata is this value,
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


def _render_pdf(report: CallReportResponse, provenance: ExportProvenance, *, title: str) -> bytes:
    buffer = io.BytesIO()
    plain_title = _pdf_title(title)
    document = SimpleDocTemplate(
        buffer, pagesize=A4, invariant=1, pageCompression=0, title=plain_title
    )
    styles = getSampleStyleSheet()
    story: list[Paragraph] = [
        Paragraph(_pdf_text(plain_title), styles["Title"]),
        Spacer(1, 12),
    ]

    def heading(text: str) -> None:
        story.append(Paragraph(_pdf_text(text), styles["Heading2"]))

    def body(text: str) -> None:
        story.append(Paragraph(_pdf_text(text), styles["BodyText"]))

    revision = provenance.transcript_revision
    analysis = provenance.analysis
    privacy = provenance.privacy or {}
    heading("Provenance")
    export_version_line = f"Export version: {provenance.export_version}"
    story.extend(
        [
            Paragraph(_pdf_text(export_version_line), styles["BodyText"]),
            Paragraph(
                _pdf_text(
                    "Report version: "
                    + (str(provenance.report_version) if provenance.report_version else "none")
                ),
                styles["BodyText"],
            ),
            Paragraph(_pdf_text(f"Meeting id: {provenance.meeting_id}"), styles["BodyText"]),
            Paragraph(
                _pdf_text(
                    "Transcript revision: "
                    + (
                        f"{revision.id} (number {revision.revision_number})"
                        if revision is not None
                        else "none"
                    )
                ),
                styles["BodyText"],
            ),
            Paragraph(
                _pdf_text(
                    "Analysis: "
                    + (
                        f"{analysis.provider} {analysis.model}, "
                        f"prompt version {analysis.prompt_version}"
                        if analysis is not None
                        else "none"
                    )
                ),
                styles["BodyText"],
            ),
            Paragraph(_pdf_text(f"View: {privacy.get('view', 'none')}"), styles["BodyText"]),
            Paragraph(
                _pdf_text(f"Privacy enforced: {privacy.get('policy_enforced', False)}"),
                styles["BodyText"],
            ),
            Paragraph(
                _pdf_text(f"Generated at: {provenance.generated_at.isoformat()}"),
                styles["BodyText"],
            ),
            Spacer(1, 6),
        ]
    )

    heading("Summary")
    body(report.summary.overview if report.summary is not None else "none")
    story.append(Spacer(1, 6))

    heading("Outcome")
    story.append(Paragraph(_pdf_text(f"State: {report.outcome.state}"), styles["BodyText"]))
    for decision in report.outcome.decisions:
        story.append(
            Paragraph(
                _pdf_text(
                    f"- {decision.text} (evidence {decision.evidence_status}, "
                    f"supported: {decision.supported})"
                ),
                styles["BodyText"],
            )
        )
    story.append(Spacer(1, 6))

    heading("Quality and findings")
    for line in _quality_lines(report):
        body(line)
    for finding in report.qa.findings:
        story.append(
            Paragraph(
                _pdf_text(
                    f"- {finding.text} (evidence {finding.evidence_status}, "
                    f"supported: {finding.supported})"
                ),
                styles["BodyText"],
            )
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
