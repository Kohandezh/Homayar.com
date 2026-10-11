import { execFileSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createGitCli, workerGitEnv } from "../src/index";
import type { GitPort } from "../src/index";

// Integration tests on a throwaway repo and bare "remote" under the OS temp folder.
// They never touch the real checkout.
function git(cwd: string, ...args: string[]): string {
  return execFileSync("git", args, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
}

describe("createGitCli", () => {
  let base = "";
  let repo = "";
  let remote = "";
  let root = "";
  let port: GitPort;
  const branch = "agent/hom-12-add-a-health-check";

  beforeEach(() => {
    base = realpathSync(mkdtempSync(path.join(os.tmpdir(), "linear-agent-git-")));
    remote = path.join(base, "remote.git");
    repo = path.join(base, "repo");
    root = path.join(base, "worktrees");
    git(base, "init", "--quiet", "--bare", "--initial-branch=main", remote);
    git(base, "init", "--quiet", "--initial-branch=main", repo);
    git(repo, "config", "user.name", "Test");
    git(repo, "config", "user.email", "test@example.invalid");
    git(repo, "config", "commit.gpgsign", "false");
    writeFileSync(path.join(repo, "README.md"), "hello\n");
    git(repo, "add", ".");
    git(repo, "commit", "--quiet", "-m", "base");
    git(repo, "remote", "add", "origin", remote);
    git(repo, "push", "--quiet", "origin", "main");
    port = createGitCli({ repoPath: repo, worktreeRoot: root, baseRef: "origin/main" });
  });

  afterEach(() => rmSync(base, { recursive: true, force: true }));

  it("creates a worktree, lists changed files, diffs, commits and pushes the agent branch", async () => {
    const wt = path.join(root, "HOM-12");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    expect(git(wt, "symbolic-ref", "--short", "HEAD").trim()).toBe(branch);
    // --no-track: the agent branch must not track origin/main.
    expect(() => git(wt, "rev-parse", "--abbrev-ref", "@{upstream}")).toThrow();

    writeFileSync(path.join(wt, "README.md"), "hello\nworld\n");
    writeFileSync(path.join(wt, "new.txt"), "new\n");
    expect((await port.changedFiles(wt)).sort()).toEqual(["README.md", "new.txt"]);
    const diff = await port.diff(wt);
    expect(diff).toContain("+world");
    expect(diff).toContain("new.txt");

    await port.commitAll(wt, "Add a health check (HOM-12)");
    expect(await port.changedFiles(wt)).toEqual(["README.md", "new.txt"]);
    await port.push(wt, branch);
    expect(git(remote, "branch", "--list", branch)).toContain(branch);
    expect(git(remote, "log", "-1", "--format=%s", "main").trim()).toBe("base");
  });

  it("commitAll with nothing to commit does not fail", async () => {
    const wt = path.join(root, "HOM-12");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    await expect(port.commitAll(wt, "nothing")).resolves.toBeUndefined();
  });

  it("a retry removes the old dirty worktree and branch of the same ticket first", async () => {
    const wt = path.join(root, "HOM-12");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    writeFileSync(path.join(wt, "left-over.txt"), "from the failed run\n");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    expect(existsSync(path.join(wt, "left-over.txt"))).toBe(false);
    expect(git(wt, "symbolic-ref", "--short", "HEAD").trim()).toBe(branch);
  });

  it("removeWorktree and deleteBranch clean up locally and on the remote", async () => {
    const wt = path.join(root, "HOM-12");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    writeFileSync(path.join(wt, "a.txt"), "a\n");
    await port.commitAll(wt, "work");
    await port.push(wt, branch);

    await port.removeWorktree(wt);
    await port.deleteBranch(branch);
    expect(existsSync(wt)).toBe(false);
    expect(git(repo, "branch", "--list", branch)).toBe("");
    expect(git(remote, "branch", "--list", branch)).toBe("");
    await expect(port.deleteBranch(branch)).resolves.toBeUndefined();
  });

  it("refuses paths that are not a ticket folder under the worktree root", async () => {
    await expect(port.removeWorktree(repo)).rejects.toThrow("Refusing to touch");
    await expect(port.removeWorktree(path.join(root, "HOM-12", "deeper"))).rejects.toThrow("Refusing to touch");
    await expect(
      port.createWorktree({ path: path.join(base, "elsewhere"), branch, baseRef: "origin/main" }),
    ).rejects.toThrow("Refusing to touch");
    expect(existsSync(path.join(repo, "README.md"))).toBe(true);
  });

  it("refuses branches that are not agent branches", async () => {
    const wt = path.join(root, "HOM-12");
    await expect(port.createWorktree({ path: wt, branch: "main", baseRef: "origin/main" })).rejects.toThrow("Refusing to use branch");
    await expect(port.deleteBranch("main")).rejects.toThrow("Refusing to use branch");
    await expect(port.push(repo, "main")).rejects.toThrow("Refusing to use branch");
  });

  it("a retry also deletes the old remote branch when no PR is tracked for it", async () => {
    const wt = path.join(root, "HOM-12");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    writeFileSync(path.join(wt, "a.txt"), "first attempt\n");
    await port.commitAll(wt, "first attempt");
    await port.push(wt, branch);
    expect(git(remote, "branch", "--list", branch)).toContain(branch);

    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    expect(git(remote, "branch", "--list", branch)).toBe("");
    writeFileSync(path.join(wt, "b.txt"), "second attempt\n");
    await port.commitAll(wt, "second attempt");
    await expect(port.push(wt, branch)).resolves.toBeUndefined();
  });

  it("a retry keeps the remote branch while a tracked PR uses it", async () => {
    const tracked = createGitCli({ repoPath: repo, worktreeRoot: root, baseRef: "origin/main", isBranchTracked: async () => true });
    const wt = path.join(root, "HOM-12");
    await tracked.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    writeFileSync(path.join(wt, "a.txt"), "a\n");
    await tracked.commitAll(wt, "a");
    await tracked.push(wt, branch);
    await tracked.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    expect(git(remote, "branch", "--list", branch)).toContain(branch);
  });

  it("never deletes a folder at the ticket path that is not a worktree", async () => {
    const wt = path.join(root, "HOM-12");
    mkdirSync(wt, { recursive: true });
    writeFileSync(path.join(wt, "notes.txt"), "keep me\n");
    await expect(port.createWorktree({ path: wt, branch, baseRef: "origin/main" })).rejects.toThrow("not a git worktree");
    await expect(port.removeWorktree(wt)).rejects.toThrow("never deletes it");
    expect(readFileSync(path.join(wt, "notes.txt"), "utf8")).toBe("keep me\n");
  });

  it("does not run repo hooks or fsmonitor", async () => {
    const marker = path.join(base, "hook-ran");
    const script = `#!/bin/sh\necho ran >> "${marker}"\n`;
    const hooks = path.join(repo, ".git", "hooks");
    for (const name of ["pre-commit", "commit-msg", "post-commit", "post-checkout", "pre-push", "reference-transaction"]) {
      writeFileSync(path.join(hooks, name), script);
      chmodSync(path.join(hooks, name), 0o755);
    }
    const fsmonitor = path.join(base, "fsmonitor.sh");
    writeFileSync(fsmonitor, script);
    chmodSync(fsmonitor, 0o755);
    git(repo, "config", "core.fsmonitor", fsmonitor);

    const wt = path.join(root, "HOM-12");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    writeFileSync(path.join(wt, "a.txt"), "a\n");
    await port.changedFiles(wt);
    await port.diff(wt);
    await port.commitAll(wt, "with hooks present");
    await port.push(wt, branch);
    expect(existsSync(marker)).toBe(false);
    // Control: the same hook does run for a plain git commit.
    writeFileSync(path.join(wt, "b.txt"), "b\n");
    git(wt, "add", "b.txt");
    git(wt, "commit", "--quiet", "-m", "plain");
    expect(existsSync(marker)).toBe(true);
  });

  describe("the .git pointer check", () => {
    const wtPath = () => path.join(root, "HOM-12");
    const steps = (wt: string) => [
      () => port.changedFiles(wt),
      () => port.diff(wt),
      () => port.commitAll(wt, "x"),
      () => port.push(wt, branch),
    ];

    it("refuses when .git points at a repo the agent made", async () => {
      const wt = wtPath();
      await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
      const evil = path.join(wt, "evil");
      git(base, "init", "--quiet", evil);
      writeFileSync(path.join(wt, ".git"), `gitdir: ${path.join(evil, ".git")}\n`);
      for (const step of steps(wt)) await expect(step()).rejects.toThrow("Refusing to run git");
      await expect(port.removeWorktree(wt)).rejects.toThrow("Refusing to run git");
      expect(existsSync(wt)).toBe(true);
    });

    it("refuses when .git is a folder or points at another ticket's worktree", async () => {
      const wt = wtPath();
      const other = path.join(root, "HOM-13");
      await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
      await port.createWorktree({ path: other, branch: "agent/hom-13-other", baseRef: "origin/main" });
      writeFileSync(path.join(wt, ".git"), readFileSync(path.join(other, ".git"), "utf8"));
      await expect(port.changedFiles(wt)).rejects.toThrow("belongs to another worktree");

      rmSync(path.join(wt, ".git"));
      mkdirSync(path.join(wt, ".git"));
      await expect(port.commitAll(wt, "x")).rejects.toThrow("not a plain file");
    });
  });

  it("push refuses when the worktree is on another branch", async () => {
    const wt = path.join(root, "HOM-12");
    await port.createWorktree({ path: wt, branch, baseRef: "origin/main" });
    git(wt, "checkout", "--quiet", "-b", "agent/other");
    await expect(port.push(wt, branch)).rejects.toThrow("Nothing was pushed");
    expect(git(remote, "branch", "--list")).not.toContain("agent/");
  });
});

describe("workerGitEnv", () => {
  const source = {
    PATH: "/bin",
    LINEAR_API_KEY: "lin_test_key",
    ANTHROPIC_API_KEY: "sk-test-not-real",
    GITHUB_TOKEN: "gh_test_token",
    GH_TOKEN: "gh_test_token",
    GIT_DIR: "/elsewhere/.git",
  };

  it("local git calls get none of the keys", () => {
    const env = workerGitEnv(false, source);
    for (const name of ["LINEAR_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "GH_TOKEN", "GIT_DIR"]) expect(env[name]).toBeUndefined();
    expect(env).toMatchObject({ PATH: "/bin", GIT_TERMINAL_PROMPT: "0" });
  });

  it("network git calls keep only the GitHub auth", () => {
    const env = workerGitEnv(true, source);
    expect(env.GITHUB_TOKEN).toBe("gh_test_token");
    expect(env.GH_TOKEN).toBe("gh_test_token");
    expect(env.LINEAR_API_KEY).toBeUndefined();
    expect(env.ANTHROPIC_API_KEY).toBeUndefined();
    expect(env.GIT_DIR).toBeUndefined();
  });
});
