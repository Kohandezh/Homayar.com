"use client";

import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ChevronDownIcon } from "@/components/icons";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware copy for the FAQ page. English is the canonical source; `fa` is
 * a direct Persian translation. Other locales fall back to English so the
 * language switcher never shows an empty page. FAQ content is inlined here
 * (mirrored from content/faq-about.json) to avoid refactoring the shared data
 * layer; group order and grouping logic match the original page.
 */
type FaqEntry = { question: string; answer: string; group: string };
type FaqCopy = {
  heroEyebrow: string;
  heroTitle: string;
  heroDescription: string;
  groupOrder: string[];
  groupEyebrows: Record<string, string>;
  faq: FaqEntry[];
  ctaTitle: string;
  ctaBody: string;
  demoCta: string;
  emailLabel: string;
};

const en: FaqCopy = {
  heroEyebrow: "FAQ",
  heroTitle: "Frequently asked questions",
  heroDescription:
    "Straight answers on how Homayar analyzes calls, protects your data, prices by volume, and deploys alongside your stack.",
  groupOrder: [
    "Product",
    "Security & Privacy",
    "Pricing & Billing",
    "Deployment & Integration",
  ],
  groupEyebrows: {
    Product: "Product",
    "Security & Privacy": "Security",
    "Pricing & Billing": "Pricing",
    "Deployment & Integration": "Deployment",
  },
  faq: [
    {
      question: "How does Homayar analyze a call?",
      answer:
        "Every recorded or uploaded call passes through a structured pipeline: speech-to-text transcription with speaker separation, followed by a team of specialized AI agents that each handle one job — quality scoring, issue detection, sentiment tracking, compliance checks, and coaching analysis. Their findings are consolidated into a single call record with a QA score, flagged moments, and recommended actions. A typical 10-minute call is fully analyzed in under 90 seconds.",
      group: "Product",
    },
    {
      question: "Which languages does Homayar support?",
      answer:
        "Homayar supports transcription and analysis in more than 30 languages, including English, Spanish, French, German, Arabic, and Persian, with automatic language detection per call. Mixed-language conversations are handled at the segment level, so a call that switches languages mid-conversation is still scored accurately. Contact us if you need a specific language or dialect validated before rollout — we run an accuracy benchmark on your own sample calls.",
      group: "Product",
    },
    {
      question: "How accurate is the transcription and scoring?",
      answer:
        "On clear telephony audio, transcription word accuracy typically lands between 92% and 97%, depending on language, audio quality, and domain vocabulary. QA scoring is calibrated against your own rubric: during onboarding, your QA team reviews a sample set side by side with Homayar, and we tune agent scoring until alignment reaches an agreed threshold — most teams target 90%+ agreement with human reviewers. You can audit any score back to the exact transcript moments that produced it.",
      group: "Product",
    },
    {
      question: "Can we try Homayar before committing?",
      answer:
        "Yes. Most teams start with a guided pilot: you upload a representative batch of recorded calls, and within a few days you review real scores, detected issues, and coaching insights from your own data — not a canned demo. Pilots run on a standard agreement with full deletion of your audio and transcripts at the end if you decide not to proceed. Book a demo and we will scope the pilot with your QA lead.",
      group: "Product",
    },
    {
      question: "How long do you retain our call data?",
      answer:
        "You set the retention policy. By default, audio is retained for 90 days and transcripts plus analysis results for 13 months, but both windows are configurable per workspace — from delete-after-processing to multi-year retention for regulated industries. Deletion requests are executed across primary storage and backups within 30 days, and every retention setting is logged for audit purposes.",
      group: "Security & Privacy",
    },
    {
      question: "Does Homayar redact personally identifiable information?",
      answer:
        "Yes. Automated PII redaction runs during transcription and can mask names, phone numbers, payment card numbers, national ID numbers, addresses, and dates of birth in both the transcript and the audio waveform. You choose the redaction mode per data type — full removal, tokenized placeholders, or role-restricted visibility — so QA teams see what they need and nothing more.",
      group: "Security & Privacy",
    },
    {
      question: "What is your security posture? Are you SOC 2 certified?",
      answer:
        "Homayar's controls are designed to align with SOC 2 Type II and ISO 27001 frameworks: encryption in transit (TLS 1.2+) and at rest (AES-256), role-based access control, single sign-on via SAML and OIDC, comprehensive audit logging, and annual third-party penetration testing. We do not claim certifications we have not completed — our current attestation status and audit reports are available under NDA through our trust page. Enterprise customers receive a completed security questionnaire and our full controls documentation during procurement.",
      group: "Security & Privacy",
    },
    {
      question:
        "How does Homayar address agent privacy and works-council requirements?",
      answer:
        "Homayar is built as a companion to agents, not a surveillance layer — and the product enforces that distinction technically. Workspaces can disable individual performance rankings, restrict per-agent views to the agent themselves and their direct coach, anonymize team-level analytics, and exclude defined call types from analysis entirely. We provide a works-council documentation pack covering data flows, access roles, and configuration options, and we routinely adapt deployments to codetermination agreements in Germany, Austria, and the Netherlands.",
      group: "Security & Privacy",
    },
    {
      question: "How is Homayar priced?",
      answer:
        "Pricing is based on analyzed call volume — measured in audio hours per month — with plan tiers that bundle seats, retention, and integrations. There is no per-seat charge for agents whose calls are analyzed; only supervisor and admin seats count toward seat limits. Enterprise plans with VPC deployment, custom retention, and dedicated support are quoted annually in USD. Volume commitments reduce the effective per-hour rate.",
      group: "Pricing & Billing",
    },
    {
      question: "Can we change plans mid-contract?",
      answer:
        "Upgrades take effect immediately, with the price difference prorated for the remainder of your billing period. Downgrades take effect at the next renewal so your historical data and configured workflows are never interrupted mid-cycle. If your call volume grows past your tier, overage is billed at your contracted per-hour rate rather than blocking analysis — your pipeline never stops because of a billing threshold.",
      group: "Pricing & Billing",
    },
    {
      question: "What is your refund policy?",
      answer:
        "Monthly plans can be cancelled at any time and are not billed for subsequent months; we do not refund the current month once analysis has run. Annual contracts include a 30-day evaluation window: if Homayar does not meet the success criteria agreed during onboarding, you can exit with a prorated refund of the unused term. Unused committed volume does not expire mid-contract — it rolls forward within the contract year.",
      group: "Pricing & Billing",
    },
    {
      question: "What deployment options are available?",
      answer:
        "Homayar runs as a managed multi-tenant cloud service by default, with data residency options in the US and EU. Enterprise customers can deploy in a single-tenant VPC — in your AWS, Azure, or GCP account — so audio, transcripts, and analysis results never leave your network boundary. Both models run the same analysis pipeline and receive the same release cadence; the VPC option adds your own KMS keys, network controls, and infrastructure audit access.",
      group: "Deployment & Integration",
    },
    {
      question: "Which CRMs and helpdesk tools does Homayar integrate with?",
      answer:
        "Native integrations cover Salesforce, HubSpot, Microsoft Dynamics 365, Zendesk, and Freshdesk. Call scores, detected issues, and coaching notes sync to the matching contact, case, or opportunity record, so supervisors work inside the tools they already use. A REST API and webhooks handle custom CRMs and internal systems — most teams complete a custom integration in one to two sprints with our integration guide.",
      group: "Deployment & Integration",
    },
    {
      question: "How do calls get into Homayar from our telephony system?",
      answer:
        "Three ingestion paths are supported: direct connectors for major platforms including Genesys Cloud, Five9, Amazon Connect, Twilio Flex, Cisco, and Avaya; SFTP or S3 batch upload for existing recording archives; and a streaming API for custom telephony stacks. Recordings arrive with their metadata — agent ID, queue, disposition, timestamps — so analysis is fully attributable from day one. Most connector setups complete in under a week.",
      group: "Deployment & Integration",
    },
  ],
  ctaTitle: "Still have questions?",
  ctaBody:
    "Talk to the team directly — we can walk through your call volumes, security requirements, and rollout plan in a 30-minute session.",
  demoCta: "Request a demo",
  emailLabel: "Email info@ksf.ir",
};

const fa: FaqCopy = {
  heroEyebrow: "پرسش‌های متداول",
  heroTitle: "پرسش‌های پرتکرار",
  heroDescription:
    "پاسخ‌های روشن درباره اینکه همایار چگونه تماس‌ها را تحلیل می‌کند، از داده‌های شما محافظت می‌کند، بر اساس حجم قیمت‌گذاری می‌کند و در کنار سامانه‌های شما مستقر می‌شود.",
  groupOrder: [
    "Product",
    "Security & Privacy",
    "Pricing & Billing",
    "Deployment & Integration",
  ],
  groupEyebrows: {
    Product: "محصول",
    "Security & Privacy": "امنیت",
    "Pricing & Billing": "قیمت‌گذاری",
    "Deployment & Integration": "استقرار",
  },
  faq: [
    {
      question: "همایار چگونه یک تماس را تحلیل می‌کند؟",
      answer:
        "هر تماس ضبط‌شده یا بارگذاری‌شده از یک خط پردازش ساختارمند عبور می‌کند: رونویسی گفتار به متن همراه با تفکیک گوینده، و سپس تیمی از عامل‌های هوشمند تخصصی که هرکدام یک وظیفه را بر عهده دارند — امتیازدهی کیفیت، شناسایی مسائل، ردیابی احساسات، بررسی انطباق با مقررات و تحلیل مربی‌گری. یافته‌های آن‌ها در یک پرونده تماس واحد، همراه با امتیاز کنترل کیفیت، لحظات نشانه‌گذاری‌شده و اقدامات پیشنهادی، گرد هم می‌آیند. یک تماس معمولی ۱۰ دقیقه‌ای در کمتر از ۹۰ ثانیه به‌طور کامل تحلیل می‌شود.",
      group: "Product",
    },
    {
      question: "همایار از چه زبان‌هایی پشتیبانی می‌کند؟",
      answer:
        "همایار از رونویسی و تحلیل در بیش از ۳۰ زبان پشتیبانی می‌کند، از جمله انگلیسی، اسپانیایی، فرانسوی، آلمانی، عربی و فارسی، با تشخیص خودکار زبان برای هر تماس. مکالمه‌های چندزبانه در سطح هر بخش مدیریت می‌شوند، بنابراین تماسی که در میانه مکالمه زبان آن عوض می‌شود همچنان با دقت امتیازدهی می‌شود. اگر پیش از راه‌اندازی به اعتبارسنجی زبان یا گویش خاصی نیاز دارید با ما تماس بگیرید — ما یک سنجش دقت را روی نمونه تماس‌های خودتان اجرا می‌کنیم.",
      group: "Product",
    },
    {
      question: "دقت رونویسی و امتیازدهی چقدر است؟",
      answer:
        "روی صدای تلفنی شفاف، دقت واژگانی رونویسی معمولاً بین ۹۲٪ تا ۹۷٪ قرار می‌گیرد، بسته به زبان، کیفیت صدا و واژگان تخصصی. امتیازدهی کنترل کیفیت بر اساس معیارهای خودِ شما کالیبره می‌شود: در فرایند راه‌اندازی، تیم کنترل کیفیت شما یک مجموعه نمونه را در کنار همایار بررسی می‌کند و ما امتیازدهی عامل‌ها را تنظیم می‌کنیم تا همسویی به آستانه توافق‌شده برسد — بیشتر تیم‌ها بیش از ۹۰٪ توافق با بازبین‌های انسانی را هدف می‌گیرند. می‌توانید هر امتیازی را تا همان لحظات دقیق رونوشت که آن را تولید کرده‌اند ردیابی و حسابرسی کنید.",
      group: "Product",
    },
    {
      question: "آیا می‌توانیم پیش از تعهد، همایار را امتحان کنیم؟",
      answer:
        "بله. بیشتر تیم‌ها با یک پایلوت راهنمایی‌شده شروع می‌کنند: شما دسته‌ای نماینده از تماس‌های ضبط‌شده را بارگذاری می‌کنید و در عرض چند روز، امتیازهای واقعی، مسائل شناسایی‌شده و بینش‌های مربی‌گری را از داده‌های خودتان بررسی می‌کنید — نه یک دموی از پیش آماده. پایلوت‌ها بر اساس یک توافق‌نامه استاندارد اجرا می‌شوند و در صورتی که تصمیم به ادامه نگیرید، صدا و رونوشت‌های شما در پایان به‌طور کامل حذف می‌شوند. یک دمو رزرو کنید تا دامنه پایلوت را با مسئول کنترل کیفیت شما مشخص کنیم.",
      group: "Product",
    },
    {
      question: "داده‌های تماس ما را تا چه مدت نگهداری می‌کنید؟",
      answer:
        "سیاست نگهداری داده را شما تعیین می‌کنید. به‌صورت پیش‌فرض، صدا برای ۹۰ روز و رونوشت‌ها به‌همراه نتایج تحلیل برای ۱۳ ماه نگهداری می‌شوند، اما هر دو بازه برای هر فضای کاری قابل پیکربندی هستند — از حذف پس از پردازش تا نگهداری چندساله برای صنایع تحت مقررات. درخواست‌های حذف، در سراسر فضای ذخیره اصلی و نسخه‌های پشتیبان، ظرف ۳۰ روز اجرا می‌شوند و هر تنظیمِ نگهداری داده برای مقاصد حسابرسی ثبت می‌شود.",
      group: "Security & Privacy",
    },
    {
      question: "آیا همایار داده‌های شخصی قابل شناسایی را حذف می‌کند؟",
      answer:
        "بله. حذف خودکار داده‌های شخصی (PII) در حین رونویسی اجرا می‌شود و می‌تواند نام‌ها، شماره‌های تلفن، شماره کارت‌های پرداخت، شماره‌های شناسایی ملی، نشانی‌ها و تاریخ تولد را هم در رونوشت و هم در شکل موجِ صوتی پنهان کند. شما حالت حذف را برای هر نوع داده انتخاب می‌کنید — حذف کامل، جایگزین‌های نشانه‌گذاری‌شده، یا نمایش محدود به نقش — تا تیم‌های کنترل کیفیت آنچه را نیاز دارند ببینند و نه بیشتر.",
      group: "Security & Privacy",
    },
    {
      question:
        "وضعیت امنیتی شما چگونه است؟ آیا گواهینامه SOC 2 دارید؟",
      answer:
        "کنترل‌های همایار به‌گونه‌ای طراحی شده‌اند که با چارچوب‌های SOC 2 Type II و ISO 27001 همسو باشند: رمزنگاری در حین انتقال (TLS 1.2+) و در حالت ذخیره (AES-256)، کنترل دسترسی نقش‌محور، ورود یکپارچه از طریق SAML و OIDC، گزارش‌گیری حسابرسی جامع و آزمون نفوذ سالانه توسط شخص ثالث. ما گواهینامه‌هایی را که کامل نکرده‌ایم ادعا نمی‌کنیم — وضعیت کنونی تأییدیه‌ها و گزارش‌های حسابرسی ما از طریق صفحه اعتماد و تحت قرارداد عدم افشا (NDA) در دسترس است. مشتریان سازمانی در فرایند تأمین، یک پرسش‌نامه امنیتی تکمیل‌شده و مستندات کامل کنترل‌های ما را دریافت می‌کنند.",
      group: "Security & Privacy",
    },
    {
      question:
        "همایار چگونه به حریم خصوصی اپراتورها و الزامات شورای کارکنان می‌پردازد؟",
      answer:
        "همایار به‌عنوان همراه اپراتورها ساخته شده است، نه یک لایه نظارتی — و محصول این تمایز را به‌صورت فنی اعمال می‌کند. فضاهای کاری می‌توانند رتبه‌بندی عملکرد فردی را غیرفعال کنند، نماهای هر اپراتور را به خودِ او و مربی مستقیمش محدود کنند، تحلیل‌های سطح تیم را ناشناس کنند و انواع تعریف‌شده‌ای از تماس‌ها را به‌طور کامل از تحلیل کنار بگذارند. ما یک بسته مستندات شورای کارکنان شامل جریان‌های داده، نقش‌های دسترسی و گزینه‌های پیکربندی ارائه می‌دهیم و به‌طور معمول استقرارها را با توافق‌نامه‌های مشارکت در تصمیم‌گیری در آلمان، اتریش و هلند تطبیق می‌دهیم.",
      group: "Security & Privacy",
    },
    {
      question: "قیمت‌گذاری همایار چگونه است؟",
      answer:
        "قیمت‌گذاری بر اساس حجم تماس‌های تحلیل‌شده است — که بر حسب ساعت صوتی در ماه اندازه‌گیری می‌شود — با سطوح طرحی که صندلی‌ها، نگهداری داده و یکپارچه‌سازی‌ها را در خود جای می‌دهند. برای اپراتورهایی که تماس‌هایشان تحلیل می‌شود هیچ هزینه‌ای به‌ازای هر صندلی دریافت نمی‌شود؛ تنها صندلی‌های سرپرست و مدیر به محدودیت صندلی شمرده می‌شوند. طرح‌های سازمانی با استقرار VPC، نگهداری داده سفارشی و پشتیبانی اختصاصی، سالانه و به دلار آمریکا قیمت‌گذاری می‌شوند. تعهدهای حجمی، نرخ مؤثر هر ساعت را کاهش می‌دهند.",
      group: "Pricing & Billing",
    },
    {
      question: "آیا می‌توانیم در میانه قرارداد طرح را تغییر دهیم؟",
      answer:
        "ارتقاها بلافاصله اعمال می‌شوند و تفاوت قیمت برای باقی‌مانده دوره صورتحساب شما به‌صورت نسبی محاسبه می‌شود. تنزل طرح در تمدید بعدی اعمال می‌شود تا داده‌های تاریخی و گردش‌کارهای پیکربندی‌شده شما هرگز در میانه دوره مختل نشوند. اگر حجم تماس شما از سطح طرحتان فراتر رود، مازاد با نرخ هر ساعتِ قراردادی شما صورتحساب می‌شود، نه اینکه تحلیل را مسدود کند — خط پردازش شما هرگز به‌خاطر یک آستانه صورتحساب متوقف نمی‌شود.",
      group: "Pricing & Billing",
    },
    {
      question: "سیاست بازپرداخت شما چیست؟",
      answer:
        "طرح‌های ماهانه را می‌توان در هر زمان لغو کرد و برای ماه‌های بعدی صورتحسابی صادر نمی‌شود؛ پس از اجرای تحلیل، مبلغ ماه جاری را بازپرداخت نمی‌کنیم. قراردادهای سالانه شامل یک بازه ارزیابی ۳۰ روزه هستند: اگر همایار معیارهای موفقیت توافق‌شده در فرایند راه‌اندازی را برآورده نکند، می‌توانید با بازپرداخت نسبی دوره استفاده‌نشده خارج شوید. حجم تعهدشده استفاده‌نشده در میانه قرارداد منقضی نمی‌شود — در طول سال قراردادی به جلو منتقل می‌شود.",
      group: "Pricing & Billing",
    },
    {
      question: "چه گزینه‌های استقراری در دسترس است؟",
      answer:
        "همایار به‌صورت پیش‌فرض به‌عنوان یک سرویس ابری چندمستأجری مدیریت‌شده اجرا می‌شود، با گزینه‌های محل نگهداری داده در ایالات متحده و اتحادیه اروپا. مشتریان سازمانی می‌توانند در یک VPC تک‌مستأجر مستقر شوند — در حساب AWS، Azure یا GCP خودتان — تا صدا، رونوشت‌ها و نتایج تحلیل هرگز از مرز شبکه شما خارج نشوند. هر دو مدل همان خط پردازش تحلیل را اجرا می‌کنند و همان آهنگ انتشار را دریافت می‌کنند؛ گزینه VPC، کلیدهای KMS، کنترل‌های شبکه و دسترسی حسابرسی زیرساخت خودتان را اضافه می‌کند.",
      group: "Deployment & Integration",
    },
    {
      question:
        "همایار با کدام CRMها و ابزارهای میز پشتیبانی یکپارچه می‌شود؟",
      answer:
        "یکپارچه‌سازی‌های بومی، Salesforce، HubSpot، Microsoft Dynamics 365، Zendesk و Freshdesk را پوشش می‌دهند. امتیازهای تماس، مسائل شناسایی‌شده و یادداشت‌های مربی‌گری با پرونده مخاطب، پرونده پشتیبانی یا فرصت فروشِ متناظر همگام‌سازی می‌شوند، تا سرپرست‌ها درون ابزارهایی که همین حالا استفاده می‌کنند کار کنند. یک REST API و وب‌هوک‌ها، CRMهای سفارشی و سامانه‌های داخلی را مدیریت می‌کنند — بیشتر تیم‌ها یک یکپارچه‌سازی سفارشی را با راهنمای یکپارچه‌سازی ما در یک تا دو اسپرینت تکمیل می‌کنند.",
      group: "Deployment & Integration",
    },
    {
      question: "تماس‌ها چگونه از سامانه تلفنی ما به همایار وارد می‌شوند؟",
      answer:
        "سه مسیر دریافت پشتیبانی می‌شود: رابط‌های مستقیم برای پلتفرم‌های بزرگ از جمله Genesys Cloud، Five9، Amazon Connect، Twilio Flex، Cisco و Avaya؛ بارگذاری دسته‌ای از طریق SFTP یا S3 برای بایگانی‌های ضبط موجود؛ و یک API جریانی برای سامانه‌های تلفنی سفارشی. فایل‌های ضبط‌شده همراه با فراداده‌های خود وارد می‌شوند — شناسه اپراتور، صف، وضعیت نهایی، برچسب‌های زمانی — تا تحلیل از همان روز نخست به‌طور کامل قابل انتساب باشد. راه‌اندازی بیشتر رابط‌ها در کمتر از یک هفته تکمیل می‌شود.",
      group: "Deployment & Integration",
    },
  ],
  ctaTitle: "هنوز پرسشی دارید؟",
  ctaBody:
    "مستقیماً با تیم صحبت کنید — می‌توانیم در یک جلسه ۳۰ دقیقه‌ای، حجم تماس‌ها، الزامات امنیتی و برنامه راه‌اندازی شما را با هم بررسی کنیم.",
  demoCta: "درخواست دمو",
  emailLabel: "ایمیل به info@ksf.ir",
};

const DICT: Record<Locale, FaqCopy> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

export function FaqContent() {
  const { locale } = useLocale();
  const c = DICT[locale] ?? en;

  const groups = c.groupOrder
    .map((group) => ({
      group,
      items: c.faq.filter((item) => item.group === group),
    }))
    .filter(({ items }) => items.length > 0);

  return (
    <>
      <PageHero
        eyebrow={c.heroEyebrow}
        title={c.heroTitle}
        description={c.heroDescription}
      />

      <section className="py-16 sm:py-24">
        <div className="container-site">
          <div className="space-y-16">
            {groups.map(({ group, items }) => (
              <div key={group}>
                <SectionHeading eyebrow={c.groupEyebrows[group]} title={group} />
                <div className="mt-8 space-y-3">
                  {items.map((item) => (
                    <details
                      key={item.question}
                      className="group rounded-xl border border-line bg-surface"
                    >
                      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded-xl px-5 py-4 text-left font-medium text-ink transition-colors hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
                        {item.question}
                        <ChevronDownIcon className="h-5 w-5 shrink-0 text-ink-muted transition-transform duration-200 group-open:rotate-180" />
                      </summary>
                      <p className="px-5 pb-5 leading-relaxed text-ink-soft">
                        {item.answer}
                      </p>
                    </details>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Still have questions */}
      <section className="border-t border-line py-16 sm:py-24">
        <div className="container-site">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
              {c.ctaTitle}
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-soft">
              {c.ctaBody}
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <ButtonLink href="/contact" variant="primary" size="lg">
                {c.demoCta}
              </ButtonLink>
              <a
                href="mailto:info@ksf.ir"
                className="inline-flex h-12 items-center justify-center gap-2 rounded-xl border border-line-strong px-6 text-base text-ink transition-colors hover:border-accent hover:text-accent"
              >
                {c.emailLabel}
              </a>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
