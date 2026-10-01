import { GitMerge } from "lucide-react";

import { PageHeader } from "../components/PageHeader";

export function MappingPage() {
  return (
    <>
      <PageHeader title="Mapping" description="Review vendor groups and the evidence that links invoices, receipts, and purchase orders." />
      <section className="panel placeholder-panel">
        <div className="large-placeholder-icon"><GitMerge size={22} /></div>
        <h2>Mapping is ready for its first data set</h2>
        <p>Vendor grouping, link evidence, and review controls will be added after ingestion and engine foundations.</p>
      </section>
    </>
  );
}
