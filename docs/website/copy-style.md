# Homayar website copy style

| Field | Value |
| --- | --- |
| Ticket | HOM-327 |
| Status | Contract for every page of homayar.com (stage P and later) |
| Source | The Home page, approved by the owner on 2026-10-28 ("excellent, go ahead the same way"). Every row is in `docs/website/copy-v2-home.md` |
| Date | 2026-10-28 |

This guide tells you how to write any line of the public website, in Persian first and English
second. If you follow it, your page will sound like the approved Home page. When this guide and a
SPEC disagree, the SPEC wins and you tell the leader.

## 1. Who we write for

| Reader | What they are measured on | What they want from a line |
| --- | --- | --- |
| Contact-center manager | Service quality, customer complaints, cost of the team | What changes for my team after we buy |
| Quality lead (QA) | How many calls get reviewed, fair scoring, coaching | How I review more calls and can prove each finding |
| Operations lead | Repeat problems, speed of fixing them | Which problems keep coming back |
| IT or security lead (gatekeeper) | Where data lives, who can see it, what leaves the network | Plain facts, no marketing words, the exact limits |

They have little time. They read one line and decide if the next one is worth it. Write for a busy
Iranian manager, not for an engineer and not for a judge.

## 2. The message

Every page tells the same story in its own words:

1. **The problem, in the buyer's words.** «به همهٔ تماس‌ها نمی‌رسید؟»
2. **The outcome.** The buyer gets a transcript, a summary and action items for each call, and reads
   instead of listening.
3. **The proof.** Every point links to the line of the call it came from. The data stays on the
   buyer's own servers.
4. **The honest limit.** What is planned is called planned. What is a simulation is called a
   simulation.

The main proof points, with their claim-register rows:

| Proof | Register row |
| --- | --- |
| Transcript, summary, action items and insights for each uploaded call | HOME-02, HOME-24 |
| Each point linked to the transcript line it came from | HOME-05, HOME-24, PROD-37 |
| Persian and English | HOME-07, OD-10 |
| Self-hosted: no application container can reach the internet | HOME-32 |
| Hybrid: cloud analysis only if turned on, masked text by default | HOME-33, OD-16 |
| Personal data replaced in the transcript, when the workspace turns masking on (never write "always masked") | HOME-34, SEC-10 |
| Each organization's data kept apart (application checks and PostgreSQL row-level security) | HOME-35 |
| Delete closes access at once, then purges transcript, analyses and audio | HOME-36 |
| No hidden cloud fallback when a local model fails | HOME-37 |
| Search across transcripts; share links; TXT, JSON, SRT, VTT exports | QA-07, QA-08, HOME-26 |

## 3. Tone

- Natural, confident, concrete, short.
- Warm but professional. Like a good Iranian account manager talking to a client, not a manual and
  not an advert on TV.
- Persian is written for Persian speakers. It must not read like a translation.
- No hype, no jokes, no slang, no bureaucratic (اداری) words.
- Say limits plainly and without apology: «امتیاز کیفیت برنامه‌ریزی‌شده است.»
- Address the reader as «شما» when it helps, but do not start every line with «شما». Imperatives
  («بدانید»، «بخوانید»، «بفرستید») are fine and natural.

## 4. How to state a benefit

**Pattern: the subject is the buyer, their team or their calls. The verb is a result they get.**
Homayar can be the subject only when the sentence still ends in the buyer's result.

Quotes are exact; «...» marks words cut from a longer approved line.

| Do | Why it works |
| --- | --- |
| «بدانید در هر تماس چه گذشت و چه کاری باید پیگیری شود.» | The reader is the subject; the verb is what they gain |
| «خلاصه و اقدام‌ها را بخوانید و بدون گوش دادن به کل تماس تصمیم بگیرید.» | Result first, then the effort saved |
| «تماس را با لینک اشتراک برای کارشناس یا مدیرش بفرستید، ...» | An action the buyer takes with the product |
| «هر کس به اندازهٔ نقشش دسترسی دارد: ...» | What a feature means for people, not its name |
| «خودتان تعیین می‌کنید داده کجا بماند» | Control stays with the buyer |

| Do not | Why it fails |
| --- | --- |
| «هستهٔ همایار هر جملهٔ تماس را می‌خواند» | Internals as the message |
| «تماس تمام می‌شود. فضای تحلیل باز می‌شود.» | Narrates the 3D scene |
| «یک فایل صوتی وارد می‌شود. یک گزارش بازبینی‌شده بیرون می‌آید.» | A file is the subject; reads like a machine |
| «نقش‌های مالک، مدیر، عضو و بیننده، تیم‌هایی با سرپرست و کارشناس» | A noun list with no verb and no outcome |

How to rewrite a line:

1. Ask: what does the buyer get from this, today? Write that.
2. Check the claim register (section 10). If the fact is not there, do not write it.
3. Make the buyer, the team or the calls the subject. Give the sentence a verb.
4. Cut every word that carries no information.
5. Read it aloud as a Persian manager would hear it. If it sounds translated, rewrite it.
6. Then write the English as a native English marketer would say the same claim.

## 5. Length

| Slot | Rule | Home numbers (Persian) |
| --- | --- | --- |
| Tight slot: hero lines, the particle slogan, text inside a 3D scene, eyebrows, chips, labels, nav, buttons, links, hints | Not longer than the old line by more than 15 percent, per language | Hero lines 14 to 28 characters, eyebrows 7 to 27, labels and links 7 to 33 |
| Section title | One short phrase or one short sentence | 21 to 42 characters, median 33 |
| Card title | A short phrase, no full stop | 6 to 25 characters, median 19 |
| Body line (card text, section text) | One or two short sentences | 68 to 176 characters, median 119. Aim for 140 or less |
| Meta title | Brand, colon, the benefit | «همایار: تحلیل مکالمه‌های مرکز تماس» (34) |
| Meta description | Problem, result, proof. About 150 to 220 characters | Home: 211 |

- Count characters with a script, not by eye (Persian joiners count as characters).
- A tight-slot line over the limit is allowed only as a listed exception, with a reason and a
  note to check it on the running page. On Home, only the three deployment labels are exceptions
  (they replaced raw ids such as `saas`).
- Read the component before you write: is the text a big kinetic line, a chip, a centered body,
  a card? The slot decides the length.
- A longer line outside a tight slot needs a note in its Reason ("Longer: fa 105 to 162").

## 6. Glossary

Use exactly these words. One idea has one name on the whole site. Words marked "owner" were
chosen by the owner on 2026-10-28 (question IDs from the Home doc).

| Idea | Persian | English | Note |
| --- | --- | --- | --- |
| call | تماس | call | |
| conversation | مکالمه | conversation | Owner Q-02. Not «گفت‌وگو» |
| transcript | متن مکالمه | transcript | Not «رونوشت» |
| recording | فایل صوتی | recording | «فایل صوتی تماس» when the call needs naming |
| agent (person) | کارشناس | agent | Owner Q-07. Not «اپراتور» |
| supervisor | سرپرست | supervisor | |
| QA team | تیم کیفیت | QA team | |
| quality control (the function) | کنترل کیفیت | quality control | Only for the function or team, not for the score |
| QC score (planned) | امتیاز کیفیت | quality score, QC scoring | |
| summary | خلاصه | summary | |
| action items | اقدام‌ها | action items | The console's word. Not «کارهای پیگیری» |
| insight (exists today) | بینش | insight | Per-call insights with a confidence level. Exists today (HOME-15) |
| business insight (planned) | بینش کسب‌وکار | business insight | Only for the planned business signals (upsell, churn, satisfaction trend). Never for today's per-call insights |
| analysis (what Homayar does) | تحلیل | analysis | Homayar's own work: «تحلیل مکالمه‌ها»، «تحلیل ابری». Never «بازبینی» for Homayar's work (OD-09) |
| review (what a person does) | بازبینی | review | Only the human act of a QA lead, supervisor or manager checking a call, with the person as the subject: «تیم کیفیت تماس‌ها را بازبینی می‌کند». Approved Home keeps «بازبینی» only in the unrendered pipeline step title |
| speech to text | تبدیل گفتار به متن | speech to text, transcription | The stage name, as in the approved sample stages. In benefit lines prefer the result: «متن کامل مکالمه» |
| problem (general) | مشکل، مشکل‌ها | problem | Plural «مشکل‌ها», not «مشکلات». Approved: «کدام مشکل در تماس‌های دیگر هم تکرار می‌شود» |
| issue (planned tracked item, open or closed) | مسئله، مسائل | issue | Only for the planned issue list, as in the approved sample «مسائل باز». Elsewhere say «مشکل» |
| incident (planned view) | حادثه، حوادث | incident | Only for the planned incidents view, as in the approved sample menu «حوادث». Never «رخداد» |
| severity | شدت | severity | Values: «زیاد»، «متوسط»، «کم» / High, Medium, Low (approved sample chips) |
| sentiment (planned) | احساس مشتری | sentiment | Approved sample chart: «توزیع احساس» / "Sentiment distribution" |
| evidence | شواهد | evidence | The console's word. Use «شواهد» everywhere, except the one short card title «گزارش با سند» |
| linked to the line it came from | وصل به جمله‌ای از مکالمه که از آن آمده | linked to the line it came from | |
| backed by the transcript | مستند به متن مکالمه | backed by the transcript | Never «مستند» alone: it reads as "documentary" |
| report with evidence | گزارش با سند | report you can check | A card title only (approved H-149). Do not use «سند» in other lines |
| root cause (planned) | ریشهٔ مشکل | root cause | Not «تشخیص مشکل» |
| coaching (planned) | آموزش کارشناس | coaching | Not «بستهٔ آموزشی» in new lines |
| upsell, churn (planned) | فروش بیشتر، ریزش مشتری | upsell, churn | Not «ریسک» |
| copilot (planned) | دستیار هوشمند | copilot | |
| AI | هوش مصنوعی | AI | |
| the web app | پنل | console | Owner Q-01. Not «کنسول» in Persian |
| overview screen | نمای کلی | overview | The real console screen |
| dashboard | داشبورد | Overview (in the sample menu) | Only as the sample menu label in simulated screens (approved `mock.nav[0]`). Never for the real overview screen |
| one screen | یک صفحه | one screen | |
| self-hosted | روی سرور خودتان | self-hosted, on your own servers | «روی سرور شما» only where the full phrase breaks the length rule (Home hero line, the owner's words) |
| who runs it | ما اجرا می‌کنیم، شما اجرا می‌کنید | we run it, you run it | Same words as the pricing page |
| hybrid | ترکیبی | hybrid | |
| managed cloud (planned) | نسخهٔ ابری | managed cloud | Not «SaaS» in Persian body text |
| cloud analysis | تحلیل ابری | cloud analysis | Not «استدلال ابری» |
| deployment | استقرار | deployment | |
| organization (customer, tenant) | سازمان | organization, org | In buyer text: «داده‌های هر سازمان، جدا». Never «مستأجر» or "tenant" in visible text |
| workspace | فضای کاری | workspace | Only when the text means the product setting of one customer: its policy, members, usage, masking switch. Approved H-193 uses both: «برنامه دسترسی هر فضای کاری را می‌سنجد ... دادهٔ سازمان دیگر» |
| admin (role) | مدیر | admin | Only inside the role list «مالک، مدیر، عضو یا بیننده» / "owner, admin, member or viewer" |
| manager (a person's boss) | مدیر + noun | manager | Always with a noun or a pronoun: «مدیر مرکز تماس»، «مدیر عملیات»، «مدیرش» (approved H-153). Never «مدیر» alone for a boss |
| owner, member, viewer (roles) | مالک، عضو، بیننده | owner, member, viewer | |
| personal-data masking | پوشاندن داده‌های شخصی | personal-data masking | |
| placeholder (masked value) | برچسب | placeholder | |
| deletion, purge | حذف، پاک شدن | delete, purge | Not «پاک‌سازی پایدار» or «بایت‌های رسانه» |
| egress control | کنترل خروج داده | egress control | |
| allowlist proxy | پراکسی با فهرست مجاز | allowlist proxy | IT and security pages |
| local model | مدل محلی | local model | |
| AI policy | سیاست هوش مصنوعی | AI policy | IT and security pages |
| stage | مرحله | stage | |
| status | وضعیت | status | |
| speaker separation (planned) | تفکیک گوینده‌ها | speaker separation | |
| audit log | گزارش رویدادها | audit log | The console's word |
| retention | مدت نگهداری | retention | |
| upload | بارگذاری | upload | |
| search | جست‌وجو | search | |
| share link | لینک اشتراک | share link | |
| export | خروجی | export | |
| phone system, PBX | مرکز تلفن | phone system, PBX | |
| extension number | شمارهٔ داخلی | extension number | |
| contact center | مرکز تماس | contact center | |
| call queue (contact-center sense) | صف تماس | queue | Fine for buyers. Never for job queues |
| step | قدم | step | Not «گام» |
| demo | دمو | demo | |
| pilot | پایلوت | pilot | |
| written proposal | پیشنهاد کتبی | written proposal | |
| sign in | ورود | log in | |
| planned (label) | برنامه‌ریزی‌شده | Planned / planned | See section 8 |
| simulation (label) | شبیه‌سازی | simulation | |
| sample data (label) | داده‌ی نمونه | sample data | Spelled «داده‌ی», not «دادهٔ» |

If a page needs a recurring word that is not here, do not choose it alone: ask the leader, who
adds one choice here for both writers. Until then, use the word the current site file uses and
flag it in your doc's message frame.

Where the Home page has no example, the choices above follow the product console's words
(`Homayar/frontend/src`): «کارشناس»، «اقدام‌ها»، «شواهد»، «گزارش رویدادها».

## 7. Avoid

| Avoid | Example | Write instead |
| --- | --- | --- |
| Scroll instructions | «اسکرول کنید»، «اسکرول را ادامه دهید»، "Keep scrolling" | Nothing. Say the benefit |
| Stage directions | «وارد ... می‌شوید»، «نگاه کنید»، «ادامه دهید»، "watch", "continue" | The benefit |
| Scene narration | «حلقه می‌چرخد»، «بلوک سفر می‌کند»، «جدول ساخته می‌شود»، «شماره محو می‌شود»، «فضای تحلیل باز می‌شود» | What the buyer gets from the thing the scene shows |
| Internals as the message | «هستهٔ همایار»، «خط لوله»، «عامل»، «پردازشگر»، «مستأجر»، pipeline, worker, queue, agent (software) | The result for the buyer. IT pages may name the technology plainly when the IT buyer needs it |
| Translation calques | «به شما اجازه می‌دهد»، «قادر به ... است»، «ساخته‌شده برای»، «درون محصول، نه چسبیده به آن»، «استدلال ابری»، «به رسمیت بشناس» | Plain Persian verbs: «می‌توانید»، «برای ...»، «تحلیل ابری» |
| Passive with «توسط» | «توسط ما اجرا می‌شود» | «ما اجرا می‌کنیم» |
| A needless «یک» before a noun | «یک کنسول واقعی، نه یک ایده» | «پنلی که همین امروز کار می‌کند». «یک» stays when it means the number one: «هر تماس، یک بینش» |
| «در نهایت» as filler | «در نهایت گزارش آماده می‌شود» | Cut it |
| Long «را» chains, stacked ezafe nouns | «مدت صدای تحلیل‌شدهٔ تماس‌ها» | Split the sentence or use a verb |
| Verbless noun lists in body text | «همدلی، حل مسئله، شفافیت، لحن، ...» | «... هر تماس در شش معیار، از همدلی تا مدیریت سکوت، امتیاز می‌گیرد ...» |
| Bureaucratic words | «نحوهٔ» in a new sentence، «مزبور»، «جهت»، «اقدام به ... نمودن»، «می‌باشد»، «صورت می‌گیرد»، «به‌منظور»، «با استفاده از» (the old hero «با استفاده از هوش مصنوعی.»)، «قابل ... بودن» («قابل بررسی‌اند»، example 4) | «چطور»، «این»، «برای»، «است»، plain verbs. **Exception:** «نحوهٔ کار» stays as the fixed name of the how-it-works page (nav, footer, the CTA link and the start of its meta title), because the owner approved `nav.howItWorks` «نحوهٔ کار». Do not use «نحوهٔ» anywhere else |
| Stock phrases that over-promise | «همه‌چیز در یک نگاه»، "Everything at a glance", «همه در یک پنل» | Name what is actually there: «وضعیت تماس‌ها در یک نگاه» |
| Lines that can be heard the wrong way | «کمتر گوش بدهید» (sounds like "pay customers less attention") | «خواندن به‌جای شنیدن» |
| The same word twice in one view | Eyebrow «خواندن به‌جای شنیدن» and body «به‌جای گوش دادن ...» | Start the body with the result |
| Time promises for planned items | «به‌زودی» / "Coming soon" as a planned label | «برنامه‌ریزی‌شده» / "Planned": one word for planned on the whole site. The chip `common.comingSoon` (next to «برای این نقش برنامه‌ریزی‌شده» on the solutions pages) moves to this word in group A, with an owner question, because OD-15 had kept both words |
| English hype words | leverage, seamless, empower, unlock, revolutionize, cutting-edge | Plain verbs |

## 8. Labels for planned and simulated items

These labels are claims. One rule:

- The label stays in the same line that held it.
- One exception: a section or screen title may drop the label when the eyebrow or the text in the
  same block carries it. Approved: `story.surfaces.title` «تماس‌های اخیر تیم‌تان، در یک صفحه»
  (H-098), with «پنل شبیه‌سازی‌شده» in its eyebrow and «شبیه‌سازی با داده‌ی نمونه‌اند» in its text.
- If a rewritten line no longer mentions the planned feature at all, its planned label goes with
  it. Approved: the meta description (H-002) dropped «امتیاز کیفیت و آموزش برنامه‌ریزی‌شده‌اند»
  together with those features.

| Case | Persian | English |
| --- | --- | --- |
| Body line about a planned feature | Start with «برنامه‌ریزی‌شده: » then a full sentence: «برنامه‌ریزی‌شده: هر کارشناس نکته‌های آموزشی روشن می‌گیرد، بر اساس ضعف‌های خودش.» | "Planned: each agent gets clear coaching tips, based on their own weak points." |
| Planned part at the end of a sentence | «... خروجی PDF برنامه‌ریزی‌شده است.» / «... برنامه‌ریزی‌شده‌اند.» | "PDF export is planned." |
| Chip or label | «امتیاز کیفیت · برنامه‌ریزی‌شده» (a middle dot with a space on each side) | "QC scoring · planned" (lowercase after the dot) |
| Partly planned | «بینش‌ها · بخشی برنامه‌ریزی‌شده» | "Insights · partly planned" |
| Eyebrow over planned content | «برنامه‌ریزی‌شده: گزارش کامل» | "Planned: the full report" |
| Simulation intro | «شبیه‌سازی با داده‌ی نمونه: ...» or «... شبیه‌سازی با داده‌ی نمونه‌اند.» | "A simulation with sample data: ..." |
| Simulated screen title | Keep «شبیه‌سازی‌شده» in the eyebrow or text: «پنل شبیه‌سازی‌شده» | "Simulated console" |
| Sample label | «داده‌ی نمونه» | "Sample data" |

Rules:

- A scroll hint that also holds a label is the common case: the label survives in the new line («شبیه‌سازی با داده‌ی نمونه. اسکرول کنید: ...» keeps «شبیه‌سازی با داده‌ی نمونه»).
- Do not mark something planned when it exists today. Insights exist; QC scores do not. On Home
  the intro first said "all four parts are planned" and had to change to «جز بینش‌های هر تماس،
  بقیه برنامه‌ریزی‌شده‌اند».
- A label must not contradict the text next to it. Chip, title and text in one block use the same
  name.
- Sample conversations, sample numbers and sample names inside a labeled simulation stay as they
  are, unless a glossary word or an unnatural phrase needs fixing.

## 9. Interface text, legal text and the footer

- `KEEP` is normal for interface text: aria labels, form field names, error messages, nav labels
  that are already short and clear, status words («در حال اجرا»، «انجام شد»).
- `KEEP` on a marketing line (headline, eyebrow, body, card, step, hint, CTA, meta) is rare. Its
  Reason must say why the line already sells in natural Persian.
- Legal pages (`legal`, `transparency`): accuracy over selling. Make the Persian natural and
  plain, and never change a legal meaning.
- **Footer (owner, 2026-10-28): no legal or technical notices in the footer.** The footer holds
  the brand, the brand line, the link columns and the copyright. Facts such as "no third-party
  scripts" or "only authorized recordings" belong on the privacy, security or legal pages.

## 10. Claims

1. Every fact in a new line must match a row in `docs/website/claim-register.md` whose status
   allows it: `KEEP`, or the supported part of a `REWORD` (the "New en / New fa" columns show the
   supported wording). Put the row ID in the Claim column.
2. `PLANNED` rows stay planned in the visible text. `SIMULATION` rows keep a simulation or sample
   label. `REMOVE` rows must not come back.
3. A fact that the register does not hold is a new claim, even if the code supports it. Add an
   owner question (`Q-nn`) with the repo evidence (`file:line`) and mark the line `Q-nn`.
4. Never invent numbers, customers, percentages, time savings, accuracy, certifications or
   partners. No «زودتر», "faster" or "save X hours" unless the register has it. The ROI page
   shows formulas, not promises.
5. Watch words that widen a claim: «همه»، «همیشه»، «هر»، "all", "every", "never". The register
   said "recent calls", so «همه در یک پنل» was wrong. Use them only when the register supports them.
6. Watch words that are slightly stronger than the register, and say so in the Reason
   («مستند به متن مکالمه» against "linked to the transcript").
7. The owner's decisions OD-01 to OD-16 stand. Hybrid is "masked text by default", never "only
   masked text" (OD-16). Managed cloud (SaaS) is planned (OD-04).

## 11. English

- Natural marketing English with the same claim as the Persian. Not a word-for-word translation.
  Approved: «خلاصه و اقدام‌ها را بخوانید و بدون گوش دادن به کل تماس تصمیم بگیرید.» became "Get the
  summary and the action items, and decide without hearing the whole call."
- US spelling. Sentence case for headings, labels and sample screens ("Call report", not "Call
  Report"). Keep brand and product names as they are (Homayar, PostgreSQL, Docker Compose).
- Short sentences. Contractions are fine in headlines ("Can't get to every call?").
- No serial comma, as in the current file: "a summary, action items and insights".
- "e-mail" with a hyphen, as in the current file. "AI" in capitals.
- No em dashes, no "--". Use a period, a comma or parentheses.
- The English line may be shorter or longer than the Persian one; the length rule is per language.

## 12. Persian orthography

- Persian «ی» and «ک», never the Arabic yeh (U+064A) or kaf (U+0643).
- Zero-width non-joiner (U+200C) in «می‌»، «نمی‌»، «ها»، «های»، «ای»، «اند»، «ام»، «تر»، «ترین» and in
  compounds: «برنامه‌ریزی‌شده»، «شبیه‌سازی»، «به‌جای»، «جست‌وجو»، «هم‌تیمی‌ها»، «مشتری‌ای».
- Ezafe after silent «ه» is «ـهٔ»: «همهٔ»، «نسخهٔ»، «ریشهٔ»، «شمارهٔ»، «خلاصهٔ». The one exception is
  the label «داده‌ی نمونه».
- Persian punctuation: «،» comma, «؛» semicolon, «؟» question mark, «» quotes.
- Brand: «همایار». Product and file names stay Latin inside Persian text: Docker Compose,
  PostgreSQL, Asterisk, FreePBX, TXT، JSON، SRT، VTT.
- Digits: follow the current file for each line (sample numbers on `/fa` are Persian digits:
  «۰۶:۱۲»؛ formats and versions stay Latin).
- Big kinetic lines (Home hero): each line stands alone; full sentences end with «.» or «؟».

## 13. Old to new: examples from the approved Home page

«...» marks words cut from a long line; ` / ` separates hero lines.

| # | Key | Old | New | Why |
| --- | --- | --- | --- | --- |
| 1 | `scene.warp.title` | «تماس تمام می‌شود. فضای تحلیل باز می‌شود.» | «تحلیل مکالمه‌ها، بعد از پایان تماس» | Scene narration became the owner's own benefit line |
| 2 | `scene.warp.text` | «اسکرول را ادامه دهید. وارد شبکه‌ای می‌شوید که گفت‌وگو را می‌خواند.» | «خلاصه و اقدام‌ها را بخوانید و بدون گوش دادن به کل تماس تصمیم بگیرید.» | A scroll instruction became the outcome for the supervisor |
| 3 | `scene.brain.title` | «هستهٔ همایار هر جملهٔ تماس را می‌خواند» | «از حرف مشتری تا گزارش سرپرست» | Internals became the buyer's path from call to report |
| 4 | `home.hero.title` | «فایل‌های صوتی تماس را به بازبینی‌هایی تبدیل کنید که با متن مکالمه قابل بررسی‌اند.» | «بدانید در هر تماس چه گذشت و چه کاری باید پیگیری شود.» | Stiff «قابل بررسی‌اند» became what the reader gets |
| 5 | `home.hero.headlines` | «تبدیل فایل‌های صوتی تماس‌ها / به گزارش بازبینی / با خلاصه و اقدام‌ها ...» | «به همهٔ تماس‌ها نمی‌رسید؟ / همایار می‌رسد. / خلاصه و اقدام‌ها. ...» | One sentence cut into pieces became six lines that each stand alone, starting with the buyer's problem |
| 6 | `scene.warp.eyebrow` | «از فضایی به فضای دیگر» | «خواندن به‌جای شنیدن» | Scene name became a three-word benefit (the first draft «کمتر گوش بدهید» was rejected: it can be heard as "care less") |
| 7 | `story.eyebrow` | «برنامه‌ریزی‌شده: چهار عامل» | «برنامه‌ریزی‌شده: گزارش کامل» | «عامل» is internals; the planned label stays |
| 8 | `home.outputs.items[0].text` | «برنامه‌ریزی‌شده: همدلی، حل مسئله، شفافیت، لحن، ...، برای هر تماس امتیازدهی می‌شود» | «برنامه‌ریزی‌شده: هر تماس در شش معیار، از همدلی تا مدیریت سکوت، امتیاز می‌گیرد و همهٔ کارشناس‌ها با همین معیارها سنجیده می‌شوند.» | A noun list became a sentence with the buyer's reason (fairness) |
| 9 | `home.deployment.items[0].text` | «برنامه‌ریزی‌شده: توسط ما در یک منطقهٔ ابری تأییدشده اجرا می‌شود؛ جداسازی مستأجرها ...» | «برنامه‌ریزی‌شده: همایار را ما در ابر برایتان اجرا می‌کنیم و داده‌های هر سازمان را ... جدا نگه می‌داریم.» | Passive «توسط» and «مستأجر» became active, plain Persian |
| 10 | `home.deployment.items[2].text` | «... استدلال ابری اختیاری به‌طور پیش‌فرض متن پوشانده‌شده می‌گیرد ...» | «فایل صوتی و متن مکالمه پیش خودتان می‌ماند. تحلیل ابری فقط وقتی روشن است که خودتان بخواهید ...» | Calque «استدلال ابری» became «تحلیل ابری»; the buyer keeps control |
| 11 | `home.trust.items[1].title` | «جداسازی مستأجرها» | «داده‌های هر سازمان، جدا» | Jargon became what the buyer cares about |
| 12 | `home.trust.items[2].text` | «حذف، دسترسی را بلافاصله می‌بندد و سپس یک پاک‌سازی پایدار متن‌ها، تحلیل‌ها و بایت‌های رسانه را حذف می‌کند.» | «با حذف یک تماس، دسترسی به آن همان لحظه بسته می‌شود؛ بعد متن، تحلیل‌ها و خود فایل صوتی کامل پاک می‌شوند.» | Internals («بایت‌های رسانه») became the same fact in plain words |
| 13 | `home.product.items[3].text` | «نقش‌های مالک، مدیر، عضو و بیننده، تیم‌هایی با سرپرست و اپراتور، و دعوت‌نامه.» | «هر کس به اندازهٔ نقشش دسترسی دارد: مالک، مدیر، عضو یا بیننده. ...» | A feature list became what roles do for people |
| 14 | `story.surfaces.title` | «کنسولی شبیه‌سازی‌شده با داده‌ی نمونه» | «تماس‌های اخیر تیم‌تان، در یک صفحه» | A label became a benefit, kept inside the register ("recent calls"; the draft «همه در یک پنل» over-claimed) |
| 15 | `story.trust.title` | «حریم خصوصی یک ویژگی محصول است» | «حریم خصوصی مشتری‌هایتان، بخشی از خود محصول» | A spec sentence became the buyer's customers as the subject |

English examples: "Listen less" was rejected (wrong meaning); "Deletion that purges" became
"Delete really means delete"; "The recording stays where you decide" became "You decide where your data
lives" (`story.flow.title`); "Call Report" became "Call report".

## 14. How to deliver a page

1. List every string the page renders: the dictionary keys its components read (array items one
   by one) and the string literals in those components. Use a script. Keys that no component reads
   go in a separate table, unchanged.
2. Write Persian first, then English, following sections 4 to 12.
3. Change only string values. Same keys, same array lengths, no JSX, class, style or scene change,
   unless your SPEC allows an exception.
4. Write the page doc in the Home doc's shape: header table, owner questions (only for a new claim
   or an owner choice), then the table `ID | Section | Key | Old fa | New fa | New en | Claim |
   Reason` in the order the visitor meets the lines, then a short message frame. Mark keys that
   other pages share with `(shared)`.
5. Check before handoff: the doc and the code agree character for character; no «اسکرول» or
   "scroll" in a rendered line; no Arabic yeh (U+064A) or kaf (U+0643); no em dashes; the length rule; every Claim
   cell holds a register row, `Q-nn` or `none`; `pnpm lint`, `pnpm test` and `pnpm build` pass.
6. Read every Persian line aloud as the buyer would hear it. If one sounds translated, rewrite it.

The writers' scripts used for the Home page are in
`/Users/sinashamsizadeh/foreman/20261028-hom327/hom327-t1/handoffs/tools/` (`build.mjs` applies
copy by key path and checks it; `gendoc.mjs` writes the doc; `verify-doc.mjs` compares the doc with
the code). Writers may copy and adapt them. Critics do not read the handoffs folder and use their
own scripts.
