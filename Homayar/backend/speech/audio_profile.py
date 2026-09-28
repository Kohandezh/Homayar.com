"""Telephone-audio preprocessing contract and a pure-Python signal quality estimator.

:func:`build_ffmpeg_argv` returns a fixed argv list (never a shell string) in
the conventions of :mod:`backend.media.validation`: operator-controlled
executable, every input an argv element, ``-nostdin``, ``-v error``, single
thread, bounded duration.  Running it (``subprocess.run(argv, shell=False,
timeout=…)``) is the caller's job — :func:`run_preprocess` is a thin helper.

:func:`estimate_quality` reads little-endian PCM16 mono bytes with the stdlib
(``array``/``math``, no numpy) and reports RMS, clipping ratio, silence ratio
and an *estimated* SNR from frame-energy percentiles (loud frames vs quiet
frames).  The SNR estimate is a heuristic for flagging, not a measurement.
"""

from __future__ import annotations

import array
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PREPROCESS_TIMEOUT_SECONDS = 60.0
MAX_DURATION_SECONDS = 4 * 3600
ALLOWED_SAMPLE_RATES = (8000, 16000)


@dataclass(frozen=True)
class AudioProfile:
    sample_rate: int = 16000
    bandpass: bool = False  # 300–3400 Hz telephone band
    bandpass_low_hz: int = 300
    bandpass_high_hz: int = 3400
    loudness_normalize: bool = True  # EBU R128 loudnorm
    target_lufs: float = -16.0
    denoise: bool = False  # afftdn
    denoise_nr_db: float = 12.0
    max_duration_seconds: float | None = None

    def __post_init__(self) -> None:
        if self.sample_rate not in ALLOWED_SAMPLE_RATES:
            raise ValueError(f"sample_rate must be one of {ALLOWED_SAMPLE_RATES}")
        if not 20 <= self.bandpass_low_hz < self.bandpass_high_hz <= self.sample_rate // 2:
            raise ValueError("bandpass must satisfy 20 <= low < high <= Nyquist")
        if not -40.0 <= self.target_lufs <= -5.0:
            raise ValueError("target_lufs out of range")
        if not 0.01 <= self.denoise_nr_db <= 97.0:
            raise ValueError("denoise_nr_db out of range")
        if (
            self.max_duration_seconds is not None
            and not 0 < self.max_duration_seconds <= MAX_DURATION_SECONDS
        ):
            raise ValueError("max_duration_seconds out of range")


TELEPHONE_8K = AudioProfile(sample_rate=8000, bandpass=True)
TELEPHONE_16K = AudioProfile(sample_rate=16000, bandpass=True)
WIDEBAND_16K = AudioProfile(sample_rate=16000)


def build_filter_chain(profile: AudioProfile) -> str:
    """Only numbers from the validated profile are interpolated."""
    filters: list[str] = []
    if profile.bandpass:
        filters.append(f"highpass=f={int(profile.bandpass_low_hz)}")
        filters.append(f"lowpass=f={int(profile.bandpass_high_hz)}")
    if profile.denoise:
        filters.append(f"afftdn=nr={float(profile.denoise_nr_db):g}")
    if profile.loudness_normalize:
        filters.append(f"loudnorm=I={float(profile.target_lufs):g}:TP=-1.5:LRA=11")
    return ",".join(filters)


def build_ffmpeg_argv(
    executable: str,
    source: Path,
    destination: Path,
    profile: AudioProfile = WIDEBAND_16K,
) -> list[str]:
    """ffmpeg argv: decode first audio stream → filters → mono PCM16 WAV at the
    profile rate.  Paths are passed as ``file:`` URLs so a name beginning with
    ``-`` or containing ``:`` is never read as an option or protocol."""
    if not executable or any(c in executable for c in "\n\0"):
        raise ValueError("invalid executable")
    for p in (source, destination):
        if "\0" in str(p):
            raise ValueError("invalid path")
    argv = [
        executable, "-nostdin", "-hide_banner", "-nostats", "-v", "error",
        "-threads", "1", "-i", f"file:{source}",
        "-map", "0:a:0", "-vn", "-sn", "-dn",
    ]  # fmt: skip
    if profile.max_duration_seconds is not None:
        argv += ["-t", f"{float(profile.max_duration_seconds):g}"]
    chain = build_filter_chain(profile)
    if chain:
        argv += ["-af", chain]
    argv += [
        "-ac", "1", "-ar", str(profile.sample_rate),
        "-c:a", "pcm_s16le", "-f", "wav", "-y", f"file:{destination}",
    ]  # fmt: skip
    return argv


def run_preprocess(argv: list[str], timeout: float = DEFAULT_PREPROCESS_TIMEOUT_SECONDS) -> None:
    """Run a prebuilt argv without a shell; raises CalledProcessError /
    TimeoutExpired.  Stderr is discarded, never logged (may hold paths)."""
    if not 0 < timeout <= 3600:
        raise ValueError("timeout out of range")
    subprocess.run(  # noqa: S603 - fixed argv, no shell
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        shell=False, close_fds=True, timeout=timeout, check=True,
    )  # fmt: skip


# --------------------------------------------------------------------------
# Quality estimation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityThresholds:
    low_snr_db: float = 10.0
    clipping_ratio: float = 0.01
    too_quiet_dbfs: float = -40.0
    silence_frame_dbfs: float = -50.0
    mostly_silence_ratio: float = 0.8


@dataclass(frozen=True)
class SignalQuality:
    duration_seconds: float
    rms_dbfs: float
    peak_dbfs: float
    clipping_ratio: float
    silence_ratio: float
    estimated_snr_db: float | None
    flags: tuple[str, ...] = field(default=())


def _dbfs(value: float) -> float:
    return 20 * math.log10(value / 32768.0) if value > 0 else -math.inf


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, max(0, int(round(q * (len(sorted_values) - 1)))))
    return sorted_values[idx]


def estimate_quality(
    pcm16: bytes,
    sample_rate: int = 16000,
    *,
    frame_ms: int = 20,
    thresholds: QualityThresholds = QualityThresholds(),
) -> SignalQuality:
    """Flags: ``empty``, ``too_quiet``, ``clipping``, ``mostly_silence``,
    ``low_snr`` (``snr_unknown`` when too few frames to estimate)."""
    if sample_rate <= 0 or frame_ms <= 0:
        raise ValueError("sample_rate and frame_ms must be positive")
    usable = len(pcm16) - (len(pcm16) % 2)
    samples = array.array("h")
    samples.frombytes(pcm16[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    n = len(samples)
    if n == 0:
        return SignalQuality(0.0, -math.inf, -math.inf, 0.0, 1.0, None, ("empty",))

    total_sq = 0.0
    peak = 0
    clipped = 0
    for s in samples:
        total_sq += s * s
        a = -s if s < 0 else s
        if a > peak:
            peak = a
        if a >= 32767:
            clipped += 1
    rms = math.sqrt(total_sq / n)

    frame = max(1, sample_rate * frame_ms // 1000)
    energies: list[float] = []
    for off in range(0, n - frame + 1, frame):
        acc = 0.0
        for s in samples[off : off + frame]:
            acc += s * s
        energies.append(math.sqrt(acc / frame))
    silence_floor = 32768.0 * 10 ** (thresholds.silence_frame_dbfs / 20)
    silent = sum(1 for e in energies if e < silence_floor)
    silence_ratio = silent / len(energies) if energies else 1.0

    snr: float | None = None
    if len(energies) >= 10:
        ordered = sorted(energies)
        noise = max(_percentile(ordered, 0.10), 1.0)
        speech = _percentile(ordered, 0.90)
        snr = 20 * math.log10(max(speech, 1.0) / noise)

    flags: list[str] = []
    rms_db = _dbfs(rms)
    if rms_db < thresholds.too_quiet_dbfs:
        flags.append("too_quiet")
    clip_ratio = clipped / n
    if clip_ratio >= thresholds.clipping_ratio:
        flags.append("clipping")
    if silence_ratio >= thresholds.mostly_silence_ratio:
        flags.append("mostly_silence")
    if snr is None:
        flags.append("snr_unknown")
    elif snr < thresholds.low_snr_db:
        flags.append("low_snr")
    return SignalQuality(
        n / sample_rate, rms_db, _dbfs(peak), clip_ratio, silence_ratio, snr, tuple(flags)
    )
