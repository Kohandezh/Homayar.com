import path from "node:path";
import { branchName } from "./branch";
import { checksForChangedFiles, createBackendTestFinder } from "./checks";
import { commitMessage, failureComment, fixPrompt, makerPrompt, prBody, prTitle, reviewerPrompt } from "./prompts";
import { isEligible, selectCandidates } from "./select";
import type {
  AgentConfig,
  Deps,
  Issue,
  ReviewResult,
  RunSummary,
  TicketOutcome,
  TrackedPr,
  WorkflowState,
} from "./types";

type Step = "claim" | "worktree" | "maker" | "review" | "checks" | "push" | "pr" | "status";

/** A failure that already knows its step and, optionally, the error output to show. */
class StepFailure extends Error {
  constructor(
    readonly step: Step,
    message: string,
    readonly detail = "",
  ) {
    super(message);
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** A runner may attach the cost it already spent to the error it throws. */
function costOf(error: unknown): number {
  const cost = (error as { costUsd?: unknown } | null)?.costUsd;
  return typeof cost === "number" && Number.isFinite(cost) ? cost : 0;
}

function findState(states: WorkflowState[], name: string): WorkflowState | undefined {
  return states.find((state) => state.name === name);
}

function money(value: number): string {
  return `$${value.toFixed(2)}`;
}

/** Per-ticket context: the cost spent so far and the log helper of SPEC 4.8. */
class TicketRun {
  spent = 0;

  constructor(
    readonly deps: Deps,
    readonly config: AgentConfig,
    readonly identifier: string,
  ) {}

  log(step: Step | "failure", message: string): void {
    this.deps.log({ step, ticket: this.identifier, costUsd: this.spent, message });
  }

  private addCost(cost: number): void {
    if (Number.isFinite(cost)) this.spent += cost;
  }

  private checkCap(step: Step): void {
    if (this.spent >= this.config.maxBudgetUsd) {
      throw new StepFailure(
        step,
        `cost cap reached: spent ${money(this.spent)} of ${money(this.config.maxBudgetUsd)} for this ticket`,
      );
    }
  }

  async maker(cwd: string, prompt: string, label: string): Promise<void> {
    let result;
    try {
      result = await this.deps.agent.runMaker({ cwd, prompt, maxBudgetUsd: this.config.maxBudgetUsd - this.spent });
    } catch (error) {
      this.addCost(costOf(error));
      this.checkCap("maker");
      throw new StepFailure("maker", `maker run failed: ${errorMessage(error)}`);
    }
    this.addCost(result.costUsd);
    this.log("maker", `${label}: ${result.ok ? "finished" : "failed"} after ${result.turns} turns`);
    // The cap is checked before `ok`, so a run that fails and also hits the cap reports the cap.
    this.checkCap("maker");
    if (!result.ok) {
      throw new StepFailure("maker", `maker run failed: ${result.error ?? "no error text"}`, result.error ?? "");
    }
  }

  async review(cwd: string, prompt: string, diff: string): Promise<ReviewResult> {
    let result;
    try {
      result = await this.deps.agent.runReviewer({ cwd, prompt, diff, maxBudgetUsd: this.config.maxBudgetUsd - this.spent });
    } catch (error) {
      this.addCost(costOf(error));
      this.checkCap("review");
      throw new StepFailure("review", `reviewer run failed: ${errorMessage(error)}`);
    }
    this.addCost(result.costUsd);
    this.log("review", result.approved ? "approved" : "changes requested");
    this.checkCap("review");
    return result;
  }

  /** The failure path of SPEC 4.5. Each Linear write is tried on its own, so one error does not skip the rest. */
  async fail(
    issue: Issue,
    step: Step,
    error: unknown,
    context: { states?: WorkflowState[]; worktreePath?: string; prUrl?: string },
  ): Promise<TicketOutcome> {
    const { linear } = this.deps;
    const { config } = this;
    const failedStep = error instanceof StepFailure ? error.step : step;
    const reason = errorMessage(error);
    const detail = error instanceof StepFailure && error.detail ? error.detail : reason;
    this.log("failure", `failed at step "${failedStep}": ${reason}`);

    const attempt = async (what: string, action: () => Promise<void>): Promise<void> => {
      try {
        await action();
      } catch (writeError) {
        this.log("failure", `could not ${what}: ${errorMessage(writeError)}`);
      }
    };
    await attempt("comment the failure", () =>
      linear.addComment(
        issue.id,
        failureComment({
          step: failedStep,
          reason,
          errorText: detail,
          worktreePath: context.worktreePath,
          prUrl: context.prUrl,
          todoState: config.todoState,
          triggerLabel: config.triggerLabel,
          failedLabel: config.failedLabel,
        }),
      ),
    );
    await attempt(`move the ticket to ${config.todoState}`, async () => {
      const states = context.states ?? (await linear.listStates(config.teamKey));
      const todo = findState(states, config.todoState);
      if (!todo) throw new Error(`workflow state "${config.todoState}" not found in team ${config.teamKey}`);
      await linear.setState(issue.id, todo.id);
    });
    await attempt(`remove the "${config.triggerLabel}" label`, () => linear.removeLabel(issue.id, config.triggerLabel));
    await attempt(`add the "${config.failedLabel}" label`, () => linear.addLabel(issue.id, config.failedLabel));

    const outcome: TicketOutcome = { identifier: this.identifier, outcome: "failed", reason: `${failedStep}: ${reason}` };
    if (context.prUrl) outcome.prUrl = context.prUrl;
    return outcome;
  }
}

/** SPEC 4.3 to 4.5 for one claimed ticket. Never throws. */
async function workOnTicket(ticket: TicketRun, issue: Issue): Promise<TicketOutcome> {
  const { deps, config } = ticket;
  let step: Step = "claim";
  let states: WorkflowState[] | undefined;
  let worktreePath: string | undefined;
  let prUrl: string | undefined;
  try {
    states = await deps.linear.listStates(config.teamKey);
    const inProgress = findState(states, config.inProgressState);
    if (!inProgress) {
      throw new StepFailure("claim", `workflow state "${config.inProgressState}" not found in team ${config.teamKey}`);
    }
    await deps.linear.setState(issue.id, inProgress.id);
    await deps.linear.addComment(issue.id, `Picked up by linear-agent at ${deps.now().toISOString()}`);
    ticket.log("claim", `claimed, moved to ${config.inProgressState}`);

    step = "worktree";
    const branch = branchName(issue);
    const wtPath = path.join(config.worktreeRoot, issue.identifier);
    await deps.git.createWorktree({ path: wtPath, branch, baseRef: config.baseRef });
    worktreePath = wtPath;
    ticket.log("worktree", `created ${wtPath} on ${branch} from ${config.baseRef}`);

    step = "maker";
    await ticket.maker(wtPath, makerPrompt(issue), "first run");
    let changed = await deps.git.changedFiles(wtPath);
    if (changed.length === 0) throw new StepFailure("maker", "no changes: the maker did not change any file");

    step = "review";
    const diff = await deps.git.diff(wtPath);
    const review = await ticket.review(wtPath, reviewerPrompt(issue), diff);
    if (!review.approved) {
      step = "maker";
      await ticket.maker(wtPath, fixPrompt(issue, review.findings), "fix round");
      changed = await deps.git.changedFiles(wtPath);
      if (changed.length === 0) throw new StepFailure("maker", "no changes left after the fix round");
    }

    step = "checks";
    const specs = checksForChangedFiles(changed, createBackendTestFinder(wtPath));
    const results = await deps.checks.run(wtPath, specs);
    const failed = results.filter((result) => !result.ok);
    ticket.log(
      "checks",
      specs.length === 0
        ? "no surface checks apply to the changed files"
        : results.map((result) => `${result.name}: ${result.ok ? "passed" : "failed"}`).join("; "),
    );
    if (failed.length > 0) {
      throw new StepFailure(
        "checks",
        `check failed: ${failed.map((result) => result.name).join(", ")}`,
        failed.map((result) => `${result.name}:\n${result.output}`).join("\n"),
      );
    }

    step = "push";
    await deps.git.commitAll(wtPath, commitMessage(issue));
    await deps.git.push(wtPath, branch);
    ticket.log("push", `pushed ${branch}`);

    step = "pr";
    const title = prTitle(issue);
    const pr = await deps.github.createDraftPr({
      branch,
      base: config.baseBranch,
      title,
      body: prBody(issue, { checks: results, changedFiles: changed, reviewApproved: review.approved, costUsd: ticket.spent }),
    });
    prUrl = pr.url;
    // Saved before any other Linear write, so a later error still leaves the PR tracked.
    const tracked = await deps.state.load();
    const entry: TrackedPr = {
      issueId: issue.id,
      identifier: issue.identifier,
      prNumber: pr.number,
      prUrl: pr.url,
      branch,
      worktreePath: wtPath,
    };
    await deps.state.save([...tracked, entry]);
    ticket.log("pr", `opened draft PR ${pr.url}`);
    await deps.linear.addComment(issue.id, `linear-agent opened a draft PR: ${pr.url}`);
    await deps.linear.attachLink(issue.id, pr.url, title);

    step = "status";
    const inReview = findState(states, config.inReviewState);
    if (inReview) {
      await deps.linear.setState(issue.id, inReview.id);
      ticket.log("status", `moved to ${config.inReviewState}`);
      return { identifier: issue.identifier, outcome: "in_review", prUrl };
    }
    await deps.linear.addComment(
      issue.id,
      `PR ready for review: ${pr.url}\nThe team has no "${config.inReviewState}" state, so the ticket stays in ${config.inProgressState}.`,
    );
    ticket.log("status", `no "${config.inReviewState}" state; stays in ${config.inProgressState}, commented PR ready`);
    return { identifier: issue.identifier, outcome: "pr_ready", prUrl };
  } catch (error) {
    return ticket.fail(issue, step, error, { states, worktreePath, prUrl });
  }
}

async function processTicket(
  deps: Deps,
  config: AgentConfig,
  candidate: Issue,
): Promise<{ claimed: boolean; outcome: TicketOutcome }> {
  const ticket = new TicketRun(deps, config, candidate.identifier);
  const skipped = (reason: string) => ({
    claimed: false,
    outcome: { identifier: candidate.identifier, outcome: "skipped" as const, reason },
  });

  let acquired: boolean;
  try {
    acquired = await deps.lock.tryAcquire(candidate.identifier);
  } catch (error) {
    ticket.log("claim", `skipped: could not take the lock: ${errorMessage(error)}`);
    return skipped(`lock error: ${errorMessage(error)}`);
  }
  if (!acquired) {
    ticket.log("claim", "skipped: another worker holds the lock");
    return skipped("lock busy");
  }

  try {
    let fresh: Issue | null;
    try {
      fresh = await deps.linear.getIssue(candidate.id);
    } catch (error) {
      ticket.log("claim", `skipped: could not re-read the ticket: ${errorMessage(error)}`);
      return skipped(`re-read failed: ${errorMessage(error)}`);
    }
    if (!fresh || !isEligible(fresh, config)) {
      ticket.log("claim", "skipped: not eligible any more on re-read");
      return skipped("not eligible on re-read");
    }
    return { claimed: true, outcome: await workOnTicket(ticket, fresh) };
  } finally {
    try {
      await deps.lock.release(candidate.identifier);
    } catch (error) {
      ticket.log("claim", `could not release the lock: ${errorMessage(error)}`);
    }
  }
}

/** SPEC 4.6: act on tracked PRs that were merged or closed. Errors are logged and the entry is kept. */
async function runFollowUps(deps: Deps, config: AgentConfig, summary: RunSummary): Promise<void> {
  const tracked = await deps.state.load();
  if (tracked.length === 0) return;

  let states: WorkflowState[] | undefined;
  const stateId = async (name: string): Promise<string> => {
    states ??= await deps.linear.listStates(config.teamKey);
    const state = findState(states, name);
    if (!state) throw new Error(`workflow state "${name}" not found in team ${config.teamKey}`);
    return state.id;
  };

  const keep: TrackedPr[] = [];
  let changed = false;
  for (const entry of tracked) {
    const log = (message: string): void =>
      deps.log({ step: "followup", ticket: entry.identifier, costUsd: 0, message });
    // Best effort: a git error is logged and the entry is still dropped, so the Linear writes
    // above it do not repeat on every poll.
    const cleanUp = async (): Promise<void> => {
      const steps = [
        { what: `the worktree ${entry.worktreePath}`, verb: "remove", done: "removed", action: () => deps.git.removeWorktree(entry.worktreePath) },
        { what: `the branch ${entry.branch}`, verb: "delete", done: "deleted", action: () => deps.git.deleteBranch(entry.branch) },
      ];
      for (const step of steps) {
        try {
          await step.action();
          log(`${step.done} ${step.what}`);
        } catch (error) {
          log(`could not ${step.verb} ${step.what}; clean it up by hand: ${errorMessage(error)}`);
        }
      }
    };
    try {
      const pr = await deps.github.getPr(entry.prNumber);
      if (pr.state === "open") {
        log(`PR #${entry.prNumber} is still open`);
        keep.push(entry);
        continue;
      }
      if (config.dryRun) {
        log(
          pr.state === "merged"
            ? `dry run: PR #${entry.prNumber} is merged; would move the ticket to ${config.doneState}, remove ${entry.worktreePath} and delete ${entry.branch}`
            : `dry run: PR #${entry.prNumber} was closed without a merge; would comment, move the ticket to ${config.todoState}, remove the "${config.triggerLabel}" label, remove ${entry.worktreePath} and delete ${entry.branch}`,
        );
        keep.push(entry);
        continue;
      }
      if (pr.state === "merged") {
        await deps.linear.setState(entry.issueId, await stateId(config.doneState));
        await cleanUp();
        summary.followUps.push({ identifier: entry.identifier, action: "done", prUrl: entry.prUrl });
        log(`PR #${entry.prNumber} merged: moved to ${config.doneState}`);
      } else {
        await deps.linear.addComment(
          entry.issueId,
          `The draft PR ${entry.prUrl} was closed without a merge. linear-agent moved the ticket back to ${config.todoState} ` +
            `and removed the "${config.triggerLabel}" label. Add the label again to retry.`,
        );
        await deps.linear.setState(entry.issueId, await stateId(config.todoState));
        await deps.linear.removeLabel(entry.issueId, config.triggerLabel);
        await cleanUp();
        summary.followUps.push({ identifier: entry.identifier, action: "reopened", prUrl: entry.prUrl });
        log(`PR #${entry.prNumber} closed without a merge: moved back to ${config.todoState}`);
      }
      changed = true;
    } catch (error) {
      log(`follow-up failed, kept for the next poll: ${errorMessage(error)}`);
      keep.push(entry);
    }
  }
  if (changed) await deps.state.save(keep);
}

/**
 * One poll: follow-ups for tracked PRs, then pick and process tickets one at a time until
 * `maxConcurrent` were claimed. In a dry run nothing is written anywhere.
 */
export async function runOnce(deps: Deps, config: AgentConfig): Promise<RunSummary> {
  const summary: RunSummary = { wouldPick: [], processed: [], followUps: [] };
  await runFollowUps(deps, config, summary);

  const listed = await deps.linear.listIssues({
    teamKey: config.teamKey,
    stateName: config.todoState,
    labelName: config.triggerLabel,
  });
  const candidates = selectCandidates(listed, config);
  summary.wouldPick = candidates.map((issue) => issue.identifier);
  deps.log({
    step: "poll",
    message:
      candidates.length > 0
        ? `${candidates.length} eligible, in pick order: ${summary.wouldPick.join(", ")}`
        : "no eligible tickets",
  });
  if (config.dryRun) {
    deps.log({ step: "poll", message: "dry run: nothing was claimed or changed" });
    return summary;
  }

  let claimed = 0;
  for (const candidate of candidates) {
    if (claimed >= config.maxConcurrent) break;
    if (deps.shouldStop?.()) {
      deps.log({ step: "poll", message: "stop requested; no more tickets in this poll" });
      break;
    }
    const result = await processTicket(deps, config, candidate);
    summary.processed.push(result.outcome);
    if (result.claimed) claimed += 1;
  }
  return summary;
}
