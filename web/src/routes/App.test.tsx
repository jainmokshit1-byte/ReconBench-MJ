import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { App } from "./App";

describe("static route shell", () => {
  afterEach(() => {
    window.history.replaceState({}, "", "/");
  });

  it("renders the Home shell and all primary destinations", () => {
    window.history.replaceState({}, "", "/home");
    render(<App />);

    expect(screen.getByRole("heading", { name: "Good morning" })).toBeInTheDocument();
    const navigation = screen.getByRole("navigation", { name: "Main navigation" });
    expect(navigation).toHaveTextContent("Ingestion");
    expect(within(navigation).getByRole("link", { name: "Mapping" })).toHaveAttribute("href", "/mapping");
    expect(within(navigation).getByRole("link", { name: "Reconciliation" })).toHaveAttribute("href", "/reconciliation");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });
});
