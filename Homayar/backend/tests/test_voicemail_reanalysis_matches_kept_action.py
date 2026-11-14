"""HOM-381: a re-analysis updates a kept voicemail action instead of duplicating it.

HOM-380 (ADR 0054) made a voicemail re-analysis keep every AI action a person
assigned.  The kept action kept its old text and old evidence too, and the new
run usually wrote a near-duplicate unassigned copy next to it.  Now, when the
new run produces an action that matches a kept action (K2), the kept action
takes the new text and evidence, keeps its assignee and every person-set field,
and no second row is written (K1 to K9, ADR 0054 "Matching a kept action").
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect, select

from backend.config import Settings
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    AuditEvent,
    EvidenceSpan,
    JobState,
    MeetingSummary,
    ProcessingJob,
    TranscriptRevision,
)
from backend.jobs import recompute
from backend.jobs.processor import process_job
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.providers.schemas import ActionKind
from backend.routers.v1 import voicemail as voicemail_router
from backend.services.audit_event_types import AuditEventType
from backend.services.coverage_service import (
    compute_coverage,
    parse_window,
    reference_coverage,
    unsupported_finding_count,
)
from backend.services.quality_service import aggregate_evidence
from backend.tests._database import inspection_session_factory
from backend.tests.test_authorization_matrix import _Persona, _workspace_with_roles
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_uploads_api import _valid_metadata
from backend.voicemail.models import VoicemailMessage

# A correction that changes one word: the descriptions built on it stay within
# the K2 ratio (0.897); a completely different sentence stays outside it (0.45).
ORIGINAL_TEXT = "کارشناس فروش پیگیری قرارداد را انجام می‌دهد"
CORRECTED_TEXT = "کارشناس فروش پیگیری فاکتور را انجام می‌دهد"
FAR_TEXT = "گزارش ماهانه بودجه را برای مدیر ارسال کن"
OWNER_TEXT = "کارشناس فروش"
_ids = itertools.count(1)


@dataclass(frozen=True)
class _Spec:
    """One action the run writes; ``{said}`` in the text is the first turn."""

    description: str
    kind: ActionKind = ActionKind.COMMITMENT


class _Actions(MockIntelligenceProvider):
    """The mock analysis with exactly the actions a test chooses."""

    def __init__(self, *, specs: Sequence[_Spec] = ()) -> None:
        super().__init__(allow_mock=True)
        self._specs = tuple(specs)

    async def analyze(self, request):
        result = await super().analyze(request)
        said = request.turns[0].text
        actions = [
            ProviderActionItem(
                description=spec.description.replace("{said}", said),
                owner=OWNER_TEXT,
                owner_quote=said,  # HOM-133: the words that name the owner
                source_turn_indexes=[0],
                kind=spec.kind,
            )
            for spec in self._specs
        ]
        return result.model_copy(update={"action_items": actions})


def _bundle(intelligence: MockIntelligenceProvider | None = None) -> ProviderBundle:
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript=ORIGINAL_TEXT),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=intelligence or _Actions(),
    )


@dataclass
class Crew:
    owner: _Persona
    member: _Persona
    settings: Settings
    intelligence: dict


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
        }
    )


@pytest.fixture
def crew(client, settings, monkeypatch, email_outbox) -> Crew:
    client.app.include_router(voicemail_router.router, prefix=settings.api_prefix)
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    monkeypatch.setattr("backend.voicemail.ingest.dispatch_job", lambda job_id, _settings: True)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=65.2),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
    holder = {"intelligence": _Actions()}
    monkeypatch.setattr(
        recompute, "create_provider_bundle", lambda _settings: _bundle(holder["intelligence"])
    )
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    _widen_concurrency(owner.workspace_id)
    return Crew(owner=owner, member=people["member"], settings=settings, intelligence=holder)


# --- helpers ------------------------------------------------------------------------


def _voicemail_meeting(crew: Crew) -> tuple[str, str]:
    response = crew.owner.post(
        "/api/v1/voicemails",
        data={
            "external_message_id": f"vm381-{next(_ids):04d}",
            "caller_number": "0912 345 6789",
            "mailbox": "sales",
        },
        files={"file": ("vm.wav", b"voicemail wav bytes", "audio/wav")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    return body["voicemail"]["meeting_id"], body["job"]["id"]


def _plain_meeting(crew: Crew) -> tuple[str, str]:
    response = crew.owner.client.post(
        "/api/v1/uploads",
        cookies=crew.owner.cookies,
        headers={**crew.owner.headers, "Idempotency-Key": f"hom381-{next(_ids):04d}"},
        data={"title": "Weekly customer call", "language": "fa"},
        files={"file": ("customer-call.wav", b"staged wav bytes", "audio/wav")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    return body["meeting"]["id"], body["job"]["id"]


def _process(crew: Crew, job_id: str, *, specs: Sequence[_Spec]) -> None:
    result = process_job(
        job_id,
        worker_id="hom381",
        settings=crew.settings,
        providers=_bundle(_Actions(specs=specs)),
    )
    assert result["status"] == "completed", result


def _processed(crew: Crew, *, voicemail: bool, specs: Sequence[_Spec]) -> str:
    meeting_id, job_id = _voicemail_meeting(crew) if voicemail else _plain_meeting(crew)
    _process(crew, job_id, specs=specs)
    if voicemail:
        with inspection_session_factory()() as db:
            assert (
                db.scalar(
                    select(VoicemailMessage.id).where(
                        VoicemailMessage.meeting_id == meeting_id,
                        VoicemailMessage.workspace_id == crew.owner.workspace_id,
                    )
                )
                is not None
            )
    return meeting_id


def _assign(crew: Crew, meeting_id: str, action_id: str) -> None:
    response = crew.owner.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{action_id}",
        json={"assignee_user_id": crew.member.user_id},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assignee_user_id"] == crew.member.user_id


def _actions(meeting_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(ActionItem).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _ai_rows(meeting_id: str) -> dict[str, dict]:
    return {
        action_id: row
        for action_id, row in _actions(meeting_id).items()
        if row["created_by"] == "ai"
    }


def _only_ai_action(meeting_id: str) -> str:
    ai = _ai_rows(meeting_id)
    assert len(ai) == 1
    return next(iter(ai))


def _spans_of(finding_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(EvidenceSpan).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(select(EvidenceSpan).where(EvidenceSpan.finding_id == finding_id)).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _current_run(meeting_id: str) -> AnalysisRun:
    with inspection_session_factory()() as db:
        runs = db.scalars(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(True)
            )
        ).all()
        assert len(runs) == 1
        return runs[0]


def _summary_status(meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        status = db.scalar(
            select(MeetingSummary.evidence_status).where(MeetingSummary.meeting_id == meeting_id)
        )
        assert status is not None
        return status


def _follow_up_action_id(crew: Crew, meeting_id: str) -> str | None:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(VoicemailMessage.follow_up_action_id).where(
                VoicemailMessage.meeting_id == meeting_id,
                VoicemailMessage.workspace_id == crew.owner.workspace_id,
            )
        )


def _correct(persona: _Persona, meeting_id: str, text: str = CORRECTED_TEXT) -> str:
    transcript = persona.get(f"/api/v1/meetings/{meeting_id}/transcript")
    assert transcript.status_code == 200, transcript.text
    body = transcript.json()
    response = persona.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        json={
            "base_revision_id": body["revision"]["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": body["segments"][0]["id"], "text": text}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["revision"]["id"]


def _reanalyse(
    crew: Crew, meeting_id: str, *, specs: Sequence[_Spec], corrected_text: str | None = None
) -> tuple[AnalysisRun, str]:
    """Run one re-analysis (a member's correction) and return the new run and revision."""
    crew.intelligence["intelligence"] = _Actions(specs=specs)
    revision_id = (
        _correct(crew.member, meeting_id, corrected_text)
        if corrected_text is not None
        else _correct(crew.member, meeting_id)
    )
    run = _current_run(meeting_id)
    assert run.transcript_revision_id == revision_id
    return run, revision_id


def _rerun(crew: Crew, meeting_id: str, *, specs: Sequence[_Spec]) -> tuple[AnalysisRun, str]:
    """Run the full processing job again, as an operator's re-run does."""
    with inspection_session_factory()() as db:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.meeting_id == meeting_id)
            .order_by(ProcessingJob.created_at.desc())
        )
        assert job is not None and job.state == JobState.COMPLETED
        job.state = JobState.QUEUED
        job.stage = "queued"
        job.completed_at = None
        db.commit()
        job_id = job.id
    _process(crew, job_id, specs=specs)
    with inspection_session_factory()() as db:
        revision_id = db.scalar(
            select(TranscriptRevision.id).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )
    return _current_run(meeting_id), revision_id


def _api_action(crew: Crew, meeting_id: str, action_id: str) -> dict:
    response = crew.owner.get(f"/api/v1/meetings/{meeting_id}/actions")
    assert response.status_code == 200, response.text
    actions = {action["id"]: action for action in response.json()}
    assert action_id in actions
    return actions[action_id]


def _evidence_items(crew: Crew, meeting_id: str) -> list[dict]:
    response = crew.owner.get(f"/api/v1/meetings/{meeting_id}/evidence")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _audit_events(crew: Crew, after: set[str]) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == crew.owner.workspace_id,
                    AuditEvent.id.in_(set(after)),
                )
            )
        )


def _audit_ids(crew: Crew) -> set[str]:
    with inspection_session_factory()() as db:
        return set(
            db.scalars(
                select(AuditEvent.id).where(AuditEvent.workspace_id == crew.owner.workspace_id)
            )
        )


def _assert_person_columns_stay(merged: dict, before: dict) -> None:
    """K4: everything the run does not own stays as it was."""
    for column in (
        "assignee_user_id",
        "assignee_text",
        "status",
        "due_at",
        "priority",
        "kind",
        "confidence",
        "created_by",
        "created_at",
        "workspace_id",
        "meeting_id",
    ):
        assert merged[column] == before[column], column


# --- criterion 1: a correction merges the matching new action into the kept one ------


def test_a_correction_merges_a_matching_new_action_into_the_kept_action(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]
    old_run = _current_run(meeting_id)
    follow_up = _follow_up_action_id(crew, meeting_id)
    assert before["assignee_user_id"] == crew.member.user_id

    run, corrected = _reanalyse(crew, meeting_id, specs=[_Spec("Call back: {said}")])

    # No near-duplicate copy: the kept row is the only AI action of this family.
    ai = _ai_rows(meeting_id)
    assert set(ai) == {kept}
    merged = ai[kept]
    assert merged["description"] == f"Call back: {CORRECTED_TEXT}"
    assert merged["analysis_run_id"] == run.id
    assert merged["source_segment_id"] is not None
    _assert_person_columns_stay(merged, before)

    # The old spans stay in the table (immutable) and the new ones arrive (K6).
    spans = _spans_of(kept)
    assert {span["analysis_run_id"] for span in spans.values()} == {old_run.id, run.id}

    # The action shows only its own run's spans, all on the current revision.
    api_action = _api_action(crew, meeting_id, kept)
    assert api_action["evidence"]
    assert all(ref["transcript_revision_id"] == corrected for ref in api_action["evidence"])
    shown = [item for item in _evidence_items(crew, meeting_id) if item["finding_id"] == kept]
    assert shown
    assert all(item["is_current_revision"] for item in shown)

    # K9: the voicemail's follow-up action link is untouched.
    assert _follow_up_action_id(crew, meeting_id) == follow_up


# --- criterion 2: the same merge on a full job re-run --------------------------------


def test_a_full_job_rerun_merges_a_matching_new_action_into_the_kept_action(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]
    old_run = _current_run(meeting_id)
    assert before["assignee_user_id"] == crew.member.user_id

    # The re-run transcribes the same audio, so the same text: ratio 1.0.
    run, revision_id = _rerun(crew, meeting_id, specs=[_Spec("Call back: {said}")])

    ai = _ai_rows(meeting_id)
    assert set(ai) == {kept}
    merged = ai[kept]
    assert merged["description"] == f"Call back: {ORIGINAL_TEXT}"
    assert merged["analysis_run_id"] == run.id
    _assert_person_columns_stay(merged, before)
    spans = _spans_of(kept)
    assert {span["analysis_run_id"] for span in spans.values()} == {old_run.id, run.id}
    api_action = _api_action(crew, meeting_id, kept)
    assert api_action["evidence"]
    assert all(ref["transcript_revision_id"] == revision_id for ref in api_action["evidence"])


# --- criterion 3: a non-matching new action is inserted, the kept one stays ----------


def test_a_new_action_of_a_different_kind_does_not_merge(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]
    original_revision = _current_run(meeting_id).transcript_revision_id

    _reanalyse(crew, meeting_id, specs=[_Spec("Call back: {said}", kind=ActionKind.NEXT_STEP)])

    after = _ai_rows(meeting_id)
    assert len(after) == 2
    assert after[kept] == before  # K5: not changed at all
    inserted = next(row for action_id, row in after.items() if action_id != kept)
    assert inserted["assignee_user_id"] is None
    assert inserted["kind"] == "next_step"
    # The kept action still shows its own (old) evidence (K6).
    api_action = _api_action(crew, meeting_id, kept)
    assert api_action["evidence"]
    assert all(ref["transcript_revision_id"] == original_revision for ref in api_action["evidence"])


def test_a_new_action_with_a_far_text_does_not_merge(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]

    _reanalyse(crew, meeting_id, specs=[_Spec("Call back: {said}")], corrected_text=FAR_TEXT)

    after = _ai_rows(meeting_id)
    assert len(after) == 2
    assert after[kept] == before  # K5: not changed at all
    inserted = next(row for action_id, row in after.items() if action_id != kept)
    assert inserted["assignee_user_id"] is None
    assert inserted["description"] == f"Call back: {FAR_TEXT}"


# --- criterion 4: one to one, deterministic pairing (K3) -----------------------------


def test_two_kept_and_two_matching_new_actions_pair_with_their_best_match(crew):
    meeting_id = _processed(
        crew,
        voicemail=True,
        specs=[_Spec("Call back 0: {said}"), _Spec("Call back 1: {said}")],
    )
    ai = _ai_rows(meeting_id)
    assert len(ai) == 2
    by_text = {row["description"]: action_id for action_id, row in ai.items()}
    first, second = (
        by_text[f"Call back 0: {ORIGINAL_TEXT}"],
        by_text[f"Call back 1: {ORIGINAL_TEXT}"],
    )
    _assign(crew, meeting_id, first)
    _assign(crew, meeting_id, second)

    _reanalyse(
        crew,
        meeting_id,
        specs=[_Spec("Call back 0: {said}"), _Spec("Call back 1: {said}")],
    )

    after = _ai_rows(meeting_id)
    assert set(after) == {first, second}  # every new action merged, none inserted
    assert after[first]["description"] == f"Call back 0: {CORRECTED_TEXT}"
    assert after[second]["description"] == f"Call back 1: {CORRECTED_TEXT}"


def test_two_new_actions_close_to_one_kept_action_only_merge_the_best_one(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)

    # One exact copy (ratio 1.0) and one one-word correction (ratio 0.897):
    # both pass K2, only the better one merges (K3).
    _reanalyse(
        crew,
        meeting_id,
        specs=[_Spec(f"Call back: {ORIGINAL_TEXT}"), _Spec("Call back: {said}")],
    )

    after = _ai_rows(meeting_id)
    assert len(after) == 2
    assert after[kept]["description"] == f"Call back: {ORIGINAL_TEXT}"
    inserted = next(row for action_id, row in after.items() if action_id != kept)
    assert inserted["description"] == f"Call back: {CORRECTED_TEXT}"
    assert inserted["assignee_user_id"] is None


# --- criterion 5: a kept action with a null kind never merges ------------------------


def test_a_kept_action_with_a_null_kind_never_merges(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[])
    old_run = _current_run(meeting_id)
    with inspection_session_factory()() as db:
        action = ActionItem(
            workspace_id=crew.owner.workspace_id,
            meeting_id=meeting_id,
            description=f"Call back: {CORRECTED_TEXT}",  # exactly the new text
            created_by="ai",
            kind=None,  # a row from before HOM-133
            analysis_run_id=old_run.id,
        )
        db.add(action)
        db.commit()
        kept = action.id
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]

    _reanalyse(crew, meeting_id, specs=[_Spec("Call back: {said}")])

    after = _ai_rows(meeting_id)
    assert len(after) == 2
    assert after[kept] == before  # not changed at all
    inserted = next(row for action_id, row in after.items() if action_id != kept)
    assert inserted["assignee_user_id"] is None


# --- criterion 6: an ordinary meeting keeps today's replace behaviour ----------------


def test_a_non_voicemail_meeting_still_replaces_an_assigned_ai_action(crew):
    meeting_id = _processed(crew, voicemail=False, specs=[_Spec("Call back: {said}")])
    assigned = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, assigned)

    _reanalyse(crew, meeting_id, specs=[_Spec("Call back: {said}")])

    after = _ai_rows(meeting_id)
    assert assigned not in after  # replaced, as today (A4)
    assert len(after) == 1
    (inserted,) = after.values()
    assert inserted["description"] == f"Call back: {CORRECTED_TEXT}"
    assert inserted["assignee_user_id"] is None


# --- criterion 7: the audit keeps its shape (K8) -------------------------------------


def test_the_recompute_audit_counts_merged_and_inserted_actions(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)
    audit_before = _audit_ids(crew)

    # One matching new action (merges) and one far one (is inserted).
    _reanalyse(
        crew,
        meeting_id,
        specs=[_Spec("Call back: {said}"), _Spec("Send the report: {said}")],
    )

    after = _ai_rows(meeting_id)
    assert len(after) == 2  # one merged row, one inserted row
    assert kept in after

    new_events = _audit_events(crew, _audit_ids(crew) - audit_before)
    assert not [event for event in new_events if event.event_type.startswith("action_item.")]
    completed = [
        event
        for event in new_events
        if event.event_type == AuditEventType.MEETING_RECOMPUTE_COMPLETED.value
    ]
    assert len(completed) == 1
    assert completed[0].details["action_item_count"] == 2  # merged + inserted


# --- criterion 8: coverage counts equal the call report (K7) -------------------------


def _evaluation_bucket(coverage, meeting_id: str) -> str:
    evaluated = [m for m in coverage.evaluated if m.meeting_id == meeting_id]
    assert len(evaluated) == 1
    return evaluated[0].bucket


def test_coverage_counts_equal_the_call_report_for_a_meeting_with_a_merged_action(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)
    _reanalyse(crew, meeting_id, specs=[_Spec("Call back: {said}")])
    merged = _ai_rows(meeting_id)[kept]
    assert merged["analysis_run_id"] == _current_run(meeting_id).id  # a merged action exists

    response = crew.owner.get(f"/api/v1/meetings/{meeting_id}/report")
    assert response.status_code == 200, response.text
    notices = response.json()["completeness"]["notices"]
    report_bucket = notices[0] if notices else "fully_evaluated"

    window = parse_window()
    with inspection_session_factory()() as db:
        computed = compute_coverage(
            db,
            workspace_id=crew.owner.workspace_id,
            window=window,
            recompute_max_attempts=crew.settings.recompute_max_attempts,
        )
        reference = reference_coverage(
            db,
            workspace_id=crew.owner.workspace_id,
            window=window,
            recompute_max_attempts=crew.settings.recompute_max_attempts,
        )
    assert _evaluation_bucket(computed, meeting_id) == report_bucket
    assert _evaluation_bucket(reference, meeting_id) == report_bucket


def test_unsupported_finding_count_reads_only_the_action_own_run_spans():
    """K7: the coverage count applies the K6 scope to action evidence."""
    action = SimpleNamespace(id="a1", evidence_status="partial", analysis_run_id="r2")

    def span(span_id: str, run_id: str | None, *, finding_id: str = "a1") -> SimpleNamespace:
        return SimpleNamespace(
            id=span_id,
            finding_type="action_item",
            finding_index=None,
            finding_id=finding_id,
            analysis_run_id=run_id,
        )

    old, new, human = span("s-old", "r1"), span("s-new", "r2"), span("s-human", None)
    # the new run's span and the human span are the action's evidence: supported
    assert unsupported_finding_count(summary=None, actions=[action], spans=[old, new, human]) == 0
    # only the old run's span: hidden from the action (K6), so unsupported
    assert unsupported_finding_count(summary=None, actions=[action], spans=[old]) == 1
    # an action without a run sees every span, as before
    legacy = SimpleNamespace(id="a2", evidence_status="partial", analysis_run_id=None)
    legacy_span = span("s-legacy", "r1", finding_id="a2")
    assert unsupported_finding_count(summary=None, actions=[legacy], spans=[legacy_span]) == 0


# --- K8: the run's quality reads the merged action too -------------------------------


def test_the_quality_grade_reads_the_merged_action_status(crew):
    meeting_id = _processed(crew, voicemail=True, specs=[_Spec("Call back: {said}")])
    kept = _only_ai_action(meeting_id)
    _assign(crew, meeting_id, kept)

    _reanalyse(crew, meeting_id, specs=[_Spec("Call back: {said}")])

    merged = _ai_rows(meeting_id)[kept]
    response = crew.owner.get(f"/api/v1/meetings/{meeting_id}/quality")
    assert response.status_code == 200, response.text
    quality = response.json()
    summary = _summary_status(meeting_id)
    # the grade is the aggregate of the summary and the merged action, nothing doubled
    assert quality["evidence"] == aggregate_evidence([summary, merged["evidence_status"]]).value
