# ADR 0046: Long-call ASR chunking, chunk checkpoints and overlap deduplication

Date: 2026-10-30 · Status: Proposed (HOM-40, WP09-T04, parent HOM-13, feature F26) · Builds on: ADR 0042, ADR 0044, the stage checkpoints of WP06-T03

## Context

The ASR stage sends the whole recording to the provider in one request. For a long call
this has three problems.

- **One failure loses everything.** A provider failure late in a two-hour call throws away
  all the work before it. The retry starts from zero.
- **One request is long.** The whole call runs under one stage timeout, and the lease has no
  natural point to be checked.
- **No boundaries for speakers.** HOM-45 and HOM-46 reconcile speakers across parts of a long
  call. They need to know where the parts are.

F26 asks that the original timeline stays correct through chunking, that no speech is lost at
the boundaries (more than 250 ms on annotated fixtures), and that empty, music or noise audio
does not produce a made-up transcript. ADR 0044 decision 5 left chunking to this work.

## Decision

### 1. Short calls do not change

A call that is not longer than the threshold runs exactly as before: one `transcribe` call with
the materialized ORIGINAL, the same checkpoint identity (no new keys), the same artifact token
and the same artifact bytes. The same holds for every call when chunking is off.

The job first checks the recorded length (`recordings.duration_seconds`, the probed length
rounded up). A call at or under the threshold by that number is not decoded at all. A longer
call is decoded, and the decoded length decides again: at or under the threshold, it runs as
one request.

`ASRResult` has a new optional field `chunking`. It is `None` for a call that is not chunked,
and then it is left out of the artifact, so those bytes stay as they were.

### 2. Long calls are chunked inside the job

For a longer call the ASR stage:

1. decodes the ORIGINAL once with ffmpeg to mono 16 kHz signed 16-bit PCM. This is what
   faster-whisper does inside for a file path. The argv is fixed (no shell) and the executable
   is `FFMPEG_PATH`, as for the HOM-38 derivative. The resampler is the same pinned `swr`
   filter; `-ac 1` mixes the channels down.
2. streams the PCM to a file in the job's temporary directory. The whole call is never held
   in memory. The same stream feeds the activity meter (decision 6).
3. plans the chunks (decision 3).
4. writes each chunk as a small 16 kHz mono PCM WAV in the same directory, calls the
   unchanged provider interface `asr.transcribe(ASRRequest(audio_path=<chunk wav>, ...))`,
   and deletes the WAV.

The provider interface, `ASRRequest` and every adapter stay unchanged.

The decode has a time limit of `max(MEDIA_DERIVATIVE_TIMEOUT_SECONDS, 0.1 x the recorded
length in seconds)`. So a 4-hour call gets 1440 s, not 300 s. Why: most of the decode time is
the activity meter in Python, and it grows with the call. A 4-hour call took about 90 s on an
Apple M3; a slower or busy server needs more room.

If the decode fails (ffmpeg missing, an error, the time limit, or more output than the longest
accepted upload), the job logs `ASR_CHUNK_DECODE_UNAVAILABLE` and sends the call as one
request, as before. A failed decode is never worse than today.

Diarization still gets the whole materialized ORIGINAL.

### 3. Chunk planning (`backend/speech/chunking.py`, version `asr-chunking/1`)

`plan_chunks` is a pure function on integer samples. Seconds become samples with
`round(seconds * sample_rate)`.

- At or under the threshold: one chunk, `[0, total)`.
- Otherwise it walks from `pos = 0`. While `total - pos > target + search`, the cut window is
  `[pos + target - search, min(pos + target + search, total - search)]`. The quiet gaps are the
  complement of the active intervals. A gap counts only when it is at least `min_gap` long. If
  any such gap meets the window, the cut is the middle (rounded down) of the longest part of a
  gap inside the window (on a tie, the earliest). Otherwise the cut is `pos + target`. The
  chunk owns `[pos, cut)`. The last chunk owns `[pos, total)`.
- Each chunk's audio reaches `overlap` past its owned range on both sides, clipped to
  `[0, total)`.

So the owned ranges cover the call with no gap and no overlap. Every owned length is between
`search` and `target + search`. The upper end of the window stops `search` before the end, so
the last chunk is never tiny.

### 4. The merge

`merge_chunk_segments` builds one transcript in original time.

- **Time.** A chunk segment moves by the chunk's start: `start_sample / sample_rate` seconds,
  added as an exact fraction and turned into a float once. The decode keeps every sample in
  place (ADR 0042), so 16 kHz sample `k` is original time `k / 16000`.
- **Ownership.** A segment is kept only when its midpoint is inside its chunk's owned range.
  A segment that both chunks heard in the overlap is kept once, by the chunk that owns its
  middle.
- **Seam.** For each pair of neighbour chunks, the last kept segment of the first and the
  first kept segment of the second are compared, only when they overlap in time. The longest
  run of words at the start of the later segment that equals the end of the earlier segment
  is removed. Words compare after `casefold` and with punctuation removed from both ends;
  Persian and Arabic letters compare as they are. A later segment that becomes empty is
  dropped. A trimmed segment starts at `min(max(start, earlier.end), end)`.
- **Output.** Sorted by start, indexes `0..n-1`, no empty text, the speaker label as the
  provider gave it.

### 5. Settings and defaults

| setting | default | why |
| --- | --- | --- |
| `ASR_CHUNKING_ENABLED` | `true` | Only calls over 20 minutes change; `false` is the rollback switch. |
| `ASR_CHUNK_MIN_CALL_SECONDS` | `1200` | Calls up to 20 minutes keep today's path exactly. A retry of a short call loses little. |
| `ASR_CHUNK_TARGET_SECONDS` | `600` | A failure loses at most about 10 minutes of ASR work. The 4-hour upload limit gives at most about 24 chunks. |
| `ASR_CHUNK_SEARCH_SECONDS` | `30` | A pause in a conversation within 30 seconds of the target is very likely. It moves a chunk by at most 5%. |
| `ASR_CHUNK_OVERLAP_SECONDS` | `2` | The word spoken at a cut is heard whole by both chunks. It costs 4 seconds of extra audio per cut, under 1% of a chunk. |

`min_gap_seconds` stays `0.2` and is not a setting. The activity meter already joins gaps
shorter than 10 frames (200 ms), so shorter gaps do not occur.

The rules are checked when the settings load, even when chunking is off: `TARGET > 0`,
`0 <= SEARCH < TARGET / 2`, `0 <= OVERLAP <= SEARCH`, `MIN_CALL >= TARGET + SEARCH`. A bad
combination stops the service at start with a clear message.

### 6. Cut places come from `signal-activity/1`

The mono PCM goes through the HOM-38 `ActivityMeter` with one channel. Its quiet intervals are
the cut places. No new model.

### 7. One checkpoint per chunk

Each non-empty chunk result is committed through the existing fenced checkpoint steps, under
the new checkpoint key `asr_chunk` (`StageName.ASR_CHUNK`). It is not a stage: `STAGE_ORDER`,
the stage-run rows and the job states do not change. There is still one ASR stage record per
attempt.

- **Identity.** The whole-call ASR request identity, plus a `chunking` object (the planner
  descriptor, the chunk audio version `asr-chunk-audio/1` and `signal-activity/1`), plus
  `chunk`: `index`, `start_sample`, `end_sample` and the language sent for that chunk. The same
  audio and settings give the same identities on a retry. A changed setting gives new ones.
- **Object key.** `art-<attempt id>-asr_chunk-<index>`: unique per attempt and chunk.
- **Empty chunks** (no text, no segments) are not committed, like an empty whole stage. The
  VAD of the provider can change (HOM-40 team 2), and an empty checkpoint would keep the old
  answer.
- **Usage.** Publishing an `asr_chunk` row writes no `ProviderUsage` row. The merged ASR
  checkpoint writes the one ASR row, with `audio_seconds` = the recording duration, as before.
- **Resume.** A retry walks the chunks in order and reuses every published chunk checkpoint
  whose identity matches (the normal `resume_stage` rules: verified artifact, live meeting,
  reuse on). Only the other chunks call the provider.
- **Lease and cancellation.** Before each chunk the job checks the heartbeat and locks the
  job row. A lost lease stops the attempt as stale. A cancelled job (meeting deleted) stops
  before the next chunk.
- **Cleanup and tenancy.** Chunk rows are ordinary `stage_checkpoints` rows: meeting deletion
  retires them (`retire_meeting_checkpoints`), the abandoned-artifact sweep removes their
  unpublished objects, and row-level security scopes them by workspace. Tests cover the first
  two. No migration: `stage` is `String(40)` with no check constraint.

### 8. The merged result

After the last chunk the stage builds one `ASRResult`: provider, model and language from the
chunk that set the language (decision 9), `segments` from the merge,
`text` = the segment texts joined with spaces, `duration_seconds` = samples / 16000, `is_mock`
when any chunk was a mock, and `chunking`: the version and, per chunk, `index`,
`start_seconds`, `end_seconds`, `owned_start_seconds` and `owned_end_seconds`. HOM-45 and
HOM-46 read the boundaries from there.

It is committed as the normal `asr` checkpoint. Its identity is the whole-call identity plus
the `chunking` object. When it is reused, nothing is decoded. An empty merged result ends the
stage as before: `insufficient_evidence` with `empty_transcript`, diarization and analysis
`skipped`, nothing committed.

### 9. Language

An explicit language is sent with every chunk. For an auto request, the chunks go in order
with the auto request until one returns a `language_decision` whose `reason` is not
`no_speech`. Every later chunk is then sent with that chunk's language, explicitly. The merged
language and decision come from that chunk.

An adapter that returns no `language_decision` (only test doubles today) sets the language
with its first chunk that has speech. When no chunk sets it, the merged language comes from
the first chunk with speech.

## Consequences

### Rollback and deployment

- `ASR_CHUNKING_ENABLED=false` sends every call as one request again. Chunk checkpoints that
  exist stay rows; they are retired with their meetings and swept like any checkpoint.
- No migration. A code rollback ignores `asr_chunk` rows. A merged checkpoint of a chunked
  call has a `chunking` object that older code rejects (`extra="forbid"`): it is invalidated
  and the ASR runs again as one request. Artifacts of calls that were not chunked load
  unchanged.

### Effects

- A long call that fails late resumes from its finished chunks.
- Each chunk call has its own stage timeout.
- A chunked call costs one extra decode of the call, about the time ffmpeg needs, plus the
  activity meter in Python.

### Limits

- **Seam dedup uses time and text only.** There are no word timestamps. When the two chunks
  write the seam words differently, a duplicate can stay. When a phrase is really said twice
  across a cut ("yes yes"), one copy can be removed.
- **Activity is a signal heuristic, not VAD.** A "quiet" gap is low signal, not proven
  silence. Music or steady noise can hide every gap; then the cut is at the target, maybe in
  a word. The overlap and the midpoint rule protect that case.
- **Whisper's text context restarts at each cut.** Whisper conditions on its previous text
  inside one request. A new chunk starts without it, so spelling of names or style can differ
  between chunks.
- **One language per call after the first decision.** A call that switches language stays in
  the first decided language. Whole-call Whisper also detects once.
- **The recorded length decides first.** A container that reports a length at or under the
  threshold is not decoded, even when its real audio is longer.
- **The decode has a time limit.** It is `MEDIA_DERIVATIVE_TIMEOUT_SECONDS` (default 300 s)
  or 0.1 s per recorded second, whichever is longer. A call that still cannot be decoded in
  time runs as one request (logged).

## Deferred

- HOM-41: warm ASR workers.
- HOM-45 and HOM-46: speakers across chunks, using `ASRResult.chunking`.
- Feeding ASR the HOM-38 derivative instead of the ORIGINAL.
- Word timestamps for a sharper seam.
- The 8 kHz VAD rescue in `backend/providers/whisper.py` is HOM-40 team 2, not this ADR.

## Alternatives considered

- **Chunk inside the Whisper adapter.** Rejected: every adapter would need it, and the
  checkpoints, the lease and the retry live in the job.
- **Fixed cuts with no activity.** Simpler, but a cut lands in a word more often. The activity
  meter already exists (HOM-38).
- **Checkpoint empty chunks.** Rejected: it would freeze an empty answer that a VAD change can
  fix.
