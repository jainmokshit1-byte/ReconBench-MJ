import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, FileText, X } from "lucide-react";
import { Link } from "react-router";

import {
  correctDocument,
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
import { buildAccuracyCorrectionExport } from "../utils/accuracyCorrectionExport";
import { AiSuggestionsPanel } from "./AiSuggestionsPanel";
import { AiValuesVerify } from "./AiValuesVerify";

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
  | "taxable_total" | "cgst" | "sgst" | "igst" | "round_off" | "grand_total"
  | "revision" | "status" | "ship_to" | "payment_terms" | "delivery_date" | "supplier_invoice_no";

type CorrectionLine = {
  id: string | null;
  line_no: number;
  description: string;
  item_code: string;
  hsn_sac: string;
  uom: string;
  quantity: string;
  received_qty: string;
  rejected_qty: string;
  accepted_qty: string;
  rate: string;
  discount: string;
  amount: string;
  tax_rate: string;
  cgst_amount: string;
  sgst_amount: string;
  igst_amount: string;
  po_ref: string;
  po_line_ref: string;
  line_type: string;
};
const invoiceHeaderFields: [EditableHeaderField, string][] = [
  ["doc_no", "Document number"], ["doc_date", "Date"], ["vendor_name", "Vendor"],
  ["vendor_gstin", "Vendor GSTIN"], ["buyer_gstin", "Buyer GSTIN"], ["currency", "Currency"],
  ["po_refs", "PO references"], ["grn_refs", "GRN references"], ["challan_no", "Challan number"],
  ["e_way_bill_no", "E-way bill"], ["irn", "IRN"], ["place_of_supply", "Place of supply"],
  ["taxable_total", "Taxable total"], ["cgst", "CGST"], ["sgst", "SGST"],
  ["igst", "IGST"], ["round_off", "Round off"], ["grand_total", "Document total"],
];

const commonCorrectionHeaders: [EditableHeaderField, string][] = [
  ["doc_no", "Document number"], ["doc_date", "Date"], ["vendor_name", "Vendor"],
  ["vendor_gstin", "Vendor GSTIN"], ["buyer_gstin", "Buyer GSTIN"], ["currency", "Currency"],
  ["taxable_total", "Taxable total"], ["cgst", "CGST"], ["sgst", "SGST"],
  ["igst", "IGST"], ["round_off", "Round off"], ["grand_total", "Document total"],
];

const poHeaderFields: [EditableHeaderField, string][] = [
  ...commonCorrectionHeaders,
  ["revision", "Revision"], ["status", "Status"], ["ship_to", "Ship to"],
  ["payment_terms", "Payment terms"], ["delivery_date", "Delivery date"],
];

const grnHeaderFields: [EditableHeaderField, string][] = [
  ...commonCorrectionHeaders,
  ["po_refs", "PO references"], ["challan_no", "Challan number"],
  ["supplier_invoice_no", "Supplier invoice number"],
];

const allEditableHeaderFields: [EditableHeaderField, string][] = [...invoiceHeaderFields, ...poHeaderFields, ...grnHeaderFields]
  .filter(([field], index, fields) => fields.findIndex(([candidate]) => candidate === field) === index);

const correctionLineFieldsByType: Record<string, readonly CorrectionLineField[]> = {
  invoice: ["description", "item_code", "hsn_sac", "uom", "quantity", "rate", "amount"],
  po: ["line_type", "description", "item_code", "hsn_sac", "uom", "quantity", "rate", "discount", "amount", "tax_rate", "cgst_amount", "sgst_amount", "igst_amount", "po_ref", "po_line_ref"],
  grn: ["line_type", "description", "item_code", "hsn_sac", "uom", "quantity", "received_qty", "rejected_qty", "accepted_qty", "rate", "discount", "amount", "tax_rate", "cgst_amount", "sgst_amount", "igst_amount", "po_ref", "po_line_ref"],
};

function headerFieldsFor(docType: string): [EditableHeaderField, string][] {
  if (docType === "invoice") return invoiceHeaderFields;
  if (docType === "po") return poHeaderFields;
  if (docType === "grn") return grnHeaderFields;
  return [];
}

function lineFieldsFor(docType: string): readonly CorrectionLineField[] {
  return correctionLineFieldsByType[docType] ?? [];
}

const fieldLabels: Record<string, string> = {
  ...Object.fromEntries([...invoiceHeaderFields, ...poHeaderFields, ...grnHeaderFields]),
  description: "Description", item_code: "Item code", hsn_sac: "HSN/SAC", uom: "UOM",
  quantity: "Quantity", received_qty: "Received quantity", rejected_qty: "Rejected quantity",
  accepted_qty: "Accepted quantity", rate: "Rate", discount: "Discount", amount: "Amount",
  tax_rate: "Tax rate", cgst_amount: "CGST amount", sgst_amount: "SGST amount",
  igst_amount: "IGST amount", po_ref: "PO reference", po_line_ref: "PO line reference",
  line_type: "Line type",
};

type CorrectionLineField = Exclude<keyof CorrectionLine, "id" | "line_no">;
function isHeaderField(field: string): field is EditableHeaderField {
  return [...invoiceHeaderFields, ...poHeaderFields, ...grnHeaderFields].some(([name]) => name === field);
}

function isLineField(field: string): field is CorrectionLineField {
  return Object.values(correctionLineFieldsByType).some((fields) => (fields as readonly string[]).includes(field));
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
    received_qty: asString(value["received_qty"]),
    rejected_qty: asString(value["rejected_qty"]),
    accepted_qty: asString(value["accepted_qty"]),
    rate: asString(value["rate"]),
    discount: asString(value["discount"]),
    amount: asString(value["amount"]),
    tax_rate: asString(value["tax_rate"]),
    cgst_amount: asString(value["cgst_amount"]),
    sgst_amount: asString(value["sgst_amount"]),
    igst_amount: asString(value["igst_amount"]),
    po_ref: asString(value["po_ref"]),
    po_line_ref: asString(value["po_line_ref"]),
    line_type: asString(value["line_type"] ?? "goods"),
  };
}

function emptyCorrectionLine(lineNo: number): CorrectionLine {
  return {
    id: null,
    line_no: lineNo,
    description: "",
    item_code: "",
    hsn_sac: "",
    uom: "",
    quantity: "",
    received_qty: "",
    rejected_qty: "",
    accepted_qty: "",
    rate: "",
    discount: "",
    amount: "",
    tax_rate: "",
    cgst_amount: "",
    sgst_amount: "",
    igst_amount: "",
    po_ref: "",
    po_line_ref: "",
    line_type: "goods",
  };
}

function correctionDraft(version: Record<string, unknown>): Record<EditableHeaderField, string> {
  return Object.fromEntries(allEditableHeaderFields.map(([field]) => [
    field,
    editableValue(version[versionHeaderField[field] ?? field]),
  ])) as Record<EditableHeaderField, string>;
}

type PdfSourceFocus = {
  page: number;
  bbox: number[] | null;
  source: string | null;
  ai_call_id: string | null;
};

function needsVerification(details: DocumentDetails): boolean {
  const document = record(details["document"]);
  const version = record(details["version"]);
  return version["needs_verification"] === true || document["needs_verification"] === true;
}

function reviewFieldSet(version: Record<string, unknown>, evidence: EvidenceRow[]): { header: Set<string>; lines: Set<number> } {
  const extras = record(version["extras"]);
  const listed = Array.isArray(extras["review_fields"]) ? extras["review_fields"] : [];
  const header = new Set<string>();
  const lines = new Set<number>();
  for (const raw of listed) {
    if (typeof raw !== "string") continue;
    const line = raw.match(/^line\s+(\d+)$/i);
    if (line) {
      lines.add(Number(line[1]));
    } else if (raw === "vendor") {
      header.add("vendor_name");
      header.add("vendor_gstin");
    } else {
      header.add(raw);
    }
  }
  if (listed.length === 0) {
    for (const item of evidence) {
      if (item["source"] === "manual" || typeof item["field_name"] !== "string") continue;
      const lineId = item["doc_line_id"];
      if (typeof lineId === "string" && lineId) continue;
      header.add(item["field_name"]);
    }
  }
  return { header, lines };
}

function fieldEvidence(evidence: EvidenceRow[], field: string, lineId?: string | null): EvidenceRow | null {
  const matches = evidence.filter((item) =>
    item["field_name"] === field
      && (lineId ? item["doc_line_id"] === lineId : !item["doc_line_id"]),
  );
  return matches.at(-1) ?? null;
}

function evidenceFocus(evidence: EvidenceRow | null): PdfSourceFocus | null {
  if (!evidence || typeof evidence["page"] !== "number") return null;
  const bbox = Array.isArray(evidence["bbox"]) && evidence["bbox"].every((value) => typeof value === "number")
    ? evidence["bbox"] as number[]
    : null;
  return {
    page: evidence["page"],
    bbox,
    source: typeof evidence["source"] === "string" ? evidence["source"] : null,
    ai_call_id: typeof evidence["ai_call_id"] === "string" ? evidence["ai_call_id"] : null,
  };
}

function DocumentCard({
  details,
  csrfToken,
  onSaved,
  reviewOnly,
  onFocusSource,
}: {
  details: DocumentDetails;
  csrfToken: string | null;
  onSaved: (details: DocumentDetails) => void;
  reviewOnly: boolean;
  onFocusSource: (focus: PdfSourceFocus | null) => void;
}) {
  const document = record(details["document"]);
  const version = record(details["version"]);
  const lines = list(details["lines"]);
  const evidence = list(details["field_evidence"]);
  const needsVerification = version["needs_verification"] === true || document["needs_verification"] === true;
  const docType = typeof document["doc_type"] === "string" ? document["doc_type"] : "unknown";
  const allowedHeaders = headerFieldsFor(docType);
  const allowedLineFields = lineFieldsFor(docType);
  const reviewFields = reviewFieldSet(version, evidence);
  const hasReviewSignals = reviewFields.header.size > 0
    || reviewFields.lines.size > 0
    || evidence.some((item) => item["source"] !== "manual");
  const visibleHeaders = reviewOnly
    ? (hasReviewSignals ? allowedHeaders.filter(([field]) => reviewFields.header.has(field)) : needsVerification ? allowedHeaders : [])
    : allowedHeaders;
  const visibleLines = reviewOnly
    ? lines.filter((line) => {
        const lineNo = typeof line["line_no"] === "number" ? line["line_no"] : 0;
        return reviewFields.lines.has(lineNo)
          || evidence.some((item) => item["doc_line_id"] === line["id"] && item["source"] !== "manual");
      })
    : lines;
  const fieldsForEdit = reviewOnly && needsVerification ? visibleHeaders : allowedHeaders;
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [verified, setVerified] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saveNotice, setSaveNotice] = useState<string | null>(null);
  const [draft, setDraft] = useState<Record<EditableHeaderField, string>>(() => correctionDraft(version));
  const [lineDraft, setLineDraft] = useState<CorrectionLine[]>(() => lines.map(correctionLine));
  const [applied, setApplied] = useState<Record<string, AppliedSuggestion>>({});
  const documentVersion = typeof document["version"] === "number" ? document["version"] : null;
  const summaryFields = [
    ["Document number", version["doc_no"]],
    ["Date", version["doc_date"]],
    ["Vendor", version["vendor_name"]],
    ["Vendor GSTIN", version["vendor_gstin"]],
    ["PO references", version["po_refs"]],
    ["GRN references", version["grn_refs"]],
    ["Taxable total", version["taxable_total"]],
    ["Document total", version["grand_total"]],
  ] as const;
  const displayedHeaderFields = reviewOnly && needsVerification
    ? visibleHeaders.map(([field, label]) => [label, version[versionHeaderField[field] ?? field]] as const)
    : summaryFields;

  const updateHeader = (field: EditableHeaderField, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
  };

  const updateLine = (lineIndex: number, field: CorrectionLineField, value: string) => {
    setLineDraft((current) => current.map((line, index) => index === lineIndex ? { ...line, [field]: value } : line));
  };

  const applyBlockedReason = (suggestion: AiFieldSuggestion): string | null => {
    if (!csrfToken) return "Sign in again to edit";
    if (suggestion.scope === "header" && !fieldsForEdit.some(([field]) => field === suggestion.field)) {
      return "This field is not editable for this document type";
    }
    if (suggestion.scope === "line" && (!allowedLineFields.includes(suggestion.field as CorrectionLineField)
      || !lineDraft.some((line) => line.line_no === suggestion.line_no))) {
      return `Line ${suggestion.line_no ?? "?"} is not in the draft`;
    }
    const entry = applied[suggestionKey(suggestion)];
    return entry && draftHolds(entry.suggestion, draft, lineDraft) ? "Applied to draft" : null;
  };

  // Apply fills the draft only. Nothing is sent until the person saves the correction.
  const applySuggestion = (suggestion: AiFieldSuggestion, aiCallId: string) => {
    const { field, value } = suggestion;
    if (suggestion.scope === "header") {
      if (!isHeaderField(field) || !fieldsForEdit.some(([name]) => name === field)) return;
      setDraft((current) => {
        if (field !== "po_refs" && field !== "grn_refs") return { ...current, [field]: value };
        const refs = refList(current[field]);
        return refs.includes(value) ? current : { ...current, [field]: [...refs, value].join("; ") };
      });
    } else {
      if (!isLineField(field) || !allowedLineFields.includes(field)) return;
      setLineDraft((current) => current.map((line) => line.line_no === suggestion.line_no ? { ...line, [field]: value } : line));
    }
    setApplied((current) => ({ ...current, [suggestionKey(suggestion)]: { suggestion, aiCallId } }));
    setEditing(true);
    setSaveNotice(null);
  };

  const keepCurrentValue = (suggestion: AiFieldSuggestion) => {
    if (suggestion.scope === "header") {
      if (!isHeaderField(suggestion.field) || !fieldsForEdit.some(([field]) => field === suggestion.field)) return;
      updateHeader(suggestion.field, editableValue(suggestion.current_value));
    } else {
      if (!isLineField(suggestion.field) || !allowedLineFields.includes(suggestion.field)) return;
      setLineDraft((current) => current.map((line) => line.line_no === suggestion.line_no
        ? { ...line, [suggestion.field]: editableValue(suggestion.current_value) }
        : line));
    }
    setApplied((current) => {
      const next = { ...current };
      delete next[suggestionKey(suggestion)];
      return next;
    });
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
    for (const [field] of allowedHeaders) {
      const value = draft[field].trim();
      if (field === "po_refs" || field === "grn_refs") {
        correction[field] = value ? value.split(/[;,\n]+/).map((entry) => entry.trim()).filter(Boolean) : [];
      } else if (numericFields.has(field)) {
        correction[field] = value || null;
      } else {
        correction[field] = value || null;
      }
    }
    correction["lines"] = lineDraft.filter((line) => line.id !== null || allowedLineFields.some((field) => field !== "line_type" && line[field].trim() !== "")).map((line) => ({
      ...(line.id ? { id: line.id } : {}),
      line_no: line.line_no,
      ...Object.fromEntries(allowedLineFields.map((field) => [field, line[field].trim() || null])),
    }));
    correction["verified"] = verified;
    correction["reason"] = `${docType.toUpperCase()} fields reviewed or corrected from the document preview.`;
    // Only values still equal to the applied suggestion are claimed as AI-suggested.
    const refs: AiSuggestionRef[] = Object.values(applied)
      .filter((entry) => draftHolds(entry.suggestion, draft, lineDraft))
      .map(({ suggestion, aiCallId }) => ({ ai_call_id: aiCallId, scope: suggestion.scope, line_no: suggestion.line_no, field: suggestion.field }));
    if (refs.length) correction["ai_suggestion_refs"] = refs;
    try {
      const updated = await correctDocument(String(document["id"]), versionNumber, correction, csrfToken);
      const updatedVersion = record(updated["version"]);
      const updatedLines = list(updated["lines"]);
      setDraft(correctionDraft(updatedVersion));
      setLineDraft(updatedLines.map(correctionLine));
      setVerified(false);
      setApplied({});
      setSaveError(null);
      onSaved(updated);
      setEditing(false);
      setSaveNotice(verified ? "Corrections saved and this document marked reviewed." : "Corrections saved as a new version. It still needs verification.");
    } catch (cause) {
      setSaveError(cause instanceof Error ? cause.message : "Document corrections could not be saved.");
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
      {allowedHeaders.length ? <div className="invoice-correction-toolbar"><button className="button button-subtle" type="button" onClick={() => { if (editing) { setDraft(correctionDraft(version)); setLineDraft(lines.map(correctionLine)); setVerified(false); setApplied({}); } setEditing((current) => !current); setSaveError(null); }}>{editing ? "Cancel editing" : `Edit extracted ${docType.toUpperCase()}`}</button>{(document["vendor_id"] === null || document["vendor_id"] === undefined) ? <Link className="button button-subtle" to={`/mapping?document_id=${encodeURIComponent(String(document["id"] ?? ""))}`}>Assign vendor</Link> : null}{saveNotice ? <span role="status">{saveNotice}</span> : null}</div> : null}
      {typeof document["id"] === "string" ? <AiSuggestionsPanel documentId={document["id"]} documentVersion={documentVersion} fieldLabels={fieldLabels} applyBlockedReason={applyBlockedReason} onApply={applySuggestion} onKeepCurrent={keepCurrentValue} onFocusSource={onFocusSource} isRelevantSuggestion={(suggestion) => {
        if (!reviewOnly || !needsVerification) return true;
        if (suggestion.scope === "header") return visibleHeaders.some(([field]) => field === suggestion.field);
        return visibleLines.some((line) => line["line_no"] === suggestion.line_no)
          || !lineDraft.some((line) => line.line_no === suggestion.line_no);
      }} /> : null}
      <AiValuesVerify details={details} csrfToken={csrfToken} fieldLabels={fieldLabels} onFocusSource={onFocusSource} onSaved={onSaved} />
      {reviewOnly && needsVerification ? (
        <section className="verify-unverified-fields" aria-label="Fields needing verification">
          <h4>Fields to verify</h4>
          {visibleHeaders.map(([field, label]) => {
            const source = evidenceFocus(fieldEvidence(evidence, field));
            return <div className="verify-field-row" key={field}><strong>{label}</strong><span>{valueText(version[versionHeaderField[field] ?? field])}</span>{source ? <button className="table-action" type="button" onClick={() => onFocusSource(source)}>Source · page {source.page}</button> : <small>Source page unavailable</small>}</div>;
          })}
          {visibleLines.map((line, index) => {
            const lineId = typeof line["id"] === "string" ? line["id"] : null;
            const lineNo = typeof line["line_no"] === "number" ? line["line_no"] : index + 1;
            return <div className="verify-line-review" key={lineId ?? lineNo}><strong>Line {lineNo}</strong>{allowedLineFields.map((field) => {
              const evidenceName = field === "uom" ? "uom" : String(field);
              const source = evidenceFocus(fieldEvidence(evidence, evidenceName, lineId));
              const raw = field === "uom" ? line["uom_as_read"] ?? line["uom"] : line[String(field)];
              return <div className="verify-field-row" key={field}><strong>{fieldLabels[String(field)] ?? String(field)}</strong><span>{valueText(raw)}</span>{source ? <button className="table-action" type="button" onClick={() => onFocusSource(source)}>Source · page {source.page}</button> : null}</div>;
            })}</div>;
          })}
          {!visibleHeaders.length && !visibleLines.length ? <p className="preview-muted">The document needs review, but no individual field was marked. Check the source PDF and use “Edit extracted {docType.toUpperCase()}” to review it.</p> : null}
        </section>
      ) : (
        <dl className="preview-fields-grid">
          {displayedHeaderFields.map(([label, fieldValue]) => <div key={label}><dt>{label}</dt><dd>{valueText(fieldValue)}</dd></div>)}
        </dl>
      )}
      {(!reviewOnly || !needsVerification) ? <><h4>Extracted line items</h4>{lines.length ? (
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
                {typeof field["page"] === "number" ? <button className="table-action" type="button" onClick={() => { const focus = evidenceFocus(field); if (focus) onFocusSource(focus); }}>Source · page {field["page"]}</button> : null}
              </div>
            );
          })}
        </div>
      ) : <p className="preview-muted">No field-level evidence is available.</p>}</> : null}
      {editing ? <section className="invoice-correction-editor" aria-label={`Correct extracted ${docType} fields`}>
        <h4>Review and correct {docType.toUpperCase()} fields</h4>
        <p>Changes create an auditable new document version. The source PDF remains unchanged.</p>
        <div className="invoice-correction-grid">
          {fieldsForEdit.map(([field, label]) => <label key={field}><span>{label}</span>{field === "status" ? <select aria-label={label} value={draft[field] || "open"} onChange={(event) => updateHeader(field, event.currentTarget.value)}><option value="open">Open</option><option value="closed">Closed</option><option value="cancelled">Cancelled</option></select> : <input aria-label={label} type={field === "doc_date" || field === "delivery_date" ? "date" : "text"} inputMode={["taxable_total", "cgst", "sgst", "igst", "round_off", "grand_total"].includes(field) ? "decimal" : undefined} value={draft[field]} onChange={(event) => updateHeader(field, event.currentTarget.value)} />}</label>)}
        </div>
        <div className="invoice-correction-lines-heading"><h4>{docType.toUpperCase()} line items</h4><button className="button button-subtle" type="button" onClick={() => setLineDraft((current) => [...current, emptyCorrectionLine(Math.max(0, ...current.map((line) => line.line_no)) + 1)])}>Add line</button></div>
        <div className="preview-table-scroll"><table className="preview-line-table invoice-correction-table"><thead><tr><th>Line</th>{allowedLineFields.map((field) => <th key={field}>{fieldLabels[String(field)] ?? String(field)}</th>)}<th>Action</th></tr></thead><tbody>
          {lineDraft.map((line, index) => <tr key={line.id ?? `new-${index}`}><td>{line.line_no}</td>{allowedLineFields.map((field) => <td key={field}>{field === "line_type" ? <select aria-label={`Line ${line.line_no} line type`} value={line.line_type || "goods"} onChange={(event) => updateLine(index, field, event.currentTarget.value)}><option value="goods">Goods</option><option value="charge">Charge</option></select> : <input aria-label={`Line ${line.line_no} ${String(field).replaceAll("_", " ")}`} inputMode={["quantity", "received_qty", "rejected_qty", "accepted_qty", "rate", "discount", "amount", "tax_rate", "cgst_amount", "sgst_amount", "igst_amount"].includes(String(field)) ? "decimal" : undefined} value={line[field]} onChange={(event) => updateLine(index, field, event.currentTarget.value)} />}</td>)}<td><button className="button button-subtle" type="button" aria-label={`Remove line ${line.line_no}`} onClick={() => setLineDraft((current) => current.filter((_entry, rowIndex) => rowIndex !== index))}>Remove</button></td></tr>)}
          {lineDraft.length === 0 ? <tr><td colSpan={allowedLineFields.length + 2}>No lines. Add a line if this document contains line items.</td></tr> : null}
        </tbody></table></div>
        <label className="invoice-verified-check"><input type="checkbox" checked={verified} onChange={(event) => setVerified(event.currentTarget.checked)} /> I reviewed the source PDF and confirm the displayed {docType.toUpperCase()} data is correct.</label>
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
    for (const [field] of invoiceHeaderFields) {
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
          {invoiceHeaderFields.map(([field, label]) => (
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
          <div className="panel-heading"><h4>Invoice lines</h4><button className="button button-subtle" type="button" onClick={() => setLineDraft((current) => [...current, emptyCorrectionLine(current.length ? Math.max(...current.map((line) => line.line_no)) + 1 : 1)])}>Add line</button></div>
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
  const [reviewOnly, setReviewOnly] = useState(true);
  const [sourceFocus, setSourceFocus] = useState<PdfSourceFocus | null>(null);
  const [pdfPageNumber, setPdfPageNumber] = useState(1);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [rawPage, setRawPage] = useState(0);
  const lines = useMemo(() => data.rawText.split(/\r?\n/), [data.rawText]);
  const pageSize = 120;
  const rawPageCount = Math.max(1, Math.ceil(lines.length / pageSize));
  const pendingDocuments = data.documents.filter(needsVerification);
  const correctionExport = useMemo(
    () => buildAccuracyCorrectionExport(data.documents, file.display_name),
    [data.documents, file.display_name],
  );

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
    const docNo = String(record(updated["version"])["doc_no"] ?? "Document");
    const wasReviewed = !needsVerification(updated);
    setSavedNotice(wasReviewed ? `${docNo} saved and marked reviewed.` : `${docNo} saved as a new version; it still needs verification.`);
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

  const focusPdfSource = (focus: PdfSourceFocus | null) => {
    if (focus) setPdfPageNumber(focus.page);
    setSourceFocus(focus);
  };

  const downloadCorrectionExport = () => {
    if (!correctionExport.documents.length) return;
    const blob = new Blob([`${JSON.stringify(correctionExport, null, 2)}\n`], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.download = `accuracy-corrections-${file.id}.json`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  const reviewToolbar = (
    <div className="verify-toolbar" aria-label="Verification tools">
      <div><strong>{pendingDocuments.length} document{pendingDocuments.length === 1 ? "" : "s"} need review</strong><span>Use Tab to move through values and actions. Source buttons jump to the cited PDF page.</span></div>
      <div className="verify-toolbar-actions">
        <button className="button button-subtle" type="button" aria-pressed={!reviewOnly} onClick={() => setReviewOnly((current) => !current)}>{reviewOnly ? "Show all fields" : "Show only fields needing review"}</button>
        <button className="button button-subtle" type="button" disabled={!correctionExport.documents.length} onClick={downloadCorrectionExport}>Download truth correction patch ({correctionExport.documents.length})</button>
      </div>
    </div>
  );

  const visibleDocuments = reviewOnly ? pendingDocuments : data.documents;
  const documentsContent = visibleDocuments.length ? visibleDocuments.map((details, index) => <DocumentCard key={String(record(details["document"])["id"] ?? index)} details={details} csrfToken={user?.csrf_token ?? null} reviewOnly={reviewOnly} onFocusSource={focusPdfSource} onSaved={replaceSavedDocument} />) : (
    <section className="preview-empty" role="status">
      <FileText size={25} aria-hidden="true" />
      <h3>{reviewOnly && data.documents.length ? "All parsed documents are verified" : "No parsed document record yet"}</h3>
      <p>{reviewOnly && data.documents.length ? "Show all fields to inspect saved documents or download corrections for the local truth set." : "The file may still be processing, or it may have failed before a document could be created."}</p>
    </section>
  );

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
        {savedNotice ? <p className="verify-saved-notice" role="status">{savedNotice}</p> : null}
        {file.format === "pdf" ? (
          <>
          {reviewToolbar}
          <div className="preview-body pdf-preview-body">
            <Suspense fallback={<p className="preview-message" role="status">Loading secure PDF viewer…</p>}>
              <PdfPreview key={file.id} fileId={file.id} focus={sourceFocus} pageNumber={pdfPageNumber} onPageChange={setPdfPageNumber} />
            </Suspense>
            {data.documents.length ? <div className="pdf-extracted-records">{documentsContent}</div> : file.zone === "invoice" && !loading ? <ManualInvoiceEntry sourceFileId={file.id} csrfToken={user?.csrf_token ?? null} onCreated={replaceSavedDocument} /> : null}
          </div>
          </>
        ) : (
          <>
            <div className="preview-tabs" role="tablist" aria-label="Preview mode">
              <button type="button" role="tab" aria-selected={activeTab === "records"} className={activeTab === "records" ? "is-active" : ""} onClick={() => setActiveTab("records")}><FileText size={14} /> Parsed records</button>
              <button type="button" role="tab" aria-selected={activeTab === "raw"} className={activeTab === "raw" ? "is-active" : ""} onClick={() => setActiveTab("raw")}>View raw</button>
            </div>
            {activeTab === "records" ? reviewToolbar : null}
            <div className="preview-body">
              {activeTab === "records" ? (
                  data.documents.length ? documentsContent : (
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
