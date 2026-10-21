import type { DocumentDetails } from "../api/ingestion";

export const ACCURACY_CORRECTION_EXPORT_FORMAT = "reconbench-accuracy-corrections/v1";

const HEADER_FIELDS: Record<string, string> = {
  doc_no: "doc_no",
  doc_date: "doc_date",
  vendor_gstin: "vendor_gstin",
  vendor_name: "vendor_name",
  buyer_gstin: "buyer_gstin",
  taxable_total: "taxable_total",
  cgst: "cgst_total",
  sgst: "sgst_total",
  igst: "igst_total",
  grand_total: "grand_total",
  po_refs: "po_refs",
};

const LINE_FIELDS = new Set(["description", "quantity", "rate", "amount"]);

type CorrectionExportDocument = {
  application_document_id: string;
  source_file_name: string;
  doc_type: string;
  current_doc_no: string | null;
  header: Record<string, unknown>;
  lines: Array<{ line_no: number; fields: Record<string, unknown> }>;
};

function record(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function list(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.filter((item) => typeof item === "object" && item !== null && !Array.isArray(item)) as Record<string, unknown>[] : [];
}

/** Build a local truth patch from saved, verified manual evidence only. */
export function buildAccuracyCorrectionExport(
  detailsList: DocumentDetails[],
  sourceFileName: string,
): { format: typeof ACCURACY_CORRECTION_EXPORT_FORMAT; documents: CorrectionExportDocument[] } {
  const documents: CorrectionExportDocument[] = [];
  for (const details of detailsList) {
    const document = record(details["document"]);
    const version = record(details["version"]);
    const docId = document["id"];
    if (typeof docId !== "string" || version["needs_verification"] !== false) continue;

    const lines = list(details["lines"]);
    const lineById = new Map(lines.map((line) => [String(line["id"] ?? ""), line]));
    const header: Record<string, unknown> = {};
    const lineFields = new Map<number, Record<string, unknown>>();
    for (const evidence of list(details["field_evidence"])) {
      if (evidence["source"] !== "manual" || typeof evidence["field_name"] !== "string") continue;
      const field = evidence["field_name"];
      const lineId = evidence["doc_line_id"];
      if (typeof lineId === "string" && lineId) {
        const line = lineById.get(lineId);
        const lineNo = line?.["line_no"];
        if (!line || typeof lineNo !== "number" || !LINE_FIELDS.has(field)) continue;
        const fields = lineFields.get(lineNo) ?? {};
        fields[field] = line[field];
        lineFields.set(lineNo, fields);
        continue;
      }
      const truthField = HEADER_FIELDS[field];
      if (!truthField) continue;
      header[field] = version[truthField];
    }

    if (Object.keys(header).length === 0 && lineFields.size === 0) continue;
    documents.push({
      application_document_id: docId,
      source_file_name: sourceFileName,
      doc_type: String(document["doc_type"] ?? "unknown"),
      current_doc_no: typeof version["doc_no"] === "string" ? version["doc_no"] : null,
      header,
      lines: [...lineFields.entries()]
        .sort(([left], [right]) => left - right)
        .map(([line_no, fields]) => ({ line_no, fields })),
    });
  }
  return { format: ACCURACY_CORRECTION_EXPORT_FORMAT, documents };
}
