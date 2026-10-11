import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { ConfigError, defaultConfig, loadConfig, requireKeys, stateDir } from "../src/index";

describe("defaultConfig", () => {
  it("has the SPEC defaults", () => {
    const config = defaultConfig();
    expect(config).toMatchObject({
      teamKey: "HOM",
      todoState: "Todo",
      inProgressState: "In Progress",
      inReviewState: "In Review",
      doneState: "Done",
      triggerLabel: "agent",
      failedLabel: "agent-failed",
      maxConcurrent: 1,
      model: "claude-opus-5",
      maxTurns: 80,
      maxBudgetUsd: 5,
      pollMinutes: 10,
      baseRef: "origin/main",
      baseBranch: "main",
      dryRun: false,
    });
    expect(config.worktreeRoot).toBe(path.join(os.homedir(), "linear-agent-worktrees"));
    expect(path.isAbsolute(config.repoPath)).toBe(true);
  });

  it("applies overrides and expands ~", () => {
    const config = defaultConfig({ maxBudgetUsd: 2, worktreeRoot: "~/wt", dryRun: true });
    expect(config.maxBudgetUsd).toBe(2);
    expect(config.dryRun).toBe(true);
    expect(config.worktreeRoot).toBe(path.join(os.homedir(), "wt"));
  });
});

describe("loadConfig", () => {
  it("reads the optional variables", () => {
    const config = loadConfig(
      {
        LINEAR_AGENT_MODEL: "claude-opus-5-5",
        LINEAR_AGENT_MAX_BUDGET_USD: "2.5",
        LINEAR_AGENT_MAX_CONCURRENT: "2",
        LINEAR_AGENT_IN_REVIEW_STATE: "Review",
        LINEAR_AGENT_TEAM_KEY: "",
      },
      { dryRun: false },
    );
    expect(config).toMatchObject({ model: "claude-opus-5-5", maxBudgetUsd: 2.5, maxConcurrent: 2, inReviewState: "Review", teamKey: "HOM" });
  });

  it("rejects bad numbers with a clear message", () => {
    expect(() => loadConfig({ LINEAR_AGENT_MAX_TURNS: "lots" }, { dryRun: false })).toThrow(ConfigError);
    expect(() => loadConfig({ LINEAR_AGENT_MAX_CONCURRENT: "1.5" }, { dryRun: false })).toThrow("whole number");
    expect(() => loadConfig({ LINEAR_AGENT_MAX_BUDGET_USD: "-1" }, { dryRun: false })).toThrow("positive");
  });
});

describe("requireKeys", () => {
  it("dry run needs only LINEAR_API_KEY", () => {
    expect(() => requireKeys({}, true)).toThrow("LINEAR_API_KEY is missing");
    expect(requireKeys({ LINEAR_API_KEY: "lin_test_key" }, true).linearApiKey).toBe("lin_test_key");
  });

  it("a normal run needs all three keys", () => {
    expect(() => requireKeys({ LINEAR_API_KEY: "lin_test_key" }, false)).toThrow(
      "ANTHROPIC_API_KEY, GITHUB_TOKEN are missing",
    );
    const keys = requireKeys({ LINEAR_API_KEY: "a", ANTHROPIC_API_KEY: "b", GITHUB_TOKEN: "c" }, false);
    expect(keys).toEqual({ linearApiKey: "a", anthropicApiKey: "b", githubToken: "c" });
  });

  it("treats empty values as missing", () => {
    expect(() => requireKeys({ LINEAR_API_KEY: "  " }, true)).toThrow("LINEAR_API_KEY is missing");
  });
});

describe("stateDir", () => {
  it("defaults to a folder under the worktree root", () => {
    expect(stateDir({}, defaultConfig({ worktreeRoot: "/wt" }))).toBe(path.join("/wt", ".linear-agent"));
    expect(stateDir({ LINEAR_AGENT_STATE_DIR: "/state" }, defaultConfig())).toBe("/state");
  });
});
