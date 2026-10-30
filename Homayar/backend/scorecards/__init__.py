"""Versioned QA scorecards: definitions, applicability and the score of one conversation
(HOM-54).

See ``README.md`` in this package.  A criterion wraps one ``conversation_checks`` check;
the results feed the HOM-69 agreement metrics.  HOM-56 stores the results.
"""

from backend.scorecards.engine import (
    applicability,
    parse_conversation,
    plan_scorecard,
    rating_pairs,
    score_conversation,
    score_pair,
)
from backend.scorecards.models import (
    CONVERSATION_DIRECTIONS,
    CONVERSATION_TYPES,
    KEY_PATTERN,
    REVIEWER_OUTCOMES,
    SCORECARDS_VERSION,
    Applicability,
    ConversationFacts,
    Criterion,
    CriterionResult,
    ScorecardDefinition,
    ScorecardError,
    ScorecardOutcome,
    ScorecardReason,
    ScorecardResult,
    definition_digest,
    valid_key,
    validate_definition,
)

__all__ = [
    "CONVERSATION_DIRECTIONS",
    "CONVERSATION_TYPES",
    "KEY_PATTERN",
    "REVIEWER_OUTCOMES",
    "SCORECARDS_VERSION",
    "Applicability",
    "ConversationFacts",
    "Criterion",
    "CriterionResult",
    "ScorecardDefinition",
    "ScorecardError",
    "ScorecardOutcome",
    "ScorecardReason",
    "ScorecardResult",
    "applicability",
    "definition_digest",
    "parse_conversation",
    "plan_scorecard",
    "rating_pairs",
    "score_conversation",
    "score_pair",
    "valid_key",
    "validate_definition",
]
