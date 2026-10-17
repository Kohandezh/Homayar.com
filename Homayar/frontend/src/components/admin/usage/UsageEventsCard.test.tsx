import { afterEach, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UsageEventsCard from "@/components/admin/usage/UsageEventsCard";
import api from "@/lib/api";
import { FIXTURE_USER, FIXTURE_WORKSPACE, okResponse, renderConsole } from "@/test/fixtures";
import type { UsageEvent } from "@/types/platform";

/**
 * The paging nav must survive a page change: a new cursor is a new query key,
 * so the card flips to its loading state while the next page loads, and the
 * focused button may not unmount (AC3). This runs the real hooks, with an
 * api spy whose second answer never resolves.
 */

const T = "2026-09-24T12:00:00Z";
const LOAD = { timeout: 5000 };

function event(id: string): UsageEvent {
  return {
    id,
    metric: "audio_seconds",
    value: 60,
    event_type: "committed",
    subject_type: "meeting",
    subject_id: `meeting-${id}`,
    occurred_at: T,
  };
}

function renderCard() {
  return renderConsole(<UsageEventsCard />, {
    lang: "en",
    auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE, workspaces: [FIXTURE_WORKSPACE] },
  });
}

describe("UsageEventsCard paging focus", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("keeps the nav and the focused Next page button mounted while the next page loads", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "get").mockImplementation((url: string, config?: { params?: unknown }) => {
      if (url !== "/billing/usage/events") throw new Error(`unexpected ${url}`);
      // The first page answers; the second one never does.
      const params = (config?.params ?? {}) as { before?: string };
      if (params.before !== undefined) return new Promise(() => undefined);
      return Promise.resolve(okResponse({ items: [event("e1")], next_before: T, next_before_id: "e1" }));
    });
    renderCard();

    const next = await screen.findByRole("button", { name: "Next page" }, LOAD);
    await expect(screen.findByText("Page 1", undefined, LOAD)).resolves.toBeInTheDocument();
    await user.click(next);

    // The card is loading the second page, and the nav is still there.
    const card = screen.getByRole("region", { name: "Usage events" });
    expect(card).toHaveAttribute("aria-busy", "true");
    expect(document.body.contains(next)).toBe(true);
    expect(within(card).getByRole("button", { name: "Previous page" })).toBeInTheDocument();
    expect(within(card).getByText("Page 2")).toBeInTheDocument();
    // Both buttons read as disabled while the request is in flight.
    expect(next).toHaveAttribute("aria-disabled", "true");
    expect(within(card).getByRole("button", { name: "Previous page" })).toHaveAttribute("aria-disabled", "true");
  });
});
