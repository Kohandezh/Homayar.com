import { isAxiosError } from "axios";
import {
  Calendar,
  Clock,
  FileText,
  Languages,
  ListChecks,
  Lock,
} from "lucide-react";
import { Link, useLocation, useParams } from "react-router";

import { useI18n } from "@/lib/i18n-context";
import { QualityPanel } from "@/components/platform/QualityPanel";
import { useSharedMeeting } from "@/lib/platform-api";
import type { SummaryInsight } from "@/types/platform";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";

const COPY = {
  en: {
    sharedBy: "Shared securely with Homayar",
    meeting: "Shared meeting",
    loading: "Loading shared meeting",
    invalid: "This share link is invalid.",
    unavailable: "This share link is unavailable or has expired.",
    rateLimited: "Too many requests. Please wait before trying again.",
    error: "The shared meeting could not be loaded.",
    authTitle: "Sign in required",
    authMessage: "This meeting was shared with authenticated viewers only.",
    signIn: "Sign in to continue",
    duration: "Duration",
    language: "Language",
    created: "Created",
    summary: "Summary",
    noSummary: "A summary was not included with this shared meeting.",
    decisions: "Decisions",
    keyPoints: "Key points",
    actions: "Action items",
    noActions: "No action items were included with this shared meeting.",
    assignee: "Assignee",
    due: "Due",
    unassigned: "Unassigned",
    transcript: "Transcript",
    noTranscript: "A transcript was not included with this shared meeting.",
    speaker: "Speaker",
  },
  fa: {
    sharedBy: "اشتراک‌گذاری امن با همیار",
    meeting: "جلسه اشتراک‌گذاری‌شده",
    loading: "در حال دریافت جلسه",
    invalid: "این پیوند اشتراک معتبر نیست.",
    unavailable: "این پیوند در دسترس نیست یا منقضی شده است.",
    rateLimited: "تعداد درخواست‌ها زیاد است؛ کمی بعد دوباره تلاش کنید.",
    error: "جلسه اشتراک‌گذاری‌شده دریافت نشد.",
    authTitle: "ورود الزامی است",
    authMessage: "این جلسه فقط با کاربران واردشده به اشتراک گذاشته شده است.",
    signIn: "ورود و ادامه",
    duration: "مدت",
    language: "زبان",
    created: "ایجاد",
    summary: "خلاصه",
    noSummary: "خلاصه‌ای همراه این جلسه به اشتراک گذاشته نشده است.",
    decisions: "تصمیم‌ها",
    keyPoints: "نکات کلیدی",
    actions: "اقدامات",
    noActions: "اقدامی همراه این جلسه به اشتراک گذاشته نشده است.",
    assignee: "مسئول",
    due: "سررسید",
    unassigned: "بدون مسئول",
    transcript: "متن جلسه",
    noTranscript: "متن جلسه همراه این اشتراک قرار نگرفته است.",
    speaker: "گوینده",
  },
} as const;

function formatDuration(seconds: number) {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const remainder = total % 60;
  if (hours > 0) {
    return `${hours}:${minutes.toString().padStart(2, "0")}:${remainder.toString().padStart(2, "0")}`;
  }
  return `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

function formatDate(value: string, lang: "fa" | "en") {
  return new Intl.DateTimeFormat(lang === "fa" ? "fa-IR" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

interface ShareErrorPayload {
  detail?: { code?: string; message?: string } | string;
  error?: { code?: string; message?: string };
}

function shareErrorStatus(error: unknown): number | undefined {
  return isAxiosError<ShareErrorPayload>(error) ? error.response?.status : undefined;
}

function insightText(insight: SummaryInsight): string | null {
  return insight.description?.trim() || insight.category?.trim() || null;
}

function InsightList({ items }: { items: SummaryInsight[] }) {
  const visibleItems = items.map(insightText).filter((item): item is string => Boolean(item));
  if (visibleItems.length === 0) return null;

  return (
    <ul className="shared-insight-list mt-3 grid gap-2 ps-5 text-sm leading-6 text-text-secondary">
      {visibleItems.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}
    </ul>
  );
}

export default function SharedMeetingPage() {
  const { lang } = useI18n();
  const copy = COPY[lang];
  const { shareToken } = useParams<{ shareToken: string }>();
  const location = useLocation();
  const sharedQuery = useSharedMeeting(shareToken);

  if (!shareToken) {
    return (
      <main className="shared-meeting-page min-h-screen bg-bg-primary px-4 py-8 sm:px-6">
        <div className="mx-auto w-full max-w-5xl">
          <ErrorState error={undefined} fallback={copy.invalid} />
        </div>
      </main>
    );
  }

  if (sharedQuery.isPending) {
    return (
      <main className="shared-meeting-page min-h-screen bg-bg-primary px-4 py-8 sm:px-6">
        <div className="mx-auto w-full max-w-5xl">
          <LoadingState label={copy.loading} />
        </div>
      </main>
    );
  }

  if (sharedQuery.isError) {
    const status = shareErrorStatus(sharedQuery.error);
    if (status === 401) {
      return (
        <main className="shared-meeting-page min-h-screen bg-bg-primary px-4 py-8 sm:px-6">
          <section className="shared-auth-state platform-surface mx-auto flex min-h-72 w-full max-w-xl flex-col items-center justify-center px-6 py-10 text-center" aria-labelledby="shared-auth-title">
            <span className="grid size-11 place-items-center rounded-md bg-bg-elevated text-accent">
              <Lock size={20} aria-hidden="true" />
            </span>
            <h1 id="shared-auth-title" className="mt-4 text-lg font-semibold text-text-primary">
              {copy.authTitle}
            </h1>
            <p className="mt-2 max-w-sm text-sm leading-6 text-text-secondary">{copy.authMessage}</p>
            <Link
              className="platform-button platform-button-primary mt-5"
              to="/login"
              state={{ from: { pathname: location.pathname, search: location.search, hash: location.hash } }}
            >
              {copy.signIn}
            </Link>
          </section>
        </main>
      );
    }

    const fallback = status === 404
      ? copy.unavailable
      : status === 429
        ? copy.rateLimited
        : copy.error;
    return (
      <main className="shared-meeting-page min-h-screen bg-bg-primary px-4 py-8 sm:px-6">
        <div className="mx-auto w-full max-w-5xl">
          <ErrorState error={undefined} fallback={fallback} />
        </div>
      </main>
    );
  }

  const meeting = sharedQuery.data;

  return (
    <main className="shared-meeting-page min-h-screen bg-bg-primary px-4 py-6 sm:px-6 sm:py-8">
      <div className="shared-meeting-shell mx-auto w-full max-w-5xl">
        <header className="shared-brand-header mb-6 flex items-center justify-between gap-3 border-b border-border-subtle pb-4">
          <a className="text-base font-semibold text-text-primary no-underline hover:text-accent" href="/">
            Homayar
          </a>
          <span className="text-xs font-medium text-text-muted">{copy.sharedBy}</span>
        </header>

        <PageHeader eyebrow={copy.meeting} title={meeting.title} />

        <dl className="shared-meeting-meta platform-surface mb-5 grid grid-cols-1 divide-y divide-border-subtle sm:grid-cols-3 sm:divide-x sm:divide-y-0 rtl:sm:divide-x-reverse">
          <div className="flex items-center gap-3 px-4 py-3">
            <Clock className="shrink-0 text-accent" size={16} aria-hidden="true" />
            <div>
              <dt className="text-[0.68rem] font-medium text-text-muted">{copy.duration}</dt>
              <dd className="mt-0.5 text-sm font-semibold text-text-primary">{formatDuration(meeting.duration_seconds)}</dd>
            </div>
          </div>
          <div className="flex items-center gap-3 px-4 py-3">
            <Languages className="shrink-0 text-info" size={16} aria-hidden="true" />
            <div>
              <dt className="text-[0.68rem] font-medium text-text-muted">{copy.language}</dt>
              <dd className="mt-0.5 text-sm font-semibold text-text-primary">{meeting.language.toUpperCase()}</dd>
            </div>
          </div>
          <div className="flex items-center gap-3 px-4 py-3">
            <Calendar className="shrink-0 text-accent-secondary" size={16} aria-hidden="true" />
            <div>
              <dt className="text-[0.68rem] font-medium text-text-muted">{copy.created}</dt>
              <dd className="mt-0.5 text-sm font-semibold text-text-primary">
                <time dateTime={meeting.created_at}>{formatDate(meeting.created_at, lang)}</time>
              </dd>
            </div>
          </div>
        </dl>

        {meeting.quality && <QualityPanel quality={meeting.quality} lang={lang} />}

        <div className="shared-meeting-content grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,1.5fr)_minmax(280px,0.75fr)] lg:items-start">
          <div className="grid min-w-0 gap-5">
            <section className="shared-summary platform-surface p-4 sm:p-5" aria-labelledby="shared-summary-title">
              <h2 id="shared-summary-title" className="flex items-center gap-2 text-sm font-semibold text-text-primary">
                <FileText size={16} aria-hidden="true" />
                {copy.summary}
              </h2>
              {meeting.summary ? (
                <div className="shared-summary-content mt-4">
                  <p className="whitespace-pre-wrap text-sm leading-7 text-text-secondary">{meeting.summary.overview}</p>
                  {meeting.summary.decisions.length > 0 && (
                    <section className="shared-decisions mt-5" aria-labelledby="shared-decisions-title">
                      <h3 id="shared-decisions-title" className="text-xs font-semibold text-text-primary">{copy.decisions}</h3>
                      <InsightList items={meeting.summary.decisions} />
                    </section>
                  )}
                  {meeting.summary.key_points.length > 0 && (
                    <section className="shared-key-points mt-5" aria-labelledby="shared-key-points-title">
                      <h3 id="shared-key-points-title" className="text-xs font-semibold text-text-primary">{copy.keyPoints}</h3>
                      <InsightList items={meeting.summary.key_points} />
                    </section>
                  )}
                </div>
              ) : (
                <div className="mt-4"><EmptyState label={copy.noSummary} /></div>
              )}
            </section>

            <section className="shared-transcript platform-surface overflow-hidden" aria-labelledby="shared-transcript-title">
              <header className="border-b border-border-subtle px-4 py-4 sm:px-5">
                <h2 id="shared-transcript-title" className="text-sm font-semibold text-text-primary">{copy.transcript}</h2>
              </header>
              {meeting.transcript?.segments.length ? (
                <ol className="shared-transcript-list m-0 list-none divide-y divide-border-subtle p-0">
                  {meeting.transcript.segments.map((segment) => (
                    <li key={segment.id} className="shared-transcript-segment grid gap-2 px-4 py-4 sm:grid-cols-[88px_minmax(0,1fr)] sm:px-5">
                      <time
                        className="w-fit rounded-md bg-bg-elevated px-2 py-1 text-xs font-semibold text-accent"
                        dateTime={`PT${Math.max(0, segment.start_ms) / 1000}S`}
                      >
                        {formatDuration(segment.start_ms / 1000)}
                      </time>
                      <div className="min-w-0">
                        <p className="text-xs font-semibold text-text-primary">
                          {segment.speaker_name || `${copy.speaker} ${segment.speaker_key}`}
                        </p>
                        <p className="mt-1 whitespace-pre-wrap text-sm leading-6 text-text-secondary">{segment.text}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              ) : (
                <div className="p-4"><EmptyState label={copy.noTranscript} /></div>
              )}
            </section>
          </div>

          <aside className="shared-actions platform-surface overflow-hidden" aria-labelledby="shared-actions-title">
            <header className="flex items-center gap-2 border-b border-border-subtle px-4 py-4 sm:px-5">
              <ListChecks size={16} aria-hidden="true" />
              <h2 id="shared-actions-title" className="text-sm font-semibold text-text-primary">{copy.actions}</h2>
            </header>
            {meeting.actions.length > 0 ? (
              <ol className="shared-action-list m-0 list-none divide-y divide-border-subtle p-0">
                {meeting.actions.map((action) => (
                  <li key={action.id} className="shared-action-item px-4 py-4 sm:px-5">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <StatusBadge status={action.status} />
                      <span className="text-[0.68rem] font-semibold uppercase text-text-muted">{action.priority}</span>
                    </div>
                    <p className="mt-3 text-sm leading-6 text-text-primary">{action.description}</p>
                    <dl className="mt-3 grid gap-2 text-xs text-text-muted">
                      <div className="flex justify-between gap-3">
                        <dt>{copy.assignee}</dt>
                        <dd className="text-end font-medium text-text-secondary">{action.assignee_text || copy.unassigned}</dd>
                      </div>
                      {action.due_at && (
                        <div className="flex justify-between gap-3">
                          <dt>{copy.due}</dt>
                          <dd className="text-end font-medium text-text-secondary">
                            <time dateTime={action.due_at}>{formatDate(action.due_at, lang)}</time>
                          </dd>
                        </div>
                      )}
                    </dl>
                  </li>
                ))}
              </ol>
            ) : (
              <div className="p-4"><EmptyState label={copy.noActions} /></div>
            )}
          </aside>
        </div>
      </div>
    </main>
  );
}
