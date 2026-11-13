"""HOM-351 Q2: the model gets a calendar of the call day and the 13 days after it.

The calendar is built by code from the request's reference date (the same anchor
the HOM-133 grounding uses), in Asia/Tehran local days, with the Jalali date and
the Persian weekday name of each day.  A request without an anchor sends exactly
the payload it sent before.  The processor passes the grounding anchor and puts
the call day into the analysis checkpoint identity.  A recompute after a transcript
correction sends the same anchor (review ruling R1).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select

from backend.database.saas_models import Meeting, StageCheckpoint
from backend.jobs import recompute
from backend.providers import egress
from backend.providers.openai_compatible import (
    SYSTEM_PROMPT,
    OpenAICompatibleIntelligenceProvider,
)
from backend.providers.schemas import IntelligenceRequest, TranscriptTurn
from backend.speech.dates import TEHRAN, WEEKDAY_NAMES_FA, format_jalali
from backend.tests._database import inspection_session_factory
from backend.tests.test_analysis_recompute import (
    CORRECTED_TEXT,
    _correct,
    _current_run,
    _processed_meeting,
)
from backend.tests.test_analysis_recompute import _bundle as _recompute_bundle
from backend.tests.test_hom133_action_grounding_oracle import (
    STARTED_AT,
    _Actions,
    _authenticated_workspace,
    _aware,
    _run,
    _set_started_at,
    _upload_meeting,
)
from backend.tests.test_intelligence_output_bounds import (
    LLM_BASE,
    _Scripted,
    _valid_output,
)

TURNS = [TranscriptTurn(text="پیش‌فاکتور رو تا فردا می‌فرستم", speaker_label="speaker_1")]
SATURDAY_MIDNIGHT = datetime(2026, 11, 13, 20, 30, 0, tzinfo=UTC)  # 2026-11-14 00:00 Tehran
FRIDAY_LAST_SECOND = datetime(2026, 11, 13, 20, 29, 59, tzinfo=UTC)  # 2026-11-13 23:59:59
SATURDAY_FIRST = ["شنبه", "یکشنبه", "دوشنبه", "سه شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


def _adapter() -> OpenAICompatibleIntelligenceProvider:
    return OpenAICompatibleIntelligenceProvider(base_url="https://llm.example.test/v1", model="m")


def _user(request: IntelligenceRequest) -> dict:
    payload = _adapter()._payload(request)
    return json.loads(payload["messages"][1]["content"])


def _request(reference_at: datetime | None = None, **fields) -> IntelligenceRequest:
    return IntelligenceRequest(
        transcript_text="x", turns=TURNS, language="fa", reference_at=reference_at, **fields
    )


def test_a_tehran_midnight_anchor_starts_on_saturday_with_fourteen_days():
    calendar = _user(_request(SATURDAY_MIDNIGHT))["calendar"]

    assert calendar[0] == {
        "date": "2026-11-14",
        "jalali": "1405/08/23",
        "weekday": "شنبه",
        "call_day": True,
    }
    assert len(calendar) == 14
    first = date(2026, 11, 14)
    for offset, entry in enumerate(calendar):
        day = first + timedelta(days=offset)
        expected = {
            "date": day.isoformat(),
            "jalali": format_jalali(day),
            "weekday": SATURDAY_FIRST[offset % 7],
        }
        if offset == 0:
            expected["call_day"] = True
        assert entry == expected, offset


def test_one_second_earlier_is_still_friday_in_tehran():
    calendar = _user(_request(FRIDAY_LAST_SECOND))["calendar"]
    assert calendar[0] == {
        "date": "2026-11-13",
        "jalali": "1405/08/22",
        "weekday": "جمعه",
        "call_day": True,
    }
    assert calendar[1]["weekday"] == "شنبه"


def test_a_naive_anchor_is_read_as_utc():
    naive = datetime(2026, 11, 13, 20, 30, 0)
    request = _request(naive)
    assert request.reference_at == naive  # accepted as given, never refused
    assert _user(request)["calendar"] == _user(_request(SATURDAY_MIDNIGHT))["calendar"]


def test_an_anchor_in_another_zone_gives_the_same_tehran_day():
    tehran_offset = timezone(timedelta(hours=3, minutes=30))
    local = datetime(2026, 11, 14, 0, 0, 0, tzinfo=tehran_offset)
    assert _user(_request(local))["calendar"] == _user(_request(SATURDAY_MIDNIGHT))["calendar"]


def test_the_weekday_names_are_the_ones_the_date_parser_reads():
    calendar = _user(_request(SATURDAY_MIDNIGHT))["calendar"]
    for entry in calendar:
        day = date.fromisoformat(entry["date"])
        assert entry["weekday"] == WEEKDAY_NAMES_FA[day.weekday()]


def test_the_calendar_crosses_the_jalali_new_year():
    # 2027-03-20 12:00 Tehran is 1405/12/29; the next day is 1406/01/01.
    calendar = _user(_request(datetime(2027, 3, 20, 8, 30, tzinfo=UTC)))["calendar"]
    assert [entry["jalali"] for entry in calendar[:3]] == ["1405/12/29", "1406/01/01", "1406/01/02"]


def test_without_an_anchor_there_is_no_calendar_and_the_payload_is_unchanged():
    request = _request(None)
    content = _adapter()._payload(request)["messages"][1]["content"]

    legacy = json.dumps(
        {
            "language": request.language,
            "transcript_text": request.transcript_text,
            "turns": [turn.model_dump(mode="json") for turn in request.turns],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert content == legacy
    assert "calendar" not in json.loads(content)


def test_with_an_anchor_only_the_calendar_is_added():
    without = _user(_request(None))
    with_anchor = _user(_request(SATURDAY_MIDNIGHT))
    assert set(with_anchor) - set(without) == {"calendar"}
    assert {key: with_anchor[key] for key in without} == without


def test_the_anchor_is_never_sent_as_a_field_of_its_own():
    content = _adapter()._payload(_request(SATURDAY_MIDNIGHT))["messages"][1]["content"]
    assert "reference_at" not in content
    assert "20:30" not in content


def test_the_prompt_says_to_resolve_days_only_with_the_calendar():
    for words in (
        "The 'calendar' array, when present, lists the day of the call",
        "The Persian week starts on Saturday (شنبه)",
        "so یکشنبه is Sunday and دوشنبه is Monday",
        "When the calendar is present, resolve a relative day such as today, tomorrow or the "
        "day after tomorrow, and a weekday name, only with this calendar",
        "a weekday name means the first entry after the call day that has that weekday",
        "These date rules also apply to the due_date of a decision",
        "When the calendar is present, use a date outside it only when a speaker states "
        "that full date",
        "otherwise set due_date to null and never guess a date",
    ):
        assert words in SYSTEM_PROMPT, words


def test_the_corrective_retry_sends_the_same_calendar():
    # F3 (HOM-351): a bound violation is retried once with the first request's messages.
    bad = _valid_output()
    bad["insights"][0]["confidence"] = 1.4
    transport = _Scripted([bad, _valid_output()])
    adapter = OpenAICompatibleIntelligenceProvider(
        base_url=LLM_BASE,
        model="calendar-retry-llm",
        client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
    )
    request = _request(SATURDAY_MIDNIGHT)

    with egress.permit(f"{LLM_BASE}/chat/completions"):
        asyncio.run(adapter.analyze(request))

    first, second = transport.requests
    assert second["messages"][:2] == first["messages"]
    assert json.loads(second["messages"][1]["content"])["calendar"] == _user(request)["calendar"]


# --- the processor ------------------------------------------------------------------


def _intelligence_manifest(meeting_id: str) -> dict:
    with inspection_session_factory()() as db:
        (row,) = db.scalars(
            select(StageCheckpoint).where(
                StageCheckpoint.meeting_id == meeting_id,
                StageCheckpoint.stage == "intelligence",
            )
        ).all()
        return row.input_manifest


class _Recording(_Actions):
    """The HOM-133 oracle's provider double; it also keeps the request it got."""

    def __init__(self) -> None:
        super().__init__(lambda _request: [])
        self.request: IntelligenceRequest | None = None

    async def analyze(self, request: IntelligenceRequest):
        self.request = request
        return await super().analyze(request)


@pytest.mark.parametrize("started", [True, False])
def test_the_processor_sends_the_grounding_anchor_and_names_the_call_day_in_the_identity(
    client, email_outbox, settings, monkeypatch, started
):
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    meeting_id, job_id = _upload_meeting(client, workspace_id, monkeypatch)
    if started:
        _set_started_at(meeting_id, STARTED_AT)
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        expected = _aware(meeting.started_at or meeting.created_at)
    intelligence = _Recording()

    _run(job_id, settings, intelligence)

    sent = intelligence.request
    assert sent is not None and sent.reference_at is not None
    assert sent.reference_at.tzinfo is not None
    assert sent.reference_at == expected
    call_day = expected.astimezone(TEHRAN).date().isoformat()
    assert _intelligence_manifest(meeting_id)["request"]["call_day"] == call_day


# --- R1: the recompute after a correction sends the same anchor ----------------------

_MOCK_PROVIDERS = {
    "allow_mock_providers": True,
    "asr_provider": "mock",
    "diarization_provider": "mock",
    "intelligence_provider": "mock",
}


def _recomputed_meeting(client, email_outbox, settings, monkeypatch, intelligence, *, started=None):
    """Process a meeting with ``intelligence``, correct its transcript and let the
    correction's recompute (HOM-63) call the same provider double.  ``started`` is
    set as the meeting's start time between the two analyses."""
    for name, value in _MOCK_PROVIDERS.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(
        recompute, "create_provider_bundle", lambda _settings: _recompute_bundle(intelligence)
    )
    _, workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch, intelligence=intelligence
    )
    if started is not None:
        _set_started_at(meeting_id, started)
    corrected = _correct(client, workspace_id, meeting_id)
    run = _current_run(meeting_id)
    assert run.transcript_revision_id == corrected["id"] and run.job_id is None
    return meeting_id, workspace_id


@pytest.mark.parametrize("started", [STARTED_AT, None])
def test_r1_a_recompute_sends_the_grounding_anchor(
    client, email_outbox, settings, monkeypatch, started
):
    intelligence = _Recording()

    meeting_id, _ = _recomputed_meeting(
        client, email_outbox, settings, monkeypatch, intelligence, started=started
    )

    sent = intelligence.request  # the last request: the recompute's
    assert sent is not None
    assert [turn.text for turn in sent.turns] == [CORRECTED_TEXT]
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        expected = _aware(meeting.started_at or meeting.created_at)
    if started is not None:
        assert expected == STARTED_AT
    assert sent.reference_at == expected
    assert sent.reference_at.tzinfo is not None
