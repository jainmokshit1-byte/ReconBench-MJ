import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { SettingsPage } from "./SettingsPage";

const capabilities = ["data.view", "settings.change", "links.edit"];
const managerContext: AuthContextValue = {
  status: "authenticated",
  user: { name: "Synthetic Manager", role: "manager", csrf_token: "csrf-settings-test", capabilities },
  expired: false,
  signIn: vi.fn(async () => undefined),
  signOut: vi.fn(async () => undefined),
  hasCapability: (capability) => capabilities.includes(capability),
};

const vendorId = "11111111-1111-1111-1111-111111111111";
const tolerance = (pct: string, abs: string) => ({ pct, abs });

function engineBody() {
  return {
    tolerances: { limit_mode: "or", qty_vs_grn: tolerance("0", "0"), cum_qty_vs_po: tolerance("0", "0"), unit_rate: tolerance("0.5", "1"), total_vs_expected: tolerance("1", "10"), arithmetic: tolerance("0", "1"), cum_value_vs_po: tolerance("0", "0") },
    thresholds: { extraction_gate: "0.70", link_auto_accept: "0.95", vendor_fuzzy_merge: "0.92", vendor_fuzzy_suggest: "0.80", item_aligned: "0.80", item_review: "0.60", item_single_line_explicit: "0.50", spec_conflict_cap: "0.60", step3_min_score: "0.70", step3_min_lead: "0.10" },
    policies: { payable_rate_mode: "pay_at_po_rate", date_order_level: "fail", pending_ageing_days: 30, duplicate: { suspect_days: 3, suspect_edit_distance: 2 }, reverse_lookup: { min_canonical_len: 4, window_runs: 6 }, nonexact_tail_min_digits: 4 },
    synonyms: { po_refs: ["PO No"], grn_refs: [], supplier_invoice_no: [], invoice_no: [], vendor_name: [], buyer: [], quantity: [], rate_amount: [], totals: [], dates: [], tax: [] },
    uom: { aliases: { PCS: "NOS", KGS: "KG" }, conversions: [] },
    charge_keywords: ["freight"],
    buyer_entities: [],
    gst_slabs: [{ rate: "18", from: null, to: null }],
    payment_terms: [{ label: "Net 30", days: 30 }],
    overrides: { vendors: [], items: [] },
  };
}

function configuration(seq: number, mutate?: (engine: ReturnType<typeof engineBody>) => void) {
  const engine = engineBody();
  mutate?.(engine);
  return { seq, body: { engine, app: { ui: { poll_seconds: 5 } } }, defaults: { engine: engineBody() }, engine_hash: `engine-${seq}`, rules_hash: `rules-${seq}` };
}

function response(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
}

interface Saved { ifMatch: string | null; body: { engine: ReturnType<typeof engineBody> } }

function installFetch(putResult?: () => Response) {
  const saved: Saved[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), "http://localhost").pathname.replace("/api/v1", "");
    const method = init?.method ?? "GET";
    if (path === "/config" && method === "GET") return response(200, configuration(1), { ETag: '"1"' });
    if (path === "/config" && method === "PUT") {
      const body = JSON.parse(String(init?.body)) as Saved["body"];
      saved.push({ ifMatch: new Headers(init?.headers).get("If-Match"), body });
      return putResult ? putResult() : response(200, { ...configuration(2), body }, { ETag: '"2"' });
    }
    if (path === "/vendor-aliases") return response(200, []);
    if (path === "/vendors") return response(200, [{ id: vendorId, canonical_name: "Acme Components", gstins: [], pan: null, anchor: "gstin", aliases: [], counts: { po: 1, grn: 0, invoice: 1, linked: 1, suggested: 0, unlinked: 0 }, has_issues: false, unassigned: false }]);
    return response(500, { detail: `Unexpected ${method} ${path}` });
  });
  vi.stubGlobal("fetch", fetchMock);
  return saved;
}

function renderPage() {
  render(<AuthContext.Provider value={managerContext}><MemoryRouter><SettingsPage /></MemoryRouter></AuthContext.Provider>);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SettingsPage", () => {
  it("shows defaults beside changed values and resets them", async () => {
    installFetch();
    renderPage();
    const rate = await screen.findByLabelText("Invoice unit rate vs PO rate percentage tolerance");
    expect(screen.queryByRole("region", { name: "Unsaved settings" })).toBeNull();
    fireEvent.change(rate, { target: { value: "2" } });
    expect(screen.getByText(/±2% or ±₹1 per unit/)).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Unsaved settings" })).getByText("1 unsaved change")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reset Invoice unit rate vs PO rate percentage to default" }));
    expect((rate as HTMLInputElement).value).toBe("0.5");
    expect(screen.queryByRole("region", { name: "Unsaved settings" })).toBeNull();
  });

  it("blocks saving an invalid buyer GSTIN and accepts a valid one", async () => {
    installFetch();
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Add buyer entity" }));
    fireEvent.change(screen.getByLabelText("Buyer entity 1 name"), { target: { value: "Benchmark Mumbai" } });
    const gstin = screen.getByLabelText("Buyer entity 1 GSTIN");
    fireEvent.change(gstin, { target: { value: "27aapfu0939f1zw" } });
    expect(screen.getAllByText(/check character does not match/).length).toBeGreaterThan(0);
    expect((screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(gstin, { target: { value: "27AAPFU0939F1ZV" } });
    expect(screen.getByText(/Valid GSTIN · state 27/)).toBeTruthy();
    expect((screen.getByRole("button", { name: "Save settings" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("saves a vendor override, synonym and UOM alias as one versioned body with If-Match", async () => {
    const saved = installFetch();
    renderPage();
    await screen.findByLabelText("Invoice unit rate vs PO rate percentage tolerance");
    await waitFor(() => expect(screen.getAllByRole("option", { name: "Acme Components" }).length).toBeGreaterThan(0));
    fireEvent.change(screen.getByLabelText("Override vendor"), { target: { value: vendorId } });
    fireEvent.click(screen.getByRole("button", { name: "Add override" }));
    fireEvent.change(screen.getByLabelText("Vendor · Acme Components Invoice unit rate vs PO rate percentage"), { target: { value: "1.5" } });
    const poTags = screen.getByLabelText("Add PO number synonym");
    fireEvent.change(poTags, { target: { value: "Order Ref" } });
    fireEvent.keyDown(poTags, { key: "Enter" });
    fireEvent.change(screen.getByLabelText("New printed unit"), { target: { value: "ea" } });
    fireEvent.change(screen.getByLabelText("New canonical unit"), { target: { value: "nos" } });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    await screen.findByText(/Configuration saved as version 2/);
    expect(screen.getByRole("link", { name: "Open Reconciliation to re-run" }).getAttribute("href")).toBe("/reconciliation");
    expect(saved).toHaveLength(1);
    expect(saved[0]?.ifMatch).toBe('"1"');
    const engine = saved[0]!.body.engine;
    expect(engine.overrides.vendors).toEqual([{ vendor_id: vendorId, tolerances: { unit_rate: { pct: "1.5", abs: "1" } } }]);
    expect(engine.synonyms.po_refs).toEqual(["PO No", "Order Ref"]);
    expect(engine.uom.aliases).toMatchObject({ EA: "NOS", PCS: "NOS" });
  });

  it("shows what another user changed on a 409 and loads their version", async () => {
    const theirs = configuration(3, (engine) => { engine.policies.payable_rate_mode = "hold"; });
    installFetch(() => response(409, { status: 409, detail: "This resource changed after it was loaded. Reload the current state.", current: { version: 3, etag: '"3"', config: theirs } }));
    renderPage();
    fireEvent.change(await screen.findByLabelText("Pending ageing days"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    const dialog = await screen.findByRole("dialog", { name: "Someone saved settings first" });
    expect(within(dialog).getByText("engine.policies.payable_rate_mode")).toBeTruthy();
    expect(within(dialog).getByText("hold")).toBeTruthy();
    fireEvent.click(within(dialog).getByRole("button", { name: "Load version 3" }));
    expect((screen.getByLabelText("Payable rate mode") as HTMLSelectElement).value).toBe("hold");
    expect((screen.getByLabelText("Pending ageing days") as HTMLInputElement).value).toBe("30");
  });

  it("lists server-rejected field locations on a 422", async () => {
    installFetch(() => response(422, { status: 422, detail: "Request validation failed.", errors: [{ location: ["engine", "gst_slabs", 0, "rate"], code: "invalid_field", detail: "This field is invalid." }] }));
    renderPage();
    fireEvent.change(await screen.findByLabelText("GST slab 1 rate"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText(/engine › gst_slabs › 0 › rate/)).toBeTruthy();
    expect(screen.getByText("The server rejected this value.")).toBeTruthy();
  });
});
