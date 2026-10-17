import PlanCatalogCard from "@/components/admin/usage/PlanCatalogCard";
import QuotaMetersCard from "@/components/admin/usage/QuotaMetersCard";
import SubscriptionCard from "@/components/admin/usage/SubscriptionCard";
import UsageEventsCard from "@/components/admin/usage/UsageEventsCard";
import { PageHeader } from "@/components/platform/Ui";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";

/**
 * Usage and subscription (HOM-181, SPEC U1). The route guard already requires
 * `billing:read`, so this component never mounts for a role without it and
 * sends nothing. Each card owns its request, so one failed request only
 * changes its own card.
 */
export default function AdminUsagePage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={isFa ? "مصرف و اشتراک" : "Usage and subscription"}
      />
      <div className="flex flex-col gap-4">
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
          <SubscriptionCard />
          <QuotaMetersCard />
        </div>
        <UsageEventsCard />
        <PlanCatalogCard />
      </div>
    </div>
  );
}
