import type { ReactNode } from "react";
import { AlertCircle, Inbox, LoaderCircle } from "lucide-react";
import { describeApiError, type ErrorCodeMessages } from "@/lib/error-messages";
import { useI18n } from "@/lib/i18n-context";
import { statusLabel } from "@/lib/status-labels";

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
      <LoaderCircle size={20} className="animate-spin" />
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
    <div className="platform-state platform-error-state" role="alert">
      <AlertCircle size={20} aria-hidden="true" />
      <span>{describeApiError(error, { lang, fallback, codes }).text}</span>
    </div>
  );
}

export function StatusBadge({ status, label }: { status: string; label?: string }) {
  const { lang } = useI18n();
  const normalized = status.toLowerCase().replaceAll("_", "-");
  return <span className={`platform-status platform-status-${normalized}`}>{label ?? statusLabel(status, lang)}</span>;
}
