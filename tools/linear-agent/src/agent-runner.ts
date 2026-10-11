import { query as sdkQuery, type SDKMessage, type SDKResultMessage } from "@anthropic-ai/claude-agent-sdk";
import { parseVerdict, reviewerMessage } from "./prompts";
import { buildQueryOptions } from "./sdk-options";
import type { AgentConfig, AgentRunner } from "./types";

export type QueryFn = typeof sdkQuery;

/** A reviewer run that ended without a usable answer. Carries the cost it already used. */
export class AgentRunError extends Error {
  override name = "AgentRunError";

  constructor(
    message: string,
    readonly costUsd: number,
  ) {
    super(message);
  }
}

/** Reads the whole stream and keeps the latest result message; its cost is already cumulative. */
async function lastResult(messages: AsyncIterable<SDKMessage>): Promise<SDKResultMessage | undefined> {
  let result: SDKResultMessage | undefined;
  for await (const message of messages) {
    if (message.type === "result") result = message;
  }
  return result;
}

function describeError(result: SDKResultMessage): string {
  if (result.subtype === "success") return result.result || "the run ended with an error";
  const details = result.errors.length > 0 ? `: ${result.errors.join("; ")}` : "";
  return `${result.subtype}${details}`;
}

export function createSdkAgentRunner(config: AgentConfig, query: QueryFn = sdkQuery): AgentRunner {
  return {
    async runMaker({ cwd, prompt, maxBudgetUsd }) {
      const options = buildQueryOptions(config, cwd, "maker", maxBudgetUsd);
      const result = await lastResult(query({ prompt, options }));
      if (!result) return { ok: false, costUsd: 0, turns: 0, error: "the SDK returned no result message" };
      const ok = result.subtype === "success" && !result.is_error;
      return {
        ok,
        costUsd: result.total_cost_usd,
        turns: result.num_turns,
        ...(ok ? {} : { error: describeError(result) }),
      };
    },

    async runReviewer({ cwd, prompt, diff, maxBudgetUsd }) {
      const options = buildQueryOptions(config, cwd, "reviewer", maxBudgetUsd);
      const result = await lastResult(query({ prompt: reviewerMessage(prompt, diff), options }));
      if (!result) throw new AgentRunError("the SDK returned no result message", 0);
      if (result.subtype !== "success" || result.is_error) {
        throw new AgentRunError(describeError(result), result.total_cost_usd);
      }
      return { approved: parseVerdict(result.result), findings: result.result, costUsd: result.total_cost_usd };
    },
  };
}
