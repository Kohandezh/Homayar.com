import { Link } from "react-router";
import { ScrollText } from "lucide-react";

import OverviewCard from "@/components/admin/overview/OverviewCard";
import { overviewLinkClass } from "@/components/admin/overview/card-state";
import { PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import { useI18n } from "@/lib/i18n-context";

/**
 * The audit filter is an exact event type, so each kind of privacy event
 * gets its own link.
 */
const AUDIT_LINKS = [
  { eventType: "subject.exported", label: "auditExports" },
  { eventType: "media.legal_hold_placed", label: "auditPlaced" },
  { eventType: "media.legal_hold_released", label: "auditReleased" },
] as const;

export default function PrivacyAuditLinks() {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  return (
    <OverviewCard title={copy.auditHeading} state="data">
      <p className="mt-0 mb-3 text-sm text-muted">{copy.auditIntro}</p>
      <ul className="m-0 flex list-none flex-wrap gap-x-6 gap-y-2 p-0">
        {AUDIT_LINKS.map((link) => (
          <li key={link.eventType}>
            <Link
              className={`${overviewLinkClass} inline-flex items-center gap-1.5`}
              to={`/admin/audit?event_type=${encodeURIComponent(link.eventType)}`}
            >
              <ScrollText aria-hidden="true" size={15} />
              {copy[link.label]}
            </Link>
          </li>
        ))}
      </ul>
    </OverviewCard>
  );
}
