// ─── Call Center Agent ───────────────────────────────────────

export interface CallAgent {
  id: string;
  name: string;
  email?: string;
  team?: string;
  language: string;
  avg_qc_score: number;
  total_calls: number;
}

// ─── Call ────────────────────────────────────────────────────

export interface Call {
  id: string;
  agent_id: string;
  agent_name?: string;
  direction: "inbound" | "outbound";
  language: string;
  duration_seconds: number;
  status: "uploaded" | "transcribing" | "analyzing" | "completed" | "failed";
  file_path?: string;
  file_name?: string;
  phone_number?: string;
  created_at?: string;
  analyzed_at?: string;
}

export interface CallListItem {
  id: string;
  agent_name?: string;
  direction: string;
  language: string;
  duration_seconds: number;
  status: string;
  overall_qc_score?: number;
  sentiment?: string;
  file_name?: string;
  created_at?: string;
}

// ─── Transcript ──────────────────────────────────────────────

export interface TranscriptSegment {
  speaker: string;
  text: string;
  start_time?: number;
  end_time?: number;
}

export interface Transcript {
  id: string;
  language: string;
  segments: string[];
  full_text?: string;
}

// ─── QC Report ───────────────────────────────────────────────

export interface QCMetrics {
  overall_score: number;
  empathy: number;
  problem_solving: number;
  communication_clarity: number;
  call_closing: number;
  silence_handling: number;
  tone: number;
}

export interface QCReport extends QCMetrics {
  id: string;
  is_mock?: boolean;
  sentiment: string;
  sentiment_progression?: string;
  resolution: string;
  silence_total_seconds: number;
}

// ─── Problem Detection ───────────────────────────────────────

export interface ProblemDetection {
  id: string;
  root_cause?: string;
  issue_category?: string;
  severity: string;
  system_failures: string[];
  repeated_issue: boolean;
  impact_scope?: string;
}

// ─── Business Insight ────────────────────────────────────────

export interface BusinessInsight {
  id: string;
  upsell_signal: boolean;
  churn_risk: string;
  customer_intent?: string;
  revenue_opportunity?: string;
  conversion_probability?: string;
  estimated_nps?: number;
  satisfaction_trend?: string;
}

// ─── Full Call Report ────────────────────────────────────────

export interface CallReport {
  call: Call;
  transcript?: Transcript;
  qc_report?: QCReport;
  problems?: ProblemDetection;
  insights?: BusinessInsight;
  recommended_actions?: string[];
}

// ─── Dashboard KPIs ──────────────────────────────────────────

export interface DashboardKpis {
  total_calls: number;
  calls_today: number;
  avg_qc_score: number;
  avg_duration_seconds: number;
  avg_nps: number;
  open_issues: number;
  churn_risk_calls: number;
  upsell_opportunities: number;
}

export interface Alert {
  id: string;
  title: string;
  severity: "critical" | "warning" | "info";
  region?: string;
  status: string;
}

export interface AggregatedIssue {
  problem: string;
  count: number;
  severity: "low" | "medium" | "high" | "critical";
  category: string;
  affected_agents: string[];
  trend: "increasing" | "stable" | "decreasing";
  recommended_action: string;
}

// ─── Graph (reusable) ───────────────────────────────────────

export interface Entity {
  id: string;
  label: string;
  name: string;
  status: string | null;
  properties: Record<string, string>;
}

export interface EntityDetail {
  id: string;
  labels: string[];
  properties: Record<string, string>;
  connections: Connection[];
}

export interface Connection {
  rel_type: string;
  target_id: string;
  target_name: string;
  target_labels: string[];
}

export interface GraphNode {
  id: string;
  label: string;
  name: string;
  properties: Record<string, string>;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: string;
}

// ─── Chat / Copilot ──────────────────────────────────────────

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  data?: Record<string, unknown>;
}
