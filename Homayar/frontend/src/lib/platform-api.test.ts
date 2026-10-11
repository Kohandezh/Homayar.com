import { AxiosError, AxiosHeaders, type AxiosResponse } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "@/lib/api";
import {
  PLATFORM_POLL_INTERVAL_MS,
  acceptInvitation,
  changeMemberRole,
  createAction,
  createShare,
  deleteAction,
  downloadRecording,
  exportMeeting,
  filenameFromContentDisposition,
  getBillingUsage,
  getCurrentSubscription,
  getMeeting,
  getMeetingJob,
  getMeetingSummary,
  getPlanCatalog,
  getReadiness,
  getSharedMeeting,
  getTranscript,
  getWorkspaceSettings,
  inviteMember,
  jobRefetchInterval,
  listActions,
  listAuditEvents,
  listInvitations,
  listMeetings,
  listMembers,
  listShares,
  listUsageEvents,
  meetingRefetchInterval,
  readinessBaseUrl,
  removeMember,
  renameSpeaker,
  revokeInvitation,
  revokeShare,
  searchMeetings,
  updateAction,
  updateWorkspaceSettings,
  uploadMeeting,
} from "@/lib/platform-api";
import type {
  Meeting,
  ProcessingJob,
  UploadAcceptedResponse,
} from "@/types/platform";

function axiosResponse<T>(
  data: T,
  headers: Record<string, string> = {},
): AxiosResponse<T> {
  return {
    data,
    status: 200,
    statusText: "OK",
    headers: new AxiosHeaders(headers),
    config: { headers: new AxiosHeaders() },
  };
}

const timestamp = "2026-08-18T12:00:00Z";

const meeting: Meeting = {
  id: "meeting-1",
  workspace_id: "workspace-1",
  title: "Weekly review",
  language: "fa",
  status: "queued",
  duration_seconds: 120,
  source: "upload",
  created_at: timestamp,
  updated_at: timestamp,
};

const job: ProcessingJob = {
  id: "job-1",
  state: "queued",
  stage: "queued",
  attempt_count: 0,
  max_attempts: 3,
  failure_code: null,
  failure_message: null,
  created_at: timestamp,
  updated_at: timestamp,
  stages: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("platform meeting API", () => {
  it("maps meeting list, detail, and job endpoints and terminal polling", async () => {
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce(axiosResponse({ items: [meeting], next_before: null }))
      .mockResolvedValueOnce(
        axiosResponse({ ...meeting, recording: null, latest_job: job }),
      )
      .mockResolvedValueOnce(axiosResponse(job));

    await listMeetings({ limit: 25, status: "queued" });
    await getMeeting(meeting.id);
    await getMeetingJob(meeting.id);

    expect(get).toHaveBeenNthCalledWith(1, "/meetings", {
      params: { limit: 25, status: "queued" },
    });
    expect(get).toHaveBeenNthCalledWith(2, "/meetings/meeting-1");
    expect(get).toHaveBeenNthCalledWith(3, "/meetings/meeting-1/job");

    expect(meetingRefetchInterval({ status: "analyzing" })).toBe(
      PLATFORM_POLL_INTERVAL_MS,
    );
    expect(meetingRefetchInterval({ status: "completed" })).toBe(false);
    expect(meetingRefetchInterval({ status: "failed" })).toBe(false);
    expect(jobRefetchInterval({ state: "running" })).toBe(PLATFORM_POLL_INTERVAL_MS);
    expect(jobRefetchInterval({ state: "dead_letter" })).toBe(false);
  });

  it("uploads binary files as multipart with an idempotency header", async () => {
    const response: UploadAcceptedResponse = {
      meeting,
      job,
      recording: {
        id: "recording-1",
        original_filename: "meeting.wav",
        media_type: "audio/wav",
        container_format: "wav",
        size_bytes: 5,
        checksum_sha256: "a".repeat(64),
        duration_seconds: 120,
        channel_count: 1,
        sample_rate_hz: 16_000,
      },
      usage: {
        metric: "audio_seconds",
        limit: 3_600,
        used: 0,
        reserved: 120,
        remaining: 3_480,
      },
      idempotent_replay: false,
    };
    const post = vi.spyOn(api, "post").mockResolvedValue(axiosResponse(response));
    const file = new File(["audio"], "meeting.wav", { type: "audio/wav" });

    await uploadMeeting({
      file,
      idempotencyKey: "upload-request-123",
      title: "Weekly review",
      language: "fa",
    });

    expect(post).toHaveBeenCalledOnce();
    const [path, body, config] = post.mock.calls[0];
    expect(path).toBe("/uploads");
    expect(body).toBeInstanceOf(FormData);
    const form = body as FormData;
    expect(form.get("file")).toBeInstanceOf(File);
    expect((form.get("file") as File).name).toBe("meeting.wav");
    expect(form.get("title")).toBe("Weekly review");
    expect(form.get("language")).toBe("fa");
    expect(config).toEqual({ headers: { "Idempotency-Key": "upload-request-123" } });
    expect(JSON.stringify(body)).not.toContain("base64");
  });

  it("uses blob responses for recordings and exports", async () => {
    const recording = new Blob(["audio"], { type: "audio/wav" });
    const report = new Blob(["report"], { type: "text/plain" });
    const get = vi
      .spyOn(api, "get")
      .mockResolvedValueOnce(
        axiosResponse(recording, {
          "content-disposition":
            "attachment; filename=recording.wav; filename*=UTF-8''team%20meeting.wav",
          "content-type": "audio/wav",
        }),
      )
      .mockResolvedValueOnce(
        axiosResponse(report, {
          "content-disposition": 'attachment; filename="homayar-meeting-1.txt"',
          "content-type": "text/plain; charset=utf-8",
        }),
      );

    const recordingDownload = await downloadRecording("meeting-1");
    const exportDownload = await exportMeeting("meeting-1", "txt");

    expect(get).toHaveBeenNthCalledWith(1, "/meetings/meeting-1/recording", {
      responseType: "blob",
    });
    expect(get).toHaveBeenNthCalledWith(2, "/meetings/meeting-1/export", {
      params: { format: "txt" },
      responseType: "blob",
    });
    expect(recordingDownload).toEqual({
      blob: recording,
      filename: "team meeting.wav",
      contentType: "audio/wav",
    });
    expect(exportDownload.filename).toBe("homayar-meeting-1.txt");
    expect(filenameFromContentDisposition(undefined, "fallback.txt")).toBe("fallback.txt");
  });
});

describe("meeting intelligence and sharing API", () => {
  it("maps transcript, summary, actions, speaker, and search contracts", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({}));
    const post = vi.spyOn(api, "post").mockResolvedValue(axiosResponse({}));
    const patch = vi.spyOn(api, "patch").mockResolvedValue(axiosResponse({}));
    const remove = vi.spyOn(api, "delete").mockResolvedValue(axiosResponse(undefined));

    await getTranscript("meeting-1");
    await getMeetingSummary("meeting-1");
    await listActions("meeting-1");
    await createAction("meeting-1", { description: "Send proposal", priority: "high" });
    await updateAction("meeting-1", "action-1", { status: "done" });
    await deleteAction("meeting-1", "action-1");
    await renameSpeaker("meeting-1", "speaker-1", "Customer");
    await searchMeetings({ q: "proposal", limit: 10 });

    expect(get).toHaveBeenNthCalledWith(1, "/meetings/meeting-1/transcript");
    expect(get).toHaveBeenNthCalledWith(2, "/meetings/meeting-1/summary");
    expect(get).toHaveBeenNthCalledWith(3, "/meetings/meeting-1/actions");
    expect(get).toHaveBeenNthCalledWith(4, "/search", {
      params: { q: "proposal", limit: 10 },
    });
    expect(post).toHaveBeenCalledWith("/meetings/meeting-1/actions", {
      description: "Send proposal",
      priority: "high",
    });
    expect(patch).toHaveBeenNthCalledWith(1, "/meetings/meeting-1/actions/action-1", {
      status: "done",
    });
    expect(patch).toHaveBeenNthCalledWith(2, "/meetings/meeting-1/speakers/speaker-1", {
      display_name: "Customer",
    });
    expect(remove).toHaveBeenCalledWith("/meetings/meeting-1/actions/action-1");
  });

  it("maps share creation, listing, access, and revocation", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({}));
    const post = vi.spyOn(api, "post").mockResolvedValue(axiosResponse({}));
    const remove = vi.spyOn(api, "delete").mockResolvedValue(axiosResponse(undefined));

    await createShare("meeting-1", {
      expires_in_hours: 48,
      require_authentication: true,
    });
    await listShares("meeting-1");
    await getSharedMeeting("share-token");
    await revokeShare("meeting-1", "share-1");

    expect(post).toHaveBeenCalledWith("/meetings/meeting-1/shares", {
      expires_in_hours: 48,
      require_authentication: true,
    });
    expect(get).toHaveBeenNthCalledWith(1, "/meetings/meeting-1/shares");
    expect(get).toHaveBeenNthCalledWith(2, "/shares/share-token");
    expect(remove).toHaveBeenCalledWith("/meetings/meeting-1/shares/share-1");
  });
});

describe("billing, members, and admin API", () => {
  it("maps all requested billing reads", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({}));

    await getPlanCatalog();
    await getCurrentSubscription();
    await getBillingUsage();
    await listUsageEvents({
      metric: "audio_seconds",
      before: timestamp,
      before_id: "event-2",
    });

    expect(get).toHaveBeenNthCalledWith(1, "/billing/plans");
    expect(get).toHaveBeenNthCalledWith(2, "/billing/subscription");
    expect(get).toHaveBeenNthCalledWith(3, "/billing/usage");
    expect(get).toHaveBeenNthCalledWith(4, "/billing/usage/events", {
      params: {
        metric: "audio_seconds",
        before: timestamp,
        before_id: "event-2",
      },
    });
  });

  it("maps the present member and admin router contracts", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({}));
    const post = vi.spyOn(api, "post").mockResolvedValue(axiosResponse({}));
    const patch = vi.spyOn(api, "patch").mockResolvedValue(axiosResponse({}));
    const remove = vi.spyOn(api, "delete").mockResolvedValue(axiosResponse(undefined));

    await listMembers();
    await listInvitations();
    await inviteMember({ email: "new@example.com", role: "member" });
    await acceptInvitation("a".repeat(32));
    await changeMemberRole("membership-1", "admin");
    await revokeInvitation("invitation-1");
    await removeMember("membership-1");
    await listAuditEvents({ event_type: "meeting.uploaded", limit: 20 });
    await getWorkspaceSettings();
    await updateWorkspaceSettings({ retention_days: 90 });

    expect(get).toHaveBeenNthCalledWith(1, "/members");
    expect(get).toHaveBeenNthCalledWith(2, "/members/invitations");
    expect(get).toHaveBeenNthCalledWith(3, "/admin/audit-events", {
      params: { event_type: "meeting.uploaded", limit: 20 },
    });
    expect(get).toHaveBeenNthCalledWith(4, "/admin/workspace-settings");
    expect(post).toHaveBeenNthCalledWith(1, "/members/invitations", {
      email: "new@example.com",
      role: "member",
    });
    expect(post).toHaveBeenNthCalledWith(2, "/members/invitations/accept", {
      token: "a".repeat(32),
    });
    expect(patch).toHaveBeenNthCalledWith(1, "/members/membership-1", {
      role: "admin",
    });
    expect(patch).toHaveBeenNthCalledWith(2, "/admin/workspace-settings", {
      retention_days: 90,
    });
    expect(remove).toHaveBeenNthCalledWith(1, "/members/invitations/invitation-1");
    expect(remove).toHaveBeenNthCalledWith(2, "/members/membership-1");
  });
});

describe("readiness API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function failure(status: number, data: unknown): AxiosError {
    const config = { headers: new AxiosHeaders() };
    return new AxiosError(`Request failed with status code ${status}`, "ERR_BAD_RESPONSE", config, {}, {
      data,
      status,
      statusText: String(status),
      headers: new AxiosHeaders(),
      config,
    });
  }

  it("asks the server root, not the API prefix", () => {
    expect(readinessBaseUrl("/api/v1")).toBe("");
    expect(readinessBaseUrl("https://host.example/api/v1")).toBe("https://host.example");
    expect(readinessBaseUrl("https://host.example/base/api/v1")).toBe("https://host.example/base");
  });

  it("returns a 200 ready body", async () => {
    const body = { status: "ready", checks: { database: "ready", redis: "ready" } };
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse(body));
    await expect(getReadiness()).resolves.toEqual(body);
    expect(get).toHaveBeenCalledWith("/readyz", { baseURL: "" });
  });

  it("passes the abort signal through", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(axiosResponse({ status: "ready", checks: {} }));
    const signal = new AbortController().signal;
    await getReadiness(signal);
    expect(get).toHaveBeenCalledWith("/readyz", { baseURL: "", signal });
  });

  it("returns the 503 body as a not-ready answer", async () => {
    const body = { status: "not_ready", checks: { database: "ready", redis: "unavailable" } };
    vi.spyOn(api, "get").mockRejectedValue(failure(503, body));
    await expect(getReadiness()).resolves.toEqual(body);
  });

  it("treats any 503 readiness body as not ready, even one that says ready", async () => {
    const checks = { database: "ready", redis: "unavailable" };
    vi.spyOn(api, "get").mockRejectedValue(failure(503, { status: "ready", checks }));
    await expect(getReadiness()).resolves.toEqual({ status: "not_ready", checks });
  });

  it("keeps network errors, other statuses and bad bodies as errors", async () => {
    const get = vi.spyOn(api, "get");
    const network = new AxiosError("Network Error", "ERR_NETWORK", { headers: new AxiosHeaders() }, {});
    get.mockRejectedValueOnce(network);
    await expect(getReadiness()).rejects.toBe(network);

    const serverError = failure(500, { status: "not_ready", checks: {} });
    get.mockRejectedValueOnce(serverError);
    await expect(getReadiness()).rejects.toBe(serverError);

    const badNotReady = failure(503, "<html>Service Unavailable</html>");
    get.mockRejectedValueOnce(badNotReady);
    await expect(getReadiness()).rejects.toBe(badNotReady);

    get.mockResolvedValueOnce(axiosResponse({ checks: { database: "ready" } }));
    await expect(getReadiness()).rejects.toThrow();
    get.mockResolvedValueOnce(axiosResponse({ status: "starting", checks: {} }));
    await expect(getReadiness()).rejects.toThrow();
    get.mockResolvedValueOnce(axiosResponse({ status: "ready" }));
    await expect(getReadiness()).rejects.toThrow();
  });
});
