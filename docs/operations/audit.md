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
  detecting changes made outside them is the job of the
  [protected audit sink](#protected-audit-sink). Neither is cryptographic immutability.
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

## Protected audit sink

Scope: HOM-115 (WP20-T03). Code: `Homayar/backend/observability/audit_sink.py`. Decision:
[ADR 0036](../architecture/decisions/0036-operational-evidence-observability-audit-release.md),
Decision 3.

The sink is an independent copy of `audit_events` that the application cannot reach: a
hash chain of JSON lines in a directory the API and worker containers do not mount (a
separate host or volume, ideally write-once storage). `verify` recomputes the chain and
compares it with the database, so a row changed, deleted or back-dated by the table owner
or a superuser becomes visible. It is **tamper-evident, not tamper-proof**: whoever holds
both write access to the sink and the HMAC key can rewrite it undetected (see
[Sink limits](#sink-limits)).

```
AUDIT_SINK_DIR/
  HEAD.json                        last_seq, head_hash, watermark, segments, algorithm, key_id,
                                   legacy_review, mac (HMAC of the other fields under the key)
  segments/00000001.jsonl          one record per line, mode 0440, never rewritten
  segments/00000001.manifest.json  file sha256, first/last seq, counts, window, prev/head hash
```

A record is `{"seq", "type", "row" | "erasure", "prev", "hash"}`: `seq` counts from 1 with
no gap, `row` holds the audit row's columns exactly as stored (`id`, `occurred_at`,
`event_type`, `actor_user_id`, `workspace_id`, `target_type`, `target_id`, `outcome`,
`request_id`, `ip_hash`, `details`), `hash = HMAC-SHA256(key, prev || "\n" || record)`.
The sink therefore holds nothing the table does not hold: free text was already masked at
write time, IPs are hashes, secrets are `[redacted]`.

### Sink settings

| Setting | Meaning |
| --- | --- |
| `AUDIT_SINK_DIR` | the sink directory (on the operator's host or volume) |
| `AUDIT_SINK_HMAC_KEY` | secret, at least 32 characters; keys the chain. Required outside test/development. Kept with the operator (secret store, `AUDIT_SINK_HMAC_KEY_FILE`), never in the application's secrets |
| `AUDIT_EXPORT_DATABASE_URL` | secret; the exporter's own login (`homayar_audit_exporter`), a member of `homayar_audit_reader`. Production: TLS as for `DATABASE_URL` (HOM-161, opt-out `DATABASE_ALLOW_INSECURE_TRANSPORT` on-premise only) |
| `AUDIT_EXPORT_SETTLE_SECONDS` | 60 to 86400, default 600: events younger than this wait for the next export, so a transaction that commits late is not skipped. Must exceed the longest transaction plus the clock skew between application hosts and the database |
| `REDIS_URL` | where the [heartbeats](#sink-heartbeats) go (the deployment's Redis, the one the API reads) |

### Exporter setup

1. Create the exporter's login (once per cluster, see [The audit reader role](#the-audit-reader-role)).
2. On the operator's host: a checkout or image of the same release, and an environment
   with the settings above. The exporter loads the normal settings, so in production it
   also needs `ENVIRONMENT=production`, `DATABASE_URL` set to the exporter's URL and its
   own random `JWT_SECRET` / `TOKEN_PEPPER` (it issues nothing): never the application's.
3. Create `AUDIT_SINK_DIR` owned by the operator's user, mode `0750`; back it up to a
   second location after each export (the backup inherits the same protection rules).

The exporter refuses to run (exit 2, one line on stderr, no URL or key) when the login is
not a member of `homayar_audit_reader`, is a superuser or has `BYPASSRLS`, or can become
`homayar_app`; when `homayar_audit_reader` itself is not inert (it can log in, holds a role
attribute, is a member of a role, or `homayar_app` reaches it); and when
`AUDIT_EXPORT_DATABASE_URL` is empty on PostgreSQL. A `HEAD.json` that fails its MAC stops
export and `record-erasure` too (run `verify`). It reads with
`SET ROLE homayar_audit_reader` in read-only transactions.

### Export runbook

```bash
python -m backend.observability.audit_sink export
# the first export into an empty sink, staging and production only:
python -m backend.observability.audit_sink export --legacy-review <approval reference>
```

Before the first export, do the one-off [legacy-row review](#legacy-row-review): decided
(owner, 2026-10-18). In staging and production the first export
into an empty sink refuses to run (exit 2, nothing written) until it is given the review's
approval reference with `--legacy-review`; later exports need nothing and refuse the flag.
Test and development are not gated.

Run it from `Homayar/` on a schedule (every 15 minutes is a good default; the window is
`[watermark, now - AUDIT_EXPORT_SETTLE_SECONDS)` by `occurred_at`, read by the database
clock). It appends one segment with every settled event since the watermark and prints
`{"exported", "segment", "last_seq", "watermark", "head_hash"}`. Only one export or erasure
record runs at a time (`.lock`); a second one exits 2. An export interrupted between its
steps is finished by the next run (the manifest is written before the segment is renamed
into place, and `HEAD.json` last). Record `head_hash` and `last_seq` somewhere the sink's
writer cannot change (the heartbeat, the scheduler's log, a ticket): that is the
independent copy of the head that [Sink limits](#sink-limits) relies on.

### Legacy-row review

**Decided (owner, 2026-10-18):** keep the `--legacy-review` gate. Audit rows written before
HOM-114 masking must be reviewed before the first export in staging or production.

Rows written before the HOM-114 release (the *masking cut-over*: when that release was
deployed) went in without write-time masking, so their free-text values (`reason`,
`*_reason`, `name`, `external_event_id`, `source_provider`) may hold personal data. Once
exported, a row's copy stays in its write-once segment (see the
[erasure procedure](#erasure-procedure)), so the review comes first:

1. List the candidate rows through the exporter's login, as `homayar_audit_reader` (it sees
   every tenant; the table owner sees only platform rows unless it binds a workspace):

   ```sql
   BEGIN;
   SET LOCAL ROLE homayar_audit_reader;
   SELECT id, occurred_at, event_type, workspace_id, details
   FROM audit_events
   WHERE occurred_at < '<masking cut-over, UTC>'
     AND EXISTS (
       SELECT 1 FROM json_object_keys(details) AS k
       WHERE k IN ('reason', 'name', 'external_event_id', 'source_provider')
          OR k LIKE '%\_reason'
     )
   ORDER BY occurred_at, id;
   ROLLBACK;
   ```

2. The owner decides, row by row or as a whole, what may be exported, and approves it under
   one reference (ticket or change id; letters, digits and `._:/#-`).
3. Rows that must not reach the sink are erased by the DBA with step 3 of the
   [erasure procedure](#erasure-procedure), before the first export. They never reach the
   sink, so there is no `record-erasure` for them (in staging and production it is refused
   on an empty sink); the approval reference documents them.
4. Run the first export with `--legacy-review <reference>`. The reference becomes record 1
   of the chain (`type: legacy_review`, with the environment and the time), is copied into
   the first manifest and `HEAD.json`, and every `verify` reports it as `legacy_review`
   (a copy that disagrees with the chain is `manifest_mismatch` / `head_mismatch`).

### Verify runbook

```bash
python -m backend.observability.audit_sink verify              # chain and database
python -m backend.observability.audit_sink verify --sink-only  # chain only (offline copy)
```

Run `verify` after each export or at least daily, and `--sink-only` on every backup copy.
Exit 0: no problem (notices may be listed); 1: at least one problem; 2: configuration
error. The JSON report lists `problems` and `notices` with the kind, `seq` and event id,
never a value, and `legacy_review` (the acknowledged [legacy-row review](#legacy-row-review),
or `null`).

### Verification findings

| Kind | Where | Meaning | Action |
| --- | --- | --- | --- |
| `record_altered` | problem | a line's hash does not match (edited, inserted, forged) | security incident: compare with the backup copy, find who wrote the sink |
| `sequence_gap` | problem | a line was removed or lines were reordered | as above |
| `record_duplicated` | problem | an event id appears twice | as above |
| `segment_missing`, `segment_file_changed`, `manifest_mismatch` | problem | a segment or manifest is missing or disagrees with its file | as above; restore from the backup copy |
| `head_mismatch` | problem | `HEAD.json` disagrees with the chain, fails its MAC (changed without the key), has a watermark before the last export window, or a segment exists beyond it | after a crash: run `export` once (it finishes the interrupted segment); otherwise as above |
| `head_missing`, `key_mismatch` | problem | no `HEAD.json`; or the configured key (or its absence) is not the sink's (`key_id` differs) | check `AUDIT_SINK_DIR` and `AUDIT_SINK_HMAC_KEY` |
| `row_changed` | problem | an exported row differs in the database (the detail names the columns) | security incident: the table owner or a superuser changed history |
| `row_deleted` | problem | an exported row is gone from the database without a recorded erasure | as above |
| `unexported_row` | problem | a row older than the watermark never reached the sink (inserted late or back-dated) | as above; or raise `AUDIT_EXPORT_SETTLE_SECONDS` if a long transaction committed late |
| `referential_null` | notice | only `workspace_id` / `actor_user_id` became `NULL`: the workspace or user was deleted (`ON DELETE SET NULL`) | none |
| `accepted_erasure` | notice | an [erasure](#erasure-procedure) recorded in the chain; the detail says `deleted`, `changed:<columns>`, `row unchanged` (not yet run), `never exported` or `recorded` (`--sink-only`) | check it matches an approval |

Notices are listed and counted (`accepted_erasures`, `referential_nulls` in the heartbeat),
never hidden.

### Erasure procedure

Owner decision 2026-10-17 (option B): the application never changes or deletes audit rows.
A historical row may be erased only by this procedure, run as the **table owner**:

1. The owner approves the erasure of named event ids, with a reference (ticket or change
   id, for example `CHG-2026-0042`; letters, digits and `._:/#-`, no free text).
2. The operator records it in the chain **before** the database change:
   `python -m backend.observability.audit_sink record-erasure --event-id <id> --approval <ref>`
   (one call per event id; a chained record, so it cannot be forged without the key).
3. The DBA, connected as the table owner, removes or edits the row in one transaction.
   The policies are `FORCE`d, so the owner binds the row's workspace first (not needed for
   a platform row, `workspace_id IS NULL`):

   ```sql
   BEGIN;
   SELECT set_config('homayar.workspace_id', '<workspace id of the row>', true);
   DELETE FROM audit_events WHERE id = '<event id>';   -- exactly one row
   COMMIT;
   ```

4. The operator runs `verify`: the row is reported as `accepted_erasure` (a notice), the
   run passes, and the notice stays in every later report.

The exported copy stays in its sink segment (segments are write-once). It holds only what
the row held after write-time masking; removing it means rebuilding the chain from segment
1 and is an owner decision outside this procedure. Rows recorded before HOM-114 masking can
hold unmasked free text: the [legacy-row review](#legacy-row-review) handles them before
the first export.

### Sink retention

`audit_events` has no retention purge, so neither has the sink: keep every segment and its
manifest. `verify` always starts at segment 1, and a deleted segment is `segment_missing`
by design. When the owner sets an audit retention period, pruning old segments needs a
checkpoint procedure (an anchored head at the cut), which is not built. Keep at least one
backup copy of the sink on separate storage and verify it with `--sink-only`.

### Sink heartbeats

Each run writes the operator heartbeat hash `homayar:obs:sweep:audit_export` or
`homayar:obs:sweep:audit_verify` in Redis (best effort, as for the sweeps): `last_run`,
`last_success` or `last_failure`, `last_duration_seconds`, `last_error_type` and the
counters (`exported`, `last_seq` for export; `records`, `erasures`, `segments`, `last_seq`,
`problems`, `notices`, `accepted_erasures`, `referential_nulls`, `legacy_reviewed` for a
passing verify). A refused first export records `last_failure` on `audit_export`. A
failing verify records `last_failure` with `last_error_type=AuditSinkError`. `/metrics`
exports them as `homayar_periodic_task_last_success_timestamp_seconds{task="audit_export"}`
(and `..._last_failure_...`, `..._last_run_...`, `homayar_periodic_task_result{task,field}`).
Two alerts read them: [HomayarAuditExportStale](alerts.md#homayarauditexportstale) (no
successful export for 2 hours, a failing first export, or no export heartbeat at all; the
threshold assumes the 15-minute schedule) and
[HomayarAuditSinkVerificationFailed](alerts.md#homayarauditsinkverificationfailed) (the last
`verify` failed).

### Sink limits

* A writer of the sink who also holds `AUDIT_SINK_HMAC_KEY` can rewrite the chain.
* Cutting the newest segments together with `HEAD.json` (and the database rows they hold)
  is invisible to the sink alone; compare `head_hash` / `last_seq` with the independent
  copy recorded after each export. `HEAD.json` carries a MAC, so it cannot be edited without
  the key (its watermark is not in the chain), but an older valid copy can be put back.
* The table owner can change a row before its first export: the window (the settle
  period plus the export interval) is short, but it is not zero.
* `verify` reads the whole sink and every row older than the watermark: fine at current
  volumes, linear in the number of events.
