# ADR 0072: Speaker alignment of ASR spans to diarization

Date: 2026-11-13 · Status: Proposed (HOM-47, WP10-T04, parent HOM-14, features F30 F31 F33) · Builds on: ADR 0046, ADR 0071

## Context

The job builds transcript turns by giving every ASR segment the speaker whose
diarization interval overlaps it most (`jobs/processor._speaker_for_segment`).
When two speakers share a segment, or the segment sits in overlapping speech,
the code still forces one label. That fabricates attribution: the transcript
looks certain while the evidence is not (F30). Overlapping speech is silently
erased from the turns (F31), and a segment that reaches past the recording
bounds is stored out of bounds (F33).

Real diarization does not exist yet (HOM-44). ASR returns segments, not word
timings, so alignment today works on whole segments. This ADR defines a pure
library, `backend/speech/speaker_alignment.py`, wired into the one place the
job builds turns.

## Decision

### 1. Time mapping

A `TimeBase` says where a caller's local time 0 sits on the original
recording: `offset_samples` at `sample_rate` (default 16 kHz, the analysis
rate). `TimeBase.from_chunk_plan` reads a chunk plan of ADR 0046 and
`TimeBase.from_span` converts a chunk span's `start_seconds`.
`seconds_to_original` and `sample_to_original` return exact `Fraction`
values; integers and fractions are used inside, floats only at the edges.
`original_sample(seconds, rate)` maps an original-time instant to a sample
index at the recording's own rate (before resampling), rounded half-even, so
a 16 kHz analysis time and a 44.1 kHz or 8 kHz index can name the same
instant. Chunk-local diarization is mapped with `map_diarization(result,
time_base)`, which shifts every segment and keeps status, provider, issue
and `is_mock`.

### 2. Input and decision per span

`align_speakers(units, diarization, *, recording_duration_seconds,
time_base, original_sample_rate, settings)` takes `AsrUnit` objects (built
from `ASRSegment` with `AsrUnit.from_segment`, or with `AsrWord` word timings
when a provider has them) and speaker intervals as a `DiarizationResult` in
original time, exactly the object `reconcile_chunk_speakers(...).result`
produces. The result is a `SpeakerAlignment`: sorted `AlignedTurn` objects,
the version `speaker-alignment/1`, the settings descriptor and counts per
status. The library is pure: no I/O, no randomness, no clock. Reordered
diarization segments change nothing.

Each span (a word when the unit has words, else the whole unit) is decided
by the first rule that fires, in this order:

1. Map to original seconds and clamp to `[0, recording_duration_seconds]`.
   A span entirely outside by more than `bounds_tolerance_seconds` (default
   0.05 s) is `unknown` with reason `outside_recording` and a zero-length
   span at the nearest bound. A span partly outside by more than the
   tolerance is clamped with reason `clamped_to_recording`; within the
   tolerance it is clamped silently.
2. No diarization, or diarization `UNAVAILABLE`: `unknown`,
   `diarization_unavailable`.
3. Zero-length span after clamping: `unknown`, `no_duration`. The mock ASR
   emits one 0..0 segment; it keeps `speaker_unknown`.
4. Overlapping speech (two or more different labels active; the same label
   twice is not overlap) at or above `max_overlap_share` (default 0.2) of
   the span: `ambiguous`, `overlap`.
5. Covered time (any label) under `min_coverage` (default 0.5) of the span:
   `unknown`, `no_speaker`.
6. The top label's share of covered time under `dominant_share` (default
   0.8): `ambiguous`, `straddles_speakers`. Two labels with equal time can
   never pass, because `dominant_share` is above 0.5.
7. Top label is `speaker_unknown`: `unknown`, `unknown_speaker`.
8. Otherwise `assigned` to the top label.

Per-label time is the union of that label's intervals inside the span, so
two overlapping segments of one label count once. Every comparison uses
exact `Fraction` math; a share exactly at a threshold follows the
inequality as written. Reasons collect every bounds rule that fired plus
the deciding rule. An `assigned` turn's `confidence` is the top label's
share of covered time, with `confidence_basis` set to
`coverage_share_uncalibrated`: it is a coverage ratio, not a calibrated
probability (F33).

### 3. Output turns

Turns are sorted by (start, end, source unit index). Every turn carries
`start_seconds`/`end_seconds` in original time inside the recording bounds,
`start_ms` (floor) and `end_ms` (ceil) clamped to `ceil(duration * 1000)`,
original-rate sample indexes when a rate is given, the assigned label or
`speaker_unknown`, the status, sorted reasons, and every active candidate
label with its share. A speaker is never invented: `ambiguous` and `unknown`
turns are stored as `speaker_unknown`. When a unit has words, consecutive
words with the same (status, label, reasons) join into one turn; turns
never join across units.

### 4. Wiring

`_transcript_turns` in `jobs/processor.py` now calls `align_speakers` with
one `AsrUnit` per ASR segment, the diarization result and the claimed
`recording_duration_seconds`; each aligned turn becomes a
`TranscriptTurn(text, speaker_label, start_seconds, end_seconds)`.
`_speaker_for_segment` is deleted. Nothing else changes: no checkpoint
fields, no columns, no stored reasons. HOM-49's timeline flags an unknown
turn for review through its existing `unknown_speaker` reason.

## Consequences

- No migration and no schema change. The stored effect is that a straddling
  or uncovered turn is stored with `speaker_key = "speaker_unknown"` instead
  of a forced label, and it reaches review through HOM-49.
- Turns whose speaker is ambiguous or unknown become visible as such, so
  overlap and attribution loss stop being hidden (F30, F31).
- Every stored turn span lies inside the recording bounds (F33).
- Rollback is reverting `_transcript_turns` to the overlap-max function and
  deleting the module, its tests and this ADR.

## Not done

- Word-level ASR timings do not exist yet; alignment runs on whole segments
  until a provider supplies words.
- Per-turn reasons are not stored; that needs a column and a migration.
- The thresholds (`min_coverage`, `dominant_share`, `max_overlap_share`,
  `bounds_tolerance_seconds`) are defaults, not calibrated values. WP10
  step 5 calibrates them on held-out annotated data.
- Real diarization is HOM-44; until then the aligner runs on mocks and
  fixtures in tests.
- Overlap intervals are reported per turn (reason and candidates) but not
  stored as a separate timeline basis.
- Alignment cost is units times diarization segments; a binary search over
  sorted ranges is a follow-up for long calls with word timings.
- A turn joined from words spans the gaps between its words, so its time
  range can include another speaker's speech in a gap; revisit when word
  timings arrive.

## Alternatives considered

- **Keep the most-overlap speaker.** Rejected: it invents attribution for
  straddling and overlapped speech, which is what HOM-47 removes.
- **Split a straddling segment at the speaker boundary.** Rejected: the cut
  point is a guess about where one voice stopped, and the resulting halves
  would look certain while being arbitrary.
- **Align inside the diarizer adapter.** Rejected: the job owns the
  recording bounds, the chunk time bases and the checkpoints; a pure
  library keeps the decision testable without a model.
