# Website copy v2: ROI calculator

| Field | Value |
| --- | --- |
| Ticket | HOM-327 (Urgent), stage P, group B |
| Base commit | `8cf5679` (the approved Home page) |
| Date | 2026-10-28 |
| Status | Waiting for review |
| Pages | `/fa/roi`, `/en/roi` |
| How to answer | Name a row ID (`RO-001` to `RO-053`) and say: keep, change to ..., or remove |
| Code | `website/i18n/fa.ts` and `website/i18n/en.ts`: string values only. Same keys and array lengths |

The voice follows the approved Home page (`docs/website/copy-v2-home.md`): every line says what
the buyer gets, no line tells the visitor to scroll or narrates the 3D scene, and planned or
simulated items keep a visible «برنامه‌ریزی‌شده» / "Planned", «شبیه‌سازی» / "Simulation" or
«داده‌ی نمونه» / "sample data" label. The Home glossary applies.

What changed, in short:

- The page shows formulas. No line promises a result, a saving or a number.
- Formula labels, error messages and notes stay as they are: accuracy matters more than selling.
- `website/components/story/roi-math.ts` has no copy, only the sample values (rows RO-010 and others). No change there.
- 53 rows. Persian changed in 10 rows, English in 6 rows. `KEEP` rows are labels,
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
| RO-001 | Head | `roi.meta.title` | محاسبه‌گر زمان بازبینی | KEEP | KEEP | none | Page title. Says what the tool does. |
| RO-002 | Head | `roi.meta.description` | عددهای خودتان را وارد کنید و ساعت‌های بازبینی ماهانه را، امروز و با همایار، حساب کنید. همهٔ فرمول‌ها در صفحه هست تا نتیجه را با دست بررسی کنید. | با عددهای خودتان حساب کنید تیم کیفیت‌تان هر ماه چند ساعت صرف بازبینی تماس‌ها می‌کند، امروز و با همایار. همهٔ فرمول‌ها در صفحه هست تا نتیجه را خودتان وارسی کنید. | Use your own numbers to work out how many hours your QA team spends reviewing calls each month, today and with Homayar. Every formula is on the page, so you can check the result yourself. | ROI-01 | Names who spends the hours (glossary «تیم کیفیت»). No result promised. Longer: en 150 to 187. |
| RO-003 | Hero | `roi.hero.eyebrow` | محاسبه‌گر زمان بازبینی | KEEP | KEEP | none | Short label, the tool's name. |
| RO-004 | Hero | `roi.hero.title` | ساعت‌های بازبینی خود را حساب کنید | بازبینی تماس‌ها چند ساعت از تیمتان می‌گیرد؟ | How many hours does call review cost your team? | ROI-01 | The buyer's own question. «خود» in «ساعت‌های بازبینی خود» was formal. No number promised. Longer: fa 33 to 43, en 23 to 47. |
| RO-005 | Hero | `roi.hero.text` | عددهای خودتان را وارد کنید. همهٔ فرمول‌ها نمایش داده می‌شوند تا بتوانید نتیجه را با دست بررسی کنید. | عددهای خودتان را بنویسید. همهٔ فرمول‌ها جلوی چشمتان است تا نتیجه را با ماشین‌حساب هم بسنجید. | Type your own numbers. Every formula is on screen, so you can check the result with a calculator. | ROI-02 | «نمایش داده می‌شوند» was passive. Longer: en 83 to 97. |
| RO-006 | Calculator | `roi.sample` | مقدارهای زیر داده‌ی نمونه‌اند، نه اندازه‌گیری. هر مقدار برآورد خود شماست. آن‌ها را با عددهای خودتان عوض کنید. | عددهای زیر داده‌ی نمونه‌اند، نه اندازه‌گیری. آن‌ها را با برآورد خودتان عوض کنید. | The numbers below are sample data, not measurements. Replace them with your own estimates. | ROI-03, ROI-11, ROI-12, ROI-13, ROI-14, ROI-15 | Keeps the sample-data label. The old middle sentence «هر مقدار برآورد خود شماست» said the samples were the visitor's own estimate, which was confusing. |
| RO-007 | Calculator | `roi.fields.agents.label` | اپراتورهایی که تماس‌هایشان بازبینی می‌شود | تعداد کارشناس‌هایی که تماس‌هایشان بازبینی می‌شود | KEEP | none | Glossary «کارشناس» (was «اپراتور»). «تعداد» makes it clear the field wants a number. Longer: fa 41 to 48. |
| RO-008 | Calculator | `roi.fields.agents.hint` (not rendered) |  | KEEP | KEEP | none | Empty string. `RoiCalculator.tsx:90` shows a hint only when it is not empty, so nothing renders. |
| RO-009 | Calculator | `roi.fields.agents.unit` | نفر | KEEP | KEEP | none | Unit next to the field. |
| RO-010 | Calculator | `components/story/roi-math.ts:21` | ۲۰ / 20 | KEEP | KEEP | ROI-11 | Sample value 20 (۲۰ on /fa) in the field. Labeled by `roi.sample`. |
| RO-011 | Calculator | `roi.fields.calls.label` | تماس‌های بازبینی‌شدهٔ هر اپراتور در ماه | تماس‌های بازبینی‌شدهٔ هر کارشناس در ماه | KEEP | none | Glossary «کارشناس». Same length. |
| RO-012 | Calculator | `roi.fields.calls.hint` (not rendered) |  | KEEP | KEEP | none | Empty string, not rendered (same as the agents hint). |
| RO-013 | Calculator | `roi.fields.calls.unit` | تماس | KEEP | KEEP | none | Unit. |
| RO-014 | Calculator | `components/story/roi-math.ts:22` | ۴۰ / 40 | KEEP | KEEP | ROI-12 | Sample value 40. |
| RO-015 | Calculator | `roi.fields.minutesToday.label` | دقیقه‌های بازبینی یک تماس، امروز | زمان بازبینی هر تماس، امروز | KEEP | none | «دقیقه‌های بازبینی یک تماس» repeated the unit shown next to the field. |
| RO-016 | Calculator | `roi.fields.minutesToday.hint` | با گوش دادن به فایل صوتی. | KEEP | KEEP | none | Short and concrete. |
| RO-017 | Calculator | `roi.fields.minutesToday.unit` | دقیقه | KEEP | KEEP | none | Unit. |
| RO-018 | Calculator | `components/story/roi-math.ts:23` | ۱۲ / 12 | KEEP | KEEP | ROI-13 | Sample value 12. |
| RO-019 | Calculator | `roi.fields.minutesHomayar.label` | دقیقه‌های بازبینی یک تماس، با همایار | زمان بازبینی هر تماس، با همایار | KEEP | ROI-09 | Same pattern as the field above. |
| RO-020 | Calculator | `roi.fields.minutesHomayar.hint` | با خواندن متن مکالمه و خلاصه. این برآورد خود شماست. | KEEP | KEEP | ROI-04 | Says what the reviewer reads and that the number is the visitor's estimate. |
| RO-021 | Calculator | `roi.fields.minutesHomayar.unit` | دقیقه | KEEP | KEEP | none | Unit. |
| RO-022 | Calculator | `components/story/roi-math.ts:24` | ۳ / 3 | KEEP | KEEP | ROI-14 | Sample value 3. |
| RO-023 | Calculator | `roi.fields.rate.label` | هزینهٔ یک ساعت کار بازبین | KEEP | KEEP | none | Field label. Clear as is. |
| RO-024 | Calculator | `roi.fields.rate.hint` | به واحد پول خودتان. | KEEP | KEEP | none | Short hint. |
| RO-025 | Calculator | `roi.fields.rate.unit` | در ساعت | KEEP | KEEP | none | Unit. |
| RO-026 | Calculator | `components/story/roi-math.ts:25` | ۱۰۰ / 100 | KEEP | KEEP | ROI-15 | Sample value 100. |
| RO-027 | Calculator | `roi.errors.whole` | یک عدد صحیح از ۰ تا ۱۰۰٬۰۰۰ وارد کنید. | KEEP | KEEP | ROI-27 | Error message. The limit of the field. |
| RO-028 | Calculator | `roi.errors.minutes` | یک عدد از ۰ تا ۶۰۰ وارد کنید. | KEEP | KEEP | ROI-28 | Error message. |
| RO-029 | Calculator | `roi.errors.rate` | یک عدد از ۰ تا ۱٬۰۰۰٬۰۰۰٬۰۰۰ وارد کنید. | KEEP | KEEP | ROI-29 | Error message. |
| RO-030 | Result | `roi.result.title` | نتیجه | KEEP | KEEP | none | Small label over the result. |
| RO-031 | Result | `roi.result.hoursUnit` | ساعت | KEEP | KEEP | none | Unit after the big number. |
| RO-032 | Result | `roi.formulas.items.S` | ساعت‌های بازبینی صرفه‌جویی‌شده در ماه | KEEP | KEEP | ROI-19, ROI-24 | Under the big number and in the formula list. A formula label: accuracy matters more than selling. |
| RO-033 | Result | `roi.result.today` | امروز | KEEP | KEEP | none | Bar label. |
| RO-034 | Result | `roi.result.withHomayar` | با همایار | KEEP | KEEP | none | Bar label. |
| RO-035 | Result | `roi.result.gap` | صرفه‌جویی‌شده | KEEP | KEEP | ROI-19 | Legend label. |
| RO-036 | Result | `roi.formulas.items.C` | هزینهٔ ساعت‌های صرفه‌جویی‌شده در ماه، با نرخ خودتان | KEEP | KEEP | ROI-06 | Cost line label, also in the formula list. Says it is the visitor's rate. |
| RO-037 | Result | `roi.result.costUnit` | به واحد پول شما | KEEP | KEEP | none | Unit. |
| RO-038 | Result | `roi.result.invalid` | فیلد مشخص‌شده را اصلاح کنید تا نتیجه نمایش داده شود. | KEEP | KEEP | none | Error text. |
| RO-039 | Result | `roi.result.none` | با این عددها هیچ زمان بازبینی صرفه‌جویی نمی‌شود. | KEEP | KEEP | ROI-10 | States the result honestly. |
| RO-040 | Formulas | `roi.formulas.title` | فرمول‌ها | KEEP | KEEP | none | Heading. |
| RO-041 | Formulas | `roi.formulas.text` | هر خط از عددهای بالا استفاده می‌کند. هر خط را می‌توانید با ماشین‌حساب بررسی کنید. | هر خط با عددهای بالا حساب شده و می‌توانید آن را با ماشین‌حساب بسنجید. | Each line uses the numbers above, and you can check it with a calculator. | ROI-21 | One sentence instead of two that both began with «هر خط». |
| RO-042 | Formulas | `roi.formulas.items.T` | تماس‌های بازبینی‌شده در ماه | KEEP | KEEP | ROI-21 | Formula label. |
| RO-043 | Formulas | `roi.formulas.items.H1` | ساعت‌های بازبینی در ماه، امروز | KEEP | KEEP | ROI-22 | Formula label. |
| RO-044 | Formulas | `roi.formulas.items.H2` | ساعت‌های بازبینی در ماه، با همایار | KEEP | KEEP | ROI-23 | Formula label. |
| RO-045 | Formulas | `components/story/RoiCalculator.tsx:39` | T = N × K … C = S × W | KEEP | KEEP | ROI-21, ROI-25 | The formulas (`T = N × K` to `C = S × W`) and the field symbols (`RoiCalculator.tsx:12`). Math, not copy. The file is outside group B. |
| RO-046 | Formulas | `roi.formulas.minutes` | ۶۰ تعداد دقیقه‌های یک ساعت است. | KEEP | KEEP | ROI-26 | Formula note. |
| RO-047 | Formulas | `roi.formulas.rounding` | ساعت‌ها و هزینه تا یک رقم اعشار گرد می‌شوند. | KEEP | KEEP | ROI-30 | Formula note. |
| RO-048 | Notes | `roi.notes.title` | این صفحه چه چیزی را می‌شمارد | KEEP | KEEP | none | Heading. Plain. |
| RO-049 | Notes | `roi.notes.items[0]` | فقط ساعت‌های بازبینی. خط هزینه، ساعت‌های صرفه‌جویی‌شده ضرب در نرخ ساعتی خود شماست. همایار این مبلغ را تضمین نمی‌کند. | KEEP | KEEP | ROI-07 | Says Homayar does not promise the amount. Accuracy over selling. |
| RO-050 | Notes | `roi.notes.items[1]` | مقدارهایی که هنگام باز شدن صفحه پر شده‌اند داده‌ی نمونه‌اند. اندازه‌گیری هیچ تیمی نیستند. | KEEP | KEEP | ROI-08 | Sample-data note. |
| RO-051 | Notes | `roi.notes.items[2]` | دقیقه‌های بازبینی یک تماس با همایار برآورد خود شماست. | KEEP | KEEP | ROI-09 | Estimate note. |
| RO-052 | What a reviewer gets | `roi.product.title` | بازبین از همایار چه می‌گیرد | KEEP | KEEP | ROI-05 | Keeps: already says what the buyer gets. |
| RO-053 | What a reviewer gets | `roi.product.text` | همایار برای هر فایل صوتی تماس که بارگذاری می‌کنید، متن مکالمه و یک خلاصه می‌نویسد، همراه با اقدام‌ها و بینش‌هایی که به متن مکالمه متصل‌اند. | برای هر فایل صوتی که بارگذاری می‌کنید، متن کامل مکالمه و خلاصه می‌گیرید، همراه با اقدام‌ها و بینش‌هایی که هر کدام به جمله‌ای از مکالمه وصل‌اند. پس بازبین به‌جای گوش دادن، می‌خواند. | For each recording you upload, you get the full transcript and a summary, with action items and insights each linked to a line of the call. Your reviewer reads instead of listening. | ROI-05, SUP-03, HOME-24 | The buyer is the subject («می‌گیرید»). Ends with the reason the hours drop, in the words of the approved Home line (H-041). No number. Longer: fa 139 to 180, en 135 to 181. |

## Length rule

Tight slots (eyebrows, hints, chips, labels, buttons, links, the role text in the 3D role panels,
the heading and text of the pinned day cards, the layer scene, the masking title, form labels and choices) are within 15 percent of the old line in both
languages, except the rows below.

| Row | Key | Characters | How it looks |
| --- | --- | --- | --- |
| RO-007 | `roi.fields.agents.label` | fa 41 to 48 | Field label in a two-column grid. It wraps to two lines at most on narrow screens, like the other labels. |

Lines outside tight slots that got longer by more than 15 percent are listed below. They are
body text, list items and meta descriptions that wrap freely. Each one has the same note at the end
of its Reason.

| Row | Key | Characters |
| --- | --- | --- |
| RO-002 | `roi.meta.description` | en 150 to 187 |
| RO-004 | `roi.hero.title` | fa 33 to 43 |
| RO-004 | `roi.hero.title` | en 23 to 47 |
| RO-005 | `roi.hero.text` | en 83 to 97 |
| RO-053 | `roi.product.text` | fa 139 to 180 |
| RO-053 | `roi.product.text` | en 135 to 181 |

## Keys the pages do not render

- `roi.fields.agents.hint` (RO-008): Empty string. `RoiCalculator.tsx:90` shows a hint only when it is not empty, so nothing renders.
- `roi.fields.calls.hint` (RO-012): Empty string, not rendered (same as the agents hint).

## Component files

- `website/components/story/roi-math.ts:21` (RO-010): Sample value 20 (۲۰ on /fa) in the field. Labeled by `roi.sample`.
- `website/components/story/roi-math.ts:22` (RO-014): Sample value 40.
- `website/components/story/roi-math.ts:23` (RO-018): Sample value 12.
- `website/components/story/roi-math.ts:24` (RO-022): Sample value 3.
- `website/components/story/roi-math.ts:25` (RO-026): Sample value 100.
- `website/components/story/RoiCalculator.tsx:39` (RO-045): The formulas (`T = N × K` to `C = S × W`) and the field symbols (`RoiCalculator.tsx:12`). Math, not copy. The file is outside group B.
