import { existsSync, lstatSync, readFileSync, realpathSync } from "node:fs";
import { promises as fs } from "node:fs";
import path from "node:path";
import { envWithout, execCommand } from "../exec";
import type { GitPort } from "../types";

const AGENT_BRANCH = /^agent\/[a-z0-9][a-z0-9-]*$/;
const PROTECTED_BRANCHES = new Set(["main", "master"]);
const TICKET_DIR = /^[A-Za-z0-9._-]+$/;

/**
 * Put in front of every worker git call. A worktree is written by the agent, so nothing it can
 * influence may run code here: no hooks, no fsmonitor, no pager. Diff calls also pass
 * --no-ext-diff and --no-textconv, and commit and push pass --no-verify.
 */
export const SAFE_GIT_CONFIG = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.pager=cat"];

/** Never given to git: the Anthropic and Linear keys, and variables that point git at another repo or config. */
const ALWAYS_STRIPPED = [
  "LINEAR_API_KEY",
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_AUTH_TOKEN",
  "CLAUDE_CODE_OAUTH_TOKEN",
  "GIT_DIR",
  "GIT_WORK_TREE",
  "GIT_INDEX_FILE",
  "GIT_COMMON_DIR",
  "GIT_OBJECT_DIRECTORY",
  "GIT_ALTERNATE_OBJECT_DIRECTORIES",
  "GIT_CONFIG_PARAMETERS",
  "GIT_CONFIG_COUNT",
];
/** Only network calls (fetch, ls-remote, push) keep these, for the GitHub credential helper. */
const GITHUB_AUTH_VARS = ["GITHUB_TOKEN", "GH_TOKEN"];

/** The env of one worker git call. */
export function workerGitEnv(network: boolean, source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env = envWithout(network ? ALWAYS_STRIPPED : [...ALWAYS_STRIPPED, ...GITHUB_AUTH_VARS], source);
  env.GIT_TERMINAL_PROMPT = "0";
  return env;
}

export class GitError extends Error {
  override name = "GitError";
}

function realPathOrSelf(p: string): string {
  try {
    return realpathSync(p);
  } catch {
    return path.resolve(p);
  }
}

export interface GitCliOptions {
  repoPath: string;
  worktreeRoot: string;
  baseRef: string;
  remote?: string;
  /**
   * True when a tracked PR still uses this branch. On a retry, the old remote branch is only
   * deleted when this says false (SPEC 15.5). Without it, no PR is assumed.
   */
  isBranchTracked?: (branch: string) => Promise<boolean>;
}

/**
 * GitPort over the `git` CLI. Every call uses an argument array, never a shell string.
 * Pushes never use a force flag. The force cleanup (`git worktree remove --force`,
 * `git branch -D`) only runs on a direct child folder of `worktreeRoot` that is a registered
 * worktree, and on `agent/...` branches.
 */
export function createGitCli(options: GitCliOptions): GitPort {
  const remote = options.remote ?? "origin";
  const root = path.resolve(options.worktreeRoot);
  let commonDir: string | undefined;

  async function run(args: string[], cwd: string, network: boolean) {
    return execCommand("git", [...SAFE_GIT_CONFIG, ...args], { cwd, env: workerGitEnv(network) });
  }

  async function git(args: string[], cwd: string, network = false): Promise<string> {
    const result = await run(args, cwd, network);
    if (result.code !== 0) {
      throw new GitError(`git ${args.join(" ")} failed (exit ${result.code}): ${result.stderr.trim() || result.stdout.trim()}`);
    }
    return result.stdout;
  }

  async function gitOk(args: string[], cwd: string, network = false): Promise<boolean> {
    return (await run(args, cwd, network)).code === 0;
  }

  /** The ticket's own worktree path: a direct child of worktreeRoot with a plain name. */
  function ownPath(p: string): string {
    const resolved = path.resolve(p);
    if (path.dirname(resolved) !== root || !TICKET_DIR.test(path.basename(resolved))) {
      throw new GitError(`Refusing to touch ${resolved}: it is not a ticket folder directly under ${root}.`);
    }
    return resolved;
  }

  function agentBranch(branch: string): string {
    if (!AGENT_BRANCH.test(branch) || PROTECTED_BRANCHES.has(branch)) {
      throw new GitError(`Refusing to use branch "${branch}": linear-agent only works on agent/... branches.`);
    }
    return branch;
  }

  async function gitCommonDir(): Promise<string> {
    if (!commonDir) {
      const out = (await git(["rev-parse", "--git-common-dir"], options.repoPath)).trim();
      commonDir = realPathOrSelf(path.resolve(options.repoPath, out));
    }
    return commonDir;
  }

  /**
   * SPEC 15.2: before git runs in a worktree, its `.git` must still be a plain file that points
   * into `<main repo .git>/worktrees/<name>`, and that entry must point back to this worktree.
   * The agent can write the worktree, so it could have pointed `.git` at a repo of its own.
   */
  async function verifyPointer(worktreePath: string): Promise<void> {
    const pointer = path.join(worktreePath, ".git");
    const refuse = (why: string): never => {
      throw new GitError(`Refusing to run git in ${worktreePath}: ${why}`);
    };
    let isFile = false;
    try {
      isFile = lstatSync(pointer).isFile();
    } catch {
      refuse("it has no .git file.");
    }
    if (!isFile) refuse(".git is not a plain file; it was replaced.");
    const match = /^gitdir: (.+)$/.exec(readFileSync(pointer, "utf8").trim());
    if (!match) return refuse(".git does not hold a gitdir line.");
    const gitdir = realPathOrSelf(path.resolve(worktreePath, match[1]));
    const worktreesDir = path.join(await gitCommonDir(), "worktrees");
    if (path.dirname(gitdir) !== worktreesDir) refuse(`.git points to ${gitdir}, not into ${worktreesDir}.`);
    let back = "";
    try {
      back = readFileSync(path.join(gitdir, "gitdir"), "utf8").trim();
    } catch {
      refuse(`${gitdir} has no gitdir file.`);
    }
    if (realPathOrSelf(back) !== realPathOrSelf(pointer)) refuse(`${gitdir} belongs to another worktree.`);
  }

  async function registeredWorktrees(): Promise<Set<string>> {
    const out = await git(["worktree", "list", "--porcelain"], options.repoPath);
    const paths = new Set<string>();
    for (const line of out.split("\n")) {
      if (line.startsWith("worktree ")) paths.add(realPathOrSelf(line.slice("worktree ".length)));
    }
    return paths;
  }

  async function isRegistered(p: string): Promise<boolean> {
    return (await registeredWorktrees()).has(realPathOrSelf(p));
  }

  async function localBranchExists(branch: string): Promise<boolean> {
    return gitOk(["show-ref", "--verify", "--quiet", `refs/heads/${branch}`], options.repoPath);
  }

  async function remoteBranchExists(branch: string): Promise<boolean> {
    return gitOk(["ls-remote", "--exit-code", "--heads", remote, branch], options.repoPath, true);
  }

  /** SPEC 15.6: a folder that is not a registered worktree is never deleted. */
  async function removeOwnWorktree(p: string): Promise<void> {
    if (await isRegistered(p)) {
      if (existsSync(p)) {
        await verifyPointer(p);
        const removed = await gitOk(["worktree", "remove", p], options.repoPath);
        if (!removed) await git(["worktree", "remove", "--force", p], options.repoPath);
        return;
      }
      // Registered but the folder is gone: prune only clears metadata of missing worktrees.
      await git(["worktree", "prune"], options.repoPath);
      return;
    }
    if (existsSync(p)) {
      throw new GitError(
        `${p} exists but is not a git worktree of ${options.repoPath}. linear-agent never deletes it. ` +
          "Move or delete it by hand, then retry the ticket.",
      );
    }
  }

  async function mergeBase(worktreePath: string): Promise<string> {
    return (await git(["merge-base", options.baseRef, "HEAD"], worktreePath)).trim();
  }

  return {
    async createWorktree({ path: wtPath, branch, baseRef }) {
      const target = ownPath(wtPath);
      agentBranch(branch);
      await git(["fetch", remote], options.repoPath, true);
      await fs.mkdir(root, { recursive: true });
      // A retry finds the worktree and branches of the failed or closed attempt (SPEC 14.6 b, 15.5).
      await removeOwnWorktree(target);
      if (await localBranchExists(branch)) await git(["branch", "-D", branch], options.repoPath);
      if ((await remoteBranchExists(branch)) && !(await options.isBranchTracked?.(branch))) {
        // `--delete` removes the remote branch; it is not a force push.
        await git(["push", "--no-verify", remote, "--delete", branch], options.repoPath, true);
      }
      // --no-track: the new branch must not track origin/main, so a plain `git push` cannot target main.
      await git(["worktree", "add", "--no-track", "-b", branch, target, baseRef], options.repoPath);
    },

    async removeWorktree(wtPath) {
      await removeOwnWorktree(ownPath(wtPath));
    },

    async deleteBranch(branch) {
      agentBranch(branch);
      if (await localBranchExists(branch)) {
        const deleted = await gitOk(["branch", "-d", branch], options.repoPath);
        if (!deleted) await git(["branch", "-D", branch], options.repoPath);
      }
      if (await remoteBranchExists(branch)) {
        await git(["push", "--no-verify", remote, "--delete", branch], options.repoPath, true);
      }
    },

    async changedFiles(worktreePath) {
      await verifyPointer(worktreePath);
      const base = await mergeBase(worktreePath);
      const tracked = await git(["diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", base], worktreePath);
      const untracked = await git(["ls-files", "--others", "--exclude-standard", "-z"], worktreePath);
      const files: string[] = [];
      for (const file of [...tracked.split("\0"), ...untracked.split("\0")]) {
        if (file && !files.includes(file)) files.push(file);
      }
      return files;
    },

    async diff(worktreePath) {
      await verifyPointer(worktreePath);
      const base = await mergeBase(worktreePath);
      // Intent-to-add makes new files show up in the diff; commitAll stages them for real later.
      await git(["add", "--all", "--intent-to-add"], worktreePath);
      return git(["diff", "--no-ext-diff", "--no-textconv", base], worktreePath);
    },

    async commitAll(worktreePath, message) {
      await verifyPointer(worktreePath);
      await git(["add", "--all"], worktreePath);
      const nothingStaged = await gitOk(["diff", "--cached", "--quiet", "--no-ext-diff"], worktreePath);
      if (nothingStaged) return;
      await git(["commit", "--no-verify", "--message", message], worktreePath);
    },

    async push(worktreePath, branch) {
      agentBranch(branch);
      await verifyPointer(worktreePath);
      const current = (await git(["symbolic-ref", "--short", "HEAD"], worktreePath)).trim();
      if (current !== branch) {
        throw new GitError(`The worktree is on "${current}", not on "${branch}". Nothing was pushed.`);
      }
      await git(
        ["push", "--no-verify", "--set-upstream", remote, `refs/heads/${branch}:refs/heads/${branch}`],
        worktreePath,
        true,
      );
    },
  };
}
