from __future__ import annotations

import dataclasses
import inspect
import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import Session

import backend
from backend.copilot_intents import CallerScope, IntentRequest, load_catalogue, route_request
from backend.copilot_tools.registry import (
    DEFAULT_LIMITS,
    LIMIT_CAPS,
    REGISTRY_DIGEST,
    TOOLS,
    TOOLS_VERSION,
    ToolLimits,
    ToolSpec,
    tool_schemas,
)
from backend.copilot_tools.results import AbstainReason, ParamDefect, ToolResult
from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    ActionItem,
    ActionStatus,
    ApiKey,
    Meeting,
    MeetingStatus,
    Membership,
    ScorecardResultRecord,
    ScorecardVersion,
    Team,
    TranscriptRevision,
    TranscriptSource,
    User,
    Workspace,
    WorkspaceRole,
)
from backend.database.tenant_context import bind_tenant_context, bind_user_context
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    SCOPE_KEYS,
    UNSUPPORTED_KEYS,
    load_definition,
    trend_provenance,
    validate_filters,
)
from backend.security.authorization import Permission, RecordAccess
from backend.security.context import Actor, WorkspaceContext
from backend.services import copilot_tool_service
from backend.services.copilot_tool_service import run_tool
from backend.services.issue_trend_service import compute_issue_trends
from backend.tests._database import TEST_APP_ROLE, inspection_session_factory
from backend.tests.test_coverage import Tenant
from backend.tests.test_issue_trend_service import add_team, add_user, seed
from backend.tests.test_issue_trends_counting import ok, stored

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
FROM = datetime(2026, 9, 30, 20, 30, tzinfo=UTC)
TO = datetime(2026, 10, 2, 20, 30, tzinfo=UTC)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)
TEN_MINUTES = timedelta(minutes=10)
FROM_LOCAL = "2026-10-01T00:00:00+03:30"
MID_LOCAL = "2026-10-02T00:00:00+03:30"
TO_LOCAL = "2026-10-03T00:00:00+03:30"
FROM_UTC = "2026-09-30T20:30:00+00:00"
TO_UTC = "2026-10-02T20:30:00+00:00"
WINDOW = {"from": FROM_LOCAL, "to": TO_LOCAL}
TREND_WINDOW = {"from": FROM_LOCAL, "to": MID_LOCAL}
DUE_BEFORE = "2026-10-15T00:00:00+00:00"
UPPER_ID = "7D9E2C1A-0B4F-4E8A-9C3D-5F6A7B8C9D0E"
LOWER_ID = "7d9e2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"

TOOL_NAMES = (
    "action_items_due",
    "conversation_count",
    "issue_aggregate",
    "scorecard_summary",
    "top_issues",
)
RESULT_KEYS = {
    "tools_version",
    "tool",
    "status",
    "reason",
    "detail",
    "key",
    "filters",
    "scope",
    "facts",
    "provenance",
    "limits",
}
FACT_KEYS = {
    "name",
    "group",
    "value",
    "parts",
    "citations",
    "citations_total",
    "citations_complete",
    "citations_withheld",
}
ABSTAIN_REASONS = {
    "tool_unknown",
    "permission_denied",
    "params_invalid",
    "scope_not_allowed",
    "unsupported_filter",
    "individual_ranking_not_supported",
    "intent_not_supported",
    "row_limit_exceeded",
    "time_limit_exceeded",
    "below_minimum_sample",
    "not_computable",
    "withheld",
    "out_of_scope",
    "invalid_request",
    "outside_caller_scope",
    "causal_not_supported",
    "forecast_not_supported",
}
PARAM_DEFECTS = {
    "params_not_mapping",
    "param_unknown",
    "param_missing",
    "param_bad_type",
    "param_bad_value",
}
PARAMS = {
    "conversation_count": {"from", "to", "team_id", "agent_user_id", "group_by"},
    "action_items_due": {
        "due_before",
        "due_after",
        "team_id",
        "status",
        "group_by",
        "assignee_user_id",
    },
    "scorecard_summary": {"scorecard_version_id", "from", "to", "team_id", "agent_user_id"},
    "issue_aggregate": {"intent", "params"},
    "top_issues": {
        "family",
        "limit",
        "from",
        "to",
        "team_id",
        "agent_user_id",
        "language",
        "source_type",
    },
}
REQUIRED = {
    "conversation_count": set(),
    "action_items_due": {"due_before"},
    "scorecard_summary": {"scorecard_version_id"},
    "issue_aggregate": {"intent", "params"},
    "top_issues": {"family"},
}
PERMISSIONS = {
    "conversation_count": ("meeting:read",),
    "action_items_due": ("meeting:read",),
    "scorecard_summary": ("meeting:read", "scorecard:read"),
    "issue_aggregate": ("meeting:read",),
    "top_issues": ("meeting:read",),
}
RANKING_KEYS = (None, "group_by")
READ_ONLY = re.compile(
    r"\s*(SELECT|WITH|SAVEPOINT|RELEASE SAVEPOINT|ROLLBACK TO SAVEPOINT)\b", re.IGNORECASE
)

DEFINITION = load_definition("trends", 1)
COMPLAINT = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"), outcome=ok("escalated"))
INVOICE_COMPLAINT = stored(intent=ok("report_dissatisfaction"), topic=ok("invoice"))
BILLING = stored(intent=ok("resolve_billing"), topic=ok("invoice"))
UNDECIDED = stored(intent=ok("report_dissatisfaction"), topic="insufficient_evidence")
RELEASED_RATE = {"status": "released", "reason": None, "kind": "rate", "masked": []}

SCORES = (
    [("passed", 100, 90, 0)] * 20
    + [("failed", 100, 40, 0)] * 6
    + [("failed", 80, 50, 0)] * 3
    + [("not_applicable", 0, 0, 0)]
    + [("insufficient_evidence", 90, 30, 60)]
)
OUTCOME_REASONS = {
    "passed": None,
    "failed": "below_threshold",
    "not_applicable": "no_applicable_criteria",
    "insufficient_evidence": "evidence_missing",
}


def new_id() -> str:
    return str(uuid.uuid4())


def limits_json(value: ToolLimits) -> dict[str, int]:
    return {
        "max_rows": value.max_rows,
        "max_citations": value.max_citations,
        "statement_timeout_ms": value.statement_timeout_ms,
        "time_budget_ms": value.time_budget_ms,
    }


def limits(**changes: Any) -> ToolLimits:
    return ToolLimits(**{**limits_json(DEFAULT_LIMITS), **changes})


def dumped(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True)


def caller(
    tenant: Tenant,
    *,
    user_id: str | None = None,
    role: WorkspaceRole = WorkspaceRole.OWNER,
    supervised: tuple[str, ...] | None = None,
    api_scopes: list[Permission] | None = None,
) -> WorkspaceContext:
    with inspection_session_factory()() as db:
        workspace = db.get(Workspace, tenant.workspace_id)
        user = db.get(User, user_id or tenant.owner_id)
        assert workspace is not None and user is not None
        db.expunge_all()
    key = None
    if api_scopes is not None:
        key = ApiKey(
            workspace_id=workspace.id,
            created_by_user_id=user.id,
            name="oracle",
            key_prefix="oracle",
            key_hash=uuid.uuid4().hex * 2,
            scopes=[scope.value for scope in api_scopes],
        )
    access = RecordAccess(
        user_id=user.id,
        unrestricted=supervised is None,
        supervised_team_ids=frozenset(supervised or ()),
        agent_team_ids=frozenset(),
    )
    return WorkspaceContext(
        actor=Actor(
            user=user,
            session=None,
            token_source="api_key" if key is not None else "bearer",
            api_key=key,
        ),
        workspace=workspace,
        membership=Membership(
            workspace_id=workspace.id, user_id=user.id, role=role, is_active=True
        ),
        record_access=access,
    )


@contextmanager
def bound(workspace_id: str, organization_id: str, user_id: str) -> Iterator[Session]:
    db = get_session_factory()()
    try:
        bind_user_context(db, user_id)
        bind_tenant_context(db, workspace_id=workspace_id, organization_id=organization_id)
        yield db
    finally:
        db.rollback()
        db.close()


@contextmanager
def session_for(context: WorkspaceContext) -> Iterator[Session]:
    with bound(
        context.workspace.id, context.workspace.organization_id, context.actor.user.id
    ) as db:
        yield db


@contextmanager
def watched(db: Session) -> Iterator[SimpleNamespace]:
    engine = db.get_bind()
    seen = SimpleNamespace(statements=[], ends=[])

    def statement(_conn, _cursor, sql, _parameters, _context, _many) -> None:
        seen.statements.append(sql)

    def commit(_conn) -> None:
        seen.ends.append("commit")

    def rollback(_conn) -> None:
        seen.ends.append("rollback")

    event.listen(engine, "before_cursor_execute", statement)
    event.listen(engine, "commit", commit)
    event.listen(engine, "rollback", rollback)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", statement)
        event.remove(engine, "commit", commit)
        event.remove(engine, "rollback", rollback)


def run(
    db: Session,
    context: WorkspaceContext,
    name: object,
    params: object,
    *,
    now: datetime = NOW,
    limits: ToolLimits | None = None,
) -> dict[str, Any]:
    options = {} if limits is None else {"limits": limits}
    result = run_tool(db, context=context, name=name, params=params, now=now, **options)
    assert isinstance(result, ToolResult)
    payload = result.to_json()
    assert set(payload) == RESULT_KEYS
    assert payload["tools_version"] == "copilot-tools/1"
    assert payload["status"] in ("answered", "abstain")
    assert payload["scope"] == {"restricted": context.record_access.restricted}
    assert payload["limits"] == limits_json(DEFAULT_LIMITS if limits is None else limits)
    if payload["status"] == "abstain":
        assert payload["reason"] in ABSTAIN_REASONS, payload
    for item in payload["facts"]:
        assert set(item) == FACT_KEYS
        assert item["citations"] == sorted(item["citations"])
        assert all(isinstance(citation, str) for citation in item["citations"])
        assert type(item["citations_total"]) is int
        if item["parts"] is None:
            assert type(item["value"]) is int, item
        else:
            assert type(item["value"]) is float, item
            assert set(item["parts"]) == {"numerator", "denominator"}
    assert json.loads(dumped(payload)) == payload
    return payload


def ask(context: WorkspaceContext, name: object, params: object, **options: Any) -> dict:
    with session_for(context) as db:
        return run(db, context, name, params, **options)


def answered(payload: dict[str, Any], tool: str) -> list[dict[str, Any]]:
    assert payload["status"] == "answered", payload
    assert payload["tool"] == tool
    assert (payload["reason"], payload["detail"], payload["key"]) == (None, None, None)
    assert isinstance(payload["filters"], dict)
    return payload["facts"]


def abstained(
    payload: dict[str, Any],
    tool: str | None,
    reason: str,
    detail: str | None = None,
    key: str | tuple[str | None, ...] | None = None,
    *,
    hidden: tuple[str, ...] = (),
) -> None:
    assert payload["status"] == "abstain", payload
    assert payload["tool"] == tool, payload
    assert payload["reason"] == reason, payload
    assert payload["detail"] == detail, payload
    if isinstance(key, tuple):
        assert payload["key"] in key, payload
    else:
        assert payload["key"] == key, payload
    assert payload["filters"] is None
    assert payload["facts"] == []
    assert payload["provenance"] == {}
    text_form = json.dumps(payload)
    for value in hidden:
        assert value not in text_form


def fact(
    name: str,
    value: Any,
    ids: list[str],
    *,
    group: dict[str, Any] | None = None,
    parts: dict[str, int] | None = None,
    total: int | None = None,
    cap: int = 100,
    withheld: bool = False,
) -> dict[str, Any]:
    ordered = sorted(ids)
    count = len(ordered) if total is None else total
    return {
        "name": name,
        "group": group,
        "value": value,
        "parts": parts,
        "citations": [] if withheld else ordered[:cap],
        "citations_total": count,
        "citations_complete": not withheld and count <= cap,
        "citations_withheld": withheld,
    }


def masked(payload: dict[str, Any], key: str, value: str) -> str:
    copy = json.loads(json.dumps(payload))
    if copy["filters"] is not None:
        assert copy["filters"][key] in (value, [value])
        copy["filters"][key] = "<id>"
    return dumped(copy)


def persist(*rows: Any) -> None:
    with inspection_session_factory()() as db:
        for row in rows:
            db.add(row)
            db.flush()
        db.commit()


def meeting(
    workspace_id: str,
    created_by: str,
    *,
    at: datetime,
    team: str | None = None,
    agent: str | None = None,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    deleted: bool = False,
    meeting_id: str | None = None,
) -> Meeting:
    return Meeting(
        id=meeting_id or new_id(),
        workspace_id=workspace_id,
        created_by_user_id=created_by,
        title="Oracle call",
        status=status,
        duration_seconds=30,
        team_id=team,
        agent_user_id=agent,
        language="fa",
        created_at=at,
        updated_at=at,
        deleted_at=at if deleted else None,
    )


def task(
    workspace_id: str,
    meeting_id: str,
    due: datetime | None,
    *,
    status: ActionStatus = ActionStatus.OPEN,
    priority: str = "medium",
    assignee: str | None = None,
    item_id: str | None = None,
) -> ActionItem:
    return ActionItem(
        id=item_id or new_id(),
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        description="Oracle task",
        due_at=due,
        priority=priority,
        status=status,
        assignee_user_id=assignee,
    )


def card(workspace_id: str, key: str = "oracle_card") -> ScorecardVersion:
    return ScorecardVersion(
        id=new_id(),
        workspace_id=workspace_id,
        scorecard_key=key,
        version=1,
        scorecards_version="scorecards/1",
        document={},
        digest="b" * 64,
        reason="oracle",
    )


def revision(workspace_id: str, meeting_id: str, revision_id: str | None = None):
    return TranscriptRevision(
        id=revision_id or new_id(),
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        revision_number=1,
        source=TranscriptSource.ASR,
        reason="seed",
        language="fa",
        segment_count=2,
        is_mock=False,
        is_current=True,
    )


def score(
    workspace_id: str,
    meeting_id: str,
    version_id: str,
    revision_id: str,
    item: tuple[str, int, int, int],
    *,
    at: datetime,
    result_id: str | None = None,
    input_digest: str | None = None,
) -> ScorecardResultRecord:
    outcome, applicable, earned, insufficient = item
    return ScorecardResultRecord(
        id=result_id or new_id(),
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        scorecard_version_id=version_id,
        transcript_revision_id=revision_id,
        scorecards_version="scorecards/1",
        checks_version="checks/1",
        definition_digest="b" * 64,
        input_digest=input_digest or uuid.uuid4().hex * 2,
        conversation_type="call",
        direction=None,
        outcome=outcome,
        reason=OUTCOME_REASONS[outcome],
        complete=insufficient == 0,
        pass_threshold=70,
        applicable_weight=applicable,
        earned_weight=earned,
        failed_weight=applicable - earned - insufficient,
        insufficient_weight=insufficient,
        check_run={},
        result={},
        created_at=at,
    )


def scored_calls(
    tenant: Tenant,
    version_id: str,
    scores: list[tuple[str, int, int, int]],
    *,
    start: datetime = FROM,
    chosen_ids: dict[int, str] | None = None,
) -> list[tuple[str, str, str]]:
    rows: list[Any] = []
    made: list[tuple[str, str, str]] = []
    for index, item in enumerate(scores):
        call = meeting(tenant.workspace_id, tenant.owner_id, at=start + index * HOUR)
        text_revision = revision(tenant.workspace_id, call.id)
        result = score(
            tenant.workspace_id,
            call.id,
            version_id,
            text_revision.id,
            item,
            at=start + index * HOUR + TEN_MINUTES,
            result_id=(chosen_ids or {}).get(index),
        )
        rows.extend((call, text_revision, result))
        made.append((call.id, text_revision.id, result.id))
    persist(*rows)
    return made


def seed_many(tenant: Tenant, count: int, extraction: Any, **options: Any) -> list[str]:
    return [seed(tenant, extraction=extraction, **options).meeting_id for _ in range(count)]


def trend_reference(
    context: WorkspaceContext, raw: dict[str, Any], *, grain: str = "none"
) -> dict[str, Any]:
    with session_for(context) as db:
        return compute_issue_trends(
            db,
            workspace_id=context.workspace.id,
            access=context.record_access,
            filters=validate_filters(raw, DEFINITION, now=NOW),
            now=NOW,
            grain=grain,
            timezone="Asia/Tehran",
        )


def row_key(row: dict[str, Any]) -> str:
    return f"issue:{row['issue']}" if row["issue"] else f"family:{row['family']}"


def cell_fact(
    name: str,
    row: dict[str, Any],
    ids: list[str],
    *,
    group: dict[str, Any],
    cap: int = 100,
    rate: bool = False,
) -> dict[str, Any]:
    cell = row["cell"]
    computed = cell["status"] == "computed"
    if computed:
        assert len(ids) == cell["numerator"], (row, ids)
    return fact(
        name,
        cell["ratio"] if rate else cell["numerator"],
        ids if computed else [],
        group=group,
        parts={"numerator": cell["numerator"], "denominator": cell["denominator"]}
        if rate
        else None,
        total=cell["numerator"],
        cap=cap,
        withheld=not computed,
    )


def two_tenants(client, email_outbox, prefix: str) -> tuple[Tenant, Tenant]:
    return (
        Tenant(client, email_outbox, f"{prefix}-a@example.com"),
        Tenant(client, email_outbox, f"{prefix}-b@example.com"),
    )


def conversations(client, email_outbox, prefix: str) -> SimpleNamespace:
    mine, other = two_tenants(client, email_outbox, prefix)
    t1, t2 = add_team(mine, "one"), add_team(mine, "two")
    foreign_team = add_team(other, "foreign")
    x, y, w, member, foreign_agent = (
        add_user(name) for name in ("agent-x", "agent-y", "agent-w", "member", "agent-b")
    )
    ws, owner = mine.workspace_id, mine.owner_id
    rows = {
        "c1": meeting(ws, owner, at=FROM, team=t1, agent=x),
        "c2": meeting(ws, owner, at=FROM + HOUR, team=t1, agent=y, status=MeetingStatus.FAILED),
        "c3": meeting(ws, owner, at=FROM + DAY + HOUR, team=t2, agent=w),
        "c4": meeting(
            ws, owner, at=FROM + DAY + 2 * HOUR, agent=member, status=MeetingStatus.PARTIAL
        ),
        "c5": meeting(ws, owner, at=FROM + 2 * HOUR, team=t1, deleted=True),
        "c6": meeting(ws, owner, at=TO, team=t2),
        "c7": meeting(ws, owner, at=FROM - timedelta(seconds=1), team=t1),
        "foreign": meeting(
            other.workspace_id,
            other.owner_id,
            at=FROM + 3 * HOUR,
            team=foreign_team,
            agent=foreign_agent,
        ),
    }
    ids = {name: row.id for name, row in rows.items()}
    persist(*rows.values())
    return SimpleNamespace(
        mine=mine,
        other=other,
        t1=t1,
        t2=t2,
        foreign_team=foreign_team,
        x=x,
        y=y,
        w=w,
        member=member,
        foreign_agent=foreign_agent,
        live=[ids["c1"], ids["c2"], ids["c3"], ids["c4"]],
        **ids,
    )


def action_items(client, email_outbox, prefix: str) -> SimpleNamespace:
    mine, other = two_tenants(client, email_outbox, prefix)
    t1, t2 = add_team(mine, "one"), add_team(mine, "two")
    foreign_team = add_team(other, "foreign")
    agent, member, u1, u2, foreign_user = (
        add_user(name) for name in ("agent", "member", "u-one", "u-two", "u-foreign")
    )
    ws, owner = mine.workspace_id, mine.owner_id
    calls = {
        "ma": meeting(ws, owner, at=FROM, team=t1, agent=agent),
        "mb": meeting(ws, owner, at=FROM, team=t2),
        "mc": meeting(ws, owner, at=FROM, agent=member),
        "md": meeting(ws, owner, at=FROM, team=t1, deleted=True),
        "mx": meeting(other.workspace_id, other.owner_id, at=FROM, team=foreign_team),
    }
    on = {name: row.id for name, row in calls.items()}
    oct5 = datetime(2026, 10, 5, tzinfo=UTC)
    items = {
        "a1": task(ws, on["ma"], oct5, priority="high", assignee=u1),
        "a2": task(
            ws,
            on["ma"],
            datetime(2026, 10, 12, tzinfo=UTC),
            status=ActionStatus.IN_PROGRESS,
            priority="low",
            assignee=u2,
        ),
        "a3": task(ws, on["mb"], datetime(2026, 10, 14, 23, 59, 59, tzinfo=UTC), assignee=u1),
        "a4": task(ws, on["ma"], oct5, status=ActionStatus.DONE),
        "a5": task(ws, on["ma"], None),
        "a6": task(ws, on["ma"], datetime(2026, 10, 15, tzinfo=UTC)),
        "a7": task(ws, on["md"], oct5),
        "a8": task(other.workspace_id, on["mx"], oct5, assignee=foreign_user),
        "a9": task(ws, on["mc"], datetime(2026, 10, 1, tzinfo=UTC)),
        "a10": task(ws, on["mb"], oct5, status=ActionStatus.CANCELLED),
    }
    ids = {name: row.id for name, row in items.items()}
    persist(*calls.values(), *items.values())
    return SimpleNamespace(
        mine=mine,
        other=other,
        t1=t1,
        t2=t2,
        foreign_team=foreign_team,
        member=member,
        u1=u1,
        u2=u2,
        foreign_user=foreign_user,
        **ids,
    )


def full_params(name: str) -> dict[str, Any]:
    team, agent = new_id(), new_id()
    return {
        "conversation_count": {
            **WINDOW,
            "team_id": team,
            "agent_user_id": agent,
            "group_by": "status",
        },
        "action_items_due": {
            "due_before": DUE_BEFORE,
            "due_after": "2026-10-01T00:00:00+00:00",
            "team_id": team,
            "status": ["open"],
            "group_by": "priority",
            "assignee_user_id": agent,
        },
        "scorecard_summary": {
            **WINDOW,
            "scorecard_version_id": new_id(),
            "team_id": team,
            "agent_user_id": agent,
        },
        "issue_aggregate": {"intent": "issue_count", "params": {"issue": "complaint_refund"}},
        "top_issues": {
            **TREND_WINDOW,
            "family": "service_complaint",
            "limit": 5,
            "team_id": team,
            "agent_user_id": agent,
            "language": "fa",
            "source_type": "file_upload",
        },
    }[name]


def test_the_registry_holds_exactly_the_five_tools():
    assert TOOLS_VERSION == "copilot-tools/1"
    assert sorted(TOOLS) == list(TOOL_NAMES)
    with pytest.raises(TypeError):
        TOOLS["free_sql"] = TOOLS["conversation_count"]
    with pytest.raises(TypeError):
        del TOOLS["conversation_count"]
    for name, spec in TOOLS.items():
        assert isinstance(spec, ToolSpec)
        assert spec.name == name
        description = spec.description
        assert isinstance(description, str) and description and description.isascii()
        assert description == description.strip() and "\n" not in description
        permissions = spec.required_permissions
        assert isinstance(permissions, tuple)
        assert all(isinstance(permission, Permission) for permission in permissions)
        assert tuple(permission.value for permission in permissions) == PERMISSIONS[name]
        schema = spec.input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert set(schema["properties"]) == PARAMS[name]
        assert set(schema["required"]) == REQUIRED[name]
        with pytest.raises(dataclasses.FrozenInstanceError):
            spec.name = "free_sql"


def test_tool_schemas_are_the_model_tool_shape_sorted_by_name():
    schemas = tool_schemas()
    assert [schema["name"] for schema in schemas] == list(TOOL_NAMES)
    for schema in schemas:
        assert set(schema) == {"name", "description", "input_schema"}
        spec = TOOLS[schema["name"]]
        assert schema["description"] == spec.description
        assert schema["input_schema"] == spec.input_schema
        assert schema["input_schema"]["additionalProperties"] is False
    assert json.loads(json.dumps(schemas, sort_keys=True)) == schemas


def test_the_registry_digest_is_64_hex_and_stable_across_fresh_imports():
    assert re.fullmatch(r"[0-9a-f]{64}", REGISTRY_DIGEST)
    root = Path(backend.__file__).resolve().parent.parent
    program = "from backend.copilot_tools.registry import REGISTRY_DIGEST; print(REGISTRY_DIGEST)"
    seen = set()
    for hash_seed in ("0", "4242"):
        finished = subprocess.run(  # noqa: S603
            [sys.executable, "-c", program],
            cwd=root,
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            capture_output=True,
            text=True,
            check=True,
            timeout=180,
        )
        seen.add(finished.stdout.strip())
    assert seen == {REGISTRY_DIGEST}


def test_the_limit_defaults_and_caps():
    assert limits_json(DEFAULT_LIMITS) == {
        "max_rows": 5000,
        "max_citations": 100,
        "statement_timeout_ms": 5000,
        "time_budget_ms": 10000,
    }
    assert limits_json(LIMIT_CAPS) == {
        "max_rows": 20000,
        "max_citations": 500,
        "statement_timeout_ms": 15000,
        "time_budget_ms": 30000,
    }
    with pytest.raises(dataclasses.FrozenInstanceError):
        DEFAULT_LIMITS.max_rows = 1


def test_the_reason_and_defect_vocabularies():
    assert {reason.value for reason in AbstainReason} == ABSTAIN_REASONS
    assert PARAM_DEFECTS <= {defect.value for defect in ParamDefect}


def test_run_tool_has_the_documented_signature():
    parameters = inspect.signature(run_tool).parameters
    assert list(parameters) == ["db", "context", "name", "params", "now", "limits"]
    assert parameters["db"].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
    for name in ("context", "name", "params", "now", "limits"):
        assert parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["limits"].default == DEFAULT_LIMITS
    for name in ("context", "name", "params", "now"):
        assert parameters[name].default is inspect.Parameter.empty


def test_programming_errors_raise_value_error_before_any_query(client, email_outbox):
    mine, other = two_tenants(client, email_outbox, "oracle-errors")
    owner = caller(mine)
    with session_for(owner) as db:
        for bad_now in (datetime(2026, 10, 10, 12, 0), "2026-10-10T12:00:00+00:00", None):
            with pytest.raises(ValueError):
                run_tool(db, context=owner, name="conversation_count", params=WINDOW, now=bad_now)
        for bad_context in (None, owner.record_access, SimpleNamespace(**vars(owner))):
            with pytest.raises(ValueError):
                run_tool(db, context=bad_context, name="conversation_count", params=WINDOW, now=NOW)
        for bad_limits in (None, (5000, 100, 5000, 10000), limits_json(DEFAULT_LIMITS)):
            with pytest.raises(ValueError):
                run_tool(
                    db,
                    context=owner,
                    name="conversation_count",
                    params=WINDOW,
                    now=NOW,
                    limits=bad_limits,
                )
        for field, value in (
            ("max_rows", 20001),
            ("max_citations", 501),
            ("statement_timeout_ms", 15001),
            ("time_budget_ms", 30001),
            ("max_rows", 0),
            ("max_citations", -1),
            ("time_budget_ms", True),
            ("statement_timeout_ms", 1500.0),
        ):
            with pytest.raises(ValueError):
                run_tool(
                    db,
                    context=owner,
                    name="conversation_count",
                    params=WINDOW,
                    now=NOW,
                    limits=limits(**{field: value}),
                )
        at_caps = run(db, owner, "conversation_count", dict(WINDOW), limits=LIMIT_CAPS)
        assert answered(at_caps, "conversation_count") == [fact("conversations", 0, [])]
        smallest = limits(max_rows=1, max_citations=1)
        at_floor = run(db, owner, "conversation_count", dict(WINDOW), limits=smallest)
        assert answered(at_floor, "conversation_count") == [fact("conversations", 0, [], cap=1)]


def test_a_session_not_bound_to_the_callers_workspace_is_refused(client, email_outbox):
    mine, other = two_tenants(client, email_outbox, "oracle-binding")
    owner = caller(mine)
    unbound = get_session_factory()()
    try:
        with watched(unbound) as seen:
            for name in ("conversation_count", "no_such_tool"):
                with pytest.raises(ValueError):
                    run_tool(unbound, context=owner, name=name, params=dict(WINDOW), now=NOW)
        assert seen.statements == []
    finally:
        unbound.close()
    user_only = get_session_factory()()
    try:
        bind_user_context(user_only, mine.owner_id)
        with pytest.raises(ValueError):
            run_tool(user_only, context=owner, name="conversation_count", params=WINDOW, now=NOW)
    finally:
        user_only.rollback()
        user_only.close()
    with bound(other.workspace_id, other.organization_id, mine.owner_id) as wrong:
        with pytest.raises(ValueError):
            run_tool(wrong, context=owner, name="conversation_count", params=WINDOW, now=NOW)
    payload = ask(owner, "conversation_count", dict(WINDOW))
    assert answered(payload, "conversation_count") == [fact("conversations", 0, [])]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(None, id="null"),
        pytest.param(7, id="number"),
        pytest.param(b"conversation_count", id="bytes"),
        pytest.param(["conversation_count"], id="list"),
        pytest.param("", id="empty"),
        pytest.param("Conversation_Count", id="case"),
        pytest.param(" conversation_count", id="leading-space"),
        pytest.param("conversation_count ", id="trailing-space"),
        pytest.param("conversation-count", id="dash"),
        pytest.param("conversation_count; DROP TABLE meetings", id="injected"),
        pytest.param("analytics_sql", id="guard-tool-name"),
    ],
)
def test_an_unknown_tool_abstains_without_echoing_its_name(client, email_outbox, name):
    tenant = Tenant(client, email_outbox, "oracle-unknown@example.com")
    payload = ask(caller(tenant), name, dict(WINDOW))
    hidden = (name,) if isinstance(name, str) and name else ()
    abstained(payload, None, "tool_unknown", hidden=hidden)
    assert "DROP" not in json.dumps(payload)


def test_the_check_order_precedence_pairs(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-order@example.com")
    owner = caller(tenant)
    keyed = caller(tenant, api_scopes=[Permission.MEETING_READ])
    nothing = caller(tenant, api_scopes=[Permission.MEMBER_READ])
    ws = tenant.workspace_id

    abstained(ask(owner, "drop_table", 42), None, "tool_unknown", hidden=("drop_table",))
    abstained(
        ask(nothing, "no_such_tool", {"workspace_id": ws}), None, "tool_unknown", hidden=(ws,)
    )

    for params in (42, None, {"workspace_id": ws, "product": "x"}, {"aaa": 1}, {1: "x"}):
        abstained(
            ask(keyed, "scorecard_summary", params),
            "scorecard_summary",
            "permission_denied",
            hidden=(ws,),
        )
    for name in TOOL_NAMES:
        abstained(ask(nothing, name, {"workspace_id": ws}), name, "permission_denied", hidden=(ws,))

    abstained(
        ask(owner, "conversation_count", {"product": "x", "workspace_id": ws}),
        "conversation_count",
        "scope_not_allowed",
        key="workspace_id",
        hidden=(ws,),
    )
    abstained(
        ask(owner, "conversation_count", {"aaa": 1, "product": "x"}),
        "conversation_count",
        "unsupported_filter",
        key="product",
    )
    abstained(
        ask(owner, "conversation_count", {"aaa": 1, "group_by": "hour"}),
        "conversation_count",
        "params_invalid",
        "param_unknown",
        "aaa",
    )
    abstained(
        ask(owner, "conversation_count", {"scope": "everything", "role": "owner"}),
        "conversation_count",
        "scope_not_allowed",
        key="role",
        hidden=("everything",),
    )
    abstained(
        ask(owner, "conversation_count", {"organization_id": None}),
        "conversation_count",
        "scope_not_allowed",
        key="organization_id",
    )
    for word in sorted(SCOPE_KEYS):
        abstained(
            ask(owner, "action_items_due", {"due_before": DUE_BEFORE, word: "guess-1234"}),
            "action_items_due",
            "scope_not_allowed",
            key=word,
            hidden=("guess-1234",),
        )
    for word in sorted(UNSUPPORTED_KEYS):
        abstained(
            ask(owner, "conversation_count", {**WINDOW, word: "acme-5678"}),
            "conversation_count",
            "unsupported_filter",
            key=word,
            hidden=("acme-5678",),
        )
    abstained(
        ask(owner, "conversation_count", {"sentiment": "bad", "phone": "0912"}),
        "conversation_count",
        "unsupported_filter",
        key="phone",
        hidden=("0912",),
    )
    for not_mapping in (
        42,
        None,
        "from=x",
        ["from", FROM_LOCAL],
        {1: "x"},
        {"from": FROM_LOCAL, 2: 3},
    ):
        abstained(
            ask(owner, "conversation_count", not_mapping),
            "conversation_count",
            "params_invalid",
            "params_not_mapping",
        )
    for name in TOOL_NAMES:
        abstained(ask(owner, name, 42), name, "params_invalid", "params_not_mapping")

    allowed = ask(owner, "conversation_count", dict(WINDOW))
    assert answered(allowed, "conversation_count") == [fact("conversations", 0, [])]
    assert answered(ask(keyed, "conversation_count", dict(WINDOW)), "conversation_count")


def test_schema_properties_are_exactly_the_accepted_keys(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-schema@example.com")
    owner = caller(tenant)
    for name in TOOL_NAMES:
        params = full_params(name)
        assert set(params) == set(TOOLS[name].input_schema["properties"])
        accepted = ask(owner, name, params)
        assert accepted["tool"] == name
        assert accepted["reason"] != "params_invalid", accepted
        for extra in ("aaa_extra", "zzz_extra"):
            abstained(
                ask(owner, name, {**params, extra: 1}),
                name,
                "params_invalid",
                "param_unknown",
                extra,
            )


CONVERSATION_DEFECTS = [
    pytest.param({"aaa": 1, "from": "not a date"}, "param_unknown", "aaa", id="unknown-key-first"),
    pytest.param({"language": "fa"}, "param_unknown", "language", id="trend-key-not-taken"),
    pytest.param(
        {"from": "2026-10-01T00:00:00", "to": TO_LOCAL},
        "filter_window_invalid",
        "from",
        id="from-without-zone",
    ),
    pytest.param(
        {"from": "yesterday", "to": TO_LOCAL}, "filter_window_invalid", "from", id="from-text"
    ),
    pytest.param(
        {"from": TO_LOCAL, "to": FROM_LOCAL}, "filter_window_invalid", None, id="from-after-to"
    ),
    pytest.param(
        {"from": FROM_LOCAL, "to": FROM_LOCAL}, "filter_window_invalid", None, id="empty-window"
    ),
    pytest.param(
        {"from": "2025-01-01T00:00:00+00:00", "to": "2026-01-03T00:00:00+00:00"},
        "filter_window_invalid",
        None,
        id="span-367-days",
    ),
    pytest.param({"from": [FROM_LOCAL]}, "filter_bad_type", "from", id="from-as-list"),
    pytest.param({"team_id": UPPER_ID}, "filter_bad_value", "team_id", id="team-upper-case"),
    pytest.param({"team_id": []}, "filter_bad_value", "team_id", id="team-empty-list"),
    pytest.param({"team_id": 7}, "filter_bad_type", "team_id", id="team-number"),
    pytest.param(
        {"agent_user_id": "agent-7"}, "filter_bad_value", "agent_user_id", id="agent-not-uuid"
    ),
    pytest.param(
        {"group_by": "hour", "team_id": "not-a-uuid"},
        "filter_bad_value",
        "team_id",
        id="filter-before-own-param",
    ),
    pytest.param({"group_by": "hour"}, "param_bad_value", "group_by", id="group-by-unknown"),
    pytest.param({"group_by": "Status"}, "param_bad_value", "group_by", id="group-by-case"),
    pytest.param({"group_by": "STATUS"}, "param_bad_value", "group_by", id="group-by-upper"),
    pytest.param({"group_by": 1}, "param_bad_type", "group_by", id="group-by-number"),
    pytest.param({"group_by": ["status"]}, "param_bad_type", "group_by", id="group-by-list"),
]


@pytest.mark.parametrize(("params", "detail", "key"), CONVERSATION_DEFECTS)
def test_conversation_count_param_defects(client, email_outbox, params, detail, key):
    tenant = Tenant(client, email_outbox, "oracle-cc-defects@example.com")
    payload = ask(caller(tenant), "conversation_count", params)
    abstained(payload, "conversation_count", "params_invalid", detail, key)


ACTION_DEFECTS = [
    pytest.param({}, "param_missing", "due_before", id="due-before-missing"),
    pytest.param({"aaa": 1}, "param_unknown", "aaa", id="unknown-key-before-missing"),
    pytest.param(
        {"due_before": DUE_BEFORE, "from": FROM_LOCAL}, "param_unknown", "from", id="from"
    ),
    pytest.param({"due_before": "2026-10-15"}, "param_bad_value", "due_before", id="no-zone"),
    pytest.param({"due_before": "soon"}, "param_bad_value", "due_before", id="not-a-date"),
    pytest.param({"due_before": 20261015}, "param_bad_type", "due_before", id="number"),
    pytest.param(
        {"due_before": DUE_BEFORE, "due_after": DUE_BEFORE},
        "param_bad_value",
        "due_after",
        id="after-equals-before",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "due_after": "2026-10-16T00:00:00+00:00"},
        "param_bad_value",
        "due_after",
        id="after-past-before",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "due_after": "2025-10-13T00:00:00+00:00"},
        "param_bad_value",
        "due_after",
        id="span-367-days",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "due_after": "2026-10-01"},
        "param_bad_value",
        "due_after",
        id="after-without-zone",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "status": "done"}, "param_bad_value", "status", id="done"
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "status": "OPEN"}, "param_bad_value", "status", id="case"
    ),
    pytest.param({"due_before": DUE_BEFORE, "status": []}, "param_bad_value", "status", id="empty"),
    pytest.param(
        {"due_before": DUE_BEFORE, "status": ["open", "cancelled"]},
        "param_bad_value",
        "status",
        id="one-bad-in-list",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "status": 1}, "param_bad_type", "status", id="status-number"
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "assignee_user_id": UPPER_ID},
        "param_bad_value",
        "assignee_user_id",
        id="assignee-upper-case",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "assignee_user_id": []},
        "param_bad_value",
        "assignee_user_id",
        id="assignee-empty-list",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "assignee_user_id": 5},
        "param_bad_type",
        "assignee_user_id",
        id="assignee-number",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "team_id": UPPER_ID},
        "filter_bad_value",
        "team_id",
        id="team-upper-case",
    ),
    pytest.param(
        {"due_before": DUE_BEFORE, "group_by": "day"}, "param_bad_value", "group_by", id="day"
    ),
]


@pytest.mark.parametrize(("params", "detail", "key"), ACTION_DEFECTS)
def test_action_items_due_param_defects(client, email_outbox, params, detail, key):
    tenant = Tenant(client, email_outbox, "oracle-ai-defects@example.com")
    payload = ask(caller(tenant), "action_items_due", params)
    abstained(payload, "action_items_due", "params_invalid", detail, key)


SCORECARD_DEFECTS = [
    pytest.param({}, "param_missing", "scorecard_version_id", id="missing"),
    pytest.param(
        {"from": "2026-10-01"}, "filter_window_invalid", "from", id="filter-before-missing"
    ),
    pytest.param(
        {"scorecard_version_id": "nope", "team_id": "bad"},
        "filter_bad_value",
        "team_id",
        id="filter-before-own-param",
    ),
    pytest.param(
        {"scorecard_version_id": UPPER_ID}, "param_bad_value", "scorecard_version_id", id="upper"
    ),
    pytest.param(
        {"scorecard_version_id": "card-1"}, "param_bad_value", "scorecard_version_id", id="text"
    ),
    pytest.param(
        {"scorecard_version_id": 5}, "param_bad_type", "scorecard_version_id", id="number"
    ),
    pytest.param(
        {"scorecard_version_id": [LOWER_ID]}, "param_bad_type", "scorecard_version_id", id="list"
    ),
    pytest.param(
        {"scorecard_version_id": LOWER_ID, "group_by": "outcome"},
        "param_unknown",
        "group_by",
        id="group-by-not-taken",
    ),
]


@pytest.mark.parametrize(("params", "detail", "key"), SCORECARD_DEFECTS)
def test_scorecard_summary_param_defects(client, email_outbox, params, detail, key):
    tenant = Tenant(client, email_outbox, "oracle-sc-defects@example.com")
    payload = ask(caller(tenant), "scorecard_summary", params)
    abstained(payload, "scorecard_summary", "params_invalid", detail, key)


ISSUE_DEFECTS = [
    pytest.param("issue_aggregate", {}, "param_missing", "intent", id="aggregate-empty"),
    pytest.param("issue_aggregate", {"params": {}}, "param_missing", "intent", id="intent-missing"),
    pytest.param(
        "issue_aggregate", {"intent": "issue_count"}, "param_missing", "params", id="params-missing"
    ),
    pytest.param(
        "issue_aggregate",
        {"intent": 5, "params": {}},
        "param_bad_type",
        "intent",
        id="intent-number",
    ),
    pytest.param(
        "issue_aggregate",
        {"intent": "issue_count", "params": [["issue", "complaint_refund"]]},
        "param_bad_type",
        "params",
        id="params-list",
    ),
    pytest.param(
        "issue_aggregate",
        {"intent": "issue_count", "params": "issue=complaint_refund"},
        "param_bad_type",
        "params",
        id="params-text",
    ),
    pytest.param(
        "issue_aggregate",
        {"intent": "issue_count", "params": {}, "grain": "day"},
        "param_unknown",
        "grain",
        id="aggregate-extra-key",
    ),
    pytest.param(
        "issue_aggregate",
        {"intent": "issue_count", "params": {}, "from": FROM_LOCAL},
        "param_unknown",
        "from",
        id="aggregate-top-level-filter",
    ),
    pytest.param("top_issues", {}, "param_missing", "family", id="family-missing"),
    pytest.param(
        "top_issues",
        {"family": ["service_complaint"]},
        "param_bad_type",
        "family",
        id="family-list",
    ),
    pytest.param("top_issues", {"family": 5}, "param_bad_type", "family", id="family-number"),
    pytest.param(
        "top_issues",
        {"family": "service_complaint", "limit": 0},
        "param_bad_value",
        "limit",
        id="limit-zero",
    ),
    pytest.param(
        "top_issues",
        {"family": "service_complaint", "limit": 11},
        "param_bad_value",
        "limit",
        id="limit-eleven",
    ),
    pytest.param(
        "top_issues",
        {"family": "service_complaint", "limit": True},
        "param_bad_type",
        "limit",
        id="limit-bool",
    ),
    pytest.param(
        "top_issues",
        {"family": "service_complaint", "limit": "5"},
        "param_bad_type",
        "limit",
        id="limit-text",
    ),
    pytest.param(
        "top_issues",
        {"family": "service_complaint", "limit": 2.0},
        "param_bad_type",
        "limit",
        id="limit-float",
    ),
    pytest.param(
        "top_issues",
        {"family": "service_complaint", "issue": "complaint_refund"},
        "param_unknown",
        "issue",
        id="issue-not-taken",
    ),
    pytest.param(
        "top_issues",
        {"family": "service_complaint", "from": "2026-10-01"},
        "filter_window_invalid",
        "from",
        id="from-without-zone",
    ),
]


@pytest.mark.parametrize(("tool", "params", "detail", "key"), ISSUE_DEFECTS)
def test_issue_tool_param_defects(client, email_outbox, tool, params, detail, key):
    tenant = Tenant(client, email_outbox, "oracle-issue-defects@example.com")
    payload = ask(caller(tenant), tool, params)
    abstained(payload, tool, "params_invalid", detail, key)


def test_conversation_count_matches_the_hand_reference(client, email_outbox):
    data = conversations(client, email_outbox, "oracle-cc")
    owner = caller(data.mine)
    live = data.live
    total = fact("conversations", 4, live)

    plain = ask(owner, "conversation_count", dict(WINDOW))
    assert answered(plain, "conversation_count") == [total]
    assert plain["filters"]["window"] == {
        "from": FROM_UTC,
        "to": TO_UTC,
        "basis": "meeting_created_at",
    }

    by_status = ask(owner, "conversation_count", {**WINDOW, "group_by": "status"})
    assert answered(by_status, "conversation_count") == [
        total,
        fact("conversations", 2, [data.c1, data.c3], group={"status": "completed"}),
        fact("conversations", 1, [data.c2], group={"status": "failed"}),
        fact("conversations", 1, [data.c4], group={"status": "partial"}),
    ]

    teams = {data.t1: [data.c1, data.c2], data.t2: [data.c3]}
    first, second = sorted(teams)
    by_team = ask(owner, "conversation_count", {**WINDOW, "group_by": "team_id"})
    assert answered(by_team, "conversation_count") == [
        total,
        fact("conversations", 1, [data.c4], group={"team_id": None}),
        fact("conversations", len(teams[first]), teams[first], group={"team_id": first}),
        fact("conversations", len(teams[second]), teams[second], group={"team_id": second}),
    ]

    by_day = ask(owner, "conversation_count", {**WINDOW, "group_by": "day"})
    assert answered(by_day, "conversation_count") == [
        total,
        fact(
            "conversations",
            2,
            [data.c1, data.c2],
            group={"point": 0, "from": FROM_LOCAL, "to": MID_LOCAL, "partial": False},
        ),
        fact(
            "conversations",
            2,
            [data.c3, data.c4],
            group={"point": 1, "from": MID_LOCAL, "to": TO_LOCAL, "partial": False},
        ),
    ]
    assert by_day["filters"]["series"] == {"grain": "day", "timezone": "Asia/Tehran"}

    for grain in ("week", "month"):
        series = ask(owner, "conversation_count", {**WINDOW, "group_by": grain})
        assert answered(series, "conversation_count") == [
            total,
            fact(
                "conversations",
                4,
                live,
                group={"point": 0, "from": FROM_LOCAL, "to": TO_LOCAL, "partial": True},
            ),
        ]
        assert series["filters"]["series"] == {"grain": grain, "timezone": "Asia/Tehran"}

    empty_days = ask(
        owner,
        "conversation_count",
        {"from": "2026-10-04T00:00:00+03:30", "to": "2026-10-06T00:00:00+03:30", "group_by": "day"},
    )
    assert answered(empty_days, "conversation_count") == [
        fact("conversations", 0, []),
        fact(
            "conversations",
            0,
            [],
            group={
                "point": 0,
                "from": "2026-10-04T00:00:00+03:30",
                "to": "2026-10-05T00:00:00+03:30",
                "partial": False,
            },
        ),
        fact(
            "conversations",
            0,
            [],
            group={
                "point": 1,
                "from": "2026-10-05T00:00:00+03:30",
                "to": "2026-10-06T00:00:00+03:30",
                "partial": False,
            },
        ),
    ]

    default_window = ask(owner, "conversation_count", {})
    assert answered(default_window, "conversation_count") == [
        fact("conversations", 6, [*live, data.c6, data.c7])
    ]
    assert default_window["filters"]["window"] == {
        "from": "2026-09-10T12:00:00+00:00",
        "to": "2026-10-10T12:00:00+00:00",
        "basis": "meeting_created_at",
    }

    one_team = ask(owner, "conversation_count", {**WINDOW, "team_id": data.t1})
    assert answered(one_team, "conversation_count") == [
        fact("conversations", 2, [data.c1, data.c2])
    ]
    assert one_team["filters"]["team_id"] == [data.t1]
    foreign = ask(owner, "conversation_count", {**WINDOW, "team_id": data.foreign_team})
    assert answered(foreign, "conversation_count") == [fact("conversations", 0, [])]
    one_agent = ask(owner, "conversation_count", {**WINDOW, "agent_user_id": data.x})
    assert answered(one_agent, "conversation_count") == [fact("conversations", 1, [data.c1])]
    both = ask(owner, "conversation_count", {**WINDOW, "team_id": data.t2, "agent_user_id": data.x})
    assert answered(both, "conversation_count") == [fact("conversations", 0, [])]
    abstained(
        ask(owner, "conversation_count", {**WINDOW, "group_by": "agent_user_id"}),
        "conversation_count",
        "individual_ranking_not_supported",
        key=RANKING_KEYS,
    )

    capped = ask(
        owner,
        "conversation_count",
        {**WINDOW, "group_by": "status"},
        limits=limits(max_citations=1),
    )
    assert answered(capped, "conversation_count") == [
        fact("conversations", 4, live, cap=1),
        fact("conversations", 2, [data.c1, data.c3], group={"status": "completed"}, cap=1),
        fact("conversations", 1, [data.c2], group={"status": "failed"}, cap=1),
        fact("conversations", 1, [data.c4], group={"status": "partial"}, cap=1),
    ]
    exact_cap = ask(owner, "conversation_count", dict(WINDOW), limits=limits(max_citations=4))
    assert answered(exact_cap, "conversation_count") == [fact("conversations", 4, live, cap=4)]

    assert answered(
        ask(owner, "conversation_count", dict(WINDOW), limits=limits(max_rows=4)),
        "conversation_count",
    ) == [total]
    for params in (dict(WINDOW), {**WINDOW, "group_by": "day"}):
        abstained(
            ask(owner, "conversation_count", params, limits=limits(max_rows=3)),
            "conversation_count",
            "row_limit_exceeded",
        )
    assert dumped(ask(owner, "conversation_count", dict(WINDOW))) == dumped(plain)


def test_conversation_count_reads_every_spelling_of_one_input_alike(client, email_outbox):
    data = conversations(client, email_outbox, "oracle-cc-spelling")
    owner = caller(data.mine)
    windows = [
        WINDOW,
        {"from": "2026-09-30T20:30:00Z", "to": "2026-10-02T20:30:00Z"},
        {"from": FROM_UTC, "to": TO_UTC},
        {"from": "2026-09-30T16:30:00-04:00", "to": "2026-10-02T16:30:00-04:00"},
    ]
    answers = {dumped(ask(owner, "conversation_count", dict(window))) for window in windows}
    assert len(answers) == 1
    teams = [data.t1, [data.t1], [data.t1, data.t1]]
    answers = {dumped(ask(owner, "conversation_count", {**WINDOW, "team_id": t})) for t in teams}
    assert len(answers) == 1
    pair = [
        ask(owner, "conversation_count", {**WINDOW, "team_id": [data.t2, data.t1]}),
        ask(owner, "conversation_count", {**WINDOW, "team_id": [data.t1, data.t2, data.t1]}),
    ]
    assert dumped(pair[0]) == dumped(pair[1])
    assert pair[0]["filters"]["team_id"] == sorted([data.t1, data.t2])
    assert answered(pair[0], "conversation_count") == [
        fact("conversations", 3, [data.c1, data.c2, data.c3])
    ]
    abstained(
        ask(owner, "conversation_count", {**WINDOW, "workspace_id": data.other.workspace_id}),
        "conversation_count",
        "scope_not_allowed",
        key="workspace_id",
        hidden=(data.other.workspace_id,),
    )
    assert answered(ask(owner, "conversation_count", dict(WINDOW)), "conversation_count")


def test_conversation_count_keeps_to_the_record_scope_and_ignores_guessed_ids(client, email_outbox):
    data = conversations(client, email_outbox, "oracle-cc-scope")
    scoped = caller(
        data.mine, user_id=data.member, role=WorkspaceRole.MEMBER, supervised=(data.t1,)
    )
    mine = ask(scoped, "conversation_count", dict(WINDOW))
    assert answered(mine, "conversation_count") == [
        fact("conversations", 3, [data.c1, data.c2, data.c4])
    ]
    supervised = ask(scoped, "conversation_count", {**WINDOW, "team_id": data.t1})
    assert answered(supervised, "conversation_count") == [
        fact("conversations", 2, [data.c1, data.c2])
    ]
    for key, guesses in (
        ("team_id", (data.t2, data.foreign_team, new_id())),
        ("agent_user_id", (data.w, data.foreign_agent, new_id())),
    ):
        answers = []
        for guess in guesses:
            payload = ask(scoped, "conversation_count", {**WINDOW, key: guess})
            assert answered(payload, "conversation_count") == [fact("conversations", 0, [])]
            answers.append(masked(payload, key, guess))
        assert len(set(answers)) == 1
    unrestricted = caller(data.mine)
    assert answered(
        ask(unrestricted, "conversation_count", {**WINDOW, "team_id": data.t2}),
        "conversation_count",
    ) == [fact("conversations", 1, [data.c3])]


def test_action_items_due_matches_the_hand_reference(client, email_outbox):
    data = action_items(client, email_outbox, "oracle-ai")
    owner = caller(data.mine)
    due = [data.a1, data.a2, data.a3, data.a9]
    totals = [
        fact("action_items_due", 4, due),
        fact("action_items_overdue", 2, [data.a1, data.a9]),
    ]

    plain = ask(owner, "action_items_due", {"due_before": DUE_BEFORE})
    assert answered(plain, "action_items_due") == totals
    assert plain["filters"]["window"] == {
        "from": None,
        "to": DUE_BEFORE,
        "basis": "action_item_due_at",
    }
    assert plain["filters"]["status"] == ["in_progress", "open"]

    by_status = ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "status"})
    assert answered(by_status, "action_items_due") == [
        *totals,
        fact("action_items_due", 1, [data.a2], group={"status": "in_progress"}),
        fact("action_items_due", 3, [data.a1, data.a3, data.a9], group={"status": "open"}),
    ]
    by_priority = ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "priority"})
    assert answered(by_priority, "action_items_due") == [
        *totals,
        fact("action_items_due", 1, [data.a1], group={"priority": "high"}),
        fact("action_items_due", 1, [data.a2], group={"priority": "low"}),
        fact("action_items_due", 2, [data.a3, data.a9], group={"priority": "medium"}),
    ]
    teams = {data.t1: [data.a1, data.a2], data.t2: [data.a3]}
    first, second = sorted(teams)
    by_team = ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "group_by": "team_id"})
    assert answered(by_team, "action_items_due") == [
        *totals,
        fact("action_items_due", 1, [data.a9], group={"team_id": None}),
        fact("action_items_due", len(teams[first]), teams[first], group={"team_id": first}),
        fact("action_items_due", len(teams[second]), teams[second], group={"team_id": second}),
    ]

    after = ask(
        owner,
        "action_items_due",
        {"due_before": "2026-10-15T03:30:00+03:30", "due_after": "2026-10-05T03:30:00+03:30"},
    )
    assert answered(after, "action_items_due") == [
        fact("action_items_due", 3, [data.a1, data.a2, data.a3]),
        fact("action_items_overdue", 1, [data.a1]),
    ]
    assert after["filters"]["window"] == {
        "from": "2026-10-05T00:00:00+00:00",
        "to": DUE_BEFORE,
        "basis": "action_item_due_at",
    }
    widest = ask(
        owner,
        "action_items_due",
        {"due_before": DUE_BEFORE, "due_after": "2025-10-14T00:00:00+00:00"},
    )
    assert answered(widest, "action_items_due") == totals

    in_progress = ask(
        owner, "action_items_due", {"due_before": DUE_BEFORE, "status": "in_progress"}
    )
    assert answered(in_progress, "action_items_due") == [
        fact("action_items_due", 1, [data.a2]),
        fact("action_items_overdue", 0, []),
    ]
    open_twice = ask(
        owner, "action_items_due", {"due_before": DUE_BEFORE, "status": ["open", "open"]}
    )
    assert answered(open_twice, "action_items_due") == [
        fact("action_items_due", 3, [data.a1, data.a3, data.a9]),
        fact("action_items_overdue", 2, [data.a1, data.a9]),
    ]
    assert open_twice["filters"]["status"] == ["open"]
    both_orders = [
        ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "status": s})
        for s in (["open", "in_progress"], ["in_progress", "open", "open"])
    ]
    assert dumped(both_orders[0]) == dumped(both_orders[1]) == dumped(plain)

    one_team = ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "team_id": data.t1})
    assert answered(one_team, "action_items_due") == [
        fact("action_items_due", 2, [data.a1, data.a2]),
        fact("action_items_overdue", 1, [data.a1]),
    ]
    assignee = [
        ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "assignee_user_id": a})
        for a in (data.u1, [data.u1], [data.u1, data.u1])
    ]
    assert len({dumped(payload) for payload in assignee}) == 1
    assert answered(assignee[0], "action_items_due") == [
        fact("action_items_due", 2, [data.a1, data.a3]),
        fact("action_items_overdue", 1, [data.a1]),
    ]
    assert assignee[0]["filters"]["assignee_user_id"] == [data.u1]
    too_many = [new_id() for _ in range(51)]
    abstained(
        ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "assignee_user_id": too_many}),
        "action_items_due",
        "params_invalid",
        "param_bad_value",
        "assignee_user_id",
        hidden=tuple(too_many),
    )
    fifty = ask(
        owner, "action_items_due", {"due_before": DUE_BEFORE, "assignee_user_id": too_many[:50]}
    )
    assert answered(fifty, "action_items_due") == [
        fact("action_items_due", 0, []),
        fact("action_items_overdue", 0, []),
    ]
    for group in ("assignee_user_id", "agent_user_id"):
        abstained(
            ask(owner, "action_items_due", {"due_before": DUE_BEFORE, "group_by": group}),
            "action_items_due",
            "individual_ranking_not_supported",
            key=RANKING_KEYS,
        )

    capped = ask(
        owner, "action_items_due", {"due_before": DUE_BEFORE}, limits=limits(max_citations=2)
    )
    assert answered(capped, "action_items_due") == [
        fact("action_items_due", 4, due, cap=2),
        fact("action_items_overdue", 2, [data.a1, data.a9], cap=2),
    ]
    assert (
        answered(
            ask(owner, "action_items_due", {"due_before": DUE_BEFORE}, limits=limits(max_rows=4)),
            "action_items_due",
        )
        == totals
    )
    abstained(
        ask(owner, "action_items_due", {"due_before": DUE_BEFORE}, limits=limits(max_rows=3)),
        "action_items_due",
        "row_limit_exceeded",
    )


def test_action_items_due_keeps_to_the_record_scope_and_ignores_guessed_ids(client, email_outbox):
    data = action_items(client, email_outbox, "oracle-ai-scope")
    scoped = caller(
        data.mine, user_id=data.member, role=WorkspaceRole.MEMBER, supervised=(data.t2,)
    )
    mine = ask(scoped, "action_items_due", {"due_before": DUE_BEFORE})
    assert answered(mine, "action_items_due") == [
        fact("action_items_due", 2, [data.a3, data.a9]),
        fact("action_items_overdue", 1, [data.a9]),
    ]
    supervised = ask(scoped, "action_items_due", {"due_before": DUE_BEFORE, "team_id": data.t2})
    assert answered(supervised, "action_items_due") == [
        fact("action_items_due", 1, [data.a3]),
        fact("action_items_overdue", 0, []),
    ]
    for key, guesses in (
        ("team_id", (data.t1, data.foreign_team, new_id())),
        ("assignee_user_id", (data.u2, data.foreign_user, new_id())),
    ):
        answers = []
        for guess in guesses:
            payload = ask(scoped, "action_items_due", {"due_before": DUE_BEFORE, key: guess})
            assert answered(payload, "action_items_due") == [
                fact("action_items_due", 0, []),
                fact("action_items_overdue", 0, []),
            ]
            answers.append(masked(payload, key, guess))
        assert len(set(answers)) == 1


def test_scorecard_summary_matches_the_hand_reference(client, email_outbox):
    mine, other = two_tenants(client, email_outbox, "oracle-sc")
    version, second, foreign = (
        card(mine.workspace_id),
        card(mine.workspace_id, "other_card"),
        card(other.workspace_id),
    )
    persist(version, second, foreign)
    winner, loser = sorted([new_id(), new_id()], reverse=True)
    calls = scored_calls(mine, version.id, SCORES, chosen_ids={1: winner})
    ws = mine.workspace_id
    gone = meeting(ws, mine.owner_id, at=FROM + 5 * HOUR, deleted=True)
    late = meeting(ws, mine.owner_id, at=TO)
    gone_revision, late_revision = revision(ws, gone.id), revision(ws, late.id)
    persist(
        score(ws, calls[0][0], version.id, calls[0][1], ("failed", 100, 10, 0), at=FROM - HOUR),
        score(
            ws,
            calls[1][0],
            version.id,
            calls[1][1],
            ("failed", 100, 0, 0),
            at=FROM + HOUR + TEN_MINUTES,
            result_id=loser,
        ),
        gone,
        late,
        gone_revision,
        late_revision,
        score(
            ws, gone.id, version.id, gone_revision.id, ("passed", 100, 100, 0), at=FROM + 6 * HOUR
        ),
        score(ws, late.id, version.id, late_revision.id, ("passed", 100, 100, 0), at=TO + HOUR),
        score(ws, calls[3][0], second.id, calls[3][1], ("passed", 100, 100, 0), at=FROM + 4 * HOUR),
    )
    scored_calls(other, foreign.id, SCORES)

    results = [call[2] for call in calls]
    assert results[1] == winner
    outcomes: dict[str, list[str]] = {}
    for (_meeting, _revision, result_id), item in zip(calls, SCORES, strict=True):
        outcomes.setdefault(item[0], []).append(result_id)
    weighted = [call[2] for call, item in zip(calls, SCORES, strict=True) if item[1] > 0]
    mean = sum(Fraction(item[2] * 100, item[1]) for item in SCORES if item[1] > 0) / len(weighted)
    assert mean == Fraction(2713, 36)
    rounded = (Decimal(mean.numerator) / Decimal(mean.denominator)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    assert float(rounded) == 75.36

    def expected(cap: int = 100) -> list[dict[str, Any]]:
        return [
            fact("scorecard_results", 31, results, cap=cap),
            *(
                fact(
                    "scorecard_results",
                    len(outcomes[outcome]),
                    outcomes[outcome],
                    group={"outcome": outcome},
                    cap=cap,
                )
                for outcome in sorted(outcomes)
            ),
            fact(
                "mean_score_percent",
                75.36,
                weighted,
                parts={"numerator": 2713, "denominator": 36},
                cap=cap,
            ),
        ]

    owner = caller(mine)
    params = {**WINDOW, "scorecard_version_id": version.id}
    payload = ask(owner, "scorecard_summary", params)
    assert answered(payload, "scorecard_summary") == expected()
    assert [item["group"] for item in payload["facts"][1:5]] == [
        {"outcome": "failed"},
        {"outcome": "insufficient_evidence"},
        {"outcome": "not_applicable"},
        {"outcome": "passed"},
    ]
    assert payload["filters"]["window"]["from"] == FROM_UTC
    assert payload["filters"]["window"]["to"] == TO_UTC
    capped = ask(owner, "scorecard_summary", params, limits=limits(max_citations=5))
    assert answered(capped, "scorecard_summary") == expected(5)
    assert dumped(ask(owner, "scorecard_summary", params)) == dumped(payload)

    member = add_user("member")
    scoped = caller(mine, user_id=member, role=WorkspaceRole.MEMBER, supervised=())
    for context, guesses in (
        (scoped, (version.id, foreign.id, new_id())),
        (owner, (foreign.id, second.id, new_id())),
    ):
        answers = []
        for guess in guesses:
            refused = ask(context, "scorecard_summary", {**WINDOW, "scorecard_version_id": guess})
            abstained(
                refused,
                "scorecard_summary",
                "below_minimum_sample",
                hidden=(version.id, foreign.id, second.id, guess),
            )
            answers.append(dumped(refused))
        assert len(set(answers)) == 1
    for key in ("team_id", "agent_user_id"):
        abstained(
            ask(owner, "scorecard_summary", {**params, key: new_id()}),
            "scorecard_summary",
            "below_minimum_sample",
        )


def test_the_mean_score_rounds_half_to_even(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-sc-even@example.com")
    version = card(tenant.workspace_id)
    persist(version)
    scores = [("passed", 100, 70, 0)] * 28 + [("passed", 100, 71, 0)] * 4
    ids = [call[2] for call in scored_calls(tenant, version.id, scores)]
    payload = ask(
        caller(tenant), "scorecard_summary", {**WINDOW, "scorecard_version_id": version.id}
    )
    assert answered(payload, "scorecard_summary") == [
        fact("scorecard_results", 32, ids),
        fact("scorecard_results", 32, ids, group={"outcome": "passed"}),
        fact("mean_score_percent", 70.12, ids, parts={"numerator": 561, "denominator": 8}),
    ]


def test_scorecard_minimum_row_limit_and_permission(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-sc-minimum@example.com")
    version, weightless = card(tenant.workspace_id), card(tenant.workspace_id, "weightless")
    persist(version, weightless)
    first = scored_calls(tenant, version.id, [("passed", 100, 80, 0)] * 29)
    owner = caller(tenant)
    params = {**WINDOW, "scorecard_version_id": version.id}
    abstained(
        ask(owner, "scorecard_summary", params),
        "scorecard_summary",
        "below_minimum_sample",
        hidden=(version.id,),
    )
    last = scored_calls(tenant, version.id, [("passed", 100, 80, 0)], start=FROM + 29 * HOUR)
    ids = [call[2] for call in first + last]
    expected = [
        fact("scorecard_results", 30, ids),
        fact("scorecard_results", 30, ids, group={"outcome": "passed"}),
        fact("mean_score_percent", 80.0, ids, parts={"numerator": 80, "denominator": 1}),
    ]
    assert answered(ask(owner, "scorecard_summary", params), "scorecard_summary") == expected
    assert (
        answered(
            ask(owner, "scorecard_summary", params, limits=limits(max_rows=30)), "scorecard_summary"
        )
        == expected
    )
    abstained(
        ask(owner, "scorecard_summary", params, limits=limits(max_rows=29)),
        "scorecard_summary",
        "row_limit_exceeded",
    )

    keyed = caller(tenant, api_scopes=[Permission.MEETING_READ])
    abstained(
        ask(keyed, "scorecard_summary", params),
        "scorecard_summary",
        "permission_denied",
        hidden=(version.id,),
    )
    reads = ask(keyed, "conversation_count", dict(WINDOW))
    assert answered(reads, "conversation_count") == [
        fact("conversations", 30, [call[0] for call in first + last])
    ]
    viewer = caller(tenant, role=WorkspaceRole.VIEWER)
    assert answered(ask(viewer, "scorecard_summary", params), "scorecard_summary") == expected

    scored_calls(tenant, weightless.id, [("not_applicable", 0, 0, 0)] * 30)
    abstained(
        ask(owner, "scorecard_summary", {**WINDOW, "scorecard_version_id": weightless.id}),
        "scorecard_summary",
        "not_computable",
    )


def test_issue_aggregate_matches_the_trend_cells(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-issues@example.com")
    refunds = seed_many(tenant, 10, COMPLAINT)
    invoices = seed_many(tenant, 3, INVOICE_COMPLAINT)
    seed_many(tenant, 22, BILLING)
    owner = caller(tenant)
    raw = {**TREND_WINDOW, "issue": "complaint_refund"}
    group = {"row": "issue:complaint_refund"}
    (row,) = trend_reference(owner, raw)["rows"]
    assert row["cell"]["status"] == "computed" and row["cell"]["numerator"] == 10

    count = ask(owner, "issue_aggregate", {"intent": "issue_count", "params": raw})
    assert answered(count, "issue_aggregate") == [fact("issue_count", 10, refunds, group=group)]
    assert count["filters"]["intent"] == "issue_count"
    for key, value in validate_filters(raw, DEFINITION, now=NOW).to_json().items():
        assert count["filters"][key] == value
    catalogue = load_catalogue()
    assert count["provenance"]["catalogue_version"] == catalogue.version
    assert count["provenance"]["catalogue_digest"] == catalogue.digest
    for key, value in trend_provenance(DEFINITION, SAMPLE_POLICY_V1).items():
        assert count["provenance"][key] == value
    assert RELEASED_RATE in count["provenance"].values()

    rate = ask(owner, "issue_aggregate", {"intent": "issue_rate", "params": raw})
    assert answered(rate, "issue_aggregate") == [
        cell_fact("issue_rate", row, refunds, group=group, rate=True)
    ]

    family_raw = {**TREND_WINDOW, "family": "service_complaint"}
    family_rows = trend_reference(owner, family_raw)["rows"]
    assert len(family_rows) == 12
    counted = {
        "family:service_complaint": refunds + invoices,
        "issue:complaint_refund": refunds,
        "issue:complaint_invoice": invoices,
    }
    by_family = ask(owner, "issue_aggregate", {"intent": "issue_count", "params": family_raw})
    assert answered(by_family, "issue_aggregate") == [
        cell_fact("issue_count", r, counted.get(row_key(r), []), group={"row": row_key(r)})
        for r in family_rows
    ]

    (point,) = trend_reference(owner, raw, grain="day")["points"]
    assert (point["from"], point["to"], point["partial"]) == (FROM_LOCAL, MID_LOCAL, False)
    (point_row,) = point["rows"]
    trend = ask(
        owner, "issue_aggregate", {"intent": "issue_trend", "params": {**raw, "grain": "day"}}
    )
    assert answered(trend, "issue_aggregate") == [
        cell_fact(
            "issue_rate",
            point_row,
            refunds,
            group={**group, "point": 0, "from": FROM_LOCAL, "to": MID_LOCAL, "partial": False},
            rate=True,
        )
    ]

    capped = ask(
        owner,
        "issue_aggregate",
        {"intent": "issue_count", "params": raw},
        limits=limits(max_citations=3),
    )
    assert answered(capped, "issue_aggregate") == [
        fact("issue_count", 10, refunds, group=group, cap=3)
    ]
    exact_rows = ask(
        owner,
        "issue_aggregate",
        {"intent": "issue_count", "params": raw},
        limits=limits(max_rows=35),
    )
    assert answered(exact_rows, "issue_aggregate") == [
        fact("issue_count", 10, refunds, group=group)
    ]
    abstained(
        ask(
            owner,
            "issue_aggregate",
            {"intent": "issue_count", "params": raw},
            limits=limits(max_rows=34),
        ),
        "issue_aggregate",
        "row_limit_exceeded",
    )
    assert dumped(
        ask(owner, "issue_aggregate", {"intent": "issue_count", "params": raw})
    ) == dumped(count)


def test_top_issues_ranks_by_count_then_issue_id(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-top@example.com")
    refunds = seed_many(tenant, 10, COMPLAINT)
    invoices = seed_many(tenant, 3, INVOICE_COMPLAINT)
    seed_many(tenant, 22, BILLING)
    owner = caller(tenant)
    family_raw = {**TREND_WINDOW, "family": "service_complaint"}
    rows = {r["issue"]: r for r in trend_reference(owner, family_raw)["rows"] if r["issue"]}
    assert len(rows) == 11
    counted = {"complaint_refund": refunds, "complaint_invoice": invoices}
    order = ["complaint_refund", "complaint_invoice", *sorted(i for i in rows if i not in counted)]

    def ranked(size: int, cap: int = 100) -> list[dict[str, Any]]:
        return [
            cell_fact(
                "issue_count",
                rows[issue],
                counted.get(issue, []),
                group={"rank": rank, "row": f"issue:{issue}"},
                cap=cap,
            )
            for rank, issue in enumerate(order[:size], start=1)
        ]

    default = ask(owner, "top_issues", family_raw)
    assert answered(default, "top_issues") == ranked(5)
    assert default["filters"]["limit"] == 5
    for size in (1, 2, 10):
        assert answered(ask(owner, "top_issues", {**family_raw, "limit": size}), "top_issues") == (
            ranked(size)
        )
    capped = ask(owner, "top_issues", {**family_raw, "limit": 2}, limits=limits(max_citations=2))
    assert answered(capped, "top_issues") == ranked(2, cap=2)
    assert answered(
        ask(owner, "top_issues", family_raw, limits=limits(max_rows=35)), "top_issues"
    ) == ranked(5)
    abstained(
        ask(owner, "top_issues", family_raw, limits=limits(max_rows=34)),
        "top_issues",
        "row_limit_exceeded",
    )
    abstained(
        ask(owner, "top_issues", {**family_raw, "product": "router"}),
        "top_issues",
        "unsupported_filter",
        key="product",
        hidden=("router",),
    )
    assert dumped(ask(owner, "top_issues", family_raw)) == dumped(default)


def test_small_issue_counts_show_and_their_citations_are_withheld(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-small@example.com")
    seed_many(tenant, 5, COMPLAINT)
    owner = caller(tenant)
    raw = {**TREND_WINDOW, "issue": "complaint_refund"}
    group = {"row": "issue:complaint_refund"}
    (row,) = trend_reference(owner, raw)["rows"]
    assert row["cell"]["status"] == "insufficient_sample" and row["cell"]["numerator"] == 5

    count = ask(owner, "issue_aggregate", {"intent": "issue_count", "params": raw})
    assert answered(count, "issue_aggregate") == [
        fact("issue_count", 5, [], group=group, total=5, withheld=True)
    ]
    for intent, params in (("issue_rate", raw), ("issue_trend", {**raw, "grain": "day"})):
        abstained(
            ask(owner, "issue_aggregate", {"intent": intent, "params": params}),
            "issue_aggregate",
            "below_minimum_sample",
            "below_minimum_sample",
        )
    top = ask(owner, "top_issues", {**TREND_WINDOW, "family": "service_complaint", "limit": 1})
    assert answered(top, "top_issues") == [
        fact(
            "issue_count",
            5,
            [],
            group={"rank": 1, "row": "issue:complaint_refund"},
            total=5,
            withheld=True,
        )
    ]
    for tool, params in (
        ("issue_aggregate", {"intent": "issue_count", "params": {**TREND_WINDOW, "family": "qa"}}),
        ("top_issues", {**TREND_WINDOW, "family": "qa"}),
    ):
        abstained(
            ask(owner, tool, params),
            tool,
            "not_computable",
            "scorecard_results_unavailable",
        )


ROUTING_REFUSALS = [
    pytest.param("forecast", {}, "forecast_not_supported", None, None, id="forecast"),
    pytest.param("causal_question", {}, "causal_not_supported", None, None, id="causal"),
    pytest.param(
        "agent_ranking", {}, "individual_ranking_not_supported", None, None, id="agent-ranking"
    ),
    pytest.param("out_of_scope", {}, "out_of_scope", None, None, id="out-of-scope"),
    pytest.param("weather_report", {}, "out_of_scope", "intent_unknown", None, id="unknown"),
    pytest.param(
        "Issue_Count",
        {"issue": "complaint_refund"},
        "out_of_scope",
        "intent_unknown",
        None,
        id="intent-case",
    ),
    pytest.param("issue_count", {}, "invalid_request", "target_required", None, id="no-selector"),
    pytest.param(
        "issue_count",
        {"issue": "complaint_refund", "product": "router-9"},
        "unsupported_filter",
        "filter_unsupported",
        "product",
        id="nested-unsupported",
    ),
    pytest.param(
        "issue_count",
        {"issue": "complaint_refund", "workspace_id": "guess-4321"},
        "scope_not_allowed",
        None,
        "workspace_id",
        id="nested-scope-key",
    ),
    pytest.param(
        "issue_rate",
        {"issue": "complaint_refund", "grain": "day"},
        "invalid_request",
        "filter_unknown_key",
        "grain",
        id="grain-on-rate",
    ),
    pytest.param(
        "issue_count",
        {"issue": "no_such_issue"},
        "invalid_request",
        "filter_bad_value",
        "issue",
        id="bad-issue",
    ),
    pytest.param(
        "issue_count",
        {"issue": "complaint_refund", "agent_user_id": "agent-7"},
        "invalid_request",
        "filter_bad_value",
        "agent_user_id",
        id="bad-agent",
    ),
]


@pytest.mark.parametrize(("intent", "params", "reason", "detail", "key"), ROUTING_REFUSALS)
def test_issue_aggregate_passes_routing_refusals_through(
    client, email_outbox, intent, params, reason, detail, key
):
    tenant = Tenant(client, email_outbox, "oracle-refusals@example.com")
    decision = route_request(
        IntentRequest(intent, params),
        load_catalogue(),
        scope=CallerScope(workspace_id=tenant.workspace_id, team_ids=None, agent_user_ids=None),
        now=NOW,
    )
    assert (decision.reason.value, decision.detail, decision.key) == (reason, detail, key)
    payload = ask(caller(tenant), "issue_aggregate", {"intent": intent, "params": params})
    abstained(payload, "issue_aggregate", reason, detail, key, hidden=("router-9", "guess-4321"))


SQL_ONLY_INTENTS = [
    pytest.param(
        "cohort_comparison",
        {"issue": "complaint_refund", "compare_by": "language", "compare_values": ["fa", "en"]},
        id="cohort-comparison",
    ),
    pytest.param("coverage_status", {}, id="coverage-status"),
    pytest.param("evidence_examples", {"issue": "complaint_refund"}, id="evidence-examples"),
    pytest.param(
        "conversation_lookup",
        {"conversation_id": "0b6f2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e"},
        id="conversation-lookup",
    ),
]


@pytest.mark.parametrize(("intent", "params"), SQL_ONLY_INTENTS)
def test_answered_intents_outside_the_three_abstain(client, email_outbox, intent, params):
    tenant = Tenant(client, email_outbox, "oracle-intents@example.com")
    seed_many(tenant, 2, COMPLAINT)
    decision = route_request(
        IntentRequest(intent, params),
        load_catalogue(),
        scope=CallerScope(workspace_id=tenant.workspace_id, team_ids=None, agent_user_ids=None),
        now=NOW,
    )
    assert decision.reason is None
    payload = ask(caller(tenant), "issue_aggregate", {"intent": intent, "params": params})
    abstained(
        payload,
        "issue_aggregate",
        "intent_not_supported",
        hidden=("0b6f2c1a-0b4f-4e8a-9c3d-5f6a7b8c9d0e",),
    )


def test_the_stricter_cell_kind_hides_small_agent_counts(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-kind@example.com")
    agents = [add_user(f"agent-{index}") for index in range(5)]
    for agent in agents:
        seed_many(tenant, 2, COMPLAINT, agent_user_id=agent)
    owner = caller(tenant)
    raw = {**TREND_WINDOW, "issue": "complaint_refund"}
    group = {"row": "issue:complaint_refund"}
    (row,) = trend_reference(owner, raw)["rows"]
    assert row["cell"]["numerator"] == 10
    whole = ask(owner, "issue_aggregate", {"intent": "issue_count", "params": raw})
    assert answered(whole, "issue_aggregate") == [
        fact("issue_count", 10, [], group=group, total=10, withheld=True)
    ]
    filtered = {**raw, "agent_user_id": agents}
    (data_row,) = trend_reference(owner, filtered)["rows"]
    assert data_row["cell"]["numerator"] == 10
    abstained(
        ask(owner, "issue_aggregate", {"intent": "issue_count", "params": filtered}),
        "issue_aggregate",
        "below_minimum_sample",
        "below_minimum_sample",
    )
    family = {**TREND_WINDOW, "family": "service_complaint"}
    abstained(
        ask(owner, "top_issues", {**family, "agent_user_id": agents}),
        "top_issues",
        "below_minimum_sample",
        "below_minimum_sample",
    )
    top = ask(owner, "top_issues", {**family, "limit": 1})
    assert answered(top, "top_issues") == [
        fact(
            "issue_count",
            10,
            [],
            group={"rank": 1, "row": "issue:complaint_refund"},
            total=10,
            withheld=True,
        )
    ]


def test_a_withheld_row_abstains_and_its_own_agent_still_gets_it(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-withheld@example.com")
    agent = add_user("solo-agent")
    complaints = seed_many(tenant, 25, COMPLAINT, agent_user_id=agent)
    undecided = seed_many(tenant, 5, UNDECIDED, agent_user_id=agent)
    owner = caller(tenant)
    raw = {**TREND_WINDOW, "issue": "intent_report_dissatisfaction"}
    (row,) = trend_reference(owner, raw)["rows"]
    assert row["cell"]["status"] == "withheld"
    abstained(
        ask(owner, "issue_aggregate", {"intent": "issue_count", "params": raw}),
        "issue_aggregate",
        "withheld",
    )
    family = {**TREND_WINDOW, "family": "call_reason"}
    for params in (family, {**family, "limit": 1}):
        abstained(ask(owner, "top_issues", params), "top_issues", "withheld")

    self_view = caller(tenant, user_id=agent, role=WorkspaceRole.MEMBER, supervised=())
    (own,) = trend_reference(self_view, raw)["rows"]
    assert own["cell"]["status"] == "computed" and own["cell"]["numerator"] == 30
    mine = ask(self_view, "issue_aggregate", {"intent": "issue_count", "params": raw})
    assert answered(mine, "issue_aggregate") == [
        fact(
            "issue_count",
            30,
            complaints + undecided,
            group={"row": "issue:intent_report_dissatisfaction"},
        )
    ]
    rows = {r["issue"]: r for r in trend_reference(self_view, family)["rows"] if r["issue"]}
    counted = {"intent_report_dissatisfaction": complaints + undecided}
    order = [*counted, *sorted(issue for issue in rows if issue not in counted)]
    expected = [
        cell_fact(
            "issue_count",
            rows[issue],
            counted.get(issue, []),
            group={"rank": rank, "row": f"issue:{issue}"},
        )
        for rank, issue in enumerate(order[:5], start=1)
    ]
    assert answered(ask(self_view, "top_issues", family), "top_issues") == expected


def test_issue_tools_keep_to_the_record_scope_and_ignore_guessed_teams(client, email_outbox):
    mine, other = two_tenants(client, email_outbox, "oracle-issue-scope")
    t1, t2 = add_team(mine, "one"), add_team(mine, "two")
    foreign_team = add_team(other, "foreign")
    seed_many(mine, 6, COMPLAINT, team_id=t1)
    seed_many(mine, 4, COMPLAINT, team_id=t2)
    seed_many(other, 3, COMPLAINT, team_id=foreign_team)
    member = add_user("supervisor")
    scoped = caller(mine, user_id=member, role=WorkspaceRole.MEMBER, supervised=(t1,))
    owner = caller(mine)
    raw = {**TREND_WINDOW, "issue": "complaint_refund"}
    group = {"row": "issue:complaint_refund"}

    def count(context: WorkspaceContext, **extra: Any) -> dict[str, Any]:
        return ask(
            context, "issue_aggregate", {"intent": "issue_count", "params": {**raw, **extra}}
        )

    six = [fact("issue_count", 6, [], group=group, total=6, withheld=True)]
    assert answered(count(scoped), "issue_aggregate") == six
    assert answered(count(scoped, team_id=t1), "issue_aggregate") == six
    assert answered(count(owner), "issue_aggregate") == [
        fact("issue_count", 10, [], group=group, total=10, withheld=True)
    ]
    assert answered(count(owner, team_id=t2), "issue_aggregate") == [
        fact("issue_count", 4, [], group=group, total=4, withheld=True)
    ]

    refusal = route_request(
        IntentRequest("issue_count", {**raw, "team_id": t2}),
        load_catalogue(),
        scope=CallerScope(
            workspace_id=mine.workspace_id, team_ids=frozenset({t1}), agent_user_ids=None
        ),
        now=NOW,
    )
    assert refusal.reason.value == "outside_caller_scope"
    guesses = (t2, foreign_team, new_id())
    family = {**TREND_WINDOW, "family": "service_complaint"}
    for answers in (
        [count(scoped, team_id=guess) for guess in guesses],
        [ask(scoped, "top_issues", {**family, "team_id": guess}) for guess in guesses],
    ):
        for payload in answers:
            abstained(
                payload,
                payload["tool"],
                "outside_caller_scope",
                refusal.detail,
                refusal.key,
                hidden=(t2, foreign_team),
            )
        assert len({dumped(payload) for payload in answers}) == 1
    owner_guesses = [count(owner, team_id=guess) for guess in (foreign_team, new_id())]
    for payload in owner_guesses:
        abstained(payload, "issue_aggregate", "not_computable", "empty_denominator")
    assert len({dumped(payload) for payload in owner_guesses}) == 1


def determinism_plan(
    tenant: Tenant, teams: list[str | None], agents: list[str | None], version_id: str
):
    statuses = [
        MeetingStatus.COMPLETED,
        MeetingStatus.FAILED,
        MeetingStatus.PARTIAL,
        MeetingStatus.PROCESSING,
    ]
    outcomes = [
        ("passed", 100, 90, 0),
        ("failed", 100, 40, 0),
        ("not_applicable", 0, 0, 0),
        ("insufficient_evidence", 90, 30, 60),
        ("passed", 80, 75, 0),
    ]
    item_states = [ActionStatus.OPEN, ActionStatus.IN_PROGRESS, ActionStatus.DONE]
    priorities = ["low", "medium", "high"]
    ws, owner = tenant.workspace_id, tenant.owner_id
    plan: dict[str, list[dict[str, Any]]] = {
        "meetings": [],
        "revisions": [],
        "results": [],
        "items": [],
    }
    for index in range(32):
        at = FROM + index * HOUR + (index % 7) * timedelta(minutes=3)
        meeting_id, revision_id = new_id(), new_id()
        plan["meetings"].append(
            {
                "meeting_id": meeting_id,
                "at": at,
                "team": teams[index % len(teams)],
                "agent": agents[index % len(agents)],
                "status": statuses[index % len(statuses)],
            }
        )
        plan["revisions"].append({"meeting_id": meeting_id, "revision_id": revision_id})
        plan["results"].append(
            {
                "meeting_id": meeting_id,
                "revision_id": revision_id,
                "item": outcomes[index % len(outcomes)],
                "at": at + TEN_MINUTES,
                "result_id": new_id(),
                "input_digest": uuid.uuid4().hex * 2,
            }
        )
        for offset in range(2):
            plan["items"].append(
                {
                    "meeting_id": meeting_id,
                    "due": datetime(2026, 10, 1 + (index + offset) % 14, 6 * offset, tzinfo=UTC),
                    "status": item_states[(index + offset) % len(item_states)],
                    "priority": priorities[(index * 2 + offset) % len(priorities)],
                    "item_id": new_id(),
                }
            )

    def build(reverse: bool) -> None:
        def ordered(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return list(reversed(rows)) if reverse else rows

        persist(
            *(
                meeting(
                    ws,
                    owner,
                    at=m["at"],
                    team=m["team"],
                    agent=m["agent"],
                    status=m["status"],
                    meeting_id=m["meeting_id"],
                )
                for m in ordered(plan["meetings"])
            ),
            *(revision(ws, r["meeting_id"], r["revision_id"]) for r in ordered(plan["revisions"])),
            *(
                score(
                    ws,
                    r["meeting_id"],
                    version_id,
                    r["revision_id"],
                    r["item"],
                    at=r["at"],
                    result_id=r["result_id"],
                    input_digest=r["input_digest"],
                )
                for r in ordered(plan["results"])
            ),
            *(
                task(
                    ws,
                    i["meeting_id"],
                    i["due"],
                    status=i["status"],
                    priority=i["priority"],
                    item_id=i["item_id"],
                )
                for i in ordered(plan["items"])
            ),
        )

    return build


def wipe(workspace_id: str) -> None:
    with inspection_session_factory()() as db:
        for model in (ScorecardResultRecord, ActionItem, TranscriptRevision, Meeting):
            db.execute(delete(model).where(model.workspace_id == workspace_id))
        db.commit()


def test_the_same_data_in_another_insert_order_gives_the_same_bytes(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-determinism@example.com")
    teams = [add_team(tenant, "one"), add_team(tenant, "two"), None]
    agents = [add_user("agent-x"), add_user("agent-y"), None]
    version = card(tenant.workspace_id)
    persist(version)
    build = determinism_plan(tenant, teams, agents, version.id)
    owner = caller(tenant)
    calls = [
        *(
            ("conversation_count", {**WINDOW, "group_by": g})
            for g in ("none", "status", "team_id", "day")
        ),
        *(
            ("action_items_due", {"due_before": DUE_BEFORE, "group_by": g})
            for g in ("none", "status", "priority", "team_id")
        ),
        ("scorecard_summary", {**WINDOW, "scorecard_version_id": version.id}),
    ]

    def answers() -> list[str]:
        payloads = [
            ask(owner, name, params, limits=limits(max_citations=7)) for name, params in calls
        ]
        for (name, _params), payload in zip(calls, payloads, strict=True):
            assert answered(payload, name)
        return [dumped(payload) for payload in payloads]

    build(False)
    first = answers()
    assert answers() == first
    wipe(tenant.workspace_id)
    build(True)
    assert answers() == first


def test_the_tools_issue_only_reads_and_never_end_the_transaction(client, email_outbox):
    data = conversations(client, email_outbox, "oracle-reads")
    version = card(data.mine.workspace_id)
    persist(version)
    scored_calls(data.mine, version.id, [("passed", 100, 90, 0)] * 3)
    seed_many(data.mine, 2, COMPLAINT)
    owner = caller(data.mine)
    calls = [
        ("conversation_count", {**WINDOW, "group_by": "day"}),
        ("action_items_due", {"due_before": DUE_BEFORE, "group_by": "status"}),
        ("scorecard_summary", {**WINDOW, "scorecard_version_id": version.id}),
        (
            "issue_aggregate",
            {"intent": "issue_count", "params": {**TREND_WINDOW, "issue": "complaint_refund"}},
        ),
        (
            "issue_aggregate",
            {"intent": "issue_trend", "params": {**TREND_WINDOW, "issue": "complaint_refund"}},
        ),
        ("top_issues", {**TREND_WINDOW, "family": "service_complaint"}),
        ("no_such_tool", {}),
    ]
    with session_for(owner) as db:
        db.execute(text("SELECT 1"))
        with watched(db) as seen:
            payloads = [run(db, owner, name, params) for name, params in calls]
        assert db.execute(text("SELECT 1")).scalar_one() == 1
    assert answered(payloads[0], "conversation_count")
    assert seen.statements
    for statement in seen.statements:
        assert READ_ONLY.match(statement), statement
    assert seen.ends == []


def test_pending_objects_of_the_caller_are_never_flushed(client, email_outbox):
    data = conversations(client, email_outbox, "oracle-pending")
    owner = caller(data.mine)
    with session_for(owner) as db:
        db.execute(text("SELECT 1"))
        probe = Team(
            workspace_id=data.mine.workspace_id,
            name="oracle-pending",
            slug=f"oracle-pending-{uuid.uuid4().hex[:8]}",
        )
        db.add(probe)
        with watched(db) as seen:
            payload = run(db, owner, "conversation_count", dict(WINDOW))
        assert probe in db.new
    assert answered(payload, "conversation_count") == [fact("conversations", 4, data.live)]
    for statement in seen.statements:
        assert READ_ONLY.match(statement), statement
    assert seen.ends == []
    with inspection_session_factory()() as check:
        stored_teams = check.scalar(
            select(func.count()).select_from(Team).where(Team.name == "oracle-pending")
        )
    assert stored_teams == 0


def test_the_time_budget_discards_the_answer(client, email_outbox, monkeypatch):
    data = conversations(client, email_outbox, "oracle-budget")
    owner = caller(data.mine)
    assert answered(ask(owner, "conversation_count", dict(WINDOW)), "conversation_count") == [
        fact("conversations", 4, data.live)
    ]
    real = time.monotonic
    ticks = iter(range(10**9))

    def jumping() -> float:
        module = sys._getframe(1).f_globals.get("__name__", "")
        if module.startswith(("backend.services.copilot_tool", "backend.copilot_tools")):
            return 3600.0 * next(ticks)
        return real()

    monkeypatch.setattr(time, "monotonic", jumping)
    if getattr(copilot_tool_service, "monotonic", None) is real:
        monkeypatch.setattr(copilot_tool_service, "monotonic", jumping)
    abstained(
        ask(owner, "conversation_count", dict(WINDOW)),
        "conversation_count",
        "time_limit_exceeded",
    )
    abstained(
        ask(owner, "action_items_due", {"due_before": DUE_BEFORE}),
        "action_items_due",
        "time_limit_exceeded",
    )


@pytest.mark.postgres
def test_a_cancelled_statement_abstains_and_the_session_stays_usable(client, email_outbox):
    tenant = Tenant(client, email_outbox, "oracle-timeout@example.com")
    calls = [meeting(tenant.workspace_id, tenant.owner_id, at=FROM + i * HOUR) for i in range(2)]
    ids = [call.id for call in calls]
    persist(*calls)
    owner = caller(tenant)
    tight = limits(statement_timeout_ms=300, time_budget_ms=30000)
    holder = inspection_session_factory()()
    try:
        holder.execute(text("LOCK TABLE meetings IN ACCESS EXCLUSIVE MODE"))
        with session_for(owner) as db:
            db.execute(text("SELECT set_config('lock_timeout', '8s', true)"))
            db.execute(text("SELECT set_config('statement_timeout', '987654ms', true)"))
            started = time.monotonic()
            payload = run(db, owner, "conversation_count", dict(WINDOW), limits=tight)
            elapsed = time.monotonic() - started
            abstained(payload, "conversation_count", "time_limit_exceeded")
            assert elapsed < 6
            assert db.execute(text("SELECT 1")).scalar_one() == 1
            assert db.execute(text("SHOW statement_timeout")).scalar_one() == "987654ms"
            holder.rollback()
            again = run(db, owner, "conversation_count", dict(WINDOW), limits=tight)
            assert answered(again, "conversation_count") == [fact("conversations", 2, ids)]
            assert db.execute(text("SHOW statement_timeout")).scalar_one() == "987654ms"
    finally:
        holder.rollback()
        holder.close()


@pytest.mark.postgres
def test_rls_as_the_app_role_keeps_another_workspace_out(client, email_outbox):
    mine, other = two_tenants(client, email_outbox, "oracle-rls")
    a_calls = [meeting(mine.workspace_id, mine.owner_id, at=FROM + i * HOUR) for i in range(2)]
    b_calls = [meeting(other.workspace_id, other.owner_id, at=FROM + i * HOUR) for i in range(3)]
    a_items = [
        task(mine.workspace_id, a_calls[0].id, datetime(2026, 10, 5, tzinfo=UTC)),
        task(mine.workspace_id, a_calls[1].id, datetime(2026, 10, 12, tzinfo=UTC)),
    ]
    b_items = [
        task(other.workspace_id, b_calls[0].id, datetime(2026, 10, 5, tzinfo=UTC)),
        task(other.workspace_id, b_calls[1].id, datetime(2026, 10, 6, tzinfo=UTC)),
    ]
    a_card, b_card = card(mine.workspace_id), card(other.workspace_id)
    persist(*a_calls, *b_calls, *a_items, *b_items, a_card, b_card)
    a_scores = scored_calls(mine, a_card.id, [("passed", 100, 90, 0)] * 30)
    b_scores = scored_calls(other, b_card.id, [("passed", 100, 90, 0)] * 30)
    seed_many(mine, 2, COMPLAINT)
    b_trend = seed_many(other, 3, COMPLAINT)
    foreign = [
        other.workspace_id,
        b_card.id,
        *(call.id for call in b_calls),
        *(item.id for item in b_items),
        *(value for triple in b_scores for value in triple),
        *b_trend,
    ]
    owner = caller(mine)
    a_meetings = [call.id for call in a_calls] + [triple[0] for triple in a_scores]
    a_results = [triple[2] for triple in a_scores]
    with session_for(owner) as db:
        assert db.execute(text("SELECT current_user")).scalar_one() == TEST_APP_ROLE
        assert (
            db.execute(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            ).scalar_one()
            is False
        )
        answers = {
            "conversation_count": run(db, owner, "conversation_count", dict(WINDOW)),
            "action_items_due": run(db, owner, "action_items_due", {"due_before": DUE_BEFORE}),
            "scorecard_summary": run(
                db, owner, "scorecard_summary", {**WINDOW, "scorecard_version_id": a_card.id}
            ),
            "issue_aggregate": run(
                db,
                owner,
                "issue_aggregate",
                {"intent": "issue_count", "params": {**TREND_WINDOW, "issue": "complaint_refund"}},
            ),
            "top_issues": run(
                db, owner, "top_issues", {**TREND_WINDOW, "family": "service_complaint", "limit": 1}
            ),
        }
        foreign_card = run(
            db, owner, "scorecard_summary", {**WINDOW, "scorecard_version_id": b_card.id}
        )
        assert db.execute(text("SELECT current_user")).scalar_one() == TEST_APP_ROLE
    assert answered(answers["conversation_count"], "conversation_count") == [
        fact("conversations", 32, a_meetings)
    ]
    assert answered(answers["action_items_due"], "action_items_due") == [
        fact("action_items_due", 2, [item.id for item in a_items]),
        fact("action_items_overdue", 1, [a_items[0].id]),
    ]
    assert answered(answers["scorecard_summary"], "scorecard_summary") == [
        fact("scorecard_results", 30, a_results),
        fact("scorecard_results", 30, a_results, group={"outcome": "passed"}),
        fact("mean_score_percent", 90.0, a_results, parts={"numerator": 90, "denominator": 1}),
    ]
    group = {"row": "issue:complaint_refund"}
    assert answered(answers["issue_aggregate"], "issue_aggregate") == [
        fact("issue_count", 2, [], group=group, total=2, withheld=True)
    ]
    assert answered(answers["top_issues"], "top_issues") == [
        fact("issue_count", 2, [], group={"rank": 1, **group}, total=2, withheld=True)
    ]
    abstained(foreign_card, "scorecard_summary", "below_minimum_sample", hidden=(b_card.id,))
    for payload in (*answers.values(), foreign_card):
        text_form = json.dumps(payload)
        for value in foreign:
            assert value not in text_form


def test_tool_schemas_cannot_be_changed_through_their_output():
    before = dumped({"schemas": tool_schemas()})
    changed = tool_schemas()
    changed[0]["input_schema"]["properties"]["injected"] = {"type": "string"}
    changed[0]["input_schema"]["additionalProperties"] = True
    changed.append({"name": "free_sql", "description": "x", "input_schema": {}})
    assert dumped({"schemas": tool_schemas()}) == before
    assert "injected" not in TOOLS[changed[0]["name"]].input_schema["properties"]
    assert TOOLS[changed[0]["name"]].input_schema["additionalProperties"] is False
