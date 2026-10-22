"""HOM-37: channel metadata, per-channel signal analysis and channel-role attribution.

The first part is pure: PCM is built in memory and the attribution rules run on
hand-made inspections.  The ``ffmpeg`` part synthesises short fixtures with
``lavfi`` (no committed media) and runs the real probe and the bounded analysis
decode.  Energy is a signal-level heuristic, not speech detection: these tests
prove the arithmetic and the rules on synthetic audio only.
"""

from __future__ import annotations

import math
import stat
import subprocess
import sys
from array import array
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.media import (
    AudioStreamMetadata,
    InvalidAudioStreamError,
    MediaMetadata,
    MediaValidationPolicy,
    validate_media_upload,
)
from backend.media.channel_roles import (
    SETTINGS_KEY,
    ChannelRoleMapping,
    InvalidChannelRoleMapping,
    active_mappings,
    mapping_digest,
    mapping_from_settings,
    validate_mapping_channels,
)
from backend.media.channels import (
    ANALYSIS_SAMPLE_RATE_HZ,
    CHANNEL_INSPECTION_VERSION,
    ChannelAnalysisPolicy,
    ChannelInspection,
    analyze_channel_samples,
    attribute_channels,
    channel_inspection_record,
    inspect_channels,
)

POLICY = ChannelAnalysisPolicy()
RATE = ANALYSIS_SAMPLE_RATE_HZ


# --- PCM helpers ----------------------------------------------------------------------


def _tone(frequency: float, *, seconds: float = 1.0, amplitude: float = 1.0) -> list[float]:
    count = int(seconds * RATE)
    return [amplitude * math.sin(2 * math.pi * frequency * n / RATE) for n in range(count)]


def _silence(seconds: float = 1.0) -> list[float]:
    return [0.0] * int(seconds * RATE)


def _pcm(*channels: list[float]) -> bytes:
    samples = array("h")
    for frame in zip(*channels, strict=True):
        samples.extend(max(-32768, min(32767, round(value * 32767))) for value in frame)
    if sys.byteorder == "big":
        samples.byteswap()
    return samples.tobytes()


def _analyze(*channels: list[float], policy: ChannelAnalysisPolicy = POLICY):
    return analyze_channel_samples(_pcm(*channels), channel_count=len(channels), policy=policy)


# --- signal analysis (pure) -----------------------------------------------------------


def test_two_different_tones_are_active_and_not_duplicated() -> None:
    result = _analyze(_tone(440), _tone(880))
    assert [channel.index for channel in result.channels] == [0, 1]
    for channel in result.channels:
        assert channel.rms_dbfs == pytest.approx(-3.01, abs=0.02)
        assert channel.active_share == 1.0
        assert channel.silent is False
        assert channel.duplicated is False
    assert result.duplicate_pairs == ()
    assert result.analyzed_seconds == pytest.approx(1.0)


def test_equal_level_different_tones_are_compared_by_samples_not_energy() -> None:
    # R6: the same RMS on both channels is not a duplicate.
    result = _analyze(_tone(300), _tone(1200))
    assert result.channels[0].rms_dbfs == result.channels[1].rms_dbfs
    assert result.duplicate_pairs == ()


def test_digital_silence_has_no_level_and_is_silent() -> None:
    result = _analyze(_tone(440), _silence())
    assert result.channels[1].rms_dbfs is None
    assert result.channels[1].active_share == 0.0
    assert result.channels[1].silent is True
    assert result.channels[0].silent is False
    assert result.duplicate_pairs == ()


def test_a_quiet_channel_below_the_threshold_is_silent_but_keeps_its_level() -> None:
    result = _analyze(_tone(440), _tone(440, amplitude=10 ** (-70 / 20)))
    quiet = result.channels[1]
    assert quiet.silent is True
    assert quiet.rms_dbfs == pytest.approx(-73.0, abs=0.5)
    assert result.duplicate_pairs == ()  # silent channels are never compared (R5)


@pytest.mark.parametrize(
    "right",
    [
        pytest.param(lambda left: left, id="identical"),
        pytest.param(lambda left: [0.5 * value for value in left], id="half-level"),
        pytest.param(lambda left: [-value for value in left], id="inverted"),
    ],
)
def test_the_same_signal_scaled_or_inverted_is_a_duplicate(right) -> None:
    left = _tone(440)
    result = _analyze(left, right(left))
    assert result.duplicate_pairs == ((0, 1),)
    assert all(channel.duplicated for channel in result.channels)


def test_two_silent_channels_are_not_a_duplicate_pair() -> None:
    result = _analyze(_silence(), _silence())
    assert all(channel.silent for channel in result.channels)
    assert result.duplicate_pairs == ()
    assert not any(channel.duplicated for channel in result.channels)


def test_duplicate_pairs_are_found_between_any_two_channels() -> None:
    base = _tone(440)
    result = _analyze(base, _tone(700), base)
    assert result.duplicate_pairs == ((0, 2),)
    assert [channel.duplicated for channel in result.channels] == [True, False, True]


def test_active_share_counts_frames_above_the_threshold() -> None:
    half_active = _tone(440, seconds=0.5) + _silence(0.5)
    result = _analyze(half_active, _tone(880))
    assert result.channels[0].active_share == pytest.approx(0.5, abs=0.02)
    assert result.channels[0].silent is False


def test_less_than_one_frame_is_refused_and_a_partial_sample_is_ignored() -> None:
    with pytest.raises(ValueError):
        analyze_channel_samples(bytes(2 * 2 * 100), channel_count=2, policy=POLICY)
    pcm = _pcm(_tone(440), _tone(880)) + b"\x01"
    assert len(analyze_channel_samples(pcm, channel_count=2, policy=POLICY).channels) == 2


def test_thresholds_are_configuration() -> None:
    loose = ChannelAnalysisPolicy(active_threshold_dbfs=-90.0)
    result = _analyze(_tone(440), _tone(440, amplitude=10 ** (-70 / 20)), policy=loose)
    assert result.channels[1].silent is False
    strict = ChannelAnalysisPolicy(duplicate_min_correlation=1.0)
    noisy = [value + (0.01 if n % 2 else -0.01) for n, value in enumerate(_tone(440))]
    assert _analyze(_tone(440), noisy, policy=strict).duplicate_pairs == ()
    assert _analyze(_tone(440), noisy).duplicate_pairs == ((0, 1),)


@pytest.mark.parametrize(
    "values",
    [
        {"analysis_seconds": -1},
        {"analysis_seconds": 121},
        {"timeout_seconds": 0},
        {"timeout_seconds": 61},
        {"active_threshold_dbfs": 0},
        {"active_threshold_dbfs": -121},
        {"silent_max_active_share": 1},
        {"duplicate_min_correlation": 0},
        {"duplicate_min_correlation": 1.01},
        {"duplicate_max_lag_ms": -1},
        {"duplicate_max_lag_ms": 20.5},
    ],
)
def test_policy_ranges_are_checked(values) -> None:
    with pytest.raises(ValueError):
        ChannelAnalysisPolicy(**values)


# --- delayed copies (turn 3, finding 1) -----------------------------------------------

MULTITONE = ((310, 0.3), (470, 0.3), (1130, 0.3))


def _multitone(*, seconds: float = 1.0, delay: int = 0) -> list[float]:
    """Three tones; ``delay`` samples later than the undelayed signal."""
    count = int(seconds * RATE)
    return [
        sum(
            level * math.sin(2 * math.pi * frequency * (n - delay) / RATE)
            for frequency, level in MULTITONE
        )
        for n in range(count)
    ]


@pytest.mark.parametrize("delay", [1, 2, 5, 40, -40])
def test_a_delayed_copy_is_a_duplicate(delay: int) -> None:
    result = _analyze(_multitone(), _multitone(delay=delay))
    assert result.duplicate_pairs == ((0, 1),)


def test_a_copy_with_independent_low_noise_stays_a_duplicate() -> None:
    import random

    generator = random.Random(7)  # noqa: S311 - seeded test noise, not security
    clean = _multitone()
    noisy = [value + generator.gauss(0, 0.002) for value in clean]  # about -50 dB
    assert _analyze(clean, noisy).duplicate_pairs == ((0, 1),)


def test_different_signals_stay_apart_within_the_lag_window() -> None:
    assert _analyze(_tone(440), _tone(880)).duplicate_pairs == ()
    assert _analyze(_multitone(), _tone(700)).duplicate_pairs == ()


def test_a_zero_lag_window_compares_only_aligned_samples() -> None:
    aligned_only = ChannelAnalysisPolicy(duplicate_max_lag_ms=0)
    delayed = _analyze(_multitone(), _multitone(delay=40), policy=aligned_only)
    assert delayed.duplicate_pairs == ()
    same = _analyze(_multitone(), _multitone(), policy=aligned_only)
    assert same.duplicate_pairs == ((0, 1),)


def test_a_delay_beyond_the_window_is_not_searched() -> None:
    narrow = ChannelAnalysisPolicy(duplicate_max_lag_ms=1)  # +-8 samples
    assert _analyze(_multitone(), _multitone(delay=40), policy=narrow).duplicate_pairs == ()
    assert _analyze(_multitone(), _multitone(delay=8), policy=narrow).duplicate_pairs == ((0, 1),)


def test_the_lag_search_keeps_its_memory_small() -> None:
    # Turn 4: no per-channel float lists. Two independent 30 s channels peaked at
    # about 18 MB with float lists; the typed arrays keep it near 3 MB.
    import random
    import tracemalloc

    generator = random.Random(3)  # noqa: S311 - seeded test noise, not security
    count = 30 * RATE * 2
    pcm = array("h", (generator.randint(-12_000, 12_000) for _ in range(count))).tobytes()
    tracemalloc.start()
    try:
        result = analyze_channel_samples(pcm, channel_count=2, policy=POLICY)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result.duplicate_pairs == ()
    assert peak < 6_000_000, f"peak {peak} bytes"


# --- containment (turn 3, finding 2b) -------------------------------------------------


def test_an_unexpected_decode_error_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    from backend.media import channels as channels_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(channels_module, "_decode_pcm", broken)
    inspection = inspect_channels(
        tmp_path / "x.flac", metadata=_metadata(channels=2), policy=POLICY
    )
    assert (inspection.status, inspection.unavailable_reason) == ("unavailable", "analysis_failed")


def test_an_unexpected_analysis_error_is_unavailable(monkeypatch, tmp_path: Path) -> None:
    from backend.media import channels as channels_module

    def broken(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(
        channels_module, "_decode_pcm", lambda *a, **k: _pcm(_tone(440), _tone(880))
    )
    monkeypatch.setattr(channels_module, "analyze_channel_samples", broken)
    inspection = inspect_channels(
        tmp_path / "x.flac", metadata=_metadata(channels=2), policy=POLICY
    )
    assert (inspection.status, inspection.unavailable_reason) == ("unavailable", "analysis_failed")


# --- attribution (pure) ---------------------------------------------------------------


def _metadata(*, channels: int, sample_rate_hz: int = 16_000, layout: str = "stereo"):
    return MediaMetadata(
        original_filename="call.flac",
        extension=".flac",
        container="flac",
        detected_format_names=("flac",),
        mime_type="audio/flac",
        duration_seconds=3.0,
        size_bytes=1000,
        audio_streams=(
            AudioStreamMetadata(
                index=0,
                codec="flac",
                channels=channels,
                sample_rate_hz=sample_rate_hz,
                channel_layout=layout,
                duration_seconds=3.0,
            ),
        ),
        video_streams=(),
    )


def _inspection(*channels: list[float], unavailable: str | None = None, rate: int = 16_000):
    metadata = _metadata(channels=len(channels), sample_rate_hz=rate)
    return ChannelInspection.from_parts(
        metadata,
        analysis=None if unavailable else _analyze(*channels),
        unavailable_reason=unavailable,
        policy=POLICY,
    )


def _mapping(*channels: tuple[int, str], source: str = "file_upload", provider: str = "console"):
    ordered = tuple(sorted(channels))
    return ChannelRoleMapping(
        source_type=source,
        source_provider=provider,
        version=1,
        digest=mapping_digest(source, provider, ordered),
        channels=ordered,
        updated_at=datetime(2026, 10, 22, tzinfo=UTC),
        updated_by_user_id="admin",
    )


AGENT_CUSTOMER = ((0, "agent"), (1, "customer"))


def _outcome(attributions) -> list[tuple[str, str, str | None]]:
    return [(item.role, item.attribution, item.reason) for item in attributions]


def test_mapped_distinct_channels_are_trusted() -> None:
    result = attribute_channels(_inspection(_tone(440), _tone(880)), _mapping(*AGENT_CUSTOMER))
    assert _outcome(result) == [("agent", "trusted", None), ("customer", "trusted", None)]


def test_a_silent_channel_is_unknown_and_the_other_stays_trusted() -> None:
    result = attribute_channels(_inspection(_tone(440), _silence()), _mapping(*AGENT_CUSTOMER))
    assert _outcome(result) == [
        ("agent", "trusted", None),
        ("unknown", "unknown", "channel_silent"),
    ]


def test_duplicated_channels_are_both_unknown() -> None:
    result = attribute_channels(_inspection(_tone(440), _tone(440)), _mapping(*AGENT_CUSTOMER))
    assert _outcome(result) == [("unknown", "unknown", "channels_duplicated")] * 2


def test_two_silent_channels_are_unknown_as_silent() -> None:
    result = attribute_channels(_inspection(_silence(), _silence()), _mapping(*AGENT_CUSTOMER))
    assert _outcome(result) == [("unknown", "unknown", "channel_silent")] * 2


def test_without_a_mapping_every_channel_is_unknown() -> None:
    result = attribute_channels(_inspection(_tone(440), _tone(880)), None)
    assert _outcome(result) == [("unknown", "unknown", "no_mapping")] * 2


@pytest.mark.parametrize(
    "mapping",
    [None, _mapping(*AGENT_CUSTOMER), _mapping((0, "agent"))],
    ids=["no-mapping", "two-channel", "one-channel"],
)
def test_mono_is_never_trusted(mapping) -> None:
    result = attribute_channels(_inspection(_tone(440)), mapping)
    assert _outcome(result) == [("unknown", "unknown", "mono")]


def test_an_unnamed_channel_is_unmapped() -> None:
    result = attribute_channels(_inspection(_tone(440), _tone(880)), _mapping((0, "agent")))
    assert _outcome(result) == [
        ("agent", "trusted", None),
        ("unknown", "unknown", "channel_unmapped"),
    ]


def test_a_mapped_channel_missing_from_the_file_makes_every_channel_unknown() -> None:
    mapping = _mapping((0, "agent"), (2, "customer"))
    result = attribute_channels(_inspection(_tone(440), _tone(880)), mapping)
    assert _outcome(result) == [("unknown", "unknown", "channel_missing")] * 2


def test_an_unavailable_analysis_trusts_nothing() -> None:
    inspection = _inspection(_tone(440), _tone(880), unavailable="analysis_timeout")
    result = attribute_channels(inspection, _mapping(*AGENT_CUSTOMER))
    assert _outcome(result) == [("unknown", "unknown", "analysis_unavailable")] * 2


def test_an_unreadable_mapping_trusts_nothing() -> None:
    result = attribute_channels(_inspection(_tone(440), _tone(880)), None, mapping_invalid=True)
    assert _outcome(result) == [("unknown", "unknown", "mapping_invalid")] * 2


def test_reason_precedence_follows_the_published_order() -> None:
    # mono beats an unreadable mapping; no mapping beats an unavailable analysis.
    mono = attribute_channels(_inspection(_tone(440)), None, mapping_invalid=True)
    assert _outcome(mono) == [("unknown", "unknown", "mono")]
    unavailable = _inspection(_tone(440), _tone(880), unavailable="analysis_failed")
    assert (
        _outcome(attribute_channels(unavailable, None))
        == [("unknown", "unknown", "no_mapping")] * 2
    )
    # an unmapped silent channel is reported as unmapped.
    silent = attribute_channels(_inspection(_tone(440), _silence()), _mapping((0, "agent")))
    assert _outcome(silent)[1] == ("unknown", "unknown", "channel_unmapped")


# --- the record (pure) ----------------------------------------------------------------


def _settings_with(mapping: ChannelRoleMapping) -> dict:
    return {
        "unrelated": {"kept": True},
        SETTINGS_KEY: {
            f"{mapping.source_type}:{mapping.source_provider}": {
                "source_type": mapping.source_type,
                "source_provider": mapping.source_provider,
                "version": mapping.version,
                "digest": mapping.digest,
                "active": True,
                "channels": [
                    {"channel": channel, "role": role} for channel, role in mapping.channels
                ],
                "updated_at": mapping.updated_at.isoformat(),
                "updated_by_user_id": mapping.updated_by_user_id,
            }
        },
    }


RECORD_KEYS = {
    "version",
    "status",
    "unavailable_reason",
    "channel_count",
    "channel_layout",
    "sample_rate_hz",
    "codec",
    "duration_seconds",
    "narrowband",
    "audio_streams",
    "analysis_seconds",
    "analysis_sample_rate_hz",
    "thresholds",
    "flags",
    "duplicate_pairs",
    "mapping_source_type",
    "mapping_source_provider",
    "mapping_version",
    "mapping_digest",
    "channels",
}


def test_the_record_carries_metadata_signal_and_attribution() -> None:
    mapping = _mapping(*AGENT_CUSTOMER)
    record = channel_inspection_record(
        _inspection(_tone(440), _silence()),
        source_type="file_upload",
        source_provider="console",
        workspace_settings=_settings_with(mapping),
    )
    assert set(record) == RECORD_KEYS
    assert record["version"] == CHANNEL_INSPECTION_VERSION == "channel-inspection/1"
    assert record["status"] == "complete" and record["unavailable_reason"] is None
    assert record["channel_count"] == 2 and record["channel_layout"] == "stereo"
    assert record["sample_rate_hz"] == 16_000 and record["codec"] == "flac"
    assert record["narrowband"] is False
    assert record["audio_streams"] == [
        {
            "index": 0,
            "codec": "flac",
            "channels": 2,
            "channel_layout": "stereo",
            "sample_rate_hz": 16_000,
            "duration_seconds": 3.0,
        }
    ]
    assert record["analysis_sample_rate_hz"] == 8000
    assert record["analysis_seconds"] == pytest.approx(1.0)
    assert record["thresholds"] == {
        "frame_ms": 20,
        "active_threshold_dbfs": -50.0,
        "silent_max_active_share": 0.01,
        "duplicate_min_correlation": 0.99,
        "duplicate_max_lag_ms": 5.0,
        "narrowband_below_hz": 16000,
    }
    assert record["flags"] == ["silent_channel"]
    assert record["duplicate_pairs"] == []
    assert record["mapping_source_type"] == "file_upload"
    assert record["mapping_source_provider"] == "console"
    assert record["mapping_version"] == 1 and record["mapping_digest"] == mapping.digest
    assert record["channels"] == [
        {
            "index": 0,
            "rms_dbfs": pytest.approx(-3.01, abs=0.02),
            "active_share": 1.0,
            "silent": False,
            "duplicated": False,
            "role": "agent",
            "attribution": "trusted",
            "reason": None,
        },
        {
            "index": 1,
            "rms_dbfs": None,
            "active_share": 0.0,
            "silent": True,
            "duplicated": False,
            "role": "unknown",
            "attribution": "unknown",
            "reason": "channel_silent",
        },
    ]


def test_the_record_flags_a_mono_as_stereo_file() -> None:
    record = channel_inspection_record(
        _inspection(_tone(440), _tone(440)),
        source_type="file_upload",
        source_provider="console",
        workspace_settings={},
    )
    assert record["flags"] == ["duplicated_channels", "mono_as_stereo"]
    assert record["duplicate_pairs"] == [[0, 1]]
    assert record["mapping_version"] is None and record["mapping_digest"] is None
    assert {channel["reason"] for channel in record["channels"]} == {"no_mapping"}


def test_three_channels_with_a_duplicate_are_not_mono_as_stereo() -> None:
    record = channel_inspection_record(
        _inspection(_tone(440), _tone(700), _tone(440)),
        source_type="file_upload",
        source_provider="console",
        workspace_settings={},
    )
    assert record["flags"] == ["duplicated_channels"]
    assert record["duplicate_pairs"] == [[0, 2]]


def test_an_unavailable_record_keeps_the_metadata_and_trusts_nothing() -> None:
    mapping = _mapping(*AGENT_CUSTOMER)
    record = channel_inspection_record(
        _inspection(_tone(440), _tone(880), unavailable="analysis_timeout"),
        source_type="file_upload",
        source_provider="console",
        workspace_settings=_settings_with(mapping),
    )
    assert record["status"] == "unavailable"
    assert record["unavailable_reason"] == "analysis_timeout"
    assert record["analysis_seconds"] is None and record["analysis_sample_rate_hz"] is None
    assert record["flags"] == [] and record["duplicate_pairs"] == []
    assert record["channel_count"] == 2 and record["sample_rate_hz"] == 16_000
    for channel in record["channels"]:
        assert channel["rms_dbfs"] is None and channel["active_share"] is None
        assert channel["silent"] is None and channel["duplicated"] is None
        assert (channel["role"], channel["attribution"], channel["reason"]) == (
            "unknown",
            "unknown",
            "analysis_unavailable",
        )


def test_a_mapping_for_another_source_never_applies() -> None:
    record = channel_inspection_record(
        _inspection(_tone(440), _tone(880)),
        source_type="file_upload",
        source_provider="console",
        workspace_settings=_settings_with(_mapping(*AGENT_CUSTOMER, provider="api")),
    )
    assert {channel["reason"] for channel in record["channels"]} == {"no_mapping"}
    assert record["mapping_version"] is None


@pytest.mark.parametrize(
    "settings",
    [
        [],
        "not an object",
        {SETTINGS_KEY: []},
        {SETTINGS_KEY: "garbage"},
        {SETTINGS_KEY: {"file_upload:console": "garbage"}},
        {
            SETTINGS_KEY: {
                "file_upload:console": {"active": True, "channels": [{"channel": [0], "role": {}}]}
            }
        },
    ],
)
def test_unreadable_workspace_settings_never_raise(settings) -> None:
    # Finding 2a: an unreadable settings value is "mapping_invalid", never an exception.
    record = channel_inspection_record(
        _inspection(_tone(440), _tone(880)),
        source_type="file_upload",
        source_provider="console",
        workspace_settings=settings,
    )
    assert {channel["reason"] for channel in record["channels"]} == {"mapping_invalid"}
    assert active_mappings(settings) == []


def test_a_corrupt_stored_mapping_is_reported_not_trusted() -> None:
    settings = {SETTINGS_KEY: {"file_upload:console": {"channels": "garbage", "active": True}}}
    record = channel_inspection_record(
        _inspection(_tone(440), _tone(880)),
        source_type="file_upload",
        source_provider="console",
        workspace_settings=settings,
    )
    assert {channel["reason"] for channel in record["channels"]} == {"mapping_invalid"}


@pytest.mark.parametrize(("rate", "narrow"), [(8000, True), (15_999, True), (16_000, False)])
def test_narrowband_means_below_16_khz(rate: int, narrow: bool) -> None:
    record = channel_inspection_record(
        _inspection(_tone(440), _tone(880), rate=rate),
        source_type="file_upload",
        source_provider="console",
        workspace_settings={},
    )
    assert record["narrowband"] is narrow


# --- the mapping vocabulary (pure) ----------------------------------------------------


def test_mapping_channels_are_validated() -> None:
    assert validate_mapping_channels([(1, "customer"), (0, "agent")], max_channels=8) == (
        (0, "agent"),
        (1, "customer"),
    )
    assert validate_mapping_channels([(0, "agent")], max_channels=8) == ((0, "agent"),)
    for invalid in (
        [],
        [(0, "unknown")],
        [(0, "supervisor")],
        [(-1, "agent")],
        [(8, "agent")],
        [(True, "agent")],
        [("0", "agent")],
        [(0, "agent"), (0, "customer")],
        [(0, "agent"), (1, "agent")],
    ):
        with pytest.raises(InvalidChannelRoleMapping):
            validate_mapping_channels(invalid, max_channels=8)


def test_the_digest_names_the_content_not_the_order() -> None:
    one = mapping_digest("file_upload", "console", [(0, "agent"), (1, "customer")])
    two = mapping_digest("file_upload", "console", [(1, "customer"), (0, "agent")])
    assert one == two and len(one) == 64
    assert one != mapping_digest("file_upload", "api", [(0, "agent"), (1, "customer")])
    assert one != mapping_digest("file_upload", "console", [(0, "customer"), (1, "agent")])


def test_mappings_are_read_from_workspace_settings() -> None:
    mapping = _mapping(*AGENT_CUSTOMER)
    settings = _settings_with(mapping)
    loaded = mapping_from_settings(settings, source_type="file_upload", source_provider="console")
    assert loaded == mapping
    assert active_mappings(settings) == [mapping]
    assert mapping_from_settings({}, source_type="file_upload", source_provider="console") is None
    settings[SETTINGS_KEY]["file_upload:console"]["active"] = False
    assert (
        mapping_from_settings(settings, source_type="file_upload", source_provider="console")
        is None
    )
    assert active_mappings(settings) == []
    settings[SETTINGS_KEY]["file_upload:console"] = {"active": True, "channels": [{"x": 1}]}
    with pytest.raises(InvalidChannelRoleMapping):
        mapping_from_settings(settings, source_type="file_upload", source_provider="console")


# --- real binaries ----------------------------------------------------------------------

STEREO = "c=stereo:s=16000:d=3"


def _ffmpeg(*arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed argv, test-only fixture generation
        ["ffmpeg", "-y", "-nostdin", "-hide_banner", "-v", "error", *arguments],  # noqa: S607
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )


def _lavfi(target: Path, source: str, *codec: str) -> Path:
    _ffmpeg("-f", "lavfi", "-i", source, *codec, str(target))
    return target


def build_channel_fixtures(root: Path) -> Path:
    """Synthesise the HOM-37 fixtures below ``root`` (shared with the API tests)."""
    flac = ("-c:a", "flac")
    pcm = ("-c:a", "pcm_s16le")
    _lavfi(root / "distinct.flac", f"aevalsrc=sin(2*PI*440*t)|sin(2*PI*880*t):{STEREO}", *flac)
    _lavfi(root / "silent_right.flac", f"aevalsrc=sin(2*PI*440*t)|0:{STEREO}", *flac)
    _lavfi(root / "identical.flac", f"aevalsrc=sin(2*PI*440*t)|sin(2*PI*440*t):{STEREO}", *flac)
    _lavfi(root / "half.flac", f"aevalsrc=sin(2*PI*440*t)|0.5*sin(2*PI*440*t):{STEREO}", *flac)
    _lavfi(root / "inverted.flac", f"aevalsrc=sin(2*PI*440*t)|-sin(2*PI*440*t):{STEREO}", *flac)
    _lavfi(root / "both_silent.flac", f"aevalsrc=0|0:{STEREO}", *flac)
    _lavfi(root / "mono.flac", "aevalsrc=sin(2*PI*440*t):c=mono:s=16000:d=3", *flac)
    _lavfi(root / "narrow.wav", "aevalsrc=sin(2*PI*440*t)|sin(2*PI*880*t):s=8000:d=3", *pcm)
    _lavfi(root / "distinct.wav", "aevalsrc=sin(2*PI*440*t)|sin(2*PI*880*t):s=16000:d=3", *pcm)
    nine = "|".join(f"sin(2*PI*{200 + 100 * n}*t)" for n in range(9))
    _lavfi(root / "nine.wav", f"aevalsrc={nine}:s=16000:d=1", *pcm)
    # The same three tones on both channels, the right one 1 or 40 samples (at 8 kHz) later.
    for name, delay in (("delayed_1.flac", 0.000125), ("delayed_40.flac", 0.005)):
        left = "+".join(f"0.3*sin(2*PI*{f}*t)" for f in (310, 470, 1130))
        right = "+".join(f"0.3*sin(2*PI*{f}*(t-{delay}))" for f in (310, 470, 1130))
        _lavfi(root / name, f"aevalsrc={left}|{right}:{STEREO}", *flac)
    return root


MIME = {".flac": "audio/flac", ".wav": "audio/wav"}


@pytest.fixture(scope="module")
def channel_fixtures(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_channel_fixtures(tmp_path_factory.mktemp("channels"))


def _inspect(path: Path, policy: ChannelAnalysisPolicy = POLICY, **kwargs) -> ChannelInspection:
    metadata = validate_media_upload(
        path,
        original_filename=path.name,
        declared_mime_type=MIME[path.suffix],
        policy=MediaValidationPolicy(decode_probe_seconds=10),
    )
    return inspect_channels(path, metadata=metadata, policy=policy, **kwargs)


def _record(inspection: ChannelInspection) -> dict:
    return channel_inspection_record(
        inspection, source_type="file_upload", source_provider="console", workspace_settings={}
    )


@pytest.mark.ffmpeg
def test_distinct_stereo_reports_its_layout_and_no_flags(channel_fixtures: Path) -> None:
    record = _record(_inspect(channel_fixtures / "distinct.flac"))
    assert record["status"] == "complete"
    assert (record["channel_count"], record["channel_layout"]) == (2, "stereo")
    assert record["audio_streams"][0]["channel_layout"] == "stereo"
    assert record["audio_streams"][0]["duration_seconds"] == pytest.approx(3.0, abs=0.05)
    assert record["codec"] == "flac" and record["sample_rate_hz"] == 16_000
    assert record["flags"] == [] and record["duplicate_pairs"] == []
    assert [channel["silent"] for channel in record["channels"]] == [False, False]
    assert record["analysis_seconds"] == pytest.approx(3.0, abs=0.05)


@pytest.mark.ffmpeg
def test_pcm_wav_reports_the_count_and_the_layout_ffprobe_gives(channel_fixtures: Path) -> None:
    # R1: the layout is reported as given; a 16-bit PCM WAV may say "unknown".
    record = _record(_inspect(channel_fixtures / "distinct.wav"))
    assert record["channel_count"] == 2
    assert isinstance(record["channel_layout"], str) and record["channel_layout"]
    assert record["flags"] == []


@pytest.mark.ffmpeg
def test_one_silent_channel_is_flagged(channel_fixtures: Path) -> None:
    record = _record(_inspect(channel_fixtures / "silent_right.flac"))
    assert record["flags"] == ["silent_channel"]
    assert [channel["silent"] for channel in record["channels"]] == [False, True]


@pytest.mark.ffmpeg
@pytest.mark.parametrize("name", ["identical.flac", "half.flac", "inverted.flac"])
def test_the_same_signal_on_both_channels_is_mono_as_stereo(
    channel_fixtures: Path, name: str
) -> None:
    record = _record(_inspect(channel_fixtures / name))
    assert record["flags"] == ["duplicated_channels", "mono_as_stereo"]
    assert record["duplicate_pairs"] == [[0, 1]]


@pytest.mark.ffmpeg
def test_two_silent_channels_are_silent_not_duplicated(channel_fixtures: Path) -> None:
    record = _record(_inspect(channel_fixtures / "both_silent.flac"))
    assert record["flags"] == ["silent_channel"]
    assert record["duplicate_pairs"] == []
    assert {channel["reason"] for channel in record["channels"]} == {"no_mapping"}


@pytest.mark.ffmpeg
def test_mono_reports_its_layout_and_no_duplicate(channel_fixtures: Path) -> None:
    record = _record(_inspect(channel_fixtures / "mono.flac"))
    assert (record["channel_count"], record["channel_layout"]) == (1, "mono")
    assert record["flags"] == [] and record["duplicate_pairs"] == []
    assert [channel["reason"] for channel in record["channels"]] == ["mono"]


@pytest.mark.ffmpeg
def test_8_khz_telephony_is_accepted_and_narrowband(channel_fixtures: Path) -> None:
    record = _record(_inspect(channel_fixtures / "narrow.wav"))
    assert record["sample_rate_hz"] == 8000
    assert record["narrowband"] is True
    assert record["status"] == "complete" and record["flags"] == []


@pytest.mark.ffmpeg
def test_more_channels_than_allowed_are_still_refused(channel_fixtures: Path) -> None:
    with pytest.raises(InvalidAudioStreamError) as refused:
        validate_media_upload(
            channel_fixtures / "nine.wav",
            original_filename="nine.wav",
            declared_mime_type="audio/wav",
            policy=MediaValidationPolicy(decode_probe_seconds=10),
        )
    assert refused.value.code == "invalid_audio_stream"


@pytest.mark.ffmpeg
def test_the_analysis_window_is_bounded(channel_fixtures: Path) -> None:
    record = _record(
        _inspect(channel_fixtures / "distinct.flac", ChannelAnalysisPolicy(analysis_seconds=1))
    )
    assert record["analysis_seconds"] == pytest.approx(1.0, abs=0.05)


def _script(path: Path, body: str) -> Path:
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.mark.ffmpeg
def test_analysis_failures_are_unavailable_never_raised(
    channel_fixtures: Path, tmp_path: Path
) -> None:
    path = channel_fixtures / "distinct.flac"
    cases = {
        "analysis_disabled": {"policy": ChannelAnalysisPolicy(analysis_seconds=0)},
        "analysis_tool_unavailable": {"ffmpeg_path": tmp_path / "missing-ffmpeg"},
        "analysis_timeout": {
            "policy": ChannelAnalysisPolicy(timeout_seconds=0.5),
            "ffmpeg_path": _script(tmp_path / "slow-ffmpeg", "sleep 5"),
        },
        "analysis_failed": {"ffmpeg_path": _script(tmp_path / "bad-ffmpeg", "exit 3")},
        "analysis_empty": {"ffmpeg_path": _script(tmp_path / "quiet-ffmpeg", "exit 0")},
    }
    for reason, options in cases.items():
        policy = options.pop("policy", POLICY)
        record = _record(_inspect(path, policy, **options))
        assert (record["status"], record["unavailable_reason"]) == ("unavailable", reason)
        assert record["channel_count"] == 2 and record["channel_layout"] == "stereo"
        assert {channel["role"] for channel in record["channels"]} == {"unknown"}


@pytest.mark.ffmpeg
def test_output_beyond_the_byte_cap_is_refused(channel_fixtures: Path, tmp_path: Path) -> None:
    flood = _script(tmp_path / "flood-ffmpeg", "exec head -c 50000000 /dev/zero")
    record = _record(
        _inspect(
            channel_fixtures / "distinct.flac",
            ChannelAnalysisPolicy(analysis_seconds=1),
            ffmpeg_path=flood,
        )
    )
    assert (record["status"], record["unavailable_reason"]) == ("unavailable", "analysis_failed")


@pytest.mark.ffmpeg
@pytest.mark.parametrize("name", ["delayed_1.flac", "delayed_40.flac"])
def test_a_delayed_copy_on_disk_is_mono_as_stereo(channel_fixtures: Path, name: str) -> None:
    record = _record(_inspect(channel_fixtures / name))
    assert record["flags"] == ["duplicated_channels", "mono_as_stereo"]
    assert record["duplicate_pairs"] == [[0, 1]]


@pytest.mark.ffmpeg
def test_non_media_bytes_give_an_unavailable_result(tmp_path: Path) -> None:
    fake = tmp_path / "fake.wav"
    fake.write_bytes(b"staged wav bytes")
    inspection = inspect_channels(fake, metadata=_metadata(channels=1), policy=POLICY)
    assert inspection.status == "unavailable"
    assert inspection.unavailable_reason == "analysis_failed"
