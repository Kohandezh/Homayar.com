"""WP06-T03: durable stage checkpoints and attempt-scoped artifacts.

Reuse, invalidation, fencing and cleanup of committed stage results.  The
identity, artifact and cleanup rules run on both databases; transaction,
fencing, uniqueness and row-level-security assertions are PostgreSQL
evidence (``postgres`` marker).  Providers are test doubles: they prove the
orchestration, not real ASR/diarization/S3 behaviour.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import PurePosixPath

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError

from backend.database.saas_models import (
    ActionItem,
    AuditEvent,
    CheckpointState,
    JobState,
    MeetingStatus,
    ProcessingJob,
    ProcessingStageRun,
    ProviderUsage,
    StageCheckpoint,
    TranscriptSegment,
    UsageEvent,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs import checkpoints as ckpt
from backend.jobs import invariants, outbox, processor, recovery
from backend.jobs.leases import StaleLeaseError
from backend.jobs.processor import process_job
from backend.jobs.states import StageName
from backend.providers.base import (
    ASRProvider,
    IntelligenceProvider,
    ProviderError,
    ProviderIdentity,
)
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.schemas import (
    ASRRequest,
    ASRResult,
    DiarizationResult,
    IntelligenceRequest,
)
from backend.security.context import CSRF_COOKIE
from backend.storage import (
    LocalObjectStorage,
    ObjectConflictError,
    StorageError,
    create_object_storage,
)
from backend.tests._database import (
    configured_database_url,
    inspection_session_factory,
    is_postgres_url,
)
from backend.tests.test_dispatch_outbox import FakeBroker
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_leases import (
    GatedASRProvider,
    _expire_lease,
    _job,
    _settlements,
    _wait_until,
)
from backend.tests.test_job_processor import (
    SingleSpeakerDiarizationProvider,
    _queued_upload,
)
from backend.tests.test_usage_ledger import _events

TRANSCRIPT = "پیگیری قرارداد انجام شود"
DEADLINE_SECONDS = 15.0


@pytest.fixture
def broker(monkeypatch) -> FakeBroker:
    fake = FakeBroker()
    monkeypatch.setattr(outbox, "publish_processing_job", fake)
    return fake


# --- doubles ------------------------------------------------------------------


class CountingASR(ASRProvider):
    """Delegates to the deterministic mock and counts real calls; identity is
    the delegate's unless overridden (a different *declared* model)."""

    def __init__(self, transcript: str = TRANSCRIPT, *, model: str | None = None) -> None:
        self._inner = MockASRProvider(allow_mock=True, transcript=transcript)
        self._model = model
        self.calls = 0

    def identity(self) -> ProviderIdentity:
        inner = self._inner.identity()
        if self._model is None:
            return inner
        return ProviderIdentity(provider=inner.provider, model=self._model, params=inner.params)

    async def transcribe(self, request: ASRRequest) -> ASRResult:
        self.calls += 1
        return await self._inner.transcribe(request)


class CountingIntelligence(IntelligenceProvider):
    """Delegates to the mock; fails the first ``fail`` calls transiently."""

    def __init__(self, *, fail: int = 0, prompt_version: int | None = None) -> None:
        self._inner = MockIntelligenceProvider(allow_mock=True)
        self.fail = fail
        self._prompt_version = prompt_version
        self.calls = 0

    def identity(self) -> ProviderIdentity:
        inner = self._inner.identity()
        if self._prompt_version is None:
            return inner
        return ProviderIdentity(
            provider=inner.provider,
            model=inner.model,
            params={**inner.params, "prompt_version": self._prompt_version},
        )

    async def analyze(self, request: IntelligenceRequest):
        self.calls += 1
        if self.fail > 0:
            self.fail -= 1
            raise ProviderError(
                "llm_unavailable", "analysis temporarily unavailable", retryable=True
            )
        return await self._inner.analyze(request)


class Bundle:
    """ProviderBundle-shaped holder so the doubles stay reachable for assertions."""

    def __init__(self, asr: CountingASR, intelligence: CountingIntelligence) -> None:
        self.asr = asr
        self.diarization = SingleSpeakerDiarizationProvider()
        self.intelligence = intelligence


class FlakyWriteStorage:
    """Storage whose next ``fail`` artifact writes fail with a transient error."""

    def __init__(self, inner, *, fail: int) -> None:
        self._inner = inner
        self.fail = fail
        self.writes = 0

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def put_stream(self, tenant_id, stream, **kwargs):
        self.writes += 1
        if self.fail > 0:
            self.fail -= 1
            raise StorageError("simulated write outage", code="storage_unavailable")
        return self._inner.put_stream(tenant_id, stream, **kwargs)


# --- helpers ------------------------------------------------------------------


def _bundle(*, fail_intelligence: int = 0, asr_model: str | None = None,
            prompt_version: int | None = None) -> Bundle:
    return Bundle(
        CountingASR(model=asr_model),
        CountingIntelligence(fail=fail_intelligence, prompt_version=prompt_version),
    )


def _checkpoints(meeting_id: str) -> list[StageCheckpoint]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(StageCheckpoint)
            .where(StageCheckpoint.meeting_id == meeting_id)
            .order_by(StageCheckpoint.created_at.asc(), StageCheckpoint.id.asc())
        ).all()
        for row in rows:
            db.expunge(row)
        return rows


def _published(meeting_id: str) -> dict[str, StageCheckpoint]:
    return {
        row.stage: row
        for row in _checkpoints(meeting_id)
        if row.state == CheckpointState.PUBLISHED
    }


def _stage_runs(job_id: str) -> list[tuple[int, str, str, str | None, bool, str | None]]:
    with inspection_session_factory()() as db:
        rows = db.scalars(
            select(ProcessingStageRun)
            .where(ProcessingStageRun.job_id == job_id)
            .order_by(ProcessingStageRun.attempt.asc(), ProcessingStageRun.sequence.asc())
        ).all()
        return [
            (r.attempt, r.stage, r.state.value, r.reason_code, r.reused, r.checkpoint_id)
            for r in rows
        ]


def _usage_rows(job_id: str) -> list[str]:
    with inspection_session_factory()() as db:
        return sorted(
            db.scalars(select(ProviderUsage.stage).where(ProviderUsage.job_id == job_id)).all()
        )


def _ledger(workspace_id: str, reservation_id: str) -> list[str]:
    return [
        event.event_type
        for event in _events(workspace_id)
        if event.reservation_id == reservation_id
    ]


def _count(model) -> int:
    with inspection_session_factory()() as db:
        return db.scalar(select(func.count(model.id)))


def _clean(workspace_id: str, settings, storage) -> None:
    with inspection_session_factory()() as db:
        violations = invariants.check_workspace(
            db, workspace_id, settings=settings, storage=storage
        )
        assert violations == []


def _object_path(storage: LocalObjectStorage, key: str):
    return storage.root.joinpath(*PurePosixPath(key).parts)


def _completed_audit(meeting_id: str) -> AuditEvent:
    with inspection_session_factory()() as db:
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "meeting.processing.completed",
                AuditEvent.target_id == meeting_id,
            )
        )
        assert event is not None
        db.expunge(event)
        return event


def _fail_then_retry(client, email_outbox, monkeypatch, settings, storage, providers):
    """Attempt 1 fails in the analysis stage (ASR and diarization committed)."""
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    first = process_job(
        job_id, worker_id="worker-1", settings=settings, providers=providers, storage=storage
    )
    assert first["status"] == "retry_scheduled", first
    _make_due(job_id)
    return job_id, workspace_id


# --- identity -----------------------------------------------------------------


def test_identity_is_deterministic_and_excludes_execution_metadata():
    lease_a = processor.Lease("job", "ws", "worker-a", 1, "attempt-a", 1, datetime.now(UTC))
    lease_b = processor.Lease("job", "ws", "worker-b", 7, "attempt-b", 3, datetime.now(UTC))
    make = lambda lease: ckpt.StageContext(  # noqa: E731
        lease=lease,
        meeting_id="meeting",
        recording_sha256="a" * 64,
        recording_size_bytes=16,
        recording_duration_seconds=12,
        language="fa",
    )
    provider = ProviderIdentity(provider="p", model="m", params={"z": 1, "a": "x"})
    same = [
        ckpt.stage_identity(make(lease), StageName.ASR, provider=provider,
                            request={"language": "fa", "beam_size": 5, "vad_filter": True})
        for lease in (lease_a, lease_b)
    ]
    assert same[0].input_hash == same[1].input_hash
    assert same[0].manifest == same[1].manifest
    blob = json.dumps(same[0].manifest)
    for forbidden in ("attempt", "worker", "generation", "expires"):
        assert forbidden not in blob
    assert same[0].input_hash == hashlib.sha256(ckpt.canonical_json(same[0].manifest)).hexdigest()

    other_model = ckpt.stage_identity(
        make(lease_a), StageName.ASR,
        provider=ProviderIdentity(provider="p", model="m2", params={"z": 1, "a": "x"}),
        request={"language": "fa", "beam_size": 5, "vad_filter": True},
    )
    other_audio = ckpt.stage_identity(
        ckpt.StageContext(lease_a, "meeting", "b" * 64, 16, 12, "fa"), StageName.ASR,
        provider=provider, request={"language": "fa", "beam_size": 5, "vad_filter": True},
    )
    other_request = ckpt.stage_identity(
        make(lease_a), StageName.ASR, provider=provider,
        request={"language": "en", "beam_size": 5, "vad_filter": True},
    )
    assert len({same[0].input_hash, other_model.input_hash, other_audio.input_hash,
                other_request.input_hash}) == 4


# --- reuse and invalidation ---------------------------------------------------


def test_retry_reuses_committed_stages_and_recomputes_only_the_failed_one(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    providers = _bundle(fail_intelligence=1)
    job_id, workspace_id = _fail_then_retry(
        client, email_outbox, monkeypatch, settings, storage, providers
    )
    meeting_id = _job(job_id).meeting_id
    first_published = _published(meeting_id)
    assert set(first_published) == {"asr", "diarization"}
    assert all(row.attempt == 1 for row in first_published.values())
    assert _usage_rows(job_id) == ["asr", "diarization"]  # the calls that were made

    second = process_job(
        job_id, worker_id="worker-2", settings=settings, providers=providers, storage=storage
    )
    assert second == {"job_id": job_id, "status": "completed"}
    assert providers.asr.calls == 1 and providers.intelligence.calls == 2

    runs = [run for run in _stage_runs(job_id) if run[0] == 2]
    assert [(r[1], r[2], r[3], r[4]) for r in runs] == [
        ("materialize", "completed", None, False),
        ("asr", "completed", "checkpoint_reused", True),
        ("diarization", "completed", "checkpoint_reused", True),
        ("intelligence", "completed", None, False),
    ]
    by_stage = {r[1]: r[5] for r in runs}
    assert by_stage["asr"] == first_published["asr"].id
    assert by_stage["diarization"] == first_published["diarization"].id
    published = _published(meeting_id)
    assert set(published) == {"asr", "diarization", "intelligence"}
    assert published["intelligence"].attempt == 2
    assert published["intelligence"].input_manifest["upstream"] == {
        "asr_artifact_sha256": first_published["asr"].artifact_sha256,
        "diarization_artifact_sha256": first_published["diarization"].artifact_sha256,
        "turns_version": ckpt.TURNS_VERSION,
    }
    assert _completed_audit(meeting_id).details["reused_stages"] == "asr,diarization"
    # effects: one call per stage in provider usage, one settlement, one transcript
    assert _usage_rows(job_id) == ["asr", "diarization", "intelligence"]
    job = _job(job_id)
    assert _ledger(workspace_id, job.reservation_id) == ["reserved", "committed"]
    assert _ledger(workspace_id, job.concurrency_reservation_id) == ["reserved", "released"]
    assert _settlements() == 1 and _count(TranscriptSegment) == 1
    _clean(workspace_id, settings, storage)


def test_changed_asr_identity_recomputes_asr_and_analysis_but_reuses_diarization(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    first = _bundle(fail_intelligence=1)
    job_id, workspace_id = _fail_then_retry(
        client, email_outbox, monkeypatch, settings, storage, first
    )
    meeting_id = _job(job_id).meeting_id
    before = _published(meeting_id)

    second = _bundle(asr_model="whisper-large-v3")  # same output, different declared model
    result = process_job(
        job_id, worker_id="worker-2", settings=settings, providers=second, storage=storage
    )
    assert result["status"] == "completed"
    assert second.asr.calls == 1  # recomputed under the new identity
    runs = {r[1]: (r[3], r[4], r[5]) for r in _stage_runs(job_id) if r[0] == 2}
    assert runs["asr"] == (None, False, runs["asr"][2]) and runs["asr"][2] != before["asr"].id
    assert runs["diarization"] == ("checkpoint_reused", True, before["diarization"].id)
    assert runs["intelligence"][1] is False
    # the earlier ASR checkpoint stays published for *its* identity
    rows = {(r.stage, r.input_hash): r.state for r in _checkpoints(meeting_id)}
    assert rows[("asr", before["asr"].input_hash)] == CheckpointState.PUBLISHED
    assert len([k for k in rows if k[0] == "asr"]) == 2
    _clean(workspace_id, settings, storage)


def test_changed_analysis_contract_recomputes_only_the_analysis(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    providers = _bundle()
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    # every stage commits, then the final persistence fails once (crash after
    # the checkpoints were committed, before the results were published)
    original = processor.commit_usage
    calls = {"n": 0}

    def flaky_commit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated crash before the results transaction committed")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "commit_usage", flaky_commit)
    first = process_job(
        job_id, worker_id="worker-1", settings=settings, providers=providers, storage=storage
    )
    assert first["status"] == "retry_scheduled"
    meeting_id = _job(job_id).meeting_id
    before = _published(meeting_id)
    assert set(before) == {"asr", "diarization", "intelligence"}
    assert _count(TranscriptSegment) == 0 and _settlements() == 0

    _make_due(job_id)
    changed = _bundle(prompt_version=2)
    second = process_job(
        job_id, worker_id="worker-2", settings=settings, providers=changed, storage=storage
    )
    assert second["status"] == "completed"
    assert changed.asr.calls == 0 and changed.intelligence.calls == 1
    runs = {r[1]: (r[4], r[5]) for r in _stage_runs(job_id) if r[0] == 2}
    assert runs["asr"] == (True, before["asr"].id)
    assert runs["diarization"] == (True, before["diarization"].id)
    assert runs["intelligence"][0] is False and runs["intelligence"][1] != before["intelligence"].id
    assert _usage_rows(job_id) == ["asr", "diarization", "intelligence", "intelligence"]
    assert _settlements() == 1 and _count(TranscriptSegment) == 1
    _clean(workspace_id, settings, storage)


def test_crash_after_checkpoint_commit_resumes_without_duplicate_effects(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    providers = _bundle()
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    original = processor.commit_usage
    state = {"n": 0}

    def flaky_commit(*args, **kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("simulated crash before the results transaction committed")
        return original(*args, **kwargs)

    monkeypatch.setattr(processor, "commit_usage", flaky_commit)
    assert process_job(job_id, worker_id="w1", settings=settings, providers=providers,
                       storage=storage)["status"] == "retry_scheduled"
    _make_due(job_id)
    assert process_job(job_id, worker_id="w2", settings=settings, providers=providers,
                       storage=storage) == {"job_id": job_id, "status": "completed"}
    assert providers.asr.calls == 1 and providers.intelligence.calls == 1
    meeting_id = _job(job_id).meeting_id
    assert all(r[4] for r in _stage_runs(job_id) if r[0] == 2 and r[1] != "materialize")
    assert _completed_audit(meeting_id).details["reused_stages"] == (
        "asr,diarization,intelligence"
    )
    assert _usage_rows(job_id) == ["asr", "diarization", "intelligence"]
    assert _settlements() == 1 and _count(TranscriptSegment) == 1 and _count(ActionItem) == 0
    assert _job(job_id).attempt_count == 2
    _clean(workspace_id, settings, storage)


def test_missing_or_corrupt_artifacts_never_produce_a_false_hit(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    assert isinstance(storage, LocalObjectStorage)
    providers = _bundle(fail_intelligence=1)
    job_id, workspace_id = _fail_then_retry(
        client, email_outbox, monkeypatch, settings, storage, providers
    )
    meeting_id = _job(job_id).meeting_id
    before = _published(meeting_id)
    # the ASR object disappears; the diarization object is silently altered
    assert storage.delete(workspace_id, before["asr"].artifact_key)
    corrupted = _object_path(storage, before["diarization"].artifact_key)
    corrupted.write_bytes(corrupted.read_bytes().replace(b"speaker_1", b"speaker_9"))

    second = process_job(
        job_id, worker_id="worker-2", settings=settings, providers=providers, storage=storage
    )
    assert second["status"] == "completed"
    assert providers.asr.calls == 2  # recomputed, not trusted
    states = {row.id: (row.state, row.invalidation_reason) for row in _checkpoints(meeting_id)}
    assert states[before["asr"].id] == (CheckpointState.INVALIDATED, "artifact_missing")
    assert states[before["diarization"].id] == (CheckpointState.INVALIDATED, "artifact_corrupt")
    published = _published(meeting_id)
    assert {row.attempt for row in published.values()} == {2}
    runs = {r[1]: r[4] for r in _stage_runs(job_id) if r[0] == 2}
    assert runs == {"materialize": False, "asr": False, "diarization": False,
                    "intelligence": False}
    assert _settlements() == 1
    _clean(workspace_id, settings, storage)


def test_artifact_bound_and_schema_are_enforced_on_load(settings):
    storage = create_object_storage(settings)
    good = ASRResult(provider="p", model="m", language="fa", text="hi")
    data = ckpt.serialize_result(good)
    stored = storage.put_stream("ws-a", BytesIO(data), content_type="application/json",
                                object_token="art-load-test-000000000000-asr")  # noqa: S106
    loaded = ckpt.load_artifact(storage, workspace_id="ws-a", key=stored.key,
                                expected_sha256=stored.sha256, expected_size=stored.size_bytes,
                                schema=ASRResult, max_bytes=len(data))
    assert loaded == good
    with pytest.raises(ckpt.ArtifactInvalid) as too_big:
        ckpt.load_artifact(storage, workspace_id="ws-a", key=stored.key,
                           expected_sha256=stored.sha256, expected_size=stored.size_bytes,
                           schema=ASRResult, max_bytes=len(data) - 1)
    assert too_big.value.reason == "artifact_corrupt"
    with pytest.raises(ckpt.ArtifactInvalid) as wrong_schema:
        ckpt.load_artifact(storage, workspace_id="ws-a", key=stored.key,
                           expected_sha256=stored.sha256, expected_size=stored.size_bytes,
                           schema=DiarizationResult, max_bytes=len(data))
    assert wrong_schema.value.reason == "artifact_corrupt"
    # another tenant cannot even address the key
    with pytest.raises(ckpt.ArtifactInvalid) as foreign:
        ckpt.load_artifact(storage, workspace_id="ws-b", key=stored.key,
                           expected_sha256=stored.sha256, expected_size=stored.size_bytes,
                           schema=ASRResult, max_bytes=len(data))
    assert foreign.value.reason == "artifact_missing"
    # non-JSON / non-object payloads are rejected, never executed
    with pytest.raises(ckpt.ArtifactInvalid):
        ckpt.parse_artifact(b"[1,2,3]", ASRResult)
    with pytest.raises(ckpt.ArtifactInvalid):
        ckpt.parse_artifact(b"\x80\x81", ASRResult)


def test_reuse_disabled_recomputes_and_supersedes_the_old_checkpoint(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    providers = _bundle(fail_intelligence=1)
    job_id, workspace_id = _fail_then_retry(
        client, email_outbox, monkeypatch, settings, storage, providers
    )
    meeting_id = _job(job_id).meeting_id
    before = _published(meeting_id)
    disabled = settings.model_copy(update={"job_checkpoint_reuse_enabled": False})
    assert process_job(job_id, worker_id="w2", settings=disabled, providers=providers,
                       storage=storage)["status"] == "completed"
    assert providers.asr.calls == 2
    states = {row.id: (row.state, row.invalidation_reason) for row in _checkpoints(meeting_id)}
    assert states[before["asr"].id] == (CheckpointState.INVALIDATED, "superseded")
    assert len(_published(meeting_id)) == 3
    _clean(workspace_id, settings, storage)


# --- writes, crashes, cleanup -------------------------------------------------


def test_incomplete_artifact_write_does_not_mark_the_stage_successful(
    client, email_outbox, monkeypatch, settings, broker
):
    flaky = FlakyWriteStorage(create_object_storage(settings), fail=1)
    providers = _bundle()
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    first = process_job(job_id, worker_id="w1", settings=settings, providers=providers,
                        storage=flaky)
    assert first == {"job_id": job_id, "status": "retry_scheduled", "code": "artifact_write_failed"}
    meeting_id = _job(job_id).meeting_id
    rows = _checkpoints(meeting_id)
    assert [(r.stage, r.state, r.invalidation_reason) for r in rows] == [
        ("asr", CheckpointState.ABANDONED, "write_failed")
    ]
    assert not flaky.exists(workspace_id, rows[0].artifact_key)
    runs = {r[1]: r[2] for r in _stage_runs(job_id) if r[0] == 1}
    assert runs["asr"] == "failed"
    assert _settlements() == 0

    _make_due(job_id)
    assert process_job(job_id, worker_id="w2", settings=settings, providers=providers,
                       storage=flaky)["status"] == "completed"
    assert providers.asr.calls == 2 and _settlements() == 1
    _clean(workspace_id, settings, flaky)


def test_crash_after_object_write_before_publication_leaves_no_authority_and_is_cleaned(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    providers = _bundle()
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    original = ckpt.publish_checkpoint
    crashes = {"n": 0}

    def crash_once(*args, **kwargs):
        if crashes["n"] == 0:
            crashes["n"] += 1
            raise RuntimeError("simulated worker death after the object write")
        return original(*args, **kwargs)

    monkeypatch.setattr(ckpt, "publish_checkpoint", crash_once)
    first = process_job(job_id, worker_id="w1", settings=settings, providers=providers,
                        storage=storage)
    assert first["status"] == "retry_scheduled"
    meeting_id = _job(job_id).meeting_id
    orphan = _checkpoints(meeting_id)[0]
    assert orphan.state == CheckpointState.PENDING and orphan.published_at is None
    assert storage.exists(workspace_id, orphan.artifact_key)  # object exists, no authority
    assert _published(meeting_id) == {}

    _make_due(job_id)
    assert process_job(job_id, worker_id="w2", settings=settings, providers=providers,
                       storage=storage)["status"] == "completed"
    assert providers.asr.calls == 2  # a PENDING row is never reused
    published = _published(meeting_id)
    assert published["asr"].artifact_key != orphan.artifact_key

    # cleanup: nothing before the grace period, the orphan after it, never the winner
    early = ckpt.sweep_abandoned_artifacts(settings, storage=storage)
    assert early["deleted"] == 0 and storage.exists(workspace_id, orphan.artifact_key)
    later = datetime.now(UTC) + timedelta(seconds=settings.job_artifact_grace_seconds + 1)
    late = ckpt.sweep_abandoned_artifacts(settings, storage=storage, now=later)
    assert late["deleted"] == 1
    assert not storage.exists(workspace_id, orphan.artifact_key)
    for row in published.values():
        assert storage.exists(workspace_id, row.artifact_key)
    refreshed = {row.id: row for row in _checkpoints(meeting_id)}[orphan.id]
    assert refreshed.state == CheckpointState.ABANDONED
    assert refreshed.invalidation_reason == "not_published"
    assert refreshed.object_deleted_at is not None
    assert ckpt.sweep_abandoned_artifacts(settings, storage=storage, now=later)["deleted"] == 0
    _clean(workspace_id, settings, storage)


def test_cleanup_respects_a_live_lease(client, email_outbox, monkeypatch, settings, broker):
    storage = create_object_storage(settings)
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    claim, status = processor._claim_job(job_id, worker_id="live", settings=settings)
    assert status == "claimed" and claim is not None
    context = processor._stage_context(claim)
    identity = ckpt.stage_identity(
        context, StageName.ASR, provider=CountingASR().identity(),
        request={"language": "fa", "beam_size": 5, "vad_filter": True},
    )
    intent = ckpt.begin_artifact(context, identity)
    far = datetime.now(UTC) + timedelta(days=2)
    assert ckpt.sweep_abandoned_artifacts(settings, storage=storage, now=far)["scanned"] == 0
    row = {r.id: r for r in _checkpoints(claim.meeting_id)}[intent.checkpoint_id]
    assert row.state == CheckpointState.PENDING and row.object_deleted_at is None
    # once the attempt is fenced (lease expired, recovered), the intent is abandoned work
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings)["recovered"] == 1
    assert ckpt.sweep_abandoned_artifacts(settings, storage=storage, now=far)["scanned"] == 1
    row = {r.id: r for r in _checkpoints(claim.meeting_id)}[intent.checkpoint_id]
    assert row.state == CheckpointState.ABANDONED and row.object_deleted_at is not None


def test_repeated_artifact_operations_are_idempotent_and_conflicts_are_detected(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    claim, status = processor._claim_job(job_id, worker_id="w", settings=settings)
    assert status == "claimed" and claim is not None
    context = processor._stage_context(claim)
    identity = ckpt.stage_identity(
        context, StageName.ASR, provider=CountingASR().identity(),
        request={"language": "fa", "beam_size": 5, "vad_filter": True},
    )
    intent = ckpt.begin_artifact(context, identity)
    result = ASRResult(provider="p", model="m", language="fa", text="same content")
    first = ckpt.store_artifact(storage, context, intent, result, settings=settings)
    again = ckpt.store_artifact(storage, context, intent, result, settings=settings)
    assert first == again  # same identity, same content: one object, no rewrite
    with pytest.raises(ObjectConflictError):
        ckpt.store_artifact(
            storage, context, intent,
            ASRResult(provider="p", model="m", language="fa", text="different content"),
            settings=settings,
        )
    published = ckpt.publish_checkpoint(context, intent, first, result, latency_ms=1,
                                        settings=settings)
    assert published == intent.checkpoint_id
    # publishing the same intent twice is refused (it is no longer PENDING)
    with pytest.raises(ckpt.CheckpointConflict):
        ckpt.publish_checkpoint(context, intent, first, result, latency_ms=1, settings=settings)


# --- fencing, races, tenancy (PostgreSQL) ------------------------------------


@pytest.mark.postgres
def test_stale_worker_cannot_publish_its_artifact_or_checkpoint(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("fencing is PostgreSQL evidence")
    storage = create_object_storage(settings)
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    gate = threading.Event()
    result: dict[str, dict] = {}
    stale = Bundle(CountingASR(), CountingIntelligence())
    stale.asr = GatedASRProvider(gate, "late transcript")  # type: ignore[assignment]

    def doomed() -> None:
        result["value"] = process_job(job_id, worker_id="worker-stale", settings=settings,
                                      providers=stale, storage=storage)

    thread = threading.Thread(target=doomed)
    thread.start()
    _wait_until(lambda: _job(job_id).state == JobState.RUNNING)
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings)["recovered"] == 1
    healthy = _bundle()
    assert process_job(job_id, worker_id="worker-healthy", settings=settings,
                       providers=healthy, storage=storage)["status"] == "completed"
    winners = _published(_job(job_id).meeting_id)
    gate.set()
    thread.join(timeout=DEADLINE_SECONDS)
    assert result["value"]["status"] == "superseded"

    after = _checkpoints(_job(job_id).meeting_id)
    assert {row.id for row in after if row.state == CheckpointState.PUBLISHED} == {
        row.id for row in winners.values()
    }
    assert all(row.attempt == 2 for row in winners.values())
    assert not any(row.attempt == 1 for row in after)  # the stale attempt never declared
    tenant_dir = _object_path(storage, winners["asr"].artifact_key).parent
    artifact_files = sorted(p.name for p in tenant_dir.iterdir() if p.name.startswith("art-")
                            and not p.name.endswith(".metadata.json"))
    assert len(artifact_files) == 3  # exactly the winning attempt's objects
    assert _settlements() == 1
    _clean(workspace_id, settings, storage)


@pytest.mark.postgres
def test_conflicting_attempts_cannot_overwrite_the_winning_reference(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("uniqueness and fencing are PostgreSQL evidence")
    storage = create_object_storage(settings)
    providers = _bundle()
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    claim, status = processor._claim_job(job_id, worker_id="first", settings=settings)
    assert status == "claimed" and claim is not None
    context = processor._stage_context(claim)
    identity = ckpt.stage_identity(
        context, StageName.ASR, provider=providers.asr.identity(),
        request={"language": "fa", "beam_size": 5, "vad_filter": True},
    )
    intent = ckpt.begin_artifact(context, identity)
    result = ASRResult(provider="p", model="m", language="fa", text="first attempt")
    stored = ckpt.store_artifact(storage, context, intent, result, settings=settings)
    # the attempt is fenced before it publishes
    _expire_lease(job_id)
    assert recovery.sweep_expired_leases(settings)["recovered"] == 1
    with pytest.raises(StaleLeaseError):
        ckpt.publish_checkpoint(context, intent, stored, result, latency_ms=1, settings=settings)
    assert _published(claim.meeting_id) == {}
    # the next attempt wins; a second PUBLISHED row for the identity is impossible
    assert process_job(job_id, worker_id="second", settings=settings, providers=providers,
                       storage=storage)["status"] == "completed"
    winner = _published(claim.meeting_id)["asr"]
    with inspection_session_factory()() as db:
        db.add(StageCheckpoint(
            workspace_id=workspace_id, meeting_id=claim.meeting_id, job_id=job_id, attempt=9,
            attempt_id="forged-attempt", lease_generation=9, stage="asr",
            state=CheckpointState.PUBLISHED, contract_version=1, input_hash=winner.input_hash,
            input_manifest={}, artifact_key=f"tenants/{workspace_id}/objects/art-forged-000000",
            artifact_schema="ASRResult", schema_version=1, artifact_sha256="0" * 64,
            artifact_size_bytes=1, published_at=datetime.now(UTC),
        ))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    rows = {r.id: r for r in _checkpoints(claim.meeting_id)}
    assert rows[intent.checkpoint_id].state == CheckpointState.PENDING  # cleanup's job
    assert rows[winner.id].state == CheckpointState.PUBLISHED
    _clean(workspace_id, settings, storage)


@pytest.mark.postgres
def test_deletion_racing_publication_cannot_resurrect_data(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row locking is PostgreSQL evidence")
    storage = create_object_storage(settings)
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    gate = threading.Event()
    result: dict[str, dict] = {}
    bundle = Bundle(CountingASR(), CountingIntelligence())
    bundle.asr = GatedASRProvider(gate, "late transcript")  # type: ignore[assignment]

    def worker() -> None:
        result["value"] = process_job(job_id, worker_id="worker", settings=settings,
                                      providers=bundle, storage=storage)

    thread = threading.Thread(target=worker)
    thread.start()
    _wait_until(lambda: _job(job_id).state == JobState.RUNNING)
    meeting_id = _job(job_id).meeting_id
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers={"X-Workspace-ID": workspace_id, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)},
    )
    assert deleted.status_code == 204, deleted.text
    gate.set()
    thread.join(timeout=DEADLINE_SECONDS)
    assert result["value"]["status"] == "cancelled"
    assert _checkpoints(meeting_id) == []  # the cancelled attempt never declared an artifact
    tenant_dir = storage.root / "tenants" / workspace_id / "objects"
    assert not any(p.name.startswith("art-") for p in tenant_dir.iterdir())
    assert _count(TranscriptSegment) == 0 and _settlements() == 0
    assert _job(job_id).state == JobState.CANCELLED


def test_deleting_a_processed_meeting_retires_its_checkpoints_and_objects(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    job_id, workspace_id = _queued_upload(client, email_outbox, monkeypatch)
    assert process_job(job_id, worker_id="w", settings=settings, providers=_bundle(),
                       storage=storage)["status"] == "completed"
    meeting_id = _job(job_id).meeting_id
    keys = [row.artifact_key for row in _published(meeting_id).values()]
    assert len(keys) == 3 and all(storage.exists(workspace_id, key) for key in keys)
    deleted = client.delete(
        f"/api/v1/meetings/{meeting_id}",
        headers={"X-Workspace-ID": workspace_id, "X-CSRF-Token": client.cookies.get(CSRF_COOKIE)},
    )
    assert deleted.status_code == 204, deleted.text
    rows = _checkpoints(meeting_id)
    assert {(r.state, r.invalidation_reason) for r in rows} == {
        (CheckpointState.INVALIDATED, "meeting_deleted")
    }
    assert all(r.object_deleted_at is not None for r in rows)
    assert not any(storage.exists(workspace_id, key) for key in keys)
    with inspection_session_factory()() as db:
        assert invariants.check_workspace(db, workspace_id, settings=settings,
                                          storage=storage) == []


@pytest.mark.postgres
def test_cross_tenant_checkpoint_access_fails(
    client, email_outbox, monkeypatch, settings, broker
):
    if not is_postgres_url(configured_database_url()):
        pytest.skip("row-level security is PostgreSQL evidence")
    storage = create_object_storage(settings)
    job_id, victim = _queued_upload(client, email_outbox, monkeypatch)
    assert process_job(job_id, worker_id="w", settings=settings, providers=_bundle(),
                       storage=storage)["status"] == "completed"
    meeting_id = _job(job_id).meeting_id
    victim_rows = _published(meeting_id)
    other_job, attacker = _queued_upload(client, email_outbox, monkeypatch)
    assert attacker != victim

    from backend.database.relational import get_session_factory

    with get_session_factory()() as db:
        bind_tenant_context(db, workspace_id=attacker)
        assert db.scalars(select(StageCheckpoint.id).where(
            StageCheckpoint.meeting_id == meeting_id)).all() == []
        assert db.scalars(select(StageCheckpoint.id)).all() == []
        db.add(StageCheckpoint(
            workspace_id=victim, meeting_id=meeting_id, job_id=job_id, attempt=1,
            attempt_id="x", lease_generation=1, stage="asr", state=CheckpointState.PENDING,
            contract_version=1, input_hash="f" * 64, input_manifest={},
            artifact_key=f"tenants/{victim}/objects/art-attacker-0000000000",
            artifact_schema="ASRResult", schema_version=1,
        ))
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
    # the artifact key of the victim cannot be read as another tenant
    with pytest.raises(ckpt.ArtifactInvalid):
        ckpt.load_artifact(storage, workspace_id=attacker, key=victim_rows["asr"].artifact_key,
                           expected_sha256=victim_rows["asr"].artifact_sha256,
                           expected_size=victim_rows["asr"].artifact_size_bytes,
                           schema=ASRResult, max_bytes=settings.job_artifact_max_bytes)
    def _facts(rows: dict[str, StageCheckpoint]) -> dict[str, tuple]:
        return {stage: (row.id, row.state, row.artifact_sha256) for stage, row in rows.items()}

    assert _facts(_published(meeting_id)) == _facts(victim_rows)  # untouched


def test_job_response_exposes_checkpoint_provenance(
    client, email_outbox, monkeypatch, settings, broker
):
    storage = create_object_storage(settings)
    providers = _bundle(fail_intelligence=1)
    job_id, workspace_id = _fail_then_retry(
        client, email_outbox, monkeypatch, settings, storage, providers
    )
    assert process_job(job_id, worker_id="w2", settings=settings, providers=providers,
                       storage=storage)["status"] == "completed"
    meeting_id = _job(job_id).meeting_id
    response = client.get(f"/api/v1/meetings/{meeting_id}/job",
                          headers={"X-Workspace-ID": workspace_id})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["attempt_count"] == 2
    stages = {
        stage["stage"]: stage for stage in payload["stages"] if stage["attempt"] == 2
    }
    assert set(stages) == {"materialize", "asr", "diarization", "intelligence"}
    assert stages["asr"]["reused"] is True and stages["asr"]["checkpoint_id"]
    assert stages["asr"]["reason_code"] == "checkpoint_reused"
    assert stages["intelligence"]["reused"] is False and stages["intelligence"]["checkpoint_id"]
    assert MeetingStatus(response.json()["stage"]) == MeetingStatus.COMPLETED
    with inspection_session_factory()() as db:
        assert db.scalar(select(func.count(UsageEvent.id)).where(
            UsageEvent.event_type == "committed")) == 1
        assert db.get(ProcessingJob, job_id).attempt_count == 2
