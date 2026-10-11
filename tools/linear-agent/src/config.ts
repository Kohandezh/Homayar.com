import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import type { AgentConfig } from "./types";

/** Error with a message meant for the person running the worker. The CLI prints it without a stack trace. */
export class ConfigError extends Error {
  override name = "ConfigError";
}

/** tools/linear-agent, the folder of this package. */
export const PACKAGE_DIR = fileURLToPath(new URL("..", import.meta.url));

export function expandHome(p: string): string {
  if (p === "~") return os.homedir();
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  return p;
}

export function defaultConfig(overrides: Partial<AgentConfig> = {}): AgentConfig {
  const config: AgentConfig = {
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
    repoPath: path.resolve(PACKAGE_DIR, "..", ".."),
    worktreeRoot: "~/linear-agent-worktrees",
    baseRef: "origin/main",
    baseBranch: "main",
    dryRun: false,
    ...overrides,
  };
  config.worktreeRoot = expandHome(config.worktreeRoot);
  config.repoPath = expandHome(config.repoPath);
  return config;
}

type Env = Record<string, string | undefined>;

const STRING_VARS = {
  LINEAR_AGENT_TEAM_KEY: "teamKey",
  LINEAR_AGENT_TODO_STATE: "todoState",
  LINEAR_AGENT_IN_PROGRESS_STATE: "inProgressState",
  LINEAR_AGENT_IN_REVIEW_STATE: "inReviewState",
  LINEAR_AGENT_DONE_STATE: "doneState",
  LINEAR_AGENT_TRIGGER_LABEL: "triggerLabel",
  LINEAR_AGENT_FAILED_LABEL: "failedLabel",
  LINEAR_AGENT_MODEL: "model",
  LINEAR_AGENT_REPO_PATH: "repoPath",
  LINEAR_AGENT_WORKTREE_ROOT: "worktreeRoot",
  LINEAR_AGENT_BASE_REF: "baseRef",
  LINEAR_AGENT_BASE_BRANCH: "baseBranch",
} as const satisfies Record<string, keyof AgentConfig>;

const NUMBER_VARS = {
  LINEAR_AGENT_MAX_CONCURRENT: { key: "maxConcurrent", integer: true },
  LINEAR_AGENT_MAX_TURNS: { key: "maxTurns", integer: true },
  LINEAR_AGENT_MAX_BUDGET_USD: { key: "maxBudgetUsd", integer: false },
  LINEAR_AGENT_POLL_MINUTES: { key: "pollMinutes", integer: false },
} as const satisfies Record<string, { key: keyof AgentConfig; integer: boolean }>;

/** Builds the config from environment variables. Unset or empty variables keep the defaults. */
export function loadConfig(env: Env, options: { dryRun: boolean }): AgentConfig {
  const overrides: Partial<AgentConfig> = { dryRun: options.dryRun };
  for (const [name, key] of Object.entries(STRING_VARS)) {
    const value = env[name]?.trim();
    if (value) overrides[key] = value;
  }
  for (const [name, { key, integer }] of Object.entries(NUMBER_VARS)) {
    const raw = env[name]?.trim();
    if (!raw) continue;
    const value = Number(raw);
    if (!Number.isFinite(value) || value <= 0 || (integer && !Number.isInteger(value))) {
      throw new ConfigError(`${name} must be a positive ${integer ? "whole number" : "number"}, got "${raw}".`);
    }
    overrides[key] = value;
  }
  return defaultConfig(overrides);
}

export interface Keys {
  linearApiKey: string;
  anthropicApiKey: string;
  githubToken: string;
}

/**
 * Checks that the keys for this mode are set. A dry run only reads Linear, so it needs
 * LINEAR_API_KEY alone. A normal run also needs ANTHROPIC_API_KEY and GITHUB_TOKEN.
 */
export function requireKeys(env: Env, dryRun: boolean): Keys {
  const needed = dryRun ? ["LINEAR_API_KEY"] : ["LINEAR_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN"];
  const missing = needed.filter((name) => !env[name]?.trim());
  if (missing.length > 0) {
    const list = missing.join(", ");
    const verb = missing.length === 1 ? "is" : "are";
    throw new ConfigError(
      `${list} ${verb} missing. Put ${missing.length === 1 ? "it" : "them"} in tools/linear-agent/.env (see .env.example) or export ${missing.length === 1 ? "it" : "them"} before you start the worker.`,
    );
  }
  return {
    linearApiKey: env.LINEAR_API_KEY?.trim() ?? "",
    anthropicApiKey: env.ANTHROPIC_API_KEY?.trim() ?? "",
    githubToken: env.GITHUB_TOKEN?.trim() ?? "",
  };
}

/** Folder for the tracked PR list and the ticket locks. Kept outside the repository by default. */
export function stateDir(env: Env, config: AgentConfig): string {
  const raw = env.LINEAR_AGENT_STATE_DIR?.trim();
  return raw ? path.resolve(expandHome(raw)) : path.join(config.worktreeRoot, ".linear-agent");
}
