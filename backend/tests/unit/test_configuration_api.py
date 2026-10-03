"""HTTP conventions for configuration and reason-code endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import boto3
import pytest
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.authentication import (
    COOKIE_NAME,
    AuthContext,
    AuthenticationCheck,
)
from reconbench_app.configuration import (
    ConfigurationResponse,
    canonical_hash,
)
from reconbench_app.db.repository import VersionConflict
from reconbench_app.db.seed_data import DEFAULT_CONFIG
from reconbench_app.settings import Settings
from reconbench_core.recon.registry import rules_hash_for_engine


def _app(monkeypatch: pytest.MonkeyPatch):
    class FakeObjectStore:
        def list_buckets(self) -> dict[str, list[object]]:
            return {"Buckets": []}

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://synthetic:synthetic@127.0.0.1:1/synthetic",
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
        session_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        display_name="Synthetic Config User",
        role="manager",
        csrf_token="synthetic-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(context, True)
    response = ConfigurationResponse(
        config_version_id=uuid4(),
        seq=1,
        body=DEFAULT_CONFIG,
        engine_hash=canonical_hash(DEFAULT_CONFIG["engine"]),
        rules_hash=rules_hash_for_engine(DEFAULT_CONFIG["engine"]),
        body_hash=canonical_hash(DEFAULT_CONFIG),
        created_at=datetime.now(UTC),
        change_note="Synthetic baseline",
    )
    app.state.configuration_service.get = lambda workspace_id: (response, 1)
    app.state.configuration_service.list_reason_codes = lambda workspace_id: [
        {
            "code": "RATE_HIGH",
            "level": "fail",
            "stage": "S5",
            "title": "Rate too high",
            "message": "Synthetic shared catalogue entry.",
            "suggested_action": "Review the invoice rate.",
            "overridable": True,
            "level_configurable": False,
            "is_proposed": False,
            "is_active": True,
        }
    ]
    return app, response


def test_get_config_returns_etag_and_honors_conditional_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _app(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        response = client.get("/api/v1/config")
        not_modified = client.get("/api/v1/config", headers={"If-None-Match": '"1"'})

    assert response.status_code == 200
    assert response.headers["etag"] == '"1"'
    assert response.json()["seq"] == 1
    assert not_modified.status_code == 304
    assert not_modified.headers["etag"] == '"1"'


def test_put_config_requires_if_match_and_maps_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _app(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        missing_header = client.put("/api/v1/config", json=DEFAULT_CONFIG)
        invalid_body = {**DEFAULT_CONFIG, "engine": {**DEFAULT_CONFIG["engine"]}}
        invalid_body["engine"]["tolerances"] = {
            **DEFAULT_CONFIG["engine"]["tolerances"],
            "limit_mode": "loose",
        }
        invalid = client.put(
            "/api/v1/config",
            json=invalid_body,
            headers={"If-Match": '"1"', "X-CSRF-Token": "synthetic-csrf"},
        )

    assert missing_header.status_code == 428
    assert missing_header.json()["code"] == "if_match_required"
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_request"
    assert any("limit_mode" in error["location"] for error in invalid.json()["errors"])


def test_put_config_returns_current_snapshot_on_stale_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, current = _app(monkeypatch)

    def conflict(actor, *, expected_version, body):
        del actor, expected_version, body
        raise VersionConflict(
            {
                "version": 2,
                "etag": '"2"',
                "config": current.model_dump(mode="json"),
            }
        )

    app.state.configuration_service.update = conflict
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        response = client.put(
            "/api/v1/config",
            json=DEFAULT_CONFIG,
            headers={"If-Match": '"1"', "X-CSRF-Token": "synthetic-csrf"},
        )

    assert response.status_code == 409
    assert response.json()["code"] == "version_conflict"
    assert response.json()["current"]["version"] == 2
    assert response.json()["current"]["config"]["seq"] == 1


def test_reason_codes_endpoint_reads_the_shared_catalogue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _app(monkeypatch)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        response = client.get("/api/v1/reason-codes")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "code": "RATE_HIGH",
                "level": "fail",
                "stage": "S5",
                "title": "Rate too high",
                "message": "Synthetic shared catalogue entry.",
                "suggested_action": "Review the invoice rate.",
                "overridable": True,
                "level_configurable": False,
                "is_proposed": False,
                "is_active": True,
            }
        ]
    }
