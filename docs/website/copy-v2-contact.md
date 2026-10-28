# Website copy v2: Contact

| Field | Value |
| --- | --- |
| Ticket | HOM-327 (Urgent), stage P, group B |
| Base commit | `8cf5679` (the approved Home page) |
| Date | 2026-10-28 |
| Status | Waiting for review |
| Pages | `/fa/contact`, `/en/contact` |
| How to answer | Name a row ID (`CO-001` to `CO-050`) and say: keep, change to ..., or remove |
| Code | `website/i18n/fa.ts` and `website/i18n/en.ts`: string values only. Same keys and array lengths |

The voice follows the approved Home page (`docs/website/copy-v2-home.md`): every line says what
the buyer gets, no line tells the visitor to scroll or narrates the 3D scene, and planned or
simulated items keep a visible «برنامه‌ریزی‌شده» / "Planned", «شبیه‌سازی» / "Simulation" or
«داده‌ی نمونه» / "sample data" label. The Home glossary applies.

What changed, in short:

- Form labels, errors and the privacy note are interface and legal text. They are plainer, and every fact the tests guard stays: Turnstile, Cloudflare, 180 days, «نسخهٔ ذخیره‌شده را پس از ۱۸۰ روز حذف می‌کنیم», no "not connected".
- `website/components/site/ContactForm.tsx` and `website/lib/contact-schema.ts` have no visible string literals; every label comes from `contact.form`. No change there.
- 50 rows. Persian changed in 18 rows, English in 8 rows. `KEEP` rows are labels,
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
| CO-001 | Head | `contact.meta.title` | تماس و درخواست دمو | KEEP | KEEP | none | Page title. Says what the page is for. |
| CO-002 | Head | `contact.meta.description` | درخواست دموی همایار. از صف تماس‌ها، نحوهٔ ضبط و جایی که داده باید بماند برایمان بگویید تا یک پایلوت را با شما تعریف کنیم. | همایار را با یک تماس نمونه ببینید. از تماس‌های مرکزتان، روش ضبط و جایی که داده باید بماند بگویید تا یک پایلوت را با هم تعریف کنیم. | See Homayar with a sample call. Tell us about your calls, your recording setup and where the data must stay, and we will scope a pilot with you. | CON-01, CON-08 | Opens with what the buyer gets. «عملیات» and «صف تماس‌ها» became the buyer's own words. Glossary «پایلوت». |
| CO-003 | Hero | `contact.hero.eyebrow` | تماس | KEEP | KEEP | none | Short label. |
| CO-004 | Hero | `contact.hero.title` | درخواست دمو | KEEP | KEEP | CON-02 | Keeps: «درخواست دمو» is what the buyer looks for on this page. |
| CO-005 | Hero | `contact.hero.text` | کمی از عملیات خود برایمان بگویید. با ایمیل پاسخ می‌دهیم و یک تماس کوتاه پیشنهاد می‌کنیم. | کمی از مرکز تماستان بگویید. با ایمیل جواب می‌دهیم و یک تماس کوتاه هماهنگ می‌کنیم. | Tell us a little about your contact center. We reply by e-mail and set up a short call. | CON-02, CHR-13 | «عملیات خود» was a calque of "your operation". Same promise as the approved CTA (H-204). |
| CO-006 | Form | `contact.form.name` | نام و نام خانوادگی | KEEP | KEEP | none | Field label. |
| CO-007 | Form | `contact.form.required` | الزامی | KEEP | KEEP | none | Mark next to required fields. |
| CO-008 | Form | `contact.form.email` | ایمیل سازمانی | KEEP | KEEP | none | Field label. |
| CO-009 | Form | `contact.form.company` | شرکت | KEEP | KEEP | none | Field label. |
| CO-010 | Form | `contact.form.phone` | شماره تلفن | KEEP | KEEP | none | Field label. |
| CO-011 | Form | `contact.form.optional` | اختیاری | KEEP | KEEP | none | Mark next to optional fields. |
| CO-012 | Form | `contact.form.role` | نقش شما | KEEP | KEEP | none | Field label. |
| CO-013 | Form | `contact.form.roleOptions[*].label` | یکی را انتخاب کنید / مدیر کیفیت / سرپرست یا سرگروه / عملیات مرکز تماس یا تجربهٔ مشتری / فناوری اطلاعات یا امنیت / سایر | KEEP | KEEP | none | Choices: «یکی را انتخاب کنید», «مدیر کیفیت», «سرپرست یا سرگروه», «عملیات مرکز تماس یا تجربهٔ مشتری», «فناوری اطلاعات یا امنیت», «سایر». Plain role names. |
| CO-014 | Form | `contact.form.deployment` | داده باید کجا بماند؟ | KEEP | KEEP | CON-06 | Field label, a plain question. |
| CO-015 | Form | `contact.form.deploymentOptions[0].label` | یکی را انتخاب کنید | KEEP | KEEP | CON-06 | Empty choice. |
| CO-016 | Form | `contact.form.deploymentOptions[1].label` | SaaS مشکلی ندارد | نسخهٔ ابری هم مشکلی ندارد | Managed cloud is fine | CON-06, OD-04 | Glossary «نسخهٔ ابری» instead of «SaaS». A question to the visitor, not a claim. Longer: see the length section. Longer: fa 16 to 25, en 12 to 21. |
| CO-017 | Form | `contact.form.deploymentOptions[2].label` | روی سرورهای خودمان | KEEP | KEEP | CON-06 | Choice. Plain words. |
| CO-018 | Form | `contact.form.deploymentOptions[3].label` | ترکیبی | KEEP | KEEP | CON-06 | Choice. Glossary «ترکیبی». |
| CO-019 | Form | `contact.form.deploymentOptions[4].label` | هنوز مطمئن نیستم | KEEP | KEEP | CON-06 | Choice. |
| CO-020 | Form | `contact.form.minutes` | دقیقهٔ تماس در ماه | KEEP | KEEP | none | Field label. |
| CO-021 | Form | `contact.form.minutesOptions[*].label` | یکی را انتخاب کنید / کمتر از ۱۰٬۰۰۰ دقیقه / ۱۰٬۰۰۰ تا ۵۰٬۰۰۰ دقیقه / ۵۰٬۰۰۰ تا ۲۵۰٬۰۰۰ دقیقه / ۲۵۰٬۰۰۰ تا ۱٬۰۰۰٬۰۰۰ دقیقه / بیش از ۱٬۰۰۰٬۰۰۰ دقیقه | KEEP | KEEP | none | Choices from «کمتر از ۱۰٬۰۰۰ دقیقه» to «بیش از ۱٬۰۰۰٬۰۰۰ دقیقه». Numbers, already plain. Values are fixed by the schema test. |
| CO-022 | Form | `contact.form.message` | نکتهٔ دیگری هست که باید بدانیم؟ | KEEP | KEEP | none | Field label, a natural question. |
| CO-023 | Form | `contact.form.messageHint` | اختیاری. مثلاً مرکز تلفن شما، زبان‌های تماس‌ها یا یک مهلت زمانی. | اختیاری. مثلاً مرکز تلفنتان، زبان تماس‌ها یا تاریخی که باید کار راه بیفتد. | Optional. For example your PBX, the languages of your calls, or a date you need it by. | none | «یک مهلت زمانی» was formal. Says what kind of date helps us. Longer: fa 64 to 74. |
| CO-024 | Form | `components/site/Turnstile.tsx` | (Cloudflare Turnstile box) | KEEP | KEEP | CON-04 | The Cloudflare Turnstile box. Its text comes from Cloudflare in the page language; not our copy. |
| CO-025 | Form | `contact.form.submit` | ارسال درخواست | KEEP | KEEP | none | Button label. The form test finds the button by this text. |
| CO-026 | Form | `contact.form.sending` | در حال ارسال | KEEP | KEEP | none | Button label while sending. |
| CO-027 | Form | `contact.form.privacyNote` | فقط چیزی را دریافت می‌کنیم که اینجا می‌نویسید. Cloudflare Turnstile بررسی می‌کند که شما انسان هستید و این بررسی را Cloudflare انجام می‌دهد. درخواست شما در Cloudflare ذخیره می‌شود و فقط برای پاسخ به شما به تیم همایار ایمیل می‌شود. نسخهٔ ذخیره‌شده را پس از ۱۸۰ روز حذف می‌کنیم و شما را به هیچ فهرست بازاریابی اضافه نمی‌کنیم. | فقط همین چیزهایی را می‌گیریم که اینجا می‌نویسید. برای اینکه مطمئن شویم شما انسان هستید، از بررسی Cloudflare Turnstile استفاده می‌کنیم که Cloudflare آن را اجرا می‌کند. درخواستتان در Cloudflare ذخیره می‌شود و فقط برای اینکه جوابتان را بدهیم، به تیم همایار ایمیل می‌شود. نسخهٔ ذخیره‌شده را پس از ۱۸۰ روز حذف می‌کنیم و شما را به هیچ فهرست بازاریابی اضافه نمی‌کنیم. | KEEP | CON-04 | Legal note. Same five facts. We are the subject of the check sentence (the old one said «Cloudflare» twice in a row). Keeps Turnstile, Cloudflare and the exact 180-day sentence the tests guard. |
| CO-028 | Form states | `contact.form.turnstileWaiting` | منتظر پایان بررسی امنیتی هستیم. درخواست شما بلافاصله پس از آن فرستاده می‌شود. | بررسی امنیتی هنوز تمام نشده است. همین که تمام شود، درخواستتان فرستاده می‌شود. | KEEP | none | Status text. «منتظر پایان ... هستیم» was a calque. Same meaning. |
| CO-029 | Form states | `contact.form.errorsTitle` | لطفاً موارد زیر را اصلاح کنید | KEEP | KEEP | none | Error summary title. Polite and clear. |
| CO-030 | Form states | `contact.form.errors.name` | نام و نام خانوادگی خود را وارد کنید. | KEEP | KEEP | none | Error message. |
| CO-031 | Form states | `contact.form.errors.email` | یک ایمیل سازمانی معتبر وارد کنید. | KEEP | KEEP | none | Error message. |
| CO-032 | Form states | `contact.form.errors.company` | نام شرکت خود را وارد کنید. | KEEP | KEEP | none | Error message. |
| CO-033 | Form states | `contact.form.errors.phone` | فقط رقم، فاصله، +، - و پرانتز بنویسید؛ حداکثر ۳۲ نویسه. | فقط رقم، فاصله، +، - و پرانتز بنویسید؛ حداکثر ۳۲ کاراکتر. | KEEP | none | «نویسه» is an academic word. Same limit (`contact-schema.ts:15`). |
| CO-034 | Form states | `contact.form.errors.deployment` | انتخاب کنید داده باید کجا بماند. | انتخاب کنید داده کجا بماند. | KEEP | none | The field is a select, so the verb is «انتخاب کنید». «باید» dropped; shorter. |
| CO-035 | Form states | `contact.form.errors.minutes` | یکی از گزینه‌های فهرست را انتخاب کنید. | KEEP | KEEP | none | Error message. |
| CO-036 | Form states | `contact.form.turnstileFailed` | بررسی امنیتی پذیرفته نشد. لطفاً آن را دوباره انجام دهید و سپس فرم را بفرستید. | بررسی امنیتی تأیید نشد. لطفاً دوباره انجامش دهید و بعد فرم را بفرستید. | KEEP | none | Error message. «پذیرفته نشد» and «سپس» were formal. |
| CO-037 | Form states | `contact.form.turnstileLoadFailed` | بررسی امنیتی بارگذاری نشد. اتصال اینترنت یا مسدودکنندهٔ تبلیغات را بررسی کنید و صفحه را دوباره بارگذاری کنید. | بررسی امنیتی باز نشد. اتصال اینترنت یا افزونهٔ ضدتبلیغ مرورگر را بررسی کنید و صفحه را دوباره باز کنید. | KEEP | none | Error message. «مسدودکنندهٔ تبلیغات» is a calque of "ad blocker"; «بارگذاری» repeated twice. |
| CO-038 | Form states | `contact.form.turnstileError` | بررسی امنیتی با مشکل روبه‌رو شد. «تلاش دوباره» را بزنید تا دوباره اجرا شود و سپس درخواست شما فرستاده می‌شود. | بررسی امنیتی به مشکل خورد. «تلاش دوباره» را بزنید؛ بررسی دوباره اجرا می‌شود و بعد درخواستتان فرستاده می‌شود. | KEEP | none | Error message. Shorter clauses. It still names the button by its label (`contact.form.retry`). |
| CO-039 | Form states | `contact.form.retry` | تلاش دوباره | KEEP | KEEP | none | Button label. Named in the line above. |
| CO-040 | Form states | `contact.form.failedTitle` | درخواست ارسال نشد | KEEP | KEEP | none | Error title. |
| CO-041 | Form states | `contact.form.failedText` | لطفاً کمی بعد دوباره تلاش کنید. | KEEP | KEEP | none | Error text. |
| CO-042 | Form states | `contact.form.successTitle` | درخواست دریافت شد | درخواست شما رسید | KEEP | CON-07 | «دریافت شد» is formal. This is how a person says it. |
| CO-043 | Form states | `contact.form.successText` | سپاسگزاریم. درخواست شما به دست ما رسید و به ایمیلی که دادید پاسخ می‌دهیم. | ممنون. به ایمیلی که نوشتید جواب می‌دهیم. | Thank you. We will reply to the e-mail address you gave. | CON-03 | The title already says it arrived; the text no longer repeats it. |
| CO-044 | Form states | `contact.form.referenceLabel` | شمارهٔ پیگیری شما | KEEP | KEEP | none | Label before the reference number. |
| CO-045 | What happens next | `contact.aside.title` | بعد از ارسال چه می‌شود | KEEP | KEEP | CON-05 | Heading. The buyer's own question. |
| CO-046 | What happens next | `contact.aside.steps[0]` | با ایمیل پاسخ می‌دهیم، معمولاً ظرف چند روز کاری. | با ایمیل جواب می‌دهیم، معمولاً ظرف چند روز کاری. | KEEP | CON-08 | «جواب» as in the hero text, so the same promise reads the same way. |
| CO-047 | What happens next | `contact.aside.steps[1]` | یک تماس کوتاه برای شناخت صف تماس‌ها، دسترسی به فایل‌های صوتی و محدودیت‌های داده. | در یک تماس کوتاه، از تماس‌های مرکزتان، دسترسی به فایل‌های صوتی و محدودیت‌های داده می‌پرسیم. | On a short call, we ask about your calls, your access to recordings and your data constraints. | CON-08 | A sentence with a verb. «شناخت صف تماس‌ها» was abstract. Longer: en 77 to 94. |
| CO-048 | What happens next | `contact.aside.steps[2]` | نمایش کنسول با یک تماس نمونه، از جمله اینکه نتایج ناقص چه شکلی دارند. | پنل همایار را با یک تماس نمونه نشانتان می‌دهیم، حتی اینکه نتیجهٔ ناقص چه شکلی دارد. | We show you the Homayar console with a sample call, including how a partial result looks. | CON-08, Q-01 | Glossary «پنل» (Q-01). A sentence with a verb instead of «نمایش کنسول». Longer: fa 69 to 83. |
| CO-049 | What happens next | `contact.aside.steps[3]` | اگر مناسب بود، پیشنهاد یک پایلوت با دامنهٔ مشخص و معیارهای موفقیت توافق‌شده. | اگر همایار به کارتان آمد، یک پایلوت با محدودهٔ روشن و معیارهای موفقیتِ توافق‌شده پیشنهاد می‌کنیم. | If Homayar fits, we propose a pilot with a clear scope and success criteria we agree on. | CON-08 | A sentence with a verb instead of «پیشنهاد یک پایلوت». Glossary «پایلوت». Longer: fa 76 to 97, en 65 to 88. |
| CO-050 | What happens next | `contact.aside.linksTitle` | پیش از تماس مفید است | پیش از تماس بخوانید | KEEP | none | Small label over three links. Says what to do. English keeps: "Worth reading before the call" breaks the length rule. |

## Length rule

Tight slots (eyebrows, hints, chips, labels, buttons, links, the role text in the 3D role panels,
the heading and text of the pinned day cards, the layer scene, the masking title, form labels and choices) are within 15 percent of the old line in both
languages, except the rows below.

| Row | Key | Characters | How it looks |
| --- | --- | --- | --- |
| CO-016 | `contact.form.deploymentOptions[1].label` | fa 16 to 25 | An option in a native select menu. The menu is as wide as the form column, so the text fits on one line on phones and desktops. |
| CO-016 | `contact.form.deploymentOptions[1].label` | en 12 to 21 | An option in a native select menu. The menu is as wide as the form column, so the text fits on one line on phones and desktops. |

Lines outside tight slots that got longer by more than 15 percent are listed below. They are
body text, list items and meta descriptions that wrap freely. Each one has the same note at the end
of its Reason.

| Row | Key | Characters |
| --- | --- | --- |
| CO-023 | `contact.form.messageHint` | fa 64 to 74 |
| CO-047 | `contact.aside.steps[1]` | en 77 to 94 |
| CO-048 | `contact.aside.steps[2]` | fa 69 to 83 |
| CO-049 | `contact.aside.steps[3]` | fa 76 to 97 |
| CO-049 | `contact.aside.steps[3]` | en 65 to 88 |

## Component files

- `website/components/site/Turnstile.tsx` (CO-024): The Cloudflare Turnstile box. Its text comes from Cloudflare in the page language; not our copy.
