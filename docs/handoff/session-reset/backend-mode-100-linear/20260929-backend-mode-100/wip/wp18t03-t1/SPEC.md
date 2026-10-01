# SPEC — team wp18t03-t1 (mission 20260929-backend-mode-100)  [DRAFT by wp18t03-drafter]

Orchestration mode: `foreman_native_adapted`. Role separation, blind oracle, bare-diff review, two-round cap.
Trigger check: this unit adds a migration with a data step, changes the schema of a billing table (`subscriptions`) and
changes how workspace entitlements are provisioned -> a **final reviewer** (database-optimizer) runs after the team
reviewer's ACCEPTED and passing gates (Foreman expansion rule: migrations, billing/entitlements). No authorization
change (no route added); the RLS change is limited to classifying one new global table (see Lock request).

## Task
- Linear: HOM-101 [WP18-T03] Versioned plans with real entitlements; stop startup overwrite of tenant commercial
  configuration (plan row 5; feature F19; guardrail GR30; verified defect D-12). Dependency group 5, P1, stabilization S.
  Unblocks WP18-T04, WP19-T04, WP22-T04, N01.
- Type: Implementation (defect fix, plus the smallest versioning that makes it observable).
- **Root cause (this SPEC):** the plan-catalog seed treats the code constant `PLAN_CATALOG` as the owner of rows that,
  once they exist, are commercial data. It rewrites `name`, `is_public` and `entitlements_json` of every catalog plan
  on every API start AND on every personal-workspace creation (sign-up, OTP, Google, demo account). Nothing records
  what a tenant was granted, so a rewrite is invisible.
- **In scope:** (a) insert-only, race-safe seeding at startup and sign-up; (b) minimal versioning: an append-only
  `plan_versions` history, `plans.current_version`, and `subscriptions.plan_version` pinned when a subscription is
  created; one sanctioned write path `publish_plan_version`; (c) "real entitlements" for the only provisioning path
  that exists (the demo grant at sign-up): the grant comes from the pinned plan version, not from literals;
  (d) one migration that snapshots existing rows losslessly.
- **Deferred (not this SPEC; recorded for Foreman):**
  - D1 Expose the pinned version through the billing API (`plan_version` and the pinned terms in
    `GET /billing/subscription`): needs `routers/v1/billing.py` and `schemas/billing.py` (not locked). Follow-up.
  - D2 An operator write path (admin API or CLI) for plan versions, with an audit event and a platform-operator
    authorization entry: WP22-T04. There is no platform-operator role today (authorization-matrix.md:497).
  - D3 Effective-dated price books and invoice reproducibility (GR30 `effective_from`): WP18-T01 / WP18-T05.
  - D4 Plan change and period rollover re-provisioning entitlements from a new version: WP18-T04.
  - D5 Provisioning of non-demo (paid) plans at onboarding: N01.
  - D6 Seat metering (see Out of scope findings).
  - D7 Signed offline entitlements built on plan versions: WP19-T04.
  - D8 Only if the rls.py lock is denied: database-level append-only for `plan_versions`. It would then be a
    follow-up with its own migration that calls `apply_row_level_security`.

## Base
`BASE_SHA` = `7112407` (integration head after HOM-32). Lane: **L3**: its own PostgreSQL cluster on port 5435 and
Redis databases 6/7 (`testenv-l3.sh`). Alembic head at base: `a1c3e5f7b9d2` (single head; 31 revision files;
verified with `uv run alembic heads` from `/home/user/Homayar.com/Homayar`).

## Context (verified at 7112407; file:line under `Homayar/backend/` unless stated)
- **Defect D-12.** `services/tenancy_service.py:61-82` `ensure_plan_catalog`: for each `PLAN_CATALOG` item
  (`:27-58`), it inserts the item if the row is missing; otherwise (`:79-82`) it sets `plan.name`, `plan.is_public` and
  `plan.entitlements_json` from code. `demo` `audio_seconds` comes from `settings.demo_quota_seconds` (`:66-67`).
  `is_active` and `display_json` are written only on insert.
  - Callers: `main.py:94-96` (lifespan, `session_scope()`, before `ensure_development_demo_account`) and
    `tenancy_service.py:93` inside `create_personal_workspace`.
  - `create_personal_workspace` is reached from `services/auth_service.py:165` (register), `:459` (dev demo
    account), `services/otp_service.py:184` and `services/google_oauth_service.py:227`. So every sign-up also rewrites
    the catalog.
- **The catalog in code.** `demo` "Free Demo" is public, with `{"audio_seconds": 1800, "concurrent_jobs": 1,
  "seats": 1}`. `professional` and `business` are public with `{}`. `enterprise` and `on_premise` are not public, with
  `{}` (`tenancy_service.py:27-58`). The API advertises `pricing_mode="manual_quote"` and `price_published=False`
  (`schemas/billing.py:9-15`). Paid-plan entitlements are therefore operator/negotiated data, and today every restart
  resets them to `{}`.
- **Demo grant today.** `tenancy_service.py:123-157`: when `settings.enable_free_demo` is set, sign-up adds a `trialing`
  `Subscription(plan_key="demo", provider="internal")` and `Entitlement` rows. These are `audio_seconds`
  (limit = `settings.demo_quota_seconds`, `:139`) and `concurrent_jobs` (limit literal `1`, `:151`), both with
  `source="demo"`. No `seats` row is created.
  - `config.py:300-301` defines `enable_free_demo=True` and `demo_quota_seconds=1800`.
  - `docs/operations/deployment.md:179` documents `DEMO_QUOTA_SECONDS` as the "Demo allowance of new workspaces".
- **Models.** Three tables are involved:
  - `database/saas_models.py:732-740` `Plan` (`plans`): PK `key` String(40); `name` String(100); `is_public`;
    `is_active`; `entitlements_json` JSON; `display_json` JSON. `TimestampMixin` has `updated_at` with
    `onupdate=utcnow` (`:347-353`).
  - `:873-891` `Subscription`: `plan_key` FK -> `plans.key`.
  - `:894-913` `Entitlement`: per-workspace `metric` String(60), `limit_value` Integer (NULL = unlimited), `used`,
    `reserved`; unique (workspace, metric); checks `limit >= 0`.
  - No other table references `plans`.
- **Append-only precedent.** `ModelManifestRecord` (`:743-767`) and `AiPolicyVersion` (`:805-833`) are versioned
  append-only catalogs. They use ORM guards (`before_update` / `before_delete`, `:2329-2357`) plus DB-level
  `REVOKE UPDATE, DELETE` for the app role through `rls.APPEND_ONLY_TABLES` (`database/rls.py:291-295`, applied at
  `:384-388`). A PostgreSQL test pattern exists at `tests/test_ai_policy.py:320-331` ("permission denied").
- **Who reads plans.** `routers/v1/billing.py:33-39` `_plan_response` returns `plan.entitlements_json` from the *current
  row*.
  - `services/billing_service.py:71` resolves a subscription's plan with `db.get(Plan, subscription.plan_key)`.
  - `:78-85` lists `is_public AND is_active` plans.
  - Nothing else reads `entitlements_json`. The `seats` key has no consumer anywhere in `backend/` except the catalog.
- **Enforcement uses entitlement rows, not plans.** `services/entitlement_service.py:102-121` and `:155-167`: a
  missing `(workspace, metric)` row means `QuotaExceededError(limit=0)`, so the upload is refused. The metered metrics
  are `audio_seconds` (`services/meeting_service.py:120-126`) and `concurrent_jobs` (`:128-135`;
  `jobs/replay.py:128-132`).
- **RLS.**
  - `plans` is deliberately unprotected: `rls.UNPROTECTED_TABLES` (`rls.py:266-285`, `"plans"` at `:275`).
  - `subscriptions` is a workspace table (`rls.py:62`, policy `workspace_id = tenant`, `:121-124`). FORCE applies to a
    non-superuser owner too (`rls.py:9-13`).
  - `entitlements` has its own policy (`rls.py:162-165`).
  - Startup and sign-up SQL runs as the app role (`database/relational.py:23-75` `SET ROLE`; tests use `homayar_app`,
    `tests/conftest.py:113`).
  - PLAN.md §2 contract: a policy or `APPEND_ONLY_TABLES` change must ship a migration that calls
    `apply_row_level_security` (idempotent, `rls.py:371-388`, also re-grants on all tables `:304-320`).
- **Migration precedents.**
  - `a2f7c4e9d1b3_organization_memberships.py:22-26,158-215`: a data step on FORCE-RLS tables must suspend FORCE,
    otherwise a non-superuser owner silently updates 0 rows. It exposes the idempotent module-level
    `backfill_owner_memberships(connection)`.
  - Downgrade tests stamp head on a `create_all` schema, then downgrade through every later revision
    (`tests/test_retention_policy.py:242-280`, also `test_ingestion_ledger.py:948`, `test_media_lifecycle.py:619`).
    Constraint names must therefore match between model and migration.
  - `tests/test_postgres_guarantees.py:29-56` runs `upgrade head` + `alembic check` (no model/migration drift) on
    PostgreSQL.
- **Authorization.** Billing routes are GET only.
  - `docs/security/authorization-matrix.md:195-199`: `billing:read` for owner/admin, `billing:manage` owner.
  - `tests/test_billing_api.py:336-340` asserts the router is GET-only; `test_authorization_matrix.py:226`
    compares the route inventory with the document.
  - Platform operator actions are absent (deny) (`authorization-matrix.md:497`).
- **Existing tests that depend on this path.**
  - `tests/test_billing_api.py:124-142`: public catalog = `{demo, professional, business}`; a new workspace has
    `audio_seconds` remaining 1800 and `concurrent_jobs` 1.
  - `:355-357`: an operator-style `is_active=False` edit.
  - `tests/test_members_api.py:83` and `tests/test_locations_api.py:70` call `ensure_plan_catalog(db, settings)`
    through a superuser inspection session.
- **Product intent.**
  - `docs/planning/homayar-commercial-blueprint.md:440`: "Subscription changes create versioned entitlements; do not
    overwrite paid plans during startup seeding."
  - `:1050` (TD15): "startup rewrites defaults".
  - `docs/planning/work-packages/WP18/README.md:19`: "without ... invisible plan resets".
- **Logging.** `backend.services.logging_service.logger` is the `homayar` logger (`services/logging_service.py:44-47`).

## Contract (binding names and ownership; the blind oracle relies on these)
**Ownership after this unit:**

| Data | Owner | Startup | Sign-up |
|---|---|---|---|
| Existence of the five catalog keys | code (`PLAN_CATALOG`) | inserts a missing key (code defaults + v1) | may insert a missing key (same rule) |
| `plans.name`, `is_public`, `entitlements_json` of an existing row | operator (commercial) | never modified (sole exception: demo `audio_seconds`, next row) | never modified |
| `demo` `entitlements_json.audio_seconds` | deployment config `DEMO_QUOTA_SECONDS` | when it differs, a NEW version (source `config`) is appended; nothing else changes | never modified |
| `plans.is_active` (retirement), `display_json`, non-catalog plans | operator | never modified | never modified |
| `plan_versions` rows | append-only history | appends v1 for a newly inserted key; config version as above | never |
| `subscriptions`, `entitlements` rows (tenant-negotiated) | operator per tenant / the ledger | never touched | created only for the new workspace |

**Names:**
- `backend.database.saas_models.PlanVersion` (table `plan_versions`). Columns:
  - `plan_key` String(40), FK `plans.key`;
  - `version` Integer, where PK = (`plan_key`, `version`) and `version >= 1`;
  - `name` String(100);
  - `is_public` Boolean;
  - `entitlements_json` JSON;
  - `source` String(40), one of `catalog_seed | legacy_snapshot | operator | config`;
  - `created_at` timestamptz.
- `Plan.current_version`: Integer NOT NULL, `>= 1`.
- `Subscription.plan_version`: Integer NOT NULL, with the composite FK (`plan_key`, `plan_version`) ->
  `plan_versions` (`plan_key`, `version`). The FK is named `fk_subscription_plan_version` in the model AND the
  migration.
- `backend.database.saas_models.PlanVersionImmutableError`: raised by ORM `before_update` / `before_delete` on
  `PlanVersion`.
- `backend.services.tenancy_service`:
  - `ensure_plan_catalog(db, settings=None) -> None`: same signature, new semantics.
  - `publish_plan_version(db, plan_key, *, entitlements, name=None, is_public=None, source="operator") -> PlanVersion`.
    It flushes and does not commit. It appends version `current_version + 1` holding the given `entitlements` (the
    complete dict) and `name` / `is_public`; each of those two defaults to the row's current value when `None`. It then
    sets the row's `name`, `is_public`, `entitlements_json` and `current_version` to equal the new version, and leaves
    `is_active` and `display_json` unchanged.
  - `PlanCatalogError(ValueError)`.
- Head migration module: exposes `backfill_plan_versions(connection) -> int`. It inserts version 1
  (`legacy_snapshot`, fields copied from the row) for every `plans` row that has no version, and returns the number
  inserted.
- Log lines on the `homayar` logger:
  - WARNING `PLAN_CATALOG_DRIFT plan=<key> fields=<comma-separated field names>`, emitted by `ensure_plan_catalog`
    when a row's `name` / `is_public` / `entitlements_json` differ from its current version, or when that version row
    is missing. Field names only.
  - INFO `PLAN_VERSION_PUBLISHED plan=<key> version=<n> source=<source>`.
- **Metered metrics** provisioned from a plan version: `audio_seconds` and `concurrent_jobs` only.

## Acceptance criteria (observable)
1. **Startup never overwrites existing plans (D-12).** On a running app, an operator changes rows by direct SQL
   (DBA session):
   - `professional.entitlements_json = {"audio_seconds": 360000, "concurrent_jobs": 3}`;
   - `business.is_public = false`;
   - `enterprise.name = "Enterprise (negotiated)"` and `entitlements_json = {"audio_seconds": 900000, "seats": 50}`;
   - `on_premise.is_active = false`;
   - `professional.display_json = {"badge": "x"}`.

   Then the app restarts (a new lifespan, same settings). Result:
   - Every pre-existing plan row is identical in every column, including `updated_at` and `current_version`.
   - The `plan_versions` row count is unchanged.
   - Owner `GET /api/v1/billing/plans` returns `professional` with the edited entitlements and no longer lists
     `business`, both before and after the restart. At base, the restart resets them.
   - One WARNING `PLAN_CATALOG_DRIFT` is logged for each plan whose `name` / `is_public` / `entitlements_json` was
     edited (`professional`, `business`, `enterprise`), naming exactly the drifted fields. None is logged for
     `on_premise`: `is_active` and `display_json` are not versioned fields.

   A restart with no edits and default settings writes nothing: no `updated_at` changes, no version is appended and no
   drift warning is logged.
2. **Sign-up never modifies the catalog.** After AC1's edits and after publishing a `professional` version,
   registering and verifying a new user (`POST /api/v1/auth/register` + `/verify-email`) leaves every `plans` row and
   every `plan_versions` row (count and content) unchanged. The dev demo-account path does the same.
3. **Missing catalog keys are inserted, never others.**
   - On a database with no plan rows, startup creates exactly the five catalog keys with the code defaults: `demo`
     `audio_seconds` = `DEMO_QUOTA_SECONDS`, `concurrent_jobs` 1, `seats` 1; the names and `is_public` values of
     `tenancy_service.py:27-58`; `is_active` true; `display_json` `{}`; `current_version` 1.
   - Each key gets exactly one version: 1, `source="catalog_seed"`, fields equal to its row.
   - If one catalog key (with its versions, no subscriptions) is deleted by a DBA, the next startup re-creates only
     that key; all other rows stay unchanged.
4. **Versions are immutable and numbered.**
   - `publish_plan_version` appends N+1 with the given source. The row then equals N+1 (`name`, `is_public`,
     `entitlements_json`, `current_version`), with `is_active` / `display_json` unchanged. Version N is byte-identical
     afterwards. A PlanVersion is returned. The INFO line is logged.
   - Any ORM update or delete of a `PlanVersion` raises `PlanVersionImmutableError`.
   - On PostgreSQL as `homayar_app`, raw `UPDATE plan_versions ...` and `DELETE FROM plan_versions` fail with
     "permission denied". This criterion is conditional on the rls.py lock; SELECT and INSERT succeed.
   - `publish_plan_version` raises `PlanCatalogError` and writes nothing (row and versions unchanged) for:
     - an unknown `plan_key`;
     - `entitlements` that is not a dict;
     - a key not matching `^[a-z][a-z0-9_]{0,59}$`;
     - a value that is a bool, float or str, is negative, or is greater than 2147483647;
     - a `name` that is empty/blank or longer than 100 characters;
     - a `source` other than `operator` or `config`.

     Values that are ints within range, or `None` (unlimited), are accepted.
5. **The demo allowance is owned by `DEMO_QUOTA_SECONDS`, changed only by appending a version.**
   - (a) A restart with `DEMO_QUOTA_SECONDS` changed (e.g. 1800 -> 3600) appends exactly one `demo` version with
     `source="config"` and `audio_seconds` 3600. Every other `demo` field and entitlement key is carried from the
     row as it was before the restart. The row equals the new version. No other plan changes. A second restart with
     3600 is a no-op.
   - (b) `publish_plan_version("demo", ..., source="operator")` whose `audio_seconds` differs from the current value,
     or omits it, raises `PlanCatalogError`, and the message names `DEMO_QUOTA_SECONDS`. Nothing is written.
   - (c) An operator publish that changes `demo` `concurrent_jobs` to 2, with `audio_seconds` unchanged, succeeds
     and survives restarts.
6. **Real entitlements at sign-up, pinned to a version.** With the free demo enabled:
   - A new workspace's subscription has `plan_key="demo"` and `plan_version` equal to `demo.current_version` at
     sign-up.
   - Its `audio_seconds` and `concurrent_jobs` `Entitlement` rows have `limit_value` equal to that version's values,
     where JSON `null` becomes a NULL (unlimited) limit.
   - A metered metric absent from the version gets no row (fail closed). Non-metered keys (`seats`) get no row.
     `source` stays `"demo"`.
   - With default settings, the tenant-visible result is unchanged: `/billing/usage` shows `audio_seconds` remaining
     1800 and `concurrent_jobs` 1, and `test_billing_api.py` passes unmodified.
   - After AC5(c), the next sign-up gets `concurrent_jobs` limit 2 and pin N+1. The earlier workspace keeps pin N
     and limit 1.
   - Sign-up with the free demo disabled creates no subscription or entitlements (unchanged).
7. **Tenant-negotiated rows are never touched.** All columns of every existing `subscriptions` and `entitlements` row
   are identical after each of: (a) a restart, (b) a publish on their plan, (c) the AC5(a) config version. This
   includes one workspace's `audio_seconds` `limit_value` raised to 5000 by a DBA.
8. **Concurrency (PostgreSQL, app role, barrier-released threads, separate sessions).**
   - (a) Two concurrent `ensure_plan_catalog` + commit on a database with no plan rows both succeed, leaving exactly
     5 plans and 5 v1 rows.
   - (b) Two concurrent runs with a changed `DEMO_QUOTA_SECONDS` both succeed, and exactly one new `demo` version is
     appended.
   - (c) Two concurrent `publish_plan_version` calls on one plan both succeed with consecutive numbers N+1 and N+2;
     the row equals N+2.
9. **Migration upgrade preserves an existing deployment (PostgreSQL).** Start from the schema at `a1c3e5f7b9d2` holding:
   - catalog rows with operator edits (as in AC1);
   - a non-catalog plan `acme_custom` (`entitlements_json={"audio_seconds": 42}`);
   - a tenant subscription on `demo`;
   - an entitlement with `limit_value` 5000.

   After `upgrade head`:
   - Every `plans` row is identical in every pre-existing column, and `current_version` is 1.
   - Each plan (including `acme_custom`) has exactly one version: 1, `legacy_snapshot`, with `name` / `is_public` /
     `entitlements_json` equal to its row.
   - Every existing subscription has `plan_version` 1, and the `entitlements` rows are unchanged.
   - The first app start afterwards writes nothing (with `DEMO_QUOTA_SECONDS` equal to the legacy demo value).
   - Calling the head module's `backfill_plan_versions(connection)` again returns 0 and changes nothing.
   - The migration runs no DML on RLS-forced tables. `subscriptions.plan_version` is filled by the column DEFAULT
     inside DDL, and the default is dropped afterwards. The migration therefore behaves the same for a superuser and
     for a plain table owner.
10. **Downgrade and round trip (PostgreSQL).** Start from head with a published `professional` v2 and pinned
    subscriptions. Then `downgrade a1c3e5f7b9d2`:
    - It succeeds, and `plan_versions`, `plans.current_version`, `subscriptions.plan_version` and
      `fk_subscription_plan_version` are gone.
    - `plans` rows keep their current values, including v2's terms.
    - `subscriptions` / `entitlements` rows are intact.
    - Only the version history and the pins are dropped; this is stated in the migration docstring.

    `upgrade head` again yields the AC9 state.

    Also:
    - Exactly one new revision file exists, with `down_revision = "a1c3e5f7b9d2"`. `uv run alembic heads` shows one
      head.
    - `test_migrations_upgrade_head_matches_models` passes, so `alembic check` shows no drift.
    - The existing downgrade tests pass unchanged: `test_retention_policy`, `test_ingestion_ledger` and
      `test_media_lifecycle`.
11. **RLS and grants.**
    - `plan_versions` holds no tenant data and gets no row policy (the same class as `plans`). With the rls.py lock
      it is listed in `UNPROTECTED_TABLES` and `APPEND_ONLY_TABLES`.
    - The migration calls `apply_row_level_security` after its DDL.
    - As `homayar_app`, a session bound to workspace A sees A's subscription (with `plan_version`) and none of B's.
    - Sign-up still works as `homayar_app` on PostgreSQL. The subscription insert must pass the composite FK with
      `plan_versions` append-only; PostgreSQL runs FK checks as the referenced table's owner. This mechanism is
      unverified by a test at base; the sign-up tests prove it.
12. **Authorization matrix unchanged.**
    - No route is added, removed or changed. `tests/test_authorization_matrix.py` passes unmodified, and
      `docs/security/authorization-matrix.md` is unchanged.
    - The billing router stays GET-only.
    - `publish_plan_version` is reachable from no HTTP route.
13. **No regression.**
    - The full backend suite on PostgreSQL (lane 3) shows no new failure relative to `BASE_SHA` (0 failed / 1278
      passed / 47 skipped; the HOM-163 drill is intermittent). New tests are added.
    - ruff check and format are clean on changed files.
    - The SQLite fallback still imports and creates the schema: no PostgreSQL-only SQL without a dialect branch.

## Deny-cases and closest allow-controls
- DENY a restart reverting a DBA-edited `professional` / `enterprise` / `business` row. ALLOW-CONTROL: a restart
  re-creates a catalog key that is missing (AC3).
- DENY a sign-up rewriting any plan row or adding a version. ALLOW-CONTROL: the same sign-up creates the demo
  subscription pinned to the current demo version, with entitlement rows from it (AC6).
- DENY an ORM update/delete of a `PlanVersion`, and a raw UPDATE/DELETE as `homayar_app`. ALLOW-CONTROL:
  `publish_plan_version` as `homayar_app` appends N+1.
- DENY a publish with `{"audio_seconds": -1}`, `True`, `1.5`, `"10"`, `2147483648`, key `"Audio"`, an unknown plan, a
  blank name, or `source="catalog_seed"`. ALLOW-CONTROL: a publish with `{"audio_seconds": 0, "concurrent_jobs": None}`.
- DENY an operator publish changing `demo` `audio_seconds`. ALLOW-CONTROL: an operator publish changing `demo`
  `concurrent_jobs`; changing `DEMO_QUOTA_SECONDS` + a restart appends a `config` version.
- DENY a publish or config version altering an existing workspace's subscription pin or entitlement limits.
  ALLOW-CONTROL: the next new sign-up gets the new version's limits and pin.
- DENY `POST`, `PUT`, `PATCH` or `DELETE /api/v1/billing/plans` as the workspace owner: 405, no row changed.
  ALLOW-CONTROL: owner `GET /api/v1/billing/plans` returns 200 with the published values; member GET returns 403
  (existing).
- DENY a tenant-A session reading tenant B's subscription. ALLOW-CONTROL: tenant A reads its own subscription and pin.
- DENY the upgrade changing an operator-edited or non-catalog plan row. ALLOW-CONTROL: an unedited catalog row also
  gets v1 equal to itself.
- DENY a second `backfill_plan_versions` inserting rows. ALLOW-CONTROL: the first call returns the number of plans
  without a version.

## File ownership
Maker may change:
- `Homayar/backend/services/tenancy_service.py`: seed, `publish_plan_version`, `PlanCatalogError`, sign-up
  provisioning.
- `Homayar/backend/database/saas_models.py`: ONLY `Plan.current_version`, the new `PlanVersion` model and its ORM
  guard/error, and `Subscription.plan_version` with `fk_subscription_plan_version`.
- `Homayar/backend/migrations/versions/<new_rev>_versioned_plans.py`: exactly ONE new file, with
  `down_revision = "a1c3e5f7b9d2"`.
- `Homayar/backend/main.py`: only the startup catalog call, if needed at all.
- New `Homayar/backend/tests/test_plan_catalog.py`. `Homayar/backend/tests/test_billing_api.py` may be extended
  additively only (no existing assertion changed).

Must not change:
- `routers/v1/billing.py`, `schemas/billing.py`, `services/billing_service.py`, `services/entitlement_service.py`;
- `security/authorization.py`, `docs/security/authorization-matrix.md`;
- `config.py`, any existing migration, `rls.py` (unless the lock below is granted), CI, any frontend file;
- the test files locked by other lanes (`tests/test_voice_admission*`, `tests/test_transport_drills.py`,
  `tests/_transport.py`).

Checker: only `Homayar/backend/tests/test_oracle_wp18t03.py` in the checker worktree, and records under
`/root/foreman/20260929-backend-mode-100/wp18t03-t1/{oracle,reviews}/`.

**Lock request (Foreman decides):**
1. `Homayar/backend/database/rls.py`: two literal additions, `"plan_versions"` in `UNPROTECTED_TABLES` and in
   `APPEND_ONLY_TABLES`. Reason: classification of the new global table, and DB-level append-only as for
   `model_manifests`. The same migration calls `apply_row_level_security`, so deployed databases receive it (PLAN.md
   §2). rls.py is HOT but unlocked in `locks.tsv`. If denied, the "permission denied" clause of AC4 and the rls.py
   clause of AC11 are dropped, and D8 is recorded.
2. Optional, docs only: `docs/operations/deployment.md`, a short "Plan catalog" paragraph next to line 179. It
   covers: startup never overwrites plans; `DEMO_QUOTA_SECONDS` owns the demo allowance via versions; retire a plan
   with `is_active=false`; direct SQL edits persist but are reported as `PLAN_CATALOG_DRIFT` and are not versions.

**Implementation notes (non-binding; pitfalls found while drafting):**
- PostgreSQL `json` (not `jsonb`) has no `=` operator. Compare entitlements in Python.
- Insert-only seeding must be race-safe. Use a dialect-aware `ON CONFLICT DO NOTHING`, or a savepoint plus re-read.
- Take `SELECT ... FOR UPDATE` on the `plans` row before appending a version. Never lock or update `plan_versions`
  rows: the app role lacks UPDATE on them.
- Migration order:
  1. create `plan_versions`;
  2. add `plans.current_version` with server_default 1;
  3. run `backfill_plan_versions`;
  4. add `subscriptions.plan_version` with server_default 1;
  5. drop both server defaults;
  6. create `fk_subscription_plan_version`;
  7. call `apply_row_level_security`.

  Use `batch_alter_table` where SQLite needs it.
- Constraint names must be explicit and identical in the model and the migration. Downgrade tests run it on a
  `create_all` schema.

## Verification commands (from `<worktree>/Homayar`, after `source /root/foreman/20260929-backend-mode-100/testenv-l3.sh`)
EVERY PostgreSQL-touching command runs under the LANE lock, `flock "$LANE_LOCK" ...`. Lane 3 has its own cluster on
5435; never use heavy.lock's database or another lane's port.
- Head: `uv run alembic heads` must print exactly one `(head)`: the new revision.
  `uv run alembic history -r a1c3e5f7b9d2:head` must show one step.
- Focused: `flock "$LANE_LOCK" uv run pytest backend/tests/test_plan_catalog.py backend/tests/test_billing_api.py
  backend/tests/test_postgres_guarantees.py backend/tests/test_members_api.py backend/tests/test_locations_api.py
  backend/tests/test_authorization_matrix.py backend/tests/test_row_level_security.py backend/tests/test_ai_policy.py
  backend/tests/test_retention_policy.py backend/tests/test_ingestion_ledger.py backend/tests/test_media_lifecycle.py
  -q -p no:cacheprovider -rs`
- Lint: `uv run ruff check <changed files> && uv run ruff format --check <changed files>`
- Full: `LANE=3 bash /root/foreman/20260929-backend-mode-100/suite.sh <worktree> <outdir> <00|01|02|03>`, one chunk
  per foreground command. Baseline at 7112407: 0 failed / 1278 passed / 47 skipped (intermittent drill HOM-163
  excepted).

## Worktrees
- Maker: /root/foreman/20260929-backend-mode-100/wt/wp18t03-t1 (branch mission/wp18t03-t1 at 7112407)
- Checker: /root/foreman/20260929-backend-mode-100/wt/wp18t03-t1-check (detached at 7112407)

Each worktree has its own `Homayar/.venv` (`uv sync --frozen`).

## Allowed and forbidden actions
As in stab01-t1: no commit/push/branch/PR/Linear, no network, no contact with other agents, and no reading the other
role's worktree or files. Exception to "no migrations": exactly the one new revision file above. Never run
`alembic upgrade` / `downgrade` / `stamp` against any database except the lane-3 cluster.

Parallel mode: never touch files outside File ownership (see `locks.tsv`). `saas_models.py`, `migrations/versions/`
and `main.py` are HOT single-writer entries held by this team.

## Communication map, envelope, turn protocol, team directory
As in stab01-t1/SPEC.md, with team `wp18t03-t1`, names `wp18t03-t1-impl`, `wp18t03-t1-rev` and final reviewer
`wp18t03-t1-final` (database-optimizer). Directory: `/root/foreman/20260929-backend-mode-100/wp18t03-t1/`.

## Models
- Implementer: opus (xhigh: migration with data step, concurrency, billing data).
- Reviewer: opus (xhigh).
- Final reviewer: database-optimizer.

## Out of scope findings (not fixed here)
- F1: the `demo` catalog advertises `seats: 1` (`services/tenancy_service.py:32`, shown by
  `routers/v1/billing.py:38`), but nothing meters or enforces seats. There is no consumer of `seats` in `backend/`.
  The catalog advertises an unenforced limit (follow-up: seat metering or removal; D6).
- F2: with `ENABLE_FREE_DEMO=false`, the `demo` plan stays `is_public=True` (`tenancy_service.py:31`; `config.py:300`).
  `GET /billing/plans` then lists a free demo that sign-up no longer grants (`tenancy_service.py:124`). After this
  unit, an operator publish of `is_public=False` persists; a config-driven rule is a follow-up.
- F3: sign-up grants the demo even when an operator retired the `demo` plan (`is_active=False`).
  `tenancy_service.py:124` checks only `settings.enable_free_demo`. Two controls disagree; this is a follow-up with
  D5/N01.
- F4: `GET /billing/subscription` reports the plan's current row, not the terms the subscription was granted under
  (`services/billing_service.py:71`, `routers/v1/billing.py:89`). This becomes a visible gap once versions exist (D1).

## Foreman rulings (activation, 2026-09-30)
- BASE_SHA = `7112407` (alembic single head a1c3e5f7b9d2). Lane L3 (`testenv-l3.sh`, port 5435, `LANE=3 suite.sh`;
  focused runs under `flock "$LANE_LOCK"`).
- Q1 lock request GRANTED: `Homayar/backend/database/rls.py`, ONLY the two literal additions (`plan_versions` in
  UNPROTECTED_TABLES and APPEND_ONLY_TABLES); the migration calls apply_row_level_security.
- Q2 GRANTED: one short "Plan catalog" paragraph in `docs/operations/deployment.md`.
- Q3 ACCEPTED as drafted: DEMO_QUOTA_SECONDS stays the documented control; a change adds a new demo version
  (`config`) at startup instead of rewriting; operator publish changing demo audio_seconds is refused.
- Q4 CONFIRMED: legacy subscriptions are pinned to the version-1 legacy snapshot (never NULL).
- Out-of-scope findings F1-F4 are filed as Linear follow-ups; not fixed here.
- Final reviewer: database-optimizer (migration + billing/entitlements).
- Known lane gate exception until HOM-163 lands: test_transport_drills::test_visibility_timeout_redelivery_during_a_live_attempt_is_inert.
- Worktrees: maker /root/foreman/20260929-backend-mode-100/wt/wp18t03-t1 (branch mission/wp18t03-t1); checker
  /root/foreman/20260929-backend-mode-100/wt/wp18t03-t1-check (detached). Parallel mode: never touch files outside
  File ownership (locks.tsv) or another lane's database.

## Amendments (Foreman, after phase A, before phase B)
- A1 (AC4 validation precision): only `int` values within range and `None` are accepted as entitlement values; every
  other type (bool, float incl. `10.0`, str, dict, list) raises `PlanCatalogError`. An entitlement key that is not a
  `str` raises. `plan_key` is case-sensitive (`"Professional"` is unknown).
- A2 (parallel harness): temporary files go ONLY under /root/foreman/20260929-backend-mode-100/wp18t03-t1/scratch/,
  never the shared session scratchpad.
