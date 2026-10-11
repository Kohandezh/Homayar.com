import { execCommand } from "../exec";
import type { GitHubPort, PullRequest } from "../types";

export class GitHubError extends Error {
  override name = "GitHubError";
}

interface PrView {
  state: string;
  url: string;
  headRefName: string;
  mergedAt: string | null;
}

export function toPullRequest(number: number, view: PrView): PullRequest {
  const state: PullRequest["state"] =
    view.state === "MERGED" || view.mergedAt ? "merged" : view.state === "CLOSED" ? "closed" : "open";
  return { number, url: view.url, branch: view.headRefName, state };
}

/** GitHubPort over the `gh` CLI. It only creates draft PRs and reads PRs. There is no merge call. */
export function createGhCli(options: { repoPath: string; token?: string }): GitHubPort {
  const env: NodeJS.ProcessEnv = { ...process.env, GH_PROMPT_DISABLED: "1" };
  if (options.token) env.GITHUB_TOKEN = options.token;

  async function gh(args: string[]): Promise<string> {
    const result = await execCommand("gh", args, { cwd: options.repoPath, env });
    if (result.code !== 0) {
      throw new GitHubError(`gh ${args.slice(0, 2).join(" ")} failed (exit ${result.code}): ${result.stderr.trim()}`);
    }
    return result.stdout;
  }

  return {
    async createDraftPr({ branch, base, title, body }) {
      if (!branch.startsWith("agent/") || branch === base) {
        throw new GitHubError(`Refusing to open a PR from "${branch}" into "${base}".`);
      }
      const out = await gh(["pr", "create", "--draft", "--base", base, "--head", branch, "--title", title, "--body", body]);
      const url = out.trim().split("\n").pop() ?? "";
      const match = /\/pull\/(\d+)\b/.exec(url);
      if (!match) throw new GitHubError(`gh pr create did not print a PR URL: ${out.trim()}`);
      return { number: Number(match[1]), url, branch, state: "open" };
    },

    async getPr(number) {
      const out = await gh(["pr", "view", String(number), "--json", "state,url,headRefName,mergedAt"]);
      return toPullRequest(number, JSON.parse(out) as PrView);
    },
  };
}
