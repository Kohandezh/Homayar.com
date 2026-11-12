"""WP13 evaluators: WER/CER, DER/JER, intent, entities, QA agreement, factual support and
retrieval (HOM-69), and action item owner and due date (HOM-133).

See ``README.md`` in this package.  Pure and deterministic: standard library plus
``backend.speech``.  File I/O only in ``loaders`` and the CLI (``python -m
backend.evaluation``).
"""

from backend.evaluation.action_items import (
    ActionItemCase,
    ActionItemReport,
    evaluate_action_items,
)
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

_CANDIDATE_EXPORTS = (
    "CANDIDATE_REPORT_VERSION",
    "Measurement",
    "build_candidate_report",
    "load_measurements",
    "parse_measurement",
)

__all__ = [
    "CANDIDATE_REPORT_VERSION",
    "EVALUATION_VERSION",
    "KINDS",
    "ActionItemCase",
    "ActionItemReport",
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
    "Measurement",
    "MetricValue",
    "RatingPair",
    "RetrievalQuery",
    "RetrievalReport",
    "ScorePair",
    "SpeakerTurn",
    "SupportLabel",
    "Utterance",
    "build_candidate_report",
    "citation_is_valid",
    "cohen_kappa",
    "evaluate_action_items",
    "evaluate_asr",
    "evaluate_by_slice",
    "evaluate_classification",
    "evaluate_diarization",
    "evaluate_entities",
    "evaluate_factual_support",
    "evaluate_retrieval",
    "kappa_by_criterion",
    "load_jsonl",
    "load_measurements",
    "parse_measurement",
    "parse_record",
    "score_diarization",
    "score_mae",
]


def __getattr__(name: str) -> object:
    # Lazy so `python -m backend.evaluation.candidate` does not re-run an imported module.
    if name in _CANDIDATE_EXPORTS:
        from backend.evaluation import candidate

        return getattr(candidate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
