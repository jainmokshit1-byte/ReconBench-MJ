import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, ArrowRight, Check, CircleCheck, FileInput, GitMerge, ListChecks, RefreshCw } from "lucide-react";
import { Link } from "react-router";

import { getReconciliationKpis, getWorkflowStatus, listRecentReconciliationRuns } from "../api/reconciliation";
import type { RunSummary, WorkflowStatus, WorkflowStep, WorkflowStepKey } from "../api/reconciliation";
import { AiAssistCard } from "../components/AiAssistCard";
import { PageHeader } from "../components/PageHeader";
import { usePeriod } from "../contexts/usePeriod";

type Kpis = Record<string, unknown>;

function periodBounds(period: string): { from: string; to: string } {
  const year = Number(period.slice(0, 4));
  const month = Number(period.slice(5, 7));
  const lastDay = new Date(year, month, 0).getDate();
  return { from: `${period}-01`, to: `${period}-${String(lastDay).padStart(2, "0")}` };
}

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "string" || typeof value === "number" ? String(value) : "—";
}

function money(value: unknown): string {
  return value === null || value === undefined ? "—" : `₹ ${String(value)}`;
}

function statusLabel(status: string): string {
  return status.replaceAll("_", " ");
}

function plural(count: number | undefined, noun: string): string {
  const value = count ?? 0;
  return `${value} ${noun}${value === 1 ? "" : "s"}`;
}

// What a step still needs; the server decides completion (decision G-12).
function stepDetail(step: WorkflowStep | undefined): string {
  if (!step) return "";
  if (step.complete) return "Done";
  switch (step.key) {
    case "ingest":
      return step.files_in_progress ? `${plural(step.files_in_progress, "file")} still parsing` : "No parsed documents yet";
    case "map":
      return step.unassigned_documents || step.suggested_links
        ? [step.unassigned_documents ? `${step.unassigned_documents} unassigned` : "", step.suggested_links ? plural(step.suggested_links, "suggested link") : ""].filter(Boolean).join(" · ")
        : "Waiting for documents";
    case "reconcile":
      return step.stale_cases ? `${plural(step.stale_cases, "stale case")} — re-run` : "No run for this period";
    case "export":
      return "No workbook from the latest run";
  }
}

const STEPS: Array<{ key: WorkflowStepKey; label: string; path: string; icon: typeof FileInput }> = [
  { key: "ingest", label: "Ingest files", path: "/ingestion", icon: FileInput },
  { key: "map", label: "Confirm mapping", path: "/mapping", icon: GitMerge },
  { key: "reconcile", label: "Review results", path: "/reconciliation", icon: ListChecks },
  { key: "export", label: "Export workbook", path: "/reconciliation", icon: Check },
];

function runPath(runId: string | null): string {
  return runId ? `/reconciliation?run_id=${encodeURIComponent(runId)}` : "/reconciliation";
}

function nextAction(workflow: WorkflowStatus | null): { path: string; label: string } {
  if (!workflow) return { path: "/ingestion", label: "Upload documents" };
  if (workflow.active_run_id) return { path: runPath(workflow.active_run_id), label: "View run progress" };
  switch (workflow.next_step) {
    case "ingest":
      return { path: "/ingestion", label: workflow.steps[0]?.files_in_progress ? "View parsing progress" : "Upload documents" };
    case "map":
      return { path: "/mapping", label: "Review vendor mapping" };
    case "reconcile":
      return { path: "/reconciliation", label: "Run reconciliation" };
    case "export":
      return { path: runPath(workflow.run_id), label: "Export workbook" };
    default:
      return { path: runPath(workflow.run_id), label: "Review results" };
  }
}

export function HomePage() {
  const { period } = usePeriod();
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [workflow, setWorkflow] = useState<WorkflowStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const bounds = useMemo(() => periodBounds(period), [period]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    void Promise.all([
      getReconciliationKpis(bounds.from, bounds.to),
      listRecentReconciliationRuns(),
      getWorkflowStatus(bounds.from, bounds.to),
    ]).then(([nextKpis, nextRuns, nextWorkflow]) => {
      setKpis(nextKpis);
      setRuns(nextRuns.slice(0, 5));
      setWorkflow(nextWorkflow);
    }).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "Workspace summary could not be loaded.");
    }).finally(() => setLoading(false));
  }, [bounds.from, bounds.to]);

  useEffect(() => {
    const timer = window.setTimeout(refresh, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const activeRun = workflow?.active_run_id ?? null;
  const { path: nextPath, label: nextLabel } = nextAction(workflow);
  const stepsByKey = new Map((workflow?.steps ?? []).map((step) => [step.key, step]));
  const cards: Array<[string, unknown]> = [
    ["Invoices", kpis?.["invoice_count"]],
    ["Matched %", kpis?.["matched_pct"] === null || kpis?.["matched_pct"] === undefined ? null : `${kpis["matched_pct"]}%`],
    ["Pending", kpis?.["pending_count"]],
    ["Exceptions", kpis?.["exception_count"]],
    ["Unlinked", kpis?.["unlinked_count"]],
    ["At-risk ₹", money(kpis?.["at_risk_total"])],
    ["GRNI ₹", money(kpis?.["grni_total"])],
  ];

  return (
    <>
      <PageHeader
        eyebrow="WORKSPACE OVERVIEW"
        title="ReconBench Home"
        description={`Invoice-date summary for ${period}. Financial totals are calculated by the shared server KPI query.`}
        action={<Link className="button button-primary" to={nextPath}>{nextLabel} <ArrowRight size={15} /></Link>}
      />

      {error ? <div className="inline-error" role="alert">{error} <button className="button" type="button" onClick={refresh}>Retry</button></div> : null}

      <section className="home-grid" aria-label="Workspace setup">
        <div className="panel tracker-panel">
          <div className="panel-heading"><div><div className="panel-kicker">YOUR WORKFLOW</div><h2>Four steps to a clear close</h2></div><span className="quiet-badge">{activeRun ? "Reconciliation running" : workflow?.next_step === null ? "All steps complete" : "Ready when you are"}</span></div>
          <ol className="workflow-steps" aria-label="Workflow progress">
            {STEPS.map(({ key, label, path, icon: Icon }, index) => {
              const step = stepsByKey.get(key);
              const state = step?.complete ? "complete" : workflow?.next_step === key ? "current" : "upcoming";
              const to = key === "export" || key === "reconcile" ? runPath(workflow?.run_id ?? null) : path;
              return <li className="workflow-step-item" key={key}><Link className={`workflow-step ${state}`} to={to} aria-current={state === "current" ? "step" : undefined}><span className="step-number">{String(index + 1).padStart(2, "0")}</span><span className="step-icon">{state === "complete" ? <CircleCheck size={18} aria-hidden="true" /> : <Icon size={18} aria-hidden="true" />}</span><span className="step-label">{label}</span><span className="step-detail" data-testid={`step-${key}-detail`}>{loading && !workflow ? "…" : stepDetail(step)}</span>{index < 3 ? <span className="step-connector" aria-hidden="true" /> : null}</Link></li>;
            })}
          </ol>
          <div className="panel-footnote">Invoice is the reconciliation case anchor. PO and GRN records provide evidence and cumulative quantity balances.</div>
        </div>
        <AiAssistCard />
      </section>

      <section className="home-lower-grid">
        <div className="panel kpi-placeholder">
          <div className="panel-heading"><div><div className="panel-kicker">PERIOD SUMMARY</div><h2>Reconciliation at a glance</h2></div><button className="icon-button" type="button" disabled={loading} onClick={refresh} aria-label="Refresh summary"><RefreshCw size={15} /></button></div>
          <div className="kpi-grid">{cards.map(([label, value]) => <div className="kpi-tile" key={label}><span>{label}</span><strong>{loading ? "…" : display(value)}</strong></div>)}</div>
          <div className="muted-note">Matched % is Matched plus Matched with flags, divided by invoices for the selected invoice-date period.</div>
        </div>
        <div className="panel recent-panel">
          <div className="panel-heading"><div><div className="panel-kicker">RECENT ACTIVITY</div><h2>{runs.length ? "Recent reconciliation runs" : "No run activity yet"}</h2></div><Activity size={16} aria-hidden="true" /></div>
          {runs.length ? <ul className="home-run-list">{runs.map((run) => <li key={run.id}><Link to={`/reconciliation?run_id=${encodeURIComponent(run.id)}`}><span>Run {run.id.slice(0, 8)}</span><strong>{statusLabel(run.status)}</strong></Link><small>{run.done_cases} / {run.total_cases} invoice cases</small></li>)}</ul> : <div className="recent-empty"><span className="empty-file-icon"><FileInput size={18} /></span><span>{loading ? "Loading recent activity…" : "Upload and process documents to begin."}</span></div>}
          <Link className="text-link" to="/ingestion">Go to Ingestion <ArrowRight size={15} /></Link>
        </div>
      </section>
    </>
  );
}
