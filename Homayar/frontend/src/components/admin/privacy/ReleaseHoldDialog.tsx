import { useState, type FormEvent } from "react";
import { Alert, Button, Description, Input, Label, Modal, TextField, toast } from "@heroui/react";

import { describeHoldError } from "@/components/admin/privacy/hold-errors";
import { PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import { isValidReason, REASON_MAX_LENGTH } from "@/components/admin/privacy/privacy-model";
import { useI18n } from "@/lib/i18n-context";
import { useReleaseLegalHold } from "@/lib/privacy-api";

export interface ReleaseTarget {
  mediaId: string;
  meetingId: string | null;
}

/**
 * The release confirmation, shared by the register and the meeting panel.
 * It asks for the reason the audit log keeps. Mounted only while open; a
 * refused release keeps it open with the reason inside.
 */
export default function ReleaseHoldDialog({ target, onClose }: { target: ReleaseTarget; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  const mutation = useReleaseLegalHold();
  const [reason, setReason] = useState("");
  const canSubmit = isValidReason(reason) && !mutation.isPending;
  const failure = mutation.error ? describeHoldError(mutation.error, lang, copy.releaseFailed) : null;

  function changeOpen(open: boolean) {
    if (mutation.isPending) return;
    if (!open) onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSubmit) return;
    mutation.mutate(
      { mediaId: target.mediaId, meetingId: target.meetingId, reason: reason.trim() },
      {
        onSuccess: () => {
          toast.success(copy.holdReleased);
          onClose();
        },
      },
    );
  }

  return (
    // The documented controlled form: open state on the backdrop, no Modal
    // root. A root is a DialogTrigger and warns when it has no pressable child.
    <Modal.Backdrop isOpen onOpenChange={changeOpen}>
      <Modal.Container>
        <Modal.Dialog>
          <form onSubmit={submit} noValidate>
            <Modal.Header>
              <Modal.Heading>{copy.releaseHeading}</Modal.Heading>
            </Modal.Header>
            <Modal.Body className="space-y-4">
              <p className="m-0 text-sm text-muted">{copy.releaseBody}</p>
              <TextField
                value={reason}
                onChange={setReason}
                maxLength={REASON_MAX_LENGTH}
                isReadOnly={mutation.isPending}
                fullWidth
              >
                <Label>{copy.reasonLabel}</Label>
                <Input dir="auto" />
                <Description>{copy.reasonHint}</Description>
              </TextField>
              {failure && (
                <Alert status="danger" role="alert">
                  <Alert.Indicator aria-hidden="true" />
                  <Alert.Content>
                    <Alert.Description>{failure.text}</Alert.Description>
                  </Alert.Content>
                </Alert>
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button variant="ghost" isDisabled={mutation.isPending} onPress={() => changeOpen(false)}>
                {copy.cancel}
              </Button>
              <Button type="submit" variant="danger" isDisabled={!canSubmit}>
                {copy.releaseConfirm}
              </Button>
            </Modal.Footer>
          </form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
