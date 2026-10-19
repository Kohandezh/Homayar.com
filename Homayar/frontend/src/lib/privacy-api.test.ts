import { AxiosError, AxiosHeaders, type AxiosRequestConfig, type AxiosResponse } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { createElement } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import api from "@/lib/api";
import { apiErrorInfo } from "@/lib/api-errors";
import { apiError } from "@/test/fixtures";
import {
  dueBeforeFor,
  exportSubjectData,
  listAdminShares,
  listLegalHolds,
  listMeetingMedia,
  placeMeetingLegalHold,
  releaseLegalHold,
  reviewAtFromDay,
  useAdminShares,
  useRevokeAdminShare,
} from "@/lib/privacy-api";
import { okResponse } from "@/test/fixtures";

/** An axios error whose body is a Blob, the way `responseType: "blob"` delivers it. */
function blobError(status: number, body: unknown): AxiosError {
  const config = { headers: new AxiosHeaders() };
  const response: AxiosResponse = {
    status,
    statusText: String(status),
    headers: new AxiosHeaders({ "content-type": "application/json" }),
    config,
    data: new Blob([JSON.stringify(body)], { type: "application/json" }),
  };
  return new AxiosError(`Request failed with status code ${status}`, "ERR_BAD_REQUEST", config, {}, response);
}

describe("privacy API", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("posts the export as a blob request and names the file from Content-Disposition", async () => {
    const blob = new Blob(["{}"], { type: "application/json" });
    const post = vi.spyOn(api, "post").mockResolvedValue({
      ...okResponse(blob),
      headers: new AxiosHeaders({
        "content-disposition": 'attachment; filename="homayar-subject-export-20261018T101010Z.json"',
        "content-type": "application/json",
      }),
    });

    const payload = await exportSubjectData({ identifiers: ["a@example.com"], reason: "court order" });

    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      "/privacy/subject-exports",
      { identifiers: ["a@example.com"], reason: "court order" },
      { responseType: "blob" },
    );
    expect(payload.blob).toBe(blob);
    expect(payload.filename).toBe("homayar-subject-export-20261018T101010Z.json");
  });

  it("falls back to a fixed file name without Content-Disposition", async () => {
    vi.spyOn(api, "post").mockResolvedValue(okResponse(new Blob(["{}"])));
    const payload = await exportSubjectData({ identifiers: ["a@example.com"], reason: "court order" });
    expect(payload.filename).toBe("homayar-subject-export.json");
  });

  it("reads a Blob error body as JSON, so the error code is known", async () => {
    vi.spyOn(api, "post").mockRejectedValue(
      blobError(422, { error: { code: "invalid_subject_identifier", message: "x", request_id: "r-1" } }),
    );
    const error = await exportSubjectData({ identifiers: ["Ali"], reason: "court order" }).catch(
      (caught: unknown) => caught,
    );
    expect(apiErrorInfo(error).code).toBe("invalid_subject_identifier");
    expect(apiErrorInfo(error).status).toBe(422);
  });

  it("keeps an error whose Blob body is not JSON as it is", async () => {
    const config = { headers: new AxiosHeaders() };
    const raw = new AxiosError("Bad gateway", "ERR_BAD_RESPONSE", config, {}, {
      status: 502,
      statusText: "502",
      headers: new AxiosHeaders(),
      config,
      data: new Blob(["<html>bad gateway</html>"], { type: "text/html" }),
    });
    vi.spyOn(api, "post").mockRejectedValue(raw);
    const error = await exportSubjectData({ identifiers: ["a@example.com"], reason: "court order" }).catch(
      (caught: unknown) => caught,
    );
    expect(apiErrorInfo(error).status).toBe(502);
    expect(apiErrorInfo(error).code).toBeNull();
  });

  it("lists legal holds with the filter and the cursor", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(okResponse({ items: [], next_cursor: null }));
    await listLegalHolds({ limit: 50, due_before: "2026-10-18T10:00:00.000Z", cursor: "c-2" });
    expect(get).toHaveBeenCalledWith("/admin/legal-holds", {
      params: { limit: 50, due_before: "2026-10-18T10:00:00.000Z", cursor: "c-2" },
    });
  });

  it("reads, holds and releases media objects on the documented paths", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(okResponse({ items: [] }));
    const post = vi.spyOn(api, "post").mockResolvedValue(okResponse({ items: [] }));
    await listMeetingMedia("m-1");
    expect(get).toHaveBeenCalledWith("/meetings/m-1/media");
    await placeMeetingLegalHold("m-1", { reason: "litigation", review_at: "2027-01-01T00:00:00.000Z" });
    expect(post).toHaveBeenCalledWith("/meetings/m-1/legal-hold", {
      reason: "litigation",
      review_at: "2027-01-01T00:00:00.000Z",
    });
    await releaseLegalHold("media-1", { reason: "matter closed" });
    expect(post).toHaveBeenCalledWith("/media/media-1/legal-hold/release", { reason: "matter closed" });
  });

  it("turns a filter into due_before: none, now, or now plus 30 days", () => {
    const now = new Date("2026-10-18T10:00:00.000Z");
    expect(dueBeforeFor("all", now)).toBeUndefined();
    expect(dueBeforeFor("review_due", now)).toBe("2026-10-18T10:00:00.000Z");
    expect(dueBeforeFor("due_30_days", now)).toBe("2026-11-17T10:00:00.000Z");
  });

  it("sends the chosen day at midnight UTC with a Z", () => {
    expect(reviewAtFromDay("2027-01-16")).toBe("2027-01-16T00:00:00.000Z");
  });

  it("lists the workspace's share links with the chosen params", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(okResponse({ items: [], next_cursor: null }));
    await listAdminShares({ status: "active", limit: 50, cursor: "c-2" });
    expect(get).toHaveBeenCalledWith("/admin/shares", {
      params: { status: "active", limit: 50, cursor: "c-2" },
    });
  });

  it("reads pages through useAdminShares: the status only when chosen, the cursor only from page two", async () => {
    const get = vi.spyOn(api, "get").mockImplementation(
      async (_url: string, config?: AxiosRequestConfig) => {
        const cursor = (config?.params as { cursor?: string } | undefined)?.cursor;
        return okResponse(
          cursor === "c-2" ? { items: [], next_cursor: null } : { items: [], next_cursor: "c-2" },
        );
      },
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const hook = renderHook(() => useAdminShares("revoked"), { wrapper });
    await act(async () => {
      await vi.waitFor(() => expect(hook.result.current.isSuccess).toBe(true));
    });
    await act(async () => {
      await hook.result.current.fetchNextPage();
    });
    expect(get).toHaveBeenNthCalledWith(
      1,
      "/admin/shares",
      expect.objectContaining({ params: { limit: 50, status: "revoked" } }),
    );
    expect(get).toHaveBeenNthCalledWith(
      2,
      "/admin/shares",
      expect.objectContaining({ params: { limit: 50, status: "revoked", cursor: "c-2" } }),
    );
    expect(get).toHaveBeenCalledTimes(2);

    const unfiltered = renderHook(() => useAdminShares(undefined), { wrapper });
    await act(async () => {
      await vi.waitFor(() => expect(unfiltered.result.current.isSuccess).toBe(true));
    });
    expect(get).toHaveBeenLastCalledWith("/admin/shares", expect.objectContaining({ params: { limit: 50 } }));
  });

  it("revokes through the meeting route and refreshes the register and the meeting's shares", async () => {
    const remove = vi.spyOn(api, "delete").mockResolvedValue(okResponse(undefined, 204));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const hook = renderHook(() => useRevokeAdminShare(), { wrapper });
    await act(async () => {
      await hook.result.current.mutateAsync({ meetingId: "meeting-1", shareId: "share-1" });
    });

    expect(remove).toHaveBeenCalledWith("/meetings/meeting-1/shares/share-1");
    const invalidations = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(invalidations).toContain(JSON.stringify(["platform", "admin", "shares"]));
    expect(invalidations).toContain(JSON.stringify(["platform", "meetings", "meeting-1", "shares"]));
  });

  it("refreshes the lists on a 404 too, because the register is stale", async () => {
    vi.spyOn(api, "delete").mockRejectedValue(apiError(404, "share_not_found"));
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children);

    const hook = renderHook(() => useRevokeAdminShare(), { wrapper });
    await act(async () => {
      await hook.result.current
        .mutateAsync({ meetingId: "meeting-1", shareId: "share-1" })
        .catch((caught: unknown) => caught);
    });
    await act(async () => {
      await vi.waitFor(() => expect(hook.result.current.error).not.toBeNull());
    });

    expect(apiErrorInfo(hook.result.current.error).code).toBe("share_not_found");
    const invalidations = invalidate.mock.calls.map((call) => JSON.stringify(call[0]?.queryKey));
    expect(invalidations).toContain(JSON.stringify(["platform", "admin", "shares"]));
    expect(invalidations).toContain(JSON.stringify(["platform", "meetings", "meeting-1", "shares"]));
  });
});
