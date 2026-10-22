"""WP08-T05: deadlines, request windows, back-off, Retry-After, health
circuits and approved same-policy fallback (ADR 0030).

F22: timeouts, 429 with Retry-After, 5xx, invalid schema and permanent
authentication errors are injected through an in-memory transport; transient
retries stay within the budget, permanent errors stop, circuits open and
recover, and no fallback leaves the workspace policy.  Nothing leaves the
process.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from sqlalchemy import select

from backend.database.saas_models import (
    AnalysisRun,
    JobState,
    ProcessingJob,
    ProviderHealth,
)
from backend.jobs import processor
from backend.jobs.leases import as_utc
from backend.jobs.model_preflight import run_preflight
from backend.jobs.processor import process_job
from backend.providers import egress
from backend.providers.base import IntelligenceProvider, ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.openai_compatible import (
    RETRY_AFTER_MAX_SECONDS,
    OpenAICompatibleIntelligenceProvider,
    retry_after_seconds,
)
from backend.providers.registry import ModelManifest
from backend.providers.resilience import CIRCUIT_OPEN, RATE_LIMITED, endpoint_key
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import _get, _patch_media, _tenant, _upload
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider
from backend.tests.test_provider_routing import CLOUD_EU, _manifest, _policy, _set, _settings_with

OUTPUT = {
    "summary": "Callback requested.",
    "summary_source_turn_indexes": [0],
    "action_items": [],
    "insights": [],
    "summary_fields": {
        "purpose": {"status": "not_stated", "text": None, "basis": None, "source_turn_indexes": []},
        "resolution": {
            "status": "not_stated",
            "text": None,
            "basis": None,
            "source_turn_indexes": [],
        },
        "next_steps": {
            "status": "not_stated",
            "text": None,
            "basis": None,
            "source_turn_indexes": [],
        },
    },
    "decisions": [],
    "key_moments": [],
    "extraction": {
        name: {"status": "not_stated", "values": []}
        for name in ("intent", "topic", "entity", "outcome")
    },
}


def ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(OUTPUT)}}]})


def unavailable(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503)


def invalid(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})


def unauthorized(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401)


def too_many(seconds: int):
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": str(seconds)})

    return respond


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow upstream", request=request)


class Script:
    """Responds with the given steps in order, repeating the last one."""

    def __init__(self, *steps) -> None:
        self.steps = list(steps)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        return step(request)


def _llm(script: Script, host: str) -> OpenAICompatibleIntelligenceProvider:
    return OpenAICompatibleIntelligenceProvider(
        base_url=f"https://{host}/v1",
        model="resilience-llm",
        api_key="configured-key-for-the-test",
        client=httpx.AsyncClient(transport=httpx.MockTransport(script)),
    )


def _bundle(primary, *fallbacks) -> ProviderBundle:
    return ProviderBundle(
        asr=MockASRProvider(allow_mock=True, transcript="Please call me back tomorrow."),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=primary,
        fallbacks={"intelligence": tuple(fallbacks)} if fallbacks else {},
    )


def _private(adapter, manifest_id: str, **kwargs) -> dict:
    return _manifest(
        "intelligence",
        adapter.identity(),
        kind=kwargs.pop("kind", "private"),
        region=kwargs.pop("region", "customer-dc"),
        manifest_id=manifest_id,
        **kwargs,
    )


def _setup(client, email_outbox, monkeypatch, settings, tmp_path, label, manifests, **update):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, label)
    configured = _settings_with(settings, tmp_path, manifests).model_copy(update=update)
    return tenant, configured


def _upload_job(client, tenant, key: str) -> tuple[str, str]:
    accepted = _upload(client, tenant, key=key, language="en")
    assert accepted.status_code == 202, accepted.text
    return accepted.json()["job"]["id"], accepted.json()["meeting"]["id"]


def _job(job_id: str) -> ProcessingJob:
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        db.expunge(job)
        return job


def _health(adapter) -> ProviderHealth | None:
    with inspection_session_factory()() as db:
        row = db.get(ProviderHealth, endpoint_key(adapter.identity()))
        if row is not None:
            db.expunge(row)
        return row


def _set_open_until(adapter, value: datetime | None) -> None:
    with inspection_session_factory()() as db:
        row = db.get(ProviderHealth, endpoint_key(adapter.identity()))
        row.open_until = value
        db.commit()


def _intelligence_stage(client, tenant, meeting_id: str) -> dict:
    stages = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/job").json()["stages"]
    return max(
        (stage for stage in stages if stage["stage"] == "intelligence"),
        key=lambda stage: stage["attempt"],
    )


# --- Retry-After -----------------------------------------------------------------------


def test_retry_after_is_parsed_and_bounded():
    now = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    assert retry_after_seconds("120", now=now) == 120
    assert retry_after_seconds("0", now=now) == 0
    assert (
        retry_after_seconds(format_datetime(now + timedelta(seconds=90), usegmt=True), now=now)
        == 90
    )
    assert (
        retry_after_seconds(format_datetime(now - timedelta(minutes=5), usegmt=True), now=now) == 0
    )
    assert retry_after_seconds("99999999", now=now) == RETRY_AFTER_MAX_SECONDS
    for unusable in ("soon", "", "  ", None, "-5", "1.5"):
        assert retry_after_seconds(unusable, now=now) is None


def test_a_429_carries_retry_after_and_stays_transient():
    adapter = _llm(Script(too_many(45)), "llm-a.example.test")
    request = processor.IntelligenceRequest(transcript_text="hello")
    with egress.permit("https://llm-a.example.test/v1/chat/completions"):
        with pytest.raises(Exception) as failed:
            asyncio.run(adapter.analyze(request))
    assert failed.value.retryable is True and failed.value.retry_after_seconds == 45
    assert failed.value.code == "intelligence_provider_error"  # code unchanged (contract)


# --- budget and permanence ---------------------------------------------------------------


def test_transient_failures_retry_within_the_budget_then_dead_letter(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    script = Script(unavailable)
    primary = _llm(script, "llm-a.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        "resilience-budget",
        [_private(primary, "test.llm.primary")],
        job_max_attempts=3,
        provider_circuit_failure_threshold=100,
    )
    job_id, _meeting = _upload_job(client, tenant, "budget-1")
    outcomes = []
    for attempt in (1, 2, 3):
        outcomes.append(
            process_job(
                job_id, worker_id=f"w{attempt}", settings=configured, providers=_bundle(primary)
            )
        )
        if attempt < 3:
            _make_due(job_id)
    assert [o["status"] for o in outcomes] == ["retry_scheduled", "retry_scheduled", "failed"]
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.attempt_count == 3
    assert job.failure_code == "intelligence_provider_error"
    assert script.calls == 3  # one call per attempt: nothing beyond the budget


@pytest.mark.parametrize(
    ("step", "code"),
    [(invalid, "intelligence_invalid_response"), (unauthorized, "intelligence_provider_error")],
)
def test_permanent_errors_stop_at_once_even_with_an_approved_fallback(
    client, email_outbox, monkeypatch, settings, tmp_path, step, code
):
    primary_script, fallback_script = Script(step), Script(ok)
    primary = _llm(primary_script, "llm-a.example.test")
    fallback = _llm(fallback_script, "llm-b.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        f"resilience-permanent-{code[-8:]}",
        [_private(primary, "test.llm.primary"), _private(fallback, "test.llm.fallback")],
    )
    _set(client, tenant, _policy(intelligence={"fallback": True}), version=0)
    job_id, _meeting = _upload_job(client, tenant, "permanent-1")
    result = process_job(
        job_id, worker_id="w", settings=configured, providers=_bundle(primary, fallback)
    )
    assert result == {"job_id": job_id, "status": "failed", "code": code}
    assert _job(job_id).state == JobState.FAILED
    assert (primary_script.calls, fallback_script.calls) == (1, 0)
    health = _health(primary)
    assert health is None or health.consecutive_failures == 0  # permanent errors do not count


# --- Retry-After and circuits -----------------------------------------------------------


def test_retry_after_lengthens_the_backoff_and_holds_the_circuit_open(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    script = Script(too_many(120), ok)
    primary = _llm(script, "llm-a.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        "resilience-retry-after",
        [_private(primary, "test.llm.primary")],
    )
    job_id, meeting_id = _upload_job(client, tenant, "retry-after-1")
    before = datetime.now(UTC)
    first = process_job(job_id, worker_id="w1", settings=configured, providers=_bundle(primary))
    assert first["status"] == "retry_scheduled", first
    wait = (as_utc(_job(job_id).retry_after) - before).total_seconds()
    assert wait >= 119  # the provider's 120 s, not the 30 s base back-off
    assert as_utc(_health(primary).open_until) >= before + timedelta(seconds=119)

    # Retried early (e.g. a replay): the open circuit refuses before any call.
    _make_due(job_id)
    second = process_job(job_id, worker_id="w2", settings=configured, providers=_bundle(primary))
    assert second == {"job_id": job_id, "status": "retry_scheduled", "code": CIRCUIT_OPEN}
    assert script.calls == 1
    assert (as_utc(_job(job_id).retry_after) - datetime.now(UTC)).total_seconds() > 60

    # After the wait the trial call succeeds and closes the circuit.
    _set_open_until(primary, datetime.now(UTC) - timedelta(seconds=1))
    _make_due(job_id)
    third = process_job(job_id, worker_id="w3", settings=configured, providers=_bundle(primary))
    assert third["status"] == "completed" and script.calls == 2
    health = _health(primary)
    assert health.open_until is None and health.consecutive_failures == 0
    assert _intelligence_stage(client, tenant, meeting_id)["state"] == "completed"


def test_a_circuit_opens_after_repeated_timeouts_and_recovers(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    script = Script(timeout, timeout, ok)
    primary = _llm(script, "llm-a.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        "resilience-circuit",
        [_private(primary, "test.llm.primary")],
        provider_circuit_failure_threshold=2,
        provider_circuit_cooldown_seconds=300,
    )
    first_job, _ = _upload_job(client, tenant, "circuit-1")
    second_job, _ = _upload_job(client, tenant, "circuit-2")
    third_job, _ = _upload_job(client, tenant, "circuit-3")
    for job_id in (first_job, second_job):
        result = process_job(job_id, worker_id="w", settings=configured, providers=_bundle(primary))
        assert result["code"] == "intelligence_timeout", result
    assert _health(primary).consecutive_failures == 2
    assert as_utc(_health(primary).open_until) > datetime.now(UTC) + timedelta(seconds=250)

    # Open: another workspace job is refused without a call.
    refused = process_job(third_job, worker_id="w", settings=configured, providers=_bundle(primary))
    assert refused == {"job_id": third_job, "status": "retry_scheduled", "code": CIRCUIT_OPEN}
    assert script.calls == 2

    # Cool-down over: one trial call; success closes the circuit.
    _set_open_until(primary, datetime.now(UTC) - timedelta(seconds=1))
    _make_due(third_job)
    recovered = process_job(
        third_job, worker_id="w", settings=configured, providers=_bundle(primary)
    )
    assert recovered["status"] == "completed" and script.calls == 3
    assert _health(primary).consecutive_failures == 0 and _health(primary).open_until is None


def test_the_request_window_limits_calls_across_jobs(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    script = Script(ok)
    primary = _llm(script, "llm-a.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        "resilience-window",
        [_private(primary, "test.llm.primary")],
        provider_rate_limits="test.llm.primary=1",
    )
    first_job, _ = _upload_job(client, tenant, "window-1")
    second_job, _ = _upload_job(client, tenant, "window-2")
    assert (
        process_job(first_job, worker_id="w", settings=configured, providers=_bundle(primary))[
            "status"
        ]
        == "completed"
    )
    limited = process_job(
        second_job, worker_id="w", settings=configured, providers=_bundle(primary)
    )
    assert limited == {"job_id": second_job, "status": "retry_scheduled", "code": RATE_LIMITED}
    assert script.calls == 1
    wait = (as_utc(_job(second_job).retry_after) - datetime.now(UTC)).total_seconds()
    assert 20 <= wait <= 70  # the base back-off; the window itself ends within a minute


# --- approved same-policy fallback -----------------------------------------------------------


def test_an_approved_fallback_takes_over_within_the_same_policy(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    primary_script, fallback_script = Script(unavailable), Script(ok)
    primary = _llm(primary_script, "llm-a.example.test")
    fallback = _llm(fallback_script, "llm-b.example.test")
    manifests = [_private(primary, "test.llm.primary"), _private(fallback, "test.llm.fallback")]
    tenant, configured = _setup(
        client, email_outbox, monkeypatch, settings, tmp_path, "resilience-fallback", manifests
    )
    _set(client, tenant, _policy(intelligence={"fallback": True}), version=0)
    job_id, meeting_id = _upload_job(client, tenant, "fallback-1")
    result = process_job(
        job_id, worker_id="w", settings=configured, providers=_bundle(primary, fallback)
    )
    assert result["status"] == "completed", result
    assert (primary_script.calls, fallback_script.calls) == (1, 1)
    stage = _intelligence_stage(client, tenant, meeting_id)
    assert stage["state"] == "completed" and stage["reason_code"] == "fallback_used"
    assert "intelligence_provider_error" in stage["reason_message"]
    assert stage["manifest_id"] == "test.llm.fallback"  # the report names who produced it
    fallback_digest = ModelManifest.model_validate(manifests[1]).digest
    with inspection_session_factory()() as db:
        run = db.scalars(select(AnalysisRun).where(AnalysisRun.meeting_id == meeting_id)).one()
        assert run.manifest_digest == fallback_digest
    snapshot = _get(client, tenant, f"/api/v1/admin/jobs/{job_id}/policy-snapshots").json()
    entries = [e for e in snapshot["items"][0]["stages"] if e["stage"] == "intelligence"]
    assert [(e["candidate"], e["verdict"], e["rank"]) for e in entries] == [
        ("primary", "allowed", 0),
        ("fallback", "allowed", 1),
    ]


def test_a_fallback_is_never_used_without_approval_or_outside_the_policy(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    primary_script, fallback_script = Script(unavailable), Script(ok)
    primary = _llm(primary_script, "llm-a.example.test")
    cloud_fallback = _llm(fallback_script, "llm-b.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        "resilience-no-fallback",
        [
            _private(primary, "test.llm.primary"),
            _private(cloud_fallback, "test.llm.cloud", kind="cloud", region="eu-central"),
        ],
    )
    bundle = _bundle(primary, cloud_fallback)

    # 1. Default policy: fallback not approved (and cloud not allowed).
    job_id, _ = _upload_job(client, tenant, "no-fallback-1")
    result = process_job(job_id, worker_id="w", settings=configured, providers=bundle)
    assert result["status"] == "retry_scheduled" and fallback_script.calls == 0
    snapshot = _get(client, tenant, f"/api/v1/admin/jobs/{job_id}/policy-snapshots").json()
    fallback_entry = [e for e in snapshot["items"][0]["stages"] if e["candidate"] == "fallback"][0]
    assert fallback_entry["verdict"] == "denied" and fallback_entry["rank"] is None
    assert fallback_entry["reason"] == "provider_not_permitted"  # policy first, then approval

    # 2. Fallback approved, but the policy still allows no cloud: never called.
    _set(client, tenant, _policy(intelligence={"fallback": True}), version=0)
    job_id, _ = _upload_job(client, tenant, "no-fallback-2")
    result = process_job(job_id, worker_id="w", settings=configured, providers=bundle)
    assert result["status"] == "retry_scheduled" and fallback_script.calls == 0

    # 3. Cloud allowed in the region but fallback not approved: still never called.
    # (A non-empty region list also governs private endpoints: list both.)
    regions = {"regions": ["customer-dc", "eu-central"]}
    _set(
        client, tenant, _policy(intelligence={**CLOUD_EU, **regions, "fallback": False}), version=1
    )
    job_id, _ = _upload_job(client, tenant, "no-fallback-3")
    result = process_job(job_id, worker_id="w", settings=configured, providers=bundle)
    assert result["status"] == "retry_scheduled" and fallback_script.calls == 0
    snapshot = _get(client, tenant, f"/api/v1/admin/jobs/{job_id}/policy-snapshots").json()
    fallback_entry = [e for e in snapshot["items"][0]["stages"] if e["candidate"] == "fallback"][0]
    assert fallback_entry["reason"] == "fallback_not_approved"
    assert primary_script.calls == 3


def test_the_preflight_reports_fallback_candidates(settings, tmp_path):
    adapter = _llm(Script(ok), "llm-b.example.test")
    configured = _settings_with(settings, tmp_path, [_private(adapter, "test.llm.fallback")])
    configured = configured.model_copy(
        update={
            "allow_mock_providers": True,
            "asr_provider": "mock",
            "diarization_provider": "mock",
            "intelligence_provider": "mock",
            "llm_fallback_base_url": "https://llm-b.example.test/v1",
            "llm_fallback_model": "resilience-llm",
            "llm_fallback_api_key": "configured-key-for-the-test",
        }
    )
    code, report = run_preflight(configured)
    assert code == 0, report
    assert report["fallbacks"]["intelligence"][0]["manifest_id"] == "test.llm.fallback"
    assert "configured-key" not in json.dumps(report)


# --- deadlines and back-off ------------------------------------------------------------------


class _SlowIntelligence(MockIntelligenceProvider):
    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="slow_intelligence", model="sleepy")

    async def analyze(self, request):
        await asyncio.sleep(5)
        return await super().analyze(request)


assert issubclass(_SlowIntelligence, IntelligenceProvider)


def test_a_provider_call_never_outlives_the_job_deadline(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    tenant, configured = _setup(
        client, email_outbox, monkeypatch, settings, tmp_path, "resilience-deadline", []
    )
    job_id, _ = _upload_job(client, tenant, "deadline-1")
    with inspection_session_factory()() as db:
        db.get(ProcessingJob, job_id).deadline_at = datetime.now(UTC) + timedelta(seconds=1.5)
        db.commit()
    started = time.monotonic()
    result = process_job(
        job_id,
        worker_id="w",
        settings=configured,
        providers=_bundle(_SlowIntelligence(allow_mock=True)),
    )
    assert time.monotonic() - started < 4.5  # cut at the deadline, not after 5 s
    assert result == {"job_id": job_id, "status": "failed", "code": "job_deadline_exceeded"}
    job = _job(job_id)
    assert job.state == JobState.DEAD_LETTER and job.failure_class == "deadline"


@pytest.mark.parametrize(("fraction", "expected"), [(0.0, 30), (1.0, 33)])
def test_jitter_spreads_retries_and_never_shortens_them(
    client, email_outbox, monkeypatch, settings, tmp_path, fraction, expected
):
    primary = _llm(Script(unavailable), "llm-a.example.test")
    tenant, configured = _setup(
        client,
        email_outbox,
        monkeypatch,
        settings,
        tmp_path,
        f"resilience-jitter-{expected}",
        [_private(primary, "test.llm.primary")],
        job_retry_delay_seconds=30,
        job_retry_jitter_ratio=0.1,
    )
    monkeypatch.setattr(processor, "_jitter", lambda: fraction)
    job_id, _ = _upload_job(client, tenant, "jitter-1")
    before = datetime.now(UTC)
    process_job(job_id, worker_id="w", settings=configured, providers=_bundle(primary))
    wait = (as_utc(_job(job_id).retry_after) - before).total_seconds()
    assert expected - 1 <= wait <= expected + 3
