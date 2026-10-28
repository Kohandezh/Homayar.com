"""HOM-38: the channel-preserving derivative, its WAV record and the bounded decode.

The first part is pure: the ffmpeg argv, the WAV header with its ``hmyr``
record chunk and the strict header parser.  The ``ffmpeg`` part synthesises
originals with ``lavfi`` at test time (no committed media) and runs the real
derivation.  Derivative bytes differ between ffmpeg builds, so no test pins
bytes or digests; the tests check sample counts, channel order and where the
activity heuristic finds a burst, mapped back to original time.
"""

from __future__ import annotations

import hashlib
import json
import math
import stat
import struct
import subprocess
import wave
from fractions import Fraction
from pathlib import Path

import pytest

from backend.media import derivatives
from backend.media.activity import FRAME_SAMPLES, activity_record, measure_activity
from backend.media.derivatives import (
    DERIVATIVE_PRODUCER,
    DERIVATIVE_SAMPLE_RATE_HZ,
    RECORD_CHUNK_ID,
    RECORD_MAX_BYTES,
    RESAMPLER,
    DerivativeRecordInvalid,
    DerivativeUnavailable,
    DerivedAudio,
    SourceFacts,
    build_derivative_argv,
    compose_wav_header,
    derivative_record,
    derive_audio,
    parse_derivative_header,
)
from backend.media.time_map import TimeMap

SHA = "a" * 64
MAX_BYTES = 262_144_000


def _facts(
    rate: int = 44_100, channels: int = 2, duration: float | None = 3.0, **extra
) -> SourceFacts:
    values = {
        "media_id": "media-original",
        "sha256": SHA,
        "sample_rate_hz": rate,
        "channel_count": channels,
        "codec": "pcm_s16le",
        "duration_seconds": duration,
    }
    values.update(extra)
    return SourceFacts(**values)


def _record(channels: int = 2, sample_count: int = 3200, rate: int = 44_100) -> dict:
    pcm = bytes(2 * channels * sample_count)
    return derivative_record(
        _facts(rate, channels),
        inspection=None,
        sample_count=sample_count,
        activity=measure_activity(pcm, channels),
    )


# --- the argv (pure) --------------------------------------------------------------------


def test_the_argv_keeps_every_channel_and_pins_the_resampler(tmp_path: Path) -> None:
    source = tmp_path / "in put.wav"
    argv = build_derivative_argv("ffmpeg", source)
    assert argv[0] == "ffmpeg"
    for element in ("-ac", "-t", "-filter_complex", "-lavfi", "-ar"):
        assert element not in argv
    assert "-threads" in argv  # a whole element, not the forbidden "-t"
    filters = [argv[index + 1] for index, element in enumerate(argv) if element == "-af"]
    assert filters == [RESAMPLER]
    for name in ("pan", "amix", "amerge", "loudnorm", "dynaudnorm", "volume"):
        assert name not in RESAMPLER
    assert RESAMPLER.startswith("aresample=osr=16000:resampler=swr:")
    assert argv[argv.index("-i") + 1] == f"file:{source.absolute()}"
    assert argv[argv.index("-map") + 1] == "0:a:0"
    assert argv[argv.index("-protocol_whitelist") + 1] == "file,pipe"
    assert argv[-5:] == ["-c:a", "pcm_s16le", "-f", "s16le", "-"]


# --- the WAV header and its record (pure) -----------------------------------------------


def test_a_composed_header_with_pcm_is_a_wav_python_reads(tmp_path: Path) -> None:
    record = _record(channels=2, sample_count=3200)
    header = compose_wav_header(record, 2, 3200)
    pcm = bytes(range(256)) * 50
    path = tmp_path / "derived.wav"
    path.write_bytes(header + pcm)
    with wave.open(str(path), "rb") as reader:
        assert (reader.getnchannels(), reader.getframerate(), reader.getsampwidth()) == (
            2,
            16000,
            2,
        )
        assert reader.readframes(reader.getnframes()) == pcm
    parsed = parse_derivative_header(header)
    assert parsed.record == record
    assert (parsed.channel_count, parsed.sample_rate_hz) == (2, 16000)
    assert parsed.data_offset == len(header)
    assert parsed.data_size == len(pcm)


def test_the_record_chunk_is_canonical_json_padded_to_even_length() -> None:
    record = _record()
    header = compose_wav_header(record, 2, 3200)
    position = header.index(RECORD_CHUNK_ID)
    (size,) = struct.unpack_from("<I", header, position + 4)
    body = header[position + 8 : position + 8 + size]
    canonical = json.dumps(
        record, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode()
    assert size % 2 == 0
    assert body in (canonical, canonical + b" ")
    assert header[position + 8 + size : position + 12 + size] == b"data"


def test_the_record_shape_is_the_published_one() -> None:
    record = _record(channels=2, sample_count=48_000, rate=11_025)
    assert set(record) == {
        "version",
        "source",
        "format",
        "time_map",
        "inspection_status",
        "activity",
    }
    assert record["version"] == DERIVATIVE_PRODUCER == "audio-derivative/1"
    assert record["source"] == {
        "media_id": "media-original",
        "sha256": SHA,
        "channel_count": 2,
        "sample_rate_hz": 11025,
        "codec": "pcm_s16le",
        "narrowband": True,
    }
    assert record["format"] == {
        "container": "wav",
        "codec": "pcm_s16le",
        "sample_rate_hz": 16000,
        "channel_count": 2,
        "channel_map": [0, 1],
        "gain": "none",
        "resampler": RESAMPLER,
    }
    assert record["time_map"] == TimeMap(11025).as_record(48_000)
    assert record["inspection_status"] == "missing"
    assert "speech" not in json.dumps(record).lower()


@pytest.mark.parametrize(("rate", "narrow"), [(8000, True), (15_999, True), (16_000, False)])
def test_narrowband_means_below_16_khz(rate: int, narrow: bool) -> None:
    assert _record(rate=rate)["source"]["narrowband"] is narrow


def test_the_inspection_status_follows_the_original_record() -> None:
    complete = {"status": "complete", "channels": [], "duplicate_pairs": []}
    unavailable = {"status": "unavailable", "channels": [], "duplicate_pairs": []}
    activity = measure_activity(bytes(2 * 2 * 320), 2)
    for inspection, status in (
        (complete, "complete"),
        (unavailable, "unavailable"),
        (None, "missing"),
    ):
        record = derivative_record(
            _facts(), inspection=inspection, sample_count=320, activity=activity
        )
        assert record["inspection_status"] == status


def _good_header() -> bytes:
    return compose_wav_header(_record(channels=2, sample_count=3200), 2, 3200)


def _chunks(header: bytes) -> list[tuple[bytes, int, int]]:
    """``(id, size, offset of the body)`` of each chunk after ``WAVE``."""
    chunks, offset = [], 12
    while offset + 8 <= len(header):
        chunk_id, size = (
            header[offset : offset + 4],
            struct.unpack_from("<I", header, offset + 4)[0],
        )
        chunks.append((chunk_id, size, offset + 8))
        offset += 8 + size
        if chunk_id == b"data":
            break
    return chunks


def _with_record(record: dict, *, channels: int = 2, sample_count: int = 3200) -> bytes:
    """A header built around ``record`` without the composer's own checks."""
    meta = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    if len(meta) % 2:
        meta += b" "
    fmt = struct.pack("<HHIIHH", 1, channels, 16000, 16000 * 2 * channels, 2 * channels, 16)
    data_size = 2 * channels * sample_count
    body = (
        b"WAVE"
        + b"fmt "
        + struct.pack("<I", 16)
        + fmt
        + RECORD_CHUNK_ID
        + struct.pack("<I", len(meta))
        + meta
        + b"data"
        + struct.pack("<I", data_size)
    )
    return b"RIFF" + struct.pack("<I", len(body) + data_size) + body


def _mutations() -> dict[str, bytes]:
    good = _good_header()
    fmt_body = _chunks(good)[0][2]
    record_size, record_body = _chunks(good)[1][1], _chunks(good)[1][2]
    record = _record(channels=2, sample_count=3200)
    other_channels = json.loads(json.dumps(record))
    other_channels["format"]["channel_count"] = 3
    other_count = json.loads(json.dumps(record))
    other_count["time_map"]["derivative_sample_count"] = 3201
    other_offset = json.loads(json.dumps(record))
    other_offset["time_map"]["derivative_offset_samples"] = 320
    other_version = json.loads(json.dumps(record))
    other_version["version"] = "audio-derivative/2"
    second = RECORD_CHUNK_ID + struct.pack("<I", 2) + b"{}"  # a second record chunk before data
    data_at = good.rindex(b"data")
    return {
        "wrong magic": b"RIFX" + good[4:],
        "not wave": good[:8] + b"AVI " + good[12:],
        "format tag 3": good[:fmt_body] + struct.pack("<H", 3) + good[fmt_body + 2 :],
        "rate 44100": good[: fmt_body + 4] + struct.pack("<I", 44100) + good[fmt_body + 8 :],
        "8 bits": good[: fmt_body + 14] + struct.pack("<H", 8) + good[fmt_body + 16 :],
        "no record": good[:12] + good[12:36] + good[data_at:],
        "second record": good[:data_at] + second + good[data_at:],
        "bad json": good[:record_body] + b"{" * record_size + good[record_body + record_size :],
        "json list": _with_record([]),  # type: ignore[arg-type]
        "channels disagree": _with_record(other_channels),
        "count disagrees with data": _with_record(other_count),
        "offset is not zero": _with_record(other_offset),
        "unknown version": _with_record(other_version),
        "truncated": good[:-4],
        "short": good[:10],
    }


@pytest.mark.parametrize("name", list(_mutations()))
def test_a_malformed_header_is_refused(name: str) -> None:
    with pytest.raises(DerivativeRecordInvalid) as caught:
        parse_derivative_header(_mutations()[name])
    assert caught.value.code == "derivative_record_invalid"
    assert isinstance(caught.value, ValueError)
    assert parse_derivative_header(_good_header()).channel_count == 2  # allow-control


def test_a_record_chunk_over_the_limit_is_refused() -> None:
    good = _good_header()
    position = good.index(RECORD_CHUNK_ID)
    oversized = (
        good[: position + 4] + struct.pack("<I", RECORD_MAX_BYTES + 2) + good[position + 8 :]
    )
    with pytest.raises(DerivativeRecordInvalid):
        parse_derivative_header(oversized)


def test_the_composer_refuses_a_record_over_the_limit(monkeypatch) -> None:
    monkeypatch.setattr(derivatives, "RECORD_MAX_BYTES", 64)
    with pytest.raises(ValueError):
        compose_wav_header(_record(), 2, 3200)


@pytest.mark.parametrize(("channels", "count"), [(0, 10), (9, 10), (2, -1), (True, 10)])
def test_the_composer_checks_its_arguments(channels, count) -> None:
    with pytest.raises(ValueError):
        compose_wav_header(_record(), channels, count)


def test_the_constants_of_the_version() -> None:
    assert DERIVATIVE_SAMPLE_RATE_HZ == 16000
    assert RECORD_CHUNK_ID == b"hmyr"
    assert RECORD_MAX_BYTES == 4 * 1024 * 1024


# --- the size check before any decode (pure) -------------------------------------------


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_a_predicted_size_over_the_cap_runs_no_decoder(settings, tmp_path: Path) -> None:
    marker = tmp_path / "ran"
    spy = _script(tmp_path / "spy-ffmpeg", f"touch {marker}\nexit 1")
    configured = settings.model_copy(update={"ffmpeg_path": str(spy)})
    work = tmp_path / "work"
    work.mkdir()
    # 2 channels, 60 s: 60 * 16000 * 2 * 2 + 4 MiB + 64 bytes.
    needed = 60 * 16000 * 2 * 2 + RECORD_MAX_BYTES + 64
    over = derive_audio(
        tmp_path / "missing.wav",
        _facts(duration=59.2),
        inspection=None,
        work_dir=work,
        settings=configured,
        max_bytes=needed - 1,
    )
    assert over == DerivativeUnavailable(reason="derivative_too_large")
    assert not marker.exists()
    # Allow-control: at the exact size the decoder runs (and fails here).
    at = derive_audio(
        tmp_path / "missing.wav",
        _facts(duration=59.2),
        inspection=None,
        work_dir=work,
        settings=configured,
        max_bytes=needed,
    )
    assert at == DerivativeUnavailable(reason="derivative_failed")
    assert marker.exists()
    assert list(work.iterdir()) == []


# --- real binaries ----------------------------------------------------------------------

RATES = [8000, 11025, 16000, 44100, 48000]


def _ffmpeg(*arguments: str) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed argv, test-only fixture generation
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error", *arguments],  # noqa: S607
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=60,
    ).stdout


def _burst(start: int, end: int) -> str:
    """A 700 Hz burst on samples ``[start, end)`` of the original (lavfi ``n``)."""
    return f"if(gte(n,{start})*lt(n,{end}),0.5*sin(2*PI*700*t),0)"


def _original(path: Path, rate: int, samples: int, *channels: str, codec: tuple = ()) -> Path:
    source = f"aevalsrc=exprs='{'|'.join(channels)}':s={rate}"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        source,
        "-af",
        f"atrim=end_sample={samples}",
        *(codec or ("-c:a", "pcm_s16le")),
        str(path),
    )
    return path


def _derive(source: Path, tmp_path: Path, settings, **facts) -> DerivedAudio:
    work = tmp_path / f"work-{source.stem}"
    work.mkdir(exist_ok=True)
    outcome = derive_audio(
        source,
        _facts(**facts),
        inspection=None,
        work_dir=work,
        settings=settings,
        max_bytes=MAX_BYTES,
    )
    assert isinstance(outcome, DerivedAudio), outcome
    return outcome


def _data(derived: DerivedAudio) -> bytes:
    raw = derived.path.read_bytes()
    header = parse_derivative_header(raw[: 64 + RECORD_MAX_BYTES])
    return raw[header.data_offset : header.data_offset + header.data_size]


@pytest.mark.ffmpeg
@pytest.mark.parametrize("rate", RATES)
def test_each_burst_is_found_on_its_own_channel_at_its_original_time(
    rate: int, tmp_path: Path, settings
) -> None:
    total = 3 * rate + 7
    left = (rate + 37, rate + 37 + rate // 2)
    right = (2 * rate - 11, 2 * rate - 11 + rate // 4)
    source = _original(tmp_path / "bursts.wav", rate, total, _burst(*left), _burst(*right))
    derived = _derive(source, tmp_path, settings, rate=rate)

    # The resampler keeps the length between floor and ceil of N_o * 16000 / R_o.
    exact = Fraction(total * 16000, rate)
    assert math.floor(exact) <= derived.sample_count <= math.ceil(exact)
    assert derived.channel_count == 2
    record = derived.record
    mapping = TimeMap.from_record(record["time_map"])
    assert record["time_map"]["derivative_sample_count"] == derived.sample_count
    frame = Fraction(FRAME_SAMPLES * rate, 16000)
    channels = record["activity"]["channels"]
    for channel, (start, end) in zip(channels, (left, right), strict=True):
        assert len(channel["intervals"]) == 1, channel["intervals"]
        found_start, found_end = channel["intervals"][0]
        assert abs(mapping.to_original(found_start) - start) <= frame
        assert abs(mapping.to_original(found_end) - end) <= frame
        low, high = mapping.original_cover(found_start, found_end)
        assert low <= start + frame and high >= end - frame


@pytest.mark.ffmpeg
def test_the_derivative_file_matches_its_digest_size_and_record(tmp_path: Path, settings) -> None:
    source = _original(tmp_path / "pair.wav", 44100, 2 * 44100, _burst(100, 20000), "0")
    derived = _derive(source, tmp_path, settings)
    raw = derived.path.read_bytes()
    assert len(raw) == derived.size_bytes
    assert hashlib.sha256(raw).hexdigest() == derived.sha256
    header = parse_derivative_header(raw[: 64 + RECORD_MAX_BYTES])
    assert header.record == derived.record
    assert header.data_size == 2 * 2 * derived.sample_count
    assert header.data_offset + header.data_size == len(raw)
    # Only the finished WAV stays in the work directory.
    assert list(derived.path.parent.iterdir()) == [derived.path]


@pytest.mark.ffmpeg
def test_the_record_is_what_the_stored_samples_give(tmp_path: Path, settings) -> None:
    source = _original(
        tmp_path / "mixed.wav", 48000, 3 * 48000, _burst(48000, 72000), "0.001*random(0)"
    )
    derived = _derive(source, tmp_path, settings, rate=48000)
    again = activity_record(measure_activity(_data(derived), 2), None)
    assert again == derived.record["activity"]


@pytest.mark.ffmpeg
def test_eight_channels_keep_their_order(tmp_path: Path, settings) -> None:
    rate = 44100
    channels = [
        _burst(rate // 10 * (index + 1), rate // 10 * (index + 1) + rate // 5) for index in range(8)
    ]
    source = _original(tmp_path / "eight.wav", rate, 2 * rate, *channels)
    derived = _derive(source, tmp_path, settings, channels=8)
    mapping = TimeMap(rate)
    found = derived.record["activity"]["channels"]
    assert [channel["index"] for channel in found] == list(range(8))
    for index, channel in enumerate(found):
        ((start, _end),) = channel["intervals"]
        assert (
            abs(mapping.to_original(start) - rate // 10 * (index + 1))
            <= FRAME_SAMPLES * rate / 16000
        )
    assert derived.record["format"]["channel_map"] == list(range(8))


@pytest.mark.ffmpeg
def test_a_16_khz_source_passes_through_unchanged(tmp_path: Path, settings) -> None:
    source = _original(
        tmp_path / "plain.wav", 16000, 48_123, "0.3*random(0)", "0.2*sin(2*PI*300*t)"
    )
    decoded = _ffmpeg("-i", str(source), "-map", "0:a:0", "-c:a", "pcm_s16le", "-f", "s16le", "-")
    derived = _derive(source, tmp_path, settings, rate=16000)
    assert derived.sample_count == 48_123
    assert _data(derived) == decoded


def _encoders() -> str:
    return subprocess.run(  # noqa: S603
        ["ffmpeg", "-hide_banner", "-encoders"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    ).stdout


@pytest.mark.ffmpeg
@pytest.mark.parametrize(
    ("suffix", "encoder", "codec"),
    [
        (".mp3", "libmp3lame", ("-c:a", "libmp3lame", "-b:a", "128k")),
        (".m4a", "aac", ("-c:a", "aac", "-b:a", "128k")),
        (".ogg", "libopus", ("-c:a", "libopus", "-b:a", "64k")),
    ],
)
def test_a_signalled_encoder_delay_is_not_in_the_timeline(
    suffix: str, encoder: str, codec: tuple, tmp_path: Path, settings
) -> None:
    if f" {encoder} " not in _encoders():
        pytest.skip(f"this ffmpeg has no {encoder} encoder")
    rate = 44100
    start = rate + 37
    source = _original(
        tmp_path / f"coded{suffix}",
        rate,
        3 * rate,
        _burst(start, start + rate // 2),
        "0",
        codec=codec,
    )
    # Opus decodes at 48 kHz: the original timeline is the decoded stream.
    decoded_rate = 48000 if encoder == "libopus" else rate
    derived = _derive(source, tmp_path, settings, rate=decoded_rate, codec=None)
    mapping = TimeMap(decoded_rate)
    ((found_start, _end),) = derived.record["activity"]["channels"][0]["intervals"]
    expected = Fraction(start * decoded_rate, rate)
    assert abs(mapping.to_original(found_start) - expected) <= Fraction(
        FRAME_SAMPLES * decoded_rate, 16000
    )
    assert derived.record["activity"]["channels"][1]["intervals"] == []


# --- failures are reasons, never exceptions (A7) ---------------------------------------


@pytest.fixture(scope="module")
def stereo_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("derivative-source")
    return _original(root / "stereo.wav", 16000, 2 * 16000, _burst(1000, 9000), "0")


@pytest.mark.ffmpeg
def test_derivation_failures_are_reasons_never_raised(
    stereo_source: Path, tmp_path: Path, settings
) -> None:
    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"staged wav bytes" * 64)
    cases = {
        "derivative_tool_unavailable": (stereo_source, {"ffmpeg_path": str(tmp_path / "missing")}),
        "derivative_timeout": (
            stereo_source,
            {
                "ffmpeg_path": str(_script(tmp_path / "slow-ffmpeg", "sleep 5")),
                "media_derivative_timeout_seconds": 0.5,
            },
        ),
        "derivative_failed": (
            stereo_source,
            {"ffmpeg_path": str(_script(tmp_path / "bad", "exit 3"))},
        ),
        "derivative_empty": (
            stereo_source,
            {"ffmpeg_path": str(_script(tmp_path / "quiet", "exit 0"))},
        ),
    }
    for reason, (source, update) in cases.items():
        work = tmp_path / f"work-{reason}"
        work.mkdir()
        outcome = derive_audio(
            source,
            _facts(rate=16000, duration=None),
            inspection=None,
            work_dir=work,
            settings=settings.model_copy(update=update),
            max_bytes=MAX_BYTES,
        )
        assert outcome == DerivativeUnavailable(reason=reason), reason
        assert list(work.iterdir()) == [], reason
    work = tmp_path / "work-corrupt"
    work.mkdir()
    outcome = derive_audio(
        corrupt,
        _facts(rate=16000, duration=None),
        inspection=None,
        work_dir=work,
        settings=settings,
        max_bytes=MAX_BYTES,
    )
    assert outcome == DerivativeUnavailable(reason="derivative_failed")
    # Allow-control: the same source with the real tool is derived.
    assert isinstance(_derive(stereo_source, tmp_path, settings, rate=16000), DerivedAudio)


@pytest.mark.ffmpeg
def test_output_beyond_the_cap_is_too_large_never_truncated(
    stereo_source: Path, tmp_path: Path, settings
) -> None:
    flood = _script(tmp_path / "flood-ffmpeg", "exec head -c 50000000 /dev/zero")
    work = tmp_path / "work"
    work.mkdir()
    outcome = derive_audio(
        stereo_source,
        _facts(rate=16000, duration=None),
        inspection=None,
        work_dir=work,
        settings=settings.model_copy(update={"ffmpeg_path": str(flood)}),
        max_bytes=RECORD_MAX_BYTES + 64 + 1_000_000,
    )
    assert outcome == DerivativeUnavailable(reason="derivative_too_large")
    assert list(work.iterdir()) == []


@pytest.mark.ffmpeg
def test_output_that_is_not_whole_samples_is_failed(
    stereo_source: Path, tmp_path: Path, settings
) -> None:
    odd = _script(tmp_path / "odd-ffmpeg", "head -c 4097 /dev/zero")
    work = tmp_path / "work"
    work.mkdir()
    outcome = derive_audio(
        stereo_source,
        _facts(rate=16000, duration=None),
        inspection=None,
        work_dir=work,
        settings=settings.model_copy(update={"ffmpeg_path": str(odd)}),
        max_bytes=MAX_BYTES,
    )
    assert outcome == DerivativeUnavailable(reason="derivative_failed")


@pytest.mark.ffmpeg
def test_a_record_over_the_limit_is_record_too_large(
    stereo_source: Path, tmp_path: Path, settings, monkeypatch
) -> None:
    monkeypatch.setattr(derivatives, "RECORD_MAX_BYTES", 256)
    work = tmp_path / "work"
    work.mkdir()
    outcome = derive_audio(
        stereo_source,
        _facts(rate=16000, duration=None),
        inspection=None,
        work_dir=work,
        settings=settings,
        max_bytes=MAX_BYTES,
    )
    assert outcome == DerivativeUnavailable(reason="record_too_large")
    assert list(work.iterdir()) == []


# --- the docs match the code (SPEC D10, A9) ---------------------------------------------

REPOSITORY = Path(__file__).resolve().parents[3]
README = Path(__file__).resolve().parents[1] / "media" / "README.md"
ADR = (
    REPOSITORY
    / "docs/architecture/decisions/0042-channel-preserving-derivatives-and-signal-activity.md"
)
SPEC_REASONS = {
    "derivatives_disabled",
    "audio_deleted_after_processing",
    "source_not_registered",
    "source_not_live",
    "source_changed",
    "source_facts_unknown",
    "already_derived",
    "derivative_too_large",
    "derivative_tool_unavailable",
    "derivative_timeout",
    "derivative_failed",
    "derivative_empty",
    "record_too_large",
    "storage_failed",
    "stale_attempt",
    "job_cancelled",
    "meeting_deleted",
    "storage_conflict",
    "tenant_mismatch",
}


def test_the_code_has_exactly_the_published_reason_codes() -> None:
    assert set(derivatives.REASON_CODES) == SPEC_REASONS
    assert len(derivatives.REASON_CODES) == len(SPEC_REASONS)


def test_the_readme_names_every_version_and_reason_code() -> None:
    from backend.media import activity, time_map
    from backend.services.audit_event_types import AuditEventType

    text = " ".join(README.read_text(encoding="utf-8").split())
    versions = (
        DERIVATIVE_PRODUCER,
        time_map.TIME_MAP_VERSION,
        time_map.ORIGINAL_TIMELINE,
        activity.ACTIVITY_VERSION,
        activity.ACTIVITY_KIND,
    )
    for name in (*versions, *derivatives.REASON_CODES, DerivativeRecordInvalid.code):
        assert f"`{name}`" in text, name
    assert f"`{AuditEventType.MEDIA_DERIVATIVE_UNAVAILABLE.value}`" in text
    assert "With the default setting, HOM-38 produces nothing in production." in text


def test_the_adr_is_accepted_and_names_the_versions_and_work_units() -> None:
    if not ADR.exists():
        pytest.skip("the repository docs are not next to this checkout")
    text = " ".join(ADR.read_text(encoding="utf-8").split())
    assert "Status: Accepted" in text
    for name in ("audio-derivative/1", "time-map/1", "signal-activity/1", "**W1.**", "**W2.**"):
        assert name in text, name
    assert "With the default setting, HOM-38 produces nothing in production." in text
