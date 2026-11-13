# ADR 0063: Persian PDF exports, export limits and retention

Date: 2026-11-13 · Status: Proposed (HOM-138, WP24-T06, F70)

## Context

F70 asks that exports match the authorized revision, that Persian fonts,
shaping and right-to-left order are checked by hand, that CSV formula
injection is neutralized, and that revoked access and retention apply to
exported objects.

HOM-137 (ADR 0059) built the report export (PDF, CSV, JSON) next to the
transcript and share exports. Its PDF used Helvetica: Persian printed as
empty boxes, letters did not join and the word order was wrong. No export
had a size limit, and no export route checked retention expiry (the
recording routes do since HOM-60).

## Decisions

1. **Font (D1).** The report PDF draws every line with Vazirmatn, Regular
   for text and Bold for the title and headings. The static TTF files of the
   official v33.003 release, with their licence (SIL Open Font License 1.1),
   are bundled in `Homayar/backend/assets/fonts/`. The README there records
   the download URL and the SHA-256 of each file. The Docker image copies
   `Homayar/backend` whole, so the fonts ship with the API. The app never
   downloads a font at runtime (air-gap). The fonts are registered with
   reportlab once per process. Vazirmatn has Latin glyphs, so English uses
   the same font and Helvetica is no longer in the PDF.
2. **Dependencies (D2).** `arabic-reshaper==3.0.0` and `python-bidi==0.6.6`
   moved from the unused `persian-export` extra to the runtime
   dependencies. The extra is gone. Both have wheels for every platform the
   image builds on.
3. **Shaping and right-to-left order (D3).** reportlab does not join
   Arabic-script letters and does not reorder text. `backend/services/pdf_text.py`
   prepares each logical line before it is drawn:
   1. C0 control characters (except newline) become a space, and whitespace
      runs become one space, as HOM-137 printed them. The no-break spaces
      `U+00A0`, `U+2007` and `U+202F` are not collapsed: they stay inside
      their word. So a line never breaks there, and `۱۰ 000` written with
      `U+00A0` stays one number in a right-to-left line (UAX #9 W4);
   2. `arabic_reshaper` picks the joined form of each letter. ZWNJ breaks a
      join. Harakat are kept (Vazirmatn draws them with zero width);
   3. an Arabic presentation form (`U+FB50` to `U+FDFF`, `U+FE70` to
      `U+FEFF`) the font lacks becomes its base letter (only `U+FBA4`,
      isolated `ۀ`, among the Persian letters). Any other character the font
      lacks keeps its value and draws as a missing glyph: `10⁵` never prints
      as `105`;
   4. the line is wrapped to the frame width in logical order, with the
      Vazirmatn metrics. Only a plain space is a line break. Wrapping after
      reordering would put the last words of a Persian sentence on the first
      line;
   5. each wrapped line is reordered alone with `python-bidi` `get_display`,
      in the direction of its logical line: the first strong character
      (UAX #9 P2 and P3). A right-to-left line is right-aligned, a
      left-to-right line left-aligned;
   6. `get_display` (the Rust implementation in python-bidi 0.6) leaves out
      two display rules, so they are added: a combining mark follows its
      base (L3), and a bracket in a right-to-left run is mirrored (L4). Both
      use the levels python-bidi resolved; when our reading of those levels
      would not give the same order as `get_display`, the line is kept as
      `get_display` returned it. Reading the levels uses python-bidi's debug
      output, and the reshaper speed fix overrides a private arabic-reshaper
      property. A test fails if either path stops working, so a version bump
      breaks CI instead of silently dropping bracket mirroring;
   7. bidi control characters are dropped after step 5.

   The prepared line is plain text. A small flowable draws it with
   `drawString` or `drawRightString`, never through `Paragraph` markup. So
   stored `<b>` prints as `<b>`, stored `&amp;` prints as `&amp;`, and no
   link annotation or file read can come from stored text. The PDF `/Title`
   is the cleaned logical title, not its visual form.
4. **CSV (D4).** `neutralize_csv_cell` is unchanged. New tests pin its
   behaviour on Persian text, including a formula hidden behind ZWNJ, RLM,
   LRM, ALM, ZWSP, BOM or NBSP.
5. **Size limit (D5).** New setting `EXPORT_MAX_TEXT_CHARS` (default
   2 000 000, at least 1). The export text size is the summed length of
   every string value the export would carry, measured before rendering:
   for the report export, every string value of its JSON payload; for the
   transcript and share exports, every string value of the `MeetingExport`
   (segment texts, speaker names, title, summary, topics, action texts,
   provenance and quality strings). Keys are not counted. Over the limit
   the answer is `413 export_too_large` with the limit in
   `error.details.limit`; nothing is rendered or audited. Exactly the limit
   is allowed.
6. **Retention (D6).** Exports are never stored, so they cannot outlive
   their source. The transcript and report exports answer
   `410 conversation_retention_expired` when `is_retention_expired` is
   true: the expiry is due, no processing job is queued or running, and no
   media object is under legal hold (the rule of the retention sweep and of
   the recording routes). The share export cannot read jobs or media rows,
   so it uses `is_expiry_due` and refuses any due expiry, as the shared
   recording does. A refusal writes no `meeting.exported` audit and no
   share-access audit.
7. **Order of checks (D7).** Transcript and report export: authentication
   and permission, meeting lookup with record predicate and tombstone
   (`404` for anyone who may not read it, so the retention state never
   leaks), retention (`410`), the privacy view, `revision_id` (`409`), size
   (`413`), then render, audit, commit. Share export: share token valid,
   not revoked, not expired, then retention (`410`), then size (`413`).
8. **Authorized downloads (D8).** No signed URLs and no stored export
   objects. Each download runs authorization again, so removing a member,
   removing record access, a tombstone or a revoked share refuses the next
   export at once, exactly as the matching read route. Every export answer,
   successful or refused, is `Cache-Control: no-store`. The download name
   stays `attachment; filename="homayar-[report-]<meeting id>.<ext>"`: the
   server's meeting id, never the title, so no header injection is possible
   and no `filename*` is needed.
9. **Contract (D9).** The public contract moves to 1.10.0 (additive). It
   documents `410 conversation_retention_expired` and `413
   export_too_large` on `GET /api/v1/meetings/{meeting_id}/export`, the only
   export route in the contract. The report export and the share export are
   not part of the public contract; their `410` and `413` are documented
   here and in the authorization matrix.

## Not built

- No stored exports, so no export retention sweep and no signed download
  URLs. If exports are ever stored, they need their own retention and
  revocation.
- Kerning and OpenType positioning (GPOS) are not applied; reportlab draws
  glyphs with their advance widths. Marks rely on Vazirmatn's zero-width
  mark glyphs.
- Letters of other Arabic-script languages that Vazirmatn lacks (for
  example some Urdu, Sindhi and Pashto letters) still print as boxes. So do
  other characters it lacks, such as superscript and subscript digits.
- `U+2007` (figure space) has the bidi class WS. Between two numbers in a
  right-to-left line the groups are still drawn in right-to-left order, as
  the bidi algorithm says. Only `U+00A0` and `U+202F` join two numbers.
- No report export through share links, and no console change.
