import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import api from "@/lib/api";
import type { AuthContextValue } from "@/lib/auth-context";
import { useI18n } from "@/lib/i18n-context";
import SettingsPage from "@/components/platform/SettingsPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { WorkspaceRole } from "@/types/auth";
import type { WorkspaceSettings } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";
// Slower CI machines need more than the 1 s default for the first render.
const LOAD = { timeout: 5000 };

function settings(overrides: Partial<WorkspaceSettings> = {}): WorkspaceSettings {
  return {
    id: "ws-1",
    name: "Fixture workspace",
    retention_days: 0,
    retention_mode: "legacy_unresolved",
    delete_audio_after_processing: false,
    record_scope: "workspace",
    public_share_links_enabled: true,
    organization_share_links_enabled: true,
    duplicate_media_policy: "allow",
    pii_redaction_enabled: true,
    pii_redaction_classes: ["bank_card", "email", "iban", "ir_national_id", "ir_postal_code", "phone"],
    updated_at: T,
    ...overrides,
  };
}

function mockReads(current: WorkspaceSettings) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/admin/workspace-settings") return okResponse(current);
    throw apiError(404, "not_found");
  });
}

/** A stand-in for the header's language switch. */
function SwitchToPersian() {
  const { setLang } = useI18n();
  return (
    <button type="button" onClick={() => setLang("fa")}>
      switch to fa
    </button>
  );
}

function renderSettings({
  lang = "en",
  role = "owner",
  refreshUser = vi.fn<AuthContextValue["refreshUser"]>().mockResolvedValue(FIXTURE_USER),
}: { lang?: "fa" | "en"; role?: WorkspaceRole; refreshUser?: AuthContextValue["refreshUser"] } = {}) {
  const workspace = { ...FIXTURE_WORKSPACE, role };
  return renderConsole(<SettingsPage />, {
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace], refreshUser },
  });
}

const SECTIONS = ["General", "Data retention", "Sharing", "Access scope", "Duplicates", "Privacy"];
const CLASS_LABELS = ["Email", "Phone", "National ID", "Bank card", "IBAN", "Postal code"];

describe("SettingsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("shows every section and control to the owner, with save and discard disabled", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 30 }));
    renderSettings();

    await screen.findByLabelText("Workspace name", undefined, LOAD);
    expect(screen.getByRole("heading", { level: 1, name: "Workspace settings" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Workspace settings" })).toBeInTheDocument();
    for (const name of SECTIONS) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
    const retention = screen.getByRole("radiogroup", { name: "Data retention policy" });
    expect(within(retention).getByRole("radio", { name: /^Finite/ })).toBeChecked();
    expect(within(retention).getByRole("radio", { name: /^Indefinite/ })).toBeInTheDocument();
    expect(within(retention).getByRole("radio", { name: /^Immediate/ })).toBeInTheDocument();
    expect(screen.getByLabelText("Retention period (days)")).toHaveValue("30");
    for (const name of ["Delete audio after processing", "Public share links", "Organization share links", "PII redaction"]) {
      expect(screen.getByRole("switch", { name })).toBeEnabled();
    }
    const scope = screen.getByRole("radiogroup", { name: "Record visibility" });
    expect(within(scope).getByRole("radio", { name: "Whole workspace" })).toBeChecked();
    expect(within(scope).getByRole("radio", { name: "By team" })).toBeInTheDocument();
    expect(screen.getByText(/agents see only their own\. Owners and admins always see everything/)).toBeInTheDocument();
    const duplicates = screen.getByRole("radiogroup", { name: "Duplicate files" });
    expect(within(duplicates).getByRole("radio", { name: "Allow" })).toBeChecked();
    expect(within(duplicates).getByRole("radio", { name: "Reject" })).toBeInTheDocument();
    const classes = screen.getByRole("group", { name: "Redacted data types" });
    for (const name of CLASS_LABELS) {
      expect(within(classes).getByRole("checkbox", { name })).toBeEnabled();
    }
    expect(screen.queryByText("Only the workspace owner can change these settings.")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Discard changes" })).toBeDisabled();
  });

  it("sends only the changed share switch and shows the saved status", async () => {
    mockReads(settings());
    const patch = vi
      .spyOn(api, "patch")
      .mockResolvedValue(okResponse(settings({ public_share_links_enabled: false, updated_at: "2026-09-24T13:00:00Z" })));
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("switch", { name: "Public share links" }, LOAD));
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { public_share_links_enabled: false });
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByText("Settings saved.")));
  });

  it("renames a legacy workspace without any retention key, then refreshes the session (QA S-02)", async () => {
    mockReads(settings());
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ name: "Renamed", updated_at: "2026-09-24T13:00:00Z" })));
    const refreshUser = vi.fn<AuthContextValue["refreshUser"]>().mockResolvedValue(FIXTURE_USER);
    const user = userEvent.setup();
    renderSettings({ refreshUser });

    const name = await screen.findByLabelText("Workspace name", undefined, LOAD);
    expect(screen.getByRole("alert")).toHaveTextContent('The old "0 days" value is ambiguous');
    for (const radio of within(screen.getByRole("radiogroup", { name: "Data retention policy" })).getAllByRole("radio")) {
      expect(radio).not.toBeChecked();
    }
    await user.clear(name);
    await user.type(name, "  Renamed ");
    await user.click(screen.getByRole("button", { name: "Save settings" }));

    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { name: "Renamed" });
    await waitFor(() => expect(refreshUser).toHaveBeenCalled());
  });

  it("disables the PII controls for an admin, keeps their values, and never sends PII keys", async () => {
    mockReads(settings({ pii_redaction_classes: ["email", "phone"] }));
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ record_scope: "team" })));
    const user = userEvent.setup();
    renderSettings({ role: "admin" });

    const pii = await screen.findByRole("switch", { name: "PII redaction" }, LOAD);
    expect(pii).toBeDisabled();
    expect(pii).toBeChecked();
    expect(screen.getByText("Only the workspace owner can change these settings.")).toBeInTheDocument();
    const classes = screen.getByRole("group", { name: "Redacted data types" });
    for (const name of CLASS_LABELS) {
      expect(within(classes).getByRole("checkbox", { name })).toBeDisabled();
    }
    expect(within(classes).getByRole("checkbox", { name: "Email" })).toBeChecked();
    expect(within(classes).getByRole("checkbox", { name: "IBAN" })).not.toBeChecked();
    expect(screen.getByRole("switch", { name: "Public share links" })).toBeEnabled();
    expect(screen.getByRole("switch", { name: "Organization share links" })).toBeEnabled();
    expect(screen.getByLabelText("Workspace name")).toBeEnabled();

    await user.click(screen.getByRole("radio", { name: "By team" }));
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { record_scope: "team" });
  });

  it("disables the class checkboxes while redaction is off and keeps the values", async () => {
    mockReads(settings({ pii_redaction_classes: ["email"] }));
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ pii_redaction_enabled: false })));
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("switch", { name: "PII redaction" }, LOAD));
    const email = screen.getByRole("checkbox", { name: "Email" });
    expect(email).toBeDisabled();
    expect(email).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { pii_redaction_enabled: false });
  });

  it("blocks saving with no data type chosen and shows the message", async () => {
    mockReads(settings({ pii_redaction_classes: ["email"] }));
    const patch = vi.spyOn(api, "patch");
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("checkbox", { name: "Email" }, LOAD));
    expect(screen.getByText("Choose at least one data type.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Phone" }));
    expect(screen.queryByText("Choose at least one data type.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { pii_redaction_classes: ["phone"] });
  });

  it("discards every edit without sending a request", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 30 }));
    const patch = vi.spyOn(api, "patch");
    const user = userEvent.setup();
    renderSettings();

    const name = await screen.findByLabelText("Workspace name", undefined, LOAD);
    await user.clear(name);
    await user.type(name, "Other");
    await user.click(screen.getByRole("switch", { name: "Organization share links" }));
    await user.click(screen.getByRole("radio", { name: "Reject" }));
    await user.click(screen.getByRole("radio", { name: /^Indefinite/ }));
    expect(screen.queryByLabelText("Retention period (days)")).not.toBeInTheDocument();

    const discard = screen.getByRole("button", { name: "Discard changes" });
    expect(discard).toBeEnabled();
    await user.click(discard);

    expect(name).toHaveValue("Fixture workspace");
    expect(screen.getByRole("switch", { name: "Organization share links" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Allow" })).toBeChecked();
    expect(screen.getByRole("radio", { name: /^Finite/ })).toBeChecked();
    expect(screen.getByLabelText("Retention period (days)")).toHaveValue("30");
    expect(discard).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save settings" })).toBeDisabled();
    expect(patch).not.toHaveBeenCalled();
  });

  it("keeps Discard disabled after a language switch with no edit, and enables it after a real edit", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 30 }));
    const user = userEvent.setup();
    const workspace = { ...FIXTURE_WORKSPACE, role: "owner" as const };
    renderConsole(
      <>
        <SwitchToPersian />
        <SettingsPage />
      </>,
      { lang: "en", auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace] } },
    );

    expect(await screen.findByLabelText("Retention period (days)", undefined, LOAD)).toHaveValue("30");
    await user.click(screen.getByRole("button", { name: "switch to fa" }));

    const discard = await screen.findByRole("button", { name: "کنار گذاشتن تغییرات" });
    expect(discard).toBeDisabled();
    expect(screen.getByRole("button", { name: "ذخیره تنظیمات" })).toBeDisabled();

    // Control: a real edit of the days enables it.
    const days = screen.getByLabelText("مدت نگهداری (روز)");
    await user.clear(days);
    await user.type(days, "۶۰");
    expect(discard).toBeEnabled();
  });

  it("asks before a shorter retention: Cancel sends nothing and keeps the edit", async () => {
    mockReads(settings({ retention_mode: "indefinite" }));
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ retention_mode: "immediate" })));
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("radio", { name: /^Immediate/ }, LOAD));
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("older data will be permanently deleted");

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByRole("radio", { name: /^Immediate/ })).toBeChecked();

    await user.click(screen.getByRole("button", { name: "Save settings" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete data and save" }));
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { retention_mode: "immediate" });
  });

  it("saves a longer retention period at once", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 30 }));
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ retention_mode: "finite", retention_days: 60 })));
    const user = userEvent.setup();
    renderSettings();

    const days = await screen.findByLabelText("Retention period (days)", undefined, LOAD);
    await user.clear(days);
    await user.type(days, "60");
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { retention_days: 60 });
  });

  it("keeps a blank or zero retention period invalid and saves Persian digits as a number (QA X-03)", async () => {
    mockReads(settings());
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ retention_mode: "finite", retention_days: 14 })));
    const user = userEvent.setup();
    renderSettings({ lang: "fa" });

    await user.click(await screen.findByRole("radio", { name: /^محدود/ }, LOAD));
    const days = screen.getByLabelText("مدت نگهداری (روز)");
    expect(days).toHaveValue("");
    expect(days).toHaveAttribute("aria-invalid", "true");
    const save = screen.getByRole("button", { name: "ذخیره تنظیمات" });
    expect(save).toBeDisabled();

    await user.type(days, "۰");
    expect(screen.getByText("یک عدد صحیح بین ۱ تا ۳۶۵۰ وارد کنید.")).toBeInTheDocument();
    expect(save).toBeDisabled();

    await user.clear(days);
    await user.type(days, "۱۴");
    await user.click(save);
    // Legacy to finite deletes data sooner, so it is confirmed first.
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "حذف داده‌ها و ذخیره" }));
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { retention_mode: "finite", retention_days: 14 });
  });

  it("shows an existing retention period in Persian digits and the Persian section names", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 90 }));
    renderSettings({ lang: "fa" });
    expect(await screen.findByLabelText("مدت نگهداری (روز)", undefined, LOAD)).toHaveValue("۹۰");
    expect(screen.getByRole("heading", { level: 1, name: "تنظیمات فضای کاری" })).toBeInTheDocument();
    for (const name of ["عمومی", "نگهداری داده", "اشتراک‌گذاری", "دامنهٔ دسترسی", "فایل‌های تکراری", "حریم خصوصی"]) {
      expect(screen.getByRole("heading", { level: 2, name })).toBeInTheDocument();
    }
  });

  it("explains a refused redaction change", async () => {
    mockReads(settings());
    vi.spyOn(api, "patch").mockRejectedValue(apiError(403, "permission_denied"));
    const user = userEvent.setup();
    renderSettings();

    await user.click(await screen.findByRole("switch", { name: "PII redaction" }, LOAD));
    await user.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Only the workspace owner can change the redaction policy.")).toBeInTheDocument();
  });
});
