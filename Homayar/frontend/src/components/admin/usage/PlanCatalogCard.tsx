import { Chip, Link, Table } from "@heroui/react";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import {
  catalogMetrics,
  entitlementValue,
  formatMetricValue,
  metricLabel,
} from "@/components/admin/usage/usage-model";
import { useI18n } from "@/lib/i18n-context";
import { useCurrentSubscription, usePlanCatalog } from "@/lib/platform-api";
import type { Plan } from "@/types/platform";

/** The public site's contact page, the same target as AllowanceNotice. */
const CONTACT_URL = "/contact";

function entitlementText(plan: Plan, metric: string, lang: "fa" | "en"): string {
  const value = entitlementValue(plan, metric);
  if (value.kind === "limit") return formatMetricValue(metric, value.value, lang);
  if (value.kind === "unlimited") return lang === "fa" ? "نامحدود" : "Unlimited";
  return lang === "fa" ? "طبق توافق" : "By agreement";
}

/**
 * The plans on offer, compared by what they grant (SPEC U6). No price is
 * shown anywhere: the catalog publishes none and every quote is manual, so
 * the only call to action is to contact us.
 */
export default function PlanCatalogCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = usePlanCatalog();
  // The same request as the current-plan card; it only marks the column.
  const currentKey = useCurrentSubscription().data?.subscription?.plan_key ?? null;
  const state = queryCardState(query, (data) => data.items.length === 0);
  const plans = query.data?.items ?? [];
  const metrics = catalogMetrics(plans);

  return (
    <OverviewCard
      title={isFa ? "طرح‌های اشتراک" : "Plan catalog"}
      state={state}
      error={query.error}
      emptyText={isFa ? "طرحی برای نمایش نیست" : "No plans to show"}
      footer={
        <div className="flex w-full flex-wrap items-center justify-between gap-3">
          <span className="text-sm text-muted">
            {isFa
              ? "طرح‌ها را تیم ما تنظیم می‌کند. برای تغییر طرح یا سقف‌های آن با ما تماس بگیرید."
              : "Plans are set up by our team. To change your plan or its limits, contact us."}
          </span>
          <Link href={CONTACT_URL}>{isFa ? "با ما تماس بگیرید" : "Contact us"}</Link>
        </div>
      }
    >
      <Table className="min-w-0">
        <Table.ScrollContainer>
          <Table.Content aria-label={isFa ? "مقایسهٔ طرح‌ها" : "Plan comparison"} className="min-w-[560px]">
            <Table.Header>
              <Table.Column isRowHeader>{isFa ? "سهمیه" : "Quota"}</Table.Column>
              {plans.map((plan) => (
                <Table.Column key={plan.key} id={plan.key}>
                  <span className="flex flex-wrap items-center gap-2">
                    {plan.name}
                    {plan.key === currentKey && (
                      <Chip variant="soft" size="sm" color="accent">
                        <Chip.Label>{isFa ? "طرح فعلی" : "Current plan"}</Chip.Label>
                      </Chip>
                    )}
                  </span>
                </Table.Column>
              ))}
            </Table.Header>
            <Table.Body>
              {metrics.map((metric) => (
                <Table.Row key={metric} id={metric}>
                  <Table.Cell>{metricLabel(metric, lang)}</Table.Cell>
                  {plans.map((plan) => (
                    <Table.Cell key={plan.key}>{entitlementText(plan, metric, lang)}</Table.Cell>
                  ))}
                </Table.Row>
              ))}
            </Table.Body>
          </Table.Content>
        </Table.ScrollContainer>
      </Table>
    </OverviewCard>
  );
}
