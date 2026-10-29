"""Revision 0033 (reason code AI_VALUE_UNVERIFIED, PD-2b): added with the seed text, reversible.

Each test clones the disposable database and works on the clone only: the source database
is never changed and nothing here touches a live database. Workspaces are synthetic.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from reconbench_app.db.seed_data import REASON_CODES
from sqlalchemy.engine import Engine, make_url

ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
_CODE = "AI_VALUE_UNVERIFIED"
_SEED_ROW = next(row for row in REASON_CODES if row["code"] == _CODE)
_COLUMNS = tuple(_SEED_ROW)


@contextmanager
def _clone() -> Iterator[Engine]:
    source = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not source:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    source_url = make_url(source)
    source_name = source_url.database or ""
    if not source_name.startswith("reconbench_phase2_"):
        raise RuntimeError("migration checks require a disposable reconbench_phase2_* database")
    clone_name = f"reconbench_phase2_m33_{uuid4().hex[:10]}"
    admin = sa.create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{clone_name}" TEMPLATE "{source_name}"')
    clone_url = source_url.set(database=clone_name).render_as_string(hide_password=False)
    engine = sa.create_engine(clone_url)
    previous = os.environ.get("RB_MIGRATOR_DATABASE_URL")
    try:
        with engine.connect() as connection:
            heads = set(MigrationContext.configure(connection).get_current_heads())
        assert heads == {"0034"}, heads
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


def _rows(connection: sa.Connection) -> dict[str, dict[str, object]]:
    """The code's catalogue row per workspace."""

    query = sa.text(
        f"SELECT workspace_id::text AS workspace_id, {', '.join(_COLUMNS)} "
        "FROM reason_code WHERE code = :code"
    )
    rows = connection.execute(query, {"code": _CODE}).mappings()
    return {str(row["workspace_id"]): {name: row[name] for name in _COLUMNS} for row in rows}


def _workspaces(connection: sa.Connection) -> set[str]:
    return set(connection.scalars(sa.text("SELECT id::text FROM workspace")))


def test_every_workspace_has_the_code_with_the_seed_text() -> None:
    with _clone() as engine, engine.connect() as connection:
        rows = _rows(connection)
        workspaces = _workspaces(connection)

    assert workspaces and set(rows) == workspaces
    assert all(row == _SEED_ROW for row in rows.values())


def test_downgrade_removes_only_the_code_and_upgrade_adds_it_back() -> None:
    with _clone() as engine:
        with engine.connect() as connection:
            before = dict(
                connection.execute(
                    sa.text(
                        "SELECT code, count(*) FROM reason_code WHERE code <> :code GROUP BY code"
                    ),
                    {"code": _CODE},
                ).all()
            )

        command.downgrade(_config(), "0032")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0032"
            assert _rows(connection) == {}
            after_downgrade = dict(
                connection.execute(
                    sa.text("SELECT code, count(*) FROM reason_code GROUP BY code")
                ).all()
            )
        assert after_downgrade == before

        command.upgrade(_config(), "head")
        with engine.connect() as connection:
            assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "0034"
            assert set(_rows(connection)) == _workspaces(connection)


def test_a_workspace_that_already_has_the_code_keeps_its_row() -> None:
    with _clone() as engine:
        command.downgrade(_config(), "0032")
        with engine.begin() as connection:
            kept = str(
                connection.scalar(
                    sa.text("INSERT INTO workspace (id, name) VALUES (:id, :name) RETURNING id"),
                    {"id": uuid4(), "name": "Synthetic PD-2b kept"},
                )
            )
            fresh = str(
                connection.scalar(
                    sa.text("INSERT INTO workspace (id, name) VALUES (:id, :name) RETURNING id"),
                    {"id": uuid4(), "name": "Synthetic PD-2b fresh"},
                )
            )
            connection.execute(
                sa.text(
                    "INSERT INTO reason_code (id, workspace_id, code, level, stage, title, "
                    "message, suggested_action, is_active) VALUES (:id, :w, :code, 'warn', "
                    "'S1', 'Own title', 'Own message', 'Own action', FALSE)"
                ),
                {"id": uuid4(), "w": kept, "code": _CODE},
            )

        command.upgrade(_config(), "head")
        with engine.connect() as connection:
            rows = _rows(connection)

    assert rows[kept]["title"] == "Own title" and rows[kept]["is_active"] is False
    assert rows[fresh] == _SEED_ROW
