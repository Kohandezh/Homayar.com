import type { Issue } from "./types";

const MAX_SLUG_LENGTH = 40;

/** `agent/hom-<number>-<slug>`, or `agent/hom-<number>` when the title has no ASCII letters or digits. */
export function branchName(issue: Pick<Issue, "number" | "title">): string {
  const slug = issue.title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, MAX_SLUG_LENGTH)
    .replace(/-+$/, "");
  return slug ? `agent/hom-${issue.number}-${slug}` : `agent/hom-${issue.number}`;
}
