import { afterEach, describe, expect, it, vi } from "vitest";
import { act, screen, within } from "@testing-library/react";

import VoiceCapacityCard from "@/components/admin/overview/VoiceCapacityCard";
import api from "@/lib/api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { VoiceCapacityResponse } from "@/types/voice";

// The card waits for its read; a loaded machine needs more than 1 s.
const LOAD = { timeout: 5000 };

function capacity(held: number, limit: number): VoiceCapacityResponse {
  return { workspace: { workspace_id: "ws-1", held, limit } };
}

/** Serves GET /voice/capacity; anything else is a 404. */
function mockCapacity(answer: VoiceCapacityResponse | Error | "pending") {
  return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url !== "/voice/capacity") throw apiError(404, "not_found");
    if (answer === "pending") return new Promise(() => undefined);
    if (answer instanceof Error) throw answer;
    return okResponse(answer);
  });
}

function capacityCalls(get: ReturnType<typeof mockCapacity>) {
  return get.mock.calls.filter(([url]) => url === "/voice/capacity").length;
}

function renderCard(lang: "fa" | "en" = "en") {
  if (lang === "fa") window.localStorage.removeItem("homayar_lang");
  // A workspace admin who is not a platform operator: the card is for every admin.
  const workspace = { ...FIXTURE_WORKSPACE, role: "admin" as const };
  return renderConsole(<VoiceCapacityCard />, {
    lang,
    auth: { status: "authenticated", user: FIXTURE_USER, workspace, workspaces: [workspace] },
  });
}

describe("VoiceCapacityCard", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows held / limit with a meter for an admin who is not an operator", async () => {
    const get = mockCapacity(capacity(0, 200));
    renderCard();
    const card = screen.getByRole("region", { name: "Voice capacity" });
    expect(await within(card).findByText("0 / 200", undefined, LOAD)).toBeInTheDocument();
    const meter = within(card).getByRole("meter", { name: "Concurrent calls" });
    expect(meter).toHaveAttribute("aria-valuenow", "0");
    expect(meter).toHaveAttribute("aria-valuemin", "0");
    expect(meter).toHaveAttribute("aria-valuemax", "100");
    expect(meter).not.toHaveClass("meter--warning");
    expect(get).toHaveBeenCalledWith("/voice/capacity");
  });

  it("says voice calls are off, with no meter, when the limit is 0", async () => {
    mockCapacity(capacity(0, 0));
    renderCard();
    const card = screen.getByRole("region", { name: "Voice capacity" });
    expect(await within(card).findByText("Voice calls are off for this workspace.", undefined, LOAD)).toBeInTheDocument();
    expect(within(card).queryByRole("meter")).not.toBeInTheDocument();
    expect(within(card).queryByText("0 / 0")).not.toBeInTheDocument();
  });

  it("turns the meter to warning at 80 percent, not at 79.5", async () => {
    mockCapacity(capacity(159, 200));
    const below = renderCard();
    const quiet = await screen.findByRole("meter", { name: "Concurrent calls" }, LOAD);
    expect(quiet).toHaveAttribute("aria-valuenow", "79.5");
    expect(quiet).not.toHaveClass("meter--warning");
    below.unmount();
    vi.restoreAllMocks();

    mockCapacity(capacity(160, 200));
    renderCard();
    const warning = await screen.findByRole("meter", { name: "Concurrent calls" }, LOAD);
    expect(warning).toHaveAttribute("aria-valuenow", "80");
    expect(warning).toHaveClass("meter--warning");
    expect(screen.getByText("160 / 200")).toBeInTheDocument();
  });

  it("caps the meter at 100 percent", async () => {
    mockCapacity(capacity(250, 200));
    renderCard();
    expect(await screen.findByRole("meter", { name: "Concurrent calls" }, LOAD)).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText("250 / 200")).toBeInTheDocument();
  });

  it("is busy while loading and shows the card's error with no retry button when the request fails", async () => {
    mockCapacity("pending");
    const loading = renderCard();
    expect(screen.getByRole("region", { name: "Voice capacity" })).toHaveAttribute("aria-busy", "true");
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    loading.unmount();
    vi.restoreAllMocks();

    mockCapacity(apiError(500, "internal_error"));
    renderCard();
    const card = screen.getByRole("region", { name: "Voice capacity" });
    expect(await within(card).findByRole("alert", undefined, LOAD)).toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).getByRole("heading", { level: 2, name: "Voice capacity" })).toBeInTheDocument();
  });

  it("asks again every 15 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const get = mockCapacity(capacity(1, 200));
    renderCard();
    expect(await screen.findByText("1 / 200", undefined, LOAD)).toBeInTheDocument();
    expect(capacityCalls(get)).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_000);
    });
    expect(capacityCalls(get)).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });
    expect(capacityCalls(get)).toBe(2);
  });

  it("uses Persian text and Persian digits", async () => {
    mockCapacity(capacity(0, 200));
    const on = renderCard("fa");
    const card = screen.getByRole("region", { name: "ظرفیت تماس صوتی" });
    expect(await within(card).findByText("۰ / ۲۰۰", undefined, LOAD)).toBeInTheDocument();
    expect(within(card).getByRole("meter", { name: "تماس‌های هم‌زمان" })).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/[0-9]/);
    on.unmount();
    vi.restoreAllMocks();

    mockCapacity(capacity(0, 0));
    renderCard("fa");
    expect(await screen.findByText("تماس صوتی برای این فضای کاری خاموش است.", undefined, LOAD)).toBeInTheDocument();
  });
});
