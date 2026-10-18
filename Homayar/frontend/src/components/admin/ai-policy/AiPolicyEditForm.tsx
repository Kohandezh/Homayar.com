import { useState, type FormEvent } from "react";
import {
  Alert,
  Button,
  Checkbox,
  FieldError,
  Fieldset,
  Input,
  Label,
  Radio,
  RadioGroup,
  Switch,
  TextArea,
  TextField,
} from "@heroui/react";
import { Save } from "lucide-react";

import {
  AI_POLICY_COPY,
  AI_POLICY_STAGES,
  ENDPOINT_KINDS,
  type AiStageName,
} from "@/components/admin/ai-policy/ai-policy-copy";
import {
  REASON_MAX_LENGTH,
  draftFromPolicy,
  policiesEqual,
  policyFromDraft,
  validateDraft,
  type PolicyDraft,
  type StageDraft,
  type StageIssueKind,
} from "@/components/admin/ai-policy/ai-policy-model";
import { ErrorState } from "@/components/platform/Ui";
import { apiErrorInfo } from "@/lib/api-errors";
import { useI18n } from "@/lib/i18n-context";
import { useUpdateAiPolicy } from "@/lib/ai-policy-api";
import type {
  AiCloudTextRule,
  AiPolicyResponse,
  AiPolicyVersionListResponse,
} from "@/types/ai-policy";

type Presets = AiPolicyVersionListResponse["presets"];

/**
 * The owner's inline edit form. The draft starts from the policy the form was
 * opened from, and `baseVersion` keeps that version for `expected_version`:
 * after a 409, "Reload policy" refills the draft from the new current policy
 * (the typed reason stays) so the next save carries the new version. A reload
 * whose read fails changes nothing: the draft, the base version and the
 * conflict alert stay until a reload succeeds.
 */
export default function AiPolicyEditForm({
  current,
  presets,
  onSaved,
  onUnchanged,
  onReload,
  onCancel,
}: {
  current: AiPolicyResponse;
  presets: Presets | null;
  onSaved: (version: number) => void;
  onUnchanged: () => void;
  onReload: () => Promise<{ data: AiPolicyResponse | null; error: unknown }>;
  onCancel: () => void;
}) {
  const { lang } = useI18n();
  const copy = AI_POLICY_COPY[lang];
  const update = useUpdateAiPolicy();
  const [draft, setDraft] = useState<PolicyDraft>(() => draftFromPolicy(current.policy));
  const [baseVersion, setBaseVersion] = useState(current.version);
  const [showErrors, setShowErrors] = useState(false);
  const [conflict, setConflict] = useState(false);
  const [reloadError, setReloadError] = useState<unknown>(null);
  const [reloading, setReloading] = useState(false);

  const validation = validateDraft(draft);
  const draftPolicy = policyFromDraft(draft);
  const unchanged = policiesEqual(draftPolicy, current.policy);
  const canSave = !update.isPending && !unchanged;

  function updateStage<K extends keyof StageDraft>(
    stage: AiStageName,
    key: K,
    value: StageDraft[K],
  ) {
    setDraft((running) => ({ ...running, [stage]: { ...running[stage], [key]: value } }));
  }

  function applyPreset(preset: Presets[keyof Presets]) {
    // A preset replaces the whole document, manifests included; the reason stays.
    setDraft((running) => ({ ...draftFromPolicy(preset), reason: running.reason }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // Errors are only shown once the reader tried to save.
    setShowErrors(true);
    if (!canSave || validation.stages.length > 0 || validation.reason) return;
    update.mutate(
      { policy: draftPolicy, expected_version: baseVersion, reason: draft.reason.trim() },
      {
        onSuccess: (response) => {
          if (response.changed) onSaved(response.current.version);
          else onUnchanged();
        },
        onError: (error) => {
          const info = apiErrorInfo(error);
          if (info.status === 409 && info.code === "policy_version_conflict") setConflict(true);
        },
      },
    );
  }

  async function reloadPolicy() {
    setReloading(true);
    setReloadError(null);
    try {
      const result = await onReload();
      if (result.error !== null || result.data === null) {
        // The read failed; refetch() resolves with the old cache, so only the
        // result's status tells. Everything the reader typed stays as it was.
        if (result.error !== null) setReloadError(result.error);
        return;
      }
      const fresh = result.data;
      setDraft((running) => ({ ...draftFromPolicy(fresh.policy), reason: running.reason }));
      setBaseVersion(fresh.version);
      setConflict(false);
      update.reset();
    } finally {
      setReloading(false);
    }
  }

  const stageIssue = (stage: AiStageName): StageIssueKind | null =>
    validation.stages.find((issue) => issue.stage === stage)?.kind ?? null;

  const putError = update.isError && !conflict ? update.error : null;
  const putInfo = putError ? apiErrorInfo(putError) : null;
  // A 403 about the session or the workspace is not about ownership; those two
  // codes keep the common helper's message.
  const ownerOnly403 =
    putInfo?.status === 403 && putInfo.code !== "csrf_failed" && putInfo.code !== "workspace_access_denied";
  const putInvalid = putInfo?.status === 422;

  return (
    <form className="platform-surface grid gap-5 p-4 sm:p-5" onSubmit={submit} noValidate>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{copy.presets}:</span>
        <div role="group" aria-label={copy.presets} className="flex flex-wrap items-center gap-2">
          {/* Filled from GET /admin/ai-policy/versions, never hard-coded. */}
          <Button
            variant="secondary"
            size="sm"
            isDisabled={!presets || update.isPending}
            onPress={() => presets && applyPreset(presets.local_only)}
          >
            {copy.presetLocalOnly}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            isDisabled={!presets || update.isPending}
            onPress={() => presets && applyPreset(presets.private)}
          >
            {copy.presetPrivate}
          </Button>
        </div>
      </div>

      {conflict && (
        <div className="grid gap-3">
          <Alert status="warning" role="alert">
            <Alert.Indicator aria-hidden="true" />
            <Alert.Content>
              <Alert.Description>{copy.conflict}</Alert.Description>
              <Button
                className="mt-2"
                size="sm"
                variant="secondary"
                isDisabled={reloading}
                onPress={() => void reloadPolicy()}
              >
                {copy.reloadPolicy}
              </Button>
            </Alert.Content>
          </Alert>
          {reloadError !== null && (
            <ErrorState error={reloadError} fallback={copy.reloadFailed} />
          )}
        </div>
      )}

      {putError && (
        <ErrorState
          // The exact texts for 403 and 422; anything else goes through the
          // common helper, so the server's own words never reach the reader.
          error={ownerOnly403 || putInvalid ? null : putError}
          fallback={
            ownerOnly403
              ? copy.putForbidden
              : putInvalid
                ? copy.putInvalid
                : copy.putFailed
          }
        />
      )}

      <div className="grid gap-5 xl:grid-cols-3">
        {AI_POLICY_STAGES.map((stage) => {
          const issue = stageIssue(stage);
          return (
            <Fieldset key={stage} className="min-w-0">
              <Fieldset.Legend>{copy.stages[stage]}</Fieldset.Legend>
              <Fieldset.Group className="grid gap-4">
                <div className="grid gap-2">
                  {ENDPOINT_KINDS.map((kind) => (
                    <Checkbox
                      key={kind}
                      isSelected={draft[stage][kind]}
                      onChange={(selected) => updateStage(stage, kind, selected)}
                    >
                      <Checkbox.Content>
                        <Checkbox.Control>
                          <Checkbox.Indicator />
                        </Checkbox.Control>
                        {copy.kinds[kind]}
                      </Checkbox.Content>
                    </Checkbox>
                  ))}
                </div>
                <TextField
                  variant="secondary"
                  value={draft[stage].regions}
                  onChange={(value) => updateStage(stage, "regions", value)}
                  isInvalid={showErrors && issue !== null}
                >
                  <Label>{copy.regions}</Label>
                  <Input dir="ltr" autoComplete="off" placeholder="eu-west-1, me-central-1" />
                  <FieldError>
                    {issue === "cloud_needs_region" ? copy.cloudNeedsRegion : copy.invalidRegion}
                  </FieldError>
                </TextField>
                <Switch
                  isSelected={draft[stage].fallback}
                  onChange={(selected) => updateStage(stage, "fallback", selected)}
                >
                  <Switch.Content>
                    <Switch.Control>
                      <Switch.Thumb />
                    </Switch.Control>
                    {copy.allowFallback}
                  </Switch.Content>
                </Switch>
                {stage === "intelligence" && (
                  <RadioGroup
                    variant="secondary"
                    value={draft.intelligence.cloud_text}
                    onChange={(value) =>
                      updateStage("intelligence", "cloud_text", value as AiCloudTextRule)
                    }
                  >
                    <Label>{copy.textToCloud}</Label>
                    <Radio value="redacted">
                      <Radio.Content>
                        <Radio.Control>
                          <Radio.Indicator />
                        </Radio.Control>
                        {copy.redactedText}
                      </Radio.Content>
                    </Radio>
                    <Radio value="original">
                      <Radio.Content>
                        <Radio.Control>
                          <Radio.Indicator />
                        </Radio.Control>
                        {copy.originalText}
                      </Radio.Content>
                    </Radio>
                  </RadioGroup>
                )}
              </Fieldset.Group>
            </Fieldset>
          );
        })}
      </div>

      <TextField
        variant="secondary"
        value={draft.reason}
        onChange={(value) => setDraft((running) => ({ ...running, reason: value }))}
        isInvalid={showErrors && validation.reason}
        isRequired
      >
        <Label>{copy.reasonForChange}</Label>
        <TextArea dir="auto" rows={3} maxLength={REASON_MAX_LENGTH} autoComplete="off" />
        <FieldError>{copy.reasonTooShort}</FieldError>
      </TextField>

      <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border-subtle pt-4">
        <Button variant="ghost" onPress={onCancel}>
          {copy.cancel}
        </Button>
        <Button type="submit" variant="primary" isDisabled={!canSave}>
          <Save aria-hidden="true" size={15} />
          {update.isPending ? copy.savingPolicy : copy.savePolicy}
        </Button>
      </div>
    </form>
  );
}
