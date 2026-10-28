"""Channel-preserving normalized derivative with its record (HOM-38, ADR 0042).

One bounded ffmpeg decode turns the primary audio stream of an ORIGINAL
(``-map 0:a:0``) into signed 16-bit PCM at 16 kHz.  Every channel stays
separate and in its order (no ``-ac``, no ``pan``, no ``amix``); there is no
gain (no ``loudnorm``, no ``volume``).  The resampler is pinned to the swr
defaults, which keep every sample in place (zero shift; the length stays
between ``floor`` and ``ceil`` of ``N_o * 16000 / R_o``).  The derivative keeps
exactly the samples the resampler emits: never padded, never trimmed.

The same PCM stream feeds the ``signal-activity/1`` heuristic
(:mod:`backend.media.activity`).  The record ``audio-derivative/1`` (source
facts, format, the exact time map of :mod:`backend.media.time_map`, the
activity) is written **inside the derivative's own bytes**: a plain PCM WAV
(format tag 1, no channel mask, so no reader infers speaker positions) with
one ``hmyr`` chunk of canonical JSON before ``data``.  The record therefore
has exactly the derivative's lifecycle, tenant and digest.  No migration.

Nothing here touches the database or the object storage.  A media or tool
problem is never raised: :func:`derive_audio` returns a
:class:`DerivativeUnavailable` with a reason code instead.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
import struct
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from backend.config import Settings
from backend.media.activity import (
    FRAME_SAMPLES,
    ActivityMeter,
    ActivityResult,
    activity_record,
    inspection_status,
)
from backend.media.channels import _stop
from backend.media.time_map import TimeMap
from backend.media.validation import (
    _MAX_ANALYZE_DURATION_MICROSECONDS,
    _MAX_FFPROBE_ALLOCATION_BYTES,
    _MAX_PROBE_SIZE_BYTES,
)

DERIVATIVE_PRODUCER: Final = "audio-derivative/1"
DERIVATIVE_SAMPLE_RATE_HZ: Final = 16_000
DERIVATIVE_CONTENT_TYPE: Final = "audio/wav"
DERIVATIVE_CONTAINER: Final = "wav"
RECORD_CHUNK_ID: Final = b"hmyr"
RECORD_MAX_BYTES: Final = 4 * 1024 * 1024
MAX_CHANNELS: Final = 8
# The swr defaults of ffmpeg 6.1 and 8.1, written out so both builds run the same resampler.
RESAMPLER: Final = (
    "aresample=osr=16000:resampler=swr:filter_size=32:phase_shift=10:linear_interp=1"
    ":exact_rational=1:dither_method=0:async=0"
)
# RIFF, WAVE, the fmt chunk and the two chunk headers fit in this; the record is extra.
_HEADER_BYTES: Final = 64
_SAMPLE_BYTES: Final = 2
_READ_CHUNK_BYTES: Final = 64 * 1024
_POSIX: Final = os.name == "posix"

# Reason codes (SPEC 5.6). Pre-check, in order:
DERIVATIVES_DISABLED: Final = "derivatives_disabled"
SOURCE_NOT_REGISTERED: Final = "source_not_registered"
SOURCE_NOT_LIVE: Final = "source_not_live"
SOURCE_CHANGED: Final = "source_changed"
SOURCE_FACTS_UNKNOWN: Final = "source_facts_unknown"
ALREADY_DERIVED: Final = "already_derived"
DERIVATIVE_TOO_LARGE: Final = "derivative_too_large"
# The run:
DERIVATIVE_TOOL_UNAVAILABLE: Final = "derivative_tool_unavailable"
DERIVATIVE_TIMEOUT: Final = "derivative_timeout"
DERIVATIVE_FAILED: Final = "derivative_failed"
DERIVATIVE_EMPTY: Final = "derivative_empty"
RECORD_TOO_LARGE: Final = "record_too_large"
STORAGE_FAILED: Final = "storage_failed"
# The registration:
STALE_ATTEMPT: Final = "stale_attempt"
JOB_CANCELLED: Final = "job_cancelled"
MEETING_DELETED: Final = "meeting_deleted"
STORAGE_CONFLICT: Final = "storage_conflict"
TENANT_MISMATCH: Final = "tenant_mismatch"

REASON_CODES: Final = (
    DERIVATIVES_DISABLED,
    SOURCE_NOT_REGISTERED,
    SOURCE_NOT_LIVE,
    SOURCE_CHANGED,
    SOURCE_FACTS_UNKNOWN,
    ALREADY_DERIVED,
    DERIVATIVE_TOO_LARGE,
    DERIVATIVE_TOOL_UNAVAILABLE,
    DERIVATIVE_TIMEOUT,
    DERIVATIVE_FAILED,
    DERIVATIVE_EMPTY,
    RECORD_TOO_LARGE,
    STORAGE_FAILED,
    STALE_ATTEMPT,
    JOB_CANCELLED,
    MEETING_DELETED,
    STORAGE_CONFLICT,
    TENANT_MISMATCH,
)


class DerivativeRecordInvalid(ValueError):
    """A derivative header that is not the ``audio-derivative/1`` shape."""

    code = "derivative_record_invalid"


class RecordTooLarge(ValueError):
    """The record JSON is over ``RECORD_MAX_BYTES``."""

    code = RECORD_TOO_LARGE


@dataclass(frozen=True)
class SourceFacts:
    """What the derivation needs to know about the ORIGINAL (from its media row)."""

    media_id: str
    sha256: str
    sample_rate_hz: int
    channel_count: int
    codec: str | None
    duration_seconds: float | None


@dataclass(frozen=True)
class DerivedAudio:
    path: Path  # the complete WAV in the work directory
    sha256: str
    size_bytes: int
    record: dict[str, Any]
    channel_count: int
    sample_count: int


@dataclass(frozen=True)
class DerivativeUnavailable:
    reason: str


@dataclass(frozen=True)
class DerivativeHeader:
    record: dict[str, Any]
    channel_count: int
    sample_rate_hz: int
    data_offset: int
    data_size: int


# --- the argv, the record and the header (pure) ------------------------------------------


def build_derivative_argv(executable: str, source: Path) -> list[str]:
    """The bounded decode: stdout is raw ``s16le`` at 16 kHz, every channel kept."""
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
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "-",
    ]


def canonical_record(record: dict[str, Any]) -> bytes:
    return json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("ascii")


def derivative_record(
    facts: SourceFacts,
    *,
    inspection: dict[str, Any] | None,
    sample_count: int,
    activity: ActivityResult,
) -> dict[str, Any]:
    """The ``audio-derivative/1`` record: no timestamps, no attempt or job ids."""
    channels = facts.channel_count
    return {
        "version": DERIVATIVE_PRODUCER,
        "source": {
            "media_id": facts.media_id,
            "sha256": facts.sha256,
            "channel_count": channels,
            "sample_rate_hz": facts.sample_rate_hz,
            "codec": facts.codec,
            "narrowband": facts.sample_rate_hz < DERIVATIVE_SAMPLE_RATE_HZ,
        },
        "format": {
            "container": DERIVATIVE_CONTAINER,
            "codec": "pcm_s16le",
            "sample_rate_hz": DERIVATIVE_SAMPLE_RATE_HZ,
            "channel_count": channels,
            "channel_map": list(range(channels)),
            "gain": "none",
            "resampler": RESAMPLER,
        },
        "time_map": TimeMap(facts.sample_rate_hz, DERIVATIVE_SAMPLE_RATE_HZ).as_record(
            sample_count
        ),
        "inspection_status": inspection_status(inspection),
        "activity": activity_record(activity, inspection),
    }


def _check_channels(channel_count: int) -> None:
    if (
        isinstance(channel_count, bool)
        or not isinstance(channel_count, int)
        or not 1 <= channel_count <= MAX_CHANNELS
    ):
        raise ValueError(f"channel_count must be between 1 and {MAX_CHANNELS}")


def compose_wav_header(record: dict[str, Any], channel_count: int, sample_count: int) -> bytes:
    """``RIFF``/``WAVE``, ``fmt `` (PCM, 16 kHz, 16 bits), ``hmyr`` and the ``data`` header.

    The record is canonical JSON padded with one space to an even length.  The
    header followed by ``2 * channel_count * sample_count`` PCM bytes is a WAV.
    """
    _check_channels(channel_count)
    if isinstance(sample_count, bool) or not isinstance(sample_count, int) or sample_count < 0:
        raise ValueError("sample_count must be an int of at least 0")
    meta = canonical_record(record)
    if len(meta) % 2:
        meta += b" "
    if len(meta) > RECORD_MAX_BYTES:
        raise RecordTooLarge(f"the record is over {RECORD_MAX_BYTES} bytes")
    block = _SAMPLE_BYTES * channel_count
    data_size = block * sample_count
    body = b"".join(
        (
            b"WAVE",
            b"fmt ",
            struct.pack("<I", 16),
            struct.pack(
                "<HHIIHH",
                1,
                channel_count,
                DERIVATIVE_SAMPLE_RATE_HZ,
                DERIVATIVE_SAMPLE_RATE_HZ * block,
                block,
                16,
            ),
            RECORD_CHUNK_ID,
            struct.pack("<I", len(meta)),
            meta,
            b"data",
            struct.pack("<I", data_size),
        )
    )
    riff_size = len(body) + data_size
    if riff_size > 0xFFFFFFFF:
        raise ValueError("the derivative is too large for a RIFF file")
    return b"RIFF" + struct.pack("<I", riff_size) + body


def parse_derivative_header(prefix: bytes) -> DerivativeHeader:
    """Strictly parse ``RIFF``/``WAVE``, ``fmt ``, ``hmyr`` and the ``data`` header.

    Anything else (another chunk, a second record, a record over the limit,
    JSON that is not an object, a record that disagrees with the ``fmt``
    chunk or the data size) raises :class:`DerivativeRecordInvalid`.
    """

    def chunk(offset: int, expected: bytes) -> tuple[int, int]:
        if len(prefix) < offset + 8 or prefix[offset : offset + 4] != expected:
            raise DerivativeRecordInvalid(f"expected the {expected!r} chunk")
        (size,) = struct.unpack_from("<I", prefix, offset + 4)
        return offset + 8, size

    if len(prefix) < 12 or prefix[:4] != b"RIFF" or prefix[8:12] != b"WAVE":
        raise DerivativeRecordInvalid("not a RIFF WAVE file")
    (riff_size,) = struct.unpack_from("<I", prefix, 4)
    fmt_at, fmt_size = chunk(12, b"fmt ")
    if fmt_size != 16 or len(prefix) < fmt_at + 16:
        raise DerivativeRecordInvalid("the fmt chunk is not 16 bytes of PCM")
    tag, channels, rate, byte_rate, block, bits = struct.unpack_from("<HHIIHH", prefix, fmt_at)
    if (
        tag != 1
        or not 1 <= channels <= MAX_CHANNELS
        or rate != DERIVATIVE_SAMPLE_RATE_HZ
        or bits != 16
        or block != _SAMPLE_BYTES * channels
        or byte_rate != rate * block
    ):
        raise DerivativeRecordInvalid("the fmt chunk is not 16-bit PCM at 16 kHz")
    record_at, record_size = chunk(fmt_at + 16, RECORD_CHUNK_ID)
    if record_size > RECORD_MAX_BYTES or record_size % 2 or len(prefix) < record_at + record_size:
        raise DerivativeRecordInvalid("the record chunk is over the limit or cut")
    try:
        record = json.loads(prefix[record_at : record_at + record_size].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DerivativeRecordInvalid("the record is not JSON") from exc
    if not isinstance(record, dict):
        raise DerivativeRecordInvalid("the record is not a JSON object")
    data_at, data_size = chunk(record_at + record_size, b"data")
    if riff_size != data_at - 8 + data_size or data_size % block:
        raise DerivativeRecordInvalid("the RIFF size or the data size is inconsistent")
    try:
        mapping = TimeMap.from_record(record.get("time_map"))  # type: ignore[arg-type]
        count = record["time_map"]["derivative_sample_count"]
        layout = record["format"]
        valid = (
            record.get("version") == DERIVATIVE_PRODUCER
            and isinstance(layout, dict)
            and layout.get("channel_count") == channels
            and layout.get("sample_rate_hz") == rate
            and mapping.derivative_rate_hz == rate
            and mapping.offset == 0
            and count * block == data_size
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DerivativeRecordInvalid("the record's time map is invalid") from exc
    if not valid:
        raise DerivativeRecordInvalid("the record disagrees with the WAV chunks")
    return DerivativeHeader(
        record=record,
        channel_count=channels,
        sample_rate_hz=rate,
        data_offset=data_at,
        data_size=data_size,
    )


# --- the bounded derivation -------------------------------------------------------------


class _Unavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def derive_audio(
    source: Path,
    facts: SourceFacts,
    *,
    inspection: dict[str, Any] | None,
    work_dir: Path,
    settings: Settings,
    max_bytes: int,
) -> DerivedAudio | DerivativeUnavailable:
    """Derive the WAV of ``source`` into ``work_dir``; never raises for a media or tool problem.

    ``max_bytes`` bounds the whole file: a predicted size over it skips the
    decode, and output past it stops the decode (``derivative_too_large``,
    never a truncated derivative).  The decode is killed (with its process
    group) at ``settings.media_derivative_timeout_seconds``.
    """
    _check_channels(facts.channel_count)
    if isinstance(facts.sample_rate_hz, bool) or facts.sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")
    block = _SAMPLE_BYTES * facts.channel_count
    reserve = RECORD_MAX_BYTES + _HEADER_BYTES
    if facts.duration_seconds is not None:
        predicted = math.ceil(facts.duration_seconds) * DERIVATIVE_SAMPLE_RATE_HZ * block + reserve
        if predicted > max_bytes:
            return DerivativeUnavailable(DERIVATIVE_TOO_LARGE)
    pcm_cap = max_bytes - reserve
    if pcm_cap < FRAME_SAMPLES * block:
        return DerivativeUnavailable(DERIVATIVE_TOO_LARGE)

    pcm_path: Path | None = None
    wav_path: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=".derivative-", suffix=".pcm", dir=work_dir)
        pcm_path = Path(name)
        meter = ActivityMeter(facts.channel_count)
        with os.fdopen(descriptor, "wb") as output:
            size = _decode(
                settings,
                Path(source),
                output=output,
                meter=meter,
                cap=pcm_cap,
                timeout=float(settings.media_derivative_timeout_seconds),
            )
        if size % block:
            raise _Unavailable(DERIVATIVE_FAILED)
        sample_count = size // block
        if sample_count < FRAME_SAMPLES:
            raise _Unavailable(DERIVATIVE_EMPTY)
        record = derivative_record(
            facts, inspection=inspection, sample_count=sample_count, activity=meter.finish()
        )
        try:
            header = compose_wav_header(record, facts.channel_count, sample_count)
        except RecordTooLarge:
            raise _Unavailable(RECORD_TOO_LARGE) from None
        if len(header) + size > max_bytes:
            raise _Unavailable(DERIVATIVE_TOO_LARGE)
        descriptor, name = tempfile.mkstemp(prefix="derivative-", suffix=".wav", dir=work_dir)
        wav_path = Path(name)
        digest = hashlib.sha256(header)
        with os.fdopen(descriptor, "wb") as output, pcm_path.open("rb") as pcm:
            output.write(header)
            while chunk := pcm.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        pcm_path.unlink()
        pcm_path = None
        derived = DerivedAudio(
            path=wav_path,
            sha256=digest.hexdigest(),
            size_bytes=len(header) + size,
            record=record,
            channel_count=facts.channel_count,
            sample_count=sample_count,
        )
        wav_path = None
        return derived
    except _Unavailable as exc:
        return DerivativeUnavailable(exc.reason)
    except Exception:  # noqa: BLE001 - a derivation never fails its caller (SPEC 5.4)
        return DerivativeUnavailable(DERIVATIVE_FAILED)
    finally:
        for leftover in (pcm_path, wav_path):
            if leftover is not None:
                leftover.unlink(missing_ok=True)


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
        raise _Unavailable(DERIVATIVE_TOOL_UNAVAILABLE) from None
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise _Unavailable(DERIVATIVE_TOOL_UNAVAILABLE)
    try:
        # The executable is operator-controlled and every input is an argv element.
        process = subprocess.Popen(  # noqa: S603
            build_derivative_argv(executable, source),
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
        raise _Unavailable(DERIVATIVE_TOOL_UNAVAILABLE) from None
    except OSError:
        raise _Unavailable(DERIVATIVE_FAILED) from None
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
                raise _Unavailable(DERIVATIVE_TOO_LARGE)
            output.write(chunk)
            meter.feed(chunk)
        returncode = process.wait()
    finally:
        timer.cancel()
        _stop(process)
        process.stdout.close()
    if expired.is_set():
        raise _Unavailable(DERIVATIVE_TIMEOUT)
    if returncode != 0:
        raise _Unavailable(DERIVATIVE_FAILED)
    return size
