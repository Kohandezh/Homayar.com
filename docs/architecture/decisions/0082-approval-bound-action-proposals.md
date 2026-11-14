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
  source was removed, the meeting's current transcript revision changed, the action item
  is no longer open, a finding is no longer `failed`, the target is no longer valid, or
  the source digest changed (D4 gives the order).
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
  `revoked` (revoke). `rejected` and `revoked` are final. Every other transition answers
  409: `action_proposal_not_pending` for approve, reject and refresh of a proposal that
  is not `pending`, `action_proposal_not_approved` for a revoke of one that is not
  `approved`.
- Only a `pending` proposal is refreshed, and only an approval stores an edit (there is
  no stored edit before approve). Each refresh or edit sets the new payload and hash and
  adds one to `payload_version`.
- Every change sends `expected_revision`. The service locks the proposal row
  (`SELECT ... FOR UPDATE`), checks the call's agent or the coachee of a `coaching_task`
  proposal (D4), then the status, then the revision, and adds one to `revision`. A moved
  revision answers `409 action_proposal_revision_conflict` with `current_revision` in the
  details. So of two writes that read the same revision,
  exactly one lands.
- **Revoke lives on the proposal.** A revoke sets `revoked_at`, `revoke_reason` and the
  revoker on the proposal. The delivery request row never changes. HOM-136 reads the
  proposal's status, so the revoke reaches it without changing the request.
- Rejected, revoked and stale proposals never get a delivery request. Rejected and
  revoked by the status rules; stale by the approval checks of D4.
- **An approved proposal stays open, on purpose.** After HOM-136 delivers it, its status
  stays `approved`. So it keeps blocking any new proposal of the same action type for
  the same action item (`uq_action_proposals_open_item`), or for the same finding
  (`uq_action_proposals_open_finding`). This is the "one logical effect per source and
  type" rule of F60. PR 2 answers such a create with `409 action_proposal_exists`
  (it catches the unique violation of those two indexes only), never with a 500 from the
  unique index. A rejected or revoked proposal frees the slot.
- Reasons come from their lists: `reject_reason` from `ACTION_PROPOSAL_REJECT_REASONS`,
  `revoke_reason` from `ACTION_PROPOSAL_REVOKE_REASONS`; anything else answers
  `422 action_proposal_invalid`.

### D4. Create, approval binding and staleness (PR 2)

**Create**, checks in this order; a refusal writes nothing:

1. Shape: `action_type` from the list; `crm_update` and `ticket_create` name an
   `action_item_id` and no finding or coaching fields; `coaching_task` names a
   `review_item_id` and a `criterion_id` and no `action_item_id`; the target fields
   follow the D1 table (`target_system` a slug). Else `422 action_proposal_invalid`,
   and the message names the field.
2. The source is visible: the action item's call is in the caller's record scope and
   not deleted (`404 action_item_not_found`), or the review item is visible under the
   review-queue rule (`404 review_item_not_found`).
3. The source is eligible: an action item is `open` or `in_progress`
   (`409 action_item_not_actionable`). A finding passes exactly the coaching create
   checks of ADR 0062 D3 route 1, with the same codes: the caller is not the call's
   agent, a completed review with a human result, no open appeal, an agent to coach, a
   criterion of the result, a final outcome of `failed`, no active coaching task, the
   owner, the due date and the instruction. Both features run one function,
   `coaching_service.check_create`, so the rules cannot drift apart.
4. The pin: the call's current transcript revision (`409 transcript_missing` without
   one). The source must have been read from it: an AI action item uses its analysis
   run's revision, a person item (no run) the current one, a finding its scorecard
   result's revision. A mismatch answers `409 action_proposal_source_stale`.
5. The target: a `crm_update` names an active `external_records` row of the workspace,
   of the target system, that is the call's current `customer` or `ticket` link in that
   system; a `ticket_create` names the source system of an active mapping profile of the
   workspace. Else `422 action_proposal_target_invalid`.
6. The payload, its hash and the source digest; the row is inserted `pending` at
   revision 1 and payload version 1.

**Payloads** (exact keys, values from the source, no other keys):

- `ticket_create.v1`: `title` (the first 120 characters of the description, trimmed),
  `description`, `priority`, `due_at`, `assignee_text`, `kind`, `meeting_id`,
  `action_item_id`.
- `crm_update.v1`: `source_system`, `record_type` and `external_id` of the record,
  `note` (the description), `due_at`, `priority`, `kind`, `meeting_id`,
  `action_item_id`.
- `coaching_task.v1`: `review_item_id`, `criterion_id`, `agent_user_id`,
  `owner_user_id`, `due_at`, `instruction`.
- `kind` is the HOM-133 label, so a proposed next step stays apart from a commitment
  (F38). Dates are ISO 8601 in UTC.
- Canonical JSON is `json.dumps(value, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False)` in UTF-8; every hash is its sha256 in lowercase hex.

**Source digest**: of an action item, its `id`, `description`, `assignee_text`,
`assignee_user_id`, `due_at`, `priority`, `status`, `kind` and `analysis_run_id`; of a
finding, the review item, the criterion, the scorecard result, the final outcome and its
source, and the call's agent.

**Stale reasons**, checked in this order, the first one wins: `source_removed` (the
source id is NULL or its row is gone), `revision_changed` (the call's current revision
is not the pin), `source_not_actionable` (the action item is not open or in progress),
`finding_not_failed` (the final outcome is no longer `failed`), `target_invalid` (step 5
fails now), and last `source_changed` (the digest differs). The preview shows the
reason of a `pending` proposal live.

**Approve**, under the proposal row lock, in this order: the proposal is visible (404),
the caller is not the call's agent or the coachee of a `coaching_task`
proposal (`422 coaching_actor_is_agent`), `pending` (`409 action_proposal_not_pending`), the revision
(`409 action_proposal_revision_conflict`), the body's `payload_hash` equals the stored
hash (`409 action_proposal_payload_changed`: the approver saw another payload), the
body's `source_transcript_revision_id` equals the pin (`409 action_proposal_pin_mismatch`),
not stale (`409 action_proposal_stale` with the `reason`), then the optional edit.

- **Nobody writes a coaching proposal about their own call.** An owner or admin passes
  the review-queue rule on their own call, so they can read a `coaching_task` proposal
  about it. Every write on it by the call's agent or the coachee (refresh, approve with
  or without a payload, reject, revoke) answers `422 coaching_actor_is_agent`, after
  visibility and before the status and revision checks, and writes only its `denied`
  audit event. The coachee is the stored payload's `agent_user_id`, so the person a
  proposal coaches stays refused after the call moves to another agent (the rule of
  `coaching_service._is_party`).
- Approve of a `coaching_task` proposal always runs the coaching create checks of step 3
  (open appeal, active coaching task, owner, due date, instruction) on the payload it
  approves: the stored one, or the edited one. So an approve with no payload answers
  like one with the unchanged payload, with the coaching codes.

- Edit-then-approve: the edited payload has exactly the stored keys. Only the editable
  fields may change (ticket `title`, `description`, `priority`, `due_at`; CRM `note`,
  `due_at`; coaching `owner_user_id`, `due_at`, `instruction`); any other changed value
  answers `422 action_proposal_field_not_editable`. A value equal to the stored one is
  not an edit: it keeps the stored value, never a normalized copy, and skips the field
  checks. So an approve with the exact stored payload is an approve with no payload: the
  same payload version, hash and idempotency key, and `edited` false. A changed value is
  checked as on create (title 1 to 120 characters, description and note 1 to 4000 of
  UTF-8 text, a priority of the list, a due date that fits in UTC, the coaching checks
  for the owner, the due date and the instruction); an unknown key, a missing key or a
  bad value answers `422 action_proposal_invalid`, an edited instruction that is empty,
  blank or too long included. The coaching create checks still run on the whole approved
  payload of a `coaching_task` (see above). The owner and the due date keep their
  coaching codes. An instruction equal to the stored one is not an edit: if the stored
  instruction is no longer valid, approve answers `422 coaching_instruction_required`,
  with or without the payload, as create and refresh do. The service stores the edit
  with a new hash and `payload_version + 1`, and approves that exact payload, all in one
  transaction.
- The approval writes one delivery request that copies the (possibly edited) payload,
  hash and version, the target, the source type, the source id, the criterion, the pin,
  the digest and the approver, with `idempotency_key` the sha256 of
  `workspace_id|proposal_id|payload_hash`.
- `refresh` rebuilds the payload from the current source (steps 2 to 5 again, with the
  same codes), takes new pins, and adds one to `payload_version`. A stale proposal is
  refreshed or rejected, never approved. A stale coaching proposal whose due date has
  passed can only be rejected (refresh fails the due-date check, approve fails the stale
  check); a person then creates a new proposal.
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

HOM-136 treats a violation of `uq_coaching_tasks_active_finding` at delivery as a refusal
of that request, not a crash: the approve of a `coaching_task` proposal and a create on
the coaching route are not atomic, so an active coaching task for the same finding can
appear between the approval and the delivery.

Approve locks the proposal row but not the source, so HOM-136 must not assume that the
request's `source_digest` still matches the source when delivery starts.

### D6. Routes, permissions and audit (PR 2)

| Method | Path | Permission | Success |
| --- | --- | --- | --- |
| POST | `/api/v1/action-proposals` | `action:propose` + CSRF | 201, the view |
| GET | `/api/v1/action-proposals` | `action:propose` | 200, `{items, next_cursor}` |
| GET | `/api/v1/action-proposals/{proposal_id}` | `action:propose` | 200, the view |
| POST | `/api/v1/action-proposals/{proposal_id}/refresh` | `action:propose` + CSRF | 200 |
| POST | `/api/v1/action-proposals/{proposal_id}/approve` | `action:approve` + CSRF | 201, with `delivery_request` |
| POST | `/api/v1/action-proposals/{proposal_id}/reject` | `action:approve` + CSRF | 200 |
| POST | `/api/v1/action-proposals/{proposal_id}/revoke` | `action:approve` + CSRF | 200 |

- Every route sits behind `require_module("integrations")` (ADR 0065) with the
  session-only workspace dependency `require_session_workspace` (it moved from the trends
  router to `security/context.py`, unchanged). API keys are refused: every bearer that
  looks like an API key gets `401 api_key_not_accepted` before any key lookup, in every
  module state, because an outbound write needs a person. Writes use
  `require_session_write_permission`, which adds the CSRF check. The refusal order is
  authentication, workspace, module, permission, CSRF, then the route's own checks.
- New permissions: `action:propose` (owner, admin, member) and `action:approve` (owner,
  admin). Neither is ever granted to an API key.
- Visibility: a proposal is visible when its call passes the caller's record scope and
  is not deleted. A `coaching_task` proposal whose review item still exists also needs
  the HOM-73 review-queue rule; once the review item is gone, the record scope alone
  decides, so the proposal can still be read, rejected or revoked. An invisible or
  missing proposal answers `404 action_proposal_not_found`, never 403.
- The view holds the ids, the target, the stored payload, its hash and version, the pin,
  the status fields, the evidence span ids of an action item source (oldest first), the
  live staleness of a `pending` proposal (`null` for other statuses) and the delivery
  request (`id`, `idempotency_key`, approver, time) or `null`. The list returns the same
  view without the payload and without the staleness check, oldest first, with the
  filters `status` (repeatable), `action_type`, `meeting_id`, `limit` (ASCII digits, 1
  to 100, default 50) and `cursor` (at most 512 characters, refused before any decoding).
  A bad parameter answers `422 action_proposal_invalid`.
- An id is 1 to 64 characters that the database can hold: no NUL byte and UTF-8 text.
  It is not a UUID pattern, because `target_external_record_id` is an outside system's
  id. A list or body id that breaks this rule answers `422 action_proposal_invalid`; a
  path id answers `404 action_proposal_not_found`. Both are refused before any query. A
  coaching `owner_user_id` that breaks it is no owner: on create and approve the coaching
  checks answer `422 review_assignee_ineligible` at their owner step, after the
  visibility checks, so the refusal does not tell whether the review item exists, and
  the value never reaches a query.
- Audit events `action_proposal.created`, `.refreshed`, `.approved`, `.rejected` and
  `.revoked`, target `action_proposal`. Their details hold only `proposal_id`,
  `meeting_id`, `action_type`, `source_type`, `payload_version`, `payload_hash`,
  `revision`, `reason`, `reason_code`, `stale_reason`, `delivery_request_id` and
  `edited`: ids, codes, hashes and versions only, never payload text and never source
  text. A 409 or 422 refusal of a visible proposal's refresh, approve, reject or revoke
  writes the same event type with outcome `denied` and the refusal code in
  `reason_code` (the audit log redacts any key named `code`). A 404 and a refused
  create write nothing.

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
