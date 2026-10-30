/**
 * The issue trends answers (HOM-80 to HOM-83): GET /trends/issues and
 * GET /trends/conversations (backend/schemas/trends.py). Only the fields the
 * console reads; the provenance blocks are left out.
 */
// Relative, not "@/": e2e/trends.spec.ts reads these types and tsconfig.e2e.json has no paths.
import type {
  AggregateDisclosure,
  MissingDataIndicator,
  TrendCoverage,
} from "./organizations";

export type TrendGrain = "none" | "day" | "week" | "month";

export type TrendCellStatus = "computed" | "not_computable" | "insufficient_sample" | "withheld";

/** One number of the trend. The denominator is always sent, the rest only when `computed`. */
export interface TrendCell {
  status: TrendCellStatus;
  numerator: number | null;
  denominator: number;
  ratio: number | null;
  reason: string | null;
  minimum_sample: number;
}

export interface TrendRow {
  level: "family" | "issue";
  family: string;
  /** Null on a family row. */
  issue: string | null;
  cell: TrendCell;
  pending_review: number | null;
  absent_pending_review: number | null;
  missing: Record<string, number>;
}

export interface TrendPopulation {
  included: number;
  excluded: { duplicate_media: number };
}

/** One period of the series: `from <= received_at < to`, in the series timezone. */
export interface TrendPoint {
  from: string;
  to: string;
  partial: boolean;
  population: TrendPopulation;
  rows: TrendRow[];
}

/** The same shape as the organization views' disclosure (HOM-82). */
export type TrendDisclosure = AggregateDisclosure;

export interface TrendSeries {
  grain: TrendGrain;
  timezone: string;
  calendar: "gregorian";
  week_starts_on: "saturday";
}

export interface IssueTrendResponse {
  notices: string[];
  window: { from: string; to: string; basis: string };
  scope: { restricted: boolean };
  series: TrendSeries;
  population: TrendPopulation;
  rows: TrendRow[];
  points: TrendPoint[];
  coverage: TrendCoverage;
  missing_data: { indicators: MissingDataIndicator[] };
  disclosure: TrendDisclosure;
}

/** The query of GET /trends/issues this page sends; it never sends `agent_user_id`. */
export interface IssueTrendParams {
  from: string;
  to: string;
  grain: "week" | "month";
  timezone: string;
  team_id?: string;
}

/** One cell's conversations: `row` is `family:<id>` or `issue:<id>`, `point` absent for the window cell. */
export interface TrendLinkParams extends IssueTrendParams {
  trend_view: "issues";
  row: string;
  point?: number;
  offset: number;
  limit: number;
}

export interface TrendLinkConversation {
  meeting_id: string;
  state: "counted";
  analysis_run_id: string;
  evidence_ids: string[];
}

export interface TrendLinkResponse {
  cell: TrendCell;
  /** `total` is the cell's numerator. */
  page: { offset: number; limit: number; total: number };
  conversations: TrendLinkConversation[];
}
