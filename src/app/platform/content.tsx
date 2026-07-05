"use client";

import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CategoryIcon } from "@/components/icons";
import { ArchitectureDiagram } from "@/components/platform/architecture-diagram";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware copy for the Platform page. English is the canonical source;
 * `fa` is a direct Persian translation. Other locales fall back to English so
 * the language switcher never shows an empty page. This pattern (a client
 * "content" component holding both locales + useLocale) is the reference every
 * other inner page follows, since the chosen locale lives in client state.
 */
type Step = { step: string; title: string; description: string };
type Item = { title: string; description: string };
type PlatformCopy = {
  heroEyebrow: string;
  heroH1: string;
  heroSub: string;
  demoCta: string;
  agentsCta: string;
  pipelineEyebrow: string;
  pipelineTitle: string;
  pipelineSub: string;
  pipeline: Step[];
  securityEyebrow: string;
  securityTitle: string;
  security: Item[];
  archEyebrow: string;
  archTitle: string;
  archDescription: string;
  browseModulesCta: string;
  ctaTitle: string;
  ctaSub: string;
  exploreCatalogCta: string;
};

const en: PlatformCopy = {
  heroEyebrow: "Platform",
  heroH1: "From raw audio to actionable insight, in one pipeline",
  heroSub:
    "Homayar ingests your call recordings, transcribes them with high accuracy, runs them through a coordinated team of AI agents, and delivers scores, risks, and coaching guidance to the people who can act on them.",
  demoCta: "Request a demo",
  agentsCta: "Meet the AI agents",
  pipelineEyebrow: "The pipeline",
  pipelineTitle: "Six steps from recording to result",
  pipelineSub:
    "Every call follows the same path — automatically, in minutes. Each stage hands verified output to the next, so nothing downstream is built on guesswork.",
  pipeline: [
    {
      step: "01",
      title: "Ingest",
      description:
        "Calls arrive via API, scheduled sync from your telephony or contact-center platform, or direct file upload. Homayar handles common audio formats, stereo and mono recordings, and high-volume batch imports.",
    },
    {
      step: "02",
      title: "Transcribe",
      description:
        "Speech-to-text converts every call into a speaker-separated, timestamped transcript. Channel separation distinguishes agent from customer; noisy audio and domain vocabulary are handled with configurable language models.",
    },
    {
      step: "03",
      title: "Multi-agent analysis",
      description:
        "Seven specialized AI agents work the transcript in parallel: evaluating quality, checking compliance, detecting issues, reading sentiment, and extracting business signals. Each agent cites the transcript evidence behind its findings.",
    },
    {
      step: "04",
      title: "Score",
      description:
        "Every call receives a quality score against your own rubric — greeting, discovery, resolution, tone, closure, and any custom criteria. Scores are consistent, explainable, and linked to the exact moments that earned or lost points.",
    },
    {
      step: "05",
      title: "Surface insights",
      description:
        "Findings roll up into dashboards: issue topics trending across thousands of calls, sentiment shifts by team or product line, compliance exposure, and the operational root causes behind repeat contacts.",
    },
    {
      step: "06",
      title: "Coach",
      description:
        "For each agent, Homayar builds a profile of strengths and development areas, then generates concrete coaching recommendations with example calls attached — ready for a supervisor's next one-on-one.",
    },
  ],
  securityEyebrow: "Security",
  securityTitle: "Security and deployment, enterprise-grade by default",
  security: [
    {
      title: "Encryption everywhere",
      description:
        "Audio, transcripts, and analysis results are encrypted in transit (TLS 1.2+) and at rest (AES-256). Sensitive fields such as payment card numbers can be automatically redacted from transcripts.",
    },
    {
      title: "Access control and audit",
      description:
        "Role-based access control with SSO/SAML support. Every view, export, and configuration change is recorded in an immutable audit log, so you always know who accessed which call and when.",
    },
    {
      title: "Data residency and retention",
      description:
        "Configurable retention windows per data type — keep transcripts longer than audio, or purge both on your schedule. Deployment options support regional data-residency requirements.",
    },
    {
      title: "Flexible deployment",
      description:
        "Run Homayar as managed cloud, single-tenant private cloud, or fully on-premises for regulated environments such as banking, healthcare, and government. Same platform, your infrastructure rules.",
    },
  ],
  archEyebrow: "Architecture",
  archTitle: "Built to sit alongside your stack, not replace it",
  archDescription:
    "Homayar integrates with your existing telephony, CRM, and BI tools through APIs and scheduled exports. The platform is horizontally scalable — from a few hundred calls a day to hundreds of thousands — with processing isolation per tenant and no agent-side software to install. Your recordings stay under your governance; Homayar adds the analysis layer on top.",
  browseModulesCta: "Browse platform modules",
  ctaTitle: "See the pipeline run on your own calls",
  ctaSub:
    "Bring a recording to a 30-minute walkthrough and leave with a scored, agent-analyzed report — or explore the modules that make up the platform.",
  exploreCatalogCta: "Explore the catalog",
};

const fa: PlatformCopy = {
  heroEyebrow: "پلتفرم",
  heroH1: "از صدای خام تا بینش کاربردی، در یک خط پردازش",
  heroSub:
    "همایار فایل‌های ضبط‌شده تماس‌های شما را دریافت می‌کند، آن‌ها را با دقت بالا رونویسی می‌کند، از میان تیمی هماهنگ از عامل‌های هوشمند عبور می‌دهد و امتیازها، ریسک‌ها و راهنمای مربی‌گری را به دست افرادی می‌رساند که می‌توانند بر اساس آن عمل کنند.",
  demoCta: "درخواست دمو",
  agentsCta: "آشنایی با عامل‌های هوشمند",
  pipelineEyebrow: "خط پردازش",
  pipelineTitle: "شش گام از ضبط تماس تا نتیجه",
  pipelineSub:
    "هر تماس مسیری یکسان را طی می‌کند — به‌صورت خودکار و در عرض چند دقیقه. هر مرحله خروجی تأییدشده را به مرحله بعد می‌سپارد، تا هیچ بخشی بر پایه حدس و گمان ساخته نشود.",
  pipeline: [
    {
      step: "۰۱",
      title: "دریافت",
      description:
        "تماس‌ها از طریق API، همگام‌سازی زمان‌بندی‌شده با سامانه تلفنی یا مرکز تماس شما، یا بارگذاری مستقیم فایل وارد می‌شوند. همایار از فرمت‌های رایج صوتی، ضبط‌های استریو و مونو و واردسازی دسته‌ای با حجم بالا پشتیبانی می‌کند.",
    },
    {
      step: "۰۲",
      title: "رونویسی",
      description:
        "تبدیل گفتار به متن، هر تماس را به یک رونوشت زمان‌دار و تفکیک‌شده بر اساس گوینده تبدیل می‌کند. تفکیک کانال، اپراتور را از مشتری جدا می‌کند؛ صدای نویزدار و واژگان تخصصی با مدل‌های زبانی قابل پیکربندی مدیریت می‌شوند.",
    },
    {
      step: "۰۳",
      title: "تحلیل چندعاملی",
      description:
        "هفت عامل هوشمند تخصصی به‌صورت موازی روی رونوشت کار می‌کنند: ارزیابی کیفیت، بررسی انطباق، شناسایی مسائل، خوانش احساسات و استخراج سیگنال‌های کسب‌وکار. هر عامل، شواهد متنی پشتِ یافته‌هایش را ذکر می‌کند.",
    },
    {
      step: "۰۴",
      title: "امتیازدهی",
      description:
        "هر تماس بر اساس معیارهای خودِ شما امتیاز کیفیت می‌گیرد — احوال‌پرسی، نیازسنجی، حل مسئله، لحن، جمع‌بندی و هر معیار سفارشی دیگر. امتیازها یکدست، قابل توضیح و به همان لحظاتی که امتیاز را ساخته یا از دست داده‌اند پیوند خورده‌اند.",
    },
    {
      step: "۰۵",
      title: "آشکارسازی بینش",
      description:
        "یافته‌ها در داشبوردها جمع‌بندی می‌شوند: موضوعات پرتکرار در میان هزاران تماس، تغییرات احساسات بر حسب تیم یا خط محصول، میزان مواجهه با ریسک انطباق و ریشه‌های عملیاتی پشتِ تماس‌های تکراری.",
    },
    {
      step: "۰۶",
      title: "مربی‌گری",
      description:
        "همایار برای هر اپراتور نیم‌رخی از نقاط قوت و حوزه‌های قابل توسعه می‌سازد، سپس توصیه‌های مربی‌گری مشخص همراه با نمونه تماس‌ها تولید می‌کند — آماده برای جلسه بعدی سرپرست با اپراتور.",
    },
  ],
  securityEyebrow: "امنیت",
  securityTitle: "امنیت و استقرار، در سطح سازمانی به‌صورت پیش‌فرض",
  security: [
    {
      title: "رمزنگاری در همه‌جا",
      description:
        "صدا، رونوشت‌ها و نتایج تحلیل، هم در حین انتقال (TLS 1.2+) و هم در حالت ذخیره (AES-256) رمزنگاری می‌شوند. فیلدهای حساس مانند شماره کارت پرداخت می‌توانند به‌صورت خودکار از رونوشت‌ها حذف (Redact) شوند.",
    },
    {
      title: "کنترل دسترسی و حسابرسی",
      description:
        "کنترل دسترسی نقش‌محور با پشتیبانی از SSO/SAML. هر مشاهده، خروجی‌گیری و تغییر در پیکربندی در یک گزارش حسابرسی تغییرناپذیر ثبت می‌شود، تا همیشه بدانید چه کسی، چه تماسی را و چه زمانی دیده است.",
    },
    {
      title: "محل نگهداری و مدت نگهداری داده",
      description:
        "بازه‌های نگهداری قابل پیکربندی برای هر نوع داده — رونوشت‌ها را بیش از فایل صوتی نگه دارید، یا هر دو را طبق زمان‌بندی خود پاک‌سازی کنید. گزینه‌های استقرار، الزامات محل نگهداری داده در هر منطقه را پشتیبانی می‌کنند.",
    },
    {
      title: "استقرار منعطف",
      description:
        "همایار را به‌صورت ابری مدیریت‌شده، ابر خصوصی تک‌مستأجر، یا کاملاً درون‌سازمانی (On-Premise) برای محیط‌های تحت مقررات مانند بانکداری، سلامت و دولتی اجرا کنید. همان پلتفرم، با قواعد زیرساخت شما.",
    },
  ],
  archEyebrow: "معماری",
  archTitle: "ساخته‌شده تا در کنار سامانه‌های شما بنشیند، نه جایگزین آن‌ها شود",
  archDescription:
    "همایار از طریق APIها و خروجی‌های زمان‌بندی‌شده با سامانه تلفنی، CRM و ابزارهای هوش تجاری (BI) موجود شما یکپارچه می‌شود. این پلتفرم به‌صورت افقی مقیاس‌پذیر است — از چند صد تماس در روز تا صدها هزار تماس — با جداسازی پردازش برای هر مستأجر و بدون نیاز به نصب هیچ نرم‌افزاری سمت اپراتور. فایل‌های ضبط‌شده شما تحت حاکمیت خودتان باقی می‌مانند؛ همایار لایه تحلیل را روی آن می‌افزاید.",
  browseModulesCta: "مرور ماژول‌های پلتفرم",
  ctaTitle: "اجرای خط پردازش را روی تماس‌های خودتان ببینید",
  ctaSub:
    "یک فایل ضبط‌شده به جلسه ۳۰ دقیقه‌ای بیاورید و با یک گزارش امتیازدهی‌شده و تحلیل‌شده توسط عامل‌ها بیرون بروید — یا ماژول‌هایی را که پلتفرم را می‌سازند کاوش کنید.",
  exploreCatalogCta: "کاوش در کاتالوگ",
};

const DICT: Record<Locale, PlatformCopy> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

const SECURITY_ICONS = ["shield", "shield", "server", "server"] as const;

export function PlatformContent() {
  const { locale } = useLocale();
  const c = DICT[locale] ?? en;

  return (
    <>
      <PageHero eyebrow={c.heroEyebrow} title={c.heroH1} description={c.heroSub}>
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/contact" size="lg">
            {c.demoCta}
            <ArrowRightIcon width={18} height={18} />
          </ButtonLink>
          <ButtonLink href="/ai-agents" variant="secondary" size="lg">
            {c.agentsCta}
          </ButtonLink>
        </div>
      </PageHero>

      {/* ── Pipeline ── */}
      <section aria-labelledby="pipeline-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow={c.pipelineEyebrow}
            title={c.pipelineTitle}
            description={c.pipelineSub}
          />
          <h2 id="pipeline-heading" className="sr-only">
            {c.pipelineTitle}
          </h2>

          <ol className="mt-12 max-w-3xl">
            {c.pipeline.map((step, i) => (
              <li key={step.step} className="relative flex gap-5 sm:gap-7">
                {/* node + connector rail */}
                <div className="flex flex-col items-center">
                  <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full border border-accent/40 bg-accent/10 font-mono text-sm font-medium text-accent">
                    {step.step}
                  </div>
                  {i < c.pipeline.length - 1 && (
                    <div
                      aria-hidden
                      className="w-px flex-1 border-l border-dashed border-line-strong"
                    />
                  )}
                </div>
                <div
                  className={`card-surface flex-1 rounded-xl p-5 sm:p-6 ${
                    i < c.pipeline.length - 1 ? "mb-6" : ""
                  }`}
                >
                  <h3 className="font-display text-lg font-semibold tracking-tight text-ink">
                    {step.title}
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                    {step.description}
                  </p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* ── Security ── */}
      <section
        aria-labelledby="security-heading"
        className="border-y border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading eyebrow={c.securityEyebrow} title={c.securityTitle} />
          <h2 id="security-heading" className="sr-only">
            {c.securityTitle}
          </h2>
          <div className="mt-12 grid gap-6 sm:grid-cols-2">
            {c.security.map((item, i) => (
              <div key={item.title} className="card-surface rounded-xl p-6">
                <div className="mb-4 inline-flex h-11 w-11 items-center justify-center rounded-lg border border-accent/30 bg-accent/10 text-accent">
                  <CategoryIcon icon={SECURITY_ICONS[i % SECURITY_ICONS.length]} />
                </div>
                <h3 className="font-display text-lg font-semibold tracking-tight text-ink">
                  {item.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                  {item.description}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Architecture ── */}
      <section aria-labelledby="architecture-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <div className="grid items-start gap-12 lg:grid-cols-2 lg:gap-16">
            <div>
              <SectionHeading
                eyebrow={c.archEyebrow}
                title={c.archTitle}
                description={c.archDescription}
              />
              <h2 id="architecture-heading" className="sr-only">
                {c.archTitle}
              </h2>
              <div className="mt-8">
                <ButtonLink href="/catalog" variant="secondary">
                  {c.browseModulesCta}
                  <ArrowRightIcon width={16} height={16} />
                </ButtonLink>
              </div>
            </div>
            <ArchitectureDiagram />
          </div>
        </div>
      </section>

      {/* ── CTA band ── */}
      <section
        aria-labelledby="platform-cta-heading"
        className="border-t border-line bg-surface py-16 sm:py-20"
      >
        <div className="container-site">
          <div className="mx-auto max-w-2xl text-center">
            <h2
              id="platform-cta-heading"
              className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl"
            >
              {c.ctaTitle}
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-muted">
              {c.ctaSub}
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <ButtonLink href="/contact" size="lg">
                {c.demoCta}
                <ArrowRightIcon width={18} height={18} />
              </ButtonLink>
              <ButtonLink href="/catalog" variant="secondary" size="lg">
                {c.exploreCatalogCta}
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
