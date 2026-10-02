"""WP08-T01: versioned model/prompt/capability manifests and preflight (ADR 0026).

An adapter runs only under a manifest that matches its declared identity
exactly (an edited prompt or another endpoint is an unknown model); strict
mode admits qualified manifests only and fails before any stage runs; every
run records the manifest it ran under; a prior version can be selected again
without touching the runs of any other version.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.config import REVEAL_SECRETS_CONTEXT, Settings
from backend.database.saas_models import (
    AnalysisRun,
    ManifestImmutableError,
    ModelManifestRecord,
    ProcessingJob,
    ProcessingStageRun,
)
from backend.jobs.model_preflight import run_preflight
from backend.jobs.processor import process_job
from backend.providers import openai_compatible
from backend.providers.base import ProviderError, ProviderIdentity
from backend.providers.factory import ProviderBundle, create_asr_provider
from backend.providers.mocks import MockASRProvider, MockIntelligenceProvider
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.registry import (
    BUILTIN_MANIFESTS,
    ManifestError,
    ManifestRegistry,
    ManifestStatus,
    ModelManifest,
    load_registry,
    parse_pins,
)
from backend.services.model_registry_service import register_manifest
from backend.tests._database import inspection_session_factory
from backend.tests.test_ingestion_ledger import CONTENT, _patch_media, _tenant, _upload
from backend.tests.test_job_processor import SingleSpeakerDiarizationProvider


class _CountingASR(MockASRProvider):
    def __init__(self) -> None:
        super().__init__(allow_mock=True, transcript="پیگیری قرارداد انجام شود")
        self.calls = 0

    async def transcribe(self, request):
        self.calls += 1
        return await super().transcribe(request)


class _CountingIntelligence(MockIntelligenceProvider):
    def __init__(self) -> None:
        super().__init__(allow_mock=True)
        self.calls = 0

    async def analyze(self, request):
        self.calls += 1
        return await super().analyze(request)


def _bundle() -> ProviderBundle:
    return ProviderBundle(
        asr=_CountingASR(),
        diarization=SingleSpeakerDiarizationProvider(),
        intelligence=_CountingIntelligence(),
    )


def _manifest(stage: str, identity: ProviderIdentity, **overrides) -> dict:
    document = {
        "manifest_id": f"test.{stage}",
        "version": 1,
        "stage": stage,
        "status": "qualified",
        "provider": identity.provider,
        "model": identity.model,
        "model_revision": "sha256:test",
        "identity_params": dict(identity.params),
        "languages": ["fa", "en"],
        "tasks": {"asr": ["transcription"], "diarization": ["speakers"]}.get(
            stage, ["summary", "action_items", "insights"]
        ),
        "license": "Apache-2.0",
        "license_policy": "approved",
        "endpoint_kind": "in_process",
        "endpoint": None,
        "region": "local",
        "offline_capable": True,
        "structured_output": "json_schema_strict" if stage == "intelligence" else "none",
    }
    document.update(overrides)
    return document


def _write(path: Path, manifests: list[dict]) -> Path:
    path.write_text(json.dumps({"schema": "homayar.model-manifests/1", "manifests": manifests}))
    return path


def _qualifying(bundle: ProviderBundle) -> list[dict]:
    return [
        _manifest(stage, getattr(bundle, stage).identity())
        for stage in ("asr", "diarization", "intelligence")
    ]


def _strict(settings: Settings, **update) -> Settings:
    return settings.model_copy(update={"model_manifest_strict": True, **update})


# --- manifests ----------------------------------------------------------------------

IDENTITY = ProviderIdentity(provider="local_whisper", model="base", params={"compute_type": "int8"})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"endpoint": "https://api.example.com/v1"}, "no endpoint"),
        ({"endpoint_kind": "cloud", "endpoint": None, "offline_capable": False}, "HTTP"),
        (
            {
                "endpoint_kind": "cloud",
                "endpoint": "https://api.example.com/v1",
                "region": "unknown",
                "offline_capable": False,
            },
            "region",
        ),
        (
            {"endpoint_kind": "private", "endpoint": "http://llm.internal/v1"},
            "offline",
        ),
        ({"model_revision": "unpinned"}, "pins the model revision"),
        ({"model_revision": "latest"}, "pins the model revision"),
        ({"model_revision": "Main"}, "pins the model revision"),
        ({"model": "llama3:latest"}, "moving alias"),
        ({"model": "chatgpt-4o-latest"}, "moving alias"),
        ({"license_policy": "review_required"}, "approved licence"),
        ({"manifest_id": "Bad Id"}, "manifest_id"),
        ({"languages": ["persian"]}, "language"),
        ({"surprise": 1}, "Extra inputs"),
    ],
)
def test_manifests_are_validated(overrides, message):
    with pytest.raises(ValueError, match=message):
        ModelManifest.model_validate(_manifest("asr", IDENTITY, **overrides))


def test_one_id_and_version_cannot_carry_two_documents(tmp_path):
    first = _manifest("asr", IDENTITY)
    second = _manifest("asr", IDENTITY, notes="edited in place")
    with pytest.raises(ManifestError) as conflict:
        ManifestRegistry.from_files([_write(tmp_path / "m.json", [first, second])])
    assert conflict.value.code == "manifest_conflict"
    # The identical document twice is the same manifest.
    registry = ManifestRegistry.from_files([_write(tmp_path / "same.json", [first, first])])
    assert len(registry.all()) == 1


def test_resolution_is_exact_strict_and_pinnable(tmp_path):
    registry = ManifestRegistry.from_files(
        [
            _write(
                tmp_path / "m.json",
                [
                    _manifest("asr", IDENTITY),
                    _manifest("asr", IDENTITY, version=2, notes="re-benchmarked"),
                    _manifest("asr", IDENTITY, version=3, status="retired"),
                    _manifest(
                        "asr",
                        IDENTITY,
                        manifest_id="test.asr-dev",
                        status="development",
                        model_revision="unpinned",
                    ),
                ],
            )
        ]
    )
    chosen = registry.resolve("asr", IDENTITY, strict=True)
    assert (chosen.manifest_id, chosen.version) == ("test.asr", 2)  # retired v3 is never chosen
    pinned = registry.resolve("asr", IDENTITY, strict=True, pin=("test.asr", 1))
    assert pinned.version == 1 and pinned.digest == registry.get("test.asr", 1).digest

    other = ProviderIdentity(provider="local_whisper", model="base", params={"compute_type": "f16"})
    with pytest.raises(ManifestError) as unknown:
        registry.resolve("asr", other, strict=True)
    assert unknown.value.code == "model_not_qualified"
    relaxed = registry.resolve("asr", other, strict=False)
    assert relaxed.manifest is None and relaxed.manifest_id == "unregistered"
    with pytest.raises(ManifestError) as missing:
        registry.resolve("asr", IDENTITY, strict=True, pin=("test.asr", 9))
    assert missing.value.code == "manifest_pin_unknown"
    with pytest.raises(ManifestError) as mismatch:
        registry.resolve("asr", other, strict=True, pin=("test.asr", 1))
    assert mismatch.value.code == "model_not_qualified"
    with pytest.raises(ManifestError):
        registry.resolve("asr", IDENTITY, strict=True, pin=("test.asr", 3))  # retired


def test_an_edited_prompt_or_another_endpoint_is_an_unknown_model(tmp_path, monkeypatch):
    adapter = OpenAICompatibleIntelligenceProvider(
        base_url="http://llm.internal:8000/v1", model="qwen2.5-72b-instruct"
    )
    identity = adapter.identity()
    registry = ManifestRegistry.from_files(
        [
            _write(
                tmp_path / "m.json",
                [
                    _manifest(
                        "intelligence",
                        identity,
                        endpoint_kind="private",
                        endpoint=identity.params["endpoint"],
                        region="customer-dc",
                        offline_capable=False,
                    )
                ],
            )
        ]
    )
    assert registry.resolve("intelligence", identity, strict=True).version == 1
    monkeypatch.setattr(openai_compatible, "SYSTEM_PROMPT", openai_compatible.SYSTEM_PROMPT + " ")
    with pytest.raises(ManifestError):
        registry.resolve("intelligence", adapter.identity(), strict=True)
    moved = OpenAICompatibleIntelligenceProvider(
        base_url="https://api.example.com/v1", model="qwen2.5-72b-instruct"
    )
    monkeypatch.undo()
    with pytest.raises(ManifestError):
        registry.resolve("intelligence", moved.identity(), strict=True)


def test_builtin_manifests_qualify_no_speech_or_language_model(settings):
    registry = ManifestRegistry.from_files([BUILTIN_MANIFESTS])
    by_stage = {m.manifest_id: m for m in registry.all()}
    assert by_stage["diarization.unavailable"].status is ManifestStatus.QUALIFIED
    assert all(
        m.status is not ManifestStatus.QUALIFIED for m in registry.all() if m.stage != "diarization"
    )
    assert not [m for m in registry.all() if m.stage == "intelligence"]
    # The default ASR configuration is known but not qualified: strict preflight fails.
    identity = create_asr_provider(settings).identity()
    assert registry.resolve("asr", identity, strict=False).manifest_id == "asr.local-whisper.base"
    with pytest.raises(ManifestError):
        registry.resolve("asr", identity, strict=True)


def test_pins_and_strictness_settings_are_validated(settings):
    assert parse_pins("asr=a.b@2, intelligence=c.d@1") == {
        "asr": ("a.b", 2),
        "intelligence": ("c.d", 1),
    }
    for bad in ("asr=a.b", "tts=a.b@1", "asr=a.b@1,asr=a.b@2"):
        with pytest.raises(ValueError):
            parse_pins(bad)
    data = settings.model_dump(context={REVEAL_SECRETS_CONTEXT: True})
    data.update(model_manifest_pins="asr=x", _env_file=None)
    with pytest.raises(ValueError):
        Settings(**data)
    assert settings.model_manifest_strict is None


# --- preflight in the processor --------------------------------------------------------


def _job(client, tenant, key: str, index: int) -> str:
    accepted = _upload(client, tenant, key=key, content=CONTENT + bytes([index + 3]) * (index + 2))
    assert accepted.status_code == 202, accepted.text
    return accepted.json()["job"]["id"]


def test_an_unqualified_model_fails_before_any_stage_runs(
    client, email_outbox, monkeypatch, settings
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "registry-strict")
    bundle = _bundle()
    job_id = _job(client, tenant, "registry-strict-1", 0)
    result = process_job(job_id, worker_id="registry", settings=_strict(settings), providers=bundle)
    assert result["status"] == "failed" and result["code"] == "model_not_qualified", result
    assert bundle.asr.calls == 0 and bundle.intelligence.calls == 0
    with inspection_session_factory()() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.failure_code == "model_not_qualified"
        assert db.scalar(select(ModelManifestRecord.digest)) is None

    code, report = run_preflight(_strict(settings), providers=_bundle())
    assert code == 1 and report["ready"] is False
    skeleton = report["stages"]["intelligence"]["manifest_skeleton"]
    assert skeleton["identity_params"] == {"max_summary_chars": 500}
    assert skeleton["status"] == "development" and skeleton["license"] == "UNKNOWN"


def test_runs_record_their_manifest_and_a_prior_version_can_be_restored(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "registry-runs")
    v1 = _write(tmp_path / "ops-v1.json", _qualifying(_bundle()))
    intelligence_v2 = _manifest(
        "intelligence", _bundle().intelligence.identity(), version=2, notes="re-qualified"
    )
    v2 = _write(tmp_path / "ops-v2.json", [intelligence_v2])
    only_v1 = _strict(settings, model_manifest_paths=str(v1))
    both = _strict(settings, model_manifest_paths=f"{v1},{v2}")
    pinned = _strict(
        settings,
        model_manifest_paths=f"{v1},{v2}",
        model_manifest_pins="intelligence=test.intelligence@1",
    )
    assert run_preflight(only_v1, providers=_bundle())[0] == 0

    jobs = {}
    for index, (name, config) in enumerate((("v1", only_v1), ("v2", both), ("back", pinned))):
        jobs[name] = _job(client, tenant, f"registry-runs-{name}", index)
        result = process_job(jobs[name], worker_id=name, settings=config, providers=_bundle())
        assert result["status"] == "completed", result

    registry = load_registry(both)
    digests = {version: registry.get("test.intelligence", version).digest for version in (1, 2)}
    with inspection_session_factory()() as db:

        def intelligence_run(job_id: str) -> ProcessingStageRun:
            return db.scalar(
                select(ProcessingStageRun).where(
                    ProcessingStageRun.job_id == job_id,
                    ProcessingStageRun.stage == "intelligence",
                )
            )

        recorded = {name: intelligence_run(job_id) for name, job_id in jobs.items()}
        assert recorded["v1"].manifest_digest == digests[1]
        assert (recorded["v2"].manifest_version, recorded["v2"].manifest_digest) == (2, digests[2])
        assert recorded["back"].manifest_digest == digests[1]  # restored; v2's run untouched
        assert all(run.manifest_id == "test.intelligence" for run in recorded.values())
        asr = db.scalar(
            select(ProcessingStageRun).where(
                ProcessingStageRun.job_id == jobs["v1"], ProcessingStageRun.stage == "asr"
            )
        )
        assert asr.manifest_id == "test.asr" and asr.manifest_digest
        materialize = db.scalar(
            select(ProcessingStageRun).where(
                ProcessingStageRun.job_id == jobs["v1"],
                ProcessingStageRun.stage == "materialize",
            )
        )
        assert materialize.manifest_id is None
        analysis = db.scalars(select(AnalysisRun).where(AnalysisRun.job_id == jobs["v2"])).one()
        assert analysis.manifest_digest == digests[2]
        catalog = {row.digest: row for row in db.scalars(select(ModelManifestRecord))}
        assert set(digests.values()) <= set(catalog)
        assert catalog[digests[2]].document["notes"] == "re-qualified"
        assert catalog[digests[1]].document["version"] == 1

        row = catalog[digests[1]]
        row.status = "retired"
        with pytest.raises(ManifestImmutableError):
            db.flush()
        db.rollback()


def test_a_changed_document_under_a_released_version_is_refused(
    client, email_outbox, monkeypatch, settings, tmp_path
):
    _patch_media(monkeypatch)
    tenant = _tenant(client, email_outbox, "registry-conflict")
    original = _qualifying(_bundle())
    with inspection_session_factory()() as db:
        for document in original:
            register_manifest(db, ModelManifest.model_validate(document))
        db.commit()
    edited = [dict(original[0], languages=["fa"]), *original[1:]]
    config = _strict(settings, model_manifest_paths=str(_write(tmp_path / "edited.json", edited)))
    bundle = _bundle()
    result = process_job(
        _job(client, tenant, "registry-conflict-1", 0),
        worker_id="conflict",
        settings=config,
        providers=bundle,
    )
    assert result["status"] == "failed" and result["code"] == "manifest_conflict", result
    assert bundle.asr.calls == 0
    with pytest.raises(ProviderError):
        with inspection_session_factory()() as db:
            register_manifest(db, ModelManifest.model_validate(edited[0]))
