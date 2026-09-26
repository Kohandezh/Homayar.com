import type { WorkspaceRole } from "@/types/auth";

const ROLE_LABELS: Record<"fa" | "en", Record<WorkspaceRole, string>> = {
  en: { owner: "Owner", admin: "Administrator", member: "Member", viewer: "Viewer" },
  fa: { owner: "مالک", admin: "مدیر", member: "عضو", viewer: "بیننده" },
};

export function roleLabel(role: WorkspaceRole, lang: "fa" | "en") {
  return ROLE_LABELS[lang][role] ?? role;
}

/**
 * Affordances derived from the workspace role using the product's
 * machine-checked authorization matrix (docs/security/authorization-matrix.md
 * at d1cc5d4). They only decide what the console offers; the API still
 * enforces every permission and its 403 is always handled (gap G4).
 */
export function roleCan(role: WorkspaceRole | undefined) {
  return {
    createMeeting: role === "owner" || role === "admin" || role === "member",
    updateMeeting: role === "owner" || role === "admin" || role === "member",
    deleteMeeting: role === "owner" || role === "admin",
    manageShares: role === "owner" || role === "admin" || role === "member",
    readBilling: role === "owner" || role === "admin",
    manageWorkspace: role === "owner" || role === "admin",
    readOriginalTranscript: role === "owner",
  };
}
