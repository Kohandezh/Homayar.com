import { existsSync, mkdirSync, mkdtempSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { checksForChangedFiles, createBackendTestFinder, createCommandChecks } from "../src/index";
import type { CheckSandbox } from "../src/index";

const FRONTEND = [
  { name: "frontend: install", cwd: "Homayar/frontend", command: "npm ci" },
  { name: "frontend: typecheck", cwd: "Homayar/frontend", command: "npx tsc -b" },
  { name: "frontend: lint", cwd: "Homayar/frontend", command: "npm run lint" },
  { name: "frontend: test", cwd: "Homayar/frontend", command: "npm test" },
  { name: "frontend: build", cwd: "Homayar/frontend", command: "npm run build" },
];
let dir = "";
afterEach(() => {
  if (dir) rmSync(dir, { recursive: true, force: true });
  dir = "";
});

const WEBSITE = [
  { name: "website: install", cwd: "website", command: "pnpm install --frozen-lockfile" },
  { name: "website: lint", cwd: "website", command: "pnpm lint" },
  { name: "website: build", cwd: "website", command: "pnpm build" },
];

describe("checksForChangedFiles", () => {
  it("frontend files give the five frontend checks once", () => {
    expect(checksForChangedFiles(["Homayar/frontend/src/a.tsx", "Homayar/frontend/package.json"])).toEqual(FRONTEND);
  });

  it("changed backend test files give one pytest command in first-seen order without duplicates", () => {
    const files = [
      "Homayar/backend/tests/test_b.py",
      "Homayar/backend/tests/test_a.py",
      "Homayar/backend/tests/test_b.py",
      "Homayar/backend/tests/conftest.py",
    ];
    expect(checksForChangedFiles(files)).toEqual([
      { name: "backend: pytest", cwd: "Homayar", command: "uv run pytest backend/tests/test_b.py backend/tests/test_a.py" },
    ]);
  });

  it("a backend source file adds no check without a finder", () => {
    expect(checksForChangedFiles(["Homayar/backend/services/health.py"])).toEqual([]);
  });

  it("a backend source file uses the finder for its stem", () => {
    const stems: string[] = [];
    const finder = (stem: string) => {
      stems.push(stem);
      return stem === "health" ? ["backend/tests/test_health.py", "backend/tests/test_health_api.py"] : [];
    };
    const files = ["Homayar/backend/services/health.py", "Homayar/backend/tests/test_health.py", "Homayar/backend/x.py"];
    expect(checksForChangedFiles(files, finder)).toEqual([
      {
        name: "backend: pytest",
        cwd: "Homayar",
        command: "uv run pytest backend/tests/test_health.py backend/tests/test_health_api.py",
      },
    ]);
    expect(stems).toEqual(["health", "x"]);
  });

  it("website files give the website checks", () => {
    expect(checksForChangedFiles(["website/app/page.tsx"])).toEqual(WEBSITE);
  });

  it("root, tools and docs files give no check", () => {
    expect(checksForChangedFiles(["README.md", "tools/linear-agent/src/run.ts", "docs/a.md", "Homayar/pyproject.toml"])).toEqual([]);
  });

  it("keeps the order frontend, backend, website", () => {
    const names = checksForChangedFiles([
      "website/a.ts",
      "Homayar/backend/tests/test_x.py",
      "Homayar/frontend/a.ts",
    ]).map((c) => c.name);
    expect(names).toEqual([...FRONTEND.map((c) => c.name), "backend: pytest", ...WEBSITE.map((c) => c.name)]);
  });
});

describe("createBackendTestFinder", () => {
  it("finds test_<stem>.py and test_<stem>_*.py in the worktree", () => {
    dir = mkdtempSync(path.join(os.tmpdir(), "linear-agent-finder-"));
    const tests = path.join(dir, "Homayar", "backend", "tests");
    mkdirSync(path.join(tests, "api"), { recursive: true });
    for (const name of ["test_health.py", "test_health_api.py", "test_healthy.py", "test_other.py", "helper_health.py"]) {
      writeFileSync(path.join(tests, name), "");
    }
    writeFileSync(path.join(tests, "api", "test_health_routes.py"), "");

    const find = createBackendTestFinder(dir);
    expect(find("health")).toEqual([
      "backend/tests/api/test_health_routes.py",
      "backend/tests/test_health.py",
      "backend/tests/test_health_api.py",
    ]);
    expect(find("missing")).toEqual([]);
  });

  it("returns nothing when the tests folder does not exist", () => {
    expect(createBackendTestFinder("/does/not/exist")("health")).toEqual([]);
  });
});

describe("createCommandChecks", () => {
  const node = process.execPath;
  const unsafe = () => {
    const warnings: string[] = [];
    return { checks: createCommandChecks({ unsandboxed: true, warn: (m) => warnings.push(m) }), warnings };
  };

  it("runs each check in its folder and stops after the first failure", async () => {
    dir = mkdtempSync(path.join(os.tmpdir(), "linear-agent-checks-"));
    mkdirSync(path.join(dir, "sub"));
    const { checks } = unsafe();
    const results = await checks.run(dir, [
      { name: "ok", cwd: "sub", command: `${node} --version` },
      { name: "fails", cwd: ".", command: `${node} --this-flag-does-not-exist` },
      { name: "never runs", cwd: ".", command: `${node} --version` },
    ]);
    expect(results.map((r) => [r.name, r.ok])).toEqual([
      ["ok", true],
      ["fails", false],
    ]);
    expect(results[0].output).toContain(process.version);
  });

  it("a command that cannot start is a failed check, not a crash", async () => {
    const { checks } = unsafe();
    const results = await checks.run(os.tmpdir(), [{ name: "missing", cwd: ".", command: "linear-agent-no-such-program --x" }]);
    expect(results[0].ok).toBe(false);
    expect(results[0].output).toContain("Could not start");
  });

  it("the unsafe opt-out warns on every run that has checks", async () => {
    const { checks, warnings } = unsafe();
    await checks.run(os.tmpdir(), [{ name: "v", cwd: ".", command: `${node} --version` }]);
    await checks.run(os.tmpdir(), [{ name: "v", cwd: ".", command: `${node} --version` }]);
    await checks.run(os.tmpdir(), []);
    expect(warnings).toHaveLength(2);
    expect(warnings[0]).toContain("UNSAFE");
  });

  it("runs the argv the sandbox returns, in the check folder, and strips keys from the sandbox env", async () => {
    dir = mkdtempSync(path.join(os.tmpdir(), "linear-agent-checks-"));
    mkdirSync(path.join(dir, "Homayar"));
    const calls: { argv: string[]; worktreePath: string; cwd: string }[] = [];
    const sandbox: CheckSandbox = {
      async wrap(argv, worktreePath, cwd) {
        calls.push({ argv, worktreePath, cwd });
        const script = "process.stdout.write(JSON.stringify([process.cwd(), process.env.LINEAR_API_KEY ?? 'none', process.env.GITHUB_TOKEN ?? 'none']))";
        return { argv: [node, "-e", script], env: { ...process.env, LINEAR_API_KEY: "lin_test_key", GITHUB_TOKEN: "gh_test_token" } };
      },
      async close() {},
    };
    const results = await createCommandChecks({ sandbox }).run(dir, [
      { name: "backend: pytest", cwd: "Homayar", command: "uv run pytest backend/tests/test_a.py" },
    ]);
    expect(calls).toEqual([
      { argv: ["uv", "run", "pytest", "backend/tests/test_a.py"], worktreePath: dir, cwd: path.join(dir, "Homayar") },
    ]);
    expect(results[0].ok).toBe(true);
    const [cwd, linear, github] = JSON.parse(results[0].output) as string[];
    expect(realpathSync(cwd)).toBe(realpathSync(path.join(dir, "Homayar")));
    expect([linear, github]).toEqual(["none", "none"]);
  });

  it("fails closed: when the sandbox cannot start, no check runs and the result says why", async () => {
    dir = mkdtempSync(path.join(os.tmpdir(), "linear-agent-checks-"));
    const marker = path.join(dir, "ran");
    const sandbox: CheckSandbox = {
      async wrap() {
        throw new Error("sandbox dependencies are missing: bubblewrap (bwrap) not installed");
      },
      async close() {},
    };
    const results = await createCommandChecks({ sandbox }).run(dir, [
      { name: "frontend: install", cwd: ".", command: `${node} -e require('fs').writeFileSync('${marker}','x')` },
      { name: "frontend: lint", cwd: ".", command: `${node} --version` },
    ]);
    expect(results).toHaveLength(1);
    expect(results[0]).toMatchObject({ name: "frontend: install", ok: false });
    expect(results[0].output).toContain("The check sandbox could not start, so no check ran");
    expect(results[0].output).toContain("bubblewrap");
    expect(existsSync(marker)).toBe(false);
  });

  it("an empty check list needs no sandbox", async () => {
    const sandbox: CheckSandbox = {
      async wrap() {
        throw new Error("should not be called");
      },
      async close() {},
    };
    expect(await createCommandChecks({ sandbox }).run(os.tmpdir(), [])).toEqual([]);
  });
});
