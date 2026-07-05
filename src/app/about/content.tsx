"use client";

import { ButtonLink, Eyebrow, PageHero, SectionHeading } from "@/components/ui";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware copy for the About page. English is the canonical source; `fa`
 * is a direct Persian translation. Other locales fall back to English. About
 * content is inlined here (mirrored from content/faq-about.json) to avoid
 * refactoring the shared data layer. `missionBody` is stored as an array of
 * paragraphs: the first is the hero lead, the rest fill the mission section —
 * matching the original split-on-blank-line behavior.
 */
type Value = { name: string; description: string };
type AboutCopy = {
  heroEyebrow: string;
  missionHeadline: string;
  missionBody: string[];
  missionEyebrow: string;
  missionTitle: string;
  nameEyebrow: string;
  nameStory: string;
  nameWordmark: string;
  nameHoma: string;
  nameYar: string;
  nameCaption: string;
  valuesEyebrow: string;
  valuesTitle: string;
  values: Value[];
  companyEyebrow: string;
  companyTitle: string;
  companyNote: string;
  ctaTitle: string;
  ctaBody: string;
  demoCta: string;
  caseStudiesCta: string;
};

const en: AboutCopy = {
  heroEyebrow: "About Homayar",
  missionHeadline: "Every call has a companion.",
  missionBody: [
    "Call centers generate thousands of hours of conversation every week, and almost all of it goes unheard. Traditional quality assurance samples 1 to 2 percent of calls, scores them days later, and turns coaching into an argument about a spreadsheet. The customer's actual voice — what frustrated them, what convinced them, what they asked for and never got — disappears into storage. Homayar exists to close that gap: a team of specialized AI agents that listens to every call, scores it against your own quality standards, surfaces the issues that matter, and turns raw conversation into decisions your operation can act on the same day.",
    "We built Homayar on a conviction that shapes every product choice we make: analysis should be a companion, not a camera. The goal is not to catch agents failing — it is to show every agent, supervisor, and operations leader exactly what is happening across 100 percent of their conversations, with the evidence to back it up. When coaching is grounded in real moments instead of gut feel, agents improve faster, supervisors spend their time where it counts, and the call center becomes what it should have been all along: measurable.",
  ],
  missionEyebrow: "Our mission",
  missionTitle: "A companion, not a camera",
  nameEyebrow: "The name",
  nameStory:
    "The name carries two ideas. Homa is the fortune-bird of Persian mythology — a creature said never to land, flying unseen above, and bringing good fortune to anyone its shadow touches. Yar is the Persian word for companion: the one who stands beside you, not over you. Together they describe exactly what we set out to build at KSF Company — an intelligence that moves quietly above every conversation, touches each one, and leaves the people below better off for it. Not a watcher. A companion that brings fortune to every call.",
  nameWordmark: "همایار",
  nameHoma: "Homa",
  nameYar: "Yar",
  nameCaption: "the fortune-bird and the companion",
  valuesEyebrow: "What we stand for",
  valuesTitle: "The values behind every product decision",
  values: [
    {
      name: "Companion, not surveillance",
      description:
        "We build for the agent on the phone as much as for the leader reading the dashboard. Features that help people improve ship; features that exist only to police people do not.",
    },
    {
      name: "Evidence over opinion",
      description:
        "Every score, flag, and recommendation traces back to a specific moment in a real conversation. If a number cannot be audited to its source, it does not belong in the product.",
    },
    {
      name: "Precision before scale",
      description:
        "An analysis platform is only as valuable as it is trustworthy. We calibrate against your QA team, publish our accuracy ranges, and tune until the numbers hold — then we scale.",
    },
    {
      name: "Privacy by design",
      description:
        "PII redaction, configurable retention, role-scoped visibility, and works-council-ready controls are core architecture, not enterprise add-ons. Customer data is analyzed, protected, and deleted on your terms.",
    },
  ],
  companyEyebrow: "The company",
  companyTitle: "Built by KSF Company",
  companyNote:
    "Homayar is built by KSF Company, a software engineering firm focused on applied AI for enterprise operations. Our team combines production machine-learning infrastructure experience with deep familiarity with how contact centers actually run — the queues, the rubrics, the coaching cycles, and the compliance realities. We work directly with every enterprise customer through onboarding, calibration, and rollout. To talk to the team about a pilot, deployment, or partnership, write to info@ksf.ir.",
  ctaTitle: "Put a companion on every call",
  ctaBody:
    "Book a 30-minute walkthrough with the team, or see the results enterprise call centers have measured with Homayar.",
  demoCta: "Request a demo",
  caseStudiesCta: "Read case studies",
};

const fa: AboutCopy = {
  heroEyebrow: "درباره همایار",
  missionHeadline: "هر تماس، یک همراه دارد.",
  missionBody: [
    "مراکز تماس هر هفته هزاران ساعت مکالمه تولید می‌کنند و تقریباً همه آن ناشنیده می‌ماند. کنترل کیفیت سنتی تنها ۱ تا ۲ درصد از تماس‌ها را نمونه‌گیری می‌کند، روزها بعد به آن‌ها امتیاز می‌دهد و مربی‌گری را به جدلی بر سر یک صفحه‌گسترده تبدیل می‌کند. صدای واقعی مشتری — آنچه او را آزرده، آنچه او را متقاعد کرده، آنچه خواسته و هرگز نگرفته است — در انبارِ داده‌ها ناپدید می‌شود. همایار برای پر کردن همین شکاف به وجود آمده است: تیمی از عامل‌های هوشمند تخصصی که به هر تماس گوش می‌دهد، آن را بر اساس استانداردهای کیفیت خودِ شما امتیاز می‌دهد، مسائل مهم را آشکار می‌سازد و مکالمه خام را به تصمیم‌هایی تبدیل می‌کند که عملیات شما می‌تواند همان روز بر اساس آن‌ها عمل کند.",
    "ما همایار را بر پایه باوری ساختیم که هر انتخاب محصولی ما را شکل می‌دهد: تحلیل باید یک همراه باشد، نه یک دوربین. هدف، مچ‌گیری از اپراتورها در هنگام خطا نیست — بلکه نشان دادن دقیق این است که در سراسر ۱۰۰ درصد مکالمه‌ها چه می‌گذرد، به هر اپراتور، سرپرست و مدیر عملیات، همراه با شواهدی که آن را پشتیبانی می‌کند. وقتی مربی‌گری به‌جای حسِ درونی بر لحظات واقعی استوار باشد، اپراتورها سریع‌تر پیشرفت می‌کنند، سرپرست‌ها وقتشان را جایی که اهمیت دارد صرف می‌کنند و مرکز تماس همان چیزی می‌شود که از ابتدا باید می‌بود: قابل اندازه‌گیری.",
  ],
  missionEyebrow: "مأموریت ما",
  missionTitle: "یک همراه، نه یک دوربین",
  nameEyebrow: "نام",
  nameStory:
    "این نام دو ایده را در خود دارد. هما پرنده فرخندگی در اسطوره‌های ایرانی است — موجودی که گفته می‌شود هرگز فرود نمی‌آید، ناپیدا در بالا پرواز می‌کند و به هر کس که سایه‌اش بر او بیفتد خوش‌بختی می‌بخشد. یار واژه فارسیِ همراه است: آن‌که در کنار تو می‌ایستد، نه بالای سرِ تو. این دو با هم دقیقاً همان چیزی را توصیف می‌کنند که ما در کهن سیستم فردا در پی ساختنش بودیم — هوشمندی‌ای که آرام بر فراز هر مکالمه حرکت می‌کند، هر یک را لمس می‌کند و کسانی را که در پایین‌اند بهره‌مندتر رها می‌کند. نه یک ناظر. همراهی که به هر تماس فرخندگی می‌آورد.",
  nameWordmark: "همایار",
  nameHoma: "هما",
  nameYar: "یار",
  nameCaption: "پرنده فرخندگی و همراه",
  valuesEyebrow: "آنچه به آن باور داریم",
  valuesTitle: "ارزش‌های پشتِ هر تصمیم محصولی",
  values: [
    {
      name: "همراه، نه نظارت",
      description:
        "ما به همان اندازه که برای مدیرِ خواننده داشبورد می‌سازیم، برای اپراتورِ پشت تلفن هم می‌سازیم. ویژگی‌هایی که به پیشرفت افراد کمک می‌کنند منتشر می‌شوند؛ ویژگی‌هایی که تنها برای کنترلِ افراد وجود دارند، نه.",
    },
    {
      name: "شواهد، نه عقیده",
      description:
        "هر امتیاز، نشانه و توصیه به لحظه‌ای مشخص در یک مکالمه واقعی بازمی‌گردد. اگر عددی نتواند تا منبعش حسابرسی شود، جایی در محصول ندارد.",
    },
    {
      name: "دقت پیش از مقیاس",
      description:
        "یک پلتفرم تحلیل، تنها به اندازه‌ای ارزش دارد که قابل اعتماد باشد. ما بر اساس تیم کنترل کیفیت شما کالیبره می‌کنیم، بازه‌های دقتمان را منتشر می‌کنیم و تا زمانی که اعداد پایدار شوند تنظیم می‌کنیم — سپس مقیاس می‌دهیم.",
    },
    {
      name: "حریم خصوصی از پایه طراحی",
      description:
        "حذف داده‌های شخصی، نگهداری داده قابل پیکربندی، نمایش محدود به نقش و کنترل‌های آماده برای شورای کارکنان، بخشی از معماری اصلی‌اند، نه افزونه‌های سازمانی. داده مشتری بر اساس شرایط شما تحلیل، محافظت و حذف می‌شود.",
    },
  ],
  companyEyebrow: "شرکت",
  companyTitle: "ساخته‌شده توسط کهن سیستم فردا",
  companyNote:
    "همایار توسط کهن سیستم فردا ساخته شده است، یک شرکت مهندسی نرم‌افزار با تمرکز بر هوش مصنوعی کاربردی برای عملیات سازمانی. تیم ما تجربه زیرساخت یادگیری ماشینِ تولیدی را با آشنایی عمیق با شیوه واقعی کار مراکز تماس ترکیب می‌کند — صف‌ها، معیارها، چرخه‌های مربی‌گری و واقعیت‌های انطباق با مقررات. ما با هر مشتری سازمانی به‌طور مستقیم در راه‌اندازی، کالیبراسیون و استقرار همکاری می‌کنیم. برای گفت‌وگو با تیم درباره یک پایلوت، استقرار یا مشارکت، به info@ksf.ir بنویسید.",
  ctaTitle: "روی هر تماس، یک همراه بگذارید",
  ctaBody:
    "یک جلسه ۳۰ دقیقه‌ای با تیم رزرو کنید، یا نتایجی را ببینید که مراکز تماس سازمانی با همایار اندازه گرفته‌اند.",
  demoCta: "درخواست دمو",
  caseStudiesCta: "خواندن مطالعات موردی",
};

const DICT: Record<Locale, AboutCopy> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

function CompanyNote({ note }: { note: string }) {
  const email = "info@ksf.ir";
  const index = note.indexOf(email);
  if (index === -1) {
    return <p className="leading-relaxed text-ink-soft">{note}</p>;
  }
  return (
    <p className="leading-relaxed text-ink-soft">
      {note.slice(0, index)}
      <a
        href={`mailto:${email}`}
        className="font-medium text-accent underline underline-offset-4 hover:text-accent-strong"
      >
        {email}
      </a>
      {note.slice(index + email.length)}
    </p>
  );
}

export function AboutContent() {
  const { locale } = useLocale();
  const c = DICT[locale] ?? en;

  const [missionLead, ...missionRest] = c.missionBody;

  return (
    <>
      <PageHero
        eyebrow={c.heroEyebrow}
        title={c.missionHeadline}
        description={missionLead}
      />

      {/* Mission */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading eyebrow={c.missionEyebrow} title={c.missionTitle} />
          <div className="mt-8 max-w-3xl space-y-6">
            {missionRest.map((paragraph) => (
              <p
                key={paragraph.slice(0, 40)}
                className="text-lg leading-relaxed text-ink-soft"
              >
                {paragraph}
              </p>
            ))}
          </div>
        </div>
      </section>

      {/* The name */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <figure className="relative overflow-hidden rounded-2xl border border-line bg-surface px-6 py-12 sm:px-12 sm:py-16">
            <span
              aria-hidden
              className="pointer-events-none absolute -top-6 right-2 select-none font-display text-[6rem] font-semibold leading-none text-accent/10 sm:-top-10 sm:text-[11rem]"
            >
              {c.nameWordmark}
            </span>
            <div className="relative">
              <Eyebrow>{c.nameEyebrow}</Eyebrow>
              <blockquote className="max-w-3xl font-display text-xl leading-relaxed tracking-tight text-ink sm:text-2xl">
                {c.nameStory}
              </blockquote>
              <figcaption className="mt-6 flex items-center gap-3 text-sm text-ink-muted">
                <span className="font-mono text-accent">{c.nameHoma}</span>
                <span aria-hidden>+</span>
                <span className="font-mono text-accent">{c.nameYar}</span>
                <span aria-hidden className="h-px w-8 bg-line-strong" />
                <span>{c.nameCaption}</span>
              </figcaption>
            </div>
          </figure>
        </div>
      </section>

      {/* Values */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <SectionHeading
            eyebrow={c.valuesEyebrow}
            title={c.valuesTitle}
          />
          <div className="mt-10 grid gap-6 sm:grid-cols-2">
            {c.values.map((value, index) => (
              <article key={value.name} className="card-surface rounded-2xl p-6 sm:p-8">
                <p className="font-mono text-sm text-accent">
                  {String(index + 1).padStart(2, "0")}
                </p>
                <h3 className="mt-3 font-display text-xl font-semibold tracking-tight text-ink">
                  {value.name}
                </h3>
                <p className="mt-3 leading-relaxed text-ink-soft">
                  {value.description}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Company note */}
      <section className="border-b border-line py-16 sm:py-24">
        <div className="container-site">
          <div className="grid gap-10 lg:grid-cols-[1fr_2fr] lg:gap-16">
            <SectionHeading eyebrow={c.companyEyebrow} title={c.companyTitle} />
            <div className="max-w-3xl lg:pt-2">
              <CompanyNote note={c.companyNote} />
            </div>
          </div>
        </div>
      </section>

      {/* CTA band */}
      <section className="py-16 sm:py-24">
        <div className="container-site">
          <div className="relative overflow-hidden rounded-2xl border border-line bg-surface px-6 py-12 text-center sm:px-12 sm:py-16">
            <div
              aria-hidden
              className="pointer-events-none absolute inset-0"
              style={{
                background:
                  "radial-gradient(50% 80% at 50% 0%, rgba(34,211,238,0.12), transparent 70%)",
              }}
            />
            <div className="relative mx-auto max-w-2xl">
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
                <ButtonLink href="/case-studies" variant="secondary" size="lg">
                  {c.caseStudiesCta}
                </ButtonLink>
              </div>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
