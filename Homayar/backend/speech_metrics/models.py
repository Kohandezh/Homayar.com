"""Version, limits, reason codes and the result types of the speech metrics (HOM-51).

Every type here is a frozen dataclass of plain values.  ``as_dict`` gives the JSON-safe
shape that a later unit stores or returns; nothing here reads a database or a file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

METRICS_VERSION = "speech-metrics/1"

# Part of version 1: a new value is a new version.
MAX_UNATTRIBUTED_SHARE = 0.2  # above this share of unknown speech, talk_share is withheld
MIN_RATE_SPEECH_MS = 5000  # a rate over less speech time than this is withheld

# Every reason a metric can be not computable, in precedence order: when several apply,
# the first one in this tuple is reported.
REASON_CODES = (
    "diarization_unavailable",
    "duration_unknown",
    "zero_length_call",
    "no_speech",
    "no_attributed_speech",
    "too_much_unattributed_speech",
    "word_counts_missing",
    "too_little_speech",
)

# Speaker keys that mean "nobody was identified", compared after strip() and casefold().
# "unknown" is the TranscriptSegment column default; "speaker_unknown" is the label the
# pipeline writes (backend.providers UNKNOWN_SPEAKER_LABEL, kept here as a copy so this
# package does not import providers).
UNKNOWN_SPEAKER_KEYS = frozenset({"", "unknown", "speaker_unknown"})

Status = Literal["computed", "not_computable"]
Unit = Literal["ms", "ratio", "words_per_minute"]


class SpeechMetricsError(ValueError):
    """Invalid input: ``invalid_interval``, ``invalid_duration`` or ``invalid_speakers``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SpeechInterval:
    """One stretch of speech, half-open ``[start_ms, end_ms)``.

    The constructor accepts any values; ``compute_speech_metrics`` validates them.
    """

    start_ms: int
    end_ms: int
    speaker_key: str | None
    word_count: int | None = None  # None when the words of the stretch are not known


@dataclass(frozen=True)
class Measurement:
    """One metric value, or the reason it could not be computed.

    ``numerator`` and ``denominator`` are the exact integers behind ``value``: for ms the
    value and None, for a ratio the two terms, for a rate the word sum and the time in ms.
    Both are None when not computable.
    """

    status: Status
    value: int | float | None
    unit: Unit
    reason: str | None
    numerator: int | None
    denominator: int | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SpeakerMetrics:
    talk_ms: Measurement
    talk_share: Measurement  # share of the sum of all known speakers' talk time
    talk_ratio_of_call: Measurement  # share of the call duration
    speaking_rate_wpm: Measurement

    def as_dict(self) -> dict[str, Any]:
        return {
            "talk_ms": self.talk_ms.as_dict(),
            "talk_share": self.talk_share.as_dict(),
            "talk_ratio_of_call": self.talk_ratio_of_call.as_dict(),
            "speaking_rate_wpm": self.speaking_rate_wpm.as_dict(),
        }


# The whole-call measurements of SpeechMetrics, in output order.
CALL_MEASUREMENTS = (
    "call_duration_ms",
    "speech_ms",
    "silence_ms",
    "speech_ratio",
    "silence_ratio",
    "attributed_speech_ms",
    "unattributed_speech_ms",
    "overlap_ms",
    "overlap_ratio_of_speech",
    "speaking_rate_wpm",
)


@dataclass(frozen=True)
class SpeechMetrics:
    version: str
    call_duration_ms: Measurement
    speech_ms: Measurement
    silence_ms: Measurement
    speech_ratio: Measurement
    silence_ratio: Measurement
    attributed_speech_ms: Measurement
    unattributed_speech_ms: Measurement
    overlap_ms: Measurement
    overlap_ratio_of_speech: Measurement
    speaking_rate_wpm: Measurement
    speakers: dict[str, SpeakerMetrics]  # known speakers, sorted by key; empty without diarization
    input_summary: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"version": self.version}
        for name in CALL_MEASUREMENTS:
            data[name] = getattr(self, name).as_dict()
        data["speakers"] = {key: speaker.as_dict() for key, speaker in self.speakers.items()}
        data["input_summary"] = dict(self.input_summary)
        return data
