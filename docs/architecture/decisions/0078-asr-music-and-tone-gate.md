# ADR 0078: ASR music and tone gate

Date: 2026-11-14 · Status: Proposed (HOM-348, feature F26) · Builds on: ADR 0044 (amendment
HOM-40), ADR 0046

## Context

Feature F26 says silence, music and tones must never become transcript text. The VAD rescue of
ADR 0044 (HOM-40) lowered the Silero threshold to 0.25 so quiet phone speech survives. Two
non-speech signals slip through it (ADR 0044, "Limits"):

- A 425 Hz tone at 0.5 peak, amplitude-modulated at 4 Hz (about the syllable rate), 10 s at
  8 kHz: the rescue VAD keeps 7.87 s of the mu-law version and `large-v3-turbo` writes
  `موسیقی` ("music") for an explicit `fa` request and for auto.
- A synthetic chord melody with noise at -35 dBFS keeps 0.46 s under the default VAD on the
  base, and Whisper writes text for it.

One root cause: the Whisper adapter keeps every decoded segment, whatever audio sits under it.

The fix is a cheap CPU check on the audio span of each decoded segment. Production runs
`large-v3` on a GPU worker; the check must not depend on the model, so it reads only the audio
and the segment times and never calls a model.

## Decision

### D1. The detector: `backend/speech/nonspeech.py`

`classify_span(samples, sample_rate, start, end)` returns exactly one of `speech`, `music`,
`tone`. `NONSPEECH_VERSION = "nonspeech/1"`; any change to features or thresholds after
release is a new version (the rule changed in review rounds 1 and 2, before any release, so
it is still version 1). Pure numpy (imported inside the function, so the module imports
without it).

The rule (reworked in review rounds 1 and 2): music and tones keep their spectral shape from
one frame to the next, speech does not.

- Frames of 32 ms, hop 16 ms, Hann window, `numpy.fft.rfft`, judged only inside 100-3800 Hz
  (the phone band and a little more).
- Active frames are those within 35 dB of the span's loudest frame. Loudness never matters:
  there is no absolute dBFS floor, so the very quiet FLEURS fixture (mean -67.4 dB) is judged
  the same as a loud one.
- A frame is judged when it and its reference frames (32 and 48 ms back) are active and the
  frames' energies agree within 4 dB: frames that straddle an onset or a gap differ by
  windowing, not by content, and would otherwise look unsteady (that alone cost DTMF 30
  percent of its frames).
- A judged frame is steady when 1 minus the cosine similarity of its magnitude spectrum
  against both references stays under 0.15.
- Speech presence, first test: when fewer than 77 percent of the judged frames are steady,
  speech is in the span and the span is speech whatever else it holds.
- The steady frames' median number of prominent in-frame peaks (local maxima within 14 dB of
  the frame's loudest bin) gives the kind: at most 2 is a `tone` (dial tone, ringback, busy
  tone, a modulated single tone, DTMF), more is `music` (chords, melodies, hold music). The
  median, not the modal count of review round 0: the 8 kHz melody resampled to 16 kHz splits
  its per-frame peak counts (7 vs 8) because the resample's mild low-pass moves third
  harmonics across the peak floor.
- Speech presence, second test (review round 2): speech under steady partials at about the
  same level (words over a dual tone, a dial tone, or music) leaves most frames steady,
  because the partials dominate the energy-weighted cosine. So every partial that lasts is
  removed and the rest is judged:
  - The steady level of each bin is its lowest magnitude over the 8 frames (128 ms) that end
    at the frame, or over the 8 frames that start at it, whichever is higher. A note that
    starts or ends inside the window still counts as steady.
  - The leftover is the frame spectrum minus that level (never below zero). A judged frame
    is speech-like when the leftover holds a clear share of the frame's energy and its shape
    changes against both reference frames (1 minus cosine at least 0.15), as speech does.
  - "Clear" depends on the background. When the steady level holds at most 2 peaks within
    20 dB of its loudest bin (median over the judged frames), the background is a tone and
    leaves almost nothing behind: a leftover share of 0.05 is enough. More steady peaks is
    music, which leaves some vibrato, beating and note-change energy: the share must be at
    least 0.20. The peaks are counted on the steady level, not on the frame, so the
    speech's own peaks never change the choice. The 20 dB floor (not 14 dB) keeps a
    single-note melody with vibrato in the music group: vibrato smears its weaker
    harmonics out of a 14 dB floor.
  - When at least 9 percent of the judged frames are speech-like, the span is speech.
- This replaces the round 1 dominant-peak mask, which removed only the strongest bin region
  (about 94 Hz): a chord melody or a 480 + 620 Hz dual tone kept its other partials in the
  rest, so speech over them was judged `music` or `tone` and deleted.

Unsure is speech: a span under 1.5 s after clipping to the array, outside the array, empty,
or digitally silent returns `speech` (amendment 4: real FLEURS speech windows of 0.5-1.0 s
over a held vowel were judged music by the round-0 rule; from 1.5 s up none were). A span
that runs past the end of the audio is clipped and only the clipped part is judged (seen on
base: a segment 5.01-34.99 s on a 10 s file). Dropping real speech is worse than keeping one
invented word. Sample rates other than 8000 and 16000 raise `ValueError`.

### D2. The gate: `backend/providers/whisper.py`

`LocalWhisperProvider` gains `nonspeech_gate: bool = True` and
`audio_loader: Callable[[str], Any] | None = None`. The default loader is
`faster_whisper.audio.decode_audio(path, sampling_rate=16000)`, the same decode
faster-whisper does for a path, imported inside the loader.

After the raw segments are read and empty texts skipped, and only when the gate is on and at
least one segment has text, the audio is loaded once and each segment's span is classified. A
`music` or `tone` segment is removed: its text is not in `ASRResult.text`, it is not in
`ASRResult.segments`, and one `ASRNonSpeechSpan` with the segment's own (unclipped) times and
the kind is added to `ASRResult.non_speech`, in time order. The kept segments are re-indexed
0..n-1. This covers every pass: the first call, the 8 kHz rescue call and the second decode
with the decided language, because all end in the same loop.

`duration_seconds` is computed before the gate, from every decoded segment with text, so the
gate never changes it (amendment 2). The language decision, `language`, the model calls and
their arguments never change; `decide_language` does not change.

Fail open: if the loader or the classifier raises anything (including `ImportError` when numpy
or faster-whisper is missing), the result is exactly the ungated result, `non_speech` stays
empty, and one warning `ASR_NONSPEECH_GATE_FAILED` is logged with the exception type only,
never text or audio values. With the gate off, or with no segment that has text, the loader is
never called. The module imports `backend.speech.nonspeech` lazily, inside the gate, so
`backend.providers.whisper` stays importable without numpy and without faster-whisper.

### D3. The schema: `backend/providers/schemas.py`

`ASRNonSpeechSpan(start_seconds, end_seconds, kind)` with `kind` in `{"music", "tone"}` and
`end_seconds` not before `start_seconds`. `ASRResult.non_speech` defaults to an empty list, so
results and checkpoints written before HOM-348 (no `non_speech` key) still load.

### D4. Checkpoint identity

`nonspeech_identity()` returns `{"version": "nonspeech/1"}` when the gate is on, `None` when
off (`ASRProvider` returns `None` by default). The job processor writes that dict into
`asr_request_identity["nonspeech"]` next to the `decode` entry. Why: a gated result differs
from a stored ungated result, so they must not reuse each other's checkpoints. The chunked
path (ADR 0046) gets the same key because it receives `asr_request_identity`. A provider
without the method, or one that returns `None`, keeps today's identity byte for byte.

### D5. The stage reason

When a job's ASR result has no text, no segments and at least one non-speech span, the ASR
record ends `INSUFFICIENT_EVIDENCE` with reason `non_speech_audio` and the message "Only music
or tones were found; no speech was transcribed". Without a span it stays `empty_transcript`.
Everything else in that branch (diarization and intelligence skipped, no checkpoint) is
unchanged.

### D6. Long calls

`merge_results` (`backend/jobs/asr_chunks.py`) maps each chunk's spans into original time:
the span's end is first clipped to the chunk's own audio length (a Whisper end far past the
chunk must not move the midpoint out of the chunk's owned range, amendment 4), and so is its
start, so a span can never end before it starts (amendment 5), then shifted
by the chunk start, and kept only when its midpoint is inside the chunk's owned range, the
same ownership rule `merge_chunk_segments` uses for segments; the spans are sorted by start.
The merged span keeps the clipped end, unlike the adapter's spans, which keep the segment's
unclipped times.

### D7. The setting

`asr_nonspeech_gate_enabled: bool = True` in `backend/config.py`, next to the ASR chunking
settings; the factory passes it to both `LocalWhisperProvider` constructions. It is the
rollback switch. Both Compose `x-app-environment` anchors pass it as
`ASR_NONSPEECH_GATE_ENABLED` with the code default (SPEC amendment 3).

## Measurements

Local, 2026-11-14, this Mac, numpy 2.5.2, review round 2 rule. Every 16 kHz case is the 8 kHz
signal resampled with `numpy.interp`, the way production resamples phone audio (amendment 4).
Both values are found by binary search on the real `classify_span`, so they are the numbers
the rule compares:

- `steady`: the judged-steady share. Non-speech needs at least 0.77.
- `speech-like`: the second test's share. Non-speech needs it under 0.09.

### Non-speech (must stay `music` or `tone`)

Full span and the 2 s sub-span 3-5 s (ringbacks: 0-6 and 0-2 s), at 8 kHz and 16 kHz.

| Signal | Class | steady | speech-like |
| --- | --- | --- | --- |
| mod_tone, mod_tone_mulaw, dial_tone, ringback, dual tone 480 + 620 Hz | tone | 1.000 | 0.000 |
| dtmf | tone | 1.000 | 0.006-0.029 |
| melody (SPEC), sawtooth melody (criterion 2c) | music | 0.876-0.956 | 0.000 |
| melody 0.25 s notes, 0.8 s notes, minor chords, 5 harmonics | music | 0.875-0.956 | 0.000-0.032 |
| melody one octave lower | music | 0.870-0.972 | 0.019-0.029 |
| chords with 1.5 % vibrato at 5 Hz | music | 0.876-0.956 | 0.008-0.029 |
| single notes with vibrato, sawtooth single notes | music | 0.876-0.956 | 0.000 |
| square-wave single notes | music (8k), tone (16k) | 0.876-0.956 | 0.000 |
| pure sine single notes | tone | 0.901-0.962 | 0.000 |
| NA busy (480 + 620 Hz, 0.5 s on/off), NA ringback (440 + 480 Hz) | tone | 1.000 | 0.000-0.018 |
| Iranian busy (425 Hz, 0.3 s on/off) | tone | 1.000 | 0.018-0.029 |
| SIT (913.8, 1370.6, 1776.7 Hz in sequence) | tone | 0.881-0.889 | 0.034-0.043 |

Margins on the non-speech side: steady at least 0.870 against 0.77 (+0.100, the melody one
octave lower), speech-like at most 0.043 against 0.09 (0.047 under it, SIT).

### Speech over music or a tone (criterion 2c)

`fleurs_16k` plus a background, speech RMS N dB over the background RMS, span 0-7.86 s:

| Background | Lowest level that stays `speech` | speech-like at the criterion 2c levels |
| --- | --- | --- |
| melody (SPEC) | -6 dB | 0.155 (0 dB), 0.194 (+3), 0.211 (+5) |
| sawtooth melody | -6 dB | 0.167 (0 dB), 0.199 (+3), 0.221 (+5) |
| dual tone 480 + 620 Hz | -12 dB (all levels measured) | 0.282 (0 dB), 0.147 (+3), 0.172 (+6), 0.246 (+9) |
| dial tone 425 Hz, mod_tone | -12 dB (all levels measured) | |
| chords with vibrato | -9 dB | |
| melody one octave lower | -11 dB | |

Margin on the speech side at the criterion 2c levels: at least 0.147 against 0.09 (+0.057,
the dual tone at +3 dB). The review round 1 rule (dominant-peak mask) judged FLEURS over the
melody `music` up to +5 dB and over the dual tone `tone` or `music` up to +9 dB.

Shorter windows over the same mixes (step 0.1 s): 3 s and 4 s windows all stay speech at
every criterion 2c level, except one 3 s window of 49 over the dual tone at 0 dB. 2 s
windows: 5-6 of 59 are non-speech at 0 to +5 dB over the melodies, 1-3 of 59 over the dual
tone at 0 to +6 dB, none from +9 dB (melody) or +7 dB (sawtooth). They are the windows over
the slow start (0-2.4 s) and end (5.7-7.7 s) of FLEURS, where the speech inside the window is
2 to 3 dB quieter than the clip average.

macOS `say` voices (8 kHz mu-law, then 16 kHz) over the same backgrounds, full span: normal,
fast and Daniel stay speech from -6 dB over the melodies and from -6 dB (normal) or -9 dB
(fast, Daniel) over the dual tone; the whispered voice stays speech at every level from -9
dB.

### Speech alone

| Case | Result |
| --- | --- |
| fleurs 0-7.86 (8k, 16k) | speech, steady 0.362-0.390 (first test); 0-4 and 3.5-7.86 are speech in the tests |
| sliding windows over fleurs, 1.5 / 2 / 3 s, step 0.1 s (8k, 16k) | 0 of 344 non-speech |
| `say` normal, fast (rate 280), Whisper (whispered), Daniel (en_GB), full and sliding 1.5 / 2 / 3 s windows (8k mu-law, 16k) | 0 of 2,012 windows non-speech; full span steady 0.035-0.393 |
| fleurs + melody at -15 dB, fleurs + dial tone at 0 dB | speech |

The `say` text is "Most riders wear riding boots, and they talk about the weather while the
horses rest near the river. Please call me back after five o'clock, the number is two three
four, five six seven."; no Persian voice exists on this Mac.

### Sensitivity

The 8-frame hold is not at an edge. With 10 frames the hard cases keep their class (melody one
octave lower, chords with vibrato, 0.8 s notes, SIT, DTMF, Iranian busy, and every criterion
2c mix). With 6 frames DTMF (100 ms bursts) turns into speech.

### Cost

Classifying 600 s at 16 kHz takes 0.13-0.34 s on this Mac (budget: under 3 s). The gate also
decodes the audio a second time per non-chunked call (see "End to end").

## End to end

`mobiuslabsgmbh/faster-whisper-large-v3-turbo`, CPU `int8`, local model cache, 8 kHz WAVs
(the modulated tone after a mu-law round trip, like the ADR 0044 case):

| Audio | Language | Gate off | Gate on |
| --- | --- | --- | --- |
| mod_tone_mulaw | fa | `موسیقی` (segment 0.5-6.5) | empty text, one tone span 0.5-6.5 |
| mod_tone_mulaw | auto | `موسیقی` (fallback, low confidence) | empty text, one tone span 0.5-6.5 |
| melody | fa | empty (rescue VAD keeps nothing here) | empty |
| melody | auto | empty | empty |
| dtmf | fa | empty (default and rescue VAD keep nothing) | empty |
| dtmf | auto | empty | empty |
| fleurs | en | "Most riders wearing riding boots with a heel and a smooth, quite narrow sole." | identical text and segment, no spans |
| fleurs | auto | same sentence | identical text and segment, no spans |

The melody and DTMF rows are controls on this machine: their VAD keeps nothing even after the
rescue, so they never reach the decoder. The gate is still the guard for environments where
they do pass (ADR 0044 measured melody text on the base). Re-run on the review round 2 rule:
every row is identical to the round 1 run (text, segments and spans, gate on and off).

The gate's own cost on a 10 min 8 kHz WAV: the second decode
(`faster_whisper.audio.decode_audio`) took 0.38 s cold and 0.08 s warm, and the classify took
0.13-0.31 s. The model decode of the same file takes minutes.

## Limits

- Quiet speech inside one segment with a louder tone: measured with FLEURS followed by 2, 4 or
  8 s of `mod_tone` in one span at 8 kHz, the span stays `speech` while the tone is up to
  30 dB louder than the speech (RMS), and becomes `tone` from a 40 dB gap at every tone
  length. Past about 35 dB the speech frames fall under the active-frame floor (35 dB under
  the loudest frame) and are never judged. (Review round 2 measured `tone` from a 20 dB gap
  with 8 s of tone under the round 1 rule; the second speech test closes that.)
- Speech over music or a tone needs a minimum level. Full FLEURS span: speech from -6 dB
  under the chord melodies, at every measured level (from -12 dB) over the single and dual
  tones. A short Whisper segment needs more. On 2 s windows over FLEURS (step 0.1 s), a few
  windows over its slow start and end are lost: 5-6 of 59 with the speech 0 to +5 dB over a
  chord melody, 1 of 59 at +7 dB over the SPEC melody, none from +9 dB (SPEC melody) or +7 dB
  (sawtooth melody); 1-3 of 59 at 0 to +6 dB over the dual tone. In those windows the speech
  is 2-3 dB quieter than the clip average. Of the 3 s and 4 s windows at the criterion 2c
  levels, only one 3 s window over the dual tone at 0 dB is lost.
- The kind (`tone` vs `music`) is decided per frame, on the median count of peaks within
  14 dB of each steady frame's loudest bin, not on the distinct frequencies over the span. So
  a square-wave single-note melody is `music` at 8 kHz but `tone` at 16 kHz (the resample's
  low-pass drops its fifth harmonic under the floor), and a pure sine single-note melody (a
  simple ringtone) is `tone`. Both are still removed; only the recorded kind differs from
  SPEC 2.1's "more than two frequencies over the span". (Under the round 1 rule the sine
  melody was missed and kept as speech; the round 2 rule removes it.) SIT, three frequencies
  in sequence, is `tone`, like DTMF.
- Singing is tonal and steady inside notes, so sung lyrics are removed as music: the macOS
  `say` voice "Good News" (sung) is `music` over its full 37 s (steady 0.94-0.95,
  speech-like 0.035-0.041) and in 270-301 of its 341-356 sliding windows.
- Real hold music, real IVR prompts over music, real callers over a radio, Persian speech and
  codec-damaged (AMR, GSM) phone audio were not measured. The judgement rests on synthetic
  melodies (11 variants), synthetic phone tones, one English FLEURS clip and five macOS `say`
  voices.
- A tone that changes frequency faster than about every 150 ms keeps too few steady frames
  and falls back to `speech`. DTMF at 100 ms on/off is the fastest changing signal that
  passes.
- Only 8 kHz and 16 kHz mono input is accepted (the adapter always decodes to 16 kHz).
- Checkpoints written before HOM-348 are not reused by gated runs (the identity gains the
  `nonspeech` key). Old results still load unchanged.
