"""Scanned-invoice switch and reading method (accuracy plan PD-2c, PD-2d; D103 decisions 1-3).

Offline and synthetic: no database, no network, no provider call.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError
from reconbench_app.ai.runtime_checks import ProviderConfig, SettingsSnapshot
from reconbench_app.ai.settings_service import AiSettingsUpdate, _scanned_ai_allowed, _Strategy
from reconbench_app.ingestion import _reading_method
from reconbench_core.schema import ExtractionMethod

URL = "http://vllm.example.test/vllm/v1"

_CAPABLE_VLLM = ProviderConfig(
    provider="vllm",
    enabled=True,
    model="qwen3.5:9b",
    base_url=URL,
    key_secret_id=None,
    version=1,
    supports_vision=True,
    supports_json_schema=True,
)


def _snapshot(**overrides: object) -> SettingsSnapshot:
    values: dict[str, object] = {
        "workspace_id": uuid4(),
        "mode": "ai_assisted",
        "provider": "gemini",
        "model": "gemini-3.8-flash",
        "version": 3,
        "consent_given": True,
        "consent_fingerprint": None,
        "key_secret_id": None,
        "key_storage": "saved",
        "reader_strategy": "single",
        "primary_reader": "vllm",
        "scanned_invoice_reader": "ai",
        "vllm": _CAPABLE_VLLM,
    }
    values.update(overrides)
    return SettingsSnapshot(**values)  # type: ignore[arg-type]


def test_scanned_invoices_are_read_only_with_the_switch_on_ai_and_a_capable_vllm() -> None:
    assert _snapshot().reads_scanned_invoices


@pytest.mark.parametrize(
    "overrides",
    [
        {"scanned_invoice_reader": "rules"},  # the default position
        {"mode": "rules_only"},
        {"reader_strategy": "off", "primary_reader": None},
        {"primary_reader": "gemini"},  # Gemini waits for O-25 (Q11)
        {"vllm": None},
        {"vllm": replace(_CAPABLE_VLLM, supports_vision=None)},
        {"vllm": replace(_CAPABLE_VLLM, supports_vision=False)},
        {"vllm": replace(_CAPABLE_VLLM, supports_json_schema=None)},
    ],
)
def test_no_ai_reading_otherwise(overrides: dict[str, object]) -> None:
    assert not _snapshot(**overrides).reads_scanned_invoices


def test_snapshot_default_is_rules() -> None:
    snapshot = SettingsSnapshot(
        workspace_id=uuid4(),
        mode="ai_assisted",
        provider="gemini",
        model="gemini-3.8-flash",
        version=1,
        consent_given=True,
        consent_fingerprint=None,
        key_secret_id=None,
        key_storage="saved",
        reader_strategy="single",
        primary_reader="vllm",
        vllm=_CAPABLE_VLLM,
    )
    assert snapshot.scanned_invoice_reader == "rules"
    assert not snapshot.reads_scanned_invoices


def _card(**overrides: Any) -> dict[str, Any]:
    card: dict[str, Any] = {
        "enabled": True,
        "model": "qwen3.5:9b",
        "base_url": URL,
        "supports_vision": True,
        "supports_json_schema": True,
    }
    card.update(overrides)
    return card


_VLLM_READER = _Strategy("single", "vllm", "suggest")


def test_switch_may_be_set_to_ai_with_consent_and_a_tested_company_server() -> None:
    assert _scanned_ai_allowed(
        mode="ai_first", consent_current=True, strategy=_VLLM_READER, vllm_card=_card()
    )


@pytest.mark.parametrize(
    ("mode", "consent", "strategy", "card"),
    [
        ("rules_only", True, _VLLM_READER, _card()),
        ("ai_first", False, _VLLM_READER, _card()),
        ("ai_first", True, _Strategy("off", None, "suggest"), _card()),
        ("ai_first", True, _Strategy("single", "gemini", "suggest"), _card()),
        ("ai_first", True, _VLLM_READER, None),
        ("ai_first", True, _VLLM_READER, _card(enabled=False)),
        ("ai_first", True, _VLLM_READER, _card(model=None)),
        ("ai_first", True, _VLLM_READER, _card(supports_vision=None)),
        ("ai_first", True, _VLLM_READER, _card(supports_json_schema=False)),
    ],
)
def test_switch_refuses_ai_otherwise(
    mode: str, consent: bool, strategy: _Strategy, card: dict[str, Any] | None
) -> None:
    assert not _scanned_ai_allowed(
        mode=mode, consent_current=consent, strategy=strategy, vllm_card=card
    )


def test_update_body_accepts_only_rules_or_ai() -> None:
    assert AiSettingsUpdate(scanned_invoices="ai").scanned_invoices == "ai"
    assert AiSettingsUpdate().scanned_invoices is None
    with pytest.raises(ValidationError):
        AiSettingsUpdate(scanned_invoices="gemini")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (ExtractionMethod.PDF_TEXT, "rules"),
        (ExtractionMethod.PDF_OCR, "rules"),
        (ExtractionMethod.XML_PROFILE, "rules"),
        (ExtractionMethod.CSV_MAPPING, "rules"),
        (ExtractionMethod.SHELL, "rules"),
        (ExtractionMethod.MANUAL, "rules"),
        (ExtractionMethod.AI_FIRST, "ai"),
        (ExtractionMethod.AI_FALLBACK, "ai"),
    ],
)
def test_reading_method_of_a_new_version(method: ExtractionMethod, expected: str) -> None:
    assert _reading_method(method) == expected
