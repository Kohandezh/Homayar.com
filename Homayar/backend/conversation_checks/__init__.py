"""Deterministic phrase checks and contextual criteria with qualified, targeted model
evaluation (HOM-55).

See ``README.md`` in this package.  Nothing in the backend uses it yet (HOM-54 scorecards
read its results; a later ticket wires a real evaluator and storage).
"""

from backend.conversation_checks.engine import finish_checks, plan_checks
from backend.conversation_checks.models import (
    CHECKS_VERSION,
    CUE_WINDOW_TOKENS,
    DOUBT_ORDER,
    MANIFEST_STATUSES,
    MAX_WINDOW_TURNS,
    MIN_ASR_CONFIDENCE,
    MIN_EVALUATOR_CONFIDENCE,
    CheckPlan,
    CheckResult,
    CheckRun,
    ChecksError,
    CheckTurn,
    ContextualCriterion,
    DecidedBy,
    Evaluation,
    EvaluationAnswer,
    EvaluationRequest,
    EvaluatorInfo,
    Outcome,
    PhraseCheck,
    ReasonCode,
    Scope,
    Span,
    definition_digest,
)
from backend.conversation_checks.text import NEGATION_CUES, NEGATION_PREFIXES, REPORTING_VERBS

__all__ = [
    "CHECKS_VERSION",
    "CUE_WINDOW_TOKENS",
    "DOUBT_ORDER",
    "MANIFEST_STATUSES",
    "MAX_WINDOW_TURNS",
    "MIN_ASR_CONFIDENCE",
    "MIN_EVALUATOR_CONFIDENCE",
    "NEGATION_CUES",
    "NEGATION_PREFIXES",
    "REPORTING_VERBS",
    "CheckPlan",
    "CheckResult",
    "CheckRun",
    "CheckTurn",
    "ChecksError",
    "ContextualCriterion",
    "DecidedBy",
    "Evaluation",
    "EvaluationAnswer",
    "EvaluationRequest",
    "EvaluatorInfo",
    "Outcome",
    "PhraseCheck",
    "ReasonCode",
    "Scope",
    "Span",
    "definition_digest",
    "finish_checks",
    "plan_checks",
]
