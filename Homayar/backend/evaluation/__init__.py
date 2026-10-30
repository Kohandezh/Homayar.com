"""WP13 evaluators: WER/CER, DER/JER, intent, entities, QA agreement, factual support and
retrieval (HOM-69).

See ``README.md`` in this package.  Pure and deterministic: standard library plus
``backend.speech``.  File I/O only in ``loaders`` and the CLI (``python -m
backend.evaluation``).
"""

from backend.evaluation.agreement import (
    RatingPair,
    ScorePair,
    cohen_kappa,
    kappa_by_criterion,
    score_mae,
)
from backend.evaluation.asr import AsrReport, Utterance, evaluate_asr
from backend.evaluation.classification import (
    ClassificationReport,
    ClassStats,
    LabeledItem,
    evaluate_classification,
)
from backend.evaluation.common import EVALUATION_VERSION, MetricValue, evaluate_by_slice
from backend.evaluation.diarization import (
    DiarizationItem,
    DiarizationReport,
    DiarizationScore,
    SpeakerTurn,
    evaluate_diarization,
    score_diarization,
)
from backend.evaluation.entities import EntityItem, EntityMention, EntityReport, evaluate_entities
from backend.evaluation.factual_support import (
    AuditedClaim,
    Citation,
    FactualSupportReport,
    SupportLabel,
    citation_is_valid,
    evaluate_factual_support,
)
from backend.evaluation.loaders import KINDS, load_jsonl, parse_record
from backend.evaluation.retrieval import RetrievalQuery, RetrievalReport, evaluate_retrieval

__all__ = [
    "EVALUATION_VERSION",
    "KINDS",
    "AsrReport",
    "AuditedClaim",
    "Citation",
    "ClassStats",
    "ClassificationReport",
    "DiarizationItem",
    "DiarizationReport",
    "DiarizationScore",
    "EntityItem",
    "EntityMention",
    "EntityReport",
    "FactualSupportReport",
    "LabeledItem",
    "MetricValue",
    "RatingPair",
    "RetrievalQuery",
    "RetrievalReport",
    "ScorePair",
    "SpeakerTurn",
    "SupportLabel",
    "Utterance",
    "citation_is_valid",
    "cohen_kappa",
    "evaluate_asr",
    "evaluate_by_slice",
    "evaluate_classification",
    "evaluate_diarization",
    "evaluate_entities",
    "evaluate_factual_support",
    "evaluate_retrieval",
    "kappa_by_criterion",
    "load_jsonl",
    "parse_record",
    "score_diarization",
    "score_mae",
]
