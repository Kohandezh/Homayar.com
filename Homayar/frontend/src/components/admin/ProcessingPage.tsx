import { useMemo, useState } from "react";
import { useSearchParams, Link } from "react-router-dom";
import { Button, Table } from "@heroui/react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import JobDrawer from "@/components/admin/processing/JobDrawer";
import JobFilterBar from "@/components/admin/processing/JobFilterBar";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { formatDate, formatNumber } from "@/lib/format";
import {
  jobQueryParams,
  readJobFilters,
  writeJobFilters,
  type JobFilters,
} from "@/lib/job-filters";
import { failureClassLabel, jobStageLabel } from "@/lib/job-labels";
import { useI18n } from "@/lib/i18n-context";
import { useAdminJobs } from "@/lib/platform-api";
import type { AdminJob } from "@/types/platform";

/** Error codes of GET /admin/jobs (backend/routers/v1/admin.py list_jobs). */
const JOB_ERROR_CODES: ErrorCodeMessages = {
  invalid_cursor: {
    fa: "فهرست پردازش‌ها تغییر کرده است؛ از صفحهٔ اول دوباره ببینید.",
    en: "The job list has changed; start again from the first page.",
  },
};

interface Paging {
  filterKey: string;
  /** One entry per page seen; the first page has no cursor. */
  cursors: Array<string | undefined>;
}

export default function ProcessingPage() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readJobFilters(searchParams), [searchParams]);
  const [selected, setSelected] = useState<AdminJob | null>(null);
  // The cursors of the pages walked so far, kept next to the filters they
  // belong to: other filters describe a different list, where an old cursor
  // means nothing, so paging starts from the first page again.
  const filterKey = useMemo(() => writeJobFilters(filters).toString(), [filters]);
  const [paging, setPaging] = useState<Paging>({ filterKey, cursors: [undefined] });
  const cursors = paging.filterKey === filterKey ? paging.cursors : [undefined];
  const cursor = cursors[cursors.length - 1];

  function goTo(next: Array<string | undefined>) {
    setPaging({ filterKey, cursors: next });
  }

  const params = useMemo(() => jobQueryParams(filters, { cursor }), [filters, cursor]);
  const jobsQuery = useAdminJobs(params);
  const items = jobsQuery.data?.items ?? [];
  const errorCode = jobsQuery.isError ? apiErrorInfo(jobsQuery.error).code : null;
  const pageNumber = formatNumber(cursors.length, lang);
  // The open row follows the refreshed list, so a replay updates the drawer.
  const openJob = selected ? items.find((job) => job.id === selected.id) ?? selected : null;

  // A cursor the server rejected is dropped at once, without a click and
  // before anything renders from it: React re-runs the render with the first
  // page, so no retry can re-send the rejected cursor either. This is the
  // render-time state adjustment the React docs describe for "state that
  // changes in response to what a query returned".
  if (errorCode === "invalid_cursor" && cursors.length > 1) {
    setPaging({ filterKey, cursors: [undefined] });
    return null;
  }

  function applyFilters(next: JobFilters) {
    setSearchParams(writeJobFilters(next, searchParams));
  }

  function pair(current: number, max: number) {
    return `${formatNumber(current, lang)}/${formatNumber(max, lang)}`;
  }

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={isFa ? "مدیریت" : "Administration"}
        title={isFa ? "پردازش" : "Processing"}
      />
      <p className="mt-0 mb-4 text-sm text-muted">
        {isFa
          ? "چه چیزی در پردازش متوقف شده، چرا، و چه چیزی را می‌توان دوباره اجرا کرد. پردازش‌ها از تازه به قدیم فهرست می‌شوند."
          : "What stopped in processing, why, and what can be run again. Jobs are listed newest first."}
      </p>

      <JobFilterBar filters={filters} onChange={applyFilters} />

      <div className="mt-4">
        {jobsQuery.isPending ? (
          <LoadingState label={isFa ? "در حال دریافت پردازش‌ها" : "Loading jobs"} />
        ) : jobsQuery.isError ? (
          <div className="grid gap-3">
            <ErrorState
              error={jobsQuery.error}
              codes={JOB_ERROR_CODES}
              fallback={isFa ? "دریافت فهرست پردازش‌ها ناموفق بود." : "Could not load the job list."}
            />
            <Button
              variant="secondary"
              size="sm"
              className="justify-self-start"
              onPress={() => void jobsQuery.refetch()}
            >
              {isFa ? "تلاش دوباره" : "Try again"}
            </Button>
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            label={isFa
              ? "کار پردازشی با این فیلترها یافت نشد."
              : "No processing jobs match these filters."}
          />
        ) : (
          <>
            <Table aria-busy={jobsQuery.isFetching}>
              <Table.ScrollContainer>
                <Table.Content aria-label={isFa ? "کارهای پردازش" : "Processing jobs"}>
                  <Table.Header>
                    <Table.Column isRowHeader>{isFa ? "جلسه" : "Meeting"}</Table.Column>
                    <Table.Column>{isFa ? "مرحله" : "Stage"}</Table.Column>
                    <Table.Column>{isFa ? "خطا" : "Failure"}</Table.Column>
                    <Table.Column>{isFa ? "تلاش‌ها" : "Attempts"}</Table.Column>
                    <Table.Column>{isFa ? "پردازش دوباره" : "Replays"}</Table.Column>
                    <Table.Column>{isFa ? "آخرین تغییر" : "Updated"}</Table.Column>
                    {/* Holds each row's Details button; no heading of its own. */}
                    <Table.Column aria-label={isFa ? "عملیات" : "Actions"}>{""}</Table.Column>
                  </Table.Header>
                  <Table.Body>
                    {items.map((job) => (
                      <Table.Row key={job.id} id={job.id}>
                        <Table.Cell>
                          <Link to={`/meetings/${job.meeting_id}`}>
                            {/* User content: no forced direction, the browser
                                reads the title's own order. */}
                            <bdi>{job.meeting_title}</bdi>
                          </Link>
                        </Table.Cell>
                        <Table.Cell>{jobStageLabel(job.stage, lang)}</Table.Cell>
                        <Table.Cell>
                          {job.failure_class || job.failure_code ? (
                            <span className="grid gap-0.5">
                              {job.failure_class && (
                                <span>{failureClassLabel(job.failure_class, lang)}</span>
                              )}
                              {/* An unknown code is shown as sent, never guessed. */}
                              {job.failure_code && (
                                <bdi dir="ltr" className="font-mono text-xs">
                                  {job.failure_code}
                                </bdi>
                              )}
                            </span>
                          ) : (
                            <span className="text-muted">—</span>
                          )}
                        </Table.Cell>
                        <Table.Cell>{pair(job.attempt_count, job.max_attempts)}</Table.Cell>
                        <Table.Cell>{pair(job.replay_count, job.max_replays)}</Table.Cell>
                        <Table.Cell>
                          <time dateTime={job.updated_at}>{formatDate(job.updated_at, lang)}</time>
                        </Table.Cell>
                        <Table.Cell>
                          <Button
                            variant="secondary"
                            size="sm"
                            onPress={() => setSelected(job)}
                          >
                            {isFa ? "جزئیات" : "Details"}
                          </Button>
                        </Table.Cell>
                      </Table.Row>
                    ))}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>

            <nav
              className="mt-3 flex items-center justify-between gap-3"
              aria-label={isFa ? "صفحه‌بندی کارهای پردازش" : "Processing jobs pagination"}
            >
              <Button
                variant="secondary"
                size="sm"
                // Still focusable while a page loads, so focus is never dropped to the page.
                aria-disabled={cursors.length === 1 || jobsQuery.isFetching}
                onPress={() => {
                  if (cursors.length === 1 || jobsQuery.isFetching) return;
                  goTo(cursors.slice(0, -1));
                }}
              >
                <ChevronLeft size={15} aria-hidden="true" className="rtl-mirror" />
                {isFa ? "قبلی" : "Previous"}
              </Button>
              <span className="text-xs text-muted" aria-live="polite">
                {isFa ? `صفحه ${pageNumber}` : `Page ${pageNumber}`}
              </span>
              <Button
                variant="secondary"
                size="sm"
                aria-disabled={!jobsQuery.data?.next_cursor || jobsQuery.isFetching}
                onPress={() => {
                  const next = jobsQuery.data?.next_cursor;
                  if (!next || jobsQuery.isFetching) return;
                  goTo([...cursors, next]);
                }}
              >
                {isFa ? "بعدی" : "Next"}
                <ChevronRight size={15} aria-hidden="true" className="rtl-mirror" />
              </Button>
            </nav>
          </>
        )}
      </div>

      <JobDrawer job={openJob} onClose={() => setSelected(null)} />
    </div>
  );
}
