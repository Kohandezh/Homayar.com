import { Label, Meter } from "@heroui/react";

import { VOICE_COPY } from "@/components/admin/voice/voice-copy";
import { capacityPercent, isNearLimit } from "@/components/admin/voice/voice-model";
import { formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import type { VoiceWorkspaceCapacity } from "@/types/voice";

/** Calls held against a limit: a meter with the `held / limit` text, warning from 80 percent. */
export function CapacityMeter({ label, held, limit }: { label: string; held: number; limit: number }) {
  const { lang } = useI18n();
  const percent = capacityPercent(held, limit);
  const amount = `${formatNumber(held, lang)} / ${formatNumber(limit, lang)}`;
  return (
    // The amount is the value text: React Aria would read the percent with ASCII digits in Persian.
    <Meter
      value={percent}
      maxValue={100}
      size="sm"
      color={isNearLimit(percent) ? "warning" : "accent"}
      valueLabel={amount}
      className="w-full"
    >
      <Label>{label}</Label>
      <span className="text-sm text-muted tabular-nums">{amount}</span>
      <Meter.Track>
        <Meter.Fill />
      </Meter.Track>
    </Meter>
  );
}

/**
 * The workspace's concurrent calls, shared by the overview card and the voice
 * operations page. A zero limit means voice calls are off, so there is no meter.
 */
export function WorkspaceCapacityMeter({ capacity }: { capacity: VoiceWorkspaceCapacity }) {
  const { lang } = useI18n();
  const copy = VOICE_COPY[lang];
  if (capacity.limit <= 0) return <p className="m-0 text-sm text-muted">{copy.callsOff}</p>;
  return <CapacityMeter label={copy.concurrentCalls} held={capacity.held} limit={capacity.limit} />;
}
