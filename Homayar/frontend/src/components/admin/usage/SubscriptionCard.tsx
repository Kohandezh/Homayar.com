import { StatusBadge } from "@/components/platform/Ui";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import { BILLING_ERROR_CODES } from "@/components/admin/usage/billing-errors";
import { formatDateOnly } from "@/components/admin/usage/usage-model";
import { useI18n } from "@/lib/i18n-context";
import { useCurrentSubscription } from "@/lib/platform-api";

/**
 * The plan the workspace is on and the dates of its subscription (SPEC U3).
 * Billing is manual and no price is published, so the card states the billing
 * mode instead of offering a plan change.
 */
export default function SubscriptionCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useCurrentSubscription();
  // U3: only a null subscription is "no active subscription"; a plan object
  // alone says nothing about a subscription.
  const state = queryCardState(query, (data) => data.subscription === null);
  const plan = query.data?.plan ?? null;
  const subscription = query.data?.subscription ?? null;

  return (
    <OverviewCard
      title={isFa ? "طرح فعلی" : "Current plan"}
      state={state}
      error={query.error}
      errorCodes={BILLING_ERROR_CODES}
      emptyText={isFa ? "اشتراک فعالی ثبت نشده است" : "No active subscription"}
    >
      <p className="m-0 break-words text-xl font-semibold">{plan?.name ?? subscription?.plan_key}</p>
      {subscription && (
        <dl className="m-0 mt-4 grid grid-cols-1 gap-x-6 gap-y-3 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-muted">{isFa ? "وضعیت" : "Status"}</dt>
            <dd className="m-0 mt-1">
              <StatusBadge status={subscription.status} />
            </dd>
          </div>
          <div>
            <dt className="text-muted">{isFa ? "شروع" : "Started"}</dt>
            <dd className="m-0 mt-1 font-medium">
              <time dateTime={subscription.starts_at}>{formatDateOnly(subscription.starts_at, lang)}</time>
            </dd>
          </div>
          <div>
            <dt className="text-muted">{isFa ? "پایان" : "Ends"}</dt>
            <dd className="m-0 mt-1 font-medium">
              {subscription.ends_at ? (
                <time dateTime={subscription.ends_at}>{formatDateOnly(subscription.ends_at, lang)}</time>
              ) : (
                <span>{isFa ? "بدون تاریخ پایان" : "No end date"}</span>
              )}
            </dd>
          </div>
          <div>
            <dt className="text-muted">{isFa ? "صورتحساب" : "Billing"}</dt>
            <dd className="m-0 mt-1 font-medium">{isFa ? "دستی" : "Manual"}</dd>
          </div>
        </dl>
      )}
    </OverviewCard>
  );
}
