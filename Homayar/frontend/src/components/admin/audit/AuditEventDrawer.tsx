import { useEffect, useState, type ReactNode } from "react";
import { Button, Drawer } from "@heroui/react";
import { Check, Clipboard } from "lucide-react";

import {
  auditDetailKeyLabel,
  auditDetailValue,
  auditEventLabel,
  auditTargetLabel,
  formatAuditDetail,
} from "@/lib/audit-labels";
import { formatDate } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { StatusBadge } from "@/components/platform/Ui";
import type { AuditEvent } from "@/types/platform";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 border-b border-separator py-2 last:border-b-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="m-0 text-sm break-words">{children}</dd>
    </div>
  );
}

/** How long the copy confirmation stays on the button. */
const COPIED_MS = 2000;

function CopyRequestId({ requestId }: { requestId: string }) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), COPIED_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <span className="flex flex-wrap items-center gap-2">
      <code dir="ltr" className="text-xs">{requestId}</code>
      <Button
        size="sm"
        variant="ghost"
        aria-label={isFa ? "کپی شناسهٔ درخواست" : "Copy request id"}
        onPress={() => {
          // Clipboard access can be refused (permission, insecure origin); the
          // id stays selectable next to the button either way.
          void Promise.resolve(navigator.clipboard?.writeText(requestId))
            .then(() => setCopied(true))
            .catch(() => undefined);
        }}
      >
        {copied ? <Check size={14} aria-hidden="true" /> : <Clipboard size={14} aria-hidden="true" />}
        {copied ? (isFa ? "کپی شد" : "Copied") : (isFa ? "کپی" : "Copy")}
      </Button>
    </span>
  );
}

/**
 * Every field of one audit event, including the sanitized details as key and
 * value pairs. The raw event type stays visible next to its label, because an
 * unknown type is never guessed.
 */
export default function AuditEventDrawer({
  event,
  actorName,
  onClose,
}: {
  event: AuditEvent | null;
  actorName: string;
  onClose: () => void;
}) {
  const { lang, dir } = useI18n();
  const isFa = lang === "fa";
  const details = event ? Object.entries(event.details) : [];

  return (
    <Drawer.Backdrop isOpen={event !== null} onOpenChange={(open) => !open && onClose()}>
      <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
        <Drawer.Dialog
          aria-label={isFa ? "جزئیات رویداد" : "Event details"}
          className="w-96 max-w-[90vw] p-4"
        >
          <Drawer.CloseTrigger aria-label={isFa ? "بستن جزئیات" : "Close details"} />
          {event && (
            <>
              <Drawer.Header>
                <Drawer.Heading>
                  {auditEventLabel(event.event_type, lang) ?? event.event_type}
                </Drawer.Heading>
              </Drawer.Header>
              <Drawer.Body>
                <dl className="m-0 grid">
                  <Field label={isFa ? "نوع رویداد" : "Event type"}>
                    <code dir="ltr" className="text-xs">{event.event_type}</code>
                  </Field>
                  <Field label={isFa ? "زمان" : "Time"}>
                    <time dateTime={event.occurred_at}>{formatDate(event.occurred_at, lang)}</time>
                  </Field>
                  <Field label={isFa ? "نتیجه" : "Outcome"}>
                    <StatusBadge status={event.outcome} />
                  </Field>
                  <Field label={isFa ? "عامل" : "Actor"}>
                    <bdi dir="auto">{actorName}</bdi>
                  </Field>
                  <Field label={isFa ? "هدف" : "Target"}>
                    {event.target_type ? (
                      <>
                        {auditTargetLabel(event.target_type, lang)}
                        {event.target_id && (
                          <>
                            {" · "}
                            <bdi dir="ltr" className="font-mono text-xs">{event.target_id}</bdi>
                          </>
                        )}
                      </>
                    ) : (
                      isFa ? "بدون هدف" : "None"
                    )}
                  </Field>
                  <Field label={isFa ? "شناسهٔ رویداد" : "Event id"}>
                    <code dir="ltr" className="text-xs">{event.id}</code>
                  </Field>
                  <Field label={isFa ? "شناسهٔ درخواست" : "Request id"}>
                    {event.request_id
                      ? <CopyRequestId requestId={event.request_id} />
                      : (isFa ? "ثبت نشده" : "Not recorded")}
                  </Field>
                  {details.map(([key, value]) => (
                    <Field key={key} label={auditDetailKeyLabel(key, lang)}>
                      <bdi dir="auto">{auditDetailValue(key, formatAuditDetail(value), lang)}</bdi>
                    </Field>
                  ))}
                </dl>
              </Drawer.Body>
            </>
          )}
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  );
}
