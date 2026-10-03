import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { AuthContext } from "./context";
import type { AuthContextValue, SessionStatus } from "./context";
import {
  fetchCurrentSession,
  SESSION_EXPIRED_EVENT,
  signInRequest,
  signOutRequest,
} from "./session";
import type { AuthSession } from "./session";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [user, setUser] = useState<AuthSession | null>(null);
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    let active = true;
    const onSessionExpired = () => {
      setUser(null);
      setStatus("anonymous");
      setExpired(true);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    void fetchCurrentSession()
      .then((session) => {
        if (!active) return;
        setUser(session);
        setStatus(session ? "authenticated" : "anonymous");
      })
      .catch(() => {
        if (!active) return;
        setUser(null);
        setStatus("anonymous");
      });

    return () => {
      active = false;
      window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      expired,
      signIn: async (email, password) => {
        const session = await signInRequest(email, password);
        setUser(session);
        setStatus("authenticated");
        setExpired(false);
      },
      signOut: async () => {
        const csrfToken = user?.csrf_token;
        try {
          if (csrfToken) await signOutRequest(csrfToken);
        } finally {
          // Clear the in-memory session even if the network is unavailable. The route
          // guard prevents cached protected screens from being shown after sign-out.
          setUser(null);
          setStatus("anonymous");
          setExpired(false);
        }
      },
      hasCapability: (capability) => user?.capabilities.includes(capability) ?? false,
    }),
    [expired, status, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
