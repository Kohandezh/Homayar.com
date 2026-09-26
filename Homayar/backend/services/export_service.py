"""Deterministic meeting export renderers for text-based formats."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ExportSegment:
    sequence: int
    speaker: str
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class ExportEvidence:
    """A citation as exported: revision, segment, text range, audio range."""

    id: str
    transcript_revision_id: str
    segment_id: str
    text_start: int
    text_end: int
    start_ms: int
    end_ms: int
    evidence_type: str


@dataclass(frozen=True)
class ExportAction:
    description: str
    assignee: str | None
    due_at: datetime | None
    priority: str
    status: str
    source_timestamp_ms: int | None
    evidence_status: str = "insufficient"
    evidence: list[ExportEvidence] = field(default_factory=list)


@dataclass(frozen=True)
class ExportRevision:
    """Which immutable transcript revision the export was rendered from."""

    id: str
    revision_number: int
    source: str
    created_at: datetime
    asr_provider: str | None
    asr_model: str | None
    is_mock: bool


@dataclass(frozen=True)
class MeetingExport:
    meeting_id: str
    title: str
    language: str
    duration_seconds: int
    created_at: datetime
    summary: str | None
    topics: list[str]
    segments: list[ExportSegment]
    actions: list[ExportAction]
    transcript_revision: ExportRevision | None = None
    summary_evidence_status: str | None = None
    # Quality/coverage assessment as a plain mapping (WP04-T06); rendered as a
    # block in text formats and copied into the JSON export.
    quality: dict[str, Any] | None = None
    # Privacy provenance (WP07-T06): which view the texts are and, for a
    # redacted export, the detector version, classes and policy digest.
    privacy: dict[str, Any] | None = None


@dataclass(frozen=True)
class RenderedExport:
    content: bytes
    media_type: str
    extension: str


def _timestamp(milliseconds: int, *, decimal: str) -> str:
    value = max(0, milliseconds)
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{decimal}{millis:03}"


def _quality_lines(export: MeetingExport) -> list[str]:
    quality = export.quality
    if quality is None:
        return []
    lines = [
        f"Transcription: {quality.get('transcription')}",
        f"Speaker attribution: {quality.get('speaker_attribution')}",
        f"Analysis: {quality.get('analysis')}"
        + (
            " (stale: the transcript changed after the analysis)"
            if quality.get("analysis_stale")
            else ""
        ),
        f"Evidence: {quality.get('evidence')}",
    ]
    ratio = quality.get("speech_coverage_ratio")
    if ratio is not None:
        lines.append(f"Speech coverage: {round(float(ratio) * 100)}%")
    for degradation in quality.get("degradations") or []:
        reason = degradation.get("reason")
        lines.append(
            f"Degraded stage: {degradation.get('stage')} = {degradation.get('state')}"
            + (f" ({reason})" if reason else "")
        )
    if quality.get("is_mock"):
        lines.append("Mock output: yes (development providers)")
    return lines


def _privacy_lines(export: MeetingExport) -> list[str]:
    privacy = export.privacy
    if not privacy or (not privacy.get("policy_enforced") and privacy.get("view") != "redacted"):
        return []
    lines = [f"Transcript view: {privacy.get('view')}"]
    if privacy.get("view") == "redacted":
        lines.append(
            f"Redaction: {privacy.get('detector_version')} "
            f"({', '.join(privacy.get('classes') or [])})"
        )
    if privacy.get("notice"):
        lines.append(str(privacy["notice"]))
    return lines


def _render_subtitles(export: MeetingExport, *, vtt: bool) -> bytes:
    blocks = ["WEBVTT\n"] if vtt else []
    if vtt and (export.quality is not None or _privacy_lines(export)):
        blocks.append(
            "NOTE\n" + "\n".join([*_privacy_lines(export), *_quality_lines(export)]) + "\n"
        )
    decimal = "." if vtt else ","
    for index, segment in enumerate(export.segments, start=1):
        if not vtt:
            blocks.append(str(index))
        blocks.append(
            f"{_timestamp(segment.start_ms, decimal=decimal)} --> "
            f"{_timestamp(segment.end_ms, decimal=decimal)}"
        )
        blocks.append(f"{segment.speaker}: {segment.text}")
        blocks.append("")
    return "\n".join(blocks).encode("utf-8")


def _render_text(export: MeetingExport) -> bytes:
    lines = [export.title, "=" * len(export.title), ""]
    if export.summary:
        lines.extend(["Summary", export.summary, ""])
    if export.topics:
        lines.extend(["Topics", ", ".join(export.topics), ""])
    quality_lines = _quality_lines(export)
    if quality_lines:
        lines.extend(["Quality", *quality_lines, ""])
    privacy_lines = _privacy_lines(export)
    if privacy_lines:
        lines.extend(["Privacy", *privacy_lines, ""])
    if export.transcript_revision is not None:
        revision = export.transcript_revision
        lines.append(
            f"Transcript (revision {revision.revision_number}, {revision.source}, "
            f"{revision.created_at.isoformat()})"
        )
    else:
        lines.append("Transcript (no revision)")
    for segment in export.segments:
        lines.append(
            f"[{_timestamp(segment.start_ms, decimal='.')}] {segment.speaker}: {segment.text}"
        )
    if export.actions:
        lines.extend(["", "Action items"])
        for action in export.actions:
            owner = f" | {action.assignee}" if action.assignee else ""
            lines.append(f"- [{action.status}] ({action.priority}) {action.description}{owner}")
    return "\n".join(lines).encode("utf-8")


def _render_json(export: MeetingExport) -> bytes:
    payload = asdict(export)
    payload["created_at"] = export.created_at.isoformat()
    if payload["transcript_revision"] is not None:
        payload["transcript_revision"]["created_at"] = (
            export.transcript_revision.created_at.isoformat()
            if export.transcript_revision is not None
            else None
        )
    for action in payload["actions"]:
        if action["due_at"] is not None:
            action["due_at"] = action["due_at"].isoformat()
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def render_meeting_export(export: MeetingExport, format_name: str) -> RenderedExport:
    if format_name == "txt":
        return RenderedExport(_render_text(export), "text/plain; charset=utf-8", "txt")
    if format_name == "json":
        return RenderedExport(_render_json(export), "application/json", "json")
    if format_name == "srt":
        return RenderedExport(
            _render_subtitles(export, vtt=False),
            "application/x-subrip; charset=utf-8",
            "srt",
        )
    if format_name == "vtt":
        return RenderedExport(
            _render_subtitles(export, vtt=True),
            "text/vtt; charset=utf-8",
            "vtt",
        )
    raise ValueError("Unsupported export format")
