"""Type-aware handwritten references in the matcher (accuracy plan WI-Q2, decision D88).

A PO-type reference may only point to a PO and a GRN-type reference only to a GRN; a
short ``MM-NNN`` number is used only because it is typed; every link that rests on a
handwritten reference stays a suggestion. Synthetic documents only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from reconbench_core.mapping import MappingDocument, MappingPO, resolve_links
from reconbench_core.parsers import parse
from reconbench_core.schema import DocType, Document, LinkState, LinkType

_VENDOR = UUID(int=100)
PO_NO = "PO/25-26/01-100"
GRN_NO = "GRN/BCSL/25-26/01-115"


def _document(xml: str, doc_type: DocType) -> Document:
    result = parse(xml.encode(), {"file_format": "xml", "doc_type": doc_type})
    assert result.success and len(result.documents) == 1
    return result.documents[0]


def _invoice(number: str, refs: list[dict[str, Any]], *, qty: str = "100") -> Document:
    amount = str(Decimal(qty) * 10)
    document = _document(
        f"<Invoice><InvoiceNo>{number}</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        f"<Supplier>Acme Supplies</Supplier><TaxableTotal>{amount}</TaxableTotal>"
        f"<InvoiceTotal>{amount}</InvoiceTotal><Line><ItemCode>MOD-100</ItemCode>"
        f"<Description>Part MOD-100</Description><Qty>{qty}</Qty><Rate>10</Rate>"
        f"<Amount>{amount}</Amount></Line></Invoice>",
        DocType.INVOICE,
    )
    header = document.header.model_copy(
        update={"extras": {**document.header.extras, "typed_refs": refs}}
    )
    return document.model_copy(update={"header": header})


def _po_document(number: str = PO_NO) -> Document:
    return _document(
        f"<PurchaseOrder><OrderNo>{number}</OrderNo><OrderDate>20260101</OrderDate>"
        "<Supplier>Acme Supplies</Supplier><TaxableTotal>1000</TaxableTotal>"
        "<Line><ItemCode>MOD-100</ItemCode><Description>Part MOD-100</Description>"
        "<Qty>100</Qty><Rate>10</Rate><Amount>1000</Amount></Line></PurchaseOrder>",
        DocType.PO,
    )


def _grn_document(number: str = GRN_NO, po_ref: str = PO_NO) -> Document:
    return _document(
        f"<GRN><GRNNo>{number}</GRNNo><GRNDate>20260115</GRNDate>"
        f"<Supplier>Acme Supplies</Supplier><PORef>{po_ref}</PORef>"
        "<Line><ItemCode>MOD-100</ItemCode><Description>Part MOD-100</Description>"
        "<Qty>100</Qty><Rate>10</Rate><Amount>1000</Amount></Line></GRN>",
        DocType.GRN,
    )


def _source(number: int, document: Document) -> MappingDocument:
    return MappingDocument(
        document_id=UUID(int=number),
        natural_key=str(number),
        vendor_id=_VENDOR,
        document=document,
    )


def _po(number: int, document: Document) -> MappingPO:
    return MappingPO(
        document_id=UUID(int=number),
        vendor_id=_VENDOR,
        document=document,
        open_balance=Decimal("1000"),
        open_qty_by_line={1: Decimal("100")},
        grn_support_qty_by_line={1: Decimal("100")},
        open_grn_qty_by_line={1: Decimal("100")},
    )


def _ref(ref_type: str, normalised: str) -> dict[str, Any]:
    return {
        "type": ref_type,
        "text": normalised,
        "normalised": normalised,
        "short": normalised[-6:],
        "type_source": "model",
        "steps": [],
        "page": 1,
    }


def _reference_signal(refs: list[dict[str, Any]]) -> Decimal:
    result = resolve_links([_source(1, _invoice("INV-1", refs))], [_po(2, _po_document())])[0]
    assert all(link.state is not LinkState.AUTO for link in result.links)
    return Decimal(result.candidates[0].signals["reference"]) if result.candidates else Decimal(0)


def test_a_typed_po_reference_full_or_short_names_the_po_and_stays_a_suggestion() -> None:
    assert _reference_signal([_ref("po", PO_NO)]) == Decimal("1")
    assert _reference_signal([_ref("po", "01-100")]) == Decimal("1")
    result = resolve_links(
        [_source(1, _invoice("INV-1", [_ref("po", "01-100")]))], [_po(2, _po_document())]
    )[0]
    assert result.links and {link.state for link in result.links} == {LinkState.SUGGESTED}


def test_a_grn_type_or_untyped_reference_never_names_a_po() -> None:
    assert _reference_signal([_ref("grn", "01-100")]) == Decimal("0")
    assert _reference_signal([_ref("other", "01-100")]) == Decimal("0")
    assert _reference_signal([{**_ref("po", "01-100"), "normalised": None}]) == Decimal("0")


def test_a_handwritten_grn_number_links_the_invoice_to_that_grn_as_a_suggestion() -> None:
    invoice = _source(1, _invoice("INV-1", [_ref("grn", "01-115")]))
    grn = _source(3, _grn_document())
    results = {
        item.document_id: item for item in resolve_links([invoice, grn], [_po(2, _po_document())])
    }
    links = results[invoice.document_id].links
    to_grn = [link for link in links if link.link_type is LinkType.INV_GRN]
    assert [link.to_document_id for link in to_grn] == [grn.document_id]
    assert all(link.state is LinkState.SUGGESTED for link in links)
    assert "handwritten_grn_reference" in to_grn[0].evidence


def test_the_same_short_number_typed_as_a_po_makes_no_grn_link() -> None:
    invoice = _source(1, _invoice("INV-1", [_ref("po", "01-115")]))
    grn = _source(3, _grn_document())
    results = {
        item.document_id: item for item in resolve_links([invoice, grn], [_po(2, _po_document())])
    }
    assert not [
        link for link in results[invoice.document_id].links if link.link_type is LinkType.INV_GRN
    ]


def test_a_handwritten_grn_number_without_part_or_amount_support_makes_no_link() -> None:
    invoice = _source(1, _invoice("INV-1", [_ref("grn", "01-115")], qty="3"))
    grn = _source(
        3,
        _document(
            f"<GRN><GRNNo>{GRN_NO}</GRNNo><GRNDate>20260115</GRNDate>"
            f"<Supplier>Acme Supplies</Supplier><PORef>{PO_NO}</PORef>"
            "<Line><Description>Other goods</Description><Qty>40</Qty></Line></GRN>",
            DocType.GRN,
        ),
    )
    results = {
        item.document_id: item for item in resolve_links([invoice, grn], [_po(2, _po_document())])
    }
    assert not [
        link for link in results[invoice.document_id].links if link.link_type is LinkType.INV_GRN
    ]
