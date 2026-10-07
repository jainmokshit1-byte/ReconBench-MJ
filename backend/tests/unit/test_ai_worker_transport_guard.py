from __future__ import annotations

import asyncio
import json
from importlib.metadata import version
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from google.genai._gaos.lib import compat_errors
from reconbench_app.ai.worker.transport_guard import (
    AsyncProviderPathBlocked,
    DuplicateAttemptBlocked,
    ProviderAttemptFinished,
    ProviderTransportFailure,
    SingleAttemptTransport,
    map_provider_error,
    map_provider_http_exception,
    map_provider_transport_failure,
    new_gemini_client,
    safe_log_fields,
    safe_provider_error_metadata,
)

MODEL = "gemini-3.8-flash"
API_KEY = "synthetic-test-key"
TIMEOUT_MS = 60_000
PROVIDER_MESSAGE = "synthetic provider error; must not leak"
RESPONSE_FORMAT = {
    "type": "text",
    "mime_type": "application/json",
    "schema": {
        "type": "object",
        "properties": {"fixture": {"type": "string"}},
        "required": ["fixture"],
        "additionalProperties": False,
    },
}
FORBIDDEN_BODY_FIELDS = (
    "contents",
    "generationConfig",
    "responseMimeType",
    "responseSchema",
    "responseJsonSchema",
    "tools",
    "stream",
    "previous_interaction_id",
)


def _legacy_body(status_code: int, status: str, reason: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": status_code, "message": PROVIDER_MESSAGE, "status": status}
    if reason is not None:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
    return {"error": error}


def _current_body(code: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": PROVIDER_MESSAGE}}


def _json_response(status_code: int, body: dict[str, Any], **headers: str) -> httpx.Response:
    return httpx.Response(
        status_code, headers={"content-type": "application/json", **headers}, json=body
    )


def _assert_request_protocol(request: httpx.Request) -> None:
    assert request.method == "POST"
    assert request.url.path == "/v1/interactions"
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)
    assert "key=" not in str(request.url)
    raw_body = request.read()
    assert API_KEY.encode() not in raw_body
    body = json.loads(raw_body)
    assert body["store"] is False
    assert body["response_format"] == RESPONSE_FORMAT
    assert body["generation_config"] == {"max_output_tokens": 128, "thinking_level": "low"}
    assert "response_format" not in body["generation_config"]
    for field in FORBIDDEN_BODY_FIELDS:
        assert field not in body


def _create_interaction(client: Any) -> Any:
    return client.interactions.create(
        model=MODEL,
        input="Synthetic fixture only.",
        system_instruction="Return only the requested synthetic JSON.",
        response_format=RESPONSE_FORMAT,
        generation_config={"max_output_tokens": 128, "thinking_level": "low"},
        store=False,
    )


def _completed_interaction() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "synthetic-id",
            "model": MODEL,
            "status": "completed",
            "steps": [{"type": "model_output", "text": '{"fixture":"ok"}'}],
            "usage": {
                "total_input_tokens": 11,
                "total_output_tokens": 4,
                "total_thought_tokens": 2,
            },
        },
    )


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> tuple[Mock, AsyncMock]:
    sleep = Mock(side_effect=AssertionError("SDK must not sleep before retrying"))
    async_sleep = AsyncMock(side_effect=AssertionError("SDK must not sleep before retrying"))
    monkeypatch.setattr("time.sleep", sleep)
    monkeypatch.setattr("asyncio.sleep", async_sleep)
    return sleep, async_sleep


def test_transport_guard_is_pinned_to_sdk_version() -> None:
    assert version("google-genai") == "2.28.0"


# Each case: HTTP status, legacy google.rpc body, current string-code body (or
# None when that format has no equivalent), expected sanitized code, and
# whether the guard (rather than the SDK) ends the attempt.
_STATUS_CASES: list[tuple[int, dict[str, Any] | None, dict[str, Any] | None, str, bool]] = [
    (
        400,
        _legacy_body(400, "INVALID_ARGUMENT"),
        _current_body("invalid_request"),
        "INVALID_REQUEST",
        False,
    ),
    (
        400,
        _legacy_body(400, "FAILED_PRECONDITION"),
        _current_body("failed_precondition"),
        "BILLING_OR_PREREQUISITE",
        False,
    ),
    (401, _legacy_body(401, "UNAUTHENTICATED"), _current_body("authentication"), "bad_key", False),
    (402, None, _current_body("payment_required"), "PREPAY_DEPLETED", False),
    (
        403,
        _legacy_body(403, "PERMISSION_DENIED"),
        _current_body("permission_denied"),
        "PERMISSION_DENIED",
        False,
    ),
    (
        404,
        _legacy_body(404, "NOT_FOUND"),
        _current_body("model_not_found"),
        "MODEL_UNAVAILABLE",
        False,
    ),
    (404, None, _current_body("not_found"), "NOT_FOUND", False),
    (
        408,
        _legacy_body(408, "DEADLINE_EXCEEDED"),
        _current_body("deadline_exceeded"),
        "timeout",
        True,
    ),
    (409, _legacy_body(409, "ABORTED"), None, "error", True),
    (
        429,
        _legacy_body(429, "RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED"),
        _current_body("quota_exceeded"),
        "QUOTA_EXHAUSTED",
        True,
    ),
    (
        429,
        _legacy_body(429, "RESOURCE_EXHAUSTED", "RATE_LIMIT_EXCEEDED"),
        _current_body("rate_limit_exceeded"),
        "rate_limited",
        True,
    ),
    (500, _legacy_body(500, "INTERNAL"), _current_body("api_error"), "PROVIDER_UNAVAILABLE", True),
    (
        503,
        _legacy_body(503, "UNAVAILABLE"),
        _current_body("service_unavailable"),
        "PROVIDER_UNAVAILABLE",
        True,
    ),
    (
        504,
        _legacy_body(504, "DEADLINE_EXCEEDED"),
        _current_body("deadline_exceeded"),
        "timeout",
        True,
    ),
]
_STATUS_PARAMS = [
    pytest.param(status, body, expected, guarded, id=f"{status}-{fmt}-{expected}")
    for status, legacy, current, expected, guarded in _STATUS_CASES
    for fmt, body in (("legacy", legacy), ("current", current))
    if body is not None
]


@pytest.mark.parametrize(("status_code", "body", "expected_code", "guarded"), _STATUS_PARAMS)
@pytest.mark.usefixtures("no_sleep")
def test_sdk_call_is_single_shot_and_status_is_mapped(
    status_code: int,
    body: dict[str, Any],
    expected_code: str,
    guarded: bool,
) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        _assert_request_protocol(request)
        headers = {"Retry-After": "3600"} if status_code in {429, 503} else {}
        return _json_response(status_code, body, **headers)

    with (
        new_gemini_client(
            API_KEY, timeout_ms=TIMEOUT_MS, inner_transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(Exception) as caught,
    ):
        _create_interaction(client)

    assert request_count == 1
    if guarded:
        assert isinstance(caught.value, ProviderAttemptFinished)
        mapping = map_provider_error(caught.value.status_code, caught.value.safe_metadata)
        assert caught.value.delivery == "may_have_been_sent"
        assert PROVIDER_MESSAGE not in str(caught.value)
    else:
        assert isinstance(caught.value, compat_errors.APIStatusError)
        mapping = map_provider_http_exception(caught.value)
    assert mapping.code == expected_code
    assert mapping.delivery == "may_have_been_sent"
    assert mapping.cooldown_seconds == (60 if status_code == 429 else None)
    logged = safe_log_fields(caught.value)
    assert logged["code"] == expected_code
    assert PROVIDER_MESSAGE not in json.dumps(dict(logged))


@pytest.mark.usefixtures("no_sleep")
def test_sdk_exception_text_can_carry_provider_message_so_only_safe_fields_are_logged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response(400, _legacy_body(400, "INVALID_ARGUMENT"))

    with (
        new_gemini_client(
            API_KEY, timeout_ms=TIMEOUT_MS, inner_transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(compat_errors.APIStatusError) as caught,
    ):
        _create_interaction(client)

    # This is why callers must never log str(error) for SDK exceptions.
    assert PROVIDER_MESSAGE in str(caught.value) or PROVIDER_MESSAGE in str(caught.value.body)
    assert dict(safe_log_fields(caught.value)) == {
        "error_type": type(caught.value).__name__,
        "provider_status": "INVALID_ARGUMENT",
        "status_code": "400",
        "code": "INVALID_REQUEST",
        "delivery": "may_have_been_sent",
    }


def test_retryable_status_metadata_is_allowlisted_and_messages_are_discarded() -> None:
    result = _json_response(429, _legacy_body(429, "RESOURCE_EXHAUSTED", "QUOTA_EXCEEDED"))
    response = httpx.Response(429, content=result.read())
    with (
        SingleAttemptTransport(httpx.MockTransport(lambda _: response)) as transport,
        pytest.raises(ProviderAttemptFinished) as caught,
    ):
        transport.handle_request(httpx.Request("POST", "https://example.test"))

    assert caught.value.safe_metadata == {
        "provider_status": "RESOURCE_EXHAUSTED",
        "provider_reason": "QUOTA_EXCEEDED",
    }
    assert PROVIDER_MESSAGE not in str(caught.value)


def test_unknown_or_non_string_provider_codes_are_dropped() -> None:
    assert safe_provider_error_metadata({"error": {"code": 429, "message": "x"}}) == {}
    assert safe_provider_error_metadata({"error": {"code": "made_up_code"}}) == {}
    assert safe_provider_error_metadata({"error": {"code": "quota_exceeded" * 100}}) == {}
    assert safe_provider_error_metadata({"error": {"status": "NOT_A_STATUS"}}) == {}
    assert safe_provider_error_metadata(["not", "a", "mapping"]) == {}


def test_rate_limit_and_billing_codes_have_safe_status_mappings() -> None:
    rate_limited = map_provider_error(
        429,
        {
            "provider_status": "RESOURCE_EXHAUSTED",
            "provider_reason": "RATE_LIMIT_EXCEEDED",
        },
    )
    prepay_depleted = map_provider_error(402)

    assert rate_limited.code == "rate_limited"
    assert rate_limited.cooldown_seconds == 60
    assert prepay_depleted.code == "PREPAY_DEPLETED"


class _SyntheticSocketFailure(OSError):
    """A non-httpx failure from the inner transport."""


@pytest.mark.parametrize(
    ("exception_type", "delivery", "mapped_code"),
    [
        (httpx.ConnectError, "not_sent", "PROVIDER_UNAVAILABLE"),
        (httpx.ConnectTimeout, "not_sent", "timeout"),
        (httpx.ReadTimeout, "may_have_been_sent", "timeout"),
        (httpx.WriteError, "may_have_been_sent", "PROVIDER_UNAVAILABLE"),
        (httpx.RemoteProtocolError, "may_have_been_sent", "PROVIDER_UNAVAILABLE"),
        (_SyntheticSocketFailure, "may_have_been_sent", "PROVIDER_UNAVAILABLE"),
    ],
)
@pytest.mark.usefixtures("no_sleep")
def test_transport_errors_are_single_shot_and_classified(
    exception_type: type[Exception],
    delivery: str,
    mapped_code: str,
) -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        raise exception_type("synthetic network failure")

    with (
        new_gemini_client(
            API_KEY, timeout_ms=TIMEOUT_MS, inner_transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(ProviderTransportFailure) as caught,
    ):
        _create_interaction(client)

    assert request_count == 1
    assert caught.value.delivery == delivery
    assert caught.value.failure_kind == exception_type.__name__
    assert map_provider_transport_failure(caught.value).code == mapped_code
    assert "synthetic network failure" not in str(caught.value)
    assert safe_log_fields(caught.value)["delivery"] == delivery


def test_duplicate_attempt_is_blocked_before_inner_transport() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200)

    transport = SingleAttemptTransport(httpx.MockTransport(handler))
    request = httpx.Request("GET", "https://example.test")
    transport.handle_request(request)
    with pytest.raises(DuplicateAttemptBlocked):
        transport.handle_request(request)
    assert request_count == 1
    transport.close()


def test_each_client_context_has_its_own_one_attempt_transport() -> None:
    request_counts = [0, 0]

    def make_transport(index: int) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            request_counts[index] += 1
            _assert_request_protocol(request)
            return _completed_interaction()

        return httpx.MockTransport(handler)

    for index in range(2):
        with new_gemini_client(
            API_KEY, timeout_ms=TIMEOUT_MS, inner_transport=make_transport(index)
        ) as client:
            result = _create_interaction(client)
            assert result.status == "completed"
    assert request_counts == [1, 1]


@pytest.mark.parametrize("timeout_ms", [0, 999, 120_001, -1])
def test_timeout_must_be_explicit_and_bounded(timeout_ms: int) -> None:
    with (
        pytest.raises(ValueError, match="timeout_ms"),
        new_gemini_client(API_KEY, timeout_ms=timeout_ms),
    ):
        pass


def test_timeout_reaches_the_transport_in_seconds() -> None:
    seen: dict[str, float] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.extensions["timeout"])
        return _completed_interaction()

    with new_gemini_client(
        API_KEY, timeout_ms=TIMEOUT_MS, inner_transport=httpx.MockTransport(handler)
    ) as client:
        _create_interaction(client)

    assert seen
    assert set(seen.values()) == {60.0}


@pytest.mark.usefixtures("no_sleep")
def test_async_interactions_are_blocked_before_any_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return _json_response(429, _current_body("rate_limit_exceeded"))

    async def call(client: Any) -> Any:
        return await client.aio.interactions.create(
            model=MODEL,
            input="Synthetic fixture only.",
            response_format=RESPONSE_FORMAT,
            generation_config={"max_output_tokens": 128, "thinking_level": "low"},
            store=False,
        )

    with (
        new_gemini_client(
            API_KEY, timeout_ms=TIMEOUT_MS, inner_transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(AsyncProviderPathBlocked) as caught,
    ):
        asyncio.run(call(client))

    assert request_count == 0
    assert safe_log_fields(caught.value)["code"] == "error"


@pytest.mark.usefixtures("no_sleep")
def test_async_model_lookup_is_blocked_before_any_request() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"name": f"models/{MODEL}"})

    async def call(client: Any) -> Any:
        return await client.aio.models.get(model=MODEL)

    with (
        new_gemini_client(
            API_KEY, timeout_ms=TIMEOUT_MS, inner_transport=httpx.MockTransport(handler)
        ) as client,
        pytest.raises(AsyncProviderPathBlocked),
    ):
        asyncio.run(call(client))

    assert request_count == 0
