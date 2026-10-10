import type { ReactNode } from "react";
import { Alert, Chip, Spinner } from "@heroui/react";
import { Inbox } from "lucide-react";
import { describeApiError, type ErrorCodeMessages } from "@/lib/error-messages";
import { useI18n } from "@/lib/i18n-context";
import { statusLabel } from "@/lib/status-labels";

type ChipColor = "default" | "accent" | "success" | "warning" | "danger";

// Same groups as the `.platform-status-*` colors in index.css.
const STATUS_COLORS: Record<string, ChipColor> = {
  completed: "success",
  active: "success",
  accepted: "success",
  success: "success",
  done: "success",
  failed: "danger",
  failure: "danger",
  "dead-letter": "danger",
  cancelled: "danger",
  queued: "warning",
  uploading: "warning",
  trialing: "warning",
  pending: "warning",
  partial: "warning",
  "insufficient-evidence": "warning",
  processing: "accent",
  transcribing: "accent",
  diarizing: "accent",
  analyzing: "accent",
};

export function PageHeader({
  title,
  eyebrow,
  actions,
}: {
  title: string;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="platform-page-header">
      <div>
        {eyebrow && <p>{eyebrow}</p>}
        <h1>{title}</h1>
      </div>
      {actions && <div className="platform-page-actions">{actions}</div>}
    </header>
  );
}

export function LoadingState({ label = "Loading" }: { label?: string }) {
  return (
    <div className="platform-state" role="status">
      {/* HeroUI's Spinner has its own role="status" and English label; the wrapper must stay the only one. */}
      <Spinner size="sm" color="current" role={undefined} aria-label={undefined} aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function EmptyState({ label }: { label: string }) {
  return (
    <div className="platform-state platform-empty-state">
      <Inbox size={22} />
      <span>{label}</span>
    </div>
  );
}

/**
 * A failed request, described by its API error code (never by the server's
 * English message): the page's `codes`, then the common codes, then a
 * generic text by kind, then `fallback` with the request id.
 */
export function ErrorState({
  error,
  fallback,
  codes,
}: {
  error: unknown;
  fallback?: string;
  codes?: ErrorCodeMessages;
}) {
  const { lang } = useI18n();
  return (
    <Alert status="danger" className="platform-state platform-error-state" role="alert">
      <Alert.Indicator aria-hidden="true" />
      <Alert.Content>
        <Alert.Description>{describeApiError(error, { lang, fallback, codes }).text}</Alert.Description>
      </Alert.Content>
    </Alert>
  );
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const { lang } = useI18n();
  const normalized = status.toLowerCase().replaceAll("_", "-");
  // The label holds the text, so it needs the status class too (`getByText` finds the label).
  return (
    <Chip
      color={STATUS_COLORS[normalized] ?? "default"}
      variant="soft"
      size="sm"
      className={`platform-status platform-status-${normalized}`}
    >
      <Chip.Label className={`platform-status-${normalized}`}>{label ?? statusLabel(status, lang)}</Chip.Label>
    </Chip>
  );
}
