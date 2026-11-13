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

The rule: music and tones have a time-invariant spectrum, speech does not.

- Frames of 32 ms, hop 16 ms, Hann window, `numpy.fft.rfft`, judged only inside 100-3800 Hz
  (the phone band and a little more).
- Active frames are those within 35 dB of the span's loudest frame. Loudness never matters:
  there is no absolute dBFS floor, so the very quiet FLEURS fixture (mean -67.4 dB) is judged
  the same as a loud one.
- Per active frame: the number of spectral peaks (in-frame local maxima within 14 dB of the
  frame's loudest bin) and the spectral shape change (1 minus the cosine similarity of the
  magnitude spectrum) against 32 ms and 48 ms earlier.
- A frame is evidence when it is steady at both lags (shape change under 0.2) and its peak
  count equals the span's most common peak count.
- At least 45 percent of the judged frames are evidence: the span is non-speech, else speech.
- The evidence frames' median peak count then separates the classes: at most 2 steady peaks is
  a `tone` (dial tone, ringback, busy tone, a modulated single tone, DTMF), more is `music`
  (chords, melodies, hold music).

Unsure is speech: a span under 0.3 s, outside the array, empty, or digitally silent returns
`speech`. A span that runs past the end of the audio is clipped and only the clipped part is
judged (Whisper can report a segment end far past the audio; seen on base: 5.01-34.99 s on a
10 s file). Dropping real speech is worse than keeping one invented word. Sample rates other
than 8000 and 16000 raise `ValueError`.

The peak-count mode is the feature that keeps synthetic speech safe: the clean macOS `say`
voices are as tonal as a chord (spectral flatness median 0.003-0.026, inside the melody range),
but their peak count keeps moving between voiced and unvoiced sounds, so their evidence
fraction stays far under the threshold.

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

`merge_results` (`backend/jobs/asr_chunks.py`) maps each chunk's spans into original time
(chunk start offset) and keeps a span only when its midpoint is inside the chunk's owned
range, the same ownership rule `merge_chunk_segments` uses for segments; the spans are sorted
by start.

### D7. The setting

`asr_nonspeech_gate_enabled: bool = True` in `backend/config.py`, next to the ASR chunking
settings; the factory passes it to both `LocalWhisperProvider` constructions. It is the
rollback switch. Both Compose `x-app-environment` anchors pass it as
`ASR_NONSPEECH_GATE_ENABLED` with the code default (SPEC amendment 3).

## Measurements

Local, 2026-11-14, this Mac, numpy 2.5.2. Signals per Linear HOM-348 (SPEC section 3), judged
full-span. `frac` is the evidence fraction (threshold 0.45), `mode` the most common peak
count, `flat` the spectral flatness median of active frames (recorded, not part of the rule),
`L2`/`L3` the shape-change medians at 32/48 ms.

| Signal | Rate | flat | L2 | L3 | mode | frac | Class |
| --- | --- | --- | --- | --- | --- | --- | --- |
| mod_tone | 8 kHz | 0.000 | 0.000 | 0.000 | 1 | 1.000 | tone |
| mod_tone | 16 kHz | 0.000 | 0.000 | 0.000 | 1 | 1.000 | tone |
| mod_tone_mulaw | 8 kHz | 0.000 | 0.000 | 0.000 | 1 | 1.000 | tone |
| melody | 8 kHz | 0.043 | 0.009 | 0.010 | 8 | 0.594 | music |
| melody | 16 kHz | 0.026 | 0.005 | 0.006 | 8 | 0.670 | music |
| dtmf | 8 kHz | 0.000 | 0.000 | 0.013 | 2 | 0.699 | tone |
| dial_tone | 8 kHz | 0.000 | 0.000 | 0.000 | 1 | 1.000 | tone |
| ringback 0.1-0.9 | 8 kHz | 0.000 | 0.000 | 0.000 | 1 | 1.000 | tone |
| ringback 0.0-2.0 | 8 kHz | 0.000 | 0.000 | 0.000 | 1 | 0.983 | tone |
| fleurs 0.0-7.86 | 8 kHz | 0.140 | 0.168 | 0.183 | 3 | 0.103 | speech |
| fleurs 0.0-4.0 | 8 kHz | 0.142 | 0.161 | 0.167 | 3 | 0.133 | speech |
| fleurs 3.5-7.86 | 8 kHz | 0.112 | 0.165 | 0.189 | 3 | 0.093 | speech |
| fleurs 0.0-7.86 | 16 kHz | 0.096 | 0.162 | 0.175 | 3 | 0.108 | speech |
| fleurs_over_melody | 8 kHz | 0.104 | 0.147 | 0.162 | 3 | 0.094 | speech |
| fleurs_over_melody | 16 kHz | 0.072 | 0.142 | 0.157 | 3 | 0.099 | speech |
| say s1 (Samantha) | 8 kHz | 0.005 | 0.122 | 0.194 | 2 | 0.156 | speech |
| say s2 (Samantha) | 8 kHz | 0.005 | 0.100 | 0.180 | 2 | 0.269 | speech |
| say s3 (Daniel) | 8 kHz | 0.021 | 0.160 | 0.230 | 4 | 0.055 | speech |

Every non-speech signal also classifies as non-speech on a 2 s sub-span (3.0-5.0 s; ringback
0.0-2.0) at both rates, and every speech control also on its second half. The widest speech
case is `say s2` at 16 kHz with fraction 0.280; the weakest non-speech case is the 10 s melody
at 8 kHz with 0.594. The threshold 0.45 sits between them with at least 0.14 margin on both
sides. The `say` sentences are macOS TTS ("Most riders wear riding boots...", "The meeting
moved to Thursday afternoon...", "Could you repeat the last part...") converted to 8 kHz
mu-law WAV; no Persian voice exists on this Mac (`say -v '?'` has none).

Cost: classifying 600 s at 16 kHz takes 0.73 s on this Mac (budget: under 3 s). The whole
check is one vectorized `rfft` pass over the span's frames.

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

- Speech over loud music was measured only with the music 15 dB under the speech RMS
  (`fleurs_over_melody`). Louder music can make a span count as music and drop real speech.
- Singing is tonal and spectrally steady inside notes; sung lyrics can be removed as music.
  Not measured.
- Real hold music was not measured; the judgement rests on the synthetic melody, the phone
  tones and the FLEURS speech of the SPEC signal set.
- A tone that changes frequency faster than roughly every 150 ms keeps too few steady frames
  and falls back to `speech`. DTMF at 100 ms on/off passes with fraction 0.699.
- Only 8 kHz and 16 kHz mono input is accepted (the adapter always decodes to 16 kHz).
- macOS `say` speech stays speech with margin (worst fraction 0.280 against 0.45), but other
  synthetic voices were not measured.
- Checkpoints written before HOM-348 are not reused by gated runs (the identity gains the
  `nonspeech` key). Old results still load unchanged.
