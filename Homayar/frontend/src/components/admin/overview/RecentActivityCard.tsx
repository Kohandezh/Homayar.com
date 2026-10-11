import { Link } from "react-router-dom";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { overviewLinkClass, queryCardState } from "@/components/admin/overview/card-state";
import { auditEventLabel } from "@/lib/audit-labels";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useAuditEvents } from "@/lib/platform-api";

const RECENT_LIMIT = 10;
const RECENT_PARAMS = { limit: RECENT_LIMIT };

export default function RecentActivityCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useAuditEvents(RECENT_PARAMS);
  const state = queryCardState(query, (data) => data.items.length === 0);

  return (
    <OverviewCard
      title={isFa ? "رویدادهای اخیر" : "Recent activity"}
      state={state}
      error={query.error}
      emptyText={isFa ? "هنوز رویدادی ثبت نشده" : "No activity yet"}
      footer={
        <Link to="/admin/audit" className={overviewLinkClass}>
          {isFa ? "مشاهدهٔ گزارش رویدادها" : "View audit log"}
        </Link>
      }
    >
      <ul className="m-0 flex list-none flex-col gap-3 p-0">
        {query.data?.items.slice(0, RECENT_LIMIT).map((event) => (
          <li key={event.id} className="flex min-w-0 flex-col gap-0.5">
            {/* Unknown types are shown as sent, never guessed. */}
            <span className="break-words text-sm">{auditEventLabel(event.event_type, lang) ?? event.event_type}</span>
            <time dateTime={event.occurred_at} className="text-xs text-muted">
              {formatDate(event.occurred_at, lang)}
            </time>
          </li>
        ))}
      </ul>
    </OverviewCard>
  );
}
