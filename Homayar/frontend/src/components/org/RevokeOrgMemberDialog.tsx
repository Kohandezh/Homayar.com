import { useState } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";
import { UserMinus } from "lucide-react";

import { useOrgAccess } from "@/components/org/org-access";
import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { NamedText } from "@/components/org/OrgUi";
import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, useRevokeOrgMember } from "@/lib/org-api";
import type { OrgMember } from "@/types/organizations";

/**
 * The confirmation behind a row's Revoke button. The server keeps the row
 * with `revoked_at` set, so the list refreshes instead of dropping it.
 * Mounted only while open; a refusal keeps it open with the reason inside.
 */
export default function RevokeOrgMemberDialog({ member, onClose }: { member: OrgMember; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const access = useOrgAccess();
  const mutation = useRevokeOrgMember(access.organization.id);
  // A snapshot: a name that changes underneath must not rewrite the question.
  const [target] = useState(member);

  function changeOpen(open: boolean) {
    if (!mutation.isPending && !open) onClose();
  }

  async function confirm() {
    if (mutation.isPending) return;
    try {
      await mutation.mutateAsync(target.id);
      toast.success(copy.membershipRevoked);
      onClose();
    } catch (error) {
      // A gone organization swaps the page; any other reason shows below.
      access.reportError(error);
    }
  }

  return (
    <AlertDialog.Backdrop isOpen onOpenChange={changeOpen}>
      <AlertDialog.Container>
        <AlertDialog.Dialog>
          <AlertDialog.Header>
            <AlertDialog.Icon status="danger" />
            <AlertDialog.Heading>{copy.revokeHeading}</AlertDialog.Heading>
          </AlertDialog.Header>
          <AlertDialog.Body className="space-y-3">
            <p className="m-0 text-sm text-muted">
              <NamedText template={copy.revokeBody} names={{ name: target.display_name }} />
            </p>
            {mutation.isError && !isOrganizationGone(mutation.error) && (
              <ErrorState error={mutation.error} codes={ORG_ERROR_CODES} fallback={copy.revokeFailed} />
            )}
          </AlertDialog.Body>
          <AlertDialog.Footer>
            <Button variant="ghost" isDisabled={mutation.isPending} onPress={onClose}>
              {copy.cancel}
            </Button>
            <Button variant="danger" isDisabled={mutation.isPending} onPress={() => void confirm()}>
              <UserMinus aria-hidden="true" size={15} />
              {copy.revoke}
            </Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog>
      </AlertDialog.Container>
    </AlertDialog.Backdrop>
  );
}
