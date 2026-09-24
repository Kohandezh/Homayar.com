# WP04 — Truthful states and evidence contracts

Status: VALIDATED IN LOCAL SCOPE on 2026-09-11 (T01, T04, T05: [ADR 0003](../../../architecture/decisions/0003-truthful-stage-states.md); T02, T03: [ADR 0014](../../../architecture/decisions/0014-transcript-revisions-and-analysis-runs.md); T06: [ADR 0015](../../../architecture/decisions/0015-quality-coverage-and-degradation-exposure.md)). Not production-validated: remote CI, the deployed stack and real providers have not run.

Completion dependencies: WP01.

## Implementation instructions

Features F23, F24. Owner: backend/ML. Dependencies: WP01; schema work coordinates with WP03.

1. Define run/stage states including queued, running, completed, partial, failed, unavailable and insufficient evidence, with typed reasons.
2. Introduce immutable transcript revisions, evidence spans, analysis runs and model/prompt/schema provenance.
3. Make every finding reference existing authorized turns and original audio offsets; reject malformed references.
4. Exclude mock providers from production configuration and preserve explicit mock labels in any development fixtures.
5. Remove random/default analytics from production result serialization; unavailable metrics remain unavailable.
6. Expose quality, coverage and degradation consistently through API, UI, exports and usage eligibility rules.

Done when injected provider failures cannot produce completed-looking invented scores and all displayed findings resolve to their exact source revision.

## Feature-level acceptance

### F23 — Honest stage states and mock exclusion (P0)

Current audited state: D unsafe; W partial. Target: Visible partial/unavailable; zero fake scores.

- [ ] Each forced missing/failing stage is represented in API/UI/export; unavailable diarization or failed QA cannot appear fully successful; production mock configuration is rejected and no random/default analytics are substituted.

### F24 — Evidence/revision/provenance schema (P0)

Current audited state: W partial. Target: Immutable spans and reproducible findings.

- [ ] 100% of returned citations resolve to an authorized immutable transcript revision and valid original audio range; correction/reanalysis preserves old provenance and explicitly supersedes dependent results.

## Completion evidence (local scope, 2026-09-11)

Against the eleven WP04 completion conditions of the master prompt:

1. Transcript revisions persist — `transcript_revisions` / `transcript_segments` keyed by revision, one current pointer, ORM immutability guards (product `10fd5f29`, migration `d4f8b2c6e9a1`).
2. Evidence survives reprocessing and corrections — `evidence_spans` bound to the immutable analysed revision; the API flags when that revision is no longer current (product `183c503d`, `f438eab7`).
3. Audio offsets exist where technically available — segment-level `start_ms`/`end_ms` from recognition are copied into every span; word-level offsets wait for WP10 alignment.
4. Findings reference evidence — summary overview, key points/decisions (`evidence_ids`) and AI action items carry `evidence_status` and citations; malformed provider references are rejected and counted (migration `e6a9c3d7f2b5`).
5. Human correction is auditable — `POST …/transcript/corrections` creates a child revision under the current row's lock; `transcript.revision.created` carries ids and counts only.
6. API reports quality state — `GET /meetings/{id}` (`quality`), `GET /meetings/{id}/quality`, shared view; console quality panel and evidence badges.
7. Exports preserve provenance — JSON carries revision, analysis provenance, citations and quality; TXT renders revision and Quality blocks; WebVTT carries a NOTE block.
8. No fabricated fallback — every degraded stage is a named state (`unavailable`, `insufficient_evidence`, `skipped`, `stale`); `verified` speaker attribution is never claimed.
9. Tenant isolation — the three new tables join the shared-content RLS policy (33 forced policies); foreign tenants answer `404`; the share ceiling is read-only and hides jobs/usage.
10. PostgreSQL tests pass — 68 + 63 + 11 focused runs as `homayar_app` on 16.15 across the three tasks; SQLite whole suite 216 passed / 31 skipped.
11. Migration validation passes — `check-migrations.sh` exit 0 for `d4f8b2c6e9a1` and `e6a9c3d7f2b5`; downgrade/upgrade round trips with seeded data.

Commands, results and reviewer notes per task: `docs/handoff/claude-fable-5.1/PROGRESS.md` (session 9, parts A–C).

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
