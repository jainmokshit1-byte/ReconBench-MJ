"""Party-role guard: a buyer GSTIN is never confirmed as the vendor's (scan fix WI-3)."""

from __future__ import annotations

from reconbench_core.extraction.extract import REVIEW_FLOOR, extract_document
from reconbench_core.extraction.layout import DocumentLayout, Word
from reconbench_core.parsers.parties import guard_unconfigured_parties
from reconbench_core.parsers.types import BuyerEntityHint, ParseResult, ParserHints
from reconbench_core.schema import DocType, FileFormat
from tests.support.layouts import document, gstin, page, row

VENDOR = gstin("29AABCU9603R1Z")
BUYER = gstin("27AAPFU0939F1Z")
ENTITIES = (BuyerEntityHint(gstin=BUYER, name="Synthetic Buyer Pvt Ltd"),)


def _invoice(*party_rows: list[Word]) -> DocumentLayout:
    """A small consistent invoice; only the party rows change between tests."""

    return document(
        page(
            row(100, (1000, "TAX INVOICE")),
            row(200, (1500, "Invoice No."), (1900, "Dated")),
            row(250, (1500, "SYN-INV-0042"), (1900, "05/08/2026")),
            *party_rows,
            row(
                900,
                (100, "Sl"),
                (300, "Description of Goods"),
                (1400, "Quantity"),
                (1700, "Rate"),
                (2150, "Amount"),
            ),
            row(
                980,
                (100, "1"),
                (300, "Synthetic widget"),
                (1400, "2 No"),
                (1700, "500.00"),
                (2150, "1,000.00"),
            ),
            row(1060, (700, "CGST"), (2150, "90.00")),
            row(1140, (700, "SGST"), (2150, "90.00")),
            row(1240, (700, "Total"), (1400, "2 No"), (2150, "1,180.00")),
        )
    )


def _po(*party_rows: list[Word]) -> DocumentLayout:
    """A small PO issued by the buyer: its "for <Company>" line is the buyer's own name."""

    return document(
        page(
            row(100, (1000, "PURCHASE ORDER")),
            row(200, (1500, "PO No."), (1900, "Dated")),
            row(250, (1500, "SYN-PO-0042"), (1900, "05/08/2026")),
            *party_rows,
            row(
                900,
                (100, "Sl"),
                (300, "Description of Goods"),
                (1400, "Quantity"),
                (1700, "Rate"),
                (2150, "Amount"),
            ),
            row(
                980,
                (100, "1"),
                (300, "Synthetic widget"),
                (1400, "2 No"),
                (1700, "500.00"),
                (2150, "1,000.00"),
            ),
            row(1240, (700, "Total"), (1400, "2 No"), (2150, "1,000.00")),
            row(1500, (1700, "for Synthetic Buyer Pvt Ltd")),
            row(1650, (1700, "Authorised Signatory")),
        )
    )


def _extract(layout: DocumentLayout, entities: tuple[BuyerEntityHint, ...] = ()):  # type: ignore[no-untyped-def]
    extraction = extract_document(layout, doc_type=DocType.INVOICE, buyer_entities=entities)
    assert extraction is not None
    return extraction


def test_single_unlabelled_gstin_without_entities_is_capped_and_flagged() -> None:
    extraction = _extract(_invoice(row(400, (100, f"GSTIN: {BUYER}"))))

    header = extraction.document.header
    assert header.vendor_gstin is not None and header.vendor_gstin.value == BUYER
    assert header.vendor_gstin.confidence is not None
    assert header.vendor_gstin.confidence < REVIEW_FLOOR
    assert extraction.checks["vendor_gstin"].note == "party role not established"
    assert extraction.document.needs_verification
    assert "vendor_gstin" in extraction.document.header.extras["review_fields"]


def test_single_unlabelled_gstin_with_entities_is_the_buyer() -> None:
    extraction = _extract(_invoice(row(400, (100, f"GSTIN: {BUYER}"))), ENTITIES)

    header = extraction.document.header
    assert header.vendor_gstin is None
    assert header.buyer_gstin is not None and header.buyer_gstin.value == BUYER


def test_bill_to_block_first_and_seller_block_second_keeps_the_seller() -> None:
    extraction = _extract(
        _invoice(
            row(400, (100, "Bill To")),
            row(450, (100, "Synthetic Buyer Pvt Ltd")),
            row(500, (100, f"GSTIN: {BUYER}")),
            row(650, (100, "Sold By")),
            row(700, (100, "Synthetic Traders Pvt Ltd")),
            row(750, (100, f"GSTIN: {VENDOR}")),
        )
    )

    header = extraction.document.header
    assert header.vendor_gstin is not None and header.vendor_gstin.value == VENDOR
    assert header.buyer_gstin is not None and header.buyer_gstin.value == BUYER
    assert header.vendor_gstin.confidence is not None
    assert header.vendor_gstin.confidence >= REVIEW_FLOOR


def test_repeated_unlabelled_gstin_is_the_buyer_and_the_vendor_needs_review() -> None:
    extraction = _extract(
        _invoice(
            row(400, (100, f"GSTIN: {VENDOR}")),
            row(550, (100, f"GSTIN: {BUYER}")),
            row(700, (100, f"GSTIN: {BUYER}")),
        )
    )

    header = extraction.document.header
    assert header.buyer_gstin is not None and header.buyer_gstin.value == BUYER
    assert header.vendor_gstin is not None and header.vendor_gstin.value == VENDOR
    # Neither GSTIN carries a label, so the repeat alone decides the buyer and the
    # vendor stays below the review floor until a person (or a configured entity) agrees.
    assert header.vendor_gstin.confidence is not None
    assert header.vendor_gstin.confidence < REVIEW_FLOOR


def test_configured_entities_keep_the_checksum_confirmation() -> None:
    extraction = _extract(
        _invoice(
            row(400, (100, f"GSTIN: {VENDOR}")),
            row(550, (100, f"GSTIN: {BUYER}")),
        ),
        ENTITIES,
    )

    header = extraction.document.header
    assert header.vendor_gstin is not None and header.vendor_gstin.value == VENDOR
    assert extraction.checks["vendor_gstin"].note == "GSTIN checksum valid"


def test_empty_entities_add_a_warning_and_list_the_vendor_gstin_for_review() -> None:
    extraction = _extract(
        _invoice(
            row(400, (100, "Sold By")),
            row(450, (100, f"GSTIN: {VENDOR}")),
            row(550, (100, "Bill To")),
            row(600, (100, f"GSTIN: {BUYER}")),
        )
    )
    result = ParseResult(
        success=True,
        file_format=FileFormat.PDF,
        documents=(extraction.document,),
        layout_signature="synthetic",
    )

    guarded = guard_unconfigured_parties(result, ParserHints(doc_type=DocType.INVOICE))
    configured = guard_unconfigured_parties(
        result, ParserHints(doc_type=DocType.INVOICE, buyer_entities=ENTITIES)
    )

    assert [warning.code for warning in guarded.warnings] == ["BUYER_ENTITIES_NOT_CONFIGURED"]
    assert guarded.documents[0].needs_verification
    assert "vendor_gstin" in guarded.documents[0].header.extras["review_fields"]
    assert configured is result
    # Confidence itself is untouched: the review listing, not a cap, carries the warning.
    assert guarded.documents[0].header.vendor_gstin is not None
    assert guarded.documents[0].header.vendor_gstin.confidence == (
        extraction.document.header.vendor_gstin.confidence  # type: ignore[union-attr]
    )


# --- Vendor name without buyer entities (scan fix WI-3, D60) ---------------------------------


def _extract_po(layout: DocumentLayout, entities: tuple[BuyerEntityHint, ...] = ()):  # type: ignore[no-untyped-def]
    extraction = extract_document(layout, doc_type=DocType.PO, buyer_entities=entities)
    assert extraction is not None
    return extraction


def test_po_issuer_line_without_entities_is_withheld_for_review() -> None:
    extraction = _extract_po(_po(row(400, (100, f"Supplier GSTIN: {VENDOR}"))))

    document = extraction.document
    assert document.header.vendor_name is None
    assert "vendor_name" in document.header.extras["review_fields"]
    assert document.needs_verification
    assert "VENDOR_NAME_ROLE_UNESTABLISHED" in {warning.code for warning in extraction.warnings}


def test_po_supplier_block_without_entities_is_the_vendor_name() -> None:
    extraction = _extract_po(
        _po(
            row(400, (100, "Supplier")),
            row(450, (100, "Synthetic Traders Pvt Ltd")),
            row(500, (100, f"GSTIN: {VENDOR}")),
        )
    )

    name = extraction.document.header.vendor_name
    assert name is not None and name.value == "Synthetic Traders Pvt Ltd"
    assert "VENDOR_NAME_ROLE_UNESTABLISHED" not in {w.code for w in extraction.warnings}


def test_po_issuer_line_with_entities_is_rejected_as_the_buyer() -> None:
    extraction = _extract_po(_po(row(400, (100, f"Supplier GSTIN: {VENDOR}"))), ENTITIES)

    document = extraction.document
    assert document.header.vendor_name is None
    assert "vendor_name" not in document.header.extras["review_fields"]
    assert "VENDOR_NAME_ROLE_UNESTABLISHED" not in {w.code for w in extraction.warnings}


def test_invoice_signatory_without_entities_is_the_seller() -> None:
    # An invoice is issued by the seller, so its "for <Company>" line stays the vendor name.
    extraction = _extract(
        _invoice(
            row(400, (100, f"GSTIN: {VENDOR}")),
            row(1500, (1700, "for Synthetic Traders Pvt Ltd")),
        )
    )

    name = extraction.document.header.vendor_name
    assert name is not None and name.value == "Synthetic Traders Pvt Ltd"
