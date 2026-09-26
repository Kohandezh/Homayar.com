import { useMemo, useState } from "react";
import {
  BarChart3,
  ChevronLeft,
  ChevronRight,
  CreditCard,
  Gauge,
  ReceiptText,
} from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { formatAudioSeconds } from "@/lib/format";
import { roleCan } from "@/lib/roles";
import AllowanceNotice from "@/components/platform/AllowanceNotice";
import {
  useBillingUsage,
  useCurrentSubscription,
  useUsageEvents,
} from "@/lib/platform-api";
import type { BillingUsageSnapshot, UsageEventListParams } from "@/types/platform";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";

interface UsageCursor {
  before?: string;
  before_id?: string;
}

function formatDate(value: string, locale: "fa" | "en"): string {
  return new Intl.DateTimeFormat(locale === "fa" ? "fa-IR" : "en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function metricLabel(metric: string, isFa: boolean): string {
  const labels: Record<string, [string, string]> = {
    audio_seconds: ["Audio processing", "پردازش صوت"],
    concurrent_jobs: ["Concurrent processing", "پردازش هم‌زمان"],
    seats: ["Workspace seats", "ظرفیت اعضا"],
  };
  const label = labels[metric];
  if (label) return isFa ? label[1] : label[0];
  return metric.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatMetricValue(metric: string, value: number, locale: string): string {
  if (metric === "audio_seconds") return formatAudioSeconds(value, locale);
  return new Intl.NumberFormat(locale === "fa" ? "fa-IR" : "en").format(value);
}

function QuotaRow({ item, lang }: { item: BillingUsageSnapshot; lang: "fa" | "en" }) {
  const isFa = lang === "fa";
  const consumed = item.used + item.reserved;
  const percent = item.limit === null || item.limit <= 0
    ? null
    : Math.min(100, Math.round((consumed / item.limit) * 100));

  return (
    <li className="platform-quota-row grid gap-3 border-b border-border-subtle px-4 py-4 last:border-b-0 sm:px-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="m-0 text-sm font-semibold text-text-primary">
            {metricLabel(item.metric, isFa)}
          </h3>
          <p className="mt-1 mb-0 text-xs text-text-secondary">
            {isFa ? "منبع سهمیه:" : "Quota source:"} {item.source.replaceAll("_", " ")}
          </p>
        </div>
        <strong className="text-sm text-text-primary">
          {item.limit === null
            ? (isFa ? "نامحدود" : "Unlimited")
            : `${formatMetricValue(item.metric, consumed, lang)} / ${formatMetricValue(item.metric, item.limit, lang)}`}
        </strong>
      </div>

      {item.limit !== null && item.limit > 0 && (
        <progress
          className="h-2 w-full overflow-hidden rounded-full accent-accent"
          max={item.limit}
          value={Math.min(consumed, item.limit)}
          aria-label={isFa ? `مصرف ${metricLabel(item.metric, true)}` : `${metricLabel(item.metric, false)} usage`}
        >
          {percent}%
        </progress>
      )}

      <dl className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
        <div>
          <dt className="text-text-muted">{isFa ? "مصرف‌شده" : "Used"}</dt>
          <dd className="mt-1 mb-0 font-semibold text-text-primary">
            {formatMetricValue(item.metric, item.used, lang)}
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">{isFa ? "رزروشده" : "Reserved"}</dt>
          <dd className="mt-1 mb-0 font-semibold text-text-primary">
            {formatMetricValue(item.metric, item.reserved, lang)}
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">{isFa ? "باقی‌مانده" : "Remaining"}</dt>
          <dd className="mt-1 mb-0 font-semibold text-text-primary">
            {item.remaining === null
              ? (isFa ? "نامحدود" : "Unlimited")
              : formatMetricValue(item.metric, item.remaining, lang)}
          </dd>
        </div>
        <div>
          <dt className="text-text-muted">{isFa ? "دوره" : "Period"}</dt>
          <dd className="mt-1 mb-0 font-semibold text-text-primary">
            {formatDate(item.period_start, lang)}
          </dd>
        </div>
      </dl>
    </li>
  );
}

function UsageEvents({ metrics }: { metrics: string[] }) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [metric, setMetric] = useState("");
  const [cursorStack, setCursorStack] = useState<UsageCursor[]>([{}]);
  const params = useMemo<UsageEventListParams>(
    () => ({
      limit: 25,
      metric: metric || undefined,
      ...(cursorStack[cursorStack.length - 1] ?? {}),
    }),
    [cursorStack, metric],
  );
  const eventsQuery = useUsageEvents(params);

  return (
    <section className="platform-usage-events platform-surface overflow-hidden" aria-labelledby="usage-events-heading">
      <div className="flex flex-col gap-3 border-b border-border-subtle px-4 py-4 sm:flex-row sm:items-end sm:justify-between sm:px-5">
        <div className="flex items-center gap-3">
          <ReceiptText aria-hidden="true" className="text-accent" size={20} />
          <div>
            <h2 id="usage-events-heading" className="m-0 text-sm font-semibold text-text-primary">
              {isFa ? "رویدادهای مصرف" : "Usage events"}
            </h2>
            <p className="mt-1 mb-0 text-xs text-text-secondary">
              {isFa ? "ثبت واقعی رزرو و مصرف سهمیه" : "Recorded quota reservations and consumption"}
            </p>
          </div>
        </div>
        <div className="platform-field w-full sm:w-52">
          <label htmlFor="usage-metric-filter">{isFa ? "فیلتر سهمیه" : "Metric filter"}</label>
          <select
            id="usage-metric-filter"
            className="platform-select"
            value={metric}
            onChange={(event) => {
              setMetric(event.currentTarget.value);
              setCursorStack([{}]);
            }}
          >
            <option value="">{isFa ? "همه سهمیه‌ها" : "All metrics"}</option>
            {metrics.map((candidate) => (
              <option key={candidate} value={candidate}>
                {metricLabel(candidate, isFa)}
              </option>
            ))}
          </select>
        </div>
      </div>

      {eventsQuery.isLoading ? (
        <LoadingState label={isFa ? "در حال دریافت رویدادها" : "Loading usage events"} />
      ) : eventsQuery.isError ? (
        <div className="p-4">
          <ErrorState
            error={eventsQuery.error}
            fallback={isFa ? "دریافت رویدادهای مصرف ناموفق بود" : "Could not load usage events"}
          />
        </div>
      ) : eventsQuery.data?.items.length ? (
        <>
          <div className="overflow-x-auto">
            <table className="platform-usage-table w-full min-w-[760px] border-collapse text-sm">
              <caption className="sr-only">
                {isFa ? "تاریخچه رویدادهای مصرف فضای کاری" : "Workspace usage event history"}
              </caption>
              <thead>
                <tr className="border-b border-border-subtle bg-bg-primary text-xs text-text-secondary">
                  <th scope="col" className="px-4 py-3 text-start font-semibold sm:px-5">{isFa ? "زمان" : "Time"}</th>
                  <th scope="col" className="px-4 py-3 text-start font-semibold">{isFa ? "سهمیه" : "Metric"}</th>
                  <th scope="col" className="px-4 py-3 text-start font-semibold">{isFa ? "رویداد" : "Event"}</th>
                  <th scope="col" className="px-4 py-3 text-end font-semibold">{isFa ? "مقدار" : "Value"}</th>
                  <th scope="col" className="px-4 py-3 text-start font-semibold sm:px-5">{isFa ? "مرجع" : "Subject"}</th>
                </tr>
              </thead>
              <tbody>
                {eventsQuery.data.items.map((event) => (
                  <tr key={event.id} className="border-b border-border-subtle last:border-b-0">
                    <td className="px-4 py-3 text-xs text-text-secondary sm:px-5">{formatDate(event.occurred_at, lang)}</td>
                    <td className="px-4 py-3 font-medium text-text-primary">{metricLabel(event.metric, isFa)}</td>
                    <td className="px-4 py-3 text-text-secondary">{event.event_type.replaceAll("_", " ")}</td>
                    <td className="px-4 py-3 text-end font-mono font-semibold text-text-primary" dir="ltr">
                      {formatMetricValue(event.metric, event.value, lang)}
                    </td>
                    <td className="px-4 py-3 text-xs text-text-secondary sm:px-5">
                      <span>{event.subject_type.replaceAll("_", " ")}</span>
                      <code className="ms-2 text-[0.68rem] text-text-muted" title={event.subject_id} dir="ltr">
                        {event.subject_id.length > 14 ? `${event.subject_id.slice(0, 8)}…${event.subject_id.slice(-4)}` : event.subject_id}
                      </code>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <nav className="flex items-center justify-between gap-3 border-t border-border-subtle px-4 py-3 sm:px-5" aria-label={isFa ? "صفحه‌بندی رویدادهای مصرف" : "Usage event pagination"}>
            <button
              type="button"
              className="platform-button"
              disabled={cursorStack.length === 1 || eventsQuery.isFetching}
              onClick={() => setCursorStack((current) => current.slice(0, -1))}
            >
              <ChevronLeft aria-hidden="true" size={15} />
              {isFa ? "قبلی" : "Previous"}
            </button>
            <span className="text-xs text-text-muted">
              {isFa ? `صفحه ${cursorStack.length}` : `Page ${cursorStack.length}`}
            </span>
            <button
              type="button"
              className="platform-button"
              disabled={!eventsQuery.data.next_before || !eventsQuery.data.next_before_id || eventsQuery.isFetching}
              onClick={() => {
                if (!eventsQuery.data.next_before || !eventsQuery.data.next_before_id) return;
                setCursorStack((current) => [
                  ...current,
                  {
                    before: eventsQuery.data.next_before ?? undefined,
                    before_id: eventsQuery.data.next_before_id ?? undefined,
                  },
                ]);
              }}
            >
              {isFa ? "بعدی" : "Next"}
              <ChevronRight aria-hidden="true" size={15} />
            </button>
          </nav>
        </>
      ) : (
        <div className="p-4">
          <EmptyState label={isFa ? "رویداد مصرفی ثبت نشده است" : "No usage events recorded"} />
        </div>
      )}
    </section>
  );
}

function UsageContent() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const subscriptionQuery = useCurrentSubscription();
  const usageQuery = useBillingUsage();
  const subscription = subscriptionQuery.data?.subscription;
  const plan = subscriptionQuery.data?.plan;
  const metrics = useMemo(
    () => Array.from(new Set((usageQuery.data?.items ?? []).map((item) => item.metric))).sort(),
    [usageQuery.data?.items],
  );
  const usageItems = usageQuery.data?.items ?? [];

  return (
    <div className="space-y-5">
      <div className="platform-billing-summary grid gap-5 lg:grid-cols-2">
        <section className="platform-subscription platform-surface p-4 sm:p-5" aria-labelledby="subscription-heading">
          <div className="mb-4 flex items-center gap-3">
            <CreditCard aria-hidden="true" className="text-accent" size={20} />
            <h2 id="subscription-heading" className="m-0 text-sm font-semibold text-text-primary">
              {isFa ? "اشتراک فعلی" : "Current subscription"}
            </h2>
          </div>
          {subscriptionQuery.isLoading ? (
            <LoadingState label={isFa ? "در حال دریافت اشتراک" : "Loading subscription"} />
          ) : subscriptionQuery.isError ? (
            <ErrorState
              error={subscriptionQuery.error}
              fallback={isFa ? "دریافت اشتراک ناموفق بود" : "Could not load subscription"}
            />
          ) : subscription ? (
            <div className="space-y-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <strong className="block text-lg text-text-primary">{plan?.name ?? subscription.plan_key}</strong>
                  <span className="mt-1 block text-xs text-text-secondary" dir="ltr">{subscription.plan_key}</span>
                </div>
                <StatusBadge status={subscription.status} />
              </div>
              <dl className="grid grid-cols-1 gap-3 text-xs sm:grid-cols-2">
                <div>
                  <dt className="text-text-muted">{isFa ? "شروع" : "Started"}</dt>
                  <dd className="mt-1 mb-0 font-semibold text-text-primary">{formatDate(subscription.starts_at, lang)}</dd>
                </div>
                <div>
                  <dt className="text-text-muted">{isFa ? "پایان" : "Ends"}</dt>
                  <dd className="mt-1 mb-0 font-semibold text-text-primary">
                    {subscription.ends_at ? formatDate(subscription.ends_at, lang) : (isFa ? "بدون تاریخ پایان" : "No end date")}
                  </dd>
                </div>
              </dl>
            </div>
          ) : (
            <EmptyState label={isFa ? "اشتراک فعالی ثبت نشده است" : "No active subscription"} />
          )}
        </section>

        <aside className="platform-pricing-notice platform-surface p-4 sm:p-5" aria-labelledby="pricing-heading">
          <div className="mb-4 flex items-center gap-3">
            <ReceiptText aria-hidden="true" className="text-accent-secondary" size={20} />
            <h2 id="pricing-heading" className="m-0 text-sm font-semibold text-text-primary">
              {isFa ? "مدیریت صورتحساب" : "Billing management"}
            </h2>
          </div>
          <p className="m-0 text-sm leading-6 text-text-secondary">
            {isFa
              ? "قیمت پلن‌ها فعلاً منتشر نشده و صورتحساب به‌صورت دستی مدیریت می‌شود. این صفحه هیچ مبلغ تخمینی یا امکان تغییر آنلاین پلن نمایش نمی‌دهد."
              : "Plan pricing is currently unpublished and billing is managed manually. This page does not show estimated prices or offer online plan changes."}
          </p>
          <div className="mt-4 flex flex-wrap gap-2" aria-label={isFa ? "وضعیت صورتحساب" : "Billing status"}>
            <span className="platform-status">{isFa ? "قیمت منتشرنشده" : "Pricing unpublished"}</span>
            <span className="platform-status">{isFa ? "مدیریت دستی" : "Manual billing"}</span>
          </div>
        </aside>
      </div>

      <section className="platform-quotas platform-surface overflow-hidden" aria-labelledby="quota-heading">
        <div className="flex items-center gap-3 border-b border-border-subtle px-4 py-4 sm:px-5">
          <Gauge aria-hidden="true" className="text-accent" size={20} />
          <div>
            <h2 id="quota-heading" className="m-0 text-sm font-semibold text-text-primary">
              {isFa ? "سهمیه‌ها" : "Quota usage"}
            </h2>
            <p className="mt-1 mb-0 text-xs text-text-secondary">
              {isFa ? "مصرف قطعی و رزروهای در حال پردازش" : "Committed usage and in-flight reservations"}
            </p>
          </div>
        </div>
        {usageQuery.isLoading ? (
          <LoadingState label={isFa ? "در حال دریافت سهمیه‌ها" : "Loading quotas"} />
        ) : usageQuery.isError ? (
          <div className="p-4">
            <ErrorState
              error={usageQuery.error}
              fallback={isFa ? "دریافت سهمیه‌ها ناموفق بود" : "Could not load quota usage"}
            />
          </div>
        ) : usageItems.length ? (
          <>
            <div className="px-4 pt-4 sm:px-5">
              <AllowanceNotice item={usageItems.find((item) => item.metric === "audio_seconds")} />
            </div>
            <ul className="m-0 list-none p-0">
              {usageItems.map((item) => <QuotaRow key={item.metric} item={item} lang={lang} />)}
            </ul>
          </>
        ) : (
          <div className="p-4">
            <EmptyState label={isFa ? "سهمیه‌ای برای این فضا ثبت نشده است" : "No quotas configured for this workspace"} />
          </div>
        )}
      </section>

      <UsageEvents metrics={metrics} />
    </div>
  );
}

export default function UsagePage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const canReadBilling = roleCan(workspace?.role).readBilling;

  return (
    <main className="platform-page platform-usage-page">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={isFa ? "مصرف و اشتراک" : "Usage and subscription"}
        actions={(
          <span className="inline-flex items-center gap-2 text-xs text-text-secondary">
            <BarChart3 aria-hidden="true" size={15} />
            {isFa ? "داده واقعی فضای کاری" : "Live workspace data"}
          </span>
        )}
      />
      {!workspace ? (
        <ErrorState error={null} fallback={isFa ? "فضای کاری فعالی انتخاب نشده است." : "No active workspace is selected."} />
      ) : !canReadBilling ? (
        <section className="platform-surface p-5" aria-labelledby="billing-access-heading">
          <div className="flex items-start gap-3">
            <CreditCard aria-hidden="true" className="mt-0.5 shrink-0 text-text-muted" size={20} />
            <div>
              <h2 id="billing-access-heading" className="m-0 text-sm font-semibold text-text-primary">
                {isFa ? "دسترسی صورتحساب محدود است" : "Billing access is restricted"}
              </h2>
              <p className="mt-2 mb-0 text-sm text-text-secondary">
                {isFa ? "فقط مالک یا مدیر فضای کاری می‌تواند اشتراک و مصرف را مشاهده کند." : "Only workspace owners and administrators can view subscription and usage data."}
              </p>
            </div>
          </div>
        </section>
      ) : (
        <UsageContent />
      )}
    </main>
  );
}
