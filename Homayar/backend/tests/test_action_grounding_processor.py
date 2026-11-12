"""HOM-133 (ADR 0058): the analysis pipeline grounds AI actions before it stores them.

Through the real processor and the actions route: an action with no cited turn is
not stored and has no evidence row, but its references are still counted as rejected
(Amendment 2); an owner or a due date that no cited turn states stays unset; a grounded
owner and due date are kept; every stored AI action has a kind, its analysis run and
evidence on the analysed revision; a person's action has no kind; a voicemail
re-analysis keeps a person-assigned AI action unchanged (ADR 0054); the mock stores no
action.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    AuditEvent,
    EvidenceSpan,
    Meeting,
)
from backend.jobs import recompute
from backend.jobs.processor import _grounding_reference
from backend.providers.base import ASRProvider, IntelligenceProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.providers.schemas import (
    ActionKind,
    ActionPriority,
    ASRRequest,
    ASRResult,
    ASRSegment,
    IntelligenceRequest,
    IntelligenceResult,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_analysis_recompute import (
    CORRECTED_TEXT,
    ORIGINAL_TEXT,
    _correct,
    _current_run,
    _processed_meeting,
    _snapshot,
)
from backend.tests.test_analysis_recompute import (
    _bundle as _recompute_bundle,
)
from backend.tests.test_evidence_spans import _get, _processed
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider, _providers
from backend.tests.test_voicemail_reanalysis_keeps_assignment import (
    _ai_actions,
    _assignee_changes,
    _link_voicemail,
    _member,
    _owner_assigns,
    _two_ai_actions,
    _TwoActionIntelligence,
)

TURNS = (
    "سارا، لطفاً گزارش فروش را تا 2026-11-20 بفرست.",
    "باشه، با مشتری هم تماس می‌گیریم.",
)
GROUNDED = "ارسال گزارش فروش"
INVENTED = "تماس با مشتری"
DROPPED_OUT_OF_RANGE = "بدون مرجع معتبر"
DROPPED_EMPTY = "بدون مرجع"


@pytest.fixture
def settings(settings):
    # The recompute builds its providers from the settings, like the worker.
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
        }
    )


@pytest.fixture
def analysis_provider(monkeypatch):
    """The intelligence adapter the recompute uses: two AI actions on the first turn."""
    intelligence = _TwoActionIntelligence(allow_mock=True)
    monkeypatch.setattr(
        recompute, "create_provider_bundle", lambda _settings: _recompute_bundle(intelligence)
    )
    return intelligence


class _TwoTurnASR(ASRProvider):
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="grounding_asr", model="v1")

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return ASRResult(
            provider="grounding_asr",
            model="v1",
            language="fa",
            text=" ".join(TURNS),
            segments=[
                ASRSegment(index=0, text=TURNS[0], start_seconds=0.5, end_seconds=4.0),
                ASRSegment(index=1, text=TURNS[1], start_seconds=4.0, end_seconds=9.5),
            ],
            duration_seconds=10,
            is_mock=True,
        )


class _GroundingIntelligence(IntelligenceProvider):
    """One grounded action, one with an invented owner and date, two with no turn."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="grounding_intelligence", model="ground-v1")

    async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
        return IntelligenceResult(
            provider="grounding_intelligence",
            model="ground-v1",
            summary="گزارش فروش و تماس با مشتری",
            summary_source_turn_indexes=[0],
            action_items=[
                ProviderActionItem(
                    description=GROUNDED,
                    kind=ActionKind.COMMITMENT,
                    owner="سارا",
                    owner_quote="سارا، لطفاً گزارش فروش را",
                    due_date=date(2026, 11, 20),
                    due_quote="تا 2026-11-20",
                    priority=ActionPriority.HIGH,
                    source_turn_indexes=[0],
                ),
                ProviderActionItem(
                    description=INVENTED,
                    kind=ActionKind.NEXT_STEP,
                    owner="مریم",  # nobody says it
                    owner_quote="مریم تماس می‌گیرد",  # not in any turn
                    due_date=date(2026, 11, 25),  # nobody says it
                    source_turn_indexes=[1, 5],  # 5 is out of range
                ),
                ProviderActionItem(
                    description=DROPPED_OUT_OF_RANGE,
                    kind=ActionKind.COMMITMENT,
                    owner="سارا",
                    owner_quote="سارا، لطفاً",
                    source_turn_indexes=[7, 9],
                ),
                ProviderActionItem(description=DROPPED_EMPTY, source_turn_indexes=[]),
            ],
            is_mock=True,
        )


def _bundle() -> ProviderBundle:
    return ProviderBundle(
        asr=_TwoTurnASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=_GroundingIntelligence(),
    )


def _grounded_meeting(client, email_outbox, settings, monkeypatch):
    return _processed(client, email_outbox, settings, monkeypatch, providers=_bundle())


def _rows(meeting_id: str) -> list[ActionItem]:
    with inspection_session_factory()() as db:
        return list(db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)))


def _spans(meeting_id: str) -> list[EvidenceSpan]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(EvidenceSpan).where(
                    EvidenceSpan.meeting_id == meeting_id,
                    EvidenceSpan.finding_type == "action_item",
                )
            )
        )


# --- AC1: no cited turn, no row, no span; references still counted -----------------


def test_ac1_an_action_with_no_cited_turn_is_not_stored_and_has_no_evidence(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _grounded_meeting(client, email_outbox, settings, monkeypatch)
    rows = _rows(meeting_id)
    assert sorted(row.description for row in rows) == sorted([GROUNDED, INVENTED])
    stored_ids = {row.id for row in rows}
    spans = _spans(meeting_id)
    assert spans and {span.finding_id for span in spans} <= stored_ids
    # Control: the item with one index in range (1 of [1, 5]) is stored.
    assert INVENTED in {row.description for row in rows}


def test_amendment_2_a_dropped_actions_references_still_count_as_rejected(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _grounded_meeting(client, email_outbox, settings, monkeypatch)
    summary = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/summary")
    run = summary["analysis_run"]
    # offered: summary 1 + grounded 1 + invented 2 + dropped 2 (+ dropped 0) = 6;
    # rejected: invented's 5 and the dropped item's 7 and 9 = 3.
    assert (run["reference_count"], run["rejected_reference_count"]) == (6, 3)
    with inspection_session_factory()() as db:
        completed = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "meeting.processing.completed",
                AuditEvent.workspace_id == workspace_id,
            )
        )
        assert completed is not None
        # spans: summary 1 + grounded 1 + invented 1 = 3; rejected 3.
        assert (completed.details["evidence_count"], completed.details["rejected_references"]) == (
            3,
            3,
        )


# --- AC2: owner and due date only when a cited turn states them ---------------------


def test_ac2_owner_and_due_date_are_kept_only_when_grounded(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _grounded_meeting(client, email_outbox, settings, monkeypatch)
    actions = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions")
    by_description = {action["description"]: action for action in actions}

    grounded = by_description[GROUNDED]
    assert grounded["assignee_text"] == "سارا"
    assert grounded["due_at"].startswith("2026-11-20T00:00:00")
    assert grounded["priority"] == "high"

    invented = by_description[INVENTED]
    assert invented["assignee_text"] is None
    assert invented["due_at"] is None
    # The out-of-range reference stays visible in the evidence status.
    assert invented["evidence_status"] == "partial"

    for action in actions:
        assert action["assignee_user_id"] is None  # the pipeline never writes it


# --- AC3 and AC4: kind, run and evidence; the API field ------------------------------


def test_ac3_every_stored_ai_action_has_a_kind_a_run_and_evidence_on_the_revision(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _grounded_meeting(client, email_outbox, settings, monkeypatch)
    rows = _rows(meeting_id)
    assert rows
    spans = _spans(meeting_id)
    with inspection_session_factory()() as db:
        for row in rows:
            assert row.created_by == "ai"
            assert row.kind in {"commitment", "next_step"}
            assert row.analysis_run_id is not None
            run = db.get(AnalysisRun, row.analysis_run_id)
            mine = [span for span in spans if span.finding_id == row.id]
            assert mine, row.description
            assert all(span.transcript_revision_id == run.transcript_revision_id for span in mine)
    kinds = {row.description: row.kind for row in rows}
    assert kinds == {GROUNDED: "commitment", INVENTED: "next_step"}


def test_ac4_the_actions_route_returns_kind_and_null_for_a_persons_action(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _grounded_meeting(client, email_outbox, settings, monkeypatch)
    created = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={"description": "کار دستی"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["kind"] is None

    actions = _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions")
    kinds = {action["description"]: action["kind"] for action in actions}
    assert kinds == {GROUNDED: "commitment", INVENTED: "next_step", "کار دستی": None}

    # A person cannot set the kind: create and update refuse the field.
    refused = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={"description": "x", "kind": "commitment"},
    )
    assert refused.status_code == 422, refused.text
    patched = client.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{created.json()['id']}",
        headers=_write_headers(client, workspace_id),
        json={"kind": "commitment"},
    )
    assert patched.status_code == 422, patched.text


# --- AC5: ADR 0054 on a voicemail re-analysis ---------------------------------------


def test_ac5_a_voicemail_reanalysis_keeps_a_person_assigned_ai_action_unchanged(
    client, email_outbox, settings, monkeypatch, analysis_provider
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client,
        email_outbox,
        settings,
        monkeypatch,
        key="hom133-voicemail",
        intelligence=_TwoActionIntelligence(allow_mock=True),
    )
    _link_voicemail(workspace_id, meeting_id)
    _, member_id = _member(client, email_outbox, workspace_id)
    assigned_id, unassigned_id = _two_ai_actions(meeting_id, ORIGINAL_TEXT)
    _owner_assigns(client, credentials, workspace_id, meeting_id, assigned_id, member_id)
    before = _snapshot(ActionItem, meeting_id)
    assert before[assigned_id]["kind"] == "next_step"  # an unlabelled provider item
    assignee_rows = _assignee_changes(workspace_id)

    from backend.tests.conftest import login_user

    login_user(client, credentials)
    _correct(client, workspace_id, meeting_id)
    run = _current_run(meeting_id)
    after = _snapshot(ActionItem, meeting_id)

    # A2: the assigned AI action is unchanged, every column (its kind included).
    assert after[assigned_id] == before[assigned_id]
    # A3: the unassigned one is replaced; the new rows carry a kind and no assignee.
    assert unassigned_id not in after
    new_rows = {
        row_id: row
        for row_id, row in _ai_actions(meeting_id).items()
        if row_id != assigned_id and row["analysis_run_id"] == run.id
    }
    assert len(new_rows) == 2
    assert all(row["kind"] == "next_step" for row in new_rows.values())
    assert all(row["assignee_user_id"] is None for row in new_rows.values())
    assert all(CORRECTED_TEXT in row["description"] for row in new_rows.values())
    assert _assignee_changes(workspace_id) == assignee_rows


# --- AC8: the mock stores no action -------------------------------------------------


def test_ac8_a_mock_run_still_stores_zero_actions(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed(
        client, email_outbox, settings, monkeypatch, providers=_providers()
    )
    assert _rows(meeting_id) == []
    assert _get(client, workspace_id, f"/api/v1/meetings/{meeting_id}/actions") == []


# --- the date anchor ----------------------------------------------------------------


def test_the_reference_is_the_call_start_else_the_upload_and_naive_reads_as_utc():
    started = datetime(2026, 11, 12, 8, 0, tzinfo=timezone(timedelta(hours=3, minutes=30)))
    created = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
    assert _grounding_reference(Meeting(started_at=started, created_at=created)) == started
    assert _grounding_reference(Meeting(started_at=None, created_at=created)) == created
    naive = _grounding_reference(
        Meeting(started_at=datetime(2026, 11, 12, 8, 0), created_at=created)
    )
    assert naive == datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
