import { describe, expect, it } from "vitest";
import api, {
  API_BASE_URL,
  getApiUrl,
  getCsrfToken,
  WORKSPACE_STORAGE_KEY,
} from "@/lib/api";

describe("API client", () => {
  it("uses the versioned API with cookie credentials", () => {
    expect(API_BASE_URL).toMatch(/\/api\/v1$/);
    expect(api.defaults.baseURL).toBe(API_BASE_URL);
    expect(api.defaults.withCredentials).toBe(true);
    expect(api.defaults.headers.common.Authorization).toBeUndefined();
    expect(getApiUrl("/auth/google/start")).toBe(`${API_BASE_URL}/auth/google/start`);
  });

  it("copies the readable CSRF cookie to mutation requests", async () => {
    document.cookie = "homayar_csrf=test-csrf-token; path=/";
    expect(getCsrfToken()).toBe("test-csrf-token");

    await api.post(
      "/auth/logout",
      undefined,
      {
        adapter: async (config) => {
          expect(config.headers.get("X-CSRF-Token")).toBe("test-csrf-token");
          return {
            config,
            data: {},
            headers: {},
            status: 200,
            statusText: "OK",
          };
        },
      }
    );
  });

  it("attaches the selected workspace to product requests only", async () => {
    window.localStorage.setItem(WORKSPACE_STORAGE_KEY, "workspace-1");

    await api.get("/meetings", {
      adapter: async (config) => {
        expect(config.headers.get("X-Workspace-ID")).toBe("workspace-1");
        return { config, data: {}, headers: {}, status: 200, statusText: "OK" };
      },
    });
    await api.get("/auth/me", {
      adapter: async (config) => {
        expect(config.headers.get("X-Workspace-ID")).toBeUndefined();
        return { config, data: {}, headers: {}, status: 200, statusText: "OK" };
      },
    });
  });
});
