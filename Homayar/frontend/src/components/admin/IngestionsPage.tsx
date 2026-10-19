import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { Button, Table } from "@heroui/react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { getLocalTimeZone } from "@internationalized/date";

import IngestionDrawer from "@/components/admin/ingestions/IngestionDrawer";
import IngestionFilterBar from "@/components/admin/ingestions/IngestionFilterBar";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/platform/Ui";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import {
  hasIngestionFilters,
  ingestionQueryParams,
  isFromAfterTo,
  nextBlockedByFrom,
  readIngestionFilters,
  visibleIngestionRows,
  writeIngestionFilters,
  type IngestionFilters,
} from "@/lib/ingestion-filters";
import {
  ingestionReasonLabel,
  ingestionSourceLabel,
  ingestionStatusLabel,
} from "@/lib/ingestion-labels";
import { useIngestions } from "@/lib/ingestions-api";

interface Paging {
  filterKey: string;
  /** One entry per page seen; the first page has no cursor. */
  cursors: Array<string | undefined>;
}

export default function IngestionsPage() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = useMemo(() => readIngestionFilters(searchParams), [searchParams]);
  // The chosen days count in the reader's own time zone.
  const timeZone = useMemo(() => getLocalTimeZone(), []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The cursors of the pages walked so far, kept next to the filters they
  // belong to: other filters describe a different list, where an old cursor
  // means nothing, so paging starts from the first page again.
  const filterKey = useMemo(() => writeIngestionFilters(filters).toString(), [filters]);
  const [paging, setPaging] = useState<Paging>({ filterKey, cursors: [undefined] });
  const cursors = paging.filterKey === filterKey ? paging.cursors : [undefined];
  const cursor = cursors[cursors.length - 1];

  function goTo(next: Array<string | undefined>) {
    setPaging({ filterKey, cursors: next });
  }

  // A start day after the end day can describe no range, so nothing is asked.
  const rangeInvalid = isFromAfterTo(filters);
  const params = useMemo(
    () => ingestionQueryParams(filters, { before: cursor, timeZone }),
    [filters, cursor, timeZone],
  );
  const listQuery = useIngestions(params, !rangeInvalid);
  const items = useMemo(() => listQuery.data?.items ?? [], [listQuery.data]);
  // The server has no status or "after" filter, so those rows are hidden here.
  const rows = useMemo(() => visibleIngestionRows(items, filters, timeZone), [items, filters, timeZone]);
  const pageNumber = formatNumber(cursors.length, lang);
  const nextBefore = listQuery.data?.next_before ?? null;
  // Every page after this one is older still, so Next would only load rows
  // the from-day filter hides again.
  const canNext = nextBefore !== null && !nextBlockedByFrom(items, filters, timeZone);
  const noMatchLabel = isFa
    ? "در این صفحه ورودی‌ای با این فیلترها نیست."
    : "No ingestions on this page match the filters.";
  // A later page the server answered empty means the list ended, not that
  // nothing was ever ingested or that the filters hide everything.
  const emptyLabel =
    cursors.length === 1 && !hasIngestionFilters(filters)
      ? (isFa ? "هنوز بارگذاری یا ورودی‌ای ثبت نشده است." : "No uploads or imports yet.")
      : hasIngestionFilters(filters)
        ? noMatchLabel
        : (isFa ? "ورودی دیگری نیست." : "No more ingestions.");

  function applyFilters(next: IngestionFilters) {
    setSearchParams(writeIngestionFilters(next, searchParams));
  }

  // The pager stays mounted across page loads, so keyboard focus on its
  // buttons survives a loading page; an empty later page still offers the
  // walk back to the pages before it.
  const pager = (
    <nav
      className="mt-3 flex items-center justify-between gap-3"
      aria-label={isFa ? "صفحه‌بندی ورودی‌ها" : "Ingestions pagination"}
    >
      <Button
        variant="secondary"
        size="sm"
        // Still focusable while a page loads, so focus is never dropped to the page.
        aria-disabled={cursors.length === 1 || listQuery.isFetching}
        onPress={() => {
          if (cursors.length === 1 || listQuery.isFetching) return;
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
        aria-disabled={!canNext || listQuery.isFetching}
        onPress={() => {
          if (!canNext || listQuery.isFetching) return;
          goTo([...cursors, nextBefore]);
        }}
      >
        {isFa ? "بعدی" : "Next"}
        <ChevronRight size={15} aria-hidden="true" className="rtl-mirror" />
      </Button>
    </nav>
  );

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={isFa ? "مدیریت" : "Administration"}
        title={isFa ? "ورودی‌ها" : "Ingestions"}
      />
      <p className="mt-0 mb-4 text-sm text-muted">
        {isFa
          ? "همهٔ بارگذاری‌ها و ورودی‌ها، از تازه به قدیم: پذیرفته شد یا نه، و چرا فایلی رد شد یا تکراری شناخته شد."
          : "Every upload and import, newest first: whether it was accepted, and why a file was rejected or marked as a duplicate."}
      </p>

      <IngestionFilterBar filters={filters} onChange={applyFilters} />

      {rangeInvalid && (
        <p className="mt-4 mb-0 text-sm" role="alert">
          {isFa ? "تاریخ شروع بعد از تاریخ پایان است." : "The start date is after the end date."}
        </p>
      )}

      <div className="mt-4">
        {rangeInvalid ? null : listQuery.isPending ? (
          <LoadingState label={isFa ? "در حال دریافت ورودی‌ها" : "Loading ingestions"} />
        ) : listQuery.isError ? (
          <div className="grid gap-3">
            <ErrorState
              error={listQuery.error}
              fallback={isFa ? "دریافت فهرست ورودی‌ها ناموفق بود." : "Could not load the ingestion list."}
            />
            <Button
              variant="secondary"
              size="sm"
              className="justify-self-start"
              onPress={() => void listQuery.refetch()}
            >
              {isFa ? "تلاش دوباره" : "Try again"}
            </Button>
          </div>
        ) : items.length === 0 ? (
          <>
            <EmptyState label={emptyLabel} />
            {cursors.length > 1 && pager}
          </>
        ) : (
          <>
            {rows.length === 0 ? (
              // The pager stays, so the reader can still walk to other pages.
              <EmptyState label={noMatchLabel} />
            ) : (
              <Table aria-busy={listQuery.isFetching}>
                <Table.ScrollContainer>
                  <Table.Content aria-label={isFa ? "ورودی‌ها" : "Ingestions"}>
                    <Table.Header>
                      <Table.Column isRowHeader>{isFa ? "فایل" : "File"}</Table.Column>
                      <Table.Column>{isFa ? "منبع" : "Source"}</Table.Column>
                      <Table.Column>{isFa ? "وضعیت" : "State"}</Table.Column>
                      <Table.Column>{isFa ? "دلیل" : "Reason"}</Table.Column>
                      <Table.Column>{isFa ? "دریافت" : "Received"}</Table.Column>
                      {/* Holds each row's Details button; no heading of its own. */}
                      <Table.Column aria-label={isFa ? "عملیات" : "Actions"}>{""}</Table.Column>
                    </Table.Header>
                    <Table.Body>
                      {rows.map((item) => (
                        <Table.Row key={item.id} id={item.id}>
                          <Table.Cell>
                            {/* User content: no forced direction, the browser
                                reads the name's own order. */}
                            {item.original_filename ? (
                              <bdi>{item.original_filename}</bdi>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </Table.Cell>
                          <Table.Cell>
                            {/* An unknown source is shown as sent, never guessed. */}
                            <bdi>{ingestionSourceLabel(item.source_type, lang)}</bdi>
                          </Table.Cell>
                          <Table.Cell>
                            <span className="grid gap-0.5">
                              <span>{ingestionStatusLabel(item.status, lang)}</span>
                              {item.duplicate_of_id && (
                                <span className="text-xs text-muted">
                                  {isFa ? "تکراری" : "Duplicate"}
                                </span>
                              )}
                            </span>
                          </Table.Cell>
                          <Table.Cell>
                            {item.reason ? (
                              ingestionReasonLabel(item.reason, lang)
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </Table.Cell>
                          <Table.Cell>
                            <time dateTime={item.received_at}>
                              {formatDate(item.received_at, lang)}
                            </time>
                          </Table.Cell>
                          <Table.Cell>
                            <Button
                              variant="secondary"
                              size="sm"
                              onPress={() => setSelectedId(item.id)}
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
            )}

            {pager}
          </>
        )}
      </div>

      <IngestionDrawer
        ingestionId={selectedId}
        onClose={() => setSelectedId(null)}
        onOpenId={setSelectedId}
      />
    </div>
  );
}
