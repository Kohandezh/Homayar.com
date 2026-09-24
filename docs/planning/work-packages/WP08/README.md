# WP08 — Provider registry and privacy routing

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

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

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
