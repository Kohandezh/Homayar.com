import { describe, expect, it } from "vitest";

import {
  LOCATION_KINDS,
  buildCreatePayload,
  buildUpdatePayload,
  emptyLocationForm,
  hasLocationFilters,
  locationToForm,
  parseCoordinate,
  parseServiceTags,
  toLocationParams,
  validateLocationForm,
} from "@/components/admin/locations/location-model";
import type { Location } from "@/types/locations";

const T = "2026-10-18T12:00:00Z";

function location(overrides: Partial<Location> = {}): Location {
  return {
    id: "loc-1",
    workspace_id: "ws-1",
    kind: "branch",
    name: "Vanak branch",
    code: "vanak",
    province: "Tehran",
    city: "Tehran",
    district: "Vanak",
    street: "Valiasr",
    postal_code: "1234567890",
    address_text: "No 1, Valiasr St",
    latitude: 35.75,
    longitude: 51.41,
    phone: "+982112345678",
    is_active: true,
    service_tags: ["oil", "tires"],
    opening_hours: null,
    created_at: T,
    updated_at: T,
    ...overrides,
  };
}

function form(overrides: Partial<ReturnType<typeof emptyLocationForm>> = {}) {
  return { ...emptyLocationForm(), ...overrides };
}

describe("parseServiceTags", () => {
  it("splits on both commas, trims and drops empties and duplicates", () => {
    expect(parseServiceTags("oil, tires ، oil , ,wash،")).toEqual(["oil", "tires", "wash"]);
  });

  it("returns an empty list for blank input", () => {
    expect(parseServiceTags("   ")).toEqual([]);
  });
});

describe("toLocationParams", () => {
  it("sends nothing when no filter is set", () => {
    expect(toLocationParams({ kind: "", status: "", city: "" })).toEqual({});
    expect(hasLocationFilters({ kind: "", status: "", city: "" })).toBe(false);
  });

  it("maps kind, status and the trimmed city", () => {
    expect(toLocationParams({ kind: "dealer", status: "", city: "" })).toEqual({ kind: "dealer" });
    expect(toLocationParams({ kind: "", status: "active", city: "" })).toEqual({ active: true });
    expect(toLocationParams({ kind: "", status: "inactive", city: "" })).toEqual({ active: false });
    expect(toLocationParams({ kind: "service_center", status: "inactive", city: "  Shiraz " })).toEqual({
      kind: "service_center",
      active: false,
      city: "Shiraz",
    });
    expect(hasLocationFilters({ kind: "", status: "", city: "x" })).toBe(true);
  });
});

describe("validateLocationForm", () => {
  it("accepts a complete form and the bare minimum", () => {
    expect(validateLocationForm(form({ name: "A", code: "a" }))).toEqual({});
    expect(validateLocationForm(form({ name: "A", code: "a", latitude: "-90", longitude: "180" }))).toEqual({});
  });

  it("requires the name", () => {
    expect(validateLocationForm(form({ name: "  ", code: "a" }))).toEqual({ name: "name_required" });
    expect(validateLocationForm(form({ name: "x".repeat(201), code: "a" }))).toEqual({ name: "name_too_long" });
  });

  it("requires a well-formed code", () => {
    expect(validateLocationForm(form({ name: "A", code: " " }))).toEqual({ code: "code_required" });
    expect(validateLocationForm(form({ name: "A", code: "bad code!" }))).toEqual({ code: "code_invalid" });
    expect(validateLocationForm(form({ name: "A", code: "x".repeat(65) }))).toEqual({ code: "code_invalid" });
  });

  it("refuses one coordinate alone, pointing at the empty one", () => {
    expect(validateLocationForm(form({ name: "A", code: "a", latitude: "35.7" }))).toEqual({
      longitude: "coordinates_partial",
    });
    expect(validateLocationForm(form({ name: "A", code: "a", longitude: "51.4" }))).toEqual({
      latitude: "coordinates_partial",
    });
  });

  it("checks the coordinate ranges", () => {
    expect(validateLocationForm(form({ name: "A", code: "a", latitude: "abc", longitude: "0" }))).toEqual({
      latitude: "latitude_invalid",
    });
    expect(validateLocationForm(form({ name: "A", code: "a", latitude: "90.1", longitude: "0" }))).toEqual({
      latitude: "latitude_invalid",
    });
    expect(validateLocationForm(form({ name: "A", code: "a", latitude: "0", longitude: "181" }))).toEqual({
      longitude: "longitude_invalid",
    });
  });

  it("accepts only a plain decimal number as a coordinate", () => {
    for (const bad of ["0x1F", "1e1", "Infinity", " "]) {
      expect(validateLocationForm(form({ name: "A", code: "a", latitude: bad, longitude: "2.35" }))).toEqual({
        latitude: "latitude_invalid",
      });
      expect(parseCoordinate(bad)).toBeNull();
    }
    // Parsing follows the same rule, so such a value never reaches a payload.
    expect(buildCreatePayload(form({ name: "A", code: "a", latitude: "1e1", longitude: "2.35" }))).toEqual({
      kind: "branch",
      name: "A",
      code: "a",
      is_active: true,
    });
  });

  it("wants ten postal digits, ignoring spaces and dashes", () => {
    expect(validateLocationForm(form({ name: "A", code: "a", postalCode: "12345" }))).toEqual({
      postalCode: "postal_invalid",
    });
    expect(validateLocationForm(form({ name: "A", code: "a", postalCode: "12345 67890" }))).toEqual({});
    expect(validateLocationForm(form({ name: "A", code: "a", postalCode: "1234-567-890" }))).toEqual({});
  });

  it("caps the phone length", () => {
    expect(validateLocationForm(form({ name: "A", code: "a", phone: "x".repeat(33) }))).toEqual({
      phone: "phone_too_long",
    });
    expect(validateLocationForm(form({ name: "A", code: "a", phone: "x".repeat(32) }))).toEqual({});
  });

  it("caps the tag count", () => {
    const tags = Array.from({ length: 51 }, (_, index) => `t${index}`).join(",");
    expect(validateLocationForm(form({ name: "A", code: "a", serviceTags: tags }))).toEqual({
      serviceTags: "too_many_tags",
    });
  });

  it("accepts Persian and Arabic-Indic digits everywhere they apply", () => {
    expect(
      validateLocationForm(
        form({ name: "A", code: "a", latitude: "۴۸٫۸۵", longitude: "٢٫٣٥", postalCode: "۱۲۳۴۵۶۷۸۹۰", phone: "۰۹۱۲۳۴۵۶۷۸" }),
      ),
    ).toEqual({});
  });
});

describe("buildCreatePayload", () => {
  it("sends only the required fields when nothing else is filled", () => {
    expect(buildCreatePayload(form({ name: " North ", code: " north-1 " }))).toEqual({
      kind: "branch",
      name: "North",
      code: "north-1",
      is_active: true,
    });
  });

  it("sends every set field, with numbers as numbers and tags deduped", () => {
    expect(
      buildCreatePayload(
        form({
          kind: "dealer",
          name: "North",
          code: "north-1",
          province: " Tehran ",
          city: "Tehran",
          district: "Vanak",
          street: "Valiasr",
          postalCode: "12345 67890",
          addressText: "No 1",
          latitude: "48.85",
          longitude: "2.35",
          phone: "+98211",
          serviceTags: "oil, oil, wash",
          isActive: false,
        }),
      ),
    ).toEqual({
      kind: "dealer",
      name: "North",
      code: "north-1",
      is_active: false,
      province: "Tehran",
      city: "Tehran",
      district: "Vanak",
      street: "Valiasr",
      postal_code: "1234567890",
      address_text: "No 1",
      latitude: 48.85,
      longitude: 2.35,
      phone: "+98211",
      service_tags: ["oil", "wash"],
    });
  });

  it("converts Persian digits before sending", () => {
    expect(
      buildCreatePayload(form({ name: "A", code: "a", latitude: "۴۸٫۸۵", longitude: "٢٫٣٥", postalCode: "۱۲۳۴۵۶۷۸۹۰", phone: "۰۹۱۲" })),
    ).toEqual({
      kind: "branch",
      name: "A",
      code: "a",
      is_active: true,
      latitude: 48.85,
      longitude: 2.35,
      postal_code: "1234567890",
      phone: "0912",
    });
  });
});

describe("buildUpdatePayload", () => {
  it("returns null when nothing differs", () => {
    expect(buildUpdatePayload(locationToForm(location()), location())).toBeNull();
  });

  it("sends only the changed scalar fields", () => {
    expect(buildUpdatePayload(locationToForm(location()), location({ name: "Old name", is_active: false }))).toEqual({
      name: "Vanak branch",
      is_active: true,
    });
  });

  it("sends a cleared optional field as null", () => {
    const values = { ...locationToForm(location()), city: "  ", phone: "" };
    expect(buildUpdatePayload(values, location())).toEqual({ city: null, phone: null });
  });

  it("sends a filled optional field that was null", () => {
    const stored = location({ district: null, latitude: null, longitude: null });
    const values = { ...locationToForm(stored), district: "Center", latitude: "35.7", longitude: "51.4" };
    expect(buildUpdatePayload(values, stored)).toEqual({ district: "Center", latitude: 35.7, longitude: 51.4 });
  });

  it("clears both coordinates to null when both are emptied", () => {
    const values = { ...locationToForm(location()), latitude: "", longitude: "" };
    expect(buildUpdatePayload(values, location())).toEqual({ latitude: null, longitude: null });
  });

  it("compares tags as arrays and sends an empty array when cleared", () => {
    expect(buildUpdatePayload({ ...locationToForm(location()), serviceTags: "wash" }, location())).toEqual({
      service_tags: ["wash"],
    });
    expect(buildUpdatePayload({ ...locationToForm(location()), serviceTags: " , " }, location())).toEqual({
      service_tags: [],
    });
    // The same list reordered is a change; the same set is not.
    expect(buildUpdatePayload({ ...locationToForm(location()), serviceTags: "tires, oil" }, location())).toEqual({
      service_tags: ["tires", "oil"],
    });
  });

  it("sends the kind and the code when they change", () => {
    const values = { ...locationToForm(location()), kind: "service_center" as const, code: "vanak-2" };
    expect(buildUpdatePayload(values, location())).toEqual({ kind: "service_center", code: "vanak-2" });
  });
});

describe("emptyLocationForm and locationToForm", () => {
  it("starts from a branch with the switch on", () => {
    expect(emptyLocationForm()).toEqual({
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
    });
  });

  it("prefills from the loaded location", () => {
    const values = locationToForm(location({ latitude: null, longitude: null, service_tags: [] }));
    expect(values.name).toBe("Vanak branch");
    expect(values.latitude).toBe("");
    expect(values.serviceTags).toBe("");
    expect(locationToForm(location()).latitude).toBe("35.75");
    expect(locationToForm(location()).serviceTags).toBe("oil, tires");
  });

  it("lists the three kinds", () => {
    expect(LOCATION_KINDS).toEqual(["branch", "dealer", "service_center"]);
  });
});
