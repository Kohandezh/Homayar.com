# ADR 0029 — Adapter contract tests, usage reporting and the Padyar AI Gateway boundary

Date: 2026-09-24 · Status: accepted · Package: WP08-T04

## Context

F20/F22 need local and API adapters qualified by the same typed contract,
including schema rejection and usage reporting. The blueprint warns that
"OpenAI-compatible" does not imply identical structured output, auth or token
behaviour, and that an aggregator is an identified subprocessor, never a
mandatory dependency. Padyar AI Gateway is the named external gateway; its API
contract has not been supplied.

## Decision

1. **One contract per port, run identically against every adapter**
   (`backend/tests/test_provider_contracts.py`). Every adapter:
   - returns the port's typed result (`ASRResult`, `DiarizationResult`,
     `IntelligenceResult`) that round-trips through the shared schema, or
     raises a typed `ProviderError`;
   - names itself in results exactly as its declared `ProviderIdentity` does,
     so a manifest matched on the identity describes the result;
   - declares a deterministic, credential-free identity;
   - reports usage as a typed value or not at all.
   Adapters covered: local Whisper (injected model), deterministic local
   doubles, the OpenAI-compatible adapter (in-memory transport); the Padyar
   slot is an explicit skip naming the missing contract.
2. **Schema rejection is typed and final.** Malformed output (non-JSON,
   empty, arrays, missing or extra keys at any depth, wrong types, negative
   indexes, unknown priorities, invalid dates, confidence outside [0, 1]) and
   malformed envelopes become `intelligence_invalid_response`,
   `retryable=false`; an impossible ASR segment becomes `asr_failed`,
   `retryable=false`. Nothing malformed becomes a result.
3. **Typed failures never echo secrets or endpoints.** HTTP 400/401/403/404/422
   are permanent; 429 and 5xx are transient (bounded retry: WP08-T05). Public
   messages contain neither the key nor the host. The key travels only in the
   `Authorization` header; strict JSON-schema output is requested at
   temperature 0.
4. **Usage is reported, never invented.** `IntelligenceResult.usage`
   (`ProviderUsageReport`: `input_tokens`, `output_tokens`) carries what the
   provider reported (OpenAI field names `prompt_tokens`/`completion_tokens`);
   missing or malformed usage is `None` and does not fail the analysis. The
   checkpoint publication writes it to `provider_usage`, whose token columns
   are now nullable: null = not reported. Rows written before revision
   `e3a7c5f9b2d4` carry 0 for "not reported" (left unchanged: a data rewrite
   under forced row-level security could silently skip rows). The
   deterministic local adapter reports nothing.
5. **Padyar AI Gateway: explicit boundary, no invented integration.**
   `INTELLIGENCE_PROVIDER`/`ASR_PROVIDER`/`DIARIZATION_PROVIDER` =
   `padyar`/`padyar_gateway`/`padyar_ai_gateway` is a typed unconfigured
   state (`provider_contract_unavailable`, permanent): the operator preflight
   exits 2 with that code and every job fails visibly before any stage.
   Product modules depend only on the ports; the adapter will live behind them.
   A generic OpenAI-compatible adapter pointed at a gateway URL is not a
   Padyar integration. Required inputs are listed in
   `backend/providers/padyar.py` (`MISSING_INPUTS`).

## Consequences

- Qualifying a new adapter means making it pass the same contract module
  first; a real model's qualification (manifest `qualified`) additionally
  needs a pinned revision and a benchmark on representative data (WP13).
- Cost estimation stays empty (`estimated_cost_micros` null) until prices are
  an owner/commercial input.
