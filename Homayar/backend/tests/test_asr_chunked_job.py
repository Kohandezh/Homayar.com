"""Long-call ASR chunking in the job (HOM-40, ADR 0046).

The real ``process_job`` runs on a generated WAV; ffmpeg decodes it for the
chunks. A fake ASR reads every chunk WAV it gets and answers per chunk.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    CheckpointState,
    ProcessingJob,
    ProcessingStageRun,
    ProviderUsage,
    StageCheckpoint,
    TranscriptSegment,
)
from backend.jobs import asr_chunks, invariants
from backend.jobs import checkpoints as ckpt
from backend.jobs.processor import process_job
from backend.media.activity import measure_activity
from backend.providers.base import ASRProvider, ProviderError
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockIntelligenceProvider
from backend.providers.schemas import (
    ASRLanguageDecision,
    ASRRequest,
    ASRResult,
    ASRSegment,
)
from backend.security.context import CSRF_COOKIE
from backend.speech.chunking import CHUNKING_VERSION, ChunkPlan, ChunkSettings, plan_chunks
from backend.speech.language_policy import normalize_language
from backend.storage import create_object_storage
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _headers, _tenant
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider

pytestmark = pytest.mark.ffmpeg

RATE = 16_000
SMALL = {
    "asr_chunk_min_call_seconds": 6.0,
    "asr_chunk_target_seconds": 4.0,
    "asr_chunk_search_seconds": 1.0,
    "asr_chunk_overlap_seconds": 0.5,
    "job_retry_delay_seconds": 1,
}
POLICY_VERSION = "test-policy/1"


# --- fixtures and helpers ----------------------------------------------------------------


def _generate(path: Path, *, seconds: float, gaps: tuple[tuple[float, float], ...]) -> bytes:
    """16 kHz mono 16-bit tone, silent inside ``gaps`` (lavfi)."""
    silent = "".join(f"*(1-gte(t,{start})*lt(t,{end}))" for start, end in gaps)
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
            f"aevalsrc=exprs='0.5*sin(2*PI*440*t){silent}':s={RATE}:d={seconds}",
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path.read_bytes()


@pytest.fixture(scope="module")
def long_wav(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    # Gaps near 4 s and 8 s: the cuts must land inside them (not at 4 s and 8 s).
    return _generate(
        tmp_path_factory.mktemp("chunked") / "long.wav",
        seconds=13,
        gaps=((3.2, 4.0), (7.7, 8.5)),
    )


@pytest.fixture(scope="module")
def short_wav(tmp_path_factory: pytest.TempPathFactory) -> bytes:
    return _generate(tmp_path_factory.mktemp("chunked") / "short.wav", seconds=5, gaps=())


@pytest.fixture
def no_dispatch(monkeypatch) -> None:
    monkeypatch.setattr("backend.routers.v1.meetings.dispatch_job", lambda *_a, **_k: True)


def _small(settings, **extra):
    return settings.model_copy(update={**SMALL, **extra})


def _chunk_settings(settings) -> ChunkSettings:
    return ChunkSettings(
        min_call_seconds=settings.asr_chunk_min_call_seconds,
        target_seconds=settings.asr_chunk_target_seconds,
        search_seconds=settings.asr_chunk_search_seconds,
        overlap_seconds=settings.asr_chunk_overlap_seconds,
    )


def _plans(content: bytes, settings) -> list[ChunkPlan]:
    with wave.open(io.BytesIO(content)) as reader:
        pcm = reader.readframes(reader.getnframes())
    active = measure_activity(pcm, 1).channels[0].intervals
    return plan_chunks(
        len(pcm) // 2, sample_rate=RATE, settings=_chunk_settings(settings), active=active
    )


def _decision(language: str | None, *, detected: str | None, reason: str | None):
    if language is not None:
        return ASRLanguageDecision(
            source="requested", requested=language, policy_version=POLICY_VERSION
        )
    return ASRLanguageDecision(
        source="fallback" if reason else "detected",
        detected=detected,
        probability=0.9 if detected else None,
        reason=reason,
        policy_version=POLICY_VERSION,
    )


class ChunkASR(ASRProvider):
    """Reads every WAV it gets; answers by chunk index (from the frame count).

    ``fail``: chunk indexes that fail once (retryable). ``empty``: chunks that
    return nothing. ``detect``: per chunk, what auto detection finds
    (``(language, reason)``). A call that is not a planned chunk is the whole call.
    """

    def __init__(
        self,
        plans: list[ChunkPlan],
        *,
        fail: tuple[int, ...] = (),
        empty: tuple[int, ...] = (),
        detect: dict[int, tuple[str, str | None]] | None = None,
        on_call=None,
    ) -> None:
        self.by_frames = {plan.end_sample - plan.start_sample: plan.index for plan in plans}
        assert len(self.by_frames) == len(plans)
        self.fail = set(fail)
        self.empty = set(empty)
        self.detect = detect or {}
        self.on_call = on_call
        self.calls: list[dict] = []

    def language_policy_identity(self, language: str | None) -> dict | None:
        return {"version": POLICY_VERSION} if normalize_language(language) is None else None

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        path = Path(request.audio_path)
        data = path.read_bytes()
        with wave.open(str(path)) as reader:
            frames = reader.getnframes()
            shape = (reader.getframerate(), reader.getnchannels(), reader.getsampwidth())
        index = self.by_frames.get(frames)
        self.calls.append(
            {
                "index": index,
                "frames": frames,
                "shape": shape,
                "language": request.language,
                "name": path.name,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
        if self.on_call is not None:
            self.on_call(index)
        if index in self.fail:
            self.fail.discard(index)
            raise ProviderError("asr_temporarily_unavailable", "Try again", retryable=True)
        explicit = normalize_language(request.language)
        detected, reason = self.detect.get(index, ("fa", None))
        language = explicit or detected
        decision = _decision(explicit, detected=detected, reason=reason)
        if index in self.empty:
            return ASRResult(
                provider="chunk_fake",
                model="fake-1",
                language=language,
                text="",
                is_mock=True,
                language_decision=decision,
            )
        text = "کل تماس" if index is None else f"بخش {index} گفته شد"
        return ASRResult(
            provider="chunk_fake",
            model="fake-1",
            language=language,
            text=text,
            segments=[
                ASRSegment(index=0, text=text, start_seconds=0.2, end_seconds=frames / RATE - 0.2)
            ],
            duration_seconds=frames / RATE,
            is_mock=True,
            language_decision=decision,
        )


def _bundle(asr: ASRProvider) -> ProviderBundle:
    return ProviderBundle(
        asr=asr,
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=MockIntelligenceProvider(allow_mock=True),
    )


def _upload(client, tenant: dict, content: bytes, *, key: str, language: str = "fa"):
    response = client.post(
        "/api/v1/uploads",
        headers=_headers(tenant, **{"Idempotency-Key": f"chunked-{key}"}),
        cookies=tenant["cookies"],
        data={"title": "Long call", "language": language},
        files={"file": ("call.wav", content, "audio/wav")},
    )
    assert response.status_code == 202, response.text
    return response.json()["job"]["id"], response.json()["meeting"]["id"]


def _run(job_id: str, settings, asr: ASRProvider, *, label: str = "w", storage=None) -> dict:
    return process_job(
        job_id,
        worker_id=f"test-chunked-{label}",
        settings=settings,
        providers=_bundle(asr),
        storage=storage,
    )


def _make_due(job_id: str) -> None:
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        job.retry_after = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()


def _checkpoints(meeting_id: str) -> list[StageCheckpoint]:
    with inspection_session_factory()() as db:
        rows = list(
            db.scalars(
                select(StageCheckpoint)
                .where(StageCheckpoint.meeting_id == meeting_id)
                .order_by(StageCheckpoint.created_at.asc(), StageCheckpoint.id.asc())
            )
        )
        for row in rows:
            db.expunge(row)
        return rows


def _published(meeting_id: str, stage: str) -> list[StageCheckpoint]:
    return [
        row
        for row in _checkpoints(meeting_id)
        if row.stage == stage and row.state == CheckpointState.PUBLISHED
    ]


def _artifact(storage, row: StageCheckpoint) -> ASRResult:
    result = ckpt.load_artifact(
        storage,
        workspace_id=row.workspace_id,
        key=row.artifact_key,
        expected_sha256=row.artifact_sha256,
        expected_size=row.artifact_size_bytes,
        schema=ASRResult,
        max_bytes=10_000_000,
    )
    assert isinstance(result, ASRResult)
    return result


def _stage_runs(job_id: str) -> list[tuple[int, str, str, str | None]]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(ProcessingStageRun)
            .where(ProcessingStageRun.job_id == job_id)
            .order_by(ProcessingStageRun.attempt.asc(), ProcessingStageRun.sequence.asc())
        ).all()
        return [(r.attempt, r.stage, r.state.value, r.reason_code) for r in rows]


def _usage(job_id: str) -> list[tuple[str, int | None]]:
    with inspection_session_factory()() as db:
        return sorted(
            (row.stage, row.audio_seconds)
            for row in db.scalars(select(ProviderUsage).where(ProviderUsage.job_id == job_id))
        )


def _transcript(meeting_id: str) -> list[tuple[int, int, str]]:
    with inspection_session_factory()() as db:
        return [
            (row.start_ms, row.end_ms, row.text)
            for row in db.scalars(
                select(TranscriptSegment)
                .where(TranscriptSegment.meeting_id == meeting_id)
                .order_by(TranscriptSegment.start_ms.asc())
            )
        ]


def _expected(plans: list[ChunkPlan], *, skip: tuple[int, ...] = ()) -> list[tuple[int, int, str]]:
    """What the fake says per chunk, in original time (every segment is owned)."""
    return [
        (
            round((plan.start_sample / RATE + 0.2) * 1000),
            round((plan.end_sample / RATE - 0.2) * 1000),
            f"بخش {plan.index} گفته شد",
        )
        for plan in plans
        if plan.index not in skip
    ]


# --- AC4: short calls and chunking off do not change ------------------------------------


@pytest.mark.parametrize(
    "case",
    ["short", "disabled", "default_threshold"],
)
def test_a_short_call_or_chunking_off_runs_as_before(
    client, email_outbox, settings, no_dispatch, short_wav, long_wav, case
):
    tenant = _tenant(client, email_outbox, f"chunk-short-{case}")
    storage = create_object_storage(settings)
    content = short_wav if case == "short" else long_wav
    run_settings = {
        "short": _small(settings),
        "disabled": _small(settings, asr_chunking_enabled=False),
        "default_threshold": settings,
    }[case]
    job_id, meeting_id = _upload(client, tenant, content, key=f"short-{case}")
    asr = ChunkASR([])
    assert _run(job_id, run_settings, asr)["status"] == "completed"

    (call,) = asr.calls
    assert call["name"] == "recording.wav"  # the materialized ORIGINAL, as before
    assert call["sha256"] == hashlib.sha256(content).hexdigest()
    (published,) = _published(meeting_id, "asr")
    assert set(published.input_manifest["request"]) == {"language", "beam_size", "vad_filter"}
    assert "chunking" not in str(published.input_manifest)
    assert _published(meeting_id, "asr_chunk") == []
    assert _artifact(storage, published).chunking is None
    with storage.open_reader(published.workspace_id, published.artifact_key) as reader:
        assert b'"chunking"' not in reader.read()  # the artifact bytes are as before
    assert published.artifact_key.endswith(f"art-{published.attempt_id}-asr")


# --- AC5, AC7, AC8: a long call is transcribed in chunks --------------------------------


def test_a_long_call_is_transcribed_in_chunks(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-long")
    storage = create_object_storage(settings)
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)
    assert len(plans) == 3
    # The cuts are inside the silent gaps, not at the target.
    assert 3.2 * RATE <= plans[0].owned_end_sample <= 4.0 * RATE
    assert 7.7 * RATE <= plans[1].owned_end_sample <= 8.5 * RATE
    assert plans[0].owned_end_sample != 4 * RATE

    job_id, meeting_id = _upload(client, tenant, long_wav, key="long")
    asr = ChunkASR(plans)
    assert _run(job_id, run_settings, asr)["status"] == "completed"

    assert [call["index"] for call in asr.calls] == [0, 1, 2]
    for call, plan in zip(asr.calls, plans, strict=True):
        assert call["shape"] == (RATE, 1, 2)
        assert call["frames"] == plan.end_sample - plan.start_sample
        assert call["language"] == "fa"
    assert _transcript(meeting_id) == _expected(plans)

    runs = _stage_runs(job_id)
    assert [(stage, state) for _attempt, stage, state, _reason in runs] == [
        ("materialize", "completed"),
        ("asr", "completed"),
        ("diarization", "completed"),
        ("intelligence", "completed"),
    ]

    chunks = _published(meeting_id, "asr_chunk")
    assert len(chunks) == 3
    assert len({row.artifact_key for row in chunks}) == 3
    for row in chunks:
        assert isinstance(_artifact(storage, row), ASRResult)
    (merged_row,) = _published(meeting_id, "asr")
    assert merged_row.input_manifest["request"]["chunking"]["version"] == CHUNKING_VERSION
    merged = _artifact(storage, merged_row)
    assert merged.chunking is not None
    assert merged.chunking.version == CHUNKING_VERSION
    assert [
        (
            chunk.index,
            chunk.start_seconds,
            chunk.end_seconds,
            chunk.owned_start_seconds,
            chunk.owned_end_seconds,
        )
        for chunk in merged.chunking.chunks
    ] == [
        (
            plan.index,
            plan.start_sample / RATE,
            plan.end_sample / RATE,
            plan.owned_start_sample / RATE,
            plan.owned_end_sample / RATE,
        )
        for plan in plans
    ]
    assert merged.duration_seconds == plans[-1].end_sample / RATE
    assert merged.text == " ".join(text for _start, _end, text in _expected(plans))
    assert merged.is_mock is True

    # AC8: one ASR usage row with the recording duration; none for the chunks.
    usage = _usage(job_id)
    assert [row for row in usage if row[0].startswith("asr")] == [("asr", 13)]
    assert [stage for stage, _seconds in usage] == ["asr", "diarization", "intelligence"]
    with inspection_session_factory()() as db:
        assert invariants.check_workspace(
            db, tenant["workspace_id"], settings=settings, storage=storage
        ) == []


# --- AC6: resume from the finished chunks ------------------------------------------------


def test_a_retry_resumes_from_the_finished_chunks(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-resume")
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)

    clean_job, clean_meeting = _upload(client, tenant, long_wav, key="resume-clean")
    assert _run(clean_job, run_settings, ChunkASR(plans), label="clean")["status"] == "completed"

    job_id, meeting_id = _upload(client, tenant, long_wav, key="resume-flaky")
    asr = ChunkASR(plans, fail=(1,))
    assert _run(job_id, run_settings, asr, label="a1")["status"] == "retry_scheduled"
    assert [call["index"] for call in asr.calls] == [0, 1]
    assert len(_published(meeting_id, "asr_chunk")) == 1
    _make_due(job_id)
    asr.calls.clear()
    assert _run(job_id, run_settings, asr, label="a2")["status"] == "completed"
    assert [call["index"] for call in asr.calls] == [1, 2]
    assert _transcript(meeting_id) == _transcript(clean_meeting) == _expected(plans)
    assert len(_published(meeting_id, "asr_chunk")) == 3
    assert [stage for stage, _seconds in _usage(job_id)] == ["asr", "diarization", "intelligence"]


def test_a_changed_chunk_setting_reuses_no_chunk(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-changed")
    first_settings = _small(settings)
    first_plans = _plans(long_wav, first_settings)
    job_id, meeting_id = _upload(client, tenant, long_wav, key="changed")
    asr = ChunkASR(first_plans, fail=(2,))
    assert _run(job_id, first_settings, asr, label="a1")["status"] == "retry_scheduled"
    assert len(_published(meeting_id, "asr_chunk")) == 2
    _make_due(job_id)

    second_settings = _small(settings, asr_chunk_target_seconds=4.5)
    second_plans = _plans(long_wav, second_settings)
    assert second_plans != first_plans
    second = ChunkASR(second_plans)
    assert _run(job_id, second_settings, second, label="a2")["status"] == "completed"
    assert [call["index"] for call in second.calls] == [plan.index for plan in second_plans]
    assert _transcript(meeting_id) == _expected(second_plans)


# --- AC9: empty chunks --------------------------------------------------------------------


def test_every_chunk_empty_ends_as_an_empty_transcript(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-empty")
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)
    job_id, meeting_id = _upload(client, tenant, long_wav, key="empty-all")
    asr = ChunkASR(plans, empty=(0, 1, 2))
    assert _run(job_id, run_settings, asr)["status"] in {"completed", "partial"}
    assert len(asr.calls) == 3
    assert [(stage, state, reason) for _a, stage, state, reason in _stage_runs(job_id)] == [
        ("materialize", "completed", None),
        ("asr", "insufficient_evidence", "empty_transcript"),
        ("diarization", "skipped", "no_transcript"),
        ("intelligence", "skipped", "no_transcript"),
    ]
    assert [row for row in _checkpoints(meeting_id) if row.stage.startswith("asr")] == []
    assert _transcript(meeting_id) == []


def test_an_empty_chunk_has_no_checkpoint_and_the_rest_complete(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-partly")
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)
    job_id, meeting_id = _upload(client, tenant, long_wav, key="empty-one")
    asr = ChunkASR(plans, empty=(1,))
    assert _run(job_id, run_settings, asr)["status"] == "completed"
    assert _transcript(meeting_id) == _expected(plans, skip=(1,))
    storage = create_object_storage(settings)
    chunks = _published(meeting_id, "asr_chunk")
    assert sorted(row.input_manifest["request"]["chunk"]["index"] for row in chunks) == [0, 2]
    assert all(_artifact(storage, row).segments for row in chunks)
    assert len(_published(meeting_id, "asr")) == 1


# --- AC10: language for an auto request ---------------------------------------------------


def test_the_first_chunk_with_speech_decides_the_language(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-language")
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)
    job_id, meeting_id = _upload(client, tenant, long_wav, key="language", language="auto")
    asr = ChunkASR(plans, empty=(0,), detect={0: ("fa", "no_speech"), 1: ("en", None)})
    assert _run(job_id, run_settings, asr)["status"] == "completed"
    assert [(call["index"], call["language"]) for call in asr.calls] == [
        (0, "auto"),
        (1, "auto"),
        (2, "en"),
    ]
    storage = create_object_storage(settings)
    (merged_row,) = _published(meeting_id, "asr")
    merged = _artifact(storage, merged_row)
    assert merged.language == "en"
    assert merged.language_decision is not None
    assert (merged.language_decision.source, merged.language_decision.detected) == (
        "detected",
        "en",
    )
    assert merged_row.input_manifest["request"]["language_policy"] == {
        "version": POLICY_VERSION
    }


# --- D6: cancellation between chunks, deletion retires the chunk checkpoints -------------


def test_a_cancelled_job_stops_before_the_next_chunk(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-cancel")
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)
    job_id, meeting_id = _upload(client, tenant, long_wav, key="cancel")

    def delete_meeting(index):
        response = client.delete(
            f"/api/v1/meetings/{meeting_id}",
            headers=_headers(tenant),
            cookies=tenant["cookies"],
        )
        assert response.status_code == 204, response.text

    asr = ChunkASR(plans, empty=(0,), on_call=delete_meeting)
    assert _run(job_id, run_settings, asr)["status"] == "cancelled"
    assert [call["index"] for call in asr.calls] == [0]


def test_deleting_the_meeting_retires_the_chunk_checkpoints(
    client, email_outbox, settings, no_dispatch, long_wav
):
    tenant = _tenant(client, email_outbox, "chunk-delete")
    storage = create_object_storage(settings)
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)
    job_id, meeting_id = _upload(client, tenant, long_wav, key="delete")
    assert _run(job_id, run_settings, ChunkASR(plans))["status"] == "completed"
    rows = _checkpoints(meeting_id)
    assert sorted(row.stage for row in rows) == [
        "asr",
        "asr_chunk",
        "asr_chunk",
        "asr_chunk",
        "diarization",
        "intelligence",
    ]
    keys = [row.artifact_key for row in rows]
    assert all(storage.exists(tenant["workspace_id"], key) for key in keys)

    response = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers={
            "X-Workspace-ID": tenant["workspace_id"],
            "X-CSRF-Token": tenant["cookies"][CSRF_COOKIE],
        },
        cookies=tenant["cookies"],
    )
    assert response.status_code == 204, response.text
    rows = _checkpoints(meeting_id)
    assert {(row.state, row.invalidation_reason) for row in rows} == {
        (CheckpointState.INVALIDATED, "meeting_deleted")
    }
    assert all(row.object_deleted_at is not None for row in rows)
    assert not any(storage.exists(tenant["workspace_id"], key) for key in keys)


def test_the_sweep_removes_an_unpublished_chunk_object_and_keeps_the_published(
    client, email_outbox, settings, no_dispatch, long_wav, monkeypatch
):
    tenant = _tenant(client, email_outbox, "chunk-sweep")
    workspace_id = tenant["workspace_id"]
    storage = create_object_storage(settings)
    run_settings = _small(settings)
    plans = _plans(long_wav, run_settings)
    job_id, meeting_id = _upload(client, tenant, long_wav, key="sweep")
    original = ckpt.publish_checkpoint
    publishes = {"n": 0}

    def crash_on_the_second_chunk(*args, **kwargs):
        publishes["n"] += 1
        if publishes["n"] == 2:
            raise RuntimeError("simulated worker death after the chunk object write")
        return original(*args, **kwargs)

    monkeypatch.setattr(ckpt, "publish_checkpoint", crash_on_the_second_chunk)
    asr = ChunkASR(plans)
    assert _run(job_id, run_settings, asr, label="a1", storage=storage)["status"] == (
        "retry_scheduled"
    )
    (orphan,) = [row for row in _checkpoints(meeting_id) if row.state == CheckpointState.PENDING]
    assert orphan.stage == "asr_chunk"
    assert storage.exists(workspace_id, orphan.artifact_key)

    _make_due(job_id)
    asr.calls.clear()
    assert _run(job_id, run_settings, asr, label="a2", storage=storage)["status"] == "completed"
    assert [call["index"] for call in asr.calls] == [1, 2]  # a PENDING chunk is never reused
    published = [row for row in _checkpoints(meeting_id) if row.state == CheckpointState.PUBLISHED]

    later = datetime.now(UTC) + timedelta(seconds=settings.job_artifact_grace_seconds + 1)
    assert ckpt.sweep_abandoned_artifacts(settings, storage=storage, now=later)["deleted"] == 1
    assert not storage.exists(workspace_id, orphan.artifact_key)
    assert all(storage.exists(workspace_id, row.artifact_key) for row in published)
    refreshed = {row.id: row for row in _checkpoints(meeting_id)}[orphan.id]
    assert (refreshed.state, refreshed.invalidation_reason) == (
        CheckpointState.ABANDONED,
        "not_published",
    )


def test_the_decode_deadline_grows_with_the_recorded_duration(settings, tmp_path, monkeypatch):
    seen: list[float] = []

    def fake_decode(_settings, _source, *, output, meter, cap, timeout):
        seen.append(timeout)
        output.write(b"\x00\x00" * 320)
        return 640

    monkeypatch.setattr(asr_chunks, "_decode", fake_decode)
    assert settings.media_derivative_timeout_seconds == 300
    for seconds in (60, 3000, 14_400):
        work_dir = tmp_path / str(seconds)
        work_dir.mkdir()
        decoded = asr_chunks.decode_call(
            tmp_path / "call.wav", work_dir=work_dir, settings=settings, duration_seconds=seconds
        )
        assert decoded is not None and decoded.total_samples == 320
    # The operator's floor for short calls, 0.1 s per recorded second for long ones.
    assert seen == [300.0, 300.0, 1440.0]
