"use client";

import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon, CategoryIcon } from "@/components/icons";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware copy for the Solutions page. English is the canonical source;
 * `fa` is a direct Persian translation. Other locales fall back to English so
 * the language switcher never shows an empty page. Follows the reference
 * pattern set by the Platform page (a client "content" component holding both
 * locales + useLocale), since the chosen locale lives in client state.
 */
type Industry = {
  slug: string;
  name: string;
  headline: string;
  description: string;
  outcomes: string[];
};
type Team = { name: string; headline: string; description: string };
type SolutionsCopy = {
  heroEyebrow: string;
  heroH1: string;
  heroSub: string;
  demoCta: string;
  platformCta: string;
  industriesEyebrow: string;
  industriesTitle: string;
  industriesDescription: string;
  industriesHidden: string;
  industries: Industry[];
  teamsEyebrow: string;
  teamsTitle: string;
  teamsDescription: string;
  teamsHidden: string;
  teams: Team[];
  ctaTitle: string;
  ctaSub: string;
  ctaTalkCta: string;
  ctaCaseStudiesCta: string;
};

const en: SolutionsCopy = {
  heroEyebrow: "Solutions",
  heroH1: "Built for the industries where every call carries weight",
  heroSub:
    "Homayar adapts to the regulations, vocabulary, and quality standards of your sector — and to the teams inside it who live on call outcomes.",
  demoCta: "Book a demo",
  platformCta: "Explore the platform",
  industriesEyebrow: "By industry",
  industriesTitle: "Tuned to your sector's rules and vocabulary",
  industriesDescription:
    "Each deployment starts from an industry baseline — the regulations, call types, and quality criteria your sector already works to — then calibrates against your own calls.",
  industriesHidden: "Industries Homayar serves",
  industries: [
    {
      slug: "banking",
      name: "Banking & Financial Services",
      headline: "Compliance coverage on every call, not every fiftieth",
      description:
        "Banks face strict disclosure, verification, and complaint-handling requirements on every customer interaction. Homayar verifies required scripts and disclosures across 100% of calls, flags potential mis-selling or prohibited language, and produces audit-ready evidence trails that stand up to regulator scrutiny.",
      outcomes: [
        "100% verification of mandatory disclosures and identity checks",
        "Same-day escalation of complaint and mis-selling risk with evidence attached",
        "Audit-ready reporting that cuts compliance review preparation time",
      ],
    },
    {
      slug: "healthcare",
      name: "Healthcare",
      headline: "Patient conversations handled with care — and proof of it",
      description:
        "Hospital call centers and patient-access teams handle scheduling, billing, and clinical routing calls where empathy and accuracy both matter. Homayar measures whether patients were heard, verified correctly, and routed safely — while keeping sensitive data redacted and access tightly controlled.",
      outcomes: [
        "Consistent quality scoring for scheduling, billing, and triage-routing calls",
        "Early detection of patient frustration, access barriers, and repeat-call drivers",
        "Privacy-conscious analysis with redaction, RBAC, and full audit logging",
      ],
    },
    {
      slug: "insurance",
      name: "Insurance",
      headline: "See claims friction before it becomes churn",
      description:
        "Claims and policy-service calls are where insurers win or lose trust. Homayar scores every claims interaction, detects disputed denials and escalation risk, verifies required policy disclosures, and shows leadership exactly which products and processes generate the most call friction.",
      outcomes: [
        "Every claims call scored for clarity, empathy, and resolution quality",
        "Dispute and escalation signals surfaced while the claim is still active",
        "Product-level insight into which policies drive confusion and complaints",
      ],
    },
    {
      slug: "telecom",
      name: "Telecom",
      headline: "Measure millions of conversations without sampling",
      description:
        "Telecom contact centers operate at a volume where manual QA is statistically meaningless. Homayar scales to hundreds of thousands of calls, tracking churn signals, billing-dispute drivers, and retention-offer effectiveness across every queue and outsourced site with the same rubric.",
      outcomes: [
        "Uniform QA standards across in-house and outsourced (BPO) sites",
        "Churn-intent and save-offer effectiveness measured on real calls",
        "Billing and network complaint trends connected to root causes",
      ],
    },
    {
      slug: "retail",
      name: "Retail & E-commerce",
      headline: "The voice of the customer, finally at dashboard scale",
      description:
        "Order issues, returns, delivery failures, product confusion — your customers explain their problems on every call. Homayar classifies and quantifies those reasons across all of them, so merchandising, logistics, and CX teams fix root causes instead of refunding symptoms.",
      outcomes: [
        "Contact-reason analytics that rank the true drivers of call volume",
        "Early warning on product and delivery issues before reviews turn negative",
        "Service quality scored consistently across peak-season ramp staff",
      ],
    },
    {
      slug: "government",
      name: "Government & Public Sector",
      headline: "Accountable, measurable citizen service",
      description:
        "Public-sector contact centers answer to citizens and oversight bodies alike. Homayar delivers transparent, consistent quality measurement, accessibility and fairness signals, and deployment options — including fully on-premises — that meet government security and data-sovereignty requirements.",
      outcomes: [
        "Consistent, defensible service-quality measurement across all calls",
        "Detection of unresolved citizen issues and repeat-contact patterns",
        "On-premises and sovereign deployment options with full audit trails",
      ],
    },
  ],
  teamsEyebrow: "By team",
  teamsTitle: "One platform, four ways to win with it",
  teamsDescription:
    "The same scored, evidence-backed call data serves every team that depends on conversation quality — each with its own view of the results.",
  teamsHidden: "Teams Homayar serves",
  teams: [
    {
      name: "QA Teams",
      headline: "Stop sampling. Start covering.",
      description:
        "Replace the 2% random sample with 100% scored coverage. Your reviewers shift from listening to calibrating: validating borderline scores, refining the rubric, and spending recovered hours on the calls that genuinely need human judgment.",
    },
    {
      name: "Support Operations",
      headline: "Find the process failures hiding in call volume",
      description:
        "Repeat contacts, long handle times, and escalations have root causes. Homayar connects them to specific products, policies, and process gaps — so operations leaders fix the source instead of staffing around the symptom.",
    },
    {
      name: "Sales Leaders",
      headline: "Know why deals close — and why they don't",
      description:
        "Homayar analyzes sales and retention calls for discovery quality, objection handling, and offer effectiveness. See which talk patterns your best closers share, and coach the rest of the team toward them with real call evidence.",
    },
    {
      name: "Compliance Teams",
      headline: "Evidence-grade monitoring across every interaction",
      description:
        "Move from periodic spot checks to continuous verification. Every disclosure, script, and prohibited-phrase rule is checked on every call, violations arrive with transcript evidence the same day, and the audit trail builds itself.",
    },
  ],
  ctaTitle: "See Homayar on your industry's calls",
  ctaSub:
    "Bring a sample of your own recordings to the demo. We'll run them through the agent team and walk through the scores, signals, and evidence together.",
  ctaTalkCta: "Talk to our team",
  ctaCaseStudiesCta: "Read case studies",
};

const fa: SolutionsCopy = {
  heroEyebrow: "راهکارها",
  heroH1: "ساخته‌شده برای صنایعی که در آن‌ها هر تماس وزن دارد",
  heroSub:
    "همایار خود را با مقررات، واژگان و استانداردهای کیفیت صنعت شما هماهنگ می‌کند — و با تیم‌هایی که در آن صنعت، زندگی‌شان به نتیجه تماس‌ها گره خورده است.",
  demoCta: "رزرو دمو",
  platformCta: "کاوش در پلتفرم",
  industriesEyebrow: "بر اساس صنعت",
  industriesTitle: "هماهنگ با قواعد و واژگان بخش شما",
  industriesDescription:
    "هر استقرار از یک خط پایه صنعتی آغاز می‌شود — مقررات، انواع تماس و معیارهای کیفیتی که بخش شما همین حالا با آن‌ها کار می‌کند — و سپس بر اساس تماس‌های خودِ شما کالیبره می‌شود.",
  industriesHidden: "صنایعی که همایار به آن‌ها خدمت می‌دهد",
  industries: [
    {
      slug: "banking",
      name: "بانکداری و خدمات مالی",
      headline: "پوشش انطباق روی هر تماس، نه فقط هر پنجاهمین تماس",
      description:
        "بانک‌ها در هر تعامل با مشتری با الزامات سختگیرانه افشاگری، احراز هویت و رسیدگی به شکایات روبه‌رو هستند. همایار اسکریپت‌ها و افشاگری‌های لازم را در ۱۰۰٪ تماس‌ها بررسی می‌کند، فروش نادرست احتمالی یا زبان ممنوعه را علامت‌گذاری می‌کند و مسیرهای شواهد آماده حسابرسی تولید می‌کند که در برابر بازرسی نهاد ناظر مقاومت می‌کنند.",
      outcomes: [
        "بررسی ۱۰۰٪ افشاگری‌های اجباری و احراز هویت‌ها",
        "ارجاع همان‌روزِ ریسک شکایت و فروش نادرست همراه با شواهد پیوست‌شده",
        "گزارش‌دهی آماده حسابرسی که زمان آماده‌سازی بازبینی انطباق را کاهش می‌دهد",
      ],
    },
    {
      slug: "healthcare",
      name: "سلامت و درمان",
      headline: "گفت‌وگو با بیماران، با دقت و مراقبت — و با اثباتِ آن",
      description:
        "مراکز تماس بیمارستان‌ها و تیم‌های دسترسی بیماران، تماس‌های نوبت‌دهی، صورت‌حساب و مسیریابی بالینی را مدیریت می‌کنند؛ جایی که هم همدلی و هم دقت اهمیت دارند. همایار اندازه‌گیری می‌کند که آیا بیماران شنیده شده‌اند، درست احراز هویت شده‌اند و ایمن مسیریابی شده‌اند یا نه — در عین حال داده‌های حساس را پنهان‌سازی کرده و دسترسی را به‌دقت کنترل می‌کند.",
      outcomes: [
        "امتیازدهی کیفیت یکدست برای تماس‌های نوبت‌دهی، صورت‌حساب و مسیریابی تریاژ",
        "تشخیص زودهنگام ناامیدی بیمار، موانع دسترسی و عوامل تماس‌های تکراری",
        "تحلیل حافظِ حریم خصوصی با حذف داده، کنترل دسترسی نقش‌محور و گزارش حسابرسی کامل",
      ],
    },
    {
      slug: "insurance",
      name: "بیمه",
      headline: "اصطکاک خسارت را پیش از تبدیل‌شدن به ریزش مشتری ببینید",
      description:
        "تماس‌های خسارت و خدمات بیمه‌نامه جایی است که بیمه‌گران اعتماد را به دست می‌آورند یا از دست می‌دهند. همایار به هر تعامل خسارت امتیاز می‌دهد، ردِ خسارت‌های مورد اختلاف و ریسک ارجاع را شناسایی می‌کند، افشاگری‌های لازم بیمه‌نامه را بررسی می‌کند و به مدیریت نشان می‌دهد دقیقاً کدام محصولات و فرایندها بیشترین اصطکاک تماس را ایجاد می‌کنند.",
      outcomes: [
        "امتیازدهی هر تماس خسارت بر اساس شفافیت، همدلی و کیفیت حل مسئله",
        "آشکارسازی سیگنال‌های اختلاف و ارجاع در حالی که خسارت هنوز در جریان است",
        "بینش در سطح محصول درباره اینکه کدام بیمه‌نامه‌ها سردرگمی و شکایت ایجاد می‌کنند",
      ],
    },
    {
      slug: "telecom",
      name: "مخابرات",
      headline: "میلیون‌ها مکالمه را بدون نمونه‌گیری اندازه بگیرید",
      description:
        "مراکز تماس مخابراتی در حجمی فعالیت می‌کنند که کنترل کیفیت دستی در آن از نظر آماری بی‌معنا است. همایار تا صدها هزار تماس مقیاس می‌پذیرد و سیگنال‌های ریزش مشتری، عوامل اختلاف صورت‌حساب و اثربخشی پیشنهادهای حفظ مشتری را در هر صف و سایت برون‌سپاری‌شده با همان معیارها دنبال می‌کند.",
      outcomes: [
        "استانداردهای یکسان کنترل کیفیت در سایت‌های داخلی و برون‌سپاری‌شده (BPO)",
        "اندازه‌گیری قصد ریزش و اثربخشی پیشنهادهای نگه‌دارنده روی تماس‌های واقعی",
        "پیوند روندهای شکایت صورت‌حساب و شبکه به ریشه‌های اصلی آن‌ها",
      ],
    },
    {
      slug: "retail",
      name: "خرده‌فروشی و تجارت الکترونیک",
      headline: "صدای مشتری، سرانجام در مقیاس داشبورد",
      description:
        "مشکلات سفارش، مرجوعی‌ها، شکست در تحویل، سردرگمی درباره محصول — مشتریان شما مشکلاتشان را در هر تماس توضیح می‌دهند. همایار این دلایل را در سراسر همه تماس‌ها دسته‌بندی و کمّی می‌کند، تا تیم‌های کالا، لجستیک و تجربه مشتری ریشه‌ها را برطرف کنند، نه اینکه فقط علائم را بازپرداخت کنند.",
      outcomes: [
        "تحلیل دلیل تماس که عوامل واقعی حجم تماس را رتبه‌بندی می‌کند",
        "هشدار زودهنگام درباره مشکلات محصول و تحویل پیش از منفی‌شدن نظرات",
        "امتیازدهی یکدست کیفیت خدمات در میان نیروهای فصل اوج",
      ],
    },
    {
      slug: "government",
      name: "دولتی و بخش عمومی",
      headline: "خدمات شهروندی پاسخگو و قابل اندازه‌گیری",
      description:
        "مراکز تماس بخش عمومی هم در برابر شهروندان و هم در برابر نهادهای نظارتی پاسخگو هستند. همایار اندازه‌گیری شفاف و یکدست کیفیت، سیگنال‌های دسترس‌پذیری و انصاف، و گزینه‌های استقرار — از جمله کاملاً درون‌سازمانی (On-Premise) — ارائه می‌دهد که الزامات امنیتی و حاکمیت داده دولتی را برآورده می‌کنند.",
      outcomes: [
        "اندازه‌گیری یکدست و قابل دفاع کیفیت خدمات در سراسر همه تماس‌ها",
        "تشخیص مسائل حل‌نشده شهروندان و الگوهای تماس تکراری",
        "گزینه‌های استقرار درون‌سازمانی و حاکمیتی همراه با مسیرهای حسابرسی کامل",
      ],
    },
  ],
  teamsEyebrow: "بر اساس تیم",
  teamsTitle: "یک پلتفرم، چهار راه برای برنده‌شدن با آن",
  teamsDescription:
    "همان داده تماسِ امتیازدهی‌شده و مستند به شواهد، به هر تیمی که به کیفیت مکالمه وابسته است خدمت می‌کند — هر کدام با نمای ویژه خودش از نتایج.",
  teamsHidden: "تیم‌هایی که همایار به آن‌ها خدمت می‌دهد",
  teams: [
    {
      name: "تیم‌های کنترل کیفیت",
      headline: "نمونه‌گیری را کنار بگذارید. پوشش کامل را آغاز کنید.",
      description:
        "نمونه تصادفی ۲ درصدی را با پوشش امتیازدهی‌شده ۱۰۰ درصدی جایگزین کنید. بازبین‌های شما از گوش‌دادن به کالیبره‌کردن جابه‌جا می‌شوند: اعتبارسنجی امتیازهای مرزی، پالایش معیارها و صرف ساعات بازیافته روی تماس‌هایی که واقعاً به قضاوت انسانی نیاز دارند.",
    },
    {
      name: "عملیات پشتیبانی",
      headline: "شکست‌های فرایندیِ پنهان در حجم تماس را پیدا کنید",
      description:
        "تماس‌های تکراری، زمان رسیدگی طولانی و ارجاع‌ها ریشه‌هایی دارند. همایار آن‌ها را به محصولات، سیاست‌ها و شکاف‌های فرایندی مشخص پیوند می‌دهد — تا مدیران عملیات منشأ را برطرف کنند، نه اینکه با افزایش نیرو دور علائم بچرخند.",
    },
    {
      name: "مدیران فروش",
      headline: "بدانید چرا معاملات بسته می‌شوند — و چرا نمی‌شوند",
      description:
        "همایار تماس‌های فروش و حفظ مشتری را از نظر کیفیت نیازسنجی، مدیریت اعتراض و اثربخشی پیشنهاد تحلیل می‌کند. ببینید بهترین فروشندگان شما چه الگوهای گفتاری مشترکی دارند و بقیه تیم را با شواهد تماس واقعی به سمت آن‌ها مربی‌گری کنید.",
    },
    {
      name: "تیم‌های انطباق",
      headline: "پایش در سطح شواهد، در سراسر هر تعامل",
      description:
        "از بازرسی‌های دوره‌ای نقطه‌ای به بررسی پیوسته بروید. هر افشاگری، اسکریپت و قاعده عبارت‌های ممنوعه روی هر تماس بررسی می‌شود، تخلف‌ها همان روز همراه با شواهد رونوشت می‌رسند و مسیر حسابرسی، خودش را می‌سازد.",
    },
  ],
  ctaTitle: "همایار را روی تماس‌های صنعت خودتان ببینید",
  ctaSub:
    "نمونه‌ای از فایل‌های ضبط‌شده خودتان را به دمو بیاورید. ما آن‌ها را از میان تیم عامل‌ها عبور می‌دهیم و امتیازها، سیگنال‌ها و شواهد را با هم مرور می‌کنیم.",
  ctaTalkCta: "با تیم ما صحبت کنید",
  ctaCaseStudiesCta: "مطالعه نمونه‌کارها",
};

const DICT: Record<Locale, SolutionsCopy> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

/**
 * Per-industry visual accent. Subtle: icon tint, top hairline, and
 * check-mark color only — body text stays on the standard ink scale.
 */
const INDUSTRY_STYLES: Record<
  string,
  { icon: string; text: string; iconBox: string; hairline: string }
> = {
  banking: {
    icon: "shield",
    text: "text-accent",
    iconBox: "border-accent/30 bg-accent/10",
    hairline: "via-accent/60",
  },
  healthcare: {
    icon: "heart",
    text: "text-teal",
    iconBox: "border-teal/30 bg-teal/10",
    hairline: "via-teal/60",
  },
  insurance: {
    icon: "gauge",
    text: "text-violet-soft",
    iconBox: "border-violet/30 bg-violet/10",
    hairline: "via-violet/60",
  },
  telecom: {
    icon: "waveform",
    text: "text-amber",
    iconBox: "border-amber/30 bg-amber/10",
    hairline: "via-amber/60",
  },
  retail: {
    icon: "trend",
    text: "text-success",
    iconBox: "border-success/30 bg-success/10",
    hairline: "via-success/60",
  },
  government: {
    icon: "server",
    text: "text-accent",
    iconBox: "border-accent/30 bg-accent/10",
    hairline: "via-accent/60",
  },
};

const TEAM_ICONS = ["gauge", "layers", "trend", "shield"];

export function SolutionsContent() {
  const { locale } = useLocale();
  const c = DICT[locale] ?? en;

  return (
    <>
      <PageHero
        eyebrow={c.heroEyebrow}
        title={c.heroH1}
        description={c.heroSub}
      >
        <div className="flex flex-wrap gap-3">
          <ButtonLink href="/contact" size="lg">
            {c.demoCta}
            <ArrowRightIcon width={18} height={18} />
          </ButtonLink>
          <ButtonLink href="/platform" variant="secondary" size="lg">
            {c.platformCta}
          </ButtonLink>
        </div>
      </PageHero>

      {/* ── Industries ── */}
      <section aria-labelledby="industries-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow={c.industriesEyebrow}
            title={c.industriesTitle}
            description={c.industriesDescription}
          />
          {/* Visually hidden anchor for the aria-labelledby landmark */}
          <h2 id="industries-heading" className="sr-only">
            {c.industriesHidden}
          </h2>

          <div className="mt-12 grid gap-6 md:grid-cols-2">
            {c.industries.map((industry) => {
              const style =
                INDUSTRY_STYLES[industry.slug] ?? INDUSTRY_STYLES.banking;
              return (
                <article
                  key={industry.slug}
                  className="card-surface relative flex flex-col overflow-hidden p-6 sm:p-8"
                >
                  <div
                    aria-hidden
                    className={`absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent to-transparent ${style.hairline}`}
                  />
                  <div className="flex items-center gap-3">
                    <span
                      className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border ${style.iconBox} ${style.text}`}
                    >
                      <CategoryIcon icon={style.icon} width={20} height={20} />
                    </span>
                    <p
                      className={`text-xs font-semibold uppercase tracking-[0.16em] ${style.text}`}
                    >
                      {industry.name}
                    </p>
                  </div>

                  <h3 className="mt-5 font-display text-xl font-semibold tracking-tight text-ink sm:text-2xl">
                    {industry.headline}
                  </h3>
                  <p className="mt-3 text-sm leading-relaxed text-ink-soft sm:text-base">
                    {industry.description}
                  </p>

                  <ul className="mt-6 space-y-3 border-t border-line pt-6">
                    {industry.outcomes.map((outcome) => (
                      <li key={outcome} className="flex items-start gap-3">
                        <CheckIcon
                          width={16}
                          height={16}
                          className={`mt-0.5 shrink-0 ${style.text}`}
                        />
                        <span className="text-sm leading-relaxed text-ink-soft">
                          {outcome}
                        </span>
                      </li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Teams ── */}
      <section
        aria-labelledby="teams-heading"
        className="border-t border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <SectionHeading
            eyebrow={c.teamsEyebrow}
            title={c.teamsTitle}
            description={c.teamsDescription}
          />
          <h2 id="teams-heading" className="sr-only">
            {c.teamsHidden}
          </h2>

          <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            {c.teams.map((team, i) => (
              <article
                key={team.name}
                className="card-surface flex flex-col p-6"
              >
                <span className="inline-flex h-10 w-10 items-center justify-center rounded-lg border border-line-strong bg-raised text-accent">
                  <CategoryIcon
                    icon={TEAM_ICONS[i] ?? "layers"}
                    width={20}
                    height={20}
                  />
                </span>
                <p className="mt-5 text-xs font-semibold uppercase tracking-[0.16em] text-ink-muted">
                  {team.name}
                </p>
                <h3 className="mt-2 font-display text-lg font-semibold leading-snug tracking-tight text-ink">
                  {team.headline}
                </h3>
                <p className="mt-3 text-sm leading-relaxed text-ink-soft">
                  {team.description}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA band ── */}
      <section className="border-t border-line py-16 sm:py-20">
        <div className="container-site">
          <div className="card-surface glow-accent flex flex-col items-start gap-6 p-8 sm:p-12 md:flex-row md:items-center md:justify-between">
            <div className="max-w-xl">
              <h2 className="font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                {c.ctaTitle}
              </h2>
              <p className="mt-3 leading-relaxed text-ink-soft">
                {c.ctaSub}
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <ButtonLink href="/contact" size="lg">
                {c.ctaTalkCta}
                <ArrowRightIcon width={18} height={18} />
              </ButtonLink>
              <ButtonLink href="/case-studies" variant="secondary" size="lg">
                {c.ctaCaseStudiesCta}
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
