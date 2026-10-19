"""WP04-T03: findings cite authorized turns and original audio offsets (ADR 0014).

Every reference a provider offers is resolved against the segments of the
revision the analysis read; anything else is rejected and counted, and the
finding's evidence status says so.  Evidence rows are immutable, resolve to
the exact text and audio range, stay bound to the analysed revision after a
correction, and are tenant-scoped.  A person's action item cites the current
revision through a ``human`` span.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    AuditEvent,
    EvidenceSpan,
    TranscriptImmutableError,
)
from backend.jobs.processor import process_job
from backend.providers.base import ASRProvider, IntelligenceProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.schemas import (
    ActionItem as ProviderActionItem,
)
from backend.providers.schemas import (
    ASRRequest,
    ASRResult,
    ASRSegment,
    IntelligenceInsight,
    IntelligenceRequest,
    IntelligenceResult,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider, _providers
from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata

TURNS = ("قیمت نهایی را تا فردا اعلام می‌کنیم", "پس جلسهٔ بعد را سه‌شنبه می‌گذاریم")


class TwoTurnASR(ASRProvider):
    """Two timed segments so references can be in or out of range."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="two_turn_asr", model="v1")

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return ASRResult(
            provider="two_turn_asr",
            model="v1",
            language="fa",
            text=" ".join(TURNS),
            segments=[
                ASRSegment(
                    index=0, text=TURNS[0], start_seconds=0.5, end_seconds=4.0, confidence=0.8
                ),
                ASRSegment(
                    index=1, text=TURNS[1], start_seconds=4.0, end_seconds=9.5, confidence=0.7
                ),
            ],
            duration_seconds=10,
            is_mock=True,
        )


class CitingIntelligence(IntelligenceProvider):
    """Cites valid turns, duplicates and turns that do not exist."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="citing_intelligence",
            model="cite-v1",
            params={"prompt_version": 7, "prompt_sha256": "ab" * 32},
        )

    async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
        return IntelligenceResult(
            provider="citing_intelligence",
            model="cite-v1",
            summary="قیمت فردا اعلام می‌شود و جلسهٔ بعد سه‌شنبه است",
            summary_source_turn_indexes=[0, 7],  # 7 does not exist -> partial
            action_items=[
                ProviderActionItem(
                    description="اعلام قیمت نهایی",
                    owner="فروش",
                    source_turn_indexes=[1, 1, 3],  # duplicate collapses, 3 rejected
                ),
                ProviderActionItem(description="بدون مرجع", source_turn_indexes=[]),
            ],
            insights=[
                IntelligenceInsight(
                    category="decision",
                    description="جلسهٔ بعد سه‌شنبه",
                    confidence=0.9,
                    source_turn_indexes=[1],
                ),
                IntelligenceInsight(
                    category="risk", description="مرجع نامعتبر", source_turn_indexes=[5]
                ),
            ],
            is_mock=True,
        )


def _citing_bundle() -> ProviderBundle:
    return ProviderBundle(
        asr=TwoTurnASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=CitingIntelligence(),
    )


def _processed(
    client, email_outbox, settings, monkeypatch, *, providers=None, expected_status="completed"
):
    credentials, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=10.0),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
    upload = _upload(client, workspace_id=workspace_id, key="evidence-upload-key")
    assert upload.status_code == 202, upload.text
    payload = upload.json()
    result = process_job(
        payload["job"]["id"],
        worker_id="evidence-worker",
        settings=settings,
        providers=providers or _providers(),
    )
    assert result["status"] == expected_status, result
    return credentials, workspace_id, payload["meeting"]["id"]


def _get(client, workspace_id: str, path: str, **params):
    response = client.get(path, headers={"X-Workspace-ID": workspace_id}, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_findings_cite_authorized_turns_and_malformed_references_are_rejected(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_citing_bundle()
    )
    transcript = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/transcript")
    revision_id = transcript["revision"]["id"]
    segments = transcript["segments"]
    assert [s["text"] for s in segments] == list(TURNS)

    summary = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert summary["evidence_status"] == "partial"  # turn 0 resolved, turn 7 rejected
    run = summary["analysis_run"]
    # offered: summary 2, insights 1 + 1, action items 2 (dedup) + 0 = 6; rejected 7, 5, 3
    assert (run["reference_count"], run["rejected_reference_count"]) == (6, 3)
    assert (run["prompt_version"], run["prompt_sha256"]) == ("7", "ab" * 32)
    assert run["schema_version"] == 2
    decision, risk = summary["key_points"]
    assert decision["evidence_status"] == "sufficient" and len(decision["evidence_ids"]) == 1
    assert risk["evidence_status"] == "insufficient" and risk["evidence_ids"] == []
    assert risk["source_turn_indexes"] == [5]  # what the provider said stays visible
    assert summary["decisions"] == [decision]

    actions = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions")
    by_description = {action["description"]: action for action in actions}
    cited = by_description["اعلام قیمت نهایی"]
    assert cited["evidence_status"] == "partial"
    assert cited["source_segment_id"] == segments[1]["id"]
    assert cited["source_timestamp_ms"] == 4000
    assert [(e["segment_id"], e["start_ms"], e["end_ms"]) for e in cited["evidence"]] == [
        (segments[1]["id"], 4000, 9500)
    ]
    assert cited["evidence"][0]["transcript_revision_id"] == revision_id
    assert cited["evidence"][0]["evidence_type"] == "turn_reference"
    uncited = by_description["بدون مرجع"]
    assert uncited["evidence_status"] == "insufficient" and uncited["evidence"] == []
    assert uncited["source_segment_id"] is None

    evidence = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence")
    assert evidence["analysis_run_id"] == run["id"]
    assert evidence["current_revision_id"] == revision_id
    kinds = sorted((item["finding_type"], item["segment_id"]) for item in evidence["items"])
    assert kinds == sorted(
        [
            ("summary", segments[0]["id"]),
            ("insight", segments[1]["id"]),
            ("action_item", segments[1]["id"]),
        ]
    )
    for item in evidence["items"]:
        segment = next(s for s in segments if s["id"] == item["segment_id"])
        assert item["text"] == segment["text"]
        assert (item["text_start"], item["text_end"]) == (0, len(segment["text"]))
        assert (item["start_ms"], item["end_ms"]) == (segment["start_ms"], segment["end_ms"])
        assert item["speaker_key"] == segment["speaker_key"]
        assert item["is_current_revision"] is True and item["revision_number"] == 1
    insight_span = next(i for i in evidence["items"] if i["finding_type"] == "insight")
    assert insight_span["id"] == decision["evidence_ids"][0]
    assert insight_span["finding_index"] == 0 and insight_span["confidence"] == 0.9
    action_span = next(i for i in evidence["items"] if i["finding_type"] == "action_item")
    assert action_span["finding_id"] == cited["id"]
    one = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence/{action_span['id']}")
    assert one == action_span
    missing = client.get(
        f"/api/v1/meetings/{meeting_id}/evidence/{'0' * 36}",
        headers={"X-Workspace-ID": workspace_id},
    )
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "evidence_not_found"

    with inspection_session_factory()() as db:
        completed = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.completed")
        )
        assert completed is not None
        assert (completed.details["evidence_count"], completed.details["rejected_references"]) == (
            3,
            3,
        )


def test_evidence_survives_correction_and_stays_bound_to_the_analysed_revision(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_citing_bundle()
    )
    original = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/transcript")
    base_id = original["revision"]["id"]
    second = original["segments"][1]
    corrected = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": base_id,
            "reason": "fix the meeting day",
            "edits": [{"segment_id": second["id"], "text": "جلسهٔ بعد را چهارشنبه می‌گذاریم"}],
        },
    )
    assert corrected.status_code == 201, corrected.text

    evidence = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence")
    assert evidence["current_revision_id"] == corrected.json()["revision"]["id"]
    assert len(evidence["items"]) == 3
    for item in evidence["items"]:
        assert item["transcript_revision_id"] == base_id
        assert item["is_current_revision"] is False and item["revision_number"] == 1
    bound = next(i for i in evidence["items"] if i["segment_id"] == second["id"])
    assert bound["text"] == TURNS[1]  # the analysed wording, not the correction

    actions = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions")
    cited = next(a for a in actions if a["description"] == "اعلام قیمت نهایی")
    assert cited["evidence"][0]["transcript_revision_id"] == base_id

    exported = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/export", format="json")
    assert exported["summary_evidence_status"] == "partial"
    action_export = next(a for a in exported["actions"] if a["description"] == "اعلام قیمت نهایی")
    assert action_export["evidence_status"] == "partial"
    assert action_export["evidence"][0]["transcript_revision_id"] == base_id
    assert action_export["evidence"][0]["segment_id"] == second["id"]
    assert (action_export["evidence"][0]["start_ms"], action_export["evidence"][0]["end_ms"]) == (
        4000,
        9500,
    )


def test_human_action_items_cite_the_current_revision(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(client, email_outbox, settings, monkeypatch)
    summary = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    assert summary["evidence_status"] == "sufficient"  # the mock cites every turn it saw
    segment_id = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/transcript")[
        "segments"
    ][0]["id"]

    cited = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={"description": "ارسال قرارداد", "source_segment_id": segment_id},
    )
    assert cited.status_code == 201, cited.text
    assert cited.json()["evidence_status"] == "sufficient"
    assert [(e["segment_id"], e["evidence_type"]) for e in cited.json()["evidence"]] == [
        (segment_id, "human")
    ]
    uncited = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={"description": "بدون مرجع"},
    )
    assert uncited.status_code == 201, uncited.text
    assert uncited.json()["evidence_status"] == "insufficient"
    assert uncited.json()["evidence"] == []

    evidence = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence")
    kinds = sorted((i["finding_type"], i["evidence_type"]) for i in evidence["items"])
    assert kinds == [("action_item", "human"), ("summary", "turn_reference")]
    human = next(i for i in evidence["items"] if i["evidence_type"] == "human")
    assert human["analysis_run_id"] is None and human["finding_id"] == cited.json()["id"]


def test_evidence_rows_are_immutable_and_tenant_scoped(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_citing_bundle()
    )
    evidence_id = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence")["items"][0][
        "id"
    ]
    with inspection_session_factory()() as db:
        span = db.get(EvidenceSpan, evidence_id)
        assert span is not None
        span.text_end = 1
        with pytest.raises(TranscriptImmutableError, match="immutable"):
            db.flush()
        db.rollback()

    outsider = register_user(client, email_outbox, email="evidence-outsider@example.com")
    other_workspace = login_user(client, outsider)["workspaces"][0]["id"]
    for path in (
        f"/api/v1/meetings/{meeting_id}/evidence",
        f"/api/v1/meetings/{meeting_id}/evidence/{evidence_id}",
    ):
        hidden = client.get(path, headers={"X-Workspace-ID": other_workspace})
        assert hidden.status_code == 404, path
