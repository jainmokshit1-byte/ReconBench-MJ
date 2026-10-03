import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ChevronLeft, ChevronRight, FileText, X } from "lucide-react";
import { Link } from "react-router";

import {
  displayError,
  getDocumentDetails,
  getDocuments,
  getFileContent,
  getFileErrors,
  isRecordValue,
} from "../api/ingestion";
import type { DocumentDetails, IngestionFile } from "../api/ingestion";

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
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function confidence(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function DocumentCard({ details }: { details: DocumentDetails }) {
  const document = record(details["document"]);
  const version = record(details["version"]);
  const lines = list(details["lines"]);
  const evidence = list(details["field_evidence"]);
  const needsVerification = version["needs_verification"] === true || document["needs_verification"] === true;
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
    </article>
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
            {data.documents.length ? <div className="pdf-extracted-records">{data.documents.map((details, index) => <DocumentCard key={String(record(details["document"])["id"] ?? index)} details={details} />)}</div> : null}
          </div>
        ) : (
          <>
            <div className="preview-tabs" role="tablist" aria-label="Preview mode">
              <button type="button" role="tab" aria-selected={activeTab === "records"} className={activeTab === "records" ? "is-active" : ""} onClick={() => setActiveTab("records")}><FileText size={14} /> Parsed records</button>
              <button type="button" role="tab" aria-selected={activeTab === "raw"} className={activeTab === "raw" ? "is-active" : ""} onClick={() => setActiveTab("raw")}>View raw</button>
            </div>
            <div className="preview-body">
              {activeTab === "records" ? (
                data.documents.length ? data.documents.map((details, index) => <DocumentCard key={String(record(details["document"])["id"] ?? index)} details={details} />) : (
                  <section className="preview-empty">
                    <FileText size={25} aria-hidden="true" />
                    <h3>No parsed document record yet</h3>
                    <p>The file may still be processing, or it may have failed before a document could be created.</p>
                  </section>
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
