import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import type { DocumentDetails, IngestionFile } from "../api/ingestion";
import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { IngestionPreview } from "./IngestionPreview";

const session: AuthContextValue = {
  status: "authenticated",
  user: {
    name: "Synthetic Reviewer",
    role: "manager",
    csrf_token: "csrf-test",
    capabilities: ["files.upload", "data.view"],
  },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => ["files.upload", "data.view"].includes(capability),
};

function correctionFixture(docType: "po" | "grn", documentId: string): DocumentDetails {
  const document = {
    id: documentId,
    doc_type: docType,
    version: 1,
    doc_no: docType === "po" ? "PO-SYNTHETIC" : "GRN-SYNTHETIC",
    vendor_id: null,
    needs_verification: true,
  };
  const version = {
    id: `${documentId}-version-1`,
    doc_no: document["doc_no"],
    doc_date: "2026-10-08",
    vendor_name: "Synthetic Supplier",
    vendor_gstin: "27AAPFU0939F1ZV",
    buyer_gstin: null,
    currency: "INR",
    taxable_total: "100.00",
    cgst_total: "9.00",
    sgst_total: "9.00",
    igst_total: null,
    round_off: null,
    grand_total: "118.00",
    revision: "A",
    po_status: "open",
    ship_to: "Synthetic location",
    payment_terms_text: "30 days",
    delivery_date: "2026-10-31",
    po_refs: ["PO-SYNTHETIC"],
    challan_no: "CH-SYNTHETIC",
    supplier_invoice_no: "INV-SYNTHETIC",
    needs_verification: true,
    extraction_method: "pdf_text",
    extras: { review_fields: ["doc_no", docType === "po" ? "revision" : "supplier_invoice_no", "line 1"] },
  };
  return {
    document,
    version,
    lines: [{
      id: `${documentId}-line-1`,
      line_no: 1,
      line_type: "goods",
      description: "Synthetic item",
      item_code: "ITEM-1",
      hsn_sac: "1234",
      uom_as_read: "EA",
      quantity: "2",
      received_qty: "2",
      rejected_qty: "0",
      accepted_qty: "2",
      rate: "50.00",
      discount: "0.00",
      amount: "100.00",
      tax_rate: "18.00",
      cgst_amount: "9.00",
      sgst_amount: "9.00",
      igst_amount: null,
      po_ref: "PO-SYNTHETIC",
      po_line_ref: "1",
    }],
    field_evidence: [
      { id: `${documentId}-evidence-doc-no`, field_name: "doc_no", source: "pdf_text", page: 1, bbox: [10, 12, 50, 20], doc_line_id: null },
      { id: `${documentId}-evidence-type-field`, field_name: docType === "po" ? "revision" : "supplier_invoice_no", source: "pdf_text", page: 1, bbox: [60, 12, 90, 20], doc_line_id: null },
      { id: `${documentId}-evidence-line`, field_name: "amount", source: "pdf_text", page: 1, bbox: [10, 40, 60, 48], doc_line_id: `${documentId}-line-1` },
    ],
    processing_log: [],
  } as unknown as DocumentDetails;
}

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("AC-7 PO/GRN verification editor", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it.each([
    { docType: "po" as const, fileId: "file-po", documentId: "po-document", field: "Revision", key: "revision", value: "B" },
    { docType: "grn" as const, fileId: "file-grn", documentId: "grn-document", field: "Supplier invoice number", key: "supplier_invoice_no", value: "INV-SYNTHETIC-2" },
  ])("saves a verified $docType correction with its typed fields and evidence", async ({ docType, fileId, documentId, field, key, value }) => {
    const original = correctionFixture(docType, documentId);
    const saved = {
      ...original,
      document: { ...(original["document"] as Record<string, unknown>), version: 2, needs_verification: false },
      version: { ...(original["version"] as Record<string, unknown>), [key]: value, needs_verification: false },
    } as DocumentDetails;
    const correctionRequests: RequestInit[] = [];
    const file: IngestionFile = {
      id: fileId,
      batch_id: "batch-synthetic",
      parent_file_id: null,
      display_name: `${docType}-synthetic.csv`,
      archive_path: null,
      zone: docType,
      zone_uploaded: docType,
      format: "csv",
      sniffed_type: null,
      mismatch_state: "none",
      status: "parsed",
      error_code: null,
      error_message: null,
      attempt_count: 1,
      size_bytes: 100,
      page_count: null,
      docs_found: 1,
      docs_new: 1,
      docs_updated: 0,
      docs_skipped: 0,
      version: 1,
      uploaded_at: "2026-10-08T10:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
      if (url.pathname === "/api/v1/documents" && url.searchParams.get("source_file_id") === fileId) return jsonResponse([{ id: documentId }]);
      if (url.pathname === `/api/v1/documents/${documentId}/ai-suggestions`) return jsonResponse({ state: "not_applicable", document_version: 1, suggestions: [] });
      if (url.pathname === `/api/v1/documents/${documentId}` && (init.method ?? "GET") === "GET") return jsonResponse(original);
      if (url.pathname === `/api/v1/files/${fileId}/errors`) return new Response("", { status: 200 });
      if (url.pathname === `/api/v1/files/${fileId}/content`) return new Response("Synthetic source text", { status: 200 });
      if (url.pathname === `/api/v1/documents/${documentId}/correction` && init.method === "PUT") {
        correctionRequests.push(init);
        return jsonResponse(saved);
      }
      throw new Error(`Unexpected request ${init.method ?? "GET"} ${url.pathname}`);
    }));

    render(
      <AuthContext.Provider value={session}>
        <MemoryRouter><IngestionPreview file={file} batchId="batch-synthetic" onClose={vi.fn()} /></MemoryRouter>
      </AuthContext.Provider>,
    );

    const originalVersion = original["version"] as Record<string, unknown>;
    expect(await screen.findByRole("heading", { name: originalVersion["doc_no"] as string })).toBeInTheDocument();
    expect(screen.getByText("Fields to verify")).toBeInTheDocument();
    expect(screen.queryByLabelText("Payment terms")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: `Edit extracted ${docType.toUpperCase()}` }));
    fireEvent.change(screen.getByLabelText(field), { target: { value } });
    fireEvent.click(screen.getByLabelText(new RegExp(`I reviewed the source PDF.*${docType.toUpperCase()}`)));
    fireEvent.click(screen.getByRole("button", { name: "Save and mark reviewed" }));

    await waitFor(() => expect(correctionRequests).toHaveLength(1));
    const request = correctionRequests[0];
    if (!request) throw new Error("expected the correction request");
    expect(new Headers(request.headers).get("If-Match")).toBe('"1"');
    const body = JSON.parse(String(request.body)) as Record<string, unknown>;
    expect(body[key]).toBe(value);
    expect(body["verified"]).toBe(true);
    expect(Array.isArray(body["lines"])).toBe(true);
    expect(await screen.findByText(/saved and marked reviewed/)).toBeInTheDocument();
  });
});
