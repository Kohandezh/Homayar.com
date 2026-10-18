import { useId } from "react";

import {
  AI_POLICY_COPY,
  ENDPOINT_KINDS,
  type AiStageName,
} from "@/components/admin/ai-policy/ai-policy-copy";
import Ids from "@/components/admin/ai-policy/Ids";
import { useI18n } from "@/lib/i18n-context";
import type {
  AiIntelligenceStagePolicy,
  AiStagePolicy,
} from "@/types/ai-policy";

/** One stage of the read view: where it may run, fallback, models, cloud text. */
export default function AiPolicyStageSection({
  stage,
  policy,
}: {
  stage: AiStageName;
  policy: AiStagePolicy | AiIntelligenceStagePolicy;
}) {
  const { lang } = useI18n();
  const copy = AI_POLICY_COPY[lang];
  const headingId = useId();
  const kinds = ENDPOINT_KINDS.filter((kind) => policy.endpoint_kinds.includes(kind));

  return (
    <section className="platform-surface p-4 sm:p-5" aria-labelledby={headingId}>
      <h2 id={headingId} className="m-0 text-sm font-semibold text-text-primary">
        {copy.stages[stage]}
      </h2>

      {kinds.length === 0 ? (
        <p className="mt-2 mb-0 text-sm">{copy.turnedOff}</p>
      ) : (
        <ul className="mt-2 mb-0 grid gap-1 ps-5 text-sm">
          {kinds.map((kind) => (
            <li key={kind}>
              {copy.kinds[kind]}
              {/* A cloud provider only runs in the regions the policy approves. */}
              {kind === "cloud" && policy.regions.length > 0 && (
                <>
                  {": "}
                  <Ids ids={policy.regions} />
                </>
              )}
            </li>
          ))}
        </ul>
      )}

      <p className="mt-2 mb-0 text-sm">
        {policy.fallback ? copy.fallbackAllowed : copy.fallbackNotAllowed}
      </p>
      <p className="mt-1 mb-0 text-sm">
        {copy.manifestsLabel}:{" "}
        {policy.manifests.length > 0 ? (
          <Ids ids={policy.manifests} />
        ) : (
          <span>{copy.anyManifest}</span>
        )}
      </p>
      {stage === "intelligence" && "cloud_text" in policy && (
        <p className="mt-1 mb-0 text-sm">{copy.cloudText[policy.cloud_text]}</p>
      )}
    </section>
  );
}
