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

# Derivatives and signal activity (`backend.media.derivatives`, `backend.media.activity`, `backend.media.time_map`)

HOM-38 (WP09-T02, features F25 to F28, ADR 0042). For a recording's ORIGINAL media object,
the job makes one channel-preserving normalized derivative, measures signal activity per
channel, and keeps an exact map between derivative samples and original time. The activity
and the map are stored **inside the derivative's own bytes**. There is no migration.

**With the default setting, HOM-38 produces nothing in production.** W1 (HOM-313) and W2
(HOM-314) are fixed. The owner ruled on 2026-10-28 that it may be turned on once HOM-313 and
HOM-314 are merged (ADR 0042, owner ruling). The setting is `MEDIA_DERIVATIVES_ENABLED`
(default `false`).
W1 and W2 are described in "What is not claimed" below.

- **Activity is a signal-level heuristic**, not speech detection. "Active" means "this
  channel carries signal above its own noise floor". It never sets or implies a role. Roles
  come only from the HOM-37 trusted mapping.
- Channels are never mixed: no `-ac`, `pan`, `amix` or `amerge`, and no downmix in Python.
  There is no gain: no `loudnorm`, `dynaudnorm` or `volume`.
- Unknown stays unknown. A recording without a derivative has **no activity and no time
  map**. T03, T04 and any API must treat it as unknown.

Versions: the derivative and its record are `audio-derivative/1` (also the media object's
`producer`), the map is `time-map/1` over the original timeline `decoded-primary-audio/1`,
and the activity is `signal-activity/1` (kind `signal_level_heuristic`). A change to any
field, definition or threshold below is a new version.

## Where it runs

`backend/jobs/processor.py` calls `derive_audio_for_job` (`backend/jobs/audio_derivatives.py`)
once per attempt, right after MATERIALIZE and before transcribing, on the materialized
ORIGINAL (its bytes are already checked against the stored digest). It never raises. It adds
no stage record. ASR still receives the original file, so transcripts, checkpoint
identities, stage runs, provider usage and meeting status do not change. It returns
`registered` or a reason code.

1. **Pre-checks**, read-only, before any decode. The first that applies wins:
   `derivatives_disabled`, `source_not_registered`, `source_not_live`, `source_changed`,
   `source_facts_unknown`, `already_derived`, `derivative_too_large`.
2. **The derivation** (`derive_audio`) in the job's temporary directory, then an upload under
   a random storage key, so each attempt owns its object.
3. **The fenced registration**: one transaction proves the job lease
   (`lock_owned_or_cancelled`), locks the meeting row and the ORIGINAL row, checks
   `source_not_live`, `source_changed` and `already_derived` again, and then calls
   `register_derived` (which audits `media.registered`). A stale attempt can upload bytes but
   can never register them. A held ORIGINAL is derived; its derivative is created held (see
   below).

On a known refusal (a re-checked pre-check reason, a lost lease, a cancelled job, a deleted
meeting, a unique-key conflict, a refusal from `register_derived`) the attempt removes the
object it uploaded. On any other commit error the bytes stay, and the orphan sweep
(`backend/jobs/media_reconciliation.py`) reports them.

The registered object: `kind = NORMALIZED`, `derived_from_id` = the ORIGINAL,
`producer = "audio-derivative/1"`, `content_type = "audio/wav"`, `container_format = "wav"`,
`channel_count` = the original's, `sample_rate_hz = 16000`,
`duration_seconds = ceil(samples / 16000)`, `job_id` = the job. Organization, workspace,
meeting, ingestion and `retention_expires_at` come from the ORIGINAL. Retention, legal hold,
conversation purge and integrity checks act on it like on any media object of the meeting.
A legal hold follows lineage (HOM-313): the derivative of a held ORIGINAL is created held, in
the same transaction, with the ORIGINAL's owner, reason and review date (audit
`media.legal_hold_inherited`); a hold by id also covers the object's descendants that are
not held yet. A release by id covers only the descendants whose hold was inherited from the
released object, or from a descendant released with it (HOM-329). A hold placed directly on a
derived object (for another matter, or by the meeting hold) stays until it is released by its
own id; the release writes `media.legal_hold_kept` for it. The row does not store where its
hold came from, so the release reads the audit trail: every `media.legal_hold_placed` and
`media.legal_hold_inherited` event records the row's exact `legal_hold_at`. A hold is
inherited only when an inherited event matches that time exactly and no placed event does.
Any other hold counts as direct and stays, also when the evidence is missing or unclear. A
direct hold request on a row whose hold is inherited makes that row's hold direct. This is
true for a hold by id and for the meeting hold, also when the request is repeated. A later
release of the row's source then keeps the row held. Holds inherited before HOM-329 have no
`legal_hold_at` in their event. They count as `unknown` and stay held when their source is
released. Deploy note: in any environment where derivatives were ever on, release such rows
by their own id. A reader selects a derivative by the exact `producer`. v1 never retires an
object.

### Reason codes

Every reason except `derivatives_disabled`, `already_derived` and `stale_attempt` is written
as the audit event `media.derivative_unavailable` (target type `media`, target id the
ORIGINAL, details exactly `producer`, `reason`, `job_id`), under the job's fence. It is for
operators only. Every outcome except `registered` and `derivatives_disabled` is also logged
as `MEDIA_DERIVATIVE_UNAVAILABLE media_id=<id> reason=<code>`, with no key, path or filename.
There is no read function for these reasons in v1. Audit rows written before HOM-313 may
still carry `source_legal_hold` (the ORIGINAL was under legal hold), and audit rows written
before HOM-314 may still carry `audio_deleted_after_processing` (the workspace deleted audio
after processing); nothing writes them now.

| Code | Stage | When |
| --- | --- | --- |
| `derivatives_disabled` | pre-check | the setting is off (not audited, not logged) |
| `source_not_registered` | pre-check, registration | the recording has no ORIGINAL media object (written before the media ledger); the audit target id is null |
| `source_not_live` | pre-check, registration | the ORIGINAL is not active, retention-pending or held, or it is held with a recorded deletion request (the hold only delays that deletion) |
| `source_changed` | pre-check, registration | the ORIGINAL's digest is not the digest the job claimed |
| `source_facts_unknown` | pre-check | the ORIGINAL row has `channel_count` null or outside 1 to 8, or `sample_rate_hz` null or not above 0 (backfilled rows); not re-checked under the lock (content columns are immutable) |
| `already_derived` | pre-check, registration | a derivative of this ORIGINAL with this producer exists and is not `DELETED` (not audited) |
| `derivative_too_large` | pre-check, run | the predicted or the produced size is over the cap |
| `derivative_tool_unavailable` | run | ffmpeg is not found or not runnable |
| `derivative_timeout` | run | the decode did not finish within `MEDIA_DERIVATIVE_TIMEOUT_SECONDS` |
| `derivative_failed` | run | non-zero exit, output that is not whole samples, an OS error, bytes ffmpeg cannot decode, or an unexpected error |
| `derivative_empty` | run | less than one whole 20 ms frame (320 samples) was decoded |
| `record_too_large` | run | the record JSON is over 4 MiB (reachable only with a raised cap, see Size) |
| `storage_failed` | run | the upload or its digest check failed |
| `stale_attempt` | registration | the attempt lost its lease (log only: a stale attempt writes nothing) |
| `job_cancelled` | registration | the job was cancelled while the attempt ran |
| `meeting_deleted` | registration | the meeting was deleted while the attempt ran |
| `storage_conflict` | registration | the insert hit a unique key |
| `tenant_mismatch` | registration | `register_derived` refused another tenant's object |

## The derivative

One ffmpeg decode of the primary audio stream (`-map 0:a:0`) to signed 16-bit PCM at 16 kHz,
every channel kept in its order. The resampler is pinned to the swr defaults of ffmpeg 6.1
and 8.1: `aresample=osr=16000:resampler=swr:filter_size=32:phase_shift=10:linear_interp=1:exact_rational=1:dither_method=0:async=0`.
Checked on both builds (ADR 0042): zero sample shift, and a length between `floor` and
`ceil` of `N_o * 16000 / R_o`. The derivative keeps exactly the samples the resampler emits:
never padded, never trimmed. A 16 kHz 16-bit source passes through unchanged. An 8 kHz
(narrowband) source is upsampled; the band above 4 kHz is empty and the record says
`narrowband: true`.

The file is a plain PCM WAV written by our code: `RIFF`/`WAVE`, a 16-byte `fmt ` chunk
(format tag 1, 16 000 Hz, 16 bits, no channel mask, so no reader infers speaker positions),
one `hmyr` chunk with the record as canonical JSON padded with one space to an even length,
then `data`. Sample `k` of channel `c` is at byte `data_offset + 2 * (C * k + c)`.
`parse_derivative_header` reads the record back; any other shape raises
`DerivativeRecordInvalid` (code `derivative_record_invalid`).

**Size.** The cap is `MEDIA_DERIVATIVE_MAX_SIZE_MB` in MiB (default 200, that is
209 715 200 bytes; owner ruling R3, HOM-314). It is never more than the storage cap of every
object, `max_upload_size_mb * 1024 * 1024` (default 262 144 000 bytes), or 2 147 483 647
(`media_objects.size_bytes` is a 32-bit integer). The same cap bounds the decode and the
upload. The size is about `32 000 * C` bytes per second plus the record. With the default
cap the longest call that gets a derivative is about 6 422 s for 1 channel, 3 211 s for 2
channels and 802 s for 8 channels (4 MiB is reserved for the record). The size is checked
before the decode from the probed duration (`ceil(duration) * 16000 * C * 2 + 4 MiB + 64`,
no ffmpeg run when over) and again while reading. Over the cap is `derivative_too_large`,
never a truncated derivative.

**Bounds.** The decode has one thread, the probe limits of the upload validator, and the
deadline `MEDIA_DERIVATIVE_TIMEOUT_SECONDS` (default 300, above 0 and at most 1800); at the
deadline its process group is killed. The PCM streams through the activity meter in blocks;
the meter keeps one float per channel and 20 ms frame. Disk: one temporary PCM file and the
WAV, both at most the cap, in the job's private temporary directory.

| Setting (env name) | Default | Range |
| --- | --- | --- |
| `MEDIA_DERIVATIVES_ENABLED` | `false` | `true` or `false` |
| `MEDIA_DERIVATIVE_TIMEOUT_SECONDS` | 300 | above 0, at most 1800 |
| `MEDIA_DERIVATIVE_MAX_SIZE_MB` | 200 (MiB) | 1 to 2047; `max_upload_size_mb` still applies |

## The time map (`time-map/1`)

**Original time** (`decoded-primary-audio/1`): sample index `n` of the primary audio stream
as ffmpeg decodes it with default decoder settings, counted from the first decoded sample. A
container start offset is ignored. An encoder delay the file signals (MP3 LAME tag, MP4 edit
list, Opus pre-skip) is removed by the decoder; one it does not signal stays in the timeline
and is never guessed. Original seconds are `n / R_o`, with `R_o` the probed rate.

`TimeMap(original_rate_hz, derivative_rate_hz=16000, offset=0)`:

- `to_original(k) = (k + offset) * R_o / R_d` and `to_derivative(n) = n * R_d / R_o - offset`,
  both as `Fraction`, so every round trip is exact. Floats and bools are refused.
- `original_seconds(k) = (k + offset) / R_d`.
- `original_cover(start, end)`: the smallest whole range of original samples that holds a
  derivative interval.
- `shared_grid()`: `(R_d / g, R_o / g)`, `g = gcd(R_o, R_d)`; on this grid both sides are
  whole numbers (10 ms at 44 100 Hz, 40 ms at 11 025 Hz).
- `child(offset)`: the map of a chunk `[a, b)` (offset `a`); offsets add, so T04 chunks need
  no new format.
- `as_record(sample_count)` / `from_record(record)`: integers only.

## The activity (`signal-activity/1`)

Each channel is measured on its own over the whole derivative. A frame is 20 ms (320
samples, no overlap); a trailing partial frame is not measured.

- Frame AC energy `E = sum(x^2) - (sum x)^2 / 320` from exact integer sums. `E = 0` (digital
  silence or a constant DC channel) has no level and is never active. The level is
  `10 * log10(E / (320 * 32768^2))` dBFS.
- Noise floor: the frame levels sorted ascending ("no level" first); the level at index
  `(frames - 1) * 10 // 100`; `null` when that frame has no level.
- `on_dbfs = max(-50.0, floor + 12.0)` (or `-50.0` without a floor); `off_dbfs = on_dbfs - 6.0`.
- A run starts on a frame at or above `on_dbfs` and continues while the level stays at or
  above `off_dbfs`. Gaps of fewer than 10 frames are merged, then runs of fewer than 5 frames
  are dropped.
- Intervals are half-open `[start, end)` in derivative samples, multiples of 320, not
  padded. `padded(intervals, pad_samples, sample_count)` widens, clamps and merges for a
  consumer; the record recommends 1600 samples (100 ms).
- The HOM-37 flags (`silent`, `duplicated`) are copied per channel and **never remove an
  interval**: HOM-37 measured only the first seconds of the file. A channel that is truly
  silent gets no interval by itself. Duplicated channels are both measured.

## The record (`audio-derivative/1`)

Canonical JSON (sorted keys, no spaces, ASCII, no NaN), no timestamps, no job or attempt ids.

| Field | Meaning |
| --- | --- |
| `version` | `audio-derivative/1` |
| `source` | `media_id`, `sha256`, `channel_count`, `sample_rate_hz` of the ORIGINAL; `codec` from the HOM-37 record (or null); `narrowband` (`sample_rate_hz < 16000`) |
| `format` | `container` `wav`, `codec` `pcm_s16le`, `sample_rate_hz` 16000, `channel_count`, `channel_map` (`[0, .., C-1]`: derivative channel `i` is original channel `i`), `gain` `none`, `resampler` (the pinned option string) |
| `time_map` | `version`, `original_timeline`, `original_sample_rate_hz`, `derivative_sample_rate_hz`, `derivative_offset_samples` (0), `derivative_sample_count`, `shared_grid` |
| `inspection_status` | `complete`, `unavailable` or `missing`: the HOM-37 record of the ORIGINAL |
| `activity` | `version`, `kind` (`signal_level_heuristic`), `frame_samples` (320), `measured_samples`, `params` (`floor_dbfs`, `margin_db`, `hysteresis_db`, `noise_percentile`, `min_activity_frames`, `min_gap_frames`, `recommended_padding_samples`), `channels` |

Each `activity.channels` entry: `index`, `source_channel` (equal to `index`),
`noise_floor_dbfs` (2 decimals, or null), `on_dbfs`, `off_dbfs`, `active_frames`, `intervals`
(list of `[start, end]`), `inspection_silent` and `inspection_duplicated` (bool, or null when
the HOM-37 record is not complete), `duplicate_of` (the other channels of HOM-37 duplicate
pairs; null when `inspection_duplicated` is null).

## Lock order (HOM-330)

Every transaction that does media work takes its row locks in this order. It never waits
for a row that comes earlier in the list while it holds a row that comes later. So two such
transactions cannot wait for each other, and PostgreSQL never has to abort one of them.

1. **The processing job row.** A job step proves its lease by locking its job row before
   anything else (`lock_owned_or_cancelled`, `owned_job`), so a stale attempt writes
   nothing. `DELETE /meetings` cancels the conversation's job, so it locks the job row first
   too; the tombstone reapply locks the queued and running job rows of the conversation
   (ascending `id`) before the meeting row. It reads the active jobs again after the meeting
   lock; when a job became active in between, it rolls back and starts again with that job
   row first (at most three passes).
2. **The meeting row**, before any media row of that meeting. The mode depends on the path:
   - `FOR UPDATE`: the derivative registration (`register_derived`), the purge attempt, the
     retention expiry, the tombstone reapply and the job's publication step;
   - `FOR NO KEY UPDATE`: `DELETE /meetings` (it writes the meeting row, its tombstone, in
     the same transaction);
   - `FOR KEY SHARE`: the hold and release paths, every byte removal (`delete_objects`) and
     every integrity check (the reconciliation sweep's missing check and
     `POST /meetings/{id}/media/{id}/verify`).

   `FOR UPDATE` conflicts with all three, so a registration or a purge attempt runs alone on
   its conversation. `FOR KEY SHARE` conflicts with neither itself nor `FOR NO KEY UPDATE`,
   so a hold, a byte removal and a conversation deletion of one conversation can run at the
   same time; they meet at the media rows, in the order of point 3. A transaction that locks
   the meeting rows of several conversations (the reconciliation sweep, for its byte removal
   and for its missing check, through `lock_media_rows`) locks them in one statement, in
   ascending `id`.
3. **Media rows**, in ascending `(created_at, id)`, in one statement. Both columns are fixed
   when the row is registered, so the order of two rows never changes. It is the same order
   at every depth of lineage: a hold or a release by id locks the object and all its
   descendants in one statement, not level by level. A path that changes several rows reads
   them without a lock, locks them all, and then decides on the fresh rows. An object without
   a meeting row (`meeting_id` null) has no meeting lock to take; its rows follow the same
   order, with one exception: a descendant registered while a hold or release by id waited
   for its locks is found only after them and is locked in a further statement. A job cannot
   do that (a job is deleted with its meeting), only a direct call of `register_derived`.
4. **The conversation purge row.** It records work on the state above, so it comes last: a
   tombstone inserts it after the media deletion requests, and a release makes a deferred
   purge due (`nudge_purge`) after the released rows. The purge attempt reads it first
   without a lock, to find the meeting and to skip a purge that is not due. Then it locks the
   meeting row and the media rows, and only then claims the purge row
   (`FOR UPDATE SKIP LOCKED`). Its last step locks only the purge row.

Usage reservation and entitlement rows (released or settled when a job ends) are locked after
the meeting row and before media rows, by the job's completion and by `DELETE /meetings`
alike.

A release never loses its nudge: the purge attempt decides "deferred" under its meeting
`FOR UPDATE` lock, and the release nudges under its meeting lock. One of the two commits
first, and the other sees it.

## What is not claimed

- No speech detection, no speaker identity, no role. Music, noise bursts, hold tones and
  crosstalk are active. A channel active most of the time gets its floor inside its own
  signal, so quieter parts can be missed. The thresholds are tested on synthetic `lavfi`
  audio only.
- That every decoder sees the same timeline is an inference: HOM-37 and this derivation
  decode with ffmpeg, and the local faster-whisper adapter decodes with PyAV (the ffmpeg
  libraries). Remote ASR providers are not verified.
- The probed rate is used as `R_o`. A stream whose real rate differs from the probe (for
  example HE-AAC signalling) or that changes rate in the middle is not detected.
- The probed channel count (the ORIGINAL row) is used as the stride to split the decoded
  samples into channels. A decoded channel count that differs from the probe (for example a
  layout change in the middle of a stream) is not detected: the channels would be split
  wrongly without an error.
- Inter-sample peaks of a source near 0 dBFS can clip at the 16-bit limit.
- A format tag 1 WAV with more than 2 channels is not `WAVE_FORMAT_EXTENSIBLE`. ffmpeg and
  Python's `wave` read it; other readers are not checked.
- Derivative bytes differ between ffmpeg builds (6.1 and 8.1 give other bytes for the same
  input). Nothing depends on byte equality across builds.
- Long calls get no derivative at the default cap (see Size). Raising the cap is an owner
  decision (ADR 0042, R3).
- **W1** (fixed by HOM-313): a legal hold follows lineage. `register_derived` locks the
  meeting row, then the source row, reads the source again, and creates the derived object
  of a held source held in the same transaction (`media.legal_hold_inherited`). It refuses a
  held source with a recorded deletion request (`source_not_live`). The meeting hold covers
  every object of the meeting that is not deleted; a hold by id also covers the object's
  descendants, and a release by id covers the descendants whose hold was inherited (HOM-329,
  see the lineage paragraph above). Known limit (HOM-331): a row has one hold at a time. A
  hold by id does not re-point a descendant that is already held, so when that descendant's
  own hold is released later, the newer hold on an ancestor does not cover it. Registration
  and every hold path lock the meeting row before any media row and read the media rows
  after that lock, so a hold never misses a derived row
  inserted at the same time (an object without a meeting has no meeting row; then the source
  row lock serializes them). These paths, the reconciliation sweep, the purge attempt and
  `DELETE /meetings` all follow one lock order (see "Lock order" above, HOM-330). So a hold or
  a release by id does not deadlock with the sweep, a release does not deadlock with a purge
  attempt, and the derivative registration does not deadlock with `DELETE /meetings`.
- **W2** (fixed by HOM-314): delete-after-processing covers every media object of the
  meeting, derived ones included, also those made before the workspace turned the setting
  on. The completion transaction records the `processed` deletion request on every object
  that is not deleted, in the same commit as COMPLETED, after its meeting row lock; a worker
  killed later leaves `deletion_pending` rows that the reconciliation sweep closes. The
  post-success step locks the meeting row (`FOR KEY SHARE`, like the hold paths), records the
  request again (idempotent) and removes the bytes of every pending row through
  `delete_objects`, which locks the meeting row and then every row, and reads each row again,
  before the first removal. A held object keeps its bytes
  and its recorded request; a release by its id resumes the request. Such workspaces are
  derived too: the `audio_deleted_after_processing` pre-check is gone. After the request
  nothing more is derived from the meeting: `register_derived` also checks the ORIGINAL at the
  root of the source's lineage, and refuses (`source_not_live`) when it is not live or is held
  with a recorded deletion request. When the source is not held but that ORIGINAL is, the new
  object takes the ORIGINAL's hold.
