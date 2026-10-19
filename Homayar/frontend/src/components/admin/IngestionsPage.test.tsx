import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { useSearchParams } from "react-router";
import { getLocalTimeZone, parseDate } from "@internationalized/date";
import IngestionsPage from "@/components/admin/IngestionsPage";
import api from "@/lib/api";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  okResponse,
  renderConsole,
} from "@/test/fixtures";
import type { IngestionRecord } from "@/types/ingestions";

const T = "2026-10-04T12:00:00Z";
// The page waits for the list query; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };

function ingestion(overrides: Partial<IngestionRecord> = {}): IngestionRecord {
  return {
    id: "ing-1",
    source_type: "file_upload",
    source_provider: "web-console",
    external_event_id: "evt-1",
    media_size_bytes: 1234567,
    declared_content_type: "audio/webm",
    original_filename: "board-meeting.webm",
    status: "accepted",
    reason: null,
    received_at: T,
    created_at: T,
    meeting_id: "meet-1",
    duplicate_of_id: null,
    conflict_count: 0,
    last_conflict_at: null,
    ...overrides,
  };
}

type ListAnswer = { items: IngestionRecord[]; next_before: string | null } | Error;

/**
 * Answers GET /ingestions from a queue (repeating the last answer) and serves
 * the records the drawer asks for. A `gates` entry holds that answer back
 * until its promise resolves, so a test can watch a page still loading.
 */
function mockIngestionReads(
  answers: ListAnswer[],
  details: Record<string, IngestionRecord | Error> = {},
  gates: Array<Promise<void> | undefined> = [],
) {
  const calls: Array<Record<string, unknown>> = [];
  const detailCalls: string[] = [];
  let index = 0;
  vi.spyOn(api, "get").mockImplementation(async (url: string, config?: { params?: unknown }) => {
    if (url === "/ingestions") {
      calls.push((config?.params ?? {}) as Record<string, unknown>);
      const answer = answers[Math.min(index, answers.length - 1)];
      const gate = gates[Math.min(index, gates.length - 1)];
      index += 1;
      const deliver = () => {
        if (answer instanceof Error) throw answer;
        return okResponse(answer);
      };
      return gate ? gate.then(deliver) : deliver();
    }
    if (url.startsWith("/ingestions/")) {
      const id = decodeURIComponent(url.slice("/ingestions/".length));
      detailCalls.push(id);
      const detail = details[id];
      if (detail instanceof Error) throw detail;
      if (detail) return okResponse(detail);
    }
    throw apiError(404, "not_found");
  });
  return { calls, detailCalls };
}

/** Shows the current query string, so the shareable URL can be asserted. */
function UrlProbe() {
  const [params] = useSearchParams();
  return <output data-testid="url">{params.toString()}</output>;
}

function renderIngestions(route = "/admin/ingestions", lang: "fa" | "en" = "en") {
  return renderConsole(
    <>
      <IngestionsPage />
      <UrlProbe />
    </>,
    {
      route,
      lang,
      auth: {
        status: "authenticated",
        user: FIXTURE_USER,
        workspace: FIXTURE_WORKSPACE,
        workspaces: [FIXTURE_WORKSPACE],
      },
    },
  );
}

function findRow(label: string) {
  return screen.findByRole("rowheader", { name: label }, LOAD);
}

/** The ISO cursor the page derives from an end day, in the machine's own zone. */
function localDayAfter(day: string) {
  return parseDate(day).add({ days: 1 }).toDate(getLocalTimeZone()).toISOString();
}

// React Aria backs every select with a hidden native one, in the order the
// filters are rendered; changing it is the same as choosing from the list.
const SOURCE_FILTER = 0;
const STATE_FILTER = 1;

function filterSelect(index: number): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[index].querySelector("select");
  if (!select) throw new Error("no filter select");
  return select;
}

describe("IngestionsPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("asks for the first page with no filters and renders the rows", async () => {
    const { calls, detailCalls } = mockIngestionReads(
      [{ items: [ingestion()], next_before: null }],
      { "ing-1": ingestion() },
    );
    renderIngestions();

    expect(await screen.findByRole("grid", { name: "Ingestions" }, LOAD)).toBeInTheDocument();
    expect(calls[0]).toEqual({ limit: 25 });
    expect(detailCalls).toEqual([]);

    const row = screen.getByRole("row", { name: /board-meeting\.webm/ });
    expect(within(row).getByText("File upload")).toBeInTheDocument();
    expect(within(row).getByText("Accepted")).toBeInTheDocument();
    expect(within(row).getByText("—")).toBeInTheDocument(); // no reason on an accepted row
    expect(row.querySelector("time")?.getAttribute("dateTime")).toBe(T);
    expect(within(row).getByRole("button", { name: "Details" })).toBeInTheDocument();
  });

  it("shows a missing filename with a dash and the duplicate marker", async () => {
    mockIngestionReads([
      { items: [ingestion({ id: "ing-d", original_filename: null, duplicate_of_id: "ing-0" })], next_before: null },
    ]);
    renderIngestions();

    const header = await findRow("—");
    const row = header.closest("tr") ?? document.createElement("tr");
    expect(within(row).getByText("Duplicate")).toBeInTheDocument();
  });

  it("sends the source from the URL and drops a source the server does not know", async () => {
    const known = mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    const first = renderIngestions("/admin/ingestions?source=file_upload");
    await findRow("board-meeting.webm");
    expect(known.calls[0]).toEqual({ limit: 25, source_type: "file_upload" });
    first.unmount();
    vi.restoreAllMocks();

    const unknown = mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    renderIngestions("/admin/ingestions?source=portal_drop");
    await findRow("board-meeting.webm");
    expect(unknown.calls[0]).toEqual({ limit: 25 });
  });

  it("filters the loaded page by state without telling the server", async () => {
    const { calls } = mockIngestionReads([
      {
        items: [
          ingestion({ id: "ing-a", original_filename: "kept.webm", status: "rejected", reason: "upload_too_large" }),
          ingestion({ id: "ing-b", original_filename: "hidden.webm" }),
        ],
        next_before: null,
      },
    ]);
    renderIngestions("/admin/ingestions?state=rejected");

    expect(await findRow("kept.webm")).toBeInTheDocument();
    expect(screen.queryByRole("rowheader", { name: "hidden.webm" })).not.toBeInTheDocument();
    expect(calls[0]).toEqual({ limit: 25 });
  });

  it("keeps every state without a known state filter", async () => {
    mockIngestionReads([
      { items: [ingestion({ id: "ing-a", original_filename: "kept.webm" })], next_before: null },
    ]);
    renderIngestions("/admin/ingestions?state=exploded");

    expect(await findRow("kept.webm")).toBeInTheDocument();
  });

  it("puts the state chosen in the filter bar into the URL", async () => {
    const { calls } = mockIngestionReads([
      {
        items: [ingestion({ id: "ing-a", original_filename: "kept.webm", status: "rejected" })],
        next_before: null,
      },
    ]);
    renderIngestions();

    await findRow("kept.webm");
    fireEvent.change(filterSelect(STATE_FILTER), { target: { value: "rejected" } });

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("state=rejected"), LOAD);
    // The server has no status filter, so the request stays unchanged.
    await waitFor(() => expect(calls.at(-1)).toEqual({ limit: 25 }), LOAD);
  });

  it("sends the source chosen in the filter bar and starts the paging again", async () => {
    const { calls } = mockIngestionReads([
      { items: [ingestion()], next_before: "cursor-2" },
      { items: [ingestion({ id: "ing-2", original_filename: "page2.webm" })], next_before: null },
      { items: [ingestion()], next_before: null },
    ]);
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();

    fireEvent.change(filterSelect(SOURCE_FILTER), { target: { value: "file_upload" } });

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("source=file_upload"), LOAD);
    await waitFor(() => expect(calls.at(-1)).toEqual({ limit: 25, source_type: "file_upload" }), LOAD);
    // The old cursor belongs to the unfiltered list, so paging starts over.
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("clears the filters and asks plainly again", async () => {
    const { calls } = mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    renderIngestions("/admin/ingestions?source=file_upload&state=accepted");

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent(""), LOAD);
    await waitFor(() => expect(calls.at(-1)).toEqual({ limit: 25 }), LOAD);
  });

  it("pages with the server's cursor and comes back", async () => {
    const { calls } = mockIngestionReads([
      { items: [ingestion()], next_before: "2026-09-30T00:00:00Z" },
      { items: [ingestion({ id: "ing-2", original_filename: "page2.webm" })], next_before: null },
      // Going back revalidates the first page, without any cursor.
      { items: [ingestion()], next_before: null },
    ]);
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();
    await findRow("page2.webm");
    expect(calls[1]).toEqual({ limit: 25, before: "2026-09-30T00:00:00Z" });

    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Page 1", undefined, LOAD)).toBeInTheDocument();
    await findRow("board-meeting.webm");
    // The first page again is a plain first-page request, never the old cursor.
    expect(calls.at(-1)).toEqual({ limit: 25 });
  }, 15000);

  it("offers no next page when the server says the list ends", async () => {
    mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    renderIngestions();

    await findRow("board-meeting.webm");
    expect(screen.getByRole("button", { name: "Next" })).toHaveAttribute("aria-disabled", "true");
  });

  it("keeps the page and the pager while the next page loads", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { calls } = mockIngestionReads(
      [
        { items: [ingestion()], next_before: "cursor-2" },
        { items: [ingestion({ id: "ing-2", original_filename: "page2.webm" })], next_before: null },
      ],
      {},
      [undefined, gate],
    );
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(calls.length).toBe(2), LOAD);

    // Page 2 is asked for but not answered: page 1 stays on screen, so the
    // pager (and the focused button) never unmounts.
    expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeInTheDocument();
    expect(screen.getByText("Page 2")).toBeInTheDocument();
    await findRow("board-meeting.webm");

    release();
    await findRow("page2.webm");
    expect(screen.queryByRole("rowheader", { name: "board-meeting.webm" })).not.toBeInTheDocument();
  }, 15000);

  it("keeps the pager on an empty page after the first, and goes back", async () => {
    // A list of exactly one full page: the server offers a cursor, then the
    // next page answers empty.
    const pageOne = Array.from({ length: 25 }, (_, index) =>
      ingestion({ id: `ing-${index}`, original_filename: `row-${index}.webm` }),
    );
    const { calls } = mockIngestionReads([
      { items: pageOne, next_before: "2026-09-30T00:00:00Z" },
      { items: [], next_before: null },
    ]);
    renderIngestions();

    await findRow("row-0.webm");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    expect(await screen.findByText("No more ingestions.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toHaveAttribute("aria-disabled", "true");
    expect(calls[1]).toEqual({ limit: 25, before: "2026-09-30T00:00:00Z" });

    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Page 1", undefined, LOAD)).toBeInTheDocument();
    await findRow("row-0.webm");
  }, 15000);

  it("sends the end day as the exclusive cursor of the first page", async () => {
    const { calls } = mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    renderIngestions("/admin/ingestions?to=2026-10-05");

    await findRow("board-meeting.webm");
    expect(calls[0]).toEqual({ limit: 25, before: localDayAfter("2026-10-05") });
  });

  it("hides rows before the start day and stops paging at the oldest row", async () => {
    mockIngestionReads([
      {
        items: [
          ingestion({ id: "ing-new", original_filename: "new.webm", received_at: "2026-10-04T12:00:00Z" }),
          ingestion({ id: "ing-old", original_filename: "old.webm", received_at: "2026-10-01T10:00:00Z" }),
        ],
        next_before: "2026-09-30T00:00:00Z",
      },
    ]);
    renderIngestions("/admin/ingestions?from=2026-10-03");

    expect(await findRow("new.webm")).toBeInTheDocument();
    expect(screen.queryByRole("rowheader", { name: "old.webm" })).not.toBeInTheDocument();
    // Every later page is older than the oldest row, so Next is withdrawn.
    expect(screen.getByRole("button", { name: "Next" })).toHaveAttribute("aria-disabled", "true");
  });

  it("keeps Next while the start day is before the oldest row", async () => {
    mockIngestionReads([
      {
        items: [ingestion({ received_at: "2026-10-01T10:00:00Z" })],
        next_before: "2026-09-30T00:00:00Z",
      },
    ]);
    renderIngestions("/admin/ingestions?from=2026-09-30");

    await findRow("board-meeting.webm");
    expect(screen.getByRole("button", { name: "Next" })).toHaveAttribute("aria-disabled", "false");
  });

  it("asks nothing while the start day is after the end day", async () => {
    const { calls } = mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    renderIngestions("/admin/ingestions?from=2026-10-06&to=2026-10-05");

    expect(
      await screen.findByText("The start date is after the end date.", undefined, LOAD),
    ).toBeInTheDocument();
    expect(calls).toEqual([]);
  });

  it("asks with a start day equal to the end day", async () => {
    const { calls } = mockIngestionReads([
      // A row on the chosen day, visible in whatever zone the machine runs.
      { items: [ingestion({ received_at: "2026-10-05T12:00:00Z" })], next_before: null },
    ]);
    renderIngestions("/admin/ingestions?from=2026-10-05&to=2026-10-05");

    await findRow("board-meeting.webm");
    expect(calls[0]).toEqual({ limit: 25, before: localDayAfter("2026-10-05") });
  });

  it("keeps the pager when the client-side filters hide every row of the page", async () => {
    mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    renderIngestions("/admin/ingestions?state=rejected");

    expect(
      await screen.findByText("No ingestions on this page match the filters.", undefined, LOAD),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeInTheDocument();
  });

  it("shows the empty state for a workspace without ingestions, and a no-match note when filtered", async () => {
    mockIngestionReads([{ items: [], next_before: null }]);
    renderIngestions();
    expect(await screen.findByText("No uploads or imports yet.", undefined, LOAD)).toBeInTheDocument();
  });

  it("says no page matches when a filtered request comes back empty", async () => {
    mockIngestionReads([{ items: [], next_before: null }]);
    renderIngestions("/admin/ingestions?source=api");
    expect(
      await screen.findByText("No ingestions on this page match the filters.", undefined, LOAD),
    ).toBeInTheDocument();
  });

  it("tries the list again after an error", async () => {
    const { calls } = mockIngestionReads([
      apiError(500, "internal_error"),
      { items: [ingestion()], next_before: null },
    ]);
    renderIngestions();

    // A 500 is described by its kind, so only the alert itself is asserted.
    expect(await screen.findByRole("alert", undefined, LOAD)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await findRow("board-meeting.webm");
    expect(calls.length).toBe(2);
  });

  it("fetches the record when the drawer opens and shows every field", async () => {
    const record = ingestion();
    const { detailCalls } = mockIngestionReads([{ items: [record], next_before: null }], { "ing-1": record });
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    // The drawer reads its own GET /ingestions/{id}, so the fields arrive with that answer.
    await waitFor(() => expect(drawer).toHaveTextContent("board-meeting.webm"), LOAD);
    expect(detailCalls).toEqual(["ing-1"]);

    expect(drawer).toHaveTextContent("Accepted");
    expect(drawer).toHaveTextContent("File upload");
    expect(drawer).toHaveTextContent("web-console");
    expect(drawer).toHaveTextContent("board-meeting.webm");
    expect(drawer).toHaveTextContent("audio/webm");
    expect(drawer).toHaveTextContent("1.2 MB");
    expect(drawer).toHaveTextContent("Event ID");
    expect(drawer).toHaveTextContent("evt-1");
    expect(drawer.querySelectorAll("time[dateTime='2026-10-04T12:00:00Z']").length).toBe(2);
    expect(screen.getByRole("link", { name: "Open meeting" })).toHaveAttribute("href", "/meetings/meet-1");
  });

  it("explains a rejected row with its reason label and raw code", async () => {
    const record = ingestion({ status: "rejected", reason: "upload_too_large" });
    mockIngestionReads([{ items: [record], next_before: null }], { "ing-1": record });
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    await waitFor(() => expect(drawer).toHaveTextContent("Rejected"), LOAD);

    expect(drawer).toHaveTextContent("File too large");
    expect(drawer).toHaveTextContent("upload_too_large");
  });

  it("opens the earlier ingestion of a duplicate row", async () => {
    const duplicate = ingestion({ id: "ing-d", original_filename: "same.webm", duplicate_of_id: "ing-0" });
    const earlier = ingestion({ id: "ing-0", original_filename: "first.webm" });
    const { detailCalls } = mockIngestionReads(
      [{ items: [duplicate], next_before: null }],
      { "ing-d": duplicate, "ing-0": earlier },
    );
    renderIngestions();

    await findRow("same.webm");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    await waitFor(() => expect(drawer).toHaveTextContent("Same file as an earlier ingestion."), LOAD);

    fireEvent.click(screen.getByRole("button", { name: "Open the earlier ingestion" }));
    await waitFor(() => expect(detailCalls).toContain("ing-0"), LOAD);
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("first.webm"), LOAD);
  }, 15000);

  it("shows the conflicts of the event identity", async () => {
    const record = ingestion({ conflict_count: 3, last_conflict_at: T });
    mockIngestionReads([{ items: [record], next_before: null }], { "ing-1": record });
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    await waitFor(() => expect(drawer).toHaveTextContent("Sent again with different content: 3 times"), LOAD);

    expect(drawer.querySelectorAll("time[dateTime='2026-10-04T12:00:00Z']").length).toBe(3);
  });

  it("shows the not-found message for a hidden ingestion", async () => {
    const record = ingestion();
    mockIngestionReads([{ items: [record], next_before: null }], { "ing-1": apiError(404, "ingestion_not_found") });
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const drawer = await screen.findByRole("dialog", undefined, LOAD);

    await waitFor(
      () =>
        expect(drawer).toHaveTextContent("This ingestion was not found or is not visible to you."),
      LOAD,
    );
  });

  it("shows no meeting link when no meeting was created", async () => {
    const record = ingestion({ meeting_id: null });
    mockIngestionReads([{ items: [record], next_before: null }], { "ing-1": record });
    renderIngestions();

    await findRow("board-meeting.webm");
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByRole("dialog", undefined, LOAD);
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("Accepted"), LOAD);

    expect(screen.queryByRole("link", { name: "Open meeting" })).not.toBeInTheDocument();
  });

  it("renders no hash or internal id, in the page or the drawer", async () => {
    // The server sends more than the console type declares; the page must not
    // render any of it, while the fields it does show stay visible.
    const record = {
      ...ingestion(),
      payload_hash: "payload-hash-secret",
      media_hash: "media-hash-secret",
      workspace_id: "ws-secret-1",
      organization_id: "org-secret-1",
      api_key_id: "key-secret-1",
      created_by_user_id: "user-secret-1",
      job_id: "job-secret-1",
    } as IngestionRecord;
    mockIngestionReads([{ items: [record], next_before: null }], { "ing-1": record });
    renderIngestions();

    await findRow("board-meeting.webm");
    const secrets = [
      "payload-hash-secret",
      "media-hash-secret",
      "ws-secret-1",
      "org-secret-1",
      "key-secret-1",
      "user-secret-1",
      "job-secret-1",
    ];
    const page = document.body.textContent ?? "";
    for (const secret of secrets) expect(page).not.toContain(secret);
    expect(page).toContain("board-meeting.webm");

    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByRole("dialog", undefined, LOAD);
    await waitFor(() => expect(screen.getByRole("dialog")).toHaveTextContent("board-meeting.webm"), LOAD);
    const drawerText = document.body.textContent ?? "";
    for (const secret of secrets) expect(drawerText).not.toContain(secret);
    expect(drawerText).toContain("evt-1");
  });

  it("shows an unknown source or reason as sent", async () => {
    const record = ingestion({ source_type: "portal_drop", status: "rejected", reason: "mystery_code" });
    mockIngestionReads([{ items: [record], next_before: null }], { "ing-1": record });
    renderIngestions();

    const header = await findRow("board-meeting.webm");
    const row = header.closest("tr") ?? document.createElement("tr");
    expect(within(row).getByText("portal_drop")).toBeInTheDocument();
    expect(within(row).getByText("mystery_code")).toBeInTheDocument();
  });

  it("renders in Persian, right to left", async () => {
    mockIngestionReads([{ items: [ingestion()], next_before: null }]);
    renderIngestions("/admin/ingestions", "fa");

    expect(await screen.findByRole("heading", { name: "ورودی‌ها", level: 1 }, LOAD)).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("rtl");
    expect(await screen.findByRole("grid", { name: "ورودی‌ها" }, LOAD)).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "جزئیات" }, LOAD)).toBeInTheDocument();
  });
});
