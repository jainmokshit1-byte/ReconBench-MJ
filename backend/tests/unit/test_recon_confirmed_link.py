"""A person-confirmed link is not flagged again as inferred (LINK_INFERRED).

A content link below the auto-accept threshold asks a person to confirm it (App Flow:
"Step 3 suggestion → Needs Review, LINK_INFERRED → one-click confirm"). Once confirmed,
the case must read like any other verified link; a suggested or auto link below the
threshold still needs review. Synthetic.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

import pytest
from reconbench_core.parsers import parse
from reconbench_core.recon import (
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationSnapshot,
)
from reconbench_core.recon.engine import evaluate
from reconbench_core.recon.types import ReconciliationLink
from reconbench_core.schema import (
    CaseResult,
    CaseStatus,
    DocType,
    Document,
    LinkLevel,
    LinkMethod,
    LinkState,
    ReasonCode,
)

VENDOR = UUID(int=601)

INVOICE = (
    "<Invoice><InvoiceNo>INV-21</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
    "<Supplier>Sample Supplier</Supplier><TaxableTotal>1000</TaxableTotal>"
    "<IGST>180</IGST><InvoiceTotal>1180</InvoiceTotal>"
    "<Line><Description>Widget W-10</Description><Qty>10</Qty><Rate>100</Rate>"
    "<Amount>1000</Amount></Line></Invoice>"
)
PO = (
    "<PurchaseOrder><OrderNo>PO-21</OrderNo><OrderDate>20260101</OrderDate>"
    "<Supplier>Sample Supplier</Supplier><TaxableTotal>1000</TaxableTotal>"
    "<Line><Description>Widget W-10</Description><Qty>10</Qty><Rate>100</Rate>"
    "<Amount>1000</Amount></Line></PurchaseOrder>"
)


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


def _case(state: LinkState, method: LinkMethod, confidence: str) -> CaseResult:
    snapshot = ReconciliationSnapshot(
        documents=(
            _record(1, _document(INVOICE, DocType.INVOICE)),
            _record(3, _document(PO, DocType.PO)),
        ),
        links=(
            ReconciliationLink(
                link_id=UUID(int=10),
                from_document_id=UUID(int=1),
                to_document_id=UUID(int=3),
                method=method,
                state=state,
                confidence=Decimal(confidence),
                level=LinkLevel.LINE,
                line_no=1,
            ),
        ),
        vendor_pan_by_id={VENDOR: "AAPFU0939F"},
    )
    [case] = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 10))
    return case


def _codes(case: CaseResult) -> set[ReasonCode]:
    checks = (*case.checks, *(check for line in case.lines for check in line.checks))
    return (
        set(case.reason_codes)
        | {code for line in case.lines for code in line.reason_codes}
        | {check.code for check in checks if check.code is not None}
    )


@pytest.mark.parametrize("state", [LinkState.SUGGESTED, LinkState.AUTO])
def test_an_unconfirmed_link_below_the_threshold_still_needs_review(state: LinkState) -> None:
    case = _case(state, LinkMethod.CONTENT, "0.88")
    assert ReasonCode.LINK_INFERRED in _codes(case)
    assert case.status is CaseStatus.NEEDS_REVIEW


def test_a_confirmed_content_link_reads_like_a_verified_link() -> None:
    confirmed = _case(LinkState.CONFIRMED, LinkMethod.CONTENT, "0.88")
    verified = _case(LinkState.AUTO, LinkMethod.CONTENT, "0.99")
    assert ReasonCode.LINK_INFERRED not in _codes(confirmed)
    assert confirmed.status is verified.status
    assert _codes(confirmed) == _codes(verified)


def _grn(accepted: str) -> Document:
    return _document(
        "<GRN><GRNNo>GRN-21</GRNNo><GRNDate>2026-01-15</GRNDate>"
        "<POReference>PO-21</POReference><Supplier>Sample Supplier</Supplier>"
        "<Line><Description>Widget W-10</Description>"
        f"<ReceivedQty>{accepted}</ReceivedQty><AcceptedQty>{accepted}</AcceptedQty>"
        "<RejectedQty>0</RejectedQty></Line></GRN>",
        DocType.GRN,
    )


def _grn_link(number: int, line_no: int | None) -> ReconciliationLink:
    return ReconciliationLink(
        link_id=UUID(int=number),
        from_document_id=UUID(int=5),
        to_document_id=UUID(int=3),
        method=LinkMethod.LABELED,
        state=LinkState.AUTO,
        confidence=Decimal("0.99"),
        level=LinkLevel.HEADER if line_no is None else LinkLevel.LINE,
        line_no=line_no,
    )


def _case_with_grn(accepted: str, grn_links: tuple[ReconciliationLink, ...]) -> CaseResult:
    invoice_link = ReconciliationLink(
        link_id=UUID(int=10),
        from_document_id=UUID(int=1),
        to_document_id=UUID(int=3),
        method=LinkMethod.LABELED,
        state=LinkState.AUTO,
        confidence=Decimal("0.99"),
        level=LinkLevel.LINE,
        line_no=1,
    )
    snapshot = ReconciliationSnapshot(
        documents=(
            _record(1, _document(INVOICE, DocType.INVOICE)),
            _record(3, _document(PO, DocType.PO)),
            _record(5, _grn(accepted)),
        ),
        links=(invoice_link, *grn_links),
        vendor_pan_by_id={VENDOR: "AAPFU0939F"},
    )
    [case] = evaluate(snapshot, ReconciliationConfig(), date(2026, 2, 10))
    return case


def test_a_grn_with_header_and_line_links_to_one_po_counts_its_receipt_once() -> None:
    both = _case_with_grn("10", (_grn_link(20, None), _grn_link(21, 1)))
    header_only = _case_with_grn("10", (_grn_link(20, None),))
    assert ReasonCode.GRN_OVER_PO not in _codes(both)
    assert [line.grn_accepted_cum for line in both.lines] == [
        line.grn_accepted_cum for line in header_only.lines
    ]
    assert both.status is header_only.status


def test_a_real_over_receipt_is_still_flagged() -> None:
    over = _case_with_grn("11", (_grn_link(20, None), _grn_link(21, 1)))
    assert ReasonCode.GRN_OVER_PO in _codes(over)
