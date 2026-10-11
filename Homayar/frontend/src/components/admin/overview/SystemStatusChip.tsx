import { Chip } from "@heroui/react";
import { degradedChecks, type ReadinessCheck } from "@/components/admin/overview/overview-model";
import { useI18n } from "@/lib/i18n-context";
import { useReadiness } from "@/lib/platform-api";

const CHECK_LABELS: Record<ReadinessCheck, { fa: string; en: string }> = {
  database: { fa: "پایگاه داده", en: "Database" },
  redis: { fa: "Redis", en: "Redis" },
};

type ChipColor = "default" | "success" | "warning";

/** Server readiness from GET /readyz; a failed check never affects the cards. */
export default function SystemStatusChip() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const query = useReadiness();

  let text: string;
  let color: ChipColor = "default";
  if (query.isError) {
    text = isFa ? "وضعیت سامانه نامشخص است" : "Status unknown";
  } else if (query.isPending || !query.data) {
    text = isFa ? "در حال بررسی وضعیت" : "Checking status";
  } else if (query.data.status === "ready") {
    text = isFa ? "سامانه آماده است" : "System ready";
    color = "success";
  } else {
    // Other check keys have no label: they are shown as sent.
    const labels = degradedChecks(query.data.checks).map(
      (check) => CHECK_LABELS[check as ReadinessCheck]?.[lang] ?? check,
    );
    const degraded = isFa ? "اختلال" : "Degraded";
    text = labels.length ? `${degraded}: ${labels.join(isFa ? "، " : ", ")}` : degraded;
    color = "warning";
  }

  return (
    <Chip color={color} variant="soft" size="md">
      <Chip.Label>{text}</Chip.Label>
    </Chip>
  );
}
