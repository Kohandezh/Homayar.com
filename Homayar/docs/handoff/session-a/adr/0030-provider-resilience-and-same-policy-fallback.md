# ADR 0030 — Provider resilience and approved same-policy fallback

Date: 2026-09-25 · Status: accepted (draft for the website ADR set) · Package: WP08-T05

> Session A draft; the integration pass copies it to
> `docs/architecture/decisions/0030-provider-resilience-and-same-policy-fallback.md`.

## Context

F22: timeouts, 429 with Retry-After, 5xx, invalid schema and permanent
authentication errors must be handled so that transient retries stay within
budget, permanent errors stop, circuits open and recover, and no fallback
violates tenant policy. The job layer (WP06-T05) already had a retry budget,
exponential back-off, a job deadline and dead-lettering; provider calls had a
per-stage timeout only, and there was no circuit, request budget or fallback.

## Decision

1. **Deadlines.** Every provider call is bounded by the stage timeout *and*
   the job deadline, whichever is sooner. A call cut by the deadline (or
   one that would start after it) ends the attempt with
   `job_deadline_exceeded` (class `deadline`), and the job is dead-lettered
   (replayable) like every other deadline.
2. **Back-off.** Unchanged exponential back-off (base doubling up to the
   maximum) plus positive jitter (`JOB_RETRY_JITTER_RATIO`, default 0.1,
   drawn through `secrets`): retries spread out and never come sooner.
3. **Retry-After.** The OpenAI-compatible adapter parses `Retry-After`
   (delta-seconds or HTTP date, clamped to [0, 3600] s) on transient
   responses; the value travels on `ProviderError`/`ProcessingError`, and the
   next attempt is never scheduled earlier. Failure codes are unchanged
   (`intelligence_provider_error` for 429/5xx).
4. **Circuits (per networked endpoint, shared by all workers).**
   `provider_health` is keyed by the digest of the adapter identity (no
   credential). `PROVIDER_CIRCUIT_FAILURE_THRESHOLD` (default 5) consecutive
   transient failures (timeouts, 429, 5xx, connection errors) open the circuit
   for `PROVIDER_CIRCUIT_COOLDOWN_SECONDS` (default 60); a Retry-After opens
   it at least that long. While open, no call is made: the attempt ends with
   the retryable `provider_circuit_open` and waits for the remaining time.
   After the cool-down the next call is a trial: success closes the circuit,
   failure re-opens it. Permanent errors (authentication, invalid schema) do
   not count: they stop the job at once. The table holds platform state only
   (no tenant data) and sits outside row-level security, like
   `model_manifests`.
5. **Request windows.** `PROVIDER_RATE_LIMITS` (`manifest_id=N`, calls per
   minute) is enforced across workers before the call; beyond it the attempt
   ends with the retryable `provider_rate_limited`. The provider's own 429 and
   Retry-After remain authoritative.
6. **Approved same-policy fallback (analysis).** A configured fallback
   endpoint (`LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_MODEL`,
   `LLM_FALLBACK_API_KEY`) is only a *candidate*. It needs its own manifest
   (qualified in strict mode, reported by the preflight), and it is eligible
   only when (a) it passes the same policy filter as any adapter
   (ADR 0028) — a local-only or private policy never reaches a cloud
   fallback; (b) the stage policy approves fallback (`fallback: true`,
   default off); (c) it serves an equivalent contract (same structured-output
   mode; qualified if the primary is). Eligible candidates are ranked with
   open circuits last. A committed result of any eligible candidate is reused
   before anything is called; a transient failure (or an open circuit / spent
   window) moves to the next eligible candidate; a permanent failure stops.
   Each candidate receives the text its own placement permits and has its
   own checkpoint identity. The stage run records the manifest that produced
   the result and `reason_code=fallback_used` with the primary's failure; the
   snapshot lists every candidate with its verdict, reason and rank.
   Speech recognition and diarization have no second adapter to fall back to.

## Consequences

- F22's injected failures are covered by `test_provider_resilience.py`; the
  job budget (`JOB_MAX_ATTEMPTS`) still bounds calls: at most one call per
  eligible candidate per attempt.
- Circuit and window state is shared through PostgreSQL row locks; very high
  call rates may want a Redis-backed limiter later (not needed at current
  volumes).
- Cost-based ranking waits for prices (commercial input); quality ranking
  waits for evaluations (WP13).
