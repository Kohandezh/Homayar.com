"""HOM-84 (WP15-T06, F61): measured latency of the trend routes on 100,000 conversations.

Not a test: pytest never collects this file, and no test asserts a time.  Run it from
``Homayar/`` against the disposable PostgreSQL of the measurement (its schema is dropped and
created again, then seeded):

    uv run python -m backend.tests.perf_trend_routes --conversations 100000 --profile
    uv run python -m backend.tests.perf_trend_routes --conversations 10000 --calls 20 \
        --slow-calls 20

Dataset (deterministic, no randomness), for ``--conversations 100000``:

* One organization with two workspaces.  Workspace A: 8 teams of 10 agents, 80,000
  conversations.  Workspace B: 2 teams of 10 agents, 20,000 conversations.  Spread evenly over
  the 12 closed weeks before this week (Saturday 00:00 Asia/Tehran), one conversation per
  agent in turn.  ``--conversations 10000`` keeps the teams, agents and weeks and seeds one
  tenth of the conversations and voicemails (fewer conversations per agent).
* Per 100 conversations: 20 complaint (refund, escalated), 24 billing (invoice), 5 complaint
  with an abstained topic, 10 support request (technical fault, outcome ``resolved``, a
  critical label: pending review), 15 purchase inquiry (pricing, follow-up), 15 complaint
  (delivery, unresolved), and the processing gaps 4 without an analysis run, 3 failed,
  2 queued, 1 with an empty transcript, 1 soft-deleted.
* Languages fa (17 of 20) and en; sources file_upload, sip_voip and api.
* 2,000 voicemails in workspace A (no ledger row), of which 1 in 10 failed.
* Every meeting's ``created_at`` is its conversation's (or voicemail's) received time, so
  ``/aggregates``, which counts meetings by ``created_at``, reads the same 12 weeks as the other
  routes.  No other measured route reads ``Meeting.created_at``.
* ``/aggregates`` is measured twice: with no window (its metadata reads the default 4 closed
  weeks) and with ``since``/``until`` on the same 12 closed weeks as every other route.
* The reference personas of ``_trend_reference`` (no reference conversations; its three
  teams in A are made inactive).  The agent persona is the first agent of A's first team.

Load: sequential, one ``TestClient`` in this one process, one request at a time, no
concurrent client and no background job of the app.  Other work on the same machine (tests,
other agents) may have run at the same time; the machine load (``uptime``) is printed at the
start.  Each route is called through the real auth path (a signed-in session; the API-key row
uses a ``meeting:read`` key), as the caller that sees the most: the owner of workspace A, who
is also the organization owner (``/trends/me``: the agent persona, its own calls).

The number of calls (HOM-84 leader decision on D3): ``--warmup`` (default 1) warm-up calls per
route; then a route whose last warm-up call took ``--slow-seconds`` (default 10) or more gets
``--slow-calls`` (default 5) measured calls, every other route ``--calls`` (default 50).  p50
and p95 are nearest-rank percentiles (the value at rank ``ceil(q * n)`` of the sorted times)
of the wall time of the whole request in the client; with n = 5, p95 is the maximum.  ``SQL
ms`` is the median over the measured calls of the time spent in database statements
(``before_cursor_execute`` to ``after_cursor_execute``, the ``set_config`` calls of the
row-level-security binding included); ``statements`` leaves those calls out, as the CI guard.

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
import json
import math
import os
import platform
import pstats
import statistics
import subprocess
import time
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

DEFAULT_URL = "postgresql+psycopg://homayar_test:local-only@127.0.0.1:4981/homayar_test"
WEEKS = 12
CONVERSATIONS = {"a": 80_000, "b": 20_000}
TEAMS = {"a": 8, "b": 2}
AGENTS_PER_TEAM = 10
VOICEMAILS = 2_000
CHUNK = 5_000
ROW = "issue:intent_report_dissatisfaction"

# One slot per conversation of 100, in a fixed order: the extraction letter or the gap.
MIX = (
    ["C"] * 20 + ["B"] * 24 + ["V"] * 5 + ["S"] * 10 + ["P"] * 15 + ["D"] * 15
    + ["no_run"] * 4 + ["failed"] * 3 + ["queued"] * 2 + ["no_transcript"] + ["deleted"]
)  # fmt: skip
assert len(MIX) == 100


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


def _settings(url: str, root: Path):
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


def seed(ref: Any, week_start: datetime, scale: float = 1.0) -> dict[str, int]:
    """The conversations and voicemails (``scale`` of 100,000 and 2,000), with Core bulk
    inserts as the DBA."""
    from sqlalchemy import insert, text, update

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

    extractions = {}
    for letter, result in _extractions().items():
        template = _extraction_row("w", "m", "r", result)
        extractions[letter] = {
            column.key: getattr(template, column.key)
            for column in AnalysisExtraction.__table__.columns
            if column.key not in ("id", "workspace_id", "meeting_id", "analysis_run_id")
            and getattr(template, column.key, None) is not None
        }
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
            tables["revisions"].append(
                {
                    "id": revision_id,
                    "workspace_id": place.workspace_id,
                    "meeting_id": meeting_id,
                    "revision_number": 1,
                    "source": TranscriptSource.ASR,
                    "reason": "seed",
                    "language": language,
                    "segment_count": 0 if slot == "no_transcript" else 40,
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
                tables["extractions"].append(
                    {
                        "workspace_id": place.workspace_id,
                        "meeting_id": meeting_id,
                        "analysis_run_id": run_id,
                        **extractions[slot],
                    }
                )
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
    with inspection_session_factory()() as db:
        # the reference teams of A hold no conversation here: A compares its 8 perf teams
        reference = [ref.teams[name] for name in ("big", "mid", "small")]
        db.execute(update(Team).where(Team.id.in_(reference)).values(is_active=False))
        for name, model in order:
            began = time.perf_counter()
            for chunk in _chunks(tables[name]):
                db.execute(insert(model), chunk)
            db.commit()
            print(f"  seeded {name}: {len(tables[name])} rows in "
                  f"{time.perf_counter() - began:.1f} s", flush=True)  # fmt: skip
        db.execute(text("ANALYZE"))
        db.commit()
    return {name: len(rows) for name, rows in tables.items()}


def _requests(ref: Any, week_start: datetime) -> dict[str, tuple[str, str, str | None, dict]]:
    a, org = ref.a.workspace_id, ref.organization_id
    window = {
        "from": (week_start - WEEKS * timedelta(days=7)).isoformat(),
        "to": week_start.isoformat(),
    }
    return {
        "GET /trends/issues": ("owner", "/api/v1/trends/issues", a, window),
        "GET /trends/issues (API key)": ("api_key", "/api/v1/trends/issues", a, window),
        "GET /trends/issues grain=week": (
            "owner", "/api/v1/trends/issues", a, {**window, "grain": "week"}),
        "GET /trends/teams": ("owner", "/api/v1/trends/teams", a, window),
        "GET /trends/me": ("agent", "/api/v1/trends/me", a, window),
        "GET /trends/conversations": ("owner", "/api/v1/trends/conversations", a,
                                      {**window, "trend_view": "issues", "row": ROW,
                                       "limit": "100"}),
        "GET /organizations/{id}/trends/issues": (
            "owner", f"/api/v1/organizations/{org}/trends/issues", None, window),
        "GET /organizations/{id}/workspaces/{id}/trends/issues": (
            "owner", f"/api/v1/organizations/{org}/workspaces/{a}/trends/issues", None, window),
        "GET /organizations/{id}/aggregates": (
            "owner", f"/api/v1/organizations/{org}/aggregates", None, {}),
        "GET /organizations/{id}/aggregates since/until 12 weeks": (
            "owner", f"/api/v1/organizations/{org}/aggregates", None,
            {"since": window["from"], "until": window["to"]}),
        "GET /voicemails/analytics": ("owner", "/api/v1/voicemails/analytics", a,
                                      {"received_from": window["from"],
                                       "received_to": window["to"]}),
        "GET /admin/coverage": ("owner", "/api/v1/admin/coverage", a, window),
    }  # fmt: skip


class _Statements:
    """Wall time of every statement of one request; optionally ``EXPLAIN ANALYZE`` of the
    statement at ``explain`` on its own connection, right after it ran."""

    def __init__(self, explain: int | None = None) -> None:
        self.explain = explain
        self.timings: list[tuple[float, str]] = []
        self.plan: str | None = None
        self._began = 0.0

    def before(self, _conn, _cursor, _statement, _parameters, _context, _executemany):
        self._began = time.perf_counter()

    def after(self, _conn, cursor, statement, parameters, _context, _executemany):
        self.timings.append((time.perf_counter() - self._began, statement))
        if self.explain is not None and len(self.timings) - 1 == self.explain:
            explain = cursor.connection.cursor()
            explain.execute("EXPLAIN (ANALYZE, BUFFERS) " + statement, parameters)
            self.plan = "\n".join(row[0] for row in explain.fetchall())
            explain.close()


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--database-url", default=DEFAULT_URL)
    parser.add_argument("--conversations", type=int, default=100_000)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--calls", type=int, default=50)
    parser.add_argument("--slow-calls", type=int, default=5)
    parser.add_argument("--slow-seconds", type=float, default=10.0)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument("--out", type=Path, default=None, help="also write the results as JSON")
    parser.add_argument(
        "--routes", default=None, help="measure only the routes whose name contains this text"
    )
    args = parser.parse_args()
    scale = args.conversations / 100_000
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
    with TestClient(app) as client:
        print("seeding", flush=True)
        began = time.perf_counter()
        ref = build_reference(client, outbox, conversations=False)
        week_start = current_week_start(datetime.now(UTC))
        counts = seed(ref, week_start, scale)
        seeded = time.perf_counter() - began
        requests = {
            name: request
            for name, request in _requests(ref, week_start).items()
            if args.routes is None or args.routes in name
        }
        for name, (caller, path, workspace, params) in requests.items():
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
            result = {
                "route": name,
                "n": calls,
                "p50_ms": _percentile(times, 0.5) * 1000,
                "p95_ms": _percentile(times, 0.95) * 1000,
                "statements": _counted(statements),
                "sql_ms": statistics.median(sql) * 1000,
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
            print(f"  {name}: n {calls}, p50 {result['p50_ms']:.0f} ms, "
                  f"p95 {result['p95_ms']:.0f} ms", flush=True)  # fmt: skip
        if slowest is not None:
            caller, path, workspace, params = requests[slowest[1]]
            explained = _Statements(explain=slowest[2])
            _call(ref, caller, path, workspace, params, explained)
            plan = explained.plan
    reset_database_state()
    root.cleanup()

    summary = {
        "dataset": {
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
        "rule": {
            "warmup": args.warmup,
            "calls": args.calls,
            "slow_calls": args.slow_calls,
            "slow_seconds": args.slow_seconds,
        },
        "results": results,
        "slowest_statement": None
        if slowest is None
        else {"route": slowest[1], "ms": slowest[0] * 1000, "sql": slowest[3], "plan": plan},
    }
    if args.out is not None:
        args.out.write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    print()
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=1)[:4000])
    print()
    print("| Route | n | p50 ms | p95 ms | statements | SQL ms |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for r in results:
        p95 = f"{r['p95_ms']:.0f}" + (" (max)" if math.ceil(0.95 * r["n"]) == r["n"] else "")
        print(f"| `{r['route']}` | {r['n']} | {r['p50_ms']:.0f} | {p95} | "
              f"{r['statements']} | {r['sql_ms']:.0f} |")  # fmt: skip


if __name__ == "__main__":
    main()
