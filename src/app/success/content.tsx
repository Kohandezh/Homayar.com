"use client";

import { CheckIcon } from "@/components/icons";
import { ButtonLink } from "@/components/ui";
import { CartClearer } from "@/components/cart-clearer";
import { useLocale } from "@/lib/locale";
import type { Locale } from "@/lib/content";

/**
 * Locale-aware copy for the order-confirmation page. English is the canonical
 * source; `fa` is a direct Persian translation; other locales fall back to
 * English. The Stripe session summary is resolved on the server and passed in
 * as props so this client component stays purely presentational (plus the
 * cart-clearing side effect, which is client-only by nature).
 */
type Step = { step: string; title: string; text: string };
type SuccessCopy = {
  heading: string;
  receiptTo: (email: string) => string;
  testCompleted: string;
  onboardingNote: string;
  paidLabel: string;
  testModeNote: string;
  numberLocale: string;
  stepLabel: (step: string) => string;
  steps: Step[];
  continueCta: string;
  contactCta: string;
};

const en: SuccessCopy = {
  heading: "Thank you — order confirmed",
  receiptTo: (email) => `A receipt is on its way to ${email}.`,
  testCompleted: "Your test-mode payment completed successfully.",
  onboardingNote:
    "Our team will reach out within one business day to start onboarding.",
  paidLabel: "Paid:",
  testModeNote: "Stripe test mode",
  numberLocale: "en-US",
  stepLabel: (step) => `Step ${step}`,
  steps: [
    {
      step: "1",
      title: "Kickoff call",
      text: "We map your telephony stack and goals.",
    },
    {
      step: "2",
      title: "Connect calls",
      text: "Secure ingestion from your CCaaS or uploads.",
    },
    {
      step: "3",
      title: "First insights",
      text: "Scored calls and dashboards within days.",
    },
  ],
  continueCta: "Continue browsing",
  contactCta: "Talk to the team",
};

const fa: SuccessCopy = {
  heading: "سپاسگزاریم — سفارش تأیید شد",
  receiptTo: (email) => `رسید سفارش در راه است و به ${email} ارسال می‌شود.`,
  testCompleted: "پرداخت شما در حالت آزمایشی با موفقیت انجام شد.",
  onboardingNote:
    "تیم ما ظرف یک روز کاری با شما تماس می‌گیرد تا فرایند راه‌اندازی را آغاز کند.",
  paidLabel: "پرداخت‌شده:",
  testModeNote: "حالت آزمایشی Stripe",
  numberLocale: "fa-IR",
  stepLabel: (step) => `گام ${step}`,
  steps: [
    {
      step: "۱",
      title: "تماس آغازین",
      text: "سامانه تلفنی و اهداف شما را با هم مشخص می‌کنیم.",
    },
    {
      step: "۲",
      title: "اتصال تماس‌ها",
      text: "دریافت امن از مرکز تماس ابری (CCaaS) یا بارگذاری فایل.",
    },
    {
      step: "۳",
      title: "نخستین بینش‌ها",
      text: "تماس‌های امتیازدهی‌شده و داشبوردها در عرض چند روز.",
    },
  ],
  continueCta: "ادامه مرور",
  contactCta: "گفت‌وگو با تیم",
};

const DICT: Record<Locale, SuccessCopy> = {
  fa,
  en,
  de: en,
  ar: en,
  fr: en,
  es: en,
};

export type SuccessSummary = {
  email: string | null;
  amountTotal: number | null;
  currency: string;
};

export function SuccessContent({ summary }: { summary: SuccessSummary | null }) {
  const { locale } = useLocale();
  const c = DICT[locale] ?? en;

  return (
    <div className="container-site flex flex-col items-center py-24 text-center">
      <CartClearer />
      <div className="flex h-16 w-16 items-center justify-center rounded-full border border-success/40 bg-success/10 text-success">
        <CheckIcon width={30} height={30} />
      </div>
      <h1 className="mt-6 font-display text-4xl font-semibold tracking-tight text-ink">
        {c.heading}
      </h1>
      <p className="mt-4 max-w-lg text-lg text-ink-muted">
        {summary?.email ? c.receiptTo(summary.email) : c.testCompleted}{" "}
        {c.onboardingNote}
      </p>

      {summary?.amountTotal != null && (
        <p className="mt-4 rounded-xl border border-line bg-raised px-5 py-3 font-mono text-sm text-ink-soft">
          {c.paidLabel} $
          {(summary.amountTotal / 100).toLocaleString(c.numberLocale)}{" "}
          {summary.currency.toUpperCase()} · {c.testModeNote}
        </p>
      )}

      <div className="mt-10 grid w-full max-w-2xl gap-4 text-left sm:grid-cols-3">
        {c.steps.map((s) => (
          <div key={s.step} className="card-surface p-5">
            <p className="font-mono text-xs text-accent">{c.stepLabel(s.step)}</p>
            <p className="mt-2 font-medium text-ink">{s.title}</p>
            <p className="mt-1 text-sm text-ink-muted">{s.text}</p>
          </div>
        ))}
      </div>

      <div className="mt-10 flex gap-3">
        <ButtonLink href="/catalog" variant="secondary">
          {c.continueCta}
        </ButtonLink>
        <ButtonLink href="/contact">{c.contactCta}</ButtonLink>
      </div>
    </div>
  );
}
