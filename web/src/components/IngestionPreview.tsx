import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, FileText, X } from "lucide-react";
import { Link } from "react-router";

import {
  correctInvoiceDocument,
  createManualInvoice,
  displayError,
  getDocumentDetails,
  getDocuments,
  getFileContent,
  getFileErrors,
  isRecordValue,
} from "../api/ingestion";
import type { DocumentDetails, IngestionFile } from "../api/ingestion";
import { suggestionKey } from "../api/ai";
import type { AiFieldSuggestion, AiSuggestionRef } from "../api/ai";
import { useAuth } from "../auth/useAuth";
import { AiSuggestionsPanel } from "./AiSuggestionsPanel";

const PdfPreview = lazy(() => import("./PdfPreview").then((module) => ({ default: module.PdfPreview })));

type EvidenceRow = Record<string, unknown>;

interface PreviewData {
  documents: DocumentDetails[];
  rawText: string;
  errors: EvidenceRow[];
}

function record(value: unknown): Record<string, unknown> {
  return isRecordValue(value) ? value : {};
}

function list(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter(isRecordValue) : [];
}

function valueText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value)) return value.map(valueText).filter((item) => item !== "—").join(", ") || "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function confidence(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

type EditableHeaderField =
  | "doc_no" | "doc_date" | "vendor_name" | "vendor_gstin" | "buyer_gstin" | "currency"
  | "po_refs" | "grn_refs" | "challan_no" | "e_way_bill_no" | "irn" | "place_of_supply"
  | "taxable_total" | "cgst" | "sgst" | "igst" | "round_off" | "grand_total";

type CorrectionLine = {
  id: string | null;
  line_no: number;
  description: string;
  item_code: string;
  hsn_sac: string;
  uom: string;
  quantity: string;
  rate: string;
  amount: string;
};
const editableHeaderFields: [EditableHeaderField, string][] = [
  ["doc_no", "Document number"], ["doc_date", "Date"], ["vendor_name", "Vendor"],
  ["vendor_gstin", "Vendor GSTIN"], ["buyer_gstin", "Buyer GSTIN"], ["currency", "Currency"],
  ["po_refs", "PO references"], ["grn_refs", "GRN references"], ["challan_no", "Challan number"],
  ["e_way_bill_no", "E-way bill"], ["irn", "IRN"], ["place_of_supply", "Place of supply"],
  ["taxable_total", "Taxable total"], ["cgst", "CGST"], ["sgst", "SGST"],
  ["igst", "IGST"], ["round_off", "Round off"], ["grand_total", "Document total"],
];

const fieldLabels: Record<string, string> = {
  ...Object.fromEntries(editableHeaderFields),
  description: "Description", item_code: "Item code", hsn_sac: "HSN/SAC", uom: "UOM",
  quantity: "Quantity", rate: "Rate", amount: "Amount",
};

type CorrectionLineField = Exclude<keyof CorrectionLine, "id" | "line_no">;
const correctionLineFields: readonly CorrectionLineField[] = ["description", "item_code", "hsn_sac", "uom", "quantity", "rate", "amount"];

function isHeaderField(field: string): field is EditableHeaderField {
  return editableHeaderFields.some(([name]) => name === field);
}

function isLineField(field: string): field is CorrectionLineField {
  return (correctionLineFields as readonly string[]).includes(field);
}

function refList(value: string): string[] {
  return value.split(/[;,\n]+/).map((entry) => entry.trim()).filter(Boolean);
}

/** Whether the draft still holds the value that was applied from a suggestion. */
function draftHolds(
  suggestion: AiFieldSuggestion,
  draft: Record<EditableHeaderField, string>,
  lines: CorrectionLine[],
): boolean {
  if (suggestion.scope === "header") {
    if (!isHeaderField(suggestion.field)) return false;
    const current = draft[suggestion.field];
    return suggestion.field === "po_refs" || suggestion.field === "grn_refs"
      ? refList(current).includes(suggestion.value)
      : current.trim() === suggestion.value;
  }
  const line = lines.find((item) => item.line_no === suggestion.line_no);
  return Boolean(line && isLineField(suggestion.field) && line[suggestion.field].trim() === suggestion.value);
}

type AppliedSuggestion = { suggestion: AiFieldSuggestion; aiCallId: string };

const versionHeaderField: Partial<Record<EditableHeaderField, string>> = {
  cgst: "cgst_total",
  sgst: "sgst_total",
  igst: "igst_total",
};

function editableValue(value: unknown): string {
  if (Array.isArray(value)) return value.map((entry) => String(entry)).join("; ");
  if (value === null || value === undefined) return "";
  const text = String(value);
  return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value)
    ? text.slice(0, 10)
    : text;
}

function correctionLine(value: Record<string, unknown>, index: number): CorrectionLine {
  const asString = (item: unknown) => item === null || item === undefined ? "" : String(item);
  return {
    id: typeof value["id"] === "string" ? value["id"] : null,
    line_no: typeof value["line_no"] === "number" ? value["line_no"] : index + 1,
    description: asString(value["description"]),
    item_code: asString(value["item_code"]),
    hsn_sac: asString(value["hsn_sac"]),
    uom: asString(value["uom_as_read"] ?? value["uom"]),
    quantity: asString(value["quantity"]),
    rate: asString(value["rate"]),
    amount: asString(value["amount"]),
  };
}

function correctionDraft(version: Record<string, unknown>): Record<EditableHeaderField, string> {
  return Object.fromEntries(editableHeaderFields.map(([field]) => [
    field,
    editableValue(version[versionHeaderField[field] ?? field]),
  ])) as Record<EditableHeaderField, string>;
}

function DocumentCard({
  details,
  csrfToken,
  onSaved,
}: {
  details: DocumentDetails;
  csrfToken: string | null;
  onSaved: (details: DocumentDetails) => void;
}) {
  const document = record(details["document"]);
  const version = record(details["version"]);
  const lines = list(details["lines"]);
  const evidence = list(details["field_evidence"]);
  const needsVerification = version["needs_verification"] === true || document["needs_verification"] === true;
  const isInvoice = document["doc_type"] === "invoice";
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verified, setVerified] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<EditableHeaderField, string>>(() => correctionDraft(version));
  const [lineDraft, setLineDraft] = useState<CorrectionLine[]>(() => lines.map(correctionLine));
  const [applied, setApplied] = useState<Record<string, AppliedSuggestion>>({});
  const documentVersion = typeof document["version"] === "number" ? document["version"] : null;
  const headerFields = [
    ["Document number", version["doc_no"]],
    ["Date", version["doc_date"]],
    ["Vendor", version["vendor_name"]],
    ["Vendor GSTIN", version["vendor_gstin"]],
    ["PO references", version["po_refs"]],
    ["GRN references", version["grn_refs"]],
    ["Taxable total", version["taxable_total"]],
    ["Document total", version["grand_total"]],
  ] as const;

  const updateHeader = (field: EditableHeaderField, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
  };

  const updateLine = (lineIndex: number, field: keyof CorrectionLine, value: string) => {
    setLineDraft((current) => current.map((line, index) => index === lineIndex ? { ...line, [field]: value } : line));
  };

  const applyBlockedReason = (suggestion: AiFieldSuggestion): string | null => {
    if (!csrfToken) return "Sign in again to edit";
    if (suggestion.scope === "line" && !lineDraft.some((line) => line.line_no === suggestion.line_no)) {
      return `Line ${suggestion.line_no ?? "?"} is not in the draft`;
    }
    const entry = applied[suggestionKey(suggestion)];
    return entry && draftHolds(entry.suggestion, draft, lineDraft) ? "Applied to draft" : null;
  };

  // Apply fills the draft only. Nothing is sent until the person saves the correction.
  const applySuggestion = (suggestion: AiFieldSuggestion, aiCallId: string) => {
    const { field, value } = suggestion;
    if (suggestion.scope === "header") {
      if (!isHeaderField(field)) return;
      setDraft((current) => {
        if (field !== "po_refs" && field !== "grn_refs") return { ...current, [field]: value };
        const refs = refList(current[field]);
        return refs.includes(value) ? current : { ...current, [field]: [...refs, value].join("; ") };
      });
    } else {
      if (!isLineField(field)) return;
      setLineDraft((current) => current.map((line) => line.line_no === suggestion.line_no ? { ...line, [field]: value } : line));
    }
    setApplied((current) => ({ ...current, [suggestionKey(suggestion)]: { suggestion, aiCallId } }));
    setEditing(true);
    setSaveNotice(null);
  };

  const saveCorrections = async () => {
    const versionNumber = documentVersion;
    if (!csrfToken || versionNumber === null) return;
    setSaving(true);
    setSaveError(null);
    setSaveNotice(null);
    const numericFields = new Set<EditableHeaderField>(["taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total"]);
    const correction: Record<string, unknown> = {};
    for (const [field] of editableHeaderFields) {
      const value = draft[field].trim();
      if (field === "po_refs" || field === "grn_refs") {
        correction[field] = value ? value.split(/[;,\n]+/).map((entry) => entry.trim()).filter(Boolean) : [];
      } else if (numericFields.has(field)) {
        correction[field] = value || null;
      } else {
        correction[field] = value || null;
      }
    }
    correction["lines"] = lineDraft.map((line) => ({
      ...(line.id ? { id: line.id } : {}),
      line_no: line.line_no,
      description: line.description.trim() || null,
      item_code: line.item_code.trim() || null,
      hsn_sac: line.hsn_sac.trim() || null,
      uom: line.uom.trim() || null,
      quantity: line.quantity.trim() || null,
      rate: line.rate.trim() || null,
      amount: line.amount.trim() || null,
    }));
    correction["verified"] = verified;
    correction["reason"] = "Invoice fields reviewed or corrected from the document preview.";
    // Only values still equal to the applied suggestion are claimed as AI-suggested.
    const refs: AiSuggestionRef[] = Object.values(applied)
      .filter((entry) => draftHolds(entry.suggestion, draft, lineDraft))
      .map(({ suggestion, aiCallId }) => ({ ai_call_id: aiCallId, scope: suggestion.scope, line_no: suggestion.line_no, field: suggestion.field }));
    if (refs.length) correction["ai_suggestion_refs"] = refs;
    try {
      const updated = await correctInvoiceDocument(String(document["id"]), versionNumber, correction, csrfToken);
      const updatedVersion = record(updated["version"]);
      const updatedLines = list(updated["lines"]);
      setDraft(correctionDraft(updatedVersion));
      setLineDraft(updatedLines.map(correctionLine));
      setVerified(false);
      setApplied({});
      setSaveError(null);
      onSaved(updated);
      setEditing(false);
      setSaveNotice(verified ? "Corrections saved and this invoice marked reviewed." : "Corrections saved as a new version. It still needs verification.");
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : "Invoice corrections could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <article className="preview-document-card" id={typeof document["id"] === "string" ? `preview-document-${document["id"]}` : undefined}>
      <div className="preview-document-heading">
        <div>
          <span className="preview-document-type">{valueText(document["doc_type"]).toUpperCase()}</span>
          <h3>{valueText(version["doc_no"])}</h3>
        </div>
        <div className="preview-document-badges">
          {version["extraction_method"] === "pdf_ocr" ? <span className="ocr-chip">OCR-assisted</span> : null}
          {needsVerification ? <span className="verification-chip"><AlertTriangle size={13} /> Needs verification</span> : <span className="quiet-badge">Parsed</span>}
        </div>
      </div>
      {isInvoice ? <div className="invoice-correction-toolbar"><button className="button button-subtle" type="button" onClick={() => { if (editing) { setDraft(correctionDraft(version)); setLineDraft(lines.map(correctionLine)); setVerified(false); setApplied({}); } setEditing((current) => !current); setSaveError(null); }}>{editing ? "Cancel editing" : "Edit extracted invoice"}</button>{saveNotice ? <span role="status">{saveNotice}</span> : null}</div> : null}
      {typeof document["id"] === "string" ? <AiSuggestionsPanel documentId={document["id"]} documentVersion={documentVersion} fieldLabels={fieldLabels} applyBlockedReason={applyBlockedReason} onApply={applySuggestion} /> : null}
      <dl className="preview-fields-grid">
        {headerFields.map(([label, fieldValue]) => (
          <div key={label}><dt>{label}</dt><dd>{valueText(fieldValue)}</dd></div>
        ))}
      </dl>
      <h4>Extracted line items</h4>
      {lines.length ? (
        <div className="preview-table-scroll">
          <table className="preview-line-table">
            <thead><tr><th>Line</th><th>Description</th><th>Code</th><th>Qty</th><th>Rate</th><th>Amount</th><th>UOM</th></tr></thead>
            <tbody>
              {lines.map((line, index) => (
                <tr key={String(line["id"] ?? index)}>
                  <td>{valueText(line["line_no"])}</td>
                  <td>{valueText(line["description"])}</td>
                  <td>{valueText(line["item_code"] ?? line["hsn_sac"])}</td>
                  <td>{valueText(line["quantity"])}</td>
                  <td>{valueText(line["rate"])}</td>
                  <td>{valueText(line["amount"])}</td>
                  <td>{valueText(line["uom_as_read"] ?? line["uom"])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <p className="preview-muted">No line items were extracted from this record.</p>}
      <h4>Field evidence</h4>
      {evidence.length ? (
        <div className="evidence-list">
          {evidence.map((field, index) => {
            const score = confidence(field["confidence"]);
            const low = score !== null && score < 0.7;
            return (
              <div className={`evidence-row${low ? " is-low-confidence" : ""}`} key={String(field["id"] ?? index)}>
                <strong>{valueText(field["field_name"]).replaceAll("_", " ")}</strong>
                <span>{valueText(field["value_text"])}</span>
                <span className="evidence-source">{valueText(field["source"])}</span>
                <span className="evidence-confidence">
                  {score === null ? "Confidence unavailable" : `${Math.round(score * 100)}%`}
                  {low ? <AlertTriangle size={13} aria-label="Low confidence" /> : null}
                </span>
                {typeof field["snippet"] === "string" && field["snippet"] ? <small>{field["snippet"]}</small> : null}
              </div>
            );
          })}
        </div>
      ) : <p className="preview-muted">No field-level evidence is available.</p>}
      {editing ? <section className="invoice-correction-editor" aria-label="Correct extracted invoice fields">
        <h4>Review and correct invoice fields</h4>
        <p>Changes create an auditable new document version. The source PDF remains unchanged.</p>
        <div className="invoice-correction-grid">
          {editableHeaderFields.map(([field, label]) => <label key={field}><span>{label}</span><input aria-label={label} type={field === "doc_date" ? "date" : "text"} inputMode={field === "taxable_total" || field === "cgst" || field === "sgst" || field === "igst" || field === "round_off" || field === "grand_total" ? "decimal" : undefined} value={draft[field]} onChange={(event) => updateHeader(field, event.currentTarget.value)} /></label>)}
        </div>
        <div className="invoice-correction-lines-heading"><h4>Invoice line items</h4><button className="button button-subtle" type="button" onClick={() => setLineDraft((current) => [...current, { id: null, line_no: Math.max(0, ...current.map((line) => line.line_no)) + 1, description: "", item_code: "", hsn_sac: "", uom: "", quantity: "", rate: "", amount: "" }])}>Add line</button></div>
        <div className="preview-table-scroll"><table className="preview-line-table invoice-correction-table"><thead><tr><th>Line</th><th>Description</th><th>Item code</th><th>HSN/SAC</th><th>Qty</th><th>Rate</th><th>Amount</th><th>UOM</th><th>Action</th></tr></thead><tbody>
          {lineDraft.map((line, index) => <tr key={line.id ?? `new-${index}`}><td>{line.line_no}</td>{(["description", "item_code", "hsn_sac", "quantity", "rate", "amount", "uom"] as const).map((field) => <td key={field}><input aria-label={`Line ${line.line_no} ${field.replaceAll("_", " ")}`} inputMode={field === "quantity" || field === "rate" || field === "amount" ? "decimal" : undefined} value={line[field]} onChange={(event) => updateLine(index, field, event.currentTarget.value)} /></td>)}<td><button className="button button-subtle" type="button" aria-label={`Remove line ${line.line_no}`} onClick={() => setLineDraft((current) => current.filter((_entry, rowIndex) => rowIndex !== index))}>Remove</button></td></tr>)}
          {lineDraft.length === 0 ? <tr><td colSpan={9}>No lines. Add a line if the invoice contains line items.</td></tr> : null}
        </tbody></table></div>
        <label className="invoice-verified-check"><input type="checkbox" checked={verified} onChange={(event) => setVerified(event.currentTarget.checked)} /> I reviewed the source PDF and confirm the displayed invoice data is correct.</label>
        {saveError ? <p className="inline-error" role="alert">{saveError}</p> : null}
        <button className="button button-primary" type="button" disabled={saving || !csrfToken} onClick={() => void saveCorrections()}>{saving ? "Saving version…" : verified ? "Save and mark reviewed" : "Save corrections"}</button>
      </section> : null}
    </article>
  );
}

function ManualInvoiceEntry({
  sourceFileId,
  csrfToken,
  onCreated,
}: {
  sourceFileId: string;
  csrfToken: string | null;
  onCreated: (details: DocumentDetails) => void;
}) {
  const [draft, setDraft] = useState<Record<EditableHeaderField, string>>(() => correctionDraft({}));
  const [lineDraft, setLineDraft] = useState<CorrectionLine[]>([]);
  const [verified, setVerified] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!csrfToken) return;
    setSaving(true);
    setError(null);
    const numericFields = new Set<EditableHeaderField>(["taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total"]);
    const invoice: Record<string, unknown> = {};
    for (const [field] of editableHeaderFields) {
      const value = draft[field].trim();
      if (field === "po_refs" || field === "grn_refs") {
        invoice[field] = value ? value.split(/[;,\n]+/).map((entry) => entry.trim()).filter(Boolean) : [];
      } else {
        invoice[field] = value || null;
      }
      if (numericFields.has(field)) invoice[field] = value || null;
    }
    invoice["lines"] = lineDraft
      .filter((line) => [line.description, line.item_code, line.hsn_sac, line.uom, line.quantity, line.rate, line.amount].some((value) => value.trim()))
      .map((line) => ({
        line_no: line.line_no,
        description: line.description.trim() || null,
        item_code: line.item_code.trim() || null,
        hsn_sac: line.hsn_sac.trim() || null,
        uom: line.uom.trim() || null,
        quantity: line.quantity.trim() || null,
        rate: line.rate.trim() || null,
        amount: line.amount.trim() || null,
      }));
    invoice["verified"] = verified;
    invoice["reason"] = "Reviewer entered invoice details against the source file.";
    try {
      const details = await createManualInvoice(sourceFileId, invoice, csrfToken);
      onCreated(details);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Manual invoice entry could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="manual-invoice-entry" aria-label="Create invoice record manually">
      <h3>No parsed invoice record</h3>
      <p>Use the source PDF beside this form to enter the invoice. Values are saved with manual provenance; an unchecked review remains pending.</p>
      <form onSubmit={(event) => void submit(event)}>
        <div className="manual-invoice-fields">
          {editableHeaderFields.map(([field, label]) => (
            <label key={field}>
              <span>{label}</span>
              <input
                aria-label={label}
                type={field === "doc_date" ? "date" : "text"}
                inputMode={["taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total"].includes(field) ? "decimal" : undefined}
                value={draft[field]}
                onChange={(event) => {
                  const value = event.currentTarget.value;
                  setDraft((current) => ({ ...current, [field]: value }));
                }}
              />
            </label>
          ))}
        </div>
        <div className="manual-invoice-lines">
          <div className="panel-heading"><h4>Invoice lines</h4><button className="button button-subtle" type="button" onClick={() => setLineDraft((current) => [...current, { id: null, line_no: current.length ? Math.max(...current.map((line) => line.line_no)) + 1 : 1, description: "", item_code: "", hsn_sac: "", uom: "", quantity: "", rate: "", amount: "" }])}>Add line</button></div>
          {lineDraft.map((line, index) => (
            <fieldset className="manual-invoice-line" key={line.line_no}>
              <legend>Line {line.line_no}</legend>
              {(["description", "item_code", "hsn_sac", "uom", "quantity", "rate", "amount"] as const).map((field) => (
                <label key={field}><span>{field.replaceAll("_", " ")}</span><input aria-label={`Line ${line.line_no} ${field.replaceAll("_", " ")}`} value={line[field]} onChange={(event) => { const value = event.currentTarget.value; setLineDraft((current) => current.map((row, rowIndex) => rowIndex === index ? { ...row, [field]: value } : row)); }} /></label>
              ))}
              <button className="button button-subtle" type="button" onClick={() => setLineDraft((current) => current.filter((_, rowIndex) => rowIndex !== index))}>Remove line</button>
            </fieldset>
          ))}
        </div>
        <label className="invoice-verified-check"><input type="checkbox" checked={verified} onChange={(event) => setVerified(event.currentTarget.checked)} /> I checked these manually entered values against the source invoice.</label>
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        <button className="button button-primary" type="submit" disabled={!csrfToken || saving}>{saving ? "Saving manual invoice…" : "Create manual invoice"}</button>
      </form>
    </section>
  );
}

export function IngestionPreview({
  file,
  batchId,
  focusDocumentId,
  onClose,
}: {
  file: IngestionFile;
  batchId: string | null;
  focusDocumentId?: string | null;
  onClose: () => void;
}) {
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<"records" | "raw">("records");
  const [data, setData] = useState<PreviewData>({ documents: [], rawText: "", errors: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rawPage, setRawPage] = useState(0);
  const lines = useMemo(() => data.rawText.split(/\r?\n/), [data.rawText]);
  const pageSize = 120;
  const rawPageCount = Math.max(1, Math.ceil(lines.length / pageSize));

  useEffect(() => {
    if (!focusDocumentId || loading) return;
    document.getElementById(`preview-document-${focusDocumentId}`)?.scrollIntoView({ block: "start" });
  }, [data.documents, focusDocumentId, loading]);

  useEffect(() => {
    let active = true;
    const contentPromise = file.format === "pdf"
      ? Promise.resolve("")
      : getFileContent(file.id).then((response) => response.text());
    const documentsPromise = batchId
      ? getDocuments(batchId, file.id).then((rows) => Promise.all(rows.map((row) => getDocumentDetails(row.id))))
      : Promise.resolve([] as DocumentDetails[]);
    const errorsPromise = getFileErrors(file.id).catch(() => [] as Record<string, unknown>[]);
    void Promise.all([contentPromise, documentsPromise, errorsPromise])
      .then(([rawText, documents, errors]) => {
        if (active) setData({ rawText, documents, errors });
      })
      .catch((cause: unknown) => {
        if (active) setError(displayError(cause));
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [batchId, file.format, file.id]);

  const documentIdFromError = (item: EvidenceRow): string | null =>
    typeof item["document_id"] === "string" ? item["document_id"] : null;

  const replaceSavedDocument = (updated: DocumentDetails) => {
    const updatedId = String(record(updated["document"])["id"] ?? "");
    setData((current) => {
      const exists = current.documents.some((item) => String(record(item["document"])["id"] ?? "") === updatedId);
      return {
        ...current,
        documents: exists
          ? current.documents.map((item) => String(record(item["document"])["id"] ?? "") === updatedId ? updated : item)
          : [...current.documents, updated],
      };
    });
  };

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="dialog-card ingestion-preview-dialog" role="dialog" aria-modal="true" aria-labelledby="preview-title">
        <div className="dialog-heading">
          <div>
            <div className="panel-kicker">DOCUMENT PREVIEW</div>
            <h2 id="preview-title">{file.display_name}</h2>
            <p>{file.zone.toUpperCase()} · {file.format.toUpperCase()} · {file.status.replaceAll("_", " ")}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close preview" onClick={onClose}><X size={18} /></button>
        </div>
        {file.format === "pdf" ? (
          <div className="preview-body pdf-preview-body">
            <Suspense fallback={<p className="preview-message" role="status">Loading secure PDF viewer…</p>}>
              <PdfPreview key={file.id} fileId={file.id} />
            </Suspense>
            {data.documents.length ? <div className="pdf-extracted-records">{data.documents.map((details, index) => <DocumentCard key={String(record(details["document"])["id"] ?? index)} details={details} csrfToken={user?.csrf_token ?? null} onSaved={replaceSavedDocument} />)}</div> : file.zone === "invoice" && !loading ? <ManualInvoiceEntry sourceFileId={file.id} csrfToken={user?.csrf_token ?? null} onCreated={replaceSavedDocument} /> : null}
          </div>
        ) : (
          <>
            <div className="preview-tabs" role="tablist" aria-label="Preview mode">
              <button type="button" role="tab" aria-selected={activeTab === "records"} className={activeTab === "records" ? "is-active" : ""} onClick={() => setActiveTab("records")}><FileText size={14} /> Parsed records</button>
              <button type="button" role="tab" aria-selected={activeTab === "raw"} className={activeTab === "raw" ? "is-active" : ""} onClick={() => setActiveTab("raw")}>View raw</button>
            </div>
            <div className="preview-body">
              {activeTab === "records" ? (
                  data.documents.length ? data.documents.map((details, index) => <DocumentCard key={String(record(details["document"])["id"] ?? index)} details={details} csrfToken={user?.csrf_token ?? null} onSaved={replaceSavedDocument} />) : (
                    <>
                      <section className="preview-empty">
                        <FileText size={25} aria-hidden="true" />
                        <h3>No parsed document record yet</h3>
                        <p>The file may still be processing, or it may have failed before a document could be created.</p>
                      </section>
                      {file.zone === "invoice" && !loading ? <ManualInvoiceEntry sourceFileId={file.id} csrfToken={user?.csrf_token ?? null} onCreated={replaceSavedDocument} /> : null}
                    </>
                  )
              ) : (
                <section className="raw-file-preview">
                  <div className="raw-preview-toolbar">
                    <span>{lines.length} lines · showing {Math.min(pageSize, Math.max(0, lines.length - rawPage * pageSize))}</span>
                    <div>
                      <button className="icon-button" type="button" aria-label="Previous raw page" disabled={rawPage <= 0} onClick={() => setRawPage((page) => Math.max(0, page - 1))}><ChevronLeft size={15} /></button>
                      <span>{rawPage + 1} / {rawPageCount}</span>
                      <button className="icon-button" type="button" aria-label="Next raw page" disabled={rawPage + 1 >= rawPageCount} onClick={() => setRawPage((page) => Math.min(rawPageCount - 1, page + 1))}><ChevronRight size={15} /></button>
                    </div>
                  </div>
                  <pre>{lines.slice(rawPage * pageSize, (rawPage + 1) * pageSize).join("\n") || "No text content"}</pre>
                </section>
              )}
            </div>
          </>
        )}
        {loading ? <p className="preview-loading" role="status">Loading extracted fields and warnings…</p> : null}
        {error ? <p className="inline-error" role="alert">{error}</p> : null}
        {data.errors.length ? (
          <div className="preview-warning-list" aria-label="File warnings">
            <strong>Processing history</strong>
            {data.errors.slice(-6).reverse().map((item, index) => {
              const documentId = documentIdFromError(item);
              return (
                <div key={`${String(item["occurred_at"] ?? index)}-${index}`}>
                  <span>{valueText(item["error_code"] ?? item["stage"])} · {valueText(item["message"] ?? item["outcome"])}</span>
                  {documentId ? <Link to={`/mapping?documentId=${encodeURIComponent(documentId)}`}>Open document</Link> : null}
                </div>
              );
            })}
          </div>
        ) : null}
      </section>
    </div>
  );
}
