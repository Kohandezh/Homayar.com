"""Strict, bounded media validation backed by ffprobe and a bounded ffmpeg decode.

The upload should still be in private quarantine when this module is called. A
successful result is suitable for persistence; no user-controlled path or raw
probe output is retained in either metadata or public exceptions.

Pipeline (WP05-T02): filename and extension policy → declared MIME syntax →
size bounds → signature pre-check (no subprocess is spawned for bytes that are
obviously not media or belong to another container family) → bounded ffprobe
(fixed argv, no shell, probe/analyze/allocation caps, timeout) → container /
codec / stream-shape / duration bounds → duration plausibility floor for
compressed audio → bounded ffmpeg decode of a prefix (fixed argv, ``-xerror``,
single thread, timeout) → declared MIME against the detected content.

Every refusal is a :class:`MediaValidationError` with a stable ``code`` and a
``terminal`` flag: terminal refusals are properties of the bytes and can be
recorded against the ingestion identity; transient ones (inspection tooling
unavailable or timed out, file changed underneath) leave the identity free.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
import subprocess
import tempfile
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol

DEFAULT_ALLOWED_EXTENSIONS: Final = frozenset(
    {".wav", ".mp3", ".ogg", ".m4a", ".aac", ".flac", ".mp4", ".webm"}
)
DEFAULT_MAX_SIZE_BYTES: Final = 250 * 1024 * 1024
DEFAULT_MAX_DURATION_SECONDS: Final = 14_400.0
DEFAULT_FFPROBE_TIMEOUT_SECONDS: Final = 10.0
DEFAULT_FFMPEG_TIMEOUT_SECONDS: Final = 20.0

_MAX_FILENAME_BYTES: Final = 255
_MAX_PROBE_OUTPUT_BYTES: Final = 256 * 1024
_MAX_PROBE_SIZE_BYTES: Final = 8 * 1024 * 1024
_MAX_ANALYZE_DURATION_MICROSECONDS: Final = 10_000_000
_MAX_FFPROBE_ALLOCATION_BYTES: Final = 64 * 1024 * 1024
_MAX_DECODE_PROBE_SECONDS: Final = 600.0
_SIGNATURE_BYTES: Final = 64
_EXTENSION_RE = re.compile(r"^\.[a-z0-9]{1,10}$")
_MIME_RE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_CODEC_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_FORMAT_RE = re.compile(r"^[a-z0-9_]{1,32}$")
_WINDOWS_RESERVED_NAMES: Final = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{number}" for number in range(1, 10)}
    | {f"LPT{number}" for number in range(1, 10)}
)


class MediaSettings(Protocol):
    """The upload-related subset of ``backend.config.Settings``."""

    allowed_extensions: str
    max_upload_size_mb: int
    max_audio_duration_seconds: int
    media_decode_probe_seconds: float
    media_min_bytes_per_second: int


class MediaValidationError(ValueError):
    """Base error with a stable API-safe code and message.

    ``terminal`` says whether the refusal is a property of the submitted bytes
    (the same bytes would be refused again) or of the inspection environment
    (a retry may succeed).
    """

    terminal: bool = True

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class UnsafeFilenameError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Original filename is unsafe", code="unsafe_filename")


class UnsupportedExtensionError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Filename extension is not supported", code="unsupported_extension")


class UnsupportedMimeTypeError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__(
            "Declared media type is invalid or unsupported",
            code="unsupported_mime_type",
        )


class MimeTypeMismatchError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__(
            "Declared media type does not match the detected media content",
            code="mime_type_mismatch",
        )


class EmptyMediaError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Media file is empty", code="empty_media")


class MediaFileTooLargeError(MediaValidationError):
    def __init__(self, limit_bytes: int, observed_bytes: int) -> None:
        super().__init__(
            f"Media file exceeds the {limit_bytes}-byte size limit",
            code="media_file_too_large",
        )
        self.limit_bytes = limit_bytes
        self.observed_bytes = observed_bytes


class MediaFileChangedError(MediaValidationError):
    terminal = False

    def __init__(self) -> None:
        super().__init__("Media file changed during validation", code="media_file_changed")


class MediaProbeError(MediaValidationError):
    terminal = False

    def __init__(self) -> None:
        super().__init__("Media inspection could not be completed", code="media_probe_failed")


class MediaProbeUnavailableError(MediaValidationError):
    terminal = False

    def __init__(self) -> None:
        super().__init__("Media inspection service is unavailable", code="media_probe_unavailable")


class MediaProbeTimeoutError(MediaValidationError):
    terminal = False

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__("Media inspection timed out", code="media_probe_timeout")
        self.timeout_seconds = timeout_seconds


class MalformedMediaError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Media file could not be parsed", code="malformed_media")


class MalformedProbeOutputError(MediaValidationError):
    terminal = False

    def __init__(self) -> None:
        super().__init__(
            "Media inspection returned invalid metadata",
            code="invalid_probe_metadata",
        )


class UnsupportedContainerError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Detected media container is not supported", code="unsupported_container")


class ExtensionContentMismatchError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__(
            "Filename extension does not match the detected media container",
            code="extension_content_mismatch",
        )


class UnsupportedCodecError(MediaValidationError):
    def __init__(self, stream_type: str) -> None:
        super().__init__(
            f"Media uses an unsupported {stream_type} codec",
            code=f"unsupported_{stream_type}_codec",
        )
        self.stream_type = stream_type


class MissingAudioStreamError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Media must contain an audio stream", code="missing_audio_stream")


class InvalidAudioStreamError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__(
            "Audio stream has invalid channels or sample rate",
            code="invalid_audio_stream",
        )


class InvalidMediaDurationError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Media duration must be greater than zero", code="invalid_media_duration")


class MediaDurationExceededError(MediaValidationError):
    def __init__(self, limit_seconds: float, observed_seconds: float) -> None:
        super().__init__(
            f"Media duration exceeds the {limit_seconds:g}-second limit",
            code="media_duration_exceeded",
        )
        self.limit_seconds = limit_seconds
        self.observed_seconds = observed_seconds


class MediaDurationImplausibleError(MediaValidationError):
    """The container claims far more audio than its bytes could hold."""

    def __init__(self) -> None:
        super().__init__(
            "Media duration is implausible for the file size",
            code="media_duration_implausible",
        )


class MediaDecodeError(MediaValidationError):
    def __init__(self) -> None:
        super().__init__("Media could not be decoded", code="media_decode_failed")


class MediaDecodeTimeoutError(MediaValidationError):
    terminal = False

    def __init__(self, timeout_seconds: float) -> None:
        super().__init__("Media decoding check timed out", code="media_decode_timeout")
        self.timeout_seconds = timeout_seconds


class MediaDecoderUnavailableError(MediaValidationError):
    terminal = False

    def __init__(self) -> None:
        super().__init__("Media decoding service is unavailable", code="media_decoder_unavailable")


@dataclass(frozen=True, slots=True)
class AudioStreamMetadata:
    index: int
    codec: str
    channels: int
    sample_rate_hz: int


@dataclass(frozen=True, slots=True)
class VideoStreamMetadata:
    index: int
    codec: str


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Sanitized metadata derived from the media bytes, never from name alone."""

    original_filename: str
    extension: str
    container: str
    detected_format_names: tuple[str, ...]
    mime_type: str
    duration_seconds: float
    size_bytes: int
    audio_streams: tuple[AudioStreamMetadata, ...]
    video_streams: tuple[VideoStreamMetadata, ...]

    @property
    def audio_codec(self) -> str:
        return self.audio_streams[0].codec

    @property
    def channels(self) -> int:
        return self.audio_streams[0].channels

    @property
    def sample_rate_hz(self) -> int:
        return self.audio_streams[0].sample_rate_hz

    @property
    def video_codec(self) -> str | None:
        return self.video_streams[0].codec if self.video_streams else None


@dataclass(frozen=True, slots=True)
class MediaValidationPolicy:
    allowed_extensions: frozenset[str] = DEFAULT_ALLOWED_EXTENSIONS
    max_size_bytes: int = DEFAULT_MAX_SIZE_BYTES
    max_duration_seconds: float = DEFAULT_MAX_DURATION_SECONDS
    min_sample_rate_hz: int = 8_000
    max_sample_rate_hz: int = 192_000
    max_channels: int = 8
    max_streams: int = 32
    # Compressed audio claiming fewer bytes per second than this is refused as
    # implausible (a header/granule that claims hours of audio in a few KB).
    # PCM containers are exempt: ffprobe derives their duration from the bytes.
    min_bytes_per_second: int = 64
    # Seconds of audio the bounded ffmpeg decode verifies (0 disables the decode).
    decode_probe_seconds: float = 0.0

    def __post_init__(self) -> None:
        extensions = _normalize_allowed_extensions(self.allowed_extensions)
        if not extensions or not extensions.issubset(_FORMAT_PROFILES):
            raise ValueError("allowed_extensions contains an unsupported validation profile")
        if self.max_size_bytes <= 0:
            raise ValueError("max_size_bytes must be positive")
        if not math.isfinite(self.max_duration_seconds) or self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be finite and positive")
        if self.min_sample_rate_hz <= 0 or self.max_sample_rate_hz < self.min_sample_rate_hz:
            raise ValueError("sample-rate limits are invalid")
        if self.max_channels <= 0 or self.max_streams <= 0:
            raise ValueError("channel and stream limits must be positive")
        if isinstance(self.min_bytes_per_second, bool) or self.min_bytes_per_second < 0:
            raise ValueError("min_bytes_per_second must be zero or positive")
        if (
            isinstance(self.decode_probe_seconds, bool)
            or not math.isfinite(self.decode_probe_seconds)
            or self.decode_probe_seconds < 0
            or self.decode_probe_seconds > _MAX_DECODE_PROBE_SECONDS
        ):
            raise ValueError(
                f"decode_probe_seconds must be between 0 and {_MAX_DECODE_PROBE_SECONDS:g}"
            )
        object.__setattr__(self, "allowed_extensions", extensions)

    @classmethod
    def from_settings(cls, settings: MediaSettings) -> MediaValidationPolicy:
        """Build a policy from the project's existing upload settings."""

        return cls(
            allowed_extensions=_normalize_allowed_extensions(settings.allowed_extensions),
            max_size_bytes=settings.max_upload_size_mb * 1024 * 1024,
            max_duration_seconds=float(settings.max_audio_duration_seconds),
            min_bytes_per_second=int(settings.media_min_bytes_per_second),
            decode_probe_seconds=float(settings.media_decode_probe_seconds),
        )


@dataclass(frozen=True, slots=True)
class _FormatProfile:
    container: str
    format_names: frozenset[str]
    audio_codecs: frozenset[str]
    video_codecs: frozenset[str]
    audio_mime_types: frozenset[str]
    video_mime_types: frozenset[str] = frozenset()
    canonical_audio_mime: str = ""
    canonical_video_mime: str = ""
    audio_codec_prefixes: tuple[str, ...] = ()
    forbid_video: bool = False
    # Signature family the leading bytes must belong to when they are recognised.
    family: str = ""
    # Duration is derived from the byte count by the demuxer (no plausibility floor).
    byte_exact_duration: bool = False


_ISO_BASE_MEDIA_FORMATS: Final = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"})
_FORMAT_PROFILES: Final[dict[str, _FormatProfile]] = {
    ".wav": _FormatProfile(
        container="wav",
        format_names=frozenset({"wav"}),
        audio_codecs=frozenset(),
        audio_codec_prefixes=("pcm_",),
        video_codecs=frozenset(),
        audio_mime_types=frozenset({"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}),
        canonical_audio_mime="audio/wav",
        forbid_video=True,
        family="wav",
        byte_exact_duration=True,
    ),
    ".mp3": _FormatProfile(
        container="mp3",
        format_names=frozenset({"mp3"}),
        audio_codecs=frozenset({"mp3"}),
        video_codecs=frozenset(),
        audio_mime_types=frozenset({"audio/mpeg", "audio/mp3"}),
        canonical_audio_mime="audio/mpeg",
        forbid_video=True,
        family="mpeg_audio",
    ),
    ".ogg": _FormatProfile(
        container="ogg",
        format_names=frozenset({"ogg"}),
        audio_codecs=frozenset({"flac", "opus", "speex", "vorbis"}),
        video_codecs=frozenset(),
        audio_mime_types=frozenset({"audio/ogg", "application/ogg"}),
        canonical_audio_mime="audio/ogg",
        forbid_video=True,
        family="ogg",
    ),
    ".m4a": _FormatProfile(
        container="mp4",
        format_names=_ISO_BASE_MEDIA_FORMATS,
        audio_codecs=frozenset({"aac", "alac", "mp3"}),
        video_codecs=frozenset(),
        audio_mime_types=frozenset({"audio/mp4", "audio/m4a", "audio/x-m4a"}),
        canonical_audio_mime="audio/mp4",
        forbid_video=True,
        family="isobmff",
    ),
    ".aac": _FormatProfile(
        container="aac",
        format_names=frozenset({"aac"}),
        audio_codecs=frozenset({"aac"}),
        video_codecs=frozenset(),
        audio_mime_types=frozenset({"audio/aac", "audio/x-aac"}),
        canonical_audio_mime="audio/aac",
        forbid_video=True,
        family="mpeg_audio",
    ),
    ".flac": _FormatProfile(
        container="flac",
        format_names=frozenset({"flac"}),
        audio_codecs=frozenset({"flac"}),
        video_codecs=frozenset(),
        audio_mime_types=frozenset({"audio/flac", "audio/x-flac"}),
        canonical_audio_mime="audio/flac",
        forbid_video=True,
        family="flac",
    ),
    ".mp4": _FormatProfile(
        container="mp4",
        format_names=_ISO_BASE_MEDIA_FORMATS,
        audio_codecs=frozenset({"aac", "ac3", "alac", "eac3", "mp3", "opus"}),
        video_codecs=frozenset({"av1", "h264", "hevc", "mpeg4", "vp8", "vp9"}),
        audio_mime_types=frozenset({"audio/mp4"}),
        video_mime_types=frozenset({"video/mp4"}),
        canonical_audio_mime="audio/mp4",
        canonical_video_mime="video/mp4",
        family="isobmff",
    ),
    ".webm": _FormatProfile(
        container="webm",
        format_names=frozenset({"matroska", "webm"}),
        audio_codecs=frozenset({"opus", "vorbis"}),
        video_codecs=frozenset({"av1", "vp8", "vp9"}),
        audio_mime_types=frozenset({"audio/webm"}),
        video_mime_types=frozenset({"video/webm"}),
        canonical_audio_mime="audio/webm",
        canonical_video_mime="video/webm",
        family="matroska",
    ),
}

# Leading-byte signatures that are certainly not media we accept: refused before
# any subprocess runs (resource control; the probe would fail anyway).
_NON_MEDIA_SIGNATURES: Final[tuple[bytes, ...]] = (
    b"MZ",  # PE executable
    b"\x7fELF",
    b"\xcf\xfa\xed\xfe",  # Mach-O
    b"\xfe\xed\xfa\xcf",
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
    b"PK\x03\x04",  # zip / office / jar
    b"PK\x05\x06",
    b"Rar!",
    b"7z\xbc\xaf\x27\x1c",
    b"\x1f\x8b",  # gzip
    b"BZh",
    b"\xfd7zXZ\x00",
    b"%PDF",
    b"#!",
    b"<?xml",
    b"<!DOCTYPE",
    b"<html",
    b"<HTML",
    b"\x89PNG",
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",
    b"SQLite format 3\x00",
)


def _signature_family(head: bytes) -> str | None:
    """Container family the leading bytes belong to, or None when unrecognised."""
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        return "ogg"
    if head[4:8] in {b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide"}:
        return "isobmff"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "matroska"
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mpeg_audio"  # MPEG-1/2 audio frame sync or ADTS; mp3 and aac share it
    return None


def validate_media_upload(
    file_path: str | os.PathLike[str],
    *,
    original_filename: str,
    declared_mime_type: str,
    policy: MediaValidationPolicy | None = None,
    ffprobe_path: str | os.PathLike[str] = "ffprobe",
    timeout_seconds: float = DEFAULT_FFPROBE_TIMEOUT_SECONDS,
    ffmpeg_path: str | os.PathLike[str] = "ffmpeg",
    ffmpeg_timeout_seconds: float = DEFAULT_FFMPEG_TIMEOUT_SECONDS,
) -> MediaMetadata:
    """Validate a quarantined media file using byte-derived ffprobe metadata."""

    active_policy = policy or MediaValidationPolicy()
    safe_filename, extension = _validate_filename(original_filename, active_policy)
    declared_mime = _normalize_mime_type(declared_mime_type)
    executable = _validate_executable(ffprobe_path)
    timeout = _validate_timeout(timeout_seconds)
    decoder = _validate_executable(ffmpeg_path)
    decode_timeout = _validate_timeout(ffmpeg_timeout_seconds)
    path, before = _validate_local_file(file_path, active_policy.max_size_bytes)
    _check_signature(path, extension)

    command = _build_ffprobe_command(executable, path)
    try:
        # The executable is operator-controlled and every input is an argv element.
        completed = subprocess.run(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            close_fds=True,
        )
    except subprocess.TimeoutExpired:
        raise MediaProbeTimeoutError(timeout) from None
    except FileNotFoundError:
        raise MediaProbeUnavailableError() from None
    except OSError:
        raise MediaProbeError() from None

    _ensure_file_unchanged(path, before)
    if completed.returncode != 0:
        raise MalformedMediaError()
    payload = _load_probe_payload(completed.stdout)
    metadata = _metadata_from_probe(
        payload,
        original_filename=safe_filename,
        extension=extension,
        declared_mime=declared_mime,
        size_bytes=before.st_size,
        policy=active_policy,
    )
    if active_policy.decode_probe_seconds > 0:
        _decode_prefix(decoder, path, active_policy.decode_probe_seconds, decode_timeout)
        _ensure_file_unchanged(path, before)
    return metadata


def _check_signature(path: Path, extension: str) -> None:
    """Refuse obvious non-media and foreign-family bytes before spawning anything.

    The probe stays the authority for everything it recognises; this only
    short-circuits inputs whose leading bytes already contradict the claimed
    extension (or are an executable, archive, document or image).
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_SIGNATURE_BYTES)
    except OSError:
        raise MediaProbeError() from None
    if any(head.startswith(signature) for signature in _NON_MEDIA_SIGNATURES):
        raise UnsupportedContainerError()
    family = _signature_family(head)
    expected = _FORMAT_PROFILES[extension].family
    if family is not None and expected and family != expected:
        raise ExtensionContentMismatchError()


def _decode_prefix(executable: str, path: Path, seconds: float, timeout: float) -> None:
    """Decode the first ``seconds`` of the primary audio stream to /dev/null.

    ``-xerror`` turns the first decoding error into a non-zero exit, which
    catches corrupted frames that a header-only probe accepts.  Some ffmpeg
    releases (6.1 on Ubuntu 24.04) conceal such errors and still exit 0, so
    any error-level diagnostic is treated as a failure too: ``-v error`` and
    ``-nostats`` leave nothing but errors on stderr.  Stderr goes to an
    anonymous temporary file whose size alone is checked, so no diagnostic
    is read into memory, stored, logged or shown; its volume is bounded by
    the ``-t`` decode window and the timeout.
    """
    command = _build_ffmpeg_decode_command(executable, path, seconds)
    with tempfile.TemporaryFile() as diagnostics:
        try:
            # The executable is operator-controlled and every input is an argv element.
            completed = subprocess.run(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=diagnostics,
                check=False,
                shell=False,
                timeout=timeout,
                close_fds=True,
            )
        except subprocess.TimeoutExpired:
            raise MediaDecodeTimeoutError(timeout) from None
        except FileNotFoundError:
            raise MediaDecoderUnavailableError() from None
        except OSError:
            raise MediaProbeError() from None
        reported_errors = os.fstat(diagnostics.fileno()).st_size > 0
    if completed.returncode != 0 or reported_errors:
        raise MediaDecodeError()


def _normalize_allowed_extensions(raw: str | Iterable[str]) -> frozenset[str]:
    values = raw.split(",") if isinstance(raw, str) else raw
    normalized: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("allowed extensions must be strings")
        extension = value.strip().lower()
        if extension and not extension.startswith("."):
            extension = f".{extension}"
        if not _EXTENSION_RE.fullmatch(extension):
            raise ValueError("allowed extension is invalid")
        normalized.add(extension)
    return frozenset(normalized)


def _validate_filename(
    filename: str,
    policy: MediaValidationPolicy,
) -> tuple[str, str]:
    if not isinstance(filename, str) or not filename or filename != filename.strip():
        raise UnsafeFilenameError()
    normalized = unicodedata.normalize("NFC", filename)
    if (
        normalized in {".", ".."}
        or normalized.startswith(".")
        or normalized.endswith((".", " "))
        or "/" in normalized
        or "\\" in normalized
        or any(unicodedata.category(character).startswith("C") for character in normalized)
    ):
        raise UnsafeFilenameError()
    try:
        if len(normalized.encode("utf-8")) > _MAX_FILENAME_BYTES:
            raise UnsafeFilenameError()
    except UnicodeEncodeError:
        raise UnsafeFilenameError() from None

    extension = Path(normalized).suffix.lower()
    stem = normalized[: -len(extension)] if extension else ""
    if not stem or stem.upper().split(".", maxsplit=1)[0] in _WINDOWS_RESERVED_NAMES:
        raise UnsafeFilenameError()
    if extension not in policy.allowed_extensions:
        raise UnsupportedExtensionError()
    return normalized, extension


def _normalize_mime_type(value: str) -> str:
    if not isinstance(value, str) or "\r" in value or "\n" in value:
        raise UnsupportedMimeTypeError()
    normalized = value.split(";", maxsplit=1)[0].strip().lower()
    if len(normalized) > 127 or not _MIME_RE.fullmatch(normalized):
        raise UnsupportedMimeTypeError()
    return normalized


def _validate_executable(value: str | os.PathLike[str]) -> str:
    try:
        executable = os.fspath(value)
    except TypeError:
        raise MediaProbeUnavailableError() from None
    if (
        not isinstance(executable, str)
        or not executable
        or len(executable) > 4_096
        or "\x00" in executable
        or "\r" in executable
        or "\n" in executable
    ):
        raise MediaProbeUnavailableError()
    return executable


def _validate_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout_seconds must be numeric")
    timeout = float(value)
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise ValueError("timeout_seconds must be between 0 and 60")
    return timeout


def _validate_local_file(
    file_path: str | os.PathLike[str],
    max_size_bytes: int,
) -> tuple[Path, os.stat_result]:
    try:
        candidate = Path(file_path)
        metadata = candidate.lstat()
        if not stat.S_ISREG(metadata.st_mode):
            raise MediaProbeError()
        resolved = candidate.resolve(strict=True)
    except MediaValidationError:
        raise
    except (OSError, TypeError, ValueError):
        raise MediaProbeError() from None
    if metadata.st_size <= 0:
        raise EmptyMediaError()
    if metadata.st_size > max_size_bytes:
        raise MediaFileTooLargeError(max_size_bytes, metadata.st_size)
    return resolved, metadata


def _ensure_file_unchanged(path: Path, before: os.stat_result) -> None:
    try:
        after = path.lstat()
    except OSError:
        raise MediaFileChangedError() from None
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if not stat.S_ISREG(after.st_mode) or any(
        getattr(before, field) != getattr(after, field) for field in stable_fields
    ):
        raise MediaFileChangedError()


def _build_ffprobe_command(executable: str, path: Path) -> list[str]:
    entries = (
        "format=format_name,duration:"
        "stream=index,codec_type,codec_name,sample_rate,channels,duration:"
        "stream_disposition=default,attached_pic"
    )
    return [
        executable,
        "-v",
        "error",
        "-hide_banner",
        "-protocol_whitelist",
        "file,pipe",
        "-probesize",
        str(_MAX_PROBE_SIZE_BYTES),
        "-analyzeduration",
        str(_MAX_ANALYZE_DURATION_MICROSECONDS),
        "-max_alloc",
        str(_MAX_FFPROBE_ALLOCATION_BYTES),
        "-show_entries",
        entries,
        "-of",
        "json",
        "-i",
        str(path),
    ]


def _build_ffmpeg_decode_command(executable: str, path: Path, seconds: float) -> list[str]:
    return [
        executable,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-nostats",
        "-xerror",
        "-threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
        "-probesize",
        str(_MAX_PROBE_SIZE_BYTES),
        "-analyzeduration",
        str(_MAX_ANALYZE_DURATION_MICROSECONDS),
        "-max_alloc",
        str(_MAX_FFPROBE_ALLOCATION_BYTES),
        "-i",
        str(path),
        "-t",
        f"{seconds:g}",
        "-map",
        "0:a:0",
        "-vn",
        "-f",
        "null",
        "-",
    ]


def _load_probe_payload(stdout: str) -> Mapping[str, Any]:
    if not isinstance(stdout, str) or not stdout.strip():
        raise MalformedProbeOutputError()
    if len(stdout.encode("utf-8")) > _MAX_PROBE_OUTPUT_BYTES:
        raise MalformedProbeOutputError()

    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        payload = json.loads(stdout, parse_constant=reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise MalformedProbeOutputError() from None
    if not isinstance(payload, dict):
        raise MalformedProbeOutputError()
    return payload


def _metadata_from_probe(
    payload: Mapping[str, Any],
    *,
    original_filename: str,
    extension: str,
    declared_mime: str,
    size_bytes: int,
    policy: MediaValidationPolicy,
) -> MediaMetadata:
    format_payload = payload.get("format")
    streams_payload = payload.get("streams")
    if not isinstance(format_payload, dict) or not isinstance(streams_payload, list):
        raise MalformedProbeOutputError()
    if not streams_payload or len(streams_payload) > policy.max_streams:
        raise MalformedProbeOutputError()

    format_names = _parse_format_names(format_payload.get("format_name"))
    profile = _FORMAT_PROFILES[extension]
    if not format_names.intersection(profile.format_names):
        known_format_names = set().union(*(item.format_names for item in _FORMAT_PROFILES.values()))
        if format_names.intersection(known_format_names):
            raise ExtensionContentMismatchError()
        raise UnsupportedContainerError()

    audio_streams: list[AudioStreamMetadata] = []
    video_streams: list[VideoStreamMetadata] = []
    duration_candidates = [_parse_duration(format_payload.get("duration"))]
    seen_indexes: set[int] = set()
    for stream in streams_payload:
        if not isinstance(stream, dict):
            raise MalformedProbeOutputError()
        index = _parse_nonnegative_integer(stream.get("index"))
        if index in seen_indexes:
            raise MalformedProbeOutputError()
        seen_indexes.add(index)
        duration_candidates.append(_parse_duration(stream.get("duration")))
        stream_type = stream.get("codec_type")
        codec = _parse_codec(stream.get("codec_name"))
        disposition = stream.get("disposition", {})
        if not isinstance(disposition, dict):
            raise MalformedProbeOutputError()

        if stream_type == "audio":
            if not _codec_allowed(codec, profile.audio_codecs, profile.audio_codec_prefixes):
                raise UnsupportedCodecError("audio")
            channels = _parse_positive_integer(stream.get("channels"))
            sample_rate = _parse_positive_integer(stream.get("sample_rate"))
            if (
                channels > policy.max_channels
                or sample_rate < policy.min_sample_rate_hz
                or sample_rate > policy.max_sample_rate_hz
            ):
                raise InvalidAudioStreamError()
            audio_streams.append(
                AudioStreamMetadata(
                    index=index,
                    codec=codec,
                    channels=channels,
                    sample_rate_hz=sample_rate,
                )
            )
        elif stream_type == "video":
            if _is_attached_picture(disposition):
                continue
            if profile.forbid_video:
                raise ExtensionContentMismatchError()
            if codec not in profile.video_codecs:
                raise UnsupportedCodecError("video")
            video_streams.append(VideoStreamMetadata(index=index, codec=codec))
        else:
            raise UnsupportedCodecError("stream")

    if not audio_streams:
        raise MissingAudioStreamError()
    duration = max((value for value in duration_candidates if value is not None), default=0.0)
    if duration <= 0:
        raise InvalidMediaDurationError()
    if duration > policy.max_duration_seconds:
        raise MediaDurationExceededError(policy.max_duration_seconds, duration)
    if (
        policy.min_bytes_per_second > 0
        and not profile.byte_exact_duration
        and size_bytes / duration < policy.min_bytes_per_second
    ):
        raise MediaDurationImplausibleError()

    has_video = bool(video_streams)
    accepted_mime_types = profile.video_mime_types if has_video else profile.audio_mime_types
    if declared_mime not in accepted_mime_types:
        raise MimeTypeMismatchError()
    canonical_mime = profile.canonical_video_mime if has_video else profile.canonical_audio_mime
    if not canonical_mime:
        raise MalformedProbeOutputError()

    return MediaMetadata(
        original_filename=original_filename,
        extension=extension,
        container=profile.container,
        detected_format_names=tuple(sorted(format_names)),
        mime_type=canonical_mime,
        duration_seconds=duration,
        size_bytes=size_bytes,
        audio_streams=tuple(audio_streams),
        video_streams=tuple(video_streams),
    )


def _parse_format_names(value: Any) -> frozenset[str]:
    if not isinstance(value, str):
        raise MalformedProbeOutputError()
    names = frozenset(part.strip().lower() for part in value.split(",") if part.strip())
    if not names or any(not _FORMAT_RE.fullmatch(name) for name in names):
        raise MalformedProbeOutputError()
    return names


def _parse_codec(value: Any) -> str:
    if not isinstance(value, str):
        raise MalformedProbeOutputError()
    codec = value.strip().lower()
    if not _CODEC_RE.fullmatch(codec):
        raise MalformedProbeOutputError()
    return codec


def _parse_duration(value: Any) -> float | None:
    if value is None or value == "" or value == "N/A":
        return None
    if isinstance(value, bool):
        raise MalformedProbeOutputError()
    try:
        duration = float(value)
    except (TypeError, ValueError):
        raise MalformedProbeOutputError() from None
    if not math.isfinite(duration) or duration < 0:
        raise MalformedProbeOutputError()
    return duration


def _parse_nonnegative_integer(value: Any) -> int:
    number = _parse_integer(value)
    if number < 0:
        raise MalformedProbeOutputError()
    return number


def _parse_positive_integer(value: Any) -> int:
    number = _parse_integer(value)
    if number <= 0:
        raise InvalidAudioStreamError()
    return number


def _parse_integer(value: Any) -> int:
    if isinstance(value, bool):
        raise MalformedProbeOutputError()
    try:
        text = str(value)
        number = int(text)
    except (TypeError, ValueError):
        raise MalformedProbeOutputError() from None
    if str(number) != text.strip():
        raise MalformedProbeOutputError()
    return number


def _codec_allowed(codec: str, allowed: frozenset[str], prefixes: tuple[str, ...]) -> bool:
    return codec in allowed or any(codec.startswith(prefix) for prefix in prefixes)


def _is_attached_picture(disposition: Mapping[str, Any]) -> bool:
    value = disposition.get("attached_pic", 0)
    return value in {1, "1"}
