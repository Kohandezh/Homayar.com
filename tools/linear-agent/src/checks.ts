import { readdirSync } from "node:fs";
import path from "node:path";
import type { CheckSandbox } from "./check-sandbox";
import { envWithout, execCommand } from "./exec";
import { SECRET_ENV_VARS } from "./policy";
import type { CheckResult, CheckSpec, ChecksPort } from "./types";

const FRONTEND_PREFIX = "Homayar/frontend/";
const BACKEND_PREFIX = "Homayar/backend/";
const BACKEND_TESTS_PREFIX = "Homayar/backend/tests/";
const WEBSITE_PREFIX = "website/";
const TEST_FILE = /^test_.*\.py$/;

const FRONTEND_CHECKS: CheckSpec[] = [
  { name: "frontend: install", cwd: "Homayar/frontend", command: "npm ci" },
  { name: "frontend: typecheck", cwd: "Homayar/frontend", command: "npx tsc -b" },
  { name: "frontend: lint", cwd: "Homayar/frontend", command: "npm run lint" },
  { name: "frontend: test", cwd: "Homayar/frontend", command: "npm test" },
  { name: "frontend: build", cwd: "Homayar/frontend", command: "npm run build" },
];

const WEBSITE_CHECKS: CheckSpec[] = [
  { name: "website: install", cwd: "website", command: "pnpm install --frozen-lockfile" },
  { name: "website: lint", cwd: "website", command: "pnpm lint" },
  { name: "website: build", cwd: "website", command: "pnpm build" },
];

/** Returns test paths relative to `Homayar` for the stem of a changed backend source file. */
export type BackendTestFinder = (stem: string) => string[];

/**
 * The checks for the surfaces that the changed files touch, in the order frontend, backend,
 * website. Paths are repo-relative. Without `findBackendTests`, only changed
 * `Homayar/backend/tests/**\/test_*.py` files select backend tests.
 */
export function checksForChangedFiles(files: string[], findBackendTests?: BackendTestFinder): CheckSpec[] {
  const checks: CheckSpec[] = [];
  if (files.some((file) => file.startsWith(FRONTEND_PREFIX))) checks.push(...FRONTEND_CHECKS);

  const backendTests: string[] = [];
  const addTest = (testPath: string): void => {
    if (!backendTests.includes(testPath)) backendTests.push(testPath);
  };
  for (const file of files) {
    if (!file.startsWith(BACKEND_PREFIX)) continue;
    const base = path.posix.basename(file);
    if (file.startsWith(BACKEND_TESTS_PREFIX) && TEST_FILE.test(base)) {
      addTest(file.slice("Homayar/".length));
    } else if (base.endsWith(".py") && findBackendTests) {
      for (const testPath of findBackendTests(base.slice(0, -".py".length))) addTest(testPath);
    }
  }
  if (backendTests.length > 0) {
    checks.push({ name: "backend: pytest", cwd: "Homayar", command: `uv run pytest ${backendTests.join(" ")}` });
  }

  if (files.some((file) => file.startsWith(WEBSITE_PREFIX))) checks.push(...WEBSITE_CHECKS);
  return checks;
}

/**
 * Finds `test_<stem>.py` and `test_<stem>_*.py` under `<worktree>/Homayar/backend/tests`.
 * The folder is read once, on the first call.
 */
export function createBackendTestFinder(worktreePath: string): BackendTestFinder {
  let testFiles: string[] | undefined;
  const listTests = (): string[] => {
    if (testFiles) return testFiles;
    const dir = path.join(worktreePath, "Homayar", "backend", "tests");
    try {
      testFiles = (readdirSync(dir, { recursive: true }) as string[])
        .map((entry) => entry.split(path.sep).join("/"))
        .filter((entry) => TEST_FILE.test(path.posix.basename(entry)))
        .sort();
    } catch {
      testFiles = [];
    }
    return testFiles;
  };
  return (stem) => {
    const exact = `test_${stem}.py`;
    const prefix = `test_${stem}_`;
    return listTests()
      .filter((entry) => {
        const base = path.posix.basename(entry);
        return base === exact || base.startsWith(prefix);
      })
      .map((entry) => `backend/tests/${entry}`);
  };
}

const CHECK_TIMEOUT_MS = 30 * 60 * 1000;
// Next.js writes telemetry settings to the home folder, which the check sandbox does not allow.
const CHECK_ENV_EXTRA = { NEXT_TELEMETRY_DISABLED: "1" };

export type CommandChecksOptions =
  | { sandbox: CheckSandbox }
  /** UNSAFE opt-out (LINEAR_AGENT_UNSANDBOXED_CHECKS=1): checks run on the host. Warns on every run. */
  | { unsandboxed: true; warn: (message: string) => void };

/**
 * Runs the checks one by one in the worktree and stops after the first failure, because later
 * steps of a surface depend on the earlier ones (install before build).
 *
 * The checks run code the maker wrote, so each one runs inside the check sandbox (SPEC 15.1)
 * and never gets a key. If the sandbox cannot start, no check runs and the result is a failure.
 */
export function createCommandChecks(options: CommandChecksOptions): ChecksPort {
  return {
    async run(worktreePath, checks) {
      const results: CheckResult[] = [];
      if (checks.length === 0) return results;
      if ("unsandboxed" in options) {
        options.warn(
          "UNSAFE: LINEAR_AGENT_UNSANDBOXED_CHECKS=1, so the checks run maker-written code on this machine without a sandbox.",
        );
      }
      for (const check of checks) {
        const argv = check.command.split(/\s+/).filter(Boolean);
        const cwd = path.join(worktreePath, check.cwd);
        let spawnArgv = argv;
        let baseEnv: NodeJS.ProcessEnv = process.env;
        if ("sandbox" in options) {
          try {
            ({ argv: spawnArgv, env: baseEnv } = await options.sandbox.wrap(argv, worktreePath, cwd));
          } catch (error) {
            results.push({
              name: check.name,
              ok: false,
              output:
                `The check sandbox could not start, so no check ran: ${(error as Error).message}\n` +
                "Install the sandbox dependencies (see tools/linear-agent/README.md) and retry the ticket.",
            });
            break;
          }
        }
        let result: CheckResult;
        try {
          const [file, ...args] = spawnArgv;
          const run = await execCommand(file, args, {
            cwd,
            env: { ...envWithout(SECRET_ENV_VARS, baseEnv), ...CHECK_ENV_EXTRA },
            timeoutMs: CHECK_TIMEOUT_MS,
            maxOutputChars: 200_000,
          });
          result = { name: check.name, ok: run.code === 0, output: run.output };
        } catch (error) {
          result = { name: check.name, ok: false, output: `Could not start "${check.command}": ${(error as Error).message}` };
        }
        results.push(result);
        if (!result.ok) break;
      }
      return results;
    },
  };
}
