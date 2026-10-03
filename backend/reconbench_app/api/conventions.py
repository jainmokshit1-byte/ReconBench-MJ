"""Reusable HTTP conventions for ReconBench API resources and jobs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Request
from pydantic import BaseModel, ConfigDict, PlainSerializer
from starlette.responses import Response

from reconbench_app.db.repository import parse_if_match, version_etag
from reconbench_app.json_serialization import DecimalJSONResponse, decimal_to_string, to_jsonable


class ProblemFieldError(BaseModel):
    """A safe field-level issue; raw submitted values are never returned."""

    location: list[str | int]
    code: str
    detail: str


class ProblemDetails(BaseModel):
    """RFC 9457 problem document with ReconBench's stable machine code."""

    model_config = ConfigDict(extra="allow")

    type: str = "about:blank"
    title: str
    status: int
    detail: str
    instance: str
    code: str
    errors: list[ProblemFieldError] | None = None
    current: dict[str, Any] | None = None


class ApiProblem(Exception):
    """A deliberate API error rendered as Problem Details."""

    def __init__(
        self,
        *,
        status: int,
        title: str,
        detail: str,
        code: str,
        errors: Sequence[ProblemFieldError] = (),
        extensions: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail
        self.code = code
        self.errors = list(errors)
        self.extensions = dict(extensions or {})
        self.headers = dict(headers or {})


def problem_response(
    *,
    status: int,
    title: str,
    detail: str,
    code: str,
    instance: str,
    errors: Sequence[ProblemFieldError] = (),
    extensions: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> DecimalJSONResponse:
    """Build a shared RFC 9457 response without including submitted field values."""

    content: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": instance,
        "code": code,
    }
    if errors:
        content["errors"] = [error.model_dump(mode="json") for error in errors]
    content.update(to_jsonable(extensions or {}))
    return DecimalJSONResponse(
        status_code=status,
        media_type="application/problem+json",
        headers=dict(headers or {}),
        content=content,
    )


def validation_errors(errors: Sequence[Mapping[str, Any]]) -> list[ProblemFieldError]:
    """Map framework validation details to stable, non-sensitive field errors."""

    result: list[ProblemFieldError] = []
    for item in errors:
        raw_location = item.get("loc", ())
        location = [part for part in raw_location if part != "body"]
        result.append(
            ProblemFieldError(
                location=[part if isinstance(part, int) else str(part) for part in location],
                code="invalid_field",
                detail="This field is invalid.",
            )
        )
    return result


def etag_for_version(version: int) -> str:
    """Return the strong ETag convention used by versioned resources."""

    return version_etag(version)


def require_if_match(header_value: str | None) -> int:
    """Require a strong integer If-Match header or raise a stable API error."""

    if header_value is None:
        raise ApiProblem(
            status=428,
            title="Precondition required",
            detail="If-Match is required for this update.",
            code="if_match_required",
        )
    try:
        return parse_if_match(header_value)
    except ValueError as exc:
        raise ApiProblem(
            status=400,
            title="Invalid request",
            detail="If-Match must contain a valid strong resource ETag.",
            code="invalid_if_match",
        ) from exc


def if_none_match_matches(header_value: str | None, etag: str) -> bool:
    """Apply GET/HEAD weak comparison semantics for If-None-Match."""

    if header_value is None:
        return False
    if header_value.strip() == "*":
        return True
    target = etag.removeprefix("W/")
    return any(
        candidate.strip().removeprefix("W/") == target for candidate in header_value.split(",")
    )


def conditional_json_response(request: Request, content: Any, etag: str) -> Response:
    """Return 304 for a matching If-None-Match, otherwise JSON with its ETag."""

    headers = {"ETag": etag}
    if if_none_match_matches(request.headers.get("If-None-Match"), etag):
        return Response(status_code=304, headers=headers)
    return DecimalJSONResponse(content=content, headers=headers)


def accepted_job_response(
    *,
    resource_url: str,
    content: Mapping[str, Any],
) -> DecimalJSONResponse:
    """Return the common 202 body and Location link for a durable job/resource."""

    if not resource_url.startswith("/api/v1/") or resource_url.startswith("//"):
        raise ValueError("job resource URLs must be same-origin paths under /api/v1")
    return DecimalJSONResponse(
        status_code=202,
        headers={"Location": resource_url},
        content=dict(content),
    )


Money = Annotated[Decimal, PlainSerializer(decimal_to_string, return_type=str, when_used="json")]
Quantity = Money
Rate = Money


def iso_date(value: date | datetime) -> str:
    """Serialize date values using ISO 8601 without locale formatting."""

    return value.isoformat()


__all__ = [
    "ApiProblem",
    "Money",
    "ProblemDetails",
    "ProblemFieldError",
    "Quantity",
    "Rate",
    "accepted_job_response",
    "conditional_json_response",
    "etag_for_version",
    "if_none_match_matches",
    "iso_date",
    "problem_response",
    "require_if_match",
    "validation_errors",
]
