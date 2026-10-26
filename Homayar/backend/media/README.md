# Channel inspection (`backend.media.channels`, `backend.media.channel_roles`)

HOM-37 (WP09-T01, features F25 and F26). On ingest, after a file is accepted, this code
reads the facts of its audio channels, measures each channel's signal, flags silent and
duplicated channels, and gives a channel a role (`agent` or `customer`) only when a
workspace administrator has mapped that channel for the recording's source. HOM-38
(normalized derivatives and VAD) builds on this record.

- It never refuses media. If the analysis cannot run, the result says
  `status: "unavailable"` with a reason code, and the upload or voicemail import goes on
  exactly as before.
- It is a **signal-level heuristic**, not speech detection. "Active" means "carries signal
  above a level". It does not mean someone speaks.
- It never infers a role. A role comes only from the trusted mapping. Stereo, channel
  order, loudness, speech share, transcript content and models never set a role.
- Unknown stays unknown, with a reason code. No value is made up: a channel of digital
  silence has `rms_dbfs: null`, not a floor like -120.

The version is `channel-inspection/1` (`CHANNEL_INSPECTION_VERSION`). A change to any
field, definition, threshold meaning or attribution rule below is a new version.

## Where it runs and its bounds

Both ingest paths (`POST /api/v1/uploads` and `POST /api/v1/voicemails`) call
`inspect_channels` right after `validate_media_upload` succeeds, inside the request. The
job stages are not involved.

- **Metadata** comes from the one existing ffprobe call. That call now also asks for
  `channel_layout`. There is no second probe.
- **Signal** comes from one extra bounded ffmpeg decode of the primary audio stream
  (`-map 0:a:0`) to signed 16-bit PCM at 8000 Hz. The channels stay separate (no `-ac`).
  - Window: the first `MEDIA_CHANNEL_ANALYSIS_SECONDS` (default 30, at most 120, 0 turns
    the analysis off).
  - Time: `MEDIA_CHANNEL_ANALYSIS_TIMEOUT_SECONDS` (default 10, at most 60). The decoder
    and its process group are killed at the deadline.
  - Memory: stdout is read up to `ceil(seconds * 8000) * channels * 2` bytes plus 64 KiB.
    More output is refused (`analysis_failed`).
  - CPU: one decoder thread; the measuring is C loops (`array`, `math.sumprod`). The
    duplicate check costs, per pair of non-silent channels, one aligned comparison over the
    window and, when that is not already a duplicate, `2 * lag + 1` comparisons on one
    block of at most 2 s, plus at most one comparison over the window at the best lag
    (only when the block already reaches the threshold).
  - Memory of the measuring: the channels stay compact `array('h')` (2 bytes per sample).
    Per pair, only the searched block of one channel and the searched region of the other
    (block plus `2 * lag` samples) are copied into `array('d')`; the whole-window check
    reads zero-copy `memoryview` slices. No per-lag Python list is built.
  - Measured on this development machine with independent noise (no pair is a
    duplicate, the most expensive case). Peak is `tracemalloc` during the measuring; the
    PCM bytes passed in exist before it starts and are not counted, the samples unpacked
    from them are. Time is the best of 3 runs without `tracemalloc`.

    | Case | Peak memory | Time |
    | --- | --- | --- |
    | 2 channels / 30 s / defaults | 2.6 MB | 0.06 s |
    | 8 channels / 30 s / defaults | 8.9 MB | 1.08 s |
    | 8 channels / 120 s / 20 ms lag (largest settings) | 34.5 MB | 3.53 s |

    The decoded input is `seconds * 8000 * channels * 2` bytes (3.8 MB for 8 channels and
    30 s).

    The Python measuring has no timeout of its own; it is bounded by these sizes.
- **Event loop.** `POST /api/v1/uploads` is an `async def` route: it runs the inspection
  with `starlette.concurrency.run_in_threadpool`, so the decode and the measuring do not
  block the event loop. The existing validator call is unchanged. `POST /api/v1/voicemails`
  calls the synchronous `backend.voicemail.ingest.import_voicemail`, which runs the
  inspection inline: the route (`routers/v1/voicemail.py`) is outside this ticket's files,
  and a synchronous function cannot hand work to the threadpool and wait for it without
  blocking anyway. Moving the whole import to a worker thread is a change of that route.

## The record

`channel_inspection_record(...)` returns one JSON object. It is stored once with the
ORIGINAL media object (see "Storage") and served as is on `GET /api/v1/meetings/{id}` as
`channel_inspection`. It is `null` there when no record is kept for the recording.

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | str | `channel-inspection/1` |
| `status` | str | `complete` or `unavailable` |
| `unavailable_reason` | str or null | why the analysis did not run (table below); null when complete |
| `channel_count` | int | channels of the primary audio stream, from ffprobe |
| `channel_layout` | str | the layout ffprobe reports, for example `stereo`, `mono`, `5.1(side)`, or `unknown`. A missing value or one outside `[A-Za-z0-9().+_ -]{1,120}` is `unknown`. It is never derived from the channel count. A 16-bit PCM WAV reports `unknown` on ffmpeg 8 |
| `sample_rate_hz` | int | sample rate of the primary stream, from ffprobe |
| `codec` | str | codec of the primary stream |
| `duration_seconds` | float | duration of the media, from ffprobe |
| `narrowband` | bool | `sample_rate_hz < 16000` (8 kHz telephony is narrowband and accepted) |
| `audio_streams` | list | every validated audio stream: `index`, `codec`, `channels`, `channel_layout`, `sample_rate_hz`, `duration_seconds` (float or null) |
| `analysis_seconds` | float or null | seconds of audio actually measured (whole 20 ms frames); null when unavailable |
| `analysis_sample_rate_hz` | int or null | `8000`; null when unavailable |
| `thresholds` | object | the values used: `frame_ms` (20), `active_threshold_dbfs`, `silent_max_active_share`, `duplicate_min_correlation`, `duplicate_max_lag_ms`, `narrowband_below_hz` (16000) |
| `flags` | list of str | sorted subset of `silent_channel`, `duplicated_channels`, `mono_as_stereo`; empty when unavailable |
| `duplicate_pairs` | list of [int, int] | duplicate channel pairs `[i, j]`, `i < j`, sorted; empty when unavailable |
| `mapping_source_type` | str | the recording's ingestion source type (`file_upload`, `voicemail`, ...) |
| `mapping_source_provider` | str | the recording's source provider (`console`, `api`, `voicemail`, ...) |
| `mapping_version` | int or null | version of the active mapping found for that source at ingest; null when there was none or it was unreadable |
| `mapping_digest` | str or null | SHA-256 of that mapping's content; null as above |
| `channels` | list | one entry per channel, index `0 .. channel_count - 1` (below) |

Each entry of `channels`:

| Field | Type | Meaning |
| --- | --- | --- |
| `index` | int | channel index in the stream |
| `rms_dbfs` | float or null | level over the window, `20 * log10(rms / 32768)`, 2 decimals; null for digital silence or when unavailable |
| `active_share` | float or null | share of 20 ms frames whose level is at least `active_threshold_dbfs`, 4 decimals; null when unavailable |
| `silent` | bool or null | `active_share <= silent_max_active_share`; null when unavailable |
| `duplicated` | bool or null | the channel is in a duplicate pair; null when unavailable |
| `role` | str | `agent`, `customer` or `unknown` |
| `attribution` | str | `trusted` or `unknown` |
| `reason` | str or null | why the role is unknown (table below); null only when trusted |

### Definitions

- **Frame**: 20 ms, 160 samples at 8000 Hz. A trailing partial frame is not measured.
- **Silent**: the share of active frames is at most `silent_max_active_share`. A quiet
  hiss below the threshold is silent and keeps its `rms_dbfs`.
- **Duplicate**: two channels that are both not silent and whose samples have an absolute
  Pearson correlation of at least `duplicate_min_correlation` at some lag within
  `duplicate_max_lag_ms` (default 5 ms = 40 samples at 8 kHz, either direction). A copy at
  another level (`R = 0.5 * L`), inverted (`R = -L`) or a few samples late is the same
  signal and a duplicate. Two different signals at the same level are not: samples are
  compared, not energy. A constant channel is never a duplicate. Silent channels are never
  compared, so two silent channels are `silent_channel` each and not a duplicate pair.
- **Lag search**: lag 0 is measured over the whole window first. If that is below the
  threshold, every other lag in the window is tried on one block (at most 2 s, the
  block with the most energy in both channels). The best lag counts only when the block
  reaches the threshold at it **and** the whole window (the overlapping samples) reaches
  it too. `duplicate_max_lag_ms = 0` compares aligned samples only.
- **mono_as_stereo**: the file has exactly two channels and they are a duplicate pair.

### Configuration

| Setting (env name) | Default | Range |
| --- | --- | --- |
| `MEDIA_CHANNEL_ANALYSIS_SECONDS` | 30.0 | 0 to 120 (0 disables) |
| `MEDIA_CHANNEL_ANALYSIS_TIMEOUT_SECONDS` | 10.0 | above 0, at most 60 |
| `MEDIA_CHANNEL_ACTIVE_THRESHOLD_DBFS` | -50.0 | -120 to below 0 |
| `MEDIA_CHANNEL_SILENT_MAX_ACTIVE_SHARE` | 0.01 | 0 to below 1 |
| `MEDIA_CHANNEL_DUPLICATE_MIN_CORRELATION` | 0.99 | above 0, at most 1 |
| `MEDIA_CHANNEL_DUPLICATE_MAX_LAG_MS` | 5.0 | 0 to 20 (0: aligned samples only) |

The narrowband limit (16000 Hz), the frame length and the analysis rate are fixed.

### Reason codes

`unavailable_reason`:

| Code | When |
| --- | --- |
| `analysis_disabled` | the analysis window is 0 |
| `analysis_tool_unavailable` | ffmpeg is not found or not runnable |
| `analysis_timeout` | the decode did not finish within the timeout |
| `analysis_failed` | non-zero exit, output over the byte cap, or another OS error (also for bytes ffmpeg cannot decode) |
| `analysis_empty` | less than one whole 20 ms frame decoded |

Channel `reason`. The first rule that applies wins:

| Order | Code | When | Applies to |
| --- | --- | --- | --- |
| 1 | `mono` | the file has one channel, whatever the mapping says (one channel holds both parties) | every channel |
| 2 | `mapping_invalid` | the stored mapping for this source cannot be read, or the workspace settings or their `channel_role_mappings` value are not a JSON object (never an error in the ingest request) | every channel |
| 3 | `no_mapping` | no active mapping for this recording's source | every channel |
| 4 | `channel_missing` | the mapping names a channel the file does not have | every channel |
| 5 | `analysis_unavailable` | `status` is `unavailable` | every channel |
| 6 | `channel_unmapped` | the mapping does not name this channel | that channel |
| 7 | `channel_silent` | this channel is silent | that channel |
| 8 | `channels_duplicated` | this channel is in a duplicate pair | that channel |
| - | null | trusted: `role` is the mapped role | that channel |

## The mapping

`backend.media.channel_roles`. A workspace administrator sets it with
`PUT /api/v1/admin/channel-role-mappings/{source_type}/{source_provider}`
(`{"channels": [{"channel": 0, "role": "agent"}, ...]}`), lists it with `GET`, removes it
with `DELETE`. It needs `workspace:manage`, a session (never an API key) and CSRF. Every
change is audited as `workspace.settings.updated`.

- A source is `(source_type, source_provider)` inside one workspace. A mapping for one
  source never applies to another.
- One or more entries; a channel `0..7` at most once; each of `agent` and `customer` at
  most once. One channel alone is valid; the other channel is then `channel_unmapped`.
- It is stored in `Workspace.settings_json["channel_role_mappings"]` with a `version`
  (grows by one on every set or removal of that source; never reused) and a `digest`
  (SHA-256 of the source and the channels in channel order).
- A body that is not a mapping (also JSON `null` or an empty body) is
  `422 invalid_channel_role_mapping`. A body that is not valid JSON is the app's
  `422 validation_error`.
- **Unreadable stored state.** If the workspace settings, or their whole
  `channel_role_mappings` value, are not a JSON object, `PUT` and `DELETE` refuse with
  `409 channel_role_mappings_unreadable` and change nothing: replacing the value would drop
  every other source's mapping and restart the version numbers. `GET` lists nothing then.
  It needs a repair by an operator. A single unreadable entry of one source is hidden by
  `GET`; `DELETE` removes it (`204`, audited as `removed` with `mapping_digest: null`,
  empty `channels`, and its stored version as `previous_version` when it has one), and
  `PUT` replaces it.

## Storage

`backend.media.channel_store` is the only code that knows where the record lives:
`store_channel_inspection(db, *, media_object, record)` and
`load_channel_inspection(db, *, recording)`. The record lives in the write-once JSON column
`media_objects.channel_inspection` (owner decision, migration `a7c3e9f5b2d1`). The store sets
it on the ORIGINAL media object before the insert. It loads it by media object id and
workspace. Meeting detail serves it as `channel_inspection`.

The column is a content column of `MediaObject`: it is not in `MUTABLE_MEDIA_COLUMNS`, so the
ORM guard refuses any change after the insert (`MediaImmutableError`). Like the other content
columns, this is an ORM guard; a raw SQL `UPDATE` is not refused by the database.

`channel_inspection` is `null` on meeting detail for objects registered before the migration,
and when the ingest code passed no inspection. A window of 0 still gives a record
(`analysis_disabled`).

The record is attributed and written once, in the ingest transaction, with the mapping read
in that transaction. A later mapping change never rewrites it: the record keeps the
mapping version and digest it used.

## What is not claimed

- No speech detection, speaker identity, diarization quality or role accuracy. The F25
  accuracy gate (at least 98 % role accuracy on an agreed set) is `blocked_external` until
  WP13 data exists.
- The thresholds are tested on synthetic `lavfi` audio only. An echo with its own
  filtering or a codec with heavy inter-channel processing can fall under the correlation
  threshold and is then not flagged.
- Only the primary audio stream is measured. Other audio streams appear in
  `audio_streams` only.
- Not detected: **swapped channels** (agent and customer on the opposite channels from the
  mapping), **mid-call transfers** (another person takes over a channel), and
  **crosstalk** (both parties mixed on both channels without one channel being a copy of
  the other). The mapping is trusted as stated for every recording of its source.
- A copy delayed by more than `duplicate_max_lag_ms`, or one that only matches outside the
  searched block, is not flagged.
- `file_upload:console` is a weak source identity: anyone with `meeting:create` can upload
  a file that the mapping then trusts. The mapping is meant for real telephony sources.
