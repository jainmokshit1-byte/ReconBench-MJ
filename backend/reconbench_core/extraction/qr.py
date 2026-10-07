"""E-invoice signed QR (IRN) as an independent reader of invoice header fields.

An Indian e-invoice carries a QR code holding a JWT signed by the Invoice
Registration Portal (RS256). Its payload names the seller and buyer GSTIN, the
document number, type and date, the invoice value, the item count, the main HSN
code and the IRN. The values are exact characters, not an OCR reading, so:

* signature verified with a configured portal key (L0): the QR value is used and
  counts as verified;
* not verified: the QR is one independent reader. A value counts as verified only
  when the document's own reading agrees, exactly or up to letter/digit
  look-alikes (O/0, I/1; the QR spelling is kept). A missing or different reading
  keeps the QR value prefilled for review, with the other reading recorded.

The QR is not used when it belongs to someone else (the seller is a buyer entity
or the buyer is not one), is not a tax invoice, or when one file carries QRs of
different invoices. Parsing and merging are pure; image decoding lives in the app.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, get_args

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from reconbench_core.extraction.pages import split_pages, split_unconfirmed
from reconbench_core.normalize.gstin import validate_gstin
from reconbench_core.parsers.parties import buyer_gstins
from reconbench_core.parsers.types import ParseResult, ParserHints, ParseWarning
from reconbench_core.schema import (
    DocType,
    Document,
    FieldEvidence,
    FieldSource,
    FieldValue,
    ReasonCode,
)

SIGNED_CONFIDENCE = Decimal("1")
CONFIRMED_CONFIDENCE = Decimal("0.98")
# Below the review floor: a single unsigned reading is shown, never trusted.
PREFILL_CONFIDENCE = Decimal("0.65")
MAX_QR_TEXT = 8192
_JWT = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_PEM_BLOCK = re.compile(rb"-----BEGIN ([A-Z ]+)-----.+?-----END \1-----", re.DOTALL)
_TAX_INVOICE = "INV"
# Fields a confirmation can clear from ``review_fields``; "vendor" is the
# "no supplier identity" entry that a seller GSTIN resolves.
_REVIEWED = ("doc_no", "doc_date", "vendor_gstin", "grand_total")
_LOOKALIKE_FOLD = str.maketrans(
    {"O": "0", "I": "1", "L": "1", "|": "1", "S": "5", "B": "8", "Z": "2"}
)


@dataclass(frozen=True, slots=True)
class EInvoiceQr:
    seller_gstin: str
    buyer_gstin: str | None
    doc_no: str
    doc_type: str
    doc_date: date
    total: Decimal
    item_count: int | None
    main_hsn: str | None
    irn: str | None
    irn_date: str | None
    page: int
    signed: bool

    @property
    def identity(self) -> tuple[str, str]:
        return (self.irn or "", f"{self.seller_gstin}/{fold(self.doc_no)}")


@dataclass
class _Merge:
    updates: dict[str, Any] = field(default_factory=dict)
    statuses: dict[str, str] = field(default_factory=dict)
    conflicts: dict[str, str] = field(default_factory=dict)
    verified: list[str] = field(default_factory=list)
    review: list[str] = field(default_factory=list)


def fold(value: str) -> str:
    """Upper-case alphanumerics with letter/digit look-alikes folded to digits."""

    return re.sub(r"[^0-9A-Z|]", "", value.upper()).translate(_LOOKALIKE_FOLD)


def load_public_keys(data: bytes) -> tuple[rsa.RSAPublicKey, ...]:
    """RSA keys from PEM certificates/public keys (several blocks allowed) or one DER item."""

    loaded: list[object] = []
    blocks = [match.group(0) for match in _PEM_BLOCK.finditer(data)]
    for block in blocks:
        if b"CERTIFICATE" in block.split(b"\n", 1)[0]:
            loaded.append(x509.load_pem_x509_certificate(block).public_key())
        else:
            loaded.append(serialization.load_pem_public_key(block))
    if not blocks:
        try:
            loaded.append(x509.load_der_x509_certificate(data).public_key())
        except ValueError:
            loaded.append(serialization.load_der_public_key(data))
    keys = tuple(key for key in loaded if isinstance(key, rsa.RSAPublicKey))
    if not keys:
        raise ValueError("no RSA public key found")
    return keys


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def _verify(signing_input: bytes, signature: bytes, keys: Sequence[rsa.RSAPublicKey]) -> bool:
    for key in keys:
        try:
            key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            continue
        return True
    return False


def _gstin(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = validate_gstin(value)
    return result.normalized if result.is_valid else None


def _text(value: Any) -> str | None:
    if isinstance(value, int | Decimal) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def parse_einvoice_qr(
    text: str, *, page: int, public_keys: Sequence[rsa.RSAPublicKey] = ()
) -> EInvoiceQr | None:
    """The e-invoice payload of a decoded QR text; ``None`` for any other QR."""

    text = text.strip()
    if len(text) > MAX_QR_TEXT or not _JWT.fullmatch(text):
        return None
    head_part, body_part, signature_part = text.split(".")
    try:
        head = json.loads(_b64(head_part))
        body = json.loads(_b64(body_part), parse_float=Decimal)
        data = body.get("data", body) if isinstance(body, dict) else None
        if isinstance(data, str):
            data = json.loads(data, parse_float=Decimal)
        signature = _b64(signature_part)
    except (ValueError, binascii.Error):
        return None
    if not isinstance(head, dict) or not isinstance(data, dict):
        return None
    seller = _gstin(data.get("SellerGstin"))
    doc_no = _text(data.get("DocNo"))
    doc_type = _text(data.get("DocTyp"))
    try:
        doc_date = datetime.strptime(str(data.get("DocDt")), "%d/%m/%Y").date()
        total = Decimal(str(data.get("TotInvVal"))).quantize(Decimal("0.01"))
    except (ValueError, InvalidOperation):
        return None
    if seller is None or doc_no is None or doc_type is None or not total.is_finite():
        return None
    item_count = data.get("ItemCnt")
    signed = head.get("alg") == "RS256" and _verify(
        f"{head_part}.{body_part}".encode("ascii"), signature, public_keys
    )
    return EInvoiceQr(
        seller_gstin=seller,
        buyer_gstin=_gstin(data.get("BuyerGstin")),
        doc_no=doc_no,
        doc_type=doc_type.upper(),
        doc_date=doc_date,
        total=total,
        item_count=item_count if isinstance(item_count, int) and item_count > 0 else None,
        main_hsn=_text(data.get("MainHsnCode")),
        irn=_text(data.get("Irn")),
        irn_date=_text(data.get("IrnDt")),
        page=page,
        signed=signed,
    )


def _same(name: str, current: Any, value: Any) -> str | None:
    """ "exact", "lookalike" or ``None`` when the two readings differ."""

    if name == "grand_total":
        try:
            equal = Decimal(str(current)).quantize(Decimal("0.01")) == value
        except (InvalidOperation, ValueError):
            equal = False
        return "exact" if equal else None
    if name == "doc_date":
        return "exact" if current == value else None
    left, right = re.sub(r"\s+", "", str(current)).upper(), str(value).upper()
    if left == right:
        return "exact"
    return "lookalike" if fold(left) == fold(right) else None


def _evidence(qr: EInvoiceQr, value: Any) -> FieldEvidence:
    kind = "signed e-invoice QR" if qr.signed else "e-invoice QR"
    return FieldEvidence(page=qr.page, snippet=f"{kind}: {value}"[:200])


def _merge_field(merge: _Merge, document: Document, name: str, value: Any, qr: EInvoiceQr) -> None:
    current: FieldValue[Any] | None = getattr(document.header, name, None)
    # The header's own parametrised type (FieldValue[str], FieldValue[date], ...).
    annotation = type(document.header).model_fields[name].annotation
    field_type: Any = next(arg for arg in get_args(annotation) if arg is not type(None))
    tracked = name in _REVIEWED
    if qr.signed:
        merge.updates[name] = field_type(
            value=value,
            confidence=SIGNED_CONFIDENCE,
            source=FieldSource.RULE,
            evidence=_evidence(qr, value),
        )
        merge.statuses[name] = "signed"
        if current is not None and _same(name, current.value, value) is None:
            merge.conflicts[name] = str(current.value)
        merge.verified.append(name)
        return
    if current is None:
        merge.updates[name] = field_type(
            value=value,
            confidence=PREFILL_CONFIDENCE,
            source=FieldSource.RULE,
            evidence=_evidence(qr, value),
        )
        merge.statuses[name] = "prefilled"
        if tracked:
            merge.review.append(name)
        return
    agreement = _same(name, current.value, value)
    if agreement == "exact":
        merge.updates[name] = current.model_copy(
            update={"confidence": max(current.confidence, CONFIRMED_CONFIDENCE)}
        )
        merge.statuses[name] = "confirmed"
        merge.verified.append(name)
    elif agreement == "lookalike":
        merge.updates[name] = field_type(
            value=value,
            confidence=CONFIRMED_CONFIDENCE,
            source=FieldSource.RULE,
            evidence=_evidence(qr, value),
        )
        merge.statuses[name] = "corrected"
        merge.conflicts[name] = str(current.value)
        merge.verified.append(name)
    else:
        merge.updates[name] = field_type(
            value=value,
            confidence=min(current.confidence, PREFILL_CONFIDENCE),
            source=FieldSource.RULE,
            evidence=_evidence(qr, value),
        )
        merge.statuses[name] = "conflict"
        merge.conflicts[name] = str(current.value)
        if tracked:
            merge.review.append(name)


def _apply(document: Document, qr: EInvoiceQr) -> tuple[Document, list[ParseWarning]]:
    merge = _Merge()
    values: dict[str, Any] = {
        "doc_no": qr.doc_no,
        "doc_date": qr.doc_date,
        "vendor_gstin": qr.seller_gstin,
        "grand_total": qr.total,
    }
    if qr.buyer_gstin is not None:
        values["buyer_gstin"] = qr.buyer_gstin
    if qr.irn is not None:
        values["irn"] = qr.irn
    for name, value in values.items():
        _merge_field(merge, document, name, value, qr)
    warnings: list[ParseWarning] = []
    if qr.item_count is not None and qr.item_count != len(document.lines):
        merge.review.append("lines")
        warnings.append(
            ParseWarning(
                code="EINVOICE_ITEM_COUNT_MISMATCH",
                message=(
                    f"The e-invoice QR lists {qr.item_count} item(s); "
                    f"{len(document.lines)} line(s) were read."
                ),
            )
        )
    header = document.header
    listed = list(header.extras.get("review_fields", []))
    resolved = set(merge.verified)
    if "vendor_gstin" in resolved:
        resolved.add("vendor")
    review = [item for item in listed if item not in resolved]
    review += [item for item in merge.review if item not in review]
    extras = {
        **header.extras,
        "review_fields": review,
        "einvoice": {
            "signed": qr.signed,
            "page": qr.page,
            "doc_type": qr.doc_type,
            "irn": qr.irn,
            "irn_date": qr.irn_date,
            "item_count": qr.item_count,
            "main_hsn": qr.main_hsn,
            "fields": merge.statuses,
            "other_readings": merge.conflicts,
        },
    }
    header = header.model_copy(update={**merge.updates, "extras": extras})
    # Same rule as the dual read: the flag clears only when every listed reason
    # is resolved and nothing other than low confidence put the document in review.
    other_reasons = any(code != ReasonCode.LOW_CONFIDENCE_DATA for code in document.reason_codes)
    needs = (
        bool(review)
        or other_reasons
        or (document.needs_verification and not listed)
        or split_unconfirmed(document)
    )
    reason_codes = document.reason_codes
    if needs and not reason_codes:
        reason_codes = (ReasonCode.LOW_CONFIDENCE_DATA,)
    elif not needs:
        reason_codes = ()
    kind = "signed e-invoice QR (signature verified)" if qr.signed else "e-invoice QR"
    summary = ", ".join(f"{name} {status}" for name, status in merge.statuses.items())
    warnings.insert(
        0, ParseWarning(code="EINVOICE_QR_APPLIED", message=f"Read the {kind}: {summary}.")
    )
    return (
        document.model_copy(
            update={"header": header, "needs_verification": needs, "reason_codes": reason_codes}
        ),
        warnings,
    )


def _flag(document: Document, item: str) -> Document:
    extras = dict(document.header.extras)
    listed = list(extras.get("review_fields", []))
    extras["review_fields"] = listed + ([item] if item not in listed else [])
    return document.model_copy(
        update={
            "header": document.header.model_copy(update={"extras": extras}),
            "needs_verification": True,
            "reason_codes": tuple(
                dict.fromkeys((*document.reason_codes, ReasonCode.LOW_CONFIDENCE_DATA))
            ),
        }
    )


def _target(documents: Sequence[Document], qr: EInvoiceQr) -> int | None:
    if len(documents) == 1:
        return 0
    invoices = [
        index
        for index, document in enumerate(documents)
        if document.header.doc_type is DocType.INVOICE
    ]
    # A file split into documents (AC-3): the QR belongs to the invoice printed on its page.
    on_page = [index for index in invoices if qr.page in split_pages(documents[index])]
    if len(on_page) == 1:
        return on_page[0]
    matches = [
        index
        for index, doc_no in ((index, documents[index].header.doc_no) for index in invoices)
        if doc_no is not None and fold(doc_no.value) == fold(qr.doc_no)
    ]
    return matches[0] if len(matches) == 1 else None


def apply_einvoice(
    result: ParseResult, codes: Sequence[EInvoiceQr], hints: ParserHints
) -> ParseResult:
    """Merge the file's e-invoice QR into its invoice (see the module docstring)."""

    if not codes or not result.success or not result.documents:
        return result
    if hints.doc_type is not None and hints.doc_type is not DocType.INVOICE:
        return result
    warnings = list(result.warnings)
    distinct = {qr.identity: qr for qr in codes}
    if len(distinct) > 1:
        warnings.append(
            ParseWarning(
                code="EINVOICE_QR_AMBIGUOUS",
                message="The file carries e-invoice QRs of different invoices; none was used.",
            )
        )
        return result.model_copy(update={"warnings": tuple(warnings)})
    # Prefer a copy whose signature verified, then the first page.
    qr = sorted(codes, key=lambda item: (not item.signed, item.page))[0]
    index = _target(result.documents, qr)
    if index is None:
        warnings.append(
            ParseWarning(
                code="EINVOICE_QR_UNMATCHED",
                message="No parsed document matches the e-invoice QR; it was not used.",
            )
        )
        return result.model_copy(update={"warnings": tuple(warnings)})
    documents = list(result.documents)
    document = documents[index]
    entities = buyer_gstins(hints.buyer_entities)
    problem: tuple[str, str, str] | None = None
    if qr.seller_gstin in entities:
        problem = (
            "EINVOICE_SELLER_IS_BUYER_ENTITY",
            "vendor_gstin",
            "The e-invoice QR names a buyer entity as the seller; it was not used.",
        )
    elif entities and qr.buyer_gstin not in entities:
        problem = (
            "EINVOICE_OTHER_BUYER",
            "buyer_gstin",
            "The e-invoice QR names a buyer that is not a configured buyer entity; "
            "it was not used.",
        )
    elif qr.doc_type != _TAX_INVOICE:
        problem = (
            "EINVOICE_NOT_TAX_INVOICE",
            "doc_type",
            f"The e-invoice QR is for document type {qr.doc_type}, not a tax invoice; "
            "it was not used.",
        )
    if problem is not None:
        code, item, message = problem
        documents[index] = _flag(document, item)
        warnings.append(ParseWarning(code=code, message=message))
    else:
        documents[index], added = _apply(document, qr)
        warnings.extend(added)
    return result.model_copy(update={"documents": tuple(documents), "warnings": tuple(warnings)})


__all__ = [
    "CONFIRMED_CONFIDENCE",
    "PREFILL_CONFIDENCE",
    "SIGNED_CONFIDENCE",
    "EInvoiceQr",
    "apply_einvoice",
    "fold",
    "load_public_keys",
    "parse_einvoice_qr",
]
