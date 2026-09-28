from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from backend.database.saas_models import ActionItem, AuditEvent, Notification
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_uploads_api import _authenticated_workspace
from backend.tests.test_voicemail_api import (  # noqa: F401
    add_transcript,
    import_vm,
    run_pipeline,
    vm_client,
)
from backend.voicemail.classify import (
    ClassificationInput,
    ClassificationResult,
)
from backend.voicemail.models import VoicemailCategory, VoicemailMessage
from backend.voicemail.pipeline import (
    ACTION_CREATED_BY,
    on_processing_failed,
    on_transcript_ready,
)


def _voicemail(voicemail_id: str) -> VoicemailMessage:
    with inspection_session_factory()() as db:
        row = db.get(VoicemailMessage, voicemail_id)
        db.expunge(row)
        return row


def _counts(meeting_id: str) -> tuple[int, int]:
    with inspection_session_factory()() as db:
        actions = db.scalar(
            select(func.count(ActionItem.id)).where(
                ActionItem.meeting_id == meeting_id, ActionItem.created_by == ACTION_CREATED_BY
            )
        )
        notes = db.scalar(
            select(func.count(Notification.id)).where(Notification.kind == "voicemail.urgent")
        )
        return actions, notes


def test_pipeline_is_idempotent_and_creates_one_action_and_notification(
    vm_client,  # noqa: F811
    email_outbox,
) -> None:  # noqa: F811
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    vm = import_vm(vm_client, workspace_id).json()["voicemail"]

    # No transcript yet: nothing happens.
    assert run_pipeline(vm["meeting_id"]) is None

    add_transcript(
        vm["meeting_id"],
        [
            "Urgent: I am furious, this is unacceptable and I want to complain",
            "cancel my contract, please call me back",
        ],
    )
    first = run_pipeline(vm["meeting_id"])
    assert first.changed and first.status == "classified"
    assert first.action_item_id and first.notification_id
    row = _voicemail(vm["id"])
    assert row.category == VoicemailCategory.COMPLAINT.value
    assert row.priority == "urgent" and row.follow_up_state == "action_created"
    assert row.classifier_kind == "rules" and row.classifier_model_id is None
    assert _counts(vm["meeting_id"]) == (1, 1)

    second = run_pipeline(vm["meeting_id"])
    assert second.changed is False and second.action_item_id == first.action_item_id
    assert _counts(vm["meeting_id"]) == (1, 1)

    # A corrected transcript re-classifies but never duplicates the action or alert.
    add_transcript(vm["meeting_id"], ["Urgent, the invoice charged me twice"], revision_number=2)
    third = run_pipeline(vm["meeting_id"])
    assert third.changed and third.action_item_id == first.action_item_id
    assert third.notification_id is None
    assert _counts(vm["meeting_id"]) == (1, 1)
    assert _voicemail(vm["id"]).category == VoicemailCategory.BILLING.value

    with inspection_session_factory()() as db:
        assert (
            db.scalar(
                select(func.count(AuditEvent.id)).where(
                    AuditEvent.event_type == "voicemail.classified"
                )
            )
            == 2
        )


def test_empty_transcript_needs_review_without_action(vm_client, email_outbox) -> None:  # noqa: F811
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    vm = import_vm(vm_client, workspace_id).json()["voicemail"]
    add_transcript(vm["meeting_id"], ["", "  "])
    outcome = run_pipeline(vm["meeting_id"])
    assert outcome.status == "needs_review" and outcome.action_item_id is None
    row = _voicemail(vm["id"])
    assert row.category == "empty" and row.intent is None and row.topics == []
    assert row.review_reason == "no_speech" and row.priority == "low"
    assert row.follow_up_state == "none"
    assert _counts(vm["meeting_id"]) == (0, 0)


def test_repeat_caller_raises_priority(vm_client, email_outbox) -> None:  # noqa: F811
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    _widen_concurrency(workspace_id)
    now = datetime.now(UTC)
    first = import_vm(
        vm_client,
        workspace_id,
        external_id="r-1",
        audio=b"one",
        received_at=now - timedelta(hours=5),
    ).json()["voicemail"]
    second = import_vm(
        vm_client, workspace_id, external_id="r-2", audio=b"two", received_at=now
    ).json()["voicemail"]
    for vm in (first, second):
        add_transcript(vm["meeting_id"], ["Please call me back"])
        run_pipeline(vm["meeting_id"])
    a, b = _voicemail(first["id"]), _voicemail(second["id"])
    assert "repeat_caller" not in [r["code"] for r in a.priority_reasons]
    assert "repeat_caller" in [r["code"] for r in b.priority_reasons]
    assert b.priority_score == a.priority_score + 15


def test_pluggable_classifier_records_provenance_and_vip_hook(vm_client, email_outbox) -> None:  # noqa: F811
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    vm = import_vm(vm_client, workspace_id).json()["voicemail"]
    add_transcript(vm["meeting_id"], ["I would like to book an appointment"])

    class FakeLlm:
        kind = "llm"
        version = "llm-adapter-1"
        model_id = "test-model@2026-09"

        def classify(self, item: ClassificationInput) -> ClassificationResult:
            return ClassificationResult(
                category=VoicemailCategory.APPOINTMENT,
                intent="schedule_appointment",
                topics=("scheduling",),
                confidence=0.9,
                needs_review=False,
            )

    with inspection_session_factory()() as db:
        outcome = on_transcript_ready(
            db, vm["meeting_id"], classifier=FakeLlm(), vip_lookup=lambda *_: True
        )
        db.commit()
    assert outcome.changed
    row = _voicemail(vm["id"])
    assert (row.classifier_kind, row.classifier_version, row.classifier_model_id) == (
        "llm",
        "llm-adapter-1",
        "test-model@2026-09",
    )
    assert "vip_caller" in [r["code"] for r in row.priority_reasons]


def test_non_voicemail_meeting_and_failure(vm_client, email_outbox) -> None:  # noqa: F811
    _, workspace_id = _authenticated_workspace(vm_client, email_outbox)
    vm = import_vm(vm_client, workspace_id).json()["voicemail"]
    with inspection_session_factory()() as db:
        assert on_transcript_ready(db, "00000000-0000-0000-0000-000000000000") is None
        assert on_processing_failed(db, vm["meeting_id"], reason="asr_failed").status == "failed"
        assert on_processing_failed(db, vm["meeting_id"]) is None
        db.commit()
    assert _voicemail(vm["id"]).review_reason == "asr_failed"
