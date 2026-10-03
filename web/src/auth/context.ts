import { createContext } from "react";
import type { AuthSession, Capability } from "./session";

export type SessionStatus = "loading" | "anonymous" | "authenticated";

export interface AuthContextValue {
  status: SessionStatus;
  user: AuthSession | null;
  expired: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  hasCapability: (capability: Capability) => boolean;
}

export const AuthContext = createContext<AuthContextValue | null>(null);
