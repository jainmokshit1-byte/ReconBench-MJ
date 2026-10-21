import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { MappingPage } from "./MappingPage";

const vendorId = "vendor-synthetic-1";
const linkId = "link-synthetic-1";
const invoiceId = "document-synthetic-invoice-1";
const poId = "document-synthetic-po-1";

const managerContext: AuthContextValue = {
  status: "authenticated",
  user: {
    name: "Synthetic Manager",
    role: "manager",
    csrf_token: "csrf-mapping-test",
    capabilities: ["links.edit", "data.view"],
  },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => ["links.edit", "data.view"].includes(capability),
};

const vendor = {
  id: vendorId,
  canonical_name: "Acme Components",
  gstins: ["27AAPFU0939F1ZV"],
  pan: "AAPFU0939F",
  anchor: "gstin",
  aliases: [],
  counts: { po: 1, grn: 0, invoice: 1, linked: 1, suggested: 0, unlinked: 0 },
  has_issues: false,
  unassigned: false,
};

function response(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function installFetch(handler: (url: URL, init: RequestInit) => Response | Promise<Response>) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init: RequestInit = {}) =>
    Promise.resolve(
      handler(
        new URL(input instanceof Request ? input.url : String(input), window.location.origin),
        init,
      ),
    ),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderMapping() {
  return render(
    <AuthContext.Provider value={managerContext}>
      <MemoryRouter initialEntries={["/mapping"]}>
        <Routes>
          <Route path="/mapping" element={<MappingPage />} />
        </Routes>
      </MemoryRouter>
    </AuthContext.Provider>,
  );
}

describe("Mapping workflow", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows link evidence and confirms a link with optimistic concurrency headers", async () => {
    let currentState = "auto";
    const currentLink = () => ({
      id: linkId,
      version_no: currentState === "auto" ? 1 : 2,
      po_document_id: poId,
      po_no: "PO-2026-001",
      method: "labeled",
      confidence: "0.99",
      state: currentState,
      evidence: "Purchase Order No: PO-2026-001",
      level: "header",
      line_no: null,
    });
    const invoice = {
      id: invoiceId,
      doc_type: "invoice",
      doc_no: "INV-2026-002",
      vendor_name: "Acme Components",
      vendor_gstin: "27AAPFU0939F1ZV",
      doc_date: "2026-04-02",
      grand_total: "236.00",
      currency: "INR",
      source_file_id: "file-synthetic-1",
      vendor_id: vendorId,
      needs_verification: false,
      links: [currentLink()],
      resolution: { outcome: "linked", resolved_step: 1, detail_code: null, cited_refs: [], candidates: [] },
    };
    const request = installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/vendors" && method === "GET") return response(200, [vendor]);
      if (url.pathname === "/api/v1/vendor-merge-suggestions" && method === "GET") return response(200, []);
      if (url.pathname === `/api/v1/vendors/${vendorId}/documents` && method === "GET") {
        return url.searchParams.get("doc_type") === "po" ? response(200, [{
          ...invoice,
          id: poId,
          doc_type: "po",
          doc_no: "PO-2026-001",
          vendor_name: "Acme Components",
          vendor_gstin: "27AAPFU0939F1ZV",
          links: [],
        }]) : response(200, [invoice]);
      }
      if (url.pathname === `/api/v1/documents/${invoiceId}/links/confirm` && method === "POST") {
        expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-mapping-test");
        expect(JSON.parse(String(init.body))).toEqual({ to_document_id: poId, links: [{ id: linkId, version_no: 1 }] });
        currentState = "confirmed";
        return response(200, { confirmed: 1, items: [currentLink()] });
      }
      throw new Error(`Unexpected request: ${method} ${url.pathname}${url.search}`);
    });

    renderMapping();
    const row = await screen.findByRole("row", { name: /INV-2026-002/ });
    expect(within(row).getByText("Acme Components")).toBeInTheDocument();
    expect(within(row).getByText("GSTIN 27AAPFU0939F1ZV")).toBeInTheDocument();
    expect(within(row).queryByRole("alert")).not.toBeInTheDocument();
    expect(within(row).getByText("Auto")).toBeInTheDocument();
    expect(within(row).getByText(/labeled · 99%/)).toBeInTheDocument();
    fireEvent.click(within(row).getByText("Evidence"));
    expect(within(row).getByText("Purchase Order No: PO-2026-001")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: /Confirm link/ }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      expect.stringContaining(`/api/v1/documents/${invoiceId}/links/confirm`),
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByRole("status")).toHaveTextContent(/1 link was confirmed/);
  });

  it("confirms an invoice's line links in one click, flags unlinked lines and survives choosing a PO", async () => {
    const lineLink = (lineNo: number) => ({
      id: `line-link-${lineNo}`,
      version_no: 1,
      po_document_id: poId,
      po_no: "PO-2026-001",
      method: "content",
      confidence: "0.88",
      state: "suggested",
      ai_suggested: false,
      evidence: null,
      level: "line",
      line_no: lineNo,
    });
    const invoice = {
      id: invoiceId,
      doc_type: "invoice",
      doc_no: "INV-2026-003",
      vendor_name: "Acme Components",
      vendor_gstin: "27AAPFU0939F1ZV",
      doc_date: "2026-04-02",
      grand_total: "236.00",
      currency: "INR",
      source_file_id: "file-synthetic-1",
      vendor_id: vendorId,
      needs_verification: false,
      links: [lineLink(1), lineLink(2)],
      line_nos: [1, 2, 3],
      resolution: { outcome: "suggested", resolved_step: 3, detail_code: null, cited_refs: [], candidates: [] },
    };
    const request = installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/vendors" && method === "GET") return response(200, [vendor]);
      if (url.pathname === "/api/v1/vendor-merge-suggestions" && method === "GET") return response(200, []);
      if (url.pathname === `/api/v1/vendors/${vendorId}/documents` && method === "GET") {
        return url.searchParams.get("doc_type") === "po"
          ? response(200, [{ ...invoice, id: poId, doc_type: "po", doc_no: "PO-2026-001", links: [], line_nos: [1] }])
          : response(200, [invoice]);
      }
      if (url.pathname === `/api/v1/documents/${invoiceId}/links/confirm` && method === "POST") {
        expect(JSON.parse(String(init.body))).toEqual({
          to_document_id: poId,
          links: [{ id: "line-link-1", version_no: 1 }, { id: "line-link-2", version_no: 1 }],
        });
        return response(200, { confirmed: 2, items: [] });
      }
      throw new Error(`Unexpected request: ${method} ${url.pathname}${url.search}`);
    });

    renderMapping();
    const row = await screen.findByRole("row", { name: /INV-2026-003/ });
    expect(within(row).getByText("2 of 3 lines linked")).toBeInTheDocument();
    expect(within(row).getByRole("note")).toHaveTextContent(/line 3 has no link to this PO/);
    // Choosing a replacement PO used to blank the page (the event was read inside a state updater).
    fireEvent.change(within(row).getByRole("combobox"), { target: { value: poId } });
    expect(within(row).getByRole("button", { name: "Change" })).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: /Confirm link \(2 lines\)/ }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      expect.stringContaining(`/api/v1/documents/${invoiceId}/links/confirm`),
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByRole("status")).toHaveTextContent(/2 links were confirmed/);
  });

  it("pins Unassigned and allows a person to assign a document", async () => {
    const unassigned = {
      ...vendor,
      id: null,
      canonical_name: "Unassigned",
      gstins: [],
      pan: null,
      unassigned: true,
      has_issues: true,
      counts: { po: 0, grn: 0, invoice: 1, linked: 0, suggested: 0, unlinked: 1 },
    };
    const invoice = {
      id: invoiceId,
      doc_type: "invoice",
      doc_no: "INV-UNASSIGNED-1",
      doc_date: "2026-04-02",
      grand_total: "100.00",
      currency: "INR",
      source_file_id: "file-synthetic-2",
      vendor_id: null,
      needs_verification: false,
      links: [],
      resolution: { outcome: "unlinked", resolved_step: null, detail_code: null, cited_refs: [], candidates: [] },
    };
    let assigned = false;
    const request = installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/vendors" && method === "GET") return response(200, assigned ? [vendor] : [unassigned, vendor]);
      if (url.pathname === "/api/v1/vendor-merge-suggestions" && method === "GET") return response(200, []);
      if (url.pathname === "/api/v1/vendors/unassigned/documents" && method === "GET") return response(200, [invoice]);
      if (url.pathname === `/api/v1/vendors/${vendorId}/documents` && method === "GET") return response(200, []);
      if (url.pathname === `/api/v1/documents/${invoiceId}/assign-vendor` && method === "POST") {
        expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-mapping-test");
        assigned = true;
        return response(200, { document_id: invoiceId, vendor_id: vendorId, vendor_locked: true });
      }
      throw new Error(`Unexpected request: ${method} ${url.pathname}${url.search}`);
    });

    renderMapping();
    expect(await screen.findByRole("heading", { name: "Unassigned" })).toBeInTheDocument();
    const row = await screen.findByRole("row", { name: /INV-UNASSIGNED-1/ });
    fireEvent.change(within(row).getByRole("combobox"), { target: { value: vendorId } });
    fireEvent.click(within(row).getByRole("button", { name: "Assign" }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      expect.stringContaining(`/api/v1/documents/${invoiceId}/assign-vendor`),
      expect.objectContaining({ method: "POST" }),
    ));
    expect(await screen.findByRole("status")).toHaveTextContent(/Vendor assignment saved/);
  });

  it("shows a warning if a vendor document response contains another supplier identity", async () => {
    const mismatchedInvoice = {
      id: invoiceId,
      doc_type: "invoice",
      doc_no: "INV-WRONG-VENDOR",
      vendor_name: "Different Supplier",
      vendor_gstin: "29AABCU9603R1ZM",
      doc_date: "2026-04-02",
      grand_total: "236.00",
      currency: "INR",
      source_file_id: "file-mismatched-vendor",
      vendor_id: vendorId,
      needs_verification: true,
      links: [],
      resolution: { outcome: "unlinked", resolved_step: null, detail_code: null, cited_refs: [], candidates: [] },
    };
    installFetch((url, init) => {
      if (url.pathname === "/api/v1/vendors" && (init.method ?? "GET") === "GET") {
        return response(200, [vendor]);
      }
      if (url.pathname === "/api/v1/vendor-merge-suggestions") return response(200, []);
      if (url.pathname === `/api/v1/vendors/${vendorId}/documents`) {
        return response(200, url.searchParams.get("doc_type") === "po" ? [] : [mismatchedInvoice]);
      }
      throw new Error(`Unexpected request: ${url.pathname}${url.search}`);
    });

    renderMapping();
    const row = await screen.findByRole("row", { name: /INV-WRONG-VENDOR/ });
    expect(within(row).getByRole("alert")).toHaveTextContent(
      /supplier identity differs from this vendor group/i,
    );
  });

  it("labels AI proposals as advisory and rejects one through the existing unlink", async () => {
    const aiLink = {
      id: linkId,
      version_no: 1,
      po_document_id: poId,
      po_no: "PO-2026-001",
      method: "content",
      confidence: "0.80",
      state: "suggested",
      ai_suggested: true,
      evidence: "Same laptop model and quantity.",
      level: "header",
      line_no: null,
    };
    const invoice = {
      id: invoiceId,
      doc_type: "invoice",
      doc_no: "INV-AI-1",
      vendor_name: "Acme Components",
      vendor_gstin: "27AAPFU0939F1ZV",
      doc_date: "2026-04-02",
      grand_total: "236.00",
      currency: "INR",
      source_file_id: "file-synthetic-ai",
      vendor_id: vendorId,
      needs_verification: false,
      links: [aiLink],
      resolution: { outcome: "unlinked", resolved_step: null, detail_code: "ai_rerank", cited_refs: [], candidates: [] },
    };
    const merge = {
      id: "merge-1",
      vendor_a_id: vendorId,
      vendor_b_id: "vendor-synthetic-2",
      score: "0.91",
      version: 1,
      vendor_a_name: "Acme Components",
      vendor_a_pan: null,
      vendor_a_gstins: [],
      vendor_b_name: "Acme Components Pvt Ltd",
      vendor_b_pan: null,
      vendor_b_gstins: [],
      pan_conflict: false,
      ai_opinion: "same",
      ai_reason: "Both names share the same registered trade name.",
    };
    const patches: Array<Record<string, unknown>> = [];
    installFetch((url, init) => {
      const method = init.method ?? "GET";
      if (url.pathname === "/api/v1/ai/settings") {
        return response(200, { mode: "ai_assisted", mode_label: "AI-assisted", provider: "gemini", model: "synthetic-model", external_calls: "enabled", status: { code: "NOT_TESTED", label: "Not tested since last change" } });
      }
      if (url.pathname === "/api/v1/vendors" && method === "GET") return response(200, [vendor]);
      if (url.pathname === "/api/v1/vendor-merge-suggestions") return response(200, [merge]);
      if (url.pathname === `/api/v1/vendors/${vendorId}/documents`) {
        return response(200, url.searchParams.get("doc_type") === "po" ? [] : [invoice]);
      }
      if (url.pathname === `/api/v1/links/${linkId}` && method === "PATCH") {
        patches.push(JSON.parse(String(init.body)) as Record<string, unknown>);
        return response(200, { ...aiLink, state: "rejected" });
      }
      throw new Error(`Unexpected request: ${method} ${url.pathname}${url.search}`);
    });

    renderMapping();
    expect(await screen.findByRole("note", { name: "AI external sharing notice" })).toHaveTextContent("AI Assist is on: AI-assisted");
    const row = await screen.findByRole("row", { name: /INV-AI-1/ });
    expect(within(row).getByText("AI-suggested")).toBeInTheDocument();
    expect(within(row).getByText(/Not used in reconciliation until a person confirms it/)).toBeInTheDocument();
    expect(within(row).getByRole("button", { name: /Confirm/ })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Review" }));
    expect(screen.getByText(/Likely the same supplier: Both names share the same registered trade name/)).toBeInTheDocument();
    expect(screen.getByText("AI opinion · advisory")).toBeInTheDocument();

    fireEvent.click(within(row).getByRole("button", { name: /Reject/ }));
    await waitFor(() => expect(patches).toEqual([{ action: "unlink", to_document_id: null }]));
  });
});
