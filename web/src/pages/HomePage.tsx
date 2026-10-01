import { ArrowRight, Check, CircleHelp, FileInput, GitMerge, ListChecks, Sparkles } from "lucide-react";
import { Link } from "react-router";

import { PageHeader } from "../components/PageHeader";

const steps = [
  { label: "Ingest files", path: "/ingestion", icon: FileInput },
  { label: "Confirm mapping", path: "/mapping", icon: GitMerge },
  { label: "Review results", path: "/reconciliation", icon: ListChecks },
  { label: "Export workbook", path: "/reconciliation", icon: Check },
];

export function HomePage() {
  return (
    <>
      <PageHeader
        eyebrow="WORKSPACE OVERVIEW"
        title="Good morning"
        description="Choose a period to see reconciliation progress and work that needs attention."
        action={<button className="button button-primary" type="button" disabled>Select a period</button>}
      />

      <section className="empty-period-banner" aria-label="No period selected">
        <div className="banner-icon"><CircleHelp size={19} aria-hidden="true" /></div>
        <div>
          <h2>Start with a period</h2>
          <p>Select a month in the top bar to see its files, mappings, and reconciliation results.</p>
        </div>
        <span className="banner-tag">No period selected</span>
      </section>

      <section className="home-grid" aria-label="Workspace setup">
        <div className="panel tracker-panel">
          <div className="panel-heading">
            <div>
              <div className="panel-kicker">YOUR WORKFLOW</div>
              <h2>Four steps to a clear close</h2>
            </div>
            <span className="quiet-badge">Ready when you are</span>
          </div>
          <div className="workflow-steps">
            {steps.map(({ label, path, icon: Icon }, index) => (
              <Link className="workflow-step" key={label} to={path}>
                <span className="step-number">{String(index + 1).padStart(2, "0")}</span>
                <span className="step-icon"><Icon size={18} aria-hidden="true" /></span>
                <span className="step-label">{label}</span>
                {index < steps.length - 1 ? <span className="step-connector" aria-hidden="true" /> : null}
              </Link>
            ))}
          </div>
          <div className="panel-footnote">Upload your purchase orders, receipts, and invoices to begin.</div>
        </div>

        <div className="panel assist-panel">
          <div className="assist-mark"><Sparkles size={17} aria-hidden="true" /></div>
          <div className="panel-kicker">OPTIONAL ASSIST</div>
          <h2>AI Assist</h2>
          <p>Keep reconciliation rules in control. Optional suggestions can help with difficult document fields and links.</p>
          <button className="text-button" type="button" disabled>
            Review settings <ArrowRight size={15} aria-hidden="true" />
          </button>
          <div className="assist-state"><span className="state-dot" /> Rules only</div>
        </div>
      </section>

      <section className="home-lower-grid">
        <div className="panel kpi-placeholder">
          <div className="panel-heading">
            <div><div className="panel-kicker">PERIOD SUMMARY</div><h2>Reconciliation at a glance</h2></div>
          </div>
          <div className="kpi-grid">
            {[["Invoices", "—"], ["Matched", "—"], ["Needs attention", "—"], ["At risk", "—"]].map(([label, value]) => (
              <div className="kpi-tile" key={label}>
                <span>{label}</span><strong>{value}</strong>
              </div>
            ))}
          </div>
          <div className="muted-note">Select a period to load server-calculated totals.</div>
        </div>
        <div className="panel recent-panel">
          <div className="panel-heading">
            <div><div className="panel-kicker">RECENT ACTIVITY</div><h2>Nothing here yet</h2></div>
          </div>
          <div className="recent-empty"><span className="empty-file-icon"><FileInput size={18} /></span><span>Your workspace activity will appear here.</span></div>
          <Link className="text-link" to="/ingestion">Go to Ingestion <ArrowRight size={15} /></Link>
        </div>
      </section>
    </>
  );
}
