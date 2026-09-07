# ADR 0001: Preserve runtime roots during workspace organization

- Date: 2026-09-07
- Status: Accepted for the current workspace organization phase
- Scope: File organization and implementation handoff; no application migration or Git-history rewrite

## Context

The commercial website is a Next.js application at the outer repository root. The API and React/Vite console are in a separate embedded Git repository at `Homayar/`. Both repositories contain substantial uncommitted work. Root deployment and CI files already reference the current filesystem arrangement, but the outer repository does not currently track the product source or establish a configured submodule contract.

Organizing all applications immediately under new `apps/` directories would touch build contexts, Docker allowlists, CI working directories, package configuration, Python imports, migrations, static exports, and documentation. It would mix structural migration risk with unfinished product work and security remediation.

## Decision

Preserve `src/`, `content/`, `public/`, `Homayar/backend/`, `Homayar/frontend/`, and `infra/` as runtime and deployment roots during this phase. Preserve both `.git` histories and every existing dirty change.

Organize planning, work packages, architectural decisions, operations, security evidence, and coding-agent handoff under `docs/`. Reserve `tooling/` for workspace utilities and `artifacts/{releases,graphify,setup}/` for deliberately managed generated outputs. Check references before moving any selected loose archive or asset. Do not move build inputs or change public asset URLs as incidental cleanup.

Do not treat this decision as a monorepo conversion. Resolve how a clean release checkout obtains product source as a separate, explicit work package. Short-term options include a separately checked-out product repository pinned to a reviewed revision or a correctly configured pinned submodule. A history-preserving monorepo import remains a future option after both working trees have been reconciled and reviewed.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Move applications to `apps/` immediately | Deferred: broad path coupling and two dirty repositories make a cosmetic move disproportionately risky |
| Keep everything as loose files | Rejected: planning and handoff become harder to navigate and ownership remains unclear |
| Delete nested `.git` and stage everything | Rejected: would discard the nested repository's local Git identity and risk mixing unreviewed source, databases, and generated artifacts |
| Documentation/support-file organization with runtime paths preserved | Selected: improves navigation while keeping the current build and deployment contracts inspectable |

## Consequences

The folder names remain temporarily asymmetric. This is acceptable while security, trustworthy analysis, tenancy, and reproducible releases take precedence. Any later runtime move must update every affected consumer and prove clean-checkout builds, migrations, tests, and deployed route behavior before it is accepted.

The unresolved outer/inner source-delivery contract remains a release blocker. Neither this ADR nor the creation of documentation directories fixes it. The path inventory and migration order are recorded in [Workspace structure](../workspace-structure.md).
