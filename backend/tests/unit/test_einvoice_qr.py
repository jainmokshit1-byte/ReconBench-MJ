"""AC-2 e-invoice QR reader on synthetic data: payload parsing, signature, merge rules."""

from __future__ import annotations

import base64
import io
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from hypothesis import given
from hypothesis import strategies as st
from reconbench_app.extraction.einvoice_keys import einvoice_keys
from reconbench_app.extraction.qr_decode import decode_qr_texts
from reconbench_app.pdf_sandbox import parse_pdf_isolated
from reconbench_core.extraction.qr import (
    CONFIRMED_CONFIDENCE,
    PREFILL_CONFIDENCE,
    SIGNED_CONFIDENCE,
    EInvoiceQr,
    apply_einvoice,
    fold,
    load_public_keys,
    parse_einvoice_qr,
)
from reconbench_core.parsers.types import BuyerEntityHint, ParseResult, ParserHints
from reconbench_core.schema import (
    DocLine,
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    FileFormat,
    InvoiceHeader,
    ReasonCode,
)
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# Typed field values must serialise without Pydantic warnings.
pytestmark = pytest.mark.filterwarnings("error::UserWarning")

D = Decimal
BUYER = "27AAPFU0939F1ZV"
SELLER = "27AABCU9603R1ZN"
ENTITIES = (BuyerEntityHint(gstin=BUYER, name="Synthetic Buyer Solutions Limited"),)
HINTS = ParserHints(doc_type=DocType.INVOICE, buyer_entities=ENTITIES)
IRN = "a" * 64
DATA: dict[str, Any] = {
    "SellerGstin": SELLER,
    "BuyerGstin": BUYER,
    "DocNo": "76ID260001",
    "DocTyp": "INV",
    "DocDt": "15/08/2026",
    "TotInvVal": 9813.36,
    "ItemCnt": 1,
    "MainHsnCode": "998713",
    "Irn": IRN,
    "IrnDt": "2026-08-15 10:00:00",
}


@pytest.fixture(scope="module")
def key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwt(private: rsa.RSAPrivateKey, data: dict[str, Any] | None = None, **changes: Any) -> str:
    payload = {**(data or DATA), **changes}
    head = _b64(json.dumps({"alg": "RS256", "kid": "SYNTHETIC", "typ": "JWT"}).encode())
    body = _b64(json.dumps({"data": json.dumps(payload), "iss": "NIC"}).encode())
    signature = private.sign(f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{head}.{body}.{_b64(signature)}"


def _qr(**changes: Any) -> EInvoiceQr:
    values: dict[str, Any] = {
        "seller_gstin": SELLER,
        "buyer_gstin": BUYER,
        "doc_no": "76ID260001",
        "doc_type": "INV",
        "doc_date": date(2026, 8, 15),
        "total": D("9813.36"),
        "item_count": 1,
        "main_hsn": "998713",
        "irn": IRN,
        "irn_date": None,
        "page": 1,
        "signed": False,
    }
    return EInvoiceQr(**{**values, **changes})


def _field(value: object, confidence: str = "0.98") -> FieldValue[object]:
    return FieldValue(
        value=value,
        confidence=D(confidence),
        source=FieldSource.RULE,
        evidence=FieldEvidence(page=1, snippet=str(value)),
    )


def _result(
    review: list[str] | None = None,
    reasons: tuple[ReasonCode, ...] = (),
    lines: int = 1,
    **fields: tuple[object, str] | None,
) -> ParseResult:
    base: dict[str, tuple[object, str] | None] = {
        "doc_no": ("76ID260001", "0.65"),
        "doc_date": (date(2026, 8, 15), "0.65"),
        "vendor_gstin": (SELLER, "0.98"),
        "grand_total": (D("9813.36"), "0.98"),
    }
    base.update(fields)
    header = InvoiceHeader(
        **{name: _field(*item) for name, item in base.items() if item is not None},
        extras={"review_fields": review if review is not None else ["doc_date", "doc_no"]},
    )
    document_lines = tuple(
        DocLine(line_no=n, quantity=_field(D("1")), amount=_field(D("8316.40")))
        for n in range(1, lines + 1)
    )
    needs = bool(review if review is not None else True) or bool(reasons)
    document = Document(
        header=header,
        lines=document_lines,
        needs_verification=needs,
        reason_codes=reasons or ((ReasonCode.LOW_CONFIDENCE_DATA,) if needs else ()),
    )
    return ParseResult(
        success=True, file_format=FileFormat.PDF, documents=(document,), layout_signature="s"
    )


def _header(result: ParseResult) -> InvoiceHeader:
    header = result.documents[0].header
    assert isinstance(header, InvoiceHeader)
    return header


def _codes(result: ParseResult) -> list[str]:
    return [warning.code for warning in result.warnings]


# --- payload --------------------------------------------------------------------------------


def test_parses_the_portal_payload_without_keys(key: rsa.RSAPrivateKey) -> None:
    qr = parse_einvoice_qr(_jwt(key), page=2)

    assert qr is not None
    assert (qr.seller_gstin, qr.buyer_gstin, qr.doc_no, qr.doc_type) == (
        SELLER,
        BUYER,
        "76ID260001",
        "INV",
    )
    assert qr.doc_date == date(2026, 8, 15)
    assert qr.total == D("9813.36")
    assert (qr.item_count, qr.main_hsn, qr.irn, qr.page) == (1, "998713", IRN, 2)
    assert not qr.signed


def test_signature_verifies_only_with_the_matching_key(key: rsa.RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    text = _jwt(key)

    signed = parse_einvoice_qr(text, page=1, public_keys=(other.public_key(), key.public_key()))
    unsigned = parse_einvoice_qr(text, page=1, public_keys=(other.public_key(),))
    head, body, signature = text.split(".")
    tampered_body = _b64(
        json.dumps({"data": json.dumps({**DATA, "TotInvVal": 1.0}), "iss": "NIC"}).encode()
    )
    tampered = parse_einvoice_qr(
        f"{head}.{tampered_body}.{signature}", page=1, public_keys=(key.public_key(),)
    )

    assert signed is not None and signed.signed
    assert unsigned is not None and not unsigned.signed
    assert tampered is not None and not tampered.signed and tampered.total == D("1.00")


@pytest.mark.parametrize(
    "text",
    [
        "232259109474/27AABCU9603R1ZN/8/5/2026",  # e-way bill QR
        "1036847555",
        "https://example.invalid/invoice",
        "eyJhbGciOiJSUzI1NiJ9.not-json.c2ln",
        "",
    ],
)
def test_other_qr_texts_are_ignored(text: str) -> None:
    assert parse_einvoice_qr(text, page=1) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"SellerGstin": "27AABCU9603R1ZX"},  # checksum fails
        {"DocDt": "2026-08-15"},
        {"TotInvVal": "n/a"},
        {"DocNo": " "},
    ],
)
def test_payload_without_a_usable_core_value_is_ignored(
    key: rsa.RSAPrivateKey, changes: dict[str, Any]
) -> None:
    assert parse_einvoice_qr(_jwt(key, **changes), page=1) is None


def test_unregistered_buyer_and_odd_item_count_are_absent(key: rsa.RSAPrivateKey) -> None:
    qr = parse_einvoice_qr(_jwt(key, BuyerGstin="URP", ItemCnt="many"), page=1)

    assert qr is not None and qr.buyer_gstin is None and qr.item_count is None


@given(st.text(max_size=300))
def test_parsing_never_raises(text: str) -> None:
    parse_einvoice_qr(text, page=1)


@given(st.text(alphabet="0123456789ABCDILOSZ|-/ ", max_size=20))
def test_fold_is_idempotent(text: str) -> None:
    assert fold(fold(text)) == fold(text)


def test_keys_load_from_pem_certificate_and_der(key: rsa.RSAPrivateKey) -> None:
    public = key.public_key()
    pem = public.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    der = public.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(public)
        .serial_number(1)
        .not_valid_before(datetime(2026, 1, 1))
        .not_valid_after(datetime(2027, 1, 1))
        .sign(key, hashes.SHA256())
    )

    for data in (pem, der, certificate.public_bytes(serialization.Encoding.PEM), pem + pem):
        keys = load_public_keys(data)
        assert keys and all(k.public_numbers() == public.public_numbers() for k in keys)
    with pytest.raises(ValueError):
        load_public_keys(b"not a key")


def test_configured_key_file_must_hold_a_key(tmp_path: Any, key: rsa.RSAPrivateKey) -> None:
    good = tmp_path / "portal.pem"
    good.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
    )
    bad = tmp_path / "bad.pem"
    bad.write_text("nothing here")

    assert einvoice_keys(None) == ()
    assert len(einvoice_keys(good)) == 1
    with pytest.raises(ValueError, match="RB_EINVOICE_PUBLIC_KEY_FILE"):
        einvoice_keys(bad)
    with pytest.raises(ValueError, match="RB_EINVOICE_PUBLIC_KEY_FILE"):
        einvoice_keys(tmp_path / "missing.pem")


# --- merge ----------------------------------------------------------------------------------


def test_agreeing_reading_confirms_the_fields_and_clears_review() -> None:
    result = apply_einvoice(_result(), [_qr()], HINTS)
    header = _header(result)
    document = result.documents[0]

    assert header.doc_no is not None and header.doc_no.confidence == CONFIRMED_CONFIDENCE
    assert header.doc_no.evidence.snippet == "76ID260001"  # the reading's own evidence kept
    assert header.doc_date is not None and header.doc_date.confidence == CONFIRMED_CONFIDENCE
    assert header.irn is not None and header.irn.value == IRN
    assert header.buyer_gstin is not None and header.buyer_gstin.value == BUYER
    assert header.extras["review_fields"] == []
    assert header.extras["einvoice"]["fields"]["doc_no"] == "confirmed"
    assert not document.needs_verification and document.reason_codes == ()
    assert _codes(result) == ["EINVOICE_QR_APPLIED"]


def test_lookalike_reading_takes_the_qr_spelling_as_verified() -> None:
    result = apply_einvoice(_result(doc_no=("761D260001", "0.65")), [_qr()], HINTS)
    header = _header(result)

    assert header.doc_no is not None
    assert header.doc_no.value == "76ID260001"
    assert header.doc_no.confidence == CONFIRMED_CONFIDENCE
    assert header.extras["einvoice"]["fields"]["doc_no"] == "corrected"
    assert header.extras["einvoice"]["other_readings"]["doc_no"] == "761D260001"
    assert not result.documents[0].needs_verification


def test_conflicting_reading_prefills_the_qr_value_for_review() -> None:
    result = apply_einvoice(_result(review=[], grand_total=(D("8154.10"), "0.98")), [_qr()], HINTS)
    header = _header(result)

    assert header.grand_total is not None
    assert header.grand_total.value == D("9813.36")
    assert header.grand_total.confidence == PREFILL_CONFIDENCE
    assert header.extras["einvoice"]["other_readings"]["grand_total"] == "8154.10"
    assert header.extras["review_fields"] == ["grand_total"]
    assert result.documents[0].needs_verification


def test_missing_reading_is_prefilled_for_review() -> None:
    result = apply_einvoice(_result(review=["doc_no"], doc_no=None), [_qr()], HINTS)
    header = _header(result)

    assert header.doc_no is not None and header.doc_no.confidence == PREFILL_CONFIDENCE
    assert header.extras["einvoice"]["fields"]["doc_no"] == "prefilled"
    assert header.extras["review_fields"] == ["doc_no"]
    assert result.documents[0].needs_verification


def test_seller_gstin_resolves_a_missing_vendor() -> None:
    result = apply_einvoice(
        _result(review=["vendor"], vendor_gstin=None), [_qr(signed=True)], HINTS
    )

    assert _header(result).vendor_gstin is not None
    assert _header(result).extras["review_fields"] == []
    assert not result.documents[0].needs_verification


def test_signed_qr_is_trusted_over_a_different_reading() -> None:
    result = apply_einvoice(
        _result(review=["grand_total"], grand_total=(D("1.00"), "0.65")),
        [_qr(signed=True)],
        HINTS,
    )
    header = _header(result)

    assert header.grand_total is not None
    assert header.grand_total.value == D("9813.36")
    assert header.grand_total.confidence == SIGNED_CONFIDENCE
    assert header.extras["einvoice"]["signed"] is True
    assert not result.documents[0].needs_verification


def test_other_review_reasons_keep_the_document_in_review() -> None:
    kept = apply_einvoice(_result(review=["doc_no", "cgst"]), [_qr()], HINTS)
    other = apply_einvoice(_result(reasons=(ReasonCode.MATH_ERROR,)), [_qr()], HINTS)

    assert _header(kept).extras["review_fields"] == ["cgst"]
    assert kept.documents[0].needs_verification
    assert other.documents[0].needs_verification
    assert other.documents[0].reason_codes == (ReasonCode.MATH_ERROR,)


def test_item_count_mismatch_sends_lines_to_review() -> None:
    result = apply_einvoice(_result(lines=2), [_qr()], HINTS)

    assert "lines" in _header(result).extras["review_fields"]
    assert result.documents[0].needs_verification
    assert "EINVOICE_ITEM_COUNT_MISMATCH" in _codes(result)


@pytest.mark.parametrize(
    ("qr", "code", "item"),
    [
        (
            _qr(seller_gstin=BUYER, buyer_gstin=SELLER),
            "EINVOICE_SELLER_IS_BUYER_ENTITY",
            "vendor_gstin",
        ),
        (_qr(buyer_gstin="29AABCU9603R1ZJ"), "EINVOICE_OTHER_BUYER", "buyer_gstin"),
        (_qr(buyer_gstin=None), "EINVOICE_OTHER_BUYER", "buyer_gstin"),
        (_qr(doc_type="CRN"), "EINVOICE_NOT_TAX_INVOICE", "doc_type"),
    ],
)
def test_foreign_or_non_invoice_qr_is_not_used(qr: EInvoiceQr, code: str, item: str) -> None:
    before = _result(review=[], doc_no=("SYN-1", "0.98"))
    result = apply_einvoice(before, [qr], HINTS)
    header = _header(result)

    assert header.doc_no is not None and header.doc_no.value == "SYN-1"
    assert "einvoice" not in header.extras
    assert item in header.extras["review_fields"]
    assert result.documents[0].needs_verification
    assert code in _codes(result)


def test_without_buyer_entities_the_buyer_is_not_checked() -> None:
    result = apply_einvoice(
        _result(), [_qr(buyer_gstin=None)], ParserHints(doc_type=DocType.INVOICE)
    )

    assert "EINVOICE_QR_APPLIED" in _codes(result)


def test_qrs_of_different_invoices_are_not_used() -> None:
    before = _result()
    result = apply_einvoice(before, [_qr(), _qr(doc_no="76ID260002", irn="b" * 64)], HINTS)

    assert result.documents == before.documents
    assert _codes(result) == ["EINVOICE_QR_AMBIGUOUS"]


def test_repeated_qr_prefers_a_signed_copy() -> None:
    result = apply_einvoice(_result(), [_qr(page=1), _qr(page=2, signed=True)], HINTS)

    assert _header(result).extras["einvoice"]["page"] == 2
    assert _header(result).extras["einvoice"]["signed"] is True


def test_non_invoice_zone_and_failed_parse_are_untouched() -> None:
    before = _result()
    failed = ParseResult(success=False, layout_signature="")

    assert apply_einvoice(before, [_qr()], ParserHints(doc_type=DocType.PO)) == before
    assert apply_einvoice(failed, [_qr()], HINTS) == failed
    assert apply_einvoice(before, [], HINTS) == before


# --- decode + sandbox -----------------------------------------------------------------------


def _invoice_with_qr(text: str) -> bytes:
    stream = io.BytesIO()
    page = canvas.Canvas(stream, pagesize=A4)
    page.setFont("Helvetica", 10)
    rows = (
        "Tax Invoice",
        "Invoice No: 76ID260001",
        "Invoice Date: 15/08/2026",
        f"Supplier GSTIN: {SELLER}",
        f"Buyer GSTIN: {BUYER}",
        "Taxable Value: 8316.40",
        "CGST: 748.48",
        "SGST: 748.48",
        "Total Value: 9813.36",
    )
    for index, row in enumerate(rows):
        page.drawString(36, 800 - index * 16, row)
    widget = QrCodeWidget(text, barLevel="M")
    x0, y0, x1, y1 = widget.getBounds()
    size = 200
    drawing = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, page, 360, 600)
    page.showPage()
    page.save()
    return stream.getvalue()


def test_decoder_finds_the_qr_on_a_rendered_page(key: rsa.RSAPrivateKey) -> None:
    text = _jwt(key)

    assert decode_qr_texts(_invoice_with_qr(text)) == [(1, text)]


def test_sandbox_applies_a_signed_qr_to_the_invoice(key: rsa.RSAPrivateKey) -> None:
    content = _invoice_with_qr(_jwt(key, ItemCnt=None))
    hints = ParserHints(
        doc_type=DocType.INVOICE, file_format=FileFormat.PDF, buyer_entities=ENTITIES
    )

    result = parse_pdf_isolated(content, hints, einvoice_keys=(key.public_key(),))
    header = _header(result)

    assert "EINVOICE_QR_APPLIED" in _codes(result)
    assert header.irn is not None and header.irn.value == IRN
    assert header.extras["einvoice"]["signed"] is True
    for name in ("doc_no", "doc_date", "vendor_gstin", "grand_total"):
        field = getattr(header, name)
        assert field is not None and field.confidence == SIGNED_CONFIDENCE, name
    assert header.grand_total is not None and header.grand_total.value == D("9813.36")
    grn = parse_pdf_isolated(content, hints.model_copy(update={"doc_type": DocType.GRN}))
    assert "EINVOICE_QR_APPLIED" not in _codes(grn)


def _split_invoice(page: int, doc_no: str, *, review: list[str] | None = None) -> Document:
    document = _result(review=review, doc_no=(doc_no, "0.98")).documents[0]
    marker = {"pages": [page], "group": page, "groups": 2, "state": "unconfirmed"}
    header = document.header.model_copy(
        update={"extras": {**document.header.extras, "page_split": marker}}
    )
    return document.model_copy(update={"header": header})


def test_split_file_qr_goes_to_the_invoice_on_its_page_and_keeps_the_split_in_review() -> None:
    other = _split_invoice(1, "OTHER-1")
    target = _split_invoice(2, "76ID260001", review=[])
    result = ParseResult(
        success=True,
        file_format=FileFormat.PDF,
        documents=(other, target),
        layout_signature="s",
    )

    merged = apply_einvoice(result, [_qr(page=2)], HINTS)

    assert "einvoice" not in merged.documents[0].header.extras
    assert "einvoice" in merged.documents[1].header.extras
    # Every QR field agrees, but the split itself still waits for a person.
    assert merged.documents[1].needs_verification
