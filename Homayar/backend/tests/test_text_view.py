"""WP22/F36 (ADR 0034): the derived normalised text view and entities of a transcript.

The post-call processor writes, next to every transcript revision it creates, a
derived view: each segment normalised by the speech profile and the entities
found in it.  These tests prove the raw transcript stays byte-identical, the
versions are recorded, the source is the reader's default view (redacted under
an enforced policy), retries and repeated derivation never duplicate a view,
a derivation fault is recorded without failing the transcript, the conversation
purge and tombstone reapply remove the rows, and tenants are isolated under
row-level security (``postgres`` marker).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.exc import DBAPIError

from backend.config import Settings
from backend.database.saas_models import (
    AuditEvent,
    JobState,
    Meeting,
    MeetingStatus,
    ProcessingJob,
    RedactedSegment,
    StageState,
    TextViewEntity,
    TextViewSegment,
    TranscriptImmutableError,
    TranscriptRedaction,
    TranscriptSegment,
    TranscriptTextView,
    Workspace,
)
from backend.jobs import processor
from backend.jobs.leases import as_utc
from backend.jobs.processor import process_job
from backend.jobs.tombstone_reapply import reapply_tombstones
from backend.privacy.redaction_service import policy_digest
from backend.privacy.text_pii import DETECTOR_VERSION, PiiClass
from backend.providers.mocks import MockASRProvider
from backend.providers.schemas import ASRRequest, ASRResult, ASRSegment
from backend.services.transcript_service import current_revision
from backend.speech import text_view
from backend.speech.dates import DateRange
from backend.speech.entities import EXTRACTOR_VERSION
from backend.speech.text_normalize import normalize_az, normalize_en, normalize_fa
from backend.speech.text_view import (
    NORMALIZER_VERSION,
    REASON_EXTRACTION_FAILED,
    REASON_NO_TRANSCRIPT,
    REASON_NORMALIZATION_FAILED,
    REASON_NORMALIZER_UNAVAILABLE,
    REASON_PERSIST_FAILED,
    REASON_SOURCE_UNAVAILABLE,
    SourceSegment,
    derive,
    derive_text_view,
    json_value,
    view_digest,
)
from backend.tests import test_row_level_security as _rls
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _patch_media, _tenant, _upload
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_processor import _providers
from backend.tests.test_tombstone_reapply import _simulate_database_restore_before

probe_role = _rls.probe_role


@pytest.fixture
def settings(settings, tmp_path):
    """Deployments of this module journal their tombstones (ADR 0022)."""
    return settings.model_copy(update={"tombstone_journal_dir": str(tmp_path / "journal")})


REFERENCE = datetime(2026, 10, 10, 8, 0, tzinfo=UTC)

# Persian with Arabic kaf/yeh, Persian and Arabic-Indic digits in one number,
# ZWNJ joins, a shadda and a tatweel run; English with an international phone.
PERSIAN = (
    "سلام، من علی هستم. فردا ساعت ۱۰ صبح تماس بگیرید؛ مبلغ ۲۵۰ هزار تومان واريز شد "
    "و كد پيگيري AB-12345 است. موبایل ۰۹۱۲ ۳۴۵ ٦٧٨٩ و کتاب‌ها را می‌خوانم."
)
PERSIAN_SECOND = "تهـــران کدّ پستی؛ ایمیل ali.rezaei@example.com را بفرستید."
ENGLISH = (
    "Hello, my name is Sara. Call +44 20 7946 0000 or email sara@example.com, "
    "total 120 thousand toman."
)
# Pieces of the phone number only ("345" also occurs in the reference AB-12345).
PII_DIGITS = ("0912", "۰۹۱۲", "۳۴۵", "6789", "٦٧٨٩")


class SegmentedASR(MockASRProvider):
    """The deterministic mock, one ASR segment per configured text."""

    def __init__(self, *texts: str, language: str = "fa") -> None:
        super().__init__(allow_mock=True, transcript="\n".join(texts), language=language)
        self._texts = texts

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        result = await super().transcribe(request)
        return result.model_copy(
            update={
                "segments": [
                    ASRSegment(
                        index=index,
                        text=value,
                        start_seconds=index * 5.0,
                        end_seconds=index * 5.0 + 4.0,
                    )
                    for index, value in enumerate(self._texts)
                ]
            }
        )


def _process(client, email_outbox, monkeypatch, settings, label: str, *texts: str, enforce=False):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    if enforce:
        with inspection_session_factory()() as db:
            workspace = db.get(Workspace, tenant["workspace_id"])
            workspace.pii_redaction_enabled = True
            db.commit()
    accepted = _upload(client, tenant, key=f"{label}-0001")
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["id"]
    result = process_job(
        job_id, worker_id=label, settings=settings, providers=_providers(SegmentedASR(*texts))
    )
    return tenant, accepted.json()["meeting"]["id"], job_id, result


def _views(meeting_id: str) -> list[TranscriptTextView]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(TranscriptTextView)
                .where(TranscriptTextView.meeting_id == meeting_id)
                .order_by(TranscriptTextView.created_at)
            )
        )


def _rows(model, meeting_id: str) -> list:
    with inspection_session_factory()() as db:
        query = select(model).where(model.meeting_id == meeting_id)
        if hasattr(model, "position"):
            query = query.order_by(model.position)
        elif hasattr(model, "sequence"):
            query = query.order_by(model.sequence)
        return list(db.scalars(query))


def _derived_rows(meeting_id: str) -> int:
    with inspection_session_factory()() as db:
        return sum(
            db.scalar(select(func.count()).select_from(model).where(model.meeting_id == meeting_id))
            or 0
            for model in (TranscriptTextView, TextViewSegment, TextViewEntity)
        )


def _completed_audit(meeting_id: str) -> AuditEvent:
    with inspection_session_factory()() as db:
        return db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "meeting.processing.completed",
                AuditEvent.target_id == meeting_id,
            )
        )


# --- pure derivation ------------------------------------------------------------


def test_normalizer_version_pins_the_speech_profile_output():
    """``NORMALIZER_VERSION`` names exactly this behaviour: a change to these
    outputs is a new normaliser version (bump it, never edit the literals)."""
    assert NORMALIZER_VERSION == "speech-normalize/1"
    assert normalize_fa("كتاب ها ي من ۱۲۳ و ٤٥٦ تهـــران كدّ  پستي می خوانم?") == (
        "کتاب‌ها ی من 123 و 456 تهران کد پستی می‌خوانم؟"
    )
    assert normalize_en("Ｔｅｓｔ ’quoted’  ۱۲۳ ١٢٣") == "Test 'quoted' 123 123"
    assert normalize_az("Bakı ǝlaqə  ۱۲") == "Bakı əlaqə 12"


def test_persian_and_english_views_with_mixed_digits_and_zwnj():
    persian = derive(
        [SourceSegment("s2", 1, PERSIAN_SECOND), SourceSegment("s1", 0, PERSIAN)],
        language="fa",
        reference=REFERENCE,
    )
    assert (persian.state, persian.reason_code) == (StageState.COMPLETED, None)
    assert [source.segment_id for source, _ in persian.segments] == ["s1", "s2"]
    normalized = persian.segments[0][1]
    assert normalized == normalize_fa(PERSIAN)
    assert "کد پیگیری" in normalized and "واریز" in normalized  # letters unified
    assert "0912 345 6789" in normalized  # Persian and Arabic-Indic digits folded
    assert "کتاب‌ها" in normalized and "می‌خوانم" in normalized  # ZWNJ kept
    assert persian.segments[1][1] == "تهران کد پستی؛ ایمیل ali.rezaei@example.com را بفرستید."
    found = [
        (e.segment_id, e.entity_type, PERSIAN if e.segment_id == "s1" else PERSIAN_SECOND)
        for e in persian.entities
    ]
    assert [(segment, kind) for segment, kind, _ in found] == [
        ("s1", "date"),
        ("s1", "time"),
        ("s1", "amount"),
        ("s1", "reference_id"),
        ("s1", "phone"),
        ("s2", "email"),
    ]
    by_type = {e.entity_type: e for e in persian.entities}
    source = {"s1": PERSIAN, "s2": PERSIAN_SECOND}
    raw = {e.entity_type: source[e.segment_id][e.start : e.end] for e in persian.entities}
    assert raw == {
        "date": "فردا",
        "time": "ساعت ۱۰ صبح",
        "amount": "۲۵۰ هزار تومان",
        "reference_id": "AB-12345",
        "phone": "۰۹۱۲ ۳۴۵ ٦٧٨٩",  # offsets index the original text
        "email": "ali.rezaei@example.com",
    }
    assert by_type["date"].value == "2026-10-11"  # "tomorrow" in Tehran
    assert by_type["time"].value == "10:00:00"
    assert by_type["amount"].value == {"amount": 250000, "currency": "IRT"}
    assert by_type["phone"].value == "09123456789"
    assert by_type["phone"].source == "privacy.text_pii:phone"

    english = derive([SourceSegment("e1", 0, ENGLISH)], language="en", reference=REFERENCE)
    assert english.state is StageState.COMPLETED
    assert english.segments[0][1] == ENGLISH
    kinds = [(e.entity_type, ENGLISH[e.start : e.end], e.value) for e in english.entities]
    assert kinds == [
        ("person_name_slot", "Sara.", None),  # a slot, never a guessed name
        ("phone", "+44 20 7946 0000", "00442079460000"),
        ("email", "sara@example.com", "sara@example.com"),
        ("amount", "120 thousand toman", {"amount": 120000, "currency": "IRT"}),
    ]


def test_derivation_states_are_honest():
    sources = [SourceSegment("s1", 0, PERSIAN)]
    empty = derive([], language="fa", reference=REFERENCE)
    assert (empty.state, empty.reason_code, empty.segments) == (
        StageState.SKIPPED,
        REASON_NO_TRANSCRIPT,
        (),
    )
    unknown = derive(sources, language="xx", reference=REFERENCE)
    assert (unknown.state, unknown.reason_code) == (
        StageState.SKIPPED,
        REASON_NORMALIZER_UNAVAILABLE,
    )
    assert derive(sources, language="fa-IR", reference=REFERENCE).state is StageState.COMPLETED


def test_a_failing_normalizer_or_extractor_is_recorded_not_raised(monkeypatch):
    sources = [SourceSegment("s1", 0, PERSIAN)]

    def broken(*_args, **_kwargs):
        raise RuntimeError("simulated fault")

    monkeypatch.setattr(text_view, "extract_entities", broken)
    partial = derive(sources, language="fa", reference=REFERENCE)
    assert (partial.state, partial.reason_code) == (StageState.PARTIAL, REASON_EXTRACTION_FAILED)
    assert partial.segments[0][1] == normalize_fa(PERSIAN) and partial.entities == ()

    monkeypatch.setattr(text_view, "get_normalizer", lambda _language: broken)
    failed = derive(sources, language="fa", reference=REFERENCE)
    assert (failed.state, failed.reason_code) == (StageState.FAILED, REASON_NORMALIZATION_FAILED)
    assert failed.segments == () and failed.entities == ()


def test_entity_values_are_encoded_as_json():
    from datetime import date, time

    assert json_value(date(2026, 10, 11)) == "2026-10-11"
    assert json_value(time(10, 30)) == "10:30:00"
    assert json_value(DateRange(date(2026, 10, 11), date(2026, 10, 17))) == {
        "start": "2026-10-11",
        "end": "2026-10-17",
    }
    assert json_value({"amount": 2.5, "currency": "IRT"}) == {"amount": 2.5, "currency": "IRT"}
    with pytest.raises(TypeError):
        json_value(object())


def test_view_identity_covers_every_version_and_the_source(monkeypatch):
    base = {
        "language": "fa",
        "input_view": "original",
        "input_policy_digest": None,
        "reference_at": REFERENCE,
        "tz": text_view.TEHRAN,
    }
    digest = view_digest(**base)
    assert digest == view_digest(**base)
    assert digest != view_digest(**{**base, "input_view": "redacted", "input_policy_digest": "d"})
    assert digest != view_digest(**{**base, "language": "en"})
    for name in ("NORMALIZER_VERSION", "EXTRACTOR_VERSION", "DETECTOR_VERSION", "VALUE_ENCODING"):
        with monkeypatch.context() as patch:
            patch.setattr(text_view, name, "changed")
            assert view_digest(**base) != digest, name


# --- the processor ----------------------------------------------------------------


def test_processor_derives_a_view_and_keeps_the_raw_transcript_byte_identical(
    client, email_outbox, monkeypatch, settings
):
    _tenant_info, meeting_id, job_id, result = _process(
        client, email_outbox, monkeypatch, settings, "text-view-raw", PERSIAN, PERSIAN_SECOND
    )
    assert result == {"job_id": job_id, "status": "completed"}
    segments = _rows(TranscriptSegment, meeting_id)
    # HOM-120: normalisation never changes raw evidence, code point for code point.
    assert [s.text.encode("utf-8") for s in segments] == [
        PERSIAN.encode("utf-8"),
        PERSIAN_SECOND.encode("utf-8"),
    ]
    (view,) = _views(meeting_id)
    assert (view.state, view.reason_code) == (StageState.COMPLETED, None)
    assert (view.input_view, view.input_policy_digest, view.redaction_id) == (
        "original",
        None,
        None,
    )
    assert (view.normalizer_version, view.extractor_version, view.detector_version) == (
        NORMALIZER_VERSION,
        EXTRACTOR_VERSION,
        DETECTOR_VERSION,
    )
    assert view.language == "fa" and view.timezone == "Asia/Tehran"
    assert view.job_id == job_id and view.attempt == 1
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=view.workspace_id, meeting_id=meeting_id)
        meeting = db.get(Meeting, meeting_id)
        assert view.revision_id == revision.id
        assert as_utc(view.reference_at) == as_utc(meeting.created_at)
    normalized = _rows(TextViewSegment, meeting_id)
    assert [(n.segment_id, n.sequence, n.text) for n in normalized] == [
        (segments[0].id, 0, normalize_fa(PERSIAN)),
        (segments[1].id, 1, normalize_fa(PERSIAN_SECOND)),
    ]
    entities = _rows(TextViewEntity, meeting_id)
    assert view.segment_count == 2 and view.entity_count == len(entities) == 6
    assert [n.entity_count for n in normalized] == [5, 1]
    text_of = {s.id: s.text for s in segments}
    assert [
        (e.entity_type, text_of[e.segment_id][e.start_offset : e.end_offset]) for e in entities
    ] == [
        ("date", "فردا"),
        ("time", "ساعت ۱۰ صبح"),
        ("amount", "۲۵۰ هزار تومان"),
        ("reference_id", "AB-12345"),
        ("phone", "۰۹۱۲ ۳۴۵ ٦٧٨٩"),
        ("email", "ali.rezaei@example.com"),
    ]
    assert [e.position for e in entities] == list(range(6))
    details = _completed_audit(meeting_id).details
    assert details["text_view_id"] == view.id and details["text_view_state"] == "completed"
    assert details["text_view_entities"] == 6 and details["text_view_input"] == "original"
    audit_text = str(details)
    assert "0912" not in audit_text and "ali.rezaei" not in audit_text


def test_an_enforced_policy_derives_from_the_redacted_version_only(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _job_id, result = _process(
        client,
        email_outbox,
        monkeypatch,
        settings,
        "text-view-redacted",
        PERSIAN,
        PERSIAN_SECOND,
        enforce=True,
    )
    assert result["status"] == "completed"
    (view,) = _views(meeting_id)
    digest = policy_digest(list(PiiClass))
    with inspection_session_factory()() as db:
        redaction = db.scalar(
            select(TranscriptRedaction).where(
                TranscriptRedaction.meeting_id == meeting_id,
                TranscriptRedaction.policy_digest == digest,
            )
        )
        redacted = {
            row.segment_id: row.text
            for row in db.scalars(
                select(RedactedSegment).where(RedactedSegment.redaction_id == redaction.id)
            )
        }
    assert (view.state, view.input_view) == (StageState.COMPLETED, "redacted")
    assert (view.input_policy_digest, view.redaction_id) == (digest, redaction.id)
    normalized = " ".join(row.text for row in _rows(TextViewSegment, meeting_id))
    assert "[PHONE_1]" in normalized and "[EMAIL_1]" in normalized
    assert not any(value in normalized for value in PII_DIGITS)
    assert "ali.rezaei" not in normalized
    entities = _rows(TextViewEntity, meeting_id)
    assert [e.entity_type for e in entities] == ["date", "time", "amount", "reference_id"]
    for entity in entities:  # offsets index the redacted source text
        assert redacted[entity.segment_id][entity.start_offset : entity.end_offset]
        assert entity.value is None or not any(v in str(entity.value) for v in PII_DIGITS)
    # The raw transcript is untouched by both derivations.
    assert [s.text for s in _rows(TranscriptSegment, meeting_id)] == [PERSIAN, PERSIAN_SECOND]
    assert tenant["workspace_id"] == view.workspace_id


def test_a_retry_after_a_crash_leaves_exactly_one_view(client, email_outbox, monkeypatch, settings):
    original = processor.commit_usage
    calls = {"n": 0}

    def flaky_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before the results transaction committed")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "commit_usage", flaky_commit)
    _tenant_info, meeting_id, job_id, first = _process(
        client, email_outbox, monkeypatch, settings, "text-view-retry", PERSIAN
    )
    assert first["status"] == "retry_scheduled"
    assert _derived_rows(meeting_id) == 0  # rolled back with the revision
    _make_due(job_id)
    second = process_job(
        job_id,
        worker_id="text-view-retry-2",
        settings=settings,
        providers=_providers(SegmentedASR(PERSIAN)),
    )
    assert second == {"job_id": job_id, "status": "completed"}
    (view,) = _views(meeting_id)
    assert view.attempt == 2 and view.state is StageState.COMPLETED
    assert len(_rows(TextViewSegment, meeting_id)) == 1
    assert len(_rows(TextViewEntity, meeting_id)) == view.entity_count == 5


def test_deriving_again_for_the_same_revision_returns_the_same_view(
    client, email_outbox, monkeypatch, settings
):
    _tenant_info, meeting_id, _job_id, _ = _process(
        client, email_outbox, monkeypatch, settings, "text-view-again", PERSIAN
    )
    (view,) = _views(meeting_id)
    before = _derived_rows(meeting_id)
    with inspection_session_factory()() as db:
        revision = current_revision(db, workspace_id=view.workspace_id, meeting_id=meeting_id)
        meeting = db.get(Meeting, meeting_id)
        again = derive_text_view(
            db,
            revision=revision,
            segments=list(
                db.scalars(
                    select(TranscriptSegment).where(TranscriptSegment.revision_id == revision.id)
                )
            ),
            view_classes=[],
            reference_at=meeting.created_at,
            settings=settings,
            now=datetime.now(UTC),
        )
        db.commit()
    assert again.view_id == view.id and again.state is StageState.COMPLETED
    assert _derived_rows(meeting_id) == before


def test_an_extraction_fault_never_fails_the_transcript(
    client, email_outbox, monkeypatch, settings
):
    def broken(*_args, **_kwargs):
        raise RuntimeError("simulated extractor fault")

    monkeypatch.setattr(text_view, "extract_entities", broken)
    _tenant_info, meeting_id, job_id, result = _process(
        client, email_outbox, monkeypatch, settings, "text-view-partial", PERSIAN
    )
    assert result == {"job_id": job_id, "status": "completed"}
    with inspection_session_factory()() as db:
        assert db.get(Meeting, meeting_id).status is MeetingStatus.COMPLETED
    (view,) = _views(meeting_id)
    assert (view.state, view.reason_code) == (StageState.PARTIAL, REASON_EXTRACTION_FAILED)
    assert [row.text for row in _rows(TextViewSegment, meeting_id)] == [normalize_fa(PERSIAN)]
    assert _rows(TextViewEntity, meeting_id) == [] and view.entity_count == 0
    details = _completed_audit(meeting_id).details
    assert (details["text_view_state"], details["text_view_reason"]) == (
        "partial",
        REASON_EXTRACTION_FAILED,
    )


def test_a_storage_fault_is_recorded_as_failed_and_the_transcript_completes(
    client, email_outbox, monkeypatch, settings
):
    def broken(*_args, **_kwargs):
        raise RuntimeError("simulated storage fault")

    monkeypatch.setattr(text_view, "_persist", broken)
    _tenant_info, meeting_id, job_id, result = _process(
        client, email_outbox, monkeypatch, settings, "text-view-failed", PERSIAN
    )
    assert result == {"job_id": job_id, "status": "completed"}
    (view,) = _views(meeting_id)
    assert (view.state, view.reason_code) == (StageState.FAILED, REASON_PERSIST_FAILED)
    assert view.segment_count == view.entity_count == 0
    assert _rows(TextViewSegment, meeting_id) == [] and _rows(TextViewEntity, meeting_id) == []
    assert len(_rows(TranscriptSegment, meeting_id)) == 1


def test_an_unreadable_redacted_source_is_recorded_as_failed(
    client, email_outbox, monkeypatch, settings
):
    monkeypatch.setattr(text_view, "redacted_segments", lambda *_a, **_k: [])
    _tenant_info, meeting_id, job_id, result = _process(
        client, email_outbox, monkeypatch, settings, "text-view-source", PERSIAN, enforce=True
    )
    assert result == {"job_id": job_id, "status": "completed"}
    (view,) = _views(meeting_id)
    assert (view.state, view.reason_code) == (StageState.FAILED, REASON_SOURCE_UNAVAILABLE)
    assert view.input_view == "redacted" and view.redaction_id is not None
    assert _rows(TextViewSegment, meeting_id) == []


def test_even_an_unrecordable_failure_never_fails_the_transcript(
    client, email_outbox, monkeypatch, settings
):
    def broken(*_args, **_kwargs):
        raise RuntimeError("simulated storage fault")

    monkeypatch.setattr(text_view, "_persist", broken)
    monkeypatch.setattr(text_view._Identity, "header", broken)
    _tenant_info, meeting_id, job_id, result = _process(
        client, email_outbox, monkeypatch, settings, "text-view-unrecorded", PERSIAN
    )
    assert result == {"job_id": job_id, "status": "completed"}
    assert _views(meeting_id) == []
    assert [s.text for s in _rows(TranscriptSegment, meeting_id)] == [PERSIAN]
    details = _completed_audit(meeting_id).details
    assert details["text_view_id"] is None
    assert (details["text_view_state"], details["text_view_reason"]) == (
        "failed",
        REASON_PERSIST_FAILED,
    )


def test_an_empty_transcript_records_a_skipped_view(client, email_outbox, monkeypatch, settings):
    from backend.tests.test_job_processor import SilentASRProvider

    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "text-view-empty")
    accepted = _upload(client, tenant, key="text-view-empty-0001")
    meeting_id = accepted.json()["meeting"]["id"]
    result = process_job(
        accepted.json()["job"]["id"],
        worker_id="text-view-empty",
        settings=settings,
        providers=_providers(SilentASRProvider()),
    )
    assert result["status"] == "partial"
    (view,) = _views(meeting_id)
    assert (view.state, view.reason_code, view.segment_count) == (
        StageState.SKIPPED,
        REASON_NO_TRANSCRIPT,
        0,
    )


def test_view_rows_are_immutable(client, email_outbox, monkeypatch, settings):
    _tenant_info, meeting_id, _job_id, _ = _process(
        client, email_outbox, monkeypatch, settings, "text-view-immutable", PERSIAN
    )
    for model, column, value in (
        (TranscriptTextView, "state", StageState.FAILED),
        (TextViewSegment, "text", "rewritten"),
        (TextViewEntity, "value", "rewritten"),
    ):
        with inspection_session_factory()() as db:
            row = db.scalars(select(model).where(model.meeting_id == meeting_id)).first()
            setattr(row, column, value)
            with pytest.raises(TranscriptImmutableError):
                db.flush()
            db.rollback()


# --- deletion ------------------------------------------------------------------


def test_the_purge_removes_the_derived_view(client, email_outbox, monkeypatch, settings):
    tenant, meeting_id, _job_id, _ = _process(
        client, email_outbox, monkeypatch, settings, "text-view-purge", PERSIAN, enforce=True
    )
    assert len(_views(meeting_id)) == 1 and _derived_rows(meeting_id) > 2
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    assert _derived_rows(meeting_id) == 0


def test_tombstone_reapply_removes_a_derived_view_brought_back_by_a_restore(
    client, email_outbox, monkeypatch, settings
):
    tenant, meeting_id, _job_id, _ = _process(
        client, email_outbox, monkeypatch, settings, "text-view-reapply", PERSIAN
    )
    derived = _derived_rows(meeting_id)
    assert derived > 2
    # Tombstone only; the "backup" restored below predates the tombstone.
    monkeypatch.setattr("backend.routers.v1.meetings.run_purge_attempt", lambda *_a, **_k: None)
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}", headers=_headers(tenant), cookies=tenant["cookies"]
    )
    assert deleted.status_code == 204
    _simulate_database_restore_before(meeting_id)
    assert _derived_rows(meeting_id) == derived

    counts = reapply_tombstones(settings)
    assert counts["reapplied"] == 1 and counts["purges_completed"] == 1, counts
    assert _derived_rows(meeting_id) == 0


# --- tenancy ---------------------------------------------------------------------


@pytest.mark.postgres
def test_rls_binds_derived_views_to_their_tenant(
    client, email_outbox, monkeypatch, settings: Settings, probe_role
):
    tenant, meeting_id, _job_id, _ = _process(
        client, email_outbox, monkeypatch, settings, "text-view-rls", PERSIAN
    )
    other = _tenant(client, email_outbox, "text-view-rls-other")
    tables = ("transcript_text_views", "text_view_segments", "text_view_entities")
    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            for table in tables:  # no bound context: nothing is visible
                assert _rls._count(connection, table) == 0
            _rls._bind(connection, workspace_id=other["workspace_id"])
            for table in tables:
                assert _rls._count(connection, table) == 0
            touched = connection.execute(
                text("UPDATE text_view_segments SET text = 'x' WHERE meeting_id = :m"),
                {"m": meeting_id},
            )
            assert touched.rowcount == 0
            removed = connection.execute(
                text("DELETE FROM text_view_entities WHERE meeting_id = :m"), {"m": meeting_id}
            )
            assert removed.rowcount == 0
            connection.rollback()
            _rls._bind(connection, workspace_id=tenant["workspace_id"])
            assert [_rls._count(connection, table) for table in tables] == [1, 1, 5]
            connection.rollback()
    finally:
        engine.dispose()
    with inspection_session_factory()() as db:
        status = db.execute(
            text(
                "SELECT relname, relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('transcript_text_views', 'text_view_segments', "
                "'text_view_entities') ORDER BY relname"
            )
        ).all()
    assert status == [(name, True) for name in sorted(tables)]


@pytest.mark.postgres
def test_a_tenant_cannot_write_a_view_into_another_workspace(
    client, email_outbox, monkeypatch, settings: Settings, probe_role
):
    tenant, meeting_id, _job_id, _ = _process(
        client, email_outbox, monkeypatch, settings, "text-view-rls-write", PERSIAN
    )
    other = _tenant(client, email_outbox, "text-view-rls-write-other")
    (view,) = _views(meeting_id)
    engine = create_engine(probe_role, pool_size=1, max_overflow=0, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            _rls._bind(connection, workspace_id=other["workspace_id"])
            with pytest.raises(DBAPIError, match="row-level security"):
                connection.execute(
                    text(
                        "INSERT INTO text_view_segments (id, workspace_id, meeting_id, view_id, "
                        "segment_id, sequence, text, entity_count) "
                        "VALUES ('forged', :w, :m, :v, :s, 99, 'forged', 0)"
                    ),
                    {
                        "w": tenant["workspace_id"],
                        "m": meeting_id,
                        "v": view.id,
                        "s": _rows(TranscriptSegment, meeting_id)[0].id,
                    },
                )
            connection.rollback()
    finally:
        engine.dispose()


def test_a_deleted_meeting_is_never_given_a_view(client, email_outbox, monkeypatch, settings):
    """A tombstone committed before publication wins: no view, no revision."""
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "text-view-deleted")
    accepted = _upload(client, tenant, key="text-view-deleted-0001")
    meeting_id = accepted.json()["meeting"]["id"]
    job_id = accepted.json()["job"]["id"]
    original = processor._persist_success

    def tombstone_first(claim, **kwargs):
        with inspection_session_factory()() as db:
            db.execute(
                update(Meeting).where(Meeting.id == meeting_id).values(deleted_at=datetime.now(UTC))
            )
            db.commit()
        return original(claim, **kwargs)

    monkeypatch.setattr(processor, "_persist_success", tombstone_first)
    result = process_job(
        job_id,
        worker_id="text-view-deleted",
        settings=settings,
        providers=_providers(SegmentedASR(PERSIAN)),
    )
    assert result["status"] == "failed" and result["code"] == "meeting_unavailable"
    assert _derived_rows(meeting_id) == 0
    with inspection_session_factory()() as db:
        assert db.get(ProcessingJob, job_id).state is JobState.FAILED
