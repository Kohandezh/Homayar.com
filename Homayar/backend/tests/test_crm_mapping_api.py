"""HOM-88 PR 2: the CRM mapping API (DESIGN sections 3 to 6, SPEC section 4).

Mapping profiles, received records, the reconciliation queue and the conversation links.
Every deny case checks the status code and the body's ``error.code``; the closest allow
case passes.  Fixtures for F64: a profile; known call, customer, ticket, agent and outcome
records that link; missing, changed and late ids that go to reconciliation; two customers
with the same phone that stay two records; the same customer id in two source systems
that stays two records.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.database.saas_models import (
    AuditEvent,
    ConversationExternalLink,
    ExternalRecord,
    IntegrationInboundRecord,
    IntegrationMappingProfile,
    IntegrationReconciliationItem,
    Meeting,
    MeetingStatus,
)
from backend.services import crm_mapping_service
from backend.services.audit_event_types import AuditEventType
from backend.tests._database import inspection_session_factory
from backend.tests.conftest import login_user, register_user
from backend.tests.test_api_keys import _bearer, _mint
from backend.tests.test_authorization_matrix import _cookies, _Persona, _workspace_with_roles

PROFILES = "/api/v1/integrations/mapping-profiles"
RECONCILIATION = "/api/v1/integrations/reconciliation"
FIELD_MAP = {
    "conversation_id": "call.id",
    "customer_id": "customer.id",
    "ticket_id": "ticket.id",
    "agent_id": "agent.id",
    "outcome": "result.code",
    "customer_active": "customer.active",
    "ticket_active": "ticket.active",
}
PHONE = "+989121234567"
NAME = "Sara Ahmadi"


# --- helpers -----------------------------------------------------------------------------


def _code(response) -> str:
    return response.json()["error"]["code"]


def _owner(client, email_outbox, label: str) -> _Persona:
    credentials = register_user(
        client, email_outbox, email=f"crm-{label}@example.com", name=f"CRM {label}"
    )
    session = login_user(client, credentials)
    return _Persona(client, session["workspaces"][0]["id"], session, _cookies(client))


def _put(persona: _Persona, system: str = "odoo", **body):
    return persona.client.put(
        f"{PROFILES}/{system}", json=body, cookies=persona.cookies, headers=persona.headers
    )


def _profile_body(**extra) -> dict:
    return {
        "conversation_source_system": "issabel",
        "field_map": dict(FIELD_MAP),
        "outcome_map": {"WON": "won", "LOST": "lost"},
        **extra,
    }


def _setup(persona: _Persona, system: str = "odoo", **extra) -> dict:
    response = _put(persona, system, **_profile_body(**extra))
    assert response.status_code == 201, response.text
    return response.json()


def _payload(
    call: str | None = "call-1",
    *,
    customer: object = None,
    ticket: object = None,
    agent: object = None,
    outcome: object = None,
    customer_active: object = None,
    ticket_active: object = None,
) -> dict:
    """A CRM payload with unmapped personal data next to every mapped id."""
    payload: dict = {"call": {"id": call, "caller": PHONE}, "note": f"called by {NAME}"}
    if call is None:
        payload["call"].pop("id")
    if customer is not None or customer_active is not None:
        payload["customer"] = {"phone": PHONE, "name": NAME}
        if customer is not None:
            payload["customer"]["id"] = customer
        if customer_active is not None:
            payload["customer"]["active"] = customer_active
    if ticket is not None or ticket_active is not None:
        payload["ticket"] = {"subject": f"{NAME} complaint"}
        if ticket is not None:
            payload["ticket"]["id"] = ticket
        if ticket_active is not None:
            payload["ticket"]["active"] = ticket_active
    if agent is not None:
        payload["agent"] = {"id": agent, "name": "Agent Reza"}
    if outcome is not None:
        payload["result"] = {"code": outcome}
    return payload


def _post(persona: _Persona, payload: dict, *, system: str = "odoo", event_id: str | None = None):
    return persona.post(
        f"/api/v1/integrations/{system}/records",
        json={"event_id": event_id or f"evt-{uuid.uuid4().hex[:10]}", "payload": payload},
    )


def _record(persona: _Persona, payload: dict, **kwargs) -> dict:
    response = _post(persona, payload, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


def _conversation(
    persona: _Persona,
    call_id: str = "call-1",
    *,
    system: str = "issabel",
    deleted: bool = False,
    team_id: str | None = None,
) -> str:
    with inspection_session_factory()() as db:
        meeting = Meeting(
            workspace_id=persona.workspace_id,
            created_by_user_id=persona.user_id,
            title=f"call {call_id}",
            status=MeetingStatus.COMPLETED,
            duration_seconds=30,
            team_id=team_id,
            external_source_system=system,
            external_conversation_id=call_id,
            deleted_at=datetime.now(UTC) if deleted else None,
        )
        db.add(meeting)
        db.commit()
        return meeting.id


def _links(persona: _Persona, meeting_id: str) -> dict[str, dict]:
    response = persona.get(f"/api/v1/conversations/{meeting_id}/external-links")
    assert response.status_code == 200, response.text
    return {(item["source_system"], item["link_type"]): item for item in response.json()["items"]}


def _items(persona: _Persona, **params) -> list[dict]:
    response = persona.get(RECONCILIATION, params=params)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def _item(persona: _Persona, item_id: str) -> dict:
    response = persona.get(f"{RECONCILIATION}/{item_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _resolve(persona: _Persona, item_id: str, **body):
    return persona.post(f"{RECONCILIATION}/{item_id}/resolve", json=body)


def _audits(event_type: str) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == event_type)
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def _external_records(persona: _Persona) -> set[tuple[str, str, str, bool]]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(ExternalRecord).where(ExternalRecord.workspace_id == persona.workspace_id)
        )
        return {(r.source_system, r.record_type, r.external_id, r.is_active) for r in rows}


def _ledger(persona: _Persona) -> list[IntegrationInboundRecord]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(IntegrationInboundRecord).where(
                    IntegrationInboundRecord.workspace_id == persona.workspace_id
                )
            )
        )


def _remove_member(owner: _Persona, membership_id: str) -> None:
    removed = owner.delete(f"/api/v1/members/{membership_id}")
    assert removed.status_code == 204, removed.text


# --- mapping profiles ----------------------------------------------------------------------


def test_profiles_need_workspace_manage_and_a_slug(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    member = people["member"]

    denied = _put(member, "odoo", **_profile_body())
    assert denied.status_code == 403 and _code(denied) == "permission_denied"
    assert member.get(PROFILES).status_code == 403
    assert member.get(f"{PROFILES}/odoo").status_code == 403

    for slug in ("Odoo", "1odoo", "odoo crm", "-odoo", "a" * 61):
        refused = _put(owner, slug, **_profile_body())
        assert refused.status_code == 422 and _code(refused) == "invalid_source_system", slug
    # The body's conversation system follows the same slug rule (never a 500).
    for slug in ("Issabel", "1pbx", "", 5, "a" * 61):
        refused = _put(owner, "odoo", **_profile_body(conversation_source_system=slug))
        assert refused.status_code == 422 and _code(refused) == "invalid_source_system", slug
    created = _put(owner, "odoo", **_profile_body())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source_system"] == "odoo" and body["version"] == 1
    assert set(body) == {
        "source_system",
        "conversation_source_system",
        "field_map",
        "outcome_map",
        "agent_map",
        "late_record_window_seconds",
        "is_active",
        "version",
        "created_at",
        "updated_at",
    }

    # Defaults: the conversation system is the path slug; the maps are empty.
    minimal = _put(owner, "hamkaran-crm", field_map={"conversation_id": "call.id"})
    assert minimal.status_code == 201, minimal.text
    assert minimal.json()["conversation_source_system"] == "hamkaran-crm"
    assert minimal.json()["outcome_map"] == {} and minimal.json()["agent_map"] == {}
    assert minimal.json()["late_record_window_seconds"] == 604800
    assert minimal.json()["is_active"] is True

    listed = owner.get(PROFILES)
    assert listed.status_code == 200
    assert [p["source_system"] for p in listed.json()["items"]] == ["hamkaran-crm", "odoo"]
    assert owner.get(f"{PROFILES}/odoo").json()["field_map"] == FIELD_MAP
    missing = owner.get(f"{PROFILES}/zoho")
    assert missing.status_code == 404 and _code(missing) == "mapping_profile_not_found"

    # Extra fields are refused.
    extra = _put(owner, "odoo", **_profile_body(unknown_field="x"))
    assert extra.status_code == 422


def test_field_map_rules_and_the_phone_guard(client, email_outbox):
    owner = _owner(client, email_outbox, "fieldmap")

    def refused(field_map: object, code: str = "invalid_field_map") -> None:
        response = _put(owner, "odoo", field_map=field_map)
        assert response.status_code == 422 and _code(response) == code, (field_map, response.text)

    refused({"customer_id": "customer.id"})  # conversation_id is required
    refused({"conversation_id": "call.id", "customer_name": "customer.name"})
    for path in ("call..id", "call id", ".call", "call.", "", "a.b.c.d.e.f.g.h.i", "x" * 65):
        refused({"conversation_id": path})
    refused({"conversation_id": 7})
    refused({"conversation_id": "call.id", "customer_id": None})

    for path in (
        "partner.phone",
        "customer.mobile_number",
        "c.caller_id",
        "c.ani",
        "c.CLI",
        "c.tel",
        "c.Telephone",
        "c.msisdn_e164",
        "c.callerid",
        "c.caller_number",
        "Phone",
    ):
        refused({"conversation_id": "call.id", "customer_id": path}, "phone_identity_forbidden")

    allowed = _put(
        owner,
        "odoo",
        field_map={
            "conversation_id": "a.b.c.d.e.f.g.h",
            "customer_id": "partner.id",
            "ticket_id": "t.phone_ticket",  # the guard is for the customer id only
            "agent_id": "agent.cli",
            "outcome": "x" * 64,
        },
    )
    assert allowed.status_code == 201, allowed.text


def test_outcome_agent_maps_and_the_late_window(client, email_outbox, monkeypatch):
    owner, people, memberships = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"agent": "member", "gone": "member"}
    )
    stranger = _owner(client, email_outbox, "stranger")
    _remove_member(owner, memberships["gone"])

    def refused(code: str, **body) -> None:
        response = _put(owner, "odoo", **_profile_body(**body))
        assert response.status_code == 422 and _code(response) == code, (body, response.text)

    refused("invalid_outcome_map", outcome_map={"SOLD": "sold"})
    refused("invalid_outcome_map", outcome_map={"": "won"})
    refused("invalid_outcome_map", outcome_map={"W" * 201: "won"})
    refused("invalid_outcome_map", outcome_map={"WON": 1})
    refused("invalid_agent_map", agent_map={"u-1": stranger.user_id})
    refused("invalid_agent_map", agent_map={"u-1": people["gone"].user_id})
    refused("invalid_agent_map", agent_map={"u-1": str(uuid.uuid4())})
    refused("invalid_agent_map", agent_map={"": people["agent"].user_id})
    refused("invalid_agent_map", agent_map={"u-1": 5})
    for seconds in (3599, 2592001, 0, -1, "3600", 3600.5, True, None):
        refused("invalid_late_window", late_record_window_seconds=seconds)
    refused("invalid_field_map", field_map=["call.id"])
    refused("invalid_outcome_map", outcome_map=["WON"])
    refused("invalid_agent_map", agent_map="u-1")

    created = _put(
        owner,
        "odoo",
        **_profile_body(
            outcome_map={"WON": "won", "F/U": "follow_up"},
            agent_map={"u-17": people["agent"].user_id},
            late_record_window_seconds=3600,
        ),
    )
    assert created.status_code == 201, created.text
    widest = _put(owner, "hamkaran", **_profile_body(late_record_window_seconds=2592000))
    assert widest.status_code == 201, widest.text


def test_a_put_that_changes_nothing_keeps_the_version_and_writes_no_audit(client, email_outbox):
    owner = _owner(client, email_outbox, "versions")
    body = _profile_body(agent_map={"u-1": owner.user_id})
    assert _put(owner, "odoo", **body).status_code == 201
    first = _audits(AuditEventType.INTEGRATION_MAPPING_UPDATED)
    assert len(first) == 1

    again = _put(owner, "odoo", **body)
    assert again.status_code == 200 and again.json()["version"] == 1
    assert len(_audits(AuditEventType.INTEGRATION_MAPPING_UPDATED)) == 1

    changed = _put(owner, "odoo", **{**body, "is_active": False})
    assert changed.status_code == 200 and changed.json()["version"] == 2
    assert changed.json()["is_active"] is False
    events = _audits(AuditEventType.INTEGRATION_MAPPING_UPDATED)
    assert len(events) == 2
    latest = events[-1]
    assert latest.target_type == "integration_mapping_profile"
    assert latest.workspace_id == owner.workspace_id
    assert latest.details["fields"] == "is_active"
    # Ids, field names and codes only: no map value reaches the audit row.
    serialized = json.dumps([event.details for event in events])
    assert owner.user_id not in serialized and "call.id" not in serialized

    # Leaving out an optional field resets it (PUT replaces the profile).
    replaced = _put(owner, "odoo", field_map=dict(FIELD_MAP), conversation_source_system="issabel")
    assert replaced.status_code == 200 and replaced.json()["version"] == 3
    assert replaced.json()["agent_map"] == {} and replaced.json()["is_active"] is True


# --- records: refusals ---------------------------------------------------------------------


def test_record_refusals_before_anything_is_stored(client, email_outbox):
    owner = _owner(client, email_outbox, "refusals")

    missing = _post(owner, _payload())
    assert missing.status_code == 404 and _code(missing) == "mapping_profile_not_found"
    bad_slug = _post(owner, _payload(), system="Odoo")
    assert bad_slug.status_code == 422 and _code(bad_slug) == "invalid_source_system"

    _setup(owner, is_active=False)
    inactive = _post(owner, _payload())
    assert inactive.status_code == 409 and _code(inactive) == "mapping_profile_inactive"
    assert _put(owner, "odoo", **_profile_body()).status_code == 200

    no_call = _post(owner, _payload(call=None))
    assert no_call.status_code == 422 and _code(no_call) == "conversation_id_missing"
    for blank in ("", "   ", None):
        payload = _payload()
        payload["call"]["id"] = blank
        response = _post(owner, payload)
        assert response.status_code == 422 and _code(response) == "conversation_id_missing"

    for value in (["C-1"], 1.5, True, "C" * 201, {"id": "C-1"}):
        response = _post(owner, _payload(customer=value))
        assert response.status_code == 422 and _code(response) == "invalid_field_value", value
        assert "customer_id" in response.json()["error"]["message"]
    for value in ("false", 0, "yes"):
        response = _post(owner, _payload(customer="C-1", customer_active=value))
        assert response.status_code == 422 and _code(response) == "invalid_field_value", value
        assert "customer_active" in response.json()["error"]["message"]

    for size in (65537, 66000):
        big = _payload()
        big["blob"] = "x" * size
        too_large = _post(owner, big)
        assert too_large.status_code == 413 and _code(too_large) == "payload_too_large", size

    # Extra fields and a bad event id are refused by the body schema.
    extra = owner.post(
        "/api/v1/integrations/odoo/records",
        json={"event_id": "e-1", "payload": _payload(), "raw": "x"},
    )
    assert extra.status_code == 422
    for event_id in ("", "e" * 201):
        refused = owner.post(
            "/api/v1/integrations/odoo/records", json={"event_id": event_id, "payload": {}}
        )
        assert refused.status_code == 422
    assert _ledger(owner) == []  # nothing was stored

    # Allow controls.
    assert _post(owner, _payload(customer="C-1")).status_code == 201
    record = _record(owner, _payload(customer=17, customer_active=False))
    stored = {row.id: row for row in _ledger(owner)}[record["id"]]
    assert stored.extracted["customer_id"] == "17"
    assert stored.extracted["customer_active"] is False
    fits = _payload()
    fits["blob"] = "x" * 60000
    assert _post(owner, fits).status_code == 201


def test_replay_and_conflict(client, email_outbox):
    owner = _owner(client, email_outbox, "replay")
    _setup(owner)
    payload = {"call": {"id": "call-9"}, "customer": {"id": "C-1", "phone": PHONE}}
    first = _record(owner, payload, event_id="evt-1")
    assert first["replayed"] is False and first["state"] == "pending"

    reordered = {"customer": {"phone": PHONE, "id": "C-1"}, "call": {"id": "call-9"}}
    replay = _post(owner, reordered, event_id="evt-1")
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True and replay.json()["id"] == first["id"]
    assert replay.json()["state"] == "pending"
    assert len(_ledger(owner)) == 1

    conflict = _post(owner, {"call": {"id": "call-9"}, "customer": {"id": "C-2"}}, event_id="evt-1")
    assert conflict.status_code == 409 and _code(conflict) == "inbound_record_conflict"
    (stored,) = _ledger(owner)
    assert stored.conflict_count == 1 and stored.last_conflict_at is not None
    assert stored.extracted["customer_id"] == "C-1"  # never overwritten
    (event,) = _audits(AuditEventType.INTEGRATION_RECORD_CONFLICT)
    assert event.target_type == "integration_inbound_record" and event.target_id == stored.id
    assert event.details["conflict_count"] == 1
    assert "C-2" not in json.dumps(event.details) and PHONE not in json.dumps(event.details)

    # The same event id in another source system is another record.
    _setup(owner, "hamkaran")
    assert _post(owner, payload, system="hamkaran", event_id="evt-1").status_code == 201


def test_a_concurrent_second_insert_loses_on_the_unique_index(client, email_outbox, monkeypatch):
    """The lookup before the insert misses the row a concurrent request committed: the
    insert fails on ``uq_inbound_record_event`` and the request is answered from that row."""
    owner = _owner(client, email_outbox, "race")
    _setup(owner)
    payload = _payload(customer="C-1")
    first = _record(owner, payload, event_id="evt-race")

    real = crm_mapping_service.find_inbound_record
    calls: list[int] = []

    def miss_once(db, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return None  # the concurrent row is not visible yet
        return real(db, **kwargs)

    monkeypatch.setattr(crm_mapping_service, "find_inbound_record", miss_once)
    replay = _post(owner, payload, event_id="evt-race")
    assert replay.status_code == 200 and replay.json()["replayed"] is True
    assert replay.json()["id"] == first["id"]

    calls.clear()
    conflict = _post(owner, _payload(customer="C-2"), event_id="evt-race")
    assert conflict.status_code == 409 and _code(conflict) == "inbound_record_conflict"
    assert len(_ledger(owner)) == 1
    # PostgreSQL names the constraint; SQLite names its columns.
    unique = "uq_inbound_record_event|UNIQUE constraint failed: integration_inbound_records"
    with pytest.raises(IntegrityError, match=unique):
        with inspection_session_factory()() as db:
            stored = _ledger(owner)[0]
            db.add(
                IntegrationInboundRecord(
                    organization_id=stored.organization_id,
                    workspace_id=stored.workspace_id,
                    profile_id=stored.profile_id,
                    source_system="odoo",
                    event_id="evt-race",
                    payload_hash="0" * 64,
                    extracted={},
                    state="applied",
                    received_at=datetime.now(UTC),
                )
            )
            db.commit()


def test_record_scope_and_api_key_scopes(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"scoped": "member"}
    )
    _setup(owner)
    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200, switched.text

    scoped = _post(people["scoped"], _payload())
    assert scoped.status_code == 403 and _code(scoped) == "record_scope_restricted"

    assert _post(owner, _payload()).status_code == 201
    writer = _mint(client, owner.headers, owner.cookies, name="crm", scopes=["meeting:update"])
    reader = _mint(client, owner.headers, owner.cookies, name="ro", scopes=["meeting:read"])
    assert writer.status_code == reader.status_code == 201, writer.text
    body = {"event_id": "evt-key", "payload": _payload()}
    with_scope = client.post(
        "/api/v1/integrations/odoo/records", json=body, headers=_bearer(writer.json()["key"])
    )
    assert with_scope.status_code == 201, with_scope.text
    stored = {row.event_id: row for row in _ledger(owner)}["evt-key"]
    assert stored.api_key_id == writer.json()["id"]
    without = client.post(
        "/api/v1/integrations/odoo/records",
        json={**body, "event_id": "evt-key-2"},
        headers=_bearer(reader.json()["key"]),
    )
    assert without.status_code == 403 and _code(without) == "api_key_scope_denied"

    # Keys never manage profiles or the queue (workspace:manage is session only).
    for path in (PROFILES, RECONCILIATION):
        response = client.get(path, headers=_bearer(writer.json()["key"]))
        assert response.status_code == 403


# --- records: matching and linking ---------------------------------------------------------


def test_conversation_matching(client, email_outbox):
    owner = _owner(client, email_outbox, "matching")
    profile = _setup(owner)

    pending = _record(owner, _payload("call-404", customer="C-1"))
    assert pending["state"] == "pending" and pending["meeting_id"] is None
    received = datetime.fromisoformat(pending["received_at"])
    expires = datetime.fromisoformat(pending["expires_at"])
    assert expires - received == timedelta(seconds=profile["late_record_window_seconds"])
    assert pending["reconciliation_item_ids"] == []

    _conversation(owner, "call-asterisk", system="asterisk")
    other_system = _record(owner, _payload("call-asterisk", customer="C-1"))
    assert other_system["state"] == "pending"
    _conversation(owner, "call-deleted", deleted=True)
    deleted = _record(owner, _payload("call-deleted", customer="C-1"))
    assert deleted["state"] == "pending"

    live = _conversation(owner, "call-1")
    applied = _record(owner, _payload("call-1", customer="C-1", ticket="T-1", outcome="WON"))
    assert applied["state"] == "applied" and applied["meeting_id"] == live
    assert applied["expires_at"] is None and applied["reconciliation_item_ids"] == []
    links = _links(owner, live)
    assert set(links) == {("odoo", "customer"), ("odoo", "ticket"), ("odoo", "outcome")}
    assert links[("odoo", "customer")]["external_id"] == "C-1"
    assert links[("odoo", "customer")]["is_active"] is True
    assert links[("odoo", "customer")]["linked_by_user_id"] is None
    assert links[("odoo", "customer")]["profile_version"] == 1
    assert links[("odoo", "outcome")]["outcome"] == "won"
    assert links[("odoo", "outcome")]["external_id"] == "WON"
    assert links[("odoo", "outcome")]["is_active"] is None

    first, second = sorted([_conversation(owner, "call-2"), _conversation(owner, "call-2")])
    ambiguous = _record(owner, _payload("call-2", customer="C-1"))
    assert ambiguous["state"] == "reconciliation" and ambiguous["meeting_id"] is None
    (item_id,) = ambiguous["reconciliation_item_ids"]
    item = _item(owner, item_id)
    assert item["reason"] == "ambiguous_conversation"
    assert item["candidate_meeting_ids"] == [first, second]
    assert item["meeting_id"] is None and item["link_type"] is None
    assert _links(owner, first) == {} and _links(owner, second) == {}


def test_changed_ids_never_overwrite_and_open_one_item(client, email_outbox):
    owner = _owner(client, email_outbox, "changed")
    _setup(owner)
    meeting = _conversation(owner, "call-1")
    assert _record(owner, _payload(customer="C-1"))["state"] == "applied"

    changed = _record(owner, _payload(customer="C-2"))
    assert changed["state"] == "reconciliation"
    (item_id,) = changed["reconciliation_item_ids"]
    item = _item(owner, item_id)
    assert (item["reason"], item["link_type"], item["meeting_id"]) == (
        "changed_id",
        "customer",
        meeting,
    )
    assert (item["current_value"], item["candidate_value"]) == ("C-1", "C-2")
    assert _links(owner, meeting)[("odoo", "customer")]["external_id"] == "C-1"

    same = _record(owner, _payload(customer="C-1"))
    assert same["state"] == "applied" and same["reconciliation_item_ids"] == []

    again = _record(owner, _payload(customer="C-2"))
    assert again["state"] == "reconciliation"
    assert again["reconciliation_item_ids"] == [item_id]  # reused, not a second item
    assert len(_items(owner, reason="changed_id")) == 1

    third = _record(owner, _payload(customer="C-3"))
    assert len(third["reconciliation_item_ids"]) == 1
    assert third["reconciliation_item_ids"] != [item_id]
    assert len(_items(owner, reason="changed_id")) == 2
    assert _links(owner, meeting)[("odoo", "customer")]["external_id"] == "C-1"


def test_agents_map_to_active_members_only(client, email_outbox, monkeypatch):
    owner, people, memberships = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"agent": "member", "gone": "member"}
    )
    _setup(owner, agent_map={"u-17": people["agent"].user_id, "u-50": people["gone"].user_id})
    _remove_member(owner, memberships["gone"])

    unknown_meeting = _conversation(owner, "call-1")
    unknown = _record(owner, _payload("call-1", customer="C-1", agent="u-99"))
    assert unknown["state"] == "reconciliation"
    (item_id,) = unknown["reconciliation_item_ids"]
    assert _item(owner, item_id)["reason"] == "unknown_agent"
    assert _item(owner, item_id)["candidate_value"] == "u-99"
    links = _links(owner, unknown_meeting)
    assert set(links) == {("odoo", "customer")}  # the clean entity still links

    removed_meeting = _conversation(owner, "call-2")
    removed = _record(owner, _payload("call-2", agent="u-50"))
    assert removed["state"] == "reconciliation"
    assert _item(owner, removed["reconciliation_item_ids"][0])["reason"] == "unknown_agent"
    assert _links(owner, removed_meeting) == {}

    known_meeting = _conversation(owner, "call-3")
    known = _record(owner, _payload("call-3", agent="u-17"))
    assert known["state"] == "applied"
    agent_link = _links(owner, known_meeting)[("odoo", "agent")]
    assert agent_link["agent_user_id"] == people["agent"].user_id
    assert agent_link["external_id"] == "u-17"
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, known_meeting)
        assert meeting.agent_user_id is None  # record scope never changes
    assert ("odoo", "agent", "u-99", True) in _external_records(owner)


def test_outcomes_match_exactly(client, email_outbox):
    owner = _owner(client, email_outbox, "outcomes")
    _setup(owner)
    for call, code in (("call-1", "MAYBE"), ("call-2", "won"), ("call-3", " WON ")):
        meeting = _conversation(owner, call)
        record = _record(owner, _payload(call, outcome=code))
        expected = "applied" if code.strip() == "WON" else "reconciliation"
        assert record["state"] == expected, code
        if expected == "reconciliation":
            assert _item(owner, record["reconciliation_item_ids"][0])["reason"] == "unknown_outcome"
            assert _links(owner, meeting) == {}
        else:
            link = _links(owner, meeting)[("odoo", "outcome")]
            assert (link["outcome"], link["external_id"]) == ("won", "WON")


def test_inactive_upstream_records_are_respected(client, email_outbox):
    owner = _owner(client, email_outbox, "inactive")
    _setup(owner)
    meeting = _conversation(owner, "call-1")

    inactive = _record(owner, _payload(customer="C-5", customer_active=False))
    assert inactive["state"] == "reconciliation"
    (item_id,) = inactive["reconciliation_item_ids"]
    assert _item(owner, item_id)["reason"] == "inactive_record"
    assert ("odoo", "customer", "C-5", False) in _external_records(owner)
    assert _links(owner, meeting) == {}

    silent = _record(owner, _payload(customer="C-5"))
    assert silent["state"] == "reconciliation"
    assert silent["reconciliation_item_ids"] == [item_id]
    assert ("odoo", "customer", "C-5", False) in _external_records(owner)  # never reactivated
    assert _links(owner, meeting) == {}

    reactivated = _record(owner, _payload(customer="C-5", customer_active=True))
    assert reactivated["state"] == "applied"
    assert ("odoo", "customer", "C-5", True) in _external_records(owner)
    assert _links(owner, meeting)[("odoo", "customer")]["external_id"] == "C-5"

    # A ticket follows the same rule with ticket_active.
    ticket_meeting = _conversation(owner, "call-2")
    ticket = _record(owner, _payload("call-2", ticket="T-1", ticket_active=False))
    assert _item(owner, ticket["reconciliation_item_ids"][0])["link_type"] == "ticket"
    assert _links(owner, ticket_meeting) == {}


def test_no_phone_merge_no_cross_system_merge_and_no_payload_values_stored(client, email_outbox):
    owner = _owner(client, email_outbox, "merge")
    _setup(owner)
    _setup(owner, "hamkaran")
    _conversation(owner, "call-1")
    _conversation(owner, "call-2")
    _record(owner, _payload("call-1", customer="C-1"))
    _record(owner, _payload("call-2", customer="C-2"))  # same phone, another customer
    _record(owner, _payload("call-1", customer="C-1"), system="hamkaran")
    _record(owner, _payload("call-3", customer="C-1", ticket="T-1", outcome="WON"))
    _post(owner, _payload("call-1", customer="C-1"), event_id="evt-x")
    _post(owner, _payload("call-1", customer="C-9"), event_id="evt-x")  # a conflict

    customers = {
        (system, external_id)
        for system, kind, external_id, _ in _external_records(owner)
        if kind == "customer"
    }
    assert customers == {("odoo", "C-1"), ("odoo", "C-2"), ("hamkaran", "C-1")}
    assert any(row.extracted["customer_id"] == "C-1" for row in _ledger(owner))

    # No unmapped value reaches any column of the five tables or any audit row.
    with inspection_session_factory()() as db:
        dumped = []
        for model in (
            IntegrationMappingProfile,
            ExternalRecord,
            IntegrationInboundRecord,
            ConversationExternalLink,
            IntegrationReconciliationItem,
            AuditEvent,
        ):
            for row in db.scalars(select(model)):
                dumped.append(
                    json.dumps(
                        {c.name: getattr(row, c.key) for c in model.__table__.columns},
                        default=str,
                    )
                )
    everything = "\n".join(dumped)
    for value in (PHONE, NAME, "Sara", "Agent Reza", "complaint", "called by"):
        assert value not in everything, value
    assert set(_ledger(owner)[0].extracted) == set(FIELD_MAP)


# --- reconciliation queue ------------------------------------------------------------------


def test_the_queue_needs_workspace_manage_and_pages(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    _setup(owner)
    meeting = _conversation(owner, "call-1")
    _record(owner, _payload(customer="C-1"))
    ids = []
    for value in ("C-2", "C-3", "C-4"):
        ids.extend(_record(owner, _payload(customer=value))["reconciliation_item_ids"])
    unknown = _record(owner, _payload(outcome="MAYBE"))["reconciliation_item_ids"]

    denied = people["member"].get(RECONCILIATION)
    assert denied.status_code == 403 and _code(denied) == "permission_denied"
    assert people["member"].get(f"{RECONCILIATION}/{ids[0]}").status_code == 403

    everything = _items(owner)
    assert {item["id"] for item in everything} == set(ids + unknown)
    assert all(item["status"] == "open" for item in everything)
    assert [item["created_at"] for item in everything] == sorted(
        (item["created_at"] for item in everything), reverse=True
    )
    assert {item["id"] for item in _items(owner, reason="unknown_outcome")} == set(unknown)
    assert _items(owner, source_system="hamkaran") == []
    assert _items(owner, status="resolved") == []

    page = owner.get(RECONCILIATION, params={"limit": 2})
    assert page.status_code == 200 and len(page.json()["items"]) == 2
    rest = owner.get(RECONCILIATION, params={"limit": 2, "before": page.json()["next_before"]})
    assert len(rest.json()["items"]) == 2 and rest.json()["next_before"] is not None
    for params in ({"limit": 0}, {"limit": 101}, {"status": "closed"}, {"reason": "fuzzy"}):
        assert owner.get(RECONCILIATION, params=params).status_code == 422, params

    item = _item(owner, ids[0])
    assert set(item) == {
        "id",
        "source_system",
        "inbound_record_id",
        "meeting_id",
        "link_type",
        "reason",
        "status",
        "current_value",
        "candidate_value",
        "candidate_meeting_ids",
        "resolution",
        "resolved_by_user_id",
        "resolved_at",
        "created_at",
    }
    assert item["meeting_id"] == meeting

    other = _owner(client, email_outbox, "queue-other")
    hidden = other.get(f"{RECONCILIATION}/{ids[0]}")
    assert hidden.status_code == 404 and _code(hidden) == "reconciliation_item_not_found"
    assert _items(other) == []
    foreign = _resolve(other, ids[0], resolution="dismiss")
    assert foreign.status_code == 404 and _code(foreign) == "reconciliation_item_not_found"


def test_resolving_entity_items(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"agent": "member"}
    )
    stranger = _owner(client, email_outbox, "resolve-stranger")
    _setup(owner)
    meeting = _conversation(owner, "call-1")
    _record(owner, _payload(customer="C-1", ticket="T-1"))

    changed = _record(owner, _payload(customer="C-2"))["reconciliation_item_ids"][0]
    agent = _record(owner, _payload(agent="u-99"))["reconciliation_item_ids"][0]
    outcome = _record(owner, _payload(outcome="MAYBE"))["reconciliation_item_ids"][0]
    ticket = _record(owner, _payload(ticket="T-2"))["reconciliation_item_ids"][0]

    def refused(item_id: str, **body) -> None:
        response = _resolve(owner, item_id, **body)
        assert response.status_code == 422 and _code(response) == "invalid_resolution", (
            body,
            response.text,
        )

    refused(agent, resolution="keep_current")
    refused(agent, resolution="link")
    refused(agent, resolution="link", user_id=stranger.user_id)
    refused(agent, resolution="link", user_id=str(uuid.uuid4()))
    refused(outcome, resolution="link", outcome="sold")
    refused(outcome, resolution="link")
    refused(changed, resolution="merge")
    refused(changed, resolution="link", user_id=owner.user_id)  # a field that does not belong
    refused(changed, resolution="dismiss", meeting_id=meeting)
    assert _resolve(owner, changed, resolution="link", extra="x").status_code == 422

    kept = _resolve(owner, changed, resolution="keep_current")
    assert kept.status_code == 200, kept.text
    assert (kept.json()["status"], kept.json()["resolution"]) == ("resolved", "keep_current")
    assert kept.json()["resolved_by_user_id"] == owner.user_id
    assert kept.json()["resolved_at"] is not None
    assert _links(owner, meeting)[("odoo", "customer")]["external_id"] == "C-1"
    closed = _resolve(owner, changed, resolution="dismiss")
    assert closed.status_code == 409 and _code(closed) == "reconciliation_item_closed"

    linked_agent = _resolve(owner, agent, resolution="link", user_id=people["agent"].user_id)
    assert linked_agent.status_code == 200, linked_agent.text
    agent_link = _links(owner, meeting)[("odoo", "agent")]
    assert agent_link["agent_user_id"] == people["agent"].user_id
    assert agent_link["external_id"] == "u-99"
    assert agent_link["linked_by_user_id"] == owner.user_id

    linked_outcome = _resolve(owner, outcome, resolution="link", outcome="lost")
    assert linked_outcome.status_code == 200, linked_outcome.text
    outcome_link = _links(owner, meeting)[("odoo", "outcome")]
    assert (outcome_link["outcome"], outcome_link["external_id"]) == ("lost", "MAYBE")

    replaced = _resolve(owner, ticket, resolution="link")
    assert replaced.status_code == 200, replaced.text
    ticket_link = _links(owner, meeting)[("odoo", "ticket")]
    assert ticket_link["external_id"] == "T-2" and ticket_link["linked_by_user_id"] == owner.user_id

    dismissed_id = _record(owner, _payload(customer="C-7"))["reconciliation_item_ids"][0]
    dismissed = _resolve(owner, dismissed_id, resolution="dismiss")
    assert dismissed.status_code == 200
    assert (dismissed.json()["status"], dismissed.json()["resolution"]) == ("dismissed", "dismiss")
    assert _links(owner, meeting)[("odoo", "customer")]["external_id"] == "C-1"
    again = _resolve(owner, dismissed_id, resolution="dismiss")
    assert again.status_code == 409 and _code(again) == "reconciliation_item_closed"

    events = _audits(AuditEventType.RECONCILIATION_ITEM_RESOLVED)
    assert len(events) == 5
    assert {event.target_type for event in events} == {"reconciliation_item"}
    serialized = json.dumps([event.details for event in events])
    for value in ("C-1", "C-2", "T-2", "u-99", "MAYBE"):
        assert value not in serialized, value

    # The inactive record is linked on a human decision and stays inactive.
    inactive_id = _record(owner, _payload("call-1", ticket="T-9", ticket_active=False))[
        "reconciliation_item_ids"
    ][0]
    assert _resolve(owner, inactive_id, resolution="link").status_code == 200
    assert _links(owner, meeting)[("odoo", "ticket")]["external_id"] == "T-9"
    assert _links(owner, meeting)[("odoo", "ticket")]["is_active"] is False


def test_resolving_conversation_items_applies_the_record(client, email_outbox, settings):
    from backend.jobs.crm_reconciliation import reconcile_pending_records

    owner = _owner(client, email_outbox, "resolve-conv")
    stranger = _owner(client, email_outbox, "resolve-conv-other")
    _setup(owner)
    first, second = sorted([_conversation(owner, "call-2"), _conversation(owner, "call-2")])
    ambiguous = _record(owner, _payload("call-2", customer="C-1", outcome="WON"))
    item_id = ambiguous["reconciliation_item_ids"][0]

    outsider = _conversation(owner, "call-other")
    response = _resolve(owner, item_id, resolution="link", meeting_id=outsider)
    assert response.status_code == 422 and _code(response) == "invalid_resolution"
    assert _resolve(owner, item_id, resolution="link").status_code == 422
    chosen = _resolve(owner, item_id, resolution="link", meeting_id=second)
    assert chosen.status_code == 200, chosen.text
    assert set(_links(owner, second)) == {("odoo", "customer"), ("odoo", "outcome")}
    assert _links(owner, first) == {}
    record = {row.id: row for row in _ledger(owner)}[ambiguous["id"]]
    assert record.meeting_id == second and record.state == "applied"

    # A record that never found its conversation, after its window.
    late = _record(owner, _payload("call-404", customer="C-1"))
    counts = reconcile_pending_records(settings, now=datetime.now(UTC) + timedelta(days=8))
    assert counts["reconciliation"] == 1
    (missing,) = _items(owner, reason="conversation_not_found")
    assert missing["inbound_record_id"] == late["id"]
    deleted = _conversation(owner, "call-gone", deleted=True)
    foreign = _conversation(stranger, "call-404")
    for meeting_id in (deleted, foreign, str(uuid.uuid4())):
        refused = _resolve(owner, missing["id"], resolution="link", meeting_id=meeting_id)
        assert refused.status_code == 422 and _code(refused) == "invalid_resolution"
    target = _conversation(owner, "call-elsewhere", system="asterisk")
    applied = _resolve(owner, missing["id"], resolution="link", meeting_id=target)
    assert applied.status_code == 200, applied.text
    assert _links(owner, target)[("odoo", "customer")]["external_id"] == "C-1"
    with inspection_session_factory()() as db:
        conversation = db.get(Meeting, target)
        # Resolving never changes the conversation's external reference.
        assert (conversation.external_source_system, conversation.external_conversation_id) == (
            "asterisk",
            "call-elsewhere",
        )


# --- conversation links --------------------------------------------------------------------


def test_external_links_follow_conversation_visibility(client, email_outbox, monkeypatch):
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"scoped": "member"}
    )
    other = _owner(client, email_outbox, "links-other")
    _setup(owner)
    _setup(owner, "hamkaran")
    visible = _conversation(owner, "call-1")
    _record(owner, _payload("call-1", customer="C-1", ticket="T-1", outcome="WON"))
    _record(owner, _payload("call-1", customer="H-1"), system="hamkaran")
    deleted = _conversation(owner, "call-gone", deleted=True)
    foreign = _conversation(other, "call-1")

    response = owner.get(f"/api/v1/conversations/{visible}/external-links")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [(i["source_system"], i["link_type"]) for i in items] == [
        ("hamkaran", "customer"),
        ("odoo", "customer"),
        ("odoo", "outcome"),
        ("odoo", "ticket"),
    ]
    assert set(items[0]) == {
        "source_system",
        "link_type",
        "external_id",
        "is_active",
        "outcome",
        "agent_user_id",
        "profile_version",
        "linked_at",
        "linked_by_user_id",
    }
    for meeting_id in (deleted, foreign, str(uuid.uuid4())):
        hidden = owner.get(f"/api/v1/conversations/{meeting_id}/external-links")
        assert hidden.status_code == 404, meeting_id
    assert owner.get(f"/api/v1/conversations/{foreign}/external-links").status_code == 404
    assert other.get(f"/api/v1/conversations/{visible}/external-links").status_code == 404

    switched = owner.patch("/api/v1/admin/workspace-settings", json={"record_scope": "team"})
    assert switched.status_code == 200
    scoped = people["scoped"].get(f"/api/v1/conversations/{visible}/external-links")
    assert scoped.status_code == 404  # outside the member's record scope
    assert owner.get(f"/api/v1/conversations/{visible}/external-links").status_code == 200


# --- audit, settings, schedule -------------------------------------------------------------


def test_audit_types_settings_and_schedule():
    from backend.celery_app import CRM_RECONCILIATION_TASK, celery_app
    from backend.config import Settings
    from backend.jobs import tasks
    from backend.observability.heartbeats import PERIODIC_TASKS
    from backend.services.audit_service import AUDIT_EVENT_CATALOG

    assert AuditEventType.INTEGRATION_MAPPING_UPDATED.value == "integration_mapping.updated"
    assert AuditEventType.INTEGRATION_RECORD_CONFLICT.value == "integration_record.conflict"
    assert AuditEventType.RECONCILIATION_ITEM_RESOLVED.value == "reconciliation_item.resolved"
    assert AUDIT_EVENT_CATALOG["integration_mapping.updated"] == "configuration"
    assert AUDIT_EVENT_CATALOG["integration_record.conflict"] == "processing"
    assert AUDIT_EVENT_CATALOG["reconciliation_item.resolved"] == "administration"

    defaults = Settings(environment="test", _env_file=None)
    assert defaults.crm_reconciliation_interval_seconds == 300
    assert defaults.crm_reconciliation_batch_size == 500
    for name in ("crm_reconciliation_interval_seconds", "crm_reconciliation_batch_size"):
        with pytest.raises(ValueError):
            Settings(environment="test", _env_file=None, **{name: 0})

    entry = celery_app.conf.beat_schedule["crm-reconciliation"]
    assert entry["task"] == CRM_RECONCILIATION_TASK == "backend.jobs.reconcile_crm_records"
    assert entry["schedule"] == 300.0
    assert CRM_RECONCILIATION_TASK in celery_app.tasks
    assert callable(tasks.reconcile_crm_records_task)
    assert PERIODIC_TASKS["crm_reconciliation"] == "crm_reconciliation_interval_seconds"


def test_the_openapi_contract_file_lists_no_integration_route():
    from backend.observability.release import PROJECT_ROOT

    contract = (PROJECT_ROOT / "backend/contracts/ingestion-v1.openapi.json").read_text()
    assert "/integrations" not in contract and "external-links" not in contract


@pytest.mark.postgres
def test_records_and_items_stay_in_their_workspace_at_the_database(client, email_outbox):
    """Row-level security backs the API: bound to another workspace, the application
    role reads none of the rows the first workspace's records created."""
    from backend.database.relational import get_session_factory
    from backend.database.tenant_context import bind_tenant_context
    from backend.tests._database import configured_database_url, is_postgres_url

    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")
    owner = _owner(client, email_outbox, "rls-a")
    other = _owner(client, email_outbox, "rls-b")
    _setup(owner)
    _conversation(owner, "call-1")
    _record(owner, _payload(customer="C-1", outcome="WON"))
    _record(owner, _payload(customer="C-2"))
    tables = (
        "integration_mapping_profiles",
        "external_records",
        "integration_inbound_records",
        "conversation_external_links",
        "integration_reconciliation_items",
    )
    for workspace_id, expected_empty in ((other.workspace_id, True), (owner.workspace_id, False)):
        with get_session_factory()() as db:
            bind_tenant_context(db, workspace_id=workspace_id)
            for table in tables:
                count = db.execute(text(f"SELECT count(*) FROM {table}")).scalar()  # noqa: S608
                assert (count == 0) is expected_empty, (workspace_id, table, count)
    assert other.get(PROFILES).json()["items"] == []
    assert _items(other) == []


# --- review turn 1 (SPEC Addendum 3) -------------------------------------------------------


def _all_pages(persona: _Persona, limit: int) -> list[str]:
    """Walk the queue with the tie-safe cursor; returns the item ids in page order."""
    seen: list[str] = []
    params: dict = {"limit": limit}
    while True:
        response = persona.get(RECONCILIATION, params=params)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(item["id"] for item in body["items"])
        assert (body["next_before"] is None) == (body["next_before_id"] is None)
        if body["next_before"] is None:
            return seen
        assert body["next_before_id"] == body["items"][-1]["id"]
        params = {
            "limit": limit,
            "before": body["next_before"],
            "before_id": body["next_before_id"],
        }


def test_the_queue_cursor_is_tie_safe_across_one_record(client, email_outbox):
    """F1: all items of one record share ``created_at``; ``limit=1`` still visits each once."""
    owner = _owner(client, email_outbox, "cursor-record")
    _setup(owner)
    _conversation(owner, "call-1")
    _record(owner, _payload(customer="C-1", ticket="T-1", outcome="WON"))
    record = _record(owner, _payload(customer="C-2", ticket="T-2", agent="u-99", outcome="MAYBE"))
    ids = record["reconciliation_item_ids"]
    assert len(ids) == 4
    assert len({item["created_at"] for item in _items(owner)}) == 1  # one shared time

    pages = _all_pages(owner, limit=1)
    assert sorted(pages) == sorted(ids) and len(pages) == len(set(pages))
    assert pages == [item["id"] for item in _items(owner)]  # the same order as one page

    # ``before`` alone keeps ``created_at < before``; ``before_id`` alone is refused.
    first = owner.get(RECONCILIATION, params={"limit": 1}).json()
    assert owner.get(RECONCILIATION, params={"before": first["next_before"]}).json()["items"] == []
    refused = owner.get(RECONCILIATION, params={"before_id": first["next_before_id"]})
    assert refused.status_code == 422 and _code(refused) == "invalid_cursor"
    last = owner.get(RECONCILIATION, params={"limit": 50}).json()
    assert last["next_before"] is None and last["next_before_id"] is None


def test_the_queue_cursor_is_tie_safe_across_one_sweep_run(client, email_outbox, settings):
    """F1: the items one sweep run opens share ``created_at``; ``limit=2`` visits each once."""
    from backend.jobs.crm_reconciliation import reconcile_pending_records

    owner = _owner(client, email_outbox, "cursor-sweep")
    _setup(owner)
    for call in ("call-1", "call-2", "call-3", "call-4", "call-5"):
        _record(owner, _payload(call, customer="C-1"))
    counts = reconcile_pending_records(settings, now=datetime.now(UTC) + timedelta(days=8))
    assert counts["reconciliation"] == 5
    items = _items(owner)
    assert len(items) == 5 and len({item["created_at"] for item in items}) == 1

    pages = _all_pages(owner, limit=2)
    assert sorted(pages) == sorted(item["id"] for item in items)
    assert len(pages) == len(set(pages)) == 5


def test_a_seen_event_is_settled_before_the_profile_state_and_extraction(client, email_outbox):
    """F3: replay and conflict come after 404 and 413, before ``is_active`` and extraction."""
    owner = _owner(client, email_outbox, "settle-first")
    _setup(owner)
    payload = _payload("call-1", customer="C-1")
    first = _record(owner, payload, event_id="evt-seen")

    assert _put(owner, "odoo", **_profile_body(is_active=False)).status_code == 200
    replay = _post(owner, payload, event_id="evt-seen")
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True and replay.json()["id"] == first["id"]
    unseen = _post(owner, payload, event_id="evt-new")
    assert unseen.status_code == 409 and _code(unseen) == "mapping_profile_inactive"

    assert _put(owner, "odoo", **_profile_body()).status_code == 200
    broken = _post(owner, _payload(call=None), event_id="evt-seen")  # would fail extraction
    assert broken.status_code == 409 and _code(broken) == "inbound_record_conflict"
    (stored,) = _ledger(owner)
    assert stored.conflict_count == 1
    assert len(_audits(AuditEventType.INTEGRATION_RECORD_CONFLICT)) == 1
    assert _post(owner, _payload(call=None), event_id="evt-other").status_code == 422

    # 404 and 413 still come first.
    big = _payload()
    big["blob"] = "x" * 66000
    too_large = _post(owner, big, event_id="evt-seen")
    assert too_large.status_code == 413 and _code(too_large) == "payload_too_large"
    missing = _post(owner, payload, system="zoho", event_id="evt-seen")
    assert missing.status_code == 404 and _code(missing) == "mapping_profile_not_found"
