"""PostgreSQL integration for immutable config updates and shared catalogue reads."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import boto3
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.authentication import (
    COOKIE_NAME,
    AuthContext,
    AuthenticationCheck,
)
from reconbench_app.configuration import canonical_hash
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.db.seed_data import DEFAULT_CONFIG, REASON_CODES
from reconbench_app.settings import Settings
from sqlalchemy.engine import Engine, make_url


@pytest.fixture(scope="module")
def configuration_engine() -> Iterator[Engine]:
    dsn = os.environ.get("RB_PHASE2_TEST_DATABASE_URL", "").strip()
    if not dsn:
        pytest.skip("RB_PHASE2_TEST_DATABASE_URL is required for disposable PostgreSQL tests")
    database_name = make_url(dsn).database
    if not database_name or not database_name.startswith("reconbench_phase2_"):
        raise RuntimeError(
            "configuration integration tests require a disposable reconbench_phase2_* database"
        )
    engine = sa.create_engine(dsn, pool_size=8, max_overflow=8)
    try:
        with engine.connect() as connection:
            server_version = int(connection.scalar(sa.text("SHOW server_version_num")))
            if server_version // 10_000 != 16:
                raise RuntimeError("configuration integration tests require PostgreSQL 16")
            expected_heads = set(
                ScriptDirectory.from_config(Config("backend/alembic.ini")).get_heads()
            )
            actual_heads = set(MigrationContext.configure(connection).get_current_heads())
            if actual_heads != expected_heads:
                raise RuntimeError("configuration integration database must be at Alembic head")
        yield engine
    finally:
        engine.dispose()


def _synthetic_workspace(engine: Engine) -> tuple[UUID, UUID, dict[str, Any]]:
    workspace = metadata.tables["workspace"]
    app_user = metadata.tables["app_user"]
    config_version = metadata.tables["config_version"]
    config_active = metadata.tables["config_active"]
    reason_code = metadata.tables["reason_code"]
    workspace_id = new_id()
    user_id = new_id()
    config_id = new_id()
    with engine.begin() as connection:
        connection.execute(
            sa.insert(workspace).values(id=workspace_id, name="Synthetic Config Workspace")
        )
        connection.execute(
            sa.insert(app_user).values(
                id=user_id,
                workspace_id=workspace_id,
                email=f"config-{workspace_id}@example.invalid",
                display_name="Synthetic Config Manager",
                role="manager",
                password_hash="synthetic-unusable-hash",
            )
        )
        connection.execute(
            sa.insert(config_version).values(
                id=config_id,
                workspace_id=workspace_id,
                seq=1,
                parent_version_id=None,
                body=DEFAULT_CONFIG,
                engine_hash=canonical_hash(DEFAULT_CONFIG["engine"]),
                body_hash=canonical_hash(DEFAULT_CONFIG),
                change_note="Synthetic baseline",
                created_by=user_id,
            )
        )
        connection.execute(
            sa.insert(config_active).values(
                id=new_id(),
                workspace_id=workspace_id,
                config_version_id=config_id,
                activated_by=user_id,
                version=1,
            )
        )
        for item in REASON_CODES:
            connection.execute(
                sa.insert(reason_code).values(id=new_id(), workspace_id=workspace_id, **item)
            )
    return workspace_id, user_id, DEFAULT_CONFIG


def test_configuration_versions_stale_writes_audit_and_reason_catalogue(
    configuration_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, user_id, baseline_body = _synthetic_workspace(configuration_engine)

    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        environment="ci",
        database_url=configuration_engine.url.render_as_string(hide_password=False),
        s3_endpoint_url="http://127.0.0.1:1",
        s3_region="us-east-1",
        s3_bucket="synthetic-test",
        s3_api_access_key="synthetic-access",
        s3_api_secret_key="synthetic-secret",
        public_base_url="https://example.invalid",
        argon2_time_cost=1,
        argon2_memory_cost_kib=8192,
        argon2_parallelism=1,
    )
    app = create_app(settings)
    context = AuthContext(
        session_id=new_id(),
        workspace_id=workspace_id,
        user_id=user_id,
        display_name="Synthetic Config Manager",
        role="manager",
        csrf_token="synthetic-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(context, True)

    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        baseline = client.get("/api/v1/config")
        assert baseline.status_code == 200
        assert baseline.headers["etag"] == '"1"'

        app_only_body = baseline.json()["body"]
        app_only_body["app"]["ui"]["poll_seconds"] = 4
        app_only = client.put(
            "/api/v1/config",
            json=app_only_body,
            headers={"If-Match": '"1"', "X-CSRF-Token": "synthetic-csrf"},
        )
        assert app_only.status_code == 200
        assert app_only.headers["etag"] == '"2"'
        assert app_only.json()["seq"] == 2
        assert app_only.json()["rules_hash"] == baseline.json()["rules_hash"]

        engine_body = app_only.json()["body"]
        engine_body["engine"]["thresholds"]["extraction_gate"] = "0.75"
        engine_change = client.put(
            "/api/v1/config",
            json=engine_body,
            headers={"If-Match": '"2"', "X-CSRF-Token": "synthetic-csrf"},
        )
        assert engine_change.status_code == 200
        assert engine_change.headers["etag"] == '"3"'
        assert engine_change.json()["seq"] == 3
        assert engine_change.json()["rules_hash"] != app_only.json()["rules_hash"]
        assert engine_change.json()["engine_hash"] != app_only.json()["engine_hash"]

        stale = client.put(
            "/api/v1/config",
            json=engine_body,
            headers={"If-Match": '"2"', "X-CSRF-Token": "synthetic-csrf"},
        )
        catalogue = client.get("/api/v1/reason-codes")

    assert stale.status_code == 409
    assert stale.json()["current"]["version"] == 3
    assert stale.json()["current"]["config"]["seq"] == 3
    assert catalogue.status_code == 200
    assert len(catalogue.json()["items"]) == len(REASON_CODES)
    assert {item["code"] for item in catalogue.json()["items"]} == {
        item["code"] for item in REASON_CODES
    }

    config_version = metadata.tables["config_version"]
    config_active = metadata.tables["config_active"]
    audit_log = metadata.tables["audit_log"]
    with configuration_engine.connect() as connection:
        first_version = (
            connection.execute(
                sa.select(config_version.c.body, config_version.c.body_hash).where(
                    config_version.c.workspace_id == workspace_id,
                    config_version.c.seq == 1,
                )
            )
            .mappings()
            .one()
        )
        active = (
            connection.execute(
                sa.select(config_active.c.version, config_active.c.config_version_id).where(
                    config_active.c.workspace_id == workspace_id
                )
            )
            .mappings()
            .one()
        )
        saved_config_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(config_version)
            .where(config_version.c.workspace_id == workspace_id)
        )
        audit_count = connection.scalar(
            sa.select(sa.func.count())
            .select_from(audit_log)
            .where(
                audit_log.c.workspace_id == workspace_id,
                audit_log.c.action == "config.save",
            )
        )
    assert first_version["body"] == baseline_body
    assert first_version["body_hash"] == canonical_hash(baseline_body)
    assert active["version"] == 3
    assert saved_config_count == 3
    assert audit_count == 2
