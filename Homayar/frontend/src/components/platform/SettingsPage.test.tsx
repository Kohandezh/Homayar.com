import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import api from "@/lib/api";
import type { AuthContextValue } from "@/lib/auth-context";
import SettingsPage from "@/components/platform/SettingsPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
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

function renderSettings(lang: "fa" | "en" = "en", refreshUser = vi.fn<AuthContextValue["refreshUser"]>().mockResolvedValue(FIXTURE_USER)) {
  return renderConsole(<SettingsPage />, {
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE], refreshUser },
  });
}

describe("SettingsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renames a legacy workspace without sending any retention change, then refreshes the session (QA S-02)", async () => {
    mockReads(settings());
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ name: "Renamed", updated_at: "2026-09-24T13:00:00Z" })));
    const refreshUser = vi.fn<AuthContextValue["refreshUser"]>().mockResolvedValue(FIXTURE_USER);
    renderSettings("en", refreshUser);

    const name = await screen.findByLabelText("Workspace name", undefined, LOAD);
    expect(screen.getByLabelText("Data retention policy")).toHaveValue("");
    fireEvent.change(name, { target: { value: "Renamed" } });
    await act(async () => {
      fireEvent.submit(name.closest("form")!);
    });

    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { name: "Renamed" });
    expect(refreshUser).toHaveBeenCalled();
    await waitFor(() => expect(document.activeElement).toBe(screen.getByText("Settings saved.")));
  });

  it("keeps a blank or zero retention period blank or zero and saves Persian digits as a number (QA X-03)", async () => {
    mockReads(settings());
    const patch = vi.spyOn(api, "patch").mockResolvedValue(okResponse(settings({ retention_mode: "finite", retention_days: 14 })));
    renderSettings("fa");

    const mode = await screen.findByLabelText("سیاست نگهداری داده", undefined, LOAD);
    fireEvent.change(mode, { target: { value: "finite" } });
    const days = screen.getByLabelText("مدت نگهداری (روز)");
    expect(days).toHaveValue("");
    expect(days).toHaveAttribute("aria-invalid", "true");

    const form = days.closest("form")!;
    await act(async () => {
      fireEvent.submit(form);
    });
    expect(patch).not.toHaveBeenCalled();

    fireEvent.change(days, { target: { value: "۰" } });
    await act(async () => {
      fireEvent.submit(form);
    });
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByText("یک عدد صحیح بین ۱ تا ۳۶۵۰ وارد کنید.")).toBeInTheDocument();

    fireEvent.change(days, { target: { value: "۱۴" } });
    await act(async () => {
      fireEvent.submit(form);
    });
    expect(patch).toHaveBeenCalledWith("/admin/workspace-settings", { retention_mode: "finite", retention_days: 14 });
  });

  it("shows an existing retention period in Persian digits", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 90 }));
    renderSettings("fa");
    expect(await screen.findByLabelText("مدت نگهداری (روز)", undefined, LOAD)).toHaveValue("۹۰");
  });

});
