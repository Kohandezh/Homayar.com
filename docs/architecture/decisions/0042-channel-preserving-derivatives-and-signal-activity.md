# ADR 0042: Channel-preserving derivatives, signal activity and the original-time map

Date: 2026-10-26 · Status: Accepted 2026-10-28, decided by the owner (HOM-38, WP09-T02, parent HOM-13, features F25, F26, F27 and F28) · Builds on: ADR 0017, ADR 0039

## Owner ruling (2026-10-28)

Decided by the owner on 2026-10-28. The status is Accepted.

- R3: the size cap is 200 MB per derivative. It replaces the storage cap named in the R3 row
  below. The cap is code, so it is not in this ADR change. It is implemented in HOM-314.
- R7: `MEDIA_DERIVATIVES_ENABLED` stays false until HOM-313 (W1) and HOM-314 (W2) are
  merged. After that it may be turned on. This is the owner decision that decision 9 waits
  for.

## Context

Later work needs three things for each recording. T03 (model profiles) and T04 (chunking
for ASR) need audio in one known format. Chunking needs to know where each channel carries
signal. And every result needs to map back to the time of the original file exactly.

Before this change Homayar kept only the ORIGINAL media object. `backend/speech/audio_profile.py`
can build an ffmpeg command, but it mixes to mono (`-ac 1`) and can apply `loudnorm`, and the
pipeline does not use it. No VAD model is in the base install (faster-whisper and its Silero
VAD are only the optional `ai-local` extra), so any activity measure here must be a
signal-level heuristic.

Parent acceptance that touches this work: F26 asks that the original timeline mapping stay
correct through resampling and chunking, and that quality flags are exposed. F25 asks that
stereo alone never establishes identity.

T02 delivers the exact original timeline through resampling, the map format that chunking
extends, per-channel activity for boundary-safe cuts, and quality facts in the record
(narrowband, the HOM-37 flags, an unknown noise floor). It leaves chunking and "no speech
loss over 250 ms at boundaries on annotated regression fixtures" to T04 (it needs the
chunker and annotated fixtures), "empty, music or noise do not fabricate a transcript" to T04
and ASR, exposing quality flags on an API or the console to T04 or later, and model profiles
to T03.

## Decision

1. **One multichannel `NORMALIZED` derivative per ORIGINAL.** A plain PCM WAV, signed 16-bit,
   16 000 Hz, every channel of the primary audio stream in its order. Derivative channel `i`
   is original channel `i`. The WAV uses format tag 1 with no channel mask, so no reader
   infers speaker positions. `CHANNEL_DERIVED` stays free for a later per-channel extract.
2. **No mixing, no gain.** No `-ac`, `pan`, `amix` or `amerge`; no `loudnorm`, `dynaudnorm` or
   `volume`. `loudnorm` was measured to output 192 kHz and change the sample count, so it
   cannot keep an exact map.
3. **Pinned resampler.** `aresample` with the swr defaults written out. On ffmpeg 6.1.1 (the
   CI build) and 8.1.1: zero sample shift, an output length between `floor` and `ceil` of
   `N_o * 16000 / R_o`, and a 16 kHz 16-bit source passed through unchanged. The derivative
   keeps exactly the samples the resampler emits. The bytes differ between the two builds,
   so nothing depends on byte equality.
4. **Original time** (`decoded-primary-audio/1`) is the decoded sample index of the primary
   audio stream, from the first decoded sample. A container start offset is ignored. A
   signalled encoder delay is removed by the decoder. An unsignalled one stays in the
   timeline and is not guessed.
5. **Exact map** (`time-map/1`). Derivative sample `k` is original sample position
   `(k + offset) * R_o / 16000`. Both directions are `Fraction` arithmetic, so round trips
   are exact. The record holds integers only. A chunk is the same map with an offset.
6. **Signal activity** (`signal-activity/1`, kind `signal_level_heuristic`). 20 ms frames, AC
   energy, a per-channel noise floor (10th percentile), `on = max(-50, floor + 12)` dBFS,
   6 dB hysteresis, gaps under 10 frames merged, runs under 5 frames dropped. Intervals are
   half-open derivative sample ranges, not padded. It is never called speech and never sets
   a role.
7. **Storage: inside the derivative** (no migration). The record
   `audio-derivative/1` is canonical JSON in one `hmyr` chunk before `data`. It has exactly
   the derivative's lifecycle (retention, legal hold, deletion, purge), tenant (the tenant
   key prefix and the media row) and integrity (the object's SHA-256).
8. **Where it runs.** In the job, right after MATERIALIZE, as a step that never raises and
   adds no stage record. ASR still gets the original file; transcripts, checkpoint
   identities, stage runs, provider usage and meeting status do not change. Registration is
   fenced by the job lease and serialized on the ORIGINAL row lock, so a retry or a second
   job never makes a second derivative.
9. **Off by default.** `MEDIA_DERIVATIVES_ENABLED=false`. With the default setting, HOM-38
   produces nothing in production. Enabling it waits for W1, W2 and an owner decision.

### Product rules (defaults chosen here, for the owner)

| # | Question | Default |
| --- | --- | --- |
| R1 | Loudness or gain normalization of the derivative? | None (gain 1). T03 decides model input gain. A constant per-channel gain would keep every sample in place, but it is a new version. |
| R2 | What does a narrowband source become? | 16 kHz by upsampling; the record keeps `narrowband: true`; no bandwidth is made up. |
| R3 | Derivative size cap and long calls | Keep the existing per-object storage cap. Above it: no derivative, reason `derivative_too_large`. See the cost table below. |
| R4 | Derivative of a held ORIGINAL | Not made (`source_legal_hold`). W1 is the separate fix. |
| R5 | Workspaces that delete audio after processing | Not made (`audio_deleted_after_processing`). W2 is the separate fix. |
| R6 | HOM-37 "silent" (first 30 s) against full-length activity | Measure every channel over the whole derivative, copy the HOM-37 flags, and never remove an interval because of them. A truly silent channel gets no interval by itself. |
| R7 | Enabled by default? | No (`MEDIA_DERIVATIVES_ENABLED=false`). |
| R8 | Duplicated channels | Both are measured; `duplicate_of` names the other channels; none is dropped. T04 decides what to transcribe. |
| R9 | Encoder delay the file does not signal | Part of the original timeline; not estimated, not removed. |
| R10 | Serve activity on the API? | Not in T02. The new `NORMALIZED` object does appear in the existing `GET /api/v1/meetings/{id}/media` list, which lists every object of a meeting. |
| R11 | Mono ORIGINAL | A one-channel derivative and activity. Roles stay `unknown` (HOM-37 reason `mono`). |
| R12 | Where a failure reason lives | The audit event `media.derivative_unavailable` (target the ORIGINAL; details `producer`, `reason`, `job_id`), for operators only, plus a log line. Consumers (T03, T04, an API) treat "no derivative" as unknown. There is no read function in v1. |

**R3, the cost of the size cap.** A recording without a derivative has **no activity and no
time map**; T04 must treat it as unknown. The longest call that gets a derivative, per
channel count, for each option (4 MiB is reserved for the record; the longest accepted upload
is 4 hours):

| Option | 1 channel | 2 channels | 8 channels | Code it would touch | Cost |
| --- | --- | --- | --- | --- | --- |
| Current storage cap (default 250 MiB, chosen) | 2 h 14 min | 67 min | 16.8 min | none | up to 250 MiB of storage and backup per derived recording |
| A dedicated derivative cap at the size-column limit (2 GiB) | 4 h | 4 h | 2 h 19 min | `backend/storage/factory.py` (a cap argument), a new setting, the derivation | up to 2 GiB per recording, in storage and in backups |
| 8 kHz output for 8 kHz sources | 4 h | 2 h 14 min | 33.6 min (narrowband sources only; others as now) | the derivation (rate choice), a new activity version (160-sample frames) | T03 must upsample for 16 kHz models; two derivative formats |
| One object per channel (`CHANNEL_DERIVED`) | 2 h 14 min | 2 h 14 min | 2 h 14 min | the derivation (one writer per channel), the job (C registrations), the record (channel index per object) | the same total bytes; C objects per recording; T04 reads C files |
| FLAC instead of PCM | about 4 h (estimate) | about 2 h 15 min (estimate) | about 34 min (estimate) | the derivation (an encoder step), the record container (a FLAC metadata block or a sidecar) | no byte-offset reads; noise compresses badly; not measured here |

Raising the cap is an owner decision. It is listed in the pull request.

### Separate work units (named here, not fixed here)

- **W1.** `register_derived` creates a derived object `ACTIVE` even when its source is under
  legal hold. The meeting hold route can also miss a derived row inserted at the same time:
  a hold call that waits on the ORIGINAL row lock does not see a derivative inserted by the
  transaction that held the lock (shown on PostgreSQL). HOM-38 skips held originals, but
  this race remains until W1.
- **W2.** Delete-after-processing (`backend/jobs/processor.py`, the completion request and
  the post-success removal) requests deletion only of the ORIGINAL. It must cover every live
  object of the meeting. HOM-38 skips such workspaces, but a workspace that turns the setting
  on after derivatives exist keeps them.

## Consequences

- When enabled, every processed recording within the cap gets one derivative whose activity
  and map T03 and T04 can read with `parse_derivative_header`, selected by the exact
  `producer` `audio-derivative/1`. v1 never retires an object.
- Storage grows by up to the cap per derived recording, and backups grow with it.
- A failure never fails an upload or a job; the reason is in the audit log for operators.

### Limits

- That every decoder sees the same timeline is an **inference**: HOM-37 and this derivation
  decode with ffmpeg and the local faster-whisper adapter decodes with PyAV. Remote ASR
  providers are not verified.
- The probed rate is used as `R_o`. A stream whose real rate differs from the probe (for
  example HE-AAC signalling) or that changes rate in the middle is not detected.
- The probed channel count (the ORIGINAL row) is used as the stride to split the decoded
  samples into channels. A decoded channel count that differs from the probe (for example a
  layout change in the middle of a stream) is not detected: the channels would be split
  wrongly without an error.
- A format tag 1 WAV with more than 2 channels is not `WAVE_FORMAT_EXTENSIBLE`. ffmpeg and
  Python's `wave` read it; other readers are not checked.
- Inter-sample peaks of a source near 0 dBFS can clip at the 16-bit limit.
- `record_too_large` (a record over 4 MiB) cannot happen at the default cap, but it can with
  a raised `max_upload_size_mb`; the guard stays.
- The legal hold race of W1 is not fixed here.
- Activity thresholds are tested on synthetic `lavfi` audio only. Music, noise bursts and
  crosstalk are active; a channel active most of the time can lose its quieter parts.

## Alternatives considered

- **A stage checkpoint artifact for the record.** Refused: it is tied to the meeting, not to
  the media object, it needs a fake provider usage row from the shared checkpoint layer, and
  it survives the deletion of the audio.
- **A new write-once JSON column `media_objects.derivation`.** Not chosen: it needs a
  migration, its immutability is an ORM guard only, and the activity timestamps would outlive
  the deleted audio on the `DELETED` row.
- **A new table for derivation results.** Not chosen: a migration, a new RLS policy and new
  purge rules for the same data.
- **One mono derivative per channel.** Not chosen for v1: it needs a channel index without a
  column and splits one clock into several files.
- **Feed ASR the derivative now.** Refused for T02: it would change transcripts, checkpoint
  identities and provider inputs, which belong to T03 and T04.
