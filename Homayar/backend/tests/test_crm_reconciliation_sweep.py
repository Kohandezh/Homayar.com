"""HOM-88 PR 2: the delayed-record sweep (DESIGN 4.5).

A CRM record can arrive before its conversation.  It waits as ``pending`` until
``expires_at``; the sweep retries it.  The tests check the four outcomes, that a second
run (or two runs at once) adds nothing, and that a record never reaches another
workspace's conversation.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend.jobs import crm_reconciliation
from backend.jobs.crm_reconciliation import reconcile_pending_records
from backend.tests._database import configured_database_url, is_postgres_url
from backend.tests.test_crm_mapping_api import (
    _conversation,
    _items,
    _ledger,
    _links,
    _owner,
    _payload,
    _record,
    _setup,
)


def _stored(persona, record_id: str):
    return {row.id: row for row in _ledger(persona)}[record_id]


def _expiry(record: dict) -> datetime:
    return datetime.fromisoformat(record["expires_at"])


def test_a_pending_record_waits_then_applies(client, email_outbox, settings):
    owner = _owner(client, email_outbox, "sweep-wait")
    _setup(owner)
    record = _record(owner, _payload("call-1", customer="C-1", outcome="WON"))
    assert record["state"] == "pending"

    counts = reconcile_pending_records(settings)
    assert counts["scanned"] == 1 and counts["still_pending"] == 1
    assert counts["applied"] == counts["reconciliation"] == 0
    stored = _stored(owner, record["id"])
    assert stored.state == "pending" and stored.attempt_count == 1
    assert stored.last_attempt_at is not None and stored.meeting_id is None

    meeting = _conversation(owner, "call-1")
    counts = reconcile_pending_records(settings)
    assert counts["scanned"] == 1 and counts["applied"] == 1 and counts["still_pending"] == 0
    stored = _stored(owner, record["id"])
    assert stored.state == "applied" and stored.meeting_id == meeting
    assert set(_links(owner, meeting)) == {("odoo", "customer"), ("odoo", "outcome")}

    # Nothing is pending any more.
    assert reconcile_pending_records(settings)["scanned"] == 0


def test_an_expired_record_goes_to_reconciliation(client, email_outbox, settings):
    owner = _owner(client, email_outbox, "sweep-expire")
    _setup(owner, late_record_window_seconds=3600)
    record = _record(owner, _payload("call-404", customer="C-1"))
    expires = _expiry(record)

    before = reconcile_pending_records(settings, now=expires - timedelta(seconds=1))
    assert before["still_pending"] == 1
    assert _stored(owner, record["id"]).state == "pending"

    after = reconcile_pending_records(settings, now=expires)
    assert after["reconciliation"] == 1 and after["still_pending"] == 0
    assert _stored(owner, record["id"]).state == "reconciliation"
    (item,) = _items(owner)
    assert item["reason"] == "conversation_not_found"
    assert item["inbound_record_id"] == record["id"]
    assert item["meeting_id"] is None and item["link_type"] is None

    # A second run finds nothing to do and opens no second item.
    assert reconcile_pending_records(settings, now=expires + timedelta(days=1))["scanned"] == 0
    assert len(_items(owner)) == 1


def test_two_matches_open_an_ambiguous_item(client, email_outbox, settings):
    owner = _owner(client, email_outbox, "sweep-ambiguous")
    _setup(owner)
    record = _record(owner, _payload("call-2", customer="C-1"))
    first, second = sorted([_conversation(owner, "call-2"), _conversation(owner, "call-2")])

    counts = reconcile_pending_records(settings)
    assert counts["reconciliation"] == 1
    (item,) = _items(owner)
    assert item["reason"] == "ambiguous_conversation"
    assert item["candidate_meeting_ids"] == [first, second]
    assert _links(owner, first) == _links(owner, second) == {}
    assert _stored(owner, record["id"]).state == "reconciliation"


def test_repeated_runs_add_nothing(client, email_outbox, settings):
    owner = _owner(client, email_outbox, "sweep-repeat")
    _setup(owner)
    _record(owner, _payload("call-1", customer="C-1", ticket="T-1", outcome="WON"))
    _record(owner, _payload("call-1", customer="C-2"))
    _record(owner, _payload("call-1", customer="C-2"))
    meeting = _conversation(owner, "call-1")

    first = reconcile_pending_records(settings)
    assert first["scanned"] == 3
    assert first["applied"] + first["reconciliation"] == 3
    links = _links(owner, meeting)
    assert set(links) == {("odoo", "customer"), ("odoo", "ticket"), ("odoo", "outcome")}
    assert links[("odoo", "customer")]["external_id"] == "C-1"
    (changed,) = _items(owner)
    assert changed["reason"] == "changed_id" and changed["candidate_value"] == "C-2"

    second = reconcile_pending_records(settings)
    assert second["scanned"] == 0
    assert _links(owner, meeting) == links and len(_items(owner)) == 1


def test_the_sweep_never_crosses_workspaces(client, email_outbox, settings):
    owner = _owner(client, email_outbox, "sweep-a")
    other = _owner(client, email_outbox, "sweep-b")
    _setup(owner)
    record = _record(owner, _payload("call-1", customer="C-1"))
    foreign = _conversation(other, "call-1")  # same (issabel, call-1), other workspace

    assert reconcile_pending_records(settings)["still_pending"] == 1
    assert _stored(owner, record["id"]).meeting_id is None
    assert _links(other, foreign) == {}

    own = _conversation(owner, "call-1")
    assert reconcile_pending_records(settings)["applied"] == 1
    assert _stored(owner, record["id"]).meeting_id == own
    assert _links(other, foreign) == {}


def test_the_batch_limit_and_the_operator_entry_point(client, email_outbox, settings):
    owner = _owner(client, email_outbox, "sweep-limit")
    _setup(owner)
    for call in ("call-1", "call-2", "call-3"):
        _record(owner, _payload(call, customer="C-1"))
    assert reconcile_pending_records(settings, limit=2)["scanned"] == 2
    assert reconcile_pending_records(settings)["scanned"] == 3
    source = Path(crm_reconciliation.__file__).read_text()
    assert 'if __name__ == "__main__":' in source


@pytest.mark.postgres
def test_two_sweeps_at_once_link_once_and_open_one_item(client, email_outbox, settings):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")
    owner = _owner(client, email_outbox, "sweep-race")
    _setup(owner)
    for _ in range(3):
        _record(owner, _payload("call-1", customer="C-1", outcome="WON"))
    for _ in range(3):
        _record(owner, _payload("call-1", customer="C-2"))
    meeting = _conversation(owner, "call-1")

    results: list[dict[str, int]] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def sweep() -> None:
        try:
            barrier.wait(timeout=10)
            results.append(reconcile_pending_records(settings, now=datetime.now(UTC)))
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=sweep) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not errors, errors
    assert sum(r["applied"] + r["reconciliation"] for r in results) == 6
    links = _links(owner, meeting)
    assert set(links) == {("odoo", "customer"), ("odoo", "outcome")}
    assert links[("odoo", "customer")]["external_id"] == "C-1"
    (changed,) = _items(owner)
    assert changed["reason"] == "changed_id" and changed["candidate_value"] == "C-2"
    assert {row.state for row in _ledger(owner)} <= {"applied", "reconciliation"}


def test_a_backlog_bigger_than_the_batch_rotates(client, email_outbox, settings):
    """F2: with batch size 1 every pending record is visited, and a record whose
    conversation exists is applied while older records stay pending."""
    owner = _owner(client, email_outbox, "sweep-rotate")
    _setup(owner)
    records = [_record(owner, _payload(call, customer="C-1")) for call in ("c-1", "c-2", "c-3")]
    meeting = _conversation(owner, "c-3")  # only the newest record can be applied

    for _ in range(3):
        assert reconcile_pending_records(settings, limit=1)["scanned"] == 1
    oldest, middle, newest = (_stored(owner, record["id"]) for record in records)
    assert (oldest.state, oldest.attempt_count) == ("pending", 1)
    assert (middle.state, middle.attempt_count) == ("pending", 1)
    assert newest.state == "applied" and newest.meeting_id == meeting

    # The rotation goes on: the least recently tried record is next.
    reconcile_pending_records(settings, limit=1)
    assert _stored(owner, records[0]["id"]).attempt_count == 2
    assert _stored(owner, records[1]["id"]).attempt_count == 1


def test_an_older_delayed_record_never_changes_is_active(client, email_outbox, settings):
    """Addendum 4 F1: ``is_active`` follows the newest record, never an older delayed one."""
    from backend.tests.test_crm_mapping_api import _external_records, _item

    owner = _owner(client, email_outbox, "sweep-older")
    _setup(owner)
    older = _record(owner, _payload("call-1", customer="C-5", customer_active=True))
    assert older["state"] == "pending"  # its conversation is not there yet
    _conversation(owner, "call-2")
    newer = _record(owner, _payload("call-2", customer="C-5", customer_active=False))
    assert newer["state"] == "reconciliation"
    assert ("odoo", "customer", "C-5", False) in _external_records(owner)

    meeting = _conversation(owner, "call-1")
    assert reconcile_pending_records(settings)["reconciliation"] == 1
    assert ("odoo", "customer", "C-5", False) in _external_records(owner)  # still inactive
    assert _links(owner, meeting) == {}
    stored = _stored(owner, older["id"])
    assert stored.state == "reconciliation"
    reasons = {_item(owner, item_id)["reason"] for item_id in _items_of(owner, older["id"])}
    assert reasons == {"inactive_record"}

    _conversation(owner, "call-3")
    silent = _record(owner, _payload("call-3", customer="C-5"))
    assert silent["state"] == "reconciliation"
    assert _item(owner, silent["reconciliation_item_ids"][0])["reason"] == "inactive_record"
    assert ("odoo", "customer", "C-5", False) in _external_records(owner)


def _items_of(persona, record_id: str) -> list[str]:
    return [item["id"] for item in _items(persona) if item["inbound_record_id"] == record_id]


def test_an_older_delayed_false_still_deactivates(client, email_outbox, settings):
    """Addendum 5 R1: an explicit ``false`` always applies (the safe side); an explicit
    ``true`` applies only from a record that is not older than ``last_seen_at``."""
    from backend.tests.test_crm_mapping_api import _external_records, _item

    owner = _owner(client, email_outbox, "sweep-false")
    _setup(owner)
    _conversation(owner, "call-0")
    assert (
        _record(owner, _payload("call-0", customer="C-5", customer_active=True))["state"]
        == "applied"
    )
    late_false = _record(owner, _payload("call-6", customer="C-5", customer_active=False))
    assert late_false["state"] == "pending"  # its call is late
    newer = _conversation(owner, "call-7")
    assert _record(owner, _payload("call-7", customer="C-5"))["state"] == "applied"
    assert _links(owner, newer)[("odoo", "customer")]["external_id"] == "C-5"

    late_meeting = _conversation(owner, "call-6")
    assert reconcile_pending_records(settings)["reconciliation"] == 1
    assert ("odoo", "customer", "C-5", False) in _external_records(owner)
    assert _links(owner, late_meeting) == {}
    reasons = {_item(owner, i)["reason"] for i in _items_of(owner, late_false["id"])}
    assert reasons == {"inactive_record"}

    _conversation(owner, "call-8")
    silent = _record(owner, _payload("call-8", customer="C-5"))
    assert silent["state"] == "reconciliation"
    assert _item(owner, silent["reconciliation_item_ids"][0])["reason"] == "inactive_record"

    reactivated_meeting = _conversation(owner, "call-9")
    reactivated = _record(owner, _payload("call-9", customer="C-5", customer_active=True))
    assert reactivated["state"] == "applied"
    assert ("odoo", "customer", "C-5", True) in _external_records(owner)
    assert _links(owner, reactivated_meeting)[("odoo", "customer")]["external_id"] == "C-5"
