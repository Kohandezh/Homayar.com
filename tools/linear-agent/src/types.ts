export interface WorkflowState {
  id: string;
  name: string;
  type: string;
}

export interface Issue {
  id: string; // Linear UUID
  identifier: string; // "HOM-197"
  number: number; // 197
  title: string;
  description: string; // may be ""
  url: string;
  priority: number; // Linear scale: 0 = no priority, 1 = urgent, 2 = high, 3 = medium, 4 = low
  createdAt: string; // ISO 8601
  teamKey: string; // "HOM"
  state: WorkflowState;
  labels: string[]; // label names
  blockedBy: { identifier: string; stateType: string }[]; // stateType of each blocking issue
  comments: { author: string; body: string; createdAt: string }[];
}

export interface LinearPort {
  listIssues(query: { teamKey: string; stateName: string; labelName: string }): Promise<Issue[]>;
  getIssue(id: string): Promise<Issue | null>; // id = UUID or identifier
  listStates(teamKey: string): Promise<WorkflowState[]>;
  setState(issueId: string, stateId: string): Promise<void>;
  addComment(issueId: string, body: string): Promise<void>;
  addLabel(issueId: string, labelName: string): Promise<void>;
  removeLabel(issueId: string, labelName: string): Promise<void>;
  attachLink(issueId: string, url: string, title: string): Promise<void>;
}

export interface GitPort {
  createWorktree(opts: { path: string; branch: string; baseRef: string }): Promise<void>;
  removeWorktree(path: string): Promise<void>;
  deleteBranch(branch: string): Promise<void>; // local and remote branch
  changedFiles(worktreePath: string): Promise<string[]>; // repo-relative paths vs baseRef
  diff(worktreePath: string): Promise<string>;
  commitAll(worktreePath: string, message: string): Promise<void>;
  push(worktreePath: string, branch: string): Promise<void>;
}

export interface PullRequest {
  number: number;
  url: string;
  branch: string;
  state: "open" | "merged" | "closed";
}

export interface GitHubPort {
  createDraftPr(opts: { branch: string; base: string; title: string; body: string }): Promise<PullRequest>;
  getPr(number: number): Promise<PullRequest>;
}

export interface AgentRunResult {
  ok: boolean;
  costUsd: number;
  turns: number;
  error?: string;
}

export interface ReviewResult {
  approved: boolean;
  findings: string;
  costUsd: number;
}

export interface AgentRunner {
  runMaker(input: { cwd: string; prompt: string; maxBudgetUsd: number }): Promise<AgentRunResult>;
  runReviewer(input: { cwd: string; prompt: string; diff: string; maxBudgetUsd: number }): Promise<ReviewResult>;
}

export interface CheckSpec {
  name: string;
  cwd: string; // relative to the worktree root
  command: string;
}

export interface CheckResult {
  name: string;
  ok: boolean;
  output: string;
}

export interface ChecksPort {
  run(worktreePath: string, checks: CheckSpec[]): Promise<CheckResult[]>;
}

export interface LockPort {
  tryAcquire(key: string): Promise<boolean>;
  release(key: string): Promise<void>;
}

export interface TrackedPr {
  issueId: string;
  identifier: string;
  prNumber: number;
  prUrl: string;
  branch: string;
  worktreePath: string;
}

export interface StateStore {
  load(): Promise<TrackedPr[]>;
  save(items: TrackedPr[]): Promise<void>;
}

export interface LogLine {
  step: string;
  ticket?: string;
  costUsd?: number;
  message: string;
}

export interface Deps {
  linear: LinearPort;
  git: GitPort;
  github: GitHubPort;
  agent: AgentRunner;
  checks: ChecksPort;
  lock: LockPort;
  state: StateStore;
  now: () => Date;
  log: (line: LogLine) => void;
  /** Optional. The daemon sets it so a poll stops before the next ticket after SIGINT or SIGTERM. */
  shouldStop?: () => boolean;
}

export interface AgentConfig {
  teamKey: string; // default "HOM"
  todoState: string; // default "Todo"
  inProgressState: string; // default "In Progress"
  inReviewState: string; // default "In Review"
  doneState: string; // default "Done"
  triggerLabel: string; // default "agent"
  failedLabel: string; // default "agent-failed"
  maxConcurrent: number; // default 1
  model: string; // default "claude-opus-5"
  maxTurns: number; // default 80
  maxBudgetUsd: number; // per ticket, default 5
  pollMinutes: number; // default 10
  repoPath: string;
  worktreeRoot: string; // default "~/linear-agent-worktrees" (expanded)
  baseRef: string; // default "origin/main"
  baseBranch: string; // default "main"
  dryRun: boolean;
}

export interface TicketOutcome {
  identifier: string;
  outcome: "in_review" | "pr_ready" | "failed" | "skipped";
  prUrl?: string;
  reason?: string;
}

export interface RunSummary {
  wouldPick: string[]; // eligible identifiers in pick order (filled in dry run and normal run)
  processed: TicketOutcome[];
  followUps: { identifier: string; action: "done" | "reopened"; prUrl: string }[];
}
