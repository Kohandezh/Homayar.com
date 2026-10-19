# Audit trail

Scope: HOM-114 (WP20-T02) and HOM-115 (WP20-T03). Decision record:
[ADR 0036](../architecture/decisions/0036-operational-evidence-observability-audit-release.md).
Code: `Homayar/backend/services/audit_service.py`.

## Event model

One `audit_events` row per security- or business-sensitive operation:

| Field | Meaning |
| --- | --- |
| `actor_user_id` | the user who acted; `NULL` for the system (a worker or sweep), which `request_id` then names |
| `workspace_id` | the tenant; `NULL` for platform events (login, logout, model registration) |
| `event_type` | the action, from `AUDIT_EVENT_CATALOG` (a test fails when code records an uncatalogued type) |
| `target_type`, `target_id` | what was acted on (ids only) |
| `occurred_at` | when (UTC) |
| `outcome` | `success`, `failure`, `denied`, `rejected`, … |
| `request_id` | correlation id: the request's `X-Request-ID`, `job:<id>` for a processing attempt, `sweep:<task>:<run>` for a periodic sweep |
| `ip_hash` | peppered hash of the client IP (never the address) |
| `details` | ids, counts and enum values |

Catalog categories (`AUDIT_EVENT_CATALOG`):

| Category | Examples |
| --- | --- |
| `security` | `auth.login` (success), `auth.login_failed` (refused), `auth.logout`, `auth.refresh_replay_detected`, password reset, OTP, Google sign-in, API key created/revoked |
| `access` | member invitations, role changes, removals, teams and assignments, organization grants and scopes, share links |
| `privacy_access` | `transcript.original_accessed`, `pii.revealed`, `subject.exported`, `meeting.exported`, `meeting.share_accessed` |
| `configuration` | `workspace.settings.updated`, `ai_policy.updated`, `model_manifest.registered`, service locations, voice nodes |
| `administration` | `meeting.processing.replayed`, `meeting.purge_requeued` |
| `deletion` | meeting deletion, retention expiry, purges, tombstone reapply, media deletion, legal hold |
| `processing` | uploads, ingestion decisions, processing outcomes, redactions, revisions, action items, voicemail |

Approvals and execution of workflow actions are not a product feature yet; when they
arrive they need catalogue entries (the coverage test enforces it).

## What never goes into an audit row

* Secrets: keys containing `password`, `passphrase`, `token`, `otp`, `code`, `secret`,
  `private_key`, `authorization`, `cookie` or `transcript` are stored as `[redacted]`, and so
  is any value shaped like a credential (bearer/basic credentials, JWTs, Homayar or provider
  API keys).
* Raw identifiers of a refused login: only a 16-character prefix of the peppered digest of
  the identifier (`target_type = login_identifier`).
* Client IP addresses: only `ip_hash`.
* Personal data in free text (owner decision 2026-10-17, option B): **every string value**
  passes through the text PII detector (`backend/privacy/text_pii.py`: phone, e-mail, Iranian
  national ID, bank card, IBAN, postal code), is stored with class placeholders (`[PHONE_1]`,
  `[EMAIL_1]`, …), and is capped at 120 characters. Masking runs before the cap, so a cut never
  leaves half an identifier. Only keys that hold ids, enums, digests, counts, timestamps or
  field-name lists are exempt (see "Review update" below); they are still secret-checked and
  capped at 500. A key naming personal data (`national`, `phone`, `card`, `email`, …) is never
  exempt.
  The match is on substrings, so keys such as `ip_address` or `scorecard_*` are masked too:
  this fails closed (masked, never leaked) and is intended.
  An id embedded in a free-text reason can be partly masked when it contains a digit run the
  detector reads as personal data (about 0.16% of random UUIDs; fails closed); a structured
  `*_id` key is never masked.

## Delivery and failure behaviour

* **Transactional** (all events of operations that happened): the row commits with the
  operation. If the insert fails, the transaction rolls back, the operation does not happen
  and the caller gets an error; `homayar_audit_write_failures_total{delivery="transactional"}`
  counts it once (`HomayarAuditWriteFailures` alerts, added with the alert rules).
* **Best effort** (refused attempts and security events whose action must stand: failed
  login, refresh-token replay after the revocation commits): recorded in its own commit after
  the rollback. If it fails, the response is unchanged, `AUDIT_WRITE_FAILED` is logged and
  `homayar_audit_write_failures_total{delivery="best_effort"}` counts it.
* Privacy-sensitive reads (`transcript.original_accessed`, `pii.revealed`, exports) commit
  their audit row before the response is returned: no audit, no data.

## Tenant visibility

Workspace owners and admins read their own workspace's events through
`GET /api/v1/admin/audit-events` (permission `audit:read`); the query is filtered by the
bound workspace and row-level security bounds it again. Platform events (`workspace_id`
NULL) are never returned to a tenant.

## Review update (PR #55): masking by default, nesting, delivery counts

- **Every string detail value is masked by default** (secret check, then the text PII
  detector, then the 120-character cap). Only keys that hold ids, enums, digests, counts or
  timestamps are exempt (`EXEMPT_DETAIL_KEYS`, plus the suffixes `_id`, `_ids`, `_digest`,
  `_sha256`, `_hash`, `_at`, `_count`, `_bytes`, `_seconds`, `_version`, `_number`, `_prefix`,
  `_class`, `_type`, `_state`, `_kind`, `_status`, `_role`, `_fields`, plus the key `fields`). Exempt values are still
  checked for secrets. A key the owner named as free text (`reason`, `*_reason`, `name`,
  `external_event_id`, `source_provider`) is never exempt.
- **Nested dicts and lists** are sanitised recursively with the same key and secret
  checks: depth 3, 50 items per level, deeper values `[redacted]`.
- **Refresh-token replay**: the session revocation commits first, then the event is written
  best effort, so a failing audit write can never leave a stolen session valid.
- **Failure counting**: a failed audit `INSERT` is counted once, by the engine hook, with the
  delivery of the path that wrote it (`best_effort` or `transactional`).
- An event type missing from `AUDIT_EVENT_CATALOG` is logged once as
  `AUDIT_EVENT_UNCATALOGUED`; the write still happens.
- **Personal data note**: the `target_id` of `auth.login_failed` (the first 16 characters of
  the identifier's keyed hash) is a pseudonym. It is personal data under the privacy rules: it
  links attempts on one identifier and is covered by subject-access and erasure requests like
  any other identifier.
- Fail-closed note: a transactional audit write that fails also fails a successful login. The
  Integrator has raised this with the owner.

## Protection

Scope: HOM-115 (owner decision 2026-10-17, option B). Migration `e6c3a9f1d4b8`
(`audit_events_append_only`), the one HOM-115 migration. Code:
`Homayar/backend/database/rls.py` (`APPEND_ONLY_TABLES`, `AUDIT_READER_ROLE`,
`audit_reader_statements`) and the ORM guard in `audit_service.py`.

### Append-only for the application

* **ORM guard (every database, SQLite included).** Flushing a changed or deleted
  `AuditEvent`, or running an ORM `update(AuditEvent)` / `delete(AuditEvent)`, raises
  `AuditImmutableError`. No application code path updates or deletes audit rows.
* **Database privileges (PostgreSQL).** `homayar_app` holds `SELECT` and `INSERT` on
  `audit_events` and no `UPDATE`, `DELETE` or `TRUNCATE`: raw SQL from a request
  handler, a worker or a sweep fails with `permission denied for table audit_events`.
  The tenant policy (`homayar_tenant_isolation`) and its `WITH CHECK` are unchanged.
* **Deleting a workspace or a user still works.** The foreign keys keep
  `ON DELETE SET NULL`; the referential action runs as the table owner, so the row
  stays (event, target, time, outcome, correlation id) and only loses the reference.
* **What it does not stop.** The table owner (the migration role) and a superuser can
  still change or delete rows. The append-only rule closes the application's paths;
  detecting changes made outside them is the job of the protected sink. Neither is
  cryptographic immutability.
* **It needs the recommended runtime login.** The rule holds when `DATABASE_URL` logs in
  as a role `IN ROLE homayar_app` ([deployment](deployment.md#database-roles-and-row-level-security)).
  With the other shape (the owner's login plus `DATABASE_APP_ROLE=homayar_app`) the
  application holds the owner's credentials, and only code that keeps the role switch
  is bound by it.

### The audit reader role

`homayar_audit_reader` is the only role that reads every tenant's audit rows:

| Property | Value |
| --- | --- |
| Login | `NOLOGIN`; no superuser, `BYPASSRLS`, `CREATEDB` or `CREATEROLE` |
| Privileges | `USAGE` on schema `public`, `SELECT` on `audit_events`, nothing else |
| Policy | `homayar_audit_export FOR SELECT TO homayar_audit_reader USING (true)` |
| Members | the exporter's own login only; never `homayar_app`, never the application's login |

`SET ROLE` checks the memberships of the session (login) user, so the application's
login must not be a member of `homayar_audit_reader` (and must not be a superuser,
which can become any role). A setting such as `homayar.service_scope` that any session
can set does not unlock other tenants: the cross-tenant view is a role, not a scope.

Create the exporter's login once per cluster, as the database owner (the password comes
from the secret store; it is never written into a file in the repository):

```sql
CREATE ROLE homayar_audit_exporter LOGIN PASSWORD :'exporter_password'
  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE IN ROLE homayar_audit_reader;
-- the application's login must not be a member:
SELECT pg_has_role('<application login>', 'homayar_audit_reader', 'MEMBER');  -- false
```

### Migration `e6c3a9f1d4b8`

* Upgrade: revokes `UPDATE`, `DELETE` on `audit_events` from `homayar_app`, creates the
  reader role (idempotent), its grants and its policy. No data changes. A role of that
  name that already exists is made inert: `NOLOGIN` without a password, no role attribute,
  a member of no role, and `homayar_app` no member of it; if that is not possible (an
  indirect membership path from `homayar_app`, or a change the migration role may not
  make) the upgrade fails with "homayar_audit_reader is not inert". Creating the
  role needs `CREATEROLE`, as for `homayar_app`; if the migration role does not have it,
  a DBA creates the role first:
  `CREATE ROLE homayar_audit_reader NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;`
* Downgrade: drops the reader policy and grants, drops the role when no other database
  on the cluster still grants it anything and the migration role may drop roles
  (otherwise the role stays, `NOLOGIN` and without privileges in this database), and
  gives `homayar_app` back `UPDATE` and `DELETE`. The previous application never updates
  or deletes audit rows, so it runs on either schema. A deeper downgrade applies the rule
  again once it passes `b9e4f2a6c8d3` (and at `f1b6d8e4a2c7`, `d7f3a1c9e2b4`): those
  downgrades, like every older upgrade, run the current `apply_row_level_security`.
