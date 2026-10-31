"""Unit tests for self-hosted AI image token calculations and output cap (KI-006, Step 10)."""

from __future__ import annotations

import hashlib
import io
import math

from PIL import Image
from reconbench_app.ai import vision
from reconbench_app.ai.limits import (
    AiLimits,
    CallKindLimits,
    local_image_tokens,
    local_output_budget,
)
from reconbench_app.ai.prompts import PromptPayload
from reconbench_app.ai.provider import ProviderImage
from reconbench_app.ai.worker.hooks.vision import _band_part
from reconbench_app.ai.worker.runner import _local_image_tokens, _prompt_bytes
from reconbench_app.extraction.vision import vision_reader


def _make_page(page_num: int, width: int, height: int) -> vision.PageRow:
    image = Image.new("RGB", (width, height), "white")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=90)
    data = buf.getvalue()
    return vision.PageRow(
        page=page_num,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="image/jpeg",
        width=width,
        height=height,
        render={"version": "vision.render.v1", "rotation": 0},
        mask={"version": "image_mask.v1", "boxes": []},
        data=data,
    )


def test_u1_the_formula() -> None:
    """U1: verify local_image_tokens(w, h, token_px=32) and assert >= round(w/32)*round(h/32)."""
    assert local_image_tokens(1449, 2048, token_px=32) == 2944
    assert local_image_tokens(2048, 1449, token_px=32) == 2944
    assert local_image_tokens(1582, 2048, token_px=32) == 3200
    assert local_image_tokens(1449, 717, token_px=32) == 1058
    assert local_image_tokens(2048, 507, token_px=32) == 1024
    assert local_image_tokens(2048, 2048, token_px=32) == 4096
    assert local_image_tokens(1, 1, token_px=32) == 1

    first_five = [
        (1449, 2048),
        (2048, 1449),
        (1582, 2048),
        (1449, 717),
        (2048, 507),
    ]
    for w, h in first_five:
        server_cost = round(w / 32) * round(h / 32)
        estimate = local_image_tokens(w, h, token_px=32)
        assert estimate >= server_cost, f"w={w}, h={h}: {estimate} < {server_cost}"


def test_u2_no_overflow_on_any_measured_shape() -> None:
    """U2: verify real input tokens + cap <= 8192 for measured shapes, doc-4 cap > 3386."""
    reader = vision_reader("vision_openai_compat")
    limits = AiLimits()
    kind_limits = limits.h6_vision_local

    def compute_cap(pages: list[vision.PageRow]) -> tuple[int, tuple[PromptPayload, ...]]:
        parts = vision.page_prompts("invoice", pages, reader)
        assert parts is not None
        band = _band_part("invoice", pages, reader)
        calls = (*parts, band) if band is not None else parts
        budgets = [
            local_output_budget(
                context_window=8_192,
                prompt_bytes=_prompt_bytes(call),
                image_tokens=_local_image_tokens(call, kind_limits),
            )
            for call in calls
        ]
        return min(kind_limits.max_output_tokens, min(budgets)), calls

    # 1 page A4 (portrait)
    a4_pages = [_make_page(1, 1449, 2048)]
    a4_cap, a4_calls = compute_cap(a4_pages)
    a4_real_inputs = [3774, 2049]
    for real_input, _call in zip(a4_real_inputs, a4_calls, strict=True):
        assert real_input + a4_cap <= 8_192

    # 1 page Letter
    letter_pages = [_make_page(1, 1582, 2048)]
    letter_cap, letter_calls = compute_cap(letter_pages)
    letter_real_inputs = [4030, 2137]
    for real_input, _call in zip(letter_real_inputs, letter_calls, strict=True):
        assert real_input + letter_cap <= 8_192

    # doc-4 shape (sideways scan upright + A4 portrait)
    doc4_pages = [_make_page(1, 2048, 1449), _make_page(2, 1449, 2048)]
    doc4_cap, doc4_calls = compute_cap(doc4_pages)
    doc4_real_inputs = [3774, 3774, 2083]
    for real_input, _call in zip(doc4_real_inputs, doc4_calls, strict=True):
        assert real_input + doc4_cap <= 8_192

    print(f"\n[U2] doc-4 shape computed cap: {doc4_cap}")
    assert doc4_cap > 3386

    # Text estimate checks
    page_prompt = a4_calls[0]
    band_prompt = a4_calls[1]
    page_bytes = _prompt_bytes(page_prompt)
    band_bytes = _prompt_bytes(band_prompt)
    ceil_page = math.ceil(page_bytes / 3)
    ceil_band = math.ceil(band_bytes / 3)

    print(f"[U2] ceil(page_prompt_bytes/3) = {ceil_page}")
    print(f"[U2] ceil(band_prompt_bytes/3) = {ceil_band}")

    assert ceil_page >= 894 - 256
    assert ceil_band >= 1059 - 256


def test_u3_shape_aware() -> None:
    """U3: 2048x2048 page gets a lower cap than A4 and satisfies real_input + cap <= 8192."""
    reader = vision_reader("vision_openai_compat")
    limits = AiLimits()
    kind_limits = limits.h6_vision_local

    a4_pages = [_make_page(1, 1449, 2048)]
    a4_parts = vision.page_prompts("invoice", a4_pages, reader)
    assert a4_parts is not None
    a4_band = _band_part("invoice", a4_pages, reader)
    a4_calls = (*a4_parts, a4_band) if a4_band is not None else a4_parts
    a4_cap = min(
        kind_limits.max_output_tokens,
        min(
            local_output_budget(
                context_window=8_192,
                prompt_bytes=_prompt_bytes(call),
                image_tokens=_local_image_tokens(call, kind_limits),
            )
            for call in a4_calls
        ),
    )

    square_pages = [_make_page(1, 2048, 2048)]
    sq_parts = vision.page_prompts("invoice", square_pages, reader)
    assert sq_parts is not None
    sq_band = _band_part("invoice", square_pages, reader)
    sq_calls = (*sq_parts, sq_band) if sq_band is not None else sq_parts
    sq_cap = min(
        kind_limits.max_output_tokens,
        min(
            local_output_budget(
                context_window=8_192,
                prompt_bytes=_prompt_bytes(call),
                image_tokens=_local_image_tokens(call, kind_limits),
            )
            for call in sq_calls
        ),
    )

    assert sq_cap < a4_cap
    real_input_square = 894 + 4096
    assert real_input_square + sq_cap <= 8_192


def test_u4_fallback() -> None:
    """U4: unreadable image bytes fallback to 4096;
    kind without image_token_px falls back to estimate.
    """
    limits = AiLimits()
    kind_limits = limits.h6_vision_local

    unreadable_image = ProviderImage(
        caption="Page 1",
        mime_type="image/jpeg",
        sha256="0" * 64,
        data=b"corrupted-data",
    )
    payload = PromptPayload(
        system_instruction="Read page",
        input_text="Invoice",
        response_schema={},
        images=(unreadable_image,),
    )
    # Pillow can't open corrupted data -> fallback to 2048x2048 = 4096 tokens
    assert _local_image_tokens(payload, kind_limits) == 4096

    # Kind without image_token_px -> len(images) * image_tokens_estimate
    no_token_px_limits = CallKindLimits(
        max_input_bytes=1000,
        max_output_tokens=100,
        image_tokens_estimate=1200,
        image_token_px=None,
    )
    assert _local_image_tokens(payload, no_token_px_limits) == 1200
