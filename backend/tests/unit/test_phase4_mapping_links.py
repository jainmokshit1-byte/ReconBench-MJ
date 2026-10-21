"""The PO anchor ladder is deterministic and conservative at every step."""

from __future__ import annotations

from datetime import date
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
    DocLine,
    DocType,
    Document,
    FieldEvidence,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
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
        open_grn_qty_by_line={1: Decimal("100")},
    )


def _verify_header_reference(source: MappingDocument, field_name: str) -> MappingDocument:
    header = source.document.header
    field = getattr(header, field_name)
    assert field is not None
    verified = field.model_copy(
        update={
            "origin": FieldOrigin.MANUAL,
            "confidence": None,
            "verification": FieldVerification.USER,
            "verified_by": "synthetic-operator",
        }
    )
    document = source.document.model_copy(
        update={"header": header.model_copy(update={field_name: verified})}
    )
    return source.model_copy(update={"document": document})


def _verify_line_reference(source: MappingDocument, line_no: int) -> MappingDocument:
    lines = tuple(
        line.model_copy(
            update={
                "po_ref": line.po_ref.model_copy(
                    update={
                        "origin": FieldOrigin.MANUAL,
                        "confidence": None,
                        "verification": FieldVerification.USER,
                        "verified_by": "synthetic-operator",
                    }
                )
            }
        )
        if line.line_no == line_no and line.po_ref is not None
        else line
        for line in source.document.lines
    )
    return source.model_copy(
        update={"document": source.document.model_copy(update={"lines": lines})}
    )


def _with_header_reference_provenance(
    source: MappingDocument,
    field_name: str,
    *,
    origin: FieldOrigin = FieldOrigin.OCR,
    confidence: Decimal | None = Decimal("0.99"),
) -> MappingDocument:
    header = source.document.header
    field = getattr(header, field_name)
    assert field is not None
    document = source.document.model_copy(
        update={
            "header": header.model_copy(
                update={
                    field_name: field.model_copy(
                        update={
                            "origin": origin,
                            "confidence": confidence,
                            "verification": FieldVerification.NONE,
                        }
                    )
                }
            )
        }
    )
    return source.model_copy(update={"document": document})


def _with_marketplace_order(source: MappingDocument, order_no: str) -> MappingDocument:
    header = source.document.header
    extras = {**header.extras, "marketplace_order_no": order_no}
    document = source.document.model_copy(
        update={"header": header.model_copy(update={"extras": extras})}
    )
    return source.model_copy(update={"document": document})


def _coded_invoice_xml(
    number: str,
    *,
    date: str = "20260201",
    qty: str = "100",
    rate: str = "10",
    amount: str = "1000",
    po_ref: str | None = None,
) -> str:
    return _invoice_xml(
        number,
        po_ref=po_ref,
        date=date,
        qty=qty,
        rate=rate,
        amount=amount,
        description="Part MOD-100",
    ).replace(
        "<Line><Description>",
        "<Line><ItemCode>MOD-100</ItemCode><Description>",
    )


def _coded_po_xml(number: str, *, date: str = "20260101") -> str:
    return (
        _item_po_xml(number, "Part MOD-100")
        .replace(
            "<Line><Description>",
            "<Line><ItemCode>MOD-100</ItemCode><Description>",
        )
        .replace("<OrderDate>20260101</OrderDate>", f"<OrderDate>{date}</OrderDate>")
    )


def test_step_zero_labeled_reference_creates_auto_link() -> None:
    invoice = _verify_header_reference(
        _source(10, _document(_invoice_xml("INV-10", po_ref="PO-100"), DocType.INVOICE)),
        "po_refs",
    )
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
    assert result.links[0].state is LinkState.SUGGESTED


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
    invoice = _verify_header_reference(
        _source(
            10,
            _document(
                _invoice_xml(
                    "INV-10",
                    grn_ref="GRN-1",
                    qty="10",
                    rate="10",
                    amount="100",
                    description="Part MOD-100",
                ),
                DocType.INVOICE,
            ),
        ),
        "grn_refs",
    )
    grn = _verify_header_reference(
        _source(
            11,
            _document(
                "<GRN><GRNNo>GRN-1</GRNNo><GRNDate>20260115</GRNDate>"
                "<Supplier>Acme Supplies</Supplier><PORef>PO-100</PORef>"
                "<Line><Description>Part MOD-100</Description><Qty>10</Qty>"
                "<Rate>10</Rate><Amount>100</Amount></Line></GRN>",
                DocType.GRN,
            ),
        ),
        "po_refs",
    )
    po = _po(20, _document(_item_po_xml("PO-100", "Part MOD-100"), DocType.PO))

    results = resolve_links([invoice, grn], [po])
    invoice_result = next(result for result in results if result.document_id == invoice.document_id)
    grn_result = next(result for result in results if result.document_id == grn.document_id)

    assert grn_result.links[0].method is LinkMethod.LABELED
    invoice_grn_link = next(
        link for link in invoice_result.links if link.link_type.value == "inv_grn"
    )
    invoice_po_link = next(
        link for link in invoice_result.links if link.link_type.value == "inv_po"
    )
    assert invoice_grn_link.method is LinkMethod.VIA_GRN
    assert invoice_grn_link.to_document_id == grn.document_id
    assert invoice_grn_link.state is LinkState.AUTO
    assert invoice_po_link.method is LinkMethod.VIA_GRN
    assert invoice_po_link.via_document_id == grn.document_id
    assert invoice_po_link.state is LinkState.AUTO


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
    assert result.links
    assert all(link.state is LinkState.SUGGESTED for link in result.links)


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
    invoice = _verify_header_reference(
        _source(10, _document(_invoice_xml("INV-10", po_ref="PO-404"), DocType.INVOICE)),
        "po_refs",
    )
    similar_po = _po(20, _document(_po_xml("PO-100"), DocType.PO))

    result = resolve_links([invoice], [similar_po])[0]

    assert not result.links
    assert result.reason_code is ReasonCode.NO_PO_LINK
    assert result.reason_detail == "PO_NOT_IN_POOL"
    assert result.reference_text == "PO-404"


def test_scn_10_prd_po_number_variants_link_to_the_same_purchase_order() -> None:
    invoice = _verify_header_reference(
        _source(
            10,
            _document(_invoice_xml("INV-10", po_ref="PO/26-27/0142"), DocType.INVOICE),
        ),
        "po_refs",
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
    invoice = _verify_line_reference(invoice, 1)
    po_a = _po(20, _document(_item_po_xml("PO-100", "Widget model A"), DocType.PO))
    po_b = _po(21, _document(_item_po_xml("PO-200", "Dock model B"), DocType.PO))

    result = resolve_links([invoice], [po_a, po_b])[0]

    assert [(link.line_no, link.to_document_id, link.method) for link in result.links] == [
        (1, po_a.document_id, LinkMethod.LABELED),
        (2, po_b.document_id, LinkMethod.CONTENT),
    ]


def test_multi_po_reference_outside_pool_stops_content_fallback() -> None:
    invoice = _verify_header_reference(
        _source(10, _document(_multi_line_invoice_xml(), DocType.INVOICE)), "po_refs"
    )
    only_po = _po(20, _document(_item_po_xml("PO-100", "Widget model A"), DocType.PO))

    result = resolve_links([invoice], [only_po])[0]

    assert not result.links
    assert result.reason_code is ReasonCode.NO_PO_LINK
    assert result.reason_detail == "PO_NOT_IN_POOL"
    assert result.reference_text == "PO-200"


def test_link_confidence_boundary_at_point_ninety_five() -> None:
    invoice = _verify_header_reference(
        _source(10, _document(_invoice_xml("INV-10", po_ref="PO-100"), DocType.INVOICE)),
        "po_refs",
    )
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

    result = resolve_links(
        [invoice],
        [po],
        MappingConfig(step3_min_score=Decimal("0.40"), step3_min_lead=Decimal("0")),
    )[0]

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


def test_period_capacity_keeps_an_overbilling_candidate_with_failed_open_quantity() -> None:
    po = _po(20, _document(_po_xml("PO-100"), DocType.PO))
    po = po.model_copy(
        update={
            "open_balance": Decimal("10"),
            "open_qty_by_line": {1: Decimal("1")},
        }
    )
    later = _source(12, _document(_invoice_xml("INV-12"), DocType.INVOICE), natural_key="z")
    earlier = _source(11, _document(_invoice_xml("INV-11"), DocType.INVOICE), natural_key="a")

    results = resolve_links(
        [later, earlier],
        [po],
        MappingConfig(step3_min_score=Decimal("0.40"), step3_min_lead=Decimal("0")),
    )

    first, second = results
    assert first.document_id == earlier.document_id
    assert first.links[0].method is LinkMethod.CONTENT
    assert second.document_id == later.document_id
    assert second.links[0].method is LinkMethod.CONTENT
    assert "OPEN_PO_QUANTITY_EXCEEDED" in second.links[0].evidence


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
    invoice_no_challan_invoice = _source(87, _document(_invoice_xml("INV-87"), DocType.INVOICE))
    invoice_no_challan_grn = _source(
        88,
        _document("<GRN><GRNNo>GRN-88</GRNNo><ChallanNo>INV-87</ChallanNo></GRN>", DocType.GRN),
    )

    assert links_module._grn_matches_invoice(invoice, by_supplier_invoice)
    assert links_module._grn_matches_invoice(by_challan_invoice, by_challan_grn)
    assert links_module._grn_matches_invoice(invoice_no_challan_invoice, invoice_no_challan_grn)
    assert links_module._verified_invoice_grn_anchor(
        _verify_header_reference(invoice_no_challan_invoice, "doc_no"),
        _verify_header_reference(invoice_no_challan_grn, "challan_no"),
    )
    assert not links_module._grn_matches_invoice(by_challan_invoice, invoice)
    assert not links_module._grn_matches_invoice(
        invoice,
        by_supplier_invoice.model_copy(update={"vendor_id": _id(999)}),
    )
    assert not links_module._grn_matches_invoice(
        invoice,
        by_supplier_invoice.model_copy(update={"vendor_id": None}),
    )


def test_direct_invoice_grn_identifier_anchor_suggests_without_amount_or_part_match() -> None:
    invoice = _source(
        151,
        _document(_invoice_xml("INV-DIRECT-151"), DocType.INVOICE),
    )
    grn = _verify_header_reference(
        _source(
            152,
            _document(
                "<GRN><GRNNo>GRN-DIRECT-152</GRNNo>"
                "<SupplierInvoiceNo>INV-DIRECT-151</SupplierInvoiceNo>"
                "<Supplier>Acme Supplies</Supplier><PORef>PO-DIRECT-153</PORef></GRN>",
                DocType.GRN,
            ),
        ),
        "po_refs",
    )
    po = _po(153, _document(_po_xml("PO-DIRECT-153"), DocType.PO))

    invoice_result = next(
        result
        for result in resolve_links((invoice, grn), (po,))
        if result.document_id == invoice.document_id
    )

    links_by_type = {link.link_type.value: link for link in invoice_result.links}
    assert links_by_type["inv_grn"].to_document_id == grn.document_id
    assert links_by_type["inv_po"].to_document_id == po.document_id
    assert links_by_type["inv_grn"].state is LinkState.SUGGESTED
    assert links_by_type["inv_po"].state is LinkState.SUGGESTED
    assert "anchor=supplier_invoice_no" in links_by_type["inv_grn"].evidence
    assert "independent_corroboration=none" in links_by_type["inv_grn"].evidence

    grn_without_po = _source(
        158,
        _document(
            "<GRN><GRNNo>GRN-UNLINKED-158</GRNNo>"
            "<SupplierInvoiceNo>INV-DIRECT-151</SupplierInvoiceNo>"
            "<Supplier>Acme Supplies</Supplier></GRN>",
            DocType.GRN,
        ),
    )
    unlinked_grn_result = next(
        result
        for result in resolve_links((invoice, grn_without_po), (po,))
        if result.document_id == invoice.document_id
    )
    inv_grn_edge = next(
        link for link in unlinked_grn_result.links if link.link_type.value == "inv_grn"
    )
    assert inv_grn_edge.to_document_id == grn_without_po.document_id
    assert not any(
        link.link_type.value == "inv_po" and link.method is LinkMethod.VIA_GRN
        for link in unlinked_grn_result.links
    )
    assert any(
        link.link_type.value == "inv_po" and link.method is LinkMethod.CONTENT
        for link in unlinked_grn_result.links
    )


def test_verified_direct_invoice_grn_anchor_auto_links_but_grn_ref_alone_does_not() -> None:
    verified_invoice = _verify_header_reference(
        _source(
            154,
            _document(_invoice_xml("INV-DIRECT-154"), DocType.INVOICE),
        ),
        "doc_no",
    )
    verified_grn = _verify_header_reference(
        _source(
            155,
            _document(
                "<GRN><GRNNo>GRN-DIRECT-155</GRNNo>"
                "<SupplierInvoiceNo>INV-DIRECT-154</SupplierInvoiceNo>"
                "<Supplier>Acme Supplies</Supplier><PORef>PO-DIRECT-156</PORef></GRN>",
                DocType.GRN,
            ),
        ),
        "supplier_invoice_no",
    )
    verified_grn = _verify_header_reference(verified_grn, "po_refs")
    po = _po(156, _document(_po_xml("PO-DIRECT-156"), DocType.PO))
    direct_result = next(
        result
        for result in resolve_links((verified_invoice, verified_grn), (po,))
        if result.document_id == verified_invoice.document_id
    )

    handwritten_grn_reference = _verify_header_reference(
        _source(
            157,
            _document(_invoice_xml("INV-GRN-REF-157", grn_ref="GRN-DIRECT-155"), DocType.INVOICE),
        ),
        "grn_refs",
    )
    reference_only_result = next(
        result
        for result in resolve_links((handwritten_grn_reference, verified_grn), (po,))
        if result.document_id == handwritten_grn_reference.document_id
    )

    assert {link.state for link in direct_result.links} == {LinkState.AUTO}
    assert {link.link_type.value for link in direct_result.links} == {"inv_grn", "inv_po"}
    assert all(link.link_type.value != "inv_grn" for link in reference_only_result.links)
    assert all(link.method is not LinkMethod.VIA_GRN for link in reference_only_result.links)


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

    assert score == Decimal("0.8000")
    assert aligned == ((1, 1, Decimal("0.8000")),)

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
    invoice_doc = _verify_header_reference(
        _source(
            10,
            _document(_invoice_xml("INV-10", po_ref="PO-100"), DocType.INVOICE),
        ),
        "po_refs",
    ).document
    po_doc = _document(_po_xml("PO-100"), DocType.PO)

    split_source = resolve_links([_source(10, _split(invoice_doc))], [_po(20, po_doc)])[0]
    split_target = resolve_links([_source(10, invoice_doc)], [_po(20, _split(po_doc))])[0]
    accepted = resolve_links([_source(10, _split(invoice_doc, "confirmed"))], [_po(20, po_doc)])[0]

    assert split_source.links[0].state is LinkState.SUGGESTED
    assert "file split not confirmed" in split_source.links[0].evidence
    assert split_target.links[0].state is LinkState.SUGGESTED
    assert accepted.links[0].state is LinkState.AUTO


def test_unconfirmed_split_grn_keeps_the_via_grn_link_a_suggestion() -> None:
    grn = _verify_header_reference(
        _source(
            30,
            _split(
                _document(
                    "<GRN><GRNNo>GRN-1</GRNNo><Supplier>Acme Supplies</Supplier>"
                    "<PORef>PO-100</PORef><Line><Description>Part MOD-100</Description>"
                    "<Qty>10</Qty><Rate>10</Rate><Amount>100</Amount></Line></GRN>",
                    DocType.GRN,
                )
            ),
        ),
        "po_refs",
    )
    invoice = _verify_header_reference(
        _source(
            10,
            _document(
                _invoice_xml(
                    "INV-10",
                    grn_ref="GRN-1",
                    qty="10",
                    rate="10",
                    amount="100",
                    description="Part MOD-100",
                ),
                DocType.INVOICE,
            ),
        ),
        "grn_refs",
    )
    po = _po(20, _document(_item_po_xml("PO-100", "Part MOD-100"), DocType.PO))

    results = {item.document_id: item for item in resolve_links([grn, invoice], [po])}

    assert results[_id(30)].links[0].state is LinkState.SUGGESTED
    assert results[_id(10)].links
    assert all(link.state is LinkState.SUGGESTED for link in results[_id(10)].links)


def test_unverified_ocr_po_reference_is_only_a_suggestion_without_corroboration() -> None:
    invoice = _with_header_reference_provenance(
        _source(
            101,
            _document(_invoice_xml("INV-OCR", po_ref="PO-100"), DocType.INVOICE),
        ),
        "po_refs",
        origin=FieldOrigin.OCR,
        confidence=Decimal("0.99"),
    )
    po = _po(102, _document(_po_xml("PO-100"), DocType.PO))

    result = resolve_links([invoice], [po])[0]

    assert result.links
    assert all(link.state is LinkState.SUGGESTED for link in result.links)
    assert not result.field_verifications


def test_system_check_requires_confidence_and_an_independent_signal() -> None:
    coded_invoice = _source(
        103,
        _document(_coded_invoice_xml("INV-CHECK", po_ref="PO-CHECK"), DocType.INVOICE),
    )
    po = _po(104, _document(_coded_po_xml("PO-CHECK"), DocType.PO))
    high_confidence = _with_header_reference_provenance(
        coded_invoice,
        "po_refs",
        origin=FieldOrigin.OCR,
        confidence=Decimal("0.90"),
    )
    low_confidence = _with_header_reference_provenance(
        coded_invoice.model_copy(update={"document_id": _id(105)}),
        "po_refs",
        origin=FieldOrigin.OCR,
        confidence=Decimal("0.84"),
    )

    verified, suggested = resolve_links([high_confidence, low_confidence], [po])

    assert verified.links[0].state is LinkState.AUTO
    assert len(verified.field_verifications) == 1
    assert verified.field_verifications[0].verification is FieldVerification.SYSTEM_CHECK
    assert suggested.links
    assert all(link.state is LinkState.SUGGESTED for link in suggested.links)
    assert not suggested.field_verifications

    system_checked_header = high_confidence.document.header.model_copy(
        update={
            "po_refs": high_confidence.document.header.po_refs.model_copy(
                update={"verification": FieldVerification.SYSTEM_CHECK}
            )
        }
    )
    system_checked = high_confidence.model_copy(
        update={
            "document": high_confidence.document.model_copy(
                update={"header": system_checked_header}
            )
        }
    )
    fully_billed = po.model_copy(
        update={"open_balance": Decimal("0"), "open_qty_by_line": {1: Decimal("0")}}
    )
    duplicate = resolve_links([system_checked], [fully_billed])[0]

    assert duplicate.links[0].state is LinkState.AUTO
    assert not duplicate.field_verifications


def test_system_check_does_not_verify_line_reference_to_fully_billed_po() -> None:
    invoice_document = _document(_coded_invoice_xml("INV-LINE-REF"), DocType.INVOICE)
    line_reference = FieldValue[str](
        value="PO-LINE-CHECK",
        confidence=Decimal("0.99"),
        source=FieldSource.RULE,
        evidence=FieldEvidence(),
        origin=FieldOrigin.OCR,
    )
    invoice_line = invoice_document.lines[0].model_copy(update={"po_ref": line_reference})
    invoice_document = invoice_document.model_copy(update={"lines": (invoice_line,)})
    invoice = _source(140, invoice_document)
    po = _po(141, _document(_coded_po_xml("PO-LINE-CHECK"), DocType.PO)).model_copy(
        update={"open_balance": Decimal("0"), "open_qty_by_line": {1: Decimal("0")}}
    )

    result = resolve_links([invoice], [po])[0]

    assert result.links
    assert all(link.state is LinkState.SUGGESTED for link in result.links)
    assert "OPEN_PO_QUANTITY_EXCEEDED" in result.links[0].evidence
    assert not result.field_verifications


def test_ocr_grn_reference_verification_uses_link_not_open_grn_quantity() -> None:
    invoice_document = _document(
        _invoice_xml(
            "INV-GRN-VERIFY",
            grn_ref="GRN-VERIFY",
            date="20260110",
            qty="10",
            rate="10",
            amount="100",
            description="Part MOD-100",
        ),
        DocType.INVOICE,
    )
    invoice = _with_header_reference_provenance(
        _source(130, invoice_document),
        "grn_refs",
        origin=FieldOrigin.OCR,
        confidence=Decimal("0.90"),
    )
    grn = _verify_header_reference(
        _source(
            131,
            _document(
                "<GRN><GRNNo>GRN-VERIFY</GRNNo><GRNDate>20260115</GRNDate>"
                "<Supplier>Acme Supplies</Supplier><PORef>PO-VERIFY</PORef>"
                "<Line><ItemCode>MOD-100</ItemCode><Description>Part MOD-100</Description>"
                "<Qty>10</Qty></Line></GRN>",
                DocType.GRN,
            ),
        ),
        "po_refs",
    )
    po = _po(132, _document(_coded_po_xml("PO-VERIFY"), DocType.PO)).model_copy(
        update={
            "grn_support_qty_by_line": {1: Decimal("10")},
            "open_grn_qty_by_line": {1: Decimal("10")},
        }
    )

    opened = resolve_links([invoice, grn], [po])[0]
    closed_po = po.model_copy(update={"open_grn_qty_by_line": {1: Decimal("0")}})
    closed = resolve_links([invoice, grn], [closed_po])[0]

    assert opened.field_verifications[0].verification is FieldVerification.SYSTEM_CHECK
    assert opened.field_verifications[0].matched_document_id == grn.document_id
    assert all(link.state is LinkState.AUTO for link in opened.links)
    assert closed.field_verifications[0].verification is FieldVerification.SYSTEM_CHECK
    assert closed.field_verifications[0].matched_document_id == grn.document_id
    assert closed.links
    assert all(link.state is LinkState.AUTO for link in closed.links)

    stale_header = invoice.document.header.model_copy(
        update={
            "grn_refs": invoice.document.header.grn_refs.model_copy(
                update={"verification": FieldVerification.SYSTEM_CHECK}
            )
        }
    )
    stale_invoice = invoice.model_copy(
        update={"document": invoice.document.model_copy(update={"header": stale_header})}
    )
    reopened = resolve_links([stale_invoice, grn], [closed_po])[0]

    assert not reopened.field_verifications
    assert all(link.state is LinkState.AUTO for link in reopened.links)


def test_handwritten_annotation_reference_needs_amount_corroboration() -> None:
    matching_document = _document(
        _coded_invoice_xml("INV-ANNOTATION", qty="100", rate="10", amount="1000"),
        DocType.INVOICE,
    )
    matching_header = matching_document.header.model_copy(
        update={"extras": {**matching_document.header.extras, "annotation_refs": ["PO-100"]}}
    )
    matching_source = _source(
        106,
        matching_document.model_copy(update={"header": matching_header}),
    )
    wrong_amount_document = _document(
        _coded_invoice_xml("INV-ANNOTATION-WRONG", qty="90", rate="10", amount="900"),
        DocType.INVOICE,
    )
    wrong_amount_header = wrong_amount_document.header.model_copy(
        update={"extras": {**wrong_amount_document.header.extras, "annotation_refs": ["PO-100"]}}
    )
    wrong_amount_source = _source(
        107,
        wrong_amount_document.model_copy(update={"header": wrong_amount_header}),
    )
    control_source = _source(111, wrong_amount_document)
    po = _po(108, _document(_coded_po_xml("PO-100"), DocType.PO))

    corroborated = resolve_links([matching_source], [po])[0]
    uncorroborated = resolve_links([wrong_amount_source], [po])[0]
    control = resolve_links([control_source], [po])[0]

    assert corroborated.links[0].state is LinkState.SUGGESTED
    assert corroborated.links[0].method is LinkMethod.CONTENT
    assert uncorroborated.links[0].state is LinkState.SUGGESTED
    assert uncorroborated.candidates[0].signals["reference"] == Decimal("0")
    assert uncorroborated.candidates[0].score == control.candidates[0].score
    assert any(
        "handwritten reference requires" in item
        for item in uncorroborated.candidates[0].missing_signals
    )


def test_part_score_uses_only_model_identity_or_lower_credit_description() -> None:
    def field(value: str) -> FieldValue[str]:
        return FieldValue(
            value=value,
            source=FieldSource.RULE,
            evidence=FieldEvidence(),
            origin=FieldOrigin.OCR,
        )

    invoice_line = DocLine(
        line_no=1,
        description=field("Precision widget assembly"),
        hsn_sac=field("123456"),
        uom=field("NOS"),
    )
    po_line = DocLine(
        line_no=1,
        description=field("Precision widget assembly"),
        hsn_sac=field("999999"),
        uom=field("KG"),
    )
    changed_supporting_codes = po_line.model_copy(
        update={"hsn_sac": field("123456"), "uom": field("NOS")}
    )

    description_fallback = links_module._part_identity(invoice_line, po_line, MappingConfig())
    with_supporting_codes = links_module._part_identity(
        invoice_line, changed_supporting_codes, MappingConfig()
    )
    conflicting_model = links_module._part_identity(
        invoice_line.model_copy(update={"item_code": field("MOD-100")}),
        po_line.model_copy(
            update={"item_code": field("MOD-200"), "description": field("Part MOD-100")}
        ),
        MappingConfig(),
    )

    assert description_fallback == with_supporting_codes
    assert description_fallback < Decimal("1")
    assert conflicting_model == Decimal("0")


def test_marketplace_reference_requires_a_complete_token_match() -> None:
    invoice = _with_marketplace_order(
        _source(141, _document(_coded_invoice_xml("INV-MKT"), DocType.INVOICE)), "MKT-42"
    )
    matching_po = _po(
        142,
        _document(_coded_po_xml("PO-MKT"), DocType.PO).model_copy(
            update={"raw_text": "Order notes mention MKT 42"}
        ),
    )
    prefix_only_po = _po(
        143,
        _document(_coded_po_xml("PO-MKT-PREFIX"), DocType.PO).model_copy(
            update={"raw_text": "Order notes mention MKT-420"}
        ),
    )
    joined_suffix_po = _po(
        147,
        _document(_coded_po_xml("PO-MKT-SUFFIX"), DocType.PO).model_copy(
            update={"raw_text": "Order notes mention MKT-42-ABC"}
        ),
    )

    assert links_module._reference_score(invoice, matching_po) == Decimal("1")
    assert links_module._reference_score(invoice, prefix_only_po) == Decimal("0")
    assert links_module._reference_score(invoice, joined_suffix_po) == Decimal("0")


def test_amount_score_uses_accepted_quantity_and_decomposes_variance_exactly() -> None:
    at_band_invoice = _source(
        109,
        _document(
            _coded_invoice_xml("INV-AMOUNT", qty="101", rate="10", amount="1010"), DocType.INVOICE
        ),
    )
    po = _po(110, _document(_coded_po_xml("PO-AMOUNT"), DocType.PO))
    line = at_band_invoice.document.lines[0]
    part = links_module._part_identity(line, po.document.lines[0], MappingConfig())
    score, signals, _ = links_module._line_candidate_breakdown(
        at_band_invoice, po, 1, 1, part, MappingConfig()
    )
    evidence = links_module._candidate_evidence(
        at_band_invoice, po, score, signals, (), (), MappingConfig()
    )

    assert signals["expected_grn_value"] == Decimal("1000")
    assert signals["amount_delta"] == Decimal("10")
    assert signals["quantity_variance_value"] == Decimal("10")
    assert signals["price_variance_value"] == Decimal("0")
    assert signals["amount"] == Decimal("1.0000")
    assert "amount_check=pass" in evidence
    assert "quantity_check=fail" in evidence
    assert "variance_decomposition=pass" in evidence

    outside_band = _source(
        111,
        _document(
            _coded_invoice_xml("INV-AMOUNT-OUT", qty="101", rate="13", amount="1313"),
            DocType.INVOICE,
        ),
    )
    outside_line = outside_band.document.lines[0]
    outside_part = links_module._part_identity(outside_line, po.document.lines[0], MappingConfig())
    outside_score, outside_signals, _ = links_module._line_candidate_breakdown(
        outside_band, po, 1, 1, outside_part, MappingConfig()
    )
    outside_evidence = links_module._candidate_evidence(
        outside_band, po, outside_score, outside_signals, (), (), MappingConfig()
    )

    assert outside_signals["amount_delta"] == Decimal("313")
    assert outside_signals["quantity_variance_value"] == Decimal("10")
    assert outside_signals["price_variance_value"] == Decimal("303")
    assert (
        outside_signals["quantity_variance_value"] + outside_signals["price_variance_value"]
        == outside_signals["amount_delta"]
    )
    assert outside_signals["amount"] < Decimal("1")
    assert "amount_check=fail" in outside_evidence
    assert "price_check=fail" in outside_evidence


def test_large_amount_variance_does_not_filter_an_identity_suggestion() -> None:
    invoice = _source(
        148,
        _document(
            _coded_invoice_xml(
                "INV-VARIANCE-900",
                qty="10",
                rate="100",
                amount="1000",
            ),
            DocType.INVOICE,
        ),
    )
    po = _po(149, _document(_coded_po_xml("PO-VARIANCE-100"), DocType.PO)).model_copy(
        update={
            "open_balance": Decimal("10000"),
            "open_qty_by_line": {1: Decimal("100")},
            "grn_support_qty_by_line": {1: Decimal("0")},
        }
    )

    result = resolve_links([invoice], [po])[0]

    assert len(result.links) == 1
    assert result.links[0].to_document_id == po.document_id
    assert result.links[0].state is LinkState.SUGGESTED
    assert "amount_check=fail:invoice_value_above_grn_value" in result.links[0].evidence
    assert "quantity_check=fail:invoice_quantity_above_grn_accepted" in result.links[0].evidence
    assert "variance_decomposition=pass" in result.links[0].evidence


def test_amount_absolute_floor_is_not_applied_to_a_partial_invoice_line() -> None:
    invoice_doc = _document(
        "<Invoice><InvoiceNo>INV-PARTIAL</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>25</TaxableTotal>"
        "<Line><ItemCode>MOD-100</ItemCode><Description>Part MOD-100</Description>"
        "<Qty>1</Qty><Rate>15</Rate><Amount>15</Amount></Line>"
        "<Line><Description>Unmatched charge line</Description><Qty>1</Qty>"
        "<Rate>10</Rate><Amount>10</Amount></Line></Invoice>",
        DocType.INVOICE,
    )
    source = _source(112, invoice_doc)
    po = _po(113, _document(_coded_po_xml("PO-PARTIAL"), DocType.PO)).model_copy(
        update={"grn_support_qty_by_line": {1: Decimal("1")}}
    )

    amount, delta, expected, *_ = links_module._amount_score_and_variances(
        source, po, ((1, 1, Decimal("1")),), po.grn_support_qty_by_line, MappingConfig()
    )

    assert delta == Decimal("5")
    assert expected == Decimal("10")
    assert amount is not None and amount < Decimal("1")


def test_marketplace_reference_suggests_and_only_invoice_before_po_is_flagged() -> None:
    invoice_doc = _document(
        _coded_invoice_xml(
            "INV-DATE",
            date="20251231",
            qty="1",
            rate="10",
            amount="10",
        ),
        DocType.INVOICE,
    )
    invoice = _with_marketplace_order(_source(114, invoice_doc), "MKT-42")
    po_doc = _document(_coded_po_xml("PO-DATE", date="20260101"), DocType.PO).model_copy(
        update={"raw_text": "Buyer notes: marketplace order MKT-42"}
    )
    po = _po(115, po_doc).model_copy(
        update={
            "open_balance": Decimal("10"),
            "open_qty_by_line": {1: Decimal("1")},
            "grn_support_qty_by_line": {1: Decimal("1")},
            "open_grn_qty_by_line": {1: Decimal("1")},
        }
    )

    before_po = resolve_links([invoice], [po])[0]
    after_po_invoice = _source(
        116,
        _document(
            _coded_invoice_xml(
                "INV-DATE-LATE",
                date="20260220",
                qty="1",
                rate="10",
                amount="10",
            ),
            DocType.INVOICE,
        ),
    )
    after_po_invoice = _with_marketplace_order(after_po_invoice, "MKT-42")
    after_po = resolve_links([after_po_invoice], [po])[0]

    assert before_po.links[0].state is LinkState.SUGGESTED
    assert "review=INVOICE_BEFORE_PO" in before_po.links[0].evidence
    assert "date_check=invoice_before_po" in before_po.links[0].evidence
    assert after_po.links[0].state is LinkState.SUGGESTED
    assert "review=" not in after_po.links[0].evidence
    assert "date_check=not_flagged" in after_po.links[0].evidence
    assert "date_proximity=50_days:strong_mismatch_manual_verification" in (
        after_po.links[0].evidence
    )
    assert "date_proximity_check=fail:strong_mismatch_manual_verification" in (
        after_po.links[0].evidence
    )

    verified_early = _verify_header_reference(
        _source(
            140,
            _document(
                _invoice_xml("INV-VERIFIED-EARLY", po_ref="PO-DATE", date="20251231"),
                DocType.INVOICE,
            ),
        ),
        "po_refs",
    )
    verified_early_result = resolve_links([verified_early], [po])[0]

    assert verified_early_result.links[0].state is LinkState.AUTO
    assert "date_check=invoice_before_po" in verified_early_result.links[0].evidence
    assert "review=INVOICE_BEFORE_PO" in verified_early_result.links[0].evidence


@pytest.mark.parametrize(
    ("invoice_date", "expected_bucket"),
    [
        ("20260108", "normal"),
        ("20260109", "review_lower_confidence"),
        ("20260116", "review_lower_confidence"),
        ("20260117", "strong_mismatch_manual_verification"),
    ],
)
def test_date_proximity_bands_are_explained_separate_checks(
    invoice_date: str, expected_bucket: str
) -> None:
    source = _source(
        122,
        _document(_coded_invoice_xml("INV-DATE-BAND", date=invoice_date), DocType.INVOICE),
    )
    po = _po(123, _document(_coded_po_xml("PO-DATE-BAND", date="20260101"), DocType.PO))
    part = links_module._part_identity(
        source.document.lines[0], po.document.lines[0], MappingConfig()
    )
    score, signals, missing = links_module._line_candidate_breakdown(
        source, po, 1, 1, part, MappingConfig()
    )

    evidence = links_module._candidate_evidence(
        source, po, score, signals, missing, (), MappingConfig()
    )

    normal_date_source = source.model_copy(
        update={
            "document": source.document.model_copy(
                update={
                    "header": source.document.header.model_copy(
                        update={
                            "doc_date": source.document.header.doc_date.model_copy(
                                update={"value": date(2026, 1, 2)}
                            )
                        }
                    )
                }
            )
        }
    )
    normal_score, _, _ = links_module._line_candidate_breakdown(
        normal_date_source, po, 1, 1, part, MappingConfig()
    )

    assert f"date_proximity={(int(invoice_date[6:8]) - 1)}_days:{expected_bucket}" in evidence
    expected_check = {
        "normal": "pass:normal",
        "review_lower_confidence": "warn:review_lower_confidence",
        "strong_mismatch_manual_verification": "fail:strong_mismatch_manual_verification",
    }[expected_bucket]
    assert f"date_proximity_check={expected_check}" in evidence
    assert "date_check=invoice_before_po" not in evidence
    assert score == normal_score


def test_invoice_grn_anchor_fields_must_match_their_corresponding_identifier() -> None:
    invoice_with_grn_ref = _source(
        144,
        _document(_invoice_xml("INV-ANCHOR", grn_ref="GRN-100"), DocType.INVOICE),
    )
    invoice_without_grn_ref = _source(
        146,
        _document(_invoice_xml("INV-ANCHOR"), DocType.INVOICE),
    )
    grn = _source(145, _document(_grn_xml("GRN-100", "PO-100"), DocType.GRN))
    assert links_module._grn_reference_matches_invoice(invoice_with_grn_ref, grn)

    supplier_invoice_header = grn.document.header.model_copy(
        update={
            "supplier_invoice_no": FieldValue(
                value="INV-ANCHOR",
                source=FieldSource.RULE,
                evidence=FieldEvidence(),
                origin=FieldOrigin.OCR,
            )
        }
    )
    supplier_invoice_grn = grn.model_copy(
        update={"document": grn.document.model_copy(update={"header": supplier_invoice_header})}
    )
    assert links_module._grn_reference_matches_invoice(
        invoice_without_grn_ref, supplier_invoice_grn
    )

    challan = FieldValue(
        value="DN-9",
        source=FieldSource.RULE,
        evidence=FieldEvidence(),
        origin=FieldOrigin.OCR,
    )
    invoice_challan_header = invoice_without_grn_ref.document.header.model_copy(
        update={"challan_no": challan}
    )
    invoice_with_challan = invoice_without_grn_ref.model_copy(
        update={
            "document": invoice_without_grn_ref.document.model_copy(
                update={"header": invoice_challan_header}
            )
        }
    )
    grn_challan_header = grn.document.header.model_copy(update={"challan_no": challan})
    grn_with_challan = grn.model_copy(
        update={"document": grn.document.model_copy(update={"header": grn_challan_header})}
    )
    assert links_module._grn_reference_matches_invoice(invoice_with_challan, grn_with_challan)

    cross_reference_header = invoice_without_grn_ref.document.header.model_copy(
        update={
            "challan_no": FieldValue(
                value="GRN-100",
                source=FieldSource.RULE,
                evidence=FieldEvidence(),
                origin=FieldOrigin.OCR,
            )
        }
    )
    cross_reference_invoice = invoice_without_grn_ref.model_copy(
        update={
            "document": invoice_without_grn_ref.document.model_copy(
                update={"header": cross_reference_header}
            )
        }
    )
    grn_with_only_challan = grn.model_copy(
        update={"document": grn.document.model_copy(update={"header": grn_challan_header})}
    )
    assert not links_module._grn_reference_matches_invoice(
        cross_reference_invoice, grn_with_only_challan
    )


def test_invoice_before_grn_is_not_a_date_mismatch() -> None:
    invoice = _verify_header_reference(
        _source(
            124,
            _document(
                _coded_invoice_xml(
                    "INV-BEFORE-GRN",
                    date="20260110",
                    qty="10",
                    rate="10",
                    amount="100",
                ).replace(
                    "<InvoiceDate>20260110</InvoiceDate>",
                    "<InvoiceDate>20260110</InvoiceDate><GRNRef>GRN-LATE</GRNRef>",
                ),
                DocType.INVOICE,
            ),
        ),
        "grn_refs",
    )
    grn = _verify_header_reference(
        _source(
            125,
            _document(
                "<GRN><GRNNo>GRN-LATE</GRNNo><GRNDate>20260115</GRNDate>"
                "<Supplier>Acme Supplies</Supplier><PORef>PO-EARLY</PORef>"
                "<Line><ItemCode>MOD-100</ItemCode><Description>Part MOD-100</Description>"
                "<Qty>10</Qty></Line></GRN>",
                DocType.GRN,
            ),
        ),
        "po_refs",
    )
    po = _po(126, _document(_po_xml("PO-EARLY", date="20260101"), DocType.PO))

    invoice_result, _grn_result = resolve_links([invoice, grn], [po])

    assert any(link.link_type.value == "inv_grn" for link in invoice_result.links)
    assert "GRN_BEFORE_INVOICE" not in (invoice_result.reason_detail or "")
    assert all("invoice_before_po" not in link.evidence for link in invoice_result.links)


def test_exact_part_without_amount_or_reference_remains_a_suggestion() -> None:
    invoice = _source(
        127,
        _document(_coded_invoice_xml("INV-900"), DocType.INVOICE),
    )
    po = _po(128, _document(_coded_po_xml("PO-PART-ONLY"), DocType.PO)).model_copy(
        update={"grn_support_qty_by_line": {}}
    )

    result = resolve_links([invoice], [po])[0]

    assert result.links
    assert result.links[0].state is LinkState.SUGGESTED
    assert result.links[0].confidence == Decimal("0.90")
    assert "GRN accepted quantity × PO rate" in result.links[0].evidence


def test_date_and_open_grn_quantity_break_only_a_close_candidate_tie() -> None:
    invoice_doc = _document(
        _coded_invoice_xml(
            "INV-TIE",
            date="20260203",
            qty="1",
            rate="10",
            amount="10",
        ),
        DocType.INVOICE,
    )
    invoice = _with_marketplace_order(_source(117, invoice_doc), "MKT-TIE")
    earlier_doc = _document(_coded_po_xml("PO-EARLY", date="20260101"), DocType.PO).model_copy(
        update={"raw_text": "Order notes MKT-TIE"}
    )
    closer_doc = _document(_coded_po_xml("PO-CLOSE", date="20260201"), DocType.PO).model_copy(
        update={"raw_text": "Order notes MKT-TIE"}
    )
    earlier = _po(118, earlier_doc).model_copy(
        update={
            "open_balance": Decimal("10"),
            "open_qty_by_line": {1: Decimal("1")},
            "grn_support_qty_by_line": {1: Decimal("1")},
            "open_grn_qty_by_line": {1: Decimal("10")},
        }
    )
    closer = _po(119, closer_doc).model_copy(
        update={
            "open_balance": Decimal("10"),
            "open_qty_by_line": {1: Decimal("1")},
            "grn_support_qty_by_line": {1: Decimal("1")},
            "open_grn_qty_by_line": {1: Decimal("1")},
        }
    )

    tied = resolve_links([invoice], [earlier, closer])[0]
    exact_tie = closer.model_copy(
        update={"document": closer.document.model_copy(update={"header": earlier.document.header})}
    ).model_copy(update={"open_grn_qty_by_line": earlier.open_grn_qty_by_line})
    ambiguous = resolve_links([invoice], [earlier, exact_tie])[0]

    assert tied.links[0].to_document_id == closer.document_id
    assert tied.links[0].state is LinkState.SUGGESTED
    assert "tie_break=date_proximity+open_grn_quantity" in tied.links[0].evidence
    assert not ambiguous.links
    assert ambiguous.reason_detail == "CONTENT_AMBIGUOUS"


def test_fully_billed_po_remains_a_candidate_and_fails_open_quantity_check() -> None:
    invoice = _with_marketplace_order(
        _source(
            120,
            _document(
                _coded_invoice_xml(
                    "INV-DUPLICATE",
                    qty="1",
                    rate="10",
                    amount="10",
                ),
                DocType.INVOICE,
            ),
        ),
        "MKT-FULL",
    )
    po_doc = _document(_coded_po_xml("PO-FULLY-BILLED"), DocType.PO).model_copy(
        update={"raw_text": "MKT-FULL"}
    )
    fully_billed = _po(121, po_doc).model_copy(
        update={
            "open_balance": Decimal("0"),
            "open_qty_by_line": {1: Decimal("0")},
            "grn_support_qty_by_line": {1: Decimal("1")},
            "open_grn_qty_by_line": {1: Decimal("0")},
        }
    )

    result = resolve_links([invoice], [fully_billed])[0]
    ranked = links_module.rank_po_candidates(invoice, [fully_billed], limit=3)

    assert result.links[0].to_document_id == fully_billed.document_id
    assert result.links[0].state is LinkState.SUGGESTED
    assert "OPEN_PO_QUANTITY_EXCEEDED" in result.links[0].evidence
    assert "open_grn_quantity" not in result.links[0].evidence
    assert ranked[0].po_document_id == fully_billed.document_id
