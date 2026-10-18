import { useState } from "react";
import { AlertDialog, Button, toast } from "@heroui/react";

import { ErrorState } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { useI18n } from "@/lib/i18n-context";
import { useRevokeApiKey } from "@/lib/platform-api";
import type { ApiKey } from "@/types/platform";

/** Error codes of DELETE /api-keys/{id} (backend/routers/v1/api_keys.py). */
const REVOKE_ERROR_CODES: ErrorCodeMessages = {
  api_key_not_found: {
    fa: "این کلید دیگر وجود ندارد. فهرست به‌روز شد.",
    en: "This key no longer exists. The list has been refreshed.",
  },
};

/**
 * The confirmation behind every "Revoke" button (K6). The dialog is owned by
 * the page, so one instance serves the whole table. `onClose(removed)` tells
 * the page whether the key is gone from the list (success or 404) or whether
 * the dialog was merely cancelled, so focus lands correctly either way (A4).
 */
export default function RevokeApiKeyDialog({
  target,
  onClose,
}: {
  target: ApiKey | null;
  onClose: (removed: boolean) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const revokeMutation = useRevokeApiKey();
  const [failedElsewhere, setFailedElsewhere] = useState(false);
  const pending = revokeMutation.isPending;

  function changeOpen(open: boolean) {
    if (pending) return;
    if (open) return;
    revokeMutation.reset();
    setFailedElsewhere(false);
    onClose(false);
  }

  function confirm() {
    if (pending || !target) return;
    setFailedElsewhere(false);
    revokeMutation.mutate(target.id, {
      onSuccess: () => {
        onClose(true);
        toast.success(isFa ? "کلید API باطل شد" : "API key revoked");
      },
      onError: (error) => {
        const info = apiErrorInfo(error);
        if (info.status === 404 && info.code === "api_key_not_found") {
          // Gone on the server: close and let the settled invalidation refresh
          // the list, so the row stops claiming the key is active.
          onClose(true);
          toast.danger(
            isFa
              ? "این کلید دیگر وجود ندارد. فهرست به‌روز شد."
              : "This key no longer exists. The list has been refreshed.",
          );
          return;
        }
        // Any other refusal keeps the dialog open, so its reason has to be
        // inside it: the page banner sits behind the backdrop.
        setFailedElsewhere(true);
      },
    });
  }

  if (!target) return null;

  return (
    <AlertDialog
      isOpen={target !== null}
      onOpenChange={changeOpen}
    >
      <AlertDialog.Backdrop>
        <AlertDialog.Container>
          <AlertDialog.Dialog>
            <AlertDialog.Header>
              <AlertDialog.Icon status="danger" />
              <AlertDialog.Heading>
                {isFa ? "کلید API باطل شود؟" : "Revoke API key?"}
              </AlertDialog.Heading>
            </AlertDialog.Header>
            <AlertDialog.Body className="space-y-3">
              <p className="m-0 text-sm text-muted" dir="auto">
                {isFa
                  ? `${target.name} همین حالا از کار می‌افتد و سامانه‌هایی که از آن استفاده می‌کنند دیگر وصل نمی‌شوند. این کار برگشت‌پذیر نیست.`
                  : `${target.name} stops working at once. Systems that use it can no longer connect. This cannot be undone.`}
              </p>
              {failedElsewhere && (
                <ErrorState
                  error={revokeMutation.error}
                  codes={REVOKE_ERROR_CODES}
                  fallback={isFa ? "ابطال کلید ناموفق بود." : "Revoking the key failed."}
                />
              )}
            </AlertDialog.Body>
            <AlertDialog.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={() => changeOpen(false)}>
                {isFa ? "انصراف" : "Cancel"}
              </Button>
              <Button variant="danger" isDisabled={pending} onPress={confirm}>
                {pending ? (isFa ? "در حال ابطال" : "Revoking") : (isFa ? "ابطال کلید" : "Revoke key")}
              </Button>
            </AlertDialog.Footer>
          </AlertDialog.Dialog>
        </AlertDialog.Container>
      </AlertDialog.Backdrop>
    </AlertDialog>
  );
}
