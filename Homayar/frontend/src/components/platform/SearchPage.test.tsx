import { afterEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { Route, Routes } from "react-router";
import api from "@/lib/api";
import SearchPage from "@/components/platform/SearchPage";
import { FIXTURE_USER, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { SearchResponse } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";

const RESULTS: SearchResponse = {
  query: "invoice",
  items: [
    {
      id: "m-1",
      title: "Billing call",
      status: "completed",
      language: "fa",
      duration_seconds: 754,
      created_at: T,
      matches: [
        { kind: "transcript", snippet: "the invoice for [EMAIL_1]", timestamp_ms: 15000 },
        { kind: "summary", snippet: "invoice follow-up", timestamp_ms: null },
      ],
    },
    { id: "m-2", title: "Speaker only", status: "completed", language: "en", duration_seconds: 60, created_at: T, matches: [] },
  ],
};

function renderSearch(route: string, lang: "fa" | "en" = "en") {
  return renderConsole(
    <Routes>
      <Route path="search" element={<SearchPage />} />
    </Routes>,
    { route, lang, auth: { status: "authenticated", user: FIXTURE_USER, workspace: FIXTURE_WORKSPACE } },
  );
}

describe("SearchPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("links only transcript matches to a time, with the kind and snippet in the link name (QA Q-01, Q-04)", async () => {
    vi.spyOn(api, "get").mockResolvedValue(okResponse(RESULTS));
    renderSearch("/search?q=invoice");

    const timed = await screen.findByRole("link", { name: /^Transcript: the invoice for \[EMAIL_1\] — Billing call, from 0:15$/ });
    expect(timed).toHaveAttribute("href", "/meetings/m-1?at=15000");
    const summary = screen.getByRole("link", { name: /^Summary: invoice follow-up — Billing call$/ });
    expect(summary).toHaveAttribute("href", "/meetings/m-1");
    expect(screen.getByText("[EMAIL_1]").closest("mark.pii-placeholder")).not.toBeNull();
  });

  it("explains a result without a preview and does not make the list a live region (QA Q-05, Q-06)", async () => {
    vi.spyOn(api, "get").mockResolvedValue(okResponse(RESULTS));
    renderSearch("/search?q=invoice");
    expect(await screen.findByText("No preview is available for this result; open the meeting.")).toBeInTheDocument();
    expect(document.querySelector(".search-results")).not.toHaveAttribute("aria-live");
    expect(screen.getByRole("status")).toHaveTextContent("2 result(s)");
  });

  it("uses Persian digits, words and spelling in Persian (QA Q-02, Q-03, Q-07)", async () => {
    vi.spyOn(api, "get").mockResolvedValue(okResponse(RESULTS));
    renderSearch("/search?q=invoice", "fa");
    expect(await screen.findByText("۱۲:۳۴")).toBeInTheDocument();
    expect(screen.getByText("۰:۱۵")).toBeInTheDocument();
    expect(screen.getAllByText("متن گفت‌وگو").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "جست‌وجوی جلسه‌ها" })).toBeInTheDocument();
    expect(screen.queryByText("TRANSCRIPT")).not.toBeInTheDocument();
    expect(screen.queryByText("FA")).not.toBeInTheDocument();
  });

  it("shows a localized search failure instead of the server text (QA Q-09, X-01)", async () => {
    vi.spyOn(api, "get").mockRejectedValue(apiError(503, "search_backend_unavailable"));
    renderSearch("/search?q=invoice", "fa");
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("سرویس الان در دسترس نیست؛ کمی بعد دوباره تلاش کنید.");
    expect(alert).not.toHaveTextContent("fixture");
  });

  it("does not query for text over the API's limit and caps the input (QA Q-10)", () => {
    const get = vi.spyOn(api, "get");
    renderSearch(`/search?q=${"x".repeat(161)}`);
    expect(get).not.toHaveBeenCalled();
    expect(screen.getByRole("searchbox")).toHaveAttribute("maxlength", "160");
  });
});
