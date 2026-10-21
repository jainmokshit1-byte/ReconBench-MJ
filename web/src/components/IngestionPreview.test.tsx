import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import type { DocumentDetails, IngestionFile } from "../api/ingestion";
import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { IngestionPreview } from "./IngestionPreview";

const file: IngestionFile = {
  id: "file-invoice-1",
  batch_id: "batch-1",
  parent_file_id: null,
  display_name: "invoice.pdf",
  archive_path: null,
  zone: "invoice",
  zone_uploaded: "invoice",
  format: "csv",
  sniffed_type: null,
  mismatch_state: "none",
  status: "parsed",
  error_code: null,
  error_message: null,
  attempt_count: 1,
  size_bytes: 100,
  page_count: 1,
  docs_found: 1,
  docs_new: 1,
  docs_updated: 0,
  docs_skipped: 0,
  version: 1,
  uploaded_at: "2026-04-01T10:00:00Z",
};

const session: AuthContextValue = {
  status: "authenticated",
  user: {
    name: "Synthetic Manager",
    role: "manager",
    csrf_token: "csrf-test",
    capabilities: ["files.upload", "data.view"],
  },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => ["files.upload", "data.view"].includes(capability),
};

const originalDocument: Record<string, unknown> = {
  id: "document-1",
  doc_type: "invoice",
  version: 1,
  doc_no: "INV-1",
};
const originalVersion: Record<string, unknown> = {
    id: "version-1",
    doc_no: "INV-1",
    doc_date: "2026-04-01",
    vendor_name: "Example Vendor",
    vendor_gstin: "27AAPFU0939F1ZV",
    buyer_gstin: null,
    currency: "INR",
    po_refs: [],
    grn_refs: [],
    taxable_total: "100.00",
    cgst_total: "9.00",
    sgst_total: "9.00",
    igst_total: null,
    round_off: "0.00",
    grand_total: "118.00",
    needs_verification: true,
    extraction_method: "pdf_text",
};
const originalDetails = {
  document: originalDocument,
  version: originalVersion,
  lines: [],
  field_evidence: [],
  processing_log: [],
} as unknown as DocumentDetails;

function jsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

describe("Invoice preview corrections", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("sends reviewed edits with the loaded version and reflects the new version", async () => {
    const correctedDetails = {
      ...originalDetails,
      document: { ...originalDocument, version: 2, doc_no: "INV-1-CORRECTED" },
      version: { ...originalVersion, id: "version-2", doc_no: "INV-1-CORRECTED", needs_verification: false, extraction_method: "manual" },
    } as unknown as DocumentDetails;
    const correctionRequests: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
      if (url.pathname === "/api/v1/documents" && url.searchParams.get("source_file_id") === file.id) {
        return jsonResponse([{ id: "document-1" }]);
      }
      if (url.pathname === "/api/v1/documents/document-1" && (init.method ?? "GET") === "GET") {
        return jsonResponse(originalDetails);
      }
      if (url.pathname === "/api/v1/files/file-invoice-1/errors") return new Response("", { status: 200 });
      if (url.pathname === "/api/v1/files/file-invoice-1/content") return new Response("", { status: 200 });
      if (url.pathname === "/api/v1/documents/document-1/correction" && init.method === "PUT") {
        correctionRequests.push({ url: url.pathname, init });
        return jsonResponse(correctedDetails);
      }
      throw new Error(`Unexpected request ${init.method ?? "GET"} ${url.pathname}`);
    }));

    const onClose = vi.fn();
    render(
      <AuthContext.Provider value={session}>
        <MemoryRouter>
          <IngestionPreview file={file} batchId="batch-1" onClose={onClose} />
        </MemoryRouter>
      </AuthContext.Provider>,
    );

    expect(await screen.findByRole("heading", { name: "INV-1" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Edit extracted invoice/i }));
    fireEvent.change(screen.getByLabelText("Document number"), { target: { value: "INV-1-CORRECTED" } });
    fireEvent.click(screen.getByLabelText(/I reviewed the source PDF/));
    fireEvent.click(screen.getByRole("button", { name: "Save and mark reviewed" }));

    await waitFor(() => expect(correctionRequests).toHaveLength(1));
    const request = correctionRequests.at(0);
    if (!request) throw new Error("expected the correction request");
    expect(request.init.method).toBe("PUT");
    expect(new Headers(request.init.headers).get("If-Match")).toBe('"1"');
    const body = JSON.parse(String(request.init.body)) as Record<string, unknown>;
    expect(body["doc_no"]).toBe("INV-1-CORRECTED");
    expect(body["verified"]).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Show all fields" }));
    expect(await screen.findByRole("heading", { name: "INV-1-CORRECTED" })).toBeInTheDocument();
    expect(await screen.findByRole("status")).toHaveTextContent("marked reviewed");
  });

  it("creates a manually reviewed invoice when parsing produced no document", async () => {
    const manualDetails = {
      ...originalDetails,
      document: { ...originalDocument, id: "manual-document", version: 1, doc_no: "MANUAL-INV-1" },
      version: { ...originalVersion, id: "manual-version", doc_no: "MANUAL-INV-1", needs_verification: false, extraction_method: "manual" },
      lines: [],
      field_evidence: [],
    } as unknown as DocumentDetails;
    const manualRequests: Array<{ init: RequestInit }> = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
      if (url.pathname === "/api/v1/documents" && url.searchParams.get("source_file_id") === file.id) {
        return jsonResponse([]);
      }
      if (url.pathname === "/api/v1/files/file-invoice-1/errors") return new Response("", { status: 200 });
      if (url.pathname === "/api/v1/files/file-invoice-1/content") return new Response("", { status: 200 });
      if (url.pathname === "/api/v1/files/file-invoice-1/manual-invoice" && init.method === "POST") {
        manualRequests.push({ init });
        return jsonResponse(manualDetails);
      }
      throw new Error(`Unexpected request ${init.method ?? "GET"} ${url.pathname}`);
    }));

    render(
      <AuthContext.Provider value={session}>
        <MemoryRouter>
          <IngestionPreview file={{ ...file, status: "failed" }} batchId="batch-1" onClose={vi.fn()} />
        </MemoryRouter>
      </AuthContext.Provider>,
    );

    expect(await screen.findByRole("heading", { name: "No parsed document record yet" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Document number"), { target: { value: "MANUAL-INV-1" } });
    fireEvent.change(screen.getByLabelText("Vendor"), { target: { value: "Manual Vendor" } });
    fireEvent.click(screen.getByLabelText(/I checked these manually entered values/));
    fireEvent.click(screen.getByRole("button", { name: "Create manual invoice" }));

    await waitFor(() => expect(manualRequests).toHaveLength(1));
    const request = manualRequests.at(0);
    if (!request) throw new Error("expected a manual invoice request");
    const body = JSON.parse(String(request.init.body)) as Record<string, unknown>;
    expect(body["doc_no"]).toBe("MANUAL-INV-1");
    expect(body["vendor_name"]).toBe("Manual Vendor");
    expect(body["verified"]).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Show all fields" }));
    expect(await screen.findByRole("heading", { name: "MANUAL-INV-1" })).toBeInTheDocument();
  });
});
