"""Calibration batch API (HOM-75, ADR 0056).

Every request body forbids unknown keys, and a body that fails these rules is the
app's usual ``422 validation_error`` and writes nothing (SPEC-pr2 "Response shapes").
The create body: a title of 1 to 200 characters that is not blank, 1 to 50 distinct
scorecard result ids and 1 to 20 distinct participant user ids.  The answer and
adjudication bodies carry an outcome of the frozen list (an unknown outcome is
refused), and an adjudication note holds at most 2000 characters.  The service then
checks what needs the database, with the calibration codes.

Every response key is always present; a list the caller may not see is ``[]``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from backend.database.saas_models import (
    CALIBRATION_MAX_EXAMPLES,
    CALIBRATION_MAX_PARTICIPANTS,
    CALIBRATION_TITLE_MAX_LENGTH,
    REVIEW_NOTE_MAX_LENGTH,
)

# ``calibration_batches.revision`` is a PostgreSQL integer; a larger number is no
# revision.
MAX_REVISION = 2**31 - 1


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


def _distinct(values: list[str]) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError("ids must be distinct")
    return values


def _storable(value: str | None) -> str | None:
    # PostgreSQL text refuses a NUL character and UTF-8 cannot hold a lone surrogate;
    # both would reach the database only as a driver error.
    if value is None:
        return None
    if "\x00" in value:
        raise ValueError("must not contain a NUL character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("must be valid text") from None
    return value


ReferenceId = Annotated[str, StringConstraints(min_length=1, max_length=36)]
ExpectedRevision = Annotated[int, Field(ge=1, le=MAX_REVISION, strict=True)]
Outcome = Literal["passed", "failed", "not_applicable"]
Title = Annotated[
    str,
    StringConstraints(min_length=1, max_length=CALIBRATION_TITLE_MAX_LENGTH),
    AfterValidator(_not_blank),
    AfterValidator(_storable),
]
ResultIds = Annotated[
    list[ReferenceId],
    Field(min_length=1, max_length=CALIBRATION_MAX_EXAMPLES),
    AfterValidator(_distinct),
]
ParticipantIds = Annotated[
    list[ReferenceId],
    Field(min_length=1, max_length=CALIBRATION_MAX_PARTICIPANTS),
    AfterValidator(_distinct),
]
Note = Annotated[
    str | None,
    Field(max_length=REVIEW_NOTE_MAX_LENGTH),
    AfterValidator(_storable),
]


# --- the batch view -----------------------------------------------------------------------


class CalibrationCriterionView(BaseModel):
    criterion_id: str
    position: int
    weight: int
    na_allowed: bool
    reviewable: bool


class CalibrationExampleView(BaseModel):
    id: str
    position: int
    meeting_id: str
    scorecard_result_id: str
    criteria: list[CalibrationCriterionView]


class CalibrationParticipantView(BaseModel):
    id: str
    user_id: str | None


class CalibrationMyAnswerView(BaseModel):
    example_id: str
    criterion_id: str
    outcome: str


class CalibrationAdjudicationView(BaseModel):
    example_id: str
    criterion_id: str
    outcome: str
    note: str | None


class CalibrationProgressView(BaseModel):
    participant_id: str
    user_id: str | None
    answered: int
    expected: int


class CalibrationAnswerView(BaseModel):
    participant_id: str
    user_id: str | None
    example_id: str
    criterion_id: str
    outcome: str


class CalibrationBatchResponse(BaseModel):
    id: str
    title: str
    status: str
    revision: int
    created_by_user_id: str | None
    created_at: datetime
    closed_at: datetime | None
    cancelled_at: datetime | None
    participants: list[CalibrationParticipantView]
    examples: list[CalibrationExampleView]
    my_answers: list[CalibrationMyAnswerView]  # [] for a non-participant
    adjudications: list[CalibrationAdjudicationView]  # [] for a participant before close
    progress: list[CalibrationProgressView]  # [] except for a non-participant admin
    answers: list[CalibrationAnswerView]  # [] except for a non-participant admin after close


class CalibrationBatchSummary(BaseModel):
    id: str
    title: str
    status: str
    revision: int
    created_at: datetime
    closed_at: datetime | None
    cancelled_at: datetime | None
    example_count: int
    participant_count: int


class CalibrationBatchListResponse(BaseModel):
    items: list[CalibrationBatchSummary]  # ``created_at`` then ``id``, descending
    next_cursor: str | None


# --- the report ---------------------------------------------------------------------------


class CalibrationAgreementBlock(BaseModel):
    expected: int
    compared: int
    matched: int
    missing: int
    agreement_bp: int | None
    kappa_e4: int | None
    confusion: dict[str, dict[str, int]]


class CalibrationParticipantReport(BaseModel):
    participant_id: str
    user_id: str | None  # null when the user was removed
    expected: int
    compared: int
    matched: int
    missing: int
    agreement_bp: int | None
    kappa_e4: int | None
    confusion: dict[str, dict[str, int]]


class CalibrationCriterionReport(BaseModel):
    criterion_id: str
    compared: int
    matched: int
    agreement_bp: int | None


class CalibrationReportResponse(BaseModel):
    batch_id: str
    overall: CalibrationAgreementBlock
    participants: list[CalibrationParticipantReport]
    criteria: list[CalibrationCriterionReport]
    machine: CalibrationAgreementBlock | None  # null for a non-admin participant


# --- the request bodies ---------------------------------------------------------------------


class CalibrationBatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Title
    scorecard_result_ids: ResultIds  # the examples, in this order
    participant_user_ids: ParticipantIds


class CalibrationAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome


class CalibrationAdjudicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Outcome
    note: Note = None


class CalibrationBatchWriteRequest(BaseModel):
    """The body of close and cancel: the revision the caller read."""

    model_config = ConfigDict(extra="forbid")

    expected_revision: ExpectedRevision
