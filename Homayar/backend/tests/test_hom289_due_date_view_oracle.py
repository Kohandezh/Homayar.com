from __future__ import annotations

import base64
import os
import re
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from backend.database.saas_models import MeetingSummary
from backend.services.analysis_claims import (
    LEGACY_SUMMARY_SCHEMA_VERSION,
    SECTION_DECISION,
    STRUCTURED_SUMMARY_SCHEMA_VERSION,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_hom284_partial_pii_oracle import (
    NEUTRAL_SEGMENTS,
    SEGMENTS,
    _add_action,
    _get,
    _process,
    _set_policy,
)
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant
from backend.tests.test_privacy_routing import _GENERATED, _dump, _owner_tenant

PHONE = "9121234567"
OTHER_PHONE = "9351112233"
CALL_ALI = "call Ali at 09121234567 before Friday"
CALL_ALI_SHOWN = "call Ali at [PHONE_1] before Friday"
NOT_STATED = {"status": "not_stated", "value": None}
UNAVAILABLE = {"status": "unavailable", "value": None}
MISSING = object()
LATIN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩０１２３４５６７８９", "0123456789" * 3)
_JOINED = re.compile(r"(?<=\d)[\s\-–+()]+(?=\d)")


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings(settings):
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


def _stated(value):
    return {"status": "stated", "value": value}


def _decision(description="قرارداد سالانه تمدید شد", *, owner=NOT_STATED, due_date=NOT_STATED):
    return {
        "section": SECTION_DECISION,
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


def _store(
    meeting_id,
    decisions,
    *,
    overview="جلسه درباره قرارداد بود.",
    topics=(),
    key_points=None,
    schema_version=STRUCTURED_SUMMARY_SCHEMA_VERSION,
):
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        summary.schema_version = schema_version
        summary.overview = overview
        summary.topics = list(topics)
        summary.key_points = list(decisions if key_points is None else key_points)
        summary.decisions = list(decisions)
        db.commit()


def _meeting(
    client,
    email_outbox,
    monkeypatch,
    settings,
    label,
    decisions,
    *,
    segments=SEGMENTS,
    actions=(),
    redacted=True,
    **summary,
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    meeting_id, _ = _process(client, tenant, settings, label, segments)
    _store(meeting_id, decisions, **summary)
    for description in actions:
        _add_action(client, tenant, meeting_id, description)
    if redacted:
        _set_policy(client, tenant, pii_redaction_enabled=True, public_share_links_enabled=True)
    return tenant, meeting_id


def _report(client, tenant, meeting_id, **params):
    return _get(client, tenant, f"/api/v1/meetings/{meeting_id}/report", **params).json()


def _summary(client, tenant, meeting_id, **params):
    return _get(client, tenant, f"/api/v1/meetings/{meeting_id}/summary", **params).json()


def _shared(client, tenant, meeting_id):
    share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        json={"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
        headers=_headers(tenant),
        cookies=tenant["cookies"],
    )
    assert share.status_code == 201, share.text
    token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]
    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{token}")
    assert shared.status_code == 200, shared.text
    return shared.json()


def _shows(body, digits=PHONE) -> bool:
    visible = _GENERATED.sub(" ", _dump(body).translate(LATIN))
    return digits in _JOINED.sub("", visible)


def _due_dates(decisions):
    return [decision["due_date"] for decision in decisions]


def _owners(decisions):
    return [decision["owner"] for decision in decisions]


def test_the_raw_check_reports_every_spelling_of_the_phone():
    for spelling in (
        "09121234567",
        "۰۹۱۲۱۲۳۴۵۶۷",
        "٠٩١٢١٢٣٤٥٦٧",
        "０９１２１２３４５６７",
        "0912 123 4567",
        "0912-123-4567",
        "+98 912 123 4567",
        "00989121234567",
    ):
        assert _shows({"value": f"تماس {spelling} تا جمعه"}), spelling
    assert not _shows({"value": CALL_ALI_SHOWN, "id": "5ea8c0d0-9240-4faa-0912-1234567ca5d1"})


DUE_PHONES = [
    ("latin-digits", CALL_ALI, CALL_ALI_SHOWN),
    ("persian-digits", "تماس با علی ۰۹۱۲۱۲۳۴۵۶۷ قبل از جمعه", "تماس با علی [PHONE_1] قبل از جمعه"),
    ("arabic-indic-digits", "تماس ٠٩١٢١٢٣٤٥٦٧ تا جمعه", "تماس [PHONE_1] تا جمعه"),
    ("fullwidth-digits", "تماس ０９１２１２３４５６７ تا جمعه", "تماس [PHONE_1] تا جمعه"),
    ("spaced", "تماس 0912 123 4567 تا جمعه", "تماس [PHONE_1] تا جمعه"),
    ("hyphenated", "تماس 0912-123-4567 تا جمعه", "تماس [PHONE_1] تا جمعه"),
    ("plus-98", "تماس +98 912 123 4567 تا جمعه", "تماس [PHONE_1] تا جمعه"),
    ("prefix-0098", "تماس 00989121234567 تا جمعه", "تماس [PHONE_1] تا جمعه"),
]


@pytest.mark.parametrize(
    ("stored", "shown"),
    [pytest.param(stored, shown, id=name) for name, stored, shown in DUE_PHONES],
)
def test_report_masks_a_phone_in_a_due_date(
    client, email_outbox, monkeypatch, settings, stored, shown
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-phone",
        [_decision(due_date=_stated(stored))],
        segments=NEUTRAL_SEGMENTS,
    )
    report = _report(client, tenant, meeting_id)
    assert report["view"] == "redacted"
    (decision,) = report["outcome"]["decisions"]
    assert decision["due_date"] == _stated(shown)
    assert not _shows(report), report["outcome"]


def test_a_member_sees_the_due_date_phone_masked(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    tenant = _owner_tenant(owner)
    meeting_id, _ = _process(client, tenant, settings, "due-member", NEUTRAL_SEGMENTS)
    _store(meeting_id, [_decision(due_date=_stated(CALL_ALI))])
    _set_policy(client, tenant, pii_redaction_enabled=True)
    member = personas["member"]
    base = f"/api/v1/meetings/{meeting_id}"
    assert member.get(f"{base}/report", params={"view": "original"}).status_code == 403
    report = member.get(f"{base}/report")
    summary = member.get(f"{base}/summary")
    assert report.status_code == summary.status_code == 200, (report.text, summary.text)
    assert report.json()["view"] == summary.json()["view"] == "redacted"
    assert _due_dates(report.json()["outcome"]["decisions"]) == [_stated(CALL_ALI_SHOWN)]
    assert _due_dates(summary.json()["decisions"]) == [_stated(CALL_ALI_SHOWN)]
    assert not _shows(report.json()) and not _shows(summary.json())


DUE_FRAGMENTS = [
    (
        "persian-phone-fragment",
        "تا جمعه با ۰۹۱۲ ۳۴۵ تماس بگیرید",
        "تا جمعه با [PHONE_1] تماس بگیرید",
    ),
    (
        "latin-hyphen-fragment",
        "تا جمعه با 0912-345 تماس بگیرید",
        "تا جمعه با [PHONE_1] تماس بگیرید",
    ),
    (
        "last-four-digits",
        "تا جمعه با شماره‌ای که به ۶۷۸۹ ختم می‌شود",
        "تا جمعه با شماره‌ای که به [PHONE_1] ختم می‌شود",
    ),
    ("second-phone-fragment", "تا جمعه با دفتر ۸۸۷۷ ۶۶۵۵", "تا جمعه با دفتر [PHONE_2]"),
    ("email-domain", "تا جمعه از سایت example.com", "تا جمعه از سایت [EMAIL_1]"),
]


@pytest.mark.parametrize(
    ("stored", "shown"),
    [pytest.param(stored, shown, id=name) for name, stored, shown in DUE_FRAGMENTS],
)
def test_report_masks_a_due_date_fragment_like_the_description(
    client, email_outbox, monkeypatch, settings, stored, shown
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-fragment",
        [_decision(stored, due_date=_stated(stored))],
    )
    (decision,) = _report(client, tenant, meeting_id)["outcome"]["decisions"]
    assert decision["text"] == shown
    assert decision["due_date"] == _stated(shown)
    (point,) = _summary(client, tenant, meeting_id)["decisions"]
    assert point["description"] == shown
    assert point["due_date"] == _stated(shown)


@pytest.mark.parametrize(
    "summary",
    [
        pytest.param({"overview": "شماره جدید ۰۹۳۵ ۱۱۱ ۲۲۳۳ است."}, id="known-from-overview"),
        pytest.param({"topics": ["پشتیبانی ۰۹۳۵۱۱۱۲۲۳۳"]}, id="known-from-topic"),
    ],
)
def test_report_masks_a_due_date_fragment_of_a_value_known_from_free_text(
    client, email_outbox, monkeypatch, settings, summary
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-free-fragment",
        [_decision(due_date=_stated("تا جمعه با ۰۹۳۵ ۱۱۱"))],
        segments=NEUTRAL_SEGMENTS,
        **summary,
    )
    report = _report(client, tenant, meeting_id)
    assert _due_dates(report["outcome"]["decisions"]) == [_stated("تا جمعه با [PHONE_1]")]
    assert not _shows(report, "0935111")


DATE_LIKE = [
    ("iso-date", "2026-11-03"),
    ("persian-iso-date", "۲۰۲۶-۱۱-۰۳"),
    ("solar-date-slashes", "۱۴۰۵/۰۸/۱۲"),
    ("date-and-time", "2026-11-03 ساعت 10:30"),
    ("day-month-year-words", "تا ۱۲ آبان ۱۴۰۵ ساعت ۱۰:۳۰"),
]


@pytest.mark.parametrize("value", [pytest.param(value, id=name) for name, value in DATE_LIKE])
def test_a_date_like_due_date_that_matches_no_known_value_is_unchanged(
    client, email_outbox, monkeypatch, settings, value
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-date-like",
        [_decision(due_date=_stated(value))],
    )
    report = _report(client, tenant, meeting_id)
    assert report["view"] == "redacted"
    assert _due_dates(report["outcome"]["decisions"]) == [_stated(value)]
    assert _due_dates(_summary(client, tenant, meeting_id)["decisions"]) == [_stated(value)]


def test_summary_report_and_share_show_the_same_masked_due_date(
    client, email_outbox, monkeypatch, settings
):
    decisions = [
        _decision("تصمیم اول", due_date=_stated("تماس با ۰۹۳۵۱۱۱۲۲۳۳ تا جمعه")),
        _decision("تصمیم دوم", due_date=_stated("تا جمعه با ۰۹۱۲ ۳۴۵ تماس بگیرید")),
        _decision("تصمیم سوم", due_date=_stated("2026-11-03")),
    ]
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, "due-surfaces", decisions
    )
    expected = [
        _stated("تماس با [PHONE_3] تا جمعه"),
        _stated("تا جمعه با [PHONE_1] تماس بگیرید"),
        _stated("2026-11-03"),
    ]
    report = _report(client, tenant, meeting_id)
    summary = _summary(client, tenant, meeting_id)
    assert summary["view"] == "redacted"
    assert _due_dates(report["outcome"]["decisions"]) == expected
    assert _due_dates(summary["decisions"]) == expected
    shared = _shared(client, tenant, meeting_id)
    assert _due_dates(shared["summary"]["decisions"]) == expected
    for body in (report, summary, shared):
        assert not _shows(body, OTHER_PHONE) and not _shows(body, "0912345")


def test_one_value_in_description_owner_and_due_date_gets_one_placeholder(
    client, email_outbox, monkeypatch, settings
):
    decision = _decision(
        "تماس با ۰۹۳۵۱۱۱۲۲۳۳ تأیید شد",
        owner=_stated("پشتیبانی 0935 111 2233"),
        due_date=_stated("تا جمعه با +98 935 111 2233"),
    )
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, "due-one-value", [decision]
    )
    (reported,) = _report(client, tenant, meeting_id)["outcome"]["decisions"]
    assert reported["text"] == "تماس با [PHONE_3] تأیید شد"
    assert reported["owner"] == _stated("پشتیبانی [PHONE_3]")
    assert reported["due_date"] == _stated("تا جمعه با [PHONE_3]")
    (summarized,) = _summary(client, tenant, meeting_id)["decisions"]
    assert summarized["description"] == "تماس با [PHONE_3] تأیید شد"
    assert summarized["owner"] == _stated("پشتیبانی [PHONE_3]")
    assert summarized["due_date"] == _stated("تا جمعه با [PHONE_3]")


def test_a_value_only_in_a_due_date_keeps_one_placeholder_on_every_call(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-numbering",
        [_decision(due_date=_stated("تا جمعه با ۰۹۳۵۱۱۱۲۲۳۳"))],
        overview="پیگیری با ۰۹۳۵ ۱۱۱",
        topics=["تماس با ۰۹۱۹ ۷۷۷ ۰۰۴۴"],
        actions=["تماس با ۰۹۱۹۷۷۷۰۰۴۴"],
    )
    due = [_stated("تا جمعه با [PHONE_3]")]
    for _ in range(2):
        summary = _summary(client, tenant, meeting_id)
        assert summary["overview"] == "پیگیری با [PHONE_3]"
        assert _due_dates(summary["decisions"]) == due
        assert summary["topics"] == ["تماس با [PHONE_4]"]
        report = _report(client, tenant, meeting_id)
        assert report["summary"]["overview"] == "پیگیری با [PHONE_3]"
        assert _due_dates(report["outcome"]["decisions"]) == due
        assert report["summary"]["topics"] == ["تماس با [PHONE_4]"]
        (action,) = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/actions").json()
        assert action["description"] == "تماس با [PHONE_4]"
    shared = _shared(client, tenant, meeting_id)
    assert shared["summary"]["overview"] == "پیگیری با [PHONE_3]"
    assert _due_dates(shared["summary"]["decisions"]) == due


@pytest.mark.parametrize("field", ["owner", "due_date"])
def test_a_fragment_of_a_value_known_only_from_a_decision_detail_is_masked_in_an_action(
    client, email_outbox, monkeypatch, settings, field
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-action-fragment",
        [_decision(**{field: _stated("تماس با ۰۹۳۵۱۱۱۲۲۳۳")})],
        segments=NEUTRAL_SEGMENTS,
        actions=["پیگیری با ۰۹۳۵ ۱۱۱"],
    )
    (action,) = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/actions").json()
    assert action["description"] == "پیگیری با [PHONE_1]"


def test_an_iso_due_date_is_returned_exactly_on_every_surface(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-iso",
        [_decision(owner=_stated("پشتیبانی ۰۹۳۵۱۱۱۲۲۳۳"), due_date=_stated("2026-11-03"))],
    )
    report = _report(client, tenant, meeting_id)
    summary = _summary(client, tenant, meeting_id)
    assert report["view"] == summary["view"] == "redacted"
    assert _due_dates(report["outcome"]["decisions"]) == [_stated("2026-11-03")]
    assert _due_dates(summary["decisions"]) == [_stated("2026-11-03")]
    shared = _shared(client, tenant, meeting_id)
    assert _due_dates(shared["summary"]["decisions"]) == [_stated("2026-11-03")]


@pytest.mark.parametrize(
    ("redacted", "params"),
    [
        pytest.param(True, {"view": "original"}, id="original-requested"),
        pytest.param(False, {}, id="policy-off"),
    ],
)
def test_the_original_view_returns_the_stored_due_date(
    client, email_outbox, monkeypatch, settings, redacted, params
):
    decision = _decision(
        "تماس با ۰۹۳۵۱۱۱۲۲۳۳", owner=_stated("پشتیبانی ۰۹۱۲ ۳۴۵"), due_date=_stated(CALL_ALI)
    )
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-original",
        [decision],
        redacted=redacted,
    )
    report = _report(client, tenant, meeting_id, **params)
    summary = _summary(client, tenant, meeting_id, **params)
    assert report["view"] == summary["view"] == "original"
    (reported,) = report["outcome"]["decisions"]
    assert reported["text"] == "تماس با ۰۹۳۵۱۱۱۲۲۳۳"
    assert reported["owner"] == _stated("پشتیبانی ۰۹۱۲ ۳۴۵")
    assert reported["due_date"] == _stated(CALL_ALI)
    (summarized,) = summary["decisions"]
    assert summarized["owner"] == _stated("پشتیبانی ۰۹۱۲ ۳۴۵")
    assert summarized["due_date"] == _stated(CALL_ALI)


def test_a_not_stated_due_date_stays_not_stated(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, "due-not-stated", [_decision()]
    )
    (reported,) = _report(client, tenant, meeting_id)["outcome"]["decisions"]
    assert reported["owner"] == reported["due_date"] == NOT_STATED
    (summarized,) = _summary(client, tenant, meeting_id)["decisions"]
    assert summarized["owner"] == summarized["due_date"] == NOT_STATED


MALFORMED = [
    ("value-is-a-number", {"status": "stated", "value": 20261103}),
    ("value-is-null", {"status": "stated", "value": None}),
    ("value-is-absent", {"status": "stated"}),
    ("status-is-absent", {"value": "2026-11-03"}),
    ("status-is-unknown", {"status": "maybe", "value": "2026-11-03"}),
    ("detail-is-a-string", "2026-11-03"),
    ("detail-is-a-list", ["2026-11-03"]),
    ("detail-is-null", None),
    ("detail-is-absent", MISSING),
]


@pytest.mark.parametrize("detail", [pytest.param(detail, id=name) for name, detail in MALFORMED])
def test_a_malformed_due_date_stays_unavailable(
    client, email_outbox, monkeypatch, settings, detail
):
    decision = _decision(owner=_stated("تیم فروش"))
    if detail is MISSING:
        del decision["due_date"]
    else:
        decision["due_date"] = detail
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, "due-malformed", [decision]
    )
    (reported,) = _report(client, tenant, meeting_id)["outcome"]["decisions"]
    assert reported["due_date"] == UNAVAILABLE
    assert reported["owner"] == _stated("تیم فروش")
    (summarized,) = _summary(client, tenant, meeting_id)["decisions"]
    assert summarized.get("due_date", MISSING) == (MISSING if detail is MISSING else UNAVAILABLE)


UNREADABLE_STATUSES = [
    ("status-capitalized", {"status": "Stated"}, UNAVAILABLE),
    ("status-unknown", {"status": "maybe"}, UNAVAILABLE),
    ("status-absent", {}, UNAVAILABLE),
    ("not-stated-with-a-value", {"status": "not_stated"}, NOT_STATED),
]


@pytest.mark.parametrize(
    ("status", "reported"),
    [pytest.param(status, reported, id=name) for name, status, reported in UNREADABLE_STATUSES],
)
def test_a_phone_under_an_unreadable_status_is_masked_and_never_stated(
    client, email_outbox, monkeypatch, settings, status, reported
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-unreadable",
        [_decision(due_date={**status, "value": CALL_ALI})],
        segments=NEUTRAL_SEGMENTS,
    )
    report = _report(client, tenant, meeting_id)
    assert _due_dates(report["outcome"]["decisions"]) == [reported]
    assert not _shows(report)
    summary = _summary(client, tenant, meeting_id)
    assert _due_dates(summary["decisions"]) == [reported]
    assert not _shows(summary)


LEGACY = {
    "category": "decision",
    "description": "قرارداد سالانه تمدید شد",
    "confidence": 0.8,
    "owner": _stated("تیم فروش"),
    "due_date": _stated("2026-11-03"),
}


@pytest.mark.parametrize(
    "schema_version",
    [
        pytest.param(LEGACY_SUMMARY_SCHEMA_VERSION, id="legacy-summary"),
        pytest.param(STRUCTURED_SUMMARY_SCHEMA_VERSION, id="insight-labeled-decision"),
    ],
)
def test_a_decision_that_is_not_a_structured_claim_keeps_unavailable_details(
    client, email_outbox, monkeypatch, settings, schema_version
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-legacy",
        [LEGACY],
        schema_version=schema_version,
    )
    (reported,) = _report(client, tenant, meeting_id)["outcome"]["decisions"]
    assert reported["owner"] == reported["due_date"] == UNAVAILABLE
    assert reported["text"] == "قرارداد سالانه تمدید شد"


def test_a_legacy_decision_phone_is_unavailable_on_the_report_and_masked_on_the_summary(
    client, email_outbox, monkeypatch, settings
):
    legacy = {
        **LEGACY,
        "owner": _stated("پشتیبانی ۰۹۳۵۱۱۱۲۲۳۳"),
        "due_date": _stated(CALL_ALI),
    }
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "due-legacy-phone",
        [legacy],
        segments=NEUTRAL_SEGMENTS,
        key_points=[],
        schema_version=LEGACY_SUMMARY_SCHEMA_VERSION,
    )
    report = _report(client, tenant, meeting_id)
    (reported,) = report["outcome"]["decisions"]
    assert reported["owner"] == reported["due_date"] == UNAVAILABLE
    assert not _shows(report) and not _shows(report, OTHER_PHONE)
    summary = _summary(client, tenant, meeting_id)
    (summarized,) = summary["decisions"]
    assert summarized["owner"] == _stated("پشتیبانی [PHONE_1]")
    assert summarized["due_date"] == _stated("call Ali at [PHONE_2] before Friday")
    assert not _shows(summary) and not _shows(summary, OTHER_PHONE)


def test_owner_rendering_is_unchanged(client, email_outbox, monkeypatch, settings):
    decisions = [
        _decision("تصمیم اول", owner=_stated("پشتیبانی ۰۹۳۵۱۱۱۲۲۳۳")),
        _decision("تصمیم دوم", owner=_stated("با ۰۹۱۲ ۳۴۵")),
        _decision("تصمیم سوم", owner=_stated("تیم فروش")),
        _decision("تصمیم چهارم"),
    ]
    tenant, meeting_id = _meeting(
        client, email_outbox, monkeypatch, settings, "due-owner", decisions
    )
    shown = [
        _stated("پشتیبانی [PHONE_3]"),
        _stated("با [PHONE_1]"),
        _stated("تیم فروش"),
        NOT_STATED,
    ]
    assert _owners(_report(client, tenant, meeting_id)["outcome"]["decisions"]) == shown
    assert _owners(_summary(client, tenant, meeting_id)["decisions"]) == shown
    original = _summary(client, tenant, meeting_id, view="original")["decisions"]
    assert _owners(original) == [decision["owner"] for decision in decisions]
