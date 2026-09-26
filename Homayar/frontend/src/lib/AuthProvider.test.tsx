import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import api from "@/lib/api";
import AuthProvider from "@/lib/AuthProvider";
import { useAuth } from "@/lib/auth-context";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, networkError, okResponse, testQueryClient } from "@/test/fixtures";

function Probe() {
  const { status, refreshUser, retrySession } = useAuth();
  return (
    <>
      <p>status: {status}</p>
      <button type="button" onClick={retrySession}>retry</button>
      <button type="button" onClick={() => void refreshUser().catch(() => undefined)}>refresh</button>
    </>
  );
}

function renderProvider() {
  return render(
    <QueryClientProvider client={testQueryClient()}>
      <AuthProvider>
        <Probe />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

const SESSION = okResponse({
  user: { ...FIXTURE_USER, role: undefined },
  workspaces: [FIXTURE_WORKSPACE],
  access_expires_in: 900,
  requires_verification: false,
});

describe("AuthProvider: an outage is not a sign-out", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("reports an unreachable API as unavailable and recovers on retry, keeping the workspace", async () => {
    window.localStorage.setItem("homayar_workspace_id", FIXTURE_WORKSPACE.id);
    const get = vi.spyOn(api, "get").mockRejectedValueOnce(apiError(502, "bad_gateway")).mockResolvedValueOnce(SESSION);
    renderProvider();
    expect(await screen.findByText("status: unavailable")).toBeInTheDocument();
    expect(window.localStorage.getItem("homayar_workspace_id")).toBe(FIXTURE_WORKSPACE.id);
    fireEvent.click(screen.getByRole("button", { name: "retry" }));
    expect(await screen.findByText("status: authenticated")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);
  });

  it("treats a network failure the same way, and only a 401 as signed out", async () => {
    vi.spyOn(api, "get").mockRejectedValueOnce(networkError());
    const first = renderProvider();
    expect(await screen.findByText("status: unavailable")).toBeInTheDocument();
    first.unmount();

    vi.spyOn(api, "get").mockRejectedValueOnce(apiError(401, "authentication_required"));
    renderProvider();
    expect(await screen.findByText("status: anonymous")).toBeInTheDocument();
  });

  it("keeps the session when a refresh meets an outage", async () => {
    vi.spyOn(api, "get").mockResolvedValueOnce(SESSION).mockRejectedValueOnce(apiError(503, "unavailable"));
    renderProvider();
    expect(await screen.findByText("status: authenticated")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "refresh" }));
    });
    await waitFor(() => expect(api.get).toHaveBeenCalledTimes(2));
    expect(screen.getByText("status: authenticated")).toBeInTheDocument();
  });
});
