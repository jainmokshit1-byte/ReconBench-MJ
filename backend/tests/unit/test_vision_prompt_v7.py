"""vision.prompt.v7.qwen (D77): v4.qwen with only the marketplace rule. Synthetic values only."""

from __future__ import annotations

import difflib
import hashlib
import json

import pytest
from reconbench_app.ai.limits import MIN_LOCAL_OUTPUT_TOKENS, AiLimits, local_output_budget
from reconbench_app.ai.prompts import QWEN_EXAMPLE, prompt_version, vision_prompt
from reconbench_app.ai.provider import ProviderImage
from reconbench_app.ai.vision import HOOK
from reconbench_app.ai.worker.runner import _prompt_bytes
from reconbench_app.extraction.vision import vision_reader

DOC_TYPES = ("invoice", "po", "grn")


def _image() -> ProviderImage:
    data = b"synthetic-page"
    return ProviderImage("Page 1", "image/jpeg", data, hashlib.sha256(data).hexdigest(), "high")


def _system(variant: str, doc_type: str = "invoice") -> str:
    payload = vision_prompt(
        doc_type,  # type: ignore[arg-type]
        [_image()],
        variant=variant,
        dialect="openai_json_schema",
    )
    return payload.system_instruction


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_v7_changes_exactly_one_v4_rule(doc_type: str) -> None:
    v4, v7 = _system("qwen", doc_type).split("\n"), _system("qwen_v7", doc_type).split("\n")
    changed = [line for line in difflib.ndiff(v4, v7) if line[:2] in {"- ", "+ "}]
    assert len(changed) == 2
    assert changed[0].startswith("- 5. po_ref only for the buyer's purchase order number")
    assert changed[1].startswith("+ 5. po_ref only for the buyer's purchase order number")
    assert "the Order Number or Order ID is marketplace_order_ref, never po_ref or " in changed[1]
    assert changed[1].endswith("Other numbers other_ref.")


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_v7_uses_the_v4_example_and_keeps_the_budget(doc_type: str) -> None:
    system = _system("qwen_v7", doc_type)
    example = system.split("never copy its values):\n", 1)[1]
    assert json.loads(example) == json.loads(json.dumps(dict(QWEN_EXAMPLE)))
    kind = AiLimits().h6_vision_local

    def budget(variant: str) -> int:
        payload = vision_prompt(
            doc_type,  # type: ignore[arg-type]
            [_image()],
            variant=variant,
            dialect="openai_json_schema",
        )
        return local_output_budget(
            context_window=8_192,
            prompt_bytes=_prompt_bytes(payload),
            image_tokens=kind.image_tokens_estimate or 0,
        )

    assert budget("qwen") - 50 <= budget("qwen_v7") < budget("qwen")
    assert budget("qwen_v7") > MIN_LOCAL_OUTPUT_TOKENS


def test_the_vllm_reader_is_back_on_v4() -> None:
    # D77: v7 lost header values on the marketplace invoice; v4.qwen stays the reader's.
    reader = vision_reader("vision_openai_compat")
    assert reader is not None and reader.prompt_variant == "qwen"
    assert prompt_version(HOOK, reader="vision_openai_compat") == "vision.prompt.v4.qwen"
