import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiApiError } from "../api/ai";
import type { AiCapabilities, AiProviderCard, AiSettingsView } from "../api/ai";
import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { AiProviderSection } from "./AiProviderSection";
import { AiProvidersPanel } from "./AiProvidersPanel";

const getAiSettings = vi.fn();
const getAiConnectNotice = vi.fn();
const connectAiProvider = vi.fn();
const startAiProviderTests = vi.fn();
const getAiRequestState = vi.fn();
const saveAiSettings = vi.fn();
const getAiConsentPreview = vi.fn();
const mocks = [getAiSettings, getAiConnectNotice, connectAiProvider, startAiProviderTests, getAiRequestState, saveAiSettings, getAiConsentPreview];

vi.mock("../api/ai", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/ai")>();
  return {
    ...actual,
    getAiSettings: (...args: unknown[]) => getAiSettings(...args),
    getAiConnectNotice: (...args: unknown[]) => getAiConnectNotice(...args),
    connectAiProvider: (...args: unknown[]) => connectAiProvider(...args),
    startAiProviderTests: (...args: unknown[]) => startAiProviderTests(...args),
    getAiRequestState: (...args: unknown[]) => getAiRequestState(...args),
    saveAiSettings: (...args: unknown[]) => saveAiSettings(...args),
    getAiConsentPreview: (...args: unknown[]) => getAiConsentPreview(...args),
  };
});

const SERVER = "http://vllm.example.test/vllm/v1";
const SECRET = "SYNTHETIC-SECRET-KEY-0001";
const NOT_CONNECTED = { state: "not_connected", reason_code: null, checked_at: null, connected_at: null };

function authContext(): AuthContextValue {
  const capabilities = ["data.view", "ai.manage"];
  return {
    status: "authenticated",
    user: { name: "Synthetic", role: "manager", csrf_token: "csrf-ai", capabilities },
    expired: false,
    signIn: vi.fn(async () => undefined),
    signOut: vi.fn(async () => undefined),
    hasCapability: (capability) => capabilities.includes(capability),
  };
}

function card(provider: "gemini" | "vllm", overrides: Partial<AiProviderCard> = {}): AiProviderCard {
  return {
    provider,
    enabled: true,
    model: provider === "gemini" ? "gemini-3.5-flash-lite" : "qwen3.5:9b",
    base_url: null,
    external: provider === "gemini",
    data_destination: provider === "gemini" ? "Google Gemini API: masked page images leave this server" : "Company server (over the company VPN): masked page images leave this machine but not the company network",
    key: { set: false, hint: null },
    capabilities: provider === "vllm" ? { vision: null, json_schema: null, context_window: null, can_read_pages: false } : null,
    connection: { state: "connected", reason_code: null, checked_at: null, connected_at: null },
    ...overrides,
  };
}

function view(overrides: Partial<AiSettingsView> = {}, vllm: Partial<AiProviderCard> = {}, gemini: Partial<AiProviderCard> = {}): AiSettingsView {
  return {
    can_manage: true,
    vllm_endpoints_allowed: [SERVER],
    reader_strategy: "off",
    primary_reader: null,
    vllm_calls: "enabled",
    providers: [card("gemini", gemini), card("vllm", { base_url: SERVER, ...vllm })],
    version: 3,
    mode: "ai_assisted",
    mode_label: "AI Assist",
    provider: "gemini",
    model: "gemini-3.5-flash-lite",
    key: { set: true, hint: "9z", storage: "saved", expires_at: null },
    consent: { given: true, current: true, at: null, required_fingerprint: "fp", hooks: [] },
    external_calls: "enabled",
    limits: null,
    budget: { usd_cap_offered: false, token_cap_guaranteed: false, priced: false },
    key_storage_available: true,
    vision: { enabled: false, available: true },
    auto_verify: { field_types: [], available_field_types: [] },
    status: { code: "READY", label: "Ready", at: null },
    ...overrides,
  };
}

function serve(next: AiSettingsView) {
  getAiSettings.mockResolvedValue({ view: next, etag: `"${next.version}"` });
}

function renderIn(element: ReactElement) {
  return render(<AuthContext.Provider value={authContext()}>{element}</AuthContext.Provider>);
}

afterEach(() => {
  cleanup();
  for (const mock of mocks) mock.mockReset();
});

describe("shared AI provider component (PD-1c)", () => {
  it("shows the same view model on Home and on Settings", async () => {
    serve(view({}, { capabilities: { vision: true, json_schema: false, context_window: 8192, can_read_pages: false } }));
    const home = renderIn(<AiProviderSection />);
    await screen.findByText("Reads page images: yes");
    const homeText = home.container.textContent;
    cleanup();
    const settings = renderIn(<AiProvidersPanel />);
    await screen.findByText("Reads page images: yes");
    expect(settings.container.textContent).toBe(homeText);
  });

  it("renders capabilities for yes, no and not checked, in plain words", async () => {
    serve(view({}, { capabilities: { vision: true, json_schema: false, context_window: 8192, can_read_pages: false } }));
    renderIn(<AiProviderSection />);
    expect(await screen.findByText("Reads page images: yes")).toBeTruthy();
    expect(screen.getByText("Structured answers: no")).toBeTruthy();
    expect(screen.getByText("Context window: 8,192 tokens")).toBeTruthy();
    cleanup();
    const unchecked: AiCapabilities = { vision: null, json_schema: null, context_window: null, can_read_pages: false };
    serve(view({}, { capabilities: unchecked }));
    renderIn(<AiProviderSection />);
    expect(await screen.findByText("Reads page images: not checked")).toBeTruthy();
    expect(screen.getByText("Structured answers: not checked")).toBeTruthy();
    expect(screen.getByText("Context window: not checked")).toBeTruthy();
  });

  it("says Connected only when the server test passed; a failed image test leaves it Connected", async () => {
    serve(view({}, { connection: NOT_CONNECTED }));
    renderIn(<AiProviderSection />);
    const company = await screen.findByRole("article", { name: /Company server \(Qwen\) provider/ });
    expect(within(company).getByText("Not connected")).toBeTruthy();
    cleanup();
    serve(view({}, { capabilities: { vision: false, json_schema: true, context_window: 8192, can_read_pages: false } }));
    renderIn(<AiProviderSection />);
    const connected = await screen.findByRole("article", { name: /Company server \(Qwen\) provider/ });
    expect(within(connected).getByText("Connected")).toBeTruthy();
    expect(within(connected).getByText("Reads page images: no")).toBeTruthy();
  });

  it("keeps the key field write-only", async () => {
    serve(view({}, {}, { key: { set: true, hint: "9z" } }));
    connectAiProvider.mockResolvedValue({ request_id: "r1", image_request_id: null, view: view() });
    getAiConnectNotice.mockResolvedValue({ provider: "gemini", model: "gemini-3.5-flash-lite", text: "Notice text", data_destination: "x", data_use_warning: false, fingerprint: "f".repeat(64) });
    getAiRequestState.mockResolvedValue({ id: "r1", request_state: "succeeded", skip_code: null, error_code: null });
    renderIn(<AiProviderSection />);
    const gemini = await screen.findByRole("article", { name: /Google Gemini provider/ });
    expect(within(gemini).getByText(/Key set 9z/)).toBeTruthy();
    fireEvent.click(within(gemini).getByRole("button", { name: "Change connection" }));
    const form = within(gemini).getByRole("form", { name: /Connect Google Gemini/ });
    const field = form.querySelector('input[type="password"]') as HTMLInputElement;
    expect(field.value).toBe("");
    fireEvent.change(within(form).getByLabelText("Key tier"), { target: { value: "paid" } });
    fireEvent.change(field, { target: { value: SECRET } });
    expect(field.value).toBe(SECRET);
    await within(form).findByText("Notice text");
    expect(document.body.textContent).not.toContain(SECRET);
    fireEvent.click(within(form).getByLabelText(/I accept this notice/));
    fireEvent.click(within(form).getByRole("button", { name: "Connect and test" }));
    await waitFor(() => expect(connectAiProvider).toHaveBeenCalledTimes(1));
    const sent = connectAiProvider.mock.calls[0] as [string, { api_key?: string; consent: { fingerprint: string } }, string];
    expect(sent[1].api_key).toBe(SECRET);
    expect(sent[1].consent.fingerprint).toBe("f".repeat(64));
    // Gone from state on submit: reopening shows an empty field and the key is nowhere on the page.
    await waitFor(() => expect(within(gemini).queryByRole("form")).toBeNull());
    expect(document.body.textContent).not.toContain(SECRET);
    const reopen = within(gemini).getByRole("button", { name: "Change connection" }) as HTMLButtonElement;
    await waitFor(() => expect(reopen.disabled).toBe(false), { timeout: 4000 });
    fireEvent.click(reopen);
    expect((within(gemini).getByRole("form").querySelector('input[type="password"]') as HTMLInputElement).value).toBe("");
  });

  it("offers only allowlisted company server addresses", async () => {
    serve(view({}, { base_url: null, model: null, connection: NOT_CONNECTED }));
    renderIn(<AiProviderSection />);
    const company = await screen.findByRole("article", { name: /Company server \(Qwen\) provider/ });
    fireEvent.click(within(company).getByRole("button", { name: "Connect" }));
    const options = within(company).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual([SERVER]);
  });

  it("is read-only for a person who cannot manage AI and shows 'Company server', not the address", async () => {
    serve(view({ can_manage: false, vllm_endpoints_allowed: [] }, { base_url: null }));
    renderIn(<AiProviderSection />);
    const company = await screen.findByRole("article", { name: /Company server \(Qwen\) provider/ });
    expect(within(company).getByText("Company server")).toBeTruthy();
    expect(screen.getByText("Only managers can change AI providers.")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(document.querySelector("input:enabled, select:enabled")).toBeNull();
    expect(document.querySelector('input[type="password"]')).toBeNull();
    expect(document.body.textContent).not.toContain("vllm.example.test");
    expect(getAiConsentPreview).not.toHaveBeenCalled();
  });

  it("shows the server's message when the company server cannot be the reader yet", async () => {
    const message = "The company server has not passed the image and structured-answer checks. Turn reading off, save, Test, then turn it on.";
    serve(view({}, { capabilities: { vision: true, json_schema: true, context_window: 8192, can_read_pages: true } }));
    getAiConsentPreview.mockResolvedValue({ text: "Pages go to the company server.", fingerprint: "a".repeat(64) });
    saveAiSettings.mockRejectedValue(new AiApiError(message, 422, "provider_capability_missing"));
    renderIn(<AiProviderSection />);
    await screen.findByText("Reads page images: yes");
    fireEvent.click(screen.getByRole("radio", { name: /Company server \(Qwen\)/ }));
    await screen.findByText("Pages go to the company server.");
    fireEvent.click(screen.getByLabelText(/I agree to send page images/));
    fireEvent.click(screen.getByRole("button", { name: /Use Company server \(Qwen\) for page reading/ }));
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(message);
  });

  it("blocks choosing the company server as reader until both checks passed", async () => {
    serve(view({}, { capabilities: { vision: true, json_schema: null, context_window: 8192, can_read_pages: false } }));
    renderIn(<AiProviderSection />);
    await screen.findByText("Reads page images: yes");
    const radio = screen.getByRole("radio", { name: /Company server \(Qwen\)/ }) as HTMLInputElement;
    expect(radio.disabled).toBe(true);
  });
});
