import path from "node:path";
import { SandboxManager, type SandboxRuntimeConfig } from "@anthropic-ai/sandbox-runtime";
import { ALLOWED_DOMAINS, checkWriteRoots, secretReadPaths } from "./policy";
import type { AgentConfig } from "./types";

/** Runs check commands under an OS sandbox. */
export interface CheckSandbox {
  /**
   * The argv and env that run `argv` inside the sandbox, with writes limited to the worktree,
   * temp folders and package caches. Throws when the sandbox cannot start.
   */
  wrap(argv: string[], worktreePath: string, cwd: string): Promise<{ argv: string[]; env: NodeJS.ProcessEnv }>;
  /** Stops the sandbox proxies, so the process can exit. */
  close(): Promise<void>;
}

export interface CheckSandboxPolicy {
  allowedDomains: string[];
  denyRead: string[];
  /** Write roots besides the worktree. */
  writeRoots: string[];
}

/** The SPEC 15.1 policy: the agent's hosts, the agent's read denies, and writes only where checks need them. */
export function checkSandboxPolicy(config: AgentConfig, home?: string, tmp?: string): CheckSandboxPolicy {
  return {
    allowedDomains: [...ALLOWED_DOMAINS],
    denyRead: secretReadPaths(config, home),
    writeRoots: checkWriteRoots(home, tmp),
  };
}

/** Quotes one argument for a POSIX shell. The sandbox runs its command string through a shell. */
export function shellQuote(arg: string): string {
  return /^[A-Za-z0-9_./=:@%+,-]+$/.test(arg) ? arg : `'${arg.replace(/'/g, `'\\''`)}'`;
}

/** The part of the sandbox-runtime manager this module uses; tests pass a fake. */
export type SandboxManagerLike = Pick<
  typeof SandboxManager,
  "isSupportedPlatform" | "checkDependencies" | "initialize" | "wrapWithSandboxArgv" | "reset"
>;

/**
 * A CheckSandbox over `@anthropic-ai/sandbox-runtime` (sandbox-exec on macOS, bubblewrap on
 * Linux). The sandbox starts on the first check, not at worker start, so a dry run never needs it.
 */
export function createSrtCheckSandbox(policy: CheckSandboxPolicy, manager: SandboxManagerLike = SandboxManager): CheckSandbox {
  let ready: Promise<void> | undefined;

  const start = async (): Promise<void> => {
    if (!manager.isSupportedPlatform()) {
      throw new Error(`the sandbox runtime does not support this platform (${process.platform})`);
    }
    const dependencies = manager.checkDependencies();
    if (dependencies.errors.length > 0) {
      throw new Error(`sandbox dependencies are missing: ${dependencies.errors.join("; ")}`);
    }
    const config: SandboxRuntimeConfig = {
      network: { allowedDomains: [...policy.allowedDomains], deniedDomains: [] },
      filesystem: { denyRead: [...policy.denyRead], allowWrite: [], denyWrite: [] },
    };
    await manager.initialize(config);
  };

  return {
    async wrap(argv, worktreePath, cwd) {
      ready ??= start().catch((error: unknown) => {
        // Try again on the next check instead of caching the failure forever.
        ready = undefined;
        throw error;
      });
      await ready;
      const command = argv.map(shellQuote).join(" ");
      const wrapped = await manager.wrapWithSandboxArgv(
        command,
        undefined,
        {
          filesystem: {
            denyRead: [...policy.denyRead],
            allowWrite: [path.resolve(worktreePath), ...policy.writeRoots],
            // The worker reads this pointer before every git call; checks may not change it.
            denyWrite: [path.join(path.resolve(worktreePath), ".git")],
          },
        },
        undefined,
        cwd,
      );
      return { argv: wrapped.argv, env: wrapped.env };
    },
    async close() {
      if (ready) await manager.reset();
      ready = undefined;
    },
  };
}
