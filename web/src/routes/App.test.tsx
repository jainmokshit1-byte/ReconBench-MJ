import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { Settings } from "lucide-react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "../api/client";
import type { AuthSession } from "../auth/session";
import { CapabilityNavLink } from "../components/CapabilityNavLink";
import { App } from "./App";

const managerSession: AuthSession = {
  name: "Synthetic Manager",
  role: "manager",
  csrf_token: "csrf-synthetic",
  capabilities: [
    "files.upload",
    "links.edit",
    "recon.run",
    "cases.decide",
    "cases.approve_exception",
    "settings.change",
    "ai.manage",
    "users.manage",
    "export.create",
    "data.view",
  ],
};

function jsonResponse(status: number, body: unknown, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...Object.fromEntries(new Headers(headers)) },
  });
}

function installFetch(handler: (input: RequestInfo | URL, init?: RequestInit) => Response) {
  const request = vi.fn((input: RequestInfo | URL, init?: RequestInit) =>
    handler(input instanceof Request ? input.url : input, init),
  );
  vi.stubGlobal("fetch", request);
  return request;
}

function problem(status: number, code: string): Response {
  return jsonResponse(status, {
    type: "about:blank",
    title: status === 401 ? "Authentication failed" : "Too many login attempts",
    status,
    detail: "Generic response detail.",
    instance: "/api/v1/auth/login",
    code,
  }, status === 429 ? { "Retry-After": "900" } : undefined);
}

describe("authenticated web shell and login flow", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it("shows generic credential errors and returns to a preserved deep link after login", async () => {
    let loginCount = 0;
    const request = installFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return problem(401, "unauthenticated");
      if (url.endsWith("/auth/login")) {
        loginCount += 1;
        return loginCount === 1 ? problem(401, "invalid_credentials") : jsonResponse(200, managerSession);
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    window.history.replaceState({}, "", "/reconciliation?status=needs_review#case-1");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Sign in to your workspace" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "ReconBench Home" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Email address")).toHaveAttribute("autoComplete", "username");
    expect(screen.getByLabelText("Password")).toHaveAttribute("autoComplete", "current-password");

    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "manager@example.invalid" },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "wrong-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
    expect(screen.getByRole("alert")).not.toHaveTextContent("registered");

    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Reconciliation" })).toBeInTheDocument();
    await waitFor(() => {
      expect(window.location.pathname + window.location.search + window.location.hash).toBe(
        "/reconciliation?status=needs_review#case-1",
      );
    });
    expect(screen.getByLabelText("Synthetic Manager, manager")).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({ credentials: "include", method: "POST" }),
    );
  });

  it("shows only the generic lockout message and a coarse retry delay", async () => {
    const request = installFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return problem(401, "unauthenticated");
      if (url.endsWith("/auth/login")) return problem(429, "login_rate_limited");
      throw new Error(`Unexpected request: ${url}`);
    });
    window.history.replaceState({}, "", "/login");
    render(<App />);
    await screen.findByRole("heading", { name: "Sign in to your workspace" });
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "manager@example.invalid" },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many sign-in attempts. Try again in 15 minutes.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("email");
    expect(request).toHaveBeenCalled();
  });

  it("shows expiry messaging, signs out with CSRF, and blocks back navigation to protected content", async () => {
    const request = installFetch((input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) return jsonResponse(200, managerSession);
      if (url.endsWith("/auth/login")) return jsonResponse(200, managerSession);
      if (url.endsWith("/auth/logout")) {
        expect(init?.headers).toEqual({ "X-CSRF-Token": "csrf-synthetic" });
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/readyz")) return problem(401, "unauthenticated");
      throw new Error(`Unexpected request: ${url}`);
    });
    window.history.replaceState({}, "", "/home");
    render(<App />);
    expect(await screen.findByRole("heading", { name: "ReconBench Home" })).toBeInTheDocument();
    expect(request).toHaveBeenCalledWith("/api/v1/auth/me", { credentials: "include" });

    await apiClient.GET("/readyz");
    expect(await screen.findByRole("heading", { name: "Sign in to your workspace" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session expired or ended.");
    expect(screen.getByRole("status")).toHaveTextContent("continue on the server");
    expect(screen.queryByRole("heading", { name: "ReconBench Home" })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "manager@example.invalid" },
    });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "ReconBench Home" })).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText("Synthetic Manager, manager"));
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in to your workspace" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "ReconBench Home" })).not.toBeInTheDocument();

    window.history.pushState({}, "", "/home");
    window.dispatchEvent(new PopStateEvent("popstate"));
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect(screen.queryByRole("heading", { name: "ReconBench Home" })).not.toBeInTheDocument();
    expect(request.mock.calls.some(([url]) => String(url).endsWith("/auth/logout"))).toBe(true);
  });

  it("keeps P0 manager controls enabled and explains controls unavailable to a restricted role", async () => {
    installFetch((input) => {
      if (String(input).endsWith("/auth/me")) return jsonResponse(200, managerSession);
      throw new Error(`Unexpected request: ${String(input)}`);
    });
    window.history.replaceState({}, "", "/home");
    render(<App />);
    await screen.findByRole("heading", { name: "ReconBench Home" });
    expect(screen.getByRole("link", { name: "Settings" })).toBeEnabled();

    render(
      <CapabilityNavLink
        to="/settings"
        label="Settings"
        capability="settings.change"
        capabilities={[]}
        icon={Settings}
      />,
    );
    const restricted = screen.getByRole("button", { name: "Settings" });
    expect(restricted).toBeDisabled();
    expect(within(restricted.parentElement as HTMLElement).getByText("Manager permission required")).toBeInTheDocument();
  });
});
