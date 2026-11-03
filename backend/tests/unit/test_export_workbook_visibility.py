"""Export visibility: Not included, own GSTIN, payable incl. GST, total fallback, PO Balance."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import reconbench_app.export_service as export_service
from openpyxl import load_workbook
from reconbench_app.export_service import ExportService


def _case(*, vendor_id: UUID, invoice_no: str = "0142") -> dict[str, Any]:
    return {
        "id": uuid4(),
        "effective_vendor_id": vendor_id,
        "link_summary": [],
        "invoice_taxable": Decimal("200.00"),
        "cgst_total": Decimal("10.00"),
        "sgst_total": Decimal("0"),
        "igst_total": Decimal("0"),
        "round_off": Decimal("0"),
        "invoice_total": Decimal("210.00"),
        "expected_taxable": Decimal("200.00"),
        "variance": Decimal("0"),
        "variance_pct": Decimal("0"),
        "payable_taxable": Decimal("180.00"),
        "at_risk": Decimal("20.00"),
        "effective_status": "exception",
        "status": "exception",
        "reason_codes": ["RATE_MISMATCH"],
        "payment_due_date": date(2026, 10, 15),
        "vendor_name": "Vendor A",
        "effective_vendor_name": "Vendor A",
        "vendor_gstin": "29ABCDE1234F1Z5",
        "invoice_no": invoice_no,
        "invoice_date": date(2026, 10, 1),
        "po_nos": ["0007"],
        "grn_nos": ["G-1"],
        "human_touched": False,
        "reading_method": "rules",
    }


def _context(vendor_ids: list[UUID], **extra: Any) -> dict[str, Any]:
    return {
        "workspace_id": uuid4(),
        "run_id": uuid4(),
        "filters": {},
        "run": {
            "finished_at": datetime(2026, 10, 2),
            "engine_version": "0.1",
            "rules_hash": "synthetic-rules-hash",
            "config_version_id": str(uuid4()),
            "ai_mode": "rules_only",
            "ai_provider": None,
            "ai_model": None,
            "config_body": {"engine": {"tolerances": {}}},
        },
        "vendor_doc_counts": {v: Counter({"po": 1}) for v in vendor_ids},
        "gstins": {v: ["29ABCDE1234F1Z5"] for v in vendor_ids},
    } | extra


def _write(path: Path, cases: list[dict[str, Any]], vendor_ids: list[UUID], **extra: Any) -> None:
    ExportService._write_workbook(
        ExportService.__new__(ExportService),
        path,
        cases=cases,
        lines=[],
        context=_context(vendor_ids, **extra),
    )


def _summary(workbook: Any) -> dict[str, Any]:
    return {
        row[0]: row[1]
        for row in workbook["Summary"].iter_rows(min_row=2, max_col=2, values_only=True)
    }


def test_invoice_gstin_is_the_invoices_own_not_the_vendor_list(tmp_path: Path) -> None:
    vendor_id = uuid4()
    path = tmp_path / "gstin.xlsx"
    _write(path, [_case(vendor_id=vendor_id) | {"vendor_gstin": "27AAAAA0000A1Z5"}], [vendor_id])

    workbook = load_workbook(path, data_only=True)
    try:
        assert workbook["Invoice Recon"]["B2"].value == "27AAAAA0000A1Z5"
        assert "29ABCDE1234F1Z5" in workbook["Vendor Summary"]["B2"].value
    finally:
        workbook.close()


def test_payable_is_labelled_ex_gst_and_has_an_incl_gst_column(tmp_path: Path) -> None:
    vendor_id = uuid4()
    path = tmp_path / "payable.xlsx"
    no_taxable = _case(vendor_id=vendor_id, invoice_no="N") | {
        "invoice_taxable": None,
        "payable_taxable": Decimal("50.00"),
    }
    _write(path, [_case(vendor_id=vendor_id), no_taxable], [vendor_id])

    values = load_workbook(path, data_only=True)
    try:
        sheet = values["Invoice Recon"]
        assert sheet["O1"].value == "Payable (ex-GST)"
        assert sheet["V1"].value == "Payable incl. GST"
        # tax 10 on taxable 200 = 5%; payable 180 -> 189.00
        assert sheet["V2"].value == pytest.approx(189.0)
        assert sheet["V3"].value is None
        summary = _summary(values)
        assert "₹ Payable (ex-GST)" in summary
        assert "₹ Payable" not in summary
        assert summary["₹ Payable incl. GST"] == pytest.approx(189.0)
    finally:
        values.close()


def test_payable_gst_rounds_half_up() -> None:
    record = {
        "invoice_taxable": Decimal("3"),
        "cgst_total": Decimal("0.5"),
        "sgst_total": Decimal("0"),
        "igst_total": Decimal("0"),
        "payable_taxable": Decimal("1"),
    }
    # GST 1 * (0.5 / 3) = 0.1666.. -> 0.17; payable incl. GST 1.17
    assert export_service._payable_incl_gst(record) == Decimal("1.17")
    half = record | {"invoice_taxable": Decimal("8"), "cgst_total": Decimal("0.5")}
    # exact half: GST 2 * 0.5 / 8 = 0.125 -> 0.13
    assert export_service._payable_incl_gst(half | {"payable_taxable": Decimal("2")}) == Decimal(
        "2.13"
    )
    assert export_service._payable_incl_gst(record | {"invoice_taxable": Decimal("0")}) is None


def test_invoiced_falls_back_to_taxable_plus_tax_with_a_note(tmp_path: Path) -> None:
    vendor_id = uuid4()
    path = tmp_path / "fallback.xlsx"
    derived = _case(vendor_id=vendor_id, invoice_no="D") | {
        "invoice_total": None,
        "cgst_total": Decimal("9"),
        "sgst_total": Decimal("9"),
        "round_off": Decimal("0.5"),
    }
    _write(path, [derived], [vendor_id])

    values = load_workbook(path, data_only=True)
    try:
        sheet = values["Invoice Recon"]
        assert sheet["K2"].value == pytest.approx(218.5)
        assert sheet["W1"].value == "Notes"
        assert "total not read" in sheet["W2"].value
        assert values["Exceptions"]["E2"].value == pytest.approx(218.5)
        assert values["Vendor Summary"]["J2"].value == pytest.approx(218.5)
        assert _summary(values)["₹ Invoiced"] == pytest.approx(218.5)
    finally:
        values.close()


def test_not_included_sheet_lists_unreconciled_documents_and_files(tmp_path: Path) -> None:
    vendor_id = uuid4()
    path = tmp_path / "notincluded.xlsx"
    rows = [
        {
            "kind": "Document",
            "doc_type": "invoice",
            "doc_no": "=SCAN-9",
            "file_name": "scan9.pdf",
            "state": "failed",
            "code": "OUTPUT_CAP",
        },
        {
            "kind": "File",
            "doc_type": None,
            "doc_no": None,
            "file_name": "bad.xml",
            "state": "partial",
            "code": "PARSE_PARTIAL",
        },
    ]
    _write(path, [_case(vendor_id=vendor_id)], [vendor_id], not_included=rows)

    values = load_workbook(path, data_only=True)
    try:
        sheet = values["Not included"]
        assert [c.value for c in sheet[1]] == [
            "Kind",
            "Doc type",
            "Doc No.",
            "File",
            "State",
            "Code",
        ]
        data = list(sheet.iter_rows(min_row=2, values_only=True))
        assert data[0] == ("Document", "invoice", "=SCAN-9", "scan9.pdf", "failed", "OUTPUT_CAP")
        assert sheet["C2"].data_type == "s"
        assert data[1][3] == "bad.xml"
        assert _summary(values)["Not included (documents / files)"] == 2
    finally:
        values.close()


def test_po_balance_sheet_lists_ledger_lines_with_open_value(tmp_path: Path) -> None:
    vendor_id = uuid4()
    path = tmp_path / "pobal.xlsx"
    po_balance = [
        {
            "po_no": "PO-1",
            "po_line_no": 1,
            "description": "Widget",
            "po_rate": Decimal("12.50"),
            "ordered": Decimal("10"),
            "billed": Decimal("4"),
            "received_accepted": Decimal("6"),
            "open_qty": Decimal("6"),
            "grni_qty": Decimal("2"),
        }
    ]
    _write(path, [], [vendor_id], po_balance=po_balance)

    values = load_workbook(path, data_only=True)
    try:
        sheet = values["PO Balance"]
        assert [c.value for c in sheet[1]] == [
            "PO No.",
            "PO Line",
            "Description",
            "PO Rate",
            "Ordered",
            "Billed",
            "Received (accepted)",
            "Open Qty",
            "GRNI Qty",
            "Open Value",
        ]
        row = next(sheet.iter_rows(min_row=2, values_only=True))
        assert row[:3] == ("PO-1", 1, "Widget")
        assert row[9] == pytest.approx(75.0)
        assert values.sheetnames[-2:] == ["Not included", "PO Balance"]
    finally:
        values.close()
