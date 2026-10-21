/* global URL */

import { expect, test } from "@playwright/test";

const manager = {
  name: "Synthetic Reviewer",
  role: "manager",
  csrf_token: "csrf-e2e",
  capabilities: ["files.upload", "data.view", "links.edit"],
};

const batch = { id: "batch-ac7", label: "Synthetic review batch", version: 1, period_start: null, period_end: null };
const documentId = "po-document-ac7";
const fileId = "po-file-ac7";

const file = {
  id: fileId,
  batch_id: batch.id,
  parent_file_id: null,
  display_name: "synthetic-po.csv",
  archive_path: null,
  zone: "po",
  zone_uploaded: "po",
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
  uploaded_at: "2026-10-08T10:00:00Z",
};

const splitFile = {
  ...file,
  id: "split-pdf-ac7",
  display_name: "synthetic-bundle.pdf",
  format: "pdf",
  mismatch_state: "suggested",
  status: "partial",
  docs_found: 0,
  sniffed_type: null,
};

function details({ reviewed = false, corrected = false } = {}) {
  return {
    document: {
      id: documentId,
      doc_type: "po",
      batch_id: batch.id,
      version: reviewed ? 2 : 1,
      doc_no: corrected ? "PO-VERIFY-2" : "PO-VERIFY-1",
      vendor_id: null,
      needs_verification: !reviewed,
    },
    version: {
      id: reviewed ? "po-version-2" : "po-version-1",
      doc_no: corrected ? "PO-VERIFY-2" : "PO-VERIFY-1",
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
      revision: reviewed ? "A" : "A",
      po_status: "open",
      ship_to: "Synthetic warehouse",
      payment_terms_text: "30 days",
      delivery_date: "2026-10-31",
      needs_verification: !reviewed,
      extraction_method: "pdf_text",
      extras: { review_fields: ["doc_no", "revision"] },
    },
    lines: [],
    field_evidence: reviewed && corrected
      ? [{ id: "manual-evidence-ac7", field_name: "doc_no", source: "manual", value_text: "PO-VERIFY-2", doc_line_id: null }]
      : [{ id: "pdf-evidence-ac7", field_name: "doc_no", source: "pdf_text", value_text: "PO-VERIFY-1", page: 1, bbox: [10, 10, 90, 22], doc_line_id: null }],
    processing_log: [],
  };
}

async function json(route, body, status = 200, headers = {}) {
  await route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });
}

test("verify preview offers typed PO correction, conflict choices, vendor assignment, and a local truth patch", async ({ page }) => {
  let correctionBody;
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const method = request.method();
    if (path === "/auth/me") return json(route, manager);
    if (path === "/config") return json(route, { body: { app: { ui: { poll_seconds: 2 }, limits: { max_file_mb: 25 } } } });
    if (path === "/batches" && method === "GET") return json(route, [batch]);
    if (path === "/files" && method === "GET") return json(route, [file, splitFile], 200, { ETag: '"files-1"' });
    if (path === "/documents" && method === "GET") return json(route, [{ id: documentId }]);
    if (path === `/documents/${documentId}` && method === "GET") return json(route, details());
    if (path === `/documents/${documentId}/ai-suggestions` && method === "GET") {
      return json(route, {
        document_id: documentId,
        document_version: 1,
        state: "available",
        code: null,
        ai_call_id: "00000000-0000-4000-8000-000000000007",
        mode: "vision_review",
        label: "Vision suggestions",
        model: "synthetic-test-reader",
        source: "vision",
        correctable: true,
        annotations: [],
        suggestions: [{
          scope: "header",
          line_no: null,
          field: "revision",
          value: "B",
          current_value: "A",
          matches_current: false,
          page: 1,
          quote: "Revision B",
          box: [100, 120, 160, 280],
        }],
      });
    }
    if (path === `/files/${fileId}/errors` && method === "GET") return route.fulfill({ status: 200, contentType: "application/x-ndjson", body: "" });
    if (path === `/files/${fileId}/content` && method === "GET") return route.fulfill({ status: 200, contentType: "text/plain", body: "Synthetic purchase order source" });
    if (path === `/documents/${documentId}/correction` && method === "PUT") {
      correctionBody = request.postDataJSON();
      return json(route, details({ reviewed: true, corrected: true }));
    }
    return json(route, { detail: `Unexpected synthetic request: ${method} ${path}` }, 500);
  });

  await page.goto("/ingestion");
  const fileRow = page.getByRole("row").filter({ hasText: "synthetic-po.csv" });
  await expect(fileRow.getByRole("button", { name: "Preview" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Split into documents" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Keep as one PO" })).toBeVisible();
  await fileRow.getByRole("button", { name: "Preview" }).click();

  const dialog = page.getByRole("dialog", { name: /synthetic-po\.csv/i });
  await expect(dialog.getByText("Fields to verify")).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Source · page 1" })).toBeVisible();
  await expect(dialog.getByRole("link", { name: "Assign vendor" })).toHaveAttribute("href", `/mapping?document_id=${documentId}`);
  await expect(dialog.getByRole("button", { name: "Use suggested Revision" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Keep current for Revision" })).toBeVisible();

  await dialog.getByRole("button", { name: "Use suggested Revision" }).click();
  await expect(dialog.getByRole("textbox", { name: "Revision" })).toHaveValue("B");
  await dialog.getByRole("button", { name: "Keep current for Revision" }).click();
  await expect(dialog.getByRole("textbox", { name: "Revision" })).toHaveValue("A");
  await dialog.getByRole("textbox", { name: "Document number" }).fill("PO-VERIFY-2");
  await dialog.getByLabel(/I reviewed the source PDF.*PO/).check();
  await dialog.getByRole("button", { name: "Save and mark reviewed" }).click();

  await expect(dialog.getByText(/saved and marked reviewed/)).toBeVisible();
  expect(correctionBody.doc_no).toBe("PO-VERIFY-2");
  expect(correctionBody.revision).toBe("A");
  expect(correctionBody.verified).toBe(true);
  expect(correctionBody.ai_suggestion_refs).toBeUndefined();

  const downloadPromise = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Download truth correction patch (1)" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe(`accuracy-corrections-${fileId}.json`);
});
