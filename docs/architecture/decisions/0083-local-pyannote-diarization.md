# ADR 0083: Local pyannote diarization

Date: 2026-11-14 · Status: Proposed (HOM-44, features F30/F31/F33) · Builds on: ADR 0026

## Context

Diarization was never real. The factory could build only an `unavailable`
provider and a mock. Every speaker stayed `speaker_unknown`, so the speaker
timeline (ADR 0069) and speaker alignment (ADR 0072) had nothing to work with.

The ticket picks the pyannote pipeline `pyannote/speaker-diarization-community-1`.
It is a local, offline-capable diarization pipeline under CC-BY-4.0. The model
is gated on Hugging Face behind a licence form, so the files cannot be fetched
by a script. The owner accepted the gate and copied the files by hand onto the
GPU server, which keeps models as `<artifact root>/<stage>/<name>` (its root is
`/home/gpu/homayar/models`, ASR is in `asr/large-v3`).

The deployment rule (ADR 0026) is that a model runs only under a manifest, and
an offline model proves its local files before any job. Nothing may be
downloaded at run time and no Hugging Face token may be used or stored.

## Decision

### D1. Adapter and port

`backend/providers/pyannote_diarization.py` adds
`PyannoteDiarizationProvider`, a `DiarizationProvider` built by the factory
under the names `pyannote_local`, `pyannote` and `pyannote_community_1`. The
default `diarization_provider` stays `unavailable`. A new setting
`diarization_device` (default `cpu`) picks the torch device.

### D2. Why community-1, why local files only

Community-1 is the maintained pyannote pipeline that keeps overlapping speech
and needs no server. It runs in process, next to the worker, so no audio
leaves the machine and the offline profile holds. The adapter loads the
pipeline only from a local directory under the deployment's
`model_artifact_root`:

```
diarization/pyannote-community-1/
  config.yaml
  segmentation/pytorch_model.bin
  embedding/pytorch_model.bin
  plda/plda.npz
  plda/xvec_transform.npz
```

The loader gets the absolute directory path, never a Hub id, and never a token.

### D3. Per-file sha256 pins, refuse on missing

The manifest `diarization.pyannote.community-1` v1 pins the model revision
`3533c8cf8e369892e6b79ff1bf80f7b0286a54ee` and each of the five files by the
sha256 the owner's server files carry (checked on the server with `sha256sum`).
Before the pipeline is loaded, every file must have a pin, and the bytes must
match it (through `providers.artifacts.verify_artifacts`, the same check the
offline profile uses). A symlink counts as unsafe. Any gap is a refusal: an
UNAVAILABLE result with issue code `diarization_artifact_root_unset`,
`diarization_model_unpinned` or `diarization_model_integrity`. The refusal
message names the paths and statuses only, never file contents. A refusal is
never cached as a success.

The licence attribution for the weights is recorded in `Homayar/NOTICE.md`
(name, source URL, revision, copyright holder, CC BY 4.0 link, "not modified;
loaded as is from local files") and in the manifest notes.

### D4. Output contract

The whole recording is diarized in one call
(`{"audio": path, "uri": "recording"}`, plus `max_speakers` only when a
speaker count hint exists; never `num_speakers`). The result is read from
`output.speaker_diarization`, which keeps overlapping speech. The exclusive
variant is never used, because overlap must not be erased (F31). Labels are
renamed to anonymous `speaker_1`, `speaker_2`, ... by the time each speaker
first talks. No pyannote label and no role name is emitted. `confidence` stays
`None`: pyannote gives no calibrated probability. Times round to 3 decimals,
start is clamped at 0, and a segment that rounds to nothing is dropped.

Because the whole call is diarized at once, labels are already global. The
HOM-46 `reconcile_chunk_speakers` step (ADR 0071) would matter only for a
chunked mode. This ADR does not add one.

### D5. Telemetry and dependency state

pyannote 4 sends usage metrics by default. The loader sets
`PYANNOTE_METRICS_ENABLED=0` before the import and calls
`set_telemetry_metrics(False)` after it when the function exists. No token is
used or stored anywhere.

pyannote.audio and torch are not dependencies yet, and nothing is installed in
this PR. The adapter imports them lazily inside the loader, so the module and
the factory load without them, and a missing library is a refusal with code
`diarization_runtime_missing`. A follow-up adds `pyannote.audio` to the
`ai-local` extra together with `uv.lock`, the image change and one opt-in run
on the GPU server. Until that follow-up lands, the adapter reports
`diarization_runtime_missing` even on the server that holds the files.

## Consequences

- A deployment that wants real diarization sets `diarization_provider` to
  `pyannote_local`, copies the five files under the artifact root at
  `diarization/pyannote-community-1` and installs the library follow-up. The
  pins are already in the builtin manifest, so any wrong or missing file is a
  refusal before the pipeline loads.
- The builtin manifest has status `development`, so strict (production)
  preflight refuses this model until HOM-50 measures DER on representative
  audio. That is intended.
- Building the provider never imports pyannote or torch; only a passing load
  gate does. The load runs off the event loop (`asyncio.to_thread`) under a
  lock, so one worker loads the pipeline once.
- An opt-in test marker `pyannote_local` runs the real pipeline on a local WAV
  with the network blocked, only when `HOMAYAR_PYANNOTE_TEST=1` and
  `MODEL_ARTIFACT_ROOT` hold the pinned files. It stays NOT_RUN in this PR
  because the library is not installed here.

## Limits

- DER is not measured yet (HOM-50). Persian call audio in particular is
  unmeasured, so quality claims wait for that ticket.
- The library is not installed in this PR, so no run in this PR proves the
  real pipeline end to end; the gate, the pins and the refusal paths are
  covered by offline tests, and the first real load is the opt-in GPU run.
- The model is monolithic: the five files must all come from the same
  community-1 revision, or the pins will disagree.
- Chunked diarization, speaker count forcing (`num_speakers`) and any role
  mapping are out of scope.
