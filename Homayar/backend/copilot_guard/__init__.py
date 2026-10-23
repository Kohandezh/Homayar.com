"""Copilot guard: abstention, unsupported questions and prompt-injection defenses around
the two model steps of the Copilot (HOM-97, WP17-T06).

See ``README.md`` in this package.  It builds on ``backend.copilot_intents`` (HOM-92).  No
model is called here, and nothing in the backend uses it yet.
"""

from backend.copilot_guard.answers import (
    MAX_ANSWER_LENGTH,
    MAX_SENTENCE_LENGTH,
    MAX_SENTENCES,
    AnswerSentence,
    GuardedAnswer,
    check_answer,
)
from backend.copilot_guard.defects import GuardDefect
from backend.copilot_guard.framing import (
    FENCE_LENGTH,
    GUARD_VERSION,
    LABEL_PATTERN,
    SPAN_ID_PATTERN,
    EvidenceSpan,
    SourceKind,
    UntrustedText,
    frame_untrusted,
)
from backend.copilot_guard.messages import Explanation, explain
from backend.copilot_guard.plans import (
    MAX_PLAN_LENGTH,
    ToolName,
    allowed_tools,
    parse_plan,
    route_plan,
)
from backend.copilot_guard.prompts import (
    ANSWER_INSTRUCTIONS,
    PLANNER_INSTRUCTIONS,
    ModelPrompt,
    build_answer_prompt,
    build_planner_prompt,
)
from backend.copilot_guard.signals import InjectionSignal, scan_injection

__all__ = [
    "ANSWER_INSTRUCTIONS",
    "FENCE_LENGTH",
    "GUARD_VERSION",
    "LABEL_PATTERN",
    "MAX_ANSWER_LENGTH",
    "MAX_PLAN_LENGTH",
    "MAX_SENTENCES",
    "MAX_SENTENCE_LENGTH",
    "PLANNER_INSTRUCTIONS",
    "SPAN_ID_PATTERN",
    "AnswerSentence",
    "EvidenceSpan",
    "Explanation",
    "GuardDefect",
    "GuardedAnswer",
    "InjectionSignal",
    "ModelPrompt",
    "SourceKind",
    "ToolName",
    "UntrustedText",
    "allowed_tools",
    "build_answer_prompt",
    "build_planner_prompt",
    "check_answer",
    "explain",
    "frame_untrusted",
    "parse_plan",
    "route_plan",
    "scan_injection",
]
