import { Chip } from "@heroui/react";

import { SYSTEM_HEALTH_COPY } from "@/components/admin/system-health/system-health-copy";
import {
  STATUS_PRESENTATIONS,
  needsRunbook,
  presentStatus,
  runbookHref,
  type RunbookSource,
} from "@/components/admin/system-health/system-health-model";
import { isolateLtr } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";

/**
 * One status value as a HeroUI Chip (P4, P4a): the tone comes from the
 * status table, the label is the localized text. A status string the table
 * does not know is shown as the raw string, LTR-isolated, on the neutral
 * tone (P5). Every warning or danger status carries its runbook link, which
 * opens the operations docs in a new tab (P7).
 */
export default function StatusChip({ status, source }: { status: string; source: RunbookSource }) {
  const { lang } = useI18n();
  const copy = SYSTEM_HEALTH_COPY[lang];
  const presentation = presentStatus(status);
  // A status the table owns gets its localized label; anything else,
  // including inherited object keys, is the raw string (P5).
  const label = Object.hasOwn(STATUS_PRESENTATIONS, status) ? presentation[lang] : isolateLtr(status);
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Chip color={presentation.tone} variant="soft" size="sm">
        <Chip.Label>{label}</Chip.Label>
      </Chip>
      {needsRunbook(presentation.tone) && (
        <a
          href={runbookHref(source)}
          target="_blank"
          rel="noreferrer"
          className="text-sm text-accent underline underline-offset-2"
        >
          {copy.runbook}
        </a>
      )}
    </span>
  );
}
