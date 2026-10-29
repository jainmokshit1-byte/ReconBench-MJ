"""Revision 0032 (AI field origin, PD-2a): stored, read back, refused when unknown, reversible.

Each test clones the disposable database and works on the clone only: the source database
is never changed and nothing here touches a live database. The document, its version and
its evidence are synthetic.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from reconbench_app.ingestion import IngestionService
from reconbench_app.mapping_service import _field
from reconbench_core.parsers import parse
from reconbench_core.schema import (
    DocType,
    Document,
    ExtractionMethod,
    FieldOrigin,
    FieldSource,
    FieldValue,
)
from sqlalchemy.engine import Engine, make_url

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
_CHECK = (
    "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'field_evidence_origin_ck'"
)
_INVOICE = (
    "<Invoice><InvoiceNo>INV-PD2A-32</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
    "<Supplier>Synthetic Supplier</Supplier><PORef>PO-PD2A-32</PORef>"
    "<TaxableTotal>100</TaxableTotal><InvoiceTotal>100</InvoiceTotal>"
    "<Line><Description>Synthetic part</Description><Qty>1</Qty><Rate>100</Rate>"
    "<Amount>100</Amount></Line></Invoice>"
)


@contextmanager
def _clone() -> Iterator[Engine]:
    source = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not source:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    source_url = make_url(source)
    source_name = source_url.database or ""
    if not source_name.startswith("reconbench_phase2_"):
        raise RuntimeError("migration checks require a disposable reconbench_phase2_* database")
    clone_name = f"reconbench_phase2_m32_{uuid4().hex[:10]}"
    admin = sa.create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{clone_name}" TEMPLATE "{source_name}"')
    clone_url = source_url.set(database=clone_name).render_as_string(hide_password=False)
    engine = sa.create_engine(clone_url)
    previous = os.environ.get("RB_MIGRATOR_DATABASE_URL")
    try:
        with engine.connect() as connection:
            heads = set(MigrationContext.configure(connection).get_current_heads())
        assert heads == {"0035"}, heads
        os.environ["RB_MIGRATOR_DATABASE_URL"] = clone_url
        yield engine
    finally:
        if previous is None:
            os.environ.pop("RB_MIGRATOR_DATABASE_URL", None)
        else:
            os.environ["RB_MIGRATOR_DATABASE_URL"] = previous
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{clone_name}" WITH (FORCE)')
        admin.dispose()


def _config() -> Config:
    return Config(str(ALEMBIC_INI))


def _ai_read_invoice() -> Document:
    parsed = parse(_INVOICE.encode(), {"file_format": "xml", "doc_type": DocType.INVOICE})
    assert parsed.success and len(parsed.documents) == 1
    document = parsed.documents[0]

    def read(value: object) -> object:
        # No origin of its own: the AI-first extraction method decides it.
        return value.model_copy(update={"origin": None}) if isinstance(value, FieldValue) else value

    header = document.header.model_copy(
        update={
            name: read(getattr(document.header, name))
            for name in type(document.header).model_fields
        }
    )
    po_refs = header.po_refs
    assert po_refs is not None
    # Marked as read by AI although it claims an OCR origin.
    header = header.model_copy(
        update={
            "po_refs": po_refs.model_copy(
                update={"source": FieldSource.AI, "origin": FieldOrigin.OCR}
            )
        }
    )
    lines = tuple(
        line.model_copy(
            update={name: read(getattr(line, name)) for name in type(line).model_fields}
        )
        for line in document.lines
    )
    return document.model_copy(update={"header": header, "lines": lines})


def _store_ai_first_version(connection: sa.Connection) -> UUID:
    """One synthetic AI-first version, its evidence written by the ingestion code."""

    workspace_id, file_id, document_id, version_id = uuid4(), uuid4(), uuid4(), uuid4()
    digest = hashlib.sha256(version_id.bytes).hexdigest()
    connection.execute(
        sa.text("INSERT INTO workspace (id, name) VALUES (:id, 'Synthetic PD-2a')"),
        {"id": workspace_id},
    )
    connection.execute(
        sa.text(
            "INSERT INTO source_file (id, workspace_id, sha256, display_name, zone, "
            "zone_uploaded, format, mismatch_state, status, attempt_count, size_bytes, "
            "object_key, docs_found, docs_new, docs_updated, docs_skipped, reupload_skips, "
            "uploaded_at, version) VALUES (:id, :w, :sha, 'synthetic.pdf', 'invoice', "
            "'invoice', 'pdf', 'none', 'parsed', 1, 1024, 'synthetic/key', 1, 1, 0, 0, 0, "
            "now(), 1)"
        ),
        {"id": file_id, "w": workspace_id, "sha": digest},
    )
    connection.execute(
        sa.text(
            "INSERT INTO document (id, workspace_id, doc_type, natural_key, doc_no, "
            "canonical_doc_no, doc_date, grand_total, current_version_id) VALUES "
            "(:id, :w, 'invoice', :key, 'INV-PD2A-32', 'INVPD2A32', :day, 100.00, :v)"
        ),
        {
            "id": document_id,
            "w": workspace_id,
            "key": digest,
            "day": date(2026, 2, 1),
            "v": version_id,
        },
    )
    connection.execute(
        sa.text(
            "INSERT INTO document_version (id, workspace_id, document_id, seq, content_hash, "
            "extraction_method, reading_method, source_file_id, parser_version) VALUES "
            "(:id, :w, :d, 1, :hash, 'ai_first', 'ai', :f, 'synthetic')"
        ),
        {"id": version_id, "w": workspace_id, "d": document_id, "hash": digest, "f": file_id},
    )
    IngestionService._insert_lines_and_evidence(
        connection,
        workspace_id=workspace_id,
        version_id=version_id,
        document=_ai_read_invoice(),
        extraction_method=ExtractionMethod.AI_FIRST,
        uom_aliases={},
        charge_keywords=(),
    )
    return version_id


def _evidence(connection: sa.Connection, version_id: UUID) -> list[sa.RowMapping]:
    return list(
        connection.execute(
            sa.text(
                "SELECT evidence.* FROM field_evidence AS evidence "
                "LEFT JOIN doc_line AS line ON line.id = evidence.doc_line_id "
                "WHERE evidence.document_version_id = :v OR line.document_version_id = :v "
                "ORDER BY evidence.field_name, evidence.id"
            ),
            {"v": version_id},
        ).mappings()
    )


def test_ai_first_values_are_stored_and_read_back_as_origin_ai() -> None:
    with _clone() as engine:
        with engine.begin() as connection:
            version_id = _store_ai_first_version(connection)
        with engine.connect() as connection:
            rows = _evidence(connection, version_id)
            assert "'ai'::text" in str(connection.scalar(sa.text(_CHECK)))

        assert rows
        assert {row["origin"] for row in rows} == {"ai"}
        po_refs = next(row for row in rows if row["field_name"] == "po_refs")
        assert po_refs["source"] == "ai"
        read_back = _field(("PO-PD2A-32",), po_refs)
        assert read_back is not None and read_back.origin is FieldOrigin.AI


def test_an_unknown_origin_is_still_refused() -> None:
    with _clone() as engine:
        with engine.begin() as connection:
            version_id = _store_ai_first_version(connection)
        with engine.connect() as connection:
            row = dict(_evidence(connection, version_id)[0])
            row.update(id=uuid4(), origin="xyz")
            row.pop("created_at")
            columns = ", ".join(row)
            values = ", ".join(f":{name}" for name in row)
            with pytest.raises(sa.exc.IntegrityError, match="field_evidence_origin_ck"):
                connection.execute(
                    sa.text(f"INSERT INTO field_evidence ({columns}) VALUES ({values})"),
                    {**row, "bbox": None},
                )


def test_downgrade_restores_the_previous_check_and_upgrade_reapplies_it() -> None:
    with _clone() as engine:
        command.downgrade(_config(), "0031")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0031"
            previous = str(connection.scalar(sa.text(_CHECK)))
        assert "'erp_import'::text" in previous and "'ai'::text" not in previous

        command.upgrade(_config(), "head")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0035"
            assert "'ai'::text" in str(connection.scalar(sa.text(_CHECK)))


def test_downgrade_is_refused_while_an_ai_value_is_stored() -> None:
    with _clone() as engine:
        with engine.begin() as connection:
            _store_ai_first_version(connection)
        # Revisions 0035 (PD-2c), 0034 (PD-1b) and 0033 (PD-2b) go first, so the refused step
        # is 0032's own.
        command.downgrade(_config(), "0032")
        with pytest.raises(sa.exc.IntegrityError, match="field_evidence_origin_ck"):
            command.downgrade(_config(), "0031")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0032"
            assert "'ai'::text" in str(connection.scalar(sa.text(_CHECK)))
