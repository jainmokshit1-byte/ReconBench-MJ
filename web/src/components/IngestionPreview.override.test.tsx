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
  user: { name: "Synthetic Manager", role: "manager", csrf_token: "csrf-test", capabilities: ["files.upload", "data.view"] },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => ["files.upload", "data.view"].includes(capability),
};

const baseVersion = {
  id: "version-1",
  doc_no: "INV-1",
  doc_date: "2026-04-01",
  vendor_name: "Example Vendor",
  vendor_gstin: null,
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
  reading_method: "rules",
};

function details(version: Record<string, unknown>, documentVersion = 1): DocumentDetails {
  return {
    document: { id: "document-1", doc_type: "invoice", version: documentVersion, doc_no: "INV-1" },
    version: { ...baseVersion, ...version },
    lines: [
      { id: "line-1", line_no: 1, description: "Item A", quantity: "1", rate: "50.00", discount: null, amount: "50.00" },
      { id: "line-2", line_no: 2, description: "Item B", quantity: "1", rate: "50.00", discount: null, amount: "50.00" },
    ],
    field_evidence: [],
    processing_log: [],
  } as unknown as DocumentDetails;
}

const failedQa = { extras: { correction_qa: { checked: true, passed: false, failures: ["line 2 amount", "taxable total"] } } };

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

/** Serves the document; answers the correction PUT with the given response and records its body. */
function installFetch(loaded: DocumentDetails, answer: () => Response): Array<Record<string, unknown>> {
  const bodies: Array<Record<string, unknown>> = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
    if (url.pathname === "/api/v1/documents" && url.searchParams.get("source_file_id") === file.id) return jsonResponse([{ id: "document-1" }]);
    if (url.pathname === "/api/v1/documents/document-1" && (init.method ?? "GET") === "GET") return jsonResponse(loaded);
    if (url.pathname.endsWith("/errors") || url.pathname.endsWith("/content")) return new Response("", { status: 200 });
    if (url.pathname === "/api/v1/documents/document-1/correction" && init.method === "PUT") {
      bodies.push(JSON.parse(String(init.body)) as Record<string, unknown>);
      return answer();
    }
    throw new Error(`Unexpected request ${init.method ?? "GET"} ${url.pathname}`);
  }));
  return bodies;
}

async function openEditor(): Promise<void> {
  render(
    <AuthContext.Provider value={session}>
      <MemoryRouter>
        <IngestionPreview file={file} batchId="batch-1" onClose={vi.fn()} />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
  expect(await screen.findByRole("heading", { name: "INV-1" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Edit extracted invoice/i }));
}

describe("Invoice correction override and field errors", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows no override section when the last correction passed arithmetic QA", async () => {
    installFetch(details({ extras: { correction_qa: { checked: true, passed: true, failures: [] } } }), () => jsonResponse(details({})));
    await openEditor();
    expect(screen.queryByText("Values don't add up")).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Reason to accept these values anyway/)).not.toBeInTheDocument();
  });

  it("shows the QA failure and sends override_reason only when it is filled and reviewed is ticked", async () => {
    const bodies = installFetch(details(failedQa), () => jsonResponse(details({ needs_verification: false, ...failedQa }, 2)));
    await openEditor();
    expect(screen.getByText("Values don't add up")).toBeInTheDocument();
    expect(screen.getByText(/line 2 amount, taxable total/)).toBeInTheDocument();

    // Reviewed and empty reason: nothing is sent.
    fireEvent.click(screen.getByLabelText(/I reviewed the source PDF/));
    fireEvent.click(screen.getByRole("button", { name: "Save and mark reviewed" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).not.toHaveProperty("override_reason");
    expect(bodies[0]?.["verified"]).toBe(true);
    expect(await screen.findAllByText(/they are marked reviewed with your reason/)).not.toHaveLength(0);
  });

  it("does not send a typed reason unless the reviewed box is ticked, and trims it when it is", async () => {
    let saves = 0;
    const bodies = installFetch(details(failedQa), () => jsonResponse(details({ needs_verification: saves++ === 0, ...failedQa }, 2)));
    await openEditor();
    fireEvent.change(screen.getByLabelText("Reason to accept these values anyway"), { target: { value: "  Header checked against the PDF  " } });
    expect(screen.getByText(/applies only when/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).not.toHaveProperty("override_reason");

    // The save closed the editor and cleared the draft; the reason is typed again with the box ticked.
    fireEvent.click(await screen.findByRole("button", { name: /Edit extracted invoice/i }));
    fireEvent.change(screen.getByLabelText("Reason to accept these values anyway"), { target: { value: "  Header checked against the PDF  " } });
    fireEvent.click(screen.getByLabelText(/I reviewed the source PDF/));
    fireEvent.click(screen.getByRole("button", { name: "Save and mark reviewed" }));
    await waitFor(() => expect(bodies).toHaveLength(2));
    expect(bodies[1]?.["override_reason"]).toBe("Header checked against the PDF");
  });

  it("tells the person when a save held fields unverified", async () => {
    installFetch(details({}), () => jsonResponse(details({ needs_verification: true, ...failedQa }, 2)));
    await openEditor();
    fireEvent.click(screen.getByLabelText(/I reviewed the source PDF/));
    fireEvent.click(screen.getByRole("button", { name: "Save and mark reviewed" }));
    const notices = await screen.findAllByText(/Those fields are held unverified/);
    expect(notices.length).toBeGreaterThan(0);
    expect(notices[0]).toHaveTextContent("line 2 amount, taxable total");
    expect(notices[0]).toHaveTextContent("reason to accept them anyway");
    // The saved version is now failing QA, so the editor offers the override.
    fireEvent.click(screen.getByRole("button", { name: /Edit extracted invoice/i }));
    expect(screen.getByText("Values don't add up")).toBeInTheDocument();
  });

  it("names the field and line a 422 refused", async () => {
    installFetch(details({}), () => jsonResponse({
      status: 422,
      title: "Invalid request",
      detail: "One or more request fields are invalid.",
      code: "invalid_request",
      errors: [
        { location: ["lines", 1, "discount"], code: "invalid_field", detail: "must have at most 2 decimals" },
        { location: ["taxable_total"], code: "invalid_field", detail: "is not a valid number" },
        { location: ["lines", 0, "rate"], code: "invalid_field", detail: "This field is invalid." },
      ],
    }, 422));
    await openEditor();
    fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Line 2 · Discount: must have at most 2 decimals");
    expect(alert).toHaveTextContent("Taxable total: is not a valid number");
    expect(alert).toHaveTextContent("Line 1 · Rate: is not valid");
    expect(alert).not.toHaveTextContent("One or more request fields are invalid.");
  });

  it("falls back to the generic text when a 422 names no field", async () => {
    installFetch(details({}), () => jsonResponse({ status: 422, detail: "One or more request fields are invalid.", code: "invalid_request" }, 422));
    await openEditor();
    fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("One or more request fields are invalid.");
  });
});
