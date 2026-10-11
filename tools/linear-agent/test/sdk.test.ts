import { mkdirSync, mkdtempSync, realpathSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import type { HookInput, Options, SDKMessage } from "@anthropic-ai/claude-agent-sdk";
import { afterAll, describe, expect, it } from "vitest";
import {
  agentEnv,
  ALLOWED_DOMAINS,
  AgentRunError,
  buildQueryOptions,
  createSdkAgentRunner,
  fileToolGuard,
  parseVerdict,
  realPathOfNearest,
  secretDenyRules,
  secretReadPaths,
} from "../src/index";
import type { QueryFn } from "../src/agent-runner";
import { testConfig } from "./fakes";

describe("buildQueryOptions", () => {
  const config = testConfig({ model: "claude-opus-5", maxTurns: 80, repoPath: "/repo" });

  it.each(["maker", "reviewer"] as const)("%s: preset, project settings, model, caps, dontAsk, sandbox", (role) => {
    const options = buildQueryOptions(config, "/wt/HOM-1", role, 3.5);
    expect(options.systemPrompt).toMatchObject({ type: "preset", preset: "claude_code" });
    expect(options.settingSources).toContain("project");
    expect(options.model).toBe("claude-opus-5");
    expect(options.cwd).toBe("/wt/HOM-1");
    expect(options.maxTurns).toBe(80);
    expect(options.maxBudgetUsd).toBe(3.5);
    expect(options.permissionMode).toBe("dontAsk");
    expect(options.allowedTools).not.toContain("WebFetch");
    expect(options.allowedTools).not.toContain("WebSearch");
    expect(options.disallowedTools).toEqual(expect.arrayContaining(["WebFetch", "WebSearch", "Bash(git push *)", "Bash(gh *)"]));
    expect(options.sandbox?.enabled).toBe(true);
    expect(options.sandbox?.allowUnsandboxedCommands).toBe(false);
    expect(options.sandbox?.network?.allowedDomains).toEqual([...ALLOWED_DOMAINS]);
    expect(options.sandbox?.network?.allowedDomains).not.toContain("*");
    expect(options.strictMcpConfig).toBe(true);
    expect(options.mcpServers).toEqual({});
    expect(options.projectConfigRoot).toBe("/repo");
    expect(options.skills).toBe("all");
  });

  it("maker gets file tools and Bash; reviewer is read-only", () => {
    const maker = buildQueryOptions(config, "/wt/HOM-1", "maker", 5);
    expect(maker.allowedTools).toEqual(expect.arrayContaining(["Read", "Write", "Edit", "Bash"]));
    const reviewer = buildQueryOptions(config, "/wt/HOM-1", "reviewer", 5);
    for (const tool of ["Write", "Edit", "Bash", "NotebookEdit"]) expect(reviewer.allowedTools).not.toContain(tool);
    expect(reviewer.tools).toEqual(["Read", "Glob", "Grep", "Skill"]);
    expect(reviewer.allowedTools).toEqual(["Read", "Glob", "Grep"]);
    expect(reviewer.disallowedTools).toEqual(expect.arrayContaining(["Write", "Edit"]));
  });

  it("the agent env drops the Linear and GitHub keys and keeps the Anthropic key", () => {
    const env = agentEnv({
      PATH: "/bin",
      HOME: "/home/x",
      ANTHROPIC_API_KEY: "sk-test-not-real",
      LINEAR_API_KEY: "lin_test_key",
      GITHUB_TOKEN: "gh_test_token",
      GH_TOKEN: "gh_test_token",
      SSH_AUTH_SOCK: "/tmp/agent.sock",
    });
    expect(env.LINEAR_API_KEY).toBeUndefined();
    expect(env.GITHUB_TOKEN).toBeUndefined();
    expect(env.GH_TOKEN).toBeUndefined();
    expect(env.SSH_AUTH_SOCK).toBeUndefined();
    expect(env.ANTHROPIC_API_KEY).toBe("sk-test-not-real");
    expect(env.PATH).toBe("/bin");
    expect(env).toMatchObject({ GIT_CONFIG_KEY_0: "credential.helper", GIT_CONFIG_VALUE_0: "", GIT_TERMINAL_PROMPT: "0" });
  });

  it("the sandbox may not read the worker .env or the usual credential stores", () => {
    const options = buildQueryOptions(config, "/wt/HOM-1", "maker", 5);
    const denied = options.sandbox?.filesystem?.denyRead ?? [];
    expect(denied).toEqual(secretReadPaths(config));
    expect(secretReadPaths(config, "/home/x")).toEqual(
      expect.arrayContaining(["/repo/tools/linear-agent/.env", "/home/x/.ssh", "/home/x/.config/gh", "/home/x/.git-credentials"]),
    );
  });

  it("passes a PreToolUse hook that keeps file tools in the worktree", () => {
    const options = buildQueryOptions(config, "/wt/HOM-1", "maker", 5);
    expect(options.hooks?.PreToolUse?.[0].hooks).toHaveLength(1);
  });

  it("does not allow api.github.com", () => {
    expect(ALLOWED_DOMAINS).not.toContain("api.github.com");
    expect(buildQueryOptions(config, "/wt/HOM-1", "maker", 5).sandbox?.network?.allowedDomains).not.toContain("api.github.com");
  });

  it.each(["maker", "reviewer"] as const)("%s: Read and Edit deny rules for every secret path", (role) => {
    const options = buildQueryOptions(config, "/wt/HOM-1", role, 5);
    expect(options.disallowedTools).toEqual(expect.arrayContaining(secretDenyRules(config)));
    expect(secretDenyRules(config, "/home/x")).toEqual(
      expect.arrayContaining([
        "Read(//repo/tools/linear-agent/.env)",
        "Edit(//repo/tools/linear-agent/.env)",
        "Read(//home/x/.ssh)",
        "Read(//home/x/.ssh/**)",
        "Edit(//home/x/.config/gh/**)",
      ]),
    );
  });

  it("unsets the Anthropic credentials for sandboxed Bash and protects the .git pointer", () => {
    const options = buildQueryOptions(config, "/wt/HOM-1", "maker", 5);
    expect(options.sandbox?.credentials?.envVars).toEqual([
      { name: "ANTHROPIC_API_KEY", mode: "deny" },
      { name: "ANTHROPIC_AUTH_TOKEN", mode: "deny" },
      { name: "CLAUDE_CODE_OAUTH_TOKEN", mode: "deny" },
    ]);
    expect(options.sandbox?.filesystem?.denyWrite).toEqual(["/wt/HOM-1/.git"]);
    // The agent process itself still has the key; only its sandboxed commands lose it.
    expect(agentEnv({ ANTHROPIC_API_KEY: "sk-test-not-real" }).ANTHROPIC_API_KEY).toBe("sk-test-not-real");
  });
});

describe("realPathOfNearest", () => {
  const base = realpathSync(mkdtempSync(path.join(os.tmpdir(), "linear-agent-real-")));
  afterAll(() => rmSync(base, { recursive: true, force: true }));

  it("resolves symlinks, also for a path whose last parts do not exist yet", () => {
    mkdirSync(path.join(base, "real"));
    symlinkSync(path.join(base, "real"), path.join(base, "link"));
    expect(realPathOfNearest(path.join(base, "link"))).toBe(path.join(base, "real"));
    expect(realPathOfNearest(path.join(base, "link", "new", "file.ts"))).toBe(path.join(base, "real", "new", "file.ts"));
    expect(realPathOfNearest("/does/not/exist/x")).toBe("/does/not/exist/x");
  });
});

describe("fileToolGuard with real symlinks", () => {
  const signal = new AbortController().signal;
  const base = realpathSync(mkdtempSync(path.join(os.tmpdir(), "linear-agent-guard-")));
  const worktree = path.join(base, "HOM-1");
  const outside = path.join(base, "outside");
  mkdirSync(path.join(worktree, "src"), { recursive: true });
  mkdirSync(outside);
  writeFileSync(path.join(outside, "secret.txt"), "x");
  writeFileSync(path.join(worktree, ".git"), "gitdir: /repo/.git/worktrees/HOM-1\n");
  symlinkSync(outside, path.join(worktree, "escape"));
  symlinkSync(path.join(worktree, "src"), path.join(worktree, "src-link"));
  symlinkSync(worktree, path.join(base, "worktree-link"));
  afterAll(() => rmSync(base, { recursive: true, force: true }));

  const call = (cwd: string, tool: string, input: Record<string, unknown>) =>
    fileToolGuard(cwd)(
      {
        hook_event_name: "PreToolUse",
        tool_name: tool,
        tool_input: input,
        tool_use_id: "t1",
        session_id: "s1",
        transcript_path: "/tmp/t.jsonl",
        cwd,
      } as HookInput,
      "t1",
      { signal },
    );
  const denied = { hookSpecificOutput: { permissionDecision: "deny" } };

  it("denies a path that leaves the worktree through a symlink", async () => {
    expect(await call(worktree, "Read", { file_path: path.join(worktree, "escape", "secret.txt") })).toMatchObject(denied);
    expect(await call(worktree, "Write", { file_path: "escape/new-file.txt" })).toMatchObject(denied);
    expect(await call(worktree, "Glob", { path: "escape" })).toMatchObject(denied);
  });

  it("allows symlinks that stay inside, new files, and a worktree reached through a symlink", async () => {
    expect(await call(worktree, "Read", { file_path: "src-link/a.ts" })).toEqual({});
    expect(await call(worktree, "Write", { file_path: "src/new/deep/file.ts" })).toEqual({});
    expect(await call(path.join(base, "worktree-link"), "Edit", { file_path: path.join(worktree, "src", "a.ts") })).toEqual({});
  });

  it("denies write tools on the .git pointer but allows reading it", async () => {
    expect(await call(worktree, "Write", { file_path: ".git" })).toMatchObject(denied);
    expect(await call(worktree, "Edit", { file_path: path.join(worktree, ".git") })).toMatchObject(denied);
    expect(await call(worktree, "Read", { file_path: ".git" })).toEqual({});
  });
});

describe("fileToolGuard", () => {
  const signal = new AbortController().signal;
  const call = (tool: string, input: Record<string, unknown>) =>
    fileToolGuard("/wt/HOM-1")(
      {
        hook_event_name: "PreToolUse",
        tool_name: tool,
        tool_input: input,
        tool_use_id: "t1",
        session_id: "s1",
        transcript_path: "/tmp/t.jsonl",
        cwd: "/wt/HOM-1",
      } as HookInput,
      "t1",
      { signal },
    );

  it("allows paths inside the worktree", async () => {
    expect(await call("Edit", { file_path: "/wt/HOM-1/src/a.ts" })).toEqual({});
    expect(await call("Write", { file_path: "src/b.ts" })).toEqual({});
    expect(await call("Grep", { pattern: "x" })).toEqual({});
    expect(await call("Bash", { command: "ls /" })).toEqual({});
  });

  it("denies paths outside the worktree", async () => {
    for (const [tool, input] of [
      ["Edit", { file_path: "/etc/hosts" }],
      ["Write", { file_path: "../HOM-2/a.ts" }],
      ["Read", { file_path: "/wt/HOM-10/secret" }],
      ["Glob", { path: "/Users" }],
    ] as const) {
      const result = await call(tool, input);
      expect(result).toMatchObject({ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny" } });
    }
  });
});

describe("parseVerdict", () => {
  it.each([
    ["All good.\nVERDICT: APPROVED", true],
    ["All good.\nVERDICT: APPROVED\n\n", true],
    ["VERDICT: CHANGES_REQUESTED", false],
    ["VERDICT: APPROVED\nbut one more thing", false],
    ["verdict: approved", false],
    ["**VERDICT: APPROVED**", false],
    ["", false],
  ])("%j is approved: %s", (text, approved) => {
    expect(parseVerdict(text)).toBe(approved);
  });
});

function result(fields: Record<string, unknown>): SDKMessage {
  return {
    type: "result",
    subtype: "success",
    is_error: false,
    num_turns: 3,
    total_cost_usd: 0.42,
    result: "done",
    errors: [],
    ...fields,
  } as unknown as SDKMessage;
}

function fakeQuery(messages: SDKMessage[]) {
  const seen: { prompt: unknown; options?: Options }[] = [];
  const query = ((params: { prompt: unknown; options?: Options }) => {
    seen.push(params);
    return (async function* () {
      yield* messages;
    })();
  }) as unknown as QueryFn;
  return { query, seen };
}

describe("createSdkAgentRunner", () => {
  const config = testConfig();

  it("maker: reads the last result message and passes the built options", async () => {
    const { query, seen } = fakeQuery([
      result({ total_cost_usd: 0.1, num_turns: 1 }),
      result({ total_cost_usd: 0.42, num_turns: 3 }),
    ]);
    const out = await createSdkAgentRunner(config, query).runMaker({ cwd: "/wt/HOM-1", prompt: "do it", maxBudgetUsd: 4 });
    expect(out).toEqual({ ok: true, costUsd: 0.42, turns: 3 });
    expect(seen[0].prompt).toBe("do it");
    expect(seen[0].options?.maxBudgetUsd).toBe(4);
    expect(seen[0].options?.cwd).toBe("/wt/HOM-1");
  });

  it("maker: a non-success subtype or is_error is not ok", async () => {
    const runner = (msg: SDKMessage) => createSdkAgentRunner(config, fakeQuery([msg]).query);
    const budget = await runner(result({ subtype: "error_max_budget_usd", total_cost_usd: 5, errors: [] })).runMaker({
      cwd: "/wt",
      prompt: "x",
      maxBudgetUsd: 5,
    });
    expect(budget).toMatchObject({ ok: false, costUsd: 5, error: "error_max_budget_usd" });
    const apiError = await runner(result({ is_error: true, result: "API error" })).runMaker({ cwd: "/wt", prompt: "x", maxBudgetUsd: 5 });
    expect(apiError).toMatchObject({ ok: false, error: "API error" });
    const none = await createSdkAgentRunner(config, fakeQuery([]).query).runMaker({ cwd: "/wt", prompt: "x", maxBudgetUsd: 5 });
    expect(none.ok).toBe(false);
  });

  it("reviewer: sends the diff, parses the verdict, and throws with the cost on a failed run", async () => {
    const approved = fakeQuery([result({ result: "Fine.\nVERDICT: APPROVED", total_cost_usd: 0.3 })]);
    const review = await createSdkAgentRunner(config, approved.query).runReviewer({
      cwd: "/wt/HOM-1",
      prompt: "review it",
      diff: "diff --git a/x b/x",
      maxBudgetUsd: 2,
    });
    expect(review).toEqual({ approved: true, findings: "Fine.\nVERDICT: APPROVED", costUsd: 0.3 });
    expect(String(approved.seen[0].prompt)).toContain("diff --git a/x b/x");
    expect(approved.seen[0].options?.allowedTools).not.toContain("Write");

    const failed = fakeQuery([result({ subtype: "error_max_turns", total_cost_usd: 1.2, errors: ["too many turns"] })]);
    const error = await createSdkAgentRunner(config, failed.query)
      .runReviewer({ cwd: "/wt", prompt: "r", diff: "d", maxBudgetUsd: 2 })
      .catch((e: unknown) => e);
    expect(error).toBeInstanceOf(AgentRunError);
    expect(error).toMatchObject({ costUsd: 1.2 });
  });
});
