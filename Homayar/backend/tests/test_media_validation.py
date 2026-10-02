"""Focused tests for byte-derived media validation."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
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


# The bounded decode runs a real child process: these tests use a stand-in
# decoder (a script) to control what it writes and how long it runs, and
# observe what validation does with it. Real ffmpeg behaviour on real media is
# covered by the ``ffmpeg``-marked tests in test_media_fixtures.py.
POSIX_SCRIPTS = pytest.mark.skipif(
    os.name != "posix", reason="the stand-in decoder is a POSIX shebang script"
)


def _fake_decoder(tmp_path: Path, body: str, name: str = "fake-ffmpeg") -> Path:
    """An executable standing in for ffmpeg; it records its pid first.

    The pid is written by a POSIX shell prologue before the Python body starts:
    ``exec`` keeps the same pid, and the shell starts in milliseconds, so a
    decoder that validation kills during a slow interpreter start-up (a loaded
    host) is still identifiable. Writing it from Python raced the kill deadline.
    """
    script = tmp_path / name
    body_path = tmp_path / f"{name}-body.py"
    body_path.write_text(
        # argv[0] is the decoder path again, as ffmpeg would see it.
        "import json, os, sys, time\nsys.argv = sys.argv[1:]\n" + textwrap.dedent(body),
        encoding="utf-8",
    )
    prologue = (
        "#!/bin/sh",
        'printf %s "$$" > "$0.pid"',
        f'exec "{sys.executable}" "{body_path}" "$0" "$@"',
    )
    script.write_text("\n".join(prologue) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return script


def _reaped(decoder: Path) -> bool:
    """The stand-in's process no longer exists (killed or exited, and reaped)."""
    pid = int(Path(f"{decoder}.pid").read_text(encoding="utf-8"))
    try:
        os.kill(pid, 0)  # a zombie still exists: only a reaped process is gone
    except ProcessLookupError:
        return True
    return False


def _validate_with(decoder: str | Path, tmp_path: Path, *, timeout: float = 20.0):
    return validate_media_upload(
        _media_file(tmp_path),
        original_filename="call.mp3",
        declared_mime_type="audio/mpeg",
        policy=MediaValidationPolicy(decode_probe_seconds=15),
        ffmpeg_path=str(decoder),
        ffmpeg_timeout_seconds=timeout,
    )


@POSIX_SCRIPTS
@patch("backend.media.validation.subprocess.run")
def test_decode_probe_runs_after_the_probe_with_fixed_argv(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    # Shell metacharacters in the configured path are part of a file name.
    decoder = _fake_decoder(
        tmp_path,
        """
        devnull = os.stat(os.devnull)
        with open(sys.argv[0] + '.argv', 'w') as handle:
            json.dump({
                'argv': sys.argv[1:],
                'stdin_devnull': os.path.samestat(os.fstat(0), devnull),
                'stdout_devnull': os.path.samestat(os.fstat(1), devnull),
            }, handle)
        """,
        name="ffmpeg --help; touch should-not-run",
    )

    result = _validate_with(decoder, tmp_path)

    assert result.container == "mp3"
    assert run.call_count == 1  # the probe; the decode is its own child process
    seen = json.loads(Path(f"{decoder}.argv").read_text(encoding="utf-8"))
    command = seen["argv"]
    media_path = str((tmp_path / "quarantined-upload").resolve())
    assert command[-3:] == ["-f", "null", "-"]
    assert command[command.index("-t") + 1] == "15"
    assert command[command.index("-threads") + 1] == "1"
    assert command[command.index("-v") + 1] == "error"
    assert command[command.index("-map") + 1] == "0:a:0"
    for flag in ("-xerror", "-nostdin", "-nostats", "-vn", "-hide_banner"):
        assert flag in command
    assert command.count(media_path) == 1
    assert seen["stdin_devnull"] and seen["stdout_devnull"]
    assert not (tmp_path / "should-not-run").exists() and not Path("should-not-run").exists()
    assert _reaped(decoder)


@patch("backend.media.validation.subprocess.run")
def test_decode_probe_is_skipped_when_disabled(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    validate_media_upload(
        _media_file(tmp_path), original_filename="call.mp3", declared_mime_type="audio/mpeg"
    )
    assert run.call_count == 1  # policy default: no decode


@POSIX_SCRIPTS
@pytest.mark.parametrize(
    ("body", "timeout", "expected", "terminal"),
    [
        ("sys.exit(1)\n", 20.0, MediaDecodeError, True),
        # ffmpeg 6.1.1 conceals some decode errors and exits 0: its diagnostic still refuses.
        ("os.write(2, b'[mp3float] invalid new backstep -1\\n')\nsys.exit(0)\n", 20.0,
         MediaDecodeError, True),
        ("time.sleep(60)\n", 2.0, MediaDecodeTimeoutError, False),
    ],
    ids=["nonzero-exit", "exit-zero-with-diagnostic", "silent-past-timeout"],
)  # fmt: skip
@patch("backend.media.validation.subprocess.run")
def test_decode_outcomes_are_classified(
    run: Mock,
    tmp_path: Path,
    body: str,
    timeout: float,
    expected: type[Exception],
    terminal: bool,
) -> None:
    run.return_value = _completed(_probe_payload())
    decoder = _fake_decoder(tmp_path, body)
    started = time.monotonic()

    with pytest.raises(expected) as error:
        _validate_with(decoder, tmp_path, timeout=timeout)

    assert error.value.terminal is terminal
    assert time.monotonic() - started < timeout + 10
    assert _reaped(decoder)


@patch("backend.media.validation.subprocess.run")
def test_missing_decoder_is_transient(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    with pytest.raises(MediaDecoderUnavailableError) as error:
        _validate_with(tmp_path / "no-such-ffmpeg", tmp_path)
    assert not error.value.terminal


@POSIX_SCRIPTS
@patch("backend.media.validation.subprocess.run")
def test_silent_decoder_output_on_stdout_is_not_a_diagnostic(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    decoder = _fake_decoder(tmp_path, "os.write(1, b'x' * (1 << 20))\nsys.exit(0)\n")
    assert _validate_with(decoder, tmp_path).container == "mp3"
    assert _reaped(decoder)


@POSIX_SCRIPTS
@patch("backend.media.validation.subprocess.run")
def test_decode_diagnostics_do_not_leak(
    run: Mock, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run.return_value = _completed(_probe_payload())
    decoder = _fake_decoder(
        tmp_path,
        "media = sys.argv[sys.argv.index('-i') + 1]\n"
        "os.write(2, ('failed to read ' + media + ': private-marker\\n').encode())\n"
        "sys.exit(0)\n",
    )
    with pytest.raises(MediaDecodeError) as error:
        _validate_with(decoder, tmp_path)
    shown = f"{error.value} {error.value.code} {error.value!r}"
    assert "private-marker" not in shown and "quarantined-upload" not in shown
    assert "private-marker" not in caplog.text


@POSIX_SCRIPTS
@patch("backend.media.validation.subprocess.run")
def test_diagnostic_flood_is_cut_off_at_the_first_byte(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    # Writes diagnostics forever and records how much it managed to write.
    decoder = _fake_decoder(
        tmp_path,
        """
        chunk, written = b'E' * 4096, 0
        while True:
            os.write(2, chunk)
            written += len(chunk)
            with open(sys.argv[0] + '.written.tmp', 'w') as handle:
                handle.write(str(written))
            os.replace(sys.argv[0] + '.written.tmp', sys.argv[0] + '.written')
        """,
    )
    started = time.monotonic()

    with pytest.raises(MediaDecodeError) as error:
        _validate_with(decoder, tmp_path, timeout=30.0)

    # Refused at the first diagnostic, long before the timeout; the decoder
    # was stopped rather than left to fill memory, a file or the timeout.
    assert error.value.terminal
    assert time.monotonic() - started < 10
    assert _reaped(decoder)
    counter = Path(f"{decoder}.written")
    written = int(counter.read_text(encoding="utf-8")) if counter.exists() else 0
    # At most the pipe's kernel buffer (64 KiB on Linux and macOS) was ever
    # accepted from it; the generous bound only guards against unbounded growth.
    assert written <= 1024 * 1024


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="needs POSIX interval timers")
@POSIX_SCRIPTS
@patch("backend.media.validation.subprocess.run")
def test_decoder_is_stopped_when_the_wait_is_interrupted(run: Mock, tmp_path: Path) -> None:
    run.return_value = _completed(_probe_payload())
    decoder = _fake_decoder(tmp_path, "time.sleep(60)\n")

    class Cancelled(BaseException):
        pass

    def cancel(_signum: int, _frame: object) -> None:
        raise Cancelled

    previous = signal.signal(signal.SIGALRM, cancel)
    signal.setitimer(signal.ITIMER_REAL, 1.5)
    try:
        with pytest.raises(Cancelled):
            _validate_with(decoder, tmp_path, timeout=30.0)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert _reaped(decoder)


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
