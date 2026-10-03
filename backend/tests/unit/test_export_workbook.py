"""Read-back checks for the P0 Excel workbook contract."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import reconbench_app.export_service as export_service
from openpyxl import load_workbook
from reconbench_app.export_service import P0_SHEETS, ExportService


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
        "vendor_name": "=2+3",
        "effective_vendor_name": "=2+3",
        "vendor_gstin": "29ABCDE1234F1Z5",
        "invoice_no": invoice_no,
        "invoice_date": date(2026, 10, 1),
        "po_nos": ["0007"],
        "grn_nos": ["G-1"],
        "human_touched": False,
    }


def _context(vendor_ids: list[UUID]) -> dict[str, Any]:
    return {
        "workspace_id": uuid4(),
        "run_id": uuid4(),
        "filters": {"from": "2026-10-01", "to": "2026-10-31"},
        "run": {
            "finished_at": datetime(2026, 10, 2),
            "engine_version": "0.1",
            "rules_hash": "synthetic-rules-hash",
            "config_version_id": str(uuid4()),
            "ai_mode": "rules_only",
            "ai_provider": None,
            "ai_model": None,
            "config_body": {"engine": {"tolerances": {"qty": {"pct": 1}}}},
        },
        "vendor_doc_counts": {
            vendor_id: Counter({"po": 1, "grn": 1, "invoice": 1}) for vendor_id in vendor_ids
        },
        "gstins": {vendor_id: ["29ABCDE1234F1Z5"] for vendor_id in vendor_ids},
    }


def _write(path: Path, cases: list[dict[str, Any]], vendor_ids: list[UUID]) -> None:
    ExportService._write_workbook(
        ExportService.__new__(ExportService),
        path,
        cases=cases,
        lines=[],
        context=_context(vendor_ids),
    )


def test_workbook_readback_checks_formulas_cached_values_and_text_safety(tmp_path: Path) -> None:
    vendor_id = uuid4()
    path = tmp_path / "reconciliation.xlsx"
    _write(path, [_case(vendor_id=vendor_id)], [vendor_id])

    formulas = load_workbook(path, data_only=False)
    values = load_workbook(path, data_only=True)
    try:
        assert tuple(formulas.sheetnames) == P0_SHEETS
        assert formulas["Summary"]["A1"].value == "Metric"
        assert formulas["Invoice Recon"]["C1"].value == "Invoice No."
        assert formulas["Invoice Recon"]["C2"].value == "0142"
        assert formulas["Invoice Recon"]["A2"].value == "=2+3"
        assert formulas["Invoice Recon"]["A2"].data_type == "s"
        assert formulas["Invoice Recon"]["K2"].value == "=I2+J2"
        assert formulas["Invoice Recon"]["M2"].value == "=I2-L2"
        assert formulas["Invoice Recon"]["P2"].value.startswith("=IF(OR(")
        assert formulas["Invoice Recon"].freeze_panes == "A2"
        assert values["Invoice Recon"]["K2"].value == 210
        assert values["Invoice Recon"]["P2"].value == 20
        assert values["Summary"]["B9"].value == 210
        assert values["Summary"]["B10"].value == 180
        assert values["Summary"]["B11"].value == 20
    finally:
        formulas.close()
        values.close()


def test_workbook_splits_rows_and_summary_formulas_cover_each_invoice_sheet(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(export_service, "EXCEL_MAX_ROWS", 2)
    vendor_ids = [uuid4(), uuid4(), uuid4()]
    cases = [
        _case(vendor_id=vendor_id, invoice_no=f"INV-{index}")
        for index, vendor_id in enumerate(vendor_ids)
    ]
    path = tmp_path / "split.xlsx"
    _write(path, cases, vendor_ids)

    formulas = load_workbook(path, data_only=False)
    values = load_workbook(path, data_only=True)
    try:
        for sheet_name in P0_SHEETS:
            assert sheet_name in formulas.sheetnames
        assert [name for name in formulas.sheetnames if name.startswith("Invoice Recon")] == [
            "Invoice Recon",
            "Invoice Recon 2",
            "Invoice Recon 3",
        ]
        assert [name for name in formulas.sheetnames if name.startswith("Exceptions")] == [
            "Exceptions",
            "Exceptions 2",
            "Exceptions 3",
        ]
        assert [name for name in formulas.sheetnames if name.startswith("Vendor Summary")] == [
            "Vendor Summary",
            "Vendor Summary 2",
            "Vendor Summary 3",
        ]
        summary_formula = formulas["Summary"]["B9"].value
        assert "'Invoice Recon'!K2:K2" in summary_formula
        assert "'Invoice Recon 2'!K2:K2" in summary_formula
        assert "'Invoice Recon 3'!K2:K2" in summary_formula
        assert values["Summary"]["B9"].value == 630
        assert values["Summary"]["B10"].value == 540
        assert values["Summary"]["B11"].value == 60
    finally:
        formulas.close()
        values.close()
