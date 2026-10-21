"""Golden bytes for the Gemini vision request and its cache keys (WI-BE1, WI-BE5, WI-BE4).

History (decision log): D62 recorded the v3 request body before the provider
refactor. D63/WI-BE5 changed the vision cache key on purpose to v2 (provider +
reader parameters) without changing a byte of the body. D63/WI-BE4 then changed
the body on purpose: ``vision.prompt.v4.gemini`` adds the unreadable rule and
``vision.schema.v4`` adds ``unreadable``. The frozen v3 builder must still
reproduce the D62 body and the v1 key, because the bench's
``--vision-cache-compat v1`` mode finds the recorded D41 answers by that key.
The page images are synthetic bytes (only their hash is checked before sending).
"""

from __future__ import annotations

import hashlib
import json

import httpx
from reconbench_app.ai import cache as ai_cache
from reconbench_app.ai import vision
from reconbench_app.ai.contracts import SCHEMA_VERSIONS
from reconbench_app.ai.gate import payload_digest
from reconbench_app.ai.prompts import (
    LEGACY_VISION_PROMPT_VERSION,
    LEGACY_VISION_SCHEMA_VERSION,
    PromptPayload,
    legacy_vision_prompt_v3,
    prompt_version,
)
from reconbench_app.ai.provider import ProviderRequest
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION
from reconbench_app.ai.worker.gemini import GeminiProvider
from reconbench_app.extraction.vision import (
    DEFAULT_VISION_READER,
    VisionReaderInfo,
    vision_reader,
)

API_KEY = "AIzaSyD-synthetic-golden-key-0000000000000"
MODEL = "gemini-3.5-flash-lite"
PAGE_BYTES = (b"synthetic-page-one-" * 8, b"synthetic-page-two-" * 8)

# v3 (D62): body and v1 key; reproduced by the frozen v3 builder.
GOLDEN_V3_BODY_SHA256 = "acd3bdd9a67614534212fe1724324dbff8d353c96ea4f423c0bc2ef88035fe9e"
GOLDEN_V3_BODY_LENGTH = 6788
GOLDEN_V3_PAYLOAD_DIGEST = "465dd433e6cec13d39ddc84adec2c5ef494c6573cf892ae5aea12b27fd1b8c99"
GOLDEN_CACHE_KEY_V1 = "5f1c6d16e9c7183f4006ba256bacc1f4628f21d83998392880349d20c14bde66"
# v2 key of the v3 payload (D63, WI-BE5 alone): recorded for the history only.
GOLDEN_V3_CACHE_KEY_V2 = "848fa3cb6b4773e85434846967d65cb71fd7ecdf7be01fd57aab660cefe55f92"
# v4 (D63, WI-BE4): what the app sends and caches now.
GOLDEN_BODY_SHA256 = "7ede12081bc92ef637bce6a812c4357f4f45f65e0a8305a6e98c8062e34c3576"
GOLDEN_BODY_LENGTH = 8121
GOLDEN_CACHE_KEY = "42cdab004640718ca9462cc24b1213f71fec806e42246b5133713609a895f4be"


def _reader() -> VisionReaderInfo:
    reader = vision_reader(DEFAULT_VISION_READER)
    assert reader is not None
    return reader


def _pages() -> list[vision.PageRow]:
    return [
        vision.PageRow(
            page=number,
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type="image/jpeg",
            width=1200,
            height=1600,
            render={},
            mask={},
            data=data,
        )
        for number, data in enumerate(PAGE_BYTES, start=1)
    ]


def _payload() -> PromptPayload:
    payload = vision.prompt_for("invoice", _pages(), _reader())
    assert payload is not None
    return payload


def _legacy_payload() -> PromptPayload:
    images = vision.provider_images(_pages(), _reader().media_resolution)
    assert images is not None
    return legacy_vision_prompt_v3("invoice", images)


def _digest(payload: PromptPayload) -> str:
    return payload_digest(
        system_instruction=payload.system_instruction,
        input_text=payload.input_text,
        response_schema=payload.response_schema,
        images=payload.image_digest_parts(),
    )


def _sent_body(payload: PromptPayload) -> bytes:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "model": MODEL,
                "status": "completed",
                "steps": [{"type": "model_output", "content": [{"type": "text", "text": "{}"}]}],
                "usage": {"total_input_tokens": 1, "total_output_tokens": 1},
            },
        )

    # Built exactly as the runner builds it: no thinking level or options passed.
    GeminiProvider(inner_transport=httpx.MockTransport(handler)).complete(
        ProviderRequest(
            model=MODEL,
            system_instruction=payload.system_instruction,
            input_text=payload.input_text,
            response_schema=payload.response_schema,
            max_output_tokens=8192,
            timeout_ms=120_000,
            images=payload.images,
        ),
        api_key=API_KEY,
    )
    [request] = sent
    return request.read()


def _shape(body: bytes) -> dict[str, object]:
    parsed = json.loads(body)
    [turn] = parsed["input"]
    return {
        "keys": sorted(parsed),
        "generation_config": parsed["generation_config"],
        "turn": turn["type"],
        "parts": [part["type"] for part in turn["content"]],
    }


def test_gemini_vision_request_body_v4() -> None:
    body = _sent_body(_payload())

    assert _shape(body) == {
        "keys": [
            "generation_config",
            "input",
            "model",
            "response_format",
            "store",
            "system_instruction",
        ],
        "generation_config": {"max_output_tokens": 8192, "thinking_level": "low"},
        "turn": "user_input",
        "parts": ["text", "text", "image", "text", "image"],
    }
    parsed = json.loads(body)
    assert "unreadable" in parsed["response_format"]["schema"]["required"]
    assert "9. unreadable:" in parsed["system_instruction"]
    assert API_KEY.encode() not in body
    assert (hashlib.sha256(body).hexdigest(), len(body)) == (
        GOLDEN_BODY_SHA256,
        GOLDEN_BODY_LENGTH,
    )


def test_frozen_v3_builder_reproduces_the_d62_body() -> None:
    body = _sent_body(_legacy_payload())
    assert (hashlib.sha256(body).hexdigest(), len(body)) == (
        GOLDEN_V3_BODY_SHA256,
        GOLDEN_V3_BODY_LENGTH,
    )


def test_vision_cache_key_v1_of_the_v3_payload_is_unchanged() -> None:
    digest = _digest(_legacy_payload())
    key = ai_cache.cache_key(
        hook=vision.HOOK,
        model=MODEL,
        prompt_version=LEGACY_VISION_PROMPT_VERSION,
        schema_version=LEGACY_VISION_SCHEMA_VERSION,
        redaction_version=IMAGE_MASK_VERSION,
        payload_digest=digest,
    )
    assert (digest, key) == (GOLDEN_V3_PAYLOAD_DIGEST, GOLDEN_CACHE_KEY_V1)


def test_vision_cache_key_v2_as_the_runner_computes_it() -> None:
    reader = _reader()
    request = {
        "hook": vision.HOOK,
        "provider": reader.provider,
        "model": MODEL,
        "prompt_version": prompt_version(vision.HOOK, reader=reader.name),
        "schema_version": SCHEMA_VERSIONS[vision.HOOK],
        "redaction_version": IMAGE_MASK_VERSION,
        "reader_params": vision.reader_params(reader),
    }
    key = ai_cache.request_cache_key(request, _digest(_payload()))
    assert key not in {GOLDEN_CACHE_KEY_V1, GOLDEN_V3_CACHE_KEY_V2}
    assert key == GOLDEN_CACHE_KEY
