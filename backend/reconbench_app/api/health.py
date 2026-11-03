"""System health and operational diagnostics endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from reconbench_app.api.conventions import ApiProblem
from reconbench_app.authorization import ActorContext, requires
from reconbench_app.health_service import HealthService, SystemHealthResponse
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable

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


def _service(request: Request) -> HealthService:
    service = getattr(request.app.state, "health_service", None)
    if not isinstance(service, HealthService):
        raise RuntimeError("Health service is unavailable")
    return service


@router.get(
    "/health",
    response_model=SystemHealthResponse,
    tags=["Operations"],
    summary="Get full system health, connections, and AI precision metrics",
)
@requires("data.view")
def get_system_health(request: Request) -> Response:
    """Return live operational metrics for databases, storage, workers, AI, and pipeline stages."""

    actor = _actor(request)
    dashboard = _service(request).get_dashboard(actor.workspace_id, actor)
    return DecimalJSONResponse(
        content=to_jsonable(dashboard.model_dump(mode="json")),
        headers={"Cache-Control": "no-store"},
    )


__all__ = ["get_system_health", "router"]
