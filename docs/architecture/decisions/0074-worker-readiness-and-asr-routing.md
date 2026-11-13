# ADR 0074: Worker readiness and routing ASR jobs to qualified workers

Date: 2026-11-13 · Status: Proposed (HOM-42, WP09-T06, parent HOM-13, features F25 to F28) · Builds on: ADR 0067, ADR 0044

## Context

Every Celery job used one queue, `processing`. A meeting job (`backend.jobs.process_meeting`:
ASR, diarization and analysis for one meeting) went there like every periodic sweep. Every
worker consumed it. Nothing checked that a worker could run the configured ASR model. A
worker without CUDA, or with too little free VRAM, took a GPU job and failed it during the
model load, or the job ran on a slower path nobody chose.

The production worker runs the `-gpu` image with `ASR_PROFILE=gpu-p40-large-v3` and
`CELERY_CONCURRENCY=1`, through a server-side overlay that is not in this repository. The
overlay may replace the worker `command`, and it may set `ASR_PROFILE` on the worker only,
not on the api.

HOM-108 already built host checks for the operator preflight
(`backend/providers/host_checks.py`): the local ASR runtime, the NVIDIA driver, free VRAM on
the target GPU, every side effect injectable. HOM-41 (ADR 0067) added the worker memory
budget, the warm model cache and the `worker_init` receiver.

The owner's terms: each worker advertises what it is qualified for after a readiness check
at start; ASR jobs go to a dedicated queue that only qualified workers consume; a job that
needs a GPU never runs on a CPU-only worker (it waits or fails clearly, it never silently
degrades); readiness is visible to operators; today's single-worker production keeps
working with the defaults. Real GPU qualification is external.

## Decision

### D1. Three declared queues (`backend/celery_app/__init__.py`)

`processing` (the default), `asr-cpu` and `asr-gpu`. `ASR_QUEUES = (asr-gpu, asr-cpu)` and
`DECLARED_QUEUES = (processing, asr-cpu, asr-gpu)`. `task_routes` sends
`backend.jobs.process_meeting` to `asr_queue_for(settings)`. Every other task and every beat
entry stays on `processing`.

### D2. The job's requirement decides its queue (`backend/jobs/worker_readiness.py`)

`asr_requirement(settings)` is `remote` when the ASR provider is not `local_whisper` (the
factory's name rule). Otherwise it is the device of the profile, or `WHISPER_DEVICE` without
a profile: `cuda` or `cuda:<n>` gives `gpu`, anything else `cpu`. It reads settings only: no
adapter is built and no model is loaded. `asr_queue_for` gives `asr-gpu` for `gpu` and
`asr-cpu` for `cpu` and `remote`. `publish_processing_job` publishes to that queue. The
message arguments do not change.

### D3. The readiness check

`assess_worker_readiness(settings, *, pool, concurrency, probes, bundle)` returns a frozen
`WorkerReadiness` record and never raises. The first matching rule decides:

| Case | qualified | reason | queues |
| --- | --- | --- | --- |
| `WORKER_ASR_QUEUES=off` (no probe runs) | no | `asr_queues_off` | none |
| requirement `remote` (no probe runs) | yes | `no_local_model` | `asr-cpu` |
| the ASR adapter cannot be built | no | `asr_adapter_failed` | none |
| `cpu`, `asr_runtime` fails | no | `asr_runtime_failed` | none |
| `cpu`, `asr_runtime` passes or warns | yes | `ready` | `asr-cpu` |
| `gpu`, `asr_runtime` fails | no | `asr_runtime_failed` | none |
| `gpu`, `gpu_driver` fails | no | `gpu_driver_failed` | none |
| `gpu`, VRAM need unknown | no | `unknown_model_vram` | none |
| `gpu`, a VRAM budget is set and the need is above it | no | `vram_over_budget` | none |
| `gpu`, `gpu_memory` fails (free VRAM below the need, unknown, or no target GPU) | no | `gpu_memory_failed` | none |
| `gpu`, every check passes (`warn` counts as a pass) | yes | `ready` | `asr-gpu`, `asr-cpu` |

An unexpected exception gives `readiness_error` with the exception class name and no queue.

A GPU-qualified worker also takes `asr-cpu`: it can run every CPU job, and the api publishes
to `asr-cpu` when it does not see `ASR_PROFILE`. A CPU worker never takes `asr-gpu`.

The checks are the HOM-108 functions, through one small public wrapper
(`host_checks.check_asr_host`). `run_host_checks` and `model_preflight --host` do not change.

VRAM need per process: `ASR_MODEL_VRAM_MIB` when above 0, else the profile's new `vram_mib`
(`gpu-p40-large-v3`: 3584; HOM-351 measured a 2.9 GiB peak, so about 600 MiB headroom; every
`cpu-*` profile: 0), else the HOM-41 model-name memory table as a conservative stand-in, else
unknown. Each batch slot above 1 adds 256 MiB. The total is the need per process times the
model-holding processes (`worker_processes`: prefork gives the concurrency, `solo` and
`threads` give 1). There is no VRAM clamp: a worker that does not fit is not qualified, and
`vram_allowed_processes = floor(min(free VRAM, budget if set) / need per process)` tells the
operator what to set.

RAM is copied from the HOM-41 decision into the record (`ram_budget_mib`,
`ram_per_process_mib`, `ram_decision`). It does not change `qualified`: HOM-41 already clamps
or refuses at start.

### D4. CTranslate2 is read in a fresh interpreter

`default_probes()` is `HostProbes(ctranslate2=subprocess_ctranslate2)`. The probe runs
`[sys.executable, "-c", <script>]` (an argument list, no shell, 60 s timeout). The child
prints the CUDA device count and the supported compute types as JSON. Any failure (missing
module, non-zero exit, timeout, bad JSON) gives `None`, which the runtime check reports as a
failure. `ctranslate2` is never imported in the worker parent: a CUDA context made before
the fork is not usable in the children (ADR 0067 D5).

### D5. Worker wiring

`on_worker_init` runs the HOM-41 memory check first. A refusal still exits with code 78
before readiness runs. After a clamp, readiness sees the clamped process count. Then it
assesses readiness, stores it (`current_worker_readiness()`), and sets the worker's queue
selection on `sender.app.amqp.queues`:

1. Start from the worker's `--queues` selection, or `processing` without one. Without
   `--queues`, Celery would consume every declared queue, `asr-gpu` included.
2. Remove every ASR queue the readiness does not hold. An ASR queue the operator named in
   `--queues` logs one `worker_queue_refused queue=<name> reason=<reason>` error.
3. Add every ASR queue the readiness holds.
4. Never leave the selection empty: Celery reads an empty selection as "every queue". An
   empty result selects `processing`.

The selection is applied with `Queues.select`, so `consume_from` holds exactly that set, and
Celery's task consumer is built from `consume_from`. The compose command keeps
`--queues processing`: the worker adds its ASR queues itself, so an overlay that replaces the
command still works.

`-X` / `--exclude-queues` is not supported for the worker at all. The worker decides its ASR
queues from readiness and never leaves its selection empty, so `-X` does not do what it says:
a qualified ASR queue is added back, and with `-X` an unqualified worker still consumes
`processing` (the fallback) and logs `worker_queue_refused` for ASR queues the operator never
named (`-X` left them in the selection). Use `--queues` (the compose command keeps
`--queues processing`) and `WORKER_ASR_QUEUES` (`off` for a worker that must not take meeting
jobs) instead. A sender without an app (the HOM-41 test fakes; a real `WorkController`
always has one) skips readiness and selection.

`on_worker_ready` logs one INFO `worker_readiness` event with the scalar fields of the
record, and one ERROR `worker_not_qualified reason=<reason> requirement=<cpu|gpu>` for an
unqualified worker with a local requirement. The HOM-41 `worker_memory_check` log stays.

`on_worker_process_init` skips the model warm when a stored readiness holds no ASR queue
(`asr_warm_skipped reason=<reason>`). With no stored readiness it warms as before.

A Celery inspect command `worker_readiness` returns the stored record as a dict, or
`{"reason": "unknown"}`.

### D6. A task guard

`process_meeting_job` checks the delivery before the payload check and before any ASR work.
Only a broker delivery is checked: eager mode and a direct call (`task.run(...)`, Celery's
`request.called_directly`) have no delivery and skip the guard.
`asr_delivery_decision(readiness, delivery_queue, settings)`:

- a delivery from an ASR queue: `run` when this worker holds that queue, else `requeue`
  (`Reject(requeue=True)` and an `asr_job_requeued` warning). The worker removed that queue
  from its selection at start, so this happens only in a race or after a manual
  `add_consumer`. `Reject(requeue=True)` puts the message back at once, so before it the task
  asks this worker to stop consuming that queue:
  `celery_app.control.cancel_consumer(queue, destination=[<this worker>])`, best effort
  (`asr_consumer_cancelled queue=<q>`, or `asr_consumer_cancel_failed queue=<q>
  error=<class>`; it never raises anything but the `Reject`). A qualified worker then takes
  the message.
- any other delivery (`processing`, no routing key): a message published before this
  change. `run` when this worker holds the job's queue (`asr_queue_for(settings)`), else
  `reroute`: the job is published again to its queue (`asr_job_rerouted from=<q> to=<q>`)
  and the task returns `{"status": "rerouted"}`. The job row is not touched. When that
  publish fails, the message is rejected with requeue and an `asr_job_reroute_failed` error
  is logged, so the job is not lost.

A worker with no stored readiness (for example a sender without an app) assesses once in the
process (`solo`, concurrency 1) and keeps the result.

### D7. Operator visibility

- The `worker_readiness` and `worker_not_qualified` log events.
- `/metrics`: `homayar_broker_queue_length` once per declared queue, one `llen` each, in the
  same Redis probe and deadline as before.
- `GET /api/v1/admin/system-health`: `queue.broker_queue_length` is the sum over the three
  queues (`None` when Redis did not answer). The response schema does not change.
- `python -m backend.jobs.worker_readiness --check` assesses this container and prints the
  record (exit 0 qualified, 1 not qualified, 2 configuration error). `--workers` asks the
  live workers (`worker_readiness`, `active_queues`, 2 s timeout) and reads the three queue
  lengths. `workers_report` lists the workers by hostname and each queue with its length and
  consumer count. `ok` is false when an ASR queue has waiting messages and no consumer, or a
  value is missing (exit 1); exit 2 when the broker cannot be reached.

### D8. Privacy

`to_dict()` and every log line hold no GPU UUID (the check records drop it), no path, no URL,
no secret and no exception message. Exception class names only.

### Product rules (defaults chosen here, for the owner)

| # | Question | Default |
| --- | --- | --- |
| P1 | Does a worker take ASR queues? | `WORKER_ASR_QUEUES=auto`: the ones it qualifies for. `off` takes none. |
| P2 | Worker VRAM budget | `WORKER_VRAM_BUDGET_MIB=0`: no budget, only the free VRAM snapshot counts. |
| P3 | VRAM need of the model | Built-in: profile `vram_mib`, else the model-name table. `ASR_MODEL_VRAM_MIB` overrides. |
| P4 | A GPU job and no qualified worker | It waits in `asr-gpu`. It never runs on the CPU. |
| P5 | A GPU worker and CPU jobs | It takes them (`asr-cpu`). |

All three settings reach the `worker` service only, in both compose files, with these
defaults. `Homayar/.env.example` documents them.

## Consequences

- Production with the defaults keeps working: the api publishes to `asr-cpu` (it does not
  see the profile), and the GPU worker qualifies for `asr-gpu` and `asr-cpu` when its probes
  pass (3584 MiB need, the P40 has far more free).
- A worker that fails its check still runs every periodic sweep on `processing`, but no
  meeting job, and it does not warm a model. The `worker_not_qualified` error says why.
- If no worker qualifies, meeting jobs wait in their queue. The lease and dispatch sweeps do
  not change: the job row stays queued until a qualified worker takes the message.
- Messages already in `processing` when this ships are rerouted or run by the first worker
  that takes them. Nothing is lost.
- Free VRAM is a snapshot taken at start on a shared GPU. A later drop is not seen until the
  next start.
- The CTranslate2 probe starts one short-lived Python child per worker start.
- `broker_queue_length` in system health now counts the ASR queues too, so its number is
  comparable with the time before this change, when meeting jobs waited in `processing`.

## Alternatives considered

- **One queue per profile.** Many queues for one real distinction (GPU or not). Two ASR
  queues cover today's profiles; a profile-level split can come later.
- **Only route with `--queues` in the compose command.** The server overlay may replace the
  command, and a worker without `--queues` would consume `asr-gpu`. The worker selects its
  own queues instead.
- **Fall back to the CPU when the GPU check fails.** The owner ruled it out: no silent
  degradation. The job waits.
- **Clamp the concurrency for VRAM like HOM-41 does for RAM.** A VRAM snapshot on a shared
  GPU is a weak base for an automatic change. The worker is refused and the record says how
  many processes fit.
- **Import `ctranslate2` in the worker to read the devices.** A CUDA context before the fork
  breaks the children (ADR 0067 D5). A child process asks instead.
- **Fail a misrouted job.** A job in the wrong queue is a routing problem, not a job
  problem. The guard reroutes or requeues it.

## Follow-ups

- Measured qualification on the real P40: VRAM need, batch slots, concurrency (external).
- An alert on an ASR queue that has waiting messages and no consumer
  (`homayar_broker_queue_length` with no qualified worker).
- A console view of worker readiness and the ASR queues.
