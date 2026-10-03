"""Capability declarations, P0 identity behavior, and HTTP security headers."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from uuid import uuid4

import boto3
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from reconbench_app.api.application import create_app
from reconbench_app.authentication import (
    COOKIE_NAME,
    AuthContext,
    AuthenticationCheck,
)
from reconbench_app.authorization import (
    CAPABILITY_ORDER,
    ROLE_CAPABILITIES,
    AuthorizationAuditError,
    audit_authorization_declarations,
    requires,
    resolve_effective_role,
)
from reconbench_app.middleware import MAX_JSON_BODY_BYTES
from reconbench_app.settings import Settings


def _service_classes() -> tuple[type[object], ...]:
    package = importlib.import_module("reconbench_app")
    classes: set[type[object]] = set()
    for module_info in pkgutil.walk_packages(package.__path__, package.__name__ + "."):
        if module_info.name == "reconbench_app.api.main":
            # Importing this module starts the production app and requires deployment settings.
            continue
        module = importlib.import_module(module_info.name)
        for member in vars(module).values():
            if (
                inspect.isclass(member)
                and member.__module__ == module.__name__
                and member.__name__.endswith("Service")
            ):
                classes.add(member)
    return tuple(sorted(classes, key=lambda service: service.__name__))


def _app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
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
    return create_app(settings)


def test_plan_three_role_capability_matrix_and_p0_role_resolution() -> None:
    assert tuple(CAPABILITY_ORDER) == (
        "files.upload",
        "links.edit",
        "recon.run",
        "cases.decide",
        "cases.approve_exception",
        "settings.change",
        "ai.manage",
        "users.manage",
        "export.create",
        "data.view",
    )
    assert {
        "ap_executive": frozenset(
            {
                "files.upload",
                "links.edit",
                "recon.run",
                "cases.decide",
                "export.create",
                "data.view",
            }
        ),
        "manager": frozenset(CAPABILITY_ORDER),
        "auditor": frozenset({"export.create", "data.view"}),
    } == ROLE_CAPABILITIES
    assert {resolve_effective_role(role) for role in ROLE_CAPABILITIES} == {"manager"}
    assert resolve_effective_role("client-supplied-role") == "manager"


def test_every_current_route_and_service_entrypoint_is_declared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch)
    audit_authorization_declarations(app, _service_classes())


def test_authorization_audit_fails_for_new_undeclared_route_and_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch)

    def added_route() -> dict[str, str]:
        return {"ok": "yes"}

    class AddedService:
        def newly_added_entrypoint(self) -> None:
            return None

    app.add_api_route("/api/v1/audit-probe", added_route, methods=["GET"])
    with pytest.raises(AuthorizationAuditError) as error:
        audit_authorization_declarations(app, (*_service_classes(), AddedService))
    assert "GET /api/v1/audit-probe has no authorization declaration" in str(error.value)
    assert "AddedService.newly_added_entrypoint has no authorization declaration" in str(
        error.value
    )


def test_headers_closed_cors_and_protected_documentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch)
    with TestClient(app, base_url="https://testserver", raise_server_exceptions=False) as client:
        health = client.get("/healthz", headers={"Origin": "https://untrusted.invalid"})
        assert health.status_code == 200
        assert health.headers["content-security-policy"] == (
            "default-src 'self'; frame-ancestors 'none'"
        )
        assert "unsafe-inline" not in health.headers["content-security-policy"]
        assert health.headers["x-content-type-options"] == "nosniff"
        assert health.headers["referrer-policy"] == "same-origin"
        assert "access-control-allow-origin" not in health.headers
        assert "strict-transport-security" not in health.headers

        preflight = client.options(
            "/api/v1/auth/me",
            headers={
                "Origin": "https://untrusted.invalid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-csrf-token",
            },
        )
        assert preflight.status_code == 401
        assert "access-control-allow-origin" not in preflight.headers

        for path in ("/api/v1/openapi.json", "/api/v1/docs", "/api/v1/auth/me"):
            response = client.get(path)
            assert response.status_code == 401
            assert response.headers["content-security-policy"].startswith("default-src 'self'")

        invalid_login = client.post("/api/v1/auth/login", json={})
        assert invalid_login.status_code == 422
        assert invalid_login.headers["x-content-type-options"] == "nosniff"
        assert invalid_login.headers["content-type"].startswith("application/problem+json")
        assert invalid_login.json()["code"] == "invalid_request"
        assert invalid_login.json()["errors"][0]["code"] == "invalid_field"

        oversized = client.post(
            "/api/v1/auth/login",
            content=b'{"payload":"' + (b"x" * MAX_JSON_BODY_BYTES) + b'"}',
            headers={"Content-Type": "application/json"},
        )
        assert oversized.status_code == 413
        assert oversized.headers["content-type"].startswith("application/problem+json")
        assert oversized.json()["code"] == "request_body_too_large"
        assert oversized.headers["referrer-policy"] == "same-origin"

        context = AuthContext(
            session_id=uuid4(),
            workspace_id=uuid4(),
            user_id=uuid4(),
            display_name="Synthetic User",
            role="manager",
            csrf_token="synthetic-csrf",
        )
        app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(
            context, True
        )

        @requires("data.view")
        def raises_server_error() -> None:
            raise RuntimeError("synthetic failure")

        app.add_api_route("/api/v1/security-header-error", raises_server_error, methods=["GET"])
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        server_error = client.get("/api/v1/security-header-error")
        assert server_error.status_code == 500
        assert server_error.headers["content-type"].startswith("application/problem+json")
        assert server_error.json()["code"] == "internal_error"
        assert server_error.headers["content-security-policy"] == (
            "default-src 'self'; frame-ancestors 'none'"
        )
        assert server_error.headers["x-content-type-options"] == "nosniff"


def test_authenticated_request_context_uses_database_identity_not_client_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app(monkeypatch)
    workspace_id, actor_id, session_id = uuid4(), uuid4(), uuid4()
    context = AuthContext(
        session_id=session_id,
        workspace_id=workspace_id,
        user_id=actor_id,
        display_name="Synthetic User",
        role="auditor",
        csrf_token="synthetic-csrf",
    )
    app.state.auth_service.authenticate = lambda *args, **kwargs: AuthenticationCheck(context, True)

    @requires("data.view")
    def whoami(request: Request) -> dict[str, str]:
        actor = request.state.actor_context
        return {
            "workspace_id": str(actor.workspace_id),
            "actor_id": str(actor.actor_id),
            "actor_role": actor.actor_role,
        }

    app.add_api_route("/api/v1/authorization-probe", whoami, methods=["GET"])
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(COOKIE_NAME, "synthetic-session", path="/api/v1")
        response = client.get("/api/v1/authorization-probe", headers={"X-Role": "auditor"})

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": str(workspace_id),
        "actor_id": str(actor_id),
        "actor_role": "manager",
    }
