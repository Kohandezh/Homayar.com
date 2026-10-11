import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { createGhCli, createJsonStateStore } from "../src/index";
import { toPullRequest } from "../src/adapters/github";

describe("toPullRequest", () => {
  const view = { url: "https://github.com/example/repo/pull/5", headRefName: "agent/hom-5-x" };
  it.each([
    [{ state: "OPEN", mergedAt: null }, "open"],
    [{ state: "MERGED", mergedAt: "2026-10-11T00:00:00Z" }, "merged"],
    [{ state: "CLOSED", mergedAt: null }, "closed"],
  ])("%o is %s", (fields, state) => {
    expect(toPullRequest(5, { ...view, ...fields })).toEqual({
      number: 5,
      url: view.url,
      branch: "agent/hom-5-x",
      state,
    });
  });
});

describe("createGhCli guards", () => {
  it("refuses to open a PR from a branch that is not an agent branch, before running gh", async () => {
    const gh = createGhCli({ repoPath: "/does/not/exist" });
    await expect(gh.createDraftPr({ branch: "main", base: "main", title: "t", body: "b" })).rejects.toThrow("Refusing");
    await expect(gh.createDraftPr({ branch: "feature/x", base: "main", title: "t", body: "b" })).rejects.toThrow("Refusing");
  });
});

describe("createJsonStateStore", () => {
  let dir = "";
  afterEach(() => rmSync(dir, { recursive: true, force: true }));

  it("starts empty, saves, and loads the same entries", async () => {
    dir = mkdtempSync(path.join(os.tmpdir(), "linear-agent-state-"));
    const store = createJsonStateStore(path.join(dir, "nested", "tracked-prs.json"));
    expect(await store.load()).toEqual([]);
    const entry = {
      issueId: "uuid-1",
      identifier: "HOM-1",
      prNumber: 1,
      prUrl: "https://github.com/example/repo/pull/1",
      branch: "agent/hom-1",
      worktreePath: "/wt/HOM-1",
    };
    await store.save([entry]);
    expect(await store.load()).toEqual([entry]);
  });

  it("a broken file throws instead of losing tracked PRs", async () => {
    dir = mkdtempSync(path.join(os.tmpdir(), "linear-agent-state-"));
    const file = path.join(dir, "tracked-prs.json");
    writeFileSync(file, "{ not json");
    await expect(createJsonStateStore(file).load()).rejects.toThrow();
    writeFileSync(file, "{}");
    await expect(createJsonStateStore(file).load()).rejects.toThrow("JSON array");
  });
});
