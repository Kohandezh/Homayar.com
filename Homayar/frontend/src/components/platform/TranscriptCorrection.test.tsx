import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { AxiosRequestConfig } from "axios";
import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import CallReport from "@/components/platform/CallReport";
import MeetingResults from "@/components/platform/MeetingResults";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import { REPORT_QUALITY, callReport, reportEvidence, reportFinding } from "@/test/fixtures-report";
import {
  correctedTranscript,
  memberList,
  oldTranscript,
  transcriptRevisions,
  transcriptWithRevision,
} from "@/test/fixtures-transcript";
import { formatDate } from "@/lib/format";
import type { CallReportResponse, MeetingDetail, Transcript } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

const MEETING: MeetingDetail = {
  id: "m-1",
  workspace_id: "ws-1",
  title: "Call",
  language: "en",
  status: "completed",
  duration_seconds: 60,
  source: "file_upload",
  created_at: T,
  updated_at: T,
  recording: null,
  latest_job: null,
  quality: null,
};

/** The transcript GET answers with this until a correction changes it. */
let currentTranscript: Transcript = transcriptWithRevision();

function mockApi({
  report = callReport(),
  members = () => okResponse(memberList()),
}: {
  report?: CallReportResponse;
  members?: () => unknown;
} = {}) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string, config?: AxiosRequestConfig) => {
    if (url === "/meetings/m-1/transcript") {
      if (config?.params?.view === "original") {
        // The audited original: the same revision without the redaction.
        return okResponse(transcriptWithRevision());
      }
      const revisionId = config?.params?.revision_id;
      if (revisionId === "rev-3") return okResponse(oldTranscript());
      if (revisionId) throw apiError(404, "revision_not_found");
      return okResponse(currentTranscript);
    }
    if (url === "/meetings/m-1/transcript/revisions") return okResponse(transcriptRevisions());
    if (url === "/members") return members() as never;
    if (url === "/meetings/m-1/actions") return okResponse([]);
    if (url === "/meetings/m-1/shares") return okResponse([]);
    if (url === "/meetings/m-1/report") return okResponse(report);
    throw apiError(404, "not_found");
  });
}

/** The transcript under an enforced redaction policy: the original needs the audited view. */
function redactedCurrentTranscript(): Transcript {
  return transcriptWithRevision({
    view: "redacted",
    privacy_enforced: true,
    redaction: { id: "red-1", detector_version: "1", classes: ["email"], policy_digest: "d", entity_count: 1, mapping_retained: true },
  });
}

function renderResults(role: "owner" | "member" | "viewer" = "owner", lang: "en" | "fa" = "en") {
  return renderConsole(<MeetingResults meeting={MEETING} />, {
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: { ...FIXTURE_WORKSPACE, role } },
  });
}

function startEdit(name = "Edit turn at 0:00") {
  fireEvent.click(screen.getByRole("button", { name }));
}

function fillForm(text?: string, reason = "Fix a typo", speaker?: string) {
  if (text !== undefined) fireEvent.change(screen.getByLabelText("Turn text"), { target: { value: text } });
  if (speaker !== undefined) fireEvent.change(screen.getByLabelText("Speaker"), { target: { value: speaker } });
  fireEvent.change(screen.getByLabelText("Reason for the correction"), { target: { value: reason } });
}

async function submitForm() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Save correction" }));
  });
}

function renderReport(overrides: Partial<CallReportResponse> = {}) {
  return renderConsole(
    <CallReport
      report={callReport(overrides)}
      canPlay={false}
      onPlay={() => undefined}
      onShowSegment={() => undefined}
      onOpenPanel={() => undefined}
    />,
    { lang: "en" },
  );
}

describe("TranscriptCorrection", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    currentTranscript = transcriptWithRevision();
  });

  it("hides the edit buttons from a viewer but keeps the revision history", async () => {
    mockApi();
    renderResults("viewer");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit turn at/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Revision history" })).toBeInTheDocument();
  });

  it("keeps Save disabled without a reason, with a 2-character reason and without a change", async () => {
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    startEdit();
    // One segment at a time: the other segment loses its Edit button.
    expect(screen.queryByRole("button", { name: "Edit turn at 0:05" })).not.toBeInTheDocument();

    const save = screen.getByRole("button", { name: "Save correction" });
    fillForm("First line, corrected", "");
    expect(save).toBeDisabled();
    fillForm(undefined, "ab");
    expect(save).toBeDisabled();
    fillForm(undefined, "Fix");
    expect(save).toBeEnabled();
    // Back to the unchanged text: nothing to save.
    fillForm("First line of the call");
    expect(save).toBeDisabled();
  });

  it("sends exactly the correction body for a text edit and reports the new revision", async () => {
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(correctedTranscript(), 201));
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    startEdit();
    fillForm("  First line, corrected  ", "  Fix   a typo ");
    await submitForm();
    expect(post).toHaveBeenCalledWith("/meetings/m-1/transcript/corrections", {
      base_revision_id: "rev-4",
      reason: "Fix a typo",
      edits: [{ segment_id: "seg-1", text: "First line, corrected" }],
    });
    expect(await screen.findByText("Saved as revision 5.")).toHaveAttribute("role", "status");
    // The saved turn keeps its sequence but gets a new id; focus follows it.
    await waitFor(() => expect(document.querySelector('[data-sequence="1"]')).toHaveFocus());
  });

  it("sends only speaker_key for a speaker move", async () => {
    mockApi();
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse(correctedTranscript(), 201));
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    startEdit();
    fillForm(undefined, "This turn belongs to the customer", "S2");
    await submitForm();
    expect(post).toHaveBeenCalledWith("/meetings/m-1/transcript/corrections", {
      base_revision_id: "rev-4",
      reason: "This turn belongs to the customer",
      edits: [{ segment_id: "seg-1", speaker_key: "S2" }],
    });
  });

  it("explains revision_stale, reloads the transcript and leaves edit mode", async () => {
    const get = mockApi();
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "revision_stale"));
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    const before = get.mock.calls.filter(([url]) => url === "/meetings/m-1/transcript").length;
    startEdit();
    fillForm("First line, corrected");
    await submitForm();
    const message = await screen.findByText(
      "The transcript changed since it was loaded. Reload it and make the correction again.",
    );
    expect(message.closest('[role="alert"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Reload transcript" }));
    expect(await screen.findByRole("button", { name: "Edit turn at 0:00" })).toBeInTheDocument();
    expect(get.mock.calls.filter(([url]) => url === "/meetings/m-1/transcript").length).toBeGreaterThan(before);
  });

  it("lists every revision with source, who, when and the correction reason", async () => {
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    const drawer = await screen.findByRole("dialog", { name: "Revision history" });
    await within(drawer).findByText("Revision 4");
    expect(within(drawer).getByText("Revision 3")).toBeInTheDocument();
    expect(within(drawer).getByText("Current")).toBeInTheDocument();
    expect(within(drawer).getByText("Automatic transcription")).toBeInTheDocument();
    expect(within(drawer).getByText("Correction")).toBeInTheDocument();
    expect(within(drawer).getByText("You")).toBeInTheDocument();
    expect(within(drawer).getByText("System")).toBeInTheDocument();
    // user-2's backfill row resolves through /members, not to Unknown member.
    expect(within(drawer).getByText("Second Member")).toBeInTheDocument();
    expect(within(drawer).queryByText("Unknown member")).not.toBeInTheDocument();
    expect(within(drawer).getByText("Fixed the company name")).toBeInTheDocument();
    expect(within(drawer).getByText(formatDate(T, "en"))).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "View revision 3" })).toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: "View revision 4" })).not.toBeInTheDocument();
  });

  it("shows an old revision read-only and returns to the current one", async () => {
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    fireEvent.click(await screen.findByRole("button", { name: "View revision 3" }));
    const banner = await screen.findByText("You are viewing revision 3. It is read only.");
    expect(banner.closest("[role=status]")).not.toBeNull();
    expect(await screen.findByText("Original first line")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit turn at/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to current" }));
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit turn at 0:00" })).toBeInTheDocument();
  });

  it("marks the summary, decisions and findings on an earlier transcript", () => {
    renderReport({
      qa: {
        quality: { ...REPORT_QUALITY, analysis_stale: true },
        findings: [reportFinding()],
        counts: { total: 1, supported: 1, unsupported: 0, sufficient: 1, partial: 0, insufficient: 0 },
      },
    });
    const markers = screen.getAllByText("Based on an earlier transcript");
    expect(markers).toHaveLength(3);
    for (const marker of markers) expect(marker.className).toContain("call-report-stale");
  });

  it("shows no stale marker while the analysis is current, and one on a stale evidence ref alone", () => {
    const { unmount } = renderReport();
    expect(screen.queryByText("Based on an earlier transcript")).not.toBeInTheDocument();
    unmount();

    renderReport({
      outcome: {
        state: "explicit",
        decisions: [
          {
            text: "Call back tomorrow",
            key_point_index: null,
            confidence: null,
            evidence_status: "sufficient",
            supported: true,
            evidence: [reportEvidence({ is_current_revision: false })],
          },
        ],
      },
    });
    expect(screen.getAllByText("Based on an earlier transcript")).toHaveLength(1);
  });

  it("shows the correction UI in Persian", async () => {
    mockApi();
    renderResults("owner", "fa");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "ویرایش بخش در ۰:۰۰" }));
    expect(screen.getByLabelText("متن بخش")).toBeInTheDocument();
    expect(screen.getByLabelText("گوینده")).toBeInTheDocument();
    expect(screen.getByLabelText("دلیل اصلاح")).toBeInTheDocument();
    expect(screen.getByText("الزامی. ۳ تا ۵۰۰ نویسه.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "ذخیرهٔ اصلاح" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "انصراف" }));
    fireEvent.click(screen.getByRole("button", { name: "تاریخچهٔ نسخه‌ها" }));
    const drawer = await screen.findByRole("dialog", { name: "تاریخچهٔ نسخه‌ها" });
    await within(drawer).findByText("فعلی");
    expect(within(drawer).getByText("اصلاح")).toBeInTheDocument();
    // Every drawer label is Persian; no English label leaks through.
    for (const english of ["Current", "Correction", "Automatic transcription", "Backfill", "System", "Unknown member", "You"]) {
      expect(within(drawer).queryByText(english, { exact: true })).not.toBeInTheDocument();
    }
    expect(within(drawer).queryByText(/^Revision /)).not.toBeInTheDocument();
    expect(within(drawer).getAllByText(/^نسخهٔ /).length).toBeGreaterThan(0);
  });

  it("labels the revision rows through the copy, in Persian digits", async () => {
    mockApi();
    renderResults("owner", "fa");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "تاریخچهٔ نسخه‌ها" }));
    const drawer = await screen.findByRole("dialog", { name: "تاریخچهٔ نسخه‌ها" });
    await within(drawer).findByText("نسخهٔ ۴");
    expect(within(drawer).getByText("نسخهٔ ۳")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "مشاهدهٔ نسخهٔ ۳" })).toBeInTheDocument();
  });

  it("leaves edit mode when an old revision is opened", async () => {
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    startEdit();
    fillForm("First line, corrected");
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    fireEvent.click(await screen.findByRole("button", { name: "View revision 3" }));
    await screen.findByText("You are viewing revision 3. It is read only.");
    expect(screen.queryByLabelText("Turn text")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save correction" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to current" }));
    expect(await screen.findByRole("button", { name: "Edit turn at 0:00" })).toBeInTheDocument();
    // The edit state is gone: the segment shows its text, not a form.
    expect(screen.queryByLabelText("Turn text")).not.toBeInTheDocument();
  });

  it("leaves edit mode when the audited original view is turned on", async () => {
    currentTranscript = redactedCurrentTranscript();
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    startEdit();
    fillForm("First line, corrected");
    fireEvent.click(screen.getByRole("button", { name: "Show original transcript" }));
    fireEvent.click(screen.getByRole("button", { name: /^Show original$/ }));
    await screen.findByText(/You are viewing the original transcript/);
    expect(screen.queryByLabelText("Turn text")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Edit turn at/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Hide original" }));
    expect(await screen.findByRole("button", { name: "Edit turn at 0:00" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Turn text")).not.toBeInTheDocument();
  });

  it("hides the speaker rename forms while an old revision is shown", async () => {
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    expect(screen.getByLabelText("Speakers", { exact: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    fireEvent.click(await screen.findByRole("button", { name: "View revision 3" }));
    await screen.findByText("You are viewing revision 3. It is read only.");
    expect(screen.queryByLabelText("Speakers", { exact: true })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to current" }));
    expect(await screen.findByLabelText("Speakers", { exact: true })).toBeInTheDocument();
  });

  it("keeps the old-revision view and the original view apart", async () => {
    currentTranscript = redactedCurrentTranscript();
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();

    // An old revision is read only: the original view is not offered on top of it.
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    fireEvent.click(await screen.findByRole("button", { name: "View revision 3" }));
    await screen.findByText("You are viewing revision 3. It is read only.");
    expect(screen.queryByRole("button", { name: "Show original transcript" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Back to current" }));

    // Turning on the original view leaves the old-revision mode.
    fireEvent.click(screen.getByRole("button", { name: "Show original transcript" }));
    fireEvent.click(screen.getByRole("button", { name: /^Show original$/ }));
    await screen.findByText(/You are viewing the original transcript/);
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    fireEvent.click(await screen.findByRole("button", { name: "View revision 3" }));
    await screen.findByText("You are viewing revision 3. It is read only.");
    expect(screen.queryByText(/You are viewing the original transcript/)).not.toBeInTheDocument();
    expect(await screen.findByText("Original first line")).toBeInTheDocument();
  });

  it("does not treat a whitespace-only difference as a change", async () => {
    const padded = transcriptWithRevision();
    padded.segments = padded.segments.map((segment) =>
      segment.id === "seg-1" ? { ...segment, text: `  ${segment.text}  ` } : segment,
    );
    currentTranscript = padded;
    mockApi();
    renderResults("owner");
    expect(await screen.findByText(/First line of the call/)).toBeInTheDocument();
    startEdit();
    // The trimmed text is what the segment already says: nothing to save.
    fillForm("First line of the call");
    expect(screen.getByRole("button", { name: "Save correction" })).toBeDisabled();
  });

  it("ends the edit when the speaker filter hides the turn being edited", async () => {
    mockApi();
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    startEdit();
    fillForm("First line, corrected");
    // Filter to the other speaker: the edited turn leaves the screen.
    fireEvent.change(screen.getByLabelText("Show speaker"), { target: { value: "S2" } });
    expect(await screen.findByText("Second line of the call")).toBeInTheDocument();
    expect(screen.queryByLabelText("Turn text")).not.toBeInTheDocument();
    // The remaining turn is editable again; no stale edit state blocks the buttons.
    expect(screen.getByRole("button", { name: "Edit turn at 0:05" })).toBeInTheDocument();
  });

  it("ends the edit when a refetch brings a revision with new segment ids", async () => {
    mockApi();
    const { queryClient } = renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    startEdit();
    fillForm("First line, corrected");
    // A background refetch (window focus) answers with revision 5, whose
    // segments have new ids; the open form belonged to a segment now gone.
    currentTranscript = correctedTranscript();
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: platformQueryKeys.transcript("m-1"), exact: true });
    });
    // waitFor: the refetch swaps the turn nodes, so a first match can detach.
    await waitFor(() => expect(screen.getByText("First line, corrected")).toBeInTheDocument());
    expect(screen.queryByLabelText("Turn text")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save correction" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit turn at 0:00" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit turn at 0:05" })).toBeInTheDocument();
  });

  it("names the row's author only once the members are loaded", async () => {
    let resolveMembers: (value: unknown) => void = () => undefined;
    const members = new Promise((resolve) => { resolveMembers = resolve; });
    mockApi({ members: () => members });
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    const drawer = await screen.findByRole("dialog", { name: "Revision history" });
    await within(drawer).findByText("Revision 4");
    // You and System need no member list, but another member's row shows no
    // name (never "Unknown member") until the list has loaded.
    expect(within(drawer).getByText("You")).toBeInTheDocument();
    expect(within(drawer).getByText("System")).toBeInTheDocument();
    expect(within(drawer).queryByText("Unknown member")).not.toBeInTheDocument();
    expect(within(drawer).queryByText("Second Member")).not.toBeInTheDocument();
    await act(async () => { resolveMembers(okResponse(memberList())); });
    expect(await within(drawer).findByText("Second Member")).toBeInTheDocument();
    expect(within(drawer).queryByText("Unknown member")).not.toBeInTheDocument();
  });

  it("shows the row without an author name when the members request fails", async () => {
    mockApi({ members: () => { throw apiError(500, "unavailable"); } });
    renderResults("owner");
    expect(await screen.findByText("First line of the call")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Revision history" }));
    const drawer = await screen.findByRole("dialog", { name: "Revision history" });
    await within(drawer).findByText("Revision 4");
    expect(within(drawer).getByText("Current")).toBeInTheDocument();
    expect(within(drawer).getByText("Automatic transcription")).toBeInTheDocument();
    expect(within(drawer).queryByText("Unknown member")).not.toBeInTheDocument();
    expect(within(drawer).queryByText("Second Member")).not.toBeInTheDocument();
  });
});
