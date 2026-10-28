import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AiValue, DocumentDetails } from "../api/ingestion";
import { AiValuesVerify } from "./AiValuesVerify";

const verifyAiValues = vi.fn();

vi.mock("../api/ingestion", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/ingestion")>();
  return { ...actual, verifyAiValues: (...args: unknown[]) => verifyAiValues(...args) };
});

afterEach(() => {
  cleanup();
  verifyAiValues.mockReset();
});

function value(overrides: Partial<AiValue>): AiValue {
  return {
    field_name: "doc_no",
    line_id: null,
    line_no: null,
    label: "doc_no",
    value_text: "INV-SYNTHETIC-1",
    ocr_value_text: "INV-SYNTHETIC-1",
    agrees: true,
    verified: false,
    page: 1,
    bbox: [1, 2, 3, 4],
    ...overrides,
  };
}

function details(values: AiValue[]): DocumentDetails {
  return { document: { id: "doc-1", version: 4, doc_type: "invoice" }, ai_values: values };
}

const labels = { doc_no: "Invoice number", rate: "Rate", quantity: "Quantity" };

describe("verify values read by AI (PD-4d)", () => {
  it("shows nothing for a document the rules read", () => {
    const { container } = render(<AiValuesVerify details={details([])} csrfToken="csrf" fieldLabels={labels} onFocusSource={vi.fn()} onSaved={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });

  it("lists each AI value beside its OCR reading and counts only agreeing ones for Verify all", () => {
    render(<AiValuesVerify details={details([
      value({}),
      value({ field_name: "rate", line_id: "line-1", line_no: 1, label: "line 1 rate", value_text: "109", ocr_value_text: "10", agrees: false }),
      value({ field_name: "quantity", line_id: "line-1", line_no: 1, label: "line 1 quantity", value_text: "2", ocr_value_text: null, agrees: false }),
    ])} csrfToken="csrf" fieldLabels={labels} onFocusSource={vi.fn()} onSaved={vi.fn()} />);

    const section = screen.getByRole("region", { name: "Values read by AI" });
    expect(within(section).getByText("Values read by AI · 3 of 3 to verify")).toBeTruthy();
    expect(within(section).getByRole("button", { name: "Verify all 1 that match the OCR reading" })).toBeTruthy();
    expect(within(section).getByText("2 differ from the OCR reading or have none: verify each one against the page.")).toBeTruthy();
    expect(within(section).getByText("10 · differs")).toBeTruthy();
    expect(within(section).getByText("No OCR reading")).toBeTruthy();
    expect(within(section).getAllByRole("button", { name: /^Verify (Invoice number|Line 1 · )/ })).toHaveLength(3);
  });

  it("disables Verify all when no value agrees with OCR", () => {
    render(<AiValuesVerify details={details([value({ agrees: false, ocr_value_text: null })])} csrfToken="csrf" fieldLabels={labels} onFocusSource={vi.fn()} onSaved={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Verify all 0 that match the OCR reading" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("sends one value with the loaded version and hands back the saved document", async () => {
    const saved = details([value({ verified: true })]);
    verifyAiValues.mockResolvedValue(saved);
    const onSaved = vi.fn();
    render(<AiValuesVerify details={details([value({ field_name: "rate", line_id: "line-1", line_no: 1, label: "line 1 rate" })])} csrfToken="csrf" fieldLabels={labels} onFocusSource={vi.fn()} onSaved={onSaved} />);

    fireEvent.click(screen.getByRole("button", { name: "Verify Line 1 · Rate" }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    expect(verifyAiValues).toHaveBeenCalledWith("doc-1", 4, { values: [{ field_name: "rate", line_id: "line-1" }] }, "csrf");
  });

  it("asks the server for every agreeing value, never a client-built list", async () => {
    verifyAiValues.mockResolvedValue(details([value({ verified: true })]));
    render(<AiValuesVerify details={details([value({})])} csrfToken="csrf" fieldLabels={labels} onFocusSource={vi.fn()} onSaved={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Verify all 1 that match the OCR reading" }));

    await waitFor(() => expect(verifyAiValues).toHaveBeenCalledWith("doc-1", 4, { all_agreeing: true }, "csrf"));
  });

  it("shows a verified value as verified with no button, and the server's refusal as an alert", async () => {
    verifyAiValues.mockRejectedValue(new Error("The resource changed after it was loaded. Reload the current state."));
    render(<AiValuesVerify details={details([
      value({ verified: true }),
      value({ field_name: "rate", line_id: "line-1", line_no: 1, label: "line 1 rate" }),
    ])} csrfToken="csrf" fieldLabels={labels} onFocusSource={vi.fn()} onSaved={vi.fn()} />);

    expect(screen.getByText("Values read by AI · 1 of 2 to verify")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Verify Invoice number" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Verify Line 1 · Rate" }));
    expect((await screen.findByRole("alert")).textContent).toBe("The resource changed after it was loaded. Reload the current state.");
  });

  it("jumps to the value's page", () => {
    const onFocusSource = vi.fn();
    render(<AiValuesVerify details={details([value({ page: 2 })])} csrfToken="csrf" fieldLabels={labels} onFocusSource={onFocusSource} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Page 2" }));
    expect(onFocusSource).toHaveBeenCalledWith({ page: 2, bbox: [1, 2, 3, 4], source: "ai", ai_call_id: null });
  });
});
