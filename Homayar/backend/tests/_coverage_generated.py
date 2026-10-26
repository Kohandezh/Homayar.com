"""HOM-312: a seeded, randomized set of coverage shapes for the parity test.

``seed_generated`` places many calls, each a random mix of the shapes the ``coverage/1``
rules read (``coverage_service.py``): rejected ledger rows with reasons, rows without a
meeting, deleted meetings, every meeting status, revisions with and without segments, mock
revisions and runs, runs in every ``StageState``, stale runs with failed recomputes, jobs
with several attempts and stage runs, summaries of both schema versions with well-formed,
missing and malformed key points, AI and human action items, and evidence spans of every
finding type (``extraction`` too), of the current run, of another run and of no run.  A few
fixed calls make sure every reachable bucket gets at least one meeting.

Rows are written as the DBA (``seed_call`` of ``test_coverage``), so every state can be
placed.  The same ``random.Random`` seed gives the same shapes.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from backend.database.saas_models import (
    AnalysisRun,
    Entitlement,
    EvidenceSpan,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    ProcessingStageRun,
    StageState,
    Team,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptSource,
    UsageReservation,
)
from backend.tests._database import inspection_session_factory
from backend.tests.test_coverage import BASE, Tenant, seed_call

STATUSES = (
    *(MeetingStatus.COMPLETED,) * 8,
    *(MeetingStatus.PARTIAL,) * 2,
    MeetingStatus.FAILED,
    MeetingStatus.CANCELLED,
    MeetingStatus.QUEUED,
    MeetingStatus.TRANSCRIBING,
    MeetingStatus.ANALYZING,
)
RUN_STATES = (*(StageState.COMPLETED,) * 6, *StageState)
GROUNDING = ("sufficient", "sufficient", "partial", "insufficient", "bogus", None)
PLACEHOLDERS = ("@insight", "@summary", "@action_item", "@human", "@other_run")
MALFORMED = (
    "a key point that is a string",
    7,
    None,
    ["a", "list"],
    {"category": "no evidence ids"},
    {"category": "ids not a list", "evidence_ids": "abc", "evidence_status": "sufficient"},
    {"category": "ids of other kinds", "evidence_ids": [5, None, ""], "evidence_status": "partial"},
    {"section": "unknown_section", "evidence_ids": ["@insight"], "evidence_status": "sufficient"},
    {"section": "summary_field", "category": "not_a_field", "status": "stated"},
    {"section": "summary_field", "category": "purpose", "status": "stated"},
    {"section": "decision", "evidence_status": "sufficient"},
    {"section": 3, "evidence_ids": ["@insight"]},
)


@dataclass
class Generated:
    seed: int
    meetings: list[str] = field(default_factory=list)
    teams: list[str] = field(default_factory=list)


def _teams(tenant: Tenant, count: int) -> list[str]:
    with inspection_session_factory()() as db:
        teams = [
            Team(workspace_id=tenant.workspace_id, name=f"Parity {n}", slug=f"parity-{n}")
            for n in range(count)
        ]
        db.add_all(teams)
        db.commit()
        return [team.id for team in teams]


def _key_points(rng: random.Random) -> list[Any]:
    points: list[Any] = []
    for _ in range(rng.randint(0, 3)):  # insights
        point: dict[str, Any] = {"category": rng.choice(("billing", "decision", "pricing"))}
        if rng.random() < 0.9:
            point["evidence_ids"] = rng.sample(PLACEHOLDERS, rng.randint(0, 2))
        status = rng.choice(GROUNDING)
        if status is not None:
            point["evidence_status"] = status
        points.append(point)
    for name in ("purpose", "resolution", "next_steps", "purpose"):  # a repeated category
        if rng.random() < 0.3:
            continue
        stated = rng.random() < 0.8
        point = {"section": "summary_field", "category": name}
        point["status"] = "stated" if stated else "not_stated"
        if stated:
            point["evidence_ids"] = rng.sample(
                ("@summary", "@insight", "@human"), rng.randint(0, 2)
            )
            point["evidence_status"] = rng.choice(GROUNDING)
        points.append(point)
    for section in ("decision", "key_moment"):
        for _ in range(rng.randint(0, 2)):
            points.append(
                {
                    "section": section,
                    "category": "decision" if section == "decision" else "commitment",
                    "evidence_ids": rng.sample(PLACEHOLDERS, rng.randint(0, 2)),
                    "evidence_status": rng.choice(GROUNDING),
                }
            )
    if rng.random() < 0.25:
        points.append(rng.choice(MALFORMED[4:]))  # objects; the others are added by _mutate
    rng.shuffle(points)
    return points


def _call_shape(rng: random.Random, teams: list[str]) -> dict[str, Any]:
    roll = rng.random()
    shape: dict[str, Any] = {
        "received_at": BASE + timedelta(minutes=rng.randint(0, 23 * 60)),
        "team_id": rng.choice([*teams, None]),
        "language": rng.choice(("fa", "en")),
        "source": rng.choice(("upload", "sip", "api")),
    }
    if roll < 0.05:
        shape.update(ledger="rejected", reason=rng.choice(("unsupported_media", "too_long")))
        return shape
    if roll < 0.08:
        shape["with_meeting"] = False
        return shape
    shape["status"] = rng.choice(STATUSES)
    shape["deleted"] = rng.random() < 0.05
    shape["segments"] = rng.choice((None, 0, 1, 2, 2, 2))
    shape["revision_source"] = rng.choice((TranscriptSource.ASR, TranscriptSource.CORRECTION))
    shape["diarization"] = rng.choice(("available", None))
    shape["run"] = rng.choice(("current", "current", "current", "stale", None))
    shape["run_state"] = rng.choice(RUN_STATES)
    shape["run_mock"] = rng.random() < 0.08
    shape["failed_recomputes"] = rng.choice((0, 0, 1, 2, 3))
    shape["summary"] = rng.random() < 0.85
    shape["summary_status"] = rng.choice(("sufficient", "sufficient", "partial", "insufficient"))
    shape["overview_cited"] = rng.random() < 0.85
    shape["schema_version"] = rng.choice((1, 2, 2))
    shape["key_points"] = _key_points(rng)
    shape["actions"] = [
        {
            "by": rng.choice(("ai", "ai", "ai", "person")),
            "status": rng.choice(("sufficient", "sufficient", "partial", "insufficient")),
            "cited": rng.random() < 0.8,
        }
        for _ in range(rng.randint(0, 3))
    ]
    shape["asr_state"] = rng.choice((None, StageState.COMPLETED, StageState.PARTIAL))
    return shape


# Fixed calls: every reachable evaluation bucket and every processing bucket at least once.
ANCHORS: tuple[dict[str, Any], ...] = (
    {},  # fully evaluated
    {"asr_state": StageState.PARTIAL},  # transcript_partial
    {"run": None},  # analysis_unavailable
    {"run_state": StageState.PARTIAL},  # analysis_partial
    {"status": MeetingStatus.PARTIAL},  # analysis_partial (status)
    {"run": "stale", "failed_recomputes": 3},  # analysis_stale (and the recompute limit)
    {"run_mock": True},  # mock_analysis
    {"actions": [{"status": "insufficient"}]},  # unsupported_findings
    {"deleted": True},  # processing deleted
    {"status": MeetingStatus.QUEUED},  # processing in_progress
    {"status": MeetingStatus.FAILED},  # processing failed
    {"segments": 0},  # processing no_transcript
    {"ledger": "rejected", "reason": "unsupported_media"},  # ingestion rejected
)


def _mutate(tenant: Tenant, meeting_id: str, rng: random.Random) -> None:
    """Shapes ``seed_call`` can not place: a mock revision, more jobs and stage runs, key
    points that are not objects, and spans of an ``extraction`` or of another run."""
    with inspection_session_factory()() as db:
        revision = db.scalar(
            select(TranscriptRevision).where(
                TranscriptRevision.meeting_id == meeting_id, TranscriptRevision.is_current.is_(True)
            )
        )
        if revision is None:
            return
        if rng.random() < 0.08:
            db.execute(
                update(TranscriptRevision)
                .where(TranscriptRevision.id == revision.id)
                .values(is_mock=True)
            )
        if rng.random() < 0.3:
            _jobs(db, tenant, meeting_id, rng)
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        if summary is not None and rng.random() < 0.15:
            points = [*summary.key_points, rng.choice(MALFORMED[:4])]
            db.execute(
                update(MeetingSummary)
                .where(MeetingSummary.id == summary.id)
                .values(key_points=points)
            )
        segment_id = db.scalar(
            select(TranscriptSegment.id).where(TranscriptSegment.revision_id == revision.id)
        )
        run_ids = list(
            db.scalars(select(AnalysisRun.id).where(AnalysisRun.meeting_id == meeting_id))
        )
        if segment_id is not None and run_ids and rng.random() < 0.3:
            for finding_type in rng.sample(("extraction", "insight", "summary", "action_item"), 2):
                db.add(
                    EvidenceSpan(
                        workspace_id=tenant.workspace_id,
                        meeting_id=meeting_id,
                        analysis_run_id=rng.choice([*run_ids, None]),
                        transcript_revision_id=revision.id,
                        segment_id=segment_id,
                        speaker_key="speaker_1",
                        finding_type=finding_type,
                        finding_id=rng.choice((None, str(uuid.uuid4()))),
                        finding_index=rng.choice((None, 0, 1)),
                        text_start=0,
                        text_end=4,
                        start_ms=0,
                        end_ms=900,
                        evidence_type="turn_reference",
                    )
                )
        db.commit()


def _jobs(db: Any, tenant: Tenant, meeting_id: str, rng: random.Random) -> None:
    """One or two more jobs, each with attempts and stage runs in any order; the newest job
    (by ``created_at``, then id) and its current attempt decide ``transcript_partial``."""
    entitlement_id = db.scalar(
        select(Entitlement.id).where(
            Entitlement.workspace_id == tenant.workspace_id, Entitlement.metric == "audio_seconds"
        )
    )
    for index in range(rng.randint(1, 2)):
        reservation = UsageReservation(
            workspace_id=tenant.workspace_id,
            entitlement_id=entitlement_id,
            meeting_id=meeting_id,
            idempotency_key=f"parity:{uuid.uuid4()}",
            reserved_value=30,
        )
        db.add(reservation)
        db.flush()
        attempts = rng.randint(1, 2)
        job = ProcessingJob(
            workspace_id=tenant.workspace_id,
            meeting_id=meeting_id,
            reservation_id=reservation.id,
            idempotency_key=f"parity:{uuid.uuid4()}",
            stage="completed",
            attempt_count=attempts,
            max_attempts=3,
            created_at=BASE - timedelta(minutes=rng.choice((0, 0, 5, 10 + index))),
        )
        db.add(job)
        db.flush()
        for attempt in range(1, attempts + 1):
            stages = rng.sample(("asr", "diarization", "intelligence"), rng.randint(1, 3))
            for sequence, stage in enumerate(stages):
                db.add(
                    ProcessingStageRun(
                        workspace_id=tenant.workspace_id,
                        meeting_id=meeting_id,
                        job_id=job.id,
                        attempt=attempt,
                        sequence=rng.randint(0, 9) * 10 + sequence,
                        stage=stage,
                        state=rng.choice(
                            (StageState.COMPLETED, StageState.PARTIAL, StageState.FAILED)
                        ),
                    )
                )
        db.flush()


def seed_generated(tenant: Tenant, *, seed: int, count: int = 150, teams: int = 3) -> Generated:
    """The anchors, then ``count`` random calls, in ``tenant``'s workspace."""
    rng = random.Random(seed)  # noqa: S311 - a reproducible test shape, not a secret
    generated = Generated(seed=seed, teams=_teams(tenant, teams))
    shapes = [{"team_id": generated.teams[n % teams], **anchor} for n, anchor in enumerate(ANCHORS)]
    shapes += [_call_shape(rng, generated.teams) for _ in range(count)]
    for shape in shapes:
        meeting_id = seed_call(tenant, **shape)
        if meeting_id is not None:
            generated.meetings.append(meeting_id)
            _mutate(tenant, meeting_id, rng)
    return generated
