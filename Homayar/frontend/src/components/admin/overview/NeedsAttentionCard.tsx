import { Link } from "react-router-dom";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { overviewLinkClass, queryCardState } from "@/components/admin/overview/card-state";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useMeetings } from "@/lib/platform-api";

const FAILED_PARAMS = { status: "failed", limit: 5 } as const;

export default function NeedsAttentionCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useMeetings(FAILED_PARAMS);
  const state = queryCardState(query, (data) => data.items.length === 0);

  return (
    <OverviewCard
      title={isFa ? "نیازمند بررسی" : "Needs attention"}
      state={state}
      error={query.error}
      emptyText={isFa ? "موردی برای بررسی نیست" : "Nothing needs attention"}
      footer={
        <Link to="/admin/processing" className={overviewLinkClass}>
          {isFa ? "مشاهدهٔ پردازش" : "View processing"}
        </Link>
      }
    >
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {query.data?.items.map((meeting) => (
          <li key={meeting.id} className="flex min-w-0 flex-col gap-0.5">
            <Link to={`/meetings/${meeting.id}`} className={`${overviewLinkClass} min-w-0 break-words`}>
              {meeting.title}
            </Link>
            <time dateTime={meeting.created_at} className="text-xs text-muted">
              {formatDate(meeting.created_at, lang)}
            </time>
          </li>
        ))}
      </ul>
    </OverviewCard>
  );
}
