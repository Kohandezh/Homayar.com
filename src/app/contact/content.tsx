"use client";

import { PageHero } from "@/components/ui";
import { CheckIcon } from "@/components/icons";
import { ContactForm } from "@/components/contact-form";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware copy for the Contact page chrome. English is the canonical
 * source; `fa` is a direct Persian translation. Other locales fall back to
 * English. The form itself owns its own localized labels/messages; this
 * component only handles the surrounding page (hero, "what to expect", and the
 * alternative-contact note).
 */
type Expectation = { title: string; description: string };
type ContactCopy = {
  heroEyebrow: string;
  heroH1: string;
  heroSub: string;
  expectationsTitle: string;
  expectations: Expectation[];
  alternativeNote: string;
  successMessage: string;
};

const en: ContactCopy = {
  heroEyebrow: "Contact",
  heroH1: "See Homayar analyze your calls",
  heroSub:
    "Tell us about your team and we'll set up a 30-minute walkthrough — bring a sample recording and leave with a scored, agent-analyzed report. No commitment, no setup required.",
  expectationsTitle: "What to expect",
  expectations: [
    {
      title: "A 30-minute walkthrough",
      description:
        "A focused session with the Homayar team — how the agent pipeline works, what the dashboards show, and how scoring maps to your existing QA rubric.",
    },
    {
      title: "Your own calls analyzed",
      description:
        "Bring a sample recording and watch it transcribed, scored, and flagged live. You leave with a real report from your own conversation, not a canned demo.",
    },
    {
      title: "A tailored rollout plan",
      description:
        "Based on your call volume, telephony stack, and compliance requirements, we outline a concrete pilot scope, timeline, and success criteria for your team.",
    },
  ],
  alternativeNote:
    "Prefer email? Reach us directly at info@ksf.ir and we'll respond within one business day.",
  successMessage:
    "Thank you — your request is in. A member of the Homayar team will reach out within one business day to schedule your walkthrough.",
};

const fa: ContactCopy = {
  heroEyebrow: "تماس",
  heroH1: "ببینید همایار چگونه تماس‌های شما را تحلیل می‌کند",
  heroSub:
    "از تیم خود برایمان بگویید تا یک جلسه معرفی ۳۰ دقیقه‌ای ترتیب دهیم — یک فایل ضبط‌شده نمونه بیاورید و با یک گزارش امتیازدهی‌شده و تحلیل‌شده توسط عامل‌ها بیرون بروید. بدون هیچ تعهدی و بدون نیاز به راه‌اندازی.",
  expectationsTitle: "چه چیزی در انتظار شماست",
  expectations: [
    {
      title: "یک جلسه معرفی ۳۰ دقیقه‌ای",
      description:
        "یک جلسه متمرکز با تیم همایار — اینکه خط پردازش عامل‌ها چگونه کار می‌کند، داشبوردها چه چیزی نشان می‌دهند و امتیازدهی چگونه با معیارهای کنترل کیفیت فعلی شما تطبیق می‌یابد.",
    },
    {
      title: "تحلیل تماس‌های خودِ شما",
      description:
        "یک فایل ضبط‌شده نمونه بیاورید و به‌صورت زنده ببینید که رونویسی، امتیازدهی و نشانه‌گذاری می‌شود. شما با یک گزارش واقعی از گفت‌وگوی خودتان بیرون می‌روید، نه یک دموی از پیش‌آماده.",
    },
    {
      title: "یک طرح استقرار سفارشی",
      description:
        "بر اساس حجم تماس، سامانه تلفنی و الزامات انطباق شما، یک محدوده مشخص برای پایلوت، زمان‌بندی و معیارهای موفقیت را برای تیم شما ترسیم می‌کنیم.",
    },
  ],
  alternativeNote:
    "ایمیل را ترجیح می‌دهید؟ مستقیماً از طریق info@ksf.ir با ما در تماس باشید و ظرف یک روز کاری پاسخ شما را می‌دهیم.",
  successMessage:
    "سپاسگزاریم — درخواست شما ثبت شد. یکی از اعضای تیم همایار ظرف یک روز کاری برای هماهنگی جلسه معرفی با شما تماس خواهد گرفت.",
};

const DICT: Record<Locale, ContactCopy> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

function AlternativeNote({ note }: { note: string }) {
  const email = "info@ksf.ir";
  const index = note.indexOf(email);
  if (index === -1) {
    return <p className="text-sm leading-relaxed text-ink-muted">{note}</p>;
  }
  return (
    <p className="text-sm leading-relaxed text-ink-muted">
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

export function ContactContent() {
  const { locale } = useLocale();
  const c = DICT[locale] ?? en;

  return (
    <>
      <PageHero eyebrow={c.heroEyebrow} title={c.heroH1} description={c.heroSub} />

      <section className="py-16 sm:py-24">
        <div className="container-site">
          <div className="grid gap-12 lg:grid-cols-2 lg:gap-16">
            {/* What to expect */}
            <div>
              <h2 className="font-display text-2xl font-semibold tracking-tight text-ink sm:text-3xl">
                {c.expectationsTitle}
              </h2>
              <ul className="mt-8 space-y-6">
                {c.expectations.map((item) => (
                  <li key={item.title} className="flex gap-4">
                    <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-accent/40 bg-accent/10 text-accent">
                      <CheckIcon className="h-4 w-4" />
                    </span>
                    <div>
                      <h3 className="font-medium text-ink">{item.title}</h3>
                      <p className="mt-1.5 leading-relaxed text-ink-soft">
                        {item.description}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
              <div className="mt-10 rounded-xl border border-line bg-surface p-5">
                <AlternativeNote note={c.alternativeNote} />
              </div>
            </div>

            {/* Form */}
            <div>
              <ContactForm successMessage={c.successMessage} />
            </div>
          </div>
        </div>
      </section>
    </>
  );
}
