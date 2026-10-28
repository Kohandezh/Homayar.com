"""HOM-38: the job derives one channel-preserving derivative per ORIGINAL.

These tests run the real upload (real validation and HOM-37 inspection) and the
real ``process_job`` with mock providers; the derivation runs the real ffmpeg.
Each test builds the app once and puts several meetings into it (HOM-292).
The derivative never changes what ASR receives, the transcript or the job
outcome; a refusal or a failure leaves a reason in the audit event
``media.derivative_unavailable`` (for operators only).
"""

from __future__ import annotations

import hashlib
import math
import stat
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, text

from backend.database.saas_models import (
    AuditEvent,
    JobState,
    MediaKind,
    MediaLifecycleState,
    MediaObject,
    ProcessingJob,
    TranscriptSegment,
    Workspace,
)
from backend.jobs import audio_derivatives
from backend.jobs import processor as processor_module
from backend.jobs.processor import process_job
from backend.media.derivatives import (
    DERIVATIVE_PRODUCER,
    RECORD_MAX_BYTES,
    parse_derivative_header,
)
from backend.media.time_map import TimeMap
from backend.providers.base import ASRProvider, ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import ASRRequest, ASRResult
from backend.services.media_lifecycle_service import (
    place_legal_hold,
    release_legal_hold,
    request_deletion,
)
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _tenant
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider

pytestmark = pytest.mark.ffmpeg

TRANSCRIPT = "پیگیری قرارداد انجام شود"
UNAVAILABLE = "media.derivative_unavailable"
RATE = 44_100


# --- helpers ----------------------------------------------------------------------------


class _FlakyASR(ASRProvider):
    """Fails the first call (retryable), then transcribes: a real job retry."""

    def __init__(self) -> None:
        self.calls = 0
        self.delegate = MockASRProvider(allow_mock=True, transcript=TRANSCRIPT)

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("asr_temporarily_unavailable", "Try again", retryable=True)
        return await self.delegate.transcribe(request)


def _providers(asr: ASRProvider | None = None) -> ProviderBundle:
    return ProviderBundle(
        asr=asr or MockASRProvider(allow_mock=True, transcript=TRANSCRIPT),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )


def _stereo_wav(path: Path, *, seconds: int = 3) -> Path:
    """A burst on the left channel at 1.0 s and on the right channel at 2.0 s (lavfi)."""
    left = "if(gte(t,1)*lt(t,1.5),0.5*sin(2*PI*700*t),0)"
    right = "if(gte(t,2)*lt(t,2.4),0.5*sin(2*PI*700*t),0)"
    subprocess.run(  # noqa: S603 - fixed argv, test-only fixture generation
        [  # noqa: S607
            "ffmpeg",
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=exprs='{left}|{right}':s={RATE}:d={seconds}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path


@pytest.fixture(scope="module")
def wav(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return _stereo_wav(tmp_path_factory.mktemp("derivative-job") / "call.wav").read_bytes()


@pytest.fixture
def outcomes(monkeypatch) -> list[str]:
    """Every return value of the job's derivation step, in order."""
    seen: list[str] = []
    real = audio_derivatives.derive_audio_for_job

    def recording(*args, **kwargs) -> str:
        outcome = real(*args, **kwargs)
        seen.append(outcome)
        return outcome

    monkeypatch.setattr(processor_module, "derive_audio_for_job", recording)
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_args, **_kwargs: True)
    return seen


def _upload(client, tenant: dict, content: bytes, *, key: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/uploads",
        headers=_headers(tenant, **{"Idempotency-Key": key}),
        cookies=tenant["cookies"],
        data={"title": "Derivative", "language": "fa"},
        files={"file": ("call.wav", content, "audio/wav")},
    )
    assert response.status_code == 202, response.text
    return response.json()["job"]["id"], response.json()["meeting"]["id"]


def _original(meeting_id: str) -> MediaObject:
    with inspection_session_factory()() as db:
        media = db.scalar(
            select(MediaObject).where(
                MediaObject.meeting_id == meeting_id, MediaObject.kind == MediaKind.ORIGINAL
            )
        )
        assert media is not None
        return media


def _derivatives(meeting_id: str) -> list[MediaObject]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(MediaObject).where(
                    MediaObject.meeting_id == meeting_id, MediaObject.kind != MediaKind.ORIGINAL
                )
            )
        )


def _unavailable(target_id: str | None) -> list[AuditEvent]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(AuditEvent)
                .where(AuditEvent.event_type == UNAVAILABLE, AuditEvent.target_id == target_id)
                .order_by(AuditEvent.occurred_at.asc())
            )
        )


def _reasons(media_id: str | None) -> list[str]:
    return [event.details["reason"] for event in _unavailable(media_id)]


def _transcript(meeting_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        return list(
            db.scalars(
                select(TranscriptSegment.text).where(TranscriptSegment.meeting_id == meeting_id)
            )
        )


def _columns(media: MediaObject) -> dict:
    return {column.key: getattr(media, column.key) for column in MediaObject.__table__.columns}


def _digest(storage, media: MediaObject) -> str:
    digest = hashlib.sha256()
    with storage.open_reader(media.workspace_id, media.storage_key) as reader:
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _make_due(job_id: str) -> None:
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        job.retry_after = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()


def _run(job_id: str, settings, *, asr: ASRProvider | None = None, label: str = "w") -> dict:
    return process_job(
        job_id, worker_id=f"test-derivative-{label}", settings=settings, providers=_providers(asr)
    )


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# --- A2, A3, A8, A10 --------------------------------------------------------------------


def test_one_derivative_per_original_and_none_with_the_default_setting(
    client, email_outbox, settings, outcomes, wav
) -> None:
    tenant = _tenant(client, email_outbox, "derive-main")
    storage = create_object_storage(settings)

    # A10: with the default setting nothing is derived and nothing is audited.
    assert settings.media_derivatives_enabled is False
    job_off, meeting_off = _upload(client, tenant, wav, key="derive-off")
    assert _run(job_off, settings, label="off")["status"] == "completed"
    assert outcomes == ["derivatives_disabled"]
    assert _derivatives(meeting_off) == []
    assert _unavailable(_original(meeting_off).id) == []
    assert _transcript(meeting_off) == [TRANSCRIPT]

    # A3 and A8: the first attempt derives, ASR fails, the retry finds the derivative.
    enabled = settings.model_copy(
        update={"media_derivatives_enabled": True, "job_retry_delay_seconds": 1}
    )
    job_id, meeting_id = _upload(client, tenant, wav, key="derive-on")
    original = _original(meeting_id)
    before, bytes_before = _columns(original), _digest(storage, original)
    flaky = _FlakyASR()
    assert _run(job_id, enabled, asr=flaky, label="on-1")["status"] == "retry_scheduled"
    assert outcomes[-1] == "registered"
    _make_due(job_id)
    assert _run(job_id, enabled, asr=flaky, label="on-2")["status"] == "completed"
    assert outcomes[-1] == "already_derived"

    (derived,) = _derivatives(meeting_id)
    assert derived.kind == MediaKind.NORMALIZED
    assert derived.derived_from_id == original.id
    assert derived.producer == DERIVATIVE_PRODUCER
    assert (derived.content_type, derived.container_format) == ("audio/wav", "wav")
    assert (derived.channel_count, derived.sample_rate_hz) == (2, 16000)
    assert derived.lifecycle_state == MediaLifecycleState.ACTIVE
    for name in ("organization_id", "workspace_id", "meeting_id", "ingestion_id"):
        assert getattr(derived, name) == getattr(original, name), name
    assert derived.retention_expires_at == original.retention_expires_at
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job is not None and job.state == JobState.COMPLETED
    assert derived.job_id == job_id

    with storage.open_reader(derived.workspace_id, derived.storage_key) as reader:
        raw = reader.read()
    assert len(raw) == derived.size_bytes
    assert hashlib.sha256(raw).hexdigest() == derived.sha256
    header = parse_derivative_header(raw[: 64 + RECORD_MAX_BYTES])
    record = header.record
    assert record["source"]["media_id"] == derived.derived_from_id == original.id
    assert record["source"]["sha256"] == original.sha256
    count = record["time_map"]["derivative_sample_count"]
    assert derived.duration_seconds == math.ceil(count / 16000)
    assert header.data_size == 2 * 2 * count
    assert record["inspection_status"] == "complete"
    # A1 through the job: activity only on the left near 1.0 s and on the right near 2.0 s.
    mapping = TimeMap.from_record(record["time_map"])
    (left,), (right,) = (channel["intervals"] for channel in record["activity"]["channels"])
    assert abs(mapping.original_seconds(left[0]) - 1) <= 0.02
    assert abs(mapping.original_seconds(right[0]) - 2) <= 0.02

    # A2: the ORIGINAL row and bytes are unchanged; the transcript is the provider's.
    assert _columns(_original(meeting_id)) == before
    assert _digest(storage, _original(meeting_id)) == bytes_before
    assert _transcript(meeting_id) == [TRANSCRIPT]
    # media.registered is audited; already_derived is not a failure and is not audited.
    with inspection_session_factory()() as db:
        registered = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "media.registered", AuditEvent.target_id == derived.id
            )
        )
        assert registered is not None
    assert _unavailable(original.id) == []


# --- deny-cases and their allow-controls -------------------------------------------------


def test_refused_sources_get_no_derivative_and_leave_their_reason(
    client, email_outbox, settings, outcomes, wav
) -> None:
    tenant = _tenant(client, email_outbox, "derive-deny")
    enabled = settings.model_copy(
        update={"media_derivatives_enabled": True, "job_retry_delay_seconds": 1}
    )
    now = datetime.now(UTC)

    # A held ORIGINAL (otherwise live and valid) is derived, and the derivative is held
    # with the ORIGINAL's owner, reason and review date (HOM-313).
    job_id, meeting_id = _upload(client, tenant, wav, key="deny-held")
    original_id = _original(meeting_id).id
    with inspection_session_factory()() as db:
        held = db.get(MediaObject, original_id)
        place_legal_hold(
            db,
            media=held,
            reason="litigation",
            review_at=now + timedelta(days=30),
            actor_user_id=tenant["user_id"],
            now=now,
            settings=settings,
        )
        db.commit()
    assert _run(job_id, enabled, label="held")["status"] == "completed"
    assert outcomes[-1] == "registered"
    (derived,) = _derivatives(meeting_id)
    original = _original(meeting_id)
    assert derived.lifecycle_state == MediaLifecycleState.LEGAL_HOLD
    assert derived.legal_hold_reason == original.legal_hold_reason == "litigation"
    assert derived.legal_hold_by_user_id == original.legal_hold_by_user_id == tenant["user_id"]
    assert derived.legal_hold_review_at == original.legal_hold_review_at
    with inspection_session_factory()() as db:
        inherited = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.event_type == "media.legal_hold_inherited",
                    AuditEvent.target_id == derived.id,
                )
            )
        )
    assert [event.details["source_id"] for event in inherited] == [original_id]
    assert _reasons(original_id) == []
    # The single-row release lifts only the ORIGINAL's hold; the derivative stays held.
    with inspection_session_factory()() as db:
        release_legal_hold(
            db,
            media=db.get(MediaObject, original_id),
            reason="case closed",
            actor_user_id=None,
            now=now,
            settings=settings,
        )
        db.commit()
    assert _derivatives(meeting_id)[0].lifecycle_state == MediaLifecycleState.LEGAL_HOLD

    # A held ORIGINAL with a recorded deletion request is not copied again.
    job_id, meeting_id = _upload(client, tenant, wav, key="deny-held-deleting")
    original_id = _original(meeting_id).id
    with inspection_session_factory()() as db:
        held = db.get(MediaObject, original_id)
        place_legal_hold(
            db,
            media=held,
            reason="litigation",
            review_at=now + timedelta(days=30),
            actor_user_id=None,
            now=now,
            settings=settings,
        )
        request_deletion(
            db, media=held, reason="test", actor_user_id=None, now=now, settings=settings
        )
        db.commit()
    assert _run(job_id, enabled, label="held-deleting")["status"] == "completed"
    assert outcomes[-1] == "source_not_live"
    assert _derivatives(meeting_id) == []
    assert _reasons(original_id) == ["source_not_live"]

    # A live control was registered above; an ORIGINAL that is not live is refused.
    job_id, meeting_id = _upload(client, tenant, wav, key="deny-not-live")
    original_id = _original(meeting_id).id
    with inspection_session_factory()() as db:
        request_deletion(
            db,
            media=db.get(MediaObject, original_id),
            reason="test",
            actor_user_id=None,
            now=now,
            settings=settings,
        )
        db.commit()
    assert _run(job_id, enabled, label="not-live")["status"] == "completed"
    assert outcomes[-1] == "source_not_live"
    assert _derivatives(meeting_id) == []
    assert _reasons(original_id) == ["source_not_live"]
    assert _transcript(meeting_id) == [TRANSCRIPT]

    # Content changed under the job (the row's digest no longer matches the claim).
    job_id, meeting_id = _upload(client, tenant, wav, key="deny-changed")
    original_id = _original(meeting_id).id
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE media_objects SET sha256 = :sha WHERE id = :id"),
            {"sha": "b" * 64, "id": original_id},
        )
        db.commit()
    assert _run(job_id, enabled, label="changed")["status"] == "completed"
    assert outcomes[-1] == "source_changed"
    assert _reasons(original_id) == ["source_changed"]

    # A backfilled ORIGINAL without channel facts: unknown stays unknown.
    job_id, meeting_id = _upload(client, tenant, wav, key="deny-facts")
    original_id = _original(meeting_id).id
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE media_objects SET channel_count = NULL WHERE id = :id"),
            {"id": original_id},
        )
        db.commit()
    assert _run(job_id, enabled, label="facts")["status"] == "completed"
    assert outcomes[-1] == "source_facts_unknown"
    assert _derivatives(meeting_id) == []
    assert _reasons(original_id) == ["source_facts_unknown"]

    # A recording without an ORIGINAL media object (written before the media ledger).
    job_id, meeting_id = _upload(client, tenant, wav, key="deny-unregistered")
    original_id = _original(meeting_id).id
    with inspection_session_factory()() as db:
        db.execute(text("DELETE FROM media_objects WHERE id = :id"), {"id": original_id})
        db.commit()
    assert _run(job_id, enabled, label="unregistered")["status"] == "completed"
    assert outcomes[-1] == "source_not_registered"
    assert _derivatives(meeting_id) == []
    assert [event.details["reason"] for event in _unavailable(None)] == ["source_not_registered"]

    # A workspace that deletes audio after processing gets its derivative, and the job then
    # deletes the derivative with the ORIGINAL (HOM-314).
    other = _tenant(client, email_outbox, "derive-deletes-audio")
    with inspection_session_factory()() as db:
        db.get(Workspace, other["workspace_id"]).delete_audio_after_processing = True
        db.commit()
    job_id, meeting_id = _upload(client, other, wav, key="deny-deletes-audio")
    original_id = _original(meeting_id).id
    assert _run(job_id, enabled, label="deletes-audio")["status"] == "completed"
    assert outcomes[-1] == "registered"
    assert _reasons(original_id) == []
    (derived,) = _derivatives(meeting_id)
    assert derived.derived_from_id == original_id
    storage = create_object_storage(settings)
    for media in (_original(meeting_id), derived):
        assert media.lifecycle_state == MediaLifecycleState.DELETED
        assert media.deletion_reason == "processed" and media.deleted_at is not None
        assert not storage.exists(media.workspace_id, media.storage_key)

    for event in _unavailable(original_id):
        assert set(event.details) == {"producer", "reason", "job_id"}
        assert event.details["producer"] == DERIVATIVE_PRODUCER
        assert event.details["job_id"] == job_id
        assert event.target_type == "media"


# --- A7: failures never fail the job -----------------------------------------------------


def test_a_failed_derivation_does_not_fail_the_job(
    client, email_outbox, settings, outcomes, wav, tmp_path: Path, monkeypatch
) -> None:
    tenant = _tenant(client, email_outbox, "derive-fail")
    enabled = settings.model_copy(update={"media_derivatives_enabled": True})
    cases = {
        "derivative_tool_unavailable": {"ffmpeg_path": str(tmp_path / "missing-ffmpeg")},
        "derivative_failed": {"ffmpeg_path": str(_script(tmp_path / "bad-ffmpeg", "exit 3"))},
        "derivative_timeout": {
            "ffmpeg_path": str(_script(tmp_path / "slow-ffmpeg", "sleep 5")),
            "media_derivative_timeout_seconds": 0.5,
        },
        # A 1 MiB storage cap is below the 4 MiB record reserve: refused before any decode.
        "derivative_too_large": {"max_upload_size_mb": 1},
    }
    for reason, update in cases.items():
        job_id, meeting_id = _upload(client, tenant, wav, key=f"fail-{reason}")
        original_id = _original(meeting_id).id
        result = _run(job_id, enabled.model_copy(update=update), label=reason)
        assert result["status"] == "completed", reason
        assert outcomes[-1] == reason
        assert _transcript(meeting_id) == [TRANSCRIPT], reason
        assert _derivatives(meeting_id) == [], reason
        assert _reasons(original_id) == [reason]
    # An unexpected error inside the step is still audited as derivative_failed.
    job_id, meeting_id = _upload(client, tenant, wav, key="fail-unexpected")
    original_id = _original(meeting_id).id
    with monkeypatch.context() as patch:

        def broken(*_args, **_kwargs):
            raise RuntimeError("unexpected")

        patch.setattr(audio_derivatives, "_derive_and_register", broken)
        assert _run(job_id, enabled, label="unexpected")["status"] == "completed"
    assert outcomes[-1] == "derivative_failed"
    assert _transcript(meeting_id) == [TRANSCRIPT]
    assert _derivatives(meeting_id) == []
    (event,) = _unavailable(original_id)
    assert event.details == {
        "producer": DERIVATIVE_PRODUCER,
        "reason": "derivative_failed",
        "job_id": job_id,
    }
    # Allow-control: the same audio with the real tool is derived.
    job_id, meeting_id = _upload(client, tenant, wav, key="fail-control")
    assert _run(job_id, enabled, label="control")["status"] == "completed"
    assert outcomes[-1] == "registered"


# --- fencing ----------------------------------------------------------------------------


def test_a_stale_attempt_registers_nothing_and_removes_its_object(
    client, email_outbox, settings, outcomes, wav, monkeypatch
) -> None:
    tenant = _tenant(client, email_outbox, "derive-stale")
    enabled = settings.model_copy(update={"media_derivatives_enabled": True})
    storage = create_object_storage(settings)
    job_id, meeting_id = _upload(client, tenant, wav, key="derive-stale")
    original = _original(meeting_id)
    real = audio_derivatives.derive_audio

    def derive_then_lose_the_lease(*args, **kwargs):
        outcome = real(*args, **kwargs)
        # Recovery fences the attempt while it derives: a newer generation owns the job.
        with inspection_session_factory()() as db:
            db.get(ProcessingJob, job_id).lease_generation += 1
            db.commit()
        return outcome

    monkeypatch.setattr(audio_derivatives, "derive_audio", derive_then_lose_the_lease)
    process_job(job_id, worker_id="test-derivative-stale", settings=enabled, providers=_providers())
    assert outcomes == ["stale_attempt"]
    assert _derivatives(meeting_id) == []
    assert _unavailable(original.id) == []  # a stale attempt writes nothing
    keys = {item.key for item in storage.iter_objects(tenant["workspace_id"])}
    assert keys == {original.storage_key}  # its uploaded object was removed


# --- HOM-314 E: the derivative's own size cap --------------------------------------------


def _mono_wav(path: Path, *, seconds: int) -> bytes:
    """One 16 kHz channel: a 700 Hz tone in the first half of every 2 s (lavfi)."""
    subprocess.run(  # noqa: S603 - fixed argv, test-only fixture generation
        [  # noqa: S607
            "ffmpeg",
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc=exprs='0.5*sin(2*PI*700*t)*lt(mod(t,2),1)':s=16000:d={seconds}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path.read_bytes()


def _set_duration(media_id: str, seconds: int | None) -> None:
    """The probed duration of a row (content columns are immutable through the ORM)."""
    with inspection_session_factory()() as db:
        db.execute(
            text("UPDATE media_objects SET duration_seconds = :seconds WHERE id = :id"),
            {"seconds": seconds, "id": media_id},
        )
        db.commit()


def _stored_keys(storage, workspace_id: str, job_id: str) -> set[str]:
    """The tenant's object keys, without the stage checkpoints of ``job_id``."""
    with inspection_session_factory()() as db:
        checkpoints = set(
            db.scalars(
                text("SELECT artifact_key FROM stage_checkpoints WHERE job_id = :job"),
                {"job": job_id},
            )
        )
    return {item.key for item in storage.iter_objects(workspace_id)} - checkpoints


def test_the_derivative_cap_setting_decides_and_a_refusal_keeps_the_original(
    client, email_outbox, settings, outcomes, tmp_path: Path
) -> None:
    tenant = _tenant(client, email_outbox, "derive-cap")
    storage = create_object_storage(settings)
    assert settings.max_upload_size_mb == 250
    # 5 MiB is 5 242 880 bytes; 4 MiB + 64 are reserved, so about 32.7 s of mono PCM fit.
    capped = settings.model_copy(
        update={"media_derivatives_enabled": True, "media_derivative_max_size_mb": 5}
    )

    job_id, meeting_id = _upload(
        client, tenant, _mono_wav(tmp_path / "short.wav", seconds=30), key="cap-under"
    )
    assert _run(job_id, capped, label="cap-under")["status"] == "completed"
    assert outcomes[-1] == "registered"
    (derived,) = _derivatives(meeting_id)
    assert derived.channel_count == 1 and derived.size_bytes <= 5 * 1024 * 1024
    assert _unavailable(_original(meeting_id).id) == []

    # 40 s does not fit: refused from the probed duration, and without one while decoding.
    long_wav = _mono_wav(tmp_path / "long.wav", seconds=40)
    for label, duration in (("cap-predicted", 40), ("cap-produced", None)):
        job_id, meeting_id = _upload(client, tenant, long_wav, key=label)
        original_id = _original(meeting_id).id
        _set_duration(original_id, duration)
        before = _columns(_original(meeting_id))
        digest_before = _digest(storage, _original(meeting_id))
        keys_before = _stored_keys(storage, tenant["workspace_id"], job_id)

        assert _run(job_id, capped, label=label)["status"] == "completed", label
        assert outcomes[-1] == "derivative_too_large", label

        assert _derivatives(meeting_id) == [], label
        (event,) = _unavailable(original_id)
        assert event.target_type == "media"
        assert event.details == {
            "producer": DERIVATIVE_PRODUCER,
            "reason": "derivative_too_large",
            "job_id": job_id,
        }
        assert _stored_keys(storage, tenant["workspace_id"], job_id) == keys_before, label
        assert _columns(_original(meeting_id)) == before, label
        assert _digest(storage, _original(meeting_id)) == digest_before, label
        assert _transcript(meeting_id) == [TRANSCRIPT], label


def test_the_default_derivative_cap_is_200_mib(
    client, email_outbox, settings, outcomes, tmp_path: Path, monkeypatch
) -> None:
    from backend.media import derivatives

    tenant = _tenant(client, email_outbox, "derive-default-cap")
    assert (settings.media_derivative_max_size_mb, settings.max_upload_size_mb) == (200, 250)
    enabled = settings.model_copy(update={"media_derivatives_enabled": True})
    decodes: list[int] = []
    real_decode = derivatives._decode

    def counted(*args, **kwargs):
        decodes.append(1)
        return real_decode(*args, **kwargs)

    monkeypatch.setattr(derivatives, "_decode", counted)
    mono = _mono_wav(tmp_path / "mono.wav", seconds=3)

    # 6423 s predicts 6423 * 32 000 + 4 194 368 = 209 730 368 bytes, over 209 715 200.
    job_id, meeting_id = _upload(client, tenant, mono, key="default-over")
    original_id = _original(meeting_id).id
    _set_duration(original_id, 6423)
    assert _run(job_id, enabled, label="default-over")["status"] == "completed"
    assert outcomes[-1] == "derivative_too_large"
    assert decodes == []  # refused before any decode
    assert _derivatives(meeting_id) == []
    assert _reasons(original_id) == ["derivative_too_large"]

    # 6422 s predicts 209 698 368 bytes: not refused for size.
    job_id, meeting_id = _upload(client, tenant, mono, key="default-under")
    original_id = _original(meeting_id).id
    _set_duration(original_id, 6422)
    assert _run(job_id, enabled, label="default-under")["status"] == "completed"
    assert outcomes[-1] == "registered"
    assert decodes == [1]
    assert _reasons(original_id) == []
