"""Run the idempotent Phase 2 bootstrap seed against RB_DATABASE_URL."""

from __future__ import annotations

import os
import sys
from importlib import import_module
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

seed_module = import_module("reconbench_app.db.seed")
BootstrapSettings = seed_module.BootstrapSettings
seed_database = seed_module.seed_database


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    database_url = os.environ.get("RB_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Set RB_DATABASE_URL to the ReconBench application database")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ValueError("The seed script requires PostgreSQL with the psycopg driver")

    settings = BootstrapSettings.from_environment()
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        counts = seed_database(engine, settings)
    finally:
        engine.dispose()
    for table_name, count in counts.items():
        print(f"{table_name}: {count}")
    if counts["reason_code"] != 28 or counts["config_active"] != 1:
        raise RuntimeError(
            "seed verification failed: expected 28 reason codes and active config seq 1"
        )
    if counts["ai_setting"] != 0:
        raise RuntimeError(
            "seed verification failed: optional ai_setting should be absent"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
