import { toAsciiDigits } from "@/lib/format";
import type { Location, LocationCreateInput, LocationKind, LocationListParams, LocationUpdateInput } from "@/types/locations";

/** The three kinds the backend knows, in filter and form order. */
export const LOCATION_KINDS = ["branch", "dealer", "service_center"] as const;

const NAME_MAX_LENGTH = 200;
const CODE_PATTERN = /^[A-Za-z0-9_.-]+$/;
const CODE_MAX_LENGTH = 64;
const PHONE_MAX_LENGTH = 32;
const POSTAL_PATTERN = /^\d{10}$/;
const MAX_TAGS = 50;
/** A coordinate is a plain decimal number; no hex, exponent or infinity. */
const COORDINATE_PATTERN = /^[+-]?(\d+(\.\d*)?|\.\d+)$/;

/** The form as the drawer holds it: everything text, numbers kept as typed. */
export interface LocationFormValues {
  kind: LocationKind;
  name: string;
  code: string;
  province: string;
  city: string;
  district: string;
  street: string;
  postalCode: string;
  addressText: string;
  latitude: string;
  longitude: string;
  phone: string;
  serviceTags: string;
  isActive: boolean;
}

/** A failed rule, keyed by the message the console shows for it. */
export type LocationFieldError =
  | "name_required"
  | "name_too_long"
  | "code_required"
  | "code_invalid"
  | "coordinates_partial"
  | "latitude_invalid"
  | "longitude_invalid"
  | "postal_invalid"
  | "phone_too_long"
  | "too_many_tags";

export type LocationFormField = keyof Pick<
  LocationFormValues,
  "name" | "code" | "latitude" | "longitude" | "postalCode" | "phone" | "serviceTags"
>;

export type LocationFormErrors = Partial<Record<LocationFormField, LocationFieldError>>;

/** The filter row's state; the empty strings mean "no filter". */
export interface LocationsFilterState {
  kind: "" | LocationKind;
  status: "" | "active" | "inactive";
  city: string;
}

export const NO_LOCATIONS_FILTERS: LocationsFilterState = { kind: "", status: "", city: "" };

export function hasLocationFilters(filters: LocationsFilterState): boolean {
  return filters.kind !== "" || filters.status !== "" || filters.city.trim() !== "";
}

/** Only the filters that are set; no filter set asks for the whole list. */
export function toLocationParams(filters: LocationsFilterState): LocationListParams {
  const params: LocationListParams = {};
  if (filters.kind) params.kind = filters.kind;
  if (filters.status === "active") params.active = true;
  else if (filters.status === "inactive") params.active = false;
  const city = filters.city.trim();
  if (city) params.city = city;
  return params;
}

/** Persian (and Arabic-Indic) digits to ASCII, `٫` counting as the decimal point. */
function toCoordinateText(value: string): string {
  return toAsciiDigits(value.trim()).replace(/٫/g, ".");
}

/** The postal code as the server wants it: ASCII digits, no spaces or dashes. */
function toPostalText(value: string): string {
  return toAsciiDigits(value).replace(/[\s-]/g, "");
}

/** A phone number normalized to ASCII digits and signs before checking or sending. */
function toPhoneText(value: string): string {
  return toAsciiDigits(value.trim());
}

/** A latitude or longitude as a JSON number; null when blank or not a plain decimal. */
export function parseCoordinate(value: string): number | null {
  const text = toCoordinateText(value);
  return COORDINATE_PATTERN.test(text) ? Number(text) : null;
}

/** Split on both commas, trim, drop empties and duplicates (first order kept). */
export function parseServiceTags(raw: string): string[] {
  const seen: string[] = [];
  for (const part of raw.split(/[,،]/)) {
    const tag = part.trim();
    if (tag !== "" && !seen.includes(tag)) seen.push(tag);
  }
  return seen;
}

export function emptyLocationForm(): LocationFormValues {
  return {
    kind: "branch",
    name: "",
    code: "",
    province: "",
    city: "",
    district: "",
    street: "",
    postalCode: "",
    addressText: "",
    latitude: "",
    longitude: "",
    phone: "",
    serviceTags: "",
    isActive: true,
  };
}

/** The loaded location as form text, so an edit starts from what the server has. */
export function locationToForm(item: Location): LocationFormValues {
  return {
    kind: item.kind,
    name: item.name,
    code: item.code,
    province: item.province ?? "",
    city: item.city ?? "",
    district: item.district ?? "",
    street: item.street ?? "",
    postalCode: item.postal_code ?? "",
    addressText: item.address_text ?? "",
    latitude: item.latitude !== null ? String(item.latitude) : "",
    longitude: item.longitude !== null ? String(item.longitude) : "",
    phone: item.phone ?? "",
    serviceTags: item.service_tags.join(", "),
    isActive: item.is_active,
  };
}

export function validateLocationForm(values: LocationFormValues): LocationFormErrors {
  const errors: LocationFormErrors = {};

  const name = values.name.trim();
  if (name === "") errors.name = "name_required";
  else if (name.length > NAME_MAX_LENGTH) errors.name = "name_too_long";

  const code = values.code.trim();
  if (code === "") errors.code = "code_required";
  else if (code.length > CODE_MAX_LENGTH || !CODE_PATTERN.test(code)) errors.code = "code_invalid";

  // A raw value that is not the empty string counts as given, so a lone
  // space is a (failing) answer rather than no answer.
  const latitudeGiven = values.latitude !== "";
  const longitudeGiven = values.longitude !== "";
  const latitude = toCoordinateText(values.latitude);
  const longitude = toCoordinateText(values.longitude);
  if (latitudeGiven && !longitudeGiven) errors.longitude = "coordinates_partial";
  else if (longitudeGiven && !latitudeGiven) errors.latitude = "coordinates_partial";
  if (latitudeGiven) {
    if (!COORDINATE_PATTERN.test(latitude)) errors.latitude = "latitude_invalid";
    else {
      const parsed = Number(latitude);
      if (parsed < -90 || parsed > 90) errors.latitude = "latitude_invalid";
    }
  }
  if (longitudeGiven) {
    if (!COORDINATE_PATTERN.test(longitude)) errors.longitude = "longitude_invalid";
    else {
      const parsed = Number(longitude);
      if (parsed < -180 || parsed > 180) errors.longitude = "longitude_invalid";
    }
  }

  if (values.postalCode.trim() !== "" && !POSTAL_PATTERN.test(toPostalText(values.postalCode))) {
    errors.postalCode = "postal_invalid";
  }

  if (toPhoneText(values.phone).length > PHONE_MAX_LENGTH) errors.phone = "phone_too_long";

  if (parseServiceTags(values.serviceTags).length > MAX_TAGS) errors.serviceTags = "too_many_tags";

  return errors;
}

/** The POST body: the required four plus only the optional fields that are set. */
export function buildCreatePayload(values: LocationFormValues): LocationCreateInput {
  const payload: LocationCreateInput = {
    kind: values.kind,
    name: values.name.trim(),
    code: values.code.trim(),
    is_active: values.isActive,
  };

  const province = values.province.trim();
  if (province !== "") payload.province = province;
  const city = values.city.trim();
  if (city !== "") payload.city = city;
  const district = values.district.trim();
  if (district !== "") payload.district = district;
  const street = values.street.trim();
  if (street !== "") payload.street = street;
  const addressText = values.addressText.trim();
  if (addressText !== "") payload.address_text = addressText;

  const postalCode = toPostalText(values.postalCode);
  if (postalCode !== "") payload.postal_code = postalCode;
  const phone = toPhoneText(values.phone);
  if (phone !== "") payload.phone = phone;

  const latitude = parseCoordinate(values.latitude);
  const longitude = parseCoordinate(values.longitude);
  if (latitude !== null && longitude !== null) {
    payload.latitude = latitude;
    payload.longitude = longitude;
  }

  const serviceTags = parseServiceTags(values.serviceTags);
  if (serviceTags.length > 0) payload.service_tags = serviceTags;

  return payload;
}

/**
 * The PATCH body: only the fields whose value differs from the loaded
 * location. A cleared optional field is sent as null, a cleared tag list as
 * `[]`. Null when nothing differs, so the caller can skip the request.
 */
export function buildUpdatePayload(values: LocationFormValues, item: Location): LocationUpdateInput | null {
  const patch: LocationUpdateInput = {};
  let changed = false;

  if (values.kind !== item.kind) {
    patch.kind = values.kind;
    changed = true;
  }
  const name = values.name.trim();
  if (name !== item.name) {
    patch.name = name;
    changed = true;
  }
  const code = values.code.trim();
  if (code !== item.code) {
    patch.code = code;
    changed = true;
  }
  if (values.isActive !== item.is_active) {
    patch.is_active = values.isActive;
    changed = true;
  }

  const texts: Array<[string, string | null, keyof LocationUpdateInput]> = [
    [values.province.trim(), item.province, "province"],
    [values.city.trim(), item.city, "city"],
    [values.district.trim(), item.district, "district"],
    [values.street.trim(), item.street, "street"],
    [values.addressText.trim(), item.address_text, "address_text"],
  ];
  for (const [trimmed, stored, key] of texts) {
    if (trimmed === (stored ?? "")) continue;
    if (trimmed === "") {
      (patch[key] as string | null) = null;
    } else {
      (patch[key] as string) = trimmed;
    }
    changed = true;
  }

  const postalCode = toPostalText(values.postalCode);
  if (postalCode !== (item.postal_code ?? "")) {
    patch.postal_code = postalCode === "" ? null : postalCode;
    changed = true;
  }
  const phone = toPhoneText(values.phone);
  if (phone !== (item.phone ?? "")) {
    patch.phone = phone === "" ? null : phone;
    changed = true;
  }

  const latitude = parseCoordinate(values.latitude);
  if (latitude !== item.latitude) {
    patch.latitude = latitude;
    changed = true;
  }
  const longitude = parseCoordinate(values.longitude);
  if (longitude !== item.longitude) {
    patch.longitude = longitude;
    changed = true;
  }

  const serviceTags = parseServiceTags(values.serviceTags);
  if (serviceTags.length !== item.service_tags.length || serviceTags.some((tag, index) => tag !== item.service_tags[index])) {
    patch.service_tags = serviceTags;
    changed = true;
  }

  return changed ? patch : null;
}
