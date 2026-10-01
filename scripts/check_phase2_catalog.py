"""Audit Phase 2 workspace ownership, numeric, text, and unique-index rules."""

from __future__ import annotations

import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from reconbench_app.db.metadata import metadata  # noqa: E402

_WORKSPACE_ROOT = "workspace"
_EXPECTED_DOMAINS = {
    "d_amount": "numeric(18,2)",
    "d_hash": "text",
    "d_qty": "numeric(18,4)",
    "d_score": "numeric(5,4)",
    "d_taxrate": "numeric(5,2)",
}
_ALLOWED_NATIVE_NUMERIC = {(18, 2), (18, 4), (5, 4), (5, 2)}
# These native NUMERIC fields are explicitly defined with other precisions in
# the validated schema; they are not amount/rate/quantity/score/tax fields.
_EXPECTED_SPECIAL_NUMERIC = {
    ("ai_call_log", "cost_usd"): (12, 6),
    ("ai_setting", "budget_cap_usd"): (12, 4),
    ("recon_case", "variance_pct"): (9, 4),
    ("recon_line_result", "rate_var_pct"): (9, 4),
}
_REQUIRED_TEXT_COLUMNS = {
    ("document", "doc_no"),
    ("document", "canonical_doc_no"),
    ("document", "vendor_identity"),
    ("document_version", "doc_no"),
    ("document_version", "canonical_doc_no"),
    ("document_version", "vendor_gstin"),
    ("document_version", "buyer_gstin"),
    ("doc_line", "hsn_sac"),
    ("doc_line", "po_ref"),
    ("doc_line", "canonical_po_ref"),
    ("doc_line", "po_line_ref"),
    ("opening_balance", "po_no_raw"),
    ("opening_balance", "canonical_po_no"),
    ("vendor_gstin", "gstin"),
}

# These are intentionally global or scoped to their parent/run, as recorded in
# docs/schema-drift-check.md. All other non-primary unique indexes on app tables
# must begin with workspace_id.
_UNIQUE_EXCEPTIONS: dict[str, tuple[str, tuple[str, ...], str]] = {
    "audit_log_seq_key": ("audit_log", ("seq",), "audit sequence"),
    "doc_line_document_version_id_line_no_key": (
        "doc_line",
        ("document_version_id", "line_no"),
        "parent/line",
    ),
    "document_version_document_id_seq_key": (
        "document_version",
        ("document_id", "seq"),
        "parent/sequence",
    ),
    "ledger_entry_line_uq": (
        "ledger_entry",
        ("po_line_ledger_id", "source_doc_line_id"),
        "parent/line",
    ),
    "ledger_entry_po_line_ledger_id_seq_key": (
        "ledger_entry",
        ("po_line_ledger_id", "seq"),
        "parent/sequence",
    ),
    "opening_balance_source_file_id_row_no_key": (
        "opening_balance",
        ("source_file_id", "row_no"),
        "parent/row",
    ),
    "recon_case_run_id_invoice_id_key": (
        "recon_case",
        ("run_id", "invoice_id"),
        "run",
    ),
    "recon_line_result_case_id_line_no_key": (
        "recon_line_result",
        ("case_id", "line_no"),
        "parent/line",
    ),
    "run_manifest_run_id_key": ("run_manifest", ("run_id",), "run"),
    "session_token_uq": ("session", ("token_hash",), "session token"),
}

_COLUMN_CATALOG = text(
    """
    SELECT table_name, column_name, data_type, udt_name, is_nullable,
           numeric_precision, numeric_scale
      FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = ANY(:table_names)
    """
)

_INDEX_CATALOG = text(
    """
    SELECT table_class.relname AS table_name,
           index_class.relname AS index_name,
           ARRAY(
               SELECT attribute.attname
                 FROM unnest(index_data.indkey) WITH ORDINALITY AS key(attnum, position)
                 JOIN pg_attribute attribute
                   ON attribute.attrelid = table_class.oid AND attribute.attnum = key.attnum
                WHERE key.position <= index_data.indnkeyatts
                ORDER BY key.position
           ) AS columns
      FROM pg_index index_data
      JOIN pg_class index_class ON index_class.oid = index_data.indexrelid
      JOIN pg_class table_class ON table_class.oid = index_data.indrelid
      JOIN pg_namespace schema ON schema.oid = table_class.relnamespace
     WHERE schema.nspname = 'public'
       AND table_class.relname = ANY(:table_names)
       AND index_data.indisunique
       AND NOT index_data.indisprimary
     ORDER BY table_class.relname, index_class.relname
    """
)


def _check_columns(connection: Connection, errors: list[str]) -> int:
    app_tables = set(metadata.tables)
    workspace_owned_tables = app_tables - {_WORKSPACE_ROOT}
    rows = connection.execute(
        _COLUMN_CATALOG, {"table_names": sorted(app_tables)}
    ).mappings()
    columns: dict[tuple[str, str], Any] = {
        (row["table_name"], row["column_name"]): row for row in rows
    }
    missing_tables = sorted(
        table_name
        for table_name in app_tables
        if not any(table == table_name for table, _ in columns)
    )
    if missing_tables:
        errors.append(f"missing ReconBench tables: {missing_tables}")

    missing_workspace = sorted(
        table_name
        for table_name in workspace_owned_tables
        if (table_name, "workspace_id") not in columns
    )
    nullable_workspace = sorted(
        table_name
        for table_name in workspace_owned_tables
        if (table_name, "workspace_id") in columns
        and columns[(table_name, "workspace_id")]["is_nullable"] != "NO"
    )
    if missing_workspace:
        errors.append(f"workspace-owned tables missing workspace_id: {missing_workspace}")
    if nullable_workspace:
        errors.append(f"workspace_id must be NOT NULL on: {nullable_workspace}")
    if (_WORKSPACE_ROOT, "workspace_id") in columns:
        errors.append("global workspace root must not have workspace_id")

    float_columns = sorted(
        f"{table}.{column} ({row['data_type']})"
        for (table, column), row in columns.items()
        if row["data_type"] in {"real", "double precision"}
        or row["udt_name"] in {"float4", "float8"}
    )
    if float_columns:
        errors.append(f"float/double columns are not allowed: {float_columns}")

    numeric_columns = {
        (table, column): row
        for (table, column), row in columns.items()
        if row["data_type"] == "numeric"
    }
    invalid_numeric = sorted(
        (table, column, row["numeric_precision"], row["numeric_scale"])
        for (table, column), row in numeric_columns.items()
        if (row["numeric_precision"], row["numeric_scale"]) not in _ALLOWED_NATIVE_NUMERIC
        and _EXPECTED_SPECIAL_NUMERIC.get((table, column))
        != (row["numeric_precision"], row["numeric_scale"])
    )
    if invalid_numeric:
        errors.append(f"native numeric columns use an unplanned precision: {invalid_numeric}")
    invalid_special_numeric = []
    for (table, column), expected in _EXPECTED_SPECIAL_NUMERIC.items():
        actual_row = numeric_columns.get((table, column))
        actual = (
            (actual_row["numeric_precision"], actual_row["numeric_scale"])
            if actual_row is not None
            else None
        )
        if actual != expected:
            invalid_special_numeric.append((table, column, expected, actual))
    if invalid_special_numeric:
        errors.append(
            "validated schema-specific numeric columns changed: "
            f"{invalid_special_numeric}"
        )

    invalid_text = []
    for table, column in _REQUIRED_TEXT_COLUMNS:
        actual = columns.get((table, column))
        if actual is None or actual["data_type"] != "text":
            invalid_text.append((table, column, actual["data_type"] if actual else "missing"))
    if invalid_text:
        errors.append(f"business identifiers/codes must remain text: {invalid_text}")

    domain_rows = connection.execute(
        text(
            "SELECT typname, format_type(typbasetype, typtypmod) AS base_type "
            "FROM pg_type WHERE typtype = 'd' "
            "AND typnamespace = 'public'::regnamespace "
            "AND typname = ANY(:domain_names)"
        ),
        {"domain_names": sorted(_EXPECTED_DOMAINS)},
    ).mappings()
    domains = {row["typname"]: row["base_type"] for row in domain_rows}
    if domains != _EXPECTED_DOMAINS:
        errors.append(f"numeric/hash domains mismatch: expected {_EXPECTED_DOMAINS}, got {domains}")
    return len(workspace_owned_tables)


def _check_unique_indexes(connection: Connection, errors: list[str]) -> tuple[int, Counter[str]]:
    rows = connection.execute(
        _INDEX_CATALOG, {"table_names": sorted(metadata.tables)}
    ).mappings()
    indexes = {row["index_name"]: row for row in rows}
    categories: Counter[str] = Counter()

    for name, (table_name, expected_columns, category) in _UNIQUE_EXCEPTIONS.items():
        actual = indexes.get(name)
        if actual is None:
            errors.append(f"reviewed unique-index exception is missing: {name}")
            continue
        if actual["table_name"] != table_name or tuple(actual["columns"]) != expected_columns:
            errors.append(
                f"reviewed exception {name} changed: expected {table_name}{expected_columns}, "
                f"got {actual['table_name']}{tuple(actual['columns'])}"
            )
        categories[category] += 1

    unexpected_unscoped = sorted(
        (row["table_name"], name, list(row["columns"]))
        for name, row in indexes.items()
        if name not in _UNIQUE_EXCEPTIONS
        and (not row["columns"] or row["columns"][0] != "workspace_id")
    )
    if unexpected_unscoped:
        errors.append(
            "tenant-scoped unique indexes must start with workspace_id; "
            f"unreviewed exceptions: {unexpected_unscoped}"
        )
    return len(indexes) - len(_UNIQUE_EXCEPTIONS), categories


def main() -> int:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "Set RB_PHASE2_TEST_DATABASE_URL to a disposable PostgreSQL 16 database"
        )
    url = make_url(dsn)
    if not url.database or not url.database.startswith("reconbench_phase2_"):
        raise RuntimeError("catalog check requires a reconbench_phase2_* database")

    engine = create_engine(dsn)
    errors: list[str] = []
    try:
        with engine.connect() as connection:
            server_version = int(connection.scalar(text("SHOW server_version_num")))
            workspace_owned_count = _check_columns(connection, errors)
            tenant_unique_count, exception_categories = _check_unique_indexes(connection, errors)
    finally:
        engine.dispose()

    if server_version // 10_000 != 16:
        errors.append(f"catalog check requires PostgreSQL 16, got {server_version}")
    if errors:
        print("Phase 2 catalog check failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        f"Phase 2 catalog check passed: {len(metadata.tables)} ReconBench tables; "
        f"{workspace_owned_count} workspace-owned tables have NOT NULL workspace_id; "
        "global workspace root is excluded; no float/double columns; text identifiers "
        "and numeric domains match; four documented schema-specific NUMERIC columns "
        "retain their exact precision; "
        f"{tenant_unique_count} tenant-scoped unique indexes are workspace-first; "
        f"{sum(exception_categories.values())} reviewed uniqueness exceptions "
        f"({dict(sorted(exception_categories.items()))})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
