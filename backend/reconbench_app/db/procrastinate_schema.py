"""Procrastinate 3.10 schema CLI app; used only by the separate queue step."""

from __future__ import annotations

from procrastinate import App, PsycopgConnector
from sqlalchemy.engine import make_url

from reconbench_app.db.migration_config import migration_url

url = make_url(migration_url()).set(drivername="postgresql")
app = App(
    connector=PsycopgConnector(conninfo=url.render_as_string(hide_password=False))
)
