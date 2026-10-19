import { useState } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";
import { Trash2 } from "lucide-react";

import { ErrorState } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useDeleteLocation } from "@/lib/locations-api";
import type { Location } from "@/types/locations";
import { LOCATIONS_COPY, LOCATION_ERROR_CODES } from "@/components/admin/locations/locations-copy";

/**
 * The delete confirmation. Mounted only while open; a failure keeps it open
 * with the reason inside, and the error never leaks into the next one.
 */
export default function LocationDeleteDialog({ location, onClose }: { location: Location; onClose: () => void }) {
  const { lang } = useI18n();
  const copy = LOCATIONS_COPY[lang];
  const mutation = useDeleteLocation();
  // The dialog closes over a snapshot: a name that changes underneath must
  // not rewrite the question mid-read.
  const [target] = useState(location);

  async function confirm() {
    if (mutation.isPending) return;
    try {
      await mutation.mutateAsync(target.id);
      toast.success(copy.deleted);
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
              <AlertDialog.Icon status="danger" />
              <AlertDialog.Heading>{copy.deleteHeading}</AlertDialog.Heading>
            </AlertDialog.Header>
            <AlertDialog.Body className="space-y-3">
              <p className="m-0 text-sm text-muted" dir="auto">
                “<bdi>{target.name}</bdi>” (<bdi dir="ltr">{target.code}</bdi>) {copy.deleteTail}
              </p>
              {mutation.isError && (
                <ErrorState error={mutation.error} codes={LOCATION_ERROR_CODES} fallback={copy.deleteFailed} />
              )}
            </AlertDialog.Body>
            <AlertDialog.Footer>
              <Button variant="ghost" isDisabled={mutation.isPending} onPress={onClose}>
                {copy.cancel}
              </Button>
              <Button variant="danger" isDisabled={mutation.isPending} onPress={() => void confirm()}>
                <Trash2 aria-hidden="true" size={15} />
                {copy.deleteConfirm}
              </Button>
            </AlertDialog.Footer>
          </AlertDialog.Dialog>
        </AlertDialog.Container>
      </AlertDialog.Backdrop>
    </AlertDialog>
  );
}
