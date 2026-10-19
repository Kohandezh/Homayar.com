"""WP04-T02: immutable transcript revisions and analysis-run provenance (ADR 0014).

Processing appends a revision with engine provenance and records the analysis
run its summary came from; reprocessing appends again and keeps a person's
speaker names; a correction is a new revision with the base as parent and the
original rows untouched; only the current revision feeds search, action-item
sources, shares and exports; committed rows refuse ORM updates; foreign
tenants see nothing; on PostgreSQL the shared-meeting ceiling covers the new
tables.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import ProgrammingError

from backend.database.saas_models import (
    AnalysisRun,
    AuditEvent,
    JobState,
    ProcessingJob,
    TranscriptImmutableError,
    TranscriptRevision,
    TranscriptSegment,
)
from backend.jobs.processor import process_job
from backend.security.context import CSRF_COOKIE
from backend.security.tokens import token_digest
from backend.tests import test_row_level_security as _rls
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.conftest import login_user, register_user
from backend.tests.test_insights_shares_api import _processed_meeting, _write_headers
from backend.tests.test_job_processor import _providers

probe_role = _rls.probe_role

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _read_headers(workspace_id: str) -> dict[str, str]:
    return {"X-Workspace-ID": workspace_id}


def _transcript(client, workspace_id: str, meeting_id: str, **params) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript",
        headers=_read_headers(workspace_id),
        params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _revisions(client, workspace_id: str, meeting_id: str) -> dict:
    response = client.get(
        f"/api/v1/meetings/{meeting_id}/transcript/revisions",
        headers=_read_headers(workspace_id),
    )
    assert response.status_code == 200, response.text
    return response.json()


def _correct(client, workspace_id: str, meeting_id: str, payload: dict):
    return client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=_write_headers(client, workspace_id),
        json=payload,
    )


def _requeue_for_reprocessing(meeting_id: str) -> str:
    """Put the completed job back in the queue as an operator would for a re-run."""
    with inspection_session_factory()() as db:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.meeting_id == meeting_id)
            .order_by(ProcessingJob.created_at.desc())
        )
        assert job is not None and job.state == JobState.COMPLETED
        job.state = JobState.QUEUED
        job.stage = "queued"
        job.completed_at = None
        db.commit()
        return job.id


def test_processing_appends_a_revision_with_engine_provenance_and_an_analysis_run(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)

    transcript = _transcript(client, workspace_id, meeting_id)
    revision = transcript["revision"]
    assert revision is not None
    assert (revision["revision_number"], revision["source"], revision["reason"]) == (
        1,
        "asr",
        "processing",
    )
    assert revision["is_current"] is True and revision["superseded_at"] is None
    assert revision["parent_revision_id"] is None and revision["created_by_user_id"] is None
    assert revision["segment_count"] == len(transcript["segments"]) == 1
    assert (revision["asr_provider"], revision["asr_model"]) == ("mock_asr", "deterministic-v1")
    assert revision["diarization_status"] == "available"
    assert revision["attempt"] == 1 and revision["job_id"] and revision["checkpoint_id"]
    assert HEX64.match(revision["config_hash"] or "")
    assert revision["is_mock"] is True  # the mock engines label their output

    listing = _revisions(client, workspace_id, meeting_id)
    assert listing["current_revision_id"] == revision["id"]
    assert [item["revision_number"] for item in listing["items"]] == [1]

    summary = client.get(
        f"/api/v1/meetings/{meeting_id}/summary", headers=_read_headers(workspace_id)
    )
    assert summary.status_code == 200, summary.text
    run = summary.json()["analysis_run"]
    assert summary.json()["analysis_run_id"] == run["id"]
    assert run["transcript_revision_id"] == revision["id"]
    assert (run["provider"], run["model"], run["state"]) == (
        "mock_intelligence",
        "deterministic-v1",
        "completed",
    )
    assert run["is_current"] is True and run["is_mock"] is True
    assert run["prompt_version"] is None  # the mock declares no prompt: nothing is invented
    assert HEX64.match(run["config_hash"] or "") and run["checkpoint_id"]

    with inspection_session_factory()() as db:
        stored = db.get(TranscriptRevision, revision["id"])
        assert stored is not None and stored.checkpoint_id == revision["checkpoint_id"]
        completed = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "meeting.processing.completed")
        )
        assert completed is not None
        assert completed.details["revision_number"] == 1
        assert completed.details["revision_id"] == revision["id"]
        assert completed.details["analysis_run_id"] == run["id"]


def test_reprocessing_appends_a_revision_and_keeps_speaker_names(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    first = _transcript(client, workspace_id, meeting_id)
    speaker_id = first["speakers"][0]["id"]
    renamed = client.patch(
        f"/api/v1/meetings/{meeting_id}/speakers/{speaker_id}",
        headers=_write_headers(client, workspace_id),
        json={"display_name": "کارشناس فروش"},
    )
    assert renamed.status_code == 200, renamed.text

    job_id = _requeue_for_reprocessing(meeting_id)
    result = process_job(job_id, worker_id="rerun", settings=settings, providers=_providers())
    assert result["status"] == "completed"

    second = _transcript(client, workspace_id, meeting_id)
    assert second["revision"]["revision_number"] == 2
    assert second["revision"]["reason"] == "reprocessing"
    assert second["revision"]["parent_revision_id"] is None  # derived from the audio, not rev 1
    assert second["segments"][0]["id"] != first["segments"][0]["id"]
    assert second["segments"][0]["speaker_name"] == "کارشناس فروش"  # the rename survived
    assert [s["id"] for s in second["speakers"]] == [speaker_id]

    listing = _revisions(client, workspace_id, meeting_id)
    assert [(i["revision_number"], i["is_current"]) for i in listing["items"]] == [
        (2, True),
        (1, False),
    ]
    superseded = next(i for i in listing["items"] if i["revision_number"] == 1)
    assert superseded["superseded_at"] is not None
    # the earlier revision stays readable, unchanged, by id
    old = _transcript(client, workspace_id, meeting_id, revision_id=first["revision"]["id"])
    assert old["revision"]["is_current"] is False
    assert [s["id"] for s in old["segments"]] == [first["segments"][0]["id"]]
    with inspection_session_factory()() as db:
        runs = db.scalars(
            select(AnalysisRun)
            .where(AnalysisRun.meeting_id == meeting_id)
            .order_by(AnalysisRun.created_at.asc())
        ).all()
        assert [run.is_current for run in runs] == [False, True]
        assert runs[1].transcript_revision_id == second["revision"]["id"]
        assert (
            db.scalar(
                select(TranscriptSegment.id).where(
                    TranscriptSegment.revision_id == first["revision"]["id"]
                )
            )
            == first["segments"][0]["id"]
        )


def test_correction_creates_a_new_revision_and_leaves_the_original_untouched(
    client, email_outbox, settings, monkeypatch
):
    credentials, workspace_id, meeting_id = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    session = login_user(client, credentials)
    original = _transcript(client, workspace_id, meeting_id)
    base = original["revision"]
    segment = original["segments"][0]

    corrected = _correct(
        client,
        workspace_id,
        meeting_id,
        {
            "base_revision_id": base["id"],
            "note": "اصلاح نام محصول",
            "edits": [
                {
                    "segment_id": segment["id"],
                    "text": "پیگیری   فاکتور انجام شود",
                    "speaker_key": "speaker_unknown",
                }
            ],
        },
    )
    assert corrected.status_code == 201, corrected.text
    payload = corrected.json()
    revision = payload["revision"]
    assert (revision["revision_number"], revision["source"], revision["reason"]) == (
        2,
        "correction",
        "human_correction",
    )
    assert revision["parent_revision_id"] == base["id"]
    assert revision["created_by_user_id"] == session["user"]["id"]
    assert revision["note"] == "اصلاح نام محصول"
    assert revision["asr_provider"] is None  # a person's text carries no engine claim
    assert revision["is_mock"] is True  # ... but inherits the mock label of its base
    new_segment = payload["segments"][0]
    assert new_segment["id"] != segment["id"]
    assert new_segment["text"] == "پیگیری فاکتور انجام شود"  # whitespace normalised
    assert new_segment["speaker_key"] == "speaker_unknown"
    assert (new_segment["start_ms"], new_segment["end_ms"], new_segment["sequence"]) == (
        segment["start_ms"],
        segment["end_ms"],
        segment["sequence"],
    )
    assert new_segment["confidence"] is None  # a person's text carries no engine confidence

    # current pointer moved; the base is intact and still readable by id
    assert _transcript(client, workspace_id, meeting_id)["revision"]["id"] == revision["id"]
    untouched = _transcript(client, workspace_id, meeting_id, revision_id=base["id"])
    assert untouched["segments"] == original["segments"]
    assert untouched["revision"]["is_current"] is False
    listing = _revisions(client, workspace_id, meeting_id)
    assert [i["revision_number"] for i in listing["items"]] == [2, 1]
    assert listing["current_revision_id"] == revision["id"]

    with inspection_session_factory()() as db:
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.event_type == "transcript.revision.created")
        )
        assert audit is not None and audit.target_id == revision["id"]
        assert audit.details["segments_changed"] == 1
        assert audit.details["parent_revision_id"] == base["id"]
        assert "فاکتور" not in str(audit.details) and "قرارداد" not in str(audit.details)

    # a stale base (no longer current) is refused, nothing is written
    stale = _correct(
        client,
        workspace_id,
        meeting_id,
        {
            "base_revision_id": base["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": segment["id"], "text": "متن دیگر"}],
        },
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_stale"
    foreign_segment = _correct(
        client,
        workspace_id,
        meeting_id,
        {
            "base_revision_id": revision["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": segment["id"], "text": "متن دیگر"}],  # of revision 1
        },
    )
    assert foreign_segment.status_code == 422
    assert foreign_segment.json()["error"]["code"] == "invalid_segment"
    unknown_speaker = _correct(
        client,
        workspace_id,
        meeting_id,
        {
            "base_revision_id": revision["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": new_segment["id"], "speaker_key": "speaker_9"}],
        },
    )
    assert unknown_speaker.status_code == 422
    assert unknown_speaker.json()["error"]["code"] == "invalid_speaker"
    unchanged = _correct(
        client,
        workspace_id,
        meeting_id,
        {
            "base_revision_id": revision["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": new_segment["id"], "text": new_segment["text"]}],
        },
    )
    assert unchanged.status_code == 422 and unchanged.json()["error"]["code"] == "no_changes"
    empty = _correct(
        client,
        workspace_id,
        meeting_id,
        {"base_revision_id": revision["id"], "reason": "fix the wording", "edits": []},
    )
    assert empty.status_code == 422
    assert [
        i["revision_number"] for i in _revisions(client, workspace_id, meeting_id)["items"]
    ] == [
        2,
        1,
    ]

    # no CSRF proof, no correction
    no_csrf = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers={"X-Workspace-ID": workspace_id},
        json={
            "base_revision_id": revision["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": new_segment["id"], "text": "متن دیگر"}],
        },
    )
    assert no_csrf.status_code == 403


def test_only_the_current_revision_feeds_search_action_sources_shares_and_exports(
    client, email_outbox, settings, monkeypatch
):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    original = _transcript(client, workspace_id, meeting_id)
    old_segment = original["segments"][0]
    corrected = _correct(
        client,
        workspace_id,
        meeting_id,
        {
            "base_revision_id": original["revision"]["id"],
            "reason": "fix the wording",
            "edits": [{"segment_id": old_segment["id"], "text": "پیگیری فاکتور انجام شود"}],
        },
    ).json()
    new_segment = corrected["segments"][0]

    def kinds(query: str) -> set[str]:
        response = client.get(
            "/api/v1/search", headers=_read_headers(workspace_id), params={"q": query}
        )
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        return {match["kind"] for item in items for match in item["matches"]}

    assert "transcript" in kinds("فاکتور")
    assert "transcript" not in kinds("قرارداد")  # superseded text is not the transcript

    stale_source = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={"description": "ارسال فاکتور", "source_segment_id": old_segment["id"]},
    )
    assert stale_source.status_code == 422
    assert stale_source.json()["error"]["code"] == "invalid_source_segment"
    fresh_source = client.post(
        f"/api/v1/meetings/{meeting_id}/actions",
        headers=_write_headers(client, workspace_id),
        json={"description": "ارسال فاکتور", "source_segment_id": new_segment["id"]},
    )
    assert fresh_source.status_code == 201, fresh_source.text
    assert fresh_source.json()["analysis_run_id"] is None  # a person's action has no AI run

    json_export = client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        headers=_read_headers(workspace_id),
        params={"format": "json"},
    )
    assert json_export.status_code == 200, json_export.text
    exported = json_export.json()
    assert exported["segments"][0]["text"] == "پیگیری فاکتور انجام شود"
    assert exported["transcript_revision"]["id"] == corrected["revision"]["id"]
    assert exported["transcript_revision"]["revision_number"] == 2
    assert exported["transcript_revision"]["source"] == "correction"
    text_export = client.get(
        f"/api/v1/meetings/{meeting_id}/export",
        headers=_read_headers(workspace_id),
        params={"format": "txt"},
    )
    assert "Transcript (revision 2, correction, " in text_export.text

    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_write_headers(client, workspace_id),
        json={"public_share_links_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    share = client.post(
        f"/api/v1/meetings/{meeting_id}/shares",
        headers=_write_headers(client, workspace_id),
        json={"expires_in_hours": 24, "require_authentication": False, "allow_export": True},
    )
    assert share.status_code == 201, share.text
    raw_token = urlparse(share.json()["url"]).path.rsplit("/", 1)[-1]
    client.cookies.clear()
    shared = client.get(f"/api/v1/shares/{raw_token}")
    assert shared.status_code == 200, shared.text
    assert shared.json()["transcript"]["revision"]["revision_number"] == 2
    assert shared.json()["transcript"]["segments"][0]["text"] == "پیگیری فاکتور انجام شود"
    shared_export = client.get(f"/api/v1/shares/{raw_token}/export", params={"format": "json"})
    assert shared_export.status_code == 200, shared_export.text
    assert shared_export.json()["transcript_revision"]["revision_number"] == 2


def test_committed_segments_and_provenance_refuse_orm_updates(
    client, email_outbox, settings, monkeypatch
):
    _, _workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    factory = inspection_session_factory()
    with factory() as db:
        segment = db.scalar(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
        )
        assert segment is not None
        segment.text = "rewritten"
        with pytest.raises(TranscriptImmutableError, match="immutable"):
            db.flush()
        db.rollback()
    with factory() as db:
        revision = db.scalar(
            select(TranscriptRevision).where(TranscriptRevision.meeting_id == meeting_id)
        )
        assert revision is not None
        revision.reason = "tampered"
        with pytest.raises(TranscriptImmutableError, match="current pointer"):
            db.flush()
        db.rollback()
    with factory() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.meeting_id == meeting_id))
        assert run is not None
        run.model = "other-model"
        with pytest.raises(TranscriptImmutableError):
            db.flush()
        db.rollback()
    with factory() as db:
        # the current pointer is the one mutable thing, and it round-trips
        revision = db.scalar(
            select(TranscriptRevision).where(TranscriptRevision.meeting_id == meeting_id)
        )
        assert revision is not None and revision.is_current is True
        revision.is_current = False
        db.flush()
        revision.is_current = True
        db.commit()
    with factory() as db:
        stored = db.scalar(
            select(TranscriptSegment).where(TranscriptSegment.meeting_id == meeting_id)
        )
        assert stored is not None and stored.text == "پیگیری قرارداد انجام شود"


def test_revisions_are_invisible_to_other_tenants(client, email_outbox, settings, monkeypatch):
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    revision_id = _transcript(client, workspace_id, meeting_id)["revision"]["id"]
    segment_id = _transcript(client, workspace_id, meeting_id)["segments"][0]["id"]

    outsider = register_user(client, email_outbox, email="revision-outsider@example.com")
    outsider_session = login_user(client, outsider)
    other_workspace = outsider_session["workspaces"][0]["id"]
    headers = {"X-Workspace-ID": other_workspace, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)}
    assert (
        client.get(
            f"/api/v1/meetings/{meeting_id}/transcript/revisions", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/meetings/{meeting_id}/transcript",
            headers=headers,
            params={"revision_id": revision_id},
        ).status_code
        == 404
    )
    correction = client.post(
        f"/api/v1/meetings/{meeting_id}/transcript/corrections",
        headers=headers,
        json={
            "base_revision_id": revision_id,
            "reason": "fix the wording",
            "edits": [{"segment_id": segment_id, "text": "x"}],
        },
    )
    assert correction.status_code == 404
    with inspection_session_factory()() as db:
        assert (
            db.scalar(
                select(TranscriptRevision.revision_number)
                .where(TranscriptRevision.meeting_id == meeting_id)
                .order_by(TranscriptRevision.revision_number.desc())
            )
            == 1
        )


@pytest.mark.postgres
def test_share_context_covers_revisions_and_analysis_runs(
    client, email_outbox, settings, monkeypatch, probe_role
):
    """The database ceiling of a share token includes the new content tables, read-only."""
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    _, workspace_id, meeting_id = _processed_meeting(client, email_outbox, settings, monkeypatch)
    _, other_workspace, other_meeting = _processed_meeting(
        client, email_outbox, settings, monkeypatch
    )
    assert other_meeting != meeting_id
    # the client is now logged in as the owner of ``other_workspace``
    enabled = client.patch(
        "/api/v1/admin/workspace-settings",
        headers=_write_headers(client, other_workspace),
        json={"public_share_links_enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    created = client.post(
        f"/api/v1/meetings/{other_meeting}/shares",
        headers=_write_headers(client, other_workspace),
        json={"expires_in_hours": 24, "require_authentication": False},
    )
    assert created.status_code == 201, created.text
    raw_token = urlparse(created.json()["url"]).path.rsplit("/", 1)[-1]
    digest = token_digest(f"share:{raw_token}", client.app.state.settings)

    engine = create_engine(probe_role, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            _rls._bind(connection, share_token_hash=digest)
            ids = lambda sql: connection.execute(text(sql)).scalars().all()  # noqa: E731
            assert ids("SELECT meeting_id FROM transcript_revisions") == [other_meeting]
            assert ids("SELECT meeting_id FROM analysis_runs") == [other_meeting]
            assert ids("SELECT meeting_id FROM transcript_segments") == [other_meeting]
            assert ids("SELECT DISTINCT meeting_id FROM evidence_spans") == [other_meeting]
            # readable through the share, never writable: the WITH CHECK half of
            # the policy refuses the new row version
            with pytest.raises(ProgrammingError, match="row-level security"):
                connection.execute(
                    text(
                        "UPDATE transcript_revisions SET note = 'via share' WHERE meeting_id = :m"
                    ),
                    {"m": other_meeting},
                )
            connection.rollback()
        with engine.connect() as connection:
            _rls._bind(connection, workspace_id=workspace_id)
            assert ids("SELECT meeting_id FROM transcript_revisions") == [meeting_id]
            assert ids("SELECT meeting_id FROM analysis_runs") == [meeting_id]
            assert ids("SELECT DISTINCT meeting_id FROM evidence_spans") == [meeting_id]
            connection.rollback()
    finally:
        engine.dispose()
