"""Count ReconBench and pinned Procrastinate tables after the queue schema step."""

from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

metadata = import_module("reconbench_app.db.metadata").metadata

_PROCRASTINATE_TABLES = {
    "procrastinate_events",
    "procrastinate_jobs",
    "procrastinate_periodic_defers",
    "procrastinate_workers",
}


def main() -> int:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "Set RB_PHASE2_TEST_DATABASE_URL to a disposable PostgreSQL 16 database"
        )
    url = make_url(dsn)
    if not url.database or not url.database.startswith("reconbench_phase2_"):
        raise RuntimeError("table-count check requires a reconbench_phase2_* database")

    engine = create_engine(dsn)
    try:
        with engine.connect() as connection:
            version = int(connection.scalar(text("SHOW server_version_num")))
            tables = set(
                connection.scalars(
                    text(
                        "SELECT relation.relname FROM pg_class relation "
                        "JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace "
                        "WHERE namespace.nspname = 'public' "
                        "AND relation.relkind IN ('r', 'p')"
                    )
                ).all()
            )
    finally:
        engine.dispose()

    errors: list[str] = []
    if version // 10_000 != 16:
        errors.append(f"expected PostgreSQL 16, got server_version_num={version}")
    application_tables = tables & set(metadata.tables)
    if application_tables != set(metadata.tables):
        missing = sorted(set(metadata.tables) - application_tables)
        errors.append(f"missing ReconBench tables: {missing}")
    queue_tables = {name for name in tables if name.startswith("procrastinate_")}
    if queue_tables != _PROCRASTINATE_TABLES:
        errors.append(
            "pinned Procrastinate table set mismatch: "
            f"expected {sorted(_PROCRASTINATE_TABLES)}, got {sorted(queue_tables)}"
        )
    allowed = set(metadata.tables) | _PROCRASTINATE_TABLES | {"alembic_version"}
    unexpected = sorted(tables - allowed)
    if unexpected:
        errors.append(f"unexpected public tables: {unexpected}")
    if "alembic_version" not in tables:
        errors.append("missing Alembic version table")

    if errors:
        print("Phase 2 table-count check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(
        "Phase 2 table-count check passed: "
        f"{len(application_tables)} ReconBench tables + {len(queue_tables)} "
        "pinned Procrastinate tables (+ Alembic bookkeeping)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
