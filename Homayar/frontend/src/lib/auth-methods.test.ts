import { afterEach, describe, expect, it, vi } from "vitest";
import api from "@/lib/api";
import { getAuthMethods } from "@/lib/auth-methods";
import { okResponse } from "@/test/fixtures";

describe("getAuthMethods", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("counts sign-up as open unless the server explicitly closes it", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue(okResponse({ password: true }));
    expect(await getAuthMethods()).toMatchObject({ password: true, registration: true });
    expect(get).toHaveBeenCalledWith("/auth/methods");
  });

  it("reports registration false only when the server says false", async () => {
    vi.spyOn(api, "get").mockResolvedValue(
      okResponse({ password: true, registration: false }),
    );
    expect(await getAuthMethods()).toMatchObject({ registration: false });
  });
});
