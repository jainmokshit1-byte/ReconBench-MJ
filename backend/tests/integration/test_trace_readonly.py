"""Integration tests for reconbench_tools read-only enforcement on disposable PostgreSQL.

Safety: Never points to port 15432 (live).
Requires: Disposable PG running on port 15499 (or RB_PHASE4_TEST_DATABASE_URL).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from reconbench_tools.trace import create_trace_engine, handle_doc, handle_run
from reconbench_tools.trace_queries import (
    find_document,
    get_ai_reading,
    get_doc_lines,
    get_document_links,
    get_document_version,
    get_field_evidence,
    get_po_ledger,
    get_queue_counts,
    get_recent_follow_events,
    get_recon_case_and_lines,
    get_recon_run_summary,
    resolve_workspace,
)

DISPOSABLE_DB_URL = os.environ.get(
    "RB_DISPOSABLE_TEST_DB_URL",
    "postgresql+psycopg://postgres:phase2-ephemeral-local-only@127.0.0.1:15499/postgres",
)


@pytest.fixture(scope="module")
def disposable_engine() -> Iterator[Engine]:
    """Provide a privileged connection to the disposable test database on 15499."""
    # Safety invariant: NEVER run against port 15432
    assert "15432" not in DISPOSABLE_DB_URL, "Refusing: must never run integration tests on live port 15432"

    old_env = os.environ.get("RECONBENCH_TRACE_DB_URL")
    os.environ["RECONBENCH_TRACE_DB_URL"] = DISPOSABLE_DB_URL

    engine = sa.create_engine(DISPOSABLE_DB_URL, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception as exc:
        if old_env is not None:
            os.environ["RECONBENCH_TRACE_DB_URL"] = old_env
        else:
            os.environ.pop("RECONBENCH_TRACE_DB_URL", None)
        pytest.skip(f"Disposable PG at {DISPOSABLE_DB_URL} not accessible: {exc}")

    yield engine
    engine.dispose()
    if old_env is not None:
        os.environ["RECONBENCH_TRACE_DB_URL"] = old_env
    else:
        os.environ.pop("RECONBENCH_TRACE_DB_URL", None)


def test_refuses_when_app_database_url_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify trace tool exits code 2 with refusal if no app database URL or credentials exist."""
    monkeypatch.delenv("RECONBENCH_TRACE_DB_URL", raising=False)
    monkeypatch.delenv("RB_DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_USER", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)
    monkeypatch.setattr("reconbench_tools.trace._load_env_file", lambda: {})

    with pytest.raises(SystemExit) as exc_info:
        create_trace_engine()
    assert exc_info.value.code == 2


def test_readonly_enforcement_rejects_all_writes(disposable_engine: Engine) -> None:
    """Requirement 5(a): a write through the tool's session is strictly rejected."""
    trace_engine = create_trace_engine()
    try:
        # 1. Check SHOW default_transaction_read_only is 'on'
        with trace_engine.connect() as conn:
            val = conn.execute(sa.text("SHOW default_transaction_read_only")).scalar()
            assert str(val).strip().lower() == "on"

        # 2. Attempt INSERT
        with trace_engine.connect() as conn:
            with pytest.raises(sa.exc.DBAPIError) as exc_info:
                conn.execute(
                    sa.text("INSERT INTO workspace (id, name) VALUES (:id, 'Illegal')"),
                    {"id": uuid4()},
                )
            assert "read-only" in str(exc_info.value).lower()

        # 3. Attempt UPDATE
        with trace_engine.connect() as conn:
            with pytest.raises(sa.exc.DBAPIError) as exc_info:
                conn.execute(sa.text("UPDATE workspace SET name = 'Hacked'"))
            assert "read-only" in str(exc_info.value).lower()

        # 4. Attempt DELETE
        with trace_engine.connect() as conn:
            with pytest.raises(sa.exc.DBAPIError) as exc_info:
                conn.execute(sa.text("DELETE FROM reason_code"))
            assert "read-only" in str(exc_info.value).lower()

        # 5. Attempt DDL (CREATE TABLE)
        with trace_engine.connect() as conn:
            with pytest.raises(sa.exc.DBAPIError) as exc_info:
                conn.execute(sa.text("CREATE TABLE illegal_test_table (x int)"))
            assert "read-only" in str(exc_info.value).lower()
    finally:
        trace_engine.dispose()


def test_queries_workspace_scoping_and_execution(disposable_engine: Engine) -> None:
    """Requirement 5(b): all queries run cleanly and strictly enforce workspace scoping."""
    import hashlib

    # 1. Setup sample data in disposable DB using privileged connection
    ws_a = uuid4()
    ws_b = uuid4()
    doc_id_a = uuid4()
    doc_id_b = uuid4()
    version_id_a = uuid4()

    hash_a = hashlib.sha256(b"doc_a").hexdigest()
    hash_b = hashlib.sha256(b"doc_b").hexdigest()

    with disposable_engine.begin() as priv_conn:
        # Create 2 workspaces
        priv_conn.execute(
            sa.text("INSERT INTO workspace (id, name) VALUES (:id, :name) ON CONFLICT DO NOTHING"),
            [{"id": ws_a, "name": "Workspace Alpha"}, {"id": ws_b, "name": "Workspace Beta"}],
        )

        # Create vendor in ws_a
        vendor_id = uuid4()
        priv_conn.execute(
            sa.text(
                """
                INSERT INTO vendor (id, workspace_id, canonical_name, normalized_name, anchor, created_by_rule)
                VALUES (:vid, :wid, 'Acme Test Corp', 'ACME TEST CORP', 'gstin', 'gstin')
                """
            ),
            {"vid": vendor_id, "wid": ws_a},
        )

        # Create source_file in ws_a
        source_file_id = uuid4()
        priv_conn.execute(
            sa.text(
                """
                INSERT INTO source_file (id, workspace_id, sha256, display_name, zone, zone_uploaded,
                                         format, mismatch_state, status, attempt_count, size_bytes,
                                         object_key, docs_found, docs_new, docs_updated, docs_skipped,
                                         reupload_skips, uploaded_at, version)
                VALUES (:sfid, :wid, :sha, 'sample_invoice.pdf', 'invoice', 'invoice',
                        'pdf', 'none', 'parsed', 1, 1024, 'test/key', 1, 1, 0, 0, 0, now(), 1)
                """
            ),
            {"sfid": source_file_id, "wid": ws_a, "sha": hash_a},
        )

        # Create document in ws_a
        priv_conn.execute(
            sa.text(
                """
                INSERT INTO document (id, workspace_id, doc_type, natural_key, doc_no, canonical_doc_no,
                                      doc_date, grand_total, vendor_id, vendor_rule, current_version_id)
                VALUES (:did, :wid, 'invoice', :nkey, 'INV-ALPHA-001', 'INVALPHA001',
                        :dt, 5000.00, :vid, 'gstin', :cvid)
                """
            ),
            {"did": doc_id_a, "wid": ws_a, "nkey": hash_a, "dt": date(2026, 10, 22), "vid": vendor_id, "cvid": version_id_a},
        )

        # Create document in ws_b with same invoice number
        priv_conn.execute(
            sa.text(
                """
                INSERT INTO document (id, workspace_id, doc_type, natural_key, doc_no, canonical_doc_no,
                                      doc_date, grand_total)
                VALUES (:did, :wid, 'invoice', :nkey, 'INV-ALPHA-001', 'INVALPHA001',
                        :dt, 9999.00)
                """
            ),
            {"did": doc_id_b, "wid": ws_b, "nkey": hash_b, "dt": date(2026, 10, 22)},
        )

        # Create document_version in ws_a
        priv_conn.execute(
            sa.text(
                """
                INSERT INTO document_version (id, workspace_id, document_id, seq, revision_rank, content_hash, doc_no,
                                              doc_date, currency, grand_total, taxable_total, vendor_gstin, extraction_method,
                                              needs_verification, verification_reasons, po_refs, canonical_po_refs,
                                              grn_refs, canonical_grn_refs, extras, source_file_id, parser_version)
                VALUES (:vid, :wid, :did, 1, 0, :chash, 'INV-ALPHA-001',
                        :dt, 'INR', 5000.00, 4500.00, '27AABCU9603R1ZM', 'pdf_ocr',
                        false, '{}', '{}', '{}', '{}', '{}', '{}', :sfid, 'v1')
                """
            ),
            {"vid": version_id_a, "wid": ws_a, "did": doc_id_a, "chash": hash_a, "dt": date(2026, 10, 22), "sfid": source_file_id},
        )

        # Create doc_line in ws_a
        priv_conn.execute(
            sa.text(
                """
                INSERT INTO doc_line (id, workspace_id, document_version_id, line_no, line_type, description, quantity, rate, amount,
                                      tokens, spec_tokens, extras)
                VALUES (:lid, :wid, :vid, 1, 'goods', 'Widget A', 5, 900.00, 4500.00, '{}', '{}', '{}')
                """
            ),
            {"lid": uuid4(), "wid": ws_a, "vid": version_id_a},
        )

    # 2. Test trace tool queries through read-only engine
    trace_engine = create_trace_engine()
    try:
        with trace_engine.connect() as conn:
            # Workspace resolution with explicit ID
            wid_res, name_res = resolve_workspace(conn, ws_a)
            assert wid_res == ws_a
            assert name_res == "Workspace Alpha"

            # Scoping check on find_document:
            # Querying ws_a finds doc_id_a (grand_total 5000.00)
            doc_a = find_document(conn, ws_a, "INV-ALPHA-001")
            assert doc_a is not None
            assert doc_a["id"] == doc_id_a
            assert doc_a["grand_total"] == Decimal("5000.00")
            assert doc_a["vendor_name"] == "Acme Test Corp"

            # Querying ws_b finds doc_id_b (grand_total 9999.00), NEVER doc_id_a
            doc_b = find_document(conn, ws_b, "INV-ALPHA-001")
            assert doc_b is not None
            assert doc_b["id"] == doc_id_b
            assert doc_b["grand_total"] == Decimal("9999.00")

            # Document version details
            ver = get_document_version(conn, ws_a, version_id_a)
            assert ver is not None
            assert ver["taxable_total"] == Decimal("4500.00")

            # Scoping: query version with wrong workspace returns None
            ver_wrong_ws = get_document_version(conn, ws_b, version_id_a)
            assert ver_wrong_ws is None

            # Doc lines
            lines = get_doc_lines(conn, ws_a, version_id_a)
            assert len(lines) == 1
            assert lines[0]["description"] == "Widget A"

            # Queue counts
            q_counts = get_queue_counts(conn)
            assert isinstance(q_counts, dict)
            assert "ingest" in q_counts
            assert "parse" in q_counts

            # Recent events
            evs = get_recent_follow_events(conn, ws_a, since=datetime(2026, 1, 1, tzinfo=UTC))
            assert isinstance(evs, list)
    finally:
        trace_engine.dispose()


def test_cli_handlers_on_disposable_db(disposable_engine: Engine) -> None:
    """Test CLI handle_doc and handle_run against disposable database."""
    trace_engine = create_trace_engine()
    try:
        with disposable_engine.connect() as conn:
            ws_id = conn.execute(sa.text("SELECT id FROM workspace WHERE name = 'Workspace Alpha'")).scalar()
            assert ws_id is not None

        # Existing doc in ws_a
        code = handle_doc(trace_engine, UUID(str(ws_id)), "INV-ALPHA-001", mask=False, no_color=True)
        assert code == 0

        # Non-existent doc
        code_missing = handle_doc(trace_engine, UUID(str(ws_id)), "DOES-NOT-EXIST", mask=False, no_color=True)
        assert code_missing == 1

        # Non-existent run
        code_run = handle_run(trace_engine, UUID(str(ws_id)), "NON-EXISTENT-RUN", no_color=True)
        assert code_run == 1
    finally:
        trace_engine.dispose()
