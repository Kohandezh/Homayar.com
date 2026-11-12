import { describe, expect, it } from "vitest";
import { orgPermissionsFor } from "@/lib/org-roles";

const AUDIT_READ = "organization:audit:read";

const PREVIOUS = [
  "organization:read",
  "organization:member:read",
  "organization:member:manage",
  "organization:workspace:read",
  "organization:workspace:create",
  "organization:workspace:administer",
  "organization:analytics:read",
  "organization:records:read",
];

function permissions(role: string | null | undefined): string[] {
  return [...(orgPermissionsFor(role) as ReadonlySet<string>)].sort();
}

describe("HOM-243 C1: organization:audit:read in the console matrix", () => {
  it("gives owners and administrators the audit read permission", () => {
    for (const role of ["owner", "admin"]) {
      expect((orgPermissionsFor(role) as ReadonlySet<string>).has(AUDIT_READ)).toBe(true);
    }
  });

  it("gives owners and administrators exactly the previous eight plus audit read", () => {
    for (const role of ["owner", "admin"]) {
      expect(permissions(role)).toEqual([...PREVIOUS, AUDIT_READ].sort());
    }
  });

  it("keeps audit read away from analysts and executives", () => {
    for (const role of ["analyst", "executive"]) {
      expect((orgPermissionsFor(role) as ReadonlySet<string>).has(AUDIT_READ)).toBe(false);
    }
  });

  it("leaves the analyst with records read and the executive without it", () => {
    expect(permissions("analyst")).toEqual(
      ["organization:analytics:read", "organization:read", "organization:records:read", "organization:workspace:read"],
    );
    expect(permissions("executive")).toEqual(
      ["organization:analytics:read", "organization:read", "organization:workspace:read"],
    );
  });

  it("grants audit read to no other spelling of a role and to no role at all", () => {
    for (const role of ["OWNER", "Owner", " admin", "admin ", "administrator", "constructor", "__proto__", "", null, undefined]) {
      expect((orgPermissionsFor(role) as ReadonlySet<string>).has(AUDIT_READ)).toBe(false);
      expect(orgPermissionsFor(role).size).toBe(0);
    }
  });
});
