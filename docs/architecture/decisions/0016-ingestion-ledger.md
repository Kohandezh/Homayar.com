# ADR 0016 — Canonical ingestion identity and ledger

Date: 2026-09-11 · Status: accepted · Package: WP05-T01

## Context

Homayar accepts recordings from one path today (the console and API-key
file upload) and will accept them from many (desktop capture of microphone
and system audio, meeting bots, SIP/VoIP and PBX recordings, external
recording URLs, folder watchers, the public API). Before this decision the
only identity a delivery had was the upload job's peppered idempotency
digest: retries replayed the job, but nothing recorded *which source event*
produced a conversation, a changed payload under the same key was refused
as a generic conflict without a trace, and the same audio delivered twice
under different keys was simply processed and charged twice. The master
prompt (§5–§16) requires one canonical ingestion contract that every future
capture provider converges into, deterministic duplicate and conflict
behaviour, concurrency safety on PostgreSQL, immutable provenance and one
billing chain per accepted intent, without redesigning the WP06 processing
pipeline.

## Decision

1. **One identity, one ledger row.** `IngestionRecord`
   (`ingestion_records`) is a tenant-owned row per accepted capture event,
   identified by `(workspace_id, source_type, external_event_id)` — a
   database unique constraint (`uq_ingestion_event`), not an application
   check. `source_type` is the canonical enumeration `FILE_UPLOAD`,
   `MICROPHONE`, `SYSTEM_AUDIO`, `MIC_PLUS_SYSTEM_AUDIO`, `SIP_VOIP`,
   `MEETING_BOT`, `EXTERNAL_RECORDING_URL`, `OFFLINE_FOLDER_WATCHER`, `API`;
   `source_provider` names the adapter that delivered the event (`console`,
   `api`, `backfill` today) and is provenance, not identity — the same event
   id presented by the console and by an API key is the same event. The same
   event id in another workspace is an independent identity: no cross-tenant
   collision and no existence oracle.
2. **Payload digest.** `payload_hash` is the SHA-256 of a canonical JSON
   document (sorted keys, no whitespace, UTF-8) of the source type, the
   external source id, the media SHA-256 and size, the original file name,
   the title, the language and a provider `extra` map. The declared content
   type is deliberately excluded (clients label the same bytes differently;
   the media digest pins the content). The migration that backfills legacy
   uploads inlines this form and pins it to its revision.
3. **Three decisions, all audited with ids only.**
   - *Replay* (same identity, same payload): the existing row and its
     conversation/root job are returned (`202`, `idempotent_replay`); no
     conversation, job, storage object, usage reservation or dispatch intent
     is created again. A replay whose conversation was deleted is `410
     ingestion_target_deleted`.
   - *Conflict* (same identity, different payload): `409 ingestion_conflict`;
     the first event is never overwritten or reprocessed; the row's
     `conflict_count` / `last_conflict_at` advance under a row lock and an
     `ingestion.conflict` audit event carries ids and the count.
   - *Duplicate media* (different identity, same media digest as a *live*
     accepted ingestion of the workspace): the workspace's
     `duplicate_media_policy` decides — `allow` (default) accepts and links
     the new row through `duplicate_of_id`; `reject` answers `409
     duplicate_media` and creates nothing. The policy is a server-side
     workspace setting (`workspace:manage`), an enum, audited on change.
     Media identity is never inferred as business identity.
4. **Single-transaction claim.** Acceptance runs inside the upload
   transaction in this order: lock the workspace row (`FOR UPDATE`, which
   serialises the duplicate-media decision per workspace), evaluate the
   policy, insert the ledger row (`flush`: the unique index decides between
   concurrent claims — on PostgreSQL the loser blocks until the winner
   commits and then fails), create the conversation, recording, usage
   reservations, root job and outbox intent, link them on the row, audit.
   A loser rolls back and resolves the winner's committed row into a replay
   or a conflict. The route rate-limits only *new* events so retries are
   never throttled.
5. **Immutability.** An ORM guard refuses any update of an ingestion row
   except `conflict_count`, `last_conflict_at`, `meeting_id` and `job_id`
   (the last two are cleared by the database when their target goes; the
   ledger outlives the conversation). Corrections of provenance, if ever
   needed, will be an explicit audited workflow, not an update.
6. **Tenant boundary.** `ingestion_records` joins the tenant-only RLS policy
   set (forced, `homayar_app` posture). Reads (`GET /ingestions`,
   `GET /ingestions/{id}`, `ingestion` on the meeting detail and the upload
   response) require `meeting:read` and honour record scope: a restricted
   member sees the events they submitted and the events of the
   conversations their scope admits.
7. **Legacy uploads.** Migration `f7b1d3e5a9c2` backfills one `ACCEPTED`
   row per existing conversation that has a recording and a root job,
   with `source_provider = backfill` and the job's peppered idempotency
   digest as the external event id (the only identity those uploads had).
   A pre-ledger client key keeps replaying through the job's digest and
   answers with the backfilled row. The downgrade drops the table and the
   policy column and is documented as intentionally lossy.

## Amendment (2026-09-11, WP05-T02) — media validation and rejections in the ledger

8. **Validation pipeline, fixed order, same bytes → same answer.** Before any
   object is stored: filename/extension policy → declared MIME syntax → size
   bounds → *signature pre-check* (executables, archives, documents, images
   and bytes of a foreign container family are refused without spawning a
   process; the probe stays the authority for everything it recognises) →
   bounded `ffprobe` (fixed argv, no shell, probe/analyze/allocation caps,
   timeout) → container / codec / stream-shape / duration bounds →
   *plausibility floor* for compressed audio (`MEDIA_MIN_BYTES_PER_SECOND`,
   default 64: a container claiming hours of audio in a few kilobytes is
   refused; PCM containers are exempt because the demuxer derives their
   duration from the bytes present) → *bounded decode* of a prefix with
   `ffmpeg -xerror` (`MEDIA_DECODE_PROBE_SECONDS`, default 30; single thread,
   timeout, output discarded), which catches corrupted frames a header-only
   probe accepts → declared MIME against the detected content. An understated
   duration (more audio than the header claims) is not refused at upload; the
   worker's job deadline bounds it.
9. **Terminal vs transient.** Every refusal carries a stable code and a
   `terminal` flag. Terminal refusals are properties of the bytes (malformed,
   forged extension, unsupported codec, out of bounds, implausible,
   undecodable). Transient refusals are properties of the environment
   (`ffprobe`/`ffmpeg` unavailable or timed out, invalid probe metadata, file
   changed during inspection) and answer `503`.
10. **Rejections are ledger rows.** A terminal refusal of a staged payload is
    recorded as a `rejected` row under the same identity an acceptance would
    have had (reason code, media digest and size, provenance; no conversation,
    job, object or reservation), in its own transaction after the upload
    transaction rolled back, with an `ingestion.rejected` audit event. The
    check constraint now requires a reason on rejected rows (migration
    `a8c4e2f6d1b9`, lossless both ways). A retry with the same payload
    replays the recorded refusal without inspecting the bytes again; a
    different payload under the same event id is a conflict, exactly as for
    accepted events. Transient refusals and refusals before the payload
    exists (missing filename, empty upload, over the byte limit) are audited
    only and leave the identity free, so a retry can succeed. Rejected rows
    are never a live duplicate for the media policy.

## Consequences

- Every future capture provider implements the same two value objects
  (`IngestionIdentity`, `IngestionPayload`) and calls `accept_ingestion`
  with its own `create` callback; no provider gets its own pipeline.
- Billing safety follows from the claim order: one accepted ingestion is
  the only thing that creates a root job and its reservation chain (WP06
  unchanged; WP18 will settle from the same chain).
- Rejected ingestions (media validation failures) are not yet ledger rows:
  the `REJECTED` status and `reason` column exist for WP05-T02, which will
  record terminal and transient rejections without creating conversations.
- Only `FILE_UPLOAD` events are produced today; the other source types are
  identity contract only until their providers arrive (WP05/WP08+).
- The console receives the `ingestion` field on the upload and detail
  responses (types only); no ledger UI is built in this package.

## Validation

`backend/tests/test_ingestion_ledger.py` (11 tests): identity/payload
validation and canonical digest; replay, conflict and malformed-key
behaviour through the route with audit and count assertions; deletion
(`410`) and media becoming free again; the duplicate-media policy in both
modes with the invalid value refused; tenant-scoped reads, filters and
pagination with cross-workspace identity independence; team record scope;
API-key provenance and identity sharing; ORM immutability plus the unique
and check constraints; PostgreSQL only: the RLS probe as a non-bypass login
role, two-thread concurrent claims (identical → one accepted + one replay;
conflicting → one accepted + one conflict; reject policy → one accepted +
one refusal, one conversation each), and the migration round trip
(head → downgrade → upgrade) proving the backfill row matches the original
digest and that a pre-ledger key replays through it. Migration standard:
`check-migrations.sh` clean, explicit `downgrade -1` / `upgrade head` /
`alembic check` clean, 34 forced policies.
