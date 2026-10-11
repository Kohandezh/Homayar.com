import { describe, expect, it } from "vitest";
import { createLinearClient } from "../src/index";

interface Sent {
  query: string;
  variables: Record<string, unknown>;
  authorization: string | null;
}

/** A fetch stand-in: answers each GraphQL operation by name. No network. */
function fakeFetch(answers: Record<string, unknown | ((vars: Record<string, unknown>) => unknown)>) {
  const sent: Sent[] = [];
  const fetchFn = (async (_url: string | URL | Request, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body)) as { query: string; variables: Record<string, unknown> };
    const headers = new Headers(init?.headers);
    sent.push({ query: body.query, variables: body.variables, authorization: headers.get("Authorization") });
    const name = /(?:query|mutation)\s+(\w+)/.exec(body.query)?.[1] ?? "";
    const answer = answers[name];
    const payload = typeof answer === "function" ? answer(body.variables) : answer;
    return new Response(JSON.stringify(payload ?? { errors: [{ message: `no fake for ${name}` }] }), { status: 200 });
  }) as typeof fetch;
  return { fetchFn, sent };
}

const rawIssue = {
  id: "uuid-12",
  identifier: "HOM-12",
  number: 12,
  title: "Add a health check",
  description: null,
  url: "https://linear.app/homayar/issue/HOM-12",
  priority: 2,
  createdAt: "2026-10-01T10:00:00.000Z",
  team: { key: "HOM" },
  state: { id: "s1", name: "Todo", type: "unstarted" },
  labels: { nodes: [{ name: "agent" }, { name: "backend" }] },
  inverseRelations: {
    nodes: [
      { type: "blocks", issue: { identifier: "HOM-3", state: { type: "completed" } } },
      { type: "related", issue: { identifier: "HOM-4", state: { type: "started" } } },
    ],
  },
  comments: {
    nodes: [
      { body: "second", createdAt: "2026-10-03T00:00:00.000Z", user: null, botActor: { name: "GitHub" } },
      { body: "first", createdAt: "2026-10-02T00:00:00.000Z", user: { name: "sina", displayName: "Sina" }, botActor: null },
    ],
  },
};

const client = (fetchFn: typeof fetch) => createLinearClient({ apiKey: "lin_test_key", teamKey: "HOM", fetchFn });

describe("createLinearClient", () => {
  it("lists issues with the right filter, maps fields, and follows pages", async () => {
    const pages = [
      { data: { issues: { nodes: [rawIssue], pageInfo: { hasNextPage: true, endCursor: "c1" } } } },
      { data: { issues: { nodes: [{ ...rawIssue, id: "uuid-13", identifier: "HOM-13", number: 13 }], pageInfo: { hasNextPage: false, endCursor: null } } } },
    ];
    const { fetchFn, sent } = fakeFetch({ LinearAgentIssues: () => pages.shift() });
    const issues = await client(fetchFn).listIssues({ teamKey: "HOM", stateName: "Todo", labelName: "agent" });

    expect(issues.map((i) => i.identifier)).toEqual(["HOM-12", "HOM-13"]);
    expect(issues[0]).toEqual({
      id: "uuid-12",
      identifier: "HOM-12",
      number: 12,
      title: "Add a health check",
      description: "",
      url: "https://linear.app/homayar/issue/HOM-12",
      priority: 2,
      createdAt: "2026-10-01T10:00:00.000Z",
      teamKey: "HOM",
      state: { id: "s1", name: "Todo", type: "unstarted" },
      labels: ["agent", "backend"],
      blockedBy: [{ identifier: "HOM-3", stateType: "completed" }],
      comments: [
        { author: "Sina", body: "first", createdAt: "2026-10-02T00:00:00.000Z" },
        { author: "GitHub", body: "second", createdAt: "2026-10-03T00:00:00.000Z" },
      ],
    });
    expect(sent[0].authorization).toBe("lin_test_key");
    expect(sent[0].variables).toMatchObject({ teamKey: "HOM", stateName: "Todo", labelName: "agent", after: null });
    expect(sent[1].variables.after).toBe("c1");
  });

  it("getIssue returns null for an unknown issue", async () => {
    const { fetchFn } = fakeFetch({ LinearAgentIssue: { errors: [{ message: "Entity not found: Issue" }] } });
    expect(await client(fetchFn).getIssue("HOM-999")).toBeNull();
  });

  it("other API errors throw", async () => {
    const { fetchFn } = fakeFetch({ LinearAgentIssue: { errors: [{ message: "Authentication required" }] } });
    await expect(client(fetchFn).getIssue("HOM-1")).rejects.toThrow("Authentication required");
  });

  it("lists states in workflow order", async () => {
    const { fetchFn } = fakeFetch({
      LinearAgentStates: {
        data: {
          workflowStates: {
            nodes: [
              { id: "b", name: "In Review", type: "started", position: 3 },
              { id: "a", name: "Todo", type: "unstarted", position: 1 },
            ],
          },
        },
      },
    });
    expect(await client(fetchFn).listStates("HOM")).toEqual([
      { id: "a", name: "Todo", type: "unstarted" },
      { id: "b", name: "In Review", type: "started" },
    ]);
  });

  it("finds labels by exact name, prefers the team label, and caches the id", async () => {
    const { fetchFn, sent } = fakeFetch({
      LinearAgentLabels: {
        data: {
          issueLabels: {
            nodes: [
              { id: "other-team", name: "agent-failed", team: { key: "ENG" } },
              { id: "workspace", name: "agent-failed", team: null },
              { id: "team", name: "agent-failed", team: { key: "HOM" } },
            ],
          },
        },
      },
      LinearAgentAddLabel: { data: { issueAddLabel: { success: true } } },
    });
    const linear = client(fetchFn);
    await linear.addLabel("uuid-12", "agent-failed");
    await linear.addLabel("uuid-13", "agent-failed");
    const adds = sent.filter((s) => s.query.includes("issueAddLabel"));
    expect(adds.map((s) => s.variables)).toEqual([
      { id: "uuid-12", labelId: "team" },
      { id: "uuid-13", labelId: "team" },
    ]);
    expect(sent.filter((s) => s.query.includes("issueLabels"))).toHaveLength(1);
  });

  it("addLabel fails when the label does not exist; removeLabel then does nothing", async () => {
    const { fetchFn, sent } = fakeFetch({ LinearAgentLabels: { data: { issueLabels: { nodes: [] } } } });
    const linear = client(fetchFn);
    await expect(linear.addLabel("uuid-12", "agent-failed")).rejects.toThrow('Label "agent-failed" not found');
    await linear.removeLabel("uuid-12", "agent-failed");
    expect(sent.some((s) => s.query.includes("issueRemoveLabel"))).toBe(false);
  });

  it("sends the mutations with the documented names", async () => {
    const { fetchFn, sent } = fakeFetch({
      LinearAgentSetState: { data: { issueUpdate: { success: true } } },
      LinearAgentComment: { data: { commentCreate: { success: true } } },
      LinearAgentAttachLink: { data: { attachmentLinkURL: { success: true } } },
      LinearAgentLabels: { data: { issueLabels: { nodes: [{ id: "l1", name: "agent", team: { key: "HOM" } }] } } },
      LinearAgentRemoveLabel: { data: { issueRemoveLabel: { success: true } } },
    });
    const linear = client(fetchFn);
    await linear.setState("uuid-12", "s2");
    await linear.addComment("uuid-12", "hello");
    await linear.attachLink("uuid-12", "https://github.com/example/repo/pull/1", "PR title");
    await linear.removeLabel("uuid-12", "agent");
    expect(sent.map((s) => s.variables)).toEqual([
      { id: "uuid-12", stateId: "s2" },
      { issueId: "uuid-12", body: "hello" },
      { issueId: "uuid-12", url: "https://github.com/example/repo/pull/1", title: "PR title" },
      { name: "agent" },
      { id: "uuid-12", labelId: "l1" },
    ]);
  });

  it("a mutation that does not succeed throws", async () => {
    const { fetchFn } = fakeFetch({ LinearAgentSetState: { data: { issueUpdate: { success: false } } } });
    await expect(client(fetchFn).setState("uuid-12", "s2")).rejects.toThrow("did not succeed");
  });
});
