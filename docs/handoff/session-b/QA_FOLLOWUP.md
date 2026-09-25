# Session B QA follow-up — members, settings/audit and search

Audit only. No product code, dependency, build configuration or shared file was changed; fixes
are to be applied **after the Session C integration is stable**. The items below are proposals
with acceptance criteria, not accepted work.

## What was reviewed

| Item | Value |
| --- | --- |
| Product commit | `71e36aadb7adf8ff0535919a964c74ac318e97f4` (`session-b/frontend-journey`, Session B's own clone) |
| Backend contract read | `backend/` at the same commit — identical to `d1cc5d4` (`git diff d1cc5d4 71e36aa -- backend` is empty) |
| Pages | `frontend/src/components/platform/MembersPage.tsx`, `SettingsPage.tsx` (settings + audit log), `SearchPage.tsx`, and the shared pieces they render (`Ui.tsx` `ErrorState`/`StatusBadge`, `lib/status-labels.ts`, header search box, sidebar) |
| Not re-checked (done in B2–B8) | auth pages, workspace gate, upload, processing, meeting results/privacy, usage, mobile drawer |
| Out of scope | invitation acceptance page, shared-meeting page (listed as partly localized in `PROGRESS.md`; not audited here) |
| Date | 2026-09-25 |

### Evidence labels

- **CODE** — found by reading the source at the commit above.
- **BROWSER-FIXTURE** — observed or measured in Claude's built-in browser (Chromium; mobile
  emulation 375×812 with an Android user agent; console language Persian, the default; the
  browser pane was hidden during the run) against **Session B's own fixture API**
  (`sb-qa-preview.mjs` in the session scratchpad, not committed). The fixture answers the product
  routes with hand-written records labelled `FIXTURE`; its error codes and messages are copied
  from the backend source (`routers/v1/members.py`, `routers/v1/admin.py`,
  `routers/v1/insights.py`, `services/member_service.py`). **No product backend, database,
  email service or Session C service, database or working tree was used. These results are not
  results for Session C's integrated version.**
- No full test suite or production build was run for this audit.

### Not run

- **Search error state in the browser: NOT_RUN.** The fixture returned 503, but React Query
  pauses its retry while the document is hidden (`document.visibilityState === "hidden"` in
  the pane), so the page stayed on "در حال جستجوی جلسه‌ها". The error behaviour below (Q-09) is
  from code only.
- Real devices, Safari/Firefox, screen readers (NVDA/VoiceOver) and desktop-width screenshots
  were not used. Keyboard behaviour of `<select>` differs by platform (see M-01).

## Priorities

- **P1** — a customer or administrator on the main path gets a wrong/unsafe action, a
  misleading promise, a broken phone layout, or core status/error text they cannot read in
  Persian.
- **P2** — keyboard/focus/label failures, RTL and digit defects, inconsistent terms, missing
  guidance in a state.
- **P3** — polish and copy.

## Summary

| ID | P | Page | Finding | Evidence |
| --- | --- | --- | --- | --- |
| M-01 | P1 | Members | One arrow key on a focused role `<select>` changes the member's role immediately (PATCH + audit event), no confirmation | BROWSER-FIXTURE |
| M-02 | P1 | Members | Page is 722 px wide on a 375 px phone for owners/admins and opens scrolled onto blank space | BROWSER-FIXTURE (+ root cause measured) |
| M-03 | P1 | Members | Invitation delivery failure shows English text and hides that the server already revoked the invitation | BROWSER-FIXTURE + CODE |
| X-01 | P1 | All three | `ErrorState` shows the server's English message; Persian fallbacks are never used for API errors | BROWSER-FIXTURE + CODE |
| X-02 | P1 | Members, audit | Status badges `pending`, `accepted`, `success`, `failure` have no labels and render in English | BROWSER-FIXTURE |
| Q-01 | P1 | Search | Match links promise "open meeting at 0:15" (`?at=`) but no page reads `at` | CODE |
| M-04 | P2 | Members | Focus drops to `<body>` after remove, cancel, role change and invite submit | BROWSER-FIXTURE |
| M-05 | P2 | Members | Table scroll area (720 px content in 341 px) cannot be reached by keyboard for read-only roles | BROWSER-FIXTURE |
| M-06 | P2 | Members | Role names differ from the rest of the console (`مشاهده‌گر` vs `بیننده`, `Admin` vs `Administrator`) | BROWSER-FIXTURE + CODE |
| M-07 | P2 | Members | Member count uses Latin digits and says "active" | BROWSER-FIXTURE |
| M-08 | P2 | Members | Remove confirmation does not say who or what happens | BROWSER-FIXTURE |
| S-01 | P2 | Settings | Focus drops to `<body>` after save (form remounts) and after audit paging | BROWSER-FIXTURE + CODE |
| S-02 | P2 | Settings | Workspace name in header and page eyebrow stays old after a rename | BROWSER-FIXTURE + CODE |
| S-03 | P2 | Audit | Pagination chevrons point the wrong way in RTL; page number uses Latin digits | BROWSER-FIXTURE |
| S-04 | P2 | Audit | Event types, detail keys/values and targets are raw English codes | BROWSER-FIXTURE |
| X-03 | P2 | Members, settings | Number inputs reject Persian digits (Persian keyboard cannot enter days) | BROWSER-FIXTURE + CODE |
| Q-02 | P2 | Search | Match kinds (`TRANSCRIPT`…) and language (`FA`/`EN`) in English | BROWSER-FIXTURE |
| Q-03 | P2 | Search | Durations and timestamps in Latin digits (local duplicate of the shared formatter) | BROWSER-FIXTURE + CODE |
| Q-04 | P2 | Search | Match link `aria-label` hides the snippet from screen readers; odd name when no timestamp | BROWSER-FIXTURE (DOM) |
| Q-05 | P2 | Search | Result with no previewable match renders an empty list with no explanation | BROWSER-FIXTURE |
| Q-06 | P2 | Search | Whole result list is an `aria-live` region | CODE (+ DOM) |
| Q-07 | P2 | Search | "جستجو" on the page vs "جست‌وجو" in header and sidebar | BROWSER-FIXTURE |
| M-09…M-14, S-05…S-10, Q-08…Q-11, X-04…X-05 | P3 | — | Copy, validation and polish items below | CODE / BROWSER-FIXTURE |

## Members (`MembersPage.tsx`)

### M-01 · P1 · Role changes on a single arrow key, without confirmation
- **Evidence:** BROWSER-FIXTURE. The member «علی رضایی (FIXTURE)»'s role select was focused
  and one `ArrowDown` was pressed. The value changed `member → viewer` and the browser sent
  `PATCH /api/v1/members/mem-3 → 200`. CODE: `MembersPage.tsx:215-233` mutates in `onChange`.
- **Why it matters:** browsers that change a closed `<select>` on arrow keys (seen here; also
  typical on Windows/Linux Chromium and Firefox) grant or remove access while the owner is only
  navigating. That includes promotion to **admin** and one audit event per keypress.
- **Repro:** as owner, open `/app/members`, Tab to a member's role select, press ArrowDown.
- **Fix acceptance:** changing the select alone sends no request. An explicit "اعمال" (Apply)
  button, or a confirmation, commits the change. Promotion to admin names the consequence
  ("مدیر می‌تواند اعضا، تنظیمات و لاگ ممیزی را مدیریت کند"). Test: `fireEvent.change` on the
  select does not call `changeMemberRole`, and the Apply button does.

### M-02 · P1 · Members page overflows to 722 px on phones (owners/admins)
- **Evidence:** BROWSER-FIXTURE at 375×812, owner workspace. Measured values:
  - `document.documentElement.scrollWidth` 722 vs `clientWidth` 375.
  - `innerWidth` 722, and `scrollX` −347 on load.
  - The screenshot shows mostly empty background with the content at the edge.
- **Root cause:** measured by toggling styles in the page (the source was not edited).
  - The `sr-only` elements inside the table (the caption and the per-row `<label>`s of the role
    selects) are `position: absolute`.
  - No ancestor inside the `overflow-x-auto` wrapper is positioned, so these elements escape
    the wrapper's clipping and extend the document to the left in RTL.
  - Setting `position: relative` on the wrapper (`MembersPage.tsx:119`) brought
    `scrollWidth` back to 375.
  - Hiding the table also gave 375. With the viewer role, where rows have no labels, the width
    was already 375.
- **Fix acceptance:** the table wrapper is a positioned scroll container. At 375 px, with owner
  and admin fixtures, `scrollWidth === clientWidth` on `/app/members`. Consider a stacked
  (card) layout below `sm` instead of a 720 px table.

### M-03 · P1 · Invitation delivery failure: English text and a hidden side effect
- **Evidence:** BROWSER-FIXTURE: after submit, `ErrorState` shows «The invitation could not be
  delivered». CODE: the backend creates the invitation, fails delivery, revokes it and then
  answers `503 invitation_delivery_failed`
  (`backend/routers/v1/members.py:162-196`). `useInviteMember` refreshes the list only on success
  (`platform-api.ts` `useInviteMember`). The fixture mirrored this: server list 5, UI list 4.
- **Proposed Persian:** «ایمیل دعوت ارسال نشد و این دعوت به‌طور خودکار لغو شد. کمی بعد دوباره
  تلاش کنید؛ اگر تکرار شد با پشتیبانی تماس بگیرید.» (en: "The invitation email could not be
  sent, so the invitation was cancelled automatically. Try again later; contact support if it
  keeps happening.")
- **Fix acceptance:** on `invitation_delivery_failed`, show that text, keep the typed email and
  invalidate the invitations query. The new revoked row appears with a "لغوشده" badge. Test with
  a 503 mock asserting the text and the refetch.

### M-04 · P2 · Focus is lost after member actions
- **Evidence:** BROWSER-FIXTURE, each checked through `document.activeElement` after a keyboard
  Enter or ArrowDown:
  - Remove (trash) → the confirmation group appears, focus is on `BODY`.
  - Cancel → focus is on `BODY`.
  - Role change → the select is disabled while pending, and focus is on `BODY`.
  - Invite submit → the button is disabled while pending, and focus is on `BODY`.
- **CODE:** the trash button unmounts at `MembersPage.tsx:182-247`, and the controls are
  disabled while their mutation is pending (lines 219, 241, 369, 402, 405).
- **Fix acceptance:**
  - Opening the remove confirmation focuses «انصراف».
  - Cancel returns focus to the trash button.
  - After a successful removal, focus moves to the directory heading (`#members-heading`,
    `tabIndex={-1}`) and an announcement is made.
  - Pending controls use `aria-disabled` plus a guard instead of `disabled`, or focus is
    restored after the mutation settles.
  - A test asserts `document.activeElement` for each step.

### M-05 · P2 · Horizontal table scroll is not keyboard-reachable for read-only roles
- **Evidence:** BROWSER-FIXTURE as viewer: wrapper `clientWidth` 341, `scrollWidth` 720,
  `tabIndex` −1, 0 focusable elements in the table; the «تاریخ عضویت» and «مدیریت» columns are
  off-screen.
- **Fix acceptance:** the wrapper has `tabIndex={0}`, `role="region"` and an accessible name
  («فهرست اعضا، قابل پیمایش افقی»). Alternatively the M-02 card layout makes the scroll
  unnecessary.

### M-06 · P2 · Role names inconsistent with the rest of the console
- **Evidence:** BROWSER-FIXTURE: the workspace chooser shows «بیننده», the members page shows
  «مشاهده‌گر». CODE: `MembersPage.tsx:55-63` defines its own `roleLabel` (en `Admin`), while
  `lib/roles.ts` (used by header and chooser) has `بیننده` / `Administrator`.
- **Fix acceptance:** the members page imports `roleLabel` from `lib/roles.ts`, and there is one
  term per role across the console. Proposed set: مالک، مدیر، عضو، بیننده.

### M-07 · P2 · Member count digits and wording
- **Evidence:** BROWSER-FIXTURE «4 عضو فعال» (`MembersPage.tsx:99`).
- **Proposed:** «۴ عضو» via `formatNumber(total, "fa")`. Drop «فعال» until the list honours
  membership validity windows (the API returns `valid_from`/`valid_until`, the console ignores
  them; relates to gap G9).
- **Fix acceptance:** Persian digits in fa; no "active" claim.

### M-08 · P2 · Remove confirmation lacks subject and consequence
- **Evidence:** BROWSER-FIXTURE: the group reads «حذف شود؟ / انصراف / تأیید»
  (`MembersPage.tsx:183-208`).
- **Proposed:** «دسترسی {نام} به این فضای کاری قطع شود؟», with the buttons «انصراف» and
  «حذف عضو». Do not claim that already-issued download links stop working.
- **Fix acceptance:** the text names the member (or their contact if there is no name) and the
  confirm button says what it does.

### P3 items
- **M-09 · Empty display name.**
  - BROWSER-FIXTURE: a member with `display_name: ""` shows the email twice.
  - The hidden label reads «نقش » and the remove button «حذف », with no name.
  - Fix: use `display_name || contact` in every label (`MembersPage.tsx:183, 213, 242`).
- **M-10 · Invitation dates.**
  - CODE `MembersPage.tsx:296-299`: accepted and revoked invitations still show «انقضا: …».
  - Fix: show «پذیرفته‌شده در: {accepted_at}» or «لغوشده در: {revoked_at}».
- **M-11 · Email field direction.**
  - BROWSER-FIXTURE: the invitation email input computes `direction: rtl`.
  - Fix: `dir="ltr"` on email inputs (the placeholder `name@company.com` is LTR).
- **M-12 · Native validation for invitation days.**
  - CODE `MembersPage.tsx:331`: 0/31/7.5 days are stopped by the browser's own bubble, in the
    browser language, and the handler returns silently.
  - Fix: use `noValidate`, then an inline error «یک عدد صحیح بین ۱ تا ۳۰ وارد کنید.» linked by
    `aria-describedby` and `aria-invalid`.
- **M-13 · Silent success states.**
  - CODE: a successful role change has no announcement.
  - «دعوت‌نامه ارسال شد.» persists indefinitely.
  - Fix: a polite status «نقش {نام} به «{نقش}» تغییر کرد.», and clear the success notices on
    the next edit.
- **M-14 · Revoke has no confirmation.**
  - CODE `MembersPage.tsx:302-311`: one click revokes.
  - Acceptable if the owner agrees; otherwise use the same inline confirmation as removal.

## Settings and audit log (`SettingsPage.tsx`)

### S-01 · P2 · Focus lost after save and after paging
- **Evidence (save):**
  - BROWSER-FIXTURE: after Enter on «ذخیره تنظیمات», the status «تنظیمات ذخیره شد.» appears
    and focus is on `BODY`.
  - CODE: `SettingsPage.tsx:252-253` remounts the form with `key={updated_at}`, so the focused
    button is replaced.
- **Evidence (paging):** BROWSER-FIXTURE: after Enter on «بعدی», focus is on `BODY` because the
  list is swapped for a loading state (`SettingsPage.tsx:340-341`).
- **Fix acceptance:**
  - After a successful save, focus stays on, or returns to, the save button. Otherwise the
    status message is focusable and receives focus.
  - Paging keeps the previous page visible while fetching (`placeholderData`) and keeps focus
    on the pressed button.

### S-02 · P2 · Renamed workspace still shows the old name
- **Evidence:**
  - BROWSER-FIXTURE: after saving a new name, the form showed the new name, but the header
    workspace select and the page eyebrow kept the old one.
  - The fixture's `/auth/me` is static, so later refreshes were not tested.
  - CODE: `useUpdateWorkspaceSettings` only writes the settings cache
    (`platform-api.ts` `useUpdateWorkspaceSettings`). Nothing refreshes the session
    workspaces used by `Header` and `PageHeader`.
- **Fix acceptance:** after a successful name change, the header, chooser and eyebrow show the
  new name without a reload (refresh the session or update the workspace in `AuthProvider`).

### S-03 · P2 · Audit pagination in RTL
- **Evidence:** BROWSER-FIXTURE: «قبلی» sits on the right with `lucide-chevron-left`, and
  «بعدی» sits on the left with `lucide-chevron-right`. The computed `transform` is `none`, so
  both arrows point inward. The label reads «صفحه 1» / «صفحه 2» (`SettingsPage.tsx:401-417`).
- **Fix acceptance:** the chevrons carry the existing `rtl-mirror` class (or use logical icons),
  and the page label uses `formatNumber` («صفحه ۲»).

### S-04 · P2 · Audit entries are raw codes
- **Evidence:** BROWSER-FIXTURE, for example:
  - «member.role_changed», «previous role: member», «new role: viewer».
  - «عامل: user-1», «هدف: membership · mem-3».
- **CODE:** `SettingsPage.tsx:357-385`. The event type placeholder is `member.role_changed`
  (line 322).
- **Fix acceptance:**
  - Known event types, detail keys, role values and target types use the tables below. The raw
    code stays visible as secondary LTR text.
  - Unknown values are shown as sent, never guessed.
  - The actor is shown by member name when the member list (readable by owner/admin) has it.
  - The event type filter offers a list of known types.

| Event type | Proposed Persian |
| --- | --- |
| `member.invitation.created` | دعوت عضو ایجاد شد |
| `member.invitation.delivery_failed` | ارسال ایمیل دعوت ناموفق بود |
| `member.invitation.revoked` | دعوت لغو شد |
| `member.invitation.accepted` | دعوت پذیرفته شد |
| `member.role_changed` | نقش عضو تغییر کرد |
| `member.removed` | عضو حذف شد |
| `workspace.settings.updated` | تنظیمات فضای کاری تغییر کرد |
| `meeting.uploaded` | جلسه بارگذاری شد |
| `meeting.updated` | جلسه ویرایش شد |
| `meeting.deleted` | جلسه حذف شد |
| `meeting.exported` | جلسه خروجی گرفته شد |
| `meeting.read` | جلسه مشاهده شد |
| `meeting.speaker_renamed` | نام گوینده تغییر کرد |
| `meeting.share_created` / `meeting.share_revoked` / `meeting.share_accessed` | لینک اشتراک ساخته شد / لغو شد / باز شد |
| `meeting.processing.completed` / `meeting.processing.failed` | پردازش جلسه تمام شد / ناموفق بود |
| `meeting.retention_expired` | مهلت نگهداری جلسه به پایان رسید |
| `meeting.recording.deleted_after_processing` | فایل صوتی پس از پردازش حذف شد |
| `transcript.original_accessed` | متن اصلی (بدون پوشش) مشاهده شد |
| `pii.revealed` | یک مقدار پوشیده آشکار شد |
| `transcript.revision.created` | نسخهٔ تازهٔ متن ساخته شد |
| `action_item.created` / `.updated` / `.deleted` | اقدام ساخته شد / ویرایش شد / حذف شد |
| `api_key.created` / `api_key.revoked` | کلید API ساخته شد / لغو شد |
| `auth.register`, `auth.email_verified`, `auth.password_reset_requested`, `auth.password_reset_completed`, `auth.otp_requested`, `auth.otp_verified`, `auth.google_completed` | ثبت‌نام، تأیید ایمیل، درخواست بازنشانی گذرواژه، بازنشانی گذرواژه، درخواست کد پیامکی، تأیید کد پیامکی، ورود با Google |

These are the event types recorded by `backend/` at the reviewed commit (found with grep; the
table covers the ones an administrator is likely to see; `media.*`, `ingestion.*`, `team.*` and
`organization.*` still need labels).

| Key / value | Proposed Persian |
| --- | --- |
| `previous_role` / `new_role` / `role` | نقش قبلی / نقش جدید / نقش (values via `lib/roles.ts`) |
| `fields` (comma list) | فیلدهای تغییرکرده: `name` نام، `retention_days` مدت نگهداری، `retention_mode` سیاست نگهداری، `delete_audio_after_processing` حذف صوت پس از پردازش |
| target `membership` / `workspace_invitation` / `workspace` / `meeting` | عضویت / دعوت / فضای کاری / جلسه |
| actor `null` | سیستم (already localized) |

### P3 items
- **S-05 · Developer jargon.**
  - «فقط فیلدهای مجاز سرور قابل ویرایش‌اند» / "Only server-allowlisted fields can be edited"
    (`SettingsPage.tsx:224-226`).
  - Proposed: «نام فضای کاری و سیاست نگهداری داده را اینجا تنظیم کنید.» / "Set the workspace
    name and data-retention policy."
- **S-06 · Retention days validation.**
  - CODE `SettingsPage.tsx:150-171`: the error `<p role="alert">` is not tied to the input
    (`aria-invalid`, `aria-describedby`).
  - The legacy warning (lines 133-139) is not linked to the select either.
  - Fix: link both.
- **S-07 · Empty audit state.**
  - With no filters, the page still says «رویدادی با این فیلتر یافت نشد».
  - Proposed: «هنوز رویدادی ثبت نشده است.» when no filter is applied.
- **S-08 · Invalid cursor.**
  - CODE: `400 invalid_cursor` / `invalid_audit_range` leave the admin on an error with no
    way back.
  - Fix: message «فهرست رویدادها تغییر کرده است؛ از صفحهٔ اول دوباره ببینید.» plus a
    «صفحهٔ اول» button that resets the cursor stack.
- **S-09 · Denied state.**
  - BROWSER-FIXTURE (viewer): the localized panel appears, but the page header still shows
    «مدیریت فضای کاری» to a viewer, and the panel offers no next step.
  - Fix: hide that header chip for non-admins and add «برای تغییر تنظیمات با مالک یا مدیر فضای
    کاری هماهنگ کنید.»
- **S-10 · Workspace name input.**
  - Add `dir="auto"` for mixed-script names (`SettingsPage.tsx:106`).

## Search (`SearchPage.tsx`)

### Q-01 · P1 · "Open meeting at {time}" does not open at that time
- **Evidence:** CODE. `meetingUrl` builds `/meetings/{id}?at={ms}` (`SearchPage.tsx:78-81`), and
  the link is labelled «باز کردن جلسه در 0:15» (line 209). No component reads `at`: `grep` for
  `useSearchParams`/`searchParams.get` finds only auth pages, invitation acceptance and
  `SearchPage` itself. BROWSER-FIXTURE confirmed the generated hrefs (`/app/meetings/m-done?at=15000`).
- **Fix acceptance, either option:**
  - The meeting page reads `at`, opens the transcript tab and focuses and scrolls to the
    segment containing that time. Test: navigating to `?at=15000` focuses the segment whose
    range contains 15 000 ms.
  - Or the label drops "at {time}" until that exists.

### Q-02 · P2 · Match kind and language are English codes
- **Evidence:** BROWSER-FIXTURE: «TRANSCRIPT», «SUMMARY», «ACTION», «TITLE», «زبان: FA / EN»
  (`SearchPage.tsx:188, 215-217`).
- **Proposed:**
  - Kinds: `title` «عنوان»، `transcript` «متن گفت‌وگو»، `summary` «خلاصه»، `action` «اقدام».
    Unknown kinds are shown as sent.
  - Language: `new Intl.DisplayNames([lang], { type: "language" }).of(code)` → «فارسی» /
    «انگلیسی». Fall back to the raw code.
- **Fix acceptance:** no Latin kind or language code in the Persian UI for known values.

### Q-03 · P2 · Latin digits in durations and timestamps
- **Evidence:** BROWSER-FIXTURE «12:34», «0:15», «1:02:05». CODE: `SearchPage.tsx:60-69`
  duplicates a formatter instead of using `formatClock`/`formatDuration` from `lib/format.ts`
  (which already produce Persian digits and are used elsewhere since B8).
- **Fix acceptance:** the shared formatter is used, giving «۱۲:۳۴», and the clock chips are
  isolated LTR.

### Q-04 · P2 · Link names hide the snippet
- **Evidence:** BROWSER-FIXTURE (DOM): each match link's `aria-label` is
  «باز کردن جلسه در 0:15: {title}», which replaces the visible kind and snippet. With no
  timestamp it reads «باز کردن جلسه در تطابق: …» (`SearchPage.tsx:201-209`).
- **Fix acceptance:** the accessible name contains the kind and snippet, for example
  «{نوع}: {snippet} — {عنوان}، دقیقهٔ {زمان}». The no-timestamp chip «تطابق» is removed or
  renamed. Test with `getByRole("link", { name: /فاکتور/ })`.

### Q-05 · P2 · Results without a preview show an empty card
- **Evidence:**
  - BROWSER-FIXTURE: a result with `matches: []` renders the header and an empty `<ul>`.
  - CODE: the backend keeps such meetings when only a speaker name matched in the redacted
    branch (`backend/routers/v1/insights.py:1188-1192`).
- **Proposed:** «پیش‌نمایشی برای این نتیجه در دسترس نیست؛ جلسه را باز کنید.» This does not
  claim why it matched.
- **Fix acceptance:** no empty list is rendered, and the sentence is shown instead.

### Q-06 · P2 · The whole result list is live
- **Evidence:** CODE/DOM: `aria-live="polite"` on `.search-results` (`SearchPage.tsx:153`)
  makes screen readers read every card on each query change.
- **Fix acceptance:** only a short status (the count, or "no results", "error") is live; the list
  is not.

### Q-07 · P2 · Spelling of "search"
- **Evidence:** BROWSER-FIXTURE: the page shows «جستجوی جلسه‌ها», «جستجو», while the header shows
  «جست‌وجوی جلسه‌ها و متن‌ها» and the sidebar «جست‌وجو».
- **Fix acceptance:** «جست‌وجو» (with ZWNJ) everywhere in `SearchPage` `COPY.fa`.

### P3 items
- **Q-08 · Result cap not shown.**
  - The page asks for `limit: 50`, and the API has no cursor or total.
  - When 50 results come back, add «۵۰ نتیجهٔ اول نمایش داده شده؛ عبارت دقیق‌تری جست‌وجو
    کنید.»
- **Q-09 · Search errors (CODE; the browser check was NOT_RUN).** Errors go through
  `ErrorState`, so users see English, for example «Search backend unavailable» (see X-01).
  Map the following:
  - `search_query_too_short` / 422 on `q` → «حداقل دو نویسه وارد کنید.»
  - 5xx / network → «جست‌وجو الان در دسترس نیست؛ کمی بعد دوباره تلاش کنید.»
- **Q-10 · Query length.**
  - The input has no `maxLength` (measured −1). The backend limit is 160
    (`insights.py:1109`).
  - Fix: `maxLength={160}` plus the helper «حداکثر ۱۶۰ نویسه.».
  - The browser counts UTF-16 units and the backend counts code points: a single astral
    character passes the 2-character check in the browser but not on the server. This is rare;
    count with `[...q].length`.
- **Q-11 · Masked values not marked.**
  - BROWSER-FIXTURE: snippets show `[EMAIL_1]` as plain text.
  - The meeting page marks placeholders with `MaskedText` (B6).
  - Fix: reuse it, with `dir="auto"` on snippets.

## Cross-cutting

### X-01 · P1 · English server text in errors
- **Evidence:**
  - BROWSER-FIXTURE: «This member operation is not permitted» on a refused role change, and
    «The invitation could not be delivered».
  - CODE: `Ui.tsx:45-52` `ErrorState` → `getAuthErrorMessage`, which prefers
    `error.message` and pydantic `details[].msg`. The Persian `fallback` props on these pages are
    therefore never used for API errors.
- **Fix acceptance:** these pages pass a code-to-text map (as B2 did for auth with
  `apiErrorInfo`), and an unknown code shows the page's Persian fallback plus the `request_id`.
  Test per code below.

| Code (HTTP) | Where | Proposed Persian |
| --- | --- | --- |
| `member_not_found` (404) | role change, remove | این عضو دیگر در این فضای کاری نیست؛ فهرست به‌روز شد. (and refetch) |
| `member_operation_forbidden` (403) | role change, remove, invite | شما اجازهٔ این تغییر را ندارید. مدیرها فقط نقش «عضو» و «بیننده» را مدیریت می‌کنند و مالک تغییرپذیر نیست. |
| `member_conflict` (409) | invite | این ایمیل از قبل عضو این فضای کاری است. |
| `invitation_pending` (409) | invite | برای این ایمیل یک دعوت فعال وجود دارد؛ آن را لغو کنید یا تا پایان اعتبارش صبر کنید. |
| `invitation_delivery_failed` (503) | invite | see M-03 |
| `invitation_not_found` (404) | revoke | این دعوت پیدا نشد؛ فهرست به‌روز شد. |
| `invitation_revoked` / `invitation_expired` (410) | revoke | این دعوت قبلاً لغو شده یا منقضی شده است. |
| `invitation_already_accepted` (409) | revoke | این دعوت پیش‌تر پذیرفته شده است؛ برای حذف دسترسی، عضو را حذف کنید. |
| `invalid_workspace_settings` (422) | settings save | مقدار واردشده پذیرفته نشد؛ فیلدها را بررسی کنید. |
| `workspace_not_found` (404) | settings | این فضای کاری در دسترس نیست. |
| `invalid_cursor` / `invalid_audit_range` (400) | audit | see S-08 |
| `validation_error` (422) on `email` | invite | نشانی ایمیل معتبر نیست. |
| `validation_error` (422) on `q` | search | see Q-09/Q-10 |
| `permission_denied` / `workspace_access_denied` (403) | all | دسترسی شما به این بخش تغییر کرده است؛ صفحه به‌روز می‌شود. (the B3 session refresh already runs) |
| `csrf_failed` (403) | mutations | نشست شما نیاز به تازه‌سازی دارد؛ صفحه را دوباره بارگذاری کنید. |
| network / 5xx | all | ارتباط با سرور برقرار نشد؛ کمی بعد دوباره تلاش کنید. |

### X-02 · P1 · Missing status labels
- **Evidence:** BROWSER-FIXTURE: invitation badges «Pending», «Accepted»; audit badges
  «Success», «Failure». CODE: `lib/status-labels.ts:7-45` has no `pending`, `accepted`,
  `success`, `failure`. `index.css` has no `platform-status-success/failure/accepted/owner/admin/
  member/viewer` colour classes, so a failed audit event looks like a successful one.
- **Proposed:** `pending` «در انتظار پذیرش»، `accepted` «پذیرفته‌شده»، `success` «موفق»، `failure`
  «ناموفق». In English: "Pending", "Accepted", "Succeeded", "Failed".
- **Fix acceptance:** the four labels exist, and a failure is visually distinct and never shown
  by colour alone.

### X-03 · P2 · Number inputs and Persian digits
- **Evidence:**
  - BROWSER-FIXTURE: typing «۱۴» into «مدت نگهداری (روز)» (`type="number"`) left the value
    empty and showed «یک عدد صحیح بین ۱ تا ۳۶۵۰ وارد کنید.», and «ذخیره تنظیمات» became
    disabled.
  - The invitation «اعتبار (روز)» field uses the same pattern (`MembersPage.tsx:391-403`, CODE).
  - Values display with Latin digits («90», «7»).
- **Fix acceptance:** use `type="text" inputMode="numeric"` with `toAsciiDigits` (as on the OTP
  page), and display the value in Persian digits in fa. Test: entering «۱۴» saves
  `retention_days: 14`.

### P3 items
- **X-04 · Eyebrow upper-casing.**
  - BROWSER-FIXTURE: the page eyebrow upper-cases English workspace names («FIXTURE WORKSPACE
    B»), which changes names the owner typed.
  - Fix: remove `text-transform` from `.platform-page-header p`, or apply it only to fixed
    labels.
- **X-05 · Sidebar English strings.**
  - Seen while auditing: `Sidebar.tsx:60` `aria-label="Close navigation"` and `:41`
    `"Unable to sign out."` are English in the fa UI.
  - Proposed: «بستن منو», «خروج انجام نشد؛ دوباره تلاش کنید.»

## Checked and fine (BROWSER-FIXTURE)

- Search and settings pages: no horizontal page scroll at 375 px (`scrollWidth == clientWidth`).
- Dates: Persian digits and Solar Hijri calendar («۲ مهر ۱۴۰۵، ۵:۳۰») on all three pages.
- Search: the result count «۳ نتیجه», the empty state «جلسه‌ای مطابق این جستجو پیدا نشد.» and the
  no-query prompt are localized.
- An English meeting title inside the RTL result card kept its glyph order (measured `(` and
  `)` positions around «FIXTURE»).
- Viewer role: the members page is read-only and hides invitations; settings shows the localized
  «دسترسی مدیریتی لازم است» panel; the sidebar hides «مصرف» and «تنظیمات».

## Observations outside frontend scope (for Sessions A/C, not changed here)

1. **Settings the console cannot show.** `GET /admin/workspace-settings` also returns:
   - `record_scope`
   - `public_share_links_enabled` and `organization_share_links_enabled`
   - `duplicate_media_policy`
   - `pii_redaction_enabled` and `pii_redaction_classes` (owner-only to change)

   The console type and page omit all of them, so an owner cannot see or change the redaction
   policy in the UI. Exposing them is a product decision.
2. **Invitation email.** The email subject and body are English only
   (`services/member_service.py` `send_invitation_email`). The accept link is
   `FRONTEND_URL + /invitations/accept`, so a deployment must set `FRONTEND_URL` to include the
   `/app` base. This was not verified.
3. **Membership validity.** The member list filters on `is_active` only. `valid_from` and
   `valid_until` are returned but not enforced in the listing (see G9).
4. **Search paging.** Search has no cursor or total (`limit` ≤ 50), so the UI cannot offer
   "more results".

## Suggested fix order (after integration is stable)

1. P1: M-01, M-02, X-01 (with the code table), X-02, M-03, Q-01.
2. P2 keyboard/focus: M-04, S-01, M-05, Q-04, Q-06.
3. P2 RTL/digits/terms: S-03, X-03, Q-03, M-07, Q-02, S-04, M-06, Q-07, S-02, M-08, Q-05.
4. P3 items.

Each fix should land with a mocked test in `frontend/src` (vitest + testing-library, as in B2–B8)
and a browser re-check at 375 px in Persian. The integrated result must then be re-verified in
Session C's environment, because nothing here was run against it.
