import type { AgentConfig, Issue } from "./types";

const DONE_STATE_TYPES = new Set(["completed", "canceled"]);

/** The five pick rules of SPEC 4.1. Label names match exactly, case included. */
export function isEligible(issue: Issue, config: AgentConfig): boolean {
  return (
    issue.teamKey === config.teamKey &&
    issue.state.name === config.todoState &&
    issue.labels.includes(config.triggerLabel) &&
    !issue.labels.includes(config.failedLabel) &&
    issue.blockedBy.every((blocker) => DONE_STATE_TYPES.has(blocker.stateType))
  );
}

/** Linear priority 1 (urgent) to 4 (low) first, then 0 (no priority) last. */
function priorityRank(priority: number): number {
  return priority >= 1 && priority <= 4 ? priority : 5;
}

function createdAtMs(issue: Issue): number {
  const ms = Date.parse(issue.createdAt);
  return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms;
}

/**
 * Filters and orders the issues the worker may pick. It re-checks every rule, so it does not
 * trust the filter of the Linear query.
 */
export function selectCandidates(issues: Issue[], config: AgentConfig): Issue[] {
  return issues
    .filter((issue) => isEligible(issue, config))
    .sort(
      (a, b) =>
        priorityRank(a.priority) - priorityRank(b.priority) ||
        createdAtMs(a) - createdAtMs(b) ||
        a.number - b.number,
    );
}
