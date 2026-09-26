from __future__ import annotations

from sqlalchemy import func, select

from backend.database.saas_models import (
    Entitlement,
    Meeting,
    ReservationState,
    UsageEvent,
    UsageReservation,
    User,
    Workspace,
)
from backend.services.entitlement_service import (
    QuotaExceededError,
    commit_usage,
    get_usage_snapshot,
    release_usage,
    reserve_usage,
)
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import register_user


def _meeting(db, *, workspace_id: str, user_id: str, title: str) -> Meeting:
    meeting = Meeting(
        workspace_id=workspace_id,
        created_by_user_id=user_id,
        title=title,
        language="fa",
        duration_seconds=1200,
    )
    db.add(meeting)
    db.flush()
    return meeting


def test_audio_quota_reservation_commit_release_and_idempotency(client, email_outbox):
    credentials = register_user(client, email_outbox)
    factory = inspection_session_factory()
    with factory() as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        workspace = db.scalar(select(Workspace))
        assert user is not None and workspace is not None
        first_meeting = _meeting(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            title="First",
        )
        second_meeting = _meeting(
            db,
            workspace_id=workspace.id,
            user_id=user.id,
            title="Second",
        )
        db.commit()

        first = reserve_usage(
            db,
            workspace_id=workspace.id,
            meeting_id=first_meeting.id,
            amount=1200,
            idempotency_key="audio:first",
        )
        db.commit()
        assert get_usage_snapshot(db, workspace.id).reserved == 1200

        repeated = reserve_usage(
            db,
            workspace_id=workspace.id,
            meeting_id=first_meeting.id,
            amount=1200,
            idempotency_key="audio:first",
        )
        db.commit()
        assert repeated.id == first.id
        assert get_usage_snapshot(db, workspace.id).reserved == 1200

        try:
            reserve_usage(
                db,
                workspace_id=workspace.id,
                meeting_id=second_meeting.id,
                amount=1200,
                idempotency_key="audio:second",
            )
        except QuotaExceededError as exc:
            assert exc.remaining == 600
        else:
            raise AssertionError("A concurrent over-quota reservation was accepted")
        db.rollback()

        committed = commit_usage(db, first.id, actual_amount=900)
        db.commit()
        assert committed.state == ReservationState.COMMITTED
        snapshot = get_usage_snapshot(db, workspace.id)
        assert (snapshot.used, snapshot.reserved, snapshot.remaining) == (900, 0, 900)

        commit_usage(db, first.id, actual_amount=900)
        db.commit()
        # WP06-T06 ledger: one "reserved" and one "committed" row, never two
        # settlements, however often commit_usage is called.
        events = db.execute(
            select(UsageEvent.event_type, UsageEvent.operation_key)
            .where(UsageEvent.reservation_id == first.id)
            .order_by(UsageEvent.occurred_at, UsageEvent.id)
        ).all()
        assert [row[0] for row in events] == ["reserved", "committed"]
        assert [row[1] for row in events] == [f"{first.id}:reserved", f"{first.id}:committed"]

        second = reserve_usage(
            db,
            workspace_id=workspace.id,
            meeting_id=second_meeting.id,
            amount=800,
            idempotency_key="audio:second",
        )
        db.commit()
        release_usage(db, second.id, reason="provider_unavailable")
        db.commit()
        assert get_usage_snapshot(db, workspace.id).reserved == 0


def test_concurrent_job_entitlement_is_atomic(client, email_outbox):
    credentials = register_user(client, email_outbox)
    factory = inspection_session_factory()
    with factory() as db:
        user = db.scalar(select(User).where(User.email == credentials["email"]))
        workspace = db.scalar(select(Workspace))
        assert user is not None and workspace is not None
        first_meeting = _meeting(db, workspace_id=workspace.id, user_id=user.id, title="One")
        second_meeting = _meeting(db, workspace_id=workspace.id, user_id=user.id, title="Two")
        db.commit()

        first = reserve_usage(
            db,
            workspace_id=workspace.id,
            meeting_id=first_meeting.id,
            amount=1,
            idempotency_key="concurrency:first",
            metric="concurrent_jobs",
        )
        db.commit()
        assert get_usage_snapshot(db, workspace.id, "concurrent_jobs").reserved == 1

        try:
            reserve_usage(
                db,
                workspace_id=workspace.id,
                meeting_id=second_meeting.id,
                amount=1,
                idempotency_key="concurrency:second",
                metric="concurrent_jobs",
            )
        except QuotaExceededError as exc:
            assert exc.remaining == 0
        else:
            raise AssertionError("A second concurrent job reservation was accepted")
        db.rollback()

        release_usage(db, first.id, reason="job_finished")
        db.commit()
        entitlement = db.scalar(
            select(Entitlement).where(
                Entitlement.workspace_id == workspace.id,
                Entitlement.metric == "concurrent_jobs",
            )
        )
        assert entitlement is not None and entitlement.reserved_value == 0
        reservation_count = db.scalar(select(func.count(UsageReservation.id)))
        assert reservation_count == 1
