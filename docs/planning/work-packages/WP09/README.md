# WP09 — Channel-aware speech and persistent inference

Status: NOT STARTED by this preparation task. Existing partial code must be reviewed and reused where suitable.

Completion dependencies: WP05, WP06, WP08, WP13.

## Implementation instructions

Features F25, F26, F27, F28. Owner: audio/ML. Dependencies: WP05/06/08 and WP13 corpus contract.

1. Inspect channel metadata, duplicated/silent channels, sample rate and trusted PBX channel-to-role mapping.
2. Create channel-preserving normalized derivatives, VAD intervals and reversible original-time mapping.
3. Implement selected Whisper model profiles and language policy; compare base versus stronger multilingual candidates.
4. Add long-call chunking/checkpoints, overlap deduplication and no-speech handling.
5. Move model lifetime to persistent warmed worker processes with memory-aware concurrency and bounded batching.
6. Add CPU/GPU readiness/preflight and route work only to qualified workers.
7. Measure Persian/English/mixed and noisy/stereo/mono accuracy, latency, GPU memory and actual cost under admitted load.

Done when speech meets the agreed corpus gates and repeated jobs reuse warm models without channel loss or duplicated boundary text.

## Feature-level acceptance

### F25 — Channel-aware speaker attribution (P1)

Current audited state: Missing. Target: Trusted PBX channels before diarization.

- [ ] Verified separated-channel role accuracy ≥98% on the agreed set; swapped, silent, duplicated and transfer-affected channels are detected or marked unknown; stereo alone never establishes identity.

### F26 — Audio normalization, VAD and quality checks (P1)

Current audited state: Partial. Target: Preserve channels and original time offsets.

- [ ] Original timeline mapping remains correct through resampling/chunking; no speech loss >250ms at boundaries on annotated regression fixtures; empty/music/noise cases do not fabricate transcript; quality flags exposed.

### F27 — Persian and English production STT (P1)

Current audited state: D/W MVP. Target: Qualified local and external ASR adapters.

- [ ] Persian held-out WER ≤20%, critical supported slice ≤30%; CER and entity accuracy reported; critical entities ≥95% exact match or routed to review; English/mixed slices measured separately and supported scope stated.

### F28 — Persistent GPU workers and bounded batching (P1)

Current audited state: Missing. Target: Warm models; measured throughput/fairness.

- [ ] Ten sequential jobs reuse the warmed model per configured worker process; configured concurrent load stays within memory budget without OOM; queue fairness and latency measured on the exact qualified hardware.

## Completion evidence to record

Record changed files, exact commands/results, migration behavior, security/quality checks, remaining limitations and reviewer decision. Do not mark complete from a version label or mock demo.

See [canonical blueprint](../../homayar-commercial-blueprint.md) and [execution protocol](../../../handoff/claude-fable-5.1/EXECUTION_PROTOCOL.md).
