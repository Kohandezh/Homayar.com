import { Search, X } from "lucide-react";
import { type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { ErrorCodeMessages } from "@/lib/error-messages";
import { formatDate, formatDuration, formatNumber, languageName } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMeetingSearch } from "@/lib/platform-api";
import MaskedText from "@/components/platform/MaskedText";
import { RESULTS_COPY } from "@/components/platform/results-copy";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";

/** The API's limits for `q` (backend/routers/v1/insights.py search_meetings). */
const MIN_QUERY = 2;
const MAX_QUERY = 160;
const RESULT_LIMIT = 50;

const COPY = {
  en: {
    eyebrow: "Workspace knowledge",
    title: "Search meetings",
    label: "Search transcripts, summaries, and action items",
    placeholder: "Search for a topic, decision, or action",
    submit: "Search",
    clear: "Clear search",
    prompt: "Enter at least two characters to search your workspace.",
    maxLength: "Up to 160 characters.",
    loading: "Searching meetings",
    error: "Search could not be completed.",
    noResults: "No meetings matched this search.",
    results: (count: string) => `${count} result(s)`,
    capped: (count: string) => `Showing the first ${count} results; search for something more specific.`,
    duration: "Duration",
    language: "Language",
    created: "Created",
    at: "from",
    openMeeting: "Open meeting",
    noPreview: "No preview is available for this result; open the meeting.",
    kinds: { title: "Title", transcript: "Transcript", summary: "Summary", action: "Action item" } as Record<string, string>,
  },
  fa: {
    eyebrow: "دانش فضای کاری",
    title: "جست‌وجوی جلسه‌ها",
    label: "جست‌وجو در متن، خلاصه و اقدام‌های جلسه‌ها",
    placeholder: "موضوع، تصمیم یا اقدام را جست‌وجو کنید",
    submit: "جست‌وجو",
    clear: "پاک کردن جست‌وجو",
    prompt: "برای جست‌وجو در فضای کاری حداقل دو نویسه وارد کنید.",
    maxLength: "حداکثر ۱۶۰ نویسه.",
    loading: "در حال جست‌وجوی جلسه‌ها",
    error: "جست‌وجو انجام نشد.",
    noResults: "جلسه‌ای مطابق این جست‌وجو پیدا نشد.",
    results: (count: string) => `${count} نتیجه`,
    capped: (count: string) => `${count} نتیجهٔ اول نمایش داده شده؛ عبارت دقیق‌تری جست‌وجو کنید.`,
    duration: "مدت",
    language: "زبان",
    created: "ایجاد",
    at: "از",
    openMeeting: "باز کردن جلسه",
    noPreview: "پیش‌نمایشی برای این نتیجه در دسترس نیست؛ جلسه را باز کنید.",
    kinds: { title: "عنوان", transcript: "متن گفت‌وگو", summary: "خلاصه", action: "اقدام" } as Record<string, string>,
  },
} as const;

const SEARCH_ERROR_CODES: ErrorCodeMessages = {
  search_query_too_short: {
    fa: "حداقل دو نویسه وارد کنید.",
    en: "Enter at least two characters.",
  },
  validation_error: {
    fa: "عبارت جست‌وجو باید بین ۲ تا ۱۶۰ نویسه باشد.",
    en: "The search text must be 2 to 160 characters long.",
  },
};

/**
 * Transcript matches open the meeting at the segment containing the time
 * (MeetingDetailPage reads `at`, focuses that segment and cues the player);
 * other matches open the meeting itself.
 */
function meetingUrl(meetingId: string, kind: string, timestampMs: number | null): string {
  if (kind !== "transcript" || timestampMs === null) return `/meetings/${meetingId}`;
  return `/meetings/${meetingId}?at=${Math.max(0, Math.round(timestampMs))}`;
}

/** Code points, as the API counts them (a UTF-16 length over-counts astral characters). */
function length(value: string) {
  return [...value].length;
}

export default function SearchPage() {
  const { lang } = useI18n();
  const copy = COPY[lang];
  const resultsCopy = RESULTS_COPY[lang];
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q")?.trim() ?? "";
  const valid = length(query) >= MIN_QUERY && length(query) <= MAX_QUERY;
  const searchQuery = useMeetingSearch(valid ? { q: query, limit: RESULT_LIMIT } : null);

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const nextQuery = String(form.get("q") ?? "").trim();
    const next = new URLSearchParams(searchParams);
    if (nextQuery) next.set("q", nextQuery);
    else next.delete("q");
    setSearchParams(next);
  }

  function clearSearch() {
    const next = new URLSearchParams(searchParams);
    next.delete("q");
    setSearchParams(next);
  }

  const items = searchQuery.data?.items ?? [];
  const resultCount = items.length;
  // Only this short status is announced; the result list itself is not a
  // live region (QA Q-06).
  let status = "";
  if (!query) status = "";
  else if (!valid) status = copy.prompt;
  else if (searchQuery.isError) status = copy.error;
  else if (searchQuery.isSuccess) status = resultCount ? copy.results(formatNumber(resultCount, lang)) : copy.noResults;

  return (
    <main className="search-page platform-page">
      <PageHeader eyebrow={copy.eyebrow} title={copy.title} />

      <section className="search-query-panel platform-surface p-4 sm:p-5" aria-label={copy.label}>
        <form className="search-form flex flex-col gap-3 sm:flex-row" role="search" onSubmit={submitSearch}>
          <label className="sr-only" htmlFor="meeting-search-input">{copy.label}</label>
          <div className="relative min-w-0 flex-1">
            <Search
              className="pointer-events-none absolute start-3 top-1/2 -translate-y-1/2 text-text-muted"
              size={17}
              aria-hidden="true"
            />
            <input
              key={query}
              id="meeting-search-input"
              className="platform-input ps-10 pe-10"
              name="q"
              type="search"
              defaultValue={query}
              placeholder={copy.placeholder}
              maxLength={MAX_QUERY}
              aria-describedby="meeting-search-help"
              autoComplete="off"
            />
            {query && (
              <button
                className="search-clear-button absolute end-1 top-1/2 grid size-8 -translate-y-1/2 place-items-center rounded-md text-text-muted hover:bg-bg-elevated hover:text-text-primary"
                type="button"
                onClick={clearSearch}
                aria-label={copy.clear}
                title={copy.clear}
              >
                <X size={16} aria-hidden="true" />
              </button>
            )}
          </div>
          <button className="platform-button platform-button-primary shrink-0" type="submit">
            <Search size={15} aria-hidden="true" />
            {copy.submit}
          </button>
        </form>
        <p id="meeting-search-help" className="mt-2 mb-0 text-xs text-text-muted">{copy.maxLength}</p>
      </section>

      <p className="sr-only" role="status" aria-live="polite">{status}</p>

      <section className="search-results mt-5" aria-busy={searchQuery.isFetching} aria-label={copy.title}>
        {!valid ? (
          <EmptyState label={copy.prompt} />
        ) : searchQuery.isPending ? (
          <LoadingState label={copy.loading} />
        ) : searchQuery.isError ? (
          <ErrorState error={searchQuery.error} codes={SEARCH_ERROR_CODES} fallback={copy.error} />
        ) : resultCount === 0 ? (
          <EmptyState label={copy.noResults} />
        ) : (
          <>
            <p className="search-result-count mb-3 text-xs font-medium text-text-secondary" aria-hidden="true">
              {copy.results(formatNumber(resultCount, lang))}
            </p>
            {resultCount >= RESULT_LIMIT && (
              <p className="mb-3 text-xs text-text-muted">{copy.capped(formatNumber(RESULT_LIMIT, lang))}</p>
            )}
            <ol className="search-result-list m-0 grid list-none gap-3 p-0">
              {items.map((meeting) => (
                <li key={meeting.id}>
                  <article className="search-result-card platform-surface overflow-hidden">
                    <header className="flex min-w-0 flex-col gap-3 border-b border-border-subtle px-4 py-4 sm:flex-row sm:items-start sm:justify-between sm:px-5">
                      <div className="min-w-0">
                        <Link
                          className="block truncate text-sm font-semibold text-text-primary hover:text-accent hover:underline"
                          to={`/meetings/${meeting.id}`}
                          aria-label={`${copy.openMeeting}: ${meeting.title}`}
                          dir="auto"
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
                            <dd>{languageName(meeting.language, lang)}</dd>
                          </div>
                          <div className="flex gap-1">
                            <dt>{copy.created}:</dt>
                            <dd><time dateTime={meeting.created_at}>{formatDate(meeting.created_at, lang)}</time></dd>
                          </div>
                        </dl>
                      </div>
                      <StatusBadge status={meeting.status} />
                    </header>

                    {meeting.matches.length === 0 ? (
                      <p className="m-0 px-4 py-3 text-sm text-text-secondary sm:px-5">{copy.noPreview}</p>
                    ) : (
                      <ul className="search-match-list m-0 list-none divide-y divide-border-subtle p-0">
                        {meeting.matches.map((match, index) => {
                          const kind = copy.kinds[match.kind] ?? match.kind;
                          const time = match.kind === "transcript" && match.timestamp_ms !== null
                            ? formatDuration(match.timestamp_ms / 1000, lang)
                            : null;
                          const name = `${kind}: ${match.snippet || copy.noPreview} — ${meeting.title}${time ? `${lang === "fa" ? "،" : ","} ${copy.at} ${time}` : ""}`;
                          return (
                            <li key={`${match.kind}-${match.timestamp_ms ?? "context"}-${index}`} className="search-match px-4 py-3 sm:px-5">
                              <Link
                                className="group grid gap-2 text-start no-underline sm:grid-cols-[92px_minmax(0,1fr)] sm:items-start"
                                to={meetingUrl(meeting.id, match.kind, match.timestamp_ms)}
                                aria-label={name}
                              >
                                <span className="inline-flex w-fit items-center gap-1 rounded-md bg-bg-elevated px-2 py-1 text-xs font-semibold text-accent group-hover:bg-accent group-hover:text-white">
                                  {time ? <bdi dir="ltr">{time}</bdi> : kind}
                                </span>
                                <span className="min-w-0">
                                  {time && (
                                    <span className="block text-[0.68rem] font-semibold text-text-muted">{kind}</span>
                                  )}
                                  <span className="mt-1 block text-sm leading-6 text-text-secondary group-hover:text-text-primary" dir="auto">
                                    {match.snippet ? <MaskedText text={match.snippet} copy={resultsCopy} revealed={{}} /> : copy.noPreview}
                                  </span>
                                </span>
                              </Link>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </article>
                </li>
              ))}
            </ol>
          </>
        )}
      </section>
    </main>
  );
}
