# Reconciliation engine CLI

The CLI is a local, database-free entry point for synthetic and approved XML/CSV
bundles. It uses the same parser, vendor-anchor, PO-link and S1–S7 engine code as
the application boundary.

## Input layout

```text
input/
  po/        # optional: gives untyped CSV/XML records the PO type
  grn/       # optional: gives untyped CSV/XML records the GRN type
  invoice/   # optional: gives untyped CSV/XML records the invoice type
```

Files can also sit at the input root when their type can be inferred from the
content. The CLI reads `.xml` and `.csv` files recursively; other extensions are
ignored. Subfolder names are hints only. A parser failure is reported per file,
and does not stop other files from being evaluated.

## Configuration

Pass either the engine object directly or the application config shape with an
`engine` member. Optional parser hints are keyed by a relative POSIX path or by
the `po`, `grn`, or `invoice` folder name:

```json
{
  "engine": {
    "limit_mode": "or",
    "payable_rate_mode": "pay_at_po_rate",
    "tolerances": {
      "unit_rate": { "pct": "0.5", "abs": "1" }
    }
  },
  "parser_hints": {
    "invoice": {
      "doc_type": "invoice",
      "column_mapping": { "Invoice No": "doc_no" }
    }
  }
}
```

Use JSON strings for decimal values. The schema rejects binary-float numeric
inputs by design. The rules fingerprint covers the check registry and `engine`
settings; parser hints and `app` settings do not change `rules_hash`, but their
resolved configuration still contributes to the run manifest.

## Run

```powershell
uv run --project backend python -m reconbench_core.cli run .\input `
  --config .\engine-config.json `
  --run-date 2026-10-01 `
  --out .\result.json
```

The run date is mandatory: pending-age calculations never read the machine
clock. Vendor identity is anchored only by a valid supplier GSTIN/PAN in this
standalone mode. PO references use the deterministic link ladder; unresolved or
ambiguous cases stay unlinked or under review. A file is never declared
financially matched merely because it parsed.

The JSON output includes engine version, rules hash, config and manifest hashes,
per-file parse summaries, parse errors, and version-stamped `CaseResult`/`LineResult`
objects. Amounts are serialized as decimal strings. A complete run exits 0; a
run with any parse error or no usable documents still writes a partial report
and exits 2.

The local command verifies repeatability on one host. CI runs the committed
synthetic bundle on Linux and Windows and compares the result bytes. Real vendor
layouts and the golden extraction-accuracy gate remain pending until G-07 is
opened by the user with approved samples.
