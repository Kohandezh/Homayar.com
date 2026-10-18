import { AxiosError, AxiosHeaders, type AxiosResponse } from "axios";
import { afterEach, describe, expect, it, vi } from "vitest";

import api from "@/lib/api";
import { apiErrorInfo } from "@/lib/api-errors";
import {
  dueBeforeFor,
  exportSubjectData,
  listLegalHolds,
  listMeetingMedia,
  placeMeetingLegalHold,
  releaseLegalHold,
  reviewAtFromDay,
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
});
