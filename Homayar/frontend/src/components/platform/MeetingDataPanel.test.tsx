import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router";
import { today } from "@internationalized/date";

import api from "@/lib/api";
import MeetingDetailPage from "@/components/platform/MeetingDetailPage";
import { privacyQueryKeys } from "@/lib/privacy-api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import { callReport } from "@/test/fixtures-report";
import type { WorkspaceRole } from "@/types/auth";
import type { MeetingDetail } from "@/types/platform";
import type { MediaObject } from "@/types/privacy";

const T = "2026-09-24T12:00:00Z";
const LOAD = { timeout: 5000 };

const MEETING: MeetingDetail = {
  id: "m-1",
  workspace_id: "ws-1",
  title: "Call",
  language: "fa",
  status: "processing",
  duration_seconds: 60,
  source: "file_upload",
  created_at: T,
  updated_at: T,
  recording: null,
  latest_job: null,
  quality: null,
};

function media(overrides: Partial<MediaObject> = {}): MediaObject {
  return {
    id: "media-1",
    meeting_id: "m-1",
    kind: "original",
    lifecycle_state: "active",
    integrity: "verified",
    content_type: "audio/wav",
    size_bytes: 1000,
    original_filename: "call.wav",
    created_at: T,
    retention_expires_at: "2027-03-01T00:00:00Z",
    deletion_requested_at: null,
    deleted_at: null,
    legal_hold_at: null,
    legal_hold_reason: null,
    legal_hold_by_user_id: null,
    legal_hold_review_at: null,
    ...overrides,
  };
}

function heldMedia(): MediaObject {
  return media({
    lifecycle_state: "legal_hold",
    legal_hold_at: T,
    legal_hold_reason: "litigation",
    legal_hold_by_user_id: "user-1",
    legal_hold_review_at: "2027-01-16T00:00:00Z",
  });
}

function mockMeetingApi(items: MediaObject[] = [media()]) {
  const state = { items, holdError: null as unknown, releaseError: null as unknown };
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url === "/meetings/m-1") return okResponse(MEETING);
    if (url === "/meetings/m-1/media") return okResponse({ items: state.items });
    if (url === "/meetings/m-1/report") return okResponse(callReport());
    throw apiError(404, "not_found");
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (url: string, body: unknown) => {
    if (url === "/meetings/m-1/legal-hold") {
      if (state.holdError) throw state.holdError;
      state.items = [heldMedia()];
      return okResponse({ items: state.items });
    }
    if (url === "/media/media-1/legal-hold/release") {
      if (state.releaseError) throw state.releaseError;
      state.items = [media()];
      return okResponse(state.items[0]);
    }
    throw apiError(404, `unexpected ${url} ${JSON.stringify(body)}`);
  });
  return { state, get, post };
}

function mediaReads(get: ReturnType<typeof mockMeetingApi>["get"]) {
  return get.mock.calls.filter(([url]) => url === "/meetings/m-1/media").length;
}

function renderMeeting(role: WorkspaceRole = "owner") {
  const workspace = { ...FIXTURE_WORKSPACE, role };
  return renderConsole(
    <Routes>
      <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
    </Routes>,
    {
      route: "/meetings/m-1",
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace] },
    },
  );
}

/** The data panel sits in the closed advanced area below the call report (HOM-245). */
async function openAdvanced() {
  fireEvent.click(await screen.findByRole("button", { name: "Transcript and tools" }, LOAD));
}

function panel() {
  return screen.getByRole("region", { name: "Data and retention" });
}

describe("MeetingDataPanel visibility", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("shows the panel and reads the media list for the owner", async () => {
    const { get } = mockMeetingApi();
    renderMeeting("owner");
    expect(await screen.findByRole("button", { name: "Transcript and tools" }, LOAD)).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("region", { name: "Data and retention" })).not.toBeInTheDocument();
    expect(mediaReads(get)).toBe(0);
    await openAdvanced();
    expect(await screen.findByRole("region", { name: "Data and retention" }, LOAD)).toBeInTheDocument();
    const table = await within(panel()).findByRole("grid", { name: "Stored recordings" }, LOAD);
    const [, row] = within(table).getAllByRole("row");
    expect(within(row).getByText("Original recording")).toBeInTheDocument();
    expect(within(row).getByText("Active")).toBeInTheDocument();
    expect(within(row).getByText("Verified")).toBeInTheDocument();
    expect(within(row).getByText("Mar 1, 2027")).toBeInTheDocument();
    expect(mediaReads(get)).toBe(1);
  });

  it.each(["admin", "member", "viewer"] as const)("has no panel and no media request for %s", async (role) => {
    const { get } = mockMeetingApi();
    renderMeeting(role);
    expect(await screen.findByRole("heading", { level: 1, name: "Call" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Data and retention" })).not.toBeInTheDocument();
    expect(screen.queryByText("Data and retention")).not.toBeInTheDocument();
    expect(mediaReads(get)).toBe(0);
  });

  it("shows the hold reason and review date of a held object", async () => {
    mockMeetingApi([heldMedia()]);
    renderMeeting();
    await openAdvanced();
    const table = await within(await screen.findByRole("region", { name: "Data and retention" }, LOAD)).findByRole(
      "grid",
      { name: "Stored recordings" },
      LOAD,
    );
    expect(within(table).getByText("On legal hold")).toBeInTheDocument();
    // The reason is its own element with the exact text; the review day has its own.
    expect(within(table).getByText("litigation", { exact: true })).toBeInTheDocument();
    expect(table.querySelector('time[datetime="2027-01-16T00:00:00Z"]')).toHaveTextContent(/^Jan 16, 2027$/);
    expect(within(table).getByRole("button", { name: "Release" })).toHaveAccessibleDescription("Original recording");
  });

  describe("west of UTC", () => {
    beforeEach(() => {
      vi.stubEnv("TZ", "America/Los_Angeles");
    });
    afterEach(() => {
      vi.unstubAllEnvs();
    });

    it("shows the hold review date as its UTC day", async () => {
      expect(new Date("2027-01-16T00:00:00Z").getDate()).toBe(15);
      mockMeetingApi([heldMedia()]);
      renderMeeting();
    await openAdvanced();
      const table = await within(await screen.findByRole("region", { name: "Data and retention" }, LOAD)).findByRole(
        "grid",
        { name: "Stored recordings" },
        LOAD,
      );
      expect(table.querySelector('time[datetime="2027-01-16T00:00:00Z"]')).toHaveTextContent(/^Jan 16, 2027$/);
    });
  });

  it("disables Place legal hold when every object is deleted", async () => {
    mockMeetingApi([media({ lifecycle_state: "deleted", deleted_at: T })]);
    renderMeeting();
    await openAdvanced();
    await within(await screen.findByRole("region", { name: "Data and retention" }, LOAD)).findByRole(
      "grid",
      { name: "Stored recordings" },
      LOAD,
    );
    expect(within(panel()).getByRole("button", { name: "Place legal hold" })).toBeDisabled();
  });
});

describe("MeetingDataPanel place and release", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  async function openPlaceDialog(user: ReturnType<typeof userEvent.setup>) {
    await within(await screen.findByRole("region", { name: "Data and retention" }, LOAD)).findByRole(
      "grid",
      { name: "Stored recordings" },
      LOAD,
    );
    await user.click(within(panel()).getByRole("button", { name: "Place legal hold" }));
    return screen.findByRole("dialog", { name: "Place a legal hold" });
  }

  it("places a hold at 00:00 UTC of the default review day (today + 90) and reads the lists again", async () => {
    const user = userEvent.setup();
    const { get, post } = mockMeetingApi();
    const { queryClient } = renderMeeting();
    await openAdvanced();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const dialog = await openPlaceDialog(user);

    const confirm = within(dialog).getByRole("button", { name: "Place hold" });
    const reason = within(dialog).getByRole("textbox", { name: "Reason" });
    await user.type(reason, "ab");
    expect(confirm).toBeDisabled();
    await user.type(reason, "c");
    expect(confirm).toBeEnabled();
    const readsBefore = mediaReads(get);
    await user.click(confirm);

    expect(await screen.findByText("The legal hold was placed.")).toBeInTheDocument();
    const expectedDay = today("UTC").add({ days: 90 }).toString();
    expect(post).toHaveBeenCalledWith("/meetings/m-1/legal-hold", {
      reason: "abc",
      review_at: `${expectedDay}T00:00:00.000Z`,
    });
    await waitFor(() => expect(mediaReads(get)).toBeGreaterThan(readsBefore));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: privacyQueryKeys.legalHoldsRoot() });
    expect(await within(panel()).findByText("On legal hold")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("shows hold_review_invalid under the date field and keeps the dialog open", async () => {
    const user = userEvent.setup();
    const { state } = mockMeetingApi();
    state.holdError = apiError(409, "hold_review_invalid");
    renderMeeting();
    await openAdvanced();
    const dialog = await openPlaceDialog(user);
    await user.type(within(dialog).getByRole("textbox", { name: "Reason" }), "litigation");
    await user.click(within(dialog).getByRole("button", { name: "Place hold" }));

    const message = await within(dialog).findByText("Choose a review date in the future, at most 10 years from today.");
    expect(message.closest(".date-picker")).not.toBeNull();
    expect(within(dialog).queryByRole("alert")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Place a legal hold" })).toBeInTheDocument();
  });

  const HOLD_ERRORS: [string, unknown, string][] = [
    ["already_deleted", apiError(409, "already_deleted"), "This recording was already deleted and cannot be held."],
    ["media_not_found", apiError(404, "media_not_found"), "This recording no longer exists."],
    ["permission_denied", apiError(403, "permission_denied"), "Only the workspace owner can do this."],
    ["another code", apiError(422, "validation_error"), "The values were not accepted; check the fields."],
  ];

  it.each(HOLD_ERRORS)("keeps the hold dialog open with the message for %s", async (_name, error, message) => {
    const user = userEvent.setup();
    const { state } = mockMeetingApi();
    state.holdError = error;
    renderMeeting();
    await openAdvanced();
    const dialog = await openPlaceDialog(user);
    await user.type(within(dialog).getByRole("textbox", { name: "Reason" }), "litigation");
    await user.click(within(dialog).getByRole("button", { name: "Place hold" }));

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("dialog", { name: "Place a legal hold" })).toBeInTheDocument();
    expect(within(dialog).getByRole("textbox", { name: "Reason" })).toHaveValue("litigation");
  });

  it("releases a held object with the reason and reads the list again", async () => {
    const user = userEvent.setup();
    const { get, post } = mockMeetingApi([heldMedia()]);
    renderMeeting();
    await openAdvanced();
    await user.click(
      await screen.findByRole("button", { name: "Release" }, LOAD),
    );
    const dialog = await screen.findByRole("dialog", { name: "Release the legal hold" });
    await user.type(within(dialog).getByRole("textbox", { name: "Reason" }), "matter closed");
    const readsBefore = mediaReads(get);
    await user.click(within(dialog).getByRole("button", { name: "Release hold" }));

    expect(await screen.findByText("The legal hold was released.")).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/media/media-1/legal-hold/release", { reason: "matter closed" });
    await waitFor(() => expect(mediaReads(get)).toBeGreaterThan(readsBefore));
    expect(await within(panel()).findByText("Active")).toBeInTheDocument();
  });

  it("says not_held in the release dialog and reads the list again", async () => {
    const user = userEvent.setup();
    const { get, state } = mockMeetingApi([heldMedia()]);
    state.releaseError = apiError(409, "not_held");
    renderMeeting();
    await openAdvanced();
    await user.click(
      await screen.findByRole("button", { name: "Release" }, LOAD),
    );
    const dialog = await screen.findByRole("dialog", { name: "Release the legal hold" });
    await user.type(within(dialog).getByRole("textbox", { name: "Reason" }), "matter closed");
    const readsBefore = mediaReads(get);
    await user.click(within(dialog).getByRole("button", { name: "Release hold" }));

    expect(
      await within(dialog).findByText("This recording is not on legal hold. It may have been released already."),
    ).toBeInTheDocument();
    await waitFor(() => expect(mediaReads(get)).toBeGreaterThan(readsBefore));
    expect(screen.getByRole("dialog", { name: "Release the legal hold" })).toBeInTheDocument();
  });
});
