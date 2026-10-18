import { useState, type FormEvent } from "react";
import { Button, FieldError, Input, Label, Modal, TextField, toast } from "@heroui/react";

import { useOrgAccess } from "@/components/org/org-access";
import { ORG_COPY, ORG_ERROR_CODES } from "@/components/org/org-copy";
import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { isOrganizationGone, useCreateOrgWorkspace } from "@/lib/org-api";

const NAME_MAX_LENGTH = 180;

/** Creates a workspace in the organization. Mounted only while open. */
export default function CreateOrgWorkspaceModal({ onClose }: { onClose: () => void }) {
  const { lang } = useI18n();
  const copy = ORG_COPY[lang];
  const access = useOrgAccess();
  const mutation = useCreateOrgWorkspace(access.organization.id);
  const pending = mutation.isPending;
  const [name, setName] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const trimmed = name.trim();

  function changeOpen(open: boolean) {
    if (pending) return;
    if (!open) onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setSubmitted(true);
    if (!trimmed) return;
    mutation.mutate(
      { name: trimmed },
      {
        onSuccess: () => {
          onClose();
          toast.success(copy.workspaceCreated);
        },
        onError: (error) => {
          access.reportError(error);
        },
      },
    );
  }

  return (
    <Modal.Backdrop isOpen onOpenChange={changeOpen}>
      <Modal.Container>
        <Modal.Dialog>
          <form onSubmit={submit} noValidate>
            <Modal.Header>
              <Modal.Heading>{copy.createWorkspace}</Modal.Heading>
            </Modal.Header>
            <Modal.Body className="space-y-4">
              <TextField
                variant="secondary"
                value={name}
                onChange={setName}
                maxLength={NAME_MAX_LENGTH}
                isRequired
                isInvalid={submitted && !trimmed}
                isReadOnly={pending}
                fullWidth
              >
                <Label>{copy.name}</Label>
                <Input dir="auto" autoComplete="off" />
                <FieldError>{copy.nameRequired}</FieldError>
              </TextField>

              {mutation.isError && !isOrganizationGone(mutation.error) && (
                <ErrorState error={mutation.error} codes={ORG_ERROR_CODES} fallback={copy.createFailed} />
              )}
            </Modal.Body>
            <Modal.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                {copy.cancel}
              </Button>
              <Button type="submit" variant="primary" isDisabled={pending} isPending={pending}>
                {copy.createWorkspace}
              </Button>
            </Modal.Footer>
          </form>
        </Modal.Dialog>
      </Modal.Container>
    </Modal.Backdrop>
  );
}
