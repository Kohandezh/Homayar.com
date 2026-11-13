# ADR 0071: Cross-chunk speaker reconciliation

Date: 2026-11-13 · Status: Proposed (HOM-46, WP10-T03, parent HOM-14, features F30 F31 F33) · Builds on: ADR 0046

## Context

ADR 0046 transcribes a long call in chunks. Every chunk gets its audio plus
`overlap` seconds past each cut, so two neighbour chunks hear the same seconds
around a cut. A diarizer that also runs per chunk (HOM-44) returns anonymous
local labels per chunk: `speaker_1` in chunk 0 and `speaker_1` in chunk 1 are
not known to be the same person. Joining them by label name would invent
identities, and a caller that knows the speaker count (for example from a
two-channel layout) has no way to pass that knowledge down. F30 asks that
ambiguous clusters stay anonymous rather than become fabricated identities,
F31 that overlapping speech is not erased by the join, and F33 that spans stay
on the original timeline.

Diarization today runs once on the whole file and there is no chunked
diarization in the job yet (a real diarizer is HOM-44). So this ADR defines a
pure library, `backend/speech/speaker_reconcile.py`, with no model and no job
wiring. HOM-44 calls it.

## Decision

### 1. Input and output

`reconcile_chunk_speakers(chunks, *, settings, count_hint)` takes one
`ChunkDiarization` per chunk: the chunk `ASRChunkSpan`, the chunk's
`DiarizationResult` in chunk-local time, and optional per-cluster embeddings.
It returns one `SpeakerReconciliation`: a `DiarizationResult` in original time
with global labels, the cluster-to-global-label map, the accepted links, the
ambiguous matches it refused, and the count-hint decision. Everything is pure:
no I/O, no randomness, no clock. The same input always gives the same output.
Reordered segments or a reordered clusters mapping change nothing.

Input is checked up front: the chunk list is not empty, span indexes match
positions, owned ranges are contiguous within 1e-6, and the settings are
finite numbers in valid ranges. A `SpeakerCountHint` count is an int of at
least 1.

### 2. Time, unknowns and unavailable chunks

Original time is segment time plus `span.start_seconds`. For the output each
segment is clipped to its chunk's owned range. A segment that becomes empty is
dropped. Two speakers that overlap in time inside one chunk are both kept;
overlapping speech is never erased (F31). Output segments are sorted by start,
end and label, and `confidence` is carried unchanged.

A segment labeled `speaker_unknown` keeps that label. It is never matched,
never mapped in `labels`, and never counted as a speaker. Nothing is invented
for it (F30).

A chunk whose diarization is `UNAVAILABLE` gives no output segments and no
clusters, and no evidence is read across it. When every chunk is unavailable
the result is `UNAVAILABLE` with the issue code `diarization_unavailable`.
When only some are, the result stays `AVAILABLE` with the issue code
`diarization_partial` (not retryable), so the loss is visible. The result
provider is the first available chunk's provider, and `is_mock` is true when
any chunk was a mock.

### 3. Evidence

Two kinds of evidence, both between clusters of different chunks.

**Overlap** (adjacent chunks only). The shared region is
`[next.span.start_seconds, prev.span.end_seconds)` in original time, the
seconds both chunks heard. Each cluster's own segments are merged first, so
self-overlap is not counted twice. `co` is the seconds where the two clusters
both speak inside the region. Agreement is `co / min(a_seconds, b_seconds)` in
the region. Evidence exists only when `co` is at least `min_overlap_seconds`
(default 0.5 s). This needs the 2 s ASR overlap of ADR 0046, or a diarization
overlap of the same size, to have anything to read.

**Embedding** (any two chunks). The cosine of the clusters' embeddings. It
exists only when both clusters carry evidence, the `embedding_model` strings
are equal, the vectors have the same non-zero length, both norms are non-zero,
and every value is finite.

### 4. Pairs, matching and joining

For a pair, overlap evidence at or above `overlap_match` says "same speaker",
and cosine at or above `embedding_match` also does. Overlap at or above the bar
with cosine under `embedding_reject` is a conflict: the pair is never linked
and both clusters are recorded `CONFLICTING_EVIDENCE`. When both kinds agree
the pair is a candidate with the smaller score and evidence `BOTH`; with one
kind it is a candidate with that score and evidence `OVERLAP` or `EMBEDDING`.
A cosine between the two bars alone is nothing: no link and no record.

A candidate is accepted only when it is the mutual best: b is a's best
candidate in that chunk and a is b's best in the other, and on both sides the
best beats the runner-up in that chunk by at least `margin` (runner-up 0 when
there is none). A cluster with candidates but no accepted link is recorded
`NOT_MUTUAL` or `NO_MARGIN` with its candidates. Accepted links are then
joined with union-find in descending score order, ties by `(left, right)`. A
link that would put two clusters of the same chunk into one group is skipped
and both sides are recorded `CANNOT_LINK`; per-chunk clusters stay distinct
speakers. Unlinked clusters stay separate speakers.

### 5. The count hint

A hint comes from `CHANNEL_LAYOUT` or `OPERATOR` (trusted) or
`PROVIDER_ESTIMATE` (never applied). With no hint nothing changes. A trusted
hint equal to the group count is `ALREADY_MET`. A trusted hint above it is
`FEWER_THAN_HINT`: a group is never split and a speaker is never invented. A
trusted hint below the group count may merge groups, using pairs from
different groups and different chunks whose support (the larger of the overlap
agreement and the cosine, when the evidence exists) is at least
`count_support`. Conflicting pairs are excluded and cannot-link holds. Merges
run greedily in descending support. Each merge becomes a `SpeakerLink` with
evidence `COUNT_HINT`. If the group count reaches the hint the decision is
`APPLIED`; if it cannot, it is `NOT_SUPPORTED` and none of the merges are
kept. All or nothing.

### 6. Global labels

Each group of joined clusters gets `speaker_1`, `speaker_2`, ... in order of
its earliest output segment start, ties by the smallest member `ClusterRef`.
Groups without output segments come last, by smallest member. The map covers
every known cluster: a non-unknown label that appears in a segment of an
available chunk, even when clipping removed all of its segments.

## Consequences

- No migration. No schema change. No job wiring yet: `jobs/processor.py` still
  diarizes the whole file and does not call this library.
- The settings carry a `descriptor()` (`speaker-reconcile/1`) so a future
  checkpoint identity can pin them.
- The reconciliation report (links, ambiguous matches, count decision) is
  computed but not stored anywhere yet.
- Rollback is deleting the module and its tests; nothing else references them.

## Limits

- The thresholds (`overlap_match`, `embedding_match`, `embedding_reject`,
  `margin`, `count_support`, `min_overlap_seconds`) are defaults, not
  calibrated values. WP10 step 5 calibrates them on held-out annotated data.
- Overlap evidence needs the 2 s ASR overlap of ADR 0046, or a diarization
  overlap of the same size. A diarizer run per chunk without overlap hears no
  shared seconds and yields no overlap evidence.
- Agreement is a time ratio, not a voice comparison. Two speakers that talk
  over each other exactly in the shared region look identical to one speaker.
  The margin and the mutual-best rule are the guard rails.
- Cosine similarity is only as good as the embeddings. Until HOM-44 supplies a
  pinned model, embedding evidence exists only in tests.

## Deferred

- HOM-44 wires the library into the diarization stage and supplies embeddings.
- Calibration of the thresholds (WP10 step 5).
- Storing the reconciliation report with the diarization artifact.

## Alternatives considered

- **Join by label name across chunks.** Rejected: it fabricates identities.
  `speaker_1` of one chunk and `speaker_1` of another are different labels of
  different runs.
- **Force the speaker count from any source.** Rejected: a provider estimate
  is a guess. Only trusted sources may merge, and only with support.
- **Do the join inside the diarizer adapter.** Rejected: the chunk boundaries,
  the checkpoints and the merge all live in the job (as ADR 0046 decided for
  ASR). A pure library keeps the policy testable without a model.
