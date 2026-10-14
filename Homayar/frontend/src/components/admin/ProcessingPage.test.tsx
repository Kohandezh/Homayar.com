import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useSearchParams } from "react-router-dom";
import ProcessingPage from "@/components/admin/ProcessingPage";
import { PROCESSING_COPY } from "@/components/platform/processing-copy";
import api from "@/lib/api";
import {
  FIXTURE_USER,
  FIXTURE_WORKSPACE,
  apiError,
  networkError,
  okResponse,
  renderConsole,
} from "@/test/fixtures";
import type { AdminJob, ProcessingJob, RunPolicySnapshot } from "@/types/platform";

const T = "2026-09-24T12:00:00Z";
// The page waits for several queries; slower machines need more than the 1 s default.
const LOAD = { timeout: 5000 };

function job(overrides: Partial<AdminJob> = {}): AdminJob {
  return {
    id: "job-1",
    meeting_id: "meet-1",
    meeting_title: "Board meeting",
    state: "dead_letter",
    stage: "dead_letter",
    attempt_count: 3,
    max_attempts: 3,
    failure_code: "asr_provider_error",
    failure_class: "transient",
    failure_message: "The speech service refused the request.",
    replay_count: 0,
    max_replays: 2,
    replayable: true,
    created_at: T,
    updated_at: T,
    ...overrides,
  };
}

const MEETING_JOB: ProcessingJob = {
  id: "job-1",
  state: "dead_letter",
  stage: "dead_letter",
  attempt_count: 3,
  max_attempts: 3,
  failure_code: "asr_provider_error",
  failure_message: "The speech service refused the request.",
  failure_class: "transient",
  replay_count: 0,
  created_at: T,
  updated_at: T,
  stages: [
    {
      stage: "materialize",
      state: "completed",
      reason_code: null,
      reason_message: null,
      provider: null,
      model: null,
      is_mock: false,
      latency_ms: 120,
      started_at: T,
      finished_at: T,
    },
    {
      stage: "asr",
      state: "failed",
      reason_code: "provider_error",
      reason_message: "The speech service refused the request.",
      provider: "mock",
      model: null,
      is_mock: false,
      latency_ms: null,
      started_at: T,
      finished_at: null,
    },
  ],
};

const SNAPSHOT: RunPolicySnapshot = {
  attempt: 1,
  policy_version: 4,
  policy_digest: "abcdef0123456789",
  policy_document: {},
  text_privacy_digest: null,
  strict_manifests: true,
  stages: [],
  created_at: T,
};

type JobAnswer = { items: AdminJob[]; next_cursor: string | null } | Error;

/**
 * Answers /admin/jobs from a queue (repeating the last answer) and serves the
 * two reads the drawer makes.
 */
function mockReads(answers: JobAnswer[]) {
  const calls: Array<Record<string, unknown>> = [];
  let index = 0;
  vi.spyOn(api, "get").mockImplementation(async (url: string, config?: { params?: unknown }) => {
    if (url === "/admin/jobs") {
      calls.push((config?.params ?? {}) as Record<string, unknown>);
      const answer = answers[Math.min(index, answers.length - 1)];
      index += 1;
      if (answer instanceof Error) throw answer;
      return okResponse(answer);
    }
    if (url === "/meetings/meet-1/job") return okResponse(MEETING_JOB);
    if (url.startsWith("/admin/jobs/") && url.endsWith("/policy-snapshots")) {
      // The older row of the "not the latest job" test gets no snapshots.
      const empty = url.includes("job-0");
      return okResponse({ job_id: empty ? "job-0" : "job-1", items: empty ? [] : [SNAPSHOT] });
    }
    throw apiError(404, "not_found");
  });
  return { calls };
}

/** Shows the current query string, so the shareable URL can be asserted. */
function UrlProbe() {
  const [params] = useSearchParams();
  return <output data-testid="url">{params.toString()}</output>;
}

function renderProcessing(route = "/admin/processing", lang: "fa" | "en" = "en") {
  return renderConsole(
    <>
      <ProcessingPage />
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

async function openDrawer(detailsLabel = "Details") {
  await findRow("Board meeting");
  fireEvent.click(screen.getByRole("button", { name: detailsLabel }));
  return screen.findByRole("dialog", undefined, LOAD);
}

// React Aria backs every select with a hidden native one, in the order the
// filters are rendered; changing it is the same as choosing from the list.
const STATE_FILTER = 0;
const STAGE_FILTER = 1;

function filterSelect(index: number): HTMLSelectElement {
  const select = screen.getAllByTestId("hidden-select-container")[index].querySelector("select");
  if (!select) throw new Error("no filter select");
  return select;
}

/** The state filter takes several values, so the options are selected directly. */
function chooseStates(values: string[]) {
  const select = filterSelect(STATE_FILTER);
  for (const option of Array.from(select.options)) option.selected = values.includes(option.value);
  fireEvent.change(select);
}

/** The pointer-free way to clear a select: Backspace on its trigger. */
function clearFilter(index: number) {
  const trigger = document.querySelectorAll<HTMLElement>('[data-slot="select-trigger"]')[index];
  fireEvent.keyDown(trigger, { key: "Backspace" });
  fireEvent.keyUp(trigger, { key: "Backspace" });
}

describe("ProcessingPage", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.localStorage.clear();
  });

  it("turns the filters in the URL into query parameters", async () => {
    const { calls } = mockReads([{ items: [job()], next_cursor: null }]);
    renderProcessing("/admin/processing?state=failed&state=dead_letter&stage=transcribing");

    expect(await screen.findByRole("grid", { name: "Processing jobs" }, LOAD)).toBeInTheDocument();
    expect(calls[0]).toEqual({
      limit: 25,
      state: ["failed", "dead_letter"],
      stage: "transcribing",
    });
  });

  it("ignores a state the API does not know", async () => {
    const { calls } = mockReads([{ items: [job()], next_cursor: null }]);
    renderProcessing("/admin/processing?state=exploded");

    await findRow("Board meeting");
    expect(calls[0]).toEqual({ limit: 25 });
  });

  it("sends the states chosen in the filter bar and puts them in the URL", async () => {
    const { calls } = mockReads([{ items: [job()], next_cursor: null }]);
    renderProcessing();

    await findRow("Board meeting");
    chooseStates(["failed", "dead_letter"]);

    await waitFor(
      () => expect(screen.getByTestId("url")).toHaveTextContent("state=failed&state=dead_letter"),
      LOAD,
    );
    await waitFor(
      () => expect(calls.at(-1)).toEqual({ limit: 25, state: ["failed", "dead_letter"] }),
      LOAD,
    );
  });

  it("sends the stage chosen in the filter bar and starts the paging again", async () => {
    const { calls } = mockReads([
      { items: [job()], next_cursor: "cursor-2" },
      { items: [job({ id: "job-2" })], next_cursor: null },
      { items: [job()], next_cursor: null },
    ]);
    renderProcessing();

    await findRow("Board meeting");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();

    fireEvent.change(filterSelect(STAGE_FILTER), { target: { value: "transcribing" } });

    await waitFor(() => expect(screen.getByTestId("url")).toHaveTextContent("stage=transcribing"), LOAD);
    await waitFor(() => expect(calls.at(-1)).toEqual({ limit: 25, stage: "transcribing" }), LOAD);
    // The old cursor belongs to the unfiltered list, so paging starts over.
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("clears one filter and leaves the other one standing", async () => {
    const { calls } = mockReads([{ items: [job()], next_cursor: null }]);
    renderProcessing("/admin/processing?state=failed&stage=transcribing");

    await findRow("Board meeting");
    clearFilter(STATE_FILTER);

    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("state="), LOAD);
    await waitFor(() => expect(calls.at(-1)).toEqual({ limit: 25, stage: "transcribing" }), LOAD);
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeInTheDocument();
  });

  it("clears the filters and starts the paging again", async () => {
    const { calls } = mockReads([
      { items: [job()], next_cursor: "cursor-2" },
      { items: [job({ id: "job-2" })], next_cursor: null },
      { items: [job()], next_cursor: null },
    ]);
    renderProcessing("/admin/processing?state=failed");

    await findRow("Board meeting");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("Page 2", undefined, LOAD)).toBeInTheDocument();
    expect(calls[1]).toMatchObject({ cursor: "cursor-2" });

    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));

    await waitFor(() => expect(screen.getByTestId("url")).not.toHaveTextContent("state="), LOAD);
    await waitFor(() => expect(calls.at(-1)).toEqual({ limit: 25 }), LOAD);
    // The old cursor belongs to the filtered list, so paging starts over.
    expect(screen.getByText("Page 1")).toBeInTheDocument();
  });

  it("says when nothing matches the filters", async () => {
    mockReads([{ items: [], next_cursor: null }]);
    renderProcessing("/admin/processing?state=queued");
    expect(
      await screen.findByText("No processing jobs match these filters.", undefined, LOAD),
    ).toBeInTheDocument();
  });

  it("tries the list again after an error", async () => {
    const { calls } = mockReads([
      apiError(500, "internal_error"),
      { items: [job()], next_cursor: null },
    ]);
    renderProcessing();

    // A 500 is described by its kind, so only the alert itself is asserted.
    expect(await screen.findByRole("alert", undefined, LOAD)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await findRow("Board meeting");
    expect(calls.length).toBe(2);
  });

  it("keeps a Persian meeting title in one order on the English page", async () => {
    mockReads([{ items: [job({ meeting_title: "شکایت از تأخیر نصب #29" })], next_cursor: null }]);
    renderProcessing();

    // A meeting title is user content; the link must not force a direction on
    // it, so the browser keeps "#29" at the end where the title puts it.
    const link = await screen.findByRole("link", { name: "شکایت از تأخیر نصب #29" }, LOAD);
    const inLink = link.querySelector("bdi");
    expect(inLink).not.toBeNull();
    expect(inLink?.getAttribute("dir")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const drawer = await screen.findByRole("dialog", undefined, LOAD);
    const inHeading = Array.from(drawer.querySelectorAll("bdi")).find(
      (element) => element.textContent === "شکایت از تأخیر نصب #29",
    );
    expect(inHeading?.getAttribute("dir")).toBeNull();
  });

  it("shows an unknown stage from the URL as the select's active value", async () => {
    const { calls } = mockReads([{ items: [job()], next_cursor: null }]);
    renderProcessing("/admin/processing?stage=custom_stage");

    await findRow("Board meeting");
    // The request still filters by the raw value...
    expect(calls[0]).toEqual({ limit: 25, stage: "custom_stage" });
    // ...and the select shows it, so the control and the request agree.
    const select = filterSelect(STAGE_FILTER);
    const active = Array.from(select.options).find((option) => option.selected);
    expect(active?.value).toBe("custom_stage");
  });

  it("returns to the first page by itself after an invalid cursor", async () => {
    mockReads([
      { items: [job()], next_cursor: "cursor-2" },
      apiError(400, "invalid_cursor"),
      { items: [job()], next_cursor: "cursor-2" },
    ]);
    renderProcessing();

    await findRow("Board meeting");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    // The rejected cursor is dropped without a click and the first page loads.
    expect(await screen.findByText("Page 1", undefined, LOAD)).toBeInTheDocument();
    await findRow("Board meeting");
    expect(screen.queryByRole("button", { name: "First page" })).not.toBeInTheDocument();
  });

  it("links each row to its meeting and opens the drawer by button", async () => {
    mockReads([{ items: [job()], next_cursor: null }]);
    renderProcessing();

    await findRow("Board meeting");
    expect(screen.getByRole("link", { name: "Board meeting" })).toHaveAttribute(
      "href",
      "/meetings/meet-1",
    );

    const drawer = await openDrawer();
    expect(drawer).toHaveTextContent("Board meeting");
  });

  it("shows the failure, the stage timeline and the policy of each attempt in the drawer", async () => {
    mockReads([{ items: [job()], next_cursor: null }]);
    renderProcessing();

    const drawer = await openDrawer();
    expect(drawer).toHaveTextContent("Temporary");
    expect(drawer).toHaveTextContent("asr_provider_error");
    expect(drawer).toHaveTextContent("The speech service refused the request.");
    expect(drawer).toHaveTextContent("3/3");
    expect(drawer).toHaveTextContent("0/2");
    // The stage timeline of GET /meetings/{id}/job, named by PROCESSING_COPY.
    expect(await screen.findByText("Preparing the recording", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Speech to text")).toBeInTheDocument();
    expect(screen.getByText("provider_error")).toBeInTheDocument();
    // The policy snapshot of each attempt.
    expect(await screen.findByText("Attempt 1", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("Policy version 4")).toBeInTheDocument();
    expect(screen.getByText("abcdef012345")).toBeInTheDocument();
  });

  it("keeps the drawer honest when the row is not the meeting's latest job", async () => {
    mockReads([{ items: [job({ id: "job-0", replayable: false })], next_cursor: null }]);
    renderProcessing();

    await openDrawer();
    expect(
      await screen.findByText(
        "This job is not the meeting's latest one, so its stages are not available.",
        undefined,
        LOAD,
      ),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("No policy was recorded for this job.", undefined, LOAD),
    ).toBeInTheDocument();
  });

  it("replays a dead-lettered job, reports it and refreshes the list", async () => {
    const { calls } = mockReads([
      { items: [job()], next_cursor: null },
      // The refreshed list: the job is queued again and no longer replayable.
      {
        items: [
          job({ state: "queued", stage: "queued", replayable: false, replay_count: 1 }),
        ],
        next_cursor: null,
      },
    ]);
    const post = vi.spyOn(api, "post").mockResolvedValue(
      okResponse({ job: MEETING_JOB, replay: 1, max_replays: 2, dispatch: "published" }),
    );
    renderProcessing();

    await openDrawer();
    const listCalls = calls.length;
    await userEvent.click(screen.getByRole("button", { name: PROCESSING_COPY.en.replay }));

    await waitFor(() => expect(post).toHaveBeenCalledWith("/admin/jobs/job-1/replay"), LOAD);
    const status = await screen.findByText(PROCESSING_COPY.en.replayQueued, undefined, LOAD);
    expect(status).toHaveAttribute("role", "status");
    // The replay requeued the job, so the list reads /admin/jobs again.
    await waitFor(() => expect(calls.length).toBeGreaterThan(listCalls), LOAD);
    // The row is no longer replayable, so the button goes away...
    await waitFor(
      () =>
        expect(screen.queryByRole("button", { name: PROCESSING_COPY.en.replay })).not.toBeInTheDocument(),
      LOAD,
    );
    // ...but what the reader was told stays told.
    expect(screen.getByText(PROCESSING_COPY.en.replayQueued)).toBeInTheDocument();
  });

  it("offers no replay when the server says the job is not replayable", async () => {
    mockReads([{ items: [job({ replayable: false })], next_cursor: null }]);
    renderProcessing();

    const drawer = await openDrawer();
    expect(drawer).toHaveTextContent("asr_provider_error");
    expect(
      screen.queryByRole("button", { name: PROCESSING_COPY.en.replay }),
    ).not.toBeInTheDocument();
  });

  it.each(Object.entries(PROCESSING_COPY.en.replayErrors))(
    "explains a refused replay: %s",
    async (code, message) => {
      mockReads([{ items: [job()], next_cursor: null }]);
      const status = code === "audio_quota_exceeded" || code === "concurrent_job_limit_reached" ? 429 : 409;
      vi.spyOn(api, "post").mockRejectedValue(apiError(status, code));
      renderProcessing();

      await openDrawer();
      await userEvent.click(screen.getByRole("button", { name: PROCESSING_COPY.en.replay }));

      const alert = await screen.findByRole("alert", undefined, LOAD);
      expect(alert).toHaveTextContent(message);
    },
  );

  it("explains a refused replay with an unknown code", async () => {
    mockReads([{ items: [job()], next_cursor: null }]);
    vi.spyOn(api, "post").mockRejectedValue(apiError(404, "job_not_found"));
    renderProcessing();

    await openDrawer();
    await userEvent.click(screen.getByRole("button", { name: PROCESSING_COPY.en.replay }));

    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(PROCESSING_COPY.en.replayFailed);
  });

  it("explains a refused replay with a code SPEC does not list", async () => {
    mockReads([{ items: [job()], next_cursor: null }]);
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "something_new"));
    renderProcessing();

    await openDrawer();
    await userEvent.click(screen.getByRole("button", { name: PROCESSING_COPY.en.replay }));

    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(PROCESSING_COPY.en.replayFailed);
  });

  it("does not read a replay error text off the prototype", async () => {
    mockReads([{ items: [job()], next_cursor: null }]);
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "constructor"));
    renderProcessing();

    await openDrawer();
    await userEvent.click(screen.getByRole("button", { name: PROCESSING_COPY.en.replay }));

    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(PROCESSING_COPY.en.replayFailed);
  });

  it("explains a replay that never reached the server", async () => {
    mockReads([{ items: [job()], next_cursor: null }]);
    vi.spyOn(api, "post").mockRejectedValue(networkError());
    renderProcessing();

    await openDrawer();
    await userEvent.click(screen.getByRole("button", { name: PROCESSING_COPY.en.replay }));

    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(PROCESSING_COPY.en.replayFailed);
  });

  it("renders in Persian, right to left", async () => {
    mockReads([{ items: [job()], next_cursor: null }]);
    vi.spyOn(api, "post").mockRejectedValue(apiError(409, "replay_limit_reached"));
    renderProcessing("/admin/processing", "fa");

    expect(await screen.findByRole("heading", { name: "پردازش", level: 1 }, LOAD)).toBeInTheDocument();
    expect(document.documentElement.dir).toBe("rtl");
    expect(await screen.findByRole("grid", { name: "کارهای پردازش" }, LOAD)).toBeInTheDocument();

    await openDrawer("جزئیات");
    await userEvent.click(screen.getByRole("button", { name: PROCESSING_COPY.fa.replay }));

    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(PROCESSING_COPY.fa.replayErrors.replay_limit_reached);
  });
});
