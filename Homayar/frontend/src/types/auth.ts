export interface AuthUser {
  id: string;
  email: string | null;
  phone_e164: string | null;
  display_name: string;
  locale: string;
  email_verified: boolean;
  phone_verified: boolean;
  is_active: boolean;
  role?: WorkspaceRole;
}

export type WorkspaceRole = "owner" | "admin" | "member" | "viewer";

export interface AuthWorkspace {
  id: string;
  organization_id: string;
  name: string;
  role: WorkspaceRole;
}

export interface AuthSession {
  user: Omit<AuthUser, "role">;
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
