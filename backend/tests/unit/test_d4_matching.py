"""WI-D4 (D90, owner 21 Oct): no self-support and header-level handwritten references.

* A document's own link never counts as evidence in its own check: receipt support comes
  only from other sources' auto or confirmed links (and opening balances), or from
  independent evidence such as the GRN's own lines against the PO's lines.
* A typed handwritten reference creates a suggestion, never an auto link, only when the
  type matches the target, the vendor GSTIN matches, the invoice total equals the
  target's total within one rupee (a PO also through a GRN that cites it) and no other
  document of that type has the same number and vendor.

Synthetic documents only.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from reconbench_core.mapping import MappingDocument, MappingPO, rank_po_candidates, resolve_links
from reconbench_core.mapping.links import OPENING_SUPPORT
from reconbench_core.parsers import parse
from reconbench_core.schema import DocType, Document, LinkState, LinkType

VENDOR = UUID(int=100)
GSTIN = "27AAPFU0939F1ZV"
OTHER_GSTIN = "29AAPFU0939F1ZR"
PO_NO = "PO/26-27/08-081"
GRN_NO = "GRN/BCSL/25-26/08-072"


def _document(xml: str, doc_type: DocType) -> Document:
    result = parse(xml.encode(), {"file_format": "xml", "doc_type": doc_type})
    assert result.success and len(result.documents) == 1
    return result.documents[0]


def _po_document(number: str = PO_NO, *, total: str = "1180", gstin: str = GSTIN) -> Document:
    return _document(
        f"<PurchaseOrder><OrderNo>{number}</OrderNo><OrderDate>20260801</OrderDate>"
        f"<Supplier>Acme Supplies</Supplier><SupplierGSTIN>{gstin}</SupplierGSTIN>"
        f"<TaxableTotal>1000</TaxableTotal><GrandTotal>{total}</GrandTotal>"
        "<Line><Description>AMC</Description><Qty>2</Qty><Rate>500</Rate>"
        "<Amount>1000</Amount></Line></PurchaseOrder>",
        DocType.PO,
    )


def _grn_document(
    number: str = GRN_NO,
    *,
    po_ref: str = PO_NO,
    total: str = "590",
    rate: str = "500",
    description: str = "AMC",
) -> Document:
    amount = str(Decimal(rate))
    return _document(
        f"<GRN><GRNNo>{number}</GRNNo><GRNDate>20260810</GRNDate>"
        f"<Supplier>Acme Supplies</Supplier><SupplierGSTIN>{GSTIN}</SupplierGSTIN>"
        f"<PORef>{po_ref}</PORef><GrandTotal>{total}</GrandTotal>"
        f"<Line><Description>{description}</Description><Qty>1</Qty><Rate>{rate}</Rate>"
        f"<Amount>{amount}</Amount></Line></GRN>",
        DocType.GRN,
    )


def _invoice(refs: list[dict[str, Any]], *, total: str = "1180", gstin: str = GSTIN) -> Document:
    document = _document(
        f"<Invoice><InvoiceNo>INV-77</InvoiceNo><InvoiceDate>20260812</InvoiceDate>"
        f"<Supplier>Acme Supplies</Supplier><SupplierGSTIN>{gstin}</SupplierGSTIN>"
        f"<TaxableTotal>1000</TaxableTotal><InvoiceTotal>{total}</InvoiceTotal>"
        "<Line><Description>Annual service visit</Description><Qty>1</Qty><Rate>1000</Rate>"
        "<Amount>1000</Amount></Line></Invoice>",
        DocType.INVOICE,
    )
    header = document.header.model_copy(
        update={"extras": {**document.header.extras, "typed_refs": refs}}
    )
    return document.model_copy(update={"header": header})


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


def _source(number: int, document: Document) -> MappingDocument:
    return MappingDocument(
        document_id=UUID(int=number), natural_key=str(number), vendor_id=VENDOR, document=document
    )


def _po(number: int, document: Document, **support: Any) -> MappingPO:
    return MappingPO(
        document_id=UUID(int=number),
        vendor_id=VENDOR,
        document=document,
        open_balance=Decimal("1000"),
        open_qty_by_line={1: Decimal("2")},
        grn_support_qty_by_line=support.pop("legacy", {1: Decimal("0")}),
        open_grn_qty_by_line={1: Decimal("0")},
        **support,
    )


def _invoice_links(
    refs: list[dict[str, Any]],
    *,
    total: str = "1180",
    gstin: str = GSTIN,
    pos: list[MappingPO] | None = None,
    grns: list[Document] | None = None,
) -> list[Any]:
    documents = [_source(1, _invoice(refs, total=total, gstin=gstin))]
    documents += [_source(10 + index, grn) for index, grn in enumerate(grns or [])]
    resolutions = resolve_links(documents, pos if pos is not None else [_po(2, _po_document())])
    invoice = next(item for item in resolutions if item.document_id == UUID(int=1))
    return [link for link in invoice.links if "Handwritten" in link.evidence]


# --------------------------------------------------------------------------- handwriting


def test_a_typed_po_reference_with_gstin_and_total_is_a_suggestion_never_auto() -> None:
    [link] = _invoice_links([_ref("po", "08-081")])
    assert (link.link_type, link.to_document_id) == (LinkType.INV_PO, UUID(int=2))
    assert link.state is LinkState.SUGGESTED
    assert "invoice total = PO total" in link.evidence


def test_the_total_must_be_within_one_rupee() -> None:
    assert _invoice_links([_ref("po", PO_NO)], total="1180.80")
    assert not _invoice_links([_ref("po", PO_NO)], total="1181.50")


def test_a_different_vendor_gstin_gives_nothing() -> None:
    assert not _invoice_links([_ref("po", PO_NO)], gstin=OTHER_GSTIN)
    assert not _invoice_links([_ref("po", PO_NO)], pos=[_po(2, _po_document(gstin=OTHER_GSTIN))])


def test_two_documents_with_the_same_number_and_vendor_give_nothing() -> None:
    twins = [_po(2, _po_document("PO/26-27/08-081")), _po(3, _po_document("PO/25-26/08-081"))]
    assert not _invoice_links([_ref("po", "08-081")], pos=twins)


def test_a_partial_delivery_matches_the_total_of_a_grn_that_cites_the_po() -> None:
    [link] = _invoice_links([_ref("po", PO_NO)], total="590", grns=[_grn_document()])
    assert link.link_type is LinkType.INV_PO and link.state is LinkState.SUGGESTED
    assert f"total of GRN {GRN_NO}" in link.evidence


def test_a_typed_grn_reference_links_only_the_grn_and_the_cross_reference_is_recorded() -> None:
    links = _invoice_links(
        [_ref("po", PO_NO), _ref("grn", "08-072")], total="590", grns=[_grn_document()]
    )
    by_type = {link.link_type: link for link in links}
    assert set(by_type) == {LinkType.INV_PO, LinkType.INV_GRN}
    assert by_type[LinkType.INV_GRN].to_document_id == UUID(int=10)
    assert all(link.state is LinkState.SUGGESTED for link in links)
    assert all("handwritten GRN cites the handwritten PO" in link.evidence for link in links)


def test_the_reference_type_must_match_the_target() -> None:
    # A GRN-type number never names a PO, a PO-type number never names a GRN.
    assert not _invoice_links([_ref("grn", "08-081")])
    links = _invoice_links([_ref("po", "08-072")], total="590", grns=[_grn_document()])
    assert all(link.link_type is not LinkType.INV_GRN for link in links)


# --------------------------------------------------------------------------- self-support


def _expected_value(po: MappingPO) -> Decimal:
    grn = _source(5, _grn_document())
    [candidate] = rank_po_candidates(grn, [po], limit=1)
    return Decimal(candidate.signals["expected_grn_value"])


def test_a_grns_own_receipt_never_supports_its_own_check() -> None:
    own = {UUID(int=5): {1: Decimal("1")}}
    other = {UUID(int=6): {1: Decimal("1")}}
    assert _expected_value(_po(2, _po_document(), grn_support_by_source=own)) == Decimal("0")
    assert _expected_value(_po(2, _po_document(), grn_support_by_source=other)) == Decimal("500")
    opening = {OPENING_SUPPORT: {1: Decimal("1")}}
    assert _expected_value(_po(2, _po_document(), grn_support_by_source=opening)) == Decimal("500")
    # The older snapshot without per-source support keeps its ledger figures.
    legacy = _po(2, _po_document(), legacy={1: Decimal("1")})
    assert _expected_value(legacy) == Decimal("500")


def _po_ref_checked(grn: Document) -> bool:
    po = _po(2, _po_document(), grn_support_by_source={})
    [resolution] = resolve_links([_source(5, grn)], [po])
    return any(link.state is LinkState.AUTO for link in resolution.links)


def test_the_grns_own_lines_against_the_po_lines_are_independent_evidence() -> None:
    assert _po_ref_checked(_grn_document())
    # A different rate on the GRN line: no independent evidence, so no auto link.
    assert not _po_ref_checked(_grn_document(rate="450"))
