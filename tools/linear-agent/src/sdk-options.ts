import { realpathSync } from "node:fs";
import path from "node:path";
import type { HookCallback, Options } from "@anthropic-ai/claude-agent-sdk";
import { ALLOWED_DOMAINS, secretReadPaths } from "./policy";
import type { AgentConfig } from "./types";

export { ALLOWED_DOMAINS, secretReadPaths } from "./policy";

export const MAKER_TOOLS = ["Read", "Write", "Edit", "Glob", "Grep", "Bash", "TodoWrite"] as const;
export const REVIEWER_TOOLS = ["Read", "Glob", "Grep"] as const;

/** The worker pushes and opens the PR itself. The agent must never do it. */
const ALWAYS_DISALLOWED = ["WebFetch", "WebSearch", "Bash(git push)", "Bash(git push *)", "Bash(gh)", "Bash(gh *)"];
const REVIEWER_DISALLOWED = ["Write", "Edit", "NotebookEdit", "Bash"];

/** Removed from the agent environment. `env` in the SDK options replaces process.env, it is not merged. */
export const AGENT_SECRET_VARS = ["LINEAR_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "SSH_AUTH_SOCK"];

/**
 * Kept in the agent process (the SDK calls the API with it) but unset for its sandboxed Bash
 * commands through the sandbox `credentials.envVars` deny mode.
 */
const SANDBOX_DENIED_ENV = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"];

const FILE_TOOL_PATH_FIELDS: Record<string, string[]> = {
  Read: ["file_path"],
  Write: ["file_path"],
  Edit: ["file_path"],
  NotebookEdit: ["notebook_path"],
  Glob: ["path"],
  Grep: ["path"],
};
const WRITE_TOOLS = new Set(["Write", "Edit", "NotebookEdit"]);

/**
 * The environment for the agent process: the worker environment without the Linear and GitHub
 * keys and without the SSH agent, and with every git credential helper switched off. So a
 * `git push` from inside the agent has no credentials. ANTHROPIC_API_KEY stays, the SDK needs it.
 */
export function agentEnv(source: NodeJS.ProcessEnv = process.env): Record<string, string | undefined> {
  const env: Record<string, string | undefined> = { ...source };
  for (const name of AGENT_SECRET_VARS) delete env[name];
  return {
    ...env,
    GIT_TERMINAL_PROMPT: "0",
    GIT_CONFIG_COUNT: "1",
    GIT_CONFIG_KEY_0: "credential.helper",
    GIT_CONFIG_VALUE_0: "",
    CLAUDE_AGENT_SDK_CLIENT_APP: "linear-agent/0.1.0",
  };
}

/**
 * The real path of `target`. For a path that does not exist yet (a new file), the real path of
 * its nearest existing parent plus the missing part, so a symlinked parent is still resolved.
 */
export function realPathOfNearest(target: string): string {
  const missing: string[] = [];
  let current = path.resolve(target);
  for (;;) {
    try {
      return path.join(realpathSync(current), ...missing);
    } catch {
      const parent = path.dirname(current);
      if (parent === current) return path.resolve(target);
      missing.unshift(path.basename(current));
      current = parent;
    }
  }
}

function isInside(root: string, target: string): boolean {
  const relative = path.relative(root, target);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function deny(toolName: string, reason: string) {
  return {
    hookSpecificOutput: {
      hookEventName: "PreToolUse" as const,
      permissionDecision: "deny" as const,
      permissionDecisionReason: `linear-agent: ${toolName} ${reason}`,
    },
  };
}

/**
 * PreToolUse hook: file tools may only touch paths inside the worktree, compared by real path,
 * so a symlink inside the worktree cannot lead outside. Write tools may not touch the worktree
 * `.git` pointer either. The sandbox covers Bash, but these tools run in the agent process.
 */
export function fileToolGuard(cwd: string): HookCallback {
  return async (input) => {
    if (input.hook_event_name !== "PreToolUse") return {};
    const fields = FILE_TOOL_PATH_FIELDS[input.tool_name];
    if (!fields || typeof input.tool_input !== "object" || input.tool_input === null) return {};
    const root = realPathOfNearest(cwd);
    const gitPointer = path.join(root, ".git");
    const toolInput = input.tool_input as Record<string, unknown>;
    for (const field of fields) {
      const value = toolInput[field];
      if (typeof value !== "string" || value === "") continue;
      const target = realPathOfNearest(path.resolve(cwd, value));
      if (!isInside(root, target)) return deny(input.tool_name, `is only allowed inside the worktree ${root}.`);
      if (WRITE_TOOLS.has(input.tool_name) && isInside(gitPointer, target)) {
        return deny(input.tool_name, "may not change the worktree .git pointer.");
      }
    }
    return {};
  };
}

/** `Read` and `Edit` deny rules for the secret paths, in the `//absolute` form of permission rules. */
export function secretDenyRules(config: AgentConfig, home?: string): string[] {
  return secretReadPaths(config, home).flatMap((p) => [`Read(/${p})`, `Read(/${p}/**)`, `Edit(/${p})`, `Edit(/${p}/**)`]);
}

const MAKER_APPEND = [
  "You are linear-agent, working alone on one Linear ticket in a fresh git worktree.",
  "Nobody will answer questions. Make reasonable choices and say what you chose in your final message.",
  "Change only files inside the current working directory. Follow CLAUDE.md, AGENTS.md and the conventions of the code you touch.",
  "Add or update tests for the behavior you change, and run them when you can.",
  "Do not commit, push, open pull requests, or run gh. Leave your changes uncommitted; the worker commits, pushes and opens a draft PR.",
  "Never touch secrets or .env files.",
].join("\n");

const REVIEWER_APPEND = [
  "You are the reviewer of linear-agent. You can read files but you cannot change them.",
  "Review the diff against the ticket: correctness, missing tests, security, scope creep, repo conventions.",
  "End your answer with exactly one final line: VERDICT: APPROVED or VERDICT: CHANGES_REQUESTED.",
].join("\n");

/** The options object passed to the SDK `query()` call for one agent run. */
export function buildQueryOptions(
  config: AgentConfig,
  cwd: string,
  role: "maker" | "reviewer",
  maxBudgetUsd: number,
): Options {
  const maker = role === "maker";
  const tools = maker ? [...MAKER_TOOLS] : [...REVIEWER_TOOLS];
  const disallowed = [...ALWAYS_DISALLOWED, ...secretDenyRules(config)];
  return {
    systemPrompt: { type: "preset", preset: "claude_code", append: maker ? MAKER_APPEND : REVIEWER_APPEND },
    // 'project' loads CLAUDE.md. Settings, hooks and .claude come from the main checkout
    // (projectConfigRoot), not from the worktree the agent can edit.
    settingSources: ["project"],
    projectConfigRoot: config.repoPath,
    // The ticket asks for the repo skills (for example heroui-react). This option turns them on.
    skills: "all",
    model: config.model,
    cwd,
    maxTurns: config.maxTurns,
    maxBudgetUsd,
    permissionMode: "dontAsk",
    // `tools` is the base set that exists at all. Skill must be in it for the repo skills;
    // the `skills` option allows it, so it stays out of allowedTools (the SDK deprecates that).
    tools: [...tools, "Skill"],
    allowedTools: tools,
    disallowedTools: maker ? disallowed : [...disallowed, ...REVIEWER_DISALLOWED],
    mcpServers: {},
    strictMcpConfig: true,
    sandbox: {
      enabled: true,
      failIfUnavailable: true,
      allowUnsandboxedCommands: false,
      network: {
        allowedDomains: [...ALLOWED_DOMAINS],
        strictAllowlist: true,
      },
      filesystem: {
        denyRead: secretReadPaths(config),
        denyWrite: [path.join(cwd, ".git")],
      },
      credentials: {
        envVars: SANDBOX_DENIED_ENV.map((name) => ({ name, mode: "deny" as const })),
      },
    },
    hooks: {
      PreToolUse: [{ hooks: [fileToolGuard(cwd)] }],
    },
    env: agentEnv(),
  };
}
