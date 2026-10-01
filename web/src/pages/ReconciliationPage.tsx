import { ListChecks } from "lucide-react";

import { PageHeader } from "../components/PageHeader";

export function ReconciliationPage() {
  return (
    <>
      <PageHeader title="Reconciliation" description="See invoice decisions, the evidence behind each result, and actions that need review." />
      <section className="panel placeholder-panel">
        <div className="large-placeholder-icon"><ListChecks size={22} /></div>
        <h2>No reconciliation run yet</h2>
        <p>Cases and payable totals will be calculated by the server after a period has been ingested and mapped.</p>
      </section>
    </>
  );
}
