"""WP08-T03: privacy and eligibility filtering at the execution boundary (ADR 0028).

The workspace AI policy decides before anything is called: a refused adapter
is never called, never replaced by another provider and never produces
output; a cloud analysis endpoint receives redacted text unless the owner
allowed the original; permission for text never covers audio; a policy that
becomes stricter during an attempt stops the attempt before its next stage.
Every provider here is a deterministic double or an OpenAI-compatible adapter
on an in-memory transport: no request leaves the process.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from backend.database.relational import get_session_factory
from backend.database.saas_models import (
    AnalysisRun,
    JobState,
    MeetingStatus,
    MeetingSummary,
    ProcessingJob,
    ReservationState,
    TranscriptRevision,
    UsageEvent,
    UsageReservation,
)
from backend.database.tenant_context import bind_tenant_context
from backend.jobs.processor import process_job
from backend.privacy.text_pii import PiiClass
from backend.providers.base import ProviderIdentity
from backend.providers.factory import ProviderBundle
from backend.providers.mocks import MockASRProvider
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.policy import DEFAULT_POLICY, PRESETS, AiPolicy
from backend.providers.registry import ModelManifest, ResolvedManifest
from backend.providers.routing import (
    LANGUAGE_NOT_SUPPORTED,
    LICENSE_NOT_PERMITTED,
    MODEL_NOT_PERMITTED,
    PROVIDER_NOT_PERMITTED,
    REGION_NOT_PERMITTED,
    STAGE_DISABLED,
    analysis_text_classes,
    evaluate,
)
from backend.providers.routing import (
    select as select_candidate,
)
from backend.services.ai_policy_service import current_policy, set_policy
from backend.services.entitlement_service import EVENT_COMMITTED
from backend.tests._database import inspection_session_factory
from backend.tests.test_ai_policy import _put_tenant
from backend.tests.test_ingestion_ledger import _get, _patch_media, _tenant, _upload
from backend.tests.test_job_failure_policy import _make_due
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider

TRANSCRIPT = "لطفاً با شماره ۰۹۱۲ ۳۴۵ ۶۷۸۹ تماس بگیرید یا به ali.rezaei@example.com بنویسید."
LLM_BASE = "https://llm.example.test/v1"


# --- doubles ----------------------------------------------------------------------------


class _CountingASR(MockASRProvider):
    def __init__(self) -> None:
        super().__init__(allow_mock=True, transcript=TRANSCRIPT, language="fa")
        self.calls = 0

    async def transcribe(self, request):
        self.calls += 1
        return await super().transcribe(request)


class _NetworkedASR(_CountingASR):
    """Speech recognition behind an HTTP endpoint (declared in its identity)."""

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="remote_asr_test",
            model="asr-test",
            params={"endpoint": "https://asr.example.test/v1/transcribe"},
        )


class _Transport:
    """In-memory OpenAI-compatible endpoint: counts and keeps every request."""

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        output = {
            "summary": "Follow up [PHONE_1]" if "[PHONE_1]" in request.content.decode() else "Done",
            "summary_source_turn_indexes": [0],
            "action_items": [],
            "insights": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    @property
    def sent_text(self) -> str:
        return "\n".join(body["messages"][1]["content"] for body in self.bodies)


def _bundle(transport: _Transport, *, asr: MockASRProvider | None = None) -> ProviderBundle:
    return ProviderBundle(
        asr=asr or _CountingASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=OpenAICompatibleIntelligenceProvider(
            base_url=LLM_BASE,
            model="test-llm",
            api_key="cloud-key-present-for-the-test",  # a configured cloud key
            client=httpx.AsyncClient(transport=httpx.MockTransport(transport)),
        ),
    )


def _manifest(
    stage: str,
    identity: ProviderIdentity,
    *,
    kind: str,
    region: str,
    manifest_id: str | None = None,
    languages: tuple[str, ...] = ("*",),
    license_policy: str = "approved",
    status: str = "qualified",
) -> dict:
    networked = kind != "in_process"
    return {
        "manifest_id": manifest_id or f"test.{stage}.{kind}",
        "version": 1,
        "stage": stage,
        "status": status,
        "provider": identity.provider,
        "model": identity.model,
        "model_revision": "2026-09-01",
        "identity_params": dict(identity.params),
        "languages": list(languages),
        "tasks": [],
        "license": "Apache-2.0",
        "license_policy": license_policy,
        "endpoint_kind": kind,
        "endpoint": identity.params.get("endpoint") if networked else None,
        "region": region,
        "offline_capable": not networked,
        "structured_output": "json_schema_strict" if stage == "intelligence" else "none",
    }


def _settings_with(settings, tmp_path: Path, manifests: list[dict]):
    path = tmp_path / "operator-manifests.json"
    path.write_text(json.dumps({"schema": "homayar.model-manifests/1", "manifests": manifests}))
    return settings.model_copy(update={"model_manifest_paths": str(path)})


def _llm_identity() -> ProviderIdentity:
    return _bundle(_Transport()).intelligence.identity()


def _policy(**stages: dict) -> dict:
    document = DEFAULT_POLICY.document()
    for stage, override in stages.items():
        document[stage] = {**document[stage], **override}
    return document


CLOUD_EU = {"endpoint_kinds": ["cloud", "in_process", "private"], "regions": ["eu-central"]}


def _set(client, tenant, document: dict, *, version: int, reason: str = "policy for the test"):
    response = _put_tenant(
        client, tenant, {"policy": document, "expected_version": version, "reason": reason}
    )
    assert response.status_code == 200, response.text


def _run(client, tenant, settings, providers, *, key: str):
    accepted = _upload(client, tenant, key=key)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["job"]["id"]
    result = process_job(job_id, worker_id=f"w-{key}", settings=settings, providers=providers)
    return job_id, accepted.json()["meeting"]["id"], result


def _intelligence_stage(client, tenant, meeting_id: str, *, attempt: int | None = None) -> dict:
    stages = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/job").json()["stages"]
    matching = [s for s in stages if s["stage"] == "intelligence"]
    if attempt is not None:
        matching = [s for s in matching if s["attempt"] == attempt]
    return max(matching, key=lambda stage: stage["attempt"])


def _snapshot_stage(client, tenant, job_id: str, stage: str, *, attempt: int = 1) -> dict:
    items = _get(client, tenant, f"/api/v1/admin/jobs/{job_id}/policy-snapshots").json()["items"]
    snapshot = next(item for item in items if item["attempt"] == attempt)
    return next(entry for entry in snapshot["stages"] if entry["stage"] == stage)


# --- decisions (no database) ------------------------------------------------------------


def _resolved(stage: str, identity: ProviderIdentity, **kwargs) -> ResolvedManifest:
    return ResolvedManifest(
        stage, ModelManifest.model_validate(_manifest(stage, identity, **kwargs)), identity
    )


LLM = ProviderIdentity(
    provider="openai_compatible", model="m", params={"endpoint": "https://llm.example.test/x"}
)
LOCAL_LLM = ProviderIdentity(provider="local_llm", model="m")


@pytest.mark.parametrize(
    ("resolved", "policy", "language", "reason"),
    [
        (
            _resolved("intelligence", LLM, kind="cloud", region="eu-central"),
            {},
            "fa",
            PROVIDER_NOT_PERMITTED,
        ),
        (
            _resolved("intelligence", LLM, kind="cloud", region="eu-central"),
            {"endpoint_kinds": ["cloud"], "regions": ["ir-thr"]},
            "fa",
            REGION_NOT_PERMITTED,
        ),
        (
            _resolved("intelligence", LLM, kind="private", region="customer-dc"),
            {"endpoint_kinds": ["private"], "regions": ["other-dc"]},
            "fa",
            REGION_NOT_PERMITTED,
        ),
        (
            _resolved("intelligence", LLM, kind="private", region="customer-dc"),
            {"manifests": ["approved.model"]},
            "fa",
            MODEL_NOT_PERMITTED,
        ),
        (
            _resolved(
                "intelligence",
                LOCAL_LLM,
                kind="in_process",
                region="local",
                status="development",
                license_policy="rejected",
            ),
            {},
            "fa",
            LICENSE_NOT_PERMITTED,
        ),
        (
            _resolved(
                "intelligence", LOCAL_LLM, kind="in_process", region="local", languages=("en",)
            ),
            {},
            "fa",
            LANGUAGE_NOT_SUPPORTED,
        ),
        (
            _resolved("intelligence", LOCAL_LLM, kind="in_process", region="local"),
            {"endpoint_kinds": []},
            "fa",
            STAGE_DISABLED,
        ),
        # An unregistered networked adapter sits in an unknown region: no
        # policy can approve it, even one that allows cloud processing.
        (
            ResolvedManifest("intelligence", None, LLM),
            {"endpoint_kinds": ["cloud"], "regions": ["eu-central"]},
            "fa",
            REGION_NOT_PERMITTED,
        ),
        (ResolvedManifest("intelligence", None, LLM), {}, "fa", PROVIDER_NOT_PERMITTED),
    ],
)
def test_refusals_are_typed(resolved, policy, language, reason):
    stage_policy = AiPolicy.model_validate(_policy(intelligence=policy)).intelligence
    decision = evaluate("intelligence", resolved, stage_policy, language=language)
    assert decision.allowed is False and decision.reason == reason
    assert "https://" not in (decision.message or "")  # no endpoint in public messages


@pytest.mark.parametrize(
    ("resolved", "language", "disclosure"),
    [
        (_resolved("intelligence", LOCAL_LLM, kind="in_process", region="local"), "fa", "none"),
        (
            _resolved(
                "intelligence", LOCAL_LLM, kind="in_process", region="local", languages=("fa",)
            ),
            "fa-IR",
            "none",
        ),
        (
            _resolved(
                "intelligence", LOCAL_LLM, kind="in_process", region="local", languages=("fa",)
            ),
            "auto",
            "none",
        ),
        (
            _resolved("intelligence", LLM, kind="private", region="customer-dc"),
            "fa",
            "original_text",
        ),
    ],
)
def test_permitted_candidates_and_what_they_receive(resolved, language, disclosure):
    decision = evaluate("intelligence", resolved, DEFAULT_POLICY.intelligence, language=language)
    assert decision.allowed and decision.disclosure == disclosure


def test_filtering_precedes_ranking_and_a_refused_candidate_is_never_ranked():
    cloud = _resolved("intelligence", LLM, kind="cloud", region="eu-central")
    private = _resolved(
        "intelligence",
        ProviderIdentity(
            provider="openai_compatible", model="m2", params={"endpoint": "http://llm.lan/v1"}
        ),
        kind="private",
        region="customer-dc",
    )
    seen: list[list[ResolvedManifest]] = []

    def cheapest_first(eligible):  # a ranker that would prefer the cloud model
        seen.append(list(eligible))
        return sorted(eligible, key=lambda c: c.placement.endpoint_kind.value)

    selection = select_candidate(
        "intelligence",
        [cloud, private],
        DEFAULT_POLICY.intelligence,
        language="fa",
        rank=cheapest_first,
    )
    assert seen == [[private]] and selection.chosen is private
    assert [d.allowed for d in selection.decisions] == [False, True]
    refused = select_candidate(
        "intelligence",
        [cloud],
        PRESETS["local_only"].intelligence,
        language="fa",
        rank=cheapest_first,
    )
    assert refused.chosen is None and refused.decision.reason == PROVIDER_NOT_PERMITTED
    assert len(seen) == 1  # nothing was ranked when nothing was eligible


def test_cloud_text_is_redacted_unless_the_owner_allows_the_original():
    cloud = _resolved("intelligence", LLM, kind="cloud", region="eu-central").placement
    private = _resolved("intelligence", LLM, kind="private", region="customer-dc").placement
    redacted = AiPolicy.model_validate(_policy(intelligence=CLOUD_EU)).intelligence
    original = redacted.model_copy(update={"cloud_text": "original"})
    every = tuple(sorted(c.value for c in PiiClass))
    assert analysis_text_classes((), cloud, redacted) == every
    assert analysis_text_classes((), cloud, original) == ()
    assert analysis_text_classes((), private, redacted) == ()
    # The workspace redaction policy applies to every endpoint.
    assert analysis_text_classes(("phone",), private, redacted) == ("phone",)
    assert analysis_text_classes(("phone",), cloud, original) == ("phone",)


# --- at the execution boundary ------------------------------------------------------------


def _usage(job_id: str) -> tuple[ReservationState, int]:
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        reservation = db.get(UsageReservation, job.reservation_id)
        events = db.scalars(
            select(UsageEvent).where(
                UsageEvent.reservation_id == job.reservation_id,
                UsageEvent.event_type == EVENT_COMMITTED,
            )
        ).all()
        return reservation.state, len(events)


def test_a_local_only_tenant_with_cloud_keys_sends_nothing_and_stays_truthful(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "routing-local-only")
    transport = _Transport()
    cloud_settings = _settings_with(
        settings,
        tmp_path,
        [_manifest("intelligence", _llm_identity(), kind="cloud", region="eu-central")],
    )

    # The default policy (no stored version) allows no cloud processing.
    job_id, meeting_id, result = _run(
        client, tenant, cloud_settings, _bundle(transport), key="routing-default"
    )
    assert result["status"] == MeetingStatus.PARTIAL.value, result
    assert transport.bodies == []
    stage = _intelligence_stage(client, tenant, meeting_id)
    assert stage["state"] == "unavailable" and stage["reason_code"] == PROVIDER_NOT_PERMITTED
    assert stage["manifest_id"] == "test.intelligence.cloud"
    entry = _snapshot_stage(client, tenant, job_id, "intelligence")
    assert entry["verdict"] == "denied" and entry["reason"] == PROVIDER_NOT_PERMITTED
    assert entry["endpoint_kind"] == "cloud" and entry["region"] == "eu-central"

    # An explicit local-only policy: same, and nothing is fabricated.
    _set(client, tenant, PRESETS["local_only"].document(), version=0)
    job_id, meeting_id, result = _run(
        client, tenant, cloud_settings, _bundle(transport), key="routing-local"
    )
    assert result["status"] == MeetingStatus.PARTIAL.value and transport.bodies == []
    with inspection_session_factory()() as db:
        assert (
            db.scalars(select(MeetingSummary).where(MeetingSummary.meeting_id == meeting_id)).all()
            == []
        )
        run = db.scalars(select(AnalysisRun).where(AnalysisRun.meeting_id == meeting_id)).one()
        assert run.state.value == "unavailable" and run.provider is None
        assert run.reason_code == PROVIDER_NOT_PERMITTED
    # The transcript exists (in-process ASR) and the audio is settled once.
    transcript = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/transcript")
    assert transcript.status_code == 200 and "۰۹۱۲" in transcript.text
    assert _usage(job_id) == (ReservationState.COMMITTED, 1)


def test_a_private_endpoint_is_allowed_by_default_but_not_air_gapped(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "routing-private")
    transport = _Transport()
    private_settings = _settings_with(
        settings,
        tmp_path,
        [_manifest("intelligence", _llm_identity(), kind="private", region="customer-dc")],
    )
    _job, meeting_id, result = _run(
        client, tenant, private_settings, _bundle(transport), key="routing-private-1"
    )
    assert result["status"] == MeetingStatus.COMPLETED.value, result
    # A private endpoint follows the workspace redaction setting (off here).
    assert len(transport.bodies) == 1 and "ali.rezaei@example.com" in transport.sent_text

    _set(client, tenant, PRESETS["local_only"].document(), version=0)
    _job, meeting_id, result = _run(
        client, tenant, private_settings, _bundle(transport), key="routing-private-2"
    )
    assert result["status"] == MeetingStatus.PARTIAL.value and len(transport.bodies) == 1
    assert _intelligence_stage(client, tenant, meeting_id)["reason_code"] == PROVIDER_NOT_PERMITTED


def test_an_approved_cloud_region_receives_redacted_text_unless_the_original_is_allowed(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "routing-cloud")
    transport = _Transport()
    cloud_settings = _settings_with(
        settings,
        tmp_path,
        [_manifest("intelligence", _llm_identity(), kind="cloud", region="eu-central")],
    )
    _set(client, tenant, _policy(intelligence=CLOUD_EU), version=0)
    job_id, meeting_id, result = _run(
        client, tenant, cloud_settings, _bundle(transport), key="routing-cloud-1"
    )
    assert result["status"] == MeetingStatus.COMPLETED.value, result
    assert len(transport.bodies) == 1
    sent = transport.sent_text
    assert "ali.rezaei" not in sent and "۶۷۸۹" not in sent and "6789" not in sent
    assert "[PHONE_1]" in sent and "[EMAIL_1]" in sent
    entry = _snapshot_stage(client, tenant, job_id, "intelligence")
    assert entry["verdict"] == "allowed" and entry["disclosure"] == "redacted_text"
    with inspection_session_factory()() as db:
        run = db.scalars(select(AnalysisRun).where(AnalysisRun.meeting_id == meeting_id)).one()
        assert run.input_view == "redacted" and run.input_policy_digest is not None
    # The workspace itself still shows originals (its redaction is off).
    transcript = _get(client, tenant, f"/api/v1/meetings/{meeting_id}/transcript").json()
    assert "ali.rezaei@example.com" in transcript["segments"][0]["text"]

    # The owner allows the original text for this cloud region.
    original = _policy(intelligence={**CLOUD_EU, "cloud_text": "original"})
    _set(client, tenant, original, version=1)
    job_id, meeting_id, result = _run(
        client, tenant, cloud_settings, _bundle(transport), key="routing-cloud-2"
    )
    assert result["status"] == MeetingStatus.COMPLETED.value
    assert len(transport.bodies) == 2 and "ali.rezaei@example.com" in json.dumps(
        transport.bodies[1], ensure_ascii=False
    )
    assert _snapshot_stage(client, tenant, job_id, "intelligence")["disclosure"] == "original_text"

    # A region outside the policy is refused.
    _set(client, tenant, _policy(intelligence={**CLOUD_EU, "regions": ["ir-thr"]}), version=2)
    _job, meeting_id, result = _run(
        client, tenant, cloud_settings, _bundle(transport), key="routing-cloud-3"
    )
    assert result["status"] == MeetingStatus.PARTIAL.value and len(transport.bodies) == 2
    assert _intelligence_stage(client, tenant, meeting_id)["reason_code"] == REGION_NOT_PERMITTED


def test_permission_for_redacted_text_never_covers_audio(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "routing-audio")
    transport = _Transport()
    asr = _NetworkedASR()
    cloud_settings = _settings_with(
        settings,
        tmp_path,
        [
            _manifest("asr", asr.identity(), kind="cloud", region="eu-central"),
            _manifest("intelligence", _llm_identity(), kind="cloud", region="eu-central"),
        ],
    )
    _set(client, tenant, _policy(intelligence=CLOUD_EU), version=0)
    job_id, meeting_id, result = _run(
        client, tenant, cloud_settings, _bundle(transport, asr=asr), key="routing-audio-1"
    )
    assert result == {"job_id": job_id, "status": "failed", "code": PROVIDER_NOT_PERMITTED}
    assert asr.calls == 0 and transport.bodies == []
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.state == JobState.FAILED and job.failure_code == PROVIDER_NOT_PERMITTED
        assert (
            db.scalars(
                select(TranscriptRevision).where(TranscriptRevision.meeting_id == meeting_id)
            ).all()
            == []
        )
    stages = {
        s["stage"]: s
        for s in _get(client, tenant, f"/api/v1/meetings/{meeting_id}/job").json()["stages"]
    }
    assert stages["asr"]["state"] == "failed"
    assert stages["asr"]["reason_code"] == PROVIDER_NOT_PERMITTED
    # Nothing was settled: the reservation is released, never committed.
    assert _usage(job_id) == (ReservationState.RELEASED, 0)


def test_a_redaction_failure_never_falls_back_to_the_original(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "routing-redaction-failure")
    transport = _Transport()
    cloud_settings = _settings_with(
        settings,
        tmp_path,
        [_manifest("intelligence", _llm_identity(), kind="cloud", region="eu-central")],
    )
    _set(client, tenant, _policy(intelligence=CLOUD_EU), version=0)

    def broken(*_args, **_kwargs):
        raise RuntimeError("detector unavailable")

    monkeypatch.setattr("backend.jobs.processor.redact", broken)
    _job, _meeting, result = _run(
        client, tenant, cloud_settings, _bundle(transport), key="routing-redaction"
    )
    assert result["status"] != MeetingStatus.COMPLETED.value
    assert transport.bodies == []  # neither redacted nor original text left


def test_a_stricter_policy_during_an_attempt_stops_before_the_next_stage(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "routing-mid-attempt")
    transport = _Transport()
    cloud_settings = _settings_with(
        settings,
        tmp_path,
        [_manifest("intelligence", _llm_identity(), kind="cloud", region="eu-central")],
    )
    _set(client, tenant, _policy(intelligence=CLOUD_EU), version=0)

    class _TighteningASR(_CountingASR):
        async def transcribe(self, request):
            result = await super().transcribe(request)
            if self.calls == 1:  # the owner switches to local-only meanwhile
                with get_session_factory()() as db:
                    bind_tenant_context(db, workspace_id=tenant["workspace_id"])
                    from datetime import UTC, datetime

                    set_policy(
                        db,
                        workspace_id=tenant["workspace_id"],
                        policy=PRESETS["local_only"],
                        expected_version=current_policy(
                            db, workspace_id=tenant["workspace_id"]
                        ).version,
                        actor_user_id=tenant["user_id"],
                        reason="incident: stop cloud processing",
                        now=datetime.now(UTC),
                    )
                    db.commit()
            return result

    asr = _TighteningASR()
    providers = _bundle(transport, asr=asr)
    job_id, meeting_id, first = _run(client, tenant, cloud_settings, providers, key="routing-mid")
    assert first["status"] == "retry_scheduled" and first["code"] == "policy_changed", first
    assert transport.bodies == []

    _make_due(job_id)
    second = process_job(job_id, worker_id="w-2", settings=cloud_settings, providers=providers)
    assert second["status"] == MeetingStatus.PARTIAL.value, second
    assert transport.bodies == [] and asr.calls == 1  # ASR reused from its checkpoint
    items = _get(client, tenant, f"/api/v1/admin/jobs/{job_id}/policy-snapshots").json()["items"]
    assert [(i["attempt"], i["policy_version"]) for i in items] == [(1, 1), (2, 2)]
    assert (
        _snapshot_stage(client, tenant, job_id, "intelligence", attempt=1)["verdict"] == "allowed"
    )
    assert _snapshot_stage(client, tenant, job_id, "intelligence", attempt=2)["verdict"] == "denied"
    stage = _intelligence_stage(client, tenant, meeting_id, attempt=2)
    assert stage["reason_code"] == PROVIDER_NOT_PERMITTED
