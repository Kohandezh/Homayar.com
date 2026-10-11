import { Label, Meter } from "@heroui/react";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import { quotaRows, type QuotaMetric, type QuotaRow } from "@/components/admin/overview/overview-model";
import { formatAudioSeconds, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useBillingUsage } from "@/lib/platform-api";

const METRIC_LABELS: Record<QuotaMetric, { fa: string; en: string }> = {
  audio_seconds: { fa: "پردازش صوت", en: "Audio processing" },
  concurrent_jobs: { fa: "پردازش‌های هم‌زمان", en: "Concurrent jobs" },
  seats: { fa: "صندلی‌ها", en: "Seats" },
};

function QuotaRowView({ row }: { row: QuotaRow }) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const label = METRIC_LABELS[row.metric][lang];
  const amount = (value: number) =>
    row.metric === "audio_seconds" ? formatAudioSeconds(value, lang) : formatNumber(value, lang);

  if (row.limit === null || row.percent === null) {
    return (
      <li className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-sm text-muted">
          {amount(row.consumed)} · <span>{isFa ? "نامحدود" : "Unlimited"}</span>
        </span>
      </li>
    );
  }

  // Our own value text: React Aria would format the percent with ASCII digits in Persian.
  const percentText = `${formatNumber(row.percent, lang)}${isFa ? "٪" : "%"}`;
  return (
    <li className="flex min-w-0 flex-col gap-1">
      <Meter
        value={row.percent}
        maxValue={100}
        size="sm"
        color={row.nearLimit ? "warning" : "accent"}
        valueLabel={percentText}
        className="w-full"
      >
        <Label>{label}</Label>
        <span className="text-sm text-muted">
          {amount(row.consumed)} / {amount(row.limit)}
        </span>
        <Meter.Track>
          <Meter.Fill />
        </Meter.Track>
      </Meter>
      {row.nearLimit && (
        <span className="text-sm font-medium text-warning">{isFa ? "نزدیک به سقف" : "Near limit"}</span>
      )}
    </li>
  );
}

export default function QuotaCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useBillingUsage();
  const state = queryCardState(query, (data) => quotaRows(data.items).length === 0);
  const rows = quotaRows(query.data?.items ?? []);

  return (
    <OverviewCard
      title={isFa ? "سهمیه" : "Quota"}
      state={state}
      error={query.error}
      emptyText={isFa ? "داده‌ای از مصرف نیست" : "No usage data"}
    >
      <ul className="m-0 flex list-none flex-col gap-4 p-0">
        {rows.map((row) => (
          <QuotaRowView key={row.metric} row={row} />
        ))}
      </ul>
    </OverviewCard>
  );
}
