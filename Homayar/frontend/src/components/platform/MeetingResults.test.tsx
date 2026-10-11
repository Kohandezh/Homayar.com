import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { AxiosRequestConfig } from "axios";
import api from "@/lib/api";
import { platformQueryKeys } from "@/lib/platform-api";
import MeetingResults from "@/components/platform/MeetingResults";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole, testQueryClient } from "@/test/fixtures";
import type { MeetingDetail, Transcript } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

const MEETING: MeetingDetail = {
  id: "m-1",
  workspace_id: "ws-1",
  title: "Call",
  language: "fa",
  status: "completed",
  duration_seconds: 60,
  source: "file_upload",
  created_at: T,
  updated_at: T,
  recording: null,
  latest_job: null,
  quality: {
    transcription: "complete",
    speaker_attribution: "inferred",
    analysis: "unavailable",
    evidence: "insufficient",
    analysis_stale: false,
    transcript_revision_id: "rev-1",
    analysed_revision_id: null,
    analysis_run_id: null,
    segment_count: 2,
    speaker_count: 2,
    speech_coverage_ratio: 0.8,
    is_mock: false,
    degradations: [{ stage: "intelligence", state: "insufficient_evidence", reason: "empty_transcript" }],
    usage: null,
  },
};

function transcript(view: "original" | "redacted", enforced = true): Transcript {
  const redacted = view === "redacted";
  return {
    meeting_id: "m-1",
    language: "fa",
    view,
    privacy_enforced: enforced,
    redaction: redacted
      ? { id: "red-1", detector_version: "1", classes: ["email", "phone"], policy_digest: "d", entity_count: 2, mapping_retained: true }
      : null,
    speakers: [
      { id: "sp-1", speaker_key: "S1", display_name: "Agent", identity_type: "inferred", confidence: null },
      { id: "sp-2", speaker_key: "S2", display_name: "Customer", identity_type: "inferred", confidence: null },
    ],
    segments: [
      {
        id: "seg-1",
        sequence: 1,
        speaker_key: "S1",
        speaker_name: "Agent",
        start_ms: 0,
        end_ms: 4000,
        text: redacted ? "Please write to [EMAIL_1] today" : "Please write to person@example.com today",
        confidence: null,
        language: "en",
      },
      {
        id: "seg-2",
        sequence: 2,
        speaker_key: "S2",
        speaker_name: "Customer",
        start_ms: 5000,
        end_ms: 9000,
        text: redacted ? "My number is [PHONE_1]" : "My number is 09121234567",
        confidence: null,
        language: "en",
      },
    ],
  };
}

interface Routes {
  summary?: () => unknown;
}

function mockApi({ summary }: Routes = {}) {
  return vi.spyOn(api, "get").mockImplementation(async (url: string, config?: AxiosRequestConfig) => {
    if (url === "/meetings/m-1/transcript") {
      return okResponse(transcript(config?.params?.view === "original" ? "original" : "redacted"));
    }
    if (url === "/meetings/m-1/summary") {
      if (summary) return summary() as never;
      throw apiError(404, "summary_not_ready");
    }
    if (url === "/meetings/m-1/evidence") {
      return okResponse({
        meeting_id: "m-1",
        analysis_run_id: "run-1",
        current_revision_id: "rev-1",
        items: [
          {
            id: "ev-1",
            meeting_id: "m-1",
            analysis_run_id: "run-1",
            transcript_revision_id: "rev-1",
            revision_number: 1,
            is_current_revision: true,
            segment_id: "seg-2",
            speaker_key: "S2",
            finding_type: "decision",
            finding_id: null,
            finding_index: 0,
            text_start: 0,
            text_end: 5,
            text: "My number",
            start_ms: 5000,
            end_ms: 9000,
            confidence: null,
            evidence_type: "turn_reference",
            created_at: T,
          },
        ],
      });
    }
    if (url === "/meetings/m-1/actions") return okResponse([]);
    if (url === "/meetings/m-1/shares") return okResponse([]);
    throw apiError(404, "not_found");
  });
}

function renderResults(role: "owner" | "member" | "viewer" = "owner", queryClient = testQueryClient()) {
  return renderConsole(<MeetingResults meeting={MEETING} />, {
    lang: "en",
    queryClient,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: { ...FIXTURE_WORKSPACE, role } },
  });
}

function openTab(name: string) {
  fireEvent.click(screen.getByRole("tab", { name }));
}

describe("MeetingResults", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("explains an unavailable summary with the API's reason instead of an error", async () => {
    mockApi();
    renderResults();
    expect(await screen.findByText(/No summary is available for this meeting\./)).toBeInTheDocument();
    expect(screen.getByText(/The analysis is not available: No speech was recognised in the recording\./)).toBeInTheDocument();
  });

  it("marks masked values and states what redaction does and does not cover", async () => {
    mockApi();
    renderResults("member");
    expect(await screen.findByText(/In this transcript, 2 item\(s\) are masked \(email, phone number\)/)).toBeInTheDocument();
    expect(screen.getByText(/names and addresses are not\. The audio recording is not masked\./)).toBeInTheDocument();
    openTab("Transcript");
    expect(await screen.findByText("[EMAIL_1]")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show original transcript" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reveal [EMAIL_1]" })).not.toBeInTheDocument();
  });

  it("shows the audited original only after confirmation, fetches it once and forgets it on hide", async () => {
    const get = mockApi();
    const queryClient = testQueryClient();
    renderResults("owner", queryClient);
    fireEvent.click(await screen.findByRole("button", { name: "Show original transcript" }));
    expect(screen.getByRole("alertdialog")).toHaveTextContent("recorded in the workspace audit log");
    const originalCalls = () =>
      get.mock.calls.filter(([, config]) => (config as AxiosRequestConfig | undefined)?.params?.view === "original");
    expect(originalCalls()).toHaveLength(0);

    fireEvent.click(screen.getByRole("button", { name: "Show original" }));
    expect(await screen.findByText("Please write to person@example.com today")).toBeInTheDocument();
    expect(screen.getByText(/You are viewing the original transcript/)).toBeInTheDocument();
    expect(originalCalls()).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Hide original" }));
    expect(await screen.findByText("[EMAIL_1]")).toBeInTheDocument();
    expect(screen.queryByText(/person@example\.com/)).not.toBeInTheDocument();
    expect(queryClient.getQueryData(platformQueryKeys.transcriptOriginal("m-1"))).toBeUndefined();
    expect(originalCalls()).toHaveLength(1);
  });

  it("explains a refused original view", async () => {
    vi.spyOn(api, "get").mockImplementation(async (url: string, config?: AxiosRequestConfig) => {
      if (url === "/meetings/m-1/transcript" && config?.params?.view === "original") throw apiError(403, "original_access_denied");
      if (url === "/meetings/m-1/transcript") return okResponse(transcript("redacted"));
      throw apiError(404, "summary_not_ready");
    });
    renderResults("owner");
    fireEvent.click(await screen.findByRole("button", { name: "Show original transcript" }));
    fireEvent.click(screen.getByRole("button", { name: "Show original" }));
    expect(await screen.findByText("Your role cannot view the original transcript in this workspace.")).toBeInTheDocument();
  });

  it("reveals one masked value with a reason and keeps it out of the query cache", async () => {
    mockApi();
    const post = vi
      .spyOn(api, "post")
      .mockResolvedValueOnce(okResponse({ redaction_id: "red-1", placeholder: "[PHONE_1]", value: "09121234567" }));
    const queryClient = testQueryClient();
    renderResults("owner", queryClient);
    openTab("Transcript");
    fireEvent.click(await screen.findByRole("button", { name: "Reveal [PHONE_1]" }));
    fireEvent.change(screen.getByLabelText(/Reason for revealing this value/), { target: { value: "Customer callback" } });
    await act(async () => {
      fireEvent.submit(screen.getByLabelText(/Reason for revealing this value/).closest("form")!);
    });
    expect(post).toHaveBeenCalledWith("/meetings/m-1/transcript/redactions/red-1/reveal", {
      placeholder: "[PHONE_1]",
      reason: "Customer callback",
    });
    expect(await screen.findByText("09121234567")).toBeInTheDocument();
    const cached = JSON.stringify(queryClient.getQueryCache().getAll().map((query) => query.state.data));
    expect(cached).not.toContain("09121234567");

    fireEvent.click(screen.getByRole("button", { name: "Hide value" }));
    expect(screen.queryByText("09121234567")).not.toBeInTheDocument();
  });

  it("explains a reveal that the server cannot perform", async () => {
    mockApi();
    vi.spyOn(api, "post").mockRejectedValue(apiError(404, "mapping_not_retained"));
    renderResults("owner");
    openTab("Transcript");
    fireEvent.click(await screen.findByRole("button", { name: "Reveal [EMAIL_1]" }));
    fireEvent.change(screen.getByLabelText(/Reason for revealing this value/), { target: { value: "Audit request" } });
    await act(async () => {
      fireEvent.submit(screen.getByLabelText(/Reason for revealing this value/).closest("form")!);
    });
    expect(await screen.findByText("The original of this value was not kept, so it cannot be revealed.")).toBeInTheDocument();
  });

  it("links a decision to its evidence segment in the transcript", async () => {
    mockApi({
      summary: () =>
        okResponse({
          meeting_id: "m-1",
          overview: "Follow up with the customer.",
          decisions: [{ category: "decision", description: "Call back tomorrow", evidence_ids: ["ev-1"], evidence_status: "sufficient" }],
          key_points: [],
          topics: ["callback"],
          sentiment: null,
          provider: "p",
          model: "m",
          schema_version: 1,
          evidence_status: "partial",
          created_at: T,
          updated_at: T,
        }),
    });
    renderResults("owner");
    expect(await screen.findByText("Call back tomorrow")).toBeInTheDocument();
    expect(screen.getByText("Evidence: Sufficient")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "Show in transcript" }));
    expect(screen.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.getElementById("segment-seg-2")).toHaveFocus());
  });

  it("moves between tabs with the arrow keys and hides editing from viewers", async () => {
    mockApi();
    renderResults("viewer");
    const tablist = screen.getByRole("tablist", { name: "Meeting results" });
    expect(within(tablist).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Summary", "Transcript", "Action items"]);
    const summaryTab = screen.getByRole("tab", { name: "Summary" });
    summaryTab.focus();
    fireEvent.keyDown(summaryTab, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Transcript" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(screen.getByRole("tab", { name: "Transcript" }), { key: "End" });
    expect(screen.getByRole("tab", { name: "Action items" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByText("No action items")).toBeInTheDocument();
    expect(screen.queryByLabelText("Action description")).not.toBeInTheDocument();
  });

  it("labels the summary by its own view, not by the transcript's (gap G10)", async () => {
    const summary = (view: "original" | "redacted", enforced: boolean) => () =>
      okResponse({
        meeting_id: "m-1",
        overview: view === "redacted" ? "Write to [EMAIL_1]" : "Write to person@example.com",
        decisions: [],
        key_points: [],
        topics: [],
        sentiment: null,
        provider: "p",
        model: "m",
        schema_version: 1,
        view,
        privacy_enforced: enforced,
        redaction: view === "redacted" ? { detector_version: "1", classes: ["email"], policy_digest: "d" } : null,
        analysis_run: view === "redacted" ? { id: "run-1", transcript_revision_id: "rev-1", state: "completed", provider: "p", model: "m", prompt_version: null, schema_version: 1, is_mock: false, input_view: "redacted", created_at: T } : null,
        created_at: T,
        updated_at: T,
      });

    mockApi({ summary: summary("redacted", true) });
    const first = renderResults("owner");
    expect(await screen.findByText(/This summary is shown with personal data masked by the same detector/)).toBeInTheDocument();
    expect(screen.getByText("The analysis was produced from the masked transcript.")).toBeInTheDocument();
    first.unmount();
    vi.restoreAllMocks();

    // The transcript is redacted (mockApi) but this summary response says original:
    // the notice follows the summary, not the transcript flag.
    mockApi({ summary: summary("original", true) });
    renderResults("owner");
    expect(await screen.findByText("This summary is shown without masking.")).toBeInTheDocument();
    expect(screen.queryByText(/This summary is shown with personal data masked/)).not.toBeInTheDocument();
  });

  it("opens the transcript at the segment playing at ?at= and says when none matches (QA Q-01)", async () => {
    mockApi();
    const { unmount } = renderConsole(<MeetingResults meeting={MEETING} atMs={6200} />, {
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER },
    });
    expect(await screen.findByText("Showing the transcript segment at 0:06.")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Transcript" })).toHaveAttribute("aria-selected", "true");
    await waitFor(() => expect(document.getElementById("segment-seg-2")).toHaveFocus());
    unmount();

    mockApi();
    renderConsole(<MeetingResults meeting={MEETING} atMs={4500} />, {
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER },
    });
    // 4.5 s falls between the segments: the one that started before it is shown.
    await waitFor(() => expect(document.getElementById("segment-seg-1")).toHaveFocus());
  });
});

