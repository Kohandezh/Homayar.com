"""Per-channel signal inspection and trusted channel-role attribution (HOM-37, ADR 0039).

Runs on ingest, after :func:`backend.media.validation.validate_media_upload`
accepted the file.  One bounded ffmpeg decode turns the first
``analysis_seconds`` of the primary audio stream into 16-bit PCM at 8 kHz,
channels kept apart.  The pure part then measures each channel:

* ``rms_dbfs``: level over the window (``None`` for digital silence, never a
  made-up floor);
* ``active_share``: share of 20 ms frames whose level reaches
  ``active_threshold_dbfs``; a channel is ``silent`` when that share is at most
  ``silent_max_active_share``;
* duplicates: two channels that are not silent and whose samples correlate
  with ``|r| >= duplicate_min_correlation`` at some lag within
  ``duplicate_max_lag_ms``; a scaled, inverted or slightly delayed copy is the
  same signal.

This is a **signal-level heuristic**, not speech detection: "active" means
"carries signal above a level", never "someone speaks".

Attribution gives a channel a role only from the workspace's trusted mapping
for the recording's source (:mod:`backend.media.channel_roles`), and only when
the channel exists, is analysed, is not silent and is not a duplicate.  A role
is never inferred from stereo, channel order, loudness, speech share,
transcript content or a model.  Unknown stays unknown, with a reason code.

The analysis never refuses media: every failure gives ``status="unavailable"``
with a reason, and every channel stays ``unknown``.
"""

from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import threading
import time
from array import array
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from backend.media.channel_roles import (
    ROLE_UNKNOWN,
    ChannelRoleMapping,
    InvalidChannelRoleMapping,
    mapping_from_settings,
)
from backend.media.validation import (
    _MAX_ANALYZE_DURATION_MICROSECONDS,
    _MAX_FFPROBE_ALLOCATION_BYTES,
    _MAX_PROBE_SIZE_BYTES,
    AudioStreamMetadata,
    MediaMetadata,
)

CHANNEL_INSPECTION_VERSION: Final = "channel-inspection/1"
ANALYSIS_SAMPLE_RATE_HZ: Final = 8_000
FRAME_MS: Final = 20
FRAME_SAMPLES: Final = ANALYSIS_SAMPLE_RATE_HZ * FRAME_MS // 1000
# Narrowband (telephony) audio: sampled below 16 kHz.
NARROWBAND_BELOW_HZ: Final = 16_000
MAX_ANALYSIS_SECONDS: Final = 120.0
MAX_DUPLICATE_LAG_MS: Final = 20.0
# Lags are searched on one block of at most this many samples (the most energetic
# 2 s); only the best lag is then measured over the whole window.
LAG_SEARCH_SAMPLES: Final = 2 * ANALYSIS_SAMPLE_RATE_HZ
_FULL_SCALE: Final = 32_768.0
_SAMPLE_BYTES: Final = 2
_OUTPUT_SLACK_BYTES: Final = 64 * 1024
_READ_CHUNK_BYTES: Final = 64 * 1024
_POSIX: Final = os.name == "posix"

STATUS_COMPLETE: Final = "complete"
STATUS_UNAVAILABLE: Final = "unavailable"
ATTRIBUTION_TRUSTED: Final = "trusted"
ATTRIBUTION_UNKNOWN: Final = "unknown"

FLAG_SILENT_CHANNEL: Final = "silent_channel"
FLAG_DUPLICATED_CHANNELS: Final = "duplicated_channels"
FLAG_MONO_AS_STEREO: Final = "mono_as_stereo"

# Why an analysis is unavailable.
ANALYSIS_DISABLED: Final = "analysis_disabled"
ANALYSIS_TOOL_UNAVAILABLE: Final = "analysis_tool_unavailable"
ANALYSIS_TIMEOUT: Final = "analysis_timeout"
ANALYSIS_FAILED: Final = "analysis_failed"
ANALYSIS_EMPTY: Final = "analysis_empty"

# Why a channel is unknown, in precedence order (the first that applies wins).
REASON_MONO: Final = "mono"
REASON_MAPPING_INVALID: Final = "mapping_invalid"
REASON_NO_MAPPING: Final = "no_mapping"
REASON_CHANNEL_MISSING: Final = "channel_missing"
REASON_ANALYSIS_UNAVAILABLE: Final = "analysis_unavailable"
REASON_CHANNEL_UNMAPPED: Final = "channel_unmapped"
REASON_CHANNEL_SILENT: Final = "channel_silent"
REASON_CHANNELS_DUPLICATED: Final = "channels_duplicated"


@dataclass(frozen=True, slots=True)
class ChannelAnalysisPolicy:
    analysis_seconds: float = 30.0
    timeout_seconds: float = 10.0
    active_threshold_dbfs: float = -50.0
    silent_max_active_share: float = 0.01
    duplicate_min_correlation: float = 0.99
    duplicate_max_lag_ms: float = 5.0

    def __post_init__(self) -> None:
        values = (
            self.analysis_seconds,
            self.timeout_seconds,
            self.active_threshold_dbfs,
            self.silent_max_active_share,
            self.duplicate_min_correlation,
            self.duplicate_max_lag_ms,
        )
        if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise ValueError("channel analysis settings must be finite numbers")
        if not 0 <= self.analysis_seconds <= MAX_ANALYSIS_SECONDS:
            raise ValueError(f"analysis_seconds must be between 0 and {MAX_ANALYSIS_SECONDS:g}")
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be above 0 and at most 60")
        if not -120 <= self.active_threshold_dbfs < 0:
            raise ValueError("active_threshold_dbfs must be from -120 to below 0")
        if not 0 <= self.silent_max_active_share < 1:
            raise ValueError("silent_max_active_share must be from 0 to below 1")
        if not 0 < self.duplicate_min_correlation <= 1:
            raise ValueError("duplicate_min_correlation must be above 0 and at most 1")
        if not 0 <= self.duplicate_max_lag_ms <= MAX_DUPLICATE_LAG_MS:
            raise ValueError(f"duplicate_max_lag_ms must be between 0 and {MAX_DUPLICATE_LAG_MS:g}")

    @property
    def duplicate_max_lag_samples(self) -> int:
        return round(self.duplicate_max_lag_ms * ANALYSIS_SAMPLE_RATE_HZ / 1000)

    @classmethod
    def from_settings(cls, settings: Any) -> ChannelAnalysisPolicy:
        return cls(
            analysis_seconds=float(settings.media_channel_analysis_seconds),
            timeout_seconds=float(settings.media_channel_analysis_timeout_seconds),
            active_threshold_dbfs=float(settings.media_channel_active_threshold_dbfs),
            silent_max_active_share=float(settings.media_channel_silent_max_active_share),
            duplicate_min_correlation=float(settings.media_channel_duplicate_min_correlation),
            duplicate_max_lag_ms=float(settings.media_channel_duplicate_max_lag_ms),
        )


@dataclass(frozen=True, slots=True)
class ChannelSignal:
    index: int
    rms_dbfs: float | None  # None: every sample was 0
    active_share: float  # 0.0 .. 1.0
    silent: bool
    duplicated: bool  # member of a duplicate pair


@dataclass(frozen=True, slots=True)
class ChannelSignalAnalysis:
    channels: tuple[ChannelSignal, ...]
    duplicate_pairs: tuple[tuple[int, int], ...]  # (i, j), i < j, sorted
    analyzed_seconds: float


@dataclass(frozen=True, slots=True)
class ChannelInspection:
    version: str
    status: str
    unavailable_reason: str | None
    channel_count: int
    channel_layout: str
    sample_rate_hz: int
    codec: str
    duration_seconds: float
    narrowband: bool
    audio_streams: tuple[AudioStreamMetadata, ...]
    analysis: ChannelSignalAnalysis | None
    policy: ChannelAnalysisPolicy

    @classmethod
    def from_parts(
        cls,
        metadata: MediaMetadata,
        *,
        analysis: ChannelSignalAnalysis | None,
        unavailable_reason: str | None,
        policy: ChannelAnalysisPolicy,
    ) -> ChannelInspection:
        if (analysis is None) == (unavailable_reason is None):
            raise ValueError("an inspection has either an analysis or an unavailable reason")
        primary = metadata.audio_streams[0]
        return cls(
            version=CHANNEL_INSPECTION_VERSION,
            status=STATUS_COMPLETE if analysis is not None else STATUS_UNAVAILABLE,
            unavailable_reason=unavailable_reason,
            channel_count=primary.channels,
            channel_layout=primary.channel_layout,
            sample_rate_hz=primary.sample_rate_hz,
            codec=primary.codec,
            duration_seconds=metadata.duration_seconds,
            narrowband=primary.sample_rate_hz < NARROWBAND_BELOW_HZ,
            audio_streams=metadata.audio_streams,
            analysis=analysis,
            policy=policy,
        )

    @property
    def flags(self) -> tuple[str, ...]:
        if self.analysis is None:
            return ()
        flags: set[str] = set()
        if any(channel.silent for channel in self.analysis.channels):
            flags.add(FLAG_SILENT_CHANNEL)
        if self.analysis.duplicate_pairs:
            flags.add(FLAG_DUPLICATED_CHANNELS)
        if self.channel_count == 2 and (0, 1) in self.analysis.duplicate_pairs:
            flags.add(FLAG_MONO_AS_STEREO)
        return tuple(sorted(flags))


@dataclass(frozen=True, slots=True)
class ChannelAttribution:
    index: int
    role: str  # "agent" | "customer" | "unknown"
    attribution: str  # "trusted" | "unknown"
    reason: str | None  # None only when trusted


# --- pure signal analysis ---------------------------------------------------------------


def analyze_channel_samples(
    pcm: bytes, *, channel_count: int, policy: ChannelAnalysisPolicy
) -> ChannelSignalAnalysis:
    """Measure interleaved signed 16-bit little-endian PCM at 8000 Hz.

    Raises ``ValueError`` when there is not one whole 20 ms frame per channel.
    A trailing partial sample or frame is ignored.
    """
    if channel_count <= 0:
        raise ValueError("channel_count must be positive")
    usable = len(pcm) - len(pcm) % (_SAMPLE_BYTES * channel_count)
    samples = array("h")
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    frames = len(samples) // channel_count // FRAME_SAMPLES
    if frames == 0:
        raise ValueError("less than one analysis frame")
    length = frames * FRAME_SAMPLES
    channels = [samples[index::channel_count][:length] for index in range(channel_count)]

    # A frame is active when its mean square reaches the threshold level.
    threshold_sum = FRAME_SAMPLES * (_FULL_SCALE * 10 ** (policy.active_threshold_dbfs / 20)) ** 2
    energies: list[int] = []
    frame_energies: list[list[int]] = []
    measured: list[tuple[float | None, float, bool]] = []
    for channel in channels:
        energy = 0
        active = 0
        per_frame: list[int] = []
        for start in range(0, length, FRAME_SAMPLES):
            frame = channel[start : start + FRAME_SAMPLES]
            frame_energy = math.sumprod(frame, frame)
            per_frame.append(frame_energy)
            energy += frame_energy
            if frame_energy > 0 and frame_energy >= threshold_sum:
                active += 1
        energies.append(energy)
        frame_energies.append(per_frame)
        rms_dbfs = (
            round(20 * math.log10(math.sqrt(energy / length) / _FULL_SCALE), 2) if energy else None
        )
        share = active / frames
        measured.append((rms_dbfs, round(share, 4), share <= policy.silent_max_active_share))

    pairs: list[tuple[int, int]] = []
    sums = [sum(channel) for channel in channels]
    max_lag = policy.duplicate_max_lag_samples
    for first in range(channel_count):
        for second in range(first + 1, channel_count):
            if measured[first][2] or measured[second][2]:
                continue  # silent channels are never compared (R5)
            correlation = _correlation(
                length,
                sums[first],
                sums[second],
                energies[first],
                energies[second],
                math.sumprod(channels[first], channels[second]),
            )
            if correlation is not None and abs(correlation) >= policy.duplicate_min_correlation:
                pairs.append((first, second))
                continue
            if max_lag == 0:
                continue
            correlation = _lagged_correlation(
                channels[first],
                channels[second],
                [a + b for a, b in zip(frame_energies[first], frame_energies[second], strict=True)],
                max_lag,
                policy.duplicate_min_correlation,
            )
            if correlation is not None and abs(correlation) >= policy.duplicate_min_correlation:
                pairs.append((first, second))
    duplicated = {index for pair in pairs for index in pair}
    return ChannelSignalAnalysis(
        channels=tuple(
            ChannelSignal(
                index=index,
                rms_dbfs=rms_dbfs,
                active_share=share,
                silent=silent,
                duplicated=index in duplicated,
            )
            for index, (rms_dbfs, share, silent) in enumerate(measured)
        ),
        duplicate_pairs=tuple(pairs),
        analyzed_seconds=round(length / ANALYSIS_SAMPLE_RATE_HZ, 3),
    )


def _correlation(
    count: int, sum_a: int, sum_b: int, energy_a: int, energy_b: int, cross: int
) -> float | None:
    """Pearson correlation from running sums; None for a constant channel."""
    variance_a = energy_a - sum_a * sum_a / count
    variance_b = energy_b - sum_b * sum_b / count
    if variance_a <= 0 or variance_b <= 0:
        return None
    return (cross - sum_a * sum_b / count) / math.sqrt(variance_a * variance_b)


def _pearson(first: memoryview, second: memoryview) -> float | None:
    """Pearson correlation of two equal-length int16 views (exact integer sums)."""
    count = len(first)
    sum_a, sum_b = sum(first), sum(second)
    variance_a = math.sumprod(first, first) - sum_a * sum_a / count
    variance_b = math.sumprod(second, second) - sum_b * sum_b / count
    if variance_a <= 0 or variance_b <= 0:
        return None
    return (math.sumprod(first, second) - sum_a * sum_b / count) / math.sqrt(
        variance_a * variance_b
    )


def _lagged_correlation(
    first: array,
    second: array,
    frame_energy: list[int],
    max_lag: int,
    threshold: float,
) -> float | None:
    """Correlation at the best lag in ``-max_lag..max_lag`` (lag 0 excluded).

    A positive lag ``d`` aligns ``first[t]`` with ``second[t + d]`` (``second``
    is ``d`` samples late).  The lag is chosen on one block of at most
    ``LAG_SEARCH_SAMPLES`` samples, the one with the most combined frame
    energy; when the block reaches ``threshold`` at that lag, the lag is then
    measured over the whole overlap (both must reach it).

    Memory stays small: the channels stay compact ``array('h')``; only the
    block of ``first`` and the searched region of ``second`` (block plus
    ``2 * max_lag`` samples) are copied, once per pair, into ``array('d')``;
    the whole-window check reads zero-copy ``memoryview`` slices.  Cost per
    pair: ``(2 * max_lag + 1) * block`` multiplications plus a few passes over
    the window.
    """
    length = len(first)
    block = min(LAG_SEARCH_SAMPLES, length - 2 * max_lag)
    if block < FRAME_SAMPLES:
        return None
    block_frames = block // FRAME_SAMPLES
    # The block starts on the frame with the most combined energy after it; when
    # no frame start leaves room for the lags, it starts right after them.
    lowest = math.ceil(max_lag / FRAME_SAMPLES)
    highest = (length - block - max_lag) // FRAME_SAMPLES
    origin = max_lag
    if highest >= lowest:
        best_start, best_energy = lowest, -1
        window = sum(frame_energy[lowest : lowest + block_frames])
        for start in range(lowest, highest + 1):
            if start > lowest:
                window += frame_energy[start + block_frames - 1] - frame_energy[start - 1]
            if window > best_energy:
                best_start, best_energy = start, window
        origin = best_start * FRAME_SAMPLES
    reference = array("d", first[origin : origin + block])
    sum_ref = math.fsum(reference)
    variance_ref = math.sumprod(reference, reference) - sum_ref * sum_ref / block
    if variance_ref <= 0:
        return None
    # The searched region of ``second``: ``region[i : i + block]`` is lag ``i - max_lag``.
    region = array("d", second[origin - max_lag : origin + max_lag + block])
    # Running sums of the shifted block, updated one sample per lag.
    shifted = region[:block]
    sum_shifted = math.fsum(shifted)
    energy_shifted = math.sumprod(shifted, shifted)
    del shifted
    best_lag, best_value = 0, 0.0
    for offset in range(2 * max_lag + 1):
        lag = offset - max_lag
        if offset > 0:
            leaving, entering = region[offset - 1], region[offset + block - 1]
            sum_shifted += entering - leaving
            energy_shifted += entering * entering - leaving * leaving
        if lag == 0:
            continue
        variance_shifted = energy_shifted - sum_shifted * sum_shifted / block
        if variance_shifted <= 0:
            continue
        cross = math.sumprod(reference, region[offset : offset + block])
        value = abs(
            (cross - sum_ref * sum_shifted / block) / math.sqrt(variance_ref * variance_shifted)
        )
        if value > best_value:
            best_lag, best_value = lag, value
    if best_lag == 0 or best_value < threshold:
        return None
    first_view, second_view = memoryview(first), memoryview(second)
    if best_lag > 0:
        return _pearson(first_view[: length - best_lag], second_view[best_lag:])
    return _pearson(first_view[-best_lag:], second_view[: length + best_lag])


# --- bounded decode ---------------------------------------------------------------------


class _Unavailable(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def inspect_channels(
    file_path: str | os.PathLike[str],
    *,
    metadata: MediaMetadata,
    policy: ChannelAnalysisPolicy,
    ffmpeg_path: str | os.PathLike[str] = "ffmpeg",
) -> ChannelInspection:
    """Inspect the primary audio stream of an accepted file.

    Never raises for an analysis failure: the result is then ``unavailable``
    with a reason, and the caller goes on exactly as without the analysis.
    """
    channel_count = metadata.audio_streams[0].channels

    def unavailable(reason: str) -> ChannelInspection:
        return ChannelInspection.from_parts(
            metadata, analysis=None, unavailable_reason=reason, policy=policy
        )

    if policy.analysis_seconds <= 0:
        return unavailable(ANALYSIS_DISABLED)
    try:
        pcm = _decode_pcm(
            ffmpeg_path,
            Path(file_path),
            seconds=policy.analysis_seconds,
            channel_count=channel_count,
            timeout=policy.timeout_seconds,
        )
    except _Unavailable as exc:
        return unavailable(exc.reason)
    except Exception:  # noqa: BLE001 - the analysis never fails an ingest (R9)
        return unavailable(ANALYSIS_FAILED)
    try:
        analysis = analyze_channel_samples(pcm, channel_count=channel_count, policy=policy)
    except ValueError:
        return unavailable(ANALYSIS_EMPTY)
    except Exception:  # noqa: BLE001 - the analysis never fails an ingest (R9)
        return unavailable(ANALYSIS_FAILED)
    return ChannelInspection.from_parts(
        metadata, analysis=analysis, unavailable_reason=None, policy=policy
    )


def _build_analysis_command(executable: str, path: Path, seconds: float) -> list[str]:
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
        str(path),
        "-t",
        f"{seconds:g}",
        "-map",
        "0:a:0",
        "-vn",
        "-ar",
        str(ANALYSIS_SAMPLE_RATE_HZ),
        "-c:a",
        "pcm_s16le",
        "-f",
        "s16le",
        "-",
    ]


def _decode_pcm(
    ffmpeg_path: str | os.PathLike[str],
    path: Path,
    *,
    seconds: float,
    channel_count: int,
    timeout: float,
) -> bytes:
    try:
        executable = os.fspath(ffmpeg_path)
    except TypeError:
        raise _Unavailable(ANALYSIS_TOOL_UNAVAILABLE) from None
    if not isinstance(executable, str) or not executable or "\x00" in executable:
        raise _Unavailable(ANALYSIS_TOOL_UNAVAILABLE)
    cap = (
        math.ceil(seconds * ANALYSIS_SAMPLE_RATE_HZ) * channel_count * _SAMPLE_BYTES
        + _OUTPUT_SLACK_BYTES
    )
    deadline = time.monotonic() + timeout
    try:
        # The executable is operator-controlled and every input is an argv element.
        process = subprocess.Popen(  # noqa: S603
            _build_analysis_command(executable, path, seconds),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            shell=False,
            close_fds=True,
            # Its own process group, so a kill also reaches any child holding the pipe.
            start_new_session=_POSIX,
        )
    except (FileNotFoundError, PermissionError):
        raise _Unavailable(ANALYSIS_TOOL_UNAVAILABLE) from None
    except OSError:
        raise _Unavailable(ANALYSIS_FAILED) from None
    reader = _BoundedReader(process, cap)
    try:
        if not reader.wait(timeout):
            raise _Unavailable(ANALYSIS_TIMEOUT)
        if reader.failed or reader.overflow:
            raise _Unavailable(ANALYSIS_FAILED)
        try:
            returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise _Unavailable(ANALYSIS_TIMEOUT) from None
    finally:
        _stop(process)
        reader.join()
        assert process.stdout is not None
        process.stdout.close()
    if returncode != 0:
        raise _Unavailable(ANALYSIS_FAILED)
    return bytes(reader.data)


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Kill the decoder (and its process group) if it still runs, then reap it."""
    if _POSIX:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    elif process.poll() is None:
        process.kill()
    process.wait()


class _BoundedReader:
    """Reads the decoder's stdout on a helper thread, never more than ``cap`` bytes.

    Past the cap it stops reading and reports an overflow; the caller kills the
    decoder, which closes the pipe.
    """

    def __init__(self, process: subprocess.Popen[bytes], cap: int) -> None:
        assert process.stdout is not None
        self._descriptor = process.stdout.fileno()
        self._cap = cap
        self.data = bytearray()
        self.overflow = False
        self.failed = False
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._read, name="media-channel-pcm", daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            while True:
                chunk = os.read(self._descriptor, _READ_CHUNK_BYTES)
                if not chunk:
                    return
                if len(self.data) + len(chunk) > self._cap:
                    self.overflow = True
                    return
                self.data.extend(chunk)
        except OSError:
            self.failed = True
        finally:
            self._done.set()

    def wait(self, timeout: float) -> bool:
        """True when stdout closed, overflowed or failed within ``timeout``."""
        return self._done.wait(timeout)

    def join(self) -> None:
        self._thread.join(1.0)


# --- attribution and the record -----------------------------------------------------------


def attribute_channels(
    inspection: ChannelInspection,
    mapping: ChannelRoleMapping | None,
    *,
    mapping_invalid: bool = False,
) -> tuple[ChannelAttribution, ...]:
    """One entry per channel of the file, by the published precedence."""
    count = inspection.channel_count

    def every(reason: str) -> tuple[ChannelAttribution, ...]:
        return tuple(_unknown(index, reason) for index in range(count))

    # One mono channel holds both parties: never trusted, whatever the mapping says.
    if count == 1:
        return every(REASON_MONO)
    if mapping_invalid:
        return every(REASON_MAPPING_INVALID)
    if mapping is None:
        return every(REASON_NO_MAPPING)
    if any(channel >= count for channel, _ in mapping.channels):
        return every(REASON_CHANNEL_MISSING)
    if inspection.analysis is None:
        return every(REASON_ANALYSIS_UNAVAILABLE)
    result: list[ChannelAttribution] = []
    for measured in inspection.analysis.channels:
        role = mapping.role_of(measured.index)
        if role is None:
            result.append(_unknown(measured.index, REASON_CHANNEL_UNMAPPED))
        elif measured.silent:
            result.append(_unknown(measured.index, REASON_CHANNEL_SILENT))
        elif measured.duplicated:
            result.append(_unknown(measured.index, REASON_CHANNELS_DUPLICATED))
        else:
            result.append(ChannelAttribution(measured.index, role, ATTRIBUTION_TRUSTED, None))
    return tuple(result)


def _unknown(index: int, reason: str) -> ChannelAttribution:
    return ChannelAttribution(index, ROLE_UNKNOWN, ATTRIBUTION_UNKNOWN, reason)


def channel_inspection_record(
    inspection: ChannelInspection,
    *,
    source_type: str,
    source_provider: str,
    workspace_settings: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attribute with the source's mapping and return the stored and served record.

    The mapping is read here, once, at ingest; the record keeps its version and
    digest, so a later change of the mapping never changes this record.
    """
    mapping_invalid = False
    try:
        mapping = mapping_from_settings(
            workspace_settings, source_type=source_type, source_provider=source_provider
        )
    except InvalidChannelRoleMapping:
        mapping, mapping_invalid = None, True
    attributions = attribute_channels(inspection, mapping, mapping_invalid=mapping_invalid)
    analysis = inspection.analysis
    signals = {measured.index: measured for measured in analysis.channels} if analysis else {}
    policy = inspection.policy
    return {
        "version": inspection.version,
        "status": inspection.status,
        "unavailable_reason": inspection.unavailable_reason,
        "channel_count": inspection.channel_count,
        "channel_layout": inspection.channel_layout,
        "sample_rate_hz": inspection.sample_rate_hz,
        "codec": inspection.codec,
        "duration_seconds": inspection.duration_seconds,
        "narrowband": inspection.narrowband,
        "audio_streams": [
            {
                "index": stream.index,
                "codec": stream.codec,
                "channels": stream.channels,
                "channel_layout": stream.channel_layout,
                "sample_rate_hz": stream.sample_rate_hz,
                "duration_seconds": stream.duration_seconds,
            }
            for stream in inspection.audio_streams
        ],
        "analysis_seconds": analysis.analyzed_seconds if analysis else None,
        "analysis_sample_rate_hz": ANALYSIS_SAMPLE_RATE_HZ if analysis else None,
        "thresholds": {
            "frame_ms": FRAME_MS,
            "active_threshold_dbfs": policy.active_threshold_dbfs,
            "silent_max_active_share": policy.silent_max_active_share,
            "duplicate_min_correlation": policy.duplicate_min_correlation,
            "duplicate_max_lag_ms": policy.duplicate_max_lag_ms,
            "narrowband_below_hz": NARROWBAND_BELOW_HZ,
        },
        "flags": list(inspection.flags),
        "duplicate_pairs": [list(pair) for pair in analysis.duplicate_pairs] if analysis else [],
        "mapping_source_type": source_type,
        "mapping_source_provider": source_provider,
        "mapping_version": mapping.version if mapping else None,
        "mapping_digest": mapping.digest if mapping else None,
        "channels": [
            {
                "index": item.index,
                "rms_dbfs": signals[item.index].rms_dbfs if analysis else None,
                "active_share": signals[item.index].active_share if analysis else None,
                "silent": signals[item.index].silent if analysis else None,
                "duplicated": signals[item.index].duplicated if analysis else None,
                "role": item.role,
                "attribution": item.attribution,
                "reason": item.reason,
            }
            for item in attributions
        ],
    }
