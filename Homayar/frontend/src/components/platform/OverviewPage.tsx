import {
  CheckCircle,
  Clock,
  FileAudio,
  Gauge,
  Search,
  Upload,
} from "lucide-react";
import { Link } from "react-router-dom";

import {
  useBillingUsage,
  useMeetings,
} from "@/lib/platform-api";
import { useI18n } from "@/lib/i18n-context";
import { formatAudioSeconds, formatDuration } from "@/lib/format";
import { useCan } from "@/lib/use-can";
import AllowanceNotice from "@/components/platform/AllowanceNotice";
import type {
  BillingUsageSnapshot,
  Meeting,
  MeetingStatus,
} from "@/types/platform";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";

const PROCESSING_STATUSES = new Set<MeetingStatus>([
  "uploading",
  "uploaded",
  "queued",
  "processing",
  "transcribing",
  "diarizing",
  "analyzing",
]);

const COPY = {
  en: {
    eyebrow: "Workspace activity",
    title: "Overview",
    search: "Search",
    upload: "Upload meeting",
    recentLoaded: "Recent meetings",
    recentLoadedHint: "Latest records loaded",
    completed: "With results",
    completedHint: "Completed or partial, in the recent list",
    processing: "Processing now",
    processingHint: "In the recent list",
    audioUsed: "Audio used",
    audioUsedHint: "Authoritative billing usage",
    notAvailable: "Not available",
    minutes: "min",
    usageTitle: "Plan usage",
    usageDescription: "Current entitlement counters reported by the server.",
    usageLoading: "Loading plan usage",
    usageError: "Plan usage could not be loaded.",
    usageEmpty: "No usage entitlements are available for this workspace.",
    usageRestricted: "Plan usage is visible to workspace owners and administrators.",
    used: "Used",
    reserved: "Reserved",
    remaining: "Remaining",
    unlimited: "Unlimited",
    recentTitle: "Recent meetings",
    recentDescription: "The latest meetings returned by your workspace.",
    viewAll: "View all",
    recentLoading: "Loading recent meetings",
    recentError: "Recent meetings could not be loaded.",
    recentEmpty: "No meetings have been uploaded yet.",
    openMeeting: "Open meeting",
    duration: "Duration",
    language: "Language",
    created: "Created",
    metricAudio: "Audio processing",
    metricConcurrency: "Concurrent jobs",
    metricSeats: "Seats",
  },
  fa: {
    eyebrow: "فعالیت فضای کاری",
    title: "نمای کلی",
    search: "جستجو",
    upload: "بارگذاری جلسه",
    recentLoaded: "جلسه‌های اخیر",
    recentLoadedHint: "آخرین موارد دریافت‌شده",
    completed: "دارای نتیجه",
    completedHint: "تکمیل‌شده یا ناقص، در فهرست اخیر",
    processing: "در حال پردازش",
    processingHint: "در فهرست اخیر",
    audioUsed: "صوت مصرف‌شده",
    audioUsedHint: "مصرف ثبت‌شده در صورتحساب",
    notAvailable: "در دسترس نیست",
    minutes: "دقیقه",
    usageTitle: "مصرف طرح",
    usageDescription: "شمارنده‌های فعلی سهمیه که سرور گزارش کرده است.",
    usageLoading: "در حال دریافت مصرف طرح",
    usageError: "اطلاعات مصرف طرح دریافت نشد.",
    usageEmpty: "برای این فضای کاری سهمیه‌ای ثبت نشده است.",
    usageRestricted: "مصرف طرح برای مالک و مدیران فضای کاری قابل مشاهده است.",
    used: "مصرف‌شده",
    reserved: "رزروشده",
    remaining: "باقی‌مانده",
    unlimited: "نامحدود",
    recentTitle: "جلسه‌های اخیر",
    recentDescription: "آخرین جلسه‌های ثبت‌شده در فضای کاری شما.",
    viewAll: "مشاهده همه",
    recentLoading: "در حال دریافت جلسه‌های اخیر",
    recentError: "جلسه‌های اخیر دریافت نشد.",
    recentEmpty: "هنوز جلسه‌ای بارگذاری نشده است.",
    openMeeting: "باز کردن جلسه",
    duration: "مدت",
    language: "زبان",
    created: "ایجاد",
    metricAudio: "پردازش صوت",
    metricConcurrency: "پردازش هم‌زمان",
    metricSeats: "کاربر",
  },
} as const;

type OverviewCopy = { [Key in keyof (typeof COPY)["en"]]: string };

function formatDate(value: string, lang: "fa" | "en") {
  return new Intl.DateTimeFormat(lang === "fa" ? "fa-IR" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function formatNumber(value: number, lang: "fa" | "en", maximumFractionDigits = 0) {
  return new Intl.NumberFormat(lang === "fa" ? "fa-IR" : "en", {
    maximumFractionDigits,
  }).format(value);
}

function metricLabel(metric: string, copy: OverviewCopy): string {
  if (metric === "audio_seconds") return copy.metricAudio;
  if (metric === "concurrent_jobs") return copy.metricConcurrency;
  if (metric === "seats") return copy.metricSeats;
  return metric.replaceAll("_", " ");
}

function usageValue(metric: string, value: number, lang: "fa" | "en"): string {
  // Whole seconds, never rounded up: a displayed amount must not promise more
  // audio than the server will accept.
  if (metric === "audio_seconds") return formatAudioSeconds(value, lang);
  return formatNumber(value, lang, 1);
}

function UsageRow({
  item,
  lang,
  copy,
}: {
  item: BillingUsageSnapshot;
  lang: "fa" | "en";
  copy: OverviewCopy;
}) {
  const consumed = item.used + item.reserved;
  const progress = item.limit && item.limit > 0
    ? Math.min(100, Math.max(0, (consumed / item.limit) * 100))
    : 0;

  return (
    <li className="overview-usage-item border-b border-border-subtle px-4 py-4 last:border-b-0 sm:px-5">
      <div className="overview-usage-heading flex flex-wrap items-center justify-between gap-2">
        <strong className="text-sm font-semibold text-text-primary">
          {metricLabel(item.metric, copy)}
        </strong>
        <span className="text-xs text-text-secondary">
          {copy.remaining}: {item.remaining === null
            ? copy.unlimited
            : usageValue(item.metric, item.remaining, lang)}
        </span>
      </div>
      {item.limit !== null && (
        <div
          className="overview-usage-progress mt-3 h-1.5 overflow-hidden rounded-full bg-bg-elevated"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={item.limit}
          aria-valuenow={Math.min(consumed, item.limit)}
          aria-label={metricLabel(item.metric, copy)}
        >
          <span
            className="block h-full rounded-full bg-accent"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}
      <dl className="overview-usage-values mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
        <div>
          <dt className="text-text-muted">{copy.used}</dt>
          <dd className="mt-1 font-medium text-text-primary">
            {usageValue(item.metric, item.used, lang)}
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">{copy.reserved}</dt>
          <dd className="mt-1 font-medium text-text-primary">
            {usageValue(item.metric, item.reserved, lang)}
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">{copy.remaining}</dt>
          <dd className="mt-1 font-medium text-text-primary">
            {item.remaining === null
              ? copy.unlimited
              : usageValue(item.metric, item.remaining, lang)}
          </dd>
        </div>
      </dl>
    </li>
  );
}

function MeetingRow({
  meeting,
  lang,
  copy,
}: {
  meeting: Meeting;
  lang: "fa" | "en";
  copy: OverviewCopy;
}) {
  return (
    <li className="overview-meeting-item border-b border-border-subtle px-4 py-4 last:border-b-0 sm:px-5">
      <div className="flex min-w-0 flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <Link
            className="block truncate text-sm font-semibold text-text-primary hover:text-accent hover:underline"
            to={`/meetings/${meeting.id}`}
            aria-label={`${copy.openMeeting}: ${meeting.title}`}
          >
            {meeting.title}
          </Link>
          <dl className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-muted">
            <div className="flex gap-1">
              <dt>{copy.duration}:</dt>
              <dd><bdi dir="ltr">{formatDuration(meeting.duration_seconds, lang)}</bdi></dd>
            </div>
            <div className="flex gap-1">
              <dt>{copy.language}:</dt>
              <dd>{meeting.language.toUpperCase()}</dd>
            </div>
            <div className="flex gap-1">
              <dt>{copy.created}:</dt>
              <dd><time dateTime={meeting.created_at}>{formatDate(meeting.created_at, lang)}</time></dd>
            </div>
          </dl>
        </div>
        <StatusBadge status={meeting.status} />
      </div>
    </li>
  );
}

export default function OverviewPage() {
  const { lang } = useI18n();
  const can = useCan();
  const copy = COPY[lang];
  const meetingsQuery = useMeetings({ limit: 8 });
  const usageQuery = useBillingUsage(can.readBilling);
  const meetings = meetingsQuery.data?.items ?? [];
  const completedCount = meetings.filter((meeting) => meeting.status === "completed" || meeting.status === "partial").length;
  const processingCount = meetings.filter((meeting) => PROCESSING_STATUSES.has(meeting.status)).length;
  const audioUsage = usageQuery.data?.items.find((item) => item.metric === "audio_seconds");

  const metrics = [
    {
      label: copy.recentLoaded,
      value: meetingsQuery.isPending ? "--" : formatNumber(meetings.length, lang),
      hint: copy.recentLoadedHint,
      icon: FileAudio,
    },
    {
      label: copy.completed,
      value: meetingsQuery.isPending ? "--" : formatNumber(completedCount, lang),
      hint: copy.completedHint,
      icon: CheckCircle,
    },
    {
      label: copy.processing,
      value: meetingsQuery.isPending ? "--" : formatNumber(processingCount, lang),
      hint: copy.processingHint,
      icon: Clock,
    },
    {
      label: copy.audioUsed,
      value: !can.readBilling
        ? "—"
        : usageQuery.isPending
        ? "--"
        : audioUsage
          ? usageValue(audioUsage.metric, audioUsage.used, lang)
          : copy.notAvailable,
      hint: can.readBilling ? copy.audioUsedHint : copy.usageRestricted,
      icon: Gauge,
    },
  ];

  return (
    <main className="overview-page platform-page space-y-5">
      <PageHeader
        eyebrow={copy.eyebrow}
        title={copy.title}
        actions={(
          <>
            <Link className="platform-button" to="/search">
              <Search size={15} aria-hidden="true" />
              {copy.search}
            </Link>
            {can.createMeeting && (
              <Link className="platform-button platform-button-primary" to="/meetings#upload">
                <Upload size={15} aria-hidden="true" />
                {copy.upload}
              </Link>
            )}
          </>
        )}
      />

      <section className="overview-metrics" aria-label={copy.eyebrow}>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {metrics.map(({ label, value, hint, icon: Icon }) => (
            <article key={label} className="overview-metric-card platform-surface p-4 sm:p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="m-0 text-xs font-medium text-text-secondary">{label}</p>
                  <p className="mt-2 text-2xl font-semibold text-text-primary" aria-live="polite">
                    {value}
                  </p>
                </div>
                <span className="grid size-9 shrink-0 place-items-center rounded-md bg-bg-elevated text-accent">
                  <Icon size={17} aria-hidden="true" />
                </span>
              </div>
              <p className="mt-3 text-xs text-text-muted">{hint}</p>
            </article>
          ))}
        </div>
      </section>

      <div className="overview-content-grid grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(300px,0.8fr)]">
        <section className="overview-recent platform-surface overflow-hidden" aria-labelledby="overview-recent-title">
          <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border-subtle px-4 py-4 sm:px-5">
            <div>
              <h2 id="overview-recent-title" className="text-sm font-semibold text-text-primary">
                {copy.recentTitle}
              </h2>
              <p className="mt-1 text-xs text-text-muted">{copy.recentDescription}</p>
            </div>
            <Link className="text-xs font-semibold text-accent hover:underline" to="/meetings">
              {copy.viewAll}
            </Link>
          </header>
          {meetingsQuery.isPending ? (
            <LoadingState label={copy.recentLoading} />
          ) : meetingsQuery.isError ? (
            <div className="p-4"><ErrorState error={meetingsQuery.error} fallback={copy.recentError} /></div>
          ) : meetings.length === 0 ? (
            <div className="p-4"><EmptyState label={copy.recentEmpty} /></div>
          ) : (
            <ul className="overview-meeting-list m-0 list-none p-0">
              {meetings.map((meeting) => (
                <MeetingRow key={meeting.id} meeting={meeting} lang={lang} copy={copy} />
              ))}
            </ul>
          )}
        </section>

        <section className="overview-usage platform-surface overflow-hidden" aria-labelledby="overview-usage-title">
          <header className="border-b border-border-subtle px-4 py-4 sm:px-5">
            <h2 id="overview-usage-title" className="flex items-center gap-2 text-sm font-semibold text-text-primary">
              <Gauge size={16} aria-hidden="true" />
              {copy.usageTitle}
            </h2>
            <p className="mt-1 text-xs text-text-muted">{copy.usageDescription}</p>
          </header>
          {!can.readBilling ? (
            <p className="p-4 text-xs text-text-muted">{copy.usageRestricted}</p>
          ) : usageQuery.isPending ? (
            <LoadingState label={copy.usageLoading} />
          ) : usageQuery.isError ? (
            <div className="p-4"><ErrorState error={usageQuery.error} fallback={copy.usageError} /></div>
          ) : !usageQuery.data?.items.length ? (
            <div className="p-4"><EmptyState label={copy.usageEmpty} /></div>
          ) : (
            <>
              <div className="px-4 pt-4 sm:px-5"><AllowanceNotice item={audioUsage} /></div>
              <ul className="overview-usage-list m-0 list-none p-0">
                {usageQuery.data.items.map((item) => (
                  <UsageRow key={item.metric} item={item} lang={lang} copy={copy} />
                ))}
              </ul>
            </>
          )}
        </section>
      </div>
    </main>
  );
}
