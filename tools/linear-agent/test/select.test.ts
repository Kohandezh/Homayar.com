import { describe, expect, it } from "vitest";
import { branchName, selectCandidates } from "../src/index";
import { IN_PROGRESS, makeIssue, testConfig } from "./fakes";

const ids = (issues: { identifier: string }[]) => issues.map((issue) => issue.identifier);

describe("selectCandidates: deny cases and their allow controls", () => {
  const config = testConfig();
  const pairs: [string, ReturnType<typeof makeIssue>, ReturnType<typeof makeIssue>][] = [
    ["Todo issue without agent label", makeIssue({ labels: ["backend"] }), makeIssue({ labels: ["backend", "agent"] })],
    ["agent plus agent-failed", makeIssue({ labels: ["agent", "agent-failed"] }), makeIssue({ labels: ["agent"] })],
    ["label Agent with another case", makeIssue({ labels: ["Agent"] }), makeIssue({ labels: ["agent"] })],
    ["In Progress with agent", makeIssue({ state: IN_PROGRESS }), makeIssue()],
    ["another team", makeIssue({ teamKey: "ENG" }), makeIssue({ teamKey: "HOM" })],
    [
      "blocked by a started issue",
      makeIssue({ blockedBy: [{ identifier: "HOM-1", stateType: "started" }] }),
      makeIssue({ blockedBy: [{ identifier: "HOM-1", stateType: "completed" }] }),
    ],
    [
      "blocked by an unstarted issue",
      makeIssue({ blockedBy: [{ identifier: "HOM-1", stateType: "unstarted" }, { identifier: "HOM-2", stateType: "canceled" }] }),
      makeIssue({ blockedBy: [{ identifier: "HOM-1", stateType: "canceled" }, { identifier: "HOM-2", stateType: "completed" }] }),
    ],
  ];

  it.each(pairs)("%s: deny is not picked, allow is picked", (_name, deny, allow) => {
    expect(selectCandidates([deny], config)).toEqual([]);
    expect(ids(selectCandidates([allow], config))).toEqual(["HOM-12"]);
  });

  it("uses the configured names", () => {
    const custom = testConfig({ teamKey: "ENG", todoState: "Ready", triggerLabel: "bot", failedLabel: "bot-failed" });
    const issue = makeIssue({ teamKey: "ENG", state: { id: "r", name: "Ready", type: "unstarted" }, labels: ["bot"] });
    expect(ids(selectCandidates([issue], custom))).toEqual(["HOM-12"]);
    expect(selectCandidates([{ ...issue, labels: ["bot", "bot-failed"] }], custom)).toEqual([]);
  });
});

describe("selectCandidates: order", () => {
  it("priority 1 to 4 first, no priority last", () => {
    const issues = [0, 4, 2, 1, 3].map((priority, i) => makeIssue({ number: i + 1, priority }));
    expect(selectCandidates(issues, testConfig()).map((i) => i.priority)).toEqual([1, 2, 3, 4, 0]);
  });

  it("equal priority: oldest createdAt first, then lower number", () => {
    const issues = [
      makeIssue({ number: 30, createdAt: "2026-10-03T00:00:00.000Z" }),
      makeIssue({ number: 20, createdAt: "2026-10-01T00:00:00.000Z" }),
      makeIssue({ number: 11, createdAt: "2026-10-02T00:00:00.000Z" }),
      makeIssue({ number: 10, createdAt: "2026-10-02T00:00:00.000Z" }),
    ];
    expect(ids(selectCandidates(issues, testConfig()))).toEqual(["HOM-20", "HOM-10", "HOM-11", "HOM-30"]);
  });

  it("does not change the input array", () => {
    const issues = [makeIssue({ number: 2, priority: 3 }), makeIssue({ number: 1, priority: 1 })];
    selectCandidates(issues, testConfig());
    expect(ids(issues)).toEqual(["HOM-2", "HOM-1"]);
  });
});

describe("branchName", () => {
  it.each([
    [{ number: 12, title: "Add a health check" }, "agent/hom-12-add-a-health-check"],
    [{ number: 7, title: "  Fix: login -- page!! " }, "agent/hom-7-fix-login-page"],
    [{ number: 8, title: "Ünïcode & spaces" }, "agent/hom-8-n-code-spaces"],
    [{ number: 9, title: "شبکه" }, "agent/hom-9"],
    [{ number: 10, title: "" }, "agent/hom-10"],
  ])("%o gives %s", (issue, expected) => {
    expect(branchName(issue)).toBe(expected);
  });

  it("cuts the slug to 40 characters and drops a trailing dash after the cut", () => {
    const name = branchName({ number: 1, title: "a".repeat(39) + " bcdef" });
    expect(name).toBe(`agent/hom-1-${"a".repeat(39)}`);
    expect(branchName({ number: 2, title: "x".repeat(60) })).toBe(`agent/hom-2-${"x".repeat(40)}`);
  });
});
