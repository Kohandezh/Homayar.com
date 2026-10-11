import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import { StatusBadge } from "@/components/platform/Ui";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useCurrentSubscription } from "@/lib/platform-api";

/** Shows what exists; empty only when both the plan and the subscription are missing. */
export default function PlanCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useCurrentSubscription();
  const state = queryCardState(query, (data) => !data.plan && !data.subscription);
  const plan = query.data?.plan ?? null;
  const subscription = query.data?.subscription ?? null;

  return (
    <OverviewCard
      title={isFa ? "طرح اشتراک" : "Plan"}
      state={state}
      error={query.error}
      emptyText={isFa ? "طرحی فعال نیست" : "No plan"}
    >
      {plan && <p className="m-0 mb-3 break-words text-xl font-semibold">{plan.name}</p>}
      {subscription && (
        <dl className="m-0 grid grid-cols-[auto_1fr] items-center gap-x-4 gap-y-2 text-sm">
          <dt className="text-muted">{isFa ? "وضعیت" : "Status"}</dt>
          <dd className="m-0">
            <StatusBadge status={subscription.status} />
          </dd>
          <dt className="text-muted">{isFa ? "پایان" : "Ends"}</dt>
          <dd className="m-0">
            {subscription.ends_at ? (
              <time dateTime={subscription.ends_at}>{formatDate(subscription.ends_at, lang)}</time>
            ) : (
              <span>{isFa ? "بدون تاریخ پایان" : "No end date"}</span>
            )}
          </dd>
        </dl>
      )}
    </OverviewCard>
  );
}
