"""Deterministic document identity fingerprints used at the ingestion boundary."""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from PIL import Image, ImageOps
from reconbench_core.normalize import canonicalize_document_number, normalize_vendor_name
from reconbench_core.parsers.types import PageImageHash
from reconbench_core.schema import (
    DocLine,
    Document,
    ExtractionMethod,
    FieldSource,
    FieldValue,
    FieldVerification,
)

VERIFIED_CONFIDENCE = Decimal("0.98")
RESCAN_PAGE_DHASH_MAX_DISTANCE = 16
_STRUCTURED_METHODS = frozenset(
    {ExtractionMethod.XML_PROFILE, ExtractionMethod.CSV_MAPPING, ExtractionMethod.MANUAL}
)


def difference_hash(image: Image.Image) -> str:
    """Return a 64-bit horizontal difference hash for a source page image."""

    pixels = ImageOps.exif_transpose(image).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    values = pixels.tobytes()
    bits = 0
    for row in range(8):
        start = row * 9
        for column in range(8):
            bits = (bits << 1) | int(values[start + column] > values[start + column + 1])
    return f"{bits:016x}"


def difference_hash_bytes(image_bytes: bytes) -> str:
    """Hash image bytes without retaining or returning the decoded image."""

    with Image.open(io.BytesIO(image_bytes)) as image:
        return difference_hash(image)


def page_hashes_within_distance(
    left: Sequence[str], right: Sequence[str], *, max_distance: int = RESCAN_PAGE_DHASH_MAX_DISTANCE
) -> bool:
    """Compare ordered page dHashes with a bounded per-page Hamming distance."""

    if max_distance < 0 or not left or len(left) != len(right):
        return False
    try:
        return all(
            (int(left_hash, 16) ^ int(right_hash, 16)).bit_count() <= max_distance
            for left_hash, right_hash in zip(left, right, strict=True)
        )
    except ValueError:
        return False


def is_verified_field(value: Any, *, structured: bool) -> bool:
    """Apply the existing extraction trust boundary to a parsed field value."""

    if not isinstance(value, FieldValue):
        return False
    if value.verification is FieldVerification.USER:
        return True
    if value.source is FieldSource.MANUAL:
        return True
    return value.source is FieldSource.RULE and (
        structured or (value.confidence is not None and value.confidence >= VERIFIED_CONFIDENCE)
    )


def verified_field_confidence(document: Document, *, structured: bool = False) -> Decimal | None:
    """Conservative D19 score: the minimum confidence among verified fields."""

    values: list[Decimal] = []
    for field_name in type(document.header).model_fields:
        if field_name == "extras":
            continue
        field_value = getattr(document.header, field_name)
        if is_verified_field(field_value, structured=structured):
            values.append(
                Decimal("1")
                if field_value.verification is FieldVerification.USER
                else (
                    field_value.confidence if field_value.confidence is not None else Decimal("1")
                )
            )
    for line in document.lines:
        for field_name in type(line).model_fields:
            field_value = getattr(line, field_name)
            if is_verified_field(field_value, structured=structured):
                values.append(
                    Decimal("1")
                    if field_value.verification is FieldVerification.USER
                    else (
                        field_value.confidence
                        if field_value.confidence is not None
                        else Decimal("1")
                    )
                )
    return min(values) if values else None


def _plain(value: Any) -> Any:
    return value.value if isinstance(value, FieldValue) else value


def _money_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _line_amount(line: DocLine) -> Decimal | None:
    amount = _plain(line.amount)
    if isinstance(amount, Decimal):
        return amount
    quantity = _plain(line.quantity)
    rate = _plain(line.rate)
    if isinstance(quantity, Decimal) and isinstance(rate, Decimal):
        return quantity * rate
    return None


def content_fingerprint(document: Document, *, structured: bool = False) -> str | None:
    """Hash the plan's complete amount/date content fingerprint, if available."""

    header = document.header
    doc_date = _plain(header.doc_date)
    grand_total = _plain(header.grand_total)
    if not isinstance(doc_date, date) or not isinstance(grand_total, Decimal):
        return None
    line_amounts = tuple(_line_amount(line) for line in document.lines)
    if not line_amounts or any(amount is None for amount in line_amounts):
        return None

    gstin_field = header.vendor_gstin
    pan: str | None = None
    if is_verified_field(gstin_field, structured=structured):
        gstin = _plain(gstin_field)
        if isinstance(gstin, str):
            compact = re.sub(r"[^A-Z0-9]", "", gstin.upper())
            if len(compact) == 15:
                pan = compact[2:12]

    payload = {
        "doc_type": header.doc_type.value,
        "doc_date": doc_date.isoformat(),
        "grand_total": _money_text(grand_total),
        "line_amounts": sorted(
            _money_text(amount) for amount in line_amounts if amount is not None
        ),
        "supplier_pan": pan,
    }
    return _hash_payload(payload)


def identity_payload(
    document: Document,
    file_sha: str,
    *,
    page_hashes: Sequence[PageImageHash] = (),
    structured: bool = False,
) -> dict[str, Any]:
    """Return the verified natural identity or the conservative pre-verification identity."""

    header = document.header
    doc_no_field = header.doc_no
    doc_date_field = header.doc_date
    gstin_field = header.vendor_gstin
    name_field = header.vendor_name

    canonical_doc_no: str | None = None
    if is_verified_field(doc_no_field, structured=structured):
        doc_no = _plain(doc_no_field)
        if isinstance(doc_no, str) and doc_no.strip():
            canonical_doc_no = canonicalize_document_number(doc_no)

    verified_date: date | None = None
    if is_verified_field(doc_date_field, structured=structured):
        value = _plain(doc_date_field)
        verified_date = value if isinstance(value, date) else None

    vendor_identity: str | None = None
    if is_verified_field(gstin_field, structured=structured):
        value = _plain(gstin_field)
        if isinstance(value, str):
            vendor_identity = re.sub(r"[^A-Z0-9]", "", value.upper()) or None
    elif is_verified_field(name_field, structured=structured):
        value = _plain(name_field)
        if isinstance(value, str):
            vendor_identity = normalize_vendor_name(value) or None

    if canonical_doc_no and verified_date and vendor_identity:
        return {
            "kind": "verified",
            "doc_type": header.doc_type.value,
            "vendor_identity": vendor_identity,
            "canonical_doc_no": canonical_doc_no,
            "doc_date": verified_date.isoformat(),
        }

    content_hash = content_fingerprint(document, structured=structured)
    ordered_hashes = [item.value for item in sorted(page_hashes, key=lambda item: item.page)]
    if content_hash is not None and ordered_hashes:
        return {
            "kind": "fingerprint",
            "doc_type": header.doc_type.value,
            "content_fingerprint": content_hash,
            "page_hashes": ordered_hashes,
        }

    # Keep file SHA and the source locator together when the content/page pair is incomplete.
    return {
        "kind": "source",
        "doc_type": header.doc_type.value,
        "source_sha256": file_sha,
        "locator": document.source_locator,
    }


def document_natural_key(
    document: Document,
    file_sha: str,
    *,
    page_hashes: Sequence[PageImageHash] = (),
    structured: bool = False,
) -> str:
    """Workspace identity: verified fields, then exact page/content fingerprints, then source."""

    return _hash_payload(
        identity_payload(document, file_sha, page_hashes=page_hashes, structured=structured)
    )


def rescan_identity_payload(
    document: Document,
    file_sha: str,
    *,
    page_hashes: Sequence[PageImageHash] = (),
    structured: bool = False,
) -> dict[str, Any]:
    """Return the exact evidence used to decide whether an upload is a re-scan."""

    content_hash = content_fingerprint(document, structured=structured)
    ordered_hashes = [item.value for item in sorted(page_hashes, key=lambda item: item.page)]
    if content_hash is not None and ordered_hashes:
        return {
            "kind": "fingerprint",
            "doc_type": document.header.doc_type.value,
            "content_fingerprint": content_hash,
            "page_hashes": ordered_hashes,
        }
    return {
        "kind": "source",
        "doc_type": document.header.doc_type.value,
        "source_sha256": file_sha,
        "locator": document.source_locator,
    }


def document_rescan_key(
    document: Document,
    file_sha: str,
    *,
    page_hashes: Sequence[PageImageHash] = (),
    structured: bool = False,
) -> str:
    """Hash the approved exact fingerprint, or SHA plus source locator fallback."""

    return _hash_payload(
        rescan_identity_payload(
            document,
            file_sha,
            page_hashes=page_hashes,
            structured=structured,
        )
    )


def document_identity_collision_key(
    document: Document,
    file_sha: str,
    *,
    page_hashes: Sequence[PageImageHash] = (),
    structured: bool = False,
) -> str:
    """Disambiguate verified natural keys when exact re-scan evidence does not match."""

    return _hash_payload(
        {
            "kind": "identity_collision",
            "verified_natural_key": document_natural_key(
                document, file_sha, page_hashes=page_hashes, structured=structured
            ),
            "rescan_key": document_rescan_key(
                document, file_sha, page_hashes=page_hashes, structured=structured
            ),
        }
    )


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "RESCAN_PAGE_DHASH_MAX_DISTANCE",
    "content_fingerprint",
    "difference_hash",
    "difference_hash_bytes",
    "document_identity_collision_key",
    "document_natural_key",
    "document_rescan_key",
    "identity_payload",
    "is_verified_field",
    "page_hashes_within_distance",
    "rescan_identity_payload",
    "verified_field_confidence",
]
