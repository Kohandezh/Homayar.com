import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import api from "@/lib/api";
import type { AuthContextValue } from "@/lib/auth-context";
import SettingsPage from "@/components/platform/SettingsPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { AuditEvent, WorkspaceSettings } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";
// First render waits for three queries; slower CI machines need more than the 1 s default.
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

const ROLE_CHANGE: AuditEvent = {
  id: "ev-1",
  actor_user_id: FIXTURE_USER.id,
  event_type: "member.role_changed",
  target_type: "membership",
  target_id: "mem-2",
  outcome: "failure",
  request_id: null,
  details: { previous_role: "member", new_role: "viewer" },
  occurred_at: T,
};

function mockReads(current: WorkspaceSettings, events: AuditEvent[] = [ROLE_CHANGE], nextCursor: string | null = null) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/admin/workspace-settings") return okResponse(current);
    if (url === "/admin/audit-events") return okResponse({ items: events, next_cursor: nextCursor });
    if (url === "/members") {
      return okResponse({
        items: [{ id: "mem-1", user_id: FIXTURE_USER.id, email: FIXTURE_USER.email, phone_e164: null, display_name: "Person", role: "owner", joined_at: T }],
        total: 1,
      });
    }
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

  it("labels audit events, details and outcomes, keeping the raw code visible (QA S-04, X-02)", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 90 }));
    renderSettings("en");
    // The label appears in the event-type filter and on the event itself.
    expect(await screen.findByText("member.role_changed", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getAllByText("Member role changed")).toHaveLength(2);
    // The outcome filter offers "Failed" too; the badge is the styled one.
    expect(screen.getAllByText("Failed").some((node) => node.className.includes("platform-status-failure"))).toBe(true);
    expect(screen.getByText("Previous role:")).toBeInTheDocument();
    expect(screen.getByText("Member")).toBeInTheDocument();
    expect(screen.getByText("Viewer")).toBeInTheDocument();
    expect(screen.getByText("Person")).toBeInTheDocument();
  });

  it("mirrors the pagination arrows and uses Persian digits for the page number (QA S-03)", async () => {
    mockReads(settings({ retention_mode: "finite", retention_days: 90 }), [ROLE_CHANGE], "cursor-2");
    renderSettings("fa");
    const next = await screen.findByRole("button", { name: "بعدی" }, LOAD);
    expect(next.querySelector("svg")?.getAttribute("class")).toContain("rtl-mirror");
    expect(screen.getByText("صفحه ۱")).toBeInTheDocument();
    fireEvent.click(next);
    expect(await screen.findByText("صفحه ۲", undefined, LOAD)).toBeInTheDocument();
    expect(document.body.contains(next)).toBe(true);
  });

  it("offers the first page again after an invalid cursor (QA S-08)", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/admin/workspace-settings") return okResponse(settings({ retention_mode: "finite", retention_days: 90 }));
      if (url === "/admin/audit-events") throw apiError(400, "invalid_cursor");
      if (url === "/members") return okResponse({ items: [], total: 0 });
      throw apiError(404, "not_found");
    });
    renderSettings("en");
    expect(await screen.findByText("The event list has changed; start again from the first page.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "First page" })).toBeInTheDocument();
  });
});
