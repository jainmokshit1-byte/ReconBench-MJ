import { Settings } from "lucide-react";

import { PageHeader } from "../components/PageHeader";

export function SettingsPage() {
  return (
    <>
      <PageHeader title="Settings" description="Product rules and workspace connections will be managed here." />
      <section className="panel placeholder-panel">
        <div className="large-placeholder-icon"><Settings size={22} /></div>
        <h2>Workspace settings are not configured</h2>
        <p>Tolerances, aliases, GST configuration, and optional AI settings are outside Phase 1.</p>
      </section>
    </>
  );
}
