<!--
Provenance: draft v0.1 generated on 2026-09-07 by `zai-coding-plan/glm-5.2` through the `opencode` CLI
from three inputs only (WP13/README.md; blueprint Sections 14-15 "Speech pipeline" to "AI evaluation and
Persian advantage"; the F25-F42/F80/F81 rows of REMAINING_WORK.md "Release gates"). Reviewed by Claude
(Fable 5.1): every numeric threshold cross-checked against those inputs, section order verified, no
customer, corpus or measured result invented. Not yet reviewed by a domain owner; every
"OWNER DECISION REQUIRED" line is open. Do not cite any threshold here as an achieved result.
-->

# WP13 — Authorized corpus and annotation protocol (draft v0.1)

**Status:** draft for owner review. **Authoritative inputs:** `WP13_README.md`, `blueprint_speech_ai_eval.md`, `release_gates_rows.md`. **Audited state:** F80 and F81 are Missing (per WP13_README). Every numeric threshold below is copied from the inputs and is **proposed, to be frozen after baseline**; no measured result is claimed. Persian text appears only inside normalization rules.

## 1. Purpose, scope and non-goals

WP13 delivers the authorized Persian-first evaluation corpus (F80, P0) and the regression / model-prompt promotion gate framework (F81, P1) that gate later model-dependent packages. This protocol covers two tasks only:

- **WP13-T01 — rights, sampling and corpus assembly:** rights/retention/access records, a sampling plan across accents, channels, intents and sensitive fields, and split/leakage-controlled manifests (README items 1 and 3).
- **WP13-T02 — annotation protocol:** verbatim/normalized text policy, speaker/role/overlap/timing labels, QA scorecard evidence labels, double annotation and adjudication (README items 2 and 4).

Non-goals: model training or fine-tuning claims; evaluator implementation (Section 10 is a specification for WP13-T05); production correction collection (kept separate, README item 7); any claim of achieved metrics. Corpus preparation starts immediately and must not block on the models that consume it (README).

## 2. Data rights, consent, access control and retention

One rights record per source organization must be completed, approved and stored in the corpus manifest before any of its recordings are ingested. Fillable template (placeholders only; no real names):

```text
rights_record_id:      RR-[NNN]
source_organization:  [ORG-PLACEHOLDER]
legal_basis:          [OWNER DECISION REQUIRED — Section 12, item 1]
consent_evidence_ref: [reference to signed agreement / consent artifact]
permitted_uses:       [e.g., annotation, internal evaluation, slice benchmarking]
prohibited_uses:      [e.g., external publication, re-identification, third-party training]
retention_limit:      [OWNER DECISION REQUIRED — Section 12, item 2]
access_roles:         [e.g., annotator, adjudicator, auditor, release-manager]
deletion_procedure:   [steps covering audio, derivatives, manifests, deletion ledger]
incident_contact:     [role mailbox, not a personal name]
```

Controls: originals remain immutable in access-controlled storage referenced by the manifest (blueprint; README requires evaluation reproducible from an access-controlled manifest); every read is logged; deletion propagates to derivatives, embeddings and the deletion ledger (blueprint data architecture).

OWNER DECISION REQUIRED: legal basis and consent-evidence standard per source; retention limits and deletion timelines; access-role definitions; whether any raw sensitive identifiers may be stored at all, or only placeholders (blueprint: real sensitive IDs only under the approved data policy).

## 3. Sampling plan

Input minimums (proposed, to be frozen after baseline): **≥20 Persian hours** of genuinely representative contact-center audio; a **frozen ≥5-hour held-out** subset; a **separate English/mixed-language** set; **≥20% double annotation**. Target counts are placeholders pending source inventory — no totals are asserted. Minority-accent and rare-event coverage must increase before any slice claims; small samples carry confidence intervals and explicit insufficiency warnings (blueprint).

| Dimension | Slices | Target |
|---|---|---|
| Accent/region | Tehran; other represented accents `[LIST]` | `[N_ACCENT_i]` h each |
| Register | formal Persian; colloquial Persian | `[N_FORMAL]`, `[N_COLLOQ]` |
| Channel layout | stereo two-channel (separated parties); mono/mixed | `[N_STEREO]`, `[N_MONO]` |
| Bandwidth/noise | 8 kHz telephony; wideband `[RATE]`; mobile/noisy | `[N_8K]`, `[N_WB]`, `[N_NOISY]` |
| Intent categories | `[LIST per TaxonomyVersion]` | `[N_INTENT_j]` per class; rare classes flagged |
| Call length | short `<[T1]`; medium `[T1]–[T2]`; long `>[T2]` | `[N_SHORT]`, `[N_MED]`, `[N_LONG]` |
| Sensitive fields | present (IDs/amounts); absent | `[N_SENS]`, `[N_NOSENS]` |
| Language | Persian; English/mixed (separate set) | `[N_EN_MIX]` in the separate set |
| Overlap-heavy | overlap share `>[X]%` of talk time | `[N_OVL]` |
| Invalid inputs | empty, music-only, noise-only, truncated, unsupported codec | `[N_INVALID]` as negative fixtures |

OWNER DECISION REQUIRED: final slice targets, length-bucket boundaries and the overlap threshold after inventory. Invalid/empty/music fixtures stay in corpus as negative cases: F26 requires empty/music/noise inputs not to fabricate transcripts.

## 4. Split policy and leakage prevention

Splits are train / development / test separated **by customer (organization), speaker and time** (blueprint). The frozen held-out set (≥5 h, proposed, to be frozen after baseline) is sealed at a recorded freeze date with a content-hashed manifest; it is used only for release evaluation and **never for prompt tuning** (blueprint), nor for calibration drills that leak its content.

Concrete identity/source leakage checks (F80: no split leakage detected):

1. Organization/customer identifiers must not appear in more than one split.
2. Hashed caller numbers and external PBX call IDs must not cross splits.
3. Audio fingerprints and checksums must be unique across splits.
4. Speaker-embedding near-matches across splits are flagged and adjudicated (same speaker ⇒ moved or removed).
5. Held-out recording timestamps must be separated from train by `[WINDOW]`.
6. A single-customer source must not appear in both train and held-out.

OWNER DECISION REQUIRED: the `[WINDOW]` value and whether single-customer sources are restricted to train.

## 5. Transcription guidelines

Keep three text surfaces separate (blueprint): raw (verbatim), display-normalized, search-normalized. Two annotation layers:

- **Verbatim layer:** exactly as spoken — disfluencies, repetitions, code-switches, numbers/dates/IDs/amounts in spoken form.
- **Normalized layer:** canonical Persian applying the rules below.

Normalization rules (normalized layer only):

1. **Letters:** Arabic ي (U+064A) → Persian ی (U+06CC); Arabic ك (U+0643) → Persian ک (U+06A9); glossaries cover Persian/Arabic character variants (blueprint).
2. **ZWNJ (U+200C):** enforce standard forms — می‌روم, کتاب‌ها, بزرگ‌تر; repair space-joined variants (می روم → می‌روم); never delete ZWNJ silently.
3. **Digits:** Arabic-Indic ٠–٩ (U+0660–0669) → Persian ۰–۹ (U+06F0–06F9); Latin-digit policy in the normalized layer: OWNER DECISION REQUIRED. Verbatim keeps the spoken/written form.
4. **Punctuation:** unify to the Persian set (، ؛ ؟) with documented Latin mappings; decimal/thousands separators canonicalized.
5. **Clitic spacing:** documented join/split rules for می‌, ـها, ـتر, ـترین.

Disfluency/repetition: fully retained in verbatim; the normalized layer may collapse them only per a documented policy `[REF]`. Unintelligible speech: tag `<unintelligible>` / `<inaudible>` with timestamps — never guess (blueprint: keep ambiguous words unknown). Code-switching/English terms: verbatim in Latin script with a per-segment language tag (blueprint: store language per segment; one global code cannot describe every multilingual call). Numbers/dates/IDs/amounts: verbatim as spoken, normalized per the rules; both verbatim and normalized number/entity accuracy are evaluated (blueprint ASR row).

PII placeholders (both layers): classes `[PERSON, PHONE, NATIONAL_ID, BANK_ACCOUNT, CARD, IBAN, EMAIL, ADDRESS, DOB, PLATE]` — OWNER DECISION REQUIRED on the final class list; original values stored only under the approved data policy.

## 6. Speaker, role, overlap and timing labels

- **Speaker cluster labels** (e.g., `SPK_01`) identify voice clusters only — "a diarization cluster is a voice cluster, not a named person or agent role" (blueprint).
- **Role labels:** `agent | customer | ivr | third_party | unknown`. `unknown` is always allowed and preferred over a confident-looking guess (blueprint).
- **Trusted-channel rules (stereo):** roles come only from verified PBX channel-to-role mappings; detect silent, duplicated and swapped channels, crosstalk and transfers; **stereo alone never establishes identity** (blueprint, F25). Mono/mixed: diarization clusters, with role resolution only via authenticated PBX agent IDs, CRM/call metadata or human review (blueprint).
- **Overlap:** label overlap intervals explicitly; collar `[COLLAR_MS = TBD]`, proposed, to be frozen after baseline; an exclusive display view must not erase overtalk evidence (F31).
- **Timing:** millisecond precision, mapped to original recording offsets through any resampling/chunking (blueprint).

## 7. QA scorecard and evidence labels

Criterion result values: `pass | fail | not_applicable | insufficient_evidence` (blueprint). Every non-NA result must cite ≥1 evidence span of the form `(start_ms, end_ms, speaker_label, revision_id)` anchored to an immutable transcript revision and original audio offsets (blueprint). Weighted denominators exclude approved NA criteria; missing evidence can never become a default pass (blueprint).

- **Rule-based criteria** (duration, literal keyword occurrence, exact script phrase presence): deterministic, computed on versioned rules.
- **Judgment criteria** (empathy, resolution quality, contextual compliance): evaluated with abstention when speaker/text/evidence quality is insufficient (blueprint).
- **Critical-violation confirmation:** contextual critical findings require precision ≥0.95 or human confirmation (F42 — proposed, to be frozen after baseline); negation, quoted speech, wrong speaker and poor ASR never create automatic confirmed violations (F42).

## 8. Double annotation, agreement and adjudication

At least **20%** of corpus hours (proposed floor, to be frozen after baseline) are double-annotated, stratified across Section 3 slices. Agreement per layer:

- **Text:** inter-annotator WER/CER on the normalized layer, plus verbatim number/entity spans.
- **Speakers:** DER/JER-style disagreement on shared diarization and role annotations.
- **QA criteria:** weighted kappa per criterion; score MAE against adjudicated scores.

Adjudication workflow: disagreements are listed per layer → an adjudicator (access role) resolves against audio → an adjudicated revision is created with `adjudicated=true`; the record stores both annotator IDs, layer, disagreement type, resolution rationale, revision IDs and date. Adjudicated outputs are the gold reference for F41 and F81 reports.

## 9. Corpus manifest and access manifest

Field-only JSON sketches (no data):

```json
{
  "rights_record": {
    "rights_record_id": "", "source_organization": "", "legal_basis": "",
    "consent_evidence_ref": "", "permitted_uses": [], "prohibited_uses": [],
    "retention_limit": "", "access_roles": [], "deletion_procedure": "",
    "incident_contact": ""
  },
  "recording": {
    "id": "", "source_ref": "", "channel_layout": "", "sample_rate_hz": "",
    "duration_ms": 0, "split": "", "slice_tags": [], "sensitive_flags": [],
    "checksum": "", "storage_ref": ""
  },
  "annotation": {
    "recording_id": "", "layer": "", "annotator_id": "", "version": 0,
    "adjudicated": false, "guidelines_version": "", "created_at": ""
  },
  "split_manifest": {
    "split_name": "", "recording_ids": [], "frozen_at": "",
    "manifest_hash": "", "leakage_check_results": []
  }
}
```

The access manifest binds roles to permitted splits/layers, with an audit log of every read; evaluation must be reproducible from it (README).

## 10. Evaluators to implement later (WP13-T05)

Specification only — no code. Common reporting fields for **every** gate: per-slice **sample size**, **uncertainty** (confidence interval or explicit insufficiency warning), **abstention rate**, **coverage**, plus latency and cost per candidate result (README item 6; blueprint). All thresholds are proposed, to be frozen after baseline.

| Gate | Metric (threshold, proposed, to be frozen after baseline) | Definition | Required inputs |
|---|---|---|---|
| F25 channels/roles | Verified separated-channel role accuracy ≥98%; detect-or-unknown rate for swapped/silent/duplicated/transfer channels | Share of channel-role assignments matching verified PBX mappings; stereo never establishes identity alone | Agreed stereo set, verified PBX mappings, role labels, channel-inspection flags |
| F26 normalize/VAD | Timeline drift; boundary speech loss >250 ms forbidden; fabrication count = 0 | Offset between normalized derivative and original timeline; speech loss at chunk boundaries on annotated regression fixtures; empty/music/noise yield no transcript; quality flags exposed | Synthetic and annotated boundary fixtures; negative fixtures; quality flags |
| F27 STT | Held-out WER ≤20%; critical supported slice ≤30%; CER reported; critical entities ≥95% exact match or routed to review | WER/CER under published Section 5 normalization; verbatim and normalized number/entity accuracy; English/mixed slices measured separately with supported scope stated | Frozen held-out set, normalized references, entity spans, English/mixed set |
| F30 diarization | Mono DER ≤15% incl. overlap; JER; speaker-count error | DER with published collar/overlap policy on the agreed mono contact-center set; ambiguous clusters stay anonymous/unknown | Agreed mono held-out set, speaker annotations, collar policy |
| F31 overlap | Overlap detection F1 ≥0.80 | F1 against annotated overlap intervals on an adequately annotated supported set; overlap survives storage/UI/export without erasure | Overlap-annotated subset; storage/UI/export round-trip artifacts |
| F33 alignment/confidence | ≥95% audited key-moment starts within ±1 s; in-bounds rate 100% | Cited span offset distribution; all spans within recording bounds; confidence origin and calibrated/uncalibrated status visible; ambiguous alignment routes to review | Audited key moments, evidence spans, confidence metadata |
| F34 acoustic metrics | Synthetic reconciliation exact to rounding; real talk/silence ratio MAE ≤5 pp; LLM-measurement count = 0 | Deterministic reconciliation of duration/silence/talk/overlap on fixtures; MAE on annotated real clips; no LLM-derived acoustic measurements | Synthetic fixtures, annotated real clips, signal metrics |
| F36 structured extraction | Intent macro-F1 ≥0.85; critical high-cost findings precision ≥0.95 or human confirmation | Macro-F1 over supported taxonomy with class imbalance report; every field has evidence/unknown semantics; contradictory fixtures abstain correctly | Versioned taxonomy, gold intents, contradictory fixtures |
| F37 summary/key moments | Atomic claim support ≥95%; structural citation validity 100%; invented severe allegations = 0 in release set | Audited atomic claims supported by cited evidence; summaries distinguish explicit outcome from inference | Release-set summaries, cited evidence spans, audit labels |
| F41 scorecards | Adjudicated QA kappa ≥0.75; score MAE ≤8/100 | Double-annotation agreement and MAE vs adjudicated QA; weights/applicability/NA/insufficient-evidence math passes fixed examples; versions reproducible | Scorecard versions, double-annotated QA, adjudications, fixed examples |
| F42 rules/compliance | Deterministic exact phrase rules; contextual critical precision ≥0.95 or review | Exact phrase rules deterministic; negation, quoted speech, wrong speaker and poor ASR fixtures never auto-confirm violations | Rule versions, regression fixtures, review records |
| F80 gold corpus | Manifests exist; ≥20 Persian hours; frozen ≥5 h held-out; ≥20% double annotation; leakage findings = 0 | Structural checks over rights/access/split manifests plus Section 4 identity/source checks | Rights/access/split manifests, annotations, leakage check runs |
| F81 AI promotion | Metrics reproduce within documented stochastic tolerance; promotion blocked without report and rollback manifest | Repeated evaluation-command runs agree within tolerance; promotion requires held-out quality/slice/cost report plus approved rollback manifest | Frozen release config, run artifacts, promotion logs |

OWNER DECISION REQUIRED: the critical entity list for F27; the stochastic tolerance definition for F81.

## 11. Promotion gate and rollback manifest outline (F81)

A candidate report (model or prompt change) must contain: candidate and incumbent version IDs with config hashes; evaluation data manifest hashes including the frozen held-out split; per-gate results against frozen thresholds with slice comparisons versus incumbent; sample sizes, uncertainty, abstention and coverage per slice; cost and latency measurements; provider/fallback policy compliance (no silent provider mixing — blueprint); the stochastic tolerance used; named approver sign-off.

**Blocks promotion:** any frozen threshold missed; missing slice or cost/latency report; metrics not reproducible within tolerance; missing approved rollback manifest (F81 acceptance). Production corrections are collected separately and never substituted for held-out evaluation (README item 7).

**Rollback manifest fields:** promoted version; previous version; promotion timestamp; trigger conditions (per-gate metric drops `[THRESHOLD]`); owner role; verification steps; artifact and report references.

OWNER DECISION REQUIRED: approver role, per-gate rollback trigger values, and the baseline freeze procedure after which thresholds are frozen.

## 12. Open owner decisions

1. Legal basis and consent-evidence standard per source organization (Section 2).
2. Retention limits and deletion timelines per source (Section 2).
3. Access-role definitions and membership (Section 2).
4. Whether raw sensitive identifiers may be stored, or placeholders only (Sections 2, 5).
5. Final PII placeholder class list (Section 5).
6. Latin-digit policy in the normalized layer (Section 5).
7. Slice target counts, length-bucket boundaries, overlap threshold and split time-window (Sections 3, 4).
8. Collar value for overlap/diarization evaluation (Sections 6, 10).
9. Critical entity list for the ≥95% exact-match gate (Section 10, F27).
10. Stochastic tolerance definition for metric reproduction (Sections 10, 11 — F81).
11. Approver role and per-gate rollback trigger values (Section 11).
12. Baseline freeze procedure and date after which thresholds are frozen (Sections 1, 11).
