"""vision.prompt.v6.qwen (D76) and the vLLM reader's prompt variant: synthetic values only.

The vLLM reader is back on v4.qwen since D77 (``test_vision_prompt_v7.py``); v4-v7,
the Gemini prompt, schema and golden stay byte-identical; every vLLM cache key and the
vLLM consent fingerprints follow the variant, the Gemini and legacy fingerprints do
not. The bench can still read v4.qwen (``--vision-prompt-variant qwen``) under the
exact D74 keys.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
from collections.abc import Callable
from types import MappingProxyType
from typing import Any

import pytest
from reconbench_app.ai import cache as ai_cache
from reconbench_app.ai import consent, prompts
from reconbench_app.ai.contracts import SCHEMA_VERSIONS, VisionOutput, parse_output
from reconbench_app.ai.gate import payload_digest
from reconbench_app.ai.limits import MIN_LOCAL_OUTPUT_TOKENS, AiLimits, local_output_budget
from reconbench_app.ai.prompts import (
    QWEN_EXAMPLE_V5,
    VISION_PROMPT_VARIANTS,
    VISION_PROMPT_VERSIONS,
    VISION_SYSTEM_QWEN,
    prompt_version,
    vision_prompt,
)
from reconbench_app.ai.provider import ProviderImage
from reconbench_app.ai.redaction import IMAGE_MASK_VERSION
from reconbench_app.ai.runtime_checks import fingerprint_for
from reconbench_app.ai.vision import HOOK, reader_params
from reconbench_app.ai.worker.runner import _prompt_bytes
from reconbench_app.extraction.vision import VisionReaderInfo, vision_reader
from scripts import accuracy_vision

MODEL = "gemini-3.8-flash"
LOCAL_MODEL = "qwen3.5:9b"
URL = "http://vllm.example.test/vllm/v1"
DOC_TYPES = ("invoice", "po", "grn")

# sha256 of the v5.qwen system instruction per document type (the D75 read).
V5_QWEN_SYSTEM = {
    "invoice": "43fddd20ae3f9ee07765900fcff54ec2bf226c1c493bc7e9eeee3177e997d20a",
    "po": "64ee474430fb6a20098b5409225602edf8e4fea1a992b85c162fdaf93d496b7b",
    "grn": "b2516d9f42b7def974f7a455667c9938f567d36e33d9a3d9705970786b5dd881",
}
# v2 cache key of one synthetic page per variant (see _key); Gemini as before D75.
KEYS = {
    "qwen": "2042f72658b40689ac25ca399d4c6df8a6fc4d0c5cf96830c1fb901ec1d07370",
    "qwen_v5": "c4f827e095860c0848b3f0d0d12684396a35716110289600e054a973c2ed71f5",
    "qwen_v6": "69cd1b2c4f60e10eababf4b54797c471c6c2b3d39023fe31373bb7f70176c278",
    "gemini": "a7a3cf0cfcd004330f9d6d11c4ca07f6b9eec0ad5f0b415e08c52ce0fd1f1922",
}
# Consent fingerprints of the vLLM single/shadow settings per vLLM prompt variant.
VLLM_FINGERPRINTS = {
    "qwen": {
        "vllm_single": "17e3f93b40f2d705e1b45b01a26109b8c81acc8dba7dc7744d4b243e79ca14a2",
        "vllm_shadow": "83dae0e64c8d5fefa7749d0f940811e93dee74c944bcd9e075ce50c877123983",
    },
    "qwen_v5": {
        "vllm_single": "14bb16a653945bb79629c82f12f8b131b94112826c0054c217939d0221c8f68e",
    },
    "qwen_v6": {
        "vllm_single": "c8c4c7f26fecc113fe28f72faf62e868ff3968f337067006e5ef889b1330359f",
    },
    "qwen_v7": {
        "vllm_single": "0786bc3915063e41839be065bd49018e41233eb1710f2161ecf4fc675a7de872",
    },
}
GEMINI_SHADOW = "9311d3f975a703a83dc9adee75a4990e7f3f90339496e8aab336729743614396"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _image() -> ProviderImage:
    data = b"synthetic-page"
    return ProviderImage("Page 1", "image/jpeg", data, hashlib.sha256(data).hexdigest(), "high")


def _system(variant: str, doc_type: str = "invoice") -> str:
    dialect = "gemini" if variant == "gemini" else "openai_json_schema"
    payload = vision_prompt(doc_type, [_image()], variant=variant, dialect=dialect)  # type: ignore[arg-type]
    return payload.system_instruction


def _vllm() -> VisionReaderInfo:
    reader = vision_reader("vision_openai_compat")
    assert reader is not None
    return reader


def _gemini() -> VisionReaderInfo:
    reader = vision_reader("vision_gemini")
    assert reader is not None
    return reader


def _rules(system: str) -> list[str]:
    return [line for line in system.split("\n") if line[:2].rstrip(".").isdigit()]


# --------------------------------------------------------------------------- the v6 text


def test_v6_is_v4_plus_the_rules_that_worked_and_two_guards() -> None:
    v4, v6 = _rules(_system("qwen")), _rules(_system("qwen_v6"))
    assert len(v4) == len(v6) == 9
    # Kept word for word: coordinates, masks, lines, unreadable, data-not-instructions.
    for index in (0, 1, 2, 5, 7, 8):
        assert v6[index] == v4[index]
    assert v6[3].startswith(v4[3])  # supplier/buyer, then the own-number rule
    system = _system("qwen_v6")
    for rule in (
        "doc_no and doc_date are this document's own number and date",
        "on a purchase order its PO number, on a goods receipt its GRN number",
        "never an order, due or delivery date",
        "grand_total is the final total, also when there is no tax",
        "po_ref only for a number labelled as the buyer's purchase order",
        "the Order Number or Order ID is marketplace_order_ref, never po_ref",
        "Every handwritten or stamped mark goes in annotations",
        "even when it repeats a printed number",
        "looks_like (po_number, grn_number, invoice_number, date, amount",
    ):
        assert rule in system, rule


def test_v6_drops_the_v5_rules_that_hurt() -> None:
    system = _system("qwen_v6")
    assert "totals block" not in system
    assert "page subtotals" not in system
    assert "full item text of that one row" not in system
    assert "never leave a printed value out silently" not in system.lower()


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_v6_example_is_synthetic_and_parses(doc_type: str) -> None:
    system = _system("qwen_v6", doc_type)
    example = system.split("never copy its values):\n", 1)[1]
    assert json.loads(example) == json.loads(json.dumps(dict(QWEN_EXAMPLE_V5)))
    assert "SYN-INV-0001" in example and "SYN-PO-0042" in example
    parsed = parse_output(HOOK, example)
    assert isinstance(parsed, VisionOutput) and len(parsed.annotations) == 1


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_v5_qwen_is_byte_identical(doc_type: str) -> None:
    assert _sha(_system("qwen_v5", doc_type)) == V5_QWEN_SYSTEM[doc_type]
    assert VISION_SYSTEM_QWEN.startswith("You read one page image")


@pytest.mark.parametrize("doc_type", DOC_TYPES)
def test_v6_answer_budget_is_back_above_v5(doc_type: str) -> None:
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

    assert budget("qwen_v5") < budget("qwen_v6") < budget("qwen")
    assert budget("qwen_v6") >= 3_300 > MIN_LOCAL_OUTPUT_TOKENS


# --------------------------------------------------------------------------- the vLLM reader


def test_variants_and_reader_versions() -> None:
    assert VISION_PROMPT_VARIANTS == {
        "gemini": "vision.prompt.v4.gemini",
        "gemini_q2": "vision.prompt.v4.q2.gemini",
        "qwen": "vision.prompt.v4.qwen",
        "qwen_q2": "vision.prompt.v4.q2.qwen",
        "qwen_v5": "vision.prompt.v5.qwen",
        "qwen_v6": "vision.prompt.v6.qwen",
        "qwen_v7": "vision.prompt.v7.qwen",
    }
    assert VISION_PROMPT_VERSIONS == {
        "vision_gemini": "vision.prompt.v4.gemini",
        "vision_openai_compat": "vision.prompt.v4.qwen",
    }
    assert _vllm().prompt_variant == "qwen"
    assert prompt_version(HOOK, reader="vision_openai_compat") == "vision.prompt.v4.qwen"
    assert _gemini().prompt_variant == "gemini"


def _key(reader: VisionReaderInfo, model: str) -> str:
    """The v2 cache key of one synthetic page as the AI worker and the bench build it."""

    payload = vision_prompt(
        "invoice", [_image()], variant=reader.prompt_variant, dialect=reader.schema_dialect
    )
    digest = payload_digest(
        system_instruction=payload.system_instruction,
        input_text=payload.input_text,
        response_schema=payload.response_schema,
        images=payload.image_digest_parts(),
    )
    return ai_cache.request_cache_key(
        {
            "hook": HOOK,
            "provider": reader.provider,
            "model": model,
            "prompt_version": VISION_PROMPT_VARIANTS[reader.prompt_variant],
            "schema_version": SCHEMA_VERSIONS[HOOK],
            "redaction_version": IMAGE_MASK_VERSION,
            "reader_params": reader_params(reader),
        },
        digest,
    )


def test_cache_keys_change_for_vllm_only() -> None:
    full_pages = dataclasses.replace(_vllm(), band_prompt_variant=None)
    for variant in ("qwen", "qwen_v5", "qwen_v6"):
        assert (
            _key(dataclasses.replace(full_pages, prompt_variant=variant), LOCAL_MODEL)
            == (KEYS[variant])
        )
    assert _key(full_pages, LOCAL_MODEL) == KEYS["qwen"]  # back on the D74 keys (D77)
    # The app reader adds the header-band read (D93): its own key, never a v4 answer's.
    assert _key(_vllm(), LOCAL_MODEL) not in set(KEYS.values())
    assert _key(_gemini(), MODEL) == KEYS["gemini"]


# --------------------------------------------------------------------------- bench arm


def test_bench_vllm_arm_reads_the_app_reader_and_can_read_v4_again() -> None:
    # A bench arm reads full pages only; the band is its own arm (D93).
    full_pages = dataclasses.replace(_vllm(), band_prompt_variant=None)
    assert accuracy_vision.VisionArm(provider="vllm", base_url=URL).reader() == full_pages
    old = accuracy_vision.VisionArm(provider="vllm", base_url=URL, prompt_variant="qwen")
    reader = old.reader()
    assert reader == dataclasses.replace(full_pages, prompt_variant="qwen")
    assert _key(reader, LOCAL_MODEL) == KEYS["qwen"]  # the D74 answers are found again
    assert old.describe()["prompt_version"] == "vision.prompt.v4.qwen"
    assert accuracy_vision.DEFAULT_ARM.reader() == _gemini()
    assert accuracy_vision.DEFAULT_ARM.prompt_variant is None


@pytest.mark.parametrize(
    ("provider", "variant", "model"),
    [
        ("gemini", "qwen", MODEL),
        ("gemini", "qwen_v6", MODEL),
        ("vllm", "gemini", LOCAL_MODEL),
        ("vllm", "qwen_v9", LOCAL_MODEL),
    ],
)
def test_bench_refuses_a_prompt_variant_of_the_other_provider(
    provider: str, variant: str, model: str
) -> None:
    arm = accuracy_vision.VisionArm(provider=provider, prompt_variant=variant)
    with pytest.raises(accuracy_vision.ArmRefused, match="prompt-variant"):
        accuracy_vision.check_arm(arm, allowlist=URL, model=model)


def test_bench_accepts_each_qwen_variant_for_vllm() -> None:
    for variant in ("qwen", "qwen_v5", "qwen_v6", "qwen_v7"):
        arm = accuracy_vision.VisionArm(provider="vllm", base_url=URL, prompt_variant=variant)
        assert accuracy_vision.check_arm(arm, allowlist=URL, model=LOCAL_MODEL).prompt_variant == (
            variant
        )
    gemini = accuracy_vision.VisionArm(prompt_variant="gemini")
    assert accuracy_vision.check_arm(gemini, allowlist=None, model=MODEL) == gemini


# --------------------------------------------------------------------------- consent


def _fingerprints() -> dict[str, str]:
    local = {"vllm_model": LOCAL_MODEL, "vllm_base_url": URL}
    common = {"provider": "gemini", "model": MODEL}
    return {
        "legacy": consent.consent_fingerprint(mode="ai_assisted", **common),
        "off": fingerprint_for(
            mode="ai_first", reader_strategy="off", primary_reader=None, **common
        ),
        "gemini_single": fingerprint_for(
            mode="ai_first", reader_strategy="single", primary_reader="gemini", **common
        ),
        "gemini_shadow": fingerprint_for(
            mode="ai_assisted", reader_strategy="shadow", primary_reader="gemini", **common
        ),
        "vllm_single": fingerprint_for(
            mode="ai_first", reader_strategy="single", primary_reader="vllm", **local, **common
        ),
        "vllm_shadow": fingerprint_for(
            mode="ai_first", reader_strategy="shadow", primary_reader="vllm", **local, **common
        ),
    }


@pytest.fixture
def vllm_variant(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], dict[str, str]]:
    """The fingerprints with the vLLM reader on another prompt variant."""

    original = consent.vision_reader

    def fingerprints(variant: str) -> dict[str, str]:
        def reader(name: str) -> Any:
            found = original(name)
            if found is not None and found.name == "vision_openai_compat":
                return dataclasses.replace(found, prompt_variant=variant)
            return found

        with monkeypatch.context() as patch:
            patch.setattr(consent, "vision_reader", reader)
            patch.setattr(
                prompts,
                "VISION_PROMPT_VERSIONS",
                MappingProxyType(
                    {
                        **VISION_PROMPT_VERSIONS,
                        "vision_openai_compat": VISION_PROMPT_VARIANTS[variant],
                    }
                ),
            )
            return _fingerprints()

    return fingerprints


def test_only_the_vllm_fingerprints_follow_the_prompt_variant(
    vllm_variant: Callable[[str], dict[str, str]],
) -> None:
    current = _fingerprints()
    variants = ("qwen", "qwen_v5", "qwen_v6", "qwen_v7")
    by_variant = {variant: vllm_variant(variant) for variant in variants}
    assert by_variant["qwen"] == current  # the reader is on v4.qwen again (D77)
    for name in ("legacy", "off", "gemini_single", "gemini_shadow", "vllm_shadow"):
        assert len({item[name] for item in by_variant.values()}) == 1, name
    assert len({item["vllm_single"] for item in by_variant.values()}) == len(variants)
    for variant, pins in VLLM_FINGERPRINTS.items():
        for name, value in pins.items():
            assert by_variant[variant][name] == value, (variant, name)
    assert current["gemini_shadow"] == GEMINI_SHADOW
