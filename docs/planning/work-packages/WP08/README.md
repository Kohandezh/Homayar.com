# WP08 — Provider registry and privacy routing

Status: PARTIAL — T01–T05 validated in local scope and T06's application layer validated ([ADR 0026](../../../architecture/decisions/0026-model-manifests-and-preflight.md), [ADR 0027](../../../architecture/decisions/0027-ai-policies-and-run-snapshots.md), [ADR 0028](../../../architecture/decisions/0028-privacy-eligibility-routing.md), [ADR 0029](../../../architecture/decisions/0029-adapter-contract-usage-and-padyar-boundary.md), [ADR 0030](../../../architecture/decisions/0030-provider-resilience-and-same-policy-fallback.md), [ADR 0031](../../../architecture/decisions/0031-egress-denial.md)). Remaining: network-layer egress denial (deployment integration), the Padyar AI Gateway adapter (BLOCKED_EXTERNAL: API contract not supplied), qualification of real models (pinned weights and representative evaluation data, WP13) and provider credentials. No live provider was called. Not production-validated.

Completion dependencies: WP03, WP04, WP06, WP07.

## Implementation instructions

Features F20, F21, F22. Owner: ML/platform. Dependencies: WP03/04/06/07.

1. Add versioned model/prompt/capability manifests with language, task, license, endpoint, region and structured-output support.
2. Define per-tenant stage policies and immutable policy snapshots on each run.
3. Implement privacy/eligibility filtering before health, quality, latency and cost selection.
4. Qualify chosen local and API adapters through identical typed contract tests, including schema rejection and usage reporting.
5. Add deadlines, rate limits, backoff, Retry-After, health circuits and approved same-policy fallback.
6. Block unauthorized egress at network and application layers; test local failure with cloud keys configured.

Done when each stage is traceable to a qualified model/policy and no outage can move protected data to an unapproved provider.

## Feature-level acceptance

### F20 — Model/prompt registry and provider capabilities (P1)

Current audited state: W partial. Target: Versioned stage-specific provider policies.

- [ ] Every production run records stage model/prompt/schema/license-policy identifiers; an unknown/unqualified model fails preflight; a prior approved manifest can be restored without rewriting historical results.

### F21 — Tenant privacy/region routing and egress denial (P0)

Current audited state: W global config. Target: Privacy precedes cost or fallback.

- [ ] With cloud credentials present and the local provider unavailable, a local-only tenant emits zero unauthorized outbound requests and returns a truthful failure/partial state; forbidden region/provider choices are rejected.

### F22 — Provider retry, health and circuit breakers (P1)

Current audited state: W partial. Target: Bounded transient retry; typed failures.

- [ ] Inject timeout, 429, Retry-After, 5xx, invalid schema and permanent auth errors; transient retries stay within budget, permanent errors stop, circuits open/recover, and no fallback violates tenant policy.

## Recorded evidence (Session A, integrated 2026-09-25)

Implemented on product branch `session-a/wp08-backend` (`f72e091` T01 reconcile, `5defa71` T02,
`1e0f5b6` T03, `552ccea` T04, `da63f1f` T05, `1527eee` T06, `18a6282` handoff) and integrated into
`feat/commercial-v1-overlay` with Session B's console work (`fc803e3`) and a test-isolation fix
(`c5bf752`, pinned). Per-task files, commands and results, migrations (`d2b6f8a4c1e7`,
`e3a7c5f9b2d4`, `f5b1d7c3a9e2`, each round-tripped on PostgreSQL 16), the Session B API contract
note and the deployment integration items are in the product's `docs/handoff/session-a/README.md`
and `docs/handoff/session-a/API_CONTRACT.md`.

- F20: every stage run records its manifest id/version/digest and the policy snapshot; an unknown or
  unqualified model fails the preflight; manifests are append-only, so restoring a prior approved
  manifest does not rewrite history. Met in local tests; real-model qualification pending (WP13).
- F21: application layer met in local tests (per-call egress permits, endpoint policy, F21 scenario
  through the real provider factory with cloud keys present and the local endpoint down: zero cloud
  requests unless the owner allows cloud *and* fallback). Network-layer denial pending deployment
  integration (internal Compose network for `api`/workers, allowlisting egress proxy fed by the
  preflight's `egress` report, air-gapped profile without an external route).
- F22: timeout, 429 with Retry-After, 5xx, invalid schema and permanent authentication errors are
  injected in `test_provider_resilience.py`: retries stay within the job budget, permanent errors
  stop, circuits open and recover, fallback never leaves the tenant policy.
- Integrated verification (product `c5bf752`, CI job environment): full backend suite on
  PostgreSQL 16 and 17 691 passed / 46 skipped / 0 failed each; Docker images build and pass the runtime smoke test; console (Node 22) lint, build and 80 tests passed.
- Reviewer decision: self-review only (single-agent sessions by owner instruction).

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
