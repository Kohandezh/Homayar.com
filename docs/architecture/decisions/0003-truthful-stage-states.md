# ADR 0003 — Truthful per-stage processing states

Date: 2026-09-08 · Status: accepted · Package: WP04-T01

## Context

The processor marked a meeting `completed` whenever the job finished without an
exception, even when diarization was unavailable (speakers unknown) or the
transcript was empty. The mock diarization provider is honestly unavailable, so
every "completed" fixture in the test suite was in fact a degraded result. The
commercial requirement (F23) is that a missing or failed stage is visible in
API, UI and exports and can never look fully successful.

## Decision

- Every processing attempt persists one `processing_stage_runs` row per stage
  (`materialize`, `asr`, `diarization`, `intelligence`) with a typed
  `StageState` (`queued`, `running`, `completed`, `partial`, `failed`,
  `unavailable`, `insufficient_evidence`, `skipped`), a typed reason code
  (`backend/jobs/states.py`), provider/model provenance, `is_mock` and latency.
- `MeetingStatus.PARTIAL` is a terminal status: processing finished but at least
  one stage did not complete. `completed` requires every stage to complete.
- A provider that was not called (unavailable diarization, skipped analysis)
  produces no `provider_usage` row.
- An empty transcript is `insufficient_evidence`, not a job failure: the audio
  was processed, so the audio-seconds reservation is committed. This is a
  product-policy assumption the owner may revise (release the reservation
  instead); it is isolated in `backend/jobs/processor.py`.
- Failures record the running stage as `failed` with the failure code and
  unreached stages as `skipped`/`not_reached`; job-level retry semantics are
  unchanged.
- The API exposes runs as `JobResponse.stages`; the console shows a stage list
  with fa/en labels and a mock marker and treats `partial` as finished.

## Consequences

Tests that want the fully successful path must use a diarization double that is
actually available; the shipped mock stays unavailable. Exports and share
pages still render partial meetings; surfacing the degradation there is
WP04-T06. Percentage progress is still never invented: the UI shows the
current stage name only.
