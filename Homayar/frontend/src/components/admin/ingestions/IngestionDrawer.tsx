import type { ReactNode } from "react";
import { Button, Drawer } from "@heroui/react";
import { Link } from "react-router";

import { ErrorState, LoadingState } from "@/components/platform/Ui";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { formatBytes, formatDate, formatNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import { useIngestion } from "@/lib/ingestions-api";
import {
  ingestionReasonLabel,
  ingestionSourceLabel,
  ingestionStatusLabel,
} from "@/lib/ingestion-labels";
import type { IngestionRecord } from "@/types/ingestions";

/** Error codes of GET /ingestions/{id} (backend/routers/v1/ingestions.py). */
const INGESTION_ERROR_CODES: ErrorCodeMessages = {
  ingestion_not_found: {
    fa: "این ورودی پیدا نشد یا برای شما قابل مشاهده نیست.",
    en: "This ingestion was not found or is not visible to you.",
  },
};

/** One labelled row of the drawer's `<dl>`: the label above, the value below. */
function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-0.5 border-b border-separator py-2 last:border-b-0">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="m-0 text-sm break-words">{children}</dd>
    </div>
  );
}

/** A value the record does not carry. */
function EmptyValue() {
  return <span className="text-muted">—</span>;
}

/**
 * Everything known about one ingestion, read from GET /ingestions/{id} at
 * the moment the drawer opens; the "Open the earlier ingestion" action of a
 * duplicate row switches the same drawer to that record.
 */
export default function IngestionDrawer({
  ingestionId,
  onClose,
  onOpenId,
}: {
  ingestionId: string | null;
  onClose: () => void;
  onOpenId: (id: string) => void;
}) {
  const { lang, dir } = useI18n();
  const isFa = lang === "fa";
  const query = useIngestion(ingestionId);
  const record = query.data ?? null;

  return (
    <Drawer.Backdrop
      isOpen={ingestionId !== null}
      onOpenChange={(open) => {
        if (open) return;
        onClose();
      }}
    >
      <Drawer.Content placement={dir === "rtl" ? "left" : "right"} className="justify-end">
        <Drawer.Dialog
          aria-label={isFa ? "جزئیات ورودی" : "Ingestion details"}
          className="w-[28rem] max-w-[90vw] p-4"
        >
          <Drawer.CloseTrigger aria-label={isFa ? "بستن جزئیات" : "Close details"} />
          {ingestionId !== null && (
            <>
              <Drawer.Header>
                <Drawer.Heading>
                  {/* User content: no forced direction, the browser reads the
                      name's own order; without a name the source stands in. */}
                  <bdi>
                    {record
                      ? (record.original_filename ?? ingestionSourceLabel(record.source_type, lang))
                      : (isFa ? "ورودی" : "Ingestion")}
                  </bdi>
                </Drawer.Heading>
              </Drawer.Header>
              <Drawer.Body>
                {query.isPending ? (
                  <LoadingState
                    label={isFa ? "در حال دریافت جزئیات ورودی" : "Loading the ingestion"}
                  />
                ) : query.isError ? (
                  <ErrorState
                    error={query.error}
                    codes={INGESTION_ERROR_CODES}
                    fallback={
                      isFa ? "دریافت جزئیات ورودی ناموفق بود." : "Could not load the ingestion."
                    }
                  />
                ) : record ? (
                  <IngestionFields record={record} onOpenId={onOpenId} />
                ) : null}
              </Drawer.Body>
            </>
          )}
        </Drawer.Dialog>
      </Drawer.Content>
    </Drawer.Backdrop>
  );
}

function IngestionFields({
  record,
  onOpenId,
}: {
  record: IngestionRecord;
  onOpenId: (id: string) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const duplicateOf = record.duplicate_of_id;
  const meetingId = record.meeting_id;
  const count = (value: number) => formatNumber(value, lang);
  return (
    <dl className="m-0 grid">
      <Field label={isFa ? "وضعیت" : "State"}>
        <span className="grid gap-0.5">
          <span>{ingestionStatusLabel(record.status, lang)}</span>
          {/* The reason rides with the record, whatever its status says, so
              the list and the drawer never disagree about it. */}
          {record.reason && (
            <span className="grid gap-0.5">
              <span>{ingestionReasonLabel(record.reason, lang)}</span>
              {/* The raw code, so support can quote exactly what the server said. */}
              <bdi dir="ltr" className="font-mono text-xs">
                {record.reason}
              </bdi>
            </span>
          )}
        </span>
      </Field>
      <Field label={isFa ? "منبع" : "Source"}>
        <span className="grid gap-0.5">
          <span>{ingestionSourceLabel(record.source_type, lang)}</span>
          <bdi dir="ltr">{record.source_provider}</bdi>
        </span>
      </Field>
      <Field label={isFa ? "فایل" : "File"}>
        {record.original_filename ? <bdi>{record.original_filename}</bdi> : <EmptyValue />}
      </Field>
      <Field label={isFa ? "نوع محتوا" : "Content type"}>
        {record.declared_content_type ? (
          <bdi dir="ltr">{record.declared_content_type}</bdi>
        ) : (
          <EmptyValue />
        )}
      </Field>
      <Field label={isFa ? "حجم" : "Size"}>
        {record.media_size_bytes !== null ? (
          formatBytes(record.media_size_bytes, lang)
        ) : (
          <EmptyValue />
        )}
      </Field>
      <Field label={isFa ? "دریافت" : "Received"}>
        <time dateTime={record.received_at}>{formatDate(record.received_at, lang)}</time>
      </Field>
      <Field label={isFa ? "ثبت" : "Created"}>
        <time dateTime={record.created_at}>{formatDate(record.created_at, lang)}</time>
      </Field>
      <Field label={isFa ? "شناسهٔ رویداد" : "Event ID"}>
        <bdi dir="ltr" className="font-mono text-xs">
          {record.external_event_id}
        </bdi>
      </Field>
      {duplicateOf && (
        <Field label={isFa ? "تکراری" : "Duplicate"}>
          <span className="grid gap-1">
            <span>
              {isFa ? "همان فایلِ یک ورودی قبلی." : "Same file as an earlier ingestion."}
            </span>
            <Button
              variant="secondary"
              size="sm"
              className="justify-self-start"
              onPress={() => onOpenId(duplicateOf)}
            >
              {isFa ? "باز کردن ورودی قبلی" : "Open the earlier ingestion"}
            </Button>
          </span>
        </Field>
      )}
      {record.conflict_count > 0 && (
        <Field label={isFa ? "تعارض" : "Conflicts"}>
          <span className="grid gap-0.5">
            <span>
              {isFa
                ? `${count(record.conflict_count)} بار با محتوای متفاوت دوباره ارسال شد`
                : `Sent again with different content: ${count(record.conflict_count)} times`}
            </span>
            {record.last_conflict_at && (
              <time dateTime={record.last_conflict_at} className="text-xs text-muted">
                {formatDate(record.last_conflict_at, lang)}
              </time>
            )}
          </span>
        </Field>
      )}
      {meetingId && (
        <Field label={isFa ? "جلسه" : "Meeting"}>
          <Link to={`/meetings/${meetingId}`}>{isFa ? "باز کردن جلسه" : "Open meeting"}</Link>
        </Field>
      )}
    </dl>
  );
}
