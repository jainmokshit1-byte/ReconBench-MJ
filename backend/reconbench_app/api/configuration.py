"""Workspace configuration and shared reason-code endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response

from reconbench_app.api.conventions import (
    ApiProblem,
    ProblemDetails,
    conditional_json_response,
    etag_for_version,
    require_if_match,
)
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.configuration import (
    ConfigurationBody,
    ConfigurationResponse,
    ConfigurationService,
    ReasonCodeCatalog,
)
from reconbench_app.json_serialization import DecimalJSONResponse

router = APIRouter()


def _actor(request: Request) -> ActorContext:
    actor = getattr(request.state, "actor_context", None)
    if not isinstance(actor, ActorContext):
        raise ApiProblem(
            status=401,
            title="Authentication required",
            detail="A valid session is required.",
            code="unauthenticated",
        )
    return actor


def _service(request: Request) -> ConfigurationService:
    service = getattr(request.app.state, "configuration_service", None)
    if not isinstance(service, ConfigurationService):
        raise RuntimeError("configuration service is unavailable")
    return service


@router.get(
    "/config",
    response_model=ConfigurationResponse,
    tags=["Settings"],
)
@requires("data.view")
def get_config(request: Request) -> Response:
    """Return the active immutable settings version and its ETag."""

    result, version = _service(request).get(_actor(request).workspace_id)
    content = result.model_dump(mode="json", by_alias=True)
    return conditional_json_response(request, content, etag_for_version(version))


@router.put(
    "/config",
    response_model=ConfigurationResponse,
    tags=["Settings"],
    responses={
        400: {"model": ProblemDetails, "description": "The If-Match header is invalid."},
        401: {"model": ProblemDetails, "description": "Authentication is required."},
        403: {"model": ProblemDetails, "description": "The request failed CSRF validation."},
        404: {"model": ProblemDetails, "description": "No active configuration exists."},
        409: {
            "model": ProblemDetails,
            "description": "The active configuration changed since it was read.",
        },
        428: {"model": ProblemDetails, "description": "If-Match is required."},
        422: {"model": ProblemDetails, "description": "The configuration body is invalid."},
    },
)
@requires("settings.change")
def put_config(
    body: ConfigurationBody,
    request: Request,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """Append a validated configuration snapshot and atomically activate it."""

    expected_version = require_if_match(if_match)
    result, version = _service(request).update(
        _actor(request), expected_version=expected_version, body=body
    )
    return DecimalJSONResponse(
        content=result.model_dump(mode="json", by_alias=True),
        headers={"ETag": etag_for_version(version)},
    )


@router.get("/reason-codes", response_model=ReasonCodeCatalog, tags=["Settings"])
@requires("data.view")
def get_reason_codes(request: Request) -> ReasonCodeCatalog:
    """Return the seeded workspace catalogue used by the UI and exports."""

    return ReasonCodeCatalog(
        items=_service(request).list_reason_codes(_actor(request).workspace_id)
    )


__all__ = ["get_config", "get_reason_codes", "put_config", "router"]
