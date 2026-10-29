"""PD-1b capability checks: the synthetic image fixture, the image test handler, the
capability rules and the context-window refresh; no database needed."""

from __future__ import annotations

import io
from typing import Any

import httpx
import pytest
from PIL import Image
from reconbench_app.ai import connection_state
from reconbench_app.ai.contracts import InvalidOutput, ProviderImageTestOutput, parse_output
from reconbench_app.ai.fixtures import (
    PROVIDER_IMAGE_TEST,
    PROVIDER_IMAGE_TEST_NUMBER,
    PROVIDER_TEST,
    SYNTHETIC_FIXTURE_DIGESTS,
    _digits_png,
    plain_schema,
)
from reconbench_app.ai.gate import DeploymentGate, payload_digest
from reconbench_app.ai.limits import AiLimits
from reconbench_app.ai.provider import ProviderRequest
from reconbench_app.ai.worker.handlers import ProviderTestHandler
from reconbench_app.ai.worker.openai_compat import OpenAICompatProvider, request_body

VLLM_URL = "http://vllm.example.test/vllm/v1"
MODEL = "qwen3.5:9b"


def _request(operation_key: str) -> dict[str, Any]:
    return {"operation_key": operation_key, "call_kind": "provider_test"}


def test_the_image_is_a_valid_png_of_the_number_and_byte_stable() -> None:
    (image,) = PROVIDER_IMAGE_TEST.images
    assert image.data == _digits_png(PROVIDER_IMAGE_TEST_NUMBER)
    decoded = Image.open(io.BytesIO(image.data))
    decoded.load()
    assert decoded.size == (308, 164) and decoded.mode == "L"
    # White margin, black ink inside the first glyph's bar of "4" (row 4 is "#####").
    assert decoded.getpixel((0, 0)) == 255
    assert decoded.getpixel((40 + 6, 40 + 4 * 12 + 6)) == 0
    assert len(image.data) < AiLimits().synthetic_image.max_image_bytes  # type: ignore[operator]


def test_the_image_test_digest_is_on_the_allowlist_and_passes_a_synthetic_only_gate() -> None:
    (image,) = PROVIDER_IMAGE_TEST.images
    digest = payload_digest(
        system_instruction=PROVIDER_IMAGE_TEST.system_instruction,
        input_text=PROVIDER_IMAGE_TEST.input_text,
        response_schema=plain_schema(PROVIDER_IMAGE_TEST),
        images=[(image.caption, image.mime_type, image.sha256, image.resolution)],
    )
    assert SYNTHETIC_FIXTURE_DIGESTS[PROVIDER_IMAGE_TEST.fixture_id] == digest
    gate = DeploymentGate.from_values(
        "disabled", None, vllm_mode="synthetic_only", vllm_endpoints=VLLM_URL
    )
    assert (
        gate.refusal(
            subject_type="fixture",
            fixture_id=PROVIDER_IMAGE_TEST.fixture_id,
            digest=digest,
            external=False,
            endpoint=VLLM_URL,
        )
        is None
    )
    # The same image under the text fixture's id is not allowlisted.
    assert gate.refusal(
        subject_type="fixture",
        fixture_id=PROVIDER_TEST.fixture_id,
        digest=digest,
        external=False,
        endpoint=VLLM_URL,
    )


def test_the_handler_sends_the_image_only_for_an_image_test() -> None:
    handler = ProviderTestHandler()
    limits = AiLimits()
    text = handler.prepare(None, _request("provider_test:2026-10-29"), limits)  # type: ignore[arg-type]
    image = handler.prepare(None, _request("provider_test_image:2026-10-29"), limits)  # type: ignore[arg-type]
    assert not isinstance(text, Exception) and not isinstance(image, Exception)
    assert text.output_kind == "provider_test" and text.payload.images == ()  # type: ignore[union-attr]
    assert image.output_kind == "provider_image_test"  # type: ignore[union-attr]
    assert image.payload.images == PROVIDER_IMAGE_TEST.images  # type: ignore[union-attr]
    assert image.fixture_id == PROVIDER_IMAGE_TEST.fixture_id  # type: ignore[union-attr]
    assert limits.for_kind("provider_image_test").max_pages == 1
    assert limits.for_kind("provider_test").max_pages is None


@pytest.mark.parametrize("answer", ["472", " 472 "])
def test_the_image_test_passes_only_when_the_number_is_read(answer: str) -> None:
    handler = ProviderTestHandler()
    prepared = handler.prepare(None, _request("provider_test_image:x"), AiLimits())  # type: ignore[arg-type]
    parsed = parse_output("provider_image_test", f'{{"number": "{answer}"}}')
    assert isinstance(parsed, ProviderImageTestOutput)
    assert handler.validate(prepared, parsed) is parsed  # type: ignore[arg-type]


@pytest.mark.parametrize("answer", ["427", "", "four seven two", "4 7 2"])
def test_a_wrong_number_is_invalid_output(answer: str) -> None:
    handler = ProviderTestHandler()
    prepared = handler.prepare(None, _request("provider_test_image:x"), AiLimits())  # type: ignore[arg-type]
    parsed = parse_output("provider_image_test", f'{{"number": "{answer}"}}')
    with pytest.raises(InvalidOutput) as caught:
        handler.validate(prepared, parsed)  # type: ignore[arg-type]
    assert caught.value.code == "invalid_output"


def test_capability_is_yes_no_or_unknown() -> None:
    assert connection_state.capability(True, None) is True
    assert connection_state.capability(False, "INVALID_REQUEST") is False
    assert connection_state.capability(False, "invalid_output") is False
    for code in ("timeout", "PROVIDER_UNAVAILABLE", "bad_key", "MODEL_UNAVAILABLE", None):
        assert connection_state.capability(False, code) is None


@pytest.mark.parametrize(
    ("vision", "json_schema", "expected"),
    [
        (True, True, True),
        (True, None, False),
        (None, True, False),
        (False, True, False),
        (True, False, False),
        (None, None, False),
    ],
)
def test_only_both_proven_capabilities_let_the_server_read_pages(
    vision: bool | None, json_schema: bool | None, expected: bool
) -> None:
    card = {"supports_vision": vision, "supports_json_schema": json_schema, "context_window": 1}
    assert connection_state.can_read_pages(card) is expected
    assert connection_state.can_read_pages(None) is False


def test_a_reset_clears_the_capabilities() -> None:
    values = connection_state.reset_values()
    assert values["supports_vision"] is None
    assert values["supports_json_schema"] is None
    assert values["context_window"] is None


def test_the_image_test_kind_comes_from_the_operation_key() -> None:
    assert connection_state.request_kind(_request("provider_test_image:2026-10-29")) == "image"
    assert connection_state.request_kind(_request("provider_test:2026-10-29")) == "test"
    assert connection_state.request_kind(_request("provider_test_rotate:vllm:x")) == "rotate"


def test_the_image_request_body_carries_one_png_and_the_strict_schema() -> None:
    (image,) = PROVIDER_IMAGE_TEST.images
    body = request_body(
        ProviderRequest(
            model=MODEL,
            system_instruction=PROVIDER_IMAGE_TEST.system_instruction,
            input_text=PROVIDER_IMAGE_TEST.input_text,
            response_schema=plain_schema(PROVIDER_IMAGE_TEST),
            max_output_tokens=32,
            timeout_ms=60_000,
            images=PROVIDER_IMAGE_TEST.images,
        )
    )
    content = body["messages"][1]["content"]
    urls = [part["image_url"]["url"] for part in content if part["type"] == "image_url"]
    assert len(urls) == 1 and urls[0].startswith("data:image/png;base64,")
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["name"] == "vision_reading"
    assert image.caption in [part.get("text") for part in content]


def test_refresh_asks_get_models_again() -> None:
    windows = iter([8192, 4096])
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"data": [{"id": MODEL, "max_model_len": next(windows)}]})

    provider = OpenAICompatProvider(
        VLLM_URL, frozenset({VLLM_URL}), inner_transport=httpx.MockTransport(handler)
    )
    assert provider.context_window(MODEL) == 8192
    assert provider.context_window(MODEL) == 8192  # cached
    assert provider.context_window(MODEL, refresh=True) == 4096
    assert calls == ["/vllm/v1/models", "/vllm/v1/models"]
