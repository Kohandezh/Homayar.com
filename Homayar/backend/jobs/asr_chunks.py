"""Long-call ASR in chunks inside the job (HOM-40, ADR 0046).

The ASR stage decodes the ORIGINAL once with ffmpeg to mono 16 kHz signed
16-bit PCM (what faster-whisper decodes a path to) and streams it to a file in
the job's temporary directory; the same stream feeds ``signal-activity/1``,
whose quiet gaps are the cut places. Each planned chunk is written as a small
WAV next to it and sent through the unchanged provider interface. The whole
call's PCM is never held in memory.

This module knows nothing about the database: the processor passes in how to
build a chunk identity, resume, call the provider, commit and check the lease.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import wave
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from backend.config import Settings
from backend.media.activity import ACTIVITY_VERSION, ActivityMeter
from backend.media.channels import _stop
from backend.media.derivatives import RESAMPLER
from backend.media.validation import (
    _MAX_ANALYZE_DURATION_MICROSECONDS,
    _MAX_FFPROBE_ALLOCATION_BYTES,
    _MAX_PROBE_SIZE_BYTES,
)
from backend.providers.schemas import ASRChunking, ASRChunkSpan, ASRRequest, ASRResult
from backend.services.logging_service import logger
from backend.speech.chunking import (
    CHUNKING_VERSION,
    ChunkPlan,
    ChunkSettings,
    merge_chunk_segments,
)
from backend.speech.language_policy import normalize_language

SAMPLE_RATE_HZ: Final = 16_000
# The chunk audio: the decode below (mono, 16 kHz, s16, the pinned resampler).
# A change to it changes what the provider hears, so it is a new version.
CHUNK_AUDIO_VERSION: Final = "asr-chunk-audio/1"
# A decode may run this far past the longest accepted upload before it is refused.
_DURATION_SLACK_SECONDS: Final = 60
# Decode time limit per second of recorded audio, on top of the operator's floor
# (MEDIA_DERIVATIVE_TIMEOUT_SECONDS): a 4-hour call gets 1440 s.
DECODE_TIMEOUT_FACTOR: Final = 0.1
_SAMPLE_BYTES: Final = 2
_READ_CHUNK_BYTES: Final = 64 * 1024
_COPY_BYTES: Final = 1024 * 1024
_POSIX: Final = os.name == "posix"


def chunking_identity(settings: ChunkSettings) -> dict[str, Any]:
    """The chunking part of a checkpoint identity: the plan and how its audio is made."""
    return {**settings.descriptor(), "audio": CHUNK_AUDIO_VERSION, "activity": ACTIVITY_VERSION}


def is_empty(result: ASRResult) -> bool:
    return not result.text.strip() and not result.segments


# --- decode ---------------------------------------------------------------------------


def build_decode_argv(executable: str, source: Path) -> list[str]:
    """The bounded decode: stdout is raw ``s16le``, one channel, 16 kHz."""
    return [
        executable,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-nostats",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-probesize",
        str(_MAX_PROBE_SIZE_BYTES),
        "-analyzeduration",
        str(_MAX_ANALYZE_DURATION_MICROSECONDS),
        "-max_alloc",
        str(_MAX_FFPROBE_ALLOCATION_BYTES),
        "-i",
        f"file:{Path(source).absolute()}",
        "-map",
        "0:a:0",
        "-vn",
        "-sn",
        "-dn",
        "-af",
        RESAMPLER,
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "-",
    ]


class _DecodeFailed(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DecodedCall:
    pcm_path: Path  # raw mono s16le at 16 kHz in the job's temporary directory
    total_samples: int
    active: tuple[tuple[int, int], ...]  # signal-activity/1 intervals, half-open samples


def decode_deadline_seconds(settings: Settings, duration_seconds: float) -> float:
    """The decode's time limit: the operator's floor, longer for a long call."""
    return max(
        float(settings.media_derivative_timeout_seconds),
        DECODE_TIMEOUT_FACTOR * duration_seconds,
    )


def decode_call(
    source: Path, *, work_dir: Path, settings: Settings, duration_seconds: float
) -> DecodedCall | None:
    """Decode the call once; ``None`` (logged) when it cannot be decoded.

    ``duration_seconds`` is the recorded length; it scales the time limit.
    The caller then sends the call as one request, as before chunking.
    """
    pcm_path = work_dir / "asr-chunks.pcm"
    cap = (settings.max_audio_duration_seconds + _DURATION_SLACK_SECONDS) * (
        SAMPLE_RATE_HZ * _SAMPLE_BYTES
    )
    meter = ActivityMeter(1)
    try:
        with pcm_path.open("xb") as output:
            os.chmod(pcm_path, 0o600)
            size = _decode(
                settings,
                source,
                output=output,
                meter=meter,
                cap=cap,
                timeout=decode_deadline_seconds(settings, duration_seconds),
            )
        if size == 0 or size % _SAMPLE_BYTES:
            raise _DecodeFailed("decode_empty" if size == 0 else "decode_failed")
    except (_DecodeFailed, OSError) as exc:
        pcm_path.unlink(missing_ok=True)
        logger.warning(
            "ASR_CHUNK_DECODE_UNAVAILABLE reason=%s",
            exc.reason if isinstance(exc, _DecodeFailed) else type(exc).__name__,
        )
        return None
    return DecodedCall(
        pcm_path=pcm_path,
        total_samples=size // _SAMPLE_BYTES,
        active=meter.finish().channels[0].intervals,
    )


def _decode(
    settings: Settings,
    source: Path,
    *,
    output: Any,
    meter: ActivityMeter,
    cap: int,
    timeout: float,
) -> int:
    """Run the decoder; stream its stdout to ``output`` and ``meter``; return the byte count."""
    try:
        executable = os.fspath(settings.ffmpeg_path)
    except TypeError:
        raise _DecodeFailed("decode_tool_unavailable") from None
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise _DecodeFailed("decode_tool_unavailable")
    try:
        # The executable is operator-controlled and every input is an argv element.
        process = subprocess.Popen(  # noqa: S603
            build_decode_argv(executable, source),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            shell=False,
            close_fds=True,
            # Its own process group, so the deadline kill also reaches any child.
            start_new_session=_POSIX,
        )
    except (FileNotFoundError, PermissionError):
        raise _DecodeFailed("decode_tool_unavailable") from None
    except OSError:
        raise _DecodeFailed("decode_failed") from None
    expired = threading.Event()

    def expire() -> None:
        expired.set()
        if _POSIX:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        else:
            process.kill()

    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    assert process.stdout is not None
    size = 0
    try:
        descriptor = process.stdout.fileno()
        while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
            size += len(chunk)
            if size > cap:
                raise _DecodeFailed("decode_too_long")
            output.write(chunk)
            meter.feed(chunk)
        returncode = process.wait()
    finally:
        timer.cancel()
        _stop(process)
        process.stdout.close()
    if expired.is_set():
        raise _DecodeFailed("decode_timeout")
    if returncode != 0:
        raise _DecodeFailed("decode_failed")
    return size


def write_chunk_wav(decoded: DecodedCall, plan: ChunkPlan, path: Path) -> Path:
    """The chunk's samples ``[start_sample, end_sample)`` as a mono 16 kHz PCM WAV."""
    remaining = (plan.end_sample - plan.start_sample) * _SAMPLE_BYTES
    with decoded.pcm_path.open("rb") as pcm, wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(_SAMPLE_BYTES)
        output.setframerate(SAMPLE_RATE_HZ)
        pcm.seek(plan.start_sample * _SAMPLE_BYTES)
        while remaining > 0:
            block = pcm.read(min(_COPY_BYTES, remaining))
            if not block:
                raise OSError("the decoded audio ended before the chunk")
            output.writeframesraw(block)
            remaining -= len(block)
    return path


# --- the chunk loop -------------------------------------------------------------------


def _decides(result: ASRResult) -> bool:
    """Whether this chunk fixes the language of an auto request (ADR 0046, D8)."""
    if result.language_decision is None:
        # An adapter without a language policy: its language counts once it heard speech.
        return not is_empty(result)
    return result.language_decision.reason != "no_speech"


def transcribe_chunks[Identity](
    plans: Sequence[ChunkPlan],
    decoded: DecodedCall,
    *,
    work_dir: Path,
    language: str,
    request_identity: Mapping[str, Any],
    chunking: Mapping[str, Any],
    identity_for: Callable[[dict[str, Any], int], Identity],
    resume: Callable[[Identity], ASRResult | None],
    transcribe: Callable[[ASRRequest], ASRResult],
    commit: Callable[[Identity, ASRResult], None],
    before_chunk: Callable[[], None],
) -> ASRResult:
    """Transcribe every chunk in order and return the merged result (empty when all are).

    A chunk with a published checkpoint of the same identity is reused; a new
    non-empty chunk result is committed before the next chunk starts.
    """
    auto = normalize_language(language) is None
    chunk_language = language
    decider: int | None = None
    results: list[ASRResult] = []
    for plan in plans:
        before_chunk()
        identity = identity_for(
            {
                **request_identity,
                "chunking": dict(chunking),
                "chunk": {
                    "index": plan.index,
                    "start_sample": plan.start_sample,
                    "end_sample": plan.end_sample,
                    "language": chunk_language,
                },
            },
            plan.index,
        )
        result = resume(identity)
        if result is None:
            path = write_chunk_wav(decoded, plan, work_dir / f"asr-chunk-{plan.index}.wav")
            try:
                result = transcribe(ASRRequest(audio_path=str(path), language=chunk_language))
            finally:
                path.unlink(missing_ok=True)
            # Like the whole stage: an empty result is never a checkpoint.
            if not is_empty(result):
                commit(identity, result)
        results.append(result)
        if auto and decider is None and _decides(result):
            decider = plan.index
            chunk_language = result.language
    return merge_results(plans, results, decider=decider)


def merge_results(
    plans: Sequence[ChunkPlan],
    results: Sequence[ASRResult],
    *,
    decider: int | None,
) -> ASRResult:
    """One ``ASRResult`` in original time that records the chunks (ADR 0046, D7)."""
    if decider is None:
        decider = next((index for index, item in enumerate(results) if not is_empty(item)), 0)
    source = results[decider]
    segments = merge_chunk_segments(plans, results)
    return ASRResult(
        provider=source.provider,
        model=source.model,
        language=source.language,
        language_decision=source.language_decision,
        text=" ".join(segment.text for segment in segments),
        segments=segments,
        duration_seconds=plans[-1].end_sample / SAMPLE_RATE_HZ,
        is_mock=any(item.is_mock for item in results),
        chunking=ASRChunking(
            version=CHUNKING_VERSION,
            chunks=[
                ASRChunkSpan(
                    index=plan.index,
                    start_seconds=plan.start_sample / plan.sample_rate,
                    end_seconds=plan.end_sample / plan.sample_rate,
                    owned_start_seconds=plan.owned_start_sample / plan.sample_rate,
                    owned_end_seconds=plan.owned_end_sample / plan.sample_rate,
                )
                for plan in plans
            ],
        ),
    )
