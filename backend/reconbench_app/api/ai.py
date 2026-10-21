"""AI Assist settings, consent, key, provider-test, usage and offline self-test endpoints.

``POST /ai/test`` is the offline guardrail self-test and never calls a provider.
Provider calls happen only in the AI worker; these endpoints enqueue and report.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

import sqlalchemy as sa
from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import Response

from reconbench_app.ai.provider_connection import (
    ConnectBody,
    ProviderConnectionService,
    RotateBody,
)
from reconbench_app.ai.settings_service import (
    AiSettingsError,
    AiSettingsService,
    AiSettingsUpdate,
    require_ai_manage,
)
from reconbench_app.ai_assist import run_stub_guardrail_test
from reconbench_app.api.conventions import ApiProblem, etag_for_version, require_if_match
from reconbench_app.api.reconciliation import _actor
from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable

router = APIRouter()


class ProviderTestBody(BaseModel):
    """Which provider to test; the default is today's Gemini text test."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["gemini", "vllm"] = "gemini"
    kind: Literal["text", "image"] = "text"


_TEST_LIMIT = 5
_TEST_WINDOW = timedelta(minutes=1)


@router.post("/ai/test", tags=["AI"])
@requires("ai.manage")
def test_ai_guardrails(request: Request) -> Response:
    """Run bounded local fixtures; this endpoint never calls an external provider."""

    actor = _actor(request)
    try:
        require_ai_manage(request.app.state.database_engine, actor, attempted="guardrail_test")
    except AiSettingsError as exc:
        raise _problem(exc) from None
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


def _connections(request: Request) -> ProviderConnectionService:
    service = getattr(request.app.state, "ai_provider_connection_service", None)
    if not isinstance(service, ProviderConnectionService):
        raise RuntimeError("AI provider connection service is unavailable")
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
    auto_verify_field_types: Annotated[
        list[Literal["doc_date", "doc_no", "gstin", "line_amount", "reference"]] | None,
        Query(),
    ] = None,
    reader_strategy: Literal["off", "shadow", "single"] | None = None,
    primary_reader: Literal["gemini", "vllm"] | None = None,
    vllm_model: Annotated[str | None, Query(max_length=128)] = None,
    vllm_base_url: Annotated[str | None, Query(max_length=512)] = None,
    apply_mode: Literal["suggest", "merge"] = "suggest",
) -> Response:
    """Consent text, per-hook data list and fingerprint for settings not yet saved.

    ``vision=true`` previews the consent with page images on (the vision reader
    replaces PDF text suggestions). ``reader_strategy``/``primary_reader`` choose
    the one vision provider; for the company server pass its model and base URL
    (checked against the deployment allowlist).
    """

    try:
        return _respond(
            _service(request).consent_preview(
                _actor(request),
                mode=mode,
                model=model,
                vision=vision,
                auto_verify_field_types=tuple(auto_verify_field_types or ()),
                reader_strategy=reader_strategy,
                primary_reader=primary_reader,
                vllm_model=vllm_model,
                vllm_base_url=vllm_base_url,
                apply_mode=apply_mode,
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
def test_ai_provider(
    request: Request, body: Annotated[ProviderTestBody | None, Body()] = None
) -> Response:
    """Queue one synthetic-fixture call; the AI worker sends it if the gate allows."""

    choice = body or ProviderTestBody()
    try:
        result = _service(request).request_provider_test(
            _actor(request),
            ip=request.client.host if request.client else None,
            provider=choice.provider,
            kind=choice.kind,
        )
    except AiSettingsError as exc:
        raise _problem(exc) from None
    return _respond(result, status_code=202)


@router.get("/ai/providers/{provider}/connect-notice", tags=["AI"])
@requires("ai.manage")
def get_provider_connect_notice(
    provider: Literal["gemini", "vllm"],
    request: Request,
    tier: Literal["free", "paid"] | None = None,
    model: Annotated[str | None, Query(max_length=128)] = None,
    base_url: Annotated[str | None, Query(max_length=512)] = None,
) -> Response:
    """The notice an admin accepts when connecting this provider, with its fingerprint.

    For Gemini the notice depends on the declared key tier; a free-tier key's notice states
    the data use explicitly. Nothing is written.
    """

    try:
        return _respond(
            _connections(request).connect_notice(
                _actor(request), provider=provider, tier=tier, model=model, base_url=base_url
            )
        )
    except AiSettingsError as exc:
        raise _problem(exc) from None


@router.post("/ai/providers/{provider}/connect", tags=["AI"], status_code=202)
@requires("ai.manage")
def connect_ai_provider(
    provider: Literal["gemini", "vllm"], body: ConnectBody, request: Request
) -> Response:
    """Save the credentials, record the accepted notice and queue the connection test.

    The card is Not connected until the AI worker's test passes (poll
    ``GET /ai/requests/{id}`` and ``GET /ai/settings``). The key is write-only.
    """

    try:
        result = _connections(request).connect(
            _actor(request),
            session_id=_session_id(request),
            provider=provider,
            body=body,
            ip=request.client.host if request.client else None,
        )
    except AiSettingsError as exc:
        raise _problem(exc) from None
    return _respond(result, status_code=202, version=int(result["settings"]["version"]))


@router.post("/ai/providers/{provider}/rotate", tags=["AI"], status_code=202)
@requires("ai.manage")
def rotate_ai_provider_key(
    provider: Literal["gemini", "vllm"], body: RotateBody, request: Request
) -> Response:
    """Test a new key first; the old key stays active until the test passes."""

    try:
        result = _connections(request).rotate(
            _actor(request),
            session_id=_session_id(request),
            provider=provider,
            body=body,
            ip=request.client.host if request.client else None,
        )
    except AiSettingsError as exc:
        raise _problem(exc) from None
    return _respond(result, status_code=202, version=int(result["settings"]["version"]))


@router.post("/ai/providers/{provider}/disconnect", tags=["AI"])
@requires("ai.manage")
def disconnect_ai_provider(provider: Literal["gemini", "vllm"], request: Request) -> Response:
    """Delete the card's keys; a request still in flight is discarded, never applied."""

    try:
        view = _connections(request).disconnect(_actor(request), provider=provider)
    except AiSettingsError as exc:
        raise _problem(exc) from None
    return _respond(view, version=int(view["version"]))


@router.get("/ai/requests/{request_id}", tags=["AI"])
@requires("ai.manage")
def get_ai_request(request_id: UUID, request: Request) -> Response:
    try:
        return _respond(_service(request).request_state(_actor(request), request_id))
    except AiSettingsError as exc:
        raise _problem(exc) from None


@router.get("/ai/usage", tags=["AI"])
@requires("ai.manage")
def get_ai_usage(request: Request, provider: Literal["gemini", "vllm"] | None = None) -> Response:
    return _respond(_service(request).usage(_actor(request), provider=provider))


@router.get("/ai/shadow-report", tags=["AI"])
@requires("ai.manage")
def get_ai_shadow_report(request: Request, since: date | None = None) -> Response:
    """Shadow reader aggregates: agreement labels per provider and field, latency and
    failure codes. Never a value or a document."""

    return _respond(_service(request).shadow_report(_actor(request), since=since))


@router.post("/ai/requests/{request_id}/acknowledge", tags=["AI"])
@requires("ai.manage")
def acknowledge_ai_request(request_id: UUID, request: Request) -> Response:
    """Accept an uncertain (possibly billed) call; this allows exactly one new attempt."""

    try:
        return _respond(_service(request).acknowledge(_actor(request), request_id))
    except AiSettingsError as exc:
        raise _problem(exc) from None


__all__ = ["router"]
