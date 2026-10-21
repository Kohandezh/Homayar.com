"""Speech, silence, talk ratio, overlap and speaking rate from intervals (HOM-51).

See ``README.md`` in this package.  Nothing in the backend uses it yet (later WP11 and
WP12 work wires it into the pipeline and reports).
"""

from backend.speech_metrics.compute import compute_speech_metrics, intervals_from_segments
from backend.speech_metrics.models import (
    MAX_UNATTRIBUTED_SHARE,
    METRICS_VERSION,
    MIN_RATE_SPEECH_MS,
    REASON_CODES,
    UNKNOWN_SPEAKER_KEYS,
    Measurement,
    SpeakerMetrics,
    SpeechInterval,
    SpeechMetrics,
    SpeechMetricsError,
)
from backend.speech_metrics.words import count_words

__all__ = [
    "MAX_UNATTRIBUTED_SHARE",
    "METRICS_VERSION",
    "MIN_RATE_SPEECH_MS",
    "REASON_CODES",
    "UNKNOWN_SPEAKER_KEYS",
    "Measurement",
    "SpeakerMetrics",
    "SpeechInterval",
    "SpeechMetrics",
    "SpeechMetricsError",
    "compute_speech_metrics",
    "count_words",
    "intervals_from_segments",
]
