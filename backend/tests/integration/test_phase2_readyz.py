"""/readyz reports the database's Alembic revision state."""

from __future__ import annotations

import os
from pathlib import Path

import boto3
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.settings import Settings
from sqlalchemy.engine import make_url


def test_readyz_requires_current_alembic_head(monkeypatch: pytest.MonkeyPatch) -> None:
    database_url = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not database_url:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    database_name = make_url(database_url).database
    if not database_name or not database_name.startswith("reconbench_phase2_"):
        raise RuntimeError("readiness test requires a disposable reconbench_phase2_* database")

    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-test",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="http://localhost:5173",
    )
    app = create_app(settings)
    engine = sa.create_engine(database_url)
    script = ScriptDirectory.from_config(Config(str(Path("backend/alembic.ini"))))
    head = script.get_current_head()
    revision = script.get_revision(head) if head else None
    if revision is None or not isinstance(revision.down_revision, str):
        raise RuntimeError("readiness test expects a single linear migration head")
    previous = revision.down_revision
    try:
        with TestClient(app) as client:
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql("SET LOCAL ROLE reconbench_migrator")
                    connection.execute(
                        sa.text("UPDATE public.alembic_version SET version_num = :revision"),
                        {"revision": previous},
                    )
                response = client.get("/readyz")
                assert response.status_code == 503
                assert response.json() == {"status": "not_ready"}
            finally:
                # Always leave the shared disposable database at the current head.
                with engine.begin() as connection:
                    connection.exec_driver_sql("SET LOCAL ROLE reconbench_migrator")
                    connection.execute(
                        sa.text("UPDATE public.alembic_version SET version_num = :revision"),
                        {"revision": head},
                    )

            response = client.get("/readyz")
            assert response.status_code == 200
            assert response.json() == {"status": "ready"}
    finally:
        engine.dispose()
