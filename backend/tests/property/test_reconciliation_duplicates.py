"""Property checks for synthetic AC-8a duplicate fingerprints."""

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from hypothesis import given, settings
from hypothesis import strategies as st
from reconbench_core.parsers import parse
from reconbench_core.recon import (
    ReconciliationConfig,
    ReconciliationDocument,
    ReconciliationSnapshot,
)
from reconbench_core.recon.engine import _duplicate_map
from reconbench_core.schema import DocType, Document


def _parse_invoice(number: str) -> Document:
    xml = (
        f"<Invoice><InvoiceNo>{number}</InvoiceNo><InvoiceDate>20260201</InvoiceDate>"
        "<Supplier>Sample Supplier</Supplier><TaxableTotal>100</TaxableTotal>"
        "<IGST>18</IGST><InvoiceTotal>118</InvoiceTotal>"
        "<Line><Description>Sample Item</Description><Qty>1</Qty><Rate>100</Rate>"
        "<Amount>100</Amount></Line></Invoice>"
    )
    parsed = parse(xml.encode("utf-8"), {"file_format": "xml", "doc_type": DocType.INVOICE})
    assert parsed.success and len(parsed.documents) == 1
    return parsed.documents[0]


_FIRST_DOCUMENT = _parse_invoice("SCAN-A")
_SECOND_DOCUMENT = _parse_invoice("SCAN-B")


def _record(number: int, document: Document, vendor_id: UUID) -> ReconciliationDocument:
    return ReconciliationDocument(
        document_id=UUID(int=number),
        version_id=UUID(int=number + 1000),
        vendor_id=vendor_id,
        natural_key=f"synthetic-{number}",
        document=document,
    )


@given(
    day_offset=st.integers(min_value=0, max_value=3),
    total_delta=st.decimals(
        min_value=Decimal("-1"), max_value=Decimal("1"), places=2, allow_nan=False
    ),
)
@settings(max_examples=50, deadline=None)
def test_same_complete_line_amounts_flag_within_date_and_total_window(
    day_offset: int, total_delta: Decimal
) -> None:
    first_date = _FIRST_DOCUMENT.header.doc_date
    second_date = _SECOND_DOCUMENT.header.doc_date
    first_total = _FIRST_DOCUMENT.header.grand_total
    second_total = _SECOND_DOCUMENT.header.grand_total
    assert first_date is not None and second_date is not None
    assert first_total is not None and second_total is not None

    first = _record(1, _FIRST_DOCUMENT, UUID(int=501))
    second_header = _SECOND_DOCUMENT.header.model_copy(
        update={
            "doc_date": second_date.model_copy(
                update={"value": first_date.value + timedelta(days=day_offset)}
            ),
            "grand_total": second_total.model_copy(
                update={"value": first_total.value + total_delta}
            ),
        }
    )
    second_document = _SECOND_DOCUMENT.model_copy(update={"header": second_header})
    second = _record(2, second_document, UUID(int=502))
    snapshot = ReconciliationSnapshot(documents=(first, second))

    exact, suspected = _duplicate_map((first, second), snapshot, ReconciliationConfig())

    assert not exact
    assert suspected == {second.document_id: first.document_id}
