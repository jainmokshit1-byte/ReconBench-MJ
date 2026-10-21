from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from reconbench_app.ai.fixtures import PROVIDER_TEST, plain_schema
from reconbench_app.ai.provider import ProviderFailure, ProviderRequest
from reconbench_app.ai.worker.config import AiWorkerSettings
from reconbench_app.ai.worker.gemini import GeminiProvider
from reconbench_app.ai.worker.providers import build_providers

API_KEY = "AIzaSyD-synthetic-adapter-key-000000000000"
MODEL = "gemini-3.8-flash"


def _request(**overrides: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "model": MODEL,
        "system_instruction": PROVIDER_TEST.system_instruction,
        "input_text": PROVIDER_TEST.input_text,
        "response_schema": plain_schema(PROVIDER_TEST),
        "max_output_tokens": 32,
        "timeout_ms": 60_000,
    }
    values.update(overrides)
    return ProviderRequest(**values)


def _interaction(status: str = "completed", text: str | None = '{"status":"ok"}') -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    if text is not None:
        steps.append({"type": "model_output", "content": [{"type": "text", "text": text}]})
    return {
        "id": "synthetic",
        "model": MODEL,
        "status": status,
        "steps": steps,
        "usage": {"total_input_tokens": 40, "total_output_tokens": 6, "total_thought_tokens": 9},
    }


class _Recorder:
    def __init__(self, response: httpx.Response | Exception) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object) -> None:
        raise AssertionError("adapter must never sleep or retry")

    monkeypatch.setattr("time.sleep", forbidden)


def _provider(recorder: _Recorder) -> GeminiProvider:
    return GeminiProvider(inner_transport=httpx.MockTransport(recorder))


def test_request_matches_the_documented_shape_exactly() -> None:
    recorder = _Recorder(httpx.Response(200, json=_interaction()))
    result = _provider(recorder).complete(_request(), api_key=API_KEY)

    assert result.output_text == '{"status":"ok"}'
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (40, 6)
    assert result.usage.thought_tokens == 9
    [request] = recorder.requests
    assert request.method == "POST" and request.url.path == "/v1/interactions"
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)
    body = json.loads(request.read())
    assert API_KEY not in json.dumps(body)
    assert body == {
        "model": MODEL,
        "system_instruction": PROVIDER_TEST.system_instruction,
        "input": PROVIDER_TEST.input_text,
        "response_format": {
            "type": "text",
            "mime_type": "application/json",
            "schema": plain_schema(PROVIDER_TEST),
        },
        "generation_config": {"max_output_tokens": 32, "thinking_level": "low"},
        "store": False,
    }
    assert set(request.extensions["timeout"].values()) == {60.0}
    assert result.model_reported == MODEL


def test_options_never_reach_gemini_and_no_thinking_level_is_omitted() -> None:
    default = _Recorder(httpx.Response(200, json=_interaction()))
    _provider(default).complete(_request(), api_key=API_KEY)
    with_options = _Recorder(httpx.Response(200, json=_interaction()))
    _provider(with_options).complete(
        _request(options={"temperature": 0, "chat_template_kwargs": {"enable_thinking": False}}),
        api_key=API_KEY,
    )
    assert with_options.requests[0].read() == default.requests[0].read()

    unset = _Recorder(httpx.Response(200, json=_interaction()))
    _provider(unset).complete(_request(thinking_level=None), api_key=API_KEY)
    body = json.loads(unset.requests[0].read())
    assert body["generation_config"] == {"max_output_tokens": 32}


def test_worker_registry_holds_only_gemini() -> None:
    providers = build_providers(AiWorkerSettings.model_construct())
    assert list(providers) == ["gemini"]
    assert isinstance(providers["gemini"], GeminiProvider)


@pytest.mark.parametrize(
    ("interaction", "usage_known"),
    [
        (_interaction(status="incomplete"), True),
        (_interaction(status="failed"), True),
        (_interaction(text=None), True),
        (_interaction(text="   "), True),
    ],
)
def test_only_completed_non_empty_output_is_accepted(
    interaction: dict[str, Any], usage_known: bool
) -> None:
    recorder = _Recorder(httpx.Response(200, json=interaction))
    with pytest.raises(ProviderFailure) as caught:
        _provider(recorder).complete(_request(), api_key=API_KEY)
    assert caught.value.code == "invalid_output"
    assert caught.value.delivery == "responded"
    assert (caught.value.usage is not None) is usage_known
    assert len(recorder.requests) == 1


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status,
        headers={"Retry-After": "3600"},
        json={"error": {"code": code, "message": f"provider says {API_KEY} is bad"}},
    )


@pytest.mark.parametrize(
    ("response", "code", "delivery", "cooldown"),
    [
        (_error(400, "invalid_request"), "INVALID_REQUEST", "responded", None),
        (_error(400, "failed_precondition"), "BILLING_OR_PREREQUISITE", "responded", None),
        (_error(401, "authentication"), "bad_key", "responded", None),
        (_error(402, "payment_required"), "PREPAY_DEPLETED", "responded", None),
        (_error(403, "permission_denied"), "PERMISSION_DENIED", "responded", None),
        (_error(404, "model_not_found"), "MODEL_UNAVAILABLE", "responded", None),
        (_error(429, "rate_limit_exceeded"), "rate_limited", "responded", 60),
        (_error(429, "quota_exceeded"), "QUOTA_EXHAUSTED", "responded", 60),
        (_error(500, "api_error"), "PROVIDER_UNAVAILABLE", "responded", None),
        (_error(503, "service_unavailable"), "PROVIDER_UNAVAILABLE", "responded", None),
        (_error(504, "deadline_exceeded"), "timeout", "responded", None),
        (httpx.ConnectError("refused"), "PROVIDER_UNAVAILABLE", "not_sent", None),
        (httpx.ConnectTimeout("slow"), "timeout", "not_sent", None),
        (httpx.ReadTimeout("slow"), "timeout", "may_have_been_sent", None),
        (httpx.RemoteProtocolError("bad"), "PROVIDER_UNAVAILABLE", "may_have_been_sent", None),
    ],
)
def test_every_failure_maps_to_a_sanitized_code_after_exactly_one_attempt(
    response: httpx.Response | Exception, code: str, delivery: str, cooldown: int | None
) -> None:
    recorder = _Recorder(response)
    with pytest.raises(ProviderFailure) as caught:
        _provider(recorder).complete(_request(), api_key=API_KEY)
    assert len(recorder.requests) == 1
    assert (caught.value.code, caught.value.delivery) == (code, delivery)
    assert caught.value.cooldown_seconds == cooldown
    assert API_KEY not in str(caught.value)
    assert "provider says" not in str(caught.value)
    assert caught.value.__cause__ is None
