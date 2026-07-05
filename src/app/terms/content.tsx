"use client";

import { renderMarkdown } from "@/lib/markdown";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware Terms of Service. English is the canonical source; `fa` is a
 * faithful, complete Persian translation; other locales fall back to English.
 * The body is authored as first-party markdown and rendered to HTML the same
 * way the original server page did (renderMarkdown + .prose-dark), so the markup
 * is identical — only the source text is externalized per locale.
 */
const en = `# Terms of Service

_Last updated: June 12, 2026_

These Terms of Service ("Terms") govern access to and use of the Homayar platform and website (the "Service"), operated by KSF Company ("Homayar," "we," "us," or "our"). By creating an account, executing an order form referencing these Terms, or using the Service, you ("Customer," "you") agree to be bound by these Terms. If you are accepting on behalf of an organization, you represent that you have authority to bind that organization.

## 1. The Service

Homayar is an agentic AI voice analysis platform for call centers and enterprise customer-service teams. The Service ingests recorded or uploaded customer calls and produces speech-to-text transcripts, AI-driven quality-control scores, issue detection, business insights, coaching recommendations, and reporting dashboards. The Service provides decision support: AI-generated scores, classifications, and recommendations are analytical aids, not final determinations. Customer remains responsible for all employment, compliance, and business decisions made using Service outputs, and should ensure qualified human review of any output used for consequential decisions. We may improve, modify, or update the Service, provided changes do not materially degrade core functionality during a paid subscription term.

## 2. Accounts

You must provide accurate, complete registration information and keep it current. You are responsible for maintaining the confidentiality of credentials and for all activity under your account, including activity by your authorized users (employees, contractors, and agents you permit to use the Service). You will notify us promptly at info@ksf.ir of any suspected unauthorized access. We may suspend accounts that present a security risk to the Service or other customers, with notice where practicable.

## 3. Subscriptions and Billing

The Service is offered on a subscription basis under the plan, usage limits (such as analyzed call hours or agent seats), and fees specified at purchase or in an order form. Payments are processed by **Stripe**; by subscribing you authorize recurring charges to your designated payment method and agree to Stripe's applicable terms. Unless otherwise stated in an order form:

- Subscriptions renew automatically at the end of each billing period (monthly or annual) unless cancelled before renewal.
- Fees are stated in USD, are exclusive of taxes, and are non-refundable except where required by law or expressly stated in these Terms.
- Usage above plan limits may incur overage charges at the rates published for your plan.
- We may change pricing with at least 30 days' notice, effective at your next renewal.
- Overdue amounts may result in suspension of access after 10 days' written notice, and may accrue interest at the lesser of 1.5% per month or the maximum lawful rate.

Cancellation takes effect at the end of the current billing period; you retain access until then.

## 4. Acceptable Use

You will not, and will not permit any user to:

- Upload calls or content you lack the legal right to record, possess, or process
- Use the Service to violate any law, including wiretapping, recording-consent, privacy, or employment laws
- Attempt to probe, breach, or circumvent security or authentication measures
- Reverse engineer, decompile, or extract the underlying models, prompts, or source code of the Service
- Use the Service to build or train a competing product
- Resell, sublicense, or provide the Service to third parties except authorized users
- Submit malicious code or use the Service in a manner that degrades it for other customers
- Use outputs to make fully automated decisions with legal or similarly significant effects on individuals without meaningful human review

We may suspend access for material violations of this section, with notice and an opportunity to cure where reasonable.

## 5. Customer Data, Recordings, and Consent Responsibility

"Customer Data" means call recordings, transcripts, configurations, and other content you submit to the Service, and all analysis derived from it. As between the parties, you own all Customer Data. You grant us a limited, non-exclusive license to host and process Customer Data solely to provide and support the Service in accordance with our Privacy Policy and any executed Data Processing Agreement.

**You are solely responsible for the lawful collection of call recordings.** This includes obtaining all consents and providing all notices required by applicable call-recording, wiretapping, biometric, privacy, and labor laws in every jurisdiction where calls are recorded or participants are located — for both your personnel and your end customers. You represent and warrant that all Customer Data submitted to the Service was collected lawfully and that you have the right to have it processed by Homayar. You will indemnify us against third-party claims arising from your breach of this section.

We will not use Customer Data to train generalized AI models without your explicit written opt-in. Upon termination, you may export Customer Data for 30 days, after which we will delete it in accordance with our retention practices.

## 6. Intellectual Property

We and our licensors own all right, title, and interest in the Service, including software, models, interfaces, documentation, and all improvements. No rights are granted except as expressly stated in these Terms. You own your Customer Data and the reports and outputs generated from it for your internal business use. If you provide feedback or suggestions, we may use them without restriction or obligation, provided we do not identify you as the source without consent. "Homayar," the Homayar logo, and associated marks are trademarks of KSF Company.

## 7. Confidentiality

Each party may receive non-public information of the other that is marked confidential or that a reasonable person would understand to be confidential ("Confidential Information"). The receiving party will use Confidential Information only to perform under these Terms, protect it with at least reasonable care, and not disclose it except to personnel and advisors bound by comparable obligations. Customer Data is your Confidential Information; non-public Service features and pricing are ours. These obligations survive for three years after termination (indefinitely for trade secrets) and do not apply to information that is independently developed, publicly available without breach, rightfully received from a third party, or required to be disclosed by law (with notice to the other party where permitted).

## 8. Warranties and Disclaimer

We warrant that the Service will perform materially in accordance with its documentation under normal use. Your exclusive remedy for breach of this warranty is repair, re-performance, or — if we cannot cure within 30 days — termination and a pro-rata refund of prepaid, unused fees.

EXCEPT AS EXPRESSLY STATED ABOVE, THE SERVICE IS PROVIDED "AS IS" AND "AS AVAILABLE." WE DISCLAIM ALL OTHER WARRANTIES, EXPRESS OR IMPLIED, INCLUDING MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, AND NON-INFRINGEMENT. WE DO NOT WARRANT THAT TRANSCRIPTS, SCORES, OR AI-GENERATED ANALYSIS WILL BE ERROR-FREE, COMPLETE, OR SUITABLE FOR ANY PARTICULAR DECISION, OR THAT THE SERVICE WILL BE UNINTERRUPTED.

## 9. Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, NEITHER PARTY WILL BE LIABLE FOR ANY INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR PUNITIVE DAMAGES, OR FOR LOST PROFITS, REVENUE, OR DATA, EVEN IF ADVISED OF THE POSSIBILITY. EACH PARTY'S TOTAL AGGREGATE LIABILITY ARISING OUT OF OR RELATING TO THESE TERMS IS LIMITED TO THE FEES PAID OR PAYABLE BY CUSTOMER IN THE TWELVE (12) MONTHS PRECEDING THE EVENT GIVING RISE TO THE CLAIM. THESE LIMITS DO NOT APPLY TO CUSTOMER'S INDEMNIFICATION OBLIGATIONS UNDER SECTION 5, EITHER PARTY'S BREACH OF SECTION 7, OR LIABILITY THAT CANNOT BE LIMITED UNDER APPLICABLE LAW.

## 10. Termination

Either party may terminate these Terms (a) for material breach not cured within 30 days of written notice, or (b) if the other party becomes insolvent or subject to bankruptcy proceedings. You may cancel a subscription at any time, effective at the end of the current billing period. Upon termination, your access ceases, outstanding fees become due, and Customer Data is handled as described in Section 5. Sections 5 through 11 survive termination.

## 11. Governing Law and General Terms

These Terms are governed by the laws of the jurisdiction specified in your order form or, if none is specified, the laws of the jurisdiction in which KSF Company is established, without regard to conflict-of-law rules. Disputes will be resolved in the courts of that jurisdiction, and each party consents to their jurisdiction and venue. Enterprise customers may negotiate alternative governing-law and dispute-resolution terms in a signed order form, which will prevail over this section.

These Terms, together with the Privacy Policy, any DPA, and applicable order forms, constitute the entire agreement and supersede prior discussions. Neither party may assign these Terms without consent, except to a successor in a merger or sale of substantially all assets. Failure to enforce a provision is not a waiver; if any provision is unenforceable, the remainder stays in effect. Neither party is liable for delays caused by events beyond its reasonable control. We may update these Terms with at least 30 days' notice for material changes; continued use after the effective date constitutes acceptance.

## 12. Contact

KSF Company — operator of Homayar
Email: info@ksf.ir
`;

const fa = `# شرایط استفاده از خدمات

_آخرین به‌روزرسانی: ۱۲ ژوئن ۲۰۲۶_

این شرایط استفاده از خدمات («شرایط») بر دسترسی به پلتفرم و وب‌سایت همایار («خدمات») و استفاده از آن‌ها حاکم است؛ خدماتی که توسط شرکت «کهن سیستم فردا» («همایار»، «ما» یا «مال ما») ارائه می‌شود. با ایجاد حساب کاربری، امضای فرم سفارشی که به این شرایط ارجاع می‌دهد، یا استفاده از خدمات، شما («مشتری»، «شما») می‌پذیرید که به این شرایط متعهد باشید. اگر این شرایط را از طرف یک سازمان می‌پذیرید، تأیید می‌کنید که اختیار متعهد کردن آن سازمان را دارید.

## ۱. خدمات

همایار یک پلتفرم عامل‌محور تحلیل صدای مبتنی بر هوش مصنوعی برای مراکز تماس و تیم‌های خدمات مشتری سازمانی است. این خدمات، تماس‌های ضبط‌شده یا بارگذاری‌شده مشتریان را دریافت کرده و رونوشت‌های گفتار به متن، امتیازهای کنترل کیفیت مبتنی بر هوش مصنوعی، شناسایی مسائل، بینش‌های کسب‌وکار، توصیه‌های مربی‌گری و داشبوردهای گزارش‌دهی تولید می‌کند. این خدمات، پشتیبانی تصمیم‌گیری فراهم می‌آورد: امتیازها، دسته‌بندی‌ها و توصیه‌های تولیدشده توسط هوش مصنوعی، ابزارهای کمکی تحلیلی هستند، نه تصمیم‌های نهایی. مشتری همچنان مسئول تمامی تصمیم‌های مربوط به اشتغال، انطباق و کسب‌وکار است که با استفاده از خروجی‌های خدمات اتخاذ می‌شوند و باید از بازبینی انسانی واجد شرایط برای هر خروجی که در تصمیم‌های پیامددار به کار می‌رود اطمینان حاصل کند. ما ممکن است خدمات را بهبود دهیم، تغییر دهیم یا به‌روزرسانی کنیم، مشروط بر آنکه این تغییرات کارکرد اصلی را در طول دوره اشتراک پرداختی به‌طور قابل‌توجه تنزل ندهد.

## ۲. حساب‌های کاربری

شما باید اطلاعات ثبت‌نام دقیق و کامل ارائه دهید و آن‌ها را به‌روز نگه دارید. شما مسئول حفظ محرمانگی اطلاعات ورود و تمامی فعالیت‌های انجام‌شده تحت حساب خود هستید، از جمله فعالیت کاربران مجاز شما (کارکنان، پیمانکاران و عاملانی که اجازه استفاده از خدمات را به آن‌ها می‌دهید). شما هرگونه دسترسی غیرمجاز مشکوک را بی‌درنگ از طریق info@ksf.ir به ما اطلاع خواهید داد. ما ممکن است حساب‌هایی را که برای خدمات یا سایر مشتریان ریسک امنیتی ایجاد می‌کنند، در صورت امکان همراه با اطلاع‌رسانی، معلق کنیم.

## ۳. اشتراک‌ها و صورت‌حساب

خدمات بر مبنای اشتراک و بر اساس طرح، محدودیت‌های مصرف (مانند ساعت‌های تماس تحلیل‌شده یا تعداد صندلی اپراتور) و هزینه‌های مشخص‌شده هنگام خرید یا در فرم سفارش ارائه می‌شود. پرداخت‌ها توسط **Stripe** پردازش می‌شوند؛ با ثبت اشتراک، شما کسر هزینه‌های دوره‌ای از روش پرداخت تعیین‌شده خود را مجاز دانسته و با شرایط مربوط به Stripe موافقت می‌کنید. مگر آنکه در فرم سفارش به‌گونه دیگری ذکر شده باشد:

- اشتراک‌ها در پایان هر دوره صورت‌حساب (ماهانه یا سالانه) به‌صورت خودکار تمدید می‌شوند، مگر آنکه پیش از تمدید لغو شوند.
- هزینه‌ها به دلار آمریکا (USD) بیان می‌شوند، شامل مالیات نیستند و جز در مواردی که قانون الزام کند یا در این شرایط صراحتاً ذکر شده باشد، غیرقابل‌استرداد هستند.
- مصرف بیش از محدودیت‌های طرح ممکن است مشمول هزینه‌های اضافی با نرخ‌های منتشرشده برای طرح شما شود.
- ما ممکن است قیمت‌گذاری را با اطلاع‌رسانی حداقل ۳۰ روز پیش‌تر تغییر دهیم، که از تمدید بعدی شما اعمال می‌شود.
- مبالغ معوق ممکن است پس از ۱۰ روز اطلاع‌رسانی کتبی به تعلیق دسترسی منجر شوند و ممکن است مشمول بهره‌ای معادل کمترین مقدار بین ۱٫۵٪ در ماه یا حداکثر نرخ قانونی شوند.

لغو در پایان دوره صورت‌حساب جاری اعمال می‌شود؛ شما تا آن زمان دسترسی خود را حفظ می‌کنید.

## ۴. استفاده مجاز

شما این موارد را انجام نخواهید داد و به هیچ کاربری اجازه انجام آن‌ها را نخواهید داد:

- بارگذاری تماس‌ها یا محتوایی که حق قانونی ضبط، نگهداری یا پردازش آن را ندارید
- استفاده از خدمات برای نقض هر قانونی، از جمله قوانین شنود، رضایت برای ضبط، حریم خصوصی یا اشتغال
- تلاش برای کاوش، نفوذ یا دور زدن تدابیر امنیتی یا احراز هویت
- مهندسی معکوس، واکافت (Decompile) یا استخراج مدل‌ها، دستورها (Prompt) یا کد منبع زیربنایی خدمات
- استفاده از خدمات برای ساخت یا آموزش یک محصول رقیب
- فروش مجدد، واگذاری مجوز یا ارائه خدمات به اشخاص ثالث به‌جز کاربران مجاز
- ارسال کد مخرب یا استفاده از خدمات به‌شیوه‌ای که آن را برای سایر مشتریان تنزل دهد
- استفاده از خروجی‌ها برای اتخاذ تصمیم‌های کاملاً خودکار با اثرات قانونی یا اثرات مشابهِ قابل‌توجه بر افراد، بدون بازبینی انسانی معنادار

ما ممکن است در صورت نقض اساسی این بخش، در صورت معقول بودن همراه با اطلاع‌رسانی و فرصت اصلاح، دسترسی را معلق کنیم.

## ۵. داده‌های مشتری، فایل‌های ضبط‌شده و مسئولیت کسب رضایت

«داده‌های مشتری» به معنای فایل‌های ضبط‌شده تماس، رونوشت‌ها، پیکربندی‌ها و سایر محتوایی است که شما به خدمات ارسال می‌کنید و تمامی تحلیل‌های به‌دست‌آمده از آن. در رابطه میان طرفین، تمامی داده‌های مشتری متعلق به شماست. شما به ما مجوزی محدود و غیرانحصاری اعطا می‌کنید تا داده‌های مشتری را صرفاً برای ارائه و پشتیبانی خدمات، مطابق با سیاست حفظ حریم خصوصی ما و هر قرارداد پردازش داده منعقدشده، میزبانی و پردازش کنیم.

**شما به‌تنهایی مسئول جمع‌آوری قانونی فایل‌های ضبط‌شده تماس هستید.** این مسئولیت شامل کسب تمامی رضایت‌ها و ارائه تمامی اطلاع‌رسانی‌های لازم بر اساس قوانین قابل‌اجرای مربوط به ضبط تماس، شنود، داده‌های زیستی (Biometric)، حریم خصوصی و کار، در هر حوزه قضایی است که در آن تماس‌ها ضبط می‌شوند یا مشارکت‌کنندگان حضور دارند — هم برای کارکنان شما و هم برای مشتریان نهایی شما. شما اظهار و تضمین می‌کنید که تمامی داده‌های مشتری ارسال‌شده به خدمات به‌صورت قانونی جمع‌آوری شده و حق پردازش آن توسط همایار را دارید. شما ما را در برابر ادعاهای اشخاص ثالث ناشی از نقض این بخش جبران خسارت خواهید کرد.

ما بدون انتخاب صریح و کتبی شما (Opt-in)، از داده‌های مشتری برای آموزش مدل‌های عمومی هوش مصنوعی استفاده نخواهیم کرد. پس از خاتمه، شما می‌توانید تا ۳۰ روز داده‌های مشتری را خروجی بگیرید، پس از آن ما آن‌ها را مطابق با رویه‌های نگهداری داده خود حذف خواهیم کرد.

## ۶. مالکیت فکری

ما و اعطاکنندگان مجوز ما، مالک تمامی حقوق، عنوان و منافع مربوط به خدمات هستیم، از جمله نرم‌افزار، مدل‌ها، رابط‌ها، مستندات و تمامی بهبودها. هیچ حقی جز آنچه در این شرایط صراحتاً ذکر شده، اعطا نمی‌شود. شما مالک داده‌های مشتری خود و گزارش‌ها و خروجی‌های تولیدشده از آن برای استفاده داخلی کسب‌وکار خود هستید. اگر بازخورد یا پیشنهادی ارائه دهید، ما ممکن است بدون محدودیت یا تعهد از آن‌ها استفاده کنیم، مشروط بر آنکه بدون رضایت شما، شما را به‌عنوان منبع معرفی نکنیم. «همایار»، نشان همایار و علائم مرتبط، علائم تجاری شرکت «کهن سیستم فردا» هستند.

## ۷. محرمانگی

هر طرف ممکن است اطلاعات غیرعمومی طرف دیگر را دریافت کند که محرمانه علامت‌گذاری شده یا یک فرد متعارف آن را محرمانه تلقی می‌کند («اطلاعات محرمانه»). طرف دریافت‌کننده از اطلاعات محرمانه تنها برای اجرای این شرایط استفاده خواهد کرد، از آن دست‌کم با مراقبت معقول محافظت می‌کند و آن را جز برای کارکنان و مشاورانِ متعهد به تعهدات مشابه فاش نمی‌کند. داده‌های مشتری، اطلاعات محرمانه شماست؛ ویژگی‌ها و قیمت‌گذاری غیرعمومی خدمات، اطلاعات محرمانه ماست. این تعهدات تا سه سال پس از خاتمه (و برای اسرار تجاری به‌صورت نامحدود) باقی می‌مانند و در مورد اطلاعاتی که به‌طور مستقل توسعه یافته، بدون نقض به‌صورت عمومی در دسترس بوده، به‌درستی از شخص ثالث دریافت شده، یا افشای آن طبق قانون الزامی است (با اطلاع‌رسانی به طرف دیگر در صورت مجاز بودن) اعمال نمی‌شوند.

## ۸. تضمین‌ها و سلب مسئولیت

ما تضمین می‌کنیم که خدمات تحت استفاده عادی، به‌طور اساسی مطابق با مستندات خود عمل خواهد کرد. تنها راهکار جبرانی شما برای نقض این تضمین، تعمیر، اجرای مجدد، یا — در صورتی که نتوانیم ظرف ۳۰ روز آن را برطرف کنیم — خاتمه و بازپرداخت نسبی هزینه‌های پیش‌پرداخت‌شده و استفاده‌نشده است.

جز در مواردی که در بالا صراحتاً ذکر شده، خدمات «همان‌گونه که هست» و «بر اساس در دسترس بودن» ارائه می‌شود. ما تمامی تضمین‌های دیگر، چه صریح و چه ضمنی، از جمله قابلیت فروش، تناسب برای یک هدف خاص و عدم نقض حقوق را سلب می‌کنیم. ما تضمین نمی‌کنیم که رونوشت‌ها، امتیازها یا تحلیل تولیدشده توسط هوش مصنوعی بدون خطا، کامل یا مناسب برای هر تصمیم خاصی باشند، یا اینکه خدمات بدون وقفه ارائه شود.

## ۹. محدودیت مسئولیت

تا حداکثر حدی که قانون اجازه می‌دهد، هیچ‌یک از طرفین مسئول هیچ خسارت غیرمستقیم، تبعی، خاص، پیامدی یا تنبیهی، یا برای سود، درآمد یا داده‌های ازدست‌رفته نخواهد بود، حتی اگر از احتمال آن آگاه شده باشد. مسئولیت کل و مجموع هر طرف ناشی از یا مرتبط با این شرایط، به هزینه‌های پرداخت‌شده یا قابل‌پرداخت توسط مشتری در دوازده (۱۲) ماه پیش از رویداد ایجادکننده ادعا محدود می‌شود. این محدودیت‌ها در مورد تعهدات جبران خسارت مشتری بر اساس بخش ۵، نقض بخش ۷ توسط هر طرف، یا مسئولیتی که بر اساس قانون قابل‌اجرا قابل‌محدود کردن نیست، اعمال نمی‌شوند.

## ۱۰. خاتمه

هر طرف می‌تواند این شرایط را (الف) به دلیل نقض اساسی که ظرف ۳۰ روز از اطلاع‌رسانی کتبی برطرف نشده، یا (ب) در صورتی که طرف دیگر دچار اعسار یا مشمول دادرسی ورشکستگی شود، خاتمه دهد. شما می‌توانید هر زمان اشتراک را لغو کنید، که در پایان دوره صورت‌حساب جاری اعمال می‌شود. پس از خاتمه، دسترسی شما متوقف می‌شود، هزینه‌های معوق قابل‌پرداخت می‌گردند و داده‌های مشتری همان‌گونه که در بخش ۵ شرح داده شده مدیریت می‌شوند. بخش‌های ۵ تا ۱۱ پس از خاتمه باقی می‌مانند.

## ۱۱. قانون حاکم و شرایط عمومی

این شرایط تابع قوانین حوزه قضایی مشخص‌شده در فرم سفارش شما یا، در صورت عدم تعیین، قوانین حوزه قضایی محل تأسیس شرکت «کهن سیستم فردا» است، بدون توجه به قواعد تعارض قوانین. اختلافات در دادگاه‌های همان حوزه قضایی حل‌وفصل خواهند شد و هر طرف به صلاحیت و محل رسیدگی آن‌ها رضایت می‌دهد. مشتریان سازمانی می‌توانند شرایط جایگزین قانون حاکم و حل اختلاف را در یک فرم سفارش امضاشده مذاکره کنند، که بر این بخش غلبه خواهد داشت.

این شرایط، به همراه سیاست حفظ حریم خصوصی، هر قرارداد پردازش داده (DPA) و فرم‌های سفارش قابل‌اجرا، کل توافق را تشکیل می‌دهند و جایگزین مذاکرات پیشین می‌شوند. هیچ‌یک از طرفین نمی‌تواند بدون رضایت این شرایط را واگذار کند، مگر به یک جانشین در ادغام یا فروش تقریباً تمام دارایی‌ها. عدم اجرای یک مفاد به‌منزله صرف‌نظر از آن نیست؛ اگر هر مفادی غیرقابل‌اجرا باشد، بقیه به قوت خود باقی می‌مانند. هیچ‌یک از طرفین مسئول تأخیرهای ناشی از رویدادهای خارج از کنترل معقول خود نیست. ما ممکن است این شرایط را برای تغییرات اساسی با اطلاع‌رسانی حداقل ۳۰ روز پیش‌تر به‌روزرسانی کنیم؛ ادامه استفاده پس از تاریخ اجرا به‌منزله پذیرش است.

## ۱۲. تماس

شرکت کهن سیستم فردا — ارائه‌دهنده همایار
رایانامه: info@ksf.ir
`;

const DICT: Record<Locale, string> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

export function TermsContent() {
  const { locale } = useLocale();
  const md = DICT[locale] ?? en;
  const html = renderMarkdown(md);

  return (
    <section className="container-site pt-16 pb-24 sm:pt-20">
      <div className="mx-auto max-w-3xl">
        <div
          className="prose-dark"
          dangerouslySetInnerHTML={{ __html: html }}
        />
      </div>
    </section>
  );
}
