"""Check the query-path indexes listed in Schema §6.4 and TRD §6.4."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_REQUIRED: dict[str, dict[str, Any]] = {
    "document_vendor_ix": {
        "table": "document",
        "columns": ["workspace_id", "doc_type", "vendor_id", "doc_date"],
        "unique": False,
        "method": "btree",
    },
    "document_natural_uq": {
        "table": "document",
        "columns": ["workspace_id", "natural_key"],
        "unique": True,
        "method": "btree",
    },
    "link_to_state_ix": {
        "table": "link",
        "columns": ["to_document_id", "state"],
        "unique": False,
        "method": "btree",
    },
    "link_from_ix": {
        "table": "link",
        "columns": ["from_document_id"],
        "unique": False,
        "method": "btree",
    },
    "recon_case_run_status_ix": {
        "table": "recon_case",
        "columns": ["run_id", "status"],
        "unique": False,
        "method": "btree",
    },
    "recon_case_reason_gin": {
        "table": "recon_case",
        "columns": ["reason_codes"],
        "unique": False,
        "method": "gin",
    },
    "case_current_workspace_id_invoice_id_key": {
        "table": "case_current",
        "columns": ["workspace_id", "invoice_id"],
        "unique": True,
        "method": "btree",
    },
    "doc_line_document_version_id_line_no_key": {
        "table": "doc_line",
        "columns": ["document_version_id", "line_no"],
        "unique": True,
        "method": "btree",
    },
    "vendor_name_trgm": {
        "table": "vendor",
        "columns": ["normalized_name"],
        "unique": False,
        "method": "gin",
        "definition_contains": "gin_trgm_ops",
    },
    "document_canon_trgm": {
        "table": "document",
        "columns": ["canonical_doc_no"],
        "unique": False,
        "method": "gin",
        "definition_contains": "gin_trgm_ops",
    },
    "dirty_scope_open_uq": {
        "table": "dirty_scope",
        "columns": ["workspace_id", "scope_kind", "scope_id"],
        "unique": True,
        "method": "btree",
        "predicate_contains": "processed_at IS NULL",
    },
    "dirty_scope_work_ix": {
        "table": "dirty_scope",
        "columns": ["workspace_id", "first_marked_at"],
        "unique": False,
        "method": "btree",
        "predicate_contains": "processed_at IS NULL",
    },
}

_INDEX_CATALOG = text(
    """
    SELECT index_class.relname AS name,
           table_class.relname AS table_name,
           am.amname AS method,
           index_data.indisunique AS is_unique,
           ARRAY(
               SELECT attribute.attname
                 FROM unnest(index_data.indkey) WITH ORDINALITY AS key(attnum, position)
                 JOIN pg_attribute attribute
                   ON attribute.attrelid = table_class.oid AND attribute.attnum = key.attnum
                WHERE key.position <= index_data.indnkeyatts
                ORDER BY key.position
           ) AS columns,
           pg_get_expr(index_data.indpred, index_data.indrelid) AS predicate,
           pg_get_indexdef(index_data.indexrelid) AS definition
      FROM pg_index index_data
      JOIN pg_class index_class ON index_class.oid = index_data.indexrelid
      JOIN pg_class table_class ON table_class.oid = index_data.indrelid
      JOIN pg_namespace schema ON schema.oid = table_class.relnamespace
      JOIN pg_am am ON am.oid = index_class.relam
     WHERE schema.nspname = 'public'
    """
)


def check_indexes(connection: Connection) -> list[str]:
    indexes = {
        row["name"]: dict(row) for row in connection.execute(_INDEX_CATALOG).mappings()
    }
    errors: list[str] = []
    for name, expected in _REQUIRED.items():
        actual = indexes.get(name)
        if actual is None:
            errors.append(f"missing index {name}")
            continue
        for field in ("table", "columns", "unique", "method"):
            actual_field = (
                "table_name"
                if field == "table"
                else ("is_unique" if field == "unique" else field)
            )
            if actual[actual_field] != expected[field]:
                errors.append(
                    f"{name}: expected {field}={expected[field]!r}, got {actual[actual_field]!r}"
                )
        if (
            expected.get("predicate_contains")
            and expected["predicate_contains"].lower()
            not in (actual["predicate"] or "").lower()
        ):
            errors.append(
                f"{name}: expected partial predicate {expected['predicate_contains']!r}"
            )
        if (
            expected.get("definition_contains")
            and expected["definition_contains"].lower()
            not in (actual["definition"] or "").lower()
        ):
            errors.append(
                f"{name}: expected index definition to contain {expected['definition_contains']!r}"
            )
    return errors


def main() -> int:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "Set RB_PHASE2_TEST_DATABASE_URL to a disposable PostgreSQL 16 database"
        )
    url = make_url(dsn)
    if not url.database or not url.database.startswith("reconbench_phase2_"):
        raise RuntimeError(
            "index review only runs against reconbench_phase2_* databases"
        )
    engine = create_engine(dsn)
    try:
        with engine.connect() as connection:
            errors = check_indexes(connection)
    finally:
        engine.dispose()
    if errors:
        print("Phase 2 index review failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Phase 2 index review passed: {len(_REQUIRED)} required indexes verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
