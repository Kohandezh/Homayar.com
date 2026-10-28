# Website copy v2: Home page

| Field | Value |
| --- | --- |
| Ticket | HOM-327 (Urgent), stage H1 |
| Base commit | `e111e669c2a8a08c863e11616db6b70c596afc51` |
| Date | 2026-10-28 |
| Status | Approved by the owner on 2026-10-28. Stage H2 changes applied (see "After the owner's approval") |
| See it | `http://localhost:4960/fa` and `http://localhost:4960/en` |
| Rows | `H-001` to `H-219`. The owner's answers to `Q-01` to `Q-10` are in the last column of the owner questions |
| Code | `website/i18n/fa.ts` and `website/i18n/en.ts`: string values, plus one new array `home.deployment.modes` (3 strings, both locales). `website/app/[locale]/page.tsx:128`: the label text only (Addendum 2, E1). No other key, array length, component or scene changed |

The owner approved the Home page on 2026-10-28 ("excellent, go ahead the same way"). Its style is now
written down in `docs/website/copy-style.md`, the contract for every other page (stage P).

What changed, in short:

- Every line now says what the buyer gets. No line tells the visitor to scroll, narrates the 3D
  scene, or describes the system's internals.
- The owner's three examples: `scene.warp.title` (H-040) now carries the owner's own direction,
  `scene.warp.text` (H-041) is a benefit instead of a stage direction, and `scene.brain.title`
  (H-043) leads with the buyer instead of «هستهٔ همایار».
- The three deployment cards had raw English ids (saas, self-hosted, hybrid) as labels on `/fa` too.
  They now come from a new key `home.deployment.modes` (H-167 to H-169). This is the one code
  change outside the dictionary (`website/app/[locale]/page.tsx:128`, SPEC Addendum 2, E1).
- 21 keys under `home` are not shown by any component. They are not changed now and are listed
  at the end of this document. Stage P decides on them.
- Planned features keep a visible «برنامه‌ریزی‌شده» / "Planned" label, and simulated screens keep
  «شبیه‌سازی» / «داده‌ی نمونه» / "Simulation" / "sample data".
- 219 rows. Persian changed in 89 rows, English in 88 rows. `KEEP` rows are
  interface text, legal notices, sample data inside the simulations, or a marketing line that
  already sells (its Reason says why).

## Owner questions

| ID | Question | Rows | Evidence | Applied now | Options | Owner answer (2026-10-28) |
| --- | --- | --- | --- | --- | --- | --- |
| Q-01 | Which word for the product's web app: «پنل» or «کنسول»? Iranian buyers say «پنل»; «کنسول» sounds technical. | H-097, H-099, H-100 | Term choice, no claim | «پنل» in Persian. English keeps "console" | (a) Keep «پنل». (b) Go back to «کنسول». | (a) «پنل». Kept |
| Q-02 | Which word for conversation: «مکالمه» (your example «تحلیل مکالمات») or «گفت‌وگو» (the product console's word)? | H-001, H-002, H-036, H-040, H-046, H-048, and every line with «متن مکالمه» | Console: `Homayar/frontend/src/components/admin/privacy/privacy-copy.ts:223` uses «گفت‌وگو» | «مکالمه» everywhere on the home page | (a) Keep «مکالمه». (b) Use «گفت‌وگو», as in the console. | (a) «مکالمه». Kept |
| Q-03 | The hero opens with «به همهٔ تماس‌ها نمی‌رسید؟ / همایار می‌رسد.» ("Can't get to every call? Homayar can."). Today calls reach Homayar by upload; direct intake from the phone system is planned. Is the promise fair? | H-033, H-034 | Uploads: HOME-08. Planned PBX intake: HOME-18, HOME-28. Supervisor cannot listen to everything: SUP-03. Slogan "Every call, an insight": HOME-10 | Used as written | (a) Keep. (b) Change H-034 to «همایار کمکتان می‌کند.» / "Homayar helps." | (a) Keep. Kept |
| Q-04 | The warp title follows your direction: «تحلیل مکالمه‌ها، بعد از پایان تماس». It does not say the analysis starts by itself when the call ends, because that needs the planned PBX intake. Is this wording right? | H-040 | Post-call product: CHR-01. Planned PBX intake: HOME-18 | Used as written | (a) Keep. (b) Your exact words «شروع تحلیل مکالمات بعد از اتمام تماس». | (a) Keep. Kept |
| Q-05 | The share image shows the Latin name "Homayar" on `/fa` too. It is one literal for both languages, so a Persian name needs a small code change. | H-005 | `website/app/[locale]/opengraph-image/[id]/route.tsx:72` | Not changed | (a) Leave it. (b) Show «همایار» on `/fa` (code change in stage P). | (b) Show «همایار» on `/fa`. Done in stage P, in the share-image route |
| Q-06 | The three deployment cards now carry a small label that says who runs it: «ما اجرا می‌کنیم»، «شما اجرا می‌کنید»، «شما، با ابر اختیاری» / We run it, You run it, You run it, plus cloud. The words come from the pricing page. They replace the raw ids saas, self-hosted, hybrid, and they no longer repeat the card title. Are these right? | H-167, H-168, H-169 | `website/app/[locale]/page.tsx:128` (label); `pricing.deployments` in `website/i18n/fa.ts` uses «ما اجرا می‌کنیم» and «شما اجرا می‌کنید»; SPEC Addendum 2, E1 | Applied | (a) Keep. (b) Short mode names instead («ابری»، «روی سرور»، «ترکیبی»), which repeat the titles. | (a) Keep. Kept |
| Q-07 | Which word for a contact-center agent: «کارشناس» (the console's word) or «اپراتور» (the old site word)? The change also shows on the product and how-it-works pages, which share these keys. | H-050, H-066, H-130, and every line with «کارشناس» | Console: `Homayar/frontend/src/components/admin/teams/teams-copy.ts:96` | «کارشناس» | (a) Keep «کارشناس». (b) Go back to «اپراتور». | (a) «کارشناس». Kept |
| Q-08 | The old hero line «با استفاده از هوش مصنوعی.» is gone; AI is now named in the meta description only. Do you want AI named in the hero? | H-037, H-002 | AI analysis: HOME-06 | AI in the meta description only | (a) Keep. (b) Bring AI back into a hero line, for example «با هوش مصنوعی، روی سرور شما.» | (b) AI back into a hero line. Applied in H2: H-037 is «با هوش مصنوعی، روی سرور شما.» / "With AI, on your own servers." |
| Q-09 | The particle slogan «هر تماس، یک بینش» / "Every call, an insight." is kept. «بینش» is a common translated business word. Keep it, or choose a plainer word? | H-031 | Insights per call: HOME-10 | Kept | (a) Keep. (b) «هر تماس، یک درس» / "Every call, a lesson." (fits the dot text). | (a) Keep «هر تماس، یک بینش». Kept |
| Q-10 | The brand line under the logo changed from «هوش صوتی پس از تماس» / "Post-call voice intelligence" to «تحلیل پس از تماس» / "Post-call analysis". It also shows in the footer and on the `/en` share image. | H-009 | Post-call product: CHR-01 | Applied | (a) Keep the new line. (b) Go back to «هوش صوتی پس از تماس» / "Post-call voice intelligence". | (a) Keep «تحلیل پس از تماس». Kept |

No row uses a claim outside the register except the framing in Q-03 and Q-04. One line is a little
stronger than its register row: «مستند به متن مکالمه» / "Backed by the transcript"
(H-036) says more than "linked to the transcript" (HOME-05). Every point links to its
source line, so it stays; its Reason gives the softer option.

## After the owner's approval (stage H2)

| Row | Change | Why |
| --- | --- | --- |
| H-037 | «روی سرور خودتان.» became «با هوش مصنوعی، روی سرور شما.»; "On your own servers." became "With AI, on your own servers." | Owner answer Q-08 (b) |
| H-041 | Starts with the result («خلاصه و اقدام‌ها را بخوانید ...»); English "Get the summary and the action items, and decide without hearing the whole call." | Review 2: the eyebrow and the body both used «به‌جای» |
| H-147 | «... و تا امروز چند دقیقه صدا تحلیل شده است.»; English "how many minutes of audio" | Review 2: the old ending stacked nouns. The console shows audio in minutes |
| H-056, H-085, H-110 to H-117, H-124, H-061, H-126 | English sample labels in sentence case ("Call report", "Churn risk", "Total calls", ...) | Review 2: Title Case next to sentence case on the same page |
| H-211 | Marked `REMOVE` | Owner, 2026-10-28 (Addendum 4). The facts move to the privacy page. Team t2 makes the code change |

## Lines in the order the visitor meets them

Sections: Head (title, description, share image), Header (and mobile menu), Particle hero, Flight
hero, Warp band, Brain scene, Planned report, Console, Deployment flow, Trust, Command CTA,
Footer and Page loader. `(shared)` means the key also shows on other pages, which
change with it. `Old fa` is the text at the base. For a component literal, `Old fa` is the visible
text and `Key` is `file:line`.

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H-001 | Head | `home.meta.title` | همایار: بازبینی پس از تماس برای مراکز تماس | همایار: تحلیل مکالمه‌های مرکز تماس | Homayar: post-call analysis for contact centers | HOME-01, CHR-01 | Says what the buyer gets in their own words. «بازبینی پس از تماس» named a process, not a benefit. |
| H-002 | Head | `home.meta.description` | همایار فایل‌های صوتی تماس‌های مجاز را به متن تبدیل می‌کند و به تیم شما خلاصه، اقدام‌ها و بینش‌هایی می‌دهد که به متن مکالمه وصل‌اند. امتیاز کیفیت و آموزش برنامه‌ریزی‌شده‌اند. فارسی و انگلیسی، روی سرور خودتان؛ نسخهٔ ابری مدیریت‌شده برنامه‌ریزی‌شده است. | سرپرست‌ها نمی‌توانند به همهٔ تماس‌ها گوش دهند. همایار با هوش مصنوعی از هر تماس متن کامل، خلاصه و اقدام‌ها را آماده می‌کند؛ هر نکته هم به جمله‌ای از مکالمه وصل است که از آن آمده. فارسی و انگلیسی، روی سرور خودتان. | Your supervisors can't listen to every call. Homayar uses AI to turn each recording into a full transcript, a summary and action items, with every point linked to the line it came from. Persian and English, on your own servers. | SUP-03, HOME-02, HOME-06, HOME-24, HOME-07, HOME-32 | Opens with the buyer's problem, then the result. Planned items moved out: a search snippet should sell what exists today. |
| H-003 | Head | `brand` (shared) | همایار | KEEP | KEEP | none | Brand name. Also the application name, the OpenGraph site name and the logo text. |
| H-004 | Head | `titleSuffix` (shared) | همایار | KEEP | KEEP | none | Brand suffix of other page titles. The home title is absolute and does not use it. |
| H-005 | Head | `app/[locale]/opengraph-image/[id]/route.tsx:72` (shared) | Homayar | KEEP | KEEP | none | Latin brand name in the share image, one literal for both locales. A Persian name on /fa needs a code change (Q-05). |
| H-006 | Head | `app/[locale]/opengraph-image/[id]/route.tsx:73` (shared) | homayar.com | KEEP | KEEP | none | Domain on the /fa share image (the /en image shows `tagline`). Not a sales line. |
| H-007 | Head | `app/[locale]/opengraph-image/[id]/route.tsx:76` (shared) | FA / EN | KEEP | KEEP | none | Locale code FA or EN in the corner of the share image. |
| H-008 | Header | `common.skipToContent` (shared) | رفتن به محتوای اصلی | KEEP | KEEP | none | Interface text for keyboard users. |
| H-009 | Header | `tagline` (shared) | هوش صوتی پس از تماس | تحلیل پس از تماس | Post-call analysis | CHR-01, Q-10 | Under the logo, in the footer and on the /en share image. «هوش صوتی» is a translated term; this says plainly what Homayar does. |
| H-010 | Header | `nav.product` (shared) | محصول | KEEP | KEEP | none | Navigation label. Short and clear. |
| H-011 | Header | `nav.howItWorks` (shared) | نحوهٔ کار | KEEP | KEEP | none | Navigation label. Short and clear. |
| H-012 | Header | `nav.solutions` (shared) | راهکارها | KEEP | KEEP | none | Navigation label. |
| H-013 | Header | `nav.security` (shared) | امنیت | KEEP | KEEP | none | Navigation label. |
| H-014 | Header | `nav.deployment` (shared) | استقرار | KEEP | KEEP | none | Navigation label. IT buyers look for this word. |
| H-015 | Header | `nav.pricing` (shared) | قیمت‌گذاری | KEEP | KEEP | none | Navigation label. «قیمت‌ها» would suggest public prices, which the site does not show (OD-05). |
| H-016 | Header | `nav.about` (shared) | دربارهٔ ما | KEEP | KEEP | none | Navigation label. |
| H-017 | Header | `nav.home` (shared) | خانه | KEEP | KEEP | none | Bottom tab label, and the accessible name of the main menu and the tab bar. |
| H-018 | Header | `theme.toDark` (shared) | تغییر به حالت تاریک | KEEP | KEEP | none | Button name for screen readers and tooltip. |
| H-019 | Header | `theme.toLight` (shared) | تغییر به حالت روشن | KEEP | KEEP | none | Button name for screen readers and tooltip. |
| H-020 | Header | `common.switchLanguage` (shared) | Switch to English | KEEP | KEEP | none | Button name in the target language, on purpose. |
| H-021 | Header | `common.switchLanguageShort` (shared) | English | KEEP | KEEP | none | Visible language button, in the target language. |
| H-022 | Header | `common.login` (shared) | ورود | KEEP | KEEP | none | Button label. |
| H-023 | Header | `common.start` (shared) | شروع | KEEP | KEEP | none | Button label. Any longer Persian form breaks the 15 percent limit (4 characters). |
| H-024 | Header | `common.menu` (shared) | باز کردن منو | KEEP | KEEP | none | Button name for screen readers. |
| H-025 | Header | `common.closeMenu` (shared) | بستن منو | KEEP | KEEP | none | Button name for screen readers. |
| H-026 | Header | `nav.contact` (shared) | تماس | KEEP | KEEP | none | Mobile menu and tab label. |
| H-027 | Header | `nav.transparency` (shared) | شفافیت | KEEP | KEEP | none | Mobile menu and footer label. |
| H-028 | Header | `nav.privacy` (shared) | حریم خصوصی | KEEP | KEEP | none | Mobile menu and footer label. |
| H-029 | Header | `nav.terms` (shared) | شرایط استفاده | KEEP | KEEP | none | Mobile menu and footer label. |
| H-030 | Particle hero | `home.particles.brand` | همایار | KEEP | KEEP | none | Brand word formed by the dots. |
| H-031 | Particle hero | `home.particles.slogan` | هر تماس، یک بینش | KEEP | KEEP | HOME-10, Q-09 | Keeps: four words, a promise the buyer understands at once, and it fits the dot text. «یک» here is the number, not a filler. |
| H-032 | Flight hero | `home.hero.title` | فایل‌های صوتی تماس را به بازبینی‌هایی تبدیل کنید که با متن مکالمه قابل بررسی‌اند. | بدانید در هر تماس چه گذشت و چه کاری باید پیگیری شود. | Know what happened on every call, and what needs follow-up. | HOME-02, SUP-05 | The page h1 (visible when motion is off). The buyer's outcome in one sentence, instead of «قابل بررسی‌اند». |
| H-033 | Flight hero | `home.hero.headlines[0]` | تبدیل فایل‌های صوتی تماس‌ها | به همهٔ تماس‌ها نمی‌رسید؟ | Can't get to every call? | none | Big kinetic line, shown alone. Starts with the buyer's daily problem. |
| H-034 | Flight hero | `home.hero.headlines[1]` | به گزارش بازبینی | همایار می‌رسد. | Homayar can. | Q-03 | The answer in two words. Owner question Q-03: today calls arrive by upload. |
| H-035 | Flight hero | `home.hero.headlines[2]` | با خلاصه و اقدام‌ها | خلاصه و اقدام‌ها. | Summaries and action items. | HOME-04 | What the buyer gets, without the stiff «با» chain of the old split sentence. Ends with a period like the other five lines. Keeps the glossary word «اقدام‌ها». |
| H-036 | Flight hero | `home.hero.headlines[3]` | متصل به متن مکالمه | مستند به متن مکالمه. | Backed by the transcript. | HOME-05, HOME-24 | Why the buyer can trust it. Slightly stronger than HOME-05 («linked»): every point links to its source line, so the owner may prefer «وصل به متن مکالمه.». Ends with a period like the others. |
| H-037 | Flight hero | `home.hero.headlines[4]` | با استفاده از هوش مصنوعی. | با هوش مصنوعی، روی سرور شما. | With AI, on your own servers. | HOME-06, HOME-32, Q-08 | Owner answer Q-08 (b): AI back in the hero, in the owner's own words. «سرور شما» is the short form of «روی سرور خودتان», because the full phrase breaks the length rule here. |
| H-038 | Flight hero | `home.hero.headlines[5]` | به فارسی و انگلیسی. | KEEP | KEEP | HOME-07 | Keeps: short, concrete, and a real reason to choose Homayar in Iran. |
| H-039 | Warp band | `scene.warp.eyebrow` | از فضایی به فضای دیگر | خواندن به‌جای شنیدن | Read, not replay | SUP-03 | Old line named the scene («از فضایی به فضای دیگر»). Now the benefit: read the result instead of replaying the call. |
| H-040 | Warp band | `scene.warp.title` | تماس تمام می‌شود. فضای تحلیل باز می‌شود. | تحلیل مکالمه‌ها، بعد از پایان تماس | Conversations analyzed after the call ends | CHR-01, Q-04 | Owner's direction («شروع تحلیل مکالمات بعد از اتمام تماس»). Does not say the start is automatic (Q-04). |
| H-041 | Warp band | `scene.warp.text` | اسکرول را ادامه دهید. وارد شبکه‌ای می‌شوید که گفت‌وگو را می‌خواند. | خلاصه و اقدام‌ها را بخوانید و بدون گوش دادن به کل تماس تصمیم بگیرید. | Get the summary and the action items, and decide without hearing the whole call. | SUP-03, HOME-02, HOME-24 | Old line was a stage direction and scene narration (owner example 2). Now the outcome for the supervisor. Starts with the result, so it does not repeat «به‌جای» from the eyebrow. |
| H-042 | Brain scene | `scene.brain.eyebrow` | درون تحلیل | تماس نمونه | A sample call | SCN-02 | «درون تحلیل» narrated the camera. Now it says what the visitor reads: a sample call. |
| H-043 | Brain scene | `scene.brain.title` | هستهٔ همایار هر جملهٔ تماس را می‌خواند | از حرف مشتری تا گزارش سرپرست | From the customer's words to your report | HOME-09, HOME-24 | Owner example 3. «هستهٔ همایار» described internals; the new line follows the buyer's call to the buyer's report. |
| H-044 | Brain scene | `scene.brain.text` | یک شبیه‌سازی با تماس نمونه. هر جمله بخشی از شبکه را روشن می‌کند که آن را می‌خواند. در پایان، گزارش به سمت شما می‌آید. | شبیه‌سازی با داده‌ی نمونه: مشتری‌ای که نه روز است سفارشش نرسیده. امتیاز کیفیت، ریشهٔ مشکل و نکتهٔ آموزشی برنامه‌ریزی‌شده‌اند. | A simulation with sample data: a customer whose order is nine days late. The quality scores, root causes and coaching tip are planned. | SCN-02, HOME-13, HOME-14, HOME-16 | Keeps the simulation label and adds the planned label for the report below. Drops the network narration. |
| H-045 | Brain scene | `scene.brain.sample` | شبیه‌سازی: داده‌ی نمونه با نام‌ها و عددهای فرضی | KEEP | KEEP | SCN-03 | Simulation label. Accuracy matters more than selling here. |
| H-046 | Brain scene | `scene.brain.analyzing` | در حال تحلیل گفت‌وگو | در حال تحلیل مکالمه | KEEP | SCN-05 | Status label. Glossary word «مکالمه» for conversation. |
| H-047 | Brain scene | `scene.brain.ready` | گزارش آماده است | KEEP | KEEP | SCN-05 | Status label. Clear as is. |
| H-048 | Brain scene | `scene.brain.conversation` | گفت‌وگوی نمونه | مکالمهٔ نمونه | KEEP | SCN-05 | Accessible name of the sample lines. Glossary word «مکالمه». |
| H-049 | Brain scene | `demo.speakers.customer` (shared) | مشتری | KEEP | KEEP | MOCK-10 | Speaker label in the sample call. |
| H-050 | Brain scene | `demo.speakers.agent` (shared) | اپراتور | کارشناس | KEEP | MOCK-10 | Glossary: «کارشناس» is the word the product console and Iranian contact centers use for an agent. |
| H-051 | Brain scene | `demo.transcript.{fa,en}[0].text` (shared) | سفارشم بعد از نه روز هنوز «در حال ارسال» است. | KEEP | KEEP | MOCK-10 | Sample customer line. /fa shows `transcript.fa`, /en shows `transcript.en`. Reads like a real customer. |
| H-052 | Brain scene | `demo.transcript.{fa,en}[1].text` (shared) | بابت این موضوع متأسفم. همین حالا وضعیت مرسوله را بررسی می‌کنم. | KEEP | KEEP | MOCK-10 | Sample agent line. Natural spoken Persian. |
| H-053 | Brain scene | `demo.transcript.{fa,en}[2].text` (shared) | هفتهٔ پیش هم تماس گرفتم و کسی پیگیری نکرد. | KEEP | KEEP | MOCK-10 | Sample customer line. |
| H-054 | Brain scene | `demo.transcript.{fa,en}[3].text` (shared) | یادداشت را می‌بینم. وضعیت ارسال از طرف ما به‌روز نشده بود. امروز پیگیری می‌کنم و پیش از ساعت شش با شما تماس می‌گیرم. | KEEP | KEEP | MOCK-10 | Sample agent line. |
| H-055 | Brain scene | `demo.transcript.{fa,en}[4].text` (shared) | باشد. لطفاً این بار حتماً. | KEEP | KEEP | MOCK-10 | Sample customer line. |
| H-056 | Brain scene | `demo.report` (shared) | گزارش تماس | KEEP | Call report | MOCK-24 | Heading of the sample report. English: Sentence case, like the rest of the page. |
| H-057 | Brain scene | `demo.sample` (shared) | داده‌ی نمونه | KEEP | KEEP | MOCK-02 | Sample data label. |
| H-058 | Brain scene | `mock.qc` (shared) | کنترل کیفیت | امتیاز کیفیت | Quality score | MOCK-23 | Heading over the sample scores. Glossary: QC scoring is «امتیاز کیفیت» everywhere. |
| H-059 | Brain scene | `mock.scores[0].label` (shared) | امتیاز کلی | KEEP | KEEP | MOCK-15 | Sample score label. |
| H-060 | Brain scene | `mock.scores[1].label` (shared) | همدلی | KEEP | KEEP | MOCK-15 | Sample score label. |
| H-061 | Brain scene | `mock.scores[2].label` (shared) | حل مسئله | KEEP | Problem solving | MOCK-15 | Sample score label. English: Sentence case, like the rest of the page. |
| H-062 | Brain scene | `mock.scores[3].label` (shared) | شفافیت | KEEP | KEEP | MOCK-15 | Sample score label. |
| H-063 | Brain scene | `mock.problems` (shared) | تشخیص مشکل | ریشهٔ مشکل | Root causes | MOCK-23 | Heading over the sample problems. Glossary «ریشهٔ مشکل», the same name as the planned panel. |
| H-064 | Brain scene | `mock.rootCauses[0].label` (shared) | وضعیت ارسال به‌روز نشده | KEEP | KEEP | MOCK-15 | Sample problem. Short and concrete. |
| H-065 | Brain scene | `mock.rootCauses[0].severity` (shared) | زیاد | KEEP | KEEP | MOCK-15 | Severity chip. |
| H-066 | Brain scene | `mock.rootCauses[1].label` (shared) | اپراتور تاریخچهٔ سفارش را نمی‌دید | کارشناس تاریخچهٔ سفارش را نمی‌دید | KEEP | MOCK-15 | Glossary word «کارشناس» for agent. |
| H-067 | Brain scene | `mock.rootCauses[1].severity` (shared) | متوسط | KEEP | KEEP | MOCK-15 | Severity chip. |
| H-068 | Brain scene | `demo.agents.teacher.name` (shared) | بستهٔ آموزشی | آموزش کارشناس | Coaching | MOCK-24, HOME-16 | Glossary: coaching is «آموزش کارشناس». «بستهٔ آموزشی» was a second name for the same idea. |
| H-069 | Brain scene | `demo.agents.teacher.items[1]` (shared) | به‌جای «به‌زودی» یک زمان مشخص برای پیگیری بده. | KEEP | KEEP | MOCK-10 | Sample coaching tip. Natural, concrete Persian. Also step 2 in the planned report below. |
| H-070 | Planned report | `story.eyebrow` | برنامه‌ریزی‌شده: چهار عامل | برنامه‌ریزی‌شده: گزارش کامل | Planned: the full report | HOME-39, OD-02 | Keeps the planned label. «چهار عامل» described internals. |
| H-071 | Planned report | `story.title` | یک متن مکالمه. چهار خوانش. | از هر تماس، چهار پاسخ | One call. Four answers. | HOME-46 | «چهار خوانش» was abstract. The four parts answer the supervisor's four questions (next line). |
| H-072 | Planned report | `story.text` | یک شبیه‌سازی با داده‌ی نمونه. هر عامل برنامه‌ریزی‌شده همان متن را می‌خواند و یک بخش از گزارش را می‌نویسد. | چهار پرسشی که هر سرپرستی دارد: کارشناس چطور بود، مشکل کجا بود، مشتری چه حالی داشت و چه باید آموزش داد. شبیه‌سازی با داده‌ی نمونه؛ جز بینش‌های هر تماس، بقیه برنامه‌ریزی‌شده‌اند. | Four questions every supervisor asks: how did the agent do, what went wrong, how does the customer feel, what should we coach? A simulation with sample data; apart from per-call insights, everything here is planned. | HOME-40, HOME-13, HOME-14, HOME-15, HOME-16, OD-02 | Leads with the supervisor's questions. Keeps the simulation and planned labels, and says insights exist today (HOME-15). Longer: fa 105 to 176, en 110 to 215. |
| H-073 | Planned report | `demo.agents.qc.agent` (shared) | عامل کنترل کیفیت · برنامه‌ریزی‌شده | امتیاز کیفیت · برنامه‌ریزی‌شده | QC scoring · planned | MOCK-04 | Keeps the planned label. Drops «عامل» (internals). Glossary «امتیاز کیفیت». |
| H-074 | Planned report | `home.outputs.items[0].icon` | gauge | KEEP | KEEP | none | Icon name, not text. |
| H-075 | Planned report | `home.outputs.items[0].title` | امتیاز کنترل کیفیت | امتیاز کیفیت هر تماس | A quality score for every call | HOME-13 | Says what the supervisor gets. The planned label sits above it and in the text. Longer: en 21 to 30. |
| H-076 | Planned report | `home.outputs.items[0].text` | برنامه‌ریزی‌شده: همدلی، حل مسئله، شفافیت، لحن، پایان تماس و مدیریت سکوت، برای هر تماس امتیازدهی می‌شود و یک نتیجهٔ کلی دارد. | برنامه‌ریزی‌شده: هر تماس در شش معیار، از همدلی تا مدیریت سکوت، امتیاز می‌گیرد و همهٔ کارشناس‌ها با همین معیارها سنجیده می‌شوند. | Planned: every call is scored on six criteria, from empathy to silence handling, and every agent is judged by the same ones. | HOME-13, QA-04 | A sentence with a verb instead of a list. Adds the buyer's reason: the same criteria for every agent. |
| H-077 | Planned report | `demo.agents.problems.agent` (shared) | تشخیص مشکل · برنامه‌ریزی‌شده | ریشهٔ مشکل · برنامه‌ریزی‌شده | Root causes · planned | MOCK-05 | Same name as the panel title (glossary «ریشهٔ مشکل»). Keeps the planned label. |
| H-078 | Planned report | `home.outputs.items[1].icon` | alert | KEEP | KEEP | none | Icon name, not text. |
| H-079 | Planned report | `home.outputs.items[1].title` | تشخیص مشکل | ریشهٔ مشکل | Root causes | HOME-14 | What a manager wants to know, not the feature name. |
| H-080 | Planned report | `home.outputs.items[1].text` | برنامه‌ریزی‌شده: علت‌های ریشه‌ای، خطاهای سیستمی، شدت مشکل و مشکلاتی که در تماس‌های مختلف تکرار می‌شوند. | برنامه‌ریزی‌شده: می‌بینید علت اصلی هر شکایت چیست، چقدر جدی است و کدام مشکل در تماس‌های دیگر هم تکرار می‌شود. | Planned: see the root cause of each complaint, how serious it is, and which problems keep coming back in other calls. | HOME-14 | Keeps the planned label. The buyer is the subject and gets a verb, instead of a noun list. |
| H-081 | Planned report | `demo.agents.insights.agent` (shared) | بینش کسب‌وکار · برنامه‌ریزی‌شده | بینش‌ها · بخشی برنامه‌ریزی‌شده | Insights · partly planned | MOCK-06, HOME-15 | Same name as the panel title. Insights exist today; the business signals in this panel are planned. |
| H-082 | Planned report | `home.outputs.items[2].icon` | trend | KEEP | KEEP | none | Icon name, not text. |
| H-083 | Planned report | `home.outputs.items[2].title` | بینش کسب‌وکار | بینش از هر تماس | Insights from every call | HOME-15 | Insights exist today; the title says what the buyer gets from each call. Longer: fa 13 to 15, en 16 to 24. |
| H-084 | Planned report | `home.outputs.items[2].text` | بینش‌هایی از همان تماس، هر کدام با میزان اطمینان و خطوطی از متن که پشت آن است. نشانه‌های فروش بیشتر و ریزش مشتری برنامه‌ریزی‌شده‌اند. | بینش‌هایی از خود تماس، هر کدام با میزان اطمینان و جمله‌هایی از مکالمه که پشتوانهٔ آن‌اند. نشانه‌های فروش بیشتر و ریزش مشتری برنامه‌ریزی‌شده‌اند. | Insights drawn from the call itself, each with a confidence level and the lines that back it up. Upsell and churn-risk signals are planned. | HOME-15, OD-03 | Simpler sentence. Keeps the planned part marked. |
| H-085 | Planned report | `demo.agents.insights.items[0].label` (shared) | ریسک از دست دادن مشتری | خطر ریزش مشتری | Churn risk | MOCK-10 | Glossary «ریزش مشتری». «ریسک» is a borrowed word. English: Sentence case, like the rest of the page. |
| H-086 | Planned report | `demo.agents.insights.items[0].value` (shared) | متوسط | KEEP | KEEP | MOCK-10 | Sample value. |
| H-087 | Planned report | `demo.agents.insights.items[1].label` (shared) | فرصت فروش | KEEP | KEEP | MOCK-10 | Sample label. «فرصت فروش بیشتر» would break the length rule in this small slot. |
| H-088 | Planned report | `demo.agents.insights.items[1].value` (shared) | ندارد | KEEP | KEEP | MOCK-10 | Sample value. |
| H-089 | Planned report | `demo.agents.insights.items[2].label` (shared) | روند رضایت | KEEP | KEEP | MOCK-10 | Sample label. |
| H-090 | Planned report | `demo.agents.insights.items[2].value` (shared) | نزولی | KEEP | KEEP | MOCK-10 | Sample value. |
| H-091 | Planned report | `demo.agents.teacher.agent` (shared) | عامل آموزش کیفیت · برنامه‌ریزی‌شده | آموزش کارشناس · برنامه‌ریزی‌شده | Coaching · planned | MOCK-07 | Keeps the planned label. Drops «عامل» (internals). Glossary «آموزش کارشناس». |
| H-092 | Planned report | `home.outputs.items[3].icon` | book | KEEP | KEEP | none | Icon name, not text. |
| H-093 | Planned report | `home.outputs.items[3].title` | بستهٔ آموزشی | آموزش برای هر کارشناس | Coaching for each agent | HOME-16 | Says who it helps. Same idea as the label above, in a full phrase. Longer: fa 12 to 21, en 16 to 23. |
| H-094 | Planned report | `home.outputs.items[3].text` | برنامه‌ریزی‌شده: بستهٔ آموزشی که از نقاط ضعف هر اپراتور ساخته می‌شود، همراه با توصیه‌های رفتاری. | برنامه‌ریزی‌شده: هر کارشناس نکته‌های آموزشی روشن می‌گیرد، بر اساس ضعف‌های خودش. | Planned: each agent gets clear coaching tips, based on their own weak points. | HOME-16 | Keeps the planned label. A sentence with a verb; «توصیه‌های رفتاری» was a calque. |
| H-095 | Planned report | `demo.agents.teacher.items[0]` (shared) | پیش از بررسی سیستم، تماس تکراری مشتری را به رسمیت بشناس. | اول به مشتری نشان بده تماس قبلی‌اش را می‌دانی. | KEEP | MOCK-10 | Sample coaching tip. «به رسمیت بشناس» is a calque of «acknowledge». |
| H-096 | Planned report | `demo.agents.teacher.items[2]` (shared) | تماس را با تأیید قدم بعدی به پایان برسان. | پیش از خداحافظی، قدم بعدی را با مشتری قطعی کن. | KEEP | MOCK-10 | Sample coaching tip. «به پایان برسان» was stiff; this is how a coach speaks. |
| H-097 | Console | `story.surfaces.eyebrow` | کنسول شبیه‌سازی‌شده | پنل شبیه‌سازی‌شده | KEEP | HOME-41, Q-01 | Keeps the simulation label. Glossary «پنل» (Q-01). |
| H-098 | Console | `story.surfaces.title` | کنسولی شبیه‌سازی‌شده با داده‌ی نمونه | تماس‌های اخیر تیم‌تان، در یک صفحه | Your team's recent calls on one screen | PROD-29, HOME-23 | A benefit instead of a label, inside PROD-29 («recent calls»). The simulation label stays in the eyebrow and the text. |
| H-099 | Console | `story.surfaces.text` | این صفحه‌ها با داده‌ی نمونه ساخته شده‌اند. امتیاز کیفیت، حوادث، تحلیل‌ها، دستیار هوشمند و گراف برنامه‌ریزی‌شده‌اند و امروز در کنسول نیستند. | این تصویرها شبیه‌سازی با داده‌ی نمونه‌اند. امتیاز کیفیت، حوادث، تحلیل‌ها، دستیار هوشمند و گراف برنامه‌ریزی‌شده‌اند و امروز در پنل نیستند. | These screens are a simulation with sample data. QC scores, incidents, analytics, the copilot and the graph are planned and not in the console yet. | HOME-42, PROD-30, HOME-13, HOME-29, MOCK-16 | Same honest facts, shorter and plainer. Cites the rows that allow each part; HOME-43 is `REMOVE` (its old sentence was removed), though its new text matches this line. |
| H-100 | Console | `common.decorativeMockup` (shared) | کنسول شبیه‌سازی‌شدهٔ همایار با داده‌ی نمونه | پنل شبیه‌سازی‌شدهٔ همایار با داده‌ی نمونه | KEEP | CHR-09, Q-01 | Accessible name of the console and phone pictures. Glossary «پنل». |
| H-101 | Console | `components/mockups/AppWindow.tsx:19` (shared) | app.homayar.local/overview | KEEP | KEEP | none | Made-up address bar of the sample window. Not a sales line. |
| H-102 | Console | `mock.nav[0]` (shared) | داشبورد | KEEP | KEEP | MOCK-16 | Sample menu label. |
| H-103 | Console | `mock.nav[1]` (shared) | تماس‌ها | KEEP | KEEP | MOCK-16 | Sample menu label. |
| H-104 | Console | `mock.nav[2]` (shared) | حوادث | KEEP | KEEP | MOCK-16 | Sample menu label. |
| H-105 | Console | `mock.nav[3]` (shared) | تحلیل‌ها | KEEP | KEEP | MOCK-16 | Sample menu label. |
| H-106 | Console | `mock.nav[4]` (shared) | دستیار هوشمند | KEEP | KEEP | MOCK-16 | Sample menu label. |
| H-107 | Console | `mock.nav[5]` (shared) | گراف | KEEP | KEEP | MOCK-16 | Sample menu label. |
| H-108 | Console | `mock.nav[6]` (shared) | تنظیمات | KEEP | KEEP | MOCK-16 | Sample menu label. |
| H-109 | Console | `mock.workspace` (shared) | صف نمونه | KEEP | KEEP | MOCK-11 | Simulation label in the window header. |
| H-110 | Console | `mock.kpis[0].label` (shared) | کل تماس‌ها | KEEP | Total calls | MOCK-15 | Sample card label. English: Sentence case, like the rest of the page. |
| H-111 | Console | `mock.kpis[0].value` (shared) | ۱٬۲۸۴ | KEEP | KEEP | MOCK-15 | Invented sample number. |
| H-112 | Console | `mock.kpis[1].label` (shared) | میانگین امتیاز کیفیت | KEEP | Avg QC score | MOCK-15 | Sample card label. Already uses «امتیاز کیفیت». English: Sentence case, like the rest of the page. |
| H-113 | Console | `mock.kpis[1].value` (shared) | ۷۸ | KEEP | KEEP | MOCK-15 | Invented sample number. |
| H-114 | Console | `mock.kpis[1].unit` (shared) | / ۱۰۰ | KEEP | KEEP | MOCK-15 | Unit of the sample number. |
| H-115 | Console | `mock.kpis[2].label` (shared) | مسائل باز | KEEP | Open issues | MOCK-15 | Sample card label. English: Sentence case, like the rest of the page. |
| H-116 | Console | `mock.kpis[2].value` (shared) | ۳۷ | KEEP | KEEP | MOCK-15 | Invented sample number. |
| H-117 | Console | `mock.kpis[3].label` (shared) | تماس‌های امروز | KEEP | Calls today | MOCK-15 | Sample card label. English: Sentence case, like the rest of the page. |
| H-118 | Console | `mock.kpis[3].value` (shared) | ۹۶ | KEEP | KEEP | MOCK-15 | Invented sample number. |
| H-119 | Console | `mock.trend` (shared) | روند کیفیت | KEEP | KEEP | MOCK-23 | Sample chart label. |
| H-120 | Console | `mock.sentiment` (shared) | توزیع احساس | KEEP | KEEP | MOCK-23 | Sample chart label. |
| H-121 | Console | `mock.sentimentLabels[0]` (shared) | مثبت | KEEP | KEEP | MOCK-23 | Sample chart label. |
| H-122 | Console | `mock.sentimentLabels[1]` (shared) | خنثی | KEEP | KEEP | MOCK-23 | Sample chart label. |
| H-123 | Console | `mock.sentimentLabels[2]` (shared) | منفی | KEEP | KEEP | MOCK-23 | Sample chart label. |
| H-124 | Console | `mock.report` (shared) | گزارش تماس | KEEP | Call report | MOCK-23 | Sample panel label. English: Sentence case, like the rest of the page. |
| H-125 | Console | `mock.exportPdf` (shared) | خروجی | KEEP | KEEP | MOCK-13 | Sample button label, already without PDF. |
| H-126 | Console | `mock.scores[4].label` (shared) | پایان تماس | KEEP | Call closing | MOCK-15 | Sample score label (the window shows five). English: Sentence case, like the rest of the page. |
| H-127 | Console | `mock.transcript` (shared) | متن مکالمه | KEEP | KEEP | MOCK-23 | Sample panel label. Glossary «متن مکالمه». |
| H-128 | Console | `mock.transcriptLines[0].who` (shared) | مشتری | KEEP | KEEP | MOCK-10 | Speaker label. |
| H-129 | Console | `mock.transcriptLines[0].text` (shared) | سفارشم بعد از نه روز هنوز «در حال ارسال» است. | KEEP | KEEP | MOCK-10 | Sample line, same as the brain scene. |
| H-130 | Console | `mock.transcriptLines[1].who` (shared) | اپراتور | کارشناس | KEEP | MOCK-10 | Glossary word «کارشناس» for agent. |
| H-131 | Console | `mock.transcriptLines[1].text` (shared) | بابت این موضوع متأسفم. همین حالا وضعیت مرسوله را بررسی می‌کنم. | KEEP | KEEP | MOCK-10 | Sample line, same as the brain scene. |
| H-132 | Console | `components/mockups/PhoneFrame.tsx:14` (shared) | 9:41 | KEEP | KEEP | none | Clock of the sample phone. |
| H-133 | Console | `mock.phoneTitle` (shared) | تماس‌های نمونه | KEEP | KEEP | MOCK-12 | Simulation label of the phone. |
| H-134 | Console | `mock.phoneItems[0].title` (shared) | داخلی ۱۰۴ · ورودی | KEEP | KEEP | MOCK-15 | Sample call row. |
| H-135 | Console | `mock.phoneItems[0].meta` (shared) | ۰۶:۱۲ · کیفیت ۸۲ | KEEP | KEEP | MOCK-15 | Sample call row. |
| H-136 | Console | `mock.phoneItems[1].title` (shared) | داخلی ۱۱۸ · ورودی | KEEP | KEEP | MOCK-15 | Sample call row. |
| H-137 | Console | `mock.phoneItems[1].meta` (shared) | ۰۳:۴۸ · کیفیت ۷۱ | KEEP | KEEP | MOCK-15 | Sample call row. |
| H-138 | Console | `mock.phoneItems[2].title` (shared) | داخلی ۱۰۴ · خروجی | KEEP | KEEP | MOCK-15 | Sample call row. |
| H-139 | Console | `mock.phoneItems[2].meta` (shared) | ۰۸:۰۵ · کیفیت ۸۸ | KEEP | KEEP | MOCK-15 | Sample call row. |
| H-140 | Console | `mock.copilotQuestion` (shared) | این هفته کدام مشکلات بیشتر تکرار شدند؟ | KEEP | KEEP | MOCK-23 | Sample question, natural Persian. |
| H-141 | Console | `mock.phoneTabs[0]` (shared) | داشبورد | KEEP | KEEP | MOCK-23 | Sample tab label. |
| H-142 | Console | `mock.phoneTabs[1]` (shared) | تماس‌ها | KEEP | KEEP | MOCK-23 | Sample tab label. |
| H-143 | Console | `mock.phoneTabs[2]` (shared) | حوادث | KEEP | KEEP | MOCK-23 | Sample tab label. |
| H-144 | Console | `mock.phoneTabs[3]` (shared) | دستیار | KEEP | KEEP | MOCK-23 | Sample tab label. |
| H-145 | Console | `home.product.items[0].icon` | layout | KEEP | KEEP | none | Icon name, not text. |
| H-146 | Console | `home.product.items[0].title` | داشبورد | وضعیت تماس‌ها در یک نگاه | Your calls at a glance | HOME-23 | The benefit of the overview, inside HOME-23. «همه‌چیز» promised more than the four facts below. Longer: fa 7 to 24, en 9 to 22. |
| H-147 | Console | `home.product.items[0].text` | تماس‌های اخیر، تماس‌های دارای نتیجه، تماس‌های در حال پردازش و مقدار صدای مصرف‌شده. | در یک صفحه بدانید کدام تماس‌ها آماده‌اند، کدام هنوز در حال پردازش‌اند و تا امروز چند دقیقه صدا تحلیل شده است. | See which calls are ready, which are still processing, and how many minutes of audio have been analyzed so far, on one screen. | HOME-23 | Same four facts, said as answers to the manager's questions. The console shows audio in minutes (`Homayar/frontend/src/lib/format.ts:83-91`). Longer: fa 82 to 109, en 69 to 126. |
| H-148 | Console | `home.product.items[1].icon` | file | KEEP | KEEP | none | Icon name, not text. |
| H-149 | Console | `home.product.items[1].title` | گزارش تماس | گزارش با سند | Reports you can check | HOME-24 | Why this report is different: every point comes with its source line. «مستند» alone reads as a documentary. Longer: fa 10 to 12, en 12 to 21. |
| H-150 | Console | `home.product.items[1].text` | خلاصه، اقدام‌ها و بینش‌هایی که به خطوط متن وصل‌اند، به‌علاوهٔ متن کامل مکالمه. | خلاصه، اقدام‌ها و بینش‌ها، هر کدام وصل به جمله‌ای از مکالمه که از آن آمده است؛ متن کامل هم کنارش است. | A summary, action items and insights, each linked to the line it came from, with the full transcript one tab away. | HOME-24, PROD-37 | Plain sentence instead of a noun list with «به‌علاوهٔ». Longer: fa 78 to 101, en 92 to 114. |
| H-151 | Console | `home.product.items[2].icon` | download | KEEP | KEEP | none | Icon name, not text. |
| H-152 | Console | `home.product.items[2].title` | خروجی‌ها | لینک اشتراک و خروجی | Share links and exports | QA-08, HOME-25 | Adds share links, which exist and matter to a supervisor. Longer: fa 8 to 19, en 7 to 23. |
| H-153 | Console | `home.product.items[2].text` | یک تماس را به‌صورت TXT، JSON، SRT یا VTT دانلود کنید. PDF برنامه‌ریزی‌شده است. | تماس را با لینک اشتراک برای کارشناس یا مدیرش بفرستید، یا به‌صورت TXT، JSON، SRT و VTT دانلود کنید. خروجی PDF برنامه‌ریزی‌شده است. | Send a call to an agent or their manager with a share link, or download it as TXT, JSON, SRT or VTT. PDF export is planned. | QA-08, QA-12, HOME-26 | Starts with what the buyer does with the call. Keeps PDF marked as planned. Longer: fa 78 to 129, en 57 to 123. |
| H-154 | Console | `home.product.items[3].icon` | users | KEEP | KEEP | none | Icon name, not text. |
| H-155 | Console | `home.product.items[3].title` | نقش‌ها | دسترسی بر اساس نقش | Access by role | HOME-27, HOME-35 | Names the benefit (who sees what), not only «نقش‌ها». Longer: fa 6 to 18, en 5 to 14. |
| H-156 | Console | `home.product.items[3].text` | نقش‌های مالک، مدیر، عضو و بیننده، تیم‌هایی با سرپرست و اپراتور، و دعوت‌نامه. | هر کس به اندازهٔ نقشش دسترسی دارد: مالک، مدیر، عضو یا بیننده. تیم‌ها سرپرست و کارشناس دارند و هم‌تیمی‌ها را خودتان دعوت می‌کنید. | Everyone gets the access their role allows: owner, admin, member or viewer. Teams have supervisors and agents, and you invite your own colleagues. | HOME-27, HOME-35 | Says what roles do for the buyer, with verbs. Glossary «کارشناس». Longer: fa 76 to 128, en 90 to 146. |
| H-157 | Console | `home.product.items[4].icon` | phone | KEEP | KEEP | none | Icon name, not text. |
| H-158 | Console | `home.product.items[4].title` | وب‌هوک VOIP | اتصال به مرکز تلفن | Phone system connection | HOME-28 | Says what it does for the buyer. «وب‌هوک VOIP» is an internal term. Longer: fa 11 to 18, en 13 to 23. |
| H-159 | Console | `home.product.items[4].text` | برنامه‌ریزی‌شده: گیرنده برای Asterisk و FreePBX؛ اپراتورها از روی شمارهٔ داخلی ساخته می‌شوند. | برنامه‌ریزی‌شده: تماس‌ها مستقیم از Asterisk و FreePBX می‌رسند و برای هر شمارهٔ داخلی یک کارشناس در همایار تعریف می‌شود. | Planned: calls arrive straight from Asterisk and FreePBX, and each extension number becomes an agent in Homayar. | HOME-28, OD-06 | Keeps the planned label and the PBX names. Glossary «کارشناس». Longer: fa 93 to 119, en 90 to 112. |
| H-160 | Console | `home.product.items[5].icon` | bot | KEEP | KEEP | none | Icon name, not text. |
| H-161 | Console | `home.product.items[5].title` | دستیار هوشمند | از داده‌هایتان بپرسید | Ask your call data | HOME-29 | An action the buyer wants, instead of the feature name. Longer: fa 13 to 21, en 10 to 18. |
| H-162 | Console | `home.product.items[5].text` | برنامه‌ریزی‌شده: دربارهٔ داده‌های تماس‌هایتان به زبان ساده سؤال بپرسید. | برنامه‌ریزی‌شده: سؤالتان را دربارهٔ تماس‌ها به زبان ساده بپرسید و جواب بگیرید. | Planned: ask about your calls in plain words and get an answer. | HOME-29 | Keeps the planned label. Shorter. |
| H-163 | Console | `home.product.link` | بررسی محصول | همهٔ امکانات | All features | none | Link to the product page. Says what is behind it. |
| H-164 | Deployment flow | `story.flow.eyebrow` | داده کجا می‌رود | استقرار | Deployment | none | Plain section name the IT buyer looks for. The title now carries «where the data stays». |
| H-165 | Deployment flow | `story.flow.title` | فایل صوتی همان‌جا می‌ماند که شما تصمیم می‌گیرید | خودتان تعیین می‌کنید داده کجا بماند | You decide where your data lives | HOME-30, HOME-44 | Buyer is the subject. Shorter than «همان‌جا می‌ماند که شما تصمیم می‌گیرید». |
| H-166 | Deployment flow | `story.flow.text` | امروز نصب روی سرور شما و ترکیبی؛ ابر مدیریت‌شده برنامه‌ریزی‌شده است. در بستهٔ نصب روی سرور شما، هیچ کانتینر برنامه‌ای مسیری به اینترنت ندارد. | همایار را روی سرور خودتان نصب کنید یا حالت ترکیبی را انتخاب کنید. نسخهٔ ابری برنامه‌ریزی‌شده است. | Install Homayar on your own servers, or choose hybrid. Managed cloud is planned. | HOME-44, OD-04, OD-16 | The choice in one sentence. The no-internet fact moved to the self-hosted column (`home.deployment.items[1].text`). |
| H-167 | Deployment flow | `app/[locale]/page.tsx:128` + `home.deployment.modes[0]` (new key) | saas | ما اجرا می‌کنیم | We run it | OD-04 | New key (Addendum 2, E1). Says who runs it, a fact the title does not give. Words from `pricing.deployments`. The card text and the diagram keep the planned label. |
| H-168 | Deployment flow | `app/[locale]/page.tsx:128` + `home.deployment.modes[1]` (new key) | self-hosted | شما اجرا می‌کنید | You run it | HOME-32 | New key (E1). Says who runs it. Words from `pricing.deployments`. |
| H-169 | Deployment flow | `app/[locale]/page.tsx:128` + `home.deployment.modes[2]` (new key) | hybrid | شما، با ابر اختیاری | You run it, plus cloud | OD-16 | New key (E1). Says who runs it and what is optional. Cloud analysis is opt-in (OD-16). |
| H-170 | Deployment flow | `home.deployment.items[0].title` | SaaS | نسخهٔ ابری | Managed cloud | HOME-31, OD-04 | Persian word instead of «SaaS». The text under it keeps the planned label. Longer: fa 4 to 10, en 4 to 13. |
| H-171 | Deployment flow | `home.deployment.items[0].text` | برنامه‌ریزی‌شده: توسط ما در یک منطقهٔ ابری تأییدشده اجرا می‌شود؛ جداسازی مستأجرها هم در برنامه و هم در پایگاه داده اعمال می‌شود. | برنامه‌ریزی‌شده: همایار را ما در ابر برایتان اجرا می‌کنیم و داده‌های هر سازمان را، هم در برنامه و هم در پایگاه داده، جدا نگه می‌داریم. | Planned: we run Homayar for you in the cloud, with each organization's data kept apart in the application and in the database. | HOME-31, OD-04 | Keeps the planned label. Removes «توسط ما» and «مستأجر». |
| H-172 | Deployment flow | `deployment.flow.source` (shared) | بارگذاری | KEEP | KEEP | HOME-08 | Diagram label. |
| H-173 | Deployment flow | `deployment.flow.app` (shared) | همایار | KEEP | KEEP | none | Brand name in the diagram. |
| H-174 | Deployment flow | `deployment.flow.region` (shared) | برنامه‌ریزی‌شده: منطقهٔ ابری | KEEP | KEEP | OD-04 | Planned label in the cloud diagram. |
| H-175 | Deployment flow | `deployment.flow.models` (shared) | مدل‌های محلی | KEEP | KEEP | none | Diagram label. |
| H-176 | Deployment flow | `home.deployment.items[1].title` | نصب روی سرور شما | روی سرور خودتان | KEEP | HOME-32 | Glossary phrase for self-hosted, used everywhere on the page. |
| H-177 | Deployment flow | `home.deployment.items[1].text` | یک بستهٔ Docker Compose روی سرورهای خودتان. هیچ کانتینر برنامه‌ای مسیری به اینترنت ندارد. | بستهٔ Docker Compose روی سرورهای خودتان. هیچ کانتینر برنامه راهی به اینترنت ندارد. | A Docker Compose package on your own servers. No application container can reach the internet. | HOME-32 | Same fact, shorter. «مسیری به اینترنت» became «راهی به اینترنت». |
| H-178 | Deployment flow | `deployment.flow.internet` (shared) | اینترنت | KEEP | KEEP | none | Diagram label. |
| H-179 | Deployment flow | `deployment.flow.blocked` (shared) | بدون مسیر | KEEP | KEEP | HOME-32 | Diagram label. A longer form breaks the slot. |
| H-180 | Deployment flow | `deployment.flow.allowlist` (shared) | فقط پراکسی با فهرست مجاز | KEEP | KEEP | DEP-27 | IT detail under the diagram. Precise as is. |
| H-181 | Deployment flow | `home.deployment.items[2].title` | ترکیبی | KEEP | KEEP | OD-16 | «ترکیبی» is the common word. |
| H-182 | Deployment flow | `home.deployment.items[2].text` | فایل‌های صوتی و متن‌ها محلی می‌مانند. استدلال ابری اختیاری به‌طور پیش‌فرض متن پوشانده‌شده می‌گیرد و فقط اگر خودتان آن را روشن کنید اجرا می‌شود. | فایل صوتی و متن مکالمه پیش خودتان می‌ماند. تحلیل ابری فقط وقتی روشن است که خودتان بخواهید، و به‌طور پیش‌فرض متنی می‌گیرد که داده‌های شخصی‌اش پوشانده شده است. | Recordings and transcripts stay with you. Cloud analysis runs only if you turn it on, and by default it gets text with personal data masked. | HOME-33, OD-16 | «استدلال ابری» is a calque. Keeps «by default», as OD-16 requires. |
| H-183 | Deployment flow | `deployment.flow.optIn` (shared) | با انتخاب هر مستأجر | با انتخاب هر سازمان | Opt-in per org | DEP-29 | Glossary: «سازمان» instead of «مستأجر». English «org» fits the slot; «organization» does not. |
| H-184 | Deployment flow | `deployment.flow.cloud` (shared) | استدلال ابری، به‌طور پیش‌فرض روی متن پوشانده‌شده | تحلیل ابری، به‌طور پیش‌فرض با متن پوشانده‌شده | Cloud analysis, masked by default | HOME-33, OD-16 | Glossary «تحلیل ابری» instead of «استدلال ابری». |
| H-185 | Deployment flow | `home.deployment.link` | مقایسهٔ گزینه‌های استقرار | کدام روش برای شما مناسب است؟ | Find the right setup | none | Invites the buyer's own question instead of «مقایسهٔ گزینه‌ها». |
| H-186 | Trust | `story.trust.eyebrow` | درون محصول، نه چسبیده به آن | از روز اول | From day one | HOME-45 | «چسبیده به آن» was a calque of «bolted on». The title under it already says «حریم خصوصی». |
| H-187 | Trust | `story.trust.title` | حریم خصوصی یک ویژگی محصول است | حریم خصوصی مشتری‌هایتان، بخشی از خود محصول | Your customers' privacy, built into the product | HOME-45 | The buyer's customers are the subject. «یک ویژگی محصول است» read like a spec. Longer: fa 29 to 42, en 28 to 47. |
| H-188 | Trust | `home.trust.items[0].icon` | eyeOff | KEEP | KEEP | none | Icon name, not text. |
| H-189 | Trust | `home.trust.items[0].title` | پوشاندن داده‌های شخصی | KEEP | KEEP | HOME-34 | Glossary term. Clear for both buyers and IT. |
| H-190 | Trust | `home.trust.items[0].text` | شماره تلفن، ایمیل، کد ملی، شمارهٔ کارت و شبا در متن جایگزین می‌شوند. محدودیت‌ها گفته می‌شوند، نه پنهان. | شماره تلفن، ایمیل، کد ملی، شمارهٔ کارت و شبا در متن مکالمه با برچسب جایگزین می‌شوند. هر جا محدودیتی هست، صریح می‌گوییم. | Phone numbers, e-mails, national IDs, card numbers and IBANs are replaced with placeholders in the transcript. Where there are limits, we say so. | HOME-34, SEC-10 | Says what replaces the data. «گفته می‌شوند، نه پنهان» was passive and stiff. Longer: fa 103 to 119, en 126 to 145. |
| H-191 | Trust | `home.trust.items[1].icon` | layers | KEEP | KEEP | none | Icon name, not text. |
| H-192 | Trust | `home.trust.items[1].title` | جداسازی مستأجرها | داده‌های هر سازمان، جدا | Each organization kept apart | HOME-35 | «جداسازی مستأجرها» is internal jargon. Longer: fa 16 to 23, en 16 to 28. |
| H-193 | Trust | `home.trust.items[1].text` | بررسی فضای کاری در برنامه به‌علاوهٔ امنیت سطح سطر PostgreSQL زیر نقشی که نمی‌تواند آن را دور بزند. | برنامه دسترسی هر فضای کاری را می‌سنجد و پایگاه داده هم با امنیت سطح سطر PostgreSQL جلوی دسترسی به دادهٔ سازمان دیگر را می‌گیرد. | The application checks each workspace's access, and PostgreSQL row-level security blocks access to another organization's data. | HOME-35 | Says what the two checks protect. The non-bypass role detail stays on the security page. Longer: fa 98 to 127, en 95 to 127. |
| H-194 | Trust | `home.trust.items[2].icon` | trash | KEEP | KEEP | none | Icon name, not text. |
| H-195 | Trust | `home.trust.items[2].title` | حذفی که واقعاً پاک می‌کند | KEEP | Delete really means delete | HOME-36 | Persian keeps: natural and concrete. English «Deletion that purges» was stiff. Longer: en 20 to 26. |
| H-196 | Trust | `home.trust.items[2].text` | حذف، دسترسی را بلافاصله می‌بندد و سپس یک پاک‌سازی پایدار متن‌ها، تحلیل‌ها و بایت‌های رسانه را حذف می‌کند. | با حذف یک تماس، دسترسی به آن همان لحظه بسته می‌شود؛ بعد متن، تحلیل‌ها و خود فایل صوتی کامل پاک می‌شوند. | When you delete a call, access ends at once. Then its transcript, analyses and audio file are purged for good. | HOME-36 | «بایت‌های رسانه» and «پاک‌سازی پایدار» are internals. Same fact in plain words. |
| H-197 | Trust | `home.trust.items[3].icon` | shield | KEEP | KEEP | none | Icon name, not text. |
| H-198 | Trust | `home.trust.items[3].title` | بدون جایگزینی پنهان به ابر | هیچ ارسال پنهانی به ابر | No hidden trips to the cloud | HOME-37 | «جایگزینی پنهان» was a calque of «fallback». Longer: en 24 to 28. |
| H-199 | Trust | `home.trust.items[3].text` | اگر مدل محلی از کار بیفتد، آن مرحله «در دسترس نیست» گزارش می‌شود. هیچ‌چیز بی‌سروصدا به جای دیگری فرستاده نمی‌شود. | اگر مدل محلی از کار بیفتد، همان مرحله «در دسترس نیست» نشان داده می‌شود و هیچ داده‌ای بی‌خبر به جای دیگری نمی‌رود. | If a local model fails, that step is marked unavailable. No data is quietly sent anywhere else. | HOME-37 | Shorter. «بی‌سروصدا» became «بی‌خبر», which says the buyer would not know. |
| H-200 | Trust | `home.trust.link` | خواندن صفحهٔ امنیت | جزئیات امنیت | Security details | none | Link label. Says what is behind it, without «خواندن صفحهٔ». |
| H-201 | Command CTA | `palette.eyebrow` (shared) | قدم بعدی | KEEP | KEEP | none | Keeps: two natural words that move the buyer on. |
| H-202 | Command CTA | `palette.title` (shared) | یک گزارش تماس واقعی بخواهید | گزارش واقعی یک تماس را ببینید | See a real call report | CHR-05 | «بخواهید» asked for effort. «ببینید» promises the thing itself. |
| H-203 | Command CTA | `palette.actions[0].label` (shared) | درخواست دمو | KEEP | KEEP | none | Main button label. Standard words. |
| H-204 | Command CTA | `palette.actions[0].meta` (shared) | با ایمیل پاسخ می‌دهیم، بعد یک تماس کوتاه | با ایمیل جواب می‌دهیم و یک تماس کوتاه هماهنگ می‌کنیم | We reply by e-mail and set up a short call | CHR-13 | Full sentence instead of a fragment. Longer: fa 40 to 52. |
| H-205 | Command CTA | `palette.actions[1].label` (shared) | دیدن مسیر پردازش | روش کار همایار | How Homayar works | none | «مسیر پردازش» is an internal word. |
| H-206 | Command CTA | `palette.actions[1].meta` (shared) | گام به گام، با وضعیت‌های صادقانه | قدم‌به‌قدم، از فایل صوتی تا گزارش | Step by step, from recording to report | CHR-07 | Says what the page shows. «وضعیت‌های صادقانه» was vague. |
| H-207 | Command CTA | `palette.actions[2].label` (shared) | خواندن صفحهٔ امنیت | امنیت داده‌ها | Data security | none | Shorter label, the subject the buyer cares about. |
| H-208 | Command CTA | `palette.actions[2].meta` (shared) | پوشاندن، جداسازی، حذف، خروج داده | پوشاندن داده‌های شخصی، جداسازی، حذف و کنترل خروج داده | Masking, isolation, deletion and egress control | CHR-08 | Glossary «پوشاندن داده‌های شخصی». A list a security lead can scan. Longer: fa 32 to 53, en 36 to 47. |
| H-209 | Command CTA | `palette.actions[3].label` (shared) | مقایسهٔ گزینه‌های استقرار | انتخاب روش استقرار | Choose your setup | none | A choice the buyer makes, not a comparison chore. |
| H-210 | Command CTA | `palette.actions[3].meta` (shared) | روی سرور شما، ترکیبی، SaaS برنامه‌ریزی‌شده | روی سرور خودتان یا ترکیبی؛ نسخهٔ ابری برنامه‌ریزی‌شده | Self-hosted or hybrid; managed cloud planned | CHR-06, OD-04 | Glossary phrases. Keeps the planned label. Longer: fa 42 to 53, en 33 to 44. |
| H-211 | Footer | `footer.note` (shared) | همایار فقط فایل‌های صوتی تماسی را تحلیل می‌کند که مجوز پردازش آن‌ها را دارید. این وب‌سایت جز بررسی Cloudflare Turnstile در صفحهٔ تماس، هیچ اسکریپت شخص ثالثی بارگذاری نمی‌کند و کوکی ردیابی نمی‌گذارد. | REMOVE (owner, 2026-10-28) | REMOVE (owner, 2026-10-28) | CHR-02 | Removed by the owner on 2026-10-28 (Addendum 4). Its true facts move to the privacy page. Team t2 makes that code change; this branch does not touch the key. |
| H-212 | Footer | `footer.product` (shared) | محصول | KEEP | KEEP | none | Column heading. |
| H-213 | Footer | `footer.trust` (shared) | اعتماد | KEEP | KEEP | none | Column heading. |
| H-214 | Footer | `footer.company` (shared) | شرکت | KEEP | KEEP | none | Column heading. |
| H-215 | Footer | `footer.legal` (shared) | حقوقی | KEEP | KEEP | none | Column heading. |
| H-216 | Footer | `nav.gdpr` (shared) | GDPR | KEEP | KEEP | none | Footer link label. |
| H-217 | Footer | `footer.rights` (shared) | همایار. همهٔ حقوق محفوظ است. | KEEP | KEEP | none | Legal notice. |
| H-218 | Page loader | `loader.loading` (shared) | در حال بارگذاری | KEEP | KEEP | none | Status text during a page change. |
| H-219 | Page loader | `components/site/TransitionLayer.tsx:207` (shared) | ٪ / % | KEEP | KEEP | none | Percent sign of the progress number. |

## Length rule

All 127 tight-slot rows (hero headline lines, the slogan, the text inside the warp and brain
scenes, chips, eyebrows, labels, links and buttons) are within 15 percent of the old line in both
languages, except the three deployment labels below. They replace the raw ids (saas, self-hosted,
hybrid) with a short fact, which cannot fit in four to eleven characters.

| Row | Key | Characters | How it looks |
| --- | --- | --- | --- |
| H-167 | `home.deployment.modes[0]` | fa 4 to 15, en 4 to 9 | Small `text-xs` label over a card in a three-column grid (one column on phones). Expected to fit on one line; check on the running page. |
| H-168 | `home.deployment.modes[1]` | fa 11 to 16, en 11 to 10 | Small `text-xs` label over a card in a three-column grid (one column on phones). Expected to fit on one line; check on the running page. |
| H-169 | `home.deployment.modes[2]` | fa 6 to 19, en 6 to 22 | Small `text-xs` label over a card in a three-column grid (one column on phones). Expected to fit on one line; check on the running page. |

Two changes were cut for this reason: «فرصت فروش بیشتر» (H-087) stays «فرصت فروش», and
`scene.warp.text` (H-041) lost «متن کامل» and «بدانید چه باید کرد».

Lines outside tight slots that got longer by more than 15 percent are listed below. They are
card titles, card texts and section text that wrap freely. Each one has the same note at the end
of its Reason. Check them on the running page.

| Row | Key | Characters |
| --- | --- | --- |
| H-072 | `story.text` | fa 105 to 176, en 110 to 215 |
| H-075 | `home.outputs.items[0].title` | en 21 to 30 |
| H-083 | `home.outputs.items[2].title` | fa 13 to 15, en 16 to 24 |
| H-093 | `home.outputs.items[3].title` | fa 12 to 21, en 16 to 23 |
| H-146 | `home.product.items[0].title` | fa 7 to 24, en 9 to 22 |
| H-147 | `home.product.items[0].text` | fa 82 to 109, en 69 to 126 |
| H-149 | `home.product.items[1].title` | fa 10 to 12, en 12 to 21 |
| H-150 | `home.product.items[1].text` | fa 78 to 101, en 92 to 114 |
| H-152 | `home.product.items[2].title` | fa 8 to 19, en 7 to 23 |
| H-153 | `home.product.items[2].text` | fa 78 to 129, en 57 to 123 |
| H-155 | `home.product.items[3].title` | fa 6 to 18, en 5 to 14 |
| H-156 | `home.product.items[3].text` | fa 76 to 128, en 90 to 146 |
| H-158 | `home.product.items[4].title` | fa 11 to 18, en 13 to 23 |
| H-159 | `home.product.items[4].text` | fa 93 to 119, en 90 to 112 |
| H-161 | `home.product.items[5].title` | fa 13 to 21, en 10 to 18 |
| H-170 | `home.deployment.items[0].title` | fa 4 to 10, en 4 to 13 |
| H-187 | `story.trust.title` | fa 29 to 42, en 28 to 47 |
| H-190 | `home.trust.items[0].text` | fa 103 to 119, en 126 to 145 |
| H-192 | `home.trust.items[1].title` | fa 16 to 23, en 16 to 28 |
| H-193 | `home.trust.items[1].text` | fa 98 to 127, en 95 to 127 |
| H-195 | `home.trust.items[2].title` | en 20 to 26 |
| H-198 | `home.trust.items[3].title` | en 24 to 28 |
| H-204 | `palette.actions[0].meta` | fa 40 to 52 |
| H-208 | `palette.actions[2].meta` | fa 32 to 53, en 36 to 47 |
| H-210 | `palette.actions[3].meta` | fa 42 to 53, en 33 to 44 |

## Message frame

**Buyer.** The contact-center manager, the quality lead and the operations lead. They are measured
on service quality, on how many calls their team reviews, and on how fast problems get fixed. The
IT or security lead is the gatekeeper: they ask where the data lives and who can see it.

**Problems this page leads with, and the outcome the buyer feels.**

| Problem, in the buyer's words | Outcome | Proof |
| --- | --- | --- |
| «به همهٔ تماس‌ها نمی‌رسم.» | Each call comes back as a transcript, a summary and action items, so the supervisor reads instead of listening | SUP-03, HOME-02 |
| «نمی‌دانم این نتیجه از کجا آمده.» | Every point links to the line of the call it came from | HOME-05, HOME-24, PROD-37 |
| «صدای مشتری‌ها نباید از سازمان بیرون برود.» | Self-hosted with no internet route; hybrid with masked text by default | HOME-32, HOME-33, OD-16 |
| «اطلاعات شخصی مشتری در متن می‌ماند.» | Phone, e-mail, national ID, card and IBAN replaced in the transcript; real deletion | HOME-34, HOME-36 |
| «می‌خواهم کارشناس‌ها را منصفانه بسنجم و آموزش بدهم.» | Planned: one set of criteria for every agent, and coaching from their own weak points | HOME-13, HOME-16 (planned) |

**Tone.** Natural, confident, concrete and short. The buyer, their team or their calls are the
subject. Persian is written for Persian speakers: «شما» where needed, no calques, no bureaucratic
words, no slang. English is natural marketing English with the same claim, not a translation.
Planned and simulated items are named plainly, without apology.

**Banned on this page.** Stage directions («اسکرول»، «ادامه دهید»، «وارد ... می‌شوید»، «نگاه کنید»,
scroll, watch). Scene narration («فضای تحلیل باز می‌شود», a network that lights up, a report that
flies toward you). Internals as the message («هسته»، «عامل»، «خط لوله»، «پردازشگر», pipeline, worker,
queue). Calques («به شما اجازه می‌دهد»، «قادر به ... است»، passive with «توسط»، «ساخته‌شده برای»،
«استدلال ابری»، «مستأجر»). Invented numbers, customers, time savings or accuracy.

**Glossary.**

| Idea | Persian | English |
| --- | --- | --- |
| call | تماس | call |
| conversation | مکالمه (Q-02) | conversation |
| transcript | متن مکالمه | transcript |
| recording | فایل صوتی | recording |
| agent (person) | کارشناس (Q-07) | agent |
| supervisor | سرپرست | supervisor |
| QA team | تیم کیفیت | QA team |
| summary | خلاصه | summary |
| action items | اقدام‌ها | action items |
| insight | بینش | insight |
| linked to the line it came from | وصل به جمله‌ای از مکالمه که از آن آمده | linked to the line it came from |
| backed by the transcript | مستند به متن مکالمه | backed by the transcript |
| QC score (planned) | امتیاز کیفیت | quality score, QC scoring |
| root cause (planned) | ریشهٔ مشکل | root cause |
| coaching (planned) | آموزش کارشناس | coaching |
| upsell, churn (planned) | فروش بیشتر، ریزش مشتری | upsell, churn |
| copilot (planned) | دستیار هوشمند | copilot |
| the web app | پنل (Q-01) | console |
| self-hosted | روی سرور خودتان | self-hosted, on your own servers |
| hybrid | ترکیبی | hybrid |
| managed cloud (planned) | نسخهٔ ابری | managed cloud |
| cloud analysis | تحلیل ابری | cloud analysis |
| personal-data masking | پوشاندن داده‌های شخصی | personal-data masking |
| organization (tenant) | سازمان | organization |
| workspace | فضای کاری | workspace |
| share link | لینک اشتراک | share link |
| step | قدم | step |
| planned (label) | برنامه‌ریزی‌شده | Planned / planned |
| simulation (label) | شبیه‌سازی | simulation |
| sample data (label) | داده‌ی نمونه | sample data |

## Keys under home that the page does not render

No component reads these leaves: `website/app/[locale]/page.tsx` reads only `home.meta`,
`home.particles`, `home.hero.title`, `home.hero.headlines`, and the `items` and `link` of
`home.product`, `home.deployment` and `home.trust`; `website/components/story/AgentJourney.tsx:9`
reads `home.outputs.items`. They are unchanged in H1. Stage P decides whether to rewrite or remove
them. `home.hero.hint` still holds «اسکرول کنید», which no visitor sees.

| Key | Old fa | Old en |
| --- | --- | --- |
| `home.hero.hint` | اسکرول کنید: یک تماس به گزارش تبدیل می‌شود | Scroll: a call becomes a report |
| `home.outputs.eyebrow` | برای هر تماس چه می‌گیرید | What you get for every call |
| `home.outputs.title` | یک فایل صوتی وارد می‌شود. یک گزارش بازبینی‌شده بیرون می‌آید. | One recording in. A reviewed report out. |
| `home.outputs.text` | هر گزارش از متن همان تماس ساخته می‌شود؛ بنابراین سرپرست می‌تواند گفت‌وگوی پشت هر امتیاز را باز کند. | Each report is built from the transcript of that call, so a supervisor can open the conversation behind any score. |
| `home.pipeline.eyebrow` | مسیر داده | How it flows |
| `home.pipeline.title` | از فایل صوتی تا کنسول در چهار گام | From the recording to the console in four steps |
| `home.pipeline.steps[0].title` | ورود تماس | Bring the call in |
| `home.pipeline.steps[0].text` | فایل را بارگذاری کنید. ارسال از سیستم VOIP با وب‌هوک برنامه‌ریزی‌شده است. | Upload a file. A webhook from your VOIP system is planned. |
| `home.pipeline.steps[1].title` | تبدیل به متن | Transcribe |
| `home.pipeline.steps[1].text` | گفتار به متن با Whisper؛ فارسی، انگلیسی یا تشخیص خودکار زبان. | Speech to text with Whisper, in Persian, English or auto-detected. |
| `home.pipeline.steps[2].title` | تحلیل | Analyze |
| `home.pipeline.steps[2].text` | مرحلهٔ تحلیل خلاصه، اقدام‌ها و بینش‌ها را می‌نویسد. | The analysis stage writes a summary, action items and insights. |
| `home.pipeline.steps[3].title` | بازبینی | Review |
| `home.pipeline.steps[3].text` | نمای کلی، نتایج تماس و خروجی‌ها. دستیار هوشمند برنامه‌ریزی‌شده است. | Overview, call results and exports. A copilot is planned. |
| `home.pipeline.link` | دیدن مسیر کامل پردازش | See the full pipeline |
| `home.product.eyebrow` | یک کنسول واقعی، نه یک ایده | A working console, not a concept |
| `home.product.title` | ساخته‌شده برای کار روزانهٔ تیم کیفیت | Built for the daily work of a QA team |
| `home.deployment.eyebrow` | دادهٔ شما، انتخاب شما برای محل نگهداری | Your data, your choice of where it lives |
| `home.deployment.title` | امروز روی سرور شما یا ترکیبی؛ ابری برنامه‌ریزی‌شده | Self-hosted or hybrid today, cloud planned |
| `home.trust.eyebrow` | حریم خصوصی یک ویژگی محصول است | Privacy is a product feature |
| `home.trust.title` | طوری طراحی شده که فایل صوتی هرگز از کنترل شما خارج نشود | Designed so the recording never leaves your control |
