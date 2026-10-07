"""Schema §5.1 tolerance overrides reach the engine and are validated at save."""

from __future__ import annotations

import copy
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from reconbench_app.configuration import ConfigurationBody, ConfigurationResponse
from reconbench_app.db.seed_data import DEFAULT_CONFIG
from reconbench_app.reconciliation_service import reconciliation_config

SURVIVOR = UUID("00000000-0000-0000-0000-00000000000a")
MERGED = UUID("00000000-0000-0000-0000-00000000000b")
OTHER = UUID("00000000-0000-0000-0000-00000000000c")


def _item(code: str, check: str = "unit_rate", pct: str = "1") -> dict[str, Any]:
    return {"item_code": code, "vendor_id": None, "tolerances": {check: {"pct": pct, "abs": "0"}}}


def _vendor(vendor_id: UUID, check: str = "unit_rate") -> dict[str, Any]:
    return {"vendor_id": str(vendor_id), "tolerances": {check: {"pct": "1", "abs": "0"}}}


def _body(overrides: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(DEFAULT_CONFIG)
    body["engine"]["overrides"] = overrides
    return body


def test_engine_overrides_are_folded_with_merge_family_and_item_scope() -> None:
    body = _body(
        {
            "vendors": [
                {"vendor_id": str(SURVIVOR), "tolerances": {"unit_rate": {"pct": "2", "abs": "5"}}}
            ],
            "items": [
                {
                    "item_code": " SKU-1 ",
                    "vendor_id": None,
                    "tolerances": {"unit_rate": {"pct": "0", "abs": "0"}},
                },
                {
                    "item_code": "SKU-1",
                    "vendor_id": str(MERGED),
                    "tolerances": {"qty_vs_grn": {"pct": "1", "abs": "2"}},
                },
            ],
        }
    )
    config = reconciliation_config(
        body,
        effective_date=date(2026, 10, 1),
        vendor_roots={SURVIVOR: SURVIVOR, MERGED: SURVIVOR, OTHER: OTHER},
    )
    unit_rate = config.tolerances["unit_rate"]
    assert set(unit_rate.by_vendor) == {str(SURVIVOR), str(MERGED)}
    assert unit_rate.by_vendor[str(MERGED)].pct == Decimal("2")
    assert unit_rate.by_item["sku-1"].abs == Decimal("0")
    qty = config.tolerances["qty_vs_grn"]
    assert set(qty.by_vendor_item) == {f"{SURVIVOR}|sku-1", f"{MERGED}|sku-1"}
    assert str(OTHER) not in unit_rate.by_vendor
    # Global values stay as configured.
    assert unit_rate.pct == Decimal("0.5") and unit_rate.abs == Decimal("1")


def test_override_on_merged_vendor_keeps_its_own_value_over_survivor_override() -> None:
    body = _body(
        {
            "vendors": [
                {"vendor_id": str(MERGED), "tolerances": {"unit_rate": {"pct": "3", "abs": "0"}}},
                {"vendor_id": str(SURVIVOR), "tolerances": {"unit_rate": {"pct": "2", "abs": "0"}}},
            ],
            "items": [],
        }
    )
    config = reconciliation_config(
        body,
        effective_date=date(2026, 10, 1),
        vendor_roots={SURVIVOR: SURVIVOR, MERGED: SURVIVOR},
    )
    assert config.tolerances["unit_rate"].by_vendor[str(MERGED)].pct == Decimal("3")
    assert config.tolerances["unit_rate"].by_vendor[str(SURVIVOR)].pct == Decimal("2")


@pytest.mark.parametrize(
    "overrides",
    [
        {"vendors": [], "items": [_item("  ")]},
        {"vendors": [], "items": [_item("A", check="bogus")]},
        {"vendors": [{"vendor_id": str(OTHER), "tolerances": {}}], "items": []},
        {"vendors": [_vendor(OTHER), _vendor(OTHER, "arithmetic")], "items": []},
        {"vendors": [], "items": [_item("a"), _item("A ", pct="2")]},
    ],
    ids=["blank-item", "unknown-check", "empty-tolerances", "duplicate-vendor", "duplicate-item"],
)
def test_invalid_overrides_are_rejected(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ConfigurationBody.model_validate(_body(overrides))


def test_buyer_gstin_check_digit_is_validated() -> None:
    body = copy.deepcopy(DEFAULT_CONFIG)
    body["engine"]["buyer_entities"] = [{"gstin": "27AAPFU0939F1ZV", "name": "Valid"}]
    ConfigurationBody.model_validate(body)
    body["engine"]["buyer_entities"] = [{"gstin": "27AAPFU0939F1ZW", "name": "Bad check digit"}]
    with pytest.raises(ValidationError):
        ConfigurationBody.model_validate(body)


def test_configuration_response_carries_selected_defaults() -> None:
    factory = ConfigurationResponse.model_fields["defaults"].default_factory
    assert factory is not None
    defaults = factory()  # type: ignore[call-arg]
    assert defaults["engine"]["tolerances"]["unit_rate"] == {"pct": "0.5", "abs": "1"}
