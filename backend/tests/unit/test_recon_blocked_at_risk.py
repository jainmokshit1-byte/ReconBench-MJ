"""A blocked case (duplicate or unlinked) never carries an at-risk amount (D95).

The schema check ``recon_case_atrisk_ck`` requires ``at_risk = 0`` for those statuses. A
partly linked invoice (one line on a PO at a lower rate, one line on no PO line) is
``unlinked`` and used to keep the first line's at-risk amount. Synthetic.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from reconbench_core.parsers import parse
from reconbench_core.recon import (
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationSnapshot,
)
from reconbench_core.recon.engine import evaluate
from reconbench_core.recon.types import ReconciliationLink
from reconbench_core.schema import CaseStatus, DocType, Document, LinkLevel, LinkMethod, LinkState

VENDOR = UUID(int=501)


def _document(xml: str, doc_type: DocType) -> Document:
    parsed = parse(xml.encode(), {"file_format": "xml", "doc_type": doc_type})
    assert parsed.success and len(parsed.documents) == 1
    return parsed.documents[0]


def _record(number: int, document: Document) -> ReconciliationDocument:
    return ReconciliationDocument(
        document_id=UUID(int=number),
        version_id=UUID(int=number + 1000),
        vendor_id=VENDOR,
        natural_key=f"synthetic-{number}",
        document=document,
    )


INVOICE = (
    "<Invoice><InvoiceNo>INV-9</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
    "<Supplier>Sample Supplier</Supplier><TaxableTotal>1200</TaxableTotal>"
    "<IGST>216</IGST><InvoiceTotal>1416</InvoiceTotal>"
    "<Line><Description>Widget W-10</Description><Qty>10</Qty><Rate>120</Rate>"
    "<Amount>1200</Amount></Line></Invoice>"
)
PARTLY_LINKED = (
    "<Invoice><InvoiceNo>INV-10</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
    "<Supplier>Sample Supplier</Supplier><TaxableTotal>1700</TaxableTotal>"
    "<IGST>306</IGST><InvoiceTotal>2006</InvoiceTotal>"
    "<Line><Description>Widget W-10</Description><Qty>10</Qty><Rate>120</Rate>"
    "<Amount>1200</Amount></Line>"
    "<Line><Description>Gasket G-77 kit</Description><Qty>5</Qty><Rate>100</Rate>"
    "<Amount>500</Amount></Line></Invoice>"
)
PO = (
    "<PurchaseOrder><OrderNo>PO-9</OrderNo><OrderDate>20260101</OrderDate>"
    "<Supplier>Sample Supplier</Supplier><TaxableTotal>1000</TaxableTotal>"
    "<Line><Description>Widget W-10</Description><Qty>10</Qty><Rate>100</Rate>"
    "<Amount>1000</Amount></Line></PurchaseOrder>"
)


def _link(number: int, source: int, line_no: int | None = None) -> ReconciliationLink:
    return ReconciliationLink(
        link_id=UUID(int=number),
        from_document_id=UUID(int=source),
        to_document_id=UUID(int=3),
        method=LinkMethod.LABELED,
        state=LinkState.AUTO,
        confidence=Decimal("0.99"),
        level=LinkLevel.HEADER if line_no is None else LinkLevel.LINE,
        line_no=line_no,
    )


def test_a_linked_duplicate_has_no_at_risk_amount() -> None:
    invoice = _document(INVOICE, DocType.INVOICE)
    snapshot = ReconciliationSnapshot(
        documents=(
            _record(1, invoice),
            _record(2, invoice),
            _record(3, _document(PO, DocType.PO)),
        ),
        links=(_link(10, 1), _link(11, 2)),
        vendor_pan_by_id={VENDOR: "AAPFU0939F"},
    )
    results = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 10))
    duplicates = [case for case in results if case.status is CaseStatus.DUPLICATE]
    assert duplicates, [case.status for case in results]
    for case in duplicates:
        assert case.at_risk == 0 and case.at_risk_gst == 0
        assert case.blocked_value == Decimal("1200")


def test_a_partly_linked_unlinked_case_has_no_at_risk_amount() -> None:
    snapshot = ReconciliationSnapshot(
        documents=(
            _record(1, _document(PARTLY_LINKED, DocType.INVOICE)),
            _record(3, _document(PO, DocType.PO)),
        ),
        links=(_link(10, 1, line_no=1),),
        vendor_pan_by_id={VENDOR: "AAPFU0939F"},
    )
    [case] = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 10))
    assert case.status is CaseStatus.UNLINKED
    assert case.at_risk == 0 and case.at_risk_gst == 0
    assert case.blocked_value == Decimal("1700")
