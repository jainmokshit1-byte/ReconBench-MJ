"""Authentication endpoints and default-deny session/CSRF middleware."""

from __future__ import annotations

import ipaddress
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, SecretStr
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from reconbench_app.api.conventions import ProblemDetails, problem_response
from reconbench_app.authentication import (
    COOKIE_NAME,
    AuthConfigurationError,
    AuthContext,
    AuthService,
    LoginRateLimited,
)
from reconbench_app.authorization import (
    CAPABILITY_ORDER,
    PUBLIC_ROUTE_ALLOWLIST,
    ActorContext,
    capabilities_for_role,
    public_endpoint,
    requires,
    resolve_effective_role,
)
from reconbench_app.json_serialization import DecimalJSONResponse

router = APIRouter()
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LoginRequest(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=1024)


class AuthResponse(BaseModel):
    name: str
    role: str
    capabilities: list[str]
    csrf_token: str


_PROBLEM_RESPONSES = {
    401: {"model": ProblemDetails, "description": "Authentication required or failed."},
    403: {"model": ProblemDetails, "description": "CSRF validation failed."},
    422: {"model": ProblemDetails, "description": "Request validation failed."},
}


def socket_peer_ip(request: Request) -> str:
    """Use ASGI's socket peer address; never parse X-Forwarded-For here."""

    peer = request.scope.get("client")
    if not peer or not peer[0]:
        raise ValueError("the request has no socket peer address")
    return ipaddress.ip_address(peer[0]).compressed


def _capabilities_for(role: str) -> list[str]:
    effective_role = resolve_effective_role(role)
    allowed = capabilities_for_role(effective_role)
    return [capability for capability in CAPABILITY_ORDER if capability in allowed]


def _auth_payload(context: AuthContext) -> dict[str, Any]:
    return {
        "name": context.display_name,
        "role": context.role,
        "capabilities": _capabilities_for(context.role),
        "csrf_token": context.csrf_token,
    }


def _cookie_flags(environment: str) -> dict[str, Any]:
    return {
        "key": COOKIE_NAME,
        "path": "/api/v1",
        "httponly": True,
        "secure": environment != "local",
        "samesite": "lax",
    }


class AuthSessionMiddleware(BaseHTTPMiddleware):
    """Require a valid session on API routes and CSRF on every unsafe request."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (path, request.method) in PUBLIC_ROUTE_ALLOWLIST:
            return await call_next(request)

        service: AuthService = request.app.state.auth_service
        session_token = request.cookies.get(COOKIE_NAME)
        unsafe = request.method in _UNSAFE_METHODS
        try:
            check = await run_in_threadpool(
                service.authenticate,
                session_token,
                request.headers.get("X-CSRF-Token"),
                require_csrf=unsafe,
            )
        except AuthConfigurationError:
            return problem_response(
                status=503,
                title="Authentication unavailable",
                detail="Authentication configuration is unavailable.",
                code="authentication_unavailable",
                instance=path,
            )

        if check.context is None:
            expired_headers = {"X-Session-Ended": "true"} if session_token else None
            response = problem_response(
                status=401,
                title="Authentication required",
                detail="A valid session is required.",
                code="unauthenticated",
                instance=path,
                headers=expired_headers,
            )
            if session_token:
                response.delete_cookie(**_cookie_flags(request.app.state.settings.environment))
            return response
        if unsafe and not check.csrf_valid:
            return problem_response(
                status=403,
                title="CSRF validation failed",
                detail="A valid X-CSRF-Token header is required.",
                code="CSRF_FAILED",
                instance=path,
            )

        request.state.auth_context = check.context
        request.state.actor_context = ActorContext(
            workspace_id=check.context.workspace_id,
            actor_id=check.context.user_id,
            actor_role=resolve_effective_role(check.context.role),
        )
        return await call_next(request)


@router.post(
    "/auth/login",
    response_model=AuthResponse,
    responses={
        **_PROBLEM_RESPONSES,
        429: {
            "model": ProblemDetails,
            "description": "Login rate limited.",
            "headers": {
                "Retry-After": {
                    "description": "Coarse retry delay in seconds.",
                    "schema": {"type": "integer", "minimum": 1},
                }
            },
        },
        503: {"model": ProblemDetails, "description": "Authentication unavailable."},
    },
    tags=["Authentication"],
)
@public_endpoint
def login(body: LoginRequest, request: Request) -> Response:
    service: AuthService = request.app.state.auth_service
    try:
        peer_ip = socket_peer_ip(request)
    except ValueError:
        return problem_response(
            status=503,
            title="Authentication unavailable",
            detail="Authentication is temporarily unavailable.",
            code="authentication_unavailable",
            instance=request.url.path,
        )
    try:
        session = service.login(
            body.email,
            body.password.get_secret_value(),
            peer_ip,
            request.headers.get("user-agent"),
        )
    except LoginRateLimited as exc:
        return problem_response(
            status=429,
            title="Too many login attempts",
            detail="Too many login attempts. Please try again later.",
            code="login_rate_limited",
            instance=request.url.path,
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )
    except AuthConfigurationError:
        return problem_response(
            status=503,
            title="Authentication unavailable",
            detail="Authentication configuration is unavailable.",
            code="authentication_unavailable",
            instance=request.url.path,
        )

    if session is None:
        return problem_response(
            status=401,
            title="Authentication failed",
            detail="Email or password is incorrect.",
            code="invalid_credentials",
            instance=request.url.path,
        )

    response = DecimalJSONResponse(
        content={
            "name": session.display_name,
            "role": session.role,
            "capabilities": _capabilities_for(session.role),
            "csrf_token": session.csrf_token,
        }
    )
    response.set_cookie(
        **_cookie_flags(request.app.state.settings.environment),
        value=session.token,
        max_age=session.max_age_seconds,
        expires=session.absolute_expires_at,
    )
    return response


@router.get(
    "/auth/me",
    response_model=AuthResponse,
    responses={
        401: {
            **_PROBLEM_RESPONSES[401],
            "headers": {
                "X-Session-Ended": {
                    "description": "Present when a supplied session cookie is no longer valid.",
                    "schema": {"type": "string", "enum": ["true"]},
                }
            },
        },
        403: _PROBLEM_RESPONSES[403],
    },
    tags=["Authentication"],
)
@requires("data.view")
def auth_me(request: Request) -> dict[str, Any]:
    context: AuthContext = request.state.auth_context
    return _auth_payload(context)


@router.post(
    "/auth/logout",
    status_code=204,
    responses={
        401: {
            **_PROBLEM_RESPONSES[401],
            "headers": {
                "X-Session-Ended": {
                    "description": "Present when a supplied session cookie is no longer valid.",
                    "schema": {"type": "string", "enum": ["true"]},
                }
            },
        },
        403: _PROBLEM_RESPONSES[403],
    },
    tags=["Authentication"],
)
@requires("data.view")
def logout(request: Request) -> Response:
    context: AuthContext = request.state.auth_context
    service: AuthService = request.app.state.auth_service
    service.revoke_session(context.session_id)
    response = Response(status_code=204)
    response.delete_cookie(**_cookie_flags(request.app.state.settings.environment))
    return response


__all__ = ["AuthSessionMiddleware", "router", "socket_peer_ip"]
