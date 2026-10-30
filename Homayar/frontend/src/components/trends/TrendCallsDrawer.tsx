import { useState } from "react";
import { Link } from "react-router";
import { Alert, Button, Drawer } from "@heroui/react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { EmptyState, LoadingState } from "@/components/platform/Ui";
import type { TrendDrillTarget } from "@/components/trends/TrendTable";
import { ofTotal } from "@/components/trends/trend-format";
import { TRENDS_COPY } from "@/components/trends/trends-copy";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useTrendConversations } from "@/lib/trends-api";
import type { IssueTrendParams, TrendLinkParams } from "@/types/trends";

/** Conversations per page; the API allows 1 to 100. */
export const TREND_CALLS_PAGE_SIZE = 25;

/**
 * The calls one computed cell was counted from (HOM-83). Every link comes
 * from the API's `conversations`, so the caller sees only the calls their
 * record scope allows; a refusal shows a message, never an empty list.
 */
function CallsList({ target, trendParams }: { target: TrendDrillTarget; trendParams: IssueTrendParams }) {
  const { lang } = useI18n();
  const copy = TRENDS_COPY[lang];
  const [offset, setOffset] = useState(0);
  const params: TrendLinkParams = {
    ...trendParams,
    trend_view: "issues",
    row: target.row,
    ...(target.point === undefined ? {} : { point: target.point }),
    offset,
    limit: TREND_CALLS_PAGE_SIZE,
  };
  const query = useTrendConversations(params);
  const cell = query.data?.cell ?? target.cell;
  const conversations = query.data?.conversations ?? [];
  // Numbers and paging follow the page the API answered, not the one asked for.
  const page = query.data?.page ?? { offset, limit: TREND_CALLS_PAGE_SIZE, total: 0 };
  const hasPrevious = page.offset > 0;
  const hasNext = page.total > page.offset + page.limit;

  return (
    <>
      <Drawer.Header>
        <Drawer.Heading>{copy.supportingCalls}</Drawer.Heading>
        <p className="m-0 text-sm text-muted">
          <bdi>{target.rowName}</bdi>, {target.period}
        </p>
      </Drawer.Header>
      <Drawer.Body className="grid content-start gap-3">
        {cell.numerator !== null && <p className="m-0">{ofTotal(cell.numerator, cell.denominator, lang)}</p>}
        <p className="m-0 text-sm text-muted">{copy.correlationNotCause}</p>
        {query.isPending ? (
          <LoadingState label={copy.loadingCalls} />
        ) : query.isError ? (
          <Alert status="warning" role="alert">
            <Alert.Indicator aria-hidden="true" />
            <Alert.Content>
              <Alert.Description>{copy.callsUnavailable}</Alert.Description>
            </Alert.Content>
          </Alert>
        ) : conversations.length === 0 ? (
          <EmptyState label={copy.noCalls} />
        ) : (
          <>
            <ol className="m-0 grid list-none gap-2 p-0">
              {conversations.map((conversation, index) => (
                <li key={`${conversation.meeting_id}-${index}`} className="flex items-baseline justify-between gap-3">
                  <Link to={`/meetings/${conversation.meeting_id}`}>
                    {copy.openCall(formatNumber(page.offset + index + 1, lang))}
                  </Link>
                  <span className="text-xs text-muted">
                    {copy.evidence(formatNumber(conversation.evidence_ids.length, lang))}
                  </span>
                </li>
              ))}
            </ol>
            {(hasPrevious || hasNext) && (
              <nav className="flex items-center justify-between gap-3" aria-label={copy.callsPaging}>
                {hasPrevious ? (
                  <Button
                    variant="secondary"
                    size="sm"
                    onPress={() => setOffset(Math.max(0, page.offset - page.limit))}
                  >
                    <ChevronLeft size={15} aria-hidden="true" className="rtl-mirror" />
                    {copy.previous}
                  </Button>
                ) : (
                  <span />
                )}
                {hasNext && (
                  <Button variant="secondary" size="sm" onPress={() => setOffset(page.offset + page.limit)}>
                    {copy.next}
                    <ChevronRight size={15} aria-hidden="true" className="rtl-mirror" />
                  </Button>
                )}
              </nav>
            )}
          </>
        )}
      </Drawer.Body>
    </>
  );
}

export default function TrendCallsDrawer({
  target,
  trendParams,
  onClose,
}: {
  target: TrendDrillTarget | null;
  trendParams: IssueTrendParams;
  onClose: () => void;
}) {
  const { lang, dir } = useI18n();
  const copy = TRENDS_COPY[lang];
  // The last cell and its query stay while the drawer animates closed, so the
  // list does not blank and a filter change asks nothing for the old cell.
  const [shown, setShown] = useState<{ target: TrendDrillTarget; trendParams: IssueTrendParams } | null>(null);
  if (target && (shown?.target !== target || shown.trendParams !== trendParams)) {
    setShown({ target, trendParams });
  }

  return (
    <Drawer.Backdrop
      isOpen={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
        <Drawer.Dialog aria-label={copy.supportingCalls} className="w-[28rem] max-w-[90vw] p-4">
          <Drawer.CloseTrigger aria-label={copy.closeCalls} />
          {/* A new cell starts on its first page. */}
          {shown && (
            <CallsList
              key={`${shown.target.row}|${shown.target.point ?? "window"}`}
              target={shown.target}
              trendParams={shown.trendParams}
            />
          )}
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  );
}
