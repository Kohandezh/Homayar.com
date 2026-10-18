import { useMemo, useState } from "react";
import { Button, Chip } from "@heroui/react";
import { PencilLine } from "lucide-react";

import AiPolicyEditForm from "@/components/admin/ai-policy/AiPolicyEditForm";
import AiPolicyHistory from "@/components/admin/ai-policy/AiPolicyHistory";
import AiPolicyStageSection from "@/components/admin/ai-policy/AiPolicyStageSection";
import { AI_POLICY_COPY } from "@/components/admin/ai-policy/ai-policy-copy";
import { ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useAiPolicy, useAiPolicyVersions } from "@/lib/ai-policy-api";
import { useMembers } from "@/lib/platform-api";
import { useCan } from "@/lib/use-can";
import type { AiPolicyResponse } from "@/types/ai-policy";

export default function AiPolicyPage() {
  const { lang } = useI18n();
  const copy = AI_POLICY_COPY[lang];
  const isFa = lang === "fa";
  const canEdit = useCan().has("ai_policy:manage");
  const policyQuery = useAiPolicy();
  const versionsQuery = useAiPolicyVersions();
  const membersQuery = useMembers();
  const [editing, setEditing] = useState(false);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [unchangedNotice, setUnchangedNotice] = useState(false);

  const current = policyQuery.data ?? null;
  const presets = versionsQuery.data?.presets ?? null;
  // A failed members read leaves the names unknown; the history shows the dash.
  const memberNames = useMemo(
    () =>
      new Map(
        (membersQuery.data?.items ?? [])
          .filter((member) => member.display_name)
          .map((member) => [member.user_id, member.display_name]),
      ),
    [membersQuery.data],
  );

  /**
   * Refetches both reads for the 409 reload. A failed refetch resolves with
   * the old cache, so the result's status decides: the form keeps its draft
   * unless the read really succeeded.
   */
  async function reloadPolicy(): Promise<{ data: AiPolicyResponse | null; error: unknown }> {
    const [policyResult] = await Promise.all([policyQuery.refetch(), versionsQuery.refetch()]);
    if (policyResult.isError) return { data: null, error: policyResult.error };
    return { data: policyResult.data ?? null, error: null };
  }

  return (
    <div className="platform-page">
      <PageHeader
        eyebrow={isFa ? "مدیریت" : "Administration"}
        title={copy.heading}
        actions={current && (
          <>
            <Chip
              variant="soft"
              size="sm"
              color={current.source === "default" ? "default" : "accent"}
            >
              <Chip.Label>
                {current.source === "default"
                  ? copy.badgeDefault
                  : copy.badgeCustom(formatNumber(current.version, lang))}
              </Chip.Label>
            </Chip>
            {canEdit ? (
              !editing && (
                <Button
                  variant="secondary"
                  size="sm"
                  onPress={() => {
                    setSavedVersion(null);
                    setUnchangedNotice(false);
                    setEditing(true);
                  }}
                >
                  <PencilLine aria-hidden="true" size={15} />
                  {copy.editPolicy}
                </Button>
              )
            ) : (
              <span className="text-xs text-muted">{copy.ownerHint}</span>
            )}
          </>
        )}
      />
      <p className="mt-0 mb-4 text-sm text-muted">{copy.intro}</p>

      {policyQuery.isPending ? (
        <LoadingState label={copy.loading} />
      ) : policyQuery.isError && !current ? (
        // Only a read with no data at all swaps the page for the error state:
        // a failed refetch keeps the cached policy (and an open edit form).
        <div className="grid gap-3">
          <ErrorState error={policyQuery.error} fallback={copy.loadError} />
          <Button
            variant="secondary"
            size="sm"
            className="justify-self-start"
            onPress={() => void policyQuery.refetch()}
          >
            {copy.tryAgain}
          </Button>
        </div>
      ) : current ? (
        <>
          {current.source === "workspace" && (
            <p className="mt-0 mb-4 text-sm text-muted">
              {/* Free text by a person, in any language: a bare bdi lets the
                  browser pick the direction from the text itself. */}
              {copy.reasonLabel}: <bdi>{current.reason}</bdi>
              {" · "}
              {copy.changedLabel}:{" "}
              {current.created_at && (
                <time dateTime={current.created_at}>{formatDate(current.created_at, lang)}</time>
              )}
            </p>
          )}

          {savedVersion !== null && (
            <p
              role="status"
              className="mb-4 rounded-md border border-success/30 bg-success/5 px-3 py-2 text-xs text-success"
            >
              {copy.savedVersion(formatNumber(savedVersion, lang))}
            </p>
          )}

          {canEdit && editing && (
            <div className="mb-4 grid gap-3">
              {unchangedNotice && (
                <p
                  role="status"
                  className="m-0 rounded-md border border-accent/30 bg-accent/5 px-3 py-2 text-xs"
                >
                  {copy.nothingChanged}
                </p>
              )}
              <AiPolicyEditForm
                current={current}
                presets={presets}
                onSaved={(version) => {
                  setSavedVersion(version);
                  setUnchangedNotice(false);
                  setEditing(false);
                }}
                onUnchanged={() => setUnchangedNotice(true)}
                onReload={reloadPolicy}
                onCancel={() => setEditing(false)}
              />
            </div>
          )}

          <div className="grid gap-4 lg:grid-cols-3">
            <AiPolicyStageSection stage="asr" policy={current.policy.asr} />
            <AiPolicyStageSection stage="diarization" policy={current.policy.diarization} />
            <AiPolicyStageSection stage="intelligence" policy={current.policy.intelligence} />
          </div>

          <AiPolicyHistory
            current={current.policy}
            items={versionsQuery.data?.items ?? []}
            memberNames={memberNames}
            isLoading={versionsQuery.isPending}
            // Same rule as the policy read: a failed refetch with cached data
            // keeps the table; only a read with no data at all shows the error.
            error={versionsQuery.isError && !versionsQuery.data ? versionsQuery.error : null}
            onRetry={() => void versionsQuery.refetch()}
          />
        </>
      ) : null}
    </div>
  );
}
