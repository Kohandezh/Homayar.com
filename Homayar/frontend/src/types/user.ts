export type UserRole = "admin" | "supervisor" | "agent";

export interface User {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  extension?: string;
  department?: string;
  phone_number?: string;
  is_active: boolean;
  auth_method: "local" | "ldap" | "voip";
  created_at?: string;
  last_login?: string;
}

export interface UserCreate {
  email: string;
  name: string;
  password: string;
  role: UserRole;
  extension?: string;
  department?: string;
  phone_number?: string;
}

export interface UserUpdate {
  name?: string;
  role?: UserRole;
  extension?: string;
  department?: string;
  phone_number?: string;
  is_active?: boolean;
}
