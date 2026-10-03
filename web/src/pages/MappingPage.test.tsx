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
          links: [],
        }]) : response(200, [invoice]);
      }
      if (url.pathname === `/api/v1/links/${linkId}` && method === "PATCH") {
        expect(new Headers(init.headers).get("If-Match")).toBe('"1"');
        expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-mapping-test");
        currentState = "confirmed";
        return response(200, currentLink());
      }
      throw new Error(`Unexpected request: ${method} ${url.pathname}${url.search}`);
    });

    renderMapping();
    const row = await screen.findByRole("row", { name: /INV-2026-002/ });
    expect(within(row).getByText("Auto")).toBeInTheDocument();
    expect(within(row).getByText(/labeled · 99%/)).toBeInTheDocument();
    fireEvent.click(within(row).getByText("Evidence"));
    expect(within(row).getByText("Purchase Order No: PO-2026-001")).toBeInTheDocument();
    fireEvent.click(within(row).getByRole("button", { name: /Confirm/ }));
    await waitFor(() => expect(request).toHaveBeenCalledWith(
      expect.stringContaining(`/api/v1/links/${linkId}`),
      expect.objectContaining({ method: "PATCH" }),
    ));
    expect(await screen.findByRole("status")).toHaveTextContent(/Link confirm was saved/);
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
});
