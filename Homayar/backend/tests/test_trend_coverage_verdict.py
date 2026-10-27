"""HOM-312, U2: the cached ``unsupported_findings`` verdict of each meeting (ADR 0043).

The coverage counters with the app's cache (``/admin/coverage`` and the trend routes) must
always equal the counters without it.  Every way the verdict's inputs can change through the
application changes the entry's key, so the next request judges the meeting again: a key
point's evidence, a new human span, a deleted span, a re-analysis (a new run, with a new
summary and new AI action items), a summary edit, an AI action item change.  Each test reads
coverage through the route before and after, with a warm cache, and checks the new value
against a request with an empty cache.  Plus: another workspace never shares an entry; the
time limit (``trend_coverage_verdict_ttl_seconds``) rebuilds an old entry, and 0 reuses none;
a verdict judged from rows that changed after the lookup is not kept; and the parity of the
counters with and without the cache on a seeded generated set.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select, update

from backend.database.saas_models import (
    ActionItem,
    AnalysisRun,
    EvidenceSpan,
    MeetingSummary,
    StageState,
    TranscriptRevision,
)
from backend.services import coverage_service as coverage
from backend.services.coverage_service import CoverageWindow
from backend.services.trend_judgment_cache import JudgmentCache
from backend.tests._coverage_generated import seed_generated
from backend.tests._database import inspection_session_factory
from backend.tests.test_coverage import BASE, WINDOW, Tenant, seed_call

COVERAGE = "/api/v1/admin/coverage"
VERDICT_KEYS = {"verdict_hits", "verdict_misses", "verdict_builds", "verdict_expired"}


def _counters(tenant: Tenant) -> dict[str, Any]:
    response = tenant.get(COVERAGE, **WINDOW)
    assert response.status_code == 200, response.text
    return response.json()["counters"]


def _unsupported(tenant: Tenant) -> int:
    return _counters(tenant)["evaluation"]["buckets"]["unsupported_findings"]


def _truth(client: Any, tenant: Tenant) -> dict[str, Any]:
    """The counters with an empty cache."""
    client.app.state.trend_judgment_cache.clear()
    return _counters(tenant)


def _supported_call(tenant: Tenant, **changes: Any) -> str:
    shape: dict[str, Any] = {
        "schema_version": 2,
        "key_points": [{"evidence_status": "sufficient", "evidence_ids": ["@insight"]}],
        "actions": [{"status": "sufficient", "cited": True}],
        **changes,
    }
    meeting_id = seed_call(tenant, **shape)
    assert meeting_id is not None
    return meeting_id


def _warm(client: Any, tenant: Tenant) -> dict[str, Any]:
    """Two reads: the second is served from the cache (checked)."""
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    cache.clear()
    first = _counters(tenant)
    before = cache.stats()
    assert _counters(tenant) == first
    after = cache.stats()
    assert after["verdict_hits"] > before["verdict_hits"]
    assert after["verdict_builds"] == before["verdict_builds"]
    return first


def test_the_stats_have_the_verdict_counts(client):
    assert VERDICT_KEYS <= set(client.app.state.trend_judgment_cache.stats())


def test_a_key_point_evidence_edit_is_judged_again(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-keypoint@example.com")
    meeting_id = _supported_call(tenant)
    assert _warm(client, tenant)["evaluation"]["buckets"]["unsupported_findings"] == 0
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        points = [dict(point) for point in summary.key_points]
        points[0]["evidence_status"] = "insufficient"
        summary.key_points = points  # an ORM update: updated_at changes
        db.commit()
    assert _unsupported(tenant) == 1
    assert _counters(tenant) == _truth(client, tenant)


def test_a_new_human_span_is_judged_again(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-human@example.com")
    meeting_id = _supported_call(
        tenant, key_points=[{"evidence_status": "sufficient", "evidence_ids": ["cited-later"]}]
    )
    assert _warm(client, tenant)["evaluation"]["buckets"]["unsupported_findings"] == 1
    with inspection_session_factory()() as db:
        template = db.scalar(select(EvidenceSpan).where(EvidenceSpan.meeting_id == meeting_id))
        db.add(
            EvidenceSpan(
                id="cited-later",
                workspace_id=template.workspace_id,
                meeting_id=meeting_id,
                analysis_run_id=None,
                transcript_revision_id=template.transcript_revision_id,
                segment_id=template.segment_id,
                speaker_key="speaker_1",
                finding_type="insight",
                finding_index=0,
                text_start=0,
                text_end=4,
                start_ms=0,
                end_ms=900,
                evidence_type="human",
            )
        )
        db.commit()
    assert _unsupported(tenant) == 0
    assert _counters(tenant) == _truth(client, tenant)


def test_a_deleted_span_is_judged_again(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-deleted@example.com")
    meeting_id = _supported_call(tenant)
    assert _warm(client, tenant)["evaluation"]["buckets"]["unsupported_findings"] == 0
    with inspection_session_factory()() as db:
        db.execute(
            delete(EvidenceSpan).where(
                EvidenceSpan.meeting_id == meeting_id, EvidenceSpan.finding_type == "insight"
            )
        )
        db.commit()
    assert _unsupported(tenant) == 1
    assert _counters(tenant) == _truth(client, tenant)


def test_a_reanalysis_is_judged_again(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-run@example.com")
    meeting_id = _supported_call(tenant)
    assert _warm(client, tenant)["evaluation"]["buckets"]["unsupported_findings"] == 0
    # what persist_analysis does: a new current run, the summary and AI items replaced
    with inspection_session_factory()() as db:
        revision_id = db.scalar(
            select(TranscriptRevision.id).where(
                TranscriptRevision.meeting_id == meeting_id, TranscriptRevision.is_current.is_(True)
            )
        )
        db.execute(
            update(AnalysisRun)
            .where(AnalysisRun.meeting_id == meeting_id, AnalysisRun.is_current.is_(True))
            .values(is_current=False)
        )
        run = AnalysisRun(
            workspace_id=tenant.workspace_id,
            meeting_id=meeting_id,
            transcript_revision_id=revision_id,
            state=StageState.COMPLETED,
            is_current=True,
        )
        db.add(run)
        db.execute(
            delete(ActionItem).where(
                ActionItem.meeting_id == meeting_id, ActionItem.created_by == "ai"
            )
        )
        db.execute(delete(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        db.flush()
        db.add(
            MeetingSummary(
                workspace_id=tenant.workspace_id,
                meeting_id=meeting_id,
                overview="Again.",
                provider="test",
                model="test",
                schema_version=2,
                analysis_run_id=run.id,
                evidence_status="insufficient",
                key_points=[],
                decisions=[],
                topics=[],
            )
        )
        db.commit()
    assert _unsupported(tenant) == 1
    assert _counters(tenant) == _truth(client, tenant)


def test_a_summary_edit_is_judged_again(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-summary@example.com")
    meeting_id = _supported_call(tenant)
    assert _warm(client, tenant)["evaluation"]["buckets"]["unsupported_findings"] == 0
    with inspection_session_factory()() as db:
        summary = db.scalar(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id))
        summary.evidence_status = "insufficient"
        db.commit()
    assert _unsupported(tenant) == 1
    assert _counters(tenant) == _truth(client, tenant)


def test_an_ai_action_change_is_judged_again(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-action@example.com")
    meeting_id = _supported_call(tenant)
    other_id = _supported_call(tenant)
    assert _warm(client, tenant)["evaluation"]["buckets"]["unsupported_findings"] == 0
    with inspection_session_factory()() as db:
        action = db.scalar(select(ActionItem).where(ActionItem.meeting_id == meeting_id))
        action.evidence_status = "insufficient"  # an edit
        db.add(  # and a new AI item with no evidence in the other call
            ActionItem(
                workspace_id=tenant.workspace_id,
                meeting_id=other_id,
                description="New.",
                created_by="ai",
                evidence_status="sufficient",
            )
        )
        db.commit()
    assert _unsupported(tenant) == 2
    assert _counters(tenant) == _truth(client, tenant)
    with inspection_session_factory()() as db:
        for item in db.scalars(select(ActionItem).where(ActionItem.meeting_id == other_id)):
            if item.description == "New.":
                db.delete(item)  # a deletion
        db.commit()
    assert _unsupported(tenant) == 1
    assert _counters(tenant) == _truth(client, tenant)


def test_another_workspace_never_shares_an_entry(client, email_outbox):
    first = Tenant(client, email_outbox, "verdict-first@example.com")
    second = Tenant(client, email_outbox, "verdict-second@example.com")
    _supported_call(first)
    _supported_call(second, actions=[{"status": "insufficient"}])
    cache: JudgmentCache = client.app.state.trend_judgment_cache
    cache.clear()
    assert _unsupported(first) == 0
    builds = cache.stats()["verdict_builds"]
    assert _unsupported(second) == 1
    assert cache.stats()["verdict_builds"] > builds  # judged for its own workspace
    keys = {key for _kind, key in cache._maps}
    assert {getattr(key, "workspace_id", None) for key in keys} >= {
        first.workspace_id,
        second.workspace_id,
    }


def test_the_time_limit_rebuilds_old_entries_and_zero_keeps_none(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-ttl@example.com")
    _supported_call(tenant)
    now = [1000.0]
    cache = JudgmentCache(max_entries=1000, verdict_ttl_seconds=600, clock=lambda: now[0])
    client.app.state.trend_judgment_cache = cache
    truth = _counters(tenant)
    assert _counters(tenant) == truth and cache.stats()["verdict_hits"] == 1
    now[0] += 601  # older than the limit
    assert _counters(tenant) == truth
    stats = cache.stats()
    assert stats["verdict_expired"] == 1 and stats["verdict_builds"] == 2
    never = JudgmentCache(max_entries=1000, verdict_ttl_seconds=0)
    client.app.state.trend_judgment_cache = never
    for _ in range(3):
        assert _counters(tenant) == truth
    assert never.stats()["verdict_hits"] == 0 and never.stats()["verdict_builds"] == 3


def test_verdicts_count_against_the_cap(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-cap@example.com")
    _supported_call(tenant)
    _supported_call(tenant, actions=[{"status": "insufficient"}])
    truth = _truth(client, tenant)
    tiny = JudgmentCache(max_entries=1)
    client.app.state.trend_judgment_cache = tiny
    for _ in range(3):
        assert _counters(tenant) == truth
    stats = tiny.stats()
    assert stats["entries"] == 1 and stats["evictions"] > 0


class _WriteAfterLookup(JudgmentCache):
    """Runs ``write`` (once) right after the verdict lookup, before the rows are read."""

    def __init__(self, write: Any) -> None:
        super().__init__(max_entries=1000)
        self.write = write

    def verdicts(self, key: Any, fingerprints: Any) -> dict[str, int]:
        found = super().verdicts(key, fingerprints)
        if self.write is not None:
            write, self.write = self.write, None
            write()
        return found


def test_a_write_between_the_lookup_and_the_rows_is_not_kept(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-race@example.com")
    meeting_id = _supported_call(tenant)
    kwargs: dict[str, Any] = {
        "workspace_id": tenant.workspace_id,
        "window": CoverageWindow(start=BASE, end=BASE + timedelta(days=1)),
        "recompute_max_attempts": 3,
    }

    def add_action() -> None:  # an AI item with no evidence: unsupported
        with inspection_session_factory()() as db:
            db.add(
                ActionItem(
                    workspace_id=tenant.workspace_id,
                    meeting_id=meeting_id,
                    description="Racing.",
                    created_by="ai",
                    evidence_status="sufficient",
                )
            )
            db.commit()

    def unsupported(cache: JudgmentCache) -> int:
        with inspection_session_factory()() as db:
            counters = coverage.compute_coverage(
                db, cache=cache, organization_id=tenant.organization_id, **kwargs
            ).counters
        return counters["evaluation"]["buckets"]["unsupported_findings"]

    cache = _WriteAfterLookup(add_action)
    assert unsupported(cache) == 1  # judged from the rows read after the write
    stats = cache.stats()
    assert stats["verdict_builds"] == 1 and stats["entries"] == 0  # but not kept
    with inspection_session_factory()() as db:  # the fingerprint is the looked-up one again
        db.execute(delete(ActionItem).where(ActionItem.description == "Racing."))
        db.commit()
    assert unsupported(cache) == 0
    assert cache.stats()["verdict_hits"] == 0


def test_the_counters_with_the_cache_equal_the_counters_without_it(client, email_outbox):
    tenant = Tenant(client, email_outbox, "verdict-parity@example.com")
    seed_generated(tenant, seed=313, count=120)
    window = CoverageWindow(start=BASE, end=BASE + timedelta(days=1))
    cache = JudgmentCache(max_entries=100_000)
    with inspection_session_factory()() as db:
        for population in (
            [],
            [coverage.IngestionRecord.status == coverage.IngestionStatus.ACCEPTED],
        ):
            kwargs: dict[str, Any] = {
                "workspace_id": tenant.workspace_id,
                "window": window,
                "recompute_max_attempts": 3,
                "population": population,
            }
            expected = coverage.reference_coverage(db, **kwargs).counters
            for _ in range(3):  # cold, then warm twice
                got = coverage.compute_coverage(
                    db, cache=cache, organization_id=tenant.organization_id, **kwargs
                ).counters
                assert got == expected
            whole, split = coverage.compute_coverage_by_team(
                db, cache=cache, organization_id=tenant.organization_id, **kwargs
            )
            whole_ref, split_ref = coverage.reference_coverage_by_team(db, **kwargs)
            assert whole.counters == whole_ref.counters
            assert {k: v.counters for k, v in split.items()} == {
                k: v.counters for k, v in split_ref.items()
            }
    assert cache.stats()["verdict_hits"] > 0
