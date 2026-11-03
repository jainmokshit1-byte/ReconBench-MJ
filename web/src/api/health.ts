import { notifySessionExpired } from "../auth/session";

export type HealthStatus = "healthy" | "degraded" | "unhealthy";
export type IssueSeverity = "critical" | "warning" | "info";

export interface InfrastructureComponent {
  name: string;
  status: HealthStatus;
  latency_ms: number | null;
  details: Record<string, unknown>;
  message: string | null;
}

export interface AiProviderHealth {
  provider: "gemini" | "vllm";
  enabled: boolean;
  model: string | null;
  base_url: string | null;
  connection_state: string;
  reason_code: string | null;
  checked_at: string | null;
  connected_at: string | null;
  capabilities: {
    vision?: boolean | null;
    json_schema?: boolean | null;
    context_window?: number | null;
    can_read_pages?: boolean | null;
  } | null;
  key_configured: boolean;
  key_hint: string | null;
}

export interface AiUsageHealth {
  calls_today: number;
  tokens_today: number;
  calls_this_month: number;
  tokens_this_month: number;
  usd_today: string | null;
  uncertain_requests_count: number;
}

export interface AiPrecisionHealth {
  total_requests: number;
  successful_requests: number;
  failed_requests: number;
  waiting_documents: number;
  failed_reading_documents: number;
  fallback_to_rules: number;
  auto_verified_fields: number;
  human_verified_fields: number;
  total_ai_fields: number;
  accuracy_pct: number | null;
  recent_errors: Array<{
    id: string;
    provider: string;
    error_code: string | null;
    skip_code: string | null;
    finished_at: string | null;
  }>;
}

export interface AiHealthDashboard {
  status: HealthStatus;
  mode: string;
  mode_label: string;
  reader_strategy: string;
  primary_reader: string | null;
  scanned_invoices_reader: string;
  scanned_invoices_available: boolean;
  scanned_invoices_reading: boolean;
  providers: AiProviderHealth[];
  usage: AiUsageHealth;
  precision: AiPrecisionHealth;
}

export interface IngestionHealthDashboard {
  status: HealthStatus;
  total_files: number;
  by_status: Record<string, number>;
  by_doc_type: Record<string, number>;
  digital_count: number;
  scanned_count: number;
  failed_files_count: number;
  recent_failed_files: Array<{
    id: string;
    display_name: string;
    error_code: string | null;
    parse_finished_at: string | null;
  }>;
}

export interface MappingHealthDashboard {
  status: HealthStatus;
  total_vendors: number;
  unassigned_documents: number;
  total_links: number;
  confirmed_links: number;
  suggested_links: number;
  three_way_matched_sets: number;
}

export interface ReconciliationHealthDashboard {
  status: HealthStatus;
  engine_version: string;
  rules_hash: string;
  latest_run_id: string | null;
  latest_run_status: string | null;
  latest_run_finished_at: string | null;
  total_cases: number;
  done_cases: number;
  matched_pct: number | null;
  exception_count: number;
  pending_count: number;
  at_risk_total: string | null;
  grni_total: string | null;
  stale_cases_count: number;
}

export interface HealthIssue {
  severity: IssueSeverity;
  component: string;
  title: string;
  description: string;
  action_hint: string;
}

export interface SystemHealthResponse {
  status: HealthStatus;
  timestamp: string;
  app_version: string;
  engine_version: string;
  infrastructure: Record<string, InfrastructureComponent>;
  ai: AiHealthDashboard;
  ingestion: IngestionHealthDashboard;
  mapping: MappingHealthDashboard;
  reconciliation: ReconciliationHealthDashboard;
  issues: HealthIssue[];
}

const apiBase = (import.meta.env["VITE_API_BASE_URL"] ?? "").replace(/\/$/, "");

export async function getSystemHealth(): Promise<SystemHealthResponse> {
  const response = await fetch(`${apiBase}/api/v1/health`, {
    credentials: "include",
    headers: { Accept: "application/json" },
  });

  if (response.status === 401) {
    notifySessionExpired();
  }

  if (!response.ok) {
    let message = `Health check failed (${response.status})`;
    try {
      const body: unknown = await response.json();
      if (
        typeof body === "object" &&
        body !== null &&
        "detail" in body &&
        typeof body.detail === "string"
      ) {
        message = body.detail;
      }
    } catch {
      // keep default status message
    }
    throw new Error(message);
  }

  return (await response.json()) as SystemHealthResponse;
}

