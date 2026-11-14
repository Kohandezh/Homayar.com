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
- The steady frames' median number of prominent peaks then separates tones
  (at most two steady frequencies, DTMF included) from music (chords,
  melodies), per frame, with the median's tolerance: a resample that moves
  one harmonic across the peak floor changes nothing.
- Speech can hide under steady partials (words over a dial tone, a dual tone
  or music at about the same level): the steady frames then outnumber the
  speech frames. So every partial that lasts ``_HOLD_FRAMES`` is removed
  from each frame, and the span is speech when at least ``_SPEECH_SHARE`` of
  the judged frames keep a clear share of their energy that changes shape.
  "Clear" depends on how many steady partials the background has (counted
  on the steady level within ``_LEVEL_PEAK_FLOOR_DB``): at most two (a tone)
  leave almost nothing behind (``_TONE_LEFTOVER``); more (music) leave some
  vibrato, beating and note-change energy (``_MUSIC_LEFTOVER``).

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
_HOLD_FRAMES: Final = 8  # a partial that lasts this long (128 ms) is steady
_TONE_LEFTOVER: Final = 0.05  # energy share left over a tone: speech-like
_MUSIC_LEFTOVER: Final = 0.20  # energy share left over music: speech-like
_SPEECH_SHARE: Final = 0.09  # this share of judged frames speech-like: speech
_PEAK_FLOOR_DB: Final = 14.0  # in-frame peaks this far under the loudest
_TONE_PEAKS: Final = 2  # at most this many steady peaks: a tone
_LEVEL_PEAK_FLOOR_DB: Final = 20.0  # steady-level peaks this far under the loudest
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

    norms = np.linalg.norm(band, axis=1)
    steady = np.ones(band.shape[0], dtype=bool)
    for lag in _SHAPE_LAGS_FRAMES:
        steady &= _shape_change(np, band, norms, lag) < _STEADY_SHAPE_CHANGE
    evidence = judged & steady
    if float(evidence.sum()) / judged_count < _STEADY_FRACTION:
        return "speech"

    frame_peaks = _peak_counts(np, band, _PEAK_FLOOR_DB)
    kind = "tone" if float(np.median(frame_peaks[evidence])) <= _TONE_PEAKS else "music"

    # Speech under steady partials: remove every partial that lasts, then
    # look for what is left. The steady level of a bin is its lowest
    # magnitude over the _HOLD_FRAMES frames that end at the frame or over
    # those that start at it, whichever is higher, so a note that starts or
    # ends inside the window still counts as steady. A frame is speech-like
    # when the energy left above that level is a clear share of the frame
    # and it changes shape like speech does. A background of at most two
    # steady partials (a tone) leaves almost nothing above it; more partials
    # (music) leave some (vibrato, beating partials, note changes), so they
    # need a larger share. The partials are counted on the steady level, not
    # on the frame, so the speech's own peaks never change the choice.
    hold = _HOLD_FRAMES
    edged = np.pad(band, ((hold - 1, hold - 1), (0, 0)), mode="edge")
    lowest = np.lib.stride_tricks.sliding_window_view(edged, hold, axis=0).min(axis=2)
    count = band.shape[0]
    level = np.maximum(lowest[:count], lowest[hold - 1 : hold - 1 + count])
    steady_partials = float(np.median(_peak_counts(np, level, _LEVEL_PEAK_FLOOR_DB)[judged]))
    leftover = np.maximum(band - level, 0.0)
    leftover_share = (leftover**2).sum(axis=1) / np.maximum(energy, 1e-30)
    leftover_norms = np.linalg.norm(leftover, axis=1)
    moving = np.ones(band.shape[0], dtype=bool)
    for lag in _SHAPE_LAGS_FRAMES:
        moving &= _shape_change(np, leftover, leftover_norms, lag) >= _STEADY_SHAPE_CHANGE
    floor = _TONE_LEFTOVER if steady_partials <= _TONE_PEAKS else _MUSIC_LEFTOVER
    speech_like = judged & moving & (leftover_share >= floor)
    if float(speech_like.sum()) / judged_count >= _SPEECH_SHARE:
        return "speech"
    return kind


def _peak_counts(np, spectra, floor_db: float):
    """Prominent peaks per frame: local maxima within ``floor_db`` of its loudest bin."""
    zeros = np.zeros((spectra.shape[0], 1))
    padded = np.concatenate((zeros, spectra, zeros), axis=1)
    interior = padded[:, 1:-1]
    floor = spectra.max(axis=1, keepdims=True) * 10.0 ** (-floor_db / 20.0)
    return ((interior > padded[:, :-2]) & (interior >= padded[:, 2:]) & (interior >= floor)).sum(
        axis=1
    )


def _shape_change(np, spectra, norms, lag: int):
    """1 - cosine similarity of each frame's spectrum against ``lag`` frames back.

    Frames without a reference, or with a silent spectrum, change fully (1.0).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        similarity = (spectra[lag:] * spectra[:-lag]).sum(axis=1) / (norms[lag:] * norms[:-lag])
    change = np.ones(spectra.shape[0])
    change[lag:] = 1.0 - np.clip(np.nan_to_num(similarity), -1.0, 1.0)
    return change
