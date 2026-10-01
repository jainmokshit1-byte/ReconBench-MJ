"""Resolve the privileged DSN used only by schema migration commands."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import URL, make_url

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def migration_url() -> str:
    """Return an explicit migrator DSN, allowing the local owner DSN only in dev."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    migrator_dsn = os.environ.get("RB_MIGRATOR_DATABASE_URL", "").strip()
    if migrator_dsn:
        dsn = migrator_dsn
    elif os.environ.get("RB_ENV", "").lower() == "local":
        dsn = os.environ.get("RB_DATABASE_URL", "").strip()
        if not dsn:
            postgres_user = os.environ.get("POSTGRES_USER", "").strip()
            postgres_password = os.environ.get("POSTGRES_PASSWORD", "")
            if postgres_user and postgres_password:
                dsn = URL.create(
                    "postgresql+psycopg",
                    username=postgres_user,
                    password=postgres_password,
                    host="127.0.0.1",
                    port=int(os.environ.get("POSTGRES_PORT", "15432")),
                    database=os.environ.get("POSTGRES_DB", "reconbench"),
                ).render_as_string(hide_password=False)
    else:
        dsn = ""

    if not dsn:
        raise RuntimeError(
            "Set RB_MIGRATOR_DATABASE_URL for migrations; only RB_ENV=local may "
            "fall back to RB_DATABASE_URL."
        )

    url = make_url(dsn)
    if url.get_backend_name() != "postgresql":
        raise ValueError("ReconBench migrations require PostgreSQL")
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("Use the psycopg PostgreSQL driver for migrations")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)
