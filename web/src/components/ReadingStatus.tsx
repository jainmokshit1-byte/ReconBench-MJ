import { useState } from "react";
import { AlertTriangle, Clock } from "lucide-react";

import { readWithRules, rereadWithRules, retryReading } from "../api/ingestion";
import type { DocumentDetails } from "../api/ingestion";
import { readingFailureText } from "./readingFailure";

/**
 * PD-2f: a scanned invoice read with the switch on AI waits for its reading; nothing is
 * used in mapping or reconciliation until the AI version is current or a person picks
 * "Read with rules". After a failure a person picks Retry or "Read with rules". A ready
 * invoice, PO or GRN gets a subtle "Read again with rules" (confirm first): the rules read
 * the stored file once more and the result becomes a new version, never AI. Renders nothing
 * when the server does not send a reading state.
 */
export function ReadingStatus({
  details,
  csrfToken,
  onSaved,
}: {
  details: DocumentDetails;
  csrfToken: string | null;
  onSaved: (details: DocumentDetails) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const document = details["document"] as Record<string, unknown> | undefined;
  const state = document?.["reading_state"];
  const documentId = typeof document?.["id"] === "string" ? document["id"] : null;
  const version = typeof document?.["version"] === "number" ? document["version"] : null;
  const code = typeof document?.["reading_failure_code"] === "string" ? document["reading_failure_code"] : null;
  const canSend = Boolean(csrfToken && documentId && version !== null) && !busy;
  const docType = document?.["doc_type"];
  const canReread = state === "ready" && (docType === "invoice" || docType === "po" || docType === "grn");
  if (state !== "waiting" && state !== "failed" && !canReread) return null;

  const send = async (action: typeof retryReading) => {
    if (!csrfToken || !documentId || version === null) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await action(documentId, version, csrfToken));
      setConfirming(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The choice could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  if (canReread) {
    // A ready document: a person may have the rules read its stored file again (never AI).
    return (
      <section className="verify-unverified-fields" aria-label="Read again with rules">
        {confirming ? (
          <>
            <p className="preview-muted">
              This replaces the current values with a fresh rules reading. Earlier versions are kept.
            </p>
            <div className="invoice-correction-toolbar">
              <button className="button button-primary" type="button" disabled={!canSend} onClick={() => void send(rereadWithRules)}>
                Read again
              </button>
              <button className="button button-subtle" type="button" disabled={busy} onClick={() => { setConfirming(false); setError(null); }}>
                Cancel
              </button>
            </div>
          </>
        ) : (
          <div className="invoice-correction-toolbar">
            <button className="button button-subtle" type="button" disabled={!canSend} onClick={() => setConfirming(true)}>
              Read again with rules
            </button>
          </div>
        )}
        {error ? <p role="alert">{error}</p> : null}
      </section>
    );
  }

  return (
    <section className="verify-unverified-fields" aria-label="AI reading">
      {state === "waiting" ? (
        <>
          <h4><Clock size={14} /> Waiting for reading</h4>
          <p className="preview-muted">
            The AI is reading this scanned invoice. Until it is done it is not used in mapping or reconciliation.
          </p>
        </>
      ) : (
        <>
          <h4><AlertTriangle size={14} /> AI reading failed: {readingFailureText(code)}</h4>
          <p className="preview-muted">
            This invoice is not used in mapping or reconciliation until you choose: read it again with AI, or use the rules reading below.
          </p>
        </>
      )}
      <div className="invoice-correction-toolbar">
        {state === "failed" ? (
          <button className="button button-primary" type="button" disabled={!canSend} onClick={() => void send(retryReading)}>
            Retry
          </button>
        ) : null}
        <button className="button button-subtle" type="button" disabled={!canSend} onClick={() => void send(readWithRules)}>
          Read with rules
        </button>
      </div>
      {error ? <p role="alert">{error}</p> : null}
    </section>
  );
}

/**
 * Small status chip for a document in a documents list or card.
 * Shows only for 'waiting' and 'failed'; renders nothing for 'ready' or when absent.
 */
export function ReadingChip({
  document,
  readingState,
  readingFailureCode,
}: {
  document?: Record<string, unknown> | null;
  readingState?: string | null;
  readingFailureCode?: string | null;
}) {
  const state = readingState ?? document?.["reading_state"];
  if (state !== "waiting" && state !== "failed") return null;
  const rawCode = readingFailureCode ?? document?.["reading_failure_code"];
  const code = typeof rawCode === "string" ? rawCode : null;
  if (state === "waiting") {
    return (
      <span className="status-chip status-progress" aria-label="Reading state">
        <Clock size={12} /> Waiting for reading
      </span>
    );
  }
  return (
    <span className="status-chip status-danger" aria-label="Reading state">
      <AlertTriangle size={12} /> Reading failed: {readingFailureText(code)}
    </span>
  );
}
