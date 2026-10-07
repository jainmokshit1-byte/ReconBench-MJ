"""AI Assist settings, consent, key, provider-test, usage and offline self-test endpoints.

``POST /ai/test`` is the offline guardrail self-test and never calls a provider.
Provider calls happen only in the AI worker; these endpoints enqueue and report.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Query, Request
from starlette.responses import Response

from reconbench_app.ai.settings_service import (
    AiSettingsError,
    AiSettingsService,
    AiSettingsUpdate,
)
from reconbench_app.ai_assist import run_stub_guardrail_test
from reconbench_app.api.conventions import ApiProblem, etag_for_version, require_if_match
from reconbench_app.api.reconciliation import _actor
from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable

router = APIRouter()
_TEST_LIMIT = 5
_TEST_WINDOW = timedelta(minutes=1)


@router.post("/ai/test", tags=["AI"])
@requires("ai.manage")
def test_ai_guardrails(request: Request) -> Response:
    """Run bounded local fixtures; this endpoint never calls an external provider."""

    actor = _actor(request)
    now = datetime.now(UTC)
    events = metadata.tables["rate_limit_event"]
    with request.app.state.database_engine.begin() as connection:
        connection.execute(
            sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"reconbench.ai-test:{actor.workspace_id}:{actor.actor_id}"},
        )
        recent = connection.scalar(
            sa.select(sa.func.count())
            .select_from(events)
            .where(
                events.c.workspace_id == actor.workspace_id,
                events.c.user_id == actor.actor_id,
                events.c.kind == "ai_test",
                events.c.occurred_at >= now - _TEST_WINDOW,
            )
        )
        if int(recent or 0) >= _TEST_LIMIT:
            raise ApiProblem(
                status=429,
                title="AI self-test rate limited",
                detail="Wait before running the local AI guardrail test again.",
                code="ai_test_rate_limited",
                headers={"Retry-After": str(int(_TEST_WINDOW.total_seconds()))},
            )
        connection.execute(
            sa.insert(events).values(
                id=new_id(),
                workspace_id=actor.workspace_id,
                kind="ai_test",
                user_id=actor.actor_id,
                ip=request.client.host if request.client else None,
                succeeded=True,
                occurred_at=now,
            )
        )
    result = run_stub_guardrail_test()
    return DecimalJSONResponse(content=result, headers={"Cache-Control": "no-store"})


def _service(request: Request) -> AiSettingsService:
    service = getattr(request.app.state, "ai_settings_service", None)
    if not isinstance(service, AiSettingsService):
        raise RuntimeError("AI settings service is unavailable")
    return service


def _respond(content: Any, *, status_code: int = 200, version: int | None = None) -> Response:
    headers = {"Cache-Control": "no-store"}
    if version is not None:
        headers["ETag"] = etag_for_version(version)
    return DecimalJSONResponse(
        status_code=status_code, content=to_jsonable(content), headers=headers
    )


def _problem(error: AiSettingsError) -> ApiProblem:
    return ApiProblem(status=error.status, title=error.title, detail=error.detail, code=error.code)


def _session_id(request: Request) -> UUID:
    context = getattr(request.state, "auth_context", None)
    session_id = getattr(context, "session_id", None)
    if not isinstance(session_id, UUID):
        raise ApiProblem(
            status=401,
            title="Authentication required",
            detail="A valid session is required.",
            code="unauthenticated",
        )
    return session_id


@router.get("/ai/settings", tags=["AI"])
@requires("data.view")
def get_ai_settings(request: Request) -> Response:
    """Masked settings: the key itself is never returned, only a four-character hint."""

    view = _service(request).view(_actor(request))
    return _respond(view, version=int(view["version"]))


@router.put("/ai/settings", tags=["AI"])
@requires("ai.manage")
def put_ai_settings(body: AiSettingsUpdate, request: Request) -> Response:
    expected = require_if_match(request.headers.get("if-match"))
    try:
        view = _service(request).update(
            _actor(request),
            session_id=_session_id(request),
            expected_version=expected,
            body=body,
        )
    except AiSettingsError as exc:
        raise _problem(exc) from None
    return _respond(view, version=int(view["version"]))


@router.get("/ai/consent-preview", tags=["AI"])
@requires("ai.manage")
def get_ai_consent_preview(
    request: Request,
    mode: Literal["ai_assisted", "ai_first"],
    model: Annotated[str, Query(max_length=64)],
    vision: bool = False,
) -> Response:
    """Consent text, per-hook data list and fingerprint for settings not yet saved.

    ``vision=true`` previews the consent with page images on (the vision reader
    replaces PDF text suggestions).
    """

    try:
        return _respond(
            _service(request).consent_preview(
                _actor(request), mode=mode, model=model, vision=vision
            )
        )
    except AiSettingsError as exc:
        raise _problem(exc) from None


@router.delete("/ai/key", tags=["AI"])
@requires("ai.manage")
def delete_ai_key(request: Request) -> Response:
    expected = require_if_match(request.headers.get("if-match"))
    try:
        view = _service(request).remove_key(_actor(request), expected_version=expected)
    except AiSettingsError as exc:
        raise _problem(exc) from None
    return _respond(view, version=int(view["version"]))


@router.post("/ai/test/provider", tags=["AI"], status_code=202)
@requires("ai.manage")
def test_ai_provider(request: Request) -> Response:
    """Queue one synthetic-fixture call; the AI worker sends it if the gate allows."""

    try:
        result = _service(request).request_provider_test(
            _actor(request), ip=request.client.host if request.client else None
        )
    except AiSettingsError as exc:
        raise _problem(exc) from None
    return _respond(result, status_code=202)


@router.get("/ai/requests/{request_id}", tags=["AI"])
@requires("ai.manage")
def get_ai_request(request_id: UUID, request: Request) -> Response:
    try:
        return _respond(_service(request).request_state(_actor(request), request_id))
    except AiSettingsError as exc:
        raise _problem(exc) from None


@router.get("/ai/usage", tags=["AI"])
@requires("ai.manage")
def get_ai_usage(request: Request) -> Response:
    return _respond(_service(request).usage(_actor(request)))


@router.post("/ai/requests/{request_id}/acknowledge", tags=["AI"])
@requires("ai.manage")
def acknowledge_ai_request(request_id: UUID, request: Request) -> Response:
    """Accept an uncertain (possibly billed) call; this allows exactly one new attempt."""

    try:
        return _respond(_service(request).acknowledge(_actor(request), request_id))
    except AiSettingsError as exc:
        raise _problem(exc) from None


__all__ = ["router"]
