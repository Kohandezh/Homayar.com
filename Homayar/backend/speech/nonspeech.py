"""Cheap CPU check whether an audio span is music or a tone (HOM-348, ADR 0078).

The ASR gate calls :func:`classify_span` on the audio under each decoded
segment. It reads only the samples and the segment times: no model, no scipy,
no librosa. Loudness never matters: only frames within ``_ACTIVE_FLOOR_DB`` of
the span's loudest frame are judged, never an absolute dBFS floor.

The rule (measured on the SPEC signals, ADR 0078): music and tones have a
time-invariant spectrum, speech does not. A frame is *evidence* when its
magnitude spectrum barely changes against 32 ms and 48 ms earlier and its
number of spectral peaks equals the span's most common peak count. When at
least ``_EVIDENCE_FRACTION`` of the judged frames are evidence, the span is
non-speech, and the evidence frames' median peak count separates tones (at
most two steady frequencies, DTMF included) from music (chords, melodies).

Unsure is speech: dropping real speech is worse than keeping one invented
word. numpy is imported inside the function, so importing this module is free
and the Whisper adapter stays importable without numpy.
"""

from __future__ import annotations

from typing import Final

NONSPEECH_VERSION: Final = "nonspeech/1"

_FRAME_SECONDS: Final = 0.032  # Hann-windowed frame length
_HOP_SECONDS: Final = 0.016  # hop between frames
_MIN_SECONDS: Final = 0.3  # a shorter span cannot be judged
_LOW_HZ: Final = 100.0  # the band judged: the phone band and a little more
_HIGH_HZ: Final = 3800.0
_ACTIVE_FLOOR_DB: Final = 35.0  # active frames: this far under the loudest
_SHAPE_LAGS_FRAMES: Final = (2, 3)  # the spectrum is compared 32 and 48 ms back
_STEADY_SHAPE_CHANGE: Final = 0.2  # 1 - cosine similarity under this: steady
_PEAK_FLOOR_DB: Final = 14.0  # in-frame peaks this far under the loudest
_EVIDENCE_FRACTION: Final = 0.45  # this share of judged frames: non-speech
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

    zeros = np.zeros((band.shape[0], 1))
    padded = np.concatenate((zeros, band, zeros), axis=1)
    interior = padded[:, 1:-1]
    peaks = (
        (interior > padded[:, :-2])
        & (interior >= padded[:, 2:])
        & (interior >= band.max(axis=1, keepdims=True) * 10.0 ** (-_PEAK_FLOOR_DB / 20.0))
    ).sum(axis=1)

    judged = active.copy()
    steady = np.ones(band.shape[0], dtype=bool)
    norms = np.linalg.norm(band, axis=1)
    for lag in _SHAPE_LAGS_FRAMES:
        reference = np.zeros_like(judged)
        reference[lag:] = active[:-lag]
        judged &= reference
        with np.errstate(divide="ignore", invalid="ignore"):
            similarity = (band[lag:] * band[:-lag]).sum(axis=1) / (norms[lag:] * norms[:-lag])
        change = np.ones(band.shape[0])
        change[lag:] = 1.0 - np.clip(similarity, -1.0, 1.0)
        steady &= change < _STEADY_SHAPE_CHANGE
    judged[:_MAX_LAG_FRAMES] = False
    judged_count = int(judged.sum())
    if judged_count < _MIN_JUDGED_FRAMES:
        return "speech"

    # The most common peak count of the active frames; ties take the smaller.
    values, counts = np.unique(peaks[active], return_counts=True)
    mode = int(values[np.argmax(counts)])
    evidence = judged & steady & (peaks == mode)
    if evidence.sum() / judged_count < _EVIDENCE_FRACTION:
        return "speech"
    median_peaks = float(np.median(peaks[evidence]))
    return "tone" if median_peaks <= _TONE_PEAKS else "music"
