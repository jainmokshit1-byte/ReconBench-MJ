import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  AlertCircle,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock,
  Cpu,
  Database,
  ExternalLink,
  FileInput,
  GitMerge,
  HardDrive,
  Layers,
  Play,
  RefreshCw,
  Server,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Zap,
} from "lucide-react";
import { Link } from "react-router";

import { runLocalAiGuardrailTest, startAiProviderTests, type AiGuardrailReport } from "../api/ai";
import {
  getSystemHealth,
  type HealthIssue,
  type HealthStatus,
  type SystemHealthResponse,
} from "../api/health";
import { useAuth } from "../auth/useAuth";
import { PageHeader } from "../components/PageHeader";

function formatLatency(ms: number | null): string {
  if (ms === null || ms === undefined) return "—";
  return `${ms.toFixed(1)} ms`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return `${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
  } catch {
    return iso;
  }
}

function statusPillClass(status: HealthStatus): string {
  switch (status) {
    case "healthy":
      return "health-pill is-healthy";
    case "degraded":
      return "health-pill is-degraded";
    case "unhealthy":
      return "health-pill is-unhealthy";
    default:
      return "health-pill";
  }
}

function issueSeverityClass(severity: HealthIssue["severity"]): string {
  switch (severity) {
    case "critical":
      return "health-issue-severity is-critical";
    case "warning":
      return "health-issue-severity is-warning";
    case "info":
      return "health-issue-severity is-info";
  }
}

export function HealthPage() {
  const { user, hasCapability } = useAuth();
  const canManageAi = hasCapability("ai.manage");

  const [data, setData] = useState<SystemHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);
  const [guardrailReport, setGuardrailReport] = useState<AiGuardrailReport | null>(null);

  const fetchHealth = useCallback(async () => {
    setError(null);
    try {
      const response = await getSystemHealth();
      setData(response);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Health check request failed.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => void fetchHealth(), 0);
    return () => window.clearTimeout(timer);
  }, [fetchHealth]);

  useEffect(() => {
    if (!autoRefresh) return;
    const interval = window.setInterval(() => {
      void fetchHealth();
    }, 15000);
    return () => window.clearInterval(interval);
  }, [autoRefresh, fetchHealth]);

  const handleTestProvider = async (provider: "gemini" | "vllm") => {
    if (!user?.csrf_token) return;
    setActionBusy(`test-${provider}`);
    setActionNotice(null);
    try {
      const queued = await startAiProviderTests(user.csrf_token, provider);
      setActionNotice(
        `Queued test for ${provider.toUpperCase()} (ID: ${queued.request_id.slice(0, 8)}). Check status below shortly.`,
      );
      setTimeout(() => void fetchHealth(), 2000);
    } catch (cause) {
      setActionNotice(
        `Provider test failed to queue: ${cause instanceof Error ? cause.message : "Error"}`,
      );
    } finally {
      setActionBusy(null);
    }
  };

  const handleRunGuardrails = async () => {
    if (!user?.csrf_token) return;
    setActionBusy("guardrails");
    setActionNotice(null);
    try {
      const report = await runLocalAiGuardrailTest(user.csrf_token);
      setGuardrailReport(report);
      setActionNotice(
        report.passed
          ? "AI offline guardrail test PASSED. All synthetic checks validated."
          : "AI offline guardrail test FAILED. Review check results.",
      );
    } catch (cause) {
      setActionNotice(
        `Guardrail test failed: ${cause instanceof Error ? cause.message : "Error"}`,
      );
    } finally {
      setActionBusy(null);
    }
  };

  const overallStatus = data?.status ?? "healthy";

  return (
    <div className="health-page-container">
      <PageHeader
        eyebrow="SYSTEM OPERATIONS & DIAGNOSTICS"
        title="System Health & API Dashboard"
        description="Comprehensive real-time status of APIs, connections, LLMs, AI precision, mapping, and reconciliation."
        action={
          <div className="health-header-actions">
            <label className="health-autorefresh-toggle" title="Auto-refresh every 15s">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              <span>Live polling (15s)</span>
            </label>
            <button
              className="button button-secondary"
              type="button"
              disabled={loading}
              onClick={() => {
                setLoading(true);
                void fetchHealth();
              }}
            >
              <RefreshCw size={14} className={loading ? "spin" : ""} aria-hidden="true" />
              <span>Refresh</span>
            </button>
          </div>
        }
      />

      {error ? (
        <div className="inline-error" role="alert">
          <AlertCircle size={16} aria-hidden="true" />
          <span>{error}</span>
          <button className="button" type="button" onClick={() => void fetchHealth()}>
            Retry
          </button>
        </div>
      ) : null}

      {actionNotice ? (
        <div className="health-action-banner" role="status">
          <Sparkles size={16} aria-hidden="true" />
          <span>{actionNotice}</span>
          <button
            className="health-banner-close"
            type="button"
            onClick={() => setActionNotice(null)}
          >
            ×
          </button>
        </div>
      ) : null}

      {loading && !data ? (
        <div
          className="panel"
          role="status"
          style={{
            padding: "3.5rem 2rem",
            textAlign: "center",
            color: "var(--color-ink-muted)",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: "0.75rem",
          }}
        >
          <RefreshCw size={28} className="spin text-accent" aria-hidden="true" />
          <strong style={{ fontSize: "1.05rem", color: "var(--color-ink-strong)" }}>
            Connecting to system health diagnostics…
          </strong>
          <span style={{ fontSize: "0.875rem", opacity: 0.85 }}>
            Gathering live telemetry from PostgreSQL, MinIO, worker queues, and AI models
          </span>
        </div>
      ) : null}

      {/* 1. System Overall Status Banner */}
      <section
        className={`health-banner ${overallStatus}`}
        aria-label="Overall system status summary"
      >
        <div className="health-banner-left">
          <div className="health-banner-icon">
            {overallStatus === "healthy" ? (
              <CheckCircle2 size={26} aria-hidden="true" />
            ) : overallStatus === "degraded" ? (
              <AlertTriangle size={26} aria-hidden="true" />
            ) : (
              <ShieldAlert size={26} aria-hidden="true" />
            )}
          </div>
          <div>
            <div className="health-banner-title">
              System Status: <strong>{overallStatus.toUpperCase()}</strong>
            </div>
            <div className="health-banner-meta">
              Engine Version: <code>{data?.engine_version ?? "0.1.16"}</code> · App:{" "}
              <code>{data?.app_version ?? "0.1.0"}</code> · Checked:{" "}
              {formatDate(data?.timestamp ?? null)}
            </div>
          </div>
        </div>

        <div className="health-banner-actions">
          {canManageAi ? (
            <>
              <button
                className="button button-secondary health-quick-btn"
                type="button"
                disabled={actionBusy !== null}
                onClick={() => void handleTestProvider("gemini")}
              >
                <Zap size={14} aria-hidden="true" />
                <span>Test Gemini</span>
              </button>
              <button
                className="button button-secondary health-quick-btn"
                type="button"
                disabled={actionBusy !== null}
                onClick={() => void handleTestProvider("vllm")}
              >
                <Server size={14} aria-hidden="true" />
                <span>Test vLLM</span>
              </button>
              <button
                className="button button-secondary health-quick-btn"
                type="button"
                disabled={actionBusy !== null}
                onClick={() => void handleRunGuardrails()}
              >
                <ShieldCheck size={14} aria-hidden="true" />
                <span>Run Guardrails</span>
              </button>
            </>
          ) : (
            <span className="quiet-badge">Read-only view</span>
          )}
        </div>
      </section>

      {/* Guardrail Self-Test Report Modal / Banner */}
      {guardrailReport ? (
        <section className="panel health-guardrail-panel">
          <div className="panel-heading">
            <div>
              <div className="panel-kicker">AI GUARDRAIL VERIFICATION</div>
              <h2>Offline Stub Guardrail Report</h2>
            </div>
            <button
              className="button button-secondary"
              type="button"
              onClick={() => setGuardrailReport(null)}
            >
              Dismiss
            </button>
          </div>
          <p className="health-card-desc">
            Local offline fixtures test bounded inputs, deterministic arithmetic, and zero false
            matches without calling external provider APIs.
          </p>
          <div className="health-guardrail-grid">
            {Object.entries(guardrailReport.checks).map(([checkName, passed]) => (
              <div
                key={checkName}
                className={`health-guardrail-check ${passed ? "is-passed" : "is-failed"}`}
              >
                {passed ? <CheckCircle2 size={15} /> : <AlertCircle size={15} />}
                <span>{checkName.replaceAll("_", " ")}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {/* 2. Active Issues & Action Guidance */}
      {data?.issues && data.issues.length > 0 ? (
        <section className="panel health-issues-panel" aria-label="Detected issues and solutions">
          <div className="panel-heading">
            <div>
              <div className="panel-kicker">ACTIONABLE GUIDANCE</div>
              <h2>Detected Issues & Recommendations</h2>
            </div>
            <span className="health-issues-count">
              {data.issues.length} {data.issues.length === 1 ? "issue" : "issues"} requiring
              attention
            </span>
          </div>

          <div className="health-issues-list">
            {data.issues.map((issue, idx) => (
              <div key={idx} className={`health-issue-item ${issue.severity}`}>
                <div className="health-issue-header">
                  <span className={issueSeverityClass(issue.severity)}>
                    {issue.severity.toUpperCase()}
                  </span>
                  <span className="health-issue-component">{issue.component}</span>
                  <strong className="health-issue-title">{issue.title}</strong>
                </div>
                <p className="health-issue-desc">{issue.description}</p>
                <div className="health-issue-action">
                  <ArrowRight size={14} aria-hidden="true" />
                  <span>
                    <strong>Resolution:</strong> {issue.action_hint}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <div className="health-no-issues-banner">
          <CheckCircle2 size={18} className="text-emerald" aria-hidden="true" />
          <span>
            No active issues detected. All connections, queue jobs, and models are running normally.
          </span>
        </div>
      )}

      {/* 3. Core Connections & Infrastructure */}
      <h3 className="health-section-title">
        <Database size={16} aria-hidden="true" /> Core Infrastructure & Connections
      </h3>
      <div className="health-cards-grid">
        {/* Database */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <Database size={18} aria-hidden="true" />
              <h4>PostgreSQL</h4>
            </div>
            <span className={statusPillClass(data?.infrastructure["database"]?.status ?? "healthy")}>
              {data?.infrastructure["database"]?.status ?? "healthy"}
            </span>
          </div>
          <p className="health-card-desc">{data?.infrastructure["database"]?.message ?? "Connected"}</p>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">Ping Latency</span>
              <strong className="health-metric-value">
                {formatLatency(data?.infrastructure["database"]?.latency_ms ?? null)}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Migrations</span>
              <strong className="health-metric-value">
                {data?.infrastructure["database"]?.details?.["migrations_in_sync"]
                  ? "In Sync"
                  : "Pending"}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Pool Checked Out</span>
              <strong className="health-metric-value">
                {String(
                  (data?.infrastructure["database"]?.details?.["pool"] as Record<string, unknown>)?.[
                    "checked_out"
                  ] ?? 0,
                )}
              </strong>
            </div>
          </div>
        </div>

        {/* Object Store */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <HardDrive size={18} aria-hidden="true" />
              <h4>Object Store (S3 / MinIO)</h4>
            </div>
            <span className={statusPillClass(data?.infrastructure["storage"]?.status ?? "healthy")}>
              {data?.infrastructure["storage"]?.status ?? "healthy"}
            </span>
          </div>
          <p className="health-card-desc">{data?.infrastructure["storage"]?.message ?? "Connected"}</p>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">Ping Latency</span>
              <strong className="health-metric-value">
                {formatLatency(data?.infrastructure["storage"]?.latency_ms ?? null)}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Target Bucket</span>
              <strong
                className="health-metric-value truncate"
                title={String(data?.infrastructure["storage"]?.details?.["bucket"] ?? "reconbench")}
              >
                {String(data?.infrastructure["storage"]?.details?.["bucket"] ?? "reconbench")}
              </strong>
            </div>
          </div>
        </div>

        {/* Worker Queue */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <Cpu size={18} aria-hidden="true" />
              <h4>Background Workers</h4>
            </div>
            <span className={statusPillClass(data?.infrastructure["workers"]?.status ?? "healthy")}>
              {data?.infrastructure["workers"]?.status ?? "healthy"}
            </span>
          </div>
          <p className="health-card-desc">{data?.infrastructure["workers"]?.message ?? "Running"}</p>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">Active Workers</span>
              <strong className="health-metric-value">
                {String(data?.infrastructure["workers"]?.details?.["active_workers"] ?? 0)}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Jobs (Todo / Doing)</span>
              <strong className="health-metric-value">
                {String(
                  (
                    data?.infrastructure["workers"]?.details?.["job_counts"] as Record<string, unknown>
                  )?.["todo"] ?? 0,
                )}{" "}
                /{" "}
                {String(
                  (
                    data?.infrastructure["workers"]?.details?.["job_counts"] as Record<string, unknown>
                  )?.["doing"] ?? 0,
                )}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Jobs Failed</span>
              <strong
                className={`health-metric-value ${
                  Number(
                    (
                      data?.infrastructure["workers"]?.details?.["job_counts"] as Record<
                        string,
                        unknown
                      >
                    )?.["failed"] ?? 0,
                  ) > 0
                    ? "text-rose"
                    : ""
                }`}
              >
                {String(
                  (
                    data?.infrastructure["workers"]?.details?.["job_counts"] as Record<string, unknown>
                  )?.["failed"] ?? 0,
                )}
              </strong>
            </div>
          </div>
        </div>
      </div>

      {/* 4. LLMs, AI Components & Precision */}
      <h3 className="health-section-title">
        <Sparkles size={16} aria-hidden="true" /> LLMs & AI Precision Analytics
      </h3>

      <div className="health-cards-grid">
        {/* AI Modes & Strategy */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <Zap size={18} aria-hidden="true" />
              <h4>AI System Configuration</h4>
            </div>
            <span className="health-pill is-info">{data?.ai?.mode_label ?? "Rules only"}</span>
          </div>
          <div className="health-kv-list">
            <div className="health-kv-item">
              <span>Scanned Reader Choice:</span>
              <strong>{data?.ai?.scanned_invoices_reader === "ai" ? "AI Reading" : "Rules Engine"}</strong>
            </div>
            <div className="health-kv-item">
              <span>Reader Strategy:</span>
              <strong>{data?.ai?.reader_strategy ?? "off"}</strong>
            </div>
            <div className="health-kv-item">
              <span>Primary Reader:</span>
              <strong>{data?.ai?.primary_reader ?? "None"}</strong>
            </div>
            <div className="health-kv-item">
              <span>AI Available on Host:</span>
              <strong>{data?.ai?.scanned_invoices_available ? "Yes (Consent & Server OK)" : "No"}</strong>
            </div>
          </div>
        </div>

        {/* AI Precision & Quality */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <Activity size={18} aria-hidden="true" />
              <h4>AI Precision & Field Accuracy</h4>
            </div>
            <span
              className={
                (data?.ai?.precision?.accuracy_pct ?? 0) >= 90
                  ? "health-pill is-healthy"
                  : (data?.ai?.precision?.accuracy_pct ?? 0) > 0
                  ? "health-pill is-degraded"
                  : "health-pill"
              }
            >
              {data?.ai?.precision?.accuracy_pct !== null && data?.ai?.precision?.accuracy_pct !== undefined
                ? `${data.ai.precision.accuracy_pct}% Accuracy`
                : "No AI fields"}
            </span>
          </div>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">AI Fields Extracted</span>
              <strong className="health-metric-value">{data?.ai?.precision?.total_ai_fields ?? 0}</strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Auto-Verified</span>
              <strong className="health-metric-value text-emerald">
                {data?.ai?.precision?.auto_verified_fields ?? 0}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Human-Verified</span>
              <strong className="health-metric-value">
                {data?.ai?.precision?.human_verified_fields ?? 0}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Waiting Docs</span>
              <strong className="health-metric-value">
                {data?.ai?.precision?.waiting_documents ?? 0}
              </strong>
            </div>
          </div>
          {data?.ai?.precision?.recent_errors && data.ai.precision.recent_errors.length > 0 ? (
            <div className="health-recent-errors">
              <span className="health-recent-errors-title">Recent AI Failures:</span>
              <ul>
                {data.ai.precision.recent_errors.map((err) => (
                  <li key={err.id}>
                    <code>{err.provider}</code>: {err.error_code || err.skip_code || "Failed"}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>

        {/* AI Usage & Cost */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <Clock size={18} aria-hidden="true" />
              <h4>AI Usage & Budget</h4>
            </div>
            <span className="health-pill is-info">Today & Month</span>
          </div>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">Calls Today</span>
              <strong className="health-metric-value">{data?.ai?.usage?.calls_today ?? 0}</strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Tokens Today</span>
              <strong className="health-metric-value">
                {(data?.ai?.usage?.tokens_today ?? 0).toLocaleString()}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Calls This Month</span>
              <strong className="health-metric-value">{data?.ai?.usage?.calls_this_month ?? 0}</strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Tokens This Month</span>
              <strong className="health-metric-value">
                {(data?.ai?.usage?.tokens_this_month ?? 0).toLocaleString()}
              </strong>
            </div>
          </div>
          <div className="health-card-footer-note">
            Estimated Spend Today: <strong>{data?.ai?.usage?.usd_today ? `$${data.ai.usage.usd_today}` : "—"}</strong> ·
            Uncertain Requests: <strong>{data?.ai?.usage?.uncertain_requests_count ?? 0}</strong>
          </div>
        </div>
      </div>

      {/* Provider Details (Gemini and vLLM) */}
      <h4 className="health-subsection-title">AI Provider Connections</h4>
      <div className="health-providers-grid">
        {(data?.ai?.providers ?? []).map((providerCard) => {
          const isConnected = providerCard.connection_state === "connected";
          const isFailed = providerCard.connection_state === "failed";
          return (
            <div key={providerCard.provider} className="panel health-provider-card">
              <div className="health-card-header">
                <div className="health-provider-title">
                  <strong>{providerCard.provider.toUpperCase()}</strong>
                  <span className="health-provider-model">{providerCard.model || "Default Model"}</span>
                </div>
                <span
                  className={`health-pill ${
                    isConnected ? "is-healthy" : isFailed ? "is-unhealthy" : "is-degraded"
                  }`}
                >
                  {providerCard.connection_state.replace("_", " ")}
                </span>
              </div>

              <div className="health-kv-list">
                <div className="health-kv-item">
                  <span>API Key Configured:</span>
                  <strong>{providerCard.key_configured ? `Yes (${providerCard.key_hint || "set"})` : "Not Set"}</strong>
                </div>
                {providerCard.base_url ? (
                  <div className="health-kv-item">
                    <span>Base URL:</span>
                    <strong className="truncate" title={providerCard.base_url}>
                      {providerCard.base_url}
                    </strong>
                  </div>
                ) : null}
                <div className="health-kv-item">
                  <span>Last Checked:</span>
                  <strong>{formatDate(providerCard.checked_at)}</strong>
                </div>
                {providerCard.reason_code ? (
                  <div className="health-kv-item text-rose">
                    <span>Error Code:</span>
                    <strong>{providerCard.reason_code}</strong>
                  </div>
                ) : null}
              </div>

              {providerCard.capabilities ? (
                <div className="health-caps-list">
                  <span className="health-caps-label">Verified Capabilities:</span>
                  <div className="health-caps-pills">
                    <span className={providerCard.capabilities.vision ? "cap-pill is-yes" : "cap-pill is-no"}>
                      Vision
                    </span>
                    <span className={providerCard.capabilities.json_schema ? "cap-pill is-yes" : "cap-pill is-no"}>
                      JSON Schema
                    </span>
                    <span className={providerCard.capabilities.can_read_pages ? "cap-pill is-yes" : "cap-pill is-no"}>
                      Full Page Reading
                    </span>
                  </div>
                </div>
              ) : null}

              {canManageAi ? (
                <button
                  className="button button-secondary health-provider-test-btn"
                  type="button"
                  disabled={actionBusy !== null}
                  onClick={() => void handleTestProvider(providerCard.provider)}
                >
                  <Play size={13} aria-hidden="true" />
                  <span>Test Connection</span>
                </button>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* 5. Invoices, Ingestion, Mapping, Reconciliation */}
      <h3 className="health-section-title">
        <Layers size={16} aria-hidden="true" /> Pipeline & Document Operations
      </h3>
      <div className="health-cards-grid">
        {/* Ingestion & Invoices */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <FileInput size={18} aria-hidden="true" />
              <h4>Invoices & Ingestion</h4>
            </div>
            <Link to="/ingestion" className="health-link">
              View <ExternalLink size={12} />
            </Link>
          </div>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">Total Files</span>
              <strong className="health-metric-value">{data?.ingestion?.total_files ?? 0}</strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Parsed</span>
              <strong className="health-metric-value text-emerald">
                {data?.ingestion?.by_status?.["parsed"] ?? 0}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Failed</span>
              <strong
                className={`health-metric-value ${
                  (data?.ingestion?.failed_files_count ?? 0) > 0 ? "text-rose" : ""
                }`}
              >
                {data?.ingestion?.failed_files_count ?? 0}
              </strong>
            </div>
          </div>
          <div className="health-kv-list" style={{ marginTop: "12px" }}>
            <div className="health-kv-item">
              <span>Digital vs Scanned:</span>
              <strong>
                {data?.ingestion?.digital_count ?? 0} Digital · {data?.ingestion?.scanned_count ?? 0} Scanned
              </strong>
            </div>
            <div className="health-kv-item">
              <span>Document Breakdown:</span>
              <strong>
                {data?.ingestion?.by_doc_type?.["invoice"] ?? 0} Invoices ·{" "}
                {data?.ingestion?.by_doc_type?.["purchase_order"] ?? 0} POs ·{" "}
                {data?.ingestion?.by_doc_type?.["goods_receipt"] ?? 0} GRNs
              </strong>
            </div>
          </div>
        </div>

        {/* Mapping & Vendor Resolution */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <GitMerge size={18} aria-hidden="true" />
              <h4>Mapping & Links</h4>
            </div>
            <Link to="/mapping" className="health-link">
              View <ExternalLink size={12} />
            </Link>
          </div>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">Vendors</span>
              <strong className="health-metric-value">{data?.mapping?.total_vendors ?? 0}</strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Unassigned Docs</span>
              <strong
                className={`health-metric-value ${
                  (data?.mapping?.unassigned_documents ?? 0) > 0 ? "text-amber" : ""
                }`}
              >
                {data?.mapping?.unassigned_documents ?? 0}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Confirmed Links</span>
              <strong className="health-metric-value text-emerald">
                {data?.mapping?.confirmed_links ?? 0}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Suggested Links</span>
              <strong className="health-metric-value">{data?.mapping?.suggested_links ?? 0}</strong>
            </div>
          </div>
          <p className="health-card-desc" style={{ marginTop: "10px" }}>
            Links bind supplier invoices to purchase orders and goods-received notes for 3-way
            reconciliation.
          </p>
        </div>

        {/* Reconciliation Engine */}
        <div className="panel health-card">
          <div className="health-card-header">
            <div className="health-card-title-wrap">
              <Layers size={18} aria-hidden="true" />
              <h4>Reconciliation Engine</h4>
            </div>
            <Link to="/reconciliation" className="health-link">
              View <ExternalLink size={12} />
            </Link>
          </div>
          <div className="health-metrics-row">
            <div className="health-metric-box">
              <span className="health-metric-label">Engine Version</span>
              <strong className="health-metric-value">{data?.reconciliation?.engine_version ?? "0.1.16"}</strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Matched %</span>
              <strong className="health-metric-value text-emerald">
                {data?.reconciliation?.matched_pct !== null && data?.reconciliation?.matched_pct !== undefined
                  ? `${data.reconciliation.matched_pct}%`
                  : "—"}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Exceptions</span>
              <strong
                className={`health-metric-value ${
                  (data?.reconciliation?.exception_count ?? 0) > 0 ? "text-amber" : ""
                }`}
              >
                {data?.reconciliation?.exception_count ?? 0}
              </strong>
            </div>
            <div className="health-metric-box">
              <span className="health-metric-label">Stale Cases</span>
              <strong
                className={`health-metric-value ${
                  (data?.reconciliation?.stale_cases_count ?? 0) > 0 ? "text-amber" : ""
                }`}
              >
                {data?.reconciliation?.stale_cases_count ?? 0}
              </strong>
            </div>
          </div>
          <div className="health-kv-list" style={{ marginTop: "12px" }}>
            <div className="health-kv-item">
              <span>Financial At-Risk:</span>
              <strong>{data?.reconciliation?.at_risk_total ? `₹ ${data.reconciliation.at_risk_total}` : "₹ 0.00"}</strong>
            </div>
            <div className="health-kv-item">
              <span>Rules Hash:</span>
              <code className="truncate" title={data?.reconciliation?.rules_hash ?? ""}>
                {(data?.reconciliation?.rules_hash ?? "").slice(0, 16)}…
              </code>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
