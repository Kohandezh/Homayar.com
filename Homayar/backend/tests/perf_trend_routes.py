"""HOM-84 (WP15-T06, F61), HOM-312: measured latency of the trend routes on 100,000 conversations.

Not a test: pytest never collects this file, and no test asserts a time.  Run it from
``Homayar/`` against the disposable PostgreSQL of the measurement (its schema is dropped and
created again, then seeded):

    uv run python -m backend.tests.perf_trend_routes --conversations 100000 --calls 20 \
        --slow-calls 20 --stages --warmup-measure
    uv run python -m backend.tests.perf_trend_routes --conversations 10000 --dataset legacy \
        --windows 12w --profile

Dataset, for ``--conversations 100000``:

* One organization with two workspaces.  Workspace A: 8 teams of 10 agents, 80,000
  conversations.  Workspace B: 2 teams of 10 agents, 20,000 conversations.  Spread evenly over
  the 12 closed weeks before this week (Saturday 00:00 Asia/Tehran), one conversation per
  agent in turn.  ``--conversations 10000`` keeps the teams, agents and weeks and seeds one
  tenth of the conversations and voicemails (fewer conversations per agent).
* Per 100 conversations: 85 with an analysis run and its extraction row, and the processing
  gaps 4 without an analysis run, 3 failed, 2 queued, 1 with an empty transcript, 1
  soft-deleted (with a run, no extraction row).
* ``--dataset realistic`` (the default, HOM-312): every extraction row is its own document, the
  real output of the pinned policy (``evaluate_extraction`` with the taxonomy ``conversation``
  v1 and ``POLICY_V1``), with its own confidence, basis and source turns per value, so almost
  every document is distinct, like production.  The label spread follows the taxonomy: intent
  single-valued (8 labels), topic multi-valued (0 to 3 of 11 labels), outcome single-valued (4
  labels), each in a Zipf spread (s = 1.2) over the labels in file order; some not stated or
  abstained.  0.5% are broken on purpose (a missing key: ``extraction_unreadable``) and 0.3% use
  another policy version (``version_mismatch``).  Every analysed call also has its report
  outputs, as the processor stores them (``jobs/processor.py``, ``persist_analysis``): 6
  transcript segments (the 6 turns the extraction cites), a summary whose ``key_points`` hold 2
  or 3 insights and the structured claims (3 summary fields, 0 to 2 decisions, 0 to 2 key
  moments), 0 to 3 AI action items, and 1 or 2 evidence spans per cited finding.  Each finding
  is grounded ``sufficient`` (90%), ``partial`` (7%) or ``insufficient`` (3%, no span), so about
  a quarter of the calls have an unsupported finding and coverage's ``unsupported_findings``
  rule does real work.  No processing jobs or stage runs are seeded (a job needs the usage
  reservation chain), so the coverage bucket ``transcript_partial`` stays empty.
  ``--no-summaries`` leaves the report outputs out (to measure their cost).  Deterministic:
  the documents and report outputs come from a seeded generator (``--seed``); only the row ids
  are random.
* ``--dataset legacy``: the HOM-84 dataset, 6 distinct extraction documents and no report
  outputs: per 100 conversations 20 complaint (refund, escalated), 24 billing (invoice), 5
  complaint with an abstained topic, 10 support request (technical fault, outcome
  ``resolved``, a critical label: pending review), 15 purchase inquiry (pricing, follow-up), 15
  complaint (delivery, unresolved).
* Languages fa (17 of 20) and en; sources file_upload, sip_voip and api.
* 2,000 voicemails in workspace A (no ledger row), of which 1 in 10 failed.
* Every meeting's ``created_at`` is its conversation's (or voicemail's) received time, so
  ``/aggregates``, which counts meetings by ``created_at``, reads the same weeks as the other
  routes.  No other measured route reads ``Meeting.created_at``.
* The reference personas of ``_trend_reference`` (no reference conversations; its three
  teams in A are made inactive).  The agent persona is the first agent of A's first team.

Windows (``--windows``, comma-separated):

* ``4w`` (the F61 target, HOM-312): every route on the 4 closed weeks before this week (the
  default window of the organization routes; ``/aggregates`` is called with no window, its
  default).
* ``12w-org``: the three organization routes on the 12 closed weeks, marked over target.
* ``12w``: every route on the 12 closed weeks (the HOM-84 measurement).

Load: sequential, one ``TestClient`` in this one process, one request at a time, no
concurrent client and no background job of the app (the warm-up is off, except in
``--warmup-measure``).  Other work on the same machine (tests, other agents) may have run at the
same time; the machine load (``uptime``) is printed at the start and the end.  Each route is
called through the real auth path (a signed-in session; the API-key row uses a ``meeting:read``
key), as the caller that sees the most: the owner of workspace A, who is also the organization
owner (``/trends/me``: the agent persona, its own calls).

The number of calls: ``--warmup`` (default 1) warm-up calls per route; then a route whose last
warm-up call took ``--slow-seconds`` (default 10) or more gets ``--slow-calls`` (default 5)
measured calls, every other route ``--calls`` (default 50).  To compare a "before" and an
"after" run, give every route the same count (``--slow-calls`` equal to ``--calls``), so that p95
means the same thing in both.  p50 and p95 are nearest-rank percentiles (the value at rank
``ceil(q * n)`` of the sorted times) of the wall time of the whole request in the client; with
n = 5, p95 is the maximum.  ``SQL ms`` is the median over the measured calls of the time spent
in database statements (``before_cursor_execute`` to ``after_cursor_execute``, the
``set_config`` calls of the row-level-security binding included); ``statements`` leaves those
calls out, as the CI guard.

``first ms`` is the first warm-up call of the route.  When the app has a trend judgment cache
(``app.state.trend_judgment_cache``, HOM-312) it is emptied right before that call, so ``first
ms`` is the cold first request of the route.  Without a cache it is just the first call.

``expired ms`` (HOM-312, U2): the median of ``EXPIRED_CALLS`` more calls, each made with every
cached coverage verdict older than the time limit (``trend_coverage_verdict_ttl_seconds``; the
cache clock is moved forward past it before each call), while the judgments stay cached: what
a request pays when it is the first after the limit.  ``n/a`` on code without verdicts.

``--stages``: one more call per route with wall-clock timers around the stages (the population
statement, ``judge_population``, ``judge_conversation``, and on HOM-312 code
``SignatureJudge.content`` (a miss judged) and ``SignatureJudge.judge``; coverage, its fact
statements, the U2 verdict part of them, and the part of coverage that serves the
``unsupported_findings`` rule: the summary, action item and evidence span statements with
their rows, plus ``unsupported_finding_count``).  A stage the code does not have prints
``n/a``.

``--warmup-measure`` (HOM-312): after the measured calls, add one processing job to each
workspace (A the most recent: the warm-up ranks workspaces by their latest job, and the dataset
has none), then start the app again with the warm-up on, twice.  First with no request: the
time from start until the warm-up is done, the process CPU time in that period, and the peak
resident memory added.  Then with requests sent one after the other (the ``4w`` routes in
turn) while the warm-up runs: their p50 and p95.  Code without a warm-up
(``app.state.trend_judgment_warmup`` missing or ``None``) prints "not available".

``--profile``: one more call per route under ``cProfile`` gives the statement count, the SQL
time, the request time (inflated by the profiler) and the top 2 Python hotspots: the
``backend`` functions (outside the routers) with the most cumulative time, where a function
is left out when one of its callees takes 80% or more of its time (a wrapper).

At the end, one more call of the route that ran the slowest SQL statement of all runs
``EXPLAIN (ANALYZE, BUFFERS)`` of that statement on the same connection, right after it ran,
so the role and the row-level-security context are the request's own.
"""

from __future__ import annotations

import argparse
import cProfile
import functools
import json
import math
import os
import platform
import pstats
import random
import resource
import statistics
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

DEFAULT_URL = "postgresql+psycopg://homayar_test:local-only@127.0.0.1:4981/homayar_test"
WEEKS = 12
TARGET_WEEKS = 4  # HOM-312: the F61 target window
CONVERSATIONS = {"a": 80_000, "b": 20_000}
TEAMS = {"a": 8, "b": 2}
AGENTS_PER_TEAM = 10
VOICEMAILS = 2_000
CHUNK = 5_000
SEED_METHOD = os.environ.get("PERF_SEED_METHOD", "copy")  # "executemany" for a check
EXPIRED_CALLS = 5
ROW = "issue:intent_report_dissatisfaction"
SEED = 312
OVER_TARGET = "over target"

# One slot per conversation of 100, in a fixed order: the extraction letter or the gap.
MIX = (
    ["C"] * 20 + ["B"] * 24 + ["V"] * 5 + ["S"] * 10 + ["P"] * 15 + ["D"] * 15
    + ["no_run"] * 4 + ["failed"] * 3 + ["queued"] * 2 + ["no_transcript"] + ["deleted"]
)  # fmt: skip
assert len(MIX) == 100

# The realistic dataset (HOM-312).  The 6 turns of a call: the extraction cites them, and the
# call has one transcript segment per turn.
TURNS = tuple(f"turn {index}" for index in range(6))
OUTCOME_WEIGHTS = {"resolved": 45, "unresolved": 25, "escalated": 15, "follow_up_scheduled": 15}
GROUNDING = (("sufficient", 90), ("partial", 7), ("insufficient", 3))
INSIGHT_CATEGORIES = ("billing", "delivery", "pricing", "support", "account")
KEY_MOMENT_KINDS = ("commitment", "objection", "escalation")


def _extractions() -> dict[str, Any]:
    from backend.tests._trend_reference import EXTRACTIONS
    from backend.tests.test_issue_trends_counting import ok, stored

    return {
        **EXTRACTIONS,
        "S": stored(intent=ok("request_support"), topic=ok("technical_fault"),
                    outcome=ok("resolved")),
        "P": stored(intent=ok("purchase_inquiry"), topic=ok("pricing"),
                    outcome=ok("follow_up_scheduled")),
        "D": stored(intent=ok("report_dissatisfaction"), topic=ok("delivery"),
                    outcome=ok("unresolved")),
    }  # fmt: skip


def _zipf(count: int, power: float = 1.2) -> list[float]:
    return [1 / (rank + 1) ** power for rank in range(count)]


class _Documents:
    """Realistic stored extraction documents: the pinned policy's real output for a generated
    model answer (see the module docstring), and the row columns ``store_extraction`` writes."""

    def __init__(self, rng: random.Random) -> None:
        from backend.analysis_taxonomy import load_taxonomy

        self.rng = rng
        self.taxonomy = load_taxonomy("conversation", 1)
        self.labels = {
            name: [label.id for label in self.taxonomy.dimension(name).labels]
            for name in ("intent", "topic", "outcome")
        }
        self.explicit_only = {
            label.id
            for name in ("intent", "topic", "outcome")
            for label in self.taxonomy.dimension(name).labels
            if "inferred" not in {basis.value for basis in label.allowed_basis}
        }

    def _value(self, label: str) -> dict[str, Any]:
        rng = self.rng
        inferred = label not in self.explicit_only and rng.random() >= 0.75
        return {
            "label": label,
            "basis": "inferred" if inferred else "explicit",
            "source_turn_indexes": sorted(rng.sample(range(len(TURNS)), rng.randint(1, 3))),
            "confidence": round(rng.uniform(0.35, 0.99), 3),
        }

    def _pick(self, labels: list[str], weights: list[float], count: int) -> list[str]:
        pool, left, chosen = list(labels), list(weights), []
        for _ in range(count):
            index = self.rng.choices(range(len(pool)), weights=left)[0]
            chosen.append(pool.pop(index))
            left.pop(index)
        return chosen

    def _single(self, name: str, weights: list[float], gaps: tuple[float, ...]) -> dict[str, Any]:
        """A single-valued field: not stated, an abstain status, or one label."""
        roll = self.rng.random()
        statuses = ("not_stated", "insufficient_evidence", "contradictory")[: len(gaps)]
        for status, below in zip(statuses, gaps, strict=True):
            if roll < below:
                return {"status": status, "values": []}
        label = self._pick(self.labels[name], weights, 1)[0]
        return {"status": "stated", "values": [self._value(label)]}

    def _answer(self) -> dict[str, Any]:
        intent = self._single("intent", _zipf(len(self.labels["intent"])), (0.05, 0.09, 0.10))
        count = self.rng.choices((0, 1, 2, 3), weights=(15, 50, 25, 10))[0]
        if count:
            labels = self._pick(self.labels["topic"], _zipf(len(self.labels["topic"])), count)
            topic: dict[str, Any] = {
                "status": "stated",
                "values": [self._value(label) for label in labels],
            }
        else:
            topic = {"status": "not_stated", "values": []}
        outcome_weights = [OUTCOME_WEIGHTS[label] for label in self.labels["outcome"]]
        outcome = self._single("outcome", outcome_weights, (0.08, 0.12))
        return {
            "intent": intent,
            "topic": topic,
            "entity": {"status": "not_stated", "values": []},
            "outcome": outcome,
        }

    def row(self) -> dict[str, Any]:
        """The ``analysis_extractions`` columns of one document (no ids)."""
        from backend.analysis_taxonomy import Disposition, evaluate_extraction
        from backend.services.analysis_extraction import kept_values

        result = evaluate_extraction(self._answer(), self.taxonomy, TURNS)
        document = result.to_json()
        columns = {
            "taxonomy_id": result.taxonomy_id,
            "taxonomy_version": result.taxonomy_version,
            "taxonomy_digest": result.taxonomy_digest,
            "validator_version": result.validator_version,
            "policy_version": result.policy_version,
            "policy_digest": result.policy_digest,
            "valid": result.valid,
            "needs_review": result.needs_review,
            "review_count": sum(
                value.disposition is Disposition.REVIEW_REQUIRED for value in kept_values(result)
            ),
        }
        roll = self.rng.random()
        if roll < 0.005:
            document["fields"]["topic"].pop("withheld")  # extraction_unreadable
        elif roll < 0.008:
            document["policy_version"] = columns["policy_version"] = "extraction-policy/0"
        return {**columns, "result": document}


class _ReportOutputs:
    """The report outputs of one analysed call, shaped like ``persist_analysis`` stores them:
    transcript segments, the summary (``key_points``: insights, then the structured claims),
    AI action items and their evidence spans."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def _grounding(self) -> str:
        statuses, weights = zip(*GROUNDING, strict=True)
        return self.rng.choices(statuses, weights=weights)[0]

    def rows(
        self, *, workspace_id: str, meeting_id: str, revision_id: str, run_id: str, language: str
    ) -> dict[str, list[dict[str, Any]]]:
        rng = self.rng
        segments = [
            {
                "id": str(uuid.uuid4()),
                "workspace_id": workspace_id,
                "meeting_id": meeting_id,
                "revision_id": revision_id,
                "sequence": index,
                "speaker_key": ("agent", "customer")[index % 2],
                "start_ms": index * 20_000,
                "end_ms": index * 20_000 + 19_000,
                "text": f"perf call turn {index}",
                "confidence": 0.9,
                "language": language,
            }
            for index in range(len(TURNS))
        ]
        spans: list[dict[str, Any]] = []

        def cite(
            status: str, finding_type: str, *, index: int | None = None, finding: str | None = None
        ) -> tuple[list[int], list[str]]:
            """Spans for one finding: none when it is insufficient, else 1 or 2."""
            if status == "insufficient":
                return [], []
            turns = sorted(rng.sample(range(len(TURNS)), rng.randint(1, 2)))
            ids = []
            for turn in turns:
                segment = segments[turn]
                ids.append(str(uuid.uuid4()))
                spans.append(
                    {
                        "id": ids[-1],
                        "workspace_id": workspace_id,
                        "meeting_id": meeting_id,
                        "analysis_run_id": run_id,
                        "transcript_revision_id": revision_id,
                        "segment_id": segment["id"],
                        "speaker_key": segment["speaker_key"],
                        "finding_type": finding_type,
                        "finding_id": finding,
                        "finding_index": index,
                        "text_start": 0,
                        "text_end": len(segment["text"]),
                        "start_ms": segment["start_ms"],
                        "end_ms": segment["end_ms"],
                        "confidence": None,
                        "evidence_type": "turn_reference",
                    }
                )
            return turns, ids

        overview = self._grounding()
        cite(overview, "summary")  # the overview: finding_index None
        key_points: list[dict[str, Any]] = []
        for _ in range(rng.randint(2, 3)):
            status = self._grounding()
            turns, ids = cite(status, "insight", index=len(key_points))
            key_points.append(
                {
                    "category": rng.choice(INSIGHT_CATEGORIES),
                    "description": "perf insight",
                    "confidence": round(rng.uniform(0.5, 0.99), 2),
                    "source_turn_indexes": turns,
                    "evidence_ids": ids,
                    "evidence_status": status,
                }
            )
        for name in ("purpose", "resolution", "next_steps"):
            if rng.random() >= 0.85:
                key_points.append(
                    {"section": "summary_field", "category": name, "status": "not_stated"}
                )
                continue
            status = self._grounding()
            turns, ids = cite(status, "summary", index=len(key_points))
            key_points.append(
                {
                    "section": "summary_field",
                    "category": name,
                    "status": "stated",
                    "description": f"perf {name}",
                    "basis": "explicit",
                    "confidence": None,
                    "source_turn_indexes": turns,
                    "evidence_ids": ids,
                    "evidence_status": status,
                }
            )
        for _ in range(rng.choices((0, 1, 2), weights=(50, 35, 15))[0]):
            status = self._grounding()
            turns, ids = cite(status, "insight", index=len(key_points))
            key_points.append(
                {
                    "section": "decision",
                    "category": "decision",
                    "description": "perf decision",
                    "basis": "explicit",
                    "owner": {"status": "not_stated", "value": None},
                    "due_date": {"status": "not_stated", "value": None},
                    "confidence": None,
                    "source_turn_indexes": turns,
                    "evidence_ids": ids,
                    "evidence_status": status,
                }
            )
        for _ in range(rng.choices((0, 1, 2), weights=(50, 35, 15))[0]):
            status = self._grounding()
            turns, ids = cite(status, "insight", index=len(key_points))
            key_points.append(
                {
                    "section": "key_moment",
                    "category": rng.choice(KEY_MOMENT_KINDS),
                    "description": "perf moment",
                    "confidence": None,
                    "source_turn_indexes": turns,
                    "evidence_ids": ids,
                    "evidence_status": status,
                }
            )
        actions = []
        for _ in range(rng.choices((0, 1, 2, 3), weights=(30, 40, 20, 10))[0]):
            action_id = str(uuid.uuid4())
            status = self._grounding()
            turns, _ids = cite(status, "action_item", finding=action_id)
            actions.append(
                {
                    "id": action_id,
                    "workspace_id": workspace_id,
                    "meeting_id": meeting_id,
                    "description": "perf action",
                    "priority": rng.choice(("low", "medium", "high")),
                    "source_segment_id": segments[turns[0]]["id"] if turns else None,
                    "source_timestamp_ms": segments[turns[0]]["start_ms"] if turns else None,
                    "created_by": "ai",
                    "analysis_run_id": run_id,
                    "evidence_status": status,
                }
            )
        summary = {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "meeting_id": meeting_id,
            "overview": "perf overview",
            "decisions": [point for point in key_points if point.get("section") == "decision"],
            "key_points": key_points,
            "topics": list(dict.fromkeys(p["category"] for p in key_points if "section" not in p)),
            "provider": "perf",
            "model": "perf-model",
            "schema_version": 2,
            "analysis_run_id": run_id,
            "evidence_status": overview,
        }
        return {"segments": segments, "summaries": [summary], "actions": actions, "spans": spans}


def _settings(url: str, root: Path, *, warmup: bool = False):
    from backend.config import Settings
    from backend.tests._database import TEST_APP_ROLE
    from backend.tests._transport import APP_REDIS_URL

    return Settings(
        environment="test",
        deployment_mode="cloud",
        database_url=url,
        database_app_role=TEST_APP_ROLE,
        redis_url=APP_REDIS_URL,
        jwt_secret="j" * 48,
        token_pepper="p" * 48,
        auto_create_schema=True,
        tasks_eager=True,
        secure_cookies=False,
        enable_free_demo=True,
        demo_quota_seconds=1800,
        storage_backend="local",
        storage_local_root=str(root / "objects"),
        upload_quarantine_dir=str(root / "quarantine"),
        upload_dir=str(root / "legacy-uploads"),
        enable_phone_auth=True,
        asanak_api_key="test-only-key",
        asanak_sender="TEST",
        enable_google_auth=False,
        cors_origins="http://testserver",
        trusted_hosts="testserver,localhost,127.0.0.1",
        frontend_url="http://testserver/app",
        public_url="http://testserver",
        # HOM-312: the warm-up is off for the measured calls (a request must find the cache it
        # expects); ``--warmup-measure`` turns it on.  Settings ignore a key the code lacks.
        trend_judgment_warmup_enabled=warmup,
        _env_file=None,
    )


def _capture_mail(outbox: list[dict[str, str]]) -> None:
    from backend.routers.v1 import auth as auth_router

    async def capture_verification(user, token, _settings):
        outbox.append({"kind": "verify", "user_id": user.id, "token": token})

    async def capture_reset(user, token, _settings):
        outbox.append({"kind": "reset", "user_id": user.id, "token": token})

    auth_router._send_verification_message = capture_verification
    auth_router._send_reset_message = capture_reset
    auth_router._send_asanak_code = lambda phone, code, _settings: None


def _chunks(rows: list[dict[str, Any]]) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), CHUNK):
        yield rows[start : start + CHUNK]


def _insert_rows(db: Any, model: Any, rows: list[dict[str, Any]]) -> None:
    """``rows`` into ``model``'s table.  On PostgreSQL with ``COPY ... FROM STDIN`` (about 2 to
    3 times faster than an executemany, which took 40 minutes of seeding at 100k): each value
    goes through the column type's own bind processor, and a missing column gets the model's
    Python-side default, as an ORM insert does, so the rows and values are the same.  Rows with
    other keys go in their own ``COPY``.  Other databases: an executemany."""
    if not rows:
        return
    connection = db.connection()
    if connection.dialect.name != "postgresql" or SEED_METHOD == "executemany":
        from sqlalchemy import insert

        for chunk in _chunks(rows):
            db.execute(insert(model), chunk)
        return
    table, dialect = model.__table__, connection.dialect
    raw = connection.connection.driver_connection
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(tuple(row), []).append(row)
    for keys, group in groups.items():
        columns = [
            column
            for column in table.columns
            if column.key in keys or (column.default is not None and not column.default.is_sequence)
        ]
        processors = [column.type._cached_bind_processor(dialect) for column in columns]
        names = ", ".join(f'"{column.name}"' for column in columns)
        with (
            raw.cursor() as cursor,
            cursor.copy(f'COPY "{table.name}" ({names}) FROM STDIN') as copy,
        ):
            for row in group:
                values = []
                for column, process in zip(columns, processors, strict=True):
                    if column.key in row:
                        value = row[column.key]
                    elif column.default.is_callable:
                        value = column.default.arg(None)
                    else:
                        value = column.default.arg
                    values.append(process(value) if process is not None else value)
                copy.write_row(values)


def seed(
    ref: Any,
    week_start: datetime,
    scale: float = 1.0,
    *,
    dataset: str = "realistic",
    summaries: bool = True,
    seed_value: int = SEED,
) -> dict[str, int]:
    """The conversations and voicemails (``scale`` of 100,000 and 2,000), with Core bulk
    inserts as the DBA.  ``dataset``, ``summaries`` and ``seed_value``: see the module
    docstring."""
    from sqlalchemy import text, update

    from backend.database.saas_models import (
        AnalysisExtraction,
        AnalysisRun,
        IngestionRecord,
        IngestionSourceType,
        IngestionStatus,
        Meeting,
        MeetingStatus,
        StageState,
        Team,
        TranscriptRevision,
        TranscriptSource,
        User,
    )
    from backend.tests._database import inspection_session_factory
    from backend.tests.test_issue_trend_service import _extraction_row
    from backend.voicemail.models import VOICEMAIL_MEETING_SOURCE, VoicemailMessage

    realistic = dataset == "realistic"
    extractions = {}
    for letter, result in _extractions().items():
        template = _extraction_row("w", "m", "r", result)
        extractions[letter] = {
            column.key: getattr(template, column.key)
            for column in AnalysisExtraction.__table__.columns
            if column.key not in ("id", "workspace_id", "meeting_id", "analysis_run_id")
            and getattr(template, column.key, None) is not None
        }
    documents = _Documents(random.Random(seed_value)) if realistic else None  # noqa: S311
    start = week_start - WEEKS * timedelta(days=7)
    span = (week_start - start).total_seconds()
    sources = (
        IngestionSourceType.FILE_UPLOAD,
        IngestionSourceType.SIP_VOIP,
        IngestionSourceType.SIP_VOIP,
        IngestionSourceType.API,
    )
    tables: dict[str, list[dict[str, Any]]] = {
        "users": [], "teams": [], "meetings": [], "ingestion": [], "revisions": [],
        "runs": [], "extractions": [], "voicemails": [],
    }  # fmt: skip
    analysed: list[tuple[str, str, str, str, str]] = []  # workspace, meeting, revision, run, lang
    persona_agent = ref.people["agent"].user_id  # GET /trends/me reads its own calls
    for key, place in (("a", ref.a), ("b", ref.b)):
        agents = []
        for team_index in range(TEAMS[key]):
            team_id = str(uuid.uuid4())
            name = f"perf-{key}-{team_index:02d}"
            tables["teams"].append(
                {"id": team_id, "workspace_id": place.workspace_id, "name": name, "slug": name}
            )
            for agent_index in range(AGENTS_PER_TEAM):
                if key == "a" and team_index == 0 and agent_index == 0:
                    agents.append((team_id, persona_agent))
                    continue
                user_id = str(uuid.uuid4())
                tables["users"].append(
                    {
                        "id": user_id,
                        "email": f"{name}-{agent_index}-{user_id[:8]}@example.com",
                        "display_name": f"{name}-{agent_index}",
                    }
                )
                agents.append((team_id, user_id))
        total = round(CONVERSATIONS[key] * scale)
        for n in range(total):
            team_id, agent = agents[n % len(agents)]
            slot = MIX[n % len(MIX)]
            received_at = start + timedelta(seconds=span * (n + 0.5) / total)
            meeting_id = str(uuid.uuid4())
            language = "en" if n % 20 in (3, 11, 17) else "fa"
            status = {"failed": MeetingStatus.FAILED, "queued": MeetingStatus.QUEUED}.get(
                slot, MeetingStatus.COMPLETED
            )
            tables["meetings"].append(
                {
                    "id": meeting_id,
                    "workspace_id": place.workspace_id,
                    "created_by_user_id": place.owner_id,
                    "title": "Perf call",
                    # /aggregates counts meetings by created_at: the call's own time
                    "created_at": received_at,
                    "updated_at": received_at,
                    "status": status,
                    "duration_seconds": 180,
                    "deleted_at": received_at if slot == "deleted" else None,
                    "team_id": team_id,
                    "agent_user_id": agent,
                    "language": language,
                }
            )
            tables["ingestion"].append(
                {
                    "id": str(uuid.uuid4()),
                    "organization_id": place.organization_id,
                    "workspace_id": place.workspace_id,
                    "source_type": sources[n % len(sources)],
                    "source_provider": "perf",
                    "external_event_id": str(uuid.uuid4()),
                    "payload_hash": "0" * 64,
                    "status": IngestionStatus.ACCEPTED,
                    "received_at": received_at,
                    "meeting_id": meeting_id,
                }
            )
            if slot in ("failed", "queued"):
                continue
            revision_id = str(uuid.uuid4())
            segment_count = len(TURNS) if realistic else 40
            tables["revisions"].append(
                {
                    "id": revision_id,
                    "workspace_id": place.workspace_id,
                    "meeting_id": meeting_id,
                    "revision_number": 1,
                    "source": TranscriptSource.ASR,
                    "reason": "seed",
                    "language": language,
                    "segment_count": 0 if slot == "no_transcript" else segment_count,
                    "is_mock": False,
                    "is_current": True,
                }
            )
            if slot in ("no_run", "no_transcript"):
                continue
            run_id = str(uuid.uuid4())
            tables["runs"].append(
                {
                    "id": run_id,
                    "workspace_id": place.workspace_id,
                    "meeting_id": meeting_id,
                    "transcript_revision_id": revision_id,
                    "state": StageState.COMPLETED,
                    "is_mock": False,
                    "is_current": True,
                }
            )
            if slot in extractions:
                document = documents.row() if documents is not None else extractions[slot]
                tables["extractions"].append(
                    {
                        "workspace_id": place.workspace_id,
                        "meeting_id": meeting_id,
                        "analysis_run_id": run_id,
                        **document,
                    }
                )
                analysed.append((place.workspace_id, meeting_id, revision_id, run_id, language))
    a_teams = [row["id"] for row in tables["teams"] if row["workspace_id"] == ref.a.workspace_id]
    voicemails = round(VOICEMAILS * scale)
    for n in range(voicemails):
        meeting_id = str(uuid.uuid4())
        failed = n % 10 == 9
        received_at = start + timedelta(seconds=span * (n + 0.5) / voicemails)
        tables["meetings"].append(
            {
                "id": meeting_id,
                "workspace_id": ref.a.workspace_id,
                "created_by_user_id": ref.a.owner_id,
                "title": "Perf voicemail",
                "created_at": received_at,
                "updated_at": received_at,
                "status": MeetingStatus.FAILED if failed else MeetingStatus.COMPLETED,
                "duration_seconds": 20,
                "team_id": a_teams[n % len(a_teams)],
                "source": VOICEMAIL_MEETING_SOURCE,
            }
        )
        if not failed:
            tables["revisions"].append(
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": ref.a.workspace_id,
                    "meeting_id": meeting_id,
                    "revision_number": 1,
                    "source": TranscriptSource.ASR,
                    "reason": "asr",
                    "language": "fa",
                    "segment_count": 3,
                    "is_mock": False,
                    "is_current": True,
                }
            )
        tables["voicemails"].append(
            {
                "id": str(uuid.uuid4()),
                "workspace_id": ref.a.workspace_id,
                "meeting_id": meeting_id,
                "external_message_id": f"perf-vm-{n}",
                "received_at": received_at,
                "duration_seconds": 20,
            }
        )

    order = (
        ("users", User),
        ("teams", Team),
        ("meetings", Meeting),
        ("ingestion", IngestionRecord),
        ("revisions", TranscriptRevision),
        ("runs", AnalysisRun),
        ("extractions", AnalysisExtraction),
        ("voicemails", VoicemailMessage),
    )
    counts = {name: len(rows) for name, rows in tables.items()}
    with inspection_session_factory()() as db:
        # the reference teams of A hold no conversation here: A compares its 8 perf teams
        reference = [ref.teams[name] for name in ("big", "mid", "small")]
        db.execute(update(Team).where(Team.id.in_(reference)).values(is_active=False))
        for name, model in order:
            began = time.perf_counter()
            _insert_rows(db, model, tables[name])
            db.commit()
            print(f"  seeded {name}: {len(tables[name])} rows in "
                  f"{time.perf_counter() - began:.1f} s", flush=True)  # fmt: skip
        tables.clear()
        if realistic and summaries:
            counts.update(_seed_report_outputs(db, analysed, seed_value))
        db.execute(text("ANALYZE"))
        db.commit()
    return counts


def _seed_report_outputs(db: Any, analysed: list[tuple[str, ...]], seed_value: int) -> dict:
    """Segments, summaries, AI action items and evidence spans of every analysed call, made
    and inserted in chunks (about 12 spans per call: the full lists would not fit in memory at
    100k calls)."""
    from backend.database.saas_models import (
        ActionItem,
        EvidenceSpan,
        MeetingSummary,
        TranscriptSegment,
    )

    outputs = _ReportOutputs(random.Random(seed_value + 1))  # noqa: S311
    models = (
        ("segments", TranscriptSegment),
        ("summaries", MeetingSummary),
        ("actions", ActionItem),
        ("spans", EvidenceSpan),
    )  # the insert order: a span cites a segment
    pending: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in models}
    counts = dict.fromkeys(pending, 0)
    began = time.perf_counter()

    def flush() -> None:
        for name, model in models:
            _insert_rows(db, model, pending[name])
            counts[name] += len(pending[name])
            pending[name].clear()
        db.commit()

    for workspace_id, meeting_id, revision_id, run_id, language in analysed:
        rows = outputs.rows(
            workspace_id=workspace_id,
            meeting_id=meeting_id,
            revision_id=revision_id,
            run_id=run_id,
            language=language,
        )
        for name, _model in models:
            pending[name].extend(rows[name])
        if len(pending["spans"]) >= 4 * CHUNK:
            flush()
    flush()
    print(f"  seeded report outputs: {counts} in {time.perf_counter() - began:.1f} s", flush=True)
    return counts


def _window(week_start: datetime, weeks: int) -> dict[str, str]:
    return {
        "from": (week_start - weeks * timedelta(days=7)).isoformat(),
        "to": week_start.isoformat(),
    }


def _requests(
    ref: Any, week_start: datetime, windows: Iterable[str] = ("12w",)
) -> dict[str, tuple[str, str, str | None, dict]]:
    """route name -> (caller, path, X-Workspace-ID, params), for each window of ``windows``
    (``4w``, ``12w-org``, ``12w``; see the module docstring)."""
    a, org = ref.a.workspace_id, ref.organization_id
    routes: dict[str, tuple[str, str, str | None, dict]] = {}
    for window_name in windows:
        weeks = TARGET_WEEKS if window_name == "4w" else WEEKS
        label = f"{weeks} weeks" + (f", {OVER_TARGET}" if window_name == "12w-org" else "")
        window = _window(week_start, weeks)
        organization = {
            f"GET /organizations/{{id}}/trends/issues ({label})": (
                "owner", f"/api/v1/organizations/{org}/trends/issues", None, window),
            f"GET /organizations/{{id}}/workspaces/{{id}}/trends/issues ({label})": (
                "owner", f"/api/v1/organizations/{org}/workspaces/{a}/trends/issues", None,
                window),
        }  # fmt: skip
        if window_name == "4w":
            organization[f"GET /organizations/{{id}}/aggregates (default, {label})"] = (
                "owner", f"/api/v1/organizations/{org}/aggregates", None, {})  # fmt: skip
        else:
            organization[f"GET /organizations/{{id}}/aggregates since/until ({label})"] = (
                "owner", f"/api/v1/organizations/{org}/aggregates", None,
                {"since": window["from"], "until": window["to"]})  # fmt: skip
        if window_name == "12w-org":
            routes.update(organization)
            continue
        routes.update({
            f"GET /trends/issues ({label})": ("owner", "/api/v1/trends/issues", a, window),
            f"GET /trends/issues (API key, {label})": (
                "api_key", "/api/v1/trends/issues", a, window),
            f"GET /trends/issues grain=week ({label})": (
                "owner", "/api/v1/trends/issues", a, {**window, "grain": "week"}),
            f"GET /trends/teams ({label})": ("owner", "/api/v1/trends/teams", a, window),
            f"GET /trends/me ({label})": ("agent", "/api/v1/trends/me", a, window),
            f"GET /trends/conversations ({label})": (
                "owner", "/api/v1/trends/conversations", a,
                {**window, "trend_view": "issues", "row": ROW, "limit": "100"}),
            **organization,
            f"GET /voicemails/analytics ({label})": (
                "owner", "/api/v1/voicemails/analytics", a,
                {"received_from": window["from"], "received_to": window["to"]}),
            f"GET /admin/coverage ({label})": ("owner", "/api/v1/admin/coverage", a, window),
        })  # fmt: skip
    return routes


class _Statements:
    """Wall time of every statement of one request; optionally ``EXPLAIN ANALYZE`` of the
    statement at ``explain`` on its own connection, right after it ran."""

    def __init__(self, explain: int | None = None) -> None:
        self.explain = explain
        self.timings: list[tuple[float, str]] = []
        self.starts: list[tuple[float, str]] = []  # (perf_counter at start, statement)
        self.plan: str | None = None
        self._began = 0.0

    def before(self, _conn, _cursor, statement, _parameters, _context, _executemany):
        self._began = time.perf_counter()
        self.starts.append((self._began, statement))

    def after(self, _conn, cursor, statement, parameters, _context, _executemany):
        self.timings.append((time.perf_counter() - self._began, statement))
        if self.explain is not None and len(self.timings) - 1 == self.explain:
            explain = cursor.connection.cursor()
            explain.execute("EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters)
            self.plan = "\n".join(row[0] for row in explain.fetchall())
            explain.close()


# --- stage timers (``--stages``) ------------------------------------------------------------

# (module, attribute, label).  The wrapper also replaces every other ``backend`` module's name
# for the same function (``from x import f``).  ``Class.method`` wraps the method on the
# class.  A missing attribute is skipped (n/a).
STAGES = (
    ("backend.services.issue_trend_service", "_population_rows", "population statement P"),
    ("backend.services.issue_trend_service", "judge_population", "judge_population"),
    ("backend.services.issue_trend_service", "judge_conversation", "judge_conversation"),
    ("backend.issue_trends.counting", "SignatureJudge.content", "SignatureJudge.content"),
    ("backend.issue_trends.counting", "SignatureJudge.judge", "SignatureJudge.judge"),
    ("backend.services.coverage_service", "compute_coverage", "compute_coverage"),
    ("backend.services.coverage_service", "compute_coverage_by_team", "compute_coverage_by_team"),
    ("backend.services.coverage_service", "compute_population_coverage", "population coverage"),
    ("backend.services.coverage_service", "_ledger_rows", "coverage ledger statement"),
    ("backend.services.coverage_service", "_load_facts", "coverage fact statements"),
    ("backend.services.coverage_service", "_load_verdicts", "coverage verdicts (U2)"),
    ("backend.services.coverage_service", "_count", "coverage _count"),
    ("backend.services.coverage_service", "unsupported_finding_count",
     "unsupported_finding_count"),
)  # fmt: skip
# The fact statements that serve the ``unsupported_findings`` rule, by their table.
REPORT_TABLES = ("meeting_summaries", "action_items", "evidence_spans")


class _StageTimers:
    """Wall-clock timers around ``STAGES`` for one call, and the time of each coverage fact
    statement from its start to the next statement's start (its rows included)."""

    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self.facts: list[tuple[float, float]] = []  # (start, end) of each _load_facts call
        self.installed: set[str] = set()  # the stages this code has (0.00 when not called)
        self._undo: list[tuple[Any, str, Any]] = []

    def _wrap(self, label: str, function: Any) -> Any:
        @functools.wraps(function)
        def timed(*args: Any, **kwargs: Any) -> Any:
            began = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                ended = time.perf_counter()
                self.seconds[label] = self.seconds.get(label, 0.0) + ended - began
                self.calls[label] = self.calls.get(label, 0) + 1
                if label == "coverage fact statements":
                    self.facts.append((began, ended))

        return timed

    def install(self) -> None:
        import importlib

        for module_name, attribute, label in STAGES:
            module = importlib.import_module(module_name)
            if "." in attribute:
                owner_name, method = attribute.split(".")
                owner = getattr(module, owner_name, None)
                original = getattr(owner, method, None) if owner is not None else None
                if original is not None:
                    setattr(owner, method, self._wrap(label, original))
                    self._undo.append((owner, method, original))
                    self.installed.add(label)
                continue
            original = getattr(module, attribute, None)
            if original is None:
                continue
            wrapper = self._wrap(label, original)
            self.installed.add(label)
            for name, loaded in list(sys.modules.items()):
                if name.startswith("backend") and getattr(loaded, attribute, None) is original:
                    setattr(loaded, attribute, wrapper)
                    self._undo.append((loaded, attribute, original))

    def uninstall(self) -> None:
        for module, attribute, original in reversed(self._undo):
            setattr(module, attribute, original)
        self._undo.clear()

    def report_statements(self, statements: _Statements) -> dict[str, float]:
        """Seconds spent in the coverage fact statements on each of ``REPORT_TABLES``, each
        statement from its start to the next statement's start (or the end of
        ``_load_facts``), so its rows are included."""
        seconds = dict.fromkeys(REPORT_TABLES, 0.0)
        starts = statements.starts
        for index, (began, statement) in enumerate(starts):
            inside = next(((s, e) for s, e in self.facts if s <= began <= e), None)
            # the statement's own table: its first FROM (the U2 revisions statement also
            # reads action items and spans, in joined subqueries after it)
            first = statement.split("FROM ", 1)[1] if "FROM " in statement else ""
            table = next((t for t in REPORT_TABLES if first.startswith(t)), None)
            if inside is None or table is None:
                continue
            following = starts[index + 1][0] if index + 1 < len(starts) else inside[1]
            seconds[table] += min(following, inside[1]) - began
        return seconds


RELOGIN_AFTER = 600.0  # seconds; an access token lives 15 minutes (``access_token_minutes``)
_logged_in: dict[str, float] = {}


def _fresh_session(ref: Any, caller: str) -> None:
    """Sign ``caller`` in again on its first call and when its session is older than
    ``RELOGIN_AFTER``, so a long run never meets an expired access token.  Not timed."""
    from backend.security.context import ACCESS_COOKIE, CSRF_COOKIE, REFRESH_COOKIE
    from backend.services.rate_limit_service import RateLimiter
    from backend.tests.conftest import login_user

    now = time.monotonic()
    if now - _logged_in.get(caller, -math.inf) < RELOGIN_AFTER:  # first use: sign in too
        return
    persona = ref.people[caller]
    RateLimiter.reset_memory()
    login_user(ref.client, {"email": persona.email, "password": "SecurePass123"})
    persona.cookies = {
        name: value
        for name in (ACCESS_COOKIE, REFRESH_COOKIE, CSRF_COOKIE)
        if (value := ref.client.cookies.get(name))
    }
    ref.client.cookies.clear()
    _logged_in[caller] = time.monotonic()


def _call(ref: Any, caller: str, path: str, workspace: str | None, params: dict,
          statements: _Statements | None = None) -> float:  # fmt: skip
    from sqlalchemy import event

    from backend.database.relational import get_engine
    from backend.tests.test_api_keys import _bearer

    if caller != "api_key":
        _fresh_session(ref, caller)
    engine = get_engine()
    if statements is not None:
        event.listen(engine, "before_cursor_execute", statements.before)
        event.listen(engine, "after_cursor_execute", statements.after)
    try:
        began = time.perf_counter()
        if caller == "api_key":
            ref.client.cookies.clear()
            response = ref.client.get(path, params=params, headers=_bearer(ref.reader_key))
        else:
            response = ref.people[caller].get(path, workspace_id=workspace, params=params)
        elapsed = time.perf_counter() - began
    finally:
        if statements is not None:
            event.remove(engine, "before_cursor_execute", statements.before)
            event.remove(engine, "after_cursor_execute", statements.after)
    assert response.status_code == 200, (path, response.status_code, response.text[:500])
    return elapsed


def _percentile(values: list[float], share: float) -> float:
    """Nearest rank."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def _machine() -> str:
    memory = ""
    if platform.system() == "Darwin":
        try:
            raw = subprocess.run(  # noqa: S603 - fixed command, no input
                ["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string", "hw.memsize"],
                capture_output=True, text=True, check=True,
            ).stdout.split("\n")  # fmt: skip
            memory = f"{raw[0]}, {int(raw[1]) / 2**30:.0f} GiB RAM, "
        except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
            memory = ""
    return (
        f"{memory}{os.cpu_count()} CPUs, {platform.platform()}, Python {platform.python_version()}"
    )


def _hotspots(profile: cProfile.Profile, root: Path, count: int = 2) -> list[str]:
    """The top ``count`` backend functions outside the routers by cumulative time, wrappers
    left out (a callee takes 80% or more of the function's time)."""
    stats = pstats.Stats(profile).stats  # (file, line, name) -> (cc, nc, tt, ct, callers)
    backend = root / "backend"
    own = {
        key: value
        for key, value in stats.items()
        if key[0].startswith(str(backend))
        and not any(part in key[0] for part in ("/routers/", "/tests/", "/backend/main.py"))
    }
    into: dict[tuple, float] = {}  # caller -> its largest callee time
    for _callee, (_cc, _nc, _tt, _ct, callers) in own.items():
        for caller, (_c, _n, _t, cumulative) in callers.items():
            into[caller] = max(into.get(caller, 0.0), cumulative)
    ranked = sorted(
        ((value[3], key) for key, value in own.items() if into.get(key, 0.0) < 0.8 * value[3]),
        reverse=True,
    )
    return [
        f"`{name}` ({Path(path).relative_to(root)}:{line}) {cumulative:.1f} s"
        for cumulative, (path, line, name) in ranked[:count]
    ]


def _counted(statements: _Statements) -> int:
    """Statements without the ``set_config`` calls of the row-level-security binding, as the
    CI guard counts them (``test_trend_statement_count.py``)."""
    return sum(1 for _seconds, statement in statements.timings if "set_config" not in statement)


def _uptime() -> str:
    try:
        return subprocess.run(  # noqa: S603 - fixed command, no input
            ["/usr/bin/uptime"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


# --- the warm-up (``--warmup-measure``, HOM-312) --------------------------------------------


def _rss_kib() -> int | None:
    """This process's resident memory now, in KiB (``ps``), or None."""
    try:
        raw = subprocess.run(  # noqa: S603 - fixed command, our own pid
            ["/bin/ps", "-o", "rss=", "-p", str(os.getpid())],
            capture_output=True, text=True, check=True,
        ).stdout  # fmt: skip
        return int(raw.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def _cpu_seconds() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_utime + usage.ru_stime


class _RssPeak:
    """The highest resident memory of this process while running, sampled every 0.25 s."""

    def __init__(self) -> None:
        self.peak = _rss_kib() or 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self._stop.wait(0.25):
            self.peak = max(self.peak, _rss_kib() or 0)

    def __enter__(self) -> _RssPeak:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join()
        self.peak = max(self.peak, _rss_kib() or 0)


def _swap_client(ref: Any, client: Any) -> Iterator[None]:
    """Send every persona's requests through ``client`` while the generator is open."""
    previous = ref.client
    ref.client = client
    for persona in ref.people.values():
        persona.client = client
    _logged_in.clear()
    try:
        yield
    finally:
        ref.client = previous
        for persona in ref.people.values():
            persona.client = previous
        _logged_in.clear()


def _activity_jobs(ref: Any) -> None:
    """One processing job in each workspace (``--warmup-measure``), A the most recent, so the
    warm-up has workspaces to rank (the dataset has no job)."""
    from sqlalchemy import select

    from backend.database.saas_models import (
        Entitlement,
        Meeting,
        ProcessingJob,
        UsageReservation,
    )
    from backend.tests._database import inspection_session_factory

    now = datetime.now(UTC)
    with inspection_session_factory()() as db:
        for place, minutes_ago in ((ref.a, 1), (ref.b, 30)):
            meeting_id = db.scalar(
                select(Meeting.id).where(Meeting.workspace_id == place.workspace_id).limit(1)
            )
            entitlement_id = db.scalar(
                select(Entitlement.id).where(Entitlement.workspace_id == place.workspace_id)
            )
            if entitlement_id is None:
                entitlement = Entitlement(
                    workspace_id=place.workspace_id,
                    metric="audio_seconds",
                    source="perf",
                    limit_value=None,
                    used_value=0,
                    reserved_value=0,
                    period_start=now - timedelta(days=60),
                )
                db.add(entitlement)
                db.flush()
                entitlement_id = entitlement.id
            reservation = UsageReservation(
                workspace_id=place.workspace_id,
                entitlement_id=entitlement_id,
                meeting_id=meeting_id,
                idempotency_key=f"perf-warmup:{uuid.uuid4()}",
                reserved_value=30,
            )
            db.add(reservation)
            db.flush()
            db.add(
                ProcessingJob(
                    workspace_id=place.workspace_id,
                    meeting_id=meeting_id,
                    reservation_id=reservation.id,
                    idempotency_key=f"perf-warmup:{uuid.uuid4()}",
                    stage="completed",
                    attempt_count=1,
                    max_attempts=3,
                    created_at=now - timedelta(minutes=minutes_ago),
                )
            )
        db.commit()


def _expired_calls(ref: Any, app: Any, request: tuple) -> list[float] | None:
    """``EXPIRED_CALLS`` calls, each with every cached verdict past the time limit (the
    cache clock moved forward before each call), or ``None`` without verdicts."""
    cache = getattr(app.state, "trend_judgment_cache", None)
    ttl = getattr(cache, "verdict_ttl_seconds", None)
    if cache is None or not ttl:
        return None
    clock, offset = cache.clock, [0.0]
    cache.clock = lambda: clock() + offset[0]
    try:
        times = []
        for _ in range(EXPIRED_CALLS):
            offset[0] += ttl + 1
            times.append(_call(ref, *request))
        return times
    finally:
        cache.clock = lambda: clock() + offset[0]  # entries keep their (moved) times


def _measure_warmup(
    ref: Any, url: str, root: Path, requests: dict, limit_seconds: float
) -> dict[str, Any]:
    """Start the app with the warm-up on, twice (see the module docstring)."""
    from contextlib import contextmanager

    from fastapi.testclient import TestClient

    from backend.main import create_app

    swap = contextmanager(_swap_client)
    report: dict[str, Any] = {}
    # 1. no request: time to fully warm, process CPU, peak memory added
    app = create_app(_settings(url, root, warmup=True))
    rss_before, cpu_before, began = _rss_kib(), _cpu_seconds(), time.perf_counter()
    with _RssPeak() as peak, TestClient(app):
        warmup = getattr(app.state, "trend_judgment_warmup", None)
        if warmup is None:
            return {"available": False}
        started = time.perf_counter() - began
        finished = warmup.wait(limit_seconds)
        report["idle"] = {
            "finished": finished,
            "seconds_to_start": started,
            "seconds_to_warm": time.perf_counter() - began,
            "cpu_seconds": _cpu_seconds() - cpu_before,
            "rss_before_kib": rss_before,
            "rss_peak_added_kib": peak.peak - (rss_before or 0),
            "status": warmup.status(),
            "cache": _cache_stats(app),
        }
    # 2. requests while the warm-up runs
    app = create_app(_settings(url, root, warmup=True))
    names = [name for name in requests if name.endswith(f"{TARGET_WEEKS} weeks)")] or list(requests)
    times: list[float] = []
    with TestClient(app) as client, swap(ref, client):
        warmup = app.state.trend_judgment_warmup
        began = time.perf_counter()
        index = 0
        while not warmup.wait(0) and time.perf_counter() - began < limit_seconds:
            caller, path, workspace, params = requests[names[index % len(names)]]
            times.append(_call(ref, caller, path, workspace, params))
            index += 1
        report["busy"] = {
            "requests": len(times),
            "p50_ms": _percentile(times, 0.5) * 1000 if times else None,
            "p95_ms": _percentile(times, 0.95) * 1000 if times else None,
            "max_ms": max(times) * 1000 if times else None,
            "seconds_to_warm": time.perf_counter() - began,
            "status": warmup.status(),
        }
    report["available"] = True
    return report


def _cache_stats(app: Any) -> dict[str, int] | None:
    cache = getattr(app.state, "trend_judgment_cache", None)
    return dict(cache.stats()) if cache is not None else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", default=DEFAULT_URL)
    parser.add_argument("--conversations", type=int, default=100_000)
    parser.add_argument("--dataset", choices=("realistic", "legacy"), default="realistic")
    parser.add_argument("--no-summaries", action="store_true",
                        help="realistic documents without the report outputs")  # fmt: skip
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--windows", default="4w,12w-org",
                        help="comma-separated: 4w, 12w-org, 12w")  # fmt: skip
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--calls", type=int, default=50)
    parser.add_argument("--slow-calls", type=int, default=5)
    parser.add_argument("--slow-seconds", type=float, default=10.0)
    parser.add_argument("--stages", action="store_true")
    parser.add_argument("--warmup-measure", action="store_true")
    parser.add_argument("--warmup-limit", type=float, default=900.0,
                        help="seconds to wait for the warm-up, each start")  # fmt: skip
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--out", type=Path, default=None, help="also write the results as JSON")
    parser.add_argument(
        "--routes", default=None, help="measure only the routes whose name contains this text"
    )
    args = parser.parse_args()
    scale = args.conversations / 100_000
    windows = [window.strip() for window in args.windows.split(",") if window.strip()]
    load = _uptime()
    print(f"load at start: {load}", flush=True)

    os.environ["TEST_DATABASE_URL"] = args.database_url  # the DBA engine of the seed helpers
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text

    from backend.database.relational import reset_database_state
    from backend.main import create_app
    from backend.services.role_trend_service import current_week_start
    from backend.tests._database import reset_postgres_schema
    from backend.tests._trend_reference import build_reference

    engine = create_engine(args.database_url)
    with engine.connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar()
    engine.dispose()
    reset_postgres_schema(args.database_url)
    outbox: list[dict[str, str]] = []
    _capture_mail(outbox)
    root = TemporaryDirectory()
    app = create_app(_settings(args.database_url, Path(root.name)))
    source_root = Path(__file__).resolve().parents[2]
    results: list[dict[str, Any]] = []
    slowest: tuple[float, str, int, str] | None = None  # seconds, route, index, statement
    plan = None
    warmup_report: dict[str, Any] | None = None
    with TestClient(app) as client:
        print("seeding", flush=True)
        began = time.perf_counter()
        ref = build_reference(client, outbox, conversations=False)
        week_start = current_week_start(datetime.now(UTC))
        counts = seed(
            ref,
            week_start,
            scale,
            dataset=args.dataset,
            summaries=not args.no_summaries,
            seed_value=args.seed,
        )
        seeded = time.perf_counter() - began
        all_requests = _requests(ref, week_start, windows)
        requests = {
            name: request
            for name, request in all_requests.items()
            if args.routes is None or args.routes in name
        }
        cache = getattr(app.state, "trend_judgment_cache", None)
        for name, (caller, path, workspace, params) in requests.items():
            if cache is not None:
                cache.clear()  # the first call is the route's cold first request
            warm = [_call(ref, caller, path, workspace, params) for _ in range(args.warmup)]
            calls = args.slow_calls if warm[-1] >= args.slow_seconds else args.calls
            print(f"  {name}: warm-up {', '.join(f'{t * 1000:.0f}' for t in warm)} ms, "
                  f"{calls} calls", flush=True)  # fmt: skip
            times, sql = [], []
            for _ in range(calls):
                statements = _Statements()
                times.append(_call(ref, caller, path, workspace, params, statements))
                sql.append(sum(t for t, _ in statements.timings))
                for index, (seconds, statement) in enumerate(statements.timings):
                    if slowest is None or seconds > slowest[0]:
                        slowest = (seconds, name, index, statement)
            result: dict[str, Any] = {
                "route": name,
                "n": calls,
                "first_ms": warm[0] * 1000,
                "p50_ms": _percentile(times, 0.5) * 1000,
                "p95_ms": _percentile(times, 0.95) * 1000,
                "statements": _counted(statements),
                "sql_ms": statistics.median(sql) * 1000,
                "cache": _cache_stats(app),
            }
            expired = _expired_calls(ref, app, (caller, path, workspace, params))
            result["expired_ms"] = statistics.median(expired) * 1000 if expired else None
            if args.stages:
                timers, staged = _StageTimers(), _Statements()
                timers.install()
                try:
                    elapsed = _call(ref, caller, path, workspace, params, staged)
                finally:
                    timers.uninstall()
                by_table = timers.report_statements(staged)
                report_seconds = sum(by_table.values())
                result["stages"] = {
                    "request_s": elapsed,
                    "seconds": timers.seconds,
                    "calls": timers.calls,
                    "installed": sorted(timers.installed),
                    "report_statements_by_table_s": by_table,
                    "report_statements_s": report_seconds,
                    "unsupported_total_s": report_seconds
                    + timers.seconds.get("unsupported_finding_count", 0.0),
                }
            if args.profile:
                profiled = _Statements()
                profile = cProfile.Profile()
                profile.enable()
                elapsed = _call(ref, caller, path, workspace, params, profiled)
                profile.disable()
                result["profile"] = {
                    "statements": _counted(profiled),
                    "sql_ms": sum(t for t, _ in profiled.timings) * 1000,
                    "request_ms": elapsed * 1000,
                    "hotspots": _hotspots(profile, source_root),
                }
            results.append(result)
            print(f"  {name}: n {calls}, first {result['first_ms']:.0f} ms, "
                  f"p50 {result['p50_ms']:.0f} ms, p95 {result['p95_ms']:.0f} ms",
                  flush=True)  # fmt: skip
        if slowest is not None:
            caller, path, workspace, params = requests[slowest[1]]
            explained = _Statements(explain=slowest[2])
            _call(ref, caller, path, workspace, params, explained)
            plan = explained.plan
    if args.warmup_measure:
        print("warm-up measurement", flush=True)
        _activity_jobs(ref)
        warmup_report = _measure_warmup(
            ref, args.database_url, Path(root.name), all_requests, args.warmup_limit
        )
    reset_database_state()
    root.cleanup()

    summary = {
        "dataset": {
            "name": args.dataset,
            "summaries": args.dataset == "realistic" and not args.no_summaries,
            "seed": args.seed,
            "conversations": round(sum(CONVERSATIONS.values()) * scale),
            "workspace_a": round(CONVERSATIONS["a"] * scale),
            "workspace_b": round(CONVERSATIONS["b"] * scale),
            "weeks": WEEKS,
            "teams": sum(TEAMS.values()),
            "agents": sum(TEAMS.values()) * AGENTS_PER_TEAM,
            "voicemails": round(VOICEMAILS * scale),
            "rows": counts,
            "seed_seconds": round(seeded),
        },
        "postgres": version,
        "machine": _machine(),
        "load_at_start": load,
        "load_at_end": _uptime(),
        "rule": {
            "windows": windows,
            "warmup": args.warmup,
            "calls": args.calls,
            "slow_calls": args.slow_calls,
            "slow_seconds": args.slow_seconds,
        },
        "results": results,
        "warmup": warmup_report,
        "slowest_statement": None
        if slowest is None
        else {"route": slowest[1], "ms": slowest[0] * 1000, "sql": slowest[3], "plan": plan},
    }
    if args.out is not None:
        args.out.write_text(json.dumps(summary, indent=1, ensure_ascii=False, default=str))
    print()
    print(json.dumps({k: v for k, v in summary.items() if k not in ("results", "warmup")},
                     indent=1, default=str)[:4000])  # fmt: skip
    print()
    print(f"load at end: {summary['load_at_end']}")
    print()
    print("| Route | n | first ms | p50 ms | p95 ms | expired ms | statements | SQL ms |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in results:
        p95 = f"{r['p95_ms']:.0f}" + (" (max)" if math.ceil(0.95 * r["n"]) == r["n"] else "")
        expired = "n/a" if r["expired_ms"] is None else f"{r['expired_ms']:.0f}"
        print(f"| `{r['route']}` | {r['n']} | {r['first_ms']:.0f} | {r['p50_ms']:.0f} | {p95} | "
              f"{expired} | {r['statements']} | {r['sql_ms']:.0f} |")  # fmt: skip
    if args.stages:
        print()
        print("| Route | request s | P | judge_population | judge_conversation (calls) | "
              "SignatureJudge.content (calls) | SignatureJudge.judge | coverage | "
              "fact statements | verdicts (U2) | report statements | "
              "unsupported_finding_count | unsupported total |")  # fmt: skip
        print("| --- |" + " ---: |" * 13)
        for r in results:
            stages = r["stages"]
            seconds, calls = stages["seconds"], stages["calls"]

            installed = set(stages.get("installed", seconds))

            def cell(label: str, seconds=seconds, installed=installed) -> str:
                value = seconds.get(label, 0.0 if label in installed else None)
                return "n/a" if value is None else f"{value:.2f}"

            coverage = sum(
                seconds.get(label, 0.0)
                for label in ("compute_coverage", "compute_coverage_by_team", "population coverage")
            )

            def counted(label: str, calls=calls) -> str:
                return cell(label) + (f" ({calls[label]})" if label in calls else "")

            print(f"| `{r['route']}` | {stages['request_s']:.2f} | "
                  f"{cell('population statement P')} | {cell('judge_population')} | "
                  f"{counted('judge_conversation')} | {counted('SignatureJudge.content')} | "
                  f"{cell('SignatureJudge.judge')} | {coverage:.2f} | "
                  f"{cell('coverage fact statements')} | {cell('coverage verdicts (U2)')} | "
                  f"{stages['report_statements_s']:.2f} | {cell('unsupported_finding_count')} | "
                  f"{stages['unsupported_total_s']:.2f} |")  # fmt: skip
        print()
        print("| Route | " + " | ".join(f"{table} s" for table in REPORT_TABLES) + " |")
        print("| --- |" + " ---: |" * len(REPORT_TABLES))
        for r in results:
            by_table = r["stages"]["report_statements_by_table_s"]
            cells = " | ".join(f"{by_table[table]:.2f}" for table in REPORT_TABLES)
            print(f"| `{r['route']}` | {cells} |")
    if args.warmup_measure:
        print()
        if not warmup_report or not warmup_report.get("available"):
            print("warm-up: not available on this code (no app.state.trend_judgment_warmup)")
        else:
            print("warm-up:", json.dumps(warmup_report, indent=1, default=str))


if __name__ == "__main__":
    main()
