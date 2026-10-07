/* global window, document, URL */

import { expect, test } from "@playwright/test";

// Synthetic, never a real credential: it only has to pass the server's key shape check.
const SENTINEL_KEY = "AIzaSyD-SENTINEL-e2e-card-key-000000wxyz";
const FINGERPRINT = "a".repeat(64);
const MODEL = "gemini-3.8-flash";

const capabilities = ["files.upload", "links.edit", "recon.run", "cases.decide", "settings.change", "export.create", "data.view"];
const manager = { name: "Synthetic Manager", role: "manager", csrf_token: "csrf-e2e", capabilities: [...capabilities, "ai.manage"] };
const viewer = { name: "Synthetic Viewer", role: "viewer", csrf_token: "csrf-e2e", capabilities: ["data.view"] };

const hooks = [
  { hook: "h1_extract:fallback", label: "PDF extraction suggestions (unclear fields only)", data_sent: "Text of up to 8 pages of a PDF whose critical fields could not be read." },
  { hook: "h2_item", label: "Item match opinion", data_sent: "Descriptions, item codes, HSN/SAC and units. No quantities, rates or amounts." },
];

function offView() {
  return {
    version: 1,
    mode: "rules_only",
    mode_label: "Off: rules only",
    provider: null,
    model: null,
    key: { set: false, hint: null, storage: null, expires_at: null },
    consent: { given: false, current: false, at: null, text_version: "consent.v1", required_fingerprint: null, text: "", hooks: [] },
    auto_accept_ai_links: false,
    redaction_enabled: true,
    external_calls: "synthetic_only",
    limits: { per_day_calls: 200, per_day_tokens: 1000000 },
    budget: { usd_cap_offered: false, budget_cap_usd: null, token_cap_guaranteed: false, priced: false },
    key_storage_available: true,
    last_test: { at: null, ok: null },
    status: { code: "OFF", label: "Off: rules only" },
  };
}

/** A small in-memory AI settings server that, like the real one, never echoes the key. */
function aiServer() {
  const state = { view: offView(), uncertain: [{ id: "req-uncertain", call_kind: "hook", hook: "h2_item", finished_at: "2026-10-05T09:00:00Z" }], puts: [], acknowledged: [] };
  const bump = (patch) => { state.view = { ...state.view, ...patch, version: state.view.version + 1 }; };
  const etag = () => ({ ETag: `"${state.view.version}"` });
  return {
    state,
    async handle(route, request, path, json) {
      const method = request.method();
      if (path === "/ai/settings" && method === "GET") return json(route, state.view, 200, etag());
      if (path.startsWith("/ai/consent-preview") && method === "GET") {
        const query = new URL(request.url()).searchParams;
        return json(route, { mode: query.get("mode"), mode_label: "AI Assist: suggestions where the rules are unsure", provider: "gemini", model: query.get("model"), text_version: "consent.v1", text: `When AI Assist is on, ReconBench sends the data listed for each enabled hook to Google Gemini (${query.get("model")}).`, fingerprint: FINGERPRINT, hooks });
      }
      if (path === "/ai/settings" && method === "PUT") {
        if (request.headers()["if-match"] !== `"${state.view.version}"`) return json(route, { detail: "AI settings were changed by someone else.", code: "version_conflict" }, 412);
        const body = request.postDataJSON();
        state.puts.push(body);
        if (body.mode !== "rules_only" && body.consent?.fingerprint !== FINGERPRINT) return json(route, { detail: "Consent required", code: "consent_required" }, 422);
        const key = body.api_key ? { set: true, hint: `****${body.api_key.slice(-4)}`, storage: body.key_storage, expires_at: body.key_storage === "session" ? "2026-10-05T20:00:00Z" : null } : state.view.key;
        bump({
          mode: body.mode,
          mode_label: "AI Assist: suggestions where the rules are unsure",
          provider: body.provider,
          model: body.model,
          key,
          consent: { ...state.view.consent, given: true, current: true, at: "2026-10-05T10:00:00Z", required_fingerprint: FINGERPRINT, hooks },
          status: { code: "NOT_TESTED", label: "Not tested since last change" },
        });
        return json(route, state.view, 200, etag());
      }
      if (path === "/ai/key" && method === "DELETE") {
        bump({ key: { set: false, hint: null, storage: null, expires_at: null }, status: { code: "KEY_REMOVED", label: "Key removed by Synthetic Manager" } });
        return json(route, state.view, 200, etag());
      }
      if (path === "/ai/test/provider" && method === "POST") return json(route, { request_id: "req-test", state: "queued" }, 202);
      if (path === "/ai/requests/req-test" && method === "GET") {
        state.view = { ...state.view, status: { code: "TEST_PASSED", label: "Last provider test passed", at: "2026-10-05T10:01:00Z" } };
        return json(route, { id: "req-test", call_kind: "provider_test", hook: null, request_state: "succeeded", apply_outcome: "applied", skip_code: null, error_code: null });
      }
      if (path === "/ai/usage" && method === "GET") return json(route, { calls_today: 3, tokens_today: 1200, calls_with_estimated_usage: 1, usd_today: null, usd_available: false, usd_notice: "Cost is not shown: no price is configured for this model.", token_notice: "Token counts for calls without reported usage are estimates (no local tokenizer).", uncertain_requests: state.uncertain });
      if (path === "/ai/requests/req-uncertain/acknowledge" && method === "POST") {
        state.acknowledged.push("req-uncertain");
        state.uncertain = [];
        return json(route, { request_id: "req-uncertain", acknowledged_at: "2026-10-05T10:02:00Z" });
      }
      return null;
    },
  };
}

async function json(route, body, status = 200, headers = {}) {
  await route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });
}

async function mockApi(page, user, server) {
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.replace("/api/v1", "") + new URL(request.url()).search;
    if (path === "/auth/me") return json(route, user);
    if (path === "/runs?limit=10") return json(route, []);
    if (path.startsWith("/kpis")) return json(route, { invoice_count: 1, matched_pct: 0, pending_count: 0, exception_count: 1, unlinked_count: 0, at_risk_total: "0.00", grni_total: "0.00" });
    if (path.startsWith("/workflow-status")) return json(route, { steps: [{ key: "ingest", complete: true }, { key: "map", complete: true }, { key: "reconcile", complete: false }, { key: "export", complete: false }], next_step: "reconcile", run_id: null, active_run_id: null });
    const handled = await server.handle(route, request, path, json);
    if (handled !== null) return handled;
    return json(route, { detail: `Unexpected API request ${request.method()} ${path}` }, 500);
  });
}

async function setPeriod(page) {
  await page.addInitScript(() => window.localStorage.setItem("reconbench-period", "2026-09"));
}

test("AI card saves a key with consent, tests the provider, acknowledges an uncertain call and removes the key without the key ever coming back", async ({ page, context }) => {
  const server = aiServer();
  const sentWithKey = [];
  const leakedUrls = [];
  const responseBodies = [];
  page.on("request", (request) => {
    if (request.url().includes(SENTINEL_KEY)) leakedUrls.push(request.url());
    if ((request.postData() ?? "").includes(SENTINEL_KEY)) sentWithKey.push(`${request.method()} ${new URL(request.url()).pathname}`);
  });
  page.on("response", async (response) => {
    if (!response.url().includes("/api/v1/")) return;
    responseBodies.push(await response.text().catch(() => ""));
  });
  await mockApi(page, manager, server);
  await setPeriod(page);
  await page.goto("/home");

  const card = page.getByRole("region", { name: "AI Assist", exact: true });
  await expect(card.getByRole("status", { name: "AI Assist status" })).toHaveText(/Off: rules only/);
  await card.getByRole("button", { name: /Manage AI Assist/ }).click();
  const dialog = page.getByRole("dialog", { name: "AI Assist settings" });
  await expect(dialog.getByText(/synthetic test fixtures/)).toBeVisible();
  await expect(dialog.getByLabel("Provider", { exact: true })).toBeDisabled();
  await expect(dialog.getByText(/No USD spending cap is offered/)).toBeVisible();
  await expect(dialog.getByText("Cost is not shown: no price is configured for this model.", { exact: false })).toBeVisible();

  await dialog.getByRole("radio", { name: /^AI Assist/ }).check();
  await dialog.getByLabel("Model", { exact: true }).fill(SENTINEL_KEY);
  await expect(dialog.getByText(/Do not paste the key here/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
  await dialog.getByLabel("Model", { exact: true }).fill(MODEL);
  const keyInput = dialog.getByLabel("Gemini API key");
  await expect(keyInput).toHaveAttribute("type", "password");
  await expect(keyInput).toHaveAttribute("autocomplete", "off");
  await keyInput.fill(SENTINEL_KEY);
  await expect(dialog.getByText(/stops working when this session ends or reaches its time limit/)).toBeVisible();
  await dialog.getByRole("radio", { name: /This sign-in session only/ }).check();
  const hookList = dialog.getByRole("list", { name: "Data sent per hook" });
  await expect(hookList.getByText("Item match opinion")).toBeVisible();
  await expect(hookList.getByText(/No quantities, rates or amounts/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Save AI settings" })).toBeDisabled();
  await dialog.getByLabel(/I consent to sending the data listed above/).check();
  await dialog.getByRole("button", { name: "Save AI settings" }).click();

  await expect(dialog.getByText(/AI settings saved as version 2/)).toBeVisible();
  await expect(keyInput).toHaveValue("");
  await expect(dialog.getByText(/Key \*\*\*\*wxyz · this session only, expires/)).toBeVisible();
  expect(server.state.puts).toHaveLength(1);
  expect(server.state.puts[0]).toMatchObject({ mode: "ai_assisted", provider: "gemini", model: MODEL, key_storage: "session", consent: { accept: true, fingerprint: FINGERPRINT } });

  await dialog.getByRole("button", { name: "Test provider" }).click();
  await expect(dialog.getByRole("status", { name: "Provider test result" })).toHaveText(/provider test call finished/);
  await expect(dialog.getByRole("status", { name: "AI Assist status detail" })).toHaveText(/Last provider test passed/);

  await expect(dialog.getByText(/may have reached Gemini and been billed/)).toBeVisible();
  await dialog.getByRole("button", { name: "Acknowledge" }).click();
  await expect(dialog.getByText(/One new attempt is allowed/)).toBeVisible();
  expect(server.state.acknowledged).toEqual(["req-uncertain"]);

  await dialog.getByRole("button", { name: "Remove key" }).click();
  await dialog.getByRole("button", { name: "Confirm remove key" }).click();
  await expect(dialog.getByRole("status", { name: "AI Assist status detail" })).toHaveText(/Key removed by Synthetic Manager/);
  await expect(dialog.getByRole("button", { name: "Remove key" })).toHaveCount(0);
  await dialog.getByRole("button", { name: "Close AI Assist settings" }).click();
  await expect(card.getByRole("status", { name: "AI Assist status" })).toHaveText(/Key removed/);

  // The key went out exactly once, in the save body, and never came back or persisted.
  expect(sentWithKey).toEqual(["PUT /api/v1/ai/settings"]);
  expect(leakedUrls).toEqual([]);
  expect(responseBodies.length).toBeGreaterThan(5);
  for (const body of responseBodies) expect(body).not.toContain(SENTINEL_KEY);
  const stored = await page.evaluate(() => JSON.stringify({ local: { ...window.localStorage }, session: { ...window.sessionStorage }, cookie: document.cookie }));
  expect(stored).not.toContain(SENTINEL_KEY);
  expect(JSON.stringify(await context.cookies())).not.toContain(SENTINEL_KEY);
  expect(JSON.stringify(await context.storageState())).not.toContain(SENTINEL_KEY);
  expect(await page.content()).not.toContain(SENTINEL_KEY);
});

test("a viewer sees the AI status on Home but cannot open AI settings", async ({ page }) => {
  const server = aiServer();
  await mockApi(page, viewer, server);
  await setPeriod(page);
  await page.goto("/home");
  const card = page.getByRole("region", { name: "AI Assist", exact: true });
  await expect(card.getByRole("status", { name: "AI Assist status" })).toHaveText(/Off: rules only/);
  await expect(card.getByText("Only managers can change AI Assist.")).toBeVisible();
  await expect(card.getByRole("button", { name: /Manage AI Assist/ })).toHaveCount(0);
});
