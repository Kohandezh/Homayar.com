"""Fully local AI runtime (AppVision F20/F21/F74, K01-K07): local TTS, local
LLM templates, offline Whisper and artifacts, deployment profile O and its
egress denial, hardware profile and qualification records.

No network, model or GPU is used: subprocesses are small POSIX scripts and
HTTP goes to an in-memory transport.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import wave
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from backend.config import Settings
from backend.jobs.model_preflight import run_preflight
from backend.providers import egress
from backend.providers.artifacts import (
    MISMATCH,
    MISSING,
    OK,
    UNSAFE,
    directory_digest,
    verify_artifacts,
)
from backend.providers.base import MockProviderDisabledError, ProviderError, ProviderIdentity
from backend.providers.diarization import UnavailableDiarizationProvider
from backend.providers.factory import ProviderBundle, create_provider_bundle, create_tts_provider
from backend.providers.hardware import (
    GpuInfo,
    HostProfile,
    QualificationMeasurements,
    QualificationRecord,
    QualificationStatus,
    detect_host_profile,
    detect_nvidia_gpus,
)
from backend.providers.local_llm import LOCAL_LLM_SERVERS, local_llm_manifest_template
from backend.providers.local_tts import (
    LocalHTTPTTSProvider,
    PiperCLITTSProvider,
    UnavailableTTSProvider,
)
from backend.providers.mocks import MockASRProvider, MockTTSProvider
from backend.providers.offline_profile import (
    ARTIFACTS_UNDECLARED,
    ARTIFACTS_UNVERIFIED,
    CLOUD_ENDPOINT,
    DENIED,
    ENDPOINT_NOT_LOCAL,
    LOCAL,
    OFFLINE_EGRESS_REFUSED,
    POLICY_ALLOWS_CLOUD,
    UNAVAILABLE,
    UNREGISTERED_ADAPTER,
    evaluate_offline_profile,
    offline_permit,
)
from backend.providers.openai_compatible import OpenAICompatibleIntelligenceProvider
from backend.providers.policy import DEFAULT_POLICY, PRESETS, AiPolicy
from backend.providers.registry import ModelManifest, ResolvedManifest, manifest_digest
from backend.providers.schemas import (
    ASRRequest,
    IntelligenceRequest,
    TTSRequest,
    TTSResult,
)
from backend.providers.whisper import LocalWhisperProvider

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX shell scripts")


def run(coro):
    return asyncio.run(coro)


def _wav(rate: int = 22050, frames: int = 2205) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(b"\x00\x00" * frames)
    return buffer.getvalue()


def _script(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


# A Piper-style CLI: reads text on stdin, writes a WAV to --output_file.
FAKE_PIPER = """
import sys, wave
args = sys.argv[1:]
out = args[args.index("--output_file") + 1]
model = args[args.index("--model") + 1]
text = sys.stdin.read()
open(sys.argv[0] + ".argv", "w").write("\\n".join(args) + "\\n--text--\\n" + text)
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(b"\\x00\\x00" * 11025)
"""


# --- TTS contract ----------------------------------------------------------


def test_tts_request_and_result_validation() -> None:
    with pytest.raises(ValidationError):
        TTSRequest(text="   ")
    with pytest.raises(ValidationError):
        TTSRequest(text="سلام", voice_id="bad voice")
    with pytest.raises(ValidationError):
        TTSRequest(text="x", sample_rate=1000)
    with pytest.raises(ValidationError):  # neither bytes nor path
        TTSResult(provider="p", model="m", format="wav")
    with pytest.raises(ValidationError):  # both
        TTSResult(provider="p", model="m", format="wav", audio_bytes=b"x", audio_path="/a")


def test_mock_tts_requires_permission_and_marks_mock() -> None:
    with pytest.raises(MockProviderDisabledError):
        MockTTSProvider()
    result = run(MockTTSProvider(allow_mock=True).synthesize(TTSRequest(text="سلام دنیا")))
    assert result.is_mock and result.format == "wav" and result.sample_rate == 16000
    assert result.duration_seconds == pytest.approx(0.09)
    assert result.usage.input_characters == 9
    with wave.open(io.BytesIO(result.audio_bytes)) as reader:
        assert reader.getframerate() == 16000


def test_tts_factory_defaults_to_unavailable_and_gates_mocks(tmp_path: Path) -> None:
    unavailable = create_tts_provider(SimpleNamespace(environment="production"))
    assert isinstance(unavailable, UnavailableTTSProvider)
    with pytest.raises(ProviderError) as exc:
        run(unavailable.synthesize(TTSRequest(text="x")))
    assert exc.value.code == "tts_unavailable"

    prod = SimpleNamespace(environment="production", allow_mock_providers=True, tts_provider="mock")
    with pytest.raises(MockProviderDisabledError):
        create_tts_provider(prod)
    dev = SimpleNamespace(environment="test", allow_mock_providers=True, tts_provider="mock")
    assert isinstance(create_tts_provider(dev), MockTTSProvider)

    with pytest.raises(ProviderError) as exc:
        create_tts_provider(
            SimpleNamespace(
                environment="production",
                tts_provider="local_http",
                tts_base_url="https://api.openai.com/v1",
                tts_model="tts-1",
                tts_default_voice="alloy",
            )
        )
    assert exc.value.code == "tts_provider_unconfigured"

    voice = tmp_path / "fa.onnx"
    voice.write_bytes(b"model")
    piper = create_tts_provider(
        SimpleNamespace(
            environment="production", tts_provider="piper", tts_voices=f"fa_amir={voice}"
        )
    )
    assert isinstance(piper, PiperCLITTSProvider)


def test_bundle_carries_tts_without_changing_processing_stages() -> None:
    settings = Settings(
        environment="test",
        local_ai_base_url="http://llm.internal:8000/v1",
        llm_model="m",
    )
    bundle = create_provider_bundle(settings)
    assert isinstance(bundle.tts, UnavailableTTSProvider)


# --- Piper CLI adapter -----------------------------------------------------


@posix_only
def test_piper_cli_synthesizes_with_argv_and_stdin(tmp_path: Path) -> None:
    binary = _script(tmp_path, "piper", FAKE_PIPER)
    voice = tmp_path / "fa_IR-amir-medium.onnx"
    voice.write_bytes(b"onnx")
    provider = PiperCLITTSProvider(
        binary=str(binary), voices={"fa_amir": str(voice)}, default_voice="fa_amir"
    )
    text = "سلام; rm -rf / $(whoami)"
    result = run(provider.synthesize(TTSRequest(text=text, language="fa")))
    assert result.provider == "piper_cli" and not result.is_mock
    assert result.sample_rate == 22050
    assert result.duration_seconds == pytest.approx(0.5)
    assert result.usage.audio_seconds == pytest.approx(0.5)
    recorded = Path(str(binary) + ".argv").read_text()
    args, _, stdin_text = recorded.partition("\n--text--\n")
    assert args.splitlines()[:2] == ["--model", str(voice)]
    assert stdin_text == text  # passed as data, never through a shell

    with pytest.raises(ProviderError) as exc:
        run(provider.synthesize(TTSRequest(text="x", sample_rate=16000)))
    assert exc.value.code == "tts_sample_rate_unsupported"
    with pytest.raises(ProviderError) as exc:
        run(provider.synthesize(TTSRequest(text="x", format="mp3")))
    assert exc.value.code == "tts_format_unsupported"
    with pytest.raises(ProviderError) as exc:
        run(provider.synthesize(TTSRequest(text="x", voice_id="other")))
    assert exc.value.code == "tts_voice_unknown"


def test_piper_cli_missing_binary_or_model_is_unavailable(tmp_path: Path) -> None:
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"onnx")
    for binary in (str(tmp_path / "no-such-piper"), "homayar-no-such-piper-binary"):
        provider = PiperCLITTSProvider(binary=binary, voices={"v": str(voice)}, default_voice="v")
        with pytest.raises(ProviderError) as exc:
            run(provider.synthesize(TTSRequest(text="x")))
        assert exc.value.code == "tts_unavailable"
    provider = PiperCLITTSProvider(
        binary=sys.executable, voices={"v": str(tmp_path / "missing.onnx")}, default_voice="v"
    )
    with pytest.raises(ProviderError) as exc:
        run(provider.synthesize(TTSRequest(text="x")))
    assert exc.value.code == "tts_unavailable"


@posix_only
def test_piper_cli_timeout_failure_and_bounds(tmp_path: Path) -> None:
    voice = tmp_path / "v.onnx"
    voice.write_bytes(b"onnx")

    def provider(body: str, **kwargs) -> PiperCLITTSProvider:
        binary = _script(tmp_path, f"piper{len(list(tmp_path.iterdir()))}", body)
        return PiperCLITTSProvider(
            binary=str(binary), voices={"v": str(voice)}, default_voice="v", **kwargs
        )

    slow = provider("import time, sys\nsys.stdin.read()\ntime.sleep(30)\n", timeout_seconds=0.5)
    with pytest.raises(ProviderError) as exc:
        run(slow.synthesize(TTSRequest(text="x")))
    assert exc.value.code == "tts_timeout" and exc.value.retryable

    failing = provider("import sys\nsys.stdin.read()\nsys.exit(3)\n")
    with pytest.raises(ProviderError) as exc:
        run(failing.synthesize(TTSRequest(text="x")))
    assert exc.value.code == "tts_failed"

    silent = provider("import sys\nsys.stdin.read()\n")  # exits 0, writes nothing
    with pytest.raises(ProviderError) as exc:
        run(silent.synthesize(TTSRequest(text="x")))
    assert exc.value.code == "tts_failed"

    garbage = provider(
        "import sys\nargs=sys.argv[1:]\nsys.stdin.read()\n"
        "open(args[args.index('--output_file')+1],'wb').write(b'not a wav')\n"
    )
    with pytest.raises(ProviderError) as exc:
        run(garbage.synthesize(TTSRequest(text="x")))
    assert exc.value.code == "tts_invalid_output"

    bounded = provider(FAKE_PIPER, max_output_bytes=100)
    with pytest.raises(ProviderError) as exc:
        run(bounded.synthesize(TTSRequest(text="x")))
    assert exc.value.code == "tts_output_too_large"


# --- Local HTTP TTS adapter and offline egress -----------------------------


class _SpeechServer:
    def __init__(self, body: bytes = b"", status: int = 200) -> None:
        self.body = body or _wav()
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, content=self.body)


def _http_tts(server: _SpeechServer, base_url: str = "http://127.0.0.1:8880/v1", **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    return LocalHTTPTTSProvider(
        base_url=base_url, model="piper-fa", default_voice="fa_amir", client=client, **kwargs
    )


def test_local_http_tts_calls_speech_endpoint_under_permit() -> None:
    server = _SpeechServer()
    provider = _http_tts(server)
    assert provider.endpoint == "http://127.0.0.1:8880/v1/audio/speech"
    with pytest.raises(ProviderError) as exc:  # no routing permit: nothing leaves
        run(provider.synthesize(TTSRequest(text="سلام")))
    assert exc.value.code == egress.EGRESS_NOT_PERMITTED and not server.requests

    with offline_permit(provider.endpoint):
        result = run(provider.synthesize(TTSRequest(text="سلام", voice_id="fa_b")))
    body = json.loads(server.requests[0].content)
    assert body == {"model": "piper-fa", "input": "سلام", "voice": "fa_b", "response_format": "wav"}
    assert "authorization" not in server.requests[0].headers
    assert result.sample_rate == 22050 and result.duration_seconds == pytest.approx(0.1)


def test_local_http_tts_errors_and_output_bound() -> None:
    with offline_permit("http://tts.internal/v1/audio/speech"):
        too_big = _http_tts(_SpeechServer(), "http://tts.internal/v1", max_output_bytes=64)
        with pytest.raises(ProviderError) as exc:
            run(too_big.synthesize(TTSRequest(text="x")))
        assert exc.value.code == "tts_output_too_large"
        failing = _http_tts(_SpeechServer(status=503), "http://tts.internal/v1")
        with pytest.raises(ProviderError) as exc:
            run(failing.synthesize(TTSRequest(text="x")))
        assert exc.value.code == "tts_provider_error" and exc.value.retryable
        garbage = _http_tts(_SpeechServer(body=b"not audio"), "http://tts.internal/v1")
        with pytest.raises(ProviderError) as exc:
            run(garbage.synthesize(TTSRequest(text="x")))
        assert exc.value.code == "tts_invalid_output"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.openai.com/v1",
        "https://tts.example.com/v1",
        "http://8.8.8.8/v1",
        "http://169.254.169.254/v1",
        "http://metadata.google.internal/v1",
    ],
)
def test_public_tts_endpoint_is_refused_in_offline_profile(url: str) -> None:
    server = _SpeechServer()
    with pytest.raises(ValueError):
        _http_tts(server, url)
    speech = url.rstrip("/") + "/audio/speech"
    assert egress.validate_endpoint(speech, kind="local") is not None
    with pytest.raises(ProviderError) as exc:
        with offline_permit(speech):
            pass
    assert exc.value.code == OFFLINE_EGRESS_REFUSED
    assert not server.requests


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8880/v1",
        "http://127.0.0.1:11434/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.20:8000/v1",
        "http://ollama:11434/v1",
        "http://llm.internal:8000/v1",
        "http://gpu-box.lan/v1",
    ],
)
def test_local_endpoints_are_accepted(url: str) -> None:
    assert egress.is_local_endpoint(url)


def test_offline_permit_blocks_llm_request_to_public_host() -> None:
    requests: list[httpx.Request] = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(500))
    )
    public = OpenAICompatibleIntelligenceProvider(
        base_url="https://api.cloud-llm.example/v1", model="m", client=client
    )
    endpoint = str(public.identity().params["endpoint"])
    with pytest.raises(ProviderError) as exc:
        with offline_permit(endpoint):
            run(public.analyze(IntelligenceRequest(transcript_text="x")))
    assert exc.value.code == OFFLINE_EGRESS_REFUSED and not requests


# --- Local LLM templates ---------------------------------------------------


@pytest.mark.parametrize("server", sorted(LOCAL_LLM_SERVERS))
def test_local_llm_template_matches_adapter_and_is_not_qualified(server: str) -> None:
    document = local_llm_manifest_template(server, model="qwen2.5:7b-instruct")
    manifest = ModelManifest.model_validate(document)
    adapter = OpenAICompatibleIntelligenceProvider(
        base_url=LOCAL_LLM_SERVERS[server], model="qwen2.5:7b-instruct"
    )
    identity = adapter.identity()
    assert manifest.status.value == "development"
    assert manifest.license_policy == "review_required"
    assert manifest.identity_params == dict(identity.params)
    assert manifest.endpoint_kind.value == "private"
    assert egress.is_local_endpoint(manifest.endpoint)


def test_local_llm_template_refuses_public_endpoint() -> None:
    with pytest.raises(ValueError):
        local_llm_manifest_template("vllm", model="m", base_url="https://llm.example.com/v1")


def test_openai_compatible_adapter_talks_to_local_server_without_key() -> None:
    seen: list[httpx.Request] = []
    content = json.dumps(
        {"summary": "خلاصه", "summary_source_turn_indexes": [], "action_items": [], "insights": []}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    adapter = OpenAICompatibleIntelligenceProvider(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:7b-instruct",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    endpoint = str(adapter.identity().params["endpoint"])
    with offline_permit(endpoint):
        result = run(adapter.analyze(IntelligenceRequest(transcript_text="سلام")))
    assert result.summary == "خلاصه"
    assert str(seen[0].url) == "http://127.0.0.1:11434/v1/chat/completions"
    assert "authorization" not in seen[0].headers


# --- Offline Whisper and artifacts -----------------------------------------


def test_whisper_offline_loads_local_files_only(tmp_path: Path) -> None:
    calls: list[tuple] = []

    def factory(source, **kwargs):
        calls.append((source, kwargs))
        raise RuntimeError("stop after load")

    audio = tmp_path / "a.wav"
    audio.write_bytes(_wav())
    model_dir = tmp_path / "faster-whisper-large-v3"
    model_dir.mkdir()
    provider = LocalWhisperProvider(
        model_name="large-v3",
        device="cpu",
        compute_type="int8",
        model_factory=factory,
        local_files_only=True,
        model_path=str(model_dir),
    )
    with pytest.raises(ProviderError):
        run(provider.transcribe(ASRRequest(audio_path=str(audio))))
    assert calls == [
        (str(model_dir), {"device": "cpu", "compute_type": "int8", "local_files_only": True})
    ]
    # Identity unchanged: the same builtin manifest still matches.
    assert provider.identity() == ProviderIdentity(
        provider="local_whisper", model="large-v3", params={"compute_type": "int8"}
    )

    missing = LocalWhisperProvider(
        model_name="large-v3",
        device="cpu",
        model_factory=factory,
        local_files_only=True,
        model_path=str(tmp_path / "absent"),
    )
    with pytest.raises(ProviderError) as exc:
        run(missing.transcribe(ASRRequest(audio_path=str(audio))))
    assert exc.value.code == "asr_model_missing"


def _in_process_manifest(**overrides) -> ModelManifest:
    data = {
        "manifest_id": "asr.local-whisper.offline",
        "version": 1,
        "stage": "asr",
        "status": "development",
        "provider": "local_whisper",
        "model": "large-v3",
        "model_revision": "unpinned",
        "identity_params": {"compute_type": "int8"},
        "languages": ["*"],
        "license": "MIT",
        "license_policy": "approved",
        "endpoint_kind": "in_process",
        "endpoint": None,
        "region": "local",
        "offline_capable": True,
    }
    data.update(overrides)
    return ModelManifest.model_validate(data)


def test_artifact_checksums_pass_and_fail(tmp_path: Path) -> None:
    root = tmp_path / "models"
    model_dir = root / "whisper-large-v3"
    model_dir.mkdir(parents=True)
    (model_dir / "model.bin").write_bytes(b"weights")
    (model_dir / "config.json").write_text("{}")
    (root / "voice.onnx").write_bytes(b"voice")
    dir_sha = directory_digest(model_dir)
    file_sha = hashlib.sha256(b"voice").hexdigest()
    manifest = _in_process_manifest(
        artifacts=[
            {"path": "whisper-large-v3", "kind": "directory", "sha256": dir_sha},
            {"path": "voice.onnx", "sha256": file_sha},
        ]
    )
    report = verify_artifacts(manifest, root)
    assert report.ok and [c.status for c in report.checks] == [OK, OK]

    (model_dir / "model.bin").write_bytes(b"tampered")
    (root / "voice.onnx").unlink()
    report = verify_artifacts(manifest, root)
    assert not report.ok and [c.status for c in report.checks] == [MISMATCH, MISSING]

    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"x")
    (root / "link.bin").symlink_to(outside)
    linked = _in_process_manifest(
        artifacts=[{"path": "link.bin", "sha256": hashlib.sha256(b"x").hexdigest()}]
    )
    assert verify_artifacts(linked, root).checks[0].status == UNSAFE


def test_artifact_paths_and_digest_stability() -> None:
    for bad in ("/etc/passwd", "../x", "a/../../b"):
        with pytest.raises(ValidationError):
            _in_process_manifest(artifacts=[{"path": bad, "sha256": "0" * 64}])
    with pytest.raises(ValidationError):  # networked models have no local artifacts
        _in_process_manifest(
            endpoint_kind="private",
            endpoint="http://asr.internal/v1",
            region="on-premise",
            offline_capable=False,
            artifacts=[{"path": "m.bin", "sha256": "0" * 64}],
        )
    plain = _in_process_manifest()
    assert "artifacts" not in plain.document()
    legacy = json.dumps(
        {k: v for k, v in plain.model_dump(mode="json").items() if k != "artifacts"},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    assert manifest_digest(plain) == hashlib.sha256(legacy.encode()).hexdigest()


def test_preflight_verifies_declared_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir()
    (root / "model.bin").write_bytes(b"weights")
    manifest = _in_process_manifest(
        manifest_id="asr.mock.offline",
        provider="mock_asr",
        model="deterministic-v1",
        identity_params=MockASRProvider(allow_mock=True).identity().params,
        artifacts=[{"path": "model.bin", "sha256": hashlib.sha256(b"weights").hexdigest()}],
    )
    path = tmp_path / "operator.json"
    path.write_text(
        json.dumps({"schema": "homayar.model-manifests/1", "manifests": [manifest.document()]})
    )
    settings = Settings(
        environment="test",
        local_ai_base_url="http://llm.internal:8000/v1",
        llm_model="m",
        model_manifest_paths=str(path),
    )
    bundle = ProviderBundle(
        asr=MockASRProvider(allow_mock=True),
        diarization=UnavailableDiarizationProvider(),
        intelligence=OpenAICompatibleIntelligenceProvider(
            base_url="http://llm.internal:8000/v1", model="m"
        ),
    )
    code, report = run_preflight(settings, providers=bundle)
    assert code == 1 and report["stages"]["asr"]["error"] == "artifact_root_unconfigured"

    with_root = settings.model_copy(update={"model_artifact_root": str(root)})
    code, report = run_preflight(with_root, providers=bundle)
    assert report["stages"]["asr"]["ready"] and report["stages"]["asr"]["artifacts"]["ok"]

    (root / "model.bin").write_bytes(b"other")
    code, report = run_preflight(with_root, providers=bundle)
    assert code == 1 and report["stages"]["asr"]["error"] == "artifacts_unverified"

    (root / "model.bin").write_bytes(b"weights")
    code, report = run_preflight(with_root, providers=bundle, offline=True)
    offline = report["offline_profile"]
    assert offline["stages"]["asr"]["status"] == LOCAL
    assert offline["stages"]["diarization"]["status"] == UNAVAILABLE
    assert offline["stages"]["tts"]["status"] == UNAVAILABLE
    # The intelligence endpoint is local but has no manifest: placement unknown.
    assert offline["stages"]["intelligence"]["reason"] == UNREGISTERED_ADAPTER
    assert code == 1 and not report["ready"]


# --- Deployment profile O --------------------------------------------------


def _resolved(stage: str, manifest: ModelManifest | None, provider: str = "p") -> ResolvedManifest:
    identity = ProviderIdentity(
        provider=manifest.provider if manifest else provider,
        model=manifest.model if manifest else "m",
        params=dict(manifest.identity_params) if manifest else {},
    )
    return ResolvedManifest(stage, manifest, identity)


def _networked(stage: str, kind: str, endpoint: str, region: str) -> ModelManifest:
    return _in_process_manifest(
        manifest_id=f"{stage}.{kind}.x",
        stage=stage,
        provider="openai_compatible",
        endpoint_kind=kind,
        endpoint=endpoint,
        region=region,
        offline_capable=False,
        identity_params={"endpoint": endpoint},
    )


def test_offline_profile_resolves_every_stage_locally_or_unavailable(tmp_path: Path) -> None:
    (tmp_path / "w.bin").write_bytes(b"w")
    asr = _in_process_manifest(
        artifacts=[{"path": "w.bin", "sha256": hashlib.sha256(b"w").hexdigest()}]
    )
    llm = _networked("intelligence", "private", "http://127.0.0.1:8080/v1/chat/completions", "lab")
    tts = _networked("tts", "private", "http://tts.internal/v1/audio/speech", "lab")
    resolved = {
        "asr": _resolved("asr", asr),
        "diarization": _resolved("diarization", None, provider="unavailable"),
        "intelligence": _resolved("intelligence", llm),
        "tts": _resolved("tts", tts),
    }
    report = evaluate_offline_profile(
        resolved, DEFAULT_POLICY, artifact_root=tmp_path, require_artifacts=True
    )
    assert report.ok, report.document()
    statuses = {s: v["status"] for s, v in report.document()["stages"].items()}
    assert statuses == {
        "asr": LOCAL,
        "diarization": UNAVAILABLE,
        "intelligence": LOCAL,
        "tts": LOCAL,
    }
    # Local-only policy refuses the private endpoints: unavailable, not denied.
    local_only = evaluate_offline_profile(resolved, PRESETS["local_only"], artifact_root=tmp_path)
    assert local_only.ok
    assert local_only.document()["stages"]["intelligence"]["status"] == UNAVAILABLE


def test_offline_profile_denies_any_external_endpoint(tmp_path: Path) -> None:
    cloud = _networked("intelligence", "cloud", "https://api.cloud-llm.example/v1", "eu-west")
    public_private = _networked("tts", "private", "http://tts.example.com/v1/audio/speech", "lab")
    no_artifacts = _in_process_manifest()
    resolved = {
        "asr": _resolved("asr", no_artifacts),
        "diarization": _resolved("diarization", None, provider="something"),
        "intelligence": _resolved("intelligence", cloud),
        "tts": _resolved("tts", public_private),
    }
    report = evaluate_offline_profile(resolved, DEFAULT_POLICY, require_artifacts=True)
    reasons = {v.stage: v.reason for v in report.denials}
    assert not report.ok
    assert reasons == {
        "asr": ARTIFACTS_UNDECLARED,
        "diarization": UNREGISTERED_ADAPTER,
        "intelligence": CLOUD_ENDPOINT,
        "tts": ENDPOINT_NOT_LOCAL,
    }

    tampered = _in_process_manifest(artifacts=[{"path": "w.bin", "sha256": "0" * 64}])
    report = evaluate_offline_profile(
        {"asr": _resolved("asr", tampered)}, DEFAULT_POLICY, artifact_root=tmp_path
    )
    assert report.denials[0].reason == ARTIFACTS_UNVERIFIED

    cloud_policy = AiPolicy.model_validate(
        {
            **DEFAULT_POLICY.document(),
            "intelligence": {
                "endpoint_kinds": ["in_process", "private", "cloud"],
                "regions": ["eu-west"],
            },
        }
    )
    report = evaluate_offline_profile({}, cloud_policy)
    assert [(v.stage, v.reason) for v in report.denials] == [("intelligence", POLICY_ALLOWS_CLOUD)]
    assert all(v.status in {UNAVAILABLE, DENIED} for v in report.stages)


# --- Hardware profile and qualification ------------------------------------


def _profile() -> HostProfile:
    return HostProfile(
        profile_id="gpu-a10-24g",
        os="linux",
        arch="x86_64",
        cpu_logical_cores=16,
        ram_bytes=64 * 1024**3,
        gpus=[GpuInfo(vendor="nvidia", name="NVIDIA A10", memory_mib=23028, driver_version="550")],
    )


def test_hardware_profile_detection_uses_safe_fallbacks() -> None:
    def missing(*args, **kwargs):
        raise FileNotFoundError

    profile = detect_host_profile(run=missing)
    assert profile.gpus == [] and any("nvidia-smi" in n for n in profile.detection_notes)
    assert profile.cpu_logical_cores == os.cpu_count()

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    assert detect_nvidia_gpus(timeout)[0] == []

    captured: dict = {}

    def smi(argv, **kwargs):
        captured.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 0, "NVIDIA A10, 23028, 550.54\n", "")

    gpus, note = detect_nvidia_gpus(smi)
    assert note is None and gpus[0].memory_mib == 23028 and gpus[0].driver_version == "550.54"
    assert captured["timeout"] > 0 and "shell" not in captured
    assert captured["argv"][0] == "nvidia-smi"


def test_hardware_profile_and_qualification_serialize_round_trip() -> None:
    profile = _profile()
    assert HostProfile.from_json(profile.to_json()) == profile
    assert json.loads(profile.to_json())["schema"] == "homayar.hardware-profile/1"
    assert profile.digest == _profile().model_copy(update={"detection_notes": ["x"]}).digest

    planned = QualificationRecord(
        manifest_id="asr.local-whisper.large-v3",
        manifest_version=1,
        stage="asr",
        profile=profile,
        status=QualificationStatus.PLANNED,
    )
    assert QualificationRecord.from_json(planned.to_json()) == planned

    validated = QualificationRecord(
        manifest_id="asr.local-whisper.large-v3",
        manifest_version=1,
        stage="asr",
        profile=profile,
        status=QualificationStatus.VALIDATED,
        measurements=QualificationMeasurements(
            realtime_factor=0.21,
            sample_count=40,
            measured_at=datetime(2026, 9, 1, tzinfo=UTC),
            dataset="fa-call-center-eval-v1",
        ),
    )
    assert QualificationRecord.from_json(validated.to_json()) == validated

    with pytest.raises(ValidationError):  # validated without evidence
        QualificationRecord(**{**planned.model_dump(), "status": "validated"})
    with pytest.raises(ValidationError):  # planned with invented numbers
        QualificationRecord(**{**validated.model_dump(), "status": "planned"})
    with pytest.raises(ValidationError):  # a measurement without a number
        QualificationMeasurements(
            sample_count=1, measured_at=datetime(2026, 9, 1, tzinfo=UTC), dataset="d"
        )
