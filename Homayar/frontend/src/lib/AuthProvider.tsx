import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AUTH_EXPIRED_EVENT,
  WORKSPACE_DENIED_EVENT,
  WORKSPACE_STORAGE_KEY,
  type WorkspaceDeniedDetail,
} from "@/lib/api";
import { apiErrorInfo } from "@/lib/api-errors";
import { getCurrentSession, loginSession, logout } from "@/lib/auth";
import {
  AuthContext,
  type AuthStatus,
  type SessionEndReason,
  type WorkspaceNotice,
} from "@/lib/auth-context";
import { resolveWorkspaceSelection } from "@/lib/workspace-selection";
import type { AuthSession, AuthUser, AuthWorkspace, LoginCredentials } from "@/types/auth";

/** The API answered that there is no valid session (as opposed to not answering). */
function sessionEnded(error: unknown) {
  const info = apiErrorInfo(error);
  return info.status === 401 || info.code === "authentication_required";
}

export default function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<AuthUser | null>(null);
  const [workspaces, setWorkspaces] = useState<AuthWorkspace[]>([]);
  const [workspace, setActiveWorkspace] = useState<AuthWorkspace | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [endReason, setEndReason] = useState<SessionEndReason | null>(null);
  const [workspaceNotice, setWorkspaceNotice] = useState<WorkspaceNotice | null>(null);
  const [unavailableWorkspaceIds, setUnavailableWorkspaceIds] = useState<string[]>([]);
  const authenticated = useRef(false);
  const active = useRef<AuthWorkspace | null>(null);
  const unavailable = useRef<Set<string>>(new Set());
  const sessionRefresh = useRef<Promise<void> | null>(null);
  const lastPermissionRefresh = useRef(0);

  const markAnonymous = useCallback(() => {
    authenticated.current = false;
    active.current = null;
    unavailable.current = new Set();
    queryClient.clear();
    window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
    setUser(null);
    setWorkspaces([]);
    setActiveWorkspace(null);
    setWorkspaceNotice(null);
    setUnavailableWorkspaceIds([]);
    setStatus("anonymous");
  }, [queryClient]);

  const applySession = useCallback((session: AuthSession) => {
    const storedId = window.localStorage.getItem(WORKSPACE_STORAGE_KEY);
    const { selected, lost } = resolveWorkspaceSelection(session.workspaces, storedId, unavailable.current);
    const previous = active.current;
    if (lost) {
      const name = previous?.id === storedId
        ? previous.name
        : session.workspaces.find((candidate) => candidate.id === storedId)?.name ?? null;
      setWorkspaceNotice({ kind: "access_lost", workspaceName: name });
    }
    // Content of a workspace the user left (or lost) must not stay in memory.
    if (previous && previous.id !== selected?.id) queryClient.clear();
    if (selected) window.localStorage.setItem(WORKSPACE_STORAGE_KEY, selected.id);
    else window.localStorage.removeItem(WORKSPACE_STORAGE_KEY);
    active.current = selected;
    setWorkspaces(session.workspaces);
    setActiveWorkspace(selected);
    const currentUser: AuthUser = session.user;
    setUser(currentUser);
    setStatus("authenticated");
    setEndReason(null);
    authenticated.current = true;
    return currentUser;
  }, [queryClient]);

  const refreshUser = useCallback(async () => {
    try {
      return applySession(await getCurrentSession());
    } catch (error) {
      // Only an answer that the session is gone signs the console out; an
      // outage (network, 5xx) leaves the current session as it is.
      if (sessionEnded(error)) markAnonymous();
      throw error;
    }
  }, [applySession, markAnonymous]);

  const [bootAttempt, setBootAttempt] = useState(0);
  const retrySession = useCallback(() => {
    setStatus("loading");
    setBootAttempt((attempt) => attempt + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;

    void getCurrentSession()
      .then((session) => {
        if (cancelled) return;
        applySession(session);
      })
      .catch((error) => {
        if (cancelled) return;
        if (sessionEnded(error)) markAnonymous();
        else setStatus("unavailable");
      });

    return () => {
      cancelled = true;
    };
  }, [applySession, markAnonymous, bootAttempt]);

  useEffect(() => {
    // The event also fires for an anonymous first visit (the initial /auth/me
    // is refused and the refresh fails); only a live session "expires".
    function handleExpired() {
      if (authenticated.current) setEndReason("expired");
      markAnonymous();
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleExpired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleExpired);
  }, [markAnonymous]);

  useEffect(() => {
    // A 403 can mean the membership was removed, expired or reduced while the
    // session was open: re-read the server's view instead of guessing, and
    // never move the user into another workspace on their behalf.
    function handleDenied(event: Event) {
      if (!authenticated.current) return;
      const detail = (event as CustomEvent<WorkspaceDeniedDetail>).detail;
      if (detail.code === "permission_denied") {
        const now = Date.now();
        if (now - lastPermissionRefresh.current < 30_000) return;
        lastPermissionRefresh.current = now;
      } else if (detail.workspaceId && !unavailable.current.has(detail.workspaceId)) {
        unavailable.current = new Set(unavailable.current).add(detail.workspaceId);
        setUnavailableWorkspaceIds([...unavailable.current]);
      }
      sessionRefresh.current ??= getCurrentSession()
        .then((session) => {
          if (authenticated.current) applySession(session);
        })
        .catch(() => undefined) // a 401 here is handled as an expired session
        .finally(() => {
          sessionRefresh.current = null;
        });
    }
    window.addEventListener(WORKSPACE_DENIED_EVENT, handleDenied);
    return () => window.removeEventListener(WORKSPACE_DENIED_EVENT, handleDenied);
  }, [applySession]);

  const signIn = useCallback(
    async (credentials: LoginCredentials) => {
      const session = await loginSession(credentials);
      queryClient.clear();
      return applySession(session);
    },
    [applySession, queryClient]
  );

  const setWorkspace = useCallback(
    (workspaceId: string) => {
      const selected = workspaces.find((candidate) => candidate.id === workspaceId);
      if (!selected || unavailable.current.has(selected.id)) return;
      window.localStorage.setItem(WORKSPACE_STORAGE_KEY, selected.id);
      active.current = selected;
      setActiveWorkspace(selected);
      setWorkspaceNotice(null);
      queryClient.clear();
    },
    [queryClient, workspaces]
  );

  const signOut = useCallback(async () => {
    await logout();
    setEndReason(null);
    markAnonymous();
  }, [markAnonymous]);

  const value = useMemo(
    () => ({
      user,
      workspaces,
      workspace,
      status,
      endReason,
      workspaceNotice,
      unavailableWorkspaceIds,
      signIn,
      signOut,
      refreshUser,
      retrySession,
      setWorkspace,
    }),
    [
      endReason,
      refreshUser,
      retrySession,
      setWorkspace,
      signIn,
      signOut,
      status,
      unavailableWorkspaceIds,
      user,
      workspace,
      workspaceNotice,
      workspaces,
    ]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
