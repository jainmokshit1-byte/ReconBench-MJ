import { useEffect, useState } from "react";
import { AlertTriangle, Sparkles } from "lucide-react";

import { getDocumentAiSuggestions, suggestionKey } from "../api/ai";
import type { AiFieldSuggestion, AiSuggestionsView } from "../api/ai";

function shownValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(String).join(", ") || "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/**
 * Advisory suggestions for one document: H1 (PDF text) or the vision reader (page images).
 * Apply only fills the correction draft; the person still saves (and verifies) through the
 * normal correction flow. Conflicts present explicit current/suggested choices, and neither
 * choice is persisted until the person saves the correction.
 */
export function AiSuggestionsPanel({
  documentId,
  documentVersion,
  fieldLabels,
  applyBlockedReason,
  onApply,
  onKeepCurrent,
  onFocusSource,
  isRelevantSuggestion,
}: {
  documentId: string;
  documentVersion: number | null;
  fieldLabels: Record<string, string>;
  applyBlockedReason: (suggestion: AiFieldSuggestion) => string | null;
  onApply: (suggestion: AiFieldSuggestion, aiCallId: string) => void;
  onKeepCurrent?: (suggestion: AiFieldSuggestion) => void;
  onFocusSource?: (focus: { page: number; bbox: number[] | null; source: string | null; ai_call_id: string | null }) => void;
  isRelevantSuggestion?: (suggestion: AiFieldSuggestion) => boolean;
}) {
  const [view, setView] = useState<AiSuggestionsView | null>(null);
  const [ignored, setIgnored] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    let active = true;
    getDocumentAiSuggestions(documentId)
      .then((value) => { if (active) { setView(value); setIgnored(new Set()); } })
      .catch(() => { if (active) setView(null); });
    return () => { active = false; };
  }, [documentId, documentVersion]);

  if (!view || view.state === "not_applicable") return null;
  const heading = view.label ?? "AI suggestion";
  if (view.state !== "available") {
    const message = view.state === "pending"
      ? "AI suggestions are being prepared. The rules output below stays authoritative; you can keep working."
      : view.state === "expired"
        ? "The AI suggestions for this document expired and are no longer shown."
        : `AI suggestions are unavailable${view.code ? ` (${view.code})` : ""}. Review the fields against the source PDF.`;
    return <section className="ai-suggestions-panel" aria-label="AI suggestions"><div className="ai-suggestions-heading"><Sparkles size={14} aria-hidden="true" /><strong>{heading}</strong><span className="ai-advisory-tag">Advisory</span></div><p className="ai-suggestions-note">{message}</p></section>;
  }

  const versionMismatch = documentVersion === null || view.document_version !== documentVersion;
  const vision = view.source === "vision";
  const readOnlyReason = view.correctable ? null : "Corrections are not enabled for this document type";
  const relevant = view.suggestions.filter((item) => isRelevantSuggestion?.(item) ?? true);
  const visible = relevant.filter((item) => !ignored.has(suggestionKey(item)));
  const ignoredCount = relevant.length - visible.length;
  const label = (item: AiFieldSuggestion) => {
    const field = fieldLabels[item.field] ?? item.field.replaceAll("_", " ");
    return item.scope === "line" ? `Line ${item.line_no ?? "?"} ${field.toLowerCase()}` : field;
  };

  return (
    <section className="ai-suggestions-panel" aria-label="AI suggestions">
      <div className="ai-suggestions-heading">
        <Sparkles size={14} aria-hidden="true" />
        <strong>{heading}</strong>
        <span className="ai-advisory-tag">Advisory</span>
        {view.model ? <small>{view.model}</small> : null}
      </div>
      <p className="ai-suggestions-note">{vision
        ? "Each value was read from the page image (bank, contact and signature areas were blacked out) and is shown as printed. Apply copies it into the correction draft only; review it against the PDF and save to keep it."
        : "Each value is quoted from the page it was read on. Apply copies it into the correction draft only; review it against the PDF and save to keep it."}</p>
      {readOnlyReason ? <p className="ai-suggestions-note">Read-only: {readOnlyReason}. Use these values to check the document.</p> : null}
      {view.annotations.length ? (
        <div className="ai-suggestions-note" aria-label="Handwritten and stamped marks">
          <strong>Handwritten or stamped marks (information only, never used as fields):</strong>
          <ul className="ai-suggestion-list">
            {view.annotations.map((note, index) => <li key={`${note.page}:${index}`} className="ai-suggestion-row"><span>Page {note.page}: “{note.text}” ({note.looks_like.replaceAll("_", " ")})</span></li>)}
          </ul>
        </div>
      ) : null}
      {versionMismatch ? <p className="ai-suggestions-mismatch" role="alert"><AlertTriangle size={13} aria-hidden="true" /> These suggestions belong to version {view.document_version}, but this preview shows version {documentVersion ?? "unknown"}. Reload the preview before applying them.</p> : null}
      {visible.length ? (
        <ul className="ai-suggestion-list">
          {visible.map((item) => {
            const key = suggestionKey(item);
            const blocked = readOnlyReason ?? (versionMismatch ? "Version mismatch" : item.matches_current ? "Same as the current value" : applyBlockedReason(item));
            return (
              <li key={key} className="ai-suggestion-row">
                <div className="ai-suggestion-values">
                  <strong>{label(item)}</strong>
                  <span>Suggested <b>{item.value}</b></span>
                  <span>Current <b>{shownValue(item.current_value)}</b></span>
                  <small>Page {item.page}{vision ? " (as printed)" : ""}: “{item.quote}”</small>
                  {onFocusSource ? <button className="table-action" type="button" onClick={() => onFocusSource({ page: item.page, bbox: item.box, source: view.source, ai_call_id: view.ai_call_id })}>View source page {item.page}</button> : null}
                </div>
                <div className="ai-suggestion-actions">
                  <button className="table-action" type="button" disabled={blocked !== null || view.ai_call_id === null} title={blocked ?? undefined} aria-label={`${item.matches_current ? "Keep" : "Use suggested"} ${label(item)}`} onClick={() => { if (view.ai_call_id) onApply(item, view.ai_call_id); }}>{item.matches_current ? "Use current" : "Use suggested"}</button>
                  <button className="table-action" type="button" aria-label={`${item.matches_current ? "Dismiss" : "Keep current for"} ${label(item)}`} onClick={() => { if (!item.matches_current) onKeepCurrent?.(item); setIgnored((current) => new Set(current).add(key)); }}>{item.matches_current ? "Dismiss" : "Keep current"}</button>
                  {blocked && blocked !== "Version mismatch" ? <small>{blocked}</small> : null}
                </div>
              </li>
            );
          })}
        </ul>
      ) : <p className="ai-suggestions-note">{view.suggestions.length ? "All suggestions were ignored." : "The AI found no field to suggest for this document."}</p>}
      {ignoredCount ? <button className="button button-subtle" type="button" onClick={() => setIgnored(new Set())}>Show {ignoredCount} ignored</button> : null}
    </section>
  );
}
