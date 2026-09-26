"""WP05-T02: media validation against real ffprobe/ffmpeg with generated fixtures.

Supported codec fixtures pass; forged, corrupted, non-media and implausible
inputs fail with stable codes, and the bounded decode catches corruption a
header-only probe accepts.  Fixtures are synthesised with ffmpeg's ``lavfi``
sources so no media file is committed.  Skipped when the binaries are absent.
"""

from __future__ import annotations

import random
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from backend.media import (
    ExtensionContentMismatchError,
    InvalidAudioStreamError,
    MalformedMediaError,
    MediaDecodeError,
    MediaDecoderUnavailableError,
    MediaDecodeTimeoutError,
    MediaDurationImplausibleError,
    MediaValidationPolicy,
    MimeTypeMismatchError,
    UnsupportedContainerError,
    validate_media_upload,
)

pytestmark = pytest.mark.ffmpeg

SINE = "sine=frequency=440:sample_rate=16000:duration=3"
DECODING = MediaValidationPolicy(decode_probe_seconds=10)
HEADER_ONLY = MediaValidationPolicy(decode_probe_seconds=0)


def _ffmpeg(*arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, test-only fixture generation
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error", *arguments],  # noqa: S607
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def _corrupt_middle(source: Path, target: Path) -> None:
    data = source.read_bytes()
    third = len(data) // 3
    target.write_bytes(data[:third] + bytes(third) + data[2 * third :])


def build_media_fixtures(root: Path) -> Path:
    """Synthesise every fixture below ``root`` (shared with the WP05-T06 API tests)."""
    with wave.open(str(root / "ok.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(bytes(2 * 16_000 * 3))
    _ffmpeg("-f", "lavfi", "-i", SINE, "-c:a", "libmp3lame", "-b:a", "32k", str(root / "ok.mp3"))
    _ffmpeg("-f", "lavfi", "-i", SINE, "-c:a", "libopus", "-b:a", "12k", str(root / "ok.ogg"))
    _ffmpeg("-f", "lavfi", "-i", SINE, "-c:a", "aac", "-b:a", "32k", str(root / "ok.m4a"))
    _ffmpeg("-f", "lavfi", "-i", SINE, "-c:a", "flac", str(root / "ok.flac"))
    _ffmpeg(
        "-f", "lavfi", "-i", SINE, "-c:a", "aac", "-b:a", "32k", "-f", "adts", str(root / "ok.aac")
    )
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        SINE,
        "-c:a",
        "libopus",
        "-b:a",
        "12k",
        "-f",
        "webm",
        str(root / "ok.webm"),
    )
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=size=64x64:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=1",
        "-c:v", "mpeg4", "-c:a", "aac", "-b:a", "32k", "-shortest", str(root / "ok.mp4"),
    )  # fmt: skip
    (root / "forged.mp3").write_bytes((root / "ok.wav").read_bytes())
    (root / "forged.flac").write_bytes((root / "ok.ogg").read_bytes())
    _corrupt_middle(root / "ok.mp3", root / "corrupt.mp3")
    _corrupt_middle(root / "ok.flac", root / "corrupt.flac")
    (root / "tail.mp3").write_bytes((root / "ok.mp3").read_bytes() + b"garbage" * 2000)
    generator = random.Random(7)  # noqa: S311 - deterministic garbage, not security
    (root / "garbage.mp3").write_bytes(bytes(generator.getrandbits(8) for _ in range(20_000)))
    (root / "text.mp3").write_text("hello world\n" * 100)
    (root / "doc.mp3").write_bytes(b"%PDF-1.4\n" + b"x" * 1000)
    (root / "program.wav").write_bytes(b"MZ" + bytes(1000))
    (root / "archive.m4a").write_bytes(b"PK\x03\x04" + bytes(1000))
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + 16_000 * 2 * 3600)
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, 16_000, 32_000, 2, 16)
        + b"data"
        + struct.pack("<I", 16_000 * 2 * 3600)
    )
    (root / "claims-hour.wav").write_bytes(header + bytes(100))
    # WP05-T06 adversarial shapes: a truncated MP4 (the moov atom never
    # arrives), an ID3 tag claiming ~268 MB, sixteen channels, 384 kHz, and a
    # WAV polyglot with markup appended after the data chunk.
    m4a = (root / "ok.m4a").read_bytes()
    (root / "truncated.m4a").write_bytes(m4a[: int(len(m4a) * 0.6)])
    (root / "huge-id3.mp3").write_bytes(
        b"ID3\x04\x00\x00" + bytes([0x7F, 0x7F, 0x7F, 0x7F]) + (root / "ok.mp3").read_bytes()
    )
    with wave.open(str(root / "wide.wav"), "wb") as handle:
        handle.setnchannels(16)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(bytes(2 * 16 * 16_000))
    with wave.open(str(root / "fast.wav"), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(384_000)
        handle.writeframes(bytes(2 * 384_000))
    (root / "polyglot.wav").write_bytes(
        (root / "ok.wav").read_bytes() + b"<html><script>alert(1)</script></html>" * 50
    )
    return root


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_media_fixtures(tmp_path_factory.mktemp("media"))


@pytest.mark.parametrize(
    ("name", "mime", "container", "codec"),
    [
        ("ok.wav", "audio/wav", "wav", "pcm_s16le"),
        ("ok.mp3", "audio/mpeg", "mp3", "mp3"),
        ("ok.ogg", "audio/ogg", "ogg", "opus"),
        ("ok.m4a", "audio/mp4", "mp4", "aac"),
        ("ok.flac", "audio/flac", "flac", "flac"),
        ("ok.aac", "audio/aac", "aac", "aac"),
        ("ok.webm", "audio/webm", "webm", "opus"),
        ("ok.mp4", "video/mp4", "mp4", "aac"),
    ],
)
def test_supported_fixtures_pass_probe_and_decode(
    fixtures: Path, name: str, mime: str, container: str, codec: str
) -> None:
    result = validate_media_upload(
        fixtures / name, original_filename=name, declared_mime_type=mime, policy=DECODING
    )
    assert result.container == container
    assert result.audio_codec == codec
    assert result.mime_type == mime
    assert 0.9 <= result.duration_seconds <= 3.2
    # Opus is always signalled at 48 kHz whatever the source rate.
    assert result.sample_rate_hz == (48_000 if codec == "opus" else 16_000)
    if name == "ok.mp4":
        assert result.video_codec == "mpeg4"
    else:
        assert result.video_codec is None


def test_forged_extension_is_refused_before_any_probe(fixtures: Path) -> None:
    # A wrong ffprobe path would surface as "unavailable" if the probe ran.
    for name, mime in (("forged.mp3", "audio/mpeg"), ("forged.flac", "audio/flac")):
        with pytest.raises(ExtensionContentMismatchError) as error:
            validate_media_upload(
                fixtures / name,
                original_filename=name,
                declared_mime_type=mime,
                ffprobe_path="/nonexistent/ffprobe",
            )
        assert error.value.code == "extension_content_mismatch" and error.value.terminal


@pytest.mark.parametrize("name", ["doc.mp3", "program.wav", "archive.m4a"])
def test_non_media_bytes_are_refused_before_any_probe(fixtures: Path, name: str) -> None:
    with pytest.raises(UnsupportedContainerError) as error:
        validate_media_upload(
            fixtures / name,
            original_filename=name,
            declared_mime_type="audio/mpeg",
            ffprobe_path="/nonexistent/ffprobe",
        )
    assert error.value.terminal


@pytest.mark.parametrize("name", ["garbage.mp3", "text.mp3"])
def test_unrecognised_bytes_fail_the_probe(fixtures: Path, name: str) -> None:
    with pytest.raises(MalformedMediaError) as error:
        validate_media_upload(
            fixtures / name, original_filename=name, declared_mime_type="audio/mpeg"
        )
    assert error.value.code == "malformed_media" and error.value.terminal


@pytest.mark.parametrize(
    ("name", "mime"),
    [("corrupt.mp3", "audio/mpeg"), ("corrupt.flac", "audio/flac"), ("tail.mp3", "audio/mpeg")],
)
def test_bounded_decode_catches_corruption_the_probe_accepts(
    fixtures: Path, name: str, mime: str
) -> None:
    # Header-only inspection accepts these bytes...
    accepted = validate_media_upload(
        fixtures / name, original_filename=name, declared_mime_type=mime, policy=HEADER_ONLY
    )
    assert accepted.duration_seconds > 0
    # ...the decode of a prefix does not.
    with pytest.raises(MediaDecodeError) as error:
        validate_media_upload(
            fixtures / name, original_filename=name, declared_mime_type=mime, policy=DECODING
        )
    assert error.value.code == "media_decode_failed" and error.value.terminal


def test_wav_header_claims_are_clamped_and_the_truncation_fails_to_decode(
    fixtures: Path,
) -> None:
    # Header-only: the demuxer derives the duration from the bytes present, so
    # a header claiming an hour cannot inflate the charge (and PCM is exempt
    # from the plausibility floor).
    result = validate_media_upload(
        fixtures / "claims-hour.wav",
        original_filename="claims-hour.wav",
        declared_mime_type="audio/wav",
        policy=MediaValidationPolicy(min_bytes_per_second=100_000, decode_probe_seconds=0),
    )
    assert result.duration_seconds < 1
    # With the decode: the truncated data chunk is a decoding error.
    with pytest.raises(MediaDecodeError):
        validate_media_upload(
            fixtures / "claims-hour.wav",
            original_filename="claims-hour.wav",
            declared_mime_type="audio/wav",
            policy=DECODING,
        )


def test_plausibility_floor_refuses_compressed_audio_claiming_too_much(fixtures: Path) -> None:
    with pytest.raises(MediaDurationImplausibleError) as error:
        validate_media_upload(
            fixtures / "ok.ogg",
            original_filename="ok.ogg",
            declared_mime_type="audio/ogg",
            policy=MediaValidationPolicy(min_bytes_per_second=100_000),
        )
    assert error.value.code == "media_duration_implausible" and error.value.terminal


def test_decode_tooling_failures_are_transient(fixtures: Path) -> None:
    with pytest.raises(MediaDecodeTimeoutError) as timeout:
        validate_media_upload(
            fixtures / "ok.mp3",
            original_filename="ok.mp3",
            declared_mime_type="audio/mpeg",
            policy=DECODING,
            ffmpeg_timeout_seconds=0.001,
        )
    assert timeout.value.code == "media_decode_timeout" and not timeout.value.terminal
    with pytest.raises(MediaDecoderUnavailableError) as missing:
        validate_media_upload(
            fixtures / "ok.mp3",
            original_filename="ok.mp3",
            declared_mime_type="audio/mpeg",
            policy=DECODING,
            ffmpeg_path="/nonexistent/ffmpeg",
        )
    assert not missing.value.terminal


def test_declared_type_is_checked_against_the_bytes(fixtures: Path) -> None:
    with pytest.raises(MimeTypeMismatchError):
        validate_media_upload(
            fixtures / "ok.mp3", original_filename="ok.mp3", declared_mime_type="audio/wav"
        )


# --- WP05-T06: adversarial shapes ------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "mime"), [("truncated.m4a", "audio/mp4"), ("huge-id3.mp3", "audio/mpeg")]
)
def test_truncated_container_and_forged_tag_size_are_terminal_malformed(
    fixtures: Path, name: str, mime: str
) -> None:
    before = (fixtures / name).read_bytes()
    with pytest.raises(MalformedMediaError) as error:
        validate_media_upload(
            fixtures / name, original_filename=name, declared_mime_type=mime, policy=DECODING
        )
    assert error.value.code == "malformed_media" and error.value.terminal
    assert (fixtures / name).read_bytes() == before  # the original is never modified


@pytest.mark.parametrize("name", ["wide.wav", "fast.wav"])
def test_channel_and_sample_rate_bounds_refuse_real_headers(fixtures: Path, name: str) -> None:
    with pytest.raises(InvalidAudioStreamError) as error:
        validate_media_upload(
            fixtures / name, original_filename=name, declared_mime_type="audio/wav", policy=DECODING
        )
    assert error.value.code == "invalid_audio_stream" and error.value.terminal


def test_wav_polyglot_is_accepted_as_audio_and_kept_byte_for_byte(fixtures: Path) -> None:
    # Trailing bytes after the data chunk are not audio and do not change the
    # decoded duration; the object is stored exactly as submitted (checksum
    # verified) and only ever served as an attachment, never rendered.
    metadata = validate_media_upload(
        fixtures / "polyglot.wav",
        original_filename="polyglot.wav",
        declared_mime_type="audio/wav",
        policy=DECODING,
    )
    assert metadata.container == "wav" and 2.9 <= metadata.duration_seconds <= 3.1
    assert b"<script>" in (fixtures / "polyglot.wav").read_bytes()
