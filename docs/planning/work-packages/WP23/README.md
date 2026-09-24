# WP23 — Enterprise federation and independent security qualification

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP02, WP03, WP19.

## Implementation instructions

Features F07, F82. Owner: identity/security/platform. Dependencies: WP02/03/19; external testing follows a stable candidate.

1. Qualify customer OIDC federation and SAML via supported broker; document callback, claim mapping and account linking.
2. Map groups to explicit application memberships/roles and define deprovisioning without pretending OIDC provides SCIM.
3. Enforce privileged MFA and test disabled users, revoked sessions, group removal and controlled break-glass access.
4. Run dependency/container/SBOM/secret scans and triage findings by reachable exploitability and supported versions.
5. Commission a scoped independent penetration test of API, browser, tenancy, upload, shares, jobs and private topology.
6. Remediate critical/high findings and retest; publish only the actual report scope/date and approved customer summary.

Done when federation lifecycle is proven and no unresolved critical/high security finding remains in the supported release scope.

## Feature-level acceptance

### F07 — Enterprise OIDC/SAML federation and MFA (P1)

Current audited state: D LDAP partial. Target: SSO through qualified identity broker.

- [ ] OIDC and the supported SAML-broker profile complete login/logout/linking; privileged MFA enforced; disabled/group-removed users lose access within the documented maximum, proposed ≤15 minutes, with immediate admin revocation available.

### F82 — Security scans and independent penetration test (P1)

Current audited state: S scan template. Target: Remediation evidence before enterprise release.

- [ ] Independent scoped test completed on release candidate; zero unresolved critical/high findings, retest evidence for fixes, and no assurance statement exceeds actual test scope.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
