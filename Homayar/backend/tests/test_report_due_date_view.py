"""HOM-289: a structured decision's due date is free text in the redacted view.

Synthetic fixtures only.  A structured decision (HOM-61) stores ``due_date``
as ``{status, value}`` in a free JSON column, so a stated value can hold any
text.  Under the redacted view it goes through the view like ``owner.value``:
a whole PII value or a fragment of a known one shows a placeholder, and a
plain date stays as stored.  The original view, the status shapes and the
numbering of the other free texts stay exact.

The transcript (``test_privacy_routing.SEGMENTS``) holds the known values
``[IR_NATIONAL_ID_1]``, ``[PHONE_1]`` (``09123456789``) and ``[EMAIL_1]``.
"""

from __future__ import annotations

import base64
import os
from urllib.parse import urlparse

import pytest

from backend.services.analysis_claims import STRUCTURED_SUMMARY_SCHEMA_VERSION
from backend.tests.test_free_text_partial_pii import (
    FRAGMENTS,
    SHOWN,
    _post,
    _redacted_meeting,
    _store_summary,
)
from backend.tests.test_privacy_routing import _dump, _get, _no_raw

NOT_STATED = {"status": "not_stated", "value": None}
# Phones that are not in the transcript, and no four digits of which are.
PHONE = "09198880770"
PHONE_FA = "۰۹۱۹۸۸۸۰۷۷۰"
SHARED = "09351112233"
ACTION = "09367654321"


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings(settings):
    # One keyed configuration for the API and the processor (mappings retained).
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


def _stated(value) -> dict:
    return {"status": "stated", "value": value}


def _decision(description: str, *, owner: dict = NOT_STATED, due_date: dict = NOT_STATED):
    """A decision as ``analysis_claims`` stores it."""
    return {
        "section": "decision",
        "category": "decision",
        "description": description,
        "basis": "explicit",
        "owner": owner,
        "due_date": due_date,
        "confidence": None,
        "source_turn_indexes": [],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }


def _seed(meeting_id: str, *decisions, schema_version=STRUCTURED_SUMMARY_SCHEMA_VERSION):
    _store_summary(
        meeting_id,
        schema_version=schema_version,
        overview="خلاصه",
        topics=[],
        key_points=list(decisions),
        decisions=list(decisions),
    )


def _meeting(client, email_outbox, monkeypatch, settings, label, *decisions, **policy):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, label, **policy
    )
    _seed(meeting_id, *decisions)
    return tenant, meeting_id


def _report(client, tenant, meeting_id, **params) -> dict:
    response = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/report", **params)
    assert response.status_code == 200, response.text
    return response.json()


def _summary(client, tenant, meeting_id, **params) -> dict:
    response = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/summary", **params)
    assert response.status_code == 200, response.text
    return response.json()


def _report_due_dates(body: dict) -> list[dict]:
    return [decision["due_date"] for decision in body["outcome"]["decisions"]]


def _summary_due_dates(body: dict) -> list[dict]:
    return [decision["due_date"] for decision in body["decisions"]]


def _no_digits(text: str, *values: str) -> bool:
    return not any(value in text for value in values)


# --- AC1: a whole value -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "phone"), [("due-latin", PHONE), ("due-persian", PHONE_FA)], ids=["latin", "persian"]
)
def test_report_shows_a_placeholder_for_a_phone_in_a_due_date(
    client, email_outbox, monkeypatch, settings, label, phone
):
    stored = f"call Ali at {phone} before Friday"
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        label,
        _decision("پیگیری", due_date=_stated(stored)),
    )
    report = _report(client, tenant, meeting_id)
    assert report["view"] == "redacted"
    assert _report_due_dates(report) == [_stated("call Ali at [PHONE_2] before Friday")]
    body = _dump(report)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC2: a fragment of a known value ---------------------------------------------------


def test_a_fragment_in_a_due_date_is_masked_like_the_description(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-fragment",
        _decision(FRAGMENTS, due_date=_stated(FRAGMENTS)),
    )
    report = _report(client, tenant, meeting_id)
    (decision,) = report["outcome"]["decisions"]
    assert decision["text"] == SHOWN
    assert decision["due_date"] == _stated(SHOWN)
    assert _no_raw(_dump(report))
    summary = _summary(client, tenant, meeting_id)
    (decision,) = summary["decisions"]
    assert decision["description"] == SHOWN
    assert decision["due_date"] == _stated(SHOWN)
    assert _no_raw(_dump(summary))


# --- AC3: /summary and the share view ---------------------------------------------------


def test_summary_and_share_show_the_due_date_the_report_shows(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-share",
        _decision("پیگیری", due_date=_stated(f"تا {PHONE_FA} تماس بگیرید")),
        public_share_links_enabled=True,
    )
    shown = [_stated("تا [PHONE_2] تماس بگیرید")]
    assert _report_due_dates(_report(client, tenant, meeting_id)) == shown
    summary = _summary(client, tenant, meeting_id)
    assert summary["view"] == "redacted"
    assert _summary_due_dates(summary) == shown
    assert _no_raw(_dump(summary))

    share = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/shares",
        {"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
    )
    assert share.status_code == 201, share.text
    token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]
    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{token}")
    assert shared.status_code == 200, shared.text
    assert _summary_due_dates(shared.json()["summary"]) == shown
    assert _no_raw(_dump(shared.json()))


# --- AC4: one placeholder per value -----------------------------------------------------


def test_a_value_keeps_one_placeholder_across_fields_endpoints_and_calls(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-numbering",
        _decision(
            f"تماس با {SHARED}",
            owner=_stated(f"مسئول {SHARED}"),
            due_date=_stated(f"تا {SHARED}"),
        ),
        _decision("پیگیری", due_date=_stated(f"قبل از تماس با {PHONE}")),
    )
    created = _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/actions",
        {"description": f"شماره {ACTION}", "priority": "high"},
    )
    assert created.status_code == 201, created.text

    # The due date is numbered in the canonical order, before the action items.
    due_dates = [_stated("تا [PHONE_2]"), _stated("قبل از تماس با [PHONE_3]")]
    for _ in range(2):
        report = _report(client, tenant, meeting_id)
        first = report["outcome"]["decisions"][0]
        assert first["text"] == "تماس با [PHONE_2]"
        assert first["owner"] == _stated("مسئول [PHONE_2]")
        assert _report_due_dates(report) == due_dates

        summary = _summary(client, tenant, meeting_id)
        first = summary["decisions"][0]
        assert first["description"] == "تماس با [PHONE_2]"
        assert first["owner"] == _stated("مسئول [PHONE_2]")
        assert _summary_due_dates(summary) == due_dates

        actions = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/actions").json()
        assert [action["description"] for action in actions] == ["شماره [PHONE_4]"]
        body = _dump([report, summary, actions])
        assert _no_digits(body, SHARED, PHONE, ACTION)


# --- AC5, AC6: allow-controls -----------------------------------------------------------


@pytest.mark.parametrize("value", ["2026-11-03", "تا ۱۴۰۵/۰۸/۱۲"], ids=["iso", "jalali"])
def test_a_date_stays_as_stored_in_the_redacted_view(
    client, email_outbox, monkeypatch, settings, value
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        f"due-date-{len(value)}",
        _decision("پیگیری", due_date=_stated(value)),
    )
    assert _report_due_dates(_report(client, tenant, meeting_id)) == [_stated(value)]
    summary = _summary(client, tenant, meeting_id)
    assert summary["view"] == "redacted"
    assert _summary_due_dates(summary) == [_stated(value)]


def test_the_original_view_returns_the_due_date_unchanged(
    client, email_outbox, monkeypatch, settings
):
    stored = f"call Ali at {PHONE} before {FRAGMENTS}"
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-original",
        _decision("پیگیری", due_date=_stated(stored)),
    )
    report = _report(client, tenant, meeting_id, view="original")
    assert report["view"] == "original"
    assert _report_due_dates(report) == [_stated(stored)]
    summary = _summary(client, tenant, meeting_id, view="original")
    assert summary["view"] == "original"
    assert _summary_due_dates(summary) == [_stated(stored)]


# --- AC7: status shapes -----------------------------------------------------------------


def test_status_shapes_are_unchanged(client, email_outbox, monkeypatch, settings):
    malformed = {"status": "maybe", "value": f"تا {PHONE}"}
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-shapes",
        _decision("پیگیری"),
        _decision("پیگیری", due_date=_stated(42)),
        _decision("پیگیری", due_date=malformed),
        _decision("پیگیری", owner=_stated(f"مسئول {PHONE}")),
    )
    unavailable = {"status": "unavailable", "value": None}
    report = _report(client, tenant, meeting_id)
    assert _report_due_dates(report) == [NOT_STATED, unavailable, unavailable, NOT_STATED]
    assert report["outcome"]["decisions"][3]["owner"] == _stated("مسئول [PHONE_2]")
    assert _no_digits(_dump(report), PHONE)
    summary = _summary(client, tenant, meeting_id)
    assert _summary_due_dates(summary) == [
        NOT_STATED,
        unavailable,
        unavailable,
        NOT_STATED,
    ]
    assert summary["decisions"][3]["owner"] == _stated("مسئول [PHONE_2]")
    assert _no_digits(_dump(summary), PHONE)


def test_a_legacy_decision_stays_unavailable_and_never_shows_the_value(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "due-legacy"
    )
    _seed(meeting_id, _decision("پیگیری", due_date=_stated(f"تا {PHONE}")), schema_version=1)
    report = _report(client, tenant, meeting_id)
    (decision,) = report["outcome"]["decisions"]
    assert decision["due_date"] == decision["owner"] == {"status": "unavailable", "value": None}
    assert _no_digits(_dump(report), PHONE)
    summary = _summary(client, tenant, meeting_id)
    assert _summary_due_dates(summary) == [_stated("تا [PHONE_2]")]
    assert _no_digits(_dump(summary), PHONE)
