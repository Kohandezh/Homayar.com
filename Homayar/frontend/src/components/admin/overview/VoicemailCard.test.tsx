import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";

import VoicemailCard from "@/components/admin/overview/VoicemailCard";
import api from "@/lib/api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { VoicemailAnalytics } from "@/types/voicemail";

// The card waits for its read; a loaded machine needs more than 1 s.
const LOAD = { timeout: 5000 };

// The suite shares the machine with parallel tickets; give the reads room.
vi.setConfig({ testTimeout: 20000 });

function analytics(overrides: Partial<VoicemailAnalytics> = {}): VoicemailAnalytics {
  return {
    received_from: "2026-09-19T00:00:00Z",
    received_to: "2026-10-19T00:00:00Z",
    total: 12,
    by_category: { billing: 7, support: 5 },
    by_priority: { urgent: 3, normal: 9 },
    by_status: { classified: 12 },
    by_follow_up_state: { none: 6, pending: 4, done: 2 },
    by_day: [{ day: "2026-10-18", count: 2 }],
    followed_up: 2,
    median_follow_up_seconds: 272,
    ...overrides,
  };
}

/** Serves GET /voicemails/analytics; anything else is a 404. */
function mockAnalytics(answer: VoicemailAnalytics | Error | "pending") {
  return vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url !== "/voicemails/analytics") throw apiError(404, "not_found");
    if (answer === "pending") return new Promise(() => undefined);
    if (answer instanceof Error) throw answer;
    return okResponse(answer);
  });
}

function renderCard(lang: "fa" | "en" = "en") {
  return renderConsole(<VoicemailCard />, {
    lang,
    auth: {
      status: "authenticated",
      user: FIXTURE_USER,
      workspace: FIXTURE_WORKSPACE,
      workspaces: [FIXTURE_WORKSPACE],
    },
  });
}

/** The <dd> right after the <dt> with this text. */
function definition(scope: HTMLElement, term: string) {
  const dt = within(scope).getByText(term, { selector: "dt" });
  const dd = dt.nextElementSibling;
  expect(dd?.tagName).toBe("DD");
  return dd?.textContent;
}

describe("VoicemailCard", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("shows total, handled, open and urgent with the median follow-up time", async () => {
    const get = mockAnalytics(analytics());
    renderCard();

    const card = screen.getByRole("region", { name: "Voicemail" });
    await waitFor(() => expect(definition(card, "Total")).toBe("12"), LOAD);
    expect(definition(card, "Handled")).toBe("2");
    expect(definition(card, "Open")).toBe("10");
    expect(definition(card, "Urgent")).toBe("3");
    expect(within(card).getByText("4:32")).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: "Open the voicemail inbox" })).toHaveAttribute(
      "href",
      "/voicemails",
    );
    expect(get).toHaveBeenCalledWith("/voicemails/analytics", expect.anything());
  });

  it("leaves the median out when no voicemail was followed up", async () => {
    mockAnalytics(analytics({ median_follow_up_seconds: null, by_follow_up_state: { none: 12 } }));
    renderCard();

    const card = screen.getByRole("region", { name: "Voicemail" });
    await waitFor(() => expect(definition(card, "Total")).toBe("12"), LOAD);
    expect(definition(card, "Handled")).toBe("0");
    expect(definition(card, "Open")).toBe("12");
    expect(within(card).queryByText("Median follow-up time")).not.toBeInTheDocument();
    expect(within(card).queryByText("4:32")).not.toBeInTheDocument();
  });

  it("shows the empty text when the window has no voicemails", async () => {
    mockAnalytics(analytics({ total: 0, by_priority: {}, by_follow_up_state: {}, median_follow_up_seconds: null }));
    renderCard();

    const card = screen.getByRole("region", { name: "Voicemail" });
    expect(
      await within(card).findByText("No voicemails in the last 30 days", undefined, LOAD),
    ).toBeInTheDocument();
    expect(within(card).queryByRole("definition")).not.toBeInTheDocument();
    // The footer link is still there, empty or not.
    expect(within(card).getByRole("link", { name: "Open the voicemail inbox" })).toBeInTheDocument();
  });

  it("is busy while loading and shows the card's error with no retry button when the request fails", async () => {
    mockAnalytics("pending");
    const loading = renderCard();
    expect(screen.getByRole("region", { name: "Voicemail" })).toHaveAttribute("aria-busy", "true");
    loading.unmount();
    vi.restoreAllMocks();

    mockAnalytics(apiError(500, "internal_error"));
    renderCard();
    const card = screen.getByRole("region", { name: "Voicemail" });
    expect(await within(card).findByRole("alert", undefined, LOAD)).toBeInTheDocument();
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
    expect(within(card).getByRole("heading", { level: 2, name: "Voicemail" })).toBeInTheDocument();
  });

  it("uses Persian text and Persian digits", async () => {
    mockAnalytics(analytics());
    renderCard("fa");

    const card = screen.getByRole("region", { name: "پیام‌های صوتی" });
    await waitFor(() => expect(definition(card, "مجموع")).toBe("۱۲"), LOAD);
    expect(definition(card, "رسیدگی‌شده")).toBe("۲");
    expect(definition(card, "باز")).toBe("۱۰");
    expect(definition(card, "فوری")).toBe("۳");
    expect(within(card).getByText("۴:۳۲")).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/[0-9]/);
    expect(within(card).getByRole("link", { name: "باز کردن صندوق پیام‌های صوتی" })).toBeInTheDocument();
  });
});
