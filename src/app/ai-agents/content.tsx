"use client";

import { ButtonLink, PageHero, SectionHeading } from "@/components/ui";
import { ArrowRightIcon, CheckIcon } from "@/components/icons";
import { AgentGlyph } from "@/components/platform/agent-glyphs";
import { OrchestrationDiagram } from "@/components/platform/orchestration-diagram";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware copy for the AI Agents page. English is the canonical source;
 * `fa` is a direct Persian translation. Other locales fall back to English so
 * the language switcher never shows an empty page. Follows the same
 * client "content" component + useLocale pattern as the Platform page, since
 * the chosen locale lives in client state.
 *
 * Note: `glyph` is the stable, locale-independent key passed to <AgentGlyph>
 * (which keys off the canonical English agent name). `name`/`role` are the
 * translated, display-only strings.
 */
type Agent = {
  glyph: string;
  name: string;
  role: string;
  description: string;
  capabilities: string[];
};

type AiAgentsCopy = {
  heroEyebrow: string;
  heroH1: string;
  heroSub: string;
  demoCta: string;
  pipelineCta: string;
  rosterEyebrow: string;
  rosterTitle: string;
  rosterDescription: string;
  rosterSrHeading: string;
  agents: Agent[];
  orchestrationEyebrow: string;
  orchestrationTitle: string;
  orchestrationDescription: string;
  orchestrationSrHeading: string;
  ctaTitle: string;
  ctaSub: string;
};

const en: AiAgentsCopy = {
  heroEyebrow: "AI Agents",
  heroH1: "Meet the team that listens to every call",
  heroSub:
    "Homayar isn't one model summarizing audio. It's seven specialized AI agents — each with a defined role, each citing evidence, each checking the others' work — collaborating on every single call.",
  demoCta: "Request a demo",
  pipelineCta: "See the pipeline",
  rosterEyebrow: "The roster",
  rosterTitle: "Seven specialists, one standard of evidence",
  rosterDescription:
    "Each agent has a defined role and a defined output. None of them summarizes in the abstract — every finding links back to the transcript moment that supports it.",
  rosterSrHeading: "The seven Homayar AI agents",
  agents: [
    {
      glyph: "Listener",
      name: "Listener",
      role: "Audio intake and signal quality",
      description:
        "Listener is the first agent on every call. It validates the recording, separates speakers, detects hold time, silence, talk-over, and audio defects, and prepares the call for accurate downstream analysis. If a recording is unusable, Listener flags it instead of letting bad data pollute your metrics.",
      capabilities: [
        "Speaker separation and channel mapping (agent vs. customer)",
        "Hold, silence, dead-air, and talk-over detection",
        "Audio quality validation and unusable-recording flagging",
        "Call segmentation for long or transferred conversations",
      ],
    },
    {
      glyph: "Scribe",
      name: "Scribe",
      role: "Transcription and structuring",
      description:
        "Scribe converts speech to a precise, timestamped, speaker-attributed transcript. It handles domain vocabulary, numbers, and names carefully, and structures the conversation into phases — opening, discovery, resolution, closure — that every other agent builds on.",
      capabilities: [
        "High-accuracy speech-to-text with timestamps and speaker labels",
        "Custom vocabulary for products, plans, and industry terms",
        "Conversation phase detection (opening, discovery, resolution, closure)",
        "Automatic redaction of sensitive data such as card numbers",
      ],
    },
    {
      glyph: "Judge",
      name: "Judge",
      role: "Quality scoring",
      description:
        "Judge evaluates each call against your QA scorecard — the same rubric your human reviewers use today. It scores every criterion with linked transcript evidence and applies the rubric identically across thousands of calls, eliminating reviewer drift.",
      capabilities: [
        "Scorecard evaluation with per-criterion evidence citations",
        "Consistent scoring calibrated to your QA team's standards",
        "Confidence flags that route borderline calls to human review",
        "Score trend tracking per agent, team, and queue",
      ],
    },
    {
      glyph: "Guardian",
      name: "Guardian",
      role: "Compliance and risk",
      description:
        "Guardian checks every call for regulatory and policy adherence: required disclosures, identity verification, prohibited language, and complaint-handling obligations. High-severity findings are escalated the same day with full evidence attached.",
      capabilities: [
        "Configurable compliance rules and mandatory-script verification",
        "Prohibited-phrase and high-risk-language detection",
        "Complaint, dispute, and escalation identification",
        "Same-day alerting with audit-ready evidence trails",
      ],
    },
    {
      glyph: "Scout",
      name: "Scout",
      role: "Issue and signal detection",
      description:
        "Scout hunts for what customers are actually telling you: product problems, process failures, churn signals, competitor mentions, and emerging complaint topics. It tags each call so patterns become visible across thousands of conversations.",
      capabilities: [
        "Issue and intent classification across every call",
        "Churn-risk and dissatisfaction signal detection",
        "Competitor and pricing mention tracking",
        "Emerging-topic discovery before issues become trends",
      ],
    },
    {
      glyph: "Coach",
      name: "Coach",
      role: "Agent development",
      description:
        "Coach translates analysis into growth. It builds a strengths-and-gaps profile for each agent from their own real calls, then produces specific, transcript-backed coaching recommendations a supervisor can deliver in the next one-on-one. Coach celebrates what's working as deliberately as it flags what isn't.",
      capabilities: [
        "Per-agent skill profiles built from real call evidence",
        "Specific coaching recommendations with example call moments",
        "Best-call highlighting for peer learning and recognition",
        "Progress tracking across coaching cycles",
      ],
    },
    {
      glyph: "Analyst",
      name: "Analyst",
      role: "Business insight and reporting",
      description:
        "Analyst aggregates everything the other agents find into the numbers leadership needs: quality trends, issue-driver rankings, sentiment movement, compliance exposure, and the operational root causes behind repeat contacts. It turns a call center's daily noise into a measurable system.",
      capabilities: [
        "Cross-call trend analysis for topics, sentiment, and scores",
        "Root-cause rollups connecting calls to products and processes",
        "Scheduled executive and operational reporting",
        "Exports and API access for your BI stack",
      ],
    },
  ],
  orchestrationEyebrow: "Orchestration",
  orchestrationTitle: "How the team works a call",
  orchestrationDescription:
    "When a call arrives, Listener validates and prepares the audio, then hands a clean recording to Scribe for transcription. The structured transcript fans out to Judge, Guardian, Scout, and Coach in parallel — each analyzing from its own specialty and citing the transcript moments behind every finding. Where agents disagree — for example, Judge scores a call well but Guardian flags a missed disclosure — the conflict is surfaced for human review rather than silently averaged away. Analyst collects the verified results from every call into trends, dashboards, and reports. The whole pass completes in minutes, and every conclusion traces back to evidence you can hear for yourself.",
  orchestrationSrHeading: "How the agents collaborate on a call",
  ctaTitle: "Put the team on your next call",
  ctaSub:
    "Bring a sample recording to a 30-minute walkthrough and watch all seven agents work it — scores, flags, and coaching notes, with evidence attached.",
};

const fa: AiAgentsCopy = {
  heroEyebrow: "عامل‌های هوشمند",
  heroH1: "با تیمی آشنا شوید که به هر تماس گوش می‌دهد",
  heroSub:
    "همایار یک مدل واحد نیست که صدا را خلاصه کند. هفت عامل هوشمند تخصصی است — هر کدام با نقشی مشخص، هر کدام با ذکر شواهد، هر کدام کار دیگری را بازبینی می‌کند — که روی تک‌تک تماس‌ها با هم همکاری می‌کنند.",
  demoCta: "درخواست دمو",
  pipelineCta: "مشاهده خط پردازش",
  rosterEyebrow: "تیم عامل‌ها",
  rosterTitle: "هفت متخصص، یک استاندارد واحد برای شواهد",
  rosterDescription:
    "هر عامل نقشی مشخص و خروجی‌ای مشخص دارد. هیچ‌کدام به‌صورت کلی و انتزاعی خلاصه نمی‌کنند — هر یافته به همان لحظه‌ای از رونوشت که آن را پشتیبانی می‌کند پیوند می‌خورد.",
  rosterSrHeading: "هفت عامل هوشمند همایار",
  agents: [
    {
      glyph: "Listener",
      name: "شنونده",
      role: "دریافت صدا و کیفیت سیگنال",
      description:
        "شنونده نخستین عامل در هر تماس است. فایل ضبط‌شده را اعتبارسنجی می‌کند، گوینده‌ها را تفکیک می‌کند، زمان انتظار، سکوت، هم‌زمان‌گویی و نقص‌های صوتی را تشخیص می‌دهد و تماس را برای تحلیل دقیق در مراحل بعدی آماده می‌کند. اگر فایلی غیرقابل‌استفاده باشد، شنونده آن را علامت‌گذاری می‌کند تا داده بد، معیارهای شما را آلوده نکند.",
      capabilities: [
        "تفکیک گوینده و نگاشت کانال (اپراتور در برابر مشتری)",
        "تشخیص انتظار، سکوت، فضای خاموش و هم‌زمان‌گویی",
        "اعتبارسنجی کیفیت صدا و علامت‌گذاری فایل‌های غیرقابل‌استفاده",
        "بخش‌بندی تماس برای مکالمه‌های طولانی یا انتقال‌یافته",
      ],
    },
    {
      glyph: "Scribe",
      name: "رونویس",
      role: "رونویسی و ساختاردهی",
      description:
        "رونویس، گفتار را به یک رونوشت دقیق، زمان‌دار و منتسب به گوینده تبدیل می‌کند. واژگان تخصصی، اعداد و نام‌ها را با دقت مدیریت می‌کند و مکالمه را به مراحلی — آغاز، نیازسنجی، حل مسئله، جمع‌بندی — ساختاردهی می‌کند که سایر عامل‌ها بر پایه آن کار می‌کنند.",
      capabilities: [
        "تبدیل گفتار به متن با دقت بالا، همراه با زمان‌نشان و برچسب گوینده",
        "واژگان سفارشی برای محصولات، طرح‌ها و اصطلاحات صنعتی",
        "تشخیص مراحل مکالمه (آغاز، نیازسنجی، حل مسئله، جمع‌بندی)",
        "حذف خودکار داده حساس مانند شماره کارت",
      ],
    },
    {
      glyph: "Judge",
      name: "داور",
      role: "امتیازدهی کیفیت",
      description:
        "داور هر تماس را بر اساس کارت امتیاز کیفیت شما ارزیابی می‌کند — همان معیارهایی که بازبین‌های انسانی شما امروز استفاده می‌کنند. هر معیار را با شواهد متنیِ پیوندخورده امتیاز می‌دهد و معیارها را به‌صورت یکسان روی هزاران تماس اعمال می‌کند و انحراف بازبین را از میان می‌برد.",
      capabilities: [
        "ارزیابی کارت امتیاز کیفیت با ذکر شواهد برای هر معیار",
        "امتیازدهی یکدست و کالیبره‌شده با استانداردهای تیم کنترل کیفیت شما",
        "نشانه‌های اطمینان که تماس‌های مرزی را به بازبینی انسانی هدایت می‌کنند",
        "پیگیری روند امتیاز برای هر اپراتور، تیم و صف",
      ],
    },
    {
      glyph: "Guardian",
      name: "نگهبان",
      role: "انطباق و ریسک",
      description:
        "نگهبان هر تماس را از نظر انطباق با مقررات و سیاست‌ها بررسی می‌کند: افشاهای الزامی، احراز هویت، زبان ممنوعه و تعهدات رسیدگی به شکایت. یافته‌های با شدت بالا در همان روز و همراه با شواهد کامل ارجاع داده می‌شوند.",
      capabilities: [
        "قواعد انطباق قابل پیکربندی و راستی‌آزمایی متن الزامی گفتار",
        "تشخیص عبارت‌های ممنوعه و زبان پرخطر",
        "شناسایی شکایت، اختلاف و ارجاع",
        "هشدار در همان روز با مسیرهای شواهد آماده برای حسابرسی",
      ],
    },
    {
      glyph: "Scout",
      name: "کاوشگر",
      role: "تشخیص مسئله و سیگنال",
      description:
        "کاوشگر در پی آن چیزی است که مشتریان واقعاً به شما می‌گویند: مشکلات محصول، نقص‌های فرایند، نشانه‌های ریزش مشتری، اشاره به رقبا و موضوعات نوظهور شکایت. هر تماس را برچسب می‌زند تا الگوها در میان هزاران مکالمه قابل مشاهده شوند.",
      capabilities: [
        "دسته‌بندی مسئله و قصد در هر تماس",
        "تشخیص نشانه‌های ریسک ریزش مشتری و نارضایتی",
        "پیگیری اشاره به رقبا و قیمت‌گذاری",
        "کشف موضوعات نوظهور پیش از آنکه مسائل به روند تبدیل شوند",
      ],
    },
    {
      glyph: "Coach",
      name: "مربی",
      role: "توسعه اپراتور",
      description:
        "مربی، تحلیل را به رشد تبدیل می‌کند. برای هر اپراتور بر پایه تماس‌های واقعی خودش نیم‌رخی از نقاط قوت و کاستی‌ها می‌سازد، سپس توصیه‌های مربی‌گری مشخص و مستند به رونوشت تولید می‌کند که سرپرست می‌تواند در جلسه بعدی ارائه دهد. مربی همان‌قدر که کاستی‌ها را علامت می‌زند، به‌عمد آنچه را خوب کار می‌کند نیز ارج می‌نهد.",
      capabilities: [
        "نیم‌رخ مهارت برای هر اپراتور بر پایه شواهد تماس‌های واقعی",
        "توصیه‌های مربی‌گری مشخص همراه با لحظه‌های نمونه از تماس‌ها",
        "برجسته‌سازی بهترین تماس‌ها برای یادگیری همتا و قدردانی",
        "پیگیری پیشرفت در طول دوره‌های مربی‌گری",
      ],
    },
    {
      glyph: "Analyst",
      name: "تحلیل‌گر",
      role: "بینش کسب‌وکار و گزارش‌دهی",
      description:
        "تحلیل‌گر هر آنچه را سایر عامل‌ها می‌یابند به اعدادی که مدیریت به آن نیاز دارد جمع می‌بندد: روندهای کیفیت، رتبه‌بندی عوامل ایجاد مسئله، تغییرات احساسات، میزان مواجهه با ریسک انطباق و ریشه‌های عملیاتی پشتِ تماس‌های تکراری. این عامل، هیاهوی روزانه یک مرکز تماس را به یک سامانه قابل اندازه‌گیری تبدیل می‌کند.",
      capabilities: [
        "تحلیل روند میان‌تماسی برای موضوعات، احساسات و امتیازها",
        "جمع‌بندی ریشه‌ای که تماس‌ها را به محصولات و فرایندها متصل می‌کند",
        "گزارش‌دهی زمان‌بندی‌شده مدیریتی و عملیاتی",
        "خروجی‌گیری و دسترسی API برای سامانه هوش تجاری (BI) شما",
      ],
    },
  ],
  orchestrationEyebrow: "هماهنگ‌سازی",
  orchestrationTitle: "تیم چگونه یک تماس را پیش می‌برد",
  orchestrationDescription:
    "وقتی تماسی می‌رسد، شنونده صدا را اعتبارسنجی و آماده می‌کند، سپس فایل ضبط‌شده تمیز را برای رونویسی به رونویس می‌سپارد. رونوشت ساختاریافته به‌صورت موازی به داور، نگهبان، کاوشگر و مربی پخش می‌شود — هر کدام از منظر تخصص خود تحلیل می‌کنند و لحظه‌های رونوشت پشتِ هر یافته را ذکر می‌کنند. هرجا که عامل‌ها اختلاف نظر دارند — برای مثال داور تماسی را خوب امتیاز می‌دهد اما نگهبان یک افشای ازقلم‌افتاده را علامت می‌زند — این تعارض به‌جای میانگین‌گیری خاموش، برای بازبینی انسانی آشکار می‌شود. تحلیل‌گر نتایج تأییدشده هر تماس را در قالب روندها، داشبوردها و گزارش‌ها گرد می‌آورد. کل این چرخه در عرض چند دقیقه کامل می‌شود و هر نتیجه‌گیری به شواهدی که می‌توانید خودتان بشنوید بازمی‌گردد.",
  orchestrationSrHeading: "عامل‌ها چگونه روی یک تماس همکاری می‌کنند",
  ctaTitle: "تیم را روی تماس بعدی‌تان بگمارید",
  ctaSub:
    "یک فایل ضبط‌شده نمونه به جلسه ۳۰ دقیقه‌ای بیاورید و تماشا کنید هر هفت عامل روی آن کار می‌کنند — امتیازها، هشدارها و یادداشت‌های مربی‌گری، همراه با شواهد ضمیمه‌شده.",
};

const DICT: Record<Locale, AiAgentsCopy> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

const TONES = [
  {
    text: "text-accent",
    badge: "border-accent/30 bg-accent/10",
    check: "text-accent",
  },
  {
    text: "text-violet-soft",
    badge: "border-violet/30 bg-violet/10",
    check: "text-violet-soft",
  },
  {
    text: "text-amber",
    badge: "border-amber/30 bg-amber/10",
    check: "text-amber",
  },
  {
    text: "text-teal",
    badge: "border-teal/30 bg-teal/10",
    check: "text-teal",
  },
] as const;

export function AiAgentsContent() {
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
            {c.pipelineCta}
          </ButtonLink>
        </div>
      </PageHero>

      {/* ── Agent roster ── */}
      <section aria-labelledby="agents-heading" className="py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow={c.rosterEyebrow}
            title={c.rosterTitle}
            description={c.rosterDescription}
          />
          <h2 id="agents-heading" className="sr-only">
            {c.rosterSrHeading}
          </h2>

          <div className="mt-12 grid gap-6 md:grid-cols-2 xl:grid-cols-3">
            {c.agents.map((agent, i) => {
              const tone = TONES[i % TONES.length];
              return (
                <article
                  key={agent.glyph}
                  className="card-surface flex flex-col rounded-xl p-6"
                >
                  <div className="flex items-center gap-4">
                    <div
                      className={`inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border ${tone.badge} ${tone.text}`}
                    >
                      <AgentGlyph name={agent.glyph} />
                    </div>
                    <div>
                      <h3 className="font-display text-xl font-semibold tracking-tight text-ink">
                        {agent.name}
                      </h3>
                      <p
                        className={`text-xs font-semibold uppercase tracking-[0.14em] ${tone.text}`}
                      >
                        {agent.role}
                      </p>
                    </div>
                  </div>

                  <p className="mt-4 text-sm leading-relaxed text-ink-soft">
                    {agent.description}
                  </p>

                  <ul className="mt-5 space-y-2.5 border-t border-line pt-5">
                    {agent.capabilities.map((capability) => (
                      <li
                        key={capability}
                        className="flex items-start gap-2.5 text-sm leading-snug text-ink-soft"
                      >
                        <CheckIcon
                          width={16}
                          height={16}
                          className={`mt-0.5 shrink-0 ${tone.check}`}
                        />
                        {capability}
                      </li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* ── Orchestration ── */}
      <section
        aria-labelledby="orchestration-heading"
        className="border-y border-line bg-surface py-16 sm:py-24"
      >
        <div className="container-site">
          <div className="grid items-center gap-12 lg:grid-cols-2 lg:gap-16">
            <div>
              <SectionHeading
                eyebrow={c.orchestrationEyebrow}
                title={c.orchestrationTitle}
                description={c.orchestrationDescription}
              />
              <h2 id="orchestration-heading" className="sr-only">
                {c.orchestrationSrHeading}
              </h2>
            </div>
            <OrchestrationDiagram />
          </div>
        </div>
      </section>

      {/* ── CTA band ── */}
      <section
        aria-labelledby="agents-cta-heading"
        className="py-16 sm:py-20"
      >
        <div className="container-site">
          <div className="mx-auto max-w-2xl text-center">
            <h2
              id="agents-cta-heading"
              className="font-display text-3xl font-semibold tracking-tight text-ink sm:text-4xl"
            >
              {c.ctaTitle}
            </h2>
            <p className="mt-4 text-lg leading-relaxed text-ink-muted">
              {c.ctaSub}
            </p>
            <div className="mt-8 flex justify-center">
              <ButtonLink href="/contact" size="lg">
                {c.demoCta}
                <ArrowRightIcon width={18} height={18} />
              </ButtonLink>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
