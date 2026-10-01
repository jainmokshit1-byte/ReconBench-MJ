"""Generate the 20 synthetic acceptance fixtures documented by PRD scenario IDs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures"

SCENARIOS: list[dict[str, Any]] = [
    {
        "title": "Exact one PO, one GRN, one invoice",
        "input": {
            "purchase_order": {"number": "SYN-PO-1001", "qty": "10", "rate": "1250.00"},
            "grn": {"number": "SYN-GRN-1001", "accepted_qty": "10"},
            "invoice": {
                "number": "SYN-INV-1001",
                "po_ref": "SYN-PO-1001",
                "qty": "10",
                "rate": "1250.00",
            },
        },
        "expected": {
            "link": {"po": "SYN-PO-1001", "method": "labeled"},
            "status": "Matched",
            "reason_codes": [],
            "ledger": {
                "ordered": "10",
                "billed_before": "0",
                "this_invoice": "10",
                "grn_accepted_cumulative": "10",
                "balance": "0",
            },
        },
    },
    {
        "title": "Partial delivery C1: 90 now and 10 later",
        "input": {
            "purchase_order": {
                "number": "BM-PO-0142",
                "vendor": "Acer India Pvt Ltd",
                "item": "Acer Aspire 5 (i5 / 16GB / 512GB)",
                "hsn": "8471",
                "qty": "100",
                "rate": "48000.00",
                "gst_rate": "18",
            },
            "documents": [
                {
                    "type": "grn",
                    "number": "GRN-2609-011",
                    "date": "2026-09-12",
                    "accepted_qty": "90",
                },
                {
                    "type": "invoice",
                    "number": "ACR/26-27/2201",
                    "date": "2026-09-14",
                    "qty": "90",
                    "taxable": "4320000.00",
                    "gst": "777600.00",
                    "total": "5097600.00",
                },
                {
                    "type": "grn",
                    "number": "GRN-2609-047",
                    "date": "2026-09-26",
                    "accepted_qty": "10",
                },
                {
                    "type": "invoice",
                    "number": "ACR/26-27/2260",
                    "date": "2026-09-28",
                    "qty": "10",
                    "taxable": "480000.00",
                    "gst": "86400.00",
                    "total": "566400.00",
                },
            ],
        },
        "expected": {
            "results": [
                {
                    "invoice": "ACR/26-27/2201",
                    "link": {"po": "BM-PO-0142", "method": "labeled"},
                    "status": "Matched",
                    "reason_codes": [],
                    "ledger": {
                        "ordered": "100",
                        "billed_before": "0",
                        "this_invoice": "90",
                        "grn_accepted_cumulative": "90",
                        "balance": "10",
                    },
                },
                {
                    "invoice": "ACR/26-27/2260",
                    "link": {"po": "BM-PO-0142", "method": "labeled"},
                    "status": "Matched",
                    "reason_codes": [],
                    "ledger": {
                        "ordered": "100",
                        "billed_before": "90",
                        "this_invoice": "10",
                        "grn_accepted_cumulative": "100",
                        "balance": "0",
                    },
                },
            ]
        },
    },
    {
        "title": "Cumulative receipts of 60 and 30 cover one invoice for 90",
        "input": {
            "purchase_order": {"number": "SYN-PO-1003", "qty": "100", "rate": "200.00"},
            "grns": [
                {"number": "SYN-GRN-1003A", "accepted_qty": "60"},
                {"number": "SYN-GRN-1003B", "accepted_qty": "30"},
            ],
            "invoice": {"number": "SYN-INV-1003", "qty": "90", "rate": "200.00"},
        },
        "expected": {
            "link": {"po": "SYN-PO-1003", "method": "labeled"},
            "status": "Matched",
            "reason_codes": [],
            "ledger": {
                "ordered": "100",
                "billed_before": "0",
                "this_invoice": "90",
                "grn_accepted_cumulative": "90",
                "balance": "10",
            },
        },
    },
    {
        "title": "Second invoice exceeds the cumulative PO cap",
        "input": {
            "purchase_order": {"number": "SYN-PO-1004", "qty": "100", "rate": "500.00"},
            "grns": [{"accepted_qty": "60"}, {"accepted_qty": "45"}],
            "invoices": [
                {"number": "SYN-INV-1004A", "qty": "90"},
                {"number": "SYN-INV-1004B", "qty": "15"},
            ],
        },
        "expected": {
            "link": {"po": "SYN-PO-1004", "method": "labeled"},
            "status": "Exception",
            "reason_codes": ["QTY_OVER_PO", "QTY_OVER_GRN"],
            "ledger": {
                "ordered": "100",
                "billed_before": "90",
                "this_invoice": "15",
                "billed_cumulative": "105",
                "grn_accepted_cumulative": "105",
                "eligible": "10",
                "balance": "0",
                "at_risk": "2500.00",
            },
        },
    },
    {
        "title": "Late GRN leaves the invoice pending, then resolves",
        "input": {
            "purchase_order": {"number": "SYN-PO-1005", "qty": "10", "rate": "800.00"},
            "grns_before": [{"accepted_qty": "8"}],
            "invoice": {"number": "SYN-INV-1005", "qty": "10"},
            "grns_after": [{"number": "SYN-GRN-1005B", "accepted_qty": "2"}],
        },
        "expected": {
            "before_late_grn": {
                "status": "Pending",
                "reason_codes": ["GRN_PENDING"],
                "ledger": {
                    "ordered": "10",
                    "billed_before": "0",
                    "this_invoice": "10",
                    "grn_accepted_cumulative": "8",
                    "balance": "0",
                },
            },
            "after_late_grn": {"status": "Matched", "reason_codes": []},
        },
    },
    {
        "title": "Rejected receipt quantity is not payable",
        "input": {
            "purchase_order": {"number": "SYN-PO-1006", "qty": "10", "rate": "1000.00"},
            "grn": {"accepted_qty": "8", "rejected_qty": "2"},
            "invoice": {"number": "SYN-INV-1006", "qty": "10"},
        },
        "expected": {
            "link": {"po": "SYN-PO-1006", "method": "labeled"},
            "status": "Exception",
            "reason_codes": ["QTY_OVER_GRN"],
            "ledger": {
                "ordered": "10",
                "this_invoice": "10",
                "grn_accepted_cumulative": "8",
                "eligible": "8",
                "payable_qty": "8",
                "at_risk": "2000.00",
            },
        },
    },
    {
        "title": "Rate tolerance accepts a small variance and flags a high rate",
        "input": {
            "purchase_order": {"number": "SYN-PO-1007", "rate": "1000.00"},
            "invoice_variants": [
                {"number": "SYN-INV-1007A", "rate": "1003.00", "delta_pct": "0.30"},
                {"number": "SYN-INV-1007B", "rate": "1020.00", "delta_pct": "2.00"},
            ],
        },
        "expected": {
            "variants": [
                {"invoice": "SYN-INV-1007A", "status": "Matched", "reason_codes": []},
                {
                    "invoice": "SYN-INV-1007B",
                    "status": "Exception",
                    "reason_codes": ["RATE_HIGH"],
                    "at_risk": "200.00",
                },
            ]
        },
    },
    {
        "title": "Specification guard rejects a 16GB item against an 8GB PO line",
        "input": {
            "purchase_order": {"number": "SYN-PO-1008", "item": "Laptop 8GB RAM"},
            "invoice": {"number": "SYN-INV-1008", "item": "Laptop 16GB RAM"},
        },
        "expected": {
            "link": {"po": "SYN-PO-1008", "method": "labeled"},
            "status": "Exception",
            "reason_codes": ["ITEM_NOT_ON_PO"],
        },
    },
    {
        "title": "PO-link ladder uses labelled reference, reverse lookup, then a suggestion",
        "input": {
            "purchase_order": {"number": "BM-PO-0142", "open_qty": "10"},
            "invoice": {"number": "ACR/26-27/2260"},
            "grn": {
                "number": "GRN-2609-047",
                "invoice_reference": "ACR/26-27/2260",
                "po": "BM-PO-0142",
            },
            "variants": ["po_ref_on_invoice", "po_ref_in_grn", "single_open_candidate"],
        },
        "expected": {
            "variants": [
                {
                    "variant": "po_ref_on_invoice",
                    "link": {"po": "BM-PO-0142", "method": "labeled"},
                    "status": "Matched",
                    "reason_codes": [],
                },
                {
                    "variant": "po_ref_in_grn",
                    "link": {"po": "BM-PO-0142", "method": "via_grn"},
                    "status": "Matched",
                    "reason_codes": [],
                },
                {
                    "variant": "single_open_candidate",
                    "link": {
                        "po": "BM-PO-0142",
                        "method": "content",
                        "state": "suggested",
                    },
                    "status": "Needs Review",
                    "reason_codes": ["LINK_INFERRED"],
                },
            ]
        },
    },
    {
        "title": "PO number canonicalization ignores separators and leading-zero formatting",
        "input": {"purchase_order": "PO/26-27/0142", "invoice": "PO-26-27-142"},
        "expected": {
            "link": {"po": "PO-26-27-142", "method": "labeled"},
            "status": "Matched",
            "reason_codes": [],
            "canonical_po": "PO2627142",
        },
    },
    {
        "title": "A repeated invoice is marked duplicate and consumes no ledger balance",
        "input": {
            "purchase_order": {"number": "SYN-PO-1011", "qty": "10"},
            "invoices": [
                {"number": "SYN-INV-1011", "qty": "4"},
                {"number": "SYN-INV-1011", "qty": "4", "upload": "rescan"},
            ],
        },
        "expected": {
            "duplicate_invoice": {
                "status": "Duplicate",
                "reason_codes": ["DUP_INVOICE"],
                "ledger_consumed": "0",
            }
        },
    },
    {
        "title": "Ledger name and legal name resolve to the same supplier",
        "input": {
            "vendor_alias": "Example Components",
            "vendor_legal_name": "Example Components Private Limited",
            "gstin": "27ABCDE1234F1Z5",
        },
        "expected": {
            "vendor_identity": "one_vendor",
            "status": "Matched",
            "reason_codes": [],
        },
    },
    {
        "title": "An invoice citing another supplier's PO fails the vendor check",
        "input": {
            "invoice_vendor": "Vendor A Synthetic Ltd",
            "po_vendor": "Vendor B Synthetic Ltd",
            "invoice_po": "SYN-PO-1013",
        },
        "expected": {
            "link": {"po": "SYN-PO-1013", "method": "labeled"},
            "status": "Exception",
            "reason_codes": ["VENDOR_MISMATCH"],
        },
    },
    {
        "title": "One invoice has separate lines linked to two purchase orders",
        "input": {
            "purchase_orders": [
                {"number": "SYN-PO-1014A", "item": "Monitor", "qty": "10"},
                {"number": "SYN-PO-1014B", "item": "Dock", "qty": "5"},
            ],
            "invoice": {
                "number": "SYN-INV-1014",
                "lines": [
                    {"item": "Monitor", "qty": "10", "po": "SYN-PO-1014A"},
                    {"item": "Dock", "qty": "5", "po": "SYN-PO-1014B"},
                ],
            },
        },
        "expected": {
            "line_links": [
                {"line": 1, "po": "SYN-PO-1014A"},
                {"line": 2, "po": "SYN-PO-1014B"},
            ],
            "status": "Matched",
            "reason_codes": [],
        },
    },
    {
        "title": "IGST on a synthetic intra-state supply is surfaced as a tax warning",
        "input": {
            "supplier_state": "27",
            "buyer_state": "27",
            "invoice_tax": "IGST",
            "invoice": "SYN-INV-1015",
        },
        "expected": {
            "status": "Matched (flags)",
            "reason_codes": ["TAX_TYPE_MISMATCH"],
        },
    },
    {
        "title": "An extra freight line is visible as a matched flag",
        "input": {
            "purchase_order": {"number": "SYN-PO-1016", "item": "Equipment"},
            "invoice": {
                "number": "SYN-INV-1016",
                "lines": [
                    {"type": "goods", "item": "Equipment"},
                    {"type": "charge", "item": "Freight", "amount": "250.00"},
                ],
            },
        },
        "expected": {"status": "Matched (flags)", "reason_codes": ["EXTRA_CHARGE"]},
    },
    {
        "title": "A prior-period PO in the pool links; a PO outside the pool is unlinked",
        "input": {
            "period": "2026-09",
            "in_pool": {"purchase_order": "SYN-PO-1017A", "invoice": "SYN-INV-1017A"},
            "not_in_pool": {"invoice_po": "SYN-PO-1017B", "po_uploaded": False},
        },
        "expected": {
            "in_pool": {"status": "Matched", "reason_codes": []},
            "not_in_pool": {
                "status": "Unlinked",
                "reason_codes": ["NO_PO_LINK"],
                "detail": "PO_NOT_IN_POOL",
            },
        },
    },
    {
        "title": "Ingestion order does not change final reconciliation results",
        "input": {
            "documents": [
                "SYN-INV-1018-SEP",
                "SYN-INV-1018-OCT",
                "SYN-PO-1018",
                "SYN-GRN-1018",
            ],
            "orders_to_compare": [
                ["SYN-INV-1018-SEP", "SYN-INV-1018-OCT"],
                ["SYN-INV-1018-OCT", "SYN-INV-1018-SEP"],
            ],
        },
        "expected": {
            "status": "Matched",
            "reason_codes": [],
            "permutation_results_equal": True,
        },
    },
    {
        "title": "Unreadable scanned document never passes silently",
        "input": {
            "document": "synthetic-only.pdf-placeholder",
            "scan_quality": "unreadable",
            "real_pdf_included": False,
        },
        "expected": {
            "status": "Needs Review",
            "reason_codes": ["NO_TEXT_LAYER"],
            "auto_matched": False,
            "pending_until": "real scan supplied for later parser work",
        },
    },
    {
        "title": "AI mode does not change calculations or verdicts",
        "input": {
            "invoice": "SYN-INV-1020",
            "purchase_order": "SYN-PO-1020",
            "ai_modes": ["rules_only", "ai_assisted"],
            "stub_provider_response": {"qty": "999999", "verdict": "Matched"},
        },
        "expected": {
            "status": "Matched",
            "reason_codes": [],
            "math_and_verdict_identical_between_modes": True,
            "provider_verdict_ignored": True,
        },
    },
]


def write_yaml(path: Path, contents: dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(contents, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for index, scenario in enumerate(SCENARIOS, start=1):
        scenario_id = f"SCN-{index:02d}"
        folder = FIXTURES / scenario_id
        folder.mkdir(parents=True, exist_ok=True)
        write_yaml(
            folder / "input.yaml",
            {
                "scenario_id": scenario_id,
                "synthetic": True,
                "title": scenario["title"],
                "input": scenario["input"],
            },
        )
        write_yaml(
            folder / "expected.yaml",
            {"scenario_id": scenario_id, **scenario["expected"]},
        )
    print(f"Wrote {len(SCENARIOS)} synthetic acceptance fixtures under {FIXTURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
