import { z } from "zod";

import type { components } from "../api/generated";

type Schemas = components["schemas"];
export type EngineBody = Schemas["EngineBody"];
export type ToleranceValues = Schemas["ToleranceValuesBody"];
export type ToleranceCheck = "qty_vs_grn" | "cum_qty_vs_po" | "unit_rate" | "total_vs_expected" | "arithmetic" | "cum_value_vs_po";
export type SynonymGroup = keyof Schemas["SynonymsBody"];
export type ThresholdKey = keyof Schemas["ThresholdsBody"];

export interface ToleranceCheckInfo {
  key: ToleranceCheck;
  title: string;
  absUnit: string;
  example: (pct: string, abs: string, mode: string) => string;
}

const join = (mode: string) => (mode === "both" ? "and" : "or");

/** Tolerance checks in Schema §5.1 order with plain units and one-line examples. */
export const TOLERANCE_CHECKS: readonly ToleranceCheckInfo[] = [
  { key: "qty_vs_grn", title: "Invoice quantity vs accepted GRN", absUnit: "units", example: (pct, abs, mode) => `Billed quantity may exceed accepted GRN quantity by ${pct}% ${join(mode)} ${abs} units.` },
  { key: "cum_qty_vs_po", title: "Cumulative billed vs PO quantity", absUnit: "units", example: (pct, abs, mode) => `All invoices together may bill ${pct}% ${join(mode)} ${abs} units above the PO quantity.` },
  { key: "unit_rate", title: "Invoice unit rate vs PO rate", absUnit: "₹ per unit", example: (pct, abs, mode) => `Invoice rate may differ from the PO rate by ±${pct}% ${join(mode)} ±₹${abs} per unit.` },
  { key: "total_vs_expected", title: "Invoice taxable total vs expected", absUnit: "₹", example: (pct, abs, mode) => `Taxable total may differ from billed quantity × PO rate by ±${pct}% ${join(mode)} ±₹${abs}.` },
  { key: "arithmetic", title: "Invoice arithmetic", absUnit: "₹", example: (pct, abs, mode) => `Line and total arithmetic may be off by ${pct}% ${join(mode)} ₹${abs} (rounding).` },
  { key: "cum_value_vs_po", title: "Cumulative PO value", absUnit: "₹", example: (pct, abs, mode) => `Billed contract value may exceed the PO value by ${pct}% ${join(mode)} ₹${abs}.` },
];

export const SYNONYM_GROUPS: readonly [SynonymGroup, string][] = [
  ["po_refs", "PO number"],
  ["grn_refs", "GRN reference"],
  ["invoice_no", "Invoice number"],
  ["supplier_invoice_no", "Supplier invoice number"],
  ["vendor_name", "Vendor name"],
  ["buyer", "Buyer"],
  ["quantity", "Quantity"],
  ["rate_amount", "Rate and amount"],
  ["totals", "Totals"],
  ["dates", "Dates"],
  ["tax", "Tax"],
];

/** Thresholds named in the Settings plan list; the others stay at their saved values. */
export const THRESHOLDS: readonly [ThresholdKey, string, string][] = [
  ["item_aligned", "Item alignment: auto", "Lines scoring at or above this align automatically."],
  ["item_review", "Item alignment: review", "Lines from this score up to the auto threshold go to review."],
  ["vendor_fuzzy_merge", "Vendor-name fuzzy match", "A unique name match at or above this assigns the vendor."],
  ["link_auto_accept", "PO link auto-accept", "Links at or above this confidence are accepted without review."],
  ["extraction_gate", "Extraction confidence gate", "Invoices below this confidence wait for verification."],
];

const BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ";
const GSTIN_PATTERN = /^\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]$/;

/** Mirrors reconbench_core.normalize.gstin: pattern plus the base-36 check character. */
export function gstinProblem(raw: string): string | null {
  const value = raw.replace(/\s+/g, "").toUpperCase();
  if (!value) return "Enter the buyer GSTIN.";
  if (value.length !== 15) return `A GSTIN has 15 characters; this has ${value.length}.`;
  if (!GSTIN_PATTERN.test(value)) return "The GSTIN pattern is 2-digit state, 10-character PAN, entity, Z, check character.";
  let factor = 2;
  let total = 0;
  for (const character of [...value.slice(0, 14)].reverse()) {
    const product = factor * BASE36.indexOf(character);
    total += Math.floor(product / 36) + (product % 36);
    factor = factor === 2 ? 1 : 2;
  }
  const expected = BASE36[(36 - (total % 36)) % 36];
  return value[14] === expected ? null : "The check character does not match; re-check the GSTIN.";
}

const decimal = z.string().trim().regex(/^\d+(\.\d+)?$/, "Enter a non-negative number, for example 0.5.");
const probability = decimal.refine((value) => Number(value) <= 1, "Enter a value from 0 to 1.");
const tolerance = z.object({ pct: decimal, abs: decimal }).passthrough();
const wholeDays = z.custom<number>((value) => Number.isInteger(value) && (value as number) >= 0, "Enter whole days, 0 or more.");
const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Use a full date.").nullable();

const engineSchema = z.object({
  tolerances: z.object({
    limit_mode: z.enum(["or", "both"]),
    qty_vs_grn: tolerance,
    cum_qty_vs_po: tolerance,
    unit_rate: tolerance,
    total_vs_expected: tolerance,
    arithmetic: tolerance,
    cum_value_vs_po: tolerance,
  }),
  thresholds: z.record(z.string(), probability),
  policies: z.object({
    payable_rate_mode: z.enum(["pay_at_po_rate", "hold"]),
    date_order_level: z.enum(["fail", "warn"]),
    pending_ageing_days: wholeDays,
  }).passthrough(),
  synonyms: z.record(z.string(), z.array(z.string().trim().min(1, "A label cannot be empty."))),
  uom: z.object({
    aliases: z.record(z.string(), z.string().trim().min(1, "Enter the canonical unit.")),
    conversions: z.array(z.object({
      item_code: z.string().trim().min(1, "Enter the item code."),
      from: z.string().trim().min(1, "Enter the source unit."),
      to: z.string().trim().min(1, "Enter the target unit."),
      factor: decimal.refine((value) => Number(value) > 0, "The factor must be above 0."),
    })),
  }),
  buyer_entities: z.array(z.object({
    name: z.string().trim().min(1, "Enter the buyer entity name."),
    gstin: z.string().superRefine((value, context) => {
      const problem = gstinProblem(value);
      if (problem) context.addIssue({ code: "custom", message: problem });
    }),
  })),
  gst_slabs: z.array(z.object({ rate: decimal, from: isoDate, to: isoDate }).refine(
    (slab) => !slab.from || !slab.to || slab.from <= slab.to,
    { message: "The start date must be on or before the end date.", path: ["to"] },
  )),
  payment_terms: z.array(z.object({
    label: z.string().trim().min(1, "Enter a label such as Net 30."),
    days: wholeDays,
  })),
  overrides: z.object({
    vendors: z.array(z.object({ vendor_id: z.string().min(1, "Choose a vendor."), tolerances: z.record(z.string(), tolerance) })),
    items: z.array(z.object({ item_code: z.string().trim().min(1, "Enter the item code."), vendor_id: z.string().nullable(), tolerances: z.record(z.string(), tolerance) })),
  }),
}).passthrough().superRefine((engine, context) => {
  const thresholds = engine.thresholds;
  if (Number(thresholds["item_aligned"]) <= Number(thresholds["item_review"])) {
    context.addIssue({ code: "custom", path: ["thresholds", "item_review"], message: "The review threshold must be below the auto threshold." });
  }
  if (Number(thresholds["vendor_fuzzy_suggest"]) >= Number(thresholds["vendor_fuzzy_merge"])) {
    context.addIssue({ code: "custom", path: ["thresholds", "vendor_fuzzy_merge"], message: "Must stay above the vendor suggestion threshold." });
  }
  const seen = new Set<string>();
  engine.uom.conversions.forEach((row, index) => {
    const key = `${row.item_code.trim().toUpperCase()}|${row.from.trim().toUpperCase()}`;
    if (seen.has(key)) context.addIssue({ code: "custom", path: ["uom", "conversions", index, "from"], message: "This item already converts from this unit." });
    seen.add(key);
  });
});

/** Field-path → message for every client-side problem, keyed like "engine.uom.conversions.0.factor". */
export function validateEngine(engine: unknown): Record<string, string> {
  const result = engineSchema.safeParse(engine);
  if (result.success) return {};
  const errors: Record<string, string> = {};
  for (const issue of result.error.issues) {
    const key = ["engine", ...issue.path.map(String)].join(".");
    errors[key] ??= issue.message;
  }
  return errors;
}

export interface ConfigChange {
  path: string;
  before: string;
  after: string;
}

function leaves(value: unknown, prefix: string, out: Map<string, string>): void {
  if (Array.isArray(value)) {
    if (!value.length) out.set(prefix, "[]");
    value.forEach((item, index) => leaves(item, `${prefix}.${index}`, out));
  } else if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value);
    if (!entries.length) out.set(prefix, "{}");
    for (const [key, item] of entries) leaves(item, prefix ? `${prefix}.${key}` : key, out);
  } else {
    out.set(prefix, value === null || value === undefined ? "—" : String(value));
  }
}

/** Leaf-level differences between two config bodies, sorted by path. */
export function diffConfig(before: unknown, after: unknown): ConfigChange[] {
  const left = new Map<string, string>();
  const right = new Map<string, string>();
  leaves(before, "", left);
  leaves(after, "", right);
  const paths = [...new Set([...left.keys(), ...right.keys()])].sort();
  return paths
    .filter((path) => left.get(path) !== right.get(path))
    .map((path) => ({ path, before: left.get(path) ?? "—", after: right.get(path) ?? "—" }));
}

/** Read a nested value by dotted path; used to compare a field with its selected default. */
export function valueAt(source: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((current, key) => (
    typeof current === "object" && current !== null ? (current as Record<string, unknown>)[key] : undefined
  ), source);
}
