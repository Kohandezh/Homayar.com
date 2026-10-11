import type { CheckResult, Issue } from "./types";

function ticketBlock(issue: Issue): string {
  const labels = issue.labels.length > 0 ? issue.labels.join(", ") : "(none)";
  const comments =
    issue.comments.length > 0
      ? issue.comments.map((c) => `- ${c.author} at ${c.createdAt}:\n${indent(c.body)}`).join("\n")
      : "(no comments)";
  return [
    `Ticket: ${issue.identifier}`,
    `Title: ${issue.title}`,
    `URL: ${issue.url}`,
    `Labels: ${labels}`,
    "",
    "Description:",
    issue.description.trim() || "(empty)",
    "",
    "Comments (oldest first):",
    comments,
  ].join("\n");
}

function indent(text: string): string {
  return text
    .split("\n")
    .map((line) => `    ${line}`)
    .join("\n");
}

export function makerPrompt(issue: Issue): string {
  return [
    `Implement Linear ticket ${issue.identifier} in this repository.`,
    "",
    ticketBlock(issue),
    "",
    "Keep the change to what the ticket asks. When you are done, reply with a short summary of what you changed and why.",
  ].join("\n");
}

export function fixPrompt(issue: Issue, findings: string): string {
  return [
    `A reviewer looked at your uncommitted change for Linear ticket ${issue.identifier} and asked for changes.`,
    "Fix every point that is correct. If you disagree with a point, say why in your final message.",
    "",
    "Reviewer findings:",
    findings.trim() || "(the reviewer gave no details)",
    "",
    ticketBlock(issue),
  ].join("\n");
}

export function reviewerPrompt(issue: Issue): string {
  return [
    `Review the uncommitted change for Linear ticket ${issue.identifier}. The diff against the base is below.`,
    "Check the diff against the ticket and its acceptance criteria. Read the changed files when the diff is not enough.",
    "List each problem with its file and a short reason.",
    "",
    ticketBlock(issue),
    "",
    "The last line of your answer must be exactly VERDICT: APPROVED or VERDICT: CHANGES_REQUESTED.",
  ].join("\n");
}

const MAX_DIFF_CHARS = 150_000;

/** The full reviewer message: the review prompt plus the diff, cut when it is very large. */
export function reviewerMessage(prompt: string, diff: string): string {
  const cut = diff.length > MAX_DIFF_CHARS;
  const shown = cut ? diff.slice(0, MAX_DIFF_CHARS) : diff;
  const note = cut ? `\n[diff cut after ${MAX_DIFF_CHARS} characters; read the files for the rest]` : "";
  return `${prompt}\n\nDiff:\n\`\`\`diff\n${shown}\n\`\`\`${note}`;
}

/** True only when the last non-empty line is exactly `VERDICT: APPROVED`. */
export function parseVerdict(text: string): boolean {
  const lines = text.split("\n").map((line) => line.trim());
  const last = lines.filter((line) => line !== "").pop();
  return last === "VERDICT: APPROVED";
}

export function prTitle(issue: Issue): string {
  return `${issue.title} (${issue.identifier})`;
}

export function commitMessage(issue: Issue): string {
  return `${issue.title} (${issue.identifier})\n\nDraft change by linear-agent for ${issue.url}`;
}

export function prBody(
  issue: Issue,
  details: { checks: CheckResult[]; changedFiles: string[]; reviewApproved: boolean; costUsd: number },
): string {
  const checkLines =
    details.checks.length > 0
      ? details.checks.map((c) => `- ${c.name}: ${c.ok ? "passed" : "failed"}`)
      : ["- No surface checks applied to the changed files."];
  const shownFiles = details.changedFiles.slice(0, 50).map((f) => `- \`${f}\``);
  if (details.changedFiles.length > 50) shownFiles.push(`- and ${details.changedFiles.length - 50} more`);
  const review = details.reviewApproved
    ? "Approved on the first review."
    : "The reviewer asked for changes; the maker ran one fix round.";
  return [
    `Draft PR opened by linear-agent for [${issue.identifier}](${issue.url}).`,
    "",
    `Linear issue: ${issue.url}`,
    "",
    "## Checks",
    ...checkLines,
    "",
    "## Changed files",
    ...shownFiles,
    "",
    "## Agent review",
    review,
    "",
    `Agent cost for this ticket: $${details.costUsd.toFixed(2)} (estimate).`,
    "",
    "A human must review this PR before it is marked ready. linear-agent never merges.",
  ].join("\n");
}

/** The last `max` lines of a text, without trailing empty lines. */
export function lastLines(text: string, max = 20): string {
  const lines = text.replace(/\s+$/, "").split("\n");
  return lines.slice(-max).join("\n");
}

export function failureComment(details: {
  step: string;
  reason: string;
  errorText: string;
  worktreePath?: string;
  prUrl?: string;
  todoState: string;
  triggerLabel: string;
  failedLabel: string;
}): string {
  const parts = [
    `linear-agent failed at step "${details.step}": ${details.reason}`,
    "",
    "Last error lines:",
    "```",
    lastLines(details.errorText || details.reason, 20),
    "```",
    "",
  ];
  if (details.prUrl) parts.push(`A draft PR was already opened: ${details.prUrl}`);
  if (details.worktreePath) parts.push(`The worktree is kept for debugging at ${details.worktreePath}.`);
  parts.push(
    `The ticket goes back to ${details.todoState} with the "${details.failedLabel}" label. ` +
      `To retry, fix the cause, remove "${details.failedLabel}" and add "${details.triggerLabel}" again.`,
  );
  return parts.join("\n");
}
