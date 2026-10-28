# Website copy v2: Pricing

| Field | Value |
| --- | --- |
| Ticket | HOM-327 (Urgent), stage P, group B |
| Base commit | `8cf5679` (the approved Home page) |
| Date | 2026-10-28 |
| Status | Waiting for review |
| Pages | `/fa/pricing`, `/en/pricing` |
| How to answer | Name a row ID (`PC-001` to `PC-020`) or a question ID and say: keep, change to ..., or remove |
| Code | `website/i18n/fa.ts` and `website/i18n/en.ts`: string values only. Same keys and array lengths |

The voice follows the approved Home page (`docs/website/copy-v2-home.md`): every line says what
the buyer gets, no line tells the visitor to scroll or narrates the 3D scene, and planned or
simulated items keep a visible «برنامه‌ریزی‌شده» / "Planned", «شبیه‌سازی» / "Simulation" or
«داده‌ی نمونه» / "sample data" label. The Home glossary applies.

What changed, in short:

- No amounts exist; OD-05 stands. Plan names and amounts are kept.
- «SaaS» became the glossary «نسخهٔ ابری» / "Managed cloud", with its planned label next to it.
- 20 rows. Persian changed in 10 rows, English in 5 rows. `KEEP` rows are labels,
  interface text, legal text, formulas, sample data, or a line that already says what the buyer gets
  (its Reason says why).

## Owner questions

| ID | Question | Rows | Evidence | Applied now | Options |
| --- | --- | --- | --- | --- | --- |
| Q-B02 | The pricing hero title changed from «قیمت‌گذاری در حال آماده‌سازی است» / "Pricing is being prepared" to «برای بودجه‌تان، پیشنهاد کتبی بگیرید» / "Get a written proposal for your budget". It sells the one thing a buyer can get today (PRI-03). The text and the notice under it still say amounts are not published and nothing is an offer. Is this title right? | PC-004 | PRI-03, OD-05 | Applied | (a) Keep the new title. (b) Go back to «قیمت‌گذاری در حال آماده‌سازی است». |

No row uses a claim outside `docs/website/claim-register.md`.

## Lines in the order the visitor meets them

`Old fa` is the text at the base. For a component literal, `Key` is `file:line` and `Old fa`
is the visible text. `(shared)` means the key also shows on another page in this doc.
`(not rendered)` means no component shows it. A row with `[*]` covers several keys with the same
reason; they are all `KEEP`.

| ID | Section | Key | Old fa | New fa | New en | Claim | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PC-001 | Head | `pricing.meta.title` | قیمت‌گذاری | KEEP | KEEP | none | Page title. |
| PC-002 | Head | `pricing.meta.description` | قیمت‌گذاری همایار در حال آماده‌سازی است. این صفحه پس از نهایی شدن مدل تجاری، طرح‌ها و مبالغ را فهرست می‌کند. هیچ‌چیز در آن پیشنهاد فروش نیست. | قیمت‌های همایار هنوز نهایی نشده است. وقتی مدل تجاری قطعی شود، طرح‌ها و مبلغ‌ها اینجا می‌آیند. تا آن زمان، برای بودجه‌تان پیشنهاد کتبی بخواهید. هیچ‌چیز در این صفحه پیشنهاد فروش نیست. | Homayar pricing is not final yet. Plans and amounts will appear here once the commercial model is set. Until then, ask us for a written proposal. Nothing on this page is an offer. | PRI-01, PRI-03, OD-05 | Adds what the buyer can do today (PRI-03). Keeps «هیچ‌چیز در این صفحه پیشنهاد فروش نیست». Glossary «پیشنهاد کتبی». Longer: fa 141 to 181, en 135 to 179. |
| PC-003 | Hero | `pricing.hero.eyebrow` | قیمت‌گذاری | KEEP | KEEP | none | Short label. |
| PC-004 | Hero | `pricing.hero.title` | قیمت‌گذاری در حال آماده‌سازی است | برای بودجه‌تان، پیشنهاد کتبی بگیرید | Get a written proposal for your budget | PRI-03, OD-05 | The old title said only that pricing is not ready. Now it says what the buyer can get today. The text and the notice below say amounts are not published. Longer: en 25 to 38. |
| PC-005 | Hero | `pricing.hero.text` | در حال نهایی کردن مدل تجاری و گزینه‌های پرداخت هستیم. تا آن زمان این صفحه فقط ساختار طرح‌ها را بدون مبلغ نشان می‌دهد. هیچ‌چیز در این صفحه پیشنهاد فروش نیست. | در حال نهایی کردن مدل تجاری و روش‌های پرداخت هستیم. تا آن زمان، اینجا فقط ساختار طرح‌ها را می‌بینید، بدون مبلغ. هیچ‌چیز در این صفحه پیشنهاد فروش نیست. | KEEP | PRI-02 | Active voice for the visitor («می‌بینید»). Same three facts. |
| PC-006 | Hero | `pricing.notice` | مبالغ، واحدهای صورت‌حساب و شرایط پس از نهایی شدن اینجا منتشر می‌شوند. اگر امروز برای بودجه به عدد نیاز دارید، با ما تماس بگیرید تا یک پیشنهاد کتبی بفرستیم. | مبلغ‌ها، واحد صورت‌حساب و شرایط، وقتی نهایی شدند، همین‌جا منتشر می‌شوند. اگر همین امروز برای بودجه عدد لازم دارید، با ما تماس بگیرید تا پیشنهاد کتبی بفرستیم. | KEEP | PRI-03 | Shown after «در حال آماده‌سازی.» (`common.inProgress`). Plainer words; same facts. |
| PC-007 | Hero | `pricing.deployments[0].name` | SaaS | نسخهٔ ابری | Managed cloud | OD-04, HOME-31 | Glossary «نسخهٔ ابری» instead of «SaaS», as on the Home cards (H-170). The text next to it keeps the planned label. Longer: fa 4 to 10, en 4 to 13. |
| PC-008 | Hero | `pricing.deployments[0].text` | برنامه‌ریزی‌شده. ما اجرا می‌کنیم. | KEEP | KEEP | PRI-04 | Planned label and who runs it. Same words as the Home deployment label (H-167). |
| PC-009 | Hero | `pricing.deployments[1].name` | روی سرور شما | روی سرور خودتان | KEEP | PRI-05, HOME-32 | Glossary phrase «روی سرور خودتان», used across the site. Longer: fa 12 to 15. |
| PC-010 | Hero | `pricing.deployments[1].text` | شما اجرا می‌کنید. | KEEP | KEEP | PRI-05 | Who runs it. Short. |
| PC-011 | Plans (3D lift) | `pricing.plans[0].name` | ماهانه | KEEP | KEEP | PRI-10, OD-05 | Plan name (OD-05). |
| PC-012 | Plans (3D lift) | `pricing.plans[0].text` | برنامه‌ریزی‌شده: پرداخت ماه به ماه. | KEEP | KEEP | PRI-06 | Planned label. Plain. |
| PC-013 | Plans (3D lift) | `pricing.plans[0].amount` | به‌زودی اعلام می‌شود | هنوز اعلام نشده | Not announced yet | PRI-09, OD-05 | «به‌زودی» promises a time (style guide, section 7). Now it says what is true today: no amount is published. |
| PC-014 | Plans (3D lift) | `pricing.plans[1].name` | سالانه | KEEP | KEEP | PRI-10, OD-05 | Plan name (OD-05). |
| PC-015 | Plans (3D lift) | `pricing.plans[1].text` | برنامه‌ریزی‌شده: تعهد یک‌ساله. | برنامه‌ریزی‌شده: قرارداد یک‌ساله. | KEEP | PRI-07 | «تعهد یک‌ساله» is a calque of "commit for a year". Keeps the planned label. |
| PC-016 | Plans (3D lift) | `pricing.plans[1].amount` | به‌زودی اعلام می‌شود | هنوز اعلام نشده | Not announced yet | PRI-09, OD-05 | Same as the plan above: no time promise. |
| PC-017 | Plans (3D lift) | `pricing.plans[2].name` | سازمانی | KEEP | KEEP | PRI-10, OD-05 | Plan name (OD-05). |
| PC-018 | Plans (3D lift) | `pricing.plans[2].text` | برنامه‌ریزی‌شده: شرایط قراردادی، استقرار خصوصی و پشتیبانی. | برنامه‌ریزی‌شده: قرارداد ویژه، استقرار اختصاصی و پشتیبانی. | KEEP | PRI-08 | «شرایط قراردادی» was bureaucratic. Same three items. |
| PC-019 | Plans (3D lift) | `pricing.plans[2].amount` | با درخواست | KEEP | KEEP | PRI-09 | No amount (OD-05). Kept: «توافقی» fits but adds "negotiable", a commercial claim the register does not hold, and other short forms break the length rule (11 characters). |
| PC-020 | Plans (3D lift) | `pricing.cta` | درخواست پیشنهاد کتبی | KEEP | KEEP | PRI-03 | Button on each plan. Matches the new hero title. |

## Length rule

Tight slots (eyebrows, hints, chips, labels, buttons, links, the role text in the 3D role panels,
the heading and text of the pinned day cards, the layer scene, the masking title, form labels and choices) are within 15 percent of the old line in both
languages, except the rows below.

| Row | Key | Characters | How it looks |
| --- | --- | --- | --- |
| PC-007 | `pricing.deployments[0].name` | fa 4 to 10 | Bold label in an inline list under the pricing hero (`flex-wrap`). The item wraps to its own line if needed; checked in the built HTML. |
| PC-007 | `pricing.deployments[0].name` | en 4 to 13 | Bold label in an inline list under the pricing hero (`flex-wrap`). The item wraps to its own line if needed; checked in the built HTML. |
| PC-009 | `pricing.deployments[1].name` | fa 12 to 15 | Bold label in an inline list under the pricing hero (`flex-wrap`). The item wraps to its own line if needed; checked in the built HTML. |

Lines outside tight slots that got longer by more than 15 percent are listed below. They are
body text, list items and meta descriptions that wrap freely. Each one has the same note at the end
of its Reason.

| Row | Key | Characters |
| --- | --- | --- |
| PC-002 | `pricing.meta.description` | fa 141 to 181 |
| PC-002 | `pricing.meta.description` | en 135 to 179 |
| PC-004 | `pricing.hero.title` | en 25 to 38 |

## Component files

This page has no visible string literal in a component.
