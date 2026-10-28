# Website copy v2: Shared keys (group A)

| Field | Value |
| --- | --- |
| Ticket | HOM-327, stage P, group A |
| Base commit | `5f2f75b` (the approved Home page and the style guide) |
| Date | 2026-10-28 |
| Status | Written with `docs/website/copy-style.md`; waiting for review |
| Pages | Keys shared by several pages, the command palette, the 404 page, the sample screens, and the `home` keys no component renders |
| Rows | `SH-001` to `SH-084`. Persian changed in 27, English in 26 |
| How to answer | Name a row ID and say: keep, change to ..., or remove |

Rules: `docs/website/copy-style.md`. `(shared)` means other pages show the key too. `(not rendered)` means no component reads the key today; it is rewritten so no old wording stays in the dictionary.

## Owner questions

| ID | Question | Rows | Evidence | Applied now | Options |
| --- | --- | --- | --- | --- | --- |
| Q-01 | On each role page (`/solutions/<role>`) the chip sits right after the roadmap heading. Today the visitor reads «برای این نقش برنامه‌ریزی‌شده» followed by the chip «به‌زودی» ("Planned for this role" + "Coming soon"): one idea, two words. The chip now says «برنامه‌ریزی‌شده» / "Planned", the style guide's one word for planned. So that the heading does not repeat the chip word, team t2 changes the roadmap heading in group B (see `docs/website/copy-v2-solutions.md`, the roadmap title rows). Which pair do you want? | SH-011 | Chip after the heading: `website/app/[locale]/solutions/[slug]/page.tsx:80-81`; register OD-15; `docs/website/copy-style.md` section 7 | (a), together with team t2's new heading | (a) Chip «برنامه‌ریزی‌شده» / "Planned" with the new group B heading (applied). (b) Go back to «به‌زودی» / "Coming soon" (OD-15 as it was). |

## Lines in the order the visitor meets them

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| SH-001 | Command palette | `palette.placeholder` | از همایار بپرسید… | رفتن به صفحه… | Go to a page… | none | The palette is a list of page links (`components/site/CommandCta.tsx:59`), not a chat. «از همایار بپرسید» suggested the planned copilot (team t2 note). |
| SH-002 | Command palette | `palette.hint` | یک اقدام را انتخاب کنید | یکی را انتخاب کنید | Pick one | none | «اقدام» is the glossary word for action items, so it is not used for a menu choice. |
| SH-003 | Command palette | `palette.actions[0].path` | /contact | KEEP | KEEP | none | Link path, not text. |
| SH-004 | Command palette | `palette.actions[0].kbd` | D | KEEP | KEEP | none | Keyboard hint letter. |
| SH-005 | Command palette | `palette.actions[1].path` | /how-it-works | KEEP | KEEP | none | Link path, not text. |
| SH-006 | Command palette | `palette.actions[1].kbd` | P | KEEP | KEEP | none | Keyboard hint letter. |
| SH-007 | Command palette | `palette.actions[2].path` | /security | KEEP | KEEP | none | Link path, not text. |
| SH-008 | Command palette | `palette.actions[2].kbd` | S | KEEP | KEEP | none | Keyboard hint letter. |
| SH-009 | Command palette | `palette.actions[3].path` | /deployment | KEEP | KEEP | none | Link path, not text. |
| SH-010 | Command palette | `palette.actions[3].kbd` | C | KEEP | KEEP | none | Keyboard hint letter. |
| SH-011 | Common | `common.comingSoon` | به‌زودی | برنامه‌ریزی‌شده | Planned | OD-15, Q-01 | One word for planned on the whole site (style guide, section 7). This changes OD-15, so it is an owner question. |
| SH-012 | Common | `common.requestDemo` (not rendered) | درخواست دمو | KEEP | KEEP | none | Not rendered. Button label. |
| SH-013 | Common | `common.seeHowItWorks` (not rendered) | ببینید چطور کار می‌کند | KEEP | KEEP | none | Not rendered. Link label. |
| SH-014 | Common | `common.seeProduct` (not rendered) | دیدن محصول | KEEP | KEEP | none | Not rendered. Link label. |
| SH-015 | Common | `common.talkToUs` (not rendered) | با ما صحبت کنید | KEEP | KEEP | none | Not rendered. Link label. |
| SH-016 | Common | `common.learnMore` (not rendered) | بیشتر بدانید | KEEP | KEEP | none | Not rendered. Link label. |
| SH-017 | Common | `common.onThisPage` | در این صفحه | KEEP | KEEP | none | Navigation label on the security page. |
| SH-018 | Common | `common.readMore` (not rendered) | ادامه | KEEP | KEEP | none | Not rendered. Link label. |
| SH-019 | Common | `common.backHome` (not rendered) | بازگشت به صفحهٔ اصلی | KEEP | KEEP | none | Not rendered. Link label. |
| SH-020 | Not found | `app/global-not-found.tsx:50` | 404 | KEEP | KEEP | none | The 404 chip. |
| SH-021 | Not found | `notFound.title` | صفحه پیدا نشد | KEEP | KEEP | CHR-12 | Interface text. |
| SH-022 | Not found | `notFound.text` | ممکن است نشانی اشتباه باشد یا صفحه جابه‌جا شده باشد. | KEEP | KEEP | CHR-12 | Interface text, plain and polite. |
| SH-023 | Not found | `notFound.home` | رفتن به صفحهٔ اصلی | KEEP | KEEP | CHR-12 | Button label. |
| SH-024 | Not found | `notFound.contact` | تماس با ما | KEEP | KEEP | CHR-12 | Button label. |
| SH-025 | CTA band (not rendered) | `ctaBand.title` (not rendered) | یک گزارش تماس واقعی ببینید، نه اسلاید. | KEEP | KEEP | CHR-03 | Not rendered. Keeps: a clear, concrete promise. |
| SH-026 | CTA band (not rendered) | `ctaBand.text` (not rendered) | از صف تماس‌ها، نحوهٔ ضبط و جایی که داده باید بماند برایمان بگویید. کنسول را با یک تماس نمونه نشان می‌دهیم و یک پایلوت را با هم تعریف می‌کنیم. | از صف تماس‌ها، روش ضبط و جایی که داده باید بماند برایمان بگویید. پنل را با یک تماس نمونه نشانتان می‌دهیم و با هم پایلوتی تعریف می‌کنیم. | Tell us about your queue, how you record calls and where the data must stay. We show you the console with a sample call and plan a pilot together. | CHR-04 | Not rendered. Glossary «پنل»; «نحوهٔ ضبط» became «روش ضبط». |
| SH-027 | CTA band (not rendered) | `ctaBand.primary` (not rendered) | درخواست دمو | KEEP | KEEP | none | Not rendered. Button label. |
| SH-028 | CTA band (not rendered) | `ctaBand.secondary` (not rendered) | نحوهٔ کار | KEEP | KEEP | none | Not rendered. «نحوهٔ کار» is the fixed page name (style guide exception). |
| SH-029 | Sample screens | `mock.webhookTitle` (shared) | وب‌هوک VOIP · برنامه‌ریزی‌شده | اتصال مرکز تلفن · برنامه‌ریزی‌شده | Phone system · planned | MOCK-14 | «وب‌هوک VOIP» is internals; the same name as the approved Home card. Planned label kept. |
| SH-030 | Sample screens | `mock.roles[0]` (shared) | مدیر | KEEP | KEEP | MOCK-17 | Admin role. It always renders inside the role list «مدیر · سرپرست · کارشناس» (`DayTimeline.tsx:93,107`), which the guide allows. |
| SH-031 | Sample screens | `mock.roles[1]` (shared) | سرپرست | KEEP | KEEP | MOCK-17 | Role name. |
| SH-032 | Sample screens | `mock.roles[2]` (shared) | کارشناس | KEEP | KEEP | MOCK-17 | Role name (glossary «کارشناس»). |
| SH-033 | Sample screens | `mock.stages[0]` (shared) | آماده‌سازی فایل | KEEP | KEEP | MOCK-21 | Stage name in the sample screens. |
| SH-034 | Sample screens | `mock.stages[1]` (shared) | تبدیل گفتار به متن | KEEP | KEEP | MOCK-21 | Stage name in the sample screens. |
| SH-035 | Sample screens | `mock.stages[2]` (shared) | تفکیک گوینده‌ها | KEEP | KEEP | MOCK-21 | Stage name in the sample screens. |
| SH-036 | Sample screens | `mock.stages[3]` (shared) | خلاصه و تحلیل | KEEP | KEEP | MOCK-21 | Stage name in the sample screens. |
| SH-037 | Sample screens | `mock.stageStates[0]` (shared) | انجام شد | KEEP | KEEP | MOCK-21 | Sample status word. |
| SH-038 | Sample screens | `mock.stageStates[1]` (shared) | انجام شد | KEEP | KEEP | MOCK-21 | Sample status word. |
| SH-039 | Sample screens | `mock.stageStates[2]` (shared) | در دسترس نیست | KEEP | KEEP | MOCK-21 | Sample status word. |
| SH-040 | Sample screens | `mock.stageStates[3]` (shared) | در حال اجرا | KEEP | KEEP | MOCK-21 | Sample status word. |
| SH-041 | Sample screens | `mock.transcriptLines[2].who` (shared) | مشتری | KEEP | KEEP | MOCK-10 | Speaker label. |
| SH-042 | Sample screens | `mock.transcriptLines[2].text` (shared) | هفتهٔ پیش هم تماس گرفتم و کسی پیگیری نکرد. | KEEP | KEEP | MOCK-10 | Sample line. |
| SH-043 | Sample screens | `mock.scores[5].label` (shared) | مدیریت سکوت | KEEP | Silence handling | MOCK-15 | English sentence case. |
| SH-044 | Sample screens | `mock.scores[6].label` (shared) | لحن | KEEP | KEEP | MOCK-15 | Sample score label. |
| SH-045 | Sample screens | `mock.copilotAnswer` (shared) | تأخیر در وضعیت ارسال در ۲۱ تماس دیده شد و بعد از آن زمان بازپرداخت در ۱۴ تماس. | KEEP | KEEP | MOCK-15 | Sample answer with invented numbers, inside a labeled simulation. |
| SH-046 | Sample screens | `mock.upload` (shared) | بارگذاری فایل صوتی | KEEP | KEEP | MOCK-20 | Sample button label. |
| SH-047 | Sample screens | `mock.uploadFormats` (shared) | WAV، MP3، OGG، M4A، AAC، FLAC، MP4، WEBM | KEEP | KEEP | MOCK-20 | Format list. |
| SH-048 | Sample screens | `mock.compare[0]` (shared) | داخلی ۱۰۴ | KEEP | KEEP | MOCK-15 | Sample extension label. |
| SH-049 | Sample screens | `mock.compare[1]` (shared) | داخلی ۱۱۸ | KEEP | KEEP | MOCK-15 | Sample extension label. |
| SH-050 | Sample screens | `mock.compare[2]` (shared) | داخلی ۱۲۱ | KEEP | KEEP | MOCK-15 | Sample extension label. |
| SH-051 | Sample screens | `mock.runbook[0]` (shared) | سلامت | KEEP | KEEP | MOCK-19 | Runbook step label. Natural. |
| SH-052 | Sample screens | `mock.runbook[1]` (shared) | آمادگی | KEEP | KEEP | MOCK-19 | Runbook step label. Natural. |
| SH-053 | Sample screens | `mock.runbook[2]` (shared) | کار مهاجرت | مهاجرت داده | KEEP | MOCK-19 | «کار مهاجرت» was a calque. Team t2's «کار مهاجرت پایگاه داده» is over the length limit for this label. |
| SH-054 | Sample screens | `mock.runbook[3]` (shared) | تخلیه | KEEP | KEEP | MOCK-19 | «تخلیه» stays: the usual ops word for drain, and any longer Persian phrase is over the length limit (5 characters). |
| SH-055 | Sample screens | `mock.runbook[4]` (shared) | بازگشت | KEEP | KEEP | MOCK-19 | «بازگشت» stays: team t2's «بازگشت به نسخهٔ قبل» is over the length limit (6 characters). The deployment page says it in full. |
| SH-056 | Sample screens | `mock.rls` (shared) | امنیت سطر: اجباری | KEEP | KEEP | MOCK-18 | IT label. Team t2's «امنیت سطح سطر» does not fit: «امنیت سطح سطر: اجباری» is 21 characters, limit 19. |
| SH-057 | Sample screens (not rendered) | `mock.insights` (shared) (not rendered) | بینش کسب‌وکار | KEEP | Business insight | MOCK-23 | Not rendered. English sentence case. |
| SH-058 | Sample screens (not rendered) | `mock.actions` (shared) (not rendered) | اقدامات پیشنهادی | اقدام‌های پیشنهادی | Recommended actions | MOCK-23 | Not rendered. Glossary plural «اقدام‌ها»; English sentence case. |
| SH-059 | Sample screens (not rendered) | `mock.severity` (shared) (not rendered) | شدت | KEEP | KEEP | MOCK-23 | Not rendered. Glossary «شدت». |
| SH-060 | Sample screens (not rendered) | `mock.agent` (shared) (not rendered) | اپراتور | کارشناس | KEEP | MOCK-23 | Not rendered. Glossary «کارشناس». |
| SH-061 | Sample screens (not rendered) | `mock.duration` (shared) (not rendered) | مدت | KEEP | KEEP | none | Not rendered. Field label. |
| SH-062 | Sample screens (not rendered) | `mock.language` (shared) (not rendered) | زبان | KEEP | KEEP | none | Not rendered. Field label. |
| SH-063 | Sample screens (not rendered) | `mock.languageValue` (shared) (not rendered) | فارسی | KEEP | KEEP | MOCK-22 | Not rendered. Field value. |
| SH-064 | Home keys not rendered | `home.hero.hint` (not rendered) | اسکرول کنید: یک تماس به گزارش تبدیل می‌شود | از تماس تا گزارش | From call to report | none | Not rendered. Old line was a scroll instruction. |
| SH-065 | Home keys not rendered | `home.outputs.eyebrow` (not rendered) | برای هر تماس چه می‌گیرید | KEEP | KEEP | none | Not rendered. Keeps: buyer-first and natural. |
| SH-066 | Home keys not rendered | `home.outputs.title` (not rendered) | یک فایل صوتی وارد می‌شود. یک گزارش بازبینی‌شده بیرون می‌آید. | فایل صوتی را بدهید، گزارش را تحویل بگیرید. | Hand over the recording. Get the report back. | HOME-11 | Not rendered. The buyer is the subject, not the file. |
| SH-067 | Home keys not rendered | `home.outputs.text` (not rendered) | هر گزارش از متن همان تماس ساخته می‌شود؛ بنابراین سرپرست می‌تواند گفت‌وگوی پشت هر امتیاز را باز کند. | هر گزارش از متن همان تماس می‌آید، پس سرپرست برای هر نکته می‌تواند جملهٔ اصلی مکالمه را باز کند. | Every report comes from that call's transcript, so a supervisor can open the exact line behind any point. | HOME-12, HOME-24 | Not rendered. «پشت هر امتیاز» named a planned score; «هر نکته» is true today. |
| SH-068 | Home keys not rendered | `home.pipeline.eyebrow` (not rendered) | مسیر داده | چهار قدم | Four steps | none | Not rendered. «مسیر داده» was internals. |
| SH-069 | Home keys not rendered | `home.pipeline.title` (not rendered) | از فایل صوتی تا کنسول در چهار گام | از فایل صوتی تا پنل، در چهار قدم | From recording to console in four steps | HOME-17 | Not rendered. Glossary «پنل» and «قدم». |
| SH-070 | Home keys not rendered | `home.pipeline.steps[0].title` (not rendered) | ورود تماس | فایل را بفرستید | Send the file | HOME-08 | Not rendered. An action for the buyer. Longer: fa 9 to 15. |
| SH-071 | Home keys not rendered | `home.pipeline.steps[0].text` (not rendered) | فایل را بارگذاری کنید. ارسال از سیستم VOIP با وب‌هوک برنامه‌ریزی‌شده است. | فایل صوتی تماس را بارگذاری کنید. دریافت مستقیم از مرکز تلفن برنامه‌ریزی‌شده است. | Upload the call recording. Direct intake from your phone system is planned. | HOME-18 | Not rendered. «وب‌هوک» was internals. Planned label kept. Longer: en 58 to 75. |
| SH-072 | Home keys not rendered | `home.pipeline.steps[1].title` (not rendered) | تبدیل به متن | متن کامل مکالمه | Full transcript | HOME-19 | Not rendered. The result of this step. Longer: fa 12 to 15, en 10 to 15. |
| SH-073 | Home keys not rendered | `home.pipeline.steps[1].text` (not rendered) | گفتار به متن با Whisper؛ فارسی، انگلیسی یا تشخیص خودکار زبان. | مکالمه به فارسی یا انگلیسی به متن تبدیل می‌شود و زبان را هم می‌شود خودکار تشخیص داد. | The conversation becomes text in Persian or English, and the language can be detected for you. | HOME-19, OD-10 | Not rendered. Drops the model name. Longer: fa 61 to 84, en 66 to 94. |
| SH-074 | Home keys not rendered | `home.pipeline.steps[2].title` (not rendered) | تحلیل | خلاصه و اقدام‌ها | Summary and action items | HOME-20 | Not rendered. What the buyer gets. Longer: fa 5 to 16, en 7 to 24. |
| SH-075 | Home keys not rendered | `home.pipeline.steps[2].text` (not rendered) | مرحلهٔ تحلیل خلاصه، اقدام‌ها و بینش‌ها را می‌نویسد. | خلاصهٔ تماس، اقدام‌ها و بینش‌ها آماده می‌شود و هر کدام به جمله‌ای وصل است که از آن آمده. | You get a summary, action items and insights, each linked to the line it came from. | HOME-20, HOME-24 | Not rendered. «مرحلهٔ تحلیل» was an internal subject. Longer: fa 51 to 88, en 63 to 83. |
| SH-076 | Home keys not rendered | `home.pipeline.steps[3].title` (not rendered) | بازبینی | KEEP | KEEP | none | Not rendered. «بازبینی» is the team's own work here (glossary). |
| SH-077 | Home keys not rendered | `home.pipeline.steps[3].text` (not rendered) | نمای کلی، نتایج تماس و خروجی‌ها. دستیار هوشمند برنامه‌ریزی‌شده است. | نتیجه را در پنل بخوانید، جست‌وجو کنید یا برای هم‌تیمی‌ها بفرستید. دستیار هوشمند برنامه‌ریزی‌شده است. | Read the result in the console, search it or send it to a colleague. The copilot is planned. | HOME-21, QA-07, QA-08 | Not rendered. Actions instead of a list of screens. Longer: fa 67 to 100, en 57 to 92. |
| SH-078 | Home keys not rendered | `home.pipeline.link` (not rendered) | دیدن مسیر کامل پردازش | روش کار، قدم‌به‌قدم | Step by step | none | Not rendered. «مسیر کامل پردازش» was internals. |
| SH-079 | Home keys not rendered | `home.product.eyebrow` (not rendered) | یک کنسول واقعی، نه یک ایده | پنلی که همین امروز کار می‌کند | KEEP | HOME-22 | Not rendered. Glossary «پنل»; drops the needless «یک … یک». |
| SH-080 | Home keys not rendered | `home.product.title` (not rendered) | ساخته‌شده برای کار روزانهٔ تیم کیفیت | برای کار هر روز تیم کیفیت | Made for your QA team's everyday work | none | Not rendered. «ساخته‌شده برای» is a calque. |
| SH-081 | Home keys not rendered | `home.deployment.eyebrow` (not rendered) | دادهٔ شما، انتخاب شما برای محل نگهداری | داده‌های شما، جایی که خودتان می‌خواهید | Your data, where you want it | none | Not rendered. Old line stacked abstract nouns. |
| SH-082 | Home keys not rendered | `home.deployment.title` (not rendered) | امروز روی سرور شما یا ترکیبی؛ ابری برنامه‌ریزی‌شده | امروز روی سرور خودتان یا ترکیبی؛ نسخهٔ ابری برنامه‌ریزی‌شده | Self-hosted or hybrid today; managed cloud planned | HOME-30, OD-04 | Not rendered. Glossary phrases; planned label kept. Longer: fa 50 to 59, en 42 to 50. |
| SH-083 | Home keys not rendered | `home.trust.eyebrow` (not rendered) | حریم خصوصی یک ویژگی محصول است | از روز اول | From day one | HOME-45 | Not rendered. Same as the approved `story.trust.eyebrow`. |
| SH-084 | Home keys not rendered | `home.trust.title` (not rendered) | طوری طراحی شده که فایل صوتی هرگز از کنترل شما خارج نشود | طوری ساخته شده که فایل صوتی از کنترل شما خارج نشود | Built so the recording stays under your control | HOME-38 | Not rendered. Keeps «designed so» from HOME-38 instead of an absolute «never»; «طراحی شده» became the plainer «ساخته شده». |

## Length rule

Tight slots (eyebrows, labels, chips, hints, buttons, links and scene text) stay within 15 percent of the old line in each language, except:

| Row | Key | Characters | Note |
| --- | --- | --- | --- |
| SH-011 | `common.comingSoon` | fa 7 to 15, en 11 to 7 | Chip next to a heading on the solutions pages. One word on one line; check on the running page. |

Lines outside tight slots that grew by more than 15 percent (each has a note in its Reason):

| Row | Key | Characters |
| --- | --- | --- |
| SH-070 | `home.pipeline.steps[0].title` | fa 9 to 15 |
| SH-071 | `home.pipeline.steps[0].text` | en 58 to 75 |
| SH-072 | `home.pipeline.steps[1].title` | fa 12 to 15, en 10 to 15 |
| SH-073 | `home.pipeline.steps[1].text` | fa 61 to 84, en 66 to 94 |
| SH-074 | `home.pipeline.steps[2].title` | fa 5 to 16, en 7 to 24 |
| SH-075 | `home.pipeline.steps[2].text` | fa 51 to 88, en 63 to 83 |
| SH-077 | `home.pipeline.steps[3].text` | fa 67 to 100, en 57 to 92 |
| SH-082 | `home.deployment.title` | fa 50 to 59, en 42 to 50 |
