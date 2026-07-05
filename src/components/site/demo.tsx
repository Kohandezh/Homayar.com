"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import Link from "next/link";
import { DIR, type Locale } from "@/lib/content";

/* ──────────────────────────── tiny inline icons ──────────────────────────── */
function IconWave({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 24" className={className} aria-hidden fill="none">
      {[4, 11, 18, 25, 32, 39].map((x, i) => {
        const h = [10, 18, 6, 22, 12, 16][i];
        return <rect key={x} x={x} y={12 - h / 2} width="4" height={h} rx="2" fill="currentColor" />;
      })}
    </svg>
  );
}
function IconPlay({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <path d="M8 5.5v13a1 1 0 0 0 1.54.84l10-6.5a1 1 0 0 0 0-1.68l-10-6.5A1 1 0 0 0 8 5.5Z" />
    </svg>
  );
}
function IconReplay({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path d="M4 4v6h6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M4 10a8 8 0 1 1-1 6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconCheck({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path d="m5 12.5 4.5 4.5L19 7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconSpark({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path d="M12 3v4m0 10v4M3 12h4m10 0h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
function IconBack({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path d="M15 6l-6 6 6 6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ──────────────────────────────── content ───────────────────────────────── */
type Turn = {
  who: "agent" | "customer";
  text: string;
  /** Cumulative sentiment after this turn, 0 (negative) … 100 (positive). */
  sentiment: number;
  /** A topic/intent chip that gets "detected" on this turn. */
  topic?: string;
};
type Scenario = {
  id: string;
  label: string;
  blurb: string;
  intent: string;
  qa: number;
  risk: { label: string; level: "low" | "med" | "high" };
  turns: Turn[];
  insights: string[];
};

type Copy = {
  brand: string;
  back: string;
  langName: string;
  badge: string;
  h1a: string;
  h1b: string;
  sub: string;
  pickScenario: string;
  play: string;
  playing: string;
  replay: string;
  live: string;
  agent: string;
  customer: string;
  sentiment: string;
  neg: string;
  neu: string;
  pos: string;
  topics: string;
  intent: string;
  qa: string;
  risk: string;
  insights: string;
  waitingTitle: string;
  waitingBody: string;
  perksTitle: string;
  perks: { title: string; body: string }[];
  formTitle: string;
  formSub: string;
  name: string;
  email: string;
  company: string;
  teamSize: string;
  message: string;
  submit: string;
  sending: string;
  success: string;
  error: string;
  disclaimer: string;
  rights: string;
};

function content(locale: Locale): { c: Copy; scenarios: Scenario[] } {
  if (locale === "en") {
    const c: Copy = {
      brand: "Homayar",
      back: "Back to site",
      langName: "فارسی",
      badge: "Live interactive demo",
      h1a: "Watch Homayar ",
      h1b: "understand a conversation",
      sub: "Pick a call and press play. Homayar transcribes it and, in real time, reads sentiment, detects topics and intent, scores quality, and surfaces the actions your team should take — exactly what it does on every real call.",
      pickScenario: "Choose a call",
      play: "Analyze this call",
      playing: "Listening…",
      replay: "Replay",
      live: "LIVE",
      agent: "Agent",
      customer: "Customer",
      sentiment: "Sentiment",
      neg: "Negative",
      neu: "Neutral",
      pos: "Positive",
      topics: "Topics & intent",
      intent: "Primary intent",
      qa: "Quality score",
      risk: "Signal",
      insights: "Insights & next actions",
      waitingTitle: "Ready when you are",
      waitingBody: "Choose a call above and press analyze — the transcript and live insights appear here.",
      perksTitle: "In a personalized demo you'll get",
      perks: [
        { title: "Your own calls analyzed", body: "Bring a recording and watch Homayar score it and surface insights on your real data." },
        { title: "A tailored walkthrough", body: "30 minutes on the modules that matter for your team — QA, coaching, churn, or revenue." },
        { title: "A clear rollout plan", body: "Integration, security, and a path to value mapped to your contact-center stack." },
      ],
      formTitle: "Book a personalized demo",
      formSub: "Tell us a little about your team and we'll set up a walkthrough on your own conversations.",
      name: "Full name",
      email: "Work email",
      company: "Company",
      teamSize: "Team size",
      message: "Anything we should know?",
      submit: "Request my demo",
      sending: "Sending…",
      success: "Thanks — we'll be in touch shortly to schedule your demo.",
      error: "Something went wrong. Please email info@ksf.ir.",
      disclaimer: "This is a simulated illustration of Homayar's analysis on sample calls.",
      rights: "© Homayar — Kohan System Farda. All rights reserved.",
    };
    const scenarios: Scenario[] = [
      {
        id: "support",
        label: "Support call",
        blurb: "A frustrated customer with a failed payment",
        intent: "Resolve a failed payment",
        qa: 92,
        risk: { label: "Recovered", level: "low" },
        turns: [
          { who: "customer", text: "I've been charged twice and my subscription still isn't active. This is the third time I'm calling.", sentiment: 18, topic: "Billing" },
          { who: "agent", text: "I'm really sorry about that. Let me pull up your account right now and make it right.", sentiment: 30 },
          { who: "customer", text: "I just need the duplicate charge refunded and my account working.", sentiment: 34, topic: "Refund" },
          { who: "agent", text: "I can see the duplicate. I've reversed it — you'll see it back in 2–3 days — and I've reactivated your plan.", sentiment: 62, topic: "Account access" },
          { who: "customer", text: "Oh, that was fast. Thank you, I appreciate it.", sentiment: 84 },
          { who: "agent", text: "Of course. I've also added a note so this can't happen again. Anything else I can help with?", sentiment: 88 },
        ],
        insights: [
          "Duplicate-charge refund issued and plan reactivated within the call.",
          "Recommend a proactive follow-up email confirming the refund timeline.",
          "Root cause: retry on a timed-out payment — flag for the billing team.",
        ],
      },
      {
        id: "sales",
        label: "Sales call",
        blurb: "A prospect evaluating the enterprise plan",
        intent: "Evaluate enterprise plan",
        qa: 88,
        risk: { label: "High intent", level: "low" },
        turns: [
          { who: "customer", text: "We run about 4,000 calls a week across three teams. Can you handle that volume?", sentiment: 58, topic: "Scale" },
          { who: "agent", text: "Easily — the enterprise tier is built for that, with dedicated throughput and no per-seat caps.", sentiment: 64 },
          { who: "customer", text: "Security is the big one for us. Where does the data live, and is it redacted?", sentiment: 55, topic: "Security" },
          { who: "agent", text: "PII and PCI are redacted before analysis, and we can deploy in your own environment.", sentiment: 70, topic: "Compliance" },
          { who: "customer", text: "That's exactly what we need. How fast could we be live?", sentiment: 82, topic: "Onboarding" },
          { who: "agent", text: "Most teams are running in two weeks. I'll send a tailored rollout plan today.", sentiment: 86 },
        ],
        insights: [
          "Strong buying signal — asked about timeline and rollout, not price.",
          "Security and on-prem deployment are the key decision criteria.",
          "Next action: send enterprise rollout plan and book a security review.",
        ],
      },
      {
        id: "churn",
        label: "At-risk call",
        blurb: "A customer threatening to cancel",
        intent: "Prevent cancellation",
        qa: 76,
        risk: { label: "Churn risk", level: "high" },
        turns: [
          { who: "customer", text: "Honestly, I'm thinking about cancelling. The reports are too hard to get anything out of.", sentiment: 16, topic: "Churn risk" },
          { who: "agent", text: "I hear you, and I don't want to lose you over that. What were you trying to find?", sentiment: 28 },
          { who: "customer", text: "Just which agents need coaching. I shouldn't have to dig for it.", sentiment: 30, topic: "Coaching" },
          { who: "agent", text: "That's a one-click view — let me turn on the coaching dashboard and walk you through it.", sentiment: 52, topic: "Onboarding" },
          { who: "customer", text: "If it's that simple, that changes things. Okay, show me.", sentiment: 68 },
          { who: "agent", text: "Done. I'll also schedule a 15-minute check-in next week so you're getting full value.", sentiment: 74 },
        ],
        insights: [
          "Churn risk flagged on the opening sentence — escalate to success team.",
          "Cause is feature discovery, not product fit — recoverable with onboarding.",
          "Next action: enable coaching dashboard and book a check-in within 7 days.",
        ],
      },
    ];
    return { c, scenarios };
  }

  // Persian (default)
  const c: Copy = {
    brand: "همایار",
    back: "بازگشت به سایت",
    langName: "English",
    badge: "دموی تعاملی زنده",
    h1a: "ببینید همایار چطور یک ",
    h1b: "مکالمه را می‌فهمد",
    sub: "یک تماس را انتخاب کنید و دکمه پخش را بزنید. همایار آن را پیاده‌سازی می‌کند و هم‌زمان احساس، موضوع و قصد را تشخیص می‌دهد، کیفیت را امتیاز می‌دهد و اقدام‌های لازم را پیشنهاد می‌کند — دقیقاً همان کاری که روی هر تماس واقعی انجام می‌دهد.",
    pickScenario: "یک تماس را انتخاب کنید",
    play: "تحلیل این تماس",
    playing: "در حال شنیدن…",
    replay: "پخش دوباره",
    live: "زنده",
    agent: "کارشناس",
    customer: "مشتری",
    sentiment: "احساس",
    neg: "منفی",
    neu: "خنثی",
    pos: "مثبت",
    topics: "موضوع و قصد",
    intent: "قصد اصلی",
    qa: "امتیاز کیفیت",
    risk: "سیگنال",
    insights: "بینش‌ها و اقدام بعدی",
    waitingTitle: "هر وقت آماده بودید",
    waitingBody: "یک تماس را از بالا انتخاب کنید و دکمه تحلیل را بزنید — متن گفتگو و بینش‌های زنده اینجا ظاهر می‌شود.",
    perksTitle: "در یک دموی اختصاصی دریافت می‌کنید",
    perks: [
      { title: "تحلیل تماس‌های خودتان", body: "یک فایل ضبط‌شده بیاورید و ببینید همایار روی داده واقعی شما امتیاز می‌دهد و بینش استخراج می‌کند." },
      { title: "یک نمایش سفارشی", body: "سی دقیقه روی ماژول‌هایی که برای تیم شما مهم‌اند — کنترل کیفیت، کوچینگ، ریزش یا فروش." },
      { title: "نقشهٔ راه‌اندازی روشن", body: "یکپارچه‌سازی، امنیت و مسیر رسیدن به ارزش، متناسب با مرکز تماس شما." },
    ],
    formTitle: "رزرو دموی اختصاصی",
    formSub: "کمی دربارهٔ تیم‌تان بگویید تا یک نمایش روی مکالمات خودتان ترتیب دهیم.",
    name: "نام و نام خانوادگی",
    email: "ایمیل سازمانی",
    company: "نام سازمان",
    teamSize: "اندازهٔ تیم",
    message: "نکته‌ای هست که بدانیم؟",
    submit: "درخواست دمو",
    sending: "در حال ارسال…",
    success: "ممنون — به‌زودی برای هماهنگی دمو با شما تماس می‌گیریم.",
    error: "خطایی رخ داد. لطفاً به info@ksf.ir ایمیل بزنید.",
    disclaimer: "این یک نمایش شبیه‌سازی‌شده از تحلیل همایار روی تماس‌های نمونه است.",
    rights: "© همایار — کهن سیستم فردا. همهٔ حقوق محفوظ است.",
  };
  const scenarios: Scenario[] = [
    {
      id: "support",
      label: "تماس پشتیبانی",
      blurb: "مشتری ناراضی با پرداخت ناموفق",
      intent: "رفع پرداخت ناموفق",
      qa: 92,
      risk: { label: "بازیابی‌شده", level: "low" },
      turns: [
        { who: "customer", text: "دو بار از حسابم کسر شده و اشتراکم هنوز فعال نیست. این سومین باری است که تماس می‌گیرم.", sentiment: 18, topic: "پرداخت" },
        { who: "agent", text: "واقعاً بابت این موضوع متأسفم. همین الان حساب شما را باز می‌کنم و درستش می‌کنم.", sentiment: 30 },
        { who: "customer", text: "فقط می‌خواهم مبلغ اضافه برگردد و حسابم کار کند.", sentiment: 34, topic: "بازپرداخت" },
        { who: "agent", text: "کسر تکراری را می‌بینم. آن را برگرداندم — تا ۲ تا ۳ روز کاری به حسابتان برمی‌گردد — و اشتراکتان را دوباره فعال کردم.", sentiment: 62, topic: "دسترسی حساب" },
        { who: "customer", text: "چه سریع. ممنونم، واقعاً لطف کردید.", sentiment: 84 },
        { who: "agent", text: "خواهش می‌کنم. یک یادداشت هم گذاشتم که دیگر تکرار نشود. کار دیگری هست کمک کنم؟", sentiment: 88 },
      ],
      insights: [
        "بازپرداخت کسر تکراری انجام و اشتراک در همان تماس دوباره فعال شد.",
        "ارسال یک ایمیل پیگیری برای تأیید زمان بازگشت وجه پیشنهاد می‌شود.",
        "ریشهٔ مشکل: تلاش مجدد روی پرداخت تایم‌اوت‌شده — برای تیم مالی علامت‌گذاری شد.",
      ],
    },
    {
      id: "sales",
      label: "تماس فروش",
      blurb: "مشتری بالقوه در حال بررسی پلن سازمانی",
      intent: "بررسی پلن سازمانی",
      qa: 88,
      risk: { label: "قصد خرید بالا", level: "low" },
      turns: [
        { who: "customer", text: "ما هفته‌ای حدود ۴۰۰۰ تماس در سه تیم داریم. این حجم را پوشش می‌دهید؟", sentiment: 58, topic: "مقیاس" },
        { who: "agent", text: "به‌راحتی — پلن سازمانی دقیقاً برای همین ساخته شده، با ظرفیت اختصاصی و بدون محدودیت کاربر.", sentiment: 64 },
        { who: "customer", text: "امنیت برای ما خیلی مهم است. داده کجا نگهداری می‌شود و آیا پاک‌سازی می‌شود؟", sentiment: 55, topic: "امنیت" },
        { who: "agent", text: "اطلاعات حساس و کارت پیش از تحلیل پاک‌سازی می‌شود و می‌توانیم روی بستر خودتان مستقر کنیم.", sentiment: 70, topic: "انطباق" },
        { who: "customer", text: "دقیقاً همین را می‌خواهیم. چقدر سریع می‌توانیم راه بیفتیم؟", sentiment: 82, topic: "راه‌اندازی" },
        { who: "agent", text: "بیشتر تیم‌ها در دو هفته فعال می‌شوند. امروز یک نقشهٔ راه‌اندازی سفارشی برایتان می‌فرستم.", sentiment: 86 },
      ],
      insights: [
        "سیگنال خرید قوی — به‌جای قیمت، دربارهٔ زمان و راه‌اندازی پرسید.",
        "امنیت و استقرار روی بستر اختصاصی، معیار اصلی تصمیم‌گیری است.",
        "اقدام بعدی: ارسال نقشهٔ راه‌اندازی سازمانی و هماهنگی جلسهٔ بررسی امنیت.",
      ],
    },
    {
      id: "churn",
      label: "تماس پرریسک",
      blurb: "مشتری در آستانهٔ لغو اشتراک",
      intent: "جلوگیری از لغو",
      qa: 76,
      risk: { label: "ریسک ریزش", level: "high" },
      turns: [
        { who: "customer", text: "راستش دارم به لغو اشتراک فکر می‌کنم. درآوردن نتیجه از گزارش‌ها خیلی سخت است.", sentiment: 16, topic: "ریسک ریزش" },
        { who: "agent", text: "متوجهم و نمی‌خواهم به‌خاطر این موضوع شما را از دست بدهم. دنبال چه چیزی بودید؟", sentiment: 28 },
        { who: "customer", text: "فقط اینکه کدام کارشناس‌ها به کوچینگ نیاز دارند. نباید دنبالش بگردم.", sentiment: 30, topic: "کوچینگ" },
        { who: "agent", text: "این فقط یک کلیک است — اجازه بدهید داشبورد کوچینگ را روشن کنم و نشانتان دهم.", sentiment: 52, topic: "راه‌اندازی" },
        { who: "customer", text: "اگر این‌قدر ساده است، فرق می‌کند. باشد، نشانم بدهید.", sentiment: 68 },
        { who: "agent", text: "انجام شد. هفتهٔ بعد هم یک جلسهٔ ۱۵ دقیقه‌ای می‌گذارم تا کامل از آن بهره ببرید.", sentiment: 74 },
      ],
      insights: [
        "ریسک ریزش از همان جملهٔ اول شناسایی شد — به تیم موفقیت مشتری ارجاع شود.",
        "علت، کشف امکانات است نه عدم تناسب محصول — با راه‌اندازی قابل جبران است.",
        "اقدام بعدی: فعال‌سازی داشبورد کوچینگ و هماهنگی جلسه طی ۷ روز.",
      ],
    },
  ];
  return { c, scenarios };
}

/* ─────────────────────────────── component ──────────────────────────────── */
const STEP_MS = 1500;

export function DemoExperience() {
  const [locale, setLocale] = useState<Locale>("fa");
  const dir = DIR[locale];
  const { c, scenarios } = useMemo(() => content(locale), [locale]);

  const [scenarioId, setScenarioId] = useState(scenarios[0].id);
  const scenario = scenarios.find((s) => s.id === scenarioId) ?? scenarios[0];

  // step = number of revealed turns. -1 = idle (nothing analyzed yet).
  const [step, setStep] = useState(-1);
  const [playing, setPlaying] = useState(false);
  const [formState, setFormState] = useState<"idle" | "submitting" | "success" | "error">("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    const html = document.documentElement;
    html.lang = locale;
    html.dir = dir;
  }, [locale, dir]);

  // Keep the newest turn in view as the transcript streams inside its panel.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [step]);

  // Drive the streaming playback. setState only ever happens inside the async
  // setTimeout callbacks (never synchronously in the effect body), so there is
  // no cascading-render warning.
  useEffect(() => {
    if (!playing) return;
    const last = scenario.turns.length - 1;
    if (step >= last) {
      const stop = setTimeout(() => setPlaying(false), 0);
      return () => clearTimeout(stop);
    }
    const next = setTimeout(() => setStep((s) => s + 1), STEP_MS);
    timer.current = next;
    return () => clearTimeout(next);
  }, [playing, step, scenario.turns.length]);

  function reset() {
    if (timer.current) clearTimeout(timer.current);
    setStep(-1);
    setPlaying(false);
  }

  function start() {
    const reduce =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      // Respect reduced motion: reveal everything at once.
      setStep(scenario.turns.length - 1);
      setPlaying(false);
      return;
    }
    setStep(0);
    setPlaying(true);
  }

  function pickScenario(id: string) {
    if (id === scenarioId) return;
    reset();
    setScenarioId(id);
  }

  function toggleLocale() {
    setLocale((l) => (l === "fa" ? "en" : "fa"));
  }

  const started = step >= 0;
  const finished = started && !playing && step >= scenario.turns.length - 1;
  const visibleTurns = useMemo(
    () => (started ? scenario.turns.slice(0, step + 1) : []),
    [started, scenario.turns, step],
  );
  const current = started ? scenario.turns[step] : undefined;
  const sentiment = current ? current.sentiment : 50;
  const topics = useMemo(
    () => visibleTurns.map((t) => t.topic).filter((x): x is string => !!x),
    [visibleTurns],
  );

  const sentimentTone =
    sentiment < 40 ? "danger" : sentiment < 66 ? "amber" : "success";
  const sentimentLabel =
    sentiment < 40 ? c.neg : sentiment < 66 ? c.neu : c.pos;

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    setFormState("submitting");
    try {
      const res = await fetch("/api/demo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
      if (!res.ok) throw new Error("bad status");
      setFormState("success");
      form.reset();
    } catch {
      setFormState("error");
    }
  }

  return (
    <>
      {/* ───────── header ───────── */}
      <header className="sticky top-0 z-50 border-b border-line/70 bg-bg/80 backdrop-blur-xl">
        <div className="container-site flex h-16 items-center justify-between gap-4">
          <Link href="/" className="flex items-center gap-2.5 shrink-0">
            <span className="grid size-9 place-items-center rounded-xl bg-gradient-to-br from-accent to-violet text-bg">
              <IconWave className="h-4 w-7" />
            </span>
            <span className="font-display text-xl font-bold tracking-tight text-ink">{c.brand}</span>
          </Link>
          <div className="flex items-center gap-2">
            <Link
              href="/"
              className="hidden items-center gap-1.5 rounded-lg px-3 py-2 text-sm text-ink-soft transition-colors hover:text-accent sm:flex"
            >
              <IconBack className="size-4 rtl:rotate-180" />
              {c.back}
            </Link>
            <button
              type="button"
              onClick={toggleLocale}
              className="rounded-lg border border-line-strong px-3 py-2 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent"
            >
              {c.langName}
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1">
        {/* ───────── demo stage — fills the viewport on desktop, flows
            naturally (no dead space) on tablet & phone ───────── */}
        <section className="relative overflow-hidden lg:flex lg:min-h-[100svh] lg:flex-col lg:justify-center">
          <div
            className="pointer-events-none absolute inset-0 -z-10 opacity-70"
            style={{
              background:
                "radial-gradient(55rem 28rem at 75% -10%, color-mix(in srgb, var(--color-accent) 16%, transparent), transparent), radial-gradient(45rem 26rem at 8% 8%, color-mix(in srgb, var(--color-violet) 14%, transparent), transparent)",
            }}
          />
          <div className="container-site w-full py-8 md:py-10">
            <div className="inline-flex w-fit items-center gap-2 rounded-full border border-line-strong bg-surface/60 px-4 py-1.5 text-sm text-ink-soft">
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-accent/70" />
                <span className="relative inline-flex size-2 rounded-full bg-accent" />
              </span>
              {c.badge}
            </div>
            <h1 className="mt-4 max-w-3xl font-display text-2xl font-bold leading-tight text-ink sm:text-3xl md:text-4xl md:leading-[1.15]">
              {c.h1a}
              <span className="text-gradient">{c.h1b}</span>
            </h1>
            <p className="mt-3 max-w-2xl text-ink-soft md:text-lg">{c.sub}</p>

            {/* scenario picker */}
            <p className="mt-6 text-sm font-semibold uppercase tracking-wide text-ink-muted">
              {c.pickScenario}
            </p>
          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            {scenarios.map((s) => {
              const selected = s.id === scenarioId;
              return (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => pickScenario(s.id)}
                  aria-pressed={selected}
                  className={`rounded-xl border p-4 text-start transition-colors ${
                    selected
                      ? "border-accent bg-accent/10"
                      : "border-line bg-raised hover:border-line-strong"
                  }`}
                >
                  <span className="font-display font-bold text-ink">{s.label}</span>
                  <span className="mt-1 block text-sm text-ink-muted">{s.blurb}</span>
                </button>
              );
            })}
          </div>

          {/* controls */}
          <div className="mt-6 flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={start}
              disabled={playing}
              className="inline-flex items-center gap-2 rounded-xl bg-accent px-5 py-3 font-semibold text-bg transition-colors hover:bg-accent-strong disabled:opacity-70"
            >
              {playing ? (
                <>
                  <EqualizerMini />
                  {c.playing}
                </>
              ) : (
                <>
                  <IconPlay className="size-4" />
                  {c.play}
                </>
              )}
            </button>
            {started && (
              <button
                type="button"
                onClick={() => {
                  reset();
                  start();
                }}
                className="inline-flex items-center gap-2 rounded-xl border border-line-strong px-5 py-3 font-semibold text-ink transition-colors hover:border-accent hover:text-accent"
              >
                <IconReplay className="size-4" />
                {c.replay}
              </button>
            )}
          </div>

          {/* analyzer grid */}
          <div className="mt-5 grid gap-5 lg:grid-cols-[1.3fr_1fr]">
            {/* transcript — bounded, scrolls internally, never stretches */}
            <div className="card-surface flex h-[clamp(22rem,56svh,40rem)] flex-col p-5 md:p-6">
              <div className="flex items-center justify-between border-b border-line pb-3">
                <span className="font-display font-semibold text-ink">{scenario.label}</span>
                {playing && (
                  <span className="inline-flex items-center gap-1.5 rounded-full bg-danger/15 px-2.5 py-1 text-xs font-bold text-danger">
                    <span className="size-1.5 animate-pulse rounded-full bg-danger" />
                    {c.live}
                  </span>
                )}
              </div>

              {!started ? (
                <div className="flex flex-1 flex-col items-center justify-center py-10 text-center">
                  <span className="grid size-12 place-items-center rounded-full bg-accent/12 text-accent">
                    <IconPlay className="size-5" />
                  </span>
                  <p className="mt-4 font-display font-semibold text-ink">{c.waitingTitle}</p>
                  <p className="mt-1 max-w-xs text-sm text-ink-muted">{c.waitingBody}</p>
                </div>
              ) : (
                <ul ref={listRef} className="mt-4 flex-1 space-y-3 overflow-y-auto pe-1 min-h-0">
                  {visibleTurns.map((t, i) => (
                    <li
                      key={i}
                      className={`flex ${t.who === "agent" ? "justify-end" : "justify-start"}`}
                    >
                      <div
                        className={`max-w-[85%] animate-fade-up rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                          t.who === "agent"
                            ? "bg-accent/15 text-ink"
                            : "bg-raised text-ink-soft"
                        }`}
                      >
                        <span
                          className={`mb-0.5 block text-xs font-semibold ${
                            t.who === "agent" ? "text-accent" : "text-violet-soft"
                          }`}
                        >
                          {t.who === "agent" ? c.agent : c.customer}
                        </span>
                        {t.text}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            {/* live analysis */}
            <div className="space-y-4">
              {/* sentiment */}
              <div className="card-surface p-5">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-ink-soft">{c.sentiment}</span>
                  <span
                    className={`text-sm font-bold ${
                      sentimentTone === "danger"
                        ? "text-danger"
                        : sentimentTone === "amber"
                          ? "text-amber"
                          : "text-success"
                    }`}
                  >
                    {started ? sentimentLabel : "—"}
                  </span>
                </div>
                <div className="mt-3 h-2.5 overflow-hidden rounded-full bg-overlay">
                  <div
                    className="h-full rounded-full transition-all duration-700 ease-out"
                    style={{
                      width: `${started ? sentiment : 50}%`,
                      background:
                        sentimentTone === "danger"
                          ? "var(--color-danger)"
                          : sentimentTone === "amber"
                            ? "var(--color-amber)"
                            : "var(--color-success)",
                    }}
                  />
                </div>
              </div>

              {/* topics + intent */}
              <div className="card-surface p-5">
                <span className="text-sm font-semibold text-ink-soft">{c.topics}</span>
                <div className="mt-3 flex min-h-[2rem] flex-wrap gap-2">
                  {topics.length === 0 ? (
                    <span className="text-sm text-ink-faint">—</span>
                  ) : (
                    topics.map((t, i) => (
                      <span
                        key={`${t}-${i}`}
                        className="animate-fade-up rounded-full border border-accent/40 bg-accent/10 px-3 py-1 text-xs font-medium text-accent"
                      >
                        {t}
                      </span>
                    ))
                  )}
                </div>
                {finished && (
                  <div className="mt-4 animate-fade-up border-t border-line pt-3">
                    <span className="text-xs text-ink-muted">{c.intent}</span>
                    <p className="font-display font-semibold text-ink">{scenario.intent}</p>
                  </div>
                )}
              </div>

              {/* qa + risk */}
              <div className="grid grid-cols-2 gap-4">
                <div className="card-surface p-5">
                  <span className="text-sm font-semibold text-ink-soft">{c.qa}</span>
                  <p className="mt-2 font-display text-3xl font-bold text-ink">
                    {finished ? (
                      <CountUp to={scenario.qa} />
                    ) : (
                      <span className="text-ink-faint">—</span>
                    )}
                    {finished && <span className="text-lg text-ink-muted">/100</span>}
                  </p>
                </div>
                <div className="card-surface p-5">
                  <span className="text-sm font-semibold text-ink-soft">{c.risk}</span>
                  <p className="mt-3">
                    {finished ? (
                      <span
                        className={`inline-block animate-fade-up rounded-full px-3 py-1 text-sm font-bold ${
                          scenario.risk.level === "high"
                            ? "bg-danger/15 text-danger"
                            : scenario.risk.level === "med"
                              ? "bg-amber/15 text-amber"
                              : "bg-success/15 text-success"
                        }`}
                      >
                        {scenario.risk.label}
                      </span>
                    ) : (
                      <span className="text-ink-faint">—</span>
                    )}
                  </p>
                </div>
              </div>
            </div>
          </div>
          </div>
        </section>

        {/* ───────── insights + disclaimer ───────── */}
        <section className="container-site pt-4 pb-12">
          {finished && (
            <div className="animate-fade-up rounded-2xl border border-accent/30 bg-accent/[0.06] p-5 md:p-6">
              <div className="flex items-center gap-2 text-accent">
                <IconSpark className="size-5" />
                <span className="font-display font-bold">{c.insights}</span>
              </div>
              <ul className="mt-4 space-y-2.5">
                {scenario.insights.map((ins, i) => (
                  <li key={i} className="flex gap-3 text-ink-soft">
                    <span className="mt-1 grid size-5 shrink-0 place-items-center rounded-full bg-accent/15 text-accent">
                      <IconCheck className="size-3.5" />
                    </span>
                    <span>{ins}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="mt-4 text-xs text-ink-faint">{c.disclaimer}</p>
        </section>

        {/* ───────── perks ───────── */}
        <section className="border-y border-line bg-surface/40">
          <div className="container-site py-16 md:py-20">
            <h2 className="text-center font-display text-2xl font-bold text-ink md:text-3xl">
              {c.perksTitle}
            </h2>
            <div className="mt-10 grid gap-6 md:grid-cols-3">
              {c.perks.map((p, i) => (
                <div key={i} className="card-surface p-6">
                  <span className="grid size-11 place-items-center rounded-xl bg-violet/15 text-violet-soft">
                    <IconSpark className="size-5" />
                  </span>
                  <h3 className="mt-4 font-display text-lg font-bold text-ink">{p.title}</h3>
                  <p className="mt-2 text-ink-soft">{p.body}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ───────── booking form ───────── */}
        <section id="book" className="container-site py-16 md:py-20">
          <div className="mx-auto max-w-2xl text-center">
            <h2 className="font-display text-2xl font-bold text-ink md:text-3xl">{c.formTitle}</h2>
            <p className="mt-3 text-lg text-ink-soft">{c.formSub}</p>
          </div>
          <form onSubmit={handleSubmit} className="card-surface mx-auto mt-8 max-w-2xl p-6 md:p-8">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field name="name" label={c.name} required />
              <Field name="email" label={c.email} type="email" required dir="ltr" />
              <Field name="company" label={c.company} />
              <Field name="teamSize" label={c.teamSize} />
            </div>
            <div className="mt-4">
              <label className="mb-1.5 block text-sm font-medium text-ink-soft">{c.message}</label>
              <textarea
                name="message"
                rows={3}
                className="w-full resize-y rounded-xl border border-line bg-bg px-4 py-3 text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent"
              />
            </div>
            <button
              type="submit"
              disabled={formState === "submitting"}
              className="mt-5 w-full rounded-xl bg-accent px-6 py-3 font-semibold text-bg transition-colors hover:bg-accent-strong disabled:opacity-60"
            >
              {formState === "submitting" ? c.sending : c.submit}
            </button>
            {formState === "success" && (
              <p className="mt-4 rounded-lg bg-success/12 px-4 py-3 text-success">{c.success}</p>
            )}
            {formState === "error" && (
              <p className="mt-4 rounded-lg bg-danger/12 px-4 py-3 text-danger">{c.error}</p>
            )}
          </form>
        </section>
      </main>

      <footer className="border-t border-line bg-surface">
        <div className="container-site flex flex-col items-center gap-3 py-8 text-sm text-ink-muted sm:flex-row sm:justify-between">
          <span>{c.rights}</span>
          <Link href="/" className="transition-colors hover:text-accent">
            {c.back}
          </Link>
        </div>
      </footer>
    </>
  );
}

/** A small CSS-only equalizer that signals "listening" — no WebGL, fast everywhere. */
function EqualizerMini() {
  return (
    <span className="flex items-end gap-0.5" aria-hidden>
      {[0, 1, 2, 3].map((i) => (
        <span
          key={i}
          className="w-0.5 rounded-full bg-bg"
          style={{
            height: "0.9rem",
            animation: "eq-bar 0.9s ease-in-out infinite",
            animationDelay: `${i * 0.12}s`,
          }}
        />
      ))}
    </span>
  );
}

/** Counts up to a target once mounted. Respects reduced motion (jumps to value). */
function CountUp({ to }: { to: number }) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) {
      const id = setTimeout(() => setN(to), 0);
      return () => clearTimeout(id);
    }
    let raf = 0;
    const startVal = 0;
    const dur = 700;
    let started = 0;
    const tick = (ts: number) => {
      if (!started) started = ts;
      const p = Math.min(1, (ts - started) / dur);
      setN(Math.round(startVal + (to - startVal) * (1 - Math.pow(1 - p, 3))));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [to]);
  return <>{n}</>;
}

function Field({
  name,
  label,
  type = "text",
  required = false,
  dir,
}: {
  name: string;
  label: string;
  type?: string;
  required?: boolean;
  dir?: "ltr" | "rtl";
}) {
  return (
    <div>
      <label className="mb-1.5 block text-sm font-medium text-ink-soft">
        {label}
        {required && <span className="text-accent"> *</span>}
      </label>
      <input
        name={name}
        type={type}
        required={required}
        dir={dir}
        className="w-full rounded-xl border border-line bg-bg px-4 py-3 text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent"
      />
    </div>
  );
}
