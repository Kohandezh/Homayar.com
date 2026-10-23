"""HOM-293: the subject export searches a structured decision's due date.

Synthetic fixtures only.  A structured decision (HOM-61) stores ``due_date``
as ``{status, value}`` in a free JSON column, so a stated value can hold any
text.  The subject export already searches ``owner.value``; it must search
``due_date.value`` the same way, right after the owner of the same decision.
Any other due date shape is skipped and never raises.

The transcript (``test_privacy_routing.SEGMENTS``) holds ``09123456789`` and
``ali.rezaei@example.com``; the values below appear nowhere in it.
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import true

from backend.privacy.subject_export import build_subject_export, parse_subject_identifier
from backend.services.analysis_claims import STRUCTURED_SUMMARY_SCHEMA_VERSION
from backend.tests._database import inspection_session_factory
from backend.tests.test_free_text_partial_pii import _redacted_meeting, _store_summary

NOT_STATED = {"status": "not_stated", "value": None}
PHONE = "09198880770"
PHONE_FA = "۰۹۱۹۸۸۸۰۷۷۰"
OTHER_PHONE = "09357771144"  # control: appears nowhere
EMAIL = "hom293.subject@example.org"


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


def _decision(*, owner=NOT_STATED, due_date=NOT_STATED, **extra) -> dict:
    """A decision as ``analysis_claims`` stores it; ``due_date`` may be any shape."""
    decision = {
        "section": "decision",
        "category": "decision",
        "description": "پیگیری",
        "basis": "explicit",
        "owner": owner,
        "due_date": due_date,
        "confidence": None,
        "source_turn_indexes": [],
        "evidence_ids": [],
        "evidence_status": "insufficient",
    }
    decision.update(extra)
    return decision


def _item(kind: str, text: str, *classes: str) -> dict:
    return {"kind": kind, "text": text, "matched": list(classes)}


def _due(text: str, *classes: str) -> dict:
    return _item("decision_due_date", text, *classes)


def _owner(text: str, *classes: str) -> dict:
    return _item("decision_owner", text, *classes)


def _export(workspace_id: str, identifier: str) -> tuple[dict, dict]:
    with inspection_session_factory()() as db:
        return build_subject_export(
            db,
            workspace_id=workspace_id,
            identifiers=[parse_subject_identifier(identifier)],
            meeting_clause=true(),
            now=datetime.now(UTC),
        )


OVERVIEW = "خلاصه"
OVERVIEW_PHONE = f"خلاصه با شماره {PHONE}"
DUE_TEXT = f"call Ali at {PHONE} before Friday"
DUE_TEXT_FA = f"تا جمعه با {PHONE_FA} تماس بگیر"
DUE_EMAIL = f"send to {EMAIL} by 2026-11-03"
OWNER_TEXT = f"پشتیبانی {PHONE}"

# (name, overview, decisions, identifier, expected summary items or None for no match)
CASES = [
    # AC1: the only match is in the due date.
    (
        "phone only in due date",
        OVERVIEW,
        [_decision(due_date=_stated(DUE_TEXT))],
        PHONE,
        [_due(DUE_TEXT, "phone")],
    ),
    (
        "email only in due date",
        OVERVIEW,
        [_decision(due_date=_stated(DUE_EMAIL))],
        EMAIL,
        [_due(DUE_EMAIL, "email")],
    ),
    # AC2: Persian digits in the due date match a Latin-digit identifier.
    (
        "persian digits in due date",
        OVERVIEW,
        [_decision(due_date=_stated(DUE_TEXT_FA))],
        PHONE,
        [_due(DUE_TEXT_FA, "phone")],
    ),
    # AC3 deny: a different phone is a real non-match.
    (
        "due date with a different phone",
        OVERVIEW,
        [_decision(due_date=_stated(DUE_TEXT))],
        OTHER_PHONE,
        None,
    ),
    ("plain iso due date", OVERVIEW, [_decision(due_date=_stated("2026-11-03"))], PHONE, None),
    # AC3 / AC4: owner only, due date not stated or a plain date.
    (
        "owner matches, due date not stated",
        OVERVIEW,
        [_decision(owner=_stated(OWNER_TEXT), due_date=NOT_STATED)],
        PHONE,
        [_owner(OWNER_TEXT, "phone")],
    ),
    (
        "owner matches, due date plain",
        OVERVIEW,
        [_decision(owner=_stated(OWNER_TEXT), due_date=_stated("2026-11-03"))],
        PHONE,
        [_owner(OWNER_TEXT, "phone")],
    ),
    # AC4: both match, owner first.
    (
        "owner and due date both match",
        OVERVIEW,
        [_decision(owner=_stated(OWNER_TEXT), due_date=_stated(DUE_TEXT))],
        PHONE,
        [_owner(OWNER_TEXT, "phone"), _due(DUE_TEXT, "phone")],
    ),
    # Order across decisions: owner then due date of each decision, in order.
    (
        "two decisions in order",
        OVERVIEW_PHONE,
        [
            _decision(owner=_stated(OWNER_TEXT), due_date=_stated(DUE_TEXT)),
            _decision(due_date=_stated(DUE_TEXT_FA)),
        ],
        PHONE,
        [
            _item("overview", OVERVIEW_PHONE, "phone"),
            _owner(OWNER_TEXT, "phone"),
            _due(DUE_TEXT, "phone"),
            _due(DUE_TEXT_FA, "phone"),
        ],
    ),
    # Allow-control for the bare string below: the same text inside {"value": ...}.
    (
        "same text inside a value dict",
        OVERVIEW,
        [_decision(due_date={"value": PHONE})],
        PHONE,
        [_due(PHONE, "phone")],
    ),
    # AC5 deny: shapes that are not a string-valued dict add nothing and never raise.
    ("due date int value", OVERVIEW, [_decision(due_date=_stated(20261103))], PHONE, None),
    ("due date bare string", OVERVIEW, [_decision(due_date=PHONE)], PHONE, None),
    ("due date list value", OVERVIEW, [_decision(due_date=_stated([PHONE]))], PHONE, None),
    ("due date dict value", OVERVIEW, [_decision(due_date=_stated({"text": PHONE}))], PHONE, None),
    ("due date none value", OVERVIEW, [_decision(due_date=_stated(None))], PHONE, None),
    ("due date is none", OVERVIEW, [_decision(due_date=None)], PHONE, None),
    ("due date is a list", OVERVIEW, [_decision(due_date=[PHONE])], PHONE, None),
    (
        "due date dict without value",
        OVERVIEW,
        [_decision(due_date={"status": "stated"})],
        PHONE,
        None,
    ),
]
CASES.append(
    (
        "due date key missing",
        OVERVIEW,
        [{key: value for key, value in _decision().items() if key != "due_date"}],
        PHONE,
        None,
    )
)
CASES.append(("decision not a dict", OVERVIEW, [PHONE], PHONE, None))
# AC5: a bad due date does not hide a match in another text of the same meeting.
for _name, _shape in [
    ("int", _stated(20261103)),
    ("bare string", PHONE),
    ("none", None),
    ("list value", _stated([PHONE])),
]:
    CASES.append(
        (
            f"bad due date ({_name}) next to a matching overview",
            OVERVIEW_PHONE,
            [_decision(due_date=_shape)],
            PHONE,
            [_item("overview", OVERVIEW_PHONE, "phone")],
        )
    )


def _problems(name, document, audit, meeting_id, identifier, expected) -> list[str]:
    """What is wrong with one case's export, as readable lines."""
    problems = []
    matches = document["matches"]
    if expected is None:
        if matches != []:
            problems.append(f"{name}: expected no match, got {matches}")
        if audit["meetings_matched"] != 0:
            problems.append(f"{name}: audit meetings_matched {audit['meetings_matched']}")
    elif len(matches) != 1:
        problems.append(f"{name}: expected one match, got {matches}")
    else:
        (match,) = matches
        if match["meeting_id"] != meeting_id:
            problems.append(f"{name}: wrong meeting {match['meeting_id']}")
        if match["revisions"] != [] or match["actions"] != [] or "extraction" in match:
            problems.append(f"{name}: unexpected match outside the summary: {match}")
        if match["summary"] != expected:
            problems.append(f"{name}: summary {match['summary']} != {expected}")
        if audit["meetings_matched"] != 1:
            problems.append(f"{name}: audit meetings_matched {audit['meetings_matched']}")
    # AC6: the audit summary holds no identifier value.
    dumped = json.dumps(audit, ensure_ascii=False)
    canonical = parse_subject_identifier(identifier).value
    for value in sorted({identifier, canonical, PHONE, PHONE_FA, EMAIL}):
        if value in dumped:
            problems.append(f"{name}: audit holds {value}")
    return problems


def test_subject_export_searches_decision_due_dates(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _redacted_meeting(
        client, email_outbox, monkeypatch, settings, "subject-due-date"
    )
    problems: list[str] = []
    for name, overview, decisions, identifier, expected in CASES:
        _store_summary(
            meeting_id,
            schema_version=STRUCTURED_SUMMARY_SCHEMA_VERSION,
            overview=overview,
            topics=[],
            key_points=[],
            decisions=decisions,
        )
        document, audit = _export(tenant["workspace_id"], identifier)
        problems += _problems(name, document, audit, meeting_id, identifier, expected)
    assert problems == [], "\n".join(problems)
