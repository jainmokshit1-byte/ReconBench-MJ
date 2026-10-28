import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CaseSummary } from "../api/reconciliation";
import { checkRows, REASON_TEXT } from "../utils/reconChecks";
import { CaseTableRow, CheckTable } from "./ReconciliationPage";

// The engine's ReasonCode values (reconbench_core/schema ReasonCode), checked in order.
const ENGINE_REASON_CODES = [
  "DUP_INVOICE", "NO_PO_LINK", "LINK_INFERRED", "LOW_CONFIDENCE_DATA", "DUP_SUSPECTED",
  "ITEM_UNSURE", "NO_TEXT_LAYER", "VENDOR_MISMATCH", "PO_NOT_OPEN", "ITEM_NOT_ON_PO",
  "QTY_OVER_PO", "QTY_OVER_GRN", "RATE_HIGH", "TOTAL_MISMATCH", "MATH_ERROR",
  "PO_VALUE_EXCEEDED", "CURRENCY_DIFF", "UOM_MISMATCH", "DATE_ORDER", "GRN_PENDING",
  "RATE_LOW", "TAX_RATE_DIFF", "TAX_TYPE_MISMATCH", "HSN_DIFF", "GSTIN_INVALID",
  "EXTRA_CHARGE", "GRN_OVER_PO", "LATE_DELIVERY", "AI_VALUE_UNVERIFIED",
];

afterEach(cleanup);

// Exact decimal difference for the test only (the UI never does money arithmetic).
function scaled(value: string): bigint {
  const [whole = "0", fraction = ""] = value.replace("₹", "").trim().split(".");
  const negative = whole.startsWith("-");
  const digits = BigInt(`${whole.replace("-", "")}${fraction.padEnd(4, "0").slice(0, 4)}`);
  return negative ? -digits : digits;
}

const storedChecks: Array<Record<string, unknown>> = [
  {
    check_id: "s5.total_vs_expected",
    check_version: 1,
    code: "TOTAL_MISMATCH",
    result: "review",
    actual: "1250.00",
    expected: "1200.00",
    variance: "50.00",
    variance_pct: "4.17",
    tolerance: { pct: "1", abs: "10", mode: "or", source: "global" },
    evidence: {},
  },
  {
    check_id: "s5.unit_rate",
    check_version: 1,
    result: "pass",
    actual: "99.5000",
    expected: "100.0000",
    variance: "-0.5000",
    variance_pct: "-0.50",
    tolerance: { pct: "1", abs: "1", mode: "or", source: "item" },
    evidence: {},
  },
  {
    check_id: "s1.extraction_gate",
    check_version: 1,
    code: "LOW_CONFIDENCE_DATA",
    result: "review",
    evidence: { message: "Line 2 quantity was read with low confidence." },
  },
];

const summary: CaseSummary = {
  case_id: "case-1",
  run_id: "run-1",
  invoice_id: "invoice-1",
  vendor_id: null,
  invoice_date: "2026-09-05",
  invoice_no: "INV-SYN-1",
  canonical_doc_no: "INVSYN1",
  computed_status: "needs_review",
  overlay: null,
  effective_status: "needs_review",
  severity_rank: 2,
  human_touched: false,
  version: 1,
  reason_codes: ["TOTAL_MISMATCH"],
  po_nos: ["PO-SYN-1"],
  grn_nos: [],
  invoice_taxable: "1250.00",
  invoice_total: "1475.00",
  expected_taxable: "1200.00",
  variance: "50.00",
  at_risk: "50.00",
  is_stale: false,
};

describe("reconciliation checks as verifiable rows (PD-4a)", () => {
  it("shows invoice, reference, difference, rule, result and reason per check", () => {
    const rows = checkRows(storedChecks);
    for (const row of rows.filter((item) => item.invoiceValue !== "—")) {
      expect(scaled(row.invoiceValue) - scaled(row.referenceValue)).toBe(scaled(row.difference));
    }
    expect(rows[0]).toMatchObject({
      name: "Invoice taxable vs expected",
      rule: "Within 1% or ₹ 10 (global tolerance)",
      result: "Needs review",
      reason: REASON_TEXT["TOTAL_MISMATCH"]?.message,
      differencePct: "4.17%",
    });
    expect(rows[1]?.reason).toBe("Within the rule.");
    expect(rows[2]?.reason).toBe("Line 2 quantity was read with low confidence.");

    render(<CheckTable rows={rows} />);
    const table = screen.getByRole("table");
    expect(within(table).getByText("Invoice taxable vs expected")).toBeTruthy();
    expect(within(table).getAllByRole("row")).toHaveLength(4);
  });

  it("has a plain sentence for every reason code of the engine", () => {
    const codes = ENGINE_REASON_CODES;
    expect(codes).toHaveLength(29);
    for (const code of codes) expect(REASON_TEXT[code]?.message, code).toBeTruthy();
  });

  it("lists taxable against expected taxable, the grand total apart, and the backend status", () => {
    render(
      <table>
        <tbody>
          <CaseTableRow item={summary} virtualIndex={0} isSelected={false} isActive={false} onSelect={vi.fn()} onToggleSelected={vi.fn()} />
        </tbody>
      </table>,
    );
    const cells = screen.getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells.slice(5, 9)).toEqual(["₹ 1250.00", "₹ 1200.00", "₹ 50.00", "₹ 1475.00"]);
    const [shown, reference, difference] = cells.slice(5, 8).map((cell) => scaled(cell ?? "0"));
    expect((shown ?? 0n) - (reference ?? 0n)).toBe(difference);
    expect(cells[9]).toBe("needs review");
  });
});
