"""HOM-381 oracle, written from SPEC.md only (rules K1 to K9).

On a voicemail meeting a re-analysis keeps every AI action a person assigned
(HOM-380).  A new action that matches a kept action (same non-null kind, text
ratio at least 0.8 after normalization, one to one by best ratio) now updates
the kept row in place: new text and evidence, same id, assignee and person-set
fields.  The matching new action is not written as its own row.  An action
shows only the evidence of its own run, in the API, the call report and the
coverage counts alike.  Every other case keeps today's behaviour.
"""

from __future__ import annotations

import dataclasses
import difflib
import hashlib
import itertools
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import inspect, select, update

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
    TranscriptSegment,
)
from backend.jobs import recompute
from backend.jobs.processor import process_job
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.providers.schemas import ActionKind, ActionPriority
from backend.routers.v1 import voicemail as voicemail_router
from backend.services.audit_event_types import AuditEventType
from backend.services.coverage_service import (
    compute_coverage,
    parse_window,
    unsupported_finding_count,
)
from backend.speech.text_normalize import normalize_for_matching
from backend.tests._database import inspection_session_factory
from backend.tests.test_authorization_matrix import _Persona, _workspace_with_roles
from backend.tests.test_coverage import (
    Tenant,
    evaluation_buckets,
    report,
    seed_call,
    unsupported_from_rows,
)
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_uploads_api import _valid_metadata
from backend.voicemail.models import VoicemailMessage

TRANSCRIPT = "کارشناس فروش پیگیری قرارداد را انجام می‌دهد"
CORRECTED = "کارشناس فروش پیگیری فاکتور را انجام می‌دهد"
OWNER_TEXT = "کارشناس فروش"

T = "تماس با مشتری درباره تمدید قرارداد فروش سالانه تا پایان هفته آینده"
T1 = "تماس با مشتری درباره تمدید فاکتور فروش سالانه تا پایان هفته آینده"
T2 = "تماس با مشتری درباره تمدید فاکتور فروش سالانه تا پایان ماه آینده"
CLOSEST = "تماس با مشتری درباره تمدید قرارداد فروش سالانه تا پایان ماه آینده"
TIE_A = "ثبت با مشتری درباره تمدید قرارداد فروش سالانه تا پایان هفته آینده"
TIE_B = "تماس با مدیر درباره تمدید قرارداد فروش سالانه تا پایان هفته آینده"
FAR = "ارسال پیش فاکتور جدید برای مدیر خرید شرکت"
S = "ارسال پیش فاکتور جدید برای مدیر خرید شرکت تا پایان امروز"
S1 = "ارسال پیش فاکتور تازه برای مدیر خرید شرکت تا پایان امروز"
W = "رزرو جلسه حضوری با تیم فنی مشتری در دفتر مرکزی"
W1 = "رزرو جلسه آنلاین با تیم فنی مشتری در دفتر مرکزی"
V = "بررسی شکایت مشتری درباره تاخیر در تحویل کالا"
R = "بازگرداندن تماس بی پاسخ صندوق صوتی همان روز"
Y = "به روزرسانی شماره حساب مشتری در سامانه مالی"
Z = "ارسال گزارش بازدید به مدیر منطقه تا پنجشنبه"
NEAR_BASE = "تماس با مشتری درباره تمدید قرارداد فروش سالانه"
NEAR_ABOVE = "تماس با مشتری درباره تمدید فاکتور خرید سالانه"
NEAR_BELOW = "تماس با مدیر درباره تمدید فاکتور فروش ماهانه"
PERSIAN_SPELLING = "کارشناس یکی از مشتری‌های کیش را برای پیگیری یک کار کوچک می‌بیند"
ARABIC_SPELLING = PERSIAN_SPELLING.replace("ک", "ك").replace("ی", "ي").replace("\u200c", " ")
BLANK = "!!! ..."


def _cross(x: str, y: str, z: str) -> str:
    return (
        f"تماس {x} با مشتری درباره تمدید قرارداد فروش سالانه "
        f"و ارسال نسخه {y} برای دفتر مرکزی تا پایان هفته {z}"
    )


KEPT_1 = _cross("تلفنی", "نهایی", "اینده")
KEPT_2 = _cross("تلفنی", "امضا شده", "آینده")
NEW_1 = _cross("حضوری", "امضا شده", "آینده")
NEW_2 = _cross("تلفنی", "امضا شده", "اینده")

THRESHOLD = 0.8
PATHS = ["recompute", "process_job"]
PERSON_SET = {"due_at": "2026-12-01T09:00:00+00:00", "priority": "high", "status": "in_progress"}
MERGE_WRITES = {
    "description",
    "source_segment_id",
    "source_timestamp_ms",
    "evidence_status",
    "analysis_run_id",
    "updated_at",
}
_ids = itertools.count(1)


@dataclass(frozen=True)
class Say:
    text: str
    kind: ActionKind = ActionKind.COMMITMENT
    owner: bool = False
    priority: ActionPriority = ActionPriority.LOW
    turns: tuple[int, ...] = (0,)


class _Scripted(MockIntelligenceProvider):
    def __init__(self, script: tuple[Say, ...]) -> None:
        super().__init__(allow_mock=True)
        self._script = script

    def identity(self):
        base = super().identity()
        digest = hashlib.sha256(repr(self._script).encode()).hexdigest()
        return dataclasses.replace(base, params={**base.params, "script": digest})

    async def analyze(self, request):
        result = await super().analyze(request)
        said = request.turns[0].text
        actions = [
            ProviderActionItem(
                description=item.text,
                kind=item.kind,
                priority=item.priority,
                source_turn_indexes=list(item.turns),
                owner=OWNER_TEXT if item.owner else None,
                owner_quote=said if item.owner else None,
            )
            for item in self._script
        ]
        return result.model_copy(update={"action_items": actions})


def _bundle(script: tuple[Say, ...]) -> ProviderBundle:
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript=TRANSCRIPT),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=_Scripted(script),
    )


@dataclass
class Crew:
    owner: _Persona
    member: _Persona
    settings: Settings
    script: tuple[Say, ...] = ()


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
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    _widen_concurrency(owner.workspace_id)
    team = Crew(owner=owner, member=people["member"], settings=settings)
    monkeypatch.setattr(recompute, "create_provider_bundle", lambda _settings: _bundle(team.script))
    return team


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(
        None, normalize_for_matching(a, "fa"), normalize_for_matching(b, "fa"), autojunk=False
    ).ratio()


def _voicemail_meeting(crew: Crew, language: str) -> tuple[str, str]:
    response = crew.owner.post(
        "/api/v1/voicemails",
        data={
            "external_message_id": f"vm381-{next(_ids):04d}",
            "caller_number": "0912 345 6789",
            "mailbox": "sales",
            "language": language,
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


def _process(crew: Crew, job_id: str) -> None:
    result = process_job(
        job_id, worker_id="hom381-oracle", settings=crew.settings, providers=_bundle(crew.script)
    )
    assert result["status"] == "completed", result


def _is_voicemail(meeting_id: str, workspace_id: str) -> bool:
    with inspection_session_factory()() as db:
        return (
            db.scalar(
                select(VoicemailMessage.id).where(
                    VoicemailMessage.meeting_id == meeting_id,
                    VoicemailMessage.workspace_id == workspace_id,
                )
            )
            is not None
        )


def _analysed(
    crew: Crew, script: list[Say], *, voicemail: bool = True, language: str = "fa"
) -> str:
    crew.script = tuple(script)
    meeting_id, job_id = _voicemail_meeting(crew, language) if voicemail else _plain_meeting(crew)
    _process(crew, job_id)
    assert _is_voicemail(meeting_id, crew.owner.workspace_id) is voicemail
    return meeting_id


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


def _ai_id(meeting_id: str, text: str) -> str:
    matches = [
        action_id for action_id, row in _ai_rows(meeting_id).items() if row["description"] == text
    ]
    assert len(matches) == 1, matches
    return matches[0]


def _written(meeting_id: str, run: AnalysisRun) -> set[str]:
    return {
        action_id
        for action_id, row in _ai_rows(meeting_id).items()
        if row["analysis_run_id"] == run.id
    }


def _inserted(meeting_id: str, run: AnalysisRun, text: str, kind: str) -> str:
    matches = [
        action_id
        for action_id, row in _ai_rows(meeting_id).items()
        if row["description"] == text and row["analysis_run_id"] == run.id
    ]
    assert len(matches) == 1, matches
    row = _actions(meeting_id)[matches[0]]
    assert row["assignee_user_id"] is None
    assert row["kind"] == kind
    return matches[0]


def _family(meeting_id: str, text: str, kind: str) -> set[str]:
    return {
        action_id
        for action_id, row in _ai_rows(meeting_id).items()
        if row["kind"] == kind
        and normalize_for_matching(row["description"], "fa")
        and _ratio(row["description"], text) >= THRESHOLD
    }


def _assign(crew: Crew, meeting_id: str, action_id: str, **fields) -> None:
    response = crew.owner.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{action_id}",
        json={"assignee_user_id": crew.member.user_id, **fields},
    )
    assert response.status_code == 200, response.text
    assert response.json()["assignee_user_id"] == crew.member.user_id


def _set_created_at(action_id: str, when: datetime) -> None:
    with inspection_session_factory()() as db:
        db.execute(update(ActionItem).where(ActionItem.id == action_id).values(created_at=when))
        db.commit()


def _direct_action(crew: Crew, meeting_id: str, text: str, *, created_by: str) -> str:
    with inspection_session_factory()() as db:
        action = ActionItem(
            workspace_id=crew.owner.workspace_id,
            meeting_id=meeting_id,
            description=text,
            created_by=created_by,
            kind="commitment",
            assignee_user_id=crew.member.user_id,
        )
        db.add(action)
        db.commit()
        return action.id


def _rules_follow_up(crew: Crew, meeting_id: str) -> str:
    with inspection_session_factory()() as db:
        action = ActionItem(
            workspace_id=crew.owner.workspace_id,
            meeting_id=meeting_id,
            description="Return the customer's call",
            created_by="voicemail_rules",
            assignee_user_id=crew.member.user_id,
        )
        db.add(action)
        db.flush()
        voicemail = db.scalar(
            select(VoicemailMessage).where(VoicemailMessage.meeting_id == meeting_id)
        )
        assert voicemail is not None
        voicemail.follow_up_action_id = action.id
        db.commit()
        return action.id


def _spans(action_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(EvidenceSpan).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(EvidenceSpan).where(
                EvidenceSpan.finding_type == "action_item", EvidenceSpan.finding_id == action_id
            )
        ).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _shown(action_id: str) -> list[tuple[str, str | None, str]]:
    with inspection_session_factory()() as db:
        action = db.scalar(select(ActionItem).where(ActionItem.id == action_id))
        assert action is not None
        return [
            (span.id, span.analysis_run_id, span.transcript_revision_id) for span in action.evidence
        ]


def _current_run(meeting_id: str) -> AnalysisRun:
    with inspection_session_factory()() as db:
        runs = db.scalars(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(True)
            )
        ).all()
        assert len(runs) == 1
        return runs[0]


def _current_revision(meeting_id: str) -> TranscriptRevision:
    with inspection_session_factory()() as db:
        revision = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )
        assert revision is not None
        return revision


def _segments(revision_id: str) -> dict[str, int]:
    with inspection_session_factory()() as db:
        rows = db.execute(
            select(TranscriptSegment.id, TranscriptSegment.start_ms).where(
                TranscriptSegment.revision_id == revision_id
            )
        ).all()
        return {segment_id: start_ms for segment_id, start_ms in rows}


def _follow_up(meeting_id: str) -> str | None:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(VoicemailMessage.follow_up_action_id).where(
                VoicemailMessage.meeting_id == meeting_id
            )
        )


def _audit_ids(workspace_id: str) -> set[str]:
    with inspection_session_factory()() as db:
        return set(db.scalars(select(AuditEvent.id).where(AuditEvent.workspace_id == workspace_id)))


def _audit_rows(ids: set[str]) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.id.in_(ids))
                .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
            )
        )


def _assignee_changes(workspace_id: str) -> set[str]:
    with inspection_session_factory()() as db:
        return set(
            db.scalars(
                select(AuditEvent.id).where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type == AuditEventType.ACTION_ITEM_ASSIGNEE_CHANGED.value,
                )
            )
        )


def _correct(persona: _Persona, meeting_id: str) -> str:
    transcript = persona.get(f"/api/v1/meetings/{meeting_id}/transcript")
    assert transcript.status_code == 200, transcript.text
    body = transcript.json()
    response = persona.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        json={
            "base_revision_id": body["revision"]["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": body["segments"][0]["id"], "text": CORRECTED}],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["revision"]["id"]


def _rerun(crew: Crew, meeting_id: str) -> str:
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
    _process(crew, job_id)
    return job_id


def _reanalyse(crew: Crew, path: str, meeting_id: str, script: list[Say]) -> AnalysisRun:
    crew.script = tuple(script)
    previous_run = _current_run(meeting_id)
    previous_revision = _current_revision(meeting_id).id
    if path == "recompute":
        revision_id = _correct(crew.member, meeting_id)
        run = _current_run(meeting_id)
        assert run.job_id is None
    else:
        job_id = _rerun(crew, meeting_id)
        revision_id = _current_revision(meeting_id).id
        run = _current_run(meeting_id)
        assert run.job_id == job_id
    assert revision_id != previous_revision
    assert run.id != previous_run.id
    assert run.transcript_revision_id == revision_id
    return run


def _api_actions(persona, meeting_id: str) -> dict[str, dict]:
    response = persona.get(f"/api/v1/meetings/{meeting_id}/actions")
    assert response.status_code == 200, response.text
    return {action["id"]: action for action in response.json()}


def _report_body(persona, meeting_id: str) -> dict:
    response = persona.get(f"/api/v1/meetings/{meeting_id}/report")
    assert response.status_code == 200, response.text
    return response.json()


def _report_finding(body: dict, action_id: str) -> dict:
    findings = [
        finding
        for finding in body["qa"]["findings"]
        if finding["finding_type"] == "action_item" and finding["finding_id"] == action_id
    ]
    assert len(findings) == 1, findings
    return findings[0]


def _assert_integrity(meeting_id: str, run: AnalysisRun) -> None:
    rows = _actions(meeting_id)
    with inspection_session_factory()() as db:
        pointed = set(
            db.scalars(
                select(EvidenceSpan.finding_id).where(
                    EvidenceSpan.meeting_id == meeting_id,
                    EvidenceSpan.finding_type == "action_item",
                    EvidenceSpan.analysis_run_id == run.id,
                )
            )
        )
    assert pointed <= set(rows)
    assert pointed == _written(meeting_id, run)


def _assert_merged(
    crew: Crew,
    meeting_id: str,
    kept_id: str,
    before: dict,
    run: AnalysisRun,
    text: str,
    *,
    evidence_status: str = "sufficient",
) -> None:
    rows = _actions(meeting_id)
    assert kept_id in rows, "the kept action was removed"
    row = rows[kept_id]
    assert row["description"] == text
    assert row["analysis_run_id"] == run.id
    assert row["evidence_status"] == evidence_status
    assert row["assignee_user_id"] == crew.member.user_id
    assert {key: value for key, value in row.items() if key not in MERGE_WRITES} == {
        key: value for key, value in before.items() if key not in MERGE_WRITES
    }
    segments = _segments(run.transcript_revision_id)
    assert row["source_segment_id"] in segments
    assert row["source_timestamp_ms"] == segments[row["source_segment_id"]]
    shown = _shown(kept_id)
    assert shown
    assert {(run_id, revision_id) for _, run_id, revision_id in shown} == {
        (run.id, run.transcript_revision_id)
    }
    shown_ids = sorted(span_id for span_id, _, _ in shown)
    api = _api_actions(crew.owner, meeting_id)[kept_id]
    assert api["description"] == text
    assert api["analysis_run_id"] == run.id
    assert sorted(ref["id"] for ref in api["evidence"]) == shown_ids
    assert {ref["transcript_revision_id"] for ref in api["evidence"]} == {
        run.transcript_revision_id
    }
    finding = _report_finding(_report_body(crew.owner, meeting_id), kept_id)
    assert sorted(ref["id"] for ref in finding["evidence"]) == shown_ids
    assert all(ref["is_current_revision"] is True for ref in finding["evidence"])


def _assert_untouched(
    crew: Crew, meeting_id: str, kept_id: str, before: dict, spans: dict[str, dict]
) -> None:
    rows = _actions(meeting_id)
    assert rows.get(kept_id) == before
    assert spans
    assert _spans(kept_id) == spans
    assert sorted(span_id for span_id, _, _ in _shown(kept_id)) == sorted(spans)
    finding = _report_finding(_report_body(crew.owner, meeting_id), kept_id)
    assert sorted(ref["id"] for ref in finding["evidence"]) == sorted(spans)
    assert all(ref["is_current_revision"] is False for ref in finding["evidence"])


@pytest.mark.parametrize("path", PATHS)
def test_k4_c1_c2_a_matching_new_action_updates_the_kept_action_in_place(crew, path):
    meeting_id = _analysed(
        crew,
        [
            Say(T, owner=True, priority=ActionPriority.MEDIUM),
            Say(W, kind=ActionKind.NEXT_STEP),
        ],
    )
    kept = _ai_id(meeting_id, T)
    loose = _ai_id(meeting_id, W)
    _assign(crew, meeting_id, kept, **PERSON_SET)
    rules = _rules_follow_up(crew, meeting_id)
    before = _actions(meeting_id)
    assert before[kept]["assignee_text"] == OWNER_TEXT
    assert before[kept]["kind"] == "commitment"
    assert before[kept]["priority"] == "high"
    assert before[loose]["assignee_user_id"] is None
    old_spans = _spans(kept)
    assert old_spans
    others = {action_id: row for action_id, row in before.items() if row["created_by"] != "ai"}
    follow_up = _follow_up(meeting_id)
    assert follow_up == rules
    assert rules in others
    audit_before = _audit_ids(crew.owner.workspace_id)
    assert _ratio(T, T1) >= THRESHOLD

    run = _reanalyse(crew, path, meeting_id, [Say(T1), Say(W1, kind=ActionKind.NEXT_STEP)])

    _assert_merged(crew, meeting_id, kept, before[kept], run, T1)
    after = _actions(meeting_id)
    assert after[kept]["assignee_text"] == OWNER_TEXT
    assert after[kept]["priority"] == "high"
    assert after[kept]["status"] == before[kept]["status"]
    assert after[kept]["due_at"] == before[kept]["due_at"]
    assert _family(meeting_id, T1, "commitment") == {kept}
    assert [action_id for action_id, row in after.items() if row["description"] == T1] == [kept]
    assert loose not in after
    new_loose = _inserted(meeting_id, run, W1, "next_step")
    assert _written(meeting_id, run) == {kept, new_loose}
    spans_now = _spans(kept)
    for span_id, span in old_spans.items():
        assert spans_now.get(span_id) == span
    assert {span["analysis_run_id"] for span in spans_now.values()} == {
        next(iter(old_spans.values()))["analysis_run_id"],
        run.id,
    }
    _assert_integrity(meeting_id, run)
    for action_id, row in others.items():
        assert after.get(action_id) == row
    assert _follow_up(meeting_id) == follow_up
    new_events = _audit_rows(_audit_ids(crew.owner.workspace_id) - audit_before)
    assert not [event for event in new_events if event.event_type.startswith("action_item.")]


def test_k4_a_merged_action_stays_kept_and_merges_again_on_the_next_reanalysis(crew):
    meeting_id = _analysed(crew, [Say(T, owner=True)])
    kept = _ai_id(meeting_id, T)
    _assign(crew, meeting_id, kept, **PERSON_SET)
    before = _actions(meeting_id)[kept]
    assert _ratio(T1, T2) >= THRESHOLD

    first = _reanalyse(crew, "recompute", meeting_id, [Say(T1)])
    first_spans = {
        span_id for span_id, span in _spans(kept).items() if span["analysis_run_id"] == first.id
    }
    assert first_spans
    second = _reanalyse(crew, "process_job", meeting_id, [Say(T2)])

    _assert_merged(crew, meeting_id, kept, before, second, T2)
    assert first_spans <= set(_spans(kept))
    assert first_spans.isdisjoint(span_id for span_id, _, _ in _shown(kept))
    assert _family(meeting_id, T2, "commitment") == {kept}
    assert _written(meeting_id, second) == {kept}
    _assert_integrity(meeting_id, second)


@pytest.mark.parametrize("path", PATHS)
def test_k5_c3_another_kind_or_far_text_is_inserted_and_the_kept_action_is_untouched(crew, path):
    meeting_id = _analysed(crew, [Say(T, owner=True)])
    kept = _ai_id(meeting_id, T)
    _assign(crew, meeting_id, kept, **PERSON_SET)
    before = _actions(meeting_id)[kept]
    spans = _spans(kept)
    assert _ratio(T, T1) >= THRESHOLD
    assert _ratio(T, FAR) < THRESHOLD

    run = _reanalyse(crew, path, meeting_id, [Say(T1, kind=ActionKind.NEXT_STEP), Say(FAR)])

    _assert_untouched(crew, meeting_id, kept, before, spans)
    other_kind = _inserted(meeting_id, run, T1, "next_step")
    far = _inserted(meeting_id, run, FAR, "commitment")
    assert _written(meeting_id, run) == {other_kind, far}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize(
    ("new_text", "merges"),
    [(NEAR_ABOVE, True), (NEAR_BELOW, False)],
    ids=["ratio_just_above", "ratio_just_below"],
)
def test_k2_the_text_threshold_is_0_8(crew, new_text, merges):
    meeting_id = _analysed(crew, [Say(NEAR_BASE, owner=True)])
    kept = _ai_id(meeting_id, NEAR_BASE)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]
    spans = _spans(kept)
    ratio = _ratio(NEAR_BASE, new_text)
    assert (ratio >= THRESHOLD) is merges
    assert abs(ratio - THRESHOLD) < 0.03

    run = _reanalyse(crew, "recompute", meeting_id, [Say(new_text)])

    if merges:
        _assert_merged(crew, meeting_id, kept, before, run, new_text)
        assert _written(meeting_id, run) == {kept}
    else:
        _assert_untouched(crew, meeting_id, kept, before, spans)
        assert _written(meeting_id, run) == {_inserted(meeting_id, run, new_text, "commitment")}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize("language", ["fa", "ar"])
def test_k2_spelling_variants_match_after_normalization(crew, language):
    meeting_id = _analysed(crew, [Say(PERSIAN_SPELLING)], language=language)
    assert _current_revision(meeting_id).language == language
    kept = _ai_id(meeting_id, PERSIAN_SPELLING)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]
    raw = difflib.SequenceMatcher(None, PERSIAN_SPELLING, ARABIC_SPELLING, autojunk=False)
    assert raw.ratio() < THRESHOLD
    assert _ratio(PERSIAN_SPELLING, ARABIC_SPELLING) == 1.0

    run = _reanalyse(crew, "process_job", meeting_id, [Say(ARABIC_SPELLING)])

    _assert_merged(crew, meeting_id, kept, before, run, ARABIC_SPELLING)
    assert _written(meeting_id, run) == {kept}
    _assert_integrity(meeting_id, run)


def test_k2_text_that_normalizes_to_nothing_never_matches(crew):
    meeting_id = _analysed(crew, [Say(T, owner=True), Say(BLANK)])
    kept = _ai_id(meeting_id, T)
    blank = _ai_id(meeting_id, BLANK)
    _assign(crew, meeting_id, kept)
    _assign(crew, meeting_id, blank)
    before = _actions(meeting_id)
    blank_spans = _spans(blank)
    assert normalize_for_matching(BLANK, "fa") == ""

    run = _reanalyse(crew, "recompute", meeting_id, [Say(BLANK), Say(T1)])

    _assert_untouched(crew, meeting_id, blank, before[blank], blank_spans)
    _assert_merged(crew, meeting_id, kept, before[kept], run, T1)
    new_blank = _inserted(meeting_id, run, BLANK, "commitment")
    assert _written(meeting_id, run) == {kept, new_blank}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize("path", PATHS)
def test_k3_c4_two_kept_actions_each_take_their_best_match(crew, path):
    meeting_id = _analysed(crew, [Say(KEPT_1, owner=True), Say(KEPT_2, owner=True)])
    first = _ai_id(meeting_id, KEPT_1)
    second = _ai_id(meeting_id, KEPT_2)
    _assign(crew, meeting_id, first)
    _assign(crew, meeting_id, second)
    start = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    _set_created_at(first, start)
    _set_created_at(second, start + timedelta(seconds=1))
    before = _actions(meeting_id)
    a = _ratio(KEPT_1, NEW_1)
    b = _ratio(KEPT_1, NEW_2)
    c = _ratio(KEPT_2, NEW_1)
    d = _ratio(KEPT_2, NEW_2)
    assert min(a, b, c, d) >= THRESHOLD
    assert d > c > b > a
    assert _ratio(NEW_1, NEW_2) < 1.0

    run = _reanalyse(crew, path, meeting_id, [Say(NEW_2), Say(NEW_1)])

    _assert_merged(crew, meeting_id, first, before[first], run, NEW_1)
    _assert_merged(crew, meeting_id, second, before[second], run, NEW_2)
    assert _written(meeting_id, run) == {first, second}
    assert set(_ai_rows(meeting_id)) == {first, second}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize("path", PATHS)
def test_k3_c4_of_two_new_actions_close_to_one_kept_action_only_the_closest_merges(crew, path):
    meeting_id = _analysed(crew, [Say(T, owner=True)])
    kept = _ai_id(meeting_id, T)
    _assign(crew, meeting_id, kept, **PERSON_SET)
    before = _actions(meeting_id)[kept]
    assert THRESHOLD <= _ratio(T, T2) < _ratio(T, CLOSEST)

    run = _reanalyse(crew, path, meeting_id, [Say(T2), Say(CLOSEST)])

    _assert_merged(crew, meeting_id, kept, before, run, CLOSEST)
    other = _inserted(meeting_id, run, T2, "commitment")
    assert other != kept
    assert _written(meeting_id, run) == {kept, other}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize("order", ["created_at", "id"])
def test_k3_a_tie_goes_to_the_earlier_kept_action(crew, order):
    meeting_id = _analysed(crew, [Say(T, owner=True), Say(FAR, owner=True)])
    one = _ai_id(meeting_id, T)
    two = _ai_id(meeting_id, FAR)
    response = crew.owner.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{two}", json={"description": T}
    )
    assert response.status_code == 200, response.text
    _assign(crew, meeting_id, one)
    _assign(crew, meeting_id, two)
    low, high = sorted([one, two])
    start = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    if order == "created_at":
        _set_created_at(high, start)
        _set_created_at(low, start + timedelta(seconds=1))
        winner, loser = high, low
    else:
        _set_created_at(low, start)
        _set_created_at(high, start)
        winner, loser = low, high
    before = _actions(meeting_id)
    assert before[one]["description"] == before[two]["description"] == T
    assert before[one]["kind"] == before[two]["kind"] == "commitment"
    loser_spans = _spans(loser)

    run = _reanalyse(crew, "recompute", meeting_id, [Say(T1)])

    _assert_merged(crew, meeting_id, winner, before[winner], run, T1)
    _assert_untouched(crew, meeting_id, loser, before[loser], loser_spans)
    assert _written(meeting_id, run) == {winner}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize("first", ["a", "b"])
def test_k3_a_tie_between_new_actions_goes_to_the_one_earlier_in_the_run(crew, first):
    ordered = [TIE_A, TIE_B] if first == "a" else [TIE_B, TIE_A]
    meeting_id = _analysed(crew, [Say(T, owner=True)])
    kept = _ai_id(meeting_id, T)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]
    assert _ratio(T, TIE_A) == _ratio(T, TIE_B) >= THRESHOLD
    assert normalize_for_matching(TIE_A, "fa") != normalize_for_matching(TIE_B, "fa")

    run = _reanalyse(crew, "recompute", meeting_id, [Say(text) for text in ordered])

    _assert_merged(crew, meeting_id, kept, before, run, ordered[0])
    other = _inserted(meeting_id, run, ordered[1], "commitment")
    assert _written(meeting_id, run) == {kept, other}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize("path", PATHS)
def test_k2_c5_a_kept_action_with_no_kind_never_merges(crew, path):
    meeting_id = _analysed(crew, [Say(T, owner=True), Say(S, owner=True)])
    legacy = _ai_id(meeting_id, T)
    modern = _ai_id(meeting_id, S)
    _assign(crew, meeting_id, legacy, **PERSON_SET)
    _assign(crew, meeting_id, modern)
    with inspection_session_factory()() as db:
        db.execute(update(ActionItem).where(ActionItem.id == legacy).values(kind=None))
        db.commit()
    before = _actions(meeting_id)
    legacy_spans = _spans(legacy)
    assert before[legacy]["kind"] is None
    assert before[modern]["kind"] == "commitment"
    assert _ratio(S, S1) >= THRESHOLD
    assert _ratio(T, S1) < THRESHOLD

    run = _reanalyse(crew, path, meeting_id, [Say(T), Say(S1)])

    _assert_untouched(crew, meeting_id, legacy, before[legacy], legacy_spans)
    _assert_merged(crew, meeting_id, modern, before[modern], run, S1)
    exact = _inserted(meeting_id, run, T, "commitment")
    assert _written(meeting_id, run) == {modern, exact}
    _assert_integrity(meeting_id, run)


@pytest.mark.parametrize("path", PATHS)
def test_k1_c6_an_ordinary_meeting_still_replaces_an_assigned_ai_action(crew, path):
    meeting_id = _analysed(crew, [Say(T, owner=True)], voicemail=False)
    assigned = _ai_id(meeting_id, T)
    _assign(crew, meeting_id, assigned, **PERSON_SET)

    run = _reanalyse(crew, path, meeting_id, [Say(T1)])

    assert assigned not in _actions(meeting_id)
    new = _inserted(meeting_id, run, T1, "commitment")
    assert new != assigned
    assert set(_ai_rows(meeting_id)) == {new}
    assert _written(meeting_id, run) == {new}
    _assert_integrity(meeting_id, run)


def test_k1_k9_only_assigned_ai_actions_take_a_match(crew):
    meeting_id = _analysed(crew, [Say(T, owner=True), Say(W)])
    kept = _ai_id(meeting_id, T)
    unassigned = _ai_id(meeting_id, W)
    _assign(crew, meeting_id, kept)
    person = _direct_action(crew, meeting_id, V, created_by="user")
    rules = _direct_action(crew, meeting_id, R, created_by="voicemail_rules")
    before = _actions(meeting_id)
    assert before[unassigned]["assignee_user_id"] is None
    assert _ratio(W, W1) >= THRESHOLD
    for one, two in itertools.combinations([T1, W1, V, R], 2):
        assert _ratio(one, two) < THRESHOLD

    run = _reanalyse(crew, "recompute", meeting_id, [Say(T1), Say(W1), Say(V), Say(R)])

    after = _actions(meeting_id)
    _assert_merged(crew, meeting_id, kept, before[kept], run, T1)
    assert unassigned not in after
    assert after.get(person) == before[person]
    assert after.get(rules) == before[rules]
    inserted = {
        _inserted(meeting_id, run, W1, "commitment"),
        _inserted(meeting_id, run, V, "commitment"),
        _inserted(meeting_id, run, R, "commitment"),
    }
    assert _written(meeting_id, run) == {kept, *inserted}
    _assert_integrity(meeting_id, run)


def _recompute_events(
    crew: Crew, meeting_id: str, script: list[Say]
) -> tuple[AnalysisRun, list[AuditEvent]]:
    audit_before = _audit_ids(crew.owner.workspace_id)
    run = _reanalyse(crew, "recompute", meeting_id, script)
    return run, _audit_rows(_audit_ids(crew.owner.workspace_id) - audit_before)


def test_k8_c7_the_recompute_audit_counts_inserted_and_merged_actions(crew):
    plain = _analysed(crew, [Say(T, owner=True), Say(W)], voicemail=False)
    voice = _analysed(crew, [Say(T, owner=True), Say(W)])
    _assign(crew, plain, _ai_id(plain, T))
    kept = _ai_id(voice, T)
    _assign(crew, voice, kept)
    script = [Say(T1), Say(Y), Say(Z)]

    plain_run, plain_events = _recompute_events(crew, plain, script)
    changes_before = _assignee_changes(crew.owner.workspace_id)
    voice_run, voice_events = _recompute_events(crew, voice, script)

    written = _written(voice, voice_run)
    assert kept in written
    assert len(written) == len(script)
    completed = AuditEventType.MEETING_RECOMPUTE_COMPLETED.value
    (plain_completed,) = [event for event in plain_events if event.event_type == completed]
    (voice_completed,) = [event for event in voice_events if event.event_type == completed]
    assert voice_completed.details["action_item_count"] == len(written) == 3
    assert plain_completed.details["action_item_count"] == len(_written(plain, plain_run)) == 3
    assert set(voice_completed.details) == set(plain_completed.details)
    assert voice_completed.details["analysis_run_id"] == voice_run.id
    assert sorted(event.event_type for event in voice_events) == sorted(
        event.event_type for event in plain_events
    )
    assert {event.event_type for event in voice_events} <= {
        member.value for member in AuditEventType
    }
    assert not [event for event in voice_events if event.event_type.startswith("action_item.")]
    assert _assignee_changes(crew.owner.workspace_id) == changes_before


def test_k8_the_job_quality_counts_the_merged_action(crew):
    meeting_id = _analysed(crew, [Say(T, owner=True)])
    kept = _ai_id(meeting_id, T)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]
    assert before["evidence_status"] == "sufficient"
    audit_before = _audit_ids(crew.owner.workspace_id)

    run = _reanalyse(crew, "process_job", meeting_id, [Say(T1, turns=(0, 99)), Say(Y)])

    _assert_merged(crew, meeting_id, kept, before, run, T1, evidence_status="partial")
    other = _inserted(meeting_id, run, Y, "commitment")
    assert _actions(meeting_id)[other]["evidence_status"] == "sufficient"
    assert _written(meeting_id, run) == {kept, other}
    new_events = _audit_rows(_audit_ids(crew.owner.workspace_id) - audit_before)
    (completed,) = [
        event
        for event in new_events
        if event.event_type == AuditEventType.MEETING_PROCESSING_COMPLETED.value
        and event.target_id == meeting_id
    ]
    assert completed.details["analysis_run_id"] == run.id
    assert completed.details["evidence_state"] == "partial"
    assert not [event for event in new_events if event.event_type.startswith("action_item.")]


def test_k7_c8_coverage_and_the_call_report_agree_on_a_merged_action(crew, settings):
    meeting_id = _analysed(crew, [Say(T, owner=True), Say(W)])
    kept = _ai_id(meeting_id, T)
    _assign(crew, meeting_id, kept)
    before = _actions(meeting_id)[kept]

    run = _reanalyse(crew, "recompute", meeting_id, [Say(T1), Say(Y)])

    _assert_merged(crew, meeting_id, kept, before, run, T1)
    body = _report_body(crew.owner, meeting_id)
    with inspection_session_factory()() as db:
        actions = db.scalars(
            select(ActionItem).where(
                ActionItem.meeting_id == meeting_id, ActionItem.created_by == "ai"
            )
        ).all()
        spans = db.scalars(select(EvidenceSpan).where(EvidenceSpan.meeting_id == meeting_id)).all()
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        assert (
            unsupported_finding_count(summary=summary, actions=actions, spans=spans)
            == body["qa"]["counts"]["unsupported"]
        )
        now = datetime.now(UTC)
        coverage = compute_coverage(
            db,
            workspace_id=crew.owner.workspace_id,
            window=parse_window(
                (now - timedelta(days=2)).isoformat(), (now + timedelta(days=1)).isoformat()
            ),
            recompute_max_attempts=settings.recompute_max_attempts,
        )
    buckets = {meeting.meeting_id: meeting.bucket for meeting in coverage.evaluated}
    assert meeting_id in buckets
    completeness = body["completeness"]
    assert (buckets[meeting_id] == "fully_evaluated") is completeness["is_complete"]
    if not completeness["is_complete"]:
        assert buckets[meeting_id] == completeness["notices"][0]


def _seed_own_run_cases(tenant: Tenant, meeting_id: str) -> dict[str, tuple[str, str]]:
    with inspection_session_factory()() as db:
        current = db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(True)
            )
        )
        older = db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(False)
            )
        )
        revision = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id,
                TranscriptRevision.is_current.is_(True),
            )
        )
        segment = db.scalar(
            select(TranscriptSegment)
            .where(TranscriptSegment.revision_id == revision.id)
            .order_by(TranscriptSegment.sequence.asc())
        )
        assert current is not None and older is not None and segment is not None
        cases = {
            "own_run": (current.id, current.id),
            "older_run": (current.id, older.id),
            "span_without_run": (current.id, None),
            "action_without_run": (None, older.id),
        }
        seeded: dict[str, tuple[str, str]] = {}
        for name, (action_run, span_run) in cases.items():
            action = ActionItem(
                workspace_id=tenant.workspace_id,
                meeting_id=meeting_id,
                description=f"Follow up ({name}).",
                created_by="ai",
                kind="commitment",
                analysis_run_id=action_run,
                evidence_status="sufficient",
            )
            db.add(action)
            db.flush()
            span = EvidenceSpan(
                workspace_id=tenant.workspace_id,
                meeting_id=meeting_id,
                analysis_run_id=span_run,
                transcript_revision_id=revision.id,
                segment_id=segment.id,
                speaker_key=segment.speaker_key,
                finding_type="action_item",
                finding_id=action.id,
                text_start=0,
                text_end=4,
                start_ms=segment.start_ms,
                end_ms=segment.end_ms,
                evidence_type="turn_reference" if span_run else "human",
            )
            db.add(span)
            db.flush()
            seeded[name] = (action.id, span.id)
        db.commit()
        return seeded


def test_k6_k7_c8_an_action_shows_only_the_spans_of_its_own_run(client, email_outbox, settings):
    tenant = Tenant(client, email_outbox, "hom381-own-run@example.com")
    meeting_id = seed_call(tenant)
    seeded = _seed_own_run_cases(tenant, meeting_id)

    body = report(tenant, meeting_id)
    response = tenant.get(f"/api/v1/meetings/{meeting_id}/actions")
    assert response.status_code == 200, response.text
    api = {action["id"]: action for action in response.json()}
    for name, (action_id, span_id) in seeded.items():
        expected = [] if name == "older_run" else [span_id]
        assert [span for span, _, _ in _shown(action_id)] == expected, name
        assert [ref["id"] for ref in api[action_id]["evidence"]] == expected, name
        finding = _report_finding(body, action_id)
        assert [ref["id"] for ref in finding["evidence"]] == expected, name
        assert finding["supported"] is (name != "older_run"), name
    assert body["qa"]["counts"]["unsupported"] == 1
    assert unsupported_from_rows(tenant, meeting_id) == 1
    completeness = body["completeness"]
    assert completeness["is_complete"] is False
    assert evaluation_buckets(tenant, settings)[meeting_id] == completeness["notices"][0]
