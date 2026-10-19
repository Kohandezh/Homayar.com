/**
 * The privacy API of the product (HOM-190): the data subject export, the
 * legal-hold register (HOM-192) and the media objects of one meeting.
 * Mirrors backend/schemas/privacy.py, backend/schemas/admin.py and
 * backend/schemas/meetings.py.
 */
import type { IsoDateTime } from "@/types/platform";

export interface SubjectExportInput {
  /** 1 to 10 values, each one e-mail, phone number, national ID, bank card or IBAN. */
  identifiers: string[];
  reason: string;
}

export interface LegalHoldItem {
  media_id: string;
  kind: string;
  meeting_id: string | null;
  meeting_title: string | null;
  reason: string | null;
  placed_by_user_id: string | null;
  placed_at: IsoDateTime;
  review_at: IsoDateTime;
}

export interface LegalHoldListResponse {
  items: LegalHoldItem[];
  next_cursor: string | null;
}

export interface LegalHoldListParams {
  /** ISO date and time; only holds with `review_at <= due_before` are listed. */
  due_before?: string;
  limit?: number;
  cursor?: string;
}

export type MediaLifecycleState =
  | "active"
  | "retention_pending"
  | "deletion_pending"
  | "deleted"
  | "legal_hold";

export interface MediaObject {
  id: string;
  meeting_id: string | null;
  kind: string;
  /** One of MediaLifecycleState; a newer server may send another value. */
  lifecycle_state: string;
  integrity: string;
  content_type: string;
  size_bytes: number;
  original_filename: string | null;
  created_at: IsoDateTime;
  retention_expires_at: IsoDateTime | null;
  deletion_requested_at: IsoDateTime | null;
  deleted_at: IsoDateTime | null;
  legal_hold_at: IsoDateTime | null;
  legal_hold_reason: string | null;
  legal_hold_by_user_id: string | null;
  legal_hold_review_at: IsoDateTime | null;
}

export interface MediaObjectListResponse {
  items: MediaObject[];
}

export interface PlaceLegalHoldInput {
  reason: string;
  /** ISO date and time with a zone, in the future and at most 3650 days out. */
  review_at: string;
}

/** The status of a share link: revoked wins, then expired, else active. */
export type ShareStatus = "active" | "revoked" | "expired";

/** Who can open a share link. */
export type ShareAudience = "link" | "workspace" | "recipients";

export interface AdminShareItem {
  id: string;
  meeting_id: string;
  meeting_title: string;
  /** One of ShareAudience; a newer server may send another value. */
  audience: string;
  /** One of ShareStatus; a newer server may send another value. */
  status: string;
  created_by_user_id: string;
  created_at: IsoDateTime;
  expires_at: IsoDateTime | null;
  access_count: number;
  last_accessed_at: IsoDateTime | null;
  revoked_at: IsoDateTime | null;
}

export interface AdminShareListResponse {
  items: AdminShareItem[];
  next_cursor: string | null;
}

export interface AdminShareListParams {
  status?: ShareStatus;
  limit?: number;
  cursor?: string;
}
