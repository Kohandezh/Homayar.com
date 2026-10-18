import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { AxiosError, AxiosHeaders, CanceledError, type AxiosRequestConfig } from "axios";
import { Route, Routes } from "react-router";
import api from "@/lib/api";
import { classifyUploadFailure } from "@/lib/upload-errors";
import UploadPanel from "@/components/platform/UploadPanel";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, networkError, okResponse, renderConsole } from "@/test/fixtures";
import type { UploadAcceptedResponse } from "@/types/platform";

describe("classifyUploadFailure", () => {
  it("separates retryable transport failures from refusals of the bytes or the identity", () => {
    expect(classifyUploadFailure(networkError())).toMatchObject({ kind: "network", retry: "same_key" });
    expect(classifyUploadFailure(new CanceledError())).toMatchObject({ kind: "cancelled", retry: "same_key" });
    expect(classifyUploadFailure(apiError(413, "media_file_too_large"))).toMatchObject({ kind: "too_large", retry: "new_file" });
    expect(classifyUploadFailure(apiError(413, "media_duration_exceeded"))).toMatchObject({ kind: "too_long", retry: "new_file" });
    expect(classifyUploadFailure(apiError(422, "extension_content_mismatch"))).toMatchObject({ kind: "unsupported_type" });
    expect(classifyUploadFailure(apiError(422, "missing_audio_stream"))).toMatchObject({ kind: "unreadable", retry: "new_file" });
    expect(classifyUploadFailure(apiError(422, "unsafe_filename"))).toMatchObject({ kind: "bad_filename" });
    expect(classifyUploadFailure(apiError(403, "verified_identity_required"))).toMatchObject({ kind: "verification_required", retry: "none" });
    expect(classifyUploadFailure(apiError(409, "ingestion_conflict"))).toMatchObject({ kind: "conflict", retry: "new_file" });
    expect(classifyUploadFailure(apiError(410, "ingestion_target_deleted"))).toMatchObject({ kind: "target_deleted", retry: "new_file" });
    expect(classifyUploadFailure(apiError(429, "concurrent_job_limit_reached"))).toMatchObject({ kind: "concurrency", retry: "same_key" });
    expect(
      classifyUploadFailure(apiError(429, "upload_rate_limited", { headers: { "Retry-After": "120" } })),
    ).toMatchObject({ kind: "rate_limited", retryAfterSeconds: 120 });
    // The reverse proxy's own 429 carries no API code.
    const proxyLimit = new AxiosError("Request failed with status code 429", "ERR_BAD_REQUEST", { headers: new AxiosHeaders() }, {}, {
      status: 429, statusText: "429", headers: new AxiosHeaders(), config: { headers: new AxiosHeaders() }, data: "<html>429 Too Many Requests</html>",
    });
    expect(classifyUploadFailure(proxyLimit)).toMatchObject({ kind: "rate_limited", retry: "same_key" });
    expect(
      classifyUploadFailure(apiError(503, "media_probe_unavailable", { headers: { "Retry-After": "30" } })),
    ).toMatchObject({ kind: "temporary", retry: "same_key", retryAfterSeconds: 30 });
  });

  it("keeps quota details and the duplicate's meeting id", () => {
    const quota = classifyUploadFailure(
      apiError(429, "audio_quota_exceeded", {
        details: { metric: "audio_seconds", limit: 1800, used: 1500, reserved: 200, requested: 300, remaining: 100 },
      }),
    );
    expect(quota).toMatchObject({ kind: "quota", retry: "none", quota: { remaining: 100, requested: 300, limit: 1800 } });
    const duplicate = classifyUploadFailure(
      apiError(409, "duplicate_media", { details: { ingestion_id: "ing-1", meeting_id: "m-9" } }),
    );
    expect(duplicate).toMatchObject({ kind: "duplicate", meetingId: "m-9" });
  });

  it("treats a proxy 413 without an API code as too large", () => {
    const error = apiError(413, "x");
    (error.response as { data: unknown }).data = "<html>413</html>";
    expect(classifyUploadFailure(error)).toMatchObject({ kind: "too_large" });
  });
});

const ACCEPTED: UploadAcceptedResponse = {
  meeting: {
    id: "m-1",
    workspace_id: "ws-1",
    title: "Call",
    language: "fa",
    status: "queued",
    duration_seconds: 42,
    source: "file_upload",
    created_at: "2026-09-24T12:00:00Z",
    updated_at: "2026-09-24T12:00:00Z",
  },
  recording: {
    id: "r-1",
    original_filename: "call.wav",
    media_type: "audio/wav",
    container_format: "wav",
    size_bytes: 5,
    checksum_sha256: "0".repeat(64),
    duration_seconds: 42,
    channel_count: 1,
    sample_rate_hz: 16000,
  },
  job: {
    id: "j-1",
    state: "queued",
    stage: "queued",
    attempt_count: 0,
    max_attempts: 3,
    failure_code: null,
    failure_message: null,
    created_at: "2026-09-24T12:00:00Z",
    updated_at: "2026-09-24T12:00:00Z",
    stages: [],
  },
  usage: { metric: "audio_seconds", limit: 1800, used: 0, reserved: 42, remaining: 1758 },
  idempotent_replay: false,
};

function renderPanel(user = FIXTURE_USER, workspace = FIXTURE_WORKSPACE) {
  return renderConsole(
    <Routes>
      <Route path="meetings" element={<UploadPanel />} />
      <Route path="meetings/:id" element={<p>Meeting detail</p>} />
    </Routes>,
    { route: "/meetings", lang: "en", auth: { status: "authenticated", user, workspace } },
  );
}

function chooseFile(name = "call.wav") {
  const input = document.querySelector('input[type="file"]') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File(["audio"], name, { type: "audio/wav" })] } });
}

async function submit() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Upload" }));
  });
}

function sentKey(call: unknown[]) {
  return ((call[2] as AxiosRequestConfig).headers as Record<string, string>)["Idempotency-Key"];
}

describe("UploadPanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("gates viewers and unverified accounts without calling the API", () => {
    const post = vi.spyOn(api, "post");
    const { unmount } = renderPanel(FIXTURE_USER, { ...FIXTURE_WORKSPACE, role: "viewer" });
    expect(screen.getByRole("status")).toHaveTextContent("read-only");
    unmount();
    renderPanel({ ...FIXTURE_USER, email_verified: false }, { ...FIXTURE_WORKSPACE, role: "member" });
    expect(screen.getByRole("status")).toHaveTextContent("Verify your email or phone number before uploading.");
    expect(post).not.toHaveBeenCalled();
  });

  it("reports the byte transfer, then server validation, then opens the accepted meeting", async () => {
    let finish: (value: unknown) => void = () => {};
    const post = vi.spyOn(api, "post").mockImplementation((_path, _body, config) => {
      config?.onUploadProgress?.({ loaded: 2, total: 5, bytes: 2, lengthComputable: true });
      return new Promise((resolve) => {
        finish = resolve;
      }) as never;
    });
    vi.spyOn(api, "get").mockResolvedValue(okResponse({ workspace_id: "ws-1", items: [] }));
    renderPanel();
    chooseFile();
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("call");
    await submit();
    expect(screen.getByText("Sending the file: 40%")).toBeInTheDocument();
    expect(screen.getByText(/processing starts after the server accepts it/)).toBeInTheDocument();

    act(() => {
      (post.mock.calls[0][2] as AxiosRequestConfig).onUploadProgress?.({ loaded: 5, total: 5, bytes: 3, lengthComputable: true });
    });
    expect(screen.getByText("File sent. The server is checking the recording…")).toBeInTheDocument();

    await act(async () => {
      finish(okResponse(ACCEPTED, 202));
    });
    expect(await screen.findByText("Meeting detail")).toBeInTheDocument();
    expect(sentKey(post.mock.calls[0])).toMatch(/^web-/);
  });

  it("waits for Retry-After and retries a transient refusal with the same key, never automatically", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValueOnce(apiError(503, "storage_unavailable", { headers: { "Retry-After": "1" } }))
      .mockResolvedValueOnce(okResponse(ACCEPTED, 202));
    renderPanel();
    chooseFile();
    await submit();

    expect(screen.getByRole("alert")).toHaveTextContent("could not accept the file right now");
    const retry = screen.getByRole("button", { name: "Try again" });
    expect(retry).toBeDisabled();
    expect(screen.getByText("You can try again in 0:01.")).toBeInTheDocument();
    await waitFor(() => expect(retry).toBeEnabled(), { timeout: 3000 });
    expect(post).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.click(retry);
    });
    expect(post).toHaveBeenCalledTimes(2);
    expect(sentKey(post.mock.calls[1])).toBe(sentKey(post.mock.calls[0]));
    expect(await screen.findByText("Meeting detail")).toBeInTheDocument();
  });

  it("needs a new file choice (and a new key) after the bytes are refused", async () => {
    const post = vi
      .spyOn(api, "post")
      .mockRejectedValueOnce(apiError(422, "missing_audio_stream"))
      .mockRejectedValueOnce(apiError(422, "missing_audio_stream"));
    renderPanel();
    chooseFile("slides.mp4");
    await submit();
    expect(screen.getByRole("alert")).toHaveTextContent("could not be read as audio");
    expect(screen.getByRole("alert")).toHaveTextContent("Choose the file again");
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();

    chooseFile("call.wav");
    await submit();
    expect(post).toHaveBeenCalledTimes(2);
    expect(sentKey(post.mock.calls[1])).not.toBe(sentKey(post.mock.calls[0]));
  });

  it("explains an exhausted allowance in minutes without inventing a price", async () => {
    vi.spyOn(api, "post").mockRejectedValue(
      apiError(429, "audio_quota_exceeded", {
        details: { metric: "audio_seconds", limit: 1800, used: 1700, reserved: 0, requested: 300, remaining: 100 },
      }),
    );
    vi.spyOn(api, "get").mockResolvedValue(okResponse({ workspace_id: "ws-1", items: [] }));
    renderPanel();
    chooseFile();
    await submit();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Remaining: 1 min 40 s; this recording needs 5 min.");
    expect(screen.getByRole("link", { name: "Contact sales" })).toHaveAttribute("href", "/contact");
    expect(alert).not.toHaveTextContent(/\$|price|تومان/i);
    expect(screen.queryByRole("button", { name: "Try again" })).not.toBeInTheDocument();
  });

  it("links a duplicate recording to the existing meeting", async () => {
    vi.spyOn(api, "post").mockRejectedValue(
      apiError(409, "duplicate_media", { details: { ingestion_id: "ing-1", meeting_id: "m-9" } }),
    );
    vi.spyOn(api, "get").mockResolvedValue(okResponse({ workspace_id: "ws-1", items: [] }));
    renderPanel();
    chooseFile();
    await submit();
    expect(screen.getByRole("link", { name: "Open the existing meeting" })).toHaveAttribute("href", "/meetings/m-9");
  });

  it("shows the server's remaining allowance to billing readers and never asks for it as a member", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(
      okResponse({
        workspace_id: "ws-1",
        items: [
          {
            metric: "audio_seconds",
            source: "demo",
            limit: 1800,
            used: 1000,
            reserved: 90,
            remaining: 710,
            period_start: "2026-09-24T00:00:00Z",
            period_end: null,
            updated_at: "2026-09-24T00:00:00Z",
          },
        ],
      }),
    );
    const { unmount } = renderPanel();
    expect(await screen.findByText("Processing allowance left: 11 min 50 s (1 min 30 s reserved by uploads in progress).")).toBeInTheDocument();
    unmount();
    get.mockClear();
    renderPanel(FIXTURE_USER, { ...FIXTURE_WORKSPACE, role: "member" });
    expect(get).not.toHaveBeenCalled();
    expect(screen.queryByText(/Processing allowance left/)).not.toBeInTheDocument();
  });
});
