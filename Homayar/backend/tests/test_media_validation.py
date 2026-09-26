"""Focused tests for byte-derived media validation."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from backend.media import (
    ExtensionContentMismatchError,
    InvalidAudioStreamError,
    InvalidMediaDurationError,
    MalformedMediaError,
    MalformedProbeOutputError,
    MediaDecodeError,
    MediaDecoderUnavailableError,
    MediaDecodeTimeoutError,
    MediaDurationExceededError,
    MediaDurationImplausibleError,
    MediaFileChangedError,
    MediaFileTooLargeError,
    MediaProbeError,
    MediaProbeTimeoutError,
    MediaProbeUnavailableError,
    MediaValidationPolicy,
    MimeTypeMismatchError,
    UnsafeFilenameError,
    UnsupportedCodecError,
    UnsupportedContainerError,
    validate_media_upload,
)


def _probe_payload(
    *,
    format_name: str = "mp3",
    duration: str = "12.5",
    audio_codec: str = "mp3",
    channels: int = 2,
    sample_rate: str = "44100",
    video_codec: str | None = None,
) -> str:
    streams: list[dict[str, object]] = [
        {
            "index": 0,
            "codec_type": "audio",
            "codec_name": audio_codec,
            "sample_rate": sample_rate,
            "channels": channels,
            "duration": duration,
            "disposition": {"default": 1, "attached_pic": 0},
        }
    ]
    if video_codec is not None:
        streams.append(
            {
                "index": 1,
                "codec_type": "video",
                "codec_name": video_codec,
                "duration": duration,
                "disposition": {"default": 1, "attached_pic": 0},
            }
        )
    return json.dumps(
        {
            "format": {"format_name": format_name, "duration": duration},
            "streams": streams,
        }
    )


def _media_file(tmp_path: Path, name: str = "quarantined-upload") -> Path:
    path = tmp_path / name
    # Large enough that the probe's claimed durations stay above the
    # plausibility floor (64 bytes per second by default).
    path.write_bytes(b"media bytes are inspected by mocked ffprobe\n" * 1500)
    return path


def _completed(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


@patch("backend.media.validation.subprocess.run")
def test_validates_audio_metadata_from_probe(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())

    result = validate_media_upload(
        _media_file(tmp_path),
        original_filename="customer-call.mp3",
        declared_mime_type="Audio/MPEG; charset=binary",
    )

    assert result.container == "mp3"
    assert result.mime_type == "audio/mpeg"
    assert result.duration_seconds == 12.5
    assert result.audio_codec == "mp3"
    assert result.channels == 2
    assert result.sample_rate_hz == 44_100
    assert result.video_codec is None


@patch("backend.media.validation.subprocess.run")
def test_validates_video_container_and_codecs(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(
        _probe_payload(
            format_name="mov,mp4,m4a,3gp,3g2,mj2",
            audio_codec="aac",
            sample_rate="48000",
            video_codec="h264",
        )
    )

    result = validate_media_upload(
        _media_file(tmp_path),
        original_filename="meeting.mp4",
        declared_mime_type="video/mp4",
    )

    assert result.container == "mp4"
    assert result.mime_type == "video/mp4"
    assert result.audio_codec == "aac"
    assert result.video_codec == "h264"


@patch("backend.media.validation.subprocess.run")
def test_rejects_extension_and_container_mismatch(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(
        _probe_payload(format_name="wav", audio_codec="pcm_s16le", sample_rate="16000")
    )

    with pytest.raises(ExtensionContentMismatchError) as error:
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="renamed.mp3",
            declared_mime_type="audio/mpeg",
        )

    assert error.value.code == "extension_content_mismatch"


@patch("backend.media.validation.subprocess.run")
def test_rejects_declared_mime_mismatch_after_probe(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())

    with pytest.raises(MimeTypeMismatchError):
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/wav",
        )


@patch("backend.media.validation.subprocess.run")
def test_timeout_is_api_safe_and_does_not_leak_command(run: Mock, tmp_path: Path) -> None:
    secret_path = _media_file(tmp_path, "private-customer-id")
    run.side_effect = subprocess.TimeoutExpired(
        cmd=["ffprobe", str(secret_path)],
        timeout=0.25,
        output="sensitive probe output",
    )

    with pytest.raises(MediaProbeTimeoutError) as error:
        validate_media_upload(
            secret_path,
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
            timeout_seconds=0.25,
        )

    assert error.value.code == "media_probe_timeout"
    assert str(secret_path) not in str(error.value)
    assert "sensitive" not in str(error.value)


@patch("backend.media.validation.subprocess.run")
def test_probe_failure_does_not_leak_stderr(run: Mock, tmp_path: Path) -> None:
    private_path = _media_file(tmp_path, "private-recording")
    run.return_value = _completed(
        "",
        returncode=1,
        stderr=f"failed to read {private_path}: private decoder details",
    )

    with pytest.raises(MalformedMediaError) as error:
        validate_media_upload(
            private_path,
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
        )

    assert str(private_path) not in str(error.value)
    assert "decoder" not in str(error.value)


@pytest.mark.parametrize("stdout", ["not-json", "[]", "{}", '{"format": {}}'])
@patch("backend.media.validation.subprocess.run")
def test_rejects_malformed_probe_output(
    run: Mock,
    tmp_path: Path,
    stdout: str,
) -> None:
    run.return_value = _completed(stdout)

    with pytest.raises(MalformedProbeOutputError):
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
        )


@pytest.mark.parametrize(
    ("duration", "expected_error"),
    [
        ("0", InvalidMediaDurationError),
        ("60.001", MediaDurationExceededError),
    ],
)
@patch("backend.media.validation.subprocess.run")
def test_enforces_positive_and_max_duration(
    run: Mock,
    tmp_path: Path,
    duration: str,
    expected_error: type[Exception],
) -> None:
    run.return_value = _completed(_probe_payload(duration=duration))
    policy = MediaValidationPolicy(max_duration_seconds=60)

    with pytest.raises(expected_error):
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
            policy=policy,
        )


@pytest.mark.parametrize(
    ("channels", "sample_rate"),
    [(0, "48000"), (2, "7999"), (9, "48000"), (2, "192001")],
)
@patch("backend.media.validation.subprocess.run")
def test_rejects_invalid_audio_shape(
    run: Mock,
    tmp_path: Path,
    channels: int,
    sample_rate: str,
) -> None:
    run.return_value = _completed(_probe_payload(channels=channels, sample_rate=sample_rate))

    with pytest.raises(InvalidAudioStreamError):
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
        )


@patch("backend.media.validation.subprocess.run")
def test_rejects_unsupported_codec(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload(audio_codec="aac"))

    with pytest.raises(UnsupportedCodecError):
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
        )


@patch("backend.media.validation.subprocess.run")
def test_rejects_oversized_file_before_probe(run: Mock, tmp_path: Path) -> None:
    policy = MediaValidationPolicy(max_size_bytes=4)

    with pytest.raises(MediaFileTooLargeError) as error:
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
            policy=policy,
        )

    assert error.value.limit_bytes == 4
    run.assert_not_called()


@patch("backend.media.validation.subprocess.run")
def test_rejects_unsafe_original_name_before_probe(run: Mock, tmp_path: Path) -> None:
    with pytest.raises(UnsafeFilenameError):
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="../../call.mp3",
            declared_mime_type="audio/mpeg",
        )

    run.assert_not_called()


@patch("backend.media.validation.subprocess.run")
def test_uses_fixed_argv_without_shell_interpretation(run: Mock, tmp_path: Path) -> None:
    media_path = _media_file(tmp_path, "-i;touch injected.mp3")
    run.return_value = _completed(_probe_payload())
    executable = "ffprobe --help; touch should-not-run"

    validate_media_upload(
        media_path,
        original_filename="safe.mp3",
        declared_mime_type="audio/mpeg",
        ffprobe_path=executable,
    )

    command = run.call_args.args[0]
    assert command[0] == executable
    assert command[-2] == "-i"
    assert command[-1] == str(media_path.resolve())
    assert command.count(str(media_path.resolve())) == 1
    assert run.call_args.kwargs["shell"] is False
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert run.call_args.kwargs["stderr"] is subprocess.DEVNULL
    assert run.call_args.kwargs["timeout"] == 10.0


# --- WP05-T02: signature pre-check, plausibility floor, bounded decode --------------


@pytest.mark.parametrize(
    ("head", "name"),
    [
        (b"%PDF-1.7 " + b"x" * 60, "call.mp3"),
        (b"MZ" + bytes(60), "call.wav"),
        (b"PK\x03\x04" + bytes(60), "call.m4a"),
        (b"\x7fELF" + bytes(60), "call.flac"),
        (b"#!/bin/sh\n" + b"x" * 60, "call.ogg"),
    ],
)
@patch("backend.media.validation.subprocess.run")
def test_non_media_signatures_are_refused_without_spawning(
    run: Mock, tmp_path: Path, head: bytes, name: str
) -> None:
    path = tmp_path / "quarantined"
    path.write_bytes(head)

    with pytest.raises(UnsupportedContainerError) as error:
        validate_media_upload(path, original_filename=name, declared_mime_type="audio/mpeg")

    assert error.value.terminal is True
    run.assert_not_called()


@pytest.mark.parametrize(
    ("head", "name"),
    [
        (b"RIFF\x24\x00\x00\x00WAVEfmt " + bytes(48), "renamed.mp3"),
        (b"OggS" + bytes(60), "renamed.flac"),
        (b"fLaC" + bytes(60), "renamed.ogg"),
        (b"\x00\x00\x00\x18ftypM4A " + bytes(52), "renamed.webm"),
        (b"\x1aE\xdf\xa3" + bytes(60), "renamed.m4a"),
        (b"ID3\x04\x00" + bytes(59), "renamed.wav"),
    ],
)
@patch("backend.media.validation.subprocess.run")
def test_foreign_family_signatures_are_refused_without_spawning(
    run: Mock, tmp_path: Path, head: bytes, name: str
) -> None:
    path = tmp_path / "quarantined"
    path.write_bytes(head)

    with pytest.raises(ExtensionContentMismatchError):
        validate_media_upload(path, original_filename=name, declared_mime_type="audio/mpeg")

    run.assert_not_called()


@patch("backend.media.validation.subprocess.run")
def test_matching_and_unknown_signatures_defer_to_the_probe(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    for head in (
        b"ID3\x04\x00" + bytes(59),
        b"\xff\xfb\x90\x00" + bytes(60),
        b"no signature at all",
    ):
        path = tmp_path / "quarantined"
        path.write_bytes(head + bytes(4096))  # padded above the plausibility floor
        result = validate_media_upload(
            path, original_filename="call.mp3", declared_mime_type="audio/mpeg"
        )
        assert result.container == "mp3"
    assert run.call_count == 3


@patch("backend.media.validation.subprocess.run")
def test_plausibility_floor_applies_to_compressed_audio_only(run: Mock, tmp_path: Path) -> None:
    path = _media_file(tmp_path)  # 66 KB: 18 bytes per claimed second below
    run.return_value = _completed(_probe_payload(duration="3600"))
    with pytest.raises(MediaDurationImplausibleError) as error:
        validate_media_upload(path, original_filename="call.mp3", declared_mime_type="audio/mpeg")
    assert error.value.code == "media_duration_implausible" and error.value.terminal

    # Disabled floor: accepted.
    result = validate_media_upload(
        path,
        original_filename="call.mp3",
        declared_mime_type="audio/mpeg",
        policy=MediaValidationPolicy(min_bytes_per_second=0),
    )
    assert result.duration_seconds == 3600

    # PCM containers derive their duration from the bytes: never implausible here.
    run.return_value = _completed(
        _probe_payload(format_name="wav", audio_codec="pcm_s16le", duration="3600")
    )
    result = validate_media_upload(
        path,
        original_filename="call.wav",
        declared_mime_type="audio/wav",
        policy=MediaValidationPolicy(min_bytes_per_second=100_000),
    )
    assert result.container == "wav"


@patch("backend.media.validation.subprocess.run")
def test_decode_probe_runs_after_the_probe_with_fixed_argv(run: Mock, tmp_path: Path) -> None:
    media_path = _media_file(tmp_path)
    run.side_effect = [_completed(_probe_payload()), _completed("", returncode=0)]

    result = validate_media_upload(
        media_path,
        original_filename="call.mp3",
        declared_mime_type="audio/mpeg",
        policy=MediaValidationPolicy(decode_probe_seconds=15),
        ffmpeg_path="ffmpeg --help; touch should-not-run",
        ffmpeg_timeout_seconds=7.5,
    )

    assert result.container == "mp3"
    assert run.call_count == 2
    decode = run.call_args_list[1]
    command = decode.args[0]
    assert command[0] == "ffmpeg --help; touch should-not-run"
    assert command[-1] == "-" and command[-3:] == ["-f", "null", "-"]
    assert command[command.index("-t") + 1] == "15"
    assert command[command.index("-threads") + 1] == "1"
    assert "-xerror" in command and "-nostdin" in command and "-vn" in command
    assert command[command.index("-map") + 1] == "0:a:0"
    assert command.count(str(media_path.resolve())) == 1
    assert decode.kwargs["shell"] is False
    assert decode.kwargs["stdin"] is subprocess.DEVNULL
    assert decode.kwargs["stdout"] is subprocess.DEVNULL
    # Diagnostics go to a temporary file (presence checked, never read), not a pipe.
    assert hasattr(decode.kwargs["stderr"], "fileno")
    assert "-nostats" in command and command[command.index("-v") + 1] == "error"
    assert decode.kwargs["timeout"] == 7.5


@patch("backend.media.validation.subprocess.run")
def test_decode_probe_is_skipped_when_disabled(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    validate_media_upload(
        _media_file(tmp_path), original_filename="call.mp3", declared_mime_type="audio/mpeg"
    )
    assert run.call_count == 1  # policy default: no decode


@pytest.mark.parametrize(
    ("second_call", "expected", "terminal"),
    [
        (_completed("", returncode=1), MediaDecodeError, True),
        (subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=1.0), MediaDecodeTimeoutError, False),
        (FileNotFoundError(), MediaDecoderUnavailableError, False),
    ],
)
@patch("backend.media.validation.subprocess.run")
def test_decode_outcomes_are_classified(
    run: Mock, tmp_path: Path, second_call: object, expected: type[Exception], terminal: bool
) -> None:
    run.side_effect = [_completed(_probe_payload()), second_call]

    with pytest.raises(expected) as error:
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
            policy=MediaValidationPolicy(decode_probe_seconds=5),
        )

    assert error.value.terminal is terminal


@patch("backend.media.validation.subprocess.run")
def test_decode_diagnostics_refuse_even_when_ffmpeg_exits_zero(run: Mock, tmp_path: Path) -> None:
    # ffmpeg 6.1.1 (Ubuntu 24.04) conceals some decode errors and exits 0.
    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if run.call_count == 1:
            return _completed(_probe_payload())
        # Like ffmpeg, write straight to the descriptor (no Python buffering).
        os.write(kwargs["stderr"].fileno(), b"[mp3float] invalid new backstep -1\n")  # type: ignore[attr-defined]
        return _completed("", returncode=0)

    run.side_effect = fake_run
    with pytest.raises(MediaDecodeError) as error:
        validate_media_upload(
            _media_file(tmp_path),
            original_filename="call.mp3",
            declared_mime_type="audio/mpeg",
            policy=MediaValidationPolicy(decode_probe_seconds=5),
        )
    assert error.value.terminal
    assert "backstep" not in str(error.value) and "backstep" not in error.value.code


def test_policy_bounds_for_the_new_limits() -> None:
    with pytest.raises(ValueError, match="decode_probe_seconds"):
        MediaValidationPolicy(decode_probe_seconds=601)
    with pytest.raises(ValueError, match="decode_probe_seconds"):
        MediaValidationPolicy(decode_probe_seconds=-1)
    with pytest.raises(ValueError, match="min_bytes_per_second"):
        MediaValidationPolicy(min_bytes_per_second=-1)


def test_refusal_classes_are_terminal_unless_the_tooling_failed() -> None:
    transient = {
        MediaProbeError,
        MediaProbeUnavailableError,
        MediaProbeTimeoutError,
        MalformedProbeOutputError,
        MediaFileChangedError,
        MediaDecodeTimeoutError,
        MediaDecoderUnavailableError,
    }
    for cls in transient:
        assert cls.terminal is False, cls
    for cls in (
        MalformedMediaError,
        MediaDecodeError,
        MediaDurationImplausibleError,
        MediaDurationExceededError,
        ExtensionContentMismatchError,
        UnsupportedContainerError,
        UnsupportedCodecError,
        MimeTypeMismatchError,
        UnsafeFilenameError,
        MediaFileTooLargeError,
    ):
        assert cls.terminal is True, cls
