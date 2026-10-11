import { spawn } from "node:child_process";

export interface ExecResult {
  code: number;
  stdout: string;
  stderr: string;
  /** stdout and stderr in the order they arrived. */
  output: string;
}

export interface ExecOptions {
  cwd: string;
  env?: NodeJS.ProcessEnv;
  timeoutMs?: number;
  /** Only the last bytes of each stream are kept, so a noisy command cannot fill the memory. */
  maxOutputChars?: number;
}

const DEFAULT_MAX_OUTPUT = 1_000_000;
/** After a timeout, how long the process group gets between SIGTERM and SIGKILL. */
const KILL_GRACE_MS = 10_000;

function keepTail(text: string, max: number): string {
  return text.length > max ? text.slice(text.length - max) : text;
}

/**
 * Runs a program with an argument array (never through a shell). Resolves with the exit code,
 * also when it is not 0. Rejects only when the program cannot start, for example when it is
 * not installed.
 *
 * The program gets its own process group, so a timeout stops everything it started (npm
 * starts node, which starts more), not only the direct child.
 */
export function execCommand(file: string, args: string[], options: ExecOptions): Promise<ExecResult> {
  const max = options.maxOutputChars ?? DEFAULT_MAX_OUTPUT;
  return new Promise((resolve, reject) => {
    const child = spawn(file, args, {
      cwd: options.cwd,
      env: options.env ?? process.env,
      stdio: ["ignore", "pipe", "pipe"],
      detached: true,
    });
    let stdout = "";
    let stderr = "";
    let output = "";
    let timedOut = false;
    let killTimer: NodeJS.Timeout | undefined;

    const signalGroup = (signal: NodeJS.Signals): void => {
      if (child.pid === undefined) return;
      try {
        // A negative pid signals the whole process group.
        process.kill(-child.pid, signal);
      } catch {
        child.kill(signal);
      }
    };

    const timer =
      options.timeoutMs === undefined
        ? undefined
        : setTimeout(() => {
            timedOut = true;
            signalGroup("SIGTERM");
            killTimer = setTimeout(() => signalGroup("SIGKILL"), KILL_GRACE_MS);
          }, options.timeoutMs);
    const clearTimers = (): void => {
      if (timer) clearTimeout(timer);
      if (killTimer) clearTimeout(killTimer);
    };

    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout = keepTail(stdout + chunk, max);
      output = keepTail(output + chunk, max);
    });
    child.stderr.on("data", (chunk: string) => {
      stderr = keepTail(stderr + chunk, max);
      output = keepTail(output + chunk, max);
    });
    child.on("error", (error) => {
      clearTimers();
      reject(error);
    });
    child.on("close", (code, signal) => {
      clearTimers();
      // Anything the program left running in its group goes too.
      if (timedOut) signalGroup("SIGKILL");
      if (timedOut) output += `\n[linear-agent] stopped after ${options.timeoutMs} ms`;
      resolve({ code: code ?? (signal ? 128 : 1), stdout, stderr, output });
    });
  });
}

/** The same as process.env without the listed variables. */
export function envWithout(names: readonly string[], source: NodeJS.ProcessEnv = process.env): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...source };
  for (const name of names) delete env[name];
  return env;
}
