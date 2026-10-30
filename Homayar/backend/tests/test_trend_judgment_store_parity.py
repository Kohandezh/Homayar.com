"""HOM-290 WU2: the trend routes read the stored judgments, with the same answers.

The routes count the conversations whose current run has a stored judgment of the current
rules (definition digest and ``JUDGMENT_VERSION``) by its content key: one representative
extraction per distinct key is judged with ``SignatureJudge.content``, and its content is used
for every conversation of that key.  A conversation without such a row is judged as before
(HOM-312 cache and Python).  So every answer is the same, byte for byte, without rows, with
every row and with a mix, on the HOM-84 reference dataset (the 92 cells of the permission
matrix and the statement-count routes) and on a randomised corpus.  A row of another
definition or judging version is never used; a row of the current ones is (its key decides).
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, text, true

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AnalysisExtraction,
    AnalysisRun,
    IngestionRecord,
    IngestionSourceType,
    StageState,
    TrendExtractionJudgment,
)
from backend.database.tenant_context import bind_tenant_context
from backend.issue_trends import validate_filters
from backend.issue_trends.counting import JUDGMENT_VERSION, SignatureJudge
from backend.services import trend_judgment_store as store
from backend.services.issue_trend_service import judge_population, trend_definition
from backend.services.trend_judgment_cache import JudgmentCache
from backend.tests._database import inspection_session_factory
from backend.tests._trend_reference import (
    EXTRACTIONS,
    Reference,
    build_reference,
    seed_conversations,
)
from backend.tests.test_coverage import Tenant
from backend.tests.test_issue_trend_service import NOW, add_team, seed
from backend.tests.test_trend_judgment_cache_judge import _corpus
from backend.tests.test_trend_permission_matrix import _send, matrix
from backend.tests.test_trend_reference_dataset import BILLING, INTENT, ISSUES, _cell
from backend.tests.test_trend_statement_count import STATEMENTS, _counts, _requests

J = TrendExtractionJudgment

# route -> populations judged per request: each one with a stored row reads its keys'
# representatives in one more statement (``trend_judgment_store.stored_contents``).
POPULATIONS = {
    "trends_issues": 1,
    "trends_issues_api_key": 1,
    "trends_issues_supervisor": 1,
    "trends_teams": 1,
    "trends_me": 1,
    "trends_conversations": 1,
    "organization_trends": 2,
    "organization_trends_restricted": 1,
    "organization_drilldown": 1,
    "organization_aggregates": 2,  # its HOM-299 metadata judges each workspace
    "voicemail_analytics": 0,
    "admin_coverage": 0,
}


# --- row modes --------------------------------------------------------------------------------


def _backfill(*workspace_ids: str) -> int:
    written = 0
    for workspace_id in workspace_ids:
        with get_session_factory()() as db:
            written += store.backfill_judgments(db, workspace_id=workspace_id, batch_size=7)
    return written


def _rows() -> list[TrendExtractionJudgment]:
    with inspection_session_factory()() as db:
        return list(db.scalars(select(J).order_by(J.id)))


def _keep_every_other() -> int:
    """Delete every second row (by id), as the DBA: a half-backfilled state."""
    gone = [row.id for row in _rows()][::2]
    with inspection_session_factory()() as db:
        db.execute(
            text("DELETE FROM trend_extraction_judgments WHERE id = ANY(:ids)"), {"ids": gone}
        )
        db.commit()
    return len(gone)


def _clear_rows() -> None:
    with inspection_session_factory()() as db:
        db.execute(text("DELETE FROM trend_extraction_judgments"))
        db.commit()


def _current_runs(*workspace_ids: str) -> set[str]:
    """The current runs with an extraction row (the runs a backfill writes)."""
    with inspection_session_factory()() as db:
        return set(
            db.scalars(
                select(AnalysisExtraction.analysis_run_id)
                .join(AnalysisRun, AnalysisRun.id == AnalysisExtraction.analysis_run_id)
                .where(AnalysisRun.is_current, AnalysisExtraction.workspace_id.in_(workspace_ids))
            )
        )


# --- the reference dataset ------------------------------------------------------------------------


# Two things of ``/aggregates`` differ between calls with or without rows, outside HOM-290: the
# time of the call (``generated_at``) and the key order of ``meetings_by_status``, which follows
# the database (``organization_service.workspace_aggregates``: GROUP BY status, no ORDER BY).
GENERATED_AT = re.compile(r'"generated_at":"[^"]*"')
BY_STATUS = re.compile(r'"meetings_by_status":(\{[^{}]*\})')


def _stable(body: str) -> str:
    body = GENERATED_AT.sub('"generated_at":"-"', body)
    return BY_STATUS.sub(
        lambda match: '"meetings_by_status":'
        + json.dumps(json.loads(match.group(1)), sort_keys=True, separators=(",", ":")),
        body,
    )


def _answers(ref: Reference) -> dict[str, tuple[int, str]]:
    """Every cell of the permission matrix and every statement-count route: the body of a 200
    (byte for byte, but for the two parts ``_stable`` sorts out), or the status and error code
    (an error body has a request id)."""
    answers = {}
    requests = [(cell["id"], cell["caller"], cell["route"], cell["workspace"], cell["params"])
                for cell in matrix(ref)]  # fmt: skip
    requests += [
        (f"pin:{name}", caller, path, workspace, params)
        for name, (caller, path, workspace, params) in _requests(ref).items()
    ]
    for name, caller, path, workspace, params in requests:
        response = _send(ref, caller, path, workspace, params)
        if response.status_code == 200:
            answers[name] = (200, _stable(response.text))
        else:
            answers[name] = (response.status_code, response.json()["error"]["code"])
    return answers


def test_reference_answers_are_the_same_without_with_and_with_some_rows(client, email_outbox):
    ref = build_reference(client, email_outbox)
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    workspaces = (ref.a.workspace_id, ref.b.workspace_id)

    cache.clear()
    none = _answers(ref)  # no row: every run judged from its JSON, the cache fills
    assert sum(status == 200 for status, _ in none.values()) > 50

    assert _backfill(*workspaces) == len(_current_runs(*workspaces)) > 100
    every_warm = _answers(ref)  # the cache still holds every run: the rows win
    cache.clear()
    every_cold = _answers(ref)

    assert _keep_every_other() > 50
    some_cold = _answers(ref)  # the cache holds only the rows' complement now
    cache.clear()
    some_again = _answers(ref)

    for name, answers in (
        ("every_warm", every_warm),
        ("every_cold", every_cold),
        ("some_cold", some_cold),
        ("some_again", some_again),
    ):
        differ = {
            key: _first_difference(none[key], answers[key])
            for key in none
            if answers[key] != none[key]
        }
        assert differ == {}, (name, differ)


def _first_difference(left: tuple[int, str], right: tuple[int, str]) -> str:
    """The path and values of the first difference of two answers (for the failure text)."""
    if left[0] != right[0] or left[0] != 200:
        return f"{left} != {right}"

    def walk(a: Any, b: Any, path: str) -> str | None:
        if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
            return next((d for k in a if (d := walk(a[k], b[k], f"{path}.{k}"))), None)
        if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
            return next((d for i, x in enumerate(a) if (d := walk(x, b[i], f"{path}[{i}]"))), None)
        return None if a == b else f"{path}: {json.dumps(a)[:200]} != {json.dumps(b)[:200]}"

    return walk(json.loads(left[1]), json.loads(right[1]), "") or "the same JSON, other bytes"


def test_the_stored_rows_are_read_one_content_per_key(client, email_outbox, monkeypatch):
    ref = build_reference(client, email_outbox)
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    calls: list[object] = []
    real = SignatureJudge.content

    def counted(self, result):
        calls.append(result)
        return real(self, result)

    monkeypatch.setattr(SignatureJudge, "content", counted)
    window = ref.window()

    cache.clear()
    before = _send(ref, "owner", ISSUES, ref.a.workspace_id, window)
    judged_without_rows = len(calls)

    _backfill(ref.a.workspace_id, ref.b.workspace_id)
    keys = {row.content_key for row in _rows() if row.workspace_id == ref.a.workspace_id}
    assert len(keys) == len(EXTRACTIONS) == 3  # C, B and V
    calls.clear()
    cache.clear()
    stats = cache.stats()
    after = _send(ref, "owner", ISSUES, ref.a.workspace_id, window)
    assert after.status_code == before.status_code == 200
    assert after.text == before.text
    # One judged content per distinct key, against one per analysed run without rows.
    assert len(calls) == len(keys)
    assert judged_without_rows > 60
    # No run went through the fallback (the cache's judgment counters did not move).
    moved = {k: cache.stats()[k] - stats[k] for k in ("hits", "misses", "builds")}
    assert moved == {"hits": 0, "misses": 0, "builds": 0}


def _rewrite(ref: Reference, letter: str, *, key: str, digest: str, version: str) -> int:
    """Give every run of workspace A whose extraction is ``letter`` one row (as the DBA)."""
    with inspection_session_factory()() as db:
        extractions = [
            e
            for e in db.scalars(
                select(AnalysisExtraction).where(
                    AnalysisExtraction.workspace_id == ref.a.workspace_id
                )
            )
            if e.result == EXTRACTIONS[letter]
        ]
        _add_rows(db, extractions, key=key, digest=digest, version=version)
        return len(extractions)


def _add_rows(db, extractions, *, key: str, digest: str, version: str) -> None:
    definition = trend_definition()
    for extraction in extractions:
        db.add(
            J(
                workspace_id=extraction.workspace_id,
                extraction_id=extraction.id,
                analysis_run_id=extraction.analysis_run_id,
                definition_id=definition.definition_id,
                definition_version=definition.version,
                definition_digest=digest,
                judgment_version=version,
                content_key=key,
            )
        )
    db.commit()


def _one_key_for_the_window(ref: Reference, key: str) -> tuple[int, str]:
    """One current row with ``key`` for every analysed run of workspace A in the window (as the
    DBA).  Returns their number and the letter of the smallest run id: the representative."""
    window = ref.window()
    with inspection_session_factory()() as db:
        extractions = list(
            db.scalars(
                select(AnalysisExtraction)
                .join(AnalysisRun, AnalysisRun.id == AnalysisExtraction.analysis_run_id)
                .join(IngestionRecord, IngestionRecord.meeting_id == AnalysisRun.meeting_id)
                .where(
                    AnalysisExtraction.workspace_id == ref.a.workspace_id,
                    AnalysisRun.is_current,
                    IngestionRecord.received_at >= datetime.fromisoformat(window["from"]),
                    IngestionRecord.received_at < datetime.fromisoformat(window["to"]),
                )
                .order_by(AnalysisExtraction.analysis_run_id)
            )
        )
        letter = next(k for k, v in EXTRACTIONS.items() if v == extractions[0].result)
        definition = trend_definition()
        _add_rows(db, extractions, key=key, digest=definition.digest, version=JUDGMENT_VERSION)
        return len(extractions), letter


def test_only_rows_of_the_current_rules_are_used(client, email_outbox):
    ref = build_reference(client, email_outbox)
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    window = ref.window()
    definition = trend_definition()
    billing = store.content_key(SignatureJudge(definition).content(EXTRACTIONS["B"]))

    def whole() -> dict[str, Any]:
        cache.clear()
        response = _send(ref, "owner", ISSUES, ref.a.workspace_id, window)
        assert response.status_code == 200, response.text
        return response.json()

    baseline = whole()
    assert _cell(baseline, *BILLING)[1:3] == (35, 73)
    assert _cell(baseline, *INTENT)[1:3] == (38, 73)

    # Deny: the complaints carry the billing key (with a billing run as the representative,
    # they would count as billing), but for an older definition, then for an older judging
    # version: those rows are not read (each run keeps its own content).
    complaints = _rewrite(ref, "C", key=billing, digest="0" * 64, version=JUDGMENT_VERSION)
    assert complaints == 35  # 33 in the window, the W0 call and one five weeks before W1
    _rewrite(ref, "B", key=billing, digest="0" * 64, version=JUDGMENT_VERSION)
    assert whole() == baseline
    with inspection_session_factory()() as db:
        db.execute(
            text(
                "UPDATE trend_extraction_judgments SET judgment_version = 'judgment/0', "
                "definition_digest = :d"
            ),
            {"d": definition.digest},
        )
        db.commit()
    assert whole() == baseline

    # Allow-control: the same grouping under the current definition and version is read and
    # trusted: every conversation takes the content of the key's representative extraction.
    tagged, letter = _one_key_for_the_window(ref, "ab" * 32)
    assert tagged == 73
    trusted = whole()
    assert trusted != baseline
    assert _cell(trusted, *BILLING)[1:3] == ((73 if letter == "B" else 0), 73)
    assert _cell(trusted, *INTENT)[1:3] == ((73 if letter in ("C", "V") else 0), 73)


def test_statements_with_rows_add_one_per_population_and_do_not_grow(client, email_outbox):
    ref = build_reference(client, email_outbox)
    _backfill(ref.a.workspace_id, ref.b.workspace_id)
    base = _counts(ref)
    seed_conversations(ref)  # twice the conversations of every cohort, all with rows
    _backfill(ref.a.workspace_id, ref.b.workspace_id)
    doubled = _counts(ref)
    assert doubled == base, {k: (base[k], doubled[k]) for k in base if base[k] != doubled[k]}
    expected = {name: STATEMENTS[name] + POPULATIONS[name] for name in STATEMENTS}
    assert base == expected, sorted(base.items())


# --- a randomised corpus ----------------------------------------------------------------------


GAPS = (
    ["row"] * 10
    + ["no_run", "stale", "revision_mock", "run_mock", "failed", "partial", "no_row"]
    + ["duplicate", "old_runs", "no_transcript"]
)


def _random_corpus(tenant: Tenant, rng: random.Random, count: int) -> dict[str, str]:
    """``count`` conversations with random stored rows (realistic and broken) and random
    conversation-level facts; returns the filter window."""
    rows = _corpus(seed=290, count=count)
    teams = [add_team(tenant, f"rand-{n}") for n in range(3)]
    start = NOW - timedelta(days=20)
    seeded: list[str] = []
    for n in range(count):
        gap = rng.choice(GAPS)
        options: dict[str, Any] = {
            "received_at": start + timedelta(hours=7 * n),
            "extraction": rng.choice(rows),
            "team_id": rng.choice([*teams, None]),
            "agent_user_id": rng.choice([tenant.owner_id, None]),
            "language": rng.choice(["fa", "fa", "en"]),
            "source_type": rng.choice(list(IngestionSourceType)[:3]),
        }
        if gap == "no_run":
            options["run"] = None
        elif gap == "stale":
            options["run"] = "stale"
        elif gap == "revision_mock":
            options["revision_mock"] = True
        elif gap == "run_mock":
            options["run_mock"] = True
        elif gap in ("failed", "partial"):
            options["run_state"] = StageState(gap)
        elif gap == "no_row":
            options["extraction"] = None
        elif gap == "duplicate" and seeded:
            options["duplicate_of"] = rng.choice(seeded)
        elif gap == "old_runs":
            options["old_runs"] = [rng.choice(rows)]
        elif gap == "no_transcript":
            options["segments"] = 0
        seeded.append(seed(tenant, **options).ingestion_id)
    return {"from": start.isoformat(), "to": (start + timedelta(hours=7 * count)).isoformat()}


def _judged(tenant: Tenant, window: dict[str, str], cache: JudgmentCache | None) -> list[str]:
    """Every conversation of the window as judged by ``judge_population`` (canonical JSON)."""
    filters = validate_filters(window, trend_definition(), now=NOW)
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant.workspace_id)
        judged = judge_population(
            db,
            workspace_id=tenant.workspace_id,
            scope=true(),
            filters=filters,
            cache=cache,
            organization_id=tenant.organization_id,
        )
        db.rollback()
    return sorted(
        json.dumps(
            {
                "received_at": c.received_at.isoformat(),
                "team_id": c.team_id,
                "agent_user_id": c.agent_user_id,
                "language": c.language,
                "source_type": c.source_type,
                "run": c.analysis_run_id,
                "judgment": c.judgment.to_json(),
            },
            sort_keys=True,
        )
        for c in judged
    )


def _route_bodies(tenant: Tenant, window: dict[str, str]) -> list[str]:
    bodies = []
    for path, extra in (
        (ISSUES, {}),
        (ISSUES, {"grain": "week"}),
        ("/api/v1/trends/teams", {"grain": "week"}),
    ):
        response = tenant.get(path, **window, **extra)
        assert response.status_code == 200, (path, response.text)
        bodies.append(response.text)
    return bodies


def test_a_randomised_corpus_is_judged_the_same_with_and_without_rows(client, email_outbox):
    tenant = Tenant(client, email_outbox, "hom290-random@example.com")
    rng = random.Random(290)  # noqa: S311 - a fixed test corpus
    window = _random_corpus(tenant, rng, count=120)
    app_cache: JudgmentCache = client.app.state.trend_judgment_cache

    def state() -> tuple[list[str], list[str], list[str]]:
        app_cache.clear()
        return (
            _judged(tenant, window, None),
            _judged(tenant, window, JudgmentCache(10_000)),
            _route_bodies(tenant, window),
        )

    none = state()
    assert len(none[0]) > 100
    written = _backfill(tenant.workspace_id)
    assert written == len(_current_runs(tenant.workspace_id)) > 50
    every = state()
    _keep_every_other()
    some = state()
    _clear_rows()
    again = state()
    assert every == none
    assert some == none
    assert again == none
