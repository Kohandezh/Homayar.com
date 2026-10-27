"""HOM-312: the trend judgment warm-up (ADR 0043).

On the HOM-84 reference dataset, with processing jobs that mark which workspace was active
last: the warm-up fills the app's cache in the background, most recently active workspace
first, never past the cap and never evicting; a request while it runs is answered correctly;
it yields while a request uses the cache, and a request never waits on a run the paused
warm-up claimed; it stops cleanly (also on shutdown); it fails without taking the app down;
and the setting turns it off.  No time is asserted: every wait is bounded and only the result
is checked.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.database.saas_models import (
    Entitlement,
    ProcessingJob,
    UsageReservation,
)
from backend.issue_trends.counting import SignatureJudge
from backend.main import create_app
from backend.services import trend_judgment_warmup
from backend.services.issue_trend_service import trend_definition
from backend.services.trend_judgment_cache import JUDGMENTS, JudgmentCache, judgment_key
from backend.services.trend_judgment_warmup import JudgmentWarmup
from backend.tests._database import inspection_session_factory
from backend.tests._trend_reference import EXTRACTIONS, Reference, build_reference

WAIT = 60.0


def _activity(ref: Reference, workspace_id: str, minutes_ago: int) -> None:
    """One processing job in ``workspace_id`` (its latest activity, ``minutes_ago``)."""
    meeting_id = sorted(ref.meetings(workspace_id=workspace_id))[0]
    with inspection_session_factory()() as db:
        entitlement_id = db.scalar(
            select(Entitlement.id).where(Entitlement.workspace_id == workspace_id).limit(1)
        )
        if entitlement_id is None:
            entitlement = Entitlement(
                workspace_id=workspace_id,
                metric="audio_seconds",
                source="test",
                limit_value=None,
                used_value=0,
                reserved_value=0,
                period_start=datetime.now(UTC) - timedelta(days=60),
            )
            db.add(entitlement)
            db.flush()
            entitlement_id = entitlement.id
        reservation = UsageReservation(
            workspace_id=workspace_id,
            entitlement_id=entitlement_id,
            meeting_id=meeting_id,
            idempotency_key=f"warmup:{uuid.uuid4()}",
            reserved_value=30,
        )
        db.add(reservation)
        db.flush()
        db.add(
            ProcessingJob(
                workspace_id=workspace_id,
                meeting_id=meeting_id,
                reservation_id=reservation.id,
                idempotency_key=f"warmup:{uuid.uuid4()}",
                stage="completed",
                attempt_count=1,
                max_attempts=3,
                created_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
            )
        )
        db.commit()


def _issues(person: Any, workspace_id: str, params: dict) -> dict:
    response = person.get("/api/v1/trends/issues", workspace_id=workspace_id, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _warm_app(settings: Any, **changes: Any):
    return create_app(
        settings.model_copy(update={"trend_judgment_warmup_enabled": True, **changes})
    )


def test_the_setting_turns_the_warmup_off(client):
    assert client.app.state.trend_judgment_warmup is None
    assert isinstance(client.app.state.trend_judgment_cache, JudgmentCache)


def test_the_warmup_fills_the_cache_before_the_first_request(client, email_outbox, settings):
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=1)
    _activity(ref, ref.b.workspace_id, minutes_ago=30)
    expected = _issues(ref.people["owner"], ref.a.workspace_id, ref.window())
    app = _warm_app(settings)
    with TestClient(app) as warm:
        warmup = app.state.trend_judgment_warmup
        assert warmup is not None
        assert warmup.wait(WAIT)
        status = warmup.status()
        assert status["state"] == "done", status
        assert status["workspaces_warmed"] == 2 and status["entries_built"] > 0
        cache = app.state.trend_judgment_cache
        assert cache.stats()["entries"] == status["entries_built"]
        before = cache.stats()
        owner = ref.people["owner"]
        owner.client = warm
        assert _issues(owner, ref.a.workspace_id, ref.window()) == expected
        after = cache.stats()
        assert after["builds"] == before["builds"]  # nothing left to judge
        assert after["hits"] > before["hits"]
        # the coverage verdicts too (U2)
        assert after["verdict_builds"] == before["verdict_builds"]
        assert after["verdict_hits"] > before["verdict_hits"]
    assert not warmup._thread.is_alive()  # stopped with the app


def test_a_time_limit_of_zero_warms_no_verdict(client, email_outbox, settings):
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=1)
    app = _warm_app(settings, trend_coverage_verdict_ttl_seconds=0)
    with TestClient(app):
        warmup = app.state.trend_judgment_warmup
        assert warmup.wait(WAIT)
        stats = app.state.trend_judgment_cache.stats()
        assert warmup.status()["state"] == "done"
        assert stats["verdict_builds"] == 0 and stats["builds"] > 0


def test_the_most_recently_active_workspace_is_warmed_first(client, email_outbox, settings):
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=60)
    _activity(ref, ref.b.workspace_id, minutes_ago=1)
    app = _warm_app(settings, trend_judgment_cache_max_entries=1)
    with TestClient(app):
        warmup = app.state.trend_judgment_warmup
        assert warmup.wait(WAIT)
        status = warmup.status()
        assert status["state"] == "done" and status["workspaces_warmed"] == 1, status
        assert app.state.trend_judgment_cache.stats()["entries"] <= 1
        maps = app.state.trend_judgment_cache._maps
        assert {key.workspace_id for _kind, key in maps} == {ref.b.workspace_id}


def test_the_warmup_never_evicts(client, email_outbox, settings):
    """With a cap smaller than all workspaces' entries, the most active workspace keeps every
    entry it warmed, and the warm-up stops with no eviction (review finding 1)."""
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=60)
    _activity(ref, ref.b.workspace_id, minutes_ago=1)  # B is the most active
    roomy = _warm_app(settings)
    with TestClient(roomy):
        assert roomy.state.trend_judgment_warmup.wait(WAIT)
        warmed = {
            key: set(entries) for key, entries in roomy.state.trend_judgment_cache._maps.items()
        }
    of_b = {key: ids for key, ids in warmed.items() if key[1].workspace_id == ref.b.workspace_id}
    b_entries = sum(len(ids) for ids in of_b.values())
    a_judgments = sum(
        len(ids)
        for (kind, key), ids in warmed.items()
        if kind == "trend_judgment" and key.workspace_id == ref.a.workspace_id
    )
    assert b_entries > 0 and a_judgments > 1
    cap = b_entries + a_judgments // 2  # all of B fits; A's judgments alone do not
    app = _warm_app(settings, trend_judgment_cache_max_entries=cap)
    with TestClient(app):
        warmup = app.state.trend_judgment_warmup
        assert warmup.wait(WAIT)
        cache = app.state.trend_judgment_cache
        stats = cache.stats()
        assert warmup.status()["state"] == "done", warmup.status()
        assert stats["evictions"] == 0
        assert stats["entries"] == cap == warmup.status()["entries_built"]
        for key, ids in of_b.items():
            assert set(cache._maps[key]) == ids  # B keeps every entry it warmed


def test_after_the_warmup_a_request_evicts_the_least_active_and_oldest_first(
    client, email_outbox, settings
):
    """Final review finding 1: the warm-up leaves the order a request would leave.  With the
    cache full after a warm-up of two workspaces (B the most active), a request that needs 2
    entries evicts the 2 oldest conversations of the less active workspace A, and B keeps
    everything.  (Verdicts off, so the maps hold judgments only.)"""
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=60)
    _activity(ref, ref.b.workspace_id, minutes_ago=1)
    roomy = _warm_app(settings, trend_coverage_verdict_ttl_seconds=0)
    with TestClient(roomy):
        assert roomy.state.trend_judgment_warmup.wait(WAIT)
        full = roomy.state.trend_judgment_cache.stats()["entries"]
    app = _warm_app(
        settings, trend_coverage_verdict_ttl_seconds=0, trend_judgment_cache_max_entries=full
    )
    with TestClient(app):
        assert app.state.trend_judgment_warmup.wait(WAIT)
        cache = app.state.trend_judgment_cache
        assert cache.stats()["entries"] == full and cache.stats()["evictions"] == 0
        maps = {key.workspace_id: entries for (kind, key), entries in cache._maps.items()}
        assert set(maps) == {ref.a.workspace_id, ref.b.workspace_id}
        of_a = dict(maps[ref.a.workspace_id])
        of_b = set(maps[ref.b.workspace_id])
        oldest_of_a = sorted(of_a, key=lambda run_id: (of_a[run_id][0], run_id))[:2]
        cold = judgment_key(None, "a-cold-workspace", trend_definition())
        cache.resolve(
            cold,
            hits=(),
            misses={f"cold-{n}": (datetime.now(UTC), EXTRACTIONS["C"]) for n in range(2)},
            build=cache.judge(trend_definition()).content,
        )
        assert cache.stats()["evictions"] == 2
        after = {key.workspace_id: set(entries) for (kind, key), entries in cache._maps.items()}
        assert after[ref.b.workspace_id] == of_b  # the most active keeps everything
        assert set(of_a) - after[ref.a.workspace_id] == set(oldest_of_a)
        assert all(kind == JUDGMENTS for kind, _key in cache._maps)


def test_a_request_never_waits_on_a_paused_warmup(client, email_outbox, monkeypatch):
    """The warm-up waits for requests only between batches, holding no claim, so a request
    that needs a run the warm-up is judging never waits for ``wait_seconds`` (implementer
    finding 6).  Here a batch is one run, ``wait_seconds`` is an hour, and the warm-up's first
    judgment goes on only once the request uses the cache: before the fix the warm-up then
    paused with every other run of the workspace claimed, and the request waited on them."""
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=1)
    owner = ref.people["owner"]
    expected = _issues(owner, ref.a.workspace_id, ref.window())
    from backend.database.relational import get_session_factory

    monkeypatch.setattr(trend_judgment_warmup, "YIELD_EVERY", 1)
    cache = JudgmentCache(max_entries=100_000, wait_seconds=3600)
    client.app.state.trend_judgment_cache = cache
    warmup = JudgmentWarmup(cache, get_session_factory())
    judging = threading.Event()
    content = SignatureJudge.content

    def first_judgment_waits(self: SignatureJudge, result: object) -> object:
        if threading.current_thread().name == "trend-judgment-warmup" and not judging.is_set():
            judging.set()  # the warm-up holds its claim now
            for _ in range(int(WAIT / 0.01)):  # until the request uses the cache (bounded)
                if cache.active_requests():
                    break
                time.sleep(0.01)
        return content(self, result)

    monkeypatch.setattr(SignatureJudge, "content", first_judgment_waits)
    answers: list[dict] = []
    request = threading.Thread(
        target=lambda: answers.append(_issues(owner, ref.a.workspace_id, ref.window())),
        daemon=True,
    )
    try:
        warmup.start()
        assert judging.wait(WAIT)
        request.start()
        request.join(WAIT)
        assert not request.is_alive(), "the request waited on a run the paused warm-up claimed"
        assert answers == [expected]
        assert warmup.wait(WAIT)
        assert warmup.status()["state"] == "done"
    finally:
        warmup.stop()  # also frees a request stuck on a claim, so the test never hangs
        request.join(WAIT)


def test_a_request_during_the_warmup_is_answered_correctly(client, email_outbox, settings):
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=1)
    owner = ref.people["owner"]
    expected = _issues(owner, ref.a.workspace_id, ref.window())
    ready = client.get("/readyz").status_code
    app = _warm_app(settings)
    with TestClient(app) as warm:
        assert warm.get("/readyz").status_code == ready  # never blocked or failed by it
        owner.client = warm
        assert _issues(owner, ref.a.workspace_id, ref.window()) == expected
        assert app.state.trend_judgment_warmup.wait(WAIT)
        assert _issues(owner, ref.a.workspace_id, ref.window()) == expected


def test_the_warmup_waits_while_a_request_uses_the_cache(client, email_outbox):
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=1)
    from backend.database.relational import get_session_factory

    cache = JudgmentCache(max_entries=100_000)
    warmup = JudgmentWarmup(cache, get_session_factory())
    with cache.request():  # a request holds the cache
        warmup.start()
        assert not warmup.wait(0.5)
        assert warmup.status()["workspaces_warmed"] == 0
    assert warmup.wait(WAIT)
    assert warmup.status()["state"] == "done"


def test_stop_ends_the_warmup_cleanly_and_twice(client, email_outbox):
    ref = build_reference(client, email_outbox)
    _activity(ref, ref.a.workspace_id, minutes_ago=1)
    from backend.database.relational import get_session_factory

    cache = JudgmentCache(max_entries=100_000)
    warmup = JudgmentWarmup(cache, get_session_factory())
    with cache.request():
        warmup.start()
        warmup.stop()
        warmup.stop()  # idempotent
    assert warmup.wait(WAIT)
    assert warmup.status()["state"] == "stopped"
    assert not warmup._thread.is_alive()


def test_a_failing_warmup_does_not_take_the_app_down(client):
    def broken() -> Any:
        raise RuntimeError("no database")

    warmup = JudgmentWarmup(JudgmentCache(max_entries=10), broken)
    warmup.start()
    assert warmup.wait(WAIT)
    status = warmup.status()
    assert status["state"] == "failed" and status["error"] == "RuntimeError"
    assert client.get("/healthz").status_code == 200
