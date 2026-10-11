import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import api from "@/lib/api";
import { formatDuration } from "@/lib/format";
import AppLayout from "@/components/layout/AppLayout";
import MeetingResults from "@/components/platform/MeetingResults";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { MeetingDetail } from "@/types/platform";

describe("keyboard and RTL affordances", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("formats clock durations with Persian digits in Persian", () => {
    expect(formatDuration(754, "fa")).toBe("۱۲:۳۴");
    expect(formatDuration(3723, "en")).toBe("1:02:03");
  });

  it("closes the mobile navigation drawer with Escape", async () => {
    renderConsole(
      <Routes>
        <Route element={<AppLayout />}>
          <Route index element={<div>Overview content</div>} />
        </Route>
      </Routes>,
      {
        lang: "en",
        auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] },
      },
    );
    fireEvent.click(screen.getByRole("button", { name: "Open navigation" }));
    // The HeroUI drawer is a modal dialog; React Aria listens for Escape on it.
    const drawer = await screen.findByRole("dialog", { name: "Navigation" });
    expect(drawer).toBeInTheDocument();
    fireEvent.keyDown(drawer, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument());
  });

  it("moves focus into the audited-view confirmation", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/meetings/m-1/transcript") {
        return okResponse({
          meeting_id: "m-1",
          language: "fa",
          view: "redacted",
          privacy_enforced: true,
          redaction: { id: "r", detector_version: "1", classes: ["email"], policy_digest: "d", entity_count: 1, mapping_retained: false },
          speakers: [],
          segments: [],
        });
      }
      throw apiError(404, "summary_not_ready");
    });
    const meeting = { id: "m-1", status: "completed", quality: null, latest_job: null } as unknown as MeetingDetail;
    renderConsole(<MeetingResults meeting={meeting} />, {
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE },
    });
    expect(await screen.findByRole("region", { name: "Personal data masking" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show original transcript" }));
    expect(screen.getByRole("button", { name: "Show original" })).toHaveFocus();
  });
});
