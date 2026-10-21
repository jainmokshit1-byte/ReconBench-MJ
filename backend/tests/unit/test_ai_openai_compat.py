"""vLLM (OpenAI-compatible) provider, endpoint policy, gate and limits (LLM plan WI-BE2).

Every HTTP exchange goes through ``httpx.MockTransport``: synthetic data only, no
network and no model server.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from reconbench_app.ai import vision
from reconbench_app.ai.contracts import InvalidOutput, VisionOutput, vision_response_schema
from reconbench_app.ai.endpoint_policy import (
    EndpointNotAllowed,
    check_allowed,
    normalize_base_url,
    parse_allowlist,
)
from reconbench_app.ai.fixtures import PROVIDER_TEST, plain_schema
from reconbench_app.ai.gate import (
    SKIP_ENDPOINT_NOT_ALLOWED,
    SKIP_EXTERNAL_CALLS_DISABLED,
    SKIP_LOCAL_CALLS_DISABLED,
    SKIP_SYNTHETIC_ONLY,
    DeploymentGate,
    GateConfigurationError,
)
from reconbench_app.ai.limits import (
    MIN_LOCAL_OUTPUT_TOKENS,
    AiLimits,
    CallKindLimits,
    local_output_budget,
)
from reconbench_app.ai.prompts import vision_prompt
from reconbench_app.ai.provider import ProviderFailure, ProviderImage, ProviderRequest
from reconbench_app.ai.worker.config import AiWorkerSettings, AiWorkerStartupError
from reconbench_app.ai.worker.gemini import GeminiProvider
from reconbench_app.ai.worker.openai_compat import (
    SAMPLING_PROFILES,
    SEED,
    OpenAICompatProvider,
    map_openai_compat_error,
    sanitize_detail,
)
from reconbench_app.ai.worker.providers import build_providers
from reconbench_app.extraction.vision import vision_reader
from reconbench_core.extraction.vision import merge_page_readings

BASE_URL = "http://vllm.example.test/vllm/v1"
ALLOWED = frozenset({BASE_URL})
MODEL = "qwen3.5:9b"
ANSWER = '{"status":"ok"}'


def _image(page: int = 1) -> ProviderImage:
    data = f"synthetic-page-{page}".encode()
    return ProviderImage(f"Page {page}", "image/jpeg", data, hashlib.sha256(data).hexdigest())


def _request(**overrides: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "model": MODEL,
        "system_instruction": PROVIDER_TEST.system_instruction,
        "input_text": PROVIDER_TEST.input_text,
        "response_schema": plain_schema(PROVIDER_TEST),
        "max_output_tokens": 32,
        "timeout_ms": 300_000,
    }
    values.update(overrides)
    return ProviderRequest(**values)


def _vision_request(**overrides: Any) -> ProviderRequest:
    payload = vision_prompt("invoice", [_image()], variant="qwen", dialect="openai_json_schema")
    return _request(
        system_instruction=payload.system_instruction,
        input_text=payload.input_text,
        response_schema=payload.response_schema,
        images=payload.images,
        max_output_tokens=3_072,
        **overrides,
    )


def _completion(
    content: str | None = ANSWER,
    *,
    finish_reason: str = "stop",
    message: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": "chatcmpl-synthetic",
        "object": "chat.completion",
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": message or {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 7, "total_tokens": 127},
    }


def _models(window: int = 8_192) -> dict[str, Any]:
    return {
        "object": "list",
        "data": [{"id": MODEL, "object": "model", "owned_by": "vllm", "max_model_len": window}],
    }


class _Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_args: object) -> None:
        raise AssertionError("the adapter must never sleep or retry")

    monkeypatch.setattr("time.sleep", forbidden)


def _provider(recorder: _Recorder) -> OpenAICompatProvider:
    return OpenAICompatProvider(BASE_URL, ALLOWED, inner_transport=httpx.MockTransport(recorder))


def _body(request: httpx.Request) -> dict[str, Any]:
    body = json.loads(request.read())
    assert isinstance(body, dict)
    return body


# --------------------------------------------------------------------------- request shape


def test_vision_request_carries_thinking_off_sampling_schema_and_one_page() -> None:
    recorder = _Recorder(httpx.Response(200, json=_completion()))
    request = _vision_request(options={"sampling_profile": "qwen_card_nonthinking"})
    result = _provider(recorder).complete(request, api_key="")

    assert result.output_text == ANSWER
    assert result.usage is not None
    assert (result.usage.input_tokens, result.usage.output_tokens) == (120, 7)
    assert result.model_reported == MODEL and result.finish_reason == "stop"
    [sent] = recorder.requests
    assert sent.method == "POST"
    # The base URL already ends in /v1: no second /v1 is appended.
    assert str(sent.url) == f"{BASE_URL}/chat/completions"
    assert "authorization" not in sent.headers
    body = _body(sent)
    assert body["model"] == MODEL
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["max_tokens"] == 3_072
    assert body["seed"] == SEED == 20261016
    assert body["stream"] is False
    for name, value in SAMPLING_PROFILES["qwen_card_nonthinking"].items():
        assert body[name] == value
    assert body["temperature"] == 0.7 and body["presence_penalty"] == 1.5
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "vision_reading",
            "schema": vision_response_schema("openai_json_schema"),
            "strict": True,
        },
    }
    system, user = body["messages"]
    assert system == {"role": "system", "content": request.system_instruction}
    assert user["role"] == "user"
    text, caption, image = user["content"]
    assert text == {"type": "text", "text": request.input_text}
    assert caption == {"type": "text", "text": "Page 1"}
    data = base64.b64encode(request.images[0].data).decode("ascii")
    assert image == {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def test_options_cannot_turn_thinking_on_and_pp0_profile_is_available() -> None:
    recorder = _Recorder(httpx.Response(200, json=_completion()))
    _provider(recorder).complete(
        _request(
            options={
                "sampling_profile": "qwen_card_nonthinking_pp0",
                "chat_template_kwargs": {"enable_thinking": True},
                "temperature": 0,
            }
        ),
        api_key="synthetic-server-key",
    )
    [sent] = recorder.requests
    body = _body(sent)
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["presence_penalty"] == 0.0 and body["temperature"] == 0.7
    assert sent.headers["authorization"] == "Bearer synthetic-server-key"
    assert "synthetic-server-key" not in str(sent.url)
    # A text-only request keeps a plain string user message.
    assert body["messages"][1]["content"] == PROVIDER_TEST.input_text
    assert body["response_format"]["json_schema"]["name"] == "answer"


def test_gemini_reader_profile_falls_back_to_the_qwen_card() -> None:
    recorder = _Recorder(httpx.Response(200, json=_completion()))
    _provider(recorder).complete(
        _request(options={"sampling_profile": "provider_default"}), api_key=""
    )
    assert _body(recorder.requests[0])["top_k"] == 20


@pytest.mark.parametrize(
    ("request_kwargs", "detail"),
    [
        ({"options": {"sampling_profile": "made_up"}}, "unknown sampling"),
        ({"images": (_image(1), _image(2))}, "one page per call"),
        ({"timeout_ms": 300_001}, "timeout bound"),
    ],
)
def test_bad_requests_are_refused_before_any_io(
    request_kwargs: dict[str, Any], detail: str
) -> None:
    recorder = _Recorder()
    with pytest.raises(ProviderFailure) as caught:
        _provider(recorder).complete(_request(**request_kwargs), api_key="")
    assert (caught.value.code, caught.value.delivery) == ("INVALID_REQUEST", "not_sent")
    assert caught.value.detail == detail
    assert recorder.requests == []


# --------------------------------------------------------------------------- errors


@pytest.mark.parametrize(
    ("status", "code", "cooldown"),
    [
        (400, "INVALID_REQUEST", None),
        (401, "bad_key", None),
        (403, "bad_key", None),
        (404, "MODEL_UNAVAILABLE", None),
        (422, "INVALID_REQUEST", None),
        (429, "rate_limited", 30),
        (503, "PROVIDER_UNAVAILABLE", 30),
        (500, "error", None),
        (502, "error", None),
        (302, "error", None),
    ],
)
def test_error_statuses_map_to_sanitized_codes(
    status: int, code: str, cooldown: int | None
) -> None:
    headers = {"location": "http://elsewhere.example.test/v1/chat/completions"}
    recorder = _Recorder(
        httpx.Response(status, json={"error": {"message": "synthetic"}}, headers=headers)
    )
    with pytest.raises(ProviderFailure) as caught:
        _provider(recorder).complete(_request(), api_key="")
    failure = caught.value
    assert (failure.code, failure.delivery, failure.cooldown_seconds) == (
        code,
        "responded",
        cooldown,
    )
    # One attempt only: no retry and no redirect is followed.
    assert len(recorder.requests) == 1
    assert map_openai_compat_error(status).code == code


def test_a_400_keeps_a_short_masked_diagnostic() -> None:
    message = (
        "This model's maximum context length is 8192 tokens. However, you requested 9300 "
        "tokens. Invoice 123456789 " + "x" * 400
    )
    recorder = _Recorder(httpx.Response(400, json={"error": {"message": message}}))
    with pytest.raises(ProviderFailure) as caught:
        _provider(recorder).complete(_request(), api_key="")
    detail = caught.value.detail
    assert detail is not None and len(detail) == 300
    assert "8192" in detail and "123456789" not in detail and "DDDDDDDDD" in detail
    assert "123456789" not in str(caught.value)
    assert sanitize_detail("  ") is None


@pytest.mark.parametrize(
    ("error", "code", "delivery"),
    [
        (httpx.ConnectError("refused"), "PROVIDER_UNAVAILABLE", "not_sent"),
        (httpx.ConnectTimeout("slow connect"), "PROVIDER_UNAVAILABLE", "not_sent"),
        (httpx.ReadTimeout("slow answer"), "timeout", "responded"),
        (httpx.RemoteProtocolError("dropped"), "PROVIDER_UNAVAILABLE", "responded"),
    ],
)
def test_transport_failures_have_no_uncertain_state(
    error: Exception, code: str, delivery: str
) -> None:
    recorder = _Recorder(error)
    with pytest.raises(ProviderFailure) as caught:
        _provider(recorder).complete(_request(), api_key="")
    assert (caught.value.code, caught.value.delivery) == (code, delivery)
    assert "refused" not in str(caught.value) and "slow" not in str(caught.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json=[1, 2]),
        httpx.Response(200, json=_completion(content=None)),
        httpx.Response(200, json=_completion(content="   ")),
        httpx.Response(200, json=_completion(content="Here is the JSON: {")),
    ],
)
def test_unusable_bodies_are_invalid_output(response: httpx.Response) -> None:
    with pytest.raises(ProviderFailure) as caught:
        _provider(_Recorder(response)).complete(_request(), api_key="")
    assert (caught.value.code, caught.value.delivery) == ("invalid_output", "responded")


def test_output_cap_is_invalid_output_with_usage() -> None:
    recorder = _Recorder(
        httpx.Response(200, json=_completion('{"status":', finish_reason="length"))
    )
    provider = _provider(recorder)
    with pytest.raises(ProviderFailure) as caught:
        provider.complete(_request(), api_key="")
    assert caught.value.code == "invalid_output" and caught.value.detail == "at_output_cap"
    assert caught.value.usage is not None and caught.value.usage.output_tokens == 7
    assert provider.counters["at_output_cap"] == 1


@pytest.mark.parametrize("leak", ["Thinking Process:\n1. read", "  <think>\nhmm</think>{}"])
def test_thinking_leak_is_counted_and_invalid(leak: str) -> None:
    provider = _provider(
        _Recorder(httpx.Response(200, json=_completion(leak, finish_reason="length")))
    )
    with pytest.raises(ProviderFailure) as caught:
        provider.complete(_request(), api_key="")
    assert caught.value.code == "invalid_output" and caught.value.detail == "thinking_leak"
    assert provider.counters["thinking_leak"] == 1


def test_separate_reasoning_part_is_flagged_and_counted() -> None:
    message = {"role": "assistant", "content": ANSWER, "reasoning_content": "synthetic"}
    provider = _provider(_Recorder(httpx.Response(200, json=_completion(message=message))))
    result = provider.complete(_request(), api_key="")
    assert result.reasoning_present is True
    assert provider.counters["reasoning_present"] == 1


# --------------------------------------------------------------------------- SSRF


def test_a_url_outside_the_allowlist_is_refused_before_any_io() -> None:
    with pytest.raises(EndpointNotAllowed):
        OpenAICompatProvider("http://other.example.test/vllm/v1", ALLOWED)
    recorder = _Recorder(httpx.Response(200, json=_completion()))
    provider = _provider(recorder)
    provider.base_url = "http://169.254.169.254/latest"
    with pytest.raises(ProviderFailure) as caught:
        provider.complete(_request(), api_key="")
    assert (caught.value.code, caught.value.delivery) == ("ENDPOINT_NOT_ALLOWED", "not_sent")
    assert provider.health().code == "ENDPOINT_NOT_ALLOWED"
    assert recorder.requests == []


def test_client_ignores_environment_proxies_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.test:3128")
    provider = OpenAICompatProvider(BASE_URL, ALLOWED)
    with provider._client(httpx.Timeout(1.0)) as client:
        assert client.trust_env is False
        assert client.follow_redirects is False


@pytest.mark.parametrize(
    ("url", "normalized"),
    [
        ("http://vllm.example.test/vllm/v1", BASE_URL),
        ("HTTP://VLLM.Example.TEST:80/vllm/v1/", BASE_URL),
        ("https://vllm.example.test:443/v1", "https://vllm.example.test/v1"),
        ("http://10.0.0.5:8000/v1", "http://10.0.0.5:8000/v1"),
        ("http://[::1]:8000/v1", "http://[::1]:8000/v1"),
        ("http://vllm.example.test", "http://vllm.example.test"),
    ],
)
def test_base_urls_are_normalised(url: str, normalized: str) -> None:
    assert normalize_base_url(url) == normalized


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://vllm.example.test/v1",
        "file:///etc/passwd",
        "http://user:pass@vllm.example.test/v1",
        "http://vllm.example.test@evil.example.test/v1",
        "http://vllm.example.test/v1?x=1",
        "http://vllm.example.test/v1#frag",
        "http://vllm.example.test/vllm/../v1",
        "http://vllm.example.test/vllm/./v1",
        "http://vllm.example.test//v1",
        "http://vllm.example.test/v%31",
        "http://vllm.example.test/v1 ",
        "http://vllm.example.test\\v1",
        "http://vllm.exämple.test/v1",
        "http:///v1",
        "http://vllm.example.test:99999/v1",
        "http://-bad-.example.test/v1",
    ],
)
def test_malformed_urls_are_refused(url: str) -> None:
    with pytest.raises(EndpointNotAllowed):
        normalize_base_url(url)


def test_allowlist_matches_origin_and_path_prefix_exactly() -> None:
    allowlist = parse_allowlist([" http://vllm.example.test/vllm/v1/ ", "", BASE_URL])
    assert allowlist == (BASE_URL,)
    assert check_allowed("http://VLLM.example.test:80/vllm/v1", allowlist) == BASE_URL
    for other in (
        "http://vllm.example.test/vllm",
        "http://vllm.example.test/vllm/v1/admin",
        "https://vllm.example.test/vllm/v1",
        "http://vllm.example.test:8000/vllm/v1",
    ):
        with pytest.raises(EndpointNotAllowed):
            check_allowed(other, allowlist)


# --------------------------------------------------------------------------- health


def test_health_reads_models_and_context_window_once() -> None:
    recorder = _Recorder(httpx.Response(200, json=_models()))
    provider = _provider(recorder)
    assert provider.context_window(MODEL) == 8_192
    assert provider.context_window(MODEL) == 8_192
    [sent] = recorder.requests
    assert sent.method == "GET" and str(sent.url) == f"{BASE_URL}/models"
    health = OpenAICompatProvider(
        BASE_URL,
        ALLOWED,
        inner_transport=httpx.MockTransport(_Recorder(httpx.Response(200, json=_models(4_096)))),
    ).health()
    assert health.ok and health.models == (MODEL,) and health.max_model_len == {MODEL: 4_096}
    assert health.latency_ms is not None


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.ConnectError("refused"), "PROVIDER_UNAVAILABLE"),
        (httpx.Response(401), "bad_key"),
        (httpx.Response(503), "PROVIDER_UNAVAILABLE"),
        (httpx.Response(200, content=b"<html>"), "invalid_output"),
        (httpx.Response(200, json={"data": "x"}), "invalid_output"),
    ],
)
def test_health_failures_are_reported_not_raised(
    response: httpx.Response | Exception, code: str
) -> None:
    provider = _provider(_Recorder(response))
    health = provider.health()
    assert (health.ok, health.code) == (False, code)
    assert provider._context_windows == {}


# --------------------------------------------------------------------------- gate, registry


def test_gate_keeps_external_and_local_switches_separate() -> None:
    gate = DeploymentGate.from_values(
        "disabled", None, vllm_mode="enabled", vllm_endpoints=f" {BASE_URL}/ ,"
    )
    assert gate.vllm_endpoints == (BASE_URL,)
    common: dict[str, Any] = {"subject_type": "document_version", "fixture_id": None, "digest": "d"}
    assert gate.refusal(**common) == SKIP_EXTERNAL_CALLS_DISABLED
    assert gate.refusal(**common, external=False, endpoint=BASE_URL) is None
    assert (
        gate.refusal(**common, external=False, endpoint="http://other.example.test/v1")
        == SKIP_ENDPOINT_NOT_ALLOWED
    )
    assert gate.refusal(**common, external=False, endpoint=None) == SKIP_ENDPOINT_NOT_ALLOWED

    local_off = DeploymentGate.from_values("synthetic_only", None)
    assert local_off.vllm_mode == "disabled"
    assert local_off.refusal(**common, external=False, endpoint=BASE_URL) == (
        SKIP_LOCAL_CALLS_DISABLED
    )
    synthetic = DeploymentGate.from_values(
        "disabled", None, vllm_mode="synthetic_only", vllm_endpoints=BASE_URL
    )
    assert synthetic.refusal(**common, external=False, endpoint=BASE_URL) == SKIP_SYNTHETIC_ONLY


@pytest.mark.parametrize(
    ("mode", "endpoints"),
    [("sometimes", BASE_URL), ("enabled", None), ("enabled", ""), ("enabled", "ftp://x/v1")],
)
def test_gate_configuration_errors(mode: str, endpoints: str | None) -> None:
    with pytest.raises(GateConfigurationError):
        DeploymentGate.from_values("disabled", None, vllm_mode=mode, vllm_endpoints=endpoints)


def _worker_settings(**values: Any) -> AiWorkerSettings:
    return AiWorkerSettings.model_construct(
        ai_external_calls="disabled", ai_external_calls_approval_ref=None, **values
    )


def test_vllm_is_registered_only_when_configured() -> None:
    assert list(build_providers(_worker_settings())) == ["gemini"]
    assert list(
        build_providers(_worker_settings(ai_vllm_calls="disabled", ai_vllm_base_url=BASE_URL))
    ) == ["gemini"]
    providers = build_providers(
        _worker_settings(
            ai_vllm_calls="synthetic_only", ai_vllm_endpoints=BASE_URL, ai_vllm_base_url=BASE_URL
        )
    )
    assert list(providers) == ["gemini", "vllm"]
    assert isinstance(providers["gemini"], GeminiProvider)
    vllm = providers["vllm"]
    assert isinstance(vllm, OpenAICompatProvider)
    assert vllm.base_url == BASE_URL and vllm.external is False


def test_an_empty_base_url_counts_as_unset() -> None:
    # Compose forwards ``RB_AI_VLLM_BASE_URL: ${RB_AI_VLLM_BASE_URL:-}``: an unset
    # variable arrives as "" and must not stop the worker or register a provider.
    settings = _worker_settings(
        ai_vllm_calls="enabled", ai_vllm_endpoints=BASE_URL, ai_vllm_base_url=""
    )
    assert list(build_providers(settings)) == ["gemini"]


def test_a_base_url_outside_the_allowlist_stops_the_worker() -> None:
    with pytest.raises(AiWorkerStartupError):
        build_providers(
            _worker_settings(
                ai_vllm_calls="enabled",
                ai_vllm_endpoints=BASE_URL,
                ai_vllm_base_url="http://other.example.test/v1",
            )
        )


def test_registry_entry_for_the_local_reader() -> None:
    reader = vision_reader("vision_openai_compat")
    assert reader is not None
    assert (reader.provider, reader.external, reader.billing) == ("vllm", False, False)
    assert (reader.schema_dialect, reader.prompt_variant) == ("openai_json_schema", "qwen")
    assert (reader.thinking, reader.sampling_profile) == ("off", "qwen_card_nonthinking")
    assert reader.pages_per_call == 1
    gemini = vision_reader("vision_gemini")
    assert gemini is not None and gemini.pages_per_call is None
    # pages_per_call is not a reader parameter: the Gemini cache key is unchanged.
    assert "pages_per_call" not in vision.reader_params(reader)


# --------------------------------------------------------------------------- limits


def test_local_limits_are_one_page_per_call_with_a_longer_timeout() -> None:
    limits = AiLimits()
    local = limits.for_kind("h6_vision", local=True)
    assert (local.max_pages, local.pages_per_call, local.max_concurrency) == (1, 1, 1)
    assert local.backoff_seconds == 30
    assert limits.timeout_for("h6_vision", local=True) == 300
    assert limits.timeout_for("h6_vision") == 120
    assert limits.for_kind("h6_vision") is limits.h6_vision
    # A kind without a local override uses its own limits; the override is not a kind.
    assert limits.for_kind("h1_extract", local=True) is limits.h1_extract
    with pytest.raises(KeyError):
        limits.for_kind("h6_vision_local")


def test_only_local_kinds_may_exceed_the_gemini_timeout_cap() -> None:
    long_call = CallKindLimits(
        max_input_bytes=1_000, max_output_tokens=64, call_timeout_seconds=300
    )
    with pytest.raises(ValidationError):
        AiLimits(h6_vision=long_call)
    assert AiLimits(h6_vision_local=long_call).timeout_for("h6_vision", local=True) == 300


def test_output_budget_follows_the_context_window() -> None:
    # 8192 - ceil(3000 / 3) - 3500 - 256 = 3436
    assert (
        local_output_budget(context_window=8_192, prompt_bytes=3_000, image_tokens=3_500) == 3_436
    )
    small = local_output_budget(context_window=4_096, prompt_bytes=3_000, image_tokens=3_500)
    assert small < MIN_LOCAL_OUTPUT_TOKENS


# --------------------------------------------------------------------------- page merge


def _page_answer(page: int, *, kind: str = "invoice", claimed: int | None = None) -> dict[str, Any]:
    at = claimed if claimed is not None else page
    return {
        "doc_kind": kind,
        "pages": [{"page": at, "kind": "invoice", "title": None}],
        "fields": [
            {
                "name": "doc_no",
                "text": f"SYN-{page}",
                "label": None,
                "page": at,
                "box_2d": [10, 10, 20, 40],
            }
        ],
        "lines": [],
        "annotations": [],
        "unreadable": [{"name": "total", "page": at, "box_2d": [1, 2, 3, 4]}],
    }


def test_merge_keeps_each_page_and_drops_items_placed_elsewhere() -> None:
    merged = merge_page_readings(
        [(2, _page_answer(2, kind="other")), (1, _page_answer(1)), (3, _page_answer(3, claimed=1))]
    )
    assert merged.dropped_off_page == 3
    assert merged.answer["doc_kind"] == "invoice"
    assert [item["page"] for item in merged.answer["fields"]] == [1, 2]
    assert [item["page"] for item in merged.answer["pages"]] == [1, 2]
    VisionOutput.model_validate(merged.answer)


def test_merge_rejects_boolean_pages_and_keeps_other_when_nothing_else() -> None:
    answer = _page_answer(1, kind="other")
    answer["fields"][0]["page"] = True
    merged = merge_page_readings([(1, answer)])
    assert merged.answer["fields"] == [] and merged.dropped_off_page == 1
    assert merged.answer["doc_kind"] == "other"
    assert merge_page_readings([]).answer["doc_kind"] == "other"


def test_page_answers_merge_into_one_strict_answer() -> None:
    texts = [json.dumps(_page_answer(1)), json.dumps(_page_answer(2))]
    merged = VisionOutput.model_validate_json(vision.merge_page_answers(texts, pages=[1, 2]))
    assert [item.text for item in merged.fields] == ["SYN-1", "SYN-2"]
    with pytest.raises(InvalidOutput):
        vision.merge_page_answers([texts[0], "{"], pages=[1, 2])
    with pytest.raises(InvalidOutput):
        vision.merge_page_answers(texts, pages=[1])


def test_local_reader_gets_one_prompt_per_page() -> None:
    pages = []
    for number in (1, 2):
        data = f"synthetic-page-{number}".encode()
        pages.append(
            vision.PageRow(
                page=number,
                sha256=hashlib.sha256(data).hexdigest(),
                mime_type="image/jpeg",
                width=100,
                height=140,
                render={},
                mask={},
                data=data,
            )
        )
    local = vision_reader("vision_openai_compat")
    gemini = vision_reader("vision_gemini")
    assert local is not None and gemini is not None
    parts = vision.page_prompts("invoice", pages, local)
    assert parts is not None and len(parts) == 2
    assert [[image.caption for image in part.images] for part in parts] == [["Page 1"], ["Page 2"]]
    assert all(
        part.response_schema == vision_response_schema("openai_json_schema") for part in parts
    )
    assert "pages 2" in parts[1].input_text
    assert vision.page_prompts("invoice", pages, gemini) == ()
    gone = [pages[0], replace(pages[1], data=None)]
    assert vision.page_prompts("invoice", gone, local) is None
