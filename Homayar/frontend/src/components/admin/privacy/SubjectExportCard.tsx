import { useState, type KeyboardEvent } from "react";
import {
  AlertDialog,
  Button,
  Description,
  ErrorMessage,
  FieldError,
  Input,
  Label,
  Tag,
  TagGroup,
  TextField,
  toast,
  type Key,
} from "@heroui/react";
import { Download, Plus } from "lucide-react";

import OverviewCard from "@/components/admin/overview/OverviewCard";
import { PRIVACY_COPY } from "@/components/admin/privacy/privacy-copy";
import { identifierKey, isValidReason, REASON_MAX_LENGTH } from "@/components/admin/privacy/privacy-model";
import { ErrorState } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { useI18n } from "@/lib/i18n-context";
import { saveDownload } from "@/lib/platform-api";
import { exportSubjectData } from "@/lib/privacy-api";

export const MAX_IDENTIFIERS = 10;
export const IDENTIFIER_MAX_LENGTH = 64;

/**
 * The data subject export. The identifiers are personal data, so they live
 * only in this component's state: no query or mutation cache, no storage,
 * no logging. The request is a plain call for the same reason, and the file
 * goes straight to the browser as a download.
 */
export default function SubjectExportCard() {
  const { lang } = useI18n();
  const copy = PRIVACY_COPY[lang];
  const [draft, setDraft] = useState("");
  const [identifiers, setIdentifiers] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const full = identifiers.length >= MAX_IDENTIFIERS;
  const canExport = identifiers.length > 0 && isValidReason(reason) && !pending;
  const invalidIdentifier = error !== null && apiErrorInfo(error).code === "invalid_subject_identifier";

  function addDraft() {
    const value = draft.trim();
    if (full || !value) return;
    if (value.length > IDENTIFIER_MAX_LENGTH) {
      setDraftError(copy.identifierTooLong);
      return;
    }
    setDraftError(null);
    setError(null);
    setDraft("");
    // A duplicate is already in the list (e-mails without case, keeping the
    // first spelling); the field is cleared all the same.
    const key = identifierKey(value);
    setIdentifiers((current) =>
      current.some((existing) => identifierKey(existing) === key) ? current : [...current, value],
    );
  }

  function onDraftKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addDraft();
  }

  function remove(keys: Set<Key>) {
    if (pending) return;
    setError(null);
    setIdentifiers((current) => current.filter((value) => !keys.has(value)));
  }

  async function download() {
    if (!canExport) return;
    setPending(true);
    setError(null);
    try {
      const payload = await exportSubjectData({ identifiers, reason: reason.trim() });
      saveDownload(payload);
      toast.success(copy.exportDone);
      setIdentifiers([]);
      setReason("");
      setDraft("");
    } catch (caught) {
      setError(caught);
    } finally {
      setPending(false);
      setConfirming(false);
    }
  }

  return (
    <OverviewCard title={copy.exportHeading} state="data">
      <div className="grid gap-4">
        <p className="m-0 text-sm text-muted">{copy.exportIntro}</p>

        <div className="flex flex-wrap items-start gap-2">
          <TextField
            value={draft}
            onChange={(value) => {
              setDraft(value);
              setDraftError(null);
            }}
            isInvalid={draftError !== null}
            isReadOnly={pending}
            className="min-w-0 flex-1 sm:max-w-sm"
          >
            <Label>{copy.identifierLabel}</Label>
            {/* E-mails, phone numbers and IBANs read left to right in both languages. */}
            <Input dir="ltr" autoComplete="off" spellCheck={false} onKeyDown={onDraftKeyDown} />
            {draftError ? (
              <FieldError>{draftError}</FieldError>
            ) : (
              full && <Description>{copy.identifierLimit}</Description>
            )}
          </TextField>
          <Button variant="secondary" className="mt-6" isDisabled={full || pending} onPress={addDraft}>
            <Plus aria-hidden="true" size={15} />
            {copy.add}
          </Button>
        </div>

        {identifiers.length > 0 && (
          <TagGroup onRemove={remove}>
            <Label>{copy.identifiersLabel}</Label>
            <TagGroup.List>
              {identifiers.map((value) => (
                <Tag key={value} id={value} textValue={value}>
                  <bdi dir="ltr">{value}</bdi>
                </Tag>
              ))}
            </TagGroup.List>
            {invalidIdentifier && <ErrorMessage>{copy.invalidIdentifier}</ErrorMessage>}
          </TagGroup>
        )}

        <TextField
          value={reason}
          onChange={setReason}
          maxLength={REASON_MAX_LENGTH}
          isReadOnly={pending}
          className="sm:max-w-lg"
        >
          <Label>{copy.reasonLabel}</Label>
          <Input dir="auto" />
          <Description>{copy.reasonHint}</Description>
        </TextField>

        {error !== null && !invalidIdentifier && <ErrorState error={error} fallback={copy.exportFailed} />}

        <Button
          variant="primary"
          className="justify-self-start"
          isDisabled={!canExport}
          onPress={() => setConfirming(true)}
        >
          <Download aria-hidden="true" size={15} />
          {copy.exportData}
        </Button>
      </div>

      {/* The documented controlled form: open state on the backdrop, no
          AlertDialog root (a DialogTrigger that warns without a pressable child). */}
      <AlertDialog.Backdrop
        isOpen={confirming}
        onOpenChange={(open) => {
          if (!pending && !open) setConfirming(false);
        }}
      >
        <AlertDialog.Container>
          <AlertDialog.Dialog>
            <AlertDialog.Header>
              <AlertDialog.Icon status="warning" />
              <AlertDialog.Heading>{copy.exportWarningTitle}</AlertDialog.Heading>
            </AlertDialog.Header>
            <AlertDialog.Body>
              <p className="m-0 text-sm text-muted">{copy.exportWarningBody}</p>
            </AlertDialog.Body>
            <AlertDialog.Footer>
              <Button variant="ghost" isDisabled={pending} onPress={() => setConfirming(false)}>
                {copy.cancel}
              </Button>
              <Button variant="primary" isDisabled={pending} onPress={() => void download()}>
                <Download aria-hidden="true" size={15} />
                {pending ? copy.exporting : copy.downloadExport}
              </Button>
            </AlertDialog.Footer>
          </AlertDialog.Dialog>
        </AlertDialog.Container>
      </AlertDialog.Backdrop>
    </OverviewCard>
  );
}
