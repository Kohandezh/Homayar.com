"use client";

import { useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import { isOpenDay, SLOT_MINUTES, BUSINESS_TZ, type Slot } from "@/lib/booking";

type Lang = "fa" | "en";

const STR = {
  fa: {
    dir: "rtl" as const,
    localeTag: "fa-IR-u-ca-gregory",
    brand: "همایار",
    home: "بازگشت به خانه",
    title: "رزرو جلسه مشاوره",
    subtitle: "یک زمان مناسب را انتخاب کنید تا تیم همایار با شما تماس بگیرد.",
    duration: `${SLOT_MINUTES} دقیقه · آنلاین`,
    pickDate: "یک روز را انتخاب کنید",
    pickTime: "یک ساعت را انتخاب کنید",
    chooseDayFirst: "ابتدا یک روز را از تقویم انتخاب کنید.",
    noTimes: "برای این روز زمانی آزاد نیست. روز دیگری را امتحان کنید.",
    loading: "در حال بارگذاری زمان‌ها…",
    tzNote: "زمان‌ها به وقت تهران (ایران)",
    name: "نام و نام خانوادگی",
    email: "ایمیل",
    company: "شرکت",
    message: "توضیحات",
    optional: "اختیاری",
    back: "بازگشت",
    confirm: "ثبت رزرو",
    booking: "در حال ثبت…",
    selected: "زمان انتخاب‌شده",
    successTitle: "رزرو شما ثبت شد!",
    successBody: "جزئیات جلسه را برایتان ایمیل می‌کنیم. آن را به تقویم خود اضافه کنید:",
    addGoogle: "افزودن به Google Calendar",
    downloadIcs: "دانلود فایل تقویم (.ics)",
    bookAnother: "رزرو زمان دیگر",
    errorGeneric: "مشکلی پیش آمد. دوباره تلاش کنید.",
  },
  en: {
    dir: "ltr" as const,
    localeTag: "en-US",
    brand: "Homayar",
    home: "Back to home",
    title: "Book a consultation",
    subtitle: "Pick a time that works and the Homayar team will meet you then.",
    duration: `${SLOT_MINUTES} min · Online`,
    pickDate: "Select a day",
    pickTime: "Select a time",
    chooseDayFirst: "Choose a day from the calendar first.",
    noTimes: "No times free on this day. Try another.",
    loading: "Loading times…",
    tzNote: "Times shown in Tehran (Iran) time",
    name: "Full name",
    email: "Email",
    company: "Company",
    message: "Anything we should know",
    optional: "optional",
    back: "Back",
    confirm: "Confirm booking",
    booking: "Booking…",
    selected: "Selected time",
    successTitle: "You're booked!",
    successBody: "We'll email you the meeting details. Add it to your calendar:",
    addGoogle: "Add to Google Calendar",
    downloadIcs: "Download calendar file (.ics)",
    bookAnother: "Book another time",
    errorGeneric: "Something went wrong. Please try again.",
  },
};

function dateKey(y: number, m: number, d: number): string {
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

type Booked = { start: string; end: string };

export function BookingExperience() {
  const [lang, setLang] = useState<Lang>("fa");
  const s = STR[lang];

  const tz = useMemo(
    () =>
      typeof Intl !== "undefined"
        ? Intl.DateTimeFormat().resolvedOptions().timeZone
        : "UTC",
    [],
  );

  // Calendar view month (Gregorian). Default to the current month.
  const now = useMemo(() => new Date(), []);
  const [view, setView] = useState(() => ({
    y: now.getUTCFullYear(),
    m: now.getUTCMonth(),
  }));
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [slots, setSlots] = useState<Slot[] | null>(null);
  const [loadingSlots, setLoadingSlots] = useState(false);
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null);

  const [step, setStep] = useState<"pick" | "details" | "done">("pick");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [booked, setBooked] = useState<Booked | null>(null);
  const [googleUrl, setGoogleUrl] = useState<string>("");
  const [icsHref, setIcsHref] = useState<string>("");

  // Weekday + month labels via Intl so they follow the chosen language.
  const weekdayNames = useMemo(() => {
    const fmt = new Intl.DateTimeFormat(s.localeTag, { weekday: "short" });
    // 2024-01-01 is a Monday; build Mon…Sun.
    return Array.from({ length: 7 }, (_, i) =>
      fmt.format(new Date(Date.UTC(2024, 0, 1 + i))),
    );
  }, [s.localeTag]);

  const monthLabel = useMemo(
    () =>
      new Intl.DateTimeFormat(s.localeTag, {
        month: "long",
        year: "numeric",
      }).format(new Date(Date.UTC(view.y, view.m, 1))),
    [s.localeTag, view],
  );

  // Build the calendar grid (weeks start Monday).
  const cells = useMemo(() => {
    const firstWeekday = (new Date(Date.UTC(view.y, view.m, 1)).getUTCDay() + 6) % 7;
    const daysInMonth = new Date(Date.UTC(view.y, view.m + 1, 0)).getUTCDate();
    const out: (string | null)[] = [];
    for (let i = 0; i < firstWeekday; i++) out.push(null);
    for (let d = 1; d <= daysInMonth; d++) out.push(dateKey(view.y, view.m, d));
    return out;
  }, [view]);

  // Don't let the user page back before the current month.
  const atCurrentMonth =
    view.y === now.getUTCFullYear() && view.m === now.getUTCMonth();

  function changeMonth(delta: number) {
    setView((v) => {
      const next = new Date(Date.UTC(v.y, v.m + delta, 1));
      return { y: next.getUTCFullYear(), m: next.getUTCMonth() };
    });
  }

  async function pickDate(key: string) {
    setSelectedDate(key);
    setSelectedSlot(null);
    setSlots(null);
    setLoadingSlots(true);
    setError(null);
    try {
      const res = await fetch(`/api/book/slots?date=${key}`);
      const data = await res.json();
      setSlots(data.slots ?? []);
    } catch {
      setSlots([]);
    } finally {
      setLoadingSlots(false);
    }
  }

  function pickSlot(iso: string) {
    setSelectedSlot(iso);
    setStep("details");
    setError(null);
  }

  // Always render slots in the business timezone (Tehran), regardless of where
  // the visitor is, so everyone sees the same Iran-local meeting time.
  function fmtTime(iso: string) {
    return new Date(iso).toLocaleTimeString(s.localeTag, {
      hour: "2-digit",
      minute: "2-digit",
      timeZone: BUSINESS_TZ,
    });
  }
  function fmtDateTime(iso: string) {
    return new Date(iso).toLocaleString(s.localeTag, {
      weekday: "long",
      day: "numeric",
      month: "long",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: BUSINESS_TZ,
    });
  }

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!selectedSlot) return;
    const form = e.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/book", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...data, slot: selectedSlot, timezone: tz, lang }),
      });
      const json = await res.json();
      if (!res.ok) {
        setError(json.error || s.errorGeneric);
        // If the slot was taken, refresh availability for that day.
        if (res.status === 409 && selectedDate) {
          setStep("pick");
          pickDate(selectedDate);
        }
        return;
      }
      setBooked(json.booking);
      setGoogleUrl(json.googleUrl);
      setIcsHref(
        `data:text/calendar;charset=utf-8,${encodeURIComponent(json.ics)}`,
      );
      setStep("done");
    } catch {
      setError(s.errorGeneric);
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    setStep("pick");
    setSelectedSlot(null);
    setBooked(null);
    setError(null);
  }

  return (
    <div dir={s.dir} className="flex min-h-screen flex-col bg-bg text-ink">
      <header className="border-b border-line">
        <div className="container-site flex items-center justify-between py-4">
          <Link href="/" className="font-display text-xl font-bold text-ink">
            {s.brand}
          </Link>
          <div className="flex items-center gap-4">
            <Link
              href="/"
              className="text-sm text-ink-soft transition-colors hover:text-accent"
            >
              {s.home}
            </Link>
            <div className="flex overflow-hidden rounded-lg border border-line-strong text-sm">
              {(["fa", "en"] as Lang[]).map((l) => (
                <button
                  key={l}
                  onClick={() => setLang(l)}
                  className={`px-3 py-1.5 transition-colors ${
                    lang === l ? "bg-accent text-bg" : "text-ink-soft hover:text-ink"
                  }`}
                >
                  {l === "fa" ? "فا" : "EN"}
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <main className="container-site flex-1 py-10 md:py-16">
        <div className="mx-auto max-w-3xl">
          <h1 className="font-display text-3xl font-bold text-ink md:text-4xl">
            {s.title}
          </h1>
          <p className="mt-2 text-ink-soft">{s.subtitle}</p>
          <p className="mt-1 text-sm font-medium text-accent">{s.duration}</p>

          {step === "done" && booked ? (
            <div className="mt-10 card-surface p-8 text-center">
              <div className="mx-auto grid size-14 place-items-center rounded-full bg-accent/15 text-accent">
                <IconCheck className="size-7" />
              </div>
              <h2 className="mt-5 font-display text-2xl font-bold text-ink">
                {s.successTitle}
              </h2>
              <p className="mt-2 text-lg font-medium text-ink">
                {fmtDateTime(booked.start)}
              </p>
              <p className="mt-4 text-ink-soft">{s.successBody}</p>
              <div className="mt-6 flex flex-wrap justify-center gap-3">
                <a
                  href={googleUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="cta-glow rounded-xl bg-accent px-5 py-2.5 font-semibold text-bg transition-colors hover:bg-accent-strong"
                >
                  {s.addGoogle}
                </a>
                <a
                  href={icsHref}
                  download="homayar-consultation.ics"
                  className="rounded-xl border border-line-strong px-5 py-2.5 font-semibold text-ink transition-colors hover:border-accent hover:text-accent"
                >
                  {s.downloadIcs}
                </a>
              </div>
              <button
                onClick={reset}
                className="mt-6 text-sm text-ink-soft underline-offset-4 hover:text-accent hover:underline"
              >
                {s.bookAnother}
              </button>
            </div>
          ) : step === "details" && selectedSlot ? (
            <form onSubmit={submit} className="mt-10 card-surface p-6 md:p-8">
              <div className="rounded-xl border border-line bg-raised px-4 py-3 text-sm">
                <span className="text-ink-muted">{s.selected}: </span>
                <span className="font-medium text-ink">
                  {fmtDateTime(selectedSlot)}
                </span>
              </div>
              <div className="mt-6 grid gap-4">
                <Field name="name" label={s.name} required />
                <Field name="email" label={s.email} type="email" required dir="ltr" />
                <Field name="company" label={`${s.company} (${s.optional})`} />
                <label className="block">
                  <span className="mb-1.5 block text-sm font-medium text-ink-soft">
                    {s.message} ({s.optional})
                  </span>
                  <textarea
                    name="message"
                    rows={3}
                    className="w-full rounded-xl border border-line-strong bg-bg px-4 py-2.5 text-ink outline-none transition-colors focus:border-accent"
                  />
                </label>
              </div>
              {error && <p className="mt-4 text-sm text-red-400">{error}</p>}
              <div className="mt-6 flex items-center gap-3">
                <button
                  type="button"
                  onClick={() => {
                    setStep("pick");
                    setError(null);
                  }}
                  className="rounded-xl border border-line-strong px-5 py-2.5 font-semibold text-ink transition-colors hover:border-accent"
                >
                  {s.back}
                </button>
                <button
                  type="submit"
                  disabled={submitting}
                  className="cta-glow rounded-xl bg-accent px-6 py-2.5 font-semibold text-bg transition-colors hover:bg-accent-strong disabled:opacity-60"
                >
                  {submitting ? s.booking : s.confirm}
                </button>
              </div>
            </form>
          ) : (
            <div className="mt-10 grid gap-6 md:grid-cols-2">
              {/* Calendar */}
              <div className="card-surface p-5">
                <div className="flex items-center justify-between">
                  <button
                    onClick={() => changeMonth(-1)}
                    disabled={atCurrentMonth}
                    aria-label="previous month"
                    className="grid size-9 place-items-center rounded-lg border border-line-strong text-ink-soft transition-colors hover:text-accent disabled:opacity-30"
                  >
                    <IconChevron className="size-4 rotate-180 rtl:rotate-0" />
                  </button>
                  <span className="font-display font-semibold text-ink">
                    {monthLabel}
                  </span>
                  <button
                    onClick={() => changeMonth(1)}
                    aria-label="next month"
                    className="grid size-9 place-items-center rounded-lg border border-line-strong text-ink-soft transition-colors hover:text-accent"
                  >
                    <IconChevron className="size-4 rtl:rotate-180" />
                  </button>
                </div>
                <div className="mt-4 grid grid-cols-7 gap-1 text-center text-xs text-ink-muted">
                  {weekdayNames.map((w) => (
                    <span key={w} className="py-1">
                      {w}
                    </span>
                  ))}
                </div>
                <div className="mt-1 grid grid-cols-7 gap-1">
                  {cells.map((key, i) => {
                    if (!key) return <span key={`b${i}`} />;
                    const open = isOpenDay(key);
                    const day = Number(key.slice(-2));
                    const active = key === selectedDate;
                    return (
                      <button
                        key={key}
                        disabled={!open}
                        onClick={() => pickDate(key)}
                        className={`aspect-square rounded-lg text-sm transition-colors ${
                          active
                            ? "bg-accent font-semibold text-bg"
                            : open
                              ? "text-ink hover:bg-raised hover:text-accent"
                              : "text-ink-muted/40"
                        }`}
                      >
                        {day}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Times */}
              <div className="card-surface p-5">
                <p className="font-display font-semibold text-ink">{s.pickTime}</p>
                {!selectedDate ? (
                  <p className="mt-4 text-sm text-ink-muted">{s.chooseDayFirst}</p>
                ) : loadingSlots ? (
                  <p className="mt-4 text-sm text-ink-muted">{s.loading}</p>
                ) : slots && slots.length === 0 ? (
                  <p className="mt-4 text-sm text-ink-muted">{s.noTimes}</p>
                ) : (
                  <>
                    <p className="mt-1 text-xs text-ink-muted">{s.tzNote}</p>
                    <div className="mt-4 grid max-h-80 grid-cols-2 gap-2 overflow-y-auto pe-1">
                      {slots?.map((slot) => (
                        <button
                          key={slot.start}
                          onClick={() => pickSlot(slot.start)}
                          className="rounded-lg border border-line-strong py-2.5 text-sm font-medium text-ink transition-colors hover:border-accent hover:text-accent"
                        >
                          {fmtTime(slot.start)}
                        </button>
                      ))}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
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
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-ink-soft">
        {label}
        {required && <span className="text-accent"> *</span>}
      </span>
      <input
        name={name}
        type={type}
        required={required}
        dir={dir}
        className="w-full rounded-xl border border-line-strong bg-bg px-4 py-2.5 text-ink outline-none transition-colors focus:border-accent"
      />
    </label>
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
        d="M9 6l6 6-6 6"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
