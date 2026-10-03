/* global window, URL, setTimeout, document, HTMLElement, requestAnimationFrame, PerformanceObserver, console, navigator */

import { expect, test } from "@playwright/test";

const manager = {
  name: "Synthetic Manager",
  role: "manager",
  csrf_token: "csrf-e2e",
  capabilities: [
    "files.upload",
    "links.edit",
    "recon.run",
    "cases.decide",
    "cases.approve_exception",
    "settings.change",
    "ai.manage",
    "export.create",
    "data.view",
  ],
};

async function json(route, body, status = 200, headers = {}) {
  await route.fulfill({
    status,
    contentType: "application/json",
    headers,
    body: JSON.stringify(body),
  });
}

async function noContent(route) {
  await route.fulfill({ status: 204 });
}

function caseSummary({ status = "exception", stale = false, index = 1 } = {}) {
  const id = `case-${index}`;
  return {
    case_id: id,
    run_id: "run-e2e",
    invoice_id: `invoice-${index}`,
    vendor_id: "vendor-1",
    invoice_date: "2026-09-18",
    invoice_no: `INV-E2E-${String(index).padStart(5, "0")}`,
    canonical_doc_no: `INV-E2E-${String(index).padStart(5, "0")}`,
    computed_status: "exception",
    overlay: status === "approved" ? "approved" : null,
    effective_status: status,
    severity_rank: 1,
    human_touched: status === "approved",
    version: status === "approved" ? 2 : 1,
    reason_codes: ["UNIT_RATE_MISMATCH"],
    po_nos: ["PO-E2E-1"],
    grn_nos: ["GRN-E2E-1"],
    invoice_taxable: "1180.00",
    invoice_total: "1392.40",
    expected_taxable: "1000.00",
    variance: "180.00",
    at_risk: "180.00",
    is_stale: stale,
  };
}

function caseDetail(overlay = null, etagVersion = 1, stale = false) {
  return {
    case: {
      id: "case-1",
      case_id: "case-1",
      invoice_no: "INV-E2E-00001",
      effective_status: overlay ?? "exception",
      status: "exception",
      overlay,
      is_stale: stale,
      etag_version: etagVersion,
      invoice_total: "1392.40",
      expected_taxable: "1000.00",
      variance: "180.00",
      at_risk: "180.00",
      checks: [{ check_id: "unit_rate", result: "fail", code: "UNIT_RATE_MISMATCH" }],
    },
    lines: [{
      line_no: 1,
      alignment_state: "aligned",
      billed_qty: "1",
      po_qty: "2",
      grn_accepted_cum: "1",
      billed_before: "0",
      balance_after: "1",
      status: "exception",
    }],
    documents: [],
    history: overlay ? [{ id: "action-1", action: "approve", reason: "Finance approved after review", occurred_at: "2026-09-20T10:00:00Z" }] : [],
  };
}

async function setPeriod(page) {
  await page.addInitScript(() => window.localStorage.setItem("reconbench-period", "2026-09"));
}

test("mapping accepts an unassigned vendor, reviews identity, confirms a link, bulk-confirms, and starts a run", async ({ page }) => {
  let assigned = false;
  let suggestionOpen = true;
  let linkState = "suggested";
  let runBody;
  const calls = [];

  const vendor = {
    id: "vendor-1",
    canonical_name: "Acer India Pvt Ltd",
    gstins: ["29ABCDE1234F1Z5"],
    pan: "ABCDE1234F",
    anchor: "gstin",
    aliases: [],
    counts: { po: 1, grn: 1, invoice: 1, linked: linkState === "confirmed" ? 1 : 0, suggested: linkState === "suggested" ? 1 : 0, unlinked: 0 },
    has_issues: false,
    unassigned: false,
  };
  const unassigned = {
    id: null,
    canonical_name: "Unassigned",
    gstins: [],
    pan: null,
    anchor: "none",
    counts: { po: 0, grn: 0, invoice: 1, linked: 0, suggested: 0, unlinked: 1 },
    has_issues: true,
    unassigned: true,
  };
  const invoice = {
    id: "doc-invoice",
    doc_type: "invoice",
    doc_no: "INV-MAP-001",
    doc_date: "2026-09-18",
    grand_total: "1180.00",
    currency: "INR",
    source_file_id: "file-invoice",
    vendor_id: assigned ? "vendor-1" : null,
    needs_verification: false,
    links: [{ id: "link-invoice", version_no: 1, po_document_id: "doc-po", po_no: "PO-MAP-001", method: "content", confidence: "0.90", state: linkState, evidence: "Description and HSN agree", level: "document", line_no: null }],
    resolution: { outcome: "suggested", resolved_step: 4, detail_code: null, cited_refs: [], candidates: [{ po_no: "PO-MAP-001", score: "0.90" }] },
  };
  const po = {
    id: "doc-po",
    doc_type: "po",
    doc_no: "PO-MAP-001",
    doc_date: "2026-09-01",
    grand_total: "1180.00",
    currency: "INR",
    source_file_id: "file-po",
    vendor_id: "vendor-1",
    needs_verification: false,
    links: [],
    resolution: null,
  };
  const mergeSuggestion = {
    id: "merge-1",
    vendor_a_id: "vendor-1",
    vendor_b_id: "vendor-2",
    score: "0.94",
    version: 1,
    vendor_a_name: "Acer India Pvt Ltd",
    vendor_a_pan: "ABCDE1234F",
    vendor_a_gstins: ["29ABCDE1234F1Z5"],
    vendor_b_name: "Acer India Limited",
    vendor_b_pan: "ABCDE1234F",
    vendor_b_gstins: [],
    pan_conflict: false,
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    calls.push({ method: request.method(), path, body: request.postDataJSON?.() });
    if (path === "/auth/me") return json(route, manager);
    if (path === "/runs" && request.method() === "GET") return json(route, []);
    if (path === "/runs" && request.method() === "POST") {
      runBody = request.postDataJSON();
      return json(route, { id: "run-mapping-e2e" }, 202);
    }
    if (path === "/runs/run-mapping-e2e") return json(route, {
      id: "run-mapping-e2e", status: "succeeded", done_cases: 1, total_cases: 1,
      status_counts: { exception: 1 }, error_code: null, error_message: null,
      started_at: "2026-09-18T10:00:00Z", finished_at: "2026-09-18T10:00:01Z", metrics: {},
    });
    if (path === "/vendors" && request.method() === "GET") return json(route, assigned ? [vendor] : [unassigned, vendor]);
    if (path === "/vendor-merge-suggestions" && request.method() === "GET") return json(route, suggestionOpen ? [mergeSuggestion] : []);
    if (path === "/vendors/unassigned/documents") return json(route, assigned ? [] : [invoice]);
    if (path === "/vendors/vendor-1/documents") {
      const type = url.searchParams.get("doc_type");
      if (type === "po") return json(route, [po]);
      if (type === "invoice") return json(route, [{ ...invoice, vendor_id: "vendor-1", links: [{ ...invoice.links[0], state: linkState }] }]);
      return json(route, []);
    }
    if (path === "/documents/doc-invoice/assign-vendor") {
      assigned = true;
      return json(route, { vendor_id: "vendor-1", vendor_locked: true });
    }
    if (path === "/vendor-merge-suggestions/merge-1/decision") {
      suggestionOpen = false;
      return json(route, { decision: "accept", survivor_vendor_id: "vendor-1" });
    }
    if (path === "/links/link-invoice" && request.method() === "PATCH") {
      linkState = "confirmed";
      return json(route, { ...invoice.links[0], state: linkState });
    }
    if (path === "/links/bulk-confirm") return json(route, { confirmed: 1, items: [{ status: 200 }] });
    if (path === "/cases") return json(route, { items: [], next_cursor: null, limit: 100, total_count: 0 });
    if (path === "/kpis") return json(route, { invoice_count: 1, matched_pct: 0, pending_count: 0, exception_count: 1, unlinked_count: 0, at_risk_total: "180.00", grni_total: "0.00" });
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await setPeriod(page);
  await page.goto("/mapping");
  await expect(page.getByRole("heading", { name: "Unassigned" })).toBeVisible();
  await page.getByLabel("Assign INV-MAP-001 to vendor").selectOption("vendor-1");
  await page.getByRole("button", { name: "Assign", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Acer India Pvt Ltd" })).toBeVisible();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "Copy vendor link" }).click();
  const copiedVendorUrl = new URL(await page.evaluate(() => navigator.clipboard.readText()));
  expect(copiedVendorUrl.searchParams.get("vendor_id")).toBe("vendor-1");
  expect(copiedVendorUrl.searchParams.has("document_id")).toBe(false);

  await page.getByRole("button", { name: "Copy link to INV-MAP-001" }).click();
  const copiedDocumentUrl = new URL(await page.evaluate(() => navigator.clipboard.readText()));
  expect(copiedDocumentUrl.searchParams.get("document_id")).toBe("doc-invoice");
  expect(copiedDocumentUrl.searchParams.get("doc_type")).toBe("invoice");
  await page.goto(`${copiedDocumentUrl.pathname}${copiedDocumentUrl.search}`);
  await expect(page.getByText("Opened from shared link")).toBeVisible();

  await page.getByRole("button", { name: "Review", exact: true }).click();
  await page.getByRole("button", { name: "Keep Acer India Pvt Ltd" }).click();
  await expect(page.getByRole("region", { name: "Vendor merge suggestions" })).toHaveCount(0);

  await page.getByRole("button", { name: "Confirm", exact: true }).click();
  await expect(page.getByText("Link confirm was saved.")).toBeVisible();
  await page.getByRole("button", { name: "Confirm all ≥ 0.95" }).click();
  await expect(page.getByText("Eligible links at or above 0.95 were confirmed.")).toBeVisible();
  await page.getByRole("button", { name: "Run reconciliation" }).click();
  await expect(page.getByRole("heading", { name: "Reconciliation" })).toBeVisible();
  await expect(page).toHaveURL(/run_id=run-mapping-e2e/);
  expect(runBody).toEqual({ scope: { kind: "vendor", vendor_id: "vendor-1" } });
  expect(calls.some((call) => call.path === "/documents/doc-invoice/assign-vendor" && call.method === "POST")).toBe(true);
  expect(calls.some((call) => call.path === "/links/link-invoice" && call.method === "PATCH")).toBe(true);
  expect(calls.some((call) => call.path === "/links/bulk-confirm" && call.method === "POST")).toBe(true);
});

test("reconciliation runs, filters, loads a single case bundle, shows the ledger, and approves with a reason", async ({ page }) => {
  let overlay = null;
  let actionBody;
  let actionHeaders;
  let detailRequests = 0;
  let exportBody;
  let exportCreateCount = 0;
  const runBody = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return json(route, manager);
    if (path === "/runs?limit=10") return json(route, []);
    if (path === "/runs" && request.method() === "POST") {
      runBody.push(request.postDataJSON());
      return json(route, { id: "run-e2e" }, 202);
    }
    if (path === "/runs/run-e2e") return json(route, {
      id: "run-e2e", status: "succeeded", done_cases: 1, total_cases: 1,
      status_counts: { exception: 1 }, error_code: null, error_message: null,
      started_at: "2026-09-18T10:00:00Z", finished_at: "2026-09-18T10:00:01Z", metrics: {},
    });
    if (path === "/cases" && request.method() === "GET") {
      const status = url.searchParams.get("status");
      const visible = overlay ? [] : [caseSummary()];
      return json(route, { items: status === "exception" ? visible : visible, next_cursor: null, limit: 100, total_count: visible.length });
    }
    if (path === "/kpis") return json(route, { invoice_count: 1, matched_pct: 0, pending_count: 0, exception_count: 1, unlinked_count: 0, at_risk_total: "180.00", grni_total: "0.00" });
    if (path === "/cases/case-1" && request.method() === "GET") {
      detailRequests += 1;
      if (detailRequests === 1) await new Promise((resolve) => setTimeout(resolve, 300));
      return json(route, caseDetail(overlay, overlay ? 2 : 1), 200, { ETag: `"${overlay ? 2 : 1}"` });
    }
    if (path === "/cases/case-1/actions" && request.method() === "POST") {
      actionBody = request.postDataJSON();
      actionHeaders = request.headers();
      overlay = "approved";
      return noContent(route);
    }
    if (path === "/exports" && request.method() === "POST") {
      exportCreateCount += 1;
      exportBody = request.postDataJSON();
      return json(route, { id: "export-e2e", status: "queued" }, 202);
    }
    if (path === "/exports/export-e2e") return json(route, {
      id: "export-e2e", run_id: "run-e2e", status: "ready", error_code: null, error_message: null, version: 1, expires_at: null,
    });
    if (path === "/exports/export-e2e/download") {
      await route.fulfill({ status: 200, contentType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", body: "synthetic-browser-download" });
      return;
    }
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await setPeriod(page);
  await page.goto("/reconciliation");
  await page.getByRole("button", { name: "Run reconciliation" }).click();
  await expect(page.getByText("Run completed: 1 invoice cases evaluated.")).toBeVisible();
  expect(runBody).toEqual([{ scope: { kind: "all" } }]);

  await page.getByRole("combobox", { name: "Status" }).selectOption("exception");
  const invoiceRow = page.getByRole("row", { name: /INV-E2E-00001/ });
  await expect(invoiceRow).toBeVisible();
  await invoiceRow.click();
  await expect(page.getByRole("dialog", { name: "Loading case details" })).toBeVisible();
  const panel = page.getByRole("dialog", { name: "INV-E2E-00001" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("region", { name: "Cumulative PO and GRN ledger" })).toContainText("PO ordered");
  await expect(panel.getByRole("region", { name: "Cumulative PO and GRN ledger" })).toContainText("This invoice");
  await expect(panel.getByRole("region", { name: "Cumulative PO and GRN ledger" })).toContainText("GRN accepted");
  expect(detailRequests).toBe(1);

  await panel.getByRole("button", { name: "Approve" }).click();
  await page.getByLabel("Reason (required)").fill("Finance approved after review");
  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(panel.locator(".case-panel-status")).toContainText("approved");
  await expect(panel.locator(".case-panel-status")).toContainText("Computed result: exception");
  expect(actionBody).toEqual({ action: "approve", reason: "Finance approved after review" });
  expect(actionHeaders["if-match"]).toBe('"1"');
  expect(actionHeaders["x-csrf-token"]).toBe("csrf-e2e");
  expect(detailRequests).toBe(2);

  await panel.getByRole("button", { name: "Close case details" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export Excel" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("reconbench-export-e2e.xlsx");
  await expect(page.getByText("Excel export is ready and has been downloaded.")).toBeVisible();
  expect(exportCreateCount).toBe(1);
  expect(exportBody.run_id).toBe("run-e2e");
  expect(exportBody.filter.status).toEqual(["exception"]);
});

test("Settings changes are versioned, AI stays offline, and reconciliation marks old cases stale", async ({ page }) => {
  let config = { seq: 1, body: { engine: { tolerances: { limit_mode: "or", unit_rate: { pct: "0.005", abs: "0" } }, synonyms: { po_refs: ["PO No"] }, uom: { aliases: {}, conversions: [] }, buyer_entities: [], gst_slabs: [], payment_terms: [], overrides: { vendors: [], items: [] } } }, engine_hash: "engine-1", rules_hash: "rules-1" };
  let savedEtag;
  let savedBody;
  let aiResponse;
  let aliases = [];
  const vendorRows = [{ id: "vendor-1", canonical_name: "Acer India Pvt Ltd", gstins: ["29ABCDE1234F1Z5"], pan: "ABCDE1234F", anchor: "gstin", counts: { po: 1, grn: 1, invoice: 1, linked: 1, suggested: 0, unlinked: 0 }, has_issues: false, unassigned: false }];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return json(route, manager);
    if (path === "/runs?limit=10") return json(route, []);
    if (path === "/config" && request.method() === "GET") return json(route, config, 200, { ETag: `"${config.seq}"` });
    if (path === "/config" && request.method() === "PUT") {
      savedEtag = request.headers()["if-match"];
      savedBody = request.postDataJSON();
      config = { ...config, seq: 2, body: savedBody };
      return json(route, config, 200, { ETag: '"2"' });
    }
    if (path === "/vendors" && request.method() === "GET") return json(route, vendorRows);
    if (path === "/vendor-aliases" && request.method() === "GET") return json(route, aliases);
    if (path === "/vendor-aliases" && request.method() === "POST") {
      const body = request.postDataJSON();
      const alias = { id: "alias-1", vendor_id: body.vendor_id, alias_raw: body.alias, alias_norm: body.alias.toLowerCase(), source: "manual" };
      aliases = [alias];
      return json(route, alias);
    }
    if (path === "/vendor-aliases/alias-1" && request.method() === "DELETE") {
      aliases = [];
      return json(route, { deleted: true });
    }
    if (path === "/ai/test" && request.method() === "POST") {
      aiResponse = { status: 200, headers: { "content-type": "application/json" }, body: { passed: true, checks: { strict_schema: true, literal_grounding: true, spec_conflict_guard: true }, provider: "stub", external_provider_called: false, financial_verdict_changed: false, status_code: "rules_only" } };
      return json(route, aiResponse.body);
    }
    if (path === "/cases" && request.method() === "GET") return json(route, { items: [caseSummary({ stale: true })], next_cursor: null, limit: 100, total_count: 1 });
    if (path === "/kpis") return json(route, { invoice_count: 1, matched_pct: 0, pending_count: 0, exception_count: 1, unlinked_count: 0, at_risk_total: "180.00", grni_total: "0.00" });
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await setPeriod(page);
  await page.goto("/settings");
  const tolerance = page.getByLabel("Invoice unit rate vs PO rate percentage tolerance");
  await tolerance.fill("0.01");
  await page.getByText("Payment terms", { exact: true }).click();
  const paymentTerms = page.getByLabel("Payment terms JSON");
  await paymentTerms.fill("invalid json");
  await expect(page.getByRole("button", { name: "Save settings" })).toBeDisabled();
  await paymentTerms.fill('[{"label":"Net 30","days":30}]');
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText(/Configuration saved as a new version/)).toBeVisible();
  expect(savedEtag).toBe('"1"');
  expect(savedBody.engine.payment_terms).toEqual([{ label: "Net 30", days: 30 }]);

  await page.getByLabel("Printed vendor alias").fill("Acer Computer Systems");
  await page.getByRole("button", { name: "Add alias" }).click();
  await expect(page.getByText("Acer Computer Systems")).toBeVisible();
  await page.getByRole("button", { name: "Remove alias Acer Computer Systems" }).click();
  await expect(page.getByText("No vendor aliases are saved yet.")).toBeVisible();

  await page.getByRole("button", { name: "Run offline guardrail test" }).click();
  await expect(page.getByText(/No invoice data was sent to a provider/)).toBeVisible();
  expect(aiResponse.body.external_provider_called).toBe(false);

  await page.getByRole("link", { name: "Reconciliation" }).click();
  await expect(page.getByText(/These cases are stale/)).toBeVisible();
});

test("Home Next step routes a new workspace to ingestion and the period is explicitly invoice-date scoped", async ({ page }) => {
  const requestedKpis = [];
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return json(route, manager);
    if (path === "/runs?limit=10") return json(route, []);
    if (path === "/kpis") {
      requestedKpis.push(url.searchParams.toString());
      return json(route, { invoice_count: 0, matched_pct: null, pending_count: 0, exception_count: 0, unlinked_count: 0, at_risk_total: "0.00", grni_total: "0.00" });
    }
    if (path === "/batches") return json(route, []);
    if (path === "/ingestion/client-config") return json(route, { max_file_bytes: 10485760, accepted_extensions: [".pdf", ".xml", ".csv", ".zip"] });
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await setPeriod(page);
  await page.goto("/home");
  await expect(page.getByRole("heading", { name: "ReconBench Home" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Upload documents/ })).toBeVisible();
  await page.getByRole("link", { name: /Upload documents/ }).click();
  await expect(page.getByRole("heading", { name: "Ingestion" })).toBeVisible();
  expect(requestedKpis.some((query) => query.includes("from=2026-09-01") && query.includes("to=2026-09-30"))).toBe(true);
});

test("a 10,000-row result set stays virtualized while scrolling", async ({ page }) => {
  const rows = Array.from({ length: 10_000 }, (_, index) => caseSummary({ index: index + 1, status: "exception" }));
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return json(route, manager);
    if (path === "/runs?limit=10") return json(route, []);
    if (path === "/cases") return json(route, { items: rows, next_cursor: null, limit: 10000, total_count: rows.length });
    if (path === "/kpis") return json(route, { invoice_count: 10000, matched_pct: 0, pending_count: 0, exception_count: 10000, unlinked_count: 0, at_risk_total: "1800000.00", grni_total: "0.00" });
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await page.setViewportSize({ width: 1366, height: 768 });
  await setPeriod(page);
  await page.goto("/reconciliation");
  const table = page.locator(".recon-table-wrap");
  await expect(page.getByText("INV-E2E-00001")).toBeVisible();
  const renderedBeforeScroll = await page.locator(".recon-table tbody tr[data-index]").count();
  expect(renderedBeforeScroll).toBeLessThan(100);
  await table.evaluate((element) => element.scrollTo({ top: element.scrollHeight, behavior: "instant" }));
  await expect.poll(() => table.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect(page.getByText("INV-E2E-10000")).toBeVisible();
  const renderedAfterScroll = await page.locator(".recon-table tbody tr[data-index]").count();
  expect(renderedAfterScroll).toBeLessThan(100);
  const frameTiming = await page.evaluate(async () => {
    const scrollArea = document.querySelector(".recon-table-wrap");
    if (!(scrollArea instanceof HTMLElement)) throw new Error("Reconciliation scroll area was not found");
    const frameTimes = [];
    const longTasks = [];
    scrollArea.scrollTop = 0;
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    await new Promise((resolve) => setTimeout(resolve, 600));
    const observer = typeof PerformanceObserver === "undefined" ? null : new PerformanceObserver((entries) => {
      for (const entry of entries.getEntries()) {
        const attribution = entry.attribution?.map((item) => ({ container_type: item.containerType, script: item.scriptUrl, function: item.name })) ?? [];
        longTasks.push({ start_ms: entry.startTime, duration_ms: entry.duration, attribution });
      }
    });
    observer?.observe({ type: "longtask" });
    let previous = await new Promise((resolve) => requestAnimationFrame(resolve));
    for (let index = 0; index < 90; index += 1) {
      scrollArea.scrollTop += 36;
      const current = await new Promise((resolve) => requestAnimationFrame(resolve));
      frameTimes.push(current - previous);
      previous = current;
    }
    frameTimes.sort((left, right) => left - right);
    observer?.disconnect();
    return { p95_ms: frameTimes[Math.floor(frameTimes.length * 0.95)], max_ms: frameTimes.at(-1), long_tasks: longTasks };
  });
  test.info().annotations.push({ type: "reconciliation-frame-timing", description: JSON.stringify(frameTiming) });
  console.log("10k-table frame sample", frameTiming);
  expect(frameTiming.p95_ms).toBeLessThanOrEqual(50);
  expect(frameTiming.max_ms).toBeLessThanOrEqual(100);
  expect(frameTiming.long_tasks).toEqual([]);
});

test("Admin error retry uses the authenticated retry endpoint and clears the retry row", async ({ page }) => {
  let retried = false;
  let retryHeaders;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return json(route, manager);
    if (path === "/admin/errors") return json(route, {
      failed_by_reason_and_day: [{ error_code: "XML_PARSE_ERROR", day: "2026-09-18", count: 1 }],
      top_failing_layouts: [],
      top_failing_vendors: [],
      unresolved_warnings: [],
      retry_actions: retried ? [] : [{ id: "file-failed", display_name: "bad-layout.xml", error_code: "XML_PARSE_ERROR", parse_finished_at: "2026-09-18T10:00:00Z" }],
    });
    if (path === "/files/file-failed/retry" && request.method() === "POST") {
      retryHeaders = request.headers();
      retried = true;
      return noContent(route);
    }
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await page.goto("/admin/errors");
  await expect(page.getByText("XML_PARSE_ERROR").first()).toBeVisible();
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("No files are waiting for retry.")).toBeVisible();
  expect(retryHeaders["x-csrf-token"]).toBe("csrf-e2e");
});

test("a stale case remains inspectable to read-only users without decision controls", async ({ page }) => {
  const auditor = { ...manager, name: "Synthetic Auditor", role: "auditor", capabilities: manager.capabilities.filter((capability) => capability !== "cases.decide" && capability !== "cases.approve_exception") };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return json(route, auditor);
    if (path === "/runs?limit=10") return json(route, []);
    if (path === "/cases") return json(route, { items: [caseSummary({ stale: true })], next_cursor: null, limit: 100, total_count: 1 });
    if (path === "/kpis") return json(route, { invoice_count: 1, matched_pct: 0, pending_count: 0, exception_count: 1, unlinked_count: 0, at_risk_total: "180.00", grni_total: "0.00" });
    if (path === "/cases/case-1") return json(route, caseDetail(null, 1, true), 200, { ETag: '"1"' });
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await setPeriod(page);
  await page.goto("/reconciliation");
  const row = page.getByRole("row", { name: /INV-E2E-00001/ });
  await expect(row).toBeVisible();
  await row.click();
  const panel = page.getByRole("dialog", { name: "INV-E2E-00001" });
  await expect(panel.getByText("Read-only view. You do not have permission to change this case.")).toBeVisible();
  await expect(panel.getByText(/Mapping or settings changed/)).toBeVisible();
  await expect(panel.getByRole("button", { name: "Approve" })).toHaveCount(0);
});

test("a stale optimistic case decision surfaces the 409 and reloads the current case", async ({ page }) => {
  let detailReads = 0;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") return json(route, manager);
    if (path === "/runs?limit=10") return json(route, []);
    if (path === "/cases") return json(route, { items: [caseSummary()], next_cursor: null, limit: 100, total_count: 1 });
    if (path === "/kpis") return json(route, { invoice_count: 1, matched_pct: 0, pending_count: 0, exception_count: 1, unlinked_count: 0, at_risk_total: "180.00", grni_total: "0.00" });
    if (path === "/cases/case-1" && request.method() === "GET") {
      detailReads += 1;
      return json(route, caseDetail(detailReads > 1 ? "approved" : null, detailReads > 1 ? 2 : 1), 200, { ETag: `"${detailReads > 1 ? 2 : 1}"` });
    }
    if (path === "/cases/case-1/actions" && request.method() === "POST") return json(route, { detail: "The case changed before this action was saved.", current: caseDetail("approved", 2) }, 409);
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await setPeriod(page);
  await page.goto("/reconciliation");
  const row = page.getByRole("row", { name: /INV-E2E-00001/ });
  await expect(row).toBeVisible();
  await row.click();
  const panel = page.getByRole("dialog", { name: "INV-E2E-00001" });
  await panel.getByRole("button", { name: "Approve" }).click();
  await page.getByLabel("Reason (required)").fill("Checked against the source documents");
  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByRole("alert")).toContainText("This case changed since you opened it");
  await expect(panel.locator(".case-panel-status")).toContainText("approved");
  expect(detailReads).toBe(2);
});

test("a protected case deep link survives login and opens the requested case", async ({ page }) => {
  let authenticated = false;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace("/api/v1", "");
    if (path === "/auth/me") {
      return authenticated
        ? json(route, manager)
        : json(route, { code: "unauthenticated", detail: "Sign in required" }, 401);
    }
    if (path === "/auth/login" && request.method() === "POST") {
      authenticated = true;
      return json(route, manager);
    }
    if (path === "/runs?limit=10") return json(route, []);
    if (path === "/cases") return json(route, { items: [caseSummary()], next_cursor: null, limit: 100, total_count: 1 });
    if (path === "/kpis") return json(route, { invoice_count: 1, matched_pct: 0, pending_count: 0, exception_count: 1, unlinked_count: 0, at_risk_total: "180.00", grni_total: "0.00" });
    if (path === "/cases/case-1") return json(route, caseDetail(), 200, { ETag: '"1"' });
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });

  await setPeriod(page);
  await page.goto("/reconciliation?status=exception&case_id=case-1");
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
  await page.getByLabel("Email address").fill("manager@example.invalid");
  await page.getByLabel("Password").fill("synthetic-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/reconciliation\?status=exception&case_id=case-1/);
  await expect(page.getByRole("dialog", { name: "INV-E2E-00001" })).toBeVisible();
});
