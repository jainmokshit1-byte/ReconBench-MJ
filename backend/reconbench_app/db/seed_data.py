"""Seed constants copied from Backend Schema §10.2–§10.3; keep aligned with those sections."""

from __future__ import annotations

import json

DEFAULT_CONFIG = json.loads(r"""
{
  "engine": {
    "tolerances": {
      "limit_mode": "or",
      "qty_vs_grn": {
        "pct": "0",
        "abs": "0"
      },
      "cum_qty_vs_po": {
        "pct": "0",
        "abs": "0"
      },
      "unit_rate": {
        "pct": "0.5",
        "abs": "1"
      },
      "total_vs_expected": {
        "pct": "1",
        "abs": "10"
      },
      "arithmetic": {
        "pct": "0",
        "abs": "1"
      },
      "cum_value_vs_po": {
        "pct": "0",
        "abs": "0"
      }
    },
    "thresholds": {
      "extraction_gate": "0.70",
      "link_auto_accept": "0.95",
      "vendor_fuzzy_merge": "0.92",
      "vendor_fuzzy_suggest": "0.80",
      "item_aligned": "0.80",
      "item_review": "0.60",
      "item_single_line_explicit": "0.50",
      "spec_conflict_cap": "0.60",
      "step3_min_score": "0.80",
      "step3_min_lead": "0.15"
    },
    "ladder_confidence": {
      "labeled": "0.99",
      "reverse": "0.97",
      "via_grn": "0.95",
      "manual": "1.00"
    },
    "weights": {
      "step3": {
        "line_coverage": "0.40",
        "qty_feasible": "0.20",
        "rate_agree": "0.15",
        "date_ok": "0.10",
        "grn_support": "0.10",
        "buyer_match": "0.05"
      },
      "align": {
        "desc": "0.55",
        "code": "0.20",
        "hsn": "0.15",
        "uom": "0.10"
      }
    },
    "policies": {
      "payable_rate_mode": "pay_at_po_rate",
      "date_order_level": "fail",
      "pending_ageing_days": 30,
      "duplicate": {
        "suspect_days": 3,
        "suspect_edit_distance": 2
      },
      "reverse_lookup": {
        "min_canonical_len": 5,
        "window_runs": 6
      },
      "nonexact_tail_min_digits": 5
    },
    "synonyms": {
      "po_refs": [
        "PO No",
        "PO No.",
        "P.O. No",
        "PO #",
        "PO Ref",
        "PO Reference",
        "Purchase Order",
        "Purchase Order No",
        "Purchase Order Number",
        "Purchase Order Ref",
        "Order No",
        "Order Number",
        "Order Ref",
        "Buyer's Order No",
        "Customer PO",
        "Cust PO No",
        "Your Order No",
        "Your PO",
        "Your Ref",
        "Ref No",
        "Reference No",
        "Work Order",
        "WO No",
        "Contract No",
        "Indent No",
        "Against PO",
        "As per PO",
        "Ref. PO",
        "Purchasing Document",
        "PO Num",
        "Source Document",
        "Base Document",
        "Reference Doc"
      ],
      "grn_refs": [
        "GRN No",
        "MRN No",
        "MRR No",
        "Receipt No",
        "Delivery Challan",
        "DC No",
        "Challan No",
        "Delivery Note",
        "Delivery Note No",
        "Dispatch Doc No",
        "Gate Entry No",
        "LR No",
        "Docket No",
        "E-way Bill No"
      ],
      "supplier_invoice_no": [
        "Invoice No",
        "Supplier Invoice No",
        "Vendor Invoice No",
        "Bill No",
        "Party Bill No",
        "Challan No",
        "DC No",
        "Delivery Note No"
      ],
      "invoice_no": [
        "Invoice No",
        "Inv No",
        "Tax Invoice No",
        "Bill No",
        "Invoice #",
        "Voucher No"
      ],
      "vendor_name": [
        "Vendor",
        "Supplier",
        "Seller",
        "Sold By",
        "Billed By",
        "Party",
        "Party Name",
        "M/s"
      ],
      "buyer": [
        "Bill To",
        "Billed To",
        "Buyer",
        "Invoice To",
        "Sold To",
        "Consignee",
        "Ship To",
        "Customer"
      ],
      "quantity": [
        "Qty",
        "Quantity",
        "Nos",
        "Pcs",
        "Units",
        "Billed Qty",
        "Shipped Qty",
        "Recd Qty",
        "Accepted Qty",
        "Passed Qty",
        "OK Qty",
        "Rejected Qty",
        "Rej Qty",
        "Shortage Qty",
        "Damaged Qty"
      ],
      "rate_amount": [
        "Rate",
        "Unit Price",
        "Price/Unit",
        "Basic Rate",
        "Unit Rate",
        "Amount",
        "Taxable Value",
        "Basic Amount",
        "Line Total"
      ],
      "totals": [
        "Grand Total",
        "Invoice Total",
        "Total Amount",
        "Net Payable",
        "Amount Payable",
        "Amount Chargeable"
      ],
      "dates": [
        "Invoice Date",
        "Date of Issue",
        "Bill Date",
        "Dated",
        "GRN Date",
        "Receipt Date",
        "Inward Date",
        "MRN Date"
      ],
      "tax": [
        "CGST",
        "SGST",
        "UTGST",
        "IGST",
        "Central Tax",
        "State Tax",
        "Integrated Tax",
        "HSN",
        "SAC",
        "IRN",
        "Ack No"
      ]
    },
    "uom": {
      "aliases": {
        "PCS": "NOS",
        "EA": "NOS",
        "UNIT": "NOS",
        "KGS": "KG",
        "M": "MTR"
      },
      "conversions": []
    },
    "charge_keywords": [
      "freight",
      "packing",
      "insurance",
      "handling",
      "round off",
      "round-off",
      "discount"
    ],
    "buyer_entities": [],
    "gst_slabs": [
      {
        "rate": "5",
        "from": null,
        "to": null
      },
      {
        "rate": "12",
        "from": null,
        "to": null
      },
      {
        "rate": "18",
        "from": null,
        "to": null
      },
      {
        "rate": "28",
        "from": null,
        "to": null
      }
    ],
    "payment_terms": [
      {
        "label": "Net 30",
        "days": 30
      },
      {
        "label": "Net 60",
        "days": 60
      }
    ],
    "overrides": {
      "vendors": [],
      "items": []
    }
  },
  "app": {
    "limits": {
      "max_file_mb": 25,
      "max_pages": 100,
      "zip_max_entries": 5000,
      "zip_max_uncompressed_gb": 2,
      "zip_max_ratio": 100
    },
    "ui": {
      "poll_seconds": 2,
      "lakh_crore_in_excel": false
    },
    "retention": {
      "log_days": 30,
      "export_days": null,
      "document_days": null
    },
    "security": {
      "session_idle_minutes": 30,
      "session_absolute_hours": 12
    }
  }
}
""")

REASON_CODES = (
    {
        "code": "DUP_INVOICE",
        "level": "block",
        "stage": "S1",
        "title": "Duplicate invoice",
        "message": (
            "Same vendor, invoice number and financial year (or the same IRN) as an earlier "
            "invoice."
        ),
        "suggested_action": "Keep the original and do not pay this one.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "NO_PO_LINK",
        "level": "block",
        "stage": "S2",
        "title": "No PO found",
        "message": "No PO could be linked to this invoice.",
        "suggested_action": "Pick the PO yourself, or upload the missing PO.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "LINK_INFERRED",
        "level": "review",
        "stage": "S2",
        "title": "PO link needs confirming",
        "message": "The PO was worked out from the invoice content, not read from it.",
        "suggested_action": "Check the suggested PO, then confirm or change it.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "LOW_CONFIDENCE_DATA",
        "level": "review",
        "stage": "S1",
        "title": "Some fields are unreliable",
        "message": "A critical field was missing or read with low confidence.",
        "suggested_action": "Check the highlighted fields against the source document.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "DUP_SUSPECTED",
        "level": "review",
        "stage": "S1",
        "title": "Possible duplicate",
        "message": (
            "Looks like another invoice from the same vendor: similar number, same amount, "
            "nearby date."
        ),
        "suggested_action": "Compare the two, then approve this one or reject it.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": True,
        "is_active": True,
    },
    {
        "code": "ITEM_UNSURE",
        "level": "review",
        "stage": "S3",
        "title": "Item match uncertain",
        "message": "A line matches a PO line only weakly.",
        "suggested_action": "Confirm which PO line this is.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": True,
        "is_active": True,
    },
    {
        "code": "NO_TEXT_LAYER",
        "level": "review",
        "stage": "S1",
        "title": "Scanned document",
        "message": "The PDF has no readable text, so values could not be extracted.",
        "suggested_action": "Upload a text-based copy of the document.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": True,
        "is_active": True,
    },
    {
        "code": "VENDOR_MISMATCH",
        "level": "fail",
        "stage": "S6",
        "title": "Vendor differs from PO",
        "message": "The GSTIN or PAN does not match the PO's vendor.",
        "suggested_action": "Check whether the invoice belongs to another vendor.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "PO_NOT_OPEN",
        "level": "fail",
        "stage": "S1",
        "title": "PO is not open",
        "message": "The PO is closed or cancelled.",
        "suggested_action": "Ask procurement to reopen the PO, or reject the invoice.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "ITEM_NOT_ON_PO",
        "level": "fail",
        "stage": "S3",
        "title": "Item not on PO",
        "message": "A line does not match any line on the PO.",
        "suggested_action": "Ask whether the item was ordered; reject the line if not.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "QTY_OVER_PO",
        "level": "fail",
        "stage": "S4",
        "title": "Billed more than ordered",
        "message": "Total billed quantity across invoices is more than was ordered.",
        "suggested_action": "Pay up to the ordered quantity and ask the vendor for a credit note.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "QTY_OVER_GRN",
        "level": "fail",
        "stage": "S4",
        "title": "Billed more than received",
        "message": "The billed quantity is more than was received and accepted.",
        "suggested_action": "Hold the excess until goods are accepted, or ask for a credit note.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "RATE_HIGH",
        "level": "fail",
        "stage": "S5",
        "title": "Rate above PO",
        "message": "The invoice rate is higher than the PO rate, beyond tolerance.",
        "suggested_action": (
            "Ask the vendor to reissue at the PO rate, or get the price change approved."
        ),
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "TOTAL_MISMATCH",
        "level": "fail",
        "stage": "S5",
        "title": "Total differs from expected",
        "message": (
            "The invoice taxable value differs from billed quantity × PO rate, beyond tolerance."
        ),
        "suggested_action": "Check charges and rounding, and ask the vendor to correct it.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "MATH_ERROR",
        "level": "fail",
        "stage": "S5",
        "title": "Arithmetic does not add up",
        "message": "Quantity × rate, the line totals or the grand total do not add up.",
        "suggested_action": "Ask the vendor to correct the invoice.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "PO_VALUE_EXCEEDED",
        "level": "fail",
        "stage": "S5",
        "title": "PO value exceeded",
        "message": "Total invoiced value on this PO is above the PO value plus tolerance.",
        "suggested_action": "Check earlier invoices on this PO; get a PO amendment if needed.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "CURRENCY_DIFF",
        "level": "fail",
        "stage": "S6",
        "title": "Currency differs",
        "message": "The currency is not the same across the invoice, PO and GRN.",
        "suggested_action": "Check the currency; multi-currency is not supported yet.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "UOM_MISMATCH",
        "level": "fail",
        "stage": "S6",
        "title": "Unit differs",
        "message": "The unit of measure differs from the PO and cannot be converted.",
        "suggested_action": "Add a unit conversion in Settings, or ask the vendor to correct it.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "DATE_ORDER",
        "level": "fail",
        "stage": "S6",
        "title": "Date out of order",
        "message": "The invoice or GRN is dated before the PO.",
        "suggested_action": "Check the dates and ask the vendor to correct them if wrong.",
        "overridable": True,
        "level_configurable": True,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "GRN_PENDING",
        "level": "pending",
        "stage": "S4",
        "title": "Waiting for goods receipt",
        "message": "Billed quantity is not yet covered by accepted GRN quantity.",
        "suggested_action": "Wait for the GRN; the case re-checks itself when it is uploaded.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "RATE_LOW",
        "level": "warn",
        "stage": "S5",
        "title": "Rate below PO",
        "message": "The invoice rate is lower than the PO rate, beyond tolerance.",
        "suggested_action": "No action needed; note it for pricing review.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "TAX_RATE_DIFF",
        "level": "warn",
        "stage": "S6",
        "title": "Tax rate differs",
        "message": "The tax rate differs from the PO or is not in the slab list for that date.",
        "suggested_action": "Check the tax rate with the vendor.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "TAX_TYPE_MISMATCH",
        "level": "warn",
        "stage": "S6",
        "title": "Wrong tax type",
        "message": (
            "CGST and SGST versus IGST does not fit the supplier and place-of-supply states."
        ),
        "suggested_action": "Check the place of supply and the tax split.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "HSN_DIFF",
        "level": "warn",
        "stage": "S6",
        "title": "HSN differs",
        "message": "The HSN is missing or differs from the PO's.",
        "suggested_action": "Confirm the HSN code.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "GSTIN_INVALID",
        "level": "warn",
        "stage": "S6",
        "title": "GSTIN looks wrong",
        "message": "A GSTIN fails the format or checksum test.",
        "suggested_action": "Check the GSTIN on the source document.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "EXTRA_CHARGE",
        "level": "warn",
        "stage": "S5",
        "title": "Charge not on PO",
        "message": "A charge line such as freight or packing is not on the PO.",
        "suggested_action": "Confirm the charge was agreed, then approve or reject it.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "GRN_OVER_PO",
        "level": "warn",
        "stage": "S4",
        "title": "Received more than ordered",
        "message": "Received quantity is above the ordered quantity; the excess is not billable.",
        "suggested_action": "Check with stores and the vendor.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
    {
        "code": "LATE_DELIVERY",
        "level": "info",
        "stage": "S6",
        "title": "Late delivery",
        "message": "The goods were received after the PO delivery date.",
        "suggested_action": "For information only.",
        "overridable": True,
        "level_configurable": False,
        "is_proposed": False,
        "is_active": True,
    },
)
