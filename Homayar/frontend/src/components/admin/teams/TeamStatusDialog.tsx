import { useState } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";
import { PowerOff, RotateCcw } from "lucide-react";

import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useUpdateTeam } from "@/lib/platform-api";
import type { Team } from "@/types/platform";
import { TEAM_ERROR_CODES, TEAMS_COPY } from "@/components/admin/teams/teams-copy";

/**
 * The deactivate and the reactivate confirmation. Which one it is follows
 * the team's current `is_active`, so the confirm always flips it. Mounted
 * only while open; a failure keeps it open with the reason inside.
 */
export default function TeamStatusDialog({ team, onClose }: { team: Team; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = TEAMS_COPY[lang];
  const mutation = useUpdateTeam();
  // The dialog closes over a snapshot: a name that changes underneath must
  // not rewrite the question mid-read.
  const [target] = useState(team);
  const deactivating = target.is_active;

  async function confirm() {
    if (mutation.isPending) return;
    try {
      await mutation.mutateAsync({ teamId: target.id, patch: { is_active: !target.is_active } });
      toast.success(deactivating ? copy.teamDeactivated : copy.teamReactivated);
      onClose();
    } catch {
      // The alert inside the dialog shows the reason.
    }
  }

  return (
    <AlertDialog
      isOpen
      onOpenChange={(open) => {
        if (!mutation.isPending && !open) onClose();
      }}
    >
      <AlertDialog.Backdrop>
        <AlertDialog.Container>
          <AlertDialog.Dialog>
            <AlertDialog.Header>
              <AlertDialog.Icon status={deactivating ? "danger" : "success"} />
              <AlertDialog.Heading>
                {deactivating ? copy.deactivateHeading(target.name) : copy.reactivateHeading(target.name)}
              </AlertDialog.Heading>
            </AlertDialog.Header>
            <AlertDialog.Body className="space-y-3">
              <p className="m-0 text-sm text-muted" dir="auto">
                {deactivating ? copy.deactivateBody : copy.reactivateBody}
              </p>
              {mutation.isError && (
                <ErrorState error={mutation.error} codes={TEAM_ERROR_CODES} fallback={copy.statusChangeFailed} />
              )}
            </AlertDialog.Body>
            <AlertDialog.Footer>
              <Button variant="ghost" isDisabled={mutation.isPending} onPress={onClose}>
                {copy.cancel}
              </Button>
              <Button
                variant={deactivating ? "danger" : "primary"}
                isDisabled={mutation.isPending}
                onPress={() => void confirm()}
              >
                {deactivating ? <PowerOff aria-hidden="true" size={15} /> : <RotateCcw aria-hidden="true" size={15} />}
                {deactivating ? copy.deactivate : copy.reactivate}
              </Button>
            </AlertDialog.Footer>
          </AlertDialog.Dialog>
        </AlertDialog.Container>
      </AlertDialog.Backdrop>
    </AlertDialog>
  );
}
