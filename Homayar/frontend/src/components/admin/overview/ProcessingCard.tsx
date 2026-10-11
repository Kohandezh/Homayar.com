import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import { countMeetings } from "@/components/admin/overview/overview-model";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMeetings } from "@/lib/platform-api";

/** The latest 100 meetings; counted on the client (the list filter takes one status). */
const SAMPLE_PARAMS = { limit: 100 };

export default function ProcessingCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useMeetings(SAMPLE_PARAMS);
  const state = queryCardState(query, (data) => data.items.length === 0);
  const counts = countMeetings(query.data?.items ?? []);
  const pairs = [
    { label: isFa ? "تکمیل‌شده" : "Completed", value: counts.completed },
    { label: isFa ? "در حال پردازش" : "In progress", value: counts.inProgress },
    { label: isFa ? "ناموفق" : "Failed", value: counts.failed },
    { label: isFa ? "ناقص" : "Partial", value: counts.partial },
  ];
  const sample = query.data?.next_before
    ? isFa ? "۱۰۰ جلسهٔ اخیر" : "Last 100 meetings"
    : isFa ? "همهٔ جلسه‌ها" : "All meetings";

  return (
    <OverviewCard
      title={isFa ? "پردازش" : "Processing"}
      state={state}
      error={query.error}
      emptyText={isFa ? "هنوز جلسه‌ای نیست" : "No meetings yet"}
    >
      <p className="m-0 mb-3 text-sm text-muted">{sample}</p>
      <dl className="m-0 grid grid-cols-2 gap-x-4 gap-y-3">
        {pairs.map((pair) => (
          <div key={pair.label} className="min-w-0">
            <dt className="text-sm text-muted">{pair.label}</dt>
            <dd className="m-0 text-2xl font-semibold tabular-nums">{formatNumber(pair.value, lang)}</dd>
          </div>
        ))}
      </dl>
    </OverviewCard>
  );
}
