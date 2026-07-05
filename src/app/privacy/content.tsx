"use client";

import { renderMarkdown } from "@/lib/markdown";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware Privacy Policy. English is the canonical source; `fa` is a
 * faithful, complete Persian translation; other locales fall back to English.
 * The body is authored as first-party markdown and rendered to HTML the same
 * way the original server page did (renderMarkdown + .prose-dark), so the markup
 * is identical — only the source text is externalized per locale.
 */
const en = `# Privacy Policy

_Last updated: June 12, 2026_

This Privacy Policy explains how KSF Company ("Homayar," "we," "us," or "our") collects, uses, and protects personal data in connection with the Homayar platform — an agentic AI voice analysis service for call centers and enterprise customer-service teams — and our website at homayar.com (together, the "Service").

Homayar acts in two distinct roles. For account information, billing details, and website analytics, we are the **data controller**. For call recordings, transcripts, and any personal data contained within them, we act as a **data processor** on behalf of our enterprise customers, who remain the controllers of that data. Where this policy refers to "Customer Content," it means call recordings, transcripts, and derived analysis that our customers submit to the Service.

## 1. Data We Collect

**Account and contact data (controller).** When you create an account, request a demo, or contact us, we collect your name, business email address, company name, job title, and authentication credentials. If you subscribe to a paid plan, our payment provider collects billing details on our behalf; we do not store full payment card numbers.

**Call recordings and transcripts (processor).** Our customers upload or connect recorded customer-service calls for analysis. These recordings may contain the voices, names, and other personal data of call participants — both agents and end customers. We process this Customer Content solely on documented instructions from our customer, as set out in our Terms of Service and applicable Data Processing Agreement (DPA). We do not determine the purposes of this processing, and we never use Customer Content for advertising or sell it to third parties.

**Derived analysis data (processor).** The Service generates speech-to-text transcripts, quality-control scores, detected issues, sentiment indicators, coaching recommendations, and aggregated reporting metrics from Customer Content. This derived data is treated with the same protections as the underlying recordings.

**Usage and technical data (controller).** We collect log data (IP address, browser type, pages viewed, timestamps), device identifiers, and product usage events (features used, reports generated) to operate, secure, and improve the Service.

## 2. How We Use Data

We use account and usage data to:

- Provide, maintain, and secure the Service, including authentication and access control
- Process subscriptions, invoices, and payments
- Respond to support requests and communicate service updates
- Monitor performance, diagnose issues, and improve reliability
- Comply with legal obligations and enforce our Terms of Service

We use Customer Content only to deliver the analysis features our customers have purchased: transcription, AI agent analysis, quality scoring, issue detection, insight generation, coaching recommendations, and reporting. We do not use Customer Content to train generalized AI models unless the customer has given explicit, written opt-in consent.

## 3. AI Processing Disclosure

Homayar uses automated AI systems as a core part of the Service. Specifically:

- **Speech-to-text models** convert call recordings into transcripts.
- **Specialized AI agents** analyze transcripts for quality criteria, compliance signals, customer issues, sentiment, and coaching opportunities.
- **Scoring models** produce structured quality-control scores against rubrics configured by the customer.

These outputs are decision-support tools. Homayar does not make autonomous decisions with legal or similarly significant effects on individuals; evaluations of agent performance, employment decisions, and customer outcomes remain the responsibility of our customers and their human reviewers. AI-generated scores and recommendations may contain errors and should be reviewed by qualified personnel before being relied upon. Customers are responsible for informing their employees and end customers about AI-assisted call analysis where required by applicable law.

## 4. Data Retention

- **Customer Content:** retained according to the retention period configured by the customer (default 12 months), after which recordings and transcripts are automatically deleted. Customers may delete individual recordings or full datasets at any time through the platform or by written request.
- **Account data:** retained for the life of the account and deleted or anonymized within 90 days of account closure, except where retention is required by law.
- **Billing records:** retained as required by tax and accounting regulations, typically 7 years.
- **Backups:** encrypted backups are purged on a rolling 35-day cycle; deleted Customer Content is removed from backups within that window.

## 5. Subprocessors

We use a limited set of vetted subprocessors to deliver the Service, including cloud infrastructure providers (hosting and storage), speech-to-text and AI inference providers, Stripe (payment processing), and email delivery services. All subprocessors are bound by written agreements imposing data protection obligations no less protective than those in this policy and our DPA. A current list of subprocessors is available on request at info@ksf.ir. We will provide enterprise customers with advance notice of any new subprocessor and an opportunity to object.

## 6. Security

We maintain administrative, technical, and physical safeguards designed to protect personal data, including:

- Encryption in transit (TLS 1.2+) and at rest (AES-256) for all recordings, transcripts, and account data
- Role-based access controls and the principle of least privilege for internal access
- Logical tenant isolation between customer environments
- Audit logging of access to Customer Content
- Regular vulnerability assessments and third-party penetration testing
- Employee confidentiality agreements and security training

No system is perfectly secure. If we become aware of a personal data breach affecting Customer Content, we will notify the affected customer without undue delay and provide information reasonably required for their own notification obligations.

## 7. International Transfers

Our infrastructure is hosted in regions agreed with each enterprise customer. Where personal data is transferred across borders — including to subprocessors — we rely on appropriate safeguards such as the European Commission's Standard Contractual Clauses or equivalent mechanisms recognized under applicable law. Enterprise customers with data residency requirements may request regional hosting options as part of their deployment agreement.

## 8. Your Rights

Depending on your jurisdiction, you may have the right to access, correct, delete, or export your personal data, to restrict or object to certain processing, and to withdraw consent where processing is based on consent. You may also have the right to lodge a complaint with your local data protection authority.

**If you are an account holder**, contact us directly at info@ksf.ir and we will respond within 30 days.

**If you are an end customer or agent whose voice appears in a recording**, the Homayar customer that recorded the call is the data controller. Please direct your request to that organization; we will assist them in fulfilling verified requests, including locating and deleting specific recordings and transcripts.

## 9. Changes to This Policy

We may update this Privacy Policy from time to time. Material changes will be announced via the Service or by email at least 30 days before they take effect. The "Last updated" date at the top of this page reflects the current version.

## 10. Contact

KSF Company — operator of Homayar
Email: info@ksf.ir

For privacy inquiries, data subject requests, DPA execution, or the current subprocessor list, write to info@ksf.ir with the subject line "Privacy Request."
`;

const fa = `# سیاست حفظ حریم خصوصی

_آخرین به‌روزرسانی: ۱۲ ژوئن ۲۰۲۶_

این سیاست حفظ حریم خصوصی توضیح می‌دهد که شرکت «کهن سیستم فردا» («همایار»، «ما» یا «مال ما») چگونه داده‌های شخصی را در ارتباط با پلتفرم همایار — یک سرویس عامل‌محور تحلیل صدای مبتنی بر هوش مصنوعی برای مراکز تماس و تیم‌های خدمات مشتری سازمانی — و وب‌سایت ما در homayar.com (در مجموع، «خدمات») جمع‌آوری، استفاده و محافظت می‌کند.

همایار در دو نقش متمایز عمل می‌کند. برای اطلاعات حساب، جزئیات صورت‌حساب و تحلیل‌های وب‌سایت، ما **کنترل‌کننده داده** هستیم. برای فایل‌های ضبط‌شده تماس، رونوشت‌ها و هر داده شخصی موجود در آن‌ها، ما به‌عنوان **پردازنده داده** از طرف مشتریان سازمانی خود عمل می‌کنیم که خود کنترل‌کننده آن داده‌ها باقی می‌مانند. هرجا این سیاست به «محتوای مشتری» اشاره می‌کند، منظور فایل‌های ضبط‌شده تماس، رونوشت‌ها و تحلیل‌های مشتق‌شده است که مشتریان ما به خدمات ارسال می‌کنند.

## ۱. داده‌هایی که جمع‌آوری می‌کنیم

**داده‌های حساب و تماس (کنترل‌کننده).** هنگامی که حساب کاربری ایجاد می‌کنید، درخواست دمو می‌دهید یا با ما تماس می‌گیرید، ما نام، نشانی رایانامه کاری، نام شرکت، عنوان شغلی و اطلاعات احراز هویت شما را جمع‌آوری می‌کنیم. اگر در یک طرح پرداختی مشترک شوید، ارائه‌دهنده پرداخت ما جزئیات صورت‌حساب را از طرف ما جمع‌آوری می‌کند؛ ما شماره کامل کارت پرداخت را ذخیره نمی‌کنیم.

**فایل‌های ضبط‌شده تماس و رونوشت‌ها (پردازنده).** مشتریان ما تماس‌های ضبط‌شده خدمات مشتری را برای تحلیل بارگذاری یا متصل می‌کنند. این فایل‌های ضبط‌شده ممکن است شامل صدا، نام و سایر داده‌های شخصی مشارکت‌کنندگان تماس — هم اپراتورها و هم مشتریان نهایی — باشند. ما این محتوای مشتری را صرفاً بر اساس دستورهای مستند مشتری خود، همان‌گونه که در شرایط استفاده از خدمات و قرارداد پردازش داده (DPA) قابل‌اجرا تعیین شده، پردازش می‌کنیم. ما اهداف این پردازش را تعیین نمی‌کنیم و هرگز از محتوای مشتری برای تبلیغات استفاده نکرده یا آن را به اشخاص ثالث نمی‌فروشیم.

**داده‌های تحلیل مشتق‌شده (پردازنده).** خدمات از محتوای مشتری، رونوشت‌های گفتار به متن، امتیازهای کنترل کیفیت، مسائل شناسایی‌شده، شاخص‌های احساسات، توصیه‌های مربی‌گری و سنجه‌های گزارش‌دهی تجمیعی تولید می‌کند. این داده‌های مشتق‌شده با همان حفاظت‌هایی که برای فایل‌های ضبط‌شده زیربنایی اعمال می‌شود، رفتار می‌شوند.

**داده‌های مصرف و فنی (کنترل‌کننده).** ما داده‌های گزارش (نشانی IP، نوع مرورگر، صفحات مشاهده‌شده، مُهرهای زمانی)، شناسه‌های دستگاه و رویدادهای مصرف محصول (ویژگی‌های استفاده‌شده، گزارش‌های تولیدشده) را برای راهبری، ایمن‌سازی و بهبود خدمات جمع‌آوری می‌کنیم.

## ۲. چگونه از داده‌ها استفاده می‌کنیم

ما از داده‌های حساب و مصرف برای این موارد استفاده می‌کنیم:

- ارائه، نگهداری و ایمن‌سازی خدمات، از جمله احراز هویت و کنترل دسترسی
- پردازش اشتراک‌ها، صورت‌حساب‌ها و پرداخت‌ها
- پاسخ به درخواست‌های پشتیبانی و اطلاع‌رسانی به‌روزرسانی‌های خدمات
- پایش عملکرد، تشخیص مسائل و بهبود قابلیت اطمینان
- رعایت تعهدات قانونی و اجرای شرایط استفاده از خدمات ما

ما از محتوای مشتری تنها برای ارائه ویژگی‌های تحلیلی که مشتریان ما خریداری کرده‌اند استفاده می‌کنیم: رونویسی، تحلیل عامل‌های هوشمند، امتیازدهی کیفیت، شناسایی مسائل، تولید بینش، توصیه‌های مربی‌گری و گزارش‌دهی. ما از محتوای مشتری برای آموزش مدل‌های عمومی هوش مصنوعی استفاده نمی‌کنیم، مگر آنکه مشتری رضایت صریح و کتبی (Opt-in) داده باشد.

## ۳. افشای پردازش هوش مصنوعی

همایار از سامانه‌های خودکار هوش مصنوعی به‌عنوان بخش اصلی خدمات استفاده می‌کند. به‌طور مشخص:

- **مدل‌های گفتار به متن** فایل‌های ضبط‌شده تماس را به رونوشت تبدیل می‌کنند.
- **عامل‌های هوشمند تخصصی** رونوشت‌ها را از نظر معیارهای کیفیت، سیگنال‌های انطباق، مسائل مشتری، احساسات و فرصت‌های مربی‌گری تحلیل می‌کنند.
- **مدل‌های امتیازدهی** امتیازهای کنترل کیفیت ساختاریافته را بر اساس معیارهای پیکربندی‌شده توسط مشتری تولید می‌کنند.

این خروجی‌ها ابزارهای پشتیبانی تصمیم‌گیری هستند. همایار تصمیم‌های خودمختار با اثرات قانونی یا اثرات مشابهِ قابل‌توجه بر افراد اتخاذ نمی‌کند؛ ارزیابی عملکرد اپراتور، تصمیم‌های اشتغال و نتایج مربوط به مشتری همچنان بر عهده مشتریان ما و بازبینان انسانی آن‌هاست. امتیازها و توصیه‌های تولیدشده توسط هوش مصنوعی ممکن است شامل خطا باشند و باید پیش از استناد، توسط کارکنان واجد شرایط بازبینی شوند. مشتریان مسئول آگاه‌سازی کارکنان و مشتریان نهایی خود درباره تحلیل تماس به‌کمک هوش مصنوعی، در مواردی که قانون قابل‌اجرا الزام کند، هستند.

## ۴. نگهداری داده

- **محتوای مشتری:** مطابق با دوره نگهداری پیکربندی‌شده توسط مشتری (به‌صورت پیش‌فرض ۱۲ ماه) نگهداری می‌شود، پس از آن فایل‌های ضبط‌شده و رونوشت‌ها به‌صورت خودکار حذف می‌شوند. مشتریان می‌توانند هر زمان از طریق پلتفرم یا با درخواست کتبی، فایل‌های ضبط‌شده منفرد یا کل مجموعه‌داده‌ها را حذف کنند.
- **داده‌های حساب:** در طول عمر حساب نگهداری شده و ظرف ۹۰ روز پس از بستن حساب حذف یا ناشناس‌سازی می‌شوند، جز در مواردی که نگهداری طبق قانون الزامی است.
- **سوابق صورت‌حساب:** مطابق با مقررات مالیاتی و حسابداری، معمولاً به مدت ۷ سال نگهداری می‌شوند.
- **پشتیبان‌ها:** پشتیبان‌های رمزنگاری‌شده در یک چرخه ۳۵ روزه پاک‌سازی می‌شوند؛ محتوای مشتری حذف‌شده در همان بازه از پشتیبان‌ها برداشته می‌شود.

## ۵. پردازنده‌های فرعی

ما برای ارائه خدمات از مجموعه‌ای محدود از پردازنده‌های فرعی بررسی‌شده استفاده می‌کنیم، از جمله ارائه‌دهندگان زیرساخت ابری (میزبانی و ذخیره‌سازی)، ارائه‌دهندگان گفتار به متن و استنتاج هوش مصنوعی، Stripe (پردازش پرداخت) و سرویس‌های تحویل رایانامه. تمامی پردازنده‌های فرعی مقید به قراردادهای کتبی هستند که تعهدات حفاظت از داده‌ای را اعمال می‌کنند که دست‌کم به اندازه این سیاست و قرارداد پردازش داده ما محافظت‌کننده باشند. فهرست به‌روز پردازنده‌های فرعی با درخواست از طریق info@ksf.ir در دسترس است. ما به مشتریان سازمانی درباره هر پردازنده فرعی جدید پیشاپیش اطلاع داده و فرصت اعتراض خواهیم داد.

## ۶. امنیت

ما تدابیر حفاظتی اداری، فنی و فیزیکی را برای محافظت از داده‌های شخصی به کار می‌گیریم، از جمله:

- رمزنگاری در حین انتقال (TLS 1.2+) و در حالت ذخیره (AES-256) برای تمامی فایل‌های ضبط‌شده، رونوشت‌ها و داده‌های حساب
- کنترل‌های دسترسی نقش‌محور و اصل کمترین دسترسی برای دسترسی داخلی
- جداسازی منطقی مستأجر میان محیط‌های مشتریان
- ثبت گزارش حسابرسی دسترسی به محتوای مشتری
- ارزیابی‌های منظم آسیب‌پذیری و آزمون نفوذ توسط شخص ثالث
- توافق‌نامه‌های محرمانگی کارکنان و آموزش امنیت

هیچ سامانه‌ای کاملاً ایمن نیست. اگر از نقض داده شخصیِ مؤثر بر محتوای مشتری آگاه شویم، بدون تأخیر ناروا مشتری متأثر را مطلع کرده و اطلاعات لازم برای تعهدات اطلاع‌رسانی خود او را به‌طور معقول فراهم خواهیم کرد.

## ۷. انتقال‌های بین‌المللی

زیرساخت ما در مناطق توافق‌شده با هر مشتری سازمانی میزبانی می‌شود. هرجا داده شخصی از مرزها عبور داده شود — از جمله به پردازنده‌های فرعی — ما به تدابیر حفاظتی مناسب مانند بندهای استاندارد قراردادی کمیسیون اروپا (SCC) یا سازوکارهای معادل به‌رسمیت‌شناخته‌شده تحت قانون قابل‌اجرا تکیه می‌کنیم. مشتریان سازمانی با الزامات محل نگهداری داده می‌توانند به‌عنوان بخشی از قرارداد استقرار خود، گزینه‌های میزبانی منطقه‌ای را درخواست کنند.

## ۸. حقوق شما

بسته به حوزه قضایی شما، ممکن است حق دسترسی، اصلاح، حذف یا خروجی‌گیری داده‌های شخصی خود، محدود کردن یا اعتراض به برخی پردازش‌ها، و بازپس‌گیری رضایت در مواردی که پردازش بر مبنای رضایت است را داشته باشید. همچنین ممکن است حق ثبت شکایت نزد مرجع حفاظت از داده محلی خود را داشته باشید.

**اگر دارنده حساب هستید**، مستقیماً از طریق info@ksf.ir با ما تماس بگیرید و ما ظرف ۳۰ روز پاسخ خواهیم داد.

**اگر مشتری نهایی یا اپراتوری هستید که صدایش در یک فایل ضبط‌شده ظاهر می‌شود**، مشتری همایار که تماس را ضبط کرده، کنترل‌کننده داده است. لطفاً درخواست خود را به آن سازمان ارجاع دهید؛ ما آن‌ها را در برآورده‌سازی درخواست‌های تأییدشده، از جمله یافتن و حذف فایل‌های ضبط‌شده و رونوشت‌های مشخص، یاری خواهیم کرد.

## ۹. تغییرات این سیاست

ما ممکن است این سیاست حفظ حریم خصوصی را گاه‌به‌گاه به‌روزرسانی کنیم. تغییرات اساسی حداقل ۳۰ روز پیش از اجرایی شدن، از طریق خدمات یا با رایانامه اعلام خواهند شد. تاریخ «آخرین به‌روزرسانی» در بالای این صفحه، نسخه جاری را بازتاب می‌دهد.

## ۱۰. تماس

شرکت کهن سیستم فردا — ارائه‌دهنده همایار
رایانامه: info@ksf.ir

برای استعلام‌های حریم خصوصی، درخواست‌های صاحب داده، انعقاد قرارداد پردازش داده (DPA) یا فهرست جاری پردازنده‌های فرعی، با موضوع «درخواست حریم خصوصی» به info@ksf.ir بنویسید.
`;

const DICT: Record<Locale, string> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

export function PrivacyContent() {
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
