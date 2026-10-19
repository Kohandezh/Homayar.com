import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { renderHook } from "@testing-library/react";

import { AuthContext } from "@/lib/auth-context";
import { canOpenAdminEntry, useCan, type Can } from "@/lib/use-can";
import { FIXTURE_USER, FIXTURE_WORKSPACE, authContext } from "@/test/fixtures";
import type { AuthUser, AuthWorkspace } from "@/types/auth";

function canFor(user: AuthUser | null, workspace: AuthWorkspace | null = FIXTURE_WORKSPACE): Can {
  const value = authContext({ status: "authenticated", user, workspace, workspaces: workspace ? [workspace] : [] });
  function Wrapper({ children }: { children: ReactNode }) {
    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
  }
  return renderHook(() => useCan(), { wrapper: Wrapper }).result.current;
}

/** A user object as the server could send it, including values the type does not allow. */
function withSystemRole(systemRole: unknown): AuthUser {
  return { ...FIXTURE_USER, system_role: systemRole } as AuthUser;
}

const VOICE = { permission: "workspace:manage", operatorOnly: true } as const;
const SETTINGS = { permission: "workspace:manage" } as const;

describe("useCan().isOperator", () => {
  it("is true only for system_role exactly \"operator\"", () => {
    expect(canFor(withSystemRole("operator")).isOperator).toBe(true);
  });

  it.each([
    ["missing", FIXTURE_USER],
    ["user", withSystemRole("user")],
    ["Operator", withSystemRole("Operator")],
    ["operator with spaces", withSystemRole(" operator ")],
    ["null", withSystemRole(null)],
    ["true", withSystemRole(true)],
  ])("is false for %s", (_label, user) => {
    expect(canFor(user).isOperator).toBe(false);
  });

  it("is false without a user", () => {
    expect(canFor(null).isOperator).toBe(false);
  });
});

describe("canOpenAdminEntry", () => {
  it("opens an operator-only entry for an operator with the permission", () => {
    expect(canOpenAdminEntry(canFor(withSystemRole("operator")), VOICE)).toBe(true);
  });

  it("closes an operator-only entry for the same admin without the operator role", () => {
    expect(canOpenAdminEntry(canFor(withSystemRole("user")), VOICE)).toBe(false);
    expect(canOpenAdminEntry(canFor(FIXTURE_USER), VOICE)).toBe(false);
  });

  it("closes an operator-only entry for an operator whose workspace role lacks the permission", () => {
    const member = { ...FIXTURE_WORKSPACE, role: "member" } as const;
    expect(canOpenAdminEntry(canFor(withSystemRole("operator"), member), VOICE)).toBe(false);
    const admin = { ...FIXTURE_WORKSPACE, role: "admin" } as const;
    expect(canOpenAdminEntry(canFor(withSystemRole("operator"), admin), VOICE)).toBe(true);
  });

  it("needs only the permission for an entry that is not operator only", () => {
    expect(canOpenAdminEntry(canFor(FIXTURE_USER), SETTINGS)).toBe(true);
    expect(canOpenAdminEntry(canFor(FIXTURE_USER, { ...FIXTURE_WORKSPACE, role: "viewer" }), SETTINGS)).toBe(false);
  });
});
