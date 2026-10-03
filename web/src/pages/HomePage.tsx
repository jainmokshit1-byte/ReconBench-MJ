import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, ArrowRight, Check, FileInput, GitMerge, ListChecks, RefreshCw, Sparkles } from "lucide-react";
import { Link } from "react-router";

import { getReconciliationKpis, listRecentReconciliationRuns } from "../api/reconciliation";
import type { RunSummary } from "../api/reconciliation";
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

export function HomePage() {
  const { period } = usePeriod();
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const bounds = useMemo(() => periodBounds(period), [period]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    void Promise.all([
      getReconciliationKpis(bounds.from, bounds.to),
      listRecentReconciliationRuns(),
    ]).then(([nextKpis, nextRuns]) => {
      setKpis(nextKpis);
      setRuns(nextRuns.slice(0, 5));
    }).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "Workspace summary could not be loaded.");
    }).finally(() => setLoading(false));
  }, [bounds.from, bounds.to]);

  useEffect(() => {
    const timer = window.setTimeout(refresh, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const hasInvoices = Number(kpis?.["invoice_count"] ?? 0) > 0;
  const activeRun = runs.find((run) => run.status === "queued" || run.status === "running");
  const nextPath = !hasInvoices ? "/ingestion" : activeRun ? `/reconciliation?run_id=${activeRun.id}` : "/mapping";
  const nextLabel = !hasInvoices ? "Upload documents" : activeRun ? "View run progress" : "Review vendor mapping";
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
          <div className="panel-heading"><div><div className="panel-kicker">YOUR WORKFLOW</div><h2>Four steps to a clear close</h2></div><span className="quiet-badge">{activeRun ? "Reconciliation running" : "Ready when you are"}</span></div>
          <div className="workflow-steps">
            {[
              { label: "Ingest files", path: "/ingestion", icon: FileInput },
              { label: "Confirm mapping", path: "/mapping", icon: GitMerge },
              { label: "Review results", path: "/reconciliation", icon: ListChecks },
              { label: "Export workbook", path: "/reconciliation", icon: Check },
            ].map(({ label, path, icon: Icon }, index) => <Link className="workflow-step" key={label} to={path}><span className="step-number">{String(index + 1).padStart(2, "0")}</span><span className="step-icon"><Icon size={18} aria-hidden="true" /></span><span className="step-label">{label}</span>{index < 3 ? <span className="step-connector" aria-hidden="true" /> : null}</Link>)}
          </div>
          <div className="panel-footnote">Invoice is the reconciliation case anchor. PO and GRN records provide evidence and cumulative quantity balances.</div>
        </div>
        <div className="panel assist-panel">
          <div className="assist-mark"><Sparkles size={17} aria-hidden="true" /></div><div className="panel-kicker">OPTIONAL ASSIST</div><h2>AI Assist</h2>
          <p>Rules-only mode stays active by default. Provider selection and credentials remain unavailable until a provider and data-sharing policy are approved.</p>
          <Link className="text-button" to="/settings">Review settings <ArrowRight size={15} /></Link>
          <div className="assist-state"><span className="state-dot" /> Rules only</div>
        </div>
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
