import { useState } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";

import { PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import { ErrorState } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { useI18n } from "@/lib/i18n-context";
import { useRevokeAdminShare } from "@/lib/privacy-api";
import type { AdminShareItem } from "@/types/privacy";

/**
 * The confirmation behind every "Revoke" button of the shared-links card
 * (HOM-259). The dialog is owned by the card, so one instance serves the
 * whole table. A 404 closes it: the link is gone and the settled
 * invalidation refreshes the list, so the row stops claiming the link is
 * active. Any other refusal keeps it open with its reason inside.
 */
export default function RevokeShareDialog({
  target,
  onClose,
}: {
  target: AdminShareItem | null;
  onClose: () => void;
}) {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  const revokeMutation = useRevokeAdminShare();
  const [failedElsewhere, setFailedElsewhere] = useState(false);
  const pending = revokeMutation.isPending;

  function changeOpen(open: boolean) {
    if (pending) return;
    if (open) return;
    revokeMutation.reset();
    setFailedElsewhere(false);
    onClose();
  }

  function confirm() {
    if (pending || !target) return;
    setFailedElsewhere(false);
    revokeMutation.mutate(
      { meetingId: target.meeting_id, shareId: target.id },
      {
        onSuccess: () => {
          onClose();
          toast.success(copy.shareRevoked);
        },
        onError: (error) => {
          const info = apiErrorInfo(error);
          if (info.status === 404 && (info.code === "share_not_found" || info.code === "meeting_not_found")) {
            // Gone on the server: close and let the settled invalidation refresh
            // the register.
            onClose();
            toast.danger(copy.shareNotFound);
            return;
          }
          // Any other refusal keeps the dialog open, so its reason has to be
          // inside it: the card sits behind the backdrop.
          setFailedElsewhere(true);
        },
      },
    );
  }

  if (!target) return null;

  return (
    // The documented controlled form: open state on the backdrop, no
    // AlertDialog root (a DialogTrigger that warns without a pressable child).
    <AlertDialog.Backdrop isOpen={target !== null} onOpenChange={changeOpen}>
      <AlertDialog.Container>
        <AlertDialog.Dialog>
          <AlertDialog.Header>
            <AlertDialog.Icon status="danger" />
            <AlertDialog.Heading>{copy.revokeShareHeading}</AlertDialog.Heading>
          </AlertDialog.Header>
          <AlertDialog.Body className="space-y-3">
            <p className="m-0 text-sm text-muted" dir="auto">
              <bdi>{target.meeting_title || copy.untitledConversation}</bdi>
            </p>
            <p className="m-0 text-sm text-muted">{copy.revokeShareBody}</p>
            {/* The two 404 codes never land here: they close the dialog. */}
            {failedElsewhere && <ErrorState error={revokeMutation.error} fallback={copy.shareRevokeFailed} />}
          </AlertDialog.Body>
          <AlertDialog.Footer>
            <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
              {copy.cancel}
            </Button>
            <Button variant="danger" isDisabled={pending} onPress={confirm}>
              {pending ? copy.revoking : copy.revokeLink}
            </Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog>
      </AlertDialog.Container>
    </AlertDialog.Backdrop>
  );
}
