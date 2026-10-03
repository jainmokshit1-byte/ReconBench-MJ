import type { components } from "../api/generated";

export type AuthSession = components["schemas"]["AuthResponse"];
export type Capability = AuthSession["capabilities"][number];

export const SESSION_EXPIRED_EVENT = "reconbench:session-expired";
const AUTH_PATH = "/api/v1/auth";

export interface AuthFailure {
  status: number;
  code: string;
  retryAfterSeconds?: number;
}

export class AuthRequestError extends Error {
  readonly failure: AuthFailure;

  constructor(failure: AuthFailure) {
    super(failure.code);
    this.name = "AuthRequestError";
    this.failure = failure;
  }
}

function endpoint(path: string): string {
  const base = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");
  return `${base}${AUTH_PATH}${path}`;
}

export function notifySessionExpired(): void {
  window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
}

export async function fetchCurrentSession(): Promise<AuthSession | null> {
  const response = await fetch(endpoint("/me"), { credentials: "include" });
  if (response.status === 401) {
    if (response.headers.get("X-Session-Ended") === "true") notifySessionExpired();
    return null;
  }
  if (!response.ok) throw new AuthRequestError({ status: response.status, code: "session_unavailable" });
  return (await response.json()) as AuthSession;
}

export async function signInRequest(email: string, password: string): Promise<AuthSession> {
  const response = await fetch(endpoint("/login"), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new AuthRequestError({
      status: response.status,
      code: await problemCode(response),
      ...(response.status === 429 ? { retryAfterSeconds: retryAfter(response.headers.get("Retry-After")) } : {}),
    });
  }
  return (await response.json()) as AuthSession;
}

export async function signOutRequest(csrfToken: string): Promise<void> {
  const response = await fetch(endpoint("/logout"), {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken },
  });
  if (response.status === 401) notifySessionExpired();
  if (!response.ok && response.status !== 401) {
    throw new AuthRequestError({ status: response.status, code: await problemCode(response) });
  }
}

async function problemCode(response: Response): Promise<string> {
  try {
    const problem: unknown = await response.json();
    if (typeof problem === "object" && problem !== null && "code" in problem) {
      const code = problem.code;
      if (typeof code === "string") return code;
    }
  } catch {
    // Use the status-specific client message when the server response isn't JSON.
  }
  return "request_failed";
}

function retryAfter(value: string | null): number | undefined {
  if (!value || !/^\d+$/.test(value)) return undefined;
  const seconds = Number(value);
  return Number.isSafeInteger(seconds) && seconds > 0 ? seconds : undefined;
}
