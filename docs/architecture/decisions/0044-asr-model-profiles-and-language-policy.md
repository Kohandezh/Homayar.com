# ADR 0044: ASR model profiles and the language policy

Date: 2026-10-29 · Status: Proposed (HOM-39, WP09-T03, parent HOM-13, features F25, F26, F27 and F28) · Builds on: ADR 0026, ADR 0042

## Context

The ASR stage runs faster-whisper in process (`backend/providers/whisper.py`). Before this
change it had three problems.

- **No named configurations.** `WHISPER_MODEL`, `WHISPER_DEVICE` and `WHISPER_COMPUTE_TYPE`
  were three separate values. Nothing checked that they agree, or that a builtin manifest
  (ADR 0026) matches the result.
- **Region tags failed.** The request language went to faster-whisper as is. faster-whisper
  1.2.0 only knows base codes: its `Tokenizer` raises
  `ValueError("'fa-IR' is not a valid language code ...")` inside `transcribe`
  (`faster_whisper/tokenizer.py` line 28), so a `fa-IR` job failed with `asr_failed`.
- **Auto mode had no limits.** With no language, Whisper picks from about 100 languages.
  Persian phone audio can come out as Arabic or Urdu, and the result then named that language.

The parent acceptance F27 asks for a Persian held-out WER of at most 20% on the WP13 corpus.
That corpus does not exist yet. The owner asked for a mergeable first step: profiles and the
language policy now, a comparison on local audio that exists now, and a clear hook for WP13.

Production facts: the app server has 2 Tesla P40 GPUs (24 GB each, Pascal, compute capability
6.1). The current image runs CPU Whisper only. The Homayar memory budget on that host is
6016 MiB in total, with 2048 MiB for the worker (`docs/operations/app-server.md`, "Memory
and CPU Budget").

## Decision

### 1. Named profiles (`backend/providers/asr_profiles.py`)

A profile fixes the model, device and compute type together, and names the builtin manifest
whose identity equals its own. `ASR_PROFILE` selects one. Empty (the default) keeps the three
`WHISPER_*` settings exactly as before.

| profile | model | device | compute type | manifest |
| --- | --- | --- | --- | --- |
| `cpu-base` | `base` | cpu | `int8` | `asr.local-whisper.base` (existing) |
| `cpu-small` | `small` | cpu | `int8` | `asr.local-whisper.small` (new) |
| `cpu-medium` | `medium` | cpu | `int8` | `asr.local-whisper.medium` (new) |
| `cpu-large-v3-turbo` | `large-v3-turbo` | cpu | `int8` | `asr.local-whisper.large-v3-turbo` (new) |
| `cpu-large-v3` | `large-v3` | cpu | `int8` | `asr.local-whisper.large-v3` (existing) |
| `gpu-p40-large-v3` | `large-v3` | cuda | `int8_float32` | `asr.local-whisper.large-v3-int8-float32` (new) |

Why these:

- The five CPU profiles span the multilingual Whisper sizes from the smallest (today's
  default) to the strongest. They use `int8`, the compute type of the existing CPU
  manifests, so `cpu-base` and `cpu-large-v3` keep their existing identities.
- `large-v3-turbo` is a named model in faster-whisper 1.2.0
  (`faster_whisper/utils.py`: `"large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"`).
  It keeps the large-v3 encoder and has a much smaller decoder, so it is the candidate between
  `medium` and `large-v3`.
- Every new manifest is `development`, `model_revision: unpinned`. A profile is not a
  qualification. Production still needs an operator-qualified manifest (ADR 0026).
- The device, device index and CPU threads are not part of the provider identity, as before.
  Only the compute type is, because it changes the numbers.

### 2. The P40 compute type is `int8_float32`

Sources, read 2026-10-29:

- CTranslate2 4.8.4 documentation, "Quantization"
  (https://opennmt.net/CTranslate2/quantization.html). int8 is "Supported on: NVIDIA GPU with
  Compute Capability >= 7.0 or Compute Capability 6.1". float16 is "Supported on: NVIDIA GPU
  with Compute Capability >= 7.0". The table "Implicit type conversion on load" has, for 6.1:
  `int8_float32`, `int8_float16` and `int8_bfloat16` all run as `int8_float32`; `int16`,
  `float16` and `bfloat16` run as `float32`. `int8_float32` is an accepted `compute_type`.
  Plain `int8` means "the floating point precision of the original model" for the other
  layers, so it is not one fixed type.
- NVIDIA CUDA C++ Programming Guide 11.8, Table 3 "Throughput of Native Arithmetic
  Instructions" (https://docs.nvidia.com/cuda/archive/11.8.0/cuda-c-programming-guide/index.html#arithmetic-instructions).
  Compute capability 6.1: 16-bit floating-point add, multiply, multiply-add: 2 results per
  clock per multiprocessor. 32-bit: 128. So FP16 math runs at 1/64 of the FP32 rate.
- NVIDIA Tesla P40 data sheet (September 2016,
  https://images.nvidia.com/content/pdf/tesla/184427-Tesla-P40-Datasheet-NV-Final-Letter-Web.pdf):
  "Single-Precision Performance 12 TeraFLOPS", "Integer Operations (INT8) 47 TOPS", "GPU Memory
  24 GB". It gives no FP16 number.

So on a P40, `float16` is both slow in hardware and converted to `float32` by CTranslate2.
`int8_float32` names exactly what the GPU runs, and its identity tells the truth. The
existing manifest `asr.local-whisper.large-v3-fp16` is not changed here. On a P40 it would
declare `float16` while CTranslate2 actually runs `float32`. It must not be used on that GPU.

### 3. The language policy (`backend/speech/language_policy.py`, version `asr-language/1`)

Settings: `ASR_ALLOWED_LANGUAGES` (default `fa,en`), `ASR_DEFAULT_LANGUAGE` (default `fa`),
`ASR_LANGUAGE_MIN_PROBABILITY` (default `0.5`). Invalid values stop the settings from loading.

1. **Normalize.** Strip, lower case, `_` becomes `-`, keep the first subtag: `fa-IR` and
   `fa_IR` become `fa`. Empty and `auto` mean "detect".
2. **Explicit request.** The normalized code is used, even outside the allowed list (the
   operator chose it; manifest routing already checks languages). One `transcribe` call.
3. **Auto request.** The first `transcribe` call runs without a language. faster-whisper
   detects the language inside that call, before it returns, and its segment generator is
   lazy (`faster_whisper/transcribe.py`: `detect_language` runs in `transcribe`;
   `generate_segments` is a generator). The policy reads `info.all_language_probs` (or, when
   that is missing, `info.language` with `info.language_probability`):
   - the best allowed language with probability at or above the threshold is used
     (`detected`);
   - else the default language is used (`fallback`, reason `low_confidence`);
   - with an empty probability list, the default is used (`fallback`, reason `no_speech`).
     The list is empty in two cases: VAD kept no audio (next point), or the library gave
     no detection at all.
   - **No speech after VAD.** If `info.duration_after_vad` is a number at or below 0, VAD
     kept no audio. The probabilities are then treated as empty, so the result is
     `fallback`, reason `no_speech`, in the default language. The adapter returns no
     segments and empty text, does not read the first generator and does not call
     `transcribe` again. Why: VAD found no speech, so any decoded text would be made up
     (F26). Evidence: on 5 s of digital silence, `cpu-base` with faster-whisper 1.2.0 still
     returned 99 language probabilities, top `en` 0.566, with `duration_after_vad` 0.0 and no
     segments (local check, 2026-10-29). In the library, `duration_after_vad` is the length
     VAD kept (`faster_whisper/transcribe.py`, `duration_after_vad = audio.shape[0] /
     sampling_rate` after `collect_chunks`). When the value is missing, not a number or above
     0, nothing changes. Explicit requests do not use this rule.
   Otherwise, if the decision equals Whisper's own language, the first generator is used.
   If it differs, the first generator is never read, and a second call forces the decided
   language. Arabic text for a Persian call is therefore never returned.
4. **Result.** `ASRResult.language` is the decided language. `ASRResult.language_decision`
   records the source, the request, the top detected language and its probability, the reason
   and the policy version. Results written before this change load without it.

### 4. Checkpoint identity

An explicit language does not depend on the policy, so the ASR checkpoint identity stays
byte-identical to the one before this change, and existing checkpoints are reused. An auto
request adds `"language_policy": {"version", "allowed", "default", "min_probability"}` to the
identity's request, next to `"language": "auto"`. A policy change then recomputes ASR for
auto jobs only.

### 5. ASR input: the original file, no gain

ASR keeps reading the ORIGINAL file, with no input gain. This answers ADR 0042 R1 for now.
Feeding ASR the `NORMALIZED` derivative would change transcripts and checkpoint identities,
and it needs the chunker. That is WP09-T04.

### 6. Comparison tool, the WP13 hook (second pull request of HOM-39)

The second pull request of HOM-39 adds `backend/speech/asr_compare.py`. Its CLI,
`python -m backend.speech.asr_compare`, scores profiles on a JSONL corpus (`id`, `audio`,
`reference`, `language`, optional `slices`, `split`, `sha256`), each profile in its own
process. It reports WER and CER per slice with `backend.speech.benchmark`, real-time factor,
load time, peak memory, language-ID accuracy and fallbacks, and the F27 gate verdict. It
never records a qualification and never changes a registry or a manifest. The same pull
request adds the first local comparison under `docs/research/asr/`. That comparison informs
the choice of a default profile. This ADR does not change the default.

### Product rules (defaults chosen here, for the owner)

| # | Question | Default |
| --- | --- | --- |
| P1 | Which profile in production? | None set: `ASR_PROFILE` stays empty and `WHISPER_MODEL=base`, as before. The comparison in the second pull request informs a later change; this ADR does not change the default. |
| P2 | Allowed languages for auto | `fa,en` |
| P3 | Default language | `fa` |
| P4 | Detection threshold | `0.5`, inclusive |
| P5 | Explicit language outside the allowed list | Honoured |
| P6 | Show the language decision to users | Not in this change. It is stored in the ASR result only. |

## Consequences

### Rollback and deployment

- The new code writes every ASR result with a `language_decision` key (`null` for providers
  that do not fill it; artifacts are written with `exclude_none=False`). The old code's
  `ASRResult` forbids unknown keys (`extra="forbid"`). After a rollback, the old code finds
  such an ASR checkpoint invalid (`ArtifactInvalid` in `backend/jobs/checkpoints.py`),
  invalidates it and runs ASR again. So a rollback re-runs ASR for meetings processed since
  the deploy. No data is lost.
- Do not run two worker versions at the same time. When an old worker reads an ASR
  checkpoint that the new code wrote for the same identity (an explicit language), it finds
  it invalid, invalidates it and runs ASR again. The new code reads the old worker's
  artifact without a problem, so this costs at most one extra ASR run per such meeting; it
  does not loop. Auto-language identities differ between the versions (the new one adds the
  policy), so the two versions never read each other's auto-language checkpoints.
- `infra/compose/compose.cloud.yaml` and `infra/compose/compose.onprem.yaml` pass
  `ASR_PROVIDER` and the three `WHISPER_*` settings, but not `ASR_PROFILE`,
  `ASR_ALLOWED_LANGUAGES`, `ASR_DEFAULT_LANGUAGE` or `ASR_LANGUAGE_MIN_PROBABILITY`. Inside
  those stacks the new settings keep their defaults until an infra follow-up passes them.
  HOM-42 needs that to select `gpu-p40-large-v3`.

### Effects

- An operator picks one profile name. The settings refuse an unknown name, a profile with a
  provider that is not local Whisper, and a policy that cannot work.
- `fa-IR` jobs now run as `fa` instead of failing.
- Auto jobs can only end in an allowed language. A second decode of the same file (audio
  decode, VAD, features and decoding run again) happens only when VAD kept some audio and
  the decided language differs from Whisper's own pick. A fallback to the language Whisper
  picked needs one call, and so does a `no_speech` result because VAD kept no audio. A
  `no_speech` result because the library gave no detection while VAD kept audio follows
  the general rule: a second call when the default language differs from `info.language`
  (for example when `info.language` is missing). faster-whisper 1.2.0 always returns a
  detection for a multilingual model, so this last case is not expected in practice.
- Explicit-language checkpoints are reused across this change. Auto-language checkpoints are
  recomputed once, because their identity gains the policy.

### Limits

- The first comparison (second pull request) uses read speech from Google FLEURS on a Mac
  CPU. That is not call-centre speech and not the production Xeon. Its numbers are not a
  qualification.
- `no_speech` depends on VAD. When VAD keeps a little noise, faster-whisper decodes it and
  the policy sees a normal detection. Only "VAD kept nothing" is treated as no speech. An
  empty result ends the stage as an empty transcript (`INSUFFICIENT_EVIDENCE`), the same
  as silent audio before this change.
- VAD can drop real speech on phone audio. With the default Silero VAD options of
  faster-whisper 1.2.0, VAD removed all speech from 1 of 150 FLEURS items converted to 8 kHz
  mu-law (clear read English, 7.86 s; its 16 kHz original kept 6.15 s). Local check,
  2026-10-30. For an auto request, the no-speech rule (decision 3) ends such a call as an
  empty transcript with `fallback`, `no_speech`. For an explicit request (the upload
  default is the explicit language `fa`) the transcript is also empty, with source
  `requested`, because the explicit path also runs VAD. Both were already empty before
  this change. VAD
  settings for 8 kHz audio are follow-up work (WP09-T04).
- The GPU profile has no GPU run yet. Its throughput claim rests on the sources in decision 2.

## Sources

All read on 2026-10-29.

- faster-whisper 1.2.0, package source (wheel `faster_whisper-1.2.0-py3-none-any.whl`, sha256
  `e5535628fe93b5123029b410fd8edba2d28f8cee9f8fff8119138e5a9d81afbe`, from PyPI):
  `utils.py` `_MODELS` (model names and repositories); `transcribe.py`
  `WhisperModel.__init__` (`device_index: Union[int, List[int]] = 0`, `cpu_threads: int = 0`),
  `WhisperModel.transcribe` (detection before return, lazy `generate_segments`),
  `TranscriptionInfo.all_language_probs: Optional[List[Tuple[str, float]]]`; `tokenizer.py`
  (invalid language code raises); `vad.py` `collect_chunks`.
- CTranslate2 4.8.4 documentation, "Quantization":
  https://opennmt.net/CTranslate2/quantization.html. The repository lock has ctranslate2
  4.8.1 (the same 4.8 line).
- NVIDIA CUDA C++ Programming Guide 11.8, section 5.4.1, Table 3 "Throughput of Native
  Arithmetic Instructions":
  https://docs.nvidia.com/cuda/archive/11.8.0/cuda-c-programming-guide/index.html#arithmetic-instructions
- NVIDIA Tesla P40 data sheet (September 2016):
  https://images.nvidia.com/content/pdf/tesla/184427-Tesla-P40-Datasheet-NV-Final-Letter-Web.pdf

## Deferred

- HOM-41: warm workers, model loading at start and concurrency.
- HOM-42: the GPU image, the `gpu-p40-large-v3` run on the P40s and its qualification.
- WP09-T04: feeding ASR the derivative, chunking, model input gain.
- WP09-T07 and WP13: the measurement on the WP13 corpus (F27) and any qualification.
- Exposing the language decision on the transcript API or the console.

## Alternatives considered

- **Pass `language=None` and trust Whisper.** Refused: it allows any of about 100 languages,
  which is the bug.
- **Call `WhisperModel.detect_language` first, then `transcribe` once.** Not chosen: it is a
  separate entry point with its own VAD handling, and the detection inside `transcribe` is
  the one the transcript uses. The chosen pattern costs nothing extra when the policy agrees.
- **Pick the GPU profile as `float16`.** Refused: CTranslate2 runs it as `float32` on
  compute capability 6.1, and the identity would not match the numbers.
- **Plain `int8` on the GPU.** Not chosen: its meaning depends on how the model was saved.
  `int8_float32` is explicit and gives the same result on a P40.
