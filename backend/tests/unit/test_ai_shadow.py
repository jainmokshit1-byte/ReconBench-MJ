"""Shadow reader (LLM plan WI-BE6): labels without values, request routing, fixed consent.

Offline and synthetic: no database, no network, no provider call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from reconbench_app.ai import runtime_checks
from reconbench_app.ai.runtime_checks import (
    BLOCK_AI_OFF,
    BLOCK_PROVIDER_CHANGED,
    ProviderConfig,
    SettingsSnapshot,
    fingerprint_for,
    request_blocker,
)
from reconbench_app.ai.shadow import LABELS, compare
from reconbench_core.extraction.vision import (
    SentPage,
    VisionField,
    VisionLine,
    VisionReading,
    VisionUnreadable,
    check_reading,
)
from reconbench_core.normalize.gstin import calculate_gstin_checksum
from reconbench_core.schema import Document

MODEL = "gemini-3.8-flash"
LOCAL_MODEL = "qwen3.5:9b"
URL = "http://vllm.example.test/vllm/v1"
SUPPLIER = "27AAPFU0939F1Z" + calculate_gstin_checksum("27AAPFU0939F1Z")
BOX = (100, 100, 120, 300)
# Synthetic values that must never appear in a comparison row or a report.
SYNTHETIC_VALUES = ("SYN-SHADOW-0042", "1180.00", "1190.00", "1000.00", "90.00", "470.00")


def _field(value: Any) -> dict[str, Any]:
    return {"value": value, "confidence": "0.65", "source": "rule", "evidence": {}}


def _rules() -> Document:
    return Document.model_validate(
        {
            "header": {
                "doc_type": "invoice",
                "extras": {},
                "doc_no": _field("SYN-SHADOW-0042"),
                "taxable_total": _field("1000.00"),
                "cgst": _field("90.00"),
                "sgst": _field("90.00"),
                "grand_total": _field("1180.00"),
            },
            "lines": [
                {
                    "line_no": 1,
                    "quantity": _field("2"),
                    "rate": _field("235.00"),
                    "amount": _field("470.00"),
                    "extras": {},
                }
            ],
            "needs_verification": True,
            "reason_codes": [],
        }
    )


def _check() -> Any:
    reading = VisionReading(
        doc_kind="invoice",
        fields=(
            VisionField("doc_no", "SYN-SHADOW-0042", 1, BOX),
            VisionField("grand_total", "1190.00", 1, (200, 100, 220, 300)),
            VisionField("supplier_gstin", SUPPLIER, 1, (300, 100, 320, 300)),
        ),
        lines=(
            VisionLine(
                1,
                (400, 50, 420, 950),
                {"description": "Synthetic widget", "quantity": "2", "amount": "470.00"},
            ),
        ),
        unreadable=(VisionUnreadable("doc_date", 1, (500, 100, 520, 300)),),
    )
    return check_reading(reading, pages={1: SentPage(1)})


def test_compare_labels_every_field_and_keeps_no_value() -> None:
    comparison = compare(_rules(), _check())
    labels = {item["field_key"]: item["label"] for item in comparison.labels}

    assert labels == {
        "header:doc_no": "agree_ocr",
        "header:grand_total": "disagree_ocr",
        "header:vendor_gstin": "llm_only",
        "header:doc_date": "unreadable",
        "header:taxable_total": "ocr_only",
        "header:cgst": "ocr_only",
        "header:sgst": "ocr_only",
        "line:1:amount": "agree_ocr",
        "line:1:quantity": "agree_ocr",
        "line:1:rate": "ocr_only",
    }
    counts = comparison.counts
    assert counts["fields_total"] == len(labels) == sum(counts[label] for label in LABELS)
    assert (counts["agree_ocr"], counts["disagree_ocr"], counts["llm_only"]) == (3, 1, 1)
    assert (counts["ocr_only"], counts["unreadable"], counts["rows"]) == (4, 1, 1)
    assert set(counts) == {
        "fields_total",
        *LABELS,
        "check_failed_by_reason",
        "dropped_by_reason",
        "rows",
    }
    # Labels only: every entry is a field key and a label, nothing else.
    assert all(set(item) == {"field_key", "label"} for item in comparison.labels)
    stored = json.dumps({"counts": counts, "labels": comparison.labels})
    for value in (*SYNTHETIC_VALUES, SUPPLIER, "Synthetic widget"):
        assert value not in stored


def test_an_empty_answer_labels_the_rules_fields_only() -> None:
    comparison = compare(_rules(), check_reading(VisionReading("invoice"), pages={1: SentPage(1)}))

    assert {item["label"] for item in comparison.labels} == {"ocr_only"}
    assert comparison.counts["rows"] == 0


def _snapshot(strategy: str) -> SettingsSnapshot:
    snapshot = SettingsSnapshot(
        workspace_id=uuid4(),
        mode="ai_first",
        provider="gemini",
        model=MODEL,
        version=4,
        consent_given=True,
        consent_fingerprint=None,
        key_secret_id=None,
        key_storage="saved",
        reader_strategy=strategy,
        primary_reader="vllm",
        vllm=ProviderConfig("vllm", True, LOCAL_MODEL, URL, None, 2),
    )
    return SettingsSnapshot(
        **{
            **{name: getattr(snapshot, name) for name in SettingsSnapshot.__slots__},
            "consent_fingerprint": snapshot.expected_fingerprint,
        }
    )


@pytest.mark.parametrize(
    ("strategy", "shadow", "blocked"),
    [
        ("shadow", True, BLOCK_PROVIDER_CHANGED),
        ("single", True, BLOCK_AI_OFF),
        ("shadow", False, BLOCK_PROVIDER_CHANGED),
        ("single", False, None),
    ],
)
def test_a_shadow_request_runs_only_while_the_workspace_shadows(
    monkeypatch: pytest.MonkeyPatch, strategy: str, shadow: bool, blocked: str | None
) -> None:
    settings = _snapshot(strategy)
    monkeypatch.setattr(runtime_checks, "load_settings", lambda *_args, **_kw: settings)
    request = {
        "workspace_id": settings.workspace_id,
        "settings_version": settings.version,
        "call_kind": "hook",
        "hook": "h6_vision",
        "provider": "vllm",
        "model": LOCAL_MODEL,
        "mode": "ai_first",
        "consent_fingerprint": settings.consent_fingerprint,
        "shadow": shadow,
    }

    result = request_blocker(None, request, datetime.now(UTC), provider_name="vllm")  # type: ignore[arg-type]

    assert result == blocked


def test_shadow_keeps_h1_and_never_lists_the_vision_hook() -> None:
    hooks = _snapshot("shadow").hooks

    assert any(name.startswith("h1_extract:") for name in hooks)
    assert not any(name.startswith("h6_vision:") for name in hooks)


def test_shadow_consent_fingerprints_are_pinned() -> None:
    # Shadow+vLLM covers the shadow reader's prompt version: cb7d2ee1… under v4.qwen
    # (D65), e4eef8f4… under v5.qwen (D75), 7ee9e281… under v6.qwen (D76), 8ea94862…
    # under v7.qwen (D77, test_vision_prompt_v6); the reader is on v4.qwen again (D77),
    # so the D65 value is back. Shadow+Gemini as D65.
    assert (
        fingerprint_for(
            provider="gemini",
            model=MODEL,
            mode="ai_first",
            reader_strategy="shadow",
            primary_reader="vllm",
            vllm_model=LOCAL_MODEL,
            vllm_base_url=URL,
        )
        == "83dae0e64c8d5fefa7749d0f940811e93dee74c944bcd9e075ce50c877123983"
    )
    assert (
        fingerprint_for(
            provider="gemini",
            model=MODEL,
            mode="ai_assisted",
            reader_strategy="shadow",
            primary_reader="gemini",
        )
        == "9311d3f975a703a83dc9adee75a4990e7f3f90339496e8aab336729743614396"
    )
