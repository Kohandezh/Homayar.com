import { describe, expect, it } from "vitest";
import { ROLE_PERMISSIONS, permissionsFor, roleCan, roleLabel, workspaceCan, type Permission } from "@/lib/roles";
import type { AuthWorkspace, WorkspaceRole } from "@/types/auth";

// Copy of backend/security/authorization.py:87-125, written out by hand so a
// drift in either direction fails here.
const MATRIX: Record<Permission, WorkspaceRole[]> = {
  "meeting:read": ["owner", "admin", "member", "viewer"],
  "meeting:create": ["owner", "admin", "member"],
  "meeting:update": ["owner", "admin", "member"],
  "meeting:delete": ["owner", "admin"],
  "share:manage": ["owner", "admin", "member"],
  "member:read": ["owner", "admin", "member", "viewer"],
  "member:manage": ["owner", "admin"],
  "team:read": ["owner", "admin", "member", "viewer"],
  "team:manage": ["owner", "admin"],
  "workspace:manage": ["owner", "admin"],
  "billing:read": ["owner", "admin"],
  "billing:manage": ["owner"],
  "audit:read": ["owner", "admin"],
  "api_key:manage": ["owner", "admin"],
  "legal_hold:manage": ["owner"],
  "pii:original_access": ["owner"],
  "ai_policy:manage": ["owner"],
  "scorecard:read": ["owner", "admin", "member", "viewer"],
  "scorecard:manage": ["owner", "admin"],
};

function workspace(role: WorkspaceRole, permissions?: unknown): AuthWorkspace {
  return { id: "ws-1", organization_id: "org-1", name: "Workspace", role, permissions: permissions as string[] | undefined };
}

describe("ROLE_PERMISSIONS", () => {
  it("matches the backend authorization matrix exactly", () => {
    for (const role of ["owner", "admin", "member", "viewer"] as const) {
      const expected = (Object.keys(MATRIX) as Permission[]).filter((permission) => MATRIX[permission].includes(role));
      expect([...ROLE_PERMISSIONS[role]].sort()).toEqual(expected.sort());
    }
  });
});

describe("permissionsFor", () => {
  it("gives nothing without a workspace", () => {
    expect(permissionsFor(null).size).toBe(0);
    expect(permissionsFor(undefined).size).toBe(0);
  });

  it("falls back to the role map when permissions are absent", () => {
    expect([...permissionsFor(workspace("viewer"))].sort()).toEqual(["meeting:read", "member:read", "scorecard:read", "team:read"]);
    expect(permissionsFor(workspace("admin")).has("workspace:manage")).toBe(true);
    expect(permissionsFor(workspace("admin")).has("pii:original_access")).toBe(false);
  });

  it("uses a present permissions array as the only source, even against the role", () => {
    const owner = permissionsFor(workspace("owner", ["meeting:read", "member:read"]));
    expect([...owner].sort()).toEqual(["meeting:read", "member:read"]);
    expect(owner.has("workspace:manage")).toBe(false);
    expect(permissionsFor(workspace("viewer", ["workspace:manage"])).has("workspace:manage")).toBe(true);
  });

  it("grants nothing for an empty array", () => {
    expect(permissionsFor(workspace("owner", [])).size).toBe(0);
  });

  it("ignores unknown strings and keeps the known ones next to them", () => {
    const set = permissionsFor(workspace("viewer", ["admin:*", "workspace:manage", "", "MEMBER:READ"]));
    expect([...set]).toEqual(["workspace:manage"]);
  });

  it("fails closed for a permissions value that is not an array", () => {
    expect(permissionsFor(workspace("owner", null)).size).toBe(0);
    expect(permissionsFor(workspace("owner", "workspace:manage")).size).toBe(0);
    expect(permissionsFor(workspace("owner", { 0: "workspace:manage" })).size).toBe(0);
  });

  it("gives nothing for an unknown role without permissions", () => {
    expect(permissionsFor(workspace("operator" as WorkspaceRole)).size).toBe(0);
  });

  it.each(["constructor", "__proto__", "toString"])("gives nothing, without throwing, for the role %s", (role) => {
    expect(permissionsFor(workspace(role as WorkspaceRole)).size).toBe(0);
  });
});

describe("prototype keys as roles", () => {
  it.each(["constructor", "__proto__", "toString"])("%s grants no capability and keeps its raw label", (role) => {
    expect(Object.values(roleCan(role as WorkspaceRole)).some(Boolean)).toBe(false);
    expect(Object.values(workspaceCan(workspace(role as WorkspaceRole))).some(Boolean)).toBe(false);
    expect(roleLabel(role as WorkspaceRole, "en")).toBe(role);
    expect(roleLabel(role as WorkspaceRole, "fa")).toBe(role);
  });

  it("still labels the known roles", () => {
    expect(roleLabel("owner", "en")).toBe("Owner");
    expect(roleLabel("viewer", "fa")).toBe("بیننده");
  });
});

describe("roleCan and workspaceCan", () => {
  it("keep the capability shape, derived from the matrix", () => {
    expect(roleCan("owner")).toEqual({
      createMeeting: true,
      updateMeeting: true,
      deleteMeeting: true,
      manageShares: true,
      readBilling: true,
      manageWorkspace: true,
      readOriginalTranscript: true,
    });
    expect(roleCan("admin")).toMatchObject({ deleteMeeting: true, readBilling: true, readOriginalTranscript: false });
    expect(roleCan("member")).toMatchObject({ createMeeting: true, manageShares: true, deleteMeeting: false, readBilling: false });
    expect(roleCan("viewer")).toMatchObject({ createMeeting: false, updateMeeting: false, manageShares: false });
    expect(Object.values(roleCan(undefined)).some(Boolean)).toBe(false);
  });

  it("read the workspace permissions when present", () => {
    expect(workspaceCan(workspace("owner", ["meeting:read"])).createMeeting).toBe(false);
    expect(workspaceCan(workspace("viewer", ["pii:original_access"])).readOriginalTranscript).toBe(true);
    expect(workspaceCan(workspace("member")).createMeeting).toBe(true);
    expect(Object.values(workspaceCan(null)).some(Boolean)).toBe(false);
  });
});
