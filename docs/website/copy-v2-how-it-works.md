# Website copy v2: How it works page

| Field | Value |
| --- | --- |
| Ticket | HOM-327, stage P, group A |
| Base commit | `5f2f75b` (the approved Home page and the style guide) |
| Date | 2026-10-28 |
| Status | Written with `docs/website/copy-style.md`; waiting for review |
| See it | `http://localhost:4960/fa/how-it-works` and `http://localhost:4960/en/how-it-works` |
| Rows | `HW-001` to `HW-059`. Persian changed in 26, English in 24 |
| How to answer | Name a row ID and say: keep, change to ..., or remove |

Rules: `docs/website/copy-style.md`. `(shared)` means other pages show the key too. `(not rendered)` means no component reads the key today; it is rewritten so no old wording stays in the dictionary.

## Owner questions

None. Every line uses a claim-register row or no claim.

## Lines in the order the visitor meets them

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| HW-001 | Head | `howItWorks.meta.title` | نحوهٔ کار: مسیر پردازش همایار | نحوهٔ کار: از فایل صوتی تا گزارش، قدم‌به‌قدم | How it works: from recording to report, step by step | none | «نحوهٔ کار» is the fixed page name (style guide exception). «مسیر پردازش» was internals. Longer: fa 29 to 44, en 45 to 52. |
| HW-002 | Head | `howItWorks.meta.description` | گام به گام: یک فایل صوتی چطور آماده، به متن تبدیل، در صورت امکان بر اساس گوینده تفکیک، همراه با شواهد خلاصه و سپس بازبینی می‌شود، و کنسول چطور مرحله‌های ناقص یا ناموفق را صادقانه گزارش می‌کند. | قدم‌به‌قدم بدانید با یک فایل صوتی چه می‌شود: بررسی فایل، متن کامل مکالمه، خلاصه و اقدام‌ها با شواهد، و کار تیم شما در پنل. هر مرحله وضعیت واقعی خودش را نشان می‌دهد، حتی وقتی ناقص یا ناموفق است. | Step by step: how a recording is checked, transcribed and summarized with evidence, and how your team works with it in the console. Every stage shows its true state, even when it is partial or failed. | HOW-01 | The reader's question first; «صادقانه گزارش می‌کند» became what they see. |
| HW-003 | Hero | `howItWorks.hero.eyebrow` | مسیر پردازش | قدم‌به‌قدم | The steps | none | «مسیر پردازش» was internals. English «Step by step» is over the length limit. |
| HW-004 | Hero | `howItWorks.hero.title` | بر سر یک فایل صوتی چه می‌آید، گام به گام | از فایل صوتی تا گزارش | From recording to report | none | «بر سر یک فایل چه می‌آید» was odd Persian. «قدم‌به‌قدم» is left to the eyebrow, so the two lines do not repeat. |
| HW-005 | Hero | `howItWorks.hero.text` | پردازش روی سرور اجرا می‌شود. می‌توانید صفحه را ترک کنید و برگردید؛ هر مرحله وضعیت خودش را گزارش می‌دهد. | لازم نیست منتظر بمانید: پردازش روی سرور انجام می‌شود و هر وقت برگردید، هر مرحله وضعیت خودش را نشان می‌دهد. | No need to wait on the page: processing runs on the server, and when you come back, every stage shows its own state. | HOW-02 | Leads with the benefit (you do not wait). |
| HW-006 | Hero | `howItWorks.snake.hint` | شبیه‌سازی با داده‌ی نمونه. اسکرول کنید: خط همراه شما حرکت می‌کند | شبیه‌سازی با داده‌ی نمونه: یک تماس، از فایل صوتی تا گزارش | Simulation with sample data: one call, from recording to report | HOW-11 | The scroll instruction is gone; the simulation label stays in the same line. |
| HW-007 | Step 1 | `howItWorks.steps[0].stage` | آماده‌سازی فایل | KEEP | KEEP | MOCK-21 | Stage name, the same as the sample stages. |
| HW-008 | Step 1 | `howItWorks.steps[0].title` | فایل پیش از هر چیز بررسی می‌شود | KEEP | KEEP | HOW-21 | Keeps: short, natural, and it says what protects the buyer. |
| HW-009 | Step 1 | `howItWorks.steps[0].text` | اندازه، امضای فایل و مدت واقعی صدا بررسی می‌شوند. فایل اصلی دست‌نخورده می‌ماند و پردازش روی یک نسخهٔ بررسی‌شده انجام می‌شود. | اندازه، امضای فایل و مدت واقعی صدا بررسی می‌شود. فایل اصلی شما دست‌نخورده می‌ماند و همایار روی نسخه‌ای بررسی‌شده کار می‌کند. | Size, file signature and real audio duration are checked. Your original recording stays untouched; Homayar works on a checked copy. | HOW-03 | «فایل اصلی شما» makes the benefit personal; drops «یک». |
| HW-010 | Step 1 | `howItWorks.snake.checks[0]` | حجم | KEEP | KEEP | HOW-12 | Sample label. |
| HW-011 | Step 1 | `howItWorks.snake.checks[1]` | امضای فایل | KEEP | KEEP | HOW-12 | Sample label. |
| HW-012 | Step 1 | `howItWorks.snake.checks[2]` | مدت | KEEP | KEEP | HOW-12 | Sample label. |
| HW-013 | Step 2 | `howItWorks.steps[1].stage` | تبدیل گفتار به متن | KEEP | KEEP | MOCK-21 | Stage name (glossary). |
| HW-014 | Step 2 | `howItWorks.steps[1].title` | Whisper گفتار را به متن تبدیل می‌کند | متن کامل مکالمه، با زمان هر جمله | A full transcript, with the time of every line | HOW-05 | The result instead of the model name (Whisper is internals). Longer: en 38 to 46. |
| HW-015 | Step 2 | `howItWorks.steps[1].text` | زبان همان است که برای فایل تعیین کرده‌اید، یا تشخیص داده می‌شود. متن، زمان‌بندی را نگه می‌دارد تا مرحله‌های بعدی بتوانند به گفت‌وگو اشاره کنند. | زبان را برای هر فایل خودتان تعیین می‌کنید یا خودکار تشخیص داده می‌شود. زمان هر جمله در متن می‌ماند، پس هر نکتهٔ گزارش به همان لحظهٔ مکالمه برمی‌گردد. | You set the language for each recording, or it is detected. The time of every line stays in the transcript, so each point in the report goes back to its moment in the call. | HOW-05 | Says why timestamps matter to the buyer. «گفت‌وگو» became «مکالمه». |
| HW-016 | Step 3 | `howItWorks.steps[2].stage` | تفکیک گوینده‌ها | KEEP | KEEP | MOCK-21 | Stage name (glossary). |
| HW-017 | Step 3 | `howItWorks.steps[2].title` | برنامه‌ریزی‌شده: نوبت‌ها به گوینده‌ها نسبت داده می‌شوند | برنامه‌ریزی‌شده: جدا کردن حرف مشتری و کارشناس | Planned: telling the customer and the agent apart | HOW-06 | What speaker separation means for the buyer. Planned label kept. Longer: en 39 to 49. |
| HW-018 | Step 3 | `howItWorks.steps[2].text` | تفکیک گوینده برنامه‌ریزی‌شده است. امروز آن مرحله با ذکر دلیل «در دسترس نیست» علامت می‌خورد و بقیهٔ مسیر روی متن ادامه پیدا می‌کند. | تفکیک گوینده‌ها برنامه‌ریزی‌شده است. امروز این مرحله با دلیلش «در دسترس نیست» نشان داده می‌شود و بقیهٔ کار روی متن ادامه پیدا می‌کند. | Speaker separation is planned. Today this stage shows as unavailable, with the reason, and the rest of the work continues on the text. | HOW-07 | Glossary «تفکیک گوینده‌ها»; «علامت می‌خورد» became «نشان داده می‌شود». |
| HW-019 | Step 3 | `howItWorks.snake.agents[0]` | کیفیت | KEEP | KEEP | HOW-13 | Sample label of a planned part, inside the labeled simulation. |
| HW-020 | Step 3 | `howItWorks.snake.agents[1]` | مشکلات | مشکل | KEEP | HOW-13 | Glossary: no «مشکلات». The plural «مشکل‌ها» is over the length limit. |
| HW-021 | Step 3 | `howItWorks.snake.agents[2]` | بینش | KEEP | KEEP | HOW-13 | Sample label. |
| HW-022 | Step 3 | `howItWorks.snake.agents[3]` | آموزش | KEEP | Coaching | HOW-13 | Glossary «coaching» instead of «Teacher». |
| HW-023 | Step 4 | `howItWorks.steps[3].stage` | خلاصه و تحلیل | KEEP | KEEP | MOCK-21 | Stage name. |
| HW-024 | Step 4 | `howItWorks.steps[3].title` | خلاصه‌ای همراه با شواهد | خلاصه و اقدام‌ها، با شواهد | A summary and action items, with evidence | HOW-08 | Names both results the buyer gets. Longer: en 23 to 41. |
| HW-025 | Step 4 | `howItWorks.steps[3].text` | مرحلهٔ تحلیل خلاصه، اقدام‌ها و بینش‌ها را می‌نویسد و هر کدام به خطوط متن پشت آن وصل است. امتیاز کیفیت، تشخیص مشکل و آموزش برنامه‌ریزی‌شده‌اند. خروجی سرویس آزمایشی برچسب می‌خورد و هرگز به‌عنوان تحلیل واقعی ارائه نمی‌شود. | خلاصه، اقدام‌ها و بینش‌ها آماده می‌شود و هر کدام به جمله‌ای از مکالمه وصل است که از آن آمده. امتیاز کیفیت، ریشهٔ مشکل و آموزش کارشناس برنامه‌ریزی‌شده‌اند. خروجی سرویس آزمایشی برچسب می‌خورد و هرگز تحلیل واقعی جا زده نمی‌شود. | You get a summary, action items and insights, each linked to the line it came from. QC scoring, root causes and coaching are planned. Test-provider output is labeled and never passed off as a real analysis. | HOW-09 | «مرحلهٔ تحلیل» is no longer the subject. Glossary names for the planned parts. |
| HW-026 | Step 5 | `howItWorks.steps[4].stage` | بازبینی | KEEP | KEEP | none | Glossary «بازبینی»: here the team does the review. |
| HW-027 | Step 5 | `howItWorks.steps[4].title` | تیم شما در کنسول کار می‌کند | تیم شما در پنل کار می‌کند | KEEP | none | Glossary «پنل» (owner, Home Q-01). |
| HW-028 | Step 5 | `howItWorks.steps[4].text` | نمای کلی، نتایج تماس، متن مکالمه، جست‌وجو، لینک‌های اشتراک و خروجی‌ها. پوشاندن داده‌های شخصی و قواعد دسترسی روی همهٔ نماها اعمال می‌شود. | تیم شما نتیجهٔ هر تماس و متن مکالمه را می‌خواند، جست‌وجو می‌کند و با لینک اشتراک یا خروجی می‌فرستد. قواعد دسترسی همه‌جا برقرار است و اگر پوشاندن داده‌های شخصی روشن باشد، در همهٔ نماها اعمال می‌شود. | Your team reads each call's result and transcript, searches, and shares through share links or exports. Access rules apply everywhere, and personal-data masking, when it is on, applies to every view. | HOW-10, SEC-10 | A sentence with verbs instead of a noun list. Masking is «when it is on» (SEC-10). Longer: fa 136 to 197, en 128 to 199. |
| HW-029 | Step 5 | `howItWorks.snake.tiles[0]` | نمای کلی | KEEP | KEEP | HOW-14 | Glossary «نمای کلی». |
| HW-030 | Step 5 | `howItWorks.snake.tiles[1]` | نتایج تماس | KEEP | KEEP | HOW-14 | Sample label. |
| HW-031 | Step 5 | `howItWorks.snake.tiles[2]` | جست‌وجو | KEEP | KEEP | HOW-14 | Sample label. |
| HW-032 | Cards | `howItWorks.snake.passing` | خط این‌جاست | مرحلهٔ فعلی | Current step | none | «خط این‌جاست» narrated the animation. |
| HW-033 | Cards | `howItWorks.rail.todo` | شروع نشده | KEEP | KEEP | HOW-19 | Status word. |
| HW-034 | Cards | `howItWorks.rail.done` | انجام شد | KEEP | KEEP | HOW-19 | Status word. |
| HW-035 | Cards | `howItWorks.rail.running` | در حال اجرا | KEEP | KEEP | HOW-19 | Status word. |
| HW-036 | States | `howItWorks.states.eyebrow` | وضعیت‌های صادقانه | وضعیت هر مرحله | Per-stage status | HOW-19 | Plainer than «وضعیت‌های صادقانه». English matches the Persian; "The state of each stage" (23) is over the limit of 17. |
| HW-037 | States | `howItWorks.states.title` | مرحلهٔ ناقص نشان داده می‌شود، نه پر | اگر چیزی کم باشد، همان را می‌بینید | If something is missing, you see it | HOW-21 | «نشان داده می‌شود، نه پر» was a calque; the reader is the subject. |
| HW-038 | States | `howItWorks.states.text` | هر مرحله از یک کار، وضعیتی صریح دارد. اگر مرحله‌ای متوقف شود، گزارش دلیل آن را به زبان کنسول نشان می‌دهد. | هر مرحله وضعیت روشنی دارد. اگر مرحله‌ای متوقف شود، گزارش دلیلش را به زبان پنل نشان می‌دهد. | Every stage has a clear state. If a stage stops, the report shows why, in the language of the console. | HOW-18 | Shorter; glossary «پنل». |
| HW-039 | States | `howItWorks.states.items[0].label` | شروع نشده | KEEP | KEEP | HOW-19 | Status word. |
| HW-040 | States | `howItWorks.states.items[0].text` | هنوز به این مرحله نرسیده است. | KEEP | KEEP | HOW-19 | Short and clear. |
| HW-041 | States | `howItWorks.states.items[1].label` | در صف | KEEP | KEEP | HOW-19 | Status word, as the console shows it. |
| HW-042 | States | `howItWorks.states.items[1].text` | فایل در صف پردازش است. | فایل در نوبت پردازش است. | The recording is waiting its turn. | HOW-19 | «در نوبت» is the natural Persian; no job-queue word. |
| HW-043 | States | `howItWorks.states.items[2].label` | در حال اجرا | KEEP | KEEP | HOW-19 | Status word. |
| HW-044 | States | `howItWorks.states.items[2].text` | این مرحله همین حالا در حال پردازش است. | KEEP | KEEP | HOW-19 | Short and clear. |
| HW-045 | States | `howItWorks.states.items[3].label` | ناقص | KEEP | KEEP | HOW-19 | Status word. |
| HW-046 | States | `howItWorks.states.items[3].text` | پردازش با کمبود تمام شد؛ مرحله‌ها نشان می‌دهند چه چیزی کم است. | KEEP | KEEP | HOW-19 | Short and clear. |
| HW-047 | States | `howItWorks.states.items[4].label` | در دسترس نیست | KEEP | KEEP | HOW-19 | Status word. |
| HW-048 | States | `howItWorks.states.items[4].text` | مرحله نتوانست اجرا شود، مثلاً وقتی تفکیک گوینده برای این فایل در دسترس نیست. | مرحله نتوانست اجرا شود، مثلاً وقتی تفکیک گوینده‌ها برای این فایل در دسترس نیست. | KEEP | HOW-19 | Glossary «تفکیک گوینده‌ها». |
| HW-049 | States | `howItWorks.states.items[5].label` | ناموفق | KEEP | KEEP | HOW-19 | Status word. |
| HW-050 | States | `howItWorks.states.items[5].text` | آخرین تلاش با دلیلی مشخص شکست خورد. تلاش‌های خودکار محدود و قابل‌مشاهده‌اند. | آخرین تلاش با دلیلی مشخص ناموفق ماند. تلاش‌های دوبارهٔ خودکار محدودند و همه را می‌بینید. | The last attempt failed for a named reason. Automatic retries are limited, and you can see each one. | HOW-19 | «قابل‌مشاهده‌اند» is a banned pattern; the reader is the subject. Longer: fa 76 to 88. |
| HW-051 | AI policy | `howItWorks.policy.eyebrow` | هوش مصنوعی کجا اجرا می‌شود | KEEP | KEEP | HOW-20 | Keeps: the IT buyer's own question, in natural Persian. |
| HW-052 | AI policy | `howItWorks.policy.title` | هر مرحله اول سیاست فضای کاری را بررسی می‌کند | هوش مصنوعی فقط همان‌جایی اجرا می‌شود که شما اجازه داده‌اید | AI runs only where you allow it | HOW-20 | The buyer's control is the message; «هر مرحله ... بررسی می‌کند» was internals. Longer: fa 44 to 58. |
| HW-053 | AI policy | `howItWorks.policy.text` | هر فضای کاری یک سیاست هوش مصنوعی دارد: کدام ارائه‌دهنده‌ها، منطقه‌ها و مدل‌ها برای تبدیل گفتار به متن، تفکیک گوینده و تحلیل مجازند. سیاست پیش از اجرای هر مرحله بررسی می‌شود. ارائه‌دهنده‌ای که سیاست اجازه نمی‌دهد، هرگز فراخوانی نمی‌شود و هرگز به‌عنوان جایگزین استفاده نمی‌شود. | هر فضای کاری سیاست هوش مصنوعی خودش را دارد: کدام ارائه‌دهنده، منطقه و مدل برای تبدیل گفتار به متن، تفکیک گوینده‌ها و تحلیل مجاز است. این سیاست پیش از هر مرحله بررسی می‌شود. ارائه‌دهنده‌ای که مجاز نیست هرگز به کار گرفته نمی‌شود، حتی به‌عنوان جایگزین. | Each workspace has its own AI policy: which providers, regions and models may be used for speech to text, speaker separation and analysis. The policy is checked before every stage. A provider it does not allow is never used, not even as a fallback. | HOW-20 | Same facts, plainer. Glossary «سیاست هوش مصنوعی»، «فضای کاری» (a product setting). |
| HW-054 | AI policy | `howItWorks.snake.providers[0].name` | Whisper محلی | KEEP | KEEP | HOW-17 | Sample provider name; IT readers need it. |
| HW-055 | AI policy | `howItWorks.snake.providers[1].name` | مدل تحلیل محلی | KEEP | KEEP | HOW-17 | Sample provider name. |
| HW-056 | AI policy | `howItWorks.snake.providers[2].name` | سرویس ابری خارج از سیاست | KEEP | KEEP | HOW-17 | Sample provider name. |
| HW-057 | AI policy | `howItWorks.snake.allowed` | مجاز | KEEP | KEEP | HOW-17 | Verdict label. |
| HW-058 | AI policy | `howItWorks.snake.refused` | رد شد | KEEP | KEEP | HOW-17 | Verdict label. |
| HW-059 | AI policy | `howItWorks.policy.link` | بخوانید مسیریابی و خروج داده چطور کار می‌کند | کنترل خروج داده، با جزئیات | Egress control in detail | none | Link label; «بخوانید ... چطور کار می‌کند» was long and «مسیریابی» was internals. |

## Length rule

Tight slots (eyebrows, labels, chips, hints, buttons, links and scene text) stay within 15 percent of the old line in each language. No exception.

Lines outside tight slots that grew by more than 15 percent (each has a note in its Reason):

| Row | Key | Characters |
| --- | --- | --- |
| HW-001 | `howItWorks.meta.title` | fa 29 to 44, en 45 to 52 |
| HW-014 | `howItWorks.steps[1].title` | en 38 to 46 |
| HW-017 | `howItWorks.steps[2].title` | en 39 to 49 |
| HW-024 | `howItWorks.steps[3].title` | en 23 to 41 |
| HW-028 | `howItWorks.steps[4].text` | fa 136 to 197, en 128 to 199 |
| HW-050 | `howItWorks.states.items[5].text` | fa 76 to 88 |
| HW-052 | `howItWorks.policy.title` | fa 44 to 58 |
