"""Local text-to-speech adapters (AppVision F20/F74, local AI runtime).

Two adapters, both keeping the audio and text on the deployment:

* ``PiperCLITTSProvider`` runs a Piper-style executable as a subprocess:
  argv only (never a shell), text on stdin, a hard timeout, bounded output,
  and an explicit ``tts_unavailable`` error when the executable or the voice
  model is missing.  It never reports success without a readable WAV file.
* ``LocalHTTPTTSProvider`` calls an OpenAI-compatible ``/v1/audio/speech``
  server (Piper/Coqui/LocalAI/openedai-speech style) that must be a loopback,
  private or internal host (``egress.validate_endpoint(kind="local")``); each
  request also passes the application egress check (ADR 0031).

``UnavailableTTSProvider`` is the explicit "no synthesis configured" adapter.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import tempfile
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from backend.providers import egress
from backend.providers.base import ProviderError, ProviderIdentity, TTSProvider, text_digest
from backend.providers.schemas import TTSRequest, TTSResult, TTSUsageReport

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def tts_unavailable(message: str) -> ProviderError:
    return ProviderError("tts_unavailable", message, retryable=False)


def wav_info(data: bytes) -> tuple[int | None, float | None]:
    """(sample rate, duration seconds) read from a WAV container, or
    ``(None, None)`` when it is not a readable WAV (never guessed)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            rate = reader.getframerate()
            frames = reader.getnframes()
    except (wave.Error, EOFError):
        return None, None
    if rate <= 0:
        return None, None
    return rate, frames / float(rate)


class UnavailableTTSProvider(TTSProvider):
    """Explicit response for deployments without a speech synthesis engine."""

    def __init__(self, reason: str = "No speech synthesis backend is configured") -> None:
        self._reason = reason

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(provider="unavailable", model="none")

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        raise tts_unavailable(self._reason)


class PiperCLITTSProvider(TTSProvider):
    """Piper-style CLI: ``<binary> --model <voice.onnx> --output_file <out.wav>``
    with the text on stdin.  ``voices`` maps a voice id to its model file."""

    def __init__(
        self,
        *,
        binary: str,
        voices: Mapping[str, str],
        default_voice: str,
        extra_args: Sequence[str] = (),
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        model_revision: str = "unpinned",
    ) -> None:
        if not binary.strip():
            raise ValueError("A TTS executable is required")
        if not voices or default_voice not in voices:
            raise ValueError("The default voice must be one of the configured voices")
        if timeout_seconds <= 0 or max_output_bytes <= 0:
            raise ValueError("TTS timeout and output bound must be positive")
        if any(not isinstance(arg, str) or "\x00" in arg for arg in extra_args):
            raise ValueError("TTS extra arguments must be plain strings")
        self._binary = binary.strip()
        self._voices = {key: str(value) for key, value in voices.items()}
        self._default_voice = default_voice
        self._extra_args = tuple(extra_args)
        self._timeout = timeout_seconds
        self._max_output = max_output_bytes
        self._revision = model_revision

    def identity(self) -> ProviderIdentity:
        # The voice set is the model; the executable path is not part of it.
        return ProviderIdentity(
            provider="piper_cli",
            model=self._default_voice,
            params={
                "voices_sha256": text_digest(
                    "\n".join(f"{k}={Path(v).name}" for k, v in sorted(self._voices.items()))
                ),
                "extra_args_sha256": text_digest("\x1f".join(self._extra_args)),
            },
        )

    def _resolve_binary(self) -> str:
        if os.sep in self._binary:
            path = Path(self._binary)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
            raise tts_unavailable("The local speech synthesis executable is not installed")
        found = shutil.which(self._binary)
        if found is None:
            raise tts_unavailable("The local speech synthesis executable is not installed")
        return found

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        if request.format != "wav":
            raise ProviderError(
                "tts_format_unsupported", "The local synthesizer only produces WAV audio"
            )
        voice = request.voice_id or self._default_voice
        model_path = self._voices.get(voice)
        if model_path is None:
            raise ProviderError("tts_voice_unknown", "The requested voice is not configured")
        if not Path(model_path).is_file():
            raise tts_unavailable("The voice model file is not present on this host")
        binary = self._resolve_binary()

        with tempfile.TemporaryDirectory(prefix="homayar-tts-") as workdir:
            output = Path(workdir) / "speech.wav"
            argv = [binary, "--model", model_path, "--output_file", str(output)]
            argv += list(self._extra_args)
            try:
                process = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    # Diagnostics may echo the input text: never captured.
                    stderr=asyncio.subprocess.DEVNULL,
                    cwd=workdir,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise tts_unavailable(
                    "The local speech synthesis executable is not installed"
                ) from exc
            try:
                await asyncio.wait_for(
                    process.communicate(request.text.encode("utf-8")), timeout=self._timeout
                )
            except TimeoutError:
                process.kill()
                await process.wait()
                raise ProviderError(
                    "tts_timeout", "Local speech synthesis timed out", retryable=True
                ) from None
            if process.returncode != 0:
                raise ProviderError("tts_failed", "Local speech synthesis failed")
            if not output.is_file():
                raise ProviderError("tts_failed", "Local speech synthesis produced no audio")
            size = output.stat().st_size
            if size == 0:
                raise ProviderError("tts_failed", "Local speech synthesis produced no audio")
            if size > self._max_output:
                raise ProviderError("tts_output_too_large", "Synthesized audio exceeds the limit")
            data = output.read_bytes()

        rate, duration = wav_info(data)
        if rate is None:
            raise ProviderError("tts_invalid_output", "Local speech synthesis output is not WAV")
        if request.sample_rate is not None and request.sample_rate != rate:
            raise ProviderError(
                "tts_sample_rate_unsupported",
                "The voice does not produce the requested sample rate",
            )
        return TTSResult(
            provider="piper_cli",
            model=voice,
            voice_id=voice,
            format="wav",
            audio_bytes=data,
            sample_rate=rate,
            duration_seconds=duration,
            usage=TTSUsageReport(input_characters=len(request.text), audio_seconds=duration),
        )


def _speech_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Local TTS base URL must be an HTTP(S) URL")
    path = parsed.path.rstrip("/")
    if not path.endswith("/audio/speech"):
        path = f"{path}/audio/speech"
    endpoint = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
    problem = egress.validate_endpoint(endpoint, kind="local")
    if problem is not None:
        raise ValueError(f"Local TTS base URL refused: {problem}")
    return endpoint


class LocalHTTPTTSProvider(TTSProvider):
    """OpenAI-compatible ``POST /v1/audio/speech`` on a local/private host."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        default_voice: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model.strip() or not default_voice.strip():
            raise ValueError("Local TTS model and default voice are required")
        if timeout_seconds <= 0 or max_output_bytes <= 0:
            raise ValueError("TTS timeout and output bound must be positive")
        self._endpoint = _speech_url(base_url)
        self._model = model.strip()
        self._voice = default_voice.strip()
        self._timeout = httpx.Timeout(timeout_seconds)
        self._max_output = max_output_bytes
        self._client = client

    @property
    def endpoint(self) -> str:
        return self._endpoint

    def identity(self) -> ProviderIdentity:
        return ProviderIdentity(
            provider="openai_compatible_tts",
            model=self._model,
            params={"endpoint": self._endpoint, "voice": self._voice},
        )

    async def _read(self, client: httpx.AsyncClient, payload: dict) -> tuple[int, bytes]:
        async with client.stream(
            "POST", self._endpoint, json=payload, timeout=self._timeout
        ) as response:
            if not 200 <= response.status_code < 300:
                return response.status_code, b""
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self._max_output:
                    raise ProviderError(
                        "tts_output_too_large", "Synthesized audio exceeds the limit"
                    )
                chunks.append(chunk)
            return response.status_code, b"".join(chunks)

    async def synthesize(self, request: TTSRequest) -> TTSResult:
        # Offline profile and ADR 0031: the endpoint was validated as local at
        # construction; the call itself must be covered by a routing permit.
        egress.check(self._endpoint)
        voice = request.voice_id or self._voice
        payload = {
            "model": self._model,
            "input": request.text,
            "voice": voice,
            "response_format": request.format,
        }
        try:
            if self._client is not None:
                status, data = await self._read(self._client, payload)
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    status, data = await self._read(client, payload)
        except httpx.TimeoutException:
            raise ProviderError(
                "tts_timeout", "Local speech synthesis timed out", retryable=True
            ) from None
        except httpx.RequestError:
            raise ProviderError(
                "tts_unavailable", "Local speech synthesis server is unavailable", retryable=True
            ) from None
        if not 200 <= status < 300:
            raise ProviderError(
                "tts_provider_error",
                "Local speech synthesis request failed",
                retryable=status == 429 or status >= 500,
            )
        if not data:
            raise ProviderError("tts_failed", "Local speech synthesis returned no audio")
        rate, duration = wav_info(data) if request.format == "wav" else (None, None)
        if request.format == "wav" and rate is None:
            raise ProviderError("tts_invalid_output", "Local speech synthesis output is not WAV")
        if request.sample_rate is not None and rate is not None and request.sample_rate != rate:
            raise ProviderError(
                "tts_sample_rate_unsupported",
                "The voice does not produce the requested sample rate",
            )
        return TTSResult(
            provider="openai_compatible_tts",
            model=self._model,
            voice_id=voice,
            format=request.format,
            audio_bytes=data,
            sample_rate=rate,
            duration_seconds=duration,
            usage=TTSUsageReport(input_characters=len(request.text), audio_seconds=duration),
        )
