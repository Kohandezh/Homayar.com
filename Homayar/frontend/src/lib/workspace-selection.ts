import type { AuthWorkspace } from "@/types/auth";

export interface WorkspaceSelection {
  selected: AuthWorkspace | null;
  /** The remembered workspace is no longer usable; the user must choose. */
  lost: boolean;
}

/**
 * Which workspace the console acts in, from the server's membership list.
 *
 * The remembered id only selects among workspaces the server listed; it never
 * grants access (the API re-checks X-Workspace-ID on every request). A
 * remembered workspace that disappeared, or that the API refused during this
 * session, is reported as lost instead of silently switching the user into
 * another tenant. Without a remembered choice, a single workspace is selected
 * and several require an explicit choice.
 */
export function resolveWorkspaceSelection(
  workspaces: readonly AuthWorkspace[],
  storedId: string | null,
  unavailable: ReadonlySet<string>,
): WorkspaceSelection {
  const usable = workspaces.filter((candidate) => !unavailable.has(candidate.id));
  if (storedId) {
    const remembered = usable.find((candidate) => candidate.id === storedId);
    return remembered ? { selected: remembered, lost: false } : { selected: null, lost: true };
  }
  return { selected: usable.length === 1 ? usable[0] : null, lost: false };
}
