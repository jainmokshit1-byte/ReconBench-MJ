import { useState } from "react";
import { ScanText } from "lucide-react";

import { AiApiError, saveAiSettings } from "../api/ai";
import type { AiScannedReader } from "../api/ai";
import { useAuth } from "../auth/useAuth";
import { useAiProviders } from "./useAiProviders";

const readerLabels: Record<AiScannedReader, string> = {
  rules: "Read with rules",
  ai: "Read with AI",
};

/** Settings → "Scanned invoices: read with rules / read with AI" (PD-2c); managers only, audited on the server. */
export function ScannedInvoicesSwitch() {
  const { user } = useAuth();
  const csrf = user?.csrf_token ?? "";
  const { settings, loadError, reload, setSettings } = useAiProviders();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const view = settings?.view ?? null;
  const canManage = view?.can_manage === true;
  const scanned = view?.scanned_invoices ?? null;

  const choose = async (reader: AiScannedReader) => {
    if (!settings || !scanned || reader === scanned.reader) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const next = await saveAiSettings({ scanned_invoices: reader }, settings.etag, csrf);
      setSettings(next);
      setNotice(reader === "ai"
        ? "New scanned invoices are now read with AI. Values read by AI need a person's check before they count."
        : "New scanned invoices are now read with rules. No AI call is made for them.");
    } catch (cause) {
      if (cause instanceof AiApiError && cause.status === 412) {
        setError("AI settings were changed by someone else. The latest settings are loaded; try again.");
        await reload();
      } else {
        setError(cause instanceof Error ? cause.message : "The setting could not be changed.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel settings-panel" aria-label="Scanned invoices">
      <div className="panel-heading"><div><div className="panel-kicker">AI ASSIST</div><h2>Scanned invoices</h2></div></div>
      <p>Digital invoices, PO files and GRN files are always read by rules. This setting chooses how scanned invoices are read; a PDF with any scanned page counts as scanned. The setting applies to new uploads only.</p>
      {view && !canManage ? <small className="ai-card-readonly">Only managers can change this setting.</small> : null}
      {loadError ? <div className="inline-error" role="alert">{loadError}</div> : null}
      {error ? <div className="inline-error" role="alert">{error}</div> : null}
      {notice ? <div className="mapping-info" role="status">{notice}</div> : null}
      {scanned ? (
        <fieldset className="ai-reader-choice" disabled={!canManage || busy}>
          <legend><ScanText size={15} aria-hidden="true" /> Scanned invoices</legend>
          {(["rules", "ai"] as const).map((reader) => (
            <label key={reader}>
              <input
                type="radio"
                name="scanned-invoice-reader"
                value={reader}
                checked={scanned.reader === reader}
                disabled={reader === "ai" && !scanned.ai_available && scanned.reader !== "ai"}
                onChange={() => void choose(reader)}
              />
              {readerLabels[reader]}{scanned.reader === reader ? " (current)" : ""}
            </label>
          ))}
          {!scanned.ai_available ? <small>Read with AI needs AI on with consent, and the company server chosen as page reader with a passing image and structured-answer check.</small> : null}
          {scanned.reader === "ai" && !scanned.ai_reading ? <small role="note">Set to AI, but AI reading is not available now, so scanned invoices are read with rules.</small> : null}
        </fieldset>
      ) : !loadError ? <p role="status">Loading…</p> : null}
    </section>
  );
}
