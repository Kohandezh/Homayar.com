# Website copy v2: Legal (privacy, terms, GDPR) and transparency

| Field | Value |
| --- | --- |
| Ticket | HOM-327 (Urgent), stage P, group B |
| Base commit | `8cf5679` (the approved Home page) |
| Date | 2026-10-28 |
| Status | Waiting for review |
| Pages | `/fa/legal/privacy`, `/fa/legal/terms`, `/fa/legal/gdpr`, `/fa/transparency` (and `/en/...`) |
| How to answer | Name a row ID (`LG-001` to `LG-019`) and say: keep, change to ..., or remove |
| Code | `website/i18n/fa.ts` and `website/i18n/en.ts`: string values only, plus F1 (below). Same keys and array lengths, except F1 |

The voice follows the approved Home page (`docs/website/copy-v2-home.md`): every line says what
the buyer gets, no line tells the visitor to scroll or narrates the 3D scene, and planned or
simulated items keep a visible «برنامه‌ریزی‌شده» / "Planned", «شبیه‌سازی» / "Simulation" or
«داده‌ی نمونه» / "sample data" label. The Home glossary applies.

## F1: the footer note moved to the privacy page

The owner asked to remove the long paragraph under the logo in the footer (2026-10-28). Its true
facts now live on the privacy page.

| What | Before | After |
| --- | --- | --- |
| Footer, fa | همایار فقط فایل‌های صوتی تماسی را تحلیل می‌کند که مجوز پردازش آن‌ها را دارید. این وب‌سایت جز بررسی Cloudflare Turnstile در صفحهٔ تماس، هیچ اسکریپت شخص ثالثی بارگذاری نمی‌کند و کوکی ردیابی نمی‌گذارد. | Removed |
| Footer, en | Homayar analyzes call recordings you are authorized to process. This website loads no third-party scripts, except the Cloudflare Turnstile check on the contact page, and sets no tracking cookies. | Removed |
| Privacy page, fa | (nothing) | Heading «آنچه همین امروز برقرار است», text «همایار فقط فایل‌های صوتی تماس‌هایی را تحلیل می‌کند که مجوز پردازششان را دارید. این وب‌سایت جز بررسی Cloudflare Turnstile در صفحهٔ تماس، هیچ اسکریپت شخص ثالثی بارگذاری نمی‌کند و کوکی ردیابی نمی‌گذارد. درخواستی که با فرم تماس می‌فرستید ذخیره می‌شود؛ یادداشت کنار فرم می‌گوید کجا و تا چه مدت.» |
| Privacy page, en | (nothing) | Heading "What holds today", text "Homayar analyzes only the call recordings you are authorized to process. This website loads no third-party scripts except the Cloudflare Turnstile check on the contact page, and it sets no tracking cookies. A request you send through the contact form is stored; the note next to the form says where and for how long." |

- **Facts.** The same three facts as the footer line, backed by CHR-02 (the footer row in "Shared
  chrome" of `docs/website/claim-register.md`). One more sentence says the contact form stores a
  request and points to the note next to the form (CON-04, 180 days). Without it, «no tracking
  cookies, no third-party script» could be read as «this website stores nothing».
- **Not a policy.** The block sits below the «در حال آماده‌سازی» / "Being prepared" notice, in its
  own section, not inside it. The reader first learns the policy is not in force. The heading names
  only this website, not the policy.
- **Code.** `website/components/site/Footer.tsx:50`: the `<p>` with `dict.footer.note` is removed.
  Nothing else in the footer changed (logo, brand line, four link columns, copyright line).
  `footer.note` is removed from `fa.ts` and `en.ts`. `website/app/[locale]/legal/[doc]/page.tsx`:
  one `<section>` after the notice, rendered only when the page has `site` (only privacy does). It
  reuses existing classes: `text-xl font-bold` for the heading (the contact aside heading) and
  `text-ink-2` for the text. New key `legal.privacy.site` with `title` and `text` in both
  locales. The key-shape test still passes.
- **Test.** `website/tests/contact/dictionary.test.ts`: the test that asserted Turnstile in
  `footer.note` now asserts the same facts in `legal.privacy.site.text` (Turnstile on the
  contact page, no tracking cookies) in both locales, and that `footer` has no `note` key.
  Nothing else in the tests changed.
- **Checked on the built page** (`pnpm build` output, `.next/server/app/{fa,en}/legal/*.html` and
  `fa.html`): the footer has no Turnstile text and no empty `<p>`; its first column holds only the
  logo and the brand line; the four link columns (4 headings), their links, the copyright and the
  tagline are there. On `/fa/legal/privacy` and `/en/legal/privacy` the new block comes after the
  notice (`role="note"`), not inside it. Terms and GDPR have no block. Not checked visually in a
  browser.

What changed, in short:

- Legal pages: accuracy over selling. Only two lines changed, both to make the Persian plain. No legal meaning changed.
- F1 is at the top.
- 19 rows. Persian changed in 3 rows, English in 2 rows. `KEEP` rows are labels,
  interface text, legal text, formulas, sample data, or a line that already says what the buyer gets
  (its Reason says why).

## Owner questions

None. Every line uses a claim-register row whose status allows it, or no claim.

No row uses a claim outside `docs/website/claim-register.md`.

## Lines in the order the visitor meets them

`Old fa` is the text at the base. For a component literal, `Key` is `file:line` and `Old fa`
is the visible text. `(shared)` means the key also shows on another page in this doc.
`(not rendered)` means no component shows it. A row with `[*]` covers several keys with the same
reason; they are all `KEEP`.

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LG-001 | Privacy | `legal.privacy.meta.title` | سیاست حریم خصوصی | KEEP | KEEP | none | Page title. |
| LG-002 | Privacy | `legal.privacy.meta.description` | سیاست حریم خصوصی همایار با بازبینی حقوقی در حال آماده‌سازی است و هنوز منتشر نشده است. | KEEP | KEEP | PRIV-02 | Legal notice. Accurate and plain. |
| LG-003 | Privacy | `legal.privacy.title` | سیاست حریم خصوصی | KEEP | KEEP | none | Page heading. |
| LG-004 | Privacy | `legal.privacy.notice` | این سند با بازبینی حقوقی در حال آماده‌سازی است و هنوز لازم‌الاجرا نیست. توضیح خواهد داد که این وب‌سایت و محصول همایار چه داده‌ای را، بر چه مبنایی، برای چه مدتی پردازش می‌کنند و با چه کسی باید تماس گرفت. تا زمان انتشار، برای شرایط فعلی مدیریت داده با ما تماس بگیرید. | این سند با بازبینی حقوقی در حال آماده‌سازی است و هنوز لازم‌الاجرا نیست. در آن خواهیم گفت این وب‌سایت و محصول همایار چه داده‌ای را، بر چه مبنایی و تا چه مدت پردازش می‌کنند و برای پرسش‌ها با چه کسی تماس بگیرید. تا زمان انتشار، برای شرایط فعلی مدیریت داده با ما تماس بگیرید. | KEEP | PRIV-01 | Legal notice. Only the second sentence changed: «توضیح خواهد داد که» and «با چه کسی باید تماس گرفت» were stiff. Same legal meaning: not in force, same four topics. |
| LG-005 | Privacy | `legal.privacy.site.title` (new key) | (new key) | آنچه همین امروز برقرار است | What holds today | CHR-02 | New key (F1). Heading of the website facts that moved here from the footer. Shown below the «در حال آماده‌سازی» notice, in its own block, so the reader first learns the policy is not in force. Neutral: it claims neither completeness nor a policy in force. |
| LG-006 | Privacy | `legal.privacy.site.text` (new key) | (new key) | همایار فقط فایل‌های صوتی تماس‌هایی را تحلیل می‌کند که مجوز پردازششان را دارید. این وب‌سایت جز بررسی Cloudflare Turnstile در صفحهٔ تماس، هیچ اسکریپت شخص ثالثی بارگذاری نمی‌کند و کوکی ردیابی نمی‌گذارد. درخواستی که با فرم تماس می‌فرستید ذخیره می‌شود؛ یادداشت کنار فرم می‌گوید کجا و تا چه مدت. | Homayar analyzes only the call recordings you are authorized to process. This website loads no third-party scripts except the Cloudflare Turnstile check on the contact page, and it sets no tracking cookies. A request you send through the contact form is stored; the note next to the form says where and for how long. | CHR-02, CON-04 | New key (F1). The three facts of the old `footer.note`, no more. The last sentence is there so the block cannot be read as «this website stores nothing»: the contact form keeps a request for 180 days (`contact.form.privacyNote`, CON-04). It points to that note instead of repeating it, so the two can never disagree. |
| LG-007 | Terms | `legal.terms.meta.title` | شرایط استفاده | KEEP | KEEP | none | Page title. |
| LG-008 | Terms | `legal.terms.meta.description` | شرایط استفادهٔ همایار با بازبینی حقوقی در حال آماده‌سازی است و هنوز منتشر نشده است. | KEEP | KEEP | TERMS-02 | Legal notice. |
| LG-009 | Terms | `legal.terms.title` | شرایط استفاده | KEEP | KEEP | none | Page heading. |
| LG-010 | Terms | `legal.terms.notice` | این سند با بازبینی حقوقی در حال آماده‌سازی است و هنوز لازم‌الاجرا نیست. هیچ شرطی در این صفحه الزام‌آور نیست. برای پرسش‌ها با ما تماس بگیرید. | KEEP | KEEP | TERMS-01 | Legal notice. Plain and exact. Changing it adds risk and no clarity. |
| LG-011 | GDPR | `legal.gdpr.meta.title` | بیانیهٔ GDPR | KEEP | KEEP | none | Page title. |
| LG-012 | GDPR | `legal.gdpr.meta.description` | بیانیهٔ GDPR همایار در حال آماده‌سازی است و هنوز منتشر نشده است. | KEEP | KEEP | GDPR-02 | Legal notice. |
| LG-013 | GDPR | `legal.gdpr.title` | بیانیهٔ GDPR | KEEP | KEEP | none | Page heading. |
| LG-014 | GDPR | `legal.gdpr.notice` | بیانیهٔ ما دربارهٔ مقررات عمومی حفاظت از دادهٔ اتحادیهٔ اروپا در حال آماده‌سازی است. پس از بازبینی، نقش‌ها، مبانی قانونی، درخواست‌های صاحبان داده و انتقال‌های بین‌المللی را شرح خواهد داد. هیچ‌چیز در این صفحه ادعای انطباق نیست. | KEEP | KEEP | GDPR-01 | Legal notice. It uses the standard Persian names of GDPR terms; plainer words would change their legal meaning. |
| LG-015 | Transparency | `transparency.meta.title` | گزارش شفافیت | KEEP | KEEP | none | Page title. |
| LG-016 | Transparency | `transparency.meta.description` | گزارش شفافیت همایار در حال آماده‌سازی است و هنوز منتشر نشده است. | KEEP | KEEP | TRANS-02 | Notice. |
| LG-017 | Transparency | `transparency.title` | گزارش شفافیت | KEEP | KEEP | none | Page heading. |
| LG-018 | Transparency | `transparency.notice` | این گزارش در حال آماده‌سازی است. شرح خواهد داد پردازش چطور انجام می‌شود، در هر گزینهٔ استقرار کدام پردازشگرهای فرعی درگیرند و درخواست‌های مربوط به داده چطور رسیدگی می‌شوند. هنوز منتشر نشده و هیچ تعهدی در آن نیست. | KEEP | KEEP | TRANS-01 | Notice. «پردازشگرهای فرعی» is the legal term for subprocessors; it stays. |
| LG-019 | Transparency | `transparency.backLink` | در این فاصله صفحهٔ امنیت را بخوانید | KEEP | KEEP | none | Button label. Clear, and a longer form breaks the length rule. |

## Length rule

Tight slots (eyebrows, hints, chips, labels, buttons, links, the role text in the 3D role panels,
the heading and text of the pinned day cards, the layer scene, the masking title, form labels and choices) are within 15 percent of the old line in both
languages.

No line outside a tight slot got longer by more than 15 percent.

## Component files

This page has no visible string literal in a component.
