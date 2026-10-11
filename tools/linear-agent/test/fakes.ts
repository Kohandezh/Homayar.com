import { defaultConfig } from "../src/index";
import type {
  AgentConfig,
  AgentRunResult,
  CheckResult,
  CheckSpec,
  Deps,
  Issue,
  LogLine,
  PullRequest,
  ReviewResult,
  TrackedPr,
  WorkflowState,
} from "../src/index";

export const TODO: WorkflowState = { id: "state-todo", name: "Todo", type: "unstarted" };
export const IN_PROGRESS: WorkflowState = { id: "state-progress", name: "In Progress", type: "started" };
export const IN_REVIEW: WorkflowState = { id: "state-review", name: "In Review", type: "started" };
export const DONE: WorkflowState = { id: "state-done", name: "Done", type: "completed" };
export const ALL_STATES = [TODO, IN_PROGRESS, IN_REVIEW, DONE];

export const NOW = new Date("2026-10-11T08:00:00.000Z");

export function makeIssue(overrides: Partial<Issue> = {}): Issue {
  const number = overrides.number ?? 12;
  return {
    id: `uuid-${number}`,
    identifier: `HOM-${number}`,
    number,
    title: "Add a health check",
    description: "The API needs a /healthz route.",
    url: `https://linear.app/homayar/issue/HOM-${number}`,
    priority: 2,
    createdAt: "2026-10-01T10:00:00.000Z",
    teamKey: "HOM",
    state: TODO,
    labels: ["agent"],
    blockedBy: [],
    comments: [],
    ...overrides,
  };
}

export function testConfig(overrides: Partial<AgentConfig> = {}): AgentConfig {
  return defaultConfig({ repoPath: "/repo", worktreeRoot: "/wt", ...overrides });
}

export interface Call {
  port: "linear" | "git" | "github" | "agent" | "checks" | "lock" | "state";
  method: string;
  args: unknown[];
}

/** Methods that change something. A dry run must call none of them. */
export const WRITE_METHODS = new Set([
  "linear.setState",
  "linear.addComment",
  "linear.addLabel",
  "linear.removeLabel",
  "linear.attachLink",
  "git.createWorktree",
  "git.removeWorktree",
  "git.deleteBranch",
  "git.commitAll",
  "git.push",
  "github.createDraftPr",
  "agent.runMaker",
  "agent.runReviewer",
  "checks.run",
  "lock.tryAcquire",
  "lock.release",
  "state.save",
]);

export interface FakeOptions {
  issues?: Issue[];
  /** What listIssues returns; defaults to `issues`. */
  listed?: Issue[];
  /** Issues returned by getIssue (the re-read); defaults to `issues`. */
  reread?: Issue[];
  states?: WorkflowState[];
  makerResults?: AgentRunResult[];
  reviewResults?: (ReviewResult | Error)[];
  changedFiles?: string[][];
  checkResults?: (specs: CheckSpec[]) => CheckResult[];
  heldLocks?: string[];
  tracked?: TrackedPr[];
  prStates?: Record<number, PullRequest["state"] | Error>;
  /** "linear.attachLink" and so on: the method throws this error. */
  failures?: Record<string, Error>;
}

export function createFakes(options: FakeOptions = {}) {
  const calls: Call[] = [];
  const logs: LogLine[] = [];
  const issues = options.issues ?? [makeIssue()];
  const reread = options.reread ?? issues;
  const makerResults = [...(options.makerResults ?? [])];
  const reviewResults = [...(options.reviewResults ?? [])];
  const changedFiles = [...(options.changedFiles ?? [])];
  const locks = new Set(options.heldLocks ?? []);
  let tracked = [...(options.tracked ?? [])];

  function record(port: Call["port"], method: string, args: unknown[]): void {
    calls.push({ port, method, args });
    const failure = options.failures?.[`${port}.${method}`];
    if (failure) throw failure;
  }

  const deps: Deps = {
    linear: {
      async listIssues(query) {
        record("linear", "listIssues", [query]);
        return options.listed ?? issues;
      },
      async getIssue(id) {
        record("linear", "getIssue", [id]);
        return reread.find((issue) => issue.id === id || issue.identifier === id) ?? null;
      },
      async listStates(teamKey) {
        record("linear", "listStates", [teamKey]);
        return options.states ?? ALL_STATES;
      },
      async setState(issueId, stateId) {
        record("linear", "setState", [issueId, stateId]);
      },
      async addComment(issueId, body) {
        record("linear", "addComment", [issueId, body]);
      },
      async addLabel(issueId, labelName) {
        record("linear", "addLabel", [issueId, labelName]);
      },
      async removeLabel(issueId, labelName) {
        record("linear", "removeLabel", [issueId, labelName]);
      },
      async attachLink(issueId, url, title) {
        record("linear", "attachLink", [issueId, url, title]);
      },
    },
    git: {
      async createWorktree(opts) {
        record("git", "createWorktree", [opts]);
      },
      async removeWorktree(path) {
        record("git", "removeWorktree", [path]);
      },
      async deleteBranch(branch) {
        record("git", "deleteBranch", [branch]);
      },
      async changedFiles(worktreePath) {
        record("git", "changedFiles", [worktreePath]);
        return changedFiles.length > 1 ? (changedFiles.shift() ?? []) : (changedFiles[0] ?? ["tools/x.ts"]);
      },
      async diff(worktreePath) {
        record("git", "diff", [worktreePath]);
        return "diff --git a/tools/x.ts b/tools/x.ts";
      },
      async commitAll(worktreePath, message) {
        record("git", "commitAll", [worktreePath, message]);
      },
      async push(worktreePath, branch) {
        record("git", "push", [worktreePath, branch]);
      },
    },
    github: {
      async createDraftPr(opts) {
        record("github", "createDraftPr", [opts]);
        return { number: 101, url: "https://github.com/example/repo/pull/101", branch: opts.branch, state: "open" };
      },
      async getPr(number) {
        record("github", "getPr", [number]);
        const state = options.prStates?.[number] ?? "open";
        if (state instanceof Error) throw state;
        return { number, url: `https://github.com/example/repo/pull/${number}`, branch: "agent/x", state };
      },
    },
    agent: {
      async runMaker(input) {
        record("agent", "runMaker", [input]);
        return makerResults.shift() ?? { ok: true, costUsd: 1, turns: 5 };
      },
      async runReviewer(input) {
        record("agent", "runReviewer", [input]);
        const next = reviewResults.shift() ?? { approved: true, findings: "Looks good.\nVERDICT: APPROVED", costUsd: 0.5 };
        if (next instanceof Error) throw next;
        return next;
      },
    },
    checks: {
      async run(worktreePath, checks) {
        record("checks", "run", [worktreePath, checks]);
        return options.checkResults ? options.checkResults(checks) : checks.map((c) => ({ name: c.name, ok: true, output: "ok" }));
      },
    },
    lock: {
      async tryAcquire(key) {
        record("lock", "tryAcquire", [key]);
        if (locks.has(key)) return false;
        locks.add(key);
        return true;
      },
      async release(key) {
        record("lock", "release", [key]);
        locks.delete(key);
      },
    },
    state: {
      async load() {
        record("state", "load", []);
        return [...tracked];
      },
      async save(items) {
        record("state", "save", [items]);
        tracked = [...items];
      },
    },
    now: () => NOW,
    log: (line) => {
      logs.push(line);
    },
  };

  const named = (name: string) => calls.filter((call) => `${call.port}.${call.method}` === name);
  return {
    deps,
    calls,
    logs,
    locks,
    tracked: () => tracked,
    named,
    writes: () => calls.filter((call) => WRITE_METHODS.has(`${call.port}.${call.method}`)),
    linearWrites: () => calls.filter((call) => call.port === "linear" && WRITE_METHODS.has(`linear.${call.method}`)),
    comments: () => named("linear.addComment").map((call) => call.args[1] as string),
  };
}
