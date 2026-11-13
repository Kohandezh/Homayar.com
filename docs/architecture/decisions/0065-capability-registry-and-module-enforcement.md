# ADR 0065: Capability registry and module enforcement

Date: 2026-11-13 · Status: Proposed (HOM-229, X01/X02) · Builds on: none (HOM-109 and HOM-233 build on this)

Code: `backend/services/capability_registry.py` (pure data and pure functions),
`backend/security/modules.py` (the guard and the effective-state evaluation),
`backend/routers/v1/capabilities.py` with `backend/schemas/capabilities.py` (the
endpoints). No database migration, no new table: the admin switch lives in the
existing `workspaces.settings_json`, the license in the existing
`plan_versions.entitlements_json`.

## Context

The product has modules (Quality, Insights, Copilot, Voice, Voicemail, Integrations)
but nothing can turn one off. AppVision 1.1: an admin can enable and disable
licensed capabilities, backend enforcement is mandatory, and disabling is not
deletion. HOM-109 (signed offline entitlements) and HOM-233 (the console modules
page) build on this registry.

## Decision

### D1. A versioned registry in code

`CAPABILITY_REGISTRY_VERSION = 1` and six frozen dataclass modules in a fixed
order: `quality`, `insights`, `copilot`, `voice`, `voicemail`, `integrations`.
Keys are lowercase ASCII and stable; they are part of the API and of plan
entitlements and are never renamed. `lookup_module` is exact: `"Voice"`,
`" voice"` and `"voice "` are unknown. `copilot` is registered today with no API
entry points (no copilot router exists yet), so the license and the admin switch
can be provisioned before its first route.

Every other router is **core** and never guarded: auth, admin, ai_policy,
api_keys, billing, ingestions, `insights.py` (meeting transcript, summary,
actions, search, export), reports, meetings, members, organizations, privacy,
notifications, shares, teams, locations, and the new capabilities router itself.
Note the naming trap: `routers/v1/insights.py` is core; the `insights` module is
`trends.py`.

### D2. Effective state: three inputs, first refusal wins

For one workspace and one module, `enabled = install_enabled and licensed and
admin_enabled`, with `reason` from the first refusing input. State is read fresh
on every request; there is no cache.

1. **Install switch.** `DISABLED_MODULES`: a comma-separated list of module keys,
   trimmed, empty items ignored, default empty. An unknown key fails settings
   validation at startup, so a typo cannot silently leave a module on. Reason
   `disabled_for_install`, for every workspace of the install.
2. **License.** The workspace's current plan version
    (`get_current_billing`), whose `entitlements_json` may hold a `"modules"` key.
    Key absent: every module is licensed, so every existing tenant keeps
    everything. Present as a JSON list of strings: only the named registry keys
    are licensed (unknown strings are ignored, an empty list licenses nothing).
    Present in any other shape (`null`, a string, an object, a list holding a
    number): **fail closed**, nothing is licensed. Entitlements that are not a
    JSON object at all (`null`, a list, a string) license nothing too, like
    every other malformed license value. `BillingConfigurationError`:
    fail closed. No current subscription or no plan version: every module is
    licensed. Reason `not_licensed`.
3. **Workspace admin switch.** `workspaces.settings_json["modules"]`: an object
   mapping a module key to `{"enabled": <bool>, ...}`. Readers use `enabled`
   only; writers may add audit-useful fields (`changed_at`, `changed_by`). No
   key, or no entry: enabled. An entry whose `enabled` is missing or not a JSON
    bool (`"false"`, `0`): **fail closed**, refused. A `"modules"` value that is
    not an object: **fail closed**, every module of the workspace is refused.
    A `settings_json` that is not an object at all (a list, a string) refuses
    every module the same way; no settings at all is the default, everything
    enabled. Reason `disabled_by_admin`.

Writes lock the workspace row (`with_for_update`, like `_locked_workspace` in
`admin_service.py`), deep-copy `settings_json` and `flag_modified` (like
`_write_entry` there), so every other settings key survives unchanged. The
write refuses unreadable stored settings instead of repairing them: a
`settings_json` that is not an object, or a present `"modules"` key that is
not an object, is HTTP 409 `module_settings_unreadable`; nothing changes and
no audit event is written.

### D3. One guard per module router, one error

`require_module(key)` in `backend/security/modules.py` resolves the request's
workspace through its `workspace` dependency (by default the existing
`require_workspace`, so FastAPI reuses the already-resolved workspace), raises
at import time for an unknown key, and carries `homayar_module = <key>`
metadata like `homayar_permission`. It is applied **once per router**
(`APIRouter(..., dependencies=[Depends(require_module("<key>"))])`), so a new
route on a module router is guarded by default. Guarded routers: `scorecards`,
`review_items`, `calibration_batches`, `review_appeals`, `coaching_tasks` (`quality`);
`trends` (`insights`); `voice` (`voice`); `voicemail` (`voicemail`); `integrations`
and `webhooks` (`integrations`).

Refusal order: authentication (401), membership (403 `workspace_access_denied`),
module, then the route's own permission. A module refusal never depends on the
caller's role. The guard runs before the route body, so a refused write changes
nothing: no row, no audit event, no job, no idempotency record. Disabling only
refuses calls; it never deletes, hides, purges or rewrites data, and enabling
again returns the same data.

A refused call is HTTP 403 with `error.code` `module_disabled`, a short English
message naming the module, and `error.details.{module, reason}` (`reason` is
`disabled_for_install`, `not_licensed` or `disabled_by_admin`); the app's error
handler adds `request_id`. The `detail` therefore nests `module` and `reason`
under `details`, the convention of `trends.py::_refused`.

**Voice exception.** The four platform-operator routes (`POST /voice/nodes`,
`POST /voice/nodes/{node_id}/heartbeat`, `POST /voice/nodes/{node_id}/drain`,
`POST /voice/recover`) are install infrastructure, not a tenant feature: they
moved to a second, unguarded `APIRouter` in the same file (same prefix and
tags), included by `main.py` next to the guarded one. Paths, methods, response
models and policies are unchanged.

**Trends order.** `trends.py` is split into two routers with the same prefix
and tags (like the voice exception above). `/trends/issues` accepts API keys
and keeps the normal guard. The three session-only role views (`/trends/teams`,
`/trends/me`, `/trends/conversations`) are guarded through
`require_module("insights", workspace=require_session_workspace)`: the guard
takes its workspace from the routes' own session-only workspace dependency
(built on `require_actor`), and that one resolution is shared, through
FastAPI's dependency cache, with the routes' `require_session_permission`. The
HOM-82 contract therefore holds **in every module state**: every bearer that
looks like an API key (valid, unknown, revoked, expired, over its rate limit)
gets `401 api_key_not_accepted` before any key lookup, no `api_key_invalid`,
no `workspace_access_denied`, no rate-limiter hit. For a session caller the
order stays: workspace resolution (membership), the module check, then the
route's own permission check.

### D4. The endpoints

- `GET /api/v1/capabilities` (the console reads this): `require_workspace`,
  any member (session or API key), never refused by a module guard. Returns the
  registry version, the workspace id and all six modules in registry order,
  each with `key`, `name`, `description`, `enabled`, `reason`,
  `install_enabled`, `licensed`, `admin_enabled` (`admin_enabled` false for an
  unreadable entry, D2).
- `PUT /api/v1/admin/capabilities/{module_key}` with `{"enabled": <bool>}`
  (the admin switch): `require_write_permission(WORKSPACE_MANAGE)` exactly like
  the channel-role writes (session-only; an API key gets 403
  `api_key_scope_denied`, member and viewer 403 `permission_denied`). The body
  is strict: `enabled` must be a JSON bool (`"true"`, `1`, `null` and extra
  fields are 422). An unknown key, including different case, is 404
  `module_not_found`. The write is allowed whatever the license or install
  state: the admin choice is kept and the effective state still shows the other
  reason. Every accepted write, also one repeating the stored value, is audited
  as `workspace.module.enabled` or `workspace.module.disabled` (target
  `workspace`). The response is the module's single-object state after the
  change. Only the caller's own workspace changes.

## Known limits

- **No subscription means every module is licensed.** License validity, expiry
  and grace belong to HOM-109 (signed offline entitlements), which must close
  this gap by refusing a workspace without a valid entitlement instead of
  licensing everything.
- **Worker enforcement is a follow-up.** Jobs of a disabled module (a queued
  transcription, an in-flight webhook delivery) keep running; the guard refuses
  only API calls. The coaching reminder jobs (HOM-77) keep running while
  `quality` is off, the same worker gap. Module dependencies, drain and the
  S/P/H/O deployment profiles are likewise out of scope.
- The effective state is computed per request and reads the subscription each
  time; `GET /capabilities` evaluates the license once for all six modules, the
  guard once per guarded route. No cache, by decision.
- **Core routes still serve module data while a module is off.** While a module
  is disabled, its data stays reachable through core routes:
  `GET /api/v1/organizations/{id}/trends/issues`,
  `GET /api/v1/organizations/{id}/workspaces/{workspace_id}/trends/issues`,
  `POST /api/v1/admin/review-samples`, and voicemail meetings through the core
  meeting and insights routes. A follow-up ticket decides them.
