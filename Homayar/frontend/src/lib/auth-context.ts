import { createContext, useContext } from "react";
import type { AuthUser, AuthWorkspace, LoginCredentials } from "@/types/auth";

/**
 * "unavailable": the session could not be read because the API did not
 * answer (network error, 5xx). That is not a sign-out: nothing is cleared and
 * the console offers to try again.
 */
export type AuthStatus = "loading" | "authenticated" | "anonymous" | "unavailable";

/** Why an authenticated session ended without the user signing out. */
export type SessionEndReason = "expired";

/** The remembered workspace could no longer be used; nothing was auto-selected. */
export interface WorkspaceNotice {
  kind: "access_lost";
  workspaceName: string | null;
}

export interface AuthContextValue {
  user: AuthUser | null;
  workspaces: AuthWorkspace[];
  workspace: AuthWorkspace | null;
  status: AuthStatus;
  endReason: SessionEndReason | null;
  workspaceNotice: WorkspaceNotice | null;
  /** Listed workspaces the API refused during this session. */
  unavailableWorkspaceIds: string[];
  signIn: (credentials: LoginCredentials) => Promise<AuthUser>;
  signOut: () => Promise<void>;
  refreshUser: () => Promise<AuthUser>;
  /** Reads the session again after "unavailable". */
  retrySession: () => void;
  setWorkspace: (workspaceId: string) => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used within AuthProvider");
  return context;
}
