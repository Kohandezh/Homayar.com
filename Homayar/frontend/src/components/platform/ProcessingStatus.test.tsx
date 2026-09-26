import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import api from "@/lib/api";
import {
  MAX_POLL_INTERVAL_MS,
  PLATFORM_POLL_INTERVAL_MS,
  meetingRefetchInterval,
  platformQueryKeys,
  pollDelay,
  pollDelayAfterError,
} from "@/lib/platform-api";
import ProcessingStatus from "@/components/platform/ProcessingStatus";
import MeetingDetailPage from "@/components/platform/MeetingDetailPage";
import { FIXTURE_USER, apiError, networkError, okResponse, renderConsole, testQueryClient } from "@/test/fixtures";
import type { MeetingDetail, ProcessingJob, StageRun } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

function stage(stage: string, state: StageRun["state"], extra: Partial<StageRun> = {}): StageRun {
  return {
    stage,
    state,
    reason_code: null,
    reason_message: null,
    provider: null,
    model: null,
    is_mock: false,
    latency_ms: null,
    started_at: T,
    finished_at: null,
    ...extra,
  };
}

function job(extra: Partial<ProcessingJob> = {}): ProcessingJob {
  return {
    id: "j-1",
    state: "running",
    stage: "asr",
    attempt_count: 1,
    max_attempts: 3,
    failure_code: null,
    failure_message: null,
    created_at: T,
    updated_at: T,
    stages: [],
    ...extra,
  };
}

function meeting(extra: Partial<MeetingDetail> = {}): MeetingDetail {
  return {
    id: "m-1",
    workspace_id: "ws-1",
    title: "Weekly call",
    language: "fa",
    status: "transcribing",
    duration_seconds: 125,
    source: "file_upload",
    created_at: T,
    updated_at: T,
    recording: null,
    latest_job: job(),
    ...extra,
  };
}

describe("polling rules", () => {
  it("slows down while nothing changes and stops on terminal states", () => {
    expect(pollDelay(0)).toBe(PLATFORM_POLL_INTERVAL_MS);
    expect(pollDelay(60_000)).toBe(5_000);
    expect(pollDelay(300_000)).toBe(15_000);
    expect(pollDelay(3_600_000)).toBe(MAX_POLL_INTERVAL_MS);
    expect(meetingRefetchInterval({ status: "queued" }, 300_000)).toBe(15_000);
    expect(meetingRefetchInterval({ status: "partial" }, 0)).toBe(false);
    expect(meetingRefetchInterval({ status: "cancelled" }, 0)).toBe(false);
  });

  it("stops after 401/403/404/410 and otherwise honours Retry-After", () => {
    expect(pollDelayAfterError(apiError(404, "meeting_not_found"))).toBe(false);
    expect(pollDelayAfterError(apiError(403, "workspace_access_denied"))).toBe(false);
    expect(pollDelayAfterError(apiError(401, "authentication_required"))).toBe(false);
    expect(pollDelayAfterError(apiError(503, "x", { headers: { "Retry-After": "60" } }))).toBe(60_000);
    expect(pollDelayAfterError(networkError())).toBe(15_000);
  });
});

function renderStatus(detail: MeetingDetail, canReplay = false) {
  return renderConsole(<ProcessingStatus meeting={detail} canReplay={canReplay} />, {
    lang: "en",
    auth: { status: "authenticated", user: FIXTURE_USER },
  });
}

describe("ProcessingStatus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows the real stage states, not-started stages and no invented percentage", () => {
    renderStatus(
      meeting({
        latest_job: job({ stages: [stage("materialize", "completed"), stage("asr", "running")] }),
      }),
    );
    expect(screen.getByText("Now running: Speech to text.")).toBeInTheDocument();
    const rows = screen.getAllByRole("listitem");
    expect(rows.map((row) => row.textContent)).toEqual([
      "Preparing the recordingCompleted",
      "Speech to textRunning",
      "Separating speakersNot started",
      "Summary and analysisNot started",
    ]);
    expect(document.body.textContent).not.toMatch(/%|٪/);
  });

  it("explains a scheduled automatic retry with the failed attempt's code", () => {
    renderStatus(
      meeting({
        status: "queued",
        latest_job: job({
          state: "queued",
          stage: "retry_scheduled",
          attempt_count: 2,
          failure_code: "provider_timeout",
          retry_after: "2026-09-24T12:05:00Z",
        }),
      }),
    );
    expect(screen.getByText(/The last attempt failed \(provider_timeout\)\./)).toBeInTheDocument();
    expect(screen.getByText(/The next automatic attempt is scheduled for/)).toBeInTheDocument();
    expect(screen.getByText("Attempt 2 of 3")).toBeInTheDocument();
  });

  it("discloses degraded and mock stages with their reasons", () => {
    renderStatus(
      meeting({
        status: "partial",
        latest_job: job({
          state: "completed",
          stage: "completed",
          stages: [
            stage("asr", "completed", { is_mock: true }),
            stage("diarization", "unavailable", { reason_code: "diarization_unavailable" }),
          ],
        }),
      }),
    );
    expect(screen.getByText("Processing finished with gaps; the stages below show what is missing.")).toBeInTheDocument();
    expect(screen.getByText("Speaker separation is not available for this recording.")).toBeInTheDocument();
    expect(screen.getByText("Test (mock) provider output — not a real analysis")).toBeInTheDocument();
    expect(screen.queryByText("Not started")).not.toBeInTheDocument();
  });

  it("offers no retry for a permanent failure", () => {
    renderStatus(
      meeting({
        status: "failed",
        latest_job: job({ state: "failed", failure_code: "unsupported_media", failure_class: "permanent" }),
      }),
      true,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("trying again would fail the same way");
    expect(screen.queryByRole("button", { name: "Retry processing" })).not.toBeInTheDocument();
  });

  it("lets an administrator replay a dead-lettered job and explains refusals", async () => {
    const post = vi.spyOn(api, "post").mockRejectedValueOnce(apiError(409, "replay_limit_reached"));
    renderStatus(
      meeting({ status: "failed", latest_job: job({ state: "dead_letter", failure_code: "attempts_exhausted" }) }),
      true,
    );
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry processing" }));
    });
    expect(post).toHaveBeenCalledWith("/admin/jobs/j-1/replay");
    expect(await screen.findByText("The retry limit for this job has been reached.")).toBeInTheDocument();
  });

  it("tells other roles who can retry", () => {
    renderStatus(meeting({ status: "failed", latest_job: job({ state: "dead_letter" }) }), false);
    expect(screen.queryByRole("button", { name: "Retry processing" })).not.toBeInTheDocument();
    expect(screen.getByText("A workspace owner or administrator can retry processing.")).toBeInTheDocument();
  });
});

describe("MeetingDetailPage lifecycle", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("clears cached content and stops when the meeting was deleted", async () => {
    const get = vi.spyOn(api, "get").mockRejectedValue(apiError(404, "meeting_not_found"));
    const queryClient = testQueryClient();
    queryClient.setQueryData(platformQueryKeys.transcript("m-1"), { segments: [{ text: "protected" }] });
    renderConsole(
      <Routes>
        <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
      </Routes>,
      { route: "/meetings/m-1", lang: "en", queryClient, auth: { status: "authenticated", user: FIXTURE_USER } },
    );
    expect(await screen.findByText(/This meeting was deleted or you no longer have access to it\./)).toBeInTheDocument();
    expect(queryClient.getQueryData(platformQueryKeys.transcript("m-1"))).toBeUndefined();
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("says when an upload was an idempotent replay of an earlier one", async () => {
    vi.spyOn(api, "get").mockResolvedValue(okResponse(meeting({ status: "queued", latest_job: job({ state: "queued", stage: "queued" }) })));
    renderConsole(
      <Routes>
        <Route path="meetings/:meetingId" element={<MeetingDetailPage />} />
      </Routes>,
      {
        route: { pathname: "/meetings/m-1", state: { uploadReplay: true } },
        lang: "en",
        auth: { status: "authenticated", user: FIXTURE_USER },
      },
    );
    expect(await screen.findByText("This recording had already been received; this is the existing meeting.")).toBeInTheDocument();
    expect(screen.getByText("Waiting in the processing queue.")).toBeInTheDocument();
  });
});
