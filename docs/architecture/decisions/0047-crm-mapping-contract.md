# ADR 0047: The CRM mapping contract and the reconciliation queue

Date: 2026-10-30 · Status: Proposed (HOM-88, WP16-T04, F64) · Builds on: ADR 0016, ADR 0045

All code paths are under `Homayar/backend/`. The number 0046 was taken on `main` when this
ADR was written.

## Context

Customers run a CRM or a ticket system next to Homayar, for example Odoo. That system knows
which customer, ticket and agent a call belongs to, and how the call ended. Homayar must
attach those facts to the right conversation (a `meetings` row, ADR 0045) without guessing.
A wrong join links one customer's call to another customer. A merge by phone number joins
two people who share a phone. Both are hard to undo.

Owner decision D08 (HOM-88, 2026-10-21): stable external ids only; an ambiguous mapping goes
to a human, never to a fuzzy match; an inactive upstream record is respected, never
recreated or reactivated.

## Decision

### D1. Stable external ids, no merge across systems

A source system is named by a slug, for example `odoo`. Each workspace has one mapping
profile per source system (`integration_mapping_profiles`). It says where the ids sit in the
record payload (`field_map`, dotted paths), how outcome codes map to Homayar outcomes
(`outcome_map`) and how agent ids map to Homayar users (`agent_map`). A map key with outer
spaces is refused (`invalid_outcome_map`, `invalid_agent_map`): extracted values are trimmed,
so such a key could never match.

A customer, ticket or agent is an `external_records` row, known only by
`(workspace, source_system, record_type, external_id)`, unique in the database. The same id
in two source systems, or as a customer and as a ticket, is two rows. Nothing is merged
across systems. No name, phone or email is stored.

### D2. The phone guard

A profile whose `customer_id` path ends in a phone-like field is refused with
`422 phone_identity_forbidden`: the last segment, lowercased, contains `phone`, `mobile` or
`msisdn`, or is `tel`, `telephone`, `ani`, `cli`, `caller_id`, `callerid` or
`caller_number`. A phone number is not an identity: people share phones and change them
(F64). The guard covers the customer id only; a ticket or agent id may have any name.

### D3. The raw payload is never stored

Only the mapped values are kept, in `integration_inbound_records.extracted`: the
conversation id, the customer, ticket and agent ids, the outcome code and the two active
flags. `payload_hash` (SHA-256 of the payload with sorted keys and compact separators)
tells a replay from a conflict. A CRM payload carries names, phone numbers and notes; none of
them reaches a table, a log or an audit row. Audit details hold Homayar ids, field names and
codes only.

### D4. One ledger row per received record

A record's identity is `(workspace, source_system, event_id)`, unique in the database. The
same payload again is a replay (`200`, the stored answer). Another payload is a conflict
(`409 inbound_record_conflict`); the stored row is kept, its `conflict_count` grows and an
`integration_record.conflict` audit event is written. Two concurrent inserts of one identity
are decided by the unique index, never by an application check alone.

### D5. Nothing is guessed: the reconciliation queue

The conversation is found by `external_source_system = profile.conversation_source_system`
and `external_conversation_id`, live rows of the workspace only. Never by phone, title or
time. Each entity of a record is then handled on its own. These cases open an
`integration_reconciliation_items` row for a human, and nothing is linked for that entity:

| Reason | When |
| ------ | ---- |
| `ambiguous_conversation` | two or more live conversations have the same external reference |
| `conversation_not_found` | no conversation appeared before the record's window ended |
| `changed_id` | the conversation already links another value for this type; a link is never overwritten |
| `unknown_agent` | the agent id is not in `agent_map`, or its user is not an active member |
| `unknown_outcome` | the outcome code is not in `outcome_map` (exact match, no case folding) |
| `inactive_record` | the customer or ticket is inactive upstream |

Clean entities of the same record still link. An open item of the same case (conversation,
system, type, reason, value) is reused, not opened twice. A person with `workspace:manage`
resolves an item with `link`, `keep_current` (only for `changed_id`) or `dismiss`. A link
made on a human decision records `linked_by_user_id`. Resolving never changes
`meetings.external_*` (ADR 0045 D4).

### D6. Delayed records and the sweep

A record can arrive before its conversation is uploaded. It waits as `pending` until
`expires_at` (received time plus the profile's `late_record_window_seconds`, 1 hour to 30
days, default 7 days). The sweep `backend/jobs/crm_reconciliation.py` runs from Celery beat
every 300 seconds. It applies a record whose conversation is now there, opens
`ambiguous_conversation` for two matches, and opens `conversation_not_found` once the window
ended. It finds pending rows under the read-only `crm_reconciliation` service scope, then
changes each one in a transaction bound to the row's own workspace, holding the row with
`FOR UPDATE SKIP LOCKED`. Writes to one conversation's links and items are serialised with a
transaction advisory lock, so two sweeps at once never link twice or open an item twice.

Turning a profile off blocks new records only. The sweep still settles the records already
received, with the profile's current maps.

There is no hook in the ingestion path. A hook would add CRM work to every upload, and a
retry path that runs anyway would still be needed for uploads that come later. The sweep is
the one retry path.

### D7. Inactive upstream records

`external_records.is_active` copies what the record says (`customer_active`,
`ticket_active`). When a record says nothing, the stored value stays. Homayar never sets a
record active on its own and never creates a replacement. A link to an inactive record is
not made automatically (`inactive_record`); a person may still link it, and it stays
inactive.

### D8. The agent link does not change record scope

An agent link stores the Homayar user the agent id maps to (`agent_user_id`). It never
changes `meetings.agent_user_id`, which decides who may see a conversation (record scope).
A CRM must not change authorization. For the same reason a team-scoped caller may not post
records (`403 record_scope_restricted`): it could link conversations it cannot see.

## Consequences

- A CRM connector (HOM-256) only has to send records; mapping and reconciliation are shared.
- Every unclear case is visible in one queue, and nothing wrong is linked silently.
- Admins must work the queue. A long-unresolved queue means the profile or the CRM data
  needs fixing.

## Not built

- No write-back to any CRM (HOM-135, HOM-256).
- No Odoo or PBX specifics (HOM-256, HOM-87).
- No webhook delivery (HOM-89) and no dead-letter admin views (HOM-90).
- No change to `meetings` columns, record scope, purge or the ingestion write path.
- No console UI.
