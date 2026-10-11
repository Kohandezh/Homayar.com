import { readFileSync } from "node:fs";
import path from "node:path";
import { parseEnv } from "node:util";
import { createGhCli } from "./adapters/github";
import { createGitCli } from "./adapters/git";
import { createLinearClient } from "./adapters/linear";
import { createJsonStateStore } from "./adapters/state-store";
import { createSdkAgentRunner } from "./agent-runner";
import { checkSandboxPolicy, createSrtCheckSandbox, type CheckSandbox } from "./check-sandbox";
import { createCommandChecks } from "./checks";
import { ConfigError, PACKAGE_DIR, type Keys, loadConfig, requireKeys, stateDir } from "./config";
import { createFileLock } from "./lock";
import { runOnce } from "./run";
import type { AgentConfig, Deps, LogLine } from "./types";

const MODES = ["once", "daemon", "dry-run"] as const;
type Mode = (typeof MODES)[number];

/** Loads tools/linear-agent/.env. Variables that are already set in the environment win. */
function loadDotEnv(file: string): void {
  let text: string;
  try {
    text = readFileSync(file, "utf8");
  } catch {
    return;
  }
  for (const [name, value] of Object.entries(parseEnv(text))) {
    if (process.env[name] === undefined) process.env[name] = value;
  }
}

function printLog(line: LogLine): void {
  const ticket = line.ticket ? ` ${line.ticket}` : "";
  const cost = line.costUsd === undefined ? "" : ` $${line.costUsd.toFixed(2)}`;
  console.log(`${new Date().toISOString()} [${line.step}]${ticket}${cost} ${line.message}`);
}

function createDeps(config: AgentConfig, keys: Keys): { deps: Deps; checkSandbox?: CheckSandbox } {
  const dir = stateDir(process.env, config);
  const state = createJsonStateStore(path.join(dir, "tracked-prs.json"));
  const unsandboxed = process.env.LINEAR_AGENT_UNSANDBOXED_CHECKS?.trim() === "1";
  const checkSandbox = unsandboxed ? undefined : createSrtCheckSandbox(checkSandboxPolicy(config));
  const warn = (message: string): void => printLog({ step: "checks", message });
  if (unsandboxed) warn("UNSAFE: LINEAR_AGENT_UNSANDBOXED_CHECKS=1 is set; checks will run without a sandbox.");
  const deps: Deps = {
    linear: createLinearClient({ apiKey: keys.linearApiKey, teamKey: config.teamKey }),
    git: createGitCli({
      repoPath: config.repoPath,
      worktreeRoot: config.worktreeRoot,
      baseRef: config.baseRef,
      isBranchTracked: async (branch) => (await state.load()).some((entry) => entry.branch === branch),
    }),
    github: createGhCli({ repoPath: config.repoPath, token: keys.githubToken || undefined }),
    agent: createSdkAgentRunner(config),
    checks: checkSandbox ? createCommandChecks({ sandbox: checkSandbox }) : createCommandChecks({ unsandboxed: true, warn }),
    lock: createFileLock(path.join(dir, "locks")),
    state,
    now: () => new Date(),
    log: printLog,
  };
  return { deps, checkSandbox };
}

async function runDaemon(deps: Deps, config: AgentConfig): Promise<void> {
  let stopping = false;
  let wake: (() => void) | undefined;
  const onSignal = (signal: NodeJS.Signals): void => {
    if (stopping) {
      printLog({ step: "daemon", message: `second ${signal}: exiting now` });
      process.exit(130);
    }
    stopping = true;
    printLog({ step: "daemon", message: `${signal}: stopping after the current ticket` });
    wake?.();
  };
  process.on("SIGINT", onSignal);
  process.on("SIGTERM", onSignal);
  deps.shouldStop = () => stopping;

  printLog({ step: "daemon", message: `polling every ${config.pollMinutes} minutes` });
  while (!stopping) {
    try {
      await runOnce(deps, config);
    } catch (error) {
      printLog({ step: "poll", message: `poll failed, trying again next time: ${(error as Error).message}` });
    }
    if (stopping) break;
    await new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, config.pollMinutes * 60_000);
      wake = () => {
        clearTimeout(timer);
        resolve();
      };
    });
    wake = undefined;
  }
  printLog({ step: "daemon", message: "stopped" });
}

async function main(argv: string[]): Promise<void> {
  const mode = argv[2] as Mode | undefined;
  if (!mode || !MODES.includes(mode)) {
    console.error(`Usage: tsx src/cli.ts <${MODES.join("|")}>`);
    process.exitCode = 2;
    return;
  }
  loadDotEnv(path.join(PACKAGE_DIR, ".env"));
  const dryRun = mode === "dry-run";
  const keys = requireKeys(process.env, dryRun);
  const config = loadConfig(process.env, { dryRun });
  const { deps, checkSandbox } = createDeps(config, keys);

  try {
    if (mode === "daemon") {
      await runDaemon(deps, config);
      return;
    }
    const summary = await runOnce(deps, config);
    console.log(JSON.stringify(summary, null, 2));
  } finally {
    // The check sandbox runs proxy servers; stop them so the process can exit.
    await checkSandbox?.close();
  }
}

main(process.argv).catch((error: unknown) => {
  // Expected problems (a missing key, a bad setting) print one clear line, without a stack trace.
  const message = error instanceof Error ? error.message : String(error);
  console.error(error instanceof ConfigError ? `linear-agent: ${message}` : `linear-agent failed: ${message}`);
  process.exitCode = 1;
});
