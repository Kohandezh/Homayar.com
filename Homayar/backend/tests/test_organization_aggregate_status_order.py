"""HOM-341: the keys of ``meetings_by_status`` follow the ``MeetingStatus`` declaration.

``_population_counts`` (``organization_service``) builds the dict from a GROUP BY with no
ORDER BY, so the key order is whatever the database hands back: SQLite happens to give
alphabetical order; PostgreSQL promises no order for grouped rows (hash or sort, by plan).
The route now builds the dict in Python, in the declaration order of ``MeetingStatus`` (the
meeting life cycle), which is not alphabetical, so the order pinned here is the fixed one,
not a database accident.
"""

from __future__ import annotations

import pytest

from backend.database.saas_models import MeetingStatus
from backend.tests.test_organization_aggregate_metadata import (
    _agents,
    _closed_weeks,
    _get,
    _item,
    _meeting,
    _meetings,
    _org_workspace,
    _persona,
    _span,
)

DECLARATION_ORDER = [status.value for status in MeetingStatus]


def _released_population(client, email_outbox, *, email: str, name: str, statuses):
    """A workspace whose block is released (5 agents with completed calls), plus one meeting
    of every ``status``.  Returns the caller, the org, the place and the window ``W``."""
    owner = _persona(client, email_outbox, email=email, name=name)
    org = owner.organization_id
    place = _org_workspace(owner, org, name)
    _, (_, week, _) = _closed_weeks()
    _meetings(place, week, None, _agents(5, f"o5{name.lower()}"))
    for agent, status in zip(_agents(len(statuses), f"ox{name.lower()}"), statuses, strict=True):
        _meeting(place, week, agent=agent, status=status)
    return owner, org, place, _span(week)


def _assert_keys_and_counts(owner, org, place, window, *, counts: dict[str, int]) -> None:
    """Both calls give the same keys, in declaration order, with the same numbers."""
    expected = [value for value in DECLARATION_ORDER if value in counts]
    first = _item(_get(owner, org, window), place)
    second = _item(_get(owner, org, window), place)
    assert list(first["meetings_by_status"]) == expected
    assert list(second["meetings_by_status"]) == list(first["meetings_by_status"])
    # the control: the counts per status and the total are the numbers the route already gave
    assert first["meetings_by_status"] == counts
    assert first["meetings_total"] == sum(counts.values())


def _released(owner, org, place, window) -> bool:
    return _item(_get(owner, org, window), place)["disclosure"]["status"] == "released"


def test_by_status_keys_follow_the_declaration_order_on_every_call(client, email_outbox):
    owner, org, place, window = _released_population(
        client,
        email_outbox,
        email="so-owner@example.com",
        name="Order",
        statuses=[
            MeetingStatus.QUEUED,
            MeetingStatus.PROCESSING,
            MeetingStatus.FAILED,
            MeetingStatus.CANCELLED,
        ],
    )
    assert _released(owner, org, place, window)
    _assert_keys_and_counts(
        owner,
        org,
        place,
        window,
        counts={"queued": 1, "processing": 1, "completed": 5, "failed": 1, "cancelled": 1},
    )


@pytest.mark.postgres
def test_by_status_keys_follow_the_declaration_order_on_postgres(client, email_outbox):
    owner, org, place, window = _released_population(
        client,
        email_outbox,
        email="sp-owner@example.com",
        name="Pgorder",
        statuses=[status for status in MeetingStatus if status is not MeetingStatus.COMPLETED],
    )
    assert _released(owner, org, place, window)
    counts = {status.value: 1 for status in MeetingStatus}
    counts["completed"] = 5
    _assert_keys_and_counts(owner, org, place, window, counts=counts)
