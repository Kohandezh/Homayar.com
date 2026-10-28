# Website copy v2: Product page

| Field | Value |
| --- | --- |
| Ticket | HOM-327, stage P, group A |
| Base commit | `5f2f75b` (the approved Home page and the style guide) |
| Date | 2026-10-28 |
| Status | Written with `docs/website/copy-style.md`; waiting for review |
| See it | `http://localhost:4960/fa/product` and `http://localhost:4960/en/product` |
| Rows | `PR-001` to `PR-108`. Persian changed in 56, English in 58 |
| How to answer | Name a row ID and say: keep, change to ..., or remove |

Rules: `docs/website/copy-style.md`. `(shared)` means other pages show the key too. `(not rendered)` means no component reads the key today; it is rewritten so no old wording stays in the dictionary.

## Owner questions

None. Every line uses a claim-register row or no claim.

## Lines in the order the visitor meets them

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PR-001 | Head | `product.meta.title` | محصول: پلتفرم تحلیل همایار | محصول: متن، خلاصه و اقدام‌ها برای هر تماس | Product: a transcript, summary and action items for every call | HOME-02 | The benefit instead of «پلتفرم تحلیل». Longer: fa 26 to 41, en 38 to 62. |
| PR-002 | Head | `product.meta.description` | ورود فایل صوتی با بارگذاری، تبدیل گفتار به متن با Whisper، دریافت خلاصه، اقدام‌ها و بینش‌های متصل به شواهد، و بازبینی آن‌ها در کنسول. عامل‌های کیفیت، وب‌هوک VOIP، خروجی PDF و دستیار هوشمند برنامه‌ریزی‌شده‌اند. | فایل صوتی تماس را بارگذاری کنید و متن کامل، خلاصه، اقدام‌ها و بینش‌ها را بگیرید؛ هر نکته به جمله‌ای از مکالمه وصل است. همه را در پنل بخوانید، جست‌وجو کنید و بفرستید. امتیاز کیفیت، اتصال مرکز تلفن، خروجی PDF و دستیار هوشمند برنامه‌ریزی‌شده‌اند. | Upload a call recording and get a full transcript, a summary, action items and insights, each linked to the line it came from. Read, search and share them in the console. QC scoring, phone system connection, PDF export and the copilot are planned. | PROD-01, QA-07, QA-08 | Buyer actions with verbs. «عامل‌های کیفیت» and «Whisper» were internals. Planned items keep their label. Longer: fa 209 to 243, en 207 to 247. |
| PR-003 | Hero | `product.hero.eyebrow` | نمای کلی پلتفرم | پنل همایار | The Homayar console | none | «نمای کلی پلتفرم» was a stiff page name. Glossary «پنل» (owner, Home Q-01). Names what the page shows, without repeating the title's «از ... تا تصمیم». |
| PR-004 | Hero | `product.hero.title` | همهٔ آنچه بین فایل صوتی و تصمیم قرار دارد | از فایل صوتی تماس تا تصمیم سرپرست | From the call recording to your supervisor's decision | HOME-02 | The buyer's path. «همهٔ آنچه ... قرار دارد» was abstract and wide. |
| PR-005 | Hero | `product.hero.text` | همایار یک موتور تحلیل پس از تماس است. فایل صوتی را می‌گیرد، متن تولید می‌کند، خلاصه را با اقدام‌ها و بینش‌ها می‌نویسد و نتیجه را در کنسولی نشان می‌دهد که تیم شما می‌تواند بر اساس آن عمل کند. | فایل صوتی تماس را بدهید؛ همایار متن کامل، خلاصه، اقدام‌ها و بینش‌ها را آماده می‌کند و تیم شما نتیجه را در پنل می‌خواند و پیگیری می‌کند. | Hand over the call recording. Homayar prepares the full transcript, a summary, action items and insights, and your team reads and follows up in the console. | PROD-02 | «موتور تحلیل» was internals; the line ends in the team's result. |
| PR-006 | 1. Upload | `product.sections.ingest.id` | ingest | KEEP | KEEP | none | Anchor id and React key, not visible. |
| PR-007 | 1. Upload | `product.sections.ingest.label` | ورود داده · صفحهٔ شبیه‌سازی‌شده | بارگذاری · صفحهٔ شبیه‌سازی‌شده | Upload · simulated screen | PROD-07 | Keeps the simulation label. «ورود داده» was a technical name. |
| PR-008 | 1. Upload | `product.sections.ingest.title` | فایل را بارگذاری کنید. اتصال مرکز تلفن برنامه‌ریزی‌شده است. | KEEP | Upload a file. A phone system connection is planned. | PROD-08 | Persian keeps: a direct action and an honest planned label. English: glossary «phone system» instead of «PBX». Longer: en 43 to 52. |
| PR-009 | 1. Upload | `product.sections.ingest.text` | امروز یک نفر فایل را از کنسول بارگذاری می‌کند. وب‌هوکی که هر تماس پایان‌یافته را از سیستم VOIP شما دریافت کند برنامه‌ریزی‌شده است. بارگذاری‌ها پیش از پذیرش از نظر اندازه، امضای فایل و مدت واقعی صدا بررسی می‌شوند. | امروز فایل را از پنل بارگذاری می‌کنید. دریافت خودکار هر تماس پایان‌یافته از مرکز تلفن برنامه‌ریزی‌شده است. هر فایل پیش از پذیرش از نظر اندازه، امضای فایل و مدت واقعی صدا بررسی می‌شود. | Today you upload a file from the console. Automatic intake of each finished call from your phone system is planned. Every file is checked for size, file signature and real audio duration before it is accepted. | PROD-09 | The reader is the subject. «وب‌هوک» and «سیستم VOIP» were internals. |
| PR-010 | 1. Upload | `product.sections.ingest.bullets[0]` | قالب‌های پذیرفته‌شده: WAV، MP3، OGG، M4A، AAC، FLAC، MP4 و WEBM. | KEEP | KEEP | PROD-10 | A plain list of formats an IT reader scans. |
| PR-011 | 1. Upload | `product.sections.ingest.bullets[1]` | برنامه‌ریزی‌شده: گیرنده‌های وب‌هوک برای Asterisk و FreePBX با نرمال‌سازی خودکار فیلدها. | برنامه‌ریزی‌شده: تماس‌ها مستقیم از Asterisk و FreePBX می‌رسند و اطلاعات هر تماس خودکار یکدست می‌شود. | Planned: calls arrive straight from Asterisk and FreePBX, with each call's details normalized for you. | PROD-11 | A result instead of «گیرنده‌های وب‌هوک با نرمال‌سازی فیلدها». Planned label kept. Longer: en 87 to 102. |
| PR-012 | 1. Upload | `product.sections.ingest.bullets[2]` | برنامه‌ریزی‌شده: اپراتورها در اولین تماس از روی شمارهٔ داخلی ساخته می‌شوند. | برنامه‌ریزی‌شده: با اولین تماس هر شمارهٔ داخلی، کارشناس آن در همایار تعریف می‌شود. | Planned: each extension's agent is set up in Homayar on its first call. | PROD-12 | Glossary «کارشناس». A person is «تعریف» in Persian, not «ساخته». |
| PR-013 | 1. Upload | `product.sections.ingest.bullets[3]` (not rendered) | برنامه‌ریزی‌شده: هر فراخوانی وب‌هوک با یک کلید مشترک تأیید می‌شود. | برنامه‌ریزی‌شده: هر تماسی که از مرکز تلفن می‌رسد با کلید مشترک تأیید می‌شود. | Planned: every call sent by your phone system is verified with a shared secret. | PROD-13 | Not rendered (only three bullets show). «فراخوانی وب‌هوک» was internals. Longer: fa 66 to 76, en 61 to 79. |
| PR-014 | 2. Transcript | `product.sections.transcription.id` | transcription | KEEP | KEEP | none | Anchor id and React key, not visible. |
| PR-015 | 2. Transcript | `product.sections.transcription.label` | تبدیل گفتار به متن · صفحهٔ شبیه‌سازی‌شده | KEEP | KEEP | PROD-14 | Simulation label with the glossary stage name. |
| PR-016 | 2. Transcript | `product.sections.transcription.title` | گفتار به متن، به فارسی و انگلیسی | متن کامل هر تماس، به فارسی و انگلیسی | A full transcript of each call, in Persian or English | PROD-15 | The result («متن کامل») instead of the stage name. Longer: en 37 to 53. |
| PR-017 | 2. Transcript | `product.sections.transcription.text` | تبدیل گفتار به متن با Whisper انجام می‌شود. می‌توانید زبان گفت‌وگو را برای هر فایل مشخص کنید یا اجازه دهید تشخیص داده شود. تفکیک گوینده‌ها مرحله‌ای جداگانه است. هنوز در دسترس نیست و گزارش همین را می‌گوید، نه اینکه حدس بزند. | زبان مکالمه را برای هر فایل تعیین کنید یا بگذارید خودکار تشخیص داده شود. تفکیک گوینده‌ها هنوز در دسترس نیست و گزارش همین را صریح می‌گوید، نه اینکه حدس بزند. | Set the spoken language for each recording, or let it be detected. Speaker separation is not available yet, and the report says so plainly instead of guessing. | PROD-16, OD-10 | Buyer actions first. The model name (Whisper) is internals and is dropped here. |
| PR-018 | 2. Transcript | `product.sections.transcription.bullets[0]` | فارسی، انگلیسی یا تشخیص خودکار زبان. | KEEP | KEEP | PROD-17 | Short and clear. |
| PR-019 | 2. Transcript | `product.sections.transcription.bullets[1]` | مرحله‌های جداگانه برای آماده‌سازی فایل، تبدیل گفتار به متن، تفکیک گوینده‌ها و تحلیل. | هر مرحله جدا اجرا می‌شود: آماده‌سازی فایل، تبدیل گفتار به متن، تفکیک گوینده‌ها و تحلیل. | Each stage runs on its own: preparing the file, speech to text, speaker separation and analysis. | PROD-18 | A sentence with a verb instead of a noun list. |
| PR-020 | 2. Transcript | `product.sections.transcription.bullets[2]` | هر مرحله وضعیت خود را نشان می‌دهد: شروع نشده، در صف، در حال اجرا، انجام‌شده، ناقص یا ناموفق. | KEEP | KEEP | PROD-19 | Already a sentence; the state names match the console. |
| PR-021 | 2. Transcript | `product.sections.transcription.bullets[3]` (not rendered) | خروجی سرویس آزمایشی برچسب می‌خورد و هرگز به‌عنوان تحلیل واقعی نشان داده نمی‌شود. | KEEP | KEEP | PROD-20 | Not rendered. Natural and exact. |
| PR-022 | 3. Planned report | `product.sections.agents.id` | agents | KEEP | KEEP | none | Anchor id and React key, not visible. |
| PR-023 | 3. Planned report | `product.sections.agents.label` | ۴ عامل · برنامه‌ریزی‌شده | ۴ بخش · برنامه‌ریزی‌شده | 4 parts · planned | PROD-21 | «عامل» is internals. Planned label kept. |
| PR-024 | 3. Planned report | `product.sections.agents.title` | برنامه‌ریزی‌شده: چهار عامل، یک گزارش | برنامه‌ریزی‌شده: یک تماس، چهار پاسخ | Planned: one call, four answers | PROD-22 | Same idea as the approved Home «از هر تماس، چهار پاسخ». Planned label kept. |
| PR-025 | 3. Planned report | `product.sections.agents.text` | برنامه‌ریزی‌شده. هر عامل متن را می‌خواند و یک بخش از گزارش را می‌نویسد. امروز مرحلهٔ تحلیل خلاصه، اقدام‌ها و بینش‌ها را همراه با شواهد می‌نویسد. | برنامه‌ریزی‌شده: گزارش هر تماس چهار بخش تازه می‌گیرد، از امتیاز کیفیت تا آموزش کارشناس. امروز هر تماس خلاصه، اقدام‌ها و بینش‌ها را همراه با شواهد دارد. | Planned: each call report gets four new parts, from the quality score to coaching. Today every call already comes with a summary, action items and insights, with evidence. | PROD-23 | No «عامل». Says what exists today and what is planned. |
| PR-026 | 3. Planned report | `product.sections.agents.items[0].title` | عامل کنترل کیفیت | امتیاز کیفیت | Quality score | PROD-24 | Shown in the sample screen. Glossary «امتیاز کیفیت»; «عامل» was internals. Longer: en 8 to 13. |
| PR-027 | 3. Planned report | `product.sections.agents.items[0].text` (not rendered) | برنامه‌ریزی‌شده: همدلی، حل مسئله، شفافیت، لحن، پایان تماس و مدیریت سکوت را امتیاز می‌دهد. | برنامه‌ریزی‌شده: هر تماس در همدلی، حل مسئله، شفافیت، لحن، پایان تماس و مدیریت سکوت امتیاز می‌گیرد. | Planned: each call is scored on empathy, problem solving, clarity, tone, call closing and silence handling. | PROD-24 | Not rendered. The call is the subject. Longer: en 91 to 107. |
| PR-028 | 3. Planned report | `product.sections.agents.items[1].title` | تشخیص مشکل | ریشهٔ مشکل | Root causes | PROD-25 | Glossary «ریشهٔ مشکل», the same as Home. |
| PR-029 | 3. Planned report | `product.sections.agents.items[1].text` (not rendered) | برنامه‌ریزی‌شده: علت‌های ریشه‌ای، خطاهای سیستمی، شدت مشکل و مشکلات تکراری را شناسایی می‌کند. | برنامه‌ریزی‌شده: می‌بینید علت اصلی هر شکایت چیست، چقدر جدی است، کجا سیستم خطا داشته و کدام مشکل تکرار می‌شود. | Planned: see the root cause of each complaint, how serious it is, where a system failed and which problems repeat. | PROD-25 | Not rendered. The buyer is the subject, with a verb. Longer: fa 92 to 109, en 87 to 114. |
| PR-030 | 3. Planned report | `product.sections.agents.items[2].title` | بینش کسب‌وکار | KEEP | Business insight | PROD-26 | Glossary «بینش کسب‌وکار» (planned). English sentence case. |
| PR-031 | 3. Planned report | `product.sections.agents.items[2].text` (not rendered) | برنامه‌ریزی‌شده: نشانه‌های فروش بیشتر، نشانه‌های ریزش مشتری، فرصت‌های درآمدی و روند رضایت را تشخیص می‌دهد. | برنامه‌ریزی‌شده: می‌بینید کدام مشتری آمادهٔ خرید بیشتر است، کدام ممکن است برود و رضایت در طول زمان چطور تغییر می‌کند. | Planned: see which customers are ready to buy more, which may leave, and how satisfaction changes over time. | PROD-26, OD-03 | Not rendered. Plain words; «فرصت‌های درآمدی» dropped, as on Home (OD-03). |
| PR-032 | 3. Planned report | `product.sections.agents.items[3].title` | مربی کیفیت | آموزش کارشناس | Coaching | PROD-27 | Glossary «آموزش کارشناس». «مربی کیفیت» was a second name. Longer: fa 10 to 13. |
| PR-033 | 3. Planned report | `product.sections.agents.items[3].text` (not rendered) | برنامه‌ریزی‌شده: از نقاط ضعف اپراتور بستهٔ آموزشی با توصیه‌های رفتاری می‌سازد. | برنامه‌ریزی‌شده: هر کارشناس نکته‌های آموزشی روشن می‌گیرد، بر اساس ضعف‌های خودش. | Planned: each agent gets clear coaching tips, based on their own weak points. | PROD-27 | Not rendered. Same line as the approved Home card (H-094). |
| PR-034 | 4. Overview | `product.sections.dashboard.id` | dashboard | KEEP | KEEP | none | Anchor id and React key, not visible. |
| PR-035 | 4. Overview | `product.sections.dashboard.label` | داشبورد · صفحهٔ شبیه‌سازی‌شده | نمای کلی · صفحهٔ شبیه‌سازی‌شده | Overview · simulated screen | PROD-28 | Glossary: the real screen is «نمای کلی», not «داشبورد». Simulation label kept. |
| PR-036 | 4. Overview | `product.sections.dashboard.title` | تماس‌های اخیر شما روی یک صفحه | KEEP | KEEP | PROD-29 | Already the buyer's benefit, inside the register («recent calls»). |
| PR-037 | 4. Overview | `product.sections.dashboard.text` | نمای کلی تماس‌های اخیر، تماس‌های دارای نتیجه، تماس‌های در حال پردازش و مقدار صدای مصرف‌شده را نشان می‌دهد. نمودار روند کیفیت، توزیع احساس، نمای حوادث و مقایسهٔ اپراتورها برنامه‌ریزی‌شده‌اند. | در نمای کلی می‌بینید کدام تماس‌ها آماده‌اند، کدام هنوز در حال پردازش‌اند و تا امروز چند دقیقه صدا تحلیل شده است. نمودار روند کیفیت، توزیع احساس، نمای حوادث و مقایسهٔ کارشناس‌ها برنامه‌ریزی‌شده‌اند. | The overview shows which calls are ready, which are still processing, and how many minutes of audio have been analyzed so far. QC trend charts, sentiment distribution, an incidents view and agent comparison are planned. | PROD-30, HOME-23 | The same facts as the approved Home card, said as answers. Glossary «کارشناس». Longer: en 190 to 219. |
| PR-038 | 4. Overview | `product.sections.dashboard.bullets[0]` | کارت‌هایی برای تماس‌های اخیر، تماس‌های دارای نتیجه، تماس‌های در حال پردازش و صدای مصرف‌شده. | برای تماس‌های اخیر، تماس‌های دارای نتیجه، تماس‌های در حال پردازش و صدای مصرف‌شده، هر کدام یک کارت دارید. | You get a card each for recent calls, calls with results, calls in processing and audio used. | PROD-31 | A sentence with the buyer as the subject. Longer: en 79 to 93. |
| PR-039 | 4. Overview | `product.sections.dashboard.bullets[1]` | برنامه‌ریزی‌شده: نمودار روند کیفیت و توزیع احساس. | برنامه‌ریزی‌شده: روند کیفیت و احساس مشتری را در نمودار می‌بینید. | Planned: see quality trends and customer sentiment in charts. | PROD-32 | A verb instead of a noun pair. Glossary «احساس مشتری». Longer: fa 49 to 64, en 52 to 61. |
| PR-040 | 4. Overview | `product.sections.dashboard.bullets[2]` | برنامه‌ریزی‌شده: حوادث فیلترشده بر اساس شدت. | برنامه‌ریزی‌شده: حوادث را بر اساس شدت فیلتر می‌کنید. | Planned: filter incidents by severity. | PROD-33 | A verb instead of «حوادث فیلترشده». Glossary «حوادث»، «شدت». Longer: fa 44 to 52. |
| PR-041 | 4. Overview | `product.sections.dashboard.bullets[3]` (not rendered) | برنامه‌ریزی‌شده: نمودار مقایسهٔ اپراتورها. | برنامه‌ریزی‌شده: کارشناس‌ها را در نمودار با هم مقایسه می‌کنید. | Planned: compare agents in a chart. | PROD-34 | Not rendered. Glossary «کارشناس». Longer: fa 42 to 62. |
| PR-042 | 5. Call report | `product.sections.reports.id` | reports | KEEP | KEEP | none | Anchor id and React key, not visible. |
| PR-043 | 5. Call report | `product.sections.reports.label` | گزارش تماس · صفحهٔ شبیه‌سازی‌شده | KEEP | KEEP | PROD-35 | Simulation label. |
| PR-044 | 5. Call report | `product.sections.reports.title` | گزارشی که می‌توانید از آن دفاع کنید | KEEP | KEEP | PROD-36 | Keeps: a strong benefit for a QA lead, natural Persian. |
| PR-045 | 5. Call report | `product.sections.reports.text` | نتیجهٔ هر تماس خلاصه، اقدام‌ها و بینش‌ها را نشان می‌دهد. هر مورد به خطوطی از متن وصل است که از آن آمده و متن مکالمه در زبانه‌ای کنار آن است. نتایج را می‌توان به‌صورت TXT، JSON، SRT یا VTT گرفت. | برای هر تماس خلاصه، اقدام‌ها و بینش‌ها را می‌بینید و هر نکته به جمله‌ای از مکالمه وصل است که از آن آمده؛ متن کامل هم کنارش است. نتیجه را به‌صورت TXT، JSON، SRT یا VTT بگیرید. | For each call you see a summary, action items and insights, each linked to the line it came from, with the full transcript one tab away. Export the result as TXT, JSON, SRT or VTT. | PROD-37 | The buyer is the subject. «می‌توان ... گرفت» was impersonal. «کنارش است» is the approved Home wording (H-150) for «one tab away». |
| PR-046 | 5. Call report | `product.sections.reports.bullets[0]` | برنامه‌ریزی‌شده: نوار امتیاز برای امتیاز کلی و هر معیار. | برنامه‌ریزی‌شده: امتیاز کلی و امتیاز هر معیار را روی نوار می‌بینید. | Planned: see the overall score and each criterion on a bar. | PROD-38 | A verb instead of a noun phrase. Longer: fa 56 to 67. |
| PR-047 | 5. Call report | `product.sections.reports.bullets[1]` | برنامه‌ریزی‌شده: جدول علت‌های ریشه‌ای همراه با شدت. | برنامه‌ریزی‌شده: ریشهٔ هر مشکل را با شدتش در یک جدول می‌بینید. | Planned: see each root cause with its severity in a table. | PROD-39 | Glossary «ریشهٔ مشکل»، «شدت». A verb. Longer: fa 51 to 62, en 41 to 58. |
| PR-048 | 5. Call report | `product.sections.reports.bullets[2]` | اقدام‌های همان تماس. بستهٔ آموزشی برنامه‌ریزی‌شده است. | اقدام‌های همان تماس را می‌بینید. آموزش کارشناس برنامه‌ریزی‌شده است. | See the action items from the call. Coaching is planned. | PROD-40 | Glossary «آموزش کارشناس» instead of «بستهٔ آموزشی». Longer: fa 54 to 67. |
| PR-049 | 5. Call report | `product.sections.reports.bullets[3]` (not rendered) | نمای متن مکالمه و خروجی TXT، JSON، SRT یا VTT. PDF برنامه‌ریزی‌شده است. | متن مکالمه را بخوانید و آن را به‌صورت TXT، JSON، SRT یا VTT بگیرید. خروجی PDF برنامه‌ریزی‌شده است. | Read the transcript and export it as TXT, JSON, SRT or VTT. PDF export is planned. | PROD-41 | Not rendered. Buyer actions instead of «نمای متن مکالمه». Longer: fa 71 to 98, en 65 to 82. |
| PR-050 | 6. Copilot (planned) | `product.sections.copilot.id` | copilot | KEEP | KEEP | none | Anchor id and React key, not visible. |
| PR-051 | 6. Copilot (planned) | `product.sections.copilot.label` | دستیار هوشمند · برنامه‌ریزی‌شده | KEEP | KEEP | PROD-42 | Planned label. |
| PR-052 | 6. Copilot (planned) | `product.sections.copilot.title` | برنامه‌ریزی‌شده: از داده‌های تماس‌هایتان سؤال بپرسید | KEEP | KEEP | PROD-43 | Already a buyer action with the planned label; the same as Home. |
| PR-053 | 6. Copilot (planned) | `product.sections.copilot.text` | برنامه‌ریزی‌شده. دستیار هوشمند یک رابط گفت‌وگو روی داده‌های تماس شما خواهد بود که از داده‌های ذخیره‌شده پاسخ می‌دهد، همراه با مرورگر گراف اپراتورها، تماس‌ها و مسائل. امروز می‌توانید در متن تماس‌ها جست‌وجو کنید و به لحظه‌ای بروید که یک عبارت گفته شده است. | برنامه‌ریزی‌شده: دستیار هوشمند به سؤال‌هایتان دربارهٔ تماس‌ها از روی داده‌های ذخیره‌شده جواب می‌دهد و رابطهٔ کارشناس‌ها، تماس‌ها و مشکل‌ها را روی یک گراف نشان می‌دهد. همین امروز می‌توانید در متن تماس‌ها جست‌وجو کنید و به لحظه‌ای بروید که یک عبارت گفته شده است. | Planned: the copilot answers your questions about calls from stored data and shows how agents, calls and problems connect on a graph. Today you can already search transcripts and jump to the moment a phrase was said. | PROD-44, QA-07 | «رابط گفت‌وگو» and «مرورگر گراف» were internals. Says what works today. |
| PR-054 | 6. Copilot (planned) | `product.sections.copilot.bullets[0]` | برنامه‌ریزی‌شده: پرسش به زبان طبیعی، فارسی یا انگلیسی. | برنامه‌ریزی‌شده: به زبان ساده بپرسید، به فارسی یا انگلیسی. | Planned: ask in plain words, in Persian or English. | PROD-45 | An action instead of «پرسش به زبان طبیعی». |
| PR-055 | 6. Copilot (planned) | `product.sections.copilot.bullets[1]` | برنامه‌ریزی‌شده: پاسخ‌ها از داده‌های ذخیره‌شدهٔ تماس‌ها ساخته می‌شوند، نه از حدس آزاد. | برنامه‌ریزی‌شده: جواب از داده‌های تماس‌های خودتان می‌آید، نه از حدس. | Planned: answers come from your own call data, not from guesses. | PROD-46 | Shorter and active. |
| PR-056 | 6. Copilot (planned) | `product.sections.copilot.bullets[2]` | برنامه‌ریزی‌شده: مرورگر گراف تعاملی روی اپراتورها، تماس‌ها و مسائل. | برنامه‌ریزی‌شده: رابطهٔ کارشناس‌ها، تماس‌ها و مشکل‌ها روی یک گراف نشان داده می‌شود. | Planned: see how agents, calls and problems connect on a graph. | PROD-47 | Glossary «کارشناس»، «مشکل». «مرورگر گراف تعاملی» was internals. Longer: fa 67 to 83. |
| PR-057 | 7. Access | `product.sections.admin.id` | admin | KEEP | KEEP | none | Anchor id and React key, not visible. |
| PR-058 | 7. Access | `product.sections.admin.label` | مدیریت · صفحهٔ شبیه‌سازی‌شده | KEEP | KEEP | PROD-48 | Simulation label. |
| PR-059 | 7. Access | `product.sections.admin.title` | کنترل دسترسی برای کل تیم شما | دسترسی هر نفر، به اندازهٔ نقشش | Access that fits each person's role | PROD-49, HOME-27 | The same idea as the approved Home card (H-156). |
| PR-060 | 7. Access | `product.sections.admin.bullets[0]` | دسترسی مبتنی بر نقش: مالک، مدیر، عضو و بیننده، به‌علاوهٔ سرپرست و اپراتور در تیم‌ها. | چهار نقش در فضای کاری: مالک، مدیر، عضو یا بیننده؛ و در هر تیم، سرپرست و کارشناس. | Four workspace roles: owner, admin, member or viewer; and in each team, supervisors and agents. | PROD-50 | Glossary role list and «کارشناس». «مبتنی بر نقش» was stiff. |
| PR-061 | 7. Access | `product.sections.admin.bullets[1]` | اعضا با دعوت‌نامه به فضای کاری می‌پیوندند؛ نقش‌ها را می‌توان تغییر داد و اعضا را حذف کرد. | هم‌تیمی‌ها را با دعوت‌نامه اضافه کنید و هر وقت لازم شد نقششان را عوض کنید یا حذفشان کنید. | Invite colleagues, then change their role or remove them whenever you need. | PROD-51 | The buyer is the subject instead of the passive «می‌توان ... تغییر داد». |
| PR-062 | 7. Access | `product.sections.admin.bullets[2]` | ورود با ایمیل و رمز عبور، یا با Google. | ورود با ایمیل و رمز عبور، یا با حساب Google. | Log in with e-mail and password, or with your Google account. | OD-07, IT-07 | Same fact. PROD-52 is `REMOVE` for LDAP; its new text is this line (OD-07). Longer: en 49 to 61. |
| PR-063 | 7. Access | `product.sections.admin.bullets[3]` (not rendered) | رابط فارسی و انگلیسی با چینش راست‌به‌چپ خودکار. | پنل به فارسی و انگلیسی کار می‌کند، با چینش راست‌به‌چپ خودکار برای فارسی. | The console works in Persian and English, with automatic right-to-left layout for Persian. | PROD-53 | Not rendered. Glossary «پنل»; a sentence with a verb. Longer: fa 47 to 72, en 56 to 90. |
| PR-064 | Live demo | `product.console.demoEyebrow` | شبیه‌سازی | KEEP | KEEP | PROD-04 | Simulation label for the demo below. |
| PR-065 | Live demo | `product.console.demoTitle` | تحلیل شبیه‌سازی‌شدهٔ یک تماس نمونه را ببینید | یک تماس نمونه، از فایل صوتی تا گزارش | A sample call, from recording to report | PROD-05 | «ببینید» was a stage direction. The eyebrow above carries «شبیه‌سازی» (label rule exception). |
| PR-066 | Live demo | `demo.label` (shared) | شبیه‌سازی | KEEP | KEEP | MOCK-01 | Simulation label. |
| PR-067 | Live demo | `components/demo/LiveDemo.tsx:389` | app.homayar.local/calls/104 | KEEP | KEEP | none | Made-up address bar of the sample window. |
| PR-068 | Live demo | `demo.play` (shared) | پخش دمو | KEEP | KEEP | none | Button label. |
| PR-069 | Live demo | `demo.pause` (shared) | توقف دمو | KEEP | KEEP | none | Button label. |
| PR-070 | Live demo | `demo.replay` (shared) | پخش دوباره | KEEP | KEEP | none | Button name for screen readers. |
| PR-071 | Live demo | `demo.recording` (shared) | call-104-inbound.wav | KEEP | KEEP | MOCK-10 | Sample file name. |
| PR-072 | Live demo | `demo.call` (shared) | داخلی ۱۰۴ · ورودی · ۰۶:۱۲ | KEEP | KEEP | MOCK-10 | Sample call line. |
| PR-073 | Live demo | `demo.states.waiting` (shared) | در انتظار | KEEP | KEEP | MOCK-21 | Status word. |
| PR-074 | Live demo | `demo.states.running` (shared) | در حال اجرا | KEEP | KEEP | MOCK-21 | Status word. |
| PR-075 | Live demo | `demo.states.done` (shared) | انجام شد | KEEP | KEEP | MOCK-21 | Status word. |
| PR-076 | Live demo | `demo.transcriptLang` (shared) | زبان متن مکالمه | KEEP | KEEP | none | Accessible name of the language switch. |
| PR-077 | Live demo | `components/demo/LiveDemo.tsx:281` | فارسی / English | KEEP | KEEP | none | Language names «فارسی» / English on the switch, each in its own language. |
| PR-078 | Live demo | `demo.transcript.fa[0].who` (shared) | customer | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-079 | Live demo | `demo.transcript.fa[1].who` (shared) | agent | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-080 | Live demo | `demo.transcript.fa[2].who` (shared) | customer | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-081 | Live demo | `demo.transcript.fa[3].who` (shared) | agent | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-082 | Live demo | `demo.transcript.fa[4].who` (shared) | customer | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-083 | Live demo | `demo.transcript.en[0].who` (shared) | customer | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-084 | Live demo | `demo.transcript.en[1].who` (shared) | agent | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-085 | Live demo | `demo.transcript.en[2].who` (shared) | customer | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-086 | Live demo | `demo.transcript.en[3].who` (shared) | agent | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-087 | Live demo | `demo.transcript.en[4].who` (shared) | customer | KEEP | KEEP | none | Speaker id, not visible text. |
| PR-088 | Live demo | `demo.agents.qc.name` (shared) | کنترل کیفیت | امتیازها | Scores | MOCK-24 | The panel shows scores, so «کنترل کیفیت» (the function) was the wrong name. Not «امتیاز کیفیت»: the chip next to it already says «امتیاز کیفیت · برنامه‌ریزی‌شده», and the same words twice in one view are against the guide. |
| PR-089 | Live demo | `demo.agents.qc.note` (shared) (not rendered) | شش معیار و یک امتیاز کلی. | KEEP | KEEP | MOCK-08 | Not rendered. Short and clear. |
| PR-090 | Live demo | `demo.agents.problems.name` (shared) | تشخیص مشکل | مشکل‌های این تماس | Problems in this call | MOCK-24 | The chip next to it already says «ریشهٔ مشکل · برنامه‌ریزی‌شده»; the heading now says what the list holds, so the two do not repeat. Longer: fa 10 to 17, en 17 to 21. |
| PR-091 | Live demo | `demo.agents.problems.severity` (shared) (not rendered) | شدت | KEEP | KEEP | MOCK-24 | Not rendered. Glossary «شدت». |
| PR-092 | Live demo | `demo.agents.problems.items[0].label` (shared) | وضعیت ارسال به‌روز نشده | KEEP | KEEP | MOCK-10 | Sample problem, the same as the Home sample. |
| PR-093 | Live demo | `demo.agents.problems.items[0].severity` (shared) | زیاد | KEEP | KEEP | MOCK-10 | Severity chip. |
| PR-094 | Live demo | `demo.agents.problems.items[1].label` (shared) | اپراتور تاریخچهٔ سفارش را نمی‌دید | کارشناس تاریخچهٔ سفارش را نمی‌دید | KEEP | MOCK-10 | Glossary «کارشناس», the same as the approved Home sample. |
| PR-095 | Live demo | `demo.agents.problems.items[1].severity` (shared) | متوسط | KEEP | KEEP | MOCK-10 | Severity chip. |
| PR-096 | Live demo | `demo.agents.insights.name` (shared) | بینش کسب‌وکار | KEEP | Business insight | MOCK-24 | Glossary «بینش کسب‌وکار» (planned). English sentence case. |
| PR-097 | Live demo | `demo.ready` (shared) | گزارش آماده است | KEEP | KEEP | MOCK-24 | Status label. |
| PR-098 | Live demo | `demo.sampleNote` (shared) | شبیه‌سازی قابلیت‌های برنامه‌ریزی‌شده با یک تماس ساختگی. نام‌ها و عددها فرضی‌اند. هیچ‌چیز اینجا دادهٔ واقعی مشتری نیست. | شبیه‌سازی بخش‌های برنامه‌ریزی‌شده با یک تماس ساختگی. نام‌ها و عددها فرضی‌اند و هیچ دادهٔ واقعی مشتری اینجا نیست. | A simulation of planned features with a made-up call. Names and numbers are invented, and no real customer data is shown. | MOCK-03 | Keeps both labels. «هیچ‌چیز اینجا ... نیست» was a calque. |
| PR-099 | Live demo | `demo.reducedNote` (shared) (not rendered) | حرکت در این دستگاه کم شده است؛ بنابراین گزارش نهایی نشان داده می‌شود. | حرکت در این دستگاه کم است، پس گزارش نهایی را یک‌جا می‌بینید. | Motion is reduced on this device, so you see the finished report at once. | none | Not rendered. The reader is the subject. |
| PR-100 | Live demo | `demo.exportPdf` (shared) (not rendered) | خروجی | KEEP | KEEP | MOCK-09 | Not rendered. Already without PDF. |
| PR-101 | Not rendered | `product.console.eyebrow` (not rendered) | ساخت کنسول | پنل همایار | The console | none | Not rendered. Glossary «پنل» (owner, Home Q-01). |
| PR-102 | Not rendered | `product.console.title` (not rendered) | کنسول با اسکرول شما ساخته می‌شود | هر بخش پنل، یک کار تیم شما | Each part of the console does one job for your team | none | Not rendered. Old line was a scroll instruction. Longer: en 39 to 51. |
| PR-103 | Not rendered | `product.console.text` (not rendered) | هفت پنل به ترتیبی که یک فایل صوتی در محصول جلو می‌رود، در یک پنجره جا می‌گیرند. | هفت بخش، به همان ترتیبی که یک تماس در همایار جلو می‌رود. | Seven parts, in the order a call moves through Homayar. | PROD-03 | Not rendered. «در پنجره جا می‌گیرند» narrated the scene. |
| PR-104 | Not rendered | `product.console.hint` (not rendered) | اسکرول کنید: هر پنل در پنجره جا می‌گیرد | هفت بخش، به ترتیب کار | Seven parts, in order | none | Not rendered. Old line was a scroll instruction. |
| PR-105 | Not rendered | `product.console.waiting` (not rendered) | در انتظار | KEEP | KEEP | none | Not rendered. Status word. |
| PR-106 | Not rendered | `product.console.docked` (not rendered) | جا گرفت | آماده | Ready | none | Not rendered. «جا گرفت» narrated the animation. |
| PR-107 | Not rendered | `product.console.complete` (not rendered) | کنسول کامل شد | پنل آماده است | Console ready | none | Not rendered. Glossary «پنل». |
| PR-108 | Not rendered | `product.console.demoText` (not rendered) | موج صدا، رونوشت، چهار عامل برنامه‌ریزی‌شده و گزارش، با داده‌ی نمونه. | شبیه‌سازی با داده‌ی نمونه: متن مکالمه، بخش‌های برنامه‌ریزی‌شدهٔ گزارش و نتیجهٔ نهایی. | A simulation with sample data: the transcript, the planned parts of the report and the finished result. | PROD-06 | Not rendered. «رونوشت» and «عامل» replaced; labels kept. Longer: fa 68 to 85, en 75 to 103. |

## Length rule

Tight slots (eyebrows, labels, chips, hints, buttons, links and scene text) stay within 15 percent of the old line in each language. No exception.

Lines outside tight slots that grew by more than 15 percent (each has a note in its Reason):

| Row | Key | Characters |
| --- | --- | --- |
| PR-001 | `product.meta.title` | fa 26 to 41, en 38 to 62 |
| PR-002 | `product.meta.description` | fa 209 to 243, en 207 to 247 |
| PR-008 | `product.sections.ingest.title` | en 43 to 52 |
| PR-011 | `product.sections.ingest.bullets[1]` | en 87 to 102 |
| PR-013 | `product.sections.ingest.bullets[3]` | fa 66 to 76, en 61 to 79 |
| PR-016 | `product.sections.transcription.title` | en 37 to 53 |
| PR-026 | `product.sections.agents.items[0].title` | en 8 to 13 |
| PR-027 | `product.sections.agents.items[0].text` | en 91 to 107 |
| PR-029 | `product.sections.agents.items[1].text` | fa 92 to 109, en 87 to 114 |
| PR-032 | `product.sections.agents.items[3].title` | fa 10 to 13 |
| PR-037 | `product.sections.dashboard.text` | en 190 to 219 |
| PR-038 | `product.sections.dashboard.bullets[0]` | en 79 to 93 |
| PR-039 | `product.sections.dashboard.bullets[1]` | fa 49 to 64, en 52 to 61 |
| PR-040 | `product.sections.dashboard.bullets[2]` | fa 44 to 52 |
| PR-041 | `product.sections.dashboard.bullets[3]` | fa 42 to 62 |
| PR-046 | `product.sections.reports.bullets[0]` | fa 56 to 67 |
| PR-047 | `product.sections.reports.bullets[1]` | fa 51 to 62, en 41 to 58 |
| PR-048 | `product.sections.reports.bullets[2]` | fa 54 to 67 |
| PR-049 | `product.sections.reports.bullets[3]` | fa 71 to 98, en 65 to 82 |
| PR-056 | `product.sections.copilot.bullets[2]` | fa 67 to 83 |
| PR-062 | `product.sections.admin.bullets[2]` | en 49 to 61 |
| PR-063 | `product.sections.admin.bullets[3]` | fa 47 to 72, en 56 to 90 |
| PR-090 | `demo.agents.problems.name` | fa 10 to 17, en 17 to 21 |
| PR-102 | `product.console.title` | en 39 to 51 |
| PR-108 | `product.console.demoText` | fa 68 to 85, en 75 to 103 |
