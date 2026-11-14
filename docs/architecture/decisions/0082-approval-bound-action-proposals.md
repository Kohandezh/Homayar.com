# ADR 0082: Approval-bound action proposals and delivery requests

Date: 2026-11-14 · Status: Proposed (HOM-135, WP24-T03, F60) · Builds on: ADR 0005, ADR 0014, ADR 0021, ADR 0048, ADR 0050, ADR 0057, ADR 0058, ADR 0062, ADR 0065

All code paths are under `Homayar/backend/`. HOM-135 lands in two PRs. PR 1 adds the
`action_proposals` and `action_delivery_requests` tables (migration `fc9730c5f039`),
the row-level security lists, the conversation purge and the backup reconciliation.
PR 2 adds the service, the routes, the permissions and the audit events. HOM-136
delivers the approved requests. This ADR records the decisions of PR 1 and PR 2, so
the schema serves the rules that come after it.

## Context

F60 (parent HOM-27) asks for this: "Rejected/unapproved/revoked/stale proposals cause
zero external writes; repeated approved delivery causes one logical CRM/ticket effect;
connector failures retry with complete audit." WP24 step 3 says: "Build a
preview/approve/reject workflow for CRM/ticket/coaching actions with authorized human
ownership."

Homayar already extracts action items with evidence (ADR 0058), judges calls in
reviews (ADR 0057), maps CRM records (ADR 0048) and turns findings into coaching tasks
(ADR 0062). What is missing is a safe step between "the system suggests an action" and
"an outside system changes". Four risks shape the design:

1. A person must approve the exact payload that leaves, not a description of it.
2. A payload built from an old transcript or an old action item must not leave.
3. A revoke must stop a delivery that has not started yet, and nothing else.
4. Two deliveries of one approval must cause one effect in the outside system.

## Definitions

- **Proposal.** One row of `action_proposals`: one action type, one source, one target
  and one exact payload.
- **Source.** An action item (for `crm_update` and `ticket_create`) or a review finding
  (for `coaching_task`). A finding is one criterion (`criterion_id`) of one review
  item.
- **Pin.** The transcript revision the source was read from
  (`source_transcript_revision_id`) and the sha256 hex of the canonical source fields
  (`source_digest`, computed by the PR 2 service).
- **Open proposal.** A proposal with status `pending` or `approved`.
- **Stale proposal.** A proposal whose pin no longer matches the current state: the
  meeting's current transcript revision changed, the source digest changed, the source
  was removed, or a finding is no longer `failed`.
- **Delivery request.** One row of `action_delivery_requests`: the immutable copy of
  an approved proposal.

## Decision

### D1. The proposal table (PR 1)

One table, `action_proposals` (model `ActionProposal`).

| `action_type` | `source_type` | target |
| --- | --- | --- |
| `crm_update` | `action_item` | `target_system` (slug) and `target_external_record_id` (an `external_records` row of HOM-88) |
| `ticket_create` | `action_item` | `target_system` (slug of a configured mapping profile), no external record |
| `coaching_task` | `review_finding` | internal: `target_system` and `target_external_record_id` are NULL |

**Frozen lists.** They live in `backend/database/saas_models.py`:

- `ACTION_PROPOSAL_TYPES`: `crm_update`, `ticket_create`, `coaching_task`.
- `ACTION_PROPOSAL_SOURCES`: `action_item`, `review_finding`.
- `ACTION_PROPOSAL_STATUSES`: `pending`, `approved`, `rejected`, `revoked`.
- `ACTION_PROPOSAL_OPEN_STATUSES`: `pending`, `approved`.
- `ACTION_PROPOSAL_REJECT_REASONS`: `not_needed`, `incorrect`, `duplicate`, `other`.
- `ACTION_PROPOSAL_REVOKE_REASONS`: `approved_in_error`, `no_longer_needed`, `other`.

**The payload.** `payload_json` is the exact payload that would leave. `payload_hash`
is the sha256 hex of its canonical JSON. `payload_version` starts at 1 and grows by
one on every edit or refresh. `revision` starts at 1 and grows by one on every state
change, so every write is a compare-and-set on it, like `coaching_tasks.revision`.

**The source.** `action_item_id` and `review_item_id` are single-column keys with ON
DELETE SET NULL. A removed source leaves the proposal with a NULL id; PR 2 treats that
proposal as stale. `criterion_id` is the finding's criterion key.
`source_transcript_revision_id` is ON DELETE CASCADE: a removed revision takes the
proposal and its request. `target_external_record_id` is ON DELETE RESTRICT: a record
that a proposal names cannot be deleted.

**Checks.** Every check uses no regex, so SQLite runs the same rule. Each check is
false, never NULL, for a row it refuses. No check names a user column, because the
user columns are SET NULL when a user is removed.

- The three lists: `ck_action_proposals_action_type`,
  `ck_action_proposals_source_type`, `ck_action_proposals_status`.
- `revision >= 1` and `payload_version >= 1`.
- `payload_hash` and `source_digest`: exactly 64 characters of lowercase hex.
- `criterion_id` follows `CRITERION_KEY_CHECK`; `target_system` follows the slug rule
  of HOM-88.
- `ck_action_proposals_source_shape`: an `action_item` source has no review item and no
  criterion; a `review_finding` source has no action item and always a criterion.
- `ck_action_proposals_type_source` and `ck_action_proposals_target_shape`: the table
  above.
- The two reason lists, and one check per status:
  - `pending`: no decision, no reason, no revoke.
  - `approved`: `decided_at` set; no reject reason and no revoke.
  - `rejected`: `decided_at` and `reject_reason` set; no revoke.
  - `revoked`: `decided_at`, `revoked_at` and `revoke_reason` set; no reject reason.

Each cross-field check passes when a value that another check owns is outside its
list. So a row that breaks one field fails only that field's own named check, on
PostgreSQL (which tests checks in name order) and on SQLite alike.

**Keys and indexes.**

- `uq_action_proposals_scope` (`id`, `workspace_id`, `meeting_id`) is the target of the
  request's composite key.
- `uq_action_proposals_open_item`: at most one open proposal per action item and action
  type. `uq_action_proposals_open_finding`: at most one open proposal per finding
  (`review_item_id`, `criterion_id`). Both are partial unique indexes over the open
  statuses and a non-NULL source, on PostgreSQL and SQLite. A rejected or revoked
  proposal does not block a new one.
- `ix_action_proposals_queue` (`workspace_id`, `status`, `created_at`, `id`) for the
  approval queue, `ix_action_proposals_workspace_meeting` for the purge, and an index on
  each key column that has none, so cascades and SET NULL do not scan.

### D2. The delivery request table (PR 1)

One table, `action_delivery_requests` (model `ActionDeliveryRequest`). An approval
writes exactly one row, in the same transaction that moves the proposal to `approved`.
The row is an exact copy of what was approved: `payload_json`, `payload_hash`,
`payload_version`, the action type, the target, the source type, `source_id`,
`source_criterion_id`, `source_transcript_revision_id`, `source_digest`, the approver
and `approved_at`.

**Why a separate immutable row.** The proposal must stay writable: PR 2 changes its
status and revision, and a revoke changes it after the approval. If the approved
payload lived only on the proposal, a bug or a later edit could change what HOM-136
sends. The request is a second row that never changes:

- `action_delivery_requests` is in `NO_UPDATE_TABLES`: the application role has SELECT,
  INSERT and DELETE, never UPDATE. DELETE stays, because the purge removes the rows.
- An ORM `before_update` guard raises `ActionDeliveryRequestImmutableError` for any
  change.
- The source columns are plain strings, not keys. The copy keeps the source it was
  approved from, even after that source is removed.

**Keys.**

- `fk_action_delivery_requests_proposal` (`proposal_id`, `workspace_id`, `meeting_id`)
  references `uq_action_proposals_scope`, ON DELETE CASCADE. A request is always in
  its proposal's own workspace and meeting.
- `uq_action_delivery_requests_proposal`: one request per proposal, so one approval is
  one delivery.
- `idempotency_key` is unique (`uq_action_delivery_requests_idempotency_key`). PR 2 sets
  it to the sha256 hex of `workspace_id|proposal_id|payload_hash`. HOM-136 sends it to
  the connector, so a retried delivery causes one logical effect.
- `uq_action_delivery_requests_scope` (`id`, `workspace_id`, `meeting_id`) is the target
  of the delivery records of HOM-136.
- The same hex, `payload_version`, `type_source`, `target_shape` and criterion key
  checks as the proposal, with the `ck_action_delivery_requests_` prefix.

### D3. The status rules (PR 2)

- `pending` to `approved` (approve), `pending` to `rejected` (reject), `approved` to
  `revoked` (revoke). `rejected` and `revoked` are final.
- Only a `pending` proposal is edited or refreshed. Each edit or refresh sets the new
  payload and hash and adds one to `payload_version`.
- Every change sends `expected_revision`. The service updates the row only when
  `revision` still equals it, and adds one. Otherwise it answers 409.
- **Revoke lives on the proposal.** A revoke sets `revoked_at`, `revoke_reason` and the
  revoker on the proposal. The delivery request row never changes. HOM-136 reads the
  proposal's status, so the revoke reaches it without changing the request.
- Rejected, revoked and stale proposals never get a delivery request. Rejected and
  revoked by the status rules; stale by the approval checks of D4.
- **An approved proposal stays open, on purpose.** After HOM-136 delivers it, its status
  stays `approved`. So it keeps blocking any new proposal of the same action type for
  the same action item (`uq_action_proposals_open_item`), or for the same finding
  (`uq_action_proposals_open_finding`). This is the "one logical effect per source and
  type" rule of F60. PR 2 answers such a create with 409 and its own error code (for
  example `proposal_exists`), never with a 500 from the unique index. A rejected or
  revoked proposal frees the slot.

### D4. Approval binding and staleness (PR 2)

The approve body carries `expected_revision`, `payload_hash`,
`source_transcript_revision_id` and an optional edited payload.

- The approval is refused with 409 when `payload_hash` differs from the stored hash
  (the approver saw another payload), or `source_transcript_revision_id` differs from
  the stored pin.
- The approval is refused with 409 when the pin is stale: the meeting's current
  transcript revision is not the pinned one, the source digest computed now differs
  from `source_digest`, the source was removed (a NULL id), or a finding's final outcome
  (ADR 0057 D4) is no longer `failed`.
- Edit-then-approve: with an edited payload, the service checks the stored hash first,
  stores the edit with a new hash and `payload_version + 1`, and approves that exact
  payload, all in one transaction.
- `refresh` rebuilds the payload from the current source, takes new pins, and adds one
  to `payload_version`. A stale proposal is refreshed or rejected, never approved.
- **Scope rule.** Before the insert, the service checks that the source item or finding
  and the pinned transcript revision belong to the proposal's own workspace and
  meeting, and that the external record belongs to the workspace. The single-column
  keys of D1 cannot hold this rule: a composite key with SET NULL would also clear
  `workspace_id` and `meeting_id`, and `transcript_revisions` has no
  (`id`, `workspace_id`, `meeting_id`) key.
- Self-approval is allowed in v1: many workspaces have one owner, who proposes and
  approves. A two-person rule is an alternative for later (see Alternatives).

### D5. The HOM-136 contract

HOM-136 delivers a request in this order, in one transaction:

1. Lock the proposal row: `SELECT ... FOR UPDATE`.
2. Under that lock, require `status = 'approved'`, and check that the request's
   `payload_hash` equals the proposal's `payload_hash`. If either check fails, nothing
   is written outside.
3. Record delivery start in its own delivery record (keyed by the request,
   `uq_action_delivery_requests_scope`), then commit and call the connector with the
   request's `idempotency_key`.

A revoke takes the same row lock. It refuses once delivery started. So a revoke either
happens before the delivery starts, and nothing leaves, or it is refused. A connector
failure retries the same request with the same key (ADR 0050 for the retry and audit
pattern of the webhook ledger).

### D6. Routes, permissions and audit (PR 2)

- Routes under `/api/v1/action-proposals`, behind `require_module("integrations")`
  (ADR 0065) with a session-only workspace dependency, like `require_session_workspace`
  of the trends routes. API keys are refused: an outbound write needs a person.
- Planned routes: create a proposal from a source, list the queue, read one proposal
  (the preview), `approve`, `reject`, `revoke` and `refresh`. The PR 2 SPEC fixes the
  bodies.
- New permissions: `action:propose` (owner, admin, member) and `action:approve` (owner,
  admin). Neither is ever granted to an API key.
- Record scope: a user acts only on proposals whose source meeting is in the user's
  record scope.
- Audit events for create, approve, reject, revoke and refresh. They carry ids, codes,
  hashes and versions only, never payload text and never source text.

### D7. Purge, backup and isolation (PR 1)

- `database/rls.py` lists both tables in `WORKSPACE_TABLES`, next to `coaching_tasks`:
  row-level security, tenant only, never through a share token. `action_proposals` has
  the full grants; `action_delivery_requests` is in `NO_UPDATE_TABLES`. The migration
  applies the policies and the grants with `apply_row_level_security`.
- The conversation purge (ADR 0021) deletes the purged conversation's
  `action_delivery_requests`, then its `action_proposals`, by `workspace_id` and
  `meeting_id`. It does this before it deletes the action items, the review items and
  the transcript revisions, whose cascades would otherwise remove them uncounted. It
  counts them in the removed total. A legal hold keeps them like the other content
  rows.
- `backup/reconcile.py` counts both tables (`CONTENT_TABLES`).

## Consequences

- A person approves one exact payload, and the request keeps a copy of it. The
  application role can never change a request in place: it has no UPDATE grant, and the
  ORM guard refuses an update. It keeps INSERT and DELETE, for the approval and the
  purge.
- An approved proposal blocks a new proposal of the same type for the same source, so
  one source and type cause one logical effect. A rejected or revoked proposal frees
  the slot.
- A payload built from an old transcript or an old source cannot be approved, because
  the approval checks the pins.
- A revoke stops a delivery that has not started, because HOM-136 and the revoke take
  the same row lock.
- A retried delivery sends the same idempotency key, so the outside system sees one
  logical effect.
- The database holds the shape rules, so a bug in the service cannot store a CRM
  update without a record, a coaching task with a target, or two open proposals for
  one source.
- A user's removal keeps every row; only the user columns become NULL.
- PR 2 needs no schema change.

## Alternatives considered

- **Approval columns on the proposal only, no request table.** Rejected. The proposal
  must stay writable for status changes and revokes, so the approved payload would not
  be protected by the grants.
- **A status column on the request, and revoke on the request.** Rejected. The request
  must never change (`NO_UPDATE_TABLES`). Revoke lives on the proposal, and HOM-136
  checks the proposal under the row lock.
- **Store only the hash and rebuild the payload at delivery.** Rejected. A rebuild can
  differ from what the person saw.
- **Composite scope keys for the source, like ADR 0062.** Rejected for now. A composite
  key with SET NULL also clears the scope columns, and a new key on
  `transcript_revisions` would change another table. The PR 2 service checks the scope
  (D4).
- **A two-person rule (no self-approval).** Not in v1, because many workspaces have one
  owner. It can be added later as a workspace setting that refuses an approval by
  `created_by_user_id`.
- **One table per action type.** Rejected. One workflow, one queue and one set of
  status rules is simpler.
- **A regex check for the hex columns.** Rejected. SQLite has no regex, and the same
  rule must run on both databases.

## Not built

- No service, routes, permissions or audit events in PR 1 (PR 2).
- No delivery, connector call, retry or delivery record (HOM-136).
- No automatic proposal: a person asks for each one.
- No other action types (for example e-mail).
- No console work.
