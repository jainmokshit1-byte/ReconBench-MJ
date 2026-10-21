"""Cache key v2 and reader parameters (WI-BE5): synthetic values only."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from reconbench_app.ai import cache as ai_cache
from reconbench_app.ai import vision
from reconbench_app.extraction.vision import DEFAULT_VISION_READER, VisionReaderInfo, vision_reader

DIGEST = "0" * 64


def _reader() -> VisionReaderInfo:
    reader = vision_reader(DEFAULT_VISION_READER)
    assert reader is not None
    return reader


def _request(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "hook": vision.HOOK,
        "provider": "gemini",
        "model": "synthetic-model",
        "prompt_version": "vision.prompt.synthetic",
        "schema_version": "vision.schema.synthetic",
        "redaction_version": "image_mask.v1",
        "reader_params": vision.reader_params(_reader()),
    }
    values.update(overrides)
    return values


def test_reader_params_are_canonical_and_complete() -> None:
    params = vision.reader_params(_reader())
    assert sorted(params) == [
        "jpeg_quality",
        "mask_version",
        "max_pixels",
        "max_side",
        "media_resolution",
        "pass",
        "prompt_variant",
        "reader",
        "render_version",
        "sampling_profile",
        "thinking",
    ]
    assert (params["reader"], params["thinking"], params["pass"]) == (
        "vision_gemini",
        "low",
        "full",
    )
    assert ai_cache.reader_params_digest(params) == ai_cache.reader_params_digest(
        dict(reversed(list(params.items())))
    )


def test_same_request_gives_the_same_key() -> None:
    assert ai_cache.request_cache_key(_request(), DIGEST) == ai_cache.request_cache_key(
        _request(), DIGEST
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "openai"},
        {"model": "other-model"},
        {"prompt_version": "vision.prompt.other"},
        {"schema_version": "vision.schema.other"},
        {"reader_params": vision.reader_params(replace(_reader(), thinking="off"))},
        {
            "reader_params": vision.reader_params(
                replace(_reader(), sampling_profile="qwen_card_nonthinking")
            )
        },
        {"reader_params": vision.reader_params(_reader(), reader_pass="crop:table")},
        {"reader_params": vision.reader_params(replace(_reader(), max_pixels=1_000_000))},
        {"reader_params": vision.reader_params(replace(_reader(), prompt_variant="qwen"))},
    ],
)
def test_any_reader_or_provider_difference_changes_the_vision_key(
    overrides: dict[str, Any],
) -> None:
    assert ai_cache.request_cache_key(_request(**overrides), DIGEST) != ai_cache.request_cache_key(
        _request(), DIGEST
    )


def test_text_hooks_keep_the_v1_key() -> None:
    request = _request(hook="h1_extract", reader_params={})
    assert ai_cache.request_cache_key(request, DIGEST) == ai_cache.cache_key(
        hook="h1_extract",
        model="synthetic-model",
        prompt_version="vision.prompt.synthetic",
        schema_version="vision.schema.synthetic",
        redaction_version="image_mask.v1",
        payload_digest=DIGEST,
    )
    # Provider is not part of v1: text-hook caches written before WI-BE5 stay valid.
    assert ai_cache.request_cache_key(
        _request(hook="h1_extract", provider="openai"), DIGEST
    ) == ai_cache.request_cache_key(request, DIGEST)


def test_vision_key_v2_never_equals_v1() -> None:
    request = _request()
    v1 = ai_cache.cache_key(
        hook=vision.HOOK,
        model=request["model"],
        prompt_version=request["prompt_version"],
        schema_version=request["schema_version"],
        redaction_version=request["redaction_version"],
        payload_digest=DIGEST,
    )
    assert ai_cache.request_cache_key(request, DIGEST) != v1
