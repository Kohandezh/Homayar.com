/**
 * The workspace locations API (HOM-191). Mirrors backend/schemas/locations.py;
 * the server refuses extra body keys (`extra="forbid"`), so every request
 * carries exactly the fields the spec allows.
 */

/** What a location is: a branch, a dealer or a service center. */
export type LocationKind = "branch" | "dealer" | "service_center";

export interface Location {
  id: string;
  workspace_id: string;
  kind: LocationKind;
  name: string;
  /** Matches ^[A-Za-z0-9_.-]+$ and is unique per workspace. */
  code: string;
  province: string | null;
  city: string | null;
  district: string | null;
  street: string | null;
  postal_code: string | null;
  address_text: string | null;
  latitude: number | null;
  longitude: number | null;
  phone: string | null;
  is_active: boolean;
  service_tags: string[];
  /** The server owns this object; the console never sends it. */
  opening_hours: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

/** Only the filters that are set; an empty object asks for the whole list. */
export interface LocationListParams {
  kind?: LocationKind;
  active?: boolean;
  city?: string;
}

export interface LocationListResponse {
  items: Location[];
  total: number;
}

/** A create body: the required four plus only the optional fields that are set. */
export interface LocationCreateInput {
  kind: LocationKind;
  name: string;
  code: string;
  is_active: boolean;
  province?: string;
  city?: string;
  district?: string;
  street?: string;
  postal_code?: string;
  address_text?: string;
  latitude?: number;
  longitude?: number;
  phone?: string;
  service_tags?: string[];
}

/** A patch body: only the changed fields; a cleared field is sent as null. */
export interface LocationUpdateInput {
  kind?: LocationKind;
  name?: string;
  code?: string;
  is_active?: boolean;
  province?: string | null;
  city?: string | null;
  district?: string | null;
  street?: string | null;
  postal_code?: string | null;
  address_text?: string | null;
  latitude?: number | null;
  longitude?: number | null;
  phone?: string | null;
  service_tags?: string[];
}

/** POST and PATCH both answer with the location plus the gazetteer warnings. */
export interface LocationSaveResponse extends Location {
  warnings: string[];
}
