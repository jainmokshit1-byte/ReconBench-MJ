import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import type { SystemHealthResponse } from "../api/health";
import { HealthPage } from "./HealthPage";

const capabilities = ["data.view", "ai.manage", "settings.change"];
const managerContext: AuthContextValue = {
  status: "authenticated",
  user: {
    name: "Synthetic Manager",
    role: "manager",
    csrf_token: "csrf-health-test",
    capabilities,
  },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => capabilities.includes(capability),
};

const sampleHealthResponse: SystemHealthResponse = {
  status: "healthy",
  timestamp: "2026-11-03T18:00:00Z",
  app_version: "0.1.0",
  engine_version: "0.1.16",
  infrastructure: {
    database: {
      name: "PostgreSQL Database",
      status: "healthy",
      latency_ms: 2.4,
      details: {
        migrations_in_sync: true,
        current_heads: ["0036_reading_state"],
        expected_heads: ["0036_reading_state"],
        pool: { size: 5, checked_in: 4, checked_out: 1 },
      },
      message: "Connected and in sync",
    },
    storage: {
      name: "Object Store (S3/MinIO)",
      status: "healthy",
      latency_ms: 6.8,
      details: { bucket: "test-invoices" },
      message: "Bucket accessible",
    },
    workers: {
      name: "Background Worker Queue",
      status: "healthy",
      latency_ms: null,
      details: {
        active_workers: 2,
        last_heartbeat: "2026-11-03T17:59:50Z",
        job_counts: { todo: 1, doing: 0, succeeded: 45, failed: 0 },
      },
      message: "2 active worker(s)",
    },
  },
  ai: {
    status: "healthy",
    mode: "ai_assisted",
    mode_label: "AI Assisted",
    reader_strategy: "single",
    primary_reader: "vllm",
    scanned_invoices_reader: "ai",
    scanned_invoices_available: true,
    scanned_invoices_reading: true,
    providers: [
      {
        provider: "gemini",
        enabled: true,
        model: "gemini-2.0-flash",
        base_url: null,
        connection_state: "connected",
        reason_code: null,
        checked_at: "2026-11-03T17:50:00Z",
        connected_at: "2026-11-03T17:50:00Z",
        capabilities: {
          vision: true,
          json_schema: true,
          context_window: 1048576,
          can_read_pages: true,
        },
        key_configured: true,
        key_hint: "…a1b2",
      },
      {
        provider: "vllm",
        enabled: true,
        model: "qwen2-vl-72b",
        base_url: "http://127.0.0.1:8000/v1",
        connection_state: "connected",
        reason_code: null,
        checked_at: "2026-11-03T17:55:00Z",
        connected_at: "2026-11-03T17:55:00Z",
        capabilities: {
          vision: true,
          json_schema: true,
          context_window: 32768,
          can_read_pages: true,
        },
        key_configured: false,
        key_hint: null,
      },
    ],
    usage: {
      calls_today: 18,
      tokens_today: 64200,
      calls_this_month: 210,
      tokens_this_month: 890000,
      usd_today: "0.14",
      uncertain_requests_count: 0,
    },
    precision: {
      total_requests: 22,
      successful_requests: 21,
      failed_requests: 1,
      waiting_documents: 0,
      failed_reading_documents: 0,
      fallback_to_rules: 1,
      auto_verified_fields: 42,
      human_verified_fields: 6,
      total_ai_fields: 50,
      accuracy_pct: 96.0,
      recent_errors: [],
    },
  },
  ingestion: {
    status: "healthy",
    total_files: 14,
    by_status: { parsed: 14 },
    by_doc_type: { invoice: 10, purchase_order: 2, goods_receipt: 2 },
    digital_count: 9,
    scanned_count: 5,
    failed_files_count: 0,
    recent_failed_files: [],
  },
  mapping: {
    status: "healthy",
    total_vendors: 6,
    unassigned_documents: 0,
    total_links: 12,
    confirmed_links: 12,
    suggested_links: 0,
    three_way_matched_sets: 4,
  },
  reconciliation: {
    status: "healthy",
    engine_version: "0.1.16",
    rules_hash: "2a203e88db941b1aa1735f1675f17dfad38d2f2fc667fbcc1df7a6565bdf3b1f",
    latest_run_id: "run-abc-123",
    latest_run_status: "completed",
    latest_run_finished_at: "2026-11-03T17:45:00Z",
    total_cases: 10,
    done_cases: 10,
    matched_pct: 90.0,
    exception_count: 1,
    pending_count: 0,
    at_risk_total: "1250.00",
    grni_total: "0.00",
    stale_cases_count: 0,
  },
  issues: [
    {
      severity: "info",
      component: "reconciliation",
      title: "Reconciliation Up to Date",
      description: "Latest run completed with 90% matched cases.",
      action_hint: "Review 1 exception case on Reconciliation page.",
    },
  ],
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("HealthPage", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the operational dashboard with all systems, connections, and AI precision metrics", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/health")) {
        return jsonResponse(sampleHealthResponse);
      }
      return jsonResponse({ detail: "Not found" }, 404);
    });

    render(
      <MemoryRouter>
        <AuthContext.Provider value={managerContext}>
          <HealthPage />
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/System Health & API Dashboard/i)).toBeInTheDocument();
    });

    // Overall banner
    expect(screen.getByText(/System Status:/i)).toBeInTheDocument();
    expect(screen.getAllByText(/HEALTHY/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0.1.16/i).length).toBeGreaterThan(0);

    // Infrastructure cards
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("2.4 ms")).toBeInTheDocument();
    expect(screen.getByText("Object Store (S3 / MinIO)")).toBeInTheDocument();
    expect(screen.getByText("Background Workers")).toBeInTheDocument();

    // AI Components & Precision
    expect(screen.getByText(/LLMs & AI Precision Analytics/i)).toBeInTheDocument();
    expect(screen.getByText(/AI Precision & Field Accuracy/i)).toBeInTheDocument();
    expect(screen.getByText(/96% Accuracy/i)).toBeInTheDocument();
    expect(screen.getByText("GEMINI")).toBeInTheDocument();
    expect(screen.getByText("VLLM")).toBeInTheDocument();
    expect(screen.getByText("gemini-2.0-flash")).toBeInTheDocument();
    expect(screen.getByText("qwen2-vl-72b")).toBeInTheDocument();

    // Pipeline sections
    expect(screen.getByText(/Invoices & Ingestion/i)).toBeInTheDocument();
    expect(screen.getByText(/Mapping & Links/i)).toBeInTheDocument();
    expect(screen.getByText(/Reconciliation Engine/i)).toBeInTheDocument();
    expect(screen.getByText("90%")).toBeInTheDocument();

    // Issues & Guidance
    expect(screen.getByText(/Reconciliation Up to Date/i)).toBeInTheDocument();
    expect(screen.getByText(/Review 1 exception case on Reconciliation page/i)).toBeInTheDocument();
  });

  it("triggers AI provider test and updates notice banner", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/v1/health")) {
        return jsonResponse(sampleHealthResponse);
      }
      if (url.includes("/api/v1/ai/test/provider")) {
        return jsonResponse({ request_id: "req-12345678-test", image_request_id: null });
      }
      return jsonResponse({ detail: "Not found" }, 404);
    });

    render(
      <MemoryRouter>
        <AuthContext.Provider value={managerContext}>
          <HealthPage />
        </AuthContext.Provider>
      </MemoryRouter>,
    );

    await waitFor(() => {
      expect(screen.getByText(/System Health & API Dashboard/i)).toBeInTheDocument();
    });

    const testGeminiBtn = screen.getByRole("button", { name: /Test Gemini/i });
    fireEvent.click(testGeminiBtn);

    await waitFor(() => {
      expect(screen.getByText(/Queued test for GEMINI/i)).toBeInTheDocument();
    });
  });
});
