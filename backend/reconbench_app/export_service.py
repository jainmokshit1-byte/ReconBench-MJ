"""Deterministic, formula-auditable P0 reconciliation workbook generation."""

from __future__ import annotations

import json
import tempfile
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
import xlsxwriter
from sqlalchemy.engine import Connection, Engine, RowMapping

from reconbench_app.authorization import requires
from reconbench_app.db.metadata import metadata
from reconbench_app.storage import ObjectStore

EXCEL_MAX_ROWS = 1_048_576
P0_SHEETS = ("Summary", "Invoice Recon", "Line Recon", "Exceptions", "Vendor Summary")
# How the invoice version in the run was read (PD-2c, Q13).
READING_METHOD_LABELS = {"rules": "Rules", "ai": "AI reading"}
_EXCEPTION_STATUSES = {"exception", "pending", "needs_review", "unlinked"}


def _decimal(value: object | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _excel_number(value: object | None) -> float | None:
    parsed = _decimal(value)
    return float(parsed) if parsed is not None else None


def _plain(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_plain(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _write_text(sheet: Any, row: int, column: int, value: object | None, fmt: Any = None) -> None:
    # write_string treats leading formula characters as text, preventing formula injection.
    sheet.write_string(row, column, _plain(value), fmt)


class ExportService:
    """Build and store one export from its anchor run and persisted filter."""

    def __init__(self, engine: Engine, object_store: ObjectStore) -> None:
        self.engine = engine
        self.object_store = object_store

    @requires("export.create")
    def build_export(self, export_id: UUID) -> None:
        export = metadata.tables["export_file"]
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    sa.select(*export.c).where(export.c.id == export_id).with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if row is None or row["status"] not in {"queued", "failed"}:
                return
            connection.execute(
                sa.update(export)
                .where(export.c.id == export_id)
                .values(
                    status="building",
                    error_code=None,
                    error_message=None,
                    version=export.c.version + 1,
                )
            )
        try:
            cases, lines, context = self._load_rows(
                cast(UUID, row["workspace_id"]),
                cast(UUID, row["run_id"]),
                cast(dict[str, Any], row["filter"]),
            )
            with tempfile.TemporaryDirectory(prefix="reconbench-export-") as directory:
                path = Path(directory) / f"{export_id}.xlsx"
                self._write_workbook(path, cases=cases, lines=lines, context=context)
                size = path.stat().st_size
                key = f"exports/{row['workspace_id']}/{export_id}.xlsx"
                with path.open("rb") as workbook_file:
                    self.object_store.put_fileobj(
                        key,
                        cast(Any, workbook_file),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
            with self.engine.begin() as connection:
                connection.execute(
                    sa.update(export)
                    .where(export.c.id == export_id)
                    .values(
                        status="ready",
                        object_key=key,
                        size_bytes=size,
                        case_count=len(cases),
                        sheets=list(P0_SHEETS),
                        built_at=datetime.now(UTC),
                        expires_at=datetime.now(UTC) + timedelta(days=7),
                        version=export.c.version + 1,
                    )
                )
        except Exception:
            with self.engine.begin() as connection:
                connection.execute(
                    sa.update(export)
                    .where(export.c.id == export_id)
                    .values(
                        status="failed",
                        error_code="EXPORT_BUILD_FAILED",
                        error_message="The workbook could not be built. Retry the export.",
                        built_at=datetime.now(UTC),
                        version=export.c.version + 1,
                    )
                )
            raise

    def _load_rows(
        self, workspace_id: UUID, run_id: UUID, filters: dict[str, Any]
    ) -> tuple[list[RowMapping], list[RowMapping], dict[str, Any]]:
        case = metadata.tables["recon_case"]
        run = metadata.tables["recon_run"]
        version = metadata.tables["document_version"]
        document = metadata.tables["document"]
        current = metadata.tables["case_current"].alias("current_case")
        vendor = metadata.tables["vendor"].alias("vendor")
        root_vendor = metadata.tables["vendor"].alias("root_vendor")
        gstin = metadata.tables["vendor_gstin"]
        active_case = sa.and_(
            current.c.case_id == case.c.id, current.c.workspace_id == case.c.workspace_id
        )
        overlay = sa.func.case_overlay(case.c.invoice_id)
        effective_status = sa.func.coalesce(
            sa.case(
                (active_case, current.c.effective_status),
                else_=sa.cast(overlay, sa.Text()),
            ),
            sa.cast(case.c.status, sa.Text()),
        ).label("effective_status")
        effective_vendor_id = sa.func.coalesce(vendor.c.merged_into, vendor.c.id).label(
            "effective_vendor_id"
        )
        statement = (
            sa.select(
                *case.c,
                version.c.doc_no.label("invoice_no"),
                version.c.doc_date.label("invoice_date"),
                version.c.vendor_name,
                version.c.vendor_gstin,
                version.c.taxable_total,
                version.c.cgst_total,
                version.c.sgst_total,
                version.c.igst_total,
                version.c.round_off,
                version.c.currency,
                version.c.reading_method,
                root_vendor.c.canonical_name.label("effective_vendor_name"),
                effective_vendor_id,
                effective_status,
                sa.exists(
                    sa.select(sa.literal(1)).where(
                        metadata.tables["case_action"].c.workspace_id == workspace_id,
                        metadata.tables["case_action"].c.invoice_id == case.c.invoice_id,
                    )
                ).label("human_touched"),
            )
            .select_from(
                case.join(run, run.c.id == case.c.run_id)
                .join(version, version.c.id == case.c.invoice_version_id)
                .join(document, document.c.id == case.c.invoice_id)
                .outerjoin(current, active_case)
                .outerjoin(vendor, vendor.c.id == case.c.vendor_id)
                .outerjoin(
                    root_vendor,
                    root_vendor.c.id == sa.func.coalesce(vendor.c.merged_into, vendor.c.id),
                )
            )
            .where(case.c.workspace_id == workspace_id, case.c.run_id == run_id)
        )
        if filters.get("vendor_id"):
            statement = statement.where(effective_vendor_id == UUID(str(filters["vendor_id"])))
        # An invoice without a readable date (an unreadable scan) is always
        # included, so a period filter can never hide it from the workbook.
        if filters.get("from"):
            statement = statement.where(
                sa.or_(
                    version.c.doc_date.is_(None),
                    version.c.doc_date >= date.fromisoformat(str(filters["from"])),
                )
            )
        if filters.get("to"):
            statement = statement.where(
                sa.or_(
                    version.c.doc_date.is_(None),
                    version.c.doc_date <= date.fromisoformat(str(filters["to"])),
                )
            )
        if filters.get("status"):
            requested_statuses = filters["status"]
            if isinstance(requested_statuses, list):
                statement = statement.where(effective_status.in_(requested_statuses))
        with self.engine.connect() as connection:
            cases = list(
                connection.execute(
                    statement.order_by(
                        case.c.status,
                        version.c.doc_date,
                        version.c.canonical_doc_no,
                        document.c.natural_key,
                    )
                ).mappings()
            )
            case_ids = [cast(UUID, row["id"]) for row in cases]
            line_table = metadata.tables["recon_line_result"]
            source_line = metadata.tables["doc_line"]
            lines = (
                list(
                    connection.execute(
                        sa.select(
                            *line_table.c,
                            source_line.c.description,
                            source_line.c.item_code,
                            source_line.c.canonical_po_ref,
                        )
                        .select_from(
                            line_table.outerjoin(
                                source_line, source_line.c.id == line_table.c.invoice_line_id
                            )
                        )
                        .where(
                            line_table.c.workspace_id == workspace_id,
                            line_table.c.case_id.in_(case_ids),
                        )
                        .order_by(line_table.c.line_no, line_table.c.case_id)
                    ).mappings()
                )
                if case_ids
                else []
            )
            config_version = metadata.tables["config_version"]
            run_row = (
                connection.execute(
                    sa.select(
                        run.c.run_date,
                        run.c.engine_version,
                        run.c.rules_hash,
                        run.c.config_version_id,
                        run.c.ai_mode,
                        run.c.ai_provider,
                        run.c.ai_model,
                        run.c.finished_at,
                        run.c.status_counts,
                        config_version.c.body.label("config_body"),
                    )
                    .select_from(
                        run.join(config_version, config_version.c.id == run.c.config_version_id)
                    )
                    .where(run.c.workspace_id == workspace_id, run.c.id == run_id)
                )
                .mappings()
                .one()
            )
            vendor_doc_counts = self._vendor_document_counts(connection, workspace_id)
            gstins = {}
            if cases:
                vendor_ids = {
                    cast(UUID, row["vendor_id"]) for row in cases if row["vendor_id"] is not None
                }
                for vendor_id, value in connection.execute(
                    sa.select(gstin.c.vendor_id, gstin.c.gstin)
                    .where(gstin.c.workspace_id == workspace_id, gstin.c.vendor_id.in_(vendor_ids))
                    .order_by(gstin.c.gstin)
                ):
                    gstins.setdefault(cast(UUID, vendor_id), []).append(str(value))
        context = {
            "workspace_id": workspace_id,
            "run_id": run_id,
            "run": dict(run_row),
            "filters": filters,
            "vendor_doc_counts": vendor_doc_counts,
            "gstins": gstins,
        }
        return cases, lines, context

    @staticmethod
    def _vendor_document_counts(
        connection: Connection, workspace_id: UUID
    ) -> dict[UUID, Counter[str]]:
        document = metadata.tables["document"]
        vendor = metadata.tables["vendor"]
        counts: dict[UUID, Counter[str]] = defaultdict(Counter)
        rows = connection.execute(
            sa.select(
                sa.func.coalesce(vendor.c.merged_into, vendor.c.id).label("effective_vendor_id"),
                document.c.doc_type,
            )
            .select_from(document.outerjoin(vendor, vendor.c.id == document.c.vendor_id))
            .where(
                document.c.workspace_id == workspace_id,
                document.c.withdrawn_at.is_(None),
                document.c.vendor_id.is_not(None),
            )
        )
        for vendor_id, doc_type in rows:
            counts[cast(UUID, vendor_id)][str(doc_type)] += 1
        return counts

    def _write_workbook(
        self,
        path: Path,
        *,
        cases: list[RowMapping],
        lines: list[RowMapping],
        context: dict[str, Any],
    ) -> None:
        workbook = xlsxwriter.Workbook(
            str(path),
            {"constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False},
        )
        workbook.set_properties(
            {
                "title": "ReconBench reconciliation export",
                "author": "ReconBench",
                "comments": "Generated from an immutable reconciliation run.",
            }
        )
        header_fmt = workbook.add_format(
            {"bold": True, "bg_color": "#EAF0F7", "border": 1, "text_wrap": True}
        )
        money_fmt = workbook.add_format({"num_format": "₹#,##0.00;[Red]-₹#,##0.00"})
        qty_fmt = workbook.add_format({"num_format": "#,##0.####"})
        pct_fmt = workbook.add_format({"num_format": '0.00"%"'})
        date_fmt = workbook.add_format({"num_format": "dd-mmm-yyyy"})
        text_fmt = workbook.add_format({"quote_prefix": True})
        summary = workbook.add_worksheet("Summary")
        summary.set_column(0, 0, 34)
        summary.set_column(1, 1, 28)
        summary.freeze_panes(1, 0)
        summary.write_row(0, 0, ["Metric", "Value"], header_fmt)

        invoice_headers = [
            "Vendor",
            "GSTIN",
            "Invoice No.",
            "Invoice Date",
            "PO(s)",
            "GRN(s)",
            "Link Method",
            "Link Confidence",
            "Taxable",
            "Tax",
            "Total",
            "Expected @ PO Rate",
            "Variance ₹",
            "Variance %",
            "Payable",
            "At-risk",
            "Status",
            "Reason Codes",
            "Override Info",
            "Payment Due Date",
            "Reading Method",
        ]
        invoice_sheets: list[tuple[Any, str, int]] = []
        invoice_row_info: list[tuple[RowMapping, str, int]] = []
        sheets_invoice = self._new_sheet(workbook, "Invoice Recon", invoice_headers, header_fmt)
        invoice_sheet_name = "Invoice Recon"
        sheet_index = 0
        row_index = 1
        for record in cases:
            if row_index >= EXCEL_MAX_ROWS:
                sheets_invoice.autofilter(0, 0, row_index - 1, len(invoice_headers) - 1)
                invoice_sheets.append((sheets_invoice, invoice_sheet_name, row_index - 1))
                sheet_index += 1
                invoice_sheet_name = f"Invoice Recon {sheet_index + 1}"
                sheets_invoice = self._new_sheet(
                    workbook, invoice_sheet_name, invoice_headers, header_fmt
                )
                row_index = 1
            row_no = row_index + 1
            current_sheet_name = invoice_sheet_name
            invoice_row_info.append((record, current_sheet_name, row_no))
            vendor_id = cast(UUID | None, record["effective_vendor_id"])
            gstins = context["gstins"].get(vendor_id, []) if vendor_id else []
            links = record["link_summary"] if isinstance(record["link_summary"], list) else []
            link_methods = [str(item.get("method", "")) for item in links if isinstance(item, dict)]
            confidences = [
                _decimal(item.get("confidence")) for item in links if isinstance(item, dict)
            ]
            confidence = max((value for value in confidences if value is not None), default=None)
            taxable = _excel_number(record["invoice_taxable"])
            tax_parts = [
                _excel_number(record[name])
                for name in ("cgst_total", "sgst_total", "igst_total", "round_off")
            ]
            tax = sum(value or 0 for value in tax_parts)
            total = _excel_number(record["invoice_total"])
            expected = _excel_number(record["expected_taxable"])
            variance = _excel_number(record["variance"])
            variance_pct = _excel_number(record["variance_pct"])
            payable = _excel_number(record["payable_taxable"])
            risk = _excel_number(record["at_risk"])
            values: list[object | None] = [
                record["effective_vendor_name"] or record["vendor_name"],
                ", ".join(gstins) if gstins else record["vendor_gstin"],
                record["invoice_no"],
                record["invoice_date"],
                record["po_nos"],
                record["grn_nos"],
                ", ".join(link_methods),
                confidence,
                taxable,
                tax,
            ]
            for column, value in enumerate(values):
                self._write_cell(
                    sheets_invoice,
                    row_index,
                    column,
                    value,
                    money_fmt
                    if column in {8, 9}
                    else date_fmt
                    if column == 3
                    else pct_fmt
                    if column == 7
                    else text_fmt
                    if column < 7
                    else None,
                )
            sheets_invoice.write_formula(
                row_index, 10, f"=I{row_no}+J{row_no}", money_fmt, total or 0
            )
            self._write_cell(sheets_invoice, row_index, 11, expected, money_fmt)
            sheets_invoice.write_formula(
                row_index, 12, f"=I{row_no}-L{row_no}", money_fmt, variance or 0
            )
            sheets_invoice.write_formula(
                row_index,
                13,
                f"=IF(L{row_no}=0,0,ABS(M{row_no})/ABS(L{row_no})*100)",
                pct_fmt,
                variance_pct or 0,
            )
            self._write_cell(sheets_invoice, row_index, 14, payable, money_fmt)
            sheets_invoice.write_formula(
                row_index,
                15,
                f'=IF(OR(Q{row_no}="duplicate",Q{row_no}="unlinked",Q{row_no}="rejected"),0,I{row_no}-O{row_no})',
                money_fmt,
                risk or 0,
            )
            self._write_cell(sheets_invoice, row_index, 16, record["effective_status"], text_fmt)
            self._write_cell(sheets_invoice, row_index, 17, record["reason_codes"], text_fmt)
            overlay = (
                record["effective_status"] if record["effective_status"] != record["status"] else ""
            )
            self._write_cell(sheets_invoice, row_index, 18, overlay, text_fmt)
            self._write_cell(sheets_invoice, row_index, 19, record["payment_due_date"], date_fmt)
            self._write_cell(
                sheets_invoice,
                row_index,
                20,
                READING_METHOD_LABELS.get(str(record["reading_method"]), record["reading_method"]),
                text_fmt,
            )
            row_index += 1
        sheets_invoice.autofilter(0, 0, max(0, row_index - 1), len(invoice_headers) - 1)
        invoice_sheets.append((sheets_invoice, invoice_sheet_name, row_index - 1))
        for invoice_sheet, _name, last_row in invoice_sheets:
            if last_row >= 1:
                invoice_sheet.conditional_format(
                    1,
                    16,
                    last_row,
                    16,
                    {
                        "type": "text",
                        "criteria": "containing",
                        "value": "exception",
                        "format": workbook.add_format(
                            {"bg_color": "#FDE2E2", "font_color": "#8A1C1C"}
                        ),
                    },
                )
                invoice_sheet.conditional_format(
                    1,
                    16,
                    last_row,
                    16,
                    {
                        "type": "text",
                        "criteria": "containing",
                        "value": "matched",
                        "format": workbook.add_format(
                            {"bg_color": "#DCFCE7", "font_color": "#166534"}
                        ),
                    },
                )

        line_headers = [
            "Vendor",
            "Invoice No.",
            "Invoice Date",
            "Item",
            "PO No.",
            "PO Line",
            "GRN(s)",
            "UOM",
            "PO Qty",
            "Billed Before",
            "This Qty",
            "GRN Accepted (cum)",
            "Balance After",
            "PO Rate",
            "Invoice Rate",
            "Rate Var %",
            "Qty / Rate Checks",
            "Result",
        ]
        line_by_case: dict[UUID, list[RowMapping]] = defaultdict(list)
        for item in lines:
            line_by_case[cast(UUID, item["case_id"])].append(item)
        line_sheet = self._new_sheet(workbook, "Line Recon", line_headers, header_fmt)
        line_sheet_name = "Line Recon"
        line_sheet_index = 0
        line_row_index = 1
        for record in cases:
            for line in line_by_case.get(cast(UUID, record["id"]), []):
                if line_row_index >= EXCEL_MAX_ROWS:
                    line_sheet.autofilter(0, 0, line_row_index - 1, len(line_headers) - 1)
                    line_sheet_index += 1
                    line_sheet_name = f"Line Recon {line_sheet_index + 1}"
                    line_sheet = self._new_sheet(
                        workbook, line_sheet_name, line_headers, header_fmt
                    )
                    line_row_index = 1
                checks_text = (
                    "; ".join(
                        f"{item.get('check_id', '')}: {item.get('result', '')}"
                        for item in line["checks"]
                        if isinstance(item, dict)
                    )
                    if isinstance(line["checks"], list)
                    else ""
                )
                line_values = [
                    record["effective_vendor_name"] or record["vendor_name"],
                    record["invoice_no"],
                    record["invoice_date"],
                    line["description"],
                    ", ".join(record["po_nos"] or []),
                    line["po_line_no"],
                    ", ".join(record["grn_nos"] or []),
                    line["uom"],
                    line["po_qty"],
                    line["billed_before"],
                    line["billed_qty"],
                    line["grn_accepted_cum"],
                    line["balance_after"],
                    line["po_rate"],
                    line["invoice_net_rate"],
                    line["rate_var_pct"],
                    checks_text,
                    line["status"],
                ]
                for column, value in enumerate(line_values):
                    self._write_cell(
                        line_sheet,
                        line_row_index,
                        column,
                        value,
                        qty_fmt
                        if column in {8, 9, 10, 11, 12}
                        else money_fmt
                        if column in {13, 14}
                        else pct_fmt
                        if column == 15
                        else date_fmt
                        if column == 2
                        else text_fmt,
                    )
                line_row_index += 1
        line_sheet.autofilter(0, 0, max(0, line_row_index - 1), len(line_headers) - 1)

        exception_headers = [
            "Vendor",
            "Invoice No.",
            "Invoice Date",
            "PO(s)",
            "Amount",
            "Status",
            "Reason Codes",
            "Suggested Action",
        ]
        exception_sheet = self._new_sheet(workbook, "Exceptions", exception_headers, header_fmt)
        exception_sheet_index = 0
        exception_row = 1
        for record, _sheet, _row in invoice_row_info:
            status = str(record["effective_status"])
            if status not in _EXCEPTION_STATUSES:
                continue
            action = {
                "unlinked": "Review candidates and assign or confirm a PO link.",
                "pending": "Wait for the expected GRN or investigate a missing receipt.",
                "needs_review": "Inspect extraction and matching evidence; keep changes auditable.",
                "exception": "Review the failed financial or compliance checks before approval.",
            }[status]
            if exception_row >= EXCEL_MAX_ROWS:
                exception_sheet.autofilter(0, 0, exception_row - 1, len(exception_headers) - 1)
                exception_sheet_index += 1
                exception_sheet = self._new_sheet(
                    workbook,
                    f"Exceptions {exception_sheet_index + 1}",
                    exception_headers,
                    header_fmt,
                )
                exception_row = 1
            self._write_cell(
                exception_sheet,
                exception_row,
                0,
                record["effective_vendor_name"] or record["vendor_name"],
                text_fmt,
            )
            self._write_cell(exception_sheet, exception_row, 1, record["invoice_no"], text_fmt)
            self._write_cell(exception_sheet, exception_row, 2, record["invoice_date"], date_fmt)
            self._write_cell(exception_sheet, exception_row, 3, record["po_nos"], text_fmt)
            self._write_cell(
                exception_sheet, exception_row, 4, _excel_number(record["invoice_total"]), money_fmt
            )
            self._write_cell(exception_sheet, exception_row, 5, status, text_fmt)
            self._write_cell(exception_sheet, exception_row, 6, record["reason_codes"], text_fmt)
            self._write_cell(exception_sheet, exception_row, 7, action, text_fmt)
            exception_row += 1
        exception_sheet.autofilter(0, 0, max(0, exception_row - 1), len(exception_headers) - 1)

        vendor_headers = [
            "Vendor",
            "GSTIN(s)",
            "# PO",
            "# GRN",
            "# Invoice",
            "Matched",
            "Pending",
            "Exceptions",
            "Unlinked",
            "₹ Invoiced",
            "₹ At-risk",
        ]
        vendor_sheet = self._new_sheet(workbook, "Vendor Summary", vendor_headers, header_fmt)
        groups: dict[UUID | str, dict[str, Any]] = {}
        for record in cases:
            key: UUID | str = (
                cast(UUID, record["effective_vendor_id"])
                if record["effective_vendor_id"]
                else "unassigned"
            )
            group = groups.setdefault(
                key,
                {
                    "name": record["effective_vendor_name"]
                    or record["vendor_name"]
                    or "Unassigned",
                    "gstins": [],
                    "statuses": Counter(),
                    "invoice": Decimal(0),
                    "risk": Decimal(0),
                },
            )
            group["statuses"][str(record["effective_status"])] += 1
            group["invoice"] += _decimal(record["invoice_total"]) or Decimal(0)
            group["risk"] += _decimal(record["at_risk"]) or Decimal(0)
            if record["vendor_gstin"] and str(record["vendor_gstin"]) not in group["gstins"]:
                group["gstins"].append(str(record["vendor_gstin"]))
            if record["effective_vendor_id"]:
                group["gstins"] = sorted(
                    set(
                        group["gstins"]
                        + context["gstins"].get(cast(UUID, record["effective_vendor_id"]), [])
                    )
                )
        vendor_sheet_index = 0
        vendor_row_index = 1
        for vendor_id, group in sorted(
            groups.items(), key=lambda pair: str(pair[1]["name"]).casefold()
        ):
            if vendor_row_index >= EXCEL_MAX_ROWS:
                vendor_sheet.autofilter(0, 0, vendor_row_index - 1, len(vendor_headers) - 1)
                vendor_sheet_index += 1
                vendor_sheet = self._new_sheet(
                    workbook,
                    f"Vendor Summary {vendor_sheet_index + 1}",
                    vendor_headers,
                    header_fmt,
                )
                vendor_row_index = 1
            doc_counts = (
                context["vendor_doc_counts"].get(vendor_id, Counter())
                if isinstance(vendor_id, UUID)
                else Counter()
            )
            values = [
                group["name"],
                group["gstins"],
                doc_counts.get("po", 0),
                doc_counts.get("grn", 0),
                group["statuses"].total(),
                group["statuses"].get("matched", 0) + group["statuses"].get("matched_flags", 0),
                group["statuses"].get("pending", 0),
                group["statuses"].get("exception", 0) + group["statuses"].get("needs_review", 0),
                group["statuses"].get("unlinked", 0),
                float(group["invoice"]),
                float(group["risk"]),
            ]
            for column, value in enumerate(values):
                self._write_cell(
                    vendor_sheet,
                    vendor_row_index,
                    column,
                    value,
                    money_fmt if column in {9, 10} else text_fmt if column < 2 else None,
                )
            vendor_row_index += 1
        vendor_sheet.autofilter(0, 0, max(0, vendor_row_index - 1), len(vendor_headers) - 1)

        statuses = Counter(str(record["effective_status"]) for record in cases)
        run = context["run"]
        tolerance_settings = ""
        config_body = run.get("config_body")
        if isinstance(config_body, dict):
            engine_config = config_body.get("engine")
            if isinstance(engine_config, dict):
                tolerance_settings = json.dumps(
                    engine_config.get("tolerances", {}), sort_keys=True, ensure_ascii=False
                )
        summary_rows: list[tuple[str, object]] = [
            ("Batch / scope", f"Run {context['run_id']} · {_plain(context['filters'])}"),
            ("Run time", run.get("finished_at") or ""),
            ("Invoices", len(cases)),
            ("Matched", statuses.get("matched", 0) + statuses.get("matched_flags", 0)),
            ("Pending", statuses.get("pending", 0)),
            # Same split as v_run_summary and the Home KPI cards: Needs Review is not an Exception.
            ("Exceptions", statuses.get("exception", 0)),
            ("Unlinked", statuses.get("unlinked", 0)),
            ("Needs Review", statuses.get("needs_review", 0)),
            ("Duplicate", statuses.get("duplicate", 0)),
            ("₹ Invoiced", None),
            ("₹ Payable", None),
            ("₹ At-risk", None),
            ("Engine version", run.get("engine_version")),
            ("Rules hash", run.get("rules_hash")),
            ("Configuration version", run.get("config_version_id")),
            ("AI on/off", run.get("ai_mode", "rules_only")),
            (
                "AI provider / model",
                f"{run.get('ai_provider') or ''} {run.get('ai_model') or ''}".strip(),
            ),
            ("Tolerances used", tolerance_settings),
        ]
        formula_by_metric = {
            "₹ Invoiced": "K",
            "₹ Payable": "O",
            "₹ At-risk": "P",
        }
        for row_index, (metric, value) in enumerate(summary_rows, start=1):
            _write_text(summary, row_index, 0, metric, text_fmt)
            if metric in formula_by_metric:
                column = formula_by_metric[metric]
                ranges = [
                    f"'{name}'!{column}2:{column}{max(last_row + 1, 2)}"
                    for _sheet, name, last_row in invoice_sheets
                ]
                cached_column = {"K": "invoice_total", "O": "payable_taxable", "P": "at_risk"}[
                    column
                ]
                cached = float(
                    sum((_decimal(item[cached_column]) or Decimal(0) for item in cases), Decimal(0))
                )
                summary.write_formula(
                    row_index,
                    1,
                    f"=SUM({','.join(ranges)})" if ranges else "=0",
                    money_fmt,
                    cached,
                )
            elif isinstance(value, int | float):
                summary.write_number(row_index, 1, value)
            else:
                _write_text(summary, row_index, 1, value, text_fmt)
        summary.write(1 + len(summary_rows), 0, "P0 sheets", header_fmt)
        summary.write_string(2 + len(summary_rows), 0, ", ".join(P0_SHEETS), text_fmt)
        workbook.close()

    @staticmethod
    def _new_sheet(workbook: Any, name: str, headers: list[str], header_fmt: Any) -> Any:
        worksheet = workbook.add_worksheet(name[:31])
        worksheet.freeze_panes(1, 0)
        worksheet.write_row(0, 0, headers, header_fmt)
        worksheet.set_row(0, 34)
        worksheet.set_column(0, len(headers) - 1, 16)
        return worksheet

    @staticmethod
    def _write_cell(
        sheet: Any, row: int, column: int, value: object | None, fmt: Any = None
    ) -> None:
        if value is None:
            sheet.write_blank(row, column, None, fmt)
        elif isinstance(value, bool):
            sheet.write_boolean(row, column, value, fmt)
        elif isinstance(value, int | float):
            sheet.write_number(row, column, value, fmt)
        elif isinstance(value, Decimal):
            sheet.write_number(row, column, float(value), fmt)
        else:
            _write_text(sheet, row, column, value, fmt)


__all__ = ["ExportService", "P0_SHEETS"]
