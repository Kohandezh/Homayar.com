import { useRef, useState } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";

import { DRAIN_ERROR_CODES, VOICE_COPY } from "@/components/admin/voice/voice-copy";
import { ErrorState } from "@/components/platform/Ui";
import { isolateLtr } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useSetNodeDraining } from "@/lib/voice-api";
import type { VoiceMediaNode } from "@/types/voice";

/**
 * The drain and the resume confirmation. Which one it is follows the node's
 * `draining` when the dialog opened, so the confirm always flips it. Mounted
 * only while open, so every opening starts with no old error. A failure keeps
 * the dialog open with the reason inside it.
 */
export default function DrainNodeDialog({ node, onClose }: { node: VoiceMediaNode; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = VOICE_COPY[lang];
  const mutation = useSetNodeDraining();
  // A snapshot: the 15 s refresh must not change the question while it is read.
  const [target] = useState(node);
  // Set at once on confirm, so a second click before the next render sends nothing.
  const sending = useRef(false);
  const resuming = target.draining;
  const nodeId = target.node_id;
  // The id is Latin text: isolated, it keeps its order inside a Persian sentence.
  const shownId = isolateLtr(nodeId);
  const pending = mutation.isPending;

  function close() {
    if (!sending.current) onClose();
  }

  function confirm() {
    if (sending.current) return;
    sending.current = true;
    mutation.mutate(
      { nodeId, draining: !resuming },
      {
        onSuccess: () => {
          toast.success(resuming ? copy.resumed(shownId) : copy.drained(shownId));
          onClose();
        },
        onSettled: () => {
          sending.current = false;
        },
      },
    );
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
            <AlertDialog.Icon status={resuming ? "accent" : "warning"} />
            <AlertDialog.Heading>
              {resuming ? copy.resumeHeading(shownId) : copy.drainHeading(shownId)}
            </AlertDialog.Heading>
          </AlertDialog.Header>
          <AlertDialog.Body className="space-y-3">
            <p className="m-0 text-sm text-muted">{resuming ? copy.resumeBody : copy.drainBody}</p>
            {mutation.isError && <ErrorState error={mutation.error} codes={DRAIN_ERROR_CODES} />}
          </AlertDialog.Body>
          <AlertDialog.Footer>
            <Button variant="ghost" isDisabled={pending} onPress={close}>
              {copy.cancel}
            </Button>
            <Button
              variant={resuming ? "primary" : "danger"}
              isDisabled={pending}
              isPending={pending}
              onPress={confirm}
            >
              {resuming ? copy.resumeConfirm : copy.drainConfirm}
            </Button>
          </AlertDialog.Footer>
        </AlertDialog.Dialog>
      </AlertDialog.Container>
    </AlertDialog.Backdrop>
  );
}
