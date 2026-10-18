import OverviewCard from "@/components/admin/overview/OverviewCard";
import { queryCardState } from "@/components/admin/overview/card-state";
import { WorkspaceCapacityMeter } from "@/components/admin/voice/CapacityMeter";
import { VOICE_COPY } from "@/components/admin/voice/voice-copy";
import { useI18n } from "@/lib/i18n-context";
import { useVoiceCapacity } from "@/lib/voice-api";

/**
 * The workspace's voice calls against its limit (HOM-194). Every admin who
 * can open /admin sees it, platform operator or not; it reads the capacity
 * again every 15 seconds.
 */
export default function VoiceCapacityCard() {
  const { lang } = useI18n();
  const query = useVoiceCapacity();
  // The answer always has the workspace part, so the card is never empty.
  const state = queryCardState(query, () => false);

  return (
    <OverviewCard title={VOICE_COPY[lang].cardTitle} state={state} error={query.error}>
      {query.data && <WorkspaceCapacityMeter capacity={query.data.workspace} />}
    </OverviewCard>
  );
}
