# ADR 0059: Report exports and provenance

Date: 2026-11-13 · Status: Proposed (HOM-137, WP24-T05, F70)

## Context

F70 asks for PDF, CSV and JSON exports of the call report next to the existing
transcript exports, with version and provenance and with the access controls the
product already enforces. The call report (`GET /meetings/{meeting_id}/report`)
already renders every text in the caller's privacy view and hides records the
caller may not see. The transcript export already exists for `txt`, `json`,
`srt` and `vtt`.

## Decisions

1. **One report builder (D1).** The body of `GET /report` moved into
   `build_call_report(db, context=..., meeting=..., view=..., current=...,
   settings=...)`. It receives the `MeetingView` the route already built with
   `meeting_view` under its own surface, and never builds a view itself. So
   one request writes at most one `transcript.original_accessed` audit
   (`report` for the read, `export` for the export), and the export prints
   exactly what the read shows. It also receives `current`, the current
   transcript revision row the route already read. The export checks
   `revision_id` against that same row, so the pin check, the
   `X-Homayar-Transcript-Revision` header, the provenance and the evidence
   statuses all come from one read and cannot disagree.
2. **The route (D2).** `GET /meetings/{meeting_id}/report/export` takes a
   required `format` of `pdf`, `csv` or `json` (anything else is 422) and the
   same `view` parameter as the report. Order: `_meeting_or_404` (workspace,
   tombstone, record predicate) then `meeting_view(surface="export")` then the
   revision check then render, audit, commit. One `meeting.exported` audit per
   success with `artifact=report`; a refused request audits no export.
3. **Renderers (D3).** `services/report_export_service.py` is pure: no
   database, no clock, no network, no file reads; the time comes in as an
   argument. The JSON body is `{"export_version", "provenance", "report"}` and
   its `report` value equals the body of `GET /report` for the same caller and
   view. The CSV is UTF-8 with a BOM, one `path,value` row per JSON leaf
   (dotted paths with list indexes, `None` empty, booleans as `true`/`false`).
   The PDF uses reportlab platypus with built-in Helvetica only; one helper
   strips C0 control characters (except newline), XML-escapes and converts
   newlines, so no dynamic text reaches Paragraph markup unescaped. The PDF
   `/Title` metadata is the same cleaned title the page shows: one line of
   plain text, no markup and no control characters.
4. **Revision pinning (D4).** Both export routes (transcript and report) take
   an optional `revision_id`, compared exactly with the current revision id.
   A mismatch answers `409 transcript_revision_mismatch` before anything is
   rendered or audited. Without the parameter the export uses the current
   revision, as before. The public contract moves to 1.9.0 for this
   (additive): it documents `revision_id`, the 409 with its own description
   on this route, and the two headers.
5. **Provenance everywhere (D5).** Both export routes send
   `X-Homayar-Export-Version: 1` and `X-Homayar-Transcript-Revision: <id or
   none>` plus `Cache-Control: no-store`. Transcript export bodies carry
   `export_version` and `analysis` (provider, model, prompt version and
   hashes, schema version, mock flag, the revision the run analysed). The
   report export carries the same in `provenance`. The report export reads
   the analysis run once: the run that wrote the summary it shows (the
   current run when there is no summary or the summary names no run). Its
   `analysis` and its privacy provenance (`analysis_input_view`) both
   describe that one run.

### No new permission

The report export uses `meeting:read` and the same record predicate, tombstone
and privacy view as the transcript export. A caller who may read the report may
export it; organization roles still never export (their grants carry no
`meeting:read`). A new permission would add a decision without changing who
may see the data.

### Share exports carry no analysis provenance

The share export keeps `analysis: null`. A share viewer is not a workspace
member; provider names, model names and prompt hashes are operator
information, not part of the shared conversation. The share export route is
otherwise unchanged (no `revision_id`, no new headers).

### The CSV rule

Every CSV cell this feature produces goes through `neutralize_csv_cell`
(OWASP CSV injection rule): a `'` prefix when the first character is a tab, a
carriage return or a newline, or the first non-space character is `=`, `+`,
`-`, `@` or the full-width lookalikes `＝ ＋ － ＠`. Before that check, the
rule skips leading whitespace and every invisible character of Unicode
category `Cc`, `Cf` or `Z*` (for example NUL, a zero width space, a byte
order mark or a no-break space), so an invisible character cannot hide a
formula from the check. Paths are neutralized too.
Only string cells are neutralized; numbers we produce stay numbers.

## Not built (HOM-138 and later)

- Persian fonts, shaping and RTL in the PDF: any Unicode renders without
  raising, but Helvetica shows Persian glyphs as empty boxes until HOM-138.
- Large export limits, stored exports, their retention and signed downloads.
- No report export through share links.
- No console change.
