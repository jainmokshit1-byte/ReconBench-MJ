import { notifySessionExpired } from "../auth/session";

export interface WorkspaceConfiguration {
  seq: number;
  body: Record<string, unknown>;
  engine_hash: string;
  rules_hash: string;
}

async function send(path: string, init: RequestInit = {}, csrf?: string): Promise<Response> {
  const base = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");
  const headers = new Headers(init.headers);
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(`${base}/api/v1${path}`, { ...init, headers, credentials: "include" });
  if (response.status === 401) notifySessionExpired();
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null);
    const detail = typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string" ? body.detail : `Request failed (${response.status})`;
    const error = new Error(detail) as Error & { status?: number; current?: unknown };
    error.status = response.status;
    if (typeof body === "object" && body !== null && "current" in body) error.current = body.current;
    throw error;
  }
  return response;
}

export async function getWorkspaceConfiguration(): Promise<{ value: WorkspaceConfiguration; etag: string }> {
  const response = await send("/config");
  return { value: await response.json() as WorkspaceConfiguration, etag: response.headers.get("ETag") ?? '"1"' };
}

export async function saveWorkspaceConfiguration(
  body: Record<string, unknown>, etag: string, csrf: string,
): Promise<{ value: WorkspaceConfiguration; etag: string }> {
  const response = await send("/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json", "If-Match": etag },
    body: JSON.stringify(body),
  }, csrf);
  return { value: await response.json() as WorkspaceConfiguration, etag: response.headers.get("ETag") ?? etag };
}

export async function getAdminErrors(): Promise<Record<string, unknown>> {
  return await (await send("/admin/errors")).json() as Record<string, unknown>;
}
