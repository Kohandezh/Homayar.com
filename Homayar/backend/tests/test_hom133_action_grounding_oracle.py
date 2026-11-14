"""HOM-133 oracle (hom133-t1): explicit action owners and due dates, with evidence.

Written from SPEC.md before the implementation was visible.  It checks the observable
contract: ``ground_action`` keeps an owner or a due date only when a cited turn states
it (G1 to G5, with G2a, G2b, G2c and G3b), the held-out set scores at least 0.95
owner and due precision with every missing value left unset (F38), the processor
stores only grounded actions with their kind, run and evidence (AC1 to AC3), the
actions route returns ``kind`` (AC4), a
voicemail re-analysis keeps a person's assignment (AC5), the ``kind`` migration on
SQLite and PostgreSQL (AC6) and the mock provider that still stores no action (AC8).
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib
import itertools
import json
import re
import socket
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError

from backend.database.model_registry import Base
from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    AuditEvent,
    EvidenceSpan,
    JobState,
    Meeting,
    ProcessingJob,
)
from backend.jobs import recompute
from backend.jobs.processor import process_job
from backend.observability.release import PROJECT_ROOT
from backend.providers import egress
from backend.providers.base import ASRProvider, ProviderError, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.schemas import (
    UNKNOWN_SPEAKER_LABEL,
    ASRRequest,
    ASRResult,
    ASRSegment,
    IntelligenceRequest,
    TranscriptTurn,
)
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.routers.v1 import voicemail as voicemail_router
from backend.speech.dates import TEHRAN
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_authorization_matrix import _workspace_with_roles
from backend.tests.test_ingestion_ledger import _widen_concurrency
from backend.tests.test_insights_shares_api import _write_headers
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider, _providers
from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata

HELDOUT = Path(__file__).parent / "fixtures" / "evaluation" / "action_items" / "heldout.jsonl"
REFERENCE = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
YA = "ي"
KINDS = {"commitment", "next_step"}
_keys = itertools.count(1)


def _grounding():
    return importlib.import_module("backend.services.action_grounding")


def _ground(item, turns, *, reference=REFERENCE, language="fa"):
    return _grounding().ground_action(item, turns, reference=reference, language=language)


def _item(**fields) -> ProviderActionItem:
    fields.setdefault("description", "ارسال پیش‌فاکتور برای مشتری")
    return ProviderActionItem(**fields)


def _turns(*pairs: tuple[str, str]) -> list[TranscriptTurn]:
    return [TranscriptTurn(speaker_label=label, text=said) for label, said in pairs]


UNIT_TURNS = (
    ("SPEAKER_0", "خانم رضایی، لطفاً پیش‌فاکتور رو تا فردا برای مشتری بفرستید."),
    ("SPEAKER_1", "باشه، من تا پس‌فردا فاکتور نهایی رو هم می‌فرستم."),
    ("SPEAKER_0", "آقای کریمی قرارداد رو تا ۱۴۰۵/۰۹/۱۰ بررسی می‌کنه."),
)
EN_TURNS = (
    ("SPEAKER_0", "Who sends the deck?"),
    ("SPEAKER_1", "Sarah will send the deck on 2026-11-20."),
)
RANGE_TURNS = (("SPEAKER_1", "انبار مرکزی هفته بعد، یعنی شنبه آینده، سفارش رو تحویل می‌ده."),)
TIME_TURNS = (
    (
        "SPEAKER_1",
        "ساعت ده صبح زنگ می‌زنم و امروز همه‌چیز رو می‌فرستم. فردا ساعت ده صبح هم جلسه داریم.",
    ),
)
WEEKDAY_TURNS = (("SPEAKER_1", "آقای حسینی دوشنبه جواب رو می‌ده، یعنی دوشنبه بعد."),)
DAY_MONTH_TURNS = (("SPEAKER_1", "تیم حقوقی تا ۲۵ آبان ۱۴۰۵ پیش‌نویس رو نهایی می‌کنه."),)
EN_RELATIVE_TURNS = (("SPEAKER_1", "Sarah will send it tomorrow."),)
LABEL_NAME_TURNS = (("رضا", "رضا هستم، گزارش رو فردا می‌فرستم."),)
WORD_TURNS = (("SPEAKER_1", "علیرضا گزارش رو می‌فرسته و علی فاکتور رو."),)
DIGIT_TURNS = (("SPEAKER_1", "تیم ۲۱ گزارش رو می‌فرسته و تیم ۲ فاکتور رو."),)
EN_WORD_TURNS = (("SPEAKER_1", "Alice will send the deck and Al will send the invoice."),)
TWO_DATE_TURNS = (
    ("SPEAKER_1", "فاکتور رو فردا یا پس‌فردا می‌فرستم."),
    ("SPEAKER_1", "فاکتور رو فردا می‌فرستم."),
)
SAME_DAY_TURNS = (("SPEAKER_1", "فردا، یعنی ۱۴۰۵/۰۸/۲۲، فاکتور رو می‌فرستم."),)
DATE_OR_WEEKDAY_TURNS = (("SPEAKER_1", "فاکتور رو فردا یا دوشنبه می‌فرستم."),)
PROMISE = "من فردا پیش‌فاکتور رو می‌فرستم"
UNKNOWN_TURNS = ((UNKNOWN_SPEAKER_LABEL, f"{PROMISE}."),)
LABELLED_TURNS = (("SPEAKER_1", f"{PROMISE}."),)
UNKNOWN_NAMED_TURNS = ((UNKNOWN_SPEAKER_LABEL, "رضا هستم، فردا پیش‌فاکتور رو می‌فرستم."),)
MIXED_LABEL_TURNS = ((UNKNOWN_SPEAKER_LABEL, f"{PROMISE}."), ("SPEAKER_1", f"{PROMISE}."))
FIRST_PERSON = "من تا پس‌فردا فاکتور نهایی رو هم می‌فرستم"
REZAEI_QUOTE = "خانم رضایی، لطفاً پیش‌فاکتور رو"


# --- G1: an action without a cited turn in range is dropped ------------------------------


@pytest.mark.parametrize(
    ("indexes", "expected"),
    [
        pytest.param([], None, id="deny-no-index"),
        pytest.param([0], (0,), id="allow-one-index"),
        pytest.param([3, 7], None, id="deny-every-index-out-of-range"),
        pytest.param([7, 2], (2,), id="allow-one-index-in-range"),
        pytest.param([3], None, id="deny-index-equal-to-turn-count"),
        pytest.param([2], (2,), id="allow-last-index"),
        pytest.param([2, 0, 2, 9, 0, 1], (2, 0, 1), id="allow-first-seen-order-no-duplicates"),
    ],
)
def test_g1_only_cited_turns_in_range_keep_an_action(indexes, expected):
    grounded = _ground(_item(source_turn_indexes=indexes), _turns(*UNIT_TURNS))
    if expected is None:
        assert grounded is None
    else:
        assert grounded is not None
        assert grounded.turn_indexes == expected
        assert isinstance(grounded.turn_indexes, tuple)


def test_g1_an_explicit_owner_and_date_do_not_save_an_action_without_evidence():
    fields = {
        "kind": "commitment",
        "owner": "خانم رضایی",
        "owner_quote": REZAEI_QUOTE,
        "due_date": date(2026, 11, 13),
        "due_quote": "تا فردا",
    }
    assert _ground(_item(**fields, source_turn_indexes=[5, 9]), _turns(*UNIT_TURNS)) is None
    assert _ground(_item(**fields, source_turn_indexes=[0]), [], reference=REFERENCE) is None
    kept = _ground(_item(**fields, source_turn_indexes=[9, 0]), _turns(*UNIT_TURNS))
    assert kept is not None
    assert (kept.owner, kept.due_date, kept.turn_indexes) == (
        "خانم رضایی",
        date(2026, 11, 13),
        (0,),
    )


# --- G2: the owner is kept only when a cited turn names it ----------------------------------


OWNER_RULES = [
    pytest.param(
        UNIT_TURNS,
        {"owner": "خانم رضایی", "owner_quote": REZAEI_QUOTE},
        [0],
        "fa",
        "خانم رضایی",
        id="allow-owner-quoted-in-cited-turn",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "آقای جعفری", "owner_quote": "آقای جعفری پیش‌فاکتور را می‌فرستد"},
        [0, 1, 2],
        "fa",
        None,
        id="deny-owner-and-quote-invented",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "خانم توکلی", "owner_quote": REZAEI_QUOTE},
        [0],
        "fa",
        None,
        id="deny-owner-missing-from-a-real-quote",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "آقای کریمی", "owner_quote": "آقای کریمی قرارداد رو"},
        [0, 1],
        "fa",
        None,
        id="deny-owner-quoted-only-in-uncited-turn",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "آقای کریمی", "owner_quote": "آقای کریمی قرارداد رو"},
        [0, 2],
        "fa",
        "آقای کریمی",
        id="allow-owner-quoted-turn-cited",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": None, "owner_quote": REZAEI_QUOTE},
        [0],
        "fa",
        None,
        id="deny-owner-absent",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "   ", "owner_quote": REZAEI_QUOTE},
        [0],
        "fa",
        None,
        id="deny-owner-blank",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "خانم رضایی", "owner_quote": None},
        [0],
        "fa",
        None,
        id="deny-owner-quote-absent",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "خانم رضایی", "owner_quote": "   "},
        [0],
        "fa",
        None,
        id="deny-owner-quote-blank",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_1", "owner_quote": "،"},
        [1],
        "fa",
        None,
        id="deny-owner-quote-only-punctuation",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "،", "owner_quote": REZAEI_QUOTE},
        [0],
        "fa",
        None,
        id="deny-owner-only-punctuation",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_1", "owner_quote": FIRST_PERSON},
        [1],
        "fa",
        "SPEAKER_1",
        id="allow-first-person-speaker-label",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_0", "owner_quote": FIRST_PERSON},
        [0, 1],
        "fa",
        None,
        id="deny-first-person-label-of-another-turn",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "commitment", "owner": "speaker_1", "owner_quote": FIRST_PERSON},
        [1],
        "fa",
        None,
        id="deny-first-person-label-other-case",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_1", "owner_quote": FIRST_PERSON},
        [0, 2],
        "fa",
        None,
        id="deny-first-person-turn-not-cited",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_1", "owner_quote": "برای مشتری بفرستید. باشه، من"},
        [0, 1],
        "fa",
        None,
        id="deny-quote-spans-two-turns",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "خانم رضایی", "owner_quote": "خانم رضایی لطفاً پیش‌فاکتور رو"},
        [0],
        "fa",
        "خانم رضایی",
        id="allow-quote-without-punctuation",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": f"خانم رضای{YA}", "owner_quote": f"خانم رضا{YA}{YA}، لطفاً"},
        [0],
        "fa",
        f"خانم رضای{YA}",
        id="allow-arabic-letters-owner-kept-verbatim",
    ),
    pytest.param(
        EN_TURNS,
        {"owner": "sarah", "owner_quote": "SARAH WILL SEND THE DECK"},
        [1],
        "en",
        "sarah",
        id="allow-english-other-case-owner-kept-verbatim",
    ),
    pytest.param(
        EN_TURNS,
        {"owner": "Mike", "owner_quote": "Mike will send the deck"},
        [0, 1],
        "en",
        None,
        id="deny-english-owner-invented",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "next_step", "owner": "SPEAKER_1", "owner_quote": FIRST_PERSON},
        [1],
        "fa",
        None,
        id="deny-g2a-next-step-owner-is-the-speaker-label",
    ),
    pytest.param(
        UNIT_TURNS,
        {"owner": "SPEAKER_1", "owner_quote": FIRST_PERSON},
        [1],
        "fa",
        None,
        id="deny-g2a-unlabelled-owner-is-the-speaker-label",
    ),
    pytest.param(
        UNIT_TURNS,
        {"kind": "next_step", "owner": "خانم رضایی", "owner_quote": REZAEI_QUOTE},
        [0],
        "fa",
        "خانم رضایی",
        id="allow-g2a-next-step-owner-named-in-the-quote",
    ),
    pytest.param(
        LABEL_NAME_TURNS,
        {"kind": "next_step", "owner": "رضا", "owner_quote": "گزارش رو فردا می‌فرستم"},
        [0],
        "fa",
        None,
        id="deny-g2a-next-step-label-path-only",
    ),
    pytest.param(
        LABEL_NAME_TURNS,
        {"kind": "commitment", "owner": "رضا", "owner_quote": "گزارش رو فردا می‌فرستم"},
        [0],
        "fa",
        "رضا",
        id="allow-g2a-commitment-label-path-only",
    ),
    pytest.param(
        LABEL_NAME_TURNS,
        {"kind": "next_step", "owner": "رضا", "owner_quote": "رضا هستم، گزارش رو فردا می‌فرستم"},
        [0],
        "fa",
        "رضا",
        id="allow-g2a-next-step-label-that-the-quote-also-names",
    ),
    pytest.param(
        WORD_TURNS,
        {"owner": "علی", "owner_quote": "علیرضا گزارش رو می‌فرسته"},
        [0],
        "fa",
        None,
        id="deny-g2b-owner-is-the-start-of-a-longer-name",
    ),
    pytest.param(
        WORD_TURNS,
        {"owner": "رضا", "owner_quote": "علیرضا گزارش رو"},
        [0],
        "fa",
        None,
        id="deny-g2b-owner-is-the-end-of-a-longer-name",
    ),
    pytest.param(
        WORD_TURNS,
        {"owner": "علی", "owner_quote": "علی فاکتور رو"},
        [0],
        "fa",
        "علی",
        id="allow-g2b-owner-as-a-whole-word",
    ),
    pytest.param(
        WORD_TURNS,
        {"owner": "علی", "owner_quote": "می‌فرسته و علی"},
        [0],
        "fa",
        "علی",
        id="allow-g2b-owner-at-the-end-of-the-quote",
    ),
    pytest.param(
        DIGIT_TURNS,
        {"owner": "تیم ۲", "owner_quote": "تیم ۲۱ گزارش رو"},
        [0],
        "fa",
        None,
        id="deny-g2b-digit-right-after-the-owner",
    ),
    pytest.param(
        DIGIT_TURNS,
        {"owner": "تیم 2", "owner_quote": "تیم ۲ فاکتور رو"},
        [0],
        "fa",
        "تیم 2",
        id="allow-g2b-latin-digit-owner-as-a-whole-word",
    ),
    pytest.param(
        EN_WORD_TURNS,
        {"owner": "Al", "owner_quote": "Alice will send the deck"},
        [0],
        "en",
        None,
        id="deny-g2b-english-owner-inside-a-name",
    ),
    pytest.param(
        EN_WORD_TURNS,
        {"owner": "Al", "owner_quote": "Al will send the invoice"},
        [0],
        "en",
        "Al",
        id="allow-g2b-english-owner-as-a-word",
    ),
    pytest.param(
        UNKNOWN_TURNS,
        {"kind": "commitment", "owner": UNKNOWN_SPEAKER_LABEL, "owner_quote": PROMISE},
        [0],
        "fa",
        None,
        id="deny-g2c-commitment-owner-is-the-unknown-label",
    ),
    pytest.param(
        LABELLED_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_1", "owner_quote": PROMISE},
        [0],
        "fa",
        "SPEAKER_1",
        id="allow-g2c-same-promise-with-a-real-label",
    ),
    pytest.param(
        UNKNOWN_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_UNKNOWN", "owner_quote": PROMISE},
        [0],
        "fa",
        None,
        id="deny-g2c-unknown-label-in-another-case",
    ),
    pytest.param(
        UNKNOWN_NAMED_TURNS,
        {
            "kind": "commitment",
            "owner": "رضا",
            "owner_quote": "رضا هستم، فردا پیش‌فاکتور رو می‌فرستم",
        },
        [0],
        "fa",
        "رضا",
        id="allow-g2c-name-path-on-an-unknown-label-turn",
    ),
    pytest.param(
        MIXED_LABEL_TURNS,
        {"kind": "commitment", "owner": UNKNOWN_SPEAKER_LABEL, "owner_quote": PROMISE},
        [0, 1],
        "fa",
        None,
        id="deny-g2c-unknown-label-owner-next-to-a-labelled-cited-turn",
    ),
    pytest.param(
        MIXED_LABEL_TURNS,
        {"kind": "commitment", "owner": "SPEAKER_1", "owner_quote": PROMISE},
        [0, 1],
        "fa",
        "SPEAKER_1",
        id="allow-g2c-real-label-of-the-other-cited-turn",
    ),
]


@pytest.mark.parametrize(("turns", "fields", "indexes", "language", "owner"), OWNER_RULES)
def test_g2_the_owner_is_kept_only_when_a_cited_turn_names_it(
    turns, fields, indexes, language, owner
):
    grounded = _ground(
        _item(**fields, source_turn_indexes=indexes), _turns(*turns), language=language
    )
    assert grounded is not None
    assert grounded.owner == owner
    assert grounded.owner_status == ("unset" if owner is None else "explicit")
    assert grounded.due_date is None
    assert grounded.due_status == "unset"


def test_g2_the_status_names_are_the_public_constants():
    module = _grounding()
    assert (module.OWNER_EXPLICIT, module.VALUE_UNSET) == ("explicit", "unset")


# --- G3: the due date is kept only when a cited turn states that exact day ---------------


DUE_RULES = [
    pytest.param(
        UNIT_TURNS, "تا فردا", date(2026, 11, 13), [0], REFERENCE, "fa", True,
        id="allow-relative-day-from-the-reference",
    ),
    pytest.param(
        UNIT_TURNS, "تا فردا", date(2026, 11, 12), [0], REFERENCE, "fa", False,
        id="deny-relative-day-read-as-another-day",
    ),
    pytest.param(
        UNIT_TURNS, "تا فردا", date(2026, 11, 13), [0],
        datetime(2026, 11, 15, 8, 0, tzinfo=UTC), "fa", False,
        id="deny-relative-day-with-the-wrong-reference-day",
    ),
    pytest.param(
        UNIT_TURNS, "تا فردا", date(2026, 11, 13), [0],
        datetime(2026, 11, 12, 11, 30, tzinfo=TEHRAN), "fa", True,
        id="allow-reference-given-in-tehran-time",
    ),
    pytest.param(
        UNIT_TURNS, "تا فردا", date(2026, 11, 13), [0],
        datetime(2026, 11, 12, 21, 0, tzinfo=UTC), "fa", False,
        id="deny-utc-day-when-tehran-is-already-the-next-day",
    ),
    pytest.param(
        UNIT_TURNS, "تا فردا", date(2026, 11, 14), [0],
        datetime(2026, 11, 12, 21, 0, tzinfo=UTC), "fa", True,
        id="allow-tehran-day-of-the-reference",
    ),
    pytest.param(
        UNIT_TURNS, "پس‌فردا", date(2026, 11, 13), [1], REFERENCE, "fa", False,
        id="deny-quote-parses-to-another-date",
    ),
    pytest.param(
        UNIT_TURNS, "پس‌فردا", date(2026, 11, 14), [1], REFERENCE, "fa", True,
        id="allow-quote-parses-to-the-same-date",
    ),
    pytest.param(
        UNIT_TURNS, "۱۴۰۵/۰۹/۱۰", date(2026, 12, 1), [0, 1], REFERENCE, "fa", False,
        id="deny-due-quote-only-in-uncited-turn",
    ),
    pytest.param(
        UNIT_TURNS, "۱۴۰۵/۰۹/۱۰", date(2026, 12, 1), [0, 2], REFERENCE, "fa", True,
        id="allow-due-quote-turn-cited",
    ),
    pytest.param(
        UNIT_TURNS, "1405/09/10", date(2026, 12, 1), [2], REFERENCE, "fa", True,
        id="allow-latin-digit-quote-of-a-persian-digit-turn",
    ),
    pytest.param(
        UNIT_TURNS, "۳۰ آبان ۱۴۰۵", date(2026, 11, 21), [0, 1, 2], REFERENCE, "fa", False,
        id="deny-due-quote-invented",
    ),
    pytest.param(
        UNIT_TURNS, "تا فردا", None, [0], REFERENCE, "fa", False,
        id="deny-due-date-absent",
    ),
    pytest.param(
        UNIT_TURNS, None, date(2026, 11, 13), [0], REFERENCE, "fa", False,
        id="deny-due-quote-absent",
    ),
    pytest.param(
        UNIT_TURNS, "   ", date(2026, 11, 13), [0], REFERENCE, "fa", False,
        id="deny-due-quote-blank",
    ),
    pytest.param(
        UNIT_TURNS, ".", date(2026, 11, 13), [0], REFERENCE, "fa", False,
        id="deny-due-quote-only-punctuation",
    ),
    pytest.param(
        RANGE_TURNS, "هفته بعد", date(2026, 11, 14), [0], REFERENCE, "fa", False,
        id="deny-date-range",
    ),
    pytest.param(
        RANGE_TURNS, "شنبه آینده", date(2026, 11, 14), [0], REFERENCE, "fa", True,
        id="allow-one-day-in-the-same-turn",
    ),
    pytest.param(
        TIME_TURNS, "ساعت ده صبح", date(2026, 11, 12), [0], REFERENCE, "fa", False,
        id="deny-time-alone",
    ),
    pytest.param(
        TIME_TURNS, "امروز", date(2026, 11, 12), [0], REFERENCE, "fa", True,
        id="allow-day-in-the-same-turn",
    ),
    pytest.param(
        TIME_TURNS, "فردا ساعت ده صبح", date(2026, 11, 13), [0], REFERENCE, "fa", True,
        id="allow-day-with-a-time",
    ),
    pytest.param(
        WEEKDAY_TURNS, "دوشنبه", date(2026, 11, 16), [0], REFERENCE, "fa", False,
        id="deny-ambiguous-bare-weekday",
    ),
    pytest.param(
        WEEKDAY_TURNS, "دوشنبه بعد", date(2026, 11, 16), [0], REFERENCE, "fa", True,
        id="allow-weekday-with-next",
    ),
    pytest.param(
        DAY_MONTH_TURNS, "۲۵ آبان", date(2026, 11, 16), [0], REFERENCE, "fa", False,
        id="deny-ambiguous-day-and-month-without-year",
    ),
    pytest.param(
        DAY_MONTH_TURNS, "۲۵ آبان ۱۴۰۵", date(2026, 11, 16), [0], REFERENCE, "fa", True,
        id="allow-day-month-and-year",
    ),
    pytest.param(
        EN_TURNS, "2026-11-20", date(2026, 11, 20), [1], REFERENCE, "en", True,
        id="allow-iso-date",
    ),
    pytest.param(
        EN_TURNS, "2026-11-20", date(2026, 11, 21), [1], REFERENCE, "en", False,
        id="deny-iso-quote-of-another-date",
    ),
    pytest.param(
        EN_RELATIVE_TURNS, "tomorrow", date(2026, 11, 13), [0], REFERENCE, "en", False,
        id="deny-english-relative-day",
    ),
    pytest.param(
        TWO_DATE_TURNS, "فردا یا پس‌فردا", date(2026, 11, 13), [0], REFERENCE, "fa", False,
        id="deny-g3b-two-dates-given-the-first",
    ),
    pytest.param(
        TWO_DATE_TURNS, "فردا یا پس‌فردا", date(2026, 11, 14), [0], REFERENCE, "fa", False,
        id="deny-g3b-two-dates-given-the-second",
    ),
    pytest.param(
        TWO_DATE_TURNS, "فردا", date(2026, 11, 13), [1], REFERENCE, "fa", True,
        id="allow-g3b-one-date",
    ),
    pytest.param(
        SAME_DAY_TURNS, "فردا، یعنی ۱۴۰۵/۰۸/۲۲", date(2026, 11, 13), [0], REFERENCE, "fa", False,
        id="deny-g3b-two-date-matches-of-the-same-day",
    ),
    pytest.param(
        SAME_DAY_TURNS, "۱۴۰۵/۰۸/۲۲", date(2026, 11, 13), [0], REFERENCE, "fa", True,
        id="allow-g3b-one-of-those-dates-alone",
    ),
    pytest.param(
        DATE_OR_WEEKDAY_TURNS, "فردا یا دوشنبه", date(2026, 11, 13), [0], REFERENCE, "fa",
        False,
        id="deny-g3b-a-date-and-an-ambiguous-weekday",
    ),
    pytest.param(
        RANGE_TURNS, "هفته بعد، یعنی شنبه آینده", date(2026, 11, 14), [0], REFERENCE, "fa",
        True,
        id="allow-g3b-one-date-next-to-a-range",
    ),
]  # fmt: skip


@pytest.mark.parametrize(
    ("turns", "quote", "due", "indexes", "reference", "language", "kept"), DUE_RULES
)
def test_g3_the_due_date_is_kept_only_when_a_cited_turn_states_that_day(
    turns, quote, due, indexes, reference, language, kept
):
    grounded = _ground(
        _item(due_date=due, due_quote=quote, source_turn_indexes=indexes),
        _turns(*turns),
        reference=reference,
        language=language,
    )
    assert grounded is not None
    assert grounded.due_date == (due if kept else None)
    assert grounded.due_status == ("explicit" if kept else "unset")
    assert (grounded.owner, grounded.owner_status) == (None, "unset")


def test_g2_g3_owner_and_due_date_are_judged_apart():
    turns = _turns(*UNIT_TURNS)
    both = _ground(
        _item(
            owner="خانم رضایی",
            owner_quote=REZAEI_QUOTE,
            due_date=date(2026, 11, 13),
            due_quote="تا فردا",
            source_turn_indexes=[0],
        ),
        turns,
    )
    owner_only = _ground(
        _item(
            owner="خانم رضایی",
            owner_quote=REZAEI_QUOTE,
            due_date=date(2026, 11, 20),
            due_quote="تا فردا",
            source_turn_indexes=[0],
        ),
        turns,
    )
    due_only = _ground(
        _item(
            owner="آقای جعفری",
            owner_quote=REZAEI_QUOTE,
            due_date=date(2026, 11, 13),
            due_quote="تا فردا",
            source_turn_indexes=[0],
        ),
        turns,
    )
    assert (both.owner, both.due_date) == ("خانم رضایی", date(2026, 11, 13))
    assert (both.owner_status, both.due_status) == ("explicit", "explicit")
    assert (owner_only.owner, owner_only.due_date) == ("خانم رضایی", None)
    assert (owner_only.owner_status, owner_only.due_status) == ("explicit", "unset")
    assert (due_only.owner, due_only.due_date) == (None, date(2026, 11, 13))
    assert (due_only.owner_status, due_only.due_status) == ("unset", "explicit")


# --- G4: the kind is the provider's label; the name path and the date ignore it ---------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param({"kind": "commitment"}, "commitment", id="commitment"),
        pytest.param({"kind": "next_step"}, "next_step", id="next-step"),
        pytest.param({}, "next_step", id="unlabelled-is-never-a-commitment"),
    ],
)
def test_g4_the_kind_is_the_providers_label_and_does_not_change_owner_or_date(given, expected):
    grounded = _ground(
        _item(
            **given,
            owner="خانم رضایی",
            owner_quote=REZAEI_QUOTE,
            due_date=date(2026, 11, 13),
            due_quote="تا فردا",
            source_turn_indexes=[0],
        ),
        _turns(*UNIT_TURNS),
    )
    assert grounded.kind == expected
    assert isinstance(grounded.kind, str)
    assert (grounded.owner, grounded.due_date) == ("خانم رضایی", date(2026, 11, 13))
    assert (grounded.owner_status, grounded.due_status) == ("explicit", "explicit")


# --- G5: pure, no clock, no network, never raises ---------------------------------------


def test_g5_the_result_depends_only_on_its_inputs_and_leaves_them_unchanged(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("ground_action opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    item = _item(
        kind="commitment",
        owner="خانم رضایی",
        owner_quote=REZAEI_QUOTE,
        due_date=date(2026, 11, 13),
        due_quote="تا فردا",
        source_turn_indexes=[1, 0, 1],
    )
    turns = _turns(*UNIT_TURNS)
    item_before = item.model_dump()
    turns_before = [turn.model_dump() for turn in turns]

    first = _ground(item, turns)
    second = _ground(item, turns)

    assert first == second
    assert isinstance(first, _grounding().GroundedAction)
    assert item.model_dump() == item_before
    assert [turn.model_dump() for turn in turns] == turns_before
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.owner = "someone else"


def test_g5_relative_days_follow_the_reference_not_the_clock():
    turns = _turns(("SPEAKER_1", "من فردا گزارش رو می‌فرستم."))
    reference = datetime(2021, 3, 20, 8, 0, tzinfo=UTC)
    real_tomorrow = datetime.now(TEHRAN).date() + timedelta(days=1)
    from_reference = _ground(
        _item(due_date=date(2021, 3, 21), due_quote="فردا", source_turn_indexes=[0]),
        turns,
        reference=reference,
    )
    from_clock = _ground(
        _item(due_date=real_tomorrow, due_quote="فردا", source_turn_indexes=[0]),
        turns,
        reference=reference,
    )
    assert from_reference.due_date == date(2021, 3, 21)
    assert from_clock.due_date is None


NEVER_RAISES = [
    pytest.param({"source_turn_indexes": [10**9]}, id="huge-index"),
    pytest.param({"owner_quote": "🙂", "owner": "🙂", "source_turn_indexes": [0]}, id="emoji-only"),
    pytest.param(
        {"due_quote": "1405/13/40", "due_date": date(2026, 11, 13), "source_turn_indexes": [0]},
        id="impossible-jalali-date",
    ),
    pytest.param(
        {"due_quote": "9999-99-99", "due_date": date(9999, 12, 31), "source_turn_indexes": [0]},
        id="impossible-iso-date",
    ),
    pytest.param(
        {"owner": "ن" * 256, "owner_quote": "ن" * 500, "source_turn_indexes": [0]},
        id="longest-owner-and-quote",
    ),
    pytest.param(
        {"due_quote": "۳" * 200, "due_date": date(2026, 11, 13), "source_turn_indexes": [0]},
        id="longest-due-quote",
    ),
    pytest.param(
        {"owner": "speaker_unknown", "owner_quote": "?", "source_turn_indexes": [0, 0, 0]},
        id="unknown-speaker-label",
    ),
]


@pytest.mark.parametrize("fields", NEVER_RAISES)
@pytest.mark.parametrize("language", ["fa", "en"])
def test_g5_odd_but_valid_input_never_raises(fields, language):
    turns = _turns(("speaker_unknown", "؟؟؟ 🙂 1405/13/40"), ("SPEAKER_1", "x"))
    grounded = _ground(_item(**fields), turns, language=language)
    assert grounded is None or isinstance(grounded, _grounding().GroundedAction)


# --- D1: the provider schema -------------------------------------------------------------


def test_d1_the_provider_action_carries_kind_and_quotes():
    schemas = importlib.import_module("backend.providers.schemas")
    assert {member.value for member in schemas.ActionKind} == KINDS
    assert schemas.ActionKind("commitment") is schemas.ActionKind.COMMITMENT
    legacy = ProviderActionItem.model_validate(
        {"description": "بررسی قرارداد", "owner": None, "due_date": None, "priority": "low"}
    )
    assert legacy.kind is schemas.ActionKind.NEXT_STEP
    assert (legacy.owner_quote, legacy.due_quote) == (None, None)
    full = _item(kind="commitment", owner_quote="ن" * 500, due_quote="ن" * 200)
    assert full.kind is schemas.ActionKind.COMMITMENT
    assert (len(full.owner_quote), len(full.due_quote)) == (500, 200)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"kind": "other"}, id="unknown-kind"),
        pytest.param({"kind": "Commitment"}, id="kind-other-case"),
        pytest.param({"owner_quote": "ن" * 501}, id="owner-quote-too-long"),
        pytest.param({"due_quote": "ن" * 201}, id="due-quote-too-long"),
    ],
)
def test_d1_the_provider_action_refuses_an_unknown_kind_and_long_quotes(fields):
    with pytest.raises(ValidationError):
        _item(**fields)
    assert _item(kind="next_step", owner_quote="ن" * 500, due_quote="ن" * 200).kind == "next_step"


# --- D6: the OpenAI-compatible contract asks for kind and quotes ---------------------------


ENDPOINT = "https://llm.example.test/v1/chat/completions"
D6_REQUEST = IntelligenceRequest(
    transcript_text="Sarah will send the deck on 2026-11-20.",
    turns=[TranscriptTurn(text="Sarah will send the deck on 2026-11-20.", speaker_label="S1")],
    language="en",
    call_id="call-hom133",
)


def _d6_output(action: dict) -> dict:
    from backend.tests.test_provider_contracts import VALID_OUTPUT

    return {**VALID_OUTPUT, "action_items": [action]}


D6_ACTION = {
    "description": "Send the deck",
    "kind": "commitment",
    "owner": "Sarah",
    "owner_quote": "Sarah will send the deck",
    "due_date": "2026-11-20",
    "due_quote": "2026-11-20",
    "priority": "high",
    "source_turn_indexes": [0],
}


def _adapter(output: dict, sent: list[dict]) -> OpenAICompatibleIntelligenceProvider:
    def respond(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    return OpenAICompatibleIntelligenceProvider(
        base_url="https://llm.example.test/v1",
        model="contract-llm-hom133",
        api_key="hom133-test-key-never-sent",
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )


def test_d6_the_strict_schema_requires_kind_and_both_quotes_and_the_prompt_moves_to_5():
    sent: list[dict] = []
    adapter = _adapter(_d6_output(D6_ACTION), sent)
    with egress.permit(ENDPOINT):
        result = asyncio.run(adapter.analyze(D6_REQUEST))

    assert adapter.identity().params["prompt_version"] == 6  # 6: HOM-351
    (body,) = sent
    item = body["response_format"]["json_schema"]["schema"]["properties"]["action_items"]["items"]
    assert {"kind", "owner_quote", "due_quote"} <= set(item["required"])
    assert set(item["properties"]["kind"]["enum"]) == KINDS
    assert set(item["properties"]["owner_quote"]["type"]) == {"string", "null"}
    assert item["properties"]["owner_quote"]["maxLength"] == 500
    assert set(item["properties"]["due_quote"]["type"]) == {"string", "null"}
    assert item["properties"]["due_quote"]["maxLength"] == 200
    system = next(message["content"] for message in body["messages"] if message["role"] == "system")
    assert "owner_quote" in system and "due_quote" in system
    (action,) = result.action_items
    assert action.kind == "commitment"
    assert (action.owner_quote, action.due_quote) == ("Sarah will send the deck", "2026-11-20")


def test_d6_an_answer_with_an_unknown_kind_is_refused():
    with egress.permit(ENDPOINT):
        with pytest.raises(ProviderError) as refused:
            asyncio.run(_adapter_for({**D6_ACTION, "kind": "promise"}).analyze(D6_REQUEST))
        assert refused.value.code == "intelligence_invalid_response"
        kept = asyncio.run(_adapter_for({**D6_ACTION, "kind": "next_step"}).analyze(D6_REQUEST))
    assert kept.action_items[0].kind == "next_step"


def _adapter_for(action: dict) -> OpenAICompatibleIntelligenceProvider:
    return _adapter(_d6_output(action), [])


# --- D7: the evaluator ---------------------------------------------------------------------


METRICS = (
    "owner_precision",
    "due_precision",
    "owner_unset_rate",
    "due_unset_rate",
    "kind_accuracy",
    "keep_precision",
)


def test_d7_an_empty_input_measures_nothing_and_says_why():
    evaluation = importlib.import_module("backend.evaluation")
    report = evaluation.evaluate_action_items([])
    for name in METRICS:
        metric = report.metrics[name]
        assert metric.value is None, name
        assert metric.reason, name
    json.dumps(report.as_dict())


def test_d7_the_evaluator_scores_items_both_sides_keep():
    evaluation = importlib.import_module("backend.evaluation")
    case = evaluation.ActionItemCase
    items = [
        case(
            gold_keep=True, gold_owner="علی", gold_due=date(2026, 11, 13),
            gold_kind="commitment", predicted_keep=True, predicted_owner=f"عل{YA}",
            predicted_due=date(2026, 11, 13), predicted_kind="commitment", item_id="a",
        ),
        case(
            gold_keep=True, gold_owner="رضا", gold_due=date(2026, 11, 13),
            gold_kind="next_step", predicted_keep=True, predicted_owner="سارا",
            predicted_due=date(2026, 11, 14), predicted_kind="commitment", item_id="b",
        ),
        case(
            gold_keep=True, gold_owner=None, gold_due=None, gold_kind=None,
            predicted_keep=True, predicted_owner=None, predicted_due=None,
            predicted_kind="next_step", item_id="c",
        ),
        case(
            gold_keep=True, gold_owner=None, gold_due=None, gold_kind="next_step",
            predicted_keep=True, predicted_owner="مریم", predicted_due=date(2026, 11, 15),
            predicted_kind="next_step", item_id="d",
        ),
        case(
            gold_keep=False, gold_owner=None, gold_due=None, gold_kind=None,
            predicted_keep=True, predicted_owner="ایکس", predicted_due=date(2026, 11, 16),
            predicted_kind="commitment", item_id="e",
        ),
        case(
            gold_keep=True, gold_owner="نیما", gold_due=date(2026, 11, 17),
            gold_kind="commitment", predicted_keep=False, predicted_owner=None,
            predicted_due=None, predicted_kind=None, item_id="f",
        ),
    ]  # fmt: skip
    metrics = evaluation.evaluate_action_items(items).metrics
    expected = {
        "owner_precision": (1, 3),
        "due_precision": (1, 3),
        "owner_unset_rate": (1, 2),
        "due_unset_rate": (1, 2),
        "kind_accuracy": (2, 3),
        "keep_precision": (4, 5),
    }
    for name, (numerator, denominator) in expected.items():
        metric = metrics[name]
        assert (metric.numerator, metric.denominator) == (numerator, denominator), name
        assert metric.value == pytest.approx(numerator / denominator), name
        assert metric.reason is None, name


# --- F38 / AC7: the held-out set ---------------------------------------------------------


def _heldout() -> list[dict]:
    return [json.loads(line) for line in HELDOUT.read_text(encoding="utf-8").splitlines()]


def _scored(row: dict):
    grounded = _ground(
        ProviderActionItem.model_validate(row["provider_item"]),
        [TranscriptTurn.model_validate(turn) for turn in row["turns"]],
        reference=datetime.fromisoformat(row["reference"]),
        language=row["language"],
    )
    return grounded


def _case(row: dict, grounded):
    evaluation = importlib.import_module("backend.evaluation")
    gold = row["gold"]
    return evaluation.ActionItemCase(
        gold_keep=gold["keep"],
        gold_owner=gold["owner"],
        gold_due=date.fromisoformat(gold["due_date"]) if gold["due_date"] else None,
        gold_kind=gold["kind"],
        predicted_keep=grounded is not None,
        predicted_owner=grounded.owner if grounded is not None else None,
        predicted_due=grounded.due_date if grounded is not None else None,
        predicted_kind=grounded.kind if grounded is not None else None,
        item_id=row["id"],
        language=row["language"],
        slices={"language": row["language"]},
    )


def test_f38_the_heldout_file_is_built_as_designed():
    rows = _heldout()
    assert len(rows) >= 30
    assert len({row["id"] for row in rows}) == len(rows)
    assert sum(row["language"] == "fa" for row in rows) > len(rows) / 2
    for row in rows:
        assert set(row) == {"id", "language", "reference", "turns", "provider_item", "gold"}
        assert set(row["gold"]) == {"keep", "owner", "due_date", "kind"}
        assert datetime.fromisoformat(row["reference"]).tzinfo is not None
        TranscriptTurn.model_validate(row["turns"][0])


def test_f38_ac7_the_heldout_set_meets_the_precision_gate_and_leaves_missing_values_unset():
    evaluation = importlib.import_module("backend.evaluation")
    rows = _heldout()
    report = evaluation.evaluate_action_items([_case(row, _scored(row)) for row in rows])
    metrics = report.metrics
    for name in ("owner_precision", "due_precision"):
        assert metrics[name].value is not None, metrics[name]
        assert metrics[name].value >= 0.95, metrics[name]
    assert metrics["owner_unset_rate"].value == 1.0, metrics["owner_unset_rate"]
    assert metrics["due_unset_rate"].value == 1.0, metrics["due_unset_rate"]
    assert metrics["owner_precision"].denominator == 39
    assert metrics["due_precision"].denominator == 34
    assert metrics["owner_unset_rate"].denominator == 8
    assert metrics["due_unset_rate"].denominator == 11
    json.dumps(report.as_dict())


HELDOUT_EXPECTED = {
    "fa-001": ("explicit", "explicit"),
    "fa-002": ("explicit", "explicit"),
    "fa-003": ("explicit", "explicit"),
    "fa-004": ("explicit", "explicit"),
    "fa-005": ("explicit", "explicit"),
    "fa-006": ("explicit", "unset"),
    "fa-007": ("unset", "explicit"),
    "fa-008": ("explicit", "explicit"),
    "fa-009": ("explicit", "explicit"),
    "fa-010": ("explicit", "explicit"),
    "fa-011": ("unset", "unset"),
    "fa-012": ("explicit", "unset"),
    "fa-013": ("unset", "unset"),
    "fa-014": ("unset", "explicit"),
    "fa-015": ("explicit", "explicit"),
    "fa-016": ("explicit", "unset"),
    "fa-017": ("explicit", "explicit"),
    "fa-018": ("explicit", "unset"),
    "fa-019": ("explicit", "explicit"),
    "fa-020": None,
    "fa-021": ("explicit", "explicit"),
    "fa-022": None,
    "fa-023": ("explicit", "explicit"),
    "fa-024": ("explicit", "unset"),
    "fa-025": ("explicit", "explicit"),
    "fa-026": ("explicit", "unset"),
    "fa-027": ("explicit", "unset"),
    "fa-028": ("explicit", "unset"),
    "fa-029": ("explicit", "unset"),
    "fa-030": ("explicit", "unset"),
    "fa-031": ("explicit", "unset"),
    "fa-032": ("explicit", "explicit"),
    "fa-033": ("unset", "unset"),
    "fa-034": ("explicit", "unset"),
    "fa-035": ("unset", "explicit"),
    "fa-036": ("explicit", "explicit"),
    "fa-037": ("unset", "unset"),
    "fa-038": ("unset", "explicit"),
    "fa-039": ("unset", "explicit"),
    "fa-040": ("explicit", "explicit"),
    "fa-041": ("unset", "explicit"),
    "fa-042": ("unset", "explicit"),
    "fa-043": ("explicit", "explicit"),
    "fa-044": ("explicit", "unset"),
    "fa-045": ("explicit", "explicit"),
    "fa-046": ("unset", "explicit"),
    "fa-047": ("explicit", "explicit"),
    "en-001": ("explicit", "explicit"),
    "en-002": ("explicit", "explicit"),
    "en-003": ("explicit", "unset"),
    "en-004": ("explicit", "explicit"),
    "en-005": ("unset", "unset"),
    "en-006": ("explicit", "unset"),
    "en-007": ("explicit", "explicit"),
    "en-008": ("unset", "explicit"),
    "en-009": ("unset", "explicit"),
    "en-010": ("explicit", "explicit"),
}


def test_f38_every_heldout_case_has_an_expected_outcome():
    assert sorted(row["id"] for row in _heldout()) == sorted(HELDOUT_EXPECTED)


@pytest.mark.parametrize("case_id", sorted(HELDOUT_EXPECTED))
def test_f38_each_heldout_case_is_grounded_per_the_rules(case_id):
    row = next(row for row in _heldout() if row["id"] == case_id)
    grounded = _scored(row)
    expected = HELDOUT_EXPECTED[case_id]
    if expected is None:
        assert grounded is None
        return
    item = row["provider_item"]
    count = len(row["turns"])
    cited = tuple(dict.fromkeys(i for i in item["source_turn_indexes"] if 0 <= i < count))
    owner_status, due_status = expected
    assert grounded is not None
    assert grounded.turn_indexes == cited
    assert grounded.kind == item["kind"]
    assert (grounded.owner_status, grounded.due_status) == expected
    assert grounded.owner == (item["owner"] if owner_status == "explicit" else None)
    explicit_due = date.fromisoformat(item["due_date"]) if due_status == "explicit" else None
    assert grounded.due_date == explicit_due


# --- the processor: AC1, AC2, AC3, AC8 ---------------------------------------------------


INTEGRATION_TURNS = (
    "خانم رضایی، پیش‌فاکتور رو تا فردا برای مشتری می‌فرستید؟",
    "بله، من تا فردا پیش‌فاکتور رو می‌فرستم.",
    "آقای کریمی هم قرارداد رو تا ۱۴۰۵/۰۹/۱۰ بررسی می‌کنه.",
)
REZAEI = "خانم رضایی"
REZAEI_TURN_QUOTE = "خانم رضایی، پیش‌فاکتور رو تا فردا برای مشتری می‌فرستید"
KARIMI = "آقای کریمی"
STARTED_AT = datetime(2026, 1, 10, 8, 0, tzinfo=UTC)


class _ThreeTurnASR(ASRProvider):
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="hom133_three_turn_asr", model="v1")

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        return ASRResult(
            provider="hom133_three_turn_asr",
            model="v1",
            language="fa",
            text=" ".join(INTEGRATION_TURNS),
            segments=[
                ASRSegment(
                    index=index,
                    text=said,
                    start_seconds=index * 3.0,
                    end_seconds=index * 3.0 + 3.0,
                    confidence=0.9,
                )
                for index, said in enumerate(INTEGRATION_TURNS)
            ],
            duration_seconds=10,
            is_mock=True,
        )


class _Actions(MockIntelligenceProvider):
    def __init__(self, build, *, max_summary_chars: int = 500) -> None:
        super().__init__(allow_mock=True, max_summary_chars=max_summary_chars)
        self.build = build
        self.seen: list[TranscriptTurn] = []

    async def analyze(self, request: IntelligenceRequest):
        result = await super().analyze(request)
        self.seen = list(request.turns)
        return result.model_copy(update={"action_items": self.build(request)})


def _upload_meeting(client, workspace_id: str, monkeypatch) -> tuple[str, str]:
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=10.0),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
    response = _upload(client, workspace_id=workspace_id, key=f"hom133-{next(_keys):04d}")
    assert response.status_code == 202, response.text
    body = response.json()
    return body["meeting"]["id"], body["job"]["id"]


def _set_started_at(meeting_id: str, value: datetime) -> None:
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        meeting.started_at = value
        db.commit()


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _created_day(meeting_id: str) -> date:
    with inspection_session_factory()() as db:
        created = db.get(Meeting, meeting_id).created_at
    return _aware(created).astimezone(TEHRAN).date()


def _run(job_id: str, settings, intelligence) -> None:
    result = process_job(
        job_id,
        worker_id="hom133-oracle",
        settings=settings,
        providers=ProviderBundle(
            asr=_ThreeTurnASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=intelligence,
        ),
    )
    assert result["status"] == "completed", result


def _rows(meeting_id: str) -> dict[str, ActionItem]:
    with inspection_session_factory()() as db:
        rows = db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).all()
        return {row.description: row for row in rows}


def _spans(meeting_id: str) -> list[EvidenceSpan]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(EvidenceSpan).where(
                    EvidenceSpan.meeting_id == meeting_id,
                    EvidenceSpan.finding_type == "action_item",
                )
            )
        )


def _current_run(meeting_id: str) -> AnalysisRun:
    with inspection_session_factory()() as db:
        (run,) = db.scalars(
            select(AnalysisRun).where(
                AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(True)
            )
        ).all()
        return run


def _due(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _mixed_actions(created_day: date):
    def build(request: IntelligenceRequest) -> list[ProviderActionItem]:
        label = request.turns[1].speaker_label
        return [
            ProviderActionItem(
                description="A grounded owner and day",
                kind="commitment",
                owner=REZAEI,
                owner_quote=REZAEI_TURN_QUOTE,
                due_date=date(2026, 1, 11),
                due_quote="تا فردا",
                source_turn_indexes=[0, 1, 1],
            ),
            ProviderActionItem(
                description="B invented owner and day",
                kind="next_step",
                owner="آقای جعفری",
                owner_quote="آقای جعفری با بانک تماس می‌گیرد",
                due_date=date(2026, 1, 20),
                due_quote="۳۰ دی",
                source_turn_indexes=[2],
            ),
            ProviderActionItem(
                description="C quotes in a turn that is not cited",
                kind="commitment",
                owner=KARIMI,
                owner_quote="آقای کریمی هم قرارداد رو",
                due_date=date(2026, 12, 1),
                due_quote="۱۴۰۵/۰۹/۱۰",
                source_turn_indexes=[1],
            ),
            ProviderActionItem(
                description="C quotes in a cited turn",
                kind="commitment",
                owner=KARIMI,
                owner_quote="آقای کریمی هم قرارداد رو",
                due_date=date(2026, 12, 1),
                due_quote="۱۴۰۵/۰۹/۱۰",
                source_turn_indexes=[2],
            ),
            ProviderActionItem(
                description="D quote of another day",
                kind="commitment",
                owner=REZAEI,
                owner_quote=REZAEI_TURN_QUOTE,
                due_date=date(2026, 1, 12),
                due_quote="تا فردا",
                source_turn_indexes=[0, 1],
            ),
            ProviderActionItem(
                description="E first person",
                kind="commitment",
                owner=label,
                owner_quote="من تا فردا پیش‌فاکتور رو می‌فرستم",
                source_turn_indexes=[1],
            ),
            ProviderActionItem(
                description="E2 next step owned by a speaker label",
                kind="next_step",
                owner=label,
                owner_quote="من تا فردا پیش‌فاکتور رو می‌فرستم",
                source_turn_indexes=[1],
            ),
            ProviderActionItem(
                description="F every index out of range",
                kind="commitment",
                owner=REZAEI,
                owner_quote=REZAEI_TURN_QUOTE,
                due_date=date(2026, 1, 11),
                due_quote="تا فردا",
                source_turn_indexes=[3, 9],
            ),
            ProviderActionItem(
                description="G no evidence",
                kind="next_step",
                source_turn_indexes=[],
            ),
            ProviderActionItem(
                description="H day read from the upload day",
                kind="commitment",
                due_date=created_day + timedelta(days=1),
                due_quote="تا فردا",
                source_turn_indexes=[1],
            ),
        ]

    return build


def test_ac1_ac2_ac3_only_grounded_actions_and_values_are_stored_with_kind_run_and_evidence(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)
    _set_started_at(meeting_id, STARTED_AT)
    intelligence = _Actions(_mixed_actions(_created_day(meeting_id)))

    _run(job_id, settings, intelligence)

    assert [turn.text for turn in intelligence.seen] == list(INTEGRATION_TURNS)
    label = intelligence.seen[1].speaker_label
    rows = _rows(meeting_id)
    expected = {
        "A grounded owner and day": (REZAEI, _due(date(2026, 1, 11)), "commitment", 2),
        "B invented owner and day": (None, None, "next_step", 1),
        "C quotes in a turn that is not cited": (None, None, "commitment", 1),
        "C quotes in a cited turn": (KARIMI, _due(date(2026, 12, 1)), "commitment", 1),
        "D quote of another day": (REZAEI, None, "commitment", 2),
        "E first person": (label, None, "commitment", 1),
        "E2 next step owned by a speaker label": (None, None, "next_step", 1),
        "H day read from the upload day": (None, None, "commitment", 1),
    }
    assert set(rows) == set(expected)
    run = _current_run(meeting_id)
    spans = _spans(meeting_id)
    assert {span.finding_id for span in spans} == {row.id for row in rows.values()}
    for description, (owner, due_at, kind, span_count) in expected.items():
        row = rows[description]
        assert row.assignee_text == owner, description
        assert _aware(row.due_at) == due_at, description
        assert row.kind == kind, description
        assert row.kind in KINDS
        assert row.created_by == "ai"
        assert row.assignee_user_id is None
        assert row.analysis_run_id == run.id
        own = [span for span in spans if span.finding_id == row.id]
        assert len(own) == span_count, description
        assert {span.transcript_revision_id for span in own} == {run.transcript_revision_id}
    assert len(spans) == sum(count for *_, count in expected.values())


def _completed_audit(run_id: str) -> dict:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.completed")
        ).all()
        (details,) = [row.details for row in rows if row.details.get("analysis_run_id") == run_id]
        return details


def test_ac1_a_dropped_action_counts_its_rejected_references_but_writes_no_row_or_span(
    client, email_outbox, settings, monkeypatch
):
    def build(k_indexes: list[int], dropped: bool):
        def actions(_request):
            items = [
                ProviderActionItem(
                    description="A grounded owner and day",
                    kind="commitment",
                    owner=REZAEI,
                    owner_quote=REZAEI_TURN_QUOTE,
                    source_turn_indexes=[0, 1],
                ),
                ProviderActionItem(
                    description="K kept with its references",
                    kind="next_step",
                    source_turn_indexes=k_indexes,
                ),
            ]
            if dropped:
                items += [
                    ProviderActionItem(
                        description="F every index out of range",
                        kind="commitment",
                        owner=REZAEI,
                        owner_quote=REZAEI_TURN_QUOTE,
                        source_turn_indexes=[3, 9],
                    ),
                    ProviderActionItem(
                        description="G no evidence", kind="next_step", source_turn_indexes=[]
                    ),
                ]
            return items

        return actions

    _, workspace_id = _authenticated_workspace(client, email_outbox)
    _widen_concurrency(workspace_id)
    variants = {
        "all in range": (build([1], dropped=False), "sufficient"),
        "kept with one bad reference": (build([1, 8], dropped=False), "partial"),
        "plus dropped actions": (build([1, 8], dropped=True), "partial"),
    }
    runs = {}
    for name, (actions, k_status) in variants.items():
        meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)
        _run(job_id, settings, _Actions(actions))
        rows = _rows(meeting_id)
        assert set(rows) == {"A grounded owner and day", "K kept with its references"}, name
        assert rows["A grounded owner and day"].evidence_status == "sufficient", name
        assert rows["K kept with its references"].evidence_status == k_status, name
        spans = _spans(meeting_id)
        assert len(spans) == 3, name
        assert {span.finding_id for span in spans} == {row.id for row in rows.values()}, name
        run = _current_run(meeting_id)
        audit = _completed_audit(run.id)
        assert audit["rejected_references"] == run.rejected_reference_count, name
        runs[name] = (run, audit)

    valid = {
        name: run.reference_count - run.rejected_reference_count for name, (run, _) in runs.items()
    }
    assert len(set(valid.values())) == 1, valid
    rejected = {name: run.rejected_reference_count for name, (run, _) in runs.items()}
    assert rejected["kept with one bad reference"] - rejected["all in range"] == 1, rejected
    assert rejected["plus dropped actions"] - rejected["kept with one bad reference"] == 2, rejected
    evidence = {name: audit["evidence_count"] for name, (_, audit) in runs.items()}
    assert len(set(evidence.values())) == 1, evidence


def test_ac2_without_a_start_time_the_upload_day_anchors_relative_days(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)
    created_day = _created_day(meeting_id)

    def build(_request):
        return [
            ProviderActionItem(
                description="tomorrow of the upload day",
                kind="commitment",
                due_date=created_day + timedelta(days=1),
                due_quote="تا فردا",
                source_turn_indexes=[1],
            ),
            ProviderActionItem(
                description="the day after that",
                kind="commitment",
                due_date=created_day + timedelta(days=2),
                due_quote="تا فردا",
                source_turn_indexes=[1],
            ),
        ]

    _run(job_id, settings, _Actions(build))

    rows = _rows(meeting_id)
    assert _aware(rows["tomorrow of the upload day"].due_at) == _due(
        created_day + timedelta(days=1)
    )
    assert rows["the day after that"].due_at is None


def test_ac8_the_mock_provider_still_stores_no_action(client, email_outbox, settings, monkeypatch):
    request = IntelligenceRequest(transcript_text="پیگیری قرارداد انجام شود", language="fa")
    analysed = asyncio.run(MockIntelligenceProvider(allow_mock=True).analyze(request))
    assert analysed.action_items == []

    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)
    result = process_job(job_id, worker_id="hom133-mock", settings=settings, providers=_providers())
    assert result["status"] == "completed", result
    assert _rows(meeting_id) == {}
    assert _spans(meeting_id) == []


# --- AC4: the actions route returns kind --------------------------------------------------


def test_ac4_the_actions_route_returns_kind_and_requests_cannot_set_it(
    client, email_outbox, settings, monkeypatch
):
    def build(request):
        return [
            ProviderActionItem(
                description="ai commitment",
                kind="commitment",
                owner=REZAEI,
                owner_quote=REZAEI_TURN_QUOTE,
                source_turn_indexes=[0],
            ),
            ProviderActionItem(description="ai unlabelled", source_turn_indexes=[1]),
        ]

    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)
    _run(job_id, settings, _Actions(build))
    path = f"/api/v1/meetings/{meeting_id}/actions"
    headers = _write_headers(client, workspace_id)

    refused = client.post(
        path, headers=headers, json={"description": "user action", "kind": "commitment"}
    )
    assert refused.status_code == 422, refused.text
    created = client.post(path, headers=headers, json={"description": "user action"})
    assert created.status_code == 201, created.text
    assert "kind" in created.json() and created.json()["kind"] is None

    listed = client.get(path, headers={"X-Workspace-ID": workspace_id})
    assert listed.status_code == 200, listed.text
    kinds = {action["description"]: action["kind"] for action in listed.json()}
    assert kinds == {
        "ai commitment": "commitment",
        "ai unlabelled": "next_step",
        "user action": None,
    }

    ai_id = next(a["id"] for a in listed.json() if a["description"] == "ai commitment")
    patch_refused = client.patch(f"{path}/{ai_id}", headers=headers, json={"kind": "next_step"})
    assert patch_refused.status_code == 422, patch_refused.text
    patched = client.patch(f"{path}/{ai_id}", headers=headers, json={"priority": "high"})
    assert patched.status_code == 200, patched.text
    assert (patched.json()["priority"], patched.json()["kind"]) == ("high", "commitment")


# --- AC5: a voicemail re-analysis keeps a person's assignment (ADR 0054) -----------------


VOICEMAIL_TEXT = "رضا فردا با مشتری تماس می‌گیره"
VOICEMAIL_CORRECTED = "رضا فردا با مشتری جلسه می‌ذاره"
VOICEMAIL_ACTIONS = ("vm owner named", "vm no owner")


def _voicemail_actions(_request) -> list[ProviderActionItem]:
    return [
        ProviderActionItem(
            description=VOICEMAIL_ACTIONS[0],
            kind="commitment",
            owner="رضا",
            owner_quote="رضا فردا با مشتری",
            source_turn_indexes=[0],
        ),
        ProviderActionItem(
            description=VOICEMAIL_ACTIONS[1], kind="next_step", source_turn_indexes=[0]
        ),
    ]


def _voicemail_reanalysis_actions(request) -> list[ProviderActionItem]:
    # HOM-381 (ADR 0054 K2/K5): the re-analysis restates both texts but labels
    # the first one a next_step, so nothing matches the kept assigned
    # commitment and the kept row stays untouched.  The matching cases live in
    # test_voicemail_reanalysis_matches_kept_action.py.
    first, second = _voicemail_actions(request)
    return [
        ProviderActionItem(
            description=first.description,
            kind="next_step",
            owner=first.owner,
            owner_quote=first.owner_quote,
            source_turn_indexes=first.source_turn_indexes,
        ),
        second,
    ]


def _voicemail_bundle(*, reanalysis: bool = False) -> ProviderBundle:
    build = _voicemail_reanalysis_actions if reanalysis else _voicemail_actions
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript=VOICEMAIL_TEXT),
        diarization=SingleSpeakerDiarizationProvider(),
        # the different identity params also stop a job re-run from replaying
        # the first attempt's intelligence checkpoint
        intelligence=_Actions(build, max_summary_chars=499 if reanalysis else 500),
    )


@pytest.fixture
def voicemail_settings(settings):
    return settings.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
        }
    )


@pytest.fixture
def crew(client, voicemail_settings, monkeypatch, email_outbox):
    client.app.include_router(voicemail_router.router, prefix=voicemail_settings.api_prefix)
    monkeypatch.setattr(
        "backend.voicemail.ingest.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=42.3),
    )
    monkeypatch.setattr("backend.voicemail.ingest.dispatch_job", lambda job_id, _settings: True)
    monkeypatch.setattr(
        recompute, "create_provider_bundle", lambda _settings: _voicemail_bundle(reanalysis=True)
    )
    owner, people, _ = _workspace_with_roles(
        client, email_outbox, monkeypatch, roles={"member": "member"}
    )
    _widen_concurrency(owner.workspace_id)
    return owner, people["member"], voicemail_settings


def _process_voicemail(settings, job_id: str, *, reanalysis: bool = False) -> None:
    result = process_job(
        job_id,
        worker_id="hom133-voicemail",
        settings=settings,
        providers=_voicemail_bundle(reanalysis=reanalysis),
    )
    assert result["status"] == "completed", result


def _column_values(meeting_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(ActionItem).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(select(ActionItem).where(ActionItem.meeting_id == meeting_id)).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _evidence_of(action_id: str) -> dict[str, dict]:
    keys = [attribute.key for attribute in inspect(EvidenceSpan).column_attrs]
    with inspection_session_factory()() as db:
        rows = db.scalars(select(EvidenceSpan).where(EvidenceSpan.finding_id == action_id)).all()
        return {row.id: {key: getattr(row, key) for key in keys} for row in rows}


def _reanalyse(owner, member, settings, path: str, meeting_id: str) -> AnalysisRun:
    previous = _current_run(meeting_id)
    if path == "recompute":
        transcript = member.get(f"/api/v1/meetings/{meeting_id}/transcript")
        assert transcript.status_code == 200, transcript.text
        body = transcript.json()
        corrected = member.post(
            f"/api/v1/meetings/{meeting_id}/transcript/corrections",
            json={
                "base_revision_id": body["revision"]["id"],
                "reason": "fix the wording",
                "edits": [{"segment_id": body["segments"][0]["id"], "text": VOICEMAIL_CORRECTED}],
            },
        )
        assert corrected.status_code == 201, corrected.text
    else:
        with inspection_session_factory()() as db:
            job = db.scalar(
                select(ProcessingJob)
                .where(ProcessingJob.meeting_id == meeting_id)
                .order_by(ProcessingJob.created_at.desc())
            )
            assert job is not None and job.state == JobState.COMPLETED
            job.state = JobState.QUEUED
            job.stage = "queued"
            job.completed_at = None
            db.commit()
            job_id = job.id
        _process_voicemail(settings, job_id, reanalysis=True)
    run = _current_run(meeting_id)
    assert run.id != previous.id
    return run


@pytest.mark.parametrize("path", ["recompute", "process_job"])
def test_ac5_a_voicemail_reanalysis_keeps_a_person_assigned_ai_action_unchanged(crew, path):
    owner, member, settings = crew
    response = owner.post(
        "/api/v1/voicemails",
        data={
            "external_message_id": f"vm133-{path}",
            "caller_number": "0912 345 6789",
            "mailbox": "sales",
        },
        files={"file": ("vm.wav", b"voicemail wav bytes", "audio/wav")},
    )
    assert response.status_code == 202, response.text
    meeting_id = response.json()["voicemail"]["meeting_id"]
    _process_voicemail(settings, response.json()["job"]["id"])

    first = _column_values(meeting_id)
    by_description = {row["description"]: action_id for action_id, row in first.items()}
    assert set(by_description) == set(VOICEMAIL_ACTIONS)
    assigned = by_description[VOICEMAIL_ACTIONS[0]]
    unassigned = by_description[VOICEMAIL_ACTIONS[1]]
    assert first[assigned]["assignee_text"] == "رضا"
    assert first[assigned]["kind"] == "commitment"
    assert all(row["assignee_user_id"] is None for row in first.values())
    patched = owner.patch(
        f"/api/v1/meetings/{meeting_id}/actions/{assigned}",
        json={"assignee_user_id": member.user_id},
    )
    assert patched.status_code == 200, patched.text
    before = _column_values(meeting_id)[assigned]
    evidence = _evidence_of(assigned)
    assert before["assignee_user_id"] == member.user_id and evidence

    run = _reanalyse(owner, member, settings, path, meeting_id)

    after = _column_values(meeting_id)
    assert after.get(assigned) == before
    assert _evidence_of(assigned) == evidence
    assert unassigned not in after
    written = {
        action_id: row for action_id, row in after.items() if row["analysis_run_id"] == run.id
    }
    assert {row["description"] for row in written.values()} == set(VOICEMAIL_ACTIONS)
    for row in written.values():
        assert row["assignee_user_id"] is None
        assert row["created_by"] == "ai"
        assert row["kind"] in KINDS
    assert set(after) == {assigned, *written}


# --- AC6: the kind column, its check and its migration ------------------------------------


KIND_CHECK = "ck_action_kind"
REFUSED_KINDS = ("other", "Commitment", "NEXT_STEP", "next-step", "", "commitment ")
ACCEPTED_KINDS = ("commitment", "next_step", None)


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini")))


def _kind_revision():
    script = _script()
    heads = script.get_heads()
    assert len(heads) == 1, heads
    found = [
        revision
        for revision in script.walk_revisions()
        if re.fullmatch(r"[0-9a-f]{12}_action_item_kind\.py", Path(revision.path).name)
    ]
    assert len(found) == 1, f"no single *_action_item_kind.py revision: {found}"
    (revision,) = found
    assert isinstance(revision.down_revision, str)
    assert revision.revision in {r.revision for r in script.iterate_revisions(heads[0], "base")}
    return revision


def test_ac6_the_model_declares_a_nullable_kind_with_its_check():
    column = ActionItem.__table__.c["kind"]
    assert column.nullable is True
    assert column.type.length == 16
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in ActionItem.__table__.constraints
        if constraint.name == KIND_CHECK
    }
    assert set(checks) == {KIND_CHECK}
    assert "'commitment'" in checks[KIND_CHECK] and "'next_step'" in checks[KIND_CHECK]


def test_ac6_one_migration_adds_kind_and_the_chain_keeps_one_head():
    revision = _kind_revision()
    assert re.fullmatch(r"[0-9a-f]{12}", revision.revision)
    pins = {
        path.name: match.group(1)
        for path in (PROJECT_ROOT / "backend" / "tests").glob("test_*.py")
        if (match := re.search(r'^HEAD_REVISION = "([0-9a-f]{12})"', path.read_text("utf-8"), re.M))
    }
    assert pins
    assert set(pins.values()) == set(_script().get_heads()), pins


def test_ac6_the_live_schema_refuses_an_unknown_kind_and_takes_the_known_ones(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)
    _run(
        job_id,
        settings,
        _Actions(lambda _r: [ProviderActionItem(description="any", source_turn_indexes=[0])]),
    )
    (action_id,) = [row.id for row in _rows(meeting_id).values()]
    factory = inspection_session_factory()
    for value in REFUSED_KINDS:
        with factory() as db:
            db.get(ActionItem, action_id).kind = value
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
    for value in ACCEPTED_KINDS:
        with factory() as db:
            db.get(ActionItem, action_id).kind = value
            db.commit()
        with factory() as db:
            assert db.get(ActionItem, action_id).kind == value


def _shape(connection) -> dict:
    inspector = inspect(connection)
    return {
        "columns": {
            column["name"]: (str(column["type"]), column["nullable"])
            for column in inspector.get_columns("action_items")
        },
        "checks": sorted(
            check["name"] for check in inspector.get_check_constraints("action_items")
        ),
    }


LEGACY_ROW = (
    "INSERT INTO action_items (id, workspace_id, meeting_id, description, priority, status, "
    "created_by, evidence_status, created_at, updated_at) VALUES (:id, :workspace, :meeting, "
    "'legacy', 'medium', 'OPEN', 'ai', 'insufficient', :now, :now)"
)
KIND_ROW = (
    "INSERT INTO action_items (id, workspace_id, meeting_id, description, priority, status, "
    "created_by, evidence_status, created_at, updated_at, kind) VALUES (:id, :workspace, "
    ":meeting, 'new', 'medium', 'OPEN', 'ai', 'insufficient', :now, :now, :kind)"
)


def _migrate(connection, step: str) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        getattr(_kind_revision().module, step)()


def test_ac6_on_sqlite_the_migration_adds_kind_with_its_check_and_the_downgrade_removes_both(
    tmp_path,
):
    engine = create_engine(f"sqlite:///{tmp_path / 'hom133-kind.db'}")
    now = "2026-11-12 08:00:00.000000"
    ids = {"workspace": "w" * 36, "meeting": "m" * 36, "now": now}
    try:
        with engine.begin() as connection:
            Base.metadata.create_all(connection)
            head = _shape(connection)
        assert head["columns"]["kind"] == ("VARCHAR(16)", True)
        assert KIND_CHECK in head["checks"]

        with engine.begin() as connection:
            _migrate(connection, "downgrade")
            below = _shape(connection)
            connection.execute(text(LEGACY_ROW), {**ids, "id": "legacy-1"})
        assert "kind" not in below["columns"]
        assert below == {
            "columns": {k: v for k, v in head["columns"].items() if k != "kind"},
            "checks": [name for name in head["checks"] if name != KIND_CHECK],
        }

        with engine.begin() as connection:
            _migrate(connection, "upgrade")
            assert _shape(connection) == head
            legacy = connection.execute(
                text("SELECT kind FROM action_items WHERE id = 'legacy-1'")
            ).scalar_one()
            assert legacy is None
        for index, value in enumerate(REFUSED_KINDS):
            with pytest.raises(IntegrityError):
                with engine.begin() as connection:
                    connection.execute(
                        text(KIND_ROW), {**ids, "id": f"refused-{index}", "kind": value}
                    )
        for index, value in enumerate(ACCEPTED_KINDS):
            with engine.begin() as connection:
                connection.execute(text(KIND_ROW), {**ids, "id": f"taken-{index}", "kind": value})

        with engine.begin() as connection:
            _migrate(connection, "downgrade")
            assert _shape(connection) == below
            count = connection.execute(text("SELECT count(*) FROM action_items")).scalar_one()
            assert count == 1 + len(ACCEPTED_KINDS)
    finally:
        engine.dispose()


@pytest.mark.postgres
def test_ac6_on_postgres_the_migration_adds_kind_with_its_check_and_the_downgrade_removes_both(
    client, email_outbox, settings, monkeypatch
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("needs PostgreSQL")
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, _ = _upload_meeting(client, workspace_id, monkeypatch)
    ids = {"workspace": workspace_id, "meeting": meeting_id, "now": datetime.now(UTC)}
    column_sql = text(
        "SELECT is_nullable, data_type, character_maximum_length FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'action_items' AND column_name = 'kind'"
    )
    check_sql = text(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = :name AND conrelid = 'public.action_items'::regclass"
    )
    engine = create_engine(configured_database_url())
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                assert connection.execute(column_sql).one() == ("YES", "character varying", 16)
                _migrate(connection, "downgrade")
                assert connection.execute(column_sql).first() is None
                assert connection.execute(check_sql, {"name": KIND_CHECK}).first() is None
                connection.execute(text(LEGACY_ROW), {**ids, "id": "legacy-pg-1"})

                _migrate(connection, "upgrade")
                assert connection.execute(column_sql).one() == ("YES", "character varying", 16)
                definition = connection.execute(check_sql, {"name": KIND_CHECK}).scalar_one()
                assert "commitment" in definition and "next_step" in definition
                legacy = connection.execute(
                    text("SELECT kind FROM action_items WHERE id = 'legacy-pg-1'")
                ).scalar_one()
                assert legacy is None
                for index, value in enumerate(REFUSED_KINDS):
                    savepoint = connection.begin_nested()
                    with pytest.raises(IntegrityError):
                        connection.execute(
                            text(KIND_ROW), {**ids, "id": f"refused-pg-{index}", "kind": value}
                        )
                    savepoint.rollback()
                for index, value in enumerate(ACCEPTED_KINDS):
                    connection.execute(
                        text(KIND_ROW), {**ids, "id": f"taken-pg-{index}", "kind": value}
                    )

                _migrate(connection, "downgrade")
                assert connection.execute(column_sql).first() is None
                assert connection.execute(check_sql, {"name": KIND_CHECK}).first() is None
                kept = connection.execute(
                    text("SELECT count(*) FROM action_items WHERE meeting_id = :meeting"),
                    {"meeting": meeting_id},
                ).scalar_one()
                assert kept == 1 + len(ACCEPTED_KINDS)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
