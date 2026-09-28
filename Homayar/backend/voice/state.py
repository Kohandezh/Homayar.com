"""Legal call-state transitions and fencing checks (pure, no I/O)."""

from __future__ import annotations

from backend.voice.models import CallState

LEGAL_TRANSITIONS: dict[CallState, frozenset[CallState]] = {
    CallState.ADMITTED: frozenset(
        {CallState.RINGING, CallState.ACTIVE, CallState.ENDED, CallState.FAILED}
    ),
    CallState.RINGING: frozenset({CallState.ACTIVE, CallState.ENDED, CallState.FAILED}),
    CallState.ACTIVE: frozenset({CallState.ENDED, CallState.FAILED}),
    CallState.ENDED: frozenset(),
    CallState.FAILED: frozenset(),
    CallState.REJECTED: frozenset(),
}

ACTION_TARGETS: dict[str, CallState] = {
    "ring": CallState.RINGING,
    "answer": CallState.ACTIVE,
    "end": CallState.ENDED,
    "fail": CallState.FAILED,
}


class VoiceStateError(Exception):
    code = "voice_state_error"
    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.public_message = message


class IllegalTransition(VoiceStateError):
    code = "illegal_transition"


class StaleGeneration(VoiceStateError):
    code = "stale_generation"


def is_legal(current: CallState, target: CallState) -> bool:
    return target in LEGAL_TRANSITIONS[current]


def assert_transition(current: CallState, target: CallState) -> None:
    if not is_legal(current, target):
        raise IllegalTransition(f"Cannot move a call from {current.value} to {target.value}")


def assert_generation(
    *, presented: int, session_generation: int | None, node_generation: int, node_lost: bool
) -> None:
    """A write is accepted only from the node incarnation that holds the call."""
    if (
        node_lost
        or session_generation is None
        or presented != session_generation
        or (presented != node_generation)
    ):
        raise StaleGeneration("The media node generation is stale")
