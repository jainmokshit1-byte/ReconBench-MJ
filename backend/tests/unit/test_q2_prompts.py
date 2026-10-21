"""WI-Q2 prompt variants, schema v5 and bench arms (accuracy plan WI-Q2, D91/D92).

The v4 prompts and schema stay byte-identical (their cache keys and the stored answers
keep working); only the Q2 variants carry the typing rule and ``vision.schema.v5``.
"""

from __future__ import annotations

import json

import pytest
from reconbench_app.ai.contracts import (
    VISION_SCHEMA_V5,
    vision_response_schema,
    vision_response_schema_v5,
)
from reconbench_app.ai.prompts import (
    QWEN_EXAMPLE_Q2,
    VISION_SYSTEM,
    VISION_SYSTEM_Q2,
    VISION_SYSTEM_QWEN,
    VISION_SYSTEM_QWEN_Q2,
    vision_prompt,
    vision_schema_version,
)
from reconbench_app.ai.provider import ProviderImage
from reconbench_core.extraction.vision import ANNOTATION_LOOKS, ANNOTATION_LOOKS_V5
from scripts import accuracy_vision


def _looks(schema: dict[str, object]) -> list[str]:
    annotation = schema["properties"]["annotations"]["items"]["properties"]  # type: ignore[index]
    looks = annotation["looks_like"]  # type: ignore[index]
    if "anyOf" in looks:
        looks = looks["anyOf"][0]
    return list(looks["enum"])


def _image() -> ProviderImage:
    return ProviderImage(caption="Page 1", mime_type="image/jpeg", data=b"x", sha256="0" * 64)


def test_q2_prompts_add_one_typing_rule_to_the_current_v4_prompts() -> None:
    for v4, q2 in ((VISION_SYSTEM, VISION_SYSTEM_Q2), (VISION_SYSTEM_QWEN, VISION_SYSTEM_QWEN_Q2)):
        assert q2 != v4 and "service_po_number" in q2 and "grn_number" in q2
        assert "service_po_number" not in v4
    looks = [item["looks_like"] for item in QWEN_EXAMPLE_Q2["annotations"]]
    assert looks == ["po_number", "grn_number"]


@pytest.mark.parametrize("dialect", ["gemini", "openai_json_schema"])
def test_schema_v5_adds_service_po_number_and_v4_is_unchanged(dialect: str) -> None:
    v4 = vision_response_schema(dialect)  # type: ignore[arg-type]
    v5 = vision_response_schema_v5(dialect)  # type: ignore[arg-type]
    assert _looks(v4) == list(ANNOTATION_LOOKS)
    assert _looks(v5) == list(ANNOTATION_LOOKS_V5) == [*ANNOTATION_LOOKS, "service_po_number"]
    v5_without = json.loads(json.dumps(v5).replace(', "service_po_number"', ""))
    assert v5_without == v4  # nothing else differs


def test_each_variant_gets_its_schema_version_and_payload_schema() -> None:
    assert vision_schema_version("qwen") == vision_schema_version("gemini") == "vision.schema.v4"
    assert (
        vision_schema_version("qwen_q2") == vision_schema_version("gemini_q2") == VISION_SCHEMA_V5
    )
    q2 = vision_prompt("invoice", [_image()], variant="qwen_q2", dialect="openai_json_schema")
    v4 = vision_prompt("invoice", [_image()], variant="qwen", dialect="openai_json_schema")
    assert "service_po_number" in json.dumps(q2.response_schema)
    assert "service_po_number" not in json.dumps(v4.response_schema)
    assert v4.response_schema == vision_response_schema("openai_json_schema")


def test_bench_arms_accept_the_q2_variants_per_provider_and_the_band_strategy() -> None:
    check = accuracy_vision.check_arm
    gemini = accuracy_vision.VisionArm(provider="gemini", prompt_variant="gemini_q2")
    assert check(gemini, allowlist=None, model="gemini-3.5-flash-lite") == gemini
    with pytest.raises(accuracy_vision.ArmRefused):
        check(
            accuracy_vision.VisionArm(provider="gemini", prompt_variant="qwen_q2"),
            allowlist=None,
            model="gemini-3.5-flash-lite",
        )
    with pytest.raises(accuracy_vision.ArmRefused):
        check(
            accuracy_vision.VisionArm(provider="vllm", prompt_variant="gemini_q2"),
            allowlist=None,
            model="qwen3.5:9b",
        )
    band = accuracy_vision.VisionArm(provider="vllm", prompt_variant="qwen_q2", strategy="band")
    assert check(band, allowlist=None, model="qwen3.5:9b").strategy == "band"
    assert band.describe()["schema_version"] == VISION_SCHEMA_V5
    # WI-BE8 (D97): the cropped two-pass read is a Qwen bench arm; Gemini is refused.
    crops = accuracy_vision.VisionArm(provider="vllm", strategy="crops")
    assert check(crops, allowlist=None, model="qwen3.5:9b").strategy == "crops"
    with pytest.raises(accuracy_vision.ArmRefused, match="WI-BE8"):
        check(
            accuracy_vision.VisionArm(provider="gemini", strategy="crops"),
            allowlist=None,
            model="gemini-3.5-flash-lite",
        )
