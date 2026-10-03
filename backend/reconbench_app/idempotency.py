"""Transactional, 24-hour idempotency support backed by the existing table."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from reconbench_app.api.conventions import ApiProblem
from reconbench_app.db.ids import new_id
from reconbench_app.db.metadata import metadata
from reconbench_app.json_serialization import DecimalJSONResponse, to_jsonable

IDEMPOTENCY_TTL = timedelta(hours=24)
_MAX_KEY_LENGTH = 200
_AUTH_ENDPOINTS = frozenset(
    {
        "POST /auth/login",
        "POST /auth/logout",
        "POST /api/v1/auth/login",
        "POST /api/v1/auth/logout",
    }
)


class IdempotencyKeyConflict(Exception):
    """The same key was submitted with a different canonical request body."""


class IdempotencyInProgress(Exception):
    """An earlier request committed a pending idempotency record."""


@dataclass(frozen=True, slots=True)
class IdempotentResult:
    status_code: int
    body: Any
    resource_type: str | None = None
    resource_id: UUID | None = None
    replayed: bool = False


def idempotent_json_response(result: IdempotentResult) -> DecimalJSONResponse:
    """Rebuild the stored result and deterministic Location for a 202 resource."""

    headers: dict[str, str] = {}
    if result.status_code == 202:
        if not result.resource_type or result.resource_id is None:
            raise ValueError("202 idempotent responses require a durable resource reference")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", result.resource_type):
            raise ValueError("idempotent resource_type must be a URL-safe path segment")
        headers["Location"] = f"/api/v1/{result.resource_type}/{result.resource_id}"
    return DecimalJSONResponse(
        status_code=result.status_code,
        headers=headers,
        content=to_jsonable(result.body),
    )


def validate_idempotency_key(value: str | None) -> str:
    """Normalize and validate an Idempotency-Key request header."""

    if value is None:
        raise ApiProblem(
            status=400,
            title="Invalid request",
            detail="Idempotency-Key is required for this operation.",
            code="idempotency_key_required",
        )
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > _MAX_KEY_LENGTH
        or any(ord(character) < 0x21 or ord(character) == 0x7F for character in normalized)
    ):
        raise ApiProblem(
            status=400,
            title="Invalid request",
            detail=f"Idempotency-Key must contain 1–{_MAX_KEY_LENGTH} visible characters.",
            code="invalid_idempotency_key",
        )
    return normalized


def execute_idempotent(
    session: Session,
    *,
    workspace_id: UUID,
    user_id: UUID,
    endpoint: str,
    key: str,
    request_body: Any,
    operation: Callable[[Session], IdempotentResult],
    now: datetime | None = None,
) -> IdempotentResult:
    """Execute and store one result in the same transaction as its side effects.

    Callers own a top-level transaction boundary. A repeated scoped key replays its
    original status/body/resource; a changed body conflicts. Expired keys can be
    reused. Login and logout are explicitly excluded from this convention.
    """

    if session.in_transaction():
        raise RuntimeError("idempotent operations require a session with no active transaction")
    endpoint_parts = endpoint.strip().split()
    method = endpoint_parts[0].upper() if endpoint_parts else ""
    endpoint_target = " ".join(endpoint_parts[1:])
    normalized_endpoint = f"{method} {endpoint_target}" if endpoint_target else ""
    route_path = urlsplit(endpoint_target).path.rstrip("/") or "/"
    if f"{method} {route_path}" in _AUTH_ENDPOINTS:
        raise ValueError("Idempotency-Key must not be applied to login or logout")
    if not normalized_endpoint or len(normalized_endpoint) > 512:
        raise ValueError("endpoint must be a non-empty route identifier")
    normalized_key = validate_idempotency_key(key)
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("idempotency timestamps must be timezone-aware")
    current_time = current_time.astimezone(UTC)
    expires_at = current_time + IDEMPOTENCY_TTL
    request_hash = _request_hash(request_body)
    table = metadata.tables["idempotency_key"]

    with session.begin():
        scope = sa.and_(
            table.c.workspace_id == workspace_id,
            table.c.user_id == user_id,
            table.c.endpoint == normalized_endpoint,
            table.c.idem_key == normalized_key,
        )
        session.execute(sa.delete(table).where(scope, table.c.expires_at <= current_time))
        inserted_id = session.scalar(
            pg_insert(table)
            .values(
                id=new_id(),
                workspace_id=workspace_id,
                user_id=user_id,
                idem_key=normalized_key,
                endpoint=normalized_endpoint,
                request_hash=request_hash,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    table.c.workspace_id,
                    table.c.user_id,
                    table.c.endpoint,
                    table.c.idem_key,
                ]
            )
            .returning(table.c.id)
        )

        if inserted_id is not None:
            result = operation(session)
            if not 100 <= result.status_code <= 599:
                raise ValueError("idempotent response status must be a valid HTTP status")
            session.execute(
                sa.update(table)
                .where(table.c.id == inserted_id)
                .values(
                    response_status=result.status_code,
                    response_body=to_jsonable(result.body),
                    resource_type=result.resource_type,
                    resource_id=result.resource_id,
                )
            )
            return IdempotentResult(
                result.status_code,
                to_jsonable(result.body),
                result.resource_type,
                result.resource_id,
                replayed=False,
            )

        existing = (
            session.execute(sa.select(table).where(scope).with_for_update())
            .mappings()
            .one_or_none()
        )
        if existing is None:
            raise IdempotencyInProgress("the idempotent result is being committed")
        if existing["request_hash"] != request_hash:
            raise IdempotencyKeyConflict("Idempotency-Key was already used for a different request")
        if existing["response_status"] is None:
            raise IdempotencyInProgress("the idempotent request is still in progress")
        return IdempotentResult(
            int(existing["response_status"]),
            existing["response_body"],
            existing["resource_type"],
            existing["resource_id"],
            replayed=True,
        )


def _request_hash(request_body: Any) -> str:
    normalized = to_jsonable(request_body)
    canonical = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "IDEMPOTENCY_TTL",
    "IdempotencyInProgress",
    "IdempotencyKeyConflict",
    "IdempotentResult",
    "execute_idempotent",
    "idempotent_json_response",
    "validate_idempotency_key",
]
