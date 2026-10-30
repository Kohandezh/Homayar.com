# ADR 0045: The conversation model on `meetings` rows

Date: 2026-10-30 · Status: Proposed (HOM-58, WP12-T01) · Builds on: ADR 0016, ADR 0021

All code paths are under `Homayar/backend/` at base `ac7cc1d`.

## Context

Homayar started with uploaded meeting recordings. It now takes calls, voicemails and
meeting-bot recordings too, and 18 later tickets need one word for all of them: a
conversation. They need to know what kind of conversation a row is, who started it, when
it happened at its source, and which record in another system it belongs to.

Every existing table, foreign key, row-level security policy, job, export and API uses
`meetings` and the meeting id. Clients and the Python SDK use those ids too.

## Decision

### D1. A conversation is a `meetings` row

A conversation is a row of `meetings`, with the same id. The table is not renamed and no
row is copied. A rename would touch every foreign key, every RLS policy and every client,
and it would add nothing a reader of the data needs. The code says "conversation" where it
means the new model; the table keeps its old name.

### D2. Five columns

Migration `6d4ec2d2e498` adds:

| Column | Meaning |
| ------ | ------- |
| `conversation_type` | What the conversation is: `call`, `meeting`, `voicemail` or `other`. NOT NULL, default `call`. |
| `direction` | Who started it, seen from the workspace: `inbound`, `outbound`, `internal`. NULL means not known. |
| `external_source_system` | The system the conversation comes from, for example a CRM or a phone system. |
| `external_conversation_id` | The conversation's id inside that system. |
| `started_at` | When the conversation happened at its source. `created_at` stays the upload time. |

`source` stays: it says how the media arrived. `conversation_type` says what the
conversation is. Existing rows got `voicemail` when `source` was `voicemail`, else `call`.

### D3. The external reference is not unique

`external_source_system` and `external_conversation_id` are both NULL or both set (a check
constraint). They are indexed but not unique. An id only means something inside its source
system, so the API never filters on the id alone (`422 external_source_system_required`).

The ingestion ledger (ADR 0016) already decides what happens when a source sends the same
event again. A second uniqueness rule on `meetings` could disagree with the ledger, for
example when a source re-sends a conversation after its purge. So the ledger stays the one
place that decides.

### D4. The source id is immutable

The reference is set once, when the conversation is created, and never changed. On the
ledger path, an ingestion identity with an `external_source_id` sets
`external_source_system = source_provider` and `external_conversation_id =
external_source_id` (in `services/ingestion_service.py`). Without one, both stay NULL.
`PATCH` does not accept either field (`422`, the request model forbids extra fields).

`PATCH` may change `conversation_type`, `direction` and `started_at`. An explicit null
clears `direction` or `started_at`. Each changed name goes into the `fields` of the existing
`meeting.updated` audit event.

### D5. Type from the ingestion source type

When a writer does not give a type, `conversation_type_for_source` in
`services/meeting_service.py` maps the ingestion source type:

| Ingestion source type | `conversation_type` |
| --------------------- | ------------------- |
| `voicemail` | `voicemail` |
| `meeting_bot` | `meeting` |
| every other type, or none | `call` |

A voicemail import is `voicemail` from the start. Its `started_at` is the received time the
caller declared, or NULL. The server never invents a time.

### D6. Purge keeps the metadata

A purge (ADR 0021 deletion flow, `services/conversation_purge_service.py`) removes the
content and clears the title. It keeps `conversation_type`, `direction`, `started_at` and
the external reference, as it keeps `duration_seconds` and `source`. These fields are not
content. The ledger keeps the same external id after a purge, because it needs it to
refuse a re-ingest of a purged conversation.

### D7. The `/conversations` API

`/api/v1/conversations` is the new name over the same rows and ids:

- `GET /conversations`: the same permission (`meeting:read`), workspace, record scope,
  soft-delete rule, paging and order as `GET /meetings`. Filters: `status`,
  `conversation_type`, `direction`, `external_source_system`, `external_conversation_id`.
  `GET /meetings` takes the same filters.
- `GET /conversations/counts`: the same answer as `GET /meetings/counts`.
- `GET /conversations/{id}` and `PATCH /conversations/{id}`: the same answer as the
  `/meetings/{id}` route, because they call the same handler.

Every `/meetings` route keeps its path, permission, status codes and fields. The meeting
responses add the five fields. The ingestion contract moves to 1.8.0 (additive).

## Consequences

- One id for a meeting and a conversation. Old links and SDK calls keep working.
- New writers (telephony, CRM sync) set the fields through `create_upload_job` or the
  ledger identity. They do not need a new table.
- Two names for one resource exist for a while. The console can move to `/conversations`
  when it needs to.

## Not built

- No uniqueness on the external reference (D3).
- No change to purge, RLS, permissions or the console.
- No rename of the table or of the `meeting_id` columns.
