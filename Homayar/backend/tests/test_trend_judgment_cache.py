"""HOM-312: the trend judgment cache (option N, ADR 0043).

* The cache's own rules: the stats keys, the cap, LRU eviction, ``clear``, separate keys, the
  eviction race (a request keeps the copies it pinned), one build per run over many threads, and
  a request that needs more than the free room evicts its own older misses, never its hits.
* Through the routes, on the HOM-84 reference dataset: every pinned route and caller gives the
  same JSON bytes and the same pinned statement count with the cache empty, half full and
  full, and with a cap so small that it evicts all the time.
* A new run is judged fresh: after a re-analysis (a new current run with another extraction
  row) the answer follows the new run, and exactly one judgment is built, counted over every
  judging path (``judge_conversation`` and ``SignatureJudge.content``).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select, true, update

from backend.database.relational import get_engine
from backend.database.saas_models import (
    AnalysisRun,
    StageState,
    TranscriptRevision,
)
from backend.issue_trends import counting, validate_filters
from backend.issue_trends.counting import SignatureJudge
from backend.issue_trends.judgment_cache import (
    JUDGMENTS,
    VERDICTS,
    CacheKey,
    JudgmentCache,
    VerdictKey,
    judgment_key,
)
from backend.services.issue_trend_service import judge_population, trend_definition
from backend.tests._database import inspection_session_factory
from backend.tests._trend_reference import EXTRACTIONS, build_reference
from backend.tests.test_api_keys import _bearer
from backend.tests.test_issue_trend_service import _extraction_row
from backend.tests.test_trend_statement_count import STATEMENTS, _requests

STATS_KEYS = {"entries", "hits", "misses", "builds", "evictions"}


def _content(name: str) -> counting.JudgedContent:
    return SignatureJudge(trend_definition()).content(EXTRACTIONS[name])


def _key(workspace: str = "w1", organization: str | None = "o1") -> CacheKey:
    return judgment_key(organization, workspace, trend_definition())


AT = datetime(2026, 3, 1, tzinfo=UTC)


# --- the cache --------------------------------------------------------------------------------


def test_the_stats_count_builds_hits_misses_and_the_cap_holds():
    cache = JudgmentCache(max_entries=3)
    assert STATS_KEYS <= set(cache.stats())
    assert cache.stats()["entries"] == 0
    misses = {f"r{n}": (AT + timedelta(minutes=n), EXTRACTIONS["C"]) for n in range(5)}
    built = cache.resolve(
        _key(), hits=[], misses=misses, build=SignatureJudge(trend_definition()).content
    )
    assert set(built) == set(misses)
    stats = cache.stats()
    assert stats["misses"] == 5 and stats["builds"] == 5
    assert stats["entries"] == 3 and stats["evictions"] == 2  # the cap holds
    pinned = cache.pin(_key(), AT, AT + timedelta(days=1))
    assert set(pinned) == {"r2", "r3", "r4"}  # the oldest went first
    cache.resolve(_key(), hits=list(pinned), misses={}, build=lambda _: pytest.fail("no build"))
    assert cache.stats()["hits"] == 3
    cache.clear()
    assert cache.stats()["entries"] == 0
    assert cache.pin(_key(), AT, AT + timedelta(days=1)) == {}


def test_the_least_recently_used_workspace_is_evicted_first():
    cache = JudgmentCache(max_entries=4)
    judge = SignatureJudge(trend_definition())
    for workspace in ("a", "b"):
        cache.resolve(
            _key(workspace),
            hits=[],
            misses={f"{workspace}{n}": (AT, EXTRACTIONS["B"]) for n in range(2)},
            build=judge.content,
        )
    cache.pin(_key("a"), AT, AT + timedelta(days=1))  # a is used again
    cache.resolve(_key("c"), hits=[], misses={"c0": (AT, EXTRACTIONS["V"])}, build=judge.content)
    assert set(cache.pin(_key("b"), AT, AT + timedelta(days=1))) == {"b1"}
    assert set(cache.pin(_key("a"), AT, AT + timedelta(days=1))) == {"a0", "a1"}


def test_keys_never_share_entries():
    cache = JudgmentCache(max_entries=100)
    judge = SignatureJudge(trend_definition())
    cache.resolve(
        _key("w1", "o1"), hits=[], misses={"r": (AT, EXTRACTIONS["C"])}, build=judge.content
    )
    window = (AT, AT + timedelta(days=1))
    assert cache.pin(_key("w2", "o1"), *window) == {}
    assert cache.pin(_key("w1", "o2"), *window) == {}
    other = CacheKey("o1", "w1", trend_definition().digest, judgment_version="judgment/0")
    assert cache.pin(other, *window) == {}
    assert set(cache.pin(_key("w1", "o1"), *window)) == {"r"}


def test_a_pinned_copy_survives_an_eviction():
    cache = JudgmentCache(max_entries=1)
    judge = SignatureJudge(trend_definition())
    cache.resolve(_key(), hits=[], misses={"r1": (AT, EXTRACTIONS["C"])}, build=judge.content)
    pinned = cache.pin(_key(), AT, AT + timedelta(days=1))
    cache.resolve(_key("other"), hits=[], misses={"x": (AT, EXTRACTIONS["B"])}, build=judge.content)
    assert cache.pin(_key(), AT, AT + timedelta(days=1)) == {}  # evicted from the cache
    assert pinned == {"r1": judge.content(EXTRACTIONS["C"])}  # the request still has it


def test_a_wide_request_keeps_its_own_hits():
    """Review round 2: cap 10, 4 recent runs cached, then a request with those 4 as hits and 8
    older misses.  The request evicts 2 of its own older misses, and keeps all 4 hits (the
    entries the next default-window request needs).  The same for coverage verdicts."""
    cache = JudgmentCache(max_entries=10)
    key = _key()
    recent = {
        f"recent-{n}": (AT + timedelta(days=30, minutes=n), EXTRACTIONS["C"]) for n in range(4)
    }
    cache.resolve(key, hits=(), misses=recent, build=cache.judge(trend_definition()).content)
    pinned = cache.pin(key, AT, AT + timedelta(days=60))
    assert set(pinned) == set(recent)
    older = {f"older-{n}": (AT + timedelta(minutes=n), EXTRACTIONS["B"]) for n in range(8)}
    found = cache.resolve(
        key, hits=list(pinned), misses=older, build=cache.judge(trend_definition()).content
    )
    assert set(found) == set(older)
    kept = set(cache._maps[(JUDGMENTS, key)])
    assert set(recent) <= kept, sorted(set(recent) - kept)
    assert cache.stats()["entries"] == 10 and cache.stats()["evictions"] == 2

    cache = JudgmentCache(max_entries=10)
    verdict_key = VerdictKey("o1", "w1", "verdict/1")
    cache.keep_verdicts(verdict_key, {f"recent-{n}": (b"p", 0) for n in range(4)})
    hits = cache.verdicts(verdict_key, {f"recent-{n}": b"p" for n in range(4)})
    assert len(hits) == 4
    cache.keep_verdicts(verdict_key, {f"older-{n}": (b"p", 1) for n in range(8)}, recent=list(hits))
    kept = set(cache._maps[(VERDICTS, verdict_key)])
    assert set(hits) <= kept, sorted(set(hits) - kept)
    assert cache.stats()["entries"] == 10 and cache.stats()["evictions"] == 2


def test_many_threads_build_one_run_once():
    cache = JudgmentCache(max_entries=100)
    judge = SignatureJudge(trend_definition())
    calls: list[str] = []
    gate = threading.Barrier(8)

    def slow(result: object) -> counting.JudgedContent:
        calls.append("build")
        threading.Event().wait(0.05)
        return judge.content(result)

    results: list[dict] = []

    def worker() -> None:
        gate.wait()
        results.append(
            cache.resolve(_key(), hits=[], misses={"r": (AT, EXTRACTIONS["C"])}, build=slow)
        )

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(calls) == 1
    assert all(result == results[0] for result in results)
    assert cache.stats()["builds"] == 1


def test_threads_of_one_cold_workspace_judge_each_run_once(client, email_outbox, monkeypatch):
    ref = build_reference(client, email_outbox)
    cache = JudgmentCache(max_entries=100_000)
    content = SignatureJudge.content
    builds: list[int] = []

    def counted(self: SignatureJudge, result: object) -> counting.JudgedContent:
        builds.append(1)
        threading.Event().wait(0.001)
        return content(self, result)

    monkeypatch.setattr(SignatureJudge, "content", counted)
    filters = validate_filters(ref.window(), trend_definition(), now=datetime.now(UTC))
    answers: list[list[Any]] = []
    errors: list[BaseException] = []
    gate = threading.Barrier(6)

    def worker() -> None:
        try:
            gate.wait()
            with inspection_session_factory()() as db:
                judged = judge_population(
                    db,
                    workspace_id=ref.a.workspace_id,
                    scope=true(),
                    filters=filters,
                    cache=cache,
                    organization_id=ref.organization_id,
                )
            answers.append([conversation.judgment for conversation in judged])
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert len(answers) == 6 and all(answer == answers[0] for answer in answers)
    assert len(builds) == cache.stats()["builds"] == cache.stats()["entries"]


# --- through the routes --------------------------------------------------------------------


def _get(
    ref: Any, caller: str, path: str, workspace: str | None, params: dict
) -> tuple[bytes, int]:
    seen: list[str] = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "set_config" not in statement:
            seen.append(statement)

    engine = get_engine()
    event.listen(engine, "before_cursor_execute", record)
    try:
        if caller == "api_key":
            ref.client.cookies.clear()
            response = ref.client.get(path, params=params, headers=_bearer(ref.reader_key))
        else:
            response = ref.people[caller].get(path, workspace_id=workspace, params=params)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert response.status_code == 200, (path, response.text)
    body = response.json()
    body.pop("generated_at", None)  # /aggregates stamps the time of each answer
    return json.dumps(body, sort_keys=True).encode(), len(seen)


def _answers(ref: Any) -> dict[str, tuple[bytes, int]]:
    return {name: _get(ref, *request) for name, request in _requests(ref).items()}


def test_every_route_answers_the_same_whatever_the_cache_holds(client, email_outbox):
    ref = build_reference(client, email_outbox)
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    cache.clear()
    cold = _answers(ref)
    assert {name: count for name, (_body, count) in cold.items()} == STATEMENTS
    assert cache.stats()["entries"] > 0
    warm = _answers(ref)
    assert warm == cold
    # half full: only the first week of the window was judged before
    cache.clear()
    first_week = ref.window(weeks=(0,))
    _get(ref, "owner", "/api/v1/trends/issues", ref.a.workspace_id, first_week)
    half = _answers(ref)
    assert half == cold
    assert cache.stats()["hits"] > 0


def test_a_tiny_cap_evicts_but_never_changes_an_answer(client, email_outbox):
    ref = build_reference(client, email_outbox)
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    cache.clear()
    cold = _answers(ref)
    tiny = JudgmentCache(max_entries=3)
    client.app.state.trend_judgment_cache = tiny
    try:
        assert _answers(ref) == cold
        assert _answers(ref) == cold
        assert tiny.stats()["entries"] <= 3
        assert tiny.stats()["evictions"] > 0
    finally:
        client.app.state.trend_judgment_cache = cache


def test_a_new_run_is_judged_fresh(client, email_outbox, monkeypatch):
    ref = build_reference(client, email_outbox)
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    owner = ref.people["owner"]
    path, params = "/api/v1/trends/issues", ref.window()

    def answer() -> dict:
        response = owner.get(path, workspace_id=ref.a.workspace_id, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    def cell(body: dict, row: str) -> Any:
        found = next(r for r in body["rows"] if f"{r['level']}:{r['issue'] or r['family']}" == row)
        return found["cell"]["numerator"], found["cell"]["denominator"]

    before = answer()
    assert cache.stats()["entries"] > 0
    # Re-analysis of one complaint ("C") as billing ("B"): a new current run with its own row.
    # A conversation of the window (weeks 0 to 2) with no gap: ``ref.meetings`` also returns
    # the C conversations outside the window (W0 and five weeks before), and ids are random.
    meeting_id = sorted(
        item.meeting_id
        for item in ref.conversations
        if item.workspace_id == ref.a.workspace_id
        and item.extraction == "C"
        and item.gap is None
        and item.week in (0, 1, 2)
    )[0]
    with inspection_session_factory()() as db:
        old = db.scalar(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(True)
            )
        )
        revision_id = db.scalar(
            select(TranscriptRevision.id).where(
                TranscriptRevision.meeting_id == meeting_id, TranscriptRevision.is_current.is_(True)
            )
        )
        db.execute(update(AnalysisRun).where(AnalysisRun.id == old.id).values(is_current=False))
        new = AnalysisRun(
            workspace_id=ref.a.workspace_id,
            meeting_id=meeting_id,
            transcript_revision_id=revision_id,
            state=StageState.COMPLETED,
            is_current=True,
        )
        db.add(new)
        db.flush()
        db.add(_extraction_row(ref.a.workspace_id, meeting_id, new.id, EXTRACTIONS["B"]))
        db.commit()

    paths: dict[str, int] = {"judge_conversation": 0, "content": 0}
    judge_conversation, content = counting.judge_conversation, SignatureJudge.content

    def count_conversation(*args: Any, **kwargs: Any) -> Any:
        paths["judge_conversation"] += 1
        return judge_conversation(*args, **kwargs)

    def count_content(self: SignatureJudge, result: object) -> Any:
        paths["content"] += 1
        return content(self, result)

    monkeypatch.setattr(counting, "judge_conversation", count_conversation)
    monkeypatch.setattr(SignatureJudge, "content", count_content)
    after = answer()
    assert paths == {"judge_conversation": 0, "content": 1}  # only the new run is judged
    monkeypatch.undo()
    cache.clear()
    assert after == answer()  # the same as with no cache at all
    row = "issue:intent_resolve_billing"
    assert cell(after, row) != cell(before, row)
