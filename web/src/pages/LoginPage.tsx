import { ArrowRight, LockKeyhole } from "lucide-react";
import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useLocation, useNavigate } from "react-router";

import { useAuth } from "../auth/useAuth";
import { AuthRequestError } from "../auth/session";

interface LoginRouteState {
  from?: { pathname?: string; search?: string; hash?: string };
  expired?: boolean;
}

function safeReturnPath(from: LoginRouteState["from"]): string {
  const pathname = from?.pathname;
  if (!pathname || !pathname.startsWith("/") || pathname.startsWith("//") || pathname === "/login") {
    return "/home";
  }
  return `${pathname}${from.search ?? ""}${from.hash ?? ""}`;
}

function retryMessage(seconds: number): string {
  if (seconds >= 60) {
    const minutes = Math.ceil(seconds / 60);
    return `Too many sign-in attempts. Try again in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.`;
  }
  return `Too many sign-in attempts. Try again in ${seconds} ${seconds === 1 ? "second" : "seconds"}.`;
}

export function LoginPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, expired, signIn } = useAuth();
  const routeState = (location.state ?? {}) as LoginRouteState;
  const returnPath = safeReturnPath(routeState.from);
  const showExpired = Boolean(routeState.expired || expired);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) navigate(returnPath, { replace: true });
  }, [navigate, returnPath, user]);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await signIn(email, password);
      navigate(returnPath, { replace: true });
    } catch (cause) {
      if (cause instanceof AuthRequestError && cause.failure.status === 401) {
        setError("Email or password is incorrect.");
      } else if (
        cause instanceof AuthRequestError &&
        cause.failure.status === 429 &&
        cause.failure.retryAfterSeconds
      ) {
        setError(retryMessage(cause.failure.retryAfterSeconds));
      } else if (cause instanceof AuthRequestError && cause.failure.status === 429) {
        setError("Too many sign-in attempts. Please try again later.");
      } else {
        setError("Unable to sign in right now. Check your connection and try again.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="brand-mark login-mark" aria-hidden="true">R</div>
        <div className="login-eyebrow">RECONBENCH</div>
        <h1 id="login-title">Sign in to your workspace</h1>
        <p>Use your workspace account to continue.</p>
        {showExpired ? (
          <p className="session-expired-message" role="status">
            Your session expired or ended. Any jobs already running will continue on the server. Sign in to return to your page.
          </p>
        ) : null}
        <form onSubmit={(event) => void submit(event)}>
          <label className="login-field" htmlFor="login-email">
            Email address
            <input
              id="login-email"
              name="email"
              type="email"
              autoComplete="username"
              inputMode="email"
              value={email}
              onChange={(event) => setEmail(event.currentTarget.value)}
              required
              maxLength={320}
              autoFocus
            />
          </label>
          <label className="login-field" htmlFor="login-password">
            Password
            <input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.currentTarget.value)}
              required
              maxLength={1024}
            />
          </label>
          {error ? <p className="login-error" role="alert">{error}</p> : null}
          <button className="button button-primary login-submit" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        </form>
        <div className="login-secure"><LockKeyhole size={14} aria-hidden="true" /> Protected workspace access</div>
      </section>
    </main>
  );
}
