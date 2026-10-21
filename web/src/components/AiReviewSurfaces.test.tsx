import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import type { DocumentDetails, IngestionFile } from "../api/ingestion";
import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { AiLineOpinion } from "./AiLineOpinion";
import { AiSharingBanner } from "./AiSharingBanner";
import { IngestionPreview } from "./IngestionPreview";

const file: IngestionFile = {
  id: "file-ai-1",
  batch_id: "batch-1",
  parent_file_id: null,
  display_name: "synthetic-invoice.csv",
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
  user: { name: "Synthetic Manager", role: "manager", csrf_token: "csrf-ai", capabilities: ["files.upload", "data.view"] },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => ["files.upload", "data.view"].includes(capability),
};

const details = {
  document: { id: "document-ai-1", doc_type: "invoice", version: 1, doc_no: "INV-1" },
  version: {
    id: "version-1", doc_no: "INV-1", doc_date: "2026-04-01", vendor_name: "Synthetic Vendor",
    vendor_gstin: null, buyer_gstin: null, currency: "INR", po_refs: [], grn_refs: [],
    taxable_total: "100.00", cgst_total: "9.00", sgst_total: "9.00", igst_total: null,
    round_off: "0.00", grand_total: "118.00", needs_verification: true, extraction_method: "pdf_text",
  },
  lines: [],
  field_evidence: [],
} as unknown as DocumentDetails;

const label = "AI suggestion: fields the rules could not read reliably";

function suggestionsView(overrides: Record<string, unknown> = {}) {
  return {
    document_id: "document-ai-1",
    document_version: 1,
    current_version_id: "version-1",
    state: "available",
    code: null,
    ai_call_id: "call-1",
    mode: "ai_assisted",
    label,
    provider: "gemini",
    model: "synthetic-model",
    requested_at: "2026-04-01T10:00:00Z",
    suggestions: [
      { scope: "header", line_no: null, field: "doc_no", value: "INV-1-AI", current_value: "INV-1", matches_current: false, page: 1, quote: "Invoice No: INV-1-AI", start: 0, end: 20 },
      { scope: "header", line_no: null, field: "po_refs", value: "PO-77", current_value: [], matches_current: false, page: 1, quote: "PO: PO-77", start: 21, end: 30 },
      { scope: "line", line_no: 1, field: "quantity", value: "2", current_value: null, matches_current: false, page: 1, quote: "Qty 2", start: 31, end: 36 },
    ],
    dropped: {},
    ...overrides,
  };
}

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
}

function installFetch(view: unknown, saved?: DocumentDetails) {
  const writes: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(input instanceof Request ? input.url : String(input), window.location.origin);
    const method = init.method ?? "GET";
    if (method !== "GET") writes.push(init);
    if (url.pathname === "/api/v1/documents" && url.searchParams.get("source_file_id") === file.id) return json([{ id: "document-ai-1" }]);
    if (url.pathname === "/api/v1/documents/document-ai-1" && method === "GET") return json(details);
    if (url.pathname === "/api/v1/documents/document-ai-1/ai-suggestions") return json(view);
    if (url.pathname === "/api/v1/documents/document-ai-1/correction" && method === "PUT" && saved) return json(saved);
    if (url.pathname.startsWith("/api/v1/files/file-ai-1/")) return new Response("", { status: 200 });
    throw new Error(`Unexpected request ${method} ${url.pathname}`);
  }));
  return writes;
}

function renderPreview() {
  render(
    <AuthContext.Provider value={session}>
      <MemoryRouter>
        <IngestionPreview file={file} batchId="batch-1" onClose={vi.fn()} />
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("H1 AI suggestions in the invoice preview", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("applies a suggestion to the draft only and cites it on save while the value is unchanged", async () => {
    const saved = {
      ...details,
      document: { id: "document-ai-1", doc_type: "invoice", version: 2, doc_no: "INV-1-TYPED" },
      version: { ...(details["version"] as Record<string, unknown>), id: "version-2", doc_no: "INV-1-TYPED", po_refs: ["PO-77"] },
    } as unknown as DocumentDetails;
    const writes = installFetch(suggestionsView(), saved);
    renderPreview();

    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.getAllByText("Advisory").length).toBeGreaterThan(0);
    expect(screen.getByText(/Page 1: “Invoice No: INV-1-AI”/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use suggested Line 1 quantity" })).toBeDisabled();
    expect(screen.getByText("Line 1 is not in the draft")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Use suggested Document number" }));
    fireEvent.click(screen.getByRole("button", { name: "Use suggested PO references" }));
    expect(screen.getByLabelText("Document number")).toHaveValue("INV-1-AI");
    expect(screen.getByLabelText("PO references")).toHaveValue("PO-77");
    expect(screen.getByRole("heading", { name: "INV-1" })).toBeInTheDocument();
    expect(writes).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Use suggested Document number" })).toBeDisabled();

    // A value the person changes afterwards is no longer claimed as AI-suggested.
    fireEvent.change(screen.getByLabelText("Document number"), { target: { value: "INV-1-TYPED" } });
    fireEvent.click(screen.getByRole("button", { name: "Save corrections" }));
    await waitFor(() => expect(writes).toHaveLength(1));
    const body = JSON.parse(String(writes[0]?.body)) as Record<string, unknown>;
    expect(body["doc_no"]).toBe("INV-1-TYPED");
    expect(body["po_refs"]).toEqual(["PO-77"]);
    expect(body["ai_suggestion_refs"]).toEqual([{ ai_call_id: "call-1", scope: "header", line_no: null, field: "po_refs" }]);
  });

  it("blocks the suggested value when it belongs to another document version", async () => {
    const writes = installFetch(suggestionsView({ document_version: 2 }));
    renderPreview();

    expect(await screen.findByRole("alert")).toHaveTextContent("belong to version 2, but this preview shows version 1");
    expect(screen.getByRole("button", { name: "Use suggested Document number" })).toBeDisabled();
    expect(screen.queryByLabelText("Document number")).not.toBeInTheDocument();
    expect(writes).toHaveLength(0);
  });

  it("hides an ignored suggestion and can show it again", async () => {
    installFetch(suggestionsView());
    renderPreview();

    fireEvent.click(await screen.findByRole("button", { name: "Keep current for Document number" }));
    expect(screen.queryByRole("button", { name: "Use suggested Document number" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Show 1 ignored" }));
    expect(screen.getByRole("button", { name: "Use suggested Document number" })).toBeEnabled();
  });

  it("explains pending suggestions and shows nothing when AI does not apply", async () => {
    installFetch(suggestionsView({ state: "pending", suggestions: [] }));
    renderPreview();
    expect(await screen.findByText(/AI suggestions are being prepared/)).toBeInTheDocument();
    cleanup();

    installFetch(suggestionsView({ state: "not_applicable", label: null, suggestions: [] }));
    renderPreview();
    expect(await screen.findByRole("heading", { name: "INV-1" })).toBeInTheDocument();
    await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledWith(expect.stringContaining("/ai-suggestions"), expect.anything()));
    expect(screen.queryByRole("region", { name: "AI suggestions" })).not.toBeInTheDocument();
  });

  it("shows page-image readings as printed with current and suggested review choices", async () => {
    installFetch(suggestionsView({
      source: "vision",
      correctable: true,
      label: "AI page-image reading: documents the rules could not verify",
      suggestions: [
        { scope: "header", line_no: null, field: "doc_no", value: "GRN-9", current_value: "GRN-8", matches_current: false, page: 1, quote: "GRN-9", start: null, end: null, box: [80, 60, 100, 400] },
      ],
      annotations: [{ kind: "handwritten", text: "PO 26-27/04-777", looks_like: "po_number", page: 1, box: [545, 95, 570, 290] }],
    }));
    renderPreview();

    expect(await screen.findByText(/read from the page image/)).toBeInTheDocument();
    expect(screen.getByText(/Page 1 \(as printed\): “GRN-9”/)).toBeInTheDocument();
    expect(screen.getByText(/“PO 26-27\/04-777” \(po number\)/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Use suggested Document number" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Keep current for Document number" })).toBeEnabled();
    expect(screen.queryByText(/Corrections are not enabled/)).not.toBeInTheDocument();
  });
});

describe("AI external-sharing banner", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  const settings = (overrides: Record<string, unknown>) => ({
    version: 3,
    mode: "ai_assisted",
    mode_label: "AI-assisted",
    provider: "gemini",
    model: "synthetic-model",
    external_calls: "enabled",
    status: { code: "TESTED", label: "Last provider test passed" },
    ...overrides,
  });

  function stubSettings(value: unknown) {
    const fetchMock = vi.fn(async () => json(value));
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("appears when AI is on and names where data goes", async () => {
    stubSettings(settings({}));
    render(<AiSharingBanner />);
    const banner = await screen.findByRole("note", { name: "AI external sharing notice" });
    expect(banner).toHaveTextContent("AI Assist is on: AI-assisted");
    expect(banner).toHaveTextContent("redacted and sent to Google Gemini (synthetic-model)");
    expect(banner).toHaveTextContent("AI output is advisory");
  });

  it("says nothing leaves the deployment when external calls are disabled", async () => {
    stubSettings(settings({ external_calls: "disabled" }));
    render(<AiSharingBanner />);
    expect(await screen.findByRole("note")).toHaveTextContent("external calls are disabled on this deployment, so no document text is sent");
  });

  it("names the page reader and the text-assist provider separately", async () => {
    stubSettings(settings({
      reader_strategy: "single",
      primary_reader: "vllm",
      vllm_calls: "enabled",
      providers: [{ provider: "vllm", model: "synthetic-qwen" }, { provider: "gemini", model: "synthetic-model" }],
    }));
    render(<AiSharingBanner />);
    const banner = await screen.findByRole("note", { name: "AI external sharing notice" });
    expect(banner).toHaveTextContent("Page reading: masked page images are read by Qwen (synthetic-qwen) on the company vLLM server");
    expect(banner).toHaveTextContent("Text assist: eligible document text is redacted and sent to Google Gemini (synthetic-model)");
  });

  it("is absent in rules-only mode", async () => {
    const fetchMock = stubSettings(settings({ mode: "rules_only", mode_label: "Off: rules only" }));
    render(<AiSharingBanner />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await Promise.resolve();
    expect(screen.queryByRole("note")).not.toBeInTheDocument();
  });
});

describe("H2 advisory line opinion", () => {
  afterEach(cleanup);

  it("labels each opinion state as advisory", () => {
    const { rerender, container } = render(<AiLineOpinion value={{ state: "available", code: null, opinion: "same", reason: "Same model number on both sides." }} />);
    expect(container).toHaveTextContent("AI opinion · advisory Likely same item: Same model number on both sides.");
    rerender(<AiLineOpinion value={{ state: "pending", code: null }} />);
    expect(container).toHaveTextContent("AI opinion pending (advisory)");
    rerender(<AiLineOpinion value={{ state: "unavailable", code: "timeout" }} />);
    expect(container).toHaveTextContent("AI opinion unavailable (timeout)");
    rerender(<AiLineOpinion value={{ state: "not_applicable", code: "OUTSIDE_BAND" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
