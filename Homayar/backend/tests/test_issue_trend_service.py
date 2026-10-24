"""HOM-80 (WP15-T02): scoped issue-trend aggregates, ``compute_issue_trends``.

The population is the coverage processing bucket ``transcribed`` of the window, ANDed
with the caller's record scope and the population filters.  Media groups are decided
over the whole workspace; only the counted copy of a group can count.  The rules come
from ``backend.issue_trends`` (``judge_conversation``, ``tally``, ``build_rows``).
Rows are seeded with DBA writes so every state sits at a fixed instant.
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

import pytest
from sqlalchemy import event, text, update
from sqlalchemy.orm import Session

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
from backend.issue_trends import (
    SAMPLE_POLICY_V1,
    MissingBucket,
    TrendFilters,
    load_definition,
    trend_provenance,
    validate_filters,
)
from backend.security.authorization import RecordAccess
from backend.services.coverage_service import CoverageWindow, compute_coverage
from backend.services.issue_trend_service import (
    TREND_DEFAULT_TIMEZONE,
    TREND_GRAINS,
    TrendSeriesError,
    compute_issue_trends,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_coverage import Tenant
from backend.tests.test_issue_trends_counting import ok, stored

DEFINITION = load_definition("trends", 1)
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
BASE = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
DAY = timedelta(days=1)
WINDOW = {"from": BASE.isoformat(), "to": (BASE + DAY).isoformat()}

COMPLAINT = stored(intent=ok("report_dissatisfaction"), topic=ok("refund"), outcome=ok("escalated"))
BILLING = stored(intent=ok("resolve_billing"), topic=ok("invoice"))
ABSTAINED = stored(intent=ok("request_support"), topic="insufficient_evidence")

ANSWER_KEYS = {
    "trends_version",
    "definition",
    "taxonomy",
    "extraction_policies",
    "validator_versions",
    "sample_policy",
    "window",
    "filters",
    "scope",
    "series",
    "population",
    "rows",
    "points",
    "disclosure_policy",  # HOM-82
    "disclosure",  # HOM-82
}


# --- seeding --------------------------------------------------------------------------------


class Seeded(NamedTuple):
    meeting_id: str | None
    ingestion_id: str


def _extraction_row(
    workspace_id: str, meeting_id: str, run_id: str, result: Any
) -> AnalysisExtraction:
    meta = result if isinstance(result, dict) else {}
    return AnalysisExtraction(
        workspace_id=workspace_id,
        meeting_id=meeting_id,
        analysis_run_id=run_id,
        taxonomy_id=str(meta.get("taxonomy_id", "conversation")),
        taxonomy_version=int(meta.get("taxonomy_version", 1)),
        taxonomy_digest=str(meta.get("taxonomy_digest", "0" * 64)),
        validator_version=str(meta.get("validator_version", "unknown")),
        policy_version=str(meta.get("policy_version", "unknown")),
        policy_digest=str(meta.get("policy_digest", "0" * 64)),
        valid=bool(meta.get("valid", False)),
        needs_review=bool(meta.get("needs_review", False)),
        review_count=0,
        result=result,
    )


def seed(
    tenant: Tenant,
    *,
    received_at: datetime = BASE,
    ledger: str = "accepted",
    with_meeting: bool = True,
    status: MeetingStatus = MeetingStatus.COMPLETED,
    deleted: bool = False,
    segments: int | None = 2,
    revision_mock: bool = False,
    run: str | None = "current",
    run_state: StageState = StageState.COMPLETED,
    run_mock: bool = False,
    extraction: Any = COMPLAINT,
    old_runs: list[Any] | None = None,
    team_id: str | None = None,
    agent_user_id: str | None = None,
    created_by: str | None = None,
    language: str = "fa",
    source_type: IngestionSourceType = IngestionSourceType.FILE_UPLOAD,
    duplicate_of: str | None = None,
    workspace_id: str | None = None,
    organization_id: str | None = None,
) -> Seeded:
    """One ledger row and, when asked, its meeting, current revision, current run and
    the run's extraction row.  ``run``: ``"current"`` (of the current revision),
    ``"stale"`` (of an older revision) or ``None`` (no current run).  ``extraction``:
    the stored ``result`` JSON, or ``None`` for no row.  ``old_runs``: results of older,
    non-current runs that must never be read."""
    workspace_id = workspace_id or tenant.workspace_id
    organization_id = organization_id or tenant.organization_id
    with inspection_session_factory()() as db:
        meeting_id = None
        if ledger == "accepted" and with_meeting:
            meeting = Meeting(
                workspace_id=workspace_id,
                created_by_user_id=created_by or tenant.owner_id,
                title="Trend call",
                status=status,
                duration_seconds=30,
                deleted_at=received_at if deleted else None,
                team_id=team_id,
                agent_user_id=agent_user_id,
                language=language,
            )
            db.add(meeting)
            db.flush()
            meeting_id = meeting.id
        record = IngestionRecord(
            organization_id=organization_id,
            workspace_id=workspace_id,
            source_type=source_type,
            source_provider="test",
            external_event_id=str(uuid.uuid4()),
            payload_hash="0" * 64,
            status=IngestionStatus.ACCEPTED if ledger == "accepted" else IngestionStatus.REJECTED,
            reason=None if ledger == "accepted" else "unsupported_media",
            received_at=received_at,
            meeting_id=meeting_id,
            duplicate_of_id=duplicate_of,
        )
        db.add(record)
        db.flush()
        if meeting_id is not None and segments is not None:

            def revision(number: int, *, current: bool) -> TranscriptRevision:
                row = TranscriptRevision(
                    workspace_id=workspace_id,
                    meeting_id=meeting_id,
                    revision_number=number,
                    source=TranscriptSource.ASR,
                    reason="seed",
                    language=language,
                    segment_count=segments,
                    is_mock=revision_mock,
                    is_current=current,
                )
                db.add(row)
                db.flush()
                return row

            if run == "stale":
                analysed = revision(1, current=False)
                current = revision(2, current=True)
            else:
                current = analysed = revision(1, current=True)
            for result in old_runs or []:
                old = AnalysisRun(
                    workspace_id=workspace_id,
                    meeting_id=meeting_id,
                    transcript_revision_id=current.id,
                    state=StageState.COMPLETED,
                    is_current=False,
                )
                db.add(old)
                db.flush()
                db.add(_extraction_row(workspace_id, meeting_id, old.id, result))
            if run is not None:
                analysis = AnalysisRun(
                    workspace_id=workspace_id,
                    meeting_id=meeting_id,
                    transcript_revision_id=analysed.id,
                    state=run_state,
                    is_mock=run_mock,
                    is_current=True,
                )
                db.add(analysis)
                db.flush()
                if extraction is not None:
                    db.add(_extraction_row(workspace_id, meeting_id, analysis.id, extraction))
        db.commit()
        return Seeded(meeting_id, record.id)


def add_team(tenant: Tenant, name: str) -> str:
    with inspection_session_factory()() as db:
        team = Team(
            workspace_id=tenant.workspace_id, name=name, slug=f"{name}-{uuid.uuid4().hex[:8]}"
        )
        db.add(team)
        db.commit()
        return team.id


def add_user(name: str) -> str:
    with inspection_session_factory()() as db:
        user = User(email=f"{name}-{uuid.uuid4().hex[:8]}@example.com", display_name=name)
        db.add(user)
        db.commit()
        return user.id


def unrestricted(user_id: str) -> RecordAccess:
    return RecordAccess(
        user_id=user_id,
        unrestricted=True,
        supervised_team_ids=frozenset(),
        agent_team_ids=frozenset(),
    )


def restricted(user_id: str, *supervised: str) -> RecordAccess:
    return RecordAccess(
        user_id=user_id,
        unrestricted=False,
        supervised_team_ids=frozenset(supervised),
        agent_team_ids=frozenset(),
    )


def filters_of(**raw: Any) -> TrendFilters:
    raw.setdefault("from", WINDOW["from"])
    raw.setdefault("to", WINDOW["to"])
    return validate_filters(raw, DEFINITION, now=NOW)


def trends(
    tenant: Tenant,
    *,
    access: RecordAccess | None = None,
    grain: str = "none",
    timezone: str = TREND_DEFAULT_TIMEZONE,
    **raw: Any,
) -> dict[str, Any]:
    with inspection_session_factory()() as db:
        return compute_issue_trends(
            db,
            workspace_id=tenant.workspace_id,
            access=access or unrestricted(tenant.owner_id),
            filters=filters_of(**raw),
            now=NOW,
            grain=grain,
            timezone=timezone,
        )


def row(answer: dict[str, Any], *, family: str, issue: str | None = None) -> dict[str, Any]:
    return next(r for r in answer["rows"] if r["family"] == family and r["issue"] == issue)


def included(answer: dict[str, Any]) -> int:
    return answer["population"]["included"]


def excluded(answer: dict[str, Any]) -> int:
    return answer["population"]["excluded"]["duplicate_media"]


def assert_rows_reconcile(population: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    for item in rows:
        if item["family"] == "qa":
            continue
        cell = item["cell"]
        assert cell["denominator"] + sum(item["missing"].values()) == population["included"], item


def assert_points_add_up(answer: dict[str, Any]) -> None:
    """SPEC section 5 as refined by A6."""
    points = answer["points"]
    assert points, "a series was expected"
    assert sum(p["population"]["included"] for p in points) == included(answer)
    assert sum(p["population"]["excluded"]["duplicate_media"] for p in points) == excluded(answer)
    for point in points:
        assert_rows_reconcile(point["population"], point["rows"])
    for index, top in enumerate(answer["rows"]):
        cells = [p["rows"][index] for p in points]
        assert all((c["family"], c["issue"]) == (top["family"], top["issue"]) for c in cells)
        assert sum(c["cell"]["denominator"] for c in cells) == top["cell"]["denominator"]
        for bucket, count in top["missing"].items():
            assert sum(c["missing"][bucket] for c in cells) == count, (top, bucket)
        for read in (
            lambda r: r["cell"]["numerator"],
            lambda r: r["pending_review"],
            lambda r: r["absent_pending_review"],
        ):
            values = [read(c) for c in cells]
            if read(top) is not None and None not in values:
                assert sum(values) == read(top), top


# --- the answer -----------------------------------------------------------------------------


def test_the_answer_has_the_documented_shape(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-shape@example.com")
    seed(tenant)

    answer = trends(tenant)

    assert set(answer) == ANSWER_KEYS
    assert "coverage" not in answer  # HOM-81 adds it
    for key, value in trend_provenance(DEFINITION, SAMPLE_POLICY_V1).items():
        assert answer[key] == value, key
    expected_filters = filters_of()
    assert answer["window"] == expected_filters.window.to_json()
    assert answer["window"]["from"].endswith("+00:00")
    assert answer["filters"] == expected_filters.to_json()
    assert answer["scope"] == {"restricted": False}
    assert answer["series"] == {
        "grain": "none",
        "timezone": "Asia/Tehran",
        "calendar": "gregorian",
        "week_starts_on": "saturday",
    }
    assert answer["points"] == []
    assert answer["population"] == {"included": 1, "excluded": {"duplicate_media": 0}}
    json.dumps(answer)  # plain JSON


def test_one_conversation_counts_once_per_issue_and_family(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-once@example.com")
    seed(
        tenant,
        extraction=stored(
            intent=ok("report_dissatisfaction"),
            topic=[("refund", "explicit", 0.9), ("invoice", "explicit", 0.9)],
        ),
    )

    answer = trends(tenant)

    assert included(answer) == 1
    for issue in ("complaint_refund", "complaint_invoice"):
        assert row(answer, family="service_complaint", issue=issue)["cell"]["numerator"] == 1
    family = row(answer, family="service_complaint")
    assert family["cell"]["numerator"] == 1 and family["cell"]["denominator"] == 1
    reason = row(answer, family="call_reason", issue="intent_report_dissatisfaction")
    assert reason["cell"]["numerator"] == 1
    assert (
        row(answer, family="call_reason", issue="intent_resolve_billing")["cell"]["numerator"] == 0
    )
    assert_rows_reconcile(answer["population"], answer["rows"])


def test_the_qa_family_is_never_zero(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-qa@example.com")
    seed(tenant)

    qa = row(trends(tenant), family="qa")

    assert qa["cell"]["status"] == "not_computable"
    assert qa["cell"]["reason"] == "scorecard_results_unavailable"
    assert qa["cell"]["numerator"] is None
    assert qa["cell"]["denominator"] == 1
    assert qa["pending_review"] is None and qa["missing"] == {}


# --- deny-case 1: another workspace ---------------------------------------------------------


def test_another_workspace_is_never_counted(client, email_outbox):
    mine = Tenant(client, email_outbox, "trend-mine@example.com")
    other = Tenant(client, email_outbox, "trend-other@example.com")
    seed(other)
    seed(other)

    assert included(trends(mine)) == 0
    # allow-control: the same data in the caller's workspace is counted
    seed(mine)
    assert included(trends(mine)) == 1
    assert included(trends(other)) == 2


# --- deny-cases 2 and 3: record scope -------------------------------------------------------


def test_record_scope_limits_the_population(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-scope@example.com")
    team_a, team_b = add_team(tenant, "a"), add_team(tenant, "b")
    member = add_user("member")
    seed(tenant, team_id=team_a)  # a supervised team: counted
    seed(tenant, team_id=team_b, agent_user_id=member)  # attributed to the member: counted
    seed(tenant, team_id=team_b)  # another team, not theirs: never counted
    seed(tenant)  # no team, not theirs: never counted
    access = restricted(member, team_a)

    scoped = trends(tenant, access=access)
    assert included(scoped) == 2
    assert scoped["scope"] == {"restricted": True}
    # allow-control: the owner sees all four
    assert included(trends(tenant)) == 4


def test_a_team_filter_outside_the_scope_only_narrows(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-narrow@example.com")
    team_a, team_b = add_team(tenant, "a"), add_team(tenant, "b")
    member = add_user("member")
    seed(tenant, team_id=team_a)
    seed(tenant, team_id=team_b)
    seed(tenant, team_id=team_b)

    narrowed = trends(tenant, access=restricted(member, team_a), team_id=team_b)
    assert included(narrowed) == 0 and excluded(narrowed) == 0
    assert narrowed["filters"]["team_id"] == [team_b]
    # allow-control: the owner with the same filter sees them
    assert included(trends(tenant, team_id=team_b)) == 2


# --- deny-cases 8 and 9: duplicate media ----------------------------------------------------


def test_a_second_copy_of_the_same_media_is_excluded(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-dup@example.com")
    first = seed(tenant, received_at=BASE)
    seed(tenant, received_at=BASE + timedelta(hours=1), duplicate_of=first.ingestion_id)

    answer = trends(tenant)
    assert included(answer) == 1 and excluded(answer) == 1
    assert row(answer, family="service_complaint")["cell"]["numerator"] == 1
    assert_rows_reconcile(answer["population"], answer["rows"])
    # allow-control: two different media are counted twice
    seed(tenant, received_at=BASE + timedelta(hours=2))
    assert included(trends(tenant)) == 2


def test_the_counted_copy_is_the_earliest_transcribed_member(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-counted@example.com")
    failed = seed(tenant, status=MeetingStatus.FAILED, segments=None, extraction=None)
    later = seed(
        tenant,
        received_at=BASE + timedelta(hours=1),
        duplicate_of=failed.ingestion_id,
        extraction=BILLING,
    )
    seed(tenant, received_at=BASE + timedelta(hours=2), duplicate_of=failed.ingestion_id)

    answer = trends(tenant)
    assert included(answer) == 1 and excluded(answer) == 1
    # the later copy (billing) is the one counted, not the latest (complaint)
    assert (
        row(answer, family="call_reason", issue="intent_resolve_billing")["cell"]["numerator"] == 1
    )
    assert row(answer, family="service_complaint")["cell"]["numerator"] == 0
    assert later.meeting_id is not None


def test_media_groups_follow_the_chain_to_its_root(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-chain@example.com")
    a = seed(tenant, deleted=True)
    b = seed(tenant, received_at=BASE + timedelta(hours=1), duplicate_of=a.ingestion_id)
    seed(tenant, received_at=BASE + timedelta(hours=2), duplicate_of=b.ingestion_id)

    answer = trends(tenant)
    # A, B and C are one group: B counts, C is excluded (keyed by duplicate_of_id they
    # would be two groups and both could count)
    assert included(answer) == 1 and excluded(answer) == 1


@contextmanager
def statement_deadline(db: Session, seconds: int) -> Iterator[None]:
    """Make a statement that runs longer than ``seconds`` fail instead of hanging:
    ``statement_timeout`` on Postgres, ``interrupt()`` from a timer on SQLite."""
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text(f"SET LOCAL statement_timeout = '{seconds}s'"))
        yield
        return
    raw = db.connection().connection.dbapi_connection
    timer = threading.Timer(seconds, raw.interrupt)
    timer.start()
    try:
        yield
    finally:
        timer.cancel()


def test_a_duplicate_of_loop_ends_and_each_row_is_its_own_group(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-loop@example.com")
    a = seed(tenant)
    b = seed(tenant, received_at=BASE + timedelta(hours=1))
    c = seed(tenant, received_at=BASE + timedelta(hours=2), duplicate_of=a.ingestion_id)
    # a normal group too: with no root row at all, Postgres skips the recursive scan
    d = seed(tenant, received_at=BASE + timedelta(hours=3))
    seed(tenant, received_at=BASE + timedelta(hours=4), duplicate_of=d.ingestion_id)
    with inspection_session_factory()() as db:  # no caller can make a loop: a direct edit
        for row, target in ((a, b), (b, a)):
            db.execute(
                update(IngestionRecord)
                .where(IngestionRecord.id == row.ingestion_id)
                .values(duplicate_of_id=target.ingestion_id)
            )
        db.commit()

    with inspection_session_factory()() as db, statement_deadline(db, 10):
        answer = compute_issue_trends(
            db,
            workspace_id=tenant.workspace_id,
            access=unrestricted(tenant.owner_id),
            filters=filters_of(),
            now=NOW,
            grain="day",
        )
    # a row in a loop has no root: A, B and C (which links into the loop) are each a
    # group; D and its copy are one group as usual
    assert included(answer) == 4 and excluded(answer) == 1
    assert_points_add_up(answer)
    assert c.meeting_id is not None


def test_a_counted_copy_outside_the_window_or_filter_excludes_the_member(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-outside@example.com")
    team_a, team_b = add_team(tenant, "a"), add_team(tenant, "b")
    early = seed(tenant, received_at=BASE - DAY, team_id=team_a)
    seed(
        tenant,
        received_at=BASE + timedelta(hours=1),
        duplicate_of=early.ingestion_id,
        team_id=team_a,
    )
    other = seed(tenant, received_at=BASE + timedelta(hours=2), team_id=team_a)
    seed(
        tenant,
        received_at=BASE + timedelta(hours=3),
        duplicate_of=other.ingestion_id,
        team_id=team_b,
    )

    answer = trends(tenant)
    # the first group's counted copy was received the day before: not counted here
    assert included(answer) == 1 and excluded(answer) == 2
    team_b_only = trends(tenant, team_id=team_b)
    assert included(team_b_only) == 0 and excluded(team_b_only) == 1
    previous_day = trends(tenant, **{"from": (BASE - DAY).isoformat(), "to": BASE.isoformat()})
    assert included(previous_day) == 1 and excluded(previous_day) == 0


def test_a_counted_copy_outside_the_scope_shows_only_as_excluded(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-dup-scope@example.com")
    team_a, team_b = add_team(tenant, "a"), add_team(tenant, "b")
    member = add_user("member")
    hidden = seed(tenant, team_id=team_b, extraction=BILLING)
    visible = seed(
        tenant,
        received_at=BASE + timedelta(hours=1),
        team_id=team_a,
        duplicate_of=hidden.ingestion_id,
    )

    scoped = trends(tenant, access=restricted(member, team_a))
    assert included(scoped) == 0 and excluded(scoped) == 1
    assert all(r["cell"]["denominator"] == 0 for r in scoped["rows"] if r["family"] != "qa")
    text = json.dumps(scoped)
    for identifier in (*hidden, *visible, team_b):
        assert identifier not in text
    # allow-control: an unrestricted caller counts the counted copy
    full = trends(tenant)
    assert included(full) == 1 and excluded(full) == 1
    assert row(full, family="call_reason", issue="intent_resolve_billing")["cell"]["numerator"] == 1


# --- deny-case 10: only the current run -----------------------------------------------------


def test_an_older_run_is_never_read(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-runs@example.com")
    seed(tenant, run=None, old_runs=[COMPLAINT, COMPLAINT])

    answer = trends(tenant)
    family = row(answer, family="service_complaint")
    assert family["cell"]["numerator"] == 0
    assert family["missing"][MissingBucket.ANALYSIS_UNAVAILABLE.value] == 1
    # allow-control: the current run is counted, its older runs are not
    seed(tenant, extraction=BILLING, old_runs=[COMPLAINT])
    answer = trends(tenant)
    assert (
        row(answer, family="call_reason", issue="intent_resolve_billing")["cell"]["numerator"] == 1
    )
    assert row(answer, family="service_complaint")["cell"]["numerator"] == 0


# --- deny-case 11: the population -----------------------------------------------------------


def test_only_transcribed_conversations_are_in_the_population(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-population@example.com")
    seed(tenant, deleted=True)
    seed(tenant, status=MeetingStatus.PROCESSING)
    seed(tenant, status=MeetingStatus.FAILED)
    seed(tenant, segments=None)  # no current revision
    seed(tenant, segments=0)
    seed(tenant, ledger="rejected")
    with inspection_session_factory()() as db:  # a meeting without a ledger row
        db.add(
            Meeting(
                workspace_id=tenant.workspace_id,
                created_by_user_id=tenant.owner_id,
                title="legacy",
                status=MeetingStatus.COMPLETED,
            )
        )
        db.commit()

    assert included(trends(tenant)) == 0
    # allow-control: completed and partial meetings with a transcript are included
    seed(tenant)
    seed(tenant, status=MeetingStatus.PARTIAL)
    assert included(trends(tenant)) == 2


def test_facts_map_to_the_conversation_buckets(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-buckets@example.com")
    seed(tenant, run=None)
    seed(tenant, run_state=StageState.SKIPPED)
    seed(tenant, run="stale")
    seed(tenant, revision_mock=True)
    seed(tenant, run_mock=True)
    seed(tenant, extraction=None)
    seed(tenant, extraction=dict(COMPLAINT, validator_version="analysis-taxonomy-validator/9"))
    seed(tenant, extraction={"not": "a row"})
    seed(tenant, run_state=StageState.PARTIAL, extraction=ABSTAINED)

    missing = row(trends(tenant), family="call_reason", issue="intent_request_support")["missing"]
    assert missing[MissingBucket.ANALYSIS_UNAVAILABLE.value] == 2
    assert missing[MissingBucket.ANALYSIS_STALE.value] == 1
    assert missing[MissingBucket.MOCK_ANALYSIS.value] == 2
    assert missing[MissingBucket.EXTRACTION_UNAVAILABLE.value] == 1
    assert missing[MissingBucket.VERSION_MISMATCH.value] == 1
    assert missing[MissingBucket.EXTRACTION_UNREADABLE.value] == 1
    support = row(trends(tenant), family="call_reason", issue="intent_request_support")
    assert support["cell"]["numerator"] == 1  # the partial run is judged on its row
    refund = row(trends(tenant), family="service_complaint", issue="complaint_refund")
    assert refund["cell"]["denominator"] == 1  # absent: the intent is not a complaint


# --- deny-case 13: sample minimum -----------------------------------------------------------


def test_a_cohort_below_the_minimum_keeps_its_counts_visible(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-sample@example.com")
    for _ in range(29):
        seed(tenant)

    below = row(trends(tenant), family="service_complaint")["cell"]
    assert below["status"] == "insufficient_sample"
    assert below["reason"] == "below_minimum_sample"
    assert below["numerator"] == 29 and below["denominator"] == 29 and below["ratio"] is None
    assert below["minimum_sample"] == 30
    # allow-control: at the minimum the cell is computed
    seed(tenant)
    at = row(trends(tenant), family="service_complaint")["cell"]
    assert at["status"] == "computed" and at["numerator"] == 30 and at["ratio"] == 1.0


def test_one_agent_filter_gives_agent_cells(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-agent@example.com")
    agent, other = add_user("agent"), add_user("other")
    seed(tenant, agent_user_id=agent)
    seed(tenant, agent_user_id=other)

    one = row(trends(tenant, agent_user_id=agent), family="service_complaint")
    assert one["cell"]["status"] == "insufficient_sample"
    assert one["cell"]["numerator"] is None and one["pending_review"] is None
    assert one["cell"]["denominator"] == 1
    # HOM-82 (L1 closed): the kind comes from the agents of the population, not from the
    # filter, so two agents are still a small group: an agent cell
    both = row(trends(tenant, agent_user_id=[agent, other]), family="service_complaint")
    assert both["cell"]["status"] == "insufficient_sample" and both["cell"]["numerator"] is None
    # allow-control: five agents other than the viewer are a rate cohort that keeps its counts
    for name in ("third", "fourth", "fifth"):
        seed(tenant, agent_user_id=add_user(name))
    many = row(trends(tenant), family="service_complaint")
    assert many["cell"]["numerator"] == 5 and trends(tenant)["disclosure"]["kind"] == "rate"


# --- deny-case 14: the half-open window -----------------------------------------------------


def test_the_window_is_half_open(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-half-open@example.com")
    seed(tenant, received_at=BASE)
    seed(tenant, received_at=BASE + DAY)

    answer = trends(tenant, **{"from": BASE.isoformat(), "to": (BASE + DAY).isoformat()})
    assert included(answer) == 1


# --- population filters ---------------------------------------------------------------------


def test_population_filters_narrow_by_and(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-filters@example.com")
    team = add_team(tenant, "a")
    agent = add_user("agent")
    seed(tenant, language="fa", team_id=team, agent_user_id=agent)
    seed(tenant, language="en", team_id=team, source_type=IngestionSourceType.API)
    seed(tenant, language="en", source_type=IngestionSourceType.VOICEMAIL)

    assert included(trends(tenant)) == 3
    assert included(trends(tenant, language="EN")) == 2
    assert included(trends(tenant, language=["fa", "en"])) == 3
    assert included(trends(tenant, source_type="api")) == 1
    assert included(trends(tenant, source_type=["api", "voicemail"])) == 2
    assert included(trends(tenant, team_id=team)) == 2
    assert included(trends(tenant, team_id=team, language="en")) == 1
    assert included(trends(tenant, agent_user_id=agent)) == 1


def test_selectors_choose_rows_and_never_change_numbers(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-selectors@example.com")
    seed(tenant)
    full = trends(tenant)

    refund = trends(tenant, service_area="refund")
    assert [r["issue"] for r in refund["rows"]] == ["complaint_refund"]
    assert refund["rows"][0] == row(full, family="service_complaint", issue="complaint_refund")
    families = trends(tenant, family="qa")
    assert [(r["level"], r["family"]) for r in families["rows"]] == [("family", "qa")]


# --- the coverage link ----------------------------------------------------------------------


def test_the_population_reconciles_with_coverage(client, email_outbox, settings):
    tenant = Tenant(client, email_outbox, "trend-coverage@example.com")
    first = seed(tenant)
    seed(tenant, received_at=BASE + timedelta(hours=1), duplicate_of=first.ingestion_id)
    seed(tenant, run="stale")
    seed(tenant, extraction=None)
    seed(tenant, deleted=True)
    seed(tenant, status=MeetingStatus.FAILED)
    seed(tenant, segments=0)
    seed(tenant, ledger="rejected")
    seed(tenant, received_at=BASE - DAY)

    answer = trends(tenant)
    with inspection_session_factory()() as db:
        coverage = compute_coverage(
            db,
            workspace_id=tenant.workspace_id,
            window=CoverageWindow(start=BASE, end=BASE + DAY),
            recompute_max_attempts=settings.recompute_max_attempts,
        )
    processing = coverage.counters["processing"]
    assert processing["numerator"] == 4
    assert included(answer) + excluded(answer) == processing["numerator"]


# --- series ---------------------------------------------------------------------------------


def _local(text: str) -> datetime:
    return datetime.fromisoformat(text)


def test_a_call_at_23_utc_belongs_to_the_next_tehran_day(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-tehran@example.com")
    seed(tenant, received_at=datetime(2026, 10, 1, 23, 0, tzinfo=UTC))
    seed(tenant, received_at=datetime(2026, 10, 1, 20, 29, tzinfo=UTC))

    answer = trends(
        tenant,
        grain="day",
        **{"from": "2026-10-01T00:00:00+03:30", "to": "2026-10-04T00:00:00+03:30"},
    )

    points = answer["points"]
    assert [(p["from"], p["to"]) for p in points] == [
        ("2026-10-01T00:00:00+03:30", "2026-10-02T00:00:00+03:30"),
        ("2026-10-02T00:00:00+03:30", "2026-10-03T00:00:00+03:30"),
        ("2026-10-03T00:00:00+03:30", "2026-10-04T00:00:00+03:30"),
    ]
    assert [p["partial"] for p in points] == [False, False, False]
    assert [p["population"]["included"] for p in points] == [1, 1, 0]
    assert answer["window"]["from"] == "2026-09-30T20:30:00+00:00"
    assert_points_add_up(answer)


def test_weeks_start_on_saturday_and_cut_points_are_partial(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-week@example.com")
    seed(tenant, received_at=datetime(2026, 10, 2, 12, 0, tzinfo=UTC))  # Friday
    seed(tenant, received_at=datetime(2026, 10, 3, 12, 0, tzinfo=UTC))  # Saturday
    seed(tenant, received_at=datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    answer = trends(
        tenant,
        grain="week",
        **{"from": "2026-10-01T12:00:00+03:30", "to": "2026-10-13T00:00:00+03:30"},
    )

    points = answer["points"]
    assert [(p["from"], p["to"], p["partial"]) for p in points] == [
        ("2026-10-01T12:00:00+03:30", "2026-10-03T00:00:00+03:30", True),
        ("2026-10-03T00:00:00+03:30", "2026-10-10T00:00:00+03:30", False),
        ("2026-10-10T00:00:00+03:30", "2026-10-13T00:00:00+03:30", True),
    ]
    assert [p["population"]["included"] for p in points] == [1, 1, 1]
    assert_points_add_up(answer)


def test_months_are_gregorian_and_utc_works(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-month@example.com")
    seed(tenant, received_at=datetime(2026, 9, 30, 23, 0, tzinfo=UTC))
    seed(tenant, received_at=datetime(2026, 10, 1, 0, 0, tzinfo=UTC))
    window = {"from": "2026-09-15T00:00:00+00:00", "to": "2026-11-01T00:00:00+00:00"}

    utc = trends(tenant, grain="month", timezone="UTC", **window)
    assert [(p["from"], p["to"], p["partial"]) for p in utc["points"]] == [
        ("2026-09-15T00:00:00+00:00", "2026-10-01T00:00:00+00:00", True),
        ("2026-10-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00", False),
    ]
    assert [p["population"]["included"] for p in utc["points"]] == [1, 1]
    assert utc["series"]["timezone"] == "UTC"

    tehran = trends(tenant, grain="month", **window)
    assert [p["from"] for p in tehran["points"]] == [
        "2026-09-15T03:30:00+03:30",
        "2026-10-01T00:00:00+03:30",
        "2026-11-01T00:00:00+03:30",
    ]
    assert [p["population"]["included"] for p in tehran["points"]] == [0, 2, 0]
    assert [p["partial"] for p in tehran["points"]] == [True, False, True]


def test_point_sums_equal_the_whole_window(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-sums@example.com")
    agent = add_user("agent")
    extractions = [COMPLAINT, BILLING, ABSTAINED, None]
    for hour in range(0, 72, 5):
        seed(
            tenant,
            received_at=BASE + timedelta(hours=hour),
            extraction=extractions[hour % 4],
            agent_user_id=agent if hour % 2 else None,
        )
    first = seed(tenant, received_at=BASE + timedelta(hours=1))
    seed(tenant, received_at=BASE + timedelta(hours=50), duplicate_of=first.ingestion_id)
    window = {"from": BASE.isoformat(), "to": (BASE + 3 * DAY).isoformat()}

    for grain in ("day", "week", "month"):
        answer = trends(tenant, grain=grain, **window)
        assert excluded(answer) == 1
        assert_points_add_up(answer)
    agent_answer = trends(tenant, grain="day", agent_user_id=agent, **window)
    assert_points_add_up(agent_answer)
    # the excluded copy is counted in the point of its own received_at (third local day)
    days = trends(tenant, grain="day", **window)["points"]
    assert [p["population"]["excluded"]["duplicate_media"] for p in days] == [0, 0, 1, 0]


# (grain, timezone, code, key); one tenant serves every case
BAD_SERIES_SETTINGS = [
    ("hour", "Asia/Tehran", "trend_grain_invalid", "grain"),
    ("", "Asia/Tehran", "trend_grain_invalid", "grain"),
    ("Day", "Asia/Tehran", "trend_grain_invalid", "grain"),
    ("day", "Mars/Base", "trend_timezone_invalid", "timezone"),
    ("day", "utc", "trend_timezone_invalid", "timezone"),
    ("day", "asia/tehran", "trend_timezone_invalid", "timezone"),
    ("day", "America", "trend_timezone_invalid", "timezone"),
    ("day", "../etc/passwd", "trend_timezone_invalid", "timezone"),
    ("day", "", "trend_timezone_invalid", "timezone"),
    ("hour", "Mars/Base", "trend_grain_invalid", "grain"),
]


def test_bad_series_settings_are_refused(client, email_outbox):
    tenant = Tenant(client, email_outbox, f"trend-series-{uuid.uuid4().hex[:8]}@example.com")
    for grain, timezone, code, key in BAD_SERIES_SETTINGS:
        with pytest.raises(TrendSeriesError) as caught:
            trends(tenant, grain=grain, timezone=timezone)
        assert (caught.value.code, caught.value.key) == (code, key), (grain, timezone)


def test_every_grain_and_the_window_edges_are_answered(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-edges@example.com")
    assert TREND_GRAINS == ("none", "day", "week", "month")
    for grain in TREND_GRAINS:
        for window in (
            {"from": "0001-01-01T00:00:00+00:00", "to": "0001-02-01T00:00:00+00:00"},
            {"from": "9999-12-01T00:00:00+00:00", "to": "9999-12-31T20:00:00+00:00"},
        ):
            answer = trends(tenant, grain=grain, **window)
            if grain != "none":
                points = answer["points"]
                assert _local(points[0]["from"]) == _local(window["from"])
                assert _local(points[-1]["to"]) == _local(window["to"])
                assert all(a["to"] == b["from"] for a, b in zip(points, points[1:], strict=False))


def test_a_window_that_cannot_be_shown_in_the_zone_is_refused(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-zone-range@example.com")
    late = {"from": "9999-12-01T00:00:00+00:00", "to": "9999-12-31T23:59:59+00:00"}
    early = {"from": "0001-01-01T00:00:00+00:00", "to": "0001-01-02T00:00:00+00:00"}
    for window, timezone in ((late, "Asia/Tehran"), (early, "America/New_York")):
        with pytest.raises(TrendSeriesError) as caught:
            trends(tenant, grain="day", timezone=timezone, **window)
        assert (caught.value.code, caught.value.key) == ("trend_timezone_invalid", "timezone")
        # allow-control: without a series nothing is shown in the zone
        assert trends(tenant, grain="none", timezone=timezone, **window)["points"] == []
    assert trends(tenant, grain="day", timezone="UTC", **late)["points"]


def test_a_naive_now_is_a_programming_error(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-now@example.com")
    with inspection_session_factory()() as db, pytest.raises(ValueError):
        compute_issue_trends(
            db,
            workspace_id=tenant.workspace_id,
            access=unrestricted(tenant.owner_id),
            filters=filters_of(),
            now=NOW.replace(tzinfo=None),
        )


# --- cost -----------------------------------------------------------------------------------


def test_the_statement_count_is_fixed(client, email_outbox):
    tenant = Tenant(client, email_outbox, "trend-cost@example.com")
    team = add_team(tenant, "a")
    small_day, large_day, empty_day = BASE, BASE + 2 * DAY, BASE + 5 * DAY
    for _ in range(2):
        seed(tenant, received_at=small_day, team_id=team)
    for hour in range(12):
        at = large_day + timedelta(hours=hour)
        copy = seed(tenant, received_at=at, team_id=team)
        seed(tenant, received_at=at + timedelta(minutes=30), duplicate_of=copy.ingestion_id)

    def statements(day: datetime, **options: Any) -> tuple[int, dict[str, Any]]:
        seen: list[str] = []

        def record(_conn, _cursor, statement, _parameters, _context, _executemany):
            seen.append(statement)

        with inspection_session_factory()() as db:
            engine = db.get_bind()
            event.listen(engine, "before_cursor_execute", record)
            try:
                answer = compute_issue_trends(
                    db,
                    workspace_id=tenant.workspace_id,
                    access=options.pop("access", unrestricted(tenant.owner_id)),
                    filters=filters_of(**{"from": day.isoformat(), "to": (day + DAY).isoformat()}),
                    now=NOW,
                    **options,
                )
            finally:
                event.remove(engine, "before_cursor_execute", record)
        return len(seen), answer

    counts = {
        "small": statements(small_day),
        "large": statements(large_day),
        "empty": statements(empty_day),
        "large_day_grain": statements(large_day, grain="day"),
        "large_restricted": statements(large_day, access=restricted(add_user("sup"), team)),
    }
    assert included(counts["small"][1]) == 2
    assert included(counts["large"][1]) == 12 and excluded(counts["large"][1]) == 12
    assert included(counts["empty"][1]) == 0
    assert included(counts["large_restricted"][1]) == 12
    assert {name: count for name, (count, _) in counts.items()} == dict.fromkeys(counts, 2)
