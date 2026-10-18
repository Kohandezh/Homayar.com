/**
 * The workspace AI processing policy API (HOM-188, ADR 0027). Mirrors
 * backend/schemas/ai_policy.py; the server rejects extra keys, so every
 * request body must carry exactly these fields.
 */

/** Where a stage may run (backend/providers/registry.py EndpointKind). */
export type AiEndpointKind = "in_process" | "private" | "cloud";

/** What a cloud analysis endpoint receives (backend/providers/policy.py). */
export type AiCloudTextRule = "redacted" | "original";

export interface AiStagePolicy {
  /** Where the stage may run; empty means the stage does not run. */
  endpoint_kinds: AiEndpointKind[];
  /** Regions a networked endpoint may process data in; cloud needs at least one. */
  regions: string[];
  /** Manifest ids the stage may use; empty means any approved model. */
  manifests: string[];
  /** Same-policy fallback to another eligible candidate. */
  fallback: boolean;
}

export interface AiIntelligenceStagePolicy extends AiStagePolicy {
  cloud_text: AiCloudTextRule;
}

export interface AiPolicy {
  asr: AiStagePolicy;
  diarization: AiStagePolicy;
  intelligence: AiIntelligenceStagePolicy;
}

export interface AiPolicyResponse {
  /** 0 = the built-in default (never stored). */
  version: number;
  source: "default" | "workspace";
  policy_schema: string;
  digest: string;
  policy: AiPolicy;
  reason: string | null;
  created_by_user_id: string | null;
  created_at: string | null;
}

export interface AiPolicyUpdateInput {
  policy: AiPolicy;
  /** The version the caller read; a different current version is a 409. */
  expected_version: number;
  reason: string;
}

export interface AiPolicyUpdateResponse {
  /** False when the document equaled the current one; no version was written. */
  changed: boolean;
  current: AiPolicyResponse;
}

export interface AiPolicyVersionListResponse {
  current_version: number;
  /** Stored versions only, newest first; empty while the default is in use. */
  items: AiPolicyResponse[];
  presets: {
    local_only: AiPolicy;
    private: AiPolicy;
  };
}
