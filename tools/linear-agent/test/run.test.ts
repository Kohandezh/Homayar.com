import { describe, expect, it } from "vitest";
import { runOnce } from "../src/index";
import type { TrackedPr } from "../src/index";
import { ALL_STATES, DONE, IN_PROGRESS, IN_REVIEW, TODO, createFakes, makeIssue, testConfig } from "./fakes";

const tracked = (overrides: Partial<TrackedPr> = {}): TrackedPr => ({
  issueId: "uuid-7",
  identifier: "HOM-7",
  prNumber: 77,
  prUrl: "https://github.com/example/repo/pull/77",
  branch: "agent/hom-7-old-work",
  worktreePath: "/wt/HOM-7",
  ...overrides,
});

describe("runOnce: pick and claim", () => {
  it("claims an eligible ticket: lock, re-read, In Progress, pickup comment", async () => {
    const fakes = createFakes();
    await runOnce(fakes.deps, testConfig());

    expect(fakes.named("lock.tryAcquire")[0].args).toEqual(["HOM-12"]);
    expect(fakes.named("linear.getIssue")[0].args).toEqual(["uuid-12"]);
    expect(fakes.named("linear.setState")[0].args).toEqual(["uuid-12", IN_PROGRESS.id]);
    expect(fakes.comments()[0]).toBe("Picked up by linear-agent at 2026-10-11T08:00:00.000Z");
    expect(fakes.named("lock.release")[0].args).toEqual(["HOM-12"]);
    expect(fakes.locks.size).toBe(0);
  });

  it("asks Linear for Todo + agent in the configured team", async () => {
    const fakes = createFakes();
    await runOnce(fakes.deps, testConfig());
    expect(fakes.named("linear.listIssues")[0].args).toEqual([
      { teamKey: "HOM", stateName: "Todo", labelName: "agent" },
    ]);
  });

  it("re-checks what listIssues returns and fills wouldPick in pick order", async () => {
    const issues = [
      makeIssue({ number: 1, priority: 0 }),
      makeIssue({ number: 2, priority: 3 }),
      makeIssue({ number: 3, labels: [] }),
      makeIssue({ number: 4, priority: 1 }),
    ];
    const fakes = createFakes({ issues });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.wouldPick).toEqual(["HOM-4", "HOM-2", "HOM-1"]);
    expect(summary.processed.map((p) => p.identifier)).toEqual(["HOM-4"]);
  });

  it("deny: lock already held gives skipped and no Linear write; allow: the next free ticket is claimed", async () => {
    const issues = [makeIssue({ number: 1, priority: 1 }), makeIssue({ number: 2, priority: 2 })];
    const fakes = createFakes({ issues, heldLocks: ["HOM-1"] });
    const summary = await runOnce(fakes.deps, testConfig());

    expect(summary.processed[0]).toMatchObject({ identifier: "HOM-1", outcome: "skipped" });
    expect(summary.processed[1]).toMatchObject({ identifier: "HOM-2", outcome: "in_review" });
    const writesForOne = fakes.linearWrites().filter((call) => call.args[0] === "uuid-1");
    expect(writesForOne).toEqual([]);
    expect(fakes.named("linear.getIssue").map((c) => c.args[0])).toEqual(["uuid-2"]);
  });

  it("deny: an issue that lost the agent label before the re-read is skipped with no write", async () => {
    const listed = [makeIssue()];
    const fakes = createFakes({ issues: listed, reread: [makeIssue({ labels: [] })] });
    const summary = await runOnce(fakes.deps, testConfig());

    expect(summary.processed).toEqual([{ identifier: "HOM-12", outcome: "skipped", reason: "not eligible on re-read" }]);
    expect(fakes.linearWrites()).toEqual([]);
    expect(fakes.named("lock.release")).toHaveLength(1);
  });

  it("deny: an issue that is gone on re-read is skipped with no write", async () => {
    const fakes = createFakes({ issues: [makeIssue()], reread: [] });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].outcome).toBe("skipped");
    expect(fakes.linearWrites()).toEqual([]);
  });

  it("claims at most maxConcurrent tickets; skipped ones do not count", async () => {
    const issues = [1, 2, 3, 4].map((number) => makeIssue({ number, priority: number }));
    const fakes = createFakes({ issues, heldLocks: ["HOM-1"] });
    const summary = await runOnce(fakes.deps, testConfig({ maxConcurrent: 2 }));
    expect(summary.processed.map((p) => [p.identifier, p.outcome])).toEqual([
      ["HOM-1", "skipped"],
      ["HOM-2", "in_review"],
      ["HOM-3", "in_review"],
    ]);
  });

  it("stops before the next ticket when shouldStop says so", async () => {
    const issues = [1, 2].map((number) => makeIssue({ number }));
    const fakes = createFakes({ issues });
    fakes.deps.shouldStop = () => fakes.named("lock.release").length > 0;
    const summary = await runOnce(fakes.deps, testConfig({ maxConcurrent: 5 }));
    expect(summary.processed.map((p) => p.identifier)).toEqual(["HOM-1"]);
  });
});

describe("runOnce: success", () => {
  it("runs worktree, maker, review, checks, push, PR, and moves the ticket to In Review", async () => {
    const issue = makeIssue({
      title: "Add a health check",
      labels: ["agent", "backend"],
      comments: [{ author: "Sina", body: "Use FastAPI.", createdAt: "2026-10-02T00:00:00.000Z" }],
    });
    const fakes = createFakes({
      issues: [issue],
      changedFiles: [["Homayar/frontend/src/App.tsx"]],
    });
    const summary = await runOnce(fakes.deps, testConfig());

    expect(summary.processed).toEqual([
      { identifier: "HOM-12", outcome: "in_review", prUrl: "https://github.com/example/repo/pull/101" },
    ]);
    expect(fakes.named("git.createWorktree")[0].args[0]).toEqual({
      path: "/wt/HOM-12",
      branch: "agent/hom-12-add-a-health-check",
      baseRef: "origin/main",
    });

    const maker = fakes.named("agent.runMaker")[0].args[0] as { cwd: string; prompt: string; maxBudgetUsd: number };
    expect(maker.cwd).toBe("/wt/HOM-12");
    expect(maker.maxBudgetUsd).toBe(5);
    for (const part of ["HOM-12", "Add a health check", "The API needs a /healthz route.", "agent", "backend", "Use FastAPI."]) {
      expect(maker.prompt).toContain(part);
    }

    const review = fakes.named("agent.runReviewer")[0].args[0] as { diff: string; maxBudgetUsd: number };
    expect(review.diff).toBe("diff --git a/tools/x.ts b/tools/x.ts");
    expect(review.maxBudgetUsd).toBe(4);

    const checks = fakes.named("checks.run")[0].args;
    expect(checks[0]).toBe("/wt/HOM-12");
    expect((checks[1] as { name: string }[]).map((c) => c.name)).toEqual([
      "frontend: install",
      "frontend: typecheck",
      "frontend: lint",
      "frontend: test",
      "frontend: build",
    ]);

    expect(fakes.named("git.push")[0].args).toEqual(["/wt/HOM-12", "agent/hom-12-add-a-health-check"]);
    const pr = fakes.named("github.createDraftPr")[0].args[0] as { branch: string; base: string; title: string; body: string };
    expect(pr.branch).toBe("agent/hom-12-add-a-health-check");
    expect(pr.base).toBe("main");
    expect(pr.title).toContain("HOM-12");
    expect(pr.body).toContain(issue.url);
    expect(pr.body).toContain("frontend: lint: passed");

    expect(fakes.comments().some((c) => c.includes("https://github.com/example/repo/pull/101"))).toBe(true);
    expect(fakes.named("linear.attachLink")[0].args).toEqual([
      "uuid-12",
      "https://github.com/example/repo/pull/101",
      pr.title,
    ]);
    expect(fakes.named("linear.setState").map((c) => c.args[1])).toEqual([IN_PROGRESS.id, IN_REVIEW.id]);
    expect(fakes.tracked()).toEqual([
      {
        issueId: "uuid-12",
        identifier: "HOM-12",
        prNumber: 101,
        prUrl: "https://github.com/example/repo/pull/101",
        branch: "agent/hom-12-add-a-health-check",
        worktreePath: "/wt/HOM-12",
      },
    ]);
  });

  it("keeps existing tracked PRs when it saves a new one", async () => {
    const fakes = createFakes({ tracked: [tracked()], prStates: { 77: "open" } });
    await runOnce(fakes.deps, testConfig());
    expect(fakes.tracked().map((t) => t.identifier)).toEqual(["HOM-7", "HOM-12"]);
  });

  it("says no surface checks applied when only tools/ changed", async () => {
    const fakes = createFakes({ changedFiles: [["tools/linear-agent/src/x.ts"]] });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].outcome).toBe("in_review");
    expect(fakes.named("checks.run")[0].args[1]).toEqual([]);
    const pr = fakes.named("github.createDraftPr")[0].args[0] as { body: string };
    expect(pr.body).toContain("No surface checks applied");
  });

  it("deny: a team without In Review gets no extra setState and a PR ready comment", async () => {
    const fakes = createFakes({ states: [TODO, IN_PROGRESS, DONE] });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0]).toMatchObject({ outcome: "pr_ready", prUrl: "https://github.com/example/repo/pull/101" });
    expect(fakes.named("linear.setState").map((c) => c.args[1])).toEqual([IN_PROGRESS.id]);
    expect(fakes.comments().some((c) => c.includes("PR ready"))).toBe(true);
  });

  it("runs the maker exactly once more with the findings when the reviewer asks for changes", async () => {
    const fakes = createFakes({
      reviewResults: [{ approved: false, findings: "Missing test for the 500 path.", costUsd: 0.5 }],
      changedFiles: [["Homayar/backend/tests/test_health.py"], ["Homayar/backend/tests/test_health.py"]],
    });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].outcome).toBe("in_review");
    const makers = fakes.named("agent.runMaker").map((c) => c.args[0] as { prompt: string; maxBudgetUsd: number });
    expect(makers).toHaveLength(2);
    expect(makers[1].prompt).toContain("Missing test for the 500 path.");
    expect(makers[1].maxBudgetUsd).toBe(3.5);
    expect(fakes.named("agent.runReviewer")).toHaveLength(1);
    expect(fakes.named("checks.run")[0].args[1]).toEqual([
      { name: "backend: pytest", cwd: "Homayar", command: "uv run pytest backend/tests/test_health.py" },
    ]);
  });
});

describe("runOnce: failures", () => {
  const expectFailurePath = (fakes: ReturnType<typeof createFakes>) => {
    expect(fakes.named("linear.setState").at(-1)?.args).toEqual(["uuid-12", TODO.id]);
    expect(fakes.named("linear.removeLabel")[0].args).toEqual(["uuid-12", "agent"]);
    expect(fakes.named("linear.addLabel")[0].args).toEqual(["uuid-12", "agent-failed"]);
    expect(fakes.named("lock.release")).toHaveLength(1);
    expect(fakes.named("git.removeWorktree")).toEqual([]);
  };

  it("maker failure: comment with step and error, Todo, agent removed, agent-failed added, no push or PR", async () => {
    const fakes = createFakes({ makerResults: [{ ok: false, costUsd: 0.4, turns: 3, error: "error_max_turns" }] });
    const summary = await runOnce(fakes.deps, testConfig());

    expect(summary.processed[0]).toMatchObject({ identifier: "HOM-12", outcome: "failed" });
    expect(summary.processed[0].reason).toContain("maker");
    const failure = fakes.comments().at(-1) ?? "";
    expect(failure).toContain('step "maker"');
    expect(failure).toContain("error_max_turns");
    expect(failure).toContain("/wt/HOM-12");
    expectFailurePath(fakes);
    expect(fakes.named("git.push")).toEqual([]);
    expect(fakes.named("github.createDraftPr")).toEqual([]);
    expect(fakes.named("agent.runReviewer")).toEqual([]);
  });

  it("no changes after the maker is a failure", async () => {
    const fakes = createFakes({ changedFiles: [[]] });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].reason).toContain("no changes");
    expectFailurePath(fakes);
  });

  it("deny: maker over the cost cap fails with no further runner call, push or PR; allow: under the cap continues", async () => {
    const over = createFakes({ makerResults: [{ ok: true, costUsd: 5, turns: 40 }] });
    const overSummary = await runOnce(over.deps, testConfig());
    expect(overSummary.processed[0].outcome).toBe("failed");
    expect(overSummary.processed[0].reason).toContain("cost cap");
    expect(over.named("agent.runReviewer")).toEqual([]);
    expect(over.named("git.push")).toEqual([]);
    expect(over.named("github.createDraftPr")).toEqual([]);
    expectFailurePath(over);

    const under = createFakes({ makerResults: [{ ok: true, costUsd: 4.99, turns: 40 }], reviewResults: [{ approved: true, findings: "", costUsd: 0 }] });
    const underSummary = await runOnce(under.deps, testConfig());
    expect(underSummary.processed[0].outcome).toBe("in_review");
  });

  it("reports the cost cap when a maker run fails and also reaches the cap", async () => {
    const fakes = createFakes({ makerResults: [{ ok: false, costUsd: 6, turns: 2, error: "error_max_budget_usd" }] });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].reason).toContain("cost cap");
  });

  it("the reviewer cost counts toward the cap", async () => {
    const fakes = createFakes({
      makerResults: [{ ok: true, costUsd: 3, turns: 4 }],
      reviewResults: [{ approved: false, findings: "Fix it.", costUsd: 2 }],
    });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].reason).toContain("cost cap");
    expect(fakes.named("agent.runMaker")).toHaveLength(1);
  });

  it("a reviewer run that throws fails at step review with no fix round", async () => {
    const fakes = createFakes({ reviewResults: [new Error("error_during_execution")] });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].reason).toMatch(/^review: /);
    expect(fakes.named("agent.runMaker")).toHaveLength(1);
    expectFailurePath(fakes);
  });

  it("deny: a failing check means no push and no PR; the last 20 output lines are in the comment", async () => {
    const output = Array.from({ length: 30 }, (_, i) => `line ${i + 1}`).join("\n");
    const fakes = createFakes({
      changedFiles: [["website/src/page.tsx"]],
      checkResults: (specs) => specs.map((s) => ({ name: s.name, ok: s.name !== "website: lint", output })),
    });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].reason).toContain("website: lint");
    expect(fakes.named("git.commitAll")).toEqual([]);
    expect(fakes.named("git.push")).toEqual([]);
    expect(fakes.named("github.createDraftPr")).toEqual([]);
    const failure = fakes.comments().at(-1) ?? "";
    expect(failure).toContain('step "checks"');
    expect(failure).toContain("line 30");
    expect(failure).not.toContain("line 10\n");
    expectFailurePath(fakes);
  });

  it("a missing In Progress state runs the failure path", async () => {
    const fakes = createFakes({ states: [TODO, IN_REVIEW, DONE] });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].reason).toContain("In Progress");
    expect(fakes.named("git.createWorktree")).toEqual([]);
    expectFailurePath(fakes);
  });

  it("a throw in the pickup comment runs the failure path", async () => {
    let first = true;
    const fakes = createFakes();
    const addComment = fakes.deps.linear.addComment;
    fakes.deps.linear.addComment = async (issueId, body) => {
      if (first) {
        first = false;
        throw new Error("Linear is down");
      }
      return addComment(issueId, body);
    };
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0]).toMatchObject({ outcome: "failed", reason: "claim: Linear is down" });
    expect(fakes.named("linear.addLabel")[0].args).toEqual(["uuid-12", "agent-failed"]);
  });

  it("an error after the PR exists: PR stays tracked and the failure comment has the PR URL", async () => {
    const fakes = createFakes({ failures: { "linear.attachLink": new Error("attachment failed") } });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0]).toMatchObject({
      outcome: "failed",
      prUrl: "https://github.com/example/repo/pull/101",
    });
    expect(fakes.tracked().map((t) => t.prNumber)).toEqual([101]);
    expect(fakes.comments().at(-1)).toContain("https://github.com/example/repo/pull/101");
    expect(fakes.named("linear.addLabel")[0].args).toEqual(["uuid-12", "agent-failed"]);
  });

  it("one failing ticket does not stop the next one, and runOnce does not throw", async () => {
    const issues = [makeIssue({ number: 1, priority: 1 }), makeIssue({ number: 2, priority: 2 })];
    const fakes = createFakes({ issues, makerResults: [{ ok: false, costUsd: 0, turns: 1, error: "boom" }] });
    const summary = await runOnce(fakes.deps, testConfig({ maxConcurrent: 2 }));
    expect(summary.processed.map((p) => p.outcome)).toEqual(["failed", "in_review"]);
  });

  it("failure writes are tried one by one even when one of them throws", async () => {
    const fakes = createFakes({
      makerResults: [{ ok: false, costUsd: 0, turns: 1, error: "boom" }],
      failures: { "linear.removeLabel": new Error("no permission") },
    });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.processed[0].outcome).toBe("failed");
    expect(fakes.named("linear.addLabel")).toHaveLength(1);
    expect(fakes.logs.some((l) => l.step === "failure" && l.message.includes("no permission"))).toBe(true);
  });

  it("a listIssues error throws out of runOnce", async () => {
    const fakes = createFakes({ failures: { "linear.listIssues": new Error("HTTP 500") } });
    await expect(runOnce(fakes.deps, testConfig())).rejects.toThrow("HTTP 500");
  });
});

describe("runOnce: follow-ups for tracked PRs", () => {
  it("merged: Done, worktree removed, branch deleted, dropped from the store", async () => {
    const fakes = createFakes({ issues: [], tracked: [tracked()], prStates: { 77: "merged" } });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.followUps).toEqual([
      { identifier: "HOM-7", action: "done", prUrl: "https://github.com/example/repo/pull/77" },
    ]);
    expect(fakes.named("linear.setState")[0].args).toEqual(["uuid-7", DONE.id]);
    expect(fakes.named("git.removeWorktree")[0].args).toEqual(["/wt/HOM-7"]);
    expect(fakes.named("git.deleteBranch")[0].args).toEqual(["agent/hom-7-old-work"]);
    expect(fakes.tracked()).toEqual([]);
  });

  it("deny: closed without merge goes back to Todo without the agent label, not Done", async () => {
    const fakes = createFakes({ issues: [], tracked: [tracked()], prStates: { 77: "closed" } });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.followUps).toEqual([
      { identifier: "HOM-7", action: "reopened", prUrl: "https://github.com/example/repo/pull/77" },
    ]);
    expect(fakes.comments()[0]).toContain("closed without a merge");
    expect(fakes.named("linear.setState").map((c) => c.args)).toEqual([["uuid-7", TODO.id]]);
    expect(fakes.named("linear.removeLabel")[0].args).toEqual(["uuid-7", "agent"]);
    expect(fakes.named("git.removeWorktree")[0].args).toEqual(["/wt/HOM-7"]);
    expect(fakes.named("git.deleteBranch")[0].args).toEqual(["agent/hom-7-old-work"]);
    expect(fakes.tracked()).toEqual([]);
  });

  it("open: nothing happens and the entry stays", async () => {
    const fakes = createFakes({ issues: [], tracked: [tracked()], prStates: { 77: "open" } });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.followUps).toEqual([]);
    expect(fakes.linearWrites()).toEqual([]);
    expect(fakes.named("state.save")).toEqual([]);
  });

  it("a follow-up error is logged and the entry is kept for the next poll", async () => {
    const entries = [tracked(), tracked({ issueId: "uuid-8", identifier: "HOM-8", prNumber: 88 })];
    const fakes = createFakes({ issues: [], tracked: entries, prStates: { 77: new Error("gh failed"), 88: "merged" } });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.followUps.map((f) => f.identifier)).toEqual(["HOM-8"]);
    expect(fakes.tracked().map((t) => t.identifier)).toEqual(["HOM-7"]);
    expect(fakes.logs.some((l) => l.step === "followup" && l.ticket === "HOM-7" && l.message.includes("gh failed"))).toBe(true);
  });

  it("closed: a git cleanup error is logged, the entry is still dropped, and the next poll repeats no Linear write", async () => {
    const fakes = createFakes({
      issues: [],
      tracked: [tracked()],
      prStates: { 77: "closed" },
      failures: { "git.removeWorktree": new Error("worktree is busy") },
    });
    const first = await runOnce(fakes.deps, testConfig());
    expect(first.followUps).toEqual([
      { identifier: "HOM-7", action: "reopened", prUrl: "https://github.com/example/repo/pull/77" },
    ]);
    expect(fakes.named("git.deleteBranch")).toHaveLength(1);
    expect(fakes.tracked()).toEqual([]);
    expect(
      fakes.logs.some((l) => l.step === "followup" && l.message.includes("could not remove the worktree /wt/HOM-7") && l.message.includes("worktree is busy")),
    ).toBe(true);
    const writesAfterFirst = fakes.linearWrites().length;
    expect(writesAfterFirst).toBe(3);

    const second = await runOnce(fakes.deps, testConfig());
    expect(second.followUps).toEqual([]);
    expect(fakes.linearWrites()).toHaveLength(writesAfterFirst);
  });

  it("merged: a branch delete error is logged and the entry is still dropped", async () => {
    const fakes = createFakes({
      issues: [],
      tracked: [tracked()],
      prStates: { 77: "merged" },
      failures: { "git.deleteBranch": new Error("remote rejected") },
    });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.followUps.map((f) => f.action)).toEqual(["done"]);
    expect(fakes.tracked()).toEqual([]);
    expect(fakes.logs.some((l) => l.message.includes("could not delete the branch agent/hom-7-old-work"))).toBe(true);
  });

  it("a missing Done state keeps the entry", async () => {
    const fakes = createFakes({
      issues: [],
      tracked: [tracked()],
      prStates: { 77: "merged" },
      states: [TODO, IN_PROGRESS],
    });
    const summary = await runOnce(fakes.deps, testConfig());
    expect(summary.followUps).toEqual([]);
    expect(fakes.named("state.save")).toEqual([]);
    expect(fakes.tracked()).toHaveLength(1);
  });

  it("runs follow-ups before it lists new issues", async () => {
    const fakes = createFakes({ tracked: [tracked()], prStates: { 77: "merged" } });
    await runOnce(fakes.deps, testConfig());
    const order = fakes.calls.map((c) => `${c.port}.${c.method}`);
    expect(order.indexOf("github.getPr")).toBeLessThan(order.indexOf("linear.listIssues"));
  });
});

describe("runOnce: dry run", () => {
  it("deny: dry run makes zero writes but fills wouldPick; allow: a normal run writes", async () => {
    const issues = [makeIssue({ number: 1 }), makeIssue({ number: 2, labels: ["agent", "agent-failed"] })];
    const entries = [tracked(), tracked({ identifier: "HOM-8", issueId: "uuid-8", prNumber: 88 })];
    const dry = createFakes({ issues, tracked: entries, prStates: { 77: "merged", 88: "closed" } });
    const summary = await runOnce(dry.deps, testConfig({ dryRun: true }));

    expect(dry.writes()).toEqual([]);
    expect(summary).toEqual({ wouldPick: ["HOM-1"], processed: [], followUps: [] });
    expect(dry.named("github.getPr")).toHaveLength(2);
    const followupLines = dry.logs.filter((l) => l.step === "followup").map((l) => l.message);
    expect(followupLines.some((m) => m.includes("would move the ticket to Done"))).toBe(true);
    expect(followupLines.some((m) => m.includes("would comment"))).toBe(true);

    const normal = createFakes({ issues, tracked: entries, prStates: { 77: "merged", 88: "closed" } });
    await runOnce(normal.deps, testConfig());
    expect(normal.writes().length).toBeGreaterThan(0);
  });
});

describe("runOnce: logging", () => {
  it("every line logged for a ticket has the ticket id and the cost so far", async () => {
    const fakes = createFakes({
      makerResults: [{ ok: true, costUsd: 1.25, turns: 5 }],
      reviewResults: [{ approved: true, findings: "VERDICT: APPROVED", costUsd: 0.5 }],
    });
    await runOnce(fakes.deps, testConfig());
    const ticketLines = fakes.logs.filter((l) => l.step !== "poll");
    for (const line of ticketLines) {
      expect(line.ticket).toBe("HOM-12");
      expect(typeof line.costUsd).toBe("number");
    }
    const steps = ticketLines.map((l) => l.step);
    for (const step of ["claim", "worktree", "maker", "review", "checks", "push", "pr", "status"]) {
      expect(steps).toContain(step);
    }
    expect(ticketLines.find((l) => l.step === "claim")?.costUsd).toBe(0);
    expect(ticketLines.find((l) => l.step === "status")?.costUsd).toBe(1.75);
  });

  it("logs a failure line with the ticket id", async () => {
    const fakes = createFakes({ makerResults: [{ ok: false, costUsd: 0.2, turns: 1, error: "boom" }] });
    await runOnce(fakes.deps, testConfig());
    const failure = fakes.logs.find((l) => l.step === "failure");
    expect(failure).toMatchObject({ ticket: "HOM-12", costUsd: 0.2 });
  });
});

it("uses only the states it looked up by name", async () => {
  const renamed = ALL_STATES.map((s) => ({ ...s, id: `x-${s.id}` }));
  const fakes = createFakes({ states: renamed });
  await runOnce(fakes.deps, testConfig());
  expect(fakes.named("linear.setState").map((c) => c.args[1])).toEqual(["x-state-progress", "x-state-review"]);
});
