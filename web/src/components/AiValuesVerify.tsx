import { useState } from "react";
import { CheckCircle2 } from "lucide-react";

import { aiValues, verifyAiValues } from "../api/ingestion";
import type { AiValue, DocumentDetails } from "../api/ingestion";

type SourceFocus = { page: number; bbox: number[] | null; source: string | null; ai_call_id: string | null };

function fieldName(item: AiValue, fieldLabels: Record<string, string>): string {
  const label = fieldLabels[item.field_name] ?? item.field_name;
  return item.line_no === null ? label : `Line ${item.line_no} · ${label}`;
}

function shown(value: string | null): string {
  return value === null || value === "" ? "—" : value;
}

/**
 * PD-4d: each value read by AI beside the rules (OCR) reading of the same page. A value
 * counts only after a person verifies it; "Verify all" covers only the values where the
 * two readings agree (Q8). The server checks the same rule again.
 */
export function AiValuesVerify({
  details,
  csrfToken,
  fieldLabels,
  onFocusSource,
  onSaved,
}: {
  details: DocumentDetails;
  csrfToken: string | null;
  fieldLabels: Record<string, string>;
  onFocusSource: (focus: SourceFocus | null) => void;
  onSaved: (details: DocumentDetails) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const values = aiValues(details);
  if (!values.length) return null;
  const document = details["document"] as Record<string, unknown> | undefined;
  const documentId = typeof document?.["id"] === "string" ? document["id"] : null;
  const version = typeof document?.["version"] === "number" ? document["version"] : null;
  const waiting = values.filter((item) => !item.verified);
  const agreeing = waiting.filter((item) => item.agrees);
  const canSend = Boolean(csrfToken && documentId && version !== null) && !busy;

  const send = async (choice: Parameters<typeof verifyAiValues>[2]) => {
    if (!csrfToken || !documentId || version === null) return;
    setBusy(true);
    setError(null);
    try {
      onSaved(await verifyAiValues(documentId, version, choice, csrfToken));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The values could not be verified.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="verify-unverified-fields" aria-label="Values read by AI">
      <h4>Values read by AI · {waiting.length} of {values.length} to verify</h4>
      <p className="preview-muted">A value read by AI counts only after you check it against the page.</p>
      {waiting.length ? (
        <div className="invoice-correction-toolbar">
          <button
            className="button button-subtle"
            type="button"
            disabled={!canSend || agreeing.length === 0}
            onClick={() => void send({ all_agreeing: true })}
          >
            Verify all {agreeing.length} that match the OCR reading
          </button>
          {waiting.length > agreeing.length ? (
            <span>{waiting.length - agreeing.length} differ from the OCR reading or have none: verify each one against the page.</span>
          ) : null}
        </div>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      <div className="preview-table-scroll">
        <table className="preview-line-table">
          <thead><tr><th>Field</th><th>AI reading</th><th>OCR reading</th><th>Page</th><th>Status</th></tr></thead>
          <tbody>
            {values.map((item) => {
              const name = fieldName(item, fieldLabels);
              return (
                <tr key={`${item.line_id ?? "header"}:${item.field_name}`} data-agrees={item.agrees ? "yes" : "no"}>
                  <td>{name}</td>
                  <td>{shown(item.value_text)}</td>
                  <td>{item.ocr_value_text === null ? "No OCR reading" : item.ocr_value_text}{item.ocr_value_text !== null && !item.agrees ? " · differs" : ""}</td>
                  <td>{item.page === null ? "—" : <button className="table-action" type="button" onClick={() => onFocusSource({ page: item.page as number, bbox: item.bbox, source: "ai", ai_call_id: null })}>Page {item.page}</button>}</td>
                  <td>{item.verified
                    ? <span className="quiet-badge"><CheckCircle2 size={13} /> Verified</span>
                    : <button className="table-action" type="button" disabled={!canSend} aria-label={`Verify ${name}`} onClick={() => void send({ values: [{ field_name: item.field_name, line_id: item.line_id }] })}>Verify</button>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
