"""HOM-304: the insight key points of /summary and the share view allow
only the known keys.

Synthetic fixtures only.  A stored ``MeetingSummary.key_points`` entry
without ``section`` is an insight.  The processor writes six keys, but the
column is free JSON, so a hand-written or older row can hold anything.  The
redacted view masked only ``category`` and ``description``; every other key
left the API exactly as stored, so a decision-shaped dict leaked its
``owner``, ``due_date`` and extra keys on /summary and the share link.
Every insight now leaves as a new dict with only the allowed keys, in every
view, and /report is unchanged.

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
    _post,
    _redacted_meeting,
    _store_summary,
)
from backend.tests.test_privacy_routing import _dump, _get, _no_raw

NOT_STATED = {"status": "not_stated", "value": None}
UNAVAILABLE = {"status": "unavailable", "value": None}
# Phones that are not in the transcript, and no four digits of which are.
PHONE = "09198880770"
PHONE_FA = "۰۹۱۹۸۸۸۰۷۷۰"


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


def _insight(description, **extra) -> dict:
    """An insight key point as ``processor`` writes it, plus any extra keys
    the caller plants on it."""
    point = {
        "category": "نکته",
        "description": description,
        "confidence": 0.5,
        "source_turn_indexes": [0],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }
    point.update(extra)
    return point


def _seed(meeting_id: str, *insights) -> None:
    _store_summary(
        meeting_id,
        schema_version=STRUCTURED_SUMMARY_SCHEMA_VERSION,
        overview="خلاصه",
        topics=[],
        key_points=list(insights),
        decisions=[],
    )


def _meeting(client, email_outbox, monkeypatch, settings, label, *insights, **policy):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, label, **policy
    )
    _seed(meeting_id, *insights)
    return tenant, meeting_id


def _summary(client, tenant, meeting_id, **params) -> dict:
    response = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/summary", **params)
    assert response.status_code == 200, response.text
    return response.json()


def _report(client, tenant, meeting_id, **params) -> dict:
    response = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/report", **params)
    assert response.status_code == 200, response.text
    return response.json()


def _shared(client, tenant, meeting_id) -> dict:
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
    return shared.json()


def _no_digits(text: str, *values: str) -> bool:
    return not any(value in text for value in values)


# --- AC1: an extra key and stated details leave only in their allowed shape --------------


def test_extra_keys_and_details_leave_only_in_their_allowed_shape(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom304-extra",
        _insight("تماس", owner=_stated(PHONE), due_date=_stated(f"تا {PHONE}"), note=PHONE),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    assert summary["view"] == "redacted"
    (point,) = summary["key_points"]
    assert "note" not in point
    assert point["owner"] == _stated("[PHONE_2]")
    assert point["due_date"] == _stated("تا [PHONE_2]")
    body = _dump(summary)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC2: the share link holds the same key points ---------------------------------------


def test_the_share_link_shows_the_same_key_points_as_the_summary(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom304-share",
        _insight("تماس", owner=_stated(PHONE), due_date=_stated(f"تا {PHONE}"), note=PHONE),
        public_share_links_enabled=True,
    )
    shared = _shared(client, tenant, meeting_id)
    (point,) = shared["summary"]["key_points"]
    assert "note" not in point
    assert point["owner"] == _stated("[PHONE_2]")
    assert point["due_date"] == _stated("تا [PHONE_2]")
    summary = _summary(client, tenant, meeting_id)
    assert shared["summary"]["key_points"] == summary["key_points"]
    body = _dump(shared)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC3: the allow-control keeps the six processor keys ---------------------------------


def test_a_processor_insight_keeps_its_six_keys_masked(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom304-allow",
        _insight(f"تماس با {PHONE}"),
        _insight("اضافه", note=PHONE, owner=_stated(PHONE), due_date=_stated(f"تا {PHONE}")),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    normal, deny = summary["key_points"]
    assert normal == {
        "category": "نکته",
        "description": "تماس با [PHONE_2]",
        "confidence": 0.5,
        "source_turn_indexes": [0],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }
    assert "note" not in deny
    shared = _shared(client, tenant, meeting_id)
    (shared_normal, shared_deny) = shared["summary"]["key_points"]
    assert shared_normal == normal
    assert "note" not in shared_deny
    assert shared["summary"]["key_points"] == summary["key_points"]
    body = _dump(shared)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC4: every malformed detail is unavailable, a proper one is kept ---------------------


MALFORMED = [
    ("unknown-status", {"status": "maybe", "value": PHONE}),
    ("number-value", {"status": "stated", "value": 20261103}),
    ("bare-string", f"تا {PHONE}"),
    ("list", [{"value": PHONE}]),
    ("null", None),
    ("value-absent", {"status": "stated"}),
]


@pytest.mark.parametrize(
    ("name", "detail"),
    [pytest.param(name, detail, id=name) for name, detail in MALFORMED],
)
def test_a_malformed_detail_is_unavailable_and_a_proper_one_is_kept(
    client, email_outbox, monkeypatch, settings, name, detail
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        f"hom304-malformed-{name}",
        _insight("شکل", owner=detail, due_date=detail),
        _insight("سالم", owner=_stated("تیم فروش"), due_date=_stated("2026-11-03")),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    deny, allow = summary["key_points"]
    assert deny["description"] == "شکل"
    assert deny["owner"] == deny["due_date"] == UNAVAILABLE
    assert allow["owner"] == _stated("تیم فروش")
    assert allow["due_date"] == _stated("2026-11-03")

    shared = _shared(client, tenant, meeting_id)
    shared_deny, shared_allow = shared["summary"]["key_points"]
    assert shared_deny["owner"] == shared_deny["due_date"] == UNAVAILABLE
    assert shared_allow["owner"] == _stated("تیم فروش")
    assert shared_allow["due_date"] == _stated("2026-11-03")


# --- AC4: not_stated never carries a value -----------------------------------------------


def test_a_not_stated_detail_drops_its_value_and_extra_keys(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom304-not-stated",
        _insight(
            "پیگیری",
            owner={"status": "not_stated", "value": PHONE, "note": PHONE},
            due_date={"status": "not_stated", "value": "2026-11-03", "extra": True},
        ),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    (point,) = summary["key_points"]
    assert point["owner"] == point["due_date"] == NOT_STATED
    (shared_point,) = _shared(client, tenant, meeting_id)["summary"]["key_points"]
    assert shared_point["owner"] == shared_point["due_date"] == NOT_STATED
    body = _dump([summary, shared_point])
    assert _no_digits(body, PHONE, PHONE_FA)


# --- AC4: a non-string category or description is dropped --------------------------------


def test_non_string_texts_are_dropped_and_a_string_one_is_masked(
    client, email_outbox, monkeypatch, settings
):
    description_point = _insight({"value": PHONE}, note=PHONE)
    category_point = _insight("دسته")
    category_point["category"] = [PHONE]
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom304-shape",
        description_point,
        category_point,
        _insight(f"تماس با {PHONE}"),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    deny_description, deny_category, allow = summary["key_points"]
    assert "description" not in deny_description
    assert "note" not in deny_description
    assert "category" not in deny_category
    assert deny_category["description"] == "دسته"
    assert allow["description"] == "تماس با [PHONE_2]"
    shared = _shared(client, tenant, meeting_id)
    assert shared["summary"]["key_points"] == summary["key_points"]
    body = _dump(summary)
    assert _no_digits(body, PHONE, PHONE_FA)


# --- AC5: the original view keeps the allow list, not the raw row -------------------------


def test_the_original_view_keeps_only_the_allowed_keys_as_stored(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom304-original",
        _insight(
            f"تماس با {PHONE}",
            owner=_stated(f"مسئول {PHONE}"),
            due_date=_stated("2026-11-03"),
            note=PHONE,
        ),
        _insight("بدون جزئیات"),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id, view="original")
    assert summary["view"] == "original"
    point, plain = summary["key_points"]
    assert point == {
        "category": "نکته",
        "description": f"تماس با {PHONE}",
        "confidence": 0.5,
        "source_turn_indexes": [0],
        "evidence_ids": [],
        "evidence_status": "insufficient",
        "owner": _stated(f"مسئول {PHONE}"),
        "due_date": _stated("2026-11-03"),
    }
    assert "owner" not in plain
    assert "due_date" not in plain
    assert plain["description"] == "بدون جزئیات"


# --- AC6: /report keeps its own explicit key point shapes --------------------------------


def test_the_report_keeps_its_own_key_point_shapes(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom304-report",
        _insight(
            f"تماس با {PHONE}",
            owner=_stated(f"مسئول {PHONE}"),
            due_date=_stated(f"تا {PHONE}"),
            note=PHONE,
        ),
        public_share_links_enabled=True,
    )
    report = _report(client, tenant, meeting_id)
    (reported,) = report["summary"]["key_points"]
    assert reported == {
        "index": 0,
        "category": "نکته",
        "text": "تماس با [PHONE_2]",
        "confidence": 0.5,
        "evidence_status": "insufficient",
        "supported": False,
        "evidence": [],
    }
    summary = _summary(client, tenant, meeting_id)
    (point,) = summary["key_points"]
    assert point["owner"] == _stated("مسئول [PHONE_2]")
    body = _dump(report)
    assert _no_digits(body, PHONE, PHONE_FA)
