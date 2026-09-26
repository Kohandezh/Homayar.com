# Session A → Session B: backend API contract (WP08 scope)

Status: living document, owned by Session A (backend). Base: product
`42543aa` (WP08-T01). Every change below is **additive**; nothing existing is
renamed, removed or re-typed unless a "Breaking" note says so (none so far).

## Conventions (unchanged)

- Prefix `/api/v1`. Workspace selection: header `X-Workspace-ID` (server
  validates membership; the header never authorizes by itself).
- Browser sessions: HttpOnly cookies + `X-CSRF-Token` header on every write.
  API keys: `Authorization: Bearer <key>`; a key never carries owner-only
  permissions (`pii:original_access`, `legal_hold:manage`, `ai_policy:manage`).
- Errors: `{"error": {"code": str, "message": str, "request_id": str|null,
  "details"?: any}}`. Validation: `422 validation_error` with
  `details=[{field, message}]`. Permission: `403 permission_denied`; key scope:
  `403 api_key_scope_denied`. Cross-tenant ids answer `404` (never `403`).
- Timestamps are ISO 8601 with offset.

## Existing routes Session B relies on (unchanged semantics)

| Route | Permission | Notes |
|---|---|---|
| `GET /meetings/{id}/job` | `meeting:read` | `JobResponse`: `state`, `stage`, `attempt_count`, `max_attempts`, `failure_code`, `failure_message`, `failure_class`, `retry_after`, `deadline_at`, `replay_count`, `stages[]`, `dispatch`, `lease`. |
| `GET /meetings/{id}` | `meeting:read` | Meeting status: `uploading`, `uploaded`, `queued`, `processing`, `transcribing`, `diarizing`, `analyzing`, `completed`, `partial`, `failed`, `cancelled`. |
| `GET /meetings/{id}/transcript?view=original\|redacted` | `meeting:read` | Default view = redacted when the workspace enforces redaction, else original. `view=original` under an enforced policy needs `pii:original_access` (audited). |
| `GET /meetings/{id}/summary`, `/actions`, `/evidence`, `/export` | `meeting:read` | Same `view` rule; `AnalysisRunResponse` carries `input_view` (`original`/`redacted`) and `input_policy_digest`. |
| `GET/PATCH /admin/workspace-settings` | `workspace:manage` (+csrf on PATCH) | Changing `pii_redaction_enabled` / `pii_redaction_classes` additionally needs `pii:original_access`. |
| `POST /admin/jobs/{id}/replay` | `workspace:manage` +csrf | Requeues a dead-lettered job (`409 job_not_replayable`, `409 replay_limit_reached`, `429` entitlement codes). |
| `POST /privacy/subject-exports` | `pii:original_access` +csrf | ADR 0025. |

### Stage outcomes (unchanged enum meanings)

`stages[].state` ∈ `queued`, `running`, `completed`, `partial`, `unavailable`,
`insufficient_evidence`, `skipped`, `failed`. A meeting is `partial` when any
stage is degraded (`partial`/`unavailable`/`insufficient_evidence`/`skipped`).
`stages[].reason_code` is an open string set; the UI must render unknown codes
generically (show `reason_message`).

## Changes from Session A (WP08)

All additive. Nothing existing changes meaning.

### WP08-T01 — stage-run manifest provenance

`JobResponse.stages[]` gains `manifest_id` (string; `"unregistered"` in
development/test when no manifest matched), `manifest_version` (int) and
`manifest_digest` (64-hex). All three are `null` for runs before WP08 and for
stages an attempt never reached.

### WP08-T02 — workspace AI policy (ADR 0027)

Policy document (`policy`), one object per stage:

```json
{
  "asr":          {"endpoint_kinds": ["in_process", "private"], "regions": [], "manifests": [], "fallback": false},
  "diarization":  {"endpoint_kinds": ["in_process", "private"], "regions": [], "manifests": [], "fallback": false},
  "intelligence": {"endpoint_kinds": ["in_process", "private"], "regions": [], "manifests": [], "fallback": false,
                   "cloud_text": "redacted"}
}
```

- `endpoint_kinds` ⊆ {`in_process`, `private`, `cloud`}, no repeats; empty =
  stage disabled. `cloud` requires ≥1 `regions` entry
  (`^[a-z0-9][a-z0-9-]{1,39}$`, not `unknown`). `manifests`: manifest ids.
  `cloud_text` ∈ {`redacted`, `original`} exists **only** on `intelligence`
  (sending it on an audio stage is a 422). Unknown keys are a 422 (no
  credential or URL can be stored).
- The server canonicalises list order; compare policies by `digest`.

| Route | Permission | Request | Response / errors |
|---|---|---|---|
| `GET /admin/ai-policy` | `workspace:manage` | – | `AiPolicyResponse`: `version` (0 = built-in default, never stored), `source` (`default`/`workspace`), `policy_schema` (`homayar.ai-policy/1`), `digest`, `policy`, `reason`, `created_by_user_id`, `created_at` (null for version 0). |
| `PUT /admin/ai-policy` | `ai_policy:manage` (owner only, session + CSRF; never an API key) | `{"policy": {...}, "expected_version": int >= 0, "reason": "3-500 chars"}` | `200 {"changed": bool, "current": AiPolicyResponse}` (`changed=false` when the document equals the current one; no new version). `409 policy_version_conflict` with `details.current_version` when `expected_version` is stale: reload and retry. `422 validation_error` for invalid documents. `403 permission_denied` for admin/member, `403 csrf_failed` without CSRF. |
| `GET /admin/ai-policy/versions` | `workspace:manage` | – | `{"current_version": int, "items": [AiPolicyResponse...] (newest first, <=50), "presets": {"local_only": policy, "private": policy}}` |
| `GET /admin/jobs/{job_id}/policy-snapshots` | `workspace:manage` | – | `{"job_id", "items": [{attempt, policy_version, policy_digest, policy_document, text_privacy_digest, strict_manifests, stages: [{stage, provider, model, manifest_id, manifest_version, manifest_digest, manifest_status, endpoint_kind, region, endpoint, placement}], created_at}]}`; `404 job_not_found` for another workspace's job. |

UI guidance (Session B decides the design): show the current version and
source; editing is owner-only, so hide or disable it for others; always send
the `version` you displayed as `expected_version`; offer the presets as
starting points; explain that `cloud` sends data to a third party in the
listed regions.

### WP08-T03 — policy enforcement (ADR 0028)

No new routes. New values in existing open sets:

- `stages[].reason_code` (with `state="unavailable"` for diarization and
  intelligence, `state="failed"` for ASR): `stage_disabled`,
  `provider_not_permitted`, `region_not_permitted`, `model_not_permitted`,
  `license_not_permitted`, `language_not_supported`. `reason_message` is a
  safe sentence (no endpoint).
- `JobResponse.failure_code` for a refused ASR: the same codes (job `failed`,
  meeting `failed`, not retried, nothing billed).
- Retryable `failure_code="policy_changed"` (`job.stage="retry_scheduled"`):
  the policy or redaction setting changed during the attempt; the next
  attempt applies the new one.
- Meeting `partial` when analysis or diarization is refused: the transcript
  exists, the summary does not (`GET /summary` answers
  `404 summary_not_ready`, as for any run without analysis; use the job's
  stage reason to explain why). `AnalysisRunResponse.input_view="redacted"` also when
  only the cloud policy (not workspace redaction) required redaction.
- Snapshot `stages[]` entries gain `verdict` (`allowed`/`denied`), `reason`
  and `disclosure` (`none`, `audio`, `original_text`, `redacted_text`).

UI guidance: render a refused stage as "not permitted by the workspace AI
policy" with the reason, not as an error or an outage, and link owners to the
AI policy page.

### WP08-T04 — adapter contract and usage (ADR 0029)

No route or response-shape change. New failure code
`provider_contract_unavailable` (job `failed`, permanent) when a deployment
selects the Padyar AI Gateway, whose API contract has not been supplied; the
UI should present it as a configuration problem for the operator, not as a
customer error. Provider token usage is recorded internally (null = not
reported); there is no usage API for it yet.

### WP08-T05 — resilience and fallback (ADR 0030)

No route change. New values in existing open sets:

- Retryable `failure_code` (job `queued`, `stage="retry_scheduled"`,
  `retry_after` set): `provider_circuit_open` (the provider is failing; no
  call was made), `provider_rate_limited` (the per-minute request budget is
  used). `retry_after` now also honours the provider's Retry-After and may be
  later than the usual back-off.
- `job_deadline_exceeded` can now also end a running attempt (job
  `dead_letter`, `failure_class="deadline"`, replayable).
- `stages[].reason_code="fallback_used"` on a **completed** analysis stage:
  the primary failed transiently and an approved same-policy fallback
  produced the result; `reason_message` names the primary's failure and
  `manifest_id` names the fallback's manifest. Treat it as informational
  (like `checkpoint_reused`), not as a degradation.
- Policy snapshot `stages[]`: there can be more than one entry per stage;
  each has `candidate` (`primary`/`fallback`) and `rank` (0 = tried first,
  `null` = not eligible). New denial reasons: `fallback_not_approved`,
  `fallback_not_equivalent`.
- Policy document: `intelligence.fallback` (already present) is now
  effective; an owner turns it on to allow a configured same-policy fallback.

### WP08-T06 — egress denial (ADR 0031)

No route change. `failure_code="egress_not_permitted"` (permanent) means a
provider request was refused because no routing decision covered it; it
indicates a defect or a misconfiguration, never a user error. Endpoint
validation happens in operator configuration (manifests, base URLs), not in
any workspace-facing API.
