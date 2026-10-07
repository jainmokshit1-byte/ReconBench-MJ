"""The PO anchor ladder is deterministic and conservative at every step."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import reconbench_core.mapping.links as links_module
from reconbench_core.mapping import (
    MappingConfig,
    MappingDocument,
    MappingPO,
    resolve_links,
)
from reconbench_core.parsers import parse
from reconbench_core.schema import (
    DocType,
    Document,
    LinkMethod,
    LinkState,
    ReasonCode,
    ResolutionOutcome,
)

_VENDOR = UUID(int=100)


def _id(value: int) -> UUID:
    return UUID(int=value)


def _document(xml: str, doc_type: DocType) -> Document:
    result = parse(
        xml.encode(),
        {"file_format": "xml", "doc_type": doc_type},
    )
    assert result.success and len(result.documents) == 1
    return result.documents[0]


def _po_xml(number: str, *, date: str = "20260101") -> str:
    return (
        f"<PurchaseOrder><OrderNo>{number}</OrderNo><OrderDate>{date}</OrderDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>1000</TaxableTotal>"
        "<Line><Description>Widget model A</Description><Qty>100</Qty>"
        "<Rate>10</Rate><Amount>1000</Amount></Line></PurchaseOrder>"
    )


def _invoice_xml(
    number: str,
    *,
    po_ref: str | None = None,
    grn_ref: str | None = None,
    date: str = "20260201",
    qty: str = "1",
    rate: str = "10",
    amount: str = "10",
    description: str = "Widget model A",
) -> str:
    refs = f"<PORef>{po_ref}</PORef>" if po_ref else ""
    grn = f"<GRNRef>{grn_ref}</GRNRef>" if grn_ref else ""
    total = str(Decimal(qty) * Decimal(rate))
    return (
        f"<Invoice><InvoiceNo>{number}</InvoiceNo><InvoiceDate>{date}</InvoiceDate>"
        f"<Supplier>Acme Supplies</Supplier>{refs}{grn}"
        f"<TaxableTotal>{amount}</TaxableTotal><InvoiceTotal>{total}</InvoiceTotal>"
        f"<Line><Description>{description}</Description><Qty>{qty}</Qty>"
        f"<Rate>{rate}</Rate><Amount>{amount}</Amount></Line></Invoice>"
    )


def _grn_xml(number: str, po_ref: str) -> str:
    return (
        f"<GRN><GRNNo>{number}</GRNNo><GRNDate>20260115</GRNDate>"
        f"<Supplier>Acme Supplies</Supplier><PORef>{po_ref}</PORef>"
        "<Line><Description>Widget model A</Description><Qty>10</Qty></Line></GRN>"
    )


def _multi_line_invoice_xml(*, explicit_first_po: str | None = None) -> str:
    first_line_ref = f"<PORef>{explicit_first_po}</PORef>" if explicit_first_po else ""
    return (
        "<Invoice><InvoiceNo>INV-MULTI</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        "<Supplier>Acme Supplies</Supplier><PORef>PO-100</PORef><PORef>PO-200</PORef>"
        "<TaxableTotal>30</TaxableTotal><InvoiceTotal>30</InvoiceTotal>"
        f"<Line>{first_line_ref}<Description>Widget model A</Description><Qty>1</Qty>"
        "<Rate>10</Rate><Amount>10</Amount></Line>"
        "<Line><Description>Dock model B</Description><Qty>2</Qty>"
        "<Rate>10</Rate><Amount>20</Amount></Line></Invoice>"
    )


def _item_po_xml(number: str, description: str) -> str:
    return (
        f"<PurchaseOrder><OrderNo>{number}</OrderNo><OrderDate>20260101</OrderDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>1000</TaxableTotal>"
        f"<Line><Description>{description}</Description><Qty>100</Qty>"
        "<Rate>10</Rate><Amount>1000</Amount></Line></PurchaseOrder>"
    )


def _source(
    number: int,
    document: Document,
    *,
    natural_key: str | None = None,
    **kwargs: Any,
) -> MappingDocument:
    return MappingDocument(
        document_id=_id(number),
        natural_key=natural_key or str(number),
        vendor_id=_VENDOR,
        document=document,
        **kwargs,
    )


def _po(number: int, document: Document, *, vendor_id: UUID = _VENDOR) -> MappingPO:
    return MappingPO(
        document_id=_id(number),
        vendor_id=vendor_id,
        document=document,
        open_balance=Decimal("1000"),
        open_qty_by_line={1: Decimal("100")},
        grn_support_qty_by_line={1: Decimal("100")},
    )


def test_step_zero_labeled_reference_creates_auto_link() -> None:
    invoice = _source(10, _document(_invoice_xml("INV-10", po_ref="PO-100"), DocType.INVOICE))
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    result = resolve_links([invoice], [po])[0]

    assert result.outcome is ResolutionOutcome.LINKED
    assert result.links[0].method is LinkMethod.LABELED
    assert result.links[0].state is LinkState.AUTO
    assert result.links[0].confidence == Decimal("0.99")
    assert result.links[0].line_no == 1


def test_scn_09_po_number_only_in_narration_uses_reverse_lookup() -> None:
    invoice = _source(
        10,
        _document(_invoice_xml("INV-10"), DocType.INVOICE),
        source_text="Notes: delivered against PO-100; thank you",
    )
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    result = resolve_links([invoice], [po])[0]

    assert result.links[0].method is LinkMethod.REVERSE
    assert result.links[0].confidence == Decimal("0.97")
    assert result.links[0].state is LinkState.AUTO


def test_reverse_lookup_requires_nearby_label_for_short_po_numbers() -> None:
    invoice = _source(
        10,
        _document(_invoice_xml("INV-10"), DocType.INVOICE),
        source_text="Buyer order number: PO-42",
    )
    po = _po(20, _document(_po_xml("PO-42"), DocType.PO))

    result = resolve_links([invoice], [po])[0]

    assert result.links[0].method is LinkMethod.REVERSE


def test_scn_09_po_number_only_on_grn_links_invoice_via_grn() -> None:
    invoice = _source(
        10,
        _document(_invoice_xml("INV-10", grn_ref="GRN-1"), DocType.INVOICE),
    )
    grn = _source(11, _document(_grn_xml("GRN-1", "PO-100"), DocType.GRN))
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    results = resolve_links([invoice, grn], [po])
    invoice_result = next(result for result in results if result.document_id == invoice.document_id)
    grn_result = next(result for result in results if result.document_id == grn.document_id)

    assert grn_result.links[0].method is LinkMethod.LABELED
    assert invoice_result.links[0].method is LinkMethod.VIA_GRN
    assert invoice_result.links[0].via_document_id == grn.document_id
    assert invoice_result.links[0].state is LinkState.AUTO


def test_invoice_cannot_inherit_another_vendors_po_through_a_referenced_grn() -> None:
    other_vendor = _id(200)
    invoice = _source(
        12,
        _document(_invoice_xml("INV-12", grn_ref="GRN-CROSS-VENDOR"), DocType.INVOICE),
    )
    other_vendor_grn = _source(
        13,
        _document(_grn_xml("GRN-CROSS-VENDOR", "PO-OTHER"), DocType.GRN),
    ).model_copy(update={"vendor_id": other_vendor})
    other_vendor_po = _po(
        14,
        _document(_po_xml("PO-OTHER"), DocType.PO),
        vendor_id=other_vendor,
    )

    invoice_result = next(
        result
        for result in resolve_links((invoice, other_vendor_grn), (other_vendor_po,))
        if result.document_id == invoice.document_id
    )

    assert not invoice_result.links
    assert invoice_result.reason_code is ReasonCode.VENDOR_MISMATCH


def test_grn_without_reference_uses_the_content_resolution_path() -> None:
    grn = _source(
        11,
        _document(
            "<GRN><GRNNo>GRN-CONTENT</GRNNo><GRNDate>20260201</GRNDate>"
            "<Supplier>Acme Supplies</Supplier><TaxableTotal>10</TaxableTotal>"
            "<Line><Description>Widget model A</Description><Qty>1</Qty><Rate>10</Rate>"
            "<Amount>10</Amount></Line></GRN>",
            DocType.GRN,
        ),
    )
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    result = resolve_links(
        [grn],
        [po],
        MappingConfig(step3_min_score=Decimal("0.50"), step3_min_lead=Decimal("0")),
    )[0]

    assert result.outcome is ResolutionOutcome.SUGGESTED
    assert result.links[0].method is LinkMethod.CONTENT
    assert result.links[0].state is LinkState.SUGGESTED


def test_scn_13_invoice_citing_another_vendors_po_is_a_hard_mismatch() -> None:
    invoice = _source(10, _document(_invoice_xml("INV-10", po_ref="PO-100"), DocType.INVOICE))
    other_vendor_po = _po(
        20,
        _document(_po_xml("PO-100"), DocType.PO),
        vendor_id=_id(200),
    )

    result = resolve_links([invoice], [other_vendor_po])[0]

    assert not result.links
    assert result.reason_code is ReasonCode.VENDOR_MISMATCH


def test_labeled_reference_missing_from_pool_is_not_replaced_by_content_guess() -> None:
    invoice = _source(10, _document(_invoice_xml("INV-10", po_ref="PO-404"), DocType.INVOICE))
    similar_po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    result = resolve_links([invoice], [similar_po])[0]

    assert not result.links
    assert result.reason_code is ReasonCode.NO_PO_LINK
    assert result.reason_detail == "PO_NOT_IN_POOL"
    assert result.reference_text == "PO-404"


def test_scn_10_prd_po_number_variants_link_to_the_same_purchase_order() -> None:
    invoice = _source(
        10,
        _document(_invoice_xml("INV-10", po_ref="PO/26-27/0142"), DocType.INVOICE),
    )
    po = _po(20, _document(_po_xml("PO-26-27-142"), DocType.PO))

    result = resolve_links([invoice], [po])[0]

    assert result.links[0].to_document_id == po.document_id
    assert result.links[0].method is LinkMethod.LABELED


def test_multi_po_invoice_assigns_each_line_to_the_matching_referenced_po() -> None:
    invoice = _source(10, _document(_multi_line_invoice_xml(), DocType.INVOICE))
    po_a = _po(20, _document(_item_po_xml("PO-100", "Widget model A"), DocType.PO))
    po_b = _po(21, _document(_item_po_xml("PO-200", "Dock model B"), DocType.PO))

    normal = resolve_links([invoice], [po_b, po_a])[0]
    reversed_inputs = resolve_links([invoice], [po_a, po_b])[0]

    assert normal.outcome is ResolutionOutcome.SUGGESTED
    assert [(link.line_no, link.to_document_id, link.method) for link in normal.links] == [
        (1, po_a.document_id, LinkMethod.CONTENT),
        (2, po_b.document_id, LinkMethod.CONTENT),
    ]
    assert [(link.line_no, link.to_document_id, link.method) for link in normal.links] == [
        (link.line_no, link.to_document_id, link.method) for link in reversed_inputs.links
    ]


def test_multi_po_invoice_keeps_explicit_line_ref_and_infers_other_line() -> None:
    invoice = _source(
        10,
        _document(_multi_line_invoice_xml(explicit_first_po="PO-100"), DocType.INVOICE),
    )
    po_a = _po(20, _document(_item_po_xml("PO-100", "Widget model A"), DocType.PO))
    po_b = _po(21, _document(_item_po_xml("PO-200", "Dock model B"), DocType.PO))

    result = resolve_links([invoice], [po_a, po_b])[0]

    assert [(link.line_no, link.to_document_id, link.method) for link in result.links] == [
        (1, po_a.document_id, LinkMethod.LABELED),
        (2, po_b.document_id, LinkMethod.CONTENT),
    ]


def test_multi_po_reference_outside_pool_stops_content_fallback() -> None:
    invoice = _source(10, _document(_multi_line_invoice_xml(), DocType.INVOICE))
    only_po = _po(20, _document(_item_po_xml("PO-100", "Widget model A"), DocType.PO))

    result = resolve_links([invoice], [only_po])[0]

    assert not result.links
    assert result.reason_code is ReasonCode.NO_PO_LINK
    assert result.reason_detail == "PO_NOT_IN_POOL"
    assert result.reference_text == "PO-200"


def test_link_confidence_boundary_at_point_ninety_five() -> None:
    invoice = _source(10, _document(_invoice_xml("INV-10", po_ref="PO-100"), DocType.INVOICE))
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    at_boundary = resolve_links([invoice], [po], MappingConfig(confidence_labeled=Decimal("0.95")))[
        0
    ]
    below_boundary = resolve_links(
        [invoice], [po], MappingConfig(confidence_labeled=Decimal("0.9499"))
    )[0]

    assert at_boundary.links[0].state is LinkState.AUTO
    assert below_boundary.links[0].state is LinkState.SUGGESTED


def test_scn_09_po_number_nowhere_uses_content_review_suggestion() -> None:
    invoice = _source(10, _document(_invoice_xml("INV-10"), DocType.INVOICE))
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    result = resolve_links([invoice], [po])[0]

    assert result.outcome is ResolutionOutcome.SUGGESTED
    assert result.links[0].method is LinkMethod.CONTENT
    assert result.links[0].confidence <= Decimal("0.90")
    assert result.links[0].state is LinkState.SUGGESTED


def test_equal_content_candidates_are_returned_for_manual_pick() -> None:
    invoice = _source(10, _document(_invoice_xml("INV-10"), DocType.INVOICE))
    pos = [
        _po(20, _document(_po_xml("PO-100"), DocType.PO)),
        _po(21, _document(_po_xml("PO-200"), DocType.PO)),
    ]

    result = resolve_links([invoice], pos)[0]

    assert result.outcome is ResolutionOutcome.UNLINKED
    assert not result.links
    assert len(result.candidates) == 2
    assert result.reason_detail == "CONTENT_AMBIGUOUS"


def test_manual_link_is_confirmed_and_rejected_link_stays_unlinked() -> None:
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))
    manual = _source(
        10,
        _document(_invoice_xml("INV-10"), DocType.INVOICE),
        manual_po_ids=(po.document_id,),
    )
    rejected = _source(
        11,
        _document(_invoice_xml("INV-11", po_ref="PO-100"), DocType.INVOICE),
        rejected_po_ids=(po.document_id,),
    )

    manual_result, rejected_result = resolve_links([manual, rejected], [po])

    assert manual_result.links[0].method is LinkMethod.MANUAL
    assert manual_result.links[0].state is LinkState.CONFIRMED
    assert not rejected_result.links


def test_content_phase_is_sorted_and_earlier_suggestion_reserves_balance() -> None:
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))
    po = po.model_copy(
        update={
            "open_balance": Decimal("10"),
            "open_qty_by_line": {1: Decimal("1")},
        }
    )
    later = _source(12, _document(_invoice_xml("INV-12"), DocType.INVOICE), natural_key="z")
    earlier = _source(11, _document(_invoice_xml("INV-11"), DocType.INVOICE), natural_key="a")

    results = resolve_links([later, earlier], [po])

    first, second = results
    assert first.document_id == earlier.document_id
    assert first.links[0].method is LinkMethod.CONTENT
    assert second.document_id == later.document_id
    assert not second.links


def test_link_configuration_and_document_type_contracts() -> None:
    po_document = _document(_po_xml("PO-100"), DocType.PO)
    invoice_document = _document(_invoice_xml("INV-10"), DocType.INVOICE)

    with pytest.raises(ValueError, match="auto-accept threshold"):
        MappingConfig(link_auto_accept="1.01")
    with pytest.raises(ValueError, match="content threshold"):
        MappingConfig(step3_min_score="0.91")
    with pytest.raises(ValueError, match="content lead threshold"):
        MappingConfig(step3_min_lead="1.01")
    with pytest.raises(ValueError, match="must be invoices or GRNs"):
        MappingDocument(document_id=_id(60), document=po_document)
    with pytest.raises(ValueError, match="must contain a PO"):
        MappingPO(document_id=_id(61), document=invoice_document)


def test_po_reference_resolution_handles_unanchored_prefix_tail_and_ambiguity() -> None:
    po = _po(60, _document(_po_xml("PO-AB12345"), DocType.PO))
    duplicate_no = _po(61, _document(_po_xml("PO-AB12345"), DocType.PO))
    prefix_variant = _po(62, _document(_po_xml("ORDER-AB12345"), DocType.PO))
    tail_variant = _po(63, _document(_po_xml("PO-ZZ12345"), DocType.PO))
    invoice = _source(64, _document(_invoice_xml("INV-10"), DocType.INVOICE))
    no_vendor = invoice.model_copy(update={"vendor_id": None})

    assert links_module._po_matches("PO-AB12345", (po,), None) == ((), True)
    assert links_module._po_matches("PO-AB12345", (po, duplicate_no), None) == ((), True)
    assert links_module._po_matches("MISSING-100", (po,), None) == ((), False)
    assert links_module._po_matches("ORDER-AB12345", (po,), _VENDOR) == ((po,), False)
    assert links_module._po_matches("PUR-AB12345", (po, prefix_variant), _VENDOR) == ((), False)
    assert links_module._po_matches("PO-XY12345", (tail_variant,), _VENDOR) == (
        (tail_variant,),
        False,
    )
    assert links_module._po_matches("PO-1234", (po,), _VENDOR) == ((), False)
    assert links_module._unique_header_po_refs(invoice.document) == ()
    assert no_vendor.vendor_id is None

    unassigned_with_reference = _source(
        65,
        _document(_invoice_xml("INV-65", po_ref="PO-AB12345"), DocType.INVOICE),
    ).model_copy(update={"vendor_id": None})
    resolution = resolve_links((unassigned_with_reference,), (po,))
    assert len(resolution) == 1
    assert not resolution[0].links
    assert resolution[0].reason_code is ReasonCode.VENDOR_MISMATCH


def test_mapping_source_text_prefers_supplied_text_then_raw_then_canonical_fields() -> None:
    invoice_document = _document(_invoice_xml("INV-70"), DocType.INVOICE)
    supplied = _source(70, invoice_document, source_text="operator context")
    raw_document = invoice_document.model_copy(update={"raw_text": "stored raw text"})
    raw = _source(71, raw_document)

    assert links_module._flatten({"a": [1, None], "b": {"nested": "value"}}) == [
        "a",
        "1",
        "b",
        "nested",
        "value",
    ]
    assert links_module._source_text(supplied) == "operator context"
    assert links_module._source_text(raw) == "stored raw text"
    assert "INV-70" in links_module._source_text(_source(72, invoice_document))


def test_reverse_and_manual_resolution_reject_cross_vendor_targets() -> None:
    other_vendor = UUID(int=900)
    po = _po(80, _document(_po_xml("PO-100"), DocType.PO), vendor_id=other_vendor)
    narration = _source(
        81,
        _document(_invoice_xml("INV-81"), DocType.INVOICE),
        source_text="Purchase Order PO-100 governs these items",
    )
    manual = _source(
        82,
        _document(_invoice_xml("INV-82"), DocType.INVOICE),
        manual_po_ids=(po.document_id, _id(999)),
    )
    unassigned_manual = _source(
        83,
        _document(_invoice_xml("INV-83"), DocType.INVOICE),
        manual_po_ids=(po.document_id,),
    ).model_copy(update={"vendor_id": None})

    reverse_result, manual_result, unassigned_result = resolve_links(
        (narration, manual, unassigned_manual), (po,)
    )

    assert reverse_result.reason_code is ReasonCode.VENDOR_MISMATCH
    assert manual_result.reason_code is ReasonCode.VENDOR_MISMATCH
    assert unassigned_result.reason_code is ReasonCode.VENDOR_MISMATCH
    assert not unassigned_result.links
    assert not reverse_result.links
    assert not manual_result.links


def test_invoice_and_grn_can_match_by_supplier_invoice_number_or_challan() -> None:
    invoice = _source(83, _document(_invoice_xml("INV-83"), DocType.INVOICE))
    by_supplier_invoice = _source(
        84,
        _document(
            "<GRN><GRNNo>GRN-84</GRNNo><SupplierInvoiceNo>INV-83</SupplierInvoiceNo>"
            "<Supplier>Acme Supplies</Supplier></GRN>",
            DocType.GRN,
        ),
    )
    by_challan_invoice = _source(
        85,
        _document(
            _invoice_xml("INV-85").replace(
                "<Supplier>Acme Supplies</Supplier>",
                "<Supplier>Acme Supplies</Supplier><ChallanNo>DC-85</ChallanNo>",
            ),
            DocType.INVOICE,
        ),
    )
    by_challan_grn = _source(
        86,
        _document("<GRN><GRNNo>GRN-86</GRNNo><ChallanNo>DC-85</ChallanNo></GRN>", DocType.GRN),
    )

    assert links_module._grn_matches_invoice(invoice, by_supplier_invoice)
    assert links_module._grn_matches_invoice(by_challan_invoice, by_challan_grn)
    assert not links_module._grn_matches_invoice(by_challan_invoice, invoice)
    assert not links_module._grn_matches_invoice(
        invoice,
        by_supplier_invoice.model_copy(update={"vendor_id": _id(999)}),
    )
    assert not links_module._grn_matches_invoice(
        invoice,
        by_supplier_invoice.model_copy(update={"vendor_id": None}),
    )


def test_content_score_handles_zero_value_and_missing_optional_signals() -> None:
    invoice_document = _document(
        "<Invoice><InvoiceNo>INV-ZERO</InvoiceNo><Supplier>Acme Supplies</Supplier>"
        "<Line><Description>Widget model A</Description></Line></Invoice>",
        DocType.INVOICE,
    )
    po_document = _document(
        "<PurchaseOrder><OrderNo>PO-ZERO</OrderNo><Supplier>Acme Supplies</Supplier>"
        "<Line><Description>Widget model A</Description></Line></PurchaseOrder>",
        DocType.PO,
    )
    source = _source(87, invoice_document)
    po = _po(88, po_document)

    score, aligned = links_module._content_score(
        source,
        po,
        {1: Decimal("0")},
        {1: Decimal("0")},
        MappingConfig(),
    )

    assert score == Decimal("0.4000")
    assert aligned == ((1, 1, Decimal("1.0000")),)

    empty_source = _source(
        89,
        _document("<Invoice><InvoiceNo>INV-EMPTY</InvoiceNo></Invoice>", DocType.INVOICE),
    )
    empty_po = _po(
        90,
        _document("<PurchaseOrder><OrderNo>PO-EMPTY</OrderNo></PurchaseOrder>", DocType.PO),
    )
    no_value_score, no_value_alignment = links_module._content_score(
        empty_source,
        empty_po,
        {},
        {},
        MappingConfig(),
    )

    assert no_value_score == Decimal("0.0000")
    assert no_value_alignment == ()


def test_reservation_skips_missing_purchase_order_and_non_invoice_sources() -> None:
    invoice = _source(91, _document(_invoice_xml("INV-91"), DocType.INVOICE))
    grn = _source(92, _document(_grn_xml("GRN-92", "PO-100"), DocType.GRN))
    po = _po(93, _document(_po_xml("PO-100"), DocType.PO))
    remaining_qty = {po.document_id: {1: Decimal("10")}}
    remaining_balance = {po.document_id: Decimal("100")}

    links_module._reserve_invoice_lines(
        invoice,
        _id(999),
        {po.document_id: po},
        remaining_qty,
        remaining_balance,
        MappingConfig(),
    )
    links_module._reserve_invoice_lines(
        grn,
        po.document_id,
        {po.document_id: po},
        remaining_qty,
        remaining_balance,
        MappingConfig(),
    )

    assert remaining_qty[po.document_id] == {1: Decimal("10")}
    assert remaining_balance[po.document_id] == Decimal("100")


def _split(document: Document, state: str = "unconfirmed") -> Document:
    marker = {"pages": [1], "group": 1, "groups": 2, "state": state}
    header = document.header.model_copy(
        update={"extras": {**document.header.extras, "page_split": marker}}
    )
    return document.model_copy(update={"header": header})


def test_unconfirmed_page_split_keeps_every_link_a_suggestion() -> None:
    invoice_doc = _document(_invoice_xml("INV-10", po_ref="PO-100"), DocType.INVOICE)
    po_doc = _document(_po_xml("PO-100"), DocType.PO)

    split_source = resolve_links([_source(10, _split(invoice_doc))], [_po(20, po_doc)])[0]
    split_target = resolve_links([_source(10, invoice_doc)], [_po(20, _split(po_doc))])[0]
    accepted = resolve_links([_source(10, _split(invoice_doc, "confirmed"))], [_po(20, po_doc)])[0]

    assert split_source.links[0].state is LinkState.SUGGESTED
    assert "file split not confirmed" in split_source.links[0].evidence
    assert split_target.links[0].state is LinkState.SUGGESTED
    assert accepted.links[0].state is LinkState.AUTO


def test_unconfirmed_split_grn_keeps_the_via_grn_link_a_suggestion() -> None:
    grn = _source(30, _split(_document(_grn_xml("GRN-1", "PO-100"), DocType.GRN)))
    invoice = _source(10, _document(_invoice_xml("INV-10", grn_ref="GRN-1"), DocType.INVOICE))
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    results = {item.document_id: item for item in resolve_links([grn, invoice], [po])}

    assert results[_id(30)].links[0].state is LinkState.SUGGESTED
    assert results[_id(10)].links
    assert all(link.state is LinkState.SUGGESTED for link in results[_id(10)].links)
