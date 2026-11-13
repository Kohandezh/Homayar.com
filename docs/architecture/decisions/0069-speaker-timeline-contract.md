# ADR 0069: The speaker timeline contract

Date: 2026-11-13 · Status: Accepted (HOM-49, WP10-T06) · Parent: HOM-14 (WP10)

## Context

The console (HOM-264) needs to show who speaks when in a meeting: one lane per
speaker, the turns on each lane, and the places where speakers talk over each
other. It also needs to mark turns that need a person to look at them, and to
send speaker corrections. Before this decision the console would have had to
rebuild all of that from the raw transcript segments on every render, with no
shared rule for what an overlap or an unknown speaker is.

What is stored today: `TranscriptRevision`, `TranscriptSegment` (one row per
turn), `MeetingSpeaker` and `Meeting.duration_seconds`. Raw diarization
segments are not stored. HOM-45 will store them later.

## Decision

### D1. One read-only route

`GET /api/v1/meetings/{meeting_id}/transcript/timeline` under
`meeting:read`, on the insights router next to `GET .../transcript/revisions`.
It takes an optional `revision_id` and answers `404 revision_not_found` when
the id is not a revision of this meeting. A meeting with no revision answers
`200` with `revision: null`, empty `lanes`, `turns` and `overlaps`, and zero
totals, even when the meeting has `MeetingSpeaker` rows. The route is
read-only: it adds, changes and flushes no row.

### D2. No transcript text in the response

The response carries no transcript text. The console joins turns to text by
`segment_id` through the existing `GET /transcript`. So the timeline needs no
redaction view, writes no original-access audit row, and cannot leak text the
caller's transcript view would hide.

### D3. Overlap is measured from turns, and says so

`overlap_basis` is `"turns"`: overlap is computed from the turn time ranges
with half-open intervals `[start_ms, end_ms)`. Touching turns and zero-length
turns produce no overlap. An overlap interval is a maximal range where the
same set of two or more turns stays active. It is `cross_speaker` when the
active turns hold two or more distinct speaker keys, or when any of them has
an unknown key. Otherwise it is `same_speaker`, and a `same_speaker` interval
adds nothing to `overlap_ms` and gives no `overlap` review reason. HOM-45
adds overlap measured from the stored diarization segments as a new basis
without breaking this shape.

### D4. Unknown, anonymous, identified; no fabricated names

A lane is `unknown` when the speaker key is `speaker_unknown` or `unknown`.
It is `identified` when a `MeetingSpeaker` row exists and has a display name
or an `identity_type` other than `unknown`. Every other lane is `anonymous`.
No name is fabricated: `display_name` is only the stored value.

### D5. Turn attribution through the baseline walk

Each turn says why the revision gives it this speaker: `none` for an unknown
speaker; `person` when the baseline revision has a different speaker for the
same sequence, or none; `backfill` when the baseline source is `backfill`;
`diarization` when the baseline diarization is available; otherwise `none`.
The baseline of a revision is the revision itself, unless it is a correction:
then the chain of `parent_revision_id` is walked up to the first
non-correction revision. When the parent chain is broken (a parent is
missing) or loops, the baseline is the last revision reached. The revision's
`diarization_status` and `diarization_provider` in the response are the
baseline's values.

### D6. Review reasons

Each turn lists, in a fixed order, the reasons it needs a person:
`unknown_speaker`, `overlap` (it takes part in a `cross_speaker` interval)
and `outside_recording` (it ends after the known recording duration). A turn
with no reason lists none.

### D7. Honesty about confidence

`confidence` is `{"origin": "asr", "calibrated": false}`: the stored turn
confidence is the ASR segment confidence, and nothing is calibrated until
WP10-T05 (HOM-48) lands.

### D8. Correction readiness through the existing route

The `correction` block says whether the revision is correctable (it exists and
is current), the `base_revision_id` to send, and `speaker_choices`: every
`MeetingSpeaker` key of the meeting plus `speaker_unknown`. This is exactly
the set the existing correction route
`POST .../transcript/corrections` accepts as a new speaker. The timeline adds
no write path of its own.

### D9. No migration

The timeline is built from what is already stored. No new table, no new
column, no change to the processor, the providers, the correction route, the
transcript route or the published OpenAPI contract.

## Consequences

- The console (HOM-264) renders the timeline from one call and sends speaker
  corrections through the existing route with values the timeline promised
  are valid.
- The response shape is the contract HOM-264 builds on: field names, types
  and enum values are fixed.
- Turn-based overlap undercounts real crosstalk until HOM-45 lands; the
  `overlap_basis` field says which basis produced the numbers, so the console
  can show it.
- The timeline response has no text, so it never triggers the original-access
  audit and works the same under any redaction policy.
