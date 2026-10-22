import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import VoiceOperationsPage from "@/components/admin/VoiceOperationsPage";
import api from "@/lib/api";
import { describeApiError } from "@/lib/error-messages";
import { isolateLtr } from "@/lib/format";
import { FIXTURE_OPERATOR, FIXTURE_WORKSPACE, apiError, okResponse, renderConsole } from "@/test/fixtures";
import type { VoiceCapacityResponse, VoiceMediaNode, VoiceMetrics, VoiceRecoverResponse } from "@/types/voice";

const NOW = Date.parse("2026-10-18T12:00:00Z");
// The page reads the capacity, then React Aria builds the table; a loaded machine needs more than 1 s.
const LOAD = { timeout: 5000 };
// Each case renders the page and often walks a dialog flow, which can pass 5 s on a loaded machine.
const SLOW = 20_000;

function ago(seconds: number) {
  return new Date(NOW - seconds * 1000).toISOString();
}

function node(overrides: Partial<VoiceMediaNode> = {}): VoiceMediaNode {
  return {
    node_id: "media-a",
    max_concurrent: 20,
    held: 3,
    draining: false,
    healthy: true,
    generation: 4,
    heartbeat_at: ago(30),
    ...overrides,
  };
}

const NODES: VoiceMediaNode[] = [
  node(),
  // A colon is a legal node id character, and must be URL encoded.
  node({ node_id: "media:b", max_concurrent: 10, held: 0, draining: true, healthy: false, heartbeat_at: ago(5 * 60 + 10) }),
  node({ node_id: "media-c", max_concurrent: 8, held: 8, heartbeat_at: ago(2 * 3600 + 5) }),
];

// The exact shape the server sends (VoiceMetricsSnapshot in the backend).
const METRICS: VoiceMetrics = {
  admitted_total: 7,
  active_calls: 2,
  // Unsorted on purpose: the panel sorts the lines by reason code.
  rejections_by_reason: { node_draining: 2, capacity_workspace: 1 },
  releases_by_reason: {},
  admission_latency_ms: { count: 4, mean: 12.5, p50: 10.2, p95: 25.5, p99: 30, max: 30 },
};

const RECOVER_EMPTY: VoiceRecoverResponse = {
  lost_nodes: [],
  failed_calls: { node_lost: [], lease_expired: [] },
};

const RECOVER_RESULT: VoiceRecoverResponse = {
  lost_nodes: ["media-z"],
  failed_calls: { node_lost: ["call-1", "call-2"], lease_expired: ["call-3"] },
};

function workspaceOnly(held = 0, limit = 200): VoiceCapacityResponse {
  return { workspace: { workspace_id: "ws-1", held, limit } };
}

function withDeployment(nodes: VoiceMediaNode[] = NODES): VoiceCapacityResponse {
  return {
    ...workspaceOnly(),
    deployment: { profile: "pilot", held: 11, limit: 50, nodes },
    metrics: METRICS,
  };
}

type Answer = VoiceCapacityResponse | Error | "pending";
type RecoverAnswer = Answer | VoiceRecoverResponse;

/**
 * Serves GET /voice/capacity from `capacity` in order (the last answer
 * repeats), POST /voice/nodes/{id}/drain from `drain` and POST /voice/recover
 * from `recover` (the empty recovery when omitted).
 */
function mockApi({
  capacity,
  drain,
  recover,
}: {
  capacity: Answer[];
  drain?: Answer | ((body: unknown) => unknown);
  recover?: RecoverAnswer;
}) {
  const answers = [...capacity];
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) => {
    if (url !== "/voice/capacity") throw apiError(404, "not_found");
    const next = answers.length > 1 ? answers.shift()! : answers[0];
    if (next === "pending") return new Promise(() => undefined);
    if (next instanceof Error) throw next;
    return okResponse(next);
  });
  const post = vi.spyOn(api, "post").mockImplementation(async (url: string, body?: unknown) => {
    if (url === "/voice/recover") {
      if (recover === "pending") return new Promise(() => undefined);
      if (recover === undefined) return okResponse(RECOVER_EMPTY);
      if (recover instanceof Error) throw recover;
      return okResponse(recover);
    }
    if (!url.endsWith("/drain")) throw apiError(404, "not_found");
    if (drain === "pending") return new Promise(() => undefined);
    if (drain instanceof Error) throw drain;
    const draining = (body as { draining: boolean }).draining;
    return okResponse({ node_id: "x", max_concurrent: 20, draining, generation: 5, heartbeat_at: ago(0) });
  });
  return { get, post };
}

function capacityCalls(get: ReturnType<typeof mockApi>["get"]) {
  return get.mock.calls.filter(([url]) => url === "/voice/capacity").length;
}

function renderPage(lang: "fa" | "en" = "en") {
  if (lang === "fa") window.localStorage.removeItem("homayar_lang");
  const workspace = { ...FIXTURE_WORKSPACE, role: "admin" as const };
  return renderConsole(<VoiceOperationsPage />, {
    route: "/admin/voice",
    lang,
    auth: { status: "authenticated", user: FIXTURE_OPERATOR, workspace, workspaces: [workspace] },
  });
}

function region(name: string) {
  return screen.getByRole("region", { name });
}

/** The <dd> that belongs to the <dt> with text `term` inside `scope`. */
function definitionOf(scope: HTMLElement, term: string) {
  const dt = within(scope).getByText(term);
  return within(dt.parentElement as HTMLElement).getByRole("definition");
}

/** The texts of one table row, row header first. */
function rowTexts(nodeId: string) {
  const header = screen.getByRole("rowheader", { name: nodeId });
  const row = header.closest('[role="row"]') as HTMLElement;
  return [header, ...within(row).getAllByRole("gridcell")].map((cell) => cell.textContent);
}

function chipIn(nodeId: string, text: string) {
  const row = screen.getByRole("rowheader", { name: nodeId }).closest('[role="row"]') as HTMLElement;
  return within(row).getByText(text).closest(".chip");
}

describe("VoiceOperationsPage", () => {
  beforeEach(() => {
    // Only the clock is fixed, so heartbeat ages do not drift; timers stay real.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows the header, the three sections and the two meters with their texts", async () => {
    const { get } = mockApi({ capacity: [withDeployment()] });
    renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "Voice operations" })).toBeInTheDocument();
    await screen.findByRole("grid", { name: "Media nodes" }, LOAD);

    expect(get).toHaveBeenCalledWith("/voice/capacity");
    for (const name of ["Workspace capacity", "Deployment capacity", "Media nodes"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(screen.getAllByRole("meter")).toHaveLength(2);
    const workspaceMeter = within(region("Workspace capacity")).getByRole("meter", { name: "Concurrent calls" });
    expect(workspaceMeter).toHaveAttribute("aria-valuenow", "0");
    expect(within(region("Workspace capacity")).getByText("0 / 200")).toBeInTheDocument();
    const deploymentMeter = within(region("Deployment capacity")).getByRole("meter", { name: "All nodes" });
    expect(deploymentMeter).toHaveAttribute("aria-valuenow", "22");
    expect(within(region("Deployment capacity")).getByText("11 / 50")).toBeInTheDocument();
    expect(within(region("Deployment capacity")).getByText("pilot")).toBeInTheDocument();
  }, SLOW);

  it("lists one row per node: health, heartbeat age, calls, status and the right button", async () => {
    mockApi({ capacity: [withDeployment()] });
    renderPage();
    const table = await screen.findByRole("grid", { name: "Media nodes" }, LOAD);

    const columns = within(table).getAllByRole("columnheader").map((column) => column.textContent);
    expect(columns).toEqual(["Node", "Health", "Last heartbeat", "Calls", "Status", "Actions"]);
    expect(rowTexts("media-a")).toEqual(["media-a", "Healthy", "30 s ago", "3 / 20", "Active", "Drain"]);
    expect(rowTexts("media:b")).toEqual(["media:b", "Unhealthy", "5 min ago", "0 / 10", "Draining", "Resume"]);
    expect(rowTexts("media-c")).toEqual(["media-c", "Healthy", "2 h ago", "8 / 8", "Active", "Drain"]);
  }, SLOW);

  it("colors the chips, writes node ids left to right and names each button after its node", async () => {
    mockApi({ capacity: [withDeployment()] });
    renderPage();
    await screen.findByRole("grid", { name: "Media nodes" }, LOAD);

    expect(screen.getByRole("rowheader", { name: "media-a" }).querySelector('[dir="ltr"]')).toHaveTextContent("media-a");
    expect(chipIn("media-a", "Healthy")).toHaveClass("chip--success");
    expect(chipIn("media:b", "Unhealthy")).toHaveClass("chip--danger");
    expect(chipIn("media:b", "Draining")).toHaveClass("chip--warning");
    expect(chipIn("media-a", "Active")).toHaveClass("chip--default");

    expect(screen.getByRole("button", { name: "Drain media-a" })).toHaveTextContent("Drain");
    expect(screen.getByRole("button", { name: "Resume media:b" })).toHaveTextContent("Resume");
    expect(screen.getByRole("button", { name: "Drain media-c" })).toBeInTheDocument();
  }, SLOW);

  it("shows the operator-only text, and no table or drain button, when the answer has no deployment", async () => {
    mockApi({ capacity: [workspaceOnly()] });
    const denied = renderPage();
    expect(await screen.findByText("Deployment details are only shown to platform operators.", undefined, LOAD)).toBeInTheDocument();
    expect(within(region("Workspace capacity")).getByText("0 / 200")).toBeInTheDocument();
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Media nodes" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Deployment capacity" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Drain/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole("meter")).toHaveLength(1);
    denied.unmount();
    vi.restoreAllMocks();

    // Allow-control: the same answer with a deployment.
    mockApi({ capacity: [withDeployment()] });
    renderPage();
    expect(await screen.findByRole("grid", { name: "Media nodes" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Drain media-a" })).toBeInTheDocument();
    expect(screen.queryByText("Deployment details are only shown to platform operators.")).not.toBeInTheDocument();
  }, SLOW);

  it("says no nodes are registered when the deployment has none", async () => {
    mockApi({ capacity: [withDeployment([])] });
    renderPage();
    expect(await screen.findByText("No media nodes are registered.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Media nodes" })).toBeInTheDocument();
    expect(screen.queryByRole("grid")).not.toBeInTheDocument();
    expect(screen.getAllByRole("meter")).toHaveLength(2);
  }, SLOW);

  it("shows the off text instead of the workspace meter when the limit is 0", async () => {
    mockApi({ capacity: [{ ...withDeployment(), workspace: { workspace_id: "ws-1", held: 0, limit: 0 } }] });
    renderPage();
    const section = await screen.findByRole("region", { name: "Workspace capacity" }, LOAD);
    expect(await within(section).findByText("Voice calls are off for this workspace.", undefined, LOAD)).toBeInTheDocument();
    expect(within(section).queryByRole("meter")).not.toBeInTheDocument();
  }, SLOW);

  it("shows the off text instead of the All nodes meter when the deployment limit is 0", async () => {
    mockApi({ capacity: [{ ...withDeployment(), deployment: { profile: "pilot", held: 0, limit: 0, nodes: NODES } }] });
    renderPage();
    const section = await screen.findByRole("region", { name: "Deployment capacity" }, LOAD);
    expect(await within(section).findByText("Voice calls are off for the whole deployment.", undefined, LOAD)).toBeInTheDocument();
    expect(within(section).queryByRole("meter")).not.toBeInTheDocument();
    // The profile line stays, and the workspace meter is untouched.
    expect(within(section).getByText("pilot")).toBeInTheDocument();
    expect(within(region("Workspace capacity")).getByRole("meter", { name: "Concurrent calls" })).toBeInTheDocument();
  }, SLOW);

  it("shows every runtime metrics value, reason lines sorted and None for an empty map", async () => {
    mockApi({ capacity: [withDeployment()] });
    renderPage();
    const section = await screen.findByRole("region", { name: "Runtime metrics" }, LOAD);
    expect(within(section).getByText("Counted by this API process since it started.")).toBeInTheDocument();
    expect(definitionOf(section, "Calls admitted")).toHaveTextContent("7");
    expect(definitionOf(section, "Active calls")).toHaveTextContent("2");
    const rejections = definitionOf(section, "Rejections by reason");
    expect(rejections.textContent).toBe(`${isolateLtr("capacity_workspace")}: 1${isolateLtr("node_draining")}: 2`);
    expect(definitionOf(section, "Ended calls by reason")).toHaveTextContent("None");
    expect(definitionOf(section, "Admission samples")).toHaveTextContent("4");
    expect(definitionOf(section, "Admission latency, mean")).toHaveTextContent("12.5 ms");
    expect(definitionOf(section, "Admission latency, p50")).toHaveTextContent("10.2 ms");
    expect(definitionOf(section, "Admission latency, p95")).toHaveTextContent("25.5 ms");
    expect(definitionOf(section, "Admission latency, p99")).toHaveTextContent("30 ms");
    expect(definitionOf(section, "Admission latency, max")).toHaveTextContent("30 ms");
  }, SLOW);

  it("shows no runtime metrics section when the answer has no metrics", async () => {
    mockApi({ capacity: [{ ...withDeployment(), metrics: undefined }] });
    renderPage();
    await screen.findByRole("grid", { name: "Media nodes" }, LOAD);
    expect(screen.queryByRole("heading", { name: "Runtime metrics" })).not.toBeInTheDocument();
    expect(screen.queryByText("Counted by this API process since it started.")).not.toBeInTheDocument();
  }, SLOW);

  it("shows the loading state, then the first-load error with a working Try again", async () => {
    mockApi({ capacity: ["pending"] });
    const loading = renderPage();
    expect(screen.getByRole("status")).toHaveTextContent("Loading voice capacity");
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
    loading.unmount();
    vi.restoreAllMocks();

    const failure = apiError(500, "internal_error");
    const { get } = mockApi({ capacity: [failure, withDeployment()] });
    const user = userEvent.setup();
    renderPage();
    const alert = await screen.findByRole("alert", undefined, LOAD);
    expect(alert).toHaveTextContent(describeApiError(failure, { lang: "en" }).text);
    expect(screen.getByRole("heading", { level: 1, name: "Voice operations" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("grid", { name: "Media nodes" }, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(capacityCalls(get)).toBe(2);
  }, SLOW);
});

describe("VoiceOperationsPage refresh", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("asks for the capacity again every 15 seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { get } = mockApi({ capacity: [withDeployment()] });
    renderPage();
    await screen.findByRole("grid", { name: "Media nodes" }, LOAD);
    expect(capacityCalls(get)).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(14_000);
    });
    expect(capacityCalls(get)).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_100);
    });
    expect(capacityCalls(get)).toBe(2);
  }, SLOW);

  it("keeps the last data on screen when a background refresh fails", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { get } = mockApi({ capacity: [withDeployment(), apiError(503, "service_unavailable")] });
    renderPage();
    await screen.findByRole("grid", { name: "Media nodes" }, LOAD);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_100);
    });
    expect(capacityCalls(get)).toBe(2);
    expect(screen.getByRole("grid", { name: "Media nodes" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "media-a" })).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  }, SLOW);

  it("moves the heartbeat age forward as time passes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.setSystemTime(NOW);
    mockApi({ capacity: [withDeployment([node({ heartbeat_at: ago(50) })])] });
    renderPage();
    await screen.findByRole("grid", { name: "Media nodes" }, LOAD);
    expect(rowTexts("media-a")[2]).toMatch(/^5\d s ago$/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20_000);
    });
    expect(rowTexts("media-a")[2]).toBe("1 min ago");
  }, SLOW);
});

describe("VoiceOperationsPage drain and resume", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  async function openDialog(buttonName: string, dialogName: string) {
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: buttonName }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: dialogName }, LOAD);
    return { user, dialog };
  }

  it("asks first and sends nothing on Cancel", async () => {
    const { post } = mockApi({ capacity: [withDeployment()] });
    renderPage();
    const { user, dialog } = await openDialog("Drain media-a", `Drain ${isolateLtr("media-a")}?`);
    expect(within(dialog).getByText("New calls will not go to this node. Calls on it now keep running.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Drain node" })).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: `Drain ${isolateLtr("media-a")}?` })).not.toBeInTheDocument());
    expect(post).not.toHaveBeenCalled();
  }, SLOW);

  it("drains on confirm with one POST, closes, toasts and reads the capacity again", async () => {
    const { get, post } = mockApi({ capacity: [withDeployment()] });
    renderPage();
    const { user, dialog } = await openDialog("Drain media-a", `Drain ${isolateLtr("media-a")}?`);
    expect(capacityCalls(get)).toBe(1);

    await user.click(within(dialog).getByRole("button", { name: "Drain node" }));
    expect(await screen.findByText(`${isolateLtr("media-a")} is draining.`, undefined, LOAD)).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/voice/nodes/media-a/drain", { draining: true });
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: `Drain ${isolateLtr("media-a")}?` })).not.toBeInTheDocument());
    await waitFor(() => expect(capacityCalls(get)).toBe(2));
  }, SLOW);

  it("resumes a draining node with draining false and URL encodes its id", async () => {
    const { get, post } = mockApi({ capacity: [withDeployment()] });
    renderPage();
    const { user, dialog } = await openDialog("Resume media:b", `Resume ${isolateLtr("media:b")}?`);
    expect(within(dialog).getByText("The node takes new calls again.")).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Resume node" }));
    expect(await screen.findByText(`${isolateLtr("media:b")} takes calls again.`, undefined, LOAD)).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/voice/nodes/media%3Ab/drain", { draining: false });
    await waitFor(() => expect(capacityCalls(get)).toBe(2));
  }, SLOW);

  it("disables the confirm button while the POST runs, so a double click sends one request", async () => {
    const { post } = mockApi({ capacity: [withDeployment()], drain: "pending" });
    renderPage();
    const { user, dialog } = await openDialog("Drain media-a", `Drain ${isolateLtr("media-a")}?`);
    const confirm = within(dialog).getByRole("button", { name: "Drain node" });
    await user.dblClick(confirm);
    await user.click(confirm);
    expect(post).toHaveBeenCalledTimes(1);
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("alertdialog", { name: `Drain ${isolateLtr("media-a")}?` })).toBeInTheDocument();
  }, SLOW);

  it.each([
    ["403 platform_operator_required", apiError(403, "platform_operator_required"), "Only platform operators can drain nodes."],
    ["404 not_found", apiError(404, "not_found"), "This node no longer exists."],
  ])("keeps the dialog open with its own text on %s", async (_label, error, text) => {
    const { get } = mockApi({ capacity: [withDeployment()], drain: error });
    renderPage();
    const { user, dialog } = await openDialog("Drain media-a", `Drain ${isolateLtr("media-a")}?`);
    await user.click(within(dialog).getByRole("button", { name: "Drain node" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(text);
    expect(screen.getByRole("alertdialog", { name: `Drain ${isolateLtr("media-a")}?` })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Drain node" })).not.toBeDisabled();
    expect(capacityCalls(get)).toBe(1);
  }, SLOW);

  it.each([
    ["403 permission_denied", apiError(403, "permission_denied")],
    ["409 voice_state_error", apiError(409, "voice_state_error")],
    ["500 internal_error", apiError(500, "internal_error")],
  ])("shows what describeApiError says for %s", async (_label, error) => {
    mockApi({ capacity: [withDeployment()], drain: error });
    renderPage();
    const { user, dialog } = await openDialog("Resume media:b", `Resume ${isolateLtr("media:b")}?`);
    await user.click(within(dialog).getByRole("button", { name: "Resume node" }));
    const alert = await within(dialog).findByRole("alert", undefined, LOAD);
    expect(alert.textContent).toBe(describeApiError(error, { lang: "en" }).text);
    // The server's English message ("fixture <code>") is never shown.
    expect(alert.textContent).not.toMatch(/fixture [a-z_]+/);
  }, SLOW);

  it("clears an old error when the dialog opens again", async () => {
    mockApi({ capacity: [withDeployment()], drain: apiError(409, "voice_state_error") });
    renderPage();
    const { user, dialog } = await openDialog("Drain media-a", `Drain ${isolateLtr("media-a")}?`);
    await user.click(within(dialog).getByRole("button", { name: "Drain node" }));
    await within(dialog).findByRole("alert", undefined, LOAD);
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: `Drain ${isolateLtr("media-a")}?` })).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "Drain media-c" }));
    const again = await screen.findByRole("alertdialog", { name: `Drain ${isolateLtr("media-c")}?` }, LOAD);
    expect(within(again).queryByRole("alert")).not.toBeInTheDocument();
  }, SLOW);
});

describe("VoiceOperationsPage recovery", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("shows the Recovery section, between Media nodes and Runtime metrics, only with a deployment", async () => {
    mockApi({ capacity: [workspaceOnly()] });
    const denied = renderPage();
    expect(await screen.findByText("Deployment details are only shown to platform operators.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Recovery" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Recover now" })).not.toBeInTheDocument();
    denied.unmount();
    vi.restoreAllMocks();

    // Allow-control: the same answer with a deployment.
    mockApi({ capacity: [withDeployment()] });
    renderPage();
    expect(await screen.findByRole("button", { name: "Recover now" }, LOAD)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recovery" })).toBeInTheDocument();
    expect(screen.getByText("Ends the calls of nodes that stopped sending heartbeats and frees call slots whose lease ran out. The worker also runs this on a schedule.")).toBeInTheDocument();
    const titles = screen.getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(titles.indexOf("Media nodes")).toBeLessThan(titles.indexOf("Recovery"));
    expect(titles.indexOf("Recovery")).toBeLessThan(titles.indexOf("Runtime metrics"));
  }, SLOW);

  it("asks first and sends nothing on Cancel", async () => {
    const { post } = mockApi({ capacity: [withDeployment()] });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Recover now" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "Run recovery now?" }, LOAD);
    expect(within(dialog).getByText("Nodes with no recent heartbeat are marked lost. Their live calls, and calls whose slot lease ran out, end as failed. This cannot be undone.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Run recovery" })).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: "Run recovery now?" })).not.toBeInTheDocument());
    expect(post).not.toHaveBeenCalled();
  }, SLOW);

  it("recovers on confirm with one POST, closes, toasts and reads the capacity again", async () => {
    const { get, post } = mockApi({ capacity: [withDeployment()], recover: RECOVER_RESULT });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Recover now" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "Run recovery now?" }, LOAD);
    expect(capacityCalls(get)).toBe(1);

    await user.click(within(dialog).getByRole("button", { name: "Run recovery" }));
    expect(await screen.findByText("Recovery done: 1 nodes marked lost, 3 calls ended.", undefined, LOAD)).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/voice/recover");
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: "Run recovery now?" })).not.toBeInTheDocument());
    await waitFor(() => expect(capacityCalls(get)).toBe(2));
  }, SLOW);

  it("toasts that nothing needed recovery when the answer is empty", async () => {
    const { post } = mockApi({ capacity: [withDeployment()] });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Recover now" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "Run recovery now?" }, LOAD);

    await user.click(within(dialog).getByRole("button", { name: "Run recovery" }));
    expect(await screen.findByText("Recovery done. Nothing needed recovery.", undefined, LOAD)).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/voice/recover");
  }, SLOW);

  it("sends one POST on a double click while the request runs", async () => {
    const { post } = mockApi({ capacity: [withDeployment()], recover: "pending" });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Recover now" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "Run recovery now?" }, LOAD);
    const confirm = within(dialog).getByRole("button", { name: "Run recovery" });
    await user.dblClick(confirm);
    await user.click(confirm);
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith("/voice/recover");
    expect(confirm).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("alertdialog", { name: "Run recovery now?" })).toBeInTheDocument();
  }, SLOW);

  it.each([
    ["403 platform_operator_required", apiError(403, "platform_operator_required"), "Only platform operators can run recovery."],
  ])("keeps the dialog open with its own text on %s", async (_label, error, text) => {
    const { get } = mockApi({ capacity: [withDeployment()], recover: error });
    renderPage();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Recover now" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "Run recovery now?" }, LOAD);
    await user.click(within(dialog).getByRole("button", { name: "Run recovery" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent(text);
    expect(screen.getByRole("alertdialog", { name: "Run recovery now?" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Run recovery" })).not.toBeDisabled();
    expect(capacityCalls(get)).toBe(1);
  }, SLOW);
});

describe("VoiceOperationsPage in Persian", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("uses Persian text everywhere and only Persian digits", async () => {
    const { post } = mockApi({ capacity: [withDeployment()] });
    const { container } = renderPage("fa");
    expect(screen.getByRole("heading", { level: 1, name: "عملیات صوتی" })).toBeInTheDocument();
    await screen.findByRole("grid", { name: "گره‌های رسانه" }, LOAD);

    for (const name of ["ظرفیت فضای کاری", "ظرفیت استقرار", "گره‌های رسانه"]) {
      expect(screen.getByRole("heading", { name })).toBeInTheDocument();
    }
    expect(within(region("ظرفیت فضای کاری")).getByRole("meter", { name: "تماس‌های هم‌زمان" })).toBeInTheDocument();
    expect(within(region("ظرفیت فضای کاری")).getByText("۰ / ۲۰۰")).toBeInTheDocument();
    expect(within(region("ظرفیت استقرار")).getByRole("meter", { name: "همهٔ گره‌ها" })).toBeInTheDocument();
    expect(within(region("ظرفیت استقرار")).getByText("۱۱ / ۵۰")).toBeInTheDocument();

    const columns = within(screen.getByRole("grid", { name: "گره‌های رسانه" }))
      .getAllByRole("columnheader")
      .map((column) => column.textContent);
    expect(columns).toEqual(["گره", "سلامت", "آخرین ضربان", "تماس‌ها", "وضعیت", "اقدام‌ها"]);
    expect(rowTexts("media-a")).toEqual(["media-a", "سالم", "۳۰ ثانیه پیش", "۳ / ۲۰", "فعال", "تخلیه"]);
    expect(rowTexts("media:b")).toEqual(["media:b", "ناسالم", "۵ دقیقه پیش", "۰ / ۱۰", "در حال تخلیه", "ازسرگیری"]);
    expect(rowTexts("media-c")[2]).toBe("۲ ساعت پیش");
    expect(screen.getByRole("button", { name: "ازسرگیری media:b" })).toBeInTheDocument();
    // Only Persian digits, except the fixed "p50"-style percentile labels of the copy.
    expect(container.textContent.replace(/p(?:50|95|99)/g, "")).not.toMatch(/[0-9]/);

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "ازسرگیری media:b" }));
    const resume = await screen.findByRole("alertdialog", { name: `${isolateLtr("media:b")} از سر گرفته شود؟` }, LOAD);
    expect(within(resume).getByText("گره دوباره تماس تازه می‌گیرد.")).toBeInTheDocument();
    expect(within(resume).getByRole("button", { name: "ازسرگیری گره" })).toBeInTheDocument();
    await user.click(within(resume).getByRole("button", { name: "انصراف" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog", { name: `${isolateLtr("media:b")} از سر گرفته شود؟` })).not.toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: "تخلیه media-a" }));
    const drain = await screen.findByRole("alertdialog", { name: `${isolateLtr("media-a")} تخلیه شود؟` }, LOAD);
    expect(within(drain).getByText("تماس‌های تازه به این گره نمی‌روند. تماس‌های جاری ادامه پیدا می‌کنند.")).toBeInTheDocument();
    await user.click(within(drain).getByRole("button", { name: "تخلیه گره" }));
    expect(await screen.findByText(`${isolateLtr("media-a")} در حال تخلیه است.`, undefined, LOAD)).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/voice/nodes/media-a/drain", { draining: true });
  }, SLOW);

  it("isolates a node id that starts with a digit in the Persian heading and toast", async () => {
    mockApi({ capacity: [withDeployment([node({ node_id: "1-media" })])] });
    renderPage("fa");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "تخلیه 1-media" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "\u20661-media\u2069 تخلیه شود؟" }, LOAD);
    // U+2066 (left-to-right isolate) opens and U+2069 (pop directional isolate) closes
    // the id, so the bidi rules cannot move "1" or "-" to the other side in Persian.
    expect(within(dialog).getByRole("heading").textContent).toBe("\u20661-media\u2069 تخلیه شود؟");

    await user.click(within(dialog).getByRole("button", { name: "تخلیه گره" }));
    expect(await screen.findByText("\u20661-media\u2069 در حال تخلیه است.", undefined, LOAD)).toBeInTheDocument();
  }, SLOW);

  it("shows the Persian operator-only, empty, off and error texts", async () => {
    mockApi({ capacity: [workspaceOnly(0, 0)] });
    const first = renderPage("fa");
    expect(await screen.findByText("جزئیات استقرار فقط به اپراتورهای پلتفرم نشان داده می‌شود.", undefined, LOAD)).toBeInTheDocument();
    expect(screen.getByText("تماس صوتی برای این فضای کاری خاموش است.")).toBeInTheDocument();
    first.unmount();
    vi.restoreAllMocks();

    mockApi({ capacity: [{ ...withDeployment(), deployment: { profile: "pilot", held: 0, limit: 0, nodes: [] } }] });
    const deploymentOff = renderPage("fa");
    expect(await screen.findByText("تماس صوتی برای کل استقرار خاموش است.", undefined, LOAD)).toBeInTheDocument();
    expect(within(region("ظرفیت استقرار")).queryByRole("meter")).not.toBeInTheDocument();
    deploymentOff.unmount();
    vi.restoreAllMocks();

    mockApi({ capacity: [withDeployment([])] });
    const second = renderPage("fa");
    expect(await screen.findByText("هیچ گره رسانه‌ای ثبت نشده است.", undefined, LOAD)).toBeInTheDocument();
    second.unmount();
    vi.restoreAllMocks();

    mockApi({ capacity: ["pending"] });
    const third = renderPage("fa");
    expect(screen.getByRole("status")).toHaveTextContent("در حال بارگذاری ظرفیت صوتی");
    third.unmount();
    vi.restoreAllMocks();

    mockApi({ capacity: [withDeployment()], drain: apiError(403, "platform_operator_required") });
    renderPage("fa");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "تخلیه media-a" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: `${isolateLtr("media-a")} تخلیه شود؟` }, LOAD);
    await user.click(within(dialog).getByRole("button", { name: "تخلیه گره" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent("فقط اپراتورهای پلتفرم می‌توانند گره را تخلیه کنند.");
  }, SLOW);

  it("shows the Persian metrics with Persian digits and the Persian recovery texts", async () => {
    const { post } = mockApi({ capacity: [withDeployment()], recover: RECOVER_RESULT });
    renderPage("fa");
    const section = await screen.findByRole("region", { name: "سنجه‌های اجرا" }, LOAD);
    expect(within(section).getByText("شمارش همین فرایند API از زمان شروع آن.")).toBeInTheDocument();
    expect(definitionOf(section, "تماس‌های پذیرفته")).toHaveTextContent("۷");
    expect(definitionOf(section, "تماس‌های فعال")).toHaveTextContent("۲");
    const rejections = definitionOf(section, "ردها بر اساس دلیل");
    expect(rejections.textContent).toBe(`${isolateLtr("capacity_workspace")}: ۱${isolateLtr("node_draining")}: ۲`);
    expect(definitionOf(section, "تماس‌های پایان‌یافته بر اساس دلیل")).toHaveTextContent("هیچ");
    expect(definitionOf(section, "نمونه‌های پذیرش")).toHaveTextContent("۴");
    expect(definitionOf(section, "تأخیر پذیرش، میانگین")).toHaveTextContent("۱۲٫۵ میلی‌ثانیه");
    expect(definitionOf(section, "تأخیر پذیرش، بیشینه")).toHaveTextContent("۳۰ میلی‌ثانیه");

    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "اکنون بازیابی کن" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: "بازیابی اکنون اجرا شود؟" }, LOAD);
    expect(within(dialog).getByText("گره‌هایی که ضربان تازه نفرستاده‌اند از دست رفته علامت می‌خورند. تماس‌های جاری روی آن‌ها و تماس‌هایی که اجارهٔ جایشان تمام شده، ناموفق پایان می‌یابند. این کار برگشت‌پذیر نیست.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "اجرای بازیابی" }));
    expect(await screen.findByText("بازیابی انجام شد: ۱ گره از دست رفته، ۳ تماس پایان یافت.", undefined, LOAD)).toBeInTheDocument();
    expect(post).toHaveBeenCalledWith("/voice/recover");
  }, SLOW);

  it("shows the Persian not-found text, toast for resume and the Try again button", async () => {
    mockApi({ capacity: [withDeployment()], drain: apiError(404, "not_found") });
    const first = renderPage("fa");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "تخلیه media-a" }, LOAD));
    const dialog = await screen.findByRole("alertdialog", { name: `${isolateLtr("media-a")} تخلیه شود؟` }, LOAD);
    await user.click(within(dialog).getByRole("button", { name: "تخلیه گره" }));
    expect(await within(dialog).findByRole("alert", undefined, LOAD)).toHaveTextContent("این گره دیگر وجود ندارد.");
    first.unmount();
    vi.restoreAllMocks();

    mockApi({ capacity: [withDeployment()] });
    const second = renderPage("fa");
    await user.click(await screen.findByRole("button", { name: "ازسرگیری media:b" }, LOAD));
    const resume = await screen.findByRole("alertdialog", { name: `${isolateLtr("media:b")} از سر گرفته شود؟` }, LOAD);
    await user.click(within(resume).getByRole("button", { name: "ازسرگیری گره" }));
    expect(await screen.findByText(`${isolateLtr("media:b")} دوباره تماس می‌گیرد.`, undefined, LOAD)).toBeInTheDocument();
    second.unmount();
    vi.restoreAllMocks();

    mockApi({ capacity: [apiError(500, "internal_error")] });
    renderPage("fa");
    expect(await screen.findByRole("button", { name: "تلاش دوباره" }, LOAD)).toBeInTheDocument();
  }, SLOW);
});
