import { Button, Chip } from "@heroui/react";
import { PhoneCall } from "lucide-react";

import { computedValue } from "@/components/trends/trend-format";
import { TRENDS_COPY, trendReasonLabel } from "@/components/trends/trends-copy";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { TrendCell } from "@/types/trends";

/**
 * One number of the trend, by its status. Only a `computed` cell shows a
 * numerator, a percent and the way to its calls; every other status shows
 * why there is no number, and the denominator wherever the API gives one.
 */
export default function TrendCellView({
  cell,
  pendingReview,
  showCallsLabel,
  onShowCalls,
}: {
  cell: TrendCell;
  pendingReview: number | null;
  /** The accessible name of the drill-down button. */
  showCallsLabel: string;
  onShowCalls: () => void;
}) {
  const { lang } = useI18n();
  const copy = TRENDS_COPY[lang];
  const reason = trendReasonLabel(cell.reason, lang);
  const computed = cell.status === "computed" && cell.numerator !== null && cell.ratio !== null;

  return (
    <span className="grid gap-1">
      {computed ? (
        <>
          <span>{computedValue(cell.numerator ?? 0, cell.denominator, cell.ratio ?? 0, lang)}</span>
          <Button
            variant="ghost"
            size="sm"
            className="justify-self-start"
            aria-label={showCallsLabel}
            onPress={onShowCalls}
          >
            <PhoneCall size={14} aria-hidden="true" />
            {copy.callsButton}
          </Button>
        </>
      ) : cell.status === "insufficient_sample" ? (
        <>
          <Chip color="warning" variant="soft" size="sm" className="justify-self-start">
            <Chip.Label>{copy.tooFewCalls}</Chip.Label>
          </Chip>
          <span className="text-xs text-muted">
            {copy.sampleNeeded(formatNumber(cell.denominator, lang), formatNumber(cell.minimum_sample, lang))}
          </span>
        </>
      ) : cell.status === "withheld" ? (
        <>
          <span className="text-xs">{copy.withheld}</span>
          <span className="text-xs text-muted">{copy.calls(formatNumber(cell.denominator, lang))}</span>
        </>
      ) : (
        <>
          <span className="text-muted">{copy.notAvailable}</span>
          {reason && (
            <span className="text-xs text-muted">
              <bdi>{reason}</bdi>
            </span>
          )}
        </>
      )}
      {pendingReview !== null && pendingReview > 0 && (
        <span className="text-xs text-muted">{copy.waitingForReview(formatNumber(pendingReview, lang))}</span>
      )}
    </span>
  );
}
