import { useEffect, useRef } from "react";
import { LockKeyhole, Settings, SlidersHorizontal } from "lucide-react";

import { useAuth } from "@/lib/auth-context";
import { useCan } from "@/lib/use-can";
import type { ErrorCodeMessages } from "@/lib/error-messages";
import { useI18n } from "@/lib/i18n-context";
import { useUpdateWorkspaceSettings, useWorkspaceSettings } from "@/lib/platform-api";
import type { UpdateWorkspaceSettingsInput } from "@/types/platform";
import WorkspaceSettingsForm from "@/components/admin/settings/WorkspaceSettingsForm";
import { ErrorState, LoadingState, PageHeader } from "@/components/platform/Ui";

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
  // Sent when the redaction keys come from someone without pii:original_access.
  permission_denied: {
    fa: "فقط مالک فضای کاری می‌تواند سیاست پنهان‌سازی را تغییر دهد.",
    en: "Only the workspace owner can change the redaction policy.",
  },
};

function SettingsPanel() {
  const { lang } = useI18n();
  const { refreshUser } = useAuth();
  const isFa = lang === "fa";
  const settingsQuery = useWorkspaceSettings();
  const updateMutation = useUpdateWorkspaceSettings();
  const canEditPii = useCan().has("pii:original_access");
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
            {isFa
              ? "نام، نگهداری داده، اشتراک‌گذاری، دسترسی و حریم خصوصی این فضای کاری را تنظیم کنید."
              : "Set the name, data retention, sharing, access and privacy of this workspace."}
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
          canEditPii={canEditPii}
          onSave={(patch) => {
            // The form only saves a non-empty patch.
            const input = patch as UpdateWorkspaceSettingsInput;
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

export default function SettingsPage() {
  const { workspace } = useAuth();
  const { lang } = useI18n();
  const isFa = lang === "fa";
  const canAdminister = useCan().manageWorkspace;

  return (
    <main className="platform-page platform-settings-page">
      <PageHeader
        eyebrow={workspace?.name ?? (isFa ? "فضای کاری" : "Workspace")}
        title={isFa ? "تنظیمات فضای کاری" : "Workspace settings"}
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
                {isFa ? "فقط مالک یا مدیر فضای کاری می‌تواند تنظیمات را مشاهده کند." : "Only workspace owners and administrators can view these settings."}
              </p>
              <p className="mt-2 mb-0 text-sm text-text-secondary">
                {isFa ? "برای تغییر تنظیمات با مالک یا مدیر فضای کاری هماهنگ کنید." : "Ask a workspace owner or administrator to change settings."}
              </p>
            </div>
          </div>
        </section>
      ) : (
        <SettingsPanel />
      )}
    </main>
  );
}
