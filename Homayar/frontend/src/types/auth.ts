export interface AuthUser {
  id: string;
  email: string | null;
  phone_e164: string | null;
  display_name: string;
  locale: string;
  email_verified: boolean;
  phone_verified: boolean;
  is_active: boolean;
  /** Platform-level role (HOM-184), separate from the workspace role. */
  system_role?: "user" | "operator";
}

export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

export interface AuthWorkspace {
  id: string;
  organization_id: string;
  name: string;
  role: WorkspaceRole;
  /**
   * Permission strings of this membership (HOM-184). When present, the only
   * source of truth for the workspace; when absent, the role decides.
   */
  permissions?: string[];
}

export interface AuthSession {
  user: AuthUser;
  workspaces: AuthWorkspace[];
  access_expires_in: number;
  requires_verification: boolean;
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface RegistrationDetails {
  name: string;
  email: string;
  password: string;
  /** Account language; the API accepts "fa" (default) or "en". */
  locale?: "fa" | "en";
}

export interface OtpChallenge {
  challenge_id: string;
  expires_in: number;
  resend_after: number;
}
