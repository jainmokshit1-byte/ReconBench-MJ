import { describe, expect, it } from "vitest";

import type { DocumentDetails } from "../api/ingestion";
import { buildAccuracyCorrectionExport } from "./accuracyCorrectionExport";

function details(needsVerification: boolean): DocumentDetails {
  return {
    document: { id: "app-document-1", doc_type: "invoice" },
    version: { needs_verification: needsVerification, doc_no: "INV-SYNTHETIC", cgst_total: "9.00" },
    lines: [{ id: "line-1", line_no: 1, description: "Synthetic widget", amount: "109.00" }],
    field_evidence: [
      { id: "evidence-1", field_name: "doc_no", source: "manual", doc_line_id: null },
      { id: "evidence-2", field_name: "cgst", source: "manual", doc_line_id: null },
      { id: "evidence-3", field_name: "amount", source: "manual", doc_line_id: "line-1" },
      { id: "evidence-4", field_name: "grand_total", source: "pdf_text", doc_line_id: null },
    ],
  };
}

describe("accuracy correction export", () => {
  it("exports only verified, manually corrected benchmark fields", () => {
    expect(buildAccuracyCorrectionExport([details(false)], "synthetic-invoice.pdf")).toEqual({
      format: "reconbench-accuracy-corrections/v1",
      documents: [
        {
          application_document_id: "app-document-1",
          source_file_name: "synthetic-invoice.pdf",
          doc_type: "invoice",
          current_doc_no: "INV-SYNTHETIC",
          header: { doc_no: "INV-SYNTHETIC", cgst: "9.00" },
          lines: [{ line_no: 1, fields: { amount: "109.00" } }],
        },
      ],
    });
  });

  it("omits documents still awaiting human verification", () => {
    expect(buildAccuracyCorrectionExport([details(true)], "synthetic-invoice.pdf").documents).toEqual([]);
  });
});
