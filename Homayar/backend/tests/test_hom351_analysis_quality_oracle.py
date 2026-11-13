"""HOM-351 phase 2 team p2-t3 oracle: output language, call calendar, duplicate merge."""

from __future__ import annotations

import copy
import dataclasses
import importlib
import inspect
import json
import pkgutil
import re
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, get_args
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

import backend.services as services_package
from backend.database.saas_models import ActionItem as ActionRow
from backend.database.saas_models import Meeting, MeetingSummary, StageCheckpoint
from backend.jobs import checkpoints as ckpt
from backend.jobs import outbox, processor
from backend.jobs.processor import ClaimedJob, _grounding_reference, process_job
from backend.providers import openai_compatible
from backend.providers.base import (
    ASRProvider,
    IntelligenceProvider,
    ProviderIdentity,
    text_digest,
)
from backend.providers.factory import ProviderBundle
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.schemas import ActionItem as ProviderActionItem
from backend.providers.schemas import (
    ActionKind,
    ActionPriority,
    ASRRequest,
    ASRResult,
    ASRSegment,
    ClaimBasis,
    ClaimStatus,
    Decision,
    IntelligenceInsight,
    IntelligenceRequest,
    IntelligenceResult,
    KeyMoment,
    SummaryField,
    SummaryFields,
    TranscriptTurn,
)
from backend.services.analysis_claims import SECTION_DECISION, SECTION_KEY_MOMENT
from backend.speech.dates import WEEKDAY_NAMES_FA, extract_temporal, format_jalali
from backend.speech.text_normalize import normalize_for_matching
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_uploads_api import _authenticated_workspace, _upload, _valid_metadata

TEHRAN = ZoneInfo("Asia/Tehran")
BASE_PROMPT_DIGEST = "62a82190ec4d5ad6b9322d211d47ef44"
BASE_REQUEST_FIELDS = frozenset({"transcript_text", "turns", "language", "call_id"})
DAY_KEYS = frozenset({"date", "jalali", "weekday"})
TURNS = (
    TranscriptTurn(text="سلام، فردا فاکتور اصلاح‌شده را می‌فرستم.", speaker_label="speaker_1"),
    TranscriptTurn(text="ممنون، تا سه‌شنبه آینده منتظر می‌مانم.", speaker_label="speaker_2"),
)


def _request(language: str = "fa", **extra: Any) -> IntelligenceRequest:
    return IntelligenceRequest(
        transcript_text=" ".join(turn.text for turn in TURNS),
        turns=list(TURNS),
        language=language,
        call_id="hom351-t3-oracle",
        **extra,
    )


def _adapter() -> OpenAICompatibleIntelligenceProvider:
    return OpenAICompatibleIntelligenceProvider(
        base_url="https://llm.example.test/v1", model="oracle-llm-2026-11"
    )


def _messages(payload: dict[str, Any]) -> dict[str, str]:
    return {message["role"]: message["content"] for message in payload["messages"]}


def _user_json(request: IntelligenceRequest) -> dict[str, Any]:
    return json.loads(_messages(_adapter()._payload(request))["user"])


def _base_user_content(request: IntelligenceRequest) -> str:
    transcript = {
        "language": request.language,
        "transcript_text": request.transcript_text,
        "turns": [turn.model_dump(mode="json") for turn in request.turns],
    }
    return json.dumps(transcript, ensure_ascii=False, separators=(",", ":"))


def _mentions_datetime(annotation: Any) -> bool:
    if isinstance(annotation, type) and issubclass(annotation, datetime):
        return True
    if "datetime" in str(annotation).lower():
        return True
    return any(_mentions_datetime(argument) for argument in get_args(annotation))


def _anchor_field() -> str:
    found = [
        name
        for name, info in IntelligenceRequest.model_fields.items()
        if name not in BASE_REQUEST_FIELDS and _mentions_datetime(info.annotation)
    ]
    if len(found) != 1:
        pytest.fail(
            "IntelligenceRequest must gain exactly one optional datetime reference anchor "
            f"field (Q2); new datetime fields found: {found}"
        )
    return found[0]


def _anchored(anchor: datetime | None, language: str = "fa") -> IntelligenceRequest:
    return _request(language, **{_anchor_field(): anchor})


def _calendar_days(user: dict[str, Any]) -> list[dict[str, Any]]:
    assert "calendar" in user, f"the user JSON has no calendar key: {sorted(user)}"
    calendar = user["calendar"]

    def is_days(value: Any) -> bool:
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(entry, dict) and DAY_KEYS <= set(entry) for entry in value)
        )

    if is_days(calendar):
        return calendar
    assert isinstance(calendar, dict), f"calendar is neither an object nor a day list: {calendar!r}"
    lists = [value for value in calendar.values() if is_days(value)]
    assert len(lists) == 1, f"calendar must hold one list of day entries: {calendar!r}"
    return lists[0]


def _expected_day(first: date, offset: int) -> dict[str, Any]:
    day = first + timedelta(days=offset)
    entry: dict[str, Any] = {
        "date": day.isoformat(),
        "jalali": format_jalali(day),
        "weekday": WEEKDAY_NAMES_FA[day.weekday()],
    }
    if offset == 0:
        entry["call_day"] = True
    return entry


def _assert_fourteen_days_from(days: list[dict[str, Any]], first: date) -> None:
    assert len(days) == 14, [entry.get("date") for entry in days]
    for offset, entry in enumerate(days):
        assert entry == _expected_day(first, offset), (offset, entry)


def _calendar_for(anchor: datetime) -> list[dict[str, Any]]:
    return _calendar_days(_user_json(_anchored(anchor)))


def _sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[.!?;])\s+", text) if part.strip()]


def test_q1_prompt_version_is_6():
    assert openai_compatible.PROMPT_VERSION == 6


def test_q1_prompt_text_changed_from_the_base():
    assert text_digest(openai_compatible.SYSTEM_PROMPT) != BASE_PROMPT_DIGEST


def test_q1_prompt_has_a_rule_for_the_output_language():
    sentences = [
        sentence
        for sentence in _sentences(openai_compatible.SYSTEM_PROMPT)
        if re.search(r"\blanguage\b", sentence, re.IGNORECASE)
    ]
    assert sentences, "SYSTEM_PROMPT names no output language rule"
    assert any(
        re.search(r"summary|free[- ]text|\bwrit(e|ten)\b", sentence, re.IGNORECASE)
        for sentence in sentences
    ), sentences


def test_q1_control_quote_fields_are_still_copied_exactly():
    prompt = openai_compatible.SYSTEM_PROMPT
    assert "owner_quote" in prompt and "due_quote" in prompt
    assert re.search(r"\bexact(ly)?\b", prompt)
    assert re.search(r"entity's text is copied exactly|entity text[^.]*exact", prompt)


@pytest.mark.parametrize("language", ["fa", "en", "unknown"])
def test_q1_control_payload_without_anchor_is_byte_identical_to_the_base_shape(language):
    request = _request(language)
    payload = _adapter()._payload(request)
    assert [message["role"] for message in payload["messages"]] == ["system", "user"]
    messages = _messages(payload)
    assert messages["system"] == openai_compatible.SYSTEM_PROMPT
    assert messages["user"] == _base_user_content(request)
    assert "calendar" not in json.loads(messages["user"])


def test_q2_request_has_one_optional_anchor_that_defaults_to_none():
    name = _anchor_field()
    assert IntelligenceRequest.model_fields[name].is_required() is False
    assert getattr(_request(), name) is None


def test_q2_explicit_none_anchor_gives_the_base_payload():
    request = _anchored(None)
    assert _messages(_adapter()._payload(request))["user"] == _base_user_content(request)


def test_q2_saturday_tehran_midnight_starts_the_calendar_on_saturday():
    days = _calendar_for(datetime(2026, 11, 13, 20, 30, 0, tzinfo=UTC))
    assert days[0] == {
        "date": "2026-11-14",
        "jalali": "1405/08/23",
        "weekday": "شنبه",
        "call_day": True,
    }
    assert days[1] == {"date": "2026-11-15", "jalali": "1405/08/24", "weekday": "یکشنبه"}
    assert (days[3]["date"], days[3]["weekday"]) == ("2026-11-17", "سه شنبه")
    assert (days[5]["date"], days[5]["weekday"]) == ("2026-11-19", "پنجشنبه")
    assert days[13] == {"date": "2026-11-27", "jalali": "1405/09/06", "weekday": "جمعه"}
    _assert_fourteen_days_from(days, date(2026, 11, 14))


def test_q2_one_second_before_tehran_midnight_is_still_friday():
    days = _calendar_for(datetime(2026, 11, 13, 20, 29, 59, tzinfo=UTC))
    assert days[0] == {
        "date": "2026-11-13",
        "jalali": "1405/08/22",
        "weekday": "جمعه",
        "call_day": True,
    }
    _assert_fourteen_days_from(days, date(2026, 11, 13))


def test_c5_only_the_first_entry_is_the_call_day():
    days = _calendar_for(datetime(2026, 11, 14, 6, 30, tzinfo=UTC))
    assert [entry.get("call_day") for entry in days] == [True] + [None] * 13
    assert all(set(entry) == DAY_KEYS for entry in days[1:])


def test_c5_weekday_names_are_the_ones_the_grounding_parser_reads():
    anchor = datetime(2026, 11, 13, 20, 30, tzinfo=UTC)
    for entry in _calendar_for(anchor)[:7]:
        matches = [
            match for match in extract_temporal(entry["weekday"], anchor) if match.kind == "date"
        ]
        assert len(matches) == 1, (entry, matches)
        assert date.fromisoformat(entry["date"]) in matches[0].candidates, (entry, matches)


@pytest.mark.parametrize(
    ("anchor", "first"),
    [
        (datetime(2026, 11, 14, 0, 0, tzinfo=TEHRAN), date(2026, 11, 14)),
        (
            datetime(2026, 11, 14, 0, 0, tzinfo=timezone(timedelta(hours=3, minutes=30))),
            date(2026, 11, 14),
        ),
        (
            datetime(2026, 11, 13, 23, 59, 59, tzinfo=timezone(timedelta(hours=3, minutes=30))),
            date(2026, 11, 13),
        ),
        (datetime(2026, 11, 14, 8, 0, tzinfo=timezone(timedelta(hours=12))), date(2026, 11, 13)),
        (datetime(2026, 11, 13, 15, 30, tzinfo=timezone(timedelta(hours=-5))), date(2026, 11, 14)),
        (datetime(2026, 11, 13, 20, 30, tzinfo=ZoneInfo("UTC")), date(2026, 11, 14)),
    ],
    ids=[
        "tehran-zone",
        "plus-0330",
        "plus-0330-before-midnight",
        "plus-12",
        "minus-05",
        "zoneinfo-utc",
    ],
)
def test_q2_any_offset_is_read_as_its_tehran_local_day(anchor, first):
    _assert_fourteen_days_from(_calendar_for(anchor), first)


@pytest.mark.parametrize(
    ("anchor", "first"),
    [
        (datetime(2026, 11, 13, 20, 30, 0), date(2026, 11, 14)),
        (datetime(2026, 11, 13, 20, 29, 59), date(2026, 11, 13)),
    ],
    ids=["naive-2030", "naive-202959"],
)
def test_q2_a_naive_anchor_is_read_as_utc(anchor, first):
    _assert_fourteen_days_from(_calendar_for(anchor), first)


@pytest.mark.parametrize(
    ("anchor", "first", "jalali"),
    [
        (datetime(2026, 6, 20, 19, 45, tzinfo=UTC), date(2026, 6, 20), "1405/03/30"),
        (datetime(2026, 6, 20, 20, 30, tzinfo=UTC), date(2026, 6, 21), "1405/03/31"),
    ],
    ids=["summer-2315-tehran", "summer-0000-tehran"],
)
def test_q2_tehran_has_no_summer_time(anchor, first, jalali):
    days = _calendar_for(anchor)
    assert days[0]["jalali"] == jalali
    _assert_fourteen_days_from(days, first)


def test_q2_calendar_crosses_the_jalali_new_year_and_ignores_the_clock():
    days = _calendar_for(datetime(2024, 3, 15, 12, 0, tzinfo=UTC))
    assert (days[0]["date"], days[0]["jalali"]) == ("2024-03-15", "1402/12/25")
    assert days[5] == {"date": "2024-03-20", "jalali": "1403/01/01", "weekday": "چهارشنبه"}
    _assert_fourteen_days_from(days, date(2024, 3, 15))


def test_q2_anchor_adds_only_the_calendar_to_the_user_json():
    plain = json.loads(_base_user_content(_request()))
    user = _user_json(_anchored(datetime(2026, 11, 13, 20, 30, tzinfo=UTC)))
    assert set(user) == set(plain) | {"calendar"}
    assert {key: value for key, value in user.items() if key != "calendar"} == plain


def test_q2_calendar_is_deterministic_for_the_same_instant():
    utc = _user_json(_anchored(datetime(2026, 11, 13, 20, 30, tzinfo=UTC)))
    tehran = _user_json(_anchored(datetime(2026, 11, 14, 0, 0, tzinfo=TEHRAN)))
    again = _user_json(_anchored(datetime(2026, 11, 13, 20, 30, tzinfo=UTC)))
    assert utc == again
    assert _calendar_days(utc) == _calendar_days(tehran)


def test_q2_control_request_without_anchor_has_no_calendar():
    assert "calendar" not in _user_json(_request())


def test_q2_prompt_tells_the_model_to_use_the_calendar():
    prompt = openai_compatible.SYSTEM_PROMPT
    assert re.search(r"\bcalendar\b", prompt, re.IGNORECASE)
    assert re.search(r"\bnull\b", prompt)


def test_c1_the_claimed_job_carries_the_reference():
    fields = {field.name: field for field in dataclasses.fields(ClaimedJob)}
    assert "reference_at" in fields
    assert _mentions_datetime(fields["reference_at"].type)


def _segments() -> list[ASRSegment]:
    texts = (
        "سارا، لطفاً گزارش فروش را تا 2026-11-20 بفرست.",
        "باشه، گزارش فروش را می‌فرستم و با مشتری تماس می‌گیرم.",
    )
    return [
        ASRSegment(index=0, text=texts[0], start_seconds=0.5, end_seconds=4.0),
        ASRSegment(index=1, text=texts[1], start_seconds=4.0, end_seconds=9.5),
    ]


class _OracleASR(ASRProvider):
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="hom351_t3_asr", model="v1")

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        segments = _segments()
        return ASRResult(
            provider="hom351_t3_asr",
            model="v1",
            language="fa",
            text=" ".join(segment.text for segment in segments),
            segments=segments,
            duration_seconds=10,
            is_mock=True,
        )


class _CapturingIntelligence(IntelligenceProvider):
    def __init__(self, result: IntelligenceResult) -> None:
        self.requests: list[IntelligenceRequest] = []
        self._result = result

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="hom351_t3_intelligence", model="oracle-v1")

    async def analyze(self, request: IntelligenceRequest) -> IntelligenceResult:
        self.requests.append(request)
        return self._result


def _upload_meeting(client, email_outbox, monkeypatch, *, key: str) -> tuple[str, str, str]:
    _, workspace_id = _authenticated_workspace(client, email_outbox)
    monkeypatch.setattr(
        "backend.routers.v1.meetings.validate_media_upload",
        lambda *args, **kwargs: _valid_metadata(duration_seconds=10.0),
    )
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)
    upload = _upload(client, workspace_id=workspace_id, key=key)
    assert upload.status_code == 202, upload.text
    body = upload.json()
    return body["job"]["id"], body["meeting"]["id"], workspace_id


def _set_started(meeting_id: str, started_at: datetime) -> None:
    with inspection_session_factory()() as db:
        meeting = db.get(Meeting, meeting_id)
        meeting.started_at = started_at
        db.commit()


def _reference(meeting_id: str) -> datetime:
    with inspection_session_factory()() as db:
        return _grounding_reference(db.get(Meeting, meeting_id))


def _process(job_id: str, settings, intelligence, storage) -> dict[str, str]:
    return process_job(
        job_id,
        worker_id="hom351-t3-oracle",
        settings=settings,
        providers=ProviderBundle(
            asr=_OracleASR(),
            diarization=SingleSpeakerDiarizationProvider(),
            intelligence=intelligence,
        ),
        storage=storage,
    )


def _run_job(client, email_outbox, monkeypatch, settings, intelligence, *, key, started_at=None):
    job_id, meeting_id, _ = _upload_meeting(client, email_outbox, monkeypatch, key=key)
    if started_at is not None:
        _set_started(meeting_id, started_at)
    result = _process(job_id, settings, intelligence, create_object_storage(settings))
    assert result["status"] == "completed", result
    return meeting_id, _reference(meeting_id)


def _analysis_checkpoints(meeting_id: str) -> list[StageCheckpoint]:
    with inspection_session_factory()() as db:
        rows = list(
            db.scalars(
                select(StageCheckpoint).where(
                    StageCheckpoint.meeting_id == meeting_id,
                    StageCheckpoint.stage == "intelligence",
                    StageCheckpoint.published_at.is_not(None),
                )
            )
        )
        for row in rows:
            db.expunge(row)
        return rows


def _request_identity(meeting_id: str) -> dict[str, Any]:
    (row,) = _analysis_checkpoints(meeting_id)
    return dict(row.input_manifest["request"])


def _raw_checkpoint_result(meeting_id: str, workspace_id: str, storage) -> IntelligenceResult:
    (row,) = _analysis_checkpoints(meeting_id)
    with storage.open_reader(workspace_id, row.artifact_key) as reader:
        data = reader.read()
    result = ckpt.parse_artifact(data, IntelligenceResult)
    assert isinstance(result, IntelligenceResult)
    return result


def _plain_result(**fields: Any) -> IntelligenceResult:
    return IntelligenceResult(
        provider="hom351_t3_intelligence",
        model="oracle-v1",
        summary="گزارش فروش و تماس با مشتری",
        summary_source_turn_indexes=[0],
        is_mock=True,
        **fields,
    )


def _sent_calendar(request: IntelligenceRequest) -> list[dict[str, Any]]:
    return _calendar_days(json.loads(_messages(_adapter()._payload(request))["user"]))


def test_q2_processor_sends_the_call_start_as_the_anchor(
    client, email_outbox, settings, monkeypatch
):
    intelligence = _CapturingIntelligence(_plain_result())
    started = datetime(2025, 1, 4, 20, 31, tzinfo=UTC)
    meeting_id, reference = _run_job(
        client,
        email_outbox,
        monkeypatch,
        settings,
        intelligence,
        key="hom351-t3-anchor-started",
        started_at=started,
    )
    assert reference == started
    assert intelligence.requests, "the processor never asked the intelligence provider"
    request = intelligence.requests[-1]
    days = _sent_calendar(request)
    assert days[0] == {
        "date": "2025-01-05",
        "jalali": "1403/10/16",
        "weekday": "یکشنبه",
        "call_day": True,
    }
    _assert_fourteen_days_from(days, date(2025, 1, 5))
    assert getattr(request, _anchor_field()) == reference
    identity = _request_identity(meeting_id)
    assert identity.get("language") == "fa"
    assert "2025-01-05" in identity.values(), identity


def test_q2_processor_without_a_call_start_sends_the_upload_time(
    client, email_outbox, settings, monkeypatch
):
    intelligence = _CapturingIntelligence(_plain_result())
    meeting_id, reference = _run_job(
        client, email_outbox, monkeypatch, settings, intelligence, key="hom351-t3-anchor-created"
    )
    assert intelligence.requests, "the processor never asked the intelligence provider"
    request = intelligence.requests[-1]
    assert getattr(request, _anchor_field()) == reference
    call_day = reference.astimezone(TEHRAN).date()
    _assert_fourteen_days_from(_sent_calendar(request), call_day)
    assert call_day.isoformat() in _request_identity(meeting_id).values()


def _not_stated() -> SummaryField:
    return SummaryField(status=ClaimStatus.NOT_STATED)


def _fields() -> SummaryFields:
    return SummaryFields(purpose=_not_stated(), resolution=_not_stated(), next_steps=_not_stated())


def _action(description: str, turns: list[int], kind: ActionKind = ActionKind.COMMITMENT, **extra):
    return ProviderActionItem(
        description=description, source_turn_indexes=turns, kind=kind, **extra
    )


def _decision(description: str, turns: list[int]) -> Decision:
    return Decision(description=description, basis=ClaimBasis.EXPLICIT, source_turn_indexes=turns)


def _moment(description: str, turns: list[int], kind: str = "commitment") -> KeyMoment:
    return KeyMoment(kind=kind, description=description, source_turn_indexes=turns)


def _value(label: str, turns: list[int], **extra: Any) -> dict[str, Any]:
    return {
        "label": label,
        "basis": "explicit",
        "source_turn_indexes": turns,
        "confidence": 0.9,
        **extra,
    }


def _extraction(**fields: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        name: {
            "status": "stated" if fields.get(name) else "not_stated",
            "values": fields.get(name, []),
        }
        for name in ("intent", "topic", "entity", "outcome")
    }


def _result(
    *,
    actions=(),
    decisions=(),
    moments=(),
    insights=(),
    extraction: Any = None,
    structured: bool = True,
    extraction_missing: bool = False,
) -> IntelligenceResult:
    return IntelligenceResult(
        provider="hom351_t3_intelligence",
        model="oracle-v1",
        summary="خلاصهٔ تماس",
        summary_source_turn_indexes=[0],
        action_items=list(actions),
        decisions=list(decisions),
        key_moments=list(moments),
        insights=list(insights),
        summary_fields=_fields() if structured else None,
        extraction=extraction,
        extraction_missing=extraction_missing,
    )


def _candidate_modules() -> list[str]:
    names = []
    for info in pkgutil.iter_modules(services_package.__path__):
        path = Path(getattr(info.module_finder, "path", "")) / f"{info.name}.py"
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        if "IntelligenceResult" in source and "normalize_for_matching" in source:
            names.append(f"{services_package.__name__}.{info.name}")
    return sorted(names)


def _language_parameter(function) -> str | None:
    for parameter in list(inspect.signature(function).parameters.values())[1:]:
        if "lang" in parameter.name.lower() and parameter.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            return parameter.name
    return None


def _call(function, result: IntelligenceResult, language: str | None = None):
    parameters = list(inspect.signature(function).parameters.values())
    if not parameters or parameters[0].kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        return None
    language_name = _language_parameter(function)
    for parameter in parameters[1:]:
        required = parameter.default is inspect.Parameter.empty and parameter.kind not in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        )
        if required and parameter.name != language_name:
            return None
    keywords: dict[str, Any] = {}
    if language_name is not None:
        language_required = (
            inspect.signature(function).parameters[language_name].default is inspect.Parameter.empty
        )
        if language is not None or language_required:
            keywords[language_name] = language or "fa"
    elif language is not None:
        pytest.fail(
            "C6: the merge must normalize in the revision language, but the function takes "
            "no language parameter"
        )
    output = function(result, **keywords)
    if isinstance(output, tuple) and output and isinstance(output[0], IntelligenceResult):
        output = output[0]
    return output if isinstance(output, IntelligenceResult) else None


def _probe() -> IntelligenceResult:
    return _result(actions=[_action("تماس با مشتری", [0]), _action("تماس  با مشتری", [1])])


def _discover_merge():
    refused: list[str] = []
    for module_name in _candidate_modules():
        module = importlib.import_module(module_name)
        for name, function in sorted(vars(module).items()):
            if name.startswith("_") or not inspect.isfunction(function):
                continue
            if function.__module__ != module.__name__:
                continue
            try:
                output = _call(function, _probe())
            except Exception as error:
                refused.append(f"{module_name}.{name}: {error!r}")
                output = None
            if output is not None and len(output.action_items) == 1:
                return lambda result, language=None, function=function: _call(
                    function, result, language
                )
    pytest.fail(
        "No pure merge function found under backend/services/ (Q3): expected a public "
        "function in a module that uses IntelligenceResult and normalize_for_matching, "
        "which takes an IntelligenceResult and returns one with duplicates merged; "
        f"candidate modules: {_candidate_modules()}; refused: {refused}"
    )


@pytest.fixture(scope="module")
def merge():
    return _discover_merge()


def _norm(text: str) -> str:
    return normalize_for_matching(text, "fa")


def test_q3_actions_that_differ_by_an_extra_space_merge_with_unioned_turns(merge):
    first = _action("تماس با مشتری", [2], priority=ActionPriority.HIGH)
    second = _action("تماس  با مشتری", [0, 2, 1], priority=ActionPriority.LOW)
    merged = merge(_result(actions=[first, second]))
    assert len(merged.action_items) == 1
    item = merged.action_items[0]
    assert item.description == "تماس با مشتری"
    assert item.source_turn_indexes == [0, 1, 2]
    assert item.kind is ActionKind.COMMITMENT
    assert item.priority is ActionPriority.HIGH


def test_q3_control_near_duplicate_actions_stay_two(merge):
    actions = [_action("تماس با مشتری", [0]), _action("تماس با مشتری فردا", [1])]
    merged = merge(_result(actions=actions))
    assert merged.action_items == actions


def test_q3_control_equal_text_with_different_kinds_stays_two(merge):
    actions = [
        _action("تماس با مشتری", [0], ActionKind.COMMITMENT),
        _action("تماس با مشتری", [1], ActionKind.NEXT_STEP),
    ]
    merged = merge(_result(actions=actions))
    assert merged.action_items == actions


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("ارسال گزارش به مشتری", "ارسال گزارش به مشتري"),
        ("ارسال کتاب", "ارسال كتاب"),
        ("ارسال ۲ نسخه", "ارسال 2 نسخه"),
        ("تماس با مشتری", "تماس با مشتری."),
        ("Send the invoice", "send the invoice."),
    ],
    ids=["arabic-yeh", "arabic-kaf", "persian-digits", "trailing-dot", "latin-case"],
)
def test_q3_spellings_that_normalize_equal_merge(merge, first, second):
    assert _norm(first) == _norm(second)
    merged = merge(_result(actions=[_action(first, [1]), _action(second, [0])]))
    assert [(item.description, item.source_turn_indexes) for item in merged.action_items] == [
        (first, [0, 1])
    ]


def test_q3_control_text_that_normalizes_differently_stays_two(merge):
    first, second = "صدور پیش‌فاکتور", "صدور پیش فاکتور"
    assert _norm(first) != _norm(second)
    actions = [_action(first, [0]), _action(second, [1])]
    assert merge(_result(actions=actions)).action_items == actions


def test_c6_the_merge_normalizes_in_the_given_language(merge):
    actions = [_action("تماس با مشتری", [0]), _action("تماس با مشتري", [1])]
    assert normalize_for_matching(actions[0].description, "en") != normalize_for_matching(
        actions[1].description, "en"
    )
    assert merge(_result(actions=actions), "en").action_items == actions
    fa = merge(_result(actions=actions), "fa").action_items
    assert [(item.description, item.source_turn_indexes) for item in fa] == [
        ("تماس با مشتری", [0, 1])
    ]


@pytest.mark.parametrize("language", ["unknown", "xx", "ar"])
def test_c6_a_language_without_a_normalizer_uses_fa_and_never_raises(merge, language):
    actions = [_action("تماس با مشتری", [0]), _action("تماس با مشتري", [1])]
    merged = merge(_result(actions=actions), language).action_items
    assert [(item.description, item.source_turn_indexes) for item in merged] == [
        ("تماس با مشتری", [0, 1])
    ]


def test_q3_merge_keeps_the_first_non_null_owner_due_and_quotes(merge):
    bare = _action("ارسال گزارش فروش", [3])
    detailed = _action(
        "ارسال  گزارش فروش",
        [1],
        owner="سارا",
        owner_quote="سارا، لطفاً گزارش فروش را",
        due_date=date(2026, 11, 20),
        due_quote="تا 2026-11-20",
    )
    item = merge(_result(actions=[bare, detailed])).action_items
    assert len(item) == 1
    assert (item[0].description, item[0].source_turn_indexes) == ("ارسال گزارش فروش", [1, 3])
    assert (item[0].owner, item[0].owner_quote) == ("سارا", "سارا، لطفاً گزارش فروش را")
    assert (item[0].due_date, item[0].due_quote) == (date(2026, 11, 20), "تا 2026-11-20")


def test_r3_different_owners_of_the_same_task_stay_two(merge):
    first = _action(
        "ارسال گزارش فروش",
        [0],
        owner="مریم",
        owner_quote="مریم می‌فرستد",
        due_date=date(2026, 11, 17),
        due_quote="سه‌شنبه",
    )
    second = _action(
        "ارسال  گزارش فروش",
        [1],
        owner="سارا",
        owner_quote="سارا می‌فرستد",
        due_date=date(2026, 11, 20),
        due_quote="تا 2026-11-20",
    )
    assert merge(_result(actions=[first, second])).action_items == [first, second]


def test_q3_merge_does_not_replace_a_present_owner_or_due(merge):
    first = _action(
        "ارسال گزارش فروش",
        [0],
        owner="مریم",
        owner_quote="مریم می‌فرستد",
        due_date=date(2026, 11, 17),
        due_quote="سه‌شنبه",
    )
    second = _action(
        "ارسال  گزارش فروش",
        [1],
        owner="مریم",
        owner_quote="مریم گفت که می‌فرستد",
        due_date=date(2026, 11, 20),
        due_quote="تا 2026-11-20",
    )
    item = merge(_result(actions=[first, second])).action_items
    assert len(item) == 1
    assert (item[0].owner, item[0].owner_quote) == ("مریم", "مریم می‌فرستد")
    assert (item[0].due_date, item[0].due_quote) == (date(2026, 11, 17), "سه‌شنبه")
    assert item[0].source_turn_indexes == [0, 1]


@pytest.mark.parametrize("owner_first", [True, False], ids=["owner-first", "owner-second"])
def test_r3_an_action_without_owner_joins_the_one_with_owner(merge, owner_first):
    owned = _action("تمدید قرارداد", [2], owner="سارا", owner_quote="سارا تمدید می‌کند")
    bare = _action("تمدید  قرارداد", [0])
    actions = [owned, bare] if owner_first else [bare, owned]
    item = merge(_result(actions=actions)).action_items
    assert len(item) == 1
    assert (item[0].owner, item[0].owner_quote) == ("سارا", "سارا تمدید می‌کند")
    assert item[0].source_turn_indexes == [0, 2]
    assert item[0].description == actions[0].description


def test_q3_order_of_first_appearance_is_kept(merge):
    actions = [
        _action("الف: تماس با مشتری", [4]),
        _action("ب: ارسال فاکتور", [1]),
        _action("الف:  تماس با مشتری", [0]),
        _action("ج: تمدید قرارداد", [2]),
        _action("ب: ارسال فاکتور.", [3, 1]),
    ]
    merged = merge(_result(actions=actions)).action_items
    assert [(item.description, item.source_turn_indexes) for item in merged] == [
        ("الف: تماس با مشتری", [0, 4]),
        ("ب: ارسال فاکتور", [1, 3]),
        ("ج: تمدید قرارداد", [2]),
    ]


def test_q3_decisions_merge_and_different_decisions_stay(merge):
    decisions = [
        _decision("تمدید قرارداد تا پایان سال", [2]),
        _decision("تخفیف ده درصد", [1]),
        _decision("تمدید  قرارداد تا پایان سال", [0, 2]),
        _decision("تمدید قرارداد تا پایان سال آینده", [3]),
    ]
    merged = merge(_result(decisions=decisions)).decisions
    assert [(item.description, item.source_turn_indexes) for item in merged] == [
        ("تمدید قرارداد تا پایان سال", [0, 2]),
        ("تخفیف ده درصد", [1]),
        ("تمدید قرارداد تا پایان سال آینده", [3]),
    ]
    assert merged[0].basis is ClaimBasis.EXPLICIT


def test_q3_key_moments_merge_and_different_moments_stay(merge):
    moments = [
        _moment("مشتری از تأخیر ارسال شکایت کرد", [1], "complaint"),
        _moment("مشتری از تأخیر ارسال شکایت کرد.", [0], "complaint"),
        _moment("مشتری از تأخیر ارسال شکایت کرد و تهدید به لغو کرد", [2], "complaint"),
    ]
    merged = merge(_result(moments=moments)).key_moments
    assert [(item.description, item.kind, item.source_turn_indexes) for item in merged] == [
        ("مشتری از تأخیر ارسال شکایت کرد", "complaint", [0, 1]),
        ("مشتری از تأخیر ارسال شکایت کرد و تهدید به لغو کرد", "complaint", [2]),
    ]


def test_q3_key_moments_with_equal_text_merge_whatever_their_kind(merge):
    moments = [
        _moment("مشتری قیمت را گران دانست", [2], "objection"),
        _moment("مشتری قیمت را گران دانست", [1], "complaint"),
    ]
    merged = merge(_result(moments=moments)).key_moments
    assert [(item.kind, item.source_turn_indexes) for item in merged] == [("objection", [1, 2])]


def test_q3_extraction_values_with_the_same_label_merge_within_one_field(merge):
    extraction = _extraction(
        intent=[
            _value("request_callback", [2]),
            _value("purchase_inquiry", [1], confidence=0.5),
            _value("request_callback", [0, 2], confidence=0.4, basis="inferred"),
        ],
        topic=[_value("pricing", [1])],
    )
    merged = merge(_result(extraction=extraction)).extraction
    assert merged["intent"]["values"] == [
        _value("request_callback", [0, 2]),
        _value("purchase_inquiry", [1], confidence=0.5),
    ]
    assert merged["intent"]["status"] == "stated"
    assert merged["topic"] == extraction["topic"]
    assert merged["entity"] == extraction["entity"] and merged["outcome"] == extraction["outcome"]


def test_c3_entity_values_merge_on_label_and_normalized_text(merge):
    extraction = _extraction(
        entity=[
            _value("phone", [1], text="۰۹۱۲۳۴۵۶۷۸۹"),
            _value("person_name", [0], text="علی"),
            _value("phone", [0, 2], text="09123456789", confidence=0.3),
            _value("person_name", [2], text="علي"),
        ]
    )
    assert _norm("۰۹۱۲۳۴۵۶۷۸۹") == _norm("09123456789") and _norm("علی") == _norm("علي")
    merged = merge(_result(extraction=extraction)).extraction
    assert merged["entity"]["values"] == [
        _value("phone", [0, 1, 2], text="۰۹۱۲۳۴۵۶۷۸۹"),
        _value("person_name", [0, 2], text="علی"),
    ]


def test_c3_control_two_names_or_two_labels_stay_two(merge):
    extraction = _extraction(
        entity=[
            _value("person_name", [0], text="سارا"),
            _value("person_name", [1], text="مریم"),
            _value("reference_id", [0], text="4471"),
            _value("amount", [1], text="4471"),
        ]
    )
    assert merge(_result(extraction=copy.deepcopy(extraction))).extraction == extraction


def test_q3_control_extraction_without_repeated_labels_is_unchanged(merge):
    extraction = _extraction(
        intent=[_value("request_callback", [0]), _value("purchase_inquiry", [1])],
        topic=[_value("pricing", [1]), _value("invoice", [0])],
        outcome=[_value("follow_up_scheduled", [1])],
    )
    assert merge(_result(extraction=copy.deepcopy(extraction))).extraction == extraction


def test_q3_empty_lists_stay_empty_and_other_fields_are_untouched(merge):
    empty = _result()
    assert merge(empty) == empty
    legacy = _result(structured=False)
    assert merge(legacy) == legacy
    missing = _result(extraction_missing=True)
    assert merge(missing) == missing


def test_q3_control_summary_insights_and_summary_fields_are_untouched(merge):
    insights = [
        IntelligenceInsight(category="risk", description="ریسک تأخیر", source_turn_indexes=[0]),
        IntelligenceInsight(category="risk", description="ریسک تأخیر", source_turn_indexes=[1]),
    ]
    source = _result(
        actions=[_action("تماس با مشتری", [0]), _action("تماس با مشتری", [1])],
        insights=insights,
    )
    merged = merge(source)
    assert merged.insights == insights
    assert (merged.summary, merged.summary_source_turn_indexes) == (
        source.summary,
        source.summary_source_turn_indexes,
    )
    assert merged.summary_fields == source.summary_fields
    assert (merged.provider, merged.model, merged.is_mock) == (
        source.provider,
        source.model,
        source.is_mock,
    )


ODD_EXTRACTIONS_WITHOUT_REPEATS = [
    "free text",
    5,
    1.5,
    True,
    [],
    [1, "a", None],
    {},
    {"intent": None},
    {"intent": "x"},
    {"intent": {"status": "stated"}},
    {"intent": {"values": None}},
    {"intent": {"values": "abc"}},
    {"intent": {"values": [None, 1, "x", []]}},
    {"other": {"values": [{"label": "a"}, {"label": "b"}]}},
    {"entity": {"values": [{"label": "phone", "text": "1"}, {"label": "phone", "text": "2"}]}},
]
ODD_EXTRACTIONS_WITH_REPEATS = [
    {"intent": {"values": [{"label": None}, {"label": None}]}},
    {"intent": {"values": [{"label": ["a"]}, {"label": ["a"]}]}},
    {"intent": {"values": [{"label": {"x": 1}}, {"label": {"x": 1}}]}},
    {"intent": {"values": [{"label": "a", "source_turn_indexes": "bad"}, {"label": "a"}]}},
    {
        "intent": {
            "values": [
                {"label": "a", "source_turn_indexes": [1, "x", None]},
                {"label": "a", "source_turn_indexes": [0]},
            ]
        }
    },
    {
        "intent": {
            "values": [
                {"label": "a", "source_turn_indexes": [1.5, True]},
                {"label": "a", "source_turn_indexes": [-1, 2]},
            ]
        }
    },
    {
        "intent": {
            "values": [
                {"label": "a", "source_turn_indexes": None},
                {"label": "a", "source_turn_indexes": {"k": 1}},
            ]
        }
    },
    {"other": {"values": [{"label": "a"}, {"label": "a"}]}},
    {
        "intent": {
            "values": [
                {"label": "a", "source_turn_indexes": [[1]]},
                {"label": "a", "source_turn_indexes": [[0]]},
            ]
        }
    },
    {"entity": {"values": [{"label": "phone"}, {"label": "phone"}]}},
    {"entity": {"values": [{"label": "phone", "text": 5}, {"label": "phone", "text": 5}]}},
    {"entity": {"values": [{"label": "phone", "text": None}, {"label": "phone", "text": ""}]}},
]


@pytest.mark.parametrize("extraction", ODD_EXTRACTIONS_WITHOUT_REPEATS, ids=repr)
def test_q3_odd_extraction_without_repeats_never_raises_and_stays(merge, extraction):
    source = _result(
        actions=[_action("تماس با مشتری", [0]), _action("تماس  با مشتری", [1])],
        extraction=copy.deepcopy(extraction),
    )
    merged = merge(source)
    assert isinstance(merged, IntelligenceResult)
    assert merged.extraction == extraction
    assert len(merged.action_items) == 1


@pytest.mark.parametrize("extraction", ODD_EXTRACTIONS_WITH_REPEATS, ids=repr)
def test_q3_odd_extraction_with_repeats_never_raises(merge, extraction):
    source = _result(
        actions=[_action("تماس با مشتری", [0]), _action("تماس  با مشتری", [1])],
        extraction=copy.deepcopy(extraction),
    )
    merged = merge(source)
    assert isinstance(merged, IntelligenceResult)
    assert len(merged.action_items) == 1
    assert source.extraction == extraction


POOL = (
    "تماس با مشتری",
    "تماس  با مشتری",
    "تماس با مشتري",
    "تماس با مشتری فردا",
    "ارسال ۲ نسخه",
    "ارسال 2 نسخه",
    "صدور پیش‌فاکتور",
    "صدور پیش فاکتور",
)
LABELS = ("request_callback", "purchase_inquiry", "schedule_appointment")
ENTITIES = (
    ("phone", "09123456789"),
    ("phone", "۰۹۱۲۳۴۵۶۷۸۹"),
    ("phone", "09120000000"),
    ("person_name", "علی"),
    ("person_name", "علي"),
    ("person_name", "سارا"),
)


class _Sequence:
    def __init__(self, seed: int) -> None:
        self._state = seed

    def random(self) -> float:
        self._state = (self._state * 6364136223846793005 + 1442695040888963407) % 2**64
        return (self._state >> 11) / 2**53

    def randrange(self, start: int, stop: int) -> int:
        return start + int(self.random() * (stop - start))

    def choice(self, options):
        return options[self.randrange(0, len(options))]


def _random_result(rng: _Sequence) -> IntelligenceResult:
    def turns() -> list[int]:
        return [rng.randrange(0, 6) for _ in range(rng.randrange(0, 4))]

    actions = []
    for _ in range(rng.randrange(0, 7)):
        extra: dict[str, Any] = {}
        if rng.random() < 0.4:
            extra.update(owner=rng.choice(["سارا", "مریم"]), owner_quote="سارا گفت")
        if rng.random() < 0.4:
            extra.update(due_date=date(2026, 11, rng.randrange(14, 28)), due_quote="فردا")
        actions.append(_action(rng.choice(POOL), turns(), rng.choice(list(ActionKind)), **extra))
    decisions = [_decision(rng.choice(POOL), turns()) for _ in range(rng.randrange(0, 4))]
    moments = [
        _moment(rng.choice(POOL), turns(), rng.choice(["commitment", "objection"]))
        for _ in range(rng.randrange(0, 4))
    ]
    roll = rng.random()
    if roll < 0.2:
        extraction: Any = None
    elif roll < 0.35:
        extraction = rng.choice(ODD_EXTRACTIONS_WITHOUT_REPEATS + ODD_EXTRACTIONS_WITH_REPEATS)
    else:
        extraction = _extraction(
            intent=[_value(rng.choice(LABELS), turns()) for _ in range(rng.randrange(0, 4))],
            topic=[_value("pricing", turns()) for _ in range(rng.randrange(0, 3))],
            entity=[
                _value(label, turns(), text=text)
                for label, text in (rng.choice(ENTITIES) for _ in range(rng.randrange(0, 4)))
            ],
        )
    return _result(
        actions=actions,
        decisions=decisions,
        moments=moments,
        extraction=copy.deepcopy(extraction),
        structured=bool(decisions or moments) or rng.random() < 0.5,
    )


def _action_key(item: ProviderActionItem) -> tuple[str, ActionKind]:
    return _norm(item.description), item.kind


def _text_key(item: Decision | KeyMoment) -> str:
    return _norm(item.description)


def _value_key(name: str, value: dict[str, Any]) -> Any:
    if name == "entity":
        return value["label"], _norm(value["text"])
    return value["label"]


def _well_formed(extraction: Any) -> bool:
    return isinstance(extraction, dict) and set(extraction) == {
        "intent",
        "topic",
        "entity",
        "outcome",
    }


def _assert_merged(name: str, original: list, kept: list, key) -> None:
    keys = [key(item) for item in kept]
    assert len(keys) == len(set(keys)), (name, keys)
    assert keys == list(dict.fromkeys(key(item) for item in original)), name
    groups: dict[Any, set[int]] = {}
    for item in original:
        groups.setdefault(key(item), set()).update(_item_turns(item))
    for item in kept:
        expected = sorted(groups[key(item)])
        assert sorted(set(_item_turns(item))) == expected, (name, item)
        if sum(1 for other in original if key(other) == key(item)) > 1:
            assert _item_turns(item) == expected, (name, item)


def _item_turns(item: Any) -> list[int]:
    if isinstance(item, dict):
        return list(item["source_turn_indexes"])
    return list(item.source_turn_indexes)


def _owner(item: ProviderActionItem) -> str | None:
    return None if item.owner is None else _norm(item.owner)


def _assert_actions_merged(original: list, kept: list) -> None:
    keys = list(dict.fromkeys(_action_key(item) for item in original))
    assert list(dict.fromkeys(_action_key(item) for item in kept)) == keys
    for key in keys:
        before = [item for item in original if _action_key(item) == key]
        after = [item for item in kept if _action_key(item) == key]
        owners = {_owner(item) for item in before} - {None}
        kept_owners = [_owner(item) for item in after]
        if owners:
            assert None not in kept_owners, (key, kept_owners)
            assert len(kept_owners) == len(set(kept_owners)), (key, kept_owners)
            assert set(kept_owners) == owners, (key, kept_owners, owners)
        else:
            assert kept_owners == [None], (key, kept_owners)
        everything = sorted({index for item in before for index in item.source_turn_indexes})
        assert sorted({index for item in after for index in item.source_turn_indexes}) == everything
        for item in after:
            assert (item.owner, item.owner_quote) in {(o.owner, o.owner_quote) for o in before}
            assert (item.due_date, item.due_quote) in {(o.due_date, o.due_quote) for o in before}
            for other in before:
                if _owner(other) is not None and _owner(other) == _owner(item):
                    assert set(other.source_turn_indexes) <= set(item.source_turn_indexes), key
            if all(item.source_turn_indexes != other.source_turn_indexes for other in before):
                assert item.source_turn_indexes == sorted(set(item.source_turn_indexes)), key
        if len(after) == 1 and len(before) > 1:
            assert after[0].source_turn_indexes == everything, key


def test_q3_never_raises_and_leaves_no_duplicates_on_many_valid_results(merge):
    rng = _Sequence(351)
    for _ in range(400):
        source = _random_result(rng)
        before = source.model_dump(mode="json")
        merged = merge(source)
        assert isinstance(merged, IntelligenceResult)
        assert source.model_dump(mode="json") == before
        assert merge(merged) == merged
        _assert_actions_merged(source.action_items, merged.action_items)
        _assert_merged("decisions", source.decisions, merged.decisions, _text_key)
        _assert_merged("key_moments", source.key_moments, merged.key_moments, _text_key)
        if _well_formed(source.extraction):
            for name in ("intent", "topic", "entity", "outcome"):
                _assert_merged(
                    name,
                    source.extraction[name]["values"],
                    merged.extraction[name]["values"],
                    lambda value, name=name: _value_key(name, value),
                )


def _duplicate_result() -> IntelligenceResult:
    return _plain_result(
        summary_fields=_fields(),
        action_items=[
            _action("ارسال گزارش فروش", [1]),
            _action(
                "ارسال  گزارش فروش",
                [0],
                owner="سارا",
                owner_quote="سارا، لطفاً گزارش فروش را",
                due_date=date(2026, 11, 20),
                due_quote="تا 2026-11-20",
            ),
            _action("ارسال گزارش فروش", [1], ActionKind.NEXT_STEP),
            _action("تماس با مشتری", [1]),
            _action("تماس با مشتری فردا", [1]),
        ],
        decisions=[
            _decision("گزارش فروش تا بیستم آبان ارسال شود", [1]),
            _decision("گزارش فروش تا بیستم آبان ارسال شود.", [0]),
        ],
        key_moments=[
            _moment("سارا ارسال گزارش را پذیرفت", [1]),
            _moment("سارا ارسال  گزارش را پذیرفت", [0, 1]),
            _moment("سارا تماس با مشتری را پذیرفت", [1]),
        ],
    )


def _assert_stored_once(meeting_id: str) -> None:
    with inspection_session_factory()() as db:
        rows = list(db.scalars(select(ActionRow).where(ActionRow.meeting_id == meeting_id)))
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        key_points = list(summary.key_points)
    stored = sorted(
        (
            row.description,
            row.kind,
            row.assignee_text,
            row.due_at.date().isoformat() if row.due_at is not None else None,
        )
        for row in rows
    )
    assert stored == sorted(
        [
            ("ارسال گزارش فروش", "commitment", "سارا", "2026-11-20"),
            ("ارسال گزارش فروش", "next_step", None, None),
            ("تماس با مشتری", "commitment", None, None),
            ("تماس با مشتری فردا", "commitment", None, None),
        ]
    )
    decisions = [point for point in key_points if point.get("section") == SECTION_DECISION]
    assert [(point["description"], point["source_turn_indexes"]) for point in decisions] == [
        ("گزارش فروش تا بیستم آبان ارسال شود", [0, 1])
    ]
    moments = [point for point in key_points if point.get("section") == SECTION_KEY_MOMENT]
    assert [(point["description"], point["source_turn_indexes"]) for point in moments] == [
        ("سارا ارسال گزارش را پذیرفت", [0, 1]),
        ("سارا تماس با مشتری را پذیرفت", [1]),
    ]


def _assert_raw(result: IntelligenceResult) -> None:
    raw = _duplicate_result()
    assert result.action_items == raw.action_items
    assert result.decisions == raw.decisions
    assert result.key_moments == raw.key_moments


def test_q3_processor_stores_each_duplicate_once_and_keeps_distinct_items(
    client, email_outbox, settings, monkeypatch
):
    intelligence = _CapturingIntelligence(_duplicate_result())
    storage = create_object_storage(settings)
    job_id, meeting_id, workspace_id = _upload_meeting(
        client, email_outbox, monkeypatch, key="hom351-t3-dedupe"
    )
    result = _process(job_id, settings, intelligence, storage)
    assert result["status"] == "completed", result
    _assert_stored_once(meeting_id)
    _assert_raw(_raw_checkpoint_result(meeting_id, workspace_id, storage))


def _crash_once_after_the_checkpoints(monkeypatch) -> None:
    original = processor.commit_usage
    calls = {"n": 0}

    def flaky_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash after the checkpoints were committed")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "commit_usage", flaky_commit)
    monkeypatch.setattr(outbox, "publish_processing_job", FakeBroker())


def _retry_with_new_start(
    client, email_outbox, settings, monkeypatch, *, key: str, retry_started: datetime
):
    intelligence = _CapturingIntelligence(_duplicate_result())
    storage = create_object_storage(settings)
    job_id, meeting_id, workspace_id = _upload_meeting(client, email_outbox, monkeypatch, key=key)
    _set_started(meeting_id, datetime(2025, 1, 4, 20, 31, tzinfo=UTC))
    _crash_once_after_the_checkpoints(monkeypatch)
    first = _process(job_id, settings, intelligence, storage)
    assert first["status"] == "retry_scheduled", first
    assert len(intelligence.requests) == 1
    _set_started(meeting_id, retry_started)
    _make_due(job_id)
    second = _process(job_id, settings, intelligence, storage)
    assert second["status"] == "completed", second
    return intelligence, meeting_id, workspace_id, storage


def test_c2_a_retry_for_another_tehran_call_day_asks_the_provider_again(
    client, email_outbox, settings, monkeypatch
):
    intelligence, meeting_id, workspace_id, storage = _retry_with_new_start(
        client,
        email_outbox,
        settings,
        monkeypatch,
        key="hom351-t3-retry-new-day",
        retry_started=datetime(2025, 1, 5, 20, 31, tzinfo=UTC),
    )
    assert len(intelligence.requests) == 2
    _assert_fourteen_days_from(_sent_calendar(intelligence.requests[-1]), date(2025, 1, 6))
    _assert_stored_once(meeting_id)


def test_c2_c4_a_retry_on_the_same_tehran_day_reuses_the_raw_checkpoint_and_still_merges(
    client, email_outbox, settings, monkeypatch
):
    intelligence, meeting_id, workspace_id, storage = _retry_with_new_start(
        client,
        email_outbox,
        settings,
        monkeypatch,
        key="hom351-t3-retry-same-day",
        retry_started=datetime(2025, 1, 5, 10, 0, tzinfo=UTC),
    )
    assert len(intelligence.requests) == 1
    _assert_stored_once(meeting_id)
    _assert_raw(_raw_checkpoint_result(meeting_id, workspace_id, storage))


def test_q3_prompt_asks_to_list_each_item_once():
    assert re.search(r"\bonce\b", openai_compatible.SYSTEM_PROMPT, re.IGNORECASE)
