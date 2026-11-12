"""HOM-75 PR 2: answers and closes racing on one batch (PostgreSQL only, ADR 0056 D3).

An answer write locks the batch row ``FOR SHARE`` before it checks the status.  A
close locks the row ``FOR UPDATE`` before it reads the revision, the status and the
answers, then writes its compare-and-set ``UPDATE``.  So:

* a close waits for an answer that holds the share lock, then reads that answer;
* an answer that starts after a close has taken the row waits, then sees ``closed``
  and writes nothing;
* two closes with the same revision: exactly one wins, the other gets 409.

The answers present at close are exactly the answers the report reads.  Each test
holds one side open in its own session (a real service call, not yet committed) and
sends the other side through the API in a thread.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.database.relational import get_session_factory
from backend.database.saas_models import CalibrationAnswer, CalibrationBatch, WorkspaceRole
from backend.database.tenant_context import bind_tenant_context
from backend.services.calibration_service import answer_criterion, close_batch
from backend.services.review_queue_service import Reviewer, reviewer
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_calibration_api import (
    BATCHES,
    GOLDS,
    _scored,
    adjudicate_all,
    answer,
    calibration_events,
    close,
    created_batch,
    example_of,
)
from backend.tests.test_review_queue_access import Queue, build_queue, code

pytestmark = pytest.mark.postgres

WAIT_SECONDS = 1.0


@pytest.fixture
def queue(client, email_outbox, monkeypatch) -> Queue:
    return build_queue(client, email_outbox, monkeypatch)


def _requires_postgres() -> None:
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")


def _together(*calls) -> list:
    from backend.tests.test_review_queue_concurrency import _together as together

    return together(*calls)


def _session(queue: Queue) -> Session:
    db = get_session_factory()()
    bind_tenant_context(db, workspace_id=queue.workspace_id)
    return db


def _caller(db: Session, queue: Queue, persona, role: WorkspaceRole) -> Reviewer:
    return reviewer(
        db,
        workspace_id=queue.workspace_id,
        user_id=persona.user_id,
        role=role,
        now=datetime.now(UTC),
    )


def _adjudicated_open_batch(queue: Queue) -> dict:
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    adjudicate_all(queue.admin, batch, GOLDS)
    return batch


def _answer_count(batch_id: str) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(func.count())
            .select_from(CalibrationAnswer)
            .where(CalibrationAnswer.batch_id == batch_id)
        )


def _in_thread(call) -> tuple[threading.Thread, list]:
    results: list = []
    thread = threading.Thread(target=lambda: results.append(call()))
    thread.start()
    return thread, results


def test_a_close_waits_for_an_uncommitted_answer_and_then_counts_it(queue):
    _requires_postgres()
    batch = _adjudicated_open_batch(queue)
    example = example_of(batch)
    with _session(queue) as db:
        # The answer is written and its transaction still holds the FOR SHARE lock.
        answer_criterion(
            db,
            _caller(db, queue, queue.sup, WorkspaceRole.MEMBER),
            batch_id=batch["id"],
            example_id=example["id"],
            criterion_id="greeting",
            outcome="passed",
            now=datetime.now(UTC),
        )
        closer, responses = _in_thread(lambda: close(queue.admin, batch))
        time.sleep(WAIT_SECONDS)
        assert closer.is_alive(), responses  # the close waits on the held row
        db.commit()
    closer.join(timeout=30)
    assert not closer.is_alive(), "the close hung"
    assert len(responses) == 1 and responses[0].status_code == 200, responses
    # The close read the answer that committed while it waited: its audit event and
    # the report both count it (greeting is adjudicated failed, answered passed).
    (closed,) = [
        event
        for event in calibration_events(queue.workspace_id)
        if event.event_type == "calibration_batch.closed"
    ]
    assert (closed.details["compared"], closed.details["matched"]) == (1, 0)
    report = queue.admin.get(f"{BATCHES}/{batch['id']}/report")
    assert report.status_code == 200, report.text
    assert report.json()["overall"]["compared"] == 1
    assert _answer_count(batch["id"]) == 1


def test_an_answer_that_starts_after_a_close_took_the_row_is_refused(queue):
    _requires_postgres()
    batch = _adjudicated_open_batch(queue)
    example = example_of(batch)
    with _session(queue) as db:
        # The close has locked and updated the row but has not committed yet.
        close_batch(
            db,
            _caller(db, queue, queue.admin, WorkspaceRole.ADMIN),
            batch_id=batch["id"],
            expected_revision=1,
            now=datetime.now(UTC),
        )
        answerer, responses = _in_thread(
            lambda: answer(queue.sup, batch, example["id"], "greeting", "passed")
        )
        time.sleep(WAIT_SECONDS)
        assert answerer.is_alive(), responses  # the FOR SHARE lock waits on the close
        db.commit()
    answerer.join(timeout=30)
    assert not answerer.is_alive(), "the answer hung"
    assert len(responses) == 1, responses
    refused = responses[0]
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"
    assert _answer_count(batch["id"]) == 0
    events = [event.event_type for event in calibration_events(queue.workspace_id)]
    assert "calibration_answer.recorded" not in events


def test_an_answer_after_a_committed_close_writes_nothing(queue):
    _requires_postgres()
    batch = _adjudicated_open_batch(queue)
    example = example_of(batch)
    assert close(queue.admin, batch).status_code == 200
    refused = answer(queue.sup, batch, example["id"], "greeting", "passed")
    assert refused.status_code == 409 and code(refused) == "calibration_batch_closed"
    assert _answer_count(batch["id"]) == 0
    with inspection_session_factory()() as db:
        revision = db.scalar(
            select(CalibrationBatch.revision).where(CalibrationBatch.id == batch["id"])
        )
    assert revision == 2  # an answer never changes the revision


def test_two_closes_with_the_same_revision_exactly_one_wins(queue):
    _requires_postgres()
    batch = _adjudicated_open_batch(queue)
    responses = _together(
        lambda: close(queue.admin, batch),
        lambda: close(queue.owner, batch),
    )
    assert sorted(response.status_code for response in responses) == [200, 409], responses
    (winner,) = [response for response in responses if response.status_code == 200]
    (loser,) = [response for response in responses if response.status_code == 409]
    assert code(loser) == "calibration_batch_revision_conflict"
    assert loser.json()["error"]["details"] == {"current_revision": 2}
    assert winner.json()["revision"] == 2
    with inspection_session_factory()() as db:
        row = db.get(CalibrationBatch, batch["id"])
        assert row.status == "closed" and row.revision == 2
        assert row.closed_by_user_id in {queue.admin.user_id, queue.owner.user_id}
    closes = [
        event
        for event in calibration_events(queue.workspace_id)
        if event.event_type == "calibration_batch.closed"
    ]
    assert len(closes) == 1 and closes[0].details["revision"] == 2


def test_two_identical_first_answers_at_once_store_one_row_and_one_event(queue):
    """A double click: both requests find no answer; the later insert meets the
    unique key, reads the row the other stored, and changes nothing."""
    _requires_postgres()
    scored = _scored(queue)
    batch = created_batch(queue.owner, [scored.result_id], [queue.sup.user_id])
    example = example_of(batch)
    responses = _together(
        lambda: answer(queue.sup, batch, example["id"], "greeting", "passed"),
        lambda: answer(queue.sup, batch, example["id"], "greeting", "passed"),
    )
    assert [response.status_code for response in responses] == [200, 200], responses
    assert _answer_count(batch["id"]) == 1
    recorded = [
        event
        for event in calibration_events(queue.workspace_id)
        if event.event_type == "calibration_answer.recorded"
    ]
    assert len(recorded) == 1
