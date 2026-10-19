import LegalHoldRegisterCard from "@/components/admin/privacy/LegalHoldRegisterCard";
import PrivacyAuditLinks from "@/components/admin/privacy/PrivacyAuditLinks";
import { PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import SharedLinksCard from "@/components/admin/privacy/SharedLinksCard";
import SubjectExportCard from "@/components/admin/privacy/SubjectExportCard";
import { PageHeader } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";

/**
 * The owner's privacy page (HOM-190): the data subject export, the
 * legal-hold register, the register of the workspace's share links (HOM-259)
 * and links into the audit log. The route guard opens it only for
 * `pii:original_access`, so other roles never mount it.
 */
export default function PrivacyPage() {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  return (
    <div className="platform-page">
      <PageHeader eyebrow={copy.eyebrow} title={copy.heading} />
      <p className="mt-0 mb-4 text-sm text-muted">{copy.intro}</p>
      <div className="grid gap-4">
        <SubjectExportCard />
        <LegalHoldRegisterCard />
        <SharedLinksCard />
        <PrivacyAuditLinks />
      </div>
    </div>
  );
}
