"""WP04-T06: quality, coverage and degradation are exposed consistently.

The same assessment reaches the meeting detail, the quality route, the
shared view, every export format that can carry it and the completion audit;
degraded stages surface as explicit states (never as a completed-looking
result), a correction after analysis marks the analysis stale, and the
assessment is derived from persisted facts only.
"""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select

from backend.database.saas_models import AuditEvent
from backend.providers.base import ASRProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockDiarizationProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRRequest, ASRResult
from backend.services.quality_service import (
    AnalysisState,
    SpeakerAttributionState,
    TranscriptionState,
    aggregate_evidence,
    speech_coverage,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_evidence_spans import TwoTurnASR, _citing_bundle, _get, _processed
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider


class SilentASR(ASRProvider):
    """Recognition that finds no speech at all (an honest empty outcome)."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="silent_asr", model="v1")

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return ASRResult(provider="silent_asr", model="v1", language="fa", text="", is_mock=True)


def _bundle(*, asr=None, diarization=None) -> ProviderBundle:
    return ProviderBundle(
        asr=asr or TwoTurnASR(),
        diarization=diarization or SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )


def test_pure_aggregation_rules():
    assert aggregate_evidence([]) == "insufficient"
    assert aggregate_evidence(["sufficient", "sufficient"]) == "sufficient"
    assert aggregate_evidence(["sufficient", "insufficient"]) == "partial"
    assert aggregate_evidence(["partial"]) == "partial"
    assert aggregate_evidence(["insufficient", "insufficient"]) == "insufficient"
    assert speech_coverage([(500, 4000), (4000, 9500)], duration_seconds=10) == 0.9
    assert speech_coverage([(0, 20_000)], duration_seconds=10) == 1.0  # clipped
    assert speech_coverage([], duration_seconds=0) is None


def test_quality_is_consistent_across_detail_route_share_export_and_audit(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_citing_bundle()
    )
    quality = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")
    assert quality["transcription"] == TranscriptionState.COMPLETE.value
    assert quality["speaker_attribution"] == SpeakerAttributionState.INFERRED.value
    assert quality["analysis"] == AnalysisState.COMPLETE.value
    assert quality["evidence"] == "partial"  # summary partial, one action partial, one insufficient
    assert quality["analysis_stale"] is False
    assert quality["segment_count"] == 2 and quality["speaker_count"] == 1
    assert quality["speech_coverage_ratio"] == 0.9
    assert quality["degradations"] == [] and quality["is_mock"] is True
    assert quality["usage"] == {"state": "committed", "audio_seconds": 10}
    assert quality["transcript_revision_id"] == quality["analysed_revision_id"]

    detail = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}")
    assert detail["quality"] == quality
    exported = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/export", format="json")
    assert exported["quality"] == quality
    text = client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        headers={"X-Workspace-ID": workspace_id},
        params={"format": "txt"},
    ).text
    assert "Quality\nTranscription: complete\nSpeaker attribution: inferred\n" in text
    assert "Analysis: complete\nEvidence: partial\nSpeech coverage: 90%" in text
    assert "Mock output: yes" in text
    vtt = client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        headers={"X-Workspace-ID": workspace_id},
        params={"format": "vtt"},
    ).text
    assert vtt.startswith("WEBVTT\n\nNOTE\nTranscription: complete\n")
    srt = client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        headers={"X-Workspace-ID": workspace_id},
        params={"format": "srt"},
    ).text
    assert "Transcription" not in srt  # SubRip has no comment syntax

    with inspection_session_factory()() as db:
        completed = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.completed")
        )
        assert completed is not None
        assert {
            key: completed.details[key]
            for key in ("asr_state", "speaker_state", "analysis_state", "evidence_state")
        } == {
            "asr_state": "complete",
            "speaker_state": "inferred",
            "analysis_state": "complete",
            "evidence_state": "partial",
        }
        assert completed.details["speech_coverage_ratio"] == 0.9

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
    raw_token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]
    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{raw_token}")
    assert shared.status_code == 200, shared.text
    # The share ceiling hides jobs, stage runs and usage: the result states are
    # identical, the job-derived parts are absent.
    job_derived = {"usage", "degradations"}
    shared_quality = shared.json()["quality"]
    assert {k: v for k, v in shared_quality.items() if k not in job_derived} == {
        k: v for k, v in quality.items() if k not in job_derived
    }
    assert shared_quality["usage"] is None and shared_quality["degradations"] == []
    shared_export = client.get(f"/api/v1/shares/{raw_token}/export", params={"format": "json"})
    assert shared_export.json()["quality"] == shared_quality


def test_degraded_stages_are_exposed_as_states_not_hidden(
    client, email_outbox, settings, monkeypatch
):
    # Unavailable diarization: speakers unknown, meeting partial, degradation listed.
    _, workspace_id, meeting_id = _processed(
        client,
        email_outbox,
        settings,
        monkeypatch,
        providers=_bundle(diarization=MockDiarizationProvider(allow_mock=True)),
        expected_status="partial",
    )
    quality = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")
    assert quality["transcription"] == "complete"
    assert quality["speaker_attribution"] == "unknown"
    assert quality["analysis"] == "complete"
    assert quality["evidence"] == "sufficient"  # the mock cites every turn it saw
    assert quality["degradations"] == [
        {"stage": "diarization", "state": "unavailable", "reason": "diarization_unavailable"}
    ]
    assert _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}")["status"] == "partial"


def test_empty_transcript_is_unavailable_everywhere(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client,
        email_outbox,
        settings,
        monkeypatch,
        providers=_bundle(asr=SilentASR()),
        expected_status="partial",
    )
    quality = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")
    assert quality["transcription"] == "unavailable"
    assert quality["speaker_attribution"] == "unknown"
    assert quality["analysis"] == "unavailable"
    assert quality["evidence"] == "insufficient"
    assert quality["segment_count"] == 0 and quality["speech_coverage_ratio"] == 0.0
    assert quality["transcript_revision_id"] is not None  # the empty outcome is recorded
    assert {(d["stage"], d["state"], d["reason"]) for d in quality["degradations"]} == {
        ("asr", "insufficient_evidence", "empty_transcript"),
        ("diarization", "skipped", "no_transcript"),
        ("intelligence", "skipped", "no_transcript"),
    }
    # The open charging decision is exposed, not hidden: the audio was processed.
    assert quality["usage"]["state"] == "committed"
    summary = client.get(
        f"/api/v1/meetings/{meeting_id}/summary", headers={"X-Workspace-ID": workspace_id}
    )
    assert summary.status_code == 404 and summary.json()["error"]["code"] == "summary_not_ready"


def test_correction_after_analysis_marks_the_analysis_stale(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_citing_bundle()
    )
    before = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")
    assert before["analysis_stale"] is False
    transcript = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/transcript")
    corrected = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": transcript["revision"]["id"],
            "edits": [{"segment_id": transcript["segments"][0]["id"], "text": "متن اصلاح‌شده"}],
        },
    )
    assert corrected.status_code == 201, corrected.text
    after = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")
    assert after["analysis_stale"] is True
    assert after["analysed_revision_id"] == before["transcript_revision_id"]
    assert after["transcript_revision_id"] == corrected.json()["revision"]["id"]
    assert after["transcription"] == "complete" and after["analysis"] == "complete"
    text = client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        headers={"X-Workspace-ID": workspace_id},
        params={"format": "txt"},
    ).text
    assert "Analysis: complete (stale: the transcript changed after the analysis)" in text
