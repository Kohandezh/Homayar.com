"""HOM-284: a redacted view never shows part of a PII value in free text.

Synthetic fixtures only.  The detector replaces whole values, so a summary or
an action item written before the policy was turned on can still quote part of
one (``۰۹۱۲ ۳۴۵`` of the phone ``۰۹۱۲ ۳۴۵ ۶۷۸۹``, ``example.com`` of
``ali.rezaei@example.com``).  Under the redacted view such a fragment shows the
placeholder of the value it belongs to, as ``MeetingView.span`` widens a
partial citation.  The original view, the numbering and the text a person
writes back stay exact.

The transcript (``test_privacy_routing.SEGMENTS``) holds the known values
``[IR_NATIONAL_ID_1]`` (``0012345679``), ``[PHONE_1]`` (``09123456789``) and
``[EMAIL_1]`` (``ali.rezaei@example.com``).
"""

from __future__ import annotations

import base64
import itertools
import os
import re
from contextlib import contextmanager
from datetime import UTC, datetime
from urllib.parse import urlparse

import pytest
from sqlalchemy import select

from backend.database.relational import get_session_factory
from backend.database.saas_models import ActionItem, Meeting, MeetingSummary, Workspace
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.processor import process_job
from backend.privacy import text_pii, transcript_view
from backend.privacy.transcript_view import MeetingView, choose_view
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider
from backend.tests._database import inspection_session_factory
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_privacy_routing import (
    _CapturingIntelligence,
    _dump,
    _get,
    _no_raw,
    _owner_tenant,
    _process,
    _set_policy,
)
from backend.tests.test_text_pii import card, iban

# Every free text below quotes one fragment of each kind: Persian digits,
# Latin digits, and an e-mail address.
FRAGMENTS = "۰۹۱۲ ۳۴۵ یا 0912-345 و example.com"
SHOWN = "[PHONE_1] یا [PHONE_1] و [EMAIL_1]"
# Share no four consecutive digits with each other or with the transcript values.
CARD = card("603799887766554")
IBAN = iban("0570028180010653120701")


def _field(label: str) -> str:
    return f"{label}: {FRAGMENTS}"


def _shown(label: str) -> str:
    return f"{label}: {SHOWN}"


def _key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


@pytest.fixture
def settings(settings):
    # One keyed configuration for the API and the processor (mappings retained).
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


def _redacted_meeting(client, email_outbox, monkeypatch, settings, label: str, **policy):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    assert _set_policy(client, tenant, pii_redaction_enabled=True, **policy).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, label)
    return tenant, meeting_id


def _store_summary(meeting_id: str, **fields) -> None:
    """Free texts as a model wrote them before the policy: raw values."""
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        for name, value in fields.items():
            setattr(summary, name, value)
        db.commit()


def _post(client, tenant: dict, path: str, payload: dict):
    return client.post(path, json=payload, headers=_headers(tenant), cookies=tenant["cookies"])


def _patch(client, tenant: dict, path: str, payload: dict):
    return client.patch(path, json=payload, headers=_headers(tenant), cookies=tenant["cookies"])


@contextmanager
def _view(tenant: dict, meeting_id: str, settings, *, requested: str = "redacted"):
    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=tenant["workspace_id"])
        workspace = db.get(Workspace, tenant["workspace_id"])
        yield MeetingView(
            db,
            meeting=db.get(Meeting, meeting_id),
            policy=choose_view(workspace, requested=requested, may_read_original=True),
            settings=settings,
            now=datetime.now(UTC),
            persist=False,
        )


# --- every field, through the HTTP API (T2) -------------------------------------------


def test_every_free_text_field_shows_placeholders_for_fragments(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-http", public_share_links_enabled=True
    )
    base = f"/api/v1/meetings/{meeting_id}"
    _store_summary(
        meeting_id,
        overview=_field("خلاصه"),
        topics=[_field("موضوع")],
        key_points=[{"category": _field("دسته"), "description": _field("نکته")}],
        decisions=[
            {
                "description": _field("تصمیم"),
                "owner": {"status": "stated", "value": _field("مسئول")},
            }
        ],
    )
    created = _post(
        client,
        tenant,
        f"{base}/actions",
        {"description": _field("اقدام"), "assignee_text": _field("پیگیری"), "priority": "high"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["description"] == _shown("اقدام")

    summary = _get(client, tenant, f"{base}/summary").json()
    assert summary["view"] == "redacted"
    assert summary["overview"] == _shown("خلاصه")
    assert summary["topics"] == [_shown("موضوع")]
    (point,) = summary["key_points"]
    assert point["category"] == _shown("دسته") and point["description"] == _shown("نکته")
    (decision,) = summary["decisions"]
    assert decision["description"] == _shown("تصمیم")
    assert decision["owner"] == {"status": "stated", "value": _shown("مسئول")}
    assert _no_raw(_dump(summary))

    report = _get(client, tenant, f"{base}/report")
    assert report.status_code == 200, report.text
    rendered = report.json()["summary"]
    assert rendered["overview"] == _shown("خلاصه") and rendered["topics"] == [_shown("موضوع")]
    (point,) = rendered["key_points"]
    assert point["category"] == _shown("دسته") and point["text"] == _shown("نکته")
    assert _no_raw(_dump(report.json()))

    (action,) = _get(client, tenant, f"{base}/actions").json()
    assert action["description"] == _shown("اقدام")
    assert action["assignee_text"] == _shown("پیگیری")
    assert _no_raw(_dump(action))
    exported = _get(client, tenant, f"{base}/export", format="json")
    assert exported.status_code == 200 and _no_raw(exported.content.decode())
    assert exported.json()["summary"] == _shown("خلاصه")

    # The transcript keeps the whole value's placeholder (N2, N6).
    transcript = _get(client, tenant, f"{base}/transcript").json()
    assert "[PHONE_1]" in transcript["segments"][0]["text"]

    share = _post(
        client,
        tenant,
        f"{base}/shares",
        {"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
    )
    assert share.status_code == 201, share.text
    token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]
    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{token}")
    assert shared.status_code == 200, shared.text
    body = shared.json()
    assert body["summary"]["overview"] == _shown("خلاصه")
    assert body["summary"]["topics"] == [_shown("موضوع")]
    assert body["actions"][0]["description"] == _shown("اقدام")
    assert _no_raw(_dump(body))
    for fmt in ("json", "txt"):
        shared_export = client.get(f"/api/v1/shares/{token}/export", params={"format": fmt})
        assert shared_export.status_code == 200, shared_export.text
        assert _no_raw(shared_export.content.decode()), fmt
        assert _shown("خلاصه") in shared_export.content.decode(), fmt


# --- the fragment rule, through MeetingView -------------------------------------------

DENIED = [
    # R1: digit runs that share four consecutive digits with a known value.
    ("با ۰۹۱۲ ۳۴۵ تماس بگیرید، داخلی ۳۴۵", "با [PHONE_1] تماس بگیرید، داخلی ۳۴۵"),
    ("شماره 0912-345 در سال ۱۴۰۳", "شماره [PHONE_1] در سال ۱۴۰۳"),
    ("0912/345", "[PHONE_1]/345"),
    ("0912  345", "[PHONE_1]  345"),
    ("+98 912 345 و 912", "[PHONE_1] و 912"),
    ("0098 912 345", "[PHONE_1]"),
    ("+98 912", "[PHONE_1]"),
    ("(0912) 345 را بگیر", "[PHONE_1] را بگیر"),
    ("3456789", "[PHONE_1]"),
    # R5: every digit script, mixed scripts, and Persian letters right before.
    ("شماره۰۹۱۲۳۴۵", "شماره[PHONE_1]"),
    ("٠٩١٢ ٣٤٥", "[PHONE_1]"),
    ("０９１２ ３４５", "[PHONE_1]"),
    ("۰۹۱2-٣٤5", "[PHONE_1]"),
    # R3: the value with the longest common digit run wins (national ID here).
    ("کد ملی ۰۰۱۲۳۴۵", "کد ملی [IR_NATIONAL_ID_1]"),
    # R2: fragments of the known e-mail address.
    ("سایت example.com، نه com و نه meeting.notes", "سایت [EMAIL_1]، نه com و نه meeting.notes"),
    ("مسئول ali.rezaei است، نه ali", "مسئول [EMAIL_1] است، نه ali"),
    ("به ali.rezaei@example بنویس", "به [EMAIL_1] بنویس"),
    ("دامنه EXAMPLE.COM.", "دامنه [EMAIL_1]."),
    ("شرکت example و rez", "شرکت [EMAIL_1] و rez"),
    # N1: whole values keep their placeholder next to a fragment.
    ("تکرار ۰۹۱۲۳۴۵۶۷۸۹ و ۰۹۱۲ ۳۴۵", "تکرار [PHONE_1] و [PHONE_1]"),
    ("[PHONE_1] و ۰۹۱۲ ۳۴۵", "[PHONE_1] و [PHONE_1]"),
]

ALLOWED = [
    "داخلی ۳۴۵",
    "اتاق 12 و ۹۱۲",
    "سال ۱۴۰۳ و 2020",
    "091 و ۷۷۷۷",
    "com, ali, meeting, report, meeting.notes, rez",
    "ساعت ۱۰:۳۰ تا ۱۲:۴۵",
    "",
]


def test_fragments_of_known_values_show_the_whole_placeholder(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-rules"
    )
    with _view(tenant, meeting_id, settings) as view:
        rendered = {text: view.text(text) for text, _ in DENIED}
        assert rendered == dict(DENIED)
        # point() and texts() render through text().
        point = {"category": "0912-345", "description": "example.com", "confidence": 0.5}
        assert view.point(point) == {
            "category": "[PHONE_1]",
            "description": "[EMAIL_1]",
            "confidence": 0.5,
        }
        assert view.texts(["۰۹۱۲ ۳۴۵", "ali.rezaei"]) == ["[PHONE_1]", "[EMAIL_1]"]


# SPEC 11, R2: a token that contains a fragment or the whole address is masked whole.
CONTAINED = [
    ("سایت www.example.com", "سایت [EMAIL_1]"),
    ("از mail-ali.rezaei", "از [EMAIL_1]"),
    ("کاربر ali.rezaei2 و ali.rezaeix", "کاربر [EMAIL_1] و [EMAIL_1]"),
    ("نشانی ali.rezaei@example.com2 است", "نشانی [EMAIL_1] است"),
    ("نشانی ali.rezaei@example.com-x است", "نشانی [EMAIL_1] است"),
    ("شرکت example2 و x-example", "شرکت [EMAIL_1] و [EMAIL_1]"),
    # A whole label needs a boundary on each side (case b).
    ("examples و counterexample و com و ali", "examples و counterexample و com و ali"),
    # SPEC 11.1, case (a): a part with "." must cover a whole label (ali, rezaei, example).
    ("نام ali.r و example.org", "نام [EMAIL_1] و [EMAIL_1]"),
    (
        "google.com و e.com و i.re و x.com و the.cost و wiki.read و file.cfg",
        "google.com و e.com و i.re و x.com و the.cost و wiki.read و file.cfg",
    ),
]
# SPEC 11.3, R2 (b): a label of the local part with four or more characters,
# with a boundary on each side.
LOCAL_LABELS = [
    ("مسئول rezaei است", "مسئول [EMAIL_1] است"),
    ("REZAEI و rezaei2 و x-rezaei", "[EMAIL_1] و [EMAIL_1] و [EMAIL_1]"),
    ("ali و rezaeian و alireza", "ali و rezaeian و alireza"),
]
# SPEC 11, R6: only the placeholders the view uses are skipped.
PLACEHOLDER_SHAPED = [
    ("کد [A_0912345] است", "کد [A_[PHONE_1]] است"),
    ("[PHONE_1] و [PHONE_9] و ۰۹۱۲ ۳۴۵", "[PHONE_1] و [PHONE_9] و [PHONE_1]"),
]


def test_tokens_that_contain_a_fragment_are_masked_whole(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-contained"
    )
    with _view(tenant, meeting_id, settings) as view:
        assert {text: view.text(text) for text, _ in CONTAINED} == dict(CONTAINED)


def test_a_label_of_the_local_part_is_masked_with_a_boundary(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-local-label"
    )
    with _view(tenant, meeting_id, settings) as view:
        assert {text: view.text(text) for text, _ in LOCAL_LABELS} == dict(LOCAL_LABELS)


def test_text_shaped_like_an_unknown_placeholder_is_searched(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-shaped"
    )
    with _view(tenant, meeting_id, settings) as view:
        rendered = {text: view.text(text) for text, _ in PLACEHOLDER_SHAPED}
        assert rendered == dict(PLACEHOLDER_SHAPED)


def test_only_values_that_share_four_digits_are_ranked(client, email_outbox, monkeypatch, settings):
    """SPEC 11, P1: a 4-digit window lookup comes before the ranking."""
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-speed"
    )
    # 250 mobile numbers whose last 8 digits use only 0-4 (base 5, all distinct).
    phones = []
    for index in range(250):
        number, tail = index * 7919 + 4321, ""
        for _ in range(8):
            number, digit = divmod(number, 5)
            tail = str(digit) + tail
        phones.append("091" + tail)
    _store_summary(meeting_id, overview=" و ".join(phones))
    # 300 runs of the digits 5-9 only: they share four consecutive digits with no
    # known value (5678, 6789 and 5679 are in the transcript's phone and ID).
    runs = [
        run
        for run in ("".join(digits) for digits in itertools.product("56789", repeat=4))
        if run not in {"5678", "6789", "5679"}
    ][:300]
    text = " و ".join(f"سال {run}" for run in runs)
    # Record each call of a ranking step: the pairwise common part, and the
    # digit ranking with the number of candidate values it was given.
    ranked: list[tuple[str, int]] = []

    def counting(name, rank):
        def counted(*args):
            ranked.append((name, len(args[0]) if name == "_shared_digits" else 1))
            return rank(*args)

        return counted

    for name in ("_common_part", "_shared_digits"):
        if hasattr(transcript_view, name):
            monkeypatch.setattr(
                transcript_view, name, counting(name, getattr(transcript_view, name))
            )
    with _view(tenant, meeting_id, settings) as view:
        assert view.text(text) == text
        assert ranked == []
        # The last six digits of the eighth number: its own placeholder wins.
        assert view.text(f"شماره {phones[7][5:]}") == "شماره [PHONE_9]"
    ((name, candidates),) = ranked
    assert name == "_shared_digits" and 0 < candidates < len(phones)


def test_text_without_a_fragment_is_the_base_output(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-allow"
    )
    with _view(tenant, meeting_id, settings) as view:
        assert {text: view.text(text) for text in ALLOWED} == {text: text for text in ALLOWED}
        assert view.text(None) is None
        # A whole value is still masked; the short run next to it stays (N1, N4).
        assert view.text("۰۹۱۲۳۴۵۶۷۸۹ و ۳۴۵") == "[PHONE_1] و ۳۴۵"


def test_fragments_of_values_known_from_free_texts(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-free"
    )
    # Whole values only the summary holds are known values too.
    _store_summary(meeting_id, overview=f"کارت {CARD} و شبا {IBAN} و ۰۹۳۵ ۱۱۱ ۲۲۲۲")
    with _view(tenant, meeting_id, settings) as view:
        assert view.text(f"کارت {CARD} و شبا {IBAN} و ۰۹۳۵ ۱۱۱ ۲۲۲۲") == (
            "کارت [BANK_CARD_1] و شبا [IBAN_1] و [PHONE_2]"
        )
        assert view.text(f"کارت {CARD[:4]} {CARD[4:8]}") == "کارت [BANK_CARD_1]"
        assert view.text(f"شبا {IBAN[:4]} {IBAN[4:8]}") == "شبا IR[IBAN_1]"
        assert view.text("۰۹۳۵ ۱۱۱") == "[PHONE_2]"


# --- numbering (N2) ---------------------------------------------------------------------


def test_fragments_never_add_or_shift_placeholders(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-numbers"
    )
    base = f"/api/v1/meetings/{meeting_id}"
    # Fragments first in the canonical order, then new whole values.
    _store_summary(
        meeting_id,
        overview="۰۹۱۲ ۳۴۵ و example.com و ۰۹۳۵ ۱۱۱ ۲۲۲۲ و other@sample.org",
        topics=["ali.rezaei", "۰۹۱۲۳۴۵۶۷۸۹"],
    )
    created = _post(
        client, tenant, f"{base}/actions", {"description": "۰۹۳۵ ۱۱۱ و other@sample.org"}
    )
    assert created.status_code == 201, created.text

    summary = _get(client, tenant, f"{base}/summary").json()
    assert summary["overview"] == "[PHONE_1] و [EMAIL_1] و [PHONE_2] و [EMAIL_2]"
    assert summary["topics"] == ["[EMAIL_1]", "[PHONE_1]"]
    (action,) = _get(client, tenant, f"{base}/actions").json()
    assert action["description"] == "[PHONE_2] و [EMAIL_2]"
    segments = _get(client, tenant, f"{base}/transcript").json()["segments"]
    assert segments[0]["text"].count("[PHONE_1]") == 1
    assert "[EMAIL_1]" in segments[1]["text"] and "[PHONE_1]" in segments[1]["text"]


# --- the original view (N3) and a meeting without known values (N4) ----------------------


def test_the_original_view_keeps_every_free_text(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-original"
    )
    base = f"/api/v1/meetings/{meeting_id}"
    _store_summary(meeting_id, overview=_field("خلاصه"), topics=[_field("موضوع")])
    created = _post(
        client,
        tenant,
        f"{base}/actions",
        {"description": _field("اقدام"), "assignee_text": _field("پیگیری")},
    )
    assert created.status_code == 201, created.text

    summary = _get(client, tenant, f"{base}/summary", view="original").json()
    assert summary["view"] == "original"
    assert summary["overview"] == _field("خلاصه") and summary["topics"] == [_field("موضوع")]
    (action,) = _get(client, tenant, f"{base}/actions", view="original").json()
    assert action["description"] == _field("اقدام")
    assert action["assignee_text"] == _field("پیگیری")
    with _view(tenant, meeting_id, settings, requested="original") as view:
        for text, _ in DENIED:
            assert view.text(text) == text
        assert view.point({"description": "0912-345"}) == {"description": "0912-345"}


PLAIN = "سلام، جلسه درباره گزارش ماهانه و برنامه سال بود."


def test_a_meeting_without_known_values_keeps_its_free_text(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "partial-plain")
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    accepted = _upload(client, tenant, key="partial-plain-0001")
    assert accepted.status_code == 202, accepted.text
    providers = ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript=PLAIN, language="fa"),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=_CapturingIntelligence(),
    )
    result = process_job(
        accepted.json()["job"]["id"],
        worker_id="partial-plain",
        settings=settings,
        providers=providers,
    )
    assert result["status"] == "completed", result
    meeting_id = accepted.json()["meeting"]["id"]
    overview = "بودجه ۰۹۱۲ ۳۴۵ و 0912-345 و example.com و ali.rezaei برای سال ۱۴۰۳"
    _store_summary(meeting_id, overview=overview, topics=["meeting.notes", "1234 5678"])
    summary = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/summary").json()
    assert summary["view"] == "redacted"
    assert summary["overview"] == overview
    assert summary["topics"] == ["meeting.notes", "1234 5678"]


# --- write-back (N5) --------------------------------------------------------------------


def test_editing_an_action_keeps_the_stored_fragment(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-write"
    )
    base = f"/api/v1/meetings/{meeting_id}"
    stored = "با ۰۹۱۲ ۳۴۵ تماس بگیرید"
    created = _post(
        client,
        tenant,
        f"{base}/actions",
        {"description": stored, "assignee_text": "ali.rezaei"},
    )
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]
    (listed,) = _get(client, tenant, f"{base}/actions").json()
    assert listed["description"] == "با [PHONE_1] تماس بگیرید"
    assert listed["assignee_text"] == "[EMAIL_1]"

    edited = _patch(
        client,
        tenant,
        f"{base}/actions/{action_id}",
        {
            "description": listed["description"] + " فوری",
            "assignee_text": listed["assignee_text"] + " (پیگیری)",
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "با [PHONE_1] تماس بگیرید فوری"
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, action_id)
        assert action.description == "با ۰۹۱۲ ۳۴۵ تماس بگیرید فوری"
        assert action.assignee_text == "ali.rezaei (پیگیری)"

    # Every [PHONE_1] of the shown text stood for the same stored text: each one
    # written back keeps it.
    twice = _post(client, tenant, f"{base}/actions", {"description": "۰۹۱۲ ۳۴۵ و باز ۰۹۱۲ ۳۴۵"})
    assert twice.status_code == 201, twice.text
    assert twice.json()["description"] == "[PHONE_1] و باز [PHONE_1]"
    edited = _patch(
        client,
        tenant,
        f"{base}/actions/{twice.json()['id']}",
        {"description": "[PHONE_1] و باز [PHONE_1] فوری"},
    )
    assert edited.status_code == 200, edited.text
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, twice.json()["id"])
        assert action.description == "۰۹۱۲ ۳۴۵ و باز ۰۹۱۲ ۳۴۵ فوری"


def test_an_ambiguous_placeholder_writes_back_the_whole_value(
    client, email_outbox, monkeypatch, settings
):
    """SPEC 11, N5: one placeholder shown for a fragment and for the whole value."""
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "partial-ambiguous"
    )
    base = f"/api/v1/meetings/{meeting_id}"
    mixed = _post(client, tenant, f"{base}/actions", {"description": "۰۹۱۲ ۳۴۵ یا ۰۹۱۲۳۴۵۶۷۸۹"})
    assert mixed.status_code == 201, mixed.text
    assert mixed.json()["description"] == "[PHONE_1] یا [PHONE_1]"
    edited = _patch(
        client, tenant, f"{base}/actions/{mixed.json()['id']}", {"description": "[PHONE_1]"}
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "[PHONE_1]"
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, mixed.json()["id"]).description == "۰۹۱۲۳۴۵۶۷۸۹"


# --- glued placeholders written back by a member (N7) -----------------------------------

ADDRESS_PARTS = ("ali", "rezaei", "example", "com", "@")


def _hides_the_address(text: str | None) -> bool:
    visible = re.sub(r"\[[A-Z_]+_[0-9]+\]", " ", text or "").lower()
    return not any(part in visible for part in ADDRESS_PARTS)


def test_glued_placeholders_never_show_the_address(client, email_outbox, monkeypatch, settings):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    tenant = _owner_tenant(owner)
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, "partial-glued")
    base = f"/api/v1/meetings/{meeting_id}"
    member = personas["member"]
    assert member.get(f"{base}/actions", params={"view": "original"}).status_code == 403

    created = member.post(
        f"{base}/actions",
        json={"description": "تماس با [EMAIL_1]2 و [EMAIL_1]-x", "assignee_text": "x[EMAIL_1]"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["description"] == "تماس با [EMAIL_1] و [EMAIL_1]"
    assert _hides_the_address(created.json()["assignee_text"]), created.json()

    # A stored fragment written back with a glued letter: the whole value is
    # restored (SPEC 11.2), and the read path masks it.
    fragment = member.post(
        f"{base}/actions", json={"description": "پیگیری ali.rezaei", "assignee_text": "ali.rezaei"}
    )
    assert fragment.status_code == 201, fragment.text
    assert fragment.json()["assignee_text"] == "[EMAIL_1]"
    edited = member.patch(
        f"{base}/actions/{fragment.json()['id']}",
        json={"description": "پیگیری [EMAIL_1]x", "assignee_text": "[EMAIL_1]x"},
    )
    assert edited.status_code == 200, edited.text
    assert _hides_the_address(edited.json()["description"]), edited.json()
    assert _hides_the_address(edited.json()["assignee_text"]), edited.json()
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, fragment.json()["id"]).assignee_text == "ali.rezaei@example.comx"

    glued = "[EMAIL_1]2 یا [EMAIL_1]-x یا x[EMAIL_1]"
    edited = member.patch(
        f"{base}/actions/{created.json()['id']}",
        json={"description": glued, "assignee_text": glued},
    )
    assert edited.status_code == 200, edited.text
    assert _hides_the_address(edited.json()["description"]), edited.json()
    assert _hides_the_address(edited.json()["assignee_text"]), edited.json()

    for reader in (member, owner):
        actions = reader.get(f"{base}/actions").json()
        assert len(actions) == 2
        for action in actions:
            assert action["view"] == "redacted"
            assert _hides_the_address(action["description"]), action
            assert _hides_the_address(action["assignee_text"]), action
    exported = member.get(f"{base}/export", params={"format": "json"}).json()
    for action in exported["actions"]:
        assert _hides_the_address(action["description"]), action
        assert _hides_the_address(action["assignee"]), action


def _member_meeting(client, email_outbox, monkeypatch, settings, label: str):
    _patch_media(monkeypatch)
    owner, personas, _ids = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    tenant = _owner_tenant(owner)
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    meeting_id, _ = _process(client, tenant, settings, label)
    return owner, personas["member"], f"/api/v1/meetings/{meeting_id}"


def _edit(member, base: str, stored: dict, sent: dict) -> tuple[dict, dict, ActionItem]:
    """POST an action with ``stored``, PATCH it with ``sent``: both responses
    and the stored row."""
    created = member.post(f"{base}/actions", json=stored)
    assert created.status_code == 201, created.text
    edited = member.patch(f"{base}/actions/{created.json()['id']}", json=sent)
    assert edited.status_code == 200, edited.text
    with inspection_session_factory()() as db:
        action = db.get(ActionItem, created.json()["id"])
    return created.json(), edited.json(), action


def _every_view_hides_the_address(base: str, *readers) -> None:
    for reader in readers:
        for listed in reader.get(f"{base}/actions").json():
            assert listed["view"] == "redacted"
            assert _hides_the_address(listed["description"]), listed
            assert _hides_the_address(listed["assignee_text"]), listed


# SPEC 11.2: a placeholder gets back its stored text only when it is kept from
# the shown text, unambiguous, and not glued to [A-Za-z0-9].


@pytest.mark.parametrize("previous", ["example", "x-example"])
def test_a_letter_glued_after_a_kept_placeholder_restores_the_whole_address(
    client, email_outbox, monkeypatch, settings, previous
):
    owner, member, base = _member_meeting(
        client, email_outbox, monkeypatch, settings, "partial-glued-after"
    )
    created, edited, action = _edit(
        member,
        base,
        {"description": "پیگیری", "assignee_text": previous},
        {"assignee_text": "[EMAIL_1]s"},
    )
    assert created["assignee_text"] == "[EMAIL_1]"
    assert action.assignee_text == "ali.rezaei@example.coms"  # never "examples"
    assert _hides_the_address(edited["assignee_text"]), edited
    _every_view_hides_the_address(base, member, owner)


def test_a_letter_glued_before_a_kept_placeholder_restores_the_whole_address(
    client, email_outbox, monkeypatch, settings
):
    owner, member, base = _member_meeting(
        client, email_outbox, monkeypatch, settings, "partial-glued-before"
    )
    created, edited, action = _edit(
        member, base, {"description": "شرکت example"}, {"description": "شرکت x[EMAIL_1]"}
    )
    assert created["description"] == "شرکت [EMAIL_1]"
    assert action.description == "شرکت xali.rezaei@example.com"  # never "xexample"
    assert _hides_the_address(edited["description"]), edited
    _every_view_hides_the_address(base, member, owner)


def test_an_added_placeholder_restores_the_whole_value(client, email_outbox, monkeypatch, settings):
    owner, member, base = _member_meeting(
        client, email_outbox, monkeypatch, settings, "partial-added"
    )
    created, edited, action = _edit(
        member,
        base,
        {"description": "با ۰۹۱۲ ۳۴۵ تماس"},
        {"description": "با [PHONE_1] تماس، شماره کامل [PHONE_1]"},
    )
    assert created["description"] == "با [PHONE_1] تماس"
    # The kept placeholder keeps the fragment; the added one is the whole number.
    assert action.description == "با ۰۹۱۲ ۳۴۵ تماس، شماره کامل ۰۹۱۲ ۳۴۵ ۶۷۸۹"
    assert edited["description"] == "با [PHONE_1] تماس، شماره کامل [PHONE_1]"


def test_a_placeholder_stored_literally_makes_it_ambiguous(
    client, email_outbox, monkeypatch, settings
):
    """SPEC 11.3: a known placeholder stored as text counts as its own stored text."""
    owner, member, base = _member_meeting(
        client, email_outbox, monkeypatch, settings, "partial-literal"
    )
    created = member.post(f"{base}/actions", json={"description": "تماس"})
    assert created.status_code == 201, created.text
    action_id = created.json()["id"]
    # As a model writes it under redaction: a placeholder next to a raw fragment.
    with inspection_session_factory()() as db:
        db.get(ActionItem, action_id).description = "با ۰۹۱۲ ۳۴۵ یا [PHONE_1] تماس"
        db.commit()
    (listed,) = member.get(f"{base}/actions").json()
    assert listed["description"] == "با [PHONE_1] یا [PHONE_1] تماس"
    edited = member.patch(f"{base}/actions/{action_id}", json={"description": "با [PHONE_1] یا"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["description"] == "با [PHONE_1] یا"
    with inspection_session_factory()() as db:
        assert db.get(ActionItem, action_id).description == "با ۰۹۱۲ ۳۴۵ ۶۷۸۹ یا"


def test_a_persian_digit_glued_to_a_kept_placeholder_restores_the_whole_value(
    client, email_outbox, monkeypatch, settings
):
    """SPEC 11.3: glue is checked after digit folding."""
    owner, member, base = _member_meeting(
        client, email_outbox, monkeypatch, settings, "partial-glued-digit"
    )
    created, edited, action = _edit(
        member,
        base,
        {"description": "پیگیری", "assignee_text": "98 912 تماس"},
        {"assignee_text": "۱[PHONE_1] تماس"},
    )
    assert created["assignee_text"] == "[PHONE_1] تماس"
    assert action.assignee_text == "۱۰۹۱۲ ۳۴۵ ۶۷۸۹ تماس"  # never "۱98 912"
    assert edited["assignee_text"] == "[PHONE_1] تماس"
    for reader in (member, owner):
        (listed,) = reader.get(f"{base}/actions").json()
        assert listed["assignee_text"] == "[PHONE_1] تماس"


# --- guards on the copied detector constants ---------------------------------------------


def test_fragment_rule_uses_the_detector_separators_and_priority():
    assert f"[{transcript_view._SEPARATORS}]?" == text_pii._SEP
    by_priority = sorted(text_pii._PRIORITY, key=text_pii._PRIORITY.__getitem__, reverse=True)
    assert list(transcript_view._CLASS_ORDER) == by_priority
