# ADR 0026 — Model manifests and preflight

Date: 2026-09-24 · Status: accepted · Package: WP08-T01

## Context

F20 requires every production run to record which model, prompt, schema and
licence policy produced it, an unknown or unqualified model to fail before it
runs, and a previously approved manifest to be selectable again without
rewriting historical results. Adapters already declare a `ProviderIdentity`
(provider, model, result-affecting parameters such as endpoint, prompt and
output-schema digests, compute type); stage checkpoints (WP06-T03) are keyed
by it. A second, competing identity system would drift from it.

## Decision

1. **A manifest is one version of a stage contract.** Schema
   `homayar.model-manifests/1`: `manifest_id`, `version`, `stage`
   (`asr`/`diarization`/`intelligence`), `status`
   (`qualified`/`development`/`retired`), `provider`, `model`,
   `model_revision`, `identity_params`, `languages`, `tasks`, `license`,
   `license_policy`, `endpoint_kind` (`in_process`/`private`/`cloud`),
   `endpoint`, `region`, `offline_capable`, `structured_output`, `notes`.
   Its digest is the SHA-256 of the canonical JSON document.
2. **Exact match on the declared identity.** An adapter resolves to a manifest
   only when provider, model and *every* identity parameter are equal. An
   edited prompt, another output schema or another endpoint is an unknown
   model. No credential is ever part of an identity or a manifest.
3. **Qualification is explicit and pinned.** A `qualified` manifest needs an
   approved licence policy and a pinned `model_revision`; moving aliases
   (`latest`, `main`, `head`, `stable`, `*-latest`, `*:latest`, `unpinned`, …)
   are refused for both `model_revision` and `model`. Development manifests
   may use them, but they are never evidence of reproducibility.
4. **Strict mode.** Default outside `development`/`test`; production refuses
   `MODEL_MANIFEST_STRICT=false`. In strict mode an unknown, development-only
   or retired model fails preflight (`model_not_qualified`,
   `manifest_pin_unknown`) before any stage runs; the failure is typed and not
   retried, and no other provider is selected to hide it. In development and
   test an unregistered adapter is recorded honestly as `unregistered`.
5. **Pins restore an earlier approved version.** `MODEL_MANIFEST_PINS`
   (`stage=manifest_id@version`) selects that version; it must still match the
   configured adapter exactly. Runs of other versions keep their records.
6. **Append-only catalog.** Every resolved manifest is stored once in
   `model_manifests` (keyed by digest, outside tenant RLS: it holds no tenant
   data) before any stage runs; a different document under an existing
   `manifest_id@version` is refused (`manifest_conflict`). Stage runs record
   `manifest_id`, `manifest_version`, `manifest_digest`; analysis runs record
   `manifest_digest`.
7. **Builtin manifests qualify nothing.** The shipped file lists the local
   Whisper variants as `development` (weights unpinned, Persian accuracy not
   benchmarked) and the "unavailable" diarization as `qualified` (it processes
   nothing). Qualification of a real model is an operator decision recorded in
   an operator manifest file (`MODEL_MANIFEST_PATHS`).
8. **Operator preflight.** `python -m backend.jobs.model_preflight
   [--register]` builds the configured bundle without loading a model or
   calling a provider, resolves every stage and prints JSON (exit 0 ready,
   1 not qualified, 2 configuration error), including a manifest skeleton
   built from the declared identity for stages without a usable manifest.

## Kept distinct

A manifest declares capability; it does not prove that the adapter is
configured, that tenant policy permits it (WP08-T02/T03), that the endpoint is
healthy (WP08-T05), that the plan entitles it, or that the live endpoint works.
Each of these is reported separately; none is inferred from another.

## Consequences

- Production needs an operator manifest file for every real model before the
  first job; with none, strict preflight fails every job visibly.
- Changing a prompt or schema without a new manifest version fails preflight
  in strict mode, by design.
- A manifest document change needs a new version; the catalog refuses edits.
