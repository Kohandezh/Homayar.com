"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import Image from "next/image";
import { CONTENT, DIR, LOCALES, LOCALE_NATIVE, type Locale } from "@/lib/content";
import { useLocale } from "@/lib/locale";
import { consoleHref, consoleLabels } from "@/lib/console-links";
import { TiltCard } from "./depth";
import { ScrollVideoHero } from "./scroll-video-hero";

/* ── tiny inline icons ── */
function IconPhone({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path
        d="M6.5 3h3l1.5 4-2 1.5a12 12 0 0 0 5 5l1.5-2 4 1.5v3a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 4.5 5.2 2 2 0 0 1 6.5 3Z"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconCheck({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path
        d="m5 12.5 4.5 4.5L19 7"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconChevron({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path
        d="m6 9 6 6 6-6"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function IconGlobe({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="1.6" />
      <path
        d="M3 12h18M12 3c2.6 2.7 2.6 15.3 0 18M12 3c-2.6 2.7-2.6 15.3 0 18"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** Accessible language dropdown: native names, RTL-aware, closes on outside
 *  click / Escape. Drives the locale state in <Landing/>. */
function LangSwitcher({
  locale,
  onChange,
}: {
  locale: Locale;
  onChange: (l: Locale) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Change language"
        className="flex items-center gap-1.5 rounded-lg border border-line-strong px-3 py-2 text-sm font-semibold text-ink transition-colors hover:border-accent hover:text-accent"
      >
        <IconGlobe className="size-4 text-accent" />
        <span>{LOCALE_NATIVE[locale]}</span>
        <IconChevron
          className={`size-3.5 transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <ul
          role="listbox"
          className="absolute end-0 z-50 mt-2 min-w-[10rem] overflow-hidden rounded-xl border border-line-strong bg-surface/95 p-1 shadow-xl backdrop-blur-xl"
        >
          {LOCALES.map((l) => (
            <li key={l}>
              <button
                type="button"
                dir={DIR[l]}
                onClick={() => {
                  onChange(l);
                  setOpen(false);
                }}
                className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-sm transition-colors ${
                  l === locale
                    ? "bg-accent/15 text-accent"
                    : "text-ink-soft hover:bg-raised hover:text-ink"
                }`}
              >
                <span>{LOCALE_NATIVE[l]}</span>
                {l === locale && <IconCheck className="size-3.5" />}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

const featureIcons = [
  // assistant + QA
  (c: string) => (
    <svg viewBox="0 0 24 24" className={c} fill="none" aria-hidden>
      <rect x="3.5" y="4.5" width="17" height="12" rx="2.5" stroke="currentColor" strokeWidth="1.6" />
      <path d="M8 20.5 12 17l4 3.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="m8.5 10.5 2 2 4.5-4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  // call routing
  (c: string) => (
    <svg viewBox="0 0 24 24" className={c} fill="none" aria-hidden>
      <circle cx="6" cy="6" r="2.4" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="18" cy="6" r="2.4" stroke="currentColor" strokeWidth="1.6" />
      <circle cx="12" cy="18" r="2.4" stroke="currentColor" strokeWidth="1.6" />
      <path d="M7.6 7.7 11 15m6-7.3L13 15" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  ),
  // multichannel bots
  (c: string) => (
    <svg viewBox="0 0 24 24" className={c} fill="none" aria-hidden>
      <rect x="4" y="8" width="16" height="10" rx="3" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 4.5V8" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="12" cy="3.5" r="1.3" fill="currentColor" />
      <circle cx="9" cy="13" r="1.2" fill="currentColor" />
      <circle cx="15" cy="13" r="1.2" fill="currentColor" />
    </svg>
  ),
];

const industryIcons = [
  // finance / insurance — shield
  (c: string) => (
    <svg viewBox="0 0 24 24" className={c} fill="none" aria-hidden>
      <path d="M12 3 5 5.5v5C5 15 8 18.5 12 20c4-1.5 7-5 7-9.5v-5L12 3Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="m9 11.5 2 2 4-4.5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
  // retail / e-commerce — cart
  (c: string) => (
    <svg viewBox="0 0 24 24" className={c} fill="none" aria-hidden>
      <path d="M4 5h2l1.6 9.5a1.5 1.5 0 0 0 1.5 1.3h7.2a1.5 1.5 0 0 0 1.5-1.2L20 8H7" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="10" cy="19.5" r="1.3" fill="currentColor" />
      <circle cx="17" cy="19.5" r="1.3" fill="currentColor" />
    </svg>
  ),
  // health / tourism — heart pulse
  (c: string) => (
    <svg viewBox="0 0 24 24" className={c} fill="none" aria-hidden>
      <path d="M12 20S4 14.5 4 9.2A4.2 4.2 0 0 1 12 7a4.2 4.2 0 0 1 8 2.2C20 14.5 12 20 12 20Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <path d="M7 12h2l1.5-2.5L13 14l1-2h3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  ),
];

type FormState = "idle" | "submitting" | "success" | "error";

export function Landing() {
  const { locale, setLocale } = useLocale();
  const [menuOpen, setMenuOpen] = useState(false);
  const [openFaq, setOpenFaq] = useState<number | null>(0);
  const [formState, setFormState] = useState<FormState>("idle");

  const t = CONTENT[locale];
  const dir = DIR[locale];

  // Cinematic continuity: sections fade/rise into view as you scroll, so the
  // page reads as one film below the hero. Motion-safe (the [data-reveal] hidden
  // state only exists under prefers-reduced-motion: no-preference in globals.css),
  // and each element reveals once then stops observing.
  useEffect(() => {
    // Stagger groups: tag each direct child with its index so CSS can offset its
    // transition-delay, making the cards rise in sequence.
    document.querySelectorAll<HTMLElement>("[data-reveal-group]").forEach((group) => {
      Array.from(group.children).forEach((child, i) => {
        (child as HTMLElement).style.setProperty("--reveal-i", String(i));
      });
    });

    const els = document.querySelectorAll<HTMLElement>("[data-reveal], [data-reveal-group]");
    if (!els.length) return;
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            io.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, []);

  function changeLocale(l: Locale) {
    setLocale(l);
    setMenuOpen(false);
  }

  const navItems = [
    { href: "#home", label: t.nav.home },
    { href: "#about", label: t.nav.about },
    { href: "#services", label: t.nav.services },
    { href: "#why", label: t.nav.why },
    { href: "#contact", label: t.nav.contact },
  ];

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    setFormState("submitting");
    try {
      const res = await fetch("/api/contact", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...data, locale }),
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
      {/* ───────── Header ───────── */}
      <header className="sticky top-0 z-50 border-b border-line/70 bg-bg/80 backdrop-blur-xl">
        <div className="container-site flex h-16 items-center justify-between gap-4">
          <a href="#home" className="flex items-center gap-2.5 shrink-0">
            <Image
              src="/homayar-icon.png"
              alt="Homayar"
              width={36}
              height={36}
              className="object-contain"
              priority
            />
            <span className="font-display text-xl font-bold tracking-tight text-ink">
              {t.footer.brand}
            </span>
          </a>

          <nav className="hidden items-center gap-1 lg:flex">
            {navItems.map((item) => (
              <a
                key={item.href}
                href={item.href}
                className="rounded-lg px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-raised hover:text-ink"
              >
                {item.label}
              </a>
            ))}
          </nav>

          <div className="flex items-center gap-2">
            <a
              href={`tel:${t.phones[0].replace(/-/g, "")}`}
              className="hidden items-center gap-2 rounded-lg px-3 py-2 text-sm font-medium text-ink-soft transition-colors hover:text-accent md:flex"
              dir="ltr"
            >
              <IconPhone className="size-4 text-accent" />
              {t.phones[0]}
            </a>
            <LangSwitcher locale={locale} onChange={changeLocale} />
            <a
              href={consoleHref("login")}
              className="hidden rounded-lg border border-line-strong px-4 py-2 text-sm font-medium text-ink-soft transition-colors hover:border-accent hover:text-accent md:inline-block"
            >
              {consoleLabels(locale).login}
            </a>
            <a
              href="/demo"
              className="cta-glow hidden rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-bg transition-colors hover:bg-accent-strong sm:inline-block"
            >
              {t.cta.demo}
            </a>
            <button
              type="button"
              aria-label="menu"
              onClick={() => setMenuOpen((o) => !o)}
              className="grid size-10 place-items-center rounded-lg border border-line-strong text-ink lg:hidden"
            >
              <svg viewBox="0 0 24 24" className="size-5" fill="none">
                <path
                  d={menuOpen ? "M6 6l12 12M18 6 6 18" : "M4 7h16M4 12h16M4 17h16"}
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>
        </div>

        {menuOpen && (
          <div className="border-t border-line bg-surface lg:hidden">
            <nav className="container-site flex flex-col py-3">
              {navItems.map((item) => (
                <a
                  key={item.href}
                  href={item.href}
                  onClick={() => setMenuOpen(false)}
                  className="rounded-lg px-3 py-3 text-ink-soft transition-colors hover:bg-raised hover:text-ink"
                >
                  {item.label}
                </a>
              ))}
              <a
                href={`tel:${t.phones[0].replace(/-/g, "")}`}
                dir="ltr"
                className="mt-1 flex items-center gap-2 rounded-lg px-3 py-3 font-medium text-accent"
              >
                <IconPhone className="size-4" />
                {t.phones[0]}
              </a>
              <div className="mt-2 grid grid-cols-2 gap-2 px-3">
                <a
                  href={consoleHref("login")}
                  className="rounded-lg border border-line-strong px-3 py-2.5 text-center text-sm font-medium text-ink-soft transition-colors hover:border-accent hover:text-accent"
                >
                  {consoleLabels(locale).login}
                </a>
                <a
                  href={consoleHref("register")}
                  className="rounded-lg border border-line-strong px-3 py-2.5 text-center text-sm font-medium text-ink-soft transition-colors hover:border-accent hover:text-accent"
                >
                  {consoleLabels(locale).register}
                </a>
              </div>
              <div className="mt-2 border-t border-line pt-3">
                <p className="px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-ink-muted">
                  {locale === "fa" ? "زبان" : "Language"}
                </p>
                <div className="flex flex-wrap gap-2 px-3">
                  {LOCALES.map((l) => (
                    <button
                      key={l}
                      type="button"
                      dir={DIR[l]}
                      onClick={() => changeLocale(l)}
                      className={`rounded-lg border px-3 py-1.5 text-sm transition-colors ${
                        l === locale
                          ? "border-accent text-accent"
                          : "border-line-strong text-ink-soft hover:border-accent hover:text-accent"
                      }`}
                    >
                      {LOCALE_NATIVE[l]}
                    </button>
                  ))}
                </div>
              </div>
            </nav>
          </div>
        )}
      </header>

      <main className="flex-1">
        {/* ───────── Hero: full-screen autoplaying film ───────── */}
        <ScrollVideoHero t={t} dir={dir} />

        {/* ───────── Pitch (detail that used to sit inside the hero) ───────── */}
        <section className="container-site py-20 md:py-24">
          <div className="mx-auto max-w-3xl" data-reveal>
            <div className="space-y-4 text-lg text-ink-soft">
              {t.hero.paragraphs.map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>

            <div className="mt-8 card-surface p-6 md:p-8">
              <p className="text-lg font-semibold text-ink">{t.hero.leadIn}</p>
              <ul className="mt-4 space-y-3">
                {t.hero.bullets.map((b, i) => (
                  <li key={i} className="flex gap-3 text-ink-soft">
                    <span className="mt-1 grid size-5 shrink-0 place-items-center rounded-full bg-accent/15 text-accent">
                      <IconCheck className="size-3.5" />
                    </span>
                    <span>{b}</span>
                  </li>
                ))}
              </ul>
            </div>

            <p className="mt-6 text-ink-muted">{t.hero.closing}</p>

            <div className="mt-8 flex flex-wrap gap-3">
              <a
                href="/demo"
                className="cta-glow rounded-xl bg-accent px-6 py-3 font-semibold text-bg transition-all duration-200 hover:-translate-y-0.5 hover:bg-accent-strong"
              >
                {t.finalCta.button}
              </a>
              <a
                href="#about"
                className="rounded-xl border border-line-strong px-6 py-3 font-semibold text-ink transition-colors hover:border-accent hover:text-accent"
              >
                {t.nav.about}
              </a>
            </div>
          </div>
        </section>

        {/* ───────── Customers ───────── */}
        <section className="border-y border-line bg-surface/50">
          <div className="container-site py-12">
            <p className="text-center text-sm font-semibold uppercase tracking-wide text-ink-muted">
              {t.customers.title}
            </p>
            <p className="mt-1 text-center text-ink-soft">{t.customers.subtitle}</p>
            <div className="mt-7 flex flex-wrap items-center justify-center gap-3 md:gap-4" data-reveal-group>
              {t.customers.logos.map((logo) => (
                <span
                  key={logo}
                  dir="ltr"
                  className="rounded-xl border border-line bg-raised px-5 py-2.5 font-display text-lg font-semibold tracking-tight text-ink-soft"
                >
                  {logo}
                </span>
              ))}
            </div>
          </div>
        </section>

        {/* ───────── Platform / What is Homayar ───────── */}
        <section id="about" className="container-site py-20 md:py-24">
          <div className="mx-auto max-w-3xl text-center" data-reveal>
            <SectionKicker>{t.nav.about}</SectionKicker>
            <h2 className="mt-3 font-display text-2xl font-bold text-ink md:text-4xl">
              {t.platform.title}
            </h2>
            <div className="mt-6 space-y-4 text-lg text-ink-soft">
              {t.platform.paragraphs.map((p, i) => (
                <p key={i}>{p}</p>
              ))}
            </div>
          </div>
        </section>

        {/* ───────── Features / Services ───────── */}
        <section id="services" className="border-y border-line bg-surface/40">
          <div className="container-site py-20 md:py-24">
            <h2 className="mx-auto max-w-3xl text-center font-display text-2xl font-bold text-ink md:text-3xl">
              {t.features.title}
            </h2>
            <div className="mt-12 grid gap-6 md:grid-cols-3" data-reveal-group>
              {t.features.items.map((f, i) => (
                <TiltCard key={i} className="card-surface p-7">
                  <div className="grid size-12 place-items-center rounded-xl bg-accent/12 text-accent">
                    {featureIcons[i]("size-6")}
                  </div>
                  <h3 className="mt-5 font-display text-lg font-bold text-ink">{f.title}</h3>
                  <p className="mt-2 text-ink-soft">{f.body}</p>
                </TiltCard>
              ))}
            </div>
          </div>
        </section>

        {/* ───────── Process ───────── */}
        <section className="container-site py-20 md:py-24">
          <div className="grid items-center gap-10 lg:grid-cols-2" data-reveal>
            <div>
              <SectionKicker>{t.nav.services}</SectionKicker>
              <h2 className="mt-3 font-display text-2xl font-bold text-ink md:text-3xl">
                {t.process.title}
              </h2>
              <div className="mt-6 space-y-4 text-lg text-ink-soft">
                {t.process.paragraphs.map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
            </div>
            <div className="card-surface glow-accent p-8">
              <ol className="space-y-6">
                {[0, 1, 2].map((step) => {
                  const labels: Record<Locale, readonly [string, string, string]> = {
                    fa: ["ممیزی و درک کسب‌وکار", "پیکربندی شخصی‌سازی‌شده", "تحلیل و بینش مستمر"],
                    en: ["Audit & business discovery", "Customized configuration", "Continuous analysis & insight"],
                    de: ["Audit & Geschäftsanalyse", "Maßgeschneiderte Konfiguration", "Kontinuierliche Analyse & Erkenntnisse"],
                    ar: ["التدقيق وفهم الأعمال", "إعداد مخصّص", "تحليل ورؤى مستمرة"],
                    fr: ["Audit & découverte métier", "Configuration sur mesure", "Analyse & insights en continu"],
                    es: ["Auditoría y descubrimiento del negocio", "Configuración personalizada", "Análisis e insights continuos"],
                  };
                  return (
                    <li key={step} className="flex gap-4">
                      <span className="grid size-10 shrink-0 place-items-center rounded-full bg-gradient-to-br from-accent to-violet font-display font-bold text-bg">
                        {step + 1}
                      </span>
                      <p className="pt-2 font-medium text-ink">{labels[locale][step]}</p>
                    </li>
                  );
                })}
              </ol>
            </div>
          </div>
        </section>

        {/* ───────── Outcomes / Why ───────── */}
        <section id="why" className="border-y border-line bg-surface/40">
          <div className="container-site py-20 md:py-24">
            <div className="mx-auto max-w-3xl text-center" data-reveal>
              <SectionKicker>{t.nav.why}</SectionKicker>
              <h2 className="mt-3 font-display text-2xl font-bold text-ink md:text-4xl">
                {t.outcomes.title}
              </h2>
              <p className="mt-6 text-lg leading-relaxed text-ink-soft">{t.outcomes.body}</p>
            </div>

            {/* Industries */}
            <h3 className="mt-16 text-center font-display text-xl font-bold text-ink md:text-2xl">
              {t.industries.title}
            </h3>
            <div className="mt-10 grid gap-6 md:grid-cols-3" data-reveal-group>
              {t.industries.items.map((ind, i) => (
                <TiltCard key={i} className="card-surface p-7">
                  <div className="grid size-12 place-items-center rounded-xl bg-violet/15 text-violet-soft">
                    {industryIcons[i]("size-6")}
                  </div>
                  <h4 className="mt-5 font-display text-lg font-bold text-ink">{ind.title}</h4>
                  <p className="mt-2 text-ink-soft">{ind.body}</p>
                </TiltCard>
              ))}
            </div>
          </div>
        </section>

        {/* ───────── Final CTA ───────── */}
        <section className="container-site py-20 md:py-24">
          <div className="relative overflow-hidden rounded-3xl border border-line-strong bg-gradient-to-br from-raised to-surface p-10 text-center md:p-16" data-reveal>
            <div
              className="pointer-events-none absolute inset-0 -z-10 opacity-80"
              style={{
                background:
                  "radial-gradient(40rem 20rem at 50% -20%, color-mix(in srgb, var(--color-accent) 22%, transparent), transparent)",
              }}
            />
            <h2 className="mx-auto max-w-3xl font-display text-2xl font-bold text-ink md:text-4xl">
              {t.finalCta.title}
            </h2>
            <p className="mx-auto mt-6 max-w-3xl text-lg leading-relaxed text-ink-soft">
              {t.finalCta.body}
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <a
                href="/demo"
                className="rounded-xl bg-accent px-7 py-3 font-semibold text-bg transition-colors hover:bg-accent-strong"
              >
                {t.finalCta.button}
              </a>
              <a
                href={consoleHref("register")}
                className="rounded-xl border border-line-strong px-7 py-3 font-semibold text-ink transition-colors hover:border-accent hover:text-accent"
              >
                {consoleLabels(locale).register}
              </a>
              {t.phones.map((p) => (
                <a
                  key={p}
                  href={`tel:${p.replace(/-/g, "")}`}
                  dir="ltr"
                  className="flex items-center gap-2 rounded-xl border border-line-strong px-5 py-3 font-semibold text-ink transition-colors hover:border-accent hover:text-accent"
                >
                  <IconPhone className="size-4 text-accent" />
                  {p}
                </a>
              ))}
            </div>
          </div>
        </section>

        {/* ───────── FAQ ───────── */}
        <section className="border-t border-line bg-surface/40">
          <div className="container-site py-20 md:py-24">
            <h2 className="text-center font-display text-2xl font-bold text-ink md:text-3xl">
              {t.faq.title}
            </h2>
            <div className="mx-auto mt-10 max-w-3xl space-y-3">
              {t.faq.items.map((item, i) => {
                const open = openFaq === i;
                return (
                  <div key={i} className="card-surface overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setOpenFaq(open ? null : i)}
                      className="flex w-full items-center justify-between gap-4 px-6 py-5 text-start"
                    >
                      <span className="font-display font-semibold text-ink">{item.q}</span>
                      <IconChevron
                        className={`size-5 shrink-0 text-accent transition-transform ${open ? "rotate-180" : ""}`}
                      />
                    </button>
                    {open && (
                      <div className="px-6 pb-5 -mt-1 text-ink-soft">{item.a}</div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        {/* ───────── Contact ───────── */}
        <section id="contact" className="container-site py-20 md:py-24">
          <div className="grid gap-10 lg:grid-cols-2">
            <div>
              <SectionKicker>{t.nav.contact}</SectionKicker>
              <h2 className="mt-3 font-display text-2xl font-bold text-ink md:text-3xl">
                {t.contact.title}
              </h2>
              <p className="mt-4 text-lg text-ink-soft">{t.contact.subtitle}</p>

              <div className="mt-8 space-y-4">
                {t.phones.map((p) => (
                  <a
                    key={p}
                    href={`tel:${p.replace(/-/g, "")}`}
                    dir="ltr"
                    className="flex w-fit items-center gap-3 text-lg font-semibold text-ink transition-colors hover:text-accent"
                  >
                    <IconPhone className="size-5 text-accent" />
                    {p}
                  </a>
                ))}
                <a
                  href="mailto:info@homayar.com"
                  dir="ltr"
                  className="flex w-fit items-center gap-3 text-ink-soft transition-colors hover:text-accent"
                >
                  <svg viewBox="0 0 24 24" className="size-5 text-accent" fill="none">
                    <rect x="3" y="5" width="18" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.6" />
                    <path d="m4 7 8 6 8-6" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  info@homayar.com
                </a>
                <p className="text-ink-muted">{t.footer.address}</p>
              </div>
            </div>

            <form onSubmit={handleSubmit} className="card-surface p-7 md:p-8">
              {/* Honeypot: hidden from users & screen readers, catches bots. */}
              <div aria-hidden className="hidden">
                <label>
                  Company URL
                  <input
                    type="text"
                    name="company_url"
                    tabIndex={-1}
                    autoComplete="off"
                  />
                </label>
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field name="firstName" label={t.contact.firstName} required />
                <Field name="lastName" label={t.contact.lastName} required />
                <Field name="mobile" label={t.contact.mobile} type="tel" required dir="ltr" />
                <Field name="email" label={t.contact.email} type="email" dir="ltr" />
              </div>
              <div className="mt-4">
                <label className="mb-1.5 block text-sm font-medium text-ink-soft">
                  {t.contact.message}
                </label>
                <textarea
                  name="message"
                  rows={4}
                  className="w-full resize-y rounded-xl border border-line bg-bg px-4 py-3 text-ink outline-none transition-colors placeholder:text-ink-faint focus:border-accent"
                />
              </div>
              <button
                type="submit"
                disabled={formState === "submitting"}
                className="mt-5 w-full rounded-xl bg-accent px-6 py-3 font-semibold text-bg transition-colors hover:bg-accent-strong disabled:opacity-60"
              >
                {t.contact.submit}
              </button>
              {formState === "success" && (
                <p className="mt-4 rounded-lg bg-success/12 px-4 py-3 text-success">
                  {t.contact.success}
                </p>
              )}
              {formState === "error" && (
                <p className="mt-4 rounded-lg bg-danger/12 px-4 py-3 text-danger">
                  {t.contact.error}
                </p>
              )}
            </form>
          </div>
        </section>
      </main>

      {/* ───────── Footer ───────── */}
      <footer className="border-t border-line bg-surface">
        <div className="container-site py-14">
          <div className="grid gap-10 md:grid-cols-[1.6fr_1fr_1fr]">
            <div>
              <Image
                src="/homayar-logo.png"
                alt={t.footer.brand}
                width={190}
                height={58}
                className="h-auto w-[190px] object-contain brightness-0 invert"
              />
              <p className="mt-4 font-semibold text-ink-soft">{t.footer.tagline}</p>
              <p className="mt-3 max-w-md text-ink-muted">{t.footer.description}</p>
            </div>

            <div>
              <h3 className="font-display font-bold text-ink">{t.footer.contactHeading}</h3>
              <p className="mt-4 text-ink-muted">{t.footer.address}</p>
              <div className="mt-4 space-y-2">
                {t.phones.map((p) => (
                  <a key={p} href={`tel:${p.replace(/-/g, "")}`} dir="ltr" className="block w-fit text-ink-soft transition-colors hover:text-accent">
                    {p}
                  </a>
                ))}
                <a href="mailto:info@homayar.com" dir="ltr" className="block w-fit text-ink-soft transition-colors hover:text-accent">
                  info@homayar.com
                </a>
              </div>
            </div>

            <div>
              <h3 className="font-display font-bold text-ink">{t.footer.socialHeading}</h3>
              <div className="mt-4 space-y-2">
                {t.footer.socials.map((s) => (
                  <a key={s.label} href={s.href} className="block w-fit text-ink-soft transition-colors hover:text-accent">
                    {s.label}
                  </a>
                ))}
              </div>
            </div>
          </div>

          <div className="mt-12 border-t border-line pt-6 text-sm text-ink-faint">
            {t.footer.copyright}
          </div>
        </div>
      </footer>
    </>
  );
}

function SectionKicker({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-block rounded-full bg-accent/12 px-3 py-1 text-sm font-semibold text-accent">
      {children}
    </span>
  );
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
