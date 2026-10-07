"""Home KPI period rule (revision 0018); requires a disposable PostgreSQL 16 database.

An invoice without a readable date is always counted, as in the export workbook
(docs/decision-log.md D19 #5), so Home KPIs equal the Excel Summary for a period.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
import sqlalchemy as sa
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from sqlalchemy.engine import Engine, make_url

_ZERO_HASH = "0" * 64


@pytest.fixture(scope="module")
def kpi_engine() -> Engine:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    database = make_url(dsn).database
    if not database or not database.startswith("reconbench_phase2_"):
        raise RuntimeError(
            "database checks require a disposable database named reconbench_phase2_*"
        )
    engine = sa.create_engine(dsn)
    yield engine
    engine.dispose()


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _add_case(
    connection: sa.Connection,
    workspace_id: UUID,
    run_id: UUID,
    invoice_date: date | None,
    status: str,
    at_risk: Decimal,
) -> None:
    invoice_id = new_id()
    connection.execute(
        metadata.tables["document"]
        .insert()
        .values(
            id=invoice_id,
            workspace_id=workspace_id,
            doc_type="invoice",
            natural_key=_digest(invoice_id),
        )
    )
    source_file_id = new_id()
    connection.execute(
        metadata.tables["source_file"]
        .insert()
        .values(
            id=source_file_id,
            workspace_id=workspace_id,
            sha256=_digest(source_file_id),
            display_name="synthetic-invoice.pdf",
            zone="invoice",
            zone_uploaded="invoice",
            format="pdf",
            size_bytes=8,
            object_key=f"synthetic/{source_file_id}",
        )
    )
    version_id = new_id()
    connection.execute(
        metadata.tables["document_version"]
        .insert()
        .values(
            id=version_id,
            workspace_id=workspace_id,
            document_id=invoice_id,
            seq=1,
            content_hash=_digest(version_id),
            extraction_method="pdf_text",
            source_file_id=source_file_id,
            parser_version="synthetic-test",
            doc_date=invoice_date,
        )
    )
    case_id = new_id()
    connection.execute(
        metadata.tables["recon_case"]
        .insert()
        .values(
            id=case_id,
            workspace_id=workspace_id,
            run_id=run_id,
            invoice_id=invoice_id,
            invoice_version_id=version_id,
            status=status,
            inputs={},
            input_fingerprint=_digest(case_id),
            at_risk=at_risk,
        )
    )
    connection.execute(
        metadata.tables["case_current"]
        .insert()
        .values(
            id=new_id(),
            workspace_id=workspace_id,
            invoice_id=invoice_id,
            case_id=case_id,
            run_id=run_id,
            invoice_date=invoice_date,
            computed_status=status,
            effective_status=status,
            severity_rank=7 if status == "matched" else 2,
        )
    )


def test_kpi_summary_counts_undated_invoices_in_every_period(kpi_engine: Engine) -> None:
    with kpi_engine.connect() as connection:
        transaction = connection.begin()
        try:
            workspace_id = new_id()
            connection.execute(
                metadata.tables["workspace"]
                .insert()
                .values(id=workspace_id, name="Synthetic KPI period test")
            )
            config_id = new_id()
            connection.execute(
                metadata.tables["config_version"]
                .insert()
                .values(
                    id=config_id,
                    workspace_id=workspace_id,
                    seq=1,
                    body={"engine": {}, "app": {}},
                    engine_hash=_ZERO_HASH,
                    body_hash=_ZERO_HASH,
                )
            )
            run_id = new_id()
            connection.execute(
                metadata.tables["recon_run"]
                .insert()
                .values(
                    id=run_id,
                    workspace_id=workspace_id,
                    scope={},
                    scope_hash=_digest("synthetic-kpi-scope"),
                    run_date=date(2026, 4, 30),
                    engine_version="synthetic-test",
                    rules_hash=_ZERO_HASH,
                    config_version_id=config_id,
                )
            )
            _add_case(connection, workspace_id, run_id, date(2026, 4, 10), "matched", Decimal("0"))
            # An unreadable scan: no date, needs review, money at risk.
            _add_case(connection, workspace_id, run_id, None, "needs_review", Decimal("125.50"))
            _add_case(connection, workspace_id, run_id, date(2026, 5, 2), "matched", Decimal("0"))

            def kpis(from_date: date, to_date: date) -> sa.RowMapping:
                return (
                    connection.execute(
                        sa.text("SELECT * FROM kpi_summary(:workspace_id, :from_date, :to_date)"),
                        {"workspace_id": workspace_id, "from_date": from_date, "to_date": to_date},
                    )
                    .mappings()
                    .one()
                )

            april = kpis(date(2026, 4, 1), date(2026, 4, 30))
            assert april["invoice_count"] == 2
            assert april["matched_pct"] == Decimal("50.00")
            assert april["at_risk_total"] == Decimal("125.50")

            may = kpis(date(2026, 5, 1), date(2026, 5, 31))
            assert may["invoice_count"] == 2
            assert may["at_risk_total"] == Decimal("125.50")

            # The same rule as the export filter: dated invoices outside the period stay out.
            june = kpis(date(2026, 6, 1), date(2026, 6, 30))
            assert june["invoice_count"] == 1
            assert june["matched_pct"] == Decimal("0.00")
        finally:
            transaction.rollback()
