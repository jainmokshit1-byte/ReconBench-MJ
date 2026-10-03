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
    installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/config") return jsonResponse(200, configResponse);
      if (url.pathname === "/api/v1/batches" && method === "GET") return jsonResponse(200, [{ id: batchId, label: "Synthetic batch", version: 1 }]);
      if (url.pathname === "/api/v1/files" && method === "GET") return jsonResponse(200, [mismatch, failed, removed], { ETag: `"${mismatch.version}-${failed.status}-${removed.status}"` });
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
    fireEvent.click(within(row("remove-invoice.pdf")).getByRole("button", { name: "Remove" }));
    expect(await within(row("remove-invoice.pdf")).findByText("Removed")).toBeInTheDocument();
  });
});
