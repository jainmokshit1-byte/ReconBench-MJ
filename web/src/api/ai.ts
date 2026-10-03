export interface AiGuardrailReport {
  passed: boolean;
  checks: Record<string, boolean>;
  provider: "stub";
  external_provider_called: false;
  financial_verdict_changed: false;
  status_code: string;
}

const apiBase = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");

export async function runLocalAiGuardrailTest(csrfToken: string): Promise<AiGuardrailReport> {
  const response = await fetch(`${apiBase}/api/v1/ai/test`, {
    method: "POST",
    credentials: "include",
    headers: { "X-CSRF-Token": csrfToken },
  });
  if (!response.ok) {
    let message = `AI guardrail test failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body && typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // Keep the status based message when an intermediary returns non-JSON.
    }
    throw new Error(message);
  }
  return (await response.json()) as AiGuardrailReport;
}
