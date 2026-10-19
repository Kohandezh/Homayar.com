import { useMemo, useState } from "react";
import { Link } from "react-router";
import { AudioLines, Filter } from "lucide-react";
import { useI18n } from "@/lib/i18n-context";
import { formatDate, formatDuration } from "@/lib/format";
import { useMeetings } from "@/lib/platform-api";
import { NONTERMINAL_MEETING_STATUSES, statusLabel } from "@/lib/status-labels";
import type { MeetingStatus } from "@/types/platform";
import UploadPanel from "@/components/platform/UploadPanel";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";

const FILTER_STATUSES: MeetingStatus[] = ["queued", "processing", "transcribing", "analyzing", "completed", "partial", "failed"];

const COPY = {
  en: {
    title: "Meetings",
    processing: (count: number) => `${count} processing`,
    allStatuses: "All statuses",
    statusFilter: "Filter by status",
    meeting: "Meeting",
    status: "Status",
    duration: "Duration",
    language: "Language",
    created: "Created",
    loading: "Loading meetings",
    failed: "Meetings could not be loaded",
    empty: "No meetings yet",
  },
  fa: {
    title: "جلسه‌ها",
    processing: (count: number) => `${new Intl.NumberFormat("fa-IR").format(count)} در حال پردازش`,
    allStatuses: "همهٔ وضعیت‌ها",
    statusFilter: "پالایش بر اساس وضعیت",
    meeting: "جلسه",
    status: "وضعیت",
    duration: "مدت",
    language: "زبان",
    created: "تاریخ",
    loading: "در حال دریافت جلسه‌ها",
    failed: "فهرست جلسه‌ها دریافت نشد",
    empty: "هنوز جلسه‌ای ثبت نشده است",
  },
} as const;

export default function MeetingsPage() {
  const { lang } = useI18n();
  const copy = COPY[lang];
  const [status, setStatus] = useState<MeetingStatus | "">("");
  const meetings = useMeetings({ limit: 100, status: status || undefined });

  const activeCount = useMemo(
    () =>
      meetings.data?.items.filter((meeting) =>
        (NONTERMINAL_MEETING_STATUSES as readonly string[]).includes(meeting.status),
      ).length ?? 0,
    [meetings.data],
  );

  return (
    <section className="platform-page meetings-page">
      <PageHeader eyebrow={activeCount ? copy.processing(activeCount) : undefined} title={copy.title} />

      <UploadPanel />

      <div className="meeting-list-toolbar">
        <div><AudioLines size={17} aria-hidden="true" /><strong>{meetings.data?.items.length ?? 0}</strong></div>
        <label>
          <Filter size={14} aria-hidden="true" />
          <select
            value={status}
            onChange={(event) => setStatus(event.target.value as MeetingStatus | "")}
            aria-label={copy.statusFilter}
          >
            <option value="">{copy.allStatuses}</option>
            {FILTER_STATUSES.map((value) => (
              <option key={value} value={value}>{statusLabel(value, lang)}</option>
            ))}
          </select>
        </label>
      </div>

      {meetings.isLoading ? (
        <LoadingState label={copy.loading} />
      ) : meetings.isError ? (
        <ErrorState error={meetings.error} fallback={copy.failed} />
      ) : !meetings.data?.items.length ? (
        <EmptyState label={copy.empty} />
      ) : (
        <div className="platform-surface meeting-table-wrap">
          <table className="meeting-table">
            <thead>
              <tr>
                <th>{copy.meeting}</th>
                <th>{copy.status}</th>
                <th>{copy.duration}</th>
                <th>{copy.language}</th>
                <th>{copy.created}</th>
              </tr>
            </thead>
            <tbody>
              {meetings.data.items.map((meeting) => (
                <tr key={meeting.id}>
                  <td data-label={copy.meeting}>
                    <Link to={meeting.id}>{meeting.title}</Link>
                    <span>{meeting.source}</span>
                  </td>
                  <td data-label={copy.status}><StatusBadge status={meeting.status} /></td>
                  <td data-label={copy.duration}><bdi dir="ltr">{formatDuration(meeting.duration_seconds, lang)}</bdi></td>
                  <td data-label={copy.language}>{meeting.language.toUpperCase()}</td>
                  <td data-label={copy.created}>{formatDate(meeting.created_at, lang)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
