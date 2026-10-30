"""HOM-73 PR 2: ``POST /review-items/from-sample`` (DESIGN §5.8).

It runs the HOM-72 draw unchanged and creates one ``sample`` item per selection, in draw
order, with the draw's versions, digests and the selection's reasons in
``source_details``.  A conversation with an active item is skipped and listed.  One
``review_sample.enqueued`` audit event records it; a refusal creates nothing and records
nothing.  ``POST /admin/review-samples`` keeps its behaviour.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.database.saas_models import AuditEvent
from backend.tests._database import inspection_session_factory
from backend.tests.test_coverage import WINDOW, Tenant, seed_call
from backend.tests.test_review_queue_access import (
    ITEMS,
    audit_count,
    code,
    due,
    item_count,
    review_events,
)
from backend.tests.test_review_sampling_api import DETAIL_KEYS

SAMPLE = f"{ITEMS}/from-sample"
DRAWS = "/api/v1/admin/review-samples"
RESPONSE_KEYS = {"coverage_version", "window", "draw", "created", "skipped_meeting_ids"}


def _enqueue(tenant: Tenant, **body: object):
    return tenant.post(SAMPLE, {**WINDOW, "due_at": due(), **body})


def _drawn_events(workspace_id: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.workspace_id == workspace_id,
                    AuditEvent.event_type == "review_sample.drawn",
                )
            )
        )


def test_one_sample_item_per_selection_in_draw_order(client, email_outbox):
    tenant = Tenant(client, email_outbox, "review-sample-items@example.com")
    population = [seed_call(tenant) for _ in range(4)]
    busy = population[1]
    manual = tenant.post(ITEMS, {"meeting_id": busy, "due_at": due()})
    assert manual.status_code == 201, manual.text
    due_at = datetime.now(UTC) + timedelta(days=7)

    response = _enqueue(tenant, seed=42, config={"random_size": 4}, due_at=due_at.isoformat())
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == RESPONSE_KEYS
    assert body["coverage_version"] == "coverage/1"
    draw = body["draw"]
    assert draw["seed"] == 42 and draw["sampler_version"] == "review-sampling/1"
    selections = draw["selections"]
    assert {s["item_id"] for s in selections} == set(population)  # the whole population

    expected_created = [s for s in selections if s["item_id"] != busy]
    assert [item["meeting_id"] for item in body["created"]] == [
        s["item_id"] for s in expected_created
    ]
    assert body["skipped_meeting_ids"] == [busy]
    for item, selection in zip(body["created"], expected_created, strict=True):
        assert (item["source"], item["status"], item["revision"]) == ("sample", "open", 1)
        assert item["assignee_user_id"] is None and item["assigned_at"] is None
        assert item["created_by_user_id"] == tenant.owner_id
        assert datetime.fromisoformat(item["due_at"]) == due_at
        assert item["source_details"] == {
            "sampler_version": draw["sampler_version"],
            "seed": 42,
            "config_digest": draw["config_digest"],
            "selection_digest": draw["selection_digest"],
            "reasons": selection["reasons"],
        }
    listed = tenant.get(ITEMS, meeting_id=expected_created[0]["item_id"]).json()["items"]
    assert [item["id"] for item in listed] == [body["created"][0]["id"]]

    # The same draw as the HOM-72 endpoint gives for the same seed and config.
    same = tenant.post(DRAWS, {**WINDOW, "seed": 42, "config": {"random_size": 4}})
    assert same.status_code == 200, same.text
    assert same.json()["draw"] == draw


def test_one_audit_event_records_the_enqueue(client, email_outbox):
    tenant = Tenant(client, email_outbox, "review-sample-audit@example.com")
    population = [seed_call(tenant) for _ in range(2)]
    tenant.post(ITEMS, {"meeting_id": population[0], "due_at": due()})
    before = len(review_events(tenant.workspace_id))
    due_at = datetime.now(UTC) + timedelta(days=3)
    body = _enqueue(tenant, seed=5, config={"random_size": 2}, due_at=due_at.isoformat()).json()
    events = review_events(tenant.workspace_id)[before:]
    (event,) = events  # one event for the whole enqueue, none per item
    assert (event.event_type, event.target_type, event.target_id) == (
        "review_sample.enqueued",
        "workspace",
        tenant.workspace_id,
    )
    details = event.details
    draw = body["draw"]
    assert details["seed"] == 5
    assert details["sampler_version"] == draw["sampler_version"]
    assert details["config_digest"] == draw["config_digest"]
    assert details["selection_digest"] == draw["selection_digest"]
    assert details["population_digest"] == draw["population_digest"]
    assert details["selected_count"] == len(draw["selections"]) == 2
    assert (details["created_count"], details["skipped_count"]) == (1, 1)
    assert datetime.fromisoformat(details["due_at"]) == due_at
    # Exactly the draw's own audit details plus the three enqueue keys (DESIGN §6, §9.3).
    assert set(details) == DETAIL_KEYS | {"created_count", "skipped_count", "due_at"}
    assert _drawn_events(tenant.workspace_id) == []  # the enqueue is not a second draw event


def test_the_same_seed_on_an_unchanged_population_selects_the_same_meetings(client, email_outbox):
    tenant = Tenant(client, email_outbox, "review-sample-repeat@example.com")
    for _ in range(5):
        seed_call(tenant)
    first = _enqueue(tenant, seed=9, config={"random_size": 2}).json()
    again = _enqueue(tenant, seed=9, config={"random_size": 2}).json()
    assert again["draw"]["selections"] == first["draw"]["selections"]
    assert again["draw"]["selection_digest"] == first["draw"]["selection_digest"]
    # Every selected conversation now has an active item, so the second run skips them.
    assert again["created"] == []
    assert again["skipped_meeting_ids"] == [s["item_id"] for s in first["draw"]["selections"]]
    assert item_count() == 2


def test_refusals_create_nothing_and_record_nothing(client, email_outbox):
    tenant = Tenant(client, email_outbox, "review-sample-refused@example.com")
    seed_call(tenant)
    now = datetime.now(UTC)
    before = audit_count()
    cases = [
        ({"config": {"random_size": 501}}, "sampling_config_invalid"),
        ({"config": {}}, "sampling_config_empty"),
        ({"config": {"random_size": 1}, "seed": -1}, "seed_invalid"),
        ({"config": {"random_size": 1}, "from": "2026-03-01T09:00:00"}, "coverage_window_invalid"),
        (
            {
                "config": {"random_size": 1},
                "due_at": (now + timedelta(days=1)).replace(tzinfo=None).isoformat(),
            },
            "review_due_at_invalid",
        ),
        (
            {"config": {"random_size": 1}, "due_at": (now - timedelta(minutes=1)).isoformat()},
            "review_due_at_invalid",
        ),
        (
            {"config": {"random_size": 1}, "due_at": (now + timedelta(days=91)).isoformat()},
            "review_due_at_invalid",
        ),
    ]
    for body, expected in cases:
        refused = _enqueue(tenant, **body)
        assert refused.status_code == 422, (body, refused.text)
        assert code(refused) == expected, body
    missing = tenant.post(SAMPLE, {**WINDOW, "config": {"random_size": 1}})
    assert missing.status_code == 422 and code(missing) == "validation_error"
    unknown = _enqueue(tenant, config={"random_size": 1}, assignee_user_id=tenant.owner_id)
    assert unknown.status_code == 422 and code(unknown) == "validation_error"
    assert audit_count() == before and item_count() == 0
    # Allow-control: the same request with a valid due time.
    assert _enqueue(tenant, config={"random_size": 1}).status_code == 201
    assert item_count() == 1


def test_due_at_must_be_an_iso_string_on_from_sample(client, email_outbox):
    tenant = Tenant(client, email_outbox, "review-sample-due-string@example.com")
    seed_call(tenant)
    instant = (datetime.now(UTC) + timedelta(days=2)).replace(microsecond=0)
    before = audit_count()
    for value in (int(instant.timestamp()), True, {"at": instant.isoformat()}):
        refused = _enqueue(tenant, config={"random_size": 1}, due_at=value)
        assert refused.status_code == 422, (value, refused.text)
        assert code(refused) == "review_due_at_invalid", value
    assert audit_count() == before and item_count() == 0
    # Allow-control: the same instant as an ISO 8601 string with ``Z``.
    iso = instant.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = _enqueue(tenant, config={"random_size": 1}, due_at=iso).json()
    assert datetime.fromisoformat(body["created"][0]["due_at"]) == instant


def test_an_empty_selection_is_a_201_with_empty_lists(client, email_outbox):
    tenant = Tenant(client, email_outbox, "review-sample-empty@example.com")
    response = _enqueue(tenant, seed=1, config={"random_size": 5})
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["created"], body["skipped_meeting_ids"], body["draw"]["selections"]) == (
        [],
        [],
        [],
    )
    (event,) = review_events(tenant.workspace_id)
    assert (event.details["created_count"], event.details["skipped_count"]) == (0, 0)


def test_the_hom72_draw_endpoint_is_unchanged(client, email_outbox):
    tenant = Tenant(client, email_outbox, "review-sample-hom72@example.com")
    seed_call(tenant)
    response = tenant.post(DRAWS, {**WINDOW, "seed": 3, "config": {"random_size": 1}})
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"coverage_version", "window", "draw"}
    assert len(_drawn_events(tenant.workspace_id)) == 1
    assert item_count() == 0 and review_events(tenant.workspace_id) == []
