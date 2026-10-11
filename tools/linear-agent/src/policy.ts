import os from "node:os";
import path from "node:path";
import { PACKAGE_DIR } from "./config";
import type { AgentConfig } from "./types";

/**
 * Hosts that sandboxed commands (agent Bash and the checks) may reach: npm, PyPI, GitHub and
 * Linear. `api.github.com` is left out on purpose: nothing in the sandbox needs the GitHub API.
 */
export const ALLOWED_DOMAINS = [
  "registry.npmjs.org",
  "pypi.org",
  "files.pythonhosted.org",
  "github.com",
  "*.githubusercontent.com",
  "api.linear.app",
] as const;

/** The three worker keys plus other Anthropic credentials. No sandboxed command gets them. */
export const SECRET_ENV_VARS = [
  "LINEAR_API_KEY",
  "GITHUB_TOKEN",
  "GH_TOKEN",
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_AUTH_TOKEN",
  "CLAUDE_CODE_OAUTH_TOKEN",
] as const;

const HOME_SECRET_PATHS = [".ssh", ".aws", ".config/gh", ".git-credentials", ".netrc", ".docker", ".config/gcloud", ".kube"];

/**
 * Paths no sandboxed command may read: the worker's own .env (it holds all three keys) and the
 * usual credential stores in the home folder. GitHub and Linear are reachable, so a readable
 * key could leave the machine.
 */
export function secretReadPaths(config: AgentConfig, home = os.homedir()): string[] {
  const paths = [
    path.join(config.repoPath, "tools", "linear-agent", ".env"),
    path.join(PACKAGE_DIR, ".env"),
    ...HOME_SECRET_PATHS.map((p) => path.join(home, p)),
  ];
  return [...new Set(paths.map((p) => path.resolve(p)))];
}

// Package manager caches the checks may write to, relative to the home folder.
const CACHE_PATHS = [
  ".npm",
  ".cache/uv",
  ".cache/pip",
  ".cache/pnpm",
  ".cache/node/corepack",
  ".local/share/uv",
  ".local/share/pnpm",
  "Library/Caches/pip",
  "Library/Caches/pnpm",
  "Library/pnpm",
];

/** Where check commands may write besides their worktree: temp folders and package caches. */
export function checkWriteRoots(home = os.homedir(), tmp = os.tmpdir()): string[] {
  return [...new Set([path.resolve(tmp), "/tmp", ...CACHE_PATHS.map((p) => path.join(home, p))])];
}
