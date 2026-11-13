# ADR 0067: Warm ASR workers and the worker memory budget

Date: 2026-11-13 · Status: Proposed (HOM-41, WP09-T05, parent HOM-13, feature F28) · Builds on: ADR 0044, ADR 0046

## Context

The Celery job path builds a fresh provider bundle for every job
(`backend/jobs/processor.py`, `create_provider_bundle(settings)`). Each bundle builds a
new `LocalWhisperProvider`, and that object kept the loaded model only on itself
(`self._model`). So every job loaded the faster-whisper model again.

HOM-351 measured this on a Tesla P40 with `large-v3` `int8_float32` (Linear comment on
HOM-41): each job pays 12 to 14 s of load time and a 3 GB memory spike. At a 2 GiB worker
limit the load is OOM-killed. At 4 GiB it works, with a peak of 3.0 GiB RAM and 2.9 GiB
VRAM. The GPU demo runs `CELERY_CONCURRENCY=1` with `mem_limit 5g`.

Nothing tied the worker process count to memory. Parent acceptance F28 asks for three
things: ten sequential jobs reuse the warmed model per configured worker process,
configured concurrent load stays within the memory budget without OOM, and queue fairness
and latency are measured on the exact qualified hardware. The last part is external
qualification. This ADR delivers the first two with fakes and counters.

One root cause sits behind all of it: model lifetime is tied to a per-job object, and
worker concurrency ignores memory.

## Decision

### D1. A process-wide warm model cache (`backend/providers/warm_models.py`)

A module-level store shared by every `LocalWhisperProvider` in the process.
`get_or_load(key, loader)` runs `loader` once per key; concurrent callers of the same
missing key wait on a per-key lock, and every waiter counts as a hit. A failed loader
stores nothing: the failure is counted and the next call tries again. `stats()` reports
`loads`, `hits`, `failures` and `keys`; `clear()` resets the store (tests).

`warm_configured_asr(settings)` loads the configured model at worker start. It returns
`False` and loads nothing when `ASR_WARM_ON_START` is off or the provider is not
`local_whisper`. A failed load logs exactly one `asr_warm_failed` warning with the error
code or exception class name only, without `exc_info`: a traceback would carry the model
path and the message text (log privacy). It never raises.

### D2. The cache key

`("local_whisper", source, device, compute_type, device_index as a tuple, cpu_threads,
local_files_only, factory)`, where `source` is `model_path or model_name` and `factory`
is the injected `model_factory` object or the string `"faster_whisper.WhisperModel"`.
Every value that changes what is loaded is in the key. The language policy is not: two
policies over one model share one load. An injected `model=` bypasses the cache
completely, as before.

### D3. The provider uses the cache (`backend/providers/whisper.py`)

`_get_model()` takes the model from `warm_models.get_or_load`. The `asr_model_missing`
check (model directory absent) runs inside the loader, so it runs only on a cache miss.
The per-instance fast path stays: after the first call an instance keeps the reference.
The default factory is still imported lazily inside the loader, so an `ImportError`
(faster-whisper not installed) counts as a failure and maps to `asr_unavailable` as
before.

### D4. Memory-aware concurrency (`backend/jobs/worker_capacity.py`)

Constants: `WORKER_BASE_MEMORY_MIB = 512` (one worker process without a model) and
`ASR_BATCH_SLOT_MEMORY_MIB = 256` (each batch slot above 1). Every ASR profile gains
`memory_mib`, the resident size of one loaded model:

| profile | memory_mib |
| --- | --- |
| cpu-base | 512 |
| cpu-small | 1024 |
| cpu-medium | 2048 |
| cpu-large-v3-turbo | 2560 |
| cpu-large-v3 | 3584 |
| gpu-p40-large-v3 | 4608 |

The GPU value comes from HOM-351: a 3.0 GiB peak, OOM at a 2 GiB limit, and the demo
running at 5 GiB. 4608 plus the 512 MiB worker base equals the demo's 5120 MiB limit.
Without a profile, a model-name table is used (`tiny` 384, `base` 512, `small` 1024,
`medium` 2048, `large-v3-turbo` 2560, `large-v3` 3584). `ASR_MODEL_MEMORY_MIB` overrides
both. `asr_memory_estimate_mib` returns 0 for a provider that holds no model and `None`
for an unknown model name.

`check_worker_memory` turns a budget into a `CapacityDecision`: `no_budget` (budget 0),
`no_asr_model`, `unknown_model_memory`, `within_budget`, `over_budget` (clamped) or
`budget_too_small` (refused). `worker_processes` counts the processes that each hold a
model: prefork gives the concurrency, `solo`, `threads`, `gevent` and `eventlet` give 1,
an unknown pool counts as prefork.

### D5. Celery wiring (`backend/celery_app/__init__.py`)

A `worker_init` receiver runs in the parent, after concurrency is final and before the
pool is created. It logs one `worker_memory_check` event with the decision fields, stores
the decision in a module-level variable, and a `worker_ready` receiver logs it again at
`INFO`: at `worker_init` Celery has not set up logging yet, so the first `INFO` record can
be dropped. When autoscale is set, the budget check uses the autoscale maximum as the
process count, not `sender.concurrency`: the pool can grow to that maximum.

On `over_budget` the receiver clamps `sender.concurrency` to `allowed_processes`, and the
autoscale too: `sender.autoscale` and `sender.options["autoscale"]` both become the list
`[allowed_processes, min(old_min, allowed_processes)]`. On the command line the autoscale
option reaches the worker either already parsed, as a tuple or list `(max, min)`, or as
the string `"max,min"` where the min may be empty (meaning 0); both forms are parsed.
The Celery Pool bootstep re-reads autoscale from `sender.options` after `worker_init`, so
both places must carry the clamped value. On `budget_too_small` or
`unknown_model_memory` it logs one error event and raises `SystemExit(78)`: Celery's
signal dispatch swallows every `Exception` a receiver raises, but not `SystemExit`, so
the worker does not start.

A `worker_process_init` receiver runs in each prefork child and calls
`warm_configured_asr(get_settings())`. It never raises. The receiver never loads a model
itself in the parent: the parent forks the children, and a CUDA context made before a
fork is not usable in the child. `worker_process_init` fires only in prefork children:
with the `solo` or `threads` pool no warm step runs, and the first job loads the model,
which the cache then keeps. `worker_max_tasks_per_child` stays `None`: recycling a child
process would drop its warm model.

A prefork child warms the model inside its initializer, before it reports up. Celery
kills a child that stays silent for `worker_proc_alive_timeout`, whose default is 4.0 s,
and a large model loads in 12 to 14 s (HOM-351). So `make_celery` sets
`worker_proc_alive_timeout` to `WORKER_PROC_ALIVE_TIMEOUT_SECONDS = 120.0`.

### D6. Opt-in bounded batching (`ASR_BATCH_SIZE`, 1 to 16, default 1)

With `batch_size > 1` and `vad_filter` on, the main decode call and the VAD rescue call
(HOM-40) go through `faster_whisper.BatchedInferencePipeline(model=<the cached model>)`,
called with today's keyword arguments plus `batch_size`. The pipeline is built once per
cached model and stored in the warm cache under the model key plus `("batched",)`. The
batch size is not part of the model key: a `batch_size=1` provider and a `batch_size=4`
provider share one model. Language detection (`_detect`, `decide_language`) always uses
the plain model. An auto-language job whose detection keeps the plain model path (the
policy agrees with the detected language, so no decode call with a language happens) is
decoded on the plain model, and no pipeline call is added for it. With
`batch_size == 1`, or `vad_filter` off, the code path is exactly today's plain path.

### D7. Checkpoint identity

Batching changes the transcript numerics, so a batched result must not be reused as an
unbatched checkpoint. The processor adds `"decode": {"batch_size": n}` to the ASR stage
request identity when the provider's `decode_identity()` returns a dict. `identity()`
does not change for any batch size: manifest routing compares identity params exactly,
so a new param there would break model preflight. With `batch_size == 1` the identity
stays byte-identical to the base. The label stays conservative: an auto-language job
that happened to decode on the plain model still carries the batch size in its identity,
so a batched provider never reuses an unbatched checkpoint, at most it recomputes one.

### D8. Settings and configuration surfaces

`ASR_WARM_ON_START` (`true`), `ASR_BATCH_SIZE` (`1`, bounds checked),
`WORKER_MEMORY_BUDGET_MIB` (`0`, no budget known, no limit applied) and
`ASR_MODEL_MEMORY_MIB` (`0`, use the built-in estimate). All four reach the `worker`
service environment of the Compose files only, with the same defaults: no other service
loads a model or clamps its concurrency. `Homayar/.env.example` documents them.

### Product rules (defaults chosen here, for the owner)

| # | Question | Default |
| --- | --- | --- |
| P1 | Warm the model at worker start? | Yes. A worker that only serves non-ASR queues still warms only when the provider is `local_whisper`. |
| P2 | Worker memory budget | 0: no budget known, nothing is clamped or refused. The operator sets it on the GPU host. |
| P3 | Batch size | 1 (today's path). Batching is opt-in until measured on the P40. |
| P4 | Model memory estimate | Built-in table by profile or model name; `ASR_MODEL_MEMORY_MIB` overrides. |

## Consequences

- Ten sequential jobs through the per-job path load the model once per process; the
  other nine are cache hits. The first job after a worker start pays nothing when
  warm-on-start is on.
- A set budget either passes, clamps the concurrency (and autoscale) to what fits, or
  stops the worker at start with exit code 78 and one error event. Budget 0 keeps
  today's behaviour exactly.
- Batched decode gets a new checkpoint identity. Rolling back to `ASR_BATCH_SIZE=1`
  invalidates the batched checkpoints and recomputes those meetings once. Unbatched
  checkpoints are unaffected in both directions.
- The memory numbers are static estimates, not measurements of this host. An unknown
  model name with a set budget refuses to start rather than guess; the override fixes
  it.
- The receivers log event names with decision fields and no paths, no exception
  messages and no tracebacks, in line with the log privacy rules.

## Alternatives considered

- **A singleton provider or a cached bundle.** One shared bundle would cross config
  changes (a profile switch must load a new model) and hide which config a job used.
  The per-job provider with a process-wide cache keeps the job path unchanged.
- **`worker_max_tasks_per_child` to bound leaks.** Recycling a child drops its warm
  model and returns to a per-cycle load spike. It stays `None`.
- **Clamp in the worker command line or the deployment only.** The check would not see
  the settings, the pool or the profile, and every deployment would have to redo it.
- **Warm the model in `worker_init` (the parent).** A CUDA context made before a fork
  is not usable in the child. Warming happens in each child (`worker_process_init`).
- **Batch by default.** It changes checkpoint numerics and memory per slot before any
  measurement exists. Opt-in with bounds instead.
- **A dedicated ASR queue instead of the clamp.** Needed for fairness, but it does not
  protect a worker whose concurrency is already too high for its memory. Deferred to
  HOM-42; the clamp applies now.

## Follow-ups

- A dedicated ASR queue and a VRAM budget (HOM-42).
- Measured GPU queue fairness and latency on the exact qualified hardware (external
  qualification, F28).
- Calibrate `memory_mib` and `ASR_BATCH_SLOT_MEMORY_MIB` on real hardware; today they
  come from the HOM-351 peak and the demo limit.
