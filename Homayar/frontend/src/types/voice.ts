/**
 * The voice capacity answers (backend/routers/v1/voice.py). The backend
 * answers with the Pydantic response models `CapacityResponse` and
 * `RecoverResponse` (backend/voice/admission.py builds their content), so
 * these types follow named server schemas, not a plain dict.
 */

/** The selected workspace's concurrent calls. A `limit` of 0 turns voice calls off. */
export interface VoiceWorkspaceCapacity {
  workspace_id: string;
  held: number;
  limit: number;
}

export interface VoiceMediaNode {
  /** Latin text: `^[A-Za-z0-9._:-]+$`, up to 128 characters. */
  node_id: string;
  max_concurrent: number;
  held: number;
  draining: boolean;
  healthy: boolean;
  generation: number;
  /** ISO datetime with an offset. */
  heartbeat_at: string;
}

export interface VoiceDeploymentCapacity {
  profile: string;
  held: number;
  limit: number;
  nodes: VoiceMediaNode[];
}

/** One latency sample set of `VoiceMetrics`; milliseconds, counted by this API process. */
export interface VoiceAdmissionLatency {
  count: number;
  mean: number;
  p50: number;
  p95: number;
  p99: number;
  max: number;
}

/** The runtime metrics of `GET /voice/capacity` (VoiceMetricsSnapshot on the server). */
export interface VoiceMetrics {
  admitted_total: number;
  active_calls: number;
  /** Reason codes to counts; the keys are dynamic. */
  rejections_by_reason: Record<string, number>;
  releases_by_reason: Record<string, number>;
  admission_latency_ms: VoiceAdmissionLatency;
}

/**
 * GET /voice/capacity. `deployment` and `metrics` come only when the server
 * agrees the caller is a platform operator on a browser session.
 */
export interface VoiceCapacityResponse {
  workspace: VoiceWorkspaceCapacity;
  deployment?: VoiceDeploymentCapacity;
  metrics?: VoiceMetrics;
}

/** The answer of POST /voice/recover (RecoverResponse on the server). */
export interface VoiceRecoverResponse {
  lost_nodes: string[];
  failed_calls: {
    node_lost: string[];
    lease_expired: string[];
  };
}

/** The answer of POST /voice/nodes/{node_id}/drain. */
export interface VoiceNodeDrainResponse {
  node_id: string;
  max_concurrent: number;
  draining: boolean;
  generation: number;
  heartbeat_at: string;
}
