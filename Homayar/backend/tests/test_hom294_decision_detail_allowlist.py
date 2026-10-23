"""HOM-294: /summary and the share response allow only the known decision
detail shapes.

Synthetic fixtures only.  A structured decision (HOM-61) stores ``owner`` and
``due_date`` as free JSON, so a stored detail can be any shape.  Under
redaction only a dict with a string value went through the view; an extra
key, a number value or an unknown status left the API as stored.  Every
present detail key now leaves in exactly the report's shape (stated plus the
rendered value, not_stated, or unavailable) in every view, an absent key
stays absent, and /report is unchanged.

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


def _decision(description: str, *, owner=NOT_STATED, due_date=NOT_STATED) -> dict:
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


def _seed(meeting_id: str, *decisions) -> None:
    _store_summary(
        meeting_id,
        schema_version=STRUCTURED_SUMMARY_SCHEMA_VERSION,
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


def _report_due_dates(body: dict) -> list[dict]:
    return [decision["due_date"] for decision in body["outcome"]["decisions"]]


# --- AC1, rule 1: every malformed shape is unavailable --------------------------------


MALFORMED = [
    ("number-value", {"status": "stated", "value": 20261103}),
    ("bare-string", "2026-11-03"),
    ("list", ["2026-11-03"]),
    ("null", None),
    ("value-null", {"status": "stated", "value": None}),
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
        f"hom294-malformed-{name}",
        _decision("شکل", owner=detail, due_date=detail),
        _decision("سالم", owner=_stated("تیم فروش"), due_date=_stated("2026-11-03")),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    assert summary["view"] == "redacted"
    deny, allow = summary["decisions"]
    assert deny["description"] == "شکل"
    assert deny["owner"] == deny["due_date"] == UNAVAILABLE
    assert allow["owner"] == _stated("تیم فروش")
    assert allow["due_date"] == _stated("2026-11-03")

    shared = _shared(client, tenant, meeting_id)
    shared_deny, shared_allow = shared["summary"]["decisions"]
    assert shared_deny["owner"] == shared_deny["due_date"] == UNAVAILABLE
    assert shared_allow["owner"] == _stated("تیم فروش")
    assert shared_allow["due_date"] == _stated("2026-11-03")

    report = _report(client, tenant, meeting_id)
    reported_deny, reported_allow = report["outcome"]["decisions"]
    assert reported_deny["owner"] == reported_deny["due_date"] == UNAVAILABLE
    assert reported_allow["owner"] == _stated("تیم فروش")
    assert reported_allow["due_date"] == _stated("2026-11-03")


# --- AC2: an extra key is dropped, its digits never leave ------------------------------


def test_an_extra_key_is_dropped_and_its_phone_never_leaves(
    client, email_outbox, monkeypatch, settings
):
    extra = {"status": "stated", "value": f"تا {PHONE}", "note": PHONE}
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom294-extra-key",
        _decision("پیگیری", due_date=extra),
        _decision("تایید", due_date=_stated(f"تا {PHONE}")),
    )
    shown = _stated("تا [PHONE_2]")
    summary = _summary(client, tenant, meeting_id)
    deny, allow = summary["decisions"]
    assert deny["due_date"] == shown
    assert allow["due_date"] == shown
    assert _report_due_dates(_report(client, tenant, meeting_id)) == [shown, shown]
    body = _dump(summary)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC3: the share link holds the same two inputs -------------------------------------


def test_the_share_link_shows_the_report_shapes_for_the_same_inputs(
    client, email_outbox, monkeypatch, settings
):
    extra = {"status": "stated", "value": f"تا {PHONE}", "note": PHONE}
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom294-share",
        _decision("عدد", owner=_stated(20261103), due_date=_stated(20261103)),
        _decision("پیگیری", due_date=extra),
        _decision("سالم", owner=_stated("تیم فروش"), due_date=_stated("2026-11-03")),
        public_share_links_enabled=True,
    )
    shared = _shared(client, tenant, meeting_id)
    number, extra_point, allow = shared["summary"]["decisions"]
    assert number["owner"] == number["due_date"] == UNAVAILABLE
    assert extra_point["due_date"] == _stated("تا [PHONE_2]")
    assert allow["owner"] == _stated("تیم فروش")
    assert allow["due_date"] == _stated("2026-11-03")
    summary = _summary(client, tenant, meeting_id)
    assert shared["summary"]["decisions"] == summary["decisions"]
    body = _dump(shared)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC4: the allow-control is masked exactly as the report ----------------------------


def test_a_stated_detail_is_shown_masked_exactly_as_the_report(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom294-allow",
        _decision("تماس", owner=_stated(f"مسئول {PHONE}"), due_date=_stated(f"تا {PHONE}")),
        public_share_links_enabled=True,
    )
    report = _report(client, tenant, meeting_id)
    (reported,) = report["outcome"]["decisions"]
    summary = _summary(client, tenant, meeting_id)
    (point,) = summary["decisions"]
    assert point["owner"] == reported["owner"] == _stated("مسئول [PHONE_2]")
    assert point["due_date"] == reported["due_date"] == _stated("تا [PHONE_2]")
    (shared_point,) = _shared(client, tenant, meeting_id)["summary"]["decisions"]
    assert shared_point["owner"] == point["owner"]
    assert shared_point["due_date"] == point["due_date"]
    body = _dump([report, summary])
    assert _no_digits(body, PHONE, PHONE_FA)


# --- AC5: not_stated never carries a value ---------------------------------------------


def test_a_not_stated_detail_drops_its_value_and_extra_keys(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom294-not-stated",
        _decision(
            "پیگیری",
            owner={"status": "not_stated", "value": PHONE, "note": PHONE},
            due_date={"status": "not_stated", "value": "2026-11-03", "extra": True},
        ),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    (point,) = summary["decisions"]
    assert point["owner"] == point["due_date"] == NOT_STATED
    (shared_point,) = _shared(client, tenant, meeting_id)["summary"]["decisions"]
    assert shared_point["owner"] == shared_point["due_date"] == NOT_STATED
    report = _report(client, tenant, meeting_id)
    (reported,) = report["outcome"]["decisions"]
    assert reported["owner"] == reported["due_date"] == NOT_STATED
    body = _dump([summary, shared_point])
    assert _no_digits(body, PHONE, PHONE_FA)


# --- AC6: an unreadable status hides the value, a stated one is masked -----------------


UNREADABLE = [
    ("unknown-status", {"status": "maybe"}),
    ("capitalized-status", {"status": "Stated"}),
    ("absent-status", {}),
]


@pytest.mark.parametrize(
    ("name", "status"),
    [pytest.param(name, status, id=name) for name, status in UNREADABLE],
)
def test_an_unreadable_status_hides_the_value_and_a_stated_one_is_masked(
    client, email_outbox, monkeypatch, settings, name, status
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        f"hom294-status-{name}",
        _decision("نامشخص", owner={**status, "value": PHONE}),
        _decision("مشخص", owner=_stated(PHONE)),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    deny, allow = summary["decisions"]
    assert deny["description"] == "نامشخص"
    assert deny["owner"] == UNAVAILABLE
    assert allow["owner"] == _stated("[PHONE_2]")
    (shared_deny, shared_allow) = _shared(client, tenant, meeting_id)["summary"]["decisions"]
    assert shared_deny["owner"] == UNAVAILABLE
    assert shared_allow["owner"] == _stated("[PHONE_2]")
    report = _report(client, tenant, meeting_id)
    assert [decision["owner"] for decision in report["outcome"]["decisions"]] == [
        UNAVAILABLE,
        _stated("[PHONE_2]"),
    ]
    body = _dump(summary)
    assert _no_digits(body, PHONE, PHONE_FA)


# --- AC7: the original view keeps the allow list ---------------------------------------


def test_the_original_view_keeps_only_the_allowed_shapes(
    client, email_outbox, monkeypatch, settings
):
    extra = {"status": "stated", "value": PHONE, "note": PHONE}
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom294-original",
        _decision("سالم", owner=_stated(f"مسئول {PHONE}"), due_date=_stated("2026-11-03")),
        _decision("اضافه", due_date=extra),
        _decision("شکل", due_date=_stated(20261103)),
    )
    summary = _summary(client, tenant, meeting_id, view="original")
    assert summary["view"] == "original"
    healthy, extra_point, malformed = summary["decisions"]
    assert healthy["owner"] == _stated(f"مسئول {PHONE}")
    assert healthy["due_date"] == _stated("2026-11-03")
    assert extra_point["due_date"] == _stated(PHONE)
    assert malformed["due_date"] == UNAVAILABLE
    report = _report(client, tenant, meeting_id)
    assert _report_due_dates(report) == [
        _stated("2026-11-03"),
        _stated("[PHONE_2]"),
        UNAVAILABLE,
    ]
    assert report["outcome"]["decisions"][0]["owner"] == _stated("مسئول [PHONE_2]")


# --- AC8: an absent key stays absent ---------------------------------------------------


ABSENT = object()
# A present detail that must collapse to unavailable, extra key and all.
MALFORMED = {"status": "stated", "value": 20261103, "note": PHONE}


def _absent_decision(owner, due_date) -> dict:
    decision = _decision("بدون مسئول")
    for key, detail in (("owner", owner), ("due_date", due_date)):
        if detail is ABSENT:
            del decision[key]
        else:
            decision[key] = detail
    return decision


def _assert_detail_key(point: dict, key: str, detail) -> None:
    if detail is ABSENT:
        assert key not in point
    else:
        assert point[key] == UNAVAILABLE


@pytest.mark.parametrize(
    ("name", "owner", "due_date"),
    [
        pytest.param("both-absent", ABSENT, ABSENT, id="both-absent"),
        pytest.param("owner-absent", ABSENT, MALFORMED, id="owner-absent"),
        pytest.param("due-date-absent", MALFORMED, ABSENT, id="due-date-absent"),
    ],
)
def test_an_absent_detail_key_stays_absent(
    client, email_outbox, monkeypatch, settings, name, owner, due_date
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        f"hom294-absent-{name}",
        _absent_decision(owner, due_date),
        public_share_links_enabled=True,
    )
    for body in (
        _summary(client, tenant, meeting_id),
        _summary(client, tenant, meeting_id, view="original"),
    ):
        (point,) = body["decisions"]
        for key, detail in (("owner", owner), ("due_date", due_date)):
            _assert_detail_key(point, key, detail)
    (shared_point,) = _shared(client, tenant, meeting_id)["summary"]["decisions"]
    for key, detail in (("owner", owner), ("due_date", due_date)):
        _assert_detail_key(shared_point, key, detail)
    (reported,) = _report(client, tenant, meeting_id)["outcome"]["decisions"]
    assert reported["owner"] == reported["due_date"] == UNAVAILABLE
