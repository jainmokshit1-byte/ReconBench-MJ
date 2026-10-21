from __future__ import annotations

import io
from datetime import date
from decimal import Decimal

from PIL import Image, ImageDraw
from reconbench_app.document_identity import (
    content_fingerprint,
    difference_hash,
    document_identity_collision_key,
    document_natural_key,
    document_rescan_key,
    identity_payload,
    is_verified_field,
    page_hashes_within_distance,
    verified_field_confidence,
)
from reconbench_app.pdf_sandbox import page_image_hashes_isolated
from reconbench_core.parsers.types import PageImageHash
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    InvoiceHeader,
)
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def _field(value: object, *, confidence: str = "0.80", source: FieldSource = FieldSource.RULE):
    return FieldValue(
        value=value,
        confidence=Decimal(confidence),
        source=source,
        evidence=FieldEvidence(page=1, snippet="synthetic"),
    )


def _invoice(
    *,
    number: str = "INV-SYN-001",
    vendor: str = "Synthetic Supplier",
    gstin: str | None = None,
    confidence: str = "0.80",
    source: FieldSource = FieldSource.RULE,
    complete_lines: bool = True,
) -> Document:
    header = InvoiceHeader(
        doc_type=DocType.INVOICE,
        doc_no=_field(number, confidence=confidence, source=source),
        doc_date=_field(date(2026, 4, 1), confidence=confidence, source=source),
        vendor_name=_field(vendor, confidence=confidence, source=source),
        vendor_gstin=(
            _field(gstin, confidence=confidence, source=source) if gstin is not None else None
        ),
        grand_total=_field(Decimal("118.00"), confidence=confidence, source=source),
    )
    line = DocLine(
        line_no=1,
        description=_field("Synthetic item", confidence=confidence, source=source),
        quantity=(
            _field(Decimal("1"), confidence=confidence, source=source) if complete_lines else None
        ),
        rate=(
            _field(Decimal("100"), confidence=confidence, source=source) if complete_lines else None
        ),
        amount=(
            _field(Decimal("100.00"), confidence=confidence, source=source)
            if complete_lines
            else None
        ),
    )
    return Document(header=header, lines=(line,), source_locator="pdf:synthetic:pages:1")


def test_preverification_identity_uses_exact_content_and_ordered_page_hashes() -> None:
    pages = (PageImageHash(page=1, value="0123456789abcdef"),)
    first = _invoice(number="INV-SYN-001", vendor="Supplier A")
    misread = _invoice(number="INV-SYN-OO1", vendor="Supplier B")

    assert content_fingerprint(first) == content_fingerprint(misread)
    assert document_natural_key(first, "a" * 64, page_hashes=pages) == document_natural_key(
        misread, "b" * 64, page_hashes=pages
    )
    assert identity_payload(first, "a" * 64, page_hashes=pages)["kind"] == "fingerprint"
    assert document_rescan_key(first, "a" * 64, page_hashes=pages) == document_rescan_key(
        misread, "b" * 64, page_hashes=pages
    )
    changed_page = (PageImageHash(page=1, value="fedcba9876543210"),)
    assert document_rescan_key(first, "a" * 64, page_hashes=pages) != document_rescan_key(
        first, "a" * 64, page_hashes=changed_page
    )
    two_pages = (
        PageImageHash(page=1, value="0123456789abcdef"),
        PageImageHash(page=2, value="fedcba9876543210"),
    )
    reversed_pages = tuple(reversed(two_pages))
    assert document_rescan_key(first, "a" * 64, page_hashes=two_pages) == document_rescan_key(
        first, "a" * 64, page_hashes=reversed_pages
    )
    swapped_pages = (
        PageImageHash(page=1, value="fedcba9876543210"),
        PageImageHash(page=2, value="0123456789abcdef"),
    )
    assert document_rescan_key(first, "a" * 64, page_hashes=two_pages) != document_rescan_key(
        first, "a" * 64, page_hashes=swapped_pages
    )


def test_rescan_page_hash_tolerance_is_bounded_and_ordered() -> None:
    zero = "0000000000000000"
    one_bit = "0000000000000001"
    seventeen_bits = "000000000001ffff"

    assert page_hashes_within_distance((zero,), (zero,))
    assert page_hashes_within_distance((zero,), (one_bit,))
    assert not page_hashes_within_distance((zero,), (seventeen_bits,))
    assert not page_hashes_within_distance((zero,), ())
    assert not page_hashes_within_distance((zero, seventeen_bits), (seventeen_bits, zero))


def test_verified_identity_uses_only_fields_that_pass_the_trust_rule() -> None:
    pages = (PageImageHash(page=1, value="0123456789abcdef"),)
    first = _invoice(source=FieldSource.MANUAL, confidence="1.00")
    changed = _invoice(
        number="INV-SYN-002", vendor="Other Synthetic Supplier", source=FieldSource.MANUAL
    )

    assert identity_payload(first, "a" * 64, page_hashes=pages)["kind"] == "verified"
    assert document_natural_key(first, "a" * 64, page_hashes=pages) != document_natural_key(
        changed, "a" * 64, page_hashes=pages
    )
    other_page = (PageImageHash(page=1, value="fedcba9876543210"),)
    assert document_identity_collision_key(
        first, "a" * 64, page_hashes=pages
    ) != document_identity_collision_key(changed, "b" * 64, page_hashes=other_page)


def test_supplier_pan_enters_content_fingerprint_only_when_verified() -> None:
    pages = (PageImageHash(page=1, value="0123456789abcdef"),)
    unverified_a = _invoice(gstin="27AAAAA0000A1Z0")
    unverified_b = _invoice(gstin="27BBBBB0000B1Z0")
    verified_a = _invoice(gstin="27AAAAA0000A1Z0", confidence="0.99")
    verified_b = _invoice(gstin="27BBBBB0000B1Z0", confidence="0.99")

    assert document_natural_key(unverified_a, "a" * 64, page_hashes=pages) == document_natural_key(
        unverified_b, "b" * 64, page_hashes=pages
    )
    assert content_fingerprint(verified_a) != content_fingerprint(verified_b)


def test_incomplete_fingerprint_falls_back_to_file_hash_and_source_locator() -> None:
    incomplete = _invoice(complete_lines=False)
    another_locator = incomplete.model_copy(update={"source_locator": "pdf:synthetic:pages:2"})

    assert (
        identity_payload(
            incomplete, "a" * 64, page_hashes=(PageImageHash(page=1, value="0123456789abcdef"),)
        )["kind"]
        == "source"
    )
    assert document_natural_key(incomplete, "a" * 64) == document_natural_key(incomplete, "a" * 64)
    assert document_natural_key(incomplete, "a" * 64) != document_natural_key(incomplete, "b" * 64)
    assert document_natural_key(incomplete, "a" * 64) != document_natural_key(
        another_locator, "a" * 64
    )
    assert document_rescan_key(incomplete, "a" * 64) == document_rescan_key(incomplete, "a" * 64)
    assert document_rescan_key(incomplete, "a" * 64) != document_rescan_key(incomplete, "b" * 64)


def test_d19_score_is_minimum_confidence_among_verified_fields() -> None:
    document = _invoice(source=FieldSource.RULE, confidence="0.99")
    manual_line = document.lines[0].model_copy(
        update={
            "description": _field("Human confirmed", confidence="0.90", source=FieldSource.MANUAL)
        }
    )
    document = document.model_copy(update={"lines": (manual_line,)})

    assert verified_field_confidence(document) == Decimal("0.90")
    assert verified_field_confidence(_invoice()) is None
    assert verified_field_confidence(_invoice(confidence="0.80"), structured=True) == Decimal(
        "0.80"
    )


def test_unstructured_rule_value_without_confidence_is_not_verified() -> None:
    field = FieldValue[str](
        value="PO-SYN-001",
        confidence=None,
        source=FieldSource.RULE,
        evidence=FieldEvidence(page=1, snippet="synthetic"),
    )

    assert not is_verified_field(field, structured=False)


def test_difference_hash_is_a_deterministic_64_bit_page_digest() -> None:
    image = Image.new("L", (90, 80), color=255)
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 40, 70), fill=0)
    draw.rectangle((60, 20, 80, 60), fill=80)
    inverted = Image.eval(image, lambda pixel: 255 - pixel)

    digest = difference_hash(image)
    assert len(digest) == 16
    assert digest == difference_hash(image.copy())
    assert digest != difference_hash(inverted)


def test_pdf_page_hashes_are_rendered_in_the_sandbox() -> None:
    output = io.BytesIO()
    pdf = canvas.Canvas(output, pagesize=letter)
    pdf.drawString(72, 720, "Synthetic identity fingerprint fixture")
    pdf.save()
    content = output.getvalue()

    first = page_image_hashes_isolated(content, (1,))
    second = page_image_hashes_isolated(content, (1,))

    assert first is not None and len(first) == 1
    assert first == second
    assert first[0].page == 1
    assert len(first[0].value) == 16
