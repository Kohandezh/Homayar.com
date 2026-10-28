# Website copy v2: Deployment page

| Field | Value |
| --- | --- |
| Ticket | HOM-327, stage P, group A |
| Base commit | `5f2f75b` (the approved Home page and the style guide) |
| Date | 2026-10-28 |
| Status | Written with `docs/website/copy-style.md`; waiting for review |
| See it | `http://localhost:4960/fa/deployment` and `http://localhost:4960/en/deployment` |
| Rows | `DP-001` to `DP-075`. Persian changed in 39, English in 33 |
| How to answer | Name a row ID and say: keep, change to ..., or remove |

Rules: `docs/website/copy-style.md`. `(shared)` means other pages show the key too. `(not rendered)` means no component reads the key today; it is rewritten so no old wording stays in the dictionary.

## Owner questions

None. Every line uses a claim-register row or no claim.

## Lines in the order the visitor meets them

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DP-001 | Head | `deployment.meta.title` | استقرار: روی سرور شما یا ترکیبی؛ SaaS برنامه‌ریزی‌شده | استقرار: روی سرور خودتان یا ترکیبی؛ نسخهٔ ابری برنامه‌ریزی‌شده | Deployment: self-hosted or hybrid; managed cloud planned | DEP-01, OD-04 | Glossary phrases instead of «SaaS» in Persian. Planned label kept. Longer: fa 53 to 62, en 47 to 56. |
| DP-002 | Head | `deployment.meta.description` | مقایسهٔ روش‌های اجرای همایار: بستهٔ Docker Compose روی سرور شما بدون مسیر خروجی، حالت ترکیبی با لایهٔ دادهٔ محلی و تحلیل ابری اختیاری که به‌طور پیش‌فرض متن پوشانده‌شده می‌گیرد، و SaaS مدیریت‌شدهٔ برنامه‌ریزی‌شده. | همایار را روی سرور خودتان اجرا کنید، بدون هیچ راهی به اینترنت؛ یا حالت ترکیبی را انتخاب کنید که داده را محلی نگه می‌دارد و تحلیل ابری را فقط اگر بخواهید، به‌طور پیش‌فرض با متن پوشانده‌شده، اضافه می‌کند. نسخهٔ ابری برنامه‌ریزی‌شده است. | Run Homayar on your own servers with no route to the internet, or choose hybrid: your data stays local, and cloud analysis is added only if you want it, with masked text by default. Managed cloud is planned. | DEP-02, OD-16 | The buyer's choice first. «لایهٔ دادهٔ محلی» was internals. |
| DP-003 | Hero | `deployment.hero.eyebrow` | استقرار | KEEP | KEEP | none | Plain page name the IT buyer looks for. |
| DP-004 | Hero | `deployment.hero.title` | یک محصول، امروز دو جا برای اجرا و جای سوم برنامه‌ریزی‌شده | خودتان تعیین می‌کنید همایار کجا اجرا شود | You decide where Homayar runs | DEP-03 | The buyer is the subject; the planned part moves to the text. |
| DP-005 | Hero | `deployment.hero.text` | عملیات خصوصی یک پروفایل پشتیبانی‌شده از همان محصول است، نه یک انشعاب. SaaS مدیریت‌شده برنامه‌ریزی‌شده است. همان قواعد امنیت و شواهد همه‌جا اعمال می‌شود. | امروز روی سرور خودتان یا به‌صورت ترکیبی؛ نسخهٔ ابری برنامه‌ریزی‌شده است. هر سه یک محصول‌اند، با همان قواعد امنیت و شواهد، نه نسخه‌هایی جدا. | Today on your own servers or as a hybrid; managed cloud is planned. All three are one product, with the same security and evidence rules, not separate versions. | DEP-04, OD-04 | «پروفایل پشتیبانی‌شده» and «انشعاب» were internals. |
| DP-006 | Map | `deployment.zones.eyebrow` | سه جا برای اجرا | KEEP | KEEP | DEP-04 | Keeps: short and plain. |
| DP-007 | Map | `deployment.zones.title` | بلوک را دنبال کنید | یک محصول، سه جا | One product | DEP-04 | «بلوک را دنبال کنید» was a stage direction. English: the old "Follow the block" has 16 characters, so the limit is 18; "One product, three places" (25) does not fit. |
| DP-008 | Map | `deployment.zones.text` | همان محصول میان سه فضا حرکت می‌کند. ببینید دور آن چه تغییر می‌کند. | محصول همان است؛ فقط جای داده و راه‌های خروج آن تغییر می‌کند. | The product is the same; only where data lives and how it can leave changes. | DEP-04 | «حرکت می‌کند ... ببینید» narrated the scene. |
| DP-009 | Map | `deployment.zones.hint` | اسکرول کنید: بلوک سفر می‌کند | نسخهٔ ابری، روی سرور، ترکیبی | Cloud, self-hosted, hybrid | none | Old line was a scroll instruction. Names the three zones. |
| DP-010 | Map | `deployment.zones.block` | همایار | KEEP | KEEP | none | Brand name on the moving block. |
| DP-011 | Map | `deployment.zones.items[0].name` | ابر (SaaS) | نسخهٔ ابری | Managed cloud | OD-04 | Glossary instead of «ابر (SaaS)». The note under it keeps the planned label. |
| DP-012 | Map | `deployment.zones.items[0].note` | برنامه‌ریزی‌شده: منطقهٔ ابری | KEEP | KEEP | DEP-10 | Planned label. |
| DP-013 | Map | `deployment.zones.items[1].name` | سرور شما | KEEP | KEEP | DEP-45 | «سرور شما» stays: the glossary «روی سرور خودتان» is over the length limit for this name (the same exception as the Home hero). |
| DP-014 | Map | `deployment.zones.items[1].note` | دیواری پشت آن بسته می‌شود: راهی به بیرون نیست | هیچ راهی به اینترنت نیست | No route to the internet | DEP-11, HOME-32 | «دیواری پشت آن بسته می‌شود» narrated the scene. |
| DP-015 | Map | `deployment.zones.items[2].name` | ترکیبی | KEEP | KEEP | OD-16 | Glossary «ترکیبی». |
| DP-016 | Map | `deployment.zones.items[2].note` | دو نیم می‌شود: دادهٔ محلی، اجزای ابری اختیاری | داده پیش شما؛ تحلیل ابری فقط اگر بخواهید | Data stays with you; cloud analysis only if you want it | DEP-12, OD-16 | «دو نیم می‌شود» narrated the scene. Glossary «تحلیل ابری». |
| DP-017 | Map | `deployment.zones.wall` | بدون مسیر | KEEP | KEEP | DEP-39 | Short label, the same as the diagram. |
| DP-018 | Map | `deployment.zones.halves[0]` | لایهٔ دادهٔ محلی | دادهٔ محلی | Local data | DEP-12 | «لایهٔ داده» was internals. |
| DP-019 | Map | `deployment.zones.halves[1]` | اجزای ابری اختیاری | ابر اختیاری | Optional cloud | DEP-12, OD-16 | Same words as the Home label «شما، با ابر اختیاری». |
| DP-020 | Options | `deployment.options[0].id` | saas | KEEP | KEEP | none | Anchor id and React key, not visible. |
| DP-021 | Options | `deployment.options[1].id` | self-hosted | KEEP | KEEP | none | Anchor id and React key, not visible. |
| DP-022 | Options | `deployment.options[2].id` | hybrid | KEEP | KEEP | none | Anchor id and React key, not visible. |
| DP-023 | Option: managed cloud | `deployment.options[0].name` | SaaS | نسخهٔ ابری | Managed cloud | OD-04 | Glossary instead of «SaaS»; the summary carries the planned label. Longer: fa 4 to 10, en 4 to 13. |
| DP-024 | Option: managed cloud | `deployment.options[0].summary` | برنامه‌ریزی‌شده: ما آن را برای شما در یک منطقهٔ ابری تأییدشده اجرا می‌کنیم. | برنامه‌ریزی‌شده: همایار را ما در یک منطقهٔ ابری تأییدشده برایتان اجرا می‌کنیم. | KEEP | DEP-13 | Active «ما ... اجرا می‌کنیم», as on Home. |
| DP-025 | Option: managed cloud | `deployment.options[0].points[0]` | PostgreSQL اشتراکی با محدودهٔ اجباری فضای کاری، مجوزدهی در برنامه و امنیت سطح سطر. | داده‌های هر سازمان در PostgreSQL مشترک جدا می‌ماند: هر درخواست به فضای کاری خودش محدود است، برنامه دسترسی را می‌سنجد و امنیت سطح سطر هم برقرار است. | Each organization's data stays apart in a shared PostgreSQL: every request is limited to its own workspace, the application checks access, and row-level security applies. | DEP-14 | The benefit first, then the three checks. Glossary «سازمان» and «فضای کاری». Longer: fa 82 to 147, en 99 to 170. |
| DP-026 | Option: managed cloud | `deployment.options[0].points[1]` | برنامه‌ریزی‌شده: استقرار اختصاصی پایگاه داده، ذخیره‌سازی و کارگرها برای قراردادهایی که به جداسازی عملیاتی قوی‌تر نیاز دارند. | برنامه‌ریزی‌شده: اگر قراردادتان جداسازی بیشتری بخواهد، پایگاه داده، فضای ذخیره‌سازی و سرورهای پردازش جدا برایتان راه می‌اندازیم. | Planned: if your contract needs stronger isolation, we set up a separate database, storage and processing servers for you. | DEP-15 | «کارگرها» was internals. Planned label kept. |
| DP-027 | Option: managed cloud | `deployment.options[0].points[2]` | سیاست هر فضای کاری منطقه‌های پردازش، ارائه‌دهنده‌ها و مدل‌های مجاز و یک مدت نگهداری را تعیین می‌کند. | سیاست هر فضای کاری تعیین می‌کند داده در کدام منطقه و با کدام ارائه‌دهنده و مدل پردازش شود و چه مدت نگهداری شود. | Each workspace policy sets where data is processed, with which providers and models, and how long it is kept. | DEP-16 | A sentence with a verb instead of a noun list. |
| DP-028 | Option: managed cloud | `deployment.options[0].points[3]` | محدودیت نرخ برای هر مسیر و سقف تماس صوتی برای هر فضای کاری. زمان‌بندی منصفانه بین مستأجرها برنامه‌ریزی‌شده است. | برای هر مسیر محدودیت نرخ و برای هر فضای کاری سقف تماس صوتی وجود دارد. نوبت‌دهی منصفانه بین سازمان‌ها برنامه‌ریزی‌شده است. | Rate limits apply per route, and a voice-call cap per workspace. Fair scheduling between organizations is planned. | DEP-17 | Glossary «سازمان» instead of «مستأجر». Planned label kept. |
| DP-029 | Option: managed cloud | `deployment.options[0].fit` | تیم‌هایی که می‌خواهند سریع شروع کنند و منطقهٔ ابری تأییدشده را می‌پذیرند. | KEEP | KEEP | DEP-18 | Keeps: says who it suits, in plain words. |
| DP-030 | Option: self-hosted | `deployment.options[1].name` | نصب روی سرور شما | روی سرور خودتان | KEEP | HOME-32 | Glossary phrase. |
| DP-031 | Option: self-hosted | `deployment.options[1].summary` | یک بستهٔ Docker Compose روی سرورهای خودتان. | بستهٔ Docker Compose روی سرورهای خودتان. | KEEP | DEP-19 | Drops the needless «یک», as on Home. |
| DP-032 | Option: self-hosted | `deployment.options[1].points[0]` | API و کنسول، PostgreSQL، صف سازگار با Redis، ذخیره‌سازی شیء سازگار با S3، کارگرها و nginx به‌عنوان تنها مبدأ منتشرشده. | همه‌چیز در بسته است: API و پنل، PostgreSQL، کارگزار پیام سازگار با Redis، فضای ذخیره‌سازی سازگار با S3، سرویس‌های پردازش، و nginx که تنها نقطهٔ ورود است. | Everything is in the package: API and console, PostgreSQL, a Redis-compatible broker, S3-compatible object storage, processing services, and nginx as the only entry point. | DEP-20 | IT detail, said plainly. Glossary «پنل». «صف» (a job queue) became «کارگزار پیام». Longer: fa 118 to 153, en 136 to 171. |
| DP-033 | Option: self-hosted | `deployment.options[1].points[1]` | تبدیل گفتار به متن محلی، اگر گزینهٔ هوش مصنوعی محلی را نصب کنید، روی CPU اجرا می‌شود. کارگرهای GPU و تفکیک گوینده برنامه‌ریزی‌شده‌اند. Neo4j اختیاری است. | اگر گزینهٔ هوش مصنوعی محلی را نصب کنید، تبدیل گفتار به متن روی CPU خود سرور انجام می‌شود. پردازش با GPU و تفکیک گوینده‌ها برنامه‌ریزی‌شده‌اند. Neo4j اختیاری است. | If you install the local AI option, speech to text runs on the server's own CPU. GPU processing and speaker separation are planned. Neo4j is optional. | DEP-21 | The condition first, in Persian word order. Planned label kept. |
| DP-034 | Option: self-hosted | `deployment.options[1].points[2]` | هیچ کانتینر برنامه‌ای مسیری به اینترنت ندارد. ترافیک خروجی، اگر باشد، از پراکسی با پیش‌فرضِ رد و فهرست مجاز شما عبور می‌کند. | هیچ کانتینر برنامه راهی به اینترنت ندارد. اگر ترافیک خروجی لازم باشد، فقط از پراکسی با فهرست مجاز شما می‌گذرد که پیش‌فرضش رد کردن است. | No application container can reach the internet. If outbound traffic is needed, it passes only through a deny-by-default proxy with your allowlist. | DEP-22 | Same facts as the approved Home card; glossary «پراکسی با فهرست مجاز». |
| DP-035 | Option: self-hosted | `deployment.options[1].points[3]` | ورود با ایمیل و رمز عبور، یا Google اگر شبکهٔ شما اجازه دهد. کلیدها در مخزن اسرار خودتان می‌مانند، به‌صورت فایل یا متغیر. | ورود با ایمیل و رمز عبور، یا با حساب Google اگر شبکه‌تان اجازه دهد. کلیدها در مخزن اسرار خودتان می‌مانند، به‌صورت فایل یا متغیر. | Log in with e-mail and password, or with Google sign-in when your network allows it. Keys stay in your own secrets storage, as files or variables. | DEP-23 | Small polish; the same facts. |
| DP-036 | Option: self-hosted | `deployment.options[1].points[4]` | نقاط پایانی سلامت و آمادگی، کار مهاجرت، مراحل تخلیه و بازگشت در راهنمای عملیاتی مستند شده‌اند. | راهنمای عملیات همه را توضیح می‌دهد: بررسی سلامت و آمادگی، اجرای مهاجرت پایگاه داده، تخلیه و بازگشت به نسخهٔ قبل. | The runbook covers it all: health and readiness checks, the database migration job, drain and rollback. | DEP-24 | A sentence with a verb; «نقاط پایانی» was internals. Longer: fa 94 to 112. |
| DP-037 | Option: self-hosted | `deployment.options[1].fit` | سازمان‌هایی که فایل‌های صوتی‌شان هرگز نباید از شبکه‌شان خارج شود. | KEEP | KEEP | DEP-25 | Keeps: names the buyer exactly. |
| DP-038 | Option: hybrid | `deployment.options[2].name` | ترکیبی | KEEP | KEEP | OD-16 | Glossary «ترکیبی». |
| DP-039 | Option: hybrid | `deployment.options[2].summary` | اول لایهٔ دادهٔ محلی، با بخش‌های ابری اختیاری که خودتان روشن می‌کنید. | داده پیش شما می‌ماند و بخش‌های ابری را فقط اگر بخواهید روشن می‌کنید. | Your data stays with you, and you switch on cloud pieces only if you want them. | DEP-26 | «لایهٔ دادهٔ محلی» was internals; the buyer is the subject. Longer: en 67 to 79. |
| DP-040 | Option: hybrid | `deployment.options[2].points[0]` | صدا، متن‌ها، هویت، جست‌وجو و شواهد محلی می‌مانند. نصب بدون اینترنت به کار ادامه می‌دهد. | صدا، متن مکالمه‌ها، حساب‌های کاربری، جست‌وجو و شواهد محلی می‌مانند و همایار بدون اینترنت هم کار می‌کند. | Audio, transcripts, user accounts, search and evidence stay local, and Homayar keeps working offline. | DEP-27 | «هویت» became «حساب‌های کاربری», the plain word. Longer: fa 87 to 103. |
| DP-041 | Option: hybrid | `deployment.options[2].points[1]` | برنامه‌ریزی‌شده: یک لایهٔ مدیریت اختیاری فقط شمارنده‌های تأییدشدهٔ سلامت، نسخه و مجوز را می‌گیرد و می‌توانید محتوای ارسالی را ببینید. | برنامه‌ریزی‌شده: بخش مدیریت اختیاری فقط شمارنده‌های تأییدشدهٔ سلامت، نسخه و مجوز را می‌گیرد و شما دقیقاً می‌بینید چه چیزی فرستاده می‌شود. | Planned: an optional management service receives only approved health, version and license counters, and you see exactly what is sent. | DEP-28, OD-06 | «لایهٔ مدیریت» was internals. Planned label kept. |
| DP-042 | Option: hybrid | `deployment.options[2].points[2]` | حالت دوم اجازه می‌دهد تبدیل گفتار به متن و پوشاندن محلی انجام شود و سپس استدلال ابری اجرا شود که به‌طور پیش‌فرض متن پوشانده‌شده می‌گیرد؛ با انتخاب صریح هر فضای کاری. | در حالت دوم، تبدیل گفتار به متن و پوشاندن داده‌های شخصی روی سرور خودتان انجام می‌شود و بعد تحلیل ابری، که به‌طور پیش‌فرض متن پوشانده‌شده می‌گیرد، فقط اگر آن فضای کاری انتخاب کند اجرا می‌شود. | In the second mode, speech to text and personal-data masking run on your own server, then cloud analysis runs on masked text by default, only if that workspace opts in. | DEP-29, OD-16 | «اجازه می‌دهد» and «استدلال ابری» were calques. Keeps «به‌طور پیش‌فرض» (OD-16). Longer: fa 165 to 190, en 131 to 168. |
| DP-043 | Option: hybrid | `deployment.options[2].points[3]` | مدل محلیِ ناموفق هرگز بی‌سروصدا به API ابری منتقل نمی‌شود. | اگر مدل محلی از کار بیفتد، کار هرگز بی‌خبر به یک سرویس ابری سپرده نمی‌شود. | If a local model fails, the work is never quietly handed to a cloud service. | DEP-30 | Same idea as the approved Home trust item; plainer than «منتقل نمی‌شود». Longer: fa 58 to 74, en 62 to 76. |
| DP-044 | Option: hybrid | `deployment.options[2].fit` | سازمان‌هایی که دادهٔ خصوصی می‌خواهند با ظرفیت ابری انتخابی. | سازمان‌هایی که داده را پیش خود نگه می‌دارند و فقط هر جا لازم باشد از ابر کمک می‌گیرند. | Organizations that keep their data in-house and use the cloud only where they need it. | DEP-31 | «ظرفیت ابری انتخابی» was a stiff noun stack. Longer: fa 59 to 86, en 66 to 86. |
| DP-045 | Side by side | `deployment.zones.compareHint` | اسکرول کنید: جدول ساخته می‌شود | سه روش اجرا | Three ways to run it | none | Old line was a scroll instruction and scene narration. Does not repeat the table title «کنار هم» below it. |
| DP-046 | Side by side | `deployment.compare.title` | کنار هم | KEEP | KEEP | none | Keeps: two plain words. |
| DP-047 | Side by side | `deployment.compare.columns[0]` |  | KEEP | KEEP | none | Empty corner cell. |
| DP-048 | Side by side | `deployment.compare.columns[1]` | SaaS | نسخهٔ ابری | Managed cloud | OD-04 | Glossary instead of «SaaS»; the cells below keep the planned label. |
| DP-049 | Side by side | `deployment.compare.columns[2]` | روی سرور شما | KEEP | KEEP | HOME-32 | «روی سرور شما» stays: the glossary «روی سرور خودتان» is over the length limit here (the short-form exception). |
| DP-050 | Side by side | `deployment.compare.columns[3]` | ترکیبی | KEEP | KEEP | OD-16 | Glossary «ترکیبی». |
| DP-051 | Side by side | `deployment.compare.rows[0][0]` | فایل‌های صوتی کجا می‌مانند | KEEP | KEEP | none | Table cell. Short, plain and accurate. |
| DP-052 | Side by side | `deployment.compare.rows[0][1]` | برنامه‌ریزی‌شده: منطقهٔ ابری | KEEP | KEEP | DEP-32 | Table cell. Short, plain and accurate. |
| DP-053 | Side by side | `deployment.compare.rows[0][2]` | سرورهای شما | KEEP | KEEP | DEP-48 | Table cell. Short, plain and accurate. |
| DP-054 | Side by side | `deployment.compare.rows[0][3]` | سرورهای شما | KEEP | KEEP | DEP-48 | Table cell. Short, plain and accurate. |
| DP-055 | Side by side | `deployment.compare.rows[1][0]` | چه کسی اجرا می‌کند | KEEP | KEEP | none | Table cell. Short, plain and accurate. |
| DP-056 | Side by side | `deployment.compare.rows[1][1]` | همایار (برنامه‌ریزی‌شده) | KEEP | KEEP | DEP-33 | Table cell. Short, plain and accurate. |
| DP-057 | Side by side | `deployment.compare.rows[1][2]` | تیم فناوری اطلاعات شما، با بسته و راهنمای ما | KEEP | KEEP | DEP-47 | Table cell. Short, plain and accurate. |
| DP-058 | Side by side | `deployment.compare.rows[1][3]` | تیم فناوری اطلاعات شما؛ مدیریت ابری برنامه‌ریزی‌شده | KEEP | KEEP | DEP-34 | Table cell. Short, plain and accurate. |
| DP-059 | Side by side | `deployment.compare.rows[2][0]` | ارائه‌دهنده‌های هوش مصنوعی | KEEP | KEEP | none | Table cell. |
| DP-060 | Side by side | `deployment.compare.rows[2][1]` | سیاست هر مستأجر | سیاست هر سازمان | Per-organization policy | DEP-49 | Glossary «سازمان» instead of «مستأجر». Longer: en 17 to 23. |
| DP-061 | Side by side | `deployment.compare.rows[2][2]` | مدل‌های محلی به‌طور پیش‌فرض؛ نقاط پایانی خصوصی با تأیید شما | KEEP | KEEP | DEP-49 | Table cell. Accurate. |
| DP-062 | Side by side | `deployment.compare.rows[2][3]` | اول محلی؛ ابر به‌طور پیش‌فرض متن پوشانده‌شده می‌گیرد، با انتخاب شما | KEEP | KEEP | DEP-35 | Table cell. Keeps «by default» (OD-16). |
| DP-063 | Side by side | `deployment.compare.rows[3][0]` | هویت | حساب‌های کاربری | User accounts | none | «هویت» became the plain word. Longer: fa 4 to 15, en 8 to 13. |
| DP-064 | Side by side | `deployment.compare.rows[3][1]` | حساب‌های کنسول | حساب‌های پنل | KEEP | DEP-36 | Glossary «پنل». |
| DP-065 | Side by side | `deployment.compare.rows[3][2]` | حساب‌های کنسول | حساب‌های پنل | KEEP | DEP-36 | Glossary «پنل». |
| DP-066 | Side by side | `deployment.compare.rows[3][3]` | حساب‌های کنسول | حساب‌های پنل | KEEP | DEP-37 | Glossary «پنل». |
| DP-067 | Side by side | `deployment.compare.rows[4][0]` | شبکه | KEEP | KEEP | none | Table cell. Short, plain and accurate. |
| DP-068 | Side by side | `deployment.compare.rows[4][1]` | برنامه‌ریزی‌شده: فایروال پلتفرم و پراکسی خروجی | KEEP | KEEP | DEP-38 | Table cell. Short, plain and accurate. |
| DP-069 | Side by side | `deployment.compare.rows[4][2]` | بدون مسیر خروجی؛ پراکسی با فهرست مجاز اختیاری | KEEP | KEEP | DEP-39 | Table cell. Short, plain and accurate. |
| DP-070 | Side by side | `deployment.compare.rows[4][3]` | برنامه‌ریزی‌شده: اتصال خروجی امضاشده و دوره‌ای، بدون پورت ورودی | KEEP | KEEP | DEP-40 | Table cell. Short, plain and accurate. |
| DP-071 | Runtime | `deployment.runtimeNote.title` | بستهٔ نصب محلی در زمان اجرا چه کاری نمی‌کند | آنچه بستهٔ روی سرور هرگز انجام نمی‌دهد | What the self-hosted package never does | DEP-41, DEP-42, DEP-43, DEP-44 | Plainer than «در زمان اجرا چه کاری نمی‌کند». |
| DP-072 | Runtime | `deployment.runtimeNote.items[0]` | مسیری برای دانلود مدل ندارد. | KEEP | KEEP | DEP-41, OD-11 | Short and exact (OD-11 wording). |
| DP-073 | Runtime | `deployment.runtimeNote.items[1]` | بیکن تله‌متری ندارد. | KEEP | KEEP | DEP-42 | Short and exact. |
| DP-074 | Runtime | `deployment.runtimeNote.items[2]` | CDN فونت یا اسکریپت ندارد. | KEEP | KEEP | DEP-43 | Short and exact. |
| DP-075 | Runtime | `deployment.runtimeNote.items[3]` | ارائه‌دهندهٔ اجباری ایمیل یا پیامک ندارد. | KEEP | KEEP | DEP-44 | Short and exact. |

## Length rule

Tight slots (eyebrows, labels, chips, hints, buttons, links and scene text) stay within 15 percent of the old line in each language, except:

| Row | Key | Characters | Note |
| --- | --- | --- | --- |
| DP-048 | `deployment.compare.columns[1]` | fa 4 to 10, en 4 to 13 | Column header of the comparison table; the column is as wide as the others. Check on the running page. |

Lines outside tight slots that grew by more than 15 percent (each has a note in its Reason):

| Row | Key | Characters |
| --- | --- | --- |
| DP-001 | `deployment.meta.title` | fa 53 to 62, en 47 to 56 |
| DP-023 | `deployment.options[0].name` | fa 4 to 10, en 4 to 13 |
| DP-025 | `deployment.options[0].points[0]` | fa 82 to 147, en 99 to 170 |
| DP-032 | `deployment.options[1].points[0]` | fa 118 to 153, en 136 to 171 |
| DP-036 | `deployment.options[1].points[4]` | fa 94 to 112 |
| DP-039 | `deployment.options[2].summary` | en 67 to 79 |
| DP-040 | `deployment.options[2].points[0]` | fa 87 to 103 |
| DP-042 | `deployment.options[2].points[2]` | fa 165 to 190, en 131 to 168 |
| DP-043 | `deployment.options[2].points[3]` | fa 58 to 74, en 62 to 76 |
| DP-044 | `deployment.options[2].fit` | fa 59 to 86, en 66 to 86 |
| DP-060 | `deployment.compare.rows[2][1]` | en 17 to 23 |
| DP-063 | `deployment.compare.rows[3][0]` | fa 4 to 15, en 8 to 13 |
