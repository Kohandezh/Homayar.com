import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { Button, Table } from "@heroui/react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { getLocalTimeZone } from "@internationalized/date";

import AuditEventDrawer from "@/components/admin/audit/AuditEventDrawer";
import AuditFilterBar from "@/components/admin/audit/AuditFilterBar";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { auditEventLabel, auditTargetLabel } from "@/lib/audit-labels";
import {
  auditQueryParams,
  hasAuditFilters,
  readAuditFilters,
  writeAuditFilters,
  type AuditFilters,
} from "@/lib/audit-filters";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useAuditEvents, useMembers } from "@/lib/platform-api";
import type { AuditEvent, WorkspaceMember } from "@/types/platform";

/** Error codes of GET /admin/audit-events (backend/routers/v1/admin.py). */
const AUDIT_ERROR_CODES: ErrorCodeMessages = {
  invalid_cursor: {
    fa: "فهرست رویدادها تغییر کرده است؛ از صفحهٔ اول دوباره ببینید.",
    en: "The event list has changed; start again from the first page.",
  },
  invalid_audit_range: {
    fa: "بازهٔ زمانی انتخاب‌شده معتبر نیست؛ آن را پاک کنید و دوباره انتخاب کنید.",
    en: "The chosen date range is not valid; clear it and choose again.",
  },
  workspace_not_found: {
    fa: "این فضای کاری در دسترس نیست.",
    en: "This workspace is not available.",
  },
};

function memberName(member: WorkspaceMember): string {
  return member.display_name || member.email || member.phone_e164 || member.user_id;
}

interface Paging {
  filterKey: string;
  /** One entry per page seen; the first page has no cursor. */
  cursors: Array<string | undefined>;
}

export default function AuditLogPage() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readAuditFilters(searchParams), [searchParams]);
  // The chosen days count in the reader's own time zone.
  const timeZone = useMemo(() => getLocalTimeZone(), []);
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  // The cursors of the pages walked so far, kept next to the filters they
  // belong to: other filters describe a different list, where an old cursor
  // means nothing, so paging starts from the first page again.
  const filterKey = useMemo(() => writeAuditFilters(filters).toString(), [filters]);
  const [paging, setPaging] = useState<Paging>({ filterKey, cursors: [undefined] });
  const cursors = paging.filterKey === filterKey ? paging.cursors : [undefined];
  const cursor = cursors[cursors.length - 1];

  function goTo(next: Array<string | undefined>) {
    setPaging({ filterKey, cursors: next });
  }

  const params = useMemo(
    () => auditQueryParams(filters, { cursor, timeZone }),
    [filters, cursor, timeZone],
  );
  const auditQuery = useAuditEvents(params);
  const membersQuery = useMembers();
  const members = useMemo(() => membersQuery.data?.items ?? [], [membersQuery.data]);
  const memberNames = useMemo(
    () => new Map(members.map((member) => [member.user_id, memberName(member)])),
    [members],
  );

  const errorCode = auditQuery.isError ? apiErrorInfo(auditQuery.error).code : null;
  const pageNumber = formatNumber(cursors.length, lang);
  const items = auditQuery.data?.items ?? [];

  function actorOf(event: AuditEvent): string {
    if (!event.actor_user_id) return isFa ? "سیستم" : "System";
    return memberNames.get(event.actor_user_id) ?? event.actor_user_id;
  }

  function applyFilters(next: AuditFilters) {
    setSearchParams(writeAuditFilters(next, searchParams));
  }

  function firstPage() {
    goTo([undefined]);
  }

  function clearDateRange() {
    applyFilters({ ...filters, from: "", to: "" });
  }

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={isFa ? "مدیریت" : "Administration"}
        title={isFa ? "گزارش رویدادها" : "Audit log"}
      />
      <p className="mt-0 mb-4 text-sm text-muted">
        {isFa
          ? "چه کسی، چه کاری و چه زمانی انجام داده است. رویدادها از تازه به قدیم فهرست می‌شوند."
          : "Who did what, and when. Events are listed newest first."}
      </p>

      <AuditFilterBar filters={filters} members={members} onChange={applyFilters} />

      <div className="mt-4">
        {auditQuery.isPending ? (
          <LoadingState label={isFa ? "در حال دریافت رویدادها" : "Loading audit events"} />
        ) : auditQuery.isError ? (
          <div className="grid gap-3">
            <ErrorState
              error={auditQuery.error}
              codes={AUDIT_ERROR_CODES}
              fallback={isFa ? "دریافت گزارش رویدادها ناموفق بود." : "Could not load the audit log."}
            />
            {errorCode === "invalid_cursor" && (
              <Button variant="secondary" size="sm" className="justify-self-start" onPress={firstPage}>
                {isFa ? "صفحهٔ اول" : "First page"}
              </Button>
            )}
            {errorCode === "invalid_audit_range" && (
              <Button variant="secondary" size="sm" className="justify-self-start" onPress={clearDateRange}>
                {isFa ? "پاک کردن بازهٔ زمانی" : "Clear the date range"}
              </Button>
            )}
          </div>
        ) : items.length === 0 ? (
          <EmptyState
            label={hasAuditFilters(filters)
              ? (isFa ? "رویدادی با این فیلترها یافت نشد." : "No events match these filters.")
              : (isFa ? "هنوز رویدادی ثبت نشده است." : "No events have been recorded yet.")}
          />
        ) : (
          <>
            <Table aria-busy={auditQuery.isFetching}>
              <Table.ScrollContainer>
                <Table.Content aria-label={isFa ? "رویدادهای ممیزی" : "Audit events"}>
                  <Table.Header>
                    <Table.Column isRowHeader>{isFa ? "زمان" : "Time"}</Table.Column>
                    <Table.Column>{isFa ? "رویداد" : "Event"}</Table.Column>
                    <Table.Column>{isFa ? "عامل" : "Actor"}</Table.Column>
                    <Table.Column>{isFa ? "هدف" : "Target"}</Table.Column>
                    <Table.Column>{isFa ? "نتیجه" : "Outcome"}</Table.Column>
                  </Table.Header>
                  <Table.Body>
                    {items.map((event) => (
                      <Table.Row key={event.id} id={event.id} onAction={() => setSelected(event)}>
                        <Table.Cell>
                          <time dateTime={event.occurred_at}>{formatDate(event.occurred_at, lang)}</time>
                        </Table.Cell>
                        <Table.Cell>
                          {/* An unknown type is shown as sent, never guessed. */}
                          {auditEventLabel(event.event_type, lang) ?? (
                            <bdi dir="ltr">{event.event_type}</bdi>
                          )}
                        </Table.Cell>
                        <Table.Cell><bdi dir="auto">{actorOf(event)}</bdi></Table.Cell>
                        <Table.Cell>
                          {event.target_type
                            ? auditTargetLabel(event.target_type, lang)
                            : (isFa ? "بدون هدف" : "None")}
                        </Table.Cell>
                        <Table.Cell><StatusBadge status={event.outcome} /></Table.Cell>
                      </Table.Row>
                    ))}
                  </Table.Body>
                </Table.Content>
              </Table.ScrollContainer>
            </Table>

            <nav
              className="mt-3 flex items-center justify-between gap-3"
              aria-label={isFa ? "صفحه‌بندی گزارش رویدادها" : "Audit log pagination"}
            >
              <Button
                variant="secondary"
                size="sm"
                // Still focusable while a page loads, so focus is never dropped to the page.
                aria-disabled={cursors.length === 1 || auditQuery.isFetching}
                onPress={() => {
                  if (cursors.length === 1 || auditQuery.isFetching) return;
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
                aria-disabled={!auditQuery.data?.next_cursor || auditQuery.isFetching}
                onPress={() => {
                  const next = auditQuery.data?.next_cursor;
                  if (!next || auditQuery.isFetching) return;
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

      <AuditEventDrawer
        event={selected}
        actorName={selected ? actorOf(selected) : ""}
        onClose={() => setSelected(null)}
      />
    </div>
  );
}
