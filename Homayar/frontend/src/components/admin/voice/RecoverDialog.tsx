import { useRef } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";

import { RECOVER_ERROR_CODES, VOICE_COPY } from "@/components/admin/voice/voice-copy";
import { ErrorState } from "@/components/platform/Ui";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useRecoverVoice } from "@/lib/voice-api";

/**
 * The recovery confirmation. Mounted only while open, so every opening
 * starts with no old error. A failure keeps the dialog open with the
 * reason inside it.
 */
export default function RecoverDialog({ onClose }: { onClose: () => void }) {
  const { lang } = useI18n();
  const copy = VOICE_COPY[lang];
  const mutation = useRecoverVoice();
  // Set at once on confirm, so a second click before the next render sends nothing.
  const sending = useRef(false);
  const pending = mutation.isPending;

  function close() {
    if (!sending.current) onClose();
  }

  function confirm() {
    if (sending.current) return;
    sending.current = true;
    mutation.mutate(undefined, {
      onSuccess: (answer) => {
        const failed = answer.failed_calls.node_lost.length + answer.failed_calls.lease_expired.length;
        if (answer.lost_nodes.length === 0 && failed === 0) {
          toast.success(copy.recoveredNothing);
        } else {
          toast.success(
            copy.recovered(formatNumber(answer.lost_nodes.length, lang), formatNumber(failed, lang)),
          );
        }
        onClose();
      },
      onSettled: () => {
        sending.current = false;
      },
    });
  }

  return (
    // Controlled from the page, so there is no AlertDialog.Trigger: the
    // backdrop takes the open state (HeroUI "Controlled State").
    <AlertDialog.Backdrop
      isOpen
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <AlertDialog.Container>
        <AlertDialog.Dialog>
          <AlertDialog.Header>
            <AlertDialog.Icon status="warning" />
            <AlertDialog.Heading>{copy.recoverHeading}</AlertDialog.Heading>
          </AlertDialog.Header>
          <AlertDialog.Body className="space-y-3">
            <p className="m-0 text-sm text-muted">{copy.recoverBody}</p>
            {mutation.isError && <ErrorState error={mutation.error} codes={RECOVER_ERROR_CODES} />}
          </AlertDialog.Body>
          <AlertDialog.Footer>
            <Button variant="ghost" isDisabled={pending} onPress={close}>
              {copy.cancel}
            </Button>
            <Button variant="danger" isDisabled={pending} isPending={pending} onPress={confirm}>
              {copy.recoverConfirm}
            </Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog>
      </AlertDialog.Container>
    </AlertDialog.Backdrop>
  );
}
