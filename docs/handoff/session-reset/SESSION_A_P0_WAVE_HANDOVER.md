# Session A — P0 wave handover (2026-09-28)

For the next session (Claude web / cloud). Read this, then `Homayar/ReportAppvisionProgress.md`
(the operational index; §H = next tasks). `Homayar/AppVision.md` is the product contract.

## 1. Where things are

| Item | Value |
| --- | --- |
| Repository | github.com/Kohandezh/Homayar.com (monorepo, product in `Homayar/`) |
| main | `12a0dab` (includes PR #6 fix `88073c8`, green CI run 36353703289, and PR #7 research docs) |
| Work branch | `feat/backend-p0-wave` — pushed; this handover is its last commit |
| Open PR | https://github.com/Kohandezh/Homayar.com/pull/10 — **NOT merged** |
| Why not merged | CI run 36498108690 never started: GitHub says "recent account payments have failed or your spending limit needs to be increased". Owner must fix billing, then re-run the workflow. No code verdict yet. |
| Migration head | `a1c3e5f7b9d2` (on the branch; main is still `f5b1d7c3a9e2`) |
| Product status | NOT PRODUCTION READY |

## 2. What was done in this session

### 2.1 Closeout of the red main (PR #6, merged)
- Root cause of red main `1b34472`: the redelivery transport drill accepted only
  `already_running`; the claim uses `FOR UPDATE SKIP LOCKED` so a redelivered copy can
  answer `busy`, and a 4 s lease could lapse (`lease_expired`) on a slow runner. The drill now
  accepts the inert answers, uses a 30 s lease and prints worker log tails on failure.
- Media decode diagnostics: ffmpeg 6.1.1 exits 0 on some corrupt files; the decoder's stderr is
  now watched and the process is killed at its first diagnostic (bounded memory/disk).
- Details: `SESSION_A_BACKEND_CLOSEOUT.md`, `VALIDATION.md`.

### 2.2 AppVision imported
`Homayar/AppVision.md` was only an untracked file in the original checkout; copied unchanged
and committed (`2aa134d`).

### 2.3 Priority-0 wave (PR #10, open) — 5 parallel workers + integration

| Area | Code | What exists | Status |
| --- | --- | --- | --- |
| Voice runtime (X21/V02/V05) | `backend/voice/`, `routers/v1/voice.py` | Call sessions + state machine, capacity admission (global/node/workspace, idempotent by external call id), fencing generations, node-loss and slot-lease recovery (Celery beat `voice-recovery`, service scope `voice_recovery`), drain, metrics, load harness `python -m backend.voice.loadtest --calls 250` | PARTIAL — control plane only, no SIP/WebRTC media |
| Local AI runtime (F20/F21/F74) | `backend/providers/` (`local_tts.py`, `artifacts.py`, `offline_profile.py`, `hardware.py`, `local_llm.py`) | TTS interface + Piper CLI and private-HTTP adapters, offline artifact sha256 checks, profile-O evaluation (every stage local or explicitly unavailable), hardware profile + qualification records, `model_preflight --offline` | PARTIAL — no model qualified on hardware |
| Persian speech (F26/F27/F29/F36/F72) | `backend/speech/` | Normalization (fa/en/az), Jalali⇄Gregorian, spoken numbers, relative dates/times with explicit ambiguity, entities, telephone ffmpeg profile + PCM quality flags, language/dialect registry (all PLANNED), WER/CER + qualification gate | PARTIAL — library not yet wired into the processor |
| Locations (owner P0) | `backend/locations/`, `routers/v1/locations.py` | Gazetteer (31 provinces, 42 cities, approximate centroids), extraction from Persian/English text, validation, haversine ranking with filters/open-now, `needs_clarification` instead of guessing, CRUD with audit | PARTIAL |
| Voicemail (owner P0) | `backend/voicemail/`, `routers/v1/voicemail.py` | Import through the existing upload/ingestion/job path (source type `VOICEMAIL`), versioned rule classifier (category/intent/topics/priority with reasons), idempotent follow-up ActionItem + urgent Notification, search, analytics; hooked in `jobs/processor._run_voicemail_hook` after the job outcome | PARTIAL |

Integrator changes (shared files): `config.py` (TTS, offline, voice settings), `security/context.py`
(`require_platform_operator`: voice node routes need `User.system_role="operator"`, never an
API key), `database/model_registry.py` (all models for Alembic/create_all), `database/rls.py`
(new tenant tables; voice node/lock/slot tables deployment-wide), `tenant_context.py`,
`celery_app` + `jobs/tasks.py` (voice recovery task), `main.py` (routers mounted), migration
`a1c3e5f7b9d2` (also widens `ck_ingestion_source_type`), `docs/security/authorization-matrix.md`
(new routes; `+operator` marker in `test_authorization_matrix.py`).

## 3. Evidence (local only — no remote CI yet)

- Migration on PostgreSQL 16: upgrade → `alembic check` clean → downgrade → upgrade OK; RLS
  enabled on the tenant tables.
- Full suite on PostgreSQL (no Redis): **1042 passed, 2 failed, 50 skipped** (skips: live S3,
  Padyar contract, Redis transport drills, one SQLite-only drill).
  - Failure 1 fixed: `test_voice_api` role helper ran without tenant context (RLS made it a no-op).
  - Failure 2 **unidentified** (the summary line was truncated). First action for the next
    session: run the suite with `-rf` or read the PR #10 CI log once billing is fixed.
- New test files on PostgreSQL: voice/locations/voicemail/local-AI 62 passed after the fix.
- Voice load (SQLite, 250 concurrent, limit 200): exactly 200 admitted; p50 ≈ 0.6 s, p99 ≈ 1.1 s
  (burst queueing; ~4–5 ms per admission). PostgreSQL not measured.

## 4. Known gaps / decisions for the owner

- TTS is not a policy stage in `AiPolicy` (changing it alters stored policy digests).
- Voice permissions reuse `meeting:*`; proposed dedicated `voice:*` permissions.
- Workspace call limits have a service function but no operator route.
- `locations` and `voicemail` carry small local normalizers; consolidate onto `backend/speech`.
- Voicemail status never becomes `processing` (no job-start hook).
- External: real PBX/SIP + 200-call infrastructure, model weights/hardware, Persian annotated
  telephone corpus, Homayar AI Gateway contract, live S3, credential owners.

## 5. Next steps (in order)

1. Owner fixes GitHub billing → re-run PR #10 workflow → fix failures → merge (merge commit,
   no bypass) → verify main CI.
2. Identify/fix the 2nd local failure if CI shows it.
3. Continue `ReportAppvisionProgress.md` §H (wire `speech` into processing, voice media bridge
   interfaces, PG load measurement, voice permissions, capability registry X01/X02, WP18, WP24…).

## 6. Rules to keep

- No `Co-Authored-By` trailers. Persian, concise reports to the owner.
- Never modify `/Users/emperor/Documents/AI/Homayar.com` (original checkout), its nested repos,
  `.git/session-c-backups/`, or `~/Backups/homayar-20260926-safe-recovery/`.
- No production deployment, credential rotation, force-push, or bypassing required checks.
- Local-only leftovers: isolated clone `~/Dev/homayar-com-session-a`, a local PostgreSQL 16 in
  `/tmp/claude/p0pg` (port 55432; stop with `pg_ctl -D /tmp/claude/p0pg stop`).
