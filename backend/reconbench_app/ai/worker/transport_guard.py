"""Single-shot transport boundary for Gemini provider attempts."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from threading import Lock
from types import MappingProxyType
from typing import Literal

import httpx
from google import genai
from google.genai import types

DeliveryCertainty = Literal["not_sent", "may_have_been_sent"]

_RETRYABLE_HTTP_STATUSES = frozenset({408, 409, 429})
_MAX_ERROR_BODY_BYTES = 8_192
_MIN_TIMEOUT_MS = 1_000
_MAX_TIMEOUT_MS = 120_000
# google.rpc-style ``error.status`` values.
_SAFE_PROVIDER_STATUSES = frozenset(
    {
        "DEADLINE_EXCEEDED",
        "FAILED_PRECONDITION",
        "INTERNAL",
        "INVALID_ARGUMENT",
        "NOT_FOUND",
        "PERMISSION_DENIED",
        "QUOTA_EXCEEDED",
        "QUOTA_EXHAUSTED",
        "RESOURCE_EXHAUSTED",
        "UNAUTHENTICATED",
        "UNAVAILABLE",
    }
)
_SAFE_PROVIDER_REASONS = frozenset(
    {
        "QUOTA_EXCEEDED",
        "QUOTA_EXHAUSTED",
        "RATE_LIMIT_EXCEEDED",
        "USER_RATE_LIMIT_EXCEEDED",
    }
)
# String ``error.code`` values from Google's current Gemini API error reference.
_SAFE_PROVIDER_CODES = frozenset(
    {
        "api_error",
        "authentication",
        "deadline_exceeded",
        "failed_precondition",
        "invalid_request",
        "model_not_found",
        "not_found",
        "payment_required",
        "permission_denied",
        "quota_exceeded",
        "rate_limit_exceeded",
        "service_unavailable",
    }
)
_QUOTA_MARKERS = frozenset({"QUOTA_EXCEEDED", "QUOTA_EXHAUSTED", "quota_exceeded"})
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z", re.ASCII)


class ProviderAttemptFinished(RuntimeError):
    """A provider returned a retryable HTTP status; the attempt must not repeat."""

    def __init__(self, status_code: int, safe_metadata: Mapping[str, str]) -> None:
        self.status_code = status_code
        self.delivery: DeliveryCertainty = "may_have_been_sent"
        self.safe_metadata = MappingProxyType(dict(safe_metadata))
        super().__init__(f"provider attempt finished with HTTP {status_code}")


class ProviderTransportFailure(RuntimeError):
    """A transport error with conservative delivery certainty and safe type only."""

    def __init__(self, *, delivery: DeliveryCertainty, failure_kind: str) -> None:
        self.delivery = delivery
        self.failure_kind = failure_kind
        super().__init__(f"provider transport failed ({failure_kind})")


class DuplicateAttemptBlocked(RuntimeError):
    """Raised before a guarded client can dispatch a second HTTP request."""

    def __init__(self) -> None:
        super().__init__("duplicate provider attempt blocked")


class AsyncProviderPathBlocked(RuntimeError):
    """Raised before any I/O when SDK async methods are used on a guarded client."""

    def __init__(self) -> None:
        super().__init__("async provider calls are disabled; use the guarded sync client")


class SingleAttemptTransport(httpx.BaseTransport):
    """Allow one inner request and convert retryable results into permanent errors."""

    def __init__(self, inner_transport: httpx.BaseTransport) -> None:
        self._inner_transport = inner_transport
        self._attempt_started = False
        self._attempt_lock = Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        with self._attempt_lock:
            if self._attempt_started:
                raise DuplicateAttemptBlocked()
            self._attempt_started = True

        try:
            response = self._inner_transport.handle_request(request)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise ProviderTransportFailure(
                delivery="not_sent", failure_kind=type(exc).__name__
            ) from None
        except httpx.HTTPError as exc:
            raise ProviderTransportFailure(
                delivery="may_have_been_sent", failure_kind=type(exc).__name__
            ) from None
        except Exception as exc:  # noqa: BLE001 - any other failure is still a delivery unknown.
            raise ProviderTransportFailure(
                delivery="may_have_been_sent", failure_kind=type(exc).__name__
            ) from None

        if response.status_code in _RETRYABLE_HTTP_STATUSES or response.status_code >= 500:
            status_code = response.status_code
            metadata = _safe_response_metadata(response)
            with suppress(httpx.HTTPError):
                response.close()
            raise ProviderAttemptFinished(status_code, metadata)

        return response

    def close(self) -> None:
        self._inner_transport.close()


class BlockedAsyncTransport(httpx.AsyncBaseTransport):
    """Fail closed for the SDK's async client, which the single-shot guard does not cover."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        del request
        raise AsyncProviderPathBlocked()


def safe_provider_error_metadata(error_body: object) -> Mapping[str, str]:
    """Extract a short allowlist of provider codes; omit messages and arbitrary text."""

    if not isinstance(error_body, Mapping):
        return MappingProxyType({})
    error = error_body.get("error")
    if not isinstance(error, Mapping):
        return MappingProxyType({})

    safe: dict[str, str] = {}
    status = error.get("status")
    if isinstance(status, str) and status in _SAFE_PROVIDER_STATUSES:
        safe["provider_status"] = status

    code = error.get("code")
    if isinstance(code, str) and code in _SAFE_PROVIDER_CODES:
        safe["provider_code"] = code

    details = error.get("details")
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, Mapping):
                continue
            reason = detail.get("reason")
            metadata = detail.get("metadata")
            if not isinstance(reason, str) and isinstance(metadata, Mapping):
                reason = metadata.get("reason")
            if isinstance(reason, str) and reason in _SAFE_PROVIDER_REASONS:
                safe["provider_reason"] = reason
                break
    return MappingProxyType(safe)


def _safe_response_metadata(response: httpx.Response) -> Mapping[str, str]:
    safe = dict(safe_provider_error_metadata(_read_error_body_prefix(response)))
    request_id = response.headers.get("x-goog-request-id") or response.headers.get("x-request-id")
    if request_id is not None and _REQUEST_ID_RE.fullmatch(request_id):
        safe["request_id"] = request_id
    return MappingProxyType(safe)


def _read_error_body_prefix(response: httpx.Response) -> object:
    """Read at most a small JSON prefix for safe status/reason codes only."""

    prefix = bytearray()
    try:
        for chunk in response.iter_bytes():
            remaining = _MAX_ERROR_BODY_BYTES - len(prefix)
            if remaining <= 0:
                break
            prefix.extend(chunk[:remaining])
            if len(chunk) > remaining:
                break
    except (httpx.HTTPError, RuntimeError, ValueError):
        return {}
    try:
        parsed = json.loads(prefix)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


@dataclass(frozen=True, slots=True)
class ProviderErrorMapping:
    """Sanitized error code and optional cooldown, with no provider message."""

    code: str
    delivery: DeliveryCertainty = "may_have_been_sent"
    cooldown_seconds: int | None = None


def map_provider_error(
    status_code: int,
    safe_metadata: Mapping[str, str] | None = None,
) -> ProviderErrorMapping:
    """Map Gemini statuses to the sanitized codes in AI Assist Plan §5.4.

    Accepts both google.rpc-style bodies (``error.status`` plus detail reasons)
    and the string ``error.code`` values from the current Gemini error reference.
    """

    metadata = safe_metadata or {}
    provider_code = metadata.get("provider_code")
    if status_code == 400:
        if (
            metadata.get("provider_status") == "FAILED_PRECONDITION"
            or provider_code == "failed_precondition"
        ):
            return ProviderErrorMapping("BILLING_OR_PREREQUISITE")
        return ProviderErrorMapping("INVALID_REQUEST")
    if status_code == 401:
        return ProviderErrorMapping("bad_key")
    if status_code == 402:
        return ProviderErrorMapping("PREPAY_DEPLETED")
    if status_code == 403:
        return ProviderErrorMapping("PERMISSION_DENIED")
    if status_code == 404:
        if provider_code == "not_found":
            return ProviderErrorMapping("NOT_FOUND")
        return ProviderErrorMapping("MODEL_UNAVAILABLE")
    if status_code in {408, 504}:
        return ProviderErrorMapping("timeout")
    if status_code == 429:
        markers = {
            metadata.get("provider_reason"),
            metadata.get("provider_status"),
            provider_code,
        }
        code = "QUOTA_EXHAUSTED" if markers & _QUOTA_MARKERS else "rate_limited"
        return ProviderErrorMapping(code, cooldown_seconds=60)
    if status_code in {500, 503}:
        return ProviderErrorMapping("PROVIDER_UNAVAILABLE")
    return ProviderErrorMapping("error")


def map_provider_http_exception(error: Exception) -> ProviderErrorMapping:
    """Map an SDK HTTP exception using only the sanitized fields in its body."""

    status_code = getattr(error, "status_code", None)
    body = getattr(error, "body", None)
    if not isinstance(status_code, int):
        return ProviderErrorMapping("error")
    return map_provider_error(status_code, safe_provider_error_metadata(body))


def map_provider_transport_failure(
    failure: ProviderTransportFailure,
) -> ProviderErrorMapping:
    """Map a transport failure without exposing its underlying exception text."""

    if failure.failure_kind in {
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
    }:
        return ProviderErrorMapping("timeout", delivery=failure.delivery)
    return ProviderErrorMapping("PROVIDER_UNAVAILABLE", delivery=failure.delivery)


def safe_log_fields(error: BaseException) -> Mapping[str, str]:
    """Return the only provider-error fields that may be logged.

    SDK exception text can contain the provider's message, so callers must log
    this mapping and never ``str(error)``.
    """

    fields: dict[str, str] = {"error_type": type(error).__name__}
    sdk_status_code = getattr(error, "status_code", None)
    if isinstance(error, ProviderAttemptFinished):
        mapping = map_provider_error(error.status_code, error.safe_metadata)
        fields.update(error.safe_metadata)
        fields["status_code"] = str(error.status_code)
    elif isinstance(error, ProviderTransportFailure):
        mapping = map_provider_transport_failure(error)
        fields["failure_kind"] = error.failure_kind
    elif isinstance(error, (DuplicateAttemptBlocked, AsyncProviderPathBlocked)):
        mapping = ProviderErrorMapping("error")
    elif isinstance(sdk_status_code, int) and isinstance(error, Exception):
        mapping = map_provider_http_exception(error)
        fields.update(safe_provider_error_metadata(getattr(error, "body", None)))
        fields["status_code"] = str(sdk_status_code)
    else:
        mapping = ProviderErrorMapping("error")
    fields["code"] = mapping.code
    fields["delivery"] = mapping.delivery
    return MappingProxyType(fields)


@contextmanager
def new_gemini_client(
    api_key: str,
    *,
    timeout_ms: int,
    inner_transport: httpx.BaseTransport | None = None,
) -> Iterator[genai.Client]:
    """Create a fresh SDK client whose transport permits one provider request.

    ``timeout_ms`` is required so production calls always use the configured
    bound. ``inner_transport`` is exposed for deterministic tests; production
    calls use an ``HTTPTransport(retries=0)`` under the request-count guard.
    SDK async methods (``client.aio``) are blocked before any I/O.
    """

    if not _MIN_TIMEOUT_MS <= timeout_ms <= _MAX_TIMEOUT_MS:
        raise ValueError(f"timeout_ms must be between {_MIN_TIMEOUT_MS} and {_MAX_TIMEOUT_MS}")
    inner = httpx.HTTPTransport(retries=0) if inner_transport is None else inner_transport
    guarded_transport = SingleAttemptTransport(inner)
    http_client = httpx.Client(transport=guarded_transport)
    # The blocked async transport holds no sockets, so this client needs no close.
    blocked_async_client = httpx.AsyncClient(transport=BlockedAsyncTransport())
    try:
        sdk_client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                api_version="v1",
                timeout=timeout_ms,
                retry_options=types.HttpRetryOptions(attempts=1),
                httpx_client=http_client,
                httpx_async_client=blocked_async_client,
            ),
        )
        yield sdk_client
    finally:
        http_client.close()


__all__ = [
    "AsyncProviderPathBlocked",
    "BlockedAsyncTransport",
    "DuplicateAttemptBlocked",
    "ProviderAttemptFinished",
    "ProviderErrorMapping",
    "ProviderTransportFailure",
    "SingleAttemptTransport",
    "map_provider_error",
    "map_provider_http_exception",
    "map_provider_transport_failure",
    "new_gemini_client",
    "safe_log_fields",
    "safe_provider_error_metadata",
]
