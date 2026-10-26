import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import type { IngestionFile } from "../api/ingestion";
import { IngestionPage } from "./IngestionPage";

const batchId = "batch-synthetic-1";
const mismatchId = "file-mismatch-1";
const failedId = "file-failed-1";
const parsedId = "file-parsed-1";
const outdatedId = "file-outdated-1";

const sessionContext: AuthContextValue = {
  status: "authenticated",
  user: {
    name: "Synthetic Manager",
    role: "manager",
    csrf_token: "csrf-phase4-test",
    capabilities: ["files.upload", "data.view"],
  },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => ["files.upload", "data.view"].includes(capability),
};

function jsonResponse(status: number, value: unknown, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json", ...Object.fromEntries(new Headers(headers)) },
  });
}

function makeFile(id: string, patch: Partial<IngestionFile> = {}): IngestionFile {
  return {
    id,
    batch_id: batchId,
    parent_file_id: null,
    display_name: `${id}.pdf`,
    archive_path: null,
    zone: "invoice",
    zone_uploaded: "invoice",
    format: "pdf",
    sniffed_type: null,
    mismatch_state: "none",
    status: "parsed",
    error_code: null,
    error_message: null,
    attempt_count: 0,
    size_bytes: 2048,
    page_count: 1,
    docs_found: 1,
    docs_new: 1,
    docs_updated: 0,
    docs_skipped: 0,
    version: 1,
    uploaded_at: "2026-04-01T10:00:00Z",
    ...patch,
  };
}

function renderPage() {
  return render(
    <AuthContext.Provider value={sessionContext}>
      <MemoryRouter initialEntries={["/ingestion"]}>
        <Routes>
          <Route path="/ingestion" element={<IngestionPage />} />
          <Route path="/mapping" element={<h1>Mapping workspace</h1>} />
          <Route path="/home" element={<h1>Home workspace</h1>} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

function installFetch(handler: (url: URL, init: RequestInit) => Response | Promise<Response>) {
  const request = vi.fn((input: RequestInfo | URL, init: RequestInit = {}) =>
    Promise.resolve(handler(new URL(input instanceof Request ? input.url : String(input), window.location.origin), init)),
  );
  vi.stubGlobal("fetch", request);
  return request;
}

const configResponse = {
  body: { app: { ui: { poll_seconds: 30 }, limits: { max_file_mb: 25 } } },
};

describe("Ingestion workflow screen", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("uploads into a new batch, shows server completion and opens Mapping from summary", async () => {
    let batchCreated = false;
    let fileUploaded = false;
    let processingStarted = false;
    const file = makeFile("file-uploaded-1", { display_name: "synthetic-invoice.pdf" });
    installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/config") return jsonResponse(200, configResponse);
      if (url.pathname === "/api/v1/batches" && method === "GET") return jsonResponse(200, batchCreated ? [{ id: batchId, label: "April synthetic close", version: 1 }] : []);
      if (url.pathname === "/api/v1/batches" && method === "POST") {
        batchCreated = true;
        return jsonResponse(201, { id: batchId, label: "April synthetic close", version: 1 });
      }
      if (url.pathname === "/api/v1/files" && method === "POST") {
        fileUploaded = true;
        return jsonResponse(201, { file_id: file.id, batch_id: batchId, format: "pdf", status: "staged" });
      }
      if (url.pathname === `/api/v1/batches/${batchId}/process` && method === "POST") {
        processingStarted = true;
        return jsonResponse(202, { batch_id: batchId, queued_count: 1, jobs: [] });
      }
      if (url.pathname === "/api/v1/files" && method === "GET") {
        const rows = fileUploaded && processingStarted ? [file] : [];
        return jsonResponse(200, rows, { ETag: '"synthetic-files-1"' });
      }
      if (url.pathname === `/api/v1/batches/${batchId}/summary`) {
        return jsonResponse(200, { file_count: 1, documents_found: 1, documents_new: 1, file_status_counts: { parsed: 1 } });
      }
      if (url.pathname === "/api/v1/documents") return jsonResponse(200, []);
      if (url.pathname === `/api/v1/files/${file.id}/errors`) return new Response("", { status: 200 });
      throw new Error(`Unexpected request ${method} ${url.pathname}`);
    });

    renderPage();
    const invoiceInput = await screen.findByLabelText("Choose invoices files");
    fireEvent.change(invoiceInput, { target: { files: [new File(["synthetic pdf bytes"], "synthetic-invoice.pdf", { type: "application/pdf" })] } });
    fireEvent.click(screen.getByRole("button", { name: "Process files" }));

    expect(await screen.findByText("Parsed")).toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "View summary" }));
    expect(await screen.findByText("Vendor anchors")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: /Go to Mapping/ }));
    expect(await screen.findByRole("heading", { name: "Mapping workspace" })).toBeInTheDocument();
  });

  it("says when a file was already uploaded and lets a person decide a held duplicate (PD-0b-4)", async () => {
    let decided: { decision: string; ifMatch: string | null } | null = null;
    const hold = {
      id: "hold-synthetic-1",
      source_file_id: "file-copy-1",
      file_name: "copy-of-invoice.pdf",
      kind: "duplicate",
      reason: "same_vendor_number_year",
      doc_no: "INV-SYN-7",
      state: "open",
      version: 1,
      duplicate_of_document_id: "document-original-1",
      original_doc_no: "INV-SYN-7",
      original_file_name: "invoice.pdf",
      original_batch_id: "batch-original-1",
    };
    installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/config") return jsonResponse(200, configResponse);
      if (url.pathname === "/api/v1/batches" && method === "GET") return jsonResponse(200, [{ id: batchId, label: "April synthetic close", version: 1 }]);
      if (url.pathname === "/api/v1/batches" && method === "POST") return jsonResponse(201, { id: batchId, label: "April synthetic close", version: 1 });
      if (url.pathname === "/api/v1/files" && method === "POST") {
        return jsonResponse(200, { file_id: "file-original-1", batch_id: batchId, format: "pdf", status: "parsed", duplicate: true, original_display_name: "invoice.pdf", original_batch_id: "batch-original-1" });
      }
      if (url.pathname === "/api/v1/files" && method === "GET") return jsonResponse(200, [], { ETag: '"synthetic-files-1"' });
      if (url.pathname === `/api/v1/batches/${batchId}/summary`) return jsonResponse(200, { file_count: 0, documents_found: 0, file_status_counts: {} });
      if (url.pathname === "/api/v1/documents") return jsonResponse(200, []);
      if (url.pathname === "/api/v1/duplicate-holds" && method === "GET") return jsonResponse(200, decided ? [] : [hold]);
      if (url.pathname === `/api/v1/duplicate-holds/${hold.id}/decision` && method === "POST") {
        decided = { decision: String(JSON.parse(String(init.body)).decision), ifMatch: new Headers(init.headers).get("If-Match") };
        return jsonResponse(200, { ...hold, state: "replaced", version: 2 });
      }
      throw new Error(`Unexpected request ${method} ${url.pathname}`);
    });

    renderPage();
    const panel = await screen.findByRole("region", { name: "Uploads not ingested" });
    expect(within(panel).getByText(/Duplicate of INV INV-SYN-7/)).toBeInTheDocument();
    expect(within(panel).getByText("Same supplier GSTIN, invoice number and financial year.")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Keep as new" })).not.toBeInTheDocument();

    const invoiceInput = await screen.findByLabelText("Choose invoices files");
    fireEvent.change(invoiceInput, { target: { files: [new File(["same bytes"], "invoice-again.pdf", { type: "application/pdf" })] } });
    fireEvent.click(screen.getByRole("button", { name: "Process files" }));
    expect(await screen.findByText(/Already uploaded: invoice-again.pdf is the same file as invoice.pdf/)).toBeInTheDocument();

    fireEvent.click(within(panel).getByRole("button", { name: "Replace with this file" }));
    await waitFor(() => expect(decided).toEqual({ decision: "replace", ifMatch: '"1"' }));
    await waitFor(() => expect(screen.queryByRole("region", { name: "Uploads not ingested" })).not.toBeInTheDocument());
  });

  it("saves a CSV column map from the file row and refreshes the queued status", async () => {
    let mappingSaved = false;
    const csvFile = makeFile("file-csv-map", {
      display_name: "custom-layout.csv",
      format: "csv",
      status: "failed",
      error_code: "CSV_MAPPING_REQUIRED",
      docs_found: 0,
      docs_new: 0,
    });
    let receivedMapping: Record<string, string> | null = null;
    installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/config") return jsonResponse(200, configResponse);
      if (url.pathname === "/api/v1/batches" && method === "GET") return jsonResponse(200, [{ id: batchId, label: "Synthetic batch", version: 1 }]);
      if (url.pathname === "/api/v1/files" && method === "GET") {
        const row = mappingSaved ? { ...csvFile, status: "queued", error_code: null } : csvFile;
        return jsonResponse(200, [row], { ETag: mappingSaved ? '"mapped-2"' : '"mapped-1"' });
      }
      if (url.pathname === `/api/v1/files/${csvFile.id}/content`) return new Response("Field A,Field B,Field C\nINV-1,2026-04-01,Example Vendor\n", { status: 200, headers: { "Content-Type": "text/csv" } });
      if (url.pathname === `/api/v1/files/${csvFile.id}/column-mapping` && method === "POST") {
        const body: unknown = JSON.parse(String(init.body));
        if (typeof body === "object" && body !== null && "column_mapping" in body) {
          receivedMapping = body.column_mapping as Record<string, string>;
        }
        mappingSaved = true;
        return jsonResponse(202, { file_id: csvFile.id, status: "queued" });
      }
      throw new Error(`Unexpected request ${method} ${url.pathname}`);
    });

    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Map CSV" }));
    expect(await screen.findByRole("dialog", { name: /Map columns for custom-layout.csv/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Map Field A"), { target: { value: "doc_no" } });
    fireEvent.change(screen.getByLabelText("Map Field B"), { target: { value: "doc_date" } });
    fireEvent.change(screen.getByLabelText("Map Field C"), { target: { value: "vendor_name" } });
    fireEvent.click(screen.getByRole("button", { name: "Save and retry" }));

    await waitFor(() => expect(receivedMapping).toEqual({ "Field A": "doc_no", "Field B": "doc_date", "Field C": "vendor_name" }));
    expect(await screen.findByText("Queued")).toBeInTheDocument();
  });

  it("resolves a type mismatch, retries a failure and soft-removes a parsed file", async () => {
    let mismatch = makeFile(mismatchId, { display_name: "misfiled-po.pdf", status: "staged", mismatch_state: "suggested", sniffed_type: "po", docs_found: 0, docs_new: 0 });
    let failed = makeFile(failedId, { display_name: "retry-invoice.pdf", status: "failed", error_code: "PDF_TEXT_EXTRACTION_FAILED", docs_found: 0, docs_new: 0 });
    let removed = makeFile(parsedId, { display_name: "remove-invoice.pdf" });
    let outdated = makeFile(outdatedId, {
      display_name: "outdated-parser.xml",
      format: "xml",
      zone: "po",
      zone_uploaded: "po",
      retry_available: true,
      retry_reason: "PARSER_VERSION_OUTDATED",
    });
    installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/config") return jsonResponse(200, configResponse);
      if (url.pathname === "/api/v1/batches" && method === "GET") return jsonResponse(200, [{ id: batchId, label: "Synthetic batch", version: 1 }]);
      if (url.pathname === "/api/v1/files" && method === "GET") return jsonResponse(200, [mismatch, failed, removed, outdated], { ETag: `"${mismatch.version}-${failed.status}-${removed.status}-${outdated.status}"` });
      if (url.pathname === `/api/v1/files/${mismatchId}` && method === "GET") return jsonResponse(200, mismatch, { ETag: '"file-v1"' });
      if (url.pathname === `/api/v1/files/${mismatchId}` && method === "PATCH") {
        expect(new Headers(init.headers).get("If-Match")).toBe('"file-v1"');
        mismatch = { ...mismatch, zone: "po", mismatch_state: "accepted", status: "queued", version: 2 };
        return jsonResponse(200, mismatch);
      }
      if (url.pathname === `/api/v1/files/${failedId}/retry` && method === "POST") {
        failed = { ...failed, status: "queued", error_code: null, version: 2 };
        return jsonResponse(202, { file_id: failedId, status: "queued" });
      }
      if (url.pathname === `/api/v1/files/${outdatedId}/retry` && method === "POST") {
        outdated = { ...outdated, status: "queued", retry_available: false, retry_reason: null, version: 2 };
        return jsonResponse(202, { file_id: outdatedId, status: "queued" });
      }
      if (url.pathname === `/api/v1/files/${parsedId}` && method === "DELETE") {
        removed = { ...removed, status: "removed", version: 2 };
        return new Response(null, { status: 204 });
      }
      if (url.pathname === `/api/v1/batches/${batchId}/process` && method === "POST") return jsonResponse(202, { batch_id: batchId, queued_count: 1, jobs: [] });
      throw new Error(`Unexpected request ${method} ${url.pathname}`);
    });
    vi.stubGlobal("confirm", () => true);

    renderPage();
    const row = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;
    await screen.findByText("misfiled-po.pdf");
    fireEvent.click(within(row("misfiled-po.pdf")).getByRole("button", { name: "Use PO" }));
    expect(await within(row("misfiled-po.pdf")).findByText("Queued")).toBeInTheDocument();
    fireEvent.click(within(row("retry-invoice.pdf")).getByRole("button", { name: "Retry" }));
    expect(await within(row("retry-invoice.pdf")).findByText("Queued")).toBeInTheDocument();
    expect(within(row("outdated-parser.xml")).getByText("PARSER VERSION OUTDATED")).toBeInTheDocument();
    fireEvent.click(within(row("outdated-parser.xml")).getByRole("button", { name: "Reprocess" }));
    expect(await within(row("outdated-parser.xml")).findByText("Queued")).toBeInTheDocument();
    fireEvent.click(within(row("remove-invoice.pdf")).getByRole("button", { name: "Remove" }));
    expect(await within(row("remove-invoice.pdf")).findByText("Removed")).toBeInTheDocument();
  });

  it("asks whether a bundle PDF is split into documents and sends the answer", async () => {
    const message = "This file looks like 3 document(s): PO p1, GRN p2, INVOICE p3.";
    let bundle = makeFile(mismatchId, { display_name: "bundle.pdf", mismatch_state: "suggested", docs_found: 3, docs_new: 3, error_message: message });
    let kept = makeFile(parsedId, { display_name: "second-bundle.pdf", mismatch_state: "suggested", docs_found: 2, docs_new: 2, error_message: message });
    const answers: Array<{ id: string; decision: string }> = [];
    installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/config") return jsonResponse(200, configResponse);
      if (url.pathname === "/api/v1/batches" && method === "GET") return jsonResponse(200, [{ id: batchId, label: "Synthetic batch", version: 1 }]);
      if (url.pathname === "/api/v1/files" && method === "GET") return jsonResponse(200, [bundle, kept], { ETag: `"${bundle.status}-${kept.status}"` });
      if (url.pathname === `/api/v1/files/${mismatchId}` && method === "GET") return jsonResponse(200, bundle, { ETag: '"bundle-v1"' });
      if (url.pathname === `/api/v1/files/${parsedId}` && method === "GET") return jsonResponse(200, kept, { ETag: '"kept-v1"' });
      const split = url.pathname.match(/^\/api\/v1\/files\/([^/]+)\/page-split$/);
      if (split && method === "POST") {
        const id = decodeURIComponent(split[1] ?? "");
        expect(new Headers(init.headers).get("If-Match")).toBe(id === mismatchId ? '"bundle-v1"' : '"kept-v1"');
        const decision = JSON.parse(String(init.body)).decision as string;
        answers.push({ id, decision });
        const state = decision === "accept_split" ? "accepted" : "kept";
        if (id === mismatchId) bundle = { ...bundle, mismatch_state: state, status: "queued", error_message: null, version: 2 };
        else kept = { ...kept, mismatch_state: state, status: "queued", error_message: null, version: 2 };
        return jsonResponse(202, id === mismatchId ? bundle : kept);
      }
      throw new Error(`Unexpected request ${method} ${url.pathname}`);
    });

    renderPage();
    const row = (name: string) => screen.getByText(name).closest("tr") as HTMLElement;
    await screen.findByText("bundle.pdf");
    expect(within(row("bundle.pdf")).getByText("PAGE SPLIT SUGGESTED")).toHaveAttribute("title", message);
    expect(within(row("bundle.pdf")).queryByRole("button", { name: /^Use / })).not.toBeInTheDocument();
    fireEvent.click(within(row("bundle.pdf")).getByRole("button", { name: "Split into documents" }));
    expect(await within(row("bundle.pdf")).findByText("Queued")).toBeInTheDocument();
    fireEvent.click(within(row("second-bundle.pdf")).getByRole("button", { name: "Keep as one INVOICE" }));
    expect(await within(row("second-bundle.pdf")).findByText("Queued")).toBeInTheDocument();
    expect(answers).toEqual([
      { id: mismatchId, decision: "accept_split" },
      { id: parsedId, decision: "keep_one" },
    ]);
  });
});
