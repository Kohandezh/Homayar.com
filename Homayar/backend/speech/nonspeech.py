"""Cheap CPU check whether an audio span is music or a tone (HOM-348, ADR 0078).

The ASR gate calls :func:`classify_span` on the audio under each decoded
segment. It reads only the samples and the segment times: no model, no scipy,
no librosa. Loudness never matters: only frames within ``_ACTIVE_FLOOR_DB`` of
the span's loudest frame are judged, never an absolute dBFS floor.

The rule (review round 1, ADR 0078): music and tones keep their spectral shape
from one frame to the next, speech does not.

- A frame is judged when it and its reference frames (32 and 48 ms back) are
  active and their energies agree within ``_REFERENCE_ENERGY_DB``: frames that
  straddle an onset or a gap differ by windowing, not by content.
- A judged frame is steady when the cosine similarity of its magnitude
  spectrum against both references stays under ``_STEADY_SHAPE_CHANGE``.
- When at least ``_STEADY_FRACTION`` of the judged frames are steady, the span
  is non-speech; a smaller steady share means speech is present and the span
  stays speech whatever else it holds.
- Speech can hide under one loud, stable partial (a dial tone under words): a
  dominant-peak mask removes the span's strongest spectral peak and repeats
  the steadiness test on the rest. Only when the rest is negligible (a pure
  tonal core) or also steady is the span non-speech.
- The steady frames' median number of prominent peaks then separates tones
  (at most two steady frequencies, DTMF included) from music (chords,
  melodies), per frame, with the median's tolerance: a resample that moves
  one harmonic across the peak floor changes nothing.

Unsure is speech: dropping real speech is worse than keeping one invented
word. numpy is imported inside the function, so importing this module is free
and the Whisper adapter stays importable without numpy.
"""

from __future__ import annotations

from typing import Final

NONSPEECH_VERSION: Final = "nonspeech/1"

_FRAME_SECONDS: Final = 0.032  # Hann-windowed frame length
_HOP_SECONDS: Final = 0.016  # hop between frames
_MIN_SECONDS: Final = 1.5  # a shorter span cannot be judged (amendment 4)
_LOW_HZ: Final = 100.0  # the band judged: the phone band and a little more
_HIGH_HZ: Final = 3800.0
_ACTIVE_FLOOR_DB: Final = 35.0  # active frames: this far under the loudest
_SHAPE_LAGS_FRAMES: Final = (2, 3)  # the spectrum is compared 32 and 48 ms back
_REFERENCE_ENERGY_DB: Final = 4.0  # a frame and its reference agree this far
_STEADY_SHAPE_CHANGE: Final = 0.15  # 1 - cosine similarity under this: steady
_STEADY_FRACTION: Final = 0.77  # this share of judged frames steady: non-speech
_MASK_HALF_BINS: Final = 3  # the dominant peak mask is this wide, both sides
_CORE_RESIDUAL_DB: Final = 20.0  # less than this left: a pure tonal core
_PEAK_FLOOR_DB: Final = 14.0  # in-frame peaks this far under the loudest
_TONE_PEAKS: Final = 2  # at most this many steady peaks: a tone
_MIN_JUDGED_FRAMES: Final = 8  # fewer judged frames: not enough to judge

_MAX_LAG_FRAMES: Final = max(_SHAPE_LAGS_FRAMES)


def classify_span(samples, sample_rate, start_seconds: float, end_seconds: float) -> str:
    """``"speech"``, ``"music"`` or ``"tone"`` for one audio span.

    ``samples`` is a 1-D mono float array in [-1, 1] at ``sample_rate`` (8000
    or 16000; anything else raises ``ValueError``). A span too short, outside
    the array, empty or silent returns ``"speech"``: there is not enough
    audio to say otherwise. A span that runs past the end of the audio is
    clipped first; only the clipped part is judged.
    """
    import numpy as np

    if sample_rate not in (8_000, 16_000):
        raise ValueError("sample_rate must be 8000 or 16000")
    if end_seconds <= start_seconds:
        return "speech"
    total = int(np.asarray(samples).shape[0])
    first = max(0, int(np.floor(start_seconds * sample_rate)))
    last = min(total, int(np.ceil(end_seconds * sample_rate)))
    if first >= total or last - first < round(_MIN_SECONDS * sample_rate):
        return "speech"
    span = np.asarray(samples[first:last], dtype=np.float64)

    frame = round(_FRAME_SECONDS * sample_rate)
    hop = round(_HOP_SECONDS * sample_rate)
    window = np.hanning(frame + 1)[:-1]
    positions = np.arange(0, span.size - frame + 1, hop)
    if positions.size == 0:
        return "speech"
    index = np.arange(frame) + positions[:, None]
    spectra = np.abs(np.fft.rfft(span[index] * window, axis=1))

    low_bin = int(np.ceil(_LOW_HZ / (sample_rate / frame)))
    high_bin = min(spectra.shape[1], int(np.floor(_HIGH_HZ / (sample_rate / frame))) + 1)
    band = spectra[:, low_bin:high_bin]
    energy = (band**2).sum(axis=1)
    loudest = energy.max(initial=0.0)
    if loudest <= 0.0:
        return "speech"
    active = energy >= loudest * 10.0 ** (-_ACTIVE_FLOOR_DB / 10.0)

    judged = active.copy()
    for lag in _SHAPE_LAGS_FRAMES:
        reference = np.zeros_like(judged)
        reference[lag:] = active[:-lag]
        judged &= reference
        ratio = np.ones(band.shape[0])
        ratio[lag:] = energy[lag:] / np.maximum(energy[:-lag], 1e-30)
        agree = (ratio <= 10.0 ** (_REFERENCE_ENERGY_DB / 10.0)) & (
            ratio >= 10.0 ** (-_REFERENCE_ENERGY_DB / 10.0)
        )
        gated = np.zeros(band.shape[0], dtype=bool)
        gated[lag:] = agree[lag:]
        judged &= gated
    judged[:_MAX_LAG_FRAMES] = False
    judged_count = int(judged.sum())
    if judged_count < _MIN_JUDGED_FRAMES:
        return "speech"

    def steady_share(spectra_to_compare: np.ndarray, norms: np.ndarray) -> float:
        steady = np.ones(spectra_to_compare.shape[0], dtype=bool)
        for lag in _SHAPE_LAGS_FRAMES:
            with np.errstate(divide="ignore", invalid="ignore"):
                similarity = (spectra_to_compare[lag:] * spectra_to_compare[:-lag]).sum(axis=1) / (
                    norms[lag:] * norms[:-lag]
                )
            change = np.ones(spectra_to_compare.shape[0])
            change[lag:] = 1.0 - np.clip(np.nan_to_num(similarity), -1.0, 1.0)
            steady &= change < _STEADY_SHAPE_CHANGE
        return float((judged & steady).sum()) / judged_count

    norms = np.linalg.norm(band, axis=1)
    if steady_share(band, norms) < _STEADY_FRACTION:
        return "speech"

    # Speech under one loud, stable partial: mask the span's dominant peak
    # (the strongest bin of the judged frames' mean spectrum) and judge the
    # rest. A tone's rest is leakage only; chords keep their other notes.
    mean_spectrum = band[judged].mean(axis=0)
    center = int(np.argmax(mean_spectrum))
    keep = np.ones(band.shape[1], dtype=bool)
    keep[max(0, center - _MASK_HALF_BINS) : center + _MASK_HALF_BINS + 1] = False
    residual = band[:, keep]
    residual_share = float((residual[judged] ** 2).sum() / (band[judged] ** 2).sum())
    if residual_share > 10.0 ** (-_CORE_RESIDUAL_DB / 10.0):
        if steady_share(residual, np.linalg.norm(residual, axis=1)) < _STEADY_FRACTION:
            return "speech"

    steady = np.ones(band.shape[0], dtype=bool)
    for lag in _SHAPE_LAGS_FRAMES:
        with np.errstate(divide="ignore", invalid="ignore"):
            similarity = (band[lag:] * band[:-lag]).sum(axis=1) / (norms[lag:] * norms[:-lag])
        change = np.ones(band.shape[0])
        change[lag:] = 1.0 - np.clip(np.nan_to_num(similarity), -1.0, 1.0)
        steady &= change < _STEADY_SHAPE_CHANGE
    evidence = judged & steady
    zeros = np.zeros((band.shape[0], 1))
    padded = np.concatenate((zeros, band, zeros), axis=1)
    interior = padded[:, 1:-1]
    peaks = (
        (interior > padded[:, :-2])
        & (interior >= padded[:, 2:])
        & (interior >= band.max(axis=1, keepdims=True) * 10.0 ** (-_PEAK_FLOOR_DB / 20.0))
    ).sum(axis=1)
    median_peaks = float(np.median(peaks[evidence]))
    return "tone" if median_peaks <= _TONE_PEAKS else "music"
