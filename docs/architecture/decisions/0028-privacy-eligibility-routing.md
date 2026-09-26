# ADR 0028 — Privacy and eligibility filtering at the execution boundary

Date: 2026-09-24 · Status: accepted · Package: WP08-T03

## Context

F21: with cloud credentials present and the local provider unavailable, a
local-only tenant must emit zero unauthorized outbound requests and get a
truthful failure or partial state; forbidden region/provider choices must be
rejected. The blueprint orders routing as eligibility and privacy, then
capability, then health/capacity, then cost/latency, and forbids hidden cloud
fallback. ADR 0027 gives each attempt a policy snapshot; this ADR decides how
the processor enforces it.

## Decision

1. **Filter before ranking.** `backend.providers.routing.select` evaluates
   every candidate of a stage against the workspace policy and capability,
   and only the eligible ones reach the ranking hook (health, quality,
   latency, cost — WP08-T05). A refused candidate is never called, never a
   fallback and never reused from a checkpoint in that attempt. Today each
   stage has one configured candidate; the contract already holds for many.
2. **Order and typed reasons.** `stage_disabled` (no endpoint kind allowed) →
   `provider_not_permitted` (endpoint kind) → `region_not_permitted` (cloud
   region not listed; private region not listed when the list is non-empty;
   an unregistered networked adapter is in region `unknown` and never
   approvable) → `model_not_permitted` (manifest allowlist) →
   `license_not_permitted` (licence policy `rejected`) →
   `language_not_supported` (an explicit job language the manifest does not
   declare; `*` = any, `fa` covers `fa-IR`; `auto` and unregistered adapters
   are not checked). Public messages never contain an endpoint.
3. **Decided before, enforced at the boundary.** Decisions are made once per
   attempt after model preflight (ADR 0026) and recorded in the attempt's
   snapshot (`verdict`, `reason`, `disclosure`: `none`, `audio`,
   `original_text`, `redacted_text`). The processor enforces them where each
   stage would call its adapter:
   - **ASR refused** → the attempt fails permanently with the reason as
     `failure_code` before the recording is even materialised; no transcript,
     the usage reservation is released (no settlement).
   - **Diarization refused** → stage `unavailable` with the reason; speakers
     stay unknown; nothing is sent.
   - **Analysis refused** → stage `unavailable` with the reason; no summary,
     no actions, no substitute provider; the transcript is kept and the
     meeting is `partial`, settled once like every partial outcome.
4. **Text transformation before disclosure.** The workspace redaction policy
   (ADR 0024) applies to every endpoint. In addition, a *cloud* analysis
   endpoint under `cloud_text: redacted` (the default) receives the
   transcript redacted with every supported class even when the workspace
   shows originals; the analysis run records `input_view=redacted` with the
   policy digest, the redacted version is persisted, and the checkpoint
   identity includes the digest, so a result computed from original text is
   never reused for a redacted run. In-process and private endpoints are
   unchanged. A redaction failure fails the attempt; there is no raw fallback.
5. **Text permission never covers audio.** Audio stages have no text form;
   allowing redacted text to a cloud analysis endpoint leaves cloud speech
   recognition and diarization refused unless their own stage policy allows
   them.
6. **A stricter policy stops the attempt.** Before each stage the worker
   re-reads the workspace's AI policy digest and redaction classes; if
   either changed since the claim, the attempt ends with the retryable
   `policy_changed` before the stage starts. The next attempt snapshots the
   current policy and reuses committed checkpoints (identity permitting).
7. **Retries and replays** always claim under the current policy (ADR 0027);
   a historical permissive snapshot never authorizes new disclosure.

## Not covered here

- Network-layer egress denial and the application HTTP guard (WP08-T06).
- Same-policy fallback, health circuits, deadlines and rate limits (WP08-T05).
- Per-tenant credentials (BYOK): none exist; provider credentials remain
  deployment configuration, so there is nothing a tenant could carry across a
  boundary. A future BYOK design must store secret references per workspace
  under row-level security.
- Embeddings/search/Copilot providers: the served v1 API has none; the legacy
  `llm_router`/Copilot code is not mounted (not reachable from the v1 app or
  the worker) and is outside this contract.
