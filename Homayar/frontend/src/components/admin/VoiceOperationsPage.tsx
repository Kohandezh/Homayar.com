import { useState } from "react";
import { Button } from "@heroui/react";

import { DeploymentCapacityMeter, WorkspaceCapacityMeter } from "@/components/admin/voice/CapacityMeter";
import DrainNodeDialog from "@/components/admin/voice/DrainNodeDialog";
import MediaNodeTable from "@/components/admin/voice/MediaNodeTable";
import RecoverDialog from "@/components/admin/voice/RecoverDialog";
import VoiceMetricsPanel from "@/components/admin/voice/VoiceMetricsPanel";
import VoiceSection from "@/components/admin/voice/VoiceSection";
import { VOICE_COPY } from "@/components/admin/voice/voice-copy";
import { EmptyState, ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";
import { useI18n } from "@/lib/i18n-context";
import { useVoiceCapacity } from "@/lib/voice-api";
import type { VoiceMediaNode } from "@/types/voice";

/**
 * Voice operations for platform operators (HOM-194). The route guard mounts
 * this page only for an operator whose workspace has `workspace:manage`, so
 * the console sends no capacity request for anyone else. The server still
 * decides: without `deployment` in its answer there is no node table, no
 * drain button and no recovery section (HOM-242 adds recovery and metrics).
 */
export default function VoiceOperationsPage() {
  const { lang } = useI18n();
  const copy = VOICE_COPY[lang];
  const query = useVoiceCapacity();
  const [drainTarget, setDrainTarget] = useState<VoiceMediaNode | null>(null);
  const [recoverOpen, setRecoverOpen] = useState(false);
  // A failed background refresh keeps the last answer on screen.
  const data = query.data;
  const deployment = data?.deployment;

  return (
    // A div, not a main: the admin layout already provides the landmark.
    <div className="platform-page space-y-5">
      <PageHeader eyebrow={copy.eyebrow} title={copy.pageTitle} />

      {data ? (
        <>
          <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
            <VoiceSection title={copy.workspaceCapacity}>
              <WorkspaceCapacityMeter capacity={data.workspace} />
            </VoiceSection>
            {deployment && (
              <VoiceSection title={copy.deploymentCapacity}>
                <div className="flex flex-col gap-3">
                  <DeploymentCapacityMeter deployment={deployment} />
                  <p className="m-0 text-sm text-muted" dir="auto">
                    {deployment.profile}
                  </p>
                </div>
              </VoiceSection>
            )}
          </div>

          {deployment ? (
            <VoiceSection title={copy.mediaNodes}>
              {deployment.nodes.length === 0 ? (
                <EmptyState label={copy.noNodes} />
              ) : (
                <MediaNodeTable nodes={deployment.nodes} onAction={setDrainTarget} />
              )}
            </VoiceSection>
          ) : (
            <p className="m-0 text-sm text-muted">{copy.operatorsOnly}</p>
          )}

          {deployment && (
            <VoiceSection title={copy.recoveryTitle}>
              <div className="flex flex-col items-start gap-3">
                <p className="m-0 text-sm text-muted">{copy.recoveryText}</p>
                <Button variant="secondary" size="sm" onPress={() => setRecoverOpen(true)}>
                  {copy.recover}
                </Button>
              </div>
            </VoiceSection>
          )}

          {data.metrics && (
            <VoiceSection title={copy.metricsTitle}>
              <VoiceMetricsPanel metrics={data.metrics} />
            </VoiceSection>
          )}
        </>
      ) : query.isError ? (
        <div className="grid gap-3">
          <ErrorState error={query.error} />
          <Button
            variant="secondary"
            size="sm"
            className="justify-self-start"
            isPending={query.isFetching}
            onPress={() => void query.refetch()}
          >
            {copy.tryAgain}
          </Button>
        </div>
      ) : (
        <LoadingState label={copy.loading} />
      )}

      {drainTarget && <DrainNodeDialog node={drainTarget} onClose={() => setDrainTarget(null)} />}
      {recoverOpen && <RecoverDialog onClose={() => setRecoverOpen(false)} />}
    </div>
  );
}
