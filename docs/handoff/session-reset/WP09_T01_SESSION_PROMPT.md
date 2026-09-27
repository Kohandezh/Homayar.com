# Next-session prompt — WP09-T01 (bounded)

Prepared 2026-09-26 by the safe-recovery session from the tracked backlog and the code at
`main` `620f14c` plus branch `ci/r02-ffmpeg-backend-tests` (R-02). Paste everything below the line
into a new session. Facts are observations at that revision; re-verify them before relying on them.
Updated 2026-09-27 by Session A: R-02 is merged (step 3); the media decode now stops at its first
error-level diagnostic (`SESSION_A_BACKEND_CLOSEOUT.md`).

---

HOMAYAR — WP09-T01: CHANNEL METADATA, SILENT/DUPLICATED CHANNELS, TRUSTED PBX CHANNEL-TO-ROLE MAPPING

Work as one agent. Do not delegate or start parallel sessions. Status stays **NOT PRODUCTION READY**.
Do not add Co-Authored-By trailers. Push, merge and deployment need a fresh owner authorization.

## 0. Start

1. Work in a clean clone of `Kohandezh/Homayar.com` outside the owner's original checkout (for
   example `/Users/emperor/Dev/homayar-com`); never update `/Users/emperor/Documents/AI/Homayar.com`
   in place (see `docs/handoff/session-reset/PROJECT_STATE.md` §10). Product code is `Homayar/`
   (ADR 0033).
2. Read `AGENTS.md`, `docs/handoff/session-reset/{PROJECT_STATE,REMAINING_WORK,VALIDATION}.md`,
   ADR 0015 (quality coverage and degradation exposure), ADR 0016 (ingestion ledger),
   `Homayar/docs/security/authorization-matrix.md`, and the WP09 entries in
   `docs/planning/homayar-commercial-blueprint.md` (F25 row, WP09 section, acceptance table).
3. R-02 (ffmpeg in the backend CI job, `HOMAYAR_REQUIRE_MEDIA_TOOLS=1`) is merged: PR #5 and the
   Session A corrective pull request (`SESSION_A_BACKEND_CLOSEOUT.md`). Confirm that the latest
   `main` CI run is green before starting; do not base work on the stale branch
   `ci/r02-ffmpeg-backend-tests` (its last two commits were never merged).

## 1. What already exists (reuse, do not duplicate)

- `Homayar/backend/media/validation.py`: one bounded `ffprobe` call
  (`stream=index,codec_type,codec_name,sample_rate,channels,duration`) parsed into per-stream
  `AudioStream(channels, sample_rate_hz, …)`; `MediaValidationPolicy` limits
  (`min_sample_rate_hz=8000`, `max_sample_rate_hz=192000`, `max_channels=8`, `max_streams`);
  the result exposes the first audio stream's `channels` / `sample_rate_hz`; a bounded decode
  (`ffmpeg -xerror … -t N -f null -`) already exists for corruption checks. **Channel layout is
  not requested from ffprobe today** (add `channel_layout` to the same call; no second probe).
- `Homayar/backend/database/saas_models.py`: `channel_count` and `sample_rate_hz` columns already
  exist on the recording row (~line 974) and on `media_objects` (~line 1039); API schema fields
  in `Homayar/backend/schemas/meetings.py`. Extend these; do not add parallel columns.
- `IngestionRecord` (`ingestion_records`, ADR 0016) carries `source_type`
  (`IngestionSourceType`: `file_upload`, `sip_voip`, `mic_plus_system_audio`, …) and a free
  `source_provider` string. **Only `file_upload` has a producer.** There is no ingestion-source
  configuration entity today, so a per-source mapping needs a new tenant table (join the RLS list
  in `Homayar/backend/database/rls.py`).
- `Homayar/backend/routers/webhook.py` (Asterisk/3CX/FreePBX event normalizer) exists but is **not
  mounted** in `main.py`; it is not a telephony producer and must not be presented as one.
- Tests: `test_media_fixtures.py` synthesizes fixtures with lavfi (no committed media);
  `test_media_adversarial_api.py`; both carry the `ffmpeg` marker.

## 2. Scope (local, synthetic fixtures only)

1. Metadata per validated audio stream: channel count, **layout**, sample rate, codec, duration,
   from the existing probe.
2. Per-channel analysis on the existing bounded decode path: energy and a speech-activity share per
   channel; flags `silent_channel`, `duplicated_channels` (near-identical), `mono_as_stereo`.
   Thresholds are configuration with documented defaults; persist results with a detector version.
   An energy threshold is a heuristic signal, not speech detection quality; name it that way.
3. Channel→role mapping (e.g. `agent` / `customer`) per ingestion source, set only by an
   authorized administrator (a permission in the authorization matrix, audited; not an API-key
   scope unless justified in an ADR).
4. Attribution state per channel: `trusted` only when a mapping configured for that source exists
   **and** the channel is neither silent nor duplicated; otherwise `unknown`. Never infer a role
   from stereo, channel order, loudness, speech share, transcript content or a model.
5. Alembic migration with upgrade and downgrade; read-only exposure in the relevant API responses
   and as a quality signal consistent with ADR 0015, without changing how stages complete.
6. Out of scope: normalization/VAD derivatives (T02), ASR, diarization (WP10), speaker identity,
   console UI beyond not breaking a screen, the F25 accuracy gate.

## 3. Owner decisions needed (ask; do not assume)

- What identifies an "ingestion source" for the mapping while only `file_upload` exists: per
  workspace + `source_type` + `source_provider`, or a named source/connection entity (preferred
  for future PBX trunks)? Record the choice in an ADR.
- Which role may configure mappings (engineering default: workspace owner/admin), and whether a
  mapping change applies to already-ingested recordings (default: no; attribution is computed at
  ingestion and records the mapping version).
- The role vocabulary (default: `agent`, `customer`, `unknown`).
If the owner is unavailable, implement the engineering defaults behind a draft ADR and mark them
as proposals.

## 4. Acceptance criteria

- ffmpeg-generated fixtures: distinct stereo; stereo with one silent channel; identical channels;
  mono; 8 kHz telephony; more channels than allowed (still refused as today). Each yields the
  expected metadata and flags.
- Mapping: authorized role can set it, every other role gets `403`; changes audited; a second
  workspace can neither read nor use it (two-tenant negative test, also under RLS on PostgreSQL);
  a silent or duplicated channel downgrades its mapped role to `unknown`; no mapping → `unknown`.
- Migration passes `infra/ci/check-migrations.sh` (single head, no drift) and a
  downgrade/upgrade round trip on PostgreSQL.
- New tests carry the `ffmpeg` marker where they need binaries and run in CI (R-02); the CI
  summary (`-rs`) explains every remaining skip.
- No existing assertion weakened; full backend suite green on PostgreSQL as `homayar_app`.
- F25 (≥98 % separated-channel role accuracy on an agreed set) stays `blocked_external` (WP13 data).

## 5. Limits on claims

- Do not claim speaker identity, speaker-role accuracy or diarization quality. Channel flags are
  signal-level heuristics validated on synthetic audio only.
- Padyar AI Gateway: keep the name; its API contract has not been supplied — do not invent one,
  do not build an adapter, and never present mock-provider runs as model-quality evidence.
- "validated" in records means local scope with the commands you actually ran.

## 6. Tests (sequential; never edit source while its suite runs)

Targeted: new module(s), `test_media_validation.py`, `test_media_fixtures.py`,
`test_media_adversarial_api.py`, `test_authorization_matrix.py`, `test_row_level_security.py`,
`test_tenant_scope.py`. Then the full suite on a disposable PostgreSQL + Redis with
`HOMAYAR_REQUIRE_POSTGRES_TESTS=1 HOMAYAR_REQUIRE_MEDIA_TOOLS=1` (recipe: `VALIDATION.md` §5).
`ruff check` / `ruff format --check` on touched files. Stop and remove only services you started.

## 7. Records

WP09 `tasks.json` + `docs/planning/backlog.json` (T01 status, evidence note, F25 gate
`blocked_external`), a `PROGRESS.md` session entry, `PROJECT_STATE.md` §4/§5, `REMAINING_WORK.md`
R-05, `VALIDATION.md`. Refresh Graphify once at the end if source changed. Report in Persian.
