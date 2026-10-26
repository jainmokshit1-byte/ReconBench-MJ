// Reconciliation checks as verifiable rows (PD-4a). Values are the engine's stored ones;
// nothing here does money arithmetic.

export interface ReasonText {
  title: string;
  message: string;
}

export interface CheckRow {
  key: string;
  name: string;
  invoiceValue: string;
  referenceValue: string;
  difference: string;
  differencePct: string;
  rule: string;
  result: string;
  reason: string;
}

// The workspace catalogue's plain sentence per code (seed text), used when the catalogue
// has not loaded or a workspace removed a code.
export const REASON_TEXT: Record<string, ReasonText> = {
  DUP_INVOICE: { title: "Duplicate invoice", message: "Same vendor, invoice number and financial year (or the same IRN) as an earlier invoice." },
  NO_PO_LINK: { title: "No PO found", message: "No PO could be linked to this invoice." },
  LINK_INFERRED: { title: "PO link needs confirming", message: "The PO was worked out from the invoice content, not read from it." },
  LOW_CONFIDENCE_DATA: { title: "Some fields are unreliable", message: "A critical field was missing or read with low confidence." },
  DUP_SUSPECTED: { title: "Possible duplicate", message: "Looks like another invoice from the same vendor: similar number, same amount, nearby date." },
  ITEM_UNSURE: { title: "Item match uncertain", message: "A line matches a PO line only weakly." },
  NO_TEXT_LAYER: { title: "Scanned document", message: "The PDF has no readable text, so values could not be extracted." },
  VENDOR_MISMATCH: { title: "Vendor differs from PO", message: "The GSTIN or PAN does not match the PO's vendor." },
  PO_NOT_OPEN: { title: "PO is not open", message: "The PO is closed or cancelled." },
  ITEM_NOT_ON_PO: { title: "Item not on PO", message: "A line does not match any line on the PO." },
  QTY_OVER_PO: { title: "Billed more than ordered", message: "Total billed quantity across invoices is more than was ordered." },
  QTY_OVER_GRN: { title: "Billed more than received", message: "The billed quantity is more than was received and accepted." },
  RATE_HIGH: { title: "Rate above PO", message: "The invoice rate is higher than the PO rate, beyond tolerance." },
  TOTAL_MISMATCH: { title: "Total differs from expected", message: "The invoice taxable value differs from billed quantity × PO rate, beyond tolerance." },
  MATH_ERROR: { title: "Arithmetic does not add up", message: "Quantity × rate, the line totals or the grand total do not add up." },
  PO_VALUE_EXCEEDED: { title: "PO value exceeded", message: "Total invoiced value on this PO is above the PO value plus tolerance." },
  CURRENCY_DIFF: { title: "Currency differs", message: "The currency is not the same across the invoice, PO and GRN." },
  UOM_MISMATCH: { title: "Unit differs", message: "The unit of measure differs from the PO and cannot be converted." },
  DATE_ORDER: { title: "Date out of order", message: "The invoice or GRN is dated before the PO." },
  GRN_PENDING: { title: "Waiting for goods receipt", message: "Billed quantity is not yet covered by accepted GRN quantity." },
  RATE_LOW: { title: "Rate below PO", message: "The invoice rate is lower than the PO rate, beyond tolerance." },
  TAX_RATE_DIFF: { title: "Tax rate differs", message: "The tax rate differs from the PO or is not in the slab list for that date." },
  TAX_TYPE_MISMATCH: { title: "Wrong tax type", message: "CGST and SGST versus IGST does not fit the supplier and place-of-supply states." },
  HSN_DIFF: { title: "HSN differs", message: "The HSN is missing or differs from the PO's." },
  GSTIN_INVALID: { title: "GSTIN looks wrong", message: "A GSTIN fails the format or checksum test." },
  EXTRA_CHARGE: { title: "Charge not on PO", message: "A charge line such as freight or packing is not on the PO." },
  GRN_OVER_PO: { title: "Received more than ordered", message: "Received quantity is above the ordered quantity; the excess is not billable." },
  LATE_DELIVERY: { title: "Late delivery", message: "The goods were received after the PO delivery date." },
};

const CHECK_NAMES: Record<string, string> = {
  "s1.exact_duplicate": "Duplicate invoice",
  "s1.suspected_duplicate": "Possible duplicate",
  "s1.extraction_gate": "Data quality",
  "s1.po_open": "PO open",
  "s1.vendor_resolved": "Vendor resolved",
  "s2.po_link": "PO link",
  "s2.link_confidence": "PO link confidence",
  "s2.multiple_pos_line": "One PO per line",
  "s3.item_alignment": "Item matches PO line",
  "s4.cumulative_qty_vs_po": "Billed vs ordered (all invoices)",
  "s4.cumulative_qty_vs_grn": "Billed vs received (all invoices)",
  "s4.grn_over_po": "Received vs ordered",
  "s4.grn_pending": "Goods receipt present",
  "s4.grn_quantity_fallback": "GRN quantity read",
  "s4.ledger_completeness": "PO history complete",
  "s5.cumulative_po_value": "Invoiced vs PO value",
  "s5.extra_charge": "Charges on PO",
  "s5.grand_total": "Grand total adds up",
  "s5.line_math": "Line arithmetic",
  "s5.taxable_sum": "Taxable total adds up",
  "s5.total_vs_expected": "Invoice taxable vs expected",
  "s5.unit_rate": "Rate vs PO rate",
  "s6.buyer_entity": "Billed to us",
  "s6.buyer_gstin_valid": "Buyer GSTIN valid",
  "s6.computed_tax": "Tax amount",
  "s6.currency": "Currency",
  "s6.date_order": "Invoice dated after PO",
  "s6.grn_date_order": "GRN dated after PO",
  "s6.hsn": "HSN",
  "s6.late_delivery": "Delivery date",
  "s6.tax_rate_diff": "Tax rate vs PO",
  "s6.tax_slab": "Tax slab",
  "s6.tax_type": "Tax type",
  "s6.uom": "Unit of measure",
  "s6.vendor_gstin_valid": "Vendor GSTIN valid",
  "s6.vendor_match": "Vendor matches PO",
  "s6.vendor_pan": "Vendor PAN",
};

const RESULT_TEXT: Record<string, string> = {
  pass: "Passed",
  info: "Information",
  warn: "Warning",
  pending: "Pending",
  review: "Needs review",
  fail: "Failed",
  block: "Blocked",
};

function text(value: unknown): string {
  if (typeof value === "string" && value !== "") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "—";
}

export function checkName(checkId: unknown): string {
  const id = typeof checkId === "string" ? checkId : "";
  return CHECK_NAMES[id] ?? (id ? id.replace(/^s\d\./, "").replaceAll("_", " ") : "Check");
}

export function ruleText(tolerance: unknown): string {
  if (typeof tolerance !== "object" || tolerance === null) return "Exact";
  const rule = tolerance as Record<string, unknown>;
  const pct = text(rule["pct"]);
  const abs = text(rule["abs"]);
  const joiner = rule["mode"] === "both" ? "and" : "or";
  const source = typeof rule["source"] === "string" ? ` (${rule["source"]} tolerance)` : "";
  return `Within ${pct}% ${joiner} ₹ ${abs}${source}`;
}

export function reasonFor(
  check: Record<string, unknown>,
  catalogue: Record<string, ReasonText> = REASON_TEXT,
): string {
  const evidence = check["evidence"];
  if (typeof evidence === "object" && evidence !== null) {
    const message = (evidence as Record<string, unknown>)["message"];
    if (typeof message === "string" && message.trim()) return message.trim();
  }
  const code = typeof check["code"] === "string" ? check["code"] : "";
  if (code) return (catalogue[code] ?? REASON_TEXT[code])?.message ?? code.replaceAll("_", " ");
  const result = typeof check["result"] === "string" ? check["result"] : "";
  return result === "pass" ? "Within the rule." : (RESULT_TEXT[result] ?? "No reason stored.");
}

export function checkRows(
  checks: Array<Record<string, unknown>>,
  catalogue: Record<string, ReasonText> = REASON_TEXT,
): CheckRow[] {
  return checks.map((check, index) => {
    const result = typeof check["result"] === "string" ? check["result"] : "";
    return {
      key: `${text(check["check_id"])}-${index}`,
      name: checkName(check["check_id"]),
      invoiceValue: text(check["actual"]),
      referenceValue: text(check["expected"]),
      difference: text(check["variance"]),
      differencePct: check["variance_pct"] === null || check["variance_pct"] === undefined ? "—" : `${text(check["variance_pct"])}%`,
      rule: ruleText(check["tolerance"]),
      result: RESULT_TEXT[result] ?? (result ? result.replaceAll("_", " ") : "—"),
      reason: reasonFor(check, catalogue),
    };
  });
}
