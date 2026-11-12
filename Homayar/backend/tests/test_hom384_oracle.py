"""HOM-384 oracle: the two demo call teams get their own calls and their own numbers."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from backend.database import demo_seed
from backend.database.demo_seed import seed_demo_workspace
from backend.database.saas_models import IngestionRecord, Meeting, ProcessingJob
from backend.services.role_trend_service import current_week_start
from backend.tests._database import inspection_session_factory
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_demo_seed import (
    TRENDS_INSTANT,
    _computed_with_numerator,
    _counts,
    _issue_point_cells,
    _login,
    _small_plan,
    _team_names,
    _trends_answer,
)
from backend.tests.test_issue_trend_routes import TRENDS, _place
from backend.tests.test_issue_trend_service import (
    BILLING,
    COMPLAINT,
    WINDOW,
    add_team,
    add_user,
    included,
    seed,
)

CALL_CENTER_MIXES = {
    1: {
        "support_connectivity": 3,
        "support_connectivity_unresolved": 2,
        "support_technical": 2,
        "support_account": 2,
        "support_abstain": 3,
        "billing_invoice": 2,
        "billing_payment": 2,
        "billing_unresolved": 2,
        "billing_abstain": 2,
        "complaint_refund": 2,
        "complaint_delivery": 2,
        "complaint_refund_abstain": 2,
        "sales_pricing": 2,
        "sales_pricing_resolved": 2,
    },
    2: {
        "support_connectivity": 2,
        "support_connectivity_unresolved": 2,
        "support_technical": 1,
        "support_account": 2,
        "support_abstain": 2,
        "billing_invoice": 3,
        "billing_payment": 2,
        "billing_unresolved": 3,
        "billing_abstain": 3,
        "complaint_refund": 3,
        "complaint_delivery": 2,
        "complaint_refund_abstain": 2,
        "sales_pricing": 2,
        "sales_pricing_resolved": 1,
    },
    3: {
        "support_connectivity": 3,
        "support_connectivity_unresolved": 3,
        "support_technical": 2,
        "support_account": 2,
        "support_abstain": 3,
        "billing_invoice": 2,
        "billing_payment": 1,
        "billing_unresolved": 2,
        "billing_abstain": 2,
        "complaint_refund": 2,
        "complaint_delivery": 2,
        "complaint_refund_abstain": 1,
        "sales_pricing": 3,
        "sales_pricing_resolved": 2,
    },
    4: {
        "support_connectivity": 3,
        "support_connectivity_unresolved": 2,
        "support_technical": 2,
        "support_account": 1,
        "support_abstain": 2,
        "billing_invoice": 2,
        "billing_payment": 2,
        "billing_unresolved": 2,
        "billing_abstain": 3,
        "complaint_refund": 3,
        "complaint_delivery": 3,
        "complaint_refund_abstain": 2,
        "sales_pricing": 2,
        "sales_pricing_resolved": 1,
    },
}
WEEKS = (1, 2, 3, 4)
SCRIPT_KEYS = frozenset(CALL_CENTER_MIXES[1])


def _plan() -> dict[str, dict[int, Counter]]:
    plan: dict[str, dict[int, Counter]] = defaultdict(lambda: defaultdict(Counter))
    for team, week, key, count in demo_seed.TREND_CALL_BLOCKS:
        plan[team][week][key] += count
    return plan


def _family_total(mix: Counter, prefixes: tuple[str, ...]) -> int:
    return sum(count for key, count in mix.items() if key.startswith(prefixes))


# --- AC1: the plan rules --------------------------------------------------------------------


def test_the_call_plan_uses_only_the_fourteen_script_keys():
    assert set(demo_seed.CALL_SCRIPTS) == SCRIPT_KEYS
    plan = _plan()
    assert set(plan) == {demo_seed.CALL_CENTER, demo_seed.CUSTOMER_CARE}
    for team in plan:
        assert set(plan[team]) == set(WEEKS), team


def test_every_team_week_has_thirty_calls_and_every_key():
    plan = _plan()
    for team in (demo_seed.CALL_CENTER, demo_seed.CUSTOMER_CARE):
        for week in WEEKS:
            mix = plan[team][week]
            assert sum(mix.values()) == 30, (team, week)
            assert set(mix) == SCRIPT_KEYS, (team, week)
            assert all(count >= 1 for count in mix.values()), (team, week)


def test_the_call_center_mix_is_unchanged():
    plan = _plan()
    for week in WEEKS:
        assert dict(plan[demo_seed.CALL_CENTER][week]) == CALL_CENTER_MIXES[week], week


def test_customer_care_differs_from_call_center_in_at_least_four_keys_each_week():
    plan = _plan()
    for week in WEEKS:
        call_center = plan[demo_seed.CALL_CENTER][week]
        customer_care = plan[demo_seed.CUSTOMER_CARE][week]
        differing = [key for key in SCRIPT_KEYS if call_center[key] != customer_care[key]]
        assert len(differing) >= 4, (week, sorted(differing))


def test_customer_care_leans_to_billing_and_complaints_each_week():
    plan = _plan()
    for week in WEEKS:
        call_center = plan[demo_seed.CALL_CENTER][week]
        customer_care = plan[demo_seed.CUSTOMER_CARE][week]
        assert _family_total(customer_care, ("billing_", "complaint_")) > _family_total(
            call_center, ("billing_", "complaint_")
        ), week
        assert _family_total(customer_care, ("support_",)) < _family_total(
            call_center, ("support_",)
        ), week


def test_the_customer_care_mix_moves_week_by_week():
    plan = _plan()
    mixes = [tuple(sorted(plan[demo_seed.CUSTOMER_CARE][week].items())) for week in WEEKS]
    assert len(set(mixes)) > 1


# --- AC1: the seeded numbers through the API ------------------------------------------------


def _issue_window_rows(answer: dict) -> dict[tuple[str, str], tuple]:
    return {
        (row["family"], row["issue"]): (row["cell"]["numerator"], row["cell"]["ratio"])
        for row in answer["rows"]
        if row["level"] == "issue"
    }


def _issue_point_rows(point: dict) -> dict[tuple[str, str], tuple]:
    return {
        (row["family"], row["issue"]): (row["cell"]["numerator"], row["cell"]["ratio"])
        for row in point["rows"]
        if row["level"] == "issue"
    }


def test_the_two_demo_call_teams_show_different_issue_numbers(client, settings):
    result = seed_demo_workspace(settings, now=TRENDS_INSTANT)
    assert result.meeting_count == 270
    _login(client, result.accounts["owner"])
    anchor = current_week_start(TRENDS_INSTANT)
    query = {
        "from": (anchor - timedelta(days=28)).isoformat(),
        "to": anchor.isoformat(),
        "grain": "week",
        "timezone": "Asia/Tehran",
    }
    with inspection_session_factory()() as db:
        ids_of = {name: team_id for team_id, name in _team_names(db, result.workspace_id).items()}
        customer_care_calls = db.scalar(
            select(func.count())
            .select_from(Meeting)
            .where(
                Meeting.workspace_id == result.workspace_id,
                Meeting.team_id == ids_of[demo_seed.CUSTOMER_CARE],
            )
        )
    assert customer_care_calls == 120

    answers = {}
    for team_name in (demo_seed.CALL_CENTER, demo_seed.CUSTOMER_CARE):
        answer = _trends_answer(client, result.workspace_id, team_id=ids_of[team_name], **query)
        assert answer["disclosure"]["status"] == "released", team_name
        assert answer["disclosure"]["kind"] == "rate", team_name
        assert len(answer["points"]) == 4, team_name
        assert len(_computed_with_numerator(_issue_point_cells(answer))) >= 3, team_name
        answers[team_name] = answer

    call_center = _issue_window_rows(answers[demo_seed.CALL_CENTER])
    customer_care = _issue_window_rows(answers[demo_seed.CUSTOMER_CARE])
    assert set(call_center) == set(customer_care)
    differing = [key for key in call_center if call_center[key] != customer_care[key]]
    assert len(differing) >= 3, differing

    for week, (left, right) in enumerate(
        zip(
            answers[demo_seed.CALL_CENTER]["points"],
            answers[demo_seed.CUSTOMER_CARE]["points"],
            strict=True,
        ),
        start=1,
    ):
        assert left["from"] == right["from"]
        left_rows, right_rows = _issue_point_rows(left), _issue_point_rows(right)
        assert any(left_rows[key] != right_rows.get(key) for key in left_rows), week


# --- AC2: the API keeps teams apart (regression, independent of the seed) --------------------


def test_two_teams_with_different_calls_get_different_numbers(client, email_outbox, monkeypatch):
    owner, _people, _ = _workspace_with_roles(client, email_outbox, monkeypatch, roles={})
    place = _place(owner)
    team_a = add_team(place, "Oracle A")
    team_b = add_team(place, "Oracle B")
    for index in range(5):
        agent = add_user(f"oracle-a{index}")
        for _ in range(8):
            seed(place, team_id=team_a, agent_user_id=agent, extraction=COMPLAINT)
        for _ in range(4):
            seed(place, team_id=team_a, agent_user_id=agent, extraction=BILLING)
    for index in range(5):
        agent = add_user(f"oracle-b{index}")
        for _ in range(3):
            seed(place, team_id=team_b, agent_user_id=agent, extraction=COMPLAINT)
        for _ in range(10):
            seed(place, team_id=team_b, agent_user_id=agent, extraction=BILLING)

    def answer(*params: tuple[str, str]) -> dict:
        response = owner.get(TRENDS, params=[*WINDOW.items(), *params])
        assert response.status_code == 200, response.text
        return response.json()

    whole = answer()
    first = answer(("team_id", team_a))
    second = answer(("team_id", team_b))

    assert included(whole) == 125
    assert included(first) == 60
    assert included(second) == 65
    for body in (first, second):
        assert body["disclosure"]["status"] == "released", body["disclosure"]
        assert body["disclosure"]["kind"] == "rate", body["disclosure"]

    first_rows = _issue_window_rows(first)
    second_rows = _issue_window_rows(second)
    differing = [
        key for key in first_rows if first_rows[key][0] != second_rows.get(key, (None,))[0]
    ]
    assert differing, (first_rows, second_rows)
    billing = ("call_reason", "intent_resolve_billing")
    assert first_rows[billing][0] == 20
    assert second_rows[billing][0] == 50
    assert _issue_window_rows(whole)[billing][0] == 70


# --- AC4: a workspace seeded by the HOM-365 plan is refused ----------------------------------


def _rename_one_seeded_job(workspace_id: str, new_key: str) -> None:
    with inspection_session_factory()() as db:
        job = db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.workspace_id == workspace_id)
            .order_by(ProcessingJob.idempotency_key)
        ).first()
        assert job is not None
        assert (
            db.scalar(
                select(func.count())
                .select_from(IngestionRecord)
                .where(IngestionRecord.meeting_id == job.meeting_id)
            )
            == 1
        )
        db.execute(
            update(ProcessingJob).where(ProcessingJob.id == job.id).values(idempotency_key=new_key)
        )
        db.execute(
            update(IngestionRecord)
            .where(IngestionRecord.meeting_id == job.meeting_id)
            .values(external_event_id=new_key)
        )
        db.commit()


@pytest.mark.parametrize(
    "legacy_key",
    ["demo-seed-meeting-001", "demo-seed-meeting-135", "demo-seed-meeting-270"],
)
def test_a_workspace_seeded_by_the_hom365_plan_is_refused(
    client, settings, monkeypatch, legacy_key
):
    _small_plan(monkeypatch)
    result = seed_demo_workspace(settings)
    _rename_one_seeded_job(result.workspace_id, legacy_key)
    before = _counts(result.workspace_id)

    with pytest.raises(demo_seed.DemoSeedRefused) as refused:
        seed_demo_workspace(settings)

    assert "Reset the database" in str(refused.value)
    assert _counts(result.workspace_id) == before


def test_a_workspace_with_a_two_digit_key_is_still_refused(client, settings, monkeypatch):
    _small_plan(monkeypatch)
    result = seed_demo_workspace(settings)
    _rename_one_seeded_job(result.workspace_id, "demo-seed-meeting-01")
    before = _counts(result.workspace_id)

    with pytest.raises(demo_seed.DemoSeedRefused) as refused:
        seed_demo_workspace(settings)

    assert "Reset the database" in str(refused.value)
    assert _counts(result.workspace_id) == before


def test_a_clean_run_and_a_second_run_of_this_seed_pass(client, settings, monkeypatch):
    _small_plan(monkeypatch)
    first = seed_demo_workspace(settings)
    before = _counts(first.workspace_id)

    second = seed_demo_workspace(settings)

    assert second.workspace_id == first.workspace_id
    assert _counts(first.workspace_id) == before


def test_this_seed_writes_no_key_an_older_plan_used(client, settings, monkeypatch):
    _small_plan(monkeypatch)
    result = seed_demo_workspace(settings)
    with inspection_session_factory()() as db:
        keys = db.scalars(
            select(ProcessingJob.idempotency_key).where(
                ProcessingJob.workspace_id == result.workspace_id
            )
        ).all()
    assert keys
    assert not {f"demo-seed-meeting-{number:03d}" for number in range(1, 271)} & set(keys)
    assert not {f"demo-seed-meeting-{number:02d}" for number in range(1, 31)} & set(keys)
