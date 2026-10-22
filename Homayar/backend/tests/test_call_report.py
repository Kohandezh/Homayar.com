"""HOM-59 (WP12-T02): the primary call report, one read of one meeting.

``GET /meetings/{id}/report`` puts the summary, the outcome, the QA result
(quality plus grounded findings) and the review actions the caller can take
into one body.  It is 200 in every processing state; what is missing is
null or empty and named in ``completeness``.  Texts use the caller's view
exactly like ``/summary``; every evidence reference resolves through
``/evidence/{id}``; an outcome or a finding is never upgraded beyond what the
evidence table supports; and each review action is available exactly when the
real endpoint would let this caller through.
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import delete, select

from backend.database.saas_models import (
    AuditEvent,
    Meeting,
    MeetingSpeaker,
    MeetingStatus,
    ProcessingJob,
    ProcessingStageRun,
    StageState,
)
from backend.providers.base import DiarizationProvider, IntelligenceProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.providers.schemas import (
    ASRRequest,
    ASRResult,
    DiarizationRequest,
    DiarizationResult,
    DiarizationSegment,
    DiarizationStatus,
    IntelligenceInsight,
    IntelligenceRequest,
    IntelligenceResult,
)
from backend.routers.v1.reports import REVIEW_ACTIONS
from backend.schemas.meetings import MeetingQualityResponse
from backend.services.report_service import (
    completeness_notices,
    evidence_status_of,
    is_report_complete,
    is_supported,
    outcome_state,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_authorization_matrix import _route_policy, _workspace_with_roles
from backend.tests.test_evidence_spans import TwoTurnASR, _citing_bundle, _processed
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_privacy_routing import _no_raw, _process, _set_policy

TOP_LEVEL = {
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
}
REF_KEYS = {
    "id",
    "segment_id",
    "transcript_revision_id",
    "speaker_key",
    "start_ms",
    "end_ms",
    "text_start",
    "text_end",
}
ACTION_ORDER = ["correct_transcript", "rename_speaker", "edit_action_items", "share", "export"]


# --- providers -----------------------------------------------------------------


class _RealASR(TwoTurnASR):
    """The two-turn transcript, reported as a real (non-mock) result."""

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        result = await super().transcribe(request)
        return result.model_copy(update={"is_mock": False})


class _RealDiarization(DiarizationProvider):
    async def diarize(self, request: DiarizationRequest) -> DiarizationResult:
        return DiarizationResult(
            provider="real-diarization-test",
            status=DiarizationStatus.AVAILABLE,
            segments=[
                DiarizationSegment(speaker_label="speaker_1", start_seconds=0, end_seconds=1e6)
            ],
            is_mock=False,
        )


class _Intelligence(IntelligenceProvider):
    """A fixed, non-mock analysis result."""

    def __init__(self, result: IntelligenceResult) -> None:
        self._result = result

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="report_intelligence", model="report-v1")

    async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
        return self._result


def _result(*, insights, action_items=None, summary_turns=(0, 1)) -> IntelligenceResult:
    return IntelligenceResult(
        provider="report_intelligence",
        model="report-v1",
        summary="قیمت فردا اعلام می‌شود و جلسهٔ بعد سه‌شنبه است",
        summary_source_turn_indexes=list(summary_turns),
        action_items=(
            action_items
            if action_items is not None
            else [ProviderActionItem(description="اعلام قیمت نهایی", source_turn_indexes=[0])]
        ),
        insights=insights,
        is_mock=False,
    )


GROUNDED = _result(
    insights=[
        IntelligenceInsight(
            category="decision",
            description="جلسهٔ بعد سه‌شنبه",
            confidence=0.9,
            source_turn_indexes=[1],
        ),
        IntelligenceInsight(
            category="risk", description="قیمت دیر اعلام شود", source_turn_indexes=[0]
        ),
    ]
)
UNGROUNDED_DECISION = _result(
    insights=[
        IntelligenceInsight(category="decision", description="بدون مرجع", source_turn_indexes=[9]),
    ]
)
NO_DECISION = _result(
    insights=[
        IntelligenceInsight(
            category="risk", description="قیمت دیر اعلام شود", source_turn_indexes=[0]
        ),
    ]
)


def _bundle(result: IntelligenceResult) -> ProviderBundle:
    return ProviderBundle(
        asr=_RealASR(), diarization=_RealDiarization(), intelligence=_Intelligence(result)
    )


# --- helpers -------------------------------------------------------------------


def _read(client, workspace_id: str, path: str, **params):
    response = client.get(path, headers={"X-Workspace-ID": workspace_id}, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _report(client, workspace_id: str, meeting_id: str, **params) -> dict:
    return _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/report", **params)


def _refs(report: dict) -> list[dict]:
    refs: list[dict] = []
    summary = report["summary"]
    if summary is not None:
        refs.extend(summary["evidence"])
        for point in summary["key_points"]:
            refs.extend(point["evidence"])
    for decision in report["outcome"]["decisions"]:
        refs.extend(decision["evidence"])
    for finding in report["qa"]["findings"]:
        refs.extend(finding["evidence"])
    return refs


def _assert_refs_resolve(client, workspace_id: str, meeting_id: str, report: dict) -> None:
    for ref in _refs(report):
        assert REF_KEYS <= set(ref), ref
        resolved = _read(
            client, workspace_id, f"/api/v1/meetings/{meeting_id}/evidence/{ref['id']}"
        )
        assert (resolved["start_ms"], resolved["end_ms"]) == (ref["start_ms"], ref["end_ms"])
        assert resolved["segment_id"] == ref["segment_id"]
        assert (resolved["text_start"], resolved["text_end"]) == (
            ref["text_start"],
            ref["text_end"],
        )


def _actions(report: dict) -> dict[str, dict]:
    return {item["action"]: item for item in report["review_actions"]}


def _set_status(meeting_id: str, status: MeetingStatus) -> None:
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        assert meeting is not None
        meeting.status = status
        db.commit()


# --- AC1, AC4, AC6, AC10 (complete case) ---------------------------------------


def test_completed_grounded_meeting_has_every_section_and_is_complete(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    report = _report(client, workspace_id, meeting_id)
    assert set(report) == TOP_LEVEL
    assert report["meeting_id"] == meeting_id and report["report_version"] == 1
    assert report["view"] == "original" and report["privacy_enforced"] is False
    assert report["redaction"] is None

    summary_body = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    summary = report["summary"]
    assert summary["overview"] == summary_body["overview"]
    assert summary["evidence_status"] == "sufficient" and summary["supported"] is True
    assert len(summary["evidence"]) == 2
    assert summary["analysis_run_id"] == summary_body["analysis_run_id"]
    assert summary["topics"] == summary_body["topics"]
    assert summary["sentiment"] == summary_body["sentiment"]
    assert [p["text"] for p in summary["key_points"]] == [
        p["description"] for p in summary_body["key_points"]
    ]
    assert [p["index"] for p in summary["key_points"]] == [0, 1]
    assert all(p["supported"] and p["evidence"] for p in summary["key_points"])

    outcome = report["outcome"]
    assert outcome["state"] == "explicit"
    (decision,) = outcome["decisions"]
    assert decision["text"] == "جلسهٔ بعد سه‌شنبه" and decision["key_point_index"] == 0
    assert decision["evidence_status"] == "sufficient" and decision["supported"] is True

    qa = report["qa"]
    assert qa["quality"] == _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/quality")
    kinds = [(f["finding_type"], f["finding_index"]) for f in qa["findings"]]
    assert kinds == [("summary", None), ("insight", 0), ("insight", 1), ("action_item", None)]
    action_finding = qa["findings"][-1]
    actions = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions")
    assert action_finding["finding_id"] == actions[0]["id"]
    assert action_finding["text"] == actions[0]["description"]
    assert all(f["supported"] for f in qa["findings"])
    assert qa["counts"] == {
        "total": 4,
        "supported": 4,
        "unsupported": 0,
        "sufficient": 4,
        "partial": 0,
        "insufficient": 0,
    }

    completeness = report["completeness"]
    assert completeness["is_complete"] is True and completeness["notices"] == []
    assert completeness["meeting_status"] == "completed"
    assert completeness["transcription"] == "complete"
    assert completeness["analysis"] == "complete"
    assert completeness["speaker_attribution"] == "inferred"
    assert completeness["evidence"] == "sufficient"

    assert [item["action"] for item in report["review_actions"]] == ACTION_ORDER
    assert all(item["available"] and item["reason"] is None for item in report["review_actions"])

    refs = _refs(report)
    assert refs
    _assert_refs_resolve(client, workspace_id, meeting_id, report)


# --- AC2 ---------------------------------------------------------------------------


def test_meeting_without_summary_is_200_with_an_unavailable_outcome(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "report-queued")
    accepted = _upload(client, tenant, key="report-queued-0001")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    workspace_id = tenant["workspace_id"]

    not_ready = client.get(
        f"/api/v1/meetings/{meeting_id}/summary", headers={"X-Workspace-ID": workspace_id}
    )
    assert not_ready.status_code == 404

    report = _report(client, workspace_id, meeting_id)
    assert set(report) == TOP_LEVEL
    assert report["summary"] is None
    assert report["outcome"] == {"state": "unavailable", "decisions": []}
    assert report["qa"]["findings"] == [] and report["qa"]["counts"]["total"] == 0
    completeness = report["completeness"]
    assert completeness["is_complete"] is False
    assert completeness["notices"] == [
        "processing_in_progress",
        "transcript_unavailable",
        "analysis_unavailable",
    ]
    assert completeness["transcription"] == "unavailable"

    actions = _actions(report)
    assert actions["correct_transcript"] == {
        "action": "correct_transcript",
        "available": False,
        "reason": "no_transcript",
    }
    assert actions["rename_speaker"]["reason"] == "no_transcript"
    for name in ("edit_action_items", "share", "export"):
        assert actions[name]["available"] is True and actions[name]["reason"] is None


# --- AC3 ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("result", "state", "decision_count"),
    [
        (GROUNDED, "explicit", 1),
        (UNGROUNDED_DECISION, "inferred", 1),
        (NO_DECISION, "not_stated", 0),
    ],
)
def test_outcome_state_follows_the_decisions_and_their_evidence(
    client, email_outbox, settings, monkeypatch, result, state, decision_count
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(result)
    )
    outcome = _report(client, workspace_id, meeting_id)["outcome"]
    assert outcome["state"] == state
    assert len(outcome["decisions"]) == decision_count
    if state == "inferred":
        (decision,) = outcome["decisions"]
        assert decision["evidence_status"] == "insufficient"
        assert decision["supported"] is False and decision["evidence"] == []


def test_identical_decisions_get_their_own_key_point_positions(
    client, email_outbox, settings, monkeypatch
):
    same = IntelligenceInsight(category="decision", description="تکرار", source_turn_indexes=[9])
    result = _result(
        insights=[
            same,
            IntelligenceInsight(category="risk", description="ریسک", source_turn_indexes=[0]),
            same,
        ]
    )
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(result)
    )
    report = _report(client, workspace_id, meeting_id)
    key_points = report["summary"]["key_points"]
    assert key_points[0]["text"] == key_points[2]["text"] == "تکرار"
    assert key_points[0]["evidence"] == key_points[2]["evidence"] == []
    decisions = report["outcome"]["decisions"]
    assert [d["key_point_index"] for d in decisions] == [0, 2]
    assert report["outcome"]["state"] == "inferred"


# --- AC5 and the mock notice ----------------------------------------------------


def test_findings_without_evidence_are_marked_counted_and_noticed(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_citing_bundle()
    )
    report = _report(client, workspace_id, meeting_id)
    findings = report["qa"]["findings"]
    by_text = {f["text"]: f for f in findings}
    risk = by_text["مرجع نامعتبر"]
    assert risk["finding_type"] == "insight" and risk["finding_index"] == 1
    assert risk["evidence_status"] == "insufficient"
    assert risk["supported"] is False and risk["evidence"] == []
    uncited = by_text["بدون مرجع"]
    assert uncited["finding_type"] == "action_item" and uncited["supported"] is False
    cited = by_text["اعلام قیمت نهایی"]
    assert cited["evidence_status"] == "partial" and cited["supported"] is True
    assert report["qa"]["counts"] == {
        "total": 5,
        "supported": 3,
        "unsupported": 2,
        "sufficient": 1,
        "partial": 2,
        "insufficient": 2,
    }
    completeness = report["completeness"]
    assert completeness["is_complete"] is False
    assert completeness["notices"] == ["mock_analysis", "unsupported_findings"]
    _assert_refs_resolve(client, workspace_id, meeting_id, report)


# --- AC10: one notice per condition ---------------------------------------------


@pytest.mark.parametrize(
    ("status", "notice"),
    [
        (MeetingStatus.FAILED, "processing_failed"),
        (MeetingStatus.CANCELLED, "processing_failed"),
        (MeetingStatus.ANALYZING, "processing_in_progress"),
        (MeetingStatus.PARTIAL, "analysis_partial"),
    ],
)
def test_meeting_status_notices(client, email_outbox, settings, monkeypatch, status, notice):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    _set_status(meeting_id, status)
    completeness = _report(client, workspace_id, meeting_id)["completeness"]
    assert completeness["notices"] == [notice]
    assert completeness["is_complete"] is False
    assert completeness["meeting_status"] == status.value


def test_a_partial_transcription_is_noticed(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    with inspection_session_factory()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.meeting_id == meeting_id))
        asr = db.scalar(
            select(ProcessingStageRun).where(
                ProcessingStageRun.job_id == job.id,
                ProcessingStageRun.attempt == job.attempt_count,
                ProcessingStageRun.stage == "asr",
            )
        )
        assert asr is not None
        asr.state = StageState.PARTIAL
        db.commit()
    report = _report(client, workspace_id, meeting_id)
    assert report["qa"]["quality"]["transcription"] == "partial"
    completeness = report["completeness"]
    assert completeness["transcription"] == "partial"
    assert completeness["notices"] == ["transcript_partial"]
    assert completeness["is_complete"] is False


def test_a_transcript_without_speaker_rows_cannot_rename_a_speaker(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    with inspection_session_factory()() as db:
        db.execute(delete(MeetingSpeaker).where(MeetingSpeaker.meeting_id == meeting_id))
        db.commit()
    report = _report(client, workspace_id, meeting_id)
    assert report["qa"]["quality"]["speaker_count"] == 0
    actions = _actions(report)
    assert actions["rename_speaker"] == {
        "action": "rename_speaker",
        "available": False,
        "reason": "no_speakers",
    }
    assert actions["correct_transcript"] == {
        "action": "correct_transcript",
        "available": True,
        "reason": None,
    }


def test_a_correction_after_analysis_marks_the_report_stale(
    client, email_outbox, settings, monkeypatch
):
    # HOM-63: this test is about the stale state, so no recompute runs.
    monkeypatch.setattr(settings, "recompute_on_correction", False)
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    transcript = _read(client, workspace_id, f"/api/v1/meetings/{meeting_id}/transcript")
    corrected = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json={
            "base_revision_id": transcript["revision"]["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": transcript["segments"][1]["id"], "text": "متن اصلاح‌شده"}],
        },
    )
    assert corrected.status_code == 201, corrected.text
    report = _report(client, workspace_id, meeting_id)
    assert report["completeness"]["notices"] == ["analysis_stale"]
    assert report["completeness"]["is_complete"] is False
    # Old references still resolve, and say they point at an older revision.
    refs = _refs(report)
    assert refs and all(ref["is_current_revision"] is False for ref in refs)
    _assert_refs_resolve(client, workspace_id, meeting_id, report)


# --- AC7 ---------------------------------------------------------------------------


def test_review_actions_match_what_each_endpoint_allows(
    client, email_outbox, settings, monkeypatch
):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"viewer": "viewer", "member": "member"}
    )
    tenant = {"workspace_id": owner.workspace_id, "cookies": owner.cookies}
    meeting_id, _ = _process(client, tenant, settings, "report-actions")
    base = f"/api/v1/meetings/{meeting_id}"
    viewer, member = personas["viewer"], personas["member"]

    viewer_report = viewer.get(f"{base}/report")
    assert viewer_report.status_code == 200, viewer_report.text
    denied = _actions(viewer_report.json())
    for name in ("correct_transcript", "rename_speaker", "edit_action_items", "share"):
        assert denied[name] == {"action": name, "available": False, "reason": "permission_denied"}
    assert denied["export"] == {"action": "export", "available": True, "reason": None}

    member_report = member.get(f"{base}/report")
    assert member_report.status_code == 200, member_report.text
    allowed = _actions(member_report.json())
    assert all(
        allowed[name]["available"] and allowed[name]["reason"] is None for name in ACTION_ORDER
    )

    transcript = member.get(f"{base}/transcript").json()
    speaker_id = transcript["speakers"][0]["id"]

    def call(persona, name: str):
        if name == "correct_transcript":
            current = persona.get(f"{base}/transcript").json()
            return persona.post(
                f"{base}/transcript/corrections",
                json={
                    "base_revision_id": current["revision"]["id"],
                    "reason": "fix the wording",
                    "edits": [{"segment_id": current["segments"][0]["id"], "text": f"{name} x"}],
                },
            )
        if name == "rename_speaker":
            return persona.patch(f"{base}/speakers/{speaker_id}", json={"display_name": "Ali"})
        if name == "edit_action_items":
            return persona.post(f"{base}/actions", json={"description": "پیگیری"})
        if name == "share":
            return persona.post(f"{base}/shares", json={})
        return persona.get(f"{base}/export", params={"format": "json"})

    for name in ACTION_ORDER:
        expected_denied = not denied[name]["available"]
        response = call(viewer, name)
        if expected_denied:
            assert response.status_code == 403, (name, response.text)
        else:
            assert response.status_code == 200, (name, response.text)
        response = call(member, name)
        assert response.status_code in (200, 201), (name, response.text)


def test_review_actions_read_the_permission_of_the_real_route(client):
    routes = {route.endpoint: route for route in client.app.routes if isinstance(route, APIRoute)}
    assert [action.name for action in REVIEW_ACTIONS] == ACTION_ORDER
    for action in REVIEW_ACTIONS:
        route = routes[action.endpoint]
        assert _route_policy(route).split(" ")[0] == action.permission.value, action.name


def test_a_member_without_a_transcript_gets_no_transcript_not_permission_denied(
    client, email_outbox, monkeypatch
):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    tenant = {"workspace_id": owner.workspace_id, "cookies": owner.cookies}
    accepted = _upload(client, tenant, key="report-member-queued")
    assert accepted.status_code == 202, accepted.text
    meeting_id = accepted.json()["meeting"]["id"]
    response = personas["member"].get(f"/api/v1/meetings/{meeting_id}/report")
    assert response.status_code == 200, response.text
    actions = _actions(response.json())
    assert actions["correct_transcript"]["reason"] == "no_transcript"
    assert actions["rename_speaker"]["reason"] == "no_transcript"
    assert actions["edit_action_items"]["available"] is True


# --- AC8 ---------------------------------------------------------------------------


def test_report_is_tenant_scoped_like_the_summary(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_bundle(GROUNDED)
    )
    assert _report(client, workspace_id, meeting_id)["meeting_id"] == meeting_id

    outsider = register_user(client, email_outbox, email="report-outsider@example.com")
    other_workspace = login_user(client, outsider)["workspaces"][0]["id"]
    headers = {"X-Workspace-ID": other_workspace}
    report = client.get(f"/api/v1/meetings/{meeting_id}/report", headers=headers)
    summary = client.get(f"/api/v1/meetings/{meeting_id}/summary", headers=headers)
    assert report.status_code == summary.status_code == 404
    for key in ("code", "message"):
        assert report.json()["error"][key] == summary.json()["error"][key]
    assert report.json()["error"]["code"] == "meeting_not_found"

    client.cookies.clear()
    anonymous = client.get(
        f"/api/v1/meetings/{meeting_id}/report", headers={"X-Workspace-ID": workspace_id}
    )
    anonymous_summary = client.get(
        f"/api/v1/meetings/{meeting_id}/summary", headers={"X-Workspace-ID": workspace_id}
    )
    assert anonymous.status_code == anonymous_summary.status_code == 401
    assert anonymous.json()["error"]["code"] == anonymous_summary.json()["error"]["code"]


# --- AC9 ---------------------------------------------------------------------------


def test_report_texts_follow_the_callers_view_like_the_summary(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    tenant = {"workspace_id": owner.workspace_id, "cookies": owner.cookies}
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, "report-privacy")
    base = f"/api/v1/meetings/{meeting_id}"
    member = personas["member"]

    for params in ({}, {"view": "redacted"}):
        report = member.get(f"{base}/report", params=params)
        summary = member.get(f"{base}/summary", params=params)
        assert report.status_code == summary.status_code == 200, report.text
        body, summary_body = report.json(), summary.json()
        assert body["summary"]["overview"] == summary_body["overview"]
        assert (body["view"], body["privacy_enforced"]) == ("redacted", True)
        assert (summary_body["view"], summary_body["privacy_enforced"]) == ("redacted", True)
        assert body["redaction"] == summary_body["redaction"]
        assert _no_raw(report.text), report.text
        evidence = member.get(f"{base}/evidence", params=params).json()["items"]
        texts = {item["id"]: item["text"] for item in evidence}
        assert _refs(body)  # the mock summary cites every turn
        for ref in _refs(body):
            assert ref["text"] == texts[ref["id"]]

    denied = member.get(f"{base}/report", params={"view": "original"})
    denied_summary = member.get(f"{base}/summary", params={"view": "original"})
    assert denied.status_code == denied_summary.status_code == 403
    assert denied.json()["error"]["code"] == denied_summary.json()["error"]["code"]

    original = owner.get(f"{base}/report", params={"view": "original"})
    original_summary = owner.get(f"{base}/summary", params={"view": "original"})
    assert original.status_code == original_summary.status_code == 200
    assert original.json()["summary"]["overview"] == original_summary.json()["overview"]
    assert original.json()["view"] == "original"
    with inspection_session_factory()() as db:
        surfaces = list(
            db.scalars(
                select(AuditEvent.details).where(
                    AuditEvent.event_type == "transcript.original_accessed",
                    AuditEvent.target_id == meeting_id,
                )
            )
        )
    assert {"surface": "report"} in [{"surface": d.get("surface")} for d in surfaces]


# --- pure rules ------------------------------------------------------------------


def _quality(**overrides) -> MeetingQualityResponse:
    values = {
        "transcription": "complete",
        "speaker_attribution": "inferred",
        "analysis": "complete",
        "evidence": "sufficient",
        "analysis_stale": False,
        "transcript_revision_id": "r",
        "analysed_revision_id": "r",
        "analysis_run_id": "a",
        "segment_count": 2,
        "speaker_count": 1,
        "speech_coverage_ratio": 0.9,
        "is_mock": False,
    }
    values.update(overrides)
    return MeetingQualityResponse(**values)


@pytest.mark.parametrize("status", list(MeetingStatus))
@pytest.mark.parametrize("transcription", ["complete", "partial", "unavailable"])
@pytest.mark.parametrize("analysis", ["complete", "partial", "unavailable"])
@pytest.mark.parametrize(("stale", "mock", "unsupported"), [(False, False, 0), (True, True, 2)])
def test_notices_are_empty_exactly_when_the_report_is_complete(
    status, transcription, analysis, stale, mock, unsupported
):
    quality = _quality(
        transcription=transcription, analysis=analysis, analysis_stale=stale, is_mock=mock
    )
    notices = completeness_notices(status=status, quality=quality, unsupported=unsupported)
    complete = is_report_complete(status=status, quality=quality, unsupported=unsupported)
    assert complete == (notices == [])
    assert len(notices) == len(set(notices))


def test_outcome_and_support_rules():
    assert outcome_state(has_summary=False, decisions_supported=[]) == "unavailable"
    assert outcome_state(has_summary=True, decisions_supported=[]) == "not_stated"
    assert outcome_state(has_summary=True, decisions_supported=[False, True]) == "explicit"
    assert outcome_state(has_summary=True, decisions_supported=[False]) == "inferred"
    assert is_supported("partial", 1) and is_supported("sufficient", 2)
    assert not is_supported("insufficient", 1) and not is_supported("sufficient", 0)
    assert evidence_status_of(None) == "insufficient"  # a legacy point is never upgraded
    assert evidence_status_of("bogus") == "insufficient"
