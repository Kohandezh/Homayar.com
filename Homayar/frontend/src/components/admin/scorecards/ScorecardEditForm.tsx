import { useId, useState, type FormEvent } from "react";
import {
  Alert,
  Button,
  Description,
  FieldError,
  Input,
  Label,
  TextArea,
  TextField,
} from "@heroui/react";
import { Plus, Send } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";

import CriterionFields from "@/components/admin/scorecards/CriterionFields";
import ScorecardPreview from "@/components/admin/scorecards/ScorecardPreview";
import {
  REASON_MAX_LENGTH,
  definitionFromDraft,
  draftApplicability,
  draftFromDefinition,
  draftWeight,
  newCriterionDraft,
  newScorecardDraft,
  validateDraft,
  type CriterionDraft,
  type ScorecardDraft,
} from "@/components/admin/scorecards/scorecard-model";
import { SCORECARDS_COPY, isScorecardInvalidCode } from "@/components/admin/scorecards/scorecards-copy";
import { ErrorState } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import {
  getScorecardVersion,
  listScorecardVersions,
  scorecardQueryKeys,
  usePublishScorecardVersion,
  useValidateScorecard,
} from "@/lib/scorecards-api";
import type { ScorecardVersionResponse } from "@/types/scorecards";

/** A 422 from Check or Publish: the code in plain words, plus the server's English detail. */
function RequestError({ error, fallback }: { error: unknown; fallback: string }) {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  const info = apiErrorInfo(error);
  if (info.status !== 422 || !isScorecardInvalidCode(info.code)) {
    return <ErrorState error={error} fallback={fallback} />;
  }
  return (
    <Alert status="danger" role="alert">
      <Alert.Indicator aria-hidden="true" />
      <Alert.Content>
        <Alert.Description>
          <span className="block">{copy.invalid[info.code]}</span>
          {info.message && (
            <span className="mt-1 block text-xs">
              {copy.serverDetail}{" "}
              {/* The message names the field path in English (criteria.1.weight). */}
              <bdi dir="ltr" className="font-mono">
                {info.message}
              </bdi>
            </span>
          )}
        </Alert.Description>
      </Alert.Content>
    </Alert>
  );
}

function conflictVersionOf(error: unknown): number | null {
  const details = apiErrorInfo(error).details;
  if (!details || typeof details !== "object") return null;
  const value = (details as { current_version?: unknown }).current_version;
  return typeof value === "number" && Number.isInteger(value) && value > 0 ? value : null;
}

/**
 * The editor, shown in place on the page. `base` is the latest version the
 * page read when the editor opened; its number is `expected_version` until a
 * 409 reload replaces it. Without `base` this is a new scorecard
 * (`expected_version: 0`). A reload whose read fails changes nothing: the
 * draft, the base version and the conflict alert stay.
 */
export default function ScorecardEditForm({
  base,
  onPublished,
  onCancel,
}: {
  base: ScorecardVersionResponse | null;
  onPublished: (response: ScorecardVersionResponse) => void;
  onCancel: () => void;
}) {
  const { lang } = useI18n();
  const copy = SCORECARDS_COPY[lang];
  const headingId = useId();
  const queryClient = useQueryClient();
  const publish = usePublishScorecardVersion();
  const validate = useValidateScorecard();
  const [draft, setDraft] = useState<ScorecardDraft>(() =>
    base ? draftFromDefinition(base.definition, base.scorecard_key) : newScorecardDraft(),
  );
  const [isNew, setIsNew] = useState(base === null);
  const [baseVersion, setBaseVersion] = useState(base?.version ?? 0);
  const [conflict, setConflict] = useState<{ version: number | null } | null>(null);
  const [reloadError, setReloadError] = useState<unknown>(null);
  const [reloading, setReloading] = useState(false);

  const key = draft.scorecard_key;
  const validation = validateDraft(draft, { isNew });
  const busy = publish.isPending || validate.isPending || reloading;

  function change(update: (running: ScorecardDraft) => ScorecardDraft) {
    setDraft(update);
    // A "valid" answer is about the draft it checked, not the edited one.
    validate.reset();
  }

  function changeCriterion(index: number, patch: Partial<CriterionDraft>) {
    change((running) => ({
      ...running,
      criteria: running.criteria.map((criterion, at) => (at === index ? { ...criterion, ...patch } : criterion)),
    }));
  }

  function runCheck() {
    publish.reset();
    if (!validation.canCheck) return;
    validate.mutate(definitionFromDraft(draft));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    validate.reset();
    if (!validation.canPublish || busy) return;
    publish.mutate(
      {
        key,
        input: {
          definition: definitionFromDraft(draft),
          reason: draft.reason.trim(),
          expected_version: baseVersion,
        },
      },
      {
        onSuccess: onPublished,
        onError: (error) => {
          const info = apiErrorInfo(error);
          if (info.status === 409 && info.code === "scorecard_version_conflict") {
            setConflict({ version: conflictVersionOf(error) });
            setReloadError(null);
          }
        },
      },
    );
  }

  async function loadConflictVersion() {
    setReloading(true);
    setReloadError(null);
    try {
      const target = conflict?.version ?? (await listScorecardVersions(key)).items[0]?.version;
      if (target === undefined) throw new Error("no stored version");
      const fresh = await getScorecardVersion(key, target);
      queryClient.setQueryData(scorecardQueryKeys.version(key, fresh.version), fresh);
      // The list and the history now hold a version this page has not read.
      void queryClient.invalidateQueries({ queryKey: scorecardQueryKeys.list(), exact: true });
      void queryClient.invalidateQueries({ queryKey: scorecardQueryKeys.versions(key), exact: true });
      // The typed reason stays; everything else is the fresh version.
      setDraft((running) => ({ ...draftFromDefinition(fresh.definition, key), reason: running.reason }));
      setBaseVersion(fresh.version);
      setIsNew(false);
      setConflict(null);
      publish.reset();
      validate.reset();
    } catch (error) {
      setReloadError(error);
    } finally {
      setReloading(false);
    }
  }

  const publishError = publish.isError && !conflict ? publish.error : null;
  // Every field error shows at once, so a disabled Publish always has a visible cause.
  const thresholdInvalid = validation.threshold !== null;
  const keyInvalid = validation.key;
  // A blank reason disables Publish; the hint under the field says why.
  // A reason that is not blank but still refused (too long) is the field error.
  const reasonBlank = draft.reason.trim() === "";
  const previewCriteria = draft.criteria.map((criterion) => ({
    id: criterion.uid,
    criterion_id: criterion.criterion_id,
    label: criterion.label,
    weight: draftWeight(criterion),
    applies_to: draftApplicability(criterion),
  }));

  return (
    <form
      className="platform-surface grid gap-5 p-4 sm:p-5"
      aria-labelledby={headingId}
      onSubmit={submit}
      noValidate
    >
      <h2 id={headingId} className="m-0 text-base font-semibold text-text-primary">
        {isNew ? copy.editorNew : copy.editorEdit(formatNumber(baseVersion, lang))}
      </h2>

      <div className="grid gap-4 sm:grid-cols-2">
        {isNew && (
          <TextField
            variant="secondary"
            value={draft.scorecard_key}
            onChange={(value) => change((running) => ({ ...running, scorecard_key: value }))}
            isInvalid={keyInvalid}
            isDisabled={busy}
            isRequired
          >
            <Label>{copy.key}</Label>
            <Input dir="ltr" autoComplete="off" spellCheck={false} maxLength={64} />
            <Description>{copy.keyHint}</Description>
            <FieldError>{copy.keyInvalid}</FieldError>
          </TextField>
        )}
        <TextField
          variant="secondary"
          value={draft.name}
          onChange={(value) => change((running) => ({ ...running, name: value }))}
          isDisabled={busy}
          isRequired
        >
          <Label>{copy.name}</Label>
          <Input dir="auto" autoComplete="off" maxLength={200} />
        </TextField>
        <TextField
          variant="secondary"
          className="sm:col-span-2"
          value={draft.description}
          onChange={(value) => change((running) => ({ ...running, description: value }))}
          isDisabled={busy}
        >
          <Label>{copy.description}</Label>
          <TextArea dir="auto" rows={2} autoComplete="off" maxLength={2000} />
        </TextField>
        <TextField
          variant="secondary"
          value={draft.pass_threshold}
          onChange={(value) => change((running) => ({ ...running, pass_threshold: value }))}
          isInvalid={thresholdInvalid}
          isDisabled={busy}
          isRequired
        >
          <Label>{copy.passThreshold}</Label>
          <Input dir="ltr" inputMode="numeric" autoComplete="off" />
          <Description>{copy.thresholdHint}</Description>
          <FieldError>{copy.thresholdInvalid}</FieldError>
        </TextField>
      </div>

      <div className="grid gap-4">
        {draft.criteria.map((criterion, index) => (
          <CriterionFields
            key={criterion.uid}
            criterion={criterion}
            index={index}
            issues={validation.criteria[index]}
            canRemove={draft.criteria.length > 1}
            isDisabled={busy}
            onChange={(patch) => changeCriterion(index, patch)}
            onRemove={() =>
              change((running) => ({
                ...running,
                criteria: running.criteria.filter((_, at) => at !== index),
              }))
            }
          />
        ))}
        <Button
          variant="secondary"
          size="sm"
          className="justify-self-start"
          isDisabled={busy}
          onPress={() =>
            change((running) => ({ ...running, criteria: [...running.criteria, newCriterionDraft()] }))
          }
        >
          <Plus aria-hidden="true" size={15} />
          {copy.addCriterion}
        </Button>
      </div>

      <div className="grid gap-1">
        <p className="m-0 text-sm font-medium" aria-live="polite">
          {copy.totalWeight(formatNumber(validation.total, lang))}
        </p>
        {validation.totalWrong && <p className="m-0 text-sm text-danger">{copy.weightsMustAdd}</p>}
      </div>

      <ScorecardPreview criteria={previewCriteria} />

      <TextField
        variant="secondary"
        value={draft.reason}
        onChange={(value) => change((running) => ({ ...running, reason: value }))}
        isInvalid={validation.reason && !reasonBlank}
        isDisabled={busy}
        isRequired
      >
        <Label>{copy.reason}</Label>
        <TextArea dir="auto" rows={2} maxLength={REASON_MAX_LENGTH} autoComplete="off" />
        {reasonBlank && <Description>{copy.reasonRequired}</Description>}
        <FieldError>{copy.reasonInvalid}</FieldError>
      </TextField>

      {validate.isSuccess && (
        <p
          role="status"
          className="m-0 rounded-md border border-success/30 bg-success/5 px-3 py-2 text-xs text-success"
        >
          {copy.valid}
        </p>
      )}
      {validate.isError && <RequestError error={validate.error} fallback={copy.checkFailed} />}
      {publishError !== null && <RequestError error={publishError} fallback={copy.publishFailed} />}

      {conflict && (
        <div className="grid gap-3">
          <Alert status="warning" role="alert">
            <Alert.Indicator aria-hidden="true" />
            <Alert.Content>
              <Alert.Description>
                {conflict.version !== null && (
                  <span className="block">{copy.conflict(formatNumber(conflict.version, lang))}</span>
                )}
                <span className="block">{copy.conflictReplace}</span>
              </Alert.Description>
              <Button
                className="mt-2"
                size="sm"
                variant="secondary"
                isDisabled={reloading}
                onPress={() => void loadConflictVersion()}
              >
                {conflict.version !== null
                  ? copy.loadVersion(formatNumber(conflict.version, lang))
                  : copy.viewCurrent}
              </Button>
            </Alert.Content>
          </Alert>
          {reloadError !== null && <ErrorState error={reloadError} fallback={copy.reloadFailed} />}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border-subtle pt-4">
        <Button variant="ghost" onPress={onCancel}>
          {copy.cancel}
        </Button>
        <Button variant="secondary" isDisabled={busy} onPress={runCheck}>
          {copy.check}
        </Button>
        <Button type="submit" variant="primary" isDisabled={!validation.canPublish || busy}>
          <Send aria-hidden="true" size={15} />
          {copy.publish}
        </Button>
      </div>
    </form>
  );
}
