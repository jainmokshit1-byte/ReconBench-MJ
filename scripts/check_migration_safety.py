"""Apply the latest revision to a disposable copy of its previous schema."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from reconbench_app.db.migration_config import migration_url  # noqa: E402


def _database_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise ValueError(
            "disposable database names may contain only letters, digits, and underscores"
        )
    if not value.startswith("reconbench_phase2_"):
        raise ValueError(
            "migration-safety check requires reconbench_phase2_* databases"
        )
    return value


def main() -> int:
    source_url = make_url(migration_url())
    source_name = _database_name(source_url.database or "")
    config = Config(str(BACKEND / "alembic.ini"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise RuntimeError(
            "migration-safety check requires a linear, single-head migration chain"
        )
    head = scripts.get_revision(heads[0])
    if head is None or not isinstance(head.down_revision, str):
        raise RuntimeError(
            "migration-safety check requires a head with one previous revision"
        )
    previous = head.down_revision

    source_engine = create_engine(source_url)
    try:
        with source_engine.connect() as connection:
            actual = set(MigrationContext.configure(connection).get_current_heads())
            server_version = int(connection.scalar(text("SHOW server_version_num")))
        if server_version // 10_000 != 16:
            raise RuntimeError("migration-safety check requires PostgreSQL 16")
        if actual != {previous}:
            raise RuntimeError(
                "source must be at the previous migration revision before cloning: "
                f"expected {previous}, found {sorted(actual)}"
            )
    finally:
        source_engine.dispose()

    copy_name = _database_name(f"{source_name[:38]}_copy_{uuid4().hex[:8]}")
    maintenance_url = source_url.set(database="postgres")
    maintenance_engine = create_engine(maintenance_url, isolation_level="AUTOCOMMIT")
    copy_created = False
    try:
        with maintenance_engine.connect() as connection:
            connection.exec_driver_sql(
                f'CREATE DATABASE "{copy_name}" TEMPLATE "{source_name}"'
            )
        copy_created = True

        copied_url: URL = source_url.set(database=copy_name)
        os.environ["RB_MIGRATOR_DATABASE_URL"] = copied_url.render_as_string(
            hide_password=False
        )
        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")

        copied_engine = create_engine(copied_url)
        try:
            with copied_engine.connect() as connection:
                copied_heads = set(
                    MigrationContext.configure(connection).get_current_heads()
                )
            if copied_heads != set(heads):
                raise RuntimeError(
                    "copied schema did not reach head: "
                    f"expected {heads}, found {sorted(copied_heads)}"
                )
        finally:
            copied_engine.dispose()
        print(
            f"Migration safety passed: cloned {previous} and upgraded the copy to {heads[0]}"
        )
        return 0
    finally:
        if copy_created:
            with maintenance_engine.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{copy_name}" WITH (FORCE)')
        maintenance_engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
