"""HOM-288: a transcript correction under the redacted view never stores a
placeholder as text and never puts back a value the view would then show.

Synthetic fixtures only.  The detector needs a boundary around a value, so a
value put back next to other text (``[PHONE_1]2``, ``x[EMAIL_1]``,
``[EMAIL_1]_x``) is either not found again, and shown raw, or found as a
different value.  Such a correction is refused with ``422
invalid_placeholder`` and writes nothing, as is a placeholder the view does
not know.  The transcript is ``test_privacy_routing.SEGMENTS`` and five more
segments: a phone glued to Persian letters, a placeholder stored as text, a
placeholder stored as text next to a phone shown with the same placeholder,
and two segments that spell one phone in two ways.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import func, select

from backend.database.saas_models import AuditEvent, TranscriptRevision, TranscriptSegment
from backend.jobs.processor import process_job
from backend.privacy import transcript_view
from backend.privacy.text_pii import PiiClass, Replacement
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRResult, ASRSegment
from backend.services.transcript_service import current_revision, segments_of
from backend.tests._cost import WIDE_BOUND_SECONDS, lines_run
from backend.tests._database import inspection_session_factory
from backend.tests.test_free_text_partial_pii import _post, _view
from backend.tests.test_ingestion_ledger import _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_privacy_routing import SEGMENTS as ROUTING_SEGMENTS
from backend.tests.test_privacy_routing import _dump, _get, _no_raw, _set_policy

PHONE = "۰۹۱۲ ۳۴۵ ۶۷۸۹"  # as segment 0 stores it
PHONE_SOLID = "۰۹۱۲۳۴۵۶۷۸۹"  # as segment 1 stores it
EMAIL = "ali.rezaei@example.com"
SEGMENTS = [
    *ROUTING_SEGMENTS,
    f"شماره{PHONE_SOLID} را یادداشت کنید.",
    "کد [EMAIL_9] از نسخه قبلی مانده است.",
    f"عبارت [PHONE_1] با موبایل {PHONE} فرق دارد.",
    f"شماره {PHONE_SOLID} یعنی {PHONE} است.",
    f"تماس با +98 912 345 6789 یا {PHONE_SOLID} است.",
]
SHOWN = [
    "سلام، من علی هستم. کد ملی [IR_NATIONAL_ID_1] و موبایل [PHONE_1] است.",
    "ایمیل [EMAIL_1] را بنویسید و دوباره [PHONE_1] را تکرار می‌کنم.",
    "شماره[PHONE_1] را یادداشت کنید.",
    "کد [EMAIL_9] از نسخه قبلی مانده است.",
    "عبارت [PHONE_1] با موبایل [PHONE_1] فرق دارد.",
    "شماره [PHONE_1] یعنی [PHONE_1] است.",
    "تماس با [PHONE_1] یا [PHONE_1] است.",
]


class _ASR(MockASRProvider):
    def __init__(self) -> None:
        super().__init__(allow_mock=True, transcript=" ".join(SEGMENTS), language="fa")

    async def transcribe(self, request):
        return ASRResult(
            provider="mock_asr",
            model="deterministic-v1",
            language="fa",
            text=" ".join(SEGMENTS),
            segments=[
                ASRSegment(index=i, text=text, start_seconds=i, end_seconds=i + 1)
                for i, text in enumerate(SEGMENTS)
            ],
            duration_seconds=10,
            is_mock=True,
        )


def _meeting(client, email_outbox, monkeypatch, settings, *, redacted: bool = True):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "correction")
    if redacted:
        assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    accepted = _upload(client, tenant, key="correction-0001")
    assert accepted.status_code == 202, accepted.text
    providers = ProviderBundle(
        asr=_ASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )
    result = process_job(
        accepted.json()["job"]["id"], worker_id="correction", settings=settings, providers=providers
    )
    assert result["status"] == "completed", result
    return tenant, accepted.json()["meeting"]["id"]


def _transcript(client, tenant: dict, meeting_id: str) -> dict:
    shown = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/transcript")
    assert shown.status_code == 200, shown.text
    return shown.json()


def _correct(client, tenant: dict, meeting_id: str, base_revision_id: str, edits):
    return _post(
        client,
        tenant,
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        {"base_revision_id": base_revision_id, "reason": "fix a word", "edits": edits},
    )


def _edits(transcript: dict, *changes: tuple[int, str, str]) -> list[dict]:
    """One edit per (segment, old, new): the shown text with ``old`` replaced once."""
    segments = transcript["segments"]
    return [
        {"segment_id": segments[index]["id"], "text": segments[index]["text"].replace(old, new, 1)}
        for index, old, new in changes
    ]


def _written(tenant: dict, meeting_id: str) -> tuple[str, int, int]:
    """The current revision, the number of revisions and the number of audit rows."""
    with inspection_session_factory()() as db:
        current = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        revisions = db.scalar(
            select(func.count(TranscriptRevision.id)).where(
                TranscriptRevision.meeting_id == meeting_id
            )
        )
        audits = db.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.workspace_id == tenant["workspace_id"]
            )
        )
    return current.id, revisions, audits


def _stored(tenant: dict, meeting_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        current = current_revision(db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id)
        return list(
            db.scalars(
                select(TranscriptSegment.text)
                .where(TranscriptSegment.revision_id == current.id)
                .order_by(TranscriptSegment.sequence)
            )
        )


def _replaced(index: int, old: str, new: str) -> list[str]:
    return [text.replace(old, new, 1) if i == index else text for i, text in enumerate(SEGMENTS)]


# --- refused: nothing is written (T1) --------------------------------------------------

DENIED = {
    "digit-after-phone": [(0, "[PHONE_1]", "[PHONE_1]2")],
    "persian-digit-after-phone": [(0, "[PHONE_1]", "[PHONE_1]۲")],
    "digit-after-email": [(1, "[EMAIL_1]", "[EMAIL_1]2")],
    "letter-before-email": [(1, "[EMAIL_1]", "x[EMAIL_1]")],
    "letter-after-email": [(1, "[EMAIL_1]", "[EMAIL_1]x")],
    "underscore-after-email": [(1, "[EMAIL_1]", "[EMAIL_1]_x")],
    "persian-letter-after-email": [(1, "[EMAIL_1]", "[EMAIL_1]ب")],
    "dot-and-letter-before-email": [(1, "[EMAIL_1]", "a.[EMAIL_1]")],
    "plus-and-letter-before-email": [(1, "[EMAIL_1]", "a+[EMAIL_1]")],
    "domain-label-after-email": [(1, "[EMAIL_1]", "[EMAIL_1].ir")],
    "phone-glued-to-a-value": [(0, "[PHONE_1]", "[PHONE_1][IR_NATIONAL_ID_1]")],
    "unknown-placeholder-added": [(0, "است.", "[EMAIL_9] است.")],
    "placeholder-stored-as-text-added-again": [(3, "است.", "[EMAIL_9] است.")],
    "ambiguous-placeholder-kept": [(4, "دارد.", "دارد، درست است.")],
    "ambiguous-placeholder-left-alone": [(4, " با موبایل [PHONE_1]", "")],
    "bad-edit-with-a-good-edit": [(0, "[PHONE_1]", "[PHONE_1]2"), (1, "بنویسید", "ثبت کنید")],
}


def test_the_fixture_shows_the_expected_placeholders(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings)
    transcript = _transcript(client, tenant, meeting_id)
    assert transcript["view"] == "redacted"
    assert [segment["text"] for segment in transcript["segments"]] == SHOWN
    assert _stored(tenant, meeting_id) == SEGMENTS


@pytest.mark.parametrize("changes", DENIED.values(), ids=DENIED.keys())
def test_a_placeholder_that_cannot_come_back_whole_is_refused(
    client, email_outbox, monkeypatch, settings, changes
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings)
    transcript = _transcript(client, tenant, meeting_id)
    before = _written(tenant, meeting_id)

    refused = _correct(
        client, tenant, meeting_id, transcript["revision"]["id"], _edits(transcript, *changes)
    )
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "invalid_placeholder"
    assert "[" not in error["message"] and _no_raw(error["message"])
    assert _written(tenant, meeting_id) == before
    assert _stored(tenant, meeting_id) == SEGMENTS


# --- accepted: the exact stored text (T2), nothing raw on the next read (T3) ------------

ALLOWED = {
    "space-after-phone": ((0, "[PHONE_1]", "[PHONE_1] 2"), (0, PHONE, f"{PHONE} 2")),
    "space-before-persian-digit": ((0, "[PHONE_1]", "[PHONE_1] ۲"), (0, PHONE, f"{PHONE} ۲")),
    "space-after-email": ((1, "[EMAIL_1]", "[EMAIL_1] 2"), (1, EMAIL, f"{EMAIL} 2")),
    "space-before-email": ((1, "[EMAIL_1]", "x [EMAIL_1]"), (1, EMAIL, f"x {EMAIL}")),
    "full-stop-after-email": ((1, "[EMAIL_1]", "[EMAIL_1]. x"), (1, EMAIL, f"{EMAIL}. x")),
    "plus-before-phone": ((0, "[PHONE_1]", "+[PHONE_1]"), (0, PHONE, f"+{PHONE}")),
    "at-sign-before-email": ((1, "[EMAIL_1]", "@[EMAIL_1]"), (1, EMAIL, f"@{EMAIL}")),
    "two-spellings-a-word-changed": ((5, "است.", "است، درست."), (5, "است.", "است، درست.")),
    "two-spellings-the-first-deleted": (
        (5, "[PHONE_1] یعنی", "یعنی"),
        (5, f"{PHONE_SOLID} یعنی", "یعنی"),
    ),
    "plus-98-spelling-a-word-changed": ((6, "است.", "است، درست."), (6, "است.", "است، درست.")),
    "email-added-to-another-segment": (
        (0, "است.", "[EMAIL_1] است."),
        (0, "است.", f"{EMAIL} است."),
    ),
    "phone-added-again-gets-this-segment-value": (
        (1, "می‌کنم.", "می‌کنم: [PHONE_1]"),
        (1, "می‌کنم.", f"می‌کنم: {PHONE_SOLID}"),
    ),
    "kept-in-an-edited-segment": ((0, "است.", "است. (تأیید شد)"), (0, "است.", "است. (تأیید شد)")),
    "kept-next-to-persian-letters": ((2, "را", "را حتماً"), (2, "را", "را حتماً")),
    "kept-placeholder-stored-as-text": ((3, "مانده", "باقی مانده"), (3, "مانده", "باقی مانده")),
    "good-edit-alone": ((1, "بنویسید", "ثبت کنید"), (1, "بنویسید", "ثبت کنید")),
}


@pytest.mark.parametrize(("change", "expected"), ALLOWED.values(), ids=ALLOWED.keys())
def test_a_placeholder_apart_from_other_text_is_restored_exactly(
    client, email_outbox, monkeypatch, settings, change, expected
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings)
    transcript = _transcript(client, tenant, meeting_id)

    corrected = _correct(
        client, tenant, meeting_id, transcript["revision"]["id"], _edits(transcript, change)
    )
    assert corrected.status_code == 201, corrected.text
    assert _stored(tenant, meeting_id) == _replaced(*expected)
    assert _no_raw(_dump(corrected.json()))
    shown = _transcript(client, tenant, meeting_id)
    assert shown["revision"]["id"] == corrected.json()["revision"]["id"]
    assert _no_raw(_dump(shown))


def test_the_original_view_stores_a_placeholder_as_written(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, redacted=False)
    transcript = _transcript(client, tenant, meeting_id)
    assert transcript["view"] == "original"

    corrected = _correct(
        client,
        tenant,
        meeting_id,
        transcript["revision"]["id"],
        _edits(transcript, (0, PHONE, "[PHONE_1]2")),
    )
    assert corrected.status_code == 201, corrected.text
    assert _stored(tenant, meeting_id) == _replaced(0, PHONE, "[PHONE_1]2")


def test_a_stale_base_stays_a_conflict(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings)
    transcript = _transcript(client, tenant, meeting_id)
    first = _correct(
        client,
        tenant,
        meeting_id,
        transcript["revision"]["id"],
        _edits(transcript, (1, "بنویسید", "ثبت کنید")),
    )
    assert first.status_code == 201, first.text
    before = _written(tenant, meeting_id)

    stale = _correct(
        client,
        tenant,
        meeting_id,
        transcript["revision"]["id"],
        _edits(transcript, (0, "[PHONE_1]", "[PHONE_1]2")),
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_stale"
    assert _written(tenant, meeting_id) == before


def test_other_refusals_keep_their_code(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings)
    transcript = _transcript(client, tenant, meeting_id)
    glued, good = _edits(transcript, (0, "[PHONE_1]", "[PHONE_1]2"), (0, "است.", "است. (تأیید شد)"))
    other = _edits(transcript, (1, "[EMAIL_1]", "[EMAIL_1]2"))[0]

    for code, edits in (
        ("duplicate_edit", [glued, good]),
        ("invalid_segment", [glued, {**good, "segment_id": "0" * 36}]),
        ("invalid_speaker", [{**glued, "speaker_key": "speaker_9"}]),
        ("invalid_speaker", [glued, {**other, "speaker_key": "speaker_9"}]),
    ):
        refused = _correct(client, tenant, meeting_id, transcript["revision"]["id"], edits)
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["code"] == code
    assert _stored(tenant, meeting_id) == SEGMENTS


# --- kept or added, against the segment as shown (T4) -----------------------------------


def test_kept_or_added_is_decided_against_the_shown_segment(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings, redacted=False)
    transcript = _transcript(client, tenant, meeting_id)
    # Written before the policy: a fragment of the phone and a placeholder stored as text.
    stored = f"پیش‌شماره ۰۹۱۲ و موبایل {PHONE} با کد [EMAIL_1] است."
    corrected = _correct(
        client,
        tenant,
        meeting_id,
        transcript["revision"]["id"],
        [{"segment_id": transcript["segments"][0]["id"], "text": stored}],
    )
    assert corrected.status_code == 201, corrected.text
    assert _set_policy(client, tenant, pii_redaction_enabled=True).status_code == 200
    shown = "پیش‌شماره ۰۹۱۲ و موبایل [PHONE_1] با کد [EMAIL_1] است."

    with _view(tenant, meeting_id, settings) as view:
        revision = current_revision(
            view.db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id
        )
        segment = segments_of(view.db, revision)[0]
        assert view.segment_text(segment, revision) == shown

        def restore(text: str) -> str:
            return view.restore_segment(text, revision=revision, segment_id=segment.id)

        assert restore(f"{shown} درست است") == f"{stored} درست است"
        assert restore(f"{shown} و [EMAIL_1]") == f"{stored} و {EMAIL}"
        assert restore(f"[PHONE_1]  {shown}") == f"{PHONE} {stored}"
        glued = shown.replace("[EMAIL_1]", "[EMAIL_1]2")
        assert restore(glued) == stored.replace("[EMAIL_1]", "[EMAIL_1]2")
        for refused in (f"{shown} [EMAIL_9]", shown.replace("[PHONE_1]", "[PHONE_1]2")):
            with pytest.raises(transcript_view.PlaceholderRefused):
                restore(refused)

        def unaligned(*args):
            raise AssertionError("an edit without a placeholder is not aligned")

        monkeypatch.setattr(transcript_view, "_kept_tokens", unaligned)
        assert restore("[یادداشت] بدون نشانه") == "[یادداشت] بدون نشانه"
    with _view(tenant, meeting_id, settings) as view:
        # Free text masks the fragment too, so the two renderings differ.
        assert view.text(stored) == "پیش‌شماره [PHONE_1] و موبایل [PHONE_1] با کد [EMAIL_1] است."


def test_glue_is_refused_even_when_the_detector_finds_the_value(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings)
    glued = SHOWN[0].replace("[PHONE_1]", "[PHONE_1]2")
    stored = SEGMENTS[0].replace(PHONE, f"{PHONE}2")

    with _view(tenant, meeting_id, settings) as view:
        revision = current_revision(
            view.db, workspace_id=tenant["workspace_id"], meeting_id=meeting_id
        )
        segment = segments_of(view.db, revision)[0]
        with pytest.raises(transcript_view.PlaceholderRefused):
            view.restore_segment(glued, revision=revision, segment_id=segment.id)
        # A detector that found the phone whole next to the digit: only the glue rule refuses.
        found = transcript_view.redact(stored, view.policy.classes)[1]
        start = stored.index(PHONE)
        exact = Replacement(start, start + len(PHONE), PiiClass.PHONE, "[PHONE_1]", PHONE)
        monkeypatch.setattr(transcript_view, "redact", lambda text, _: (text, [*found, exact]))
        with pytest.raises(transcript_view.PlaceholderRefused):
            view.restore_segment(glued, revision=revision, segment_id=segment.id)


# --- cost (R10) -------------------------------------------------------------------------


def test_restoring_stops_at_the_first_refusal(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id = _meeting(client, email_outbox, monkeypatch, settings)
    transcript = _transcript(client, tenant, meeting_id)
    restore = transcript_view.MeetingView.restore_segment
    calls: list[str] = []

    def counted(view, value, **kwargs):
        calls.append(kwargs["segment_id"])
        return restore(view, value, **kwargs)

    monkeypatch.setattr(transcript_view.MeetingView, "restore_segment", counted)
    edits = _edits(transcript, (0, "[PHONE_1]", "[PHONE_1]2"), (1, "بنویسید", "ثبت کنید"))
    refused = _correct(client, tenant, meeting_id, transcript["revision"]["id"], edits)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid_placeholder"
    assert calls == [edits[0]["segment_id"]]


_WORDS = ("یک کلمه " * 1000)[:7981]
_FAR = f"[PHONE_1] {_WORDS} پایان"
_ALIGNMENT_CASES = (
    (_WORDS, "[A_1] " * 1333),
    ("[A_1] " * 1330, "[A_1]  " * 1142),
    (_FAR, _FAR.replace("پایان", "آخر")),
)
_ALIGNMENT_MODULES = ("backend.privacy.transcript_view", "difflib")
# Lines of transcript_view and difflib code one _kept_tokens call runs on the cases
# above, measured on the base (Python 3.12.13): 4,019; 7,442; 99.  The ceiling is about
# 9x the largest count, like the old 1 s limit's headroom.  With the cap off, the
# second case runs far past it (87 s on the wall clock, without counting).
ALIGNMENT_WORK_CEILING = 67_000


def test_the_alignment_is_bounded():
    for shown, value in _ALIGNMENT_CASES:
        count, kept = lines_run(
            _ALIGNMENT_MODULES,
            transcript_view._kept_tokens,
            shown,
            value,
            stop_at=ALIGNMENT_WORK_CEILING,  # fail at once, not after minutes of alignment
        )
        assert count < ALIGNMENT_WORK_CEILING, (len(shown), len(value), count)
        # Backstop for work in C the line count cannot see, in CPU seconds of this thread.
        started = time.thread_time()
        transcript_view._kept_tokens(shown, value)
        assert time.thread_time() - started < WIDE_BOUND_SECONDS, (len(shown), len(value))
    assert kept == {0: 0}
    shown = "شماره [PHONE_1] یعنی [PHONE_1] است."
    assert transcript_view._kept_tokens(shown, "شماره یعنی [PHONE_1] است.") == {11: 21}


def test_the_alignment_ceiling_catches_an_uncapped_alignment(monkeypatch):
    monkeypatch.setattr(transcript_view, "_MAX_ALIGNED_PIECES", 10**9)
    counts = [
        lines_run(
            _ALIGNMENT_MODULES,
            transcript_view._kept_tokens,
            shown,
            value,
            stop_at=ALIGNMENT_WORK_CEILING,
        )[0]
        for shown, value in _ALIGNMENT_CASES
    ]
    assert max(counts) >= ALIGNMENT_WORK_CEILING, counts
