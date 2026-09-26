import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  ChevronLeft,
  ChevronRight,
  History,
  LockKeyhole,
  Save,
  Settings,
  SlidersHorizontal,
} from "lucide-react";

import {
  AUDIT_EVENT_LABELS,
  auditDetailKeyLabel,
  auditDetailValue,
  auditEventLabel,
  auditTargetLabel,
} from "@/lib/audit-labels";
import { useAuth } from "@/lib/auth-context";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { formatDate, formatNumber, localizeDigits, parseWholeNumber } from "@/lib/format";
import { useI18n } from "@/lib/i18n-context";
import {
  useAuditEvents,
  useMembers,
  useUpdateWorkspaceSettings,
  useWorkspaceSettings,
} from "@/lib/platform-api";
import { apiErrorInfo } from "@/lib/api-errors";
import type {
  AuditEventListParams,
  JsonValue,
  RetentionMode,
  UpdateWorkspaceSettingsInput,
  WorkspaceSettings as WorkspaceSettingsType,
} from "@/types/platform";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "@/components/platform/Ui";

type ChosenRetentionMode = Exclude<RetentionMode, "legacy_unresolved">;

/** Error codes of backend/routers/v1/admin.py. */
const SETTINGS_ERROR_CODES: ErrorCodeMessages = {
  invalid_workspace_settings: {
    fa: "مقدار واردشده پذیرفته نشد؛ فیلدها را بررسی کنید.",
    en: "The values were not accepted; check the fields.",
  },
  workspace_not_found: {
    fa: "این فضای کاری در دسترس نیست.",
    en: "This workspace is not available.",
  },
  invalid_cursor: {
    fa: "فهرست رویدادها تغییر کرده است؛ از صفحهٔ اول دوباره ببینید.",
    en: "The event list has changed; start again from the first page.",
  },
  invalid_audit_range: {
    fa: "بازهٔ زمانی رویدادها معتبر نیست؛ از صفحهٔ اول دوباره ببینید.",
    en: "The event range is not valid; start again from the first page.",
  },
};

function WorkspaceSettingsForm({
  settings,
  isPending,
  onSave,
}: {
  settings: WorkspaceSettingsType;
  isPending: boolean;
  onSave: (input: UpdateWorkspaceSettingsInput) => void;
}) {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [name, setName] = useState(settings.name);
  const legacyUnresolved = settings.retention_mode === "legacy_unresolved";
  // The legacy sentinel is never preselected as something else: until the
  // administrator picks a mode, no retention change is sent (a rename alone
  // must not start age-based deletion).
  const [retentionMode, setRetentionMode] = useState<ChosenRetentionMode | "">(
    legacyUnresolved ? "" : (settings.retention_mode as ChosenRetentionMode),
  );
  // Blank stays blank and zero stays zero: neither is turned into a number
  // the server would accept; both keep the save disabled.
  const [retentionDays, setRetentionDays] = useState(
    settings.retention_days > 0 ? localizeDigits(String(settings.retention_days), lang) : "",
  );
  const [deleteAudio, setDeleteAudio] = useState(settings.delete_audio_after_processing);
  const parsedDays = parseWholeNumber(retentionDays);
  const finite = retentionMode === "finite";
  const retentionIsValid = !finite || (parsedDays !== null && parsedDays >= 1 && parsedDays <= 3650);
  const modeChanged = retentionMode !== "" && (legacyUnresolved || retentionMode !== settings.retention_mode);
  const daysChanged = finite && parsedDays !== settings.retention_days;
  const nameChanged = name.trim() !== settings.name;
  const audioPolicyChanged = deleteAudio !== settings.delete_audio_after_processing;
  const isDirty = nameChanged || modeChanged || daysChanged || audioPolicyChanged;
  const canSave = !isPending && isDirty && Boolean(name.trim()) && retentionIsValid;

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canSave) return;
    const input: Partial<UpdateWorkspaceSettingsInput> = {};
    if (nameChanged) input.name = name.trim();
    if (modeChanged && retentionMode) input.retention_mode = retentionMode;
    if (finite && (modeChanged || daysChanged) && parsedDays !== null) input.retention_days = parsedDays;
    if (audioPolicyChanged) input.delete_audio_after_processing = deleteAudio;
    onSave(input as UpdateWorkspaceSettingsInput);
  }

  return (
    <form className="platform-settings-form grid gap-5" onSubmit={submit} noValidate>
      <div className="platform-field">
        <label htmlFor="workspace-name">{isFa ? "نام فضای کاری" : "Workspace name"}</label>
        <input
          id="workspace-name"
          className="platform-input"
          dir="auto"
          value={name}
          onChange={(event) => setName(event.currentTarget.value)}
          maxLength={180}
          autoComplete="organization"
          aria-invalid={!name.trim()}
          required
          readOnly={isPending}
        />
      </div>

      <div className="platform-field">
        <label htmlFor="retention-mode">{isFa ? "سیاست نگهداری داده" : "Data retention policy"}</label>
        <select
          id="retention-mode"
          className="platform-input"
          value={retentionMode}
          onChange={(event) => {
            if (!isPending) setRetentionMode(event.currentTarget.value as ChosenRetentionMode | "");
          }}
          aria-describedby={legacyUnresolved ? "retention-legacy retention-help" : "retention-help"}
          aria-disabled={isPending}
        >
          {legacyUnresolved && (
            <option value="">{isFa ? "انتخاب نشده (بدون تغییر)" : "Not chosen (no change)"}</option>
          )}
          <option value="finite">{isFa ? "محدود: حذف پس از تعداد روز مشخص" : "Finite: delete after a set number of days"}</option>
          <option value="indefinite">{isFa ? "نامحدود: فقط حذف دستی" : "Indefinite: only explicit deletion"}</option>
          <option value="immediate">{isFa ? "فوری: حذف پس از پایان پردازش" : "Immediate: delete once processing finishes"}</option>
        </select>
        {legacyUnresolved && (
          <p id="retention-legacy" className="platform-form-error" role="alert">
            {isFa
              ? "مقدار قدیمی «۰ روز» مبهم است و تا انتخاب یک سیاست صریح هیچ داده‌ای بر اساس زمان حذف نمی‌شود."
              : "The old \"0 days\" value is ambiguous; nothing is deleted by age until you choose an explicit policy."}
          </p>
        )}
        <p id="retention-help" className="m-0 text-xs text-text-muted">
          {isFa
            ? "حذف بر اساس زمان فقط با سیاست محدود یا فوری انجام می‌شود؛ حذف دستی همیشه در دسترس است."
            : "Age-based deletion only happens under the finite or immediate policy; explicit deletion is always available."}
        </p>
      </div>

      {finite && (
        <div className="platform-field">
          <label htmlFor="retention-days">{isFa ? "مدت نگهداری (روز)" : "Retention period (days)"}</label>
          <input
            id="retention-days"
            className="platform-input"
            type="text"
            inputMode="numeric"
            autoComplete="off"
            value={retentionDays}
            onChange={(event) => setRetentionDays(event.currentTarget.value)}
            aria-describedby={retentionIsValid ? "retention-days-help" : "retention-days-help retention-days-error"}
            aria-invalid={!retentionIsValid}
            required
            readOnly={isPending}
          />
          <p id="retention-days-help" className="m-0 text-xs text-text-muted">
            {isFa ? "از ۱ تا ۳۶۵۰ روز پس از دریافت فایل." : "1 to 3650 days after ingestion."}
          </p>
          {!retentionIsValid && (
            <p id="retention-days-error" className="platform-form-error" role="alert">
              {isFa ? "یک عدد صحیح بین ۱ تا ۳۶۵۰ وارد کنید." : "Enter a whole number from 1 to 3650."}
            </p>
          )}
        </div>
      )}

      <label className="platform-audio-policy flex cursor-pointer items-start gap-3 border-t border-border-subtle pt-4">
        <input
          className="mt-1 size-4 accent-accent"
          type="checkbox"
          checked={deleteAudio}
          onChange={(event) => {
            if (!isPending) setDeleteAudio(event.currentTarget.checked);
          }}
          aria-disabled={isPending}
        />
        <span>
          <span className="block text-sm font-semibold text-text-primary">
            {isFa ? "حذف فایل صوتی پس از پردازش" : "Delete audio after processing"}
          </span>
          <span className="mt-1 block text-xs leading-5 text-text-secondary">
            {isFa ? "پس از پایان موفق پردازش، فایل ضبط‌شده طبق سیاست فضای کاری حذف می‌شود." : "After successful processing, the recording is deleted according to workspace policy."}
          </span>
        </span>
      </label>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border-subtle pt-4">
        <span className="text-xs text-text-muted">
          {isFa ? "آخرین تغییر:" : "Last updated:"} {formatDate(settings.updated_at, lang)}
        </span>
        <button
          type="submit"
          className="platform-button platform-button-primary"
          aria-disabled={!canSave}
        >
          <Save aria-hidden="true" size={15} />
          {isPending ? (isFa ? "در حال ذخیره" : "Saving") : (isFa ? "ذخیره تنظیمات" : "Save settings")}
        </button>
      </div>
    </form>
  );
}

function SettingsPanel() {
  const { lang } = useI18n();
  const { refreshUser } = useAuth();
  const isFa = lang === "fa";
  const settingsQuery = useWorkspaceSettings();
  const updateMutation = useUpdateWorkspaceSettings();
  const statusRef = useRef<HTMLParagraphElement>(null);
  const focusStatus = useRef(false);

  // The form remounts with the saved values; focus moves to the confirmation
  // instead of being dropped to the page (QA S-01).
  useEffect(() => {
    if (!focusStatus.current || !updateMutation.isSuccess) return;
    focusStatus.current = false;
    statusRef.current?.focus();
  });

  return (
    <section className="platform-workspace-settings platform-surface p-4 sm:p-5" aria-labelledby="workspace-settings-heading">
      <div className="mb-5 flex items-center gap-3">
        <SlidersHorizontal aria-hidden="true" className="text-accent" size={20} />
        <div>
          <h2 id="workspace-settings-heading" className="m-0 text-sm font-semibold text-text-primary">
            {isFa ? "تنظیمات فضای کاری" : "Workspace settings"}
          </h2>
          <p className="mt-1 mb-0 text-xs text-text-secondary">
            {isFa ? "نام فضای کاری و سیاست نگهداری داده را اینجا تنظیم کنید." : "Set the workspace name and data-retention policy."}
          </p>
        </div>
      </div>

      {updateMutation.error && (
        <div className="mb-4">
          <ErrorState
            error={updateMutation.error}
            codes={SETTINGS_ERROR_CODES}
            fallback={isFa ? "ذخیره تنظیمات ناموفق بود." : "Could not save workspace settings."}
          />
        </div>
      )}
      {updateMutation.isSuccess && (
        <p
          ref={statusRef}
          tabIndex={-1}
          className="mb-4 rounded-md border border-success/30 bg-success/5 px-3 py-2 text-xs text-success"
          role="status"
        >
          {isFa ? "تنظیمات ذخیره شد." : "Settings saved."}
        </p>
      )}

      {settingsQuery.isLoading ? (
        <LoadingState label={isFa ? "در حال دریافت تنظیمات" : "Loading settings"} />
      ) : settingsQuery.isError ? (
        <ErrorState
          error={settingsQuery.error}
          codes={SETTINGS_ERROR_CODES}
          fallback={isFa ? "دریافت تنظیمات ناموفق بود." : "Could not load workspace settings."}
        />
      ) : settingsQuery.data ? (
        <WorkspaceSettingsForm
          key={settingsQuery.data.updated_at}
          settings={settingsQuery.data}
          isPending={updateMutation.isPending}
          onSave={(input) => {
            updateMutation.reset();
            updateMutation.mutate(input, {
              onSuccess: () => {
                focusStatus.current = true;
                // The header, chooser and page eyebrow read the workspace name
                // from the session (QA S-02).
                if (input.name !== undefined) void refreshUser().catch(() => undefined);
              },
            });
          }}
        />
      ) : null}
    </section>
  );
}

function formatAuditDetail(value: JsonValue): string {
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function AuditPanel() {
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const [eventTypeInput, setEventTypeInput] = useState("");
  const [outcomeInput, setOutcomeInput] = useState("");
  const [filters, setFilters] = useState({ eventType: "", outcome: "" });
  const [cursorStack, setCursorStack] = useState<Array<string | undefined>>([undefined]);
  const cursor = cursorStack[cursorStack.length - 1];
  const params = useMemo<AuditEventListParams>(
    () => ({
      limit: 25,
      cursor,
      event_type: filters.eventType || undefined,
      outcome: filters.outcome || undefined,
    }),
    [cursor, filters],
  );
  const auditQuery = useAuditEvents(params);
  const membersQuery = useMembers();
  const memberNames = useMemo(
    () => new Map((membersQuery.data?.items ?? []).map((member) => [member.user_id, member.display_name || member.email || member.phone_e164 || member.user_id])),
    [membersQuery.data],
  );
  const filtered = Boolean(filters.eventType || filters.outcome);
  const errorCode = auditQuery.isError ? apiErrorInfo(auditQuery.error).code : null;
  const cursorLost = errorCode === "invalid_cursor" || errorCode === "invalid_audit_range";

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters({ eventType: eventTypeInput.trim(), outcome: outcomeInput });
    setCursorStack([undefined]);
  }

  const knownTypes = Object.keys(AUDIT_EVENT_LABELS);
  const pageNumber = formatNumber(cursorStack.length, lang);

  return (
    <section className="platform-audit-log platform-surface overflow-hidden" aria-labelledby="audit-heading">
      <div className="border-b border-border-subtle px-4 py-4 sm:px-5">
        <div className="flex items-center gap-3">
          <History aria-hidden="true" className="text-accent" size={20} />
          <div>
            <h2 id="audit-heading" className="m-0 text-sm font-semibold text-text-primary">
              {isFa ? "لاگ ممیزی" : "Audit log"}
            </h2>
            <p className="mt-1 mb-0 text-xs text-text-secondary">
              {isFa ? "رویدادهای امنیتی و مدیریتی این فضای کاری" : "Security and administrative events for this workspace"}
            </p>
          </div>
        </div>
      </div>

      <form className="platform-audit-filters grid gap-3 border-b border-border-subtle bg-bg-primary p-4 sm:grid-cols-[minmax(180px,1fr)_160px_auto] sm:items-end sm:p-5" onSubmit={applyFilters}>
        <div className="platform-field">
          <label htmlFor="audit-event-type">{isFa ? "نوع رویداد" : "Event type"}</label>
          <select
            id="audit-event-type"
            className="platform-select"
            value={eventTypeInput}
            onChange={(event) => setEventTypeInput(event.currentTarget.value)}
          >
            <option value="">{isFa ? "همهٔ رویدادها" : "All events"}</option>
            {knownTypes.map((type) => (
              <option key={type} value={type}>{auditEventLabel(type, lang)}</option>
            ))}
          </select>
        </div>
        <div className="platform-field">
          <label htmlFor="audit-outcome">{isFa ? "نتیجه" : "Outcome"}</label>
          <select id="audit-outcome" className="platform-select" value={outcomeInput} onChange={(event) => setOutcomeInput(event.currentTarget.value)}>
            <option value="">{isFa ? "همه نتایج" : "All outcomes"}</option>
            <option value="success">{isFa ? "موفق" : "Succeeded"}</option>
            <option value="failure">{isFa ? "ناموفق" : "Failed"}</option>
          </select>
        </div>
        <button type="submit" className="platform-button">
          <SlidersHorizontal aria-hidden="true" size={15} />
          {isFa ? "اعمال فیلتر" : "Apply filters"}
        </button>
      </form>

      {auditQuery.isLoading ? (
        <LoadingState label={isFa ? "در حال دریافت رویدادها" : "Loading audit events"} />
      ) : auditQuery.isError ? (
        <div className="grid gap-3 p-4">
          <ErrorState
            error={auditQuery.error}
            codes={SETTINGS_ERROR_CODES}
            fallback={isFa ? "دریافت لاگ ممیزی ناموفق بود." : "Could not load the audit log."}
          />
          {cursorLost && (
            <button type="button" className="platform-button justify-self-start" onClick={() => setCursorStack([undefined])}>
              {isFa ? "صفحهٔ اول" : "First page"}
            </button>
          )}
        </div>
      ) : auditQuery.data?.items.length ? (
        <>
          <ol className="m-0 list-none p-0" aria-busy={auditQuery.isFetching}>
            {auditQuery.data.items.map((auditEvent) => {
              const eventLabel = auditEventLabel(auditEvent.event_type, lang);
              const actor = auditEvent.actor_user_id
                ? memberNames.get(auditEvent.actor_user_id) ?? auditEvent.actor_user_id
                : (isFa ? "سیستم" : "System");
              return (
                <li key={auditEvent.id} className="platform-audit-event border-b border-border-subtle px-4 py-4 last:border-b-0 sm:px-5">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <strong className="break-words text-sm text-text-primary">
                          {eventLabel ?? <bdi dir="ltr">{auditEvent.event_type}</bdi>}
                        </strong>
                        <StatusBadge status={auditEvent.outcome} />
                      </div>
                      {eventLabel && (
                        <code className="mt-1 block text-[0.68rem] text-text-muted" dir="ltr">{auditEvent.event_type}</code>
                      )}
                      <dl className="mt-3 grid gap-x-5 gap-y-2 text-xs text-text-secondary sm:grid-cols-2">
                        <div>
                          <dt className="inline text-text-muted">{isFa ? "عامل: " : "Actor: "}</dt>
                          <dd className="inline"><bdi dir="auto">{actor}</bdi></dd>
                        </div>
                        <div>
                          <dt className="inline text-text-muted">{isFa ? "هدف: " : "Target: "}</dt>
                          <dd className="inline">
                            {auditEvent.target_type
                              ? <>{auditTargetLabel(auditEvent.target_type, lang)}{auditEvent.target_id && <> · <bdi dir="ltr" className="font-mono">{auditEvent.target_id}</bdi></>}</>
                              : (isFa ? "بدون هدف" : "None")}
                          </dd>
                        </div>
                      </dl>
                      {Object.keys(auditEvent.details).length > 0 && (
                        <dl className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-text-secondary">
                          {Object.entries(auditEvent.details).map(([key, value]) => (
                            <div key={key} className="min-w-0">
                              <dt className="inline text-text-muted">{auditDetailKeyLabel(key, lang)}: </dt>
                              <dd className="inline break-all"><bdi dir="auto">{auditDetailValue(key, formatAuditDetail(value), lang)}</bdi></dd>
                            </div>
                          ))}
                        </dl>
                      )}
                    </div>
                    <time className="shrink-0 text-xs text-text-muted" dateTime={auditEvent.occurred_at}>
                      {formatDate(auditEvent.occurred_at, lang)}
                    </time>
                  </div>
                </li>
              );
            })}
          </ol>
          <nav className="flex items-center justify-between gap-3 border-t border-border-subtle px-4 py-3 sm:px-5" aria-label={isFa ? "صفحه‌بندی لاگ ممیزی" : "Audit log pagination"}>
            <button
              type="button"
              className="platform-button"
              aria-disabled={cursorStack.length === 1 || auditQuery.isFetching}
              onClick={() => {
                if (cursorStack.length === 1 || auditQuery.isFetching) return;
                setCursorStack((current) => current.slice(0, -1));
              }}
            >
              <ChevronLeft aria-hidden="true" size={15} className="rtl-mirror" />
              {isFa ? "قبلی" : "Previous"}
            </button>
            <span className="text-xs text-text-muted" aria-live="polite">
              {isFa ? `صفحه ${pageNumber}` : `Page ${pageNumber}`}
            </span>
            <button
              type="button"
              className="platform-button"
              aria-disabled={!auditQuery.data.next_cursor || auditQuery.isFetching}
              onClick={() => {
                if (!auditQuery.data.next_cursor || auditQuery.isFetching) return;
                setCursorStack((current) => [...current, auditQuery.data.next_cursor ?? undefined]);
              }}
            >
              {isFa ? "بعدی" : "Next"}
              <ChevronRight aria-hidden="true" size={15} className="rtl-mirror" />
            </button>
          </nav>
        </>
      ) : (
        <div className="p-4">
          <EmptyState
            label={filtered
              ? (isFa ? "رویدادی با این فیلتر یافت نشد." : "No audit events match these filters.")
              : (isFa ? "هنوز رویدادی ثبت نشده است." : "No events have been recorded yet.")}
          />
        </div>
      )}
    </section>
  );
}

function SettingsContent() {
  return (
    <div className="platform-settings-grid grid gap-5 xl:grid-cols-[minmax(320px,0.42fr)_minmax(0,0.58fr)]">
      <SettingsPanel />
      <AuditPanel />
    </div>
  );
}

export default function SettingsPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const canAdminister = workspace?.role === "owner" || workspace?.role === "admin";

  return (
    <main className="platform-page platform-settings-page">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={isFa ? "تنظیمات و ممیزی" : "Settings and audit"}
        actions={canAdminister ? (
          <span className="inline-flex items-center gap-2 text-xs text-text-secondary">
            <Settings aria-hidden="true" size={15} />
            {isFa ? "مدیریت فضای کاری" : "Workspace administration"}
          </span>
        ) : undefined}
      />

      {!workspace ? (
        <ErrorState error={null} fallback={isFa ? "فضای کاری فعالی انتخاب نشده است." : "No active workspace is selected."} />
      ) : !canAdminister ? (
        <section className="platform-surface p-5" aria-labelledby="settings-access-heading">
          <div className="flex items-start gap-3">
            <LockKeyhole aria-hidden="true" className="mt-0.5 shrink-0 text-text-muted" size={20} />
            <div>
              <h2 id="settings-access-heading" className="m-0 text-sm font-semibold text-text-primary">
                {isFa ? "دسترسی مدیریتی لازم است" : "Administrative access required"}
              </h2>
              <p className="mt-2 mb-0 text-sm text-text-secondary">
                {isFa ? "فقط مالک یا مدیر فضای کاری می‌تواند تنظیمات و لاگ ممیزی را مشاهده کند." : "Only workspace owners and administrators can view settings and the audit log."}
              </p>
              <p className="mt-2 mb-0 text-sm text-text-secondary">
                {isFa ? "برای تغییر تنظیمات با مالک یا مدیر فضای کاری هماهنگ کنید." : "Ask a workspace owner or administrator to change settings."}
              </p>
            </div>
          </div>
        </section>
      ) : (
        <SettingsContent />
      )}
    </main>
  );
}
