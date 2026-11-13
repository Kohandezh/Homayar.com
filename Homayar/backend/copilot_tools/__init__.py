"""Read-only, allowlisted analytical tools of the Copilot (HOM-96, WP17-T05).

See ``README.md`` in this package.  The registry, the params and the results are pure.
``backend.services.copilot_tool_service.run_tool`` runs a tool against the database.
"""

from backend.copilot_tools.params import (
    ACCEPTED_KEYS,
    FILTER_KEYS,
    OWN_KEYS,
    ParamError,
    check_params,
)
from backend.copilot_tools.registry import (
    DEFAULT_LIMITS,
    LIMIT_CAPS,
    REGISTRY_DIGEST,
    TOOLS,
    TOOLS_VERSION,
    ToolLimits,
    ToolSpec,
    check_limits,
    registry_digest,
    tool_schemas,
)
from backend.copilot_tools.results import (
    AbstainReason,
    Fact,
    ParamDefect,
    ResultStatus,
    ToolResult,
    round_half_even_2,
)

__all__ = [
    "ACCEPTED_KEYS",
    "DEFAULT_LIMITS",
    "FILTER_KEYS",
    "LIMIT_CAPS",
    "OWN_KEYS",
    "REGISTRY_DIGEST",
    "TOOLS",
    "TOOLS_VERSION",
    "AbstainReason",
    "Fact",
    "ParamDefect",
    "ParamError",
    "ResultStatus",
    "ToolLimits",
    "ToolResult",
    "ToolSpec",
    "check_limits",
    "check_params",
    "registry_digest",
    "round_half_even_2",
    "tool_schemas",
]
