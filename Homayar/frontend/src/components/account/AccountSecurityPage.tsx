import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { Button, Modal, Surface, linkVariants } from "@heroui/react";
import { useQueryClient } from "@tanstack/react-query";
import { apiErrorInfo } from "@/lib/api-errors";
import { formatNumber } from "@/lib/format";
import { useAuth } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import { regenerateRecoveryCodes, refreshMfaStatus, useMfaStatus } from "@/lib/mfa-api";
import { ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { MFA_COPY } from "@/components/mfa/mfa-copy";
import RecoveryCodesView from "@/components/mfa/RecoveryCodesView";

/** Where the MFA pages send the user back to after they finish. */
const RETURN = "/account/security";
const RETURN_QUERY = `?next=${encodeURIComponent(RETURN)}`;

/**
 * The account security page (HOM-241 AC7): the two-step verification status
 * for the signed-in user, with enrolment, re-enrolment and recovery-code
 * actions. The recovery codes of a regeneration live only in component
 * state (AC8). No break-glass UI: the API does not expose it (ADR 0076 D7).
 */
export default function AccountSecurityPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const copy = MFA_COPY[lang].account;
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const status = useMfaStatus();
  const [codes, setCodes] = useState<string[] | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [making, setMaking] = useState(false);
  const [makeError, setMakeError] = useState<unknown>(null);

  async function makeNewCodes() {
    if (making) return;
    setMaking(true);
    setMakeError(null);
    try {
      const { recovery_codes } = await regenerateRecoveryCodes();
      await refreshMfaStatus(queryClient);
      setDialogOpen(false);
      setCodes(recovery_codes);
    } catch (caught) {
      if (apiErrorInfo(caught).code === "mfa_required") {
        setDialogOpen(false);
        navigate(`/mfa/verify${RETURN_QUERY}`, { replace: true });
        return;
      }
      setMakeError(caught);
    } finally {
      setMaking(false);
    }
  }

  if (codes) {
    return (
      <div className="platform-page max-w-2xl">
        <PageHeader
          title={copy.title}
          eyebrow={workspace?.name ?? (lang === "fa" ? "فضای کاری" : "Workspace")}
        />
        {/* Continue goes back to the account page view with the fresh status (AC6). */}
        <RecoveryCodesView codes={codes} onContinue={() => setCodes(null)} />
      </div>
    );
  }

  const data = status.data;

  return (
    <div className="platform-page max-w-2xl">
      <PageHeader
        title={copy.title}
        eyebrow={workspace?.name ?? (lang === "fa" ? "فضای کاری" : "Workspace")}
      />
      <p className="mt-0 mb-4 text-sm text-muted">{copy.intro}</p>

      {status.isPending ? (
        <LoadingState label={copy.loading} />
      ) : status.isError ? (
        <div>
          <ErrorState error={status.error} fallback={copy.errorFallback} />
          <Button variant="secondary" className="mt-3" onPress={() => void status.refetch()}>
            {copy.retry}
          </Button>
        </div>
      ) : data ? (
        <Surface aria-labelledby="two-step-heading" className="rounded-2xl p-5">
          <h2 id="two-step-heading" className="m-0 text-lg font-semibold">{copy.twoStep}</h2>

          <ul className="m-0 mt-3 mb-0 flex list-none flex-col gap-1 p-0 text-sm">
            <li><strong>{data.totp_enrolled ? copy.on : copy.off}</strong></li>
            {data.required && <li>{copy.requiredForRole}</li>}
            {data.totp_enrolled && (
              <li>{copy.codesLeft(formatNumber(data.recovery_codes_remaining, lang))}</li>
            )}
          </ul>

          {data.totp_enrolled && !data.session_verified && (
            <p className="mb-1 mt-3 text-sm" role="status">
              {copy.sessionNotVerified}{" "}
              <Link className={linkVariants().base()} to={`/mfa/verify${RETURN_QUERY}`}>
                {copy.verifyNow}
              </Link>
            </p>
          )}

          <div className="mt-4 flex flex-wrap items-center gap-3">
            {!data.totp_enrolled ? (
              <Link className={linkVariants().base()} to={`/mfa/setup${RETURN_QUERY}`}>
                {copy.setupLink}
              </Link>
            ) : data.session_verified ? (
              <>
                <Button variant="secondary" onPress={() => setDialogOpen(true)}>
                  {MFA_COPY[lang].codes.makeNew}
                </Button>
                <Link className={linkVariants().base()} to={`/mfa/setup${RETURN_QUERY}`}>
                  {copy.newApp}
                </Link>
              </>
            ) : null}
          </div>
        </Surface>
      ) : null}

      <Modal
        isOpen={dialogOpen}
        onOpenChange={(open) => {
          // A close while the POST runs would lose its answer, so the dialog waits.
          if (!open && making) return;
          setDialogOpen(open);
          if (!open) setMakeError(null);
        }}
      >
        <Modal.Backdrop isDismissable={!making}>
          <Modal.Container>
            <Modal.Dialog>
              <Modal.Header>
                <Modal.Heading>{copy.dialogTitle}</Modal.Heading>
              </Modal.Header>
              <Modal.Body>
                <p className="m-0">{copy.dialogBody}</p>
                {makeError !== null && <ErrorState error={makeError} fallback={copy.errorFallback} />}
              </Modal.Body>
              <Modal.Footer>
                <Button variant="ghost" isDisabled={making} onPress={() => setDialogOpen(false)}>
                  {copy.cancel}
                </Button>
                <Button
                  variant="primary"
                  isPending={making}
                  isDisabled={making}
                  onPress={() => void makeNewCodes()}
                >
                  {copy.dialogConfirm}
                </Button>
              </Modal.Footer>
            </Modal.Dialog>
          </Modal.Container>
        </Modal.Backdrop>
      </Modal>
    </div>
  );
}
