"""PD-2a: an invoice read by AI is never system-checked and never auto-links without a person."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st
from reconbench_core.mapping import MappingConfig, MappingDocument, MappingPO, resolve_links
from reconbench_core.schema import (
    DocType,
    Document,
    FieldOrigin,
    FieldSource,
    FieldValue,
    FieldVerification,
    LinkState,
)
from tests.unit import test_phase4_mapping_links as mapping_case

_INVOICES = {
    "header_po_ref": mapping_case._coded_invoice_xml("INV-PD2A-1", po_ref="PO-PD2A-9"),
    "line_po_ref": mapping_case._coded_invoice_xml("INV-PD2A-2").replace(
        "<Line>", "<Line><PORef>PO-PD2A-9</PORef>"
    ),
    "grn_ref": mapping_case._invoice_xml(
        "INV-PD2A-3",
        grn_ref="GRN-PD2A-8",
        qty="100",
        rate="10",
        amount="1000",
        description="Part MOD-100",
    ),
    "no_reference": mapping_case._coded_invoice_xml("INV-PD2A-4"),
}
_GRN_XML = (
    "<GRN><GRNNo>GRN-PD2A-8</GRNNo><GRNDate>20260115</GRNDate>"
    "<SupplierInvoiceNo>{invoice_no}</SupplierInvoiceNo>"
    "<Supplier>Acme Supplies</Supplier><PORef>PO-PD2A-9</PORef>"
    "<Line><ItemCode>MOD-100</ItemCode><Description>Part MOD-100</Description>"
    "<Qty>100</Qty></Line></GRN>"
)
_PO = mapping_case._po(
    9, mapping_case._document(mapping_case._coded_po_xml("PO-PD2A-9"), DocType.PO)
)
_BILLED_PO = _PO.model_copy(
    update={"open_balance": Decimal("0"), "open_qty_by_line": {1: Decimal("0")}}
)

# Every way a value can be marked as read by AI: its origin, its source, or both.
_AI_READ = st.sampled_from(
    [
        (FieldOrigin.AI, FieldSource.AI),
        (FieldOrigin.AI, FieldSource.RULE),
        (FieldOrigin.OCR, FieldSource.AI),
        (FieldOrigin.ERP_IMPORT, FieldSource.AI),
        (None, FieldSource.AI),
    ]
)
_NOT_BY_A_PERSON = st.sampled_from([FieldVerification.NONE, FieldVerification.SYSTEM_CHECK])
# Often at or above the system-check floor, where only PD-2a keeps the value out.
_CONFIDENCE = st.one_of(
    st.none(),
    st.sampled_from([Decimal("0.85"), Decimal("0.9"), Decimal("0.99"), Decimal("1")]),
    st.decimals(min_value=Decimal("0"), max_value=Decimal("1"), places=2, allow_nan=False),
)


@st.composite
def _read_by_ai(draw: st.DrawFn, document: Document) -> Document:
    def read(value: object) -> object:
        if not isinstance(value, FieldValue):
            return value
        origin, source = draw(_AI_READ)
        return value.model_copy(
            update={
                "origin": origin,
                "source": source,
                "confidence": draw(_CONFIDENCE),
                "verification": draw(_NOT_BY_A_PERSON),
            }
        )

    header = document.header.model_copy(
        update={
            name: read(getattr(document.header, name))
            for name in type(document.header).model_fields
        }
    )
    lines = tuple(
        line.model_copy(
            update={name: read(getattr(line, name)) for name in type(line).model_fields}
        )
        for line in document.lines
    )
    return document.model_copy(update={"header": header, "lines": lines})


@st.composite
def _case(
    draw: st.DrawFn,
) -> tuple[MappingDocument, tuple[MappingDocument, ...], MappingPO, MappingConfig]:
    kind = draw(st.sampled_from(sorted(_INVOICES)))
    parsed = mapping_case._document(_INVOICES[kind], DocType.INVOICE)
    invoice = mapping_case._source(1, draw(_read_by_ai(parsed)))
    peers: tuple[MappingDocument, ...] = ()
    if draw(st.booleans()):
        invoice_no = parsed.header.doc_no.value if parsed.header.doc_no else ""
        grn = mapping_case._source(
            2, mapping_case._document(_GRN_XML.format(invoice_no=invoice_no), DocType.GRN)
        )
        for field_name in ("po_refs", "supplier_invoice_no"):
            grn = mapping_case._verify_header_reference(grn, field_name)
        peers = (grn,)
    po = draw(st.sampled_from([_PO, _BILLED_PO]))
    # A zero confidence floor shows that confidence is not what keeps the value out.
    floor = draw(st.sampled_from([Decimal("0"), Decimal("0.85")]))
    return invoice, peers, po, MappingConfig(reference_system_check_min_confidence=floor)


@given(case=_case())
@settings(max_examples=200, deadline=None)
def test_an_invoice_read_by_ai_is_never_system_checked_or_auto_linked(
    case: tuple[MappingDocument, tuple[MappingDocument, ...], MappingPO, MappingConfig],
) -> None:
    invoice, peers, po, config = case

    results = {item.document_id: item for item in resolve_links((invoice, *peers), (po,), config)}

    result = results[invoice.document_id]
    assert all(link.state is LinkState.SUGGESTED for link in result.links)
    assert all(
        item.verification is not FieldVerification.SYSTEM_CHECK
        for item in result.field_verifications
    )
