import { useState } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";
import { ShieldMinus } from "lucide-react";

import { useOrgAccess } from "@/components/org/org-access";
import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { grantMemberName, grantWorkspaceName, type ScopeGrantRow } from "@/components/org/org-model";
import { NamedText } from "@/components/org/OrgUi";
import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, useRevokeScopeGrant } from "@/lib/org-api";

/** The confirmation behind a scope grant's Revoke button. Mounted only while open. */
export default function RevokeScopeGrantDialog({ grant, onClose }: { grant: ScopeGrantRow; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const access = useOrgAccess();
  const mutation = useRevokeScopeGrant(access.organization.id);
  const [target] = useState(grant);

  function changeOpen(open: boolean) {
    if (!mutation.isPending && !open) onClose();
  }

  async function confirm() {
    if (mutation.isPending) return;
    try {
      await mutation.mutateAsync(target.id);
      toast.success(copy.grantRevoked);
      onClose();
    } catch (error) {
      access.reportError(error);
    }
  }

  return (
    <AlertDialog.Backdrop isOpen onOpenChange={changeOpen}>
      <AlertDialog.Container>
        <AlertDialog.Dialog>
          <AlertDialog.Header>
            <AlertDialog.Icon status="danger" />
            <AlertDialog.Heading>{copy.revokeGrantHeading}</AlertDialog.Heading>
          </AlertDialog.Header>
          <AlertDialog.Body className="space-y-3">
            <p className="m-0 text-sm text-muted">
              <NamedText
                template={copy.revokeGrantBody}
                names={{ name: grantMemberName(target), workspace: grantWorkspaceName(target) }}
              />
            </p>
            {mutation.isError && !isOrganizationGone(mutation.error) && (
              <ErrorState error={mutation.error} codes={ORG_ERROR_CODES} fallback={copy.revokeGrantFailed} />
            )}
          </AlertDialog.Body>
          <AlertDialog.Footer>
            <Button variant="ghost" isDisabled={mutation.isPending} onPress={onClose}>
              {copy.cancel}
            </Button>
            <Button variant="danger" isDisabled={mutation.isPending} onPress={() => void confirm()}>
              <ShieldMinus aria-hidden="true" size={15} />
              {copy.revoke}
            </Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog>
      </AlertDialog.Container>
    </AlertDialog.Backdrop>
  );
}
