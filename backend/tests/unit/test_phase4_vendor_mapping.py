"""Vendor identity resolution follows the ordered, conservative cascade."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest
from reconbench_core.mapping import (
    VendorAliasRecord,
    VendorDocument,
    VendorRecord,
    resolve_vendors,
)
from reconbench_core.schema import VendorRule


def _id(value: int) -> UUID:
    return UUID(int=value)


def _vendor(
    number: int,
    name: str,
    *,
    pan: str | None = None,
    gstins: tuple[str, ...] = (),
    aliases: tuple[str, ...] = (),
) -> VendorRecord:
    return VendorRecord(
        vendor_id=_id(number),
        display_name=name,
        pan=pan,
        gstins=gstins,
        aliases=aliases,
    )


def _document(
    number: int,
    name: str | None,
    gstin: str | None = None,
    *,
    natural_key: str = "",
) -> VendorDocument:
    return VendorDocument(
        document_id=_id(number),
        natural_key=natural_key,
        vendor_name=name,
        vendor_gstin=gstin,
    )


def test_vendor_cascade_uses_exact_gstin_before_name() -> None:
    exact_gstin_vendor = _vendor(
        2,
        "Different Display Name",
        gstins=("27AAPFU0939F1ZV",),
    )
    same_name_vendor = _vendor(1, "Acer India", pan="BBBBB1234X")

    result = resolve_vendors(
        [_document(10, "Acer India", "27AAPFU0939F1ZV")],
        [same_name_vendor, exact_gstin_vendor],
    )

    resolution = result.resolutions[0]
    assert resolution.vendor_id == exact_gstin_vendor.vendor_id
    assert resolution.rule is VendorRule.GSTIN


def test_vendor_cascade_resolves_same_pan_before_name() -> None:
    vendor = _vendor(1, "Acer Legal", pan="AAPFU0939F")

    result = resolve_vendors(
        [_document(10, "Ledger Name", "27AAPFU0939F1ZV")],
        [vendor],
    )

    assert result.resolutions[0].vendor_id == vendor.vendor_id
    assert result.resolutions[0].rule is VendorRule.PAN


def test_normalized_name_and_saved_aliases_resolve_in_order() -> None:
    name_vendor = _vendor(1, "Acer India Pvt Ltd")
    alias_vendor = _vendor(2, "Different Legal Entity")
    documents = [_document(10, "ACER INDIA")]
    result = resolve_vendors(
        documents,
        [name_vendor, alias_vendor],
        [VendorAliasRecord(vendor_id=alias_vendor.vendor_id, alias="ACER INDIA")],
    )

    assert result.resolutions[0].vendor_id == name_vendor.vendor_id
    assert result.resolutions[0].rule is VendorRule.NAME_EXACT

    alias_result = resolve_vendors(
        [_document(11, "Acer Ledger")],
        [alias_vendor],
        [VendorAliasRecord(vendor_id=alias_vendor.vendor_id, alias="Acer Ledger")],
    )
    assert alias_result.resolutions[0].vendor_id == alias_vendor.vendor_id
    assert alias_result.resolutions[0].rule is VendorRule.ALIAS


def test_scn_12_ledger_name_alias_resolves_to_legal_vendor() -> None:
    legal_vendor = _vendor(2, "Acer India Private Limited")
    ledger_alias = VendorAliasRecord(
        vendor_id=legal_vendor.vendor_id,
        alias="ACER INDIA - LOCAL LEDGER",
    )

    result = resolve_vendors(
        [_document(10, "ACER INDIA - LOCAL LEDGER")],
        [legal_vendor],
        [ledger_alias],
    )

    assert result.resolutions[0].vendor_id == legal_vendor.vendor_id
    assert result.resolutions[0].rule is VendorRule.ALIAS


def test_fuzzy_auto_resolution_requires_anchored_vendor_and_unique_top_score() -> None:
    vendor = _vendor(1, "Acer India", pan="AAPFU0939F")

    result = resolve_vendors([_document(10, "Acerr India")], [vendor])

    resolution = result.resolutions[0]
    assert resolution.vendor_id == vendor.vendor_id
    assert resolution.rule is VendorRule.FUZZY

    unanchored = resolve_vendors([_document(11, "Acerr India")], [_vendor(2, "Acer India")])
    assert unanchored.resolutions[0].vendor_id is None
    assert not unanchored.resolutions[0].suggestions


def test_midrange_fuzzy_match_becomes_suggestion_not_assignment() -> None:
    vendor = _vendor(1, "Acer India", pan="AAPFU0939F")

    result = resolve_vendors([_document(10, "Acer Ind")], [vendor])

    resolution = result.resolutions[0]
    assert resolution.vendor_id is None
    assert resolution.rule is VendorRule.NONE
    assert len(resolution.suggestions) == 1
    assert resolution.suggestions[0].score == Decimal("0.8889")
    assert resolution.suggestions[0].merge_allowed


def test_different_pan_never_auto_merges_even_at_high_similarity() -> None:
    vendor = _vendor(1, "Acer India", pan="BBBBB1234X")

    result = resolve_vendors(
        [_document(10, "Acerr India", "27AAPFU0939F1ZV")],
        [vendor],
    )

    resolution = result.resolutions[0]
    assert resolution.vendor_id is None
    assert resolution.suggestions
    assert resolution.suggestions[0].pan_conflict
    assert not resolution.suggestions[0].merge_allowed


def test_name_exact_does_not_bypass_different_pan_guard() -> None:
    vendor = _vendor(1, "Acer India", pan="BBBBB1234X")

    result = resolve_vendors(
        [_document(10, "Acer India", "27AAPFU0939F1ZV")],
        [vendor],
    )

    assert result.resolutions[0].vendor_id is None
    assert result.resolutions[0].suggestions[0].pan_conflict
    assert not result.resolutions[0].suggestions[0].merge_allowed


@pytest.mark.parametrize("name", ["Acer India", "Acerr India", "Acer India Pvt Ltd"])
def test_different_pan_never_auto_merges_for_exact_or_fuzzy_names(name: str) -> None:
    vendor = _vendor(1, "Acer India", pan="BBBBB1234X")

    result = resolve_vendors(
        [_document(10, name, "27AAPFU0939F1ZV")],
        [vendor],
    )

    resolution = result.resolutions[0]
    assert resolution.vendor_id != vendor.vendor_id
    assert all(not suggestion.merge_allowed for suggestion in resolution.suggestions)


def test_adding_unrelated_vendor_documents_does_not_change_existing_resolution() -> None:
    acer = _vendor(1, "Acer India", pan="AAPFU0939F")
    other = _vendor(2, "Boeing India", pan="BBBBB1234X")
    target = _document(10, "Acerr India", natural_key="target")
    other_before = _document(20, "Boeing India", natural_key="other")
    other_after = _document(21, "Boeing Aerospace India", natural_key="other-new")

    before = resolve_vendors([target, other_before], [acer, other])
    after = resolve_vendors([target, other_before, other_after], [acer, other])

    before_target = next(
        item for item in before.resolutions if item.document_id == target.document_id
    )
    after_target = next(
        item for item in after.resolutions if item.document_id == target.document_id
    )
    assert before_target == after_target


def test_fuzzy_tie_is_a_suggestion_and_batch_order_is_stable() -> None:
    vendors = [
        _vendor(1, "Acer India", pan="AAPFU0939F"),
        _vendor(2, "Acer India", pan="BBBBB1234X"),
    ]
    documents = [
        _document(12, "Acerr India", natural_key="B"),
        _document(11, "Acer India", natural_key="A"),
    ]

    result = resolve_vendors(documents, vendors)
    reversed_result = resolve_vendors(list(reversed(documents)), list(reversed(vendors)))

    assert result == reversed_result
    assert result.resolutions[0].document_id == _id(11)
    assert result.resolutions[1].vendor_id is None
    assert len(result.resolutions[1].suggestions) == 2
