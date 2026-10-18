import { useState, type FormEvent } from "react";
import { Button, Input, Label, Modal, TextField, toast } from "@heroui/react";

import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useCreateTeam, useUpdateTeam } from "@/lib/platform-api";
import type { Team } from "@/types/platform";
import { TEAM_ERROR_CODES, TEAMS_COPY } from "@/components/admin/teams/teams-copy";

const NAME_MAX_LENGTH = 180;

/**
 * The create and the rename dialog: one name field, one request. It is
 * mounted only while open, so every open starts from a clean form. A refused
 * name keeps the dialog open with what was typed.
 */
export default function TeamNameDialog({
  mode,
  team,
  onClose,
}: {
  mode: "create" | "rename";
  team: Team | null;
  onClose: () => void;
}) {
  const { lang } = useI18n();
  const copy = TEAMS_COPY[lang];
  const createMutation = useCreateTeam();
  const renameMutation = useUpdateTeam();
  const pending = mode === "create" ? createMutation.isPending : renameMutation.isPending;
  const error = mode === "create" ? createMutation.error : renameMutation.error;
  const [name, setName] = useState(mode === "rename" ? (team?.name ?? "") : "");

  const trimmed = name.trim();
  // An unchanged rename would be refused with `no_changes`, so it never sends.
  const canSubmit = trimmed.length > 0 && (mode === "create" || trimmed !== team?.name);

  function changeOpen(open: boolean) {
    if (pending) return;
    if (!open) onClose();
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending || !canSubmit) return;
    if (mode === "create") {
      createMutation.mutate(
        { name: trimmed },
        {
          onSuccess: () => {
            onClose();
            toast.success(copy.teamCreated);
          },
        },
      );
    } else if (team) {
      renameMutation.mutate(
        { teamId: team.id, patch: { name: trimmed } },
        {
          onSuccess: () => {
            onClose();
            toast.success(copy.teamRenamed);
          },
        },
      );
    }
  }

  return (
    <Modal isOpen onOpenChange={changeOpen}>
      <Modal.Backdrop>
        <Modal.Container>
          <Modal.Dialog>
            <form onSubmit={submit} noValidate>
              <Modal.Header>
                <Modal.Heading>{mode === "create" ? copy.createHeading : copy.renameHeading}</Modal.Heading>
              </Modal.Header>
              <Modal.Body className="space-y-4">
                <TextField value={name} onChange={setName} maxLength={NAME_MAX_LENGTH} isReadOnly={pending} fullWidth>
                  <Label>{copy.nameLabel}</Label>
                  <Input dir="auto" />
                </TextField>

                {error && (
                  <ErrorState error={error} codes={TEAM_ERROR_CODES} fallback={copy.saveFailed} />
                )}
              </Modal.Body>
              <Modal.Footer>
                <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                  {copy.cancel}
                </Button>
                <Button type="submit" variant="primary" isDisabled={pending || !canSubmit}>
                  {mode === "create" ? copy.create : copy.save}
                </Button>
              </Modal.Footer>
            </form>
          </Modal.Dialog>
        </Modal.Container>
      </Modal.Backdrop>
    </Modal>
  );
}
