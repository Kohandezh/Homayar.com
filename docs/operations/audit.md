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
| `security` | `auth.login` (success and failure), `auth.logout`, `auth.refresh_replay_detected`, password reset, OTP, Google sign-in, API key created/revoked |
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
* Personal data in free text (owner decision 2026-10-17, option B): the values of `reason`,
  `release_reason`, `deletion_reason`, any `*_reason`, `name` (an API key's label),
  `external_event_id` and `source_provider` pass through the text PII detector
  (`backend/privacy/text_pii.py`: phone, e-mail, Iranian national ID, bank card, IBAN, postal
  code) and are stored with class placeholders (`[PHONE_1]`, `[EMAIL_1]`, …), then capped at
  120 characters. Masking runs before the cap, so a cut never leaves half an identifier. Ids,
  counts and enum values are stored unchanged.

## Delivery and failure behaviour

* **Transactional** (all events of operations that happened): the row commits with the
  operation. If the insert fails, the transaction rolls back, the operation does not happen
  and the caller gets an error; `homayar_audit_write_failures_total{delivery="transactional"}`
  counts it (`HomayarAuditWriteFailures` alerts).
* **Best effort** (refused attempts only: failed login): recorded in its own commit after
  the rollback. If it fails, the response is unchanged, `AUDIT_WRITE_FAILED` is logged and
  `homayar_audit_write_failures_total{delivery="best_effort"}` counts it.
* Privacy-sensitive reads (`transcript.original_accessed`, `pii.revealed`, exports) commit
  their audit row before the response is returned: no audit, no data.

## Tenant visibility

Workspace owners and admins read their own workspace's events through
`GET /api/v1/admin/audit-events` (permission `audit:read`); the query is filtered by the
bound workspace and row-level security bounds it again. Platform events (`workspace_id`
NULL) are never returned to a tenant.
