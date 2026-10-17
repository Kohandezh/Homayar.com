import { Meter } from "@heroui/react";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import { BILLING_ERROR_CODES } from "@/components/admin/usage/billing-errors";
import {
  formatDateOnly,
  formatMetricValue,
  metricLabel,
  usageRows,
  type QuotaLevel,
  type UsageRow,
} from "@/components/admin/usage/usage-model";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useBillingUsage } from "@/lib/platform-api";

const METER_COLORS: Record<QuotaLevel, "accent" | "warning" | "danger"> = {
  normal: "accent",
  warning: "warning",
  danger: "danger",
};

const LEVEL_TEXT: Record<Exclude<QuotaLevel, "normal">, { fa: string; en: string }> = {
  warning: { fa: "نزدیک به سقف", en: "Near limit" },
  danger: { fa: "به سقف رسیده", en: "Limit reached" },
};

/** The label and value pairs under one quota (SPEC U4). */
function Amounts({ row, lang }: { row: UsageRow; lang: "fa" | "en" }) {
  const isFa = lang === "fa";
  const amount = (value: number) => formatMetricValue(row.metric, value, lang);
  return (
    <dl className="m-0 mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-xs sm:grid-cols-4">
      <div>
        <dt className="text-muted">{isFa ? "مصرف‌شده" : "Used"}</dt>
        <dd className="m-0 mt-0.5 font-medium">{amount(row.used)}</dd>
      </div>
      <div>
        <dt className="text-muted">{isFa ? "رزروشده" : "Reserved"}</dt>
        <dd className="m-0 mt-0.5 font-medium">{amount(row.reserved)}</dd>
      </div>
      <div>
        <dt className="text-muted">{isFa ? "باقی‌مانده" : "Remaining"}</dt>
        <dd className="m-0 mt-0.5 font-medium">
          {row.remaining === null ? (isFa ? "نامحدود" : "Unlimited") : amount(row.remaining)}
        </dd>
      </div>
      <div>
        <dt className="text-muted">{isFa ? "دوره" : "Period"}</dt>
        <dd className="m-0 mt-0.5 font-medium">
          <time dateTime={row.periodStart}>{formatDateOnly(row.periodStart, lang)}</time>
          {row.periodEnd && (
            <>
              {" – "}
              <time dateTime={row.periodEnd}>{formatDateOnly(row.periodEnd, lang)}</time>
            </>
          )}
        </dd>
      </div>
    </dl>
  );
}

/** One quota: the meter (or the unlimited text) and the amounts. */
function QuotaItem({ row, lang }: { row: UsageRow; lang: "fa" | "en" }) {
  const isFa = lang === "fa";
  const label = metricLabel(row.metric, lang);
  const amount = (value: number) => formatMetricValue(row.metric, value, lang);

  if (row.limit === null) {
    return (
      <li className="flex min-w-0 flex-col gap-1" data-level="normal">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <span className="text-sm font-medium">{label}</span>
          <span className="text-sm text-muted">{isFa ? "نامحدود" : "Unlimited"}</span>
        </div>
        <Amounts row={row} lang={lang} />
      </li>
    );
  }

  // Our own percent text: React Aria would format it with ASCII digits in Persian.
  const percentText = localePercent(row.percent, lang);
  return (
    <li className="flex min-w-0 flex-col gap-1" data-level={row.level}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="text-sm font-medium">{label}</span>
        {row.level !== "normal" && (
          <span className={`text-sm font-medium ${row.level === "danger" ? "text-danger" : "text-warning"}`}>
            {LEVEL_TEXT[row.level][lang]}
          </span>
        )}
      </div>
      {/*
       * One visible metric name: the span above, with the meter named through
       * aria-label so its accessible name is still exactly the label.
       */}
      <Meter
        value={row.percent ?? 0}
        maxValue={100}
        color={METER_COLORS[row.level]}
        valueLabel={percentText}
        aria-label={label}
        className="w-full"
      >
        <Meter.Output />
        <Meter.Track>
          <Meter.Fill />
        </Meter.Track>
      </Meter>
      <p className="m-0 text-xs text-muted">
        {amount(row.consumed)}
        {isFa ? " از " : " of "}
        {amount(row.limit)}
      </p>
      <Amounts row={row} lang={lang} />
    </li>
  );
}

/** The locale percent text, e.g. "40%" / "۴۰٪". */
function localePercent(percent: number | null, lang: "fa" | "en"): string {
  return `${formatNumber(percent ?? 0, lang)}${lang === "fa" ? "٪" : "%"}`;
}

/**
 * One meter per quota (SPEC U4). The meter measures used plus reserved
 * against the limit, because reserved time is already spoken for.
 */
export default function QuotaMetersCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useBillingUsage();
  const state = queryCardState(query, (data) => data.items.length === 0);
  const rows = usageRows(query.data?.items ?? []);

  return (
    <OverviewCard
      title={isFa ? "سهمیه‌ها" : "Quota usage"}
      state={state}
      error={query.error}
      errorCodes={BILLING_ERROR_CODES}
      emptyText={isFa ? "سهمیه‌ای برای این فضا ثبت نشده است" : "No quotas configured for this workspace"}
    >
      <ul className="m-0 flex list-none flex-col gap-5 p-0">
        {rows.map((row) => (
          <QuotaItem key={row.metric} row={row} lang={lang} />
        ))}
      </ul>
    </OverviewCard>
  );
}
