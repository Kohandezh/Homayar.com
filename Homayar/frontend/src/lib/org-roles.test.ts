import { describe, expect, it } from "vitest";
import {
  canManageOrgMember,
  delegatedWorkspaceRoles,
  grantableOrgRoles,
  orgPermissionsFor,
  orgRoleLabel,
} from "@/lib/org-roles";

const ALL = [
  "organization:read",
  "organization:member:read",
  "organization:member:manage",
  "organization:workspace:read",
  "organization:workspace:create",
  "organization:workspace:administer",
  "organization:analytics:read",
  "organization:records:read",
];

describe("orgPermissionsFor", () => {
  it("gives owners and administrators all eight permissions", () => {
    for (const role of ["owner", "admin"]) {
      expect([...orgPermissionsFor(role)].sort()).toEqual([...ALL].sort());
    }
  });

  it("gives an analyst reads, analytics and records only", () => {
    expect([...orgPermissionsFor("analyst")].sort()).toEqual(
      ["organization:analytics:read", "organization:read", "organization:records:read", "organization:workspace:read"],
    );
  });

  it("gives an executive no records and no member read", () => {
    expect([...orgPermissionsFor("executive")].sort()).toEqual(
      ["organization:analytics:read", "organization:read", "organization:workspace:read"],
    );
  });

  it("grants nothing for an unknown role, a prototype key, or no role", () => {
    for (const role of ["superuser", "constructor", "toString", "__proto__", "", null, undefined]) {
      expect(orgPermissionsFor(role).size).toBe(0);
    }
  });
});

describe("grantableOrgRoles", () => {
  it("lets an owner grant admin, analyst and executive, and an admin only analyst and executive", () => {
    expect(grantableOrgRoles("owner")).toEqual(["admin", "analyst", "executive"]);
    expect(grantableOrgRoles("admin")).toEqual(["analyst", "executive"]);
  });

  it("lets analysts, executives and unknown roles grant nothing", () => {
    for (const role of ["analyst", "executive", "constructor", null]) {
      expect(grantableOrgRoles(role)).toEqual([]);
    }
  });
});

describe("delegatedWorkspaceRoles", () => {
  it("lets owners and admins install admin, member and viewer, and nobody else anything", () => {
    expect(delegatedWorkspaceRoles("owner")).toEqual(["admin", "member", "viewer"]);
    expect(delegatedWorkspaceRoles("admin")).toEqual(["admin", "member", "viewer"]);
    expect(delegatedWorkspaceRoles("analyst")).toEqual([]);
    expect(delegatedWorkspaceRoles("constructor")).toEqual([]);
  });
});

describe("canManageOrgMember", () => {
  const target = { user_id: "user-2", role: "analyst", revoked_at: null };

  it("lets an owner manage an analyst, an executive and an admin", () => {
    for (const role of ["analyst", "executive", "admin"]) {
      expect(canManageOrgMember("owner", "user-1", { ...target, role })).toBe(true);
    }
  });

  it("never lets anyone manage themself or an owner", () => {
    expect(canManageOrgMember("owner", "user-2", target)).toBe(false);
    expect(canManageOrgMember("owner", "user-1", { ...target, role: "owner" })).toBe(false);
  });

  it("does not let an admin change another admin", () => {
    expect(canManageOrgMember("admin", "user-1", { ...target, role: "admin" })).toBe(false);
    expect(canManageOrgMember("admin", "user-1", target)).toBe(true);
  });

  it("offers nothing on a revoked row, nor to an analyst, an executive or an unknown role", () => {
    expect(canManageOrgMember("owner", "user-1", { ...target, revoked_at: "2026-10-01T00:00:00Z" })).toBe(false);
    for (const role of ["analyst", "executive", "constructor"]) {
      expect(canManageOrgMember(role, "user-1", { ...target, role: "executive" })).toBe(false);
    }
  });

  it("does not trust an unknown target role", () => {
    expect(canManageOrgMember("owner", "user-1", { ...target, role: "superuser" })).toBe(false);
  });
});

describe("orgRoleLabel", () => {
  it("names the four roles in both languages and shows an unknown role as sent", () => {
    expect(["owner", "admin", "analyst", "executive"].map((role) => orgRoleLabel(role, "en"))).toEqual([
      "Owner",
      "Administrator",
      "Analyst",
      "Executive",
    ]);
    expect(["owner", "admin", "analyst", "executive"].map((role) => orgRoleLabel(role, "fa"))).toEqual([
      "مالک",
      "مدیر",
      "تحلیلگر",
      "مدیر ارشد",
    ]);
    expect(orgRoleLabel("constructor", "en")).toBe("constructor");
  });
});
