import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, within } from "@testing-library/react";
import CallReport from "@/components/platform/CallReport";
import { renderConsole } from "@/test/fixtures";
import { REPORT_QUALITY, callReport, reportEvidence, reportFinding, reviewActions } from "@/test/fixtures-report";
import type { CallReportResponse } from "@/types/platform";

function renderReport(
  report: CallReportResponse,
  { canPlay = true, lang = "en" as "fa" | "en", analysisOnRedacted = false } = {},
) {
  const handlers = { onPlay: vi.fn(), onShowSegment: vi.fn(), onOpenPanel: vi.fn() };
  const view = renderConsole(
    <CallReport report={report} canPlay={canPlay} analysisOnRedacted={analysisOnRedacted} {...handlers} />,
    { lang },
  );
  return { ...view, ...handlers };
}

const region = (name: string) => screen.getByRole("region", { name });

describe("CallReport", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("renders the sections in the report order", () => {
    renderReport(callReport({ completeness: { ...callReport().completeness, is_complete: false, notices: ["mock_analysis"] } }));
    const names = screen.getAllByRole("region").map((element) => element.getAttribute("aria-label") ?? element.querySelector("h2")?.textContent);
    const order = ["Report completeness", "Summary", "Outcome", "QA findings", "Review actions"];
    expect(names.filter((name) => order.includes(name ?? ""))).toEqual(order);
  });

  it("shows one label per notice in API order, a raw unknown code, and no section without notices", () => {
    const notices = [
      "processing_in_progress",
      "processing_failed",
      "transcript_unavailable",
      "transcript_partial",
      "analysis_unavailable",
      "analysis_partial",
      "analysis_stale",
      "mock_analysis",
      "unsupported_findings",
      "brand_new_notice",
    ];
    const { unmount } = renderReport(callReport({ completeness: { ...callReport().completeness, is_complete: false, notices } }));
    const items = within(region("Report completeness")).getAllByRole("listitem").map((item) => item.textContent);
    expect(items).toEqual([
      "Processing is still running. This report is not final.",
      "Processing failed. This report is incomplete.",
      "No transcript is available.",
      "The transcript is partial.",
      "No analysis is available.",
      "The analysis is partial.",
      "The transcript changed after the analysis. Results may be out of date.",
      "This analysis was made by a test (mock) provider.",
      "Some findings have no evidence and are hidden.",
      "brand_new_notice",
    ]);
    unmount();

    renderReport(callReport());
    expect(screen.queryByRole("region", { name: "Report completeness" })).not.toBeInTheDocument();
  });

  it("shows the summary with its evidence status, topics and sentiment, and labels an unsupported one", () => {
    const base = callReport().summary!;
    const { unmount } = renderReport(callReport({ summary: { ...base, sentiment: "positive" } }));
    const summary = region("Summary");
    expect(within(summary).getByText("Follow up with the customer.")).toBeInTheDocument();
    expect(within(summary).getByText("Evidence: Sufficient")).toBeInTheDocument();
    expect(within(summary).getByText("callback")).toBeInTheDocument();
    expect(within(summary).getByText("positive")).toBeInTheDocument();
    expect(within(summary).queryByText("Not supported by evidence")).not.toBeInTheDocument();
    unmount();

    renderReport(callReport({ summary: { ...base, supported: false, evidence_status: "insufficient", evidence: [] } }));
    expect(within(region("Summary")).getByText("Not supported by evidence")).toBeInTheDocument();
    expect(within(region("Summary")).getByText("Evidence: Insufficient")).toBeInTheDocument();
  });

  it("says when there is no summary and gives the analysis reason from the quality", () => {
    renderReport(
      callReport({
        summary: null,
        outcome: { state: "unavailable", decisions: [] },
        qa: {
          quality: { ...REPORT_QUALITY, degradations: [{ stage: "intelligence", state: "insufficient_evidence", reason: "empty_transcript" }] },
          findings: [],
          counts: { total: 0, supported: 0, unsupported: 0, sufficient: 0, partial: 0, insufficient: 0 },
        },
      }),
    );
    const summary = region("Summary");
    expect(within(summary).getByText(/No summary is available yet\./)).toBeInTheDocument();
    expect(within(summary).getByText(/The analysis is not available: No speech was recognised in the recording\./)).toBeInTheDocument();
    expect(within(region("Outcome")).getByText("The outcome is not available yet.")).toBeInTheDocument();
  });

  it("labels the summary by the report's own view (gap G10)", () => {
    const { unmount } = renderReport(
      callReport({ view: "redacted", privacy_enforced: true, redaction: { detector_version: "1", classes: ["email"], policy_digest: "d" } }),
      { analysisOnRedacted: true },
    );
    expect(within(region("Summary")).getByText(/This summary is shown with personal data masked by the same detector/)).toBeInTheDocument();
    expect(within(region("Summary")).getByText("The analysis was produced from the masked transcript.")).toBeInTheDocument();
    unmount();

    renderReport(callReport({ view: "original", privacy_enforced: true }));
    expect(within(region("Summary")).getByText("This summary is shown without masking.")).toBeInTheDocument();
    expect(screen.queryByText(/This summary is shown with personal data masked/)).not.toBeInTheDocument();
    expect(screen.queryByText("The analysis was produced from the masked transcript.")).not.toBeInTheDocument();
  });

  it("labels each outcome state, and shows evidence only for supported decisions", () => {
    const labels = {
      explicit: "Stated outcome",
      inferred: "AI inference, not stated in the call",
      not_stated: "No outcome was stated in the call.",
      unavailable: "The outcome is not available yet.",
    } as const;
    for (const [state, label] of Object.entries(labels)) {
      const view = renderReport(callReport({ outcome: { state: state as keyof typeof labels, decisions: [] } }));
      expect(within(region("Outcome")).getByText(label)).toBeInTheDocument();
      view.unmount();
    }

    const supported = callReport().outcome.decisions[0];
    renderReport(
      callReport({
        outcome: {
          state: "explicit",
          decisions: [
            supported,
            { ...supported, text: "Send the offer", supported: false, evidence_status: "insufficient", evidence: [] },
          ],
        },
        qa: { ...callReport().qa, findings: [] },
      }),
    );
    const outcome = region("Outcome");
    const [first, second] = within(outcome).getAllByRole("listitem").filter((item) => item.dataset.decision !== undefined);
    expect(within(first).getByText("Call back tomorrow")).toBeInTheDocument();
    expect(within(first).getByRole("button", { name: "Play evidence at 0:05" })).toBeInTheDocument();
    expect(within(second).getByText("Send the offer")).toBeInTheDocument();
    expect(within(second).getByText("No evidence")).toBeInTheDocument();
    expect(within(second).queryByRole("button", { name: /Play evidence at/ })).not.toBeInTheDocument();
  });

  it("lists only supported findings, with the counts line and the quality panel", () => {
    renderReport(
      callReport({
        qa: {
          quality: REPORT_QUALITY,
          findings: [
            reportFinding({ finding_type: "summary", text: "Overview finding", finding_index: null }),
            reportFinding({ text: "Partial with evidence", evidence_status: "partial", category: "pricing" }),
            reportFinding({ text: "Partial without evidence", evidence_status: "partial", supported: false, evidence: [] }),
            reportFinding({ text: "Insufficient with a ref", evidence_status: "insufficient", supported: false }),
            reportFinding({ finding_type: "action_item", text: "Send the quote", finding_id: "a-1", finding_index: null }),
          ],
          counts: { total: 5, supported: 3, unsupported: 2, sufficient: 2, partial: 2, insufficient: 1 },
        },
      }),
    );
    const qa = region("QA findings");
    expect(within(qa).getByText("3 of 5 findings have evidence")).toBeInTheDocument();
    expect(within(qa).getByRole("region", { name: "Quality and coverage" })).toBeInTheDocument();
    expect(within(qa).getByText("Overview finding")).toBeInTheDocument();
    expect(within(qa).getByText("Partial with evidence")).toBeInTheDocument();
    expect(within(qa).getByText("Send the quote")).toBeInTheDocument();
    expect(within(qa).queryByText("Partial without evidence")).not.toBeInTheDocument();
    expect(within(qa).queryByText("Insufficient with a ref")).not.toBeInTheDocument();
    // Type labels: the category for an insight that has one.
    expect(within(qa).getByText("pricing")).toBeInTheDocument();
    expect(within(qa).getByText("Action item")).toBeInTheDocument();
    expect(within(qa).getByText("Evidence: Partial")).toBeInTheDocument();
    expect(within(qa).getAllByRole("button", { name: "Play evidence at 0:05" })).toHaveLength(3);
  });

  it("never lists a finding without evidence refs, even when it says supported", () => {
    renderReport(
      callReport({
        qa: {
          quality: REPORT_QUALITY,
          findings: [
            reportFinding({ text: "Supported but empty", supported: true, evidence: [] }),
            reportFinding({ text: "Supported with a ref" }),
          ],
          counts: { total: 2, supported: 2, unsupported: 0, sufficient: 2, partial: 0, insufficient: 0 },
        },
      }),
    );
    const qa = region("QA findings");
    expect(within(qa).queryByText("Supported but empty")).not.toBeInTheDocument();
    expect(within(qa).getByText("Supported with a ref")).toBeInTheDocument();
  });

  it("says when no finding has evidence", () => {
    renderReport(
      callReport({
        qa: {
          quality: REPORT_QUALITY,
          findings: [reportFinding({ supported: false, evidence: [], evidence_status: "insufficient" })],
          counts: { total: 1, supported: 0, unsupported: 1, sufficient: 0, partial: 0, insufficient: 1 },
        },
      }),
    );
    expect(within(region("QA findings")).getByText("No findings with evidence.")).toBeInTheDocument();
    expect(within(region("QA findings")).queryByText("Customer asked for a callback")).not.toBeInTheDocument();
  });

  it("shows every evidence ref with its quote, speaker and time, and plays or opens it", () => {
    const refs = [
      reportEvidence(),
      reportEvidence({ id: "ev-2", segment_id: "seg-1", speaker_key: "S1", text: "Call me", start_ms: 65000, end_ms: 70000 }),
    ];
    const { onPlay, onShowSegment } = renderReport(callReport({ qa: { ...callReport().qa, findings: [reportFinding({ evidence: refs })] } }));
    const qa = region("QA findings");
    const quote = within(qa).getByText("Call me");
    expect(quote.closest("[dir='auto']")).not.toBeNull();
    expect(within(qa).getByText("S1")).toBeInTheDocument();
    const time = within(qa).getByText("1:05–1:10");
    expect(time.tagName).toBe("BDI");
    expect(time).toHaveAttribute("dir", "ltr");

    fireEvent.click(within(qa).getByRole("button", { name: "Play evidence at 1:05" }));
    expect(onPlay).toHaveBeenCalledWith(65000);
    fireEvent.click(within(qa).getAllByRole("button", { name: "Show in transcript" })[1]);
    expect(onShowSegment).toHaveBeenCalledWith("seg-1");
  });

  it("labels evidence from an earlier revision and gives it no transcript link", () => {
    renderReport(
      callReport({
        qa: {
          ...callReport().qa,
          findings: [
            reportFinding({ text: "Old ref", evidence: [reportEvidence({ is_current_revision: false })] }),
          ],
        },
        outcome: { state: "not_stated", decisions: [] },
      }),
    );
    const qa = region("QA findings");
    expect(within(qa).getByText("From an earlier transcript revision")).toBeInTheDocument();
    expect(within(qa).queryByRole("button", { name: "Show in transcript" })).not.toBeInTheDocument();
    expect(within(qa).getByRole("button", { name: "Play evidence at 0:05" })).toBeInTheDocument();
  });

  it("has no play control without a recording, but keeps the quote and time", () => {
    renderReport(callReport(), { canPlay: false });
    expect(screen.queryByRole("button", { name: /Play evidence at/ })).not.toBeInTheDocument();
    const qa = region("QA findings");
    expect(within(qa).getByText("My number")).toBeInTheDocument();
    expect(within(qa).getByText("0:05–0:09")).toBeInTheDocument();
  });

  it("decides review actions only from the report: enabled ones open a panel, others are disabled with a reason", () => {
    const { onOpenPanel } = renderReport(
      callReport({
        review_actions: reviewActions({
          correct_transcript: { available: false, reason: "permission_denied" },
          rename_speaker: { available: false, reason: "no_speakers" },
          edit_action_items: { available: false, reason: "no_transcript" },
          share: { available: false, reason: "brand_new_reason" },
        }),
      }),
    );
    const actions = region("Review actions");
    expect(within(actions).getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Correct transcript",
      "Rename speakers",
      "Edit action items",
      "Share",
      "Export",
    ]);
    expect(within(actions).getByRole("button", { name: "Correct transcript" })).toBeDisabled();
    expect(within(actions).getByText("Your role cannot do this.")).toBeInTheDocument();
    expect(within(actions).getByText("No speakers were found.")).toBeInTheDocument();
    expect(within(actions).getByText("Needs a transcript.")).toBeInTheDocument();
    expect(within(actions).getByText("brand_new_reason")).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: "Export" })).toBeEnabled();
    fireEvent.click(within(actions).getByRole("button", { name: "Export" }));
    expect(onOpenPanel).toHaveBeenCalledWith("export");
    fireEvent.click(within(actions).getByRole("button", { name: "Correct transcript" }));
    expect(onOpenPanel).toHaveBeenCalledTimes(1);
  });

  it("maps each available review action to its panel", () => {
    const { onOpenPanel } = renderReport(callReport());
    const actions = region("Review actions");
    for (const name of ["Correct transcript", "Rename speakers", "Edit action items", "Share", "Export"]) {
      fireEvent.click(within(actions).getByRole("button", { name }));
    }
    expect(onOpenPanel.mock.calls.map(([panel]) => panel)).toEqual(["transcript", "transcript", "actions", "shares", "export"]);
  });

  it("uses the Persian headings and keeps times left to right", () => {
    renderReport(callReport({ completeness: { ...callReport().completeness, is_complete: false, notices: ["analysis_partial"] } }), { lang: "fa" });
    for (const name of ["کامل بودن گزارش", "خلاصه", "نتیجه", "یافته‌های کنترل کیفیت", "اقدام‌های بازبینی"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
    expect(within(screen.getByRole("region", { name: "کامل بودن گزارش" })).getByText("تحلیل ناقص است.")).toBeInTheDocument();
    const time = within(screen.getByRole("region", { name: "یافته‌های کنترل کیفیت" })).getByText("۰:۰۵–۰:۰۹");
    expect(time.closest("bdi")).toHaveAttribute("dir", "ltr");
  });
});
