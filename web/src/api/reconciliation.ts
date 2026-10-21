import { notifySessionExpired } from "../auth/session";

export interface CaseSummary {
  case_id: string;
  run_id: string;
  invoice_id: string;
  vendor_id: string | null;
  invoice_date: string | null;
  invoice_no: string | null;
  canonical_doc_no: string | null;
  computed_status: string;
  overlay: string | null;
  effective_status: string;
  severity_rank: number;
  human_touched: boolean;
  version: number;
  reason_codes: string[];
  po_nos: string[];
  grn_nos: string[];
  invoice_taxable: string | null;
  invoice_total: string | null;
  expected_taxable: string | null;
  variance: string | null;
  at_risk: string | null;
  is_stale: boolean;
}

export interface CasesPage {
  items: CaseSummary[];
  next_cursor: string | null;
  limit: number;
  total_count?: number;
}

export interface RunSummary {
  id: string;
  status: string;
  done_cases: number;
  total_cases: number;
  status_counts: Record<string, number>;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  metrics: Record<string, unknown>;
}

export interface CaseDetailResponse {
  case: Record<string, unknown> & {
    id: string;
    effective_status: string;
    overlay: string | null;
    etag_version: number;
  };
  lines: Array<Record<string, unknown>>;
  documents: Array<Record<string, unknown>>;
  history: Array<Record<string, unknown>>;
}

const apiBase = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function request(path: string, init: RequestInit = {}, csrfToken?: string): Promise<Response> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const response = await fetch(`${apiBase}/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  });
  if (response.status === 401) notifySessionExpired();
  if (!response.ok && response.status !== 304) {
    let message = `Request failed (${response.status})`;
    let current: unknown;
    try {
      const body: unknown = await response.json();
      if (isRecord(body)) {
        if (typeof body["detail"] === "string") message = body["detail"];
        else if (typeof body["title"] === "string") message = body["title"];
        current = body["current"];
      }
    } catch {
      // Keep the HTTP status if an intermediary returned a non-JSON error.
    }
    const error = new Error(message) as Error & { status?: number; current?: unknown };
    error.status = response.status;
    error.current = current;
    throw error;
  }
  return response;
}

async function parseJson<T>(response: Response): Promise<T> {
  return response.json() as Promise<T>;
}

export async function createReconciliationRun(
  csrfToken: string,
  scope: { kind: "all" } | { kind: "vendor"; vendor_id: string },
): Promise<{ id: string }> {
  const response = await request(
    "/runs",
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ scope }),
    },
    csrfToken,
  );
  return parseJson(response);
}

export async function getReconciliationRun(runId: string): Promise<RunSummary> {
  return parseJson(await request(`/runs/${encodeURIComponent(runId)}`));
}

export async function listRecentReconciliationRuns(): Promise<RunSummary[]> {
  return parseJson(await request("/runs?limit=10"));
}

export interface ExportSummary {
  id: string;
  run_id: string;
  status: "queued" | "building" | "ready" | "failed" | "expired";
  error_code: string | null;
  error_message: string | null;
  version: number;
  expires_at: string | null;
}

export async function createReconciliationExport(
  runId: string,
  filters: { vendor_id?: string; status?: string[]; from?: string; to?: string },
  csrfToken: string,
): Promise<{ id: string; status: string }> {
  const response = await request(
    "/exports",
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ run_id: runId, filter: filters, scope: "filtered" }),
    },
    csrfToken,
  );
  return parseJson(response);
}

export async function getReconciliationExport(exportId: string): Promise<ExportSummary> {
  return parseJson(await request(`/exports/${encodeURIComponent(exportId)}`));
}

export async function retryReconciliationExport(exportId: string, version: number, csrfToken: string): Promise<void> {
  await request(
    `/exports/${encodeURIComponent(exportId)}/retry`,
    { method: "POST", headers: { "If-Match": `"${version}"` } },
    csrfToken,
  );
}

export async function downloadReconciliationExport(exportId: string): Promise<void> {
  const response = await request(`/exports/${encodeURIComponent(exportId)}/download`);
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = `reconbench-${exportId}.xlsx`;
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

export async function listReconciliationCases(filters: {
  vendorId?: string | null;
  /** Only the cases this run evaluated. */
  runId?: string | null;
  status?: string | null;
  from?: string | null;
  to?: string | null;
  cursor?: string | null;
}): Promise<CasesPage> {
  const params = new URLSearchParams();
  if (filters.vendorId) params.set("vendor_id", filters.vendorId);
  if (filters.runId) params.set("run_id", filters.runId);
  if (filters.status) params.append("status", filters.status);
  if (filters.from) params.set("from", filters.from);
  if (filters.to) params.set("to", filters.to);
  params.set("limit", "100");
  if (filters.cursor) params.set("cursor", filters.cursor);
  return parseJson(await request(`/cases?${params.toString()}`));
}

export async function getReconciliationCase(caseId: string): Promise<{
  value: CaseDetailResponse;
  etag: string;
}> {
  const response = await request(`/cases/${encodeURIComponent(caseId)}`);
  return { value: await parseJson(response), etag: response.headers.get("ETag") ?? '"1"' };
}

export async function getReconciliationKpis(from: string, to: string): Promise<Record<string, unknown>> {
  const params = new URLSearchParams({ from, to });
  return parseJson(await request(`/kpis?${params.toString()}`));
}

export type WorkflowStepKey = "ingest" | "map" | "reconcile" | "export";

export interface WorkflowStep {
  key: WorkflowStepKey;
  complete: boolean;
  parsed_documents?: number;
  files_in_progress?: number;
  unassigned_documents?: number;
  suggested_links?: number;
  current_cases?: number;
  stale_cases?: number;
}

export interface WorkflowStatus {
  from: string;
  to: string;
  steps: WorkflowStep[];
  next_step: WorkflowStepKey | null;
  run_id: string | null;
  active_run_id: string | null;
}

export async function getWorkflowStatus(from: string, to: string): Promise<WorkflowStatus> {
  const params = new URLSearchParams({ from, to });
  return parseJson(await request(`/workflow-status?${params.toString()}`));
}

export async function addCaseAction(
  caseId: string,
  etag: string,
  action: "approve" | "hold" | "reject" | "note" | "release",
  csrfToken: string,
  value?: string,
): Promise<void> {
  await request(
    `/cases/${encodeURIComponent(caseId)}/actions`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "If-Match": etag },
      body: JSON.stringify(action === "note" ? { action, note: value } : { action, reason: value }),
    },
    csrfToken,
  );
}

export async function bulkCaseAction(
  items: Array<{ case_id: string; expected_version: number }>,
  action: "approve" | "hold" | "reject",
  reason: string,
  csrfToken: string,
): Promise<{ items: Array<Record<string, unknown>> }> {
  return parseJson(await request(
    "/cases/bulk-actions",
    {
      method: "POST",
      headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
      body: JSON.stringify({ items: items.map((item) => ({ ...item, action, reason })) }),
    },
    csrfToken,
  ));
}

export async function runManualReconciliation(
  invoiceId: string,
  poIds: string[],
  grnIds: string[],
  csrfToken: string,
): Promise<Record<string, unknown>> {
  return parseJson(await request(
    "/manual-recon",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invoice_id: invoiceId, po_ids: poIds, grn_ids: grnIds }),
    },
    csrfToken,
  ));
}
