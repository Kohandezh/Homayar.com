import MembersCard from "@/components/admin/overview/MembersCard";
import NeedsAttentionCard from "@/components/admin/overview/NeedsAttentionCard";
import PlanCard from "@/components/admin/overview/PlanCard";
import ProcessingCard from "@/components/admin/overview/ProcessingCard";
import QuotaCard from "@/components/admin/overview/QuotaCard";
import RecentActivityCard from "@/components/admin/overview/RecentActivityCard";
import SystemStatusChip from "@/components/admin/overview/SystemStatusChip";
import { PageHeader } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useCan } from "@/lib/use-can";

/**
 * Workspace health at a glance (HOM-177). Each card owns its requests, so a
 * card the workspace may not read is not rendered and sends nothing, and one
 * failed request only changes its own card.
 */
export default function AdminOverviewPage() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const { has } = useCan();
  const canReadMeetings = has("meeting:read");

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={isFa ? "مدیریت" : "Administration"}
        title={isFa ? "نمای کلی مدیریت" : "Admin overview"}
        actions={<SystemStatusChip />}
      />
      <div className="grid grid-cols-1 items-start gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {canReadMeetings && <ProcessingCard />}
        {canReadMeetings && <NeedsAttentionCard />}
        {has("billing:read") && <QuotaCard />}
        {has("billing:read") && <PlanCard />}
        {has("member:read") && <MembersCard />}
        {has("audit:read") && <RecentActivityCard />}
      </div>
    </div>
  );
}
