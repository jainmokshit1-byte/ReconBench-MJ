import { FileInput } from "lucide-react";

import { PageHeader } from "../components/PageHeader";

export function IngestionPage() {
  return (
    <>
      <PageHeader title="Ingestion" description="Bring purchase orders, goods-received notes, and invoices into one reviewable pool." />
      <section className="panel placeholder-panel">
        <div className="large-placeholder-icon"><FileInput size={22} /></div>
        <h2>File intake is coming in a later phase</h2>
        <p>This scaffold reserves the place for upload zones, file status, parsing progress, and document warnings.</p>
      </section>
    </>
  );
}
