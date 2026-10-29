import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiScannedInvoices, AiSettingsView } from "../api/ai";
import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import { ScannedInvoicesSwitch } from "./ScannedInvoicesSwitch";

const getAiSettings = vi.fn();
const saveAiSettings = vi.fn();

vi.mock("../api/ai", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/ai")>();
  return {
    ...actual,
    getAiSettings: (...args: unknown[]) => getAiSettings(...args),
    saveAiSettings: (...args: unknown[]) => saveAiSettings(...args),
  };
});

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

function view(scanned: AiScannedInvoices, overrides: Partial<AiSettingsView> = {}): AiSettingsView {
  return { can_manage: true, scanned_invoices: scanned, ...overrides } as AiSettingsView;
}

function renderSwitch() {
  return render(<AuthContext.Provider value={authContext()}><ScannedInvoicesSwitch /></AuthContext.Provider>);
}

afterEach(() => {
  cleanup();
  getAiSettings.mockReset();
  saveAiSettings.mockReset();
});

describe("ScannedInvoicesSwitch (PD-2c)", () => {
  it("defaults to rules and keeps AI unavailable until a tested company server reads pages", async () => {
    getAiSettings.mockResolvedValue({ view: view({ reader: "rules", ai_available: false, ai_reading: false }), etag: '"3"' });
    renderSwitch();
    const rules = await screen.findByRole("radio", { name: /Read with rules/ });
    expect(rules).toBeChecked();
    expect(screen.getByRole("radio", { name: /Read with AI/ })).toBeDisabled();
    expect(screen.getByText(/always read by rules/)).toBeInTheDocument();
  });

  it("saves the switch with the current ETag when AI is available", async () => {
    getAiSettings.mockResolvedValue({ view: view({ reader: "rules", ai_available: true, ai_reading: false }), etag: '"3"' });
    saveAiSettings.mockResolvedValue({ view: view({ reader: "ai", ai_available: true, ai_reading: true }), etag: '"4"' });
    renderSwitch();
    fireEvent.click(await screen.findByRole("radio", { name: /Read with AI/ }));
    await waitFor(() => expect(saveAiSettings).toHaveBeenCalledWith({ scanned_invoices: "ai" }, '"3"', "csrf-ai"));
    expect(await screen.findByRole("status")).toHaveTextContent(/now read with AI/);
  });

  it("says when the switch is on AI but AI reading is not available now", async () => {
    getAiSettings.mockResolvedValue({ view: view({ reader: "ai", ai_available: false, ai_reading: false }), etag: '"3"' });
    renderSwitch();
    expect(await screen.findByRole("note")).toHaveTextContent(/read with rules/);
  });

  it("is read-only for a person without ai.manage", async () => {
    getAiSettings.mockResolvedValue({ view: view({ reader: "rules", ai_available: true, ai_reading: false }, { can_manage: false }), etag: '"3"' });
    renderSwitch();
    expect(await screen.findByRole("radio", { name: /Read with AI/ })).toBeDisabled();
    expect(screen.getByText(/Only managers/)).toBeInTheDocument();
  });
});
