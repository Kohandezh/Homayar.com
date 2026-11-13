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
`tone`. `NONSPEECH_VERSION = "nonspeech/1"`; any change to features or thresholds later is a
new version. Pure numpy (imported inside the function, so the module imports without it).

The rule (reworked in review round 1): music and tones keep their spectral shape from one
frame to the next, speech does not.

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
- Speech presence: when fewer than 77 percent of the judged frames are steady, speech is in
  the span and the span is speech whatever else it holds.
- Speech under one loud, stable partial (a dial tone at the same RMS as the words) hides from
  the energy-weighted cosine: the tone bin dominates it. A dominant-peak mask therefore
  removes the strongest bin region of the judged frames' mean spectrum (plus and minus 3
  bins) and repeats the steadiness test on the rest. A tone's rest is window leakage only
  (under -20 dB of its energy: a pure tonal core, no test needed); chords keep their other
  notes; speech keeps its formants and fails the test.
- The steady frames' median number of prominent in-frame peaks (local maxima within 14 dB of
  the frame's loudest bin) separates the classes with a tolerance: at most 2 is a `tone`
  (dial tone, ringback, busy tone, a modulated single tone, DTMF), more is `music` (chords,
  melodies, hold music). The median, not the modal count of review round 0: the 8 kHz melody
  resampled to 16 kHz splits its per-frame peak counts (7 vs 8) because the resample's mild
  low-pass moves third harmonics across the peak floor.

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
chunk must not move the midpoint out of the chunk's owned range, amendment 4), then shifted
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

Local, 2026-11-14, this Mac, numpy 2.5.2, review round 1 rule. Every 16 kHz case is the 8 kHz
signal resampled with `numpy.interp`, the way production resamples phone audio (amendment 4).
`flip` is the steadiness threshold at which the real `classify_span` would change the class,
found by binary search: speech cases must flip under the chosen 0.77, non-speech above it.

| Signal (rate) | Expected | flip | Margin to 0.77 |
| --- | --- | --- | --- |
| mod_tone / mod_tone_mulaw / dial_tone (8k, 16k) | tone | 1.000 | +0.230 |
| ringback 0-6.0 and 0-2.0 (8k, 16k) | tone | 1.000 | +0.230 |
| dtmf full and 3-5 s (8k, 16k) | tone | 0.931-0.941 | +0.161 |
| melody 0-10 (8k, 16k) | music | 0.956 | +0.186 |
| melody 3-5 s (8k, 16k) | music | 0.876 | +0.106 |
| melody 0.25 s notes (8k, 16k) | music | 0.885 | +0.115 |
| melody 0.8 s notes (8k, 16k) | music | 0.945 | +0.175 |
| melody minor chords (8k, 16k) | music | 0.956 | +0.186 |
| melody 5 harmonics (8k, 16k) | music | 0.956 | +0.186 |
| melody one octave lower (8k, 16k) | music | 0.870-0.872 | +0.100 |
| fleurs 0-7.86 / 0-4 / 3.5-7.86 (8k, 16k) | speech | 0.288-0.383 | -0.387 |
| fleurs + melody at -15 dB (8k, 16k) | speech | 0.424-0.444 | -0.326 |
| fleurs + dial_tone at 0 dB (8k, 16k) | speech | 0.290-0.305 | -0.465 |
| say s1/s2/s3 full and second half (8k, 16k) | speech | 0.200-0.386 | -0.384 |

Sliding windows over `fleurs` and `fleurs_16k` (lengths 1.5, 2.0, 3.0 s, step 0.1 s; shorter
lengths are under the 1.5 s minimum and always speech): the worst window, the one over the
held vowel around 2.7 s, flips at 0.655-0.661 (8 kHz) and 0.569-0.661 (16 kHz) across the
three lengths, a margin of 0.109 to the 0.77 threshold.

So the round-1 margins are: at least 0.100 on the non-speech side (the weakest case is the
melody one octave lower, 0.870) and at least 0.109 on the speech side (the worst sliding
window, 0.661). The round-0 claim of "at least 0.14 on both sides" was measured on signals
synthesized natively at 16 kHz and without sliding windows; it did not survive review round 1
(the resampled melody and 0.5-1.0 s speech windows broke it).

The `say` samples are macOS TTS ("Most riders wear riding boots...", "The meeting moved to
Thursday afternoon...", "Could you repeat the last part...") converted to 8 kHz mu-law WAV;
no Persian voice exists on this Mac (`say -v '?'` has none). The clean TTS voices are as
tonal as a chord (spectral flatness 0.003-0.026, inside the melody range), so flatness is not
part of the rule; their unsteady frame share keeps them speech with a margin of 0.38.

Cost, this Mac: classifying 600 s at 16 kHz takes 0.42-0.51 s (budget: under 3 s). The gate
also decodes the audio a second time per non-chunked call: for a 10 min 8 kHz WAV the decode
(`faster_whisper.audio.decode_audio`) took 0.40 s and the classify 0.27 s.

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
they do pass (ADR 0044 measured melody text on the base).

## Limits

- Quiet speech inside one segment with a much louder tone is judged on the tone only: the
  active-frame floor is 35 dB under the loudest frame, so speech more than 35 dB under a tone
  never reaches a judged frame and the span can end `tone`. Speech at or near the tone level
  is protected (the dominant-peak mask and the speech-presence share); `fleurs` plus a dial
  tone at the same RMS stays `speech`.
- Speech over music was measured with the music 15 dB under the speech RMS
  (`fleurs_over_melody`). Louder music can make a span count as music and drop real speech.
- Singing is tonal and spectrally steady inside notes; sung lyrics can be removed as music.
  Not measured.
- Real hold music was not measured; the judgement rests on the synthetic melodies (five
  variants), the phone tones and the FLEURS speech of the SPEC signal set.
- The gate decodes the audio a second time per non-chunked call: 0.40 s decode plus 0.27 s
  classify for a 10 min file on this Mac (the model decode of the same file is minutes).
- A tone that changes frequency faster than every ~150 ms keeps too few steady frames and
  falls back to `speech`. DTMF at 100 ms on/off is the fastest changing signal that passes
  (flip 0.931).
- Only 8 kHz and 16 kHz mono input is accepted (the adapter always decodes to 16 kHz).
- macOS `say` speech stays speech with a margin of 0.38, but other synthetic voices were not
  measured.
- Checkpoints written before HOM-348 are not reused by gated runs (the identity gains the
  `nonspeech` key). Old results still load unchanged.
