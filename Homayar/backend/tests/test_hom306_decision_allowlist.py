"""HOM-306: the decisions of /summary and the share view allow only the
known top-level keys.

Synthetic fixtures only.  ``MeetingSummary.decisions`` is free JSON: the
processor writes a labeled insight (the six insight keys) or a structured
decision (the same six plus ``section``, ``basis``, ``owner``, ``due_date``),
but a hand-written or older row can hold anything.  ``_decision_point``
rendered the texts and fixed only ``owner`` and ``due_date`` (HOM-294); every
other top-level key left the API exactly as stored, so an extra key held raw
on the redacted /summary and the share link.  Every decision now leaves as a
new dict with only the allowed keys, in every view, and /report is unchanged.

The transcript (``test_privacy_routing.SEGMENTS``) holds the known values
``[IR_NATIONAL_ID_1]``, ``[PHONE_1]`` (``09123456789``) and ``[EMAIL_1]``.
"""

from __future__ import annotations

import pytest

from backend.services.analysis_claims import STRUCTURED_SUMMARY_SCHEMA_VERSION
from backend.tests.test_free_text_partial_pii import _redacted_meeting, _store_summary
from backend.tests.test_hom294_decision_detail_allowlist import (
    PHONE,
    PHONE_FA,
    _decision,
    _key,
    _no_digits,
    _report,
    _shared,
    _stated,
    _summary,
)
from backend.tests.test_privacy_routing import _dump, _no_raw


@pytest.fixture
def settings(settings):
    # One keyed configuration for the API and the processor (mappings retained).
    return settings.model_copy(
        update={"pii_mapping_keys": f"k1:{_key()}", "pii_mapping_active_key_id": "k1"}
    )


def _labeled(description, **extra) -> dict:
    """A decision as a labeled insight (``decisions_for`` stores it), plus
    any extra keys the caller plants on it."""
    point = {
        "category": "decision",
        "description": description,
        "confidence": 0.5,
        "source_turn_indexes": [0],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }
    point.update(extra)
    return point


def _structured(description, **extra) -> dict:
    """A structured decision as ``cite_claims`` stores it, plus any extra
    keys the caller plants on it."""
    point = _decision(description)
    point.update(extra)
    return point


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


# --- AC1: an extra top-level key is dropped, its digits never leave -----------------------


def test_an_extra_top_level_key_is_dropped_on_both_processor_shapes(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom306-extra",
        _structured("پیگیری", note=PHONE),
        _labeled(f"تماس با {PHONE}", note=PHONE),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    assert summary["view"] == "redacted"
    structured, labeled = summary["decisions"]
    assert structured["description"] == "پیگیری"
    assert "note" not in structured
    assert labeled["description"] == "تماس با [PHONE_2]"
    assert "note" not in labeled
    body = _dump(summary)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC1: the allow-controls keep every allowed key of both shapes ------------------------


def test_processor_decisions_keep_every_allowed_key_masked(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom306-allow",
        _labeled(f"تماس با {PHONE}"),
        _structured("سالم", owner=_stated(f"مسئول {PHONE}"), due_date=_stated("2026-11-03")),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    labeled, structured = summary["decisions"]
    assert labeled == {
        "category": "decision",
        "description": "تماس با [PHONE_2]",
        "confidence": 0.5,
        "source_turn_indexes": [0],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }
    assert structured == {
        "section": "decision",
        "category": "decision",
        "description": "سالم",
        "basis": "explicit",
        "owner": _stated("مسئول [PHONE_2]"),
        "due_date": _stated("2026-11-03"),
        "confidence": None,
        "source_turn_indexes": [],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }


# --- AC2: the share link holds the same decisions -----------------------------------------


def test_the_share_link_shows_the_same_decisions_as_the_summary(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom306-share",
        _structured("پیگیری", note=PHONE),
        _labeled(f"تماس با {PHONE}", note=PHONE),
        _structured("سالم", owner=_stated(f"مسئول {PHONE}"), due_date=_stated("2026-11-03")),
        public_share_links_enabled=True,
    )
    shared = _shared(client, tenant, meeting_id)
    deny_structured, deny_labeled, allow = shared["summary"]["decisions"]
    assert "note" not in deny_structured
    assert "note" not in deny_labeled
    assert allow["owner"] == _stated("مسئول [PHONE_2]")
    summary = _summary(client, tenant, meeting_id)
    assert shared["summary"]["decisions"] == summary["decisions"]
    body = _dump(shared)
    assert _no_raw(body)
    assert _no_digits(body, PHONE, PHONE_FA, "8880770", "۸۸۸۰۷۷۰")


# --- AC3, rule table row by row: the wrong shape drops, the right one keeps ----------------


DROPPED = object()

RULE_ROWS = [
    pytest.param(
        "confidence-bool", "confidence", {"confidence": True}, DROPPED, {"confidence": 1}, 1
    ),
    pytest.param(
        "confidence-none", "confidence", {"confidence": True}, DROPPED, {"confidence": None}, None
    ),
    pytest.param(
        "source-turn-indexes-bool",
        "source_turn_indexes",
        {"source_turn_indexes": [True]},
        DROPPED,
        {"source_turn_indexes": [0]},
        [0],
    ),
    pytest.param(
        "source-turn-indexes-string",
        "source_turn_indexes",
        {"source_turn_indexes": ["0"]},
        DROPPED,
        {"source_turn_indexes": [0]},
        [0],
    ),
    pytest.param(
        "evidence-ids",
        "evidence_ids",
        {"evidence_ids": [1]},
        DROPPED,
        {"evidence_ids": ["e1"]},
        ["e1"],
    ),
    pytest.param(
        "evidence-status",
        "evidence_status",
        {"evidence_status": "SUFFICIENT"},
        "insufficient",
        {"evidence_status": "partial"},
        "partial",
    ),
    pytest.param(
        "section-capital",
        "section",
        {"section": "Decision"},
        DROPPED,
        {"section": "decision"},
        "decision",
    ),
    pytest.param(
        "section-key-moment",
        "section",
        {"section": "key_moment"},
        DROPPED,
        {"section": "decision"},
        "decision",
    ),
    pytest.param(
        "section-phone", "section", {"section": PHONE}, DROPPED, {"section": "decision"}, "decision"
    ),
    pytest.param(
        "section-number", "section", {"section": 1}, DROPPED, {"section": "decision"}, "decision"
    ),
    pytest.param(
        "basis-phone", "basis", {"basis": PHONE}, DROPPED, {"basis": "explicit"}, "explicit"
    ),
    pytest.param(
        "basis-wrong-case",
        "basis",
        {"basis": "Explicit"},
        DROPPED,
        {"basis": "inferred"},
        "inferred",
    ),
]


@pytest.mark.parametrize(
    ("name", "key", "deny_extra", "deny_expected", "allow_extra", "allow_expected"),
    RULE_ROWS,
)
def test_a_wrong_rule_row_shape_is_dropped_and_a_right_one_is_kept(
    client,
    email_outbox,
    monkeypatch,
    settings,
    name,
    key,
    deny_extra,
    deny_expected,
    allow_extra,
    allow_expected,
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        f"hom306-rule-{name}",
        _labeled("غلط", **deny_extra),
        _labeled("درست", **allow_extra),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    assert summary["view"] == "redacted"
    deny, allow = summary["decisions"]
    assert deny["description"] == "غلط"
    assert allow["description"] == "درست"
    if deny_expected is DROPPED:
        assert key not in deny
    else:
        assert deny[key] == deny_expected
    assert allow[key] == allow_expected
    assert _no_digits(_dump(summary), PHONE, PHONE_FA)


# --- AC3: a non-string category or description is dropped ---------------------------------


def test_non_string_texts_are_dropped_and_a_string_one_is_masked(
    client, email_outbox, monkeypatch, settings
):
    description_point = _labeled({"value": PHONE}, note=PHONE)
    category_point = _labeled("دسته")
    category_point["category"] = [PHONE]
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom306-shape",
        description_point,
        category_point,
        _labeled(f"تماس با {PHONE}"),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id)
    deny_description, deny_category, allow = summary["decisions"]
    assert "description" not in deny_description
    assert "note" not in deny_description
    assert "category" not in deny_category
    assert deny_category["description"] == "دسته"
    assert allow["description"] == "تماس با [PHONE_2]"
    shared = _shared(client, tenant, meeting_id)
    assert shared["summary"]["decisions"] == summary["decisions"]
    body = _dump(summary)
    assert _no_digits(body, PHONE, PHONE_FA)


# --- AC4: the original view keeps the allow list, not the raw row -------------------------


def test_the_original_view_keeps_only_the_allowed_keys_as_stored(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom306-original",
        _structured(
            f"تماس با {PHONE}",
            owner=_stated(f"مسئول {PHONE}"),
            due_date=_stated("2026-11-03"),
            note=PHONE,
        ),
        _labeled("بدون جزئیات"),
        public_share_links_enabled=True,
    )
    summary = _summary(client, tenant, meeting_id, view="original")
    assert summary["view"] == "original"
    structured, labeled = summary["decisions"]
    assert structured == {
        "section": "decision",
        "category": "decision",
        "description": f"تماس با {PHONE}",
        "basis": "explicit",
        "owner": _stated(f"مسئول {PHONE}"),
        "due_date": _stated("2026-11-03"),
        "confidence": None,
        "source_turn_indexes": [],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }
    assert labeled == {
        "category": "decision",
        "description": "بدون جزئیات",
        "confidence": 0.5,
        "source_turn_indexes": [0],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }


# --- AC5: /report keeps its own decision shapes -------------------------------------------


def test_the_report_keeps_its_own_decision_shapes(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "hom306-report",
        _structured(
            f"تماس با {PHONE}",
            owner=_stated(f"مسئول {PHONE}"),
            due_date=_stated(f"تا {PHONE}"),
            note=PHONE,
        ),
        public_share_links_enabled=True,
    )
    report = _report(client, tenant, meeting_id)
    (reported,) = report["outcome"]["decisions"]
    assert reported == {
        "text": "تماس با [PHONE_2]",
        "key_point_index": None,
        "confidence": None,
        "evidence_status": "insufficient",
        "supported": False,
        "evidence": [],
        "basis": "explicit",
        "owner": _stated("مسئول [PHONE_2]"),
        "due_date": _stated("تا [PHONE_2]"),
    }
    body = _dump(report)
    assert _no_digits(body, PHONE, PHONE_FA)
