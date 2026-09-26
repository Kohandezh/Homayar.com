import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import api from "@/lib/api";
import { formatAudioSeconds } from "@/lib/format";
import AllowanceNotice from "@/components/platform/AllowanceNotice";
import OverviewPage from "@/components/platform/OverviewPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, okResponse, renderConsole } from "@/test/fixtures";
import type { BillingUsageSnapshot } from "@/types/platform";

const T = "2026-09-24T00:00:00Z";

function audio(extra: Partial<BillingUsageSnapshot> = {}): BillingUsageSnapshot {
  return {
    metric: "audio_seconds",
    source: "demo",
    limit: 1800,
    used: 1,
    reserved: 0,
    remaining: 1799,
    period_start: T,
    period_end: null,
    updated_at: T,
    ...extra,
  };
}

describe("audio allowance formatting", () => {
  it("never rounds remaining time up", () => {
    expect(formatAudioSeconds(1799, "en")).toBe("29 min 59 s");
    expect(formatAudioSeconds(1800, "en")).toBe("30 min");
    expect(formatAudioSeconds(59.9, "en")).toBe("59 s");
    expect(formatAudioSeconds(1799, "fa")).toBe("۲۹ دقیقه و ۵۹ ثانیه");
  });
});

describe("AllowanceNotice", () => {
  it("states the total trial allowance and what reserved time means", () => {
    renderConsole(<AllowanceNotice item={audio({ used: 600, reserved: 90, remaining: 1110 })} />, { lang: "en" });
    expect(screen.getByText("Allowance: 30 min of audio processing in total.")).toBeInTheDocument();
    expect(screen.getByText(/1 min 30 s is held for recordings still being processed/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers contact sales without prices when the allowance is used up", () => {
    renderConsole(<AllowanceNotice item={audio({ used: 1800, remaining: 0 })} />, { lang: "en" });
    expect(screen.getByRole("alert")).toHaveTextContent("The audio processing allowance is used up");
    expect(screen.getByRole("link", { name: "Contact sales to extend it" })).toHaveAttribute("href", "/contact");
    expect(document.body.textContent).not.toMatch(/\$|price|تومان|ریال/i);
  });

  it("stays silent for unlimited or non-audio metrics", () => {
    const { container } = renderConsole(<AllowanceNotice item={audio({ limit: null, remaining: null })} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("OverviewPage usage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  function mockMeetings(usage = audio()) {
    return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      if (url === "/meetings") return okResponse({ items: [], next_before: null });
      if (url === "/billing/usage") return okResponse({ workspace_id: "ws-1", items: [usage] });
      throw new Error(`unexpected ${url}`);
    });
  }

  it("shows the server's usage to owners in floored minutes and seconds", async () => {
    mockMeetings(audio({ used: 1741, remaining: 59 }));
    renderConsole(<OverviewPage />, {
      lang: "en",
      auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] },
    });
    expect(await screen.findAllByText("29 min 1 s")).not.toHaveLength(0);
    expect(screen.getAllByText(/59 s/).length).toBeGreaterThan(0);
  });

  it("never requests billing data for members and hides upload from viewers", async () => {
    const get = mockMeetings();
    const { unmount } = renderConsole(<OverviewPage />, {
      lang: "en",
      auth: { status: "authenticated", user: { ...FIXTURE_USER, role: "member" } },
    });
    expect(screen.getAllByText("Plan usage is visible to workspace owners and administrators.").length).toBeGreaterThan(0);
    expect(get.mock.calls.map(([url]) => url)).not.toContain("/billing/usage");
    expect(screen.getByRole("link", { name: "Upload meeting" })).toBeInTheDocument();
    unmount();
    renderConsole(<OverviewPage />, { lang: "en", auth: { status: "authenticated", user: { ...FIXTURE_USER, role: "viewer" } } });
    expect(screen.queryByRole("link", { name: "Upload meeting" })).not.toBeInTheDocument();
  });
});
