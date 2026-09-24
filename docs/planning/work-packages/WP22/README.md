# WP22 — Administration, i18n and documentation

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP03, WP07, WP08, WP11.

Documentation follows each package incrementally; complete admin acceptance depends on listed policy modules.

## Implementation instructions

Features F72, F86, F92. Owner: frontend/backend/product documentation. Dependencies: WP03/07/08/11; document earlier packages as they land.

1. Define a role-specific information architecture and complete Persian/English message catalog, including errors and empty/degraded states.
2. Standardize locale-aware dates/numbers and Persian/Arabic text normalization without changing raw evidence.
3. Test RTL/LTR layout, focus order, keyboard controls, screen-reader labels and narrow-screen review workflows.
4. Build validated admin forms for providers, scorecards, retention, quotas and integration settings with secret masking.
5. Version changes, preview their impact, enforce permission checks and support rollback/audit.
6. Write product, API, architecture, install, SaaS/private admin, integration, security, backup, upgrade, troubleshooting and model/QA configuration guides.
7. Have a new operator and supervisor complete installation/admin/review tasks from documentation alone; correct observed gaps.

Done when supported locales and configuration flows are complete and documentation matches the exact released profile.

## Feature-level acceptance

### F72 — Persian RTL/English LTR, dates, digits, accessibility (P1)

Current audited state: W partial. Target: Consistent bilingual usable workflows.

- [ ] All critical auth/upload/report/review/settings/error flows complete in Persian RTL and English LTR; keyboard-only flow succeeds, focus/labels are usable and 200% zoom does not hide required controls.

### F86 — Product/API/install/admin/security documentation (P1)

Current audited state: D/S drifting. Target: Versioned role and deployment guides.

- [ ] New operator installs/administers/restores supported profile using released docs, and new supervisor completes review/correction/coaching with no undocumented developer step; broken command/API references corrected.

### F92 — Tenant provider/scorecard/retention administration (P1)

Current audited state: W partial. Target: Audited versioned policy configuration.

- [ ] Authorized admins can preview/apply/version/rollback supported provider/scorecard/retention settings; invalid or privacy-violating configuration rejected; secrets masked and changes audited; other tenants unaffected.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
