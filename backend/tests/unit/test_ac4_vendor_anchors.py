"""AC-4 vendor hygiene: who may create a vendor, and which name it gets."""

from __future__ import annotations

import random
from decimal import Decimal
from uuid import UUID

from reconbench_app.vendor_resolution import anchor_document, field_verified
from reconbench_core.mapping import (
    AnchorDocument,
    AnchorVendor,
    VendorNameStatus,
    plan_vendor_anchors,
)
from reconbench_core.normalize.gstin import calculate_gstin_checksum
from reconbench_core.parsers.types import BuyerEntityHint


def _gstin(state: str, pan: str, entity: str = "1") -> str:
    body = f"{state}{pan}{entity}Z"
    return body + calculate_gstin_checksum(body)


SUPPLIER = _gstin("27", "AAPFU0939F")
SUPPLIER_OTHER_STATE = _gstin("29", "AAPFU0939F")
SECOND_SUPPLIER = _gstin("27", "AABCS1234K")
BUYER = _gstin("27", "AACCB9999Q")


def _id(value: int) -> UUID:
    return UUID(int=value)


def _doc(
    number: int,
    gstin: str | None,
    name: str | None = None,
    *,
    gstin_verified: bool = True,
    name_verified: bool = False,
) -> AnchorDocument:
    return AnchorDocument(
        document_id=_id(number),
        natural_key=f"key-{number:03d}",
        vendor_gstin=gstin,
        gstin_verified=gstin_verified,
        vendor_name=name,
        name_verified=name_verified,
    )


def test_verified_gstin_without_name_creates_a_pending_vendor_never_a_gstin_name() -> None:
    plan = plan_vendor_anchors([_doc(1, SUPPLIER)], [])

    assert len(plan.new_vendors) == 1
    vendor = plan.new_vendors[0]
    assert vendor.name == ""
    assert vendor.name_status is VendorNameStatus.PENDING
    assert [item.gstin for item in plan.registrations] == [SUPPLIER]


def test_unverified_invalid_or_buyer_gstin_creates_nothing() -> None:
    broken = SUPPLIER[:-1] + ("A" if SUPPLIER[-1] != "A" else "B")
    plan = plan_vendor_anchors(
        [
            _doc(1, SUPPLIER, "Uncertain Traders", gstin_verified=False),
            _doc(2, broken, "Broken Checksum Ltd"),
            _doc(3, BUYER, "Own Company"),
            _doc(4, None, "Name Only Services"),
        ],
        [],
        frozenset({BUYER}),
    )

    assert plan.new_vendors == ()
    assert plan.registrations == ()
    assert dict(plan.skipped) == {
        _id(1): "gstin_unverified",
        _id(2): "gstin_invalid",
        _id(3): "buyer_gstin",
    }


def test_best_name_wins_regardless_of_document_order() -> None:
    documents = [
        _doc(1, SUPPLIER, "Acme Tradrs"),
        _doc(2, SUPPLIER, "Acme Traders"),
        _doc(3, SUPPLIER, "Acme Traders"),
        _doc(4, SUPPLIER_OTHER_STATE, "Acme Traders Private Limited", name_verified=True),
    ]
    expected = plan_vendor_anchors(documents, [])
    for seed in range(5):
        shuffled = documents[:]
        random.Random(seed).shuffle(shuffled)
        assert plan_vendor_anchors(shuffled, []) == expected

    assert len(expected.new_vendors) == 1, "GSTINs sharing a PAN are one vendor"
    assert expected.new_vendors[0].name == "Acme Traders Private Limited"
    assert expected.new_vendors[0].name_status is VendorNameStatus.VERIFIED
    assert sorted(item.gstin for item in expected.registrations) == sorted(
        [SUPPLIER, SUPPLIER_OTHER_STATE]
    )


def test_provisional_majority_spelling_is_used_and_ocr_typos_never_become_aliases() -> None:
    plan = plan_vendor_anchors(
        [
            _doc(1, SUPPLIER, "Acme Traders"),
            _doc(2, SUPPLIER, "Acme Traders"),
            _doc(3, SUPPLIER, "Acme Tradrs"),
        ],
        [],
    )

    assert plan.new_vendors[0].name == "Acme Traders"
    assert plan.new_vendors[0].name_status is VendorNameStatus.PROVISIONAL
    assert plan.aliases == ()


def test_verified_variant_name_is_learned_as_alias() -> None:
    plan = plan_vendor_anchors(
        [
            _doc(1, SUPPLIER, "Acme Traders Pvt Ltd", name_verified=True),
            _doc(2, SUPPLIER, "Acme Traders Private Limited (Pune)", name_verified=True),
            _doc(3, SUPPLIER, "Acme Traders Pvt Ltd", name_verified=True),
        ],
        [],
    )

    assert plan.new_vendors[0].name == "Acme Traders Pvt Ltd"
    assert [item.alias for item in plan.aliases] == ["Acme Traders Private Limited (Pune)"]


def test_pending_vendor_name_is_upgraded_but_a_manual_name_is_never_replaced() -> None:
    pending = AnchorVendor(
        vendor_id=_id(10),
        name="",
        name_status=VendorNameStatus.PENDING,
        pan="AAPFU0939F",
        gstins=(SUPPLIER,),
    )
    manual = AnchorVendor(
        vendor_id=_id(11),
        name="Seller Chosen Name",
        name_status=VendorNameStatus.MANUAL,
        pan="AABCS1234K",
        gstins=(SECOND_SUPPLIER,),
    )
    plan = plan_vendor_anchors(
        [
            _doc(1, SUPPLIER, "Acme Traders"),
            _doc(2, SECOND_SUPPLIER, "Second Supplies", name_verified=True),
        ],
        [pending, manual],
    )

    assert plan.new_vendors == ()
    assert plan.registrations == ()
    assert [(item.vendor_id, item.name, item.name_status) for item in plan.renames] == [
        (_id(10), "Acme Traders", VendorNameStatus.PROVISIONAL)
    ]
    assert [(item.vendor_id, item.alias) for item in plan.aliases] == [(_id(11), "Second Supplies")]


def test_equal_status_name_does_not_churn_an_existing_vendor() -> None:
    existing = AnchorVendor(
        vendor_id=_id(10),
        name="Acme Traders",
        name_status=VendorNameStatus.PROVISIONAL,
        gstins=(SUPPLIER,),
    )
    plan = plan_vendor_anchors([_doc(1, SUPPLIER, "Acme Trading Co")], [existing])

    assert plan.renames == ()
    assert plan.aliases == ()


def test_new_gstin_joins_the_single_vendor_with_its_pan() -> None:
    existing = AnchorVendor(
        vendor_id=_id(10), name="Acme Traders", pan="AAPFU0939F", gstins=(SUPPLIER,)
    )
    plan = plan_vendor_anchors([_doc(1, SUPPLIER_OTHER_STATE, "Acme Traders")], [existing])

    assert plan.new_vendors == ()
    assert [(item.vendor_id, item.gstin) for item in plan.registrations] == [
        (_id(10), SUPPLIER_OTHER_STATE)
    ]


def test_pan_held_by_two_vendors_waits_for_a_person() -> None:
    vendors = [
        AnchorVendor(vendor_id=_id(10), name="Acme One", pan="AAPFU0939F"),
        AnchorVendor(vendor_id=_id(11), name="Acme Two", pan="AAPFU0939F"),
    ]
    plan = plan_vendor_anchors([_doc(1, SUPPLIER, "Acme Traders")], vendors)

    assert plan.new_vendors == ()
    assert plan.registrations == ()
    assert dict(plan.skipped) == {_id(1): "pan_ambiguous"}


def test_anchor_document_drops_label_gstin_and_buyer_names() -> None:
    buyers = (BuyerEntityHint(gstin=BUYER, name="Benchmark Computer Solutions"),)

    def name_of(raw: str) -> str | None:
        return anchor_document(
            document_id=_id(1),
            natural_key="k",
            vendor_gstin=SUPPLIER,
            vendor_name=raw,
            gstin_verified=True,
            name_verified=True,
            buyers=buyers,
        ).vendor_name

    assert name_of("(Bill from)") is None
    assert name_of(SUPPLIER) is None
    assert name_of("Benchmark Computer Solutions Pvt Ltd") is None
    assert name_of("  Acme Traders  ") == "Acme Traders"


def test_field_verified_trusts_digital_sources_people_and_checked_rule_values_only() -> None:
    assert field_verified(Decimal("0.50"), "rule", structured=True)
    assert field_verified(Decimal("0.10"), "manual", structured=False)
    assert field_verified(Decimal("0.98"), "rule", structured=False)
    assert not field_verified(Decimal("0.97"), "rule", structured=False)
    assert not field_verified(Decimal("0.99"), "ai", structured=False)
    assert not field_verified(None, None, structured=False)
