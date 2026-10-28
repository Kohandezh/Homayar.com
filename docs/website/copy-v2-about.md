# Website copy v2: About page

| Field | Value |
| --- | --- |
| Ticket | HOM-327, stage P, group A |
| Base commit | `5f2f75b` (the approved Home page and the style guide) |
| Date | 2026-10-28 |
| Status | Written with `docs/website/copy-style.md`; waiting for review |
| See it | `http://localhost:4960/fa/about` and `http://localhost:4960/en/about` |
| Rows | `AB-001` to `AB-043`. Persian changed in 16, English in 16 |
| How to answer | Name a row ID and say: keep, change to ..., or remove |

Rules: `docs/website/copy-style.md`. `(shared)` means other pages show the key too. `(not rendered)` means no component reads the key today; it is rewritten so no old wording stays in the dictionary.

## Owner questions

None. Every line uses a claim-register row or no claim.

## Lines in the order the visitor meets them

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| AB-001 | Head | `about.meta.title` | دربارهٔ همایار | KEEP | KEEP | none | Plain page title. |
| AB-002 | Head | `about.meta.description` | همایار مکالمه‌های ضبط‌شدهٔ مشتریان را به خلاصه، اقدام‌ها و بینش‌های قابل‌بازبینی تبدیل می‌کند، متصل به شواهد اصلی، قابل‌استقرار در محیط خصوصی شما؛ ابر مدیریت‌شده برنامه‌ریزی‌شده است. | همایار مکالمه‌های ضبط‌شدهٔ مشتریان را به متن، خلاصه، اقدام‌ها و بینش‌هایی تبدیل می‌کند که هر کدام به شواهد اصلی وصل‌اند؛ روی سرور خودتان یا ترکیبی، و نسخهٔ ابری برنامه‌ریزی‌شده است. | Homayar turns recorded customer calls into transcripts, summaries, action items and insights, each linked to the original evidence. On your own servers or hybrid; managed cloud is planned. | ABOUT-01, OD-04 | «قابل‌بازبینی» and «قابل‌استقرار» are banned patterns. |
| AB-003 | Hero | `about.hero.eyebrow` | دربارهٔ ما | KEEP | KEEP | none | Plain page name. |
| AB-004 | Hero | `about.hero.title` | بازبینی کیفیت تماس‌های فارسی مشتریان، با شواهد پیوست | بازبینی کیفیت تماس‌های فارسی، با شواهد کنار هر یافته | KEEP | ABOUT-02, OD-09 | «با شواهد پیوست» was stiff. «بازبینی کیفیت» stays: OD-09 kept it on About as a goal. |
| AB-005 | Hero | `about.hero.text` | همایار محصولی فارسی‌محور برای کیفیت مرکز تماس است. وعدهٔ ما ساده است: مکالمه‌های ضبط‌شدهٔ مشتریان را به ارزیابی‌های کیفی قابل‌بازبینی و مسائل عملیاتی تکرارشونده تبدیل کنیم، متصل به گفت‌وگوی اصلی، در محیطی که شما انتخاب می‌کنید. | همایار برای مراکز تماس فارسی‌زبان ساخته شده است. وعدهٔ ما ساده است: از مکالمه‌های ضبط‌شدهٔ مشتریان، ارزیابی کیفیتی بسازیم که بشود وارسی کرد و مشکل‌های تکراری را نشان دهیم؛ هر کدام وصل به مکالمهٔ اصلی و در محیطی که شما انتخاب می‌کنید. | Homayar is built for Persian-speaking contact centers. Our promise is simple: turn recorded customer calls into quality reviews you can check and repeat problems you can act on, each linked to the original call, in the environment you choose. | ABOUT-03 | «فارسی‌محور» and «قابل‌بازبینی» were stiff; glossary «مکالمه»، «مشکل‌ها». |
| AB-006 | Principles | `about.constellation.eyebrow` | شش اصل | KEEP | KEEP | none | Keeps: plain and short. |
| AB-007 | Principles | `about.constellation.hint` (not rendered) | اسکرول کنید: پیوندها کشیده می‌شوند | شش اصلی که همایار را با آن‌ها می‌سازیم | Six rules we build Homayar by | none | Not rendered (`StarMap.tsx:16` reads only the eyebrow). Old line was a scroll instruction and scene narration. |
| AB-008 | Principles | `about.principles.title` (not rendered) | چطور می‌سازیم | KEEP | KEEP | none | Not rendered. Plain. |
| AB-009 | Principles | `about.principles.items[0].icon` | file | KEEP | KEEP | none | Icon name, not text. |
| AB-010 | Principles | `about.principles.items[0].title` | شواهد، نه حدس | KEEP | Evidence, not guesses | ABOUT-04 | Persian keeps: three words, a clear stand. English «vibes» was slang. |
| AB-011 | Principles | `about.principles.items[0].text` | هر یافته کنار متنی می‌نشیند که از آن آمده. عددی که گفت‌وگویی پشتش نیست، یافته نیست. | هر یافته کنار جمله‌ای از مکالمه است که از آن آمده. عددی که مکالمه‌ای پشتش نباشد، یافته نیست. | Every finding sits next to the line of the call it came from. A number with no call behind it is not a finding. | ABOUT-04 | Glossary «مکالمه» instead of «گفت‌وگو». |
| AB-012 | Principles | `about.principles.items[1].icon` | check | KEEP | KEEP | none | Icon name, not text. |
| AB-013 | Principles | `about.principles.items[1].title` | وضعیت‌های صادقانه | KEEP | Honest states | ABOUT-05 | Persian keeps. English matches the how-it-works eyebrow. |
| AB-014 | Principles | `about.principles.items[1].text` | اگر مرحله‌ای نتواند اجرا شود، کنسول می‌گوید. خروجی سرویس آزمایشی برچسب می‌خورد. کمبودها را با مقدار پیش‌فرض پر نمی‌کنیم. | اگر مرحله‌ای اجرا نشود، پنل همین را می‌گوید. خروجی سرویس آزمایشی برچسب می‌خورد. جای خالی را با مقدار پیش‌فرض پر نمی‌کنیم. | KEEP | ABOUT-05 | Glossary «پنل»; «کمبودها» became «جای خالی». |
| AB-015 | Principles | `about.principles.items[2].icon` | shield | KEEP | KEEP | none | Icon name, not text. |
| AB-016 | Principles | `about.principles.items[2].title` | حریم خصوصی در طراحی | حریم خصوصی، از روز اول | Privacy from day one | ABOUT-06 | Same words as the approved Home eyebrow «از روز اول». Longer: fa 19 to 22, en 17 to 20. |
| AB-017 | Principles | `about.principles.items[2].text` | پوشاندن، جداسازی مستأجرها، نگهداری صریح و حذف پایدار بخشی از هستهٔ محصول‌اند، نه یک سطح ویژه. | پوشاندن داده‌های شخصی، جدا نگه داشتن داده‌های هر سازمان، مدت نگهداری روشن و حذف کامل در خود محصول‌اند، نه در یک نسخهٔ گران‌تر. | Personal-data masking, keeping each organization's data apart, a clear retention period and real deletion are in the product itself, not in a premium tier. | ABOUT-06 | «هستهٔ محصول» and «مستأجر» were internals; «سطح ویژه» became plain words. Longer: fa 93 to 126, en 116 to 155. |
| AB-018 | Principles | `about.principles.items[3].icon` | globe | KEEP | KEEP | none | Icon name, not text. |
| AB-019 | Principles | `about.principles.items[3].title` | اول فارسی، همیشه دوزبانه | KEEP | KEEP | ABOUT-13 | Keeps: short and memorable. |
| AB-020 | Principles | `about.principles.items[3].text` | رابط و متن مکالمه‌ها به فارسی و انگلیسی کار می‌کنند. راست‌به‌چپ از اول در طراحی بوده است. | پنل و متن مکالمه‌ها به فارسی و انگلیسی کار می‌کنند و چینش راست‌به‌چپ از روز اول در طراحی بوده است. | The console and the transcripts work in Persian and English, and right-to-left layout was there from day one. | ABOUT-07 | Glossary «پنل»; one sentence. Longer: en 86 to 109. |
| AB-021 | Principles | `about.principles.items[4].icon` | server | KEEP | KEEP | none | Icon name, not text. |
| AB-022 | Principles | `about.principles.items[4].title` | محیط شما | KEEP | KEEP | ABOUT-08 | Keeps: two words. |
| AB-023 | Principles | `about.principles.items[4].text` | امروز روی سرور شما یا ترکیبی؛ ابر مدیریت‌شده برنامه‌ریزی‌شده است. عملیات خصوصی یک پروفایل پشتیبانی‌شده از همان محصول است. | امروز روی سرور خودتان یا ترکیبی؛ نسخهٔ ابری برنامه‌ریزی‌شده است. نسخهٔ روی سرور خودتان همان محصول است، نه نسخه‌ای جدا. | Self-hosted or hybrid today; managed cloud is planned. The self-hosted version is the same product, not a separate one. | ABOUT-08, OD-04, OD-16 | «پروفایل پشتیبانی‌شده» was internals. Glossary phrases. |
| AB-024 | Principles | `about.principles.items[5].icon` | scale | KEEP | KEEP | none | Icon name, not text. |
| AB-025 | Principles | `about.principles.items[5].title` | ادعاهایی که می‌توانیم نشان دهیم | KEEP | KEEP | ABOUT-09 | Keeps: natural and honest. |
| AB-026 | Principles | `about.principles.items[5].text` | نام مشتری، درصد یا گواهینامه‌ای را که نتوانیم با شواهد پشتیبانی کنیم منتشر نمی‌کنیم. | KEEP | KEEP | ABOUT-09 | Keeps: a plain, honest promise. |
| AB-027 | How we work | `about.stepper.hint` | اسکرول کنید: قدم به قدم | چهار قدم تا استقرار | Four steps to rollout | ABOUT-11 | Old line was a scroll instruction. |
| AB-028 | How we work | `about.howWeWork.title` | با مشتریان جدید چطور کار می‌کنیم | KEEP | KEEP | ABOUT-11 | Keeps: the buyer's own question. |
| AB-029 | How we work | `about.stepper.step` | قدم | KEEP | KEEP | none | Counter word («قدم ۱ از ۴»). |
| AB-030 | How we work | `about.stepper.of` | از | KEEP | KEEP | none | Counter word. |
| AB-031 | How we work | `about.howWeWork.steps[0].title` | شناخت | KEEP | KEEP | ABOUT-11 | Step name. |
| AB-032 | How we work | `about.howWeWork.steps[0].text` | صف تماس‌ها، دسترسی به فایل‌های صوتی، فرایند فعلی کیفیت و جایی که داده باید بماند. | با هم مرور می‌کنیم: صف تماس‌ها، دسترسی به فایل‌های صوتی، روند فعلی کنترل کیفیت و جایی که داده باید بماند. | We go through your queue, recording access, current QA process and where the data must stay. | ABOUT-11 | A sentence with a verb instead of a noun list. Longer: fa 81 to 105, en 74 to 92. |
| AB-033 | How we work | `about.howWeWork.steps[1].title` | نمونه و خط پایه | KEEP | KEEP | ABOUT-11 | Step name. |
| AB-034 | How we work | `about.howWeWork.steps[1].text` | نمونه‌ای مجاز از تماس‌ها و زمان بازبینی فعلی شما، تا نتایج در برابر چیزی واقعی سنجیده شوند. | نمونه‌ای مجاز از تماس‌هایتان و زمانی را که امروز صرف بازبینی می‌کنید می‌گیریم تا نتیجه با چیزی واقعی مقایسه شود. | We take an authorized sample of your calls and your current review time, so results are measured against something real. | ABOUT-11 | Active voice; «بازبینی» is the team's own work (glossary). Longer: fa 91 to 112. |
| AB-035 | How we work | `about.howWeWork.steps[2].title` | پایلوت اندازه‌گیری‌شده | KEEP | KEEP | ABOUT-11 | Step name. |
| AB-036 | How we work | `about.howWeWork.steps[2].text` | پایلوتی با دامنهٔ مشخص و معیارهای موفقیت توافق‌شده. شکست‌ها در کنار موفقیت‌ها گزارش می‌شوند. | پایلوتی با دامنهٔ روشن و معیار موفقیتی که با هم توافق می‌کنیم. آنچه جواب نداد را هم کنار آنچه جواب داد گزارش می‌دهیم. | A pilot with a clear scope and success criteria we agree on. We report what did not work next to what did. | ABOUT-11 | Active voice; «شکست‌ها ... گزارش می‌شوند» was a calque. Longer: fa 92 to 117, en 87 to 106. |
| AB-037 | How we work | `about.howWeWork.steps[3].title` | استقرار | KEEP | KEEP | ABOUT-11 | Step name. |
| AB-038 | How we work | `about.howWeWork.steps[3].text` | آموزش، پذیرش و تحویل به پشتیبانی. | آموزش تیم شما، پذیرش نهایی و تحویل به پشتیبانی. | Training for your team, final acceptance and a handover to support. | ABOUT-11 | Names who is trained. Longer: fa 33 to 47, en 43 to 67. |
| AB-039 | Who and where | `about.who.title` | برای چه کسانی است | KEEP | KEEP | none | Keeps: the buyer's question. |
| AB-040 | Who and where | `about.who.text` | مراکز تماس و عملیات خدماتی که تماس‌ها را به فارسی یا انگلیسی ضبط می‌کنند و می‌خواهند تیم کیفیت، سرپرست‌ها و مدیران عملیات‌شان از یک دادهٔ بازبینی‌شده کار کنند. خریدار اول معمولاً مدیر مرکز تماس یا تجربهٔ مشتری است؛ کاربران روزانه مدیران کیفیت و سرپرست‌ها هستند؛ فناوری اطلاعات و امنیت یکپارچه‌سازی را تأیید می‌کنند. | برای مراکز تماس و واحدهای خدماتی که تماس‌ها را به فارسی یا انگلیسی ضبط می‌کنند و می‌خواهند تیم کیفیت، سرپرست‌ها و مدیران عملیات از یک دادهٔ مشترک و وارسی‌شده کار کنند. معمولاً مدیر مرکز تماس یا تجربهٔ مشتری تصمیم به خرید می‌گیرد، مدیران کیفیت و سرپرست‌ها هر روز با آن کار می‌کنند و تیم فناوری اطلاعات و امنیت اتصال را تأیید می‌کند. | For contact centers and service teams that record calls in Persian or English and want their QA team, supervisors and operations leaders to work from the same checked data. The buyer is usually the head of the contact center or customer experience; QA managers and supervisors use it every day; IT and security approve the integration. | ABOUT-10 | Plainer Persian; «مدیر» always with a noun (glossary). |
| AB-041 | Who and where | `common.inProgress` (shared) | در حال آماده‌سازی | KEEP | KEEP | none | Status chip, also on pricing, legal and transparency. |
| AB-042 | Who and where | `about.status.title` | کجا هستیم | KEEP | KEEP | none | Keeps: plain. |
| AB-043 | Who and where | `about.status.text` | همایار محصولی در مراحل اولیه است. تا وقتی مشتری اجازه نداده و اعداد روشی پشت خود ندارند، لوگو یا نتیجه‌ای از مشتریان نشان نمی‌دهیم. | KEEP | KEEP | ABOUT-12 | Keeps: honest and natural Persian. |

## Length rule

Tight slots (eyebrows, labels, chips, hints, buttons, links and scene text) stay within 15 percent of the old line in each language. No exception.

Lines outside tight slots that grew by more than 15 percent (each has a note in its Reason):

| Row | Key | Characters |
| --- | --- | --- |
| AB-016 | `about.principles.items[2].title` | fa 19 to 22, en 17 to 20 |
| AB-017 | `about.principles.items[2].text` | fa 93 to 126, en 116 to 155 |
| AB-020 | `about.principles.items[3].text` | en 86 to 109 |
| AB-032 | `about.howWeWork.steps[0].text` | fa 81 to 105, en 74 to 92 |
| AB-034 | `about.howWeWork.steps[1].text` | fa 91 to 112 |
| AB-036 | `about.howWeWork.steps[2].text` | fa 92 to 117, en 87 to 106 |
| AB-038 | `about.howWeWork.steps[3].text` | fa 33 to 47, en 43 to 67 |
