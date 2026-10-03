"""Offline-only AI guardrail self-test endpoint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from fastapi import APIRouter, Request
from starlette.responses import Response

from reconbench_app.ai_assist import run_stub_guardrail_test
from reconbench_app.api.conventions import ApiProblem
from reconbench_app.api.reconciliation import _actor
from reconbench_app.authorization import requires
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.json_serialization import DecimalJSONResponse

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


__all__ = ["router"]
