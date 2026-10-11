import { Link } from "react-router-dom";
import OverviewCard from "@/components/admin/overview/OverviewCard";
import { overviewLinkClass, type CardState } from "@/components/admin/overview/card-state";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useInvitations, useMembers } from "@/lib/platform-api";

/** Two requests: either failure shows the error, even while the other is still loading. */
export default function MembersCard() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const members = useMembers();
  const invitations = useInvitations();
  const failed = members.isError ? members : invitations.isError ? invitations : null;
  const state: CardState = failed
    ? "error"
    : members.data === undefined || invitations.data === undefined
      ? "loading"
      : "data";
  const pending = invitations.data?.items.filter((invitation) => invitation.status === "pending").length ?? 0;
  const pairs = [
    { label: isFa ? "تعداد اعضا" : "Total members", value: members.data?.total ?? 0 },
    { label: isFa ? "دعوت‌های در انتظار" : "Pending invitations", value: pending },
  ];

  return (
    <OverviewCard
      title={isFa ? "اعضا" : "Members"}
      state={state}
      error={failed?.error}
      footer={
        <Link to="/admin/members" className={overviewLinkClass}>
          {isFa ? "مدیریت اعضا" : "Manage members"}
        </Link>
      }
    >
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
