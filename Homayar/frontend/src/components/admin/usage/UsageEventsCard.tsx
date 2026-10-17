import { useId, useMemo, useState } from "react";
import { Button, Label, ListBox, Select, Table } from "@heroui/react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import { BILLING_ERROR_CODES } from "@/components/admin/usage/billing-errors";
import {
  USAGE_METRICS,
  eventTypeLabel,
  formatMetricValue,
  metricLabel,
  subjectLabel,
} from "@/components/admin/usage/usage-model";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useUsageEvents } from "@/lib/platform-api";
import type { UsageEventListParams } from "@/types/platform";

const PAGE_SIZE = 25;
/** The selectedKey of the "no filter" option; no metric is named this. */
const ALL_METRICS = "all";

interface Cursor {
  before?: string;
  before_id?: string;
}

/** An id short enough to read, with the whole value in the title. */
function shortId(value: string): string {
  return value.length > 14 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;
}

/**
 * The workspace's usage ledger (SPEC U5): one row per reservation, commit,
 * release or adjustment. Paging is by keyset, so a page is asked for with the
 * cursor the server returned, never with an offset.
 */
export default function UsageEventsCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const filterId = useId();
  const [metric, setMetric] = useState<string>(ALL_METRICS);
  // One entry per page seen; the first page has no cursor.
  const [cursors, setCursors] = useState<Cursor[]>([{}]);
  const params = useMemo<UsageEventListParams>(
    () => ({
      limit: PAGE_SIZE,
      ...(metric === ALL_METRICS ? {} : { metric }),
      ...(cursors[cursors.length - 1] ?? {}),
    }),
    [cursors, metric],
  );
  const query = useUsageEvents(params);
  const state = queryCardState(query, (data) => data.items.length === 0);
  const page = query.data;
  const hasNext = Boolean(page?.next_before && page.next_before_id);
  const pageNumber = formatNumber(cursors.length, lang);
  // While any page loads the buttons stay mounted and focusable, only marked
  // disabled, so keyboard focus is never dropped to the page (AC3).
  const blocked = query.isFetching;
  const atFirstPage = cursors.length === 1;

  const nav = (
    <nav
      className="flex w-full flex-wrap items-center justify-between gap-3"
      aria-label={isFa ? "صفحه‌بندی رویدادهای مصرف" : "Usage event pagination"}
    >
      <Button
        variant="secondary"
        size="sm"
        aria-disabled={atFirstPage || blocked}
        onPress={() => {
          if (atFirstPage || blocked) return;
          setCursors((current) => current.slice(0, -1));
        }}
      >
        <ChevronLeft size={15} aria-hidden="true" className="rtl-mirror" />
        {isFa ? "صفحهٔ قبل" : "Previous page"}
      </Button>
      <span className="text-xs text-muted" aria-live="polite">
        {isFa ? `صفحهٔ ${pageNumber}` : `Page ${pageNumber}`}
      </span>
      <Button
        variant="secondary"
        size="sm"
        aria-disabled={!hasNext || blocked}
        onPress={() => {
          const before = page?.next_before;
          const beforeId = page?.next_before_id;
          if (!hasNext || blocked || !before || !beforeId) return;
          setCursors((current) => [...current, { before, before_id: beforeId }]);
        }}
      >
        {isFa ? "صفحهٔ بعد" : "Next page"}
        <ChevronRight size={15} aria-hidden="true" className="rtl-mirror" />
      </Button>
    </nav>
  );

  return (
    <OverviewCard
      title={isFa ? "رویدادهای مصرف" : "Usage events"}
      action={
        <Select
          className="w-full sm:w-56"
          selectedKey={metric}
          onSelectionChange={(key) => {
            if (key === null) return;
            setMetric(String(key));
            // A new filter is a new list, so paging starts from the first page.
            setCursors([{}]);
          }}
        >
          {/*
           * React Aria names the trigger "label + current value"; the SPEC
           * wants the button's accessible name to be the label alone, so the
           * label carries a stable id and the trigger points at it.
           */}
          <Label id={filterId}>{isFa ? "فیلتر سهمیه" : "Filter by metric"}</Label>
          <Select.Trigger aria-labelledby={filterId}>
            <Select.Value />
            <Select.Indicator />
          </Select.Trigger>
          <Select.Popover>
            <ListBox>
              <ListBox.Item id={ALL_METRICS} textValue={isFa ? "همه سهمیه‌ها" : "All metrics"}>
                {isFa ? "همه سهمیه‌ها" : "All metrics"}
              </ListBox.Item>
              {USAGE_METRICS.map((candidate) => (
                <ListBox.Item key={candidate} id={candidate} textValue={metricLabel(candidate, lang)}>
                  {metricLabel(candidate, lang)}
                </ListBox.Item>
              ))}
            </ListBox>
          </Select.Popover>
        </Select>
      }
      state={state}
      error={query.error}
      errorCodes={BILLING_ERROR_CODES}
      emptyText={
        metric === ALL_METRICS
          ? isFa
            ? "رویداد مصرفی ثبت نشده است"
            : "No usage events recorded"
          : isFa
            ? "رویدادی برای این سهمیه ثبت نشده است"
            : "No usage events for this metric"
      }
      // The nav never unmounts: a new cursor is a new query, so the card flips
      // to loading while the next page loads, and after an invalid cursor the
      // reader can still walk back (U5, AC3).
      persistentFooter={nav}
    >
      <Table className="min-w-0">
        <Table.ScrollContainer>
          <Table.Content
            aria-label={isFa ? "تاریخچهٔ رویدادهای مصرف" : "Usage event history"}
            className="min-w-[720px]"
          >
            <Table.Header>
              <Table.Column isRowHeader>{isFa ? "زمان" : "Time"}</Table.Column>
              <Table.Column>{isFa ? "سهمیه" : "Metric"}</Table.Column>
              <Table.Column>{isFa ? "رویداد" : "Event"}</Table.Column>
              <Table.Column>{isFa ? "مقدار" : "Amount"}</Table.Column>
              <Table.Column>{isFa ? "مرجع" : "Subject"}</Table.Column>
            </Table.Header>
            <Table.Body>
              {(page?.items ?? []).map((event) => (
                <Table.Row key={event.id} id={event.id}>
                  <Table.Cell>
                    <time dateTime={event.occurred_at}>{formatDate(event.occurred_at, lang)}</time>
                  </Table.Cell>
                  <Table.Cell>{metricLabel(event.metric, lang)}</Table.Cell>
                  <Table.Cell>{eventTypeLabel(event.event_type, lang)}</Table.Cell>
                  <Table.Cell>{formatMetricValue(event.metric, event.value, lang)}</Table.Cell>
                  <Table.Cell>
                    <span>{subjectLabel(event.subject_type, lang)}</span>{" "}
                    <code className="text-xs text-muted" title={event.subject_id} dir="ltr">
                      {shortId(event.subject_id)}
                    </code>
                  </Table.Cell>
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Content>
        </Table.ScrollContainer>
      </Table>
    </OverviewCard>
  );
}
