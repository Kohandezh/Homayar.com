/**
 * The voice capacity answers (backend/routers/v1/voice.py,
 * backend/voice/admission.py capacity_snapshot). The backend sends a plain
 * dict, so these types follow what it builds, not a server schema.
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

/**
 * GET /voice/capacity. `deployment` and `metrics` come only when the server
 * agrees the caller is a platform operator on a browser session.
 */
export interface VoiceCapacityResponse {
  workspace: VoiceWorkspaceCapacity;
  deployment?: VoiceDeploymentCapacity;
  metrics?: Record<string, unknown>;
}

/** The answer of POST /voice/nodes/{node_id}/drain. */
export interface VoiceNodeDrainResponse {
  node_id: string;
  max_concurrent: number;
  draining: boolean;
  generation: number;
  heartbeat_at: string;
}
