# ADR 0027 — Workspace AI policies and run policy snapshots

Date: 2026-09-24 · Status: accepted · Package: WP08-T02

## Context

The blueprint requires each tenant policy to state the allowed processing
regions/providers/models, a per-stage tenant policy frozen on every run, and
routing in which legal/contractual eligibility and privacy precede capability,
health and cost. Until now provider selection was global configuration: every
workspace ran on the same adapters, and nothing recorded which policy applied.

## Decision

1. **A per-stage policy document** (`homayar.ai-policy/1`), one entry per stage
   (`asr`, `diarization`, `intelligence`):
   - `endpoint_kinds` — where the stage may run: `in_process` (inside the
     worker, no network), `private` (an endpoint the deployment or customer
     operates), `cloud` (a third-party subprocessor). Empty = the stage may
     not run.
   - `regions` — regions a networked endpoint may process data in. `cloud`
     is accepted only together with at least one region (explicit
     approval); for `private` an empty list means any declared region.
     `unknown` is never approvable.
   - `manifests` — optional allowlist of manifest ids.
   - `fallback` — whether a same-policy fallback may be used (WP08-T05);
     default off.
   - `intelligence.cloud_text` — `redacted` (default) or `original`: what a
     cloud analysis endpoint receives. Audio stages have no text form:
     permission to send redacted text never permits sending audio.
   The document holds no credential and no URL; lists are canonicalised, so
   the digest (SHA-256 of the canonical document with its schema id) does not
   depend on order.
2. **Default (version 0): private.** A workspace without a stored version runs
   under in-process and private endpoints only. Cloud processing needs an
   owner's explicit policy. A preset `local_only` (in-process only) is the
   air-gapped posture.
3. **Versioned, append-only, owner-controlled.** `PUT /admin/ai-policy`
   appends a version; it needs `ai_policy:manage` (owner; never an API key),
   CSRF, a reason and the version the caller read
   (`409 policy_version_conflict`). An identical document adds no version.
   Changes are audited (`ai_policy.updated`: versions, digest, changed stages,
   reason). Reading needs `workspace:manage`.
4. **A snapshot per attempt.** At claim the worker reads the workspace's
   current version together with the text-privacy policy (WP07-T06); after
   model preflight (ADR 0026) and before any stage it writes
   `run_policy_snapshots` (job, attempt, policy version, digest and document,
   text-privacy digest, strict mode, and per stage the adapter, manifest id,
   version, digest and status, endpoint kind, region, endpoint and whether the
   placement was declared by a manifest or inferred). Unregistered adapters
   (development/test only) are placed in-process when their identity has no
   endpoint and in an unknown cloud region otherwise, so no policy can approve
   them.
5. **Snapshots explain, they never authorize.** Every attempt — retry,
   replay, resumed work — reads the then-current version; an older, more
   permissive snapshot is never reused for a new attempt.
6. **Storage.** `ai_policy_versions` and `run_policy_snapshots` are workspace
   tables under forced row-level security. Both, and `model_manifests`, are
   append-only for the application role: ORM guards refuse updates and deletes,
   and PostgreSQL revokes `UPDATE`/`DELETE` from `homayar_app` (referential
   cascades run as the owner). Snapshots hold no conversation content and stay
   after a conversation purge, like stage runs.

## Consequences

- Deployments whose analysis provider is a cloud endpoint need an owner to
  allow `cloud` (with regions) for `intelligence`; until then WP08-T03 denies
  the stage visibly instead of sending data.
- Changing where data may go is an owner action, as changing the redaction
  policy is (ADR 0024); a narrower role is an owner decision.
- The policy has no per-tenant credentials (BYOK); provider credentials stay
  deployment configuration until a later package adds secret references.
