"""Unit tests for the HealthService and /api/v1/health dashboard endpoint."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import boto3
import pytest
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.authentication import AuthContext, AuthenticationCheck
from reconbench_app.health_service import (
    AiHealthDashboard,
    HealthIssue,
    InfrastructureComponent,
    IngestionHealthDashboard,
    MappingHealthDashboard,
    ReconciliationHealthDashboard,
    InfrastructureComponent,
    IngestionHealthDashboard,
    MappingHealthDashboard,
    ReconciliationHealthDashboard,
    SystemHealthResponse,
)
from reconbench_app.settings import Settings
from reconbench_core.version import ENGINE_VERSION


class FakeObjectStore:
    def list_buckets(self) -> dict[str, list[object]]:
        return {"Buckets": []}


def _settings() -> Settings:
    return Settings(
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


def _sample_dashboard() -> SystemHealthResponse:
    return SystemHealthResponse(
        status="healthy",
        timestamp=datetime.now(UTC),
        app_version="0.1.0",
        engine_version=ENGINE_VERSION,
        infrastructure={
            "database": InfrastructureComponent(
                name="PostgreSQL Database",
                status="healthy",
                latency_ms=1.2,
                details={"migrations_in_sync": True},
                message="Connected",
            ),
            "storage": InfrastructureComponent(
                name="Object Store",
                status="healthy",
                latency_ms=4.5,
                details={"bucket": "synthetic-test"},
                message="Bucket accessible",
            ),
            "workers": InfrastructureComponent(
                name="Background Workers",
                status="healthy",
                details={"active_workers": 1},
                message="1 active worker",
            ),
        },
        ai=AiHealthDashboard(
            status="healthy",
            mode="ai_assisted",
            mode_label="AI Assisted",
            reader_strategy="single",
            primary_reader="vllm",
            scanned_invoices_reader="ai",
            scanned_invoices_available=True,
            scanned_invoices_reading=True,
            providers=[],
        ),
        ingestion=IngestionHealthDashboard(
            status="healthy",
            total_files=5,
            by_status={"parsed": 5},
            by_doc_type={"invoice": 5},
        ),
        mapping=MappingHealthDashboard(
            status="healthy",
            total_vendors=2,
            unassigned_documents=0,
            total_links=3,
            confirmed_links=3,
        ),
        reconciliation=ReconciliationHealthDashboard(
            status="healthy",
            engine_version=ENGINE_VERSION,
            rules_hash="a" * 64,
            latest_run_id=str(uuid4()),
            latest_run_status="completed",
            total_cases=5,
            done_cases=5,
            matched_pct=100.0,
        ),
        issues=[
            HealthIssue(
                severity="info",
                component="ai",
                title="System Operational",
                description="All components healthy.",
                action_hint="Ready for processing.",
            )
        ],
    )


def test_system_health_response_schema() -> None:
    """SystemHealthResponse instantiates and validates all nested dashboard models."""
    response = _sample_dashboard()
    assert response.status == "healthy"
    assert response.engine_version == ENGINE_VERSION
    assert response.reconciliation.matched_pct == 100.0
    assert response.issues[0].severity == "info"


def test_health_api_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET /api/v1/health returns 200 with complete system diagnostics."""
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: FakeObjectStore())
    settings = _settings()
    app = create_app(settings)

    ws_id = uuid4()
    context = AuthContext(
        session_id=uuid4(),
        workspace_id=ws_id,
        user_id=uuid4(),
        display_name="Health Tester",
        role="manager",
        csrf_token="test-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(context, True)
    sample = _sample_dashboard()
    app.state.health_service.get_dashboard = MagicMock(return_value=sample)

    client = TestClient(app)
    response = client.get("/api/v1/health", cookies={"reconbench_session": "test-cookie"})
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "healthy"
    assert payload["engine_version"] == ENGINE_VERSION
    assert "infrastructure" in payload
    assert "database" in payload["infrastructure"]
    assert "storage" in payload["infrastructure"]
    assert "workers" in payload["infrastructure"]
    assert "ai" in payload
    assert "ingestion" in payload
    assert "mapping" in payload
    assert "reconciliation" in payload
    assert len(payload["issues"]) == 1
