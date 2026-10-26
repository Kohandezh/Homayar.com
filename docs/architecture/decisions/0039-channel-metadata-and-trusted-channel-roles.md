# ADR 0039: Channel metadata and trusted channel-to-role mapping

Date: 2026-10-22 · Status: proposed (HOM-37, WP09-T01, parent HOM-13, features F25 and F26) · Builds on: ADR 0013, ADR 0015, ADR 0016, ADR 0017, ADR 0036

## Context

A call recording often has two channels: one for the agent and one for the customer. That
split is valuable, but only when it is true. A stereo file can carry the same signal twice,
one silent channel, swapped channels or a mixed call. Before this change the ingest path
read only the channel count and the sample rate of the first audio stream. Nothing looked
at the channels, and nothing could say which party a channel holds.

Parent acceptance (F25): swapped, silent, duplicated and transfer-affected channels are
detected or marked unknown, and stereo alone never establishes identity.

The owner had to decide three things. The engineering defaults below were proposed and are
recorded here as proposals.

## Decision

1. **Source identity.** A mapping belongs to an ingestion source:
   `(source_type, source_provider)` inside one workspace, the identity of the ingestion
   ledger (ADR 0016). A mapping for one source never applies to another source. A named
   source or connection entity (one per PBX trunk) is a later step, when a telephony
   producer exists.
2. **Who configures.** Only `workspace:manage` (owner and admin) reads, sets or removes a
   mapping, through `GET/PUT/DELETE /api/v1/admin/channel-role-mappings`. Writes need a
   session and CSRF. API keys never carry `workspace:manage` (ADR 0013). Every set or
   removal is audited as `workspace.settings.updated` with the actor, the source, the
   version and digest, the previous version and digest, and the channel roles (ADR 0036).
3. **Vocabulary.** `agent`, `customer`, `unknown`. Only `agent` and `customer` can be
   mapped, each at most once per source. `unknown` is the result when trust is missing.
4. **No retroactive change.** Attribution happens once, at ingest, with the mapping read in
   the ingest transaction. The per-recording record keeps the mapping's version and digest.
   A later mapping change or removal never rewrites a past recording.
5. **Trust rule.** A channel is `trusted` with its mapped role only when a mapping exists for
   the recording's source, the file has every mapped channel, the analysis ran, and the
   channel is mapped, not silent and not part of a duplicated pair. A mono file is never
   trusted: one channel holds both parties. Otherwise the role is `unknown` with a reason
   code. A role is never inferred from stereo, channel order, loudness, speech share,
   transcript content or a model.
6. **Signal analysis.** One bounded ffmpeg decode per ingest (default 30 s window, 10 s
   timeout, byte cap, one thread) measures each channel: level, share of active 20 ms
   frames, silence, and duplicates by sample correlation within a small lag window (a
   scaled, inverted or slightly delayed copy is the same signal). It is a signal-level heuristic, not speech detection. It never refuses
   media: a failure gives `status: "unavailable"` and every channel stays `unknown`.
   Narrowband means a sample rate below 16 kHz; such files are accepted as before.
7. **Storage.** The mapping lives in the existing `Workspace.settings_json`, so it needs no
   migration. The per-recording record goes to a write-once JSON column
   `media_objects.channel_inspection` (owner decision, migration `a7c3e9f5b2d1`). The column
   exists and is set once, on the ORIGINAL media object before its insert. It is a content
   column, so the ORM guard refuses a later change. All storage sits behind
   `backend/media/channel_store.py`, and meeting detail fills `channel_inspection` from it.
   Objects registered before the migration keep `null`. The audit log is not used as a
   result store.
8. **Exposure.** Read-only, as a new `channel_inspection` field on
   `GET /api/v1/meetings/{id}`. Upload and voicemail responses, stage completion and the
   quality assessment (ADR 0015) do not change.

## Consequences

- An administrator can declare which channel carries which party, and a recording shows
  per channel whether that declaration was trusted and, if not, why.
- `file_upload:console` and `file_upload:api` are weak identities: anyone who may upload in
  the workspace can send a file whose channels the mapping then trusts. The mapping is
  meant for real telephony sources; for uploads it states how the workspace's own
  recorder writes its files.
- The F25 accuracy gate (at least 98 % role accuracy on an agreed set) stays
  `blocked_external` until WP13 data exists. Nothing here claims role accuracy, speaker
  identity or diarization quality.
- Thresholds are configuration and are recorded in each record, so a later change is
  visible per recording.
- HOM-38 (normalized derivatives, VAD) reads the record shape documented in
  `Homayar/backend/media/README.md`.

## Alternatives considered

- **Infer roles from channel order or loudness.** Refused: it is exactly the guess F25
  forbids.
- **Run the analysis as a job stage.** Refused: the record must exist when the recording is
  created so it can name the mapping valid at ingest; a stage runs later and again on retry.
- **Keep the record in the `media.registered` audit row.** Refused by the owner: the audit
  log is not a result store.
- **A new tenant table for mappings.** Not needed now: `settings_json` already exists,
  is tenant-scoped under RLS and is written only through the admin route.
